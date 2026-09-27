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

## Implementation (2026-09-27)

- **Estimators** (`wormspy.tracking.estimators`):
  - **DarkObject** measures contrast against a heavily blurred background, computed at a coarse
    scale. It is robust to uneven illumination and takes about 4 ms per 1920×1200 frame.
  - **BrightMarker** thresholds at median + k·MAD of the frame's own noise, so a noise-only frame
    yields no target. It finds the intensity-weighted centroid and takes about 12 ms per frame.
- **Controller** (`wormspy.tracking.controller`):
  - `TargetFilter` does plausibility gating (`jump_margin + max_worm_speed × Δt`), declares *lost*
    after three misses, and re-acquires with a search radius that grows with time since the worm
    was last seen.
  - `VelocityServo` does proportional control toward the absolute target with a deadband, a speed
    cap and an acceleration limit. It also includes **velocity feed-forward**, which this ADR did
    not originally specify: the filter estimates the worm's velocity and the servo follows it.
    That removes the proportional lag (speed ÷ gain).
- **Measured on the simulated rig** (8 worms × 60 s at 200 µm/s, with reversals):
  - median centring error drops from ~100 µm to ~17 µm with feed-forward
  - worst case drops from 273 µm to 163 µm, in a 1440 × 900 µm field of view
  - zero lost events
- **Tracker thread** (`wormspy.tracking.tracker`):
  - It evaluates the newest frame of the source channel, stepping at 50 Hz, and skips frames when
    the frame rate is higher than that.
  - It converts pixels to plate position using the stage position at capture time.
  - It logs each evaluated frame to `tracking.csv` (ADR 0007).
  - It sends XY velocity only on meaningful change.
  - It reports status changes to the state machine.
- **Tracking requires a calibration.** The source channel may be named directly, or given as a role
  ("behaviour"/"fluorescence"), which resolves to the clock camera first. Acquisition failure,
  Stop Live and Shutdown all stop the tracker, which stops the stage.
- **Tuning** (`ServoConfig` defaults: gain 2 s⁻¹, deadband 10 µm, max speed 1500 µm/s, max
  acceleration 3000 µm/s²) was done in simulation only. Re-check it on the rig.
