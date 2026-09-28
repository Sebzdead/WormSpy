# WormsPy rework — requirements & scope

*Agreed 2026-09-27 in a design interview based on
[`docs/codebase-review-2026-09-27.md`](../codebase-review-2026-09-27.md). The glossary is in
[`CONTEXT.md`](../../CONTEXT.md) and the decisions are in [`docs/adr/`](../adr/).*

## Audience

The rework is built and validated for **our rig**. It is structured so that other labs can adopt
it: hardware sits behind interfaces, and the rig's details live in config. Multi-vendor QA and an
installer are out of scope.

## Use cases (v1)

| Use case | v1 | Notes |
|---|---|---|
| *C. elegans*: track on brightfield + record calcium | ✅ | The paper's core use (BWMC, AWC, RIA). |
| *C. elegans*: track on a fluorescent marker | ✅ | Estimator source channel is configurable. |
| Ratiometric imaging (e.g. GCaMP + DsRed, ASH) | ✅ | Both channels have the fluorescence role and are recorded raw 16-bit. |
| *Drosophila* larvae | ✅ | Via estimator parameters and presets; no special code. |
| Closed-loop optogenetics | Designed for | Reserved CSV columns and a `Stimulus` interface (sim only). Built later. |
| DeepLabCut / pose tracking, body-frame setpoint | Later | Added as a plugin estimator. |
| Software control of excitation LEDs | ❌ | Out of scope; LEDs stay manual. |
| Drift-compensating focus (ADR 0002 mode B) | Later | Deferred. |
| Analysis pipeline | Reader only | Reader library plus format docs. Segmentation example ported later. |

## Hardware (reference rig)

- Cameras: 2× Teledyne FLIR Blackfly S **BFS-U3-23S6M-C** (USB3). They are linked by a GPIO
  cable: the calcium camera's ExposureActive output drives the follower camera's trigger input.
- Stages: Zaber **VSR20A-T3A** (Z) and 2× Zaber **TSB60E** (XY, 50.4 × 50.4 mm).
- Optics: 7.5× objective with a 100 mm tube lens, giving a ~1440 × 900 µm field of view. There
  are two emission channels via a dual-band dichroic. Excitation is 470 and 565 nm.
- Dual-stick gamepad.
- The rig PC runs Windows. Development happens on macOS.
- Typical acquisition: 10 Hz, 14 ms calcium exposure, sessions of 1–30+ minutes.

## Decision log

