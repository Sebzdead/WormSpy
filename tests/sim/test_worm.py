import numpy as np

from wormspy.sim.worm import SyntheticWorm


def _segment_lengths(points):
    return np.linalg.norm(np.diff(points, axis=0), axis=1)


def test_worm_starts_straight_with_requested_length_and_head_at_start():
    worm = SyntheticWorm(seed=0, start_um=(100.0, 200.0), length_um=1000.0, n_points=21)
    assert worm.points.shape == (21, 2)
    np.testing.assert_allclose(worm.head, (100.0, 200.0))
    assert abs(_segment_lengths(worm.points).sum() - 1000.0) < 1e-6


def test_same_seed_gives_identical_trajectory():
    a = SyntheticWorm(seed=42, reversal_rate_hz=0.5)
    b = SyntheticWorm(seed=42, reversal_rate_hz=0.5)
    for _ in range(200):
        a.step(0.1)
        b.step(0.1)
    np.testing.assert_array_equal(a.points, b.points)


def test_body_segments_keep_their_length_while_crawling():
    worm = SyntheticWorm(seed=1, length_um=1000.0, n_points=21, reversal_rate_hz=0.3)
    for _ in range(500):
        worm.step(0.1)
    np.testing.assert_allclose(_segment_lengths(worm.points), 50.0, rtol=1e-6)


def test_head_speed_never_exceeds_crawl_speed():
    worm = SyntheticWorm(seed=2, speed_um_s=150.0)
    for _ in range(300):
        before = worm.head.copy()
        worm.step(0.1)
        assert np.linalg.norm(worm.head - before) <= 150.0 * 0.1 + 1e-9


def test_forward_crawling_moves_the_worm_away_from_its_start():
    worm = SyntheticWorm(seed=3, speed_um_s=150.0, reversal_rate_hz=0.0)
    start = worm.centroid.copy()
    for _ in range(100):  # 10 s
        worm.step(0.1)
    assert np.linalg.norm(worm.centroid - start) > 500.0


def test_during_a_reversal_the_tail_leads():
    worm = SyntheticWorm(seed=4, reversal_rate_hz=0.0)
    for _ in range(20):
        worm.step(0.1)
    worm.reverse(duration_s=2.0)
    assert worm.reversing
    tail_before, head_before = worm.tail.copy(), worm.head.copy()
    worm.step(0.1)
    assert np.linalg.norm(worm.tail - tail_before) > 0
    # the head is pulled back toward where the body was, i.e. it moves toward its neighbour
    assert np.linalg.norm(worm.head - head_before) > 0
    for _ in range(25):
        worm.step(0.1)
    assert not worm.reversing  # reversal ends after its duration
