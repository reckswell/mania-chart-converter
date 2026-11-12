"""GITADORA sources: catalog and jackets. The chart files (.dtx) are on a host this machine cannot reach.

- The game's music database as GD-Data ships it (GitHub 9DYnastY/GD-Data `public/mdb/M32_mdb_<n>.bin`, n from
  `m32_manifest.json`; 11 = GALAXY WAVE DELTA): a protobuf list of songs with id, title, ASCII title and artist, BPM,
  first GF/DM version, 15 level slots (×100) and the per-area disable flags. Field map: GD-Data `src/lib/mdb-parser.ts`.
- Jackets and the Japanese artist names: Haven-Lau/jacket_search (`song_metadata.json` + `thumbnails/<name>.webp`,
  128 px), matched by title.
- Tachi seeds (`songs-gitadora.json`, `charts-gitadora-gita.json` / `-dora.json`, up to FUZZ-UP): a level cross-check.
- Charts: GD-Data's `.dtx` files at `gddata-mdb.selundine.top/<id4>/<g|b|d>_<bsc|adv|ext|mst>.dtx`, recorded per chart
  as `source.dtx`.

Details: docs/sources/gitadora.md.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name
from .fetch import Fetcher, NotFound

GH = "https://raw.githubusercontent.com/"
GD_DATA = GH + "9DYnastY/GD-Data/HEAD/public/mdb/"
MANIFEST_URL = GD_DATA + "m32_manifest.json"
JACKETS = GH + "Haven-Lau/jacket_search/HEAD/"
JACKET_META_URL = JACKETS + "song_metadata.json"
JACKET_FULL_URL = JACKETS + "jacket_urls.txt"          # RemyWiki originals (not reachable from the cloud machine)
TACHI = GH + "zkldi/Tachi/HEAD/db/seeds/"
DTX_URL = "https://gddata-mdb.selundine.top/{id:04d}/{part}_{diff}.dtx"

LATEST_VERSION = 11                     # mdb 11 = GALAXY WAVE DELTA (GF version 30 / DM version 29)
PARTS = ["guitar", "drum", "bass"]      # order of the 3 blocks of 5 level slots; slot 0 of each block is unused
DIFFS = [("bsc", "bsc"), ("adv", "adv"), ("ext", "ext"), ("mas", "mst")]   # (registry key, .dtx file key)
DTX_PART = {"guitar": "g", "drum": "d", "bass": "b"}
# GD-Data's version names (src/lib/song-normalizer.ts). GF counts from GuitarFreaks 1st, DM from drummania 1st.
GF_VERSIONS = ["GF1st", "GF2nd", "GF3rd", "GF4th", "GF5th", "GF6th", "GF7th", "GF8th", "GF9th", "GF10th", "GF11th",
               "V", "V2", "V3", "V4", "V5", "V6", "XG", "XG2", "XG3", "GITADORA", "OverDrive", "Tri-Boost",
               "Tri-Boost Re:EVOLVE", "Matixx", "EXCHAIN", "NEX+AGE", "HIGH-VOLTAGE", "FUZZ-UP", "GALAXY WAVE",
               "GALAXY WAVE DELTA"]
DM_VERSIONS = ["DM1st", "DM2nd", "DM3rd", "DM4th", "DM5th", "DM6th", "DM7th", "DM8th", "DM9th", "DM10th",
               *GF_VERSIONS[11:]]
TACHI_DIFFS = {"BASIC": ("guitar", "bsc"), "ADVANCED": ("guitar", "adv"), "EXTREME": ("guitar", "ext"),
               "MASTER": ("guitar", "mas"), "BASS BASIC": ("bass", "bsc"), "BASS ADVANCED": ("bass", "adv"),
               "BASS EXTREME": ("bass", "ext"), "BASS MASTER": ("bass", "mas")}


# ------------------------------------------------------------------------------------------------ protobuf

def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return r, i


def _fields(b: bytes) -> dict[int, list[Any]]:
    """One protobuf message -> {field number: [values]} (varints as int, length-delimited as bytes)."""
    out: dict[int, list[Any]] = {}
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(b, i)
        elif wire == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wire == 1:
            v, i = b[i:i + 8], i + 8
        elif wire == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        out.setdefault(num, []).append(v)
    return out


def _packed(v: bytes | None) -> list[int]:
    out, i = [], 0
    while v and i < len(v):
        x, i = _varint(v, i)
        out.append(x)
    return out


def parse_mdb(data: bytes) -> list[dict[str, Any]]:
    """The mdb's songs (field 1 of the top message), with GD-Data's field numbers."""
    rows = []
    for raw in _fields(data).get(1, []):
        m = _fields(raw)

        def one(n: int) -> Any:
            return m[n][0] if n in m else None

        def text(n: int) -> str:
            return one(n).decode("utf-8", "replace").strip() if isinstance(one(n), bytes) else ""

        def zigzag(n: int) -> int | None:
            v = one(n)
            return None if not isinstance(v, int) else (v >> 1) ^ -(v & 1)

        rows.append({"id": zigzag(1) if zigzag(1) is not None else zigzag(44), "title": text(38),
                     "title_ascii": text(14), "artist_ascii": text(18), "bpm": one(12) or 0, "bpm2": one(13) or 0,
                     "first_ver": _packed(one(8)), "levels": _packed(one(3)), "disable_area": _packed(one(41)),
                     "music_type": one(32), "genre": one(33), "classic": one(45), "remaster": one(37),
                     "long": one(10), "session": one(25)})
    return rows


# ------------------------------------------------------------------------------------------------ records

def version_label(first_ver: list[int]) -> str:
    """'XG', 'GALAXY WAVE DELTA' or, before the two games shared versions, 'GF3rd / DM1st' (GD-Data's labels)."""
    if len(first_ver) < 2 or first_ver[0] >= len(GF_VERSIONS) or first_ver[1] >= len(DM_VERSIONS):
        return "unknown"
    gf, dm = GF_VERSIONS[first_ver[0]], DM_VERSIONS[first_ver[1]]
    return gf if gf == dm else f"{gf} / {dm}"


def is_placeholder(levels: list[int]) -> bool:
    """New songs whose levels are not published carry dummy values (1, 2, 3, 4, 5 or 999 for 0.01 ...)."""
    return any(0 < v < 100 for v in levels)


def mdb_record(row: dict[str, Any]) -> SongRecord | None:
    """One mdb row -> SongRecord, or None for a row with no chart at all (old entries kept only as ids)."""
    levels = row["levels"] + [0] * (15 - len(row["levels"]))
    if not any(levels):
        return None
    title = row["title"] or row["title_ascii"]
    bpm = str(row["bpm"]) if row["bpm"] else ""
    if row["bpm2"] and row["bpm2"] != row["bpm"]:
        bpm = f"{row['bpm']}-{row['bpm2']}"
    label = version_label(row["first_ver"])
    rec = SongRecord(
        game="gitadora", song_id=f"{row['id']:04d}", title=title, artist=row["artist_ascii"],
        title_romaji=row["title_ascii"] if any(ord(c) > 127 for c in title) else "",
        artist_romaji=row["artist_ascii"], genre=label, bpm=bpm, version=label,
        source={"mdb": {"id": row["id"], "version": LATEST_VERSION}},
        extra={"title_ascii": row["title_ascii"], "first_version": {
            "gf": GF_VERSIONS[row["first_ver"][0]] if row["first_ver"] and row["first_ver"][0] < len(GF_VERSIONS)
            else None,
            "dm": DM_VERSIONS[row["first_ver"][1]] if len(row["first_ver"]) > 1 and row["first_ver"][1] < len(
                DM_VERSIONS) else None}, "music_type": row["music_type"], "genre_code": row["genre"]},
    )
    area = row["disable_area"]
    if len(area) >= 3 and all(area[:3]):
        rec.extra["removed"] = True            # GD-Data's rule: disabled in areas 0-2 = deleted from the game
    elif any(area):
        rec.extra["disabled_areas"] = [i for i, v in enumerate(area) if v]
    for flag in ("classic", "remaster", "long", "session"):
        if row.get(flag):
            rec.extra[flag] = True
    placeholder = is_placeholder(levels)
    if placeholder:
        rec.extra["levels_unpublished"] = True
    for p, part in enumerate(PARTS):
        for j, (key, dtx) in enumerate(DIFFS):
            v = levels[p * 5 + 1 + j]
            if not v:
                continue
            native_key = f"{part}-{key}"
            slot = canonical_difficulty("gitadora", native_key)
            rec.charts[slot] = ChartRecord(
                slot=slot, native=native_name("gitadora", native_key), level="" if placeholder else f"{v / 100:.2f}",
                notes="level not published (placeholder in the mdb)" if placeholder else "",
                source={"dtx": DTX_URL.format(id=row["id"], part=DTX_PART[part], diff=dtx)})
    return rec


# ------------------------------------------------------------------------------------------------ jackets

# look-alike letters (Cyrillic, Greek) and the simplified/traditional Chinese forms the two lists disagree on
_CONFUSABLE = str.maketrans({"е": "e", "ё": "e", "а": "a", "о": "o", "р": "p", "с": "c", "я": "r", "и": "n",
                             "φ": "o", "麗": "丽", "郷": "乡", "鄉": "乡", "風": "风"})
# mdb title -> jacket_search title, where the lists spell a title differently
JACKET_ALIASES = {"色ハニホヘト散リヌルヲ": "色ハニホヘト散リネルヲ"}


def _key(text: str) -> str:
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).translate(_CONFUSABLE)
    return "".join(c for c in t if c.isalnum())


