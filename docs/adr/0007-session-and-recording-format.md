# 7. Session and recording format

Date: 2026-09-27

## Status

Accepted. Supersedes ADR 0003, and keeps its "per-frame CSV is the spine" principle.

## Context

v1 wrote brightfield as XVID AVI and, by default, fluorescence as XVID AVI too. Each frame was
min–max normalised on its own, which destroys quantitative calcium data. It also wrote one TIFF
file per frame, named by wall-clock time, and a position CSV that existed only in memory until
Stop. The paper's analyses join activity, behaviour and stage position frame by frame. The ASH
experiment used the second camera as a DsRed fluorescence channel for ratiometry.

## Decision

**Structure**

```
<root>/<date>_<animal-id>/                 # Session: one animal
  session.json                             # metadata form + notes, full config, preset,
                                           # calibration snapshot, software version + git hash
  rec-001/                                 # Recording: one Start→Stop span
    <channel>.tif      (fluorescence role) # raw uint16 BigTIFF, appended per frame
    <channel>.mkv      (behaviour role)    # FFV1 lossless, 8-bit, intra-frame
    frames.csv                             # per-frame CSV, appended per frame
    recording.json                         # start/stop times, frame count, drop summary
  rec-002/ …
```

**Channel roles decide the format.** A channel in the *fluorescence* role is written as raw
16-bit BigTIFF (tifffile, opens in Fiji). A channel in the *behaviour* role is written as FFV1
8-bit video. It must be intra-frame, so every frame is exact. A ratiometric session has two
fluorescence channels, both raw. Page *i* of every file corresponds to row *i* of `frames.csv`.

**`frames.csv` is appended as frames arrive and flushed regularly.** A crash loses at most the
last few rows. Columns:

- **Keys and timing:** `frame_index`, `host_time`, then for each channel `<channel>_frame_id`,
  `<channel>_timestamp_ns` and `<channel>_dropped`, where `<channel>` is the channel's name, the
  same as its file stem. Then `pair_skew_ns`: the follower-minus-clock timestamp offset relative to
  the first pair. A large value means the pairing is suspect.
- **Position:** stage `x_um`, `y_um`, `z_um`, interpolated to frame time from a fixed-rate stage
  poller; `worm_x_um`, `worm_y_um` (world/plate position, using calibration); `target_px_x`,
  `target_px_y`.
- **Tracking:** `tracking_state` (off / tracking / lost / override / limit), `estimator`,
  `source_channel`.
- **Focus:** `z_moved`, `focus_set_z`.
- **Events:** `event` (operator marker label).
- **Reserved, empty in v1:** `stim_state`, `stim_param`, for closed-loop optogenetics.

A drop produces a gap row instead of a missing row.

**Operator metadata** is a light, configurable form: operator, animal/strain ID, condition and
free notes, pre-filled from the previous session.

**Analysis in v1:** a small reader library opens a recording as channel arrays plus the frame
table, and the format is documented. `tifstacker.py` becomes unnecessary. `Segmentation.py` is
ported later as an example, fixing its dead jump-filter bug on the way.

## Consequences

- Every file is self-describing and frame-accurate. The hand-typed `FPS` constant and wall-clock
  file names go away.
- FFV1 is written through OpenCV's bundled FFmpeg (`cv2.VideoWriter`, fourcc `FFV1`, `.mkv`).
  Checked on 2026-09-27: lossless round trip, exact frame count, frame-exact seeking. PyAV is
  deliberately **not** used. It bundles a second FFmpeg, and loading both in one process
  triggers duplicate-library warnings on macOS that risk crashes.
- A dropped frame is written as a zero-filled placeholder page or frame, flagged in
  `frames.csv`, so page *i* of every channel file always matches CSV row *i*.
- Disk throughput: 1920×1200 at 16 bits and 10 Hz is about 46 MB/s per fluorescence channel.
  Free disk space is shown in the UI.
