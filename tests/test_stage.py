"""Stage interface (µm) and NullStage (ADR 0005)."""

from wormspy.core.stage import NullStage, Stage, clamp


def test_clamp_within_bounds_is_unchanged():
    assert clamp(50.0, 0.0, 100.0) == (50.0, False)


def test_clamp_above_max_pins_to_max_and_flags():
    assert clamp(150.0, 0.0, 100.0) == (100.0, True)


def test_clamp_below_min_pins_to_min_and_flags():
    assert clamp(-5.0, 0.0, 100.0) == (0.0, True)


def test_null_stage_is_a_disconnected_stage_whose_moves_do_nothing():
    stage = NullStage()
    assert isinstance(stage, Stage)
    assert stage.is_connected() is False
    stage.move_absolute_um("x", 100.0)
    stage.move_velocity_um_s("y", 50.0)
    stage.stop("z")
    assert stage.position_um("x") == 0.0
