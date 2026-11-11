"""StepMania parser (formats/stepmania.py) and the DDR adapter (sources/ddr.py)."""
import json
from fractions import Fraction
from pathlib import Path

import pytest

from vsrgconv.db import ChartDB
from vsrgconv.formats.stepmania import (chart_to_ir, count_steps, normalize_difficulty, parse_msd,
                                        parse_simfile)
from vsrgconv.games.registry import genre_slug
from vsrgconv.model import ChartMeta, NoteKind
from vsrgconv.sources import ddr
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
F = Fraction


def meta(**kw):
    return ChartMeta(game="ddr", song_id="t", title="T", **kw)


def sm_fixture():
    return parse_simfile((FIX / "ddr_sample.sm").read_text("utf-8"), "sm")


def notes_of(chart):
    return {(n.beat, int(n.lane), n.kind.value, n.duration) for n in chart.notes}


# ---------------------------------------------------------------- MSD syntax

def test_msd_comments_escapes_and_missing_semicolon():
    values = parse_msd("#A:x\\:y; // c:d;\n#B:one // gone\n#C:two:three;\n#D:mid#dle;")
    assert values == [["A", "x:y"], ["B", "one"], ["C", "two", "three"], ["D", "mid#dle"]]


def test_sm_headers_and_charts():
    sim = sm_fixture()
    assert sim.format == "sm" and sim.song_id == "test"
    assert sim.title == "Fixture #1: Test" and sim.tags["SUBTITLE"] == "no semicolon here"
    assert sim.artist == "vsrgconv" and sim.tags["TITLETRANSLIT"] == "Fixture One"
    assert [(c.steps_type, c.difficulty_name, c.meter) for c in sim.charts] == [
        ("dance-single", "Hard", "10"), ("dance-single", "Challenge", "12"), ("dance-double", "Hard", "9"),
        ("dance-solo", "Medium", "5")]
    assert [c.columns for c in sim.charts] == [4, 4, 8, 6]
    assert normalize_difficulty("Basic") == "Easy" and normalize_difficulty("Hard", "smaniac") == "Challenge"


# ---------------------------------------------------------------- timing and notes

def test_sm_every_timing_tag():
    sim = sm_fixture()
    c = chart_to_ir(sim, sim.charts[0], meta())
    t = c.timing
    assert t.offset_ms == 250.0                                     # #OFFSET:-0.250 -> beat 0 at 250 ms
    assert [(b.beat, b.bpm) for b in t.bpms] == [(0, 120.0), (8, 240.0), (F(37, 3), 120.0)]  # 12.333 -> 12 1/3
    assert t.stops == [(F(4), 500.0)]
    assert [(m.beat, m.numerator, m.denominator) for m in t.meters] == [(0, 4, 4), (8, 3, 4)]
    # #SCROLLS × #SPEEDS: the speed tweens from 1 to 2 over beat 10-11 (steps every 1/4 beat, midpoint values)
    assert [(s.beat, s.speed) for s in t.scrolls] == [(0, 1.0), (2, 0.5), (10, 0.5625), (F(41, 4), 0.6875),
                                                      (F(21, 2), 0.8125), (F(43, 4), 0.9375), (11, 1.0)]
    assert t.delays == [(F(6), 250.0)] and t.warps == [(F(14), F(1))]
    assert t.ms_at(4) == 250 + 2000 and t.ms_at(5) == 250 + 2000 + 500 + 500   # the stop comes after beat 4
    assert t.ms_at(9) == 250 + 4000 + 500 + 250 + 250                           # + the delay at beat 6
    assert t.ms_at(6) == t.ms_at(F(5999, 1000)) + 250 + 0.5                     # the delay comes before beat 6
    assert t.ms_at(15) == t.ms_at(14)                                           # warped
    src = c.meta.source["stepmania"]
    assert set(src["unrepresented"]) == {"SPEEDS", "FAKES", "TICKCOUNTS"}
    assert src["unrepresented"]["SPEEDS"] == "0.000=1.000=0.000=0,10.000=2.000=1.000=0"
    assert src["timing_from"] == "song" and src["offset_s"] == -0.25 and src["offset_given"] and src["fake_notes"] == 2
    assert any("1 tweened speed changes" in w for w in c.warnings)


