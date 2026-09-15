# WormsPy — Context & Glossary

A glossary of the domain language used in WormsPy. Implementation details live in code and ADRs, not here.

## Glossary

### Frame-synced recording
The guarantee that a frame from the **brightfield/behaviour** camera and a frame from the
**fluorescence/calcium** camera can be reliably paired for offline analysis (e.g. correlating
RIA compartmental calcium with head-bending).

Two distinct properties are bundled under this term; keep them separate:

- **Sensor-level sync** — the two sensors expose at the same instant. Currently **not** provided
  (cameras free-run). Achievable via FLIR GPIO master–slave hardware trigger. Considered
  *desirable but optional*: **tens of ms of offset between the two streams is acceptable** for the
  current science.
- **Loop coherence (no drift)** — the pairing between the two streams must **not drift without
  bound** over a recording. This is a **hard requirement**. The current two-independent-generator
  design violates it.

### Hold Focus (formerly "FocusLock" / "autofocus")
Keeping the neuron of interest in focus on the fluorescence/calcium feed. True autofocus is
**not achievable** on this rig:
- There is **one shared z motor and one objective**, so both cameras focus at a single plane —
  you cannot independently focus brightfield and fluorescence.
- Best focus is defined by the **target neuron**, whose depth inside the worm **changes every
  session**, so there is no fixed parfocal offset to calibrate once.

Therefore focus is **set by the operator** (manually, via joystick, watching the fluorescence
feed) and software only **holds or restores** that z. An optional, opt-in **drift compensation**
mode may actively nudge z using a brightfield reference captured at focus-set time, at the cost of
a focus/intensity artifact in the recording (see [[ADR 0002]]).

### Loop drift
The accumulating divergence between the brightfield and fluorescence acquisition loops because
each runs its own independent `while cap.isOpened()` loop with its own wall-clock timestamps and
its own (possibly different, possibly dropped-frame) cadence. **Unacceptable** — see
[[frame-synced-recording]].
