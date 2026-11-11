"""PUMP IT UP (PHOENIX 2) sources. Details, URLs and coverage: docs/sources/piu.md.

- Catalog: the Pumpout database (aaaaaa2493/pumpout-db-updates, SQLite with a version history). The songs and charts
  live at PHOENIX 2 v1.02.0 are worked out from each table's INSERT/DELETE/REVIVE history over that version's
  ancestors (`_derived_versionAncestor`). Songs that were live in an earlier mainline version and are gone now are
  catalogued too, with the charts they had then, flagged `extra.removed`.
- Charts: the StepF2/StepP1 "The Resistance" simfiles in rayden-61/PIU-Simfiles, parsed by `formats/stepmania.py`.
  The list of files is the committed manifest `data/piu_simfiles.json`, built by
  `python -m vsrgconv.sources.piu build-manifest` (code search over the thedaryen/PIU-Simfiles copy, the piu-vis-ss
  chart metadata and guessed PHOENIX / PHOENIX 2 folder names; only the code search needs `gh`). The manifest keeps
  each file's headers and per-chart steps type, description, meter and computed combo, so the catalog matches
  offline.
- Jackets: kalinathalie/piu-charts thumbnails (256 px), else Pumpout's song card (`img/`, else `img_big/`).

song_id is the game's song id (`song_id_for`): Pumpout's `gameIdentifier` (e.g. "1617", "F20"), else the number of the
simfile's folder when it has the game's id form, 3-4 hex digits (e.g. "14C6" for songs Pumpout has no id for, "1701"
for PHOENIX songs Pumpout has a placeholder "17XX_1502" for). The other PHOENIX and PHOENIX 2 songs get "po" + Pumpout's
song number (`fallback_id`, e.g. "po1099" for CALL ME BACK): rayden numbers their folders with its own sort keys
("19135", "18044.5"), which it renumbers, so those are kept in `source.simfile.id` only.

Slots are `<style>-<level>` (style single, double, halfdouble or coop; a co-op level is the number of players), with
`-2`, `-3` ... for a second chart of the same style and level. The native name is "S17", "D22", "HDB12" or
"CO-OP x2"; the ladder slot from `registry.slot_for_level` is kept in `ChartRecord.source["ladder"]`.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import ChartRecord, SongRecord
from ..formats.stepmania import (
    Simfile,
    SmChart,
    chart_to_ir,
    parse_simfile,
    pump_counts,
)
from ..games.registry import slot_for_level
from ..model import Chart, ChartMeta
from .fetch import Fetcher, NotFound

PUMPOUT_REPO = "aaaaaa2493/pumpout-db-updates"
PUMPOUT = f"https://raw.githubusercontent.com/{PUMPOUT_REPO}/HEAD/"
PUMPOUT_DB_URL = PUMPOUT + "db/pumpout-phoenix-2-1.02.db"
TARGET_MIX, TARGET_VERSION = "Phoenix 2", "v1.02.0"
RAYDEN_REPO = "rayden-61/PIU-Simfiles"
RAYDEN = f"https://raw.githubusercontent.com/{RAYDEN_REPO}/HEAD/"
DARYEN_REPO = "thedaryen/PIU-Simfiles"
PVS = "https://raw.githubusercontent.com/maxwshen/piu-vis-ss/HEAD/public/chart-jsons/050726/"
KALI = "https://raw.githubusercontent.com/kalinathalie/piu-charts/HEAD/"
KALI_THUMBS_TS = KALI + "app/src/thumbs.ts"
KALI_THUMB = KALI + "app/assets/thumbs/"
MANIFEST = Path(__file__).parent / "data" / "piu_simfiles.json"
#: simfile folders in rayden-61/PIU-Simfiles, oldest first
FOLDERS = ["01 - 1ST~3RD", "02 - S.E.~EXTRA", "03 - REBIRTH~PREX 3", "04 - EXCEED~ZERO", "05 - NX~NX2",
           "06 - NX ABSOLUTE", "07 - PRO~PRO2", "08 - FIESTA", "09 - FIESTA EX", "10 - FIESTA 2", "11 - INFINITY",
           "12 - PRIME", "13 - PRIME 2", "14 - XX", "15 - MOBILE EDITION", "16 - PHOENIX", "17 - PHOENIX 2"]
COPYCAT = "the copycat steps"   # PHOENIX 2 fan transcriptions credit this group

#: Pumpout mode abbreviation -> (slot style, native prefix)
MODES = {"S": ("single", "S"), "D": ("double", "D"), "HDB": ("halfdouble", "HDB"), "C": ("coop", "CO-OP x"),
         # only in removed songs (earlier versions): performance and routine charts
         "SP": ("single-performance", "SP"), "DP": ("double-performance", "DP"), "R": ("routine", "R")}
CUTS = {"Arcade": "ARCADE", "Short Cut": "SHORTCUT", "Full Song": "FULLSONG", "Remix": "REMIX"}


# ------------------------------------------------------------------------------------------------ names

def slot_name(style: str, level: str, n: int = 1) -> str:
    """`<style>-<level>`, `-<n>` for a repeat. A level that is not a number ("??") gives "unknown": slots are file
    names."""
    base = f"{style}-{re.sub(r'[^0-9a-z]+', '', level.lower()) or 'unknown'}"
    return base if n == 1 else f"{base}-{n}"


def native_for(mode: str, level: str) -> str:
    return f"{MODES[mode][1]}{level}"


def fallback_id(pumpout_song_id: int) -> str:
    return f"po{pumpout_song_id}"


def norm(text: str | None) -> str:
    """Match key: case, width and accents folded; punctuation and spaces removed."""
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", "", t)


_CUT_WORDS = re.compile(r"\s*(?:-\s*(?:short\s*cut|full\s*song|remix)\s*-|\[(?:short\s*cut|full\s*song|remix)\])\s*",
                        re.IGNORECASE)


def base_title(title: str) -> str:
    """A title without its cut marker ("Destination - SHORT CUT -", "[Full Song] Gargoyle" -> the plain title)."""
    return _CUT_WORDS.sub(" ", title or "").strip()


def simfile_url(path: str) -> str:
    return RAYDEN + urllib.parse.quote(path, safe="/")


def folder_id(path: str) -> str:
    """The number in a simfile's folder name: "(1) 301 - Final Audition 2/..." -> "301" (up to XX the game's song id,
    used to match Pumpout's gameIdentifier); "17 - PHOENIX 2/19135 - CALL ME BACK/..." -> "19135" (rayden's own sort
    key). Numbers that are not plain hex digits ("18049.6") give ""."""
    parts = path.split("/")
    folder = parts[-2] if len(parts) >= 2 else ""
    m = re.match(r"^(?:\(\w+\)\s*)?([0-9A-Za-z.]+)(?:\s*\(\d+\))?\s+-\s", folder)
    return m.group(1) if m and re.fullmatch(r"[0-9A-F]{3,6}", m.group(1)) else ""


def song_id_for(gid: str, entry: dict[str, Any] | None) -> str:
    """The game's song id: Pumpout's gameIdentifier if real, else the simfile folder's number if it has the game's
    id form (3-4 hex digits: "14C6", "1701"; rayden's 5-digit PHOENIX-era numbers are its own sort keys), else ""."""
    fid = (entry or {}).get("id", "")
    return real_game_id(gid) or (fid if 3 <= len(fid) <= 4 else "")


def real_game_id(gid: str) -> str:
    """Pumpout's gameIdentifier if it is a real id; PHOENIX-era songs have placeholders such as "18XX_0101"."""
    return gid if gid and re.fullmatch(r"[0-9A-Za-z]{2,8}", gid) and "XX" not in gid else ""


def kali_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
                  .lower()).strip("_")


