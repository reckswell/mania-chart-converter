"""Registry of per-game sources. Each game module exposes a `GameSource` here.

A `GameSource` answers three questions for the `catalog` and `scrape` commands:
- `catalog(fetcher)`: every song in the game with every difficulty (metadata only);
- `chart(fetcher, song, slot)`: one chart's notes as an intermediate-format `Chart` (None if no source has it);
- `jacket(fetcher, song)`: the song's jacket image as (bytes, extension, url), or None.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..db import SongRecord
from ..model import Chart
from .fetch import Fetcher

CatalogFn = Callable[[Fetcher], tuple[list[SongRecord], dict[str, Any]]]
ChartFn = Callable[[Fetcher, SongRecord, str], "Chart | None"]
JacketFn = Callable[[Fetcher, SongRecord], "tuple[bytes, str, str] | None"]


@dataclass
class GameSource:
    game: str
    catalog: CatalogFn
    chart: ChartFn | None = None
    jacket: JacketFn | None = None
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
