"""SimStage: µm stage with finite speed, velocity moves and safe-range limits (ADR 0005/0011)."""

import pytest

from wormspy.core.stage import Stage
from wormspy.sim.stage import SimStage

LIMITS = {"x": (0.0, 50_000.0), "y": (0.0, 50_000.0), "z": (0.0, 10_000.0)}


def _stage():
    return SimStage(limits_um=LIMITS, max_speed_um_s=1000.0,
                    positions_um={"x": 25_000.0, "y": 25_000.0, "z": 5_000.0})


def test_sim_stage_satisfies_the_stage_interface():
    stage = _stage()
    assert isinstance(stage, Stage) and stage.is_connected()
    assert stage.limits_um("z") == (0.0, 10_000.0)


def test_absolute_move_takes_time_and_respects_max_speed():
    stage = _stage()
    stage.move_absolute_um("x", 25_300.0)
    assert stage.position_um("x") == 25_000.0          # nothing happens until time passes
    stage.advance(0.1)
    assert stage.position_um("x") == pytest.approx(25_100.0)
    stage.advance(1.0)
    assert stage.position_um("x") == pytest.approx(25_300.0)   # stops on target


def test_relative_move_is_from_the_current_position():
    stage = _stage()
    stage.move_relative_um("y", -50.0)
    stage.advance(1.0)
    assert stage.position_um("y") == pytest.approx(24_950.0)


def test_absolute_moves_are_clamped_to_the_safe_range():
    stage = _stage()
    assert stage.move_absolute_um("z", 99_999.0) is True
    stage.advance(100.0)
    assert stage.position_um("z") == 10_000.0
    assert stage.at_limit("z")
    assert stage.move_relative_um("x", 10.0) is False


def test_velocity_move_runs_until_stopped():
    stage = _stage()
    stage.move_velocity_um_s("x", 200.0)
    stage.advance(0.5)
    assert stage.position_um("x") == pytest.approx(25_100.0)
    stage.stop("x")
    stage.advance(1.0)
    assert stage.position_um("x") == pytest.approx(25_100.0)


def test_velocity_is_capped_at_max_speed():
    stage = _stage()
    stage.move_velocity_um_s("y", -5000.0)
    stage.advance(0.1)
    assert stage.position_um("y") == pytest.approx(24_900.0)


def test_a_velocity_move_stops_at_the_safe_range_limit():
    stage = _stage()
    stage.move_velocity_um_s("z", 1000.0)
    stage.advance(60.0)
    assert stage.position_um("z") == 10_000.0
    assert stage.at_limit("z")


def test_an_absolute_move_cancels_a_velocity_move():
    stage = _stage()
    stage.move_velocity_um_s("x", 500.0)
    stage.move_absolute_um("x", 24_900.0)
    stage.advance(1.0)
    assert stage.position_um("x") == pytest.approx(24_900.0)


def test_all_axes_move_simultaneously():
    stage = _stage()
    stage.move_relative_um("x", 100.0)
    stage.move_velocity_um_s("y", 100.0)
    stage.advance(0.05)
    assert stage.position_um("x") == pytest.approx(25_050.0)
    assert stage.position_um("y") == pytest.approx(25_005.0)
