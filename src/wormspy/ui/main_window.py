"""MainWindow (ADR 0004): brightfield and fluorescence side by side, the control sidebar on the
right, an alarm bar above the views and a status bar below.

A QTimer calls `tick()` about 25 times a second. It pulls the newest frames from the FrameHub,
the tracker's latest estimate (overlay) and status readouts. Acquisition never waits for the UI.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wormspy.control import AppState, Mode, TrackingStatus
from wormspy.control import commands as C
from wormspy.core.frames import FrameHub
from wormspy.ui import theme
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
        self.setStyleSheet(f"AlarmBar {{ background: {theme.RED_BG}; border: 1px solid"
                           f" {theme.RED_DIM}; border-radius: 8px; }} QLabel {{ color:"
                           f" {theme.RED}; font-weight: 600; }}")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 6, 8, 6)
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


MARGIN = 12
FEED_GAP = 10


class Readouts(QFrame):
    """The live readouts, top right: each value under a caption saying what it is."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("readouts")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 4, 12, 5)
        row.setSpacing(14)
        self.values: list[QLabel] = []
        for index, (caption, tip) in enumerate((
                ("STAGE POSITION", "Where the stage is, in µm"),
                ("FRAME RATE", "Frame rate measured from each camera's frames"),
                ("DISK", "Free space where recordings are saved"))):
            if index:
                divider = QFrame()
                divider.setObjectName("readoutDivider")
                divider.setFixedWidth(1)
                row.addWidget(divider)
            tile = QVBoxLayout()
            tile.setSpacing(1)
            title = QLabel(caption)
            title.setObjectName("readoutCaption")
            value = QLabel("—")
            value.setObjectName("readout")
            value.setFont(theme.mono_font(11))
            for label in (title, value):
                label.setToolTip(tip)
                tile.addWidget(label)
            row.addLayout(tile)
            self.values.append(value)


class Header(QWidget):
    """The WormsPy wordmark, mode chips and live readouts, in one slim bar."""

    def __init__(self, bridge: StateBridge) -> None:
        super().__init__()
        self.wordmark = QLabel("WormsPy")
        self.wordmark.setStyleSheet(f"color: {theme.ACCENT}; font-size: 24px; font-weight: 800;"
                                    "letter-spacing: -0.5px;")
        version = QLabel("v2")
        version.setObjectName("muted")
        self.mode_chip, self.rec_chip, self.tracking_chip = (QLabel() for _ in range(3))
        for chip in (self.mode_chip, self.rec_chip, self.tracking_chip):
            chip.setObjectName("chip")
        self.readouts = Readouts()
        self.xyz_label, self.fps_label, self.disk_label = self.readouts.values
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.wordmark)
        row.addWidget(version, 0, Qt.AlignmentFlag.AlignBottom)
        row.addSpacing(14)
        row.addWidget(self.mode_chip)
        row.addWidget(self.rec_chip)
        row.addWidget(self.tracking_chip)
        row.addStretch(1)
        row.addWidget(self.readouts)
        bridge.state_changed.connect(self.render)
        self.render(bridge.state)

    def render(self, s: AppState) -> None:
        mode = {Mode.IDLE: ("IDLE", ""), Mode.LIVE: ("LIVE", "live"),
                Mode.RECORDING: ("LIVE", "live")}[s.mode]
        self._chip(self.mode_chip, *mode)
        self.rec_chip.setVisible(s.mode is Mode.RECORDING)
        if s.mode is Mode.RECORDING:
            drops = f" · {s.recording_drops} dropped" if s.recording_drops else ""
            self._chip(self.rec_chip, f"● REC {s.recording_name}{drops}", "rec")
        tracking = {TrackingStatus.TRACKING: ("TRACKING", "live"),
                    TrackingStatus.LOST: ("TARGET LOST", "rec"),
                    TrackingStatus.OVERRIDE: ("JOYSTICK", "warn"),
                    TrackingStatus.LIMIT: ("STAGE LIMIT", "warn")}.get(s.tracking)
        self.tracking_chip.setVisible(tracking is not None)
        if tracking:
            self._chip(self.tracking_chip, *tracking)

    @staticmethod
    def _chip(chip: QLabel, text: str, tone: str) -> None:
        chip.setText(text)
        chip.setProperty("tone", tone)
        theme.repolish(chip)


