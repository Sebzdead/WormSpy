import numpy as np
from core.cameras import FakeCamera
from core.frames import FrameHub
from core.acquisition import AcquisitionEngine


def _imgs(n, base=0):
    return [np.full((2, 2), (base + i) % 256, np.uint8) for i in range(n)]


def test_run_once_assigns_contiguous_monotonic_indices():
    left = FakeCamera(_imgs(3, base=0))
    right = FakeCamera(_imgs(3, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    indices = []
    for _ in range(3):
        pair = engine.run_once()
        indices.append(pair.frame_index)
    assert indices == [0, 1, 2]


def test_run_once_pairs_left_and_right_per_tick():
    left = FakeCamera(_imgs(2, base=0))
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    pair = engine.run_once()
    assert int(pair.left.image[0, 0]) == 0
    assert int(pair.right.image[0, 0]) == 100


def test_run_once_publishes_latest_to_hub():
    hub = FrameHub()
    left = FakeCamera(_imgs(1, base=5))
    right = FakeCamera(_imgs(1, base=50))
    engine = AcquisitionEngine(left, right, hub)
    engine.run_once()
    assert int(hub.latest("left").image[0, 0]) == 5
    assert int(hub.latest("right").image[0, 0]) == 50


def test_run_once_feeds_the_sink():
    received = []
    left = FakeCamera(_imgs(2))
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub(), sink=received.append)
    engine.run_once()
    engine.run_once()
    assert [p.frame_index for p in received] == [0, 1]


def test_one_camera_failing_still_increments_index_and_keeps_other():
    left = FakeCamera(_imgs(2), fail_on={0})
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    pair0 = engine.run_once()
    assert pair0.frame_index == 0
    assert pair0.left is None
    assert pair0.right is not None
    pair1 = engine.run_once()
    assert pair1.frame_index == 1
    assert pair1.left is not None


def test_device_frame_ids_are_preserved_for_drop_detection():
    left = FakeCamera(_imgs(3), frame_ids=[0, 1, 3])   # camera dropped id 2
    right = FakeCamera(_imgs(3, base=100), frame_ids=[0, 1, 2])
    engine = AcquisitionEngine(left, right, FrameHub())
    left_ids = [engine.run_once().left.device_frame_id for _ in range(3)]
    assert left_ids == [0, 1, 3]


def test_run_loop_stops_and_drains_until_stream_ends():
    left = FakeCamera(_imgs(4))
    right = FakeCamera(_imgs(4, base=100))
    sink = []
    engine = AcquisitionEngine(left, right, FrameHub(), sink=sink.append)
    engine.run(max_ticks=4)
    assert [p.frame_index for p in sink] == [0, 1, 2, 3]


def test_run_loop_exits_when_stop_called():
    left = FakeCamera(_imgs(1000))
    right = FakeCamera(_imgs(1000, base=1))
    engine = AcquisitionEngine(left, right, FrameHub())
    engine.stop()
    engine.run(max_ticks=1000)
    assert engine.ticks_completed <= 1
