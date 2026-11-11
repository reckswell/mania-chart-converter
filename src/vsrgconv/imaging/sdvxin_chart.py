"""sdvx.in layers -> intermediate `Chart`: layout, strip, measures, notes, timing, plus checks against published
note counts."""
from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING

from ..model import Chart, MeterChange
from .sdvxin_layout import find_layout, find_measures, lane_strip, load_rgba
from .sdvxin_notes import ReaderConfig, Timeline, read_notes

if TYPE_CHECKING:
    from ..db import SongRecord


def extract_chart(song: "SongRecord", slot: str, layers: dict[str, bytes],
                  cfg: ReaderConfig | None = None) -> Chart:
    from ..sources.chunithm import chart_meta, timing_from_bpm

    cfg = cfg or ReaderConfig()
    bg, obj, bar = (load_rgba(layers[k]) for k in ("bg", "obj", "bar"))
    if not (bg.shape == obj.shape == bar.shape):
        raise ValueError(f"layer sizes differ: bg {bg.shape}, obj {obj.shape}, bar {bar.shape}")
    layout = find_layout(bg)
    measures = find_measures(bar, layout)
    timeline = Timeline(measures, strip_height=layout.strip_height)
    notes, warnings = read_notes(lane_strip(obj, layout), timeline, cfg)

    timing, tw = timing_from_bpm(song.bpm)
    timing.meters = [MeterChange(beat, *time_signature(length)) for beat, length in timeline.meters()]
    # Beat 0 is measure 1's line. Anything drawn below it gets a negative beat and is reported.
    chart = Chart(meta=chart_meta(song, slot), lanes=16, timing=timing, notes=notes, warnings=tw + warnings)
    chart.meta.source["layout"] = {"columns": len(layout.columns), "measures": len(measures),
                                   "strip_height": layout.strip_height}
    early = [n for n in notes if n.beat < 0]
    if early:
        chart.warnings.append(f"{len(early)} notes before measure 1's line")
    chart.warnings += count_mismatches(chart)
    return chart.sort()


def time_signature(beats: "Fraction") -> tuple[int, int]:
    """Measure length in quarter beats -> (numerator, denominator): 4 -> 4/4, 7/2 -> 7/8, 13/4 -> 13/16."""
    for denominator in (4, 8, 16, 32):
        n = beats * denominator / 4
        if n.denominator == 1:
            return int(n), denominator
    return 4, 4


def count_mismatches(chart: Chart) -> list[str]:
    """Compare extracted counts with the published ones (tap, hold, slide, air, flick). Published 'tap' includes
    ExTAP and DAMAGE is not counted (otoge-db / sdvx.in convention, unverified)."""
    published = chart.meta.note_counts
    if not published:
        return []
    got = chart.count_by_kind()
    mine = {
        "tap": got.get("tap", 0) + got.get("ex_tap", 0),
        "hold": got.get("hold", 0),
        "slide": got.get("slide", 0),
        "air": got.get("air", 0) + got.get("air_hold", 0),
        "flick": got.get("flick", 0),
    }
    out = []
    for t, want in published.items():
        if mine.get(t, 0) != want:
            out.append(f"count {t}: read {mine.get(t, 0)}, published {want}")
    return out
