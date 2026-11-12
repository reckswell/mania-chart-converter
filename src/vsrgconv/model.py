"""The normalised intermediate chart format (IR).

Every source adapter produces a `Chart`; every conversion rule reads and writes `Chart`s; the osu!mania writer is the
only step that leaves this format. It is deliberately lossless for every mechanic listed in
docs/research/vsrg-mechanics.md, so a rule can decide later what to do with each one.

Conventions:
- Time is in **beats** from the chart's beat 0, as exact `Fraction`s. Milliseconds come from the `TimingMap` only at
  the end (osu writer, lint).
- Horizontal position is **source lane units**: `lane` is the left edge, `width` the size, on a surface of
  `Chart.lanes` units (16 for Chunithm, 12 for Sekai, 7 for Bandori, 4 for DDR ...). Fixed-lane games use width 1.
- A note's `kind` is one of `NoteKind`; anything game-specific goes in `extra` (air direction, slide easing, ...).
"""
from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from fractions import Fraction
from typing import Any, Iterable


class NoteKind(str, Enum):
    TAP = "tap"            # plain note
    EX_TAP = "ex_tap"      # emphasised tap (Chunithm ExTAP, Sekai critical)
    HOLD = "hold"          # held in place
    SLIDE = "slide"        # held while moving along `path`
    FLICK = "flick"        # gesture; direction in extra["direction"]
    AIR = "air"            # vertical input (Chunithm AIR, Arcaea arctap); direction in extra
    AIR_HOLD = "air_hold"  # sustained vertical input (Chunithm AIR-HOLD, Arcaea arc)
    MINE = "mine"          # avoid (Chunithm DAMAGE, PIU/StepMania mine, DDR shock)
    SCRATCH = "scratch"    # IIDX turntable, GH strum-only
    KNOB = "knob"          # SDVX laser segment; path holds positions 0..1
    OTHER = "other"


@dataclass
class PathPoint:
    """A control point of a slide/laser: beat offset from the note start, lane and width at that point."""

    beat: Fraction
    lane: float
    width: float = 1.0
    easing: str = "linear"   # linear | in | out | inout | step (slam)


@dataclass
class Note:
    beat: Fraction
    lane: float
    width: float = 1.0
    kind: NoteKind = NoteKind.TAP
    duration: Fraction = Fraction(0)          # beats; 0 for taps
    path: list[PathPoint] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def end_beat(self) -> Fraction:
        return self.beat + self.duration

    @property
    def is_long(self) -> bool:
        return self.duration > 0

    @property
    def centre(self) -> float:
        return self.lane + self.width / 2


@dataclass
class BpmChange:
    beat: Fraction
    bpm: float


@dataclass
class MeterChange:
    """Time signature from this beat on. `beat` must be on a measure boundary."""

    beat: Fraction
    numerator: int = 4
    denominator: int = 4

    @property
    def beats_per_measure(self) -> Fraction:
        return Fraction(self.numerator * 4, self.denominator)


@dataclass
class ScrollChange:
    """Visual-only speed multiplier (soflan, PIU scroll, Arcaea timing groups). 0 = stop, negative = reverse."""

    beat: Fraction
    speed: float


