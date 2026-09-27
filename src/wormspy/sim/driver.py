"""SimDriver: ticks a SimRig on its own thread, standing in for the free-running clock camera.

Ticks are scheduled on absolute deadlines, so the frame rate doesn't drift with render time.
`speed` > 1 runs simulated time faster than real time (for tests). If rendering can't keep up,
ticks run back-to-back rather than being skipped: a real clock camera never skips exposures.
"""

import threading
import time

from wormspy.sim.rig import SimRig


class SimDriver:
    def __init__(self, rig: SimRig, speed: float = 1.0) -> None:
        self._rig = rig
        self._interval_s = rig.frame_period_s / speed
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sim-driver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            self._rig.tick()
            next_tick += self._interval_s
            delay = next_tick - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
