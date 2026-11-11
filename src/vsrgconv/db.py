"""The chart database: game > genre > song > difficulty, on disk.

Layout (all paths under `root`, default `data/db`):

    <game>/index.json                          song_id -> "<genre-slug>/<song-dir>" (fast lookup, rebuildable)
    <game>/<genre-slug>/<song_id>-<slug>/song.json         SongRecord: metadata + every chart's metadata
    <game>/<genre-slug>/<song_id>-<slug>/<slot>.chart.json  the chart's notes in the intermediate format (model.py)

`<slot>` is the canonical ladder name (normal, hard, expert, master, extra, special ...), so every game's files are named
the same way; the native name ("ADVANCED", "EXHAUST", ...) is inside the record. A song's chart starts as metadata only
(status "listed") and gains a chart file when a reader extracts its notes (status "extracted").

The tree is the source of truth and is meant to be committed. `build_sqlite` makes a throwaway index for queries.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .games.registry import LADDER, genre_slug, slugify
from .model import Chart


@dataclass
class ChartRecord:
    slot: str                       # canonical ladder slot
    native: str                     # native difficulty name
    level: str = ""
    constant: float | None = None
    designer: str = ""
    note_counts: dict[str, int] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    status: str = "listed"          # listed | not-drawn | extracted | converted | failed
    notes: str = ""                 # free text: why a status is what it is


@dataclass
class SongRecord:
    game: str
    song_id: str
    title: str
    artist: str = ""
    title_romaji: str = ""
    artist_romaji: str = ""
    genre: str = ""
    bpm: str = ""
    version: str = ""
    date_added: str = ""
    charts: dict[str, ChartRecord] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    @property
    def genre_slug(self) -> str:
        return genre_slug(self.game, self.genre)

    @property
    def dir_name(self) -> str:
        slug = slugify(self.title_romaji or self.title, fallback="")
        return f"{self.song_id}-{slug}" if slug else self.song_id

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["charts"] = {k: asdict(v) for k, v in sorted(self.charts.items(), key=lambda kv: _slot_order(kv[0]))}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SongRecord":
        d = dict(d)
        charts = {k: ChartRecord(**v) for k, v in d.pop("charts", {}).items()}
        return cls(**d, charts=charts)

    def merge(self, other: "SongRecord") -> "SongRecord":
        """Fields from `other` win when set; charts merge per slot, keeping extraction status already reached."""
        for k, v in asdict(other).items():
            if k in ("charts", "game", "song_id"):
                continue
            if v not in ("", None, {}, []):
                if isinstance(v, dict) and isinstance(getattr(self, k), dict):
                    getattr(self, k).update(v)
                else:
                    setattr(self, k, v)
        for slot, rec in other.charts.items():
            mine = self.charts.get(slot)
            if mine is None:
                self.charts[slot] = rec
                continue
            keep_status = mine.status if _status_rank(mine.status) > _status_rank(rec.status) else rec.status
            for k, v in asdict(rec).items():
                if v not in ("", None, {}, []):
                    if isinstance(v, dict):
                        getattr(mine, k).update(v)
                    else:
                        setattr(mine, k, v)
            mine.status = keep_status
        return self


def _slot_order(slot: str) -> int:
    return LADDER.index(slot) if slot in LADDER else len(LADDER)


def _status_rank(status: str) -> int:
    return {"listed": 0, "not-drawn": 0, "failed": 1, "extracted": 2, "converted": 3}.get(status, 0)


class ChartDB:
    def __init__(self, root: str | Path = "data/db") -> None:
        self.root = Path(root)

    # ---- index ----
    def _index_path(self, game: str) -> Path:
        return self.root / game / "index.json"

    def _index(self, game: str) -> dict[str, str]:
        p = self._index_path(game)
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    def _write_index(self, game: str, index: dict[str, str]) -> None:
        p = self._index_path(game)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(sorted(index.items())), ensure_ascii=False, indent=0) + "\n", "utf-8")

    # ---- songs ----
    def song_dir(self, rec: SongRecord) -> Path:
        return self.root / rec.game / rec.genre_slug / rec.dir_name

    def get_song(self, game: str, song_id: str) -> SongRecord | None:
        rel = self._index(game).get(song_id)
        if rel is None:
            return None
        p = self.root / game / rel / "song.json"
        return SongRecord.from_dict(json.loads(p.read_text("utf-8"))) if p.exists() else None

    def upsert_songs(self, records: list[SongRecord]) -> int:
        """Insert or merge many songs; returns how many were new. Moves a song's folder if its genre/title changed."""
        by_game: dict[str, list[SongRecord]] = {}
        for r in records:
            by_game.setdefault(r.game, []).append(r)
        new = 0
        for game, recs in by_game.items():
            index = self._index(game)
            for rec in recs:
                old_rel = index.get(rec.song_id)
                existing = None
                if old_rel is not None:
                    p = self.root / game / old_rel / "song.json"
                    if p.exists():
                        existing = SongRecord.from_dict(json.loads(p.read_text("utf-8")))
                merged = existing.merge(rec) if existing else rec
                new += existing is None
                target = self.song_dir(merged)
                if old_rel is not None and (self.root / game / old_rel) != target and (self.root / game / old_rel).exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    (self.root / game / old_rel).rename(target)
                target.mkdir(parents=True, exist_ok=True)
                (target / "song.json").write_text(
                    json.dumps(merged.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")
                index[rec.song_id] = target.relative_to(self.root / game).as_posix()
            self._write_index(game, index)
        return new

    def upsert_song(self, rec: SongRecord) -> None:
        self.upsert_songs([rec])

    def iter_songs(self, game: str) -> Iterator[SongRecord]:
        for song_id, rel in sorted(self._index(game).items()):
            p = self.root / game / rel / "song.json"
            if p.exists():
                yield SongRecord.from_dict(json.loads(p.read_text("utf-8")))

    # ---- charts ----
    def chart_path(self, game: str, song_id: str, slot: str) -> Path:
        rel = self._index(game).get(song_id)
        if rel is None:
            raise KeyError(f"{game}/{song_id} is not in the database")
        return self.root / game / rel / f"{slot}.chart.json"

    def save_chart(self, chart: Chart, status: str = "extracted") -> Path:
        m = chart.meta
        p = self.chart_path(m.game, m.song_id, m.difficulty)
        p.write_text(chart.to_json(indent=0) + "\n", "utf-8")
        rec = self.get_song(m.game, m.song_id)
        if rec is not None:
            cr = rec.charts.setdefault(m.difficulty, ChartRecord(slot=m.difficulty, native=m.difficulty_native))
            cr.status = status
            cr.notes = "; ".join(chart.warnings)[:500]
            (p.parent / "song.json").write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=1) + "\n",
                                                "utf-8")
        return p

    def load_chart(self, game: str, song_id: str, slot: str) -> Chart:
        return Chart.from_json(self.chart_path(game, song_id, slot).read_text("utf-8"))

    # ---- query index ----
    def build_sqlite(self, path: str | Path) -> Path:
        path = Path(path)
        path.unlink(missing_ok=True)
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE songs(game TEXT, song_id TEXT, title TEXT, artist TEXT, title_romaji TEXT, genre TEXT,
                               bpm TEXT, version TEXT, dir TEXT, PRIMARY KEY(game, song_id));
            CREATE TABLE charts(game TEXT, song_id TEXT, slot TEXT, native TEXT, level TEXT, constant REAL,
                                designer TEXT, status TEXT, notes_total INTEGER, PRIMARY KEY(game, song_id, slot));
        """)
        for game_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for rec in self.iter_songs(game_dir.name):
                con.execute("INSERT INTO songs VALUES (?,?,?,?,?,?,?,?,?)",
                            (rec.game, rec.song_id, rec.title, rec.artist, rec.title_romaji, rec.genre, rec.bpm,
                             rec.version, self.song_dir(rec).relative_to(self.root).as_posix()))
                for slot, c in rec.charts.items():
                    con.execute("INSERT INTO charts VALUES (?,?,?,?,?,?,?,?,?)",
                                (rec.game, rec.song_id, slot, c.native, c.level, c.constant, c.designer, c.status,
                                 sum(c.note_counts.values()) if c.note_counts else None))
        con.commit()
        con.close()
        return path
