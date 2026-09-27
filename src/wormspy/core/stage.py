"""The Stage interface, in µm (ADR 0005). Axes are "x", "y" and "z".

Every move is clamped to the axis's configured safe range (`limits_um`). Moves never block: an
absolute move sets a target, and a velocity move runs until `stop()` or until it reaches the edge
of the safe range. Adapters wrap real hardware (Zaber) or simulate it (SimStage).
"""

from abc import ABC, abstractmethod

LIMIT_TOLERANCE_UM = 1.0


def clamp(value: float, lo: float, hi: float) -> tuple[float, bool]:
    """Clamp value into [lo, hi]. Returns (clamped_value, limit_was_hit)."""
    if value > hi:
        return hi, True
    if value < lo:
        return lo, True
    return value, False


class Stage(ABC):
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def position_um(self, axis: str) -> float: ...

    @abstractmethod
    def limits_um(self, axis: str) -> tuple[float, float]:
        """The axis's safe range."""

    @abstractmethod
    def move_absolute_um(self, axis: str, target_um: float) -> bool:
        """Start moving to target (clamped to the safe range). True if it was clamped."""

    @abstractmethod
    def move_velocity_um_s(self, axis: str, velocity_um_s: float) -> None:
        """Move at constant velocity until stop() or the edge of the safe range. 0 stops."""

    @abstractmethod
    def stop(self, axis: str) -> None: ...

    def move_relative_um(self, axis: str, delta_um: float) -> bool:
        return self.move_absolute_um(axis, self.position_um(axis) + delta_um)

    def at_limit(self, axis: str) -> bool:
        lo, hi = self.limits_um(axis)
        position = self.position_um(axis)
        return position <= lo + LIMIT_TOLERANCE_UM or position >= hi - LIMIT_TOLERANCE_UM


class NullStage(Stage):
    """Used when no stage hardware is present, so the app still runs. Every move is a no-op."""

    def is_connected(self) -> bool:
        return False

    def position_um(self, axis: str) -> float:
        return 0.0

    def limits_um(self, axis: str) -> tuple[float, float]:
        return (0.0, 0.0)

    def move_absolute_um(self, axis: str, target_um: float) -> bool:
        return False

    def move_velocity_um_s(self, axis: str, velocity_um_s: float) -> None:
        pass

    def stop(self, axis: str) -> None:
        pass

    def at_limit(self, axis: str) -> bool:
        return False
