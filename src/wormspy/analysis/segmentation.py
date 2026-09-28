"""Follow one bright neuron through a recording and measure its calcium signal.

The v1 `analysis_code/Segmentation.py`, ported to the recording format (ADR 0007):

    python -m wormspy.analysis.segmentation WormsPy_data/2026-09-28_N2-3/rec-001 --overlay

or, from Python:

    from wormspy.analysis.segmentation import Params, segment
    from wormspy.reader import open_recording

    with open_recording(".../rec-001") as rec:
        table = segment(rec, "gcamp", Params(max_jump_px=15))

Per frame, the image is contrast-stretched to 8 bits, equalised (CLAHE) and denoised
(non-local means). An adaptive threshold then gives candidate objects of 4–200 px. The neuron
is the largest candidate within `max_jump_px` of where it was last seen. The signal is measured
on the **raw 16-bit** image: the sum and mean of the brightest 25 and 10 pixels within
`roi_radius_px` of the centroid, plus the median of a surrounding annulus as a local background.

Changes from v1:
- One row per frame_index, joined to the recording's own frames table (time, stage position,
  z_moved, events), instead of a bare frame counter.
- Dropped frames (all-zero placeholders) are skipped and give NaN, instead of being segmented.
- The jump gate works. In v1 `prev_centroid` was only updated inside `if prev_centroid:`, so it
  stayed None and the gate never fired. The gate now picks the largest candidate *near* the last
  position rather than rejecting the frame outright, and it resets after `reacquire_after`
  frames without a plausible candidate, so a lost neuron is found again.
- A frame with no candidate gives NaN instead of the mean of an empty array.
- A local background (annulus median) is measured, and `top25_mean_bgsub` subtracts it.
  ΔF/F₀ is left to the analysis: the right F₀ depends on the experiment.
- Parameters are arguments, not module globals, and the module can be imported.
- The overlay is an MP4 of the enhanced frames (optional) instead of a GIF plus imshow.
"""

import argparse
import sys
from dataclasses import dataclass, fields
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from wormspy.reader import Recording, open_recording, open_session

GREEN, RED = (0, 255, 0), (0, 0, 255)


@dataclass(frozen=True)
class Params:
    """Segmentation parameters. Lengths are in pixels (~0.75 µm/px on the reference rig)."""
    block_size: int = 7                 # adaptive-threshold neighbourhood (odd)
    offset: float = -3.0                # adaptive-threshold C: more negative = more selective
    denoise_h: float = 25.0             # non-local-means strength: larger = smoother
    clahe_clip: float = 2.0
    min_area_px: float = 4.0
    max_area_px: float = 200.0
    roi_radius_px: int = 10             # the brightest pixels are taken within this radius
    background_px: tuple[int, int] = (15, 25)   # annulus (inner, outer) for the local background
    max_jump_px: float = 10.0           # furthest the neuron may move between frames
    reacquire_after: int = 10           # frames without a candidate before the gate resets


DEFAULTS = Params()


@dataclass(frozen=True)
class Candidate:
    centroid: tuple[float, float]       # (x, y) in pixels
    area_px: float
    contour: np.ndarray


def enhance(image: np.ndarray, params: Params = DEFAULTS) -> np.ndarray:
    """Per-frame min/max stretch to 8 bits, then CLAHE and non-local-means denoising.

    For finding the neuron only: the stretch discards absolute intensity, so never measure on it.
    """
    low, high = float(image.min()), float(image.max())
    scale = 255.0 / (high - low) if high > low else 0.0
    eight = ((image.astype(np.float32) - low) * scale).astype(np.uint8)
    eight = cv2.createCLAHE(clipLimit=params.clahe_clip, tileGridSize=(8, 8)).apply(eight)
    return cv2.fastNlMeansDenoising(eight, None, h=params.denoise_h, templateWindowSize=7,
                                    searchWindowSize=11)