class MainWindow(QMainWindow):
    closing = Signal()          # emitted when the operator closes the window: shut down cleanly

    def __init__(self, bridge: StateBridge, hub: FrameHub, left: tuple[str, str],
                 right: tuple[str, str], status: Callable[[], StatusInfo],
                 overlay: Callable[[], dict] = dict,
                 refresh_hz: float = 25.0,
                 camera_labels: tuple[str, str] = ("Clock cam", "Follower")) -> None:
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

        self.header = Header(bridge)
        self.xyz_label = self.header.xyz_label
        self.fps_label = self.header.fps_label
        self.disk_label = self.header.disk_label
        self.alarm_bar = AlarmBar(bridge)
        self.controls = ControlPanel(bridge, camera_labels)

        feeds = QHBoxLayout()
        feeds.setSpacing(FEED_GAP)
        feeds.addStretch(1)
        feeds.addWidget(self.left_view)
        feeds.addWidget(self.right_view)
        feeds.addStretch(1)
        central = QWidget()
        central.setObjectName("central")
        self._column = QVBoxLayout(central)
        self._column.setContentsMargins(MARGIN, 8, MARGIN, MARGIN)
        self._column.setSpacing(8)
        self._column.addWidget(self.header)
        self._column.addWidget(self.alarm_bar)
        self._column.addLayout(feeds)
        self._column.addWidget(self.controls)
        self._column.addStretch(1)
        self.setCentralWidget(central)
        self._fitted_for = None
        bridge.rejected.connect(lambda reason: self.statusBar().showMessage(reason, 6000))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(int(1000 / refresh_hz))

    def tick(self) -> None:
        for view in self._views:
            if view.refresh():
                self._rates[view.channel].append((view.frame.device_frame_id,
                                                  view.frame.capture_ts))
        self._fit_feeds()
        self._draw_overlay()
        self._draw_status()

    # --- feed geometry --------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_feeds(force=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_feeds(force=True)

    def _fit_feeds(self, force: bool = False) -> None:
        """Size both feeds to exactly their camera aspect, as large as the window allows:
        the full width if there is height to spare, otherwise the height left over after the
        header, alarms, view toolbars and control cards."""
        central = self.centralWidget()
        key = (central.width(), central.height(), self.alarm_bar.isHidden(),
               tuple(view.aspect for view in self._views))
        if key == self._fitted_for and not force:
            return
        self._fitted_for = key
        width = central.width() - 2 * MARGIN - FEED_GAP
        spacing = self._column.spacing()
        others = (self.header.sizeHint().height() + self.controls.sizeHint().height()
                  + (0 if self.alarm_bar.isHidden() else self.alarm_bar.sizeHint().height())
                  + max(view.chrome_height() for view in self._views)
                  + 8 + MARGIN + 4 * spacing)
        height_left = central.height() - others
        aspects = [view.aspect for view in self._views]
        h = max(60, min(width / sum(aspects), height_left))
        for view, aspect in zip(self._views, aspects, strict=True):
            view.canvas.set_target(int(h * aspect), int(h))

    def _draw_overlay(self) -> None:
        overlays = self._overlay()
        for view in self._views:
            mine = overlays.get(view.channel)
            view.set_tracked_point(None if mine is None else mine.point_px,
                                   mapped=mine is not None and mine.mapped)
            view.set_mask(None if mine is None else mine.mask)

    def _draw_status(self) -> None:
        info = self._status()
        if info.xyz_um is not None:
            x, y, z = info.xyz_um
            self.xyz_label.setText(f"X {x:.1f}  Y {y:.1f}  Z {z:.1f}")
        if info.disk_free_bytes is not None:
            self.disk_label.setText(f"{info.disk_free_bytes / 1e9:.0f} GB free")
        rates = []
        for view in self._views:
            samples = self._rates[view.channel]
            if len(samples) >= 2 and samples[-1][1] > samples[0][1]:
                fps = (samples[-1][0] - samples[0][0]) / (samples[-1][1] - samples[0][1])
                rates.append(f"{view.channel} {fps:.1f}")
        self.fps_label.setText("  ".join(rates) + " fps" if rates else "—")

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self.closing.emit()
        super().closeEvent(event)
