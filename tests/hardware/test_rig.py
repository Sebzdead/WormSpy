"""HardwareRig / open_rig against fake PySpin cameras and a fake stage connector."""

import dataclasses

import numpy as np
import pytest

from tests.hardware.fake_pyspin import FakeCamera, FakePySpin
from wormspy.config import load_config
from wormspy.core.stage import NullStage
from wormspy.hardware.flir import HardwareError
from wormspy.hardware.rig import open_rig


def _config(tmp_path, clock_serial="21000001", follower_serial="21000002"):
    path = tmp_path / "rig.toml"
    path.write_text(f"""
[cameras.clock]
serial = "{clock_serial}"
[cameras.follower]
serial = "{follower_serial}"
""")
    return load_config(path)


def _rig(tmp_path, stage_connector=lambda _: NullStage(), **serials):
    clock = FakeCamera("21000001", shape=(1200, 1920), dtype=np.uint16)
    follower = FakeCamera("21000002", shape=(600, 960), dtype=np.uint8)
    pyspin = FakePySpin([clock, follower])
    rig = open_rig(_config(tmp_path, **serials), pyspin=pyspin, stage_connector=stage_connector)
    return rig, clock, follower, pyspin


def test_open_rig_assigns_roles_and_shapes_from_config_and_the_cameras(tmp_path):
    rig, clock, follower, _ = _rig(tmp_path)
    assert (rig.clock_channel.name, rig.clock_channel.role) == ("gcamp", "fluorescence")
    assert rig.follower_channel.shape == (600, 960)
    assert rig.fps == 10
    assert clock.written("LineSource") == ["LineSource_ExposureActive"]
    assert follower.written("TriggerMode")[-1] == "TriggerMode_On"


def test_start_arms_the_follower_before_the_clock_and_stop_reverses_it(tmp_path):
    rig, clock, follower, _ = _rig(tmp_path)
    order = []
    clock.BeginAcquisition = lambda: order.append("clock") or setattr(clock, "streaming", True)
    follower.BeginAcquisition = (lambda: order.append("follower")
                                 or setattr(follower, "streaming", True))
    rig.start()
    assert order == ["follower", "clock"]
    rig.stop()
    assert not clock.streaming and not follower.streaming


def test_if_the_clock_fails_to_start_the_follower_is_disarmed(tmp_path):
    rig, clock, follower, _ = _rig(tmp_path)

    def boom():
        raise RuntimeError("USB bandwidth")
    clock.BeginAcquisition = boom
    with pytest.raises(RuntimeError):
        rig.start()
    assert not follower.streaming


def test_a_camera_that_is_not_connected_is_fatal_and_releases_the_system(tmp_path):
    clock = FakeCamera("21000001")
    pyspin = FakePySpin([clock])
    with pytest.raises(HardwareError, match="21000002"):
        open_rig(_config(tmp_path), pyspin=pyspin, stage_connector=lambda _: NullStage())
    assert pyspin.system.released and not clock.initialised


def test_a_missing_stage_is_a_warning_not_a_failure(tmp_path):
    def no_stage(_):
        raise OSError("COM6 not found")
    rig, *_ = _rig(tmp_path, stage_connector=no_stage)
    assert isinstance(rig.stage, NullStage)
    assert "COM6 not found" in rig.warnings[0]


def test_close_releases_cameras_system_and_stage(tmp_path):
    class ClosableStage(NullStage):
        closed = False

        def close(self):
            self.closed = True
    stage = ClosableStage()
    rig, clock, follower, pyspin = _rig(tmp_path, stage_connector=lambda _: stage)
    rig.start()
    rig.close()
    assert stage.closed and pyspin.system.released
    assert not clock.initialised and not follower.initialised


def test_frames_flow_from_both_cameras(tmp_path):
    rig, clock, follower, _ = _rig(tmp_path)
    rig.start()
    clock.push(3, 1_000, value=500)
    follower.push(17, 2_000, value=40)
    a, b = rig.clock_camera.read(), rig.follower_camera.read()
    assert (a.device_frame_id, b.device_frame_id) == (0, 0)
    assert a.image.dtype == np.uint16 and b.image.shape == (600, 960)
    assert dataclasses.is_dataclass(a)


def test_new_settings_are_written_in_an_order_the_camera_accepts(tmp_path):
    from dataclasses import replace
    rig, clock, follower, _ = _rig(tmp_path)
    faster = replace(rig.settings, fps=20, clock_exposure_us=30_000, follower_gain_db=6)
    clock.log.clear()
    rig.apply_settings(faster)
    names = [name for name, _ in clock.log]
    assert names.index("ExposureTime") < names.index("AcquisitionFrameRate")   # shorten first
    assert clock.written("AcquisitionFrameRate")[-1] == 20.0
    assert follower.written("Gain")[-1] == 6.0
    assert (rig.fps, rig.settings) == (20, faster)
    slower = replace(faster, fps=5, clock_exposure_us=150_000)
    clock.log.clear()
    rig.apply_settings(slower)
    names = [name for name, _ in clock.log]
    assert names.index("AcquisitionFrameRate") < names.index("ExposureTime")   # slow down first
