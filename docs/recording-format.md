# WormsPy recording format

This document describes what WormsPy 2 writes to disk and how to read it, with or without
WormsPy. The design decisions behind it are in ADR 0006 (acquisition) and ADR 0007
(recording).

## Folder layout

```
WormsPy_data/                      data_root in rig.toml
└── 2026-09-28_N2-3/               a session: <date>_<animal-id>, one animal on one plate
    ├── session.json
    ├── rec-001/                   a recording: one Record … Stop
    │   ├── gcamp.tif              fluorescence channel: raw uint16 BigTIFF
    │   ├── brightfield.mkv        behaviour channel: FFV1 lossless 8-bit video
    │   ├── frames.csv             one row per frame
    │   ├── tracking.csv           one row per frame the tracker evaluated
    │   └── recording.json         written when the recording stops
    └── rec-002/ …
```

Channel files are named after the channel (`[cameras.*].name` in rig.toml). The **role** sets
the format:
- a `fluorescence` channel is always raw 16-bit TIFF;
- a `behaviour` channel is always FFV1 video.

A ratiometric rig with two fluorescence channels therefore writes two TIFFs.

## The one rule: row *i* is frame *i*

Row *i* of `frames.csv`, page *i* of every TIFF and frame *i* of every video all describe the
same exposure pair.

- **A dropped frame is still written**, as an all-zero placeholder, and flagged in
  `<channel>_dropped`. Files therefore never fall out of step. Filter on the `_dropped` flags
  (or `rec.valid`) before analysing pixel values.
- **`frame_index`** is the acquisition run's own frame counter. It is contiguous and starts
  where the live view started, so a recording started a minute into live view begins around
  frame_index 600. Use it to join `frames.csv` and `tracking.csv`, and as the row label.
  Positions within the files are `frame_index − first_frame_index`.

## frames.csv

Written as frames arrive and flushed every 10 rows, so a crash loses at most about a second.

| Column | Unit | Meaning |
|---|---|---|
| `frame_index` | — | See above. |
| `host_time` | s | Capture time on the PC's monotonic clock, at mid-exposure (the camera timestamp mapped to host time). Only differences are meaningful. Empty if both frames were dropped. |
| `<ch>_frame_id` | — | The camera's frame counter for this frame, counted from the start of the acquisition run. Empty if dropped. |
| `<ch>_timestamp_ns` | ns | The camera's own exposure timestamp (chunk data). Each camera has its own clock. |
| `<ch>_dropped` | 0/1 | 1: this channel's image is a zero-filled placeholder. |
| `pair_skew_ns` | ns | Change in the two cameras' timestamp offset since the first pair. It should stay within a few hundred µs; drift or jumps mean the trigger or pairing is suspect. |
| `x_um`, `y_um`, `z_um` | µm | Stage position interpolated to the frame's capture time. This is the plate position at the centre of the reference (clock) camera's image. |
| `z_moved` | 0/1 | Z changed since the previous frame (the operator refocused). Fluorescence intensity across such frames is not directly comparable. |
| `focus_set_z_um` | µm | The stored focus plane (Set Focus), if any. |
| `event` | — | Event markers placed on this frame; several are separated by `;`. |
| `stim_state`, `stim_param` | — | Reserved for closed-loop optogenetics; empty for now. |

## tracking.csv

One row per frame the tracker evaluated. The tracker runs beside acquisition and may skip
frames when busy, so this file can have gaps. Join it to `frames.csv` on `frame_index`.

| Column | Meaning |
|---|---|
| `frame_index` | The frame the estimate was made on (empty if it fell outside the recording). |
| `source_channel` | Which camera the tracker was watching. |
| `frame_id`, `capture_ts` | That frame's camera frame ID and host capture time. |
| `estimator` | `dark_object` (brightfield silhouette) or `bright_marker` (fluorescent marker). |
| `target_px_x`, `target_px_y` | The detected target in that camera's pixels (x = column, y = row). Empty if nothing was found. |
| `worm_x_um`, `worm_y_um` | The stage position that would centre the target in the source camera. |
| `accepted` | 1 if the estimate passed the plausibility check and drove the stage. |
| `status` | `tracking`, `lost`, `override` (joystick), or `limit` (at the safe-range edge). |

