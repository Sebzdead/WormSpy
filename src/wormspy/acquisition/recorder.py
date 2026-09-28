"""Recorder: writes one Recording from a stream of FramePairs on its own thread (ADR 0006/0007).

Acquisition must never wait on the disk. `submit()` is non-blocking: when the bounded queue is
full, the pair is refused. The writer thread later sees the jump in `frame_index` and writes
placeholder pages plus gap rows for the skipped indices, so page *i* of every channel file still
matches row *i* of `frames.csv`. Every drop, whether a camera drop (a missing frame in a pair) or
a writer-backlog drop, is reported as a `FramesDropped` event. A writer error is reported as
`RecordingFailed`. After that the thread only drains the queue until `stop()`.
"""

import json
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from wormspy.acquisition.pairing import FramePair
from wormspy.acquisition.writers import FramesCsvWriter, TrackingCsvWriter, open_channel_writer
from wormspy.control import events as E

BACKLOG = "writer backlog"
TelemetryFn = Callable[[FramePair], Mapping[str, object]]


@dataclass(frozen=True)
class ChannelConfig:
    name: str                 # file stem and CSV column prefix, e.g. "gcamp", "brightfield"
    role: str                 # "fluorescence" or "behaviour" (ADR 0007)
    shape: tuple[int, int]    # (height, width)


class Recorder:
    def __init__(self, recording_dir: Path, clock: ChannelConfig, follower: ChannelConfig,
                 fps: float, on_event: Callable[[object], None],
                 telemetry: TelemetryFn | None = None, queue_size: int = 64,
                 extra: dict | None = None) -> None:
        self.dir = Path(recording_dir)
        self._clock, self._follower = clock, follower
        self._fps = fps
        self._extra = dict(extra or {})     # e.g. the camera settings, for recording.json
        self._on_event = on_event
        self._telemetry = telemetry
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._last_offered: int | None = None
        self._markers: list[tuple[int, str]] = []      # (first frame_index it may land on, label)
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._failed: str | None = None
        self._expected: int | None = None
        self._rows = 0
        self._first: int | None = None
        self._dropped = {clock.name: 0, follower.name: 0, BACKLOG: 0}

    # --- producer side (acquisition / executor threads) -----------------------------------

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=False)   # a recording folder is never reused
        self._writers = {
            ch.name: open_channel_writer(ch.role, self.dir / ch.name, ch.shape, self._fps)
            for ch in (self._clock, self._follower)}
        self._csv = FramesCsvWriter(self.dir / "frames.csv",
                                    channels=(self._clock.name, self._follower.name))
        self._tracking: TrackingCsvWriter | None = TrackingCsvWriter(self.dir / "tracking.csv")
        self._tracking_lock = threading.Lock()
        self._thread.start()

    def submit(self, pair: FramePair) -> bool:
        """Queue a pair for writing. Never blocks; False means refused (the queue is full)."""
        with self._lock:
            self._last_offered = pair.frame_index
        try:
            self._queue.put_nowait(pair)
            return True
        except queue.Full:
            return False

    def mark_event(self, label: str) -> None:
        """Attach `label` to the first frame after the most recently submitted one."""
        with self._lock:
            first = -1 if self._last_offered is None else self._last_offered + 1
            self._markers.append((first, label))

    def log_tracking(self, **row) -> None:
        """Write one tracker result to tracking.csv (tracker thread). Ignored after stop()."""
        with self._tracking_lock:
            if self._tracking is not None:
                self._tracking.write_row(**row)

    def stop(self) -> None:
        """Write everything already queued, close the files and write recording.json."""
        self._queue.put(None)
        self._thread.join()
        for writer in self._writers.values():
            writer.close()
        self._csv.close()
        with self._tracking_lock:
            self._tracking.close()
            self._tracking = None
        self._write_summary()

    # --- writer thread ------------------------------------------------------------------

    def _run(self) -> None:
        while (pair := self._queue.get()) is not None:
            if self._failed is not None:
                continue                  # drain until stop() after a failure
            try:
                self._write(pair)
            except Exception as exc:      # noqa: BLE001 - any writer error ends the recording
                self._failed = f"{type(exc).__name__}: {exc}"
                self._on_event(E.RecordingFailed(reason=self._failed))

    def _write(self, pair: FramePair) -> None:
        if self._expected is not None and pair.frame_index > self._expected:
            skipped = pair.frame_index - self._expected
            for index in range(self._expected, pair.frame_index):
                self._write_row(FramePair(index, None, None, None), backlog=True)
            self._dropped[BACKLOG] += skipped
            self._on_event(E.FramesDropped(channel=BACKLOG, count=skipped))
        self._write_row(pair, backlog=False)

    def _write_row(self, pair: FramePair, backlog: bool) -> None:
        frames = {self._clock.name: pair.clock, self._follower.name: pair.follower}
        for name, frame in frames.items():
            self._writers[name].write(None if frame is None else frame.image)
            if frame is None and not backlog:
                self._dropped[name] += 1
                self._on_event(E.FramesDropped(channel=name, count=1))
        telemetry = dict(self._telemetry(pair)) if self._telemetry and not backlog else {}
        labels = self._take_markers(pair.frame_index)
        if labels:
            telemetry["event"] = ";".join(labels)
        present = pair.clock or pair.follower
        self._csv.write_row(pair.frame_index, None if present is None else present.capture_ts,
                            frames, pair.skew_ns, telemetry)
        if self._first is None:
            self._first = pair.frame_index
        self._expected = pair.frame_index + 1
        self._rows += 1

    def _take_markers(self, frame_index: int) -> list[str]:
        with self._lock:
            due = [label for first, label in self._markers if first <= frame_index]
            self._markers = [(f, label) for f, label in self._markers if f > frame_index]
        return due

    def _write_summary(self) -> None:
        summary = {
            "frames": self._rows,
            "first_frame_index": self._first,
            "last_frame_index": None if self._expected is None else self._expected - 1,
            "fps": self._fps,
            "dropped": self._dropped,
            "failed": self._failed,
            "channels": {
                ch.name: {"role": ch.role, "file": self._writers[ch.name].path.name}
                for ch in (self._clock, self._follower)},
            **self._extra,
        }
        (self.dir / "recording.json").write_text(json.dumps(summary, indent=2))
