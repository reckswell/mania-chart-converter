"""Draws a chart's layout as a picture from its notes (the intermediate format), for any game.

The layout follows sdvx.in's chart pictures: black, columns read from the bottom left, each a 50 px gutter (measure
numbers, BPM changes) and a 150 px lane area between gold rails, breaking at measure lines. Every note kind has one colour in every game (see
`COLOURS`), so a picture of a Sekai chart and a picture of a DDR chart read the same way. Pictures are made from the
stored notes on demand (`vsrgconv render`), so they always match the database and never need to be fetched.

Vertical spacing simulates an approach rate (`RenderConfig.approach_rate`, osu!'s AR): a note crosses a `screen_px`
tall screen in the AR's preempt time (AR 9: 600 ms on 480 px, 0.8 px per ms), so the distance between two notes is
their distance in time, as in the game, and 1/16 streams at 150 BPM are 80 px apart. Scroll-speed gimmicks (SFL,
Sekai hi-speed) are not simulated: the picture is for reading patterns. The older compact layout (`axis="beats"`:
24 px per beat, 8 measures a column) is still available.

Each source lane unit is `lane_px` wide (`lane_area_px / lanes`), so a 16-lane Chunithm chart and a 4-lane DDR chart
fill the same column width.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, replace
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
#: opacity of long-note bodies over the lanes; `other` (invisible guides, decorations, fakes) is faint
BODY_ALPHA = {"hold": 150, "slide": 120, "air_hold": 110, "knob": 120, "scratch": 150, "other": 22}
BACKGROUND = (0, 0, 0)             # sdvx.in: black, lanes framed by gold rails
LANE_BG = (8, 8, 14)
LANE_LINE = (40, 30, 56)
BEAT_LINE = (58, 58, 72)
MEASURE_LINE = (150, 150, 170)
RAIL = (190, 160, 100)
TEXT = (235, 235, 240)
BPM_TEXT = (255, 200, 90)
WHITE = (255, 255, 255)


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


def preempt_ms(ar: float) -> float:
    """osu!'s approach time for an approach rate: AR 5 = 1200 ms, AR 9 = 600 ms, AR 10 = 450 ms."""
    return 1200.0 + 120.0 * (5 - ar) if ar < 5 else 1200.0 - 150.0 * (ar - 5)


@dataclass
class RenderConfig:
    axis: str = "time"             # "time": spaced by time at approach_rate; "beats": px_per_beat; "auto": beats,
    #                                but time when gimmick BPMs stretch the beat axis
    approach_rate: float = 9.0     # osu! AR: a note crosses screen_px in preempt_ms(AR)
    screen_px: int = 480           # height of the simulated screen (osu!'s playfield is 480 units tall)
    column_px: int | None = None   # tallest column; whole measures when they fit. Default: 2400 on the time axis,
    #                                measures_per_column 4/4 measures on the beats axis
    px_per_beat: int = 24          # beats axis
    measures_per_column: int = 8   # beats axis
    lane_area_px: int = 150        # sdvx.in: a 150 px lane area after a 50 px gutter
    min_lane_px: int = 8
    gutter_px: int = 50
    rail_px: int = 8               # gold rails either side of the lanes
    label_px: int = 16             # measure numbers and BPMs
    margin_px: int = 6
    note_px: int = 6               # thickness of a tap
    footer_px: int = 16            # title line under the columns
    stretch_limit: float = 3.0     # auto: use time when the chart is this many times longer in beats than in time
    max_columns: int = 400         # a chart longer than this is cut, and the title says so
    max_width_px: int = 16384      # wider pictures wrap into rows of columns, read top to bottom
    row_gap_px: int = 24
    max_pause_ms: float = 2000.0   # stops and delays longer than this are drawn this long (Pump It Up ends charts
    #                                with 999-second stops; a picture of them would be hundreds of empty columns)
    px_per_ms_override: float | None = None   # time axis scale instead of the approach rate's (A/B pictures)

    @classmethod
    def compact(cls) -> RenderConfig:
        """The first layout: 24 px per beat, 8 measures a column, narrow lanes; time axis only for gimmick charts."""
        return cls(axis="auto", px_per_beat=24, measures_per_column=8, lane_area_px=128, gutter_px=30, rail_px=0,
                   label_px=10, note_px=4, footer_px=14, max_columns=300, max_width_px=1 << 20)

    @property
    def px_per_ms(self) -> float:
        return self.px_per_ms_override or self.screen_px / preempt_ms(self.approach_rate)

    def column_height(self, time_axis: bool) -> int:
        if self.column_px:
            return self.column_px
        return 2400 if self.axis == "time" else self.px_per_beat * 4 * self.measures_per_column


