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
    status="in-progress",
))
_add(Game(
    key="bandori", name="BanG Dream! Girls Band Party!", lanes=7, variable_width=False,
    difficulties={"easy": "easy", "normal": "normal", "hard": "hard", "expert": "expert", "special": "master"},
    native_names={k: k.upper() for k in ["easy", "normal", "hard", "expert", "special"]},
    # Genre = band name (masterBandMap). Main bands get a folder each; every one-off unit or collaboration goes to
    # "other", as in the game's band filter (listed so "Afterglow×香澄" does not slugify into afterglow/). New units
    # show up in the catalog report as `unmapped_bands` (docs/sources/bandori.md).
    genres={"Poppin'Party": "poppin-party", "Afterglow": "afterglow", "ハロー、ハッピーワールド！": "hello-happy-world",
            "Pastel＊Palettes": "pastel-palettes", "Roselia": "roselia", "RAISE A SUILEN": "raise-a-suilen",
            "Morfonica": "morfonica", "MyGO!!!!!": "mygo", "Ave Mujica": "ave-mujica",
            **dict.fromkeys([
                "Glitter*Green", "Afterglow×香澄", "Poppin'Party×グリグリ", "戸山香澄", "花園たえ", "牛込りみ", "山吹沙綾",
                "市ヶ谷有咲", "GBP!スペシャルバンド", "ハロー、ハッピーワールド！×蘭×彩", "香澄×蘭×彩×友希那×こころ",
                "彩×モカ×リサ×花音×つぐみ", "Roselia×蘭", "Poppin'Party×彩×こころ", "Poppin'Party×友希那", "Afterglow×こころ",
                "コラボレーション", "RAISE A SUILEN×友希那", "Pastel＊Palettes×こころ×ましろ", "Poppin'Party×鈴木このみ",
                "Poppin'Party×Pastel＊Palettes×Morfonica", "Roselia×Afterglow", "RAISE A SUILEN×ハロー、ハッピーワールド！",
                "ハロー、ハッピーワールド！×Machico", "香澄×蘭×彩×友希那×こころ×ましろ×レイヤ", "花園たえ×レイヤ",
                "Poppin'Party×ときのそら", "Pastel＊Palettes×湊あくあ", "ハロー、ハッピーワールド！×白上フブキ",
                "Afterglow×FLOW", "FLOW", "ClariS × 丸山彩 from Pastel＊Palettes", "Roselia×レイヤ",
                "ハロー、ハッピーワールド！×彩×ましろ", "Pastel＊Palettes×ClariS", "レイヤ×紗夜×沙綾×ミッシェル×瑠唯",
                "CiRCLE THANKS PARTY！スペシャルバンド", "Poppin'Party×夏希", "Morfonica×fhána", "Roselia×いとうかなこ",
                "RAISE A SUILEN×MYTH & ROID", "ハロー、ハッピーワールド！×P丸様。", "Poppin'Party×P丸様。",
                "ハロー、ハッピーワールド！×香澄×有咲×イヴ", "Afterglow×96猫", "Morfonica×友希那", "Roselia×＋α／あるふぁきゅん。",
                "Afterglow×レイヤ", "彩×日菜×千聖×麻弥×友希那×紗夜×リサ×燐子×薫×花音", "Pastel＊Palettes×有咲×つぐみ×ましろ",
                "kemu feat. 夢ノ結唱 POPY×夢ノ結唱 ROSE", "Poppin'Party×ましろ", "ハロー、ハッピーワールド！×蘭",
                "Pastel＊Palettes×パレオ", "ますかっと feat. 夢ノ結唱 POPY", "Pastel＊Palettes 日菜×千聖×麻弥×イヴ",
                "メガテラ・ゼロ feat. 夢ノ結唱 POPY", "Roselia×ロボ子さん×常闇トワ×沙花叉クロヱ", "Roselia×沙花叉クロヱ",
                "RAISE A SUILEN×星街すいせい", "Afterglow×宝鐘マリン", "Morfonica×雪花ラミィ", "MyGO!!!!!×獅白ぼたん",
                "john feat. 夢ノ結唱 ROSE", "ラプラス・ダークネス", "Pastel＊Palettes×花音", "あこがれの共演",
                "Pastel＊Palettes×蘭", "Poppin'Party×燈", "ハロー、ハッピーワールド！×ましろ", "I♡インドア", "夢ノ結唱 ROSE",
                "CRYCHIC", "羽沢珈琲店にようこそ♪", "優雅なティータイムを♪", "友希那×愛音×りみ×つくし×つぐみ", "ぽわぽわが～るず",
                "クールビューティーズ", "香澄×蘭×彩×友希那×こころ×ましろ×レイヤ×燈×ドロリス×あられ", "Poppin'Party×ハローキティ",
                "Pastel＊Palettes×マイメロディ", "夢ノ結唱 PASTEL", "夢ノ結唱 HALO", "Poppin'Party×蘭", "Morfonica×燈",
                "Pastel＊Palettes×友希那", "ハロー、ハッピーワールド！×レイヤ", "スキを貫け♡", "SISTER'S QUARTET",
                "さんふらわー！", "ぺぺ（CV: Machico）", "星降るティーパーティー", "PINKISH!!!!", "GO OUR WAYS！", "月島まりな",
                "モカ×つくし×愛音", "有咲×ひまり×友希那×燐子×ミッシェル", "麻弥×瑠唯×燈"], "other")},
    status="in-progress",
))
_add(Game(
    key="sdvx", name="SOUND VOLTEX", lanes=6, variable_width=False,   # 4 BT + 2 FX; lasers via KNOB notes
    # NOV/ADV/EXH, then one 4th chart per song (MXM/INF/GRV/HVN/VVD/XCD/NBL) = master, ULT (5th) = extra.
    # A song never has two 4th charts (checked); if one did, the second would go to master-2 (sources/sdvx.py).
    difficulties={"nov": "normal", "adv": "hard", "exh": "expert", "mxm": "master", "inf": "master",
                  "grv": "master", "hvn": "master", "vvd": "master", "xcd": "master", "nbl": "master",
                  "ult": "extra",
                  "novice": "normal", "advanced": "hard", "exhaust": "expert", "maximum": "master",
                  "infinite": "master", "gravity": "master", "heavenly": "master", "vivid": "master",
                  "exceed": "master", "nabla": "master", "ultimate": "extra"},
    native_names={"nov": "NOVICE", "adv": "ADVANCED", "exh": "EXHAUST", "mxm": "MAXIMUM", "inf": "INFINITE",
                  "grv": "GRAVITY", "hvn": "HEAVENLY", "vvd": "VIVID", "xcd": "EXCEED", "nbl": "NABLA",
                  "ult": "ULTIMATE"},
    genres={"FLOOR": "floor", "BEMANI": "bemani", "東方アレンジ": "touhou-arrange", "SDVXオリジナル": "sdvx-original",
            "ボーカロイド": "vocaloid", "その他": "other", "EXIT TUNES": "exit-tunes",
            "ひなビタ♪/バンめし♪": "hinabita-banmeshi", "POPS&アニメ": "pops-anime", "": "unknown"},
))
_add(Game(
    key="ddr", name="DanceDanceRevolution", lanes=4, variable_width=False,   # SINGLE 4 panels; DOUBLE charts are 8
    difficulties={"beginner": "easy", "basic": "normal", "difficult": "hard", "expert": "expert",
                  "challenge": "master",
                  # second play style: "double-<slot>", 8 lanes (docs/sources/ddr.md)
                  "double basic": "double-normal", "double difficult": "double-hard",
                  "double expert": "double-expert", "double challenge": "double-master"},
    native_names={**{k: k.upper() for k in ["beginner", "basic", "difficult", "expert", "challenge"]},
                  **{f"double {k}": f"DOUBLE {k.upper()}" for k in ["basic", "difficult", "expert", "challenge"]}},
    # genre = the version folder the song was added in (DDRCardDraw `folder`); slugs sort in release order
    genres={"DanceDanceRevolution 1st Mix": "01-1st-mix", "DanceDanceRevolution 2nd Mix": "02-2nd-mix",
            "DanceDanceRevolution 3rd Mix": "03-3rd-mix", "DanceDanceRevolution 4th Mix": "04-4th-mix",
            "DanceDanceRevolution 5th Mix": "05-5th-mix", "DDRMAX -DanceDanceRevolution 6thMIX-": "06-ddrmax",
            "DDRMAX2 -DanceDanceRevolution 7thMIX-": "07-ddrmax2", "DanceDanceRevolution EXTREME": "08-extreme",
            "DanceDanceRevolution SuperNOVA": "09-supernova", "DanceDanceRevolution SuperNOVA2": "10-supernova2",
            "DanceDanceRevolution X": "11-x", "DanceDanceRevolution X2": "12-x2",
            "DanceDanceRevolution X3 vs 2nd MIX": "13-x3-vs-2ndmix", "DanceDanceRevolution (2013)": "14-2013",
            "DanceDanceRevolution (2014)": "15-2014", "DanceDanceRevolution A": "16-a",
            "DanceDanceRevolution A20": "17-a20", "DanceDanceRevolution A20 PLUS": "18-a20-plus",
            "DanceDanceRevolution A3": "19-a3", "DanceDanceRevolution World": "20-world"},
    status="in-progress",
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
    # SP on the ladder; DP (the second play style) as double-<slot>. Folders are versions ("34 ZINRAI"), not genres.
    difficulties={"beginner": "easy", "normal": "normal", "hyper": "hard", "another": "expert",
                  "leggendaria": "master",
                  "spb": "easy", "spn": "normal", "sph": "hard", "spa": "expert", "spl": "master",
                  "dpb": "double-easy", "dpn": "double-normal", "dph": "double-hard", "dpa": "double-expert",
                  "dpl": "double-master"},
    native_names={**{k: k.upper() for k in ["beginner", "normal", "hyper", "another", "leggendaria"]},
                  **{f"{s}{d[0]}": f"{s.upper()} {d.upper()}" for s in ("sp", "dp")
                     for d in ["beginner", "normal", "hyper", "another", "leggendaria"]}},
))
_add(Game(
    key="guitarhero", name="Guitar Hero", lanes=5, variable_width=False,
    # Lead guitar uses the ladder; other parts are prefixed: coop-, bass-, rhythm-, drums- (drums Expert+ = master).
    difficulties={"easy": "easy", "medium": "normal", "hard": "hard", "expert": "expert", "expert+": "master"},
    native_names={"easy": "EASY", "medium": "MEDIUM", "hard": "HARD", "expert": "EXPERT", "expert+": "EXPERT+"},
    # Genre = the game (that is how the games sort their songs). Song ids are <slug>-<title-slug>.
    genres={"Guitar Hero": "gh1", "Guitar Hero II": "gh2", "Guitar Hero Encore: Rocks the 80s": "gh80s",
            "Guitar Hero III: Legends of Rock": "gh3", "Guitar Hero: Aerosmith": "gha",
            "Guitar Hero World Tour": "ghwt", "Guitar Hero: Metallica": "ghm", "Guitar Hero Smash Hits": "ghsh",
            "Guitar Hero 5": "gh5", "Guitar Hero: Van Halen": "ghvh", "Guitar Hero: Warriors of Rock": "ghwor",
            "Guitar Hero Live": "ghl"},
    status="in-progress",
))
_add(Game(
    key="gitadora", name="GITADORA", lanes=5, variable_width=False,
    # Three parts, no unprefixed default: guitar-<slot>, bass-<slot>, drum-<slot>. Folders are first versions.
    difficulties={f"{p}-{d}": f"{p}-{s}" for p in ("guitar", "bass", "drum")
                  for d, s in (("bsc", "normal"), ("adv", "hard"), ("ext", "expert"), ("mas", "master"))},
    native_names={f"{p}-{d}": f"{p.upper()} {n}" for p in ("guitar", "bass", "drum")
                  for d, n in (("bsc", "BASIC"), ("adv", "ADVANCED"), ("ext", "EXTREME"), ("mas", "MASTER"))},
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
