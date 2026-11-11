"""Bestdori chart JSON (BanG Dream! Girls Band Party!) -> `Chart`, exact. See docs/sources/bandori.md.

A chart is a JSON list of entries (the MaaBanGDream mirror wraps it as `{"chart": [...], "source": ...}`):

    {"type": "BPM", "beat": 0, "bpm": 185}
    {"type": "System", "beat": 0, "data": "bgm001.wav"}            # also cmd_fever_ready/start/end.wav
    {"type": "Single", "beat": 10, "lane": 5, "flick"?, "skill"?, "charge"?}
    {"type": "Directional", "beat": 15, "lane": 2, "direction": "Left"|"Right", "width": 3}
    {"type": "Long",  "connections": [{"beat", "lane", "flick"?, "skill"?, "charge"?}, ...]}   # always 2 points
    {"type": "Slide", "connections": [{"beat", "lane", "flick"?, "skill"?, "charge"?, "hidden"?}, ...]}

Lanes are 0..6, left to right (checked over all 1,805 charts of the mirror; only hidden slide points are fractional,
-0.5..6.5). A lane is the IR's left edge on a 7-unit surface, width 1. A Directional's `lane` is its anchor tile: the
rightmost tile for Left, the leftmost for Right (Bestdori's simulator draws tile i at lane + i * (Left ? -1 : 1)).

IR mapping:
- Single -> tap, or flick (`extra.direction` "up"); `skill` and `charge` kept in `extra`. `charge` marks the
  fever-chance notes (between cmd_fever_ready and cmd_fever_start) that fill the multi-live fever gauge.
- Directional -> flick, `extra.direction` "left"/"right", `width` in lanes, `lane` its left edge.
- Long -> hold. Slide -> slide whose `path` holds every later point (offset from the head, lane, easing "linear" for a
  judged tick, "control" for a hidden point), or a hold (`extra.static_slide`) when no point changes lane; its judged
  ticks then go in `extra.ticks`. Flags on a line's points: head -> `extra.<flag>`, tail -> `extra.<flag>_end`
  (`flick_end` is the end flick), judged ticks -> `extra.<flag>_ticks` (offsets).
  As in Bestdori's simulator, hidden points at either end are trimmed, a line with one visible point is played as a
  Single (`extra.line`), and a line with none is dropped.
- BPM -> `BpmChange`; beat 0 is the start of the audio (offset 0). There is no metre: 4/4 throughout.
- System -> `meta.source.bestdori_system` as [beat, data] pairs; a `bgm*.wav`/`music_*.wav` entry is `meta.audio`.

Beats are JSON floats. `exact_beat` turns each into a Fraction by this rule:
1. the nearest 1/192 beat, if the float is within 1e-9 of it (99.99 % of beats);
2. else the fraction with the smallest denominator up to 1000 within 1e-9: tuplets such as 336.6 = 1683/5 or
   290.11764705882354 = 4932/17 (the game's charts come from measure-slot files, so any slot count occurs);
3. else the smallest-denominator fraction up to 192 within 1e-6 (a float printed with few digits, 10.333333 -> 31/3);
4. else the nearest 1/192 beat, and the chart gets a warning with the count and the largest error.
Over the whole mirror, every beat resolves in steps 1-2 with an error under 2e-15 beats.

Combo (Bandori's note count): every Single and Directional is 1, and every visible point of a Long or Slide is 1
(head, each judged tick, tail); hidden points are 0. This reproduces Bestdori's count on all 1,805 charts.
"""
from __future__ import annotations

import json
import re
from fractions import Fraction
from typing import Any

from ..model import (
    BpmChange,
    Chart,
    ChartMeta,
    MeterChange,
    Note,
    NoteKind,
    PathPoint,
    TimingMap,
)

LANES = 7
GRID = 192
MAX_DENOMINATOR = 1000
EXACT_TOLERANCE = 1e-9
ROUNDED_TOLERANCE = 1e-6
FLAGS = ("flick", "skill", "charge")
AUDIO_RE = re.compile(r"^(bgm|music_).*\.wav$")


