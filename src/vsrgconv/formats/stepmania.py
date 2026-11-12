"""StepMania simfiles (.sm and .ssc): text, exact. Used for DDR and Pump It Up.

File syntax (StepMania's MsdFile): values are `#TAG:param:param...;`. `//` starts a comment that runs to the end of the
line, wherever it is (a title with `//` in it has to escape it as `\\/\\/`). `\\` escapes the next character. A `#` at
the start of a line ends a value whose `;` is missing. Tag names are case-insensitive.

- **.sm**: song tags, then one `#NOTES:<steps type>:<description>:<difficulty>:<meter>:<radar>:<note data>;` per
  chart.
- **.ssc**: song tags, then for each chart `#NOTEDATA:;`, that chart's own tags (#STEPSTYPE, #DIFFICULTY, #METER,
  #DESCRIPTION, #CHARTNAME, #CREDIT and, optionally, timing tags), and last `#NOTES:` (or `#NOTES2:`).

Timing tags, at song level or per chart in .ssc. A chart with any timing tag of its own uses only its own segments,
and the song's #OFFSET unless it gives one. This is what StepMania's SSC loader does, for files of #VERSION 0.70 and
later.

| Tag | Value | IR |
|---|---|---|
| `#OFFSET` | seconds; beat 0 sounds at -OFFSET s | `TimingMap.offset_ms = -OFFSET * 1000`; none given: 0, as in StepMania, and `offset_given` false |
| `#BPMS` | beat=bpm,... | `BpmChange`. In a .sm file a negative (or > 9,999,999) BPM is the old warp trick: it becomes a warp, as StepMania's SM loader makes it. In a .ssc file StepMania ignores a BPM <= 0, and so does the reader (warning). |
| `#STOPS` (`#FREEZES`) | beat=seconds | `TimingMap.stops` in ms (the note on the stop's beat comes before the pause). A negative stop is a warp in a .sm file, ignored in a .ssc file (both as StepMania does). |
| `#DELAYS` | beat=seconds (the pause comes before the note on that beat) | `TimingMap.delays` in ms (StepMania ignores delays <= 0; so does the reader, with a warning) |
| `#WARPS` | beat=length in beats (skipped instantly) | `TimingMap.warps`; notes inside become fakes. In .ssc files before #VERSION 0.70 the value is the end beat, as StepMania reads it. |
| `#SCROLLS` | beat=ratio | `ScrollChange` |
| `#SPEEDS` | beat=ratio=duration=unit (unit 0: beats, 1: seconds) | multiplied into the `ScrollChange`s: StepMania scales the whole field by the speed at the current time, and the scroll ratio the notes pass the receptors at is speed × scroll. An instant change (duration 0) is exact. A tween (duration > 0, linear in time from the previous ratio) is approximated by steps every 1/4 beat: warning + verbatim. |
| `#FAKES` | beat=length in beats | notes inside become fakes (see below); verbatim |
| `#TIMESIGNATURES` | beat=num=den | `MeterChange` |
| `#TICKCOUNTS`, `#COMBOS` | hold checkpoints per beat; combo multiplier | parsed (`SmTiming.tickcounts`, `.combos`) for `pump_counts`; verbatim |
| `#LABELS` | editor data | verbatim only |

"Verbatim" means the tag's text is kept in `meta.source["stepmania"]["unrepresented"]`.

Beats in timing tags are decimal text, such as "63.875" or "0.333". StepMania stores them as rows of 1/48 beat
(BeatToNoteRow), so the reader snaps them to that grid. This is the format's own grid, not a rounding by us. Note rows
are exact: a measure is always 4 beats (time signatures only move bar lines), and row r of a measure of R rows is at
beat `measure*4 + r*4/R`.

Note characters (StepMania's NoteData loader semantics):

| Char | Meaning | IR |
|---|---|---|
| `1` | tap | tap |
| `2` / `4` | hold / roll head | hold (roll: `extra.roll`) |
| `3` | hold or roll tail | ends the head above it |
| `M` | mine | mine |
| `L` | lift | tap with `extra.lift` |
| `F` | fake (shown, never judged) | other, `extra.fake = "note"` |
| `K` | auto-keysound (hidden) | other, `extra.keysound` |
| `X` `Y` `Z` / `x` `y` `z` | StepF2 co-op notes: tap / hold head of player 2, 3, 4 | tap / hold with `extra.player`; `1`/`2` notes of such a chart get player 1 |
| `6` | StepF2 hold head variant (6 charts in PIU-Simfiles; often a hold right after a tap on the same panel) | hold with `extra.sm_head = "6"`. With it, FIESTA EX "Everybody Got 2 Know" S21 has the 948 taps and 109 holds piu-vis-ss lists. |
| `{c\\|a\\|f\\|v}` | StepF2 note with attributes: c is the note character, a its appearance (`n` normal), f is 1 for a fake | as `c`; a fake (f = 1) becomes other with `extra.fake = "note"`; the text is kept in `extra.sm_attr` |
| other | not a StepMania note (StepMania loads it as empty) | other, `extra.sm = <char>`, warning |

Routine (and other multi-player) note data separates the players' notes with `&`: each part is read on its own and its
notes get `extra.player` 1, 2, ...

A hold whose tail is missing, or whose column has a tap, mine, lift or fake before its tail, is dropped, as StepMania
drops it. Each drop is reported as a warning. Any note inside a #FAKES or #WARPS range becomes kind `other` with
`extra.fake` set to "fakes" or "warp" and `extra.fake_kind` set to the kind it would have had. The baseline rules
drop `other`, so a fake never becomes a playable note. Attributes after a note, such as `{...}` (an attack) or `[n]`
(a keysound), are kept in `extra.sm_attr`.

`pump_counts` gives Pump It Up's max combo the way StepMania 5 scores the `pump` game, which is meant to play like the
arcade: one combo per judged row (a jump is one), plus one per hold checkpoint row that has no tap of its own. Hold
checkpoints are the rows of the #TICKCOUNTS grid after the hold's head, up to and including its end. Each row counts
the #COMBOS multiplier in force there; rows in warps and #FAKES are not judged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from math import floor
from typing import Any

from ..model import (
    BpmChange,
    Chart,
    ChartMeta,
    MeterChange,
    Note,
    NoteKind,
    ScrollChange,
    TimingMap,
)

ROWS_PER_BEAT = 48
BEATS_PER_MEASURE = 4
SPLIT_TIMING_VERSION = 0.70

#: columns per steps type. Unknown types take the width of their first note row.
STEPS_TYPES: dict[str, int] = {
    "dance-single": 4, "dance-double": 8, "dance-couple": 8, "dance-solo": 6, "dance-threepanel": 3,
    "dance-routine": 8,
    "pump-single": 5, "pump-halfdouble": 6, "pump-double": 10, "pump-couple": 10, "pump-routine": 10,
    "kb7-single": 7, "ez2-single": 5, "ez2-double": 10, "ez2-real": 7, "para-single": 5,
    "ds3ddx-single": 8, "beat-single5": 6, "beat-double5": 12, "beat-single7": 8, "beat-double7": 16,
    "maniax-single": 4, "maniax-double": 8, "techno-single4": 4, "techno-single5": 5, "techno-single8": 8,
    "techno-double4": 8, "techno-double5": 10, "techno-double8": 16, "popn-five": 5, "popn-nine": 9,
    "lights-cabinet": 6, "kickbox-human": 4, "kickbox-quadarm": 4, "kickbox-insect": 6, "kickbox-arachnid": 8,
}

TIMING_TAGS = ("OFFSET", "BPMS", "STOPS", "FREEZES", "DELAYS", "WARPS", "SCROLLS", "SPEEDS", "FAKES",
               "TIMESIGNATURES", "TICKCOUNTS", "COMBOS", "LABELS")
#: how StepMania reads a difficulty name (Difficulty.cpp, OldStyleStringToDifficulty)
DIFFICULTY_NAMES = {
    "beginner": "Beginner", "easy": "Easy", "basic": "Easy", "light": "Easy",
    "medium": "Medium", "another": "Medium", "trick": "Medium", "standard": "Medium", "difficult": "Medium",
    "hard": "Hard", "ssr": "Hard", "maniac": "Hard", "heavy": "Hard",
    "smaniac": "Challenge", "challenge": "Challenge", "expert": "Challenge", "oni": "Challenge",
    "edit": "Edit",
}


# ------------------------------------------------------------------------------------------------ MSD syntax

_MSD_SPECIAL = re.compile(r"//|[#:;\\]")


def parse_msd(text: str) -> list[list[str]]:
    """Every `#TAG:p1:p2...;` value as [TAG, p1, p2, ...], in file order (StepMania's MsdFile.ReadBuf, scanning from
    one special character to the next)."""
    values: list[list[str]] = []
    params: list[str] = []
    cur: list[str] = []
    reading = False
    pos, n = 0, len(text)
    while pos < n:
        m = _MSD_SPECIAL.search(text, pos)
        at = m.start() if m else n
        if reading and at > pos:
            cur.append(text[pos:at])
        if m is None:
            break
        tok = m.group()
        if tok == "//":                       # comment: skip to the newline (the newline itself stays)
            nl = text.find("\n", at)
            pos = n if nl < 0 else nl
            continue
        if tok == "\\":                       # escape: the next character is literal (skipped outside a value)
            if reading and at + 1 < n:
                cur.append(text[at + 1])
            pos = at + 2
            continue
        if tok == "#":
            if reading:
                so_far = "".join(cur)
                before = so_far.rstrip(" \t")
                if before and before[-1] not in "\r\n":   # not the first character of its line: literal
                    cur.append("#")
                    pos = at + 1
                    continue
                params.append(so_far.rstrip(" \t\r\n"))  # a value with its ';' missing ends here
                values.append(params)
            params, cur, reading = [], [], True
            pos = at + 1
            continue
        pos = at + 1                          # ':' or ';'
        if not reading:
            continue
        params.append("".join(cur))
        cur = []
        if tok == ";":
            values.append(params)
            params, reading = [], False
    if reading:
        params.append("".join(cur))
        values.append(params)
    return [v for v in values if v]


# ------------------------------------------------------------------------------------------------ structures

@dataclass
class SmTiming:
    """Timing segments as parsed (beats snapped to StepMania's 1/48 grid; seconds as given)."""

    offset: float | None = None         # seconds; None = not given (0 for a song, the song's for a chart)
    bpms: list[tuple[Fraction, float]] = field(default_factory=list)
    stops: list[tuple[Fraction, float]] = field(default_factory=list)
    delays: list[tuple[Fraction, float]] = field(default_factory=list)
    warps: list[tuple[Fraction, Fraction]] = field(default_factory=list)
    scrolls: list[tuple[Fraction, float]] = field(default_factory=list)
    speeds: list[tuple[Fraction, float, float, int]] = field(default_factory=list)
    fakes: list[tuple[Fraction, Fraction]] = field(default_factory=list)
    timesigs: list[tuple[Fraction, int, int]] = field(default_factory=list)
    tickcounts: list[tuple[Fraction, int]] = field(default_factory=list)
    combos: list[tuple[Fraction, int, int]] = field(default_factory=list)   # beat, combo, miss combo
    raw: dict[str, str] = field(default_factory=dict)   # tag -> text as written
    errors: list[str] = field(default_factory=list)


@dataclass
class SmChart:
    steps_type: str
    difficulty: str          # as written
    meter: str
    notes: str               # note data, comments removed
    description: str = ""
    chart_name: str = ""
    credit: str = ""
    radar: str = ""
    tags: dict[str, str] = field(default_factory=dict)   # .ssc: every tag of this chart
    timing: SmTiming | None = None                       # .ssc: the chart's own timing, if it has any

    @property
    def difficulty_name(self) -> str:
        """StepMania's name for the difficulty: Beginner, Easy, Medium, Hard, Challenge or Edit ("" if unknown)."""
        return normalize_difficulty(self.difficulty, self.description)

    @property
    def columns(self) -> int:
        cols = STEPS_TYPES.get(self.steps_type.lower())
        if cols:
            return cols
        for line in self.notes.splitlines():
            row = line.strip()
            if row and row not in (",", ";"):
                return len(_tokens(row))
        return 0


@dataclass
class Simfile:
    format: str                                          # "sm" | "ssc"
    tags: dict[str, str] = field(default_factory=dict)   # song tags (last value wins), upper-case names
    timing: SmTiming = field(default_factory=SmTiming)
    charts: list[SmChart] = field(default_factory=list)
    song_id: str = ""                                    # "//----- song ID: xxxx -----//" header (ZiV DDR packs)
    version: float | None = None

    @property
    def title(self) -> str:
        return self.tags.get("TITLE", "")

    @property
    def artist(self) -> str:
        return self.tags.get("ARTIST", "")


def normalize_difficulty(name: str, description: str = "") -> str:
    d = DIFFICULTY_NAMES.get(name.strip().lower(), "")
    # old .sm files mark a challenge chart as Hard with this description (SMLoader)
    if d == "Hard" and description.strip().lower() in ("smaniac", "challenge"):
        return "Challenge"
    return d


# ------------------------------------------------------------------------------------------------ parsing

SONG_ID_RE = re.compile(r"song ID:\s*([^\s-][^\s]*?)\s*-+//")


def parse_simfile(text: str, fmt: str | None = None) -> Simfile:
    """Parse a .sm or .ssc file. `fmt` is "sm" or "ssc"; if None it is guessed (.ssc files have #NOTEDATA)."""
    text = text.lstrip("﻿")
    values = parse_msd(text)
    if fmt is None:
        fmt = "ssc" if any(v[0].strip().upper() == "NOTEDATA" for v in values) else "sm"
    sim = Simfile(format=fmt)
    m = SONG_ID_RE.search(text[:400])
    if m:
        sim.song_id = m.group(1)
    if fmt == "ssc":
        _read_ssc(values, sim)
    else:
        _read_sm(values, sim)
    return sim


def _value(v: list[str]) -> str:
    return ":".join(v[1:]).strip()


def _read_sm(values: list[list[str]], sim: Simfile) -> None:
    timing_raw: dict[str, str] = {}
    for v in values:
        tag = v[0].strip().upper()
        if tag == "NOTES":
            p = [x.strip() for x in v[1:]]
            if len(p) < 6:
                sim.timing.errors.append(f"#NOTES with {len(p)} fields skipped")
                continue
            sim.charts.append(SmChart(steps_type=p[0], description=p[1], difficulty=p[2], meter=p[3], radar=p[4],
                                      notes=v[-1]))
        elif tag in TIMING_TAGS:
            timing_raw[tag] = _value(v)
        else:
            sim.tags[tag] = _value(v)
    sim.timing = _timing(timing_raw)


def _read_ssc(values: list[list[str]], sim: Simfile) -> None:
    song_raw: dict[str, str] = {}
    chart: dict[str, str] | None = None
    chart_raw: dict[str, str] = {}
    for v in values:
        tag = v[0].strip().upper()
        if tag == "VERSION":
            try:
                sim.version = float(_value(v))
            except ValueError:
                pass
            sim.tags[tag] = _value(v)
        elif tag == "NOTEDATA":
            chart, chart_raw = {}, {}
        elif chart is None:
            if tag in TIMING_TAGS:
                song_raw[tag] = _value(v)
            else:
                sim.tags[tag] = _value(v)
        elif tag in ("NOTES", "NOTES2"):
            sim.charts.append(_ssc_chart(chart, chart_raw, v[-1] if len(v) > 1 else "", sim))
            chart = None
        elif tag in TIMING_TAGS:
            chart_raw[tag] = _value(v)
        else:
            chart[tag] = _value(v)
    sim.timing = _timing(song_raw)


def _ssc_chart(tags: dict[str, str], timing_raw: dict[str, str], notes: str, sim: Simfile) -> SmChart:
    c = SmChart(steps_type=tags.get("STEPSTYPE", ""), difficulty=tags.get("DIFFICULTY", ""),
                meter=tags.get("METER", ""), notes=notes, description=tags.get("DESCRIPTION", ""),
                chart_name=tags.get("CHARTNAME", ""), credit=tags.get("CREDIT", ""),
                radar=tags.get("RADARVALUES", ""), tags=dict(tags))
    if timing_raw:
        if sim.version is not None and sim.version < SPLIT_TIMING_VERSION:
            c.tags["IGNORED_TIMING"] = ",".join(sorted(timing_raw))   # StepMania ignores them before 0.70
        else:
            c.timing = _timing(timing_raw)
    return c


def _lround(x: Fraction) -> int:
    return floor(x + Fraction(1, 2)) if x >= 0 else -floor(-x + Fraction(1, 2))


def _beat(text: str) -> Fraction:
    f = Fraction(text.strip())
    if (f * ROWS_PER_BEAT).denominator != 1:
        f = Fraction(_lround(f * ROWS_PER_BEAT), ROWS_PER_BEAT)
    return f


def _pairs(text: str) -> list[list[str]]:
    return [[x.strip() for x in item.split("=")] for item in text.split(",") if item.strip()]


def _timing(raw: dict[str, str]) -> SmTiming:
    t = SmTiming(raw=dict(raw))
    if raw.get("OFFSET", "").strip():
        try:
            t.offset = float(raw["OFFSET"])
        except ValueError:
            t.errors.append(f"#OFFSET: bad value {raw['OFFSET']!r}")
    for tag in ("BPMS", "STOPS", "FREEZES", "DELAYS", "WARPS", "SCROLLS", "SPEEDS", "FAKES", "TIMESIGNATURES",
                "TICKCOUNTS", "COMBOS"):
        want = {"TIMESIGNATURES": 3}.get(tag, 2)
        for p in _pairs(raw.get(tag, "")):
            try:
                if len(p) < want:
                    raise ValueError("too few fields")
                b = _beat(p[0])
                if tag == "BPMS":
                    t.bpms.append((b, float(p[1])))
                elif tag in ("STOPS", "FREEZES"):
                    t.stops.append((b, float(p[1])))
                elif tag == "DELAYS":
                    t.delays.append((b, float(p[1])))
                elif tag == "WARPS":
                    t.warps.append((b, _beat(p[1])))      # a length (an end beat in .ssc before 0.70)
                elif tag == "SCROLLS":
                    t.scrolls.append((b, float(p[1])))
                elif tag == "SPEEDS":   # beat=ratio[=duration[=unit]]: StepMania fills in 0 (instant) and 0 (beats)
                    t.speeds.append((b, float(p[1]), float(p[2]) if len(p) > 2 else 0.0,
                                     int(float(p[3])) if len(p) > 3 else 0))
                elif tag == "FAKES":
                    t.fakes.append((b, _beat(p[1])))
                elif tag == "TIMESIGNATURES":
                    t.timesigs.append((b, int(float(p[1])), int(float(p[2]))))
                elif tag == "COMBOS":
                    combo = int(float(p[1]))
                    t.combos.append((b, combo, int(float(p[2])) if len(p) > 2 else combo))
                else:
                    t.tickcounts.append((b, int(float(p[1]))))
            except (ValueError, ZeroDivisionError):
                t.errors.append(f"#{tag}: bad entry {'='.join(p)!r}")
    for name in ("bpms", "stops", "delays", "warps", "scrolls", "speeds", "fakes", "timesigs", "tickcounts",
                 "combos"):
        getattr(t, name).sort(key=lambda seg: seg[0])
    return t


def effective_timing(sim: Simfile, chart: SmChart) -> tuple[SmTiming, str]:
    """The timing a chart plays with, and where it came from ("song" or "chart")."""
    if chart.timing is None:
        return sim.timing, "song"
    return chart.timing, "chart"


def _offset(sim: Simfile, timing: SmTiming) -> float | None:
    """The #OFFSET a chart plays with (its own, else the song's), or None if the file gives none (StepMania: 0)."""
    return timing.offset if timing.offset is not None else sim.timing.offset


# ------------------------------------------------------------------------------------------------ note data

def _tokens(row: str) -> list[tuple[str, str, list[str] | None]]:
    """One (char, attributes, StepF2 fields) per column. Attributes are `{...}` / `[...]` written right after a note;
    a StepF2 note `{c|a|f|v}` gives c as the char, the braces as its attributes and the fields split on `|`."""
    out: list[tuple[str, str, list[str] | None]] = []
    i, n = 0, len(row)
    while i < n:
        c = row[i]
        i += 1
        if c in " \t":
            continue
        fields: list[str] | None = None
        attr = ""
        if c == "{":                       # StepF2 note with attributes
            j = row.find("}", i)
            j = n if j < 0 else j
            fields = row[i:j].split("|")
            attr = row[i - 1:j + 1]
            c = fields[0][:1] or "0"
            i = j + 1
        while i < n and row[i] in "{[":
            close = "}" if row[i] == "{" else "]"
            j = row.find(close, i)
            j = n - 1 if j < 0 else j
            if close == "}" and "|" in row[i:j]:     # the next column's StepF2 note, not an attribute
                break
            attr += row[i:j + 1]
            i = j + 1
        out.append((c, attr, fields))
    return out


@dataclass
class _Head:
    beat: Fraction
    char: str
    attr: str
    broken: bool = False
    player: int | None = None
    fake: bool = False


#: StepF2 co-op notes: player 2-4 taps (X Y Z) and hold heads (x y z)
PLAYER_TAPS = {"X": 2, "Y": 3, "Z": 4}
PLAYER_HEADS = {"x": 2, "y": 3, "z": 4}
_ATTR_RE = re.compile(r"\{[^}]*\}|\[[^\]]*\]")
F2_FAKE_FIELD = 2          # {c|appearance|fake|...}: "1" in the third field is a fake note
#: StepF2 hold head variant: `6` starts a hold (ended by `3`), e.g. right after a tap on the same panel. piu-vis-ss
#: reads it as a hold too; whether StepF2 judges its head is not known, so it is kept in `extra.sm_head`.
F2_HOLD_HEADS = "6"


def read_notes(data: str, columns: int, player_letters: bool = False) -> tuple[list[Note], list[str]]:
    """Notes of one chart's note data (exact beats), and warnings. `player_letters`: read StepF2's extensions, the
    co-op letters (X Y Z x y z) as other players' notes and `6` as a hold head; only Pump It Up charts use them."""
    notes: list[Note] = []
    warnings: list[str] = []
    open_heads: dict[int, _Head] = {}
    dropped: list[str] = []
    unmatched_tails: list[str] = []
    odd_chars: dict[str, int] = {}
    bad_width = 0
    taps, heads = (PLAYER_TAPS, PLAYER_HEADS) if player_letters else ({}, {})
    f2_heads = F2_HOLD_HEADS if player_letters else ""
    coop = player_letters and bool(re.search(r"[XYZxyz]", _ATTR_RE.sub("", data)))
    measures = data.split(",")
    for m_idx, measure in enumerate(measures):
        rows = [line.strip() for line in measure.replace(";", "").splitlines()]
        rows = [r for r in rows if r]
        for r_idx, row in enumerate(rows):
            beat = Fraction(m_idx * BEATS_PER_MEASURE) + Fraction(r_idx * BEATS_PER_MEASURE, len(rows))
            toks = _tokens(row)
            if len(toks) != columns:
                bad_width += 1
            for col, (c, attr, f2) in enumerate(toks[:columns]):
                if c == "0":
                    continue
                fake = bool(f2 and len(f2) > F2_FAKE_FIELD and f2[F2_FAKE_FIELD].strip() == "1")
                if c == "3":
                    head = open_heads.pop(col, None)
                    if head is None or head.broken:
                        unmatched_tails.append(f"{beat}@{col}")
                        if head is not None:
                            dropped.append(f"{head.beat}@{col}")
                        continue
                    n = Note(head.beat, col, kind=NoteKind.HOLD, duration=beat - head.beat)
                    if head.char == "4":
                        n.extra["roll"] = True
                    elif head.char in F2_HOLD_HEADS:
                        n.extra["sm_head"] = head.char
                    if head.player is not None:
                        n.extra["player"] = head.player
                    if head.attr:
                        n.extra["sm_attr"] = head.attr
                    if head.fake:
                        n.extra.update({"fake": "note", "fake_kind": "hold"})
                        n.kind = NoteKind.OTHER
                    notes.append(n)
                    continue
                player = taps.get(c) or heads.get(c) or (1 if coop and c in "124" else None)
                if c == "K":
                    n = Note(beat, col, kind=NoteKind.OTHER, extra={"keysound": True})
                elif c in "24" or c in heads or (f2_heads and c in f2_heads):
                    prev = open_heads.get(col)
                    if prev is not None:
                        dropped.append(f"{prev.beat}@{col}")
                    open_heads[col] = _Head(beat, c, attr, player=player, fake=fake)
                    continue
                elif c in "1LMF" or c in taps:
                    if col in open_heads:            # a note before the tail: StepMania drops that hold
                        open_heads[col].broken = True
                    if c == "1" or c in taps:
                        n = Note(beat, col)
                    elif c == "L":
                        n = Note(beat, col, extra={"lift": True})
                    elif c == "M":
                        n = Note(beat, col, kind=NoteKind.MINE)
                    else:
                        n = Note(beat, col, kind=NoteKind.OTHER, extra={"fake": "note", "fake_kind": "tap"})
                else:
                    odd_chars[c] = odd_chars.get(c, 0) + 1
                    n = Note(beat, col, kind=NoteKind.OTHER, extra={"sm": c})
                if player is not None:
                    n.extra["player"] = player
                if attr:
                    n.extra["sm_attr"] = attr
                if fake and n.kind is not NoteKind.OTHER:
                    n.extra.update({"fake": "note", "fake_kind": n.kind.value})
                    n.kind = NoteKind.OTHER
                notes.append(n)
    dropped += [f"{h.beat}@{col}" for col, h in open_heads.items()]
    if dropped:
        warnings.append(f"{len(dropped)} hold heads without a tail dropped, as StepMania does (beat@column: "
                        f"{', '.join(dropped[:8])})")
    if unmatched_tails:
        warnings.append(f"{len(unmatched_tails)} hold tails without a head ignored ({', '.join(unmatched_tails[:8])})")
    if odd_chars:
        warnings.append("note characters StepMania does not know (kept as other): "
                        + ", ".join(f"{k!r}×{v}" for k, v in sorted(odd_chars.items())))
    if bad_width:
        warnings.append(f"{bad_width} note rows are not {columns} columns wide")
    return notes, warnings


def read_players(data: str, columns: int, player_letters: bool = False) -> tuple[list[Note], list[str]]:
    """Note data that may hold several players' notes separated by `&` (routine charts): each part is read on its own
    and its notes get `extra.player` 1, 2, ... (unless a co-op letter already gave one)."""
    parts = data.split("&")
    if len(parts) == 1:
        return read_notes(data, columns, player_letters)
    notes: list[Note] = []
    warnings: list[str] = []
    for i, part in enumerate(parts, 1):
        ns, ws = read_notes(part, columns, player_letters)
        for n in ns:
            n.extra.setdefault("player", i)
        notes += ns
        warnings += [f"player {i}: {w}" for w in ws]
    return notes, warnings


# ------------------------------------------------------------------------------------------------ to the IR

def _in_ranges(beat: Fraction, ranges: list[tuple[Fraction, Fraction]]) -> bool:
    return any(start <= beat < start + length for start, length in ranges)


FAST_BPM_WARP = 9999999.0      # StepMania: a BPM above this is an "infinite" BPM, i.e. a warp (NotesLoaderSM.h)
TWEEN_STEP = Fraction(1, 4)    # beats between the steps that stand in for a tweened #SPEEDS change
MAX_TWEEN_STEPS = 4096


def _q(x: float) -> Fraction:
    """A parsed decimal as an exact Fraction (the shortest decimal that gives the float back, i.e. the text)."""
    return Fraction(repr(float(x)))


def sm_warp_tricks(bpms: list[tuple[Fraction, float]], stops: list[tuple[Fraction, float]]
                   ) -> tuple[list[tuple[Fraction, float]], list[tuple[Fraction, float]],
                              list[tuple[Fraction, Fraction]], float, list[str]]:
    """StepMania's SMLoader::ProcessBPMsAndStops for a .sm file: negative (and "infinite") BPMs and negative stops
    become warps. Returns (bpms, stops in seconds, warps, seconds that stops before beat 0 add before beat 0,
    warnings). Beats are exact: the warped length is worked out with Fractions of the decimal values."""
    bpms = sorted(bpms, key=lambda x: x[0])
    stops = sorted(stops, key=lambda x: x[0])
    out_bpms: list[tuple[Fraction, Fraction]] = []
    out_stops: list[tuple[Fraction, Fraction]] = []
    warps: list[tuple[Fraction, Fraction]] = []
    warnings: list[str] = []
    fast = _q(FAST_BPM_WARP)
    before_zero = Fraction(0)
    i = j = 0
    while j < len(stops) and stops[j][0] < 0:     # stops before beat 0 only move beat 0 (the offset)
        before_zero += _q(stops[j][1])
        j += 1
    bpm = Fraction(0)
    while i < len(bpms) and bpms[i][0] <= 0:
        bpm = _q(bpms[i][1])
        if bpm < 0 and bpms[i][0] < 0:
            warnings.append("a negative BPM before beat 0 ignored, as StepMania does")
        i += 1
    if bpm == 0:
        if i == len(bpms):
            return [], [], [], float(before_zero), warnings + ["no #BPMS (StepMania uses 60)"]
        bpm = _q(bpms[i][1])
        warnings.append(f"no BPM at beat 0; the first one ({float(bpm)} at beat {bpms[i][0]}) used from beat 0")
    if 0 < bpm <= fast:
        out_bpms.append((Fraction(0), bpm))
    prevbeat = Fraction(0)
    warpstart: Fraction | None = None
    prewarpbpm = Fraction(0)
    timeofs = Fraction(0)
    while i < len(bpms) or j < len(stops):
        is_bpm = j == len(stops) or (i < len(bpms) and bpms[i][0] <= stops[j][0])
        beat, value = (bpms[i][0], _q(bpms[i][1])) if is_bpm else (stops[j][0], _q(stops[j][1]))
        if bpm != 0 and bpm <= fast:
            timeofs += (beat - prevbeat) * 60 / bpm
            if warpstart is not None and bpm > 0 and timeofs > 0:
                warps.append((warpstart, beat - timeofs * bpm / 60 - warpstart))
                if bpm != prewarpbpm:
                    out_bpms.append((warpstart, bpm))
                warpstart = None
        prevbeat = beat
        if is_bpm:
            if warpstart is None and (value < 0 or value > fast):
                warpstart, prewarpbpm, timeofs = beat, bpm, Fraction(0)
            elif warpstart is None:
                out_bpms.append((beat, value))
            if value == 0:
                warnings.append(f"a BPM of 0 at beat {beat} ignored")
            else:
                bpm = value
            i += 1
        else:
            if warpstart is None and value < 0:
                warpstart, prewarpbpm, timeofs = beat, bpm, value
            elif warpstart is None:
                out_stops.append((beat, value))
            else:
                timeofs += value
                if value > 0 and timeofs > 0:     # the stop more than pays back the warp: it ends here
                    warps.append((warpstart, beat - warpstart))
                    out_stops.append((beat, timeofs))
                    if bpm < 0 or bpm > fast:
                        warpstart, timeofs = beat, Fraction(0)
                    else:
                        if bpm != prewarpbpm:
                            out_bpms.append((warpstart, bpm))
                        warpstart = None
            j += 1
    if warpstart is not None:
        end = Fraction(99999999) if (bpm < 0 or bpm > fast) else prevbeat - timeofs * bpm / 60
        warps.append((warpstart, end - warpstart))
        if bpm != prewarpbpm:
            out_bpms.append((warpstart, bpm))
    dedup: dict[Fraction, Fraction] = {}
    for b, v in sorted(out_bpms, key=lambda x: x[0]):   # stable: a later segment on the same beat replaces
        dedup[b] = v
    return ([(b, float(v)) for b, v in dedup.items() if v > 0], [(b, float(v)) for b, v in out_stops],
            [(b, x) for b, x in warps if x > 0], float(before_zero), warnings)


def _speed_scrolls(timing: SmTiming, tm: TimingMap) -> tuple[list[ScrollChange], int]:
    """#SCROLLS × #SPEEDS as ScrollChanges, and how many tweened speed changes were approximated by steps."""
    speeds = [s for s in timing.speeds]
    trivial = all(r == 1.0 and d == 0 for _, r, d, _u in speeds)
    if trivial:
        if any(s != 1.0 for _, s in timing.scrolls):
            return [ScrollChange(b, s) for b, s in timing.scrolls], 0
        return [], 0
    # speed as a step function of beats: (beat, ratio) from that beat on
    steps: list[tuple[Fraction, float]] = []
    tweens = 0
    prior = 1.0
    for idx, (b, ratio, dur, unit) in enumerate(speeds):
        nxt = speeds[idx + 1][0] if idx + 1 < len(speeds) else None
        if dur <= 0:
            steps.append((b, ratio))
        else:
            tweens += 1
            start_ms = tm.ms_at(b)
            end_ms = tm.ms_at(b + _q(dur)) if unit == 0 else start_ms + dur * 1000.0
            span = max(end_ms - start_ms, 1e-9)
            at = b
            for _ in range(MAX_TWEEN_STEPS):
                if (nxt is not None and at >= nxt) or tm.ms_at(at) >= end_ms:
                    break
                mid = min(max((tm.ms_at(at + TWEEN_STEP / 2) - start_ms) / span, 0.0), 1.0)
                steps.append((at, prior + (ratio - prior) * mid))
                at += TWEEN_STEP
            if nxt is None or at < nxt:
                steps.append((at, ratio))
        prior = ratio
    scrolls = sorted(timing.scrolls, key=lambda x: x[0])
    beats = sorted({b for b, _ in steps} | {b for b, _ in scrolls} | {Fraction(0)})

    def value_at(segs: list[tuple[Fraction, float]], beat: Fraction) -> float:
        v = 1.0
        for b, r in segs:
            if b > beat:
                break
            v = r
        return v

    out: list[ScrollChange] = []
    for b in beats:
        v = value_at(scrolls, b) * value_at(steps, b)
        if not out or out[-1].speed != v:
            out.append(ScrollChange(b, v))
    return (out if any(s.speed != 1.0 for s in out) else []), tweens


def chart_to_ir(sim: Simfile, chart: SmChart, meta: ChartMeta) -> Chart:
    """One simfile chart as an IR `Chart` (lanes = the steps type's columns)."""
    timing, timing_from = effective_timing(sim, chart)
    columns = chart.columns
    warnings: list[str] = list(timing.errors)
    given = _offset(sim, timing)
    offset = given if given is not None else 0.0
    tm = TimingMap(offset_ms=(-offset * 1000.0) or 0.0, bpms=[], meters=[], scrolls=[], stops=[])
    unrepresented: dict[str, str] = {}

    warps: list[tuple[Fraction, Fraction]] = []
    if sim.format == "sm":
        bpms, stops, warps, before_zero, notes_ = sm_warp_tricks(timing.bpms, timing.stops)
        warnings += notes_
        tm.offset_ms += before_zero * 1000.0
        if warps:
            warnings.append(f"{len(warps)} warps from negative BPMs or stops (the old .sm warp trick), as "
                            f"StepMania reads them; notes inside are fakes")
    else:
        bpms = [(b, v) for b, v in timing.bpms if v > 0 and b >= 0]
        bad = [(b, v) for b, v in timing.bpms if not (v > 0 and b >= 0)]
        if bad:
            unrepresented["BPMS"] = timing.raw.get("BPMS", "")
            warnings.append(f"{len(bad)} BPMs <= 0 or before beat 0 ignored, as StepMania's .ssc loader does")
        stops = [(b, s) for b, s in timing.stops if s > 0]
        if any(s < 0 for _, s in timing.stops):
            unrepresented["STOPS"] = timing.raw.get("STOPS", timing.raw.get("FREEZES", ""))
            warnings.append(f"{sum(1 for _, s in timing.stops if s < 0)} negative stops ignored, as StepMania's "
                            f".ssc loader does")
    for b, v in bpms:
        if tm.bpms and tm.bpms[-1].beat == b:
            tm.bpms[-1] = BpmChange(b, v)
        else:
            tm.bpms.append(BpmChange(b, v))
    if not tm.bpms:
        tm.bpms.append(BpmChange(Fraction(0), 120.0))
        warnings.append("no usable #BPMS; 120 assumed")
    tm.stops = [(b, s * 1000.0) for b, s in stops if s > 0]
    tm.delays = [(b, s * 1000.0) for b, s in timing.delays if s > 0]
    if any(s < 0 for _, s in timing.delays):
        unrepresented["DELAYS"] = timing.raw.get("DELAYS", "")
        warnings.append(f"{sum(1 for _, s in timing.delays if s < 0)} negative delays ignored, as StepMania does")
    absolute = sim.format == "ssc" and sim.version is not None and sim.version < SPLIT_TIMING_VERSION
    for b, x in timing.warps:
        length = x - b if absolute and x > b else x       # pre-0.70 .ssc: the value is the end beat
        if length > 0:
            warps.append((b, length))
    tm.warps = sorted(warps, key=lambda w: w[0])
    tm.meters = [MeterChange(b, n, d) for b, n, d in timing.timesigs] or [MeterChange(Fraction(0))]
    tm.scrolls, tweens = _speed_scrolls(timing, tm)
    if tweens:
        unrepresented["SPEEDS"] = timing.raw.get("SPEEDS", "")
        warnings.append(f"{tweens} tweened speed changes (#SPEEDS) approximated by scroll steps every "
                        f"{TWEEN_STEP} beat")
    for tag in ("FAKES", "TICKCOUNTS", "COMBOS", "LABELS"):
        if timing.raw.get(tag):
            unrepresented[tag] = timing.raw[tag]
    if chart.tags.get("IGNORED_TIMING"):
        warnings.append(f"chart timing tags ignored (file #VERSION {sim.version} < 0.70): "
                        f"{chart.tags['IGNORED_TIMING']}")

    notes, note_warnings = read_players(chart.notes, columns, chart.steps_type.lower().startswith("pump-"))
    warnings += note_warnings
    fake_ranges = [(b, x) for b, x in timing.fakes if x > 0]
    pause_beats = {b for b, _ in tm.stops} | {b for b, _ in tm.delays}
    faked = 0
    for n in notes:
        why = ""
        if fake_ranges and _in_ranges(n.beat, fake_ranges):
            why = "fakes"
        elif tm.warps and _in_ranges(n.beat, tm.warps) and n.beat not in pause_beats:
            why = "warp"
        if why and n.kind is not NoteKind.OTHER:
            n.extra.update({"fake": why, "fake_kind": n.kind.value})
            n.kind = NoteKind.OTHER
            faked += 1
    meta.source["stepmania"] = {
        "format": sim.format, "steps_type": chart.steps_type, "difficulty": chart.difficulty,
        "meter": chart.meter, "description": chart.description, "chart_name": chart.chart_name,
        "credit": chart.credit, "timing_from": timing_from, "offset_s": offset, "offset_given": given is not None,
        **({"fake_notes": faked} if faked else {}),
        **({"unrepresented": unrepresented} if unrepresented else {}),
    }
    return Chart(meta=meta, lanes=columns, timing=tm, notes=notes, warnings=warnings).sort()


# ------------------------------------------------------------------------------------------------ counts

def judged(n: Note) -> bool:
    return n.kind in (NoteKind.TAP, NoteKind.HOLD)


def count_steps(chart: Chart) -> dict[str, int]:
    """Row-based counts. `steps`: rows with at least one judged tap, lift or hold head (a jump counts once, as in
    DDR); `jumps`: rows with two or more; `holds`/`rolls`: judged holds and rolls; `hold_rows`: rows where at least
    one of them starts; `mine_rows`: rows with a mine; plus `taps`, `lifts`, `mines`, `fakes`."""
    rows: dict[Fraction, int] = {}
    mine_rows: set[Fraction] = set()
    hold_rows: set[Fraction] = set()
    out = {"steps": 0, "jumps": 0, "taps": 0, "holds": 0, "rolls": 0, "hold_rows": 0, "lifts": 0, "mines": 0,
           "mine_rows": 0, "fakes": 0}
    for n in chart.notes:
        if judged(n):
            rows[n.beat] = rows.get(n.beat, 0) + 1
            if n.kind is NoteKind.HOLD:
                out["rolls" if n.extra.get("roll") else "holds"] += 1
                hold_rows.add(n.beat)
            elif n.extra.get("lift"):
                out["lifts"] += 1
            else:
                out["taps"] += 1
        elif n.kind is NoteKind.MINE:
            out["mines"] += 1
            mine_rows.add(n.beat)
        elif n.extra.get("fake"):
            out["fakes"] += 1
    out["steps"] = len(rows)
    out["jumps"] = sum(1 for v in rows.values() if v >= 2)
    out["mine_rows"] = len(mine_rows)
    out["hold_rows"] = len(hold_rows)
    return out


def _segment_at(segs: list, row: int, default: Any) -> Any:
    """The value of the last (beat, value...) segment at or before `row` (StepMania's GetSegmentAtRow)."""
    out = default
    for seg in segs:
        if _lround(seg[0] * ROWS_PER_BEAT) > row:
            break
        out = seg
    return out


def pump_counts(sim: Simfile, chart: SmChart, ir: Chart) -> dict[str, int]:
    """Pump It Up's max combo for a chart read by `chart_to_ir`, as StepMania 5 scores the `pump` game.

    - Each judged row (taps and hold heads; a jump is one) adds the #COMBOS multiplier in force on that row.
    - Each hold has a checkpoint on every row of the #TICKCOUNTS grid (`row % (48 // ticks) == 0`) after its head,
      up to and including its end (Player::CrossedRows looks at holds over rows r-1..r). A checkpoint row with a tap
      of its own adds nothing more (CheckpointsTapsSeparateJudgment is false for pump), and several holds on one row
      add one (ComboIsPerRow).
    - Rows in warps or #FAKES are not judged.

    Rows are StepMania's 1/48-beat rows (a note's beat is rounded to its row, as StepMania loads it). Returns `combo`,
    `rows` (judged rows), `checkpoints` (checkpoint rows that add to the combo), `holds`, `notes` (judged notes) and,
    for a chart with several players, `combo_p<n>` per player."""
    timing, _ = effective_timing(sim, chart)
    ticks = sorted(timing.tickcounts, key=lambda x: x[0]) or [(Fraction(0), 4)]   # StepMania's default: 4 per beat
    combos = sorted(timing.combos, key=lambda x: x[0])
    fakes = [(b, x) for b, x in timing.fakes if x > 0]
    warps = ir.timing.warps
    pauses = {b for b, _ in ir.timing.stops} | {b for b, _ in ir.timing.delays}

    def judgable(row: int) -> bool:
        beat = Fraction(row, ROWS_PER_BEAT)
        if fakes and _in_ranges(beat, fakes):
            return False
        return not (warps and _in_ranges(beat, warps) and beat not in pauses)

    def multiplier(row: int) -> int:
        seg = _segment_at(combos, row, None)
        return 1 if seg is None else seg[1]

    tick_rows = [(_lround(b * ROWS_PER_BEAT), t) for b, t in ticks]

    def checkpoints(head: int, end: int) -> set[int]:
        out: set[int] = set()
        for i, (start, t) in enumerate(tick_rows):
            stop = tick_rows[i + 1][0] - 1 if i + 1 < len(tick_rows) else end
            lo, hi = max(head + 1, start), min(end, stop)
            if t <= 0 or lo > hi:
                continue
            step = max(1, ROWS_PER_BEAT // t)
            first = lo + (-lo) % step
            out.update(range(first, hi + 1, step))
        return out

    def count(notes: list[Note]) -> tuple[int, int, int]:
        rows = {_lround(n.beat * ROWS_PER_BEAT) for n in notes if judged(n)}
        cps: set[int] = set()
        for n in notes:
            if n.kind is NoteKind.HOLD:
                cps |= checkpoints(_lround(n.beat * ROWS_PER_BEAT), _lround(n.end_beat * ROWS_PER_BEAT))
        cps = {r for r in cps - rows if judgable(r)}
        return sum(multiplier(r) for r in rows | cps), len(rows), len(cps)

    combo, rows, cps = count(ir.notes)
    out = {"combo": combo, "rows": rows, "checkpoints": cps,
           "holds": sum(1 for n in ir.notes if n.kind is NoteKind.HOLD),
           "notes": sum(1 for n in ir.notes if judged(n))}
    players = sorted({n.extra["player"] for n in ir.notes if "player" in n.extra})
    if len(players) > 1:
        for pl in players:
            out[f"combo_p{pl}"] = count([n for n in ir.notes if n.extra.get("player") == pl])[0]
    return out


def summary(sim: Simfile) -> dict[str, Any]:
    """Headers and per-chart counts of a simfile, for manifests and matching."""
    charts = []
    for c in sim.charts:
        ir = chart_to_ir(sim, c, ChartMeta(game="", song_id="", title=sim.title))
        charts.append({"type": c.steps_type, "difficulty": c.difficulty_name or c.difficulty, "meter": c.meter,
                       **count_steps(ir)})
    return {"title": sim.title, "titletranslit": sim.tags.get("TITLETRANSLIT", ""), "artist": sim.artist,
            "song_id": sim.song_id, "charts": charts}
