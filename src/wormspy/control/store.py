"""StateStore: the single, thread-safe owner of the application state (ADR 0010).

Any thread may `dispatch` a Command or Event: the UI, the gamepad, the executor, workers.
Reduction is serialised under a lock. Listeners are called *outside* the lock, so an effect
handler may dispatch an Event back (e.g. CaptureFocus → FocusCaptured) without deadlocking.
State listeners see strictly newer states. Each state change gets a version number, and a
delivery is skipped if a newer version has already been delivered. Subscribers may therefore
miss intermediate states under contention, but never see an older state after a newer one, and
the final state is always delivered. Listeners run on the dispatching thread. A Qt UI must
marshal them onto its own thread.
"""

import threading
from collections.abc import Callable

from wormspy.control.reducer import Transition, reduce
from wormspy.control.state import AppState

StateListener = Callable[[AppState], None]
EffectHandler = Callable[[object], None]
RejectionListener = Callable[[object, str], None]


class StateStore:
    def __init__(self, initial: AppState) -> None:
        self._lock = threading.Lock()
        self._state = initial
        self._version = 0
        self._delivery_lock = threading.RLock()  # re-entrant: listeners may dispatch
        self._delivered_version = 0
        self._state_listeners: list[StateListener] = []
        self._effect_handlers: list[EffectHandler] = []
        self._rejection_listeners: list[RejectionListener] = []

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    def subscribe(self, listener: StateListener) -> Callable[[], None]:
        self._state_listeners.append(listener)
        return lambda: self._state_listeners.remove(listener)

    def on_effect(self, handler: EffectHandler) -> None:
        self._effect_handlers.append(handler)

    def on_rejected(self, listener: RejectionListener) -> None:
        self._rejection_listeners.append(listener)

    def dispatch(self, message: object) -> Transition:
        with self._lock:
            before = self._state
            transition = reduce(before, message)
            self._state = transition.state
            changed = transition.rejected is None and transition.state != before
            if changed:
                self._version += 1
            version = self._version

        if transition.rejected is not None:
            for listener in list(self._rejection_listeners):
                listener(message, transition.rejected)
            return transition
        if changed:
            self._deliver(version, transition.state)
        for effect in transition.effects:
            for handler in list(self._effect_handlers):
                handler(effect)
        return transition

    def _deliver(self, version: int, state: AppState) -> None:
        with self._delivery_lock:
            if version <= self._delivered_version:
                return  # a newer state has already been delivered
            self._delivered_version = version
            for listener in list(self._state_listeners):
                listener(state)
