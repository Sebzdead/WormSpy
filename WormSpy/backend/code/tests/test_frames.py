import threading
import numpy as np
from core.frames import Frame, AcquiredPair, FrameHub


def _frame(val, fid=0, ts=0.0):
    img = np.full((4, 4), val, dtype=np.uint8)
    return Frame(image=img, device_frame_id=fid, capture_ts=ts)


def test_hub_returns_none_before_first_publish():
    hub = FrameHub()
    assert hub.latest("left") is None


def test_hub_returns_most_recent_frame():
    hub = FrameHub()
    hub.publish("left", _frame(1, fid=1))
    hub.publish("left", _frame(2, fid=2))
    latest = hub.latest("left")
    assert latest.device_frame_id == 2
    assert int(latest.image[0, 0]) == 2


def test_hub_keeps_streams_independent():
    hub = FrameHub()
    hub.publish("left", _frame(1))
    hub.publish("right", _frame(9))
    assert int(hub.latest("left").image[0, 0]) == 1
    assert int(hub.latest("right").image[0, 0]) == 9


def test_hub_is_thread_safe_under_concurrent_writes():
    hub = FrameHub()
    def writer(start):
        for i in range(start, start + 500):
            hub.publish("left", _frame(i % 256, fid=i))
    threads = [threading.Thread(target=writer, args=(s,)) for s in (0, 1000)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hub.latest("left") is not None


def test_acquired_pair_fields():
    pair = AcquiredPair(frame_index=7, capture_ts=1.5, left=_frame(1), right=_frame(2))
    assert pair.frame_index == 7
    assert pair.capture_ts == 1.5
    assert pair.left.device_frame_id == 0
    assert pair.right is not None
