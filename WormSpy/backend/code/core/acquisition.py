import threading
import time
from typing import Callable, Optional

from core.cameras import Camera
from core.frames import AcquiredPair, FrameHub

Sink = Callable[[AcquiredPair], None]


class AcquisitionEngine:
    """Single loop that owns both cameras. Grabs both per tick, assigns a shared monotonic
    frame_index, publishes the latest frame of each to the hub for display/tracking, and
    forwards the paired result to an optional sink (the recorder). This is the only place that
    reads from the cameras, so the two streams cannot drift relative to each other."""

    LEFT = "left"
    RIGHT = "right"

    def __init__(self, left: Camera, right: Camera, hub: FrameHub,
                 sink: Optional[Sink] = None) -> None:
        self._left = left
        self._right = right
        self._hub = hub
        self._sink = sink
        self._next_index = 0
        self._stop = threading.Event()
        self.ticks_completed = 0

    def run_once(self) -> AcquiredPair:
        index = self._next_index
        self._next_index += 1
        ts = time.monotonic()

        left_frame = self._left.read()
        right_frame = self._right.read()

        if left_frame is not None:
            self._hub.publish(self.LEFT, left_frame)
        if right_frame is not None:
            self._hub.publish(self.RIGHT, right_frame)

        pair = AcquiredPair(frame_index=index, capture_ts=ts,
                            left=left_frame, right=right_frame)
        if self._sink is not None:
            self._sink(pair)
        self.ticks_completed += 1
        return pair

    def run(self, max_ticks: Optional[int] = None) -> None:
        produced = 0
        while not self._stop.is_set():
            if max_ticks is not None and produced >= max_ticks:
                break
            if not (self._left.is_open() and self._right.is_open()):
                break
            self.run_once()
            produced += 1

    def stop(self) -> None:
        self._stop.set()
