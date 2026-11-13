"""SOUND VOLTEX sources: catalog and jackets. No chart notes are reachable from the build machine.

- sdvxindex: every song with genres, version, date and, per chart, the level (decimal from 17 up on ∇), effector,
  illustrator, max EX score, max chain and the chip / hold / tsumami chain counts. This is the catalog's backbone. The
  site's own list is read (`sdvxindex.com/songsv<version>.json`, named in the site's script bundle; 1.4.9 in November
  2026), so new songs arrive as the site adds them; the older GitHub copy (Kizuruki/sdvxindex `songsv1.4.2.json`, up
  to 2026-08-20) is the fallback when the site cannot be read.
- Tachi seeds (`songs-sdvx.json`, `charts-sdvx.json`): every song and chart with the game versions it is in. Tachi
  decides what is in the current arcade version (∇, Tachi's `nabla`) and supplies songs and charts newer than sdvxindex.
- Jackets: RRRGR/unnamed-sdvxindex-clone (676 px, a variant per difficulty), else DDRCardDraw (108 px).
- Chart pictures: RRRGR's column renders. Their URL is recorded as `source.picture`; they are not downloaded.

Details, coverage and the slot rules: docs/sources/sdvx.md.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name
from .fetch import Fetcher, NotFound

GH = "https://raw.githubusercontent.com/"
SDVXINDEX_URL = GH + "Kizuruki/sdvxindex/HEAD/songsv1.4.2.json"
TACHI_SONGS_URL = GH + "zkldi/Tachi/HEAD/db/seeds/songs-sdvx.json"
TACHI_CHARTS_URL = GH + "zkldi/Tachi/HEAD/db/seeds/charts-sdvx.json"
RRRGR = GH + "RRRGR/unnamed-sdvxindex-clone/HEAD/"
RRRGR_URL = RRRGR + "songsv1.1.9.json"
DDR_URL = GH + "noahm/DDRCardDraw/HEAD/src/songs/sdvx_nabla.json"
DDR_JACKETS = GH + "noahm/DDRCardDraw/HEAD/src/assets/jackets/"
SDVXINDEX_SITE = "https://sdvxindex.com/"

CURRENT_VERSION = "nabla"          # Tachi's key for the current arcade version, SOUND VOLTEX ∇
# sdvxindex / DDRCardDraw difficulty type -> native key (Tachi's abbreviation, lowercased)
TYPE_KEYS = {"novice": "nov", "advanced": "adv", "exhaust": "exh", "maximum": "mxm", "infinite": "inf",
             "gravity": "grv", "heavenly": "hvn", "vivid": "vvd", "exceed": "xcd", "nabla": "nbl", "ultimate": "ult"}
VERSIONS = {1: "BOOTH", 2: "II -infinite infection-", 3: "III GRAVITY WARS", 4: "IV HEAVENLY HAVEN",
            5: "VIVID WAVE", 6: "EXCEED GEAR", 7: "∇"}
TACHI_VERSIONS = {"booth": 1, "inf": 2, "gw": 3, "heaven": 4, "vivid": 5, "exceed": 6, "nabla": 7}
COUNT_FIELDS = {"chip": "chip_count", "hold": "hold_count", "tsumami": "tsumami_count"}


def _int(v: Any) -> int | None:
    s = str(v).strip()
    return int(s) if s.isdigit() else None


def _level(v: Any) -> str:
    return str(v) if v not in (None, "") else ""


def _non_ascii(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


def _url_path(url: str) -> str:
    """'https://sdvxindex.com//img/x/jk.webp' or '/img/x/jk.webp' -> 'img/x/jk.webp'."""
    return re.sub(r"^(https?://[^/]+)?/+", "", url or "")


def place_chart(rec: SongRecord, cr: ChartRecord) -> str:
    """Put a chart on its slot. A second chart for a taken slot goes to `<slot>-2` (then -3 ...), like CHUNITHM's
    second WORLD'S END chart. No SOUND VOLTEX song needs this today (checked against sdvxindex and Tachi)."""
    slot, n = cr.slot, 2
    while slot in rec.charts:
        slot, n = f"{cr.slot}-{n}", n + 1
    cr.slot = slot
    rec.charts[slot] = cr
    return slot


# ------------------------------------------------------------------------------------------------ sdvxindex

