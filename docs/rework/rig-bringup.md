# Rig bring-up checklist

The FLIR and Zaber adapters are tested against fakes of their SDKs. Anything that depends on
real devices is checked here, by hand, on the rig PC. Work top to bottom; each step assumes the
ones before it passed.

## 1. Install

- [ ] Spinnaker SDK installed, and SpinView sees both cameras.
- [ ] `uv sync --extra zaber --extra gamepad`
- [ ] `uv pip install spinnaker_python-<version>.whl`, using the wheel matching the venv's
      Python. `uv run python -c "import PySpin"` works.
- [ ] `config/rig.toml` copied from `rig.example.toml`, with both camera serials and both
      Zaber ports filled in.
- [ ] Close SpinView and Zaber Launcher before starting WormsPy. Each holds the devices
      exclusively.

## 2. Trigger wiring (ADR 0006)

The defaults follow FLIR's Blackfly S synchronisation app note: the clock camera outputs
ExposureActive on **Line2**, with its 3.3 V enabled, and the follower triggers on **Line3**,
rising edge. Check them against the GPIO pinout of your cable.

- [ ] Start WormsPy and press **Start live**. Both feeds update.
- [ ] Unplug the GPIO cable. The behaviour feed freezes while fluorescence keeps running, and
      the frames-dropped alarm counts up. Plug it back in.
- [ ] If the behaviour feed is frozen from the start, set `follower_trigger_line`
      / `clock_output_line` in rig.toml and restart.

## 3. Pairing and frame IDs

- [ ] Record for **10 minutes** at 10 Hz. There must be no frames-dropped alarm, and in
      `frames.csv` no row may have a `dropped` flag.
- [ ] `pair_skew_ns` in `frames.csv` stays within a few hundred µs, and does not grow over
      the recording.
- [ ] No `pairing_suspect` alarm. It fires if paired frames were captured more than half a
      frame apart, which would mean the two cameras' frame IDs are misaligned.
- [ ] Stop and restart the live view 5 times, recording a few seconds each time. The same
      three checks hold every time.

## 4. Pixel data

- [ ] Fluorescence is `uint16` in the TIFF. Check what a saturated pixel reads (point the
      camera at something bright). If it tops out at 4095 or 65520 rather than 65535, note it:
      the histogram and saturation readout assume 65535.
- [ ] The exposure and gain in the camera match rig.toml (SpinView, after closing WormsPy).

## 5. Stage (ADR 0005)

- [ ] No "No stage" alarm at startup.
- [ ] Zaber Launcher shows `limit.min` / `limit.max` equal to `stage.safe_range_um`.
- [ ] **Z safe range:** with the objective raised, drive Z toward each end of the range with
      the joystick. It stops at the configured limit, clear of the plate and holder.
- [ ] The XY readout in the header changes by the expected distance for a known move.

## 6. Gamepad

- [ ] Left stick moves XY, right stick moves Z, both in the expected directions. If not, set
      `invert_y` / `invert_z` in rig.toml.
- [ ] X toggles tracking, B toggles recording, A returns to focus, Y sets focus.

## 7. Calibration and tracking

- [ ] With the calibration slide under the objective, **Calibrate stage** completes.
      `calibration.json` is written with sub-pixel residuals.
- [ ] Double-clicking a feature in either feed centres it.
- [ ] Track a worm for 5 minutes on brightfield. Tune the servo gains and the estimator
      thresholds if it lags or oscillates.
