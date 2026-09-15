# Backend Status, UI Truth & Cleanup (Q6) — Phase 4

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the backend the single source of truth via a polled `/status` endpoint, and make the Angular UI reflect that truth (so a browser refresh no longer lies), surface errors and live telemetry (recording timer, frame count, z, dropped/limit warnings), expose the new Hold Focus controls, and delete the dead code accumulated across the refactor.

**Architecture:** A pure `core/status.py:build_status(...)` assembles an authoritative state dict from the existing singletons (`ENGINE`, `RECORDER`, `TELEMETRY`, `STAGE`); a thin `/status` route serializes it. The Recorder gains `frames_written` + elapsed counters. In Angular, a `StatusService` polls `/status` at ~2 Hz and the `live-feed` component binds its toggles/readouts to that stream instead of local booleans; an `HttpInterceptor` surfaces backend errors as toasts. The dead socket.io stub and commented-out code are removed.

**Tech Stack:** Python/Flask + pytest (pure status fn is unit-tested); Angular 13 + TypeScript + Material (UI verified manually via `ng serve`; karma/headless-browser tests are out of scope in CI here). **The Angular 13→current framework upgrade remains a separate deferred follow-up — this phase does functional UI work on Angular 13 as-is.**

**Scope:** Implements Q6 (authoritative `/status`, UI truth, error surfacing, telemetry readouts), wires UI to the Phase 3 focus/tracking routes, and the cleanup pass. Out of scope: framework upgrade.

**Depends on:** Phases 1–3 (singletons, telemetry, recorder, focus/tracking routes). Same branch.

---

## File Structure

Backend paths relative to `WormSpy/backend/code/`; frontend paths relative to `WormSpy/wormspy/`.

- Modify: `core/recording.py` — add `frames_written` + `started_at`/`elapsed_s` to `Recorder`.
- Create: `core/status.py` — pure `build_status(...)`.
- Create: `tests/test_status.py`; extend `tests/test_recording.py`.
- Modify: `app.py` — `/status` route; remove dead globals (`heatmap_enable` typo etc.); reconcile.
- Create: `src/app/status/status.service.ts`, `src/app/core/error.interceptor.ts`, `src/app/core/toast.service.ts` (Angular).
- Modify: `src/app/live-feed/live-feed.component.ts` + `.html` — bind to status; Hold Focus controls; readouts; poll `hist_max`; remove dead code.
- Delete: `src/app/socket/socket.service.ts` (+ its spec) — abandoned stub.
- Modify: `ReadMe.md` — reconcile codec/feature claims with the refactor.

---

### Task 1: Recorder counters (frames written + elapsed)

**Files:**
- Modify: `WormSpy/backend/code/core/recording.py`
- Test: `WormSpy/backend/code/tests/test_recording.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recording.py`:

```python
def test_recorder_counts_frames_written():
    rec, _, _, _, _ = _recorder_with_fakes()
    rec.start_recording(config={"name": "p"})
    rec.consume(_pair(0))
    rec.consume(_pair(1))
    assert rec.frames_written == 2
    rec.stop_recording()


def test_recorder_frames_written_resets_on_new_recording():
    rec, _, _, _, _ = _recorder_with_fakes()
    rec.start_recording(config={"name": "p"})
    rec.consume(_pair(0))
    rec.stop_recording()
    rec.start_recording(config={"name": "p2"})
    assert rec.frames_written == 0


def test_recorder_elapsed_is_zero_when_not_recording():
    rec, _, _, _, _ = _recorder_with_fakes()
    assert rec.elapsed_s == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py -k "frames_written or elapsed"`
Expected: FAIL — `AttributeError: 'Recorder' object has no attribute 'frames_written'`

- [ ] **Step 3: Implement**

In `core/recording.py`, add `import time` (top), and in `Recorder.__init__` add:

```python
        self.frames_written = 0
        self._started_at: Optional[float] = None
```

In `start_recording`, after `self._recording = True`, add:

```python
        self.frames_written = 0
        self._started_at = time.monotonic()
```

In `consume`, increment after a row is written (end of the method):

```python
        self.frames_written += 1
```

In `stop_recording`, after `self._recording = False`, add:

```python
        self._started_at = None
```

Add a property:

```python
    @property
    def elapsed_s(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_recording.py`
