"""Project SEKAI: the SUS reader (formats/sus.py) and the source adapter (sources/sekai.py)."""
import json
from fractions import Fraction
from pathlib import Path

from vsrgconv.convert.pipeline import convert
from vsrgconv.db import ChartDB
from vsrgconv.formats.sus import half_beat_ticks, sekai_combo, sus_to_chart
from vsrgconv.games.registry import canonical_difficulty, genre_slug
from vsrgconv.model import Chart, ChartMeta, NoteKind
from vsrgconv.osu.reader import parse_osu
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
SUS = (FIX / "sekai_sample.sus").read_text("utf-8")
MASTER = json.loads((FIX / "sekai_master_sample.json").read_text("utf-8"))


def parsed() -> Chart:
    return sus_to_chart(SUS, ChartMeta(game="sekai", song_id="t", title="T", difficulty="master"))


def by(chart: Chart, beat, lane) -> list:
    return [n for n in chart.notes if n.beat == Fraction(beat) and n.lane == lane]


# ---------------------------------------------------------------- SUS reader

def test_sus_timing_is_exact():
    c = parsed()
    assert c.lanes == 12
    assert [(b.beat, b.bpm) for b in c.timing.bpms] == [(0, 120.0), (8, 240.0)]
    # #00302: 3 -> measure 3 is 3/4, measure 4 back to 4/4
    assert [(m.beat, m.numerator, m.denominator) for m in c.timing.meters] == [(0, 4, 4), (12, 3, 4), (15, 4, 4)]
    # #TIL00 items arrive unsorted; 1'960 = measure 1 + 960 ticks = beat 6
    assert [(s.beat, s.speed) for s in c.timing.scrolls] == [(0, 1.0), (6, 0.5), (8, 1.0)]
    assert c.timing.offset_ms == 0
    assert all(isinstance(n.beat, Fraction) and isinstance(n.duration, Fraction) for n in c.notes)


def test_sus_every_note_type():
    c = parsed()
    assert c.count_by_kind() == {"tap": 2, "ex_tap": 2, "flick": 3, "mine": 1, "slide": 2, "hold": 3, "other": 1}
    assert by(c, 4, 0)[0].kind is NoteKind.TAP and by(c, 4, 0)[0].width == 3      # SUS lane 2 -> IR lane 0
    assert by(c, 5, 3)[0].kind is NoteKind.EX_TAP and by(c, 5, 3)[0].extra["critical"]
    up = by(c, 6, 6)[0]
    assert up.kind is NoteKind.FLICK and up.extra == {"direction": "up"}
    crit_flick = by(c, 7, 9)[0]
    assert crit_flick.extra == {"direction": "up-left", "critical": True}
    assert by(c, 8, 0)[0].extra == {"trace": True} and by(c, 8, 0)[0].kind is NoteKind.TAP
    assert by(c, 9, 3)[0].kind is NoteKind.EX_TAP and by(c, 9, 3)[0].extra == {"critical": True, "trace": True}
    assert by(c, 10, 6)[0].extra == {"direction": "up-right", "trace": True}
    assert by(c, 11, 9)[0].kind is NoteKind.MINE
    assert "stacked taps identical: 1" in c.warnings                                # the doubled #00112 line


def test_sus_slide_points_easing_and_attach():
    c = parsed()
    s = by(c, 12, 0)[0]
    assert s.kind is NoteKind.SLIDE and s.duration == 5 and s.width == 3
    assert [(p.beat, p.lane, p.easing) for p in s.path] == [(1, 2, "in"), (2, 4, "out"), (5, 6, "linear")]
    assert s.extra["points"] == ["tick", "hidden", "end"]
    assert s.extra["attach"] == ["7/2"]                       # relay + tap type 3: a tick on the curve, not a point
    assert s.extra["critical"] and s.extra["start"] == "tap" and s.extra["end_direction"] == "up"
    # channel 0 is reused at beat 17: the second slide's start line comes before the first one's end line
    hold = by(c, 17, 8)[0]
    assert hold.kind is NoteKind.HOLD and hold.duration == 1 and hold.extra["start"] == "tap"
    hidden_end = by(c, 19, 8)[0]
    assert hidden_end.kind is NoteKind.HOLD and hidden_end.extra["end"] == "hidden"
    traced = by(c, 19, 0)[0]
    assert traced.kind is NoteKind.SLIDE and traced.extra["start"] == traced.extra["end"] == "trace"
    assert by(c, 12, 9)[0].extra.get("sus_hold")              # #mmm2xy hold on a playable lane


