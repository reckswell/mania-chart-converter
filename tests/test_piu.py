"""Pump It Up: the StepMania parser features PIU simfiles use (formats/stepmania.py), and the PIU adapter
(sources/piu.py)."""
import json
import sqlite3
from fractions import Fraction
from pathlib import Path

import pytest

from vsrgconv.db import ChartDB
from vsrgconv.formats.stepmania import (
    chart_to_ir,
    parse_simfile,
    pump_counts,
    read_players,
    sm_warp_tricks,
)
from vsrgconv.games.registry import genre_slug
from vsrgconv.model import ChartMeta, NoteKind
from vsrgconv.sources import piu
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
F = Fraction


def meta():
    return ChartMeta(game="piu", song_id="t", title="T")


def sample():
    return parse_simfile((FIX / "piu_sample.ssc").read_text("utf-8"))


def chart(sim, i):
    return chart_to_ir(sim, sim.charts[i], meta())


def notes_of(c):
    return {(n.beat, int(n.lane), n.kind.value, n.duration) for n in c.notes}


# ---------------------------------------------------------------- timing: delays, warps, speeds, combos

def test_delays_warps_and_instant_speed_change_are_in_the_timing_map():
    sim = sample()
    c = chart(sim, 0)
    t = c.timing
    assert t.offset_ms == 100.0 and [(b.beat, b.bpm) for b in t.bpms] == [(0, 120.0)]
    assert t.delays == [(F(2), 500.0)] and t.warps == [(F(4), F(1))] and t.stops == []
    assert t.ms_at(2) == 100 + 1000 + 500                     # the delay comes before the note on beat 2
    assert t.ms_at(4) == 100 + 2000 + 500 and t.ms_at(5) == t.ms_at(4)          # beats 4-5 are warped
    assert t.ms_at(6) == t.ms_at(5) + 500
    assert [(s.beat, s.speed) for s in t.scrolls] == [(0, 1.0), (6, 2.0)]       # #SPEEDS 6=2, instant
    src = c.meta.source["stepmania"]
    assert set(src["unrepresented"]) == {"TICKCOUNTS", "COMBOS"} and src["fake_notes"] == 1
    assert not c.warnings
    assert sim.charts[0].timing.combos == [(F(0), 1, 1), (F(7), 2, 2)]


def test_stepf2_notes_and_fakes():
    c = chart(sample(), 0)
    assert notes_of(c) == {(0, 0, "tap", 0), (0, 4, "tap", 0), (1, 1, "hold", 1), (3, 2, "tap", 0),
                           (4, 3, "other", 0), (5, 0, "other", 0), (6, 1, "tap", 0), (7, 4, "tap", 0)}
    by = {(n.beat, int(n.lane)): n for n in c.notes}
    assert by[(4, 3)].extra == {"fake": "warp", "fake_kind": "tap"}
    assert by[(5, 0)].extra == {"fake": "note", "fake_kind": "tap", "sm_attr": "{1|n|1|0}"}   # StepF2 fake flag
    assert by[(6, 1)].extra == {"sm_attr": "{1|h|0|0}"}                                        # hidden, still judged


def test_pump_combo_counts_rows_hold_checkpoints_and_multipliers():
    sim = sample()
    # rows 0, 1, 3, 6 (one each, the jump is one) + 4 checkpoints of the 1-beat hold at 4 ticks + row 7 at ×2
    assert pump_counts(sim, sim.charts[0], chart(sim, 0)) == {"combo": 10, "rows": 5, "checkpoints": 4,
                                                              "holds": 1, "notes": 6}
    # song timing: 2 ticks a beat -> the 1-beat hold at beat 2 has checkpoints at 2.5 and 3
    assert pump_counts(sim, sim.charts[2], chart(sim, 2))["combo"] == 4


def test_checkpoint_on_a_tap_row_counts_once():
    sim = parse_simfile("#BPMS:0=120;\n#TICKCOUNTS:0=1;\n#NOTEDATA:;\n#STEPSTYPE:pump-single;\n#METER:1;\n"
                        "#NOTES:\n20000\n00000\n00000\n31000\n;")
    c = chart_to_ir(sim, sim.charts[0], meta())
    # head row 0; checkpoints at beats 1 and 2; beat 3 is the tail and a tap: one row
    assert pump_counts(sim, sim.charts[0], c) == {"combo": 4, "rows": 2, "checkpoints": 2, "holds": 1, "notes": 2}