def exact_beat(x: float | int) -> tuple[Fraction, float]:
    """A Bestdori beat as an exact Fraction, and how far the float was from it (see the module docstring)."""
    if isinstance(x, int):
        return Fraction(x), 0.0
    x = float(x)
    grid = Fraction(round(x * GRID), GRID)
    err = abs(float(grid) - x)
    if err <= EXACT_TOLERANCE:
        return grid, err
    for tol, max_den in ((EXACT_TOLERANCE, MAX_DENOMINATOR), (ROUNDED_TOLERANCE, GRID)):
        f = _simplest(x, tol, max_den)
        if f is not None:
            return f, abs(float(f) - x)
    return grid, err


def _simplest(x: float, tol: float, max_den: int) -> Fraction | None:
    for q in range(1, max_den + 1):
        p = round(x * q)
        if abs(p / q - x) <= tol:
            return Fraction(p, q)
    return None


def _lane(v: Any) -> float:
    r = round(float(v), 6)            # drops float noise such as 0.35000000000000003
    return int(r) if r == int(r) else r


def bestdori_entries(data: Any) -> list[dict[str, Any]]:
    """The entry list from raw JSON (bytes/str), a list, or the MaaBanGDream wrapper {"chart": [...]}."""
    if isinstance(data, (bytes, str)):
        data = json.loads(data)
    if isinstance(data, dict) and isinstance(data.get("chart"), list):
        data = data["chart"]
    if not isinstance(data, list):
        raise ValueError("not a Bestdori chart: expected a list of entries (the old {'notes': ...} form is not read)")
    return data


def _trimmed(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """A line's points sorted by beat, without hidden points at either end (as Bestdori's simulator plays it)."""
    pts = sorted(entry.get("connections") or [], key=lambda p: p.get("beat", 0))
    while pts and pts[0].get("hidden"):
        pts.pop(0)
    while pts and pts[-1].get("hidden"):
        pts.pop()
    return pts


def bestdori_combo(data: Any) -> int:
    """Bandori's combo for a chart, counted from the raw entries."""
    n = 0
    for e in bestdori_entries(data):
        if e.get("type") in ("Single", "Directional"):
            n += 1
        elif e.get("type") in ("Long", "Slide"):
            n += sum(1 for p in _trimmed(e) if not p.get("hidden"))
    return n


def chart_combo(chart: Chart) -> int:
    """Bandori's combo counted from an IR chart made by `bestdori_to_chart` (it keeps every judged point)."""
    n = 0
    for note in chart.notes:
        if note.kind is NoteKind.HOLD:
            n += 2 + len(note.extra.get("ticks", []))
        elif note.kind is NoteKind.SLIDE:
            n += 1 + sum(1 for p in note.path if p.easing != "control")
        elif note.kind in (NoteKind.TAP, NoteKind.FLICK):
            n += 1
    return n


def bestdori_to_chart(data: Any, meta: ChartMeta, lossy: str = "") -> Chart:
    """Parse a Bestdori chart. `lossy` names a copy known to have lost detail; it becomes a warning, and a static
    Slide is then not marked `static_slide` (such copies turn every Long into a Slide)."""
    entries = bestdori_entries(data)
    warnings: list[str] = []
    off_grid: list[float] = []

    def beat(x: Any) -> Fraction:
        f, err = exact_beat(x)
        if err > ROUNDED_TOLERANCE:
            off_grid.append(err)
        return f

    timing = TimingMap(bpms=[], meters=[MeterChange(Fraction(0))])
    notes: list[Note] = []
    system: list[list[Any]] = []
    unknown: list[dict[str, Any]] = []
    dropped = zero_length = 0
    for e in entries:
        t = e.get("type")
        if t == "BPM":
            timing.bpms.append(BpmChange(beat(e["beat"]), float(e["bpm"])))
        elif t == "System":
            system.append([str(beat(e["beat"])), e.get("data")])
        elif t == "Single":
            notes.append(_point_note(beat(e["beat"]), e))
        elif t == "Directional":
            width = int(e.get("width") or 1)
            direction = str(e.get("direction", "")).lower()
            anchor = _lane(e["lane"])
            n = Note(beat(e["beat"]), anchor - (width - 1) if direction == "left" else anchor, width, NoteKind.FLICK,
                     extra={"direction": direction or "up"})
            n.extra.update({f: True for f in ("skill", "charge") if e.get(f)})
            notes.append(n)
        elif t in ("Long", "Slide"):
            note = _line_note(e, t, beat, lossy)
            if note is None:
                dropped += 1
            else:
                zero_length += note.kind in (NoteKind.HOLD, NoteKind.SLIDE) and note.duration == 0
                notes.append(note)
        else:
            unknown.append(e)

    if not any(b.beat == 0 for b in timing.bpms):
        timing.bpms.append(BpmChange(Fraction(0), 120.0))      # what Bestdori's simulator assumes
        warnings.append("no BPM at beat 0; assumed 120 there")
    lanes_out = sorted({n.lane for n in notes if not 0 <= n.lane <= LANES - n.width})
    if lanes_out:
        warnings.append(f"notes outside lanes 0..6 at lane {lanes_out[:5]}")
    if off_grid:
        warnings.append(f"{len(off_grid)} beats are not within 1e-6 of a fraction with denominator <= "
                        f"{MAX_DENOMINATOR}; snapped to 1/{GRID} (largest error {max(off_grid):.3g} beats)")
    if dropped:
        warnings.append(f"{dropped} Long/Slide entries with no visible point dropped")
    if zero_length:
        warnings.append(f"{zero_length} Long/Slide entries start and end on the same beat")
    if unknown:
        kinds: dict[str, int] = {}
        for e in unknown:
            kinds[str(e.get("type"))] = kinds.get(str(e.get("type")), 0) + 1
        warnings.append("not represented: " + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))
    if lossy:
        warnings.append(lossy)

    chart = Chart(meta=meta, lanes=LANES, timing=timing, notes=notes, warnings=warnings)
    if system:
        chart.meta.source["bestdori_system"] = system
        audio = next((d for _, d in system if isinstance(d, str) and AUDIO_RE.match(d)), "")
        if audio and not chart.meta.audio:
            chart.meta.audio = audio
    if unknown:
        chart.meta.source["bestdori_unrepresented"] = unknown
    chart.meta.source["combo"] = bestdori_combo(entries)
    return chart.sort()


