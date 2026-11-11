"""The conversion-rules layer: an ordered pipeline of small, named rules that turn a source `Chart` into a mania-ready one.

**Status: placeholders.** The rules that decide how each mechanic becomes mania (docs/research/vsrg-mechanics.md) are
not decided yet. What is here is the plumbing, plus naive baseline rules so the pipeline runs end to end:

    snap -> kinds -> slides -> lanes -> min-hold -> collisions

Each rule records what it changed in the `ConvertContext.report`, so the agent loop can see which rules are lossy and
where. To add or replace a rule: subclass `Rule`, register it with `@register`, and name it in a profile
(`PROFILES` below or a JSON profile file passed with `--profile`).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable

from ..model import Chart, Note, NoteKind


@dataclass
class ConvertContext:
    keys: int
    tier: str = ""                             # canonical ladder slot of the chart
    options: dict[str, Any] = field(default_factory=dict)
    report: list[dict[str, Any]] = field(default_factory=list)

    def log(self, rule: str, what: str, **data: Any) -> None:
        self.report.append({"rule": rule, "what": what, **data})


class Rule:
    name = "rule"

    def __init__(self, **options: Any) -> None:
        self.options = options

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:   # pragma: no cover - interface
        raise NotImplementedError


RULES: dict[str, Callable[..., Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    RULES[cls.name] = cls
    return cls


# --------------------------------------------------------------------------------------------- baseline rules

ALLOWED_DIVISORS = (1, 2, 3, 4, 6, 8, 12, 16)


@register
class SnapBeats(Rule):
    """Snap each note start/end to the nearest allowed beat divisor (removes pixel rounding from image sources)."""

    name = "snap"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        divisors = self.options.get("divisors", ALLOWED_DIVISORS)
        tolerance = Fraction(self.options.get("tolerance", "1/96"))   # beats; farther than this is reported
        moved = far = 0
        for n in chart.notes:
            points = [n.beat, n.end_beat] if n.duration else [n.beat]
            snapped = [snap(p, divisors) for p in points]
            for before, after in zip(points, snapped):
                if after != before:
                    moved += 1
                    far += abs(after - before) > tolerance
            n.beat = snapped[0]
            n.duration = max(Fraction(0), snapped[1] - snapped[0]) if n.duration else Fraction(0)
        ctx.log(self.name, "snapped", moved=moved, beyond_tolerance=far)
        return chart


def snap(beat: Fraction, divisors: tuple[int, ...] = ALLOWED_DIVISORS) -> Fraction:
    whole = math.floor(beat)
    frac = beat - whole
    best = min((Fraction(round(frac * d), d) for d in divisors), key=lambda f: (abs(f - frac), f.denominator))
    return whole + best


DEFAULT_KIND_MAP = {
    # placeholder: decided later per game (docs/research/vsrg-mechanics.md)
    NoteKind.TAP.value: "tap",
    NoteKind.EX_TAP.value: "tap",
    NoteKind.HOLD.value: "hold",
    NoteKind.SLIDE.value: "hold",
    NoteKind.FLICK.value: "tap",
    NoteKind.AIR.value: "drop",
    NoteKind.AIR_HOLD.value: "drop",
    NoteKind.MINE.value: "drop",
    NoteKind.SCRATCH.value: "tap",
    NoteKind.KNOB.value: "drop",
    NoteKind.OTHER.value: "drop",
}


@register
class MapKinds(Rule):
    """Turn every note kind into tap, hold or nothing, by a table (options: kinds={kind: tap|hold|drop})."""

    name = "kinds"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        table = {**DEFAULT_KIND_MAP, **self.options.get("kinds", {})}
        kept: list[Note] = []
        dropped: dict[str, int] = {}
        for n in chart.notes:
            action = table.get(n.kind.value, "drop")
            if action == "drop":
                dropped[n.kind.value] = dropped.get(n.kind.value, 0) + 1
                continue
            n.extra.setdefault("source_kind", n.kind.value)
            if action == "hold" and n.duration > 0:
                n.kind = NoteKind.HOLD if n.kind != NoteKind.SLIDE else NoteKind.SLIDE
            else:
                n.kind = NoteKind.TAP
                n.duration = Fraction(0)
            kept.append(n)
        chart.notes = kept
        ctx.log(self.name, "dropped", counts=dropped)
        return chart


@register
class SlidesToHolds(Rule):
    """Placeholder: a slide becomes a hold at its starting position (the path is ignored)."""

    name = "slides"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        count = 0
        for n in chart.notes:
            if n.kind == NoteKind.SLIDE:
                n.kind = NoteKind.HOLD
                n.path = []
                count += 1
        ctx.log(self.name, "flattened", count=count)
        return chart


@register
class LaneMapCentre(Rule):
    """Placeholder: column = which of `keys` equal bands of the source surface the note's centre falls in."""

    name = "lanes"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        keys = ctx.keys
        src = chart.lanes
        for n in chart.notes:
            n.extra.setdefault("source_lane", [n.lane, n.width])
            n.lane = min(keys - 1, max(0, int(n.centre / src * keys)))
            n.width = 1.0
        chart.lanes = keys
        ctx.log(self.name, "mapped", source_lanes=src, keys=keys)
        return chart


