"""CameraCard: presets apply at once; edits wait for Apply; locked when acquisition is busy."""

from dataclasses import replace

from PySide6.QtCore import Qt

from wormspy.control import AppState, StateStore, TrackingStatus
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.presets import CameraSettings
from wormspy.ui.bridge import StateBridge
from wormspy.ui.controls import ControlPanel

DEFAULT = CameraSettings(fps=10, clock_exposure_us=14_000, clock_gain_db=0,
                         follower_exposure_us=5_000, follower_gain_db=0)
DIM = replace(DEFAULT, clock_exposure_us=40_000, clock_gain_db=6)


def _card(qtbot, **state):
    initial = AppState(camera_settings=DEFAULT, preset="Default",
                       presets=(("Default", DEFAULT), ("dim neurons", DIM)))
    store = StateStore(replace(initial, **state))
    effects = []
    store.on_effect(effects.append)
    panel = ControlPanel(StateBridge(store), camera_labels=("GCaMP", "Brightfield"))
    qtbot.addWidget(panel)
    return panel.cameras, store, effects


def test_the_fields_show_the_applied_settings_in_ms(qtbot):
    card, _, _ = _card(qtbot)
    assert card.fps.value() == 10 and card.clock_exposure.value() == 14.0
    assert card.follower_exposure.value() == 5.0
    assert card.preset_box.currentText() == "Default"
    assert not card.apply_button.isEnabled() and not card.delete_button.isEnabled()


def test_choosing_a_preset_applies_it(qtbot):
    card, store, effects = _card(qtbot)
    card.preset_box.activated.emit(card.preset_box.findText("dim neurons"))
    assert effects[-1] == X.ApplyCameraSettings(settings=DIM, preset="dim neurons")
    store.dispatch(E.CameraSettingsApplied(settings=DIM, preset="dim neurons"))
    assert card.clock_exposure.value() == 40.0 and card.clock_gain.value() == 6.0
    assert card.delete_button.isEnabled()


def test_edits_wait_for_apply_and_survive_other_state_changes(qtbot):
    card, store, effects = _card(qtbot)
    card.clock_exposure.setValue(20.0)
    assert card.apply_button.isEnabled()
    assert not card.save_button.isEnabled()                  # save applied settings only
    store.dispatch(C.StartLive())                             # an unrelated state change
    assert card.clock_exposure.value() == 20.0
    qtbot.mouseClick(card.apply_button, Qt.MouseButton.LeftButton)
    assert effects[-1] == X.ApplyCameraSettings(
        settings=replace(DEFAULT, clock_exposure_us=20_000), preset=None)


def test_an_exposure_too_long_for_the_rate_is_refused_with_the_reason(qtbot):
    card, _store, effects = _card(qtbot)
    reasons = []
    card._bridge.rejected.connect(reasons.append)
    card.clock_exposure.setValue(120.0)
    assert "must be under" in card.apply_button.toolTip()
    qtbot.mouseClick(card.apply_button, Qt.MouseButton.LeftButton)
    assert effects == [] and "must be under" in reasons[-1]


def test_edited_settings_show_as_unsaved_and_can_be_saved(qtbot):
    edited = replace(DEFAULT, fps=20)
    card, _, effects = _card(qtbot, camera_settings=edited, preset=None)
    assert card.preset_box.currentText() == "(edited)"
    assert card.save_button.isEnabled()
    card.save_preset("larva")
    assert effects[-1] == X.WritePresets(presets=(("Default", DEFAULT), ("dim neurons", DIM),
                                                  ("larva", edited)))
    assert card.preset_box.currentText() == "larva"


def test_deleting_removes_the_current_preset(qtbot):
    card, _, effects = _card(qtbot, camera_settings=DIM, preset="dim neurons")
    card.delete_preset("dim neurons")
    assert [card.preset_box.itemText(i) for i in range(card.preset_box.count())] == [
        "Default", "(edited)"]
    assert isinstance(effects[-1], X.WritePresets)


def test_everything_is_locked_while_tracking(qtbot):
    card, _, _ = _card(qtbot, tracking=TrackingStatus.TRACKING)
    for widget in (card.preset_box, card.fps, card.clock_exposure, card.apply_button,
                   card.save_button):
        assert not widget.isEnabled()
    assert "Locked" in card.toolTip()
