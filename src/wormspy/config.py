"""Rig configuration from `rig.toml` (ADR 0005 amendment).

Everything has a default, so a missing section is fine. Anything unsafe or contradictory, like
an inverted safe range, an unknown axis, or a button mapped to a command that doesn't exist, is
rejected at startup with a message naming the offending key.
"""

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from wormspy.control import commands as C
from wormspy.control.gamepad import DEFAULT_BUTTONS, StickConfig

AXES = ("x", "y", "z")
# Travel of the reference rig: 2× Zaber TSB60E (50.4 mm) and VSR20A (20 mm). Narrow Z in
# rig.toml to a range that is safe for your objective and sample holder.
DEFAULT_SAFE_RANGE_UM = {"x": (0.0, 50_400.0), "y": (0.0, 50_400.0), "z": (0.0, 20_000.0)}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StageConfig:
    xy_port: str = "COM6"
    z_port: str = "COM3"
    max_speed_um_s: float = 2000.0
    safe_range_um: dict[str, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_SAFE_RANGE_UM))


@dataclass(frozen=True)
class GamepadConfig:
    sticks: StickConfig = field(default_factory=StickConfig)
    buttons: dict[str, type] = field(default_factory=lambda: dict(DEFAULT_BUTTONS))


@dataclass(frozen=True)
class PathsConfig:
    data_root: Path
    calibration: Path


@dataclass(frozen=True)
class RigConfig:
    stage: StageConfig
    gamepad: GamepadConfig
    paths: PathsConfig


def _button_commands() -> dict[str, type]:
    """Commands a button can issue: those that need no arguments."""
    out = {}
    for name in dir(C):
        cls = getattr(C, name)
        if dataclasses.is_dataclass(cls) and isinstance(cls, type) and all(
                f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
                for f in dataclasses.fields(cls)):
            out[name] = cls
    return out


def load_config(path: Path) -> RigConfig:
    path = Path(path)
    if not path.exists():
        # no rig.toml yet: every default
        return RigConfig(stage=_stage({}), gamepad=_gamepad({}), paths=_paths({}, path))
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    return RigConfig(stage=_stage(raw.get("stage", {})),
                     gamepad=_gamepad(raw.get("gamepad", {})),
                     paths=_paths(raw.get("paths", {}), path))


def _stage(raw: dict) -> StageConfig:
    safe = dict(DEFAULT_SAFE_RANGE_UM)
    for axis, value in raw.get("safe_range_um", {}).items():
        if axis not in AXES:
            raise ConfigError(f"stage.safe_range_um: unknown axis {axis!r} (use x, y, z)")
        lo, hi = (float(v) for v in value)
        if not lo < hi:
            raise ConfigError(f"stage.safe_range_um.{axis}: min {lo} must be below max {hi}")
        safe[axis] = (lo, hi)
    speed = float(raw.get("max_speed_um_s", StageConfig.max_speed_um_s))
    if speed <= 0:
        raise ConfigError("stage.max_speed_um_s must be positive")
    return StageConfig(xy_port=str(raw.get("xy_port", StageConfig.xy_port)),
                       z_port=str(raw.get("z_port", StageConfig.z_port)),
                       max_speed_um_s=speed, safe_range_um=safe)


def _gamepad(raw: dict) -> GamepadConfig:
    sticks_raw = raw.get("sticks", {})
    unknown = set(sticks_raw) - {f.name for f in dataclasses.fields(StickConfig)}
    if unknown:
        raise ConfigError(f"gamepad.sticks: unknown key(s) {sorted(unknown)}")
    sticks = StickConfig(**sticks_raw)
    if not 0 <= sticks.deadzone < 1:
        raise ConfigError("gamepad.sticks.deadzone must be in [0, 1)")
    if sticks.expo <= 0 or sticks.xy_max_um_s <= 0 or sticks.z_max_um_s <= 0:
        raise ConfigError("gamepad.sticks: expo and max speeds must be positive")
    buttons = dict(DEFAULT_BUTTONS)
    if "buttons" in raw:
        allowed = _button_commands()
        buttons = {}
        for button, command in raw["buttons"].items():
            if command not in allowed:
                raise ConfigError(f"gamepad.buttons.{button}: unknown command {command!r}; "
                                  f"choose from {sorted(allowed)}")
            buttons[button] = allowed[command]
    return GamepadConfig(sticks=sticks, buttons=buttons)


def _paths(raw: dict, config_path: Path) -> PathsConfig:
    data_root = Path(raw.get("data_root", Path.home() / "WormsPy_data")).expanduser()
    calibration = Path(raw.get("calibration", config_path.parent / "calibration.json"))
    return PathsConfig(data_root=data_root, calibration=calibration.expanduser())
