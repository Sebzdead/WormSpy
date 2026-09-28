"""wormspy.reader against recordings written by the real Recorder (ADR 0007).

Every frame's pixels encode its frame_index, so any misalignment between frames.csv and the
channel files shows up as a wrong pixel value.
"""

import json

import numpy as np
import pytest

from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.recorder import ChannelConfig, Recorder
from wormspy.acquisition.session import create_session
from wormspy.calibration.math import CameraCalibration
from wormspy.calibration.routine import RigCalibration
from wormspy.core.frames import Frame
from wormspy.reader import open_recording, open_session
from wormspy.sim.render import ImageGeometry

GCAMP = ChannelConfig("gcamp", "fluorescence", (24, 32))
BF = ChannelConfig("brightfield", "behaviour", (20, 30))
FLUO_CAM = ImageGeometry(width_px=32, height_px=24, um_per_px=2.0, rotation_deg=3.0)
BF_CAM = ImageGeometry(width_px=30, height_px=20, um_per_px=2.5, flip_x=True,
                       offset_px=(1.5, -2.0))


def _calibration():
    return RigCalibration(reference="gcamp", cameras={
        "gcamp": CameraCalibration(FLUO_CAM.matrix, 0.1, (24, 32)),
        "brightfield": CameraCalibration(BF_CAM.matrix, 0.1, (20, 30),
                                         origin_px=tuple(BF_CAM.origin_px))})


def _frame(channel, index, dtype):
    value = 1000 + index if dtype == np.uint16 else index % 250 + 1
    return Frame(np.full(channel.shape, value, dtype), device_frame_id=index,
                 capture_ts=100.0 + index / 10, device_timestamp_ns=index * 100_000_000)


def _record(tmp_path, first=50, count=12, drop_clock=(), drop_follower=(), events=(),
            tracked=()):
    session = create_session(tmp_path / "data", {"animal_id": "N2 #3"},
                             {"fps": 10.0, "calibration": _calibration().to_json(),
                              "channels": {"gcamp": {"role": "fluorescence"},
                                           "brightfield": {"role": "behaviour"}}})
    stage = {}
    recorder = Recorder(session / "rec-001", GCAMP, BF, fps=10.0, on_event=lambda _: None,
                        telemetry=lambda pair: stage.get(pair.frame_index, {}),
                        extra={"camera": {"preset": "Default"}})
    recorder.start()
    for index in range(first, first + count):
        stage[index] = {"x_um": 1000.0 + index, "y_um": 2000.0, "z_um": 500.0}
        if index in events:
            recorder.mark_event(events[index])
        clock = None if index in drop_clock else _frame(GCAMP, index, np.uint16)
        follower = None if index in drop_follower else _frame(BF, index, np.uint8)
        recorder.submit(FramePair(index, clock, follower, 0 if clock and follower else None))
        if index in tracked:
            recorder.log_tracking(frame_index=index, source_channel="brightfield",
                                  frame_id=index, capture_ts=100.0 + index / 10,
                                  estimator="dark_object", target_px=(15.0, 10.0),
                                  worm_um=(1000.0 + index, 2000.0), accepted=True,
                                  status="tracking")
    recorder.stop()
    return session / "rec-001"


def test_frames_are_indexed_by_frame_index_and_images_line_up(tmp_path):
    rec = open_recording(_record(tmp_path, first=50, count=12))
    assert list(rec.index) == list(range(50, 62))
    assert rec.complete and rec.warnings == []
    assert rec["gcamp"][55].dtype == np.uint16 and int(rec["gcamp"][55][0, 0]) == 1055
    assert rec["brightfield"][55].shape == (20, 30) and int(rec["brightfield"][55][0, 0]) == 56
    assert rec.frames.loc[55, "x_um"] == 1055.0
    with pytest.raises(IndexError):
        rec["gcamp"][49]
    rec.close()


