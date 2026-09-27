# WormsPy — Context & Glossary

The domain language of WormsPy, a tracking epifluorescence microscope for freely moving
*C. elegans* (Wittekindt et al., *Nat. Commun.* 17:4411, 2026). Implementation details live in
code and ADRs (`docs/adr/`), not here. Scope of the rework: `docs/rework/requirements.md`.

## The rig

- **Channel**: one of the two cameras (2× Teledyne FLIR Blackfly S BFS-U3-23S6M-C). A shared
  7.5× objective and dual-band dichroic splits the light onto them. Both channels see the same field
  of view (~1440 × 900 µm) through one light path.
- **Channel role**: what a channel records in a given session.
  - **Fluorescence**: calcium or marker signal, recorded raw at 16 bits.
  - **Behaviour**: the red transmission band used as a brightfield silhouette, recorded 8-bit and
    losslessly compressed.

  A normal session has one of each. A **ratiometric** session (e.g. GCaMP + DsRed in ASH) has
  two fluorescence channels.
- **Calcium channel**: the fluorescence channel whose signal the experiment is about. Its frame
  rate and exposure are treated as fixed for the whole session.
- **Stage**: Zaber TSB60E × 2 (XY, 50.4 × 50.4 mm travel) carrying the optics, and a Zaber
  VSR20A-T3A (Z) for focus. Stage motion moves the microscope relative to a fixed plate.
- **Gamepad**: a dual-stick controller. The left stick jogs XY and the right stick jogs Z. Its buttons issue
  **Commands**.

## Acquisition

- **Clock camera (primary)**: the calcium channel's camera. It free-runs at the fixed frame rate
  and outputs its exposure-active signal on a GPIO line.
- **Follower camera**: the other camera. Each clock pulse triggers exactly one follower exposure (1:1).
  The follower keeps its own exposure time, typically shorter for the behaviour role.
- **Frame pair**: one clock exposure plus the follower exposure it triggered. Pairing is exact by
  construction, with no software matching.
- **Frame index**: a contiguous counter over frame pairs, assigned by the application. It is the key
  that joins video frames, telemetry and events.
- **Device frame ID / device timestamp**: each camera's own counter and clock, read from Spinnaker
  chunk data. Used to detect drops and verify pairing.
- **Dropped frame**: a gap in a device frame ID, or a frame that the writer could not persist. A
  recording never stops for a drop. The drop becomes a **gap row** in the per-frame CSV and raises a UI alarm,
  and the drop count goes into the recording's metadata.
- **Loop drift**: the unbounded divergence between two free-running acquisition loops. The
  hardware trigger eliminates it. Tens of ms of offset between channels was acceptable for the paper's science,
  but drift never was.
- **Preset**: a named, saved set of camera settings (frame rate, exposures, gain, ROI/binning,
  pixel format, channel roles). The application owns camera configuration, so SpinView is not
  needed.

## Sessions and recordings

- **Session**: one animal on one plate. It is one folder `<date>_<animal-id>/` containing `session.json`,
  which holds the metadata form, notes, the full config, the preset and a calibration snapshot. Live view and
  tracking run continuously through a session.
- **Recording**: one Start→Stop span within a session. It consists of:
  - one file per channel: **BigTIFF** (fluorescence role) or **FFV1** video (behaviour role)
  - one **per-frame CSV**
- **Per-frame CSV (the spine)**: one row per frame index, appended while recording. It holds
  device IDs and timestamps, stage and worm position, tracking state, focus/Z flags, event markers and
  reserved stimulus columns. It is the single source of truth for aligning video, behaviour and activity offline.
- **Event marker**: an operator-triggered flag (hotkey or button) written into the current CSV
  row, used for trials, stimuli and observations.
- **Session metadata**: a light form (operator, animal/strain ID, condition, free notes) with
  configurable fields, pre-filled from the previous session.

## Space and calibration

- **Stage coordinates**: native Zaber positions, logged in µm.
- **Calibration**: a routine that moves the stage by known distances and measures the resulting image
  shift (phase correlation). It yields each camera's px→µm scale plus a rotation/sign matrix. Run it
  when the optics change. The result is stored in config and snapshotted into each session.
