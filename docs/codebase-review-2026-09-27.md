# WormsPy codebase review: findings for the rework

*2026-09-27. Covers `master` (8df172c; `origin/master` is one commit ahead with only an STL upload),
the `PyQT` branch, and `refactor/acquisition-core-adr0001`. Every source file on all three was read
in full. Line references point at `master` unless marked otherwise.*

---

## 1. The short version

The software does work, but the problems are worse than messy code. Several defects put the
**scientific data** at risk without any warning, and a few are **hardware-safety** issues. Fix those
first. Style and architecture come after.

**Top 10, in priority order:**

| # | Finding | Where | Why it matters |
|---|---|---|---|
| 1 | By default the fluorescence camera records as **per-frame min–max normalised, 8-bit, XVID-compressed AVI** | `app.py:256-263,281,293`; UI default `useAviRight = 1` (`live-feed.component.ts:90`) | Intensities can't be compared across frames, so ΔF/F is meaningless. The README says the right camera records 16-bit TIFF, but that only happens if the operator changes the toggle. |
| 2 | Basler path asks for Mono16 but the converter outputs Mono8 | `app.py:764,809-810` | The Basler cameras never deliver 16-bit data. |
| 3 | Stage soft-limit checks are joined with `or`, so they are **always true** | `app.py:567-571` (tracking), `730-744` (joystick, including **Z**) | There are no software limits at all. The joystick can drive Z, i.e. the objective, without bounds. |
| 4 | When no worm blob is found, the "centre" fallback gets x/y-swapped and multiplied by the downsample factor again | `app.py:603-613` with `initial_coords` from `110` | Every frame without a blob commands a move toward (1200, 1920) px. Losing the worm makes the stage lurch. |
| 5 | The two cameras run in **independent loops** with their own wall clocks. Nothing links a right-camera frame to a left-camera frame or a CSV row. | `video_feed` / `video_feed_fluorescent` | Frame pairing is unreliable and drifts (ADR 0001 already identifies this). |
| 6 | The AVI frame rate comes from a hand-typed `FPS = 10`, not from the camera | `app.py:48,190,281` | Playback timing is wrong whenever the camera isn't running at exactly 10 fps. |
| 7 | Stopping a recording with controller button **B** never sets `stop_recording` | `app.py:717-723` | The AVI is never finalised, the CSV is never written, and the right camera keeps recording. |
| 8 | Left and right recordings each build their folder name to one-minute resolution, and both use `mkdir(exist_ok=False)` | `app.py:181-189` vs `273-280` | If Start is pressed near a minute boundary, the two cameras write to **different folders**, or one thread crashes on a race. |
| 9 | The CSV only exists in memory (`np.append`, O(n²)) until Stop | `app.py:199-218` | A crash or disconnect loses **all** position telemetry for the session. |
| 10 | After Stop Live Feed, a restart doesn't work: `stop_stream` is never reset and cameras are never released | `app.py:115-116,248-249,400` | The operator has to restart the server. |

**On the two unfinished branches:**
- **`PyQT`:** abandon it. It fails at import time and its video threads bypass the backend entirely (§4.1).
- **`refactor/acquisition-core-adr0001`:** keep the ADRs and the `core/` interfaces; they point in the right direction. Before building Phases 2–4 on top, fix these flaws (§4.2):
  - 8-bit normalisation inside the camera adapter
  - a fake "device frame id"
  - a Tracker that re-applies relative moves to stale frames
  - an engine that can't be stopped or restarted

---

## 2. Inventory

