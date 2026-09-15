import numpy as np
from core.cameras import Camera, FakeCamera


def test_fake_camera_yields_scripted_frames_in_order():
    cam = FakeCamera(images=[np.full((2, 2), 1, np.uint8),
                             np.full((2, 2), 2, np.uint8)])
    f0 = cam.read()
    f1 = cam.read()
    assert int(f0.image[0, 0]) == 1
    assert int(f1.image[0, 0]) == 2


def test_fake_camera_assigns_incrementing_device_frame_ids():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)] * 3)
    ids = [cam.read().device_frame_id for _ in range(3)]
    assert ids == [0, 1, 2]


def test_fake_camera_can_simulate_dropped_frames():
    # device_frame_ids 0,1,3 -> a gap (2 dropped) that downstream code can detect
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)] * 3, frame_ids=[0, 1, 3])
    ids = [cam.read().device_frame_id for _ in range(3)]
    assert ids == [0, 1, 3]


def test_fake_camera_returns_none_when_exhausted():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)])
    assert cam.read() is not None
    assert cam.read() is None


def test_fake_camera_read_failure_returns_none():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)], fail_on={0})
    assert cam.read() is None


def test_fake_camera_is_open_until_released():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)])
    assert cam.is_open() is True
    cam.release()
    assert cam.is_open() is False


def test_fake_camera_satisfies_camera_interface():
    assert issubclass(FakeCamera, Camera)
