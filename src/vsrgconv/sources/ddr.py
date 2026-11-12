"""DanceDanceRevolution (WORLD) sources. Details, URLs and coverage: docs/sources/ddr.md.

- Catalog: DDRCardDraw's `ddr_world.json` (every WORLD song and chart: level, EX max score, version folder, flags,
  jacket), joined to Tachi's DDR seeds (Konami's song basename, in-game id and published step counts) by Konami's song
  hash, else by title and artist.
- Charts: StepMania simfiles from blackalice/ddr-dan-tool (copied from the Zenius-I-vanisher DDR packs), parsed by
  `formats/stepmania.py`. The list of files is the committed manifest `data/ddr_simfiles.json`, built by
  `python -m vsrgconv.sources.ddr build-manifest` (that needs `gh` for code search; the adapter does not). The manifest
  also keeps each file's headers and per-chart step counts, so the catalog matches songs to files offline.
- Jackets: `<dir>-jacket.png` beside the simfile (512 px; the SN1/SN2 folders have none), else DDRCardDraw's jacket
  (128-200 px).

song_id is Konami's song basename (e.g. "albi" for ALBIDA): Tachi's `basename`, else the simfile's
`//----- song ID: xxxx -----//` header. A song with neither gets `h` + the first 10 characters of its song hash,
lower-cased (`fallback_id`).

Slots: SINGLE charts take the ladder slot (BEGINNER easy ... CHALLENGE master), DOUBLE charts `double-<slot>`
(8 lanes). The native name ("DOUBLE EXPERT"), level and published step count stay on each ChartRecord.
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..db import ChartRecord, SongRecord
from ..formats.stepmania import Simfile, SmChart, chart_to_ir, count_steps, parse_simfile
from ..games.registry import canonical_difficulty, native_name
from ..model import Chart, ChartMeta
from .fetch import Fetcher, NotFound

DDRCD = "https://raw.githubusercontent.com/noahm/DDRCardDraw/HEAD/"
DDRCD_URL = DDRCD + "src/songs/ddr_world.json"
DDRCD_JACKET = DDRCD + "src/assets/jackets/"
TACHI = "https://raw.githubusercontent.com/zkldi/Tachi/HEAD/db/seeds/"
TACHI_SONGS = TACHI + "songs-ddr.json"
TACHI_CHARTS = {"single": TACHI + "charts-ddr-sp.json", "double": TACHI + "charts-ddr-dp.json"}
DAN_REPO = "blackalice/ddr-dan-tool"
DAN = f"https://raw.githubusercontent.com/{DAN_REPO}/HEAD/"
DAN_SONG_IDS = DAN + "data/song-ids.json"
#: simfile folders in the repo, one per ZiV arcade pack (data/song-packs.config.json there), oldest first
DAN_FOLDERS = ["DDR", "2nd", "3rd", "4th", "4th Plus", "5th", "6th", "7th", "EX", "SN1", "SN2", "X", "X2",
               "X3 vs 2nd", "2013", "2014", "A", "A20", "A20 Plus", "A3", "World"]
MANIFEST = Path(__file__).parent / "data" / "ddr_simfiles.json"

STYLES = {"single": "dance-single", "double": "dance-double"}
DIFF_CLASSES = ["beginner", "basic", "difficult", "expert", "challenge"]
#: StepMania difficulty name -> DDR difficulty
SM_TO_DDR = {"Beginner": "beginner", "Easy": "basic", "Medium": "difficult", "Hard": "expert", "Challenge": "challenge"}
BASENAME_RE = re.compile(r"^[a-z0-9]{2,8}$")


# ------------------------------------------------------------------------------------------------ names

def slot_for(style: str, diff_class: str) -> str:
    base = canonical_difficulty("ddr", diff_class)
    return base if style == "single" else f"double-{base}"


def native_for(style: str, diff_class: str) -> str:
    return native_name("ddr", diff_class if style == "single" else f"double {diff_class}")


def style_and_class(slot: str) -> tuple[str, str]:
    """("single" | "double", "beginner" ... "challenge") for a DDR slot."""
    style, base = ("double", slot[len("double-"):]) if slot.startswith("double-") else ("single", slot)
    for dc in DIFF_CLASSES:
        if canonical_difficulty("ddr", dc) == base:
            return style, dc
    raise KeyError(f"not a DDR slot: {slot!r}")


def fallback_id(song_hash: str) -> str:
    return "h" + song_hash[:10].lower()


#: Cyrillic and Greek letters DDR titles use as look-alikes of Latin ones (TЁЯRA, RЁVOLUTIФN), after accents go
_LOOKALIKES = str.maketrans("аеёорсухяфφиклмнт", "aeeopcyxroonkamht")


def norm(text: str | None) -> str:
    """Match key: case, width, accents and look-alike letters folded; punctuation and spaces removed."""
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).translate(_LOOKALIKES)
    return re.sub(r"[\W_]+", "", t)


def simfile_url(path: str) -> str:
    return DAN + urllib.parse.quote(path, safe="/")


def jacket_url(path: str) -> str:
    """The 512 px jacket beside a simfile: `<dir>/<dir>-jacket.png` (every simfile is named after its folder)."""
    folder = path.rsplit("/", 1)[0]
    return DAN + urllib.parse.quote(f"{folder}/{folder.rsplit('/', 1)[-1]}-jacket.png", safe="/")


def ddrcd_jacket_url(name: str) -> str:
    return DDRCD_JACKET + urllib.parse.quote(name, safe="/")


# ------------------------------------------------------------------------------------------------ manifest

def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


MANIFEST_CHART_FIELDS = ["meter", "steps", "freezes", "freeze_rows", "shocks"]


def _chart_counts(c: dict[str, Any]) -> list[Any]:
    return [c["meter"], c["steps"], c["holds"] + c["rolls"], c["hold_rows"], c["mine_rows"]]


def combo_of(steps: int, freeze_rows: int, shocks: int) -> int:
    """DDR's max combo: steps (a jump is one), plus freeze rows (a freeze jump is one OK), plus shock arrow rows. This
    is Tachi's `stepCount`, and a third of the EX score maximum (DDRCardDraw `maxScore`)."""
    return steps + freeze_rows + shocks


