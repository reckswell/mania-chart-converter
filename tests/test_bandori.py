import json
from fractions import Fraction
from pathlib import Path

from vsrgconv.db import ChartDB
from vsrgconv.formats.bestdori import (
    bestdori_combo,
    bestdori_to_chart,
    chart_combo,
    exact_beat,
)
from vsrgconv.games.registry import canonical_difficulty, genre_slug, native_name
from vsrgconv.model import Chart, ChartMeta, NoteKind
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
SAMPLE = json.loads((FIX / "bandori_sample.json").read_text("utf-8"))


def meta(**kw):
    return ChartMeta(game="bandori", song_id="1", title="T", difficulty="expert", **kw)


# ---------------------------------------------------------------- beats

def test_exact_beat_rule():
    assert exact_beat(7) == (Fraction(7), 0.0)
    assert exact_beat(0.5) == (Fraction(1, 2), 0.0)
    assert exact_beat(13.020833333333334)[0] == Fraction(2500, 192)        # on the 1/192 grid
    assert exact_beat(336.6)[0] == Fraction(1683, 5)                       # quintuplet
    assert exact_beat(290.11764705882354)[0] == Fraction(4932, 17)         # 1/17 slot from the game's file
    assert exact_beat(14.844919786096256)[0] == Fraction(2776, 187)
    assert exact_beat(10.333333)[0] == Fraction(31, 3)                     # few printed digits: within 1e-6
    beat, err = exact_beat(20.0001234)                                     # nothing simple: nearest 1/192 + error
    assert beat == 20 and 1e-4 < err < 2e-4


# ---------------------------------------------------------------- parser

def test_bestdori_parses_every_entry_type():
    c = bestdori_to_chart(SAMPLE, meta())
    assert c.lanes == 7
    assert [(b.beat, b.bpm) for b in c.timing.bpms] == [(0, 150.0), (16, 180.5)]
    assert c.count_by_kind() == {"tap": 8, "flick": 4, "hold": 3, "slide": 1}
    assert c.meta.audio == "bgm999.wav"
    assert c.meta.source["bestdori_system"] == [["0", "bgm999.wav"], ["8", "cmd_fever_ready.wav"],
                                                ["10", "cmd_fever_start.wav"], ["20", "cmd_fever_end.wav"]]
    by = {(n.beat, n.kind): n for n in c.notes}

    assert by[(2, NoteKind.FLICK)].extra == {"direction": "up"} and by[(2, NoteKind.FLICK)].lane == 6
    assert by[(3, NoteKind.TAP)].extra == {"skill": True}
    assert by[(9, NoteKind.TAP)].extra == {"charge": True}
    assert by[(Fraction(19, 2), NoteKind.FLICK)].extra == {"direction": "up", "charge": True}

    left, right = by[(4, NoteKind.FLICK)], by[(Fraction(9, 2), NoteKind.FLICK)]
    assert (left.lane, left.width, left.extra["direction"]) == (0, 3, "left")     # anchor 2 is the rightmost tile
    assert (right.lane, right.width, right.extra["direction"]) == (4, 2, "right")

    long1, long2 = by[(5, NoteKind.HOLD)], by[(Fraction(13, 2), NoteKind.HOLD)]
    assert long1.lane == 1 and long1.duration == 1 and long1.extra == {"skill": True, "flick_end": True,
                                                                       "direction": "up"}
    assert long2.duration == Fraction(3, 4) and long2.extra == {"charge": True, "charge_end": True}

    slide = by[(11, NoteKind.SLIDE)]
    assert slide.lane == 0 and slide.duration == 1
    assert [(p.beat, p.lane, p.easing) for p in slide.path] == [(Fraction(1, 2), 1, "linear"),
                                                              (Fraction(3, 4), 0.35, "control"),
                                                              (1, 3, "linear")]
    assert slide.extra == {"flick_end": True, "charge_ticks": ["1/2"], "direction": "up"}

    static = by[(13, NoteKind.HOLD)]                                    # a Slide that never moves
    assert static.lane == 6 and static.duration == 1 and static.extra == {"ticks": ["1/2"], "static_slide": True}
    one = by[(Fraction(31, 2), NoteKind.TAP)]                           # hidden ends trimmed -> one point -> Single
    assert one.lane == 2 and one.extra == {"line": "Slide"}

    beats = {n.beat for n in c.notes}
    assert {Fraction(1683, 5), Fraction(4932, 17), Fraction(31, 3), Fraction(20)} <= beats
    assert all(isinstance(n.beat, Fraction) and isinstance(n.duration, Fraction) for n in c.notes)
    assert any("1 beats are not within 1e-6" in w for w in c.warnings)
    assert "1 Long/Slide entries with no visible point dropped" in c.warnings
    assert "not represented: Fever×1" in c.warnings and c.meta.source["bestdori_unrepresented"] == [SAMPLE[-1]]


