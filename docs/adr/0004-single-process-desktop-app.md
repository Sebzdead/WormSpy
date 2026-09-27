# 4. Single-process desktop app (PySide6 + pyqtgraph), no HTTP layer

Date: 2026-09-27

## Status

Accepted

## Context

v1 is a Flask server plus a committed Angular 13 build. Acquisition ran inside MJPEG HTTP
generators, and the browser UI flipped its own state optimistically on every click. This caused
several problems:

- frame rates were set by how fast the browser pulled frames
- motors only existed while a browser tab was open
- the UI disagreed with the hardware
- the frontend needed two languages and a manual copy-the-build step to change

The unfinished `PyQT` branch wrapped the same Flask routes in a Qt window, called them through
`test_client()`, and opened the cameras a second time. It never worked. WormsPy has one operator
on one rig PC, so the HTTP layer adds cost and gives nothing back.

## Decision

- WormsPy becomes **one Python process**: a PySide6 desktop application with an in-process core.
  There is no web server and no browser.
- Live views use **pyqtgraph** (`ImageView` / `HistogramLUTItem`) to display 16-bit frames with a
  histogram and operator-set fixed levels, LUTs (including the former "heatmap"), tracking and
  threshold overlays, and status readouts (XYZ, fps, frames written, drops, free disk space,
  recording timer). Display processing never touches recorded data.
- The core runs in worker threads: acquisition, writers, tracker, stage poller and gamepad. The UI
  talks to the core only by issuing **Commands** and rendering **state** (ADR 0010).
- The `PyQT` branch is abandoned. The Flask routes, the Angular app and its committed build, Docker
  and the socket.io stubs are removed when the rework lands. The old code is preserved under a
  `v1-legacy` tag, because the paper cites this repository.

## Consequences

- There is one language and one process. There is no MJPEG encoding cost, and no UI that can
  drift out of sync with the hardware.
- Remote viewing from another machine is gone. It was never a requirement.
- Qt needs its own threading discipline: frames reach the UI by signals or queues, never by
  calling widgets from worker threads.
