"""GamepadController: sticks -> stage velocity, buttons -> Commands (ADR 0010)."""

import pytest

from wormspy.control import commands as C
from wormspy.control import events as E
from wormspy.control.gamepad import DEFAULT_BUTTONS, GamepadController, GamepadState, StickConfig
from wormspy.core.stage import Stage
from wormspy.sim.gamepad import SimGamepad


class SpyStage(Stage):
    def __init__(self):
        self.velocity_calls = []
        self.stops = []

    def is_connected(self):
        return True

    def position_um(self, axis):
        return 0.0

    def limits_um(self, axis):
        return (0.0, 1.0)

    def move_absolute_um(self, axis, target_um):
        return False

    def move_velocity_um_s(self, axis, velocity_um_s):
        self.velocity_calls.append((axis, velocity_um_s))

    def stop(self, axis):
        self.stops.append(axis)


STICKS = StickConfig(xy_max_um_s=1000.0, z_max_um_s=100.0, deadzone=0.1, expo=1.0,
                     invert_y=True)


def _controller():
    pad, stage, dispatched = SimGamepad(), SpyStage(), []
    return GamepadController(pad, stage, dispatched.append, STICKS), pad, stage, dispatched


def test_default_mapping_matches_the_agreed_buttons():
    assert DEFAULT_BUTTONS == {"X": C.ToggleTracking, "B": C.ToggleRecording,
                               "A": C.ReturnToFocus, "Y": C.SetFocus}


def test_full_stick_deflection_moves_at_max_speed():
    ctl, pad, stage, _ = _controller()
    pad.set(axes={"left_x": 1.0, "left_y": -1.0, "right_y": 0.5})
    ctl.step()
    velocities = dict(stage.velocity_calls)
    assert velocities["x"] == pytest.approx(1000.0)
    assert velocities["y"] == pytest.approx(1000.0)      # stick up = +y with invert_y
    assert velocities["z"] == pytest.approx(100.0 * (0.5 - 0.1) / 0.9)


def test_small_deflections_inside_the_deadzone_do_nothing():
    ctl, pad, stage, _ = _controller()
    pad.set(axes={"left_x": 0.05})
    ctl.step()
    assert stage.velocity_calls == [] and not ctl.xy_active


def test_velocity_is_only_resent_when_it_changes():
    ctl, pad, stage, _ = _controller()
    pad.set(axes={"left_x": 0.8})
    for _ in range(5):
        ctl.step()
    assert len([c for c in stage.velocity_calls if c[0] == "x"]) == 1


def test_releasing_a_stick_stops_that_axis_once():
    ctl, pad, stage, _ = _controller()
    pad.set(axes={"left_x": 0.8})
    ctl.step()
    assert ctl.xy_active
    pad.set(axes={"left_x": 0.0})
    ctl.step()
    ctl.step()
    assert stage.stops == ["x"] and not ctl.xy_active


def test_expo_gives_finer_control_near_centre():
    pad, stage = SimGamepad(), SpyStage()
    ctl = GamepadController(pad, stage, lambda _: None,
                            StickConfig(xy_max_um_s=1000.0, z_max_um_s=100.0, deadzone=0.0,
                                        expo=2.0, invert_y=False))
    pad.set(axes={"left_x": 0.5})
    ctl.step()
    assert dict(stage.velocity_calls)["x"] == pytest.approx(250.0)


def test_buttons_dispatch_their_command_once_per_press():
    ctl, pad, _, dispatched = _controller()
    pad.set(buttons={"B"})
    ctl.step()
    ctl.step()                      # held: no repeat
    pad.set(buttons=set())
    ctl.step()
    pad.set(buttons={"B", "Y"})
    ctl.step()
    assert dispatched == [C.ToggleRecording(), C.ToggleRecording(), C.SetFocus()]


def test_unmapped_buttons_are_ignored():
    ctl, pad, _, dispatched = _controller()
    pad.set(buttons={"Start"})
    ctl.step()
    assert dispatched == []


def test_a_disconnected_gamepad_stops_moving_axes_and_raises_an_alarm_once():
    ctl, pad, stage, dispatched = _controller()
    pad.set(axes={"left_x": 1.0, "right_y": 1.0})
    ctl.step()
    pad.disconnect()
    ctl.step()
    ctl.step()
    assert sorted(stage.stops) == ["x", "z"]
    assert [d for d in dispatched if isinstance(d, E.AlarmRaised)] == [
        E.AlarmRaised(kind="gamepad", message="Gamepad disconnected.")]


def test_reconnecting_clears_the_alarm():
    ctl, pad, _, dispatched = _controller()
    pad.disconnect()
    ctl.step()
    pad.set(axes={})
    ctl.step()
    assert dispatched[-1] == E.AlarmCleared(kind="gamepad")


def test_state_defaults_are_neutral():
    assert GamepadState().axes == {} and GamepadState().buttons == frozenset()