def candidates(enhanced: np.ndarray, params: Params = DEFAULTS) -> list[Candidate]:
    """Bright objects of plausible size, largest first."""
    binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, params.block_size, params.offset)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        area = cv2.contourArea(contour)
        moments = cv2.moments(contour)
        if params.min_area_px <= area <= params.max_area_px and moments["m00"] > 0:
            centroid = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
            found.append(Candidate(centroid, area, contour))
    return sorted(found, key=lambda c: c.area_px, reverse=True)


class JumpGate:
    """Keeps the neuron's identity from frame to frame.

    Picks the largest candidate within `max_jump_px` of the last accepted position. With no
    last position (the start, or after `reacquire_after` misses in a row), the largest wins.
    """

    def __init__(self, params: Params = DEFAULTS) -> None:
        self.params = params
        self.last: tuple[float, float] | None = None
        self.misses = 0

    def choose(self, found: list[Candidate]) -> Candidate | None:
        if self.last is not None and self.misses >= self.params.reacquire_after:
            self.last = None
        if self.last is not None:
            found = [c for c in found
                     if np.hypot(c.centroid[0] - self.last[0],
                                 c.centroid[1] - self.last[1]) <= self.params.max_jump_px]
        if not found:
            self.misses += 1
            return None
        self.last, self.misses = found[0].centroid, 0
        return found[0]

    def skip(self) -> None:
        """A frame with no image (dropped): time passes, the neuron was not seen."""
        self.misses += 1


def measure(image: np.ndarray, centroid: tuple[float, float],
            params: Params = DEFAULTS) -> dict[str, float]:
    """Brightest-pixel signal and local background around `centroid`, on the raw image."""
    inner, outer = params.background_px
    reach = max(outer, params.roi_radius_px)
    cx, cy = round(centroid[0]), round(centroid[1])
    height, width = image.shape
    x0, x1 = max(cx - reach, 0), min(cx + reach + 1, width)
    y0, y1 = max(cy - reach, 0), min(cy + reach + 1, height)
    window = image[y0:y1, x0:x1].astype(np.float64)
    yy, xx = np.ogrid[y0 - cy:y1 - cy, x0 - cx:x1 - cx]
    distance = np.hypot(xx, yy)
    roi = np.sort(window[distance <= params.roi_radius_px])[::-1]
    ring = window[(distance >= inner) & (distance <= outer)]
    background = float(np.median(ring)) if ring.size else np.nan
    top25, top10 = roi[:25], roi[:10]
    return {"top25_sum": top25.sum(), "top25_mean": top25.mean(),
            "top10_sum": top10.sum(), "top10_mean": top10.mean(),
            "background": background, "top25_mean_bgsub": top25.mean() - background}


def segment(rec: Recording, channel: str | None = None, params: Params = DEFAULTS,
            overlay: Path | None = None, progress=None) -> pd.DataFrame:
    """One row per frame_index: where the neuron was and its signal (NaN where not found).

    Columns: centroid_x_px, centroid_y_px, area_px, the `measure` columns, then time_s, valid,
    z_moved, x_um, y_um, event from the recording, and neuron_x_um, neuron_y_um (the neuron's
    position on the plate) when the session has a calibration for the channel.
    `overlay`: write an MP4 of the enhanced frames with candidates (green) and the choice (red).
    `progress(done, total)` is called every 100 frames.
    """
    channel = channel or default_channel(rec)
    source = rec[channel]
    valid = rec.valid
    gate = JumpGate(params)
    writer = None
    rows = []
    for done, (frame_index, image) in enumerate(zip(rec.index, source)):
        row = {"frame_index": frame_index}
        if not valid.loc[frame_index]:
            gate.skip()
        else:
            enhanced = enhance(image, params)
            found = candidates(enhanced, params)
            chosen = gate.choose(found)
            if chosen is not None:
                row.update(centroid_x_px=chosen.centroid[0], centroid_y_px=chosen.centroid[1],
                           area_px=chosen.area_px, **measure(image, chosen.centroid, params))
            if overlay is not None:
                writer = writer or _video_writer(overlay, image.shape, rec.fps)
                writer.write(_draw(enhanced, found, chosen, params))
        rows.append(row)
        if progress and done % 100 == 0:
            progress(done, len(source))
    if writer is not None:
        writer.release()

    columns = ["centroid_x_px", "centroid_y_px", "area_px", "top25_sum", "top25_mean",
               "top10_sum", "top10_mean", "background", "top25_mean_bgsub"]
    table = pd.DataFrame(rows, columns=["frame_index", *columns]).set_index("frame_index")
    context = pd.DataFrame({"time_s": rec.time_s, "valid": valid}, index=rec.index)
    for name in ("z_moved", "x_um", "y_um", "event"):
        if name in rec.frames:
            context[name] = rec.frames[name]
    table = table.join(context)
    _add_plate_position(rec, channel, table)
    return table


