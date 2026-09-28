"""RigHandle: what the executor needs from a rig, simulated or real (ADR 0005/0011)."""

from typing import Protocol

from wormspy.acquisition.recorder import ChannelConfig
from wormspy.core.cameras import Camera
from wormspy.core.stage import Stage
from wormspy.presets import CameraSettings


class RigHandle(Protocol):
    clock_camera: Camera      # calcium channel, free-running at `fps` (ADR 0006)
    follower_camera: Camera   # triggered 1:1 by the clock camera
    clock_channel: ChannelConfig
    follower_channel: ChannelConfig
    fps: float
    settings: CameraSettings  # frame rate, exposures and gains currently configured
    stage: Stage              # connected once at startup; NullStage if absent

    def apply_settings(self, settings: CameraSettings) -> None:
        """Reconfigure the cameras. Only called while stopped."""

    def now(self) -> float:
        """The clock that stamps frames' capture_ts (time.monotonic on the rig)."""

    def start(self) -> None:
        """Begin exposing frames (configure trigger, start both cameras)."""

    def stop(self) -> None:
        """Stop exposing frames."""
