"""Source chart -> rules -> lint -> .osu, in one call. The agent loop and the CLI both go through here."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import Chart
from ..osu.writer import OsuSettings, osu_filename, write_osu
from .lint import LintReport, lint
from .rules import ConvertContext, Pipeline

DEFAULT_KEYS = {"chunithm": 8, "sekai": 6, "bandori": 7, "sdvx": 6, "ddr": 4, "piu": 5, "arcaea": 6, "iidx": 8,
                "guitarhero": 5, "gitadora": 5}   # placeholders until rules decide per game and tier


@dataclass
class Conversion:
    source: Chart
    mania: Chart
    lint: LintReport
    report: list[dict[str, Any]]
    osu: str
    filename: str
    settings: OsuSettings = field(default_factory=OsuSettings)

    def summary(self) -> dict[str, Any]:
        return {
            "chart": f"{self.source.meta.game}/{self.source.meta.song_id}/{self.source.meta.difficulty}",
            "keys": self.mania.lanes,
            "source_notes": len(self.source.notes),
            "mania_notes": len(self.mania.notes),
            "source_warnings": self.source.warnings,
            "rules": self.report,
            "lint": self.lint.to_dict(),
        }

    def save(self, out_dir: str | Path) -> Path:
        out = Path(out_dir) / self.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.osu, "utf-8")
        return out


def convert(chart: Chart, keys: int | None = None, profile: str | list = "baseline",
            settings: OsuSettings | None = None, options: dict[str, Any] | None = None) -> Conversion:
    keys = keys or DEFAULT_KEYS.get(chart.meta.game, 7)
    ctx = ConvertContext(keys=keys, tier=chart.meta.difficulty, options=options or {})
    mania = Pipeline.from_profile(profile).run(chart, ctx)
    # osu! times cannot be negative: move audio offset so the first note is at >= 0 ms
    first = min((mania.timing.ms_at(n.beat) for n in mania.notes), default=0.0)
    if first < 0:
        mania.timing.offset_ms -= first
        ctx.log("pipeline", "offset", shifted_ms=-first)
    settings = settings or OsuSettings()
    report = lint(mania, tier=chart.meta.difficulty)
    return Conversion(source=chart, mania=mania, lint=report, report=ctx.report, osu=write_osu(mania, settings),
                      filename=osu_filename(mania, settings), settings=settings)
