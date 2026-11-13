"""`vsrgconv update`: find what a game's sources added since the last run and fetch only that (update.py)."""
import json
from datetime import date
from fractions import Fraction
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from vsrgconv.db import ChartDB, ChartRecord, SongRecord
from vsrgconv.model import BpmChange, Chart, ChartMeta, MeterChange, Note, NoteKind, TimingMap
from vsrgconv.sources import GameSource
from vsrgconv.sources.fetch import Fetcher, NotFound
from vsrgconv.update import (STATE_FILE, TrackingFetcher, UpdateOptions, UpdateState, compare, update,
                             update_game)

SITE = "https://example.invalid/"
LIST = SITE + "songs.json"


def chart_url(song_id: str, slot: str) -> str:
    return f"{SITE}charts/{song_id}_{slot}.json"


def png(colour=(200, 30, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), colour).save(buf, "PNG")
    return buf.getvalue()


def fake_source() -> GameSource:
    """A game whose catalog is one JSON list, with one JSON file of note beats per chart and a jacket per song."""
    def catalog(f: Fetcher):
        rows = json.loads(f.get(LIST))
        songs = [SongRecord(game="chunithm", song_id=r["id"], title=r["title"], genre="ORIGINAL",
                            charts={s: ChartRecord(slot=s, native=s.upper(), level=str(lv))
                                    for s, lv in r["charts"].items()},
                            extra={"removed": True} if r.get("removed") else {})
                 for r in rows]
        return songs, {"rows": len(rows)}

    def chart(f: Fetcher, song: SongRecord, slot: str):
        try:
            beats = json.loads(f.get(chart_url(song.song_id, slot)))
        except NotFound:
            return None
        meta = ChartMeta(game="chunithm", song_id=song.song_id, title=song.title, difficulty=slot,
                         difficulty_native=slot.upper(), source={"extracted_from": "test"})
        return Chart(meta=meta, lanes=16, timing=TimingMap(bpms=[BpmChange(Fraction(0), 150)],
                                                            meters=[MeterChange(Fraction(0))]),
                     notes=[Note(Fraction(b), 0, 4, NoteKind.TAP) for b in beats])

    def jacket(f: Fetcher, song: SongRecord):
        url = f"{SITE}jackets/{song.song_id}.png"
        return f.get(url), "png", url

    return GameSource("chunithm", catalog=catalog, chart=chart, jacket=jacket)


def put(f: Fetcher, url: str, data: bytes | str) -> None:
    p = f.cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode() if isinstance(data, str) else data)


def setup(tmp_path):
    db = ChartDB(tmp_path / "data" / "db")
    f = Fetcher(cache_dir=tmp_path / "cache", offline=True)      # offline: the cache plays the web
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13}}]))
    put(f, chart_url("1", "expert"), "[0, 1, 2]")
    put(f, chart_url("1", "master"), "[0, 0.5, 1]")
    put(f, f"{SITE}jackets/1.png", png())
    return db, f


def run(db, f, src=None, **kw):
    opts = UpdateOptions(renders_dir=db.root.parent / "renders", pictures_dir=db.root.parent / "pictures",
                         **{"today": date(2026, 11, 13), **kw})
    return update(db, ["chunithm"], {"chunithm": src or fake_source()}, f, opts)


def test_first_run_builds_the_catalog_fetches_everything_and_records_state(tmp_path):
    db, f = setup(tmp_path)
    out = run(db, f)
    r = out["games"]["chunithm"]
    assert r["status"] == "updated" and r["first_run"]
    assert (r["new_songs"], r["new_charts"], r["charts_saved"], r["pending"]) == (1, 2, 2, 0)
    assert db.has_chart("chunithm", "1", "master") and "jacket" in db.get_song("chunithm", "1").assets
    assert db.get_song("chunithm", "1").extra["first_seen"] == "2026-11-13"
    state = json.loads((db.root / "chunithm" / STATE_FILE).read_text("utf-8"))
    assert list(state["feeds"]) == [LIST] and state["pending"] == {} and state["catalog_changed"] == "2026-11-13"
    log = (db.root / "CHANGELOG.md").read_text("utf-8")
    assert "## 2026-11-13" in log and "1 new songs" in log and "notes for 2 charts" in log and "Old Song" in log


