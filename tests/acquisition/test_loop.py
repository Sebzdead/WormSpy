"""AcquisitionLoop: cameras -> FramePairer -> FrameHub + on_pair (ADR 0006)."""

import time

from wormspy.acquisition.loop import AcquisitionLoop
from wormspy.core.cameras import Camera
from wormspy.core.frames import FrameHub
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

GEOM = ImageGeometry(width_px=64, height_px=48, um_per_px=20.0)


def _rig(**kw):
    stage = SimStage({a: (0.0, 1e7) for a in "xyz"}, 1000.0, {"x": 0.0, "y": 0.0, "z": 0.0})
    return SimRig(SyntheticWorm(seed=0), stage, ChannelSpec(GEOM, "fluorescence"),
                  ChannelSpec(GEOM, "behaviour"), fps=10.0, **kw)


def _loop(rig, pairs, hub=None, failures=None):
    return AcquisitionLoop(rig.clock_camera, rig.follower_camera, clock_name="gcamp",
                           follower_name="bf", hub=hub or FrameHub(), on_pair=pairs.append,
                           on_failure=(failures.append if failures is not None else None))


def test_each_triggered_exposure_yields_one_pair_and_updates_the_hub():
    rig, pairs, hub = _rig(), [], FrameHub()
    loop = _loop(rig, pairs, hub)
    for _ in range(3):
        rig.tick()
        loop.step()
    assert [p.frame_index for p in pairs] == [0, 1, 2]
    assert hub.latest("gcamp").device_frame_id == 2
    assert hub.latest("bf").device_frame_id == 2


def test_drops_come_through_as_pairs_with_a_missing_side():
    rig, pairs = _rig(clock_drops={1}, follower_drops={2}), []
    loop = _loop(rig, pairs)
    for _ in range(4):
        rig.tick()
        loop.step()
    assert [(p.clock is None, p.follower is None) for p in pairs[:3]] == \
        [(False, False), (True, False), (False, True)]


def test_stopping_flushes_pairs_still_waiting_for_a_frame():
    rig, pairs = _rig(follower_drops={1}), []
    loop = _loop(rig, pairs)
    for _ in range(2):
        rig.tick()
        loop.step()
    assert [p.frame_index for p in pairs] == [0]      # index 1 unresolved: follower silent
    loop.flush()
    assert [(p.frame_index, p.follower is None) for p in pairs] == [(0, False), (1, True)]


class _BrokenCamera(Camera):
    def read(self):
        raise RuntimeError("USB disconnect")

    def is_open(self):
        return True

    def release(self):
        pass


def test_a_camera_error_stops_the_thread_and_reports_failure():
    rig, pairs, failures = _rig(), [], []
    loop = AcquisitionLoop(_BrokenCamera(), rig.follower_camera, "gcamp", "bf", FrameHub(),
                           on_pair=pairs.append, on_failure=failures.append)
    loop.start()
    deadline = time.monotonic() + 2
    while not failures and time.monotonic() < deadline:
        time.sleep(0.01)
    loop.stop()
    assert failures and "USB disconnect" in failures[0]


def test_the_thread_pairs_frames_as_the_rig_produces_them():
    rig, pairs = _rig(), []
    loop = _loop(rig, pairs)
    loop.start()
    for _ in range(5):
        rig.tick()
    deadline = time.monotonic() + 2
    while len(pairs) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    loop.stop()
    assert [p.frame_index for p in pairs] == [0, 1, 2, 3, 4]
