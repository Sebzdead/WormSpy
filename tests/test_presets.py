"""Camera settings and the presets file (ADR 0006)."""

from dataclasses import replace

import pytest

from wormspy.config import load_config
from wormspy.presets import CameraSettings, load_presets, save_presets

DEFAULT = CameraSettings(fps=10, clock_exposure_us=14_000, clock_gain_db=0,
                         follower_exposure_us=5_000, follower_gain_db=0)


def test_default_comes_first_and_is_rig_tomls_camera_values(tmp_path):
    config = load_config(tmp_path / "rig.toml")
    assert config.cameras.settings == DEFAULT
    assert config.paths.presets == tmp_path / "presets.json"
    assert load_presets(tmp_path / "presets.json", DEFAULT) == (("Default", DEFAULT),)


def test_presets_round_trip_in_order_without_storing_default(tmp_path):
    path = tmp_path / "presets.json"
    presets = (("Default", DEFAULT), ("slow", replace(DEFAULT, fps=5)),
               ("bright", replace(DEFAULT, clock_gain_db=12)))
    save_presets(path, presets)
    assert "Default" not in path.read_text()
    assert load_presets(path, DEFAULT) == presets
    assert not path.with_suffix(".json.tmp").exists()


def test_a_corrupt_presets_file_is_reported_by_name(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text('{"presets": {"x": {"fps": 10}}}')
    with pytest.raises(ValueError, match="presets.json"):
        load_presets(path, DEFAULT)


@pytest.mark.parametrize(("change", "message"), [
    ({"fps": 0}, "Frame rate"),
    ({"clock_exposure_us": 99_000}, "clock exposure"),
    ({"follower_exposure_us": 0}, "follower exposure"),
    ({"follower_gain_db": 60}, "follower gain"),
])
def test_problems_name_the_setting(change, message):
    assert message in replace(DEFAULT, **change).problem()


def test_valid_settings_have_no_problem():
    assert DEFAULT.problem() is None
    assert replace(DEFAULT, fps=20, clock_exposure_us=48_000).problem() is None
