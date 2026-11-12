"""Merged sdvx.in chart pictures (punipu2/chunithm-chart-downloader `resized_charts/<id><mst|ult|we>.png`).

Reads the layout (columns, measure and beat lines), the measure lengths and the note-head bars, and compares them
with an exact chart (`match_heads`). It does not read hold or slide bodies, AIR or mines yet, so it builds no `Chart`
and no source calls it. The layout, measured accuracy and blockers are in docs/sources/chunithm-pictures.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from io import BytesIO

import numpy as np

PITCH = 200          # one column per 200 px
GUTTER = (20, 50)    # measure numbers, x range inside a column
LANE = (50, 200)     # lane frame (walls + playfield), x range inside a column
PF_LEFT = 75         # playfield (16 cells) left edge inside a column
CELL = 6.25          # 100 px / 16 cells
CELLS = 16


class PictureError(ValueError):
    pass


def load_rgba(data: bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        return np.asarray(im.convert("RGBA"), dtype=np.uint8).copy()


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(first, last) index of each run of True values."""
    m = np.concatenate(([False], np.asarray(mask, bool), [False]))
    d = np.flatnonzero(np.diff(m.view(np.int8)))
    return [(int(a), int(b) - 1) for a, b in zip(d[::2], d[1::2])]


def cell_samples(k: int) -> list[int]:
    """Two x positions near the centre of each cell of column k (avoids the lane divider lines)."""
    xs = []
    for c in range(CELLS):
        mid = PITCH * k + PF_LEFT + CELL * c + CELL / 2
        xs += [int(mid) - 1, int(mid)]
    return xs


@dataclass
class Column:
    index: int
    labels: list[int]                  # bottom row of each measure label, bottom first
    lines: list[int]                   # measure line (upper row of the 2-row line), bottom first
    beat_lines: list[int] = field(default_factory=list)
    lane_top: int = 0
    lane_bottom: int = 0


def _row_modes(a: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, int]:
    xs = cell_samples(k)
    px = a[:, xs, :3].astype(np.int64)
    code = (px[:, :, 0] << 16) | (px[:, :, 1] << 8) | px[:, :, 2]
    code = np.where(a[:, xs, 3] > 0, code, -1)
    srt = np.sort(code, axis=1)
    modes = np.empty(len(code), np.int64)
    counts = np.empty(len(code), np.int64)
    for i, row in enumerate(srt):
        change = np.flatnonzero(np.diff(row)) + 1
        starts = np.concatenate(([0], change))
        lens = np.diff(np.concatenate((starts, [len(row)])))
        j = int(lens.argmax())
        modes[i], counts[i] = row[starts[j]], lens[j]
    return modes, counts, len(xs)


def _is_grey(code: int, tol: int = 14) -> bool:
    r, g, b = (code >> 16) & 255, (code >> 8) & 255, code & 255
    return code >= 0 and max(r, g, b) - min(r, g, b) <= tol


def _brightness(code: int) -> int:
    return ((code >> 16) & 255) + ((code >> 8) & 255) + (code & 255)


def _labels(a: np.ndarray, k: int) -> list[tuple[int, int]]:
    """(top, bottom) rows of the measure numbers in column k's gutter, bottom first."""
    g = a[:, PITCH * k + GUTTER[0]: PITCH * k + GUTTER[1], 3] > 0
    return [(t, b) for t, b in _runs(g.any(axis=1)) if 8 <= b - t <= 22][::-1]


@dataclass
class LineStyle:
    background: int
    measure: set[int]
    beat: int


def _line_style(modes_by_col: list[tuple[np.ndarray, np.ndarray, int]], labels_by_col: list[list[int]]) -> LineStyle:
    from collections import Counter

    every: Counter[int] = Counter()
    at_label: Counter[int] = Counter()
    greys: Counter[int] = Counter()
    for (modes, counts, m), labs in zip(modes_by_col, labels_by_col):
        every.update(modes.tolist())
        for y in range(len(modes)):
            c = int(modes[y])
            if counts[y] * 4 < m or not _is_grey(c):
                continue
            greys[c] += 1
            if any(-3 <= y - b <= 3 for b in labs) and _brightness(c) > 150:
                at_label[c] += 1
    bg = every.most_common(1)[0][0]
    if not at_label:
        raise PictureError("no measure lines found")
    top = at_label.most_common(1)[0][1]
    measure = {c for c, v in at_label.items() if v * 5 >= top}
    beats = [c for c, _ in greys.most_common() if c != bg and c not in measure and 60 <= _brightness(c) <= 160]
    return LineStyle(bg, measure, beats[0] if beats else -1)