def test_sm_every_note_type_with_exact_beats():
    sim = sm_fixture()
    c = chart_to_ir(sim, sim.charts[0], meta())
    assert c.lanes == 4
    want = {
        (0, 0, "tap", 0), (1, 1, "tap", 0), (2, 0, "tap", 0), (2, 3, "tap", 0),
        (4, 0, "hold", 1), (4, 1, "hold", F(3, 2)), (6, 0, "hold", 1),
        (F(15, 2), 0, "mine", 0), (F(15, 2), 1, "mine", 0), (F(15, 2), 2, "mine", 0), (F(15, 2), 3, "mine", 0),
        (8, 0, "tap", 0), (F(25, 3), 1, "other", 0), (F(26, 3), 2, "other", 0), (9, 3, "other", 0),
        (F(31, 3), 2, "tap", 0), (12, 0, "tap", 0), (F(40, 3), 1, "other", 0), (F(44, 3), 2, "other", 0),
    }
    assert notes_of(c) == want
    by = {(n.beat, int(n.lane)): n for n in c.notes}
    assert by[(6, 0)].extra == {"roll": True}
    assert by[(8, 0)].extra == {"lift": True}
    assert by[(F(25, 3), 1)].extra == {"fake": "note", "fake_kind": "tap"}
    assert by[(F(26, 3), 2)].extra == {"keysound": True}
    assert by[(9, 3)].extra == {"sm": "X"}
    assert by[(F(40, 3), 1)].extra == {"fake": "fakes", "fake_kind": "tap"}
    assert by[(F(44, 3), 2)].extra == {"fake": "warp", "fake_kind": "tap"}
    # the hold at 10 is broken by the tap at 10 1/3, and the head at 11 never ends: StepMania drops both
    assert any(w.startswith("2 hold heads without a tail dropped") for w in c.warnings)
    assert any(w.startswith("1 hold tails without a head") for w in c.warnings)
    assert any("'X'" in w for w in c.warnings)


def test_counts_jump_freeze_jump_and_shock_rows():
    sim = sm_fixture()
    c = chart_to_ir(sim, sim.charts[0], meta())
    counts = count_steps(c)
    assert counts == {"steps": 8, "jumps": 2, "taps": 6, "holds": 2, "rolls": 1, "hold_rows": 2, "lifts": 1,
                      "mines": 4, "mine_rows": 1, "fakes": 3}
    assert ddr.ddr_counts(c) == {"steps": 8, "jumps": 2, "freezes": 3, "shocks": 1, "combo": 11, "ex_max": 33}
    double = chart_to_ir(sim, sim.charts[2], meta())
    assert double.lanes == 8 and ddr.ddr_counts(double)["combo"] == 4        # jump, freeze, one shock row of 8


def test_ssc_song_and_chart_timing():
    sim = parse_simfile((FIX / "ddr_sample.ssc").read_text("utf-8"))
    assert sim.format == "ssc" and sim.version == 0.83 and sim.song_id == "sscf"
    medium, challenge, pump = sim.charts
    a = chart_to_ir(sim, medium, meta())
    assert a.meta.source["stepmania"]["timing_from"] == "song"
    assert a.timing.offset_ms == -100.0 and a.timing.stops == [(F(1), 200.0)]
    assert a.timing.ms_at(2) == -100 + 800 + 200
    b = chart_to_ir(sim, challenge, meta())
    src = b.meta.source["stepmania"]
    assert src["timing_from"] == "chart" and src["chart_name"] == "own timing" and src["credit"] == "Someone"
    assert [(x.beat, x.bpm) for x in b.timing.bpms] == [(0, 200.0), (2, 100.0)]
    assert b.timing.stops == [] and b.timing.offset_ms == -100.0    # own timing: no song stops, the song's offset
    assert notes_of(b) == {(0, 0, "tap", 0), (F(3, 2), 1, "tap", 0), (2, 2, "tap", 0), (3, 3, "other", 0)}
    assert "unrepresented" not in src and b.timing.warps == [(F(3), F(1, 2))]      # the warp is in the IR
    p = chart_to_ir(sim, pump, meta())                               # Pump It Up next: 5 panels
    assert p.lanes == 5 and notes_of(p) == {(0, 0, "tap", 0), (0, 4, "tap", 0), (1, 1, "hold", 1), (3, 2, "tap", 0)}