## recording.json

Written once, when the recording stops. **If it is missing, the recording was cut short**, by a
crash or power loss. Everything up to the last flushed row is still readable, but the channel
files may hold a frame or two fewer than `frames.csv`: use the shortest.

```json
{
  "frames": 18000, "first_frame_index": 612, "last_frame_index": 18611, "fps": 10.0,
  "dropped": {"gcamp": 0, "brightfield": 1, "writer backlog": 0},
  "failed": null,
  "channels": {"gcamp": {"role": "fluorescence", "file": "gcamp.tif"},
               "brightfield": {"role": "behaviour", "file": "brightfield.mkv"}},
  "camera": {"preset": "Default",
             "settings": {"fps": 10.0, "clock_exposure_us": 14000.0, "clock_gain_db": 0.0,
                          "follower_exposure_us": 5000.0, "follower_gain_db": 0.0}}
}
```

`writer backlog` counts frames the disk could not keep up with. They are zero-filled in every
channel. `failed` holds the error that stopped a recording early.

## session.json

```json
{
  "created": "2026-09-28T14:03:11+02:00",
  "metadata": {"animal_id": "N2 #3", "operator": "SW", "condition": "off food", "notes": ""},
  "config": {"fps": 10.0, "camera": {…}, "channels": {…}, "calibration": {…}},
  "software": {"name": "wormspy", "version": "2.0.0", "git": "14e41b2…"}
}
```

`config.calibration` is a snapshot of `calibration.json` at the time the session was created
(see ADR 0009):

- `reference`: the camera whose image centre is the stage position (the clock camera).
- `cameras.<ch>.matrix_px_per_um`: a 2×2 matrix mapping a plate offset (µm) from the stage
  position to a pixel offset, including rotation and mirroring.
- `cameras.<ch>.origin_px`: the pixel that sees the stage position. It is the image centre for
  the reference camera; for the other camera it is measured by registration. It is null if the
  cameras could not be registered, in which case `registration` says why.

**Plate position of a pixel** `p` in channel `c` at frame *i*:

```
plate_um = (x_um[i], y_um[i]) + inverse(matrix_c) · (p − origin_c)
```

## Reading it

**With WormsPy** (`uv sync --extra analysis`):

```python
from wormspy.reader import open_recording, open_session

session = open_session("WormsPy_data/2026-09-28_N2-3")
with session.open(1) as rec:                      # rec-001
    calcium = rec["gcamp"].array()                # (N, H, W) uint16, memory-mapped
    trace = calcium[:, 540:560, 900:920].mean(axis=(1, 2))
    trace[~rec.valid.to_numpy()] = float("nan")   # drop placeholders
    head = rec["brightfield"][rec.index[100]]     # one behaviour frame, by frame_index
    for frame_index, label in rec.events: ...
    rec.frames[["x_um", "y_um", "track_status"]]  # stage path and tracking, one row per frame
    rec.plate_um("brightfield", (960, 600), rec.index[100])
```

**Example analysis:** `python -m wormspy.analysis.segmentation <rec-NNN or session folder>
[--overlay]` follows one bright neuron in the calcium channel and writes
`analysis/<channel>_segmentation.csv` (one row per frame_index: centroid, brightest-pixel
signal, local background, and the neuron's plate position) into each recording folder. It is the
v1 `Segmentation.py`, ported; its docstring lists what changed.

**Without WormsPy:**
- **Fiji:** open `gcamp.tif` directly; it is a plain, uncompressed multi-page BigTIFF. FFV1
  `.mkv` files open through the FFmpeg-based importers, or convert with
  `ffmpeg -i brightfield.mkv frames_%06d.png`.
- **Python:** `tifffile.memmap("gcamp.tif")`, `cv2.VideoCapture("brightfield.mkv")` (FFV1 is
  intra-frame only, so seeking by frame number is exact), and `pandas.read_csv("frames.csv")`.
- **MATLAB:** `readtable("frames.csv")`, `Tiff`/`imread` for pages, and `VideoReader` for the
  video (this needs an FFmpeg-enabled build on some platforms).
