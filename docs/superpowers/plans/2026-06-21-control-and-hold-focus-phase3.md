# Stage Control Restoration & Hold Focus (ADR 0002) — Phase 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Restore the stage-control features that Phase 1 deliberately orphaned (auto-tracking, manual joystick) on the new architecture, and replace the broken PID "autofocus" with the honest **Hold Focus** model from ADR 0002 (operator sets focus; software holds/restores it; optional opt-in brightfield drift compensation).

**Architecture:** Tracking compute algorithms move into a pure `core/tracking_algorithms.py` and are injected into the Phase-1 `Tracker` as its `compute` callback, so the heavy path (DLC) still runs off the acquisition loop. Focus becomes `core/focus.py`: a numpy-only sharpness metric, a `FocusHold` (store/return a z), and an opt-in `DriftCompensator` (brightfield hill-climb that nudges z). The broken `PIDController`/`continuous_autofocus`/`calculate_focus_metric` are deleted. app.py wires tracker + focus + manual joystick to the shared `STAGE`/`HUB`/`TELEMETRY` from Phases 1–2.

**Tech Stack:** Python 3.8/3.12, NumPy, scikit-image (already used), pygame (joystick), pytest. Focus metric is numpy-only so it is testable without OpenCV.

**Scope:** Implements ADR 0002 and restores auto-tracking + manual joystick. Closes the Q8 limit-hit loop by feeding the Tracker's `on_move(hit)` into `Telemetry.set_limit_hit`. Does NOT touch the Angular UI or the `/status` endpoint (Phase 4) — though it adds the backend routes the Phase 4 UI will call.

**Depends on:** Phases 1 (`core/` engine, Tracker, Stage, HUB) and 2 (`Telemetry`). Build on the same branch.

---

## File Structure

All paths relative to `WormSpy/backend/code/`. Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest` from that dir.

- Create: `core/focus.py` — `focus_metric` (numpy Laplacian variance); `FocusHold`; `DriftCompensator`.
- Create: `core/tracking_algorithms.py` — `thresh_light_background`, `thresh_fluorescent_marker`, `make_compute` (returns a `Tracker` `compute` callback).
- Create: `tests/test_focus.py`, `tests/test_tracking_algorithms.py`.
- Modify: `app.py` — delete dead PID autofocus; construct `Tracker` + `FocusHold` + `DriftCompensator`; restore manual joystick on `STAGE`; routes `/set_focus`, `/return_to_focus`, `/toggle_drift_comp` (replaces `/toggle_af`), and feed tracking limit-hit into telemetry.

---

### Task 1: Focus metric and FocusHold

**Files:**
- Create: `WormSpy/backend/code/core/focus.py`
- Test: `WormSpy/backend/code/tests/test_focus.py`
- Reference: Phase 1 `core/stage.py` (`FakeStage`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_focus.py`:

```python
import numpy as np
from core.focus import focus_metric, FocusHold
from core.stage import FakeStage, NullStage


def test_focus_metric_higher_for_sharper_image():
    blurry = np.full((64, 64), 128, np.uint8)
    blurry[20:44, 20:44] = 130                      # very low contrast edge
    sharp = np.zeros((64, 64), np.uint8)
    sharp[20:44, 20:44] = 255                        # hard high-contrast edge
    assert focus_metric(sharp) > focus_metric(blurry)


def test_focus_metric_zero_for_flat_image():
    assert focus_metric(np.full((32, 32), 100, np.uint8)) == 0.0


def _stage(z=50.0):
    return FakeStage(limits={"x": (0, 1e9), "y": (0, 1e9), "z": (0.0, 100.0)},
                     positions={"x": 0.0, "y": 0.0, "z": z})


def test_focus_hold_has_no_focus_before_set():
    fh = FocusHold(_stage())
    assert fh.has_focus is False
    assert fh.stored_z is None


def test_set_focus_stores_current_z():
    stage = _stage(z=42.0)
    fh = FocusHold(stage)
    fh.set_focus()
    assert fh.has_focus is True
    assert fh.stored_z == 42.0


def test_return_to_focus_moves_stage_back_to_stored_z():
    stage = _stage(z=42.0)
    fh = FocusHold(stage)
    fh.set_focus()
    stage.move_relative("z", 10.0)          # drift to 52
    assert stage.get_position("z") == 52.0
    fh.return_to_focus()
    assert stage.get_position("z") == 42.0


def test_return_to_focus_is_noop_when_focus_not_set():
    stage = _stage(z=42.0)
    fh = FocusHold(stage)
    fh.return_to_focus()                     # must not raise / move
    assert stage.get_position("z") == 42.0


def test_set_focus_noop_when_stage_disconnected():
    fh = FocusHold(NullStage())
    fh.set_focus()
    assert fh.has_focus is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_focus.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.focus'`

