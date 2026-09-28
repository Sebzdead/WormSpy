"""The simulated rig: synthetic worm, stage and two hardware-triggered cameras in closed loop.

This mirrors ADR 0006. The clock camera free-runs at `fps`, and each of its exposures triggers
exactly one follower exposure. Both cameras report device frame IDs and device timestamps (the
follower's clock is offset by `follower_offset_ns`). Frames listed in `*_drops` still consume a
frame ID but are never delivered, so they appear downstream as gaps, as a real drop would. Every
image is rendered from the *current* stage position, so stage moves change what the cameras see.

Time is simulated. Each `tick()` is one exposure followed by one frame period of worm and stage
motion, which keeps tests deterministic and faster than real time.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from wormspy.core.cameras import Camera
from wormspy.core.frames import Frame
from wormspy.sim.render import ImageGeometry, render_behaviour, render_fluorescence
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

_NO_WORM = SyntheticWorm(seed=0, start_um=(1e9, 1e9))   # far off every field of view


@dataclass(frozen=True)
class ChannelSpec:
    geometry: ImageGeometry
    role: str                 # "fluorescence" (uint16) or "behaviour" (uint8), see CONTEXT.md
    noise_sigma: float = 0.0

    def __post_init__(self) -> None:
        if self.role not in ("fluorescence", "behaviour"):
            raise ValueError(f"unknown channel role {self.role!r}")


class SimCamera(Camera):
    """Delivers frames the rig pushes to it. `read()` is non-blocking: None when empty."""

    def __init__(self) -> None:
        self._queue: deque[Frame] = deque()
        self._open = True
        self._failure: str | None = None

    def deliver(self, frame: Frame) -> None:
        if self._open:
            self._queue.append(frame)

    def read(self) -> Frame | None:
        if self._failure is not None:
            raise RuntimeError(self._failure)
        return self._queue.popleft() if self._queue else None

    def fail(self, reason: str) -> None:
        """Make every later read raise, like a camera that has been disconnected."""
        self._failure = reason

    def is_open(self) -> bool:
        return self._open

    def release(self) -> None:
        self._open = False
        self._queue.clear()


class SimRig:
    def __init__(self, worm: SyntheticWorm, stage: SimStage, clock: ChannelSpec,
                 follower: ChannelSpec, fps: float, follower_offset_ns: int = 0,
                 clock_drops: Iterable[int] = (), follower_drops: Iterable[int] = (),
                 seed: int = 0) -> None:
        self.worm = worm
        self.stage = stage
        self.clock_camera = SimCamera()
        self.follower_camera = SimCamera()
        self.clock_spec, self.follower_spec = clock, follower
        self.target = False       # True: a calibration bead slide is under the objective
        self._channels = ((self.clock_camera, clock, set(clock_drops), 0),
                          (self.follower_camera, follower, set(follower_drops),
                           follower_offset_ns))
        self._period_ns = round(1e9 / fps)
        self._brightness = [1.0, 1.0]    # clock, follower: signal vs reference settings
        self._frame_id = 0
        self._rng = np.random.default_rng(seed)

    def set_camera(self, fps: float, clock_brightness: float,
                   follower_brightness: float) -> None:
        """New frame rate, and each camera's signal relative to its reference settings
        (exposure × linear gain), as a real sensor would respond."""
        self._period_ns = round(1e9 / fps)
        self._brightness = [clock_brightness, follower_brightness]

    @property
    def frame_period_s(self) -> float:
        return self._period_ns / 1e9

    @property
    def time_s(self) -> float:
        return self._frame_id * self._period_ns / 1e9

    def tick(self) -> None:
        """Expose one triggered frame pair, then advance the world by one frame period."""
        stage_um = (self.stage.position_um("x"), self.stage.position_um("y"))
        t_ns = self._frame_id * self._period_ns
        for (camera, spec, drops, offset_ns), brightness in zip(self._channels,
                                                                self._brightness, strict=True):
            if self._frame_id in drops:
                continue
            camera.deliver(Frame(image=self._render(spec, stage_um, brightness),
                                 device_frame_id=self._frame_id,
                                 capture_ts=t_ns / 1e9,
                                 device_timestamp_ns=t_ns + offset_ns))
        self._frame_id += 1
        dt = self._period_ns / 1e9
        self.stage.advance(dt)
        self.worm.step(dt)

    def _render(self, spec: ChannelSpec, stage_um, brightness: float = 1.0) -> np.ndarray:
        render = render_fluorescence if spec.role == "fluorescence" else render_behaviour
        # the calibration slide replaces the plate: no worm under the objective meanwhile
        worm = _NO_WORM if self.target else self.worm
        image = render(worm, stage_um, spec.geometry, noise_sigma=spec.noise_sigma,
                       rng=self._rng, target=self.target)
        if brightness != 1.0:
            top = np.iinfo(image.dtype).max
            image = np.clip(image * brightness, 0, top).astype(image.dtype)
        return image
