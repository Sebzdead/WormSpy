"""The launcher: `wormspy` on the rig, `wormspy --sim` anywhere (or `python -m wormspy`).

Builds the state store, rig (the FLIR cameras and Zaber stage, or the simulated rig), executor,
gamepad controller and window, and shuts all of them down cleanly when the window closes. If
the rig's hardware can't be opened as configured, it stops with a message saying what to fix.
"""

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from wormspy.app.executor import Executor
from wormspy.config import RigConfig, load_config
from wormspy.control import AppState, StateStore
from wormspy.control import commands as C
from wormspy.control import events as E
from wormspy.control.gamepad import GamepadController
from wormspy.core.frames import FrameHub
from wormspy.sim.handle import SimRigHandle
from wormspy.sim.render import ImageGeometry
from wormspy.sim.rig import ChannelSpec, SimRig
from wormspy.sim.stage import SimStage
from wormspy.sim.worm import SyntheticWorm

DEFAULT_CONFIG = Path("config/rig.toml")
# The reference rig: 1920×1200 px at ~0.75 µm/px → 1440 × 900 µm field of view.
SIM_GEOMETRY = ImageGeometry(width_px=1920, height_px=1200, um_per_px=0.75, rotation_deg=1.5)


@dataclass
class App:
    window: object
    store: StateStore
    executor: Executor
    rig: object                    # HardwareRig or SimRigHandle
    gamepad: GamepadController | None = None
    slide_action: object = None
    _closed: bool = field(default=False, repr=False)

    def shutdown(self) -> None:
        """Tracker off, recording closed, acquisition stopped, threads joined. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self.store.dispatch(C.Shutdown())
        self.executor.wait_idle()
        if self.gamepad is not None:
            self.gamepad.stop()
        self.executor.close()
        close = getattr(self.rig, "close", None)
        if close is not None:
            close()                # cameras, Spinnaker system, stage connections


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wormspy", description=__doc__.splitlines()[0])
    parser.add_argument("--sim", action="store_true",
                        help="run against the simulated rig instead of the hardware")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="rig.toml (default: config/rig.toml; defaults if absent)")
    parser.add_argument("--sim-speed", type=float, default=1.0,
                        help="simulated time per real second (sim only)")
    return parser.parse_args(argv)


def _sim_rig(config: RigConfig, speed: float) -> SimRigHandle:
    safe = config.stage.safe_range_um
    centre = {axis: (lo + hi) / 2 for axis, (lo, hi) in safe.items()}
    stage = SimStage(safe, config.stage.max_speed_um_s, centre)
    worm = SyntheticWorm(seed=0, start_um=(centre["x"], centre["y"]), speed_um_s=150.0)
    rig = SimRig(worm, stage, ChannelSpec(SIM_GEOMETRY, "fluorescence", noise_sigma=300.0),
                 ChannelSpec(SIM_GEOMETRY, "behaviour", noise_sigma=4.0), fps=10.0)
    return SimRigHandle(rig, clock_name="gcamp", follower_name="brightfield", speed=speed)


def _gamepad(config: RigConfig, stage, store) -> GamepadController | None:
    try:
        from wormspy.hardware.gamepad_pygame import PygameGamepad
        pad = PygameGamepad()
    except ImportError:
        return None
    controller = GamepadController(pad, stage, store.dispatch, config.gamepad.sticks,
                                   config.gamepad.buttons)
    controller.start()
    return controller


def _disk_free(path: Path) -> float | None:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return float(shutil.disk_usage(candidate).free)
    return None


def _hardware_rig(config: RigConfig):
    from wormspy.config import ConfigError
    from wormspy.hardware.flir import HardwareError
    from wormspy.hardware.rig import open_rig
    try:
        return open_rig(config)
    except (HardwareError, ConfigError) as exc:
        raise SystemExit(f"wormspy: {exc}") from exc


def _views(rig) -> tuple[tuple[str, str], tuple[str, str]]:
    """Behaviour on the left, fluorescence on the right (as in v1)."""
    channels = sorted((rig.clock_channel, rig.follower_channel),
                      key=lambda ch: ch.role != "behaviour")
    return tuple((ch.name, ch.name.replace("_", " ").title()) for ch in channels)


def build(argv: list[str]) -> App:
    args = _parse(argv)
    from PySide6.QtGui import QAction  # Qt only once we know we are building a UI
    from PySide6.QtWidgets import QApplication

    from wormspy.ui import theme
    from wormspy.ui.bridge import StateBridge
    from wormspy.ui.main_window import MainWindow, StatusInfo

    theme.apply(QApplication.instance())

    try:
        config = load_config(args.config)      # a missing file means all defaults
    except ValueError as exc:                  # ConfigError names the offending key
        raise SystemExit(f"wormspy: {exc}") from exc
    rig = _sim_rig(config, args.sim_speed) if args.sim else _hardware_rig(config)
    store, hub = StateStore(AppState()), FrameHub()
    for warning in getattr(rig, "warnings", ()):
        store.dispatch(E.AlarmRaised(kind="hardware", message=warning))
    gamepad = _gamepad(config, rig.stage, store)
    executor = Executor(store, rig, hub, data_root=config.paths.data_root,
                        calibration_path=config.paths.calibration,
                        override=(lambda: gamepad.xy_active) if gamepad else (lambda: False))

    def status() -> StatusInfo:
        sample = executor.poller.latest()
        xyz = None if sample is None else (sample.x_um, sample.y_um, sample.z_um)
        return StatusInfo(xyz_um=xyz, disk_free_bytes=_disk_free(config.paths.data_root))

    if args.sim:
        left, right = ("brightfield", "Brightfield"), ("gcamp", "Fluorescence")
    else:
        left, right = _views(rig)
    window = MainWindow(StateBridge(store), hub, left=left, right=right, status=status,
                        overlay=executor.tracking_overlay)
    slide = None
    if args.sim:
        window.setWindowTitle("WormsPy — simulated rig")
        slide = QAction("Calibration slide under the objective", window, checkable=True)
        slide.toggled.connect(lambda on: setattr(rig.rig, "target", bool(on)))
        window.menuBar().addMenu("Simulator").addAction(slide)
    else:
        window.setWindowTitle("WormsPy")

    app = App(window=window, store=store, executor=executor, rig=rig, gamepad=gamepad,
              slide_action=slide)
    window.closing.connect(app.shutdown)
    return app


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication(sys.argv[:1])
    app = build(sys.argv[1:] if argv is None else argv)
    app.window.resize(1600, 900)
    app.window.show()
    return qt.exec()


if __name__ == "__main__":
    raise SystemExit(main())
