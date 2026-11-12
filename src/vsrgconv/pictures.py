"""Source pictures: each chart as its source published it, kept beside the redrawn layout for A/B checks.

`vsrgconv pictures <game>` downloads the picture a chart's source publishes (the URLs in the chart's `source` whose
key is `picture` or ends in `_picture`) to `data/pictures/<game>/<genre>/<song>/<slot>.<ext>`, the same layout as
`data/db` and `data/renders`. The file is kept exactly as published: it is the "true source". Each game's
`manifest.json` lists every picture's URL, bytes, sha256 and size, and which charts share one file (a mirror can
reuse one picture for several charts, so a shared picture is the right one for at most one of them).

`vsrgconv ab <game>` pairs each source picture with the chart's stored notes (A = the source picture, B = the
redrawn notes):

- `data/ab/<game>/.../<slot>.png` (with `--images`): the source picture above the notes redrawn at the source's
  scale, for looking at both;
- where a reader for the source's pictures exists, the notes read from the picture against the stored notes, per
  note kind. Chunithm's merged sdvx.in pictures have one (imaging/merged_chart.py). Its accuracy is measured, not
  perfect (docs/sources/chunithm-pictures.md), so a low score is a lead to check, not proof that the stored chart is
  wrong.

Which games have source pictures, and from where, is in docs/sources/pictures.md.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .db import ChartDB, ChartRecord
from .sources.fetch import Fetcher, FetchError

PICTURE_EXTS = {".png", ".webp", ".jpg", ".jpeg", ".gif"}


def picture_urls(cr: ChartRecord) -> list[tuple[str, str]]:
    """(source key, URL) of a chart's published pictures, best first: `picture`, then other `*_picture` keys."""
    src = cr.source or {}
    keys = sorted((k for k in src if k == "picture" or k.endswith("_picture")), key=lambda k: (k != "picture", k))
    return [(k, src[k]) for k in keys if isinstance(src[k], str) and src[k].startswith("http")]


def _ext(url: str) -> str:
    ext = PurePosixPath(urlsplit(url).path).suffix.lower()
    return ext if ext in PICTURE_EXTS else ".png"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _size(path: Path) -> list[int] | None:
    from PIL import Image

    try:
        with Image.open(path) as im:
            return [im.width, im.height]
    except Exception:       # a file that is not a picture is reported, not fatal
        return None


def _manifest_path(out: Path, game: str) -> Path:
    return out / game / "manifest.json"


def load_manifest(out: str | Path, game: str) -> dict[str, dict[str, Any]]:
    p = _manifest_path(Path(out), game)
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def fetch_pictures(db: ChartDB, fetcher: Fetcher, game: str, out: str | Path = "data/pictures",
                   only: set[str] | None = None, slot: str | None = None, workers: int = 8, refresh: bool = False,
                   log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Download every chart's source picture of one game. Pictures already there are kept unless `refresh`."""
    out = Path(out)
    manifest = load_manifest(out, game)
    jobs: list[tuple[str, list[tuple[str, str]], Path]] = []
    summary = {"charts": 0, "with_picture": 0, "downloaded": 0, "already": 0, "failed": 0}
    for rec in db.iter_songs(game):
        if only and rec.song_id not in only:
            continue
        rel = db.song_dir(rec).relative_to(db.root)
        for s, cr in rec.charts.items():
            if slot and s != slot:
                continue
            summary["charts"] += 1
            urls = picture_urls(cr)
            if not urls:
                continue
            summary["with_picture"] += 1
            jobs.append((f"{rec.song_id}/{s}", urls, out / rel / s))

    def one(job: tuple[str, list[tuple[str, str]], Path]) -> tuple[str, dict[str, Any] | None, str]:
        key, urls, stem = job
        old = manifest.get(key)
        if old and not refresh and (out / game / old["file"]).exists():
            return key, old, "already"
        errors = []
        for source_key, url in urls:
            try:
                data = fetcher.get(url, refresh=refresh)
            except FetchError as e:
                errors.append(str(e))
                continue
            dst = stem.with_suffix(_ext(url))
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            cached = fetcher.cache_path(url)
            try:                                # the download cache already holds the bytes: share them
                os.link(cached, dst)
            except OSError:
                dst.write_bytes(data)
            return key, {"url": url, "key": source_key, "file": dst.relative_to(out / game).as_posix(),
                         "bytes": len(data), "sha256": _sha256(data), "size": _size(dst)}, "downloaded"
        return key, {"errors": errors}, "failed"

    failed: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max(1, workers)) as pool:
        for i, (key, entry, status) in enumerate(pool.map(one, jobs), 1):
            summary[status] += 1
            if status == "failed":
                failed[key] = entry["errors"] if entry else []
            else:
                manifest[key] = entry
            if log and (i % 500 == 0 or i == len(jobs)):
                log(f"pictures {game}: {i}/{len(jobs)}")
    _mark_shared(manifest)
    p = _manifest_path(out, game)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=1) + "\n", "utf-8")
    summary["bytes"] = sum(e.get("bytes", 0) for e in manifest.values())
    summary["shared"] = sum(1 for e in manifest.values() if e.get("shared_with"))
    summary["failed_charts"] = failed
    return summary


def _mark_shared(manifest: dict[str, dict[str, Any]]) -> None:
    by_hash: dict[str, list[str]] = {}
    for key, e in manifest.items():
        e.pop("shared_with", None)
        if e.get("sha256"):
            by_hash.setdefault(e["sha256"], []).append(key)
    for keys in by_hash.values():
        if len(keys) > 1:
            for k in keys:
                manifest[k]["shared_with"] = [o for o in keys if o != k]


# ------------------------------------------------------------------------------------------------------------ A/B