@dataclass
class _Column:
    index: int
    start: float                   # px along the strip at the column's bottom
    end: float                     # px at its top
    row: int = 0                   # where it is drawn: row (top to bottom) and place in the row (left to right)
    slot: int = 0


class _Axis:
    """Where a beat is drawn: px along one long strip that the columns cut up.

    Time axis: the beat's time times px_per_ms, so spacing is spacing in time. On the beats axis it is the beat times
    px_per_beat, so measures are evenly spaced. Gimmick charts (Arcaea's 99,999,999 BPM jumps) put notes millions of
    beats apart that are seconds apart in time; in "auto" they go on the time axis, at px_per_beat at the main BPM."""

    def __init__(self, chart: Chart, cfg: RenderConfig):
        self.timing = chart.timing
        cap = cfg.max_pause_ms
        if cap and any(abs(ms) > cap for _, ms in [*self.timing.stops, *self.timing.delays]):
            def clamp(pauses: list[tuple[Fraction, float]]) -> list[tuple[Fraction, float]]:
                return [(b, max(-cap, min(cap, ms))) for b, ms in pauses]
            self.timing = replace(self.timing, stops=clamp(self.timing.stops), delays=clamp(self.timing.delays))
        end = max(chart.end_beat, Fraction(1))
        self._cache: dict[Fraction, float] = {}
        self.scale = float(cfg.px_per_beat)            # px per beat (beats) or px per ms (time)
        mode = cfg.axis
        try:
            self.ms = self.timing.ms_lookup()
            self.ms0 = self.ms(Fraction(0))
            main = abs(self.timing.main_bpm(end)) or 120.0
            if mode == "auto":
                span = (self.ms(end) - self.ms0) * main / 60000.0
                if float(end) > cfg.stretch_limit * max(span, 1.0) + 16:
                    mode, self.scale = "time", cfg.px_per_beat * main / 60000.0
                else:
                    mode = "beats"
            elif mode == "time":
                self.scale = cfg.px_per_ms
        except (ValueError, ZeroDivisionError):      # no usable BPM: beats it is
            mode, self.scale = "beats", float(cfg.px_per_beat)
        self.time = mode == "time"

    def pos(self, beat: Fraction) -> float:
        if not self.time:
            return float(beat) * self.scale
        p = self._cache.get(beat)
        if p is None:
            p = self._cache[beat] = (self.ms(beat) - self.ms0) * self.scale
        return p

    def segments(self, lo: Fraction, hi: Fraction) -> list[tuple[Fraction, Fraction, float]]:
        """(from beat, to beat, px per beat) pieces that cover [lo, hi]; one piece on the beats axis."""
        if not self.time:
            return [(lo, hi, self.scale)]
        bpms = sorted(self.timing.bpms, key=lambda b: b.beat)
        out = []
        for i, b in enumerate(bpms):
            a = lo if i == 0 else max(lo, b.beat)
            z = min(hi, bpms[i + 1].beat) if i + 1 < len(bpms) else hi
            if a < z:
                out.append((a, z, 60000.0 / b.bpm * self.scale if b.bpm > 0 else 0.0))
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
        try:
            length = meters[idx].beats_per_measure
        except ZeroDivisionError:          # a broken time signature (x/0) in a source: draw 4/4
            length = Fraction(4)
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


def _visible_measures(chart: Chart, axis: _Axis, hi: Fraction) -> list[tuple[int, Fraction, float]]:
    """(number, beat, px) of the measure lines worth drawing up to beat `hi`: none inside gimmick spans, where
    measures are less than 2 px apart (a 99,999,999 BPM jump crosses millions of measures in no time)."""
    out: list[tuple[int, Fraction, float]] = []
    for a, z, px_per_beat in axis.segments(Fraction(0), hi):
        if px_per_beat * 4 < 2:
            continue
        for number, beat in measure_starts(chart.timing, max(a, Fraction(0)), z):
            if out and out[-1][1] == beat:
                continue
            out.append((number, beat, axis.pos(beat)))
    return out


