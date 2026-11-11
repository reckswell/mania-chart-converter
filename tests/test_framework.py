import json
from fractions import Fraction
from pathlib import Path

import pytest

from vsrgconv.convert.lint import lint
from vsrgconv.convert.pipeline import convert
from vsrgconv.convert.rules import snap
from vsrgconv.db import ChartDB, ChartRecord, SongRecord
from vsrgconv.games.registry import canonical_difficulty, genre_slug
from vsrgconv.imaging.sdvxin_chart import extract_chart, time_signature
from vsrgconv.imaging.testing import DrawnNote, draw_chart
from vsrgconv.model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, TimingMap
from vsrgconv.osu.reader import parse_osu
from vsrgconv.osu.writer import ManiaContractError, write_osu
from vsrgconv.sources.chunithm import build_catalog, layer_urls, otogedb_record, sdvxin_record
from vsrgconv.sources.fetch import Fetcher, NotFound

FIX = Path(__file__).parent / "fixtures"


def song(bpm="150"):
    return SongRecord(game="chunithm", song_id="t1", title="Test", artist="A", genre="ORIGINAL", bpm=bpm,
                      charts={"master": ChartRecord(slot="master", native="MASTER", level="13")})


# ---------------------------------------------------------------- model / timing

def test_timing_ms_with_bpm_change_and_stop():
    t = TimingMap(offset_ms=100, bpms=[BpmChange(Fraction(0), 120), BpmChange(Fraction(4), 240)],
                  stops=[(Fraction(6), 500.0)])
    assert t.ms_at(0) == 100
    assert t.ms_at(4) == 100 + 2000
    assert t.ms_at(6) == 100 + 2000 + 500          # stop at 6 applies after it
    assert t.ms_at(7) == 100 + 2000 + 750 + 500
    assert t.ms_at(-1) == 100 - 500


def test_chart_json_round_trip():
    c = Chart(meta=ChartMeta(game="chunithm", song_id="x", title="t"), lanes=16,
              timing=TimingMap(bpms=[BpmChange(Fraction(0), 150)]),
              notes=[Note(Fraction(1, 3), 2, 4, NoteKind.SLIDE, Fraction(1, 2), extra={"a": 1})])
    back = Chart.from_json(c.to_json())
    assert back.notes[0].beat == Fraction(1, 3) and back.notes[0].kind == NoteKind.SLIDE
    assert back.notes[0].duration == Fraction(1, 2) and back.meta.title == "t"


def test_main_bpm_is_longest_held():
    t = TimingMap(bpms=[BpmChange(Fraction(0), 100), BpmChange(Fraction(8), 200)])
    assert t.main_bpm(Fraction(100)) == 200
    assert t.main_bpm(Fraction(10)) == 100


# ---------------------------------------------------------------- registry

def test_difficulty_ladder_and_genres():
    assert canonical_difficulty("chunithm", "mst") == "master"
    assert canonical_difficulty("chunithm", "BAS") == "normal"
    assert canonical_difficulty("sdvx", "exh") == "expert"
    assert genre_slug("chunithm", "東方Project") == "touhou-project"


# ---------------------------------------------------------------- sources

def test_sdvxin_rows_and_urls():
    rows = json.loads((FIX / "sdvxin_songs01_sample.json").read_text("utf-8"))
    rec = sdvxin_record(rows[0], "songs01.json")
    assert rec.title_romaji == "contrapassoinferno"
    assert rec.charts["normal"].status == "not-drawn"         # "_4" = not drawn on sdvx.in
    assert rec.charts["master"].level == "15" and rec.charts["master"].note_counts["flick"] == 156
    assert "extra" not in rec.charts                          # empty ULTIMA level
    urls = layer_urls(rec.charts["master"].source["sdvxin"])
    assert urls["obj"][0] == "https://sdvx.in/chunithm/01/obj/data01189mst.webp"
    assert urls["bar"][1] == "https://sdvx.in/chunithm/01/bg/01189bar.png"


def test_catalog_merge_by_title():
    otoge = [otogedb_record(r) for r in json.loads((FIX / "otogedb_sample.json").read_text("utf-8"))]
    sdvx = [sdvxin_record(r, "songs01.json")
            for r in json.loads((FIX / "sdvxin_songs01_sample.json").read_text("utf-8"))]
    songs, unmatched = build_catalog(otoge, sdvx)
    assert unmatched == []
    contra = next(s for s in songs if s.title == "Contrapasso -inferno-")
    assert contra.song_id == "201"
    assert contra.charts["master"].source["sdvxin"] == {"id": "01189", "key": "mst"}
    assert contra.charts["master"].constant is not None
    assert contra.title_romaji == "contrapassoinferno"


