import numpy as np
from core.frames import FrameHub, Frame
from core.stage import FakeStage
from core.tracking import find_worm_cms, simple_to_center, Tracker


def test_find_worm_cms_returns_centroid_of_largest_blob():
    # 40x40 frame, a bright square blob centered near (10, 20) in (row, col)
    frame = np.zeros((40, 40), np.uint8)
    frame[8:13, 18:23] = 255       # rows 8-12, cols 18-22 -> centroid ~ (10, 20)
    x, y = find_worm_cms(frame, factor=1, initial_coords=(20, 20))
    # returned as (x=col, y=row)
    assert abs(x - 20) <= 1
    assert abs(y - 10) <= 1


def test_find_worm_cms_applies_downsample_factor():
    frame = np.zeros((20, 20), np.uint8)
    frame[9:11, 9:11] = 255        # centroid ~ (9.5, 9.5)
    x, y = find_worm_cms(frame, factor=2, initial_coords=(10, 10))
    assert abs(x - 19) <= 2        # ~9.5 * 2
    assert abs(y - 19) <= 2


def test_find_worm_cms_falls_back_to_initial_when_empty():
    frame = np.zeros((10, 10), np.uint8)
    assert find_worm_cms(frame, factor=1, initial_coords=(5, 5)) == (5, 5)


def test_simple_to_center_centered_worm_needs_no_move():
    mx, my = simple_to_center(960, 600, resolution=(1920, 1200),
                              total_mm_x=1.92, total_mm_y=1.2,
                              orient_x=1, orient_y=-1)
    assert abs(mx) < 1e-9
    assert abs(my) < 1e-9


def test_simple_to_center_respects_orientation_sign():
    mx, _ = simple_to_center(1920, 600, resolution=(1920, 1200),
                             total_mm_x=1.92, total_mm_y=1.2,
                             orient_x=1, orient_y=-1)
    assert mx > 0
    mx_inv, _ = simple_to_center(1920, 600, resolution=(1920, 1200),
                                 total_mm_x=1.92, total_mm_y=1.2,
                                 orient_x=-1, orient_y=-1)
    assert mx_inv < 0


def test_tracker_step_does_nothing_when_disabled():
    hub = FrameHub()
    hub.publish("left", Frame(np.zeros((40, 40), np.uint8), 0, 0.0))
    stage = FakeStage(limits={"x": (0.0, 1e9), "y": (0.0, 1e9)},
                      positions={"x": 100.0, "y": 100.0})
    tracker = Tracker(hub, stage, enabled=lambda: False, mode=lambda: 0,
                      compute=lambda frame, mode: (5, 5))
    tracker.step()
    assert stage.get_position("x") == 100.0  # unchanged


def test_tracker_step_commands_stage_when_enabled():
    hub = FrameHub()
    hub.publish("left", Frame(np.zeros((40, 40), np.uint8), 0, 0.0))
    stage = FakeStage(limits={"x": (-1e9, 1e9), "y": (-1e9, 1e9)},
                      positions={"x": 0.0, "y": 0.0})
    moves = []
    tracker = Tracker(hub, stage, enabled=lambda: True, mode=lambda: 0,
                      compute=lambda frame, mode: (123.0, -45.0),
                      on_move=lambda dx, dy, hit: moves.append((dx, dy)))
    tracker.step()
    assert moves == [(123.0, -45.0)]
    assert stage.get_position("x") == 123.0
    assert stage.get_position("y") == -45.0


def test_tracker_step_skips_when_no_frame_available():
    hub = FrameHub()  # nothing published
    stage = FakeStage(limits={"x": (0.0, 10.0), "y": (0.0, 10.0)},
                      positions={"x": 1.0, "y": 1.0})
    tracker = Tracker(hub, stage, enabled=lambda: True, mode=lambda: 0,
                      compute=lambda frame, mode: (5, 5))
    tracker.step()  # must not raise
    assert stage.get_position("x") == 1.0
