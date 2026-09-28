"""Calibration maths against the simulator's ground-truth geometry (ADR 0009)."""

from dataclasses import replace

import numpy as np
import pytest

from wormspy.calibration.math import (
    CameraCalibration,
    fit_matrix,
    measure_shift,
    register_origin,
)
from wormspy.sim.render import ImageGeometry, render_behaviour, render_fluorescence
from wormspy.sim.worm import SyntheticWorm

FAR = SyntheticWorm(seed=0, start_um=(1e6, 1e6))       # keep the worm out of view


def _shot(geom, stage, channel="behaviour"):
    render = render_behaviour if channel == "behaviour" else render_fluorescence
    return render(FAR, np.asarray(stage, float), geom, target=True)


def test_measure_shift_recovers_a_whole_pixel_translation():
    geom = ImageGeometry(width_px=320, height_px=240, um_per_px=4.0)
    dx, dy = measure_shift(_shot(geom, (0, 0)), _shot(geom, (40.0, -20.0)))
    assert (dx, dy) == pytest.approx((-10.0, 5.0), abs=0.05)    # image moves opposite to stage


def test_measure_shift_is_subpixel_accurate():
    # Sub-pixel accuracy needs features sampled over several pixels, as on the rig
    # (~0.75 µm/px). At coarse sampling the beads alias and no estimator is unbiased.
    # OpenCV's centroid peak refinement has a ~0.1 px bias on small shifts; calibration moves
    # are ~100 px, so that is ~0.1 % scale error (the end-to-end test below demands < 1 %).
    geom = ImageGeometry(width_px=320, height_px=240, um_per_px=1.0)
    dx, dy = measure_shift(_shot(geom, (0, 0)), _shot(geom, (1.5, 0.5)))
    assert (dx, dy) == pytest.approx((-1.5, -0.5), abs=0.15)


def test_fit_matrix_recovers_scale_rotation_and_flip_from_clean_data():
    truth = ImageGeometry(width_px=10, height_px=10, um_per_px=2.5, rotation_deg=17.0,
                          flip_x=True).matrix
    deltas = np.array([[100, 0], [0, 100], [-100, 0], [0, -100], [70, 70]], float)
    shifts = -(deltas @ truth.T)
    matrix, residual = fit_matrix(deltas, shifts)
    np.testing.assert_allclose(matrix, truth, atol=1e-9)
    assert residual < 1e-9


def test_fit_matrix_rejects_moves_that_do_not_span_both_axes():
    deltas = np.array([[100, 0], [200, 0], [-50, 0]], float)
    with pytest.raises(ValueError):
        fit_matrix(deltas, deltas)


@pytest.mark.parametrize("channel", ["behaviour", "fluorescence"])
def test_calibrating_from_rendered_images_recovers_the_true_camera_matrix(channel):
    geom = ImageGeometry(width_px=400, height_px=300, um_per_px=3.0, rotation_deg=-8.0,
                         flip_x=True)
    moves = np.array([[90, 0], [0, 90], [-90, 0], [0, -90], [60, 60], [-60, 60]], float)
    reference = _shot(geom, (0, 0), channel)
    shifts = np.array([measure_shift(reference, _shot(geom, d, channel)) for d in moves])
    matrix, residual = fit_matrix(moves, shifts)
    np.testing.assert_allclose(matrix, geom.matrix, rtol=0.01, atol=1e-3)
    assert residual < 0.2


def _calibration(geom):
    return CameraCalibration(matrix=geom.matrix, residual_px=0.05,
                             image_shape=(geom.height_px, geom.width_px))


def test_pixel_to_stage_offset_matches_the_ground_truth_geometry():
    geom = ImageGeometry(width_px=400, height_px=300, um_per_px=3.0, rotation_deg=12.0)
    cal = _calibration(geom)
    assert cal.stage_offset_um((200.0, 150.0)) == pytest.approx((0.0, 0.0))
    px = (310.0, 40.0)
    expected = geom.px_to_world(np.array([px]), stage_um=(0.0, 0.0))[0]
    assert cal.stage_offset_um(px) == pytest.approx(tuple(expected))


def test_calibration_round_trips_through_json():
    geom = ImageGeometry(width_px=400, height_px=300, um_per_px=3.0, rotation_deg=12.0)
    cal = _calibration(geom)
    again = CameraCalibration.from_json(cal.to_json())
    np.testing.assert_allclose(again.matrix, cal.matrix)
    assert again.image_shape == (300, 400) and again.residual_px == 0.05


FLUO_CAM = ImageGeometry(width_px=320, height_px=200, um_per_px=2.0, rotation_deg=1.5)
BF_CAM = ImageGeometry(width_px=300, height_px=190, um_per_px=2.4, rotation_deg=-2.0,
                       flip_x=True, offset_px=(13.5, -8.25))


def test_registration_finds_where_the_second_camera_sees_the_first_cameras_centre():
    stage = (500.0, -300.0)
    origin, match = register_origin(_shot(FLUO_CAM, stage, "fluorescence"), FLUO_CAM.matrix,
                                    _shot(BF_CAM, stage, "behaviour"), BF_CAM.matrix)
    assert origin == pytest.approx(BF_CAM.origin_px, abs=0.3)     # despite inverted contrast
    assert match > 0.1


def test_cameras_with_no_common_features_match_poorly():
    rng = np.random.default_rng(1)
    noise = rng.normal(100, 10, (190, 300)).astype(np.float32)
    _, match = register_origin(_shot(FLUO_CAM, (0, 0), "fluorescence"), FLUO_CAM.matrix, noise,
                               BF_CAM.matrix)
    assert match < 0.05


def test_registered_cameras_map_a_pixel_to_the_same_plate_point():
    from wormspy.calibration.routine import RigCalibration
    cal = RigCalibration(cameras={
        "gcamp": _calibration(FLUO_CAM),
        "bf": replace(_calibration(BF_CAM), origin_px=tuple(BF_CAM.origin_px))},
        reference="gcamp")
    stage = np.array([1000.0, 2000.0])
    world = stage + (40.0, -25.0)
    in_fluo = FLUO_CAM.world_to_px(world[None, :], stage)[0]
    in_bf = BF_CAM.world_to_px(world[None, :], stage)[0]
    assert cal.map_px("gcamp", "bf", in_fluo) == pytest.approx(tuple(in_bf), abs=1e-6)
    assert cal.map_px("bf", "gcamp", in_bf) == pytest.approx(tuple(in_fluo), abs=1e-6)


def test_unregistered_cameras_do_not_map():
    from wormspy.calibration.routine import RigCalibration
    cal = RigCalibration(cameras={"gcamp": _calibration(FLUO_CAM), "bf": _calibration(BF_CAM)},
                         reference="gcamp", registration="no common features")
    assert cal.map_px("gcamp", "bf", (10, 10)) is None
    assert cal.map_px("bf", "bf", (10, 10)) == (10.0, 10.0)


def test_origin_round_trips_and_old_files_load_unregistered():
    cal = replace(_calibration(BF_CAM), origin_px=(163.5, 86.75))
    assert CameraCalibration.from_json(cal.to_json()).origin_px == (163.5, 86.75)
    old = cal.to_json()
    del old["origin_px"]
    assert CameraCalibration.from_json(old).origin_px is None
