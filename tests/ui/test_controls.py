"""ControlPanel: buttons issue Commands; every enabled state and label comes from AppState."""

from PySide6.QtCore import Qt

from wormspy.control import AppState, Mode, StateStore, TrackingStatus
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.ui.bridge import StateBridge
from wormspy.ui.controls import ControlPanel


def _panel(qtbot):
    store = StateStore(AppState())
    effects = []
    store.on_effect(effects.append)
    panel = ControlPanel(StateBridge(store))
    qtbot.addWidget(panel)
    return panel, store, effects


def _click(qtbot, button):
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _live_with_session(store):
    store.dispatch(C.StartLive())
    store.dispatch(E.SessionOpened(path="/data/2026-09-27_w1"))


def test_idle_only_offers_what_is_possible(qtbot):
    panel, _, _ = _panel(qtbot)
    assert panel.live_button.isEnabled() and panel.live_button.text() == "Start live"
    for button in (panel.record_button, panel.track_button, panel.calibrate_button,
                   panel.mark_button, panel.return_focus_button):
        assert not button.isEnabled(), button.text()


def test_the_live_button_toggles_live_view(qtbot):
    panel, store, _ = _panel(qtbot)
    _click(qtbot, panel.live_button)
    assert store.state.mode is Mode.LIVE and panel.live_button.text() == "Stop live"
    _click(qtbot, panel.live_button)
    assert store.state.mode is Mode.IDLE


def test_new_session_sends_the_metadata_form(qtbot):
    panel, _, effects = _panel(qtbot)
    panel.animal_id.setText("w12")
    panel.operator.setText("SW")
    panel.notes.setPlainText("on food")
    _click(qtbot, panel.new_session_button)
    assert effects[-1] == X.CreateSession(metadata={"animal_id": "w12", "operator": "SW",
                                                    "condition": "", "notes": "on food"})


def test_recording_needs_live_and_a_session_and_locks_the_session_controls(qtbot):
    panel, store, _ = _panel(qtbot)
    store.dispatch(C.StartLive())
    assert not panel.record_button.isEnabled()
    store.dispatch(E.SessionOpened(path="/data/2026-09-27_w1"))
    assert panel.record_button.isEnabled()
    assert "2026-09-27_w1" in panel.session_label.text()
    _click(qtbot, panel.record_button)
    assert store.state.mode is Mode.RECORDING
    assert "rec-001" in panel.recording_label.text()
    assert not panel.new_session_button.isEnabled() and not panel.live_button.isEnabled()
    assert panel.mark_button.isEnabled()
    _click(qtbot, panel.record_button)
    assert store.state.mode is Mode.LIVE


def test_event_markers_carry_the_typed_label(qtbot):
    panel, store, effects = _panel(qtbot)
    _live_with_session(store)
    store.dispatch(C.StartRecording())
    panel.event_label.setText("glycerol")
    _click(qtbot, panel.mark_button)
    assert effects[-1] == X.WriteEvent(label="glycerol")


def test_drops_are_shown_on_the_recording_line(qtbot):
    panel, store, _ = _panel(qtbot)
    _live_with_session(store)
    store.dispatch(C.StartRecording())
    store.dispatch(E.FramesDropped(channel="gcamp", count=3))
    assert "3 dropped" in panel.recording_label.text()


def test_tracking_needs_a_calibration_and_shows_its_status(qtbot):
    panel, store, _ = _panel(qtbot)
    store.dispatch(C.StartLive())
    assert not panel.track_button.isEnabled()
    store.dispatch(E.CalibrationCompleted(residual_px={"bf": 0.1}))
    assert panel.track_button.isEnabled()
    _click(qtbot, panel.track_button)
    assert store.state.tracking is TrackingStatus.TRACKING
    assert panel.tracking_label.text() == "Tracking"
    assert not panel.estimator_box.isEnabled()          # locked while tracking
    store.dispatch(E.TrackingStatusChanged(status=TrackingStatus.LOST))
    assert panel.tracking_label.text() == "Target lost"
    _click(qtbot, panel.track_button)
    assert store.state.tracking is TrackingStatus.OFF


def test_choosing_an_estimator_selects_it_and_its_source_channel(qtbot):
    panel, store, _ = _panel(qtbot)
    panel.estimator_box.setCurrentIndex(panel.estimator_box.findData("bright_marker"))
    assert store.state.estimator == "bright_marker"
    assert store.state.source_channel == "fluorescence"
    assert panel.source_box.currentData() == "fluorescence"


def test_focus_buttons(qtbot):
    panel, store, effects = _panel(qtbot)
    _click(qtbot, panel.set_focus_button)
    assert effects[-1] == X.CaptureFocus()
    store.dispatch(E.FocusCaptured(z_um=4321.0))
    assert panel.return_focus_button.isEnabled() and "4321" in panel.focus_label.text()
    _click(qtbot, panel.return_focus_button)
    assert effects[-1] == X.MoveZTo(z_um=4321.0)


def test_calibration_button_and_status(qtbot):
    panel, store, effects = _panel(qtbot)
    store.dispatch(C.StartLive())
    assert panel.calibration_label.text() == "Not calibrated"
    _click(qtbot, panel.calibrate_button)
    assert effects[-1] == X.RunCalibration()
    assert panel.calibration_label.text() == "Calibrating…"
    assert not panel.calibrate_button.isEnabled() and not panel.live_button.isEnabled()
    store.dispatch(E.CalibrationCompleted(residual_px={"gcamp": 0.12, "bf": 0.08}))
    assert panel.calibration_label.text().startswith("Calibrated")
