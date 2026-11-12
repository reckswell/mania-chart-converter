"""Arcaea sources. Details, coverage and URL schemes: docs/sources/arcaea.md.

- Catalog: the game's own `songlist` and `packlist` (from the APK, mirrored by SkyEye-FAST/arcaea_wiki_data):
  song_id = songlist `id`, genre = pack name (an append pack files under its parent pack, Memory Archive for
  "single"), every difficulty with level, designer and the per-chart overrides (BEYOND titles, artists, jackets).
- Chart constants and published note counts (the in-game combo): arconline-ext (constants for every chart up to 7.1),
  DipTaken/arcaea-b50-web (a newer copy of arcaea-toolbelt-data: counts up to 7.0), arcaea-toolbelt-data (6.16.1c)
  and Mattyakyou/ArcaeaData (also jacket numbers and song length), in that order of preference.
- Charts: the official .aff files mirrored by HarumiEna/Arcaea-AFF, parsed by `formats/aff.py`. The file list is the
  committed manifest `data/arcaea_aff.json` (built with `python -m vsrgconv.sources.arcaea build-manifest`, which
  needs `gh` for code search; the adapter does not). The combo computed from each file is checked against the
  published count.
- Jackets: Mattyakyou/ArcaeaData `jacket/<jacket_number>.png` (WebP data, 768 px); a BEYOND/ETERNAL chart with its
  own jacket (songlist jacketOverride) gets it as the chart asset `jacket-<slot>` when Mattyakyou lists its number.

A song removed from the game (particlearts) is kept from arcaea-toolbelt-data's chart data, with extra["removed"].
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name
from ..model import Chart, ChartMeta
from .fetch import Fetcher, FetchError, NotFound

WIKI_DATA = "https://raw.githubusercontent.com/SkyEye-FAST/arcaea_wiki_data/HEAD/output/"
SONGLIST_URL = WIKI_DATA + "songlist"
PACKLIST_URL = WIKI_DATA + "packlist"
TOOLBELT = "https://raw.githubusercontent.com/DarrenDanielDay/arcaea-toolbelt-data/HEAD/src/data/"
DIPTAKEN = "https://raw.githubusercontent.com/DipTaken/arcaea-b50-web/HEAD/scripts/data/"
ARCONLINE_CONSTANTS = "https://raw.githubusercontent.com/Insouciant21/arconline-ext/HEAD/src/lib/chart-constants.json"
MATTY = "https://raw.githubusercontent.com/Mattyakyou/ArcaeaData/HEAD/"
MATTY_JACKET = MATTY + "jacket/{n}.png"
AFF_REPO = "HarumiEna/Arcaea-AFF"
AFF_BASE = f"https://raw.githubusercontent.com/{AFF_REPO}/HEAD/"
MANIFEST = Path(__file__).parent / "data" / "arcaea_aff.json"

KEYS = ["pst", "prs", "ftr", "byd", "etr"]                  # songlist ratingClass 0..4
MATTY_KEYS = ["Past", "Present", "Future", "Beyond", "Eternal"]
SINGLE_PACK = "Memory Archive"                               # songlist set "single"
# chart fields kept as ChartRecord fields (the rest of a songlist difficulty goes to source["songlist"])
_CHART_FIELDS = {"ratingClass", "chartDesigner", "rating", "ratingPlus"}
_SONG_FIELDS = {"id", "title_localized", "artist", "bpm", "version", "date", "difficulties", "set"}
_AFF_NAME = re.compile(r"songs/([^/]+)/(?:([^/]+)_)?([0-4])\.aff")


# ------------------------------------------------------------------------------------------------ manifest

def load_manifest(path: Path = MANIFEST) -> dict[str, dict[int, str]]:
    """song id -> {ratingClass: repo path} for the current files (older revisions `*_old_<ver>_<n>.aff` skipped)."""
    if not path.exists():
        return {}
    out: dict[str, dict[int, str]] = {}
    for p in json.loads(path.read_text("utf-8"))["paths"]:
        m = _AFF_NAME.fullmatch(p)
        if m and (m[2] is None or m[2] == m[1]):
            out.setdefault(m[1], {})[int(m[3])] = p
    return out


def aff_url(song_id: str, rating_class: int, manifest: dict[str, dict[int, str]]) -> str | None:
    path = manifest.get(song_id, {}).get(rating_class)
    return AFF_BASE + path if path else None


def build_manifest(out: Path = MANIFEST) -> dict[str, Any]:
    """Enumerate the repo's .aff files with GitHub code search (`gh api`; at most 1000 hits per query, so the search is
    sliced by file size and a slice that hits the cap is split). Writes the manifest; returns its summary."""
    import subprocess
    import time

    def search(lo: int, hi: int) -> tuple[int, list[str]]:
        paths: list[str] = []
        total = 0
        for page in range(1, 11):
            q = f"repo:{AFF_REPO} extension:aff size:{lo}..{hi}"
            for attempt in range(5):
                r = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f", f"q={q}", "-f", "per_page=100",
                                    "-f", f"page={page}"], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                time.sleep(20 * (attempt + 1))         # code search allows about 10 requests a minute
            else:
                raise RuntimeError(f"gh api failed for {q}: {r.stderr[:200]}")
            data = json.loads(r.stdout)
            total = data["total_count"]
            paths += [item["path"] for item in data["items"]]
            time.sleep(6)
            if len(data["items"]) < 100 or len(paths) >= total:
                break
        return total, paths

    slices = [(0, 9999), (10000, 19999), (20000, 29999), (30000, 39999), (40000, 59999), (60000, 99999),
              (100000, 10_000_000)]
    found: set[str] = set()
    while slices:
        lo, hi = slices.pop(0)
        total, paths = search(lo, hi)
        if total >= 1000 and hi > lo:
            mid = (lo + hi) // 2
            slices[:0] = [(lo, mid), (mid + 1, hi)]
            continue
        found.update(paths)
        print(f"size {lo}..{hi}: {total} hits, {len(paths)} listed", file=sys.stderr)
    manifest = {"repo": AFF_REPO, "base": AFF_BASE, "enumerated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "note": "GitHub code search over size slices; current files are songs/<id>/<id>_<n>.aff or "
                        "songs/<id>/<n>.aff (n = ratingClass), older revisions *_old_<ver>_<n>.aff",
                "paths": sorted(found)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=0) + "\n", "utf-8")
    return {"paths": len(found), "current_charts": sum(len(v) for v in load_manifest(out).values())}


# ------------------------------------------------------------------------------------------------ catalog

def norm_title(t: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", t).lower())


def pack_names(packlist: dict[str, Any]) -> dict[str, str]:
    """set id -> genre: the pack's name, or its parent's for an append pack."""
    packs = {p["id"]: p for p in packlist.get("packs", [])}
    out = {"single": SINGLE_PACK}
    for pid, p in packs.items():
        top = p
        seen = {pid}
        while top.get("pack_parent") in packs and top["pack_parent"] not in seen:
            seen.add(top["pack_parent"])
            top = packs[top["pack_parent"]]
        out[pid] = top.get("name_localized", {}).get("en", pid)
    return out


def _date(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _level(d: dict[str, Any]) -> str:
    rating = d.get("rating", 0)
    return f"{rating}{'+' if d.get('ratingPlus') else ''}" if rating else ""


def songlist_record(s: dict[str, Any], genres: dict[str, str], manifest: dict[str, dict[int, str]]) -> SongRecord:
    titles = s.get("title_localized", {})
    rec = SongRecord(
        game="arcaea", song_id=s["id"], title=titles.get("en") or next(iter(titles.values()), s["id"]),
        artist=s.get("artist", ""), genre=genres.get(s.get("set", ""), s.get("set", "")), bpm=str(s.get("bpm", "")),
        version=str(s.get("version", "")), date_added=_date(s.get("date")),
        source={"songlist": {"idx": s.get("idx"), "set": s.get("set", "")}},
        extra={"pack": s.get("set", ""), "pack_name": genres.get(s.get("set", ""), ""),
               "songlist": {k: v for k, v in s.items() if k not in _SONG_FIELDS}},
    )
    if len(titles) > 1:
        rec.extra["title_localized"] = titles
    for d in s.get("difficulties", []):
        rc = d["ratingClass"]
        key = KEYS[rc]
        slot = canonical_difficulty("arcaea", key)
        cr = ChartRecord(slot=slot, native=native_name("arcaea", key), level=_level(d),
                         designer=d.get("chartDesigner", ""),
                         source={"songlist": {k: v for k, v in d.items() if k not in _CHART_FIELDS}})
        url = aff_url(s["id"], rc, manifest)
        if url:
            cr.source["aff"] = url
        if not d.get("rating"):
            cr.notes = "placeholder difficulty (rating 0, hidden in game)"
        rec.charts[slot] = cr
    return rec


def _per_class(rows: Any) -> dict[str, list[Any]]:
    return rows if isinstance(rows, dict) else {}


def _get(fetcher: Fetcher, url: str, notes: list[str]) -> Any:
    try:
        return json.loads(fetcher.get(url))
    except (FetchError, ValueError) as e:
        notes.append(f"{url} unavailable ({e})")
        return None


def attach_counts(songs: list[SongRecord], counts: list[tuple[str, dict[str, list[Any]]]],
                  constants: list[tuple[str, dict[str, list[Any]]]]) -> dict[str, int]:
    """Published combo and chart constant per chart, from the first source that has one (sources in preference
    order). Disagreeing counts are kept in the chart record's source["counts"]."""
    stats = {"with_count": 0, "with_constant": 0, "count_disagreements": 0}
    for song in songs:
        for slot, cr in song.charts.items():
            rc = KEYS.index(next(k for k in KEYS if canonical_difficulty("arcaea", k) == slot))
            found: dict[str, int] = {}
            for name, table in counts:
                row = table.get(song.song_id) or []
                v = row[rc] if rc < len(row) else None
                if isinstance(v, int) and v > 0:
                    found[name] = v
            if found:
                first = next(iter(found))
                cr.note_counts = {"combo": found[first]}
                cr.source["counts_from"] = first
                stats["with_count"] += 1
                if len(set(found.values())) > 1:
                    cr.source["counts"] = found
                    stats["count_disagreements"] += 1
            for name, table in constants:
                row = table.get(song.song_id) or []
                v = row[rc] if rc < len(row) else None
                if isinstance(v, dict):
                    v = v.get("constant")
                if isinstance(v, (int, float)) and v > 0:
                    cr.constant = float(v)
                    cr.source["constant_from"] = name
                    stats["with_constant"] += 1
                    break
    return stats


