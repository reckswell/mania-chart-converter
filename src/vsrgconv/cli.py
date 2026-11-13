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
    """Fill the database for one game: charts (notes) and/or jackets, resumable (skips what is already there)."""
    from .scrape import scrape
    from .sources import load_all

    db = ChartDB(a.db)
    songs = list(db.iter_songs(a.game))
    if a.only:
        wanted = set(a.only.split(","))
        songs = [s for s in songs if s.song_id in wanted]
    songs = songs[: a.limit or None]
    res = scrape(db, a.game, load_all()[a.game], _fetcher(a), songs, charts=a.charts, jackets=a.jackets,
                 refresh=a.refresh, workers=a.workers, image_size=a.image_size, full_images=a.full_images,
                 progress=a.progress)
    stats = res.stats
    stats["error_count"] = len(res.errors)
    stats["errors"] = res.errors[:30]
    stats["missing_sample"] = res.missing[:60]
    if a.report:
        Path(a.report).write_text(json.dumps({**stats, "errors": res.errors, "missing": res.missing},
                                             ensure_ascii=False, indent=1) + "\n", "utf-8")
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
    """Per game: songs (removed; not_in_game also counts songs never in the arcade), charts by status, jackets."""
    out: dict[str, dict[str, Any]] = {}
    for g in games:
        row: dict[str, Any] = {"songs": 0, "removed_songs": 0, "not_in_game": 0, "genres": set(), "charts": 0,
                               "jackets": 0, "chart_jackets": 0, "by_status": {}, "by_slot": {}}
        for rec in db.iter_songs(g):
            row["songs"] += 1
            row["removed_songs"] += bool(rec.extra.get("removed"))
            row["not_in_game"] += bool(rec.extra.get("removed") or rec.extra.get("not_in_arcade"))
            row["genres"].add(rec.genre_slug)
            row["jackets"] += "jacket" in rec.assets
            for slot, cr in rec.charts.items():
                row["charts"] += 1
                row["chart_jackets"] += any(k.startswith("jacket") for k in cr.assets)
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


def _render_one(job: tuple[str, str, float, float, bool]) -> tuple[str, int, str]:
    """Worker: read one stored chart, write its layout picture. Returns (picture path, bytes, error)."""
    import gzip

    from .imaging.render import RenderConfig, render_png
    from .model import Chart

    src, dst, scale, approach_rate, compact = job
    cfg = RenderConfig.compact() if compact else RenderConfig(approach_rate=approach_rate)
    try:
        data = Path(src).read_bytes()
        chart = Chart.from_json((gzip.decompress(data) if src.endswith(".gz") else data).decode("utf-8"))
        png = render_png(chart, cfg, scale=scale)
    except Exception as e:      # one bad chart must not stop a full run
        return dst, 0, f"{type(e).__name__}: {e}"
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_bytes(png)
    return dst, len(png), ""


def _reference_urls(cr: ChartRecord) -> dict[str, str]:
    """External chart pictures or pages a chart without stored notes points at (sdvx.in, RRRGR, textage ...)."""
    return {k: v for k, v in cr.source.items() if isinstance(v, str) and v.startswith("http")}


