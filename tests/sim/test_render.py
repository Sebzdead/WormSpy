import numpy as np

from wormspy.sim.render import ImageGeometry, render_behaviour, render_fluorescence
from wormspy.sim.worm import SyntheticWorm

GEOM = ImageGeometry(width_px=400, height_px=250, um_per_px=4.0)


def test_point_under_the_stage_maps_to_image_centre():
    px = GEOM.world_to_px(np.array([[500.0, -300.0]]), stage_um=(500.0, -300.0))
    np.testing.assert_allclose(px, [[200.0, 125.0]])


def test_moving_the_stage_shifts_the_image_the_opposite_way():
    point = np.array([[0.0, 0.0]])
    px0 = GEOM.world_to_px(point, stage_um=(0.0, 0.0))
    px1 = GEOM.world_to_px(point, stage_um=(40.0, 0.0))
    np.testing.assert_allclose(px1 - px0, [[-10.0, 0.0]])  # 40 µm / 4 µm per px


def test_rotation_and_flip_are_applied():
    rotated = ImageGeometry(width_px=400, height_px=250, um_per_px=4.0, rotation_deg=90.0)
    px = rotated.world_to_px(np.array([[40.0, 0.0]]), stage_um=(0.0, 0.0))
    np.testing.assert_allclose(px, [[200.0, 135.0]], atol=1e-9)  # world +x -> image +y
    flipped = ImageGeometry(width_px=400, height_px=250, um_per_px=4.0, flip_x=True)
    px = flipped.world_to_px(np.array([[40.0, 0.0]]), stage_um=(0.0, 0.0))
    np.testing.assert_allclose(px, [[190.0, 125.0]])


def test_px_to_world_inverts_world_to_px():
    geom = ImageGeometry(width_px=400, height_px=250, um_per_px=4.0, rotation_deg=17.0,
                         flip_x=True)
    world = np.array([[12.0, -80.0], [300.0, 55.0]])
    px = geom.world_to_px(world, stage_um=(10.0, 20.0))
    np.testing.assert_allclose(geom.px_to_world(px, stage_um=(10.0, 20.0)), world, atol=1e-9)


def test_behaviour_image_is_a_dark_worm_on_a_light_8bit_background():
    worm = SyntheticWorm(seed=0, n_points=21)
    mid = worm.points[10]
    img = render_behaviour(worm, stage_um=mid, geometry=GEOM)
    assert img.dtype == np.uint8 and img.shape == (250, 400)
    assert img[125, 200] < 100          # body passes through the FOV centre
    assert img[0, 0] > 150              # background


def test_worm_outside_the_field_of_view_renders_as_pure_background():
    worm = SyntheticWorm(seed=0)
    img = render_behaviour(worm, stage_um=(1e5, 1e5), geometry=GEOM)
    assert img.min() == img.max()


def test_fluorescence_image_is_16bit_with_the_neuron_as_the_brightest_spot():
    worm = SyntheticWorm(seed=0, n_points=21)
    stage = worm.points[5]
    img = render_fluorescence(worm, stage_um=stage, geometry=GEOM, neuron_index=2)
    assert img.dtype == np.uint16 and img.shape == (250, 400)
    row, col = np.unravel_index(np.argmax(img), img.shape)
    expected = GEOM.world_to_px(worm.points[[2]], stage_um=stage)[0]
    assert abs(col - expected[0]) <= 1.5 and abs(row - expected[1]) <= 1.5
    assert img.max() > 10 * img[0, 0]


def test_noise_is_added_only_when_requested():
    worm = SyntheticWorm(seed=0)
    clean = render_fluorescence(worm, stage_um=worm.head, geometry=GEOM)
    again = render_fluorescence(worm, stage_um=worm.head, geometry=GEOM)
    np.testing.assert_array_equal(clean, again)
    noisy = render_fluorescence(worm, stage_um=worm.head, geometry=GEOM, noise_sigma=50.0,
                                rng=np.random.default_rng(0))
    assert not np.array_equal(clean, noisy)
    assert abs(noisy.astype(float).mean() - clean.astype(float).mean()) < 5.0
