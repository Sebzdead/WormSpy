# Acquisition Core Refactor (ADR 0001) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move camera acquisition out of the Flask MJPEG handlers into a single application-owned acquisition loop that grabs both cameras in lockstep, assigns a shared monotonic `frame_index`, publishes the latest frame for display, and feeds a tracking thread — eliminating inter-camera loop drift and the "motors die when the browser disconnects" failure class.

**Architecture:** A small `core` package introduces three seams that can be faked in tests — `Camera`, `Stage`, and a thread-safe `FrameHub` — plus an `AcquisitionEngine` that orchestrates them and a `Tracker` thread that consumes the hub. Pure logic and orchestration are unit-tested against fakes on the dev machine; the hardware adapters (`ZaberStage`, `SpinnakerCamera`) are thin and verified manually on the rig. `app.py` becomes thin wiring: it constructs the engine at startup and exposes display endpoints that read from the hub.

**Tech Stack:** Python 3.8 (rig) / 3.12 (dev), Flask, NumPy, OpenCV, pytest. Hardware libs (`EasyPySpin`, `pypylon`, `zaber_motion`) are imported only inside adapter modules so tests never load them.

**Scope:** This is Phase 1 of a 4-phase roadmap (see `docs/adr/0001-0003`). It deliberately does NOT implement the new recording format (Phase 2 / ADR 0003), the Hold Focus redesign (Phase 3 / ADR 0002), or the `/status` endpoint and UI changes (Phase 4 / Q6). Recording and focus are left calling their existing code paths, adapted minimally to read frames from the hub, so the app keeps working after each task.

---

## File Structure

All paths are relative to `WormSpy/backend/code/`.

- Create: `core/__init__.py` — package marker.
- Create: `core/frames.py` — `Frame` and `AcquiredPair` dataclasses; the `FrameHub` thread-safe latest-frame store.
- Create: `core/cameras.py` — `Camera` ABC, `FakeCamera` (test double). Hardware adapter `SpinnakerCamera` added in Task 8.
- Create: `core/stage.py` — `Stage` ABC, `clamp_native()` pure helper, `NullStage`, `FakeStage`. Hardware adapter `ZaberStage` + `connect_stage()` factory added in Task 7.
- Create: `core/acquisition.py` — `AcquisitionEngine`.
- Create: `core/tracking.py` — pure tracking helpers moved from `app.py` (`thresh_light_background`, `thresh_fluorescent_marker`, `find_worm_cms`, `simple_to_center`) plus the `Tracker` thread.
- Create: `tests/conftest.py`, `tests/test_frames.py`, `tests/test_cameras.py`, `tests/test_stage.py`, `tests/test_acquisition.py`, `tests/test_tracking.py`.
- Create: `requirements-dev.txt`, `pytest.ini`.
- Modify: `app.py` — remove acquisition/motor setup from `video_feed`/`video_feed_fluorescent`; construct engine + stage at startup; serve display from the hub.

---

### Task 1: Test harness and package skeleton

**Files:**
- Create: `WormSpy/backend/code/core/__init__.py`
- Create: `WormSpy/backend/code/tests/conftest.py`
- Create: `WormSpy/backend/code/pytest.ini`
- Create: `WormSpy/backend/code/requirements-dev.txt`

- [ ] **Step 1: Create the package marker**

Create `core/__init__.py`:

```python
"""WormsPy acquisition core: hardware-independent acquisition, tracking, and stage control."""
```

- [ ] **Step 2: Create pytest config**

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v
```

- [ ] **Step 3: Create conftest to make `core` importable from the code dir**

Create `tests/conftest.py`:

```python
import os
import sys

# Ensure `import core...` resolves when pytest is run from WormSpy/backend/code
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 4: Pin dev-only deps**

Create `requirements-dev.txt`:

```text
pytest>=7.0
numpy>=1.20
```

- [ ] **Step 5: Verify pytest collects an empty suite**

Run: `cd WormSpy/backend/code && python -m pytest`
Expected: exit code 5 ("no tests ran") — confirms config is valid and collection works.

- [ ] **Step 6: Commit**

```bash
git add WormSpy/backend/code/core/__init__.py WormSpy/backend/code/pytest.ini WormSpy/backend/code/tests/conftest.py WormSpy/backend/code/requirements-dev.txt
git commit -m "test: add pytest harness and core package skeleton"
```

---

### Task 2: Frame types and the thread-safe FrameHub

**Files:**
- Create: `WormSpy/backend/code/core/frames.py`
- Test: `WormSpy/backend/code/tests/test_frames.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_frames.py`:

```python
import threading
import numpy as np
from core.frames import Frame, AcquiredPair, FrameHub


def _frame(val, fid=0, ts=0.0):
    img = np.full((4, 4), val, dtype=np.uint8)
    return Frame(image=img, device_frame_id=fid, capture_ts=ts)


def test_hub_returns_none_before_first_publish():
    hub = FrameHub()
    assert hub.latest("left") is None


def test_hub_returns_most_recent_frame():
    hub = FrameHub()
    hub.publish("left", _frame(1, fid=1))
    hub.publish("left", _frame(2, fid=2))
    latest = hub.latest("left")
    assert latest.device_frame_id == 2
    assert int(latest.image[0, 0]) == 2


def test_hub_keeps_streams_independent():
    hub = FrameHub()
    hub.publish("left", _frame(1))
    hub.publish("right", _frame(9))
    assert int(hub.latest("left").image[0, 0]) == 1
    assert int(hub.latest("right").image[0, 0]) == 9


def test_hub_is_thread_safe_under_concurrent_writes():
    hub = FrameHub()
    def writer(start):
        for i in range(start, start + 500):
            hub.publish("left", _frame(i % 256, fid=i))
    threads = [threading.Thread(target=writer, args=(s,)) for s in (0, 1000)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No exception and a valid frame is present
    assert hub.latest("left") is not None


def test_acquired_pair_fields():
    pair = AcquiredPair(frame_index=7, capture_ts=1.5, left=_frame(1), right=_frame(2))
    assert pair.frame_index == 7
    assert pair.capture_ts == 1.5
    assert pair.left.device_frame_id == 0
    assert pair.right is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_frames.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.frames'`