@dataclass
class TimingMap:
    """Tempo, metre and scroll for a chart, plus the audio offset of beat 0."""

    offset_ms: float = 0.0
    bpms: list[BpmChange] = field(default_factory=list)
    meters: list[MeterChange] = field(default_factory=lambda: [MeterChange(Fraction(0))])
    scrolls: list[ScrollChange] = field(default_factory=list)
    # Stops: beat, duration in ms. Time passes, beats do not; a note on the stop's beat is hit before the pause.
    stops: list[tuple[Fraction, float]] = field(default_factory=list)
    # Delays (StepMania #DELAYS): like stops, but the pause comes before the notes on that beat.
    delays: list[tuple[Fraction, float]] = field(default_factory=list)
    # Warps (StepMania #WARPS, negative BPMs/stops): beat, length in beats. The warped beats take no time; notes inside
    # a warp cannot be hit (readers mark them as fakes).
    warps: list[tuple[Fraction, Fraction]] = field(default_factory=list)

    def sorted(self) -> "TimingMap":
        self.bpms.sort(key=lambda b: b.beat)
        self.meters.sort(key=lambda m: m.beat)
        self.scrolls.sort(key=lambda s: s.beat)
        self.stops.sort(key=lambda s: s[0])
        self.delays.sort(key=lambda s: s[0])
        self.warps.sort(key=lambda s: s[0])
        return self

    def ms_at(self, beat: Fraction | float) -> float:
        """Milliseconds from audio start at a beat: piecewise constant BPM, minus warped beats, plus stops before it
        and delays at or before it."""
        if not self.bpms:
            raise ValueError("timing map has no BPM")
        beat = Fraction(beat)
        ms = self.offset_ms + self._tempo_ms(beat)
        for at, length in self.warps:
            if beat > at:
                ms -= self._tempo_ms(min(beat, at + length)) - self._tempo_ms(at)
        for at, length in self.stops:
            if at < beat:
                ms += length
        for at, length in self.delays:
            if at <= beat:
                ms += length
        return ms

    def ms_lookup(self) -> Callable[[Fraction], float]:
        """`ms_at` for many lookups: bisects precomputed sums instead of rescanning every change on each call (a PIU
        co-op chart has 330,000 notes and 784 warps). Same values as ms_at, up to float rounding."""
        if not self.bpms:
            raise ValueError("timing map has no BPM")
        bpms = sorted(self.bpms, key=lambda b: b.beat)
        change_beats = [b.beat for b in bpms[1:]]
        seg_start, seg_ms, seg_rate = [Fraction(0)], [0.0], [60000.0 / bpms[0].bpm]
        for b in bpms[1:]:
            seg_ms.append(seg_ms[-1] + float(b.beat - seg_start[-1]) * seg_rate[-1])
            seg_start.append(b.beat)
            seg_rate.append(60000.0 / b.bpm)

        def tempo(beat: Fraction) -> float:
            i = bisect_left(change_beats, beat)
            return seg_ms[i] + float(beat - seg_start[i]) * seg_rate[i]

        warps = sorted(self.warps)
        warp_at = [w[0] for w in warps]
        warp_ms, warp_end = [0.0], [Fraction(-10**18)]     # prefix sums of whole warps, latest end so far
        for at, length in warps:
            warp_ms.append(warp_ms[-1] + tempo(at + length) - tempo(at))
            warp_end.append(max(warp_end[-1], at + length))
        stops = sorted(self.stops)
        stop_at, stop_ms = [s[0] for s in stops], [0.0]
        for _, length in stops:
            stop_ms.append(stop_ms[-1] + length)
        delays = sorted(self.delays)
        delay_at, delay_ms = [d[0] for d in delays], [0.0]
        for _, length in delays:
            delay_ms.append(delay_ms[-1] + length)

        def ms(beat: Fraction | float) -> float:
            beat = Fraction(beat)
            out = self.offset_ms + tempo(beat)
            j = bisect_left(warp_at, beat)                  # warps that start before this beat
            if j:
                if warp_end[j] <= beat:                     # all of them are behind it
                    out -= warp_ms[j]
                elif warp_end[j - 1] <= beat:               # inside the last one
                    at, length = warps[j - 1]
                    out -= warp_ms[j - 1] + tempo(min(beat, at + length)) - tempo(at)
                else:                                       # overlapping warps: the plain way
                    for at, length in warps[:j]:
                        out -= tempo(min(beat, at + length)) - tempo(at)
            out += stop_ms[bisect_left(stop_at, beat)]
            out += delay_ms[bisect_right(delay_at, beat)]
            return out

        return ms

    def _tempo_ms(self, beat: Fraction) -> float:
        bpms = sorted(self.bpms, key=lambda b: b.beat)
        ms = 0.0
        prev, current = Fraction(0), bpms[0].bpm   # the first BPM also covers beats before 0
        for change in bpms[1:]:
            if change.beat >= beat:
                break
            ms += float(change.beat - prev) * 60000.0 / current
            prev, current = change.beat, change.bpm
        return ms + float(beat - prev) * 60000.0 / current

    def bpm_at(self, beat: Fraction) -> float:
        current = self.bpms[0].bpm
        for change in sorted(self.bpms, key=lambda b: b.beat):
            if change.beat <= beat:
                current = change.bpm
        return current

    def measure_starts(self, end_beat: Fraction) -> list[Fraction]:
        """Beats where each measure starts, from beat 0 up to end_beat."""
        meters = sorted(self.meters, key=lambda m: m.beat) or [MeterChange(Fraction(0))]
        starts: list[Fraction] = []
        beat = Fraction(0)
        idx = 0
        while beat <= end_beat:
            while idx + 1 < len(meters) and meters[idx + 1].beat <= beat:
                idx += 1
            starts.append(beat)
            beat += meters[idx].beats_per_measure
        return starts

    def main_bpm(self, end_beat: Fraction) -> float:
        """The BPM held for the longest time: osu!'s 'mode BPM', used for scroll normalisation."""
        bpms = sorted(self.bpms, key=lambda b: b.beat)
        totals: dict[float, float] = {}
        for i, change in enumerate(bpms):
            stop = bpms[i + 1].beat if i + 1 < len(bpms) else max(end_beat, change.beat)
            totals[change.bpm] = totals.get(change.bpm, 0.0) + float(stop - change.beat) * 60000.0 / change.bpm
        return max(totals.items(), key=lambda kv: kv[1])[0]