def test_nothing_changed_means_no_catalog_rebuild_and_no_file_written(tmp_path):
    db, f = setup(tmp_path)
    run(db, f)
    before = {p: p.stat().st_mtime_ns for p in db.root.rglob("*") if p.is_file()}

    def catalog_must_not_run(f):
        raise AssertionError("catalog rebuilt although its files did not change")

    src = fake_source()
    src.catalog = catalog_must_not_run
    out = run(db, f, src, today=date(2026, 11, 14))
    r = out["games"]["chunithm"]
    assert r["status"] == "up to date" and r["feeds"] == {"checked": 1, "changed": 0, "changed_sample": []}
    assert out["files_written"] == 0
    assert before == {p: p.stat().st_mtime_ns for p in db.root.rglob("*") if p.is_file()}     # git stays clean


def test_new_song_and_new_chart_are_fetched_and_a_missing_chart_stays_pending(tmp_path):
    db, f = setup(tmp_path)
    run(db, f)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13, "extra": 14}},
                             {"id": "2", "title": "New Song", "charts": {"master": 12}}]))
    put(f, chart_url("2", "master"), "[0, 1]")
    put(f, f"{SITE}jackets/2.png", png((0, 0, 200)))
    out = run(db, f, today=date(2026, 11, 20))       # no file for 1/extra yet
    r = out["games"]["chunithm"]
    assert (r["new_songs"], r["new_charts"], r["charts_saved"], r["pending"]) == (1, 2, 1, 1)
    assert r["new_song_titles"] == ["2 New Song"] and r["new_chart_keys"] == ["1/extra"]
    assert r["fetch"]["charts_skipped"] == 0           # only the new charts were looked at, not the stored ones
    state = UpdateState.load(db, "chunithm")
    assert state.pending == {"1/extra": "2026-11-20"}
    log = (db.root / "CHANGELOG.md").read_text("utf-8")
    assert log.index("## 2026-11-20") < log.index("## 2026-11-13")          # newest first
    assert "1 new charts in songs already listed" in log

    # the chart file appears a few days later: the catalog has not changed, the pending chart is found anyway
    put(f, chart_url("1", "extra"), "[0, 2]")
    r = run(db, f, today=date(2026, 11, 23))["games"]["chunithm"]
    assert r["status"] == "updated" and (r["charts_saved"], r["pending"], r["found_pending"]) == (1, 0, 1)
    assert db.has_chart("chunithm", "1", "extra") and UpdateState.load(db, "chunithm").pending == {}


def test_a_pending_chart_is_given_up_after_the_retry_window(tmp_path):
    db, f = setup(tmp_path)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13, "extra": 14}}]))
    run(db, f)
    assert UpdateState.load(db, "chunithm").pending == {"1/extra": "2026-11-13"}
    r = run(db, f, today=date(2026, 12, 13))["games"]["chunithm"]       # day 30: still looked for
    assert (r["pending"], r["gave_up"], r["fetch"]["charts_missing"]) == (1, 0, 1)
    r = run(db, f, today=date(2026, 12, 14))["games"]["chunithm"]       # day 31: dropped
    assert (r["pending"], r["gave_up"]) == (0, 1) and "fetch" not in r
    assert UpdateState.load(db, "chunithm").pending == {}


def test_charts_no_source_knows_are_not_kept_pending(tmp_path):
    """A chart whose source returns None without asking for any file (no reference to it) is not retried."""
    db, f = setup(tmp_path)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13, "extra": 14}}]))
    src = fake_source()
    inner = src.chart
    src.chart = lambda f, song, slot: None if slot == "extra" else inner(f, song, slot)
    r = run(db, f, src)["games"]["chunithm"]
    assert (r["charts_saved"], r["pending"]) == (2, 0)


def test_first_run_looks_again_for_charts_that_had_no_notes(tmp_path):
    """Charts listed before the updater existed are looked for once on its first run, and kept pending if a source
    was asked for them."""
    db, f = setup(tmp_path)
    db.upsert_songs([SongRecord(game="chunithm", song_id="1", title="Old Song", genre="ORIGINAL",
                                charts={"expert": ChartRecord("expert", "EXPERT", "10"),
                                        "master": ChartRecord("master", "MASTER", "13"),
                                        "extra": ChartRecord("extra", "EXTRA", "14")})])
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13, "extra": 14}}]))
    r = run(db, f)["games"]["chunithm"]
    assert (r["new_songs"], r["new_charts"], r["charts_saved"], r["pending"]) == (0, 0, 2, 1)


def test_check_only_writes_nothing(tmp_path):
    db, f = setup(tmp_path)
    out = run(db, f, check_only=True)
    assert out["games"]["chunithm"]["status"] == "would rebuild the catalog" and out["files_written"] == 0
    assert not db.root.exists()
    run(db, f)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10}}]))
    out = run(db, f, check_only=True)
    assert out["games"]["chunithm"]["status"] == "would rebuild the catalog"
    assert out["games"]["chunithm"]["feeds"]["changed"] == 1


