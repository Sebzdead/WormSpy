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
