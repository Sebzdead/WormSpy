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

BEAD_SPACING_UM = 60.0
BEAD_RADIUS_UM = 6.0


def _target_beads_px(stage_um, geometry: ImageGeometry) -> np.ndarray:
    """Pixel positions of the calibration target's beads in view.

    Beads sit on a 60 µm world grid, each jittered by a deterministic hash of its cell. The
    pattern is irregular (phase correlation needs that) but always the same for a given patch
    of plate, like a bead or grid calibration slide.
    """
    w, h = geometry.width_px, geometry.height_px
    corners = geometry.px_to_world(np.array([[0, 0], [w, 0], [0, h], [w, h]], float), stage_um)
    margin = 2 * BEAD_SPACING_UM
    lo = np.floor((corners.min(axis=0) - margin) / BEAD_SPACING_UM).astype(np.int64)
    hi = np.ceil((corners.max(axis=0) + margin) / BEAD_SPACING_UM).astype(np.int64)
    ix, iy = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
    ix, iy = ix.ravel().astype(np.uint64), iy.ravel().astype(np.uint64)
    with np.errstate(over="ignore"):
        hx = (ix * np.uint64(73856093)) ^ (iy * np.uint64(19349663))
        hy = (ix * np.uint64(83492791)) ^ (iy * np.uint64(2654435761))
    jitter = np.stack([(hx % 1000) / 1000.0, (hy % 1000) / 1000.0], axis=1) - 0.5
    cells = np.stack([ix.astype(np.int64), iy.astype(np.int64)], axis=1).astype(float)
    world = (cells + 0.6 * jitter) * BEAD_SPACING_UM
    px = geometry.world_to_px(world, stage_um)
    inside = ((px[:, 0] > -10) & (px[:, 0] < w + 10) & (px[:, 1] > -10) & (px[:, 1] < h + 10))
    return px[inside]


def _add_noise(img: np.ndarray, noise_sigma: float, rng: np.random.Generator | None,
               max_value: int) -> np.ndarray:
    if noise_sigma > 0:
        rng = rng if rng is not None else np.random.default_rng()
        img = img + rng.normal(0.0, noise_sigma, img.shape)
    return np.clip(np.rint(img), 0, max_value)


def render_behaviour(worm: SyntheticWorm, stage_um, geometry: ImageGeometry,
                     background: int = 200, body: int = 60, body_width_um: float = 60.0,
                     noise_sigma: float = 0.0, rng: np.random.Generator | None = None,
                     target: bool = False) -> np.ndarray:
    """Brightfield silhouette: a dark worm on a light background, uint8.
    `target` adds the calibration bead slide (dark beads)."""
    img = np.full((geometry.height_px, geometry.width_px), background, np.uint8)
    if target:  # beads are optically blurred: smooth dark spots, not hard discs
        shaded = img.astype(float)
        for bx, by in _target_beads_px(stage_um, geometry):
            _add_spot(shaded, bx, by, body - background, BEAD_RADIUS_UM / 2 / geometry.um_per_px)
        img = np.clip(np.rint(shaded), 0, 255).astype(np.uint8)
    px = geometry.world_to_px(worm.points, stage_um)
    thickness = max(1, round(body_width_um / geometry.um_per_px))
    margin = thickness + 2
    if (px[:, 0].max() < -margin or px[:, 0].min() > geometry.width_px + margin
            or px[:, 1].max() < -margin or px[:, 1].min() > geometry.height_px + margin):
        return _add_noise(img.astype(float), noise_sigma, rng, 255).astype(np.uint8)  # not in view
    pts = np.rint(px * (1 << _SUBPIXEL_SHIFT)).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(img, [pts], isClosed=False, color=int(body), thickness=thickness,
                  lineType=cv2.LINE_AA, shift=_SUBPIXEL_SHIFT)
    return _add_noise(img.astype(float), noise_sigma, rng, 255).astype(np.uint8)


def render_fluorescence(worm: SyntheticWorm, stage_um, geometry: ImageGeometry,
                        neuron_index: int = 2, background: float = 1000.0,
                        peak: float = 30000.0, neuron_sigma_um: float = 4.0,
                        noise_sigma: float = 0.0, rng: np.random.Generator | None = None,
                        target: bool = False) -> np.ndarray:
    """Fluorescence channel: a Gaussian neuron on a dark background, uint16.
    `target` adds the calibration bead slide (fluorescent beads)."""
    img = np.full((geometry.height_px, geometry.width_px), background, float)
    if target:
        for bx, by in _target_beads_px(stage_um, geometry):
            _add_spot(img, bx, by, peak / 2, BEAD_RADIUS_UM / 2 / geometry.um_per_px)
    cx, cy = geometry.world_to_px(worm.points[[neuron_index]], stage_um)[0]
    _add_spot(img, cx, cy, peak, neuron_sigma_um / geometry.um_per_px)
    return _add_noise(img, noise_sigma, rng, 65535).astype(np.uint16)


def _add_spot(img: np.ndarray, cx: float, cy: float, peak: float, sigma: float) -> None:
    height, width = img.shape
    r = math.ceil(4 * sigma) + 1
    x0, x1 = max(0, int(cx) - r), min(width, int(cx) + r + 1)
    y0, y1 = max(0, int(cy) - r), min(height, int(cy) + r + 1)
    if x0 < x1 and y0 < y1:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        img[y0:y1, x0:x1] += peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2))