- [ ] **Step 3: Write minimal implementation**

Create `core/focus.py`:

```python
from typing import Optional

import numpy as np

from core.stage import Stage


def focus_metric(image: np.ndarray) -> float:
    """Sharpness = variance of a discrete Laplacian. Numpy-only (no OpenCV) so it is testable
    without hardware libs. Higher = sharper. Flat images return 0.0."""
    img = image.astype(np.float64)
    lap = (-4.0 * img
           + np.roll(img, 1, axis=0) + np.roll(img, -1, axis=0)
           + np.roll(img, 1, axis=1) + np.roll(img, -1, axis=1))
    # trim the 1px border where np.roll wraps, to avoid edge artifacts
    inner = lap[1:-1, 1:-1]
    return float(inner.var())


class FocusHold:
    """Operator-set focus. Stores a z and can return the stage to it. No sensing, no dither."""

    def __init__(self, stage: Stage) -> None:
        self._stage = stage
        self._stored_z: Optional[float] = None
        self._ref_metric: Optional[float] = None

    @property
    def has_focus(self) -> bool:
        return self._stored_z is not None

    @property
    def stored_z(self) -> Optional[float]:
        return self._stored_z

    @property
    def ref_metric(self) -> Optional[float]:
        return self._ref_metric

    def set_focus(self, ref_metric: Optional[float] = None) -> None:
        """Store the current z (and an optional brightfield reference metric for drift comp)."""
        if not self._stage.is_connected():
            return
        self._stored_z = self._stage.get_position("z")
        self._ref_metric = ref_metric

    def return_to_focus(self) -> None:
        if self._stored_z is None:
            return
        self._stage.move_absolute("z", self._stored_z)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_focus.py`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/focus.py WormSpy/backend/code/tests/test_focus.py
git commit -m "feat: add numpy focus metric and FocusHold (set/return z)"
```
End commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 2: DriftCompensator (opt-in brightfield hill-climb)

**Files:**
- Modify: `WormSpy/backend/code/core/focus.py`
- Test: `WormSpy/backend/code/tests/test_focus.py` (extend)

> The opt-in (B) path from ADR 0002. Hill-climbs the z-stage to keep the brightfield sharpness at
> the reference captured at focus-set time. Dithers the shared stage (documented artifact). The
> metric source is injected as a `sample_metric()` callable so the climb logic is testable without
> images: a unimodal metric-of-z lets us assert convergence.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus.py`:

```python
from core.focus import DriftCompensator


def test_drift_compensator_climbs_toward_metric_peak():
    # metric peaks at z == 60; FakeStage starts at 50. The compensator should climb toward 60.
    stage = FakeStage(limits={"x": (0, 1e9), "y": (0, 1e9), "z": (0.0, 100.0)},
                      positions={"x": 0.0, "y": 0.0, "z": 50.0})

    def sample_metric():
        z = stage.get_position("z")
        return 1000.0 - (z - 60.0) ** 2          # unimodal, peak at z=60

    comp = DriftCompensator(stage, sample_metric, step_native=1.0)
    for _ in range(100):
        comp.step()
    assert abs(stage.get_position("z") - 60.0) <= 2.0


def test_drift_compensator_step_respects_stage_limits():
    stage = FakeStage(limits={"x": (0, 1e9), "y": (0, 1e9), "z": (0.0, 5.0)},
                      positions={"x": 0.0, "y": 0.0, "z": 5.0})
    # metric rewards going higher, but z is pinned at its max of 5.0
    comp = DriftCompensator(stage, lambda: stage.get_position("z"), step_native=1.0)
    for _ in range(10):
        comp.step()
    assert stage.get_position("z") <= 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_focus.py -k drift`
Expected: FAIL — `ImportError: cannot import name 'DriftCompensator'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/focus.py`:

