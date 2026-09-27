"""Channel writers (BigTIFF, FFV1) and the per-frame CSV (ADR 0007)."""

import csv

import cv2
import numpy as np
import pytest
import tifffile

from wormspy.acquisition.writers import (
    CSV_TELEMETRY_COLUMNS,
    Ffv1Writer,
    FramesCsvWriter,
    TiffStackWriter,
    open_channel_writer,
)
from wormspy.core.frames import Frame

SHAPE = (30, 40)


def _u16(v):
    return np.full(SHAPE, v, np.uint16)


def _read_mkv(path):
    cap, frames = cv2.VideoCapture(str(path)), []
    while True:
        ok, f = cap.read()
        if not ok:
            return frames
        frames.append(f if f.ndim == 2 else f[..., 0])


# --- fluorescence: raw uint16 BigTIFF -------------------------------------------------------

def test_tiff_writer_stores_raw_16bit_pages_losslessly(tmp_path):
    path = tmp_path / "gcamp.tif"
    rng = np.random.default_rng(0)
    images = [rng.integers(0, 65536, SHAPE, dtype=np.uint16) for _ in range(5)]
    with TiffStackWriter(path, SHAPE) as w:
        for img in images:
            w.write(img)
    stack = tifffile.imread(path)
    assert stack.dtype == np.uint16 and stack.shape == (5, *SHAPE)
    np.testing.assert_array_equal(stack, np.stack(images))


def test_tiff_writer_is_bigtiff(tmp_path):
    path = tmp_path / "gcamp.tif"
    with TiffStackWriter(path, SHAPE) as w:
        w.write(_u16(1))
    with tifffile.TiffFile(path) as tif:
        assert tif.is_bigtiff


def test_a_dropped_frame_becomes_a_zero_placeholder_page(tmp_path):
    path = tmp_path / "gcamp.tif"
    with TiffStackWriter(path, SHAPE) as w:
        w.write(_u16(7))
        w.write(None)
        w.write(_u16(9))
    stack = tifffile.imread(path)
    assert stack.shape[0] == 3
    assert stack[1].max() == 0 and stack[2][0, 0] == 9


def test_tiff_writer_rejects_frames_of_the_wrong_type_or_shape(tmp_path):
    with TiffStackWriter(tmp_path / "gcamp.tif", SHAPE) as w:
        with pytest.raises(ValueError):
            w.write(np.zeros(SHAPE, np.uint8))           # 8-bit would silently lose data
        with pytest.raises(ValueError):
            w.write(np.zeros((10, 10), np.uint16))


# --- behaviour: FFV1 lossless video -----------------------------------------------------------

def test_ffv1_writer_stores_8bit_frames_losslessly(tmp_path):
    path = tmp_path / "brightfield.mkv"
    rng = np.random.default_rng(1)
    images = [rng.integers(0, 256, SHAPE, dtype=np.uint8) for _ in range(6)]
    with Ffv1Writer(path, SHAPE, fps=10.0) as w:
        for img in images:
            w.write(img)
        w.write(None)
    back = _read_mkv(path)
    assert len(back) == 7
    for a, b in zip(images, back):
        np.testing.assert_array_equal(a, b)
    assert back[6].max() == 0


def test_ffv1_writer_rejects_16bit_frames(tmp_path):
    with Ffv1Writer(tmp_path / "brightfield.mkv", SHAPE, fps=10.0) as w, \
            pytest.raises(ValueError):
        w.write(np.zeros(SHAPE, np.uint16))


# --- role decides the format ------------------------------------------------------------------

def test_channel_role_selects_the_file_format(tmp_path):
    with open_channel_writer("fluorescence", tmp_path / "gcamp", SHAPE, fps=10.0) as w:
        assert isinstance(w, TiffStackWriter) and w.path.name == "gcamp.tif"
    with open_channel_writer("behaviour", tmp_path / "bf", SHAPE, fps=10.0) as w:
        assert isinstance(w, Ffv1Writer) and w.path.name == "bf.mkv"


# --- frames.csv -------------------------------------------------------------------------------

def _frame(fid, ts):
    return Frame(image=np.zeros((1, 1), np.uint16), device_frame_id=fid, capture_ts=0.0,
                 device_timestamp_ns=ts)


def _rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_csv_has_per_channel_and_telemetry_columns(tmp_path):
    path = tmp_path / "frames.csv"
    with FramesCsvWriter(path, channels=("gcamp", "bf")):
        pass
    with open(path) as fh:
        header = fh.readline().strip().split(",")
    assert header[:2] == ["frame_index", "host_time"]
    assert header[2:9] == ["gcamp_frame_id", "gcamp_timestamp_ns", "gcamp_dropped",
                           "bf_frame_id", "bf_timestamp_ns", "bf_dropped", "pair_skew_ns"]
    assert header[9:] == list(CSV_TELEMETRY_COLUMNS)
    for column in ("x_um", "worm_x_um", "tracking_state", "z_moved", "event", "stim_state"):
        assert column in header


def test_csv_rows_record_frames_drops_and_telemetry(tmp_path):
    path = tmp_path / "frames.csv"
    with FramesCsvWriter(path, channels=("gcamp", "bf")) as w:
        w.write_row(0, host_time=1.25, frames={"gcamp": _frame(10, 1000), "bf": _frame(10, 1150)},
                    skew_ns=0, telemetry={"x_um": 25000.5, "tracking_state": "tracking"})
        w.write_row(1, host_time=1.35, frames={"gcamp": None, "bf": _frame(11, 2150)},
                    skew_ns=None, telemetry={"event": "glycerol"})
    rows = _rows(path)
    assert rows[0]["gcamp_frame_id"] == "10" and rows[0]["gcamp_dropped"] == "0"
    assert rows[0]["x_um"] == "25000.5" and rows[0]["tracking_state"] == "tracking"
    assert rows[1]["gcamp_dropped"] == "1" and rows[1]["gcamp_frame_id"] == ""
    assert rows[1]["pair_skew_ns"] == "" and rows[1]["event"] == "glycerol"
    assert rows[1]["x_um"] == ""        # telemetry not reported this frame -> blank


def test_csv_rejects_unknown_telemetry_columns(tmp_path):
    with FramesCsvWriter(tmp_path / "frames.csv", channels=("a", "b")) as w, \
            pytest.raises(KeyError):
        w.write_row(0, host_time=0.0, frames={"a": None, "b": None}, skew_ns=None,
                    telemetry={"x_position": 1.0})


def test_csv_rows_reach_disk_while_the_recording_is_still_open(tmp_path):
    path = tmp_path / "frames.csv"
    w = FramesCsvWriter(path, channels=("a", "b"), flush_every=5)
    for i in range(5):
        w.write_row(i, host_time=float(i), frames={"a": None, "b": None}, skew_ns=None)
    assert len(_rows(path)) == 5      # a crash now would lose nothing
    w.close()