def test_bestdori_combo_counts_visible_points():
    # 9 Singles + 2 Directional + 2 Longs (2 each) + moving Slide 3 + static Slide 3 + one-point Slide 1
    assert bestdori_combo(SAMPLE) == 22
    c = bestdori_to_chart(SAMPLE, meta())
    assert c.meta.source["combo"] == chart_combo(c) == 22
    back = Chart.from_json(c.to_json())
    assert back.to_dict() == c.to_dict() and chart_combo(back) == 22


def test_bestdori_wrapper_and_lossy_copy():
    wrapped = {"schema_version": 1, "source": {"url": "https://bestdori.com/api/charts/1/expert.json"},
               "chart": SAMPLE}
    assert bestdori_to_chart(wrapped, meta()).to_dict() == bestdori_to_chart(SAMPLE, meta()).to_dict()
    c = bestdori_to_chart(json.dumps(kanon_copy(SAMPLE)), meta(), lossy="lossy copy")
    assert "lossy copy" in c.warnings and chart_combo(c) == 22
    assert not any(n.extra.get("static_slide") for n in c.notes)        # Long and Slide cannot be told apart
    assert "bestdori_system" not in c.meta.source


def kanon_copy(entries):
    """What kanon511's files look like: Long written as Slide, no System entries, no charge flags."""
    out = []
    for e in entries:
        if e["type"] == "System":
            continue
        e = json.loads(json.dumps(e))
        e.pop("charge", None)
        for p in e.get("connections", []):
            p.pop("charge", None)
        if e["type"] == "Long":
            e["type"] = "Slide"
        out.append(e)
    return out


# ---------------------------------------------------------------- registry

def test_bandori_ladder_and_genres():
    assert canonical_difficulty("bandori", "special") == "master" and native_name("bandori", "special") == "SPECIAL"
    assert genre_slug("bandori", "ハロー、ハッピーワールド！") == "hello-happy-world"
    assert genre_slug("bandori", "MyGO!!!!!") == "mygo"
    assert genre_slug("bandori", "Afterglow×香澄") == "other"            # a collaboration, not Afterglow's folder


# ---------------------------------------------------------------- adapter (offline)

def _put(f: Fetcher, url: str, data) -> None:
    p = f.cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8"))


FAR = 4102369200000          # 2100: "never closes" in the master data
PAST = 1593496799000


