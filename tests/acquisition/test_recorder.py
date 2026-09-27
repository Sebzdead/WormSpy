"""Recorder: bounded-queue worker that writes a Recording (ADR 0006/0007)."""

import csv
import json
import threading
import time

import cv2
import numpy as np
import tifffile

from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.recorder import ChannelConfig, Recorder
from wormspy.control import events as E
from wormspy.core.frames import Frame

SHAPE = (20, 30)
CLOCK = ChannelConfig(name="gcamp", role="fluorescence", shape=SHAPE)
FOLLOWER = ChannelConfig(name="bf", role="behaviour", shape=SHAPE)


def _pair(index, clock=True, follower=True):
    def frame(dtype, value):
        return Frame(image=np.full(SHAPE, value, dtype), device_frame_id=index,
                     capture_ts=index / 10, device_timestamp_ns=index * 100_000_000)
    return FramePair(frame_index=index,
                     clock=frame(np.uint16, 1000 + index) if clock else None,
                     follower=frame(np.uint8, index % 256) if follower else None,
                     skew_ns=0 if clock and follower else None)


def _recorder(tmp_path, events=None, **kw):
    events = events if events is not None else []
    rec = Recorder(tmp_path / "rec-001", CLOCK, FOLLOWER, fps=10.0, on_event=events.append, **kw)
    rec.start()
    return rec


def _rows(tmp_path):
    with open(tmp_path / "rec-001" / "frames.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def _mkv_frames(path):
    cap, n = cv2.VideoCapture(str(path)), 0
    while cap.read()[0]:
        n += 1
    return n


def test_every_submitted_pair_is_written_to_both_files_and_the_csv(tmp_path):
    rec = _recorder(tmp_path)
    for i in range(100, 108):
        assert rec.submit(_pair(i))
    rec.stop()
    stack = tifffile.imread(tmp_path / "rec-001" / "gcamp.tif")
    assert stack.shape == (8, *SHAPE) and stack[0][0, 0] == 1100
    assert _mkv_frames(tmp_path / "rec-001" / "bf.mkv") == 8
    assert [int(r["frame_index"]) for r in _rows(tmp_path)] == list(range(100, 108))


def test_camera_drops_become_placeholders_and_are_reported(tmp_path):
    events = []
    rec = _recorder(tmp_path, events)
    rec.submit(_pair(0))
    rec.submit(_pair(1, clock=False))
    rec.submit(_pair(2))
    rec.stop()
    stack = tifffile.imread(tmp_path / "rec-001" / "gcamp.tif")
    assert stack.shape[0] == 3 and stack[1].max() == 0
    rows = _rows(tmp_path)
    assert rows[1]["gcamp_dropped"] == "1" and rows[1]["bf_dropped"] == "0"
    assert E.FramesDropped(channel="gcamp", count=1) in events


def test_backlog_drops_keep_files_aligned_with_the_csv(tmp_path):
    events, release = [], threading.Event()

    def slow_telemetry(pair):          # holds the writer thread on the first pair
        release.wait(timeout=5)
        return {}

    rec = _recorder(tmp_path, events, queue_size=1, telemetry=slow_telemetry)
    accepted = [rec.submit(_pair(i)) for i in range(6)]
    release.set()
    while not rec.submit(_pair(6)):   # wait until the writer has caught up
        time.sleep(0.01)
    rec.stop()
    assert not all(accepted)                              # the queue refused some pairs
    rows = _rows(tmp_path)
    assert [int(r["frame_index"]) for r in rows] == list(range(7))   # no missing rows
    skipped = accepted.count(False)
    assert sum(r["gcamp_dropped"] == "1" and r["bf_dropped"] == "1" for r in rows) == skipped
    assert tifffile.imread(tmp_path / "rec-001" / "gcamp.tif").shape[0] == 7
    backlog = [e.count for e in events
               if isinstance(e, E.FramesDropped) and e.channel == "writer backlog"]
    assert sum(backlog) == skipped      # may arrive as several gaps, depending on timing


def test_an_event_marker_lands_on_the_first_frame_after_the_latest_submitted_one(tmp_path):
    rec = _recorder(tmp_path)
    rec.submit(_pair(0))
    rec.mark_event("glycerol")
    rec.submit(_pair(1))
    rec.stop()
    rows = _rows(tmp_path)
    assert [r["event"] for r in rows] == ["", "glycerol"]


def test_telemetry_is_written_into_each_row(tmp_path):
    rec = _recorder(tmp_path, telemetry=lambda pair: {"x_um": pair.frame_index * 1.5})
    rec.submit(_pair(4))
    rec.stop()
    assert _rows(tmp_path)[0]["x_um"] == "6.0"


def test_a_writer_failure_reports_recording_failed_and_stops_writing(tmp_path):
    events = []
    rec = _recorder(tmp_path, events)
    bad = FramePair(0, clock=Frame(np.zeros((5, 5), np.uint16), 0, 0.0), follower=None,
                    skew_ns=None)
    rec.submit(bad)
    rec.stop()
    assert any(isinstance(e, E.RecordingFailed) for e in events)


def test_recording_json_summarises_the_recording(tmp_path):
    rec = _recorder(tmp_path)
    rec.submit(_pair(10))
    rec.submit(_pair(11, follower=False))
    rec.stop()
    summary = json.loads((tmp_path / "rec-001" / "recording.json").read_text())
    assert summary["frames"] == 2
    assert summary["first_frame_index"] == 10 and summary["last_frame_index"] == 11
    assert summary["dropped"] == {"gcamp": 0, "bf": 1, "writer backlog": 0}
    assert summary["channels"]["gcamp"] == {"role": "fluorescence", "file": "gcamp.tif"}
    assert summary["channels"]["bf"] == {"role": "behaviour", "file": "bf.mkv"}


def test_simultaneous_event_markers_share_a_row(tmp_path):
    rec = _recorder(tmp_path)
    rec.mark_event("a")
    rec.mark_event("b")
    rec.submit(_pair(0))
    rec.stop()
    assert _rows(tmp_path)[0]["event"] == "a;b"
