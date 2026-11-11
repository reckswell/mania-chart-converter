"""Checks a mania-ready chart against the machine-checkable parts of docs/research/osu-mania-mapping.md.

`errors` are ranking-criteria rules (the chart is wrong). `warnings` are guidelines. `metrics` are numbers the agent
loop tracks between iterations (jacks, chord sizes, hand balance, snap use) to tell whether a rule change helped.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ..model import Chart, NoteKind

ALLOWED_KEYS = {4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18}
CHORD_LIMIT = {"easy": 2, "normal": 3, "hard": 4}          # 7K-ish guideline; 4K is one less (see doc section 6)
MIN_HOLD = {"easy": Fraction(1), "normal": Fraction(1, 2), "hard": Fraction(1, 4)}


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "metrics": self.metrics}


def lint(chart: Chart, tier: str = "") -> LintReport:
    r = LintReport()
    keys = chart.lanes
    notes = sorted(chart.notes, key=lambda n: (n.beat, n.lane))
    if keys not in ALLOWED_KEYS:
        r.errors.append(f"{keys}K is not an allowed key count")

    # same-column overlaps and holds
    by_col: dict[int, list] = {}
    for n in notes:
        by_col.setdefault(int(n.lane), []).append(n)
    for col in range(keys):
        if col not in by_col:
            r.errors.append(f"column {col} is empty")
    for col, col_notes in by_col.items():
        for a, b in zip(col_notes, col_notes[1:]):
            if b.beat == a.beat:
                r.errors.append(f"two notes in column {col} at beat {a.beat}")
            elif a.duration and b.beat <= a.end_beat:
                r.errors.append(f"note at beat {b.beat} starts inside the hold in column {col}")

    # hold length
    min_hold = MIN_HOLD.get(tier, Fraction(1, 12))
    short = [n for n in notes if n.kind == NoteKind.HOLD and n.duration < min_hold]
    if short:
        (r.errors if min_hold == Fraction(1, 12) else r.warnings).append(
            f"{len(short)} holds shorter than {min_hold} beat (first at beat {short[0].beat})")

    # chords (presses only)
    chords = Counter(n.beat for n in notes)
    sizes = Counter(chords.values())
    biggest = max(sizes, default=0)
    if tier in ("easy", "normal", "hard", "expert") and biggest > 6:
        r.errors.append(f"chord of {biggest} notes (more than 6 at once)")
    limit = CHORD_LIMIT.get(tier)
    if limit is not None and keys <= 4:
        limit -= 1
    if limit is not None and biggest > limit:
        r.warnings.append(f"chords of up to {biggest} notes; {tier} guideline is {limit}")

    # snapping
    snaps = Counter(_divisor(n.beat) for n in notes)
    odd = sum(c for d, c in snaps.items() if d not in (1, 2, 3, 4, 6, 8, 12, 16))
    if odd:
        r.warnings.append(f"{odd} notes off the 1/16 and 1/12 grids")

    # jacks: consecutive notes in one column closer than 1/2 beat
    jacks = 0
    for col_notes in by_col.values():
        for a, b in zip(col_notes, col_notes[1:]):
            if b.beat - a.end_beat <= Fraction(1, 2) and b.beat != a.beat:
                jacks += 1

    # hand balance: share of notes on the left half (middle column of odd keys counts half)
    left = sum(1.0 if n.lane < keys // 2 else 0.5 if keys % 2 and n.lane == keys // 2 else 0.0 for n in notes)
    r.metrics = {
        "notes": len(notes),
        "holds": sum(1 for n in notes if n.kind == NoteKind.HOLD),
        "chord_sizes": dict(sorted(sizes.items())),
        "snap_divisors": dict(sorted(snaps.items())),
        "jacks_half_beat": jacks,
        "left_hand_share": round(left / len(notes), 3) if notes else None,
        "column_counts": {c: len(v) for c, v in sorted(by_col.items())},
        "length_ms": round(chart.timing.ms_at(chart.end_beat) - chart.timing.ms_at(0)) if notes else 0,
    }
    if notes and abs(r.metrics["left_hand_share"] - 0.5) > 0.15:
        r.warnings.append(f"hand balance {r.metrics['left_hand_share']:.0%} left")
    return r


def _divisor(beat: Fraction) -> int:
    return (beat - int(beat)).denominator if beat != int(beat) else 1