@register
class MinHold(Rule):
    """Holds shorter than `min_beats` (default 1/12, the ranking-criteria minimum) become taps."""

    name = "min-hold"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        minimum = Fraction(self.options.get("min_beats", "1/12"))
        count = 0
        for n in chart.notes:
            if n.kind == NoteKind.HOLD and n.duration < minimum:
                n.kind, n.duration = NoteKind.TAP, Fraction(0)
                count += 1
        ctx.log(self.name, "shortened", count=count)
        return chart


@register
class ResolveCollisions(Rule):
    """Make the chart legal: no two notes in a column at once, nothing starting inside a hold in its column.
    A colliding note moves to the nearest free column; if there is none it is dropped. Both are reported."""

    name = "collisions"

    def apply(self, chart: Chart, ctx: ConvertContext) -> Chart:
        keys = chart.lanes
        busy_until: list[Fraction] = [Fraction(-1)] * keys      # last end beat per column
        last_start: list[Fraction | None] = [None] * keys
        kept: list[Note] = []
        moved = dropped = 0
        for n in sorted(chart.notes, key=lambda n: (n.beat, -n.duration, n.lane)):
            def free(c: int) -> bool:
                return last_start[c] != n.beat and busy_until[c] < n.beat

            col = int(n.lane)
            if not free(col):
                options = sorted(range(keys), key=lambda c: (abs(c - col), c))
                alt = next((c for c in options if free(c)), None)
                if alt is None:
                    dropped += 1
                    continue
                col, moved = alt, moved + 1
                n.lane = col
            last_start[col] = n.beat
            busy_until[col] = max(busy_until[col], n.end_beat if n.duration else n.beat)
            kept.append(n)
        chart.notes = kept
        ctx.log(self.name, "resolved", moved=moved, dropped=dropped)
        return chart


# --------------------------------------------------------------------------------------------- pipeline

PROFILES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "baseline": [("snap", {}), ("kinds", {}), ("slides", {}), ("lanes", {}), ("min-hold", {}), ("collisions", {})],
}


@dataclass
class Pipeline:
    rules: list[Rule]

    @classmethod
    def from_profile(cls, profile: str | list[tuple[str, dict[str, Any]]] = "baseline") -> "Pipeline":
        steps = PROFILES[profile] if isinstance(profile, str) else profile
        return cls([RULES[name](**opts) for name, opts in steps])

    def run(self, chart: Chart, ctx: ConvertContext) -> Chart:
        out = copy.deepcopy(chart)
        out.meta.source = {**out.meta.source, "converted_from_lanes": chart.lanes}
        for rule in self.rules:
            out = rule.apply(out, ctx)
        return out.sort()
