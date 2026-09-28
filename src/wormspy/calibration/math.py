"""Calibration maths (ADR 0009): image shift by phase correlation, and the stage→image matrix.

Convention (the same as the simulator's ImageGeometry): the stage position is the world point
at the centre of the *reference* camera's field of view, and `matrix` (px per µm, 2×2) maps a
world offset from that point to a pixel offset. Moving the stage by d therefore moves the image
content by -matrix·d, in every camera.

The other camera sees that same world point at `origin_px`, a few pixels off its own centre:
two cameras behind one objective are never perfectly aligned. The matrices alone give the
rotation and scale between the cameras; `register_origin` measures the translation.
"""

import json
from dataclasses import dataclass

import cv2
import numpy as np


def measure_shift(reference: np.ndarray, image: np.ndarray) -> tuple[float, float]:
    """How far the content moved from `reference` to `image`, in pixels (x, y), sub-pixel."""
    dx, dy, _response = _phase_correlate(reference, image)
    return dx, dy


def _phase_correlate(reference: np.ndarray, image: np.ndarray,
                     invert: bool = False) -> tuple[float, float, float]:
    a = reference.astype(np.float32)
    b = image.astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    if invert:
        b = -b
    window = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, window)
    return float(dx), float(dy), float(response)


def register_origin(reference_image: np.ndarray, reference_matrix: np.ndarray,
                    image: np.ndarray, matrix: np.ndarray) -> tuple[np.ndarray, float]:
    """The pixel of `image` that sees the reference camera's centre, and the match quality.

    Both images must show the same static scene, taken with the stage still. The reference
    image is warped into the other camera's pixel grid (using the two calibrated matrices, and
    assuming the centres line up), then phase-correlated: the remaining shift is the offset.
    Brightfield and fluorescence show the same beads with opposite contrast, so both
    polarities are tried and the stronger match wins. `response` is the phase-correlation peak
    (1 = perfect match, near 0 = no common features).
    """
    ref_centre = np.array([reference_image.shape[1] / 2, reference_image.shape[0] / 2])
    centre = np.array([image.shape[1] / 2, image.shape[0] / 2])
    to_image = matrix @ np.linalg.inv(reference_matrix)        # ref pixel offsets -> image px
    shift = centre - to_image @ ref_centre
    warped = cv2.warpAffine(reference_image.astype(np.float32),
                            np.hstack([to_image, shift[:, None]]).astype(np.float64),
                            (image.shape[1], image.shape[0]), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT,
                            borderValue=float(reference_image.mean()))
    dx, dy, response = max((_phase_correlate(warped, image, invert) for invert in (False, True)),
                           key=lambda result: result[2])
    return centre + (dx, dy), response


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
    origin_px: tuple[float, float] | None = None   # sees the stage point; None = not measured

    @property
    def centre_px(self) -> np.ndarray:
        height, width = self.image_shape
        return np.array([width / 2, height / 2])

    @property
    def origin(self) -> np.ndarray:
        """The pixel that sees the stage position (the image centre until registered)."""
        return self.centre_px if self.origin_px is None else np.asarray(self.origin_px, float)

    def stage_offset_um(self, px) -> tuple[float, float]:
        """The stage move (µm) that brings pixel `px` to the centre of this camera's image.

        For the reference camera this is also the pixel's plate position relative to the
        stage; for the other camera the plate position is relative to `origin`.
        """
        offset = np.linalg.solve(self.matrix, np.asarray(px, float) - self.centre_px)
        return float(offset[0]), float(offset[1])

    def to_json(self) -> dict:
        return {"matrix_px_per_um": self.matrix.tolist(), "residual_px": self.residual_px,
                "image_shape": list(self.image_shape),
                "origin_px": None if self.origin_px is None else list(self.origin_px)}

    @classmethod
    def from_json(cls, data: dict) -> "CameraCalibration":
        origin = data.get("origin_px")              # absent in files from before registration
        return cls(matrix=np.array(data["matrix_px_per_um"], float),
                   residual_px=float(data["residual_px"]),
                   image_shape=tuple(data["image_shape"]),
                   origin_px=None if origin is None else (float(origin[0]), float(origin[1])))

    def dumps(self) -> str:
        return json.dumps(self.to_json())