| # | Topic | Decision | ADR |
|---|---|---|---|
| — | UI stack | Single-process PySide6 + pyqtgraph desktop app; no HTTP layer | 0004 |
| — | Recording | Raw 16-bit fluorescence, one file per recording; per-frame CSV written incrementally | 0007 |
| — | Calibration | Automatic routine replaces hand-measured constants | 0009 |
| — | Tracking target | Absolute stage targets, not relative steps | 0008 |
| — | Simulation | Simulated rig first | 0011 |
| Q1 | Audience | Our rig, adoptable later | — |
| Q2 | Use cases | See the table above; optogenetics designed for, built later | — |
| Q3 | Estimators | Two thresholds in v1; DLC later as a plugin | 0008 |
| Q4/Q6 | HW layer | Direct PySpin + zaber-motion; pymmcore-plus rejected | 0005 |
| Q5 | Frame rates | Same rate, 1:1 hardware trigger | 0006 |
| Q7/Q8 | Clock | Calcium camera is primary; follower keeps its own exposure | 0006 |
| Q9 | Structure | Session ⊃ Recordings + event markers | 0007 |
| Q10 | Fluorescence file | BigTIFF (raw uint16) | 0007 |
| Q11 | Behaviour file | FFV1 lossless | 0007 |
| Q12 | Drops | Keep recording; gap row + loud alarm + summary | 0006 |
| Q13 | Control law | Velocity servo toward the absolute target | 0008 |
| Q14 | Target lost | Stop, alert, auto-resume | 0008 |
| Q15 | Joystick vs tracking | Joystick overrides; tracking resumes on release | 0008 |
| Q16 | Setpoint | Always FOV centre (body-frame offset needs pose) | 0008 |
| Q17 | Estimator source | Selectable channel per estimator | 0008 |
| Q18 | Calibration | Both cameras + registration; re-run when optics change | 0009 |
| Q19 | Focus | Set / Return to Focus only | 0002 (amended) |
| Q20 | Z while recording | Allowed; logged and flagged per frame | 0002 (amended) |
| Q21 | Z safety | Rig-wide Z safe range in config | 0002 (amended) |
| Q22 | XY limit | Clamp, alert, keep recording | 0008 |
| Q23 | Controls | Shared Commands; double-click to centre | 0010 |
| Q24 | Display | Histogram + fixed levels, LUTs, tracking overlays, status readouts | 0004 |
| Q25 | Camera settings | Set in the app, saved as named presets | 0006 |
| Q26 | Metadata | Light configurable form + notes | 0007 |
| Q27 | Platform | Windows rig, Mac dev, uv, Python 3.12 (if PySpin allows) | 0011 |
| Q28 | Simulator | Synthetic closed-loop worm + replay of real recordings | 0011 |
| Q29 | Analysis | Reader library now; port segmentation later | 0007 |
| Q30 | Interim fixes | None; the rig is idle during the rework | — |
| Q31 | Repo | `rework` branch in this repo; `v1-legacy` tag; delete `PyQT` | 0004 |
| Q32 | Channel roles | Role per channel (behaviour or fluorescence); the role sets the format | 0007 |
| Q33 | LEDs | Out of scope | — |

## Review findings: where each is resolved

| Review finding (§1) | Resolved by |
|---|---|
| 1. Calcium recorded as normalised 8-bit XVID | ADR 0007: fluorescence role is always raw uint16 BigTIFF |
| 2. Basler Mono16 → Mono8 | ADR 0005: Basler is an optional adapter, outside v1 QA |
| 3. No-op stage limits, including Z | ADR 0005/0008 safe XY range; ADR 0002 amendment Z safe range |
| 4. Lost-worm fallback lurch | ADR 0008: Target lost → ramp to zero |
| 5. Independent camera loops | ADR 0006: hardware trigger, frame pairs, chunk IDs |
| 6. Hand-typed `FPS` | ADR 0006/0007: camera sets the rate; timestamps logged |
| 7. B button can't stop recording | ADR 0010: shared Commands, one state machine |
| 8. Folder race | ADR 0007/0010: session/recording folders created by the state machine |
| 9. CSV only in memory | ADR 0007: appended per frame |
| 10. Stop/restart broken | ADR 0004/0010: app-owned lifecycle; no HTTP generators |

## Build order

1. Packaging and skeleton: pyproject/uv, CI, and the core interfaces and tests carried over from
   `refactor/acquisition-core-adr0001`.
2. Simulated rig (ADR 0011).
3. State machine and Commands (ADR 0010).
4. Acquisition pipeline, writers and per-frame CSV against the simulated rig (ADR 0006/0007).
   Rig verification: a 10-minute recording with zero FrameID gaps.
5. Stage: poller, safe ranges, gamepad, Hold Focus, calibration (ADR 0002/0005/0009).
6. Tracking: estimators and velocity servo, tuned in simulation, then on the rig (ADR 0008).
7. PySide6 UI (ADR 0004).
8. Reader library and format docs (done 2026-09-28). Remove legacy code (done 2026-09-28) and tag `v1-legacy`
   (done).
9. FLIR camera adapter and hardware start-up (done 2026-09-28). Rig verification:
   [`rig-bringup.md`](rig-bringup.md).

Steps marked "rig verification" are done by hand on the rig PC.

## Housekeeping (needs explicit go-ahead)

- Tag the current `master` as `v1-legacy`.
- Delete the `PyQT` branch (local and remote).
- Decide whether `refactor/acquisition-core-adr0001` is deleted after its code is carried over.
