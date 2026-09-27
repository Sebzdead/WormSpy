"""LiveView: one camera channel, displayed faithfully (ADR 0004).

- The image with a linked histogram. Levels are **fixed**: 8-bit starts at 0–255, 16-bit is
  levelled once on its first frame, and after that only the operator (histogram handles or
  "Auto") changes them. A brighter frame must look brighter.
- A LUT choice (grey, inferno, …), which replaces v1's heatmap toggle.
- A saturation readout: the fraction of pixels at the dtype's maximum.
- Overlays: FOV-centre crosshair, tracked point, and an optional threshold-mask tint.
- A double-click emits `centre_requested(x, y)` in image pixels, for click-to-centre.
Display processing never touches recorded data.
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wormspy.core.frames import FrameHub

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

LUTS = ("grey", "inferno", "viridis", "magma", "plasma", "thermal", "flame")
MASK_RGBA = (60, 220, 60, 90)


class LiveView(QWidget):
    centre_requested = Signal(float, float)

    def __init__(self, hub: FrameHub, channel: str, title: str) -> None:
        super().__init__()
        self._hub = hub
        self.channel = channel
        self.image: np.ndarray | None = None
        self.frame = None                     # the Frame currently displayed
        self._frame_id: int | None = None
        self._levelled = False
        self.saturated_fraction = 0.0
        self.tracked_point: tuple[float, float] | None = None
        self._mask: np.ndarray | None = None
        self.mask_visible = False
        self.lut_name = "grey"

        self._canvas = pg.GraphicsLayoutWidget()
        self._viewbox = self._canvas.addViewBox(lockAspect=True, invertY=True)
        self._viewbox.setMenuEnabled(False)
        self.image_item = pg.ImageItem()
        self._mask_item = pg.ImageItem()
        self._mask_item.setVisible(False)
        self._point_item = pg.ScatterPlotItem(size=14, symbol="x", pen=pg.mkPen("r", width=2),
                                              brush=None)
        self._cross_h = pg.InfiniteLine(angle=0, pen=pg.mkPen((255, 200, 0, 140)))
        self._cross_v = pg.InfiniteLine(angle=90, pen=pg.mkPen((255, 200, 0, 140)))
        for item in (self.image_item, self._mask_item, self._point_item, self._cross_h,
                     self._cross_v):
            self._viewbox.addItem(item)
        self._canvas.scene().sigMouseClicked.connect(self._on_click)

        self._histogram = pg.HistogramLUTWidget(orientation="horizontal")
        self._histogram.setImageItem(self.image_item)
        self._histogram.setMaximumHeight(90)

        self.saturation_label = QLabel("")
        self.saturation_label.setStyleSheet("color: #ff5050; font-weight: bold;")
        auto = QPushButton("Auto levels")
        auto.clicked.connect(self.auto_levels)
        self._lut_box = QComboBox()
        self._lut_box.addItems(LUTS)
        self._lut_box.currentTextChanged.connect(self.set_lut)
        mask_box = QCheckBox("Threshold mask")
        mask_box.toggled.connect(self.show_mask)

        header = QHBoxLayout()
        name = QLabel(title)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        header.addStretch(1)
        header.addWidget(self.saturation_label)
        header.addWidget(mask_box)
        header.addWidget(self._lut_box)
        header.addWidget(auto)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(header)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._histogram)

    # --- frames ------------------------------------------------------------------------------

    def refresh(self) -> bool:
        """Show the hub's newest frame for this channel. Returns True if it was new."""
        frame = self._hub.latest(self.channel)
        if frame is None or frame.device_frame_id == self._frame_id:
            return False
        self._frame_id = frame.device_frame_id
        self.frame = frame
        first = self.image is None or self.image.shape != frame.image.shape
        self.image = frame.image
        self.image_item.setImage(self.image, autoLevels=False)
        if not self._levelled:
            if self.image.dtype == np.uint8:
                self._set_levels(0.0, 255.0)
            else:
                self.auto_levels()
            self._levelled = True
        if first:
            height, width = self.image.shape[:2]
            self._cross_h.setPos(height / 2)
            self._cross_v.setPos(width / 2)
            self._viewbox.autoRange(padding=0)
        self._update_saturation()
        return True

    @property
    def levels(self) -> tuple[float, float]:
        lo, hi = self.image_item.levels
        return float(lo), float(hi)

    def auto_levels(self) -> None:
        if self.image is None:
            return
        lo, hi = np.percentile(self.image[::2, ::2], (0.5, 99.5))
        self._set_levels(float(lo), float(max(hi, lo + 1)))

    def _set_levels(self, lo: float, hi: float) -> None:
        self.image_item.setLevels((lo, hi))
        self._histogram.item.setLevels(lo, hi)

    def _update_saturation(self) -> None:
        if np.issubdtype(self.image.dtype, np.integer):
            self.saturated_fraction = float(np.mean(self.image >= np.iinfo(self.image.dtype).max))
        text = f"saturated {self.saturated_fraction:.1%}" if self.saturated_fraction > 0 else ""
        self.saturation_label.setText(text)

    # --- appearance and overlays -----------------------------------------------------------

    @staticmethod
    def available_luts() -> tuple[str, ...]:
        return LUTS

    def set_lut(self, name: str) -> None:
        self._histogram.item.gradient.loadPreset(name)
        self.lut_name = name
        if self._lut_box.currentText() != name:
            self._lut_box.setCurrentText(name)

    def set_tracked_point(self, point: tuple[float, float] | None) -> None:
        self.tracked_point = point
        self._point_item.setData([] if point is None else [{"pos": point}])

    def set_mask(self, mask: np.ndarray | None) -> None:
        self._mask = mask
        if self.mask_visible:
            self._draw_mask()

    def show_mask(self, visible: bool) -> None:
        self.mask_visible = bool(visible)
        self._mask_item.setVisible(self.mask_visible)
        if self.mask_visible:
            self._draw_mask()

    def _draw_mask(self) -> None:
        if self._mask is None:
            self._mask_item.clear()
            return
        rgba = np.zeros((*self._mask.shape, 4), np.uint8)
        rgba[self._mask] = MASK_RGBA
        self._mask_item.setImage(rgba, autoLevels=False)

    # --- click-to-centre -------------------------------------------------------------------

    def _on_click(self, event) -> None:
        if event.double():
            self.request_centre_at_scene(event.scenePos())

    def request_centre_at_scene(self, scene_pos) -> None:
        if self.image is None:
            return
        point = self.image_item.mapFromScene(scene_pos)
        height, width = self.image.shape[:2]
        if 0 <= point.x() < width and 0 <= point.y() < height:
            self.centre_requested.emit(float(point.x()), float(point.y()))
