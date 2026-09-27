"""The tracking controller (ADR 0008), as pure logic: no threads, hardware or clocks.

TargetFilter (called once per processed frame) turns estimator output, already converted to
world (plate) µm, into the tracking target:
- An estimate is plausible if it lies within `jump_margin + max_worm_speed × Δt` of the last
  accepted target. Anything further is a phantom (debris, a second worm, noise) and counts as a
  miss.
- After `lost_after_misses` consecutive misses the target is *lost*. It is re-acquired when a
  plausible estimate appears near the last known position, with a search radius that keeps
  growing with time since the worm was last seen.

VelocityServo (called at a fixed rate) turns the target into a stage velocity:
- Velocity feed-forward: the stage moves with the worm's estimated velocity, so a steadily
  crawling worm is followed without the proportional term's steady-state lag (speed / gain).
- On top of that, a correction proportional to the distance to the absolute target, with a deadband, a speed
  cap (direction preserved) and an acceleration limit, so starts, stops and a lost target are
  ramps, not jerks.
- It yields completely to the joystick (override) and never pushes an axis further into its
  safe-range limit.
"""

import math
from dataclasses import dataclass

from wormspy.control.state import TrackingStatus

Point = tuple[float, float]


@dataclass(frozen=True)
class ServoConfig:
    gain_per_s: float = 2.0            # stage velocity (µm/s) per µm of error
    deadband_um: float = 10.0          # hold still when the worm is this close to centre
    max_speed_um_s: float = 1500.0
    max_accel_um_s2: float = 3000.0
    max_worm_speed_um_s: float = 500.0  # C. elegans crawls at ~100–300 µm/s
    jump_margin_um: float = 100.0      # estimator jitter / body-shape change allowance
    lost_after_misses: int = 3
    feed_forward: float = 1.0          # fraction of the worm's estimated velocity to follow
    velocity_smoothing: float = 0.2    # EMA weight of each new velocity sample


class TargetFilter:
    def __init__(self, config: ServoConfig) -> None:
        self._cfg = config
        self.reset()

    def reset(self) -> None:
        self.status = TrackingStatus.TRACKING
        self.target: Point | None = None
        self.velocity: Point = (0.0, 0.0)      # smoothed worm velocity estimate, µm/s
        self._last: Point | None = None        # last accepted position, kept while lost
        self._last_t = 0.0
        self._misses = 0

    def update(self, t: float, point: Point | None) -> TrackingStatus:
        if point is not None and self._plausible(t, point):
            accepted = (float(point[0]), float(point[1]))
            if self.target is not None and t > self._last_t:
                a, dt = self._cfg.velocity_smoothing, t - self._last_t
                sample = ((accepted[0] - self.target[0]) / dt, (accepted[1] - self.target[1]) / dt)
                self.velocity = (a * sample[0] + (1 - a) * self.velocity[0],
                                 a * sample[1] + (1 - a) * self.velocity[1])
            self.target = self._last = accepted
            self._last_t = t
            self._misses = 0
            self.status = TrackingStatus.TRACKING
            return self.status
        self._misses += 1
        if self._misses >= self._cfg.lost_after_misses:
            self.status = TrackingStatus.LOST
            self.target = None
            self.velocity = (0.0, 0.0)
        return self.status

    def _plausible(self, t: float, point: Point) -> bool:
        if self._last is None:
            return True                        # nothing to compare with: first acquisition
        radius = self._cfg.jump_margin_um + self._cfg.max_worm_speed_um_s * (t - self._last_t)
        return math.dist(point, self._last) <= radius


class VelocityServo:
    def __init__(self, config: ServoConfig) -> None:
        self._cfg = config
        self._v = (0.0, 0.0)

    def step(self, dt: float, target: Point | None, position: Point, override: bool = False,
             at_limit: dict[str, str] | None = None,
             target_velocity: Point = (0.0, 0.0)) -> tuple[Point | None, TrackingStatus]:
        """One servo tick. Returns (velocity to command, or None to send nothing; status)."""
        if override:
            self._v = (0.0, 0.0)               # restart from rest when the stick is released
            return None, TrackingStatus.OVERRIDE
        cfg = self._cfg
        status = TrackingStatus.TRACKING if target is not None else TrackingStatus.LOST
        desired = [0.0, 0.0]
        if target is not None:
            desired = [cfg.feed_forward * target_velocity[0], cfg.feed_forward * target_velocity[1]]
            error = (target[0] - position[0], target[1] - position[1])
            if math.hypot(*error) > cfg.deadband_um:
                desired = [desired[0] + cfg.gain_per_s * error[0],
                           desired[1] + cfg.gain_per_s * error[1]]
        for i, axis in enumerate("xy"):
            side = (at_limit or {}).get(axis)
            if (side == "max" and desired[i] > 0) or (side == "min" and desired[i] < 0):
                desired[i] = 0.0
                status = TrackingStatus.LIMIT
        speed = math.hypot(*desired)
        if speed > cfg.max_speed_um_s:
            desired = [d * cfg.max_speed_um_s / speed for d in desired]
        change = (desired[0] - self._v[0], desired[1] - self._v[1])
        max_change = cfg.max_accel_um_s2 * dt
        magnitude = math.hypot(*change)
        if magnitude > max_change:
            change = (change[0] * max_change / magnitude, change[1] * max_change / magnitude)
        self._v = (self._v[0] + change[0], self._v[1] + change[1])
        return self._v, status
