"""LiveView: one camera channel, displayed faithfully (ADR 0004).

- The image, locked to its canvas: no pan or zoom, the feed always shows the whole sensor.
- An exposure histogram whose axis is fixed to the camera's dynamic range (0 to the dtype
  maximum) with log counts, so it reads like a camera histogram: where the pixels sit, and how
  close they are to clipping. Only its two level handles move.
- Levels are **fixed**: 8-bit starts at 0–255, 16-bit is levelled once on its first frame, and
  after that only the operator (level handles or "Auto") changes them. A brighter frame must
  look brighter.
- A LUT choice (grey, inferno, …), which replaces v1's heatmap toggle.
- A saturation readout: the fraction of pixels at the dtype's maximum.
- Overlays: FOV-centre crosshair, tracked point, and an optional threshold-mask tint.
- A double-click emits `centre_requested(x, y)` in image pixels, for click-to-centre.
Display processing never touches recorded data.
"""

import cv2
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
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
from wormspy.ui import theme

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)

LUTS = ("grey", "inferno", "viridis", "magma", "plasma", "thermal", "flame")
MASK_RGBA = (57, 255, 90, 60)          # the WormsPy green, faint
DEFAULT_ASPECT = 1920 / 1200
HISTOGRAM_BINS = 256
HISTOGRAM_STEP = 4                     # histogram from every 4th pixel in each direction


