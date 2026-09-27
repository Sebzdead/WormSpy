"""StateStore: thread-safe owner of the current state (ADR 0010)."""

import threading

from wormspy.control import AppState, Mode, StateStore
from wormspy.control import commands as C
from wormspy.control import effects as X
from wormspy.control import events as E


def test_dispatch_updates_the_published_state():
    store = StateStore(AppState())
    store.dispatch(C.StartLive())
    assert store.state.mode is Mode.LIVE


def test_subscribers_see_each_new_state_but_not_unchanged_ones():
    store = StateStore(AppState())
    seen = []
    store.subscribe(seen.append)
    store.dispatch(C.StartLive())
    store.dispatch(C.StartLive())          # rejected: already live
    store.dispatch(C.SetFocus())           # accepted, but only requests an effect
    assert [s.mode for s in seen] == [Mode.LIVE]


def test_unsubscribe_stops_notifications():
    store = StateStore(AppState())
    seen = []
    unsubscribe = store.subscribe(seen.append)
    unsubscribe()
    store.dispatch(C.StartLive())
    assert seen == []


def test_effects_go_to_the_effect_handler_in_order():
    store = StateStore(AppState())
    effects = []
    store.on_effect(effects.append)
    store.dispatch(C.StartLive())
    store.dispatch(C.SetFocus())
    assert effects == [X.StartAcquisition(), X.CaptureFocus()]


def test_rejections_are_reported_with_their_reason():
    store = StateStore(AppState())
    rejections = []
    store.on_rejected(lambda message, reason: rejections.append((message, reason)))
    store.dispatch(C.StartRecording())
    assert rejections and rejections[0][0] == C.StartRecording()
    assert "live" in rejections[0][1].lower()


def test_an_effect_handler_can_report_back_synchronously_without_deadlock():
    store = StateStore(AppState())

    def executor(effect):
        if effect == X.CaptureFocus():
            store.dispatch(E.FocusCaptured(z_um=42.0))

    store.on_effect(executor)
    store.dispatch(C.SetFocus())
    assert store.state.focus_z_um == 42.0


def test_concurrent_dispatches_are_all_applied():
    store = StateStore(AppState())
    store.dispatch(C.StartLive())
    store.dispatch(E.SessionOpened(path="/data/s"))
    store.dispatch(C.StartRecording())

    def report_drops():
        for _ in range(500):
            store.dispatch(E.FramesDropped(channel="fluorescence", count=1))

    threads = [threading.Thread(target=report_drops) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.state.recording_drops == 2000


def test_subscribers_never_see_an_older_state_after_a_newer_one():
    store = StateStore(AppState())
    store.dispatch(C.StartLive())
    store.dispatch(E.SessionOpened(path="/data/s"))
    store.dispatch(C.StartRecording())
    seen = []
    store.subscribe(lambda s: seen.append(s.recording_drops))

    def report_drops():
        for _ in range(2000):
            store.dispatch(E.FramesDropped(channel="fluorescence", count=1))

    threads = [threading.Thread(target=report_drops) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == sorted(seen)            # delivery order is monotonic
    assert seen[-1] == 16000               # and the final state is always delivered last
