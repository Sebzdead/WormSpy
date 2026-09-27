"""The tracker (ADR 0008): its own thread, a best-effort consumer of the latest frame.

Each step:
1. If the source channel has a new frame, run the estimator on it and convert the tracked pixel
   to a plate position: the stage position *at that frame's capture time* (StagePoller) plus the
   calibrated pixel offset. That absolute target is fed to the TargetFilter, and the result is
   logged (tracking.csv while recording).
2. Run one VelocityServo tick toward the current target and command XY velocity. A command is
   sent only when the velocity changes meaningfully, to spare the serial line.
Status changes (tracking / lost / override / limit) are dispatched to the state machine.
`step()` is public so tests can drive the whole closed loop synchronously in simulated time.
"""

import threading
from collections.abc import Callable

from wormspy.acquisition.stage_poller import StagePoller
from wormspy.calibration.math import CameraCalibration
from wormspy.control import events as E
from wormspy.control.state import TrackingStatus
from wormspy.core.frames import FrameHub
from wormspy.core.stage import LIMIT_TOLERANCE_UM, Stage
from wormspy.tracking.controller import ServoConfig, TargetFilter, VelocityServo

RESEND_UM_S = 5.0     # resend a velocity only if it changed by more than this


class Tracker:
    def __init__(self, hub: FrameHub, poller: StagePoller, stage: Stage,
                 calibration: CameraCalibration, source_channel: str, estimator,
                 config: ServoConfig, dispatch: Callable[[object], None],
                 now: Callable[[], float],
                 index_of: Callable[[int], int | None] = lambda _: None,
                 log: Callable[..., None] | None = None,
                 override: Callable[[], bool] = lambda: False, rate_hz: float = 50.0) -> None:
        self._hub, self._poller, self._stage = hub, poller, stage
        self._calibration = calibration
        self._source = source_channel
        self._estimator = estimator
        self._dispatch = dispatch
        self._now = now
        self._index_of = index_of
        self._log = log
        self._override = override
        self._interval_s = 1.0 / rate_hz
        self._filter = TargetFilter(config)
        self._servo = VelocityServo(config)
        self._last_frame_id: int | None = None
        self._last_t: float | None = None
        self._sent: dict[str, float | None] = {"x": None, "y": None}
        self.status: TrackingStatus | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        for axis in "xy":
            self._stage.stop(axis)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.step()
            self._stop.wait(self._interval_s)

    # --- one step ---------------------------------------------------------------------------

    def step(self) -> None:
        self._evaluate_new_frame()
        now = self._now()
        dt = self._interval_s if self._last_t is None else max(now - self._last_t, 1e-6)
        self._last_t = now
        latest = self._poller.latest()
        if latest is None:
            return
        position = (latest.x_um, latest.y_um)
        override = self._override()
        velocity, status = self._servo.step(dt, self._filter.target, position, override=override,
                                            at_limit=self._limits(position),
                                            target_velocity=self._filter.velocity)
        if velocity is None:
            self._sent = {"x": None, "y": None}   # after an override, resend from scratch
        else:
            for axis, v in zip("xy", velocity, strict=True):
                self._command(axis, v)
        self._set_status(status)

    def _evaluate_new_frame(self) -> None:
        frame = self._hub.latest(self._source)
        if frame is None or frame.device_frame_id == self._last_frame_id:
            return
        self._last_frame_id = frame.device_frame_id
        estimate = self._estimator(frame.image)
        world = None
        if estimate.point_px is not None:
            sample = self._poller.at(frame.capture_ts)
            if sample is not None:
                dx, dy = self._calibration.stage_offset_um(estimate.point_px)
                world = (sample.x_um + dx, sample.y_um + dy)
        status = self._filter.update(frame.capture_ts, world)
        if self._log is not None:
            self._log(frame_index=self._index_of(frame.device_frame_id),
                      source_channel=self._source, frame_id=frame.device_frame_id,
                      capture_ts=frame.capture_ts, estimator=self._estimator.name,
                      target_px=estimate.point_px, worm_um=world,
                      accepted=world is not None and self._filter.target == world,
                      status=status.value)

    def _limits(self, position: tuple[float, float]) -> dict[str, str]:
        sides = {}
        for axis, value in zip("xy", position, strict=True):
            lo, hi = self._stage.limits_um(axis)
            if value >= hi - LIMIT_TOLERANCE_UM:
                sides[axis] = "max"
            elif value <= lo + LIMIT_TOLERANCE_UM:
                sides[axis] = "min"
        return sides

    def _command(self, axis: str, velocity: float) -> None:
        sent = self._sent[axis]
        if sent is not None and abs(velocity - sent) <= RESEND_UM_S and (velocity == 0) == (sent == 0):
            return
        if velocity == 0:
            self._stage.stop(axis)
        else:
            self._stage.move_velocity_um_s(axis, velocity)
        self._sent[axis] = velocity

    def _set_status(self, status: TrackingStatus) -> None:
        if status is not self.status:
            self.status = status
            self._dispatch(E.TrackingStatusChanged(status=status))