def test_a_song_listed_twice_by_the_catalog_counts_once():
    one = SongRecord(game="chunithm", song_id="9", title="A", charts={"master": ChartRecord("master", "MASTER", "13")})
    two = SongRecord(game="chunithm", song_id="9", title="A", charts={"special": ChartRecord("special", "WE", "5")})
    d = compare({}, [one, two])
    assert len(d.new_songs) == 1 and set(d.new_songs[0].charts) == {"master", "special"}
    assert set(one.charts) == {"master"}            # the catalog's records are not changed


def test_removed_and_changed_songs_are_reported():
    old = SongRecord(game="chunithm", song_id="1", title="A", charts={"master": ChartRecord("master", "MASTER", "13")})
    same = SongRecord(game="chunithm", song_id="1", title="A", charts={"master": ChartRecord("master", "MASTER", "13")})
    assert not compare({"1": old}, [same]).any
    gone = SongRecord(game="chunithm", song_id="1", title="A", extra={"removed": True},
                      charts={"master": ChartRecord("master", "MASTER", "13+")})
    d = compare({"1": old, "2": old}, [gone])
    assert (d.removed, d.changed, d.new_charts, d.not_in_catalog) == (["1"], ["1"], [], 1)


def test_catalog_files_are_downloaded_again_once_per_run(tmp_path, monkeypatch):
    base = Fetcher(cache_dir=tmp_path)
    put(base, LIST, "[1]")                      # a stale copy in the cache
    asked = []
    cached = Fetcher.get

    def web(self, url, refresh=False):          # the network has a newer file
        asked.append(refresh)
        if url.endswith("gone.json"):
            raise NotFound(url)
        if refresh:
            put(self, url, "[1, 2]")
        return cached(self, url, refresh=False)

    monkeypatch.setattr(Fetcher, "get", web)
    t = TrackingFetcher(base)
    assert t.get(LIST) == b"[1, 2]" and t.get(LIST) == b"[1, 2]"
    assert asked == [True, False]               # downloaded again the first time only
    assert t.seen[LIST] == t.used[LIST] and len(t.used[LIST]) == 64
    with pytest.raises(NotFound):
        t.get(SITE + "gone.json")
    assert t.used[SITE + "gone.json"] is None


def test_only_the_files_a_run_changed_are_published(tmp_path):
    db, f = setup(tmp_path)
    run(db, f)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"expert": 10, "master": 13}},
                             {"id": "2", "title": "New Song", "charts": {"master": 12}}]))
    put(f, chart_url("2", "master"), "[0, 1]")
    put(f, f"{SITE}jackets/2.png", png((0, 0, 200)))

    class Api:
        def __init__(self):
            self.asked, self.uploaded = [], []

        def create_repo(self, *a, **kw):
            pass

        def repo_info(self, *a, **kw):
            return type("Info", (), {"sha": "a"})()

        def get_paths_info(self, repo_id, paths, **kw):
            self.asked += paths
            return []

        def list_repo_tree(self, *a, **kw):
            raise AssertionError("the whole dataset is listed")

        def create_commit(self, repo_id, operations, **kw):
            self.uploaded += [op.path_in_repo for op in operations]
            return type("Info", (), {"oid": "b"})()

    api = Api()
    import vsrgconv.publish as publish

    orig = publish.publish_hf

    def publish_with_fake(*a, **kw):
        return orig(*a, api=api, **kw)

    publish.publish_hf = publish_with_fake
    try:
        opts = UpdateOptions(renders_dir=db.root.parent / "renders", pictures_dir=db.root.parent / "pictures",
                             today=date(2026, 11, 14), render=True)
        out = update(db, ["chunithm"], {"chunithm": fake_source()}, f, opts, publish="me/charts")
    finally:
        publish.publish_hf = orig
    song2 = db.song_dir(db.get_song("chunithm", "2")).relative_to(db.root.parent).as_posix()
    expected = {"db/CHANGELOG.md", "db/chunithm/index.json", "db/chunithm/update.json", f"{song2}/song.json",
                f"{song2}/master.chart.json.gz", f"{song2}/assets/jacket.jpg",
                "renders/" + song2.split("/", 1)[1] + "/master.png"}
    assert set(api.uploaded) == expected and set(api.asked) == expected
    assert out["games"]["chunithm"]["render"] == {"rendered": 1, "failed": 0, "errors": []}
    assert (db.root.parent / "renders" / song2.split("/", 1)[1] / "master.png").exists()
    assert not (db.root.parent / "renders" / db.song_dir(db.get_song("chunithm", "1")).relative_to(db.root)).exists()


