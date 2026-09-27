"""The application state published by the state machine. The UI renders only this (ADR 0010)."""

import enum
from dataclasses import dataclass


class Mode(enum.Enum):
    IDLE = "idle"            # cameras not acquiring
    LIVE = "live"            # acquiring; display and tracking run; nothing written
    RECORDING = "recording"  # acquiring and writing a Recording


class TrackingStatus(enum.Enum):
    """Mirrors the per-frame `tracking_state` column (ADR 0007/0008)."""
    OFF = "off"
    TRACKING = "tracking"
    LOST = "lost"          # target lost: stage ramped to zero, resumes automatically
    OVERRIDE = "override"  # joystick has XY; tracking resumes on release
    LIMIT = "limit"        # clamped at the XY safe range


@dataclass(frozen=True)
class Alarm:
    """A condition the operator must see. Stays until acknowledged or cleared by its source."""
    kind: str      # stable key, e.g. "frames_dropped", "disk_space", "recording_failed"
    message: str


@dataclass(frozen=True)
class AppState:
    mode: Mode = Mode.IDLE
    session_dir: str | None = None
    next_recording: int = 1           # number the next Recording in this session gets
    recording_name: str | None = None  # e.g. "rec-001" while RECORDING
    tracking: TrackingStatus = TrackingStatus.OFF
    estimator: str = "dark_object"
    source_channel: str = "behaviour"
    focus_z_um: float | None = None   # stored by Set Focus (ADR 0002)
    preset: str | None = None         # named camera preset (ADR 0006)
    recording_drops: int = 0          # dropped frames in the current Recording
    alarms: tuple[Alarm, ...] = ()
