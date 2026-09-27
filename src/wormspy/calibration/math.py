"""Calibration maths (ADR 0009): image shift by phase correlation, and the stage→image matrix.

Convention (the same as the simulator's ImageGeometry): the stage position is the world point
at the centre of the field of view, and `matrix` (px per µm, 2×2) maps a world offset from that
point to a pixel offset from the image centre. Moving the stage by d therefore moves the image
content by -matrix·d.
"""

import json
from dataclasses import dataclass

import cv2
import numpy as np


def measure_shift(reference: np.ndarray, image: np.ndarray) -> tuple[float, float]:
    """How far the content moved from `reference` to `image`, in pixels (x, y), sub-pixel."""
    a = reference.astype(np.float32)
    b = image.astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    window = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), _response = cv2.phaseCorrelate(a, b, window)
    return float(dx), float(dy)


def fit_matrix(deltas_um: np.ndarray, shifts_px: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares matrix M with shift = -M·delta. Returns (M, RMS residual in px)."""
    deltas = np.asarray(deltas_um, float)
    shifts = np.asarray(shifts_px, float)
    if np.linalg.matrix_rank(deltas) < 2:
        raise ValueError("calibration moves must span both stage axes")
    solution, *_ = np.linalg.lstsq(deltas, -shifts, rcond=None)
    matrix = solution.T
    predicted = -(deltas @ matrix.T)
    residual = float(np.sqrt(np.mean(np.sum((predicted - shifts) ** 2, axis=1))))
    return matrix, residual


@dataclass(frozen=True)
class CameraCalibration:
    matrix: np.ndarray                 # px per µm, world offset -> pixel offset
    residual_px: float                 # RMS fit residual; large values mean a bad calibration
    image_shape: tuple[int, int]       # (height, width) the calibration was measured at

    @property
    def centre_px(self) -> np.ndarray:
        height, width = self.image_shape
        return np.array([width / 2, height / 2])

    def stage_offset_um(self, px) -> tuple[float, float]:
        """World offset (µm) of pixel `px` from the point under the stage (FOV centre).

        To centre that pixel, move the stage by this offset. Its plate position is the stage
        position plus this offset.
        """
        offset = np.linalg.solve(self.matrix, np.asarray(px, float) - self.centre_px)
        return float(offset[0]), float(offset[1])

    def to_json(self) -> dict:
        return {"matrix_px_per_um": self.matrix.tolist(), "residual_px": self.residual_px,
                "image_shape": list(self.image_shape)}

    @classmethod
    def from_json(cls, data: dict) -> "CameraCalibration":
        return cls(matrix=np.array(data["matrix_px_per_um"], float),
                   residual_px=float(data["residual_px"]),
                   image_shape=tuple(data["image_shape"]))

    def dumps(self) -> str:
        return json.dumps(self.to_json())