def test_tweened_speed_is_approximated_with_a_warning():
    sim = parse_simfile("#BPMS:0=120;\n#SPEEDS:0=1=0=0,4=3=1=0;\n#NOTES:pump-single::Hard:1::10000\n;")
    c = chart_to_ir(sim, sim.charts[0], meta())
    assert [(s.beat, s.speed) for s in c.timing.scrolls] == [(0, 1.0), (4, 1.25), (F(17, 4), 1.75),
                                                             (F(9, 2), 2.25), (F(19, 4), 2.75), (5, 3.0)]
    assert c.meta.source["stepmania"]["unrepresented"]["SPEEDS"] == "0=1=0=0,4=3=1=0"
    assert any("1 tweened speed changes" in w for w in c.warnings)


def test_old_sm_warp_tricks_become_warps():
    sim = parse_simfile((FIX / "piu_warps.sm").read_text("utf-8"), "sm")
    c = chart_to_ir(sim, sim.charts[0], meta())
    t = c.timing
    assert [(b.beat, b.bpm) for b in t.bpms] == [(0, 120.0)] and t.stops == []
    assert t.warps == [(F(4), F(2)), (F(10), F(1))]          # BPM -120 for 1 beat; stop -0.5 s at 120 BPM
    assert t.ms_at(6) == t.ms_at(4) == 2000 and t.ms_at(11) == t.ms_at(10) == 4000 and t.ms_at(12) == 4500
    fakes = sorted(n.beat for n in c.notes if n.extra.get("fake") == "warp")
    assert fakes == [4, 5, 10] and sum(n.kind is NoteKind.TAP for n in c.notes) == 8
    assert any("2 warps from negative BPMs or stops" in w for w in c.warnings)
    # the same tricks in a .ssc file are ignored, as StepMania's SSC loader does
    ssc = parse_simfile("#VERSION:0.81;\n#BPMS:0=120,4=-120,5=120;\n#STOPS:10=-0.5;\n#NOTEDATA:;\n"
                        "#STEPSTYPE:pump-single;\n#NOTES:\n10000\n;")
    s = chart_to_ir(ssc, ssc.charts[0], meta())
    assert [(b.beat, b.bpm) for b in s.timing.bpms] == [(0, 120.0), (5, 120.0)] and s.timing.warps == []
    assert any("BPMs <= 0" in w for w in s.warnings) and any("negative stops ignored" in w for w in s.warnings)


def test_sm_warp_trick_port_keeps_plain_timing():
    bpms, stops, warps, before, _ = sm_warp_tricks([(F(0), 150.0), (F(8), 75.0)], [(F(-1), 0.25), (F(4), 0.5)])
    assert bpms == [(0, 150.0), (8, 75.0)] and stops == [(F(4), 0.5)] and warps == [] and before == 0.25


# ---------------------------------------------------------------- players: co-op letters and routine `&`

def test_coop_letters_and_routine_players():
    sim = sample()
    coop = chart(sim, 5)
    assert {(n.beat, int(n.lane), n.kind.value, n.extra.get("player")) for n in coop.notes} == {
        (0, 0, "tap", 1), (1, 4, "tap", 2), (2, 0, "hold", 2)}
    counts = pump_counts(sim, sim.charts[5], coop)
    assert counts["combo"] == 5 and counts["combo_p1"] == 1 and counts["combo_p2"] == 4
    routine = chart(sim, 6)
    assert routine.lanes == 10
    assert {(n.beat, int(n.lane), n.extra["player"]) for n in routine.notes} == {(0, 0, 1), (0, 5, 2)}
    assert pump_counts(sim, sim.charts[6], routine) == {"combo": 1, "rows": 1, "checkpoints": 0, "holds": 0,
                                                        "notes": 2, "combo_p1": 1, "combo_p2": 1}


def test_letters_are_only_players_in_pump_charts():
    notes, warnings = read_players("X000\n", 4)              # dance: X is not a note
    assert notes[0].kind is NoteKind.OTHER and notes[0].extra == {"sm": "X"} and warnings
    notes, _ = read_players("X0000\n", 5, player_letters=True)
    assert notes[0].kind is NoteKind.TAP and notes[0].extra == {"player": 2}


def test_stepf2_6_is_a_hold_head_in_pump_charts():
    # FIESTA EX "Everybody Got 2 Know" S21: a tap, then `6` on the next row and a tail; piu-vis-ss reads a hold there
    notes, warnings = read_players("10000\n60000\n30000\n00000\n", 5, player_letters=True)
    assert [(n.beat, n.kind, n.duration) for n in notes] == [(0, NoteKind.TAP, 0), (1, NoteKind.HOLD, 1)]
    assert notes[1].extra == {"sm_head": "6"} and not warnings
    notes, warnings = read_players("6000\n3000\n", 4)        # dance: not a note, as in StepMania
    assert notes[0].kind is NoteKind.OTHER and warnings


