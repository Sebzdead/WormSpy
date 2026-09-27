"""Idle → Live → Recording, sessions and recording numbering (ADR 0010, ADR 0007)."""

from wormspy.control import AppState, Mode, reduce
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E


def _live_with_session(path="/data/2026-09-27_w1"):
    state = reduce(AppState(), C.StartLive()).state
    return reduce(state, E.SessionOpened(path=path)).state


def test_app_starts_idle_without_a_session():
    state = AppState()
    assert state.mode is Mode.IDLE
    assert state.session_dir is None


def test_start_live_begins_acquisition():
    t = reduce(AppState(), C.StartLive())
    assert t.state.mode is Mode.LIVE
    assert t.effects == (X.StartAcquisition(),)
    assert t.rejected is None


def test_rejected_commands_leave_state_unchanged_and_say_why():
    state = AppState()
    t = reduce(state, C.StartRecording())
    assert t.state == state
    assert t.effects == ()
    assert "live" in t.rejected.lower()


def test_new_session_asks_the_executor_to_create_the_folder():
    t = reduce(AppState(), C.NewSession(metadata={"animal_id": "w1"}))
    assert t.effects == (X.CreateSession(metadata={"animal_id": "w1"}),)
    assert t.state.session_dir is None          # not open until the executor reports back
    opened = reduce(t.state, E.SessionOpened(path="/data/2026-09-27_w1")).state
    assert opened.session_dir == "/data/2026-09-27_w1"


def test_recording_requires_an_open_session():
    live = reduce(AppState(), C.StartLive()).state
    t = reduce(live, C.StartRecording())
    assert "session" in t.rejected.lower()


def test_start_and_stop_recording_number_recordings_within_a_session():
    state = _live_with_session()
    t = reduce(state, C.StartRecording())
    assert t.state.mode is Mode.RECORDING
    assert t.effects == (X.OpenRecording(session_dir="/data/2026-09-27_w1", name="rec-001"),)
    t = reduce(t.state, C.StopRecording())
    assert t.state.mode is Mode.LIVE
    assert t.effects == (X.CloseRecording(),)
    t = reduce(t.state, C.StartRecording())
    assert t.effects == (X.OpenRecording(session_dir="/data/2026-09-27_w1", name="rec-002"),)


def test_a_new_session_restarts_recording_numbers():
    state = _live_with_session()
    state = reduce(reduce(state, C.StartRecording()).state, C.StopRecording()).state
    state = reduce(state, E.SessionOpened(path="/data/2026-09-27_w2")).state
    t = reduce(state, C.StartRecording())
    assert t.effects == (X.OpenRecording(session_dir="/data/2026-09-27_w2", name="rec-001"),)


def test_toggle_recording_resolves_against_current_state():
    state = _live_with_session()
    t = reduce(state, C.ToggleRecording())
    assert t.state.mode is Mode.RECORDING
    t = reduce(t.state, C.ToggleRecording())
    assert t.state.mode is Mode.LIVE


def test_cannot_stop_live_or_change_session_while_recording():
    recording = reduce(_live_with_session(), C.StartRecording()).state
    assert reduce(recording, C.StopLive()).rejected
    assert reduce(recording, C.NewSession(metadata={})).rejected


def test_stop_live_ends_acquisition():
    live = reduce(AppState(), C.StartLive()).state
    t = reduce(live, C.StopLive())
    assert t.state.mode is Mode.IDLE
    assert t.effects == (X.StopAcquisition(),)


def test_shutdown_closes_an_open_recording_before_stopping_acquisition():
    recording = reduce(_live_with_session(), C.StartRecording()).state
    t = reduce(recording, C.Shutdown())
    assert t.state.mode is Mode.IDLE
    assert t.effects == (X.CloseRecording(), X.StopAcquisition())
