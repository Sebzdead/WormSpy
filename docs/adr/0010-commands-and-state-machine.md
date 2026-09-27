# 10. One command set and one state machine for all controls

Date: 2026-09-27

## Status

Accepted

## Context

In v1, UI buttons flipped their own local state and sent a POST without checking whether it
succeeded. Gamepad buttons flipped backend globals directly and the UI never found out. The B
button started a recording but could never stop it cleanly. One-shot boolean flags shared between
threads caused races, including recordings split across two folders.

## Decision

- Every operator action is a named **Command**: toggle tracking, select estimator, start/stop
  recording, new session, mark event, set focus, return to focus, centre point, jog, and so on.
- The gamepad, keyboard shortcuts and UI buttons **all issue the same Commands**. Gamepad and
  keyboard mappings live in config.
- A single **state machine** in the core owns the application state:
  - main states: Idle → Live → Recording
  - orthogonal flags: tracking state, focus set, alarms

  It validates each command, for example refusing a preset change while recording, and
  publishes the resulting state.
- The UI **only renders published state**. It never keeps a parallel copy. Command failures show
  up as visible alarms, not silent inconsistencies.
- The gamepad XY and Z sticks are continuous inputs routed through the `Stage` interface, so they
  pass through the same safe-range clamping and override logic as tracking (ADR 0008).

## Implementation (2026-09-27)

- **Pure reducer.** `reduce(state, message) -> Transition(state, effects, rejected)` lives in
  `wormspy.control`.
  - **Commands** are operator intents and may be rejected. A rejection carries a reason for the UI.
  - **Events** are facts from the executor or workers.
  - **Effects** are requests for the executor.

  Anything whose outcome is only known after I/O takes a round trip: create the session folder, or
  read Z for Set Focus. The request goes out as an Effect and the result comes back as an Event.
- **`StateStore`** serialises dispatch under a lock and calls listeners outside it, so executors can
  report back synchronously. State deliveries are versioned, so subscribers never see an older state
  after a newer one.
- **Operator-visible rules chosen during implementation:**
  - Stop Live, New Session and Apply Preset are refused while recording.
  - Recording needs an open session.
  - The estimator can only be changed while tracking is off.
  - Event markers can only be placed while recording.
  - Stopping live, shutting down, or losing acquisition switches the tracker off first.
  - A tracker report that arrives after tracking was switched off is ignored.
  - Dropped frames raise one `frames_dropped` alarm, which is updated in place with the running
    count for the current recording.
- **Failures roll the state back.**
  - `RecordingFailed` returns the app to Live and raises an alarm.
  - `AcquisitionFailed` returns it to Idle. If a recording was open, it also closes the recording
    so the data already written is finalised.
  - A recording's number is consumed whenever the recording ends, even by failure, so a partly
    written `rec-NNN` folder is never reused.

## Consequences

- The UI can't misrepresent the hardware, and every control path is the same code path.
- The state machine is pure logic, so it can be unit-tested without Qt or hardware.

## Default gamepad mapping (2026-09-27)

The mapping is editable in `rig.toml`:
- Left stick jogs XY and the right stick jogs Z.
- **X** = toggle tracking.
- **B** = toggle recording (both unchanged from v1).
- **A** = Return to Focus.
- **Y** = Set Focus.

Buttons fire on the press edge, not while held.
