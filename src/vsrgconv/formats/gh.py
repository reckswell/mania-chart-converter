"""Guitar Hero / Clone Hero charts (`.mid` and `.chart`) to the intermediate format.

Both readers fill a `GhSong`: the tempo map, time signatures and, per (part, difficulty), the notes with their
modifiers already resolved. `GhSong.to_chart(part, difficulty, meta)` turns one of them into a `Chart`.

Parts: `guitar` (lead), `coop` (co-op lead), `bass`, `rhythm`, `keys`, `drums`, `guitar_ghl`, `bass_ghl` (GH Live
6-fret). Difficulties: `easy`, `medium`, `hard`, `expert`, and `expert+` for drums (expert plus the 2x kick notes).
Vocals are not read.

Conventions (sources: TheNathannator/GuitarGame_ChartFormats docs and YARG.Core's Moonscraper-derived readers,
see docs/sources/guitarhero.md):

- Beats are `tick / resolution` as exact Fractions. Tempo and time signatures come from the file's tempo map; the
  song.ini `delay` (ms, higher = later notes) and the .chart `Offset` (s) set `TimingMap.offset_ms`.
- 5-fret lanes are 0..4 = green, red, yellow, blue, orange. An open note is one note across the whole highway:
  lane 0, width 5, `extra.open`. GH Live 6-fret lanes are 0..5 = black 1-3, white 1-3 (open: lane 0, width 6).
  A chord is several notes at one beat.
- Drums: pads are lanes 0..3 (4-lane: red, yellow, blue, green) or 0..4 (5-lane: red, yellow, blue, orange, green);
  the kick is a full-width note with `extra.kick` (`extra.double_kick` for Expert+ 2x kick notes).
- Sustains: a .mid note shorter than the sustain cutoff (resolution/3 ticks, a 1/12 note, unless song.ini
  `sustain_cutoff_threshold` says otherwise, but never below resolution/12, 1/12 beat) is a tap; .chart keeps every
  sustain longer than 0. Longer notes are `NoteKind.HOLD` with `duration`.
- HOPOs: a single note within the HOPO threshold of the previous note/chord, on a different fret (.mid: not one of
  the previous chord's frets) is a natural HOPO. The threshold is resolution/3 + 1 ticks in .mid,
  resolution*65/192 in .chart, Harmonix's 170 ticks (at 480) for GH1/GH2 game files, or song.ini `hopo_frequency`/
  `eighthnote_hopo`/`hopofreq`. Forced-HOPO / forced-strum markers (.mid base+5 / base+6, e.g. expert 101/102; .chart
  N 5 flips the natural state) and taps (.mid note 104 or the Phase Shift SysEx; .chart N 6) override it.
  Each guitar note gets `extra.hopo` or `extra.tap` when not a strum, and `extra.forced` ("hopo"/"strum") when a
  marker set its type.
- Star power: .mid note 116 for every difficulty; GH1/GH2 game files mark it per difficulty (base+7: 67, 79, 91,
  103); a file without any 116 treats 103 as legacy star power (otherwise 103 is a solo). .chart `S 2`. Notes inside a
  phrase get `extra.star_power`; solos give `extra.solo`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ..model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, TimingMap
from .midi import MidiFile, MidiTrack, note_spans, parse_midi

DIFFICULTIES = ["easy", "medium", "hard", "expert"]
DIFF_BASE = {"easy": 60, "medium": 72, "hard": 84, "expert": 96}
GHL_BASE = {"easy": 58, "medium": 70, "hard": 82, "expert": 94}
SYSEX_DIFF = {0: "easy", 1: "medium", 2: "hard", 3: "expert"}

MID_TRACKS = {
    "PART GUITAR": "guitar", "T1 GEMS": "guitar", "PART GUITAR COOP": "coop", "PART BASS": "bass",
    "PART RHYTHM": "rhythm", "PART KEYS": "keys", "PART DRUMS": "drums", "PART DRUM": "drums",
    "PART GUITAR GHL": "guitar_ghl", "PART BASS GHL": "bass_ghl", "PART RHYTHM GHL": "rhythm_ghl",
    "PART GUITAR COOP GHL": "coop_ghl",
}
CHART_INSTRUMENTS = {
    "Single": "guitar", "DoubleGuitar": "coop", "DoubleBass": "bass", "DoubleRhythm": "rhythm", "Keyboard": "keys",
    "Drums": "drums", "GHLGuitar": "guitar_ghl", "GHLBass": "bass_ghl", "GHLRhythm": "rhythm_ghl",
    "GHLCoop": "coop_ghl",
}
GHL_PARTS = {"guitar_ghl", "bass_ghl", "rhythm_ghl", "coop_ghl"}

STAR_POWER = 116
SOLO = 103
TAP_NOTE = 104
OPEN = 5          # the fret number used for open notes in HOPO logic (green..orange are 0..4)
GHL_OPEN = 6
TAP_PHRASE, OPEN_PHRASE = 0x04, 0x01


@dataclass
class GhOptions:
    """Per-song parse settings (from song.ini or the game's song list). None = the format's default."""

    hopo_threshold: int | None = None      # ticks, as written in song.ini `hopo_frequency` / GH2 `hopo_threshold`
    eighthnote_hopo: bool | None = None
    hopofreq: int | None = None
    sustain_cutoff: int | None = None      # ticks (song.ini `sustain_cutoff_threshold`)
    star_power_note: int | None = None     # 103 or 116 (song.ini `multiplier_note` / `star_power_note`)
    five_lane_drums: bool | None = None
    pro_drums: bool | None = None
    delay_ms: float = 0.0                  # song.ini `delay`
    harmonix: bool = False                 # a GH1/GH2 game file: 170-tick HOPOs, per-difficulty star power, snap

    @classmethod
    def from_ini(cls, ini: dict[str, str]) -> "GhOptions":
        def num(key: str) -> int | None:
            v = ini.get(key, "").strip()
            try:
                return int(float(v))
            except ValueError:
                return None

        def flag(key: str) -> bool | None:
            v = ini.get(key, "").strip().lower()
            return {"true": True, "1": True, "false": False, "0": False}.get(v)

        spn = num("star_power_note")
        if spn is None:
            spn = num("multiplier_note")
        hopo = num("hopo_frequency")
        return cls(hopo_threshold=hopo if hopo and hopo > 0 else None, eighthnote_hopo=flag("eighthnote_hopo"),
                   hopofreq=num("hopofreq"), sustain_cutoff=num("sustain_cutoff_threshold"),
                   star_power_note=spn if spn in (103, 116) else None, five_lane_drums=flag("five_lane_drums"),
                   pro_drums=flag("pro_drums"), delay_ms=float(num("delay") or 0))


@dataclass
class GhNote:
    tick: int
    lane: int          # fret 0..4 (6-fret: 0..5), OPEN for open notes; drums: pad 0..4, -1 kick
    length: int = 0
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class GhTrack:
    notes: list[GhNote] = field(default_factory=list)
    lanes: int = 5
    kind: str = "guitar"          # guitar | ghl | drums
    native: str = ""              # track/section name in the file, e.g. "PART BASS" / "ExpertDoubleBass"
    warnings: list[str] = field(default_factory=list)


@dataclass
class GhSong:
    resolution: int
    tempos: list[tuple[int, float]] = field(default_factory=list)          # tick, BPM
    meters: list[tuple[int, int, int]] = field(default_factory=list)       # tick, numerator, denominator
    tracks: dict[tuple[str, str], GhTrack] = field(default_factory=dict)   # (part, difficulty)
    offset_ms: float = 0.0
    sections: list[tuple[int, str]] = field(default_factory=list)
    fmt: str = "mid"
    warnings: list[str] = field(default_factory=list)

    def parts(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for (part, diff), t in sorted(self.tracks.items()):
            if t.notes:
                out.setdefault(part, []).append(diff)
        order = DIFFICULTIES + ["expert+"]
        return {p: sorted(ds, key=order.index) for p, ds in out.items()}

    def beat(self, tick: int) -> Fraction:
        return Fraction(tick, self.resolution)

    def timing(self) -> TimingMap:
        bpms = [BpmChange(self.beat(t), bpm) for t, bpm in sorted(self.tempos)] or [BpmChange(Fraction(0), 120.0)]
        if bpms[0].beat != 0:
            bpms.insert(0, BpmChange(Fraction(0), bpms[0].bpm))
        meters = [MeterChange(self.beat(t), n, d) for t, n, d in sorted(self.meters)] or [MeterChange(Fraction(0))]
        if meters[0].beat != 0:
            meters.insert(0, MeterChange(Fraction(0)))
        dedup: list[BpmChange] = []
        for b in bpms:
            if dedup and dedup[-1].beat == b.beat:
                dedup[-1] = b
            else:
                dedup.append(b)
        return TimingMap(offset_ms=self.offset_ms, bpms=dedup, meters=meters)

    def to_chart(self, part: str, difficulty: str, meta: ChartMeta) -> Chart | None:
        track = self.tracks.get((part, difficulty))
        if track is None or not track.notes:
            return None
        notes: list[Note] = []
        for n in track.notes:
            extra = dict(n.flags)
            if track.kind == "drums" and n.lane < 0:
                lane, width = 0, track.lanes
            elif n.lane in (OPEN, GHL_OPEN) and track.kind != "drums" and extra.get("open"):
                lane, width = 0, track.lanes
            else:
                lane, width = n.lane, 1
            dur = self.beat(n.length) if n.length > 0 else Fraction(0)
            notes.append(Note(self.beat(n.tick), lane, width, NoteKind.HOLD if dur else NoteKind.TAP, dur,
                              extra=extra))
        meta.source.setdefault("track", track.native)
        meta.source.setdefault("resolution", self.resolution)
        chart = Chart(meta=meta, lanes=track.lanes, timing=self.timing(), notes=notes,
                      warnings=self.warnings + track.warnings)
        return chart.sort()


# ---------------------------------------------------------------------------------------------------- shared logic

def _hopo_threshold(res: int, fmt: str, o: GhOptions) -> int:
    """Natural-HOPO distance in ticks (inclusive), following YARG.Core (SongEntry.IniBase + Mid/ChartReader)."""
    if o.harmonix and o.hopo_threshold is None:
        return 170 * res // 480
    if o.hopo_threshold:
        base = o.hopo_threshold
    elif o.eighthnote_hopo is not None:
        base = res // (2 if o.eighthnote_hopo else 3)
    elif o.hopofreq is not None and o.hopofreq in range(6):
        base = 4 * res // [24, 16, 12, 8, 6, 4][o.hopofreq]
    elif fmt == "chart":
        return res * 65 // 192
    else:
        base = res // 3
    if o.harmonix:
        return base
    return base + 1 if fmt == "mid" else base + res // 192


def _sustain_cutoff(res: int, fmt: str, o: GhOptions) -> int:
    """Shortest .mid note that is a sustain. A song.ini `sustain_cutoff_threshold` below 1/12 beat (GH3+ rip tools
    write 0 so that short official sustains survive) is raised to `resolution / 12`: those rips write unsustained
    notes as 1-5 tick notes, which no game draws as a sustain."""
    if o.sustain_cutoff is not None:
        return max(o.sustain_cutoff, res // 12) if fmt == "mid" else max(o.sustain_cutoff, 0)
    if o.harmonix:
        return 160 * res // 480
    return res // 3 if fmt == "mid" else 0


class TickSet:
    """Tick ranges merged into disjoint inclusive intervals, for fast membership tests (bisect).

    `include_end=False`: a phrase [start, end) that still covers its start when it has no length (star power, solos,
    open SysEx, forcing notes); `include_end=True`: [start, end] (tap SysEx, which covers its end tick)."""

    def __init__(self, ranges: list[tuple[int, int]] = (), include_end: bool = False) -> None:
        spans = sorted((s, max(e if include_end else e - 1, s)) for s, e in ranges)
        self.starts: list[int] = []
        self.ends: list[int] = []
        for s, e in spans:
            if self.ends and s <= self.ends[-1] + 1:
                self.ends[-1] = max(self.ends[-1], e)
            else:
                self.starts.append(s)
                self.ends.append(e)

    def __contains__(self, tick: int) -> bool:
        import bisect

        i = bisect.bisect_right(self.starts, tick) - 1
        return i >= 0 and tick <= self.ends[i]

    def __bool__(self) -> bool:
        return bool(self.starts)

    def union(self, other: "TickSet") -> "TickSet":
        out = TickSet()
        merged = TickSet(list(zip(self.starts + other.starts, self.ends + other.ends)), include_end=True)
        out.starts, out.ends = merged.starts, merged.ends
        return out


def _resolve_guitar(notes: list[GhNote], threshold: int, mid_subset: bool, force_hopo: TickSet,
                    force_strum: TickSet, flips: set[int], taps: set[int] | TickSet, is_chart: bool) -> None:
    """Set hopo/tap/forced flags on 5- or 6-fret notes. `force_hopo`/`force_strum`: .mid marker ranges (a HOPO
    marker wins over a strum marker, as in YARG); `flips`: ticks with a .chart N 5; `taps`: tap ticks or ranges.
    A tap overrides any forcing."""
    by_tick: dict[int, list[GhNote]] = {}
    for n in notes:
        by_tick.setdefault(n.tick, []).append(n)
    prev_tick: int | None = None
    prev_frets: set[int] = set()
    for tick in sorted(by_tick):
        group = by_tick[tick]
        frets = {n.lane for n in group}
        chord = len(frets) > 1
        natural = False
        if not chord and prev_tick is not None and tick - prev_tick <= threshold:
            fret = next(iter(frets))
            if len(prev_frets) == 1:
                natural = fret not in prev_frets
            else:
                natural = (fret not in prev_frets) if mid_subset else True
        if tick in taps:
            for n in group:
                n.flags["tap"] = True
        else:
            hopo = natural
            mark = "hopo" if tick in force_hopo else "strum" if tick in force_strum else None
            if mark is not None:
                hopo = mark == "hopo"
            elif is_chart and tick in flips:
                hopo = not natural
                mark = "hopo" if hopo else "strum"
            for n in group:
                if hopo:
                    n.flags["hopo"] = True
                if mark is not None:
                    n.flags["forced"] = mark
        prev_tick, prev_frets = tick, frets


def _mark_phrases(notes: list[GhNote], ranges: list[tuple[int, int]], key: str) -> None:
    within = TickSet(ranges)
    if not within:
        return
    for n in notes:
        if n.tick in within:
            n.flags[key] = True


def _cut_sustains(notes: list[GhNote], cutoff: int) -> None:
    for n in notes:
        if n.length < cutoff or n.length <= 0:
            n.length = 0


def _snap_chords(notes: list[GhNote], window: int) -> int:
    """GH1/GH2: notes up to `window` ticks apart are one chord at the earliest note's tick. Returns notes moved."""
    moved = 0
    notes.sort(key=lambda n: (n.tick, n.lane))
    anchor = None
    for n in notes:
        if anchor is not None and 0 < n.tick - anchor <= window:
            n.length = max(0, n.length - (anchor - n.tick))
            n.tick = anchor
            moved += 1
        else:
            anchor = n.tick
    return moved


def _dedupe(notes: list[GhNote]) -> list[GhNote]:
    seen: dict[tuple[int, int], GhNote] = {}
    for n in notes:
        k = (n.tick, n.lane)
        if k not in seen or n.length > seen[k].length:
            seen[k] = n
    return sorted(seen.values(), key=lambda n: (n.tick, n.lane))


# ---------------------------------------------------------------------------------------------------- .mid

def read_mid(data: bytes, options: GhOptions | None = None) -> GhSong:
    o = options or GhOptions()
    mf = parse_midi(data)
    song = GhSong(resolution=mf.resolution, fmt="mid", offset_ms=o.delay_ms)
    _sync_from_midi(mf, song)
    events = mf.track("EVENTS")
    if events is not None:
        for e in events.events:
            if e.kind == "text":
                m = re.match(r"^\[?(?:section|prc)[ _](.+?)\]?$", e.text.strip())
                if m:
                    song.sections.append((e.tick, m.group(1)))
    # star power convention for the whole file
    instrument_tracks = [(MID_TRACKS[t.name], t) for t in mf.tracks if t.name in MID_TRACKS]
    has_116 = any(e.kind == "note_on" and e.note == STAR_POWER for _, t in instrument_tracks for e in t.events)
    sp_note = o.star_power_note or (STAR_POWER if has_116 else SOLO)
    seen: set[str] = set()
    for part, t in instrument_tracks:
        if part in seen:
            song.warnings.append(f"second {t.name} track ignored")
            continue
        seen.add(part)
        if part == "drums":
            _read_mid_drums(t, song, o, sp_note)
        elif part in GHL_PARTS:
            _read_mid_guitar(t, song, part, o, sp_note, ghl=True)
        else:
            _read_mid_guitar(t, song, part, o, sp_note, ghl=False)
    return song


def _sync_from_midi(mf: MidiFile, song: GhSong) -> None:
    for e in mf.tempo_events():
        if e.value > 0:
            song.tempos.append((e.tick, 60_000_000 / e.value))
    for e in mf.time_signatures():
        song.meters.append((e.tick, e.numerator, e.denominator))


def _sysex_phrases(track: MidiTrack) -> dict[tuple[str, int], list[tuple[int, int]]]:
    """Phase Shift SysEx phrases: (difficulty or "all", modifier) -> [(start, end)]."""
    open_: dict[tuple[str, int], int] = {}
    out: dict[tuple[str, int], list[tuple[int, int]]] = {}
    for e in track.events:
        if e.kind != "sysex":
            continue
        d = e.data
        if len(d) < 7 or d[:3] != b"PS\x00" or d[3] != 0:
            continue
        diff = "all" if d[4] == 0xFF else SYSEX_DIFF.get(d[4], "")
        key = (diff, d[5])
        if d[6] == 1:
            open_.setdefault(key, e.tick)
        elif key in open_:
            out.setdefault(key, []).append((open_.pop(key), e.tick))
    return out


def _read_mid_guitar(track: MidiTrack, song: GhSong, part: str, o: GhOptions, sp_note: int, ghl: bool) -> None:
    res = song.resolution
    spans = note_spans(track)
    texts = {e.text.strip().strip("[]").upper() for e in track.events if e.kind == "text"}
    enhanced_opens = "ENHANCED_OPENS" in texts
    sysex = _sysex_phrases(track)
    threshold = _hopo_threshold(res, "mid", o)
    cutoff = _sustain_cutoff(res, "mid", o)
    harmonix_sp = o.harmonix or (not any(s.note == STAR_POWER for s in spans)
                                 and any(s.note in (67, 79, 91) for s in spans))
    global_sp = [(s.tick, s.tick + s.length) for s in spans if s.note == sp_note and not harmonix_sp]
    solo = [(s.tick, s.tick + s.length) for s in spans if s.note == SOLO and sp_note != SOLO and not harmonix_sp]
    # tap note 104 does not cover its end tick; the tap SysEx does (TheNathannator docs, YARG MidReader)
    tap_all = TickSet([(s.tick, s.tick + s.length) for s in spans if s.note == TAP_NOTE]).union(
        TickSet(sysex.get(("all", TAP_PHRASE), []), include_end=True))
    lanes = 6 if ghl else 5
    for diff in DIFFICULTIES:
        base = (GHL_BASE if ghl else DIFF_BASE)[diff]
        notes: list[GhNote] = []
        hopo_ranges: list[tuple[int, int]] = []
        strum_ranges: list[tuple[int, int]] = []
        for s in spans:
            off = s.note - base
            if ghl:
                if 0 <= off <= 6:
                    if off == 0:
                        notes.append(GhNote(s.tick, GHL_OPEN, s.length, {"open": True}))
                    else:   # 1..3 white 1-3 -> lanes 3..5; 4..6 black 1-3 -> lanes 0..2
                        notes.append(GhNote(s.tick, off + 2 if off <= 3 else off - 4, s.length))
                elif off in (7, 8):
                    (hopo_ranges if off == 7 else strum_ranges).append((s.tick, s.tick + s.length))
                continue
            if 0 <= off <= 4:
                notes.append(GhNote(s.tick, off, s.length))
            elif off == -1 and enhanced_opens:
                notes.append(GhNote(s.tick, OPEN, s.length, {"open": True}))
            elif off in (5, 6) and not o.harmonix:
                (hopo_ranges if off == 5 else strum_ranges).append((s.tick, s.tick + s.length))
        if not notes:
            continue
        gt = GhTrack(lanes=lanes, kind="ghl" if ghl else "guitar", native=track.name)
        if o.harmonix:
            moved = _snap_chords(notes, 10 * res // 480)
            if moved:
                gt.warnings.append(f"{diff}: {moved} notes within 10 ticks snapped into chords (GH1/GH2 rule)")
        if not ghl:
            opens = TickSet(sysex.get((diff, OPEN_PHRASE), []))
            if opens:
                for n in notes:
                    if n.tick in opens:
                        n.lane = OPEN
                        n.flags["open"] = True
        notes = _dedupe(notes)
        _cut_sustains(notes, cutoff)
        taps = tap_all.union(TickSet(sysex.get((diff, TAP_PHRASE), []), include_end=True))
        _resolve_guitar(notes, threshold, True, TickSet(hopo_ranges), TickSet(strum_ranges), set(), taps,
                        is_chart=False)
        sp = global_sp if not harmonix_sp else [(s.tick, s.tick + s.length) for s in spans if s.note == base + 7]
        _mark_phrases(notes, sp, "star_power")
        _mark_phrases(notes, solo, "solo")
        gt.notes = notes
        song.tracks[(part, diff)] = gt


def _read_mid_drums(track: MidiTrack, song: GhSong, o: GhOptions, sp_note: int) -> None:
    res = song.resolution
    spans = note_spans(track)
    texts = {e.text.strip().strip("[]").upper() for e in track.events if e.kind == "text"}
    dynamics = "ENABLE_CHART_DYNAMICS" in texts
    five = o.five_lane_drums
    if five is None:
        five = any(s.note - DIFF_BASE[d] == 5 for s in spans for d in DIFFICULTIES)
    lanes = 5 if five else 4
    cutoff = _sustain_cutoff(res, "mid", o)
    sp = [(s.tick, s.tick + s.length) for s in spans if s.note == (sp_note if sp_note != SOLO else STAR_POWER)]
    if not sp and sp_note == SOLO:
        sp = [(s.tick, s.tick + s.length) for s in spans if s.note == SOLO]
    toms = {110: 1, 111: 2, 112: 3}
    tom_ranges = {lane: TickSet([(s.tick, s.tick + s.length) for s in spans if s.note == n])
                  for n, lane in toms.items()}
    pro = bool(o.pro_drums) and not five
    for diff in DIFFICULTIES:
        base = DIFF_BASE[diff]
        notes: list[GhNote] = []
        double: list[GhNote] = []
        for s in spans:
            off = s.note - base
            if off == 0:
                notes.append(GhNote(s.tick, -1, s.length, {"kick": True}))
            elif 1 <= off <= (5 if five else 4):
                n = GhNote(s.tick, off - 1, s.length)
                if dynamics and s.velocity == 127:
                    n.flags["accent"] = True
                elif dynamics and s.velocity == 1:
                    n.flags["ghost"] = True
                if pro and off - 1 in (1, 2, 3) and s.tick not in tom_ranges[off - 1]:
                    n.flags["cymbal"] = True
                notes.append(n)
            elif off == -1 and diff == "expert":
                double.append(GhNote(s.tick, -1, s.length, {"kick": True, "double_kick": True}))
        if not notes:
            continue
        for variant, extra_notes in ((diff, []), ("expert+", double)):
            if variant == "expert+" and (diff != "expert" or not double):
                continue
            vs = [GhNote(n.tick, n.lane, n.length, dict(n.flags)) for n in notes + extra_notes]
            vs = _dedupe_drums(vs)
            _cut_sustains(vs, cutoff)
            _mark_phrases(vs, sp, "star_power")
            song.tracks[("drums", variant)] = GhTrack(notes=vs, lanes=lanes, kind="drums", native=track.name)


def _dedupe_drums(notes: list[GhNote]) -> list[GhNote]:
    out: dict[tuple[int, int], GhNote] = {}
    for n in notes:
        k = (n.tick, n.lane)
        if k in out and not n.flags.get("double_kick"):
            continue
        if k in out and n.flags.get("double_kick") and not out[k].flags.get("double_kick"):
            continue       # a 2x kick on top of a normal kick is the same hit
        out[k] = n
    return sorted(out.values(), key=lambda n: (n.tick, n.lane))


# ---------------------------------------------------------------------------------------------------- .chart

_CHART_SECTION = re.compile(r"^(Easy|Medium|Hard|Expert)(.+)$")


def parse_chart_sections(text: str) -> dict[str, list[tuple[str, str]]]:
    """[Section] { key = value } blocks; values keep their raw text (several entries can share a key)."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current: list[tuple[str, str]] | None = None
    name = ""
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            current = sections.setdefault(name, [])
            continue
        if line in ("{", "}"):
            continue
        if current is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        current.append((k.strip(), v.strip()))
    return sections


def read_chart(text: str, options: GhOptions | None = None) -> GhSong:
    o = options or GhOptions()
    sec = parse_chart_sections(text)
    meta = {k: v.strip('"') for k, v in sec.get("Song", [])}
    res = int(float(meta.get("Resolution", "192")))
    try:
        offset = float(meta.get("Offset", "0") or 0)
    except ValueError:
        offset = 0.0
    song = GhSong(resolution=res, fmt="chart", offset_ms=o.delay_ms + offset * 1000.0)
    for k, v in sec.get("SyncTrack", []):
        if not k.isdigit():
            continue
        parts = v.split()
        if parts[0] == "B" and len(parts) > 1:
            song.tempos.append((int(k), int(parts[1]) / 1000.0))
        elif parts[0] == "TS" and len(parts) > 1:
            den = 2 ** int(parts[2]) if len(parts) > 2 else 4
            song.meters.append((int(k), int(parts[1]), den))
    for k, v in sec.get("Events", []):
        if k.isdigit() and v.startswith("E"):
            txt = v[1:].strip().strip('"')
            m = re.match(r"^\[?(?:section|prc)[ _](.+?)\]?$", txt)
            if m:
                song.sections.append((int(k), m.group(1)))
    threshold = _hopo_threshold(res, "chart", o)
    cutoff = _sustain_cutoff(res, "chart", o)
    for name, rows in sec.items():
        m = _CHART_SECTION.match(name)
        if not m:
            continue
        diff, inst = m.group(1).lower(), m.group(2)
        part = CHART_INSTRUMENTS.get(inst)
        if part is None:
            continue
        if part == "drums":
            _read_chart_drums(rows, song, diff, name, o, cutoff)
        else:
            _read_chart_guitar(rows, song, part, diff, name, threshold, cutoff, ghl=part in GHL_PARTS)
    return song


def _chart_rows(rows: list[tuple[str, str]]):
    for k, v in rows:
        if not k.isdigit():
            continue
        parts = v.split()
        if not parts:
            continue
        yield int(k), parts


def _read_chart_guitar(rows, song: GhSong, part: str, diff: str, native: str, threshold: int, cutoff: int,
                       ghl: bool) -> None:
    notes: list[GhNote] = []
    flips: set[int] = set()
    taps: set[int] = set()
    sp: list[tuple[int, int]] = []
    solo: list[tuple[int, int]] = []
    solo_start: int | None = None
    ghl_lane = {0: 3, 1: 4, 2: 5, 3: 0, 4: 1, 8: 2}
    for tick, p in _chart_rows(rows):
        if p[0] == "N" and len(p) >= 3:
            fret, length = int(p[1]), int(p[2])
            if fret == 5:
                flips.add(tick)
            elif fret == 6:
                taps.add(tick)
            elif fret == 7:
                notes.append(GhNote(tick, GHL_OPEN if ghl else OPEN, length, {"open": True}))
            elif ghl and fret in ghl_lane:
                notes.append(GhNote(tick, ghl_lane[fret], length))
            elif not ghl and 0 <= fret <= 4:
                notes.append(GhNote(tick, fret, length))
        elif p[0] == "S" and len(p) >= 3 and p[1] == "2":
            sp.append((tick, tick + int(p[2])))
        elif p[0] == "E" and len(p) >= 2:
            if p[1] == "solo":
                solo_start = tick
            elif p[1] == "soloend" and solo_start is not None:
                solo.append((solo_start, tick + 1))
                solo_start = None
    if not notes:
        return
    notes = _dedupe(notes)
    _cut_sustains(notes, cutoff)
    _resolve_guitar(notes, threshold, False, TickSet(), TickSet(), flips, taps, is_chart=True)
    _mark_phrases(notes, sp, "star_power")
    _mark_phrases(notes, solo, "solo")
    song.tracks[(part, diff)] = GhTrack(notes=notes, lanes=6 if ghl else 5, kind="ghl" if ghl else "guitar",
                                        native=native)


def _read_chart_drums(rows, song: GhSong, diff: str, native: str, o: GhOptions, cutoff: int) -> None:
    raw: list[tuple[int, int, int]] = []
    sp: list[tuple[int, int]] = []
    for tick, p in _chart_rows(rows):
        if p[0] == "N" and len(p) >= 3:
            raw.append((tick, int(p[1]), int(p[2])))
        elif p[0] == "S" and len(p) >= 3 and p[1] == "2":
            sp.append((tick, tick + int(p[2])))
    five = o.five_lane_drums
    if five is None:
        five = any(v == 5 for _, v, _ in raw)
    lanes = 5 if five else 4
    accents = {(t, v - 33) for t, v, _ in raw if 34 <= v <= 38}
    ghosts = {(t, v - 39) for t, v, _ in raw if 40 <= v <= 44}
    cymbals = {(t, v - 64) for t, v, _ in raw if 66 <= v <= 68}
    notes: list[GhNote] = []
    double: list[GhNote] = []
    for t, v, length in raw:
        if v == 0:
            notes.append(GhNote(t, -1, length, {"kick": True}))
        elif 1 <= v <= (5 if five else 4):
            n = GhNote(t, v - 1, length)
            if (t, v) in accents:
                n.flags["accent"] = True
            if (t, v) in ghosts:
                n.flags["ghost"] = True
            if o.pro_drums and not five and (t, v) in cymbals:
                n.flags["cymbal"] = True
            notes.append(n)
        elif v == 32:
            double.append(GhNote(t, -1, length, {"kick": True, "double_kick": True}))
    if not notes:
        return
    for variant, extra_notes in ((diff, []), ("expert+", double)):
        if variant == "expert+" and (diff != "expert" or not double):
            continue
        vs = _dedupe_drums([GhNote(n.tick, n.lane, n.length, dict(n.flags)) for n in notes + extra_notes])
        _cut_sustains(vs, cutoff)
        _mark_phrases(vs, sp, "star_power")
        song.tracks[("drums", variant)] = GhTrack(notes=vs, lanes=lanes, kind="drums", native=native)


# ---------------------------------------------------------------------------------------------------- song.ini

def parse_ini(text: str) -> dict[str, str]:
    """song.ini: `key = value` lines (first section only, keys lowercased). Clone Hero colour tags are kept."""
    out: dict[str, str] = {}
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#", "//")) or line.startswith("["):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out.setdefault(k.strip().lower(), v.strip())
    return out


def strip_tags(text: str) -> str:
    """Remove Clone Hero rich-text tags (<color=...>, <b>, ...) from a song.ini value."""
    return re.sub(r"</?[a-zA-Z]+(=[^>]*)?>", "", text or "").strip()


def read(data: bytes, name: str, options: GhOptions | None = None) -> GhSong:
    """Read a chart file by name: `.chart` as text, anything else as MIDI."""
    if name.lower().endswith(".chart"):
        return read_chart(data.decode("utf-8-sig", errors="replace"), options)
    return read_mid(data, options)


# ---------------------------------------------------------------------------------------------------- song lists

def qs_key(text: str) -> int:
    """Neversoft's localised-string key: CRC-32 of the UTF-16-LE text, no final inversion (`qs(0x...)` in .q)."""
    import zlib

    return zlib.crc32(text.encode("utf-16-le")) ^ 0xFFFFFFFF


_Q_VALUE = re.compile(r"""^qs\((?:0x([0-9a-fA-F]+)|"(.*)")\)$""")


def _q_value(raw: str) -> Any:
    raw = raw.strip()
    m = _Q_VALUE.match(raw)
    if m:
        if m.group(1) is not None:
            return {"qs": int(m.group(1), 16)}
        return m.group(2).replace("\\L", "")
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1].replace("\\L", "")
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_q_songlist(text: str) -> dict[str, dict[str, Any]]:
    """Song entries of a Neversoft `*songlist*.q` script: `<list>_props = { <id> = { key = value ... } }`.

    Values: plain strings, `qs("...")` text (the `\\L` case marker removed), `{"qs": key}` for hashed strings
    (see `qs_key`), ints, or the raw word. Nested blocks inside a song (challenge_props ...) are skipped."""
    songs: dict[str, dict[str, Any]] = {}
    depth = 0
    in_props = False
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        opens, closes = line.count("{"), line.count("}")
        if depth == 0 and line.endswith("{"):
            in_props = line.split("=")[0].strip().endswith("_props")
        elif depth == 1 and in_props and line.endswith("{") and "=" in line:
            sid = line.split("=")[0].strip()
            current = songs.setdefault(sid.lower(), {"id": sid})
        elif depth == 2 and current is not None and "=" in line and not opens:
            k, v = line.split("=", 1)
            current[k.strip().lower()] = _q_value(v)
        depth += opens - closes
        if depth <= 1:
            current = None
        if depth <= 0:
            depth, in_props = 0, False
    return songs


def q_year(entry: dict[str, Any]) -> int | None:
    """A .q entry's release year: `year_num`, a numeric `year`, ", 1995" text, or a hashed "\\L, 1995"."""
    for key in ("year_num", "year"):
        v = entry.get(key)
        if isinstance(v, int) and 1900 <= v <= 2100:
            return v
        if isinstance(v, str):
            m = re.search(r"(19|20)\d\d", v)
            if m:
                return int(m.group(0))
        if isinstance(v, dict) and "qs" in v:
            y = _YEAR_KEYS.get(v["qs"])
            if y:
                return y
    return None


_YEAR_KEYS = {qs_key(fmt.format(y)): y for y in range(1900, 2031) for fmt in ("\\L, {}", ", {}", "\\L{}", "{}")}


def parse_dta(text: str) -> list[Any]:
    """Harmonix DTA (Lisp-like song lists): nested lists of symbols, numbers and strings.

    `;` comments and `#ifdef/#else/#endif` lines are dropped (both branches are kept, which is harmless for the
    fields read here); `{...}` and `[...]` are read as lists too."""
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[(){}\[\]]|[^\s(){}\[\]"]+', "\n".join(
        line.split(";", 1)[0] if '"' not in line.split(";", 1)[0] or line.count('"') % 2 == 0 else line
        for line in text.splitlines() if not line.lstrip().startswith("#")))
    stack: list[list[Any]] = [[]]
    for tok in tokens:
        if tok in "({[":
            stack.append([])
        elif tok in ")}]":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        elif tok.startswith('"'):
            stack[-1].append({"str": tok[1:-1]})
        else:
            try:
                stack[-1].append(int(tok))
            except ValueError:
                try:
                    stack[-1].append(float(tok))
                except ValueError:
                    stack[-1].append(tok)
    return stack[0]