# ---------------------------------------------------------------- DDR catalog

def ddrcd_song(sid, name, artist, charts, folder="DanceDanceRevolution A3", **kw):
    return {"id": sid, "name": name, "artist": artist, "bpm": "150", "folder": folder, "jacket": f"ddr_a3/{name}.png",
            "charts": [{"style": st, "diffClass": dc, "lvl": lvl, **({"maxScore": ms} if ms else {})}
                       for st, dc, lvl, ms in charts], **kw}


def tachi_song(tid, basename, title, artist, song_hash=None):
    data = {"basename": basename, "inGameID": 1, "flareCategory": "WHITE"}
    if song_hash:
        data["ddrSongHash"] = song_hash
    return {"id": tid, "title": title, "artist": artist, "altTitles": [], "data": data}


def fixture_catalog():
    entry = ddr.manifest_entry("public/sm/A3/Fixture/Fixture.sm", sm_fixture())
    variant = dict(entry, path="public/sm/A3/Fixture (Chinese)/Fixture (Chinese).sm", artist="other singer")
    manifest = {"files": [variant, entry,
                          {"path": "public/sm/X/Lonely/Lonely.sm", "title": "Lonely", "translit": "",
                           "artist": "Somebody Else", "song_id": "", "charts": {"single/Hard": ["7", 50, 0, 0, 0]}},
                          {"path": "public/sm/World/Newer/Newer.sm", "title": "Newer", "translit": "",
                           "artist": "N", "song_id": "newr", "charts": {"single/Hard": ["9", 99, 0, 0, 0]}}]}
    ddrcd = {"songs": [
        ddrcd_song("HashOne000000000000000000000000A", "Fixture #1: Test", "vsrgconv",
                   [("single", "beginner", 1, None), ("single", "expert", 10, 33), ("single", "challenge", 12, 3),
                    ("double", "expert", 9, 12)], genre="TEST GENRE", flags=["unlock"]),
        ddrcd_song("HashTwo000000000000000000000000B", "Lonely", "Artist", [("single", "expert", 7, 600)]),
        ddrcd_song("HashThree00000000000000000000000", "Newer", "N", [("single", "expert", 9, 297)],
                   folder="DanceDanceRevolution World"),
    ]}
    tachi = [tachi_song("S1", "test", "Fixture #1: Test", "vsrgconv", "HashOne000000000000000000000000A")]
    charts = {"single": [{"id": "C1", "songID": "S1", "difficulty": "EXPERT", "data": {"stepCount": 11}}],
              "double": [{"id": "C2", "songID": "S1", "difficulty": "EXPERT", "data": {"stepCount": 5}}]}
    return ddr.build_catalog(ddrcd, tachi, charts, manifest)


def test_slots_names_and_genres():
    assert ddr.slot_for("single", "challenge") == "master" and ddr.slot_for("double", "basic") == "double-normal"
    assert ddr.native_for("double", "expert") == "DOUBLE EXPERT" and ddr.native_for("single", "expert") == "EXPERT"
    assert ddr.style_and_class("double-hard") == ("double", "difficult")
    assert ddr.style_and_class("easy") == ("single", "beginner")
    assert genre_slug("ddr", "DanceDanceRevolution A3") == "19-a3"
    assert ddr.norm("RЁVOLUTIФN") == ddr.norm("RЁVOLUTIΦN") == "revolution" and ddr.norm("TЁЯRA") == ddr.norm("TËЯRA")


def test_catalog_joins_tachi_and_simfiles():
    songs, report = fixture_catalog()
    one, lonely, newer = songs
    assert one.song_id == "test" and one.genre == "DanceDanceRevolution A3" and one.extra["ingame_genre"] == "TEST GENRE"
    assert one.source["simfile"]["path"] == "public/sm/A3/Fixture/Fixture.sm"     # not the "(Chinese)" variant
    assert one.source["tachi"]["basename"] == "test"
    assert set(one.charts) == {"easy", "expert", "master", "double-expert"}
    assert one.charts["double-expert"].native == "DOUBLE EXPERT" and one.charts["expert"].level == "10"
    assert one.charts["expert"].note_counts == {"combo": 11}
    assert one.charts["expert"].source["simfile"] == {"chart": "dance-single/Hard", "meter": "10", "steps": 8,
                                                      "freezes": 3, "shocks": 1, "combo": 11}
    assert "simfile" not in one.charts["easy"].source                              # no Beginner in the file
    assert "simfile" not in lonely.source and lonely.song_id == ddr.fallback_id("HashTwo000000000000000000000000B")
    assert newer.song_id == "newr" and newer.source["simfile"]["path"].endswith("Newer.sm")   # id from the header
    assert report["no_simfile"] == ["Lonely"] and report["fallback_ids"] == 1
    assert report["simfile_by_song_id"] == 1 and report["simfile_by_title"] == 1


