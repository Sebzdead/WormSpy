# 3. Recording integrity: CSV spine and intra-frame brightfield codec

Date: 2026-06-21

## Status

Accepted

## Context

The original recording wrote the brightfield camera as an XVID-compressed AVI and the
fluorescence camera as uncompressed 16-bit TIFF. A separate CSV logged only
`timestamp, X_position, Y_position` from the left/brightfield loop, with no link between a CSV
row and a specific video frame, and playback speed depended on a hand-typed `FPS` constant.
The README and code also disagreed on the brightfield codec (README said MJPG, code used XVID).

The paper's analysis aligns calcium activity with stage motion and behaviour, so frame-accurate
addressability and frame-accurate behaviour scoring matter.

## Decision

- **The CSV is the per-frame spine of a session.** One row per acquisition tick, keyed by a
  shared `frame_index`, logging at minimum: `frame_index, capture_timestamp, left_frame_id,
  right_frame_id, x_pos, y_pos, z_pos, tracking_mode, is_tracking`. Every video frame is
  addressable by `frame_index`; dropped frames appear as index gaps. (Reserve room for stimulus
  state once closed-loop optogenetics lands.)
- **Brightfield uses an intra-frame codec** (MJPG, or FFV1 for lossless) rather than the
  interframe XVID, so each frame stands alone and behaviour scoring never reads interpolated
  content. Lossless is offered as an option alongside the existing TIFF path. README and code are
  reconciled.
- Fluorescence remains uncompressed 16-bit TIFF to preserve dynamic range.

## Consequences

- Sessions become self-describing and frame-accurate; the `FPS` constant is no longer trusted for
  timing (see [[ADR 0001]]).
- Brightfield files are larger than XVID but frame-accurate.
- Downstream analysis joins video to telemetry on `frame_index` rather than wall-clock guesswork.
