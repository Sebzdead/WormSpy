"""Read WormsPy recordings back for analysis (ADR 0007). Needs the `analysis` extra (pandas).

    from wormspy.reader import open_recording, open_session

    rec = open_recording("WormsPy_data/2026-09-28_N2-3/rec-001")
    rec.frames                      # one row per frame, indexed by frame_index
    rec["gcamp"][1234]              # the image at frame_index 1234 (uint16)
    rec["gcamp"].array()            # every calcium frame as one memory-mapped (N, H, W) array
    rec.valid                       # frames present in both channels (drops are zero-filled)
    rec.events                      # [(frame_index, label), ...]
    rec.plate_um("brightfield", (x_px, y_px), 1234)   # pixel -> position on the plate (µm)

Everything is indexed by **frame_index**, the acquisition's own frame counter, exactly as in
the CSVs. A recording that started mid-live starts at a frame_index above 0; `rec.index`
lists them. The file formats are described in docs/recording-format.md, so the data can also
be read without WormsPy.

Recordings cut short by a crash still open. They have no recording.json, and the channel files
may hold a frame or two fewer than frames.csv. The reader keeps only the frames every file has,
and lists what it noticed in `rec.warnings`.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import tifffile

try:
    import pandas as pd
except ImportError as exc:                 # pragma: no cover - depends on the environment
    raise ImportError("wormspy.reader needs pandas: install the `analysis` extra "
                      "(uv sync --extra analysis, or pip install pandas).") from exc

from wormspy.calibration.routine import RigCalibration

ROLE_SUFFIX = {"fluorescence": ".tif", "behaviour": ".mkv"}


class Channel:
    """One camera's frames. `channel[frame_index]` returns that frame's image."""

    def __init__(self, name: str, role: str, path: Path, first_index: int, length: int) -> None:
        self.name, self.role, self.path = name, role, Path(path)
        self._first, self._length = first_index, length
        self._tif: tifffile.TiffFile | None = None
        self._video: cv2.VideoCapture | None = None
        self._next_position = 0             # the video's read position, to avoid needless seeks

    def __len__(self) -> int:
        return self._length

    def __repr__(self) -> str:
        return f"Channel({self.name!r}, {self.role}, {self._length} frames, {self.path.name})"

    def __getitem__(self, frame_index: int) -> np.ndarray:
        position = self._position(frame_index)
        if self.role == "fluorescence":
            return self._tiff().pages[position].asarray()
        return self._read_video(position)

    def __iter__(self):
        """Every frame in order (fast: the video is decoded sequentially)."""
        for position in range(self._length):
            yield self[self._first + position]

    def array(self) -> np.ndarray:
        """All frames as one (N, H, W) array. Fluorescence is memory-mapped, so this is instant
        and uses no memory until read; behaviour video is decoded into memory."""
        if self.role == "fluorescence":
            try:
                return tifffile.memmap(self.path, mode="r")[: self._length]
            except ValueError:                  # not memory-mappable (e.g. an incomplete file)
                return tifffile.imread(self.path, key=range(self._length))
        return np.stack(list(self))

    def close(self) -> None:
        if self._tif is not None:
            self._tif.close()
            self._tif = None
        if self._video is not None:
            self._video.release()
            self._video = None

    def _position(self, frame_index: int) -> int:
        position = int(frame_index) - self._first
        if not 0 <= position < self._length:
            raise IndexError(f"{self.name}: frame_index {frame_index} is outside "
                             f"{self._first}..{self._first + self._length - 1}")
        return position

    def _tiff(self) -> tifffile.TiffFile:
        if self._tif is None:
            self._tif = tifffile.TiffFile(self.path)
        return self._tif

    def _read_video(self, position: int) -> np.ndarray:
        if self._video is None:
            self._video = cv2.VideoCapture(str(self.path))
            if not self._video.isOpened():
                raise OSError(f"cannot open {self.path} (OpenCV built without FFV1?)")
        if position != self._next_position:     # FFV1 is intra-only, so seeking is exact
            self._video.set(cv2.CAP_PROP_POS_FRAMES, position)
        ok, image = self._video.read()
        if not ok:
            raise OSError(f"{self.path.name}: could not decode frame {position}")
        self._next_position = position + 1
        return image[:, :, 0] if image.ndim == 3 else image


@dataclass
class Recording:
    path: Path
    frames: pd.DataFrame                    # frames.csv joined with tracking.csv, by frame_index
    tracking: pd.DataFrame                  # tracking.csv as written (the tracker's own rows)
    channels: dict[str, Channel]
    info: dict | None                       # recording.json; None if the recording was cut short
    session: dict | None                    # the parent folder's session.json, if present
    warnings: list[str] = field(default_factory=list)

    def __getitem__(self, name: str) -> Channel:
        try:
            return self.channels[name]
        except KeyError:
            raise KeyError(f"no channel {name!r}; this recording has "
                           f"{sorted(self.channels)}") from None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for channel in self.channels.values():
            channel.close()

    @property
    def index(self) -> pd.Index:
        return self.frames.index

    @property
    def complete(self) -> bool:
        """False if the recording was cut short (no recording.json was written)."""
        return self.info is not None

    @property
    def fps(self) -> float | None:
        return None if self.info is None else self.info.get("fps")

    @property
    def valid(self) -> pd.Series:
        """True where every channel has a real frame (dropped frames are zero-filled)."""
        dropped = [c for c in self.frames.columns if c.endswith("_dropped")]
        return ~self.frames[dropped].astype(bool).any(axis=1)

    @property
    def time_s(self) -> pd.Series:
        """Seconds since the first frame, from the frames' capture times (mid-exposure)."""
        return self.frames["host_time"] - self.frames["host_time"].dropna().iloc[0]

    @property
    def events(self) -> list[tuple[int, str]]:
        marked = self.frames["event"].dropna()
        return [(int(index), label) for index, cell in marked.items()
                for label in str(cell).split(";") if label]

    @property
    def camera(self) -> dict | None:
        """The camera preset name and settings this recording was made with."""
        return None if self.info is None else self.info.get("camera")

    @property
    def calibration(self) -> RigCalibration | None:
        data = (self.session or {}).get("config", {}).get("calibration")
        return None if data is None else RigCalibration.from_json(data)

    def plate_um(self, channel: str, px, frame_index: int) -> tuple[float, float]:
        """Where pixel `px` (x, y) of `channel` was on the plate at that frame, in stage µm.

        Uses the stage position interpolated to the frame's capture time and the session's
        calibration, including the offset between the cameras.
        """
        calibration = self.calibration
        if calibration is None or channel not in calibration.cameras:
            raise ValueError(f"the session has no calibration for {channel!r}")
        camera = calibration.cameras[channel]
        if channel != calibration.reference and camera.origin_px is None:
            raise ValueError(f"{channel} is not registered to {calibration.reference}; "
                             "plate positions from it would be off by the camera offset")
        row = self.frames.loc[frame_index]
        offset = np.linalg.solve(camera.matrix, np.asarray(px, float) - camera.origin)
        return float(row["x_um"] + offset[0]), float(row["y_um"] + offset[1])


