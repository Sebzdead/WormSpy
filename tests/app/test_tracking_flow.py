"""Tracking end to end through the executor on the threaded simulated rig (ADR 0008)."""

import csv
import math
import time
from pathlib import Path

from wormspy.app.executor import Executor
from wormspy.calibration.math import CameraCalibration
from wormspy.calibration.routine import RigCalibration
from wormspy.control import AppState, StateStore, TrackingStatus
from wormspy.control import commands as C
from wormspy.core.frames import FrameHub
from wormspy.sim.handle import SimRigHandle
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm
from wormspy.tracking.estimators import BrightMarker, DarkObject

GEOM = ImageGeometry(width_px=320, height_px=200, um_per_px=4.5)
START = (25_000.0, 25_000.0)
ESTIMATORS = {"dark_object": DarkObject(background_sigma_px=30.0, min_area_px=300.0),
              "bright_marker": BrightMarker(spot_sigma_px=1.0)}


def _app(tmp_path, override=lambda: False):
    worm = SyntheticWorm(seed=2, start_um=START, speed_um_s=150.0)
    stage = SimStage({a: (0.0, 50_400.0) for a in "xyz"}, 2000.0,
                     {"x": START[0], "y": START[1], "z": 5_000.0})
    rig = SimRig(worm, stage, ChannelSpec(GEOM, "fluorescence", noise_sigma=100.0),
                 ChannelSpec(GEOM, "behaviour", noise_sigma=4.0), fps=20.0)
    handle = SimRigHandle(rig, clock_name="gcamp", follower_name="bf", speed=4.0)
    shape = (GEOM.height_px, GEOM.width_px)
    RigCalibration(cameras={name: CameraCalibration(GEOM.matrix, 0.0, shape)
                            for name in ("gcamp", "bf")}).save(tmp_path / "calibration.json")
    store = StateStore(AppState())
    executor = Executor(store, handle, FrameHub(), data_root=tmp_path / "data",
                        calibration_path=tmp_path / "calibration.json", estimators=ESTIMATORS,
                        override=override)
    return store, executor, handle, worm


def _wait(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _offset(handle, worm):
    return math.dist(worm.centroid, (handle.stage.position_um("x"), handle.stage.position_um("y")))


def _sim_seconds(handle, seconds):
    end = handle.rig.time_s + seconds
    _wait(lambda: handle.rig.time_s >= end, timeout=seconds + 10)


def test_tracking_keeps_the_worm_centred(tmp_path):
    store, executor, handle, worm = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.SetTracking(enabled=True))
    executor.wait_idle()
    _sim_seconds(handle, 20.0)
    assert store.state.tracking is TrackingStatus.TRACKING
    assert math.dist(worm.centroid, START) > 800 and _offset(handle, worm) < 250
    store.dispatch(C.SetTracking(enabled=False))
    executor.wait_idle()
    assert handle.stage.velocity_um_s("x") == 0.0 and handle.stage.velocity_um_s("y") == 0.0
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_tracking_marker_mode_follows_the_fluorescent_neuron(tmp_path):
    store, executor, handle, worm = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.SelectEstimator(name="bright_marker", source_channel="fluorescence"))
    store.dispatch(C.SetTracking(enabled=True))
    executor.wait_idle()
    _sim_seconds(handle, 15.0)
    neuron = worm.points[2]
    assert math.dist(neuron, (handle.stage.position_um("x"), handle.stage.position_um("y"))) < 150
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_a_recording_gets_a_tracking_csv_joinable_to_frames_csv(tmp_path):
    store, executor, handle, _ = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    store.dispatch(C.SetTracking(enabled=True))
    executor.wait_idle()
    _wait(lambda: store.state.session_dir is not None)
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    _sim_seconds(handle, 5.0)
    store.dispatch(C.StopRecording())
    store.dispatch(C.StopLive())
    executor.wait_idle()
    rec = Path(store.state.session_dir) / "rec-001"
    with open(rec / "frames.csv", newline="") as fh:
        frames = {r["frame_index"] for r in csv.DictReader(fh)}
    with open(rec / "tracking.csv", newline="") as fh:
        tracking = list(csv.DictReader(fh))
    # The tracker evaluates the latest frame at 50 Hz *real* time; the sim runs 4x faster than
    # real time (80 frames per real second), so here it skips some frames by design (ADR 0008).
    # On the rig (10-20 fps) it evaluates every frame.
    assert len(tracking) > 30
    assert {r["frame_index"] for r in tracking} <= frames | {""}
    assert sum(r["frame_index"] in frames for r in tracking) > 30
    assert {r["status"] for r in tracking} == {"tracking"}


def test_the_joystick_overrides_tracking(tmp_path):
    stick = {"active": False}
    store, executor, handle, _ = _app(tmp_path, override=lambda: stick["active"])
    store.dispatch(C.StartLive())
    store.dispatch(C.SetTracking(enabled=True))
    executor.wait_idle()
    _sim_seconds(handle, 2.0)
    stick["active"] = True
    _wait(lambda: store.state.tracking is TrackingStatus.OVERRIDE)
    stick["active"] = False
    _wait(lambda: store.state.tracking is TrackingStatus.TRACKING)
    store.dispatch(C.StopLive())
    executor.wait_idle()
