# WormsPy
![](media/Wormspy.png)
WormsPy is a software for controlling an open source tracking fluorescence microscope. It is designed to utilize two cameras, shifted to different wavelengths to allow simultaneous tracking and behavioural measurements with epifluorescence imaging. The dual recording approach using the same light path allows the user to align the images in post and make inferences about how neural activity affects behaviour and how the animal's interactions with its environment affect neural activity. While WormsPy was developed for calcium imaging in head sensory and interneurons in C. elegans, its modular design and low cost allows it to be iterated on by other researchers and find applications in new fields.

Example of a worm expressing pansensory GCaMP being tracked using WormsPy
![](media/WormsPy3.gif)

Example behavioural analysis of headbending correlated with compartmentalized calcium activity in nrD and nrV in the RIA interneuron.
![](media/RIA.gif)

Frame-synced recordings and stage position allow for sophisticated offline analysis
![](media/VideoFigure.gif)

Example with various drosophila larvae expressing jGCaMP8f in muscle tissue courtesy of the Ohyama lab at McGill.
![](media/drosophila.gif)

## Features:
WormsPy was designed for calcium imaging in head sensory and interneurons in C. elegans. However, many applications are possible by changing the filter sets and objectives and through its configuration.
WormsPy 2 (this branch) is a single desktop application:
1. Dual live feeds (behaviour and fluorescence) at the cameras' aspect ratio, with exposure histograms, fixed display levels and LUTs.
2. Hardware-synchronised acquisition: the calcium camera is the clock and triggers the behaviour camera 1:1. Every frame is paired by frame ID, and dropped frames are recorded and flagged, never silently lost.
3. Manual mode: a dual-stick gamepad drives XY and Z. Set/Return to Focus remembers a focal plane.
4. Closed-loop tracking on the brightfield silhouette or on a fluorescent marker, aiming the stage at absolute positions through a velocity servo. The joystick always overrides.
5. Automatic calibration of stage-to-image geometry for both cameras, which replaces hand-measured field-of-view constants.
6. Recording: raw 16-bit fluorescence (BigTIFF) and lossless FFV1 behaviour video, plus a per-frame CSV (timestamps, stage position, drops) and a tracking CSV, both written as frames arrive.
7. A simulated rig (`--sim`), so everything above can be tried without hardware.

WormsPy 1 (Flask + Angular) is preserved at the `v1-legacy` tag.

See the demonstration video here: https://youtu.be/mhyYDpziSE8

## Building the WormsPy imaging platform:
For a full build guide please see our publication: [Nature Communications](https://www.nature.com/articles/s41467-026-72709-w) [bioRxiv](https://www.biorxiv.org/content/10.1101/2025.03.21.644605v1)

Please cite our work if you use this project or any derivative thereof. 
```bibtex
@article{Wittekindt2026,
  author    = {Wittekindt, Sebastian N. and Owens, Hannah and Guisnet, Aur{\'e}lie and Wittekindt, Lennard and Hendricks, Michael},
  title     = {An epifluorescence microscope design for naturalistic behavior and cellular activity in freely moving {Caenorhabditis elegans}},
  journal   = {Nature Communications},
  year      = {2026},
  volume    = {17},
  pages     = {4411},
  doi       = {10.1038/s41467-026-72709-w},
  url       = {https://doi.org/10.1038/s41467-026-72709-w},
  publisher = {Springer Nature}
}
```

### Minimum requirements:
- 2× Teledyne FLIR Spinnaker cameras (reference rig: Blackfly S BFS-U3-23S6M-C), with a GPIO cable from the clock camera's output line to the follower camera's trigger input
- XY and Z Zaber stages (ASCII protocol; reference rig: 2× TSB60E and a VSR20A)
- a dual-stick gamepad supported by pygame
- Windows (the rig) or macOS/Linux (development and the simulated rig), Python 3.11+

### Getting started with WormsPy:
1. Install [uv](https://docs.astral.sh/uv/) and clone this repository.
2. Try the simulated rig, no hardware needed: `uv run wormspy --sim`
3. On the rig PC, install the Spinnaker SDK and put its Python wheel into the environment: `uv sync --extra zaber --extra gamepad`, then `uv pip install spinnaker_python-<version>.whl`. The wheel ships with the SDK and must match your Python version.
4. Copy `config/rig.example.toml` to `config/rig.toml` and fill in the camera serial numbers, Zaber serial ports and, importantly, a safe Z range for your objective.
5. Run `uv run wormspy`.

### Analysing recordings:
Recordings are plain files: a raw 16-bit TIFF per fluorescence channel, lossless FFV1 video per behaviour channel, and CSV/JSON for everything else. The format is documented in [`docs/recording-format.md`](docs/recording-format.md). To load them in Python with frames, drops, events, tracking and plate positions lined up, install the analysis extra (`uv sync --extra analysis`) and use `wormspy.reader`:
```python
from wormspy.reader import open_recording
with open_recording("WormsPy_data/2026-09-28_N2-3/rec-001") as rec:
    calcium = rec["gcamp"].array()     # (frames, height, width), memory-mapped
    rec.frames                         # one row per frame: time, stage position, drops, tracking
```

### Developer's notes:
- Code lives in `src/wormspy`, tests in `tests`. Run them with `uv run pytest` and lint with `uv run ruff check`.
- Design decisions are recorded in `docs/adr`, and the vocabulary in `CONTEXT.md`.
- Hardware sits behind small interfaces (`Camera`, `Stage`, `Gamepad`). To support other hardware, write an adapter against those interfaces; see `src/wormspy/hardware`.

## License:
WormSpy was developed by Sebastian Wittekindt and Lennard Wittekindt at McGill University. Funded by the Canadian Institute for Health Research.

Provided as open source software under the MIT license, view the [license](LICENSE.txt) for details.