def matty_tables(rows: list[dict[str, Any]], songs: list[SongRecord]) -> tuple[dict[str, Any], dict[str, list[Any]],
                                                                              dict[str, list[Any]], list[str]]:
    """Match Mattyakyou's rows (no song ids) to songs by any localized title, then artist. Returns per song id: the
    row's meta, its note counts and constants by ratingClass, and the titles that did not match."""
    by_title: dict[str, list[SongRecord]] = {}
    for s in songs:
        titles = {s.title, *s.extra.get("title_localized", {}).values()}
        for cr in s.charts.values():
            titles.update(cr.source.get("songlist", {}).get("title_localized", {}).values())
        for t in titles:
            by_title.setdefault(norm_title(t), []).append(s)
    meta: dict[str, Any] = {}
    counts: dict[str, list[Any]] = {}
    consts: dict[str, list[Any]] = {}
    unmatched: list[str] = []

    def find(title: str, artist: str = "") -> SongRecord | None:
        cands = list({id(c): c for c in by_title.get(norm_title(title), [])}.values())
        if len(cands) > 1 and artist:
            cands = [c for c in cands if norm_title(c.artist) == norm_title(artist)] or cands
        return cands[0] if len(cands) == 1 else None

    for row in rows:
        m = row.get("meta", {})
        song = find(m.get("title", ""), m.get("composer", ""))
        if song is None:
            unmatched.append(m.get("title", ""))
            continue
        meta[song.song_id] = m
        for key, d in row.get("data", {}).items():
            target, rc = song, None
            if key in MATTY_KEYS:
                rc = MATTY_KEYS.index(key)
            elif key.startswith("Beyond"):       # "Beyond0": another song's BEYOND listed here (Last | Eternity)
                other = find(d.get("title", ""))
                if other is None or other is song:
                    continue
                target, rc = other, 3
                meta.setdefault(other.song_id, {**m, "title": d.get("title", ""),
                                                "jacket_number": d.get("jacket_number", "")})
            if rc is None:
                continue
            for table, field in ((counts, "notes"), (consts, "const")):
                row_ = table.setdefault(target.song_id, [None] * 5)
                row_[rc] = d.get(field)
            if d.get("jacket_number"):
                meta.setdefault(target.song_id, m).setdefault("chart_jackets", {})[KEYS[rc]] = d["jacket_number"]
    return meta, counts, consts, unmatched


