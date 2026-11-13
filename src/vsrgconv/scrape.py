"""Fetch charts (notes) and jackets for songs already in the catalog: the work behind `vsrgconv scrape` and the
fetching step of `vsrgconv update`.

Fetching and parsing run on `workers` threads; every database write happens on the calling thread, in order, so a
long run never writes two song.json files at once.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .db import IMAGE_MAX_SIZE, ChartDB, SongRecord
from .sources import GameSource
from .sources.fetch import Fetcher, FetchError, NotFound


@dataclass
class ScrapeResult:
    stats: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    saved_charts: list[str] = field(default_factory=list)       # "song_id/slot" of every chart stored this run
    saved_jackets: list[str] = field(default_factory=list)      # song ids with a jacket stored this run
    asked: set[str] = field(default_factory=set)                # "song_id/slot" or "song_id/jacket" for which some
                                                                # source was asked (missing ones may appear later)


def scrape(db: ChartDB, game: str, src: GameSource, fetcher: Fetcher, songs: list[SongRecord], *,
           charts: bool = True, jackets: bool = True, refresh: bool = False, workers: int = 1,
           image_size: int = IMAGE_MAX_SIZE, full_images: bool = False, progress: bool = False,
           slots: dict[str, set[str]] | None = None) -> ScrapeResult:
    """Fetch what is missing for `songs`: charts not extracted yet and jackets not stored yet (everything again with
    `refresh`). With `slots`, only those charts (and per-difficulty jackets) of each song listed there are fetched,
    and only songs listed there get their song jacket."""
    stats: dict[str, Any] = {"charts_saved": 0, "charts_skipped": 0, "charts_missing": 0, "charts_failed": 0,
                             "jackets_saved": 0, "jackets_skipped": 0, "jackets_missing": 0}
    res = ScrapeResult(stats)

    jobs: list[tuple[str, SongRecord, str]] = []
    for song in songs:
        wanted = None if slots is None else slots.get(song.song_id)
        if slots is not None and wanted is None:
            continue
        if jackets and src.jacket:
            if "jacket" in song.assets and not refresh:
                stats["jackets_skipped"] += 1
            else:
                jobs.append(("jacket", song, ""))
        if jackets and src.chart_jackets:
            todo = [k for k, c in song.charts.items()
                    if (wanted is None or k in wanted) and (refresh or f"jacket-{k}" not in c.assets)]
            if todo:
                jobs.append(("chart_jackets", song, ",".join(todo)))
        if charts and src.chart:
            for slot, cr in song.charts.items():
                if wanted is not None and slot not in wanted:
                    continue
                if cr.status in ("extracted", "converted") and not refresh:
                    stats["charts_skipped"] += 1
                else:
                    jobs.append(("chart", song, slot))

    def fetch(job: tuple[str, SongRecord, str]) -> Any:
        kind, song, slot = job
        try:
            if kind == "chart_jackets":
                return src.chart_jackets(fetcher, song, slot.split(","))
            return src.jacket(fetcher, song) if kind == "jacket" else src.chart(fetcher, song, slot)
        except (NotFound, FetchError) as e:
            return e
        except Exception as e:  # a reader bug must not stop a long scrape; it is reported
            return RuntimeError(f"{type(e).__name__}: {e}")

    def work(job: tuple[str, SongRecord, str]) -> tuple[Any, bool]:
        before = fetcher.calls_in_thread()
        result = fetch(job)
        return result, fetcher.calls_in_thread() > before

    def results():
        """Results in job order, a batch at a time, so finished charts do not pile up in memory while the
        database writes (on this thread) catch up."""
        batch = max(1, workers) * 16
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i in range(0, len(jobs), batch):
                chunk = jobs[i:i + batch]
                yield from zip(chunk, pool.map(work, chunk))

    max_size = None if full_images else image_size
    for n, (job, (result, asked)) in enumerate(results(), 1):
        kind, song, slot = job
        name = f"{song.song_id}/{slot or 'jacket'}"
        if asked and kind != "chart_jackets":
            res.asked.add(name)
        if kind == "chart_jackets" and isinstance(result, list):
            for cslot, data, ext, url in result:
                try:
                    db.save_asset(game, song.song_id, f"jacket-{cslot}", data, ext=ext, url=url, slot=cslot,
                                  max_size=max_size)
                    stats["chart_jackets_saved"] = stats.get("chart_jackets_saved", 0) + 1
                    res.saved_jackets.append(song.song_id)
                except (OSError, ValueError) as e:
                    res.errors.append(f"{song.song_id}/jacket-{cslot}: {e}"[:200])
            continue
        if kind == "chart_jackets":
            res.errors.append(f"{song.song_id}/difficulty jackets: {result}"[:200])
            continue
        if isinstance(result, NotFound) or result is None:
            stats[f"{kind}s_missing"] += 1
            res.missing.append(name)
        elif isinstance(result, Exception):
            stats["charts_failed" if kind == "chart" else "jackets_missing"] += 1
            res.errors.append(f"{name}: {result}"[:200])
        elif kind == "jacket":
            data, ext, url = result
            try:
                db.save_asset(game, song.song_id, "jacket", data, ext=ext, url=url, max_size=max_size)
                stats["jackets_saved"] += 1
                res.saved_jackets.append(song.song_id)
            except (OSError, ValueError) as e:
                stats["jackets_missing"] += 1
                res.errors.append(f"{name}: {e}"[:200])
        else:
            db.save_chart(result)
            stats["charts_saved"] += 1
            res.saved_charts.append(name)
        if progress and n % 250 == 0:
            print(f"{n}/{len(jobs)} {json.dumps(stats)}", file=sys.stderr, flush=True)
    return res