def _offline_catalog(tmp_path) -> Fetcher:
    from vsrgconv.sources import bandori as b

    f = Fetcher(tmp_path / "cache", offline=True)
    music = [
        {"musicId": 1, "musicTitle": "Yes! BanG_Dream!", "bandId": 1, "lyricist": "L", "composer": "C",
         "arranger": "A", "tag": "normal", "jacketImage": "yes_bang_dream", "publishedAt": 1462071600000,
         "closedAt": FAR, "musicDataType": "normal", "ruby": "yesbangdream", "bgmId": "bgm001"},
        {"musicId": 273, "musicTitle": "躄final phase", "bandId": 24, "tag": "normal", "jacketImage": "fp",
         "publishedAt": 1592546400000, "closedAt": PAST, "musicDataType": "collabo_original"},
        {"musicId": 800, "musicTitle": "未来の曲", "bandId": 45, "tag": "normal", "jacketImage": "x",
         "publishedAt": FAR - 1, "closedAt": FAR, "musicDataType": "normal"},
    ]
    diffs = [{"musicId": 1, "difficulty": d, "playLevel": lv, "notesQuantity": q}
             for d, lv, q in [("easy", 5, 60), ("normal", 10, 1000), ("hard", 17, 330), ("expert", 22, 1000),
                              ("special", 26, 1000)]]
    diffs[-1]["publishedAt"] = 1603972800000
    diffs += [{"musicId": 273, "difficulty": d, "playLevel": 20, "notesQuantity": 1000} for d in ("easy", "expert")]
    diffs += [{"musicId": 800, "difficulty": "hard", "playLevel": 20, "notesQuantity": 1000},
              {"musicId": 99, "difficulty": "easy", "playLevel": 1, "notesQuantity": 50}]
    bands = {"1": {"bandName": "Poppin'Party"}, "24": {"bandName": "コラボレーション"}, "45": {"bandName": "MyGO!!!!!"}}
    jackets = {"1": [{"jacketImage": "yes_bang_dream", "startAt": 1462071600000}]}
    greatdori = {"1": {"musicTitle": ["Yes! BanG_Dream!", "Yes! BanG_Dream!"], "length": 120.048,
                       "difficulty": {"0": {"playLevel": 5}, "2": {"playLevel": 17}, "3": {"playLevel": 22}},
                       "notes": {"0": 22, "2": 21, "3": 22},
                       "bpm": {"3": [{"bpm": 150, "start": 0, "end": 6}, {"bpm": 180.5, "start": 6, "end": 120}]}}}
    manifest = {"songs": [
        {"bestdori_song_id": 1,
         "jackets": [{"server": "cn", "name": "yes_bang_dream", "path": "bestdori/1/jacket-cn-1.png"},
                     {"server": "jp", "name": "yes_bang_dream", "path": "bestdori/1/jacket-jp-1.png"}],
         "difficulties": {d: {"path": f"bestdori/1/{d}.json", "expected_notes": n,
                              "source_url": f"https://bestdori.com/api/charts/1/{d}.json"}
                          for d, n in [("hard", 21), ("expert", 22)]}},
        {"bestdori_song_id": 800, "jackets": [],
         "difficulties": {"hard": {"path": "bestdori/800/hard.json", "expected_notes": 22}}}]}
    for key, doc in zip(("music", "difficulty", "band", "jacket"), (music, diffs, bands, jackets)):
        _put(f, b.MASTER + b.MASTER_FILES[key], doc)
    _put(f, b.GREATDORI_SONGS, greatdori)
    _put(f, b.MAA_MANIFEST, manifest)
    for path in ("bestdori/1/hard.json", "bestdori/1/expert.json", "bestdori/800/hard.json"):
        _put(f, b.MAA + path, {"schema_version": 1, "source": {"url": "https://bestdori.com/x"}, "chart": SAMPLE})
    _put(f, b.KANON.format(id=1, index=0, level=5), kanon_copy(SAMPLE))
    _put(f, b.MAA + "bestdori/1/jacket-jp-1.png", b"png")
    return f


def test_bandori_catalog_offline(tmp_path):
    from vsrgconv.sources import load_all
    from vsrgconv.sources.bandori import _catalog

    assert "bandori" in load_all()
    songs, report = _catalog(_offline_catalog(tmp_path))
    by = {s.song_id: s for s in songs}
    assert sorted(by) == ["1", "273", "800"]
    s1 = by["1"]
    assert (s1.title, s1.artist, s1.genre, s1.bpm, s1.date_added) == ("Yes! BanG_Dream!", "Poppin'Party",
                                                                     "Poppin'Party", "150-180.5", "2016-05-01")
    assert list(s1.charts) == ["easy", "normal", "hard", "expert", "master"]
    sp = s1.charts["master"]
    assert (sp.native, sp.level, sp.source["notesQuantity"]) == ("SPECIAL", "26", 1000)
    assert s1.charts["expert"].note_counts == {"combo": 22} and s1.charts["easy"].source["bestdori"]["level"] == 5
    assert s1.charts["hard"].source["maa"]["path"] == "bestdori/1/hard.json"
    assert s1.source["maa"]["jacket"]["server"] == "jp"
    assert s1.extra["composer"] == "C" and s1.extra["length_s"] == 120.048 and "removed" not in s1.extra
    s273 = by["273"]
    assert s273.title == "[原曲] final phase" and s273.extra["title_raw"] == "躄final phase"
    assert s273.extra["removed"] and s273.genre_slug == "other"
    assert by["800"].extra["unreleased"] and by["800"].bpm == "150-180.5"     # BPM read from its mirrored chart
    assert report["removed_songs"] == 1 and report["unreleased_songs"] == 1
    assert report["difficulty_rows_without_song"] == [99] and report["songs_not_in_mirror"] == ["273"]
    assert report["no_bpm"] == ["273"] and report["bpm_from_chart"] == 1 and report["unmapped_bands"] == []


