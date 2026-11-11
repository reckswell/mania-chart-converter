"""The chart database: game > genre > song > difficulty, on disk.

Layout (all paths under `root`, default `data/db`):

    <game>/index.json                          song_id -> "<genre-slug>/<song-dir>" (fast lookup, rebuildable)
    <game>/<genre-slug>/<song_id>-<slug>/
        song.json            SongRecord: metadata + every chart's metadata
        <slot>.chart.json.gz the chart's notes in the intermediate format (model.py), gzipped JSON (`zcat` to read;
                             a plain .chart.json also loads)
        assets/<name>.<ext>  images: jacket, chart pictures (see AssetRecord)

`<slot>` is the canonical ladder name (normal, hard, expert, master, extra, special ...), so every game's files are named
the same way; the native name ("ADVANCED", "EXHAUST", ...) is inside the record. A song's chart starts as metadata only
(status "listed") and gains a chart file when a reader extracts its notes (status "extracted").

The tree is the source of truth and is meant to be committed. `build_sqlite` makes a throwaway index for queries.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterator

from .games.registry import LADDER, genre_slug, slugify
from .model import Chart


@dataclass
class AssetRecord:
    """An image (or other file) stored in the song's folder under assets/."""

    file: str                       # path relative to the song folder, e.g. "assets/jacket.jpg"
    kind: str = "jacket"            # jacket | chart-picture | background | other
    url: str = ""                   # where it came from
    sha256: str = ""
    width: int | None = None
    height: int | None = None
    bytes: int | None = None


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
    assets: dict[str, AssetRecord] = field(default_factory=dict)   # e.g. sdvx.in chart pictures


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
    assets: dict[str, AssetRecord] = field(default_factory=dict)    # e.g. "jacket"
    extra: dict[str, Any] = field(default_factory=dict)             # game-specific metadata

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
        charts = {}
        for k, v in d.pop("charts", {}).items():
            v = dict(v)
            v["assets"] = {a: AssetRecord(**x) for a, x in v.get("assets", {}).items()}
            charts[k] = ChartRecord(**v)
        assets = {a: AssetRecord(**x) for a, x in d.pop("assets", {}).items()}
        return cls(**d, charts=charts, assets=assets)

    def merge(self, other: "SongRecord") -> "SongRecord":
        """Fields from `other` win when set; charts merge per slot, keeping extraction status already reached."""
        for f in fields(other):
            k, v = f.name, getattr(other, f.name)
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
            for f in fields(rec):
                k, v = f.name, getattr(rec, f.name)
                if v not in ("", None, {}, []):
                    if isinstance(v, dict):
                        getattr(mine, k).update(v)
                    else:
                        setattr(mine, k, v)
            mine.status = keep_status
        return self


def _shrink_image(data: bytes, max_size: int) -> tuple[bytes, int, int]:
    """JPEG at most `max_size` px on the long side. A JPEG that already fits is kept byte for byte."""
    from io import BytesIO

    from PIL import Image  # optional dependency: pip install vsrgconv[images]

    with Image.open(BytesIO(data)) as im:
        if im.format == "JPEG" and max(im.size) <= max_size:
            return data, im.width, im.height
        im = im.convert("RGB")
        im.thumbnail((max_size, max_size))
        out = BytesIO()
        im.save(out, "JPEG", quality=88, optimize=True)
        return out.getvalue(), im.width, im.height


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
        """Where a chart is stored: `<slot>.chart.json.gz` (an older plain `<slot>.chart.json` is used if present)."""
        rel = self._index(game).get(song_id)
        if rel is None:
            raise KeyError(f"{game}/{song_id} is not in the database")
        plain = self.root / game / rel / f"{slot}.chart.json"
        return plain if plain.exists() else plain.with_name(plain.name + ".gz")

    def has_chart(self, game: str, song_id: str, slot: str) -> bool:
        try:
            return self.chart_path(game, song_id, slot).exists()
        except KeyError:
            return False

    def save_chart(self, chart: Chart, status: str = "extracted") -> Path:
        import gzip

        m = chart.meta
        p = self.chart_path(m.game, m.song_id, m.difficulty)
        if p.suffix != ".gz":
            p.unlink()
            p = p.with_name(p.name + ".gz")
        # mtime=0 so the same chart always gives the same bytes (clean git diffs)
        p.write_bytes(gzip.compress(chart.to_json(separators=(",", ":")).encode("utf-8") + b"\n", mtime=0))
        rec = self.get_song(m.game, m.song_id)
        if rec is not None:
            cr = rec.charts.setdefault(m.difficulty, ChartRecord(slot=m.difficulty, native=m.difficulty_native))
            cr.status = status
            cr.notes = "; ".join(chart.warnings)[:500]
            cr.source["extracted"] = {"from": m.source.get("extracted_from", ""), "counts": chart.count_by_kind()}
            (p.parent / "song.json").write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=1) + "\n",
                                                "utf-8")
        return p

    def load_chart(self, game: str, song_id: str, slot: str) -> Chart:
        import gzip

        p = self.chart_path(game, song_id, slot)
        data = p.read_bytes()
        return Chart.from_json((gzip.decompress(data) if p.suffix == ".gz" else data).decode("utf-8"))

    # ---- assets (images) ----
    def save_asset(self, game: str, song_id: str, name: str, data: bytes, *, ext: str = "", url: str = "",
                   kind: str = "jacket", slot: str | None = None, max_size: int | None = 512) -> AssetRecord:
        """Store an image in the song's assets/ folder and record it in song.json (on the chart if `slot`).

        Images are re-encoded to JPEG at most `max_size` px on the long side (None keeps the original bytes), so a
        full catalog of jackets stays small enough to commit. The source URL is kept for the full-size original."""
        import hashlib

        rec = self.get_song(game, song_id)
        if rec is None:
            raise KeyError(f"{game}/{song_id} is not in the database")
        width = height = None
        if max_size is not None:
            data, width, height = _shrink_image(data, max_size)
            ext = "jpg"
        ext = (ext or "bin").lstrip(".")
        rel = f"assets/{name}.{ext}"
        folder = self.song_dir(rec)
        (folder / "assets").mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(data)
        asset = AssetRecord(file=rel, kind=kind, url=url, sha256=hashlib.sha256(data).hexdigest(), width=width,
                            height=height, bytes=len(data))
        if slot is None:
            rec.assets[name] = asset
        else:
            rec.charts[slot].assets[name] = asset
        (folder / "song.json").write_text(json.dumps(rec.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")
        return asset

    def asset_path(self, rec: SongRecord, name: str) -> Path | None:
        a = rec.assets.get(name)
        return self.song_dir(rec) / a.file if a else None

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
