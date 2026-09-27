"""Calibration end to end on the simulated rig: Calibrate -> matrices, calibration.json,
click-to-centre (ADR 0009)."""

import json
import time

import numpy as np
import pytest

from wormspy.app.executor import Executor
from wormspy.control import AppState, StateStore
from wormspy.control import commands as C
from wormspy.core.frames import FrameHub
from wormspy.sim.handle import SimRigHandle
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

FLUO = ImageGeometry(width_px=180, height_px=140, um_per_px=2.0, rotation_deg=-3.0)
BF = ImageGeometry(width_px=160, height_px=120, um_per_px=2.5, rotation_deg=5.0, flip_x=True)
START = (20_000.0, 20_000.0, 3_000.0)


def _app(tmp_path, target=True):
    stage = SimStage({a: (0.0, 50_000.0) for a in "xyz"}, 2000.0, dict(zip("xyz", START)))
    rig = SimRig(SyntheticWorm(seed=0, start_um=(1e6, 1e6)), stage, ChannelSpec(FLUO, "fluorescence"),
                 ChannelSpec(BF, "behaviour"), fps=20.0)
    rig.target = target
    handle = SimRigHandle(rig, clock_name="gcamp", follower_name="bf", speed=10.0)
    store = StateStore(AppState())
    executor = Executor(store, handle, FrameHub(), data_root=tmp_path / "data",
                        calibration_path=tmp_path / "calibration.json")
    return store, executor, handle


def _wait(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _stage_xy(handle):
    return handle.stage.position_um("x"), handle.stage.position_um("y")


def _calibrate(store, executor):
    store.dispatch(C.StartLive())
    executor.wait_idle()
    store.dispatch(C.Calibrate())
    executor.wait_idle()


def test_calibration_recovers_each_cameras_matrix_and_saves_it(tmp_path):
    store, executor, handle = _app(tmp_path)
    _calibrate(store, executor)
    assert store.state.calibrated and not store.state.calibrating, store.state.alarms
    saved = json.loads((tmp_path / "calibration.json").read_text())
    for name, geom in (("gcamp", FLUO), ("bf", BF)):
        matrix = np.array(saved["cameras"][name]["matrix_px_per_um"])
        # error relative to the matrix scale; element-wise rtol is meaningless for the
        # near-zero off-diagonal terms of a slightly rotated camera
        error = np.abs(matrix - geom.matrix).max() / np.abs(geom.matrix).max()
        assert error < 0.01, (name, matrix, geom.matrix)
    assert _stage_xy(handle) == pytest.approx(START[:2], abs=0.5)      # returns to start
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_click_to_centre_brings_the_clicked_point_to_the_middle(tmp_path):
    store, executor, handle = _app(tmp_path)
    _calibrate(store, executor)
    target_world = np.array(START[:2]) + (60.0, -35.0)        # a point on the plate in view
    px = BF.world_to_px(target_world[None, :], stage_um=_stage_xy(handle))[0]
    store.dispatch(C.CentreOn(channel="bf", x_px=float(px[0]), y_px=float(px[1])))
    executor.wait_idle()
    _wait(lambda: np.allclose(_stage_xy(handle), target_world, atol=2.0))
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_without_texture_calibration_fails_safely(tmp_path):
    store, executor, handle = _app(tmp_path, target=False)
    _calibrate(store, executor)
    assert not store.state.calibrated and not store.state.calibrating
    assert any(a.kind == "calibration_failed" for a in store.state.alarms)
    assert not (tmp_path / "calibration.json").exists()
    assert _stage_xy(handle) == pytest.approx(START[:2], abs=0.5)
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_a_saved_calibration_is_loaded_at_startup(tmp_path):
    store, executor, _ = _app(tmp_path)
    _calibrate(store, executor)
    store.dispatch(C.StopLive())
    executor.wait_idle()
    store2, executor2, _ = _app(tmp_path)
    executor2.wait_idle()
    assert store2.state.calibrated