def sdvxindex_record(row: dict[str, Any], rrrgr: dict[str, Any] | None = None) -> SongRecord:
    """One sdvxindex song. `rrrgr` is the same song in RRRGR's copy, which says which pictures and jackets exist."""
    genres = list(row.get("genres") or [])
    version = _int(row.get("version"))
    rec = SongRecord(
        game="sdvx", song_id=str(row["songid"]).zfill(4), title=row.get("title", ""), artist=row.get("artist", ""),
        title_romaji=row.get("title_yomigana_romaji", "") if _non_ascii(row.get("title", "")) else "",
        artist_romaji=row.get("artist_yomigana_romaji", "") if _non_ascii(row.get("artist", "")) else "",
        genre=genres[0] if genres else "", bpm=str(row.get("bpm", "")),
        version=VERSIONS.get(version or 0, str(row.get("version", ""))), date_added=row.get("date", ""),
        source={"sdvxindex": {"id": row["songid"], "ascii": row.get("ascii", "")}},
        extra={"genres": genres, "version_number": version, "ascii": row.get("ascii", ""),
               "title_yomigana": row.get("title_yomigana", ""), "artist_yomigana": row.get("artist_yomigana", ""),
               "charts": {}},
    )
    if row.get("eac_exc"):
        rec.extra["eac_exc"] = True
    mirror_rows = (rrrgr or {}).get("difficulties") or []
    mirror = {c.get("type"): c for c in mirror_rows}
    # the song jacket is the first chart's (NOVICE's); later charts may have their own
    mirror_base = _url_path(mirror_rows[0].get("jacketArtPath", "")) if mirror_rows else ""
    for ch in row.get("difficulties", []):
        key = TYPE_KEYS.get(ch.get("type", ""), ch.get("type", ""))
        slot = canonical_difficulty("sdvx", key)
        source: dict[str, Any] = {}
        if ch.get("columnPath"):
            source["sdvxindex_picture"] = SDVXINDEX_SITE + _url_path(ch["columnPath"])
        m = mirror.get(ch.get("type"))
        if m and m.get("columnPath"):
            source["picture"] = RRRGR + _url_path(m["columnPath"])
        if m and m.get("jacketArtPath") and _url_path(m["jacketArtPath"]) != mirror_base:
            source["jacket"] = {"url": RRRGR + _url_path(m["jacketArtPath"]), "base": RRRGR + mirror_base}
        cr = ChartRecord(slot=slot, native=native_name("sdvx", key), level=_level(ch.get("level")),
                         designer=ch.get("effectorName", ""),
                         note_counts={k: n for k, f in COUNT_FIELDS.items() if (n := _int(ch.get(f))) is not None},
                         source=source)
        slot = place_chart(rec, cr)
        info = {"illustrator": ch.get("illustratorName", ""), "max_exscore": _int(ch.get("max_exscore")),
                "max_chain": _int(ch.get("max_chain")), "radar": ch.get("radar") or {}}
        rec.extra["charts"][slot] = {k: v for k, v in info.items() if v not in ("", None, {})}
    if mirror_base:
        rec.source["jacket"] = [RRRGR + mirror_base]
    return rec


# ------------------------------------------------------------------------------------------------ Tachi