def test_new_charts_get_their_source_pictures_from_a_manifest_only_machine(tmp_path):
    """A machine with the manifest but not the pictures (a CI runner) downloads only the new charts' pictures."""
    db, f = setup(tmp_path)
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"master": 13}}]))
    src = fake_source()
    cat = src.catalog

    def with_pictures(f):
        songs, rep = cat(f)
        for s in songs:
            for slot, cr in s.charts.items():
                cr.source["picture"] = f"{SITE}pictures/{s.song_id}{slot}.png"
        return songs, rep

    src.catalog = with_pictures
    put(f, f"{SITE}pictures/1master.png", png())
    run(db, f, src)
    pictures = db.root.parent / "pictures"
    (pictures / "chunithm").mkdir(parents=True)
    old_entry = {"url": f"{SITE}pictures/1master.png", "key": "picture", "file": "x/1/master.png", "bytes": 1}
    (pictures / "chunithm" / "manifest.json").write_text(json.dumps({"1/master": old_entry}))
    put(f, LIST, json.dumps([{"id": "1", "title": "Old Song", "charts": {"master": 13}},
                             {"id": "2", "title": "New Song", "charts": {"master": 12}}]))
    put(f, f"{SITE}pictures/2master.png", png((0, 200, 0)))
    r = run(db, f, src, today=date(2026, 11, 14), pictures=True)["games"]["chunithm"]
    assert r["pictures"]["db"]["added"] == 1
    manifest = json.loads((pictures / "chunithm" / "manifest.json").read_text())
    assert manifest["1/master"] == old_entry and manifest["2/master"]["url"].endswith("2master.png")
    assert not (pictures / "chunithm" / "x").exists()       # the old picture was not downloaded again


def test_pictures_without_a_manifest_here_or_a_dataset_are_skipped(tmp_path):
    db, f = setup(tmp_path)
    r = run(db, f, pictures=True)["games"]["chunithm"]
    assert "skipped" in r["pictures"]["db"]
    assert not (db.root.parent / "pictures" / "chunithm" / "manifest.json").exists()


def test_scrape_records_which_charts_a_source_was_asked_for(tmp_path):
    from vsrgconv.scrape import scrape

    db, f = setup(tmp_path)
    songs, _ = fake_source().catalog(f)
    db.upsert_songs(songs)
    put(f, LIST, "[]")
    src = fake_source()
    inner = src.chart
    src.chart = lambda f, song, slot: None if slot == "expert" else inner(f, song, slot)
    res = scrape(db, "chunithm", src, f, list(db.iter_songs("chunithm")), workers=2)
    assert res.asked == {"1/master", "1/jacket"} and res.saved_charts == ["1/master"]
    assert res.saved_jackets == ["1"]


