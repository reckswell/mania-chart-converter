"""Adding games beyond the built-in ones: definitions, folder sources, adapter modules and the `games` commands."""
import json
import shutil
from pathlib import Path

import pytest

from vsrgconv.cli import main
from vsrgconv.db import ChartDB
from vsrgconv.games.registry import GAMES, Game, candidates
from vsrgconv.sources import SOURCES, load_user_games
from vsrgconv.sources.local import FolderSource

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_registry():
    """Tests add games; take them out again so other tests see only the built-in ones."""
    games, sources = dict(GAMES), dict(SOURCES)
    yield
    GAMES.clear()
    GAMES.update(games)
    SOURCES.clear()
    SOURCES.update(sources)


def run(capsys, *args) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(args))
    return code, capsys.readouterr().out


def test_definitions_are_checked():
    g = Game.from_dict({"key": "mygame", "name": "My Game", "lanes": 6,
                        "difficulties": {"Normal": "normal", "Double Hard": "double-hard"}})
    assert g.difficulties == {"normal": "normal", "double hard": "double-hard"} and g.lanes == 6
    with pytest.raises(ValueError, match="missing name"):
        Game.from_dict({"key": "x"})
    with pytest.raises(ValueError, match="lower-case"):
        Game.from_dict({"key": "My Game", "name": "x"})
    with pytest.raises(ValueError, match="not on the ladder"):
        Game.from_dict({"key": "x", "name": "x", "difficulties": {"hard": "insane"}})


def test_candidates_are_valid_definitions_and_exclude_added_games():
    known = candidates()
    assert "maimai" in known and "chunithm" not in known
    for d in known.values():
        Game.from_dict(d, origin="candidate")
    assert known["itg"]["readable"] and known["itg"]["chart_format"] == "stepmania"


def test_add_a_stepmania_game_from_a_folder(tmp_path, capsys):
    """ITG is a known VSRG whose charts the StepMania reader reads: one command adds it, no code."""
    songs = tmp_path / "Songs" / "Fixture Pack" / "SSC Fixture"
    songs.mkdir(parents=True)
    shutil.copy(FIX / "ddr_sample.ssc", songs / "song.ssc")
    (songs / "jacket.png").write_bytes((FIX / "chunithm_picture_2525_master_c10.png").read_bytes())
    games, db = tmp_path / "games", tmp_path / "db"
    base = ["--db", str(db), "--games", str(games), "--offline", "--cache", str(tmp_path / "cache")]

    code, out = run(capsys, *base, "add-game", "itg", "--folder", str(tmp_path / "Songs"), "--format", "stepmania")
    assert code == 0 and json.loads(out)["written"] == [str(games / "itg.json")]
    d = json.loads((games / "itg.json").read_text("utf-8"))
    assert d["name"] == "In The Groove / ITGmania" and d["source"]["format"] == "stepmania"
    assert d["difficulties"]["challenge"] == "master"            # from the known-VSRG list

    assert run(capsys, *base, "catalog", "itg")[0] == 0
    rec = next(ChartDB(db).iter_songs("itg"))
    assert (rec.title, rec.artist, rec.genre_slug) == ("SSC Fixture", "vsrgconv", "fixture-pack")
    assert {s: c.native for s, c in rec.charts.items()} == {"hard": "MEDIUM", "master": "CHALLENGE",
                                                            "expert": "HARD"}
    code, out = run(capsys, *base, "scrape", "itg", "--charts", "--jackets")
    assert code == 0 and json.loads(out)["charts_saved"] == 3 and json.loads(out)["jackets_saved"] == 1
    chart = ChartDB(db).load_chart("itg", rec.song_id, "master")
    assert chart.notes and chart.meta.difficulty_native == "CHALLENGE" and chart.meta.level == "14"

    code, out = run(capsys, *base, "games", "--json")
    row = next(r for r in json.loads(out) if r["key"] == "itg")
    assert row["added_by"] == "definition file" and row["source"] == "catalog,charts,jackets"
    assert row["songs"] == 1 and row["charts_with_notes"] == 3
    code, out = run(capsys, *base, "games", "--all", "--json")
    assert "itg" not in [r["key"] for r in json.loads(out) if r["status"] == "not added"]
    assert run(capsys, *base, "add-game", "itg")[0] == 2                         # exists: needs --force


