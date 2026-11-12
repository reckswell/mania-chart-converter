"""Source pictures (the "true source") next to the database, and A/B checks against the stored notes."""
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from vsrgconv import pictures as pic
from vsrgconv.db import ChartDB, ChartRecord, SongRecord
from vsrgconv.formats.c2s import c2s_to_chart
from vsrgconv.model import ChartMeta
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"
URL = "https://example.invalid/pics/{}.png"


def png(colour, size=(400, 300)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def setup_db(tmp_path):
    db = ChartDB(tmp_path / "db")
    rec = SongRecord(game="chunithm", song_id="t1", title="Test", genre="ORIGINAL", charts={
        "master": ChartRecord("master", "MASTER", "13", source={"picture": URL.format("t1mst")}),
        "expert": ChartRecord("expert", "EXPERT", "11", source={"picture": URL.format("same"),
                                                                "sdvxindex_picture": URL.format("t1exp")}),
        "extra": ChartRecord("extra", "ULTIMA", "14", source={"picture": URL.format("same")}),
        "hard": ChartRecord("hard", "ADVANCED", "8")})
    db.upsert_song(rec)
    db.save_chart(c2s_to_chart((FIX / "sample.c2s").read_text("utf-8"),
                               ChartMeta(game="chunithm", song_id="t1", title="Test", difficulty="master")))
    cache = tmp_path / "cache"
    f = Fetcher(cache_dir=cache, offline=True)
    for name, colour in [("t1mst", (200, 0, 0)), ("same", (0, 200, 0))]:     # t1exp is not cached: fails offline
        p = f.cache_path(URL.format(name))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png(colour))
    return db, f


def test_picture_urls_prefer_the_main_picture():
    cr = ChartRecord("expert", "EXPERT", source={"sdvxindex_picture": "https://b/x.png", "picture": "https://a/x.png",
                                                 "aff": "https://c/x.aff", "x_picture": 3})
    assert pic.picture_urls(cr) == [("picture", "https://a/x.png"), ("sdvxindex_picture", "https://b/x.png")]


def test_pictures_are_kept_as_published_beside_the_database(tmp_path):
    db, f = setup_db(tmp_path)
    out = tmp_path / "pictures"
    s = pic.fetch_pictures(db, f, "chunithm", out, workers=2)
    assert (s["charts"], s["with_picture"], s["downloaded"], s["failed"]) == (4, 3, 3, 0)
    rel = db.song_dir(db.get_song("chunithm", "t1")).relative_to(db.root)
    master = out / rel / "master.png"
    assert master.read_bytes() == f.cache_path(URL.format("t1mst")).read_bytes()        # the published bytes
    m = json.loads((out / "chunithm" / "manifest.json").read_text("utf-8"))
    assert m["t1/master"]["size"] == [400, 300] and m["t1/master"]["key"] == "picture"
    # two charts that point at one file are marked: the picture is right for at most one of them
    assert m["t1/expert"]["shared_with"] == ["t1/extra"] and "shared_with" not in m["t1/master"]
    again = pic.fetch_pictures(db, f, "chunithm", out)
    assert again["already"] == 3 and again["downloaded"] == 0


def test_a_picture_that_cannot_be_fetched_is_reported(tmp_path):
    db, f = setup_db(tmp_path)
    rec = db.get_song("chunithm", "t1")
    rec.charts["expert"].source = {"picture": URL.format("missing")}
    db.upsert_song(rec)
    s = pic.fetch_pictures(db, f, "chunithm", tmp_path / "pictures")
    assert s["failed"] == 1 and list(s["failed_charts"]) == ["t1/expert"]


def test_ab_pairs_source_pictures_with_stored_notes(tmp_path, monkeypatch):
    db, f = setup_db(tmp_path)
    out = tmp_path / "pictures"
    pic.fetch_pictures(db, f, "chunithm", out)
    jobs, counts = pic.ab_jobs(db, "chunithm", out)
    assert counts == {"pictures": 3, "with_notes": 1, "without_notes": 2} and jobs[0]["key"] == "t1/master"
    seen = {}

    def fake_reader(picture, chart):
        seen["notes"] = len(chart.notes)
        return {"notes": 10, "matched": 9, "agreement": 0.9, "beat_px": 48}

    monkeypatch.setitem(pic.READERS, "chunithm", fake_reader)
    res = pic.ab_one(jobs[0], tmp_path / "ab")
    assert res["agreement"] == 0.9 and seen["notes"] > 0 and "error" not in res
    img = Image.open(res["image"])
    assert img.width >= 400 and img.height > 300 + 2 * 22                  # A above B
    assert pic.ab_summary([res, {"key": "x", "error": "boom"}])["compared"] == 1


def test_ab_command_draws_comparisons_even_when_the_reader_fails(tmp_path, capsys):
    from vsrgconv.cli import main

    db, f = setup_db(tmp_path)
    pic.fetch_pictures(db, f, "chunithm", tmp_path / "pictures")
    args = ["--db", str(db.root), "ab", "chunithm", "--pictures", str(tmp_path / "pictures"),
            "--images", "--out", str(tmp_path / "ab"), "--report", str(tmp_path / "r.json")]
    assert main(args) == 0
    r = json.loads((tmp_path / "r.json").read_text("utf-8"))["chunithm"]
    # the picture is a flat colour, so the reader finds no columns: an error, but the comparison picture is drawn
    assert r["with_notes"] == 1 and "error" in r["results"][0] and r["errors"] == 1
    assert list((tmp_path / "ab").rglob("master.png"))