# ------------------------------------------------------------------------------------------------ pumpout

@dataclass
class PoChart:
    chart_id: int
    mode: str               # S, D, HDB, C (also SP, DP, R in old versions)
    level: str              # "17"; "??" when Pumpout has no level
    stepmakers: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    former: list[tuple[str, str]] = field(default_factory=list)   # other (mode, level) ratings it had in any version


@dataclass
class PoSong:
    song_id: int
    title: str
    cut: str
    game_id: str = ""
    titles: dict[str, str] = field(default_factory=dict)    # language code -> title
    artist: str = ""
    artists: list[str] = field(default_factory=list)
    bpm: str = ""
    category: str = ""
    card: str = ""
    first: str = ""         # "Phoenix 2 v1.01.0": the first mainline version the song was in
    first_mix: str = ""
    removed: str = ""       # "XX v1.00.1": the version that removed it, for songs gone at the target version
    revived: str = ""       # the last version that brought it back after a removal
    labels: list[str] = field(default_factory=list)
    charts: list[PoChart] = field(default_factory=list)


def _latest(con: sqlite3.Connection, table: str, keys: str, cols: str, version_id: int,
            where: str = "") -> list[tuple]:
    """For each `keys` value, `cols` of its latest row among the version's ancestors."""
    return con.execute(f"""
        SELECT {keys}, {cols} FROM (
          SELECT t.*, ROW_NUMBER() OVER (PARTITION BY {', '.join('t.' + k.strip() for k in keys.split(','))}
                                          ORDER BY va.ancestorSortOrder DESC) AS rn
          FROM {table} t JOIN _derived_versionAncestor va ON va.ancestorId = t.versionId
          WHERE va.versionId = ? {where}) WHERE rn = 1""", (version_id,)).fetchall()


def _ops(con: sqlite3.Connection, table: str, key: str, version_id: int) -> dict[int, str]:
    """Latest operation (INSERT, DELETE, REVIVE, EXISTS, UPDATE, CROSS) per entity at a version."""
    rows = con.execute(f"""
        SELECT {key}, op FROM (
          SELECT t.{key}, o.internalTitle AS op,
                 ROW_NUMBER() OVER (PARTITION BY t.{key} ORDER BY va.ancestorSortOrder DESC) AS rn
          FROM {table} t JOIN operation o ON o.operationId = t.operationId
          JOIN _derived_versionAncestor va ON va.ancestorId = t.versionId
          WHERE va.versionId = ?) WHERE rn = 1""", (version_id,)).fetchall()
    return {k: op for k, op in rows}


def version_id(con: sqlite3.Connection, mix: str, version: str) -> int:
    row = con.execute("SELECT v.versionId FROM version v JOIN mix m ON m.mixId = v.mixId "
                      "WHERE m.internalTitle = ? AND v.internalTitle = ?", (mix, version)).fetchone()
    if row is None:
        raise KeyError(f"Pumpout has no version {mix} {version}")
    return row[0]


def _version_names(con: sqlite3.Connection) -> dict[int, tuple[str, str, int | None, int]]:
    return {vid: (mix, ver, parent, order) for vid, mix, ver, parent, order in con.execute(
        "SELECT v.versionId, m.internalTitle, v.internalTitle, v.parentVersionId, v.sortOrder "
        "FROM version v JOIN mix m ON m.mixId = v.mixId")}


def _charts_at(con: sqlite3.Connection, vid: int, song_ids: set[int], modes: dict[int, str],
               levels: dict[int, str], stepmakers: dict[int, list[str]], labels: dict[int, str]
               ) -> dict[int, list[PoChart]]:
    ops = _ops(con, "chartVersion", "chartId", vid)
    rating = {cid: (mode, diff) for cid, mode, diff in con.execute("""
        SELECT chartId, modeId, difficultyId FROM (
          SELECT crv.chartId, cr.modeId, cr.difficultyId,
                 ROW_NUMBER() OVER (PARTITION BY crv.chartId ORDER BY va.ancestorSortOrder DESC) AS rn
          FROM chartRatingVersion crv JOIN chartRating cr ON cr.chartRatingId = crv.chartRatingId
          JOIN _derived_versionAncestor va ON va.ancestorId = crv.versionId
          WHERE va.versionId = ?) WHERE rn = 1""", (vid,))}
    every: dict[int, set[tuple[int, int]]] = {}
    for cid, mode, diff in con.execute("SELECT crv.chartId, cr.modeId, cr.difficultyId FROM chartRatingVersion crv "
                                       "JOIN chartRating cr ON cr.chartRatingId = crv.chartRatingId"):
        every.setdefault(cid, set()).add((mode, diff))
    label_ops = _ops(con, "chartLabelVersion", "chartLabelId", vid)
    chart_labels: dict[int, list[str]] = {}
    for clid, cid, lid in con.execute("SELECT chartLabelId, chartId, labelId FROM chartLabel"):
        if label_ops.get(clid, "DELETE") != "DELETE":
            chart_labels.setdefault(cid, []).append(labels[lid])
    out: dict[int, list[PoChart]] = {}
    for cid, sid in con.execute("SELECT chartId, songId FROM chart"):
        if sid not in song_ids or ops.get(cid, "DELETE") == "DELETE" or cid not in rating:
            continue
        mode, diff = rating[cid]
        former = sorted({(modes[m], levels[d]) for m, d in every.get(cid, ()) if (m, d) != (mode, diff)},
                        key=lambda x: (x[0], _level_key(x[1])))
        out.setdefault(sid, []).append(PoChart(cid, modes[mode], levels[diff], stepmakers.get(cid, []),
                                               sorted(chart_labels.get(cid, [])), former))
    return out


