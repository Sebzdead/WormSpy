"""SimDriver: ticks the simulated rig on a thread, like the free-running clock camera."""

import time

from wormspy.sim.driver import SimDriver
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

GEOM = ImageGeometry(width_px=32, height_px=24, um_per_px=40.0)


def _rig(fps=50.0):
    stage = SimStage({a: (0.0, 1e7) for a in "xyz"}, 1000.0, {"x": 0.0, "y": 0.0, "z": 0.0})
    return SimRig(SyntheticWorm(seed=0), stage, ChannelSpec(GEOM, "fluorescence"),
                  ChannelSpec(GEOM, "behaviour"), fps=fps)


def _count(camera):
    n = 0
    while camera.read() is not None:
        n += 1
    return n


def test_driver_exposes_frames_at_roughly_the_frame_rate():
    rig = _rig(fps=50.0)
    driver = SimDriver(rig)
    driver.start()
    time.sleep(0.5)
    driver.stop()
    assert 15 <= _count(rig.clock_camera) <= 35      # ~25 expected; generous for CI jitter


def test_speed_factor_runs_simulated_time_faster_than_real_time():
    rig = _rig(fps=50.0)
    driver = SimDriver(rig, speed=10.0)
    driver.start()
    time.sleep(0.2)
    driver.stop()
    assert _count(rig.clock_camera) >= 50            # ~100 expected


def test_no_frames_after_stop():
    rig = _rig()
    driver = SimDriver(rig)
    driver.start()
    time.sleep(0.05)
    driver.stop()
    _count(rig.clock_camera)
    time.sleep(0.1)
    assert _count(rig.clock_camera) == 0
