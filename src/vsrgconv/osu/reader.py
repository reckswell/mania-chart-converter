"""Minimal `.osu` reader for mania maps: used to round-trip our own output and to measure real ranked maps
(snap distribution, chord sizes, LN lengths) for the mapping-practice targets in docs/research/osu-mania-mapping.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OsuTimingPoint:
    time: float
    beat_length: float
    meter: int
    uninherited: bool
    effects: int

    @property
    def bpm(self) -> float | None:
        return 60000.0 / self.beat_length if self.uninherited and self.beat_length > 0 else None

    @property
    def sv(self) -> float | None:
        return -100.0 / self.beat_length if not self.uninherited and self.beat_length < 0 else None


@dataclass
class OsuObject:
    column: int
    time: int
    end_time: int | None = None   # holds only


@dataclass
class OsuMap:
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    timing: list[OsuTimingPoint] = field(default_factory=list)
    objects: list[OsuObject] = field(default_factory=list)

    @property
    def keys(self) -> int:
        return int(float(self.sections.get("Difficulty", {}).get("CircleSize", "4")))

    @property
    def mode(self) -> int:
        return int(self.sections.get("General", {}).get("Mode", "0"))


def parse_osu(text: str) -> OsuMap:
    m = OsuMap()
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section in ("General", "Editor", "Metadata", "Difficulty"):
            if ":" in line:
                k, v = line.split(":", 1)
                m.sections.setdefault(section, {})[k.strip()] = v.strip()
        elif section == "TimingPoints":
            p = line.split(",")
            m.timing.append(OsuTimingPoint(
                time=float(p[0]), beat_length=float(p[1]), meter=int(p[2]) if len(p) > 2 else 4,
                uninherited=(p[6] == "1") if len(p) > 6 else True, effects=int(p[7]) if len(p) > 7 else 0))
        elif section == "HitObjects":
            p = line.split(",")
            x, time, typ = int(float(p[0])), int(float(p[2])), int(p[3])
            keys = m.keys
            col = min(keys - 1, max(0, x * keys // 512))
            end = int(p[5].split(":")[0]) if typ & 128 and len(p) > 5 else None
            m.objects.append(OsuObject(col, time, end))
    return m


def read_osu(path: str | Path) -> OsuMap:
    return parse_osu(Path(path).read_text("utf-8-sig"))
