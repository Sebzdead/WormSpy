# Recording Integrity (ADR 0003) Implementation Plan — Phase 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore recording on the new acquisition architecture as a *frame-accurate, self-describing session*: a CSV spine keyed by `frame_index` (with capture timestamp, both camera frame IDs, stage x/y/z, and tracking state), brightfield written with an intra-frame codec, fluorescence written as 16-bit TIFF, and all disk I/O behind bounded queues so it never silently corrupts timing.

**Architecture:** Builds on Phase 1 (ADR 0001). The `AcquisitionEngine.sink` is set to `Recorder.consume`, which runs on the acquisition thread but does only cheap work — snapshot a thread-safe telemetry cache, format one CSV row, and hand frames to bounded `QueueWriter` threads. A `StatePoller` thread keeps the telemetry cache (stage x/y/z, tracking mode/flag, last limit-hit) fresh without ever blocking the acquisition loop on a serial read. Frame/CSV writers sit behind small interfaces so the orchestration is unit-tested with fakes; the real cv2/TIFF writers are verified on the rig.

**Tech Stack:** Python 3.8 (rig) / 3.12 (dev), NumPy, OpenCV (`cv2.VideoWriter`, `cv2.imwrite`), pytest. As in Phase 1, hardware/`cv2` imports live only inside adapter modules/lazy imports so the pure tests run without them.

