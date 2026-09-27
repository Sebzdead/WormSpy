"""Zaber stage adapter (ADR 0005). zaber-motion is imported lazily so the app runs without it.

Works in µm; zaber-motion converts to microsteps using its device database. At construction
each axis's safe range is written into the controller's `limit.min`/`limit.max`, so the firmware
itself stops velocity moves at the edge even if the app hangs. Software also clamps.
"""

from collections.abc import Callable

from wormspy.core.stage import NullStage, Stage, clamp


class ZaberStage(Stage):
    def __init__(self, axes: dict, safe_ranges_um: dict[str, tuple[float, float]],
                 max_speed_um_s: float, connections: tuple = ()) -> None:
        from zaber_motion import Units  # lazy: only needed with hardware (or its tests)
        self._units = Units
        self._axes = axes
        self._limits = dict(safe_ranges_um)
        self._max_speed = max_speed_um_s
        self._connections = connections
        for name, axis in self._axes.items():
            lo, hi = self._limits[name]
            axis.settings.set("limit.min", lo, unit=Units.LENGTH_MICROMETRES)
            axis.settings.set("limit.max", hi, unit=Units.LENGTH_MICROMETRES)

    @classmethod
    def connect(cls, xy_port: str, z_port: str, safe_ranges_um: dict[str, tuple[float, float]],
                max_speed_um_s: float) -> "ZaberStage":
        """The rig's wiring: XY controllers are devices 1 (x) and 2 (y) on one port, Z is
        device 1 on another. Verified manually on the rig, not in CI."""
        from zaber_motion import Library
        from zaber_motion.ascii import Connection
        Library.enable_device_db_store()
        xy, z = Connection.open_serial_port(xy_port), Connection.open_serial_port(z_port)
        horizontal, vertical = xy.detect_devices(), z.detect_devices()
        axes = {"x": horizontal[0].get_axis(1), "y": horizontal[1].get_axis(1),
                "z": vertical[0].get_axis(1)}
        return cls(axes, safe_ranges_um, max_speed_um_s, connections=(xy, z))

    def close(self) -> None:
        for connection in self._connections:
            connection.close()

    def is_connected(self) -> bool:
        return True

    def position_um(self, axis: str) -> float:
        return float(self._axes[axis].get_position(unit=self._units.LENGTH_MICROMETRES))

    def limits_um(self, axis: str) -> tuple[float, float]:
        return self._limits[axis]

    def move_absolute_um(self, axis: str, target_um: float) -> bool:
        target, hit = clamp(target_um, *self._limits[axis])
        self._axes[axis].move_absolute(target, unit=self._units.LENGTH_MICROMETRES,
                                       wait_until_idle=False)
        return hit

    def move_velocity_um_s(self, axis: str, velocity_um_s: float) -> None:
        if velocity_um_s == 0:
            self.stop(axis)
            return
        velocity = max(-self._max_speed, min(self._max_speed, velocity_um_s))
        self._axes[axis].move_velocity(velocity,
                                       unit=self._units.VELOCITY_MICROMETRES_PER_SECOND)

    def stop(self, axis: str) -> None:
        self._axes[axis].stop(wait_until_idle=False)


def connect_stage(connector: Callable[[], Stage]) -> Stage:
    """Open the stage once at startup. On any failure, return a NullStage so the app still runs
    (e.g. UI work with no hardware)."""
    try:
        return connector()
    except Exception as exc:   # noqa: BLE001 - degrade gracefully on any hardware/SDK error
        print(f"connect_stage: no stage hardware ({exc!r}); using NullStage.")
        return NullStage()
