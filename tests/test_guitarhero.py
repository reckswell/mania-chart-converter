"""Guitar Hero: MIDI reader, GH/Clone Hero chart rules (.mid and .chart), song lists, and the adapter (offline)."""
import json
import struct
from fractions import Fraction
from pathlib import Path

from vsrgconv.formats.gh import (GhOptions, dta_songs, parse_ini, parse_q_songlist, q_year, qs_key, read_chart,
                                 read_mid, strip_tags)
from vsrgconv.formats.midi import note_spans, parse_midi
from vsrgconv.model import ChartMeta, NoteKind
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------- a tiny hand-built MIDI

def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _meta(tick, mtype, payload: bytes):
    return (tick, b"\xff" + bytes([mtype]) + _vlq(len(payload)) + payload)


def _name(text):
    return _meta(0, 0x03, text.encode())


def _text(tick, text):
    return _meta(tick, 0x01, text.encode())


def _notes(spec, velocity=100):
    """(tick, length, note) -> note on/off events; offs are note-ons with velocity 0 (so running status applies)."""
    out = []
    for tick, length, note, *vel in spec:
        out.append((tick, bytes([0x90, note, vel[0] if vel else velocity])))
        out.append((tick + length, bytes([0x90, note, 0])))
    return out


def _sysex(tick, payload: bytes):
    return (tick, b"\xf0" + _vlq(len(payload)) + payload)


def _track(events) -> bytes:
    """Events in time order (stable), running status for repeated channel status bytes, end-of-track appended."""
    body, last, status = b"", 0, None
    order = sorted(range(len(events)), key=lambda i: (events[i][0], 0 if events[i][1][:1] == b"\xff" else 1, i))
    for i in order:
        tick, data = events[i]
        delta = tick - last
        last = tick
        if data[0] < 0xF0:
            if data[0] == status:
                data = data[1:]            # running status
            else:
                status = data[0]
        body += _vlq(delta) + data
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(body)) + body


def _midi(tracks, res=480) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), res) + b"".join(_track(t) for t in tracks)


SYNC = [_name("tiny"), _meta(0, 0x51, (500000).to_bytes(3, "big")), _meta(0, 0x58, bytes([4, 2, 24, 8])),
        _meta(1920, 0x51, (400000).to_bytes(3, "big")), _meta(3840, 0x58, bytes([3, 2, 24, 8]))]
GUITAR = [_name("PART GUITAR")] + _notes([
    (0, 960, 96),                    # green, 2-beat sustain
    (480, 60, 97),                   # red, 60 ticks < 160 cutoff -> tap; 480 after green -> strum
    (640, 10, 98),                   # yellow 160 ticks after red -> natural HOPO (threshold 161)
    (800, 10, 98),                   # yellow again: same fret -> strum
    (960, 10, 96), (960, 10, 97),    # green+red chord -> strum
    (1120, 10, 97),                  # red after a chord containing red -> strum (.mid subset rule)
    (1280, 10, 99), (1280, 10, 102),  # blue would be a HOPO, forced strum
    (1920, 10, 100), (1920, 120, 101),  # orange would be a strum, forced HOPO
    (1920, 480, 116),                # star power over the orange only (phrase end excluded)
    (2400, 10, 96), (2400, 1, 104),  # tap marker
    (2880, 10, 96),                  # open (SysEx phrase below)
    (0, 0, 60),                      # one easy note
]) + [_sysex(2870, b"PS\x00\x00\x03\x01\x01\xf7"), _sysex(2890, b"PS\x00\x00\x03\x01\x00\xf7")]
DRUMS = [_name("PART DRUMS"), _text(0, "[ENABLE_CHART_DYNAMICS]")] + _notes([
    (0, 0, 96), (0, 0, 97, 127), (480, 0, 101), (960, 0, 95), (0, 480, 116)])
OTHER = [[_name("PART VOCALS")] + _notes([(0, 100, 60)]), [_name("EVENTS"), _text(0, "[section Intro]")]]


def meta(slot="expert"):
    return ChartMeta(game="guitarhero", song_id="t", title="T", difficulty=slot)


