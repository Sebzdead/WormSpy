"""Tracking controller: target filter + velocity servo, as pure logic (ADR 0008)."""

import numpy as np
import pytest

from wormspy.control import TrackingStatus as S
from wormspy.tracking.controller import ServoConfig, TargetFilter, VelocityServo

CFG = ServoConfig(gain_per_s=2.0, deadband_um=10.0, max_speed_um_s=1000.0,
                  max_accel_um_s2=1e9, max_worm_speed_um_s=500.0, jump_margin_um=100.0,
                  lost_after_misses=3)


# --- target filter --------------------------------------------------------------------------

def test_the_first_estimate_is_accepted():
    f = TargetFilter(CFG)
    assert f.update(0.0, (100.0, 200.0)) is S.TRACKING
    assert f.target == (100.0, 200.0)


def test_a_few_missed_frames_keep_the_last_target_then_the_target_is_lost():
    f = TargetFilter(CFG)
    f.update(0.0, (0.0, 0.0))
    assert f.update(0.1, None) is S.TRACKING and f.target == (0.0, 0.0)
    assert f.update(0.2, None) is S.TRACKING
    assert f.update(0.3, None) is S.LOST and f.target is None


def test_a_physically_impossible_jump_is_rejected():
    f = TargetFilter(CFG)
    f.update(0.0, (0.0, 0.0))
    # 0.1 s later: at most 500 µm/s * 0.1 s + 100 µm margin = 150 µm is plausible
    assert f.update(0.1, (2000.0, 0.0)) is S.TRACKING
    assert f.target == (0.0, 0.0)                          # kept the plausible one
    f.update(0.2, (60.0, 0.0))
    assert f.target == (60.0, 0.0)


def test_tracking_resumes_when_the_worm_reappears_near_where_it_was_lost():
    f = TargetFilter(CFG)
    f.update(0.0, (0.0, 0.0))
    for t in (0.1, 0.2, 0.3):
        f.update(t, None)
    assert f.status is S.LOST
    assert f.update(0.4, (5000.0, 0.0)) is S.LOST          # far away, too soon
    assert f.update(0.5, (120.0, 30.0)) is S.TRACKING      # near the last position
    assert f.target == (120.0, 30.0)


def test_the_search_radius_grows_while_lost():
    f = TargetFilter(CFG)
    f.update(0.0, (0.0, 0.0))
    for t in (0.1, 0.2, 0.3):
        f.update(t, None)
    assert f.update(10.0, (4000.0, 0.0)) is S.TRACKING     # 10 s: the worm could be there


def test_reset_forgets_everything():
    f = TargetFilter(CFG)
    f.update(0.0, (0.0, 0.0))
    f.reset()
    assert f.target is None and f.update(1.0, (9000.0, 9000.0)) is S.TRACKING


# --- velocity servo -----------------------------------------------------------------------------

def _servo(**kw):
    return VelocityServo(ServoConfig(**{**CFG.__dict__, **kw}))


def test_inside_the_deadband_the_stage_holds_still():
    v, status = _servo().step(0.02, target=(5.0, 5.0), position=(0.0, 0.0))
    assert v == (0.0, 0.0) and status is S.TRACKING


def test_velocity_is_proportional_to_the_distance_to_the_target():
    v, _ = _servo().step(0.02, target=(100.0, -50.0), position=(0.0, 0.0))
    assert v == pytest.approx((200.0, -100.0))


def test_speed_is_capped_without_changing_direction():
    v, _ = _servo().step(0.02, target=(3000.0, 4000.0), position=(0.0, 0.0))
    assert np.hypot(*v) == pytest.approx(1000.0)
    assert v[1] / v[0] == pytest.approx(4 / 3)


def test_acceleration_is_limited():
    servo = _servo(max_accel_um_s2=500.0)
    v, _ = servo.step(0.02, target=(1000.0, 0.0), position=(0.0, 0.0))
    assert v == pytest.approx((10.0, 0.0))                  # 500 µm/s² × 20 ms
    v, _ = servo.step(0.02, target=(1000.0, 0.0), position=(0.0, 0.0))
    assert v == pytest.approx((20.0, 0.0))


def test_a_lost_target_ramps_the_stage_to_a_stop_instead_of_halting_instantly():
    servo = _servo(max_accel_um_s2=5000.0)
    for _ in range(50):
        servo.step(0.02, target=(1e5, 0.0), position=(0.0, 0.0))    # cruising at max speed
    v, status = servo.step(0.02, target=None, position=(0.0, 0.0))
    assert v == pytest.approx((900.0, 0.0)) and status is S.LOST
    for _ in range(20):
        v, _ = servo.step(0.02, target=None, position=(0.0, 0.0))
    assert v == (0.0, 0.0)


def test_joystick_override_yields_completely_and_restarts_from_rest():
    servo = _servo(max_accel_um_s2=5000.0)
    for _ in range(50):
        servo.step(0.02, target=(1e5, 0.0), position=(0.0, 0.0))
    v, status = servo.step(0.02, target=(1e5, 0.0), position=(0.0, 0.0), override=True)
    assert v is None and status is S.OVERRIDE                # send nothing; the stick drives
    v, _ = servo.step(0.02, target=(1e5, 0.0), position=(0.0, 0.0))
    assert v == pytest.approx((100.0, 0.0))                  # ramps up again from rest


def test_an_axis_at_its_limit_is_not_pushed_further():
    v, status = _servo().step(0.02, target=(500.0, 100.0), position=(0.0, 0.0),
                              at_limit={"x": "max"})
    assert v == pytest.approx((0.0, 200.0)) and status is S.LIMIT


def test_moving_away_from_a_limit_is_allowed():
    v, status = _servo().step(0.02, target=(-500.0, 0.0), position=(0.0, 0.0),
                              at_limit={"x": "max"})
    assert v[0] < 0 and status is S.TRACKING
