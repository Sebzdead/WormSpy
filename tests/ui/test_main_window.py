"""MainWindow: views side by side, sidebar, alarm bar and status bar (ADR 0004)."""

import numpy as np
from PySide6.QtCore import Qt

from wormspy.control import AppState, StateStore
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.core.frames import Frame, FrameHub
from wormspy.tracking.estimators import Estimate
from wormspy.ui.bridge import StateBridge
from wormspy.ui.main_window import MainWindow, StatusInfo


class Sources:
    def __init__(self):
        self.status = StatusInfo(xyz_um=(25012.4, 24987.9, 5000.0), disk_free_bytes=812e9)
        self.overlay = None


def _window(qtbot):
    store, hub, sources = StateStore(AppState()), FrameHub(), Sources()
    effects = []
    store.on_effect(effects.append)
    window = MainWindow(StateBridge(store), hub, left=("bf", "Brightfield"),
                        right=("gcamp", "Fluorescence"), status=lambda: sources.status,
                        overlay=lambda: sources.overlay)
    qtbot.addWidget(window)
    return window, store, hub, sources, effects


def _publish(hub, channel, fid, dtype=np.uint16):
    hub.publish(channel, Frame(np.zeros((40, 60), dtype), device_frame_id=fid,
                               capture_ts=fid / 10.0))


def test_brightfield_left_fluorescence_right_and_the_sidebar(qtbot):
    window, *_ = _window(qtbot)
    assert window.left_view.channel == "bf" and window.right_view.channel == "gcamp"
    assert window.controls.live_button is not None


def test_each_tick_refreshes_the_views_and_measures_frame_rate(qtbot):
    window, _, hub, _, _ = _window(qtbot)
    for fid in range(0, 20, 2):                  # the UI only sees every other frame
        _publish(hub, "gcamp", fid)
        _publish(hub, "bf", fid, np.uint8)
        window.tick()
    assert window.right_view.image is not None
    assert "10.0 fps" in window.fps_label.text()  # from frame ids over capture times


def test_status_bar_shows_stage_position_and_free_disk(qtbot):
    window, *_ = _window(qtbot)
    window.tick()
    assert "25012" in window.xyz_label.text() and "5000" in window.xyz_label.text()
    assert "812 GB" in window.disk_label.text()


def test_alarms_are_listed_and_can_be_acknowledged(qtbot):
    window, store, *_ = _window(qtbot)
    assert window.alarm_bar.isHidden()
    store.dispatch(E.AlarmRaised(kind="disk_space", message="Less than 10 GB left"))
    assert window.alarm_bar.messages() == ["Less than 10 GB left"]
    assert not window.alarm_bar.isHidden()
    qtbot.mouseClick(window.alarm_bar.acknowledge_buttons()[0], Qt.MouseButton.LeftButton)
    assert store.state.alarms == () and window.alarm_bar.messages() == []
    assert window.alarm_bar.isHidden()


def test_refused_commands_are_explained_in_the_status_bar(qtbot):
    window, store, *_ = _window(qtbot)
    store.dispatch(C.StartRecording())
    assert "live" in window.statusBar().currentMessage().lower()


def test_double_click_on_a_view_centres_that_point(qtbot):
    window, store, hub, _, effects = _window(qtbot)
    store.dispatch(C.StartLive())
    store.dispatch(E.CalibrationCompleted(residual_px={"bf": 0.1}))
    _publish(hub, "bf", 1, np.uint8)
    window.tick()
    window.left_view.centre_requested.emit(12.5, 30.0)
    assert effects[-1] == X.CentreOn(channel="bf", x_px=12.5, y_px=30.0)


def test_the_tracking_overlay_is_drawn_on_the_source_channel(qtbot):
    window, _, _hub, sources, _ = _window(qtbot)
    sources.overlay = ("bf", Estimate(point_px=(20.0, 10.0), mask=np.ones((40, 60), bool)))
    window.tick()
    assert window.left_view.tracked_point == (20.0, 10.0)
    assert window.right_view.tracked_point is None
    sources.overlay = None
    window.tick()
    assert window.left_view.tracked_point is None
