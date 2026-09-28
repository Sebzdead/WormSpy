"""CameraCard: frame rate, exposure and gain, and the named presets (ADR 0006).

Picking a preset applies it at once. Edits to the numbers are held in the card until
**Apply**, and nothing else overwrites them in the meantime. Settings the cameras can't take are
refused with the reason in the status bar, like any other rejected Command. Once applied, the
settings can be saved as a preset. Everything is locked while recording, tracking or
calibrating, because changing camera settings restarts acquisition.
"""

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
)

from wormspy.control import AppState, Mode, TrackingStatus
from wormspy.control import commands as C
from wormspy.presets import DEFAULT_PRESET, MAX_GAIN_DB, CameraSettings
from wormspy.ui.bridge import StateBridge

EDITED = "(edited)"


def _spin(lo: float, hi: float, step: float, decimals: int, suffix: str) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


class CameraCard(QGroupBox):
    def __init__(self, bridge: StateBridge, labels: tuple[str, str]) -> None:
        super().__init__("CAMERAS")
        self._bridge = bridge
        self._state = bridge.state
        self._shown: CameraSettings | None = None     # the applied settings the fields show

        self.preset_box = QComboBox()
        self.preset_box.activated.connect(self._preset_chosen)
        self.save_button = QPushButton("Save as…")
        self.save_button.clicked.connect(self._ask_save)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._ask_delete)

        self.fps = _spin(0.5, 100, 0.5, 1, " fps")
        self.clock_exposure = _spin(0.01, 1000, 1, 2, " ms")
        self.clock_gain = _spin(0, MAX_GAIN_DB, 1, 1, " dB")
        self.follower_exposure = _spin(0.01, 1000, 1, 2, " ms")
        self.follower_gain = _spin(0, MAX_GAIN_DB, 1, 1, " dB")
        self.fields = (self.fps, self.clock_exposure, self.clock_gain, self.follower_exposure,
                       self.follower_gain)
        for field in self.fields:
            field.valueChanged.connect(self._edited)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setProperty("role", "primary")
        self.apply_button.clicked.connect(self.apply)

        grid = QGridLayout(self)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        presets = QHBoxLayout()
        presets.addWidget(self.preset_box, 1)
        presets.addWidget(self.save_button)
        presets.addWidget(self.delete_button)
        grid.addLayout(presets, 0, 0, 1, 3)
        for row, (label, exposure, gain) in enumerate(
                ((labels[0], self.clock_exposure, self.clock_gain),
                 (labels[1], self.follower_exposure, self.follower_gain)), start=1):
            grid.addWidget(self._muted(label), row, 0)
            grid.addWidget(exposure, row, 1)
            grid.addWidget(gain, row, 2)
        grid.addWidget(self._muted("Rate"), 3, 0)
        grid.addWidget(self.fps, 3, 1)
        grid.addWidget(self.apply_button, 3, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    # --- the fields ---------------------------------------------------------------------

    def edited_settings(self) -> CameraSettings:
        return CameraSettings(fps=self.fps.value(),
                              clock_exposure_us=round(self.clock_exposure.value() * 1000, 1),
                              clock_gain_db=self.clock_gain.value(),
                              follower_exposure_us=round(self.follower_exposure.value() * 1000, 1),
                              follower_gain_db=self.follower_gain.value())

    def _show(self, settings: CameraSettings) -> None:
        values = (settings.fps, settings.clock_exposure_us / 1000, settings.clock_gain_db,
                  settings.follower_exposure_us / 1000, settings.follower_gain_db)
        for field, value in zip(self.fields, values, strict=True):
            field.blockSignals(True)
            field.setValue(value)
            field.blockSignals(False)
        self._shown = settings

    @property
    def dirty(self) -> bool:
        return self._shown is not None and self.edited_settings() != self._shown

    # --- commands -----------------------------------------------------------------------

    def apply(self) -> None:
        self._bridge.dispatch(C.ApplyCameraSettings(self.edited_settings()))

    def _preset_chosen(self, index: int) -> None:
        name = self.preset_box.itemData(index)
        if name is not None and name != self._state.preset:
            self._bridge.dispatch(C.ApplyPreset(name=name))

    def save_preset(self, name: str) -> None:
        self._bridge.dispatch(C.SavePreset(name=name))

    def delete_preset(self, name: str) -> None:
        self._bridge.dispatch(C.DeletePreset(name=name))

    def _ask_save(self) -> None:
        suggestion = self._state.preset if self._state.preset != DEFAULT_PRESET else ""
        name, ok = QInputDialog.getText(self, "Save camera preset", "Preset name:",
                                        text=suggestion or "")
        if ok and name.strip():
            self.save_preset(name)

    def _ask_delete(self) -> None:
        name = self._state.preset
        if name and QMessageBox.question(self, "Delete preset",
                                         f"Delete the preset “{name}”?") == \
                QMessageBox.StandardButton.Yes:
            self.delete_preset(name)

    # --- appearance ---------------------------------------------------------------------

    def _edited(self) -> None:
        self.render(self._state)

    def render(self, s: AppState) -> None:
        self._state = s
        if s.camera_settings is not None and s.camera_settings != self._shown:
            self._show(s.camera_settings)          # newly applied: replaces any edits
        locked = (s.mode is Mode.RECORDING or s.calibrating
                  or s.tracking is not TrackingStatus.OFF)

        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        for name, _settings in s.presets:
            self.preset_box.addItem(name, name)
        if s.preset is None:
            self.preset_box.addItem(EDITED, None)
        self.preset_box.setCurrentIndex(self.preset_box.findText(s.preset or EDITED))
        self.preset_box.blockSignals(False)

        dirty = self.dirty
        for widget in (self.preset_box, *self.fields):
            widget.setEnabled(not locked)
        self.apply_button.setEnabled(not locked and dirty)
        self.apply_button.setToolTip(
            (self.edited_settings().problem() or "Apply: restarts the live view") if dirty
            else "")
        self.save_button.setEnabled(not locked and not dirty and s.camera_settings is not None)
        self.delete_button.setEnabled(not locked and s.preset not in (None, DEFAULT_PRESET))
        tip = "Locked while recording, tracking or calibrating" if locked else ""
        self.setToolTip(tip)