def removed_records(chart_data: list[dict[str, Any]], current: set[str],
                    manifest: dict[str, dict[int, str]]) -> list[SongRecord]:
    """Songs arcaea-toolbelt-data lists as deleted and the songlist no longer has."""
    out = []
    for row in chart_data or []:
        if row.get("id") in current or not (row.get("version") or {}).get("deleted"):
            continue
        rec = SongRecord(game="arcaea", song_id=row["id"], title=row.get("name", row["id"]),
                         genre=row.get("pack", ""), bpm=str(row.get("bpm", "")),
                         version=str(row["version"].get("added", "")),
                         source={"toolbelt": {"id": row["id"]}},
                         extra={"removed": True, "deleted_in": row["version"]["deleted"]})
        for c in row.get("charts", []):
            key = c.get("difficulty", "")
            if key not in KEYS:
                continue
            slot = canonical_difficulty("arcaea", key)
            cr = ChartRecord(slot=slot, native=native_name("arcaea", key),
                             level=f"{c.get('level', '')}{'+' if c.get('plus') else ''}",
                             designer=c.get("designer", ""),
                             constant=float(c["constant"]) if c.get("constant") else None,
                             note_counts={"combo": c["note"]} if c.get("note") else {},
                             source={"counts_from": "toolbelt", "constant_from": "toolbelt"})
            url = aff_url(row["id"], KEYS.index(key), manifest)
            if url:
                cr.source["aff"] = url
            rec.charts[slot] = cr
        out.append(rec)
    return out


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    notes: list[str] = []
    songlist = json.loads(fetcher.get(SONGLIST_URL))
    packlist = _get(fetcher, PACKLIST_URL, notes) or {}
    genres = pack_names(packlist)
    manifest = load_manifest()
    if not manifest:
        notes.append(f"no .aff manifest at {MANIFEST}; charts cannot be fetched")
    rows = [s for s in songlist.get("songs", []) if "difficulties" in s and not s.get("deleted")]
    songs = [songlist_record(s, genres, manifest) for s in rows]
    chart_data = _get(fetcher, TOOLBELT + "chart-data.json", notes)
    removed = removed_records(chart_data or [], {s.song_id for s in songs}, manifest)
    songs += removed

    matty_rows = _get(fetcher, MATTY + "data.json", notes) or []
    m_meta, m_counts, m_consts, m_unmatched = matty_tables(matty_rows, songs)
    for s in songs:
        m = m_meta.get(s.song_id)
        if not m:
            continue
        s.source["matty"] = {"jacket": m.get("jacket_number", ""), "title": m.get("title", "")}
        if m.get("length"):
            s.extra["length_s"] = m["length"]
        for key, n in (m.get("chart_jackets") or {}).items():
            cr = s.charts.get(canonical_difficulty("arcaea", key))
            if cr is not None and n != m.get("jacket_number"):
                cr.source["jacket"] = MATTY_JACKET.format(n=n)
    dip_counts = (_get(fetcher, DIPTAKEN + "note_count.json", notes) or {}).get("notes", {})
    counts = [("diptaken", _per_class(dip_counts)),
              ("toolbelt", _per_class(_get(fetcher, TOOLBELT + "ChartNotes.json", notes))),
              ("matty", m_counts)]
    constants = [("arconline", _per_class(_get(fetcher, ARCONLINE_CONSTANTS, notes))),
                 ("diptaken", _per_class(_get(fetcher, DIPTAKEN + "cc.json", notes))),
                 ("toolbelt", _per_class(_get(fetcher, TOOLBELT + "ChartConstant.json", notes))),
                 ("matty", m_consts)]
    stats = attach_counts(songs, counts, constants)
    charts = sum(len(s.charts) for s in songs)
    with_aff = sum(1 for s in songs for cr in s.charts.values() if cr.source.get("aff"))
    return songs, {"songlist_songs": len(rows), "removed_songs": len(removed), "charts_with_aff": with_aff,
                   "charts_without_aff": charts - with_aff, **stats,
                   "jacket_numbers": sum(1 for s in songs if s.source.get("matty", {}).get("jacket")),
                   "matty_unmatched": m_unmatched[:20], "notes": notes}


