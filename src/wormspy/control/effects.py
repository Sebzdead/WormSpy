"""Effects: side effects the reducer requests. An executor carries them out on the hardware and
the filesystem, and reports the outcomes back as Events."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StartAcquisition:
    pass


@dataclass(frozen=True)
class StopAcquisition:
    pass


@dataclass(frozen=True)
class CreateSession:
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenRecording:
    session_dir: str
    name: str


@dataclass(frozen=True)
class CloseRecording:
    pass


@dataclass(frozen=True)
class SetTracker:
    enabled: bool
    estimator: str
    source_channel: str


@dataclass(frozen=True)
class CaptureFocus:
    """Read the current Z and report it back as FocusCaptured."""


@dataclass(frozen=True)
class MoveZTo:
    z_um: float


@dataclass(frozen=True)
class WriteEvent:
    label: str


@dataclass(frozen=True)
class CentreOn:
    channel: str
    x_px: float
    y_px: float


@dataclass(frozen=True)
class ApplyPreset:
    name: str


@dataclass(frozen=True)
class RunCalibration:
    pass
