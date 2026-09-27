"""ControlPanel: the sidebar (ADR 0004/0010).

Buttons only dispatch Commands. `render(state)` is the single place that sets every enabled
state and label, so the panel always shows what the state machine says, never a local guess.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wormspy.control import AppState, Mode, TrackingStatus
from wormspy.control import commands as C
from wormspy.ui.bridge import StateBridge

ESTIMATORS = (("dark_object", "Brightfield silhouette", "behaviour"),
              ("bright_marker", "Fluorescent marker", "fluorescence"))
SOURCES = (("behaviour", "Brightfield camera"), ("fluorescence", "Fluorescence camera"))
TRACKING_TEXT = {
    TrackingStatus.OFF: ("Off", "#9a9a9a"),
    TrackingStatus.TRACKING: ("Tracking", "#3fbf5f"),
    TrackingStatus.LOST: ("Target lost", "#ff5050"),
    TrackingStatus.OVERRIDE: ("Joystick override", "#f0b040"),
    TrackingStatus.LIMIT: ("At stage limit", "#ff8c30"),
}


class ControlPanel(QWidget):
    def __init__(self, bridge: StateBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._state = bridge.state
        layout = QVBoxLayout(self)

        # live
        self.live_button = QPushButton("Start live")
        self.live_button.clicked.connect(self._toggle_live)
        layout.addWidget(self.live_button)

        # session
        session = QGroupBox("Session")
        form = QFormLayout(session)
        self.animal_id, self.operator, self.condition = QLineEdit(), QLineEdit(), QLineEdit()
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(60)
        form.addRow("Animal / strain", self.animal_id)
        form.addRow("Operator", self.operator)
        form.addRow("Condition", self.condition)
        form.addRow("Notes", self.notes)
        self.new_session_button = QPushButton("New session")
        self.new_session_button.clicked.connect(self._new_session)
        self.session_label = QLabel()
        form.addRow(self.new_session_button)
        form.addRow(self.session_label)
        layout.addWidget(session)

        # recording
        recording = QGroupBox("Recording")
        rec_layout = QVBoxLayout(recording)
        self.record_button = QPushButton()
        self.record_button.clicked.connect(lambda: bridge.dispatch(C.ToggleRecording()))
        self.recording_label = QLabel()
        marker_row = QHBoxLayout()
        self.event_label = QLineEdit()
        self.event_label.setPlaceholderText("event label")
        self.mark_button = QPushButton("Mark event")
        self.mark_button.clicked.connect(
            lambda: bridge.dispatch(C.MarkEvent(label=self.event_label.text() or "marker")))
        marker_row.addWidget(self.event_label)
        marker_row.addWidget(self.mark_button)
        rec_layout.addWidget(self.record_button)
        rec_layout.addWidget(self.recording_label)
        rec_layout.addLayout(marker_row)
        layout.addWidget(recording)

        # tracking
        tracking = QGroupBox("Tracking")
        trk = QFormLayout(tracking)
        self.estimator_box = QComboBox()
        for key, label, _source in ESTIMATORS:
            self.estimator_box.addItem(label, key)
        self.source_box = QComboBox()
        for key, label in SOURCES:
            self.source_box.addItem(label, key)
        self.estimator_box.currentIndexChanged.connect(self._estimator_chosen)
        self.source_box.currentIndexChanged.connect(self._source_chosen)
        self.track_button = QPushButton()
        self.track_button.clicked.connect(lambda: bridge.dispatch(C.ToggleTracking()))
        self.tracking_label = QLabel()
        trk.addRow("Estimator", self.estimator_box)
        trk.addRow("Camera", self.source_box)
        trk.addRow(self.track_button)
        trk.addRow("Status", self.tracking_label)
        layout.addWidget(tracking)

        # focus
        focus = QGroupBox("Focus")
        foc = QVBoxLayout(focus)
        row = QHBoxLayout()
        self.set_focus_button = QPushButton("Set focus")
        self.set_focus_button.clicked.connect(lambda: bridge.dispatch(C.SetFocus()))
        self.return_focus_button = QPushButton("Return to focus")
        self.return_focus_button.clicked.connect(lambda: bridge.dispatch(C.ReturnToFocus()))
        row.addWidget(self.set_focus_button)
        row.addWidget(self.return_focus_button)
        self.focus_label = QLabel()
        foc.addLayout(row)
        foc.addWidget(self.focus_label)
        layout.addWidget(focus)

        # calibration
        calibration = QGroupBox("Stage calibration")
        cal = QVBoxLayout(calibration)
        self.calibrate_button = QPushButton("Calibrate (bead slide in focus)")
        self.calibrate_button.clicked.connect(lambda: bridge.dispatch(C.Calibrate()))
        self.calibration_label = QLabel()
        cal.addWidget(self.calibrate_button)
        cal.addWidget(self.calibration_label)
        layout.addWidget(calibration)
        layout.addStretch(1)

        bridge.state_changed.connect(self.render)
        self.render(bridge.state)

    # --- commands ------------------------------------------------------------------------

    def _toggle_live(self) -> None:
        self._bridge.dispatch(C.StartLive() if self._state.mode is Mode.IDLE else C.StopLive())

    def _new_session(self) -> None:
        self._bridge.dispatch(C.NewSession(metadata={
            "animal_id": self.animal_id.text().strip(),
            "operator": self.operator.text().strip(),
            "condition": self.condition.text().strip(),
            "notes": self.notes.toPlainText().strip()}))

    def _estimator_chosen(self, _index: int) -> None:
        name = self.estimator_box.currentData()
        default_source = next(src for key, _label, src in ESTIMATORS if key == name)
        self._bridge.dispatch(C.SelectEstimator(name=name, source_channel=default_source))

    def _source_chosen(self, _index: int) -> None:
        self._bridge.dispatch(C.SelectEstimator(name=self.estimator_box.currentData(),
                                                source_channel=self.source_box.currentData()))

    # --- the single source of truth for the panel's appearance ------------------------------

    def render(self, s: AppState) -> None:
        self._state = s
        live, recording = s.mode is not Mode.IDLE, s.mode is Mode.RECORDING
        busy = s.calibrating
        tracking_on = s.tracking is not TrackingStatus.OFF

        self.live_button.setText("Start live" if s.mode is Mode.IDLE else "Stop live")
        self.live_button.setEnabled(not recording and not busy)

        self.new_session_button.setEnabled(not recording)
        self.session_label.setText(Path(s.session_dir).name if s.session_dir else "No session")

        self.record_button.setText("■ Stop recording" if recording else "● Record")
        self.record_button.setEnabled(recording or (s.mode is Mode.LIVE and s.session_dir is not None
                                                    and not busy))
        self.recording_label.setText(f"{s.recording_name} · {s.recording_drops} dropped"
                                     if recording else "Not recording")
        self.mark_button.setEnabled(recording)
        self.event_label.setEnabled(recording)

        self.track_button.setText("Stop tracking" if tracking_on else "Start tracking")
        self.track_button.setEnabled(tracking_on or (live and s.calibrated and not busy))
        text, colour = TRACKING_TEXT[s.tracking]
        self.tracking_label.setText(text)
        self.tracking_label.setStyleSheet(f"color: {colour}; font-weight: bold;")
        for box, value in ((self.estimator_box, s.estimator), (self.source_box, s.source_channel)):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(value)))
            box.blockSignals(False)
            box.setEnabled(not tracking_on)

        self.set_focus_button.setEnabled(not busy)
        self.return_focus_button.setEnabled(s.focus_z_um is not None and not busy)
        self.focus_label.setText(f"Focus z = {s.focus_z_um:.0f} µm" if s.focus_z_um is not None
                                 else "No focus stored")

        self.calibrate_button.setEnabled(s.mode is Mode.LIVE and not tracking_on and not busy)
        self.calibration_label.setText("Calibrating…" if busy else
                                       "Calibrated" if s.calibrated else "Not calibrated")
