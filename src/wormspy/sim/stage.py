"""A µm stage that moves at finite speed in simulated time, for closed-loop tests (ADR 0011).

`advance(dt)` moves each axis. An axis either travels toward an absolute target at up to
`max_speed_um_s`, or moves at a commanded velocity until it is stopped or reaches the edge of its
safe range. That mirrors the firmware-enforced limits on the Zaber controllers.
"""

from wormspy.core.stage import Stage, clamp


class SimStage(Stage):
    def __init__(self, limits_um: dict[str, tuple[float, float]], max_speed_um_s: float,
                 positions_um: dict[str, float]) -> None:
        self._limits = dict(limits_um)
        self._max_speed = max_speed_um_s
        self._pos = dict(positions_um)
        self._target: dict[str, float | None] = {axis: None for axis in self._pos}
        self._velocity = {axis: 0.0 for axis in self._pos}

    def is_connected(self) -> bool:
        return True

    def position_um(self, axis: str) -> float:
        return self._pos[axis]

    def limits_um(self, axis: str) -> tuple[float, float]:
        return self._limits[axis]

    def move_absolute_um(self, axis: str, target_um: float) -> bool:
        self._velocity[axis] = 0.0
        self._target[axis], hit = clamp(target_um, *self._limits[axis])
        return hit

    def move_velocity_um_s(self, axis: str, velocity_um_s: float) -> None:
        self._target[axis] = None
        self._velocity[axis] = max(-self._max_speed, min(self._max_speed, velocity_um_s))

    def stop(self, axis: str) -> None:
        self._target[axis] = None
        self._velocity[axis] = 0.0

    def advance(self, dt: float) -> None:
        max_step = self._max_speed * dt
        for axis in self._pos:
            target = self._target[axis]
            if target is not None:
                delta = target - self._pos[axis]
                self._pos[axis] += max(-max_step, min(max_step, delta))
            elif self._velocity[axis]:
                moved, hit = clamp(self._pos[axis] + self._velocity[axis] * dt,
                                   *self._limits[axis])
                self._pos[axis] = moved
                if hit:
                    self._velocity[axis] = 0.0
