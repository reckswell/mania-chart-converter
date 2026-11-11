"""Draws a chart's layout as a picture from its notes (the intermediate format), for any game.

The picture reads like sdvx.in's chart pictures: columns of measures, read from the bottom left. Each column is a
fixed number of measures. Measure numbers and BPM changes are written in the gutter to the left of each column. Every
note kind has one colour in every game (see `COLOURS`), so a picture of a Sekai chart and a picture of a DDR chart
read the same way. Pictures are made from the stored notes on demand (`vsrgconv render`), so they always match the
database and never need to be fetched.

Geometry, in pixels at scale 1: `px_per_beat` vertically (4/4 measure = 4 * px_per_beat), each source lane unit is
`lane_px` wide, so a 16-lane Chunithm chart and a 4-lane DDR chart are drawn at sensible widths.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterator
from itertools import islice
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO

from ..model import Chart, MeterChange, Note, NoteKind

COLOURS: dict[str, tuple[int, int, int]] = {
    "tap": (255, 70, 110),
    "ex_tap": (255, 214, 60),
    "flick": (70, 190, 255),
    "hold": (255, 170, 40),
    "slide": (40, 230, 220),
    "air": (90, 230, 90),
    "air_hold": (190, 90, 255),
    "mine": (150, 150, 150),
    "scratch": (255, 110, 40),
    "knob": (80, 120, 255),
    "other": (90, 90, 100),
}
BACKGROUND = (12, 12, 18)
LANE_BG = (24, 24, 34)
LANE_LINE = (44, 44, 60)
BEAT_LINE = (52, 52, 70)
MEASURE_LINE = (140, 140, 160)
TEXT = (210, 210, 220)
BPM_TEXT = (255, 200, 90)


# fonts with Japanese, Chinese and Korean, for titles; set VSRGCONV_FONT to use another
TITLE_FONTS = ["C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/msgothic.ttc",
               "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
               "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
               "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"]
_title_font: dict[int, object] = {}


def _font_for_title(size: int = 11):
    """A font that has the title's characters; PIL's built-in font only covers Latin."""
    if size not in _title_font:
        import os

        from PIL import ImageFont

        font = None
        for path in [os.environ.get("VSRGCONV_FONT", "")] + TITLE_FONTS:
            if path and os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except OSError:
                    continue
        _title_font[size] = font or ImageFont.load_default()
    return _title_font[size]


@dataclass
class RenderConfig:
    px_per_beat: int = 24
    measures_per_column: int = 8
    lane_area_px: int = 128        # width of the lane area; lane_px = lane_area_px / lanes, at least min_lane_px
    min_lane_px: int = 8
    gutter_px: int = 30
    margin_px: int = 6
    note_px: int = 4               # thickness of a tap
    footer_px: int = 14            # title line under the columns
    axis: str = "auto"             # "beats", "time", or "auto" (time when gimmick BPMs stretch the beat axis)
    stretch_limit: float = 3.0     # auto: use time when the chart is this many times longer in beats than in time
    max_columns: int = 300         # a chart longer than this is cut, and the title says so


@dataclass
class _Column:
    index: int
    start: float                   # first display unit in the column
    end: float                     # display unit at its top


class _Axis:
    """Where a beat is drawn, in display units of one beat each.

    Beats axis: the beat itself, so measures are evenly spaced, like sdvx.in's pictures. Time axis: the beat's time in
    beats of the main BPM. Gimmick charts (Arcaea's 99,999,999 BPM jumps) put notes millions of beats apart that are
    seconds apart in time; on the time axis they are drawn the way the game plays them."""

    def __init__(self, chart: Chart, cfg: RenderConfig):
        self.timing = chart.timing
        end = max(chart.end_beat, Fraction(1))
        self.time = False
        self._cache: dict[Fraction, float] = {}
        mode = cfg.axis
        try:
            main = abs(self.timing.main_bpm(end)) or 120.0
            self.unit = main / 60000.0
            self.ms = self.timing.ms_lookup()
            self.ms0 = self.ms(Fraction(0))
            if mode == "auto":
                span = (self.ms(end) - self.ms0) * self.unit
                mode = "time" if float(end) > cfg.stretch_limit * max(span, 1.0) + 16 else "beats"
        except (ValueError, ZeroDivisionError):      # no usable BPM: beats it is
            mode = "beats"
        self.time = mode == "time"

    def pos(self, beat: Fraction) -> float:
        if not self.time:
            return float(beat)
        p = self._cache.get(beat)
        if p is None:
            p = self._cache[beat] = (self.ms(beat) - self.ms0) * self.unit
        return p

    def segments(self, lo: Fraction, hi: Fraction) -> list[tuple[Fraction, Fraction, float]]:
        """(from beat, to beat, display units per beat) pieces that cover [lo, hi]; one piece on the beats axis."""
        if not self.time:
            return [(lo, hi, 1.0)]
        bpms = sorted(self.timing.bpms, key=lambda b: b.beat)
        out = []
        for i, b in enumerate(bpms):
            a = lo if i == 0 else max(lo, b.beat)
            z = min(hi, bpms[i + 1].beat) if i + 1 < len(bpms) else hi
            if a < z:
                out.append((a, z, 60000.0 / b.bpm * self.unit if b.bpm > 0 else 0.0))
        return out


def measure_starts(timing, lo: Fraction, hi: Fraction) -> Iterator[tuple[int, Fraction]]:
    """(measure number from 1, beat) of each measure that starts in [lo, hi].

    Same measures as `TimingMap.measure_starts`, which lists every measure from beat 0; this jumps over runs of one
    time signature, so a window far into a gimmick chart costs nothing."""
    meters = sorted(timing.meters, key=lambda m: m.beat) or [MeterChange(Fraction(0))]
    beat, number, idx = Fraction(0), 1, 0
    while beat <= hi:
        while idx + 1 < len(meters) and meters[idx + 1].beat <= beat:
            idx += 1
        length = meters[idx].beats_per_measure
        if length <= 0:
            length = Fraction(4)
        if idx + 1 < len(meters):          # this time signature runs until a measure starts at or after the next one
            n = max(1, math.ceil((meters[idx + 1].beat - beat) / length))
        else:
            n = math.floor((hi - beat) / length) + 1
        first = max(0, math.ceil((lo - beat) / length))
        last = min(n - 1, math.floor((hi - beat) / length))
        for k in range(first, last + 1):
            yield number + k, beat + k * length
        beat += n * length
        number += n


def _columns(chart: Chart, cfg: RenderConfig, axis: _Axis | None = None) -> tuple[list[_Column], bool]:
    """Columns in display units, and whether the chart was cut at max_columns."""
    axis = axis or _Axis(chart, cfg)
    positions = [axis.pos(b) for n in chart.notes for b in (n.beat, n.end_beat)] or [0.0]
    first, last = min(0.0, min(positions)), max(1.0, max(positions))
    m = cfg.measures_per_column
    if axis.time:                          # fixed columns of m 4/4 measures at the main BPM
        size = 4.0 * m
        count = max(1, math.ceil((last - first) / size + 1e-9))
        bounds = [first + i * size for i in range(count + 1)]
    else:                                  # every m measures
        cap = cfg.max_columns * m + 2      # enough measures for max_columns (+1 tells that the chart was cut)
        starts = [float(b) for _, b in islice(measure_starts(chart.timing, Fraction(0), Fraction(last)), cap)]
        if starts[-1] <= last:             # one more measure line so the last note's measure is closed
            starts.append(starts[-1] + ((starts[-1] - starts[-2]) if len(starts) > 1 else 4.0))
        # m measures per column, fewer when long measures (32/4 ...) would make a column taller than m 4/4 measures
        bounds, count = [starts[0]], 0
        for b, nxt in zip(starts, starts[1:]):
            if count and (count == m or nxt - bounds[-1] > 4.0 * m):
                bounds.append(b)
                count = 0
            count += 1
        bounds.append(starts[-1])
        bounds[0] = min(bounds[0], first)  # notes before beat 0 (pickups) go in the first column
    cut = len(bounds) - 1 > cfg.max_columns
    bounds = bounds[:cfg.max_columns + 1]
    return [_Column(i, a, b) for i, (a, b) in enumerate(zip(bounds, bounds[1:]))], cut


def render_chart(chart: Chart, cfg: RenderConfig | None = None, title: str | None = None):
    """The chart as a PIL image (RGB)."""
    from PIL import Image, ImageDraw, ImageFont

    cfg = cfg or RenderConfig()
    axis = _Axis(chart, cfg)
    lanes = max(int(chart.lanes), 1)
    lane_px = max(cfg.min_lane_px, cfg.lane_area_px // lanes)
    area = lane_px * lanes
    col_w = cfg.gutter_px + area + cfg.margin_px
    cols, cut = _columns(chart, cfg, axis)
    starts = [c.start for c in cols]
    ppb = cfg.px_per_beat
    col_h = math.ceil(max(c.end - c.start for c in cols) * ppb)
    width = cfg.margin_px + col_w * len(cols)
    height = cfg.margin_px * 2 + col_h + cfg.footer_px
    img = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default()
    bottom = cfg.margin_px + col_h
    top_pos = cols[-1].end

    def col_of(p: float) -> _Column:
        return cols[min(max(bisect_right(starts, p) - 1, 0), len(cols) - 1)]

    def x_of(col: _Column, lane: float) -> float:
        return cfg.margin_px + col.index * col_w + cfg.gutter_px + float(lane) * lane_px

    def y_of(col: _Column, p: float) -> float:
        return bottom - (p - col.start) * ppb

    # ---- lanes, beat and measure lines, numbers, BPM changes
    for col in cols:
        x0 = x_of(col, 0)
        top = y_of(col, col.end)
        draw.rectangle([x0, top, x0 + area - 1, bottom], fill=LANE_BG)
        for k in range(1, lanes):
            draw.line([x_of(col, k), top, x_of(col, k), bottom], fill=LANE_LINE)
        draw.line([x0, top, x0 + area - 1, top], fill=MEASURE_LINE)
    end_beat = max(chart.end_beat, Fraction(1))
    lo_beat = min(Fraction(0), min((n.beat for n in chart.notes), default=Fraction(0)))
    # lines go up to the top of the last column: on the time axis, one more column of beats is plenty (the loops stop
    # at the top)
    hi_beat = end_beat + 4 * cfg.measures_per_column if axis.time else max(end_beat, Fraction(top_pos))
    last_line: dict[int, float] = {}
    last_label: dict[int, float] = {}
    for a, z, units in axis.segments(lo_beat, hi_beat):
        if units * ppb >= 4:               # beat lines, where beats are at least 4 px apart
            b = Fraction(math.ceil(max(a, 0)))
            while b < z:
                p = axis.pos(b)
                if p >= top_pos:
                    break
                c = col_of(p)
                draw.line([x_of(c, 0), y_of(c, p), x_of(c, lanes) - 1, y_of(c, p)], fill=BEAT_LINE)
                b += 1
        if units * ppb < 0.5:              # gimmick span: measures are too close to draw
            continue
        for number, mb in measure_starts(chart.timing, max(a, Fraction(0)), z):
            p = axis.pos(mb)
            if p >= top_pos:
                break
            c = col_of(p)
            y = y_of(c, p)
            if abs(last_line.get(c.index, 1e9) - y) >= 2:
                last_line[c.index] = y
                draw.line([x_of(c, 0), y, x_of(c, lanes) - 1, y], fill=MEASURE_LINE)
            if abs(last_label.get(c.index, 1e9) - y) >= 11:
                last_label[c.index] = y
                draw.text((x_of(c, 0) - cfg.gutter_px + 2, y - 11), f"{number:03d}", fill=TEXT, font=font)
    bpm_changes = sorted(chart.timing.bpms, key=lambda b: b.beat)
    last_bpm: float | None = None
    for change in bpm_changes:
        if len(bpm_changes) == 1 and change.beat <= 0:
            continue
        p = axis.pos(change.beat)
        if p > top_pos or (last_bpm is not None and abs(p - last_bpm) < 1):
            continue      # gimmick bursts (warps written as huge BPMs): label the first change only
        last_bpm = p
        c = col_of(p)
        text = f"{change.bpm:.4g}" if abs(change.bpm) < 10000 else "warp"
        draw.text((x_of(c, 0) - cfg.gutter_px + 2, y_of(c, p) - 22), text, fill=BPM_TEXT, font=font)

    # ---- notes: long bodies first, then heads, so heads stay visible
    def lane_at(n: Note, beat: Fraction) -> tuple[float, float]:
        """Lane and width of a moving note at `beat` (linear between path points)."""
        pts = [(Fraction(0), float(n.lane), float(n.width))] + [(p.beat, float(p.lane), float(p.width))
                                                                  for p in n.path]
        rel = beat - n.beat
        for (b0, l0, w0), (b1, l1, w1) in zip(pts, pts[1:]):
            if b0 <= rel <= b1:
                t = float((rel - b0) / (b1 - b0)) if b1 != b0 else 1.0
                return l0 + (l1 - l0) * t, w0 + (w1 - w0) * t
        return pts[-1][1], pts[-1][2]

    def body(n: Note, colour: tuple[int, int, int], alpha: int, inset: float) -> None:
        if n.duration <= 0:
            return
        breaks = sorted({n.beat, n.end_beat} | {n.beat + p.beat for p in n.path if 0 < p.beat < n.duration})
        for a, b in zip(breaks, breaks[1:]):
            pa, pb = axis.pos(a), axis.pos(b)
            (la, wa), (lb, wb) = lane_at(n, a), lane_at(n, b)
            if pb < pa:                    # negative BPM: drawn backwards
                pa, pb, la, wa, lb, wb = pb, pa, lb, wb, la, wa
            if pb - pa < 1e-9 or pa > top_pos:
                continue
            for col in cols[col_of(pa).index:col_of(min(pb, top_pos)).index + 1]:
                lo, hi = max(pa, col.start), min(pb, col.end)
                if hi <= lo:
                    continue
                t0, t1 = (lo - pa) / (pb - pa), (hi - pa) / (pb - pa)
                l0, w0 = la + (lb - la) * t0, wa + (wb - wa) * t0
                l1, w1 = la + (lb - la) * t1, wa + (wb - wa) * t1
                ins = min(inset, w0 * lane_px / 3, w1 * lane_px / 3)
                poly = [(x_of(col, l0) + ins, y_of(col, lo)), (x_of(col, l0 + w0) - ins, y_of(col, lo)),
                        (x_of(col, l1 + w1) - ins, y_of(col, hi)), (x_of(col, l1) + ins, y_of(col, hi))]
                draw.polygon(poly, fill=(*colour, alpha))

    def at(beat: Fraction) -> tuple[_Column, float] | None:
        p = axis.pos(beat)
        if p > top_pos:
            return None
        c = col_of(p)
        return c, y_of(c, p)

    def head(n: Note, colour: tuple[int, int, int], thick: int) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 1, x_of(col, float(n.lane) + float(n.width)) - 1
        draw.rectangle([x0, y - thick + 1, max(x0, x1), y], fill=colour)

    def end_cap(n: Note, colour: tuple[int, int, int]) -> None:
        if (where := at(n.end_beat)) is None:
            return
        col, y = where
        lane, w = lane_at(n, n.end_beat)
        draw.rectangle([x_of(col, lane) + 1, y - 1, max(x_of(col, lane) + 1, x_of(col, lane + w) - 1), y], fill=colour)

    def arrow(n: Note, colour: tuple[int, int, int]) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 1, x_of(col, float(n.lane) + float(n.width)) - 1
        mid = (x0 + x1) / 2
        down = str(n.extra.get("direction", "up")).startswith("down")
        tip = y + 6 if down else y - 8
        draw.polygon([(x0, y - 2), (x1, y - 2), (mid, tip)], fill=colour)

    def mine(n: Note) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 2, x_of(col, float(n.lane) + float(n.width)) - 2
        draw.line([x0, y - 4, x1, y + 2], fill=COLOURS["mine"], width=2)
        draw.line([x0, y + 2, x1, y - 4], fill=COLOURS["mine"], width=2)

    # body opacity; `other` (invisible guides, decorations, fakes) is drawn faintly so it never hides real notes
    long_kinds = {NoteKind.HOLD: 150, NoteKind.SLIDE: 120, NoteKind.AIR_HOLD: 110, NoteKind.KNOB: 120,
                  NoteKind.SCRATCH: 150, NoteKind.OTHER: 22}
    for n in chart.notes:
        if n.kind in long_kinds and n.duration > 0:
            inset = 0 if n.kind in (NoteKind.HOLD, NoteKind.SCRATCH) else lane_px * 0.25
            body(n, COLOURS[n.kind.value], long_kinds[n.kind], inset)
    for n in chart.notes:
        colour = COLOURS[n.kind.value]
        if n.kind is NoteKind.AIR:
            arrow(n, colour)
        elif n.kind is NoteKind.MINE:
            mine(n)
        elif n.kind is NoteKind.OTHER:
            if n.duration == 0:
                head(n, colour, 2)
        elif n.kind is NoteKind.AIR_HOLD:
            if not n.extra.get("air_slide"):
                end_cap(n, colour)
        else:
            head(n, COLOURS["ex_tap"] if n.extra.get("ex_start") and n.kind is NoteKind.HOLD else colour,
                 cfg.note_px)
            if n.kind is NoteKind.FLICK:
                arrow(n, (255, 255, 255))
            if n.duration > 0 and n.kind in (NoteKind.HOLD, NoteKind.SLIDE, NoteKind.SCRATCH):
                end_cap(n, colour)

    m = chart.meta
    native = m.difficulty_native or m.difficulty
    level = f" {m.level}" if m.level and not str(native).endswith(str(m.level)) else ""
    label = title if title is not None else f"{m.game.upper()} {m.song_id} {m.title_romaji or m.title} [{native}{level}]"
    label += " (time axis)" * axis.time + f" (first {len(cols)} columns only)" * cut
    draw.text((cfg.margin_px, bottom + cfg.margin_px - 1), label, fill=TEXT, font=_font_for_title())
    return img


def render_png(chart: Chart, cfg: RenderConfig | None = None, scale: float = 1.0, colours: int = 64) -> bytes:
    """PNG bytes, palette-quantised (pictures are flat colours, so this is small and still exact to look at)."""
    from PIL import Image

    img = render_chart(chart, cfg)
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    out = BytesIO()
    img.quantize(colors=colours, method=Image.Quantize.MEDIANCUT).save(out, "PNG", optimize=True)
    return out.getvalue()