def test_fetcher_offline_cache(tmp_path):
    f = Fetcher(cache_dir=tmp_path, offline=True)
    url = "https://sdvx.in/chunithm/_/json/songs01.json"
    with pytest.raises(NotFound):
        f.get(url)
    p = f.cache_path(url)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"[]")
    assert f.get(url) == b"[]"
    assert p == tmp_path / "sdvx.in" / "chunithm" / "_" / "json" / "songs01.json"


# ---------------------------------------------------------------- database

def test_db_layout_and_merge(tmp_path):
    db = ChartDB(tmp_path)
    rec = song()
    rec.title_romaji = "tesuto"
    db.upsert_song(rec)
    d = tmp_path / "chunithm" / "original" / "t1-tesuto"
    assert (d / "song.json").exists()
    again = SongRecord(game="chunithm", song_id="t1", title="Test", genre="VARIETY",
                       charts={"master": ChartRecord(slot="master", native="MASTER", designer="X")})
    db.upsert_song(again)
    got = db.get_song("chunithm", "t1")
    assert got.genre == "VARIETY" and got.charts["master"].designer == "X" and got.charts["master"].level == "13"
    assert (tmp_path / "chunithm" / "variety" / "t1-tesuto" / "song.json").exists()
    assert not d.exists()
    sql = db.build_sqlite(tmp_path / "i.sqlite")
    assert sql.exists()


# ---------------------------------------------------------------- picture reader

DRAWN = [
    DrawnNote(Fraction(0), 0, 4), DrawnNote(Fraction(1, 2), 4, 2), DrawnNote(Fraction(3, 4), 12, 4, "ex_tap"),
    DrawnNote(Fraction(2), 6, 4, "hold", Fraction(3)),
    DrawnNote(Fraction(3), 7, 2),                          # tap over the hold body
    DrawnNote(Fraction(8), 0, 16, "flick"), DrawnNote(Fraction(41, 4), 2, 3, "air"),
    DrawnNote(Fraction(12), 9, 3, "slide", Fraction(2)), DrawnNote(Fraction(30), 15, 1, "mine"),
] + [DrawnNote(Fraction(16) + Fraction(i, 4), i % 16, 1) for i in range(32)]


def test_reader_recovers_drawn_notes_across_columns():
    layers, _ = draw_chart(DRAWN, measures=9, height=600)  # several picture columns
    chart = extract_chart(song(), "master", layers)
    assert chart.meta.source["layout"]["columns"] >= 3
    want = {(n.beat, n.cell, n.width, n.cls, n.duration) for n in DRAWN}
    got = {(n.beat, int(n.lane), int(n.width), n.kind.value, n.duration) for n in chart.notes}
    assert want == got
    assert [m.beats_per_measure for m in chart.timing.meters] == [4]


def test_time_signature():
    assert time_signature(Fraction(4)) == (4, 4)
    assert time_signature(Fraction(7, 2)) == (7, 8)
    assert time_signature(Fraction(3)) == (3, 4)


# ---------------------------------------------------------------- rules, lint, writer

def test_snap():
    assert snap(Fraction(1, 4) + Fraction(1, 200)) == Fraction(1, 4)
    assert snap(Fraction(2, 3) - Fraction(1, 500)) == Fraction(2, 3)


def test_end_to_end_to_osu():
    layers, _ = draw_chart(DRAWN, measures=9, height=600)
    chart = extract_chart(song(), "master", layers)
    conv = convert(chart, keys=8)
    assert conv.lint.ok, conv.lint.errors
    m = parse_osu(conv.osu)
    assert m.mode == 3 and m.keys == 8
    assert len(m.objects) == len(conv.mania.notes)
    holds = [o for o in m.objects if o.end_time]
    assert holds and holds[0].time == 800 and holds[0].end_time == 2000   # beat 2..5 at 150 BPM
    assert m.timing[0].bpm == pytest.approx(150)
    kinds = {r["rule"]: r for r in conv.report}
    assert kinds["kinds"]["counts"] == {"air": 1, "mine": 1}


def test_writer_rejects_unconverted_chart():
    c = Chart(meta=ChartMeta(game="chunithm", song_id="x", title="t"), lanes=16,
              timing=TimingMap(bpms=[BpmChange(Fraction(0), 150)]),
              notes=[Note(Fraction(0), 3.5, 2, NoteKind.AIR)])
    with pytest.raises(ManiaContractError):
        write_osu(c)


