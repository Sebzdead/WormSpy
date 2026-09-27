"""Tracker in closed loop with the simulated worm, stepped synchronously in simulated time."""

import math

import numpy as np

from wormspy.acquisition.loop import AcquisitionLoop
from wormspy.acquisition.stage_poller import StagePoller
from wormspy.calibration.math import CameraCalibration
from wormspy.control import TrackingStatus as S
from wormspy.control import events as E
from wormspy.core.frames import FrameHub
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm
from wormspy.tracking.controller import ServoConfig
from wormspy.tracking.estimators import DarkObject
from wormspy.tracking.tracker import Tracker

GEOM = ImageGeometry(width_px=320, height_px=200, um_per_px=4.5, rotation_deg=4.0)  # 1.4 × 0.9 mm
FPS = 20.0
START = (25_000.0, 25_000.0)


class Loop:
    """Everything wired together, advanced one frame period at a time."""

    def __init__(self, worm=None, safe=(0.0, 50_400.0)):
        self.worm = worm or SyntheticWorm(seed=1, start_um=START, speed_um_s=150.0)
        self.stage = SimStage({"x": safe, "y": safe, "z": (0.0, 20_000.0)}, 2000.0,
                              {"x": START[0], "y": START[1], "z": 5_000.0})
        self.rig = SimRig(self.worm, self.stage, ChannelSpec(GEOM, "fluorescence"),
                          ChannelSpec(GEOM, "behaviour", noise_sigma=4.0), fps=FPS)
        self.hub = FrameHub()
        self.acq = AcquisitionLoop(self.rig.clock_camera, self.rig.follower_camera, "gcamp",
                                   "bf", self.hub, on_pair=lambda pair: None)
        self.poller = StagePoller(self.stage, lambda: self.rig.time_s)
        self.events, self.rows, self.override = [], [], False
        self.tracker = Tracker(
            hub=self.hub, poller=self.poller, stage=self.stage,
            calibration=CameraCalibration(GEOM.matrix, 0.0, (GEOM.height_px, GEOM.width_px)),
            source_channel="bf", estimator=DarkObject(background_sigma_px=30.0,
                                                       min_area_px=300.0),
            config=ServoConfig(), dispatch=self.events.append, now=lambda: self.rig.time_s,
            index_of=self.acq.index_of, log=lambda **row: self.rows.append(row),
            override=lambda: self.override)

    def run(self, seconds):
        for _ in range(int(seconds * FPS)):
            self.rig.tick()
            self.acq.step()
            self.poller.sample_once()
            self.tracker.step()

    def distance_to_centre(self):
        return math.dist(self.worm.centroid, (self.stage.position_um("x"),
                                              self.stage.position_um("y")))

    def statuses(self):
        return [e.status for e in self.events if isinstance(e, E.TrackingStatusChanged)]


def test_the_worm_stays_centred_while_it_crawls():
    loop = Loop()
    loop.run(2.0)                                   # acquire
    worst = 0.0
    for _ in range(40):                             # 40 s of crawling, reversals included
        loop.run(1.0)
        worst = max(worst, loop.distance_to_centre())
    travelled = math.dist(loop.worm.centroid, START)
    assert travelled > 1500                         # it really went somewhere
    assert worst < 180                              # but stayed near the middle of the FOV
    assert S.LOST not in loop.statuses()


def test_every_evaluated_frame_is_logged_with_its_frame_index():
    loop = Loop()
    loop.run(3.0)
    indices = [r["frame_index"] for r in loop.rows]
    assert len(indices) > 50 and indices == sorted(indices)
    last = loop.rows[-1]
    assert last["accepted"] and last["status"] == "tracking" and last["source_channel"] == "bf"
    assert math.dist(last["worm_um"], loop.worm.centroid) < 100


def test_joystick_override_hands_xy_to_the_operator_and_tracking_resumes():
    loop = Loop()
    loop.run(2.0)
    loop.override = True
    loop.stage.move_velocity_um_s("x", 300.0)      # the operator steers away
    loop.run(1.0)
    assert loop.statuses()[-1] is S.OVERRIDE
    assert loop.stage.velocity_um_s("x") == 300.0      # tracker sent nothing while overridden
    loop.override = False
    loop.stage.stop("x")
    loop.run(3.0)
    assert loop.statuses()[-1] is S.TRACKING and loop.distance_to_centre() < 250


def test_a_vanished_worm_is_lost_the_stage_stops_and_tracking_resumes_when_it_returns():
    loop = Loop()
    loop.run(2.0)
    loop.worm.points += 1e5                         # gone from view
    loop.run(1.0)
    assert loop.statuses()[-1] is S.LOST
    assert np.allclose([loop.stage.velocity_um_s("x"), loop.stage.velocity_um_s("y")], 0.0)
    loop.worm.points -= 1e5                         # back where it was
    loop.run(2.0)
    assert loop.statuses()[-1] is S.TRACKING


def test_reaching_the_safe_range_edge_is_reported_as_limit():
    worm = SyntheticWorm(seed=1, start_um=START, speed_um_s=150.0, reversal_rate_hz=0.0)
    loop = Loop(worm=worm, safe=(24_900.0, 25_100.0))  # a tiny safe range
    loop.run(10.0)
    assert S.LIMIT in loop.statuses()


def test_stopping_the_tracker_stops_the_stage():
    loop = Loop()
    loop.run(3.0)
    loop.tracker.stop()
    assert loop.stage.velocity_um_s("x") == 0.0 and loop.stage.velocity_um_s("y") == 0.0


def test_the_latest_estimate_is_available_for_the_display_overlay():
    loop = Loop()
    assert loop.tracker.last_estimate is None
    loop.run(1.0)
    estimate = loop.tracker.last_estimate
    assert estimate is not None and estimate.point_px is not None and estimate.mask is not None
