"""Guitar Hero: every song of the mainline games (on-disc and DLC), exact notes from official chart files, album art.

Catalog (one record per song per game; a song in several games has several records):
- rodbove/score-shots `lib/songs.json`: {game: {disc: [{song, artist}], dlc: [...]}} for GH1 .. GH Live. This is
  the backbone (title, artist, on-disc or DLC). Band Hero is in that file too but is not a mainline game.
- The games' own song lists add the release year, the internal song id, cover vs master recording and (GH3, GHA)
  whether the co-op part is bass or rhythm: Neversoft `*songlist*.q` scripts (nsneverhax/neversoft-script-library;
  titles there are often `qs(0x...)` hashes, matched by hashing our titles with `formats.gh.qs_key`) and, for GH2 and
  GH80s, Harmonix's `songs_merge.dta` / `songs_80s.dta` (hmxmilohax/Guitar-Hero-II-Deluxe-Unified).

Charts (exact; `formats/gh.py` reads both formats):
- GH2 and GH80s: Harmonix's own MIDI files from Guitar-Hero-II-Deluxe-Unified (`_ark/songs/<id>/<id>.mid`).
- Everything else: Clone Hero rips of the official charts (`notes.mid` / `notes.chart` + `song.ini` + album art) in
  public GitHub repos, used only when the song.ini `charter` names the studio that made the chart (Neversoft,
  Harmonix, Beenox, FreeStyleGames, Vicarious Visions, Budcat). They were found with GitHub code search
  (`filename:song.ini "icon = <game>"`) and are listed in the committed manifest `data/guitarhero_rips.json`, so this
  module never needs `gh` at run time. Rebuild it with `python -m vsrgconv.sources.guitarhero enumerate`.

Jackets: the album art of the song's rip folder, else of any rip of the same title and artist (any game, any
charter: the picture does not depend on who charted).

Slots: lead guitar uses the ladder (EASY easy, MEDIUM normal, HARD hard, EXPERT expert); other parts are prefixed:
`coop-<slot>` (co-op lead), `bass-<slot>`, `rhythm-<slot>`, `drums-<slot>` with Expert+ (2x kick) as `drums-master`.
Song ids are `<game-slug>-<title-slug>`. Details: docs/sources/guitarhero.md.
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import unicodedata
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import ChartRecord, SongRecord
from ..games.registry import slugify
from ..model import Chart, ChartMeta
from .fetch import Fetcher, NotFound

SCORESHOTS_URL = "https://raw.githubusercontent.com/rodbove/score-shots/HEAD/lib/songs.json"
GH2DX_REPO = "hmxmilohax/Guitar-Hero-II-Deluxe-Unified"
GH2DX = f"https://raw.githubusercontent.com/{GH2DX_REPO}/HEAD/_ark/"
GH2DX_LISTS = {"gh2": "config/songs_merge.dta", "gh80s": "config/songs_80s.dta"}
NS_REPO = "nsneverhax/neversoft-script-library"
NS_LIB = f"https://raw.githubusercontent.com/{NS_REPO}/HEAD/"
MANIFEST = Path(__file__).parent / "data" / "guitarhero_rips.json"
OFFICIAL_CHARTERS = re.compile(r"neversoft|harmonix|beenox|free\s*style\s*games|vicarious\s*visions|budcat", re.I)
#: preferred repos when several official rips of one song exist (largest, most consistent collections first)
REPO_ORDER = ["Dakota-Minnema/CloneHero", "Stoverbrock1/CloneHeroSongs", "chindc13/GG2Hero", "KebabMumsare/Songs",
              "ChristianMendozaa/CH_MGVNUN"]
NOTES_FILES = ["notes.mid", "notes.chart", "Notes.mid", "notes.midi", "Notes.chart"]
ART_FILES = ["album.png", "album.jpg", "album.jpeg", "Album.png", "Album.jpg"]


@dataclass(frozen=True)
class GhGame:
    slug: str
    name: str             # display name = the genre
    scoreshots: str       # key in songs.json
    icons: tuple[str, ...]
    kind: str             # guitar | coop | band | band+ | live (which parts the game has)


GAMES = [
    GhGame("gh1", "Guitar Hero", "Guitar Hero", ("gh1",), "guitar"),
    GhGame("gh2", "Guitar Hero II", "Guitar Hero II", ("gh2", "gh2dlc"), "coop"),
    GhGame("gh80s", "Guitar Hero Encore: Rocks the 80s", "Guitar Hero Encore: Rocks the 80s", ("gh80s",), "coop"),
    GhGame("gh3", "Guitar Hero III: Legends of Rock", "Guitar Hero III: Legends of Rock", ("gh3", "gh3dlc"), "coop"),
    GhGame("gha", "Guitar Hero: Aerosmith", "Guitar Hero: Aerosmith", ("gha",), "coop"),
    GhGame("ghwt", "Guitar Hero World Tour", "Guitar Hero World Tour", ("ghwt", "ghwtdlc"), "band"),
    GhGame("ghm", "Guitar Hero: Metallica", "Guitar Hero: Metallica", ("ghm",), "band+"),
    GhGame("ghsh", "Guitar Hero Smash Hits", "Guitar Hero Smash Hits", ("ghsh",), "band+"),
    GhGame("gh5", "Guitar Hero 5", "Guitar Hero 5", ("gh5", "gh5dlc"), "band+"),
    GhGame("ghvh", "Guitar Hero: Van Halen", "Guitar Hero: Van Halen", ("ghvh",), "band+"),
    GhGame("ghwor", "Guitar Hero: Warriors of Rock", "Guitar Hero: Warriors of Rock", ("ghwor", "ghwordlc"), "band+"),
    GhGame("ghl", "Guitar Hero Live", "Guitar Hero Live", ("ghl", "ghtv"), "live"),
]
GAME_BY_SLUG = {g.slug: g for g in GAMES}
ICON_GAME = {icon: g.slug for g in GAMES for icon in g.icons}

DIFFS = ["easy", "medium", "hard", "expert"]
DIFF_SLOT = {"easy": "easy", "medium": "normal", "hard": "hard", "expert": "expert", "expert+": "master"}
PART_PREFIX = {"guitar": "", "guitar_ghl": "", "coop": "coop-", "bass": "bass-", "rhythm": "rhythm-",
               "drums": "drums-"}
PART_NATIVE = {"guitar": "", "guitar_ghl": "", "coop": "CO-OP ", "bass": "BASS ", "rhythm": "RHYTHM ",
               "drums": "DRUMS "}


def slot_for(part: str, diff: str) -> str:
    return PART_PREFIX[part] + DIFF_SLOT[diff]


def native_for(part: str, diff: str) -> str:
    return PART_NATIVE[part] + diff.upper()


def game_parts(game: GhGame, coop_part: str = "") -> dict[str, list[str]]:
    """The chart slots a song of this game has, as {part: [difficulties]} (before looking at any file)."""
    if game.kind == "live":
        return {"guitar_ghl": list(DIFFS)}
    parts: dict[str, list[str]] = {"guitar": list(DIFFS)}
    if game.kind == "coop" and coop_part in ("bass", "rhythm"):
        parts[coop_part] = list(DIFFS)
    if game.kind in ("band", "band+"):
        parts["bass"] = list(DIFFS)
        parts["drums"] = list(DIFFS) + (["expert+"] if game.kind == "band+" else [])
    return parts


GAME_KIND_PARTS = {"guitar": {"guitar"}, "coop": {"guitar", "coop", "bass", "rhythm"},
                   "band": {"guitar", "bass", "drums"}, "band+": {"guitar", "bass", "drums"}, "live": {"guitar_ghl"}}


def usable_parts(game: GhGame, parts: dict[str, list[str]]) -> dict[str, list[str]]:
    """Parts of a chart file that count as this game's official charts: only instruments the game has (a GH3 rip's
    drums are fan-made), GH Live's lead is its 6-fret track (its 5-fret PART GUITAR is a conversion), and the other
    games' 6-fret tracks are conversions."""
    keep = GAME_KIND_PARTS[game.kind]
    return {p: ds for p, ds in parts.items() if p in keep}


