"""SOUND VOLTEX, beatmania IIDX and GITADORA catalogs (sources/sdvx.py, iidx.py, gitadora.py) from small real fixtures."""
import json
from io import BytesIO
from pathlib import Path

from vsrgconv.db import ChartDB, ChartRecord, SongRecord
from vsrgconv.games.registry import canonical_difficulty, genre_slug, native_name
from vsrgconv.sources import gitadora as gd
from vsrgconv.sources import iidx, sdvx
from vsrgconv.sources.fetch import Fetcher

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text("utf-8"))


def put(fetcher, url, data):
    p = fetcher.cache_path(url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8"))


def png(colour):
    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (8, 8), colour).save(out, "PNG")
    return out.getvalue()


def test_sources_registered():
    from vsrgconv.sources import load_all

    sources = load_all()
    assert sources["sdvx"].jacket and sources["gitadora"].jacket and sources["iidx"].jacket is None
    assert all(sources[g].chart is None for g in ("sdvx", "iidx", "gitadora"))   # no chart source reachable


# ---------------------------------------------------------------- SOUND VOLTEX

def sdvx_catalog():
    t = load("sdvx_tachi_sample.json")
    return sdvx.build_catalog(load("sdvx_sdvxindex_sample.json"), t["songs"], t["charts"],
                              load("sdvx_rrrgr_sample.json"), load("sdvx_ddr_sample.json")["songs"])


def test_sdvx_slots_and_registry():
    assert [canonical_difficulty("sdvx", k) for k in ("nov", "adv", "exh", "mxm", "nbl", "ult")] == \
        ["normal", "hard", "expert", "master", "master", "extra"]
    assert native_name("sdvx", "nbl") == "NABLA" and native_name("sdvx", "ult") == "ULTIMATE"
    assert genre_slug("sdvx", "東方アレンジ") == "touhou-arrange" and genre_slug("sdvx", "") == "unknown"
    rec = SongRecord(game="sdvx", song_id="0001", title="x")
    assert sdvx.place_chart(rec, ChartRecord(slot="master", native="MAXIMUM")) == "master"
    assert sdvx.place_chart(rec, ChartRecord(slot="master", native="NABLA")) == "master-2"


def test_sdvx_catalog_from_fixtures():
    songs, rep = sdvx_catalog()
    by = {s.song_id: s for s in songs}
    assert sorted(by) == ["0001", "0020", "0067", "0636", "0664", "0840", "2264"]
    albida = by["0001"]
    m = albida.charts["master"]
    assert (m.native, m.level, m.designer) == ("INFINITE", "17.5", "MAD CHILD")
    assert m.note_counts == {"chip": 570, "hold": 387, "tsumami": 579}
    assert sum(m.note_counts.values()) == albida.extra["charts"]["master"]["max_chain"]   # counts are chain counts
    assert m.source["picture"] == sdvx.RRRGR + "img/0001_albida_muryoku/0001_albida_muryoku_4i_column_rendered.png"
    assert albida.genre == "BEMANI" and albida.version == "BOOTH" and albida.date_added == "2012-01-18"
    assert albida.source["jacket"][0].endswith("/jk_0001_1_b.webp") and "DDRCardDraw" in albida.source["jacket"][1]
    assert "jacket" not in albida.charts["normal"].source                       # NOVICE's is the song jacket
    assert albida.charts["hard"].source["jacket"] == {"url": sdvx.RRRGR + "img/0001_albida_muryoku/jk_0001_2_b.webp",
                                                      "base": albida.source["jacket"][0]}
    em = by["0636"]
    assert (em.charts["master"].native, em.charts["extra"].native, em.charts["extra"].level) == \
        ("GRAVITY", "ULTIMATE", "20.9")
    assert by["0067"].charts["master"].native == "NABLA" and by["0067"].charts["master"].level == "18"   # Tachi
    assert list(by["0840"].charts) == ["master"]                                # tutorial: one GRAVITY chart
    assert by["0020"].extra["removed"] and by["0020"].genre_slug == "unknown"   # Tachi only, not in ∇
    assert by["0664"].extra["removed"] and by["0664"].genre == "EXIT TUNES"     # RRRGR's older snapshot
    assert by["0664"].source["rrrgr"] and "picture" in by["0664"].charts["normal"].source
    assert not by["2264"].extra.get("removed") and by["2264"].version == "∇"
    assert rep["tachi_current_songs"] == 5 and rep["removed_songs"] == 2
    assert rep["slot_collisions_count"] == 0 and rep["level_mismatches_count"] == 0
    assert all(c.status == "listed" for s in songs for c in s.charts.values())


