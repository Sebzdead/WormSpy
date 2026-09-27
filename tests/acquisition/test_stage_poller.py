"""StagePoller and StageTelemetry: stage position at frame time, without serial I/O on the
acquisition or tracking paths (ADR 0007/0008)."""

import time

import numpy as np
import pytest

from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.stage_poller import StagePoller, StageTelemetry
from wormspy.core.frames import Frame
from wormspy.sim.stage import SimStage


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _stage():
    return SimStage({a: (0.0, 50_000.0) for a in "xyz"}, max_speed_um_s=1000.0,
                    positions_um={"x": 100.0, "y": 200.0, "z": 300.0})


def test_latest_sample_holds_all_axes_and_its_time():
    clock = Clock()
    poller = StagePoller(_stage(), clock)
    assert poller.latest() is None
    clock.t = 1.5
    poller.sample_once()
    s = poller.latest()
    assert (s.t, s.x_um, s.y_um, s.z_um) == (1.5, 100.0, 200.0, 300.0)


def test_position_at_a_time_is_interpolated_between_samples():
    clock, stage = Clock(), _stage()
    poller = StagePoller(stage, clock)
    poller.sample_once()                      # t=0, x=100
    stage.move_velocity_um_s("x", 100.0)
    stage.advance(1.0)
    clock.t = 1.0
    poller.sample_once()                      # t=1, x=200
    assert poller.at(0.25).x_um == pytest.approx(125.0)


def test_times_outside_the_history_clamp_to_the_nearest_sample():
    clock = Clock()
    poller = StagePoller(_stage(), clock)
    clock.t = 5.0
    poller.sample_once()
    assert poller.at(1.0).t == 5.0 and poller.at(9.0).t == 5.0


def test_history_is_bounded():
    clock = Clock()
    poller = StagePoller(_stage(), clock, rate_hz=10.0, history_s=1.0)
    for i in range(50):
        clock.t = i / 10
        poller.sample_once()
    assert len(poller) == 10


def test_the_thread_samples_at_roughly_the_poll_rate():
    poller = StagePoller(_stage(), time.monotonic, rate_hz=100.0)
    poller.start()
    time.sleep(0.3)
    poller.stop()
    assert 15 <= len(poller) <= 45


def _pair(t):
    frame = Frame(np.zeros((1, 1), np.uint16), 0, capture_ts=t)
    return FramePair(0, clock=frame, follower=None, skew_ns=None)


def test_telemetry_reports_position_at_the_frame_time():
    clock, stage = Clock(), _stage()
    poller = StagePoller(stage, clock)
    poller.sample_once()
    stage.move_velocity_um_s("y", 400.0)
    stage.advance(1.0)
    clock.t = 1.0
    poller.sample_once()
    row = StageTelemetry(poller, focus_z_um=lambda: 280.0)(_pair(0.5))
    assert row["x_um"] == pytest.approx(100.0) and row["y_um"] == pytest.approx(400.0)
    assert row["z_um"] == pytest.approx(300.0) and row["focus_set_z_um"] == 280.0


def test_z_moved_flags_frames_where_focus_changed_since_the_previous_frame():
    clock, stage = Clock(), _stage()
    poller = StagePoller(stage, clock)
    telemetry = StageTelemetry(poller, focus_z_um=lambda: None)
    poller.sample_once()
    assert telemetry(_pair(0.0))["z_moved"] is False          # first frame: nothing to compare
    clock.t = 0.1
    poller.sample_once()
    assert telemetry(_pair(0.1))["z_moved"] is False          # z unchanged
    stage.move_velocity_um_s("z", 50.0)
    stage.advance(0.1)
    clock.t = 0.2
    poller.sample_once()
    assert telemetry(_pair(0.2))["z_moved"] is True           # 5 µm focus change


def test_telemetry_is_blank_before_the_first_sample():
    poller = StagePoller(_stage(), Clock())
    row = StageTelemetry(poller, focus_z_um=lambda: None)(_pair(0.0))
    assert "x_um" not in row and row["focus_set_z_um"] is None
