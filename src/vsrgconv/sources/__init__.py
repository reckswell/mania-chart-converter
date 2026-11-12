"""Registry of per-game sources. Each game module exposes a `GameSource` here.

A `GameSource` answers three questions for the `catalog` and `scrape` commands:
- `catalog(fetcher)`: every song in the game with every difficulty (metadata only);
- `chart(fetcher, song, slot)`: one chart's notes as an intermediate-format `Chart` (None if no source has it);
- `jacket(fetcher, song)`: the song's jacket image as (bytes, extension, url), or None;
- `chart_jackets(fetcher, song, slots)`: per-difficulty jackets that differ from the song's (SDVX), as a list of
  (slot, bytes, extension, url); stored as chart assets named `jacket-<slot>`.

Games beyond the built-in ones come from `load_user_games` (docs/adding-a-game.md): definition files, adapter modules
beside them, and installed packages with a `vsrgconv.games` entry point.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..db import SongRecord
from ..model import Chart
from .fetch import Fetcher

CatalogFn = Callable[[Fetcher], tuple[list[SongRecord], dict[str, Any]]]
ChartFn = Callable[[Fetcher, SongRecord, str], "Chart | None"]
JacketFn = Callable[[Fetcher, SongRecord], "tuple[bytes, str, str] | None"]
ChartJacketsFn = Callable[[Fetcher, SongRecord, list[str]], "list[tuple[str, bytes, str, str]]"]


@dataclass
class GameSource:
    game: str
    catalog: CatalogFn
    chart: ChartFn | None = None
    jacket: JacketFn | None = None
    chart_jackets: ChartJacketsFn | None = None
    notes: str = ""


SOURCES: dict[str, GameSource] = {}


def register(source: GameSource) -> GameSource:
    SOURCES[source.game] = source
    return source


def load_all() -> dict[str, GameSource]:
    """Import every game module so it registers itself."""
    import importlib
    import pkgutil

    for mod in pkgutil.iter_modules(__path__):
        if mod.name not in ("base", "fetch"):
            importlib.import_module(f"{__name__}.{mod.name}")
    return SOURCES


def user_game_dirs(extra: Iterable[str | Path] = ()) -> list[Path]:
    """Folders searched for game definitions: the given ones, then each folder in $VSRGCONV_GAMES."""
    env = [x for x in os.environ.get("VSRGCONV_GAMES", "").split(os.pathsep) if x]
    return [Path(d) for d in [*extra, *env]]


def load_user_games(dirs: Iterable[str | Path] = ()) -> list[str]:
    """Register games added by users, and their sources. Returns the keys added.

    - `<key>.json` in each folder: a game definition (games/registry.py `Game.from_dict`). A `"source"` of type
      `"folder"` reads chart files from disk with no code (sources/local.py).
    - `<key>.py` beside it: an adapter module that calls `register(GameSource(...))`, like the built-in ones.
    - installed packages: each `vsrgconv.games` entry point is loaded, and called if it is a function.
    """
    import importlib.util
    from importlib.metadata import entry_points

    from ..games.registry import Game, register_game

    added: list[str] = []
    for folder in user_game_dirs(dirs):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            d = json.loads(path.read_text("utf-8"))
            game = register_game(Game.from_dict(d, origin=str(path)), replace=True)
            added.append(game.key)
            src = d.get("source") or {}
            if src.get("type") == "folder":
                from .local import FolderSource

                register(FolderSource(game.key, Path(src["folder"]), src.get("format", game.chart_format)).source())
            module = path.with_suffix(".py")
            if module.exists():
                spec = importlib.util.spec_from_file_location(f"vsrgconv_user_games.{game.key}", module)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
    for ep in entry_points(group="vsrgconv.games"):
        obj = ep.load()
        if callable(obj):
            obj()
        added.append(ep.name)
    return added
