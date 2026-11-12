"""beatmania IIDX sources: catalog only. IIDX has no song jackets, and the chart source (textage) refuses this machine.

- IIDX-Data-Table (GitHub chinimuruhi/IIDX-Data-Table, branch gh-pages, `textage/*.json`): textage's song list as five
  dicts keyed by the same id: title, artist, genre, version, BPM, the textage tag, whether the song is in the current
  arcade version (`in_ac`) and INFINITAS (`in_inf`), and per chart the SP/DP levels and note counts for
  BEGINNER/NORMAL/HYPER/ANOTHER/LEGGENDARIA. The ids are textage's, not the game's.
- Tachi seeds (`songs-iidx.json`, `charts-iidx-sp.json`, `charts-iidx-dp.json`): the game's own song ids
  (`inGameID`), note counts and versions, to cross-check textage and fill its gaps.

Textage keeps several entries for one song when its charts changed between versions (and for some corrected tags);
they are grouped into one song by title and artist, and the entry that is in the arcade (else INFINITAS, else the
newest) gives the charts. Details and the textage URL format: docs/sources/iidx.md.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from typing import Any, Iterable

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name

GH = "https://raw.githubusercontent.com/"
TEXTAGE_DATA = GH + "chinimuruhi/IIDX-Data-Table/gh-pages/textage/"
TEXTAGE_FILES = ["song-info", "chart-info", "title", "textage-tag", "version"]
TACHI = GH + "zkldi/Tachi/HEAD/db/seeds/"
TACHI_FILES = {"songs": "songs-iidx.json", "sp": "charts-iidx-sp.json", "dp": "charts-iidx-dp.json"}
TEXTAGE_SCORE = "https://textage.cc/score/{folder}/{tag}.html?{side}{diff}{level}00"

CURRENT_VERSION = 34                       # 34 ZINRAI; Tachi's version key "34"
DIFFS = ["b", "n", "h", "a", "l"]          # textage's level/notes arrays: BEGINNER NORMAL HYPER ANOTHER LEGGENDARIA
TEXTAGE_DIFF = {"b": "B", "n": "N", "h": "H", "a": "A", "l": "X"}
TACHI_DIFF = {"NORMAL": "n", "HYPER": "h", "ANOTHER": "a", "LEGGENDARIA": "l"}   # Kiraku/Kichiku/All Scratch skipped
BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
INFINITAS_IDS = 80000                      # Tachi: in-game ids from 80000 up are INFINITAS ids


# ------------------------------------------------------------------------------------------------ versions

def version_folder(v: int, names: list[str], in_inf: bool = False) -> str:
    """Folder (stored as the song's genre) for IIDX-Data-Table's version index: '1st style', 'substream',
    '2nd style' .. '10th style', then '11 IIDX RED' .. '34 ZINRAI'. Index -1 (never in an arcade version) is
    'INFINITAS' for songs INFINITAS has, else 'CS'."""
    if v < 0:
        return "INFINITAS" if in_inf else "CS"
    name = names[v] if v < len(names) else f"version {v}"
    return name if v <= 10 else f"{v} {name}"


def textage_folder(v: int) -> str:
    """textage's score folder: its own version number. IIDX-Data-Table renumbered textage's versions (textage: -1
    substream, 0 consumer only, 1 1st style), so: 1 -> 's', 0 -> '1', -1 -> '0', n -> 'n'."""
    return {1: "s", 0: "1", -1: "0"}.get(v, str(v))


def textage_url(version: int, tag: str, style: str, diff: str, level: int) -> str:
    """`https://textage.cc/score/<folder>/<tag>.html?<side><diff><level>00`: side 1 (SP, 1P side) or D (DP);
    diff B/N/H/A/X (X = LEGGENDARIA); level in base 36 (A = 10, B = 11, C = 12); 00 = no options."""
    return TEXTAGE_SCORE.format(folder=textage_folder(version), tag=tag, side="1" if style == "sp" else "D",
                                diff=TEXTAGE_DIFF[diff], level=BASE36[level] if 0 < level < 36 else str(level))


def tachi_version(display: str) -> int:
    """Tachi's displayVersion -> IIDX-Data-Table's version index ('1' 1st style = 0, 'sub' = 1, 'inf...' = -1)."""
    if display == "sub":
        return 1
    if display.isdigit():
        return 0 if display == "1" else int(display)
    return -1


# ------------------------------------------------------------------------------------------------ matching

_CONFUSABLE = str.maketrans({"♥": "♡", "★": "☆", "ә": "ə", "ø": "0", "o": "0", "χ": "x", "ꓘ": "k", "ʞ": "k",
                             "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "р": "p", "с": "c",
                             "т": "t", "х": "x", "я": "r", "и": "n", "〜": "~"})


def norm(text: str) -> str:
    """Exact-ish key: NFKC, lower case, no spaces or punctuation."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\s・'\"’”“`~～\-‐―:：!！?？.,()（）\[\]【】]", "", t)