def read_pumpout(path: str | Path, mix: str = TARGET_MIX, version: str = TARGET_VERSION,
                 removed: bool = True) -> tuple[list[PoSong], dict[str, Any]]:
    """Songs live at `mix version` (and, if `removed`, songs live in an earlier mainline version and gone now, with
    the charts they had in the last version they were live in)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        return _read_pumpout(con, mix, version, removed)
    finally:
        con.close()


def _read_pumpout(con: sqlite3.Connection, mix: str, version: str, removed: bool
                  ) -> tuple[list[PoSong], dict[str, Any]]:
    vid = version_id(con, mix, version)
    versions = _version_names(con)
    modes = dict(con.execute("SELECT modeId, internalAbbreviation FROM mode"))
    levels = {d: (str(v) if v is not None else title) for d, v, title in
              con.execute("SELECT difficultyId, value, internalTitle FROM difficulty")}
    labels = dict(con.execute("SELECT labelId, internalTitle FROM label"))
    cuts = dict(con.execute("SELECT cutId, internalTitle FROM cut"))
    langs = dict(con.execute("SELECT languageId, code FROM language"))
    names = dict(con.execute("SELECT stepmakerId, internalTitle FROM stepmaker"))
    stepmakers: dict[int, list[str]] = {}
    for cid, smid, _order, prefix in con.execute(
            "SELECT chartId, stepmakerId, sortOrder, prefix FROM chartStepmaker ORDER BY chartId, sortOrder"):
        stepmakers.setdefault(cid, []).append((f"{prefix} " if prefix else "") + names[smid])
    artists: dict[int, list[tuple[str, str]]] = {}
    for sid, name, prefix in con.execute(
            "SELECT sa.songId, a.internalTitle, sa.prefix FROM songArtist sa JOIN artist a ON a.artistId = sa.artistId "
            "ORDER BY sa.songId, sa.sortOrder"):
        artists.setdefault(sid, []).append((name, prefix))

    song_ops = _ops(con, "songVersion", "songId", vid)
    live = {s for s, op in song_ops.items() if op != "DELETE"}
    # the version each song (last) came in, and the one that removed it
    history: dict[int, list[tuple[int, str]]] = {}
    for sid, ver, op in con.execute("""
            SELECT sv.songId, sv.versionId, o.internalTitle FROM songVersion sv
            JOIN operation o ON o.operationId = sv.operationId
            JOIN _derived_versionAncestor va ON va.ancestorId = sv.versionId AND va.versionId = ?
            ORDER BY va.ancestorSortOrder""", (vid,)):
        history.setdefault(sid, []).append((ver, op))

    def vname(v: int) -> str:
        return f"{versions[v][0]} {versions[v][1]}"

    songs: dict[int, PoSong] = {}
    for sid, cut, title in con.execute("SELECT songId, cutId, internalTitle FROM song"):
        if sid not in song_ops or (sid not in live and not removed):
            continue
        hist = history.get(sid, [])
        came = hist[0][0] if hist else None
        rec = PoSong(sid, title, cuts[cut], first=vname(came) if came else "",
                     first_mix=versions[came][0] if came else "")
        revived = [v for v, op in hist if op == "REVIVE"]
        if revived:
            rec.revived = vname(revived[-1])
        if sid not in live:
            gone = next(v for v, op in reversed(hist) if op == "DELETE")
            rec.removed = vname(gone)
        names_ = artists.get(sid, [])
        rec.artists = [n for n, _ in names_]
        rec.artist = "".join((n if i == 0 else (f" {p} {n}" if p else f", {n}")) for i, (n, p) in enumerate(names_))
        songs[sid] = rec

    for sid, title_id in [(r[0], r[2]) for r in _latest(con, "songTitleVersion", "songId, languageId", "songTitleId",
                                                         vid)]:
        if sid in songs:
            lang, title = con.execute("SELECT languageId, title FROM songTitle WHERE songTitleId = ?",
                                      (title_id,)).fetchone()
            songs[sid].titles[langs[lang]] = title
    for sid, bid in _latest(con, "songBpmVersion", "songId", "songBpmId", vid):
        if sid in songs:
            lo, hi = con.execute("SELECT bpmMin, bpmMax FROM songBpm WHERE songBpmId = ?", (bid,)).fetchone()
            songs[sid].bpm = _bpm_text(lo, hi)
    for sid, scid in _latest(con, "songCategoryVersion", "songId", "songCategoryId", vid):
        if sid in songs:
            (cat,) = con.execute("SELECT c.internalTitle FROM songCategory sc JOIN category c "
                                 "ON c.categoryId = sc.categoryId WHERE sc.songCategoryId = ?", (scid,)).fetchone()
            songs[sid].category = cat
    gid_ops = _ops(con, "songGameIdentifierVersion", "songGameIdentifierId", vid)
    for gi, sid, gid in con.execute("SELECT songGameIdentifierId, songId, gameIdentifier FROM songGameIdentifier"):
        if sid in songs and gid_ops.get(gi, "DELETE") != "DELETE":
            songs[sid].game_id = gid
    card_ops = _ops(con, "songCardVersion", "songCardId", vid)
    for cid, sid, path, _order in con.execute("SELECT songCardId, songId, path, sortOrder FROM songCard "
                                               "ORDER BY songId, sortOrder DESC"):
        if sid in songs and card_ops.get(cid, "DELETE") != "DELETE":
            songs[sid].card = path
    label_ops = _ops(con, "songLabelVersion", "songLabelId", vid)
    for slid, sid, lid in con.execute("SELECT songLabelId, songId, labelId FROM songLabel"):
        if sid in songs and label_ops.get(slid, "DELETE") != "DELETE":
            songs[sid].labels.append(labels[lid])

    # charts: at the target version for live songs; for removed songs, at the version before the one that removed them
    groups: dict[int, set[int]] = {vid: live & set(songs)}
    for sid, rec in songs.items():
        if rec.removed:
            gone = next(v for v, op in reversed(history[sid]) if op == "DELETE")
            groups.setdefault(versions[gone][2] or gone, set()).add(sid)
    for at, sids in groups.items():
        for sid, charts in _charts_at(con, at, sids, modes, levels, stepmakers, labels).items():
            songs[sid].charts = sorted(charts, key=lambda c: (list(MODES).index(c.mode) if c.mode in MODES else 9,
                                                              _level_key(c.level), c.chart_id))
    out = [s for s in songs.values() if s.charts or not s.removed]
    live_charts = [c for s in out if not s.removed for c in s.charts]
    report = {"pumpout_version": f"{mix} {version}", "live_songs": sum(1 for s in out if not s.removed),
              "live_charts": len(live_charts),
              "live_charts_by_mode": {m: sum(1 for c in live_charts if c.mode == m) for m in MODES},
              "removed_songs": sum(1 for s in out if s.removed),
              "removed_charts": sum(len(s.charts) for s in out if s.removed),
              "live_charts_of_removed_songs": _orphans(con, vid, live)}
    return sorted(out, key=lambda s: s.song_id), report


def _orphans(con: sqlite3.Connection, vid: int, live: set[int]) -> int:
    """Charts Pumpout still has live although their song was deleted (a data glitch; not catalogued)."""
    ops = _ops(con, "chartVersion", "chartId", vid)
    song_of = dict(con.execute("SELECT chartId, songId FROM chart"))
    return sum(1 for cid, op in ops.items() if op != "DELETE" and song_of.get(cid) not in live)


def _level_key(level: str) -> tuple[int, str]:
    return (int(level), "") if level.isdigit() else (999, level)


def _bpm_text(lo: float, hi: float) -> str:
    def f(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else str(x)
    return f(lo) if lo == hi else f"{f(lo)}-{f(hi)}"


def _pumpout_db(fetcher: Fetcher) -> Path:
    """The Pumpout SQLite file (downloaded once into the cache; sqlite3 needs a file)."""
    fetcher.get(PUMPOUT_DB_URL)
    return fetcher.cache_path(PUMPOUT_DB_URL)


# ------------------------------------------------------------------------------------------------ manifest

MANIFEST_CHART_FIELDS = ["steps_type", "description", "meter", "difficulty", "active", "credit", "chart_name",
                         "combo", "notes"]


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def manifest_entry(path: str, sim: Simfile) -> dict[str, Any]:
    """One manifest row: headers, and per chart (in file order) MANIFEST_CHART_FIELDS. `active` is 1 for a chart
    marked `#CHARTSTYLE:ACTIVE` (the simfiles mark the charts the current arcade version has)."""
    charts: list[list[Any]] = []
    for c in sim.charts:
        ir = chart_to_ir(sim, c, ChartMeta(game="piu", song_id="", title=sim.title))
        counts = pump_counts(sim, c, ir)
        charts.append([c.steps_type, c.description, c.meter, c.difficulty,
                       int(c.tags.get("CHARTSTYLE", "").strip().upper() == "ACTIVE"), c.credit, c.chart_name[:60],
                       counts["combo"], counts["notes"]])
    t = sim.tags
    return {"path": path, "id": folder_id(path), "title": sim.title, "translit": t.get("TITLETRANSLIT", ""),
            "artist": sim.artist, "credit": t.get("CREDIT", ""), "songtype": t.get("SONGTYPE", ""),
            "category": t.get("SONGCATEGORY", "") or t.get("GENRE", ""), "charts": charts}


def code_search_paths(repo: str = DARYEN_REPO, progress: bool = False) -> list[str]:
    """.ssc paths per folder from GitHub code search (`gh api`, about 10 requests a minute). Forks are not indexed,
    so this searches thedaryen/PIU-Simfiles, which has rayden's layout. Files over 384 KB are not indexed either."""
    import subprocess
    import time

    out: list[str] = []
    for folder in FOLDERS:
        page = 1
        while True:
            for attempt in range(6):
                r = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f",
                                    f'q=repo:{repo} extension:ssc path:"{folder}"', "-f", "per_page=100",
                                    "-f", f"page={page}"], capture_output=True, text=True, check=False)
                if r.returncode == 0:
                    break
                time.sleep(20 * (attempt + 1))      # secondary rate limit
            else:
                raise RuntimeError(f"code search failed for {folder}: {r.stderr.strip()[:200]}")
            data = json.loads(r.stdout)
            items = [i["path"] for i in data.get("items", []) if i["path"].split("/")[0] == folder]
            out += items
            if progress:
                print(f"{folder} page {page}: {len(items)} of {data.get('total_count')}", flush=True)
            time.sleep(7)
            if len(data.get("items", [])) < 100 or page * 100 >= data.get("total_count", 0):
                break
            page += 1
    return out


