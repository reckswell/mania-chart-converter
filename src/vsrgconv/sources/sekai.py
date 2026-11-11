"""Project SEKAI COLORFUL STAGE! (JP) sources. Everything is exact and on GitHub raw; docs/sources/sekai.md.

- Catalog: Sekai-World/sekai-master-db-diff (`musics`, `musicDifficulties`, `musicTags`, `musicArtists`,
  `musicVocals`, `gameCharacters`, `outsideCharacters`), joined on the music id. BPM from MoeSekai-Hub's
  `music_bpms.json` (the master data has none; the SUS files have the exact changes).
- Charts: the game's own SUS files, parsed by `formats/sus.py`. First patrickv0220/pjsekai-decrypted-data
  (`music_score/<id:04d>_01/<diff>.txt`), else ChiyukiYunokawa/Sekai-Chart-Json (`<id>/<diff>.sus`, older songs; may be
  an older revision, so the chart's meta.source["extracted_from"] says which was used).
- Jackets: pjsekai-decrypted-data `jacket/<assetbundleName>.png`, else Untitled-World/sekai-jacket-db.

The combo of every parsed chart is checked against the published totalNoteCount (warning when it differs).
"""
from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import ChartRecord, SongRecord
from ..games.registry import canonical_difficulty, native_name
from ..model import Chart, ChartMeta
from .fetch import Fetcher, NotFound

MASTER_DB = "https://raw.githubusercontent.com/Sekai-World/sekai-master-db-diff/main/{name}.json"
BPM_URL = "https://raw.githubusercontent.com/StarMoe-org/MoeSekai-Hub/HEAD/data/music_bpm/music_bpms.json"
DECRYPTED = "https://raw.githubusercontent.com/patrickv0220/pjsekai-decrypted-data/HEAD/pjsekai-decrypted-data-omg/JP/"
SUS_SOURCES = [
    ("pjsekai-decrypted-data", DECRYPTED + "music_score/{id:04d}_01/{diff}.txt"),
    ("sekai-chart-json", "https://raw.githubusercontent.com/ChiyukiYunokawa/Sekai-Chart-Json/HEAD/{id}/{diff}.sus"),
]
JACKET_SOURCES = [
    DECRYPTED + "jacket/{asset}.png",
    "https://raw.githubusercontent.com/Untitled-World/sekai-jacket-db/HEAD/src/jacket/{asset}.png",
]
DIFFICULTIES = ["easy", "normal", "hard", "expert", "master", "append"]

UNITS = {   # musicTags.musicTag -> unit display name (the database genre)
    "light_music_club": "Leo/need",
    "idol": "MORE MORE JUMP!",
    "street": "Vivid BAD SQUAD",
    "theme_park": "ワンダーランズ×ショウタイム",
    "school_refusal": "25時、ナイトコードで。",
}
VIRTUAL_SINGER, OTHER = "VIRTUAL SINGER", "その他"
JST = timezone(timedelta(hours=9))


def unit_genre(tags: set[str]) -> str:
    """One unit for the folder: the only unit tag; songs of several units or tagged "other" are その他;
    songs tagged only vocaloid are VIRTUAL SINGER."""
    units = [UNITS[t] for t in sorted(tags) if t in UNITS]
    if len(units) == 1:
        return units[0]
    if units or "other" in tags:
        return OTHER
    return VIRTUAL_SINGER if "vocaloid" in tags else OTHER


# ------------------------------------------------------------------------------------------------ catalog

def _load(fetcher: Fetcher, name: str) -> list[dict[str, Any]]:
    return json.loads(fetcher.get(MASTER_DB.format(name=name)))


def _bpms(fetcher: Fetcher) -> dict[int, dict[str, Any]]:
    from .fetch import FetchError

    try:
        data = json.loads(fetcher.get(BPM_URL))
    except FetchError:
        return {}
    return {int(s["music_id"]): s for s in data.get("songs", []) if "music_id" in s}


