"""wormspy.analysis.segmentation on recordings written by the real Recorder.

The calcium channel holds a small Gaussian "neuron" on a noisy background; its position and
brightness are known per frame, so the found centroid and the signal can be checked.
"""

import numpy as np
import pandas as pd
import pytest

from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.recorder import ChannelConfig, Recorder
from wormspy.acquisition.session import create_session
from wormspy.analysis.segmentation import Candidate, JumpGate, Params, main, measure, segment
from wormspy.calibration.math import CameraCalibration
from wormspy.calibration.routine import RigCalibration
from wormspy.core.frames import Frame
from wormspy.reader import open_recording

SHAPE = (80, 120)
GCAMP = ChannelConfig("gcamp", "fluorescence", SHAPE)
BF = ChannelConfig("brightfield", "behaviour", SHAPE)
MATRIX = np.array([[0.5, 0.0], [0.0, 0.5]])            # 2 µm/px, no rotation


def _blob(x, y, amplitude, sigma=1.6):
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    return amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


def _record(tmp_path, scene, count=20, first=100, drop=()):
    """`scene(i)` -> list of (x, y, amplitude) blobs for the i-th frame of the recording."""
    calibration = RigCalibration(reference="gcamp", cameras={
        "gcamp": CameraCalibration(MATRIX, 0.1, SHAPE)})
    session = create_session(tmp_path / "data", {"animal_id": "RIA #1"},
                             {"fps": 10.0, "calibration": calibration.to_json(),
                              "channels": {"gcamp": {"role": "fluorescence"},
                                           "brightfield": {"role": "behaviour"}}})
    rng = np.random.default_rng(0)
    recorder = Recorder(session / "rec-001", GCAMP, BF, fps=10.0, on_event=lambda _: None,
                        telemetry=lambda pair: {"x_um": 5000.0, "y_um": 7000.0, "z_um": 0.0})
    recorder.start()
    for i in range(count):
        index = first + i
        image = 500 + rng.normal(0, 15, SHAPE)
        for x, y, amplitude in scene(i):
            image += _blob(x, y, amplitude)
        clock = None if i in drop else Frame(image.astype(np.uint16), device_frame_id=index,
                                             capture_ts=10.0 + i / 10,
                                             device_timestamp_ns=i * 100_000_000)
        follower = Frame(np.full(SHAPE, 80, np.uint8), device_frame_id=index,
                         capture_ts=10.0 + i / 10, device_timestamp_ns=i * 100_000_000)
        recorder.submit(FramePair(index, clock, follower, 0 if clock else None))
    recorder.stop()
    return session / "rec-001"


def _drifting(i):
    """A neuron drifting 1 px per frame, with a calcium transient peaking at frame 10."""
    return [(40 + i, 40, 1500 + 1500 * np.exp(-((i - 10) / 3) ** 2))]


def test_the_neuron_is_followed_and_its_transient_measured(tmp_path):
    with open_recording(_record(tmp_path, _drifting)) as rec:
        table = segment(rec)
    assert list(table.index) == list(range(100, 120))
    assert table["centroid_x_px"].to_numpy() == pytest.approx(40 + np.arange(20), abs=0.5)
    assert table["centroid_y_px"].to_numpy() == pytest.approx(np.full(20, 40.0), abs=0.5)
    signal = table["top25_mean_bgsub"]
    assert signal.idxmax() == pytest.approx(110, abs=1)
    assert signal.loc[110] > 1.5 * signal.loc[100]
    assert table["background"].to_numpy() == pytest.approx(np.full(20, 500.0), abs=10)
    assert table["time_s"].iloc[5] == pytest.approx(0.5)


def test_dropped_frames_are_skipped_not_segmented(tmp_path):
    with open_recording(_record(tmp_path, _drifting, drop={4})) as rec:
        table = segment(rec)
    assert not table.loc[104, "valid"]
    assert table.loc[104, ["centroid_x_px", "top25_mean"]].isna().all()
    assert table.loc[105, "centroid_x_px"] == pytest.approx(45, abs=0.5)


def test_a_brighter_distractor_far_away_does_not_steal_the_neuron(tmp_path):
    def scene(i):                            # larger, brighter "gut" appears at frame 5
        return [(30, 30, 2000)] + ([(100, 60, 4000)] if i >= 5 else [])

    with open_recording(_record(tmp_path, scene)) as rec:
        table = segment(rec, params=Params(max_area_px=400))
    assert table["centroid_x_px"].to_numpy() == pytest.approx(np.full(20, 30.0), abs=0.5)


def test_the_plate_position_of_the_neuron_comes_from_the_calibration(tmp_path):
    with open_recording(_record(tmp_path, lambda i: [(80, 50, 2000)])) as rec:
        table = segment(rec)
    # the stage position is the image centre (60, 40); 2 µm per pixel
    assert table["neuron_x_um"].to_numpy() == pytest.approx(np.full(20, 5040.0), abs=1.0)
    assert table["neuron_y_um"].to_numpy() == pytest.approx(np.full(20, 7020.0), abs=1.0)


def test_nothing_to_find_gives_nan_not_a_warning(tmp_path):
    with open_recording(_record(tmp_path, lambda i: [], count=5)) as rec:
        table = segment(rec, params=Params(offset=-40))
    assert table["top25_mean"].isna().all()
    assert table["valid"].all()


def _candidate(x, y, area):
    return Candidate((x, y), area, np.zeros((0, 1, 2), np.int32))


def test_the_jump_gate_keeps_identity_and_reacquires_a_lost_neuron():
    gate = JumpGate(Params(max_jump_px=10, reacquire_after=3))
    assert gate.choose([_candidate(10, 10, 20)]).centroid == (10, 10)
    near, far = _candidate(15, 10, 10), _candidate(80, 80, 50)
    assert gate.choose([far, near]) is near              # larger but too far: ignored
    for _ in range(3):                                   # the neuron has moved away
        assert gate.choose([far]) is None
    assert gate.choose([far]) is far                     # gate reset: take the largest again


def test_dropped_frames_count_towards_reacquiring():
    gate = JumpGate(Params(reacquire_after=2))
    gate.choose([_candidate(10, 10, 20)])
    gate.skip()
    gate.skip()
    assert gate.choose([_candidate(90, 90, 20)]) is not None


def test_measure_handles_a_neuron_at_the_image_edge():
    image = np.full((40, 40), 100, np.uint16)
    image[0:4, 0:4] = 1000
    values = measure(image, (1.0, 1.0))
    assert values["top10_mean"] == 1000.0 and values["background"] == 100.0


def test_the_command_line_writes_a_table_and_an_overlay(tmp_path):
    path = _record(tmp_path, _drifting, count=6)
    assert main([str(path.parent), "--overlay", "--max-jump-px", "12"]) == 0
    table = pd.read_csv(path / "analysis" / "gcamp_segmentation.csv", index_col="frame_index")
    assert len(table) == 6 and table["centroid_x_px"].notna().all()
    assert (path / "analysis" / "gcamp_segmentation.mp4").stat().st_size > 0
