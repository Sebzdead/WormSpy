"""Presets, dropped-frame alarms and executor failures (ADR 0006, ADR 0010)."""

from dataclasses import replace

from wormspy.control import Alarm, AppState, Mode, TrackingStatus, reduce
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E
from wormspy.presets import CameraSettings


def _live():
    return reduce(AppState(), C.StartLive()).state


def _recording():
    state = reduce(_live(), E.SessionOpened(path="/data/s")).state
    return reduce(state, C.StartRecording()).state


# --- presets (ADR 0006) -----------------------------------------------------------------------

DEFAULT = CameraSettings(fps=10, clock_exposure_us=14_000, clock_gain_db=0,
                         follower_exposure_us=5_000, follower_gain_db=0)
BRIGHT = replace(DEFAULT, clock_exposure_us=40_000)


def _with_presets(state):
    return replace(state, camera_settings=DEFAULT, preset="Default",
                   presets=(("Default", DEFAULT), ("dim neurons", BRIGHT)))


def test_a_preset_is_applied_by_name_and_takes_effect_when_the_executor_confirms():
    t = reduce(_with_presets(_live()), C.ApplyPreset(name="dim neurons"))
    assert t.effects == (X.ApplyCameraSettings(settings=BRIGHT, preset="dim neurons"),)
    assert t.state.camera_settings == DEFAULT                  # not yet applied
    state = reduce(t.state, E.CameraSettingsApplied(settings=BRIGHT, preset="dim neurons")).state
    assert (state.camera_settings, state.preset) == (BRIGHT, "dim neurons")


def test_edited_settings_lose_the_preset_name_unless_they_match_one():
    state = _with_presets(_live())
    edited = replace(DEFAULT, follower_gain_db=6)
    assert reduce(state, C.ApplyCameraSettings(edited)).effects == (
        X.ApplyCameraSettings(settings=edited, preset=None),)
    assert reduce(state, C.ApplyCameraSettings(BRIGHT)).effects == (
        X.ApplyCameraSettings(settings=BRIGHT, preset="dim neurons"),)


def test_invalid_settings_are_rejected_with_the_reason():
    too_long = replace(DEFAULT, clock_exposure_us=120_000)
    t = reduce(_with_presets(_live()), C.ApplyCameraSettings(too_long))
    assert "exposure" in t.rejected and t.effects == ()


def test_camera_settings_are_locked_while_recording_tracking_or_calibrating():
    recording = _with_presets(_recording())
    tracking = replace(_with_presets(_live()), tracking=TrackingStatus.TRACKING)
    calibrating = replace(_with_presets(_live()), calibrating=True)
    for state in (recording, tracking, calibrating):
        assert reduce(state, C.ApplyPreset(name="dim neurons")).rejected
        assert reduce(state, C.ApplyCameraSettings(BRIGHT)).rejected


def test_settings_can_be_changed_while_idle():
    state = _with_presets(AppState())
    assert reduce(state, C.ApplyPreset(name="dim neurons")).effects


def test_unknown_presets_are_rejected():
    assert reduce(_with_presets(_live()), C.ApplyPreset(name="nope")).rejected


def test_a_failure_to_apply_raises_an_alarm_and_keeps_the_old_settings():
    state = reduce(_with_presets(_live()), E.CameraSettingsFailed(reason="node locked")).state
    assert state.camera_settings == DEFAULT
    assert any(a.kind == "camera_settings" for a in state.alarms)


def test_saving_stores_the_applied_settings_and_persists_the_list():
    state = replace(_with_presets(_live()), camera_settings=replace(DEFAULT, fps=20), preset=None)
    t = reduce(state, C.SavePreset(name="  larva 20Hz "))
    assert t.state.presets[-1] == ("larva 20Hz", replace(DEFAULT, fps=20))
    assert t.state.preset == "larva 20Hz"
    assert t.effects == (X.WritePresets(presets=t.state.presets),)


def test_saving_under_an_existing_name_replaces_it_in_place():
    state = replace(_with_presets(_live()), camera_settings=replace(DEFAULT, fps=5))
    presets = reduce(state, C.SavePreset(name="dim neurons")).state.presets
    assert [n for n, _ in presets] == ["Default", "dim neurons"]
    assert presets[1][1].fps == 5


