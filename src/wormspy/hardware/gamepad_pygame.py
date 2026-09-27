"""pygame gamepad adapter. pygame is imported lazily (optional `gamepad` extra).

Index maps default to the v1 layout on the rig's controller: axes 0/1 = left stick, axis 3 =
right stick vertical (Z); buttons A=0, B=1, X=2, Y=3. Override them in rig.toml if a different
controller reports other indices. Verified manually on the rig, not in CI.
"""

from wormspy.control.gamepad import Gamepad, GamepadState

DEFAULT_AXES = {"left_x": 0, "left_y": 1, "right_y": 3}
DEFAULT_BUTTONS = {"A": 0, "B": 1, "X": 2, "Y": 3}


class PygameGamepad(Gamepad):
    def __init__(self, index: int = 0, axes: dict[str, int] | None = None,
                 buttons: dict[str, int] | None = None) -> None:
        import pygame
        self._pygame = pygame
        pygame.init()
        pygame.joystick.init()
        self._index = index
        self._axes = DEFAULT_AXES if axes is None else axes
        self._buttons = DEFAULT_BUTTONS if buttons is None else buttons
        self._joystick = None

    def poll(self) -> GamepadState | None:
        pg = self._pygame
        pg.event.pump()
        if pg.joystick.get_count() <= self._index:
            self._joystick = None
            return None
        if self._joystick is None:
            self._joystick = pg.joystick.Joystick(self._index)
            self._joystick.init()
        js = self._joystick
        axes = {name: js.get_axis(i) for name, i in self._axes.items() if i < js.get_numaxes()}
        pressed = frozenset(name for name, i in self._buttons.items()
                            if i < js.get_numbuttons() and js.get_button(i))
        return GamepadState(axes=axes, buttons=pressed)
