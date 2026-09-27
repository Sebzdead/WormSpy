"""RigHandle: what the executor needs from a rig, simulated or real (ADR 0005/0011)."""

from typing import Protocol

from wormspy.acquisition.recorder import ChannelConfig
from wormspy.core.cameras import Camera


class RigHandle(Protocol):
    clock_camera: Camera      # calcium channel, free-running at `fps` (ADR 0006)
    follower_camera: Camera   # triggered 1:1 by the clock camera
    clock_channel: ChannelConfig
    follower_channel: ChannelConfig
    fps: float

    def start(self) -> None:
        """Begin exposing frames (configure trigger, start both cameras)."""

    def stop(self) -> None:
        """Stop exposing frames."""