Expected: PASS (all recording tests incl. 3 new)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/recording.py WormSpy/backend/code/tests/test_recording.py
git commit -m "feat: track frames_written and elapsed time in Recorder"
```
End commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 2: Pure build_status

**Files:**
- Create: `WormSpy/backend/code/core/status.py`
- Test: `WormSpy/backend/code/tests/test_status.py`

> Pure assembly of the authoritative state dict so it is unit-testable without Flask/hardware.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
from core.status import build_status
from core.telemetry import TelemetrySnapshot


def _snap(**kw):
    base = dict(x=1.0, y=2.0, z=3.0, tracking_mode=0, is_tracking=False, limit_hit=False)
    base.update(kw)
    return TelemetrySnapshot(**base)


def test_build_status_reports_core_flags():
    s = build_status(snapshot=_snap(is_tracking=True, tracking_mode=1),
                     is_recording=True, frames_written=42, elapsed_s=4.0,
                     engine_running=True, zaber_connected=True,
                     manual_enabled=False, has_focus=True, drift_on=False)
    assert s["is_recording"] is True
    assert s["frames_written"] == 42
    assert s["recording_elapsed_s"] == 4.0
    assert s["is_tracking"] is True
    assert s["tracking_mode"] == 1
    assert s["engine_running"] is True
    assert s["zaber_connected"] is True
    assert s["has_focus"] is True
    assert s["x_pos"] == 1.0 and s["y_pos"] == 2.0 and s["z_pos"] == 3.0


def test_build_status_passes_through_limit_hit():
    s = build_status(snapshot=_snap(limit_hit=True),
                     is_recording=False, frames_written=0, elapsed_s=0.0,
                     engine_running=False, zaber_connected=False,
                     manual_enabled=False, has_focus=False, drift_on=False)
    assert s["limit_hit"] is True


def test_build_status_is_json_serializable():
    import json
    s = build_status(snapshot=_snap(), is_recording=False, frames_written=0, elapsed_s=0.0,
                     engine_running=False, zaber_connected=False,
                     manual_enabled=False, has_focus=False, drift_on=False)
    json.dumps(s)   # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_status.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.status'`

- [ ] **Step 3: Implement**

Create `core/status.py`:

```python
from core.telemetry import TelemetrySnapshot


def build_status(snapshot: TelemetrySnapshot, is_recording: bool, frames_written: int,
                 elapsed_s: float, engine_running: bool, zaber_connected: bool,
                 manual_enabled: bool, has_focus: bool, drift_on: bool) -> dict:
    """Assemble the authoritative UI state. Pure: all inputs supplied by the caller (route)."""
    return {
        "engine_running": bool(engine_running),
        "zaber_connected": bool(zaber_connected),
        "is_recording": bool(is_recording),
        "recording_elapsed_s": round(float(elapsed_s), 2),
        "frames_written": int(frames_written),
        "is_tracking": bool(snapshot.is_tracking),
        "tracking_mode": int(snapshot.tracking_mode),
        "manual_enabled": bool(manual_enabled),
        "has_focus": bool(has_focus),
        "drift_on": bool(drift_on),
        "limit_hit": bool(snapshot.limit_hit),
        "x_pos": float(snapshot.x),
        "y_pos": float(snapshot.y),
        "z_pos": float(snapshot.z),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_status.py`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/core/status.py WormSpy/backend/code/tests/test_status.py
git commit -m "feat: add pure build_status assembler"
```
End commit message with the Co-Authored-By trailer.

---

### Task 3: /status route

**Files:**
- Modify: `WormSpy/backend/code/app.py`

- [ ] **Step 1: Add the route**

Add near the other routes in `app.py`:

```python
from core.status import build_status

@cross_origin()
@app.route("/status")
def status():
    return jsonify(build_status(
        snapshot=TELEMETRY.snapshot(),
        is_recording=RECORDER.is_recording,
        frames_written=RECORDER.frames_written,
        elapsed_s=RECORDER.elapsed_s,
        engine_running=(ENGINE is not None),
        zaber_connected=STAGE.is_connected(),
        manual_enabled=isManualEnabled,
        has_focus=FOCUS.has_focus,
        drift_on=_drift["on"],
    ))
