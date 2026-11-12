"""Chart layout pictures drawn from the stored notes (imaging/render.py) and the `render` command."""
import json
import math
from fractions import Fraction
from pathlib import Path

import pytest

from vsrgconv.db import ChartDB, ChartRecord, SongRecord
from vsrgconv.formats.c2s import c2s_to_chart
from vsrgconv.imaging.render import (BACKGROUND, COLOURS, LANE_BG, RAIL, RenderConfig, _columns, measure_starts,
                                     preempt_ms, render_chart, render_png)
from vsrgconv.model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, PathPoint, TimingMap

FIX = Path(__file__).parent / "fixtures"


def chart(notes: list[Note], lanes: int = 16, bpm: float = 120) -> Chart:
    return Chart(meta=ChartMeta(game="chunithm", song_id="t1", title="Test", difficulty="master",
                                difficulty_native="MASTER", level="13"),
                 lanes=lanes, timing=TimingMap(bpms=[BpmChange(Fraction(0), bpm)], meters=[MeterChange(Fraction(0))]),
                 notes=notes)


def colours_in(img) -> set[tuple[int, int, int]]:
    return {c for _, c in img.convert("RGB").getcolors(maxcolors=1 << 22)}


def test_columns_hold_a_fixed_number_of_measures():
    """Compact layout: 8 measures (32 beats, 768 px) a column."""
    c = chart([Note(Fraction(79), 0, 1, NoteKind.TAP)])           # last note in measure 20 (4/4)
    cols, cut = _columns(c, RenderConfig(axis="beats", measures_per_column=8))
    assert [(col.start, col.end) for col in cols] == [(0, 768), (768, 1536), (1536, 1920)] and not cut
    cols, cut = _columns(c, RenderConfig(axis="beats", measures_per_column=8, max_columns=2))
    assert len(cols) == 2 and cut


def test_approach_rate_spaces_notes_by_time():
    """AR 9: a note crosses 480 px in 600 ms, 0.8 px per ms. 1/4 beats at 150 BPM (100 ms) are 80 px apart."""
    assert (preempt_ms(9), preempt_ms(5), preempt_ms(10), preempt_ms(0)) == (600, 1200, 450, 1800)
    taps = [Note(Fraction(k, 4), 0, 1, NoteKind.TAP) for k in range(4)]
    for ar, gap in [(9, 80), (5, 40)]:
        cfg = RenderConfig(approach_rate=ar)
        img = render_chart(chart(taps, lanes=4, bpm=150), cfg).convert("RGB")
        x = cfg.margin_px + cfg.gutter_px + cfg.rail_px + cfg.lane_area_px // 8    # middle of lane 0, first column
        ys = [y for y in range(img.height) if img.getpixel((x, y)) == COLOURS["tap"]
              and img.getpixel((x, y + 1)) != COLOURS["tap"]]                # bottom row of each tap
        assert [a - b for a, b in zip(ys[::-1], ys[::-1][1:])] == [gap] * 3, (ar, ys)
        assert img.getpixel((cfg.margin_px + cfg.gutter_px, ys[0])) == RAIL      # sdvx.in's gold rails
        assert img.getpixel((cfg.margin_px + cfg.gutter_px + cfg.rail_px + cfg.lane_area_px, ys[0])) == RAIL


def test_time_columns_end_on_measure_lines():
    """At 160 BPM a 4/4 measure is 1500 ms, 1200 px: two measures fit the 2400 px column."""
    c = chart([Note(Fraction(b), b % 4, 1, NoteKind.TAP) for b in range(40)], lanes=4, bpm=160)
    cols, cut = _columns(c, RenderConfig())
    assert not cut and len(cols) == 5
    assert all(round(col.start) % 2400 == 0 and 2400 <= round(col.end - col.start) <= 2401 for col in cols)
    # 70 BPM: a measure is 2743 px, so the columns grow to one measure; at 40 BPM (4800 px) measures are split
    slow = chart([Note(Fraction(7), 0, 1, NoteKind.TAP)], lanes=4, bpm=70)
    cols, _ = _columns(slow, RenderConfig())
    assert [round(col.end) for col in cols] == [2743, 5486]
    slower = chart([Note(Fraction(7), 0, 1, NoteKind.TAP)], lanes=4, bpm=40)
    cols, _ = _columns(slower, RenderConfig())
    assert [round(col.end) for col in cols] == [2400, 4800, 7200, 9600]


