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


def test_hardware_mode_explains_that_camera_adapters_do_not_exist_yet(tmp_path):
    with pytest.raises(SystemExit, match="PySpin camera adapter"):
        build(["--config", str(_config(tmp_path))])