def default_channel(rec: Recording) -> str:
    """The recording's only fluorescence channel, or, if several, the calcium (clock) channel."""
    fluorescence = [name for name, ch in rec.channels.items() if ch.role == "fluorescence"]
    if len(fluorescence) == 1:
        return fluorescence[0]
    reference = rec.calibration.reference if rec.calibration else None
    if reference in fluorescence:
        return reference
    raise ValueError(f"cannot tell which channel to segment ({fluorescence}); pass channel=")


def _add_plate_position(rec: Recording, channel: str, table: pd.DataFrame) -> None:
    try:
        rec.plate_um(channel, (0.0, 0.0), rec.index[0])
    except (ValueError, IndexError, KeyError):
        return                                   # no usable calibration: pixel columns only
    found = table["centroid_x_px"].notna()
    plate = [rec.plate_um(channel, (row.centroid_x_px, row.centroid_y_px), index)
             for index, row in table[found].iterrows()]
    table["neuron_x_um"] = np.nan
    table["neuron_y_um"] = np.nan
    if plate:
        table.loc[found, ["neuron_x_um", "neuron_y_um"]] = plate


def _video_writer(path: Path, shape: tuple[int, int], fps: float | None) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = shape
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps or 10.0,
                             (width, height))
    if not writer.isOpened():
        raise OSError(f"cannot write {path}")
    return writer


def _draw(enhanced: np.ndarray, found: list[Candidate], chosen: Candidate | None,
          params: Params) -> np.ndarray:
    picture = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(picture, [c.contour for c in found], -1, GREEN, 1)
    if chosen is not None:
        centre = (round(chosen.centroid[0]), round(chosen.centroid[1]))
        cv2.circle(picture, centre, params.roi_radius_px, RED, 1)
    return picture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wormspy.analysis.segmentation",
        description="Follow one bright neuron and measure its calcium signal. Writes "
                    "analysis/<channel>_segmentation.csv into each recording folder.")
    parser.add_argument("path", type=Path, help="a rec-NNN folder, or a session folder "
                                                "(every recording in it)")
    parser.add_argument("--channel", help="channel to segment (default: the calcium channel)")
    parser.add_argument("--overlay", action="store_true",
                        help="also write analysis/<channel>_segmentation.mp4")
    for field in fields(Params):
        if field.type in (int, float):
            parser.add_argument("--" + field.name.replace("_", "-"), type=field.type,
                                default=getattr(DEFAULTS, field.name))
    args = parser.parse_args(argv)
    params = Params(**{f.name: getattr(args, f.name) for f in fields(Params)
                       if hasattr(args, f.name)})

    folders = ([args.path] if (args.path / "frames.csv").exists()
               else open_session(args.path).recordings)
    for folder in folders:
        with open_recording(folder) as rec:
            for warning in rec.warnings:
                print(f"{folder.name}: {warning}", file=sys.stderr)
            channel = args.channel or default_channel(rec)
            out = folder / "analysis" / f"{channel}_segmentation"
            table = segment(rec, channel, params,
                            overlay=out.with_suffix(".mp4") if args.overlay else None,
                            progress=lambda done, total, name=folder.name:
                                print(f"\r{name}: {done}/{total}", end="", file=sys.stderr))
            out.parent.mkdir(exist_ok=True)
            table.to_csv(out.with_suffix(".csv"))
            found = table["centroid_x_px"].notna().sum()
            print(f"\r{folder.name}: neuron found in {found}/{len(table)} frames "
                  f"-> {out.with_suffix('.csv')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
