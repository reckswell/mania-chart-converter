"""What every per-game source adapter provides. Register new adapters in `sources/__init__.py`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..db import SongRecord
from ..model import Chart
from .fetch import Fetcher


class SourceAdapter(ABC):
    #: registry key, e.g. "chunithm-sdvxin"
    key: str = ""
    #: games/registry.py key
    game: str = ""
    #: what the adapter can do: "catalog" (song list), "charts" (notes)
    provides: tuple[str, ...] = ()

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    @abstractmethod
    def list_songs(self) -> Iterable[SongRecord]:
        """Every song this source knows, with per-difficulty metadata."""

    def fetch_chart(self, song: SongRecord, slot: str) -> Chart:
        """One chart's notes in the intermediate format. Adapters that only list songs leave this unimplemented."""
        raise NotImplementedError(f"{self.key} does not provide charts")