def _point_note(b: Fraction, p: dict[str, Any], line: str = "") -> Note:
    n = Note(b, _lane(p["lane"]), 1, NoteKind.FLICK if p.get("flick") else NoteKind.TAP)
    if p.get("flick"):
        n.extra["direction"] = "up"
    n.extra.update({f: True for f in ("skill", "charge") if p.get(f)})
    if line:
        n.extra["line"] = line
    return n


def _line_note(e: dict[str, Any], kind: str, beat, lossy: str) -> Note | None:
    pts = _trimmed(e)
    visible = [p for p in pts if not p.get("hidden")]
    if not visible:
        return None
    if len(visible) == 1:
        p = visible[0]
        return _point_note(beat(p["beat"]), p, line=kind)
    head, tail = pts[0], pts[-1]
    start = beat(head["beat"])
    offsets = [beat(p["beat"]) - start for p in pts]
    extra: dict[str, Any] = {}
    for f in FLAGS:
        if head.get(f):
            extra[f] = True
        if tail.get(f):
            extra[f"{f}_end"] = True
        marked = [str(o) for p, o in zip(pts[1:-1], offsets[1:-1]) if p.get(f) and not p.get("hidden")]
        if marked:
            extra[f"{f}_ticks"] = marked
    if extra.get("flick_end"):
        extra["direction"] = "up"
    lanes = {_lane(p["lane"]) for p in pts}
    if len(lanes) == 1:
        ticks = [str(o) for p, o in zip(pts[1:-1], offsets[1:-1]) if not p.get("hidden")]
        if ticks:
            extra["ticks"] = ticks
        if kind == "Slide" and not lossy:
            extra["static_slide"] = True
        return Note(start, _lane(head["lane"]), 1, NoteKind.HOLD, offsets[-1], extra=extra)
    path = [PathPoint(o, _lane(p["lane"]), 1.0, "control" if p.get("hidden") else "linear")
            for p, o in zip(pts[1:], offsets[1:])]
    return Note(start, _lane(head["lane"]), 1, NoteKind.SLIDE, offsets[-1], path=path, extra=extra)
