# 1. Decouple acquisition from the HTTP stream and from tracking

Date: 2026-06-21

## Status

Accepted

## Context

In the original design, frame acquisition for each camera lived *inside* a Flask
MJPEG generator (`video_feed`, `video_feed_fluorescent`). Each generator ran its
own `while cap.isOpened()` loop, opened its own camera, and the left generator also
opened the Zaber serial connection and ran the worm-tracking control inline.

Consequences of that design:

- The two camera loops were driven by how fast each browser `<img>` consumed the
  MJPEG stream, so they free-ran independently and could drift without bound. This
  violates the hard requirement that the two streams stay paired (see
  [[loop-drift]] in CONTEXT.md).
- The Zaber motors and `xMotor/yMotor/zMotor` globals only existed while the left
  feed was streaming, so `move_to_center`, manual mode, and autofocus crashed
  (`NameError`) otherwise, and the motor connection died on browser disconnect.
- Recorded playback speed depended on a hand-typed `FPS` constant rather than real
  capture timing.

The science needs a **fixed, stable acquisition frame rate with constant exposure**
for the calcium recording above all else. Tracking stability is explicitly lower
priority and may lag.

## Decision

1. **Single acquisition loop, owned by the application (not the HTTP handler).**
   One loop grabs both cameras, assigns each pair a shared monotonically-increasing
   `frame_index` plus capture timestamp + camera frame ID, writes to the recording,
   and publishes the latest frame of each camera for display. Frame pairing is
   guaranteed by construction; drift is structurally impossible.

2. **The camera is the clock.** Acquisition rate and exposure are fixed camera-side
   (`AcquisitionFrameRateEnable` + fixed `AcquisitionFrameRate`, auto-exposure/gain
   off). Recording uses a queued grab strategy so every sensor frame is written;
   per-frame camera frame ID + device timestamp are logged so dropped frames appear
   as index gaps rather than silent timing errors. `LatestImageOnly` is used only for
   the display publish. This is the natural bridge to an optional external hardware
   trigger later.

3. **The Zaber connection opens once at application startup** and is held for the
   process lifetime, degrading gracefully if no hardware is present.

4. **Tracking runs in its own thread for all tracking modes** (thresholding,
   fluorescent marker, DeepLabCut). It consumes the latest brightfield frame,
   computes worm position, and commands the motors. Fast modes keep up frame-for-frame;
   DLC may lag but never affects the acquisition/recording cadence. This supersedes an
   earlier consideration of tracking inline in the acquisition loop.

## Consequences

- The non-negotiable (paired frames at a fixed, stable rate and exposure for the
  calcium recording) is met regardless of tracking cost.
- Tracking control may lag the live frame by one or more frames, especially under
  DLC. Acceptable: a worm does not teleport between frames and motor control tolerates
  lag far better than the recording tolerates a variable rate.
- A "latest frame" hand-off between acquisition and both the display endpoints and the
  tracker becomes a shared-state surface that must be made thread-safe.
- Motor absence at startup must be handled so the server still runs for UI/dev work.