def skeleton(text: str) -> str:
    """Looser key for spelling variants (♥/♡, Cyrillic or Greek look-alikes, Ø/0, accents): letters and digits only."""
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).translate(_CONFUSABLE)
    return "".join(c for c in t if c.isalnum() or c in "♡☆")


# textage title -> the game's (Tachi's) title, where no normalisation can pair them. Also used to group textage
# entries: 月下散刃 (3424, an early entry without BPM or note counts) and 月下散刄 (103424) are one song.
TITLE_ALIASES = {"SANA MOLLETTE NE ENTE": "サナ・モレッテ・ネ・エンテ",
                 "SANA MOLLETTE NE ENTE(B.L.T.STYLE)": "サナ・モレッテ・ネ・エンテ(B.L.T.STYLE)",
                 "月下散刃": "月下散刄"}


def match_tachi(groups: dict[tuple[str, str], list[dict[str, Any]]],
                tachi_songs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """textage song group -> Tachi song. Three passes, each only taking unclaimed Tachi songs: exact title (artist
    breaks ties), then skeleton title (+ artist), then same artist with one title a prefix of the other or close
    (difflib ratio >= 0.8): Tachi sometimes drops a subtitle textage keeps."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    used: set[str] = set()

    def title(entries: list[dict[str, Any]]) -> str:
        return TITLE_ALIASES.get(entries[0]["title"], entries[0]["title"])

    def run(key_fn, artist_fn) -> None:
        index: dict[str, list[dict[str, Any]]] = {}
        for s in tachi_songs:
            if s["id"] not in used:
                index.setdefault(key_fn(s["title"]), []).append(s)
        for gk, entries in groups.items():
            if gk in out:
                continue
            cands = [s for s in index.get(key_fn(title(entries)), []) if s["id"] not in used]
            if len(cands) > 1:
                cands = [s for s in cands if artist_fn(s["artist"]) == artist_fn(entries[0]["artist"])] or cands
            if len(cands) == 1:
                out[gk] = cands[0]
                used.add(cands[0]["id"])

    run(norm, norm)
    run(skeleton, skeleton)
    by_artist: dict[str, list[dict[str, Any]]] = {}
    for s in tachi_songs:
        if s["id"] not in used:
            by_artist.setdefault(skeleton(s["artist"]), []).append(s)
    for gk, entries in groups.items():
        if gk in out:
            continue
        t = skeleton(title(entries))
        cands = [s for s in by_artist.get(skeleton(entries[0]["artist"]), []) if s["id"] not in used
                 and t and (skeleton(s["title"]).startswith(t) or t.startswith(skeleton(s["title"]))
                            or difflib.SequenceMatcher(None, t, skeleton(s["title"])).ratio() >= 0.8)]
        if len(cands) == 1:
            out[gk] = cands[0]
            used.add(cands[0]["id"])
    return out


# ------------------------------------------------------------------------------------------------ records

def textage_entries(files: dict[str, Any]) -> list[dict[str, Any]]:
    """IIDX-Data-Table's five dicts -> one dict per textage entry."""
    si, ci, ti, tg = (files[k] for k in ("song-info", "chart-info", "title", "textage-tag"))
    out = []
    for k in sorted(ti, key=lambda x: int(x) if x.isdigit() else 0):
        s, c = si.get(k, {}), ci.get(k, {})
        out.append({"id": k, "title": ti[k], "tag": tg.get(k, ""), "artist": s.get("artist", ""),
                    "genre": s.get("genre", ""), "version": int(s.get("version", -1)),
                    "in_ac": bool(c.get("in_ac")), "in_inf": bool(c.get("in_inf")), "bpm": c.get("bpm"),
                    "level": c.get("level", {}), "notes": c.get("notes", {})})
    return out


def _priority(e: dict[str, Any]) -> tuple:
    """Which textage entry of a song gives its charts: in the arcade, in INFINITAS, newest version, most charts, then
    the newest tag (IIDX-Data-Table gives a re-tagged entry its old id + 100000)."""
    charts = sum(1 for s in ("sp", "dp") for x in (e["level"].get(s) or []) if x)
    return (e["in_ac"], e["in_inf"], e["version"], charts, int(e["id"]) if e["id"].isdigit() else 0)


def _bpm(v: Any) -> str:
    if isinstance(v, list):
        return "-".join(str(x) for x in v)
    return "" if v in (None, "") else str(v)


def _tachi_ids(charts: Iterable[dict[str, Any]]) -> tuple[list[int], list[int]]:
    """(every in-game id, the arcade ids of charts in the current version) of a Tachi song's charts."""
    every: set[int] = set()
    current: set[int] = set()
    for c in charts:
        ids = c.get("data", {}).get("inGameID")
        ids = ids if isinstance(ids, list) else [ids] if ids is not None else []
        every.update(int(i) for i in ids)
        if str(CURRENT_VERSION) in c.get("versions", []):
            current.update(int(i) for i in ids if int(i) < INFINITAS_IDS)
    return sorted(every), sorted(current)


def game_song_id(every: list[int], current: list[int]) -> str | None:
    """The game's id: the arcade id the current version uses, else the oldest arcade id, else the INFINITAS id.
    Arcade ids are written with 5 digits, as the game's own files are (01000 = 5.1.1.)."""
    arcade = [i for i in every if i < INFINITAS_IDS]
    if current:
        return f"{current[0]:05d}"
    if arcade:
        return f"{arcade[0]:05d}"
    return str(every[0]) if every else None


def build_catalog(files: dict[str, Any], tachi_songs: list[dict[str, Any]],
                  tachi_charts: dict[str, list[dict[str, Any]]]) -> tuple[list[SongRecord], dict[str, Any]]:
    names = list(files.get("version") or [])
    entries = textage_entries(files)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault((norm(TITLE_ALIASES.get(e["title"], e["title"])), norm(e["artist"])), []).append(e)
    for es in groups.values():
        es.sort(key=_priority, reverse=True)
    matched = match_tachi(groups, tachi_songs)
    charts_by_song: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    all_by_song: dict[str, list[dict[str, Any]]] = {}
    for style, rows in tachi_charts.items():
        for c in rows:
            all_by_song.setdefault(c["songID"], []).append(c)
            d = TACHI_DIFF.get(c.get("difficulty", ""))
            if d and c.get("isPrimary", True):
                charts_by_song.setdefault(c["songID"], {})[(style, d)] = c
    report: dict[str, Any] = {"textage_entries": len(entries), "textage_songs": len(groups),
                              "textage_in_ac_entries": sum(e["in_ac"] for e in entries),
                              "tachi_songs": len(tachi_songs), "matched_to_tachi": len(matched),
                              "textage_only": [], "tachi_only": [], "note_mismatches": [], "level_mismatches": [],
                              "charts_from_tachi": [], "id_collisions": []}
    songs: list[SongRecord] = []
    used_ids: set[str] = set()

    def claim(sid: str | None, fallback: str) -> str:
        if sid is None or sid in used_ids:
            if sid is not None:
                report["id_collisions"].append(f"{sid} -> {fallback}")
            sid = fallback
        used_ids.add(sid)
        return sid

    for gk, es in groups.items():
        p = es[0]
        t = matched.get(gk)
        every, current = _tachi_ids(all_by_song.get(t["id"], [])) if t else ([], [])
        sid = claim(game_song_id(every, current), f"tx-{p['tag']}")
        if t is None:
            report["textage_only"].append(f"{sid} {p['title']}")
        rec = textage_record(sid, p, es[1:], names)
        if t is not None:
            rec.source["tachi"] = {"id": t["id"]}
            rec.extra["ingame_ids"] = every
            _cross_check(rec, charts_by_song.get(t["id"], {}), report)
        songs.append(rec)
    claimed = {t["id"] for t in matched.values()}
    for t in tachi_songs:
        if t["id"] in claimed or t["id"] not in all_by_song:
            continue
        every, current = _tachi_ids(all_by_song[t["id"]])
        rec = tachi_record(claim(game_song_id(every, current), f"tachi-{t['id']}"), t, charts_by_song.get(t["id"], {}),
                           names, every)
        report["tachi_only"].append(f"{rec.song_id} {rec.title}")
        songs.append(rec)
    songs.sort(key=lambda s: s.song_id)
    report["songs"] = len(songs)
    report["in_arcade"] = sum(not s.extra.get("not_in_arcade") for s in songs)
    report["removed_from_arcade"] = sum(bool(s.extra.get("removed")) for s in songs)
    report["never_in_arcade"] = sum(bool(s.extra.get("not_in_arcade")) and not s.extra.get("removed") for s in songs)
    for k in ("textage_only", "tachi_only", "note_mismatches", "level_mismatches", "charts_from_tachi",
              "id_collisions"):
        report[f"{k}_count"] = len(report[k])
        report[k] = report[k][:40]
    return songs, report


def textage_record(song_id: str, p: dict[str, Any], others: list[dict[str, Any]], names: list[str]) -> SongRecord:
    """A song from its main textage entry `p`; `others` are its other textage entries (older chart sets)."""
    in_inf = p["in_inf"] or any(o["in_inf"] for o in others)
    arcade = [e["version"] for e in [p, *others] if e["version"] >= 0]
    first = min(arcade) if arcade else -1       # the song's first arcade version, over all its textage entries
    folder = version_folder(first, names, in_inf)
    rec = SongRecord(
        game="iidx", song_id=song_id, title=p["title"], artist=p["artist"], genre=folder, bpm=_bpm(p["bpm"]),
        version=folder,
        source={"textage": {"id": p["id"], "tag": p["tag"], "version": p["version"]}},
        extra={"genre": p["genre"], "version_number": first, "in_arcade": p["in_ac"], "in_infinitas": in_inf},
    )
    if not p["in_ac"]:
        rec.extra["not_in_arcade"] = True
        if arcade:
            rec.extra["removed"] = True          # was in an arcade version once
    if others:
        rec.extra["textage_variants"] = [{"tag": o["tag"], "id": o["id"], "version": o["version"],
                                          "in_ac": o["in_ac"], "in_inf": o["in_inf"], "level": o["level"]}
                                         for o in others]
    for style in ("sp", "dp"):
        levels = p["level"].get(style) or []
        notes = p["notes"].get(style) or []
        for i, d in enumerate(DIFFS):
            lv = int(levels[i]) if i < len(levels) and levels[i] else 0
            if lv <= 0:
                continue
            key = f"{style}{d}"
            n = int(notes[i]) if i < len(notes) and notes[i] else 0
            rec.charts[canonical_difficulty("iidx", key)] = ChartRecord(
                slot=canonical_difficulty("iidx", key), native=native_name("iidx", key), level=str(lv),
                note_counts={"notes": n} if n > 0 else {},
                source={"textage": textage_url(p["version"], p["tag"], style, d, lv)})
    return rec


def _cross_check(rec: SongRecord, tachi: dict[tuple[str, str], dict[str, Any]], report: dict[str, Any]) -> None:
    """Compare textage's levels and note counts with Tachi's; fill missing counts and charts textage lacks."""
    for (style, d), c in tachi.items():
        key = f"{style}{d}"
        slot = canonical_difficulty("iidx", key)
        n = c.get("data", {}).get("notecount")
        cr = rec.charts.get(slot)
        if cr is None:
            if rec.extra.get("in_arcade") and str(CURRENT_VERSION) not in c.get("versions", []):
                continue                        # an old chart the arcade no longer has
            cr = rec.charts[slot] = ChartRecord(slot=slot, native=native_name("iidx", key), level=str(c.get("level")),
                                                note_counts={"notes": n} if n else {}, notes="from Tachi only")
            report["charts_from_tachi"].append(f"{rec.song_id}/{slot} {rec.title}")
        elif n and not cr.note_counts:
            cr.note_counts = {"notes": int(n)}
        elif n and cr.note_counts.get("notes") != n:
            report["note_mismatches"].append(f"{rec.song_id}/{slot} textage {cr.note_counts.get('notes')} tachi {n}")
        if str(c.get("level")) != cr.level:
            report["level_mismatches"].append(f"{rec.song_id}/{slot} textage {cr.level} tachi {c.get('level')}")
        cr.source["tachi"] = {"id": c["id"], "versions": c.get("versions", [])}
        if rec.extra.get("in_arcade") and str(CURRENT_VERSION) not in c.get("versions", []):
            # textage lists every platform's charts: CS / INFINITAS / omnimix-only charts of an arcade song
            cr.notes = "not in the arcade version (Tachi: CS, INFINITAS or omnimix only)"
            rec.extra.setdefault("charts_not_in_arcade", []).append(slot)
            report.setdefault("charts_not_in_arcade", 0)
            report["charts_not_in_arcade"] += 1
    _order(rec)


def _order(rec: SongRecord) -> None:
    """SP slots first, then DP, each in ladder order (db.py sorts the ladder slots; double-* keep this order)."""
    order = [canonical_difficulty("iidx", f"{s}{d}") for s in ("sp", "dp") for d in DIFFS]
    rec.charts = {k: rec.charts[k] for k in sorted(rec.charts, key=lambda k: order.index(k) if k in order else 99)}


def tachi_record(song_id: str, t: dict[str, Any], charts: dict[tuple[str, str], dict[str, Any]], names: list[str],
                 every: list[int]) -> SongRecord:
    """A song only Tachi has (textage lists it under a title the matcher could not pair)."""
    display = t.get("data", {}).get("displayVersion", "")
    v = tachi_version(display)
    current = any(str(CURRENT_VERSION) in c.get("versions", []) for c in charts.values())
    in_inf = any(x.startswith("inf") for c in charts.values() for x in c.get("versions", []))
    folder = version_folder(v, names, in_inf)
    rec = SongRecord(game="iidx", song_id=song_id, title=t.get("title", ""), artist=t.get("artist", ""), genre=folder,
                     version=folder, source={"tachi": {"id": t["id"]}},
                     extra={"genre": t.get("data", {}).get("genre", ""), "version_number": v, "in_arcade": current,
                            "in_infinitas": in_inf, "ingame_ids": every})
    if not current:
        rec.extra["not_in_arcade"] = True
        if v >= 0:
            rec.extra["removed"] = True
    for (style, d), c in charts.items():
        key = f"{style}{d}"
        n = c.get("data", {}).get("notecount")
        rec.charts[canonical_difficulty("iidx", key)] = ChartRecord(
            slot=canonical_difficulty("iidx", key), native=native_name("iidx", key), level=str(c.get("level", "")),
            note_counts={"notes": int(n)} if n else {}, source={"tachi": {"id": c["id"],
                                                                          "versions": c.get("versions", [])}})
    _order(rec)
    return rec


# ------------------------------------------------------------------------------------------------ registry

def _catalog(fetcher: Any) -> tuple[list[SongRecord], dict[str, Any]]:
    files = {k: json.loads(fetcher.get(TEXTAGE_DATA + k + ".json")) for k in TEXTAGE_FILES}
    tachi = {k: json.loads(fetcher.get(TACHI + v)) for k, v in TACHI_FILES.items()}
    return build_catalog(files, tachi["songs"], {"sp": tachi["sp"], "dp": tachi["dp"]})


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="iidx", catalog=_catalog, chart=None, jacket=None,
                        notes="catalog: IIDX-Data-Table (textage) + Tachi in-game ids; no jackets (the game has "
                              "none); charts: textage only (unreachable here), URL in source.textage"))


_register()