```python
from typing import Callable


class DriftCompensator:
    """Opt-in brightfield hill-climb. Each step() nudges z by ±step_native, keeping the direction
    that improved the metric (flips on a decrease). Stateful across calls. Caller is responsible
    for logging z per frame and for clearly labelling the focus/intensity artifact this introduces."""

    def __init__(self, stage, sample_metric: Callable[[], float], step_native: float = 1.0) -> None:
        self._stage = stage
        self._sample = sample_metric
        self._step = step_native
        self._direction = 1
        self._last_metric: Optional[float] = None

    def step(self) -> None:
        metric = self._sample()
        if self._last_metric is not None and metric < self._last_metric:
            self._direction = -self._direction          # got worse -> reverse
        self._stage.move_relative("z", self._direction * self._step)
        self._last_metric = metric
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_focus.py`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/focus.py WormSpy/backend/code/tests/test_focus.py
git commit -m "feat: add opt-in DriftCompensator brightfield hill-climb"
```
End commit message with the Co-Authored-By trailer.

---

### Task 3: Port tracking compute algorithms

**Files:**
- Create: `WormSpy/backend/code/core/tracking_algorithms.py`
- Test: `WormSpy/backend/code/tests/test_tracking_algorithms.py`
- Reference: legacy `app.py` `Thresh_Light_Background`, `Thresh_Fluorescent_Marker`, `trackWorm` (mm→native conversion via `MM_MST`, orientation, `simple_to_center`).

> These become the `Tracker.compute` callback: `(frame_image, mode) -> (dx, dy)` in native stage
> microsteps. Preprocessing uses scikit-image (`threshold_yen`) and numpy; the centroid + geometry
> reuse Phase 1's `find_worm_cms`/`simple_to_center`. DLC (mode 2) is guarded behind an injected
> pose function so this module never imports DeepLabCut.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracking_algorithms.py`:

```python
import numpy as np
from core.tracking_algorithms import (thresh_light_background,
                                      thresh_fluorescent_marker, make_compute)


def test_thresh_light_background_returns_binary_uint8():
    frame = np.random.randint(0, 255, (64, 64), np.uint8)
    out = thresh_light_background(frame)
    assert out.dtype == np.uint8
    assert set(np.unique(out)).issubset({0, 255})


def test_thresh_fluorescent_marker_isolates_bright_blob():
    frame = np.zeros((64, 64), np.uint8)
    frame[28:36, 28:36] = 255
    out = thresh_fluorescent_marker(frame)
    assert out.dtype == np.uint8
    assert out[32, 32] == 255            # bright blob kept
    assert out[0, 0] == 0                # dark background dropped


def test_make_compute_centered_blob_yields_near_zero_move():
    # bright blob at frame center -> minimal stage move
    cfg = dict(factor=1, resolution=(64, 64), total_mm_x=1.92, total_mm_y=1.2,
               mm_microsteps=20997, orient_x=1, orient_y=-1, gain=1.0)
    compute = make_compute(cfg)
    frame = np.zeros((64, 64), np.uint8)
    frame[30:34, 30:34] = 255            # ~center
    dx, dy = compute(frame, 1)           # mode 1 = fluorescent marker
    assert abs(dx) < cfg["mm_microsteps"] * 0.1
    assert abs(dy) < cfg["mm_microsteps"] * 0.1


def test_make_compute_offcenter_blob_yields_proportional_move():
    cfg = dict(factor=1, resolution=(64, 64), total_mm_x=1.92, total_mm_y=1.2,
               mm_microsteps=20997, orient_x=1, orient_y=-1, gain=1.0)
    compute = make_compute(cfg)
    frame = np.zeros((64, 64), np.uint8)
    frame[30:34, 56:60] = 255            # far right -> positive x move (orient_x=1)
    dx, _ = compute(frame, 1)
    assert dx > 0


def test_make_compute_dlc_uses_injected_pose_fn():
    cfg = dict(factor=1, resolution=(64, 64), total_mm_x=1.92, total_mm_y=1.2,
               mm_microsteps=20997, orient_x=1, orient_y=-1, gain=1.0,
               dlc_pose_fn=lambda img: (32.0, 32.0), node_index=0)
    compute = make_compute(cfg)
    dx, dy = compute(np.zeros((64, 64), np.uint8), 2)   # mode 2 = DLC
    assert abs(dx) < cfg["mm_microsteps"] * 0.1         # pose at center -> ~no move
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_tracking_algorithms.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tracking_algorithms'`

- [ ] **Step 3: Write minimal implementation**