# ------------------------------------------------------------------------------------------------ matching

def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", t)


def norm_loose(text: str) -> str:
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", text or "")
    t = re.sub(r"^\s*the\s+", "", t, flags=re.I)
    return norm(t) or norm(text)


def norm_artist(text: str) -> str:
    t = re.sub(r"^\s*the\s+", "", text or "", flags=re.I)
    t = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+", t, flags=re.I)[0]
    return norm(t)


class SongIndex:
    """Find a catalog song of one game by title (and artist when titles collide)."""

    def __init__(self, songs: list[SongRecord]) -> None:
        self.exact: dict[str, list[SongRecord]] = {}
        self.loose: dict[str, list[SongRecord]] = {}
        for s in songs:
            self.exact.setdefault(norm(s.title), []).append(s)
            self.loose.setdefault(norm_loose(s.title), []).append(s)

    @staticmethod
    def _pick(cands: list[SongRecord], artist: str, need_artist: bool) -> SongRecord | None:
        if artist:
            same = [c for c in cands if _artist_like(c.artist, artist)]
            if same:
                return same[0]
            if need_artist:
                return None
        return cands[0] if len(cands) == 1 else None

    def find(self, title: str, artist: str = "", fuzzy: bool = True, need_artist: bool = False) -> SongRecord | None:
        for table, key in ((self.exact, norm(title)), (self.loose, norm_loose(title))):
            if key and key in table:
                hit = self._pick(table[key], artist, need_artist)
                if hit is not None:
                    return hit
        if not fuzzy:
            return None
        key = norm_loose(title)
        for close in difflib.get_close_matches(key, list(self.loose), n=3, cutoff=0.8):
            hit = self._pick(self.loose[close], artist, need_artist=True)
            if hit is not None:
                return hit
        return None