def test_one_file_per_difficulty_formats_read_song_json(tmp_path):
    song = tmp_path / "charts" / "song-a"
    song.mkdir(parents=True)
    shutil.copy(FIX / "sample.c2s", song / "master.c2s")
    shutil.copy(FIX / "sample.c2s", song / "lunatic.c2s")
    (song / "song.json").write_text(json.dumps({"title": "Song A", "artist": "Someone", "genre": "ORIGINAL",
                                                "levels": {"master": "13+"}}), "utf-8")
    GAMES["myc2s"] = Game.from_dict({"key": "myc2s", "name": "My C2S Game", "lanes": 16, "variable_width": True,
                                     "difficulties": {"lunatic": "extra"}}, origin="test")
    fs = FolderSource("myc2s", tmp_path / "charts", "c2s")
    songs, report = fs.catalog()
    assert report["problems"] == [] and len(songs) == 1
    rec = songs[0]
    assert (rec.song_id, rec.title, rec.genre) == ("song-a", "Song A", "ORIGINAL")
    assert {s: (c.native, c.level) for s, c in rec.charts.items()} == {"master": ("MASTER", "13+"),
                                                                       "extra": ("LUNATIC", "")}
    chart = fs.chart(None, rec, "extra")
    assert chart.lanes == 16 and chart.notes and chart.meta.difficulty == "extra"


def test_guitar_hero_chart_folders_use_the_guitar_hero_slots(tmp_path):
    song = tmp_path / "gh" / "Fixture Song"
    song.mkdir(parents=True)
    shutil.copy(FIX / "guitarhero_sample.chart", song / "notes.chart")
    GAMES["clonehero"] = Game.from_dict({"key": "clonehero", "name": "Clone Hero", "lanes": 5}, origin="test")
    fs = FolderSource("clonehero", tmp_path / "gh", "gh")
    rec = fs.catalog()[0][0]
    assert rec.title == "Fixture Song" and rec.artist == "vsrgconv"
    assert set(rec.charts) == {"expert", "expert-2", "bass-expert", "drums-expert", "drums-master"}
    assert fs.chart(None, rec, "drums-master").notes


def test_adapter_stub_runs_and_registers(tmp_path, capsys):
    games = tmp_path / "games"
    code, out = run(capsys, "--db", str(tmp_path / "db"), "--games", str(games), "add-game", "newgame",
                    "--name", "New Game", "--lanes", "7", "--difficulty", "Lunatic=master", "--adapter")
    assert code == 0 and (games / "newgame.py").exists()
    SOURCES.pop("newgame", None)
    assert "newgame" in load_user_games([games])
    assert SOURCES["newgame"].catalog(None) == ([], {})            # the stub's empty catalog
    assert GAMES["newgame"].difficulties == {"lunatic": "master"}
    code, out = run(capsys, "--db", str(tmp_path / "db"), "--games", str(games), "games", "newgame")
    assert json.loads(out)["source"]["catalog"] is True


def test_add_game_refuses_built_in_and_unknown_without_a_name(tmp_path, capsys):
    base = ["--db", str(tmp_path / "db"), "--games", str(tmp_path / "games")]
    assert run(capsys, *base, "add-game", "chunithm")[0] == 2
    assert run(capsys, *base, "add-game", "unheardof")[0] == 2
    assert run(capsys, *base, "add-game", "maimai", "--folder", str(tmp_path), "--format", "simai")[0] == 2
    assert not (tmp_path / "games").exists()
