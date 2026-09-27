"""SimRigHandle: presents a SimRig through the RigHandle interface the executor uses."""

from wormspy.acquisition.recorder import ChannelConfig
from wormspy.sim.driver import SimDriver
from wormspy.sim.rig import ChannelSpec, SimRig


def _config(name: str, spec: ChannelSpec) -> ChannelConfig:
    return ChannelConfig(name=name, role=spec.role,
                         shape=(spec.geometry.height_px, spec.geometry.width_px))


class SimRigHandle:
    def __init__(self, rig: SimRig, clock_name: str, follower_name: str,
                 speed: float = 1.0) -> None:
        self.rig = rig
        self.clock_camera = rig.clock_camera
        self.follower_camera = rig.follower_camera
        self.clock_channel = _config(clock_name, rig.clock_spec)
        self.follower_channel = _config(follower_name, rig.follower_spec)
        self.fps = 1.0 / rig.frame_period_s
        self.stage = rig.stage
        self._speed = speed
        self._driver: SimDriver | None = None

    def now(self) -> float:
        """Simulated time: stage motion and frame capture_ts both run on it."""
        return self.rig.time_s

    def start(self) -> None:
        self._driver = SimDriver(self.rig, speed=self._speed)
        self._driver.start()

    def stop(self) -> None:
        if self._driver is not None:
            self._driver.stop()
            self._driver = None

    def fail_camera(self, reason: str) -> None:
        """Simulate the clock camera disconnecting."""
        self.clock_camera.fail(reason)