def cmd_render(a: argparse.Namespace) -> int:
    """Draw a layout picture for every chart with stored notes, into a folder that mirrors the database."""
    db = ChartDB(a.db)
    games = sorted(p.name for p in db.root.iterdir() if (p / "index.json").exists()) if a.game == "all" else [a.game]
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    jobs: list[tuple[str, str, float, float, bool]] = []
    summary: dict[str, dict[str, Any]] = {}
    without_notes: list[dict[str, Any]] = []
    for g in games:
        row = summary.setdefault(g, {"charts": 0, "with_notes": 0, "rendered": 0, "already": 0, "failed": 0,
                                     "bytes": 0, "without_notes": 0, "without_notes_with_reference": 0})
        for rec in db.iter_songs(g):
            if only and rec.song_id not in only:
                continue
            rel = db.song_dir(rec).relative_to(db.root)
            for slot, cr in rec.charts.items():
                if a.slot and slot != a.slot:
                    continue
                row["charts"] += 1
                if not db.has_chart(g, rec.song_id, slot):
                    refs = _reference_urls(cr)
                    row["without_notes"] += 1
                    row["without_notes_with_reference"] += bool(refs)
                    without_notes.append({"game": g, "song_id": rec.song_id, "slot": slot, "status": cr.status,
                                          "references": refs})
                    continue
                row["with_notes"] += 1
                dst = out / rel / f"{slot}.png"
                if dst.exists() and not a.refresh:
                    row["already"] += 1
                    row["bytes"] += dst.stat().st_size
                    continue
                jobs.append((str(db.chart_path(g, rec.song_id, slot)), str(dst), a.scale, a.approach_rate,
                             a.compact))
    errors: list[dict[str, str]] = []
    if a.workers > 1 and len(jobs) > 1:
        from multiprocessing import Pool

        with Pool(a.workers) as pool:
            results = list(_progress(pool.imap_unordered(_render_one, jobs, chunksize=8), len(jobs), a.progress))
    else:
        results = list(_progress(map(_render_one, jobs), len(jobs), a.progress))
    for dst, size, err in results:
        g = Path(dst).relative_to(out).parts[0]
        if err:
            summary[g]["failed"] += 1
            errors.append({"picture": dst, "error": err})
        else:
            summary[g]["rendered"] += 1
            summary[g]["bytes"] += size
    if a.report:
        Path(a.report).write_text(json.dumps({"summary": summary, "errors": errors, "without_notes": without_notes},
                                             ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({"summary": summary, "errors": errors[:20]}, ensure_ascii=False, indent=1))
    return 1 if errors else 0


def _progress(it, total: int, show: bool):
    for i, x in enumerate(it, 1):
        if show and (i % 500 == 0 or i == total):
            print(f"  {i}/{total}", file=sys.stderr, flush=True)
        yield x


def _game_counts(db: ChartDB, key: str) -> tuple[int, int]:
    """Songs in the catalog and charts with notes, without reading every song.json."""
    folder = db.root / key
    index = folder / "index.json"
    songs = len(json.loads(index.read_text("utf-8"))) if index.exists() else 0
    return songs, sum(1 for _ in folder.rglob("*.chart.json*")) if folder.is_dir() else 0


def cmd_games(a: argparse.Namespace) -> int:
    """The games the framework knows: added ones with what their sources can do and what the database holds, and
    (--all) other VSRGs known but not added yet."""
    from .games.registry import GAMES, candidates
    from .sources import load_all

    sources = load_all()
    db = ChartDB(a.db)
    if a.key:
        if a.key in GAMES:
            g = GAMES[a.key]
            src = sources.get(a.key)
            out = {**g.to_dict(), "origin": g.origin,
                   "source": {"catalog": bool(src), "charts": bool(src and src.chart),
                              "jackets": bool(src and (src.jacket or src.chart_jackets)),
                              "notes": src.notes if src else "no source yet"},
                   "database": coverage(db, [a.key])[a.key] if (db.root / a.key / "index.json").exists() else {}}
        elif a.key in candidates():
            out = {**candidates()[a.key], "how_to_add": f"vsrgconv add-game {a.key}"}
        else:
            print(f"unknown game {a.key!r}; `vsrgconv games --all` lists every game", file=sys.stderr)
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    rows = []
    for key, g in sorted(GAMES.items()):
        src = sources.get(key)
        can = [n for n, ok in (("catalog", src), ("charts", src and src.chart),
                               ("jackets", src and (src.jacket or src.chart_jackets))) if ok]
        songs, charts = _game_counts(db, key)
        rows.append({"key": key, "name": g.name, "lanes": g.lanes, "status": g.status,
                     "added_by": "built-in" if g.origin == "built-in" else "definition file",
                     "source": ",".join(can) or "none", "songs": songs, "charts_with_notes": charts})
    if a.all:
        for key, c in sorted(candidates().items()):
            rows.append({"key": key, "name": c["name"], "lanes": c.get("lanes", ""), "status": "not added",
                         "added_by": "", "source": f"{c.get('chart_format') or 'no open format'}"
                         + (" (readable: add with a folder)" if c.get("readable") else ""),
                         "songs": "", "charts_with_notes": ""})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    cols = ["key", "name", "lanes", "status", "source", "songs", "charts_with_notes"]
    width = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(width[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(width[c]) for c in cols))
    if not a.all:
        print(f"\n{len(candidates())} more VSRGs are known but not added: `vsrgconv games --all`", file=sys.stderr)
    return 0


ADAPTER_STUB = '''"""{name}: source adapter, added with `vsrgconv add-game`. See docs/adding-a-game.md.

Fill in the three functions; each returns what the built-in adapters return (src/vsrgconv/sources/*.py). They run
when this file sits beside {key}.json in a games folder (data/games by default).
"""
from __future__ import annotations

from vsrgconv.db import ChartRecord, SongRecord
from vsrgconv.games.registry import canonical_difficulty, native_name
from vsrgconv.model import Chart, ChartMeta
from vsrgconv.sources import GameSource, register
from vsrgconv.sources.fetch import Fetcher, NotFound

GAME = "{key}"


def catalog(fetcher: Fetcher) -> tuple[list[SongRecord], dict]:
    """Every song with every difficulty (metadata only), and a report dict printed by `vsrgconv catalog`."""
    songs: list[SongRecord] = []
    # rows = json.loads(fetcher.get(SONG_LIST_URL))
    # for row in rows:
    #     rec = SongRecord(game=GAME, song_id=str(row["id"]), title=row["title"], artist=row["artist"],
    #                      genre=row["genre"])
    #     for native, level in row["levels"].items():
    #         slot = canonical_difficulty(GAME, native)
    #         rec.charts[slot] = ChartRecord(slot, native_name(GAME, native), str(level))
    #     songs.append(rec)
    return songs, {{}}


def chart(fetcher: Fetcher, song: SongRecord, slot: str) -> Chart | None:
    """One chart's notes in the intermediate format, in this game's lane units; None if no source has it.
    Raise NotFound when the file is missing. Beats stay Fractions."""
    return None


def jacket(fetcher: Fetcher, song: SongRecord) -> tuple[bytes, str, str] | None:
    """(image bytes, extension, url), or None."""
    return None


register(GameSource(GAME, catalog=catalog, chart=chart, jacket=jacket, notes="TODO: where the data comes from"))
'''


def _pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        k, sep, v = item.partition("=")
        if not sep or not k or not v:
            raise SystemExit(f"--{what} wants NATIVE=VALUE, got {item!r}")
        out[k] = v
    return out


def cmd_add_game(a: argparse.Namespace) -> int:
    """Write a game definition (and optionally an adapter stub) into the games folder."""
    from .games.registry import GAMES, Game, candidates
    from .sources.local import FORMATS

    key = a.key.lower()
    if key in GAMES and GAMES[key].origin == "built-in":
        print(f"{key} is already a built-in game", file=sys.stderr)
        return 2
    folder = Path(a.games)
    path = folder / f"{key}.json"
    if path.exists() and not a.force:
        print(f"{path} exists; --force replaces it", file=sys.stderr)
        return 2
    base = {k: v for k, v in candidates().get(key, {}).items() if k not in ("readable", "checked", "status")}
    d: dict[str, Any] = {"key": key, "name": a.name or base.get("name", ""), "lanes": a.lanes or base.get("lanes", 4),
                         "variable_width": a.variable_width or base.get("variable_width", False),
                         "difficulties": {**base.get("difficulties", {}), **_pairs(a.difficulty, "difficulty")},
                         "native_names": base.get("native_names", {}),
                         "genres": {**base.get("genres", {}), **_pairs(a.genre, "genre")},
                         "status": "in-progress" if a.folder or a.adapter else "planned",
                         "chart_format": a.format or base.get("chart_format", ""), "notes": base.get("notes", "")}
    if not d["name"]:
        print(f"{key} is not a known VSRG; give it a --name (known: `vsrgconv games --all`)", file=sys.stderr)
        return 2
    if a.folder:
        if d["chart_format"] not in FORMATS:
            print(f"--folder needs --format, one of {', '.join(FORMATS)} (got {d['chart_format'] or 'none'}); for "
                  f"other formats write an adapter (--adapter)", file=sys.stderr)
            return 2
        d["source"] = {"type": "folder", "folder": a.folder, "format": d["chart_format"]}
    try:
        Game.from_dict(d, origin=str(path))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", "utf-8")
    written = [str(path)]
    if a.adapter:
        stub = path.with_suffix(".py")
        if stub.exists() and not a.force:
            print(f"{stub} exists; kept it", file=sys.stderr)
        else:
            stub.write_text(ADAPTER_STUB.format(key=key, name=d["name"]), "utf-8")
            written.append(str(stub))
    nxt = [f"vsrgconv catalog {key}", f"vsrgconv scrape {key} --charts --jackets", f"vsrgconv render {key}"]
    print(json.dumps({"written": written, "next": nxt if (a.folder or a.adapter) else
                      [f"fill in a source: re-run with --folder and --format, or --adapter"]}, ensure_ascii=False,
                     indent=1))
    return 0


def cmd_publish(a: argparse.Namespace) -> int:
    from .publish import publish_hf

    parts = a.parts.split(",") if a.parts else None
    print(json.dumps(publish_hf(a.repo, a.data, parts, delete=a.delete, dry_run=a.dry_run, xet=not a.no_xet,
                                batch_files=a.batch_files), indent=1))
    return 0


def _games_arg(db: ChartDB, game: str) -> list[str]:
    return sorted(p.name for p in db.root.iterdir() if (p / "index.json").exists()) if game == "all" else [game]


def cmd_pictures(a: argparse.Namespace) -> int:
    """Download each chart's source picture (the "true source") next to the database: data/pictures."""
    from .pictures import fetch_pictures, fetch_sdvxin

    db = ChartDB(a.db)
    only = set(a.only.split(",")) if a.only else None
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if a.progress else None
    if a.source == "sdvxin":
        from .pictures import SDVXIN_GAMES

        res = {g: fetch_sdvxin(db, _fetcher(a), a.out, only=only, slot=a.slot, workers=min(a.workers, 4),
                               refresh=a.refresh, log=log, game=g)
               for g in (SDVXIN_GAMES if a.game == "all" else [a.game])}
        print(json.dumps({g: {**r, "failed_charts": dict(list(r["failed_charts"].items())[:10])}
                          for g, r in res.items()}, ensure_ascii=False, indent=1))
        return 1 if any(r["failed"] for r in res.values()) else 0
    out = {g: fetch_pictures(db, _fetcher(a), g, a.out, only=only, slot=a.slot, workers=a.workers,
                             refresh=a.refresh, log=log) for g in _games_arg(db, a.game)}
    shown = {g: {k: (v if k != "failed_charts" else dict(list(v.items())[:10])) for k, v in r.items()}
             for g, r in out.items() if r["charts"]}
    print(json.dumps(shown, ensure_ascii=False, indent=1))
    return 1 if any(r["failed"] for r in out.values()) else 0


def _ab_worker(args: tuple[dict, str | None]) -> dict:
    from .pictures import ab_one

    return ab_one(*args)


def cmd_ab(a: argparse.Namespace) -> int:
    """A/B: each chart's source picture against its stored notes (metrics, and comparison pictures with --images)."""
    from .pictures import READERS, ab_jobs, ab_summary

    db = ChartDB(a.db)
    only = set(a.only.split(",")) if a.only else None
    report: dict[str, Any] = {}
    for g in _games_arg(db, a.game):
        jobs, counts = ab_jobs(db, g, a.pictures, only=only, slot=a.slot,
                               source=None if a.source == "db" else a.source)
        if not counts["pictures"]:
            continue
        if g not in READERS and not a.images:
            report[g] = {**counts, "note": "no picture reader for this game: use --images to compare by eye"}
            continue
        args = [(j, a.out if a.images else None) for j in jobs]
        if a.workers > 1 and len(args) > 1:
            from multiprocessing import Pool

            with Pool(a.workers) as pool:
                results = list(_progress(pool.imap_unordered(_ab_worker, args, chunksize=4), len(args), a.progress))
        else:
            results = list(_progress(map(_ab_worker, args), len(args), a.progress))
        results.sort(key=lambda r: r["key"])
        errors = [r for r in results if "error" in r]
        report[g] = {**counts, **ab_summary(results), "errors": len(errors),
                     "lowest": [{k: r[k] for k in ("key", "agreement", "notes", "matched", "shared_with") if k in r}
                                for r in sorted((r for r in results if r.get("agreement") is not None),
                                                key=lambda r: r["agreement"])[:15]],
                     "results": results}
    if a.report:
        Path(a.report).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(json.dumps({g: {k: v for k, v in r.items() if k != "results"} for g, r in report.items()},
                     ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_update(a: argparse.Namespace) -> int:
    """Bring the database up to date with what the games' sources have released since the last run."""
    from .sources import load_all
    from .update import UpdateOptions, update

    sources = load_all()
    db = ChartDB(a.db)
    games = sorted(sources) if not a.game_keys or a.game_keys == ["all"] else a.game_keys
    unknown = [g for g in games if g not in sources]
    if unknown:
        print(f"no source for {', '.join(unknown)}; known: {', '.join(sorted(sources))}", file=sys.stderr)
        return 2
    data = db.root.parent
    opts = UpdateOptions(charts=not a.no_charts, jackets=not a.no_jackets, retry_days=a.retry_days,
                         retry_all=a.retry_all, force=a.force, check_only=a.check, render=a.render or a.all_parts,
                         pictures=a.pictures or a.all_parts, renders_dir=data / "renders",
                         pictures_dir=data / "pictures", hub=a.publish or a.hub, workers=a.workers,
                         image_size=a.image_size, progress=a.progress)
    log = (lambda m: print(m, file=sys.stderr, flush=True)) if a.progress else None
    out = update(db, games, sources, _fetcher(a), opts, publish=None if a.check else a.publish, xet=not a.no_xet,
                 log=log)
    if a.report:
        Path(a.report).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if any(r["status"].startswith("error") for r in out["games"].values()) else 0


def cmd_index(a: argparse.Namespace) -> int:
    print(ChartDB(a.db).build_sqlite(a.sqlite))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vsrgconv", description=__doc__)
    p.add_argument("--db", default="data/db", help="chart database root")
    p.add_argument("--cache", default="data/cache", help="download cache (mirrors URLs)")
    p.add_argument("--offline", action="store_true", help="only use the cache")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--games", default="data/games", help="folder of added games' definitions (and $VSRGCONV_GAMES)")
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

    s = sub.add_parser("render", help="draw a layout picture for every chart with stored notes (data/renders)")
    s.add_argument("game", nargs="?", default="all")
    s.add_argument("--only", help="comma-separated song ids")
    s.add_argument("--slot", help="only this difficulty slot")
    s.add_argument("--out", default="data/renders", help="picture folder; mirrors the database layout")
    s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--approach-rate", "--ar", type=float, default=9.0,
                   help="vertical spacing as at this osu! approach rate (AR 9: 0.8 px per ms)")
    s.add_argument("--compact", action="store_true", help="the older compact layout: 24 px per beat, 8 measures "
                                                          "a column")
    s.add_argument("--refresh", action="store_true", help="redraw pictures that already exist")
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--progress", action="store_true")
    s.add_argument("--report", help="write the summary, errors and the charts without notes (with their "
                                    "external picture URLs) to this JSON file")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("pictures", help="download each chart's source picture, the \"true source\" (data/pictures)")
    s.add_argument("game", nargs="?", default="all")
    s.add_argument("--only", help="comma-separated song ids")
    s.add_argument("--slot", help="only this difficulty slot")
    s.add_argument("--out", default="data/pictures", help="picture folder; mirrors the database layout")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument("--refresh", action="store_true", help="download pictures again")
    s.add_argument("--from", dest="source", choices=["db", "sdvxin"], default="db",
                   help="db: the picture URLs in the database; sdvxin: sdvx.in's layers of every drawn chart "
                        "(chunithm, sekai)")
    s.add_argument("--progress", action="store_true")
    s.set_defaults(fn=cmd_pictures)

    s = sub.add_parser("ab", help="A/B: source pictures against the stored notes (docs/sources/pictures.md)")
    s.add_argument("game", nargs="?", default="all")
    s.add_argument("--only", help="comma-separated song ids")
    s.add_argument("--slot", help="only this difficulty slot")
    s.add_argument("--pictures", default="data/pictures", help="where `vsrgconv pictures` put the source pictures")
    s.add_argument("--images", action="store_true", help="also write comparison pictures (source above redrawn)")
    s.add_argument("--out", default="data/ab", help="comparison pictures; mirrors the database layout")
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--progress", action="store_true")
    s.add_argument("--report", help="write every chart's result to this JSON file")
    s.add_argument("--from", dest="source", choices=["db", "sdvxin"], default="db",
                   help="which source pictures: db (manifest.json) or sdvxin (manifest-sdvxin.json)")
    s.set_defaults(fn=cmd_ab)

    s = sub.add_parser("games", help="list the games: added ones, and with --all other VSRGs not added yet")
    s.add_argument("key", nargs="?", help="show one game in full")
    s.add_argument("--all", action="store_true", help="also list known VSRGs that are not added yet")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_games)

    s = sub.add_parser("add-game", help="add a VSRG: write a game definition (docs/adding-a-game.md)")
    s.add_argument("key", help="short lower-case key, e.g. maimai; a known VSRG's details are filled in")
    s.add_argument("--name", help="display name (needed for a game not in `games --all`)")
    s.add_argument("--lanes", type=int)
    s.add_argument("--variable-width", action="store_true", help="notes have widths (Chunithm, Sekai)")
    s.add_argument("--difficulty", action="append", metavar="NATIVE=SLOT",
                   help="map a native difficulty to a ladder slot (easy normal hard expert master extra special)")
    s.add_argument("--genre", action="append", metavar="NATIVE=SLUG", help="map a native genre to a folder name")
    s.add_argument("--folder", help="read chart files from this folder (no code needed); see --format")
    s.add_argument("--format", help="chart file format: stepmania, c2s, sus, aff, bestdori or gh")
    s.add_argument("--adapter", action="store_true", help="also write a Python adapter stub to fill in")
    s.add_argument("--force", action="store_true", help="replace an existing definition")
    s.set_defaults(fn=cmd_add_game)

    s = sub.add_parser("publish", help="mirror the database and pictures to a private Hugging Face dataset")
    s.add_argument("repo", help="<user>/<dataset>; created private if missing (token: $HF_TOKEN)")
    s.add_argument("--data", default="data", help="folder holding db/, renders/, pictures/, games/ and ab/")
    s.add_argument("--parts", help="comma-separated: db, renders, pictures, games, ab (default: all but ab)")
    s.add_argument("--delete", action="store_true", help="also remove dataset files that are gone locally")
    s.add_argument("--dry-run", action="store_true", help="only count what would be uploaded")
    s.add_argument("--no-xet", action="store_true", help="upload through Git LFS in batches instead of Xet storage "
                                                         "(cloud sessions: docs/hosting.md)")
    s.add_argument("--batch-files", type=int, default=1000, help="files per commit with --no-xet")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("update", help="add the songs and charts the games released since the last run "
                                      "(docs/updating.md)")
    s.add_argument("game_keys", nargs="*", metavar="game", help="games to update (default: all)")
    s.add_argument("--check", action="store_true", help="only say which games' sources changed; write nothing")
    s.add_argument("--force", action="store_true", help="rebuild catalogs even when their files did not change")
    s.add_argument("--retry-days", type=int, default=30,
                   help="look for a chart without notes on every run for this many days after it is first seen")
    s.add_argument("--retry-all", action="store_true", help="also look again for every chart without notes")
    s.add_argument("--no-charts", action="store_true", help="do not fetch notes")
    s.add_argument("--no-jackets", action="store_true", help="do not fetch jackets")
    s.add_argument("--render", action="store_true", help="draw layout pictures of the charts stored in this run "
                                                         "(data/renders)")
    s.add_argument("--pictures", action="store_true", help="download source pictures of new charts (data/pictures)")
    s.add_argument("--all", dest="all_parts", action="store_true", help="--render and --pictures")
    s.add_argument("--publish", metavar="REPO", help="upload the files this run changed to this Hugging Face dataset "
                                                     "(e.g. Reckswell/vsrgs); picture manifests are read from it")
    s.add_argument("--hub", metavar="REPO", help="read picture manifests from this dataset without uploading")
    s.add_argument("--no-xet", action="store_true", help="publish through Git LFS (cloud sessions: docs/hosting.md)")
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--image-size", type=int, default=IMAGE_MAX_SIZE, help="long side of stored jackets (px)")
    s.add_argument("--progress", action="store_true")
    s.add_argument("--report", help="also write the report to this JSON file")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("index", help="build a SQLite index of the database for queries")
    s.add_argument("--sqlite", default="data/index.sqlite")
    s.set_defaults(fn=cmd_index)

    a = p.parse_args(argv)
    from .sources import load_user_games

    load_user_games([a.games])
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
