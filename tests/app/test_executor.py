"""Executor, end to end on the simulated rig: commands -> effects -> files on disk."""

import csv
import json
import time
from pathlib import Path

import cv2
import pytest
import tifffile

from wormspy.app.executor import Executor
from wormspy.control import AppState, Mode, StateStore
from wormspy.control import commands as C
from wormspy.core.frames import FrameHub
from wormspy.presets import load_presets
from wormspy.sim.handle import SimRigHandle
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

GEOM = ImageGeometry(width_px=48, height_px=32, um_per_px=30.0)


def _app(tmp_path, clock_drops=(), speed=10.0, with_presets=False):
    stage = SimStage({a: (0.0, 1e7) for a in "xyz"}, 1000.0,
                     {"x": 1000.0, "y": 2000.0, "z": 3000.0})
    rig = SimRig(SyntheticWorm(seed=0), stage, ChannelSpec(GEOM, "fluorescence"),
                 ChannelSpec(GEOM, "behaviour"), fps=20.0, clock_drops=clock_drops)
    handle = SimRigHandle(rig, clock_name="gcamp", follower_name="bf", speed=speed)
    state = AppState()
    if with_presets:        # as the launcher starts: Default applied, saved presets loaded
        state = AppState(camera_settings=handle.settings, preset="Default",
                         presets=load_presets(tmp_path / "presets.json", handle.settings))
    store, hub = StateStore(state), FrameHub()
    executor = Executor(store, handle, hub, data_root=tmp_path / "data",
                        presets_path=tmp_path / "presets.json")
    return store, hub, executor, handle


