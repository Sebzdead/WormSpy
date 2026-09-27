"""Render the synthetic worm into simulated camera images.

`ImageGeometry` holds the ground-truth stage→image transform for one camera. That transform
(px per µm, rotation, flip) is exactly what the calibration routine must recover (ADR 0009). The
stage position is the world point at the centre of the field of view, so moving the stage by +d
shifts the worm's image by -d.
"""

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from wormspy.sim.worm import SyntheticWorm


@dataclass(frozen=True)
class ImageGeometry:
    width_px: int
    height_px: int
    um_per_px: float
    rotation_deg: float = 0.0
    flip_x: bool = False
    matrix: np.ndarray = field(init=False, repr=False, compare=False)  # px per µm, 2x2

    def __post_init__(self) -> None:
        a = math.radians(self.rotation_deg)
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        flip = np.diag([-1.0 if self.flip_x else 1.0, 1.0])
        object.__setattr__(self, "matrix", flip @ rot / self.um_per_px)

    @property
    def centre_px(self) -> np.ndarray:
        return np.array([self.width_px / 2, self.height_px / 2])

    def world_to_px(self, points_um: np.ndarray, stage_um) -> np.ndarray:
        """(N, 2) world µm -> (N, 2) pixel (x=col, y=row) for the given stage position."""
        delta = np.asarray(points_um, float) - np.asarray(stage_um, float)
        return delta @ self.matrix.T + self.centre_px

    def px_to_world(self, points_px: np.ndarray, stage_um) -> np.ndarray:
        delta = np.asarray(points_px, float) - self.centre_px
        return delta @ np.linalg.inv(self.matrix).T + np.asarray(stage_um, float)


_SUBPIXEL_SHIFT = 4  # cv2 fixed-point bits for sub-pixel polyline vertices


def _add_noise(img: np.ndarray, noise_sigma: float, rng: np.random.Generator | None,
               max_value: int) -> np.ndarray:
    if noise_sigma > 0:
        rng = rng if rng is not None else np.random.default_rng()
        img = img + rng.normal(0.0, noise_sigma, img.shape)
    return np.clip(np.rint(img), 0, max_value)


def render_behaviour(worm: SyntheticWorm, stage_um, geometry: ImageGeometry,
                     background: int = 200, body: int = 60, body_width_um: float = 60.0,
                     noise_sigma: float = 0.0,
                     rng: np.random.Generator | None = None) -> np.ndarray:
    """Brightfield silhouette: a dark worm on a light background, uint8."""
    img = np.full((geometry.height_px, geometry.width_px), background, np.uint8)
    px = geometry.world_to_px(worm.points, stage_um)
    pts = np.rint(px * (1 << _SUBPIXEL_SHIFT)).astype(np.int32).reshape(-1, 1, 2)
    thickness = max(1, round(body_width_um / geometry.um_per_px))
    cv2.polylines(img, [pts], isClosed=False, color=int(body), thickness=thickness,
                  lineType=cv2.LINE_AA, shift=_SUBPIXEL_SHIFT)
    return _add_noise(img.astype(float), noise_sigma, rng, 255).astype(np.uint8)


def render_fluorescence(worm: SyntheticWorm, stage_um, geometry: ImageGeometry,
                        neuron_index: int = 2, background: float = 1000.0,
                        peak: float = 30000.0, neuron_sigma_um: float = 4.0,
                        noise_sigma: float = 0.0,
                        rng: np.random.Generator | None = None) -> np.ndarray:
    """Fluorescence channel: a Gaussian neuron on a dark background, uint16."""
    img = np.full((geometry.height_px, geometry.width_px), background, float)
    cx, cy = geometry.world_to_px(worm.points[[neuron_index]], stage_um)[0]
    sigma = neuron_sigma_um / geometry.um_per_px
    r = math.ceil(4 * sigma) + 1
    x0, x1 = max(0, int(cx) - r), min(geometry.width_px, int(cx) + r + 1)
    y0, y1 = max(0, int(cy) - r), min(geometry.height_px, int(cy) + r + 1)
    if x0 < x1 and y0 < y1:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
    return _add_noise(img, noise_sigma, rng, 65535).astype(np.uint16)