def dynamic_range(dtype: np.dtype) -> tuple[float, float]:
    """The full range a camera image of this dtype can take."""
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return float(info.min), float(info.max)
    return 0.0, 1.0


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

        # The canvas is sized by the main window to exactly the camera's aspect ratio, so the
        # image fills it edge to edge with no letterboxing.
        self.canvas = pg.GraphicsLayoutWidget()
        self.canvas.setBackground(theme.FEED_BACKGROUND)
        self.canvas.ci.setContentsMargins(0, 0, 0, 0)
        self._viewbox = self.canvas.addViewBox(lockAspect=True, invertY=True)
        self._viewbox.setMenuEnabled(False)
        self._viewbox.setDefaultPadding(0)
        self._viewbox.setMouseEnabled(x=False, y=False)    # a fixed feed: no pan, no zoom
        self.image_item = pg.ImageItem()
        self._mask_item = pg.ImageItem()
        self._mask_item.setVisible(False)
        # v1's green tracking dot, kept as the tracked-point marker
        self._point_item = pg.ScatterPlotItem(size=11, symbol="o", pen=pg.mkPen("#0a0a0a", width=1),
                                              brush=pg.mkBrush(theme.ACCENT))
        crosshair = pg.mkPen((57, 255, 90, 70), width=1)
        self._cross_h = pg.InfiniteLine(angle=0, pen=crosshair)
        self._cross_v = pg.InfiniteLine(angle=90, pen=crosshair)
        for item in (self.image_item, self._mask_item, self._cross_h, self._cross_v,
                     self._point_item):
            self._viewbox.addItem(item)
        self.canvas.scene().sigMouseClicked.connect(self._on_click)

        self._histogram = pg.HistogramLUTWidget(orientation="horizontal")
        self._histogram.setImageItem(self.image_item)
        self._histogram.setBackground(theme.GROUND)
        self._histogram.setFixedHeight(84)
        hist = self._histogram.item
        # We draw the histogram ourselves (fixed bins over the full range, log counts), so
        # pyqtgraph's auto-binned one must not overwrite it on every frame.
        self.image_item.sigImageChanged.disconnect(hist.imageChanged)
        hist.plot.setBrush((57, 255, 90, 60))
        hist.plot.setPen((57, 255, 90, 160))
        hist.region.setBrush((57, 255, 90, 22))
        for line in hist.region.lines:
            line.setPen(pg.mkPen((57, 255, 90, 150), width=1))
        hist.vb.setMouseEnabled(x=False, y=False)
        hist.vb.setMenuEnabled(False)
        hist.vb.disableAutoRange()
        hist.gradient.showTicks(False)                      # the LUT comes from the combo box
        hist.gradient.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.histogram_range: tuple[float, float] = (0.0, 0.0)
        self._set_histogram_range(np.dtype(np.uint16))

        self.saturation_label = QLabel("")
        self.saturation_label.setStyleSheet(f"color: {theme.RED}; font-weight: 700;")
        auto = QPushButton("Auto levels")
        auto.clicked.connect(self.auto_levels)
        self._lut_box = QComboBox()
        self._lut_box.addItems(LUTS)
        self._lut_box.currentTextChanged.connect(self.set_lut)
        self.mask_checkbox = QCheckBox("Mask")
        self.mask_checkbox.setToolTip("Show the tracking threshold mask")
        self.mask_checkbox.toggled.connect(self.show_mask)

        self._toolbar = QWidget()
        toolbar = QHBoxLayout(self._toolbar)
        toolbar.setContentsMargins(2, 4, 2, 0)
        name = QLabel(title.upper())
        name.setStyleSheet(f"color: {theme.MUTED}; font-weight: 700; letter-spacing: 1px;")
        toolbar.addWidget(name)
        toolbar.addWidget(self.saturation_label)
        toolbar.addStretch(1)
        toolbar.addWidget(self.mask_checkbox)
        toolbar.addWidget(self._lut_box)
        toolbar.addWidget(auto)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.canvas)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._histogram)

    @property
    def aspect(self) -> float:
        """Width / height of the camera image (the canvas is kept at exactly this)."""
        if self.image is None:
            return DEFAULT_ASPECT
        height, width = self.image.shape[:2]
        return width / height

    def chrome_height(self) -> int:
        """Height of everything below the canvas (toolbar + histogram + spacing)."""
        spacing = self.layout().spacing()
        return self._toolbar.sizeHint().height() + self._histogram.height() + 2 * spacing

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
        if first:
            self._set_histogram_range(self.image.dtype)
        self._update_histogram()
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
            self._viewbox.setRange(xRange=(0, width), yRange=(0, height), padding=0)
        self._update_saturation()
        return True

    @property
    def levels(self) -> tuple[float, float]:
        lo, hi = self.image_item.levels
        return float(lo), float(hi)

    def auto_levels(self) -> None:
        """Background to brightest real structure.

        The top is the maximum of a 3×3-averaged image: a lone hot pixel is averaged away, while
        a neuron a few pixels wide survives. Sparse fluorescence (a neuron on dark background)
        therefore shows the neuron unclipped instead of stretching noise to full contrast.
        """
        if self.image is None:
            return
        height, width = self.image.shape[:2]
        lo = float(np.percentile(self.image[::2, ::2], 0.5))
        binned = cv2.resize(self.image.astype(np.float32), (max(1, width // 3),
                                                            max(1, height // 3)),
                            interpolation=cv2.INTER_AREA)
        hi = float(binned.max())
        self._set_levels(lo, max(hi, lo + 1))

    def _set_histogram_range(self, dtype: np.dtype) -> None:
        lo, hi = dynamic_range(dtype)
        self.histogram_range = (lo, hi)
        hist = self._histogram.item
        hist.vb.setLimits(xMin=lo, xMax=hi)
        hist.vb.setXRange(lo, hi, padding=0)
        hist.region.setBounds((lo, hi))
        samples = -(-1200 // HISTOGRAM_STEP) * -(-1920 // HISTOGRAM_STEP)
        if self.image is not None:
            samples = self.image[::HISTOGRAM_STEP, ::HISTOGRAM_STEP].size
        hist.vb.setYRange(0, np.log10(1 + samples), padding=0.05)

    def _update_histogram(self) -> None:
        """Fixed bins spanning the whole dynamic range; counts on a log scale."""
        lo, hi = self.histogram_range
        sample = self.image[::HISTOGRAM_STEP, ::HISTOGRAM_STEP]
        counts, edges = np.histogram(sample, bins=HISTOGRAM_BINS, range=(lo, hi + 1))
        self._histogram.item.plot.setData(edges, np.log10(1 + counts), stepMode="center",
                                          fillLevel=0)

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
        if self.mask_checkbox.isChecked() != self.mask_visible:
            self.mask_checkbox.setChecked(self.mask_visible)
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