# ---------------------------------------------------------------- Pumpout catalog

@pytest.fixture
def pumpout_db(tmp_path):
    p = tmp_path / "pumpout.db"
    con = sqlite3.connect(p)
    con.executescript((FIX / "piu_pumpout_sample.sql").read_text("utf-8"))
    con.commit()
    con.close()
    return p


def test_pumpout_live_set_at_phoenix2(pumpout_db):
    songs, report = piu.read_pumpout(pumpout_db)
    by = {s.title: s for s in songs}
    assert set(by) == {"Fixture Song", "Gone Song", "New Song", "Fixture Song - SHORT CUT -", "Copy Song"}
    fx = by["Fixture Song"]
    assert [(c.mode, c.level) for c in fx.charts] == [("S", "5"), ("D", "10"), ("D", "13"), ("HDB", "6"),
                                                      ("C", "2")]           # S9 deleted; D12 re-rated to 13
    assert fx.artist == "vsrgconv feat. Guest" and fx.titles == {"en": "Fixture Song", "kr": "픽스처 송"}
    assert fx.category == "ORIGINAL" and fx.bpm == "120" and fx.game_id == "1A01" and fx.first == "Phoenix v1.00.0"
    gone = by["Gone Song"]
    assert gone.removed == "Phoenix 2 v1.00.0" and [(c.mode, c.level) for c in gone.charts] == [("S", "3")]
    assert by["New Song"].labels == ["UNLOCK REQUIRED"] and by["Gone Song"].bpm == "90-180"
    assert report["live_songs"] == 4 and report["live_charts"] == 8 and report["removed_songs"] == 1
    assert report["live_charts_by_mode"]["D"] == 2


def fixture_manifest():
    return {"chart_fields": piu.MANIFEST_CHART_FIELDS, "files": [
        piu.manifest_entry("12 - PRIME/1A01 - Fixture Song/1A01 - Fixture Song.ssc", sample()),
        piu.manifest_entry("17 - PHOENIX 2/19135 - Copy Song/Copy Song.ssc",
                           parse_simfile((FIX / "piu_copycat.ssc").read_text("utf-8")))]}


def test_catalog_matches_songs_and_charts(pumpout_db):
    po, _ = piu.read_pumpout(pumpout_db)
    kali = piu.kali_index("PHOENIX\nFixture Song\nFixture Song SHORT CUT\n",
                          '"fixture_song": require("x"),\n"fixture_song_short_cut": require("y"),')
    songs, report = piu.build_catalog(po, fixture_manifest(), kali)
    by = {s.title: s for s in songs if s.extra.get("cut") != "Short Cut"}
    fx = by["Fixture Song"]
    assert fx.song_id == "1A01" and fx.genre == "ORIGINAL" and fx.version == "Phoenix"
    assert fx.source["simfile"]["path"].endswith("1A01 - Fixture Song.ssc") and fx.source["kalinathalie"] == {
        "slug": "fixture_song"}
    assert list(fx.charts) == ["single-5", "double-10", "double-13", "halfdouble-6", "coop-2"]
    s5 = fx.charts["single-5"]
    assert s5.native == "S5" and s5.level == "5" and s5.designer == "STEPMAKER A"
    assert s5.source["ladder"] == "easy" and s5.source["simfile"]["description"] == "S5"     # not "S5 HIDDEN"
    assert s5.source["simfile"]["chart"] == 0 and s5.note_counts == {"combo": 10}
    assert fx.charts["double-13"].source["simfile"]["match"] == "re-rated"
    assert fx.charts["double-13"].source["simfile"]["meter"] == "12" and fx.charts["double-13"].native == "D13"
    assert fx.charts["halfdouble-6"].native == "HDB6" and fx.charts["halfdouble-6"].source["ladder"] == "easy"
    coop = fx.charts["coop-2"]
    assert coop.native == "CO-OP x2" and "ladder" not in coop.source
    assert coop.source["simfile"]["steps_type"] == "pump-double"                # the ACTIVE one, not the routine
    short = next(s for s in songs if s.extra.get("cut") == "Short Cut")
    assert short.song_id == "1A51" and "simfile" not in short.source               # the arcade file is not it
    assert short.source["kalinathalie"] == {"slug": "fixture_song_short_cut"}
    new = by["New Song"]
    assert new.song_id == piu.fallback_id(3) == "po3" and "simfile" not in new.source
    copy = by["Copy Song"]
    # rayden numbers PHOENIX-era folders with its own sort keys (18044.5, 19135 ...), not game ids
    assert copy.song_id == "po5" and copy.source["simfile"]["id"] == "19135"
    assert copy.source["simfile"]["fan_transcription"]
    assert copy.charts["single-10"].source["simfile"]["fan_transcription"]
    gone = by["Gone Song"]
    assert gone.extra["removed"] and gone.extra["removed_in"] == "Phoenix 2 v1.00.0" and list(gone.charts) == [
        "single-3"]
    assert report["songs"] == 4 and report["removed_songs"] == 1 and report["charts"] == 8
    assert report["charts_with_simfile"] == 6 and report["charts_re_rated"] == 1
    assert report["fan_transcription_charts"] == 1 and report["no_simfile_count"] == 2


