import pytest

from wormspy.core.stage import Stage
from wormspy.sim.stage import SimStage

LIMITS = {"x": (0.0, 1_000_000.0), "y": (0.0, 1_000_000.0), "z": (0.0, 200_000.0)}


def _stage(**kw):
    return SimStage(limits=LIMITS, native_per_um=20.0, max_speed_um_s=1000.0,
                    positions={"x": 500_000.0, "y": 500_000.0, "z": 100_000.0}, **kw)


def test_sim_stage_satisfies_the_stage_interface():
    assert isinstance(_stage(), Stage)
    assert _stage().is_connected()


def test_absolute_move_takes_time_and_respects_max_speed():
    stage = _stage()
    stage.move_absolute("x", 500_000.0 + 20.0 * 300)   # +300 µm
    assert stage.get_position("x") == 500_000.0         # nothing happens until time passes
    stage.advance(0.1)                                  # 1000 µm/s * 0.1 s = 100 µm
    assert stage.position_um("x") == pytest.approx(500_000.0 / 20.0 + 100.0)
    stage.advance(1.0)
    assert stage.position_um("x") == pytest.approx(500_000.0 / 20.0 + 300.0)  # stops on target


def test_relative_move_is_from_the_current_position():
    stage = _stage()
    stage.move_relative("y", -20.0 * 50)
    stage.advance(1.0)
    assert stage.position_um("y") == pytest.approx(500_000.0 / 20.0 - 50.0)


def test_moves_are_clamped_to_limits_and_report_the_hit():
    stage = _stage()
    assert stage.move_absolute("z", 999_999.0) is True
    stage.advance(1000.0)
    assert stage.get_position("z") == 200_000.0
    assert stage.move_relative("x", 10.0) is False


def test_all_axes_move_simultaneously():
    stage = _stage()
    stage.move_relative("x", 20.0 * 100)
    stage.move_relative("y", 20.0 * 100)
    stage.advance(0.05)
    assert stage.position_um("x") == pytest.approx(25_000.0 + 50.0)
    assert stage.position_um("y") == pytest.approx(25_000.0 + 50.0)
