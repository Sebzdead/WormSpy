"""rig.toml loading and validation (ADR 0005 amendment)."""

from pathlib import Path

import pytest

from wormspy.config import ConfigError, load_config
from wormspy.control import commands as C

EXAMPLE = Path(__file__).parents[1] / "config" / "rig.example.toml"


def _write(tmp_path, text):
    path = tmp_path / "rig.toml"
    path.write_text(text)
    return path


def test_the_shipped_example_config_loads():
    config = load_config(EXAMPLE)
    assert config.stage.xy_port and config.stage.z_port
    assert set(config.stage.safe_range_um) == {"x", "y", "z"}


def test_missing_sections_fall_back_to_defaults(tmp_path):
    config = load_config(_write(tmp_path, ""))
    assert config.gamepad.buttons == {"X": C.ToggleTracking, "B": C.ToggleRecording,
                                      "A": C.ReturnToFocus, "Y": C.SetFocus}
    assert config.gamepad.sticks.xy_max_um_s > 0
    assert config.paths.data_root.name == "WormsPy_data"


def test_values_are_read_from_the_file(tmp_path):
    config = load_config(_write(tmp_path, """
[stage]
xy_port = "COM6"
z_port = "COM3"
max_speed_um_s = 3000
[stage.safe_range_um]
x = [1000, 49000]
y = [1000, 49000]
z = [2000, 18000]

[gamepad.sticks]
xy_max_um_s = 800
deadzone = 0.15

[gamepad.buttons]
X = "ToggleTracking"
Start = "MarkEvent"

[paths]
data_root = "D:/WormsPy_data"
"""))
    assert config.stage.xy_port == "COM6" and config.stage.max_speed_um_s == 3000
    assert config.stage.safe_range_um["z"] == (2000.0, 18000.0)
    assert config.gamepad.sticks.xy_max_um_s == 800 and config.gamepad.sticks.deadzone == 0.15
    assert config.gamepad.buttons == {"X": C.ToggleTracking, "Start": C.MarkEvent}
    assert config.paths.data_root == Path("D:/WormsPy_data")
    assert config.paths.calibration.name == "calibration.json"


@pytest.mark.parametrize("text, message", [
    ('[stage.safe_range_um]\nz = [5000, 100]', "safe_range_um.z"),
    ('[stage.safe_range_um]\nq = [0, 1]', "unknown axis"),
    ('[gamepad.buttons]\nX = "LaunchRockets"', "LaunchRockets"),
    ('[gamepad.sticks]\ndeadzone = 1.5', "deadzone"),
    ('[stage]\nmax_speed_um_s = -1', "max_speed_um_s"),
    ('[stage\nbroken', "rig.toml"),
])
def test_invalid_configs_are_rejected_with_a_clear_message(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, text))


def test_a_missing_config_file_means_all_defaults(tmp_path):
    config = load_config(tmp_path / "rig.toml")
    assert config.stage.xy_port == "COM6"
    assert config.paths.calibration == tmp_path / "calibration.json"


def test_camera_defaults_match_the_reference_rig(tmp_path):
    cameras = load_config(_write(tmp_path, "")).cameras
    assert cameras.fps == 10
    assert (cameras.clock.name, cameras.clock.role, cameras.clock.pixel_format) == (
        "gcamp", "fluorescence", "Mono16")
    assert cameras.clock.exposure_us == 14_000
    assert (cameras.follower.name, cameras.follower.role) == ("brightfield", "behaviour")


def test_camera_settings_are_read_from_the_file(tmp_path):
    cameras = load_config(_write(tmp_path, """
[cameras]
fps = 20
[cameras.clock]
serial = "21000001"
exposure_us = 30000
[cameras.follower]
serial = 21000002
name = "bf"
""")).cameras
    assert cameras.fps == 20
    assert cameras.clock.serial == "21000001" and cameras.clock.exposure_us == 30_000
    assert cameras.follower.serial == "21000002" and cameras.follower.name == "bf"


@pytest.mark.parametrize(("text", "message"), [
    ("[cameras.clock]\nexposure_us = 99000", "exposure_us"),
    ("[cameras.clock]\npixel_format = 'Mono8'", "Mono16"),
    ("[cameras.follower]\nrole = 'calcium'", "role"),
    ("[cameras.follower]\nname = 'gcamp'", "different names"),
    ("[cameras.clock]\nserial='1'\n[cameras.follower]\nserial='1'", "same serial"),
    ("[cameras.clock]\nexposure = 5", "unknown key"),
    ("[cameras]\nfps = 0", "fps"),
])
def test_bad_camera_settings_are_rejected_by_name(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, text))