Create `core/tracking_algorithms.py`:

```python
"""Per-mode tracking compute, ported from legacy app.py. Produces a Tracker `compute` callback
returning (dx, dy) in native stage microsteps. scikit-image + numpy only; DLC is injected."""

import math
from typing import Callable, Tuple

import numpy as np
from scipy import ndimage as _ndi          # for gaussian blur without cv2
from skimage.filters import threshold_yen

from core.tracking import find_worm_cms, simple_to_center


def thresh_light_background(frame: np.ndarray) -> np.ndarray:
    """Bright-background thresholding -> binary (0/255) image of the dark worm body."""
    blurred = _ndi.gaussian_filter(frame, sigma=4)
    thr = blurred.mean() - 0.5 * blurred.std()      # dark object on bright field
    out = (blurred < thr).astype(np.uint8) * 255
    out = _ndi.binary_erosion(out > 0, iterations=3)
    out = _ndi.binary_dilation(out, iterations=1)
    return out.astype(np.uint8) * 255


def thresh_fluorescent_marker(frame: np.ndarray) -> np.ndarray:
    """Yen-threshold a fluorescent marker -> binary (0/255) of the bright blob."""
    gamma = ((frame / 255.0) ** 1.5 * 255).astype(np.uint8)
    thr = threshold_yen(gamma) if gamma.max() > gamma.min() else 255
    return (gamma > thr).astype(np.uint8) * 255


def _mm_to_native(centroid_xy: Tuple[float, float], cfg: dict) -> Tuple[float, float]:
    mm_x, mm_y = simple_to_center(centroid_xy[0], centroid_xy[1],
                                  resolution=cfg["resolution"],
                                  total_mm_x=cfg["total_mm_x"], total_mm_y=cfg["total_mm_y"],
                                  orient_x=cfg["orient_x"], orient_y=cfg["orient_y"])
    gain = cfg.get("gain", 1.0)
    return (mm_x * cfg["mm_microsteps"] * gain, mm_y * cfg["mm_microsteps"] * gain)


def make_compute(cfg: dict) -> Callable[[np.ndarray, int], Tuple[float, float]]:
    """Build the Tracker compute callback. cfg keys: factor, resolution, total_mm_x/y,
    mm_microsteps, orient_x/y, gain; optional dlc_pose_fn + node_index for mode 2."""
    center = (cfg["resolution"][0] / 2.0, cfg["resolution"][1] / 2.0)

    def compute(frame: np.ndarray, mode: int) -> Tuple[float, float]:
        if mode == 0:
            processed = thresh_light_background(frame)
            coords = find_worm_cms(processed, cfg["factor"], center)
        elif mode == 1:
            processed = thresh_fluorescent_marker(frame)
            coords = find_worm_cms(processed, cfg["factor"], center)
        elif mode == 2:
            pose_fn = cfg.get("dlc_pose_fn")
            if pose_fn is None:
                return (0.0, 0.0)
            coords = pose_fn(frame)             # (x, y) in frame pixels
        else:
            return (0.0, 0.0)
        if coords is None or (isinstance(coords[0], float) and math.isnan(coords[0])):
            return (0.0, 0.0)
        return _mm_to_native(coords, cfg)

    return compute
```

NOTE for implementer: legacy used OpenCV (`cv2.GaussianBlur`/`adaptiveThreshold`/`erode`). This port
uses `scipy.ndimage` + a mean/std threshold so it is testable without OpenCV and has no behavioural
dependency on cv2. If the rig shows the bright-background mode under/over-segments, tune the
`mean - 0.5*std` threshold and the erosion/dilation iterations — these are the knobs.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_tracking_algorithms.py`
Expected: PASS (5 passed)

- [ ] **Step 5: Run full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add WormSpy/backend/code/core/tracking_algorithms.py WormSpy/backend/code/tests/test_tracking_algorithms.py
git commit -m "feat: port per-mode tracking compute as Tracker compute callback"
```
End commit message with the Co-Authored-By trailer.

---

### Task 4: Delete dead PID autofocus; add Hold Focus routes

**Files:**
- Modify: `WormSpy/backend/code/app.py`

> Removes `PIDController`, `continuous_autofocus`, `calculate_focus_metric`, `start_autofocus` and
> the old `/toggle_af` behaviour. Adds `FocusHold` + `DriftCompensator` at startup and three routes.
> Hardware-adjacent; verified on the rig (Step 4).

