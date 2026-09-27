"""GamepadController: the gamepad drives the stage and issues Commands (ADR 0010).

Sticks map to stage *velocity*: left stick = XY, right stick = Z. A deadzone and an expo curve
give fine control near centre, and a velocity is sent only when it changes meaningfully, so a
held stick doesn't flood the serial line. Buttons dispatch the same Commands the UI uses, on the
press edge (never repeated while held). A disconnected gamepad stops any axis it was moving and
raises an alarm once. `xy_active` tells the tracker when the operator has taken over XY
(joystick override, ADR 0008).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from wormspy.control import commands as C
from wormspy.control import events as E
from wormspy.core.stage import Stage

DEFAULT_BUTTONS: dict[str, type] = {
    "X": C.ToggleTracking,
    "B": C.ToggleRecording,
    "A": C.ReturnToFocus,
    "Y": C.SetFocus,
}

RESEND_FRACTION = 0.02   # resend a velocity only if it changed by > 2 % of that axis's max


@dataclass(frozen=True)
class GamepadState:
    axes: dict[str, float] = field(default_factory=dict)    # "left_x", "left_y", "right_y" ∈ [-1, 1]
    buttons: frozenset[str] = frozenset()                   # names of pressed buttons


class Gamepad:
    """A gamepad input source. poll() returns None while disconnected."""

    def poll(self) -> GamepadState | None:
        raise NotImplementedError


@dataclass(frozen=True)
class StickConfig:
    xy_max_um_s: float = 1000.0
    z_max_um_s: float = 100.0
    deadzone: float = 0.1
    expo: float = 2.0            # 1 = linear; >1 = finer control near centre
    invert_y: bool = True        # most gamepads report "stick up" as negative
    invert_z: bool = False


class GamepadController:
    def __init__(self, gamepad: Gamepad, stage: Stage, dispatch: Callable[[object], None],
                 sticks: StickConfig | None = None,
                 buttons: dict[str, type] | None = None, rate_hz: float = 50.0) -> None:
        self._gamepad = gamepad
        self._stage = stage
        self._dispatch = dispatch
        self._sticks = sticks if sticks is not None else StickConfig()
        self._buttons = DEFAULT_BUTTONS if buttons is None else buttons
        self._interval_s = 1.0 / rate_hz
        self._sent = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._pressed: frozenset[str] = frozenset()
        self._connected = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def xy_active(self) -> bool:
        return self._sent["x"] != 0.0 or self._sent["y"] != 0.0

    def step(self) -> None:
        state = self._gamepad.poll()
        if state is None:
            self._on_disconnected()
            return
        if not self._connected:
            self._connected = True
            self._dispatch(E.AlarmCleared(kind="gamepad"))
        s = self._sticks
        self._drive("x", self._shape(state.axes.get("left_x", 0.0)) * s.xy_max_um_s,
                    s.xy_max_um_s)
        y = self._shape(state.axes.get("left_y", 0.0)) * s.xy_max_um_s
        self._drive("y", -y if s.invert_y else y, s.xy_max_um_s)
        z = self._shape(state.axes.get("right_y", 0.0)) * s.z_max_um_s
        self._drive("z", -z if s.invert_z else z, s.z_max_um_s)
        for name in sorted(state.buttons - self._pressed):
            command = self._buttons.get(name)
            if command is not None:
                self._dispatch(command())
        self._pressed = state.buttons

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.step()
            self._stop.wait(self._interval_s)

    def _shape(self, value: float) -> float:
        magnitude = (abs(value) - self._sticks.deadzone) / (1.0 - self._sticks.deadzone)
        if magnitude <= 0:
            return 0.0
        return (1.0 if value > 0 else -1.0) * min(1.0, magnitude) ** self._sticks.expo

    def _drive(self, axis: str, velocity: float, max_speed: float) -> None:
        previous = self._sent[axis]
        if velocity == 0.0:
            if previous != 0.0:
                self._stage.stop(axis)
                self._sent[axis] = 0.0
            return
        if previous == 0.0 or abs(velocity - previous) > RESEND_FRACTION * max_speed:
            self._stage.move_velocity_um_s(axis, velocity)
            self._sent[axis] = velocity

    def _on_disconnected(self) -> None:
        for axis, velocity in self._sent.items():
            if velocity != 0.0:
                self._stage.stop(axis)
                self._sent[axis] = 0.0
        self._pressed = frozenset()
        if self._connected:
            self._connected = False
            self._dispatch(E.AlarmRaised(kind="gamepad", message="Gamepad disconnected."))