def _artist_like(a: str, b: str) -> bool:
    x, y = norm_artist(a), norm_artist(b)
    if not x or not y:
        return False
    return x == y or x in y or y in x or difflib.SequenceMatcher(None, x, y).ratio() >= 0.8


def is_official(charter: str) -> bool:
    from ..formats.gh import strip_tags

    return bool(OFFICIAL_CHARTERS.search(strip_tags(charter or "")))


def only_official(charter: str) -> bool:
    """Every name in the charter field is a studio (no fan co-charter): used for folders with no game icon."""
    from ..formats.gh import strip_tags

    names = [n for n in re.split(r"\s*(?:,|&|/|;|\band\b)\s*", strip_tags(charter or "")) if n.strip()]
    return bool(names) and all(OFFICIAL_CHARTERS.fullmatch(n.strip()) for n in names)


#: which games each studio charted, to place a rip folder that has no game icon
STUDIO_GAMES = {"harmonix": ("gh1", "gh2", "gh80s"),
                "neversoft": ("gh3", "gha", "ghwt", "ghm", "ghsh", "gh5", "ghvh", "ghwor"),
                "beenox": ("ghsh",), "freestyle": ("ghl",), "vicarious": ("gh3", "gha", "ghwt", "ghm", "gh5"),
                "budcat": ("gh3", "ghwt", "ghm", "ghsh", "gh5", "ghvh")}


def guess_game(rip: dict[str, Any], indexes: dict[str, "SongIndex"]) -> SongRecord | None:
    """A song for a rip folder without a GH icon: the charter must be only studios; the title and artist must match
    exactly one game that studio charted (a game slug in the folder name breaks ties). Harmonix folders with drums
    are Rock Band, not GH1/GH2."""
    from ..formats.gh import strip_tags

    ini = rip.get("ini", {})
    charter = strip_tags(ini.get("charter", "") or ini.get("frets", "")).lower()
    if not only_official(charter):
        return None
    if "harmonix" in charter and "drums" in (rip.get("parts") or {}):
        return None
    games = {g for key, gs in STUDIO_GAMES.items() if key in charter.replace(" ", "") for g in gs}
    found = {g: idx.find(ini.get("name", ""), ini.get("artist", ""), fuzzy=False, need_artist=True)
             for g, idx in indexes.items() if g in games}
    found = {g: r for g, r in found.items() if r is not None}
    if len(found) > 1:
        tokens = set(re.findall(r"[a-z0-9]+", rip.get("dir", "").lower()))
        found = {g: r for g, r in found.items() if g in tokens} or found
    return next(iter(found.values())) if len(found) == 1 else None


