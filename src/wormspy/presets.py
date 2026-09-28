"""Camera settings and named presets (ADR 0006).

A preset is what an operator tunes per experiment: the frame rate and each camera's exposure
and gain. The rig's wiring (serials, channel roles, pixel formats, trigger lines) stays in
`rig.toml`. The **Default** preset *is* rig.toml's `[cameras]` values: it is always present,
read-only, and not stored. Saved presets live in `presets.json` next to rig.toml.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_PRESET = "Default"
EXPOSURE_MARGIN_US = 2000.0     # sensor time needed between exposures (readout + margin)
MAX_GAIN_DB = 47.0              # Blackfly S range is 0–48 dB


def exposure_budget_us(fps: float) -> float:
    """The longest exposure that still fits one frame period."""
    return 1e6 / fps - EXPOSURE_MARGIN_US


@dataclass(frozen=True)
class CameraSettings:
    fps: float
    clock_exposure_us: float
    clock_gain_db: float
    follower_exposure_us: float
    follower_gain_db: float

    def problem(self) -> str | None:
        """Why these settings can't be applied, or None if they can."""
        if not 0.5 <= self.fps <= 100:
            return f"Frame rate {self.fps:g} fps is outside 0.5–100 fps."
        budget = exposure_budget_us(self.fps)
        for camera, exposure in (("clock", self.clock_exposure_us),
                                 ("follower", self.follower_exposure_us)):
            if not 0 < exposure <= budget:
                return (f"The {camera} exposure ({exposure / 1000:g} ms) must be under "
                        f"{budget / 1000:g} ms at {self.fps:g} fps.")
        for camera, gain in (("clock", self.clock_gain_db), ("follower", self.follower_gain_db)):
            if not 0 <= gain <= MAX_GAIN_DB:
                return f"The {camera} gain ({gain:g} dB) must be within 0–{MAX_GAIN_DB:g} dB."
        return None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "CameraSettings":
        return cls(**{key: float(data[key]) for key in cls.__dataclass_fields__})


Presets = tuple[tuple[str, CameraSettings], ...]


def load_presets(path: Path, default: CameraSettings) -> Presets:
    """Default first, then the saved presets in the order they were saved."""
    saved: list[tuple[str, CameraSettings]] = []
    path = Path(path)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            saved = [(name, CameraSettings.from_json(values))
                     for name, values in data.get("presets", {}).items()
                     if name != DEFAULT_PRESET]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path.name} is not a valid presets file: {exc}") from exc
    return ((DEFAULT_PRESET, default), *saved)


def save_presets(path: Path, presets: Presets) -> None:
    data = {"presets": {name: settings.to_json() for name, settings in presets
                        if name != DEFAULT_PRESET}}
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)                   # never leave a half-written file behind