def piu_vis_paths(fetcher: Fetcher, progress: bool = False) -> list[str]:
    """Simfile paths from maxwshen/piu-vis-ss: one chart JSON per song (its metadata `ssc_file` is the path in a
    snapshot of rayden-61/PIU-Simfiles)."""
    from concurrent.futures import ThreadPoolExecutor

    from .fetch import FetchError

    names = json.loads(fetcher.get(PVS + "__search-struct.json"))
    groups: dict[tuple[str, str], str] = {}
    for n in names:
        m = re.match(r"^(.*?)_[A-Za-z]+\d+(?:_[A-Z_0-9]+?)?_(ARCADE|SHORTCUT|FULLSONG|REMIX)$", n)
        groups.setdefault((m.group(1), m.group(2)) if m else (n, ""), n)

    def one(name: str) -> str:
        try:
            meta = json.loads(fetcher.get(PVS + urllib.parse.quote(name) + ".json"))[2]
        except (FetchError, ValueError, IndexError, KeyError, TypeError):
            return ""
        return re.sub(r"^.*?/PIU-Simfiles-rayden-61-\d+/", "", meta.get("ssc_file", ""))

    with ThreadPoolExecutor(8) as pool:
        paths = {p for p in pool.map(one, groups.values()) if p.endswith(".ssc")}
    if progress:
        print(f"piu-vis-ss: {len(names)} charts, {len(groups)} songs, {len(paths)} simfile paths", flush=True)
    return sorted(paths)


def _safe_name(title: str) -> list[str]:
    """Folder/file names a title may have been saved under (Windows forbids some characters)."""
    out = [title]
    for drop in (r'[\\/:*?"<>|]', r'[\\/:*?"<>|!]'):
        out.append(re.sub(r"\s+", " ", re.sub(drop, "", title)).strip())
    out.append(title.replace("\u2019", "'").replace("\u2018", "'"))
    out.append(re.sub(r'[\\/:*?"<>|]', "_", title))
    return list(dict.fromkeys(x for x in out if x))


def guess_candidates(songs: list[PoSong], entries: list[dict[str, Any]]) -> list[str]:
    """Paths to try for PHOENIX and PHOENIX 2 songs that no listed simfile covers (same title and cut).

    rayden's folders number songs `<id> - <Title>/<Title>.ssc`, arcade ids in steps of 5 (18000... in PHOENIX, 19000...
    in PHOENIX 2), short cuts `[Short Cut] <Title>` with ids 18F00-18F99 / 19F00-19F99."""
    listed = {(norm(base_title(t)), file_cut(e)) for e in entries
              for t in (e.get("title", ""), e["path"].rsplit("/", 2)[-2].split(" - ", 1)[-1]) if t}
    jobs: list[str] = []
    for s in songs:
        if s.removed or s.first_mix not in ("Phoenix", "Phoenix 2"):
            continue
        cut = CUTS.get(s.cut, "ARCADE")
        titles = {base_title(t) for t in (s.title, s.titles.get("en", "").strip()) if t}
        if any((norm(t), cut) in listed for t in titles):
            continue
        folder, mix = ("16 - PHOENIX", "18") if s.first_mix == "Phoenix" else ("17 - PHOENIX 2", "19")
        for t in titles:
            for name in _safe_name(t):
                if cut == "SHORTCUT":
                    name = f"[Short Cut] {name}"
                    ids = [f"{mix}F{i:02d}" for i in range(100)]
                elif cut == "ARCADE":
                    ids = [str(i) for i in range(int(mix) * 1000, int(mix) * 1000 + 401, 5)]
                else:
                    continue
                jobs += [f"{folder}/{i} - {name}/{name}.ssc" for i in ids]
    return sorted(set(jobs))


