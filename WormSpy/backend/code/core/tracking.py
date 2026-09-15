"""Pure tracking helpers (moved from app.py) and the Tracker thread.

The Tracker reads the latest brightfield frame from the hub, computes a target stage move via
an injected `compute` callback, and commands the stage. Compute is injected so the heavy/slow
algorithm (e.g. DeepLabCut) can run here without ever throttling the AcquisitionEngine."""

import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np
from skimage.measure import label, regionprops

from core.frames import FrameHub
from core.stage import Stage


def find_worm_cms(processed_frame: np.ndarray, factor: int,
                  initial_coords: Tuple[float, float]) -> Tuple[float, float]:
    """Centroid (x=col, y=row) of the largest connected region, rescaled by `factor`.
    Falls back to initial_coords when no region is found."""
    labeled = label(processed_frame)
    regions = regionprops(labeled)
    regions_by_area = sorted(regions, key=lambda r: r.area, reverse=True)
    if regions_by_area:
        coords = regions_by_area[0].centroid           # (row, col)
    else:
        return initial_coords
    coords = (round(coords[1]), round(coords[0]))       # -> (x=col, y=row)
    return (coords[0] * factor, coords[1] * factor)


def simple_to_center(centroid_x: float, centroid_y: float,
                     resolution: Tuple[float, float],
                     total_mm_x: float, total_mm_y: float,
                     orient_x: int, orient_y: int) -> Tuple[float, float]:
    """Millimetres the stage must move to bring (centroid_x, centroid_y) to frame center."""
    percent_x = float(centroid_x) / float(resolution[0])
    percent_y = float(centroid_y) / float(resolution[1])
    millis_x = percent_x * total_mm_x
    millis_y = percent_y * total_mm_y
    move_x = orient_x * (millis_x - total_mm_x / 2)
    move_y = orient_y * (millis_y - total_mm_y / 2)
    return move_x, move_y


# Callback types
EnabledFn = Callable[[], bool]
ModeFn = Callable[[], int]
ComputeFn = Callable[[np.ndarray, int], Tuple[float, float]]   # (frame, mode) -> (dx, dy) native
OnMoveFn = Callable[[float, float, bool], None]


class Tracker:
    """Runs in its own thread. Best-effort consumer of the latest brightfield frame."""

    def __init__(self, hub: FrameHub, stage: Stage,
                 enabled: EnabledFn, mode: ModeFn, compute: ComputeFn,
                 on_move: Optional[OnMoveFn] = None,
                 stream: str = "left", interval_s: float = 0.0) -> None:
        self._hub = hub
        self._stage = stage
        self._enabled = enabled
        self._mode = mode
        self._compute = compute
        self._on_move = on_move
        self._stream = stream
        self._interval_s = interval_s
        self._stop = threading.Event()

    def step(self) -> None:
        if not self._enabled():
            return
        frame = self._hub.latest(self._stream)
        if frame is None:
            return
        dx, dy = self._compute(frame.image, self._mode())
        hit_x = self._stage.move_relative("x", dx)
        hit_y = self._stage.move_relative("y", dy)
        if self._on_move is not None:
            self._on_move(dx, dy, hit_x or hit_y)

    def run(self) -> None:
        while not self._stop.is_set():
            self.step()
            if self._interval_s:
                time.sleep(self._interval_s)

    def stop(self) -> None:
        self._stop.set()