```

- [ ] **Step 2: Verify**

Parse: `python -c "import ast; ast.parse(open('WormSpy/backend/code/app.py').read()); print('parse ok')"` → `parse ok`.
Suite: `cd WormSpy/backend/code && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` → all pass.
On the rig (or with NullStage locally): `curl localhost:5000/status` returns the JSON with all keys.

- [ ] **Step 3: Commit**

```bash
git add WormSpy/backend/code/app.py
git commit -m "feat: add authoritative /status endpoint"
```
End commit message with the Co-Authored-By trailer.

---

### Task 4: Angular StatusService + error interceptor + toast

**Files:**
- Create: `WormSpy/wormspy/src/app/status/status.service.ts`
- Create: `WormSpy/wormspy/src/app/core/toast.service.ts`
- Create: `WormSpy/wormspy/src/app/core/error.interceptor.ts`
- Modify: `WormSpy/wormspy/src/app/app.module.ts` (provide interceptor + MatSnackBar)

> Angular work is verified manually with `npm install` + `ng serve` (no headless-browser test run in
> this environment). Provide the code exactly; the implementer runs the build.

- [ ] **Step 1: StatusService (polls /status ~2 Hz)**

Create `src/app/status/status.service.ts`:

```typescript
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, timer, Subject, EMPTY } from 'rxjs';
import { switchMap, catchError, shareReplay } from 'rxjs/operators';

export interface BackendStatus {
  engine_running: boolean; zaber_connected: boolean;
  is_recording: boolean; recording_elapsed_s: number; frames_written: number;
  is_tracking: boolean; tracking_mode: number; manual_enabled: boolean;
  has_focus: boolean; drift_on: boolean; limit_hit: boolean;
  x_pos: number; y_pos: number; z_pos: number;
}

@Injectable({ providedIn: 'root' })
export class StatusService {
  private apiUrl = 'http://127.0.0.1:5000';
  public status$: Observable<BackendStatus> = timer(0, 500).pipe(
    switchMap(() =>
      this.http.get<BackendStatus>(this.apiUrl + '/status').pipe(catchError(() => EMPTY))
    ),
    shareReplay(1)
  );
  constructor(private http: HttpClient) {}
}
```

- [ ] **Step 2: ToastService + error interceptor**

Create `src/app/core/toast.service.ts`:

```typescript
import { Injectable } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