def test_midi_reader_running_status_meta_and_sysex():
    m = parse_midi(_midi([SYNC, GUITAR, DRUMS] + OTHER))
    assert m.resolution == 480 and [t.name for t in m.tracks][1:] == ["PART GUITAR", "PART DRUMS", "PART VOCALS",
                                                                      "EVENTS"]
    assert [(e.tick, e.value) for e in m.tempo_events()] == [(0, 500000), (1920, 400000)]
    assert [(e.tick, e.numerator, e.denominator) for e in m.time_signatures()] == [(0, 4, 4), (3840, 3, 4)]
    spans = note_spans(m.tracks[1])
    assert (0, 960, 96) in [(s.tick, s.length, s.note) for s in spans]
    assert [e.data for e in m.tracks[1].events if e.kind == "sysex"][0] == b"PS\x00\x00\x03\x01\x01\xf7"
    assert next(s for s in note_spans(m.tracks[2]) if s.note == 97).velocity == 127


def test_gh_mid_notes_hopos_forcing_taps_open_star_power():
    song = read_mid(_midi([SYNC, GUITAR, DRUMS] + OTHER))
    assert song.parts() == {"drums": ["expert", "expert+"], "guitar": ["easy", "expert"]}
    c = song.to_chart("guitar", "expert", meta())
    assert c.lanes == 5 and c.meta.source["track"] == "PART GUITAR"
    assert [(b.beat, b.bpm) for b in c.timing.bpms] == [(0, 120.0), (4, 150.0)]
    assert [(m.beat, m.numerator) for m in c.timing.meters] == [(0, 4), (8, 3)]
    got = [(n.beat, n.lane, n.width, n.kind.value, n.duration,
            {k: v for k, v in n.extra.items()}) for n in c.notes]
    assert got == [
        (0, 0, 1, "hold", 2, {}),
        (1, 1, 1, "tap", 0, {}),
        (Fraction(4, 3), 2, 1, "tap", 0, {"hopo": True}),
        (Fraction(5, 3), 2, 1, "tap", 0, {}),
        (2, 0, 1, "tap", 0, {}), (2, 1, 1, "tap", 0, {}),
        (Fraction(7, 3), 1, 1, "tap", 0, {}),
        (Fraction(8, 3), 3, 1, "tap", 0, {"forced": "strum"}),
        (4, 4, 1, "tap", 0, {"hopo": True, "forced": "hopo", "star_power": True}),
        (5, 0, 1, "tap", 0, {"tap": True}),
        (6, 0, 5, "tap", 0, {"open": True}),
    ]
    assert len(song.to_chart("guitar", "easy", meta("easy")).notes) == 1
    assert song.to_chart("bass", "expert", meta()) is None


def test_gh_mid_drums_five_lane_kick_accent_and_expert_plus():
    song = read_mid(_midi([SYNC, GUITAR, DRUMS] + OTHER))
    ex = song.to_chart("drums", "expert", meta())
    assert ex.lanes == 5
    assert [(n.beat, n.lane, n.width, n.extra) for n in ex.notes] == [
        (0, 0, 5, {"kick": True, "star_power": True}),          # the kick sorts before a pad at the same beat
        (0, 0, 1, {"accent": True, "star_power": True}),
        (1, 4, 1, {}),
    ]
    plus = song.to_chart("drums", "expert+", meta("drums-master"))
    assert len(plus.notes) == 4 and plus.notes[-1].extra == {"kick": True, "double_kick": True}


def test_gh_mid_harmonix_layout_per_difficulty_star_power_snap_and_170_tick_hopos():
    track = [_name("PART GUITAR")] + _notes([
        (0, 10, 96), (5, 10, 97),        # 5 ticks apart: one chord (GH1/GH2 snap)
        (170, 10, 98),                   # 170 ticks after the chord, not in it -> HOPO
        (405, 300, 99),                  # 230 later -> strum; 300-tick sustain kept (cutoff 160)
        (0, 200, 103),                   # expert star power
        (0, 10, 84), (350, 10, 85), (300, 200, 91),   # hard notes, hard star power
    ])
    song = read_mid(_midi([SYNC[:3], track]), GhOptions(harmonix=True))
    ex = song.to_chart("guitar", "expert", meta())
    assert [(n.beat, n.lane) for n in ex.notes][:2] == [(0, 0), (0, 1)]
    assert [n.extra for n in ex.notes] == [{"star_power": True}, {"star_power": True},
                                          {"hopo": True, "star_power": True}, {}]
    assert ex.notes[-1].kind is NoteKind.HOLD and ex.notes[-1].duration == Fraction(300, 480)
    assert any("snapped" in w for w in ex.warnings)
    hard = song.to_chart("guitar", "hard", meta("hard"))
    assert [n.extra.get("star_power", False) for n in hard.notes] == [False, True]
    # the same file read as a Clone Hero chart: no 116 anywhere, so per-difficulty markers are still detected
    assert read_mid(_midi([SYNC[:3], track])).to_chart("guitar", "hard", meta()).notes[1].extra["star_power"]