def test_default_can_be_neither_overwritten_nor_deleted():
    state = _with_presets(_live())
    assert reduce(state, C.SavePreset(name="Default")).rejected
    assert reduce(state, C.DeletePreset(name="Default")).rejected
    assert reduce(state, C.SavePreset(name="   ")).rejected


def test_deleting_the_current_preset_leaves_the_settings_but_drops_the_name():
    state = replace(_with_presets(_live()), camera_settings=BRIGHT, preset="dim neurons")
    t = reduce(state, C.DeletePreset(name="dim neurons"))
    assert [n for n, _ in t.state.presets] == ["Default"]
    assert (t.state.camera_settings, t.state.preset) == (BRIGHT, None)
    assert t.effects == (X.WritePresets(presets=t.state.presets),)


def test_dropped_frames_are_counted_per_recording_and_raise_an_alarm():
    state = reduce(_recording(), E.FramesDropped(channel="fluorescence", count=2)).state
    state = reduce(state, E.FramesDropped(channel="behaviour", count=1)).state
    assert state.recording_drops == 3
    assert [a.kind for a in state.alarms] == ["frames_dropped"]   # one alarm, updated in place
    assert "3" in state.alarms[0].message


def test_drop_count_restarts_with_each_recording():
    state = reduce(_recording(), E.FramesDropped(channel="fluorescence", count=2)).state
    state = reduce(reduce(state, C.StopRecording()).state, C.StartRecording()).state
    assert state.recording_drops == 0


def test_alarms_stay_until_acknowledged():
    state = reduce(AppState(), E.AlarmRaised(kind="disk_space", message="Less than 10 GB")).state
    assert state.alarms == (Alarm(kind="disk_space", message="Less than 10 GB"),)
    state = reduce(state, C.AcknowledgeAlarm(kind="disk_space")).state
    assert state.alarms == ()


def test_a_worker_can_clear_its_own_alarm():
    state = reduce(AppState(), E.AlarmRaised(kind="disk_space", message="low")).state
    assert reduce(state, E.AlarmCleared(kind="disk_space")).state.alarms == ()


# --- executor failures roll the state back -----------------------------------------------------

def test_failed_recording_returns_to_live_and_raises_an_alarm():
    state = reduce(_recording(), E.RecordingFailed(reason="Disk full")).state
    assert state.mode is Mode.LIVE
    assert state.recording_name is None
    assert any(a.kind == "recording_failed" and "Disk full" in a.message for a in state.alarms)


def test_failed_acquisition_returns_to_idle_with_tracking_off():
    calibrated = reduce(_live(), E.CalibrationCompleted(residual_px={"bf": 0.1})).state
    on = reduce(calibrated, C.SetTracking(enabled=True)).state
    assert on.tracking is TrackingStatus.TRACKING          # precondition: tracking really on
    state = reduce(on, E.AcquisitionFailed(reason="Camera 2 not found")).state
    assert state.mode is Mode.IDLE
    assert state.tracking is TrackingStatus.OFF
    assert any(a.kind == "acquisition_failed" for a in state.alarms)


def test_acquisition_failure_during_recording_also_ends_the_recording():
    t = reduce(_recording(), E.AcquisitionFailed(reason="USB disconnect"))
    assert t.state.mode is Mode.IDLE
    assert X.CloseRecording() in t.effects        # finalise what was written so far


def test_a_failed_recording_never_reuses_its_folder_name():
    state = reduce(_recording(), E.RecordingFailed(reason="Disk full")).state
    t = reduce(state, C.StartRecording())
    assert t.effects == (X.OpenRecording(session_dir="/data/s", name="rec-002"),)


def test_a_failed_recording_is_closed_so_its_files_are_finalised():
    t = reduce(_recording(), E.RecordingFailed(reason="Disk full"))
    assert t.effects == (X.CloseRecording(),)
    assert reduce(_live(), E.RecordingFailed(reason="late report")).effects == ()


def test_acquisition_failure_stops_a_running_tracker():
    calibrated = reduce(_live(), E.CalibrationCompleted(residual_px={"bf": 0.1})).state
    on = reduce(calibrated, C.SetTracking(enabled=True)).state
    t = reduce(on, E.AcquisitionFailed(reason="USB disconnect"))
    assert t.effects[0] == X.SetTracker(enabled=False, estimator=on.estimator,
                                        source_channel=on.source_channel)