**Scope:** Implements ADR 0003 and the "surface the limit-hit" half of Q8 (the clamp itself shipped in Phase 1's `clamp_native`). Does NOT implement Hold Focus (Phase 3 / ADR 0002) or the `/status` endpoint + UI (Phase 4 / Q6) — though the telemetry cache built here is the data source Phase 4 will expose. After this phase, recording works end-to-end again; autofocus and manual mode remain on their Phase-1-deferred state until Phase 3.

**Depends on:** branch `refactor/acquisition-core-adr0001` (Phase 1) — build Phase 2 on top of it. Phase 1's hardware adapters should ideally be rig-verified first; the pure tasks here do not require it.

---

## File Structure

All paths relative to `WormSpy/backend/code/`.

- Create: `core/telemetry.py` — `TelemetrySnapshot` dataclass; thread-safe `Telemetry` cache; `StatePoller` thread.
- Create: `core/recording.py` — pure CSV helpers (`CSV_HEADER`, `csv_row`); `QueueWriter` (bounded queue + worker thread); `FrameWriter` interface + `FakeFrameWriter`; `Recorder` orchestrator.
- Create: `core/recording_writers.py` — real writers: `Cv2VideoWriter` (intra-frame codec), `TiffSequenceWriter` (16-bit), lazy `cv2` import.
- Create: `tests/test_telemetry.py`, `tests/test_recording.py`.
- Modify: `app.py` — build `Telemetry` + `StatePoller` at startup, set `ENGINE` sink to `Recorder.consume`, rewrite `start_recording`/`stop_recording` routes to drive the single `Recorder`, feed tracking state + limit-hit into telemetry.

---

### Task 1: Telemetry cache and snapshot

**Files:**
- Create: `WormSpy/backend/code/core/telemetry.py`
- Test: `WormSpy/backend/code/tests/test_telemetry.py`

> Run tests in this sandbox with: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_telemetry.py -v` from `WormSpy/backend/code`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_telemetry.py`:

```python
import threading
from core.telemetry import Telemetry, TelemetrySnapshot


def test_default_snapshot_is_zeroed_and_not_tracking():
    snap = Telemetry().snapshot()
    assert snap.x == 0.0 and snap.y == 0.0 and snap.z == 0.0
    assert snap.tracking_mode == 0
    assert snap.is_tracking is False
    assert snap.limit_hit is False


def test_set_position_then_snapshot_reflects_it():
    t = Telemetry()
    t.set_position(10.0, 20.0, 30.0)
    snap = t.snapshot()
    assert (snap.x, snap.y, snap.z) == (10.0, 20.0, 30.0)


def test_set_tracking_state_is_reflected():
    t = Telemetry()
    t.set_tracking(mode=2, is_tracking=True)
    snap = t.snapshot()
    assert snap.tracking_mode == 2
    assert snap.is_tracking is True


def test_limit_hit_is_recorded():
    t = Telemetry()
    t.set_limit_hit(True)
    assert t.snapshot().limit_hit is True


def test_snapshot_is_immutable_copy():
    t = Telemetry()
    t.set_position(1.0, 2.0, 3.0)
    snap = t.snapshot()
    t.set_position(9.0, 9.0, 9.0)
    # the earlier snapshot must not change
    assert (snap.x, snap.y, snap.z) == (1.0, 2.0, 3.0)


def test_concurrent_updates_do_not_raise():
    t = Telemetry()
    def writer(base):
        for i in range(1000):
            t.set_position(base + i, base + i, base + i)
    threads = [threading.Thread(target=writer, args=(b,)) for b in (0, 10000)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert t.snapshot() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_telemetry.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.telemetry'`

- [ ] **Step 3: Write minimal implementation**

Create `core/telemetry.py`:

```python
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class TelemetrySnapshot:
    """An immutable, point-in-time view of stage + tracking state for one CSV row."""
    x: float
    y: float
    z: float
    tracking_mode: int
    is_tracking: bool
    limit_hit: bool


class Telemetry:
    """Thread-safe mutable cache of the latest stage position and tracking state.

    Written by the StatePoller (position) and by control routes (tracking/limit). Read
    (non-blocking) by the Recorder at acquisition-tick time and, later, by the /status endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._tracking_mode = 0
        self._is_tracking = False
        self._limit_hit = False

    def set_position(self, x: float, y: float, z: float) -> None:
        with self._lock:
            self._x, self._y, self._z = x, y, z

    def set_tracking(self, mode: int, is_tracking: bool) -> None:
        with self._lock:
            self._tracking_mode = mode
            self._is_tracking = is_tracking

    def set_limit_hit(self, hit: bool) -> None:
        with self._lock:
            self._limit_hit = hit

    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return TelemetrySnapshot(
                x=self._x, y=self._y, z=self._z,
                tracking_mode=self._tracking_mode,
                is_tracking=self._is_tracking,
                limit_hit=self._limit_hit,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_telemetry.py`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/telemetry.py WormSpy/backend/code/tests/test_telemetry.py
git commit -m "feat: add thread-safe Telemetry cache and snapshot"
```
End commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 2: StatePoller thread

**Files:**
- Modify: `WormSpy/backend/code/core/telemetry.py`
- Test: `WormSpy/backend/code/tests/test_telemetry.py` (extend)
- Reference: Phase 1 `core/stage.py` (`Stage`, `FakeStage`)

> The poller reads stage position into the telemetry cache at a fixed rate, OFF the acquisition thread, so the acquisition loop never blocks on a serial read.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_telemetry.py`:

```python
from core.stage import FakeStage
from core.telemetry import StatePoller


def test_poller_once_copies_stage_position_into_telemetry():
    t = Telemetry()
    stage = FakeStage(limits={"x": (0, 1e9), "y": (0, 1e9), "z": (0, 1e9)},
                      positions={"x": 5.0, "y": 6.0, "z": 7.0})
    poller = StatePoller(stage, t)
    poller.poll_once()
    snap = t.snapshot()
    assert (snap.x, snap.y, snap.z) == (5.0, 6.0, 7.0)


def test_poller_once_is_noop_when_stage_disconnected():
    from core.stage import NullStage
    t = Telemetry()
    t.set_position(1.0, 2.0, 3.0)
    poller = StatePoller(NullStage(), t)
    poller.poll_once()
    # NullStage is not connected -> poller must not overwrite with zeros
    snap = t.snapshot()
    assert (snap.x, snap.y, snap.z) == (1.0, 2.0, 3.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_telemetry.py -k poller`
Expected: FAIL — `ImportError: cannot import name 'StatePoller'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/telemetry.py`:

```python
import time

from core.stage import Stage


class StatePoller:
    """Polls the stage position into a Telemetry cache at a fixed rate, in its own thread.

    Keeps position fresh for the recorder/status without ever blocking the acquisition loop."""

    def __init__(self, stage: Stage, telemetry: Telemetry, interval_s: float = 0.05) -> None:
        self._stage = stage
        self._telemetry = telemetry
        self._interval_s = interval_s
        self._stop = threading.Event()

    def poll_once(self) -> None:
        if not self._stage.is_connected():
            return
        self._telemetry.set_position(
            self._stage.get_position("x"),
            self._stage.get_position("y"),
            self._stage.get_position("z"),
        )

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            time.sleep(self._interval_s)

    def stop(self) -> None:
        self._stop.set()
```

(Put the `import time` and `from core.stage import Stage` near the top of the file with the other imports rather than mid-file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_telemetry.py`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/telemetry.py WormSpy/backend/code/tests/test_telemetry.py
git commit -m "feat: add StatePoller to keep telemetry position fresh off the acquisition thread"
```
End commit message with the Co-Authored-By trailer.

---

### Task 3: CSV spine helpers

**Files:**
- Create: `WormSpy/backend/code/core/recording.py`
- Test: `WormSpy/backend/code/tests/test_recording.py`

> Pure formatting for the per-frame CSV spine (ADR 0003). One row per acquisition tick, keyed by `frame_index`; dropped frames are detectable as `frame_index` gaps and as camera-frame-id gaps.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recording.py`:

```python
from core.recording import CSV_HEADER, csv_row
from core.telemetry import TelemetrySnapshot


def _snap(x=1.0, y=2.0, z=3.0, mode=0, tracking=True, limit=False):
    return TelemetrySnapshot(x=x, y=y, z=z, tracking_mode=mode,
                             is_tracking=tracking, limit_hit=limit)


def test_header_lists_the_spine_columns_in_order():
    assert CSV_HEADER == ("frame_index,capture_timestamp,left_frame_id,right_frame_id,"
                          "x_pos,y_pos,z_pos,tracking_mode,is_tracking")


def test_csv_row_formats_all_fields():
    row = csv_row(frame_index=5, capture_ts=12.5, left_id=5, right_id=5, snap=_snap())
    assert row == "5,12.500000,5,5,1.000000,2.000000,3.000000,0,True"


def test_csv_row_uses_minus_one_for_missing_frame_ids():
    row = csv_row(frame_index=7, capture_ts=0.0, left_id=-1, right_id=3, snap=_snap())
    assert row.startswith("7,0.000000,-1,3,")


def test_csv_row_reflects_tracking_state_false():
    row = csv_row(frame_index=1, capture_ts=0.0, left_id=0, right_id=0,
                  snap=_snap(tracking=False, mode=2))
    assert row.endswith(",2,False")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.recording'`

- [ ] **Step 3: Write minimal implementation**

Create `core/recording.py`:

```python
from core.telemetry import TelemetrySnapshot

CSV_HEADER = ("frame_index,capture_timestamp,left_frame_id,right_frame_id,"
              "x_pos,y_pos,z_pos,tracking_mode,is_tracking")


def csv_row(frame_index: int, capture_ts: float, left_id: int, right_id: int,
            snap: TelemetrySnapshot) -> str:
    """Format one spine row. Keep field order identical to CSV_HEADER."""
    return (f"{frame_index},{capture_ts:.6f},{left_id},{right_id},"
            f"{snap.x:.6f},{snap.y:.6f},{snap.z:.6f},"
            f"{snap.tracking_mode},{snap.is_tracking}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/recording.py WormSpy/backend/code/tests/test_recording.py
git commit -m "feat: add CSV spine header and row formatting"
```
End commit message with the Co-Authored-By trailer.

---

### Task 4: QueueWriter (bounded, backpressure)

**Files:**
- Modify: `WormSpy/backend/code/core/recording.py`
- Test: `WormSpy/backend/code/tests/test_recording.py` (extend)

> All disk I/O goes through this so a slow disk never silently corrupts timing. Two policies: `block` (recording — back-pressures to camera-level drops, which appear as logged frame-id gaps rather than silent loss) and `drop` (non-critical — counts drops).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recording.py`:

```python
import threading
from core.recording import QueueWriter


def test_queue_writer_writes_all_items_in_order_then_closes():
    written = []
    w = QueueWriter(write_fn=written.append, maxsize=10, on_full="block")
    w.start()
    for i in range(5):
        w.put(i)
    w.close()
    assert written == [0, 1, 2, 3, 4]


def test_queue_writer_drop_policy_counts_drops_when_full():
    started = threading.Event()
    release = threading.Event()
    def slow_write(item):
        started.set()
        release.wait(timeout=2)
    w = QueueWriter(write_fn=slow_write, maxsize=1, on_full="drop")
    w.start()
    w.put("a")               # taken by worker, which then blocks in slow_write
    started.wait(timeout=2)
    w.put("b")               # fills the queue (size 1)
    w.put("c")               # queue full -> dropped
    w.put("d")               # dropped
    assert w.dropped >= 1
    release.set()
    w.close()


def test_queue_writer_block_policy_does_not_drop():
    written = []
    w = QueueWriter(write_fn=written.append, maxsize=1, on_full="block")
    w.start()
    for i in range(50):
        w.put(i)            # must block rather than drop
    w.close()
    assert written == list(range(50))
    assert w.dropped == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py -k queue_writer`
Expected: FAIL — `ImportError: cannot import name 'QueueWriter'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/recording.py`:

```python
import queue
import threading
from typing import Callable


class QueueWriter:
    """Bounded queue + single worker thread that calls write_fn(item) off the caller's thread.

    on_full="block": put() blocks until space (use for the recording — back-pressure surfaces
    as camera-level frame-id gaps, never silent loss). on_full="drop": put() drops and counts."""

    _SENTINEL = object()

    def __init__(self, write_fn: Callable[[object], None], maxsize: int = 256,
                 on_full: str = "block") -> None:
        if on_full not in ("block", "drop"):
            raise ValueError("on_full must be 'block' or 'drop'")
        self._write = write_fn
        self._on_full = on_full
        self._q: "queue.Queue" = queue.Queue(maxsize)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.dropped = 0

    def start(self) -> None:
        self._thread.start()

    def put(self, item: object) -> None:
        if self._on_full == "drop":
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.dropped += 1
        else:
            self._q.put(item)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is self._SENTINEL:
                break
            self._write(item)

    def close(self) -> None:
        self._q.put(self._SENTINEL)
        self._thread.join()
```

(Move the `import queue`, `import threading`, and `from typing import Callable` to the top of the file with the existing imports.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/recording.py WormSpy/backend/code/tests/test_recording.py
git commit -m "feat: add bounded QueueWriter with block/drop backpressure policies"
```
End commit message with the Co-Authored-By trailer.

---

### Task 5: FrameWriter interface + FakeFrameWriter, and the Recorder orchestrator

**Files:**
- Modify: `WormSpy/backend/code/core/recording.py`
- Test: `WormSpy/backend/code/tests/test_recording.py` (extend)
- Reference: Phase 1 `core/frames.py` (`AcquiredPair`, `Frame`), `core/telemetry.py`

> The Recorder is the engine `sink`. It runs on the acquisition thread but does only cheap work:
> snapshot telemetry, format a CSV row, and `put` frames/rows onto QueueWriters. When not recording
> it ignores pairs. One Recorder = one recording state machine for both cameras (fixes the legacy
> two-state-machine bug).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recording.py`:

```python
import numpy as np
from core.frames import AcquiredPair, Frame
from core.telemetry import Telemetry
from core.recording import Recorder, FrameWriter, FakeFrameWriter


def _pair(idx, left_val=1, right_val=2, left_id=None, right_id=None):
    left = Frame(np.full((2, 2), left_val, np.uint8),
                 left_id if left_id is not None else idx, 0.0)
    right = Frame(np.full((2, 2), right_val, np.uint8),
                  right_id if right_id is not None else idx, 0.0)
    return AcquiredPair(frame_index=idx, capture_ts=float(idx), left=left, right=right)


def _recorder_with_fakes():
    telem = Telemetry()
    left_w = FakeFrameWriter()
    right_w = FakeFrameWriter()
    rows = []
    rec = Recorder(telemetry=telem,
                   left_writer_factory=lambda cfg: left_w,
                   right_writer_factory=lambda cfg: right_w,
                   csv_sink=rows.append)
    return rec, telem, left_w, right_w, rows


def test_recorder_ignores_pairs_when_not_recording():
    rec, _, left_w, right_w, rows = _recorder_with_fakes()
    rec.consume(_pair(0))
    assert left_w.images == [] and right_w.images == [] and rows == []


def test_recorder_writes_header_then_rows_and_frames_when_recording():
    rec, telem, left_w, right_w, rows = _recorder_with_fakes()
    telem.set_position(10.0, 20.0, 30.0)
    rec.start_recording(config={"name": "proj"})
    rec.consume(_pair(0))
    rec.consume(_pair(1))
    rec.stop_recording()
    assert rows[0].startswith("frame_index,")          # header first
    assert rows[1].startswith("0,0.000000,0,0,10.000000,20.000000,30.000000,")
    assert rows[2].startswith("1,1.000000,1,1,")
    assert len(left_w.images) == 2 and len(right_w.images) == 2
    assert left_w.closed and right_w.closed             # writers closed on stop


def test_recorder_snapshots_position_at_consume_time():
    rec, telem, _, _, rows = _recorder_with_fakes()
    rec.start_recording(config={"name": "p"})
    telem.set_position(1.0, 1.0, 1.0)
    rec.consume(_pair(0))
    telem.set_position(99.0, 99.0, 99.0)
    rec.consume(_pair(1))
    rec.stop_recording()
    assert ",1.000000,1.000000,1.000000," in rows[1]    # row for idx 0
    assert ",99.000000,99.000000,99.000000," in rows[2] # row for idx 1


def test_recorder_handles_missing_side_with_minus_one_id():
    rec, _, left_w, right_w, rows = _recorder_with_fakes()
    rec.start_recording(config={"name": "p"})
    pair = _pair(0)
    pair = AcquiredPair(frame_index=0, capture_ts=0.0, left=None, right=pair.right)
    rec.consume(pair)
    rec.stop_recording()
    assert rows[1].startswith("0,0.000000,-1,0,")       # left missing -> -1
    assert len(left_w.images) == 0 and len(right_w.images) == 1


def test_recorder_double_start_is_ignored():
    rec, _, _, _, rows = _recorder_with_fakes()
    rec.start_recording(config={"name": "p"})
    rec.start_recording(config={"name": "p"})   # no second header
    rec.consume(_pair(0))
    rec.stop_recording()
    assert rows.count(rows[0]) == 1             # exactly one header line


def test_fake_frame_writer_satisfies_interface():
    assert issubclass(FakeFrameWriter, FrameWriter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py -k recorder`
Expected: FAIL — `ImportError: cannot import name 'Recorder'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/recording.py`:

```python
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

import numpy as np

from core.frames import AcquiredPair
from core.telemetry import Telemetry


class FrameWriter(ABC):
    """Sink for one camera's frames. Real impls wrap cv2; FakeFrameWriter is the test double."""

    @abstractmethod
    def write(self, image: np.ndarray) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


class FakeFrameWriter(FrameWriter):
    def __init__(self) -> None:
        self.images: List[np.ndarray] = []
        self.closed = False

    def write(self, image: np.ndarray) -> None:
        self.images.append(image)

    def close(self) -> None:
        self.closed = True


WriterFactory = Callable[[dict], FrameWriter]


class Recorder:
    """Engine sink. Cheap, runs on the acquisition thread; ignores pairs unless recording.

    `csv_sink` receives header + row strings (a QueueWriter.put in production, a list in tests)."""

    def __init__(self, telemetry: Telemetry,
                 left_writer_factory: WriterFactory,
                 right_writer_factory: WriterFactory,
                 csv_sink: Callable[[str], None]) -> None:
        self._telemetry = telemetry
        self._left_factory = left_writer_factory
        self._right_factory = right_writer_factory
        self._csv_sink = csv_sink
        self._recording = False
        self._left: Optional[FrameWriter] = None
        self._right: Optional[FrameWriter] = None

    def start_recording(self, config: dict) -> None:
        if self._recording:
            return
        self._left = self._left_factory(config)
        self._right = self._right_factory(config)
        self._csv_sink(CSV_HEADER)
        self._recording = True

    def consume(self, pair: AcquiredPair) -> None:
        if not self._recording:
            return
        snap = self._telemetry.snapshot()
        left_id = pair.left.device_frame_id if pair.left is not None else -1
        right_id = pair.right.device_frame_id if pair.right is not None else -1
        self._csv_sink(csv_row(pair.frame_index, pair.capture_ts, left_id, right_id, snap))
        if pair.left is not None:
            self._left.write(pair.left.image)
        if pair.right is not None:
            self._right.write(pair.right.image)

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._left is not None:
            self._left.close()
        if self._right is not None:
            self._right.close()
        self._left = None
        self._right = None

    @property
    def is_recording(self) -> bool:
        return self._recording
```

(Consolidate the new imports with those already at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py`
Expected: PASS (14 passed)

- [ ] **Step 5: Run the whole suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS (Phase 1's 38 + Phase 2 new tests)

- [ ] **Step 6: Commit**

```bash
git add WormSpy/backend/code/core/recording.py WormSpy/backend/code/tests/test_recording.py
git commit -m "feat: add Recorder orchestrator with single recording state machine"
```
End commit message with the Co-Authored-By trailer.

---

### Task 6: Real frame writers (cv2 video + 16-bit TIFF)

**Files:**
- Create: `WormSpy/backend/code/core/recording_writers.py`
- Reference: legacy `app.py` original recording (`cv2.VideoWriter_fourcc(*'XVID')` → replace with intra-frame codec; 16-bit TIFF via `cv2.imwrite`)

> Hardware/codec-dependent; verified manually on the rig (Step 4). `cv2` is imported lazily so the
> module imports without OpenCV in the sandbox. Brightfield uses an intra-frame codec (MJPG default,
> FFV1 lossless option) — NOT XVID. Fluorescence frames are written as individual 16-bit TIFFs to
> preserve dynamic range.

- [ ] **Step 1: Write the implementation**

Create `core/recording_writers.py`:

```python
"""Real frame writers. cv2 is imported lazily so this module imports without OpenCV present."""

import pathlib
from typing import Tuple

import numpy as np

from core.recording import FrameWriter

# Intra-frame codecs only (each frame stands alone -> frame-accurate behaviour scoring).
CODEC_FOURCC = {
    "mjpg": "MJPG",     # intra-frame, compressed
    "ffv1": "FFV1",     # intra-frame, lossless
}


class Cv2VideoWriter(FrameWriter):
    """Writes grayscale frames to a single video file with an intra-frame codec."""

    def __init__(self, path: str, fps: float, size: Tuple[int, int], codec: str = "mjpg") -> None:
        import cv2  # lazy
        fourcc = cv2.VideoWriter_fourcc(*CODEC_FOURCC[codec])
        self._writer = cv2.VideoWriter(path, fourcc, fps, size, isColor=False)

    def write(self, image: np.ndarray) -> None:
        self._writer.write(image)

    def close(self) -> None:
        self._writer.release()


class TiffSequenceWriter(FrameWriter):
    """Writes each frame as an individual TIFF (16-bit preserved) into a folder."""

    def __init__(self, folder: str) -> None:
        self._folder = pathlib.Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def write(self, image: np.ndarray) -> None:
        import cv2  # lazy
        cv2.imwrite(str(self._folder / f"frame_{self._n:06d}.tiff"), image)
        self._n += 1

    def close(self) -> None:
        pass
```

- [ ] **Step 2: Parse check (no cv2 needed)**

Run: `python -c "import ast; ast.parse(open('core/recording_writers.py').read()); print('parse ok')"`
Expected: `parse ok`

- [ ] **Step 3: Confirm the unit suite is unaffected**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`
Expected: PASS (no new tests; nothing regressed)

- [ ] **Step 4: Manual hardware/codec verification (on the rig)**

After Task 7 wiring, record a short session and confirm: the brightfield video opens and scrubs
frame-by-frame in a standard player; the fluorescence TIFFs are 16-bit; the CSV has one row per
frame with a header. (Pure-Python check of the TIFF bit depth, on any machine with the files:
`python -c "import cv2; print(cv2.imread('frame_000000.tiff', cv2.IMREAD_UNCHANGED).dtype)"` →
expect `uint16` when the camera supplied 16-bit frames.)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/recording_writers.py
git commit -m "feat: add intra-frame video and 16-bit TIFF frame writers"
```
End commit message with the Co-Authored-By trailer.

---

### Task 7: Wire the Recorder + telemetry into app.py

**Files:**
- Modify: `WormSpy/backend/code/app.py`
  - Startup block (the Phase 1 wiring after `settings = {...}`): add `Telemetry`, `StatePoller`, `Recorder`, and writer factories; set the engine `sink` to `RECORDER.consume`.
  - `start_recording` / `stop_recording` routes: drive the single `Recorder`.
  - `toggle_tracking`: also push mode/flag into telemetry.

> Hardware-wired; validated by recording on the rig (Step 4). `cv2` import already exists at the top
> of app.py. This restores recording on the new architecture. Autofocus/manual remain Phase-3 work.

- [ ] **Step 1: Extend the startup wiring**

In `app.py`, in the Phase 1 startup block (right after `STAGE = connect_stage(...)`), add:

```python
from core.telemetry import Telemetry, StatePoller
from core.recording import Recorder, QueueWriter
from core.recording_writers import Cv2VideoWriter, TiffSequenceWriter
import pathlib as _pathlib

TELEMETRY = Telemetry()
_poller = StatePoller(STAGE, TELEMETRY)
_threading.Thread(target=_poller.run, daemon=True).start()

# CSV rows go through a blocking QueueWriter so disk lag never corrupts timing.
_csv_writer = {"w": None}

def _open_csv_writer(config):
    folder = _pathlib.Path(config["filepath"]) / config["folder_name"]
    folder.mkdir(parents=True, exist_ok=True)
    fh = open(folder / (config["name"] + ".csv"), "w")
    w = QueueWriter(write_fn=lambda line: fh.write(line + "\n"), maxsize=4096, on_full="block")
    w.start()
    _csv_writer["w"] = (w, fh)
    return w

def _left_writer_factory(config):
    folder = _pathlib.Path(config["filepath"]) / config["folder_name"]
    return Cv2VideoWriter(str(folder / (config["folder_name"] + "_L.avi")),
                          fps=FPS, size=config["left_size"], codec="mjpg")

def _right_writer_factory(config):
    folder = _pathlib.Path(config["filepath"]) / config["folder_name"]
    return TiffSequenceWriter(str(folder / "rightcam_tiffs"))

RECORDER = Recorder(telemetry=TELEMETRY,
                    left_writer_factory=_left_writer_factory,
                    right_writer_factory=_right_writer_factory,
                    csv_sink=lambda line: _csv_writer["w"][0].put(line))
```

And change the engine construction in `_start_engine` so the sink is the recorder:

```python
    ENGINE = AcquisitionEngine(left, right, HUB, sink=RECORDER.consume)
```

- [ ] **Step 2: Rewrite the recording routes to drive the single Recorder**

Replace the existing `start_record` and `stop_record` route functions with:

```python
@cross_origin()
@app.route("/start_recording", methods=['POST'])
def start_record():
    dt = datetime.now(tz=timeZone).strftime("%d-%m-%Y_%H-%M")
    name = request.json.get("filename", settings["filename"])
    filepath = request.json.get("filepath", settings["filepath"])
    folder_name = f"{name}_{dt}"
    left = HUB.latest("left")
    left_size = (int(left.image.shape[1]), int(left.image.shape[0])) if left else (0, 0)
    config = {"name": name, "filepath": filepath,
              "folder_name": folder_name, "left_size": left_size}
    _open_csv_writer(config)
    RECORDER.start_recording(config)
    return jsonify({"message": "Recording started", "folder": folder_name})


@cross_origin()
@app.route("/stop_recording", methods=['POST'])
def stop_record():
    RECORDER.stop_recording()
    if _csv_writer["w"] is not None:
        w, fh = _csv_writer["w"]
        w.close()
        fh.close()
        _csv_writer["w"] = None
    return jsonify({"message": "Recording stopped"})
```

- [ ] **Step 3: Push tracking state into telemetry in `toggle_tracking`**

In the existing `toggle_tracking` route, after setting `is_tracking` and `track_algorithm`, add:

```python
    TELEMETRY.set_tracking(mode=track_algorithm, is_tracking=is_tracking)
```

- [ ] **Step 4: Verify + manual rig check**

Parse: `python -c "import ast; ast.parse(open('WormSpy/backend/code/app.py').read()); print('parse ok')"` → `parse ok`
Suite: `cd WormSpy/backend/code && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` → all pass.
On the rig: start live feed, Start Recording, move the stage / toggle tracking, Stop Recording. Confirm a session folder with `<name>.csv` (header + one row per frame, x/y/z populated, tracking_mode/is_tracking correct), an `_L.avi` that scrubs frame-by-frame, and a `rightcam_tiffs/` folder of 16-bit TIFFs.

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "refactor: drive recording through Recorder + telemetry (ADR 0003)"
```
End commit message with the Co-Authored-By trailer.

---

## Self-Review

**Spec coverage (ADR 0003 + Q8 surfacing):**
- *CSV is the per-frame spine keyed by frame_index, with capture ts, both frame IDs, x/y/z, tracking mode/flag* → Tasks 3 (format) + 5 (Recorder emits header+rows) + 7 (real CSV file). ✓
- *Dropped frames detectable as gaps* → frame_index is contiguous (Phase 1 engine) and camera frame IDs are logged per row; writer back-pressure uses `block` so loss surfaces as camera-level frame-id gaps, not silent writer drops (Task 4). ✓
- *Brightfield intra-frame codec instead of XVID; lossless option* → Task 6 (`CODEC_FOURCC` mjpg/ffv1, default mjpg). ✓
- *Fluorescence uncompressed 16-bit TIFF* → Task 6 (`TiffSequenceWriter`, `IMREAD_UNCHANGED` stays 16-bit). ✓
- *Bounded queues / backpressure* → Task 4 (`QueueWriter`). ✓
- *Single recording state machine (fix legacy two-machine bug)* → Task 5 (`Recorder.start/stop`, double-start ignored) + Task 7 (one route pair drives it). ✓
- *Per-frame position without jittering the sacred acquisition rate* → Tasks 1–2 (Telemetry + StatePoller off-thread) + Recorder snapshots the cache at consume time (Task 5). ✓
- *Q8 limit-hit surfaced* → `Telemetry.set_limit_hit` (Task 1) is the surface; the Tracker's `on_move(hit)` (Phase 1) and joystick path feed it. (Wiring `on_move`→telemetry is a one-line hook added when the Tracker is wired into app.py — note for the implementer if tracking wiring lands here; otherwise it carries to Phase 3.)

**Placeholder scan:** No "TBD"/"add error handling"/"similar to" placeholders; pure-logic steps carry complete code; IO-adapter and app.py steps carry complete code + explicit manual verification.

**Type consistency:** `TelemetrySnapshot(x,y,z,tracking_mode,is_tracking,limit_hit)` used identically in Tasks 1/3/5. `csv_row(frame_index, capture_ts, left_id, right_id, snap)` signature matches between Task 3 definition, its tests, and the Recorder call in Task 5. `FrameWriter.write(image)/close()` consistent across Tasks 5/6. `Recorder(telemetry, left_writer_factory, right_writer_factory, csv_sink)` matches between Task 5 definition, its tests, and Task 7 construction. `QueueWriter(write_fn, maxsize, on_full)` consistent across Tasks 4/7.

**Known follow-ups (not gaps):** wiring the Tracker's `on_move`→`set_limit_hit` and the real per-mode tracking `compute` are completed when tracking is restored (here or Phase 3); the `/status` endpoint that exposes `Telemetry` is Phase 4; FFV1 selection is exposed in the UI in Phase 4 (codec arg already supported).
