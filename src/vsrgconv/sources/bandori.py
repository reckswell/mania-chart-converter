"""BanG Dream! Girls Band Party! (JP) sources. Details, coverage and URL schemes: docs/sources/bandori.md.

- Catalog: the game's own master data, mirrored by esterTion/bangdream_master_db_diff (`masterMusicList`,
  `masterMusicDifficultyList`, `masterBandMap`, `masterMusicJacketMap`). song_id = musicId, genre = band name.
  BPM and length come from Greatdori's offline copy of Bestdori's song list (`api/songs/all.7.json`, up to song 742),
  else from the song's first mirrored chart.
- Charts (Bestdori JSON, parsed by `formats/bestdori.py`):
  1. MaaBanGDream's mirror of Bestdori (`resource/charts/bestdori/<id>/<hard|expert|special>.json`, listed in its
     `manifest.json`): exact, HARD/EXPERT/SPECIAL only.
  2. bestdori.com's own API (`api/charts/<id>/<difficulty>.json`): exact, every difficulty. The cloud machine cannot
     reach it, so this only works on the PC (or from files dropped into the cache); after one connection failure the
     run stops asking.
  3. kanon511/BangDreamDifficultyPrediction (`charts/<id>-<index>_<level>.json`): every difficulty for songs up to
     KANON_MAX_ID, but re-serialised by the `bestdori-api` package, which lost detail (see KANON_LOSS). In practice
     this is where EASY/NORMAL come from on the cloud machine.
- Jackets: the mirror's jacket PNGs (400 px, mostly CN-server art; a JP one is preferred when listed), else
  Bestdori's JP jacket (PC only).

The master's `notesQuantity` is not the combo: it is the placeholder 1000 on 3,281 of 3,441 rows and a stale
launch-era number on the rest. The published count checked against is Bestdori's (`note_counts.combo`), which is
computed from the chart; `notesQuantity` is kept in the chart record's source and compared when it is not 1000.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, game, native_name
from ..model import Chart, ChartMeta
from .fetch import Fetcher, FetchError, NotFound

RAW = "https://raw.githubusercontent.com/"
MASTER = RAW + "esterTion/bangdream_master_db_diff/HEAD/"
MASTER_FILES = {"music": "masterMusicList.json", "difficulty": "masterMusicDifficultyList.json",
                "band": "masterBandMap.json", "jacket": "masterMusicJacketMap.json"}
GREATDORI_SONGS = RAW + "Chorus-ACE/Greatdori-OfflineResBundle/HEAD/api/songs/all.7.json"
MAA = RAW + "coatcn1/MaaBanGDream/HEAD/resource/charts/"
MAA_MANIFEST = MAA + "manifest.json"
KANON = RAW + "kanon511/BangDreamDifficultyPrediction/HEAD/charts/{id}-{index}_{level}.json"
# Bestdori itself: exact, every difficulty, but not reachable from the cloud machine (works on the PC).
BESTDORI_CHART = "https://bestdori.com/api/charts/{id}/{diff}.json"
BESTDORI_JACKET = ("https://bestdori.com/assets/jp/musicjacket/musicjacket{bucket}_rip/"
                   "assets-star-forassetbundle-startapp-musicjacket-musicjacket{bucket}-{name}-jacket.png")
_unreachable: set[str] = set()   # hosts that failed to connect in this run: not asked again
KANON_MAX_ID = 704            # the repository's last song (checked 2026-11-11); no probing above it
KANON_LOSS = ("lossy copy (kanon511, bestdori-api re-serialised): Long and Slide are both written as Slide, "
              "System events and charge flags are dropped")
DIFFICULTIES = ["easy", "normal", "hard", "expert", "special"]      # Bestdori's difficulty index 0..4
MAA_DIFFICULTIES = ("hard", "expert", "special")
NOTES_PLACEHOLDER = 1000
# The game's font draws these as badges; Bestdori writes them out.
TITLE_BADGES = {"甅": "[FULL] ", "躄": "[原曲] "}
JST = timezone(timedelta(hours=9))


def _json(fetcher: Fetcher, url: str) -> Any:
    return json.loads(fetcher.get(url).decode("utf-8-sig"))


def _optional(fetcher: Fetcher, url: str) -> bytes | None:
    """A file from a host that may be unreachable (bestdori.com from the cloud): None if missing or unreachable.
    The first connection failure marks the host, so a long scrape does not ask it again; cached files still load."""
    host = urllib.parse.urlsplit(url).netloc
    if host in _unreachable and not fetcher.cache_path(url).exists():
        return None
    try:
        return fetcher.get(url)
    except NotFound:
        return None
    except FetchError:
        _unreachable.add(host)
        return None


def _date(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, JST).date().isoformat() if ms else ""


def clean_title(raw: str) -> str:
    for glyph, badge in TITLE_BADGES.items():
        if raw.startswith(glyph):
            return badge + raw[len(glyph):]
    return raw


def bpm_display(bpms: list[float]) -> str:
    values = sorted({float(b) for b in bpms if b})
    if not values:
        return ""
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{values[0]:g}-{values[-1]:g}"


def _greatdori_bpm(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    per_diff = entry.get("bpm") or {}
    for key in ("3", "2", "4", "1", "0"):
        if per_diff.get(key):
            return bpm_display([seg.get("bpm") for seg in per_diff[key]])
    return ""


def _pick_jacket(maa_song: dict[str, Any] | None, jp_name: str) -> dict[str, Any] | None:
    """The mirror's jacket for a song: a JP one first, then one with the JP jacket's name, then the first listed."""
    jackets = (maa_song or {}).get("jackets") or []
    if not jackets:
        return None
    for test in (lambda j: j.get("server") == "jp" and j.get("name") == jp_name,
                 lambda j: j.get("server") == "jp",
                 lambda j: j.get("name") == jp_name,
                 lambda j: True):
        for j in jackets:
            if test(j) and j.get("path"):
                return {"path": j["path"], "server": j.get("server", ""), "name": j.get("name", "")}
    return None


def song_record(row: dict[str, Any], diffs: list[dict[str, Any]], bands: dict[str, Any], jackets: list[dict[str, Any]],
                greatdori: dict[str, Any] | None, maa_song: dict[str, Any] | None, now_ms: int) -> SongRecord:
    """One SongRecord from the master rows of a song, plus what Greatdori (BPM, Bestdori counts) and the mirror
    (chart paths, jacket) know about it."""
    mid = int(row["musicId"])
    band = (bands.get(str(row.get("bandId"))) or {}).get("bandName", "")
    title = clean_title(row.get("musicTitle", ""))
    jp_jacket = row.get("jacketImage", "")
    current = [j for j in jackets if (j.get("startAt") or 0) <= now_ms]
    if current:
        jp_jacket = max(current, key=lambda j: (j.get("startAt") or 0, j.get("seq") or 0)).get("jacketImage", jp_jacket)
    extra: dict[str, Any] = {k: row.get(k, "") for k in ("lyricist", "composer", "arranger", "tag")}
    extra.update({"published_at": row.get("publishedAt"), "closed_at": row.get("closedAt"),
                  "music_data_type": row.get("musicDataType", ""), "ruby": row.get("ruby", "")})
    if title != row.get("musicTitle", ""):
        extra["title_raw"] = row.get("musicTitle", "")
    if row.get("closedAt") and row["closedAt"] < now_ms:
        extra["removed"] = True
        extra["closed_date"] = _date(row["closedAt"])
    if row.get("publishedAt") and row["publishedAt"] > now_ms:
        extra["unreleased"] = True
    if greatdori:
        titles = greatdori.get("musicTitle") or []
        if len(titles) > 1 and titles[1] and titles[1] != title:
            extra["title_en"] = titles[1]
        if greatdori.get("length"):
            extra["length_s"] = greatdori["length"]
    source: dict[str, Any] = {"master": {"musicId": mid, "bandId": row.get("bandId"), "jacketImage": jp_jacket,
                                         "bgmId": row.get("bgmId", "")}}
    jacket = _pick_jacket(maa_song, jp_jacket)
    if maa_song:
        source["maa"] = {"bestdori_song_id": maa_song.get("bestdori_song_id"), **({"jacket": jacket} if jacket else {})}
    rec = SongRecord(game="bandori", song_id=str(mid), title=title, artist=band, genre=band,
                     bpm=_greatdori_bpm(greatdori), date_added=_date(row.get("publishedAt")), source=source,
                     extra=extra)
    maa_diffs = (maa_song or {}).get("difficulties") or {}
    gd_diffs = (greatdori or {}).get("difficulty") or {}
    gd_notes = (greatdori or {}).get("notes") or {}
    for d in sorted(diffs, key=lambda d: DIFFICULTIES.index(d["difficulty"])):
        name = d["difficulty"]
        index = DIFFICULTIES.index(name)
        slot = canonical_difficulty("bandori", name)
        src: dict[str, Any] = {"notesQuantity": d.get("notesQuantity")}
        if d.get("publishedAt"):
            src["published_at"] = d["publishedAt"]
        if d.get("enableSpecialNotes"):
            src["special_notes"] = True          # the chart has Directional flicks or hidden slide points
        bestdori: dict[str, Any] = {}
        if (gd_diffs.get(str(index)) or {}).get("playLevel") is not None:
            bestdori["level"] = gd_diffs[str(index)]["playLevel"]
        if gd_notes.get(str(index)) is not None:
            bestdori["notes"] = gd_notes[str(index)]
        maa = maa_diffs.get(name)
        if maa:
            src["maa"] = {"path": maa["path"], "bestdori_url": maa.get("source_url", ""),
                          "raw_sha256": maa.get("raw_sha256", "")}
            if maa.get("expected_notes") is not None:
                bestdori["notes"] = maa["expected_notes"]
        if bestdori:
            src["bestdori"] = bestdori
        rec.charts[slot] = ChartRecord(
            slot=slot, native=native_name("bandori", name), level=str(d.get("playLevel", "")),
            note_counts={"combo": bestdori["notes"]} if "notes" in bestdori else {}, source=src)
        if name == "special" and d.get("publishedAt") and d["publishedAt"] > now_ms:
            rec.charts[slot].notes = "not released yet"
    return rec


def build_catalog(music: list[dict[str, Any]], difficulties: list[dict[str, Any]], bands: dict[str, Any],
                  jackets: dict[str, Any], greatdori: dict[str, Any], manifest: dict[str, Any],
                  now_ms: int) -> tuple[list[SongRecord], dict[str, Any]]:
    by_song: dict[int, list[dict[str, Any]]] = {}
    for d in difficulties:
        if d.get("difficulty") in DIFFICULTIES:
            by_song.setdefault(int(d["musicId"]), []).append(d)
    maa = {int(s["bestdori_song_id"]): s for s in manifest.get("songs", [])}
    songs = [song_record(row, by_song.get(int(row["musicId"]), []), bands, jackets.get(str(row["musicId"])) or [],
                         greatdori.get(str(row["musicId"])), maa.get(int(row["musicId"])), now_ms)
             for row in music]
    ids = {int(r["musicId"]) for r in music}
    mapped = game("bandori").genres
    rows = [d for d in difficulties if int(d["musicId"]) in ids]
    report = {
        "master_songs": len(music), "removed_songs": sum(1 for s in songs if s.extra.get("removed")),
        "unreleased_songs": sum(1 for s in songs if s.extra.get("unreleased")),
        "difficulty_rows_without_song": sorted({int(d["musicId"]) for d in difficulties} - ids),
        "songs_in_mirror": sum(1 for s in songs if "maa" in s.source),
        "songs_not_in_mirror": [s.song_id for s in songs if "maa" not in s.source],
        "notes_quantity_placeholder": sum(1 for d in rows if d.get("notesQuantity") == NOTES_PLACEHOLDER),
        "unmapped_bands": sorted({s.genre for s in songs if s.genre not in mapped}),
    }
    return songs, report


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    master = {k: _json(fetcher, MASTER + f) for k, f in MASTER_FILES.items()}
    notes: list[str] = []
    greatdori: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    try:
        greatdori = _json(fetcher, GREATDORI_SONGS)
    except FetchError as e:
        notes.append(f"Greatdori song list unavailable ({e}); no BPM from it")
    try:
        manifest = _json(fetcher, MAA_MANIFEST)
    except FetchError as e:
        notes.append(f"MaaBanGDream manifest unavailable ({e}); no chart paths or jackets")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    songs, report = build_catalog(master["music"], master["difficulty"], master["band"], master["jacket"],
                                  greatdori, manifest, now_ms)
    from_chart, no_bpm = 0, []
    for song in songs:                       # songs newer than the Greatdori copy: BPM from a mirrored chart
        if song.bpm:
            continue
        ref = next((song.charts[s].source["maa"] for s in ("expert", "hard", "master")
                    if s in song.charts and "maa" in song.charts[s].source), None)
        if ref is None:
            no_bpm.append(song.song_id)
            continue
        try:
            entries = _json(fetcher, MAA + ref["path"]).get("chart", [])
        except FetchError:
            no_bpm.append(song.song_id)
            continue
        song.bpm = bpm_display([e.get("bpm") for e in entries if e.get("type") == "BPM"])
        from_chart += 1
    report.update({"bpm_from_chart": from_chart, "no_bpm": no_bpm, "note": "; ".join(notes)})
    return songs, report


# ------------------------------------------------------------------------------------------------ charts

def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    return ChartMeta(game="bandori", song_id=song.song_id, title=song.title, artist=song.artist,
                     title_romaji=song.title_romaji, artist_romaji=song.artist_romaji, genre=song.genre,
                     difficulty=slot, difficulty_native=cr.native, level=cr.level, designer=cr.designer,
                     bpm_display=song.bpm, note_counts=dict(cr.note_counts),
                     source={k: copy.deepcopy(v) for k, v in cr.source.items() if k != "extracted"})


def kanon_urls(song: SongRecord, slot: str) -> list[str]:
    """kanon511 file names carry the level at scrape time: try Bestdori's level, today's, then +-1, +-2."""
    cr = song.charts.get(slot)
    if cr is None or not song.song_id.isdigit() or int(song.song_id) > KANON_MAX_ID:
        return []
    index = DIFFICULTIES.index(cr.native.lower())
    levels: list[int] = []
    for lv in [cr.source.get("bestdori", {}).get("level")] + ([int(cr.level) + d for d in (0, -1, 1, -2, 2)]
                                                             if cr.level.isdigit() else []):
        if lv is not None and lv not in levels:
            levels.append(lv)
    return [KANON.format(id=int(song.song_id), index=index, level=lv) for lv in levels]


def chart_from_maa(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    from ..formats.bestdori import bestdori_to_chart

    ref = song.charts[slot].source.get("maa")
    if not ref:
        return None
    url = MAA + ref["path"]
    try:
        doc = _json(fetcher, url)
    except NotFound:
        return None
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "maabangdream-bestdori", "url": url,
                        "bestdori_url": (doc.get("source") or {}).get("url", ref.get("bestdori_url", ""))})
    return bestdori_to_chart(doc, meta)


def chart_from_bestdori(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    """Bestdori's own API (exact). Unreachable from the cloud; on the PC it fills what the mirrors lack."""
    from ..formats.bestdori import bestdori_to_chart

    if not song.song_id.isdigit():
        return None
    url = BESTDORI_CHART.format(id=int(song.song_id), diff=song.charts[slot].native.lower())
    data = _optional(fetcher, url)
    if data is None:
        return None
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "bestdori-api", "url": url})
    return bestdori_to_chart(data, meta)


