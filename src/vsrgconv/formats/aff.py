"""Arcaea's own chart format (.aff): text, one event per line, times in **integer milliseconds**. Exact.

Format (from ArcCreate's reader, Arcthesia/ArcCreate `Assets/Scripts/ChartFormat/Aff/`, checked against every
official file; see docs/sources/arcaea.md):

    AudioOffset:214                      header lines `Key:value` until a line `-`
    TimingPointDensityFactor:2           (optional) divides the hold/arc combo tick interval
    -
    timing(t,bpm,beats);                 tempo from t; beats = beats per bar line (0 hides bar lines); bpm <= 0 and
                                         huge values are visual effects (the floor stops, runs backwards, jumps)
    (t,lane);                            floor tap; lane 1-4, or 0-5 on a 6-lane chart (scenecontrol enwidenlanes)
    hold(t1,t2,lane);                    floor hold
    arc(t1,t2,x1,x2,ease,y1,y2,color,fx,trace[,smooth])[arctap(t),...];
                                         sky arc. x: 0 and 1 are the lane 1|2 and 3|4 boundaries (the floor spans
                                         -0.5..1.5); y: 0 floor, 1 top. ease: s b si so sisi siso sosi soso (first
                                         pair x, second y; si = sine, fast start; so = 1-cos, slow start; b = cubic).
                                         color 0 blue, 1 red, 2 green, 3 grey. fx: hit sound ("none"). trace: false =
                                         a playable arc; true = a trace ("skyline"), no input, carries arctaps;
                                         designant = the trace variant used by Designant. smooth: render resolution.
    timinggroup(props){ ... };           events with their own timing list; props joined by "_": noinput (not
                                         judged), fadingholds, anglex<n>, angley<n>, tracecol<rgb>, ...
    scenecontrol(t,type,...); camera(t,...);   visual effects

IR mapping (`aff_to_chart`):
- Beats come from the main timing list (group 0): beat(t) = segment beat + (t - t0) * bpm / 60000 with `Fraction`s of
  the integer ms and the decimal BPM text, so `TimingMap.ms_at(beat)` gives back the file's ms (+ AudioOffset) exactly
  after rounding; `aff_to_chart` checks this for every note. A segment with BPM <= 0 does not move the beat axis
  backwards: the axis keeps the previous positive tempo and the effect becomes a `ScrollChange` (speed = bpm / axis
  bpm, so 0 or negative), back to 1.0 at the next normal segment. Timing groups' own timing lists only change scroll
  and combo ticks; they are kept raw in meta.source["aff"]["groups"].
- Lane surface: 4 units (lane = aff lane - 1) unless a floor note uses lane 0 or 5, then 6 units (lane = aff lane).
  Arc positions use the same units: centre = 2x + 2 - first_lane; arc/arctap notes are 1 lane wide around it.
- tap -> TAP, hold -> HOLD, playable arc -> AIR_HOLD (path: one point at the end, easing by curve shape; x, y, ease,
  color in extra), arctap -> AIR (x, y on its trace in extra). Traces, zero-length arcs (instant moves between arcs),
  Designant.'s arctaps (no combo) and everything in a `noinput` group -> OTHER with extra["aff"] naming what it was.
  Notes outside group 0 have extra["group"]. An arc that continues another has extra["continues"].
- Scene control and camera lines are kept verbatim in meta.source["aff"].

Combo (`combo_counts`, the in-game note count): 1 per tap and arctap; holds and arcs count ticks. With bpm the group's
|BPM| at the note start, interval = (60000 if bpm >= 255 else 30000) / bpm / TimingPointDensityFactor and
n = floor(duration / interval), exact: a hold gives n - 1 (at least 1); an arc that starts a chain likewise; an arc that
continues one (a playable arc ends within CHAIN_TOLERANCE_MS of its start at the same x and y, in any group or colour)
gives n (at least 1). Traces, zero-length arcs, zero-BPM starts, Designant.'s arctaps and `noinput` groups give 0.
This is ArcCreate's rule (Hold.cs, Arc.Judgement.cs, ArcNoteGroup.cs) with two changes fitted to the published counts:
chains join across up to 10 ms, not 1 (arc ends are rounded ms; with 1 ms 1658 of the 1833 charts with a published
count match, with 3-15 ms about 1808), and designant arctaps do not count. Exact tick maths matches 5 more charts
than 32-bit floats.
"""
from __future__ import annotations