def manifest_entry(path: str, sim: Simfile) -> dict[str, Any]:
    """One manifest row: headers, and per DDR chart MANIFEST_CHART_FIELDS under "<style>/<sm diff>"."""
    from ..formats.stepmania import summary

    s = summary(sim)
    charts: dict[str, list[Any]] = {}
    for c in s["charts"]:
        style = next((k for k, v in STYLES.items() if v == c["type"]), None)
        if style and c["difficulty"] in SM_TO_DDR:
            charts.setdefault(f"{style}/{c['difficulty']}", _chart_counts(c))
    return {"path": path, "title": s["title"], "translit": s["titletranslit"], "artist": s["artist"],
            "song_id": s["song_id"], "charts": charts}


def build_manifest(fetcher: Fetcher, use_code_search: bool = True, progress: bool = False) -> dict[str, Any]:
    """List every simfile in the repo (song-ids.json, plus `gh` code search per folder), keep the ones that exist,
    and summarise each. Run by hand when the repo changes; the result is committed."""
    from concurrent.futures import ThreadPoolExecutor

    from .fetch import FetchError

    candidates = {"public/" + k for k in json.loads(fetcher.get(DAN_SONG_IDS, refresh=True))}
    searched = 0
    if use_code_search:
        for p in code_search_paths(progress=progress):
            searched += 1
            candidates.add(p)
    candidates = {p for p in candidates if p.lower().endswith((".sm", ".ssc"))}

    def one(path: str) -> dict[str, Any] | str:
        try:
            text = fetcher.get(simfile_url(path)).decode("utf-8-sig", errors="replace")
        except NotFound:
            return "stale"
        except FetchError as e:
            return f"error: {e}"
        return manifest_entry(path, parse_simfile(text, path.rsplit(".", 1)[-1].lower()))

    files, stale, errors = [], 0, []
    with ThreadPoolExecutor(8) as pool:
        for path, row in zip(sorted(candidates), pool.map(one, sorted(candidates))):
            if isinstance(row, dict):
                files.append(row)
            elif row == "stale":
                stale += 1
            else:
                errors.append(f"{path}: {row}")
    if errors:
        raise RuntimeError(f"{len(errors)} simfiles could not be fetched, e.g. {errors[0]}")
    return {"repo": DAN_REPO, "base": DAN, "folders": DAN_FOLDERS, "chart_fields": MANIFEST_CHART_FIELDS,
            "listed": len(candidates), "stale": stale, "code_search_hits": searched, "files": files}