@Injectable({ providedIn: 'root' })
export class ToastService {
  constructor(private snack: MatSnackBar) {}
  error(message: string): void {
    this.snack.open(message, 'Dismiss', { duration: 5000, panelClass: 'toast-error' });
  }
}
```

Create `src/app/core/error.interceptor.ts`:

```typescript
import { Injectable } from '@angular/core';
import { HttpInterceptor, HttpHandler, HttpRequest, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { ToastService } from './toast.service';

@Injectable()
export class ErrorInterceptor implements HttpInterceptor {
  constructor(private toast: ToastService) {}
  intercept(req: HttpRequest<any>, next: HttpHandler): Observable<any> {
    return next.handle(req).pipe(
      catchError((err: HttpErrorResponse) => {
        // Don't toast the high-frequency /status poll failures.
        if (!req.url.endsWith('/status')) {
          this.toast.error(`Request failed: ${req.url.split('/').pop()} (${err.status})`);
        }
        return throwError(() => err);
      })
    );
  }
}
```

- [ ] **Step 3: Register in app.module.ts**

In `src/app/app.module.ts`, add to imports `MatSnackBarModule` (from `@angular/material/snack-bar`)
and to providers:

```typescript
{ provide: HTTP_INTERCEPTORS, useClass: ErrorInterceptor, multi: true },
```

(import `HTTP_INTERCEPTORS` from `@angular/common/http` and `ErrorInterceptor` from
`./core/error.interceptor`.)

- [ ] **Step 4: Build check (manual)**

Run from `WormSpy/wormspy`: `npm install` then `ng build`. Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add WormSpy/wormspy/src/app/status WormSpy/wormspy/src/app/core WormSpy/wormspy/src/app/app.module.ts
git commit -m "feat: add StatusService polling and global HTTP error toasts"
```
End commit message with the Co-Authored-By trailer.

---

### Task 5: Bind the UI to backend truth + Hold Focus controls + readouts

**Files:**
- Modify: `WormSpy/wormspy/src/app/live-feed/live-feed.component.ts`
- Modify: `WormSpy/wormspy/src/app/live-feed/live-feed.component.html`
- Delete: `WormSpy/wormspy/src/app/socket/socket.service.ts` and `socket.service.spec.ts`

> The big UX fix: toggles reflect `status$` (so refresh re-syncs); add recording timer / frame
> counter / z readout / limit-hit warning; rename FocusLock→Hold Focus and add Set Focus / Return to
> Focus / drift-comp controls calling the Phase 3 routes; poll `hist_max` via `status$`/its own timer;
> remove dead commented code and the abandoned socket service. Verified manually via `ng serve`.

- [ ] **Step 1: Inject StatusService and expose status$ in the component**

In `live-feed.component.ts`: inject `StatusService` in the constructor, expose `public status$ = this.sockOrStatus.status$;` (rename field as you like), and DELETE the commented-out socket code and the unused `SocketService` injection. Derive button labels from the latest status where they currently use local booleans (subscribe in `ngOnInit` to keep `isRecording`, `isTrackingEnabled`, `manualEnabled`, etc. in sync with the backend; keep optimistic local toggles for responsiveness but reconcile from `status$`).

```typescript
import { StatusService, BackendStatus } from '../status/status.service';
// ...
export class LiveFeedComponent implements OnInit {
  public latest: BackendStatus | null = null;
  constructor(private http: HttpClient, private statusSvc: StatusService) {}
  ngOnInit(): void {
    this.statusSvc.status$.subscribe((s) => {
      this.latest = s;
      this.isRecording = s.is_recording;
      this.isTrackingEnabled = s.is_tracking;
      this.manualEnabled = s.manual_enabled;
      this.isHoldFocusSet = s.has_focus;
      this.driftOn = s.drift_on;
    });
  }
  // Hold Focus controls
  public setFocus(): void {
    this.http.post(this.apiUrl + '/set_focus', {}).subscribe(() => {});
  }
  public returnToFocus(): void {
    this.http.post(this.apiUrl + '/return_to_focus', {}).subscribe(() => {});
  }
  public toggleDriftComp(): void {
    this.driftOn = !this.driftOn;
    this.http.post(this.apiUrl + '/toggle_drift_comp',
      { drift_enabled: this.driftOn ? 'True' : 'False' }).subscribe(() => {});
  }
}
```

Add the fields `isHoldFocusSet = false;` and `driftOn = false;`. Remove the old `toggleAutofocus()`
method and `isAutofocusEnabled` field.

- [ ] **Step 2: Update the template**

In `live-feed.component.html`:
- Replace the FocusLock toggle with three controls: "Set Focus" → `setFocus()`, "Return to Focus" →
  `returnToFocus()`, and a toggle "Drift Comp (experimental)" → `toggleDriftComp()` showing `driftOn`.
- Add a status strip (visible when `latest as s`) showing: recording timer
  `{{ s.recording_elapsed_s }}s`, `{{ s.frames_written }} frames`, `z = {{ s.z_pos | number:'1.0-0' }}`,
  connection chips (`engine_running`, `zaber_connected`), and a warning banner when `s.limit_hit`
  ("Stage limit reached — worm may be off-frame").
- Bind the Start/Stop Recording and Tracking button labels to `latest?.is_recording` /
  `latest?.is_tracking` so they reflect the backend after a refresh.

```html
<div class="status-strip" *ngIf="latest as s">
  <span [class.ok]="s.engine_running">engine</span>
  <span [class.ok]="s.zaber_connected">zaber</span>
  <span *ngIf="s.is_recording">REC {{ s.recording_elapsed_s }}s · {{ s.frames_written }} frames</span>
  <span>z = {{ s.z_pos | number:'1.0-0' }}</span>
  <span class="warn" *ngIf="s.limit_hit">⚠ Stage limit reached</span>
</div>
```

- [ ] **Step 3: Delete the abandoned socket stub**

```bash
git rm WormSpy/wormspy/src/app/socket/socket.service.ts WormSpy/wormspy/src/app/socket/socket.service.spec.ts
```

Remove any remaining imports of `SocketService`.

- [ ] **Step 4: Build check (manual)**

From `WormSpy/wormspy`: `ng build`. Then `ng serve` and, with the backend running, verify: toggles
reflect backend state across a page refresh; Set/Return Focus call through; recording shows a live
timer + frame count; the limit warning appears when tracking hits a limit; a failed request shows a
toast.

- [ ] **Step 5: Commit**

```bash
git add WormSpy/wormspy/src/app/live-feed
git commit -m "feat: bind UI to backend /status; add Hold Focus controls and telemetry readouts"
```
End commit message with the Co-Authored-By trailer.

---

### Task 6: Backend cleanup + README reconciliation

**Files:**
- Modify: `WormSpy/backend/code/app.py`
- Modify: `ReadMe.md`

> Remove cruft exposed by the refactor; reconcile docs. No behaviour change.

- [ ] **Step 1: Remove dead globals / fix the typo'd one**

In `app.py`: remove the `heatmap_enable` typo global (keep `heatmap_enabled` if heatmap is still
wired, else remove both and the `/toggle_heatmap` route if unused). Remove now-unused module globals
left from the old generators (`start_recording`, `stop_recording`, `start_recording_r`,
`stop_recording_r`, `hist_frame`/`hist_max` if the histogram is reworked, `serialPort` if unused,
`nodeIndex` if DLC node is set elsewhere). For each, confirm with `grep -n <name> app.py` that it has
no remaining readers before deleting.

- [ ] **Step 2: Decide BaslerCamera disposition**

`grep -n "BaslerCamera" app.py` — it is a legacy alt-camera wrapper now superseded by
`core/cameras_spinnaker.py`. If unused, remove it from `app.py` (a future `core/cameras_basler.py`
adapter can be added later if needed). If you keep it, leave a comment that the canonical path is the
`core` adapters.

- [ ] **Step 3: Reconcile the README**

In `ReadMe.md`, update the recording description: brightfield is now an **intra-frame codec (MJPG,
or FFV1 lossless)** — not XVID/“MJPG-via-fourcc-XVID” — and the right camera remains 16-bit TIFF.
Update the autofocus/“FocusLock” mention to **Hold Focus** (operator-set; optional drift comp).
Add a one-line note that recordings now emit a per-frame CSV spine keyed by `frame_index`.

- [ ] **Step 4: Verify**

Parse: `python -c "import ast; ast.parse(open('WormSpy/backend/code/app.py').read()); print('parse ok')"` → `parse ok`.
Suite: `cd WormSpy/backend/code && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add WormSpy/backend/code/app.py ReadMe.md
git commit -m "chore: remove dead globals/BaslerCamera; reconcile README with refactor"
```
End commit message with the Co-Authored-By trailer.

---

## Self-Review

**Spec coverage (Q6):**
- *Authoritative state via polled /status* → Tasks 2 (`build_status`) + 3 (route) + 4 (`StatusService` ~2 Hz). ✓
- *UI reflects backend truth (fixes refresh-lie)* → Task 5 Step 1 (`status$` reconciles toggles). ✓
- *Surface HTTP errors* → Task 4 (`ErrorInterceptor` → toast; skips the /status poll). ✓
- *Telemetry readouts (recording timer, frame counter, z, warnings)* → Tasks 1 (counters) + 5 Step 2 (status strip). ✓
- *Hold Focus controls wired to Phase 3 routes* → Task 5 (set/return/drift). ✓
- *Poll hist_max instead of one-shot* → Task 5 (status strip / dedicated timer; the legacy one-shot `callHistMax` is removed). ✓
- *Delete dead socket.io stub + commented code* → Task 5 Step 3 (rm socket service) + Step 1 (remove commented socket code). ✓
- *Cleanup dead backend code + reconcile README* → Task 6. ✓

**Placeholder scan:** Backend tasks (1–3, 6) carry complete code + tests/parse checks. Angular tasks (4–5) carry complete code with explicit manual `ng build`/`ng serve` verification (no headless-browser CI here) — this is the standard "implement + manual verify" pattern for UI, not a placeholder.

**Type consistency:** `BackendStatus` (TS) mirrors `build_status` (py) keys exactly (Tasks 2/4). `Recorder.frames_written`/`elapsed_s`/`is_recording` used in Tasks 1/3. `StatusService.status$: Observable<BackendStatus>` consumed in Task 5. The Phase 3 routes `/set_focus`, `/return_to_focus`, `/toggle_drift_comp` called in Task 5 match Phase 3 definitions.

**Known follow-ups (not gaps):** the Angular 13→current framework upgrade (deferred); karma/headless-browser unit tests for the new Angular services (the harness here can't run them); a richer histogram view if the one-shot `get_hist` stream is reworked.