def test_gh_chart_format():
    song = read_chart((FIX / "guitarhero_sample.chart").read_text("utf-8"))
    assert song.resolution == 192 and song.offset_ms == 500.0
    assert song.sections == [(0, "Intro"), (768, "Verse 1")]
    assert song.parts() == {"bass": ["expert"], "drums": ["expert", "expert+"], "guitar": ["expert"],
                            "guitar_ghl": ["expert"]}
    c = song.to_chart("guitar", "expert", meta())
    assert [(b.beat, b.bpm) for b in c.timing.bpms] == [(0, 120.0), (4, 180.5)]
    assert [(m.beat, m.numerator, m.denominator) for m in c.timing.meters] == [(0, 4, 4), (4, 3, 4)]
    assert c.timing.offset_ms == 500.0
    got = [(n.beat, n.lane, n.width, n.duration, n.extra) for n in c.notes]
    assert got == [
        (0, 0, 1, 0, {"star_power": True}),
        (Fraction(1, 3), 1, 1, 0, {"hopo": True, "star_power": True}),     # 64 ticks <= 65
        (Fraction(2, 3), 1, 1, 0, {"star_power": True}),                   # same fret
        (1, 2, 1, 0, {"forced": "strum"}),                                 # N 5 flips a natural HOPO
        (2, 3, 1, 0, {"tap": True}),
        (3, 0, 5, Fraction(1, 2), {"open": True}),                         # open, .chart keeps short sustains
        (4, 0, 1, 0, {"solo": True}), (4, 4, 1, 0, {"solo": True}),
        (Fraction(13, 3), 4, 1, 0, {"hopo": True, "solo": True}),          # no chord-subset rule in .chart
        (5, 1, 1, 2, {"solo": True}),                                      # soloend's own tick is in the solo
    ]
    assert c.notes[5].kind is NoteKind.HOLD
    bass = song.to_chart("bass", "expert", meta())
    assert [(n.lane, n.width) for n in bass.notes] == [(0, 1), (0, 5)]
    drums = song.to_chart("drums", "expert", meta())
    assert drums.lanes == 5
    assert [(n.beat, n.lane, n.width, n.extra) for n in drums.notes] == [
        (0, 0, 5, {"kick": True}), (0, 0, 1, {"accent": True}), (Fraction(1, 2), 4, 1, {}),
        (Fraction(3, 2), 1, 1, {"star_power": True})]
    assert len(song.to_chart("drums", "expert+", meta()).notes) == 5
    ghl = song.to_chart("guitar_ghl", "expert", meta())
    assert ghl.lanes == 6 and [(n.lane, n.width) for n in ghl.notes] == [(0, 1), (3, 1), (2, 1), (0, 6)]


def test_song_ini_options_and_tags():
    ini = parse_ini("[song]\nname = X\ncharter = <color=orange>Neversoft</color>\nhopo_frequency = 250\n"
                    "multiplier_note = 103\nfive_lane_drums = True\ndelay = -30\n")
    o = GhOptions.from_ini(ini)
    assert (o.hopo_threshold, o.star_power_note, o.five_lane_drums, o.delay_ms) == (250, 103, True, -30.0)
    assert strip_tags(ini["charter"]) == "Neversoft" and strip_tags("Harm<color=#0072bc>o</color>nix") == "Harmonix"


# ---------------------------------------------------------------- song lists