# ------------------------------------------------------------------------------------------------ charts

def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    """The chart's metadata; a BEYOND with its own title/artist/BPM (songlist overrides) uses them."""
    cr = song.charts[slot]
    sl = cr.source.get("songlist", {})
    title = (sl.get("title_localized") or {}).get("en") or song.title
    source = {k: v for k, v in cr.source.items() if k != "songlist"}
    if title != song.title:
        source["song_title"] = song.title
    return ChartMeta(
        game="arcaea", song_id=song.song_id, title=title, artist=sl.get("artist") or song.artist,
        title_romaji=song.title_romaji, artist_romaji=song.artist_romaji, genre=song.genre, difficulty=slot,
        difficulty_native=cr.native, level=cr.level, constant=cr.constant, designer=cr.designer,
        bpm_display=str(sl.get("bpm") or song.bpm), source=source, note_counts=dict(cr.note_counts),
    )


def chart_from_aff(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    from ..formats.aff import aff_to_chart

    cr = song.charts.get(slot)
    url = cr.source.get("aff") if cr else None
    if not url:
        return None
    try:
        text = fetcher.get(url).decode("utf-8-sig", errors="replace")
    except NotFound:
        return None
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "harumiena-aff", "url": url})
    chart = aff_to_chart(text, meta)
    combo = chart.meta.source["aff"]["combo"]["total"]
    published = cr.note_counts.get("combo")
    if published:
        chart.meta.source["combo_check"] = "match" if combo == published else "mismatch"
        if combo != published:
            chart.warnings.append(f"combo {combo} computed from the .aff differs from the published note count "
                                  f"{published}")
    else:
        chart.meta.source["combo_check"] = "unpublished"
    if not chart.notes:
        chart.warnings.append("no notes" + (f" ({cr.notes})" if cr.notes else ""))
    return chart


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    n = song.source.get("matty", {}).get("jacket", "")
    if not n:
        return None
    url = MATTY_JACKET.format(n=n)
    data = fetcher.get(url)
    ext = "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "png"
    return data, ext, url


