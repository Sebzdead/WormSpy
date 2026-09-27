import numpy as np
import pytest

from wormspy.core.cameras import Camera
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

GEOM = ImageGeometry(width_px=360, height_px=225, um_per_px=4.0)
NATIVE_PER_UM = 20.0


def _rig(worm=None, **kw):
    worm = worm if worm is not None else SyntheticWorm(seed=0, start_um=(25_000.0, 25_000.0))
    stage = SimStage(limits={a: (0.0, 1_000_000.0) for a in "xyz"},
                     native_per_um=NATIVE_PER_UM, max_speed_um_s=2000.0,
                     positions={"x": 500_000.0, "y": 500_000.0, "z": 0.0})
    return SimRig(worm, stage,
                  clock=ChannelSpec(GEOM, role="fluorescence"),
                  follower=ChannelSpec(GEOM, role="behaviour"),
                  fps=10.0, **kw)


def _drain(camera):
    frames = []
    while (frame := camera.read()) is not None:
        frames.append(frame)
    return frames


def test_rig_exposes_clock_and_follower_as_cameras():
    rig = _rig()
    assert isinstance(rig.clock_camera, Camera) and isinstance(rig.follower_camera, Camera)
    assert rig.clock_camera.read() is None  # nothing exposed before the first tick


def test_every_clock_exposure_triggers_exactly_one_follower_exposure():
    rig = _rig()
    for _ in range(3):
        rig.tick()
    assert [f.device_frame_id for f in _drain(rig.clock_camera)] == [0, 1, 2]
    assert [f.device_frame_id for f in _drain(rig.follower_camera)] == [0, 1, 2]


def test_device_timestamps_follow_the_frame_period_with_a_fixed_follower_offset():
    rig = _rig(follower_offset_ns=250_000)
    for _ in range(3):
        rig.tick()
    clock = _drain(rig.clock_camera)
    follower = _drain(rig.follower_camera)
    assert [f.device_timestamp_ns for f in clock] == [0, 100_000_000, 200_000_000]
    assert [f.device_timestamp_ns for f in follower] == [250_000, 100_250_000, 200_250_000]
    assert [f.capture_ts for f in clock] == pytest.approx([0.0, 0.1, 0.2])


def test_injected_drops_leave_a_gap_in_the_device_frame_ids():
    rig = _rig(clock_drops={1}, follower_drops={2})
    for _ in range(4):
        rig.tick()
    assert [f.device_frame_id for f in _drain(rig.clock_camera)] == [0, 2, 3]
    assert [f.device_frame_id for f in _drain(rig.follower_camera)] == [0, 1, 3]


def test_channel_role_selects_image_type():
    rig = _rig()
    rig.tick()
    assert rig.clock_camera.read().image.dtype == np.uint16    # fluorescence
    assert rig.follower_camera.read().image.dtype == np.uint8  # behaviour


def test_stage_position_closes_the_loop_on_the_rendered_image():
    worm = SyntheticWorm(seed=0, start_um=(25_000.0, 25_000.0), speed_um_s=0.0,
                         reversal_rate_hz=0.0)
    rig = _rig(worm=worm)
    cx, cy = worm.centroid
    rig.stage.move_absolute("x", cx * NATIVE_PER_UM)
    rig.stage.move_absolute("y", cy * NATIVE_PER_UM)
    for _ in range(20):  # let the stage arrive
        rig.tick()
    image = _drain(rig.follower_camera)[-1].image
    rows, cols = np.nonzero(image < 128)
    assert abs(cols.mean() - GEOM.width_px / 2) < 10
    assert abs(rows.mean() - GEOM.height_px / 2) < 10