def tachi_by_game_id(songs: Iterable[dict[str, Any]], charts: Iterable[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Tachi's songs keyed by the game's song id (`inGameID`), each with its charts by native key."""
    by_tachi = {s["id"]: s for s in songs}
    out: dict[int, dict[str, Any]] = {}
    for c in charts:
        gid = c.get("data", {}).get("inGameID")
        song = by_tachi.get(c.get("songID"))
        if gid is None or song is None:
            continue
        entry = out.setdefault(int(gid), {"song": song, "charts": {}})
        entry["charts"][c["difficulty"].lower()] = c
    return out


def is_current(entry: dict[str, Any]) -> bool:
    return any(CURRENT_VERSION in c.get("versions", []) for c in entry["charts"].values())


def tachi_record(gid: int, entry: dict[str, Any]) -> SongRecord:
    """A song only Tachi has (newer than sdvxindex, or no longer in the game): title, artist, version, levels."""
    s = entry["song"]
    title = s.get("title", "")
    term = (s.get("searchTerms") or [""])[0]
    version = TACHI_VERSIONS.get(s.get("data", {}).get("displayVersion", ""))
    return SongRecord(
        game="sdvx", song_id=str(gid).zfill(4), title=title, artist=s.get("artist", ""),
        title_romaji=term.split("_")[0] if _non_ascii(title) and term.isascii() else "",
        version=VERSIONS.get(version or 0, s.get("data", {}).get("displayVersion", "")),
        source={"tachi": {"id": s["id"]}}, extra={"version_number": version, "charts": {}},
    )


def tachi_chart(key: str, c: dict[str, Any]) -> ChartRecord:
    return ChartRecord(slot=canonical_difficulty("sdvx", key), native=native_name("sdvx", key),
                       level=str(c.get("level", "")), source={"tachi": {"id": c["id"]}})


# ------------------------------------------------------------------------------------------------ merging

def build_catalog(index_rows: list[dict[str, Any]], tachi_songs: list[dict[str, Any]],
                  tachi_charts: list[dict[str, Any]], rrrgr_rows: Iterable[dict[str, Any]] = (),
                  ddr_songs: Iterable[dict[str, Any]] = ()) -> tuple[list[SongRecord], dict[str, Any]]:
    """sdvxindex songs, plus Tachi's songs and charts it lacks, flagged by whether ∇ has them; jacket URLs attached."""
    rrrgr = {int(r["songid"]): r for r in rrrgr_rows}
    ddr = {int(d["id"]): d for d in ddr_songs}
    tachi = tachi_by_game_id(tachi_songs, tachi_charts)
    songs: dict[int, SongRecord] = {}
    report: dict[str, Any] = {"sdvxindex_songs": len(index_rows), "tachi_songs": len(tachi),
                              "tachi_current_songs": sum(is_current(e) for e in tachi.values()),
                              "tachi_current_charts": sum(CURRENT_VERSION in c.get("versions", [])
                                                          for e in tachi.values() for c in e["charts"].values()),
                              "songs_from_tachi": [], "charts_from_tachi": [], "level_mismatches": [],
                              "charts_not_current": [], "slot_collisions": []}
    for row in index_rows:
        gid = int(row["songid"])
        songs[gid] = sdvxindex_record(row, rrrgr.get(gid))
    older = set()
    for gid, row in rrrgr.items():   # RRRGR's older snapshot still has some songs removed since; same row format
        if gid not in songs and gid in tachi:
            rec = songs[gid] = sdvxindex_record(row, row)
            rec.source["rrrgr"] = rec.source.pop("sdvxindex")
            for cr in rec.charts.values():
                cr.source.pop("sdvxindex_picture", None)
            older.add(gid)
    report["songs_from_rrrgr"] = len(older)
    for gid, entry in sorted(tachi.items()):
        rec = songs.get(gid)
        if rec is None:
            rec = songs[gid] = tachi_record(gid, entry)
            report["songs_from_tachi"].append(f"{rec.song_id} {rec.title}")
        rec.source.setdefault("tachi", {"id": entry["song"]["id"]})
        by_native = {cr.native: slot for slot, cr in rec.charts.items()}
        for key, c in entry["charts"].items():
            cr = tachi_chart(key, c)
            slot = by_native.get(cr.native)
            if slot is None:
                slot = place_chart(rec, cr)
                if slot != canonical_difficulty("sdvx", key):
                    report["slot_collisions"].append(f"{rec.song_id} {key} -> {slot}")
                if rec.source.get("sdvxindex"):
                    report["charts_from_tachi"].append(f"{rec.song_id}/{slot} {cr.native} {cr.level}")
            else:
                mine = rec.charts[slot]
                mine.source.setdefault("tachi", cr.source["tachi"])
                if _as_float(mine.level) != _as_float(cr.level):
                    what = "rrrgr" if gid in older else "sdvxindex"
                    report["level_mismatches"].append(f"{rec.song_id}/{slot} {what} {mine.level} tachi {cr.level}")
                    if gid in older:          # Tachi is newer than RRRGR's snapshot
                        mine.level = cr.level
            versions = c.get("versions", [])
            rec.extra.setdefault("charts", {}).setdefault(slot, {})["versions"] = versions
            if CURRENT_VERSION not in versions and is_current(entry):
                rec.charts[slot].notes = "not in SOUND VOLTEX ∇ (Tachi)"
                report["charts_not_current"].append(f"{rec.song_id}/{slot}")
        rec.extra["tachi_versions"] = sorted({v for c in entry["charts"].values() for v in c.get("versions", [])})
        if not is_current(entry):
            rec.extra["removed"] = True
    for gid, rec in songs.items():
        _attach_ddr(rec, ddr.get(gid))
    out = [songs[k] for k in sorted(songs)]
    report["removed_songs"] = sum(bool(s.extra.get("removed")) for s in out)
    report["songs_with_jacket_url"] = sum(bool(s.source.get("jacket")) for s in out)
    report["charts_with_picture"] = sum("picture" in c.source for s in out for c in s.charts.values())
    report["chart_jacket_variants"] = sum("jacket" in c.source for s in out for c in s.charts.values())
    for k in ("songs_from_tachi", "charts_from_tachi", "level_mismatches", "charts_not_current", "slot_collisions"):
        report[f"{k}_count"] = len(report[k])
        report[k] = report[k][:40]
    return out, report


def _as_float(level: str) -> float | None:
    try:
        return float(level)
    except ValueError:
        return None


def _attach_ddr(rec: SongRecord, d: dict[str, Any] | None) -> None:
    """DDRCardDraw: a 108 px jacket for every song it has (fallback), per-chart variants where RRRGR has none, the
    date for Tachi-only songs and unlock flags (omegaDimension, variantgate, otherEvents, jpOnly)."""
    if not d:
        return
    base = d.get("jacket", "")
    if base:
        rec.source["jacket"] = rec.source.get("jacket", []) + [DDR_JACKETS + base]
    rec.date_added = rec.date_added or d.get("date_added", "")
    if d.get("flags"):
        rec.extra["unlock_flags"] = d["flags"]
    by_native = {cr.native: cr for cr in rec.charts.values()}
    for ch in d.get("charts", []):
        cr = by_native.get(native_name("sdvx", TYPE_KEYS.get(ch.get("diffClass", ""), "")))
        jk = ch.get("jacket", base)
        if cr is not None and "jacket" not in cr.source and jk and jk != base and "picture" not in cr.source:
            cr.source["jacket"] = {"url": DDR_JACKETS + jk, "base": DDR_JACKETS + base}


# ------------------------------------------------------------------------------------------------ registry

_SITE_BUNDLE = re.compile(r'src="/?(static/js/main\.[0-9a-f]+\.js)"')
_SITE_LIST = re.compile(r"songsv(\d+(?:\.\d+)*)\.json")


def site_list_url(fetcher: Fetcher) -> str | None:
    """sdvxindex.com's current song list. The site is a single-page app whose script bundle names the list it loads
    (`JSON.parse('{"i":"songsv1.4.9.json",...}')`); the newest version named there is taken. None if the page has no
    bundle or the bundle names no list."""
    page = fetcher.get(SDVXINDEX_SITE).decode("utf-8", "replace")
    m = _SITE_BUNDLE.search(page)
    if not m:
        return None
    names = _SITE_LIST.findall(fetcher.get(SDVXINDEX_SITE + m[1]).decode("utf-8", "replace"))
    if not names:
        return None
    return f"{SDVXINDEX_SITE}songsv{max(names, key=lambda v: tuple(int(x) for x in v.split('.')))}.json"


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    from .fetch import FetchError

    def load(url: str) -> Any:
        return json.loads(fetcher.get(url))

    index_rows, list_url, note = None, SDVXINDEX_URL, ""
    try:
        url = site_list_url(fetcher)
        if url:
            index_rows, list_url = load(url), url
    except (FetchError, ValueError) as e:
        note = f"sdvxindex.com's own list unavailable ({e}); read the GitHub copy"

    try:
        rrrgr = load(RRRGR_URL)
    except NotFound:
        rrrgr = []
    try:
        ddr = load(DDR_URL).get("songs", [])
    except NotFound:
        ddr = []
    songs, report = build_catalog(index_rows if index_rows is not None else load(SDVXINDEX_URL),
                                  load(TACHI_SONGS_URL), load(TACHI_CHARTS_URL), rrrgr, ddr)
    report["sdvxindex_list"] = list_url
    if note:
        report["note"] = note
    return songs, report


def _get_first(fetcher: Fetcher, urls: Iterable[str]) -> tuple[bytes, str, str] | None:
    for url in urls:
        try:
            return fetcher.get(url), url.rsplit(".", 1)[-1].lower(), url
        except NotFound:
            continue
    return None


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    return _get_first(fetcher, song.source.get("jacket", []))


def difficulty_jackets(fetcher: Fetcher, song: SongRecord, slots: Iterable[str] | None = None,
                       missing: list[str] | None = None) -> list[tuple[str, bytes, str, str]]:
    """Per-difficulty jackets that differ from the song's: (slot, bytes, ext, url). A variant is compared with the
    song jacket from the same source, byte for byte, so a copy of the song jacket is never stored twice. Slots
    whose variant is not found are appended to `missing`."""
    out = []
    wanted = set(song.charts if slots is None else slots)
    for slot, cr in song.charts.items():
        ref = cr.source.get("jacket")
        if not ref or slot not in wanted:
            continue
        got = _get_first(fetcher, [ref["url"]])
        if got is None:
            if missing is not None:
                missing.append(slot)
            continue
        base = _get_first(fetcher, [ref.get("base", "")]) if ref.get("base") else None
        if base is None or base[0] != got[0]:
            out.append((slot, *got))
    return out


def save_difficulty_jackets(db: Any, fetcher: Fetcher, *, workers: int = 8, refresh: bool = False,
                            image_size: int | None = None, only: set[str] | None = None) -> dict[str, Any]:
    """Store per-difficulty jacket variants as chart assets named `jacket-<slot>`. `scrape sdvx --jackets` does the
    same through `GameSource.chart_jackets`; this standalone runner is `python -m vsrgconv.sources.sdvx`."""
    from concurrent.futures import ThreadPoolExecutor

    from .fetch import FetchError

    def todo(s: SongRecord) -> list[str]:
        return [k for k, c in s.charts.items() if "jacket" in c.source and (refresh or f"jacket-{k}" not in c.assets)]

    songs = [s for s in db.iter_songs("sdvx") if (only is None or s.song_id in only) and todo(s)]
    stats: dict[str, Any] = {"songs": len(songs), "saved": 0, "same_as_song": 0, "missing": [], "errors": []}

    def work(song: SongRecord) -> Any:
        missing: list[str] = []
        try:
            return difficulty_jackets(fetcher, song, todo(song), missing), missing
        except FetchError as e:
            return e

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for song, result in zip(songs, pool.map(work, songs)):
            if isinstance(result, Exception):
                stats["errors"].append(f"{song.song_id}: {result}"[:200])
                continue
            result, missing = result
            stats["missing"] += [f"{song.song_id}/{k}" for k in missing]
            stats["same_as_song"] += len(todo(song)) - len(result) - len(missing)
            size = {} if image_size is None else {"max_size": image_size}   # default: db.py's committed size
            for slot, data, ext, url in result:
                try:
                    db.save_asset("sdvx", song.song_id, f"jacket-{slot}", data, ext=ext, url=url, slot=slot, **size)
                    stats["saved"] += 1
                except (OSError, ValueError) as e:
                    stats["errors"].append(f"{song.song_id}/{slot}: {e}"[:200])
    stats["error_count"] = len(stats["errors"])
    stats["errors"] = stats["errors"][:30]
    stats["missing_count"] = len(stats["missing"])
    stats["missing"] = stats["missing"][:60]
    return stats


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="sdvx", catalog=_catalog, chart=None, jacket=_jacket,
                        chart_jackets=lambda f, song, slots: difficulty_jackets(f, song, slots),
                        notes="catalog: sdvxindex + Tachi (current = in ∇); jackets: RRRGR sdvxindex clone, else "
                              "DDRCardDraw; charts: none reachable (RRRGR column pictures recorded as source.picture)"))


_register()


def main(argv: list[str] | None = None) -> int:
    import argparse

    from ..db import ChartDB

    p = argparse.ArgumentParser(prog="python -m vsrgconv.sources.sdvx",
                                description="store SOUND VOLTEX per-difficulty jacket variants as chart assets")
    p.add_argument("--db", default="data/db")
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--interval", type=float, default=0.02)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--only", help="comma-separated song ids")
    a = p.parse_args(argv)
    f = Fetcher(cache_dir=a.cache, offline=a.offline, min_interval=a.interval)
    stats = save_difficulty_jackets(ChartDB(a.db), f, workers=a.workers, refresh=a.refresh,
                                    only=set(a.only.split(",")) if a.only else None)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