def guess_paths(songs: list[PoSong], entries: list[dict[str, Any]], fetcher: Fetcher,
                progress: bool = False) -> list[str]:
    """The `guess_candidates` paths that exist in rayden-61/PIU-Simfiles."""
    from concurrent.futures import ThreadPoolExecutor

    from .fetch import FetchError

    jobs = guess_candidates(songs, entries)

    def exists(path: str) -> str:
        try:
            fetcher.get(simfile_url(path))
            return path
        except (NotFound, FetchError):
            return ""

    with ThreadPoolExecutor(8) as pool:
        found = [p for p in pool.map(exists, jobs) if p]
    if progress:
        print(f"guessed {len(jobs)} paths, found {len(found)}", flush=True)
    return found


def build_manifest(fetcher: Fetcher, candidates: set[str], progress: bool = False,
                   sources: dict[str, int] | None = None) -> dict[str, Any]:
    """Fetch every candidate simfile from rayden-61/PIU-Simfiles, keep the ones that exist, and summarise each."""
    from concurrent.futures import ThreadPoolExecutor

    from .fetch import FetchError

    def one(path: str) -> dict[str, Any] | str:
        try:
            text = fetcher.get(simfile_url(path)).decode("utf-8-sig", errors="replace")
        except NotFound:
            return "stale"
        except FetchError as e:
            return f"error: {e}"
        return manifest_entry(path, parse_simfile(text, "ssc" if path.lower().endswith(".ssc") else "sm"))

    files, stale, errors = [], [], []
    with ThreadPoolExecutor(8) as pool:
        for n, (path, row) in enumerate(zip(sorted(candidates), pool.map(one, sorted(candidates))), 1):
            if isinstance(row, dict):
                files.append(row)
            elif row == "stale":
                stale.append(path)
            else:
                errors.append(f"{path}: {row}")
            if progress and n % 100 == 0:
                print(f"{n}/{len(candidates)} files, {len(stale)} stale", flush=True)
    if errors:
        raise RuntimeError(f"{len(errors)} simfiles could not be fetched, e.g. {errors[0]}")
    return {"repo": RAYDEN_REPO, "base": RAYDEN, "folders": FOLDERS, "chart_fields": MANIFEST_CHART_FIELDS,
            "listed": len(candidates), "stale": len(stale), "sources": sources or {}, "files": files}


# ------------------------------------------------------------------------------------------------ catalog

#: words in a simfile chart description that mark a chart the arcade does not have as a plain chart
SPECIAL_WORDS = {"HIDDEN", "QUEST", "PRO", "UCS", "TRAIN", "ANOTHER", "INFINITY", "INFOBAR", "TITLE", "NEW",
                 "BOSS", "MISSION", "SQUEST", "DQUEST", "CR", "JUMP", "RANDOM"}
_FILE_CUTS = {"ARCADE": "ARCADE", "SHORTCUT": "SHORTCUT", "FULLSONG": "FULLSONG", "REMIX": "REMIX"}


def file_cut(entry: dict[str, Any]) -> str:
    """ARCADE / SHORTCUT / FULLSONG / REMIX for a manifest file: #SONGTYPE, else the folder or title marker."""
    t = re.sub(r"[^A-Z]", "", (entry.get("songtype") or "").upper())
    if t in _FILE_CUTS:
        return t
    text = (entry["path"].rsplit("/", 2)[-2] + " " + entry.get("title", "")).lower()
    for word, cut in (("short cut", "SHORTCUT"), ("full song", "FULLSONG"), ("remix", "REMIX")):
        if word in text:
            return cut
    return "ARCADE"


def sim_style(steps_type: str, description: str) -> str:
    """The Pumpout mode a simfile chart plays as: S, D, HDB, C, SP, DP or "" (not a playable PIU chart)."""
    st, desc = steps_type.lower(), description.upper()
    if "COOP" in desc.replace("-", "") and st in ("pump-double", "pump-routine", "pump-couple"):
        return "C"
    if st == "pump-halfdouble" or (st == "pump-double" and "HALFDOUBLE" in desc.replace("-", "")):
        return "HDB"
    if "PERFORMANCE" in desc or re.match(r"^(SP|DP)\d", desc):
        return {"pump-single": "SP", "pump-double": "DP"}.get(st, "")
    if st == "pump-routine":
        return "R"
    return {"pump-single": "S", "pump-double": "D"}.get(st, "")


def sim_level(mode: str, meter: str, description: str) -> str:
    """The level a simfile chart claims: its meter, or for co-op the number of players ("COOP 4P" -> "4")."""
    if mode == "C":
        m = re.search(r"(\d)\s*P\b", description.upper()) or re.search(r"X\s*(\d)", description.upper())
        return m.group(1) if m else ""
    return meter.strip()


def _clean(description: str, mode: str, level: str) -> bool:
    words = set(re.findall(r"[A-Z]+", description.upper()))
    return not (words & SPECIAL_WORDS)