def test_match_prefers_a_former_rating_over_the_nearest_meter():
    song = piu.PoSong(1, "T", "Arcade", charts=[piu.PoChart(1, "D", "15", former=[("D", "13")]),
                                                piu.PoChart(2, "S", "8"), piu.PoChart(3, "S", "20")])
    row = lambda st, desc, meter: [st, desc, meter, "Edit", 1, "", "", 0, 0]
    entry = {"charts": [row("pump-double", "D14", "14"), row("pump-double", "D13", "13"),
                        row("pump-single", "S7", "7"), row("pump-single", "S17", "17")]}
    out, notes = piu.match_charts(song, entry, piu.MANIFEST_CHART_FIELDS)
    assert out[1][0] == 1 and out[1][2] == "re-rated"       # Pumpout rated it D13 before; D14 is only nearer
    assert out[2][0] == 2 and out[2][2] == "near-level"     # no history: the nearest left-over ACTIVE chart
    assert 3 not in out                                     # S17 is too far from S20
    assert len(notes) == 2


def test_match_never_takes_a_non_arcade_chart():
    song = piu.PoSong(1, "T", "Arcade", charts=[piu.PoChart(1, "HDB", "16"), piu.PoChart(2, "D", "9"),
                                                piu.PoChart(3, "S", "12", former=[("S", "11")])])
    row = lambda st, desc, meter, active=0: [st, desc, meter, "Edit", active, "", "", 0, 0]
    entry = {"charts": [row("pump-halfdouble", "UCS", "16"), row("pump-double", "D9", "9"),
                        row("pump-single", "S11 QUEST", "11"), row("pump-single", "S12 PRO", "12")]}
    out, _ = piu.match_charts(song, entry, piu.MANIFEST_CHART_FIELDS)
    # a user chart, a mission and a PRO chart are not the arcade's; a plain non-ACTIVE D9 may be (old file)
    assert list(out) == [2] and out[2][2] == "level"


def test_pumpout_keeps_former_ratings(pumpout_db):
    songs, _ = piu.read_pumpout(pumpout_db)
    d13 = next(c for s in songs if s.title == "Fixture Song" for c in s.charts if c.level == "13")
    assert d13.former == [("D", "12")]


def test_slot_names_and_genres():
    assert piu.slot_name("single", "17") == "single-17" and piu.slot_name("double", "22", 2) == "double-22-2"
    assert piu.slot_name("double", "??") == "double-unknown"      # 1948's PREMIUM MODE chart; slots are file names
    assert piu.native_for("S", "17") == "S17" and piu.native_for("C", "3") == "CO-OP x3"
    assert piu.folder_id("17 - PHOENIX 2/19135 - CALL ME BACK/CALL ME BACK.ssc") == "19135"
    assert piu.folder_id("01 - 1ST~3RD/(1) 301 - Final Audition 2/301 - Final Audition 2.ssc") == "301"
    assert piu.folder_id("16 - PHOENIX/18049.6 - Darkside Of The Mind/Darkside Of The Mind.ssc") == ""
    assert piu.real_game_id("18XX_0101") == "" and piu.real_game_id("F20") == "F20"
    # song_id: Pumpout's id, else a folder number in the game's id form (3-4 hex digits), else "po" + Pumpout's number
    assert piu.song_id_for("1617", {"id": "1617"}) == "1617" and piu.song_id_for("", {"id": "14C6"}) == "14C6"
    assert piu.song_id_for("17XX_1502", {"id": "1701"}) == "1701" and piu.song_id_for("", {"id": "19135"}) == ""
    assert piu.song_id_for("", None) == ""
    assert piu.base_title("Destination - SHORT CUT -") == "Destination" and piu.base_title("[Full Song] X") == "X"
    assert genre_slug("piu", "WORLD MUSIC") == "world-music" and genre_slug("piu", "K-POP") == "k-pop"
    assert piu.sim_style("pump-double", "D10 HALFDOUBLE") == "HDB"
    assert piu.sim_style("pump-routine", "DP COOP 2P") == "C"
    assert piu.sim_level("C", "99", "DP COOP 4P") == "4"