def test_cli_update(tmp_path, capsys, monkeypatch):
    from vsrgconv import sources
    from vsrgconv.cli import main

    db, f = setup(tmp_path)
    monkeypatch.setattr(sources, "load_all", lambda: {"chunithm": fake_source()})
    args = ["--db", str(db.root), "--cache", str(tmp_path / "cache"), "--offline", "update", "chunithm"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["games"]["chunithm"]["charts_saved"] == 2
    assert main(args + ["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["games"]["chunithm"]["status"] == "up to date"
    assert main(["--db", str(db.root), "update", "nosuchgame"]) == 2


def test_sdvxindex_site_list_is_read_from_the_bundle(tmp_path):
    from vsrgconv.sources.sdvx import SDVXINDEX_SITE, site_list_url

    f = Fetcher(cache_dir=tmp_path, offline=True)
    put(f, SDVXINDEX_SITE, '<html><script defer="defer" src="/static/js/main.400238aa.js"></script></html>')
    put(f, SDVXINDEX_SITE + "static/js/main.400238aa.js",
        "a=JSON.parse('{\"i\":\"songsv1.4.9.json\",\"G\":\"xa\"}');b=\"songsv1.4.10.json\";c='songsv1.3.json'")
    assert site_list_url(f) == "https://sdvxindex.com/songsv1.4.10.json"      # the newest version named
    put(f, SDVXINDEX_SITE, "<html>no bundle</html>")
    assert site_list_url(f) is None


def test_chunithm_charts_without_c2s_are_not_read_from_sdvxin_pictures_yet(tmp_path):
    from vsrgconv.sources import chunithm

    song = SongRecord(game="chunithm", song_id="sdvx-03182", title="T",
                      charts={"master": ChartRecord("master", "MASTER", "12",
                                                    source={"sdvxin": {"id": "03182", "key": "mst"}})})
    f = Fetcher(cache_dir=tmp_path, offline=True)
    assert chunithm.SDVXIN_READER_READY is False
    assert chunithm._chart(f, song, "master") is None and f.calls_in_thread() == 0


def test_song_folders_keep_their_names_and_pictures_follow_a_genre_move(tmp_path):
    db, f = setup(tmp_path)
    run(db, f)
    folder = db.song_dir(db.get_song("chunithm", "1"))
    assert folder.name == "1-old-song"
    renders = db.root.parent / "renders" / "chunithm" / "original" / "1-old-song"
    renders.mkdir(parents=True)
    (renders / "master.png").write_bytes(b"png")
    pictures = db.root.parent / "pictures" / "chunithm"
    (pictures / "original" / "1-old-song").mkdir(parents=True)
    (pictures / "manifest.json").write_text(json.dumps({"1/master": {"file": "original/1-old-song/master.png"}}))

    src = fake_source()
    cat = src.catalog

    def respelled(f, genre=None):
        songs, rep = cat(f)
        for s in songs:
            s.title_romaji = "orudo songu"          # a newer list spells the title its own way
            if genre:
                s.genre = genre
        return songs, rep

    src.catalog = respelled
    run(db, f, src, force=True, today=date(2026, 11, 14))
    assert db.song_dir(db.get_song("chunithm", "1")) == folder and folder.exists()        # not renamed

    src.catalog = lambda f: respelled(f, "VARIETY")
    r = update_game(db, "chunithm", src, f, UpdateOptions(renders_dir=db.root.parent / "renders",
                                                          pictures_dir=pictures.parent, force=True,
                                                          today=date(2026, 11, 15)))
    moved = db.song_dir(db.get_song("chunithm", "1"))
    assert moved == db.root / "chunithm" / "variety" / "1-old-song" and (moved / "song.json").exists()
    assert r["moved_songs"] == ["chunithm/original/1-old-song -> chunithm/variety/1-old-song"]
    assert r["_gone"] == ["db/chunithm/original/1-old-song/", "renders/chunithm/original/1-old-song/",
                          "pictures/chunithm/original/1-old-song/"]       # removed from the dataset after upload
    assert (db.root.parent / "renders" / "chunithm" / "variety" / "1-old-song" / "master.png").exists()
    manifest = json.loads((pictures / "manifest.json").read_text())
    assert manifest["1/master"]["file"] == "variety/1-old-song/master.png"


def test_old_folders_of_moved_songs_are_removed_from_the_dataset():
    from vsrgconv.publish import delete_folders

    class Api:
        deleted: list = []

        def list_repo_tree(self, repo_id, path_in_repo, **kw):
            if path_in_repo == "db/sdvx/unknown/2264-x":
                return [type("F", (), {"path": f"{path_in_repo}/{n}", "size": 1})() for n in ("song.json", "a.jpg")]
            raise FileNotFoundError(path_in_repo)

        def repo_info(self, *a, **kw):
            return type("Info", (), {"sha": "a"})()

        def delete_files(self, repo_id, paths, **kw):
            self.deleted += paths

    api = Api()
    n = delete_folders("me/charts", ["db/sdvx/unknown/2264-x/", "renders/sdvx/unknown/2264-x/"], api=api,
                       log=lambda m: None)
    assert n == 2 and api.deleted == ["db/sdvx/unknown/2264-x/song.json", "db/sdvx/unknown/2264-x/a.jpg"]


def test_pictures_only_on_the_dataset_are_moved_there():
    from vsrgconv.publish import move_folders

    class Api:
        commits: list = []

        def list_repo_tree(self, repo_id, path_in_repo, **kw):
            if path_in_repo == "pictures/sdvx/other/2224-x":
                return [type("F", (), {"path": f"{path_in_repo}/master.png", "size": 1})()]
            raise FileNotFoundError(path_in_repo)

        def repo_info(self, *a, **kw):
            return type("Info", (), {"sha": "a"})()

        def create_commit(self, repo_id, operations, **kw):
            self.commits.append([(op.src_path_in_repo, op.path_in_repo) for op in operations])
            return type("Info", (), {"oid": "b"})()

    api = Api()
    out = move_folders("me/charts", [("pictures/sdvx/other/2224-x/", "pictures/sdvx/pops-anime/2224-x/"),
                                     ("renders/sdvx/other/2224-x/", "renders/sdvx/pops-anime/2224-x/")],
                       api=api, log=lambda m: None)
    assert out == {"files": 1, "folders": ["pictures/sdvx/other/2224-x/"]}
    assert api.commits == [[("pictures/sdvx/other/2224-x/master.png", "pictures/sdvx/pops-anime/2224-x/master.png")]]
