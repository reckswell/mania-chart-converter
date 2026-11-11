"""Chunithm sources.

- `OtogeDbChunithm`: song catalog from otoge-db (GitHub zvuc/otoge-db `chunithm/data/music-ex.json`): genres, levels,
  chart constants, designers and **per-type note counts**. Reachable from anywhere; this is the catalog's backbone.
- `SdvxinChunithm`: sdvx.in's chart pictures (CHUNITHM譜面保管所). Its JSON song lists give sdvx.in's own ids, which
  name the picture layers; the pictures are read by `imaging/` into notes.

`build_catalog` merges the two by normalised title (sdvx.in and SEGA spell some titles differently; see TITLE_FIXES).
Details and the URL scheme: docs/sources/chunithm-sdvxin.md.
"""
from __future__ import annotations

import json
import re
import unicodedata
from fractions import Fraction
from typing import Any, Iterable

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name
from ..model import BpmChange, Chart, ChartMeta, MeterChange, TimingMap
from .base import SourceAdapter
from .fetch import Fetcher, NotFound

OTOGE_DB_URL = "https://raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/data/music-ex.json"
SDVXIN = "https://sdvx.in"
SDVXIN_JSON = SDVXIN + "/chunithm/_/json/"
SDVXIN_EXTRA_LISTS = ["songs_del.json", "songs_we.json", "songs_wedel.json", "songs_wecourse.json", "songs_weuso.json"]
SDVXIN_MAX_FOLDERS = 40
SDVXIN_KEYS = ["bsc", "adv", "exp", "mst", "ult"]
OTOGE_KEYS = ["bas", "adv", "exp", "mas", "ult"]
NOTE_TYPES = ["tap", "hold", "slide", "air", "flick"]


# ------------------------------------------------------------------------------------------------ otoge-db

class OtogeDbChunithm(SourceAdapter):
    key = "chunithm-otogedb"
    game = "chunithm"
    provides = ("catalog",)

    def __init__(self, fetcher: Fetcher, url: str = OTOGE_DB_URL) -> None:
        super().__init__(fetcher)
        self.url = url

    def list_songs(self) -> Iterable[SongRecord]:
        rows = json.loads(self.fetcher.get(self.url))
        for row in rows:
            yield otogedb_record(row)


def otogedb_record(row: dict[str, Any]) -> SongRecord:
    rec = SongRecord(
        game="chunithm", song_id=str(row["id"]), title=row.get("title", ""), artist=row.get("artist", ""),
        genre=row.get("catname", ""), bpm=str(row.get("bpm", "")), version=row.get("version", ""),
        date_added=row.get("date_added", ""), source={"otogedb": {"id": row["id"], "image": row.get("image", "")}},
    )
    for key in OTOGE_KEYS:
        level = row.get(f"lev_{key}", "")
        if not level:
            continue
        counts = {}
        for t in NOTE_TYPES:
            v = row.get(f"lev_{key}_notes_{t}", "")
            if str(v).isdigit():
                counts[t] = int(v)
        const = row.get(f"lev_{key}_i", "")
        rec.charts[canonical_difficulty("chunithm", key)] = ChartRecord(
            slot=canonical_difficulty("chunithm", key), native=native_name("chunithm", key), level=level,
            constant=float(const) if _is_number(const) else None, designer=row.get(f"lev_{key}_designer", ""),
            note_counts=counts,
            source={"sdvxin_legacy": row[f"lev_{key}_chart_link"]} if row.get(f"lev_{key}_chart_link") else {},
        )
    if row.get("we_kanji"):
        rec.genre = "WORLD'S END"
        rec.charts["special"] = ChartRecord(slot="special", native="WORLD'S END",
                                            level=f"{row['we_kanji']}{'☆' * int(row.get('we_star') or 0)}")
    return rec


# ------------------------------------------------------------------------------------------------ sdvx.in

