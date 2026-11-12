"""Project SEKAI's chart format (SUS, "Sliding Universal Score", as written by Ched): text, exact.

Semantics follow the open-source readers of the official files (NonSpicyBurrito/sonolus-pjsekai-engine and its
successor Next-SEKAI/sonolus-next-sekai-engine `sus/analyze.ts` + `sus/convert.ts`, sevenc-nanashi/usctool,
Sekai-World/pjsekai-scores); docs/sources/sekai.md has the details and the evidence.

Lines (`#` + header + `:` + data; other `#KEY value` lines are metadata):
    #mmm02: n            measure mmm (and on) is n beats long (exact; 4 in every official file)
    #BPMzz: x            BPM definition zz (base 36);  #mmm08: zz..   BPM changes at those positions
    #TILzz: "m't:s, .."  hi-speed (time-scale) group zz: speed s from measure m + t ticks;  #HISPEED zz picks the
                         group for the lines that follow
    #mmm1x: tw..         tap-channel object at lane x (base 36), type t, width w
    #mmm2xy / 3xy / 9xy  hold / slide / guide stream y at lane x (1 start, 2 end, 3 relay, 5 invisible relay)
    #mmm5x: tw..         directional modifier at lane x
Data is a run of 2-char pairs spread evenly over the measure ("00" = nothing). Time is `beat = measure start +
i/n * measure length`, an exact Fraction (ticks_per_beat only matters for #TIL, whose offsets are in ticks).

Lanes: SUS lanes 2..13 are the 12 playable lanes, so IR lane = SUS lane - 2. Lane 0 holds skill markers (tap type 4),
lane 15 the fever markers (type 1 FEVER CHANCE, type 2 SUPER FEVER); these go to meta.source["sus_events"].

Tap-channel types: 1 tap, 2 critical, 3 "tick remove", 4 damage, 5 trace, 6 critical trace, 7 "no judgement",
8 critical + no judgement. On a slide point (same lane and time) a tap-channel object is a modifier, not a note:
2/6/8 make the slide critical, 5/6 make the point a trace, 7/8 hide a start/end (no judgement), 3 turns a relay
into a tick attached to the curve (and deletes an invisible relay). Directional types: 1 flick up, 3 flick up-left,
4 flick up-right (on a tap: flick note; on a slide end: flick end); 2 ease-in, 5/6 ease-out (on a slide point: the
curve of the segment that starts there).

IR:
- tap / ex_tap (critical); flick with extra["direction"]; trace notes are tap/ex_tap/flick with extra["trace"];
  damage is mine. extra["critical"] is set on every critical note.
- slides (#3) are `slide`, or `hold` when every point keeps the start's lane and width. `path` holds the shape points
  after the start (visible relays, invisible relays, end); PathPoint.easing is the curve of the segment that *arrives*
  at the point (linear | in | out). extra["points"] names each path point: tick (visible relay, a combo tick),
  trace (trace relay), hidden (invisible relay, shape only), end. extra["attach"] lists beat offsets of ticks that sit
  on the curve (relay + tap type 3). extra["start"] is tap | trace | hidden, extra["end"] release | trace | hidden,
  extra["end_direction"] a flick end's direction (also on a hidden end, which the flick still makes judged).
- guide slides (#9) are `other` with extra["guide"] = True, extra["color"] green | yellow and extra["fade"].
- holds (#2) on playable lanes are `hold`; anywhere else they and any other unknown line are kept verbatim in
  meta.source["sus_unrepresented"] and named in the warnings.

Combo (`sekai_combo`): every judged object counts 1 (taps, flicks, traces, slide starts and ends that are not hidden,
hidden ends with a flick, relay ticks, attached ticks), plus one hidden tick on every half beat strictly inside each
slide. This gives the published totalNoteCount for every chart of both sources (docs/sources/sekai.md).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ..model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, PathPoint, ScrollChange, TimingMap

LANE_OFFSET = 2            # SUS lane of IR lane 0
PLAYABLE = range(2, 14)    # SUS lanes 2..13
SKILL_LANE, FEVER_LANE = 0, 15

FLICK = {1: "up", 3: "up-left", 4: "up-right"}
EASE = {2: "in", 5: "out", 6: "out"}

_DATA = re.compile(r"^#([0-9A-Za-z]+):\s*(.*)$")
_META = re.compile(r"^#([0-9A-Za-z_]+)(?:\s+(.*))?$")
_TIL_ITEM = re.compile(r"^\s*(-?\d+)\s*'\s*(-?\d+)\s*:\s*([-+0-9.eE]+)\s*$")


@dataclass
class SusLine:
    no: int            # 1-based line number in the file
    header: str
    data: str
    measure_base: int  # #MEASUREBS in force
    hispeed: str       # #HISPEED group in force ("" = none)


@dataclass
class SusObject:
    beat: Fraction
    lane: int          # SUS lane
    width: int
    type: int
    channel: str = ""  # stream id for holds/slides/guides
    no: int = 0        # source line, for stable ordering
    hispeed: str = ""

    @property
    def key(self) -> tuple[int, Fraction]:
        return self.lane, self.beat


@dataclass
class Sus:
    meta: dict[str, list[str]] = field(default_factory=dict)
    lines: list[SusLine] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    ticks_per_beat: int = 480


def parse_sus(text: str) -> Sus:
    s = Sus()
    measure_base, hispeed = 0, ""
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("#"):
            s.comments.append(line)
            continue
        m = _DATA.match(line)
        if m:
            s.lines.append(SusLine(no, m.group(1), m.group(2).strip(), measure_base, hispeed))
            continue
        m = _META.match(line)
        if not m:
            s.comments.append(line)
            continue
        key, value = m.group(1).upper(), (m.group(2) or "").strip()
        if key == "MEASUREBS":
            measure_base = int(value or 0)
        elif key == "HISPEED":
            hispeed = value
        elif key == "NOSPEED":
            hispeed = ""
        s.meta.setdefault(key, []).append(value)
        if key == "REQUEST":
            req = _unquote(value).split()
            if len(req) == 2 and req[0] == "ticks_per_beat" and req[1].isdigit():
                s.ticks_per_beat = int(req[1])
    return s


def _unquote(v: str) -> str:
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def _pairs(data: str) -> tuple[list[tuple[int, str]], int]:
    """Non-empty 2-char pairs of a data line with their index, and how many pairs the line has."""
    data = re.sub(r"\s", "", data)
    n = len(data) // 2
    return [(i, data[2 * i:2 * i + 2]) for i in range(n) if data[2 * i:2 * i + 2] != "00"], n


class _Bars:
    """Measure lengths (#mmm02) -> beat of any (measure, fraction of measure)."""

    def __init__(self, lengths: dict[int, Fraction]) -> None:
        if 0 not in lengths:
            lengths = {0: Fraction(4), **lengths}
        self.bars: list[tuple[int, Fraction, Fraction]] = []   # (measure, start beat, length)
        beat = Fraction(0)
        prev: tuple[int, Fraction] | None = None
        for measure, length in sorted(lengths.items()):
            if prev is not None:
                beat += (measure - prev[0]) * prev[1]
            self.bars.append((measure, beat, length))
            prev = (measure, length)

    def beat(self, measure: int, pos: Fraction = Fraction(0)) -> Fraction:
        bar = self.bars[0]
        for b in self.bars:
            if b[0] <= measure:
                bar = b
            else:
                break
        m0, start, length = bar
        return start + (measure - m0) * length + pos * length

    def meters(self) -> list[MeterChange]:
        out = []
        for measure, beat, length in self.bars:
            if length.denominator == 1:
                num, den = int(length), 4
            else:
                num, den = length.numerator, 4 * length.denominator
            if not out or (out[-1].numerator, out[-1].denominator) != (num, den):
                out.append(MeterChange(beat, numerator=num, denominator=den))
        return out


def sus_to_chart(text: str, meta: ChartMeta) -> Chart:
    s = parse_sus(text)
    warnings: list[str] = []
    unrepresented: list[str] = []
    unknown: dict[str, int] = {}

    lengths: dict[int, Fraction] = {}
    for ln in s.lines:
        m = re.fullmatch(r"(\d{3})02", ln.header)
        if m:
            try:
                lengths[int(m.group(1)) + ln.measure_base] = Fraction(ln.data.strip('"'))
            except (ValueError, ZeroDivisionError):
                warnings.append(f"bad measure length on line {ln.no}: {ln.data!r}")
    bars = _Bars(lengths)
    tpb = s.ticks_per_beat

    bpm_defs: dict[str, str] = {}
    til_defs: dict[str, SusLine] = {}
    bpm_refs: list[tuple[Fraction, str, int]] = []
    taps: list[SusObject] = []
    dirs: list[SusObject] = []
    streams: dict[tuple[str, str], list[SusObject]] = {}     # (kind 2|3|9, channel[/lane]) -> objects
    hispeeds_used: dict[str, int] = {}

    for ln in s.lines:
        h = ln.header
        hu = h.upper()
        if hu.startswith("BPM") and len(h) == 5:
            bpm_defs[hu[3:]] = ln.data
            continue
        if hu.startswith("TIL") and len(h) == 5:
            til_defs[hu[3:]] = ln
            continue
        if re.fullmatch(r"\d{3}02", h):
            continue
        m = re.fullmatch(r"(\d{3})([0-9A-Za-z])([0-9A-Za-z]{1,2})", h)
        if not m:
            s.meta.setdefault(hu, []).append(ln.data)   # e.g. #VOLUME: ""
            continue
        measure = int(m.group(1)) + ln.measure_base
        ch, rest = m.group(2), m.group(3).lower()
        pairs, n = _pairs(ln.data)
        if ch == "0" and rest == "8":
            for i, v in pairs:
                bpm_refs.append((bars.beat(measure, Fraction(i, n)), v.upper(), ln.no))
            continue
        kind = {("1", 1): "tap", ("5", 1): "dir", ("2", 2): "2", ("3", 2): "3", ("9", 2): "9"}.get((ch, len(rest)))
        if kind is None:
            unknown[f"#mmm{ch}{'x' * len(rest)}"] = unknown.get(f"#mmm{ch}{'x' * len(rest)}", 0) + 1
            unrepresented.append(f"#{h}:{ln.data}")
            continue
        lane = int(rest[0], 36)
        objs = []
        for i, v in pairs:
            try:
                objs.append(SusObject(bars.beat(measure, Fraction(i, n)), lane, int(v[1], 36), int(v[0], 36),
                                      rest[1:] if kind in ("3", "9") else rest, ln.no, ln.hispeed))
            except ValueError:
                warnings.append(f"bad pair {v!r} on line {ln.no}")
        if objs:
            hispeeds_used[ln.hispeed] = hispeeds_used.get(ln.hispeed, 0) + len(objs)
        if kind == "tap":
            taps += objs
        elif kind == "dir":
            dirs += objs
        elif kind == "2" and lane not in PLAYABLE:
            unrepresented.append(f"#{h}:{ln.data}")
            unknown["side-lane hold #mmm2xy"] = unknown.get("side-lane hold #mmm2xy", 0) + len(objs)
        else:
            streams.setdefault((kind, rest[1:] if kind != "2" else rest), []).extend(objs)

    # ---- timing ----
    timing = TimingMap(bpms=[], meters=bars.meters(), scrolls=[])
    for beat, ref, no in bpm_refs:
        if ref not in bpm_defs:
            warnings.append(f"BPM change to undefined #BPM{ref} on line {no}")
            continue
        try:
            timing.bpms.append(BpmChange(beat, float(bpm_defs[ref])))
        except ValueError:
            warnings.append(f"bad #BPM{ref}: {bpm_defs[ref]!r}")
    if not timing.bpms:
        first = next(iter(bpm_defs.values()), "120")
        timing.bpms.append(BpmChange(Fraction(0), float(first)))
        warnings.append(f"no BPM change line; used {first}")
    try:
        wave_offset = float(_unquote((s.meta.get("WAVEOFFSET") or ["0"])[0]) or 0)
    except ValueError:
        wave_offset = 0.0
        warnings.append(f"bad #WAVEOFFSET {s.meta['WAVEOFFSET'][0]!r}")
    timing.offset_ms = -wave_offset * 1000.0

    main_group = max(hispeeds_used.items(), key=lambda kv: kv[1])[0] if hispeeds_used else ""
    scroll_group = main_group.upper() or min(til_defs, default="")
    if len(hispeeds_used) > 1:
        warnings.append(f"notes use {len(hispeeds_used)} hi-speed groups; scroll changes are from #TIL{scroll_group} "
                        f"only (other notes keep extra['hispeed'])")
    for gid, ln in til_defs.items():
        items = [x for x in _unquote(ln.data).split(",") if x.strip()]
        if gid != scroll_group:
            if items:
                unrepresented.append(f"#{ln.header}:{ln.data}")
            continue
        for item in items:
            m = _TIL_ITEM.match(item)
            if not m:
                warnings.append(f"bad #TIL{gid} item {item.strip()!r}")
                continue
            beat = bars.beat(int(m.group(1))) + Fraction(int(m.group(2)), tpb)
            timing.scrolls.append(ScrollChange(beat, float(m.group(3))))

    # ---- modifiers by (lane, beat) ----
    critical: set[tuple[int, Fraction]] = set()
    trace: set[tuple[int, Fraction]] = set()
    tick_remove: set[tuple[int, Fraction]] = set()
    no_judge: set[tuple[int, Fraction]] = set()
    for t in taps:
        if t.type in (2, 6, 8):
            critical.add(t.key)
        if t.type in (5, 6):
            trace.add(t.key)
        if t.type == 3:
            tick_remove.add(t.key)
        if t.type in (7, 8):
            no_judge.add(t.key)
    flick: dict[tuple[int, Fraction], str] = {}
    ease: dict[tuple[int, Fraction], str] = {}
    for d in sorted(dirs, key=lambda o: o.no):
        if d.type in FLICK:
            flick[d.key] = FLICK[d.type]
        elif d.type in EASE:
            ease[d.key] = EASE[d.type]
        else:
            warnings.append(f"unknown directional type {d.type} at beat {d.beat}")

    notes: list[Note] = []
    used_keys: set[tuple[int, Fraction]] = set()      # modifier keys that found a target
    slide_keys: set[tuple[int, Fraction]] = set()     # keys of active slide points (no single there)
    events: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def bump(k: str, n: int = 1) -> None:
        counts[k] = counts.get(k, 0) + n

    # ---- slides, guides, holds ----
    slides: dict[tuple[int, Fraction], Note] = {}
    for (kind, channel), objs in sorted(streams.items()):
        for group in _split_streams(objs, warnings, kind, channel):
            if kind in ("2", "3"):
                for o in group:
                    if o.type in (1, 2, 3, 5):
                        slide_keys.add(o.key)
            note = _slide_note(group, kind, critical, trace, tick_remove, no_judge, flick, ease, used_keys, warnings,
                               main_group)
            if note is None:
                continue
            if kind == "3":
                start = (group[0].lane, group[0].beat)
                if start in slides:
                    bump("duplicate_slides")
                    notes.remove(slides[start])
                slides[start] = note
            notes.append(note)

    # ---- singles ----
    singles: dict[tuple[int, Fraction], list[SusObject]] = {}
    for t in sorted(taps, key=lambda o: o.no):
        if t.lane == SKILL_LANE and t.type == 4:
            events.append({"beat": str(t.beat), "event": "skill"})
            continue
        if t.lane == FEVER_LANE and t.type in (1, 2):
            events.append({"beat": str(t.beat), "event": "fever_chance" if t.type == 1 else "super_fever"})
            continue
        if t.lane not in PLAYABLE:
            unrepresented.append(f"tap lane {t.lane} type {t.type} width {t.width} at beat {t.beat}")
            unknown["side-lane tap"] = unknown.get("side-lane tap", 0) + 1
            continue
        if t.key in slide_keys:
            continue        # a modifier of a slide point
        if t.type in (3, 7, 8):
            if t.type == 3 or t.key not in used_keys:
                bump(f"modifier_without_target_{t.type}")
            continue        # 7/8 on a guide point colour it; otherwise nothing to modify
        singles.setdefault(t.key, []).append(t)
    for key, group in singles.items():
        if len(group) > 1:
            same = all((g.width, g.type) == (group[0].width, group[0].type) for g in group)
            bump("stacked_taps_identical" if same else "stacked_taps_merged", len(group) - 1)
        first = group[0]
        types = {g.type for g in group}
        lane = first.lane - LANE_OFFSET
        if types == {4}:
            notes.append(_with_group(Note(first.beat, lane, first.width, NoteKind.MINE), first, main_group))
            continue
        is_crit = bool(types & {2, 6}) or key in critical
        is_trace = bool(types & {5, 6})
        extra: dict[str, Any] = {}
        if key in flick:
            nk = NoteKind.FLICK
            extra["direction"] = flick[key]
            used_keys.add(key)
        else:
            nk = NoteKind.EX_TAP if is_crit else NoteKind.TAP
        if is_crit:
            extra["critical"] = True
        if is_trace:
            extra["trace"] = True
        if 4 in types:
            extra["damage_stacked"] = True
        notes.append(_with_group(Note(first.beat, lane, first.width, nk, extra=extra), first, main_group))

    orphan_dirs = sum(1 for d in dirs if d.key not in used_keys and d.key not in slide_keys
                      and d.lane in PLAYABLE and d.type in FLICK)
    if orphan_dirs:
        bump("flick_without_note", orphan_dirs)
    out_of_range = sum(1 for n in notes if n.lane < 0 or n.lane + n.width > 12 or n.width <= 0)
    if out_of_range:
        warnings.append(f"{out_of_range} notes reach outside the 12 lanes")
    if unknown:
        warnings.append("not represented: " + ", ".join(f"{k}×{v}" for k, v in sorted(unknown.items())))
    for k, v in sorted(counts.items()):
        warnings.append(f"{k.replace('_', ' ')}: {v}")

    chart = Chart(meta=meta, lanes=12, timing=timing, notes=notes, warnings=warnings)
    header = {k.lower(): (_unquote(v[0]) if len(v) == 1 else [_unquote(x) for x in v]) for k, v in s.meta.items()
              if k in ("TITLE", "ARTIST", "DESIGNER", "DIFFICULTY", "PLAYLEVEL", "SONGID", "WAVE", "WAVEOFFSET",
                       "JACKET", "REQUEST", "HISPEED", "MEASUREHS", "VOLUME")}
    if s.comments:
        header["comment"] = s.comments[0][:120]
    chart.meta.source["sus"] = header
    if events:
        chart.meta.source["sus_events"] = sorted(events, key=lambda e: Fraction(e["beat"]))
    if unrepresented:
        chart.meta.source["sus_unrepresented"] = unrepresented
    chart.sort()
    chart.meta.source["combo"] = sekai_combo(chart)
    return chart


def _with_group(note: Note, obj: SusObject, main_group: str) -> Note:
    if obj.hispeed != main_group:
        note.extra["hispeed"] = obj.hispeed
    return note


def _split_streams(objs: list[SusObject], warnings: list[str], kind: str, channel: str) -> list[list[SusObject]]:
    """One stream (same kind and channel) -> its slides, in time order, each from a start (1) to an end (2).

    At equal times relays come first, then the end, then a start, so a slide that ends where the next one in the
    same channel begins is split correctly whatever the line order."""
    rank = {3: 0, 5: 0, 2: 1, 1: 2}
    out: list[list[SusObject]] = []
    current: list[SusObject] | None = None
    for o in sorted(objs, key=lambda o: (o.beat, rank.get(o.type, 0), o.no)):
        if o.type not in (1, 2, 3, 5):
            warnings.append(f"unknown slide point type {o.type} in #{kind} stream {channel} at beat {o.beat}")
            continue
        if current is None:
            current = []
            out.append(current)
            if o.type != 1:
                warnings.append(f"#{kind} stream {channel}: slide at beat {o.beat} does not begin with a start")
        elif o.type == 1:
            warnings.append(f"#{kind} stream {channel}: start at beat {o.beat} inside an open slide")
        current.append(o)
        if o.type == 2:
            current = None
    if current is not None:
        warnings.append(f"#{kind} stream {channel}: slide from beat {current[0].beat} has no end")
    return out


def _slide_note(group: list[SusObject], kind: str, critical, trace, tick_remove, no_judge, flick, ease, used_keys,
                warnings: list[str], main_group: str) -> Note | None:
    start, end = group[0], group[-1]
    if start.type != 1 or end.type != 2:
        return None        # malformed; _split_streams has already warned
    active = kind in ("2", "3")
    is_crit = start.key in critical
    shape: list[tuple[SusObject, str]] = []   # path points after the start, with their point type
    attach: list[str] = []
    crit_points: list[int] = []
    for o in group[1:]:
        if o.type == 3 and o.key in tick_remove and active:
            attach.append(str(o.beat - start.beat))
            used_keys.add(o.key)
            continue
        if o.type == 5 and o.key in tick_remove:
            used_keys.add(o.key)
            continue
        if o.type == 2:
            ptype = "end"
        elif o.type == 3 and active:
            ptype = "trace" if o.key in trace else "tick"
        else:
            ptype = "hidden"
        if active and not is_crit and o.key in critical and ptype != "hidden":
            crit_points.append(len(shape))
        shape.append((o, ptype))
    path: list[PathPoint] = []
    prev_ease = ease.get(start.key, "linear")
    for o, ptype in shape:
        path.append(PathPoint(o.beat - start.beat, o.lane - LANE_OFFSET, o.width, prev_ease))
        prev_ease = ease.get(o.key, "linear") if ptype != "end" else "linear"
    for o in group:
        if o.key in ease or o.key in critical or o.key in trace or o.key in no_judge:
            used_keys.add(o.key)
    straight = all(p.lane == start.lane - LANE_OFFSET and p.width == start.width for p in path)
    extra: dict[str, Any] = {"points": [p for _, p in shape]}
    if attach:
        extra["attach"] = attach
    if not active:
        nk = NoteKind.OTHER
        extra["guide"] = True
        extra["color"] = "yellow" if is_crit else "green"
        extra["fade"] = "none" if end.key in no_judge else "out"
    else:
        nk = NoteKind.HOLD if straight else NoteKind.SLIDE
        if is_crit:
            extra["critical"] = True
        if crit_points:
            extra["critical_points"] = crit_points
        extra["start"] = "hidden" if start.key in no_judge else "trace" if start.key in trace else "tap"
        extra["end"] = "hidden" if end.key in no_judge else "trace" if end.key in trace else "release"
        if end.key in flick:
            # kept on a hidden end too: the game still judges that flick (the one case, 690 APPEND, needs it to
            # reach the published combo; Next-SEKAI's reader drops it)
            extra["end_direction"] = flick[end.key]
            used_keys.add(end.key)
        if kind == "2":
            extra["sus_hold"] = True
    note = Note(start.beat, start.lane - LANE_OFFSET, start.width, nk, end.beat - start.beat, path=path, extra=extra)
    return _with_group(note, start, main_group)


def half_beat_ticks(start: Fraction, end: Fraction) -> int:
    """Hidden combo ticks of a slide: half beats (on the chart's beat grid) strictly between start and end."""
    return max(0, math.ceil(end * 2) - math.floor(start * 2) - 1)


def sekai_combo(chart: Chart) -> dict[str, int]:
    """The combo the game counts for a chart, with its parts (see the module docstring)."""
    parts = {"singles": 0, "slide_heads": 0, "slide_tails": 0, "slide_ticks": 0, "attached_ticks": 0,
             "half_beat_ticks": 0}
    for n in chart.notes:
        if n.kind in (NoteKind.TAP, NoteKind.EX_TAP, NoteKind.FLICK):
            parts["singles"] += 1
        elif n.kind in (NoteKind.SLIDE, NoteKind.HOLD) and "points" in n.extra:
            parts["slide_heads"] += n.extra.get("start") != "hidden"
            parts["slide_tails"] += n.extra.get("end") != "hidden" or "end_direction" in n.extra
            parts["slide_ticks"] += sum(p in ("tick", "trace") for p in n.extra["points"])
            parts["attached_ticks"] += len(n.extra.get("attach", []))
            parts["half_beat_ticks"] += half_beat_ticks(n.beat, n.end_beat)
    parts["total"] = sum(parts.values())
    return parts