def _colour_mask(px: np.ndarray, colours: set[int] | list[int], tol: int = 10) -> np.ndarray:
    """Pixels (..., 3) within `tol` (per channel) of any of `colours`."""
    out = np.zeros(px.shape[:-1], bool)
    for c in colours:
        rgb = np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255])
        out |= (np.abs(px.astype(np.int16) - rgb).max(axis=-1) <= tol)
    return out


def _playfield_xs(k: int) -> np.ndarray:
    """Every playfield x of column k except the lane divider columns (every 2 cells)."""
    x0 = PITCH * k + PF_LEFT
    return np.array([x for x in range(x0 + 1, x0 + 99) if (x - x0) % 12.5 not in (0, 0.5) and (x - x0) % 25 > 1])


def find_columns(a: np.ndarray) -> list[Column]:
    h, w = a.shape[:2]
    n = (w - 50) // PITCH
    if n < 1 or h < 200:
        raise PictureError(f"{w}x{h} is not a merged chart picture")
    modes = [_row_modes(a, k) for k in range(n)]
    labs = [[b for _, b in _labels(a, k)] for k in range(n)]
    style = _line_style(modes, labs)
    cols = []
    for k in range(n):
        md, ct, m = modes[k]
        strong = (ct * 4 >= m)
        xs = _playfield_xs(k)
        mline = _colour_mask(a[:, xs, :3], style.measure).sum(axis=1)
        lines = []
        for b in labs[k]:
            best, score = -1, len(xs) // 4
            for y in range(max(0, b - 3), min(h - 1, b + 4)):
                sc = min(mline[y], mline[y + 1])
                if sc > score:
                    best, score = y, sc
            lines.append(best if best >= 0 else -b - 1)     # not seen: resolved below
        is_beat = (md == style.beat) & strong
        beat_rows = [r0 for r0, r1 in _runs(is_beat) if r1 - r0 in (1, 2)]   # beat lines are 2 rows
        lane = _runs(a[:, PITCH * k + LANE[0]: PITCH * k + LANE[1], 3].any(axis=1))
        cols.append(Column(k, labs[k], lines, beat_rows, lane[0][0] if lane else 0, lane[-1][1] if lane else h - 1))
    # Lines hidden under notes: one beat from the nearest beat line, else the chart's usual label-to-line offset.
    from collections import Counter

    offs = Counter(y - b for c in cols for y, b in zip(c.lines, c.labels) if y >= 0)
    off = offs.most_common(1)[0][0] if offs else 0
    gaps = Counter(q - p for c in cols for p, q in zip(sorted(c.beat_lines), sorted(c.beat_lines)[1:]))
    beat_px = gaps.most_common(1)[0][0] if gaps else 0
    for c in cols:
        fixed = []
        for y in c.lines:
            if y < 0:
                b = -y - 1
                guess = [r + beat_px for r in c.beat_lines if abs(r + beat_px - b) <= 2] + \
                        [r - beat_px for r in c.beat_lines if abs(r - beat_px - b) <= 2]
                y = guess[0] if beat_px and guess else b + off
            fixed.append(y)
        c.lines = fixed
    return cols


# ------------------------------------------------------------------------------------------------ timing

@dataclass
class Measure:
    column: int
    bottom: int                         # row of the measure line where it starts
    top: int                            # row of the next measure line
    start: Fraction = Fraction(0)       # beat at `bottom`
    beats: Fraction = Fraction(4)
    anchors: list[tuple[int, Fraction]] = field(default_factory=list)   # (row, beat offset), bottom first

    @property
    def px(self) -> int:
        return self.bottom - self.top


def _nice_beats(px: float, beat_px: float, quantum: Fraction = Fraction(1, 4)) -> Fraction:
    q = Fraction(round(px / beat_px / quantum)) * quantum
    return q if q > 0 else quantum


