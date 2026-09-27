"""SimGamepad: a scriptable gamepad for tests and the simulated rig."""

from wormspy.control.gamepad import Gamepad, GamepadState


class SimGamepad(Gamepad):
    def __init__(self) -> None:
        self._state: GamepadState | None = GamepadState()

    def set(self, axes: dict[str, float] | None = None, buttons: set[str] | None = None) -> None:
        current = self._state or GamepadState()
        self._state = GamepadState(
            axes=dict(current.axes if axes is None else axes),
            buttons=current.buttons if buttons is None else frozenset(buttons))

    def disconnect(self) -> None:
        self._state = None

    def poll(self) -> GamepadState | None:
        return self._state