- [ ] **Step 3: Write minimal implementation**

Create `core/frames.py`:

```python
import threading
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass
class Frame:
    """A single grabbed image plus the metadata needed to align it offline."""
    image: np.ndarray
    device_frame_id: int   # camera-provided frame id; -1 if the camera cannot supply one
    capture_ts: float      # time.monotonic() captured as close to the grab as possible


@dataclass
class AcquiredPair:
    """One acquisition tick: a brightfield and a fluorescence frame sharing an index."""
    frame_index: int       # monotonic counter assigned by the engine, contiguous, never reused
    capture_ts: float      # engine timestamp for the tick (time.monotonic())
    left: Optional[Frame]  # brightfield/behaviour; None if that grab failed this tick
    right: Optional[Frame] # fluorescence/calcium; None if that grab failed this tick


class FrameHub:
    """Thread-safe store of the latest frame per named stream, for display and tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Dict[str, Frame] = {}

    def publish(self, name: str, frame: Frame) -> None:
        with self._lock:
            self._latest[name] = frame

    def latest(self, name: str) -> Optional[Frame]:
        with self._lock:
            return self._latest.get(name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_frames.py`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/frames.py WormSpy/backend/code/tests/test_frames.py
git commit -m "feat: add Frame/AcquiredPair types and thread-safe FrameHub"
```

---

### Task 3: Camera interface and FakeCamera

**Files:**
- Create: `WormSpy/backend/code/core/cameras.py`
- Test: `WormSpy/backend/code/tests/test_cameras.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cameras.py`:

```python
import numpy as np
from core.cameras import Camera, FakeCamera


def test_fake_camera_yields_scripted_frames_in_order():
    cam = FakeCamera(images=[np.full((2, 2), 1, np.uint8),
                             np.full((2, 2), 2, np.uint8)])
    f0 = cam.read()
    f1 = cam.read()
    assert int(f0.image[0, 0]) == 1
    assert int(f1.image[0, 0]) == 2


def test_fake_camera_assigns_incrementing_device_frame_ids():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)] * 3)
    ids = [cam.read().device_frame_id for _ in range(3)]
    assert ids == [0, 1, 2]


def test_fake_camera_can_simulate_dropped_frames():
    # device_frame_ids 0,1,3 -> a gap (2 dropped) that downstream code can detect
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)] * 3, frame_ids=[0, 1, 3])
    ids = [cam.read().device_frame_id for _ in range(3)]
    assert ids == [0, 1, 3]


def test_fake_camera_returns_none_when_exhausted():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)])
    assert cam.read() is not None
    assert cam.read() is None


def test_fake_camera_read_failure_returns_none():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)], fail_on={0})
    assert cam.read() is None


def test_fake_camera_is_open_until_released():
    cam = FakeCamera(images=[np.zeros((2, 2), np.uint8)])
    assert cam.is_open() is True
    cam.release()
    assert cam.is_open() is False


def test_fake_camera_satisfies_camera_interface():
    assert issubclass(FakeCamera, Camera)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_cameras.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.cameras'`

- [ ] **Step 3: Write minimal implementation**

Create `core/cameras.py`:

```python
import time
from abc import ABC, abstractmethod
from typing import Iterable, List, Optional, Set

import numpy as np

from core.frames import Frame


class Camera(ABC):
    """Acquisition interface. Adapters wrap real SDKs; FakeCamera is the test double."""

    @abstractmethod
    def read(self) -> Optional[Frame]:
        """Grab the next frame, or return None if the grab failed / stream ended."""

    @abstractmethod
    def is_open(self) -> bool:
        ...

    @abstractmethod
    def release(self) -> None:
        ...


class FakeCamera(Camera):
    """Deterministic camera for tests. Emits a scripted list of images."""

    def __init__(self, images: Iterable[np.ndarray],
                 frame_ids: Optional[List[int]] = None,
                 fail_on: Optional[Set[int]] = None) -> None:
        self._images: List[np.ndarray] = list(images)
        self._frame_ids = frame_ids if frame_ids is not None else list(range(len(self._images)))
        self._fail_on = fail_on or set()
        self._pos = 0
        self._open = True

    def read(self) -> Optional[Frame]:
        if not self._open or self._pos >= len(self._images):
            return None
        idx = self._pos
        self._pos += 1
        if idx in self._fail_on:
            return None
        return Frame(image=self._images[idx],
                     device_frame_id=self._frame_ids[idx],
                     capture_ts=time.monotonic())

    def is_open(self) -> bool:
        return self._open

    def release(self) -> None:
        self._open = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_cameras.py`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/cameras.py WormSpy/backend/code/tests/test_cameras.py
git commit -m "feat: add Camera interface and FakeCamera test double"
```

---

### Task 4: Stage interface, clamp helper, Null/Fake stages

**Files:**
- Create: `WormSpy/backend/code/core/stage.py`
- Test: `WormSpy/backend/code/tests/test_stage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_stage.py`:

```python
import pytest
from core.stage import Stage, NullStage, FakeStage, clamp_native


