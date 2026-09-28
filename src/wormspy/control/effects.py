"""Effects: side effects the reducer requests. An executor carries them out on the hardware and
the filesystem, and reports the outcomes back as Events."""

from dataclasses import dataclass, field
from typing import Any

from wormspy.presets import CameraSettings, Presets


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
class ApplyCameraSettings:
    """Reconfigure the cameras. Acquisition restarts around it if the view is live."""
    settings: CameraSettings
    preset: str | None


@dataclass(frozen=True)
class WritePresets:
    """Persist the saved presets (Default is never written)."""
    presets: Presets


@dataclass(frozen=True)
class RunCalibration:
    pass