def test_variable_bpm_writes_normalising_sv():
    c = Chart(meta=ChartMeta(game="chunithm", song_id="x", title="t"), lanes=4,
              timing=TimingMap(bpms=[BpmChange(Fraction(0), 100), BpmChange(Fraction(16), 200)],
                               meters=[MeterChange(Fraction(0))]),
              notes=[Note(Fraction(i), i % 4) for i in range(40)])
    m = parse_osu(write_osu(c))
    reds = [t for t in m.timing if t.uninherited]
    greens = [t for t in m.timing if not t.uninherited]
    assert [round(t.bpm) for t in reds] == [100, 200]
    # main BPM is 100 (9.6 s vs 7.2 s), so only the 200 BPM section needs a 0.5x line
    assert [round(g.sv, 3) for g in greens] == [0.5] and greens[0].time == reds[1].time


def test_lint_catches_overlap_and_empty_column():
    c = Chart(meta=ChartMeta(game="x", song_id="x", title="t", difficulty="hard"), lanes=4,
              timing=TimingMap(bpms=[BpmChange(Fraction(0), 120)]),
              notes=[Note(Fraction(0), 0, kind=NoteKind.HOLD, duration=Fraction(2)), Note(Fraction(1), 0),
                     Note(Fraction(1), 1), Note(Fraction(1), 2)])
    r = lint(c, "hard")
    assert any("inside the hold" in e for e in r.errors)
    assert any("column 3 is empty" in e for e in r.errors)


# ---------------------------------------------------------------- orthomania hooks, CLI

def test_playtest_command(tmp_path, monkeypatch):
    from vsrgconv.playtest import orthomania as om

    (tmp_path / "scripts" / "ortho").mkdir(parents=True)
    monkeypatch.setattr(om.sys, "platform", "linux")
    cmd = om.playtest_command(om.PlaytestRequest(Path("a.osu"), times_ms=[1000, 2000], scroll_speed=14), tmp_path)
    assert cmd[1].endswith("playtest.sh") and cmd[cmd.index("--times") + 1] == "1000,2000" and "--play-to-end" in cmd
    monkeypatch.setattr(om.sys, "platform", "win32")
    cmd = om.playtest_command(om.PlaytestRequest(Path("a.osu")), tmp_path)
    assert "-Beatmap" in cmd and "-PlayToEnd" in cmd