def test_sus_guides_events_and_unrepresented():
    c = parsed()
    guide = by(c, 8, 1)[0]
    assert guide.kind is NoteKind.OTHER and guide.extra["guide"]
    assert guide.extra["color"] == "yellow" and guide.extra["fade"] == "none" and guide.duration == 2
    assert c.meta.source["sus_events"] == [{"beat": "4", "event": "skill"}, {"beat": "4", "event": "fever_chance"},
                                           {"beat": "8", "event": "super_fever"}]
    assert c.meta.source["sus_unrepresented"] == ["#0012f0:11", "#0022f0:21", "#00140:11"]
    assert any(w.startswith("not represented: #mmm4x×1") for w in c.warnings)
    assert c.meta.source["sus"]["request"] == "ticks_per_beat 480"


def test_sus_combo_counts_half_beat_ticks():
    assert half_beat_ticks(Fraction(12), Fraction(17)) == 9                       # 12.5 .. 16.5
    assert half_beat_ticks(Fraction(1, 4), Fraction(1, 2)) == 0
    assert half_beat_ticks(Fraction(1, 4), Fraction(3, 4)) == 1
    combo = parsed().meta.source["combo"]
    # singles 7; slides: 5 heads (one hidden-end hold still has its head), 4 tails, 1 relay tick, 1 attached tick,
    # 9 + 5 + 1 + 2 + 2 half-beat ticks (worked out by hand from the fixture)
    assert combo == {"singles": 7, "slide_heads": 5, "slide_tails": 4, "slide_ticks": 1, "attached_ticks": 1,
                     "half_beat_ticks": 19, "total": 37}


def test_sus_hidden_end_with_flick_is_still_judged():
    # 690 APPEND has the only one: tap type 8 (critical, no judgement) + flick up on a slide end. The published
    # combo (1171) counts that end; without it the reader gets 1170.
    sus = "#00002: 4\n#BPM01: 120\n#00008: 01\n#000320: 13002300\n#00012: 23000000\n#00012: 00008300\n" \
          "#00052: 00001300\n"     # slide beat 0 -> 2 at lane 2; critical start; end: type 8 + flick up
    c = sus_to_chart(sus, ChartMeta(game="sekai", song_id="t", title="T"))
    s = c.notes[0]
    assert s.extra["end"] == "hidden" and s.extra["end_direction"] == "up" and s.extra["critical"]
    assert c.meta.source["combo"]["slide_tails"] == 1 and c.meta.source["combo"]["total"] == 1 + 1 + 3


def test_sus_chart_round_trips_and_combo_survives(tmp_path):
    c = parsed()
    back = Chart.from_json(c.to_json())
    assert back.to_dict() == c.to_dict()
    assert sekai_combo(back) == c.meta.source["combo"]


def test_sus_chart_converts_end_to_end():
    # 4 keys: the fixture's few notes reach every column under the placeholder centre mapping (at 6 keys column 1
    # gets nothing and lint rightly reports an empty column)
    conv = convert(parsed(), keys=4)
    assert conv.lint.ok, conv.lint.errors
    m = parse_osu(conv.osu)
    assert m.mode == 3 and m.keys == 4 and len(m.objects) == len(conv.mania.notes) > 0
    assert {r["rule"]: r for r in conv.report}["kinds"]["counts"] == {"mine": 1, "other": 1}


# ---------------------------------------------------------------- adapter