def raw_url(repo: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/HEAD/" + urllib.parse.quote(path)


def _year(text: Any) -> int | None:
    m = re.search(r"(19|20)\d\d", str(text or ""))
    return int(m.group(0)) if m else None


# ------------------------------------------------------------------------------------------------ manifest

def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    if not path.exists():
        return {"rips": [], "gh2dx": {}, "songlists": {}}
    return json.loads(path.read_text("utf-8"))


# ------------------------------------------------------------------------------------------------ catalog

def base_records(data: dict[str, Any]) -> list[SongRecord]:
    """One SongRecord per (game, song) from score-shots' songs.json. Slots are added later (`assign_slots`)."""
    out: list[SongRecord] = []
    for g in GAMES:
        lists = data.get(g.scoreshots) or {}
        seen: set[str] = set()
        for key in ("disc", "dlc"):
            for row in lists.get(key, []):
                title, artist = row.get("song", "").strip(), row.get("artist", "").strip()
                if not title:
                    continue
                sid = f"{g.slug}-{slugify(title, fallback='song')}"
                n = 2
                while sid in seen:
                    sid = f"{g.slug}-{slugify(title, fallback='song')}-{n}"
                    n += 1
                seen.add(sid)
                out.append(SongRecord(
                    game="guitarhero", song_id=sid, title=title, artist=artist, genre=g.name, version=g.name,
                    source={"scoreshots": {"game": g.scoreshots, "list": key}},
                    extra={"game": g.slug, "dlc": key == "dlc"}))
    return out


def apply_songlist(songs: list[SongRecord], entries: dict[str, dict[str, Any]], source: str) -> int:
    """Years, song-list ids, cover flag and co-op part from a Neversoft .q song list (titles plain or hashed)."""
    from ..formats.gh import q_year, qs_key

    index = SongIndex(songs)
    by_hash: dict[int, SongRecord] = {}
    by_id: dict[str, SongRecord] = {}
    for s in songs:
        for t in {s.title, s.title.upper(), s.title.lower(), s.title.title()}:
            by_hash.setdefault(qs_key("\\L" + t), s)
            by_hash.setdefault(qs_key(t), s)
        by_id.setdefault(norm(s.title), s)
    hits = 0
    for sid, e in entries.items():
        title, artist = e.get("title"), e.get("artist")
        rec = None
        if isinstance(title, str) and title:
            rec = index.find(title, artist if isinstance(artist, str) else "", fuzzy=False)
        elif isinstance(title, dict):
            rec = by_hash.get(title["qs"])
        if rec is None:
            rec = by_id.get(norm(e.get("id", "")))
        if rec is None:
            continue
        hits += 1
        year = q_year(e)
        if year and not rec.extra.get("year"):
            rec.extra["year"] = year
        rec.extra.setdefault("songlist_id", e.get("id", sid))
        rec.source.setdefault("songlist", source)
        if e.get("original_artist") == 0:
            rec.extra["cover"] = True
        if e.get("rhythm_track") in (0, 1) and "coop_part" not in rec.extra:
            rec.extra["coop_part"] = "rhythm" if e["rhythm_track"] == 1 else "bass"
        if isinstance(e.get("double_kick"), int):
            rec.extra.setdefault("double_kick", bool(e["double_kick"]))
    return hits


def apply_gh2dx(songs: list[SongRecord], dta_text: str, list_name: str, parts_by_id: dict[str, Any]) -> int:
    """Attach Harmonix's MIDI (GH2 Deluxe) to GH2/GH80s songs; co-op part from the .dta audio tracks."""
    from ..formats.gh import dta_songs

    index = SongIndex(songs)
    hits = 0
    for d in dta_songs(dta_text):
        info = parts_by_id.get(d["id"])
        if info is None:
            continue
        rec = index.find(d.get("name", ""), d.get("artist", ""))
        if rec is None or "gh2dx" in rec.source:
            continue
        hits += 1
        midi = d["midi_file"] if d["midi_file"].endswith(".mid") else d["midi_file"] + ".mid"
        rec.source["gh2dx"] = {"id": d["id"], "list": list_name, "url": GH2DX + midi,
                               "hopo_threshold": d.get("hopo_threshold"), "parts": info.get("parts", {})}
        tracks = d.get("tracks", [])
        if "rhythm" in tracks:
            rec.extra["coop_part"] = "rhythm"
        elif "bass" in tracks:
            rec.extra["coop_part"] = "bass"
        rec.extra["songlist_id"] = d["id"]
        rec.extra["source_repo"] = GH2DX_REPO
    return hits


def is_coop_rip(rip: dict[str, Any]) -> bool:
    """A "(Co-op)" rip folder: its lead track is the co-op lead (checked: GH2 Free Bird's co-op folder PART GUITAR
    has the 2150 notes of Harmonix's PART GUITAR COOP, not the lead's 2154)."""
    return bool(re.search(r"\bco-?op\b", f"{rip.get('ini', {}).get('name', '')} {rip.get('dir', '')}", re.I))


def rip_part_map(rip: dict[str, Any], game: GhGame) -> dict[str, tuple[str, list[str]]]:
    """slot part -> (part in the file, difficulties), only for instruments the game has."""
    coop = is_coop_rip(rip)
    out: dict[str, tuple[str, list[str]]] = {}
    for p, ds in (rip.get("parts") or {}).items():
        sp = "coop" if coop and p == "guitar" else p
        if sp in GAME_KIND_PARTS[game.kind] and ds:
            out[sp] = (p, ds)
    return out


def _rip_rank(rip: dict[str, Any], game: GhGame, song: SongRecord, slot_part: str) -> tuple:
    """Lower is better: a plain folder over a co-op one (except for the co-op part), an exact title over a variant
    ("(Full Band Upgrade)", "(... Convert)"), more parts, .mid, the larger collections, art."""
    repo = rip.get("repo", "")
    variant = norm(rip.get("ini", {}).get("name", "")) != norm(song.title)
    return (is_coop_rip(rip) != (slot_part == "coop"), variant,
            -sum(len(ds) for _, ds in rip_part_map(rip, game).values()),
            0 if str(rip.get("notes", "")).endswith(".mid") else 1,
            REPO_ORDER.index(repo) if repo in REPO_ORDER else len(REPO_ORDER), 0 if rip.get("art") else 1,
            repo, rip.get("dir", ""))


def rip_key(rip: dict[str, Any]) -> str:
    return f"{rip['repo']}/{rip['dir']}"


def apply_rips(songs: list[SongRecord], rips: list[dict[str, Any]]) -> dict[str, Any]:
    """Match rip folders to songs (by the song.ini icon's game, then any game with the same title and artist; a
    folder with no GH icon by `guess_game`), pick a chart file per part, find album art, and list every slot."""
    by_game: dict[str, list[SongRecord]] = {}
    for s in songs:
        by_game.setdefault(s.extra["game"], []).append(s)
    indexes = {g: SongIndex(ss) for g, ss in by_game.items()}
    attached: dict[str, list[dict[str, Any]]] = {}
    art_by_song: dict[tuple[str, str], str] = {}
    unmatched: list[str] = []
    seen: set[str] = set()
    for rip in rips:
        if rip_key(rip) in seen:
            continue
        seen.add(rip_key(rip))
        ini = rip.get("ini", {})
        title, artist = ini.get("name", ""), ini.get("artist", "")
        game = ICON_GAME.get(str(ini.get("icon", "")).lower())
        rec = indexes[game].find(title, artist) if game in indexes else None
        if game is None:
            rec = guess_game(rip, indexes)
        elif rec is None:
            for g, idx in indexes.items():
                rec = idx.find(title, artist, fuzzy=False, need_artist=True)
                if rec is not None:
                    break
        if rip.get("art"):
            art_by_song.setdefault((norm_loose(title), norm_artist(artist)), raw_url(rip["repo"], _join(rip, rip["art"])))
        if rec is None:
            unmatched.append(f"{ini.get('icon', '')} {artist} - {title} ({rip.get('repo')})")
            continue
        attached.setdefault(rec.song_id, []).append(rip)
    official_songs = 0
    for s in songs:
        game = GAME_BY_SLUG[s.extra["game"]]
        rips_here = attached.get(s.song_id, [])
        files: dict[str, dict[str, dict[str, Any]]] = {}      # slot part -> difficulty -> file
        if "gh2dx" in s.source:
            g2 = s.source["gh2dx"]
            for p, ds in usable_parts(game, g2.get("parts", {})).items():
                files[p] = {d: {"from": "gh2dx", "url": g2["url"], "part": p, "charter": "Harmonix"} for d in ds}
        official = [r for r in rips_here if is_official(r.get("ini", {}).get("charter", "")) and r.get("notes")
                    and rip_part_map(r, game)]
        used: dict[str, dict[str, Any]] = {}
        wanted = sorted({(sp, d) for r in official for sp, (_, ds) in rip_part_map(r, game).items() for d in ds})
        for sp, d in wanted:
            if d in files.get(sp, {}):
                continue
            best = min((r for r in official if d in rip_part_map(r, game).get(sp, ("", []))[1]),
                       key=lambda r: _rip_rank(r, game, s, sp))
            ref = used.setdefault(rip_key(best), _rip_ref(best))
            files.setdefault(sp, {})[d] = {"from": "clonehero-rip", "url": ref["url"],
                                           "part": rip_part_map(best, game)[sp][0], "rip": rip_key(best),
                                           "charter": ref["charter"]}
        primary = None
        if used:
            official_songs += 1
            lead_files = files.get("guitar_ghl" if game.kind == "live" else "guitar", {})
            lead = (lead_files.get("expert") or next(iter(lead_files.values()), {})).get("rip")
            primary = used[lead] if lead in used else next(iter(used.values()))
            s.source["rip"] = rip_key(primary)
            s.source["rips"] = used
            others = [rip_key(r) for r in official if rip_key(r) not in used]
            if others:
                s.source["rip_alternatives"] = others
            s.extra.setdefault("source_repo", primary["repo"])
        by_pref = sorted(rips_here, key=lambda r: (rip_key(r) not in used, primary is None or
                                                   rip_key(r) != rip_key(primary)))
        art_rip = next((r for r in by_pref if r.get("art")), None)
        if art_rip is not None:
            s.source["art"] = raw_url(art_rip["repo"], _join(art_rip, art_rip["art"]))
        else:
            url = art_by_song.get((norm_loose(s.title), norm_artist(s.artist)))
            if url:
                s.source["art"] = url
        inis = [(primary or {}).get("ini") or {}] + [r.get("ini", {}) for r in by_pref]
        year = next((_year(i.get("year")) for i in inis if _year(i.get("year"))), None)
        if year and not s.extra.get("year"):
            s.extra["year"] = year
        album = next((i["album"] for i in inis if i.get("album")), "")
        if album and not s.extra.get("album"):
            s.extra["album"] = album
        assign_slots(s, files)
    return {"rips": len(rips), "rips_matched": len(seen) - len(unmatched), "songs_with_official_rip": official_songs,
            "rips_unmatched": len(unmatched), "unmatched_sample": unmatched[:25]}


def _join(rip: dict[str, Any], name: str) -> str:
    return f"{rip['dir']}/{name}" if rip.get("dir") else name


def _rip_ref(rip: dict[str, Any]) -> dict[str, Any]:
    from ..formats.gh import strip_tags

    ini = rip.get("ini", {})
    return {"repo": rip["repo"], "dir": rip["dir"], "notes": rip["notes"],
            "url": raw_url(rip["repo"], _join(rip, rip["notes"])),
            "charter": strip_tags(ini.get("charter", "") or ini.get("frets", "")), "ini": ini}


def assign_slots(song: SongRecord, files: dict[str, dict[str, dict[str, Any]]]) -> None:
    """Every chart slot of the song: the game's parts plus whatever the chosen files have. `files`: slot part ->
    difficulty -> {from, url, part (in the file), charter, rip}."""
    game = GAME_BY_SLUG[song.extra["game"]]
    if game.kind == "coop" and not song.extra.get("coop_part"):
        in_file = [p for p in ("bass", "rhythm") if p in files]
        song.extra["coop_part"] = in_file[0] if in_file else "unknown"
    wanted = game_parts(game, song.extra.get("coop_part", ""))
    order = DIFFS + ["expert+"]
    for part, by_diff in files.items():
        wanted.setdefault(part, [])
        for d in by_diff:
            if d not in wanted[part]:
                wanted[part].append(d)
        wanted[part].sort(key=order.index)
    for part, diffs in wanted.items():
        by_diff = files.get(part, {})
        for d in diffs:
            slot = slot_for(part, d)
            cr = ChartRecord(slot=slot, native=native_for(part, d))
            f = by_diff.get(d)
            if f:
                cr.designer = f["charter"]
                cr.source = {"file": {k: v for k, v in (("from", f["from"]), ("url", f["url"]), ("part", f["part"]),
                                                         ("difficulty", d), ("rip", f.get("rip"))) if v}}
            elif files:
                cr.notes = "not in the chart files" + (" (no 2x kick notes: Expert+ is Expert)"
                                                       if d == "expert+" and "expert" in by_diff else "")
            else:
                cr.notes = "no official chart file found"
            song.charts[slot] = cr


def build_catalog(data: dict[str, Any], manifest: dict[str, Any], songlists: dict[str, list[tuple[str, str]]],
                  dtas: dict[str, str]) -> tuple[list[SongRecord], dict[str, Any]]:
    """Pure merge (no network): score-shots rows + song lists + GH2DX .dta + the rip manifest."""
    from ..formats.gh import parse_q_songlist

    songs = base_records(data)
    by_game: dict[str, list[SongRecord]] = {}
    for s in songs:
        by_game.setdefault(s.extra["game"], []).append(s)
    report: dict[str, Any] = {"songlist_hits": {}, "gh2dx_hits": {}}
    for slug, dta in dtas.items():
        report["gh2dx_hits"][slug] = apply_gh2dx(by_game.get(slug, []), dta, GH2DX_LISTS.get(slug, slug),
                                                 manifest.get("gh2dx", {}))
    for slug, files in songlists.items():
        for path, text in files:
            report["songlist_hits"][path] = apply_songlist(by_game.get(slug, []), parse_q_songlist(text), path)
    report.update(apply_rips(songs, manifest.get("rips", [])))
    report["per_game"] = {g.slug: {"songs": len(by_game.get(g.slug, [])),
                                   "with_chart_file": sum(1 for s in by_game.get(g.slug, [])
                                                          if any(c.source.get("file") for c in s.charts.values())),
                                   "with_art": sum(1 for s in by_game.get(g.slug, []) if "art" in s.source)}
                          for g in GAMES}
    return songs, report


def _catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict[str, Any]]:
    data = json.loads(fetcher.get(SCORESHOTS_URL))
    manifest = load_manifest()
    songlists: dict[str, list[tuple[str, str]]] = {}
    missing: list[str] = []
    for slug, paths in manifest.get("songlists", {}).items():
        for path in paths:
            try:
                songlists.setdefault(slug, []).append((path, fetcher.get(NS_LIB + path).decode("latin-1")))
            except NotFound:
                missing.append(path)
    dtas = {}
    for slug, path in GH2DX_LISTS.items():
        try:
            dtas[slug] = fetcher.get(GH2DX + path).decode("latin-1")
        except NotFound:
            missing.append(GH2DX + path)
    songs, report = build_catalog(data, manifest, songlists, dtas)
    report["missing_lists"] = missing
    return songs, report


