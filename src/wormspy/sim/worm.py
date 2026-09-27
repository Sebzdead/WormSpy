"""A synthetic crawling worm for the simulated rig (ADR 0011).

The body is a chain of points in world (plate) coordinates, in µm, with index 0 at the head. The
leading end moves at the crawl speed and every other point follows its neighbour at a fixed
segment length. Moving "follow the leader" like this keeps the body length constant and means no
point can move faster than the leading end. Forward crawling leads with the head: the heading
does a random walk with a superimposed head swing. A reversal leads with the tail for a few
seconds.
"""

import math

import numpy as np


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class SyntheticWorm:
    def __init__(self, seed: int = 0, start_um=(0.0, 0.0), length_um: float = 1000.0,
                 n_points: int = 25, speed_um_s: float = 150.0,
                 reversal_rate_hz: float = 0.05, turn_sigma_rad_s: float = 0.4,
                 swing_amplitude_rad: float = 0.5, swing_freq_hz: float = 0.5) -> None:
        self._rng = np.random.default_rng(seed)
        self._seg = length_um / (n_points - 1)
        self._speed = speed_um_s
        self._reversal_rate = reversal_rate_hz
        self._turn_sigma = turn_sigma_rad_s
        self._swing_amp = swing_amplitude_rad
        self._swing_freq = swing_freq_hz
        self._t = 0.0
        self._reversal_left_s = 0.0

        self._heading = self._rng.uniform(-math.pi, math.pi)
        direction = np.array([math.cos(self._heading), math.sin(self._heading)])
        steps = np.arange(n_points)[:, None] * self._seg
        self.points = np.asarray(start_um, dtype=float) - steps * direction

    @property
    def head(self) -> np.ndarray:
        return self.points[0]

    @property
    def tail(self) -> np.ndarray:
        return self.points[-1]

    @property
    def centroid(self) -> np.ndarray:
        return self.points.mean(axis=0)

    @property
    def reversing(self) -> bool:
        return self._reversal_left_s > 0

    def reverse(self, duration_s: float) -> None:
        self._reversal_left_s = duration_s

    def step(self, dt: float) -> None:
        self._t += dt
        if self.reversing:
            self._reversal_left_s -= dt
            if not self.reversing:
                # resume forward crawling along the current head direction
                d = self.points[0] - self.points[1]
                self._heading = math.atan2(d[1], d[0])
        elif self._rng.random() < self._reversal_rate * dt:
            self.reverse(self._rng.uniform(1.0, 3.0))

        if self.reversing:
            self._crawl_backward(dt)
        else:
            self._crawl_forward(dt)

    def _crawl_forward(self, dt: float) -> None:
        self._heading += self._rng.normal(0.0, self._turn_sigma * math.sqrt(dt))
        swing = self._swing_amp * math.sin(2 * math.pi * self._swing_freq * self._t)
        angle = self._heading + swing
        self.points[0] = self.points[0] + self._speed * dt * np.array([math.cos(angle),
                                                                      math.sin(angle)])
        for i in range(1, len(self.points)):
            self._follow(i, i - 1)

    def _crawl_backward(self, dt: float) -> None:
        out_of_tail = _unit(self.points[-1] - self.points[-2])
        self.points[-1] = self.points[-1] + self._speed * dt * out_of_tail
        for i in range(len(self.points) - 2, -1, -1):
            self._follow(i, i + 1)

    def _follow(self, i: int, leader: int) -> None:
        self.points[i] = self.points[leader] + self._seg * _unit(self.points[i] -
                                                                 self.points[leader])