class Timeline:
    """Row positions -> beats. Beat 0 is the start of the first drawn measure; measure lines and beat lines are
    anchors, and positions between anchors are interpolated linearly."""

    def __init__(self, columns: list[Column]) -> None:
        self.columns = columns
        self.beat_px = self._main_beat_px(columns)
        self.measures: list[Measure] = []
        for c in columns:
            for yb, yt in zip(c.lines, c.lines[1:]):
                self.measures.append(Measure(c.index, yb, yt))
        if not self.measures:
            raise PictureError("no measures found")
        self._regularise()
        beat = Fraction(0)
        self.irregular = 0
        d_prev = float(self.beat_px)
        for m in self.measures:
            m.start = beat
            col = columns[m.column]
            inside = sorted((r for r in col.beat_lines if m.top + 3 < r < m.bottom - 3), reverse=True)
            m.beats, m.anchors = self._fit(m, inside, d_prev)
            d_prev = m.px / float(m.beats)
            beat += m.beats
        self.end = beat
        self._points: dict[int, list[tuple[int, Fraction]]] = {}
        for m in self.measures:
            pts = self._points.setdefault(m.column, [])
            for r, b in m.anchors:
                if not pts or pts[-1][0] != r:
                    pts.append((r, m.start + b))

    def _main_beat_px(self, columns: list[Column]) -> int:
        """The most common distance between neighbouring beat lines; also sets `spacings`, every distance seen often
        (one per tempo)."""
        from collections import Counter

        gaps: Counter[int] = Counter()
        for c in columns:
            rows = sorted(set(c.beat_lines) | set(c.lines))
            gaps.update(q - p for p, q in zip(rows, rows[1:]) if q - p >= 30)
        if not gaps:
            raise PictureError("no beat spacing found")
        self.spacings = [g for g, v in gaps.items() if v >= 4 and v * 50 >= gaps.most_common(1)[0][1]]
        return gaps.most_common(1)[0][0]

    def _regularise(self) -> None:
        """A measure line seen one or two rows off (its upper row hidden under a note) makes one measure too long and
        the next one as much too short; move it back."""
        d0 = self.beat_px
        for m1, m2 in zip(self.measures, self.measures[1:]):
            if m1.column != m2.column:
                continue
            e1 = m1.px - float(_nice_beats(m1.px, d0)) * d0
            e2 = m2.px - float(_nice_beats(m2.px, d0)) * d0
            if e1 and abs(e1) <= 2 and abs(e1 + e2) < 0.5:
                m1.top += int(e1)
                m2.bottom += int(e1)

    def _fit(self, m: Measure, inside: list[int], d_prev: float) -> tuple[Fraction, list[tuple[int, Fraction]]]:
        """The measure's length in beats, and anchors (row, beat offset) for mapping rows inside it.

        Most measures are drawn at the chart's main spacing (beat_px per beat). A BPM change redraws measures at
        another spacing (the pictures are drawn to time, not beats), so the length is the count of beats that fits
        the beat lines seen inside the measure, the previous measure's spacing, or the main one, preferring 4 beats.
        Beat lines are often hidden under notes: a missing line is no evidence, a line off the grid is."""
        d0 = self.beat_px

        def score(beats: Fraction, d: float) -> float:
            on = sum(1 for r in inside if abs((m.bottom - r) / d - round((m.bottom - r) / d)) * d <= 2.5)
            sc = 3 * on - 3 * (len(inside) - on) - 0.25 * abs(float(beats) - 4)
            known = any(abs(d - g) <= 1.5 for g in self.spacings)
            sc += 2 * (abs(d - d_prev) <= 1.5) + 2 * (abs(d - d0) <= 1.5) + 2 * known
            return sc - (0.5 if beats.denominator > 1 else 0)

        options = [(Fraction(b), m.px / b) for b in range(1, 17) if m.px / b >= 30]
        nb = _nice_beats(m.px, d0, Fraction(1, 2))       # x/8 meters
        if nb.denominator > 1 and abs(float(nb) * d0 - m.px) <= 1.5:
            options.append((nb, m.px / float(nb)))
        if not options:
            options = [(Fraction(1), float(m.px))]
        beats, d = max(options, key=lambda o: score(*o))
        if abs(d - d0) > 1.5:
            self.irregular += 1
        anchors = [(m.bottom, Fraction(0))]
        for r in inside:
            j = round((m.bottom - r) / d)
            if 0 < j < beats and abs(m.bottom - r - j * d) <= 2.5 and all(j != a[1] for a in anchors):
                anchors.append((r, Fraction(j)))
        anchors.sort(key=lambda a: a[1])
        anchors.append((m.top, beats))
        return beats, anchors

    def beat_at(self, column: int, y: float) -> tuple[float, float]:
        """(beat, pixels per beat there) for a row of a column; rows outside the column's measures extrapolate."""
        pts = self._points.get(column)
        if not pts:
            raise PictureError(f"column {column} has no measures")
        if len(pts) == 1:
            return float(pts[0][1]), float(self.beat_px)
        if y >= pts[0][0]:
            (r0, b0), (r1, b1) = pts[0], pts[1]
        elif y <= pts[-1][0]:
            (r0, b0), (r1, b1) = pts[-2], pts[-1]
        else:
            i = next(i for i in range(len(pts) - 1) if pts[i][0] >= y >= pts[i + 1][0])
            (r0, b0), (r1, b1) = pts[i], pts[i + 1]
        scale = (r0 - r1) / float(b1 - b0)
        return float(b0) + (r0 - y) / scale, scale