def code_search_paths(progress: bool = False) -> list[str]:
    """Simfile paths per folder from GitHub code search (`gh api`, rate-limited to about 10 requests a minute)."""
    import subprocess
    import time

    out: list[str] = []
    for folder in DAN_FOLDERS:
        page = 1
        while True:
            for attempt in range(5):
                r = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f",
                                    f'q=repo:{DAN_REPO} path:"public/sm/{folder}"', "-f", "per_page=100",
                                    "-f", f"page={page}"], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                time.sleep(20 * (attempt + 1))      # secondary rate limit
            else:
                raise RuntimeError(f"code search failed for {folder}: {r.stderr.strip()[:200]}")
            data = json.loads(r.stdout)
            items = [i["path"] for i in data.get("items", [])
                     if i["path"].split("/")[2:3] == [folder]]
            out += items
            if progress:
                print(f"{folder} page {page}: {len(items)} of {data.get('total_count')}", flush=True)
            time.sleep(7)
            if len(data.get("items", [])) < 100 or page * 100 >= data.get("total_count", 0):
                break
            page += 1
    return out


# ------------------------------------------------------------------------------------------------ catalog

def _tachi_charts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["songID"], r["difficulty"].lower()): r for r in rows}


def _artist_ok(sim_artist: str, song: dict[str, Any]) -> bool:
    a = norm(sim_artist)
    names = [norm(song.get("artist")), norm(song.get("artist_translation"))]
    return bool(a) and any(x and (x == a or x in a or a in x) for x in names)


def _score(entry: dict[str, Any], song: dict[str, Any], steps: dict[tuple[str, str], int]) -> tuple:
    """How well a simfile's charts agree with the catalog: counts matching, then levels, then charts present, then
    exact title, exact artist, folder named like the title (not a "(Chinese)"/"(AC)" variant), newest pack."""
    count_hits = level_hits = present = 0
    for c in song["charts"]:
        sm_diff = next((k for k, v in SM_TO_DDR.items() if v == c["diffClass"]), "")
        row = entry["charts"].get(f"{c['style']}/{sm_diff}")
        if row is None:
            continue
        present += 1
        meter, n_steps, _freezes, freeze_rows, shocks = row
        combo = combo_of(n_steps, freeze_rows, shocks)
        level_hits += str(meter) == str(c["lvl"])
        published = steps.get((c["style"], c["diffClass"]))
        count_hits += published is not None and published == combo
        count_hits += c.get("maxScore") is not None and c["maxScore"] == 3 * combo
    titles = (norm(song["name"]), norm(song.get("name_translation")))
    exact = norm(entry["title"]) in titles
    artist = norm(entry["artist"]) in (norm(song.get("artist")), norm(song.get("artist_translation")))
    folder = entry["path"].split("/")
    plain_dir = norm(folder[3]) in (*titles, norm(entry["title"]), norm(entry["translit"]))
    newest = DAN_FOLDERS.index(folder[2]) if folder[2] in DAN_FOLDERS else -1
    return count_hits, level_hits, present, exact, artist, plain_dir, newest