def _bpm_display(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    values = sorted({float(b) for b in row.get("bpms") or [row.get("bpm")] if b is not None})
    if not values:
        return ""
    main = float(row.get("bpm") or values[0])
    if len(values) == 1:
        return f"{values[0]:g}"
    return f"{values[0]:g}-{values[-1]:g} ({main:g})"


def _jst(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, JST).strftime("%Y-%m-%d") if ms else ""


def _singers(vocal: dict[str, Any], chars: dict[int, str], outside: dict[int, str]) -> list[str]:
    out = []
    for c in sorted(vocal.get("characters", []), key=lambda c: c.get("seq", 0)):
        table = chars if c.get("characterType") == "game_character" else outside
        out.append(table.get(c.get("characterId"), f"{c.get('characterType')}:{c.get('characterId')}"))
    return out


def sekai_record(music: dict[str, Any], diffs: list[dict[str, Any]], tags: set[str], artists: dict[int, dict],
                 vocals: list[dict[str, Any]], chars: dict[int, str], outside: dict[int, str],
                 bpm: dict[str, Any] | None, now_ms: float) -> SongRecord:
    mid = int(music["id"])
    creator = artists.get(music.get("creatorArtistId"), {})
    artist = creator.get("name") or music.get("composer", "")
    title = music.get("title", "")
    rec = SongRecord(
        game="sekai", song_id=str(mid), title=title, artist=artist,
        title_romaji=unicodedata.normalize("NFKC", title) if _is_latin(title)
        else romanize(music.get("pronunciation", "")),
        artist_romaji=unicodedata.normalize("NFKC", artist) if _is_latin(artist)
        else romanize(creator.get("pronunciation", "")),
        genre=unit_genre(tags), bpm=_bpm_display(bpm), date_added=_jst(music.get("publishedAt")),
        source={"sekai_master": {"music_id": mid, "assetbundle": music.get("assetbundleName", ""),
                                 "seq": music.get("seq")}},
    )
    extra: dict[str, Any] = {
        "pronunciation": music.get("pronunciation", ""),
        "lyricist": music.get("lyricist", ""), "composer": music.get("composer", ""),
        "arranger": music.get("arranger", ""),
        "published_at": music.get("publishedAt"), "released_at": music.get("releasedAt"),
        "tags": sorted(tags - {"all"}),
        "vocals": [{"type": v.get("musicVocalType", ""), "caption": v.get("caption", ""),
                    "singers": _singers(v, chars, outside), "asset": v.get("assetbundleName", "")}
                   for v in sorted(vocals, key=lambda v: (v.get("seq", 0), v.get("id", 0)))],
        "filler_sec": music.get("fillerSec"),
    }
    for k in ("categories", "isFullLength", "musicCollaborationId"):
        if music.get(k):
            extra[{"isFullLength": "full_length", "musicCollaborationId": "collaboration_id"}.get(k, k)] = music[k]
    if bpm:
        extra["bpm_main"] = bpm.get("bpm")
    if (music.get("publishedAt") or 0) > now_ms:
        extra["unreleased"] = True
    rec.extra = extra
    for d in sorted(diffs, key=lambda d: DIFFICULTIES.index(d["musicDifficulty"])
                    if d["musicDifficulty"] in DIFFICULTIES else 99):
        native = d["musicDifficulty"]
        slot = canonical_difficulty("sekai", native)
        rec.charts[slot] = ChartRecord(
            slot=slot, native=native_name("sekai", native), level=str(d.get("playLevel", "")),
            note_counts={"combo": int(d["totalNoteCount"])} if d.get("totalNoteCount") is not None else {},
            source={"music_difficulty_id": d.get("id"), "difficulty": native},
        )
    return rec


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    musics = _load(fetcher, "musics")
    diffs = _load(fetcher, "musicDifficulties")
    tag_rows = _load(fetcher, "musicTags")
    artists = {a["id"]: a for a in _load(fetcher, "musicArtists")}
    vocal_rows = _load(fetcher, "musicVocals")
    chars = {c["id"]: f"{c.get('firstName') or ''}{c.get('givenName') or ''}" for c in _load(fetcher, "gameCharacters")}
    outside = {c["id"]: c.get("name", "") for c in _load(fetcher, "outsideCharacters")}
    bpms = _bpms(fetcher)
    by_music: dict[int, list] = {}
    for d in diffs:
        by_music.setdefault(d["musicId"], []).append(d)
    tags: dict[int, set[str]] = {}
    for t in tag_rows:
        tags.setdefault(t["musicId"], set()).add(t["musicTag"])
    vocals: dict[int, list] = {}
    for v in vocal_rows:
        vocals.setdefault(v["musicId"], []).append(v)
    now_ms = time.time() * 1000
    songs = [sekai_record(m, by_music.get(m["id"], []), tags.get(m["id"], set()), artists, vocals.get(m["id"], []),
                          chars, outside, bpms.get(int(m["id"])), now_ms)
             for m in sorted(musics, key=lambda m: m["id"])]
    for song in songs:
        if not song.bpm:
            _bpm_from_sus(fetcher, song)
    unknown = sorted({d["musicDifficulty"] for d in diffs} - set(DIFFICULTIES))
    return songs, {
        "musics": len(musics), "difficulties": len(diffs),
        "unreleased": [s.song_id for s in songs if s.extra.get("unreleased")],
        "bpm_from_sus": [s.song_id for s in songs if s.extra.get("bpm_source") == "sus"],
        "no_bpm": [s.song_id for s in songs if not s.bpm],
        "no_difficulties": [s.song_id for s in songs if not s.charts],
        "unknown_difficulties": unknown,
    }


def _bpm_from_sus(fetcher: Fetcher, song: SongRecord) -> None:
    """BPM display for a song MoeSekai-Hub does not list, from the exact BPM changes of one of its charts."""
    for slot in sorted(song.charts, key=lambda s: s != "master"):
        chart = chart_from_sus(fetcher, song, slot)
        if chart is None:
            continue
        t = chart.timing
        values = sorted({b.bpm for b in t.bpms})
        main = t.main_bpm(chart.end_beat)
        song.bpm = f"{values[0]:g}" if len(values) == 1 else f"{values[0]:g}-{values[-1]:g} ({main:g})"
        song.extra["bpm_main"] = main
        song.extra["bpm_source"] = "sus"
        return


# ------------------------------------------------------------------------------------------------ charts

def sus_urls(song: SongRecord, slot: str) -> list[tuple[str, str]]:
    cr = song.charts.get(slot)
    if cr is None or not song.song_id.isdigit():
        return []
    diff = cr.source.get("difficulty") or cr.native.lower()
    return [(name, url.format(id=int(song.song_id), diff=diff)) for name, url in SUS_SOURCES]


def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    source = dict(cr.source)
    if song.extra.get("filler_sec") is not None:
        source["filler_sec"] = song.extra["filler_sec"]   # silence before beat 0 in the game's own audio file
    return ChartMeta(
        game="sekai", song_id=song.song_id, title=song.title, artist=song.artist, title_romaji=song.title_romaji,
        artist_romaji=song.artist_romaji, genre=song.genre, difficulty=slot, difficulty_native=cr.native,
        level=cr.level, designer=cr.designer, bpm_display=song.bpm, source=source, note_counts=dict(cr.note_counts),
    )


def chart_from_sus(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    from ..formats.sus import sus_to_chart

    for i, (name, url) in enumerate(sus_urls(song, slot)):
        try:
            text = fetcher.get(url).decode("utf-8-sig", errors="replace")
        except NotFound:
            continue
        if not text.lstrip().startswith(("This file", "#")):
            continue        # not a SUS file (an error page)
        meta = chart_meta(song, slot)
        meta.source.update({"extracted_from": name, "url": url})
        chart = sus_to_chart(text, meta)
        if i > 0:
            chart.warnings.append(f"from the fallback source {name} (may be an older chart revision)")
        published = song.charts[slot].note_counts.get("combo")
        combo = chart.meta.source["combo"]["total"]
        if published and combo != published:
            chart.warnings.append(f"combo {combo} differs from the published note count {published}")
        return chart
    return None


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    asset = song.source.get("sekai_master", {}).get("assetbundle", "")
    if not asset:
        return None
    for pattern in JACKET_SOURCES:
        url = pattern.format(asset=asset)
        try:
            return fetcher.get(url), "png", url
        except NotFound:
            continue
    return None


# ------------------------------------------------------------------------------------------------ romaji

def _is_latin(text: str) -> bool:
    """True when a title needs no romanisation: no CJK, kana or CJK punctuation, and some letters or digits."""
    norm = unicodedata.normalize("NFKC", text)
    return all(ord(ch) < 0x2E80 for ch in norm) and any(ch.isalnum() for ch in norm)


_KANA = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko", "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so", "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to", "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho", "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo", "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro", "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "ゔ": "vu", "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}


def romanize(kana: str) -> str:
    """Hepburn-style romaji of a kana reading (the master data's `pronunciation`). Non-kana characters are kept."""
    text = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in unicodedata.normalize("NFKC", kana))
    out: list[str] = []
    i = 0
    while i < len(text):
        c, nxt = text[i], text[i + 1] if i + 1 < len(text) else ""
        if c == "っ":
            following = romanize(text[i + 1:i + 3])[:1] if i + 1 < len(text) else ""
            if following == "c":
                following = "t"                     # っち -> tchi
            out.append(following if following.isalpha() and following not in "aiueon" else "")
            i += 1
            continue
        if c == "ー":
            prev = "".join(out)
            out.append(next((ch for ch in reversed(prev) if ch in "aiueo"), ""))
            i += 1
            continue
        base = _KANA.get(c)
        if base is None:
            out.append(c)
            i += 1
            continue
        if nxt in _SMALL_Y and len(base) >= 2 and base.endswith("i"):
            stem = base[:-1]
            out.append(stem + _SMALL_Y[nxt] if stem in ("sh", "ch", "j") else stem + "y" + _SMALL_Y[nxt])
            i += 2
            continue
        if nxt in _SMALL_V and len(base) >= 1 and c not in _SMALL_V:
            stem = {"ふ": "f", "ゔ": "v", "う": "w", "て": "t", "で": "d", "し": "sh", "じ": "j", "ち": "ch",
                    "つ": "ts", "と": "t", "ど": "d"}.get(c)
            if stem is not None:
                out.append(stem + _SMALL_V[nxt])
                i += 2
                continue
        out.append(base)
        i += 1
    return "".join(out)


def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="sekai", catalog=_catalog, chart=chart_from_sus, jacket=_jacket,
                        notes="catalog: sekai-master-db-diff (+ MoeSekai-Hub BPMs); charts: official SUS from "
                              "pjsekai-decrypted-data, else Sekai-Chart-Json; jackets: pjsekai-decrypted-data, else "
                              "sekai-jacket-db"))


_register()
