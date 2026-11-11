"""Command line: `python -m vsrgconv <command>` (or `vsrgconv <command>` once installed). `--help` on each command."""
from __future__ import annotations

import argparse
import json
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from .db import IMAGE_MAX_SIZE, ChartDB, ChartRecord, SongRecord
from .sources.fetch import Fetcher, FetchError


def _fetcher(a: argparse.Namespace) -> Fetcher:
    return Fetcher(cache_dir=a.cache, offline=a.offline, min_interval=a.interval)


def cmd_catalog(a: argparse.Namespace) -> int:
    from .sources import load_all

    sources = load_all()
    games = sorted(sources) if a.game == "all" else [a.game]
    db = ChartDB(a.db)
    out = {}
    for g in games:
        if g not in sources:
            print(f"no source for {g} yet; known: {', '.join(sorted(sources))}", file=sys.stderr)
            return 2
        songs, report = sources[g].catalog(_fetcher(a))
        new = db.upsert_songs(songs)
        out[g] = {"songs": len(songs), "new": new,
                  "charts": sum(len(s.charts) for s in songs), **report}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_scrape(a: argparse.Namespace) -> int:
    """Fill the database for one game: charts (notes) and/or jackets, resumable (skips what is already there).

    Fetching and parsing run on `--workers` threads; every database write happens on this thread, in order."""
    from concurrent.futures import ThreadPoolExecutor

    from .sources import load_all
    from .sources.fetch import NotFound

    src = load_all()[a.game]
    db = ChartDB(a.db)
    f = _fetcher(a)
    stats: dict[str, Any] = {"charts_saved": 0, "charts_skipped": 0, "charts_missing": 0, "charts_failed": 0,
                             "jackets_saved": 0, "jackets_skipped": 0, "jackets_missing": 0}
    errors: list[str] = []
    missing: list[str] = []
    songs = list(db.iter_songs(a.game))
    if a.only:
        wanted = set(a.only.split(","))
        songs = [s for s in songs if s.song_id in wanted]
    songs = songs[: a.limit or None]

    jobs: list[tuple[str, SongRecord, str]] = []
    for song in songs:
        if a.jackets and src.jacket:
            if "jacket" in song.assets and not a.refresh:
                stats["jackets_skipped"] += 1
            else:
                jobs.append(("jacket", song, ""))
        if a.charts and src.chart:
            for slot, cr in song.charts.items():
                if cr.status in ("extracted", "converted") and not a.refresh:
                    stats["charts_skipped"] += 1
                else:
                    jobs.append(("chart", song, slot))

    def work(job: tuple[str, SongRecord, str]) -> Any:
        kind, song, slot = job
        try:
            return src.jacket(f, song) if kind == "jacket" else src.chart(f, song, slot)
        except (NotFound, FetchError) as e:
            return e
        except Exception as e:  # a reader bug must not stop a long scrape; it is reported
            return RuntimeError(f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        for n, (job, result) in enumerate(zip(jobs, pool.map(work, jobs)), 1):
            kind, song, slot = job
            name = f"{song.song_id}/{slot or 'jacket'}"
            if isinstance(result, NotFound) or result is None:
                stats[f"{kind}s_missing"] += 1
                missing.append(name)
            elif isinstance(result, Exception):
                stats["charts_failed" if kind == "chart" else "jackets_missing"] += 1
                errors.append(f"{name}: {result}"[:200])
            elif kind == "jacket":
                data, ext, url = result
                try:
                    db.save_asset(a.game, song.song_id, "jacket", data, ext=ext, url=url,
                                  max_size=None if a.full_images else a.image_size)
                    stats["jackets_saved"] += 1
                except (OSError, ValueError) as e:
                    stats["jackets_missing"] += 1
                    errors.append(f"{name}: {e}"[:200])
            else:
                db.save_chart(result)
                stats["charts_saved"] += 1
            if a.progress and n % 250 == 0:
                print(f"{n}/{len(jobs)} {json.dumps(stats)}", file=sys.stderr, flush=True)
    stats["error_count"] = len(errors)
    stats["errors"] = errors[:30]
    stats["missing_sample"] = missing[:60]
    if a.report:
        Path(a.report).write_text(json.dumps({**stats, "errors": errors, "missing": missing}, ensure_ascii=False,
                                             indent=1) + "\n", "utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


def cmd_extract(a: argparse.Namespace) -> int:
    from .sources import load_all

    db = ChartDB(a.db)
    song = db.get_song(a.game, a.song_id)
    if song is None:
        print(f"{a.game}/{a.song_id} not in database; run `catalog` first", file=sys.stderr)
        return 2
    src = load_all()[a.game]
    chart = src.chart(_fetcher(a), song, a.slot) if src.chart else None
    if chart is None:
        print(f"no chart source has {a.game}/{a.song_id}/{a.slot}", file=sys.stderr)
        return 1
    path = db.save_chart(chart)
    print(json.dumps({"saved": str(path), "notes": chart.count_by_kind(), "warnings": chart.warnings},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_convert(a: argparse.Namespace) -> int:
    from .convert.pipeline import convert

    db = ChartDB(a.db)
    chart = db.load_chart(a.game, a.song_id, a.slot)
    conv = convert(chart, keys=a.keys, profile=a.profile)
    out = conv.save(a.out)
    (out.with_suffix(".report.json")).write_text(json.dumps(conv.summary(), ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({"osu": str(out), "lint_ok": conv.lint.ok, "errors": conv.lint.errors[:10],
                      "warnings": conv.lint.warnings[:10]}, ensure_ascii=False, indent=1))
    return 0 if conv.lint.ok else 1


def cmd_calibrate(a: argparse.Namespace) -> int:
    from .imaging.sdvxin_layout import find_layout, find_measures, lane_strip, load_rgba
    from .imaging.sdvxin_notes import calibration_report

    bg, obj, bar = (load_rgba(Path(p).read_bytes()) for p in (a.bg, a.obj, a.bar))
    layout = find_layout(bg)
    rep = calibration_report(lane_strip(obj, layout))
    rep["columns"] = len(layout.columns)
    rep["measures"] = len(find_measures(bar, layout))
    print(json.dumps(rep, indent=1))
    return 0


def cmd_check_counts(a: argparse.Namespace) -> int:
    from .imaging.sdvxin_chart import count_mismatches

    db = ChartDB(a.db)
    rows = []
    for song in db.iter_songs(a.game):
        for slot, cr in song.charts.items():
            if cr.status in ("extracted", "converted"):
                chart = db.load_chart(a.game, song.song_id, slot)
                rows.append({"chart": f"{song.song_id}/{slot}", "title": song.title,
                             "mismatches": count_mismatches(chart)})
    bad = [r for r in rows if r["mismatches"]]
    print(json.dumps({"checked": len(rows), "mismatched": len(bad), "details": bad[:50]}, ensure_ascii=False, indent=1))
    return 0 if not bad else 1


def cmd_demo(a: argparse.Namespace) -> int:
    """Synthetic end to end: draw sdvx.in-style layers -> read -> database -> rules -> .osu (no network)."""
    from .convert.pipeline import convert
    from .imaging.sdvxin_chart import extract_chart
    from .imaging.testing import DrawnNote, draw_chart

    notes = [DrawnNote(Fraction(b, 2), (b * 3) % 14, 2) for b in range(0, 56)]
    notes += [DrawnNote(Fraction(32), 0, 16, "ex_tap"), DrawnNote(Fraction(4), 4, 4, "hold", Fraction(2)),
              DrawnNote(Fraction(12), 10, 4, "slide", Fraction(3, 2)), DrawnNote(Fraction(81, 4), 6, 4, "air")]
    layers, _ = draw_chart(notes, measures=9)
    song = SongRecord(game="chunithm", song_id="demo", title="Synthetic Demo", artist="vsrgconv", genre="ORIGINAL",
                      bpm="150", charts={"master": ChartRecord(slot="master", native="MASTER", level="1")})
    db = ChartDB(a.db)
    db.upsert_song(song)
    chart = extract_chart(song, "master", layers)
    db.save_chart(chart)
    conv = convert(chart, keys=a.keys)
    out = conv.save(a.out)
    print(json.dumps({"osu": str(out), **conv.summary()}, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_playtest(a: argparse.Namespace) -> int:
    from .playtest.orthomania import PlaytestRequest, run_playtest

    req = PlaytestRequest(beatmap=Path(a.osu), times_ms=[int(t) for t in a.times.split(",")] if a.times else [],
                          scroll_speed=a.scroll_speed)
    print(json.dumps(run_playtest(req, repo=a.ortho, dry_run=a.dry_run), indent=1))
    return 0


def coverage(db: ChartDB, games: list[str]) -> dict[str, dict[str, Any]]:
    """Per game: songs (current/removed), charts by status, jackets, and how many genres."""
    out: dict[str, dict[str, Any]] = {}
    for g in games:
        row: dict[str, Any] = {"songs": 0, "removed_songs": 0, "genres": set(), "charts": 0, "jackets": 0,
                               "by_status": {}, "by_slot": {}}
        for rec in db.iter_songs(g):
            row["songs"] += 1
            row["removed_songs"] += bool(rec.extra.get("removed"))
            row["genres"].add(rec.genre_slug)
            row["jackets"] += "jacket" in rec.assets
            for slot, cr in rec.charts.items():
                row["charts"] += 1
                row["by_status"][cr.status] = row["by_status"].get(cr.status, 0) + 1
                base = re.sub(r"-\d+$", "", slot)      # special-2 counts as special; double-expert stays
                row["by_slot"][base] = row["by_slot"].get(base, 0) + 1
        row["genres"] = len(row["genres"])
        out[g] = row
    return out


def cmd_stats(a: argparse.Namespace) -> int:
    db = ChartDB(a.db)
    games = sorted(p.name for p in db.root.iterdir() if (p / "index.json").exists()) if a.game == "all" else [a.game]
    print(json.dumps(coverage(db, games), ensure_ascii=False, indent=1))
    return 0


def cmd_shrink_images(a: argparse.Namespace) -> int:
    db = ChartDB(a.db)
    games = sorted(p.name for p in db.root.iterdir() if (p / "index.json").exists()) if a.game == "all" else [a.game]
    print(json.dumps({g: db.shrink_assets(g, a.image_size) for g in games}, indent=1))
    return 0


def cmd_index(a: argparse.Namespace) -> int:
    print(ChartDB(a.db).build_sqlite(a.sqlite))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vsrgconv", description=__doc__)
    p.add_argument("--db", default="data/db", help="chart database root")
    p.add_argument("--cache", default="data/cache", help="download cache (mirrors URLs)")
    p.add_argument("--offline", action="store_true", help="only use the cache")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("catalog", help="build/refresh the song catalog for a game (or all)")
    s.add_argument("game")
    s.set_defaults(fn=cmd_catalog)

    s = sub.add_parser("scrape", help="fetch charts and/or jackets for every song of a game into the database")
    s.add_argument("game")
    s.add_argument("--charts", action="store_true")
    s.add_argument("--jackets", action="store_true")
    s.add_argument("--only", help="comma-separated song ids")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--refresh", action="store_true", help="re-fetch what is already stored")
    s.add_argument("--image-size", type=int, default=IMAGE_MAX_SIZE, help="long side of stored jackets (px)")
    s.add_argument("--full-images", action="store_true", help="store original image bytes")
    s.add_argument("--progress", action="store_true")
    s.add_argument("--workers", type=int, default=1, help="parallel downloads (set --interval low with this)")
    s.add_argument("--report", help="write the full list of missing charts and errors to this JSON file")
    s.set_defaults(fn=cmd_scrape)

    s = sub.add_parser("extract", help="read one chart's notes from its source into the database")
    s.add_argument("game")
    s.add_argument("song_id")
    s.add_argument("slot", help="canonical difficulty: normal/hard/expert/master/extra/special")
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("convert", help="database chart -> .osu (+ report)")
    s.add_argument("game")
    s.add_argument("song_id")
    s.add_argument("slot")
    s.add_argument("--keys", type=int)
    s.add_argument("--profile", default="baseline")
    s.add_argument("--out", default="out")
    s.set_defaults(fn=cmd_convert)

    s = sub.add_parser("calibrate-sdvxin", help="report colours/extent of a real sdvx.in chart picture")
    s.add_argument("bg")
    s.add_argument("obj")
    s.add_argument("bar")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("check-counts", help="compare extracted note counts with published ones")
    s.add_argument("game")
    s.set_defaults(fn=cmd_check_counts)

    s = sub.add_parser("demo", help="offline end-to-end run on a synthetic chart picture")
    s.add_argument("--keys", type=int, default=8)
    s.add_argument("--out", default="out/demo")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("playtest", help="run orthomania's playtest harness on a .osu (on the PC)")
    s.add_argument("osu")
    s.add_argument("--times", default="")
    s.add_argument("--scroll-speed", type=int, default=29)
    s.add_argument("--ortho", help="orthomania game repo (default $ORTHO_REPO)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_playtest)

    s = sub.add_parser("stats", help="database coverage per game: songs, charts by status, jackets")
    s.add_argument("game", nargs="?", default="all")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("shrink-images", help="re-encode stored images above a size limit, in place")
    s.add_argument("game", nargs="?", default="all")
    s.add_argument("--image-size", type=int, default=IMAGE_MAX_SIZE)
    s.set_defaults(fn=cmd_shrink_images)

    s = sub.add_parser("index", help="build a SQLite index of the database for queries")
    s.add_argument("--sqlite", default="data/index.sqlite")
    s.set_defaults(fn=cmd_index)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