import bisect
import math
import re
import struct
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ..model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, PathPoint, ScrollChange, TimingMap

CHAIN_TOLERANCE_MS = 10    # an arc continues another whose end is this close (see the docstring)
EASINGS = ("s", "b", "si", "so", "sisi", "siso", "sosi", "soso")
# PathPoint easing by the shape of the x curve: "si" (sine) starts fast = conventional ease-out; "so" (1 - cos)
# starts slow = ease-in; "b" (cubic, flat at both ends) = in-out.
PATH_EASING = {"s": "linear", "b": "inout", "si": "out", "so": "in", "sisi": "out", "siso": "out", "sosi": "in",
               "soso": "in"}

_TIMING = re.compile(r"timing\(\s*(-?\d+)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\);?$")
_TAP = re.compile(r"\(\s*(-?\d+)\s*,\s*([^,)]+?)\s*\);?$")
_HOLD = re.compile(r"hold\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*([^,)]+?)\s*\);?$")
_ARC = re.compile(r"arc\(([^)]*)\)\s*(?:\[(.*)\])?\s*;?$")
_ARCTAP = re.compile(r"arctap\(\s*(-?\d+)\s*(?:,[^)]*)?\)")
_GROUP = re.compile(r"timinggroup\((.*)\)\s*\{$")


@dataclass
class Timing:
    t: int
    bpm: Fraction
    beats: Fraction
    bpm_text: str
    beats_text: str

    def raw(self) -> list[Any]:
        return [self.t, self.bpm_text, self.beats_text]


@dataclass
class Group:
    index: int
    props: str = ""
    timings: list[Timing] = field(default_factory=list)

    @property
    def options(self) -> set[str]:
        return {p.strip().lower() for p in re.split(r"[_,]", self.props) if p.strip()}

    @property
    def noinput(self) -> bool:
        return "noinput" in self.options

    def bpm_at(self, t: int) -> Fraction:
        """The group's BPM in force at t (the first one also covers times before it)."""
        cur = self.timings[0].bpm
        for tm in self.timings:
            if tm.t <= t:
                cur = tm.bpm
            else:
                break
        return cur


@dataclass
class Tap:
    t: int
    lane: int | Fraction
    group: int = 0


@dataclass
class Hold:
    t: int
    end: int
    lane: int | Fraction
    group: int = 0


@dataclass
class Arc:
    t: int
    end: int
    x1: float
    x2: float
    ease: str
    y1: float
    y2: float
    color: int
    fx: str
    trace: str                    # "false" (playable), "true" (trace), "designant"
    smooth: str | None = None     # optional 11th field
    arctaps: list[int] = field(default_factory=list)
    group: int = 0
    line: int = 0

    @property
    def is_trace(self) -> bool:
        return self.trace != "false"


