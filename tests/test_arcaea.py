import json
from fractions import Fraction
from pathlib import Path

from vsrgconv.db import ChartDB
from vsrgconv.formats.aff import aff_to_chart, combo_counts, parse_aff
from vsrgconv.model import ChartMeta, NoteKind
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
SAMPLE = (FIX / "arcaea_sample.aff").read_text("utf-8")


def meta(**kw):
    return ChartMeta(game="arcaea", song_id="t", title="T", difficulty="expert", difficulty_native="FUTURE", **kw)


def put(f: Fetcher, url: str, data: bytes) -> None:
    p = f.cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_aff_parses_every_event_type():
    aff = parse_aff(SAMPLE)
    assert not aff.errors and not aff.unknown
    assert aff.audio_offset == 100 and aff.density == 1
    assert [g.props for g in aff.groups] == ["", "", "noinput_anglex900", "fadingholds"]
    assert aff.groups[2].noinput and not aff.groups[3].noinput
    assert [t.raw() for t in aff.groups[1].timings] == [[0, "60.00", "4.00"]]
    assert len(aff.taps) == 4 and len(aff.holds) == 3 and len(aff.arcs) == 7
    trace = aff.arcs[3]
    assert trace.is_trace and trace.arctaps == [6000, 6500, 7000] and trace.color == 1
    fancy = aff.arcs[4]
    assert (fancy.ease, fancy.color, fancy.fx, fancy.smooth) == ("b", 2, "glass_wav", "2.00")
    assert aff.arcs[-1].trace == "designant"
    assert aff.scenecontrols == [(0, "scenecontrol(10000,trackhide);")] and len(aff.cameras) == 1


def test_aff_beats_are_exact_through_bpm_changes_and_round_trip_to_ms():
    chart = aff_to_chart(SAMPLE, meta())
    t = chart.timing
    assert t.offset_ms == 100.0
    assert [(b.beat, b.bpm) for b in t.bpms] == [(0, 120.0), (16, 240.0)]
    # BPM -120 at 9000 ms (beat 20): the beat axis keeps 240, the floor runs backwards at half speed until 9500 ms
    assert [(s.beat, s.speed) for s in t.scrolls] == [(20, -0.5), (22, 1.0)]
    assert [(m.beat, m.numerator, m.denominator) for m in t.meters] == [(0, 4, 4), (22, 3, 4)]
    taps = [n for n in chart.notes if n.kind == NoteKind.TAP]
    assert [(n.beat, n.lane) for n in taps] == [(2, 0), (2, 3), (24, 2)]
    for n in chart.notes:
        assert isinstance(n.beat, Fraction)
    # every note start gives back its file ms through the timing map (no round-trip warning either)
    assert round(t.ms_at(Fraction(24))) == 10100
    assert not any("round-trip" in w for w in chart.warnings)
    assert any("BPM <= 0" in w for w in chart.warnings)


def test_aff_maps_notes_to_the_ir():
    chart = aff_to_chart(SAMPLE, meta())
    assert chart.lanes == 4
    assert chart.count_by_kind() == {"tap": 3, "hold": 3, "air_hold": 3, "air": 3, "other": 6}
    hold = next(n for n in chart.notes if n.kind == NoteKind.HOLD and n.beat == 3)
    assert (hold.lane, hold.duration) == (1, 2)
    arcs = [n for n in chart.notes if n.kind == NoteKind.AIR_HOLD]
    first, second, fancy = arcs
    assert (first.beat, first.duration, first.lane, first.path[0].lane) == (6, 2, 0.5, 2.5)
    assert second.extra["continues"] and "continues" not in first.extra
    assert second.path[0].easing == "out" and fancy.path[0].easing == "inout"
    assert fancy.extra == {"x": [0.25, 0.75], "y": [0.5, 0.5], "ease": "b", "color": 2, "fx": "glass_wav",
                           "smooth": "2.00"}
    arctaps = [n for n in chart.notes if n.kind == NoteKind.AIR]
    assert [(n.beat, n.lane, n.extra["x"]) for n in arctaps] == [(12, 0.5, 0.0), (13, 1.5, 0.5), (14, 2.5, 1.0)]
    other = {(n.extra["aff"], n.extra.get("noinput", False)) for n in chart.notes if n.kind == NoteKind.OTHER}
    assert other == {("arc", False), ("trace", False), ("designant", False), ("arctap", False), ("tap", True),
                     ("arc", True)}
    grouped = next(n for n in chart.notes if n.kind == NoteKind.HOLD and n.extra.get("group") == 1)
    assert grouped.beat == 28 and grouped.duration == 8
    src = chart.meta.source["aff"]
    assert src["timing"][2] == [9000, "-120.00", "0.00"]
    assert [g["props"] for g in src["groups"]] == ["", "noinput_anglex900", "fadingholds"]
    assert src["scenecontrol"] == [[0, "scenecontrol(10000,trackhide);"]] and len(src["camera"]) == 1


