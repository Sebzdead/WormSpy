"""Target estimators (ADR 0008): one frame in, the tracked point out, or "nothing plausible".

- DarkObject: the worm's silhouette in brightfield. Subtracting a heavily blurred background
  makes the threshold a *contrast* threshold, which is robust to uneven illumination. The
  largest blob that is big enough to be a worm wins, and its centroid is the tracked point.
- BrightMarker: the brightest fluorescent object. The threshold is set from the frame's own
  noise level (median + k·MAD), so a noise-only frame yields nothing instead of a phantom
  target. The tracked point is the intensity-weighted centroid (sub-pixel).

Parameters are in pixels, and the defaults suit the rig's ~0.75 µm/px sampling. Estimators are
pure and stateless: temporal logic (plausibility, lost target) lives in the tracking controller.
"""

from dataclasses import dataclass

import cv2
import numpy as np

MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class Estimate:
    point_px: tuple[float, float] | None   # (x, y) in full-resolution pixels; None = no target
    area_px: float = 0.0
    mask: np.ndarray | None = None         # full-resolution bool mask, for the overlay


NOTHING = Estimate(point_px=None)


def _smooth_background(image: np.ndarray, sigma_px: float) -> np.ndarray:
    """A very wide Gaussian blur, computed at a coarse scale and upsampled.

    The result is smooth by construction, so computing it on an image shrunk by ~sigma/4 loses
    nothing and is far cheaper than blurring at full size.
    """
    factor = max(1, int(sigma_px / 4))
    height, width = image.shape
    coarse = cv2.resize(image, (max(1, width // factor), max(1, height // factor)),
                        interpolation=cv2.INTER_AREA)
    coarse = cv2.GaussianBlur(coarse, (0, 0), sigma_px / factor)
    return cv2.resize(coarse, (width, height), interpolation=cv2.INTER_LINEAR)


@dataclass(frozen=True)
class DarkObject:
    name = "dark_object"
    background_sigma_px: float = 150.0   # much larger than the worm's width
    min_contrast: float = 25.0           # grey levels darker than the local background
    min_area_px: float = 3000.0          # smaller blobs are debris, not a worm
    max_area_px: float | None = None
    downsample: int = 2

    def __call__(self, image: np.ndarray) -> Estimate:
        height, width = image.shape[:2]
        ds = self.downsample
        small = cv2.resize(image.astype(np.float32), (width // ds, height // ds),
                           interpolation=cv2.INTER_AREA)
        background = _smooth_background(small, self.background_sigma_px / ds)
        mask = ((background - small) > self.min_contrast).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
        best, best_area = None, 0.0
        for label in range(1, count):
            area = stats[label, cv2.CC_STAT_AREA] * ds * ds
            if area < self.min_area_px or (self.max_area_px and area > self.max_area_px):
                continue
            if area > best_area:
                best, best_area = label, area
        if best is None:
            return NOTHING
        cx, cy = centroids[best]
        full_mask = cv2.resize((labels == best).astype(np.uint8), (width, height),
                               interpolation=cv2.INTER_NEAREST).astype(bool)
        # a pixel's centre at small index i sits at full-resolution coordinate ds*i + (ds-1)/2
        point = (float(cx * ds + (ds - 1) / 2), float(cy * ds + (ds - 1) / 2))
        return Estimate(point_px=point, area_px=float(best_area), mask=full_mask)


@dataclass(frozen=True)
class BrightMarker:
    name = "bright_marker"
    spot_sigma_px: float = 2.0     # about the marker's size; smooths shot noise
    k_sigma: float = 8.0           # threshold = background + k · noise
    min_area_px: float = 3.0

    def __call__(self, image: np.ndarray) -> Estimate:
        raw = image.astype(np.float32)
        smooth = cv2.GaussianBlur(raw, (0, 0), self.spot_sigma_px)
        sample = smooth[::4, ::4]
        background = float(np.median(sample))
        noise = float(np.median(np.abs(sample - background))) * MAD_TO_SIGMA
        threshold = background + self.k_sigma * max(noise, 1e-3)
        mask = (smooth > threshold).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        best, best_peak = None, -np.inf
        for label in range(1, count):
            if stats[label, cv2.CC_STAT_AREA] < self.min_area_px:
                continue
            peak = float(smooth[labels == label].max())
            if peak > best_peak:
                best, best_peak = label, peak
        if best is None:
            return NOTHING
        region = labels == best
        weights = np.where(region, raw - background, 0.0).clip(min=0.0)
        total = float(weights.sum())
        rows, cols = np.indices(raw.shape)
        point = (float((cols * weights).sum() / total), float((rows * weights).sum() / total))
        return Estimate(point_px=point, area_px=float(region.sum()), mask=region)
