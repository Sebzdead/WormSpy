"""The WormsPy look: dark, quiet greys, and the v1 neon green used sparingly.

Every colour in the UI comes from these tokens. Green marks the few things that matter: the
wordmark, primary actions, the tracked point and keyboard focus. Red is reserved for recording
and alarms, amber for warnings.
"""

import pyqtgraph as pg
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

GROUND = "#141414"      # window background (v1 used #181818)
PANEL = "#1d1f1d"       # control cards
RAISED = "#282b28"      # inputs and buttons
BORDER = "#343834"
TEXT = "#dde2dd"
MUTED = "#858b85"
ACCENT = "#39ff5a"      # the WormsPy neon green
ACCENT_DIM = "#1f6b2c"
RED = "#ff4d4d"
AMBER = "#f0b040"
FEED_BACKGROUND = "#0a0a0a"


def mono_font(point_size: int = 11) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    return font


STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QWidget#central {{ background: {GROUND}; }}
QStatusBar {{ background: {GROUND}; color: {MUTED}; }}

QGroupBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
    margin-top: 16px; padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;
}}

QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 5px 12px; min-height: 18px;
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5a5f5a; background: #202220; border-color: #262926; }}
QPushButton[role="primary"] {{ border-color: {ACCENT_DIM}; color: {ACCENT}; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #22341f; }}
QPushButton[role="primary"]:disabled {{ color: #3f5a42; border-color: #262926; }}
QPushButton[role="danger"] {{ border-color: #7a2a2a; color: {RED}; font-weight: 600; }}
QPushButton[role="danger"]:hover {{ background: #3a1c1c; }}

QLineEdit, QPlainTextEdit, QComboBox {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px;
    selection-background-color: {ACCENT_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: #5a5f5a; }}
QComboBox QAbstractItemView {{ background: {RAISED}; selection-background-color: {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {BORDER};
                        background: {RAISED}; border-radius: 2px; }}

QLabel#chip {{
    border-radius: 9px; padding: 2px 10px; font-weight: 700; font-size: 11px;
    background: {RAISED}; color: {MUTED};
}}
QLabel#chip[tone="live"] {{ background: #1c3a22; color: {ACCENT}; }}
QLabel#chip[tone="rec"] {{ background: #4a1616; color: {RED}; }}
QLabel#chip[tone="warn"] {{ background: #43340f; color: {AMBER}; }}
QLabel#readout {{ color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    """Dark Fusion palette + the stylesheet, and matching pyqtgraph colours."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, colour in (
            (QPalette.ColorRole.Window, GROUND), (QPalette.ColorRole.WindowText, TEXT),
            (QPalette.ColorRole.Base, RAISED), (QPalette.ColorRole.AlternateBase, PANEL),
            (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, RAISED),
            (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT_DIM),
            (QPalette.ColorRole.HighlightedText, TEXT), (QPalette.ColorRole.ToolTipBase, PANEL),
            (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, MUTED)):
        palette.setColor(role, QColor(colour))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    pg.setConfigOptions(background=FEED_BACKGROUND, foreground=MUTED)


def repolish(widget) -> None:
    """Re-apply the stylesheet after changing a dynamic property (role/tone)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