@dataclass
class Aff:
    header: dict[str, str] = field(default_factory=dict)
    groups: list[Group] = field(default_factory=lambda: [Group(0)])
    taps: list[Tap] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    scenecontrols: list[tuple[int, str]] = field(default_factory=list)   # (group, line)
    cameras: list[tuple[int, str]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def audio_offset(self) -> int:
        try:
            return int(self.header.get("AudioOffset", "0").strip())
        except ValueError:
            return 0

    @property
    def density(self) -> Fraction:
        raw = self.header.get("TimingPointDensityFactor", self.header.get("TimingPointsDensityFactor", "1"))
        try:
            d = Fraction(raw.strip())
        except (ValueError, ZeroDivisionError):
            return Fraction(1)
        return d if d > 0 else Fraction(1)


def _lane(text: str) -> int | Fraction:
    v = Fraction(text.strip())
    return int(v) if v.denominator == 1 else v


def parse_aff(text: str) -> Aff:
    aff = Aff()
    lines = text.lstrip("﻿").splitlines()
    i = 0
    # header: until "-" (a file whose header has no "-" starts its events at the first event-looking line)
    while i < len(lines):
        s = lines[i].strip()
        i += 1
        if s == "-":
            break
        if not s:
            continue
        if s.startswith(("timing(", "(", "hold(", "arc(")):
            aff.errors.append("no '-' line after the header")
            i -= 1
            break
        key, _, value = s.partition(":")
        aff.header[key.strip()] = value.strip()
    group = 0
    for n in range(i, len(lines)):
        s = lines[n].strip()
        if not s:
            continue
        try:
            if s.startswith("timing("):
                m = _TIMING.match(s)
                if not m:
                    raise ValueError("bad timing")
                aff.groups[group].timings.append(Timing(int(m[1]), Fraction(m[2]), Fraction(m[3]), m[2], m[3]))
            elif s.startswith("("):
                m = _TAP.match(s)
                if not m:
                    raise ValueError("bad tap")
                aff.taps.append(Tap(int(m[1]), _lane(m[2]), group))
            elif s.startswith("hold("):
                m = _HOLD.match(s)
                if not m:
                    raise ValueError("bad hold")
                aff.holds.append(Hold(int(m[1]), int(m[2]), _lane(m[3]), group))
            elif s.startswith("arc("):
                m = _ARC.match(s)
                if not m:
                    raise ValueError("bad arc")
                a = [x.strip() for x in m[1].split(",")]
                if len(a) not in (10, 11):
                    raise ValueError(f"arc has {len(a)} fields")
                aff.arcs.append(Arc(int(a[0]), int(a[1]), float(a[2]), float(a[3]), a[4], float(a[5]), float(a[6]),
                                    int(a[7]), a[8], a[9].lower(), a[10] if len(a) > 10 else None,
                                    [int(t) for t in _ARCTAP.findall(m[2] or "")], group, n + 1))
            elif s.startswith("timinggroup("):
                m = _GROUP.match(s)
                if not m:
                    raise ValueError("bad timinggroup")
                if group != 0:
                    aff.errors.append(f"line {n + 1}: timinggroup inside a timinggroup")
                aff.groups.append(Group(len(aff.groups), m[1].strip()))
                group = len(aff.groups) - 1
            elif s.startswith("}"):
                group = 0
            elif s.startswith("scenecontrol("):
                aff.scenecontrols.append((group, s))
            elif s.startswith("camera("):
                aff.cameras.append((group, s))
            else:
                aff.unknown.append(s)
        except (ValueError, ZeroDivisionError, IndexError) as e:
            aff.errors.append(f"line {n + 1}: {e}: {s[:80]}")
    for g in aff.groups:
        g.timings.sort(key=lambda tm: tm.t)      # stable: of two timings at one t, the later line wins
    return aff


# ------------------------------------------------------------------------------------------------ beat axis

@dataclass
class _Segment:
    t: int
    beat: Fraction
    bpm: Fraction        # beat-axis tempo (always > 0)
    real: Fraction       # the file's tempo (may be <= 0)


class BeatAxis:
    """ms -> beats through the main timing list, exactly. See the module docstring for BPM <= 0."""

    def __init__(self, timings: list[Timing], fallback_bpm: Fraction = Fraction(120)) -> None:
        last: dict[int, Timing] = {}
        for tm in timings:
            last[tm.t] = tm                       # later line wins at the same t
        ordered = [last[t] for t in sorted(last)]
        if not ordered:
            ordered = [Timing(0, fallback_bpm, Fraction(4), str(fallback_bpm), "4")]
        positive = [tm.bpm for tm in ordered if tm.bpm > 0]
        axis = positive[0] if positive else fallback_bpm
        self.segments: list[_Segment] = []
        beat = ordered[0].t * axis / 60000       # beat 0 is t = 0: the first segment also covers earlier times
        prev_t = ordered[0].t
        for tm in ordered:
            if self.segments:
                beat += (tm.t - prev_t) * self.segments[-1].bpm / 60000
            if tm.bpm > 0:
                axis = tm.bpm
            self.segments.append(_Segment(tm.t, beat, axis, tm.bpm))
            prev_t = tm.t
        self._starts = [s.t for s in self.segments]

    def segment(self, t: int) -> _Segment:
        i = bisect.bisect_right(self._starts, t) - 1
        return self.segments[max(i, 0)]

    def beat(self, t: int) -> Fraction:
        s = self.segment(t)
        return s.beat + (t - s.t) * s.bpm / 60000

    def timing_map(self, offset_ms: int, timings: list[Timing]) -> TimingMap:
        tm = TimingMap(offset_ms=float(offset_ms), bpms=[], meters=[], scrolls=[])
        for i, s in enumerate(self.segments):
            b = Fraction(0) if i == 0 else s.beat
            if not tm.bpms or tm.bpms[-1].bpm != float(s.bpm):
                tm.bpms.append(BpmChange(b, float(s.bpm)))
            speed = s.real / s.bpm
            current = tm.scrolls[-1].speed if tm.scrolls else 1.0
            if float(speed) != current:
                tm.scrolls.append(ScrollChange(b, float(speed)))
        tm.meters = _meters(self, timings)
        return tm


def _meters(axis: BeatAxis, timings: list[Timing]) -> list[MeterChange]:
    """A metre change wherever a main timing line's `beats` changes to a value that is n/4, n/8 or n/16. Arcaea
    restarts bar lines at every timing line, as an osu! red line does."""
    out: list[MeterChange] = []
    for tm in timings:
        if tm.beats <= 0:
            continue
        for den in (4, 8, 16):
            num = tm.beats * den / 4
            if num.denominator == 1 and 1 <= num <= 64:
                b = Fraction(0) if not out else axis.beat(tm.t)
                if not out or (out[-1].numerator, out[-1].denominator) != (int(num), den):
                    out.append(MeterChange(b, int(num), den))
                break
    return out or [MeterChange(Fraction(0))]


# ------------------------------------------------------------------------------------------------ arcs

def ease_x(start: float, end: float, p: float, ease: str) -> float:
    return _ease(start, end, p, {"s": "s", "b": "b", "si": "i", "sisi": "i", "siso": "i", "so": "o", "sosi": "o",
                                 "soso": "o"}.get(ease, "s"))


def ease_y(start: float, end: float, p: float, ease: str) -> float:
    return _ease(start, end, p, {"s": "s", "si": "s", "so": "s", "b": "b", "sisi": "i", "sosi": "i", "siso": "o",
                                 "soso": "o"}.get(ease, "s"))


def _ease(a: float, b: float, p: float, kind: str) -> float:
    if kind == "i":
        return a + (b - a) * math.sin(math.pi / 2 * p)
    if kind == "o":
        return a + (b - a) * (1 - math.cos(math.pi / 2 * p))
    if kind == "b":
        q = 1 - p
        return q ** 3 * a + 3 * q * q * p * a + 3 * q * p * p * b + p ** 3 * b
    return a + (b - a) * p


def arc_links(arcs: list[Arc], same_group: bool = False, tolerance: int = CHAIN_TOLERANCE_MS) -> list[bool]:
    """For each arc: does it continue another one (an arc of the same trace-ness ending within `tolerance` ms of its
    start at the same x and y, in any timing group unless `same_group`)? An arc that continues another has no head and
    counts its first tick. ArcCreate's rule uses 1 ms; 10 fits the published counts best (module docstring)."""
    ends: dict[tuple[bool, float, float], list[tuple[int, int, int]]] = {}
    for i, a in enumerate(arcs):
        ends.setdefault((a.trace == "false", a.x2, a.y2), []).append((a.end, a.group, i))
    out = []
    for i, a in enumerate(arcs):
        cands = ends.get((a.trace == "false", a.x1, a.y1), [])
        out.append(any(abs(e - a.t) <= tolerance and j != i and (not same_group or g == a.group) for e, g, j in cands))
    return out


# ------------------------------------------------------------------------------------------------ combo

def _f32(v: Fraction | float) -> float:
    return struct.unpack("f", struct.pack("f", float(v)))[0]


def _ticks(duration: int, bpm: Fraction, density: Fraction, exact: bool) -> int:
    """floor(duration / interval), interval = (60000 if bpm >= 255 else 30000) / bpm / density."""
    k = 60000 if bpm >= 255 else 30000
    if exact:
        return math.floor(Fraction(duration) * bpm * density / k)
    interval = k / _f32(bpm) / _f32(density)          # the game reads BPM and the factor as 32-bit floats
    return int(duration / interval)


def combo_counts(aff: Aff, exact: bool = True, same_group_chains: bool = False,
                 tolerance: int = CHAIN_TOLERANCE_MS) -> dict[str, int]:
    """The in-game combo (Arcaea's published note count), by note type. See the module docstring for the rule."""
    groups = aff.groups
    main = groups[0]
    density = aff.density

    def bpm_at(g: int, t: int) -> Fraction:
        grp = groups[g] if g < len(groups) and groups[g].timings else main
        return abs(grp.bpm_at(t)) if grp.timings else Fraction(0)

    out = {"tap": 0, "hold": 0, "arc": 0, "arctap": 0}
    for tap in aff.taps:
        out["tap"] += not groups[tap.group].noinput
    for h in aff.holds:
        bpm = bpm_at(h.group, h.t)
        if groups[h.group].noinput or bpm == 0 or h.end <= h.t:
            continue
        n = _ticks(h.end - h.t, bpm, density, exact)
        out["hold"] += 1 if n <= 1 else n - 1
    links = arc_links(aff.arcs, same_group_chains, tolerance)
    for a, continues in zip(aff.arcs, links):
        if groups[a.group].noinput:
            continue
        if a.trace != "designant":                     # Designant.'s arctaps give no combo
            out["arctap"] += len(a.arctaps)
        bpm = bpm_at(a.group, a.t)
        if a.is_trace or a.end <= a.t or bpm == 0:
            continue
        n = _ticks(a.end - a.t, bpm, density, exact)
        first = 0 if continues else 1
        out["arc"] += 1 if n <= first else n - first
    out["total"] = sum(out.values())
    return out


# ------------------------------------------------------------------------------------------------ to the IR

def aff_to_chart(text: str, meta: ChartMeta) -> Chart:
    aff = parse_aff(text)
    warnings = list(aff.errors)
    main = aff.groups[0]
    if not main.timings:
        raise ValueError("no timing line in the main group")
    axis = BeatAxis(main.timings)
    timing = axis.timing_map(aff.audio_offset, main.timings)
    effects = sum(1 for tm in main.timings if tm.bpm <= 0)
    if effects:
        warnings.append(f"{effects} main timing lines with BPM <= 0 (visual effects) kept as scroll changes; "
                        f"the beat axis keeps the previous tempo there")
    if aff.unknown:
        warnings.append(f"{len(aff.unknown)} unknown lines ignored, e.g. {aff.unknown[0][:60]!r}")

    floor_lanes = [x.lane for x in aff.taps] + [x.lane for x in aff.holds]
    six = any(not 1 <= lane <= 4 for lane in floor_lanes)
    first_lane = 0 if six else 1

    def centre(x: float) -> float:
        return 2 * x + 2 - first_lane

    def lane(v: int | Fraction) -> int | float:
        v = v - first_lane
        return v if isinstance(v, int) else float(v)

    notes: list[Note] = []

    def add(n: Note, group: int, aff_kind: str) -> None:
        if group:
            n.extra["group"] = group
            if aff.groups[group].noinput:
                n.extra["noinput"] = True
                n.kind = NoteKind.OTHER
        if n.kind == NoteKind.OTHER:
            n.extra["aff"] = aff_kind
        notes.append(n)

    for tap in aff.taps:
        add(Note(axis.beat(tap.t), lane(tap.lane)), tap.group, "tap")
    zero_holds = 0
    for h in aff.holds:
        b = axis.beat(h.t)
        dur = axis.beat(h.end) - b
        if dur <= 0:
            zero_holds += 1
            dur = Fraction(0)
        add(Note(b, lane(h.lane), 1.0, NoteKind.HOLD, dur), h.group, "hold")
    if zero_holds:
        warnings.append(f"{zero_holds} holds of zero length (kept as holds with duration 0)")

    links = arc_links(aff.arcs)
    reversed_arcs = 0
    for a, continues in zip(aff.arcs, links):
        b = axis.beat(a.t)
        span = axis.beat(a.end) - b
        if span < 0:
            reversed_arcs += 1
        extra: dict[str, Any] = {"x": [a.x1, a.x2], "y": [a.y1, a.y2], "ease": a.ease, "color": a.color}
        if a.fx != "none":
            extra["fx"] = a.fx
        if a.smooth is not None:
            extra["smooth"] = a.smooth
        if continues:
            extra["continues"] = True
        if a.ease not in PATH_EASING:
            warnings.append(f"line {a.line}: unknown arc easing {a.ease!r}, read as linear")
        playable = not a.is_trace and a.end > a.t
        n = Note(b, centre(a.x1) - 0.5, 1.0, NoteKind.AIR_HOLD if playable else NoteKind.OTHER, max(span, Fraction(0)),
                 path=[PathPoint(span, centre(a.x2) - 0.5, 1.0, PATH_EASING.get(a.ease, "linear"))], extra=extra)
        add(n, a.group, "arc" if not a.is_trace else a.trace if a.trace != "true" else "trace")
        for t in a.arctaps:
            p = min(max((t - a.t) / (a.end - a.t), 0.0), 1.0) if a.end > a.t else 0.0
            x = round(ease_x(a.x1, a.x2, p, a.ease), 6)
            y = round(ease_y(a.y1, a.y2, p, a.ease), 6)
            tap_extra: dict[str, Any] = {"x": x, "y": y}
            if not a.is_trace:
                tap_extra["on_arc"] = True
            kind = NoteKind.AIR
            if a.trace == "designant":               # no combo: not an ordinary arctap
                kind, tap_extra["designant"] = NoteKind.OTHER, True
            add(Note(axis.beat(t), round(centre(x) - 0.5, 6), 1.0, kind, extra=tap_extra), a.group, "arctap")
    if reversed_arcs:
        warnings.append(f"{reversed_arcs} arcs end before they start (duration 0; the path point keeps the "
                        f"negative span)")

    chart = Chart(meta=meta, lanes=6 if six else 4, timing=timing, notes=notes, warnings=warnings)
    # every note time must give back its file ms (the IR keeps ms only through the timing map)
    times = {x.t for x in aff.taps} | {t for h in aff.holds for t in (h.t, h.end)}
    times |= {t for a in aff.arcs for t in (a.t, a.end, *a.arctaps)}
    off = sorted(t for t in times if round(timing.ms_at(axis.beat(t))) != t + aff.audio_offset)
    if off:
        warnings.append(f"{len(off)} note times do not round-trip to their file ms through the timing map, "
                        f"e.g. {off[0]}")
    src: dict[str, Any] = {
        "audio_offset": aff.audio_offset,
        "lanes": "0-5" if six else "1-4",
        "timing": [tm.raw() for tm in main.timings],
    }
    if "TimingPointDensityFactor" in aff.header or "TimingPointsDensityFactor" in aff.header:
        src["density"] = str(aff.density)
    extra_header = {k: v for k, v in aff.header.items() if k not in ("AudioOffset", "TimingPointDensityFactor",
                                                                     "TimingPointsDensityFactor")}
    if extra_header:
        src["header"] = extra_header
    if len(aff.groups) > 1:
        src["groups"] = [{"index": g.index, "props": g.props, "timing": [tm.raw() for tm in g.timings]}
                         for g in aff.groups[1:]]
    if aff.scenecontrols:
        src["scenecontrol"] = [[g, s] for g, s in aff.scenecontrols]
    if aff.cameras:
        src["camera"] = [[g, s] for g, s in aff.cameras]
    if aff.unknown:
        src["unknown"] = aff.unknown
    src["combo"] = combo_counts(aff)
    chart.meta.source["aff"] = src
    return chart.sort()
