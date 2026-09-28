"""SimRigHandle: presents a SimRig through the RigHandle interface the executor uses."""

from wormspy.acquisition.recorder import ChannelConfig
from wormspy.presets import CameraSettings
from wormspy.sim.driver import SimDriver
from wormspy.sim.rig import ChannelSpec, SimRig


def _config(name: str, spec: ChannelSpec) -> ChannelConfig:
    return ChannelConfig(name=name, role=spec.role,
                         shape=(spec.geometry.height_px, spec.geometry.width_px))


class SimRigHandle:
    def __init__(self, rig: SimRig, clock_name: str, follower_name: str,
                 speed: float = 1.0, settings: CameraSettings | None = None) -> None:
        self.rig = rig
        self.clock_camera = rig.clock_camera
        self.follower_camera = rig.follower_camera
        self.clock_channel = _config(clock_name, rig.clock_spec)
        self.follower_channel = _config(follower_name, rig.follower_spec)
        self.fps = 1.0 / rig.frame_period_s
        # The simulated images are rendered for these settings; others scale the brightness.
        self.settings = settings or CameraSettings(
            fps=self.fps, clock_exposure_us=14_000, clock_gain_db=0,
            follower_exposure_us=5_000, follower_gain_db=0)
        self._reference = self.settings
        self.stage = rig.stage
        self._speed = speed
        self._driver: SimDriver | None = None

    def apply_settings(self, settings: CameraSettings) -> None:
        ref = self._reference

        def signal(exposure_us: float, gain_db: float) -> float:
            return exposure_us * 10 ** (gain_db / 20)
        self.rig.set_camera(
            settings.fps,
            signal(settings.clock_exposure_us, settings.clock_gain_db)
            / signal(ref.clock_exposure_us, ref.clock_gain_db),
            signal(settings.follower_exposure_us, settings.follower_gain_db)
            / signal(ref.follower_exposure_us, ref.follower_gain_db))
        self.settings, self.fps = settings, settings.fps

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
