import time
from abc import ABC, abstractmethod
from typing import Iterable, List, Optional, Set

import numpy as np

from core.frames import Frame


class Camera(ABC):
    """Acquisition interface. Adapters wrap real SDKs; FakeCamera is the test double."""

    @abstractmethod
    def read(self) -> Optional[Frame]:
        """Grab the next frame, or return None if the grab failed / stream ended."""

    @abstractmethod
    def is_open(self) -> bool:
        ...

    @abstractmethod
    def release(self) -> None:
        ...


class FakeCamera(Camera):
    """Deterministic camera for tests. Emits a scripted list of images."""

    def __init__(self, images: Iterable[np.ndarray],
                 frame_ids: Optional[List[int]] = None,
                 fail_on: Optional[Set[int]] = None) -> None:
        self._images: List[np.ndarray] = list(images)
        self._frame_ids = frame_ids if frame_ids is not None else list(range(len(self._images)))
        self._fail_on = fail_on or set()
        self._pos = 0
        self._open = True

    def read(self) -> Optional[Frame]:
        if not self._open or self._pos >= len(self._images):
            return None
        idx = self._pos
        self._pos += 1
        if idx in self._fail_on:
            return None
        return Frame(image=self._images[idx],
                     device_frame_id=self._frame_ids[idx],
                     capture_ts=time.monotonic())

    def is_open(self) -> bool:
        return self._open

    def release(self) -> None:
        self._open = False
