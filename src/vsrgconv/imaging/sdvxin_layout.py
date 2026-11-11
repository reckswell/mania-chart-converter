"""Reads the layout of an sdvx.in chart picture and unrolls it into one bottom-up strip.

sdvx.in draws a chart as three same-size layers (lanes `bg`, notes `obj`, measure numbers `bar`). Each layer is a row
of columns COLUMN_PITCH px apart: a GUTTER-wide strip for measure numbers, then the LANE-wide track. A column is read
bottom-up and the next column continues above where it ends; neighbouring columns share OVERLAP rows (the top rows of
one are the bottom rows of the next). Each measure's number label in the gutter has its bottom row on the measure line.

These geometry facts are taken from the description in Trilleo/web's `packages/chunithm/src/charts.ts`, which was
measured against ~1,900 real column pairs. This module is an independent implementation of that description.
Positions `p` count pixels from the strip's bottom (the chart's start), upward.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np

COLUMN_PITCH = 200
GUTTER = 50
LANE = 150
OVERLAP = 48
LANE_ALPHA = 128


class LayoutError(ValueError):
    pass


def load_rgba(data: bytes) -> np.ndarray:
    """Decode a PNG/WebP layer into an H x W x 4 uint8 array."""
    from PIL import Image  # optional dependency: pip install vsrgconv[images]

    with Image.open(BytesIO(data)) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8).copy()


@dataclass
class Column:
    lane_x: int      # left edge of the lane
    top: int         # first row with lanes drawn


@dataclass
class Layout:
    width: int
    height: int
    columns: list[Column]

    def segments(self) -> list[tuple[int, int, int, int]]:
        """(column index, strip start p, length, source bottom row) for each column, bottom first."""
        out = []
        start = 0
        for i, col in enumerate(self.columns):
            bottom = self.height - 1 if i == 0 else self.height - 1 - OVERLAP
            length = bottom - col.top + 1
            out.append((i, start, length, bottom))
            start += length
        return out

    @property
    def strip_height(self) -> int:
        return sum(seg[2] for seg in self.segments())


def find_layout(bg: np.ndarray) -> Layout:
    h, w = bg.shape[:2]
    if not 200 <= h <= 4096:
        raise LayoutError(f"height {h} is not an sdvx.in chart picture's")
    opaque = bg[:, :, 3] >= LANE_ALPHA
    used = opaque[::4].any(axis=0)
    runs: list[tuple[int, int]] = []
    x = 0
    while x < w:
        if used[x]:
            start = x
            while x < w and used[x]:
                x += 1
            runs.append((start, x))
        else:
            x += 1
    if not runs:
        raise LayoutError("no lanes found")
    columns = []
    for i, (left, right) in enumerate(runs):
        if right - left != LANE:
            raise LayoutError(f"column {i + 1} is {right - left}px wide, not {LANE}")
        if left != GUTTER + i * COLUMN_PITCH:
            raise LayoutError(f"column {i + 1} starts at x={left}, off the {COLUMN_PITCH}px grid")
        rows = np.nonzero(opaque[:, left:right].any(axis=1))[0]
        columns.append(Column(lane_x=left, top=int(rows[0]) if len(rows) else h))
    return Layout(width=w, height=h, columns=columns)


def unroll(layer: np.ndarray, layout: Layout, x0: int, x1: int) -> np.ndarray:
    """The strip of columns' x range [lane_x + x0, lane_x + x1), as an array with row 0 = p 0 (bottom of the chart)."""
    parts = []
    for i, _start, length, bottom in layout.segments():
        lx = layout.columns[i].lane_x
        rows = layer[bottom - length + 1: bottom + 1, lx + x0: lx + x1]
        parts.append(rows[::-1])          # bottom-up
    return np.concatenate(parts, axis=0)


def lane_strip(layer: np.ndarray, layout: Layout) -> np.ndarray:
    return unroll(layer, layout, 0, LANE)


def gutter_strip(layer: np.ndarray, layout: Layout) -> np.ndarray:
    return unroll(layer, layout, -GUTTER, 0)


def find_measures(bar: np.ndarray, layout: Layout) -> list[int]:
    """Strip positions where each measure starts (bottom row of each number label in the gutter)."""
    marked = gutter_strip(bar, layout)[:, :, 3].any(axis=1)
    starts = []
    prev = False
    for p, m in enumerate(marked):
        if m and not prev:
            starts.append(p)
        prev = bool(m)
    return starts