def match_charts(song: PoSong, entry: dict[str, Any], fields: list[str]
                 ) -> tuple[dict[int, tuple[int, list[Any], str]], list[str]]:
    """Pair the song's Pumpout charts with charts of a simfile: chart_id -> (index in the file, manifest row, how).

    Only ACTIVE charts and charts without HIDDEN/QUEST/PRO/UCS... in the description take part. Pass 1 ("level") pairs
    the same mode and level (meter; players for co-op). Among candidates the simfile's ACTIVE charts come first, then
    plain descriptions, then a credit naming the stepmaker. Pass 2 ("re-rated") pairs a left-over chart with a left-over chart at a level Pumpout says it had in
    another version (simfiles often keep an older rating). Pass 3 ("near-level") pairs a left-over chart with the
    nearest left-over ACTIVE chart of its mode whose meter is within 2: a guess, flagged in the chart's warnings."""
    ix = {f: i for i, f in enumerate(fields)}
    rows = [(i, r, sim_style(r[ix["steps_type"]], r[ix["description"]])) for i, r in enumerate(entry["charts"])]
    # a chart that is neither ACTIVE nor plain (UCS, QUEST, PRO, HIDDEN, INFINITY ...) is not the arcade's
    rows = [(i, r, m, sim_level(m, str(r[ix["meter"]]), r[ix["description"]])) for i, r, m in rows
            if m and (r[ix["active"]] or _clean(r[ix["description"]], m, ""))]
    by_index = {i: r for i, r, _m, _l in rows}
    out: dict[int, tuple[int, list[Any], str]] = {}
    used: set[int] = set()

    def greedy(levels: dict[int, set[tuple[str, str]]], how: str) -> None:
        pairs: list[tuple[tuple, int, int]] = []
        for c in song.charts:
            if c.chart_id in out:
                continue
            makers = {norm(x) for x in c.stepmakers if norm(x)}
            for i, r, m, lvl in rows:
                if i in used or (m, lvl) not in levels[c.chart_id]:
                    continue
                credit = norm(r[ix["credit"]])
                score = (r[ix["active"]], _clean(r[ix["description"]], m, lvl),
                         any(x and (x in credit or credit in x) for x in makers) if credit else False, -i)
                pairs.append((score, c.chart_id, i))
        for _score, cid, i in sorted(pairs, key=lambda x: x[0], reverse=True):
            if cid not in out and i not in used:
                out[cid] = (i, by_index[i], how)
                used.add(i)

    greedy({c.chart_id: {(c.mode, c.level)} for c in song.charts}, "level")
    greedy({c.chart_id: {(m, lvl) for m, lvl in c.former if m == c.mode} for c in song.charts}, "re-rated")
    notes: list[str] = []
    for c in song.charts:
        if c.chart_id in out or not c.level.isdigit():
            continue
        left = [(abs(int(lvl) - int(c.level)), i) for i, r, m, lvl in rows
                if m == c.mode and i not in used and r[ix["active"]] and lvl.isdigit()
                and abs(int(lvl) - int(c.level)) <= 2]
        if left:
            _d, i = min(left)
            out[c.chart_id] = (i, by_index[i], "near-level")
            used.add(i)
    for c in song.charts:
        if c.chart_id in out and out[c.chart_id][2] != "level":
            i, r, how = out[c.chart_id]
            notes.append(f"{native_for(c.mode, c.level)}: simfile chart {r[ix['description']]!r} has meter "
                         f"{r[ix['meter']]} ({how})")
    return out, notes


def _titles(song: PoSong) -> set[str]:
    return {norm(base_title(t)) for t in (song.title, song.titles.get("en", "")) if t and norm(base_title(t))}


def _file_keys(entry: dict[str, Any]) -> set[str]:
    folder = entry["path"].rsplit("/", 2)[-2]
    folder = re.sub(r"^(?:\(\w+\)\s*)?[0-9A-Za-z.]+(?:\s*\(\d+\))?\s+-\s+", "", folder)
    return {k for k in (norm(base_title(entry["title"])), norm(base_title(entry.get("translit", ""))),
                        norm(base_title(folder))) if k}


def _artist_ok(entry: dict[str, Any], song: PoSong) -> bool:
    a = norm(entry.get("artist"))
    names = [norm(song.artist)] + [norm(x) for x in song.artists]
    return bool(a) and any(x and (x == a or x in a or a in x) for x in names)


def is_fan_transcription(entry: dict[str, Any], row: list[Any] | None = None, fields: list[str] | None = None) -> bool:
    credit = (entry.get("credit") or "").lower()
    if row is not None and fields is not None:
        credit += " " + str(row[fields.index("credit")]).lower()
    return COPYCAT in credit


def kali_index(songlist: str, thumbs_ts: str) -> dict[str, list[str]]:
    """norm(title as kalinathalie lists it) -> thumbnail slugs (their slugify of the song list line, `_2` for a
    repeat; only slugs with a thumbnail)."""
    have = set(re.findall(r'"([^"]+)":\s*require\(', thumbs_ts))
    counts: dict[str, int] = {}
    out: dict[str, list[str]] = {}
    headers = {"1ST TO ZERO", "NX TO NXA", "FIESTA TO FIESTA2", "PRIME", "PRIME2", "XX", "PHOENIX"}
    seen_headers: set[str] = set()
    for line in songlist.splitlines():
        line = line.strip()
        if not line:
            continue
        up = re.sub(r"\s+", " ", line.upper())
        if up in headers and up not in seen_headers:
            seen_headers.add(up)
            continue
        slug = kali_slug(line) or "song"
        counts[slug] = counts.get(slug, 0) + 1
        if counts[slug] > 1:
            slug = f"{slug}_{counts[slug]}"
        if slug in have:
            out.setdefault(norm(line), []).append(slug)
    return out


_KALI_CUT = {"Arcade": [""], "Short Cut": ["short cut", "shortcut"], "Full Song": ["full song", "fullsong"],
             "Remix": ["remix", ""]}


def kali_slug_for(song: PoSong, index: dict[str, list[str]]) -> str:
    for t in (song.titles.get("en", "").strip(), song.title):
        if not t:
            continue
        for suffix in _KALI_CUT.get(song.cut, [""]):
            hits = index.get(norm(f"{base_title(t)} {suffix}"), [])
            if len(hits) == 1:
                return hits[0]
    return ""