def test_sdvx_catalog_and_jackets_offline(tmp_path):
    f = Fetcher(tmp_path / "cache", offline=True)
    t = load("sdvx_tachi_sample.json")
    for url, data in [(sdvx.SDVXINDEX_URL, load("sdvx_sdvxindex_sample.json")), (sdvx.TACHI_SONGS_URL, t["songs"]),
                      (sdvx.TACHI_CHARTS_URL, t["charts"]), (sdvx.RRRGR_URL, load("sdvx_rrrgr_sample.json")),
                      (sdvx.DDR_URL, load("sdvx_ddr_sample.json"))]:
        put(f, url, data)
    songs, rep = sdvx._catalog(f)
    assert len(songs) == 7 and rep["charts_from_tachi_count"] == 1
    albida = next(s for s in songs if s.song_id == "0001")
    assert sdvx._jacket(f, albida) is None                     # nothing cached yet, no crash
    put(f, albida.source["jacket"][1], png("red"))             # only DDRCardDraw's: the fallback is used
    data, ext, url = sdvx._jacket(f, albida)
    assert ext == "png" and url == albida.source["jacket"][1]
    put(f, albida.source["jacket"][0], png("red"))
    assert sdvx._jacket(f, albida)[2] == albida.source["jacket"][0]   # RRRGR first when it has it
    put(f, albida.charts["hard"].source["jacket"]["url"], png("red"))      # same bytes as the song jacket
    put(f, albida.charts["expert"].source["jacket"]["url"], png("blue"))   # a real variant
    assert [v[0] for v in sdvx.difficulty_jackets(f, albida)] == ["expert"]

    db = ChartDB(tmp_path / "db")
    db.upsert_songs(songs)
    db.save_asset("sdvx", "0001", "jacket", png("red"), url=albida.source["jacket"][0])
    stats = sdvx.save_difficulty_jackets(db, f, workers=2, only={"0001"})
    assert stats["saved"] == 1 and stats["same_as_song"] == 1 and stats["missing"] == ["0001/master"]
    assert not stats["errors"]
    rec = db.get_song("sdvx", "0001")
    assert rec.charts["expert"].assets["jacket-expert"].file == "assets/jacket-expert.jpg"
    assert rec.assets["jacket"].file == "assets/jacket.jpg" and (db.song_dir(rec) / "assets/jacket.jpg").exists()
    again = sdvx.save_difficulty_jackets(db, f, only={"0001"})      # resumable: a stored variant is not re-fetched
    assert again["saved"] == 0 and again["same_as_song"] == 1 and again["missing_count"] == 1   # hard, master


# ---------------------------------------------------------------- beatmania IIDX

def iidx_catalog():
    t = load("iidx_tachi_sample.json")
    return iidx.build_catalog(load("iidx_textage_sample.json"), t["songs"], {"sp": t["sp"], "dp": t["dp"]})


def test_iidx_textage_urls_and_versions():
    names = load("iidx_textage_sample.json")["version"]
    assert [iidx.textage_folder(v) for v in (-1, 0, 1, 2, 34)] == ["0", "1", "s", "2", "34"]
    assert iidx.textage_url(34, "teknoksr", "sp", "a", 12) == "https://textage.cc/score/34/teknoksr.html?1AC00"
    assert iidx.textage_url(1, "deepindd", "dp", "l", 10) == "https://textage.cc/score/s/deepindd.html?DXA00"
    assert [iidx.version_folder(v, names, inf) for v, inf in ((0, False), (1, False), (10, False), (34, False),
                                                                (-1, True), (-1, False))] == \
        ["1st style", "substream", "10th style", "34 ZINRAI", "INFINITAS", "CS"]
    assert canonical_difficulty("iidx", "dpa") == "double-expert" and native_name("iidx", "dpa") == "DP ANOTHER"
    assert canonical_difficulty("iidx", "spb") == "easy" and native_name("iidx", "spl") == "SP LEGGENDARIA"


