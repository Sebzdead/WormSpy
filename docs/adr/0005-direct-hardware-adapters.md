# 5. Direct hardware adapters (PySpin, zaber-motion) instead of pymmcore-plus

Date: 2026-09-27

## Status

Accepted

## Context

The rig hardware is:

- cameras: 2× FLIR BFS-U3-23S6M-C
- Z stage: Zaber VSR20A-T3A
- XY stages: 2× Zaber TSB60E

We evaluated pymmcore-plus (Micro-Manager core from Python) on 2026-09-27, against the
pymmcore-plus and mmCoreAndDevices sources. It offers ready-made adapters, Qt widgets, OME
writers and a demo mode. It blocks this use case in several ways:

- MMCore has a **single circular buffer sized for one frame shape**. Two cameras streaming at once
  must have identical width × height × bit depth. That rules out an 8-bit behaviour channel or a
  cropped brightfield channel alongside 16-bit calcium.
- The Spinnaker adapters expose **no hardware FrameID or camera timestamp** (no chunk data), so
  dropped frames can't be detected.
- MMCore's public API **can't issue velocity moves**, so the velocity servo (ADR 0008) would need
  workarounds.
- Adapters are pinned to specific SDK versions (Spinnaker 4.3–<5 for SpinnakerC, pylon 7.1).
  Fixing an adapter bug means C++ and a nightly build.

## Decision

- Hardware sits behind **our own small interfaces**: `Camera`, `Stage`, `Gamepad`, and later
  `Stimulus`. The fakes from the refactor branch carry over as the basis of the simulated rig
  (ADR 0011).
- **Cameras: PySpin directly, not EasyPySpin.** This gives full GenICam access for rate, exposure,
  gain, pixel format, trigger/GPIO lines, and chunk data (`FrameID`, `Timestamp`). A pypylon
  (Basler) adapter is optional and not part of v1 QA.
- **Stages: zaber-motion (ASCII).** The connection is opened once at startup. It supports absolute
  moves, velocity moves (`move vel`) and position reads. Safe XY and Z ranges are enforced inside
  the `Stage` adapter, in native units.
- Hardware SDKs are optional imports, so the app runs anywhere with the simulated rig. Useful
  pieces of the pymmcore-plus ecosystem, such as `ome-writers` or `useq-schema`, may be used
  standalone.

## Consequences

- We write and maintain three thin adapters, but we control per-camera formats, chunk data and
  velocity control.
- Adoption by other labs happens by writing an adapter against the interfaces. Any vendor-specific
  code stays inside its adapter.
