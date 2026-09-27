"""AcquisitionLoop: the single reader of both cameras (ADR 0006).

Each step reads one frame from each camera, publishes it to the FrameHub under its channel name
(display and tracking read the latest frame there, never slowing acquisition), and feeds the
FramePairer. Every completed pair goes to `on_pair` (the recorder, when recording). Camera
reads may block (real hardware, with a timeout) or return None at once (simulated cameras). A
camera error ends the thread and is reported through `on_failure`.
"""

import threading
import time
from collections.abc import Callable

from wormspy.acquisition.pairing import FramePair, FramePairer
from wormspy.core.cameras import Camera
from wormspy.core.frames import FrameHub

IDLE_SLEEP_S = 0.001


class AcquisitionLoop:
    def __init__(self, clock: Camera, follower: Camera, clock_name: str, follower_name: str,
                 hub: FrameHub, on_pair: Callable[[FramePair], None],
                 on_failure: Callable[[str], None] | None = None) -> None:
        self._clock, self._follower = clock, follower
        self._clock_name, self._follower_name = clock_name, follower_name
        self._hub = hub
        self._on_pair = on_pair
        self._on_failure = on_failure
        self._pairer = FramePairer()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> int:
        """Read at most one frame per camera; returns how many frames were read."""
        read = 0
        frame = self._clock.read()
        if frame is not None:
            read += 1
            self._hub.publish(self._clock_name, frame)
            self._emit(self._pairer.add_clock(frame))
        frame = self._follower.read()
        if frame is not None:
            read += 1
            self._hub.publish(self._follower_name, frame)
            self._emit(self._pairer.add_follower(frame))
        return read

    def flush(self) -> None:
        """Resolve everything seen so far; frames still missing count as dropped."""
        self._emit(self._pairer.flush())

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="acquisition", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.flush()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.step() == 0:
                    time.sleep(IDLE_SLEEP_S)
            except Exception as exc:      # noqa: BLE001 - any camera error ends acquisition
                if self._on_failure is not None:
                    self._on_failure(f"{type(exc).__name__}: {exc}")
                return

    def _emit(self, pairs: list[FramePair]) -> None:
        for pair in pairs:
            self._on_pair(pair)