def test_aff_combo_rule():
    c = combo_counts(parse_aff(SAMPLE))
    # taps 3 (the noinput tap gives none); holds: 1500-2500 @120 -> 4 ticks -> 3, the 60-BPM group's 2 s hold
    # uses its own timing -> 3, the 300-BPM group's 1 s hold (>= 255: one tick a beat) -> 4; arcs: a head 3, the arc
    # continuing it 4, the 240-BPM arc 3; arctaps: 3 on the trace, none on the designant trace
    assert c == {"tap": 3, "hold": 10, "arc": 10, "arctap": 3, "total": 26}
    assert aff_to_chart(SAMPLE, meta()).meta.source["aff"]["combo"]["total"] == 26
    dense = "AudioOffset:0\nTimingPointDensityFactor:2\n-\ntiming(0,120.00,4.00);\nhold(0,1000,1);\n"
    assert combo_counts(parse_aff(dense))["hold"] == 7          # interval 125 ms -> 8 -> 7
    short = "AudioOffset:0\n-\ntiming(0,120.00,4.00);\nhold(0,200,1);\narc(0,100,0,0,s,1,1,0,none,false);\n"
    assert combo_counts(parse_aff(short))["total"] == 2         # short notes still give 1 each


def test_aff_six_lane_chart():
    text = ("AudioOffset:0\n-\ntiming(0,100.00,4.00);\n(0,0);\n(0,5);\n(600,2);\n"
            "arc(0,600,-0.50,1.50,s,0.00,0.00,0,none,false);\nscenecontrol(0,enwidenlanes,0.00,1);\n")
    chart = aff_to_chart(text, meta())
    assert chart.lanes == 6 and chart.meta.source["aff"]["lanes"] == "0-5"
    assert sorted(n.lane for n in chart.notes if n.kind == NoteKind.TAP) == [0, 2, 5]
    arc = next(n for n in chart.notes if n.kind == NoteKind.AIR_HOLD)
    assert (arc.lane, arc.path[0].lane) == (0.5, 4.5)