def test_iidx_catalog_from_fixtures():
    songs, rep = iidx_catalog()
    by = {s.song_id: s for s in songs}
    tk = by["34000"]                       # two textage tags: the one with the ANOTHER charts wins
    assert tk.source["textage"]["tag"] == "teknoksr" and tk.genre == "34 ZINRAI"
    assert tk.charts["expert"].source["textage"] == "https://textage.cc/score/34/teknoksr.html?1AC00"
    assert tk.charts["double-expert"].source["textage"].endswith("?DAC00")
    assert tk.charts["expert"].note_counts == {"notes": 2010} and tk.charts["expert"].native == "SP ANOTHER"
    assert [s.title for s in songs].count("月下散刄") == 1 and "月下散刃" not in [s.title for s in songs]
    first = by["01000"]                    # 5.1.1.: game id 1000, written with 5 digits
    assert first.genre == "1st style" and first.charts["easy"].source["textage"].endswith("/score/1/511_hs.html?1B100")
    assert by["01015"].genre == "substream" and "/score/s/" in by["01015"].charts["normal"].source["textage"]
    bang = by["80121"]                     # INFINITAS only: INFINITAS id, never in an arcade version
    assert bang.genre == "INFINITAS" and bang.extra["not_in_arcade"] and not bang.extra.get("removed")
    assert "/score/0/" in bang.charts["normal"].source["textage"]
    sana = by["04211"]                     # textage romaji title, paired by alias with Tachi's katakana
    assert sana.title == "SANA MOLLETTE NE ENTE" and sana.extra["removed"] and sana.extra["not_in_arcade"]
    assert by["06000"].extra["textage_variants"][0]["tag"] == "20nov1st" and by["06000"].genre == "1st style"
    assert by["18201"].title == "jewelry girl*" and by["18201"].genre == "18 Resort Anthem"   # Tachi only
    gold = by["tx-gold_all"]               # newer than Tachi: no game id known yet
    assert gold.charts["normal"].note_counts == {} and gold.charts["expert"].note_counts == {"notes": 1111}
    assert rep["in_arcade"] == 5 and rep["removed_from_arcade"] == 3 and rep["never_in_arcade"] == 1
    assert list(tk.charts)[:3] == ["normal", "hard", "expert"] and not any(c.assets for c in tk.charts.values())


def test_iidx_catalog_offline(tmp_path):
    f = Fetcher(tmp_path / "cache", offline=True)
    for k, v in load("iidx_textage_sample.json").items():
        put(f, iidx.TEXTAGE_DATA + k + ".json", v)
    t = load("iidx_tachi_sample.json")
    for k, name in iidx.TACHI_FILES.items():
        put(f, iidx.TACHI + name, t[k])
    songs, rep = iidx._catalog(f)
    assert rep["songs"] == len(songs) == 9 and rep["textage_only_count"] == 1


# ---------------------------------------------------------------- GITADORA

def gd_catalog():
    t = load("gitadora_tachi_sample.json")
    return gd.build_catalog((FIX / "gitadora_mdb_sample.bin").read_bytes(), load("gitadora_jackets_sample.json"),
                            t["songs"], {"gita": t["gita"], "dora": t["dora"]})