@dataclass
class ChartMeta:
    game: str                    # registry key, e.g. "chunithm"
    song_id: str                 # source's song id
    title: str
    artist: str = ""
    title_romaji: str = ""
    artist_romaji: str = ""
    genre: str = ""              # native genre name
    difficulty: str = ""         # canonical ladder slot (games/registry.py)
    difficulty_native: str = ""  # e.g. "MASTER"
    level: str = ""              # e.g. "14+"
    constant: float | None = None
    designer: str = ""
    bpm_display: str = ""
    audio: str = ""              # audio file name, if known
    source: dict[str, Any] = field(default_factory=dict)   # adapter refs (urls, ids)
    note_counts: dict[str, int] = field(default_factory=dict)  # published counts, for checking readers


@dataclass
class Chart:
    meta: ChartMeta
    lanes: int
    timing: TimingMap
    notes: list[Note] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # anything a reader or rule could not do exactly

    def sort(self) -> "Chart":
        self.notes.sort(key=lambda n: (n.beat, n.lane, n.kind.value))
        self.timing.sorted()
        return self

    @property
    def end_beat(self) -> Fraction:
        return max((n.end_beat for n in self.notes), default=Fraction(0))

    def count_by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.notes:
            out[n.kind.value] = out.get(n.kind.value, 0) + 1
        return out

    # ---- JSON (de)serialisation; fractions are written as "n/d" strings ----
    def to_dict(self) -> dict[str, Any]:
        d = _encode(asdict(self))
        d["notes"] = [_compact_note(n) for n in d["notes"]]
        return d

    def to_json(self, **kw: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kw)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chart":
        t = d["timing"]
        timing = TimingMap(
            offset_ms=t.get("offset_ms", 0.0),
            bpms=[BpmChange(_frac(b["beat"]), b["bpm"]) for b in t.get("bpms", [])],
            meters=[MeterChange(_frac(m["beat"]), m["numerator"], m["denominator"]) for m in t.get("meters", [])],
            scrolls=[ScrollChange(_frac(s["beat"]), s["speed"]) for s in t.get("scrolls", [])],
            stops=[(_frac(s[0]), s[1]) for s in t.get("stops", [])],
            delays=[(_frac(s[0]), s[1]) for s in t.get("delays", [])],
            warps=[(_frac(w[0]), _frac(w[1])) for w in t.get("warps", [])],
        )
        notes = [
            Note(
                beat=_frac(n["beat"]),
                lane=n["lane"],
                width=n.get("width", 1.0),
                kind=NoteKind(n.get("kind", "tap")),
                duration=_frac(n.get("duration", "0")),
                path=[PathPoint(_frac(p["beat"]), p["lane"], p.get("width", 1.0), p.get("easing", "linear"))
                      for p in n.get("path", [])],
                extra=n.get("extra", {}),
            )
            for n in d.get("notes", [])
        ]
        return cls(meta=ChartMeta(**d["meta"]), lanes=d["lanes"], timing=timing, notes=notes,
                   warnings=d.get("warnings", []))

    @classmethod
    def from_json(cls, s: str) -> "Chart":
        return cls.from_dict(json.loads(s))


def _frac(v: Any) -> Fraction:
    return Fraction(v) if not isinstance(v, Fraction) else v


NOTE_DEFAULTS = {"width": 1.0, "kind": "tap", "duration": "0", "path": [], "extra": {}}
POINT_DEFAULTS = {"width": 1.0, "easing": "linear"}


def _compact_note(n: dict[str, Any]) -> dict[str, Any]:
    """Leave out fields at their default value; `from_dict` fills them back in. Keeps big charts small on disk."""
    out = {k: v for k, v in n.items() if NOTE_DEFAULTS.get(k, object()) != v}
    if "path" in out:
        out["path"] = [{k: v for k, v in p.items() if POINT_DEFAULTS.get(k, object()) != v} for p in out["path"]]
    return out


def _encode(v: Any) -> Any:
    if isinstance(v, Fraction):
        return str(v)
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in v]
    return v


def notes_at(notes: Iterable[Note], beat: Fraction) -> list[Note]:
    return [n for n in notes if n.beat == beat]