Q_TEXT = """gh3_songlist = [
\tfixturesong
]
permanent_songlist_props = {
\tfixturesong = {
\t\tchecksum = fixturesong
\t\tName = 'fixturesong'
\t\ttitle = "Fixture Song"
\t\tartist = "vsrgconv"
\t\tyear = ", 2007"
\t\toriginal_artist = 0
\t\trhythm_track = 0
\t}
\tdlc77 = {
\t\tchecksum = dlc77
\t\ttitle = qs(0x%08x)
\t\tartist = qs(0x12345678)
\t\tyear = qs(0x%08x)
\t\tchallenge_props = {
\t\t\tguitar = [
\t\t\t]
\t\t}
\t\tdouble_kick = 1
\t}
}
""" % (qs_key("\\LHashed Song"), qs_key("\\L, 1999"))

DTA_TEXT = """(badreputation
   (name "Bad Reputation")
   (artist "Thin Lizzy")
   (song
      (name songs/badreputation/badreputation)
      (tracks ((guitar (2 3)) (bass 4)))
      ; a comment
      (midi_file songs/badreputation/badreputation.mid)
      (hopo_threshold 250)
   )
   (quickplay
      #ifdef HX_XBOX
      (guitar lespaul)
      #else
      (guitar flying_v)
      #endif
   )
)
"""


def test_q_songlist_plain_hashed_and_dta():
    q = parse_q_songlist(Q_TEXT)
    assert set(q) == {"fixturesong", "dlc77"}
    assert q["fixturesong"]["title"] == "Fixture Song" and q_year(q["fixturesong"]) == 2007
    assert q["dlc77"]["title"] == {"qs": qs_key("\\LHashed Song")} and q_year(q["dlc77"]) == 1999
    assert q["dlc77"]["double_kick"] == 1          # after the nested block
    assert dta_songs(DTA_TEXT) == [{"id": "badreputation", "name": "Bad Reputation", "artist": "Thin Lizzy",
                                    "tracks": ["guitar", "bass"],
                                    "midi_file": "songs/badreputation/badreputation.mid", "hopo_threshold": 250}]


# ---------------------------------------------------------------- the adapter, offline

SCORESHOTS = {
    "Guitar Hero II": {"disc": [{"song": "Bad Reputation", "artist": "Thin Lizzy"}], "dlc": []},
    "Guitar Hero III: Legends of Rock": {"disc": [{"song": "Fixture Song", "artist": "vsrgconv"}],
                                         "dlc": [{"song": "Hashed Song", "artist": "X"},
                                                 {"song": "Fan Song", "artist": "Y"}]},
    "Band Hero": {"disc": [{"song": "Not Mainline", "artist": "Z"}], "dlc": []},
}
RIP = {"repo": "someone/CloneHero", "dir": "GH3/vsrgconv - Fixture Song", "notes": "notes.chart", "art": "album.png",
       "ini": {"name": "Fixture Song", "artist": "vsrgconv", "charter": "<color=orange>Neversoft</color>",
               "icon": "gh3", "year": "2006", "album": "Fixtures"},
       "parts": {"guitar": ["expert"], "bass": ["expert"], "drums": ["expert", "expert+"], "guitar_ghl": ["expert"]}}
COOP_RIP = {"repo": "other/Songs", "dir": "vsrgconv - Fixture Song (Co-op)", "notes": "notes.mid", "art": "",
            "ini": {"name": "Fixture Song (Co-op)", "artist": "vsrgconv", "charter": "Neversoft", "icon": "gh3"},
            "parts": {"guitar": ["easy", "medium", "hard", "expert"], "bass": ["easy", "medium", "hard", "expert"]}}
FAN_RIP = {"repo": "fan/Songs", "dir": "Fan Song", "notes": "notes.mid", "art": "album.jpg",
           "ini": {"name": "Fan Song", "artist": "Y", "charter": "Some Fan", "icon": "gh3dlc"},
           "parts": {"guitar": ["expert"]}}
MANIFEST = {"songlists": {"gh3": ["gh3/360/base/scripts/guitar/songlist.q"]}, "rips": [RIP, COOP_RIP, FAN_RIP, RIP],
            "gh2dx": {"badreputation": {"list": "gh2", "midi": "songs/badreputation/badreputation.mid",
                                        "parts": {"guitar": ["easy", "medium", "hard", "expert"],
                                                  "bass": ["expert"]}}}}


