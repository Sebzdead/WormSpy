"""A stage that moves at finite speed in simulated time, for closed-loop tests (ADR 0011).

Positions are in native units (microsteps), as on the Zaber hardware. `native_per_um` sets the
conversion. Unlike `FakeStage`, moves do not complete instantly: `advance(dt)` moves each axis
toward its target at no more than `max_speed_um_s`.
"""

from wormspy.core.stage import Stage, clamp_native


class SimStage(Stage):
    def __init__(self, limits: dict[str, tuple[float, float]], native_per_um: float,
                 max_speed_um_s: float, positions: dict[str, float]) -> None:
        self._limits = dict(limits)
        self._native_per_um = native_per_um
        self._max_speed_native = max_speed_um_s * native_per_um
        self._pos = dict(positions)
        self._target = dict(positions)

    def is_connected(self) -> bool:
        return True

    def get_position(self, axis: str) -> float:
        return self._pos[axis]

    def position_um(self, axis: str) -> float:
        return self._pos[axis] / self._native_per_um

    def limits(self, axis: str) -> tuple[float, float]:
        return self._limits[axis]

    def move_relative(self, axis: str, native: float) -> bool:
        return self.move_absolute(axis, self._pos[axis] + native)

    def move_absolute(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        self._target[axis], hit = clamp_native(native, lo, hi)
        return hit

    def advance(self, dt: float) -> None:
        max_step = self._max_speed_native * dt
        for axis, target in self._target.items():
            delta = target - self._pos[axis]
            self._pos[axis] += max(-max_step, min(max_step, delta))