class SdvxinChunithm(SourceAdapter):
    key = "chunithm-sdvxin"
    game = "chunithm"
    provides = ("catalog", "charts")

    def list_files(self) -> list[str]:
        return [f"songs{i:02d}.json" for i in range(1, SDVXIN_MAX_FOLDERS + 1)] + SDVXIN_EXTRA_LISTS

    def raw_lists(self) -> Iterable[tuple[str, list[dict[str, Any]]]]:
        misses = 0
        for name in self.list_files():
            numbered = name[5:7].isdigit()
            if numbered and misses >= 3:      # folders are numbered without gaps; stop after a few misses
                continue
            try:
                data = self.fetcher.get(SDVXIN_JSON + name)
            except NotFound:
                misses += numbered
                continue
            text = data.decode("utf-8-sig", errors="replace").strip()
            if not text.startswith("["):  # missing lists redirect to an HTML page
                continue
            yield name, json.loads(text)

    def list_songs(self) -> Iterable[SongRecord]:
        for name, rows in self.raw_lists():
            for row in rows:
                yield sdvxin_record(row, name)

    def fetch_chart(self, song: SongRecord, slot: str) -> Chart:
        from ..imaging.sdvxin_chart import extract_chart

        ref = song.charts[slot].source.get("sdvxin")
        if not ref:
            raise NotFound(f"{song.title} [{slot}] has no sdvx.in picture")
        layers = {name: self._layer(url_list) for name, url_list in layer_urls(ref).items()}
        return extract_chart(song, slot, layers)

    def _layer(self, urls: list[str]) -> bytes:
        last: Exception | None = None
        for url in urls:
            try:
                return self.fetcher.get(url)
            except NotFound as e:
                last = e
        raise last or NotFound(str(urls))


def layer_urls(ref: dict[str, str]) -> dict[str, list[str]]:
    """URLs of a chart's three layers (each: WebP first, then the older PNG), from {"id": ..., "key": ...}.

    Regular charts: /chunithm/<id[:2]>/bg/<id>bg, /obj/data<id><key>, /bg/<id>bar.
    World's End:    folder "end", key "end".
    """
    sid, key = ref["id"], ref["key"]
    folder = "end" if key == "end" else sid[:2]
    base = f"{SDVXIN}/chunithm/{folder}"
    stems = {"bg": f"{base}/bg/{sid}bg", "obj": f"{base}/obj/data{sid}{key}", "bar": f"{base}/bg/{sid}bar"}
    return {k: [v + ".webp", v + ".png"] for k, v in stems.items()}


def sdvxin_record(row: dict[str, Any], list_name: str = "") -> SongRecord:
    is_we = "_we" in list_name
    rec = SongRecord(
        game="chunithm", song_id=f"sdvx-{'we-' if is_we else ''}{row['id']}", title=row.get("title", ""),
        artist=row.get("artist", ""), title_romaji=row.get("title_search_abc", ""),
        artist_romaji=row.get("artist_search_abc", ""), genre="WORLD'S END" if is_we else row.get("genre", ""),
        bpm=str(row.get("bpm", "")), source={"sdvxin": {"id": row["id"], "list": list_name}},
    )
    levels = row.get("levels", {})
    if isinstance(levels, str):               # World's End rows: "<kanji><stars>", "_" = not drawn
        drawn = not levels.startswith("_")
        lv = levels.lstrip("_")
        rec.charts["special"] = ChartRecord(
            slot="special", native="WORLD'S END", level=lv[:1] + "☆" * int(lv[1:] or 0) if lv[1:].isdigit() else lv,
            status="listed" if drawn else "not-drawn", source={"sdvxin": {"id": row["id"], "key": "end"}})
        return rec
    for key in SDVXIN_KEYS:
        level = str(levels.get(key, ""))
        if not level:
            continue
        drawn = not level.startswith("_")
        counts = {t: int(v) for t, v in (row.get(key) or {}).items() if str(v).isdigit()}
        designer = (row.get("designer") or {}).get(key, "")
        slot = canonical_difficulty("chunithm", key)
        rec.charts[slot] = ChartRecord(
            slot=slot, native=native_name("chunithm", key), level=level.lstrip("_"), designer=designer,
            note_counts=counts, status="listed" if drawn else "not-drawn",
            source={"sdvxin": {"id": row["id"], "key": key},
                    "youtube": [v for k, v in (row.get("youtube") or {}).items() if k.startswith(key[:3]) and v]},
        )
    return rec


