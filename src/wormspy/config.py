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


ROLES = ("fluorescence", "behaviour")
PIXEL_FORMATS = ("Mono8", "Mono16")
# Time the sensor needs between exposures at the reference frame rates (readout + margin).
EXPOSURE_MARGIN_US = 2000.0


@dataclass(frozen=True)
class CameraConfig:
    name: str                  # channel name: file stem and CSV column prefix
    role: str                  # "fluorescence" (raw 16-bit TIFF) or "behaviour" (FFV1 video)
    pixel_format: str
    exposure_us: float
    gain_db: float = 0.0
    serial: str = ""           # FLIR serial number (printed on the camera / SpinView)


@dataclass(frozen=True)
class CamerasConfig:
    """The two FLIR cameras (ADR 0006): the clock camera free-runs at `fps` and drives the
    follower's trigger input through a GPIO cable."""
    fps: float = 10.0
    clock: CameraConfig = field(default_factory=lambda: CameraConfig(
        name="gcamp", role="fluorescence", pixel_format="Mono16", exposure_us=14_000.0))
    follower: CameraConfig = field(default_factory=lambda: CameraConfig(
        name="brightfield", role="behaviour", pixel_format="Mono8", exposure_us=5_000.0))
    clock_output_line: str = "Line2"        # carries the clock camera's ExposureActive
    follower_trigger_line: str = "Line3"    # the follower's trigger input


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
    cameras: CamerasConfig
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
        return RigConfig(cameras=_cameras({}), stage=_stage({}), gamepad=_gamepad({}),
                         paths=_paths({}, path))
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    return RigConfig(cameras=_cameras(raw.get("cameras", {})),
                     stage=_stage(raw.get("stage", {})),
                     gamepad=_gamepad(raw.get("gamepad", {})),
                     paths=_paths(raw.get("paths", {}), path))


def _camera(raw: dict, default: CameraConfig, key: str, fps: float) -> CameraConfig:
    unknown = set(raw) - {f.name for f in dataclasses.fields(CameraConfig)}
    if unknown:
        raise ConfigError(f"{key}: unknown key(s) {sorted(unknown)}")
    camera = dataclasses.replace(default, **raw)
    camera = dataclasses.replace(camera, exposure_us=float(camera.exposure_us),
                                 gain_db=float(camera.gain_db), serial=str(camera.serial))
    if camera.role not in ROLES:
        raise ConfigError(f"{key}.role must be one of {list(ROLES)}, not {camera.role!r}")
    if camera.pixel_format not in PIXEL_FORMATS:
        raise ConfigError(f"{key}.pixel_format must be one of {list(PIXEL_FORMATS)}")
    if camera.role == "fluorescence" and camera.pixel_format != "Mono16":
        raise ConfigError(f"{key}: fluorescence is recorded raw 16-bit; use Mono16 (ADR 0007)")
    budget = 1e6 / fps - EXPOSURE_MARGIN_US
    if not 0 < camera.exposure_us <= budget:
        raise ConfigError(f"{key}.exposure_us must be in (0, {budget:.0f}] at {fps:g} fps")
    return camera


def _cameras(raw: dict) -> CamerasConfig:
    defaults = CamerasConfig()
    unknown = set(raw) - {f.name for f in dataclasses.fields(CamerasConfig)}
    if unknown:
        raise ConfigError(f"cameras: unknown key(s) {sorted(unknown)}")
    fps = float(raw.get("fps", defaults.fps))
    if fps <= 0:
        raise ConfigError("cameras.fps must be positive")
    clock = _camera(raw.get("clock", {}), defaults.clock, "cameras.clock", fps)
    follower = _camera(raw.get("follower", {}), defaults.follower, "cameras.follower", fps)
    if clock.name == follower.name:
        raise ConfigError("cameras: clock and follower need different names")
    if clock.serial and clock.serial == follower.serial:
        raise ConfigError("cameras: clock and follower have the same serial number")
    return CamerasConfig(
        fps=fps, clock=clock, follower=follower,
        clock_output_line=str(raw.get("clock_output_line", defaults.clock_output_line)),
        follower_trigger_line=str(raw.get("follower_trigger_line",
                                          defaults.follower_trigger_line)))


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