def build_catalog(po_songs: list[PoSong], manifest: dict[str, Any], kali: dict[str, list[str]] | None = None
                  ) -> tuple[list[SongRecord], dict[str, Any]]:
    fields = manifest.get("chart_fields", MANIFEST_CHART_FIELDS)
    files = manifest["files"]
    by_id: dict[str, list[dict[str, Any]]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if f.get("id"):
            by_id.setdefault(f["id"].upper(), []).append(f)
        for k in _file_keys(f):
            by_title.setdefault(k, []).append(f)
    report: dict[str, Any] = {"songs": 0, "removed_songs": 0, "charts": 0, "simfile_by_id": 0,
                              "simfile_by_title": 0, "no_simfile": [], "charts_with_simfile": 0,
                              "charts_re_rated": 0, "charts_near_level": 0, "charts_without_simfile": 0,
                              "fan_transcription_charts": 0,
                              "fallback_ids": 0, "ambiguous": [], "kali_jackets": 0}
    songs: list[SongRecord] = []
    used: set[str] = set()
    # live songs first, so a removed song never takes a live song's id
    for s in sorted(po_songs, key=lambda x: (bool(x.removed), x.song_id)):
        gid = real_game_id(s.game_id)
        cut = CUTS.get(s.cut, "ARCADE")
        cands = {id(f): f for f in by_id.get(gid.upper(), [])} if gid else {}
        how = "id" if cands else ""
        for k in _titles(s):
            for f in by_title.get(k, []):
                cands.setdefault(id(f), f)
        scored = []
        for f in cands.values():
            if file_cut(f) != cut:
                continue
            id_hit = bool(gid) and f.get("id", "").upper() == gid.upper()
            title_hit = bool(_file_keys(f) & _titles(s))
            matched, _ = match_charts(s, f, fields)
            exact = sum(1 for v in matched.values() if v[2] == "level")
            if not (id_hit or (title_hit and (_artist_ok(f, s) or exact >= max(1, len(s.charts) // 2)))):
                continue
            newest = FOLDERS.index(f["path"].split("/")[0]) if f["path"].split("/")[0] in FOLDERS else -1
            scored.append(((exact, len(matched), id_hit, _artist_ok(f, s), newest), f, matched))
        best = max(scored, key=lambda x: x[0]) if scored else None
        if len(scored) > 1:
            path = best[1]["path"]  # type: ignore[index]
            report["ambiguous"].append(f"{s.title} ({s.cut}): {path} of {len(scored)}")
        entry = best[1] if best else None
        sid = song_id_for(gid, entry)
        if not sid or sid.lower() in used:
            sid = fallback_id(s.song_id)
            report["fallback_ids"] += 1
        used.add(sid.lower())
        rec = _record(s, sid, entry, fields, kali or {})
        if entry is not None:
            how = "id" if (gid and entry.get("id", "").upper() == gid.upper()) else "title"
            report["simfile_by_id" if how == "id" else "simfile_by_title"] += 1
        elif not s.removed:
            report["no_simfile"].append(f"{s.title} ({s.cut}, {s.first_mix})")
        report["kali_jackets"] += bool(rec.source.get("kalinathalie"))
        report["removed_songs" if s.removed else "songs"] += 1
        for cr in rec.charts.values():
            if s.removed:
                continue
            report["charts"] += 1
            sf = cr.source.get("simfile")
            if sf:
                report["charts_with_simfile"] += 1
                report["charts_re_rated"] += sf.get("match") == "re-rated"
                report["charts_near_level"] += sf.get("match") == "near-level"
                report["fan_transcription_charts"] += bool(sf.get("fan_transcription"))
            else:
                report["charts_without_simfile"] += 1
        songs.append(rec)
    report["no_simfile_count"] = len(report["no_simfile"])
    return songs, report


def _record(s: PoSong, sid: str, entry: dict[str, Any] | None, fields: list[str],
            kali: dict[str, list[str]]) -> SongRecord:
    title = (s.titles.get("en") or s.title).strip()
    rec = SongRecord(
        game="piu", song_id=sid, title=title, artist=s.artist, genre=s.category, bpm=s.bpm, version=s.first_mix,
        source={"pumpout": {k: v for k, v in {"song_id": s.song_id, "game_id": s.game_id, "card": s.card,
                                               "first": s.first, "title": s.title}.items() if v}},
        extra={k: v for k, v in {"cut": s.cut, "titles": {k: v for k, v in s.titles.items() if k != "en"},
                                 "artists": s.artists if len(s.artists) > 1 else [], "labels": s.labels,
                                 "removed": bool(s.removed), "removed_in": s.removed,
                                 "revived_in": s.revived}.items() if v},
    )
    slug = kali_slug_for(s, kali)
    if slug:
        rec.source["kalinathalie"] = {"slug": slug}
    matched: dict[int, tuple[int, list[Any], str]] = {}
    notes: list[str] = []
    if entry is not None:
        matched, notes = match_charts(s, entry, fields)
        rec.source["simfile"] = {"path": entry["path"], "url": simfile_url(entry["path"]), "id": entry.get("id", ""),
                                 "title": entry["title"], "artist": entry["artist"]}
        if is_fan_transcription(entry):
            rec.source["simfile"]["fan_transcription"] = True
    if notes:
        rec.extra["simfile_notes"] = notes
    ix = {f: i for i, f in enumerate(fields)}
    seen: dict[str, int] = {}
    for c in s.charts:
        style, _ = MODES.get(c.mode, (c.mode.lower(), c.mode))
        base = slot_name(style, c.level)
        seen[base] = seen.get(base, 0) + 1
        slot = slot_name(style, c.level, seen[base])
        cr = ChartRecord(slot=slot, native=native_for(c.mode, c.level) if c.mode in MODES else f"{c.mode}{c.level}",
                         level=c.level, designer=", ".join(c.stepmakers),
                         source={"pumpout": {k: v for k, v in {"chart_id": c.chart_id, "mode": c.mode,
                                                                "labels": c.labels}.items() if v}})
        if c.level.isdigit() and c.mode != "C":
            cr.source["ladder"] = slot_for_level(int(c.level))
        hit = matched.get(c.chart_id)
        if hit is not None and entry is not None:
            i, row, how = hit
            cr.source["simfile"] = {"chart": i, "steps_type": row[ix["steps_type"]],
                                    "description": row[ix["description"]], "meter": row[ix["meter"]],
                                    "active": bool(row[ix["active"]]), "credit": row[ix["credit"]],
                                    "combo": row[ix["combo"]], "match": how,
                                    **({"fan_transcription": True} if is_fan_transcription(entry, row, fields)
                                       else {})}
            cr.note_counts = {"combo": row[ix["combo"]]}     # computed from the simfile (no published counts)
        rec.charts[slot] = cr
    return rec


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    po_songs, po_report = read_pumpout(_pumpout_db(fetcher))
    try:
        kali = kali_index(fetcher.get(KALI + "pipeline/catalog/songlist.txt").decode("utf-8", "replace"),
                          fetcher.get(KALI_THUMBS_TS).decode("utf-8", "replace"))
    except NotFound:
        kali = {}
    manifest = load_manifest()
    songs, report = build_catalog(po_songs, manifest, kali)
    report["no_simfile"] = report["no_simfile"][:60]
    report["ambiguous"] = report["ambiguous"][:20]
    report["manifest_files"] = len(manifest["files"])
    return songs, {**po_report, **report}


# ------------------------------------------------------------------------------------------------ charts

_SIM_CACHE: OrderedDict[str, Simfile] = OrderedDict()
_SIM_LOCK = threading.Lock()


def load_simfile(fetcher: Fetcher, url: str) -> Simfile:
    """Parse a simfile once (a song's charts all come from one file); a small LRU shared by scrape's threads."""
    key = f"{fetcher.cache_dir}|{url}"
    with _SIM_LOCK:
        if key in _SIM_CACHE:
            _SIM_CACHE.move_to_end(key)
            return _SIM_CACHE[key]
    text = fetcher.get(url).decode("utf-8-sig", errors="replace")
    sim = parse_simfile(text, "ssc" if url.lower().endswith(".ssc") else "sm")
    with _SIM_LOCK:
        _SIM_CACHE[key] = sim
        while len(_SIM_CACHE) > 64:
            _SIM_CACHE.popitem(last=False)
    return sim


def find_chart(sim: Simfile, ref: dict[str, Any]) -> SmChart | None:
    """The simfile chart a ChartRecord's `source.simfile` points at: by index, checked against its steps type,
    description and meter, else the first chart that has all three."""
    def same(c: SmChart) -> bool:
        return (c.steps_type == ref.get("steps_type") and c.description == ref.get("description")
                and str(c.meter) == str(ref.get("meter")))
    i = ref.get("chart")
    if isinstance(i, int) and 0 <= i < len(sim.charts) and same(sim.charts[i]):
        return sim.charts[i]
    return next((c for c in sim.charts if same(c)), None)


def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    return ChartMeta(
        game="piu", song_id=song.song_id, title=song.title, artist=song.artist, title_romaji=song.title_romaji,
        artist_romaji=song.artist_romaji, genre=song.genre, difficulty=slot, difficulty_native=cr.native,
        level=cr.level, designer=cr.designer, bpm_display=song.bpm, source=json.loads(json.dumps(cr.source)),
        note_counts=dict(cr.note_counts),
    )


def chart_from_simfile(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    ref = song.source.get("simfile")
    cr = song.charts.get(slot)
    if not ref or cr is None or not cr.source.get("simfile"):
        return None
    sf = cr.source["simfile"]
    sim = load_simfile(fetcher, ref["url"])
    sm_chart = find_chart(sim, sf)
    if sm_chart is None:
        raise RuntimeError(f"{ref['path']} has no chart {sf.get('steps_type')} {sf.get('description')!r} meter "
                           f"{sf.get('meter')} any more; rebuild the manifest")
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "piu-simfiles-rayden", "url": ref["url"]})
    chart = chart_to_ir(sim, sm_chart, meta)
    counts = pump_counts(sim, sm_chart, chart)
    chart.meta.source["piu_counts"] = counts
    if cr.note_counts.get("combo") not in (None, counts["combo"]):
        chart.warnings.append(f"combo {counts['combo']} from the simfile, the catalog has {cr.note_counts['combo']} "
                              f"(manifest out of date)")
    chart.meta.note_counts["combo"] = counts["combo"]
    if COPYCAT in f"{sim.tags.get('CREDIT', '')} {sm_chart.credit}".lower():
        chart.meta.source["fan_transcription"] = "The Copycat Steps"
        chart.warnings.append("fan transcription (The Copycat Steps), not Andamiro's chart data: notes may differ "
                              "from the arcade")
    if sf.get("match") == "re-rated":
        chart.warnings.append(f"simfile meter {sm_chart.meter} differs from the PHOENIX 2 level {cr.level}: the "
                              f"chart was re-rated (Pumpout lists the old level), and the notes may be an older "
                              f"version")
    elif sf.get("match") == "near-level":
        chart.warnings.append(f"simfile meter {sm_chart.meter} differs from the PHOENIX 2 level {cr.level} and "
                              f"Pumpout never rated the chart {sm_chart.meter}: paired as the nearest left-over ACTIVE "
                              f"chart, which may be a different chart")
    if not sf.get("active"):
        chart.warnings.append(f"the simfile does not mark this chart ACTIVE (description {sm_chart.description!r})")
    return chart


# ------------------------------------------------------------------------------------------------ jackets

def card_urls(card: str) -> list[str]:
    """Pumpout's song card (a still from the song's video) in its repo: `img/` (250 px wide), else `img_big/`."""
    name = card.rsplit("/", 1)[-1]
    return [PUMPOUT + "img/" + urllib.parse.quote(name), PUMPOUT + "img_big/" + urllib.parse.quote(name)] if name \
        else []


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    urls = []
    slug = song.source.get("kalinathalie", {}).get("slug")
    if slug:
        urls.append(KALI_THUMB + urllib.parse.quote(slug) + ".jpg")
    urls += card_urls(song.source.get("pumpout", {}).get("card", ""))
    for url in urls:
        try:
            return fetcher.get(url), url.rsplit(".", 1)[-1].lower(), url
        except NotFound:
            continue
    return None


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="piu", catalog=_catalog, chart=chart_from_simfile, jacket=_jacket,
                        notes="catalog: Pumpout database (PHOENIX 2 v1.02.0); charts: rayden-61/PIU-Simfiles "
                              "(manifest data/piu_simfiles.json); jackets: kalinathalie/piu-charts, else Pumpout "
                              "cards"))


_register()


def write_manifest(m: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
    # one file per line, so a rebuilt manifest diffs cleanly
    text = text.replace(',{"path":', ',\n{"path":').replace('"files":[{', '"files":[\n{')
    out.write_text(text + "\n", "utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m vsrgconv.sources.piu")
    p.add_argument("command", choices=["build-manifest"])
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--code-search", action="store_true", help="list thedaryen/PIU-Simfiles with `gh` code search")
    p.add_argument("--piu-vis", action="store_true", help="read paths from piu-vis-ss chart metadata")
    p.add_argument("--guess", action="store_true", help="guess PHOENIX / PHOENIX 2 folders for unlisted songs")
    p.add_argument("--paths", action="append", default=[], help="JSON list of extra candidate paths")
    p.add_argument("--out", default=str(MANIFEST))
    a = p.parse_args(argv)
    f = Fetcher(a.cache, min_interval=0.02)
    candidates: set[str] = set()
    sources: dict[str, int] = {}
    if Path(a.out).exists():
        prev = {r["path"] for r in load_manifest(Path(a.out))["files"]}
        sources["previous_manifest"] = len(prev)
        candidates |= prev
    for extra in a.paths:
        got = set(json.loads(Path(extra).read_text("utf-8")))
        sources[Path(extra).name] = len(got)
        candidates |= got
    if a.code_search:
        got = set(code_search_paths(progress=True))
        sources["code_search"] = len(got)
        candidates |= got
    if a.piu_vis:
        got = set(piu_vis_paths(f, progress=True))
        sources["piu_vis_ss"] = len(got)
        candidates |= got
    m = build_manifest(f, candidates, progress=True, sources=sources)
    if a.guess:
        songs, _ = read_pumpout(_pumpout_db(f), removed=False)
        got = set(guess_paths(songs, m["files"], f, progress=True)) - candidates
        sources["guessed"] = len(got)
        extra = build_manifest(f, got)
        m["files"] = sorted(m["files"] + extra["files"], key=lambda r: r["path"])
        m["listed"] += len(got)
    write_manifest(m, Path(a.out))
    print(json.dumps({k: v for k, v in m.items() if k != "files"} | {"files": len(m["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
