# 6. Hardware-triggered acquisition: the calcium camera is the clock

Date: 2026-09-27

## Status

Accepted. Supersedes the acquisition part of ADR 0001.

## Context

ADR 0001 paired frames by reading two free-running cameras once per software loop tick. That is
index pairing, not temporal pairing. If either camera drops or buffers one extra frame, every
later pair shifts by a frame (100 ms at 10 Hz), and nothing detects it. The calcium recording's
fixed, stable rate and exposure is the non-negotiable requirement.

## Decision

- **1:1 hardware trigger.** The **calcium channel's camera is the primary** (the clock). It
  free-runs at the preset's fixed frame rate with fixed exposure (auto-exposure and auto-gain
  off) and outputs *ExposureActive* on a GPIO line. That line is wired to the follower camera's
  trigger input.
- **The follower** (behaviour channel, or the second fluorescence channel in ratiometric sessions)
  fires once per clock exposure and **keeps its own exposure time**. For behaviour that is
  typically shorter, to reduce motion blur.
- **Both cameras run at the same rate.** Divided rates are out of scope for v1.
- **Chunk data** (`FrameID`, `Timestamp`) is enabled on both cameras and logged per frame.
  Acquisition uses a queued (not latest-only) buffer strategy so every sensor frame is seen.
- **Frame pairs are formed per clock frame and keyed by a contiguous `frame_index`.** Pairing is
  checked with device frame IDs. The two device clocks are related through the trigger, so an
  offset estimated at start and continuously checked is logged in `session.json` and the
  recording metadata.
- **Drop policy: never stop, flag loudly.** A FrameID gap or a writer that can't keep up leads to:
  - a gap row in the per-frame CSV (`dropped` flags per channel; `frame_index` stays contiguous)
  - a red UI alarm with a drop count
  - a drop summary in the recording's metadata

  Writers use bounded queues. Backpressure is surfaced, never silently absorbed.
- **The app owns camera configuration** through named **presets**: rate, exposures, gain,
  ROI/binning, pixel format and channel roles. Presets are editable only while not recording and
  are snapshotted into every session.

## Consequences

- Pairing is exact by construction, and any loss is visible and addressable by `frame_index`.
- It needs one GPIO cable between the cameras, plus a documented wiring diagram in the build guide.
- The display and tracker consume frames as best-effort *latest-only* readers off the pair
  stream. They can never slow down acquisition.
