"""The launcher: `wormspy --sim` assembles a running app; closing it shuts down cleanly."""

import time

import pytest
from PySide6.QtCore import Qt

from wormspy.app.main import build
from wormspy.control import Mode
from wormspy.control import commands as C


def _config(tmp_path):
    path = tmp_path / "rig.toml"
    path.write_text(f'[paths]\ndata_root = "{(tmp_path / "data").as_posix()}"\n')
    return path


def _wait(qtbot, condition, timeout=5000):
    qtbot.waitUntil(condition, timeout=timeout)


def test_sim_mode_builds_a_working_app(qtbot, tmp_path):
    app = build(["--sim", "--config", str(_config(tmp_path)), "--sim-speed", "4"])
    qtbot.addWidget(app.window)
    assert app.window.left_view.channel == "brightfield"
    assert app.window.right_view.channel == "gcamp"
    qtbot.mouseClick(app.window.controls.live_button, Qt.MouseButton.LeftButton)
    _wait(qtbot, lambda: app.window.tick() or app.window.right_view.image is not None)
    assert app.window.right_view.image.shape == (1200, 1920)
    app.shutdown()
    assert app.store.state.mode is Mode.IDLE


def test_the_simulator_menu_places_the_calibration_slide(qtbot, tmp_path):
    app = build(["--sim", "--config", str(_config(tmp_path))])
    qtbot.addWidget(app.window)
    assert app.rig.rig.target is False
    app.slide_action.trigger()
    assert app.rig.rig.target is True
    app.shutdown()


def test_closing_the_window_shuts_everything_down(qtbot, tmp_path):
    app = build(["--sim", "--config", str(_config(tmp_path)), "--sim-speed", "4"])
    qtbot.addWidget(app.window)
    app.store.dispatch(C.StartLive())
    app.executor.wait_idle()
    app.window.close()
    deadline = time.monotonic() + 5
    while app.store.state.mode is not Mode.IDLE and time.monotonic() < deadline:
        qtbot.wait(20)
    assert app.store.state.mode is Mode.IDLE


def _hardware_config(tmp_path, serials=("21000001", "21000002")):
    path = tmp_path / "rig.toml"
    path.write_text(f"""
[cameras.clock]
serial = "{serials[0]}"
[cameras.follower]
serial = "{serials[1]}"
[stage]
xy_port = "/dev/wormspy-test-no-such-port"
z_port = "/dev/wormspy-test-no-such-port"
[paths]
data_root = "{(tmp_path / "data").as_posix()}"
""")
    return path


def _fake_pyspin(monkeypatch):
    import sys

    import numpy as np

    from tests.hardware.fake_pyspin import FakeCamera, FakePySpin
    clock = FakeCamera("21000001", shape=(120, 192), dtype=np.uint16)
    follower = FakeCamera("21000002", shape=(120, 192), dtype=np.uint8)
    pyspin = FakePySpin([clock, follower])
    monkeypatch.setitem(sys.modules, "PySpin", pyspin)
    return pyspin, clock, follower


def test_hardware_mode_without_pyspin_says_how_to_install_it(monkeypatch, tmp_path):
    import sys
    monkeypatch.setitem(sys.modules, "PySpin", None)
    with pytest.raises(SystemExit, match="Spinnaker"):
        build(["--config", str(_hardware_config(tmp_path))])


def test_hardware_mode_names_the_missing_camera_serial(monkeypatch, tmp_path):
    _fake_pyspin(monkeypatch)
    with pytest.raises(SystemExit, match=r"cameras.clock.serial.*21000001, 21000002"):
        build(["--config", str(_hardware_config(tmp_path, serials=("", "21000002")))])


def test_hardware_mode_streams_both_cameras_and_warns_about_a_missing_stage(
        monkeypatch, qtbot, tmp_path):
    pyspin, clock, follower = _fake_pyspin(monkeypatch)
    app = build(["--config", str(_hardware_config(tmp_path))])
    qtbot.addWidget(app.window)
    assert app.window.windowTitle() == "WormsPy"
    assert app.window.left_view.channel == "brightfield"      # behaviour on the left
    assert any(a.kind == "hardware" and "stage" in a.message for a in app.store.state.alarms)
    for fid in range(3):
        clock.push(fid, fid * 100_000_000, value=1000)
        follower.push(fid, fid * 100_000_000 + 10_000, value=50)
    app.store.dispatch(C.StartLive())
    _wait(qtbot, lambda: app.window.tick() or app.window.left_view.image is not None)
    assert app.window.right_view.image.dtype.name == "uint16"
    app.shutdown()
    assert not clock.initialised and not follower.initialised and pyspin.system.released