def _chunithm_compare(picture: bytes, chart) -> dict[str, Any]:
    """Note heads read from a merged sdvx.in picture against the stored chart (imaging/merged_chart.py)."""
    from .imaging import merged_chart as mc

    reading = mc.read_picture(picture)
    got = mc.match_heads(reading, chart)
    kinds = {k: v for k, v in got.items() if k not in ("extra", "offset")}
    total = sum(sum(v.values()) for v in kinds.values())
    ok = sum(v.get("ok", 0) for v in kinds.values())
    return {"notes": total, "matched": ok, "agreement": round(ok / total, 4) if total else None,
            "kinds": kinds, "extra_heads": got["extra"]["heads"], "offset_beats": str(got["offset"]),
            "measures_read": len(reading.timeline.measures), "beat_px": reading.timeline.beat_px}


#: game -> function(picture bytes, stored Chart) -> metrics; games without one get the comparison picture only
READERS: dict[str, Callable[[bytes, Any], dict[str, Any]]] = {"chunithm": _chunithm_compare}


def ab_jobs(db: ChartDB, game: str, pictures: str | Path = "data/pictures", only: set[str] | None = None,
            slot: str | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Charts that have both a source picture and stored notes."""
    pictures = Path(pictures)
    manifest = load_manifest(pictures, game)
    jobs, counts = [], {"pictures": 0, "with_notes": 0, "without_notes": 0}
    for key, e in sorted(manifest.items()):
        song_id, s = key.rsplit("/", 1)
        if (only and song_id not in only) or (slot and s != slot) or "file" not in e:
            continue
        counts["pictures"] += 1
        if not db.has_chart(game, song_id, s):
            counts["without_notes"] += 1
            continue
        counts["with_notes"] += 1
        jobs.append({"game": game, "key": key, "picture": str(pictures / game / e["file"]),
                     "chart": str(db.chart_path(game, song_id, s)), "file": e["file"],
                     "shared_with": e.get("shared_with", [])})
    return jobs, counts


def ab_one(job: dict[str, Any], out: str | Path | None = None) -> dict[str, Any]:
    """Compare one chart: metrics where a reader exists, and the comparison picture when `out` is given."""
    import gzip

    from .model import Chart

    res: dict[str, Any] = {"key": job["key"]}
    if job.get("shared_with"):
        res["shared_with"] = job["shared_with"]
    try:
        raw = Path(job["chart"]).read_bytes()
        chart = Chart.from_json((gzip.decompress(raw) if job["chart"].endswith(".gz") else raw).decode("utf-8"))
        picture = Path(job["picture"]).read_bytes()
    except Exception as e:      # one bad file must not stop a run
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    reader = READERS.get(job["game"])
    if reader:
        try:
            res.update(reader(picture, chart))
        except Exception as e:  # the reader could not read this picture; the comparison picture still helps
            res["error"] = f"{type(e).__name__}: {e}"
    if out is not None:
        try:
            dst = Path(out) / job["game"] / PurePosixPath(job["file"]).with_suffix(".png")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(comparison_png(picture, chart, beat_px=res.get("beat_px") or 96))
            res["image"] = str(dst)
        except Exception as e:
            res["image_error"] = f"{type(e).__name__}: {e}"
    return res


def comparison_png(picture: bytes, chart, beat_px: float = 96, label_px: int = 22) -> bytes:
    """The source picture (A) above the stored notes (B) redrawn at the source's scale: the same column height, and
    the main BPM's beat `beat_px` tall (the source's commonest beat spacing where a reader measured it; sdvx.in's
    merged pictures mostly use 96 px)."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from .imaging.render import RenderConfig, _font_for_title, render_chart

    with Image.open(BytesIO(picture)) as im:
        a = im.convert("RGBA")
    flat = Image.new("RGB", a.size, (0, 0, 0))
    flat.paste(a, mask=a.getchannel("A"))
    main = abs(chart.timing.main_bpm(chart.end_beat)) or 120.0
    cfg = RenderConfig(px_per_ms_override=beat_px * main / 60000, column_px=max(400, flat.height - 40),
                       lane_area_px=100, gutter_px=50, rail_px=24, max_width_px=max(flat.width, 4096))
    b = render_chart(chart, cfg).convert("RGB")
    width = max(flat.width, b.width)
    canvas = Image.new("RGB", (width, flat.height + b.height + 2 * label_px), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    font = _font_for_title(14)
    draw.text((6, 3), "A: source picture", fill=(255, 220, 120), font=font)
    canvas.paste(flat, (0, label_px))
    draw.text((6, label_px + flat.height + 3), "B: redrawn from the stored notes", fill=(120, 220, 255), font=font)
    canvas.paste(b, (0, 2 * label_px + flat.height))
    q = canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    buf = BytesIO()
    q.save(buf, "PNG", compress_level=9)
    return buf.getvalue()


def ab_summary(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rs = [r for r in results if "agreement" in r and r["agreement"] is not None]
    if not rs:
        return {"compared": 0}
    ags = sorted(r["agreement"] for r in rs)
    bands = {"≥0.95": 0, "0.8–0.95": 0, "0.5–0.8": 0, "<0.5": 0}
    for v in ags:
        bands["≥0.95" if v >= 0.95 else "0.8–0.95" if v >= 0.8 else "0.5–0.8" if v >= 0.5 else "<0.5"] += 1
    notes = sum(r["notes"] for r in rs)
    return {"compared": len(rs), "notes": notes, "matched": sum(r["matched"] for r in rs),
            "agreement_all_notes": round(sum(r["matched"] for r in rs) / notes, 4) if notes else None,
            "agreement_median_chart": ags[len(ags) // 2], "charts_by_agreement": bands,
            "mean_measures_read": round(math.fsum(r.get("measures_read", 0) for r in rs) / len(rs), 1)}
