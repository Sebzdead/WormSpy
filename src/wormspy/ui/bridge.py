"""StateBridge: connects the StateStore (any thread) to Qt widgets (GUI thread only).

Store listeners run on whichever thread dispatched. Emitting a Qt signal from there is safe:
Qt queues the call to receivers living on the GUI thread. Widgets therefore only ever see state
on their own thread (ADR 0004).
"""

from PySide6.QtCore import QObject, Signal

from wormspy.control import AppState, StateStore


class StateBridge(QObject):
    state_changed = Signal(object)    # AppState
    rejected = Signal(str)            # human-readable reason a Command was refused

    def __init__(self, store: StateStore) -> None:
        super().__init__()
        self._store = store
        store.subscribe(self.state_changed.emit)
        store.on_rejected(lambda _command, reason: self.rejected.emit(reason))

    @property
    def state(self) -> AppState:
        return self._store.state

    def dispatch(self, message: object) -> None:
        self._store.dispatch(message)