def _base_title(text: str) -> str:
    """Title without a trailing edition note: 'Any% (GITADORA Edition)' -> 'Any%', 'X (CLASSIC)' -> 'X' (a CLASSIC
    chart set or a long version shares the song's jacket)."""
    t = re.sub(r"\s*[\(（][^()（）]*(gitadora|ver\.|edition|version|classic)[^()（）]*[\)）]\s*$", "", text or "",
               flags=re.I)
    return re.sub(r"\s*[～~][^～~]*(edition|ver\.|version|mix)[^～~]*[～~]\s*$", "", t, flags=re.I)


def thumbnail_url(name: str) -> str:
    """jacket_search's thumbnail for a jacket file name, encoded as its app does (encodeURIComponent)."""
    from urllib.parse import quote

    return JACKETS + "thumbnails/" + quote(name.rsplit(".", 1)[0], safe="!~*'()") + ".webp"


def remywiki_urls(text: str) -> dict[str, str]:
    """jacket_urls.txt (one RemyWiki image URL per line) -> {file name: URL}: the full-size originals."""
    from urllib.parse import unquote

    return {unquote(u.rsplit("/", 1)[-1]): u for u in text.split() if u.startswith("http")}


def match_jackets(songs: Iterable[SongRecord], meta: dict[str, dict[str, str]],
                  full_size: dict[str, str] | None = None) -> int:
    """Attach jacket_search thumbnails by title (artist breaks ties; then the title without an edition note). The
    matched entry's artist (with Japanese names) becomes the song's artist. `full_size` (RemyWiki URLs by file name)
    is recorded as `source.jacket_full` for a machine that can reach RemyWiki."""
    index: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for name, m in meta.items():
        for k in {_key(m.get("title", "")), _key(_base_title(m.get("title", "")))}:
            index.setdefault(k, []).append((name, m))
    n = 0
    for s in songs:
        title = JACKET_ALIASES.get(s.title, s.title)
        exact = index.get(_key(title))
        cands = exact or index.get(_key(_base_title(title))) or []
        if len(cands) > 1:
            cands = [c for c in cands if _key(c[1].get("artist", "")) == _key(s.artist)] or \
                    [c for c in cands if _key(c[1].get("title", "")) == _key(title)] or \
                    [c for c in cands if _key(c[1].get("title", "")) == _key(_base_title(title))] or cands[:0]
        if len(cands) != 1:
            continue
        name, m = cands[0]
        s.source["jacket"] = thumbnail_url(name)
        if (full_size or {}).get(name):
            s.source["jacket_full"] = full_size[name]
        if m.get("artist") and exact:
            s.artist = m["artist"]            # as the game shows it (kanji, case); the mdb has ASCII only
        n += 1
    return n


