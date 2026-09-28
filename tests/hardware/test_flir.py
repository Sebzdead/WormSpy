"""FlirCamera / FlirSystem against a fake PySpin (ADR 0005/0006). No hardware needed."""

import numpy as np
import pytest

from tests.hardware.fake_pyspin import FakeCamera, FakePySpin, SpinnakerException
from wormspy.config import CamerasConfig
from wormspy.hardware.flir import FlirCamera, FlirSystem, HardwareError

CLOCK = CamerasConfig().clock
FOLLOWER = CamerasConfig().follower


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def _camera(config=CLOCK, **kwargs):
    cam = FakeCamera("21000001")
    return FlirCamera(cam, FakePySpin(), config, **kwargs), cam


def test_opening_configures_fixed_exposure_gain_format_and_chunk_data():
    _, cam = _camera()
    assert cam.initialised
    assert cam.written("ExposureAuto") == ["ExposureAuto_Off"]
    assert cam.written("ExposureTime") == [14_000.0]
    assert cam.written("GainAuto") == ["GainAuto_Off"]
    assert cam.written("PixelFormat") == ["PixelFormat_Mono16"]
    assert cam.written("ChunkModeActive") == [True]
    assert cam.written("ChunkSelector") == ["ChunkSelector_FrameID", "ChunkSelector_Timestamp"]
    assert cam.written("ChunkEnable") == [True, True]
    assert cam.written("TLStream.StreamBufferHandlingMode") == [
        "StreamBufferHandlingMode_OldestFirst"]


def test_the_clock_free_runs_and_outputs_exposure_active():
    camera, cam = _camera()
    camera.configure_as_clock(fps=10.0, output_line="Line2")
    assert cam.written("TriggerMode")[-1] == "TriggerMode_Off"
    assert cam.written("AcquisitionFrameRate") == [10.0]
    assert cam.written("LineSelector") == ["LineSelector_Line2"]
    assert cam.written("LineMode") == ["LineMode_Output"]
    assert cam.written("LineSource") == ["LineSource_ExposureActive"]


def test_the_follower_is_triggered_by_the_line_and_the_mode_is_switched_on_last():
    camera, cam = _camera(FOLLOWER)
    camera.configure_as_follower(trigger_line="Line3")
    assert cam.written("TriggerSource") == ["TriggerSource_Line3"]
    assert cam.written("TriggerActivation") == ["TriggerActivation_RisingEdge"]
    names = [name for name, _ in cam.log]
    last_trigger_mode = max(i for i, n in enumerate(names) if n == "TriggerMode")
    assert last_trigger_mode > names.index("TriggerSource")
    assert cam.written("TriggerMode")[-1] == "TriggerMode_On"
    assert cam.written("ExposureTime") == [5_000.0]       # keeps its own exposure


def test_read_before_begin_returns_nothing_without_touching_the_sdk():
    camera, cam = _camera()
    cam.push(7, 0)
    assert camera.read() is None
    assert cam.timeouts == []


def test_frames_carry_a_copy_of_the_pixels_and_ids_counted_from_the_run_start():
    camera, cam = _camera()
    camera.begin()
    cam.push(41, 0, value=1234)
    cam.push(42, 0, value=99)
    first, second = camera.read(), camera.read()
    assert first.image.dtype == np.uint16 and int(first.image[0, 0]) == 1234  # survived Release
    assert (first.device_frame_id, second.device_frame_id) == (0, 1)
    camera.end()
    camera.begin()                                     # a new run counts from 0 again
    cam.push(90, 0)
    assert camera.read().device_frame_id == 0


def test_capture_time_is_mid_exposure_in_host_time_via_the_timestamp_latch():
    clock = Clock(100.0)
    camera, cam = _camera(clock=clock)
    cam.device_clock_ns = 5_000_000_000                # device time 5 s == host time 100 s
    camera.begin()
    cam.push(0, 5_300_000_000)                         # exposure started 0.3 s later
    clock.t = 100.9                                    # read late: host time must not matter
    frame = camera.read()
    assert frame.device_timestamp_ns == 5_300_000_000
    assert frame.capture_ts == pytest.approx(100.3 + 0.007)   # + half the 14 ms exposure


def test_the_latch_is_retaken_to_follow_clock_drift():
    clock = Clock(0.0)
    camera, cam = _camera(clock=clock, relatch_s=10.0)
    camera.begin()
    clock.t = 11.0
    cam.device_clock_ns = 11_000_000_000
    cam.push(0, 0)
    camera.read()
    assert cam.written("TimestampLatch") == ["execute", "execute"]


def test_a_timeout_is_not_an_error_but_other_sdk_errors_are():
    camera, cam = _camera()
    camera.begin()
    assert camera.read() is None                      # empty queue → timeout
    cam.queue.append(SpinnakerException("device lost"))
    with pytest.raises(SpinnakerException):
        camera.read()


def test_incomplete_images_are_released_and_skipped_so_they_show_as_drops():
    camera, cam = _camera()
    camera.begin()
    bad = cam.push(0, 0, incomplete=True)
    cam.push(8, 0)
    assert camera.read() is None and bad.released
    assert camera.read().device_frame_id == 1          # the lost first frame still counts
    assert camera.incomplete_frames == 1


def test_end_stops_streaming_and_release_deinitialises():
    camera, cam = _camera()
    camera.begin()
    camera.end()
    assert not cam.streaming and camera.read() is None
    camera.release()
    assert not cam.initialised and not camera.is_open()


def test_the_system_opens_cameras_by_serial():
    cams = [FakeCamera("21000001"), FakeCamera("21000002", dtype=np.uint8)]
    pyspin = FakePySpin(cams)
    system = FlirSystem(pyspin)
    assert system.serials() == ["21000001", "21000002"]
    follower = system.camera(FOLLOWER.__class__(**{**FOLLOWER.__dict__, "serial": "21000002"}),
                             "cameras.follower")
    assert follower.shape == (1200, 1920)
    system.close()
    assert not cams[1].initialised and pyspin.system.released


def test_a_missing_or_unset_serial_is_explained_with_the_cameras_found():
    system = FlirSystem(FakePySpin([FakeCamera("21000001")]))
    with pytest.raises(HardwareError, match=r"cameras.clock.serial.*21000001"):
        system.camera(CLOCK, "cameras.clock")
    wrong = CLOCK.__class__(**{**CLOCK.__dict__, "serial": "99"})
    with pytest.raises(HardwareError, match=r"no camera with serial 99.*21000001"):
        system.camera(wrong, "cameras.clock")


def test_without_pyspin_the_error_says_how_to_install_it(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "PySpin", None)
    with pytest.raises(HardwareError, match="Spinnaker"):
        FlirSystem()