def _cache(f: Fetcher, url: str, data: bytes) -> None:
    p = f.cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _offline_master(tmp_path) -> Fetcher:
    from vsrgconv.sources.sekai import BPM_URL, MASTER_DB

    f = Fetcher(tmp_path / "cache", offline=True)
    for name, rows in MASTER.items():
        url = BPM_URL if name == "music_bpms" else MASTER_DB.format(name=name)
        _cache(f, url, json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    return f


def test_sekai_registry_and_genres():
    from vsrgconv.sources.sekai import unit_genre

    assert canonical_difficulty("sekai", "append") == "extra"
    assert unit_genre({"all", "vocaloid"}) == "VIRTUAL SINGER"
    assert unit_genre({"all", "vocaloid", "street"}) == "Vivid BAD SQUAD"
    assert unit_genre({"all", "vocaloid", "other"}) == "その他"
    assert unit_genre({"idol", "street", "vocaloid"}) == "その他"                 # several units
    assert genre_slug("sekai", unit_genre({"school_refusal"})) == "nightcord-at-25"


def test_sekai_romanize():
    from vsrgconv.sources.sekai import romanize

    assert romanize("じゃっくぽっとさっどがーる") == "jakkupottosaddogaaru"
    assert romanize("ちょっと") == "chotto" and romanize("まっち") == "matchi"
    assert romanize("ティーパーティー") == "tiipaatii" and romanize("ゔぁんぷ") == "vanpu"


def test_sekai_catalog_offline(tmp_path):
    from vsrgconv.sources.sekai import SUS_SOURCES, _catalog

    f = _offline_master(tmp_path)
    # song 76 has no MoeSekai BPM: the catalog reads it from the master chart's #BPM lines
    _cache(f, SUS_SOURCES[0][1].format(id=76, diff="master"), SUS.encode("utf-8"))
    songs, report = _catalog(f)
    by_id = {s.song_id: s for s in songs}
    assert report["unreleased"] == ["999"] and by_id["999"].extra["unreleased"]
    one = by_id["1"]
    assert one.genre == "VIRTUAL SINGER" and one.bpm == "150" and one.date_added == "2019-06-10"
    assert list(one.charts) == ["easy", "normal", "hard", "expert", "master", "extra"]
    assert one.charts["extra"].native == "APPEND" and one.charts["master"].note_counts == {"combo": 1147}
    assert one.extra["composer"] == "kz" and one.extra["vocals"][0]["singers"] == ["初音ミク"]
    sekai = by_id["76"]
    assert sekai.genre == "その他" and sekai.title_romaji == "sekai" and sekai.dir_name == "76-sekai"
    assert sekai.bpm == "120-240 (120)" and sekai.extra["bpm_source"] == "sus"
    assert report["bpm_from_sus"] == ["76"]
    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    assert (tmp_path / "db" / "sekai" / "virtual-singer" / "1-tell-your-world" / "song.json").exists()


def test_sekai_chart_source_uses_sus_with_fallback_and_checks_combo(tmp_path):
    from vsrgconv.sources.sekai import JACKET_SOURCES, SUS_SOURCES, _catalog, _jacket, chart_from_sus, sus_urls

    f = _offline_master(tmp_path)
    songs, _ = _catalog(f)
    song = next(s for s in songs if s.song_id == "1")
    assert sus_urls(song, "extra")[0][1].endswith("/music_score/0001_01/append.txt")
    assert sus_urls(song, "extra")[1][1].endswith("/Sekai-Chart-Json/HEAD/1/append.sus")
    _cache(f, sus_urls(song, "master")[0][1], SUS.encode("utf-8"))
    chart = chart_from_sus(f, song, "master")
    assert chart.meta.source["extracted_from"] == "pjsekai-decrypted-data"
    assert chart.meta.level == "26" and chart.meta.difficulty_native == "MASTER" and chart.lanes == 12
    assert chart.meta.source["filler_sec"] == 9.0
    assert "combo 37 differs from the published note count 1147" in chart.warnings
    _cache(f, sus_urls(song, "expert")[1][1], SUS.encode("utf-8"))            # only the fallback has it
    song.charts["expert"].note_counts["combo"] = 37
    fallback = chart_from_sus(f, song, "expert")
    assert fallback.meta.source["extracted_from"] == "sekai-chart-json"
    assert not any("differs" in w for w in fallback.warnings)
    assert any("fallback source" in w for w in fallback.warnings)
    assert chart_from_sus(f, song, "hard") is None                           # in neither source -> None, no crash
    assert _jacket(f, song) is None
    _cache(f, JACKET_SOURCES[1].format(asset="jacket_s_001"), b"png")
    assert _jacket(f, song) == (b"png", "png", JACKET_SOURCES[1].format(asset="jacket_s_001"))
    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    db.save_chart(chart)
    stored = db.get_song("sekai", "1").charts["master"]
    assert stored.status == "extracted" and stored.source["extracted"]["counts"]["slide"] == 2
    assert db.load_chart("sekai", "1", "master").to_dict() == chart.to_dict()
