"""Zaber hardware adapter. Imports zaber_motion lazily so tests never load the SDK."""

from typing import Callable, Optional, Tuple

from core.stage import Stage, NullStage, clamp_native


class ZaberStage(Stage):
    """Wraps three Zaber axes (x, y on the XY connection; z on the Z connection).

    Connections are opened once at construction and held for the process lifetime."""

    def __init__(self, xy_port: str, z_port: str) -> None:
        from zaber_motion import Library                       # lazy import
        from zaber_motion.ascii import Connection
        Library.enable_device_db_store()

        self._xy_conn = Connection.open_serial_port(xy_port)
        self._z_conn = Connection.open_serial_port(z_port)
        self._xy_conn.enable_alerts()
        self._z_conn.enable_alerts()

        horizontal = self._xy_conn.detect_devices()
        vertical = self._z_conn.detect_devices()
        self._axes = {
            "x": horizontal[0].get_axis(1),
            "y": horizontal[1].get_axis(1),
            "z": vertical[0].get_axis(1),
        }
        self._limits = {
            name: (float(axis.settings.get("limit.min")),
                   float(axis.settings.get("limit.max")))
            for name, axis in self._axes.items()
        }

    def is_connected(self) -> bool:
        return True

    def get_position(self, axis: str) -> float:
        from zaber_motion import Units
        return float(self._axes[axis].get_position(unit=Units.NATIVE))

    def limits(self, axis: str) -> Tuple[float, float]:
        return self._limits[axis]

    def move_relative(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        current = self.get_position(axis)
        target, hit = clamp_native(current + native, lo, hi)
        delta = int(target - current)
        if delta != 0:
            self._axes[axis].generic_command_no_response(f"move rel {delta} 10000 5")
        return hit

    def move_absolute(self, axis: str, native: float) -> bool:
        from zaber_motion import Units
        lo, hi = self._limits[axis]
        target, hit = clamp_native(native, lo, hi)
        self._axes[axis].move_absolute(target, unit=Units.NATIVE, wait_until_idle=False)
        return hit


def connect_stage(xy_port: str, z_port: str,
                  connector: Optional[Callable[..., Stage]] = None) -> Stage:
    """Open the stage once at startup. On any failure, return a NullStage so the server still
    runs (e.g. for UI/dev work with no hardware). `connector` is injectable for tests."""
    factory = connector if connector is not None else (lambda **kw: ZaberStage(**kw))
    try:
        return factory(xy_port=xy_port, z_port=z_port)
    except Exception as exc:   # noqa: BLE001 - degrade gracefully on any hardware/SDK error
        print(f"connect_stage: no stage hardware ({exc!r}); using NullStage.")
        return NullStage()
