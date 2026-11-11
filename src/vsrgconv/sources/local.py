"""Charts from a folder on disk, for games added with a definition file (docs/adding-a-game.md).

Any game whose charts are in a format with a reader can be added without code. The definition's source says where the
files are and what format they are in:

    "source": {"type": "folder", "folder": "D:/Games/ITGmania/Songs", "format": "stepmania"}

Every folder under `folder` that holds chart files is one song. Its genre is the first folder under `folder` when the
song sits deeper (a StepMania song pack), else the `genre` of a `song.json` in the song folder, else empty.

Formats and what each file is:

| format | files | one file is | title, artist |
|---|---|---|---|
| stepmania | .ssc, else .sm | every chart of the song | from the file |
| c2s | .c2s | one difficulty, named by the file stem (`master.c2s`) | song.json, else the folder name |
| sus | .sus | one difficulty, named by the file stem | #TITLE / #ARTIST, else as above |
| aff | .aff | one difficulty: `2.aff` (Arcaea numbers) or the stem | song.json, else the folder name |
| bestdori | .json (not song.json) | one difficulty, named by the file stem | song.json, else the folder name |
| gh | notes.chart, else notes.mid, with song.ini | every part and difficulty | song.ini, else [Song] |

A file stem or native difficulty becomes a slot through the game's `difficulties`; a stem that is already a ladder
slot (`expert`) is used as is. Charts with no slot get `special`, then `special-2` ... `song.json` may also set
`title`, `artist`, `title_romaji`, `artist_romaji`, `genre`, `bpm` and `levels` ({stem: level}).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import ChartRecord, SongRecord
from ..games.registry import LADDER, game as game_info, slugify
from ..model import Chart, ChartMeta
from . import GameSource
from .guitarhero import DIFF_SLOT, PART_NATIVE, PART_PREFIX, native_for, slot_for
from .fetch import Fetcher

FORMATS = {"stepmania": (".ssc", ".sm"), "c2s": (".c2s",), "sus": (".sus",), "aff": (".aff",),
           "bestdori": (".json",), "gh": (".chart", ".mid")}
#: StepMania difficulty -> ladder slot, when the game's definition does not say
SM_DEFAULT = {"beginner": "easy", "easy": "normal", "medium": "hard", "hard": "expert", "challenge": "master",
              "edit": "special"}
ARCAEA_NUMBERS = {"0": "pst", "1": "prs", "2": "ftr", "3": "byd", "4": "etr"}
IMAGE_NAMES = ("jacket", "cover", "album", "bn", "banner", "bg", "background")


@dataclass
class FolderSource:
    """Builds a `GameSource` that reads one game's charts from a folder."""

    game: str
    folder: Path
    format: str

    def __post_init__(self) -> None:
        self.folder = Path(self.folder).expanduser()
        if self.format not in FORMATS:
            raise ValueError(f"{self.game}: format {self.format!r} has no reader; readable: {', '.join(FORMATS)}")

    def source(self) -> GameSource:
        return GameSource(self.game, catalog=self.catalog, chart=self.chart, jacket=self.jacket,
                          notes=f"{self.format} files under {self.folder}")

    # ---------------------------------------------------------------- catalog

    def song_folders(self) -> list[Path]:
        exts = FORMATS[self.format]
        found = {p.parent for p in self.folder.rglob("*") if p.suffix.lower() in exts and p.name != "song.json"}
        return sorted(found)

    def catalog(self, fetcher: Fetcher | None = None) -> tuple[list[SongRecord], dict[str, Any]]:
        if not self.folder.is_dir():
            raise FileNotFoundError(f"{self.game}: chart folder {self.folder} does not exist")
        songs: list[SongRecord] = []
        used: set[str] = set()
        problems: list[str] = []
        for folder in self.song_folders():
            try:
                rec = self._song(folder)
            except (OSError, ValueError, KeyError) as e:
                problems.append(f"{folder.relative_to(self.folder)}: {e}"[:200])
                continue
            if not rec.charts:
                problems.append(f"{folder.relative_to(self.folder)}: no charts")
                continue
            rec.song_id = _unique(rec.song_id, used)
            songs.append(rec)
        return songs, {"folder": str(self.folder), "format": self.format, "problems": problems}

    def _song(self, folder: Path) -> SongRecord:
        rel = folder.relative_to(self.folder)
        info = _json(folder / "song.json")
        genre = info.get("genre") or (rel.parts[0] if len(rel.parts) > 1 else "")
        rec = SongRecord(game=self.game, song_id="", title=info.get("title") or folder.name,
                         artist=info.get("artist", ""), title_romaji=info.get("title_romaji", ""),
                         artist_romaji=info.get("artist_romaji", ""), genre=genre, bpm=str(info.get("bpm", "")),
                         source={"folder": str(rel)})
        levels = {str(k).lower(): str(v) for k, v in info.get("levels", {}).items()}
        for native, display, level, ref in self._charts_in(folder, rec):
            slot = _unique(self._slot(native), set(rec.charts))
            rec.charts[slot] = ChartRecord(slot, display, level or levels.get(native, ""), source={"file": ref})
        rec.song_id = slugify(rec.title_romaji or rec.title, fallback="") or slugify(folder.name, fallback="")
        if not rec.song_id:
            rec.song_id = "s" + hashlib.md5(str(rel).encode("utf-8")).hexdigest()[:8]
        return rec

    def _charts_in(self, folder: Path, rec: SongRecord):
        """(native difficulty key, display name, level, file#index) for each chart in a song folder."""
        if self.format == "stepmania":
            from ..formats.stepmania import parse_simfile

            path = _first(folder, FORMATS["stepmania"])
            sim = parse_simfile(path.read_text("utf-8", errors="replace"))
            rec.title, rec.artist = sim.title or rec.title, sim.artist or rec.artist
            rec.title_romaji = rec.title_romaji or sim.tags.get("TITLETRANSLIT", "")
            rec.artist_romaji = rec.artist_romaji or sim.tags.get("ARTISTTRANSLIT", "")
            for i, c in enumerate(sim.charts):
                name = (c.difficulty_name or c.difficulty or "edit").lower()
                double = "double" in c.steps_type.lower()
                native = f"double {name}" if double else name
                display = f"{'DOUBLE ' if double else ''}{name.upper()}"
                yield native, display, c.meter, f"{path.name}#{i}"
        elif self.format == "gh":
            from ..formats import gh

            path = _first(folder, FORMATS["gh"])
            ini = gh.parse_ini((folder / "song.ini").read_text("utf-8", errors="replace")) \
                if (folder / "song.ini").exists() else {}
            if path.suffix.lower() == ".chart":         # [Song] Name/Artist when there is no song.ini
                song_rows = dict(gh.parse_chart_sections(path.read_text("utf-8", errors="replace")).get("Song", []))
                ini = {**{k.lower(): v.strip().strip('"') for k, v in song_rows.items()}, **ini}
            rec.title = gh.strip_tags(ini.get("name", "")) or rec.title
            rec.artist = gh.strip_tags(ini.get("artist", "")) or rec.artist
            rec.genre = rec.genre or ini.get("genre", "")
            song = gh.read(path.read_bytes(), path.name, gh.GhOptions.from_ini(ini))
            for part, diffs in song.parts().items():
                for d in diffs:
                    native = d if part in ("guitar", "guitar_ghl") else f"{part} {d}"
                    yield native, native_for(part, d) if part in PART_NATIVE else f"{part} {d}".upper(), "", \
                        f"{path.name}#{part}/{d}"
        else:
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() not in FORMATS[self.format] or path.name == "song.json":
                    continue
                stem = path.stem.lower()
                if self.format == "sus":
                    tags = _sus_tags(path.read_text("utf-8", errors="replace"))
                    rec.title = tags.get("TITLE") or rec.title
                    rec.artist = tags.get("ARTIST") or rec.artist
                native = ARCAEA_NUMBERS.get(stem, stem) if self.format == "aff" else stem
                yield native, native.upper(), "", path.name

    def _slot(self, native: str) -> str:
        diffs = game_info(self.game).difficulties
        if native in diffs:
            return diffs[native]
        if self.format == "stepmania":
            double, _, name = native.rpartition(" ")
            slot = SM_DEFAULT.get(name, "special")
            return f"double-{slot}" if double else slot
        if self.format == "gh":
            part, _, name = native.rpartition(" ")
            if name in DIFF_SLOT and (part or "guitar") in PART_PREFIX:
                return slot_for(part or "guitar", name)
            return f"{part}-special" if part else "special"
        return native if native in LADDER else "special"

    # ---------------------------------------------------------------- charts and jackets

    def chart(self, fetcher: Fetcher | None, song: SongRecord, slot: str) -> Chart | None:
        cr = song.charts.get(slot)
        if cr is None or "file" not in cr.source:
            return None
        folder = self.folder / song.source["folder"]
        name, _, part = cr.source["file"].partition("#")
        path = folder / name
        meta = ChartMeta(game=self.game, song_id=song.song_id, title=song.title, artist=song.artist,
                         title_romaji=song.title_romaji, artist_romaji=song.artist_romaji, genre=song.genre,
                         difficulty=slot, difficulty_native=cr.native, level=cr.level, designer=cr.designer,
                         source={"file": str(path)})
        if self.format == "stepmania":
            from ..formats.stepmania import chart_to_ir, parse_simfile

            sim = parse_simfile(path.read_text("utf-8", errors="replace"))
            return chart_to_ir(sim, sim.charts[int(part)], meta)
        if self.format == "gh":
            from ..formats import gh

            ini_path = folder / "song.ini"
            ini = gh.parse_ini(ini_path.read_text("utf-8", errors="replace")) if ini_path.exists() else {}
            p, _, d = part.partition("/")
            return gh.read(path.read_bytes(), path.name, gh.GhOptions.from_ini(ini)).to_chart(p, d, meta)
        text = path.read_text("utf-8", errors="replace")
        if self.format == "c2s":
            from ..formats.c2s import c2s_to_chart

            return c2s_to_chart(text, meta)
        if self.format == "sus":
            from ..formats.sus import sus_to_chart

            return sus_to_chart(text, meta)
        if self.format == "aff":
            from ..formats.aff import aff_to_chart

            return aff_to_chart(text, meta)
        from ..formats.bestdori import bestdori_to_chart

        return bestdori_to_chart(json.loads(text), meta)

    def jacket(self, fetcher: Fetcher | None, song: SongRecord) -> tuple[bytes, str, str] | None:
        folder = self.folder / song.source["folder"]
        images = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
        for want in IMAGE_NAMES:
            for p in images:
                if want in p.stem.lower():
                    return p.read_bytes(), p.suffix.lower().lstrip(".").replace("jpeg", "jpg"), p.as_uri()
        return None


def _first(folder: Path, exts: tuple[str, ...]) -> Path:
    for ext in exts:
        hits = sorted(p for p in folder.iterdir() if p.suffix.lower() == ext)
        if hits:
            return hits[0]
    raise FileNotFoundError(f"no {'/'.join(exts)} file in {folder}")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def _sus_tags(text: str) -> dict[str, str]:
    tags = {}
    for line in text.splitlines()[:60]:
        if line.startswith("#") and " " in line and not line[1:2].isdigit():
            k, _, v = line[1:].partition(" ")
            tags[k.upper()] = v.strip().strip('"')
    return tags


def _unique(name: str, used: set[str]) -> str:
    out, n = name, 2
    while out in used:
        out, n = f"{name}-{n}", n + 1
    used.add(out)
    return out
