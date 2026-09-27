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
from pathlib import Path

from wormspy.acquisition.loop import AcquisitionLoop
from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.recorder import Recorder
from wormspy.acquisition.rig import RigHandle
from wormspy.acquisition.session import create_session
from wormspy.control import StateStore
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.core.frames import FrameHub


class Executor:
    def __init__(self, store: StateStore, rig: RigHandle, hub: FrameHub, data_root: Path,
                 recorder_queue_size: int = 64) -> None:
        self._store = store
        self._rig = rig
        self._hub = hub
        self._data_root = Path(data_root)
        self._recorder_queue_size = recorder_queue_size
        self._loop: AcquisitionLoop | None = None
        self._recorder: Recorder | None = None
        self._recorder_lock = threading.Lock()
        self._effects: queue.Queue = queue.Queue()
        self._handlers = {
            X.StartAcquisition: self._start_acquisition,
            X.StopAcquisition: self._stop_acquisition,
            X.CreateSession: self._create_session,
            X.OpenRecording: self._open_recording,
            X.CloseRecording: self._close_recording,
            X.WriteEvent: self._write_event,
        }
        threading.Thread(target=self._run, name="executor", daemon=True).start()
        store.on_effect(self._effects.put)

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
                            on_event=self._store.dispatch, queue_size=self._recorder_queue_size)
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