def test_cli_demo(tmp_path, capsys):
    from vsrgconv.cli import main

    assert main(["--db", str(tmp_path / "db"), "demo", "--out", str(tmp_path / "out")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lint"]["ok"] and Path(out["osu"]).exists()
    assert (tmp_path / "db" / "chunithm" / "original" / "demo-synthetic-demo" / "master.chart.json.gz").exists()


def test_save_asset_shrinks_and_records(tmp_path):
    from io import BytesIO

    from PIL import Image

    db = ChartDB(tmp_path)
    db.upsert_song(song())
    buf = BytesIO()
    Image.new("RGB", (1000, 800), (200, 10, 10)).save(buf, "PNG")
    a = db.save_asset("chunithm", "t1", "jacket", buf.getvalue(), ext="png", url="https://x/j.png", max_size=256)
    assert a.file == "assets/jacket.jpg" and (a.width, a.height) == (256, 205)
    rec = db.get_song("chunithm", "t1")
    assert rec.assets["jacket"].url == "https://x/j.png" and db.asset_path(rec, "jacket").exists()
    db.upsert_song(song())                       # re-cataloguing keeps the asset
    assert "jacket" in db.get_song("chunithm", "t1").assets


# ---------------------------------------------------------------- Chunithm .c2s

def test_c2s_parses_every_note_type():
    from vsrgconv.formats.c2s import c2s_to_chart

    c = c2s_to_chart((FIX / "sample.c2s").read_text("utf-8"), ChartMeta(game="chunithm", song_id="t1", title="T",
                                                                        difficulty="master"))
    assert c.lanes == 16 and c.meta.designer == "Tester"
    assert [(b.beat, b.bpm) for b in c.timing.bpms] == [(0, 150.0), (16, 300.0)]
    assert [(m.beat, m.numerator) for m in c.timing.meters] == [(0, 4), (8, 3)]
    assert c.timing.scrolls[0].speed == 0.5 and c.timing.scrolls[1].beat == Fraction(6)
    kinds = c.count_by_kind()
    assert kinds == {"tap": 1, "ex_tap": 1, "flick": 1, "mine": 1, "hold": 2, "slide": 2, "air": 2, "air_hold": 3,
                     "other": 2}
    slides = [n for n in c.notes if n.kind is NoteKind.SLIDE]
    chained = next(n for n in slides if n.lane == 2)          # SLD + SLC + SLD (duplicate SLC dropped)
    assert chained.beat == 12 and chained.duration == 2 and [p.lane for p in chained.path] == [2, 6, 6]
    assert [p.easing for p in chained.path] == ["linear", "control", "linear"]
    air = next(n for n in c.notes if n.kind is NoteKind.AIR_HOLD and n.extra.get("air_slide"))
    assert air.duration == 2 and air.extra["heights"] == [1.0, 3.0, 5.0] and air.path[-1].lane == 4
    hold = next(n for n in c.notes if n.kind is NoteKind.HOLD and n.extra.get("ex_start"))
    assert hold.lane == 14 and hold.duration == 1
    assert "1 exact duplicate lines ignored" in c.warnings
    assert c.meta.source["c2s_totals"]["T_JUDGE_ALL"] == "21"
    assert c.meta.source["c2s_unrepresented"] == ["SLP\t1\t0\t384\t2.000000\t0"]


def test_chunithm_chart_source_uses_c2s_and_checks_counts(tmp_path):
    from vsrgconv.sources.chunithm import _jacket, c2s_url, chart_from_c2s

    rec = otogedb_record(json.loads((FIX / "otogedb_sample.json").read_text("utf-8"))[0])
    assert c2s_url(rec, "master").endswith("/chunithm/chart/2490_03.c2s")
    assert c2s_url(rec, "special-2") is None
    f = Fetcher(tmp_path / "cache", offline=True)
    p = f.cache_path(c2s_url(rec, "master"))
    p.parent.mkdir(parents=True)
    p.write_bytes((FIX / "sample.c2s").read_bytes())
    chart = chart_from_c2s(f, rec, "master")
    assert chart.meta.source["extracted_from"] == "marisabot-c2s" and chart.meta.level == rec.charts["master"].level
    assert any("differs from the published note count" in w for w in chart.warnings)
    assert chart_from_c2s(f, rec, "expert") is None          # not cached offline -> no chart, no crash
    jp = f.cache_path("https://raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/jacket/"
                      + rec.source["otogedb"]["image"])
    jp.parent.mkdir(parents=True)
    jp.write_bytes(b"jpg")
    assert _jacket(f, rec)[1] == "jpg"


def test_charts_are_stored_gzipped_and_round_trip(tmp_path):
    from vsrgconv.formats.c2s import c2s_to_chart

    db = ChartDB(tmp_path)
    db.upsert_song(song())
    c = c2s_to_chart((FIX / "sample.c2s").read_text("utf-8"), ChartMeta(game="chunithm", song_id="t1", title="Test",
                                                                        difficulty="master", difficulty_native="MASTER"))
    p = db.save_chart(c)
    assert p.name == "master.chart.json.gz" and db.has_chart("chunithm", "t1", "master")
    assert db.save_chart(c).read_bytes() == p.read_bytes()      # deterministic bytes
    back = db.load_chart("chunithm", "t1", "master")
    assert back.to_dict() == c.to_dict()
    rec = db.get_song("chunithm", "t1").charts["master"]
    assert rec.status == "extracted" and rec.source["extracted"]["counts"]["slide"] == 2


def test_shrink_assets_reencodes_large_images_in_place(tmp_path):
    from io import BytesIO

    from PIL import Image

    db = ChartDB(tmp_path)
    db.upsert_song(song())
    buf = BytesIO()
    Image.new("P", (600, 600)).save(buf, "PNG", transparency=0)       # palette PNG with transparency
    db.save_asset("chunithm", "t1", "jacket", buf.getvalue(), ext="png", max_size=None)
    assert db.get_song("chunithm", "t1").assets["jacket"].file == "assets/jacket.png"
    stats = db.shrink_assets("chunithm", 100)
    a = db.get_song("chunithm", "t1").assets["jacket"]
    assert stats["shrunk"] == 1 and a.file == "assets/jacket.jpg" and (a.width, a.height) == (100, 100)
    assert not (tmp_path / "chunithm" / "original" / "t1-test" / "assets" / "jacket.png").exists()
    assert db.shrink_assets("chunithm", 100)["shrunk"] == 0
