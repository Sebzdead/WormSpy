"""FLIR/Spinnaker camera adapter via EasyPySpin. Imports the SDK lazily so tests don't load it.

Sets the camera as the acquisition clock: a fixed AcquisitionFrameRate and a fixed exposure,
with auto-exposure/auto-gain off. EasyPySpin exposes these through cv2-style properties; the
device frame id is read from the grabbed image metadata when available, else -1."""

import time
from typing import Optional

import cv2
import numpy as np

from core.cameras import Camera
from core.frames import Frame


class SpinnakerCamera(Camera):
    def __init__(self, identifier, frame_rate: float, exposure_us: float) -> None:
        import EasyPySpin    # lazy import
        self._cap = EasyPySpin.VideoCapture(identifier)
        if not self._cap.isOpened():
            print(f"SpinnakerCamera: could not open camera {identifier!r}")
            return
        # Camera is the clock: disable auto, pin rate + exposure.
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)        # manual exposure
        self._cap.set(cv2.CAP_PROP_EXPOSURE, exposure_us)
        self._cap.set(cv2.CAP_PROP_FPS, frame_rate)
        self.width = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.height = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    def read(self) -> Optional[Frame]:
        ok, image = self._cap.read()
        ts = time.monotonic()
        if not ok or image is None:
            return None
        if image.dtype != np.uint8:
            # Preserve raw for recording elsewhere; this path is display-oriented in Phase 1.
            image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        frame_id = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
        return Frame(image=image, device_frame_id=frame_id, capture_ts=ts)

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
