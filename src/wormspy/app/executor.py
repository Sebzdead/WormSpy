"""Executor: carries out the state machine's Effects on the rig and the filesystem (ADR 0010).

Effects are queued and run in order on the executor's own thread. That keeps the dispatching
thread (UI, gamepad) responsive, since closing a recording waits for queued writes. It also
avoids self-joins: a worker can report an Event whose Effect stops that same worker, e.g.
RecordingFailed → CloseRecording. Outcomes go back to the store as Events. Effects whose
subsystems don't exist yet raise a visible `not_implemented` alarm instead of being silently
ignored.
"""

import queue
import threading
from collections.abc import Callable
from pathlib import Path

from wormspy.acquisition.loop import AcquisitionLoop
from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.recorder import Recorder
from wormspy.acquisition.rig import RigHandle
from wormspy.acquisition.session import create_session
from wormspy.acquisition.stage_poller import StagePoller, StageTelemetry
from wormspy.calibration.routine import CalibrationError, RigCalibration, run_calibration
from wormspy.control import StateStore, TrackingStatus
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.core.frames import FrameHub
from wormspy.tracking.controller import ServoConfig
from wormspy.tracking.estimators import BrightMarker, DarkObject
from wormspy.tracking.tracker import Tracker


class Executor:
    def __init__(self, store: StateStore, rig: RigHandle, hub: FrameHub, data_root: Path,
                 calibration_path: Path | None = None, recorder_queue_size: int = 64,
                 estimators: dict | None = None, servo: ServoConfig | None = None,
                 override: Callable[[], bool] = lambda: False) -> None:
        self._store = store
        self._rig = rig
        self._hub = hub
        self._data_root = Path(data_root)
        self._recorder_queue_size = recorder_queue_size
        self._calibration_path = None if calibration_path is None else Path(calibration_path)
        self._calibration: RigCalibration | None = None
        self._estimators = estimators if estimators is not None else {
            "dark_object": DarkObject(), "bright_marker": BrightMarker()}
        self._servo = servo if servo is not None else ServoConfig()
        self._override = override          # True while the joystick is moving XY
        self._tracker: Tracker | None = None
        self._loop: AcquisitionLoop | None = None
        self._recorder: Recorder | None = None
        self._recorder_lock = threading.Lock()
        self._effects: queue.Queue = queue.Queue()
        self.poller = StagePoller(rig.stage, rig.now)   # runs for the app's lifetime
        self.poller.start()
        self._handlers = {
            X.StartAcquisition: self._start_acquisition,
            X.StopAcquisition: self._stop_acquisition,
            X.CreateSession: self._create_session,
            X.OpenRecording: self._open_recording,
            X.CloseRecording: self._close_recording,
            X.WriteEvent: self._write_event,
            X.CaptureFocus: self._capture_focus,
            X.MoveZTo: self._move_z_to,
            X.RunCalibration: self._run_calibration,
            X.CentreOn: self._centre_on,
            X.SetTracker: self._set_tracker,
        }
        threading.Thread(target=self._run, name="executor", daemon=True).start()
        store.on_effect(self._effects.put)
        self._load_calibration()

    def close(self) -> None:
        self.poller.stop()

    def wait_idle(self) -> None:
        """Block until every queued effect, including follow-ups, has been carried out."""
        self._effects.join()

    def _run(self) -> None:
        while True:
            effect = self._effects.get()
            try:
                handler = self._handlers.get(type(effect))
                if handler is None:
                    self._store.dispatch(E.AlarmRaised(
                        kind="not_implemented",
                        message=f"{type(effect).__name__} is not implemented yet."))
                else:
                    handler(effect)
            except Exception as exc:      # noqa: BLE001 - surface, never kill the executor
                self._store.dispatch(E.AlarmRaised(
                    kind="executor_error", message=f"{type(effect).__name__} failed: {exc}"))
            finally:
                self._effects.task_done()

    # --- acquisition -------------------------------------------------------------------

    def _start_acquisition(self, _: X.StartAcquisition) -> None:
        rig = self._rig
        self._loop = AcquisitionLoop(rig.clock_camera, rig.follower_camera,
                                     rig.clock_channel.name, rig.follower_channel.name,
                                     self._hub, on_pair=self._on_pair,
                                     on_failure=self._on_acquisition_failure)
        self._loop.start()
        try:
            rig.start()
        except Exception as exc:          # noqa: BLE001
            self._loop.stop()
            self._loop = None
            self._store.dispatch(E.AcquisitionFailed(reason=f"{type(exc).__name__}: {exc}"))

    def _stop_acquisition(self, _: X.StopAcquisition) -> None:
        self._stop_tracker()                  # safety net; the reducer normally did this first
        self._rig.stop()
        if self._loop is not None:
            self._loop.stop()             # flushes the last pairs to the recorder, if any
            self._loop = None

    def _on_pair(self, pair: FramePair) -> None:          # acquisition thread
        with self._recorder_lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.submit(pair)

    def _on_acquisition_failure(self, reason: str) -> None:  # acquisition thread, exiting
        self._rig.stop()
        self._store.dispatch(E.AcquisitionFailed(reason=reason))

    # --- sessions and recordings ------------------------------------------------------------

    def _create_session(self, effect: X.CreateSession) -> None:
        config = {"fps": self._rig.fps,
                  "channels": {ch.name: {"role": ch.role, "shape": list(ch.shape),
                                         "clock": ch is self._rig.clock_channel}
                               for ch in (self._rig.clock_channel, self._rig.follower_channel)}}
        path = create_session(self._data_root, dict(effect.metadata), config)
        self._store.dispatch(E.SessionOpened(path=str(path)))

    def _open_recording(self, effect: X.OpenRecording) -> None:
        recorder = Recorder(Path(effect.session_dir) / effect.name, self._rig.clock_channel,
                            self._rig.follower_channel, fps=self._rig.fps,
                            on_event=self._store.dispatch, queue_size=self._recorder_queue_size,
                            telemetry=StageTelemetry(self.poller,
                                                     lambda: self._store.state.focus_z_um))
        try:
            recorder.start()
        except OSError as exc:
            self._store.dispatch(E.RecordingFailed(reason=f"could not open {effect.name}: {exc}"))
            return
        with self._recorder_lock:
            self._recorder = recorder

    def _close_recording(self, _: X.CloseRecording) -> None:
        with self._recorder_lock:
            recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder.stop()

    def _write_event(self, effect: X.WriteEvent) -> None:
        with self._recorder_lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.mark_event(effect.label)

    # --- focus (ADR 0002) ----------------------------------------------------------------

    def _capture_focus(self, _: X.CaptureFocus) -> None:
        self._store.dispatch(E.FocusCaptured(z_um=self._rig.stage.position_um("z")))

    def _move_z_to(self, effect: X.MoveZTo) -> None:
        self._rig.stage.move_absolute_um("z", effect.z_um)   # clamped to the Z safe range

    # --- calibration and click-to-centre (ADR 0009) --------------------------------------

    def _load_calibration(self) -> None:
        if self._calibration_path is None or not self._calibration_path.exists():
            return
        try:
            self._calibration = RigCalibration.load(self._calibration_path)
        except (OSError, ValueError, KeyError) as exc:
            self._store.dispatch(E.AlarmRaised(kind="calibration_failed",
                                               message=f"Could not load calibration: {exc}"))
            return
        self._store.dispatch(E.CalibrationCompleted(residual_px=self._calibration.residuals()))

    def _run_calibration(self, _: X.RunCalibration) -> None:
        channels = [self._rig.clock_channel.name, self._rig.follower_channel.name]
        try:
            calibration = run_calibration(self._rig.stage, self._hub, channels)
        except CalibrationError as exc:
            self._store.dispatch(E.CalibrationFailed(reason=str(exc)))
            return
        if self._calibration_path is not None:
            calibration.save(self._calibration_path)
        self._calibration = calibration
        self._store.dispatch(E.CalibrationCompleted(residual_px=calibration.residuals()))

    def _centre_on(self, effect: X.CentreOn) -> None:
        camera = None if self._calibration is None else self._calibration.cameras.get(effect.channel)
        if camera is None:
            self._store.dispatch(E.AlarmRaised(kind="calibration_failed",
                                               message=f"No calibration for {effect.channel}."))
            return
        dx, dy = camera.stage_offset_um((effect.x_px, effect.y_px))
        stage = self._rig.stage
        stage.move_absolute_um("x", stage.position_um("x") + dx)
        stage.move_absolute_um("y", stage.position_um("y") + dy)

    # --- tracking (ADR 0008) ------------------------------------------------------------

    def _set_tracker(self, effect: X.SetTracker) -> None:
        self._stop_tracker()
        if not effect.enabled:
            return
        channel = self._resolve_channel(effect.source_channel)
        estimator = self._estimators.get(effect.estimator)
        camera = None if self._calibration is None else self._calibration.cameras.get(channel)
        if channel is None or estimator is None or camera is None:
            missing = ("channel" if channel is None else
                       "estimator" if estimator is None else "calibration")
            self._store.dispatch(E.AlarmRaised(
                kind="tracking_failed",
                message=f"Cannot track ({missing} unavailable for {effect.source_channel!r} / "
                        f"{effect.estimator!r})."))
            self._store.dispatch(E.TrackingStatusChanged(status=TrackingStatus.OFF))
            return
        self._tracker = Tracker(
            hub=self._hub, poller=self.poller, stage=self._rig.stage, calibration=camera,
            source_channel=channel, estimator=estimator, config=self._servo,
            dispatch=self._store.dispatch, now=self._rig.now,
            index_of=lambda frame_id: None if self._loop is None else self._loop.index_of(frame_id),
            log=self._log_tracking, override=self._override)
        self._tracker.start()

    def _stop_tracker(self) -> None:
        tracker, self._tracker = self._tracker, None
        if tracker is not None:
            tracker.stop()

    def _resolve_channel(self, name_or_role: str) -> str | None:
        """A channel name, or a role ("behaviour"/"fluorescence") -> the first camera with it
        (the clock camera first, so ratiometric sessions track on the calcium channel)."""
        channels = (self._rig.clock_channel, self._rig.follower_channel)
        for ch in channels:
            if ch.name == name_or_role:
                return ch.name
        for ch in channels:
            if ch.role == name_or_role:
                return ch.name
        return None

    def _log_tracking(self, **row) -> None:                    # tracker thread
        with self._recorder_lock:
            recorder = self._recorder
        if recorder is not None:
            recorder.log_tracking(**row)
