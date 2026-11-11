"""Merged sdvx.in chart pictures (punipu2 `resized_charts`): layout, timing and note heads.

The crops under tests/fixtures/chunithm_picture_* are cut from real pictures at column boundaries; the expected heads
and measure lengths come from the same charts' exact .c2s files (see chunithm_picture_truth.json). The thresholds
are what the reader achieved when the fixtures were made: they guard against regressions, they are not the goal.
"""
from __future__ import annotations

import io
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vsrgconv.imaging import merged_chart as mc

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------------------------------------ synthetic picture

BG = (2, 2, 2)
MEASURE_ROWS = [(88, 88, 87), (107, 107, 107)]
BEAT_ROW = (40, 40, 39)
SAT = {"tap": (114, 9, 22), "ex": (200, 170, 20), "flick": (20, 180, 200), "hold": (220, 120, 20),
       "slide": (30, 60, 220)}
BEAT_PX = 48


def _png(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    return buf.getvalue()


def _bar(a: np.ndarray, y: int, cell: int, width: int, kind: str) -> None:
    """A note head whose beat is on row y: light row above, bright row, the kind's saturated row, dark row; one
    darker pixel at each end (the end caps)."""
    x0 = int(mc.cell_edge(0, cell))
    x1 = int(mc.cell_edge(0, cell + width))
    rows = [(y - 1, (242, 230, 235)), (y, (240, 200, 205)), (y + 1, SAT[kind]), (y + 2, (70, 30, 30))]
    for r, rgb in rows:
        a[r, x0:x1, :3] = rgb
        a[r, x0, :3] = a[r, x1 - 1, :3] = tuple(v // 3 for v in rgb)


def synthetic_picture() -> tuple[bytes, list[tuple[Fraction, int, int, str]]]:
    """One column, three 4/4 measures at 48 px per beat, and a few heads (beat, cell, width, kind)."""
    h, w = 700, 250
    a = np.zeros((h, w, 4), np.uint8)
    a[:, 50:200] = (*BG, 255)
    first = 660
    lines = [first - 4 * BEAT_PX * i for i in range(4)]
    for y in lines:
        a[y - 14: y + 1, 25:45] = (254, 254, 254, 200)          # measure number
        for i, rgb in enumerate(MEASURE_ROWS):
            a[y + i, 75:175, :3] = rgb
        for j in (1, 2, 3):
            if y - j * BEAT_PX > lines[-1]:
                a[y - j * BEAT_PX: y - j * BEAT_PX + 2, 75:175, :3] = BEAT_ROW

    def row(beat: Fraction) -> int:
        return first - int(beat * BEAT_PX)

    heads = [(Fraction(1), 4, 4, "tap"),
             (Fraction(2), 0, 4, "ex"), (Fraction(2), 4, 4, "flick"),     # touching, different kinds
             (Fraction(6), 0, 4, "tap"), (Fraction(6), 4, 4, "tap"),      # touching, same kind: end caps
             (Fraction(19, 2), 8, 8, "slide"),
             (Fraction(10), 2, 3, "hold")]
    for beat, cell, width, kind in heads:
        _bar(a, row(beat), cell, width, kind)
    return _png(a), heads


def test_snap_beat_prefers_simple_fractions():
    assert mc.snap_beat(1.01, 96) == 1
    assert mc.snap_beat(2.49, 96) == Fraction(5, 2)
    assert mc.snap_beat(1 / 3 + 0.005, 96) == Fraction(1, 3)
    assert mc.snap_beat(0.03125, 96) == Fraction(1, 32)
    assert mc.snap_beat(0.5 + 1 / 96, 96) == Fraction(1, 2)          # 1 px off: within tolerance
    assert mc.snap_beat(0.5 + 1 / 96, 384) == Fraction(49, 96)       # 4 px off


def test_synthetic_layout_and_timing():
    data, _ = synthetic_picture()
    reading = mc.read_picture(data)
    assert reading.measures() == [(0, 4), (4, 4), (8, 4)]
    assert reading.timeline.beat_px == BEAT_PX


def test_synthetic_heads():
    data, want = synthetic_picture()
    reading = mc.read_picture(data)
    got = sorted((h.beat, h.cell, h.width, h.cls) for h in reading.heads)
    assert got == sorted(want)


def test_not_a_picture():
    with pytest.raises(mc.PictureError):
        mc.read_picture(_png(np.zeros((100, 100, 4), np.uint8)))


# ------------------------------------------------------------------------------------------------ real crops

TRUTH = json.loads((FIXTURES / "chunithm_picture_truth.json").read_text())

# (fixture, kind) -> heads read exactly (beat, cell, width and colour) when the fixture was made, of the total.
FLOORS = {
    ("chunithm_picture_2871_master_c10.png", "tap"): (19, 21),
    ("chunithm_picture_2871_master_c10.png", "ex_tap"): (12, 14),
    ("chunithm_picture_2871_master_c10.png", "flick"): (24, 27),
    ("chunithm_picture_2871_master_c10.png", "slide"): (8, 8),
    ("chunithm_picture_2525_master_c10.png", "tap"): (42, 44),
    ("chunithm_picture_2525_master_c10.png", "hold"): (2, 2),
    # overlapping curved slides: two slides drawn as one head, taps over slide starts
    ("chunithm_picture_2525_master_c10.png", "slide"): (1, 16),
}


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_crop_measures_match_c2s(name):
    reading = mc.read_picture((FIXTURES / name).read_bytes())
    assert [str(b) for _, b in reading.measures()] == TRUTH[name]["measures"]


@pytest.mark.parametrize("name,kind", sorted(FLOORS))
def test_crop_heads_match_c2s(name, kind):
    reading = mc.read_picture((FIXTURES / name).read_bytes())
    want = [(Fraction(b), c, w, cls, k) for b, c, w, cls, k in TRUTH[name]["heads"]]
    result = mc._match(reading.heads, want, Fraction(0))
    floor, total = FLOORS[(name, kind)]
    assert sum(result[kind].values()) == total
    assert result[kind].get("ok", 0) >= floor, result
