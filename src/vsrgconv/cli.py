"""Command line: `python -m vsrgconv <command>` (or `vsrgconv <command>` once installed). `--help` on each command."""
from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path

from .db import ChartDB, ChartRecord, SongRecord
from .sources.fetch import Fetcher, FetchError


def _fetcher(a: argparse.Namespace) -> Fetcher:
    return Fetcher(cache_dir=a.cache, offline=a.offline, min_interval=a.interval)


def cmd_catalog(a: argparse.Namespace) -> int:
    from .sources.chunithm import OtogeDbChunithm, SdvxinChunithm, build_catalog

    if a.game != "chunithm":
        print(f"no catalog adapter for {a.game} yet", file=sys.stderr)
        return 2
    f = _fetcher(a)
    primary = list(OtogeDbChunithm(f).list_songs())
    sdvx: list[SongRecord] = []
    if not a.no_sdvxin:
        try:
            sdvx = list(SdvxinChunithm(f).list_songs())
        except FetchError as e:
            print(f"sdvx.in song lists unavailable ({e}); catalog has otoge-db data only", file=sys.stderr)
    songs, unmatched = build_catalog(primary, sdvx)
    new = ChartDB(a.db).upsert_songs(songs)
    print(json.dumps({"songs": len(songs), "new": new, "sdvxin_rows": len(sdvx),
                      "sdvxin_unmatched": len(unmatched), "unmatched_sample": unmatched[:20]}, ensure_ascii=False))
    return 0


def cmd_extract(a: argparse.Namespace) -> int:
    from .sources.chunithm import SdvxinChunithm

    db = ChartDB(a.db)
    song = db.get_song(a.game, a.song_id)
    if song is None:
        print(f"{a.game}/{a.song_id} not in database; run `catalog` first", file=sys.stderr)
        return 2
    chart = SdvxinChunithm(_fetcher(a)).fetch_chart(song, a.slot)
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

    s = sub.add_parser("catalog", help="build/refresh the song catalog for a game")
    s.add_argument("game")
    s.add_argument("--no-sdvxin", action="store_true", help="skip sdvx.in's song lists")
    s.set_defaults(fn=cmd_catalog)

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

    s = sub.add_parser("index", help="build a SQLite index of the database for queries")
    s.add_argument("--sqlite", default="data/index.sqlite")
    s.set_defaults(fn=cmd_index)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
