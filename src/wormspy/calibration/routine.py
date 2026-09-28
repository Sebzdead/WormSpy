"""The calibration routine (ADR 0009): move the stage through a small pattern, measure the image
shift in every camera, and fit each camera's stage→image matrix.

It needs a static, textured sample under the objective (a bead or grid slide) and a running
live view. If both cameras can see the sample (fluorescent beads under transmitted light), it
also registers the cameras: where the second camera sees the first camera's centre. Robustness:
- the fit uses the stage position *measured* after each move, not the commanded one, so a move
  clamped near a limit can't corrupt it;
- each image is taken at least two frames after the stage settled, so its exposure can't overlap
  the motion;
- the result is validated (enough image shift, small residual), and the stage always returns to
  where it started.
"""

import json
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from wormspy.calibration.math import (
    CameraCalibration,
    fit_matrix,
    measure_shift,
    register_origin,
)
from wormspy.core.frames import FrameHub
from wormspy.core.stage import Stage

PATTERN = np.array([[1, 0], [0, 1], [-1, 0], [0, -1], [0.7, 0.7], [-0.7, 0.7]], float)


class CalibrationError(Exception):
    pass


@dataclass(frozen=True)
class RigCalibration:
    cameras: dict[str, CameraCalibration]
    reference: str | None = None          # the camera whose centre is the stage position
    registration: str | None = None       # why the cameras aren't registered; None = they are
    created: str = field(default_factory=lambda: datetime.now().astimezone()
                         .isoformat(timespec="seconds"))

    def residuals(self) -> dict[str, float]:
        return {name: cal.residual_px for name, cal in self.cameras.items()}

    @property
    def registered(self) -> bool:
        return self.reference is not None and all(
            name == self.reference or cal.origin_px is not None
            for name, cal in self.cameras.items())

    def map_px(self, source: str, target: str, px) -> tuple[float, float] | None:
        """Where camera `target` sees what camera `source` sees at pixel `px`
        (None until the cameras are registered)."""
        if source == target:
            return float(px[0]), float(px[1])
        if not self.registered or source not in self.cameras or target not in self.cameras:
            return None
        src, dst = self.cameras[source], self.cameras[target]
        world = np.linalg.solve(src.matrix, np.asarray(px, float) - src.origin)
        mapped = dst.origin + dst.matrix @ world
        return float(mapped[0]), float(mapped[1])

    def to_json(self) -> dict:
        return {"created": self.created, "reference": self.reference,
                "registration": self.registration,
                "cameras": {name: cal.to_json() for name, cal in self.cameras.items()}}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "RigCalibration":
        return cls.from_json(json.loads(Path(path).read_text()))

    @classmethod
    def from_json(cls, data: dict) -> "RigCalibration":
        return cls(cameras={name: CameraCalibration.from_json(cam)
                            for name, cam in data["cameras"].items()},
                   reference=data.get("reference"),
                   registration=data.get("registration", "Calibrated before registration "
                                                         "existed; calibrate again."),
                   created=data["created"])


def run_calibration(stage: Stage, hub: FrameHub, channels: list[str], step_um: float = 100.0,
                    settle_tolerance_um: float = 0.5, timeout_s: float = 10.0,
                    min_shift_px: float = 2.0, max_residual_px: float = 1.0,
                    min_match: float = 0.05) -> RigCalibration:
    start = np.array([stage.position_um("x"), stage.position_um("y")])
    try:
        reference = _settled_images(stage, hub, channels, start, settle_tolerance_um, timeout_s)
        deltas, shots = [], []
        for direction in PATTERN:
            reached = _move_and_settle(stage, start + step_um * direction, settle_tolerance_um,
                                       timeout_s)
            shots.append(_fresh_images(hub, channels, timeout_s))
            deltas.append(reached - start)
    finally:
        _move_and_settle(stage, start, settle_tolerance_um, timeout_s)

    cameras = {}
    for name in channels:
        shifts = np.array([measure_shift(reference[name], shot[name]) for shot in shots])
        if np.min(np.linalg.norm(shifts, axis=1)) < min_shift_px:
            raise CalibrationError(f"{name}: the image barely moved. Is a textured calibration "
                                   "slide in focus under the objective?")
        matrix, residual = fit_matrix(np.array(deltas), shifts)
        if residual > max_residual_px:
            raise CalibrationError(f"{name}: residual {residual:.2f} px is too large. Check "
                                   "that the sample is static and in focus.")
        cameras[name] = CameraCalibration(matrix=matrix, residual_px=residual,
                                          image_shape=reference[name].shape[:2])
    return _register(cameras, reference, channels[0], min_match)


def _register(cameras: dict[str, CameraCalibration], images: dict[str, np.ndarray],
              reference: str, min_match: float) -> RigCalibration:
    """Measure where each other camera sees the reference camera's centre. A failure here is
    not fatal: tracking and click-to-centre only need each camera's own matrix."""
    ref = cameras[reference]
    for name, cal in list(cameras.items()):
        if name == reference:
            continue
        origin, match = register_origin(images[reference], ref.matrix, images[name], cal.matrix)
        if match < min_match:
            return RigCalibration(
                cameras=cameras, reference=reference,
                registration=f"{name} and {reference} share no features (match {match:.2f}). "
                             "Use a slide both cameras see, e.g. fluorescent beads under "
                             "transmitted light.")
        cameras[name] = replace(cal, origin_px=(float(origin[0]), float(origin[1])))
    return RigCalibration(cameras=cameras, reference=reference)


def _move_and_settle(stage: Stage, target_xy: np.ndarray, tolerance_um: float,
                     timeout_s: float) -> np.ndarray:
    stage.move_absolute_um("x", float(target_xy[0]))
    stage.move_absolute_um("y", float(target_xy[1]))
    deadline = time.monotonic() + timeout_s
    while True:
        position = np.array([stage.position_um("x"), stage.position_um("y")])
        if np.all(np.abs(position - target_xy) <= tolerance_um):
            return position
        if time.monotonic() > deadline:
            raise CalibrationError("the stage did not reach its target in time")
        time.sleep(0.005)


def _settled_images(stage: Stage, hub: FrameHub, channels: list[str], target_xy: np.ndarray,
                    tolerance_um: float, timeout_s: float) -> dict[str, np.ndarray]:
    _move_and_settle(stage, target_xy, tolerance_um, timeout_s)
    return _fresh_images(hub, channels, timeout_s)


def _fresh_images(hub: FrameHub, channels: list[str], timeout_s: float,
                  frames_after: int = 2) -> dict[str, np.ndarray]:
    """Images from frames that began exposing after this call: `frames_after` new frames."""
    wait_for = {}
    for name in channels:
        latest = hub.latest(name)
        wait_for[name] = (latest.device_frame_id if latest is not None else -1) + frames_after
    deadline = time.monotonic() + timeout_s
    images: dict[str, np.ndarray] = {}
    while len(images) < len(channels):
        for name in channels:
            frame = hub.latest(name)
            if name not in images and frame is not None and frame.device_frame_id >= wait_for[name]:
                images[name] = frame.image
        if time.monotonic() > deadline:
            raise CalibrationError("no new frames arrived. Is the live view running?")
        time.sleep(0.002)
    return images

