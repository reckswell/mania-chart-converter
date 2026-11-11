"""Static facts about each source game: lanes, native difficulty names on the shared ladder, genre slugs.

The canonical ladder (easy, normal, hard, expert, master, extra, special) is explained in
docs/research/vsrg-mechanics.md, "Cross-game difficulty ladder". Every file name and database path uses the canonical
slot; the native name is always kept beside it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

LADDER = ["easy", "normal", "hard", "expert", "master", "extra", "special"]


@dataclass(frozen=True)
class Game:
    key: str
    name: str                      # display name, used in osu! Source/Version
    lanes: int                     # width of the source lane surface, in source lane units
    variable_width: bool
    difficulties: dict[str, str]   # native key (as sources spell it, lowercased) -> canonical slot
    native_names: dict[str, str]   # native key -> display name
    genres: dict[str, str] = field(default_factory=dict)  # native genre -> slug
    status: str = "planned"        # planned | in-progress | working


GAMES: dict[str, Game] = {}


def _add(game: Game) -> None:
    GAMES[game.key] = game


_add(Game(
    key="chunithm", name="CHUNITHM", lanes=16, variable_width=True,
    difficulties={"bas": "normal", "bsc": "normal", "basic": "normal",
                  "adv": "hard", "advanced": "hard",
                  "exp": "expert", "expert": "expert",
                  "mas": "master", "mst": "master", "master": "master",
                  "ult": "extra", "ultima": "extra",
                  "we": "special", "worldsend": "special"},
    native_names={"bas": "BASIC", "bsc": "BASIC", "basic": "BASIC", "adv": "ADVANCED", "advanced": "ADVANCED",
                  "exp": "EXPERT", "expert": "EXPERT", "mas": "MASTER", "mst": "MASTER", "master": "MASTER",
                  "ult": "ULTIMA", "ultima": "ULTIMA", "we": "WORLD'S END", "worldsend": "WORLD'S END"},
    genres={"POPS & ANIME": "pops-anime", "niconico": "niconico", "東方Project": "touhou-project",
            "VARIETY": "variety", "イロドリミドリ": "irodorimidori", "ゲキマイ": "gekimai",
            "ORIGINAL": "original", "WORLD'S END": "worlds-end"},
    status="in-progress",
))
_add(Game(
    key="sekai", name="Project SEKAI COLORFUL STAGE!", lanes=12, variable_width=True,
    difficulties={"easy": "easy", "normal": "normal", "hard": "hard", "expert": "expert", "master": "master",
                  "append": "extra"},
    native_names={k: k.upper() for k in ["easy", "normal", "hard", "expert", "master", "append"]},
    genres={"VIRTUAL SINGER": "virtual-singer", "Leo/need": "leo-need", "MORE MORE JUMP!": "more-more-jump",
            "Vivid BAD SQUAD": "vivid-bad-squad", "ワンダーランズ×ショウタイム": "wonderlands-showtime",
            "25時、ナイトコードで。": "nightcord-at-25", "その他": "other"},
))
_add(Game(
    key="bandori", name="BanG Dream! Girls Band Party!", lanes=7, variable_width=False,
    difficulties={"easy": "easy", "normal": "normal", "hard": "hard", "expert": "expert", "special": "master"},
    native_names={k: k.upper() for k in ["easy", "normal", "hard", "expert", "special"]},
))
_add(Game(
    key="sdvx", name="SOUND VOLTEX", lanes=6, variable_width=False,   # 4 BT + 2 FX; lasers via KNOB notes
    difficulties={"nov": "normal", "adv": "hard", "exh": "expert", "mxm": "master", "inf": "master",
                  "grv": "master", "hvn": "master", "vvd": "master", "xcd": "master"},
    native_names={"nov": "NOVICE", "adv": "ADVANCED", "exh": "EXHAUST", "mxm": "MAXIMUM", "inf": "INFINITE",
                  "grv": "GRAVITY", "hvn": "HEAVENLY", "vvd": "VIVID", "xcd": "EXCEED"},
))
_add(Game(
    key="ddr", name="DanceDanceRevolution", lanes=4, variable_width=False,
    difficulties={"beginner": "easy", "basic": "normal", "difficult": "hard", "expert": "expert",
                  "challenge": "master"},
    native_names={k: k.upper() for k in ["beginner", "basic", "difficult", "expert", "challenge"]},
))
_add(Game(
    key="piu", name="PUMP IT UP", lanes=5, variable_width=False,
    difficulties={},   # level-banded; see slot_for_level
    native_names={"single": "SINGLE", "double": "DOUBLE", "coop": "CO-OP"},
))
_add(Game(
    key="arcaea", name="Arcaea", lanes=4, variable_width=False,
    difficulties={"pst": "normal", "prs": "hard", "ftr": "expert", "byd": "master", "etr": "extra"},
    native_names={"pst": "PAST", "prs": "PRESENT", "ftr": "FUTURE", "byd": "BEYOND", "etr": "ETERNAL"},
))
_add(Game(
    key="iidx", name="beatmania IIDX", lanes=8, variable_width=False,   # 7 keys + scratch (SP)
    difficulties={"beginner": "easy", "normal": "normal", "hyper": "hard", "another": "expert",
                  "leggendaria": "master"},
    native_names={k: k.upper() for k in ["beginner", "normal", "hyper", "another", "leggendaria"]},
))
_add(Game(
    key="guitarhero", name="Guitar Hero", lanes=5, variable_width=False,
    difficulties={"easy": "easy", "medium": "normal", "hard": "hard", "expert": "expert", "expert+": "master"},
    native_names={"easy": "EASY", "medium": "MEDIUM", "hard": "HARD", "expert": "EXPERT", "expert+": "EXPERT+"},
))
_add(Game(
    key="gitadora", name="GITADORA", lanes=5, variable_width=False,
    difficulties={"bsc": "normal", "adv": "hard", "ext": "expert", "mas": "master"},
    native_names={"bsc": "BASIC", "adv": "ADVANCED", "ext": "EXTREME", "mas": "MASTER"},
))


def game(key: str) -> Game:
    try:
        return GAMES[key]
    except KeyError:
        raise KeyError(f"unknown game {key!r}; known: {', '.join(sorted(GAMES))}") from None


def canonical_difficulty(game_key: str, native: str) -> str:
    g = game(game_key)
    slot = g.difficulties.get(native.lower())
    if slot is None:
        raise KeyError(f"{g.name}: no ladder slot for difficulty {native!r}")
    return slot


def native_name(game_key: str, native: str) -> str:
    return game(game_key).native_names.get(native.lower(), native.upper())


def slot_for_level(level: float) -> str:
    """Ladder slot for level-only games (Pump It Up): bands chosen to roughly match other games' tiers."""
    if level < 7:
        return "easy"
    if level < 13:
        return "normal"
    if level < 18:
        return "hard"
    if level < 22:
        return "expert"
    return "master"


def slugify(text: str, fallback: str = "untitled") -> str:
    """Filesystem-safe ASCII slug. Non-Latin text falls back to `fallback` (pass the source's romaji or id)."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_ = norm.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_).strip("-").lower()
    return slug[:60] or fallback


def genre_slug(game_key: str, genre: str) -> str:
    g = game(game_key)
    return g.genres.get(genre) or slugify(genre, fallback="other")