def test_long_pauses_are_drawn_short():
    """Pump It Up ends charts with a 999-second stop or delay: it must not become hundreds of empty columns."""
    for pause in ("stops", "delays"):
        c = chart([Note(Fraction(b), b % 4, 1, NoteKind.TAP) for b in range(8)], lanes=4)
        setattr(c.timing, pause, [(Fraction(7), 999_000.0)])
        cols, cut = _columns(c, RenderConfig())
        assert not cut and len(cols) <= 3 and cols[-1].end < 3 * 2401, pause
    mid = chart([Note(Fraction(0), 0, 1, NoteKind.TAP), Note(Fraction(4), 0, 1, NoteKind.TAP)], lanes=4)
    mid.timing.stops = [(Fraction(2), 60_000.0)]                    # a minute's stop is drawn as 2 s (1,600 px)
    cols, _ = _columns(mid, RenderConfig(max_pause_ms=2000))
    assert round(cols[-1].end) == 3200 + 1600                       # beat 4 + the stop, closed at beat 8


def test_wide_pictures_wrap_into_rows():
    cfg = RenderConfig(max_width_px=700)                                  # 3 columns of 222 px a row
    c = chart([Note(Fraction(b), 0, 1, NoteKind.TAP) for b in range(80)], lanes=4)   # 20 measures, 1600 px each
    cols, _ = _columns(c, cfg)
    img = render_chart(c, cfg)
    rows = math.ceil(len(cols) / 3)
    assert len(cols) == 20 and img.width <= 700 and rows == 7
    col_h = max(round(col.end - col.start) for col in cols)
    assert img.height == 2 * cfg.margin_px + rows * (col_h + cfg.row_gap_px) - cfg.row_gap_px + cfg.footer_px
    last_row = img.convert("RGB").crop((0, img.height - col_h - cfg.footer_px - cfg.margin_px, img.width,
                                        img.height - cfg.footer_px))
    assert COLOURS["tap"] in colours_in(last_row)                          # the last measures are drawn too


def test_measure_starts_jump_over_runs_of_one_time_signature():
    t = TimingMap(bpms=[BpmChange(Fraction(0), 120)],
                  meters=[MeterChange(Fraction(0)), MeterChange(Fraction(10), 3, 4), MeterChange(Fraction(10**9), 7, 8)])
    every = t.measure_starts(Fraction(40))
    assert list(measure_starts(t, Fraction(0), Fraction(40))) == list(enumerate(every, 1))
    # a window a billion beats in: measure numbers still count from the start, without listing every measure
    far = list(measure_starts(t, Fraction(10**9), Fraction(10**9 + 7)))
    assert far[0][1] >= 10**9 and far[1][1] - far[0][1] == Fraction(7, 2)
    assert far[0][0] == 1 + 3 + math.ceil((Fraction(10**9) - 12) / 3)


