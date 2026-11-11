"""CHUNITHM's own chart format (.c2s): tab-separated text, exact.

Timing: absolute tick = measure * RESOLUTION + offset (RESOLUTION is 384 = one 4/4 bar, 96 ticks per beat). The
"measure" column is a 384-tick block; MET only changes the drawn bar lines. Cells are 0-15 from the left, width 1-16.

Line types used (columns after the tag; see docs/sources/chunithm-c2s.md):
    BPM m o bpm | MET m o den num | SFL/DCM m o dur mult | STP m o dur | SLP/SLA (per-lane scroll, not represented)
    TAP/MNE m o cell width | CHR m o cell width dir | FLK m o cell width L
    HLD m o cell width dur | HXD m o cell width dur dir
    SLD/SLC m o cell width dur endcell endwidth [style] | SXD/SXC ... style dir   (slide segments, chained)
    AIR/AUR/AUL/ADW/ADR/ADL m o cell width target [colour]
    AHD/AHX m o cell width target dur [colour]   (AHX: newer air-hold variant, same columns)
    ASC/ASD m o cell width target h0 dur endcell endwidth h1 colour   (air slide; colour NON = invisible)
    ALD m o cell width interval h0 dur endcell endwidth h1 colour     (air crush; interval 0 or colour NON: no notes)
Footer `T_*` lines are totals; T_JUDGE_ALL is the combo count. Lines the IR has no field for yet (SLP/SLA: per-lane
scroll; CLK) are kept verbatim in meta.source["c2s_unrepresented"]. Exact duplicate lines (common for SLC) are
dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from ..model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, PathPoint, ScrollChange, TimingMap

AIR_DIRECTIONS = {"AIR": "up", "AUR": "up-right", "AUL": "up-left", "ADW": "down", "ADR": "down-right",
                  "ADL": "down-left"}
SLIDE_TAGS = {"SLD", "SLC", "SXD", "SXC"}


@dataclass
class C2s:
    header: dict[str, list[str]] = field(default_factory=dict)
    totals: dict[str, str] = field(default_factory=dict)
    rows: list[list[str]] = field(default_factory=list)
    resolution: int = 384
    duplicates: int = 0


def parse_c2s(text: str) -> C2s:
    c = C2s()
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip("\r\n")
        if not line.strip():
            continue
        cols = line.split("\t")
        tag = cols[0].strip()
        if tag.startswith("T_"):
            c.totals[tag] = cols[1].strip() if len(cols) > 1 else ""
        elif len(cols) > 2 and cols[1].strip().lstrip("-").isdigit() and tag.isupper() and len(tag) == 3:
            key = "\t".join(x.strip() for x in cols)
            if key in seen:
                c.duplicates += 1
                continue
            seen.add(key)
            c.rows.append([x.strip() for x in cols])
        else:
            c.header[tag] = [x.strip() for x in cols[1:]]
    if "RESOLUTION" in c.header:
        c.resolution = int(c.header["RESOLUTION"][0])
    return c


def c2s_to_chart(text: str, meta: ChartMeta) -> Chart:
    c = parse_c2s(text)
    res = c.resolution
    per_beat = res // 4
    warnings: list[str] = []
    if c.duplicates:
        warnings.append(f"{c.duplicates} exact duplicate lines ignored")

    def tick(row: list[str]) -> int:
        return int(row[1]) * res + int(row[2])

    def beat(t: int) -> Fraction:
        return Fraction(t, per_beat)

    timing = TimingMap(bpms=[], meters=[], scrolls=[])
    notes: list[Note] = []
    slide_segments: list[dict[str, Any]] = []
    air_segments: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    raw_rows: list[str] = []      # lines with no IR field yet (SLP/SLA per-lane scroll), kept verbatim

    for row in c.rows:
        tag, t = row[0], tick(row)
        b = beat(t)
        try:
            if tag == "BPM":
                timing.bpms.append(BpmChange(b, float(row[3])))
            elif tag == "MET":
                den, num = int(row[3]), int(row[4])
                if den > 0 and num > 0:
                    timing.meters.append(MeterChange(b, numerator=num, denominator=den))
                else:     # "MET 117 0 0 0": a zero time signature (24 charts) changes nothing; the old one stays
                    warnings.append(f"MET {num}/{den} at tick {t} ignored")
            elif tag in ("SFL", "DCM"):
                dur = int(row[3])
                timing.scrolls += [ScrollChange(b, float(row[4])), ScrollChange(beat(t + dur), 1.0)]
            elif tag == "STP":
                timing.scrolls += [ScrollChange(b, 0.0), ScrollChange(beat(t + int(row[3])), 1.0)]
            elif tag in ("TAP", "MNE", "CHR", "FLK"):
                kind = {"TAP": NoteKind.TAP, "MNE": NoteKind.MINE, "CHR": NoteKind.EX_TAP,
                        "FLK": NoteKind.FLICK}[tag]
                n = Note(b, int(row[3]), int(row[4]), kind)
                if tag == "CHR" and len(row) > 5:
                    n.extra["effect"] = row[5]
                notes.append(n)
            elif tag in ("HLD", "HXD"):
                n = Note(b, int(row[3]), int(row[4]), NoteKind.HOLD, beat(int(row[5])))
                if tag == "HXD":
                    n.extra["ex_start"] = True
                notes.append(n)
            elif tag in SLIDE_TAGS:
                slide_segments.append({"tag": tag, "start": t, "cell": int(row[3]), "width": int(row[4]),
                                       "dur": int(row[5]), "end_cell": int(row[6]), "end_width": int(row[7]),
                                       "style": row[8] if len(row) > 8 else ""})
            elif tag in AIR_DIRECTIONS:
                notes.append(Note(b, int(row[3]), int(row[4]), NoteKind.AIR,
                                  extra={"direction": AIR_DIRECTIONS[tag], "target": row[5] if len(row) > 5 else ""}))
            elif tag in ("AHD", "AHX"):
                n = Note(b, int(row[3]), int(row[4]), NoteKind.AIR_HOLD, beat(int(row[6])), extra={"target": row[5]})
                if tag == "AHX":
                    n.extra["variant"] = "AHX"
                notes.append(n)
            elif tag in ("ASC", "ASD"):
                air_segments.append({"tag": tag, "start": t, "cell": int(row[3]), "width": int(row[4]),
                                     "target": row[5], "h0": float(row[6]), "dur": int(row[7]),
                                     "end_cell": int(row[8]), "end_width": int(row[9]), "h1": float(row[10]),
                                     "colour": row[11] if len(row) > 11 else ""})
            elif tag == "ALD":
                interval = int(row[5])
                colour = row[11] if len(row) > 11 else ""
                visible = interval > 0 and colour != "NON"
                n = Note(b, int(row[3]), int(row[4]), NoteKind.AIR_HOLD if visible else NoteKind.OTHER,
                         beat(int(row[7])), path=[PathPoint(beat(int(row[7])), int(row[8]), int(row[9]))],
                         extra={"air_crush": interval, "heights": [float(row[6]), float(row[10])],
                                "colour": colour})
                notes.append(n)
            else:
                skipped[tag] = skipped.get(tag, 0) + 1
                raw_rows.append("\t".join(row))
        except (IndexError, ValueError) as e:
            warnings.append(f"bad {tag} line at tick {t}: {e}")
    notes += _chain_slides(slide_segments, beat)
    notes += _chain_air_slides(air_segments, beat)
    if skipped:
        warnings.append("not represented: " + ", ".join(f"{k}×{v}" for k, v in sorted(skipped.items())))
    if not timing.bpms:
        bpm_def = c.header.get("BPM_DEF", ["120"])
        timing.bpms.append(BpmChange(Fraction(0), float(bpm_def[0])))
        warnings.append("no BPM line; used BPM_DEF")
    if not timing.meters:
        timing.meters.append(MeterChange(Fraction(0)))
    chart = Chart(meta=meta, lanes=16, timing=timing, notes=notes, warnings=warnings)
    chart.meta.source["c2s_totals"] = {k: v for k, v in c.totals.items() if k.startswith(("T_JUDGE", "T_REC"))}
    if raw_rows:
        chart.meta.source["c2s_unrepresented"] = raw_rows
    if "CREATOR" in c.header and not chart.meta.designer:
        chart.meta.designer = c.header["CREATOR"][0] if c.header["CREATOR"] else ""
    return chart.sort()


def _chains(segments: list[dict[str, Any]], start_key, end_key, may_continue) -> list[list[dict[str, Any]]]:
    """Group segments into chains: segment j continues segment i when i's end key equals j's start key."""
    by_start: dict[Any, list[int]] = {}
    for i, s in enumerate(segments):
        if may_continue(s):
            by_start.setdefault(start_key(s), []).append(i)
    continued: set[int] = set()
    next_of: dict[int, int] = {}
    for i, s in enumerate(segments):
        for j in by_start.get(end_key(s), []):
            if j != i and j not in continued:
                next_of[i] = j
                continued.add(j)
                break
    chains = []
    for i in range(len(segments)):
        if i in continued:
            continue
        chain, cur, seen = [], i, set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(segments[cur])
            cur = next_of.get(cur)
        chains.append(chain)
    return chains


def _chain_slides(segments: list[dict[str, Any]], beat) -> list[Note]:
    """Ground slides: SLD/SLC (and ExSLIDE SXD/SXC) segments chained by matching end and start (tick, cell, width).

    Path points are offsets from the slide's start; easing is "linear" at a visible node (SLD) and "control" at an
    invisible control point (SLC)."""
    out: list[Note] = []
    for chain in _chains(segments, lambda s: (s["start"], s["cell"], s["width"]),
                         lambda s: (s["start"] + s["dur"], s["end_cell"], s["end_width"]), lambda s: True):
        first, last = chain[0], chain[-1]
        start, end = first["start"], last["start"] + last["dur"]
        path = [PathPoint(beat(s["start"] + s["dur"]) - beat(start), s["end_cell"], s["end_width"],
                          easing="linear" if s["tag"] in ("SLD", "SXD") else "control") for s in chain]
        n = Note(beat(start), first["cell"], first["width"], NoteKind.SLIDE, beat(end) - beat(start), path=path)
        if first["tag"] in ("SXD", "SXC"):
            n.extra["ex_start"] = True
        out.append(n)
    return out


def _chain_air_slides(segments: list[dict[str, Any]], beat) -> list[Note]:
    """AIR SLIDE (ASC/ASD) segments chained like ground slides, also matching height. A segment whose target is
    itself an air slide continues one; a chain drawn only with colour NON is invisible (kind OTHER)."""
    out: list[Note] = []
    for chain in _chains(segments, lambda s: (s["start"], s["cell"], s["width"], s["h0"]),
                         lambda s: (s["start"] + s["dur"], s["end_cell"], s["end_width"], s["h1"]),
                         lambda s: s["target"] in ("ASC", "ASD")):
        first, last = chain[0], chain[-1]
        start, end = first["start"], last["start"] + last["dur"]
        visible = any(s["colour"] != "NON" for s in chain)
        path = [PathPoint(beat(s["start"] + s["dur"]) - beat(start), s["end_cell"], s["end_width"],
                          easing="linear" if s["tag"] == "ASD" else "control") for s in chain]
        out.append(Note(beat(start), first["cell"], first["width"], NoteKind.AIR_HOLD if visible else NoteKind.OTHER,
                        beat(end) - beat(start), path=path,
                        extra={"air_slide": True, "target": first["target"], "colour": first["colour"],
                               "heights": [first["h0"]] + [s["h1"] for s in chain]}))
    return out
