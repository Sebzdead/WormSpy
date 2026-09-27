# 8. Tracking: pluggable estimators, absolute targets, velocity servo

Date: 2026-09-27

## Status

Accepted

## Context

v1 sent a relative `move rel (error/5)` on every frame, after two blocking serial position
reads. It had several faults:

- Its limit checks were no-ops (joined with `or`).
- When no worm was found, it drove toward a mis-scaled fallback coordinate.
- The DeepLabCut path never worked.

The refactor branch's `Tracker` re-applied relative moves to stale frames. The paper recommends
tracking at ≥5–10 Hz for runs of more than 30 minutes. *C. elegans* is sensitive to acceleration,
and stage jerk blurs frames and couples into the calcium signal.

## Decision

- **Pluggable target estimators**, each with a configurable **source channel**. v1 ships two:
  - dark-object threshold (brightfield silhouette)
  - bright-marker threshold (brightest fluorescent object)

  Parameters live in config and can be tuned live using a threshold-mask overlay. Pose estimators
  such as DeepLabCut are a later plugin.
- **The setpoint is always the centre of the field of view.** Keeping a body part centred needs a
  body-frame offset from pose estimation, so that is out of scope for v1.
- **Absolute targets.** Each *new* frame is processed at most once, deduplicated by
  `frame_index`. The target is the stage position at that frame's capture time, interpolated from
  the stage poller, plus the pixel error converted by calibration. Because of this, estimator
  latency can't cause overshoot.
- **Velocity servo.** A control loop commands Zaber `move vel` in proportion to the distance to the
  target, with a deadband, a speed limit and an acceleration limit. There are no per-frame step
  moves.
- **Target lost** means the estimator finds no object, or a jump that is physically impossible.
  The stage ramps to zero velocity and the UI alerts. Tracking resumes automatically when a
  plausible target reappears near the last position.
- **Joystick override:** any XY stick deflection takes over immediately. Tracking resumes when the
  stick is released.
- **Limit reached:** motion is clamped at the configured XY safe range (default: device travel).
  The UI alerts and recording continues.
- Every state (off / tracking / lost / override / limit) is logged per frame (ADR 0007).
- **Click-to-centre** (double-click on either live view) uses the same absolute-target path.

## Consequences

- Motion is smooth and continuous, and lag is harmless.
- Tuning (gain, deadband, speed and acceleration limits) happens in the simulated rig first
  (ADR 0011), then on the rig.
- Stage position comes from a single poller thread, so neither the tracker nor acquisition ever
  blocks on serial I/O.