# ---------------------------------------------------------------- DDR charts and jackets (offline)

def test_ddr_chart_source_offline(tmp_path):
    songs, _ = fixture_catalog()
    one = songs[0]
    f = Fetcher(tmp_path / "cache", offline=True)
    p = f.cache_path(one.source["simfile"]["url"])
    p.parent.mkdir(parents=True)
    p.write_bytes((FIX / "ddr_sample.sm").read_bytes())
    c = ddr.chart_from_simfile(f, one, "expert")
    assert c.meta.difficulty == "expert" and c.meta.difficulty_native == "EXPERT" and c.lanes == 4
    assert c.meta.source["extracted_from"] == "ddr-dan-tool-simfile" and c.meta.source["ddr_counts"]["combo"] == 11
    assert not any("Tachi" in w or "EX max" in w for w in c.warnings)             # both published counts agree
    d = ddr.chart_from_simfile(f, one, "double-expert")
    assert d.lanes == 8 and any("Tachi's stepCount is 5" in w for w in d.warnings)
    assert ddr.chart_from_simfile(f, one, "master").meta.source["stepmania"]["description"] == "SMANIAC"
    assert ddr.chart_from_simfile(f, one, "easy") is None                         # not in the simfile
    assert ddr.chart_from_simfile(f, songs[1], "expert") is None                  # no simfile at all

    cd = f.cache_path(ddr.ddrcd_jacket_url("ddr_a3/Fixture #1: Test.png"))
    cd.parent.mkdir(parents=True)
    cd.write_bytes(b"small")
    assert ddr._jacket(f, one)[0] == b"small"                                     # simfile jacket not there
    jp = f.cache_path(ddr.jacket_url(one.source["simfile"]["path"]))
    jp.write_bytes(b"big")
    assert jp.name == "Fixture-jacket.png" and ddr._jacket(f, one) == (b"big", "png", ddr.jacket_url(
        one.source["simfile"]["path"]))

    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    saved = db.save_chart(d)
    assert saved.name == "double-expert.chart.json.gz" and saved.parent.parent.name == "19-a3"
    assert db.load_chart("ddr", "test", "double-expert").to_dict() == d.to_dict()


def test_ddr_source_is_registered_and_manifest_is_well_formed():
    from vsrgconv.sources import SOURCES

    assert SOURCES["ddr"].chart is ddr.chart_from_simfile and SOURCES["ddr"].jacket is ddr._jacket
    m = ddr.load_manifest()
    assert m["chart_fields"] == ddr.MANIFEST_CHART_FIELDS and m["files"]
    for row in m["files"]:
        assert row["path"].startswith("public/sm/") and row["path"].endswith((".sm", ".ssc"))
        assert row["path"].split("/")[2] in ddr.DAN_FOLDERS
        assert all(len(v) == len(ddr.MANIFEST_CHART_FIELDS) for v in row["charts"].values())


@pytest.mark.parametrize("text", ["#BPMS:0=120;\n#NOTES:dance-single::Easy:1::1000\n;",
                                  "#NOTES:\n dance-single:\n :\n Easy:\n 1:\n :\n1000\n"])
def test_minimal_and_unterminated_files(text):
    sim = parse_simfile(text)
    c = chart_to_ir(sim, sim.charts[0], meta())
    assert [(n.beat, n.lane) for n in c.notes] == [(0, 0)] and c.timing.bpms
    assert c.timing.offset_ms == 0.0 and not c.meta.source["stepmania"]["offset_given"]   # StepMania's default
    assert json.loads(c.to_json())["lanes"] == 4