def test_gimmick_bpm_charts_are_drawn_on_a_time_axis():
    """Arcaea-style teleports: 99,999,999 BPM for a million beats takes under a millisecond."""
    t = TimingMap(bpms=[BpmChange(Fraction(0), 200), BpmChange(Fraction(16), 99_999_999),
                        BpmChange(Fraction(1_000_016), 200)], meters=[MeterChange(Fraction(0))])
    c = Chart(meta=ChartMeta(game="arcaea", song_id="g", title="G"), lanes=4, timing=t,
              notes=[Note(Fraction(b), b % 4, 1, NoteKind.TAP) for b in range(16)]
              + [Note(Fraction(1_000_016 + b), b % 4, 1, NoteKind.TAP) for b in range(16)])
    img = render_chart(c, RenderConfig.compact())
    assert img.width < 400 and img.height < 1000                    # 32 beats of time: one column
    cols, cut = _columns(c, RenderConfig(axis="beats", max_columns=300))   # in beats: 31,251 columns long
    assert len(cols) == 300 and cut
    assert COLOURS["tap"] in colours_in(img)
    # at AR 9: 32 beats at 200 BPM and the 600 ms jump are 8,160 px, five columns
    img = render_chart(c)
    assert img.width < 1200 and img.height < 2600 and COLOURS["tap"] in colours_in(img)


def test_notes_are_drawn_where_they_are():
    """Compact layout. A hold on lanes 3-4, beats 0-4, of a 16-lane chart: lane px = 128 / 16 = 8, 24 px per beat."""
    cfg = RenderConfig.compact()
    hold = Note(Fraction(0), 3, 2, NoteKind.HOLD, Fraction(4))
    slide = Note(Fraction(4), 0, 2, NoteKind.SLIDE, Fraction(4), path=[PathPoint(Fraction(4), 12, 2)])
    img = render_chart(chart([hold, slide, Note(Fraction(7), 8, 1, NoteKind.TAP)]), cfg).convert("RGB")
    bottom = img.height - cfg.footer_px - cfg.margin_px                # one column, as tall as its measures

    def x(lane: float) -> int:                                          # centre of a lane
        return int(cfg.margin_px + cfg.gutter_px + (lane + 0.5) * 8)

    def y(beat: float) -> int:
        return int(bottom - beat * cfg.px_per_beat)

    body = img.getpixel((x(4), y(2) - 3))
    assert body != LANE_BG and body[0] > body[2]                        # orange hold body
    assert img.getpixel((x(10), y(2) - 3)) == LANE_BG                   # nothing in lane 10
    # the slide moves from lane 0 to lane 12 over beats 4-8: halfway it is around lane 6-8
    assert img.getpixel((x(7), y(6) - 3)) != LANE_BG
    assert img.getpixel((x(1), y(6) - 3)) == LANE_BG
    assert img.getpixel((x(8), y(7))) == COLOURS["tap"]
    assert img.getpixel((1, 1)) == BACKGROUND


def test_every_c2s_note_kind_gets_its_colour():
    c = c2s_to_chart((FIX / "sample.c2s").read_text("utf-8"),
                     ChartMeta(game="chunithm", song_id="t1", title="T", difficulty="master"))
    seen = colours_in(render_chart(c))
    kinds = {n.kind.value for n in c.notes if n.kind is not NoteKind.OTHER}
    heads = {k for k in kinds if k not in ("air_hold", "slide")}   # bodies are translucent: their colour is blended
    assert {COLOURS[k] for k in heads} <= seen


def test_png_is_small_and_palette():
    from io import BytesIO

    from PIL import Image

    c = chart([Note(Fraction(b), b % 16, 1, NoteKind.TAP) for b in range(64)])
    data = render_png(c)
    img = Image.open(BytesIO(data))
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and img.mode == "P" and len(data) < 30_000
    half = Image.open(BytesIO(render_png(c, scale=0.5)))
    assert half.width == render_chart(c).width // 2