# ------------------------------------------------------------------------------------------------ colours

def hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees (0-360), saturation and value (0-1) of an (..., 3) uint8 array."""
    c = rgb.astype(np.float32) / 255.0
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    mx = c.max(axis=-1)
    mn = c.min(axis=-1)
    d = mx - mn
    safe = np.where(d == 0, 1, d)
    h = np.where(mx == r, (g - b) / safe % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) * 60
    h = np.where(d == 0, 0, h)
    s = np.where(mx == 0, 0, d / np.where(mx == 0, 1, mx))
    return h, s, mx


# Note heads are bars: a light row, then rows of the kind's colour getting darker. The kind is the hue of the
# saturated row under the light one.
HEAD_HUES = [  # (name, hue from, hue to)
    ("tap", 335, 360), ("tap", 0, 12),
    ("hold", 15, 44),
    ("ex", 45, 72),
    ("flick", 165, 196),
    ("slide", 200, 245),
]


def head_class(h: np.ndarray) -> np.ndarray:
    out = np.full(h.shape, -1, np.int8)
    for i, (_, lo, hi) in enumerate(HEAD_HUES):
        out[(h >= lo) & (h < hi)] = i
    return out


def rows_for_beat(tl: Timeline, beat: float, margin: int = 30) -> list[tuple[int, float]]:
    """Where a beat is drawn: (column, row), possibly twice where neighbouring columns overlap."""
    out = []
    for k, pts in tl._points.items():
        lo_b, hi_b = float(pts[0][1]), float(pts[-1][1])
        bottom_scale = (pts[0][0] - pts[1][0]) / float(pts[1][1] - pts[0][1]) if len(pts) > 1 else tl.beat_px
        top_scale = (pts[-2][0] - pts[-1][0]) / float(pts[-1][1] - pts[-2][1]) if len(pts) > 1 else tl.beat_px
        if lo_b - margin / bottom_scale <= beat <= hi_b + margin / top_scale:
            if beat <= lo_b:
                y = pts[0][0] + (lo_b - beat) * bottom_scale
            elif beat >= hi_b:
                y = pts[-1][0] - (beat - hi_b) * top_scale
            else:
                i = next(i for i in range(len(pts) - 1) if float(pts[i][1]) <= beat <= float(pts[i + 1][1]))
                (r0, b0), (r1, b1) = pts[i], pts[i + 1]
                y = r0 - (beat - float(b0)) * (r0 - r1) / float(b1 - b0)
            out.append((k, y))
    return out


# ------------------------------------------------------------------------------------------------ note heads

@dataclass
class Head:
    """A bar-shaped note head (tap, ExTAP, flick, hold start, slide start/node/end) as drawn."""

    column: int
    row: int            # the bar's light top row
    cell: int
    width: int
    cls: str            # tap | hold | ex | flick | slide
    sat_row: int = 0
    raw_beat: float = 0.0           # picture beats (0 = first drawn measure line), unsnapped
    px_per_beat: float = 96.0
    beat: Fraction = Fraction(0)    # raw_beat snapped to the c2s grid (see snap_beat)


def cell_edge(k: int, c: float) -> float:
    return PITCH * k + PF_LEFT + CELL * c


def cell_pixels(k: int) -> list[int]:
    """Three x positions inside each cell of column k, away from its edges (end caps, divider lines)."""
    out = []
    for c in range(CELLS):
        e = int(cell_edge(k, c))
        out += [e + 2, e + 3, e + 4]
    return out


def find_heads(a: np.ndarray, k: int, hsv_all: tuple[np.ndarray, np.ndarray, np.ndarray]) -> list[Head]:
    """Bars in column k. Read down a cell, a note head is: a pale light row, a bright row, a saturated row whose hue
    is the kind's, then a darker row; the head's own row (where its beat is) is the bright one. Neighbouring cells of
    one colour on one row are one note unless a darker end cap separates them."""
    xs = cell_pixels(k)
    hh, ss, vv = (m[:, xs] for m in hsv_all)
    opaque = a[:, xs, 3] > 0
    light = (vv >= 0.75) & (ss <= 0.4) & opaque
    bright = (vv >= 0.72) & opaque
    sat = (ss >= 0.38) & (vv >= 0.42) & opaque
    cls = np.where(sat, head_class(hh), -1)
    n_rows = a.shape[0]

    def vote(m: np.ndarray) -> np.ndarray:          # per cell: 2 of its 3 pixels
        return (m[:, 0::3].astype(np.int8) + m[:, 1::3] + m[:, 2::3]) >= 2

    light_c, bright_c = vote(light), vote(bright)
    cls_c = np.full((n_rows, CELLS), -1, np.int8)
    for ci in range(len(HEAD_HUES)):
        name = HEAD_HUES[ci][0]
        same = np.zeros(cls.shape, bool)
        for cj, (other, _, _) in enumerate(HEAD_HUES):
            if other == name:
                same |= cls == cj
        cls_c[vote(same) & (cls_c < 0)] = ci
    found: dict[tuple[int, int], tuple[int, int]] = {}      # (mid row, cell) -> (class, sat row)
    for c in range(CELLS):
        rows = np.flatnonzero(light_c[1:n_rows - 3, c]) + 1
        for r in rows:
            if light_c[r - 1, c]:
                continue
            for mid in (r + 1, r + 2):
                if mid + 1 < n_rows and cls_c[mid + 1, c] >= 0:
                    found[(mid, c)] = (int(cls_c[mid + 1, c]), mid + 1)
                    break
    # rows with a head somewhere; read each such row across the whole lane
    rows_seen: dict[int, int] = {}
    for (r, c), (ci, sr) in found.items():
        rows_seen[r] = rows_seen.get(r, 0) + 1
    heads: list[Head] = []
    claimed: list[tuple[int, int, int]] = []          # (row, x0, x1) already read
    for r in sorted(rows_seen, key=lambda r: -rows_seen[r]):
        if r < 4 or r + 5 >= n_rows:
            continue
        row_cls = {c: found[(r, c)][0] for c in range(CELLS) if (r, c) in found}
        for x0, x1 in _segments(k, r, hsv_all, row_cls):
            if any(abs(r - rr) <= 1 and x0 < b and a0 < x1 for rr, a0, b in claimed):
                continue
            core = [c for c in row_cls if x0 <= cell_edge(k, c) + 3 < x1]
            if not core:
                continue
            names = [HEAD_HUES[row_cls[c]][0] for c in core]
            name = max(set(names), key=names.count)
            left = int(round((x0 - cell_edge(k, 0)) / CELL))
            right = int(round((x1 - cell_edge(k, 0)) / CELL))
            left, right = max(0, min(left, min(core))), min(CELLS, max(right, max(core) + 1))
            heads.append(Head(k, r, left, right - left, name, r + 1))
            claimed.append((r, x0, x1))
    return heads


def _cell_kinds(k: int, r: int, hsv_all) -> list[str]:
    """The head kind each cell of row r shows on its saturated row r + 1 (2 of 3 samples agree), or ""."""
    hh, ss, vv = hsv_all
    out = []
    for c in range(CELLS):
        e = int(cell_edge(k, c))
        xs = [e + 2, e + 3, e + 4]
        names = [HEAD_HUES[i][0] if i >= 0 and ss[r + 1, x] >= 0.38 and vv[r + 1, x] >= 0.42 else ""
                 for i, x in zip(head_class(hh[r + 1, xs]), xs)]
        best = max(set(names), key=names.count)
        out.append(best if best and names.count(best) >= 2 else "")
    return out


def _segments(k: int, r: int, hsv_all, row_cls: dict[int, int]) -> list[tuple[int, int]]:
    """Bars drawn on row r (the head's middle row) of column k: pixel ranges [x0, x1), split where two bars' end
    caps meet or where the colour changes from one kind of head to another. Cells between two kinds that show
    neither (a flick's grey arrows) go to the flick."""
    is_bar = _bar_mask(k, r, hsv_all)
    kinds = _cell_kinds(k, r, hsv_all)
    lo = int(cell_edge(k, 0))
    out = []
    for a0, a1 in _runs(is_bar):
        cuts = [lo + a0]
        inside = [c for c in range(CELLS) if lo + a0 <= cell_edge(k, c) + 3 <= lo + a1]
        for c in inside[1:]:
            xb = int(round(cell_edge(k, c)))
            if not lo + a0 + 3 <= xb <= lo + a1 - 3:
                continue
            left = next((kinds[j] for j in range(c - 1, inside[0] - 1, -1) if kinds[j]), "")
            right = next((kinds[j] for j in range(c, inside[-1] + 1) if kinds[j]), "")
            if left and right and left != right:
                # cut at the boundary between the last cell of one kind and the first of the other
                first_right = next(j for j in range(c, inside[-1] + 1) if kinds[j])
                last_left = next(j for j in range(c - 1, inside[0] - 1, -1) if kinds[j])
                if right == "flick":
                    at = last_left + 1
                elif left == "flick":
                    at = first_right
                else:
                    at = (last_left + 1 + first_right + 1) // 2 if first_right > last_left + 1 else first_right
                if at == c:
                    cuts.append(xb)
                    continue
            if _is_cap(k, c, r - 1, r + 2, hsv_all):
                cuts.append(xb)
        cuts.append(lo + a1 + 1)
        cuts = sorted(set(cuts))
        for c0, c1 in zip(cuts, cuts[1:]):
            if c1 - c0 >= 4:
                out.append((c0, c1))
    return out


def _bar_mask(k: int, r: int, hsv_all) -> np.ndarray:
    """Pixels of the playfield that belong to a bar whose middle row is r: bright on the bar's middle and saturated
    rows and brighter there than above or below the bar (so hold and slide bodies running through the row are not
    bars; AIR arrows sit on the rows above). Measure and beat lines through the row are not bars either."""
    hh, ss, vv = hsv_all
    lo, hi = int(cell_edge(k, 0)), int(cell_edge(k, CELLS))
    xs = np.arange(lo, hi)
    rows = []
    for row in (r, r + 1):
        v_row, s_row = vv[row, xs], ss[row, xs]
        grey = s_row < 0.12
        if grey.sum() >= 20:
            q = np.round(v_row[grey], 2)
            vals, counts = np.unique(q, return_counts=True)
            line_v = vals[counts.argmax()]
            if counts.max() >= 15:
                v_row = np.where(grey & (np.abs(v_row - line_v) <= 0.03), 0.0, v_row)
        rows.append(v_row)
    top = np.maximum(rows[0], rows[1])
    outside = np.minimum(vv[r - 3, xs], vv[r + 4, xs])
    return (top >= 0.4) & (top >= outside + 0.12)


def _is_cap(k: int, boundary: int, light_row: int, dark_row: int, hsv_all) -> bool:
    """Two bars side by side: their end caps make a darker notch at the cell boundary, deepest in the light row
    (measured on real pictures: the light row's brightness drops by 0.38 or more at 3 in 4 such boundaries, and by
    less than 0.03 at 19 in 20 boundaries inside one bar). An AIR arrow can cover the light row; the notch then
    still shows in the bar's dark bottom row."""
    xb = int(round(cell_edge(k, boundary)))
    notch = range(xb - 2, xb + 2)
    inner = [int(cell_edge(k, boundary - 1)) + 3, int(cell_edge(k, boundary)) + 3]
    hh, ss, vv = hsv_all
    v_drop = min(vv[light_row, x] for x in inner) - min(vv[light_row, x] for x in notch)
    dark_ref = min(vv[dark_row, x] for x in inner)
    dark_min = min(vv[dark_row, x] for x in notch)
    return bool(v_drop >= 0.2 or (dark_ref >= 0.4 and dark_min <= 0.3 and dark_ref - dark_min >= 0.25))


# ------------------------------------------------------------------------------------------------ reading

GRID = (1, 2, 4, 3, 8, 6, 16, 12, 32, 24, 48, 96)    # beat subdivisions, simplest first (c2s: 96 ticks per beat)


def snap_beat(beat: float, px_per_beat: float, tol_px: float = 1.5) -> Fraction:
    """The simplest beat on the c2s grid within `tol_px` pixels of a measured position (heads drift by a pixel
    between columns of one picture), else the nearest 1/96."""
    for d in GRID:
        f = Fraction(round(beat * d), d)
        if abs(float(f) - beat) * px_per_beat <= tol_px:
            return f
    return Fraction(round(beat * 96), 96)


@dataclass
class PictureReading:
    """What a merged picture shows, in picture beats (0 = the first drawn measure line; which c2s bar that is
    varies by chart). Heads include every bar-shaped sprite: slide nodes and ends and hold ends are drawn as bars
    too, and are not told apart from heads yet."""

    timeline: Timeline
    heads: list[Head]

    def measures(self) -> list[tuple[Fraction, Fraction]]:
        """(start beat, length in beats) of each drawn measure."""
        return [(m.start, m.beats) for m in self.timeline.measures]


def read_picture(data: bytes | np.ndarray) -> PictureReading:
    """Read a merged picture: its file bytes, or an RGBA array (height, width, 4)."""
    a = data if isinstance(data, np.ndarray) else load_rgba(data)
    tl = Timeline(find_columns(a))
    hs = hsv(a[:, :, :3])
    heads: list[Head] = []
    for k in range(len(tl.columns)):
        if k not in tl._points:
            continue
        for h in find_heads(a, k, hs):
            h.raw_beat, h.px_per_beat = tl.beat_at(k, h.row)
            h.beat = snap_beat(h.raw_beat, h.px_per_beat)
            # neighbouring columns overlap by a few rows: a head drawn twice is one note
            if not any(o.column != k and o.beat == h.beat and (o.cell, o.width) == (h.cell, h.width) for o in heads):
                heads.append(h)
    heads.sort(key=lambda h: (h.beat, h.cell))
    return PictureReading(tl, heads)


# What each c2s note looks like as a head; ExHOLD and ExSLIDE start with an ExTAP-coloured head.
HEAD_OF_KIND = {"tap": "tap", "ex_tap": "ex", "flick": "flick", "hold": "hold", "slide": "slide"}


def truth_heads(chart) -> list[tuple[Fraction, int, int, str, str]]:
    """(beat, cell, width, head colour, note kind) of every note of an exact chart that is drawn as a head."""
    out = []
    for n in chart.notes:
        cls = HEAD_OF_KIND.get(n.kind.value)
        if cls is None:
            continue
        if n.extra.get("ex_start"):
            cls = "ex"
        out.append((Fraction(n.beat), int(n.lane), int(n.width), cls, n.kind.value))
    return out


def match_heads(reading: PictureReading, chart, offset: Fraction | None = None) -> dict:
    """Calibration against an exact chart of the same picture: per note kind, how many heads were read exactly
    (beat, cell, width and colour), with the wrong colour, or not at all. `offset` (chart beat = picture beat +
    offset) is searched over whole beats 0-16 when not given. Unmatched picture heads are counted as `extra`
    (most are slide nodes and ends and hold ends)."""
    want = truth_heads(chart)
    if offset is None:
        best = None
        for off in range(17):
            got = _match(reading.heads, want, Fraction(off))
            score = sum(v.get("ok", 0) for k, v in got.items() if k != "extra")
            if best is None or score > best[0]:
                best = (score, Fraction(off), got)
        assert best is not None
        offset, result = best[1], best[2]
    else:
        result = _match(reading.heads, want, offset)
    result["offset"] = offset
    return result


def _match(heads: list[Head], want, offset: Fraction) -> dict:
    by_key: dict[tuple[Fraction, int, int], list[int]] = {}
    for i, h in enumerate(heads):
        by_key.setdefault((h.beat + offset, h.cell, h.width), []).append(i)
    used: set[int] = set()
    out: dict[str, dict[str, int]] = {}
    for beat, cell, width, cls, kind in want:
        cands = [i for i in by_key.get((beat, cell, width), []) if i not in used]
        right = [i for i in cands if heads[i].cls == cls]
        if right:
            used.add(right[0])
            res = "ok"
        elif cands:
            used.add(cands[0])
            res = "wrong_kind"
        else:
            res = "missing"
        d = out.setdefault(kind, {})
        d[res] = d.get(res, 0) + 1
    out["extra"] = {"heads": len(heads) - len(used)}
    return out

