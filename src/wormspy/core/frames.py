import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    """A single grabbed image plus the metadata needed to align it offline."""
    image: np.ndarray
    device_frame_id: int   # camera-provided frame id; -1 if the camera cannot supply one
    capture_ts: float      # time.monotonic() captured as close to the grab as possible
    device_timestamp_ns: int = -1  # camera's own exposure timestamp (chunk data); -1 if unknown


class FrameHub:
    """Thread-safe store of the latest frame per named stream, for display and tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Frame] = {}

    def publish(self, name: str, frame: Frame) -> None:
        with self._lock:
            self._latest[name] = frame

    def latest(self, name: str) -> Frame | None:
        with self._lock:
            return self._latest.get(name)
