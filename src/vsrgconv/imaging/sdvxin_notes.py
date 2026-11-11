"""Turns an unrolled sdvx.in Chunithm notes strip into notes in the intermediate format.

**Status: uncalibrated.** The geometry (strip, measures) is known (sdvxin_layout.py). The note palette, the cell grid
inside the 150 px lane and the note anchor below are **first guesses**. No real sdvx.in picture could be downloaded
from the build machine. Before trusting the output:

1. put one real chart's three layers in the cache (docs/sources/chunithm-sdvxin.md);
2. run `vsrgconv calibrate-sdvxin <layers>`, which prints the colours actually used, the x extent of notes and
   component sizes, and writes the palette it would use;
3. compare `extract` counts with the published per-type counts (otoge-db / sdvx.in JSON): `vsrgconv check-counts`.

The method: classify each opaque pixel to the nearest palette colour, label connected runs per class, then read each
component's cell range (x) and time (y). Holds and slides are tall components; air notes are matched to the ground
note under them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from ..model import Note, NoteKind, PathPoint
from .sdvxin_layout import LANE

# Guessed palette (RGB). Override with a JSON file: {"tap": [r,g,b], ...}.
DEFAULT_PALETTE: dict[str, tuple[int, int, int]] = {
    "tap": (255, 64, 64),
    "ex_tap": (255, 230, 60),
    "hold": (255, 160, 40),
    "slide": (40, 120, 255),
    "flick": (120, 230, 255),
    "air": (60, 230, 60),
    "air_hold": (150, 255, 150),
    "mine": (120, 120, 140),
}
KIND_OF = {"tap": NoteKind.TAP, "ex_tap": NoteKind.EX_TAP, "hold": NoteKind.HOLD, "slide": NoteKind.SLIDE,
           "flick": NoteKind.FLICK, "air": NoteKind.AIR, "air_hold": NoteKind.AIR_HOLD, "mine": NoteKind.MINE}


@dataclass
class ReaderConfig:
    palette: dict[str, tuple[int, int, int]] = field(default_factory=lambda: dict(DEFAULT_PALETTE))
    max_colour_distance: float = 90.0
    alpha_min: int = 160
    cells: int = 16
    cell_left: float | None = None      # x of cell 0's left edge inside the lane; None = auto from the notes
    cell_width: float | None = None
    tap_max_height: int = 14            # px; taller components are holds/slides
    anchor: str = "bottom"              # where a tap's time is: bottom | centre
    min_pixels: int = 6

    @classmethod
    def load(cls, path: str | Path | None) -> "ReaderConfig":
        cfg = cls()
        if path:
            d = json.loads(Path(path).read_text("utf-8"))
            for k, v in d.items():
                if k == "palette":
                    cfg.palette = {kk: tuple(vv) for kk, vv in v.items()}
                else:
                    setattr(cfg, k, v)
        return cfg


@dataclass
class Component:
    cls: str
    x0: int
    x1: int          # exclusive
    p0: int          # bottom row (strip position)
    p1: int          # top row, inclusive
    pixels: int
    # per-row x extents, for slide paths: p -> (x0, x1)
    rows: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def height(self) -> int:
        return self.p1 - self.p0 + 1


def classify(strip: np.ndarray, cfg: ReaderConfig) -> np.ndarray:
    """Per-pixel palette index (-1 = nothing) for an S x LANE x 4 strip."""
    names = list(cfg.palette)
    pal = np.array([cfg.palette[n] for n in names], dtype=np.float32)
    rgb = strip[:, :, :3].astype(np.float32)
    d = np.sqrt(((rgb[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3))
    idx = d.argmin(axis=2)
    ok = (strip[:, :, 3] >= cfg.alpha_min) & (d.min(axis=2) <= cfg.max_colour_distance)
    return np.where(ok, idx, -1)


def components(labels: np.ndarray, names: list[str], min_pixels: int = 6) -> list[Component]:
    """Connected components (4-neighbour, via horizontal runs) per class."""
    comps: list[Component] = []
    for ci, name in enumerate(names):
        mask = labels == ci
        rows_with = np.nonzero(mask.any(axis=1))[0]
        open_runs: list[tuple[int, int, int]] = []   # (x0, x1, comp index) on the previous row
        prev_row = -2
        parent: list[int] = []

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        local: list[Component] = []
        for p in rows_with:
            row = mask[p]
            xs = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
            runs = list(zip(xs[::2], xs[1::2]))
            cur: list[tuple[int, int, int]] = []
            for x0, x1 in runs:
                hits = [c for (a, b, c) in open_runs if p == prev_row + 1 and a < x1 and x0 < b]
                if hits:
                    root = find(hits[0])
                    for other in hits[1:]:
                        r = find(other)
                        if r != root:
                            parent[r] = root
                else:
                    root = len(local)
                    parent.append(root)
                    local.append(Component(name, int(x0), int(x1), int(p), int(p), 0))
                c = local[root]
                c.x0, c.x1 = min(c.x0, int(x0)), max(c.x1, int(x1))
                c.p0, c.p1 = min(c.p0, int(p)), max(c.p1, int(p))
                c.pixels += int(x1 - x0)
                lo, hi = c.rows.get(int(p), (int(x0), int(x1)))
                c.rows[int(p)] = (min(lo, int(x0)), max(hi, int(x1)))
                cur.append((int(x0), int(x1), root))
            open_runs, prev_row = cur, p
        # fold merged components into their roots
        merged: dict[int, Component] = {}
        for i, c in enumerate(local):
            r = find(i)
            if r == i:
                merged[r] = c
        for i, c in enumerate(local):
            r = find(i)
            if r != i:
                m = merged[r]
                m.x0, m.x1, m.p0, m.p1 = min(m.x0, c.x0), max(m.x1, c.x1), min(m.p0, c.p0), max(m.p1, c.p1)
                m.pixels += c.pixels
                for p, (a, b) in c.rows.items():
                    lo, hi = m.rows.get(p, (a, b))
                    m.rows[p] = (min(lo, a), max(hi, b))
        comps += [c for c in merged.values() if c.pixels >= min_pixels]
    return comps


LONG_CLASSES = ("hold", "slide", "air_hold")


def merge_split_bodies(comps: list[Component], max_gap: int) -> list[Component]:
    """A tap drawn over a hold/slide body cuts it in two; join same-class pieces that line up across a short gap."""
    keep = [c for c in comps if c.cls not in LONG_CLASSES]
    for cls in LONG_CLASSES:
        pieces = sorted((c for c in comps if c.cls == cls), key=lambda c: c.p0)
        merged: list[Component] = []
        for c in pieces:
            target = None
            for m in merged:
                if 0 < c.p0 - m.p1 <= max_gap:
                    top = m.rows.get(m.p1)
                    bottom = c.rows.get(c.p0)
                    if top and bottom and abs(top[0] - bottom[0]) <= 3 and abs(top[1] - bottom[1]) <= 3:
                        target = m
                        break
            if target is None:
                merged.append(c)
                continue
            target.x0, target.x1 = min(target.x0, c.x0), max(target.x1, c.x1)
            target.p1 = max(target.p1, c.p1)
            target.pixels += c.pixels
            target.rows.update(c.rows)
        keep += merged
    return keep


class Timeline:
    """Maps strip positions to beats from measure starts (px) and each measure's length in beats."""

    def __init__(self, measure_px: list[int], beats_per_measure: list[Fraction] | None = None,
                 strip_height: int | None = None) -> None:
        if len(measure_px) < 1:
            raise ValueError("no measures found")
        self.px = list(measure_px)
        gaps = [b - a for a, b in zip(self.px, self.px[1:])]
        self.median_gap = sorted(gaps)[len(gaps) // 2] if gaps else (strip_height or 192)
        if beats_per_measure is None:
            # Measures are drawn at a fixed height per beat: infer each measure's length from its height.
            beats_per_measure = [Fraction(round(g * 16 / self.median_gap), 4) or Fraction(4) for g in gaps]
            beats_per_measure.append(Fraction(4))
        self.bpm_list = beats_per_measure
        self.start_beats = [Fraction(0)]
        for b in beats_per_measure[:-1]:
            self.start_beats.append(self.start_beats[-1] + b)

    def beat_at(self, p: float) -> Fraction:
        i = 0
        while i + 1 < len(self.px) and self.px[i + 1] <= p:
            i += 1
        start = self.px[i]
        span = self.px[i + 1] - start if i + 1 < len(self.px) else self.median_gap * self.bpm_list[i] / 4
        return self.start_beats[i] + Fraction(int(round((p - start) * 48)), int(round(span * 48))) * self.bpm_list[i] \
            if span else self.start_beats[i]

    def meters(self) -> list[tuple[Fraction, Fraction]]:
        """(start beat, beats per measure) wherever the measure length changes."""
        out: list[tuple[Fraction, Fraction]] = []
        for start, length in zip(self.start_beats, self.bpm_list):
            if not out or out[-1][1] != length:
                out.append((start, length))
        return out


def cell_grid(comps: list[Component], cfg: ReaderConfig) -> tuple[float, float]:
    if cfg.cell_left is not None and cfg.cell_width is not None:
        return cfg.cell_left, cfg.cell_width
    ground = [c for c in comps if c.cls not in ("air", "air_hold")]
    if not ground:
        return 11.0, (LANE - 22) / cfg.cells
    left = min(c.x0 for c in ground)
    right = max(c.x1 for c in ground)
    return float(left), (right - left) / cfg.cells


def read_notes(strip: np.ndarray, timeline: Timeline, cfg: ReaderConfig) -> tuple[list[Note], list[str]]:
    names = list(cfg.palette)
    labels = classify(strip, cfg)
    comps = merge_split_bodies(components(labels, names, cfg.min_pixels), cfg.tap_max_height + 2)
    left, cw = cell_grid(comps, cfg)
    warnings: list[str] = []

    def cells(x0: float, x1: float) -> tuple[int, int]:
        a = int(round((x0 - left) / cw))
        b = int(round((x1 - left) / cw))
        a = max(0, min(cfg.cells - 1, a))
        return a, max(1, min(cfg.cells - a, b - a))

    notes: list[Note] = []
    for c in sorted(comps, key=lambda c: (c.p0, c.x0)):
        kind = KIND_OF[c.cls]
        lane, width = cells(c.x0, c.x1)
        anchor = c.p0 if cfg.anchor == "bottom" else (c.p0 + c.p1) / 2
        beat = timeline.beat_at(anchor)
        n = Note(beat=beat, lane=lane, width=width, kind=kind, extra={"px": [c.x0, c.x1, c.p0, c.p1]})
        tall = c.height > cfg.tap_max_height
        if kind in (NoteKind.HOLD, NoteKind.SLIDE, NoteKind.AIR_HOLD):
            if tall:
                n.duration = max(Fraction(0), timeline.beat_at(c.p1) - beat)
            else:
                n.kind = NoteKind.TAP if kind == NoteKind.HOLD else kind
        elif tall:
            warnings.append(f"{c.cls} component {c.height}px tall at p={c.p0}; read as one note")
        if n.kind == NoteKind.SLIDE and n.duration:
            step = max(1, c.height // 8)
            for p in sorted(c.rows)[::step]:
                a, b = c.rows[p]
                pl, pw = cells(a, b)
                n.path.append(PathPoint(beat=timeline.beat_at(p) - beat, lane=pl, width=pw))
        if kind in (NoteKind.AIR,):
            n.extra["direction"] = "up"   # TODO: read the arrow shape
        notes.append(n)
    return notes, warnings


def calibration_report(strip: np.ndarray, top_colours: int = 24) -> dict[str, Any]:
    """What a real strip contains, to tune ReaderConfig: dominant opaque colours and the x extent of drawing."""
    alpha = strip[:, :, 3]
    opaque = strip[alpha >= 160][:, :3]
    if len(opaque) == 0:
        return {"opaque_pixels": 0}
    q = (opaque // 16) * 16 + 8
    keys, counts = np.unique(q, axis=0, return_counts=True)
    order = counts.argsort()[::-1][:top_colours]
    cols = np.nonzero((alpha >= 160).any(axis=0))[0]
    return {
        "opaque_pixels": int(len(opaque)),
        "colours": [{"rgb": keys[i].tolist(), "pixels": int(counts[i])} for i in order],
        "x_extent": [int(cols.min()), int(cols.max()) + 1],
        "strip_height": int(strip.shape[0]),
    }