# ------------------------------------------------------------------------------------------------ Tachi

def tachi_cross_check(songs: dict[int, SongRecord], tachi_songs: list[dict[str, Any]],
                      charts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Compare levels with Tachi (FUZZ-UP era). Only reported: the mdb is newer and levels get revised."""
    by_tachi = {s["id"]: s for s in tachi_songs}
    mismatches, missing = [], []
    for style, rows in charts.items():
        for c in rows:
            gid = c.get("data", {}).get("inGameID")
            rec = songs.get(int(gid)) if gid is not None else None
            if c.get("songID") not in by_tachi:
                continue
            if rec is None:
                missing.append(str(gid))
                continue
            part, key = ("drum", TACHI_DIFFS.get(c["difficulty"], ("", ""))[1]) if style == "dora" \
                else TACHI_DIFFS.get(c["difficulty"], ("", ""))
            if not key:
                continue
            cr = rec.charts.get(canonical_difficulty("gitadora", f"{part}-{key}"))
            rec.source.setdefault("tachi", {"id": c["songID"]})
            if cr is not None and cr.level and abs(float(cr.level) - float(c.get("levelNum", 0))) > 0.001:
                mismatches.append(f"{rec.song_id}/{cr.slot} mdb {cr.level} tachi {c.get('level')}")
    return {"tachi_level_mismatches_count": len(mismatches), "tachi_level_mismatches": mismatches[:30],
            "tachi_songs_not_in_mdb_charted": len(set(missing)), "tachi_missing_sample": sorted(set(missing))[:20]}


def build_catalog(mdb: bytes, jacket_meta: dict[str, dict[str, str]] | None = None,
                  tachi_songs: list[dict[str, Any]] | None = None,
                  tachi_charts: dict[str, list[dict[str, Any]]] | None = None,
                  jacket_full: dict[str, str] | None = None) -> tuple[list[SongRecord], dict]:
    rows = parse_mdb(mdb)
    songs: dict[int, SongRecord] = {}
    for row in rows:
        rec = mdb_record(row)
        if rec is not None:
            songs[row["id"]] = rec
    out = [songs[k] for k in sorted(songs)]
    report: dict[str, Any] = {"mdb_rows": len(rows), "rows_without_charts": len(rows) - len(out),
                              "songs": len(out), "removed": sum(bool(s.extra.get("removed")) for s in out),
                              "area_disabled": sum(bool(s.extra.get("disabled_areas")) for s in out),
                              "levels_unpublished": sum(bool(s.extra.get("levels_unpublished")) for s in out)}
    report["jackets_matched"] = match_jackets(out, jacket_meta or {}, jacket_full)
    report["jacket_entries"] = len(jacket_meta or {})
    if tachi_songs and tachi_charts:
        report.update(tachi_cross_check(songs, tachi_songs, tachi_charts))
    return out, report


# ------------------------------------------------------------------------------------------------ registry

def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    manifest = json.loads(fetcher.get(MANIFEST_URL))
    latest = int(manifest.get("latest", LATEST_VERSION))
    path = manifest.get("files", {}).get(str(latest), f"/mdb/M32_mdb_{latest}.bin")
    mdb = fetcher.get(GD_DATA + path.rsplit("/", 1)[-1])
    try:
        meta = json.loads(fetcher.get(JACKET_META_URL))
    except NotFound:
        meta = {}
    try:
        full = remywiki_urls(fetcher.get(JACKET_FULL_URL).decode("utf-8", "replace"))
    except NotFound:
        full = {}
    try:
        tachi_songs = json.loads(fetcher.get(TACHI + "songs-gitadora.json"))
        tachi_charts = {k: json.loads(fetcher.get(TACHI + f"charts-gitadora-{k}.json")) for k in ("gita", "dora")}
    except NotFound:
        tachi_songs, tachi_charts = [], {}
    songs, report = build_catalog(mdb, meta, tachi_songs, tachi_charts, full)
    if latest != LATEST_VERSION:
        report["note"] = f"mdb {latest} is newer than {LATEST_VERSION} (GALAXY WAVE DELTA); check the version names"
    for s in songs:
        s.source["mdb"]["version"] = latest
    return songs, {"mdb_version": latest, **report}


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    url = song.source.get("jacket", "")
    if not url:
        return None
    return fetcher.get(url), "webp", url


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="gitadora", catalog=_catalog, chart=None, jacket=_jacket,
                        notes="catalog: GD-Data mdb (GALAXY WAVE DELTA) + Tachi cross-check; jackets: jacket_search "
                              "thumbnails; charts: GD-Data .dtx (unreachable here), URL in source.dtx"))


_register()