# ------------------------------------------------------------------------------------------------ merging

TITLE_FIXES: dict[str, str] = {
    # sdvx.in spelling -> SEGA spelling, where normalisation alone does not match. Extend as matches fail.
}


def norm_title(title: str) -> str:
    t = TITLE_FIXES.get(title, title)
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"[\s　'\"’”“`~～\-‐―:：!！?？・.,()（）\[\]【】〚〛《》♡♥☆★✪]", "", t)


def build_catalog(primary: Iterable[SongRecord], sdvxin: Iterable[SongRecord]) -> tuple[list[SongRecord], list[str]]:
    """Attach sdvx.in refs to otoge-db songs by title. Unmatched sdvx.in songs are kept as their own records."""
    songs: list[SongRecord] = []
    by_id: dict[str, SongRecord] = {}
    for s in primary:
        first = by_id.get(s.song_id)
        if first is None:
            by_id[s.song_id] = s
            songs.append(s)
            continue
        # The same song id twice: a song with several WORLD'S END charts. Keep them as special, special-2, ...
        for slot, cr in s.charts.items():
            n, key = 2, slot
            while key in first.charts and first.charts[key].level != cr.level:
                key, n = f"{slot}-{n}", n + 1
            cr.slot = key
            first.charts[key] = cr
    by_title: dict[str, list[SongRecord]] = {}
    for s in songs:
        by_title.setdefault(norm_title(s.title), []).append(s)
    unmatched: list[str] = []
    out_extra: list[SongRecord] = []
    for sx in sdvxin:
        cands = by_title.get(norm_title(sx.title), [])
        we = "special" in sx.charts and len(sx.charts) == 1
        cands = [c for c in cands if ("special" in c.charts) == we] or cands
        if len(cands) > 1 and sx.artist:
            cands = [c for c in cands if norm_title(c.artist) == norm_title(sx.artist)] or cands
        if not cands:
            unmatched.append(f"{sx.source['sdvxin']['id']} {sx.title}")
            out_extra.append(sx)
            continue
        target = cands[0]
        target.title_romaji = target.title_romaji or sx.title_romaji
        target.artist_romaji = target.artist_romaji or sx.artist_romaji
        target.source.setdefault("sdvxin", sx.source["sdvxin"])
        for slot, cr in sx.charts.items():
            mine = target.charts.setdefault(slot, cr)
            if mine is not cr:
                mine.source.update(cr.source)
                if cr.status == "not-drawn":
                    mine.status = "not-drawn"
                for t, v in cr.note_counts.items():
                    mine.note_counts.setdefault(t, v)
    return songs + out_extra, unmatched


def timing_from_bpm(bpm: str) -> tuple[TimingMap, list[str]]:
    """A single-BPM timing map from a catalog's BPM field. Variable-BPM songs are flagged: their changes are drawn
    as text on the pictures and are not read yet."""
    warnings: list[str] = []
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", bpm or "")]
    if not nums:
        warnings.append("no BPM in catalog; assumed 120")
        nums = [120.0]
    if len(set(nums)) > 1:
        warnings.append(f"BPM {bpm!r} varies; only {nums[-1]:g} used (BPM changes are not read from pictures yet)")
    return TimingMap(bpms=[BpmChange(Fraction(0), nums[-1])], meters=[MeterChange(Fraction(0))]), warnings


def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    return ChartMeta(
        game="chunithm", song_id=song.song_id, title=song.title, artist=song.artist,
        title_romaji=song.title_romaji, artist_romaji=song.artist_romaji, genre=song.genre, difficulty=slot,
        difficulty_native=cr.native, level=cr.level, constant=cr.constant, designer=cr.designer,
        bpm_display=song.bpm, source=dict(cr.source), note_counts=dict(cr.note_counts),
    )


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
