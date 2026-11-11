"""Draws synthetic sdvx.in-style chart pictures (three layers) from known notes, for tests and for checking the reader
end to end without network access. The geometry follows sdvxin_layout; the colours are sdvxin_notes' palette."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO

import numpy as np

from .sdvxin_layout import COLUMN_PITCH, GUTTER, LANE, OVERLAP
from .sdvxin_notes import DEFAULT_PALETTE


@dataclass
class DrawnNote:
    beat: Fraction
    cell: int
    width: int
    cls: str = "tap"
    duration: Fraction = Fraction(0)


def draw_chart(notes: list[DrawnNote], measures: int, px_per_beat: int = 48, height: int = 1000,
               cell_left: int = 11, cell_width: int = 8, tap_px: int = 6, lead_px: int = 24
               ) -> tuple[dict[str, bytes], list[int]]:
    """Returns ({"bg","obj","bar"}: PNG bytes) and the measure start positions on the strip."""
    strip_len = lead_px + measures * 4 * px_per_beat + 40
    measure_px = [lead_px + i * 4 * px_per_beat for i in range(measures)]
    lane = np.zeros((strip_len, LANE, 4), np.uint8)
    lane[:, :, :] = (20, 20, 20, 255)
    for p in measure_px:
        lane[p:p + 2, :, :3] = 120
    obj = np.zeros((strip_len, LANE, 4), np.uint8)
    # long notes first: sdvx.in draws taps over hold and slide bodies
    for n in sorted(notes, key=lambda n: n.duration == 0):
        p0 = lead_px + int(n.beat * px_per_beat)
        p1 = p0 + (int(n.duration * px_per_beat) if n.duration else tap_px - 1)
        x0 = cell_left + n.cell * cell_width
        x1 = x0 + n.width * cell_width
        obj[p0:p1 + 1, x0:x1, :3] = DEFAULT_PALETTE[n.cls]
        obj[p0:p1 + 1, x0:x1, 3] = 255
    gutter = np.zeros((strip_len, GUTTER, 4), np.uint8)
    for p in measure_px:
        gutter[p:p + 10, 25:45] = (255, 255, 255, 255)

    # cut the strip into columns of this picture's height, sharing OVERLAP rows
    usable = height - 20
    firsts, tops = [], []
    start = 0
    while True:
        first = 0 if not firsts else start - OVERLAP
        rows = min(usable, strip_len - first)
        firsts.append(first)
        tops.append(height - rows)
        start = first + rows
        if start >= strip_len:
            break
    width = GUTTER + len(firsts) * COLUMN_PITCH
    layers = {k: np.zeros((height, width, 4), np.uint8) for k in ("bg", "obj", "bar")}
    for k, (first, top) in enumerate(zip(firsts, tops)):
        lx = GUTTER + k * COLUMN_PITCH
        rows = height - top
        seg = slice(first, first + rows)
        layers["bg"][top:, lx:lx + LANE] = lane[seg][::-1]
        layers["obj"][top:, lx:lx + LANE] = obj[seg][::-1]
        layers["bar"][top:, lx - GUTTER:lx] = gutter[seg][::-1]
    return {k: _png(v) for k, v in layers.items()}, measure_px


def _png(a: np.ndarray) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.fromarray(a, "RGBA").save(buf, "PNG")
    return buf.getvalue()
