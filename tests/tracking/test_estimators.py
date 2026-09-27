"""Target estimators on simulated images (ADR 0008)."""

import numpy as np
import pytest

from wormspy.sim.render import ImageGeometry, render_behaviour, render_fluorescence
from wormspy.sim.worm import SyntheticWorm
from wormspy.tracking.estimators import BrightMarker, DarkObject

GEOM = ImageGeometry(width_px=480, height_px=300, um_per_px=3.0)
DARK = DarkObject(background_sigma_px=40.0, min_contrast=25, min_area_px=200)
MARKER = BrightMarker(spot_sigma_px=1.5, k_sigma=8.0, min_area_px=2)


def _worm(seed=0):
    worm = SyntheticWorm(seed=seed, start_um=(0.0, 0.0), length_um=900.0)
    for _ in range(30):
        worm.step(0.1)
    return worm


def _mask_centroid(img):
    rows, cols = np.nonzero(img < 128)
    return cols.mean(), rows.mean()


# --- dark object (brightfield silhouette) ---------------------------------------------------

def test_dark_object_finds_the_worm_centroid():
    worm = _worm()
    img = render_behaviour(worm, worm.centroid, GEOM)
    estimate = DARK(img)
    assert estimate.point_px == pytest.approx(_mask_centroid(img), abs=2.0)
    assert estimate.area_px > 1000


def test_dark_object_copes_with_noise_and_uneven_illumination():
    worm = _worm(seed=3)
    img = render_behaviour(worm, worm.centroid, GEOM, noise_sigma=6.0,
                           rng=np.random.default_rng(0)).astype(float)
    truth = _mask_centroid(img)
    gradient = np.linspace(-45, 25, GEOM.width_px)[None, :]          # vignetting / tilt
    img = np.clip(img + gradient, 0, 255).astype(np.uint8)
    assert DARK(img).point_px == pytest.approx(truth, abs=4.0)


def test_dark_object_reports_nothing_on_an_empty_plate():
    far = SyntheticWorm(seed=0, start_um=(1e6, 1e6))
    img = render_behaviour(far, (0.0, 0.0), GEOM, noise_sigma=6.0, rng=np.random.default_rng(1))
    assert DARK(img).point_px is None


def test_dark_object_ignores_debris_smaller_than_a_worm():
    far = SyntheticWorm(seed=0, start_um=(1e6, 1e6))
    img = render_behaviour(far, (0.0, 0.0), GEOM, target=True)   # beads: small dark specks
    assert DARK(img).point_px is None


def test_dark_object_returns_a_mask_for_the_overlay():
    worm = _worm()
    estimate = DARK(render_behaviour(worm, worm.centroid, GEOM))
    assert estimate.mask is not None and estimate.mask.shape == (GEOM.height_px, GEOM.width_px)
    assert estimate.mask.dtype == bool and estimate.mask.sum() > 1000


# --- bright marker (fluorescence) ---------------------------------------------------------------

def test_bright_marker_finds_the_neuron_with_subpixel_accuracy():
    worm = _worm()
    img = render_fluorescence(worm, worm.points[5], GEOM, neuron_index=2, noise_sigma=200.0,
                              rng=np.random.default_rng(2))
    truth = GEOM.world_to_px(worm.points[[2]], worm.points[5])[0]
    assert MARKER(img).point_px == pytest.approx(tuple(truth), abs=0.5)


def test_bright_marker_reports_nothing_on_a_noise_only_frame():
    far = SyntheticWorm(seed=0, start_um=(1e6, 1e6))
    img = render_fluorescence(far, (0.0, 0.0), GEOM, noise_sigma=200.0,
                              rng=np.random.default_rng(3))
    assert MARKER(img).point_px is None


def test_bright_marker_picks_the_brightest_of_several_spots():
    img = np.full((100, 100), 1000.0)
    yy, xx = np.mgrid[:100, :100]
    img += 4000 * np.exp(-((xx - 20) ** 2 + (yy - 30) ** 2) / 8)     # dim spot
    img += 20000 * np.exp(-((xx - 70) ** 2 + (yy - 60) ** 2) / 8)    # bright spot
    img += np.random.default_rng(4).normal(0, 50, img.shape)
    assert MARKER(img.astype(np.uint16)).point_px == pytest.approx((70, 60), abs=0.5)


def test_bright_marker_works_on_8bit_images_too():
    img = np.full((60, 60), 10, np.uint8)
    img[29:32, 39:42] = 200
    assert MARKER(img).point_px == pytest.approx((40, 30), abs=0.5)