def chart_from_kanon(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    from ..formats.bestdori import bestdori_to_chart

    for url in kanon_urls(song, slot):
        try:
            data = fetcher.get(url)
        except NotFound:
            continue
        meta = chart_meta(song, slot)
        meta.source.update({"extracted_from": "kanon511-bestdori-api", "url": url})
        return bestdori_to_chart(data, meta, lossy=KANON_LOSS)
    return None


def check_combo(chart: Chart) -> Chart:
    """Warn when the chart's combo differs from Bestdori's published count, or from the master's notesQuantity when
    that is a real number (not the 1000 placeholder)."""
    combo = chart.meta.source.get("combo")
    published = chart.meta.note_counts.get("combo")
    if published is not None and combo != published:
        chart.warnings.append(f"combo {combo} differs from Bestdori's count {published}")
    quantity = chart.meta.source.get("notesQuantity")
    if isinstance(quantity, int) and quantity != NOTES_PLACEHOLDER and combo != quantity:
        chart.warnings.append(f"master notesQuantity {quantity} differs from combo {combo} (a launch-era value; "
                              f"the game counts the chart)")
    return chart


def _chart(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    if slot not in song.charts:
        return None
    chart = (chart_from_maa(fetcher, song, slot) or chart_from_bestdori(fetcher, song, slot)
             or chart_from_kanon(fetcher, song, slot))
    return check_combo(chart) if chart is not None else None


def bestdori_jacket_url(song_id: int, name: str) -> str:
    """Bestdori's JP jacket: bundles hold ten songs each (musicjacket10 = songs 1-10, ...)."""
    return BESTDORI_JACKET.format(bucket=(song_id + 9) // 10 * 10, name=urllib.parse.quote(name))


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    ref = song.source.get("maa", {}).get("jacket")
    if ref:
        url = MAA + ref["path"]
        return fetcher.get(url), ref["path"].rsplit(".", 1)[-1], url
    name = song.source.get("master", {}).get("jacketImage", "")
    if not name or not song.song_id.isdigit():
        return None
    url = bestdori_jacket_url(int(song.song_id), name)
    data = _optional(fetcher, url)
    return (data, "png", url) if data is not None else None


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="bandori", catalog=_catalog, chart=_chart, jacket=_jacket,
                        notes="catalog: game master data (esterTion mirror) + Greatdori BPM; charts: MaaBanGDream "
                              "Bestdori mirror (HARD/EXPERT/SPECIAL), else bestdori.com (PC only), else kanon511 "
                              "(lossy; EASY/NORMAL); jackets: MaaBanGDream, else bestdori.com (PC only)"))


_register()