def dta_songs(text: str) -> list[dict[str, Any]]:
    """Songs of a GH2/GH80s `songs*.dta`: id, name, artist, midi file, audio tracks, hopo_threshold."""
    out = []
    for node in parse_dta(text):
        if not isinstance(node, list) or not node or not isinstance(node[0], str):
            continue
        rec: dict[str, Any] = {"id": node[0]}
        for child in node[1:]:
            if not isinstance(child, list) or not child:
                continue
            key = child[0]
            if key in ("name", "artist"):
                rec[key] = _first_string(child[1:])
            elif key == "song":
                for sub in child[1:]:
                    if not isinstance(sub, list) or not sub:
                        continue
                    if sub[0] == "midi_file" and len(sub) > 1:
                        rec["midi_file"] = sub[1]["str"] if isinstance(sub[1], dict) else str(sub[1])
                    elif sub[0] == "hopo_threshold" and len(sub) > 1:
                        rec["hopo_threshold"] = sub[1]
                    elif sub[0] == "tracks" and len(sub) > 1 and isinstance(sub[1], list):
                        rec["tracks"] = _dta_track_names(sub[1:])
        if "midi_file" in rec and rec.get("name"):
            out.append(rec)
    return out


def _dta_track_names(items: list[Any]) -> list[str]:
    """`(tracks ((guitar (2 3)) (bass 4)))` -> ["guitar", "bass"] (any nesting depth)."""
    names: list[str] = []
    for it in items:
        if isinstance(it, list) and it:
            if isinstance(it[0], str):
                names.append(it[0])
            else:
                names += _dta_track_names(it)
    return names


def _first_string(items: list[Any]) -> str:
    for it in items:
        if isinstance(it, dict) and "str" in it:
            return it["str"]
        if isinstance(it, list):
            s = _first_string(it)
            if s:
                return s
    return ""
