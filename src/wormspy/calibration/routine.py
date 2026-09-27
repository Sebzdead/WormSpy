"""The calibration routine (ADR 0009): move the stage through a small pattern, measure the image
shift in every camera, and fit each camera's stage→image matrix.

It needs a static, textured sample under the objective (a bead or grid slide) and a running
live view. Robustness:
- the fit uses the stage position *measured* after each move, not the commanded one, so a move
  clamped near a limit can't corrupt it;
- each image is taken at least two frames after the stage settled, so its exposure can't overlap
  the motion;
- the result is validated (enough image shift, small residual), and the stage always returns to
  where it started.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from wormspy.calibration.math import CameraCalibration, fit_matrix, measure_shift
from wormspy.core.frames import FrameHub
from wormspy.core.stage import Stage

PATTERN = np.array([[1, 0], [0, 1], [-1, 0], [0, -1], [0.7, 0.7], [-0.7, 0.7]], float)


class CalibrationError(Exception):
    pass


@dataclass(frozen=True)
class RigCalibration:
    cameras: dict[str, CameraCalibration]
    created: str = field(default_factory=lambda: datetime.now().astimezone()
                         .isoformat(timespec="seconds"))

    def residuals(self) -> dict[str, float]:
        return {name: cal.residual_px for name, cal in self.cameras.items()}

    def save(self, path: Path) -> None:
        data = {"created": self.created,
                "cameras": {name: cal.to_json() for name, cal in self.cameras.items()}}
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "RigCalibration":
        data = json.loads(Path(path).read_text())
        return cls(cameras={name: CameraCalibration.from_json(cam)
                            for name, cam in data["cameras"].items()},
                   created=data["created"])


def run_calibration(stage: Stage, hub: FrameHub, channels: list[str], step_um: float = 100.0,
                    settle_tolerance_um: float = 0.5, timeout_s: float = 10.0,
                    min_shift_px: float = 2.0, max_residual_px: float = 1.0) -> RigCalibration:
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
    return RigCalibration(cameras=cameras)


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