def build_catalog(ddrcd: dict[str, Any], tachi_songs: list[dict[str, Any]],
                  tachi_charts: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]
                  ) -> tuple[list[SongRecord], dict[str, Any]]:
    by_hash = {t["data"]["ddrSongHash"]: t for t in tachi_songs if t["data"].get("ddrSongHash")}
    tachi_by_title: dict[str, list[dict[str, Any]]] = {}
    for t in tachi_songs:
        for k in {norm(t["title"]), *(norm(a) for a in t.get("altTitles", []))}:
            tachi_by_title.setdefault(k, []).append(t)
    tcharts = {style: _tachi_charts(rows) for style, rows in tachi_charts.items()}
    files = manifest["files"]
    by_sid: dict[str, list[dict[str, Any]]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if f["song_id"]:
            by_sid.setdefault(f["song_id"], []).append(f)
        for k in {norm(f["title"]), norm(f["translit"]), norm(f["path"].split("/")[3])}:
            if k:
                by_title.setdefault(k, []).append(f)

    # basenames that belong to a catalog song (through Tachi's song hash): a simfile naming one of these is that song
    claimed = {by_hash[s["id"]]["data"]["basename"] for s in ddrcd["songs"] if s["id"] in by_hash}
    songs: list[SongRecord] = []
    used_ids: set[str] = set()
    report: dict[str, Any] = {"ddrcarddraw_songs": len(ddrcd["songs"]), "tachi_by_hash": 0, "tachi_by_title": 0,
                              "simfile_by_song_id": 0, "simfile_by_title": 0, "no_simfile": [],
                              "fallback_ids": 0, "ambiguous": []}
    for s in ddrcd["songs"]:
        t = by_hash.get(s["id"])
        if t is not None:
            report["tachi_by_hash"] += 1
        else:
            cands = [x for k in {norm(s["name"]), norm(s.get("name_translation"))} if k
                     for x in tachi_by_title.get(k, []) if not x["data"].get("ddrSongHash")]
            cands = [x for x in cands if norm(x["artist"]) == norm(s["artist"])] or cands[:1]
            t = cands[0] if cands else None
            report["tachi_by_title"] += t is not None
        basename = t["data"]["basename"] if t is not None else ""
        basename = basename if BASENAME_RE.match(basename) else ""
        steps: dict[tuple[str, str], int] = {}
        tachi_chart_ids: dict[tuple[str, str], str] = {}
        if t is not None:
            for style in STYLES:
                for dc in DIFF_CLASSES:
                    row = tcharts.get(style, {}).get((t["id"], dc))
                    if row is not None:
                        tachi_chart_ids[(style, dc)] = row["id"]
                        if row["data"].get("stepCount") is not None:
                            steps[(style, dc)] = int(row["data"]["stepCount"])

        cands = list(by_sid.get(basename, [])) if basename else []
        how = "song_id" if cands else ""
        if not cands:
            keys = {norm(s["name"]), norm(s.get("name_translation")), norm(s.get("search_hint"))}
            if t is not None:
                keys |= {norm(t["title"])} | {norm(a) for a in t.get("altTitles", [])}
            found = {f["path"]: f for k in keys if k for f in by_title.get(k, [])}
            # Same title, different song: the file names another catalog song's id, or its artist differs and its
            # step counts do not agree with this song's.
            cands = [f for f in found.values()
                     if not (f["song_id"] and f["song_id"] != basename and f["song_id"] in claimed)
                     and (_artist_ok(f["artist"], s) or _score(f, s, steps)[0] >= max(2, len(steps) // 2))]
            how = "title" if cands else ""
        best = max(cands, key=lambda f: _score(f, s, steps)) if cands else None
        if len(cands) > 1:
            report["ambiguous"].append(f"{s['name']}: {best['path']} of {len(cands)}")  # type: ignore[index]
        if best is not None:
            report["simfile_by_song_id" if how == "song_id" else "simfile_by_title"] += 1
        else:
            report["no_simfile"].append(s["name"])

        song_id = basename or (best["song_id"] if best and BASENAME_RE.match(best["song_id"] or "") else "")
        if not song_id or song_id in used_ids:
            song_id = fallback_id(s["id"])
            report["fallback_ids"] += 1
        used_ids.add(song_id)
        songs.append(_record(s, song_id, t, best, steps, tachi_chart_ids))
    report["no_simfile_count"] = len(report["no_simfile"])
    report["charts_with_simfile"] = sum(1 for r in songs for c in r.charts.values() if c.source.get("simfile"))
    return songs, report


def _record(s: dict[str, Any], song_id: str, t: dict[str, Any] | None, best: dict[str, Any] | None,
            steps: dict[tuple[str, str], int], tachi_chart_ids: dict[tuple[str, str], str]) -> SongRecord:
    rec = SongRecord(
        game="ddr", song_id=song_id, title=s["name"], artist=s.get("artist", ""),
        title_romaji=s.get("name_translation", "") or (best["translit"] if best else ""),
        artist_romaji=s.get("artist_translation", ""), genre=s.get("folder", ""), bpm=str(s.get("bpm", "")),
        version=s.get("folder", ""),
        source={"ddrcarddraw": {"id": s["id"], "jacket": s.get("jacket", "")}},
        extra={k: v for k, v in {"song_hash": s["id"], "ingame_genre": s.get("genre", ""),
                                 "flags": s.get("flags", []), "remywiki": s.get("remyLink", ""),
                                 "search_hint": s.get("search_hint", "")}.items() if v},
    )
    if t is not None:
        rec.source["tachi"] = {"id": t["id"], "basename": t["data"]["basename"], "inGameID": t["data"]["inGameID"]}
        rec.extra["flare_category"] = t["data"].get("flareCategory", "")
    if best is not None:
        rec.source["simfile"] = {"path": best["path"], "url": simfile_url(best["path"]),
                                 "song_id": best["song_id"]}
    for c in s["charts"]:
        style, dc = c["style"], c["diffClass"]
        slot = slot_for(style, dc)
        cr = ChartRecord(slot=slot, native=native_for(style, dc), level=str(c["lvl"]),
                         source={"ddrcarddraw": {k: c[k] for k in ("maxScore", "sanbaiTier", "flags", "bpm")
                                                 if k in c}})
        if (style, dc) in steps:
            cr.note_counts = {"combo": steps[(style, dc)]}    # Tachi's stepCount is the max combo
        if (style, dc) in tachi_chart_ids:
            cr.source["tachi"] = {"id": tachi_chart_ids[(style, dc)]}
        sm_diff = next(k for k, v in SM_TO_DDR.items() if v == dc)
        row = best["charts"].get(f"{style}/{sm_diff}") if best else None
        if row is not None:
            cr.source["simfile"] = {"chart": f"{STYLES[style]}/{sm_diff}", "meter": row[0], "steps": row[1],
                                    "freezes": row[2], "shocks": row[4], "combo": combo_of(row[1], row[3], row[4])}
        rec.charts[slot] = cr
    return rec


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    ddrcd = json.loads(fetcher.get(DDRCD_URL))
    tachi_songs = json.loads(fetcher.get(TACHI_SONGS))
    tachi_charts = {style: json.loads(fetcher.get(url)) for style, url in TACHI_CHARTS.items()}
    manifest = load_manifest()
    songs, report = build_catalog(ddrcd, tachi_songs, tachi_charts, manifest)
    report["no_simfile"] = report["no_simfile"][:40]
    report["ambiguous"] = report["ambiguous"][:20]
    report["manifest_files"] = len(manifest["files"])
    return songs, report


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


def find_chart(sim: Simfile, style: str, diff_class: str) -> tuple[SmChart | None, int]:
    """The simfile chart for a DDR style and difficulty, and how many charts claimed that place."""
    found = [c for c in sim.charts if c.steps_type.lower() == STYLES[style] and SM_TO_DDR.get(c.difficulty_name)
             == diff_class]
    return (found[0] if found else None), len(found)


def ddr_counts(chart: Chart) -> dict[str, int]:
    """DDR's counts: steps (a jump is one step), jumps, freeze arrows, shock arrow rows, max combo and EX maximum."""
    c = count_steps(chart)
    combo = combo_of(c["steps"], c["hold_rows"], c["mine_rows"])
    return {"steps": c["steps"], "jumps": c["jumps"], "freezes": c["holds"] + c["rolls"], "shocks": c["mine_rows"],
            "combo": combo, "ex_max": 3 * combo}


def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    return ChartMeta(
        game="ddr", song_id=song.song_id, title=song.title, artist=song.artist, title_romaji=song.title_romaji,
        artist_romaji=song.artist_romaji, genre=song.genre, difficulty=slot, difficulty_native=cr.native,
        level=cr.level, designer=cr.designer, bpm_display=song.bpm, source=json.loads(json.dumps(cr.source)),
        note_counts=dict(cr.note_counts),
    )


def chart_from_simfile(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    ref = song.source.get("simfile")
    if not ref or slot not in song.charts:
        return None
    style, dc = style_and_class(slot)
    sim = load_simfile(fetcher, ref["url"])
    sm_chart, claims = find_chart(sim, style, dc)
    if sm_chart is None:
        return None
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "ddr-dan-tool-simfile", "url": ref["url"]})
    chart = chart_to_ir(sim, sm_chart, meta)
    if claims > 1:
        chart.warnings.append(f"{claims} {STYLES[style]} {sm_chart.difficulty} charts in the simfile; the first used")
    counts = ddr_counts(chart)
    chart.meta.source["ddr_counts"] = counts
    cr = song.charts[slot]
    published = cr.note_counts.get("combo")
    if published is not None and published != counts["combo"]:
        chart.warnings.append(f"combo {counts['combo']} in the simfile (steps + freeze rows + shock rows), Tachi's "
                              f"stepCount is {published}")
    max_score = cr.source.get("ddrcarddraw", {}).get("maxScore")
    if max_score is not None and max_score != counts["ex_max"]:
        chart.warnings.append(f"EX max {counts['ex_max']} from the simfile (3 × combo), DDRCardDraw has {max_score}")
    if str(sm_chart.meter).strip() != cr.level:
        chart.warnings.append(f"simfile meter {sm_chart.meter} differs from the WORLD level {cr.level}")
    return chart


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    ref = song.source.get("simfile")
    if ref:
        url = jacket_url(ref["path"])
        try:
            return fetcher.get(url), "png", url
        except NotFound:
            pass
    name = song.source.get("ddrcarddraw", {}).get("jacket", "")
    if not name:
        return None
    url = ddrcd_jacket_url(name)
    return fetcher.get(url), name.rsplit(".", 1)[-1].lower(), url


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="ddr", catalog=_catalog, chart=chart_from_simfile, jacket=_jacket,
                        notes="catalog: DDRCardDraw ddr_world.json + Tachi seeds; charts: ddr-dan-tool simfiles "
                              "(manifest data/ddr_simfiles.json); jackets: simfile folder, else DDRCardDraw"))


_register()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m vsrgconv.sources.ddr")
    p.add_argument("command", choices=["build-manifest"])
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--no-code-search", action="store_true", help="only song-ids.json (no `gh` needed)")
    p.add_argument("--out", default=str(MANIFEST))
    a = p.parse_args(argv)
    m = build_manifest(Fetcher(a.cache, min_interval=0.02), use_code_search=not a.no_code_search, progress=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
    # one file per line, so a rebuilt manifest diffs cleanly
    text = text.replace(',{"path":', ',\n{"path":').replace('"files":[{', '"files":[\n{')
    Path(a.out).write_text(text + "\n", "utf-8")
    print(json.dumps({k: v for k, v in m.items() if k != "files"} | {"files": len(m["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