def _catalog():
    from vsrgconv.sources.guitarhero import build_catalog

    songs, report = build_catalog(SCORESHOTS, MANIFEST,
                                  {"gh3": [("gh3/360/base/scripts/guitar/songlist.q", Q_TEXT)]}, {"gh2": DTA_TEXT})
    return {s.song_id: s for s in songs}, report


def test_guitarhero_catalog_merges_lists_rips_and_slots():
    songs, report = _catalog()
    assert set(songs) == {"gh2-bad-reputation", "gh3-fixture-song", "gh3-hashed-song", "gh3-fan-song"}
    fx = songs["gh3-fixture-song"]
    assert fx.genre == "Guitar Hero III: Legends of Rock" and fx.genre_slug == "gh3"
    assert fx.extra["year"] == 2007 and fx.extra["cover"] and fx.extra["dlc"] is False
    assert fx.extra["coop_part"] == "bass" and fx.extra["source_repo"] == "someone/CloneHero"
    # GH3 slots: lead + bass (from the .q) + co-op lead (from the "(Co-op)" folder); the main rip's drums (fan-made:
    # GH3 has none) and 6-fret track are ignored
    assert sorted(fx.charts) == sorted(["easy", "normal", "hard", "expert", "bass-easy", "bass-normal", "bass-hard",
                                        "bass-expert", "coop-easy", "coop-normal", "coop-hard", "coop-expert"])
    assert fx.charts["coop-hard"].source["file"] == {
        "from": "clonehero-rip", "url": "https://raw.githubusercontent.com/other/Songs/HEAD/"
        "vsrgconv%20-%20Fixture%20Song%20%28Co-op%29/notes.mid", "part": "guitar", "difficulty": "hard",
        "rip": "other/Songs/vsrgconv - Fixture Song (Co-op)"}
    # bass: the plain folder wins where it has the difficulty (expert); the co-op folder fills the others
    assert fx.charts["bass-expert"].source["file"]["rip"] == "someone/CloneHero/GH3/vsrgconv - Fixture Song"
    assert fx.charts["bass-hard"].source["file"]["rip"] == "other/Songs/vsrgconv - Fixture Song (Co-op)"
    assert fx.source["rip"] == "someone/CloneHero/GH3/vsrgconv - Fixture Song" and len(fx.source["rips"]) == 2
    assert fx.charts["expert"].source["file"]["url"].endswith("/GH3/vsrgconv%20-%20Fixture%20Song/notes.chart")
    assert fx.charts["expert"].designer == "Neversoft" and fx.charts["bass-expert"].native == "BASS EXPERT"
    assert fx.charts["easy"].source == {} and fx.charts["easy"].notes == "not in the chart files"
    hashed = songs["gh3-hashed-song"]
    assert hashed.extra["year"] == 1999 and hashed.extra["dlc"] and hashed.extra["coop_part"] == "unknown"
    assert sorted(hashed.charts) == ["easy", "expert", "hard", "normal"]
    fan = songs["gh3-fan-song"]
    assert "rip" not in fan.source and fan.source["art"].endswith("/Fan%20Song/album.jpg")   # art only
    br = songs["gh2-bad-reputation"]
    assert br.source["gh2dx"]["hopo_threshold"] == 250 and br.extra["coop_part"] == "bass"
    assert br.charts["bass-expert"].source["file"]["from"] == "gh2dx"
    assert br.charts["bass-easy"].status == "listed" and not br.charts["bass-easy"].source
    assert report["per_game"]["gh3"] == {"songs": 3, "with_chart_file": 1, "with_art": 2}


