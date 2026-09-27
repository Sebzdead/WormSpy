"""FramePairer: clock + follower frames -> contiguous FramePairs with drop detection (ADR 0006)."""

import numpy as np

from wormspy.acquisition.pairing import FramePairer
from wormspy.core.frames import Frame

PERIOD_NS = 100_000_000
OFFSET_NS = 150_000


def _f(frame_id, ts_ns=None):
    ts = frame_id * PERIOD_NS if ts_ns is None else ts_ns
    return Frame(image=np.zeros((2, 2), np.uint16), device_frame_id=frame_id,
                 capture_ts=ts / 1e9, device_timestamp_ns=ts)


def _follower(frame_id, extra_ns=0):
    return _f(frame_id, frame_id * PERIOD_NS + OFFSET_NS + extra_ns)


def _summary(pairs):
    return [(p.frame_index,
             None if p.clock is None else p.clock.device_frame_id,
             None if p.follower is None else p.follower.device_frame_id) for p in pairs]


def test_matching_frames_pair_with_contiguous_indices_from_zero():
    pairer = FramePairer()
    out = []
    for i in range(100, 103):          # device ids needn't start at 0
        out += pairer.add_clock(_f(i))
        out += pairer.add_follower(_follower(i))
    assert _summary(out) == [(0, 100, 100), (1, 101, 101), (2, 102, 102)]


def test_a_pair_is_emitted_only_once_both_channels_are_resolved():
    pairer = FramePairer()
    assert pairer.add_clock(_f(0)) == []
    assert pairer.add_clock(_f(1)) == []          # follower lagging behind
    out = pairer.add_follower(_follower(0))
    assert _summary(out) == [(0, 0, 0)]


def test_a_clock_drop_becomes_a_pair_without_a_clock_frame():
    pairer = FramePairer()
    out = []
    for i in (0, 2):
        out += pairer.add_clock(_f(i))
    for i in (0, 1, 2):
        out += pairer.add_follower(_follower(i))
    assert _summary(out) == [(0, 0, 0), (1, None, 1), (2, 2, 2)]


def test_a_follower_drop_becomes_a_pair_without_a_follower_frame():
    pairer = FramePairer()
    out = []
    for i in (0, 1, 2):
        out += pairer.add_clock(_f(i))
    for i in (0, 2):
        out += pairer.add_follower(_follower(i))
    assert _summary(out) == [(0, 0, 0), (1, 1, None), (2, 2, 2)]


def test_follower_frames_before_the_first_clock_frame_are_discarded():
    pairer = FramePairer()
    assert pairer.add_follower(_follower(4)) == []
    out = pairer.add_clock(_f(5)) + pairer.add_follower(_follower(5))
    assert _summary(out) == [(0, 5, 5)]


def test_flush_resolves_everything_seen_marking_missing_frames_dropped():
    pairer = FramePairer()
    pairer.add_clock(_f(0))
    pairer.add_clock(_f(1))
    pairer.add_follower(_follower(0))
    assert _summary(pairer.flush()) == [(1, 1, None)]
    assert pairer.flush() == []


def test_skew_is_measured_against_the_first_pairs_offset():
    pairer = FramePairer()
    out = []
    for i, extra in ((0, 0), (1, 0), (2, 30_000_000)):   # frame 2 arrives 30 ms late
        out += pairer.add_clock(_f(i))
        out += pairer.add_follower(_follower(i, extra))
    assert [p.skew_ns for p in out] == [0, 0, 30_000_000]


def test_skew_is_unknown_when_either_frame_is_missing():
    pairer = FramePairer()
    out = pairer.add_clock(_f(0)) + pairer.add_follower(_follower(0))
    out += pairer.add_clock(_f(1)) + pairer.add_clock(_f(2)) + pairer.add_follower(_follower(2))
    assert [p.skew_ns for p in out] == [0, None, 0]
