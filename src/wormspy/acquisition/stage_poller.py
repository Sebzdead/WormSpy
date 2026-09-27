"""StagePoller: the single reader of stage position (ADR 0007/0008).

A thread samples XYZ at a fixed rate into a short, timestamped history. Consumers ask for the
position *at a frame's capture time* and get it interpolated, so neither acquisition nor
tracking ever blocks on a serial read. The poller's clock must be the same clock that stamps the
frames' `capture_ts` (RigHandle.now: time.monotonic on the rig, simulated time in the sim).
"""

import bisect
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from wormspy.acquisition.pairing import FramePair
from wormspy.core.stage import Stage

Z_MOVED_THRESHOLD_UM = 0.5


@dataclass(frozen=True)
class StageSample:
    t: float
    x_um: float
    y_um: float
    z_um: float


class StagePoller:
    def __init__(self, stage: Stage, clock: Callable[[], float], rate_hz: float = 50.0,
                 history_s: float = 10.0) -> None:
        self._stage = stage
        self._clock = clock
        self._interval_s = 1.0 / rate_hz
        self._samples: deque[StageSample] = deque(maxlen=max(1, round(rate_hz * history_s)))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)

    def sample_once(self) -> None:
        t = self._clock()
        sample = StageSample(t, *(self._stage.position_um(axis) for axis in "xyz"))
        with self._lock:
            self._samples.append(sample)

    def latest(self) -> StageSample | None:
        with self._lock:
            return self._samples[-1] if self._samples else None

    def at(self, t: float) -> StageSample | None:
        """Position at time t, interpolated linearly; clamped to the oldest/newest sample."""
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return None
        times = [s.t for s in samples]
        i = bisect.bisect_left(times, t)
        if i == 0:
            return samples[0]
        if i == len(samples):
            return samples[-1]
        a, b = samples[i - 1], samples[i]
        w = (t - a.t) / (b.t - a.t) if b.t > a.t else 0.0
        return StageSample(t, *(getattr(a, f) + w * (getattr(b, f) - getattr(a, f))
                                for f in ("x_um", "y_um", "z_um")))

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="stage-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self._interval_s)


class StageTelemetry:
    """Per-frame stage columns for frames.csv: x_um, y_um, z_um, z_moved, focus_set_z_um.

    `z_moved` is True when Z changed since the previous frame. The operator may refocus during a
    recording (ADR 0002), and those frames must be excludable in analysis.
    """

    def __init__(self, poller: StagePoller, focus_z_um: Callable[[], float | None]) -> None:
        self._poller = poller
        self._focus_z_um = focus_z_um
        self._previous_z: float | None = None

    def __call__(self, pair: FramePair) -> dict[str, object]:
        row: dict[str, object] = {"focus_set_z_um": self._focus_z_um()}
        frame = pair.clock or pair.follower
        sample = None if frame is None else self._poller.at(frame.capture_ts)
        if sample is None:
            return row
        row.update(x_um=sample.x_um, y_um=sample.y_um, z_um=sample.z_um)
        row["z_moved"] = (self._previous_z is not None
                          and abs(sample.z_um - self._previous_z) > Z_MOVED_THRESHOLD_UM)
        self._previous_z = sample.z_um
        return row
