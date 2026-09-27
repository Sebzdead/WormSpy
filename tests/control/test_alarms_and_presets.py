"""Presets, dropped-frame alarms and executor failures (ADR 0006, ADR 0010)."""

from wormspy.control import Alarm, AppState, Mode, TrackingStatus, reduce
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E


def _live():
    return reduce(AppState(), C.StartLive()).state


def _recording():
    state = reduce(_live(), E.SessionOpened(path="/data/s")).state
    return reduce(state, C.StartRecording()).state


# --- presets (ADR 0006) -----------------------------------------------------------------------

def test_presets_can_be_applied_while_not_recording():
    t = reduce(_live(), C.ApplyPreset(name="calcium-10Hz"))
    assert t.state.preset == "calcium-10Hz"
    assert t.effects == (X.ApplyPreset(name="calcium-10Hz"),)


def test_presets_are_locked_while_recording():
    assert reduce(_recording(), C.ApplyPreset(name="other")).rejected


# --- alarms ------------------------------------------------------------------------------------

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
    on = reduce(_live(), C.SetTracking(enabled=True)).state
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
