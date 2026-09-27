"""StateBridge: store updates from any thread arrive as Qt signals on the GUI thread."""

import threading

from PySide6.QtCore import QThread

from wormspy.control import AppState, Mode, StateStore
from wormspy.control import commands as C
from wormspy.ui.bridge import StateBridge


def test_state_changes_from_a_worker_thread_arrive_on_the_gui_thread(qtbot):
    store = StateStore(AppState())
    bridge = StateBridge(store)
    received = []
    bridge.state_changed.connect(lambda s: received.append((s, QThread.currentThread())))
    with qtbot.waitSignal(bridge.state_changed, timeout=2000):
        threading.Thread(target=store.dispatch, args=(C.StartLive(),)).start()
    state, thread = received[-1]
    assert state.mode is Mode.LIVE
    assert thread is bridge.thread()          # the GUI thread, safe for widgets


def test_rejections_arrive_with_their_reason(qtbot):
    store = StateStore(AppState())
    bridge = StateBridge(store)
    with qtbot.waitSignal(bridge.rejected, timeout=2000) as blocker:
        store.dispatch(C.StartRecording())
    assert "live" in blocker.args[0].lower()


def test_the_bridge_dispatches_commands_and_knows_the_current_state(qtbot):
    store = StateStore(AppState())
    bridge = StateBridge(store)
    bridge.dispatch(C.StartLive())
    assert bridge.state.mode is Mode.LIVE