# ------------------------------------------------------------------------------------------------ charts

_PARSED: "OrderedDict[str, Any]" = OrderedDict()
_PARSED_LOCK = threading.Lock()
_PARSED_MAX = 48
_PARSING: dict[str, threading.Lock] = {}


def _options(song: SongRecord, src: dict[str, Any]):
    from ..formats.gh import GhOptions

    if src.get("from") == "gh2dx":
        g2 = song.source.get("gh2dx", {})
        return GhOptions(harmonix=True, hopo_threshold=g2.get("hopo_threshold"))
    return GhOptions.from_ini(song.source.get("rips", {}).get(src.get("rip", ""), {}).get("ini", {}))


def load_gh_song(fetcher: Fetcher, url: str, options) -> Any:
    """Download and parse a chart file once per URL (several slots of a song read the same file)."""
    from ..formats.gh import read

    key = f"{url}|{options!r}"
    with _PARSED_LOCK:
        if key in _PARSED:
            _PARSED.move_to_end(key)
            return _PARSED[key]
        lock = _PARSING.setdefault(key, threading.Lock())
    with lock:                      # one thread parses a file; the song's other slots wait for it
        with _PARSED_LOCK:
            if key in _PARSED:
                return _PARSED[key]
        parsed = read(fetcher.get(url), urllib.parse.unquote(url.rsplit("/", 1)[-1]), options)
        with _PARSED_LOCK:
            _PARSED[key] = parsed
            _PARSING.pop(key, None)
            while len(_PARSED) > _PARSED_MAX:
                _PARSED.popitem(last=False)
    return parsed