def test_clamp_within_bounds_is_unchanged():
    value, hit = clamp_native(50.0, 0.0, 100.0)
    assert value == 50.0
    assert hit is False


def test_clamp_above_max_pins_to_max_and_flags():
    value, hit = clamp_native(150.0, 0.0, 100.0)
    assert value == 100.0
    assert hit is True


def test_clamp_below_min_pins_to_min_and_flags():
    value, hit = clamp_native(-5.0, 0.0, 100.0)
    assert value == 0.0
    assert hit is True


def test_null_stage_is_not_connected():
    stage = NullStage()
    assert stage.is_connected() is False


def test_null_stage_moves_are_noops():
    stage = NullStage()
    # Must not raise even though there is no hardware
    stage.move_relative("x", 100.0)
    assert stage.get_position("x") == 0.0


def test_fake_stage_tracks_relative_moves_and_clamps():
    stage = FakeStage(limits={"x": (0.0, 100.0)}, positions={"x": 90.0})
    hit = stage.move_relative("x", 50.0)  # 90 + 50 = 140 -> clamp to 100
    assert stage.get_position("x") == 100.0
    assert hit is True


def test_fake_stage_relative_move_within_bounds_does_not_flag():
    stage = FakeStage(limits={"x": (0.0, 100.0)}, positions={"x": 10.0})
    hit = stage.move_relative("x", 5.0)
    assert stage.get_position("x") == 15.0
    assert hit is False


def test_fake_stage_satisfies_stage_interface():
    assert issubclass(FakeStage, Stage)
    assert issubclass(NullStage, Stage)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_stage.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.stage'`

- [ ] **Step 3: Write minimal implementation**

Create `core/stage.py`:

```python
from abc import ABC, abstractmethod
from typing import Dict, Tuple


def clamp_native(value: float, minimum: float, maximum: float) -> Tuple[float, bool]:
    """Clamp value into [minimum, maximum]. Returns (clamped_value, limit_was_hit)."""
    if value > maximum:
        return maximum, True
    if value < minimum:
        return minimum, True
    return value, False


class Stage(ABC):
    """XYZ stage interface in native (microstep) units. Adapters wrap real hardware."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def get_position(self, axis: str) -> float:
        ...

    @abstractmethod
    def limits(self, axis: str) -> Tuple[float, float]:
        ...

    @abstractmethod
    def move_relative(self, axis: str, native: float) -> bool:
        """Move axis by `native` microsteps, clamped to limits. Returns True if a limit was hit."""

    @abstractmethod
    def move_absolute(self, axis: str, native: float) -> bool:
        """Move axis to absolute `native` microsteps, clamped to limits. Returns True if hit."""


class NullStage(Stage):
    """Used when no hardware is present so the app still runs. All moves are no-ops."""

    def is_connected(self) -> bool:
        return False

    def get_position(self, axis: str) -> float:
        return 0.0

    def limits(self, axis: str) -> Tuple[float, float]:
        return (0.0, 0.0)

    def move_relative(self, axis: str, native: float) -> bool:
        return False

    def move_absolute(self, axis: str, native: float) -> bool:
        return False


class FakeStage(Stage):
    """In-memory stage for tests. Honors limits via clamp_native."""

    def __init__(self, limits: Dict[str, Tuple[float, float]],
                 positions: Dict[str, float]) -> None:
        self._limits = dict(limits)
        self._pos = dict(positions)

    def is_connected(self) -> bool:
        return True

    def get_position(self, axis: str) -> float:
        return self._pos[axis]

    def limits(self, axis: str) -> Tuple[float, float]:
        return self._limits[axis]

    def move_relative(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        target, hit = clamp_native(self._pos[axis] + native, lo, hi)
        self._pos[axis] = target
        return hit

    def move_absolute(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        target, hit = clamp_native(native, lo, hi)
        self._pos[axis] = target
        return hit
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_stage.py`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/stage.py WormSpy/backend/code/tests/test_stage.py
git commit -m "feat: add Stage interface, clamp helper, Null/Fake stages"
```

---

### Task 5: AcquisitionEngine (the core)

**Files:**
- Create: `WormSpy/backend/code/core/acquisition.py`
- Test: `WormSpy/backend/code/tests/test_acquisition.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_acquisition.py`:

```python
import numpy as np
from core.cameras import FakeCamera
from core.frames import FrameHub
from core.acquisition import AcquisitionEngine


def _imgs(n, base=0):
    return [np.full((2, 2), (base + i) % 256, np.uint8) for i in range(n)]