- **Registration**: the brightfield↔fluorescence (or fluorescence↔fluorescence) image transform
  derived from calibration. Used for overlays and ratiometric analysis.
- **World (plate) position**: stage position plus the tracked point's offset in the field of view, converted
  to µm by calibration. This is where the worm actually is on the plate, e.g. relative to a food patch.

## Tracking

- **Target estimator**: a pluggable function that maps a frame to a tracked point (or "none").
  Each estimator has a configurable **source channel**. v1 ships two:
  - **Dark-object threshold**: brightfield silhouette on a light background.
  - **Bright-marker threshold**: brightest fluorescent object.

  Pose estimators (e.g. DeepLabCut, to track the nose or nerve ring) are plugins added later.
- **Setpoint**: where the tracked point is held. Always the centre of the field of view. Body-frame
  offsets (e.g. "keep the nerve ring centred") need a pose estimator and are out of scope for v1.
- **Tracking target**: the absolute stage position that would centre the tracked point. It is
  computed from the stage position *at the frame's capture time* plus the calibrated pixel error, so
  estimator latency does not cause overshoot.
- **Velocity servo**: the tracking control law. It continuously commands Zaber velocity toward the
  tracking target, with a deadband and a maximum speed. It is chosen for smooth motion, because jerks
  blur the image and couple into Z, and *C. elegans* is sensitive to acceleration.
- **Target lost**: the estimator finds nothing plausible, either no object or a jump that is physically
  impossible. The stage ramps to zero, the UI alerts, and tracking resumes automatically when a plausible target
  reappears near the last position.
- **Joystick override**: any XY stick deflection takes over XY immediately while tracking is
  on. Tracking resumes when the stick is released. Overrides are logged per frame.
- **Limit reached**: a tracking or joystick move clamped at the edge of the configured XY safe range.
  The stage stops on that axis, the UI alerts, the event is flagged per frame, and recording continues.

## Focus

- **Hold Focus**: focus is set by the operator, never computed, because one shared Z serves both channels and the
  neuron's depth changes every session (ADR 0002).
  - **Set Focus** stores the current Z.
  - **Return to Focus** moves back to it.
- **Z move during recording**: allowed via the joystick, because the operator *is* the focus loop. Every Z change
  is logged and flagged per frame so affected frames can be excluded.
- **Z safe range**: a rig-wide Z range from config that software never exceeds. It exists because
  Zaber's travel limit is not a safe objective-to-sample distance.

## Control

- **Command**: a named operator action (toggle tracking, start/stop recording, set focus, return to
  focus, mark event, centre point, …). The gamepad, keyboard shortcuts and UI buttons all issue the
  *same* commands to a single **state machine**. The UI only renders that state machine's state, so the UI and the
  hardware cannot disagree.
- **Event**: a fact reported *to* the state machine by the executor or a worker, e.g. session
  folder created, frames dropped, tracker lost the target, recording failed. Events cannot be
  rejected. Commands can.
- **Effect**: a side effect the state machine *asks for*, e.g. start acquisition, open a recording,
  move Z. The state machine never touches hardware or files itself.
- **Executor**: the component that carries out Effects on the hardware and filesystem, and reports
  the outcomes back as Events.
- **Alarm**: a condition the operator must see, e.g. dropped frames, a failed recording, or low disk
  space. There is at most one alarm per kind. It stays until acknowledged or cleared by its source.
- **Click-to-centre**: double-clicking a point on either live view moves the stage so that point sits at the
  centre of the field of view (uses calibration).

## Development

- **Simulated rig**: fake cameras and a fake stage coupled in closed loop. A synthetic worm crawls on a
  virtual plate and is rendered into both channels from the simulated stage position. Frame drops,
  latency and noise can be injected. It is used to develop and test everything except final hardware
  verification.
- **Replay**: a simulated-rig mode that feeds a real recorded session's frames, for tuning estimators.
  Stage moves do not affect the image in this mode.