def _chart_jackets(fetcher: Fetcher, song: SongRecord, slots: list[str]) -> list[tuple[str, bytes, str, str]]:
    """BEYOND / ETERNAL charts with their own jacket (songlist jacketOverride; Mattyakyou lists its number)."""
    out = []
    for slot in slots:
        cr = song.charts.get(slot)
        url = cr.source.get("jacket", "") if cr else ""
        if not url:
            continue
        try:
            data = fetcher.get(url)
        except NotFound:
            continue
        out.append((slot, data, "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "png", url))
    return out


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="arcaea", catalog=_catalog, chart=chart_from_aff, jacket=_jacket,
                        chart_jackets=_chart_jackets,
                        notes="catalog: official songlist/packlist (arcaea_wiki_data) + counts and constants "
                              "(arconline-ext, DipTaken, toolbelt, Mattyakyou); charts: HarumiEna .aff files "
                              "(manifest data/arcaea_aff.json); jackets: Mattyakyou"))


_register()


if __name__ == "__main__":     # python -m vsrgconv.sources.arcaea build-manifest
    if sys.argv[1:] == ["build-manifest"]:
        print(json.dumps(build_manifest(), indent=1))
    else:
        print("usage: python -m vsrgconv.sources.arcaea build-manifest", file=sys.stderr)
        raise SystemExit(2)
