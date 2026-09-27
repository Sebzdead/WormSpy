"""MainWindow (ADR 0004): brightfield and fluorescence side by side, the control sidebar on the
right, an alarm bar above the views and a status bar below.

A QTimer calls `tick()` about 25 times a second. It pulls the newest frames from the FrameHub,
the tracker's latest estimate (overlay) and status readouts. Acquisition never waits for the UI.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from wormspy.control import AppState
from wormspy.control import commands as C
from wormspy.core.frames import FrameHub
from wormspy.ui.bridge import StateBridge
from wormspy.ui.controls import ControlPanel
from wormspy.ui.live_view import LiveView


@dataclass(frozen=True)
class StatusInfo:
    xyz_um: tuple[float, float, float] | None
    disk_free_bytes: float | None


class AlarmBar(QFrame):
    """One row per active alarm, each with an Acknowledge button. Hidden when all is well."""

    def __init__(self, bridge: StateBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self.setStyleSheet("AlarmBar { background: #5a1a1a; border-radius: 4px; }"
                           "QLabel { color: white; font-weight: bold; }")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._rows: list[tuple[QLabel, QPushButton]] = []
        bridge.state_changed.connect(self.render)
        self.render(bridge.state)

    def render(self, state: AppState) -> None:
        for label, button in self._rows:
            label.parentWidget().deleteLater()
        self._rows = []
        for alarm in state.alarms:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            label = QLabel(alarm.message)
            button = QPushButton("Acknowledge")
            button.clicked.connect(lambda _=False, kind=alarm.kind:
                                   self._bridge.dispatch(C.AcknowledgeAlarm(kind=kind)))
            line.addWidget(label, 1)
            line.addWidget(button)
            self._layout.addWidget(row)
            self._rows.append((label, button))
        self.setHidden(not state.alarms)

    def messages(self) -> list[str]:
        return [label.text() for label, _ in self._rows]

    def acknowledge_buttons(self) -> list[QPushButton]:
        return [button for _, button in self._rows]


class MainWindow(QMainWindow):
    def __init__(self, bridge: StateBridge, hub: FrameHub, left: tuple[str, str],
                 right: tuple[str, str], status: Callable[[], StatusInfo],
                 overlay: Callable[[], tuple | None] = lambda: None,
                 refresh_hz: float = 25.0) -> None:
        super().__init__()
        self.setWindowTitle("WormsPy")
        self._bridge = bridge
        self._status = status
        self._overlay = overlay
        self.left_view = LiveView(hub, channel=left[0], title=left[1])
        self.right_view = LiveView(hub, channel=right[0], title=right[1])
        self._views = (self.left_view, self.right_view)
        self._rates = {view.channel: deque(maxlen=20) for view in self._views}
        for view in self._views:
            view.centre_requested.connect(
                lambda x, y, channel=view.channel:
                bridge.dispatch(C.CentreOn(channel=channel, x_px=x, y_px=y)))

        self.alarm_bar = AlarmBar(bridge)
        views = QHBoxLayout()
        views.addWidget(self.left_view, 1)
        views.addWidget(self.right_view, 1)
        central = QWidget()
        column = QVBoxLayout(central)
        column.addWidget(self.alarm_bar)
        column.addLayout(views, 1)
        self.setCentralWidget(central)

        self.controls = ControlPanel(bridge)
        scroll = QScrollArea()
        scroll.setWidget(self.controls)
        scroll.setWidgetResizable(True)
        dock = QDockWidget("Controls")
        dock.setWidget(scroll)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        self.xyz_label, self.fps_label, self.disk_label = QLabel(), QLabel(), QLabel()
        for label in (self.xyz_label, self.fps_label, self.disk_label):
            self.statusBar().addPermanentWidget(label)
        bridge.rejected.connect(lambda reason: self.statusBar().showMessage(reason, 6000))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(int(1000 / refresh_hz))

    def tick(self) -> None:
        for view in self._views:
            if view.refresh():
                self._rates[view.channel].append((view.frame.device_frame_id,
                                                  view.frame.capture_ts))
        self._draw_overlay()
        self._draw_status()

    def _draw_overlay(self) -> None:
        overlay = self._overlay()
        channel, estimate = overlay if overlay is not None else (None, None)
        for view in self._views:
            mine = estimate if view.channel == channel else None
            view.set_tracked_point(None if mine is None else mine.point_px)
            view.set_mask(None if mine is None else mine.mask)

    def _draw_status(self) -> None:
        info = self._status()
        if info.xyz_um is not None:
            x, y, z = info.xyz_um
            self.xyz_label.setText(f"X {x:.1f} · Y {y:.1f} · Z {z:.1f} µm")
        if info.disk_free_bytes is not None:
            self.disk_label.setText(f"{info.disk_free_bytes / 1e9:.0f} GB free")
        rates = []
        for view in self._views:
            samples = self._rates[view.channel]
            if len(samples) >= 2 and samples[-1][1] > samples[0][1]:
                fps = (samples[-1][0] - samples[0][0]) / (samples[-1][1] - samples[0][1])
                rates.append(f"{view.channel} {fps:.1f} fps")
        self.fps_label.setText(" · ".join(rates))