def open_recording(path) -> Recording:
    """Open one `rec-NNN/` folder."""
    path = Path(path)
    if not (path / "frames.csv").exists():
        raise FileNotFoundError(f"{path} is not a WormsPy recording (no frames.csv)")
    warnings: list[str] = []
    info = _json(path / "recording.json")
    session = _json(path.parent / "session.json")
    if info is None:
        warnings.append("No recording.json: the recording was cut short. Using frames.csv.")

    frames = pd.read_csv(path / "frames.csv").set_index("frame_index")
    tracking = _tracking(path / "tracking.csv")
    roles = _roles(frames, info, session, path)
    lengths = {"frames.csv": len(frames)}
    files = {}
    for name, role in roles.items():
        file = path / (info["channels"][name]["file"] if info else name + ROLE_SUFFIX[role])
        files[name] = file
        # a cleanly closed recording wrote exactly one frame per row; a cut-short one is
        # counted (slow for long videos, but only needed after a crash)
        lengths[file.name] = info["frames"] if info else _count_frames(file, role)
    usable = min(lengths.values())
    if len(set(lengths.values())) > 1:
        warnings.append(f"Files hold different numbers of frames ({lengths}); "
                        f"using the first {usable}.")
    frames = frames.iloc[:usable]
    first = int(frames.index[0]) if usable else 0
    channels = {name: Channel(name, role, files[name], first, usable)
                for name, role in roles.items()}
    joined = frames.join(tracking.add_prefix("track_"), how="left")
    return Recording(path=path, frames=joined, tracking=tracking, channels=channels,
                     info=info, session=session, warnings=warnings)


@dataclass
class Session:
    path: Path
    info: dict                              # session.json: metadata, config, software version
    recordings: list[Path]

    @property
    def metadata(self) -> dict:
        return self.info.get("metadata", {})

    def open(self, name_or_number) -> Recording:
        """`session.open(1)` or `session.open("rec-001")`."""
        name = (f"rec-{name_or_number:03d}" if isinstance(name_or_number, int)
                else str(name_or_number))
        return open_recording(self.path / name)


def open_session(path) -> Session:
    """Open a `<date>_<animal-id>/` folder: its metadata and the recordings in it."""
    path = Path(path)
    info = _json(path / "session.json")
    if info is None:
        raise FileNotFoundError(f"{path} is not a WormsPy session (no session.json)")
    recordings = sorted(p for p in path.glob("rec-*") if (p / "frames.csv").exists())
    return Session(path=path, info=info, recordings=recordings)


def _json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def _tracking(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(index=pd.Index([], name="frame_index"))
    table = pd.read_csv(path)
    table = table.dropna(subset=["frame_index"])     # results for frames outside the recording
    table["frame_index"] = table["frame_index"].astype(int)
    # the tracker evaluates each frame at most once; keep the last row if that ever changes
    return table.drop_duplicates("frame_index", keep="last").set_index("frame_index")


def _roles(frames: pd.DataFrame, info: dict | None, session: dict | None,
           path: Path) -> dict[str, str]:
    """Channel name -> role, from recording.json, else session.json, else the files present."""
    if info is not None:
        return {name: ch["role"] for name, ch in info["channels"].items()}
    names = [c.removesuffix("_dropped") for c in frames.columns if c.endswith("_dropped")]
    known = (session or {}).get("config", {}).get("channels", {})
    roles = {}
    for name in names:
        if name in known:
            roles[name] = known[name]["role"]
        else:
            roles[name] = next(role for role, suffix in ROLE_SUFFIX.items()
                               if (path / (name + suffix)).exists())
    return roles


def _count_frames(file: Path, role: str) -> int:
    if not file.exists():
        return 0
    if role == "fluorescence":
        try:
            with tifffile.TiffFile(file) as tif:
                return len(tif.pages)
        except (tifffile.TiffFileError, ValueError):
            return 0
    video = cv2.VideoCapture(str(file))
    count = 0
    while video.grab():               # exact, unlike CAP_PROP_FRAME_COUNT for a cut-short file
        count += 1
    video.release()
    return count
