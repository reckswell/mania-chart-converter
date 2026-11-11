"""Hooks into orthomania (Reckswell/osu's ManiaOrtho ruleset and its playtest harness) for checking converted maps.

Three levels, cheapest first (mirrors docs/ortho/PLAYTESTING.md in that repo):

1. **lint** (here, seconds): `convert.lint` on the mania chart. Always run.
2. **harness screenshots / autoplay** (2-4 min per chart): orthomania's `scripts/ortho/playtest.sh --beatmap <.osu>`
   renders real frames of the map in osu!mania and maniaortho at chosen times, and with `--play-to-end` writes final
   statistics to `summary-<ruleset>.json`. `run_playtest` builds that command; it needs a clone of the orthomania
   game repo (env `ORTHO_REPO`, e.g. `C:\\Users\\<you>\\Documents\\Projects\\osu!\\ortho`).
3. **human play** (Alpha): import the `.osz` into lazer (`import_into_lazer`), play it with orthomania, then read
   the play log `<ortho-data>/maniaortho/plays.csv` (`read_play_log`) for judgement counts and accuracy per map.

None of this runs in the cloud build machine; levels 2 and 3 run on the user's PC (Remote Control session).
"""
from __future__ import annotations

import csv
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SCROLL_SPEED = 29   # Alpha's speed per the orthomania docs (405 ms visible)
CHUNITHM_SCROLL_SPEED = 14  # ~840 ms, where Chunithm/Sekai players' notes move (orthomania research)


@dataclass
class PlaytestRequest:
    beatmap: Path
    times_ms: list[int] = field(default_factory=list)
    scroll_speed: int = DEFAULT_SCROLL_SPEED
    play_to_end: bool = True
    humanised_seed: int | None = None
    out: Path | None = None
    extra: list[str] = field(default_factory=list)


def ortho_repo(path: str | Path | None = None) -> Path:
    p = Path(path or os.environ.get("ORTHO_REPO", ""))
    if not p or not (p / "scripts" / "ortho").is_dir():
        raise FileNotFoundError("set ORTHO_REPO to a clone of Reckswell/osu (the orthomania game repo)")
    return p


def playtest_command(req: PlaytestRequest, repo: str | Path | None = None) -> list[str]:
    root = ortho_repo(repo)
    windows = sys.platform == "win32"
    times = ",".join(str(t) for t in req.times_ms)
    if windows:   # playtest.ps1 takes PowerShell-style parameters and has no --humanised
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "ortho" / "playtest.ps1"),
               "-Beatmap", str(req.beatmap), "-ScrollSpeed", str(req.scroll_speed)]
        if times:
            cmd += ["-Times", times]
        if req.play_to_end:
            cmd.append("-PlayToEnd")
        if req.out:
            cmd += ["-Out", str(req.out)]
        return cmd + req.extra
    cmd = ["bash", str(root / "scripts" / "ortho" / "playtest.sh"),
           "--beatmap", str(req.beatmap), "--scroll-speed", str(req.scroll_speed)]
    if times:
        cmd += ["--times", times]
    if req.play_to_end:
        cmd.append("--play-to-end")
    if req.humanised_seed is not None:
        cmd += ["--humanised", str(req.humanised_seed)]
    if req.out:
        cmd += ["--out", str(req.out)]
    return cmd + req.extra


def run_playtest(req: PlaytestRequest, repo: str | Path | None = None, dry_run: bool = False) -> dict[str, Any]:
    cmd = playtest_command(req, repo)
    if dry_run:
        return {"command": shlex.join(cmd)}
    proc = subprocess.run(cmd, cwd=ortho_repo(repo), capture_output=True, text=True)
    return {"command": shlex.join(cmd), "returncode": proc.returncode, "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:]}


def read_summaries(run_dir: str | Path) -> dict[str, Any]:
    """`summary-<ruleset>.json` files from one playtest run folder."""
    out = {}
    for p in Path(run_dir).glob("summary-*.json"):
        out[p.stem.removeprefix("summary-")] = json.loads(p.read_text("utf-8"))
    return out


def import_into_lazer(osz: str | Path) -> None:
    """Open the .osz with osu!(lazer), which imports it. Windows only (lazer registers .osz)."""
    if sys.platform != "win32":
        raise OSError("import_into_lazer needs the Windows PC with lazer installed")
    os.startfile(str(osz))  # type: ignore[attr-defined]


def read_play_log(data_dir: str | Path, title_contains: str = "") -> list[dict[str, str]]:
    """Rows of orthomania's play log (`<data>/maniaortho/plays.csv`), optionally filtered by map name."""
    p = Path(data_dir) / "maniaortho" / "plays.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if title_contains:
        rows = [r for r in rows if any(title_contains.lower() in str(v).lower() for v in r.values())]
    return rows