def test_render_command_mirrors_the_database(tmp_path, capsys):
    from vsrgconv.cli import main

    db = ChartDB(tmp_path / "db")
    rec = SongRecord(game="chunithm", song_id="t1", title="Test", genre="ORIGINAL",
                     charts={"master": ChartRecord("master", "MASTER", "13"),
                             "special": ChartRecord("special", "WORLD'S END", source={
                                 "picture": "https://example.invalid/t1we.png"})})
    db.upsert_song(rec)
    db.save_chart(c2s_to_chart((FIX / "sample.c2s").read_text("utf-8"),
                               ChartMeta(game="chunithm", song_id="t1", title="Test", difficulty="master")))
    out, report = tmp_path / "renders", tmp_path / "report.json"
    args = ["--db", str(tmp_path / "db"), "render", "chunithm", "--out", str(out), "--report", str(report)]
    assert main(args) == 0
    png = out / db.song_dir(db.get_song("chunithm", "t1")).relative_to(db.root) / "master.png"
    assert png.exists()
    r = json.loads(report.read_text("utf-8"))
    assert r["summary"]["chunithm"] == {"charts": 2, "with_notes": 1, "rendered": 1, "already": 0, "failed": 0,
                                        "bytes": png.stat().st_size, "without_notes": 1,
                                        "without_notes_with_reference": 1}
    assert r["without_notes"] == [{"game": "chunithm", "song_id": "t1", "slot": "special", "status": "listed",
                                   "references": {"picture": "https://example.invalid/t1we.png"}}]
    capsys.readouterr()
    assert main(args) == 0                                              # second run: nothing to draw
    assert json.loads(capsys.readouterr().out)["summary"]["chunithm"]["already"] == 1


def test_publish_uploads_the_data_folders_and_removes_what_is_gone(tmp_path):
    from vsrgconv.publish import publish_hf

    (tmp_path / "db" / "chunithm").mkdir(parents=True)
    (tmp_path / "db" / "chunithm" / "index.json").write_text("{}")
    (tmp_path / "renders").mkdir()
    (tmp_path / "renders" / "a.png").write_bytes(b"x" * 10)

    class FakeApi:
        def __init__(self):
            self.calls = []

        def create_repo(self, repo_id, **kw):
            self.calls.append(("create", repo_id, kw))

        def upload_folder(self, **kw):
            self.calls.append(("upload", kw))
            return type("Info", (), {"commit_url": "https://hf.example/commit/1"})()

        def list_repo_files(self, repo_id, **kw):
            return ["db/chunithm/index.json", "renders/a.png", "renders/old.png", "README.md"]

        def delete_files(self, repo_id, paths, **kw):
            self.calls.append(("delete", paths))

    dry = publish_hf("me/charts", tmp_path, ["db", "renders"], dry_run=True)
    assert dry["parts"] == {"db": {"files": 1, "bytes": 2}, "renders": {"files": 1, "bytes": 10}}
    api = FakeApi()
    out = publish_hf("me/charts", tmp_path, ["db", "renders"], delete=True, api=api)
    assert api.calls[0] == ("create", "me/charts", {"repo_type": "dataset", "private": True, "exist_ok": True})
    assert api.calls[1][1]["allow_patterns"] == ["db/**", "renders/**"]
    assert api.calls[2] == ("delete", ["renders/old.png"]) and out["deleted"] == 1


def test_publish_resumes_when_a_commit_answer_is_lost():
    """The proxy can time out on Hugging Face's answer to a commit that did land: carry on while the dataset moves."""
    from vsrgconv.publish import _resuming

    class Api:
        sha = "a"

        def repo_info(self, repo_id, **kw):
            return type("Info", (), {"sha": self.sha})()

    api, calls = Api(), []

    def step():
        calls.append(1)
        if len(calls) < 4:
            api.sha += "x"                       # the commit landed...
            raise ConnectionError("502 Bad Gateway")   # ...but its answer did not
        return "done"

    assert _resuming(api, "me/charts", step, log=lambda m: None) == "done" and len(calls) == 4

    def stuck():
        raise ConnectionError("401 Unauthorized")    # nothing lands: give up after 2 more tries

    calls.clear()
    with pytest.raises(ConnectionError):
        _resuming(api, "me/charts", lambda: calls.append(1) or stuck(), log=lambda m: None)
    assert len(calls) == 3
