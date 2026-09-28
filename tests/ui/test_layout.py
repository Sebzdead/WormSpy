"""Layout and theme: feeds fill the width at the camera aspect, controls below, dark theme."""

import numpy as np
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from wormspy.control import AppState, StateStore
from wormspy.control import commands as C
from wormspy.core.frames import Frame, FrameHub
from wormspy.ui import theme
from wormspy.ui.bridge import StateBridge
from wormspy.ui.main_window import MainWindow, StatusInfo


def _window(qtbot, width=1600, height=1000):
    theme.apply(QApplication.instance())
    store, hub = StateStore(AppState()), FrameHub()
    window = MainWindow(StateBridge(store), hub, left=("bf", "Brightfield"),
                        right=("gcamp", "Fluorescence"),
                        status=lambda: StatusInfo(xyz_um=(1.0, 2.0, 3.0), disk_free_bytes=1e11))
    qtbot.addWidget(window)
    window.resize(width, height)
    window.show()
    qtbot.waitExposed(window)
    for fid in (1, 2):
        hub.publish("bf", Frame(np.zeros((1200, 1920), np.uint8), fid, fid / 10))
        hub.publish("gcamp", Frame(np.zeros((1200, 1920), np.uint16), fid, fid / 10))
        window.tick()
    qtbot.wait(50)
    return window, store


def _aspect(widget):
    return widget.width() / widget.height()


def test_each_feed_has_exactly_the_camera_aspect_ratio(qtbot):
    window, _ = _window(qtbot)
    for view in (window.left_view, window.right_view):
        assert abs(_aspect(view.canvas) - 1920 / 1200) < 0.02


def test_the_feeds_use_the_whole_width_of_the_window(qtbot):
    window, _ = _window(qtbot)
    used = window.left_view.canvas.width() + window.right_view.canvas.width()
    assert used > 0.9 * window.width()


def test_all_controls_sit_below_the_feeds_not_beside_them(qtbot):
    window, _ = _window(qtbot)
    feeds_bottom = window.left_view.canvas.mapTo(window, window.left_view.canvas.rect()
                                                 .bottomLeft()).y()
    controls_top = window.controls.mapTo(window, window.controls.rect().topLeft()).y()
    assert controls_top > feeds_bottom
    assert window.controls.width() > 0.9 * window.width()


def test_a_short_window_shrinks_the_feeds_but_keeps_their_aspect(qtbot):
    window, _ = _window(qtbot, width=1600, height=760)
    for view in (window.left_view, window.right_view):
        assert abs(_aspect(view.canvas) - 1.6) < 0.02
    controls_bottom = window.controls.mapTo(window, window.controls.rect().bottomLeft()).y()
    assert controls_bottom <= window.height()              # nothing pushed off screen


def test_dark_theme_with_the_wormspy_accent(qtbot):
    window, _store = _window(qtbot)
    palette = QApplication.instance().palette()
    assert palette.color(QPalette.ColorRole.Window).lightness() < 40
    assert palette.color(QPalette.ColorRole.WindowText).lightness() > 180
    assert window.header.wordmark.text() == "WormsPy"
    assert theme.ACCENT in window.header.wordmark.styleSheet()


def test_the_header_shows_mode_chips(qtbot):
    window, store = _window(qtbot)
    assert window.header.mode_chip.text() == "IDLE"
    store.dispatch(C.StartLive())
    assert window.header.mode_chip.text() == "LIVE"


def test_the_window_can_shrink_back_after_being_large(qtbot):
    # The feeds must not pin the window at its current size: snapping a large window to a
    # smaller screen has to take effect, and the feeds refit at the camera aspect.
    window, _ = _window(qtbot, width=1500, height=1000)
    window.resize(1320, 760)
    qtbot.wait(50)
    assert (window.width(), window.height()) == (1320, 760)
    for view in (window.left_view, window.right_view):
        assert abs(_aspect(view.canvas) - 1.6) < 0.02
    controls_bottom = window.controls.mapTo(window, window.controls.rect().bottomLeft()).y()
    assert controls_bottom <= window.height()


def test_the_window_fits_a_laptop_screen(qtbot):
    window, _ = _window(qtbot)
    assert window.minimumSizeHint().width() <= 1320     # smallest Mac laptop default: 1440
