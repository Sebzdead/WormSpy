import threading
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class Frame:
    """A single grabbed image plus the metadata needed to align it offline."""
    image: np.ndarray
    device_frame_id: int   # camera-provided frame id; -1 if the camera cannot supply one
    capture_ts: float      # time.monotonic() captured as close to the grab as possible


@dataclass
class AcquiredPair:
    """One acquisition tick: a brightfield and a fluorescence frame sharing an index."""
    frame_index: int       # monotonic counter assigned by the engine, contiguous, never reused
    capture_ts: float      # engine timestamp for the tick (time.monotonic())
    left: Optional[Frame]  # brightfield/behaviour; None if that grab failed this tick
    right: Optional[Frame] # fluorescence/calcium; None if that grab failed this tick


class FrameHub:
    """Thread-safe store of the latest frame per named stream, for display and tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Dict[str, Frame] = {}

    def publish(self, name: str, frame: Frame) -> None:
        with self._lock:
            self._latest[name] = frame

    def latest(self, name: str) -> Optional[Frame]:
        with self._lock:
            return self._latest.get(name)