# ---------------------------------------------------------------- charts and jackets (offline)

def test_piu_chart_source_offline(tmp_path, pumpout_db):
    po, _ = piu.read_pumpout(pumpout_db)
    songs, _ = piu.build_catalog(po, fixture_manifest(), {})
    by = {s.song_id: s for s in songs}
    f = Fetcher(tmp_path / "cache", offline=True)
    for song, fix in ((by["1A01"], "piu_sample.ssc"), (by["po5"], "piu_copycat.ssc")):
        p = f.cache_path(song.source["simfile"]["url"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((FIX / fix).read_bytes())
    c = piu.chart_from_simfile(f, by["1A01"], "single-5")
    assert c.lanes == 5 and c.meta.difficulty == "single-5" and c.meta.difficulty_native == "S5"
    assert c.meta.source["extracted_from"] == "piu-simfiles-rayden" and c.meta.note_counts == {"combo": 10}
    assert c.meta.source["piu_counts"]["checkpoints"] == 4 and not c.warnings
    d = piu.chart_from_simfile(f, by["1A01"], "double-13")
    assert d.lanes == 10 and any("re-rated" in w for w in d.warnings)
    assert piu.chart_from_simfile(f, by["1A01"], "halfdouble-6").lanes == 6
    coop = piu.chart_from_simfile(f, by["1A01"], "coop-2")
    assert {n.extra.get("player") for n in coop.notes} == {1, 2}
    cc = piu.chart_from_simfile(f, by["po5"], "single-10")
    assert cc.meta.source["fan_transcription"] == "The Copycat Steps" and any("fan transcription" in w
                                                                              for w in cc.warnings)
    assert piu.chart_from_simfile(f, by["po3"], "single-10") is None                # no simfile

    # jackets: kalinathalie first, else the Pumpout card (img/, then img_big/)
    assert piu._jacket(f, by["po3"]) is None
    big = f.cache_path(piu.PUMPOUT + "img_big/Phoenix2_New_Song.png")
    big.parent.mkdir(parents=True)
    big.write_bytes(b"big")
    assert piu._jacket(f, by["po3"]) == (b"big", "png", piu.PUMPOUT + "img_big/Phoenix2_New_Song.png")
    small = f.cache_path(piu.PUMPOUT + "img/Phoenix2_New_Song.png")
    small.parent.mkdir(parents=True)
    small.write_bytes(b"small")
    assert piu._jacket(f, by["po3"])[0] == b"small"

    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    saved = db.save_chart(c)
    assert saved.name == "single-5.chart.json.gz" and saved.parent.parent.name == "original"
    assert db.load_chart("piu", "1A01", "single-5").to_dict() == c.to_dict()


def test_guess_candidates_cover_unlisted_phoenix_songs_and_short_cuts():
    songs = [piu.PoSong(1, "Listed", "Arcade", first_mix="Phoenix 2"),
             piu.PoSong(2, "Listed - SHORT CUT -", "Short Cut", first_mix="Phoenix"),
             piu.PoSong(3, "New: One", "Arcade", first_mix="Phoenix 2"),
             piu.PoSong(4, "Old", "Arcade", first_mix="XX")]
    entries = [{"path": "17 - PHOENIX 2/19000 - Listed/Listed.ssc", "title": "Listed", "songtype": "ARCADE"}]
    jobs = piu.guess_candidates(songs, entries)
    assert "17 - PHOENIX 2/19135 - New One/New One.ssc" in jobs
    assert "16 - PHOENIX/18F80 - [Short Cut] Listed/[Short Cut] Listed.ssc" in jobs   # the arcade file is not it
    assert not any("Old" in j or "19000 - Listed/" in j for j in jobs)


def test_piu_source_is_registered_and_manifest_is_well_formed():
    from vsrgconv.sources import SOURCES

    assert SOURCES["piu"].chart is piu.chart_from_simfile and SOURCES["piu"].jacket is piu._jacket
    m = piu.load_manifest()
    assert m["chart_fields"] == piu.MANIFEST_CHART_FIELDS and m["files"]
    for row in m["files"]:
        assert row["path"].split("/")[0] in piu.FOLDERS and row["path"].lower().endswith(".ssc")   # one is .SSC
        assert all(len(c) == len(piu.MANIFEST_CHART_FIELDS) for c in row["charts"])
    assert len({r["path"] for r in m["files"]}) == len(m["files"])
    json.dumps(m)