def _columns(chart: Chart, cfg: RenderConfig, axis: _Axis | None = None) -> tuple[list[_Column], bool]:
    """Columns along the strip, breaking at measure lines; a measure taller than a column is split. Also whether the
    chart was cut at max_columns."""
    axis = axis or _Axis(chart, cfg)
    positions = [axis.pos(b) for n in chart.notes for b in (n.beat, n.end_beat)] or [0.0]
    first, last = min(0.0, min(positions)), max(axis.pos(Fraction(1)), max(positions))
    # measure lines from the start up to the first one above the last note, which closes the last column
    end_beat = max(chart.end_beat, Fraction(1))
    lines = [p for _, _, p in _visible_measures(chart, axis, end_beat + 64) if p > first]
    height = cfg.column_height(axis.time)
    if axis.time and not cfg.column_px:
        # as many whole typical measures as fit in the default height, so columns end on measure lines and are
        # filled. A slow chart's measure may make the column up to 1.5 times taller; past that, and for a longer
        # measure than the typical one (a slow section), the measure is split
        gaps = sorted(b - a for a, b in zip(lines, lines[1:]) if b - a >= 8)
        if gaps and (typical := gaps[len(gaps) // 2]) <= height * 1.5:
            height = typical * max(1, int(height // typical)) + 1
    above = [p for p in lines if p > last]
    top = min(above) if above and min(above) - last <= height else last + cfg.note_px + 1.0
    breaks = sorted({p for p in lines if p < top} | {top})
    bounds, start, prev = [first], first, first
    limit = cfg.max_columns + 1
    for p in breaks:
        if p - start > height:
            if prev > start:                      # close the column at the last measure line that fit
                bounds.append(prev)
                start = prev
            while p - start > height and len(bounds) <= limit:   # a measure taller than a column: split it
                start += height
                bounds.append(start)
        if len(bounds) > limit:
            break
        prev = p
    if bounds[-1] < top and len(bounds) <= limit:
        bounds.append(top)
    cut = len(bounds) - 1 > cfg.max_columns
    bounds = bounds[:cfg.max_columns + 1]
    return [_Column(i, a, b) for i, (a, b) in enumerate(zip(bounds, bounds[1:]))], cut


def _blend(colour: tuple[int, int, int], alpha: int, under: tuple[int, int, int] = LANE_BG) -> tuple[int, int, int]:
    return tuple(round(c * alpha / 255 + u * (255 - alpha) / 255) for c, u in zip(colour, under))


class _Palette:
    """Pictures are drawn straight into a palette image: flat colours, so the PNG is small and nothing is
    quantised. Long-note bodies get their colour pre-blended over the lanes."""

    def __init__(self) -> None:
        self.colours: list[tuple[int, int, int]] = []
        self.index: dict[tuple[int, int, int], int] = {}

    def __call__(self, colour: tuple[int, int, int]) -> int:
        if colour not in self.index:
            self.index[colour] = len(self.colours)
            self.colours.append(colour)
        return self.index[colour]

    def flat(self) -> list[int]:
        return [v for c in self.colours for v in c]


def render_chart(chart: Chart, cfg: RenderConfig | None = None, title: str | None = None):
    """The chart as a PIL image (palette mode, "P")."""
    from PIL import Image, ImageDraw, ImageFont

    cfg = cfg or RenderConfig()
    axis = _Axis(chart, cfg)
    lanes = max(int(chart.lanes), 1)
    lane_px = max(cfg.min_lane_px, cfg.lane_area_px // lanes)
    area = lane_px * lanes
    col_w = cfg.gutter_px + 2 * cfg.rail_px + area + cfg.margin_px
    cols, cut = _columns(chart, cfg, axis)
    starts = [c.start for c in cols]
    col_h = math.ceil(max(c.end - c.start for c in cols))
    per_row = max(1, min(len(cols), (cfg.max_width_px - cfg.margin_px) // col_w))
    rows = math.ceil(len(cols) / per_row)
    for c in cols:
        c.row, c.slot = divmod(c.index, per_row)
    row_h = col_h + cfg.row_gap_px
    width = cfg.margin_px + col_w * per_row
    height = cfg.margin_px * 2 + row_h * rows - cfg.row_gap_px + cfg.footer_px
    pal = _Palette()
    img = Image.new("P", (width, height), pal(BACKGROUND))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=cfg.label_px)
    bottom = cfg.margin_px + col_h                 # of the first row
    top_pos = cols[-1].end

    def col_of(p: float) -> _Column:
        return cols[min(max(bisect_right(starts, p) - 1, 0), len(cols) - 1)]

    def x_of(col: _Column, lane: float) -> float:
        return cfg.margin_px + col.slot * col_w + cfg.gutter_px + cfg.rail_px + float(lane) * lane_px

    def y_of(col: _Column, p: float) -> float:
        return bottom + col.row * row_h - (p - col.start)

    # ---- lanes
    for col in cols:
        x0, top, low = x_of(col, 0), y_of(col, col.end), y_of(col, col.start)
        draw.rectangle([x0, top, x0 + area - 1, low], fill=pal(LANE_BG))
        if cfg.rail_px:
            draw.rectangle([x0 - cfg.rail_px, top, x0 - 1, low], fill=pal(RAIL))
            draw.rectangle([x0 + area, top, x0 + area + cfg.rail_px - 1, low], fill=pal(RAIL))
        for k in range(1, lanes):
            draw.line([x_of(col, k), top, x_of(col, k), low], fill=pal(LANE_LINE))

    end_beat = max(chart.end_beat, Fraction(1))
    lo_beat = min(Fraction(0), min((n.beat for n in chart.notes), default=Fraction(0)))
    hi_beat = end_beat + 64

    def line_at(p: float, colour: tuple[int, int, int]) -> None:
        c = col_of(p)
        draw.line([x_of(c, 0), y_of(c, p), x_of(c, lanes) - 1, y_of(c, p)], fill=pal(colour))

    # ---- beat lines (where beats are at least 4 px apart)
    for a, z, px_per_beat in axis.segments(lo_beat, hi_beat):
        if px_per_beat < 4:
            continue
        b = Fraction(math.ceil(max(a, 0)))
        while b < z:
            p = axis.pos(b)
            if p >= top_pos:
                break
            line_at(p, BEAT_LINE)
            b += 1

    # ---- notes: long bodies first (beat lines show where they cross), then heads, so heads stay on top
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

    def body(n: Note, colour: int, inset: float) -> None:
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
                draw.polygon([(x_of(col, l0) + ins, y_of(col, lo)), (x_of(col, l0 + w0) - ins, y_of(col, lo)),
                              (x_of(col, l1 + w1) - ins, y_of(col, hi)), (x_of(col, l1) + ins, y_of(col, hi))],
                             fill=colour)

    def at(beat: Fraction) -> tuple[_Column, float] | None:
        p = axis.pos(beat)
        if p > top_pos:
            return None
        c = col_of(p)
        return c, y_of(c, p)

    def head(n: Note, colour: int, thick: int) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 1, x_of(col, float(n.lane) + float(n.width)) - 1
        draw.rectangle([x0, y - thick + 1, max(x0, x1), y], fill=colour)

    def end_cap(n: Note, colour: int) -> None:
        if (where := at(n.end_beat)) is None:
            return
        col, y = where
        lane, w = lane_at(n, n.end_beat)
        draw.rectangle([x_of(col, lane) + 1, y - 1, max(x_of(col, lane) + 1, x_of(col, lane + w) - 1), y], fill=colour)

    def arrow(n: Note, colour: int) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 1, x_of(col, float(n.lane) + float(n.width)) - 1
        mid = (x0 + x1) / 2
        size = max(6, min(14, (x1 - x0) / 2))
        down = str(n.extra.get("direction", "up")).startswith("down")
        tip = y + size - 2 if down else y - size
        draw.polygon([(x0, y - 2), (x1, y - 2), (mid, tip)], fill=colour)

    def mine(n: Note) -> None:
        if (where := at(n.beat)) is None:
            return
        col, y = where
        x0, x1 = x_of(col, n.lane) + 2, x_of(col, float(n.lane) + float(n.width)) - 2
        draw.line([x0, y - 4, x1, y + 2], fill=pal(COLOURS["mine"]), width=2)
        draw.line([x0, y + 2, x1, y - 4], fill=pal(COLOURS["mine"]), width=2)

    long_kinds = {NoteKind.HOLD, NoteKind.SLIDE, NoteKind.AIR_HOLD, NoteKind.KNOB, NoteKind.SCRATCH, NoteKind.OTHER}
    for n in chart.notes:
        if n.kind in long_kinds and n.duration > 0:
            inset = 0 if n.kind in (NoteKind.HOLD, NoteKind.SCRATCH) else lane_px * 0.25
            body(n, pal(_blend(COLOURS[n.kind.value], BODY_ALPHA[n.kind.value])), inset)

    def gutter_text(col: _Column, y: float, text: str, colour: tuple[int, int, int]) -> None:
        """Right-aligned in the gutter next to the column's own lanes, the text's bottom at y."""
        left, top, right, low = draw.textbbox((0, 0), text, font=font)
        x = x_of(col, 0) - cfg.rail_px - 3 - right
        draw.text((max(x, x_of(col, 0) - cfg.rail_px - cfg.gutter_px + 1), y - low), text, fill=pal(colour),
                  font=font)

    # ---- measure lines and numbers (the number's bottom row on its line, as on sdvx.in), BPM changes
    last_line: dict[int, float] = {}
    last_label: dict[int, float] = {}
    for number, _, p in _visible_measures(chart, axis, hi_beat):
        if p >= top_pos:
            break
        c = col_of(p)
        y = y_of(c, p)
        if abs(last_line.get(c.index, 1e9) - y) >= 2:
            last_line[c.index] = y
            line_at(p, MEASURE_LINE)
        if abs(last_label.get(c.index, 1e9) - y) >= cfg.label_px + 2:
            last_label[c.index] = y
            gutter_text(c, y, f"{number:02d}", TEXT)
    for col in cols:
        draw.line([x_of(col, 0), y_of(col, col.end), x_of(col, lanes) - 1, y_of(col, col.end)], fill=pal(MEASURE_LINE))
    bpm_changes = sorted(chart.timing.bpms, key=lambda b: b.beat)
    last_bpm: float | None = None
    for change in bpm_changes:
        if len(bpm_changes) == 1 and change.beat <= 0:
            continue
        p = axis.pos(change.beat)
        if p > top_pos or (last_bpm is not None and abs(p - last_bpm) < 2 * cfg.label_px):
            continue      # gimmick bursts (warps written as huge BPMs): label the first change only
        last_bpm = p
        c = col_of(p)
        text = f"{change.bpm:.4g}" if abs(change.bpm) < 10000 else "warp"
        gutter_text(c, y_of(c, p) - cfg.label_px - 3, text, BPM_TEXT)

    for n in chart.notes:
        colour = pal(COLOURS[n.kind.value])
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
            ex = n.extra.get("ex_start") and n.kind is NoteKind.HOLD
            head(n, pal(COLOURS["ex_tap"]) if ex else colour, cfg.note_px)
            if n.kind is NoteKind.FLICK:
                arrow(n, pal(WHITE))
            if n.duration > 0 and n.kind in (NoteKind.HOLD, NoteKind.SLIDE, NoteKind.SCRATCH):
                end_cap(n, colour)

    m = chart.meta
    native = m.difficulty_native or m.difficulty
    level = f" {m.level}" if m.level and not str(native).endswith(str(m.level)) else ""
    label = title if title is not None else f"{m.game.upper()} {m.song_id} {m.title_romaji or m.title} [{native}{level}]"
    if axis.time and cfg.axis == "time" and not cfg.px_per_ms_override:
        label += f"  (AR {cfg.approach_rate:g})"
    elif axis.time:
        label += " (time axis)"
    label += f" (first {len(cols)} columns only)" * cut
    draw.text((cfg.margin_px, height - cfg.footer_px - cfg.margin_px + 5), label, fill=pal(TEXT), font=_font_for_title())
    img.putpalette(pal.flat())
    return img


def render_png(chart: Chart, cfg: RenderConfig | None = None, scale: float = 1.0) -> bytes:
    """PNG bytes. Drawn in a palette, so flat colours compress well and nothing is quantised."""
    from PIL import Image

    img = render_chart(chart, cfg)
    if scale != 1.0:
        img = img.convert("RGB").resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                                        Image.LANCZOS).quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    out = BytesIO()
    img.save(out, "PNG", compress_level=9)
    return out.getvalue()