| Area | Files | Notes |
|---|---|---|
| Backend | `WormSpy/backend/code/app.py` (874 lines) | Everything lives in one module: Flask routes, camera loops, tracking, recording, joystick, the autofocus PID and a Basler wrapper. 30+ mutable globals. |
| Frontend | Angular 13 app in `WormSpy/wormspy/` (one component, ~300 lines of TS) | The compiled bundle is committed under `backend/code/production/`. |
| Helpers | `plot_worm_path.py`, `test_segmentation.py` (a manual camera script, not a test) | |
| Analysis | `analysis_code/Segmentation.py`, `preprocess_tiff.py`, `tifstacker.py` | Standalone scripts with hard-coded `D:\` paths. |
| Infra | `Dockerfile`, `docker-compose.yml`, `StartWormSpy.bat`, two `requirements.txt`, a Jekyll Pages workflow | Docker can't reach USB3 cameras or COM ports and uses Windows-only paths. It's dead weight. |
| Committed junk | `__pycache__/*.pyc` (tracked even though `.gitignore` lists them), the build output, and a UTF-16 encoded `backend/code/requirements.txt` | |

---

## 3. Findings on `master`

### 3.1 Data integrity (highest priority)

- **The fluorescence channel loses quantitative meaning when recorded as AVI.** The frame is
  normalised with `cv2.normalize(..., NORM_MINMAX)` *per frame*, then written through XVID, which is
  lossy and inter-frame (`app.py:256-263,293`). The UI defaults to AVI for both cameras
  (`live-feed.component.ts:89-90`). The PyQt branch has the same defaults. At minimum, the rework
  should make it impossible to record calcium as anything other than raw 16-bit.
- **The brightfield AVI is also per-frame normalised when the camera delivers >8-bit data** (`148-149,205`).
  XVID's inter-frame compression means behaviour scoring reads interpolated content. ADR 0003 already covers this.
- **Timing:**
  - Every timestamp is wall-clock `datetime.now(US/Eastern)` taken *after* the grab and after the serial position queries (`201`).
  - No camera timestamps or frame IDs are recorded.
  - The time zone is hard-coded (`49`).
- **TIFF file names are `frame_HH-MM-SS.ffffff.tiff`** (`91`). Ordering breaks across midnight, and there is
  no frame index. `tifstacker.py` sorts on the *first* number in the name, which is the **hour**, so
  it only produces the right order because the sort is stable and NTFS happens to return files alphabetically.
- **The CSV logs position in µm from a blocking serial read.** The read happens before tracking moves the
  stage, so the logged position is from before this frame's move and isn't time-aligned with the exposure.
- **`plot_worm_path` runs synchronously inside the acquisition generator on Stop** (`219`). It uses the
  default (possibly Tk) matplotlib backend off the main thread and never closes the figure. It also
  hard-codes 0–50 800 µm axis limits.

### 3.2 Hardware safety

- **Limits are a no-op** (`567-571`, `730-744`). Because of `A < max or A > min`, every position passes.
  There is also a unit mismatch: `xPos` is read in **µm** (`155`) and compared with a limit in **native microsteps** (`40`).
  The Z joystick path is unbounded. The rework needs a configurable **Z soft limit**, because
  Zaber's device travel limit is not a safe objective-to-sample distance.
- **Unsynchronised motor access.** The tracking loop skips its move if `mutex.locked()`, which is a race
  (`172`). Autofocus and the joystick both command Z with no lock at all.
- **A lost worm makes the stage lurch** (finding 4 above). The Fluorescent mode's Yen threshold always
  finds *something*, even in pure noise, so it never takes the fallback path. It just chases noise instead.

### 3.3 Correctness bugs

| Location | Bug |
|---|---|
| `DLC_tracking` (`615-624`, called at `167`) | `firstIt` is passed by value and never cleared, so **`init_inference` runs every frame**. `downsample_size = (height, width)` is passed to `cv2.resize`, which expects `(w, h)`, so the image is transposed. `dlc_live = None` by default, so selecting DeepLabCut raises inside the generator, which **kills the left feed and the Zaber connection**. |
| `start_controller` (`656-749`) | Runs **synchronously inside the HTTP request**, so the POST never returns. With no joystick, `x_data` and `joystick` are undefined, causing a NameError on the first loop. If there are fewer than 2 axes, `sticks_good` is unset. The `except KeyboardInterrupt` branch calls `mutex.release()` on an unheld lock. |
| Controller button A (`713-716`) | Flips `af_enabled` but never starts the thread, so the UI and backend disagree. |
| `toggle_af` / `start_autofocus` | `latest_frame` isn't defined at module level, so this raises a NameError unless the right feed has already produced a frame. The PID design is fundamentally wrong (ADR 0002 already covers this). |
| `move_to_center`, manual mode, autofocus | Depend on `xMotor`/`zMotor` globals that only exist while the left feed's generator is alive. |
| `/camera_settings` (`404-411`) | `serialInput` ("COM4" in the UI) is accepted and ignored. The real ports are hard-coded (`34-35`). |
| Camera selection | Uses enumeration index ("Camera 1" → 0). The index isn't stable across reboots or USB re-plugs; use the serial number. |
| Heatmap | Applied *before* `hist_frame` and `latest_frame` are captured (`264-268`). With the heatmap on, the histogram shows the JET blue channel and autofocus measures the colour-mapped image. |
| `/get_hist` (`316-348`) | A `while True` loop with **no sleep**. It pins a CPU core re-rendering and PNG-encoding the same frame. It also histograms the per-frame-*normalised* 8-bit display image, so it can't be used to set exposure. |
| `hist_max` | The UI fetches `/stream_max` **once** at stream start (`live-feed.component.ts:118,134-140`), so the value never updates. |
| MJPEG | Frames are PNG-encoded (`228,305`) but labelled `image/jpeg`. PNG at 1920×1200 is slow and burns CPU on every frame, on the same thread that does acquisition. |
| `Flask(template_folder='production\\templates')` (`24-26`) | Backslash paths only work on Windows. |

### 3.4 Architectural root causes

1. **Acquisition happens inside HTTP generators.** Frame rate is set by how fast the browser pulls
   MJPEG. The motor connection only lives as long as the left `<img>` tag. Recording, tracking,
   display encoding and serial I/O are serialised in one loop per camera. ADR 0001 describes this correctly.
2. **Control flow is one-shot boolean flags** (`start_recording`, `stop_recording`, `*_r`, …) that two
   threads set and clear without synchronisation. This design produced the B-button and folder-race bugs.
3. **The UI state is optimistic.** Every toggle flips locally and fire-and-forgets a POST. Errors are
   ignored, and joystick actions never reach the UI. The UI regularly misrepresents what the
   hardware is doing.
4. **The control law runs a stage step on every frame.** `move rel (error/5)` fires on every frame, and
   two serial position queries block acquisition first (`155-156`). There is no latency model, deadband
   or velocity control, so motion is jerky, which shows up as blur and z-wobble in the calcium channel.
5. **Configuration means editing source.** Ports, FOV size in mm, orientation, FPS, DLC model path and
   frame-size constants are all edited in `app.py`. FOV size is measured by hand; nothing calibrates it.
6. **There is no hardware abstraction.** Switching to Basler means editing two call sites. The Basler
   wrapper exists but isn't selectable.

### 3.5 Dependencies and tooling

- **Everything is past end-of-life:** Python 3.8 (EOL Oct 2024), Angular 13 (EOL 2023), TensorFlow 2.10 pinned for DLC-Live 1.0.4.
- **The root `requirements.txt` is a 141-line `pip freeze`.**
  - It includes IDE and Jupyter packages, `pylint 2.4`, `virtualenv`, and Windows-only `pywin32` and `zaber-motion-bindings-windows`.
  - `spinnaker-python` is **not installable from PyPI** because it ships with the Spinnaker SDK.
  - `pypylon` is **missing** even though `app.py` imports it unconditionally.
- **The duplicate `backend/code/requirements.txt` is UTF-16.**
- **`Flask-SocketIO`, `gevent` and `socket.io-client` are installed but unused.** `socket.service.ts` is a commented-out stub.
- **There are no tests on master.** `test_segmentation.py` is an interactive camera viewer.
- **The README contradicts the code in several places:**
  - It says the brightfield codec is MJPG; the code uses XVID.
  - It says the right camera records 16-bit TIFF; the default is AVI.
  - It says to uncomment "lines #87-92"; those lines are gone.
  - It says Zaber Launcher must be open, while the `.bat` says serial ports must be *closed*.

---

## 4. The two unfinished branches

### 4.1 `PyQT`: recommend abandoning

The branch is based on `096b25a`. It adds `wormspy_pyqt.py` (618 lines) and launcher scripts. The only other
change, the 4-camera dropdown, has already been cherry-picked to master. Problems:

- **It won't start.** It imports `toggle_autofocus` from `app`, but the route function is named
  `toggle_af`. That's an `ImportError`.
- **It calls the Flask routes through `app.test_client()`** from the Qt thread, so an in-process
  function call goes through a fake HTTP round-trip.
- **The video threads ignore the backend.** `VideoThread.run` opens `cv2.VideoCapture(0)` and `(1)`
  directly. That's plain OpenCV, not Spinnaker, and it opens the *same* index for the histogram as for
  the right feed. Because `/video_feed` is never consumed, **the backend generator never runs**. That means:
  - no Zaber connection
  - no tracking
  - no recording, even though the Record button posts the flags
- **It reads `heatmap_enabled` through `flask_func.__globals__`.**
- **Display bugs:** OpenCV frames are BGR but are rendered as `Format_RGB888`, so red and blue are swapped. The histogram
  thread emits an `ndarray` into a slot that expects a `QImage`. The `CameraThread` class is unused.
- **The "Browse…" directory picker** is the only thing worth keeping, as an idea.

It confirms the desktop-UI instinct but doesn't provide a base to build from.

### 4.2 `refactor/acquisition-core-adr0001`: keep the design, fix the core before continuing

**What's good:**
- `CONTEXT.md` and ADRs 0001–0003 are sound. They pin down the right non-negotiable: the calcium
  channel needs a fixed, stable rate and exposure, and tracking is a best-effort consumer. The
  "Hold Focus, not autofocus" reasoning (one shared Z, neuron depth varies by session) is physically correct.
- The `Camera` / `Stage` interfaces, `FakeCamera` / `FakeStage` / `NullStage`, `FrameHub`, and
  `connect_stage` with graceful fallback are clean and testable. **All 38 tests pass**
  (checked today; run them with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, because a napari/numba plugin in the
  base env breaks collection).

**What must change before building Phases 2–4 on top:**

1. **`SpinnakerCamera.read()` normalises to uint8 and converts to grey.** This is the same
   per-frame `NORM_MINMAX` as master. Anything downstream, including the Phase 2 Recorder, only ever sees
   8-bit, per-frame-stretched data, which directly contradicts ADR 0003's "16-bit TIFF". `Frame` should carry
   the **raw** array. Display conversion (with *fixed* contrast levels) should happen in the display path.
2. **`device_frame_id = CAP_PROP_POS_FRAMES` is not the camera's FrameID.** It's a software counter, or
   unsupported, in EasyPySpin. Drop detection, the main reason for logging IDs, doesn't work. Use PySpin
   chunk data (`ChunkModeActive`, `FrameID`, `Timestamp`) directly and drop EasyPySpin.
3. **Pairing by tick isn't temporal sync.** The engine reads left then right from two free-running
   cameras. If either camera drops or buffers one extra frame, every later pair is offset by one frame
   (100 ms at 10 fps), and the index pairing hides it. Real device IDs and timestamps would at least make
   this detectable. **Hardware-trigger the secondary camera from the primary's exposure-active GPIO line.**
   It costs one cable, makes pairing exact by construction, and is the proper fix for "loop coherence".
4. **The Tracker re-acts on stale frames.** `step()` doesn't check whether the frame is new. With
   `interval_s=0` it busy-spins and sends the *same* relative correction repeatedly until the next frame
   arrives, which means overshoot and oscillation. Each `move_relative` also does a blocking serial
   `get_position`. Fix:
   - Process each `frame_index` once.
   - Compute an **absolute** target: stage position at capture time plus the pixel error converted to stage units.
   Absolute targets make the lag ADR 0001 accepts harmless, because a late correction still aims at the right place.
5. **The engine lifecycle is missing:**
   - A second `/camera_settings` call starts a *second* engine on the same cameras.
   - `/stop_live_stream` doesn't stop the engine.
   - `run()` has no exception handling, so a read error silently kills acquisition.
   - If a camera fails to open, `__init__` returns early without `width`/`height`, and the engine exits immediately with no error in the UI.
6. **Rate and exposure are silently overridden.** `_start_engine` hard-codes `exposure_us=10000` and `FPS`
   for *both* cameras. That overrides whatever the operator tuned in SpinView (README step 12). These need to be explicit
   per-camera settings visible in the UI. Also check whether EasyPySpin's `CAP_PROP_AUTO_EXPOSURE = 0` actually
   disables auto-exposure. Going straight to PySpin removes that doubt.
7. **The branch isn't shippable.** By design, Phase 1 leaves recording as a no-op, manual/Hold Focus returning
   HTTP 500, and tracking unwired. The old helper functions (`trackWorm`, `start_controller`, the PID) are
   still there as dead code. About 3,400 lines of plan documents accompany about 500 lines of code.
   If the rework goes ahead, fold this branch's *decisions* into it instead of finishing all four phases on the old Flask/Angular shell.

---

## 5. Analysis code

- **`Segmentation.py`:**
  - The centroid-jump gate is dead code: `prev_centroid` is only updated inside `if prev_centroid:`, so it stays `None` forever.
  - `frames` is a global that's only defined under `__main__`, so the module can't be imported.
  - If no contour is found, the mask is empty and the mean of an empty array is NaN.
  - The signal is "sum/mean of the brightest 25 px within r=10", with no background subtraction or F₀. That biases the trace upward in low-SNR frames.
  - It uses hard-coded paths and globals for its parameters.
- **`preprocess_tiff.py`:**
  - Every frame is cropped around the brightest object independently, with no temporal continuity, so it can jump to gut autofluorescence.
  - It contains a duplicated line (`binary = …` twice).
- **`tifstacker.py`:**
  - Its sort key is the hour (see §3.1).
  - It loads the whole stack into RAM.
  - The script body runs at import.
- **Overall:** after the rework, analysis should join on `frame_index` from the session spine (ADR 0003). The scripts
  should become an importable package with a CLI, not scripts you edit before running.

---

## 6. Recommendations for the rework

### 6.1 Target architecture (recommendation)

**Build a single-process Python desktop app with no HTTP layer.** It's one operator on one rig PC. The web
stack costs the most (two languages, a committed build, Angular upgrades, MJPEG encoding, optimistic UI
state) and adds nothing here. Recommended stack:

- **PySide6 + pyqtgraph for the UI.** `ImageView` / `HistogramLUTItem` display 16-bit frames with a
  live histogram and *fixed, operator-set* contrast levels. That replaces `/get_hist`, `/stream_max` and the
  heatmap toggle with a standard, fast widget.
- **A hardware layer behind the existing `Camera` / `Stage` interfaces.** Before writing more adapters
  by hand, **spend a day evaluating `pymmcore-plus`** (Micro-Manager core in Python). It already has
  Spinnaker, Basler and Zaber device adapters, multi-camera support and `pymmcore-widgets`, which would
  make vendor switching a matter of configuration. If its tracking latency or Zaber semantics don't fit,
  keep the custom adapters but write them on **PySpin** and **pypylon** directly.
- **Threads, each with a single owner:**
  - **Acquisition:** hardware-triggered pair, raw frames, device ID and timestamp.
  - **Writer:** a bounded queue that alarms loudly on backpressure. It must never drop frames silently.
  - **Tracker:** processes each frame index once, sends absolute targets.
  - **Stage poller:** fixed-rate timestamped positions, interpolated onto frame timestamps.
  - **Joystick:** routed through the same `Stage`, so limits and locking live in one place.
- **An explicit state machine** (Idle → Live → Recording, with Tracking and HoldFocus as orthogonal
  flags) owned by the backend. The UI only renders it. Controller buttons and UI buttons send the
  *same* commands, which removes the whole "UI lies" bug class.

### 6.2 Specific design decisions to make

1. **Sync:** use the GPIO hardware trigger (primary exposure-active output to the secondary's trigger
   input) and keep device timestamps as a cross-check.
2. **Storage:**
   - **Fluorescence:** raw uint16 written to a *single* container per session. Options are appendable BigTIFF (`tifffile`), OME-Zarr, or HDF5. Don't write one file per frame.
   - **Brightfield:** lossless (FFV1) or MJPG.
   - **Per-frame spine:** CSV or Parquet (ADR 0003), written **incrementally** as frames arrive.
   - **`session.json`:** full config, exposure, gain, rate, calibration, software version and git hash.
3. **Calibration instead of hand-measured `TOTAL_MM_X/Y` and orientation signs.** Move the stage a known
   distance, measure the image shift by phase correlation, and derive the px→µm scale plus the
   rotation/sign matrix. Store the result in config.
4. **Control law:** absolute targets, a deadband, and a velocity limit. Consider Zaber velocity
   mode (`move vel`) for smooth continuous tracking instead of per-frame step moves. Smoother
   motion means less blur and less z-coupling into the calcium trace.
5. **Safety:**
   - Enforce XY and Z soft limits in `Stage` itself, in native units, as the refactor branch already started.
   - Add a Z "do not go below" limit set per session.
   - Stop the stage when tracking loses the target, instead of chasing a fallback coordinate.
6. **Config:** move ports, camera serial numbers, calibration and paths into a TOML/YAML file with a
   settings dialog. Keep no constants in code.
7. **DLC:** make it an optional extra loaded from config. Check the current `dlclive` release;
   DeepLabCut 3 moved to PyTorch, so the TF 2.10 pin can probably go.

### 6.3 Keep, drop, and fix-first

- **Keep:**
  - the ADRs and `CONTEXT.md`
  - the `core/` interfaces, fakes and tests
  - Hold Focus
  - the threshold tracking algorithms (after fixing the fallback bug)
  - the Basler identifier-parsing logic
- **Drop:**
  - Flask routes, the Angular app and committed build, Docker, socket.io
  - the PID autofocus
  - the `PyQT` branch
  - the frozen `requirements.txt` files, committed `.pyc` files, and the Jekyll workflow (unless the Pages site is actually used)
- **If the current rig must keep running during the rework, fix these on `master` now:** findings 1, 3, 4 and 7
  from §1. Each is only a few lines:
  - default the right camera to TIFF
  - replace `or` with `and` and use consistent units
  - return early with no move when no blob is found
  - set `stop_recording` flags from button B

### 6.4 Suggested sequencing

1. **Packaging and simulation.** Set up `pyproject.toml` (Python 3.12, `uv` or a conda env file), a CI job
   running pytest, and a **simulated rig**. In the simulated rig, `FakeStage` motion shifts a synthetic
   worm image in `FakeCamera`, so tracking can be tested in closed loop without hardware.
2. **Acquisition and recording.** Build on real PySpin/pypylon adapters (or pymmcore-plus) with a
   hardware trigger, raw frames, the incremental spine, and `session.json`. Validate on the rig: record 10 minutes and confirm
   zero ID gaps and paired timestamps within tolerance.
3. **Stage.** Add the limits, the poller, calibration, the joystick, and Hold Focus.
4. **Tracking.** Implement absolute targets, the frame-index dedupe and a lost-target stop. Tune it in simulation, then on the rig.
5. **UI.** Build the PySide6 shell on top of the state machine.
6. **Analysis.** Turn the scripts into a package that reads the new session format.

Items 2–4 need manual verification on the rig. Everything else can be developed and tested without hardware.
