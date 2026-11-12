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


# ------------------------------------------------------------------------------------------------ sdvx.in layers

def webp(colour, size=(400, 300), box=None) -> bytes:
    """A layer: transparent, with `colour` over `box` (or everywhere)."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste(colour + (255,), box or (0, 0) + size)
    buf = BytesIO()
    im.save(buf, "WEBP", lossless=True)
    return buf.getvalue()


def setup_sdvxin(tmp_path):
    from vsrgconv.sources.chunithm import SDVXIN_JSON, layer_urls

    db = ChartDB(tmp_path / "db")
    db.upsert_song(SongRecord(game="chunithm", song_id="s1", title="Song", genre="ORIGINAL", charts={
        s: ChartRecord(s, s.upper(), "9", source={"sdvxin_legacy": f"01/01001{k}"} if k else {})
        for s, k in [("normal", ""), ("hard", ""), ("expert", "exp"), ("master", "mst")]}))
    db.upsert_song(SongRecord(game="chunithm", song_id="w1", title="WE Song", genre="WORLD'S END", charts={
        "special": ChartRecord("special", "WORLD'S END", "狂☆☆☆")}))
    f = Fetcher(cache_dir=tmp_path / "cache", offline=True)

    def put(url, data):
        p = f.cache_path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    put(SDVXIN_JSON + "songs01.json", json.dumps([{"id": "01001", "title": "Song", "levels": {
        "bsc": "_3", "adv": "7", "exp": "10", "mst": "_13", "ult": ""}}]).encode())
    put(SDVXIN_JSON + "songs_we.json", json.dumps([{"id": "100", "title": "WE Song", "levels": "狂3"},
                                                   {"id": "101", "title": "WE Song", "levels": "招5"}]).encode())
    for ref in [{"id": "01001", "key": "adv"}, {"id": "01001", "key": "exp"}, {"id": "100", "key": "end"}]:
        urls = layer_urls(ref)
        put(urls["bg"][0], webp((40, 40, 60)))
        put(urls["obj"][0], webp((250, 0, 0), box=(10, 10, 60, 20)))
        put(urls["bar"][0], webp((255, 255, 255), box=(0, 0, 5, 5)))
    return db, f


def test_sdvxin_refs_follow_the_lists_drawn_levels(tmp_path):
    db, f = setup_sdvxin(tmp_path)
    refs, stats = pic.sdvxin_chunithm_refs(db, f)
    # basic and master are not drawn ("_" levels); ADVANCED is, though otoge-db has no link for it;
    # the WORLD'S END chart is matched by title, then by its level's kanji
    assert refs == {"s1/hard": {"id": "01001", "key": "adv"}, "s1/expert": {"id": "01001", "key": "exp"},
                    "w1/special": {"id": "100", "key": "end"}}
    assert stats["not_drawn"] == 2 and stats["we_unmatched"] == []


def test_sdvxin_layers_are_kept_as_published_with_a_merged_picture(tmp_path):
    from vsrgconv.sources.chunithm import layer_urls

    db, f = setup_sdvxin(tmp_path)
    out = tmp_path / "pictures"
    s = pic.fetch_sdvxin(db, f, out, workers=2)
    assert (s["jobs"], s["downloaded"], s["failed"]) == (3, 3, 0)
    rel = db.song_dir(db.get_song("chunithm", "s1")).relative_to(db.root)
    folder = out / rel / "sdvxin"
    obj_url = layer_urls({"id": "01001", "key": "exp"})["obj"][0]
    assert (folder / "expert.obj.webp").read_bytes() == f.cache_path(obj_url).read_bytes()   # the published bytes
    m = json.loads((out / "chunithm" / "manifest-sdvxin.json").read_text("utf-8"))
    e = m["s1/expert"]
    assert e["viewer"].endswith("viewer.php?id=01001&diff=exp") and e["size"] == [400, 300]
    assert sorted(e["layers"]) == ["bar", "bg", "obj"] and e["layers"]["obj"]["url"] == obj_url
    with Image.open(out / "chunithm" / e["file"]) as merged:     # the viewer's stack: notes over lanes, numbers on top
        px = merged.convert("RGB")
        assert px.getpixel((30, 15))[0] > 200 and px.getpixel((2, 2))[1] > 200 and px.getpixel((200, 200))[2] > 40
    assert m["w1/special"]["viewer"].endswith("id=100&diff=we")
    again = pic.fetch_sdvxin(db, f, out)
    assert again["already"] == 3 and again["downloaded"] == 0


def test_ab_reads_sdvxin_layers_not_the_merged_picture(tmp_path):
    db, f = setup_sdvxin(tmp_path)
    out = tmp_path / "pictures"
    pic.fetch_sdvxin(db, f, out)
    jobs, counts = pic.ab_jobs(db, "chunithm", out, source="sdvxin")
    assert counts["pictures"] == 3 and counts["with_notes"] == 0
    assert pic.load_manifest(out, "chunithm") == {}          # the database's own picture manifest is separate
    arr = pic.sdvxin_reader_array({k: out / "chunithm" / v["file"] for k, v in
                                   pic.load_manifest(out, "chunithm", "sdvxin")["s1/expert"]["layers"].items()})
    assert arr.shape == (300, 400, 4) and arr[15, 30, 3] == 255


def test_sdvxin_sekai_charts_are_matched_by_title_then_artist(tmp_path):
    db = ChartDB(tmp_path / "db")
    for sid, title in [("207", "Peaky Peaky"), ("709", "初音ミクの消失−劇場版−"), ("826", "スロウライト")]:
        db.upsert_song(SongRecord(game="sekai", song_id=sid, title=title, artist="A" if sid != "826" else "B",
                                  genre="VIRTUAL SINGER", charts={s: ChartRecord(s, s.upper(), "30")
                                                                  for s in ("hard", "expert", "master", "extra")}))
    f = Fetcher(cache_dir=tmp_path / "cache", offline=True)
    p = f.cache_path("https://sdvx.in/prsk/_/json/songs0001.json")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps([
        {"id": "0164", "title": "Peaky", "artist": "A", "levels": {"hrd": "_25", "exp": "28", "mst": "31", "apd": ""}},
        {"id": "0647", "title": "初音ミクの消失-劇場版-", "artist": "A", "levels": {"exp": "29", "mst": "33",
                                                                                "apd": "36"}},
        {"id": "0379", "title": "スロウダウナー", "artist": "C", "levels": {"exp": "27", "mst": "30"}}]), "utf-8")
    refs, stats = pic.sdvxin_refs(db, f, "sekai")
    assert refs == {"207/expert": {"id": "0164", "key": "exp"}, "207/master": {"id": "0164", "key": "mst"},
                    "709/expert": {"id": "0647", "key": "exp"}, "709/master": {"id": "0647", "key": "mst"},
                    "709/extra": {"id": "0647", "key": "apd"}}
    assert stats["near_matches"] == ["207 Peaky Peaky = 0164 Peaky"] and stats["unmatched"] == ["826 スロウライト"]
    assert pic.sdvxin_layer_urls(refs["709/extra"], "sekai")["obj"][0] == "https://sdvx.in/prsk/obj/data0647apd.webp"
    assert pic.sdvxin_viewer_url("0647", "apd", "sekai") == "https://sdvx.in/prsk/_/viewer.php?id=0647&diff=apd"
