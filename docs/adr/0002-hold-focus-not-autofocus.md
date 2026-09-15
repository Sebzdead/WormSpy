# 2. "Hold Focus" instead of automatic focus

Date: 2026-06-21

## Status

Accepted

## Context

The original `FocusLock`/autofocus drove the z-stage with a PID whose setpoint was the
Laplacian-variance focus metric captured at the instant the user enabled it, reading frames
from the fluorescence camera. This is broken: sharpness vs. z is an unsigned unimodal hill (a
PID cannot hill-climb it), the setpoint was an arbitrary starting value rather than the
in-focus peak, and the metric was computed on the wrong camera.

Attempting to redesign it surfaced hard physical constraints of the rig:

- **One shared z motor and one objective.** Both cameras image a single focal plane; you
  cannot keep brightfield and fluorescence in focus at two different planes at once.
- **Best focus is defined by the target neuron**, whose depth within the worm changes every
  recording session. There is no fixed parfocal offset to calibrate once; per-session
  calibration was judged infeasible.
- The neuron is often only a few bright pixels in an otherwise black/low-signal frame, so a
  whole-frame focus metric on the fluorescence channel is dominated by background noise.
- Any active focus loop dithers the single shared stage, and that dither couples directly into
  the calcium intensity signal — a confound that is worse the thinner the depth of field.

## Decision

Focus is **set by the operator** (manual joystick z control while watching the fluorescence
feed). Software does not compute focus. Two behaviours are provided:

- **(A) Hold / Restore focus (default).** A "Set Focus" action stores the current z; the stage
  holds it and can lock out accidental z drift during recording; a "Return to Focus" action
  snaps back to the stored z. No sensing, no dither, no artifact.
- **(B) Drift compensation (opt-in, experimental).** At focus-set time, capture the brightfield
  focus state as a reference (implicitly capturing the session's offset, no separate
  calibration). During recording a small brightfield hill-climb nudges z to keep the worm body
  at that reference, tracking z-drift on the assumption the neuron-to-body geometry holds. Logs
  z per frame. Clearly labelled as introducing a focus/intensity artifact.

The UI is renamed from "FocusLock"/"autofocus" to "Hold Focus" so the name does not promise
behaviour the hardware cannot deliver.

## Consequences

- The default path produces the cleanest possible calcium trace (no stage motion during
  recording).
- Worm z-drift during a recording is handled by the operator (default) or by opt-in (B) at a
  known cost.
- Removes the PID controller and the fluorescence-channel focus metric entirely.