def chart_meta(song: SongRecord, slot: str) -> ChartMeta:
    cr = song.charts[slot]
    return ChartMeta(game="guitarhero", song_id=song.song_id, title=song.title, artist=song.artist, genre=song.genre,
                     difficulty=slot, difficulty_native=cr.native, level=cr.level, designer=cr.designer,
                     source=dict(cr.source))


def _chart(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    cr = song.charts.get(slot)
    src = (cr.source or {}).get("file") if cr else None
    if not src:
        return None
    gs = load_gh_song(fetcher, src["url"], _options(song, src))
    meta = chart_meta(song, slot)
    meta.source.update({"extracted_from": "gh2dx-mid" if src["from"] == "gh2dx" else f"clonehero-{gs.fmt}",
                        "url": src["url"]})
    chart = gs.to_chart(src["part"], src["difficulty"], meta)
    if chart is None:
        raise ValueError(f"{src['url']} has no notes for {src['part']} {src['difficulty']}")
    return chart


def _jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    url = song.source.get("art")
    if not url:
        return None
    ext = urllib.parse.unquote(url).rsplit(".", 1)[-1].lower()
    return fetcher.get(url), ext, url


# ------------------------------------------------------------------------------------------------ enumeration
# Dev-time only (needs the `gh` CLI): python -m vsrgconv.sources.guitarhero enumerate

SONGLISTS = {
    "gh3": ["gh3/360/base/scripts/guitar/songlist.q"],
    "gha": ["gha/360/base/scripts/guitar/songlist.q"],
    "ghwt": ["ghwt/360/base/scripts/guitar/songlist.q", "ghwor/360/base/scripts/guitar/gh4_songlist.q",
             "ghwor/360/base/scripts/guitar/gh4_dlc_songlist.q"],
    "ghm": ["ghm/360/base/scripts/guitar/songlist.q", "ghwor/360/base/scripts/guitar/gh4_1_songlist.q"],
    "ghsh": ["ghsh/360/base/scripts/guitar/songlist.q", "ghwor/360/base/scripts/guitar/gh4_2_songlist.q"],
    "ghvh": ["ghvh/360/base/scripts/guitar/songlist.q", "ghwor/360/base/scripts/guitar/gh4_3_songlist.q"],
    "gh5": ["gh5/360/base/scripts/guitar/songlist.q", "ghwor/360/base/scripts/guitar/gh5_0_songlist.q",
            "ghwor/360/base/scripts/guitar/gh5_dlc_songlist.q"],
    "ghwor": ["ghwor/360/base/scripts/guitar/songlist.q"],
}
#: icon-less rip folders are found by their charter (most hits are Rock Band rips, filtered out by icon)
CHARTER_QUERIES = ["filename:song.ini Neversoft", "filename:song.ini Harmonix", "filename:song.ini Beenox",
                   "filename:song.ini FreeStyleGames", 'filename:song.ini "Vicarious Visions"',
                   "filename:song.ini Budcat"]
INI_KEYS = ["name", "artist", "album", "year", "genre", "charter", "frets", "icon", "hopo_frequency",
            "eighthnote_hopo", "hopofreq", "sustain_cutoff_threshold", "multiplier_note", "star_power_note",
            "five_lane_drums", "pro_drums", "delay", "song_length", "diff_guitar", "diff_bass", "diff_rhythm",
            "diff_guitar_coop", "diff_drums", "diff_guitarghl"]


def _gh_search(query: str, page: int) -> dict[str, Any]:
    import subprocess
    import time

    for attempt in range(5):
        p = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f", f"q={query}", "-f", "per_page=100",
                            "-f", f"page={page}"], capture_output=True, text=True)
        if p.returncode == 0:
            return json.loads(p.stdout)
        time.sleep(20 * (attempt + 1))      # secondary rate limit
    raise RuntimeError(f"gh search failed: {query} page {page}: {p.stderr[:200]}")


