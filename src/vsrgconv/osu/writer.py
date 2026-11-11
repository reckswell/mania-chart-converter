"""Writes a mania-ready `Chart` (integer columns, taps and holds only) as an osu! file format v14 `.osu`.

Input contract (enforced; the conversion rules are responsible for meeting it):
- `chart.lanes` is the key count, every note has an integer `lane` in [0, keys) and width 1;
- every note is NoteKind.TAP or NoteKind.HOLD.

Timing (docs/research/osu-mania-mapping.md section 2):
- a red line at beat 0 and at every BPM or metre change;
- with variable BPM, a green line at each red line normalising scroll to the main BPM (SV = main / current);
- `timing.scrolls` (if a rule left any) are multiplied in as extra green lines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..model import Chart, NoteKind


@dataclass
class OsuSettings:
    creator: str = "vsrgconv"
    version: str = ""               # difficulty name; default "<GAME> <NATIVE> <level>"
    od: float = 8.0
    hp: float = 8.0
    audio_filename: str = "audio.mp3"
    background: str = ""
    preview_ms: int = -1
    sample_set: str = "Soft"
    tags: list[str] = field(default_factory=list)
    source: str = ""
    special_style: bool = False     # N+1
    kiai: list[tuple[Fraction, Fraction]] = field(default_factory=list)  # beat ranges


class ManiaContractError(ValueError):
    pass


def check_contract(chart: Chart) -> None:
    keys = chart.lanes
    for n in chart.notes:
        if n.kind not in (NoteKind.TAP, NoteKind.HOLD):
            raise ManiaContractError(f"note at beat {n.beat} is {n.kind.value}; a rule must turn it into tap/hold")
        if n.lane != int(n.lane) or not 0 <= n.lane < keys:
            raise ManiaContractError(f"note at beat {n.beat} has lane {n.lane}, not a column of {keys}K")


def column_x(column: int, keys: int) -> int:
    return int((column + 0.5) * 512 / keys)


def _ms(chart: Chart, beat: Fraction) -> int:
    return int(round(chart.timing.ms_at(beat)))


def timing_lines(chart: Chart, kiai: list[tuple[Fraction, Fraction]] | None = None) -> list[str]:
    t = chart.timing
    end = chart.end_beat
    main = t.main_bpm(end)
    variable = len({b.bpm for b in t.bpms}) > 1
    meters = sorted(t.meters, key=lambda m: m.beat) or []

    # Events at each beat: (red?, bpm, meter numerator) and scroll factor.
    beats = sorted({b.beat for b in t.bpms} | {m.beat for m in meters} | {s.beat for s in t.scrolls}
                   | {k[0] for k in (kiai or [])} | {k[1] for k in (kiai or [])})
    lines: list[str] = []

    def meter_at(beat: Fraction) -> int:
        num = 4
        for m in meters:
            if m.beat <= beat:
                num = int(m.beats_per_measure) if m.beats_per_measure == int(m.beats_per_measure) else m.numerator
        return num

    def scroll_at(beat: Fraction) -> float:
        s = 1.0
        for sc in sorted(t.scrolls, key=lambda x: x.beat):
            if sc.beat <= beat:
                s = sc.speed
        return s

    def kiai_at(beat: Fraction) -> bool:
        return any(a <= beat < b for a, b in (kiai or []))

    red_beats = {b.beat for b in t.bpms} | {m.beat for m in meters}
    for beat in beats:
        ms = _ms(chart, beat)
        bpm = t.bpm_at(beat)
        effects = 1 if kiai_at(beat) else 0
        if beat in red_beats:
            lines.append(f"{ms},{60000.0 / bpm:.12g},{meter_at(beat)},2,0,70,1,{effects}")
        sv = (main / bpm if variable else 1.0) * scroll_at(beat)
        if beat in red_beats and abs(sv - 1.0) < 1e-9 and not t.scrolls:
            continue
        sv = max(min(sv, 10.0), 0.01)
        lines.append(f"{ms},{-100.0 / sv:.12g},{meter_at(beat)},2,0,70,0,{effects}")
    return lines


def write_osu(chart: Chart, settings: OsuSettings | None = None) -> str:
    s = settings or OsuSettings()
    check_contract(chart)
    m = chart.meta
    keys = chart.lanes
    version = s.version or " ".join(x for x in [m.game.upper(), m.difficulty_native, m.level] if x)
    title_r = m.title_romaji or m.title
    artist_r = m.artist_romaji or m.artist
    tags = s.tags or [m.game, m.genre.replace(" ", "_"), m.designer.replace(" ", "_"), "vsrgconv"]
    out: list[str] = ["osu file format v14", ""]
    out += ["[General]", f"AudioFilename: {s.audio_filename}", "AudioLeadIn: 0", f"PreviewTime: {s.preview_ms}",
            "Countdown: 0", f"SampleSet: {s.sample_set}", "StackLeniency: 0.7", "Mode: 3", "LetterboxInBreaks: 0",
            f"SpecialStyle: {1 if s.special_style else 0}", "WidescreenStoryboard: 0", ""]
    out += ["[Editor]", "DistanceSpacing: 1", "BeatDivisor: 4", "GridSize: 4", "TimelineZoom: 1", ""]
    out += ["[Metadata]", f"Title:{_ascii(title_r)}", f"TitleUnicode:{m.title}", f"Artist:{_ascii(artist_r)}",
            f"ArtistUnicode:{m.artist}", f"Creator:{s.creator}", f"Version:{version}",
            f"Source:{s.source or _game_name(m.game)}", f"Tags:{' '.join(x for x in tags if x)}",
            "BeatmapID:0", "BeatmapSetID:-1", ""]
    out += ["[Difficulty]", f"HPDrainRate:{s.hp:g}", f"CircleSize:{keys}", f"OverallDifficulty:{s.od:g}",
            "ApproachRate:5", "SliderMultiplier:1.4", "SliderTickRate:1", ""]
    out += ["[Events]", "//Background and Video events"]
    if s.background:
        out.append(f'0,0,"{s.background}",0,0')
    out += ["//Break Periods", "//Storyboard Layer 0 (Background)", ""]
    out += ["[TimingPoints]", *timing_lines(chart, s.kiai), "", ""]
    out += ["[HitObjects]"]
    for n in sorted(chart.notes, key=lambda n: (n.beat, n.lane)):
        x = column_x(int(n.lane), keys)
        start = _ms(chart, n.beat)
        if n.kind == NoteKind.HOLD and n.duration > 0:
            out.append(f"{x},192,{start},128,0,{_ms(chart, n.end_beat)}:0:0:0:0:")
        else:
            out.append(f"{x},192,{start},1,0,0:0:0:0:")
    return "\n".join(out) + "\n"


def save_osu(chart: Chart, path: str | Path, settings: OsuSettings | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(write_osu(chart, settings), "utf-8")
    return path


def osu_filename(chart: Chart, settings: OsuSettings | None = None) -> str:
    s = settings or OsuSettings()
    m = chart.meta
    version = s.version or " ".join(x for x in [m.game.upper(), m.difficulty_native, m.level] if x)
    name = f"{_ascii(m.artist_romaji or m.artist)} - {_ascii(m.title_romaji or m.title)} ({s.creator}) [{version}].osu"
    return "".join(c for c in name if c not in '<>:"/\\|?*')


def _game_name(key: str) -> str:
    from ..games.registry import GAMES
    return GAMES[key].name if key in GAMES else key


def _ascii(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().strip() or text