- [ ] **Step 1: Delete the dead autofocus code**

In `app.py`, delete the `PIDController` class, `calculate_focus_metric`, `continuous_autofocus`, and
`start_autofocus` functions in full. Remove the `af_enabled`/`af_thread`/`latest_frame` globals that
only the PID used.

- [ ] **Step 2: Construct focus objects at startup**

In the startup block (after `RECORDER = ...` from Phase 2), add:

```python
from core.focus import FocusHold, DriftCompensator, focus_metric

FOCUS = FocusHold(STAGE)
_drift = {"comp": None, "thread": None, "on": False}

def _drift_sample():
    frame = HUB.latest("left")
    return focus_metric(frame.image) if frame is not None else 0.0
```

- [ ] **Step 3: Replace the `/toggle_af` route with Hold Focus routes**

Remove the old `toggle_af` route. Add:

```python
@cross_origin()
@app.route("/set_focus", methods=['POST'])
def set_focus():
    FOCUS.set_focus(ref_metric=_drift_sample())
    return jsonify({"message": "Focus set", "z": FOCUS.stored_z})


@cross_origin()
@app.route("/return_to_focus", methods=['POST'])
def return_to_focus():
    FOCUS.return_to_focus()
    return jsonify({"message": "Returned to focus", "z": FOCUS.stored_z})


@cross_origin()
@app.route("/toggle_drift_comp", methods=['POST'])
def toggle_drift_comp():
    want = request.json.get('drift_enabled') == "True"
    if want and not _drift["on"]:
        comp = DriftCompensator(STAGE, _drift_sample, step_native=2.0)
        _drift["comp"] = comp
        _drift["on"] = True
        def _loop():
            import time as _t
            while _drift["on"]:
                comp.step()
                _t.sleep(0.2)
        t = _threading.Thread(target=_loop, daemon=True)
        _drift["thread"] = t
        t.start()
    elif not want and _drift["on"]:
        _drift["on"] = False
    return jsonify({"drift_enabled": _drift["on"]})
```

- [ ] **Step 4: Verify + manual rig check**

Parse: `python -c "import ast; ast.parse(open('WormSpy/backend/code/app.py').read()); print('parse ok')"` → `parse ok`.
Suite: `cd WormSpy/backend/code && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` → all pass.
On the rig: focus the neuron manually with the joystick, click Set Focus; drift the stage, click Return to Focus → stage snaps back to stored z. Enable drift comp and confirm small z dithering that holds focus (and that you would log z per frame via the CSV from Phase 2).

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "refactor: replace PID autofocus with Hold Focus + drift comp routes (ADR 0002)"
```
End commit message with the Co-Authored-By trailer.

---

### Task 5: Wire the Tracker (restore auto-tracking)

**Files:**
- Modify: `WormSpy/backend/code/app.py`

> Restores auto-tracking on the new architecture and closes the Q8 limit-hit loop. Hardware-verified
> on the rig.

- [ ] **Step 1: Construct + start the Tracker at startup**

In the startup block, after `FOCUS = ...`, add:

```python
from core.tracking import Tracker
from core.tracking_algorithms import make_compute

_track_cfg = dict(factor=2, resolution=(1920, 1200),
                  total_mm_x=TOTAL_MM_X, total_mm_y=TOTAL_MM_Y,
                  mm_microsteps=MM_MST, orient_x=ZABER_ORIENTATION_X,
                  orient_y=ZABER_ORIENTATION_Y, gain=0.2)   # 0.2 ~ legacy /5 damping
TRACKER = Tracker(HUB, STAGE,
                  enabled=lambda: is_tracking,
                  mode=lambda: track_algorithm,
                  compute=make_compute(_track_cfg),
                  on_move=lambda dx, dy, hit: TELEMETRY.set_limit_hit(hit),
                  stream="left", interval_s=0.0)
