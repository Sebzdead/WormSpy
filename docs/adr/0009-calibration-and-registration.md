# 9. Calibration and registration replace hand-measured constants

Date: 2026-09-27

## Status

Accepted

## Context

v1 converted pixels to stage motion using several values set by hand in source code: field of
view size in mm (`TOTAL_MM_X/Y`), `MM_MST`, and per-axis orientation signs. There was no way to
check them, they did not account for camera rotation, and they described only one camera. The
paper also derives the worm's position on the plate (the AWC food-patch assay), and ratiometric
analysis needs the two channels registered.

## Decision

- A **calibration routine** moves the stage through a small known pattern and measures the image
  shift by phase correlation. It produces, **for each camera**, a px→µm scale and a
  rotation/sign matrix (an affine transform from the stage to the image).
- It also derives the **registration** between channels (brightfield↔fluorescence, or
  fluorescence↔fluorescence), which is used for overlays and ratiometric analysis.
- Calibration is run **when the optics change**, not every session. The result is stored in
  config with a date and a residual error. It is snapshotted into every session's
  `session.json`. The app warns when no calibration exists, or when it is older than the preset
  or config it was measured with.
- Tracking, click-to-centre and world (plate) position all use the calibration. There are no
  constants in code.

## Consequences

- Either camera can drive tracking.
- Changing the objective or camera mount requires re-running calibration, and the app prompts for
  it.
- Calibration needs a textured sample (e.g. a plate with a grid or bacterial lawn edge). This is
  documented in the operator guide.

## Implementation notes (2026-09-27)

- **Routine** (`wormspy.calibration.routine`):
  - It moves the stage through six moves of 100 µm: ±x, ±y and two diagonals.
  - It measures each camera's image shift by windowed phase correlation, fits the matrix by
    least squares, and always returns the stage to its start position.
  - It uses the stage position *measured* after each move, and images taken at least two frames
    after settling.
  - It fails safely, without saving, when there isn't enough image shift (no texture) or when the
    residual exceeds 1 px.
- **Storage.** The result goes to `calibration.json`, with each camera's `matrix_px_per_um`, RMS
  residual and image shape, plus a creation time. It is loaded at startup. Click-to-centre is
  refused until a calibration exists.
- **Accuracy on the simulated rig:** matrices are within 1% of the true scale, rotation and mirror
  for both cameras, and click-to-centre lands within 2 µm. Sub-pixel shift estimation has a bias of
  about 0.1 px (OpenCV's centroid peak refinement). That is negligible for moves of about 100 px.
- **Not yet done: the translation offset between the two cameras' image centres.** The two
  matrices give the rotation and scale between the channels, but not their offset. Measuring it
  needs a feature seen by both cameras. A fluorescent bead slide under transmitted light would
  work: the beads appear dark in brightfield and bright in fluorescence. This matters for overlays
  and ratiometric analysis, not for tracking or click-to-centre.
