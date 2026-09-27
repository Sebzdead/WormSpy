"""Turn the clock and follower camera streams into FramePairs (ADR 0006).

Each clock exposure triggers exactly one follower exposure, and both cameras' device frame IDs
are reset together when acquisition starts. A clock frame and a follower frame with the same
device frame ID are therefore a pair. `frame_index` counts from the first clock frame and is
contiguous.

A frame counts as dropped once its channel has delivered a *later* ID. The pair is then emitted
with that side set to None. Pairs are emitted strictly in index order, as soon as both channels
are resolved. `flush()` at stop resolves everything seen so far.

`skew_ns` is the follower-minus-clock device timestamp difference relative to the first pair's
difference. The two cameras keep separate clocks, so only the *change* in offset is meaningful.
A large skew means the pairing is suspect.
"""

from dataclasses import dataclass

from wormspy.core.frames import Frame


@dataclass(frozen=True)
class FramePair:
    frame_index: int
    clock: Frame | None     # None: the clock frame was dropped
    follower: Frame | None  # None: the follower frame was dropped
    skew_ns: int | None     # None when either frame is missing


class FramePairer:
    def __init__(self) -> None:
        self._origin: int | None = None      # device id of frame_index 0
        self._next_index = 0
        self._clock: dict[int, Frame] = {}
        self._follower: dict[int, Frame] = {}
        self._clock_max: int | None = None
        self._follower_max: int | None = None
        self._reference_offset_ns: int | None = None

    def index_of(self, device_frame_id: int) -> int | None:
        """The frame_index a device frame ID has (or will have) in this acquisition run."""
        if self._origin is None or device_frame_id < self._origin:
            return None
        return device_frame_id - self._origin

    def add_clock(self, frame: Frame) -> list[FramePair]:
        if self._origin is None:
            self._origin = frame.device_frame_id
            self._follower = {i: f for i, f in self._follower.items() if i >= self._origin}
        self._clock[frame.device_frame_id] = frame
        self._clock_max = _max(self._clock_max, frame.device_frame_id)
        return self._emit_ready()

    def add_follower(self, frame: Frame) -> list[FramePair]:
        if self._origin is not None and frame.device_frame_id < self._origin:
            return []
        self._follower[frame.device_frame_id] = frame
        self._follower_max = _max(self._follower_max, frame.device_frame_id)
        return self._emit_ready()

    def flush(self) -> list[FramePair]:
        """Emit every remaining index up to the highest ID seen; missing frames are drops."""
        if self._origin is None:
            return []
        last = max(m for m in (self._clock_max, self._follower_max) if m is not None)
        pairs = []
        while self._origin + self._next_index <= last:
            pairs.append(self._pop(self._origin + self._next_index))
        return pairs

    def _emit_ready(self) -> list[FramePair]:
        pairs = []
        while self._origin is not None:
            device_id = self._origin + self._next_index
            if not (_resolved(self._clock, self._clock_max, device_id)
                    and _resolved(self._follower, self._follower_max, device_id)):
                break
            pairs.append(self._pop(device_id))
        return pairs

    def _pop(self, device_id: int) -> FramePair:
        clock = self._clock.pop(device_id, None)
        follower = self._follower.pop(device_id, None)
        pair = FramePair(self._next_index, clock, follower, self._skew(clock, follower))
        self._next_index += 1
        return pair

    def _skew(self, clock: Frame | None, follower: Frame | None) -> int | None:
        if clock is None or follower is None:
            return None
        offset = follower.device_timestamp_ns - clock.device_timestamp_ns
        if self._reference_offset_ns is None:
            self._reference_offset_ns = offset
        return offset - self._reference_offset_ns


def _max(current: int | None, value: int) -> int:
    return value if current is None else max(current, value)


def _resolved(frames: dict[int, Frame], max_seen: int | None, device_id: int) -> bool:
    return device_id in frames or (max_seen is not None and max_seen > device_id)