_threading.Thread(target=TRACKER.run, daemon=True).start()
```

(Use the real frame resolution if you prefer: read it from `HUB.latest("left")` lazily inside
`compute` config — but the fixed `resolution` here matches the legacy default and is fine for v1.)

- [ ] **Step 2: Confirm `toggle_tracking` only updates flags**

Ensure the existing `toggle_tracking` route sets the `is_tracking`/`track_algorithm` globals and
`TELEMETRY.set_tracking(...)` (added in Phase 2) — and nothing else. The Tracker thread reads those
globals via its `enabled`/`mode` callbacks; no per-request tracking work remains.

- [ ] **Step 3: Verify + manual rig check**

Parse + suite as before. On the rig: enable tracking (mode 0/1), confirm the stage follows a moving
target and that when the worm reaches a stage limit the limit-hit flag is set (observable later via
Phase 4 `/status`, or temporarily via a log line).

- [ ] **Step 4: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "refactor: restore auto-tracking via Tracker thread (ADR 0001/Q8)"
```
End commit message with the Co-Authored-By trailer.

---

### Task 6: Restore the manual joystick on STAGE

**Files:**
- Modify: `WormSpy/backend/code/app.py`
- Reference: legacy `start_controller` (pygame axes → motor moves; X/A/B buttons toggled tracking/af/recording).

> Rewrites the manual joystick loop to drive the shared `STAGE` (the legacy version used the removed
> `xMotor/yMotor/zMotor` globals). Joystick + stage; verified on the rig.

- [ ] **Step 1: Rewrite `start_controller` to use STAGE**

Replace the legacy `start_controller` body so axis input maps to `STAGE.move_relative`:

```python
def start_controller():
    import pygame
    global isManualEnabled, is_tracking
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No joystick connected.")
        return
    js = pygame.joystick.Joystick(0)
    js.init()
    last_x = False
    while isManualEnabled:
        pygame.event.pump()
        ix, iy, iz = js.get_axis(0), js.get_axis(1), js.get_axis(3)
        if ix:
            STAGE.move_relative("x", int(ix * 2000))
        if iy:
            STAGE.move_relative("y", int(-iy * 2000))
        if iz:
            STAGE.move_relative("z", int(iz * 100))
        if js.get_numbuttons() > 2:
            cur_x = js.get_button(2)
            if cur_x and not last_x:
                is_tracking = not is_tracking
                TELEMETRY.set_tracking(mode=track_algorithm, is_tracking=is_tracking)
            last_x = cur_x
    pygame.quit()
```

(Keep the existing `toggle_manual` route, which starts this in a thread when manual mode is enabled.)

- [ ] **Step 2: Verify + manual rig check**

Parse + suite. On the rig with a joystick: enable manual mode, confirm the sticks move XY and Z, and
the X button toggles tracking. Confirm manual moves and the tracker do not fight (the Stage clamps
both; if you observe contention, gate the tracker's `enabled` to also require `not isManualEnabled`).

- [ ] **Step 3: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "refactor: restore manual joystick control on shared STAGE"
```
End commit message with the Co-Authored-By trailer.

---

## Self-Review

**Spec coverage (ADR 0002 + control restoration + Q8):**
- *Remove broken PID autofocus* → Task 4 Step 1. ✓
- *Hold/Restore focus (default)* → Tasks 1 (`FocusHold`) + 4 (`/set_focus`, `/return_to_focus`). ✓
- *Opt-in drift compensation, logs z, labelled artifact* → Tasks 2 (`DriftCompensator`) + 4 (`/toggle_drift_comp`); per-frame z logged via Phase 2 CSV. ✓
- *Restore auto-tracking for all modes off the acquisition loop* → Tasks 3 (compute) + 5 (Tracker wiring). ✓
- *Q8 limit-hit surfaced* → Task 5 `on_move=lambda ...: TELEMETRY.set_limit_hit(hit)`. ✓
- *Restore manual joystick* → Task 6. ✓

**Placeholder scan:** No "TBD"/"add error handling"/"similar to". Pure-logic tasks carry full code + tests; app.py/joystick tasks carry full code + explicit manual verification (hardware).

**Type consistency:** `focus_metric(image)->float`, `FocusHold(stage)` with `set_focus/return_to_focus/stored_z/has_focus`, `DriftCompensator(stage, sample_metric, step_native)` consistent across Tasks 1/2/4. `make_compute(cfg)->compute(frame, mode)->(dx,dy)` matches the Phase-1 `Tracker` `ComputeFn` signature and is used in Task 5. `Telemetry.set_limit_hit/set_tracking` match Phase 2 definitions.

**Known follow-ups (not gaps):** real DLC pose function injection (`dlc_pose_fn`) is left to the user's model (guarded so absence = no DLC move); the bright-background threshold constants may need rig tuning (called out in Task 3); UI controls for the new focus/tracking routes are Phase 4.