def test_guitarhero_chart_and_jacket_offline(tmp_path):
    from vsrgconv.sources.guitarhero import _chart, _jacket

    songs, _ = _catalog()
    f = Fetcher(tmp_path / "cache", offline=True)
    fx = songs["gh3-fixture-song"]
    p = f.cache_path(fx.charts["expert"].source["file"]["url"])
    p.parent.mkdir(parents=True)
    p.write_bytes((FIX / "guitarhero_sample.chart").read_bytes())
    c = _chart(f, fx, "expert")
    assert c.meta.source["extracted_from"] == "clonehero-chart" and c.meta.difficulty_native == "EXPERT"
    assert len(c.notes) == 10 and c.meta.designer == "Neversoft"
    assert len(_chart(f, fx, "bass-expert").notes) == 2
    assert _chart(f, fx, "hard") is None                       # slot listed, not in the file
    ap = f.cache_path(fx.source["art"])
    ap.write_bytes(b"png")
    assert _jacket(f, fx) == (b"png", "png", fx.source["art"])
    br = songs["gh2-bad-reputation"]
    mp = f.cache_path(br.source["gh2dx"]["url"])
    mp.parent.mkdir(parents=True)
    mp.write_bytes(_midi([SYNC, GUITAR]))
    g = _chart(f, br, "expert")
    assert g.meta.source["extracted_from"] == "gh2dx-mid" and g.notes[0].kind is NoteKind.HOLD
    assert _jacket(f, songs["gh3-hashed-song"]) is None


def test_guitarhero_registered_and_genres():
    from vsrgconv.games.registry import genre_slug
    from vsrgconv.sources import load_all

    assert "guitarhero" in load_all()
    assert genre_slug("guitarhero", "Guitar Hero") == "gh1"
    assert genre_slug("guitarhero", "Guitar Hero: Warriors of Rock") == "ghwor"


def test_guitarhero_manifest_is_valid_json():
    from vsrgconv.sources.guitarhero import MANIFEST as path, load_manifest

    m = load_manifest(path)
    assert isinstance(m.get("rips"), list) and isinstance(m.get("songlists"), dict)
    for rip in m["rips"]:
        assert rip["repo"] and "ini" in rip and isinstance(rip.get("parts", {}), dict)
    json.dumps(m)


def test_iconless_official_rip_is_placed_by_studio_and_title():
    from vsrgconv.sources.guitarhero import SongIndex, base_records, guess_game

    songs = base_records({"Guitar Hero": {"disc": [{"song": "Thunder Kiss '65", "artist": "White Zombie"}]},
                          "Guitar Hero II": {"disc": [{"song": "Thunder Kiss '65", "artist": "White Zombie"}]},
                          "Guitar Hero III: Legends of Rock": {"disc": [{"song": "One", "artist": "Metallica"}]}})
    by_game = {}
    for s in songs:
        by_game.setdefault(s.extra["game"], []).append(s)
    idx = {g: SongIndex(ss) for g, ss in by_game.items()}
    rip = {"dir": "integration_tests/thunder-kiss-65-gh1", "parts": {"guitar": ["expert"]},
           "ini": {"name": "Thunder Kiss '65", "artist": "White Zombie", "charter": "Harmonix"}}
    assert guess_game(rip, idx).song_id == "gh1-thunder-kiss-65"          # the folder name breaks the GH1/GH2 tie
    assert guess_game({**rip, "dir": "x"}, idx) is None                    # ambiguous without it
    assert guess_game({**rip, "parts": {"guitar": ["expert"], "drums": ["expert"]}}, idx) is None   # Rock Band
    assert guess_game({**rip, "ini": {**rip["ini"], "charter": "fan, Harmonix"}}, idx) is None
    one = {"dir": "One", "parts": {"guitar": ["expert"]}, "ini": {"name": "One", "artist": "Metallica",
                                                                   "charter": "Neversoft"}}
    assert guess_game(one, idx).song_id == "gh3-one"


def test_rip_sustain_cutoff_zero_keeps_short_sustains_but_not_placeholder_lengths():
    track = [_name("PART GUITAR")] + _notes([(0, 1, 96), (480, 5, 97), (960, 39, 98), (1440, 40, 99),
                                             (1920, 120, 100)])
    data = _midi([SYNC[:3], track])
    o = GhOptions.from_ini({"sustain_cutoff_threshold": "0"})
    c = read_mid(data, o).to_chart("guitar", "expert", meta())
    assert [n.duration for n in c.notes] == [0, 0, 0, Fraction(40, 480), Fraction(120, 480)]
    default = read_mid(data).to_chart("guitar", "expert", meta())             # no ini: 1/12 note = 160 ticks
    assert [n.duration for n in default.notes] == [0, 0, 0, 0, 0]
