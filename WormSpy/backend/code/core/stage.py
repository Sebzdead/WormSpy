from abc import ABC, abstractmethod
from typing import Dict, Tuple


def clamp_native(value: float, minimum: float, maximum: float) -> Tuple[float, bool]:
    """Clamp value into [minimum, maximum]. Returns (clamped_value, limit_was_hit)."""
    if value > maximum:
        return maximum, True
    if value < minimum:
        return minimum, True
    return value, False


class Stage(ABC):
    """XYZ stage interface in native (microstep) units. Adapters wrap real hardware."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def get_position(self, axis: str) -> float:
        ...

    @abstractmethod
    def limits(self, axis: str) -> Tuple[float, float]:
        ...

    @abstractmethod
    def move_relative(self, axis: str, native: float) -> bool:
        """Move axis by `native` microsteps, clamped to limits. Returns True if a limit was hit."""

    @abstractmethod
    def move_absolute(self, axis: str, native: float) -> bool:
        """Move axis to absolute `native` microsteps, clamped to limits. Returns True if hit."""


class NullStage(Stage):
    """Used when no hardware is present so the app still runs. All moves are no-ops."""

    def is_connected(self) -> bool:
        return False

    def get_position(self, axis: str) -> float:
        return 0.0

    def limits(self, axis: str) -> Tuple[float, float]:
        return (0.0, 0.0)

    def move_relative(self, axis: str, native: float) -> bool:
        return False

    def move_absolute(self, axis: str, native: float) -> bool:
        return False


class FakeStage(Stage):
    """In-memory stage for tests. Honors limits via clamp_native."""

    def __init__(self, limits: Dict[str, Tuple[float, float]],
                 positions: Dict[str, float]) -> None:
        self._limits = dict(limits)
        self._pos = dict(positions)

    def is_connected(self) -> bool:
        return True

    def get_position(self, axis: str) -> float:
        return self._pos[axis]

    def limits(self, axis: str) -> Tuple[float, float]:
        return self._limits[axis]

    def move_relative(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        target, hit = clamp_native(self._pos[axis] + native, lo, hi)
        self._pos[axis] = target
        return hit

    def move_absolute(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        target, hit = clamp_native(native, lo, hi)
        self._pos[axis] = target
        return hit