def _wait(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def _record(store, executor, hub, frames=30):
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    _wait(lambda: store.state.session_dir is not None)
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    start = hub.latest("gcamp").device_frame_id
    _wait(lambda: hub.latest("gcamp").device_frame_id >= start + frames)
    store.dispatch(C.StopRecording())
    executor.wait_idle()
    return Path(store.state.session_dir) / "rec-001"


def _mkv_frames(path):
    cap, n = cv2.VideoCapture(str(path)), 0
    while cap.read()[0]:
        n += 1
    return n


def test_live_view_publishes_frames_to_the_hub(tmp_path):
    store, hub, executor, _ = _app(tmp_path)
    store.dispatch(C.StartLive())
    _wait(lambda: hub.latest("gcamp") is not None and hub.latest("bf") is not None)
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_new_session_creates_the_folder_and_opens_it(tmp_path):
    store, _, executor, _ = _app(tmp_path)
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    executor.wait_idle()
    session = Path(store.state.session_dir)
    assert session.parent == tmp_path / "data" and (session / "session.json").exists()


def test_a_recording_writes_aligned_files(tmp_path):
    store, hub, executor, _ = _app(tmp_path)
    rec = _record(store, executor, hub)
    store.dispatch(C.StopLive())
    executor.wait_idle()
    with open(rec / "frames.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    pages = tifffile.imread(rec / "gcamp.tif").shape[0]
    assert len(rows) >= 30
    assert pages == len(rows) == _mkv_frames(rec / "bf.mkv")
    indices = [int(r["frame_index"]) for r in rows]
    assert indices == list(range(indices[0], indices[0] + len(rows)))   # contiguous
    assert json.loads((rec / "recording.json").read_text())["frames"] == len(rows)


def test_camera_drops_reach_the_state_and_the_csv(tmp_path):
    store, hub, executor, _ = _app(tmp_path, clock_drops=set(range(0, 10_000, 7)))
    rec = _record(store, executor, hub)
    assert store.state.recording_drops > 0
    assert any(a.kind == "frames_dropped" for a in store.state.alarms)
    with open(rec / "frames.csv", newline="") as fh:
        assert any(r["gcamp_dropped"] == "1" for r in csv.DictReader(fh))
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_event_markers_are_written_into_the_recording(tmp_path):
    store, hub, executor, _ = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    _wait(lambda: store.state.session_dir is not None)
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    store.dispatch(C.MarkEvent(label="glycerol"))
    executor.wait_idle()
    start = hub.latest("gcamp").device_frame_id
    _wait(lambda: hub.latest("gcamp").device_frame_id >= start + 5)
    store.dispatch(C.StopRecording())
    store.dispatch(C.StopLive())
    executor.wait_idle()
    with open(Path(store.state.session_dir) / "rec-001" / "frames.csv", newline="") as fh:
        assert [r["event"] for r in csv.DictReader(fh) if r["event"]] == ["glycerol"]


def test_a_recording_that_cannot_open_returns_to_live_with_an_alarm(tmp_path):
    store, _, executor, _ = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    _wait(lambda: store.state.session_dir is not None)
    (Path(store.state.session_dir) / "rec-001").mkdir()      # folder already taken
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    assert store.state.mode is Mode.LIVE
    assert any(a.kind == "recording_failed" for a in store.state.alarms)
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_camera_failure_while_recording_finalises_the_files_and_goes_idle(tmp_path):
    store, hub, executor, handle = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    _wait(lambda: store.state.session_dir is not None)
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    _wait(lambda: hub.latest("gcamp") is not None)
    handle.fail_camera("USB disconnect")
    _wait(lambda: store.state.mode is Mode.IDLE)
    executor.wait_idle()
    assert any(a.kind == "acquisition_failed" for a in store.state.alarms)
    assert (Path(store.state.session_dir) / "rec-001" / "recording.json").exists()


def _csv(rec):
    with open(rec / "frames.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def test_recorded_rows_carry_the_stage_position_at_frame_time(tmp_path):
    store, hub, executor, _ = _app(tmp_path)
    rows = _csv(_record(store, executor, hub))
    store.dispatch(C.StopLive())
    executor.wait_idle()
    assert {(r["x_um"], r["y_um"], r["z_um"]) for r in rows} == {("1000.0", "2000.0", "3000.0")}
    assert {r["z_moved"] for r in rows} == {"0"}


def test_refocusing_during_a_recording_is_flagged_per_frame(tmp_path):
    store, hub, executor, handle = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.NewSession(metadata={"animal_id": "w1"}))
    _wait(lambda: store.state.session_dir is not None)
    store.dispatch(C.StartRecording())
    executor.wait_idle()
    _wait(lambda: hub.latest("gcamp") is not None)
    start = hub.latest("gcamp").device_frame_id
    _wait(lambda: hub.latest("gcamp").device_frame_id >= start + 10)
    handle.stage.move_velocity_um_s("z", 200.0)          # operator turns the focus stick
    _wait(lambda: hub.latest("gcamp").device_frame_id >= start + 20)
    handle.stage.stop("z")
    _wait(lambda: hub.latest("gcamp").device_frame_id >= start + 30)
    store.dispatch(C.StopRecording())
    store.dispatch(C.StopLive())
    executor.wait_idle()
    rows = _csv(Path(store.state.session_dir) / "rec-001")
    assert rows[0]["z_moved"] == "0" and rows[-1]["z_moved"] == "0"
    assert any(r["z_moved"] == "1" for r in rows)
    assert float(rows[-1]["z_um"]) > float(rows[0]["z_um"])


def test_set_focus_stores_z_and_return_to_focus_moves_back(tmp_path):
    store, _, executor, handle = _app(tmp_path)
    store.dispatch(C.StartLive())
    store.dispatch(C.SetFocus())
    executor.wait_idle()
    assert store.state.focus_z_um == 3000.0
    handle.stage.move_absolute_um("z", 3400.0)
    _wait(lambda: handle.stage.position_um("z") == 3400.0)
    store.dispatch(C.ReturnToFocus())
    executor.wait_idle()
    _wait(lambda: handle.stage.position_um("z") == 3000.0)
    store.dispatch(C.StopLive())
    executor.wait_idle()


def test_an_effect_without_a_handler_raises_a_visible_alarm(tmp_path):
    store, _, executor, _ = _app(tmp_path)

    class NotBuiltYet:
        pass
    executor._effects.put(NotBuiltYet())
    executor.wait_idle()
    assert any(a.kind == "not_implemented" for a in store.state.alarms)
    executor.close()


def _presets_app(tmp_path):
    return _app(tmp_path, with_presets=True)


def test_new_camera_settings_restart_the_live_view_with_the_new_rate_and_exposure(tmp_path):
    from dataclasses import replace
    store, hub, executor, handle = _presets_app(tmp_path)
    store.dispatch(C.StartLive())
    executor.wait_idle()
    _wait(lambda: hub.latest("gcamp") is not None)
    before = float(hub.latest("gcamp").image.mean())
    brighter = replace(handle.settings, fps=10.0,
                       clock_exposure_us=handle.settings.clock_exposure_us * 2)
    store.dispatch(C.ApplyCameraSettings(brighter))
    executor.wait_idle()
    assert store.state.camera_settings == brighter and store.state.preset is None
    assert store.state.mode is Mode.LIVE and handle.fps == 10.0
    start = hub.latest("gcamp").device_frame_id
    _wait(lambda: hub.latest("gcamp").device_frame_id > start + 2)
    assert float(hub.latest("gcamp").image.mean()) == pytest.approx(2 * before, rel=0.1)
    store.dispatch(C.StopLive())
    executor.wait_idle()
    executor.close()


def test_saved_presets_are_written_to_disk_and_the_session_records_the_settings(tmp_path):
    store, hub, executor, handle = _presets_app(tmp_path)
    store.dispatch(C.SavePreset(name="bright"))
    executor.wait_idle()
    assert [n for n, _ in load_presets(tmp_path / "presets.json", handle.settings)] == [
        "Default", "bright"]
    folder = _record(store, executor, hub, frames=3)
    session = json.loads((folder.parent / "session.json").read_text())
    assert session["config"]["camera"]["preset"] == "bright"
    recording = json.loads((folder / "recording.json").read_text())
    assert recording["camera"]["settings"]["fps"] == handle.settings.fps
    store.dispatch(C.StopLive())
    executor.wait_idle()
    executor.close()


def test_a_camera_that_rejects_the_settings_raises_an_alarm_and_the_view_comes_back(tmp_path):
    from dataclasses import replace
    store, hub, executor, handle = _presets_app(tmp_path)
    store.dispatch(C.StartLive())
    executor.wait_idle()

    def refuse(_settings):
        raise RuntimeError("ExposureTime is not writable")
    handle.apply_settings = refuse
    store.dispatch(C.ApplyCameraSettings(replace(handle.settings, clock_gain_db=3)))
    executor.wait_idle()
    assert any(a.kind == "camera_settings" for a in store.state.alarms)
    assert store.state.camera_settings == handle.settings
    start = hub.latest("gcamp").device_frame_id if hub.latest("gcamp") else -1
    _wait(lambda: hub.latest("gcamp") is not None and hub.latest("gcamp").device_frame_id != start)
    store.dispatch(C.StopLive())
    executor.wait_idle()
    executor.close()


def test_pairs_captured_a_frame_apart_raise_a_pairing_alarm_once(tmp_path):
    from wormspy.acquisition.pairing import FramePair
    from wormspy.core.frames import Frame
    store, _, executor, _ = _app(tmp_path)          # 20 fps: 50 ms period
    frame = lambda ts: Frame(image=None, device_frame_id=0, capture_ts=ts)
    executor._on_pair(FramePair(0, frame(1.000), frame(1.004), 0))       # trigger latency: fine
    assert not any(a.kind == "pairing_suspect" for a in store.state.alarms)
    executor._on_pair(FramePair(1, frame(2.000), frame(2.050), 0))       # one frame off
    executor._on_pair(FramePair(2, frame(3.000), frame(3.050), 0))
    alarms = [a for a in store.state.alarms if a.kind == "pairing_suspect"]
    assert len(alarms) == 1 and "50 ms apart" in alarms[0].message
    executor.close()