def test_whole_channels_load_as_arrays_the_tiff_memory_mapped(tmp_path):
    with open_recording(_record(tmp_path, count=8)) as rec:
        calcium = rec["gcamp"].array()
        assert isinstance(calcium, np.memmap) and calcium.shape == (8, 24, 32)
        assert list(calcium[:, 0, 0]) == [1050 + i for i in range(8)]
        behaviour = rec["brightfield"].array()
        assert list(behaviour[:, 0, 0]) == [(50 + i) % 250 + 1 for i in range(8)]


def test_random_access_into_the_video_seeks_exactly(tmp_path):
    with open_recording(_record(tmp_path, count=10)) as rec:
        bf = rec["brightfield"]
        assert [int(bf[i][0, 0]) for i in (58, 52, 59, 51)] == [59, 53, 60, 52]


def test_dropped_frames_are_zero_filled_and_marked_invalid(tmp_path):
    with open_recording(_record(tmp_path, drop_clock={53}, drop_follower={56})) as rec:
        assert not rec["gcamp"][53].any()                    # placeholder
        assert int(rec["gcamp"][54][0, 0]) == 1054           # alignment survives the drop
        invalid = [int(i) for i in rec.valid[~rec.valid].index]
        assert invalid == [53, 56]


def test_events_and_tracking_are_joined_by_frame_index(tmp_path):
    rec = open_recording(_record(tmp_path, events={52: "glycerol", 58: "wash"},
                                 tracked={51, 52, 53}))
    assert [label for _, label in rec.events] == ["glycerol", "wash"]
    assert rec.events[0][0] >= 52
    assert rec.frames.loc[52, "track_status"] == "tracking"
    assert np.isnan(rec.frames.loc[57, "track_target_px_x"])        # not tracked then
    assert len(rec.tracking) == 3
    rec.close()


def test_metadata_camera_settings_and_time(tmp_path):
    rec = open_recording(_record(tmp_path))
    assert rec.session["metadata"]["animal_id"] == "N2 #3"
    assert rec.camera == {"preset": "Default"} and rec.fps == 10.0
    assert rec.time_s.iloc[3] == pytest.approx(0.3)
    rec.close()


def test_pixels_map_to_plate_positions_through_the_calibration(tmp_path):
    rec = open_recording(_record(tmp_path))
    stage = np.array([1052.0, 2000.0])
    world = stage + (6.0, -4.0)
    for channel, geom in (("gcamp", FLUO_CAM), ("brightfield", BF_CAM)):
        px = geom.world_to_px(world[None, :], stage)[0]
        assert rec.plate_um(channel, px, 52) == pytest.approx(tuple(world), abs=1e-6)
    rec.close()


def test_a_recording_cut_short_still_opens_with_the_frames_every_file_has(tmp_path):
    path = _record(tmp_path, count=10)
    (path / "recording.json").unlink()
    with (path / "frames.csv").open("a") as csv:        # rows written, frames never flushed
        csv.write("60,101.0,60,0,0,60,0,0,0,,,,,,,,\n61,101.1,61,0,0,61,0,0,0,,,,,,,,\n")
    rec = open_recording(path)
    assert not rec.complete and len(rec.index) == 10
    assert int(rec["gcamp"][59][0, 0]) == 1059
    assert any("cut short" in w for w in rec.warnings)
    assert any("different numbers of frames" in w for w in rec.warnings)
    rec.close()


def test_a_session_lists_and_opens_its_recordings(tmp_path):
    path = _record(tmp_path)
    session = open_session(path.parent)
    assert session.metadata["animal_id"] == "N2 #3"
    assert [p.name for p in session.recordings] == ["rec-001"]
    with session.open(1) as rec:
        assert len(rec.index) == 12


def test_a_folder_that_is_not_a_recording_is_explained(tmp_path):
    with pytest.raises(FileNotFoundError, match="not a WormsPy recording"):
        open_recording(tmp_path)
    with pytest.raises(FileNotFoundError, match="not a WormsPy session"):
        open_session(tmp_path)


def test_the_recording_json_is_the_source_of_channel_files(tmp_path):
    path = _record(tmp_path)
    info = json.loads((path / "recording.json").read_text())
    assert info["channels"]["gcamp"]["file"] == "gcamp.tif"
