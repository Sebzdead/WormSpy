"""Commands: operator intents. Gamepad, keyboard and UI all issue these same Commands (ADR 0010)."""

from dataclasses import dataclass, field
from typing import Any

from wormspy.presets import CameraSettings


@dataclass(frozen=True)
class StartLive:
    pass


@dataclass(frozen=True)
class StopLive:
    pass


@dataclass(frozen=True)
class NewSession:
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StartRecording:
    pass


@dataclass(frozen=True)
class StopRecording:
    pass


@dataclass(frozen=True)
class ToggleRecording:
    pass


@dataclass(frozen=True)
class Shutdown:
    pass


@dataclass(frozen=True)
class SetTracking:
    enabled: bool


@dataclass(frozen=True)
class ToggleTracking:
    pass


@dataclass(frozen=True)
class SelectEstimator:
    name: str
    source_channel: str


@dataclass(frozen=True)
class SetFocus:
    pass


@dataclass(frozen=True)
class ReturnToFocus:
    pass


@dataclass(frozen=True)
class MarkEvent:
    label: str = "marker"     # a gamepad button marks an unlabelled event


@dataclass(frozen=True)
class CentreOn:
    channel: str
    x_px: float
    y_px: float


@dataclass(frozen=True)
class ApplyPreset:
    """Apply a saved camera preset (ADR 0006). Not while recording, tracking or calibrating."""
    name: str


@dataclass(frozen=True)
class ApplyCameraSettings:
    """Apply edited camera settings; they match a preset only if identical to one."""
    settings: CameraSettings


@dataclass(frozen=True)
class SavePreset:
    """Save the applied camera settings under `name` (replacing a preset of that name)."""
    name: str


@dataclass(frozen=True)
class DeletePreset:
    name: str


@dataclass(frozen=True)
class AcknowledgeAlarm:
    kind: str


@dataclass(frozen=True)
class Calibrate:
    """Run the stage-to-camera calibration routine (ADR 0009). Moves the stage."""
