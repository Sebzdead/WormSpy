# 11. Simulated rig first; cross-platform development

Date: 2026-09-27

## Status

Accepted

## Context

Development has so far required the physical rig. Hardware steps are verified by hand on the rig,
which runs Windows, while day-to-day development happens on a Mac. Tracking can only be tuned
meaningfully in closed loop.

## Decision

- **Build the simulated rig before any hardware adapter.** It consists of `SimCamera` ×2,
  `SimStage` and `SimGamepad`, coupled in closed loop:
  - A synthetic worm crawls on a virtual plate (sinusoidal body with a behavioural random walk,
    reversals, and speed changes).
  - The worm is rendered into both channels from the simulated stage position: a brightfield
    silhouette, and a fluorescent spot or neuron. Noise, blur and bleaching are optional.
  - Frame drops, trigger jitter, stage latency and serial delays can be injected.
  - The simulated clock camera triggers the simulated follower camera, as the real ones do
    (ADR 0006).
- **Replay mode** feeds frames from a real recorded session for estimator tuning. In this mode,
  stage moves don't affect the image. It is optional, used once real recordings in the new format
  exist.
- **Packaging:**
  - `pyproject.toml` plus `uv`, targeting Python 3.12, or the newest version that has PySpin
    wheels for the chosen Spinnaker SDK. This must be checked before pinning.
  - Hardware SDKs (PySpin, zaber-motion, pypylon) are optional extras.
  - One launcher replaces `StartWormSpy.bat`.
  - CI runs pytest against the simulated rig on macOS and Windows.
- The rig runs Windows. Development happens on macOS against the simulated rig. Hardware
  verification is a manual step on the rig.

## Consequences

- Tracking, recording, the state machine and the UI can all be developed and regression-tested
  without hardware.
- The simulator's worm model has to be good enough to expose tracking failures: fast reversals,
  omega turns, and the worm leaving the field of view. Real recordings through replay close the
  remaining gap.