def test_run_once_assigns_contiguous_monotonic_indices():
    left = FakeCamera(_imgs(3, base=0))
    right = FakeCamera(_imgs(3, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    indices = []
    for _ in range(3):
        pair = engine.run_once()
        indices.append(pair.frame_index)
    assert indices == [0, 1, 2]


def test_run_once_pairs_left_and_right_per_tick():
    left = FakeCamera(_imgs(2, base=0))
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    pair = engine.run_once()
    assert int(pair.left.image[0, 0]) == 0
    assert int(pair.right.image[0, 0]) == 100


def test_run_once_publishes_latest_to_hub():
    hub = FrameHub()
    left = FakeCamera(_imgs(1, base=5))
    right = FakeCamera(_imgs(1, base=50))
    engine = AcquisitionEngine(left, right, hub)
    engine.run_once()
    assert int(hub.latest("left").image[0, 0]) == 5
    assert int(hub.latest("right").image[0, 0]) == 50


def test_run_once_feeds_the_sink():
    received = []
    left = FakeCamera(_imgs(2))
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub(), sink=received.append)
    engine.run_once()
    engine.run_once()
    assert [p.frame_index for p in received] == [0, 1]


def test_one_camera_failing_still_increments_index_and_keeps_other():
    # Left fails on tick 0; right is fine. The pair has left=None but a valid index + right.
    left = FakeCamera(_imgs(2), fail_on={0})
    right = FakeCamera(_imgs(2, base=100))
    engine = AcquisitionEngine(left, right, FrameHub())
    pair0 = engine.run_once()
    assert pair0.frame_index == 0
    assert pair0.left is None
    assert pair0.right is not None
    pair1 = engine.run_once()
    assert pair1.frame_index == 1
    assert pair1.left is not None


def test_device_frame_ids_are_preserved_for_drop_detection():
    left = FakeCamera(_imgs(3), frame_ids=[0, 1, 3])   # camera dropped id 2
    right = FakeCamera(_imgs(3, base=100), frame_ids=[0, 1, 2])
    engine = AcquisitionEngine(left, right, FrameHub())
    left_ids = [engine.run_once().left.device_frame_id for _ in range(3)]
    assert left_ids == [0, 1, 3]


def test_run_loop_stops_and_drains_until_stream_ends():
    left = FakeCamera(_imgs(4))
    right = FakeCamera(_imgs(4, base=100))
    sink = []
    engine = AcquisitionEngine(left, right, FrameHub(), sink=sink.append)
    engine.run(max_ticks=4)
    assert [p.frame_index for p in sink] == [0, 1, 2, 3]


def test_run_loop_exits_when_stop_called():
    left = FakeCamera(_imgs(1000))
    right = FakeCamera(_imgs(1000, base=1))
    engine = AcquisitionEngine(left, right, FrameHub())
    # stop before running; loop should not produce more than a couple of ticks
    engine.stop()
    engine.run(max_ticks=1000)
    assert engine.ticks_completed <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_acquisition.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.acquisition'`

- [ ] **Step 3: Write minimal implementation**

Create `core/acquisition.py`:

```python
import threading
import time
from typing import Callable, Optional

from core.cameras import Camera
from core.frames import AcquiredPair, FrameHub

Sink = Callable[[AcquiredPair], None]


class AcquisitionEngine:
    """Single loop that owns both cameras. Grabs both per tick, assigns a shared monotonic
    frame_index, publishes the latest frame of each to the hub for display/tracking, and
    forwards the paired result to an optional sink (the recorder). This is the only place that
    reads from the cameras, so the two streams cannot drift relative to each other."""

    LEFT = "left"
    RIGHT = "right"

    def __init__(self, left: Camera, right: Camera, hub: FrameHub,
                 sink: Optional[Sink] = None) -> None:
        self._left = left
        self._right = right
        self._hub = hub
        self._sink = sink
        self._next_index = 0
        self._stop = threading.Event()
        self.ticks_completed = 0

    def run_once(self) -> AcquiredPair:
        index = self._next_index
        self._next_index += 1
        ts = time.monotonic()

        left_frame = self._left.read()
        right_frame = self._right.read()

        if left_frame is not None:
            self._hub.publish(self.LEFT, left_frame)
        if right_frame is not None:
            self._hub.publish(self.RIGHT, right_frame)

        pair = AcquiredPair(frame_index=index, capture_ts=ts,
                            left=left_frame, right=right_frame)
        if self._sink is not None:
            self._sink(pair)
        self.ticks_completed += 1
        return pair

    def run(self, max_ticks: Optional[int] = None) -> None:
        produced = 0
        while not self._stop.is_set():
            if max_ticks is not None and produced >= max_ticks:
                break
            if not (self._left.is_open() and self._right.is_open()):
                break
            self.run_once()
            produced += 1

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_acquisition.py`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/acquisition.py WormSpy/backend/code/tests/test_acquisition.py
git commit -m "feat: add AcquisitionEngine with shared monotonic frame index"
```

---

### Task 6: Tracking helpers and the Tracker thread

**Files:**
- Create: `WormSpy/backend/code/core/tracking.py`
- Test: `WormSpy/backend/code/tests/test_tracking.py`
- Reference (copy logic from): `app.py:544-613` (`simpleToCenter`, `find_worm_cms`)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracking.py`:

```python
import numpy as np
from core.frames import FrameHub, Frame
from core.stage import FakeStage
from core.tracking import find_worm_cms, simple_to_center, Tracker


def test_find_worm_cms_returns_centroid_of_largest_blob():
    # 40x40 frame, a bright square blob centered near (10, 20) in (row, col)
    frame = np.zeros((40, 40), np.uint8)
    frame[8:13, 18:23] = 255       # rows 8-12, cols 18-22 -> centroid ~ (10, 20)
    x, y = find_worm_cms(frame, factor=1, initial_coords=(20, 20))
    # returned as (x=col, y=row)
    assert abs(x - 20) <= 1
    assert abs(y - 10) <= 1


def test_find_worm_cms_applies_downsample_factor():
    frame = np.zeros((20, 20), np.uint8)
    frame[9:11, 9:11] = 255        # centroid ~ (9.5, 9.5)
    x, y = find_worm_cms(frame, factor=2, initial_coords=(10, 10))
    assert abs(x - 19) <= 2        # ~9.5 * 2
    assert abs(y - 19) <= 2


def test_find_worm_cms_falls_back_to_initial_when_empty():
    frame = np.zeros((10, 10), np.uint8)
    assert find_worm_cms(frame, factor=1, initial_coords=(5, 5)) == (5, 5)


def test_simple_to_center_centered_worm_needs_no_move():
    # worm exactly at center -> zero move
    mx, my = simple_to_center(960, 600, resolution=(1920, 1200),
                              total_mm_x=1.92, total_mm_y=1.2,
                              orient_x=1, orient_y=-1)
    assert abs(mx) < 1e-9
    assert abs(my) < 1e-9


def test_simple_to_center_respects_orientation_sign():
    # worm to the right of center -> positive-ish move scaled by orient_x
    mx, _ = simple_to_center(1920, 600, resolution=(1920, 1200),
                             total_mm_x=1.92, total_mm_y=1.2,
                             orient_x=1, orient_y=-1)
    assert mx > 0
    mx_inv, _ = simple_to_center(1920, 600, resolution=(1920, 1200),
                                 total_mm_x=1.92, total_mm_y=1.2,
                                 orient_x=-1, orient_y=-1)
    assert mx_inv < 0


def test_tracker_step_does_nothing_when_disabled():
    hub = FrameHub()
    hub.publish("left", Frame(np.zeros((40, 40), np.uint8), 0, 0.0))
    stage = FakeStage(limits={"x": (0.0, 1e9), "y": (0.0, 1e9)},
                      positions={"x": 100.0, "y": 100.0})
    tracker = Tracker(hub, stage, enabled=lambda: False, mode=lambda: 0,
                      compute=lambda frame, mode: (5, 5))
    tracker.step()
    assert stage.get_position("x") == 100.0  # unchanged


def test_tracker_step_commands_stage_when_enabled():
    hub = FrameHub()
    hub.publish("left", Frame(np.zeros((40, 40), np.uint8), 0, 0.0))
    stage = FakeStage(limits={"x": (-1e9, 1e9), "y": (-1e9, 1e9)},
                      positions={"x": 0.0, "y": 0.0})
    moves = []
    tracker = Tracker(hub, stage, enabled=lambda: True, mode=lambda: 0,
                      compute=lambda frame, mode: (123.0, -45.0),
                      on_move=lambda dx, dy, hit: moves.append((dx, dy)))
    tracker.step()
    assert moves == [(123.0, -45.0)]
    assert stage.get_position("x") == 123.0
    assert stage.get_position("y") == -45.0


def test_tracker_step_skips_when_no_frame_available():
    hub = FrameHub()  # nothing published
    stage = FakeStage(limits={"x": (0.0, 10.0), "y": (0.0, 10.0)},
                      positions={"x": 1.0, "y": 1.0})
    tracker = Tracker(hub, stage, enabled=lambda: True, mode=lambda: 0,
                      compute=lambda frame, mode: (5, 5))
    tracker.step()  # must not raise
    assert stage.get_position("x") == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_tracking.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.tracking'`

- [ ] **Step 3: Write minimal implementation**

Create `core/tracking.py`:

```python
"""Pure tracking helpers (moved from app.py) and the Tracker thread.

The Tracker reads the latest brightfield frame from the hub, computes a target stage move via
an injected `compute` callback, and commands the stage. Compute is injected so the heavy/slow
algorithm (e.g. DeepLabCut) can run here without ever throttling the AcquisitionEngine."""

import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np
from skimage.measure import label, regionprops

from core.frames import FrameHub
from core.stage import Stage


def find_worm_cms(processed_frame: np.ndarray, factor: int,
                  initial_coords: Tuple[float, float]) -> Tuple[float, float]:
    """Centroid (x=col, y=row) of the largest connected region, rescaled by `factor`.
    Falls back to initial_coords when no region is found."""
    labeled = label(processed_frame)
    regions = regionprops(labeled)
    regions_by_area = sorted(regions, key=lambda r: r.area, reverse=True)
    if regions_by_area:
        coords = regions_by_area[0].centroid           # (row, col)
    else:
        return initial_coords
    coords = (round(coords[1]), round(coords[0]))       # -> (x=col, y=row)
    return (coords[0] * factor, coords[1] * factor)


def simple_to_center(centroid_x: float, centroid_y: float,
                     resolution: Tuple[float, float],
                     total_mm_x: float, total_mm_y: float,
                     orient_x: int, orient_y: int) -> Tuple[float, float]:
    """Millimetres the stage must move to bring (centroid_x, centroid_y) to frame center."""
    percent_x = float(centroid_x) / float(resolution[0])
    percent_y = float(centroid_y) / float(resolution[1])
    millis_x = percent_x * total_mm_x
    millis_y = percent_y * total_mm_y
    move_x = orient_x * (millis_x - total_mm_x / 2)
    move_y = orient_y * (millis_y - total_mm_y / 2)
    return move_x, move_y


# Callback types
EnabledFn = Callable[[], bool]
ModeFn = Callable[[], int]
ComputeFn = Callable[[np.ndarray, int], Tuple[float, float]]   # (frame, mode) -> (dx, dy) native
OnMoveFn = Callable[[float, float, bool], None]


class Tracker:
    """Runs in its own thread. Best-effort consumer of the latest brightfield frame."""

    def __init__(self, hub: FrameHub, stage: Stage,
                 enabled: EnabledFn, mode: ModeFn, compute: ComputeFn,
                 on_move: Optional[OnMoveFn] = None,
                 stream: str = "left", interval_s: float = 0.0) -> None:
        self._hub = hub
        self._stage = stage
        self._enabled = enabled
        self._mode = mode
        self._compute = compute
        self._on_move = on_move
        self._stream = stream
        self._interval_s = interval_s
        self._stop = threading.Event()

    def step(self) -> None:
        if not self._enabled():
            return
        frame = self._hub.latest(self._stream)
        if frame is None:
            return
        dx, dy = self._compute(frame.image, self._mode())
        hit_x = self._stage.move_relative("x", dx)
        hit_y = self._stage.move_relative("y", dy)
        if self._on_move is not None:
            self._on_move(dx, dy, hit_x or hit_y)

    def run(self) -> None:
        while not self._stop.is_set():
            self.step()
            if self._interval_s:
                time.sleep(self._interval_s)

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_tracking.py`
Expected: PASS (9 passed)

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

Run: `cd WormSpy/backend/code && python -m pytest`
Expected: PASS (all tests across the 5 files)

- [ ] **Step 6: Commit**

```bash
git add WormSpy/backend/code/core/tracking.py WormSpy/backend/code/tests/test_tracking.py
git commit -m "feat: add pure tracking helpers and Tracker thread"
```

---

### Task 7: ZaberStage adapter and connect-once factory

**Files:**
- Create: `WormSpy/backend/code/core/stage_zaber.py`
- Test: `WormSpy/backend/code/tests/test_stage.py` (extend)
- Reference (copy logic from): `app.py:127-142` (connection/detect), `app.py:573-576` (move command format), `app.py:457-462` (limits)

> **Note:** `ZaberStage` itself talks to hardware and is verified manually on the rig (Step 5). Only the connection *factory's fallback* is unit-tested, by injecting a connector that raises.

- [ ] **Step 1: Write the failing test (factory fallback only)**

Append to `tests/test_stage.py`:

```python
from core.stage_zaber import connect_stage


def test_connect_stage_returns_null_stage_when_connection_fails():
    def boom(*_args, **_kwargs):
        raise RuntimeError("no serial port")
    stage = connect_stage(xy_port="COM6", z_port="COM3", connector=boom)
    assert stage.is_connected() is False  # NullStage


def test_connect_stage_uses_injected_connector_result():
    sentinel = FakeStage(limits={"x": (0.0, 1.0), "y": (0.0, 1.0), "z": (0.0, 1.0)},
                         positions={"x": 0.0, "y": 0.0, "z": 0.0})
    stage = connect_stage(xy_port="COM6", z_port="COM3", connector=lambda **_: sentinel)
    assert stage is sentinel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_stage.py -k connect_stage`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.stage_zaber'`

- [ ] **Step 3: Write minimal implementation**

Create `core/stage_zaber.py`:

```python
"""Zaber hardware adapter. Imports zaber_motion lazily so tests never load the SDK."""

from typing import Callable, Optional, Tuple

from core.stage import Stage, NullStage, clamp_native


class ZaberStage(Stage):
    """Wraps three Zaber axes (x, y on the XY connection; z on the Z connection).

    Connections are opened once at construction and held for the process lifetime."""

    def __init__(self, xy_port: str, z_port: str) -> None:
        from zaber_motion import Library                       # lazy import
        from zaber_motion.ascii import Connection
        Library.enable_device_db_store()

        self._xy_conn = Connection.open_serial_port(xy_port)
        self._z_conn = Connection.open_serial_port(z_port)
        self._xy_conn.enable_alerts()
        self._z_conn.enable_alerts()

        horizontal = self._xy_conn.detect_devices()
        vertical = self._z_conn.detect_devices()
        self._axes = {
            "x": horizontal[0].get_axis(1),
            "y": horizontal[1].get_axis(1),
            "z": vertical[0].get_axis(1),
        }
        self._limits = {
            name: (float(axis.settings.get("limit.min")),
                   float(axis.settings.get("limit.max")))
            for name, axis in self._axes.items()
        }

    def is_connected(self) -> bool:
        return True

    def get_position(self, axis: str) -> float:
        from zaber_motion import Units
        return float(self._axes[axis].get_position(unit=Units.NATIVE))

    def limits(self, axis: str) -> Tuple[float, float]:
        return self._limits[axis]

    def move_relative(self, axis: str, native: float) -> bool:
        lo, hi = self._limits[axis]
        current = self.get_position(axis)
        target, hit = clamp_native(current + native, lo, hi)
        delta = int(target - current)
        if delta != 0:
            self._axes[axis].generic_command_no_response(f"move rel {delta} 10000 5")
        return hit

    def move_absolute(self, axis: str, native: float) -> bool:
        from zaber_motion import Units
        lo, hi = self._limits[axis]
        target, hit = clamp_native(native, lo, hi)
        self._axes[axis].move_absolute(target, unit=Units.NATIVE, wait_until_idle=False)
        return hit


def connect_stage(xy_port: str, z_port: str,
                  connector: Optional[Callable[..., Stage]] = None) -> Stage:
    """Open the stage once at startup. On any failure, return a NullStage so the server still
    runs (e.g. for UI/dev work with no hardware). `connector` is injectable for tests."""
    factory = connector if connector is not None else (lambda **kw: ZaberStage(**kw))
    try:
        return factory(xy_port=xy_port, z_port=z_port)
    except Exception as exc:   # noqa: BLE001 - degrade gracefully on any hardware/SDK error
        print(f"connect_stage: no stage hardware ({exc!r}); using NullStage.")
        return NullStage()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd WormSpy/backend/code && python -m pytest tests/test_stage.py -k connect_stage`
Expected: PASS (2 passed)

- [ ] **Step 5: Manual hardware verification (on the rig only)**

On the rig with Zaber connected and ZaberLauncher running, in a Python shell from `WormSpy/backend/code`:

```python
from core.stage_zaber import connect_stage
s = connect_stage("COM6", "COM3")
print(s.is_connected(), s.limits("x"))
s.move_relative("x", 1000)   # observe the XY stage step; confirm no error
```

Expected: `True` and a `(min, max)` tuple; the stage moves a small step.

- [ ] **Step 6: Commit**

```bash
git add WormSpy/backend/code/core/stage_zaber.py WormSpy/backend/code/tests/test_stage.py
git commit -m "feat: add ZaberStage adapter with graceful connect-once fallback"
```

---

### Task 8: SpinnakerCamera adapter (camera-as-clock)

**Files:**
- Create: `WormSpy/backend/code/core/cameras_spinnaker.py`
- Reference (copy logic from): `app.py:108-109` (open + resolution), `test_segmentation.py:6-13` (EasyPySpin read + uint8 normalize)

> **Note:** This adapter talks to hardware and is verified manually (Step 4). It configures the camera to be the clock: fixed frame rate + fixed exposure, so every recorded frame shares an exposure and the rate does not wobble with software load (ADR 0001 decision #2).

- [ ] **Step 1: Write the adapter**

Create `core/cameras_spinnaker.py`:

```python
"""FLIR/Spinnaker camera adapter via EasyPySpin. Imports the SDK lazily so tests don't load it.

Sets the camera as the acquisition clock: a fixed AcquisitionFrameRate and a fixed exposure,
with auto-exposure/auto-gain off. EasyPySpin exposes these through cv2-style properties; the
device frame id is read from the grabbed image metadata when available, else -1."""

import time
from typing import Optional

import cv2
import numpy as np

from core.cameras import Camera
from core.frames import Frame


class SpinnakerCamera(Camera):
    def __init__(self, identifier, frame_rate: float, exposure_us: float) -> None:
        import EasyPySpin    # lazy import
        self._cap = EasyPySpin.VideoCapture(identifier)
        if not self._cap.isOpened():
            print(f"SpinnakerCamera: could not open camera {identifier!r}")
            return
        # Camera is the clock: disable auto, pin rate + exposure.
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)        # manual exposure
        self._cap.set(cv2.CAP_PROP_EXPOSURE, exposure_us)
        self._cap.set(cv2.CAP_PROP_FPS, frame_rate)
        self.width = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        self.height = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    def read(self) -> Optional[Frame]:
        ok, image = self._cap.read()
        ts = time.monotonic()
        if not ok or image is None:
            return None
        if image.dtype != np.uint8:
            # Preserve raw for recording elsewhere; this path is display-oriented in Phase 1.
            image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        frame_id = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
        return Frame(image=image, device_frame_id=frame_id, capture_ts=ts)

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
```

- [ ] **Step 2: Import-smoke check (dev machine, no camera)**

Run: `cd WormSpy/backend/code && python -c "import ast; ast.parse(open('core/cameras_spinnaker.py').read()); print('parse ok')"`
Expected: `parse ok` (syntax valid; the SDK is not imported because the lazy import is inside `__init__`).

- [ ] **Step 3: Confirm the rest of the suite is unaffected**

Run: `cd WormSpy/backend/code && python -m pytest`
Expected: PASS (no new tests; nothing regressed)

- [ ] **Step 4: Manual hardware verification (on the rig only)**

With a FLIR camera connected, from `WormSpy/backend/code`:

```python
from core.cameras_spinnaker import SpinnakerCamera
cam = SpinnakerCamera(0, frame_rate=10.0, exposure_us=10000)
f = cam.read()
print(cam.is_open(), None if f is None else (f.image.shape, f.device_frame_id))
cam.release()
```

Expected: `True` and a `(shape, frame_id)` tuple printed.

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/cameras_spinnaker.py
git commit -m "feat: add SpinnakerCamera adapter configured as the acquisition clock"
```

---

### Task 9: Wire the engine into app.py; serve display from the hub

**Files:**
- Modify: `WormSpy/backend/code/app.py`
  - Replace per-request camera/motor setup in `video_feed` (`app.py:104-237`) and `video_feed_fluorescent` (`app.py:240-313`).
  - Add startup construction near `app = Flask(...)` (`app.py:24-27`).
  - Repoint `move_to_center` (`app.py:454-463`) and the manual controller (`app.py:656-749`) at the shared `STAGE`.

> **Note:** This task is hardware-wired and validated by launching the app on the rig (Step 5). It removes acquisition from the HTTP handlers (the root cause of loop drift and motor-on-disconnect). The engine `sink` is wired but set to a no-op placeholder.
>
> **ACCEPTED TEMPORARY REGRESSION (decided 2026-06-21):** Because the legacy recording loop lived *inside* the old `video_feed` generators and autofocus/manual/tracking depended on globals those generators set, this minimal Task 9 leaves the app with working **dual display + Center** only. Recording is a silent no-op; `toggle_af` and `toggle_manual` will return HTTP 500 (NameError on the removed motor globals); `toggle_tracking` does nothing (no consumer). These are restored in Phases 2–3 (recording/codec, Hold Focus, and wiring the `Tracker` with the ported algorithms). Do **not** patch the orphaned code in this task.
>
> **Insertion-order correction:** place the startup wiring block (Step 1) *after* the configuration constants (after the `settings = {...}` dict / `FPS`), NOT after the CORS block — `STAGE = connect_stage(XYmotorport, Zmotorport)` executes at import time and needs `XYmotorport`/`Zmotorport`/`FPS` already defined.

- [ ] **Step 1: Add startup wiring after the Flask app is created**

In `app.py`, immediately after the `CORS(...)` block (after `app.py:28`), add:

```python
from core.frames import FrameHub
from core.cameras_spinnaker import SpinnakerCamera
from core.stage_zaber import connect_stage
from core.acquisition import AcquisitionEngine
from core.tracking import Tracker, find_worm_cms, simple_to_center
import threading as _threading

# Shared singletons constructed once at startup (replaces per-request setup).
HUB = FrameHub()
STAGE = connect_stage(XYmotorport, Zmotorport)   # NullStage if no hardware
ENGINE = None            # set when the live feed starts (cameras chosen in the UI)
ACQ_THREAD = None

def _start_engine(left_id, right_id):
    global ENGINE, ACQ_THREAD
    left = SpinnakerCamera(left_id, frame_rate=FPS, exposure_us=10000)
    right = SpinnakerCamera(right_id, frame_rate=FPS, exposure_us=10000)
    ENGINE = AcquisitionEngine(left, right, HUB, sink=lambda pair: None)  # recorder: Phase 2
    ACQ_THREAD = _threading.Thread(target=ENGINE.run, daemon=True)
    ACQ_THREAD.start()
```

- [ ] **Step 2: Start the engine from `/camera_settings` instead of opening cameras per request**

Replace the body of `camera_settings` (`app.py:404-411`) with:

```python
@cross_origin()
@app.route("/camera_settings", methods=['POST'])
def camera_settings():
    leftCam = request.json['leftCam']
    rightCam = request.json['rightCam']
    _start_engine(leftCam, rightCam)
    return jsonify({"message": "Engine started"})
```

- [ ] **Step 3: Serve both display feeds from the hub**

Replace `video_feed` (`app.py:104-237`) and `video_feed_fluorescent` (`app.py:240-313`) with thin hub readers:

```python
def _mjpeg_from_hub(stream):
    def gen():
        while True:
            frame = HUB.latest(stream)
            if frame is None:
                time.sleep(0.03)
                continue
            ok, buf = cv2.imencode('.jpg', frame.image)   # jpeg matches the declared mimetype
            if ok:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(0.03)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@cross_origin()
@app.route('/video_feed')
def video_feed():
    return _mjpeg_from_hub("left")

@cross_origin()
@app.route('/video_feed_fluorescent')
def video_feed_fluorescent():
    return _mjpeg_from_hub("right")
```

- [ ] **Step 4: Repoint `move_to_center` at the shared STAGE**

Replace `move_to_center` (`app.py:454-463`) with:

```python
@cross_origin()
@app.route("/move_to_center", methods=['POST'])
def move_to_center():
    lo_x, hi_x = STAGE.limits("x")
    lo_y, hi_y = STAGE.limits("y")
    STAGE.move_absolute("x", (lo_x + hi_x) / 2)
    STAGE.move_absolute("y", (lo_y + hi_y) / 2)
    return jsonify({"message": "Moved to center"})
```

- [ ] **Step 5: Manual end-to-end verification (on the rig)**

1. Start the app: `cd WormSpy/backend/code && python app.py`
2. Visit `localhost:5000`, pick the two cameras, Start Live Feed.
3. Confirm: both feeds display; refreshing the browser does NOT stop acquisition (the terminal shows the engine still running); "Center" moves the stage even right after a refresh; closing the browser tab does not kill the motor connection.

Expected: both feeds live, stage controllable independent of the browser, no `NameError` in the terminal.

- [ ] **Step 6: Run the unit suite once more**

Run: `cd WormSpy/backend/code && python -m pytest`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "refactor: own acquisition at startup; serve display from FrameHub (ADR 0001)"
```

---

## Self-Review

**Spec coverage (ADR 0001 decisions):**
1. *Single acquisition loop owning both cameras with shared frame_index* → Task 5 (engine) + Task 9 (wired at startup). ✓
2. *Camera as the clock (fixed rate + fixed exposure), frame IDs logged* → Task 8 (config) + `device_frame_id` carried through Tasks 2/5 (`AcquiredPair`). Note: writing IDs to the CSV is Phase 2 (ADR 0003); Phase 1 only *preserves* them. ✓ (within scope)
3. *Zaber connection opened once at startup, degrade gracefully* → Task 7 (`connect_stage` → NullStage) + Task 9 (constructed at startup). ✓
4. *Tracking in its own thread for all modes* → Task 6 (`Tracker`, compute injected so DLC runs off the acquisition loop). ✓

**Out-of-scope, deferred deliberately (called out in headers):** new recording format + CSV spine + codec (Phase 2), Hold Focus (Phase 3), `/status` + UI (Phase 4). The engine `sink` is a no-op placeholder so recording behaviour is unchanged until Phase 2.

**Placeholder scan:** No "TBD"/"add error handling"/"similar to" placeholders; every code step contains complete code. Manual-verification steps (7.5, 8.4, 9.5) are explicit hardware procedures, not code placeholders.

**Type consistency:** `Frame(image, device_frame_id, capture_ts)` and `AcquiredPair(frame_index, capture_ts, left, right)` used identically across Tasks 2/5/6/8. `Stage.move_relative/move_absolute` return `bool` (limit-hit) consistently across Tasks 4/7 and consumed in Task 6. `find_worm_cms(frame, factor, initial_coords)` and `simple_to_center(...)` signatures match between Task 6 definition and its tests. `connect_stage(xy_port, z_port, connector=None)` matches between Task 7 definition and tests.

**Known follow-ups for later phases (not gaps in this plan):** the `factor=2` downsample, the per-mode `compute` implementation (thresholding/fluorescent/DLC) wiring into `Tracker`, and `move_to_center` using true device midpoints all land when Phases 2–3 replace the remaining legacy code paths.
