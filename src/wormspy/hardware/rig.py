"""HardwareRig: the real cameras and stage behind the RigHandle interface (ADR 0005/0006).

`open_rig` opens everything once at startup: the two FLIR cameras by serial (configured for the
1:1 hardware trigger) and the Zaber stage. A missing camera is fatal, with a message saying
what to fix. A missing stage is not: the app runs with a NullStage and reports why in
`warnings`, so imaging still works while the stage is being sorted out.
"""

import time
from collections.abc import Callable

from wormspy.acquisition.recorder import ChannelConfig
from wormspy.config import CamerasConfig, RigConfig
from wormspy.core.stage import NullStage, Stage
from wormspy.hardware.flir import FlirCamera, FlirSystem, HardwareError


class HardwareRig:
    def __init__(self, clock: FlirCamera, follower: FlirCamera, cameras: CamerasConfig,
                 stage: Stage, closers: tuple[Callable[[], None], ...] = (),
                 warnings: tuple[str, ...] = ()) -> None:
        self.clock_camera, self.follower_camera = clock, follower
        self.clock_channel = ChannelConfig(cameras.clock.name, cameras.clock.role, clock.shape)
        self.follower_channel = ChannelConfig(cameras.follower.name, cameras.follower.role,
                                              follower.shape)
        self.fps = cameras.fps
        self.stage = stage
        self.warnings = warnings
        self._closers = closers
        clock.configure_as_clock(cameras.fps, cameras.clock_output_line)
        follower.configure_as_follower(cameras.follower_trigger_line)

    def now(self) -> float:
        return time.monotonic()        # FlirCamera maps device timestamps onto this clock

    def start(self) -> None:
        """Arm the follower first, so it sees the clock's very first exposure."""
        self.follower_camera.begin()
        try:
            self.clock_camera.begin()
        except Exception:
            self.follower_camera.end()
            raise

    def stop(self) -> None:
        self.clock_camera.end()        # no more triggers, then the follower
        self.follower_camera.end()

    def close(self) -> None:
        self.stop()
        for close in self._closers:
            close()


def _zaber(config: RigConfig) -> Stage:
    from wormspy.hardware.zaber import ZaberStage
    s = config.stage
    return ZaberStage.connect(s.xy_port, s.z_port, s.safe_range_um, s.max_speed_um_s)


def open_rig(config: RigConfig, pyspin=None,
             stage_connector: Callable[[RigConfig], Stage] = _zaber) -> HardwareRig:
    system = FlirSystem(pyspin)
    try:
        clock = system.camera(config.cameras.clock, "cameras.clock")
        follower = system.camera(config.cameras.follower, "cameras.follower")
    except HardwareError:
        system.close()
        raise
    warnings = []
    try:
        stage = stage_connector(config)
    except Exception as exc:          # noqa: BLE001 - any SDK/serial error: run without a stage
        stage = NullStage()
        warnings.append(f"No stage ({type(exc).__name__}: {exc}). Check the Zaber ports in "
                        "rig.toml; joystick, tracking and calibration need the stage.")
    closers = [system.close]
    if hasattr(stage, "close"):
        closers.append(stage.close)
    try:
        return HardwareRig(clock, follower, config.cameras, stage, tuple(closers),
                           tuple(warnings))
    except Exception:
        for close in closers:
            close()
        raise