def test_arcaea_catalog_offline(tmp_path):
    from vsrgconv.sources import arcaea

    f = Fetcher(tmp_path / "cache", offline=True)
    put(f, arcaea.SONGLIST_URL, (FIX / "arcaea_songlist.json").read_bytes())
    put(f, arcaea.PACKLIST_URL, (FIX / "arcaea_packlist.json").read_bytes())
    put(f, arcaea.MATTY + "data.json", (FIX / "arcaea_matty_sample.json").read_bytes())
    put(f, arcaea.TOOLBELT + "ChartNotes.json", json.dumps({"fairytale": [336, 511, 782, 932]}).encode())
    songs, report = arcaea._catalog(f)                 # the other sources are not cached: noted, not fatal
    by_id = {s.song_id: s for s in songs}
    assert set(by_id) == {"fairytale", "pragmatism", "kanagawa", "primevaltexture"}
    assert report["notes"] and report["songlist_songs"] == 4
    fairy = by_id["fairytale"]
    assert (fairy.genre, fairy.bpm, fairy.version, fairy.date_added) == ("Arcaea", "189", "1.0", "2017-02-25")
    assert list(fairy.charts) == ["normal", "hard", "expert", "master"]
    ftr = fairy.charts["expert"]
    assert (ftr.native, ftr.level, ftr.note_counts, ftr.source["counts_from"]) == ("FUTURE", "7", {"combo": 782},
                                                                                     "toolbelt")
    assert ftr.constant == 7.0 and ftr.source["constant_from"] == "matty"
    assert ftr.source["aff"].startswith(arcaea.AFF_BASE + "songs/fairytale/")
    assert fairy.source["matty"]["jacket"] == "0001"
    assert by_id["kanagawa"].source["matty"]["jacket"]                     # matched by its Japanese title
    assert by_id["primevaltexture"].genre == "Lasting Eden"                # an append pack files under its parent
    byd = by_id["pragmatism"].charts["master"]
    assert byd.level == "11" and byd.source["songlist"]["title_localized"]["en"] == "PRAGMATISM -RESURRECTION-"
    assert arcaea.chart_meta(by_id["pragmatism"], "master").title == "PRAGMATISM -RESURRECTION-"
    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    assert db.song_dir(fairy).parent.name == "arcaea"                       # genre slug of the Arcaea pack


def test_arcaea_chart_source_uses_aff_and_checks_counts(tmp_path):
    from vsrgconv.sources import arcaea

    songlist = json.loads((FIX / "arcaea_songlist.json").read_text("utf-8"))["songs"]
    manifest = {"fairytale": {2: "songs/fairytale/2.aff"}}
    rec = arcaea.songlist_record(songlist[[s["id"] for s in songlist].index("fairytale")], {"base": "Arcaea"},
                                 manifest)
    rec.charts["expert"].note_counts = {"combo": 25}
    rec.source["matty"] = {"jacket": "0001"}
    f = Fetcher(tmp_path / "cache", offline=True)
    url = rec.charts["expert"].source["aff"]
    assert url == arcaea.AFF_BASE + "songs/fairytale/2.aff"
    put(f, url, SAMPLE.encode())
    chart = arcaea.chart_from_aff(f, rec, "expert")
    assert chart.meta.source["extracted_from"] == "harumiena-aff" and chart.meta.level == "7"
    assert chart.meta.source["combo_check"] == "mismatch"
    assert any("differs from the published note count 25" in w for w in chart.warnings)
    assert arcaea.chart_from_aff(f, rec, "normal") is None        # not in the manifest -> no chart, no crash
    put(f, arcaea.MATTY_JACKET.format(n="0001"), b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    data, ext, jurl = arcaea._jacket(f, rec)
    assert ext == "webp" and jurl.endswith("/jacket/0001.png")
    rec.charts["master"].source["jacket"] = arcaea.MATTY_JACKET.format(n="1001")     # BEYOND's own jacket
    put(f, arcaea.MATTY_JACKET.format(n="1001"), b"\x89PNG\r\n")
    assert [(s, e) for s, _, e, _ in arcaea._chart_jackets(f, rec, ["expert", "master"])] == [("master", "png")]


def test_arcaea_manifest_skips_old_revisions(tmp_path):
    from vsrgconv.sources.arcaea import load_manifest

    p = tmp_path / "m.json"
    p.write_text(json.dumps({"paths": ["songs/a/a_0.aff", "songs/a/a_2_old_6.16_1.aff", "songs/b/3.aff",
                                       "songs/c/d_1.aff"]}), "utf-8")
    assert load_manifest(p) == {"a": {0: "songs/a/a_0.aff"}, "b": {3: "songs/b/3.aff"}}
    real = load_manifest()
    assert len(real) >= 560 and sum(len(v) for v in real.values()) >= 1833