def test_bandori_chart_sources_offline(tmp_path):
    from vsrgconv.sources.bandori import _catalog, _chart, _jacket, kanon_urls

    f = _offline_catalog(tmp_path)
    songs, _ = _catalog(f)
    s1 = next(s for s in songs if s.song_id == "1")
    expert = _chart(f, s1, "expert")
    assert expert.meta.source["extracted_from"] == "maabangdream-bestdori" and expert.meta.level == "22"
    assert expert.meta.source["bestdori_url"] == "https://bestdori.com/x"
    assert not any("combo" in w or "notesQuantity" in w for w in expert.warnings)   # 22 = Bestdori's 22
    hard = _chart(f, s1, "hard")
    assert "combo 22 differs from Bestdori's count 21" in hard.warnings
    assert any(w.startswith("master notesQuantity 330 differs from combo 22") for w in hard.warnings)
    easy = _chart(f, s1, "easy")                                     # EASY: only the lossy kanon511 copy
    assert kanon_urls(s1, "easy")[0].endswith("/charts/1-0_5.json")
    assert easy.meta.source["extracted_from"] == "kanon511-bestdori-api"
    assert any(w.startswith("lossy copy (kanon511") for w in easy.warnings)
    assert any(w.startswith("master notesQuantity 60 differs") for w in easy.warnings)
    assert _chart(f, s1, "normal") is None                           # no file anywhere -> missing, no crash
    jp = "https://raw.githubusercontent.com/coatcn1/MaaBanGDream/HEAD/resource/charts/bestdori/1/jacket-jp-1.png"
    assert _jacket(f, s1) == (b"png", "png", jp)
    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    p = db.save_chart(expert)
    assert p.parent.name == "1-yes-bang-dream" and p.parent.parent.name == "poppin-party"
    rec = db.get_song("bandori", "1").charts["expert"]
    assert rec.status == "extracted" and rec.source["extracted"]["from"] == "maabangdream-bestdori"
    assert db.load_chart("bandori", "1", "expert").to_dict() == expert.to_dict()


def test_bandori_bestdori_api_fallback(tmp_path):
    from vsrgconv.sources import bandori as b

    f = _offline_catalog(tmp_path)
    songs, _ = b._catalog(f)
    s273 = next(s for s in songs if s.song_id == "273")              # not in the mirror, no kanon511 file
    assert b._chart(f, s273, "easy") is None
    _put(f, b.BESTDORI_CHART.format(id=273, diff="easy"), SAMPLE)    # e.g. fetched on the PC
    easy = b._chart(f, s273, "easy")
    assert easy.meta.source["extracted_from"] == "bestdori-api" and not any("lossy" in w for w in easy.warnings)
    url = b.bestdori_jacket_url(273, s273.source["master"]["jacketImage"])
    assert "/musicjacket280_rip/" in url and url.endswith("-musicjacket280-fp-jacket.png")
    assert b.bestdori_jacket_url(236, "236_ aquarion").endswith("-236_%20aquarion-jacket.png")
    assert b._jacket(f, s273) is None
    _put(f, url, b"png")
    assert b._jacket(f, s273) == (b"png", "png", url)


def test_unreachable_host_is_asked_once(tmp_path, monkeypatch):
    from vsrgconv.sources import bandori as b
    from vsrgconv.sources.fetch import FetchError

    monkeypatch.setattr(b, "_unreachable", set())
    calls = []

    class Down(Fetcher):
        def get(self, url, refresh=False):
            calls.append(url)
            raise FetchError(f"{url}: Tunnel connection failed: 403 Forbidden")

    f = Down(tmp_path / "cache")
    assert b._optional(f, "https://bestdori.com/api/charts/1/easy.json") is None
    assert b._optional(f, "https://bestdori.com/api/charts/2/easy.json") is None
    assert len(calls) == 1 and b._unreachable == {"bestdori.com"}