def _first_existing(fetcher: Fetcher, repo: str, folder: str, names: list[str]) -> tuple[str, bytes] | None:
    for n in names:
        try:
            return n, fetcher.get(raw_url(repo, f"{folder}/{n}" if folder else n))
        except NotFound:
            continue
    return None


def enumerate_rips(fetcher: Fetcher, log=print) -> dict[str, Any]:
    """Search GitHub for Clone Hero rip folders of the GH games, read each song.ini, find the chart and art files,
    list the parts each chart has, and do the same for GH2 Deluxe's MIDI files. Returns the manifest."""
    import time

    from ..formats.gh import GhOptions, dta_songs, parse_ini, read

    hits: dict[tuple[str, str], str] = {}
    queries = [f'filename:song.ini "icon = {icon}"' for icon in sorted(ICON_GAME)] + CHARTER_QUERIES
    for q in queries:
        for page in range(1, 11):
            res = _gh_search(q, page)
            items = res.get("items", [])
            for it in items:
                hits.setdefault((it["repository"]["full_name"], it["path"]), q)
            log(f"{q} page {page}: {len(items)} (total {res.get('total_count')})")
            time.sleep(7)
            if len(items) < 100:
                break
    rips = []
    for n, ((repo, path), icon_q) in enumerate(sorted(hits.items()), 1):
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        try:
            ini_all = parse_ini(fetcher.get(raw_url(repo, path)).decode("utf-8", errors="replace"))
        except NotFound:
            continue
        icon = ini_all.get("icon", "").strip().lower()
        charter = ini_all.get("charter", "") or ini_all.get("frets", "")
        if icon not in ICON_GAME and (icon or not only_official(charter)):
            continue          # another game's rip (Rock Band ...), or an icon-less folder by a fan
        ini = {k: ini_all[k] for k in INI_KEYS if ini_all.get(k, "") != ""}
        notes = _first_existing(fetcher, repo, folder, NOTES_FILES)
        art = _first_existing(fetcher, repo, folder, ART_FILES)
        parts: dict[str, list[str]] = {}
        error = ""
        if notes:
            try:
                parts = read(notes[1], notes[0], GhOptions.from_ini(ini_all)).parts()
            except Exception as e:  # a broken file is listed with its error, not dropped
                error = f"{type(e).__name__}: {e}"[:200]
        rip = {"repo": repo, "dir": folder, "notes": notes[0] if notes else "", "art": art[0] if art else "",
               "ini": ini, "parts": parts}
        if error:
            rip["error"] = error
        rips.append(rip)
        if n % 50 == 0:
            log(f"{n}/{len(hits)} folders read")
    gh2dx: dict[str, Any] = {}
    for slug, path in GH2DX_LISTS.items():
        for d in dta_songs(fetcher.get(GH2DX + path).decode("latin-1")):
            midi = d["midi_file"] if d["midi_file"].endswith(".mid") else d["midi_file"] + ".mid"
            if d["id"] in gh2dx:
                continue
            try:
                data = fetcher.get(GH2DX + midi)
            except NotFound:
                continue
            gs = read(data, midi, GhOptions(harmonix=True, hopo_threshold=d.get("hopo_threshold")))
            gh2dx[d["id"]] = {"list": slug, "midi": midi, "parts": gs.parts()}
    return {"generated": time.strftime("%Y-%m-%d"),
            "how": "python -m vsrgconv.sources.guitarhero enumerate (GitHub code search, then raw files)",
            "queries": queries, "songlists": SONGLISTS, "gh2dx": gh2dx,
            "rips": sorted(rips, key=lambda r: (r["repo"], r["dir"]))}


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m vsrgconv.sources.guitarhero")
    p.add_argument("command", choices=["enumerate"])
    p.add_argument("--cache", default="data/cache")
    p.add_argument("--interval", type=float, default=0.05)
    p.add_argument("--out", default=str(MANIFEST))
    a = p.parse_args(argv)
    manifest = enumerate_rips(Fetcher(a.cache, min_interval=a.interval))
    Path(a.out).write_text(json.dumps(manifest, ensure_ascii=False, indent=0, sort_keys=False) + "\n", "utf-8")
    print(f"{len(manifest['rips'])} rip folders, {len(manifest['gh2dx'])} GH2DX MIDIs -> {a.out}")
    return 0


# ------------------------------------------------------------------------------------------------ registry

def _register() -> None:
    from . import GameSource, register

    register(GameSource(game="guitarhero", catalog=_catalog, chart=_chart, jacket=_jacket,
                        notes="catalog: score-shots songs.json + Neversoft .q / GH2DX .dta song lists; charts: "
                              "GH2DX Harmonix MIDI (GH2, GH80s), official-charter Clone Hero rips (manifest); "
                              "jackets: rip album art"))


_register()

if __name__ == "__main__":
    raise SystemExit(_main())
