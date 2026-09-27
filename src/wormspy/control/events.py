"""Events: facts reported to the state machine by the executor and worker threads."""

from dataclasses import dataclass

from wormspy.control.state import TrackingStatus


@dataclass(frozen=True)
class SessionOpened:
    path: str


@dataclass(frozen=True)
class TrackingStatusChanged:
    status: TrackingStatus


@dataclass(frozen=True)
class FocusCaptured:
    z_um: float


@dataclass(frozen=True)
class FramesDropped:
    channel: str
    count: int


@dataclass(frozen=True)
class AlarmRaised:
    kind: str
    message: str


@dataclass(frozen=True)
class AlarmCleared:
    kind: str


@dataclass(frozen=True)
class RecordingFailed:
    """OpenRecording or a writer failed; nothing more is being written."""
    reason: str


@dataclass(frozen=True)
class AcquisitionFailed:
    """The cameras stopped delivering (open failure, disconnect)."""
    reason: str
