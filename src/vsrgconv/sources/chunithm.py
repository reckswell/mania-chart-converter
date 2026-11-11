"""Chunithm sources.

- `OtogeDbChunithm`: song catalog from otoge-db (GitHub zvuc/otoge-db `chunithm/data/music-ex.json`, plus
  `music-ex-deleted.json` for songs removed from the game): genres, levels, chart constants, designers, **per-type
  note counts** and the jacket file name. Reachable from anywhere; this is the catalog's backbone.
- MarisaBot's copy of the game's own chart files (.c2s, exact; GitHub QingQiz/MarisaBot): the notes, parsed by
  `formats/c2s.py`. Covers every song up to X-VERSE-X; see docs/sources/chunithm-c2s.md.
- `SdvxinChunithm`: sdvx.in's chart pictures (CHUNITHM譜面保管所). Its JSON song lists give sdvx.in's own ids, which
  name the picture layers; the pictures are read by `imaging/` into notes. Fallback for charts with no .c2s.

`build_catalog` merges otoge-db and sdvx.in by normalised title (sdvx.in and SEGA spell some titles differently; see
TITLE_FIXES). Details and the URL scheme: docs/sources/chunithm-sdvxin.md.
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

OTOGE_DB = "https://raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/"
OTOGE_DB_URL = OTOGE_DB + "data/music-ex.json"
OTOGE_DB_DELETED_URL = OTOGE_DB + "data/music-ex-deleted.json"
OTOGE_DB_JACKET = OTOGE_DB + "jacket/"
MARISA_C2S = ("https://raw.githubusercontent.com/QingQiz/MarisaBot/master/Marisa.Frontend/public/assets/chunithm/"
              "chart/{id:04d}_{dd:02d}.c2s")
C2S_DIFFICULTY = {"normal": 0, "hard": 1, "expert": 2, "master": 3, "extra": 4, "special": 5}
# Merged sdvx.in pictures (MASTER, ULTIMA, WORLD'S END only), for charts no .c2s covers yet
PUNIPU2_PICTURE = "https://raw.githubusercontent.com/punipu2/chunithm-chart-downloader/main/resized_charts/{ref}.png"
PUNIPU2_KEYS = {"master": "mst", "extra": "ult", "special": "we"}
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

    def list_removed(self) -> Iterable[SongRecord]:
        """Songs SEGA has removed from the game (otoge-db keeps them in a separate file)."""
        for row in json.loads(self.fetcher.get(OTOGE_DB_DELETED_URL)):
            rec = otogedb_record(row)
            rec.extra["removed"] = True
            yield rec


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


# ------------------------------------------------------------------------------------------------ registry

def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    from .fetch import FetchError

    otoge = OtogeDbChunithm(fetcher)
    primary = list(otoge.list_songs())
    current_ids = {s.song_id for s in primary}
    removed_rows = list(otoge.list_removed())
    removed = [s for s in removed_rows if s.song_id not in current_ids]
    sdvx: list[SongRecord] = []
    note = ""
    try:
        sdvx = list(SdvxinChunithm(fetcher).list_songs())
    except FetchError as e:
        note = f"sdvx.in song lists unavailable ({e}); sdvx.in ids come from otoge-db chart links"
    songs, unmatched = build_catalog(primary + removed, sdvx)
    for song in songs:
        _add_picture_refs(song)
    return songs, {"otogedb_rows": len(primary), "removed_rows": len(removed_rows),
                   "removed_songs": len({s.song_id for s in removed}), "sdvxin_rows": len(sdvx),
                   "sdvxin_unmatched": len(unmatched), "unmatched_sample": unmatched[:20], "note": note}


def _add_picture_refs(song: SongRecord) -> None:
    """Record where each chart's sdvx.in picture is (otoge-db's chart link "09/09001mst" is the sdvx.in id + key)."""
    for slot, cr in song.charts.items():
        link = cr.source.get("sdvxin_legacy", "")
        key = PUNIPU2_KEYS.get(slot)
        if link and key:
            ref = link.rsplit("/", 1)[-1]
            sid = re.sub(r"[a-z]+$", "", ref)
            cr.source["picture"] = PUNIPU2_PICTURE.format(ref=f"{sid}{key}")


def c2s_url(song: SongRecord, slot: str) -> str | None:
    base = slot.split("-")[0]
    if base not in C2S_DIFFICULTY or not song.song_id.isdigit() or slot != base:
        return None
    return MARISA_C2S.format(id=int(song.song_id), dd=C2S_DIFFICULTY[base])


def chart_from_c2s(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    from ..formats.c2s import c2s_to_chart

    url = c2s_url(song, slot)
    if url is None:
        return None
    try:
        text = fetcher.get(url).decode("utf-8-sig", errors="replace")
    except NotFound:
        return None
    if text.startswith("404"):
        return None
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "marisabot-c2s", "url": url})
    chart = c2s_to_chart(text, meta)
    published = sum(song.charts[slot].note_counts.values())
    combo = chart.meta.source.get("c2s_totals", {}).get("T_JUDGE_ALL", "")
    if published and combo.isdigit() and int(combo) != published:
        chart.warnings.append(f"combo {combo} in the .c2s differs from the published note count {published} "
                              f"(chart revised since this file?)")
    return chart


def _chart(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    chart = chart_from_c2s(fetcher, song, slot)
    if chart is not None:
        return chart
    if song.charts.get(slot) and song.charts[slot].source.get("sdvxin"):
        return SdvxinChunithm(fetcher).fetch_chart(song, slot)
    return None


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    image = song.source.get("otogedb", {}).get("image", "")
    if not image:
        return None
    url = OTOGE_DB_JACKET + image
    return fetcher.get(url), image.rsplit(".", 1)[-1], url


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="chunithm", catalog=_catalog, chart=_chart, jacket=_jacket,
                        notes="catalog: otoge-db (+ removed songs, + sdvx.in lists); charts: MarisaBot .c2s, "
                              "else sdvx.in pictures; jackets: otoge-db"))


_register()
