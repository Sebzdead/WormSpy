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

## Consequences

- The UI can't misrepresent the hardware, and every control path is the same code path.
- The state machine is pure logic, so it can be unit-tested without Qt or hardware.
