"""Recording writers (ADR 0007).

- Fluorescence role: raw uint16 frames appended to one BigTIFF (opens in Fiji).
- Behaviour role: 8-bit frames as FFV1 lossless video (.mkv), via OpenCV's bundled FFmpeg.
- frames.csv: one row per frame_index, appended as frames arrive and flushed every
  `flush_every` rows, so a crash loses at most that many rows.

A dropped frame (`write(None)`) becomes a zero-filled placeholder, so page/frame *i* of every
channel file always matches row *i* of frames.csv. The drop itself is flagged in the CSV.
Writers refuse frames of the wrong dtype or shape rather than silently converting them.
"""

import csv
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np
import tifffile

from wormspy.core.frames import Frame


class _ChannelWriter:
    dtype: type = np.uint16
    suffix = ""

    def __init__(self, path: Path, shape: tuple[int, int]) -> None:
        self.path = Path(path)
        self.shape = tuple(shape)
        self.frames_written = 0

    def write(self, image: np.ndarray | None) -> None:
        if image is None:
            image = np.zeros(self.shape, self.dtype)
        elif image.dtype != self.dtype or image.shape != self.shape:
            raise ValueError(f"{self.path.name}: expected {np.dtype(self.dtype).name} "
                             f"{self.shape}, got {image.dtype} {image.shape}")
        self._write(image)
        self.frames_written += 1

    def _write(self, image: np.ndarray) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class TiffStackWriter(_ChannelWriter):
    """Raw uint16 BigTIFF stack, one page per frame_index."""
    dtype = np.uint16
    suffix = ".tif"

    def __init__(self, path: Path, shape: tuple[int, int]) -> None:
        super().__init__(path, shape)
        self._tif = tifffile.TiffWriter(self.path, bigtiff=True)

    def _write(self, image: np.ndarray) -> None:
        self._tif.write(image, contiguous=True, photometric="minisblack")

    def close(self) -> None:
        self._tif.close()


class Ffv1Writer(_ChannelWriter):
    """8-bit FFV1 lossless, intra-frame video in Matroska, one frame per frame_index."""
    dtype = np.uint8
    suffix = ".mkv"

    def __init__(self, path: Path, shape: tuple[int, int], fps: float) -> None:
        super().__init__(path, shape)
        height, width = self.shape
        self._video = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"FFV1"), fps,
                                      (width, height), isColor=False)
        if not self._video.isOpened():
            raise OSError(f"could not open FFV1 writer for {self.path}")

    def _write(self, image: np.ndarray) -> None:
        self._video.write(image)

    def close(self) -> None:
        self._video.release()


def open_channel_writer(role: str, path_stem: Path, shape: tuple[int, int],
                        fps: float) -> _ChannelWriter:
    """The channel role decides the format (ADR 0007); the suffix is added here."""
    path_stem = Path(path_stem)
    if role == "fluorescence":
        return TiffStackWriter(path_stem.with_suffix(TiffStackWriter.suffix), shape)
    if role == "behaviour":
        return Ffv1Writer(path_stem.with_suffix(Ffv1Writer.suffix), shape, fps)
    raise ValueError(f"unknown channel role {role!r}")


# Tracking results (tracked point, worm plate position, tracking state) are written to their own
# tracking.csv by the tracker, keyed by frame_index, because the tracker may lag the recorder.
CSV_TELEMETRY_COLUMNS = (
    "x_um", "y_um", "z_um",                    # stage, interpolated to frame time
    "z_moved", "focus_set_z_um",
    "event",
    "stim_state", "stim_param",                # reserved for closed-loop optogenetics
)


class FramesCsvWriter:
    def __init__(self, path: Path, channels: tuple[str, ...], flush_every: int = 10) -> None:
        self.path = Path(path)
        self._channels = tuple(channels)
        self._flush_every = flush_every
        self._since_flush = 0
        self._file = open(self.path, "w", newline="")  # noqa: SIM115 - closed in close()
        self._csv = csv.writer(self._file)
        header = ["frame_index", "host_time"]
        for ch in self._channels:
            header += [f"{ch}_frame_id", f"{ch}_timestamp_ns", f"{ch}_dropped"]
        header += ["pair_skew_ns", *CSV_TELEMETRY_COLUMNS]
        self._csv.writerow(header)
        self._file.flush()

    def write_row(self, frame_index: int, host_time: float | None, frames: Mapping[str, Frame | None],
                  skew_ns: int | None, telemetry: Mapping[str, object] | None = None) -> None:
        telemetry = dict(telemetry or {})
        unknown = set(telemetry) - set(CSV_TELEMETRY_COLUMNS)
        if unknown:
            raise KeyError(f"unknown telemetry column(s): {sorted(unknown)}")
        row: list[object] = [frame_index, host_time]
        for ch in self._channels:
            frame = frames[ch]
            if frame is None:
                row += ["", "", 1]
            else:
                row += [frame.device_frame_id, frame.device_timestamp_ns, 0]
        row.append("" if skew_ns is None else skew_ns)
        row += [_cell(telemetry.get(col)) for col in CSV_TELEMETRY_COLUMNS]
        self._csv.writerow(row)
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._file.flush()
            self._since_flush = 0

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    return value


TRACKING_COLUMNS = ("frame_index", "source_channel", "frame_id", "capture_ts", "estimator",
                    "target_px_x", "target_px_y", "worm_x_um", "worm_y_um", "accepted", "status")


class TrackingCsvWriter:
    """tracking.csv: one row per frame the tracker evaluated (it may skip frames when busy).

    Written from the tracker thread, independently of frames.csv, and joined to it on
    frame_index. `accepted` = 0 means the estimate was rejected as implausible, or nothing was
    found.
    """

    def __init__(self, path: Path, flush_every: int = 10) -> None:
        self.path = Path(path)
        self._flush_every = flush_every
        self._since_flush = 0
        self._file = open(self.path, "w", newline="")  # noqa: SIM115 - closed in close()
        self._csv = csv.writer(self._file)
        self._csv.writerow(TRACKING_COLUMNS)
        self._file.flush()

    def write_row(self, frame_index: int | None, source_channel: str, frame_id: int,
                  capture_ts: float, estimator: str, target_px: tuple[float, float] | None,
                  worm_um: tuple[float, float] | None, accepted: bool, status: str) -> None:
        px = target_px or ("", "")
        um = worm_um or ("", "")
        self._csv.writerow([_cell(frame_index), source_channel, frame_id, capture_ts, estimator,
                            px[0], px[1], um[0], um[1], int(accepted), status])
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._file.flush()
            self._since_flush = 0

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