def test_gitadora_mdb_and_slots():
    rows = gd.parse_mdb((FIX / "gitadora_mdb_sample.bin").read_bytes())
    assert len(rows) == 8 and rows[0]["title"] == "I think about you" and rows[0]["first_ver"] == [1, 0]
    assert gd.version_label([30, 29]) == "GALAXY WAVE DELTA" and gd.version_label([1, 0]) == "GF2nd / DM1st"
    assert canonical_difficulty("gitadora", "drum-mas") == "drum-master"
    assert native_name("gitadora", "bass-ext") == "BASS EXTREME"
    removed = gd.mdb_record({**rows[0], "disable_area": [1, 1, 1, 1]})
    assert removed.extra["removed"] and len(removed.charts) == 6
    assert gd.thumbnail_url("40°C_Desert.png").endswith("/thumbnails/40%C2%B0C_Desert.webp")
    assert gd.thumbnail_url("LUCKY?_STAFF.png").endswith("/thumbnails/LUCKY%3F_STAFF.webp")   # not a query
    full = gd.remywiki_urls("https://remywiki.com/images/0/00/Screaming_Your_Faith%21%21%21.png\n")
    assert full == {"Screaming_Your_Faith!!!.png": "https://remywiki.com/images/0/00/Screaming_Your_Faith%21%21%21.png"}


def test_gitadora_catalog_from_fixtures():
    songs, rep = gd_catalog()
    by = {s.song_id: s for s in songs}
    assert sorted(by) == ["0000", "0626", "1530", "2210", "3032", "5039"] and rep["rows_without_charts"] == 2
    first = by["0000"]
    assert set(first.charts) == {f"{p}-{s}" for p in ("guitar", "drum") for s in ("normal", "hard", "expert")}
    g = first.charts["guitar-normal"]
    assert (g.native, g.level, g.source["dtx"]) == ("GUITAR BASIC", "2.20", "https://gddata-mdb.selundine.top/0000/g_bsc.dtx")
    assert first.charts["drum-expert"].source["dtx"].endswith("/0000/d_ext.dtx")
    assert first.genre == "GF2nd / DM1st" and first.artist_romaji == "ELIZABETH DOBBS"
    assert first.source["jacket"] == gd.JACKETS + "thumbnails/I_think_about_you.webp"
    assert by["0626"].extra["disabled_areas"] == [3] and not by["0626"].extra.get("removed")
    new = by["3032"]
    assert new.extra["levels_unpublished"] and new.genre == "GALAXY WAVE DELTA" and len(new.charts) == 12
    assert all(c.level == "" and c.notes for c in new.charts.values())
    hina = by["2210"]
    assert hina.artist == "日向美ビタースイーツ♪" and hina.charts["drum-master"].level == "8.65"
    assert hina.source["jacket"].endswith("/Rin_to_shite_saku_hana_no_gotoku_~HinaBitter_edition~.webp")
    classic = by["5039"]                 # a CLASSIC chart set shares the song's jacket, keeps its own artist
    assert classic.extra["classic"] and classic.source["jacket"] == by["1530"].source["jacket"]
    assert classic.artist == "BENIIROLITMUS"
    assert rep["tachi_level_mismatches_count"] > 0     # GITADORA revises levels; reported, mdb wins


def test_gitadora_catalog_and_jacket_offline(tmp_path):
    f = Fetcher(tmp_path / "cache", offline=True)
    put(f, gd.MANIFEST_URL, {"latest": 11, "files": {"11": "/mdb/M32_mdb_11.bin"}})
    put(f, gd.GD_DATA + "M32_mdb_11.bin", (FIX / "gitadora_mdb_sample.bin").read_bytes())
    put(f, gd.JACKET_META_URL, load("gitadora_jackets_sample.json"))
    t = load("gitadora_tachi_sample.json")
    put(f, gd.TACHI + "songs-gitadora.json", t["songs"])
    for k in ("gita", "dora"):
        put(f, gd.TACHI + f"charts-gitadora-{k}.json", t[k])
    songs, rep = gd._catalog(f)
    assert rep["mdb_version"] == 11 and len(songs) == 6
    song = songs[0]
    put(f, song.source["jacket"], b"RIFF....WEBP")
    assert gd._jacket(f, song) == (b"RIFF....WEBP", "webp", song.source["jacket"])
    assert gd._jacket(f, SongRecord(game="gitadora", song_id="9", title="x")) is None
