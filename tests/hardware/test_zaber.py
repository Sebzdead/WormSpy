"""ZaberStage against fake zaber-motion axes (the real SDK's units, no hardware; ADR 0005)."""

from zaber_motion import Units

from wormspy.core.stage import Stage
from wormspy.hardware.zaber import ZaberStage, connect_stage

SAFE = {"x": (1_000.0, 49_000.0), "y": (1_000.0, 49_000.0), "z": (500.0, 9_000.0)}


class FakeAxis:
    def __init__(self, position_um=25_000.0):
        self.position_um = position_um
        self.settings = self
        self.calls = []
        self.limits = {}

    # AxisSettings
    def set(self, setting, value, unit=Units.NATIVE):
        self.limits[setting] = (value, unit)

    # Axis
    def get_position(self, unit=Units.NATIVE):
        assert unit is Units.LENGTH_MICROMETRES
        return self.position_um

    def move_absolute(self, position, unit=Units.NATIVE, wait_until_idle=True):
        self.calls.append(("move_absolute", position, unit, wait_until_idle))

    def move_velocity(self, velocity, unit=Units.NATIVE):
        self.calls.append(("move_velocity", velocity, unit))

    def stop(self, wait_until_idle=True):
        self.calls.append(("stop", wait_until_idle))


def _stage(max_speed=2000.0):
    axes = {"x": FakeAxis(), "y": FakeAxis(), "z": FakeAxis(5_000.0)}
    return ZaberStage(axes, safe_ranges_um=SAFE, max_speed_um_s=max_speed), axes


def test_connecting_writes_the_safe_range_into_the_controller_firmware():
    stage, axes = _stage()
    assert isinstance(stage, Stage) and stage.is_connected()
    assert axes["z"].limits == {"limit.min": (500.0, Units.LENGTH_MICROMETRES),
                                "limit.max": (9_000.0, Units.LENGTH_MICROMETRES)}
    assert stage.limits_um("x") == (1_000.0, 49_000.0)


def test_positions_are_read_in_micrometres():
    stage, _ = _stage()
    assert stage.position_um("z") == 5_000.0


def test_absolute_moves_are_clamped_and_do_not_block():
    stage, axes = _stage()
    assert stage.move_absolute_um("x", 60_000.0) is True
    assert axes["x"].calls[-1] == ("move_absolute", 49_000.0, Units.LENGTH_MICROMETRES, False)


def test_velocity_moves_are_capped_at_max_speed():
    stage, axes = _stage(max_speed=2000.0)
    stage.move_velocity_um_s("y", -3500.0)
    assert axes["y"].calls[-1] == ("move_velocity", -2000.0,
                                   Units.VELOCITY_MICROMETRES_PER_SECOND)


def test_zero_velocity_and_stop_both_stop_the_axis_without_blocking():
    stage, axes = _stage()
    stage.move_velocity_um_s("x", 0.0)
    stage.stop("y")
    assert axes["x"].calls[-1] == ("stop", False)
    assert axes["y"].calls[-1] == ("stop", False)


def test_connect_stage_falls_back_to_a_null_stage_when_hardware_is_missing():
    def boom(**_):
        raise RuntimeError("no serial port")
    assert connect_stage(connector=boom).is_connected() is False


def test_connect_stage_returns_the_connected_stage():
    stage, _ = _stage()
    assert connect_stage(connector=lambda: stage) is stage
