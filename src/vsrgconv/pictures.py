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
import re
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


def _manifest_path(out: Path, game: str, source: str | None = None) -> Path:
    return out / game / (f"manifest-{source}.json" if source else "manifest.json")


def load_manifest(out: str | Path, game: str, source: str | None = None) -> dict[str, dict[str, Any]]:
    """A game's picture manifest: `manifest.json` for the pictures named in the database (`source.picture`), or
    `manifest-<source>.json` for a source fetched on its own (`sdvxin`)."""
    p = _manifest_path(Path(out), game, source)
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
            size = _size(dst)
            if size is None:        # a site that moved its pictures can answer with a web page: not the source
                dst.unlink()
                cached.unlink(missing_ok=True)
                errors.append(f"{url}: not a picture ({data[:40]!r})")
                continue
            return key, {"url": url, "key": source_key, "file": dst.relative_to(out / game).as_posix(),
                         "bytes": len(data), "sha256": _sha256(data), "size": size}, "downloaded"
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


# ----------------------------------------------------------------------------------- sdvx.in (Chunithm, Sekai)
#
# sdvx.in publishes each chart as three same-size layers that its viewer stacks (bottom to top): the lanes (`bg`, one
# per song), the notes (`obj`, one per difficulty) and the measure numbers (`bar`, one per song). Since October 2026
# they are lossy WebP. `fetch_sdvxin` keeps the three layers exactly as published and adds a merged picture (the
# viewer's stack on black) for looking at. URL scheme: docs/sources/chunithm-sdvxin.md; the Project SEKAI section
# (`/prsk/`) has the same layers without the numbered folders.

SDVXIN_LAYERS = ("bg", "obj", "bar")
SDVXIN_MERGED_QUALITY = 90
SDVXIN = "https://sdvx.in"
SDVXIN_GAMES = ("chunithm", "sekai")
SEKAI_KEYS = {"esy": "easy", "nml": "normal", "hrd": "hard", "exp": "expert", "mst": "master", "apd": "append"}
SEKAI_TITLE_FIXES = {"drop pop candy": "dropop candy"}       # game title -> sdvx.in's, where artists differ too


def sdvxin_viewer_url(sid: str, key: str, game: str = "chunithm") -> str:
    if game == "sekai":
        return f"{SDVXIN}/prsk/_/viewer.php?id={sid}&diff={key}"
    return f"{SDVXIN}/chunithm/_/viewer.php?id={sid}&diff={'we' if key == 'end' else key}"


def sdvxin_layer_urls(ref: dict[str, str], game: str = "chunithm") -> dict[str, list[str]]:
    """Each layer's URLs, WebP first, then the older PNG."""
    if game == "chunithm":
        from .sources.chunithm import layer_urls

        return layer_urls(ref)
    sid, key = ref["id"], ref["key"]
    stems = {"bg": f"{SDVXIN}/prsk/bg/{sid}bg", "obj": f"{SDVXIN}/prsk/obj/data{sid}{key}",
             "bar": f"{SDVXIN}/prsk/bg/{sid}bar"}
    return {k: [v + ".webp", v + ".png"] for k, v in stems.items()}


def sdvxin_refs(db: ChartDB, fetcher: Fetcher, game: str) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    if game == "chunithm":
        return sdvxin_chunithm_refs(db, fetcher)
    if game == "sekai":
        return sdvxin_sekai_refs(db, fetcher)
    raise ValueError(f"sdvx.in has no pictures for {game}; choose from {SDVXIN_GAMES}")


def sdvxin_sekai_refs(db: ChartDB, fetcher: Fetcher) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """`song_id/slot` -> {"id", "key"} for every Project SEKAI chart of the database that sdvx.in has drawn. sdvx.in
    numbers songs its own way, so songs are matched by title, then artist."""
    from .games.registry import canonical_difficulty
    from .sources.chunithm import norm_title
    from .sources.fetch import NotFound

    from difflib import SequenceMatcher

    by_title: dict[str, list[dict[str, Any]]] = {}
    for name in ("songs0001.json", "songs_removed.json"):
        try:
            rows = json.loads(fetcher.get(f"{SDVXIN}/prsk/_/json/{name}").decode("utf-8-sig"))
        except (NotFound, ValueError):
            continue
        for row in rows:
            if row.get("title"):
                by_title.setdefault(norm_title(row["title"]).replace("−", ""), []).append(row)
    refs: dict[str, dict[str, str]] = {}
    stats: dict[str, Any] = {"songs": 0, "not_drawn": 0, "near_matches": [], "unmatched": []}
    for rec in db.iter_songs("sekai"):
        stats["songs"] += 1
        title = norm_title(SEKAI_TITLE_FIXES.get(rec.title, rec.title)).replace("−", "")
        cands = by_title.get(title, [])
        if len(cands) > 1:
            cands = [r for r in cands if norm_title(r.get("artist", "")) == norm_title(rec.artist)] or cands
        if not cands:       # sdvx.in sometimes shortens a title ("Peaky Peaky" -> "Peaky"): same artist, close title
            near = [(SequenceMatcher(None, title, t).ratio(), r) for t, rs in by_title.items() for r in rs
                    if norm_title(r.get("artist", "")) == norm_title(rec.artist)]
            near = [x for x in near if x[0] >= 0.6]
            if near:
                cands = [max(near, key=lambda x: x[0])[1]]
                stats["near_matches"].append(f"{rec.song_id} {rec.title} = {cands[0]['id']} {cands[0]['title']}")
        if not cands:
            stats["unmatched"].append(f"{rec.song_id} {rec.title}")
            continue
        row = cands[0]
        for key, name in SEKAI_KEYS.items():
            level = str((row.get("levels") or {}).get(key, ""))
            slot = canonical_difficulty("sekai", name)
            if not level or slot not in rec.charts:
                continue
            if level.startswith("_"):
                stats["not_drawn"] += 1
                continue
            refs[f"{rec.song_id}/{slot}"] = {"id": str(row["id"]), "key": key}
    return refs, stats


def _we_level(level: str) -> tuple[str, int]:
    """WORLD'S END level as (kanji, stars): otoge-db writes "招☆☆☆☆☆", sdvx.in "招5" ("_招5" when not drawn)."""
    level = level.lstrip("_")
    stars = level.count("☆") + level.count("★")
    digits = "".join(ch for ch in level if ch.isdigit())
    return level[:1], stars or int(digits or 0)


def sdvxin_chunithm_refs(db: ChartDB, fetcher: Fetcher) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """`song_id/slot` -> {"id", "key"} for every Chunithm chart of the database that sdvx.in has drawn.

    Regular songs: otoge-db's chart links (`source.sdvxin_legacy`, "07/07150exp") give sdvx.in's song id, and the
    song's row in sdvx.in's lists says which difficulties are drawn (a level starting with "_" is not). WORLD'S END
    charts have their own ids and are matched by title, then level."""
    from .games.registry import canonical_difficulty
    from .sources.chunithm import SDVXIN_KEYS, SdvxinChunithm, norm_title

    rows: dict[str, dict[str, Any]] = {}
    we_rows: dict[str, list[dict[str, Any]]] = {}
    for name, listed in SdvxinChunithm(fetcher).raw_lists():
        for row in listed:
            if "_we" in name:
                we_rows.setdefault(norm_title(row.get("title", "")), []).append(row)
            else:
                rows.setdefault(str(row["id"]), row)
    refs: dict[str, dict[str, str]] = {}
    stats = {"songs": 0, "no_sdvxin_id": 0, "unknown_id": 0, "not_drawn": 0, "we_unmatched": []}
    for rec in db.iter_songs("chunithm"):
        stats["songs"] += 1
        ids = {re.sub(r"[a-z]+$", "", cr.source["sdvxin_legacy"].rsplit("/", 1)[-1])
               for cr in rec.charts.values() if cr.source.get("sdvxin_legacy")}
        for sid in sorted(ids):
            row = rows.get(sid)
            if row is None:
                stats["unknown_id"] += 1
                continue
            levels = row.get("levels") or {}
            for key in SDVXIN_KEYS:
                level = str(levels.get(key, ""))
                slot = canonical_difficulty("chunithm", key)
                if not level or slot not in rec.charts:
                    continue
                if level.startswith("_"):
                    stats["not_drawn"] += 1
                    continue
                refs[f"{rec.song_id}/{slot}"] = {"id": sid, "key": key}
        we_slots = [s for s in rec.charts if s.startswith("special")]
        if not ids and not we_slots:
            stats["no_sdvxin_id"] += 1
        for s in we_slots:
            cands = we_rows.get(norm_title(rec.title), [])
            kanji, stars = _we_level(rec.charts[s].level)
            cands = ([r for r in cands if _we_level(str(r.get("levels", "")))[0] == kanji] or cands)
            if len(cands) > 1:
                cands = [r for r in cands if _we_level(str(r.get("levels", "")))[1] == stars] or cands
            if not cands:
                stats["we_unmatched"].append(f"{rec.song_id}/{s} {rec.title}")
                continue
            if str(cands[0].get("levels", "")).startswith("_"):
                stats["not_drawn"] += 1
                continue
            refs[f"{rec.song_id}/{s}"] = {"id": str(cands[0]["id"]), "key": "end"}
    return refs, stats


def sdvxin_merge(layers: dict[str, bytes]):
    """The viewer's stack of the layers (bg, obj, bar) as one RGBA picture; layers of other sizes sit at the top
    left, as in the viewer."""
    from io import BytesIO

    from PIL import Image

    ims = []
    for name in SDVXIN_LAYERS:
        if name in layers:
            with Image.open(BytesIO(layers[name])) as im:
                ims.append(im.convert("RGBA"))
    w, h = max(i.width for i in ims), max(i.height for i in ims)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for im in ims:
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.paste(im, (0, 0))
        out = Image.alpha_composite(out, layer)
    return out


def fetch_sdvxin(db: ChartDB, fetcher: Fetcher, out: str | Path = "data/pictures", only: set[str] | None = None,
                 slot: str | None = None, workers: int = 4, refresh: bool = False,
                 log: Callable[[str], None] | None = None, game: str = "chunithm") -> dict[str, Any]:
    """Download sdvx.in's layers of every drawn chart of one game of the database (Chunithm or Project SEKAI) into
    `<out>/<game>/<genre>/<song>/sdvxin/<slot>.{bg,obj,bar}.webp` (as published) plus `<slot>.webp` (merged, on
    black, WebP quality 90). The manifest is `<out>/<game>/manifest-sdvxin.json`. A chart whose merged picture is
    there is skipped unless `refresh` (its layers may have been removed locally after upload to save disk)."""
    from io import BytesIO

    from PIL import Image

    out = Path(out)
    manifest = load_manifest(out, game, "sdvxin")
    refs, stats = sdvxin_refs(db, fetcher, game)
    dirs = {rec.song_id: db.song_dir(rec).relative_to(db.root) for rec in db.iter_songs(game)}
    jobs = [(k, ref) for k, ref in sorted(refs.items())
            if (not only or k.split("/")[0] in only) and (not slot or k.split("/")[1] == slot)]
    summary: dict[str, Any] = {"charts_drawn": len(refs), "jobs": len(jobs), "downloaded": 0, "already": 0,
                               "failed": 0, **stats}

    def one(job: tuple[str, dict[str, str]]) -> tuple[str, dict[str, Any], str]:
        key, ref = job
        song_id, s = key.split("/")
        old = manifest.get(key)
        if old and not refresh and (out / game / old["file"]).exists():
            return key, old, "already"
        folder = out / dirs[song_id] / "sdvxin"
        folder.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {"id": ref["id"], "key": ref["key"],
                                 "viewer": sdvxin_viewer_url(ref["id"], ref["key"], game), "layers": {}}
        data: dict[str, bytes] = {}
        for name, urls in sdvxin_layer_urls(ref, game).items():
            errors = []
            for url in urls:
                try:
                    data[name] = fetcher.get(url, refresh=refresh)
                except FetchError as e:
                    errors.append(str(e))
                    continue
                dst = folder / f"{s}.{name}{_ext(url)}"
                if dst.exists():
                    dst.unlink()
                try:
                    os.link(fetcher.cache_path(url), dst)
                except OSError:
                    dst.write_bytes(data[name])
                entry["layers"][name] = {"url": url, "file": dst.relative_to(out / game).as_posix(),
                                         "bytes": len(data[name]), "sha256": _sha256(data[name])}
                break
            else:
                if name == "obj":           # without the notes there is no chart picture
                    return key, {"errors": errors}, "failed"
                entry.setdefault("missing_layers", []).append(name)
        merged = sdvxin_merge(data)
        entry["size"] = list(merged.size)
        sizes = {Image.open(BytesIO(b)).size for b in data.values()}
        if len(sizes) > 1:
            entry["layer_sizes_differ"] = sorted(list(x) for x in sizes)
        flat = Image.new("RGB", merged.size, (0, 0, 0))
        flat.paste(merged, mask=merged.getchannel("A"))
        buf = BytesIO()
        flat.save(buf, "WEBP", quality=SDVXIN_MERGED_QUALITY, method=4)
        dst = folder / f"{s}.webp"
        dst.write_bytes(buf.getvalue())
        entry.update({"file": dst.relative_to(out / game).as_posix(),
                      "merged": {"bytes": len(buf.getvalue()), "sha256": _sha256(buf.getvalue()),
                                 "made": f"layers stacked on black, WebP quality {SDVXIN_MERGED_QUALITY}"}})
        return key, entry, "downloaded"

    failed: dict[str, list[str]] = {}
    p = _manifest_path(out, game, "sdvxin")
    p.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        for i, (key, entry, status) in enumerate(pool.map(one, jobs), 1):
            summary[status] += 1
            if status == "failed":
                failed[key] = entry.get("errors", [])
            else:
                manifest[key] = entry
            if i % 100 == 0 or i == len(jobs):
                if log:
                    log(f"sdvx.in {game}: {i}/{len(jobs)} ({summary['downloaded']} new, {summary['failed']} failed)")
                p.write_text(json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=1) + "\n", "utf-8")
    p.write_text(json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=1) + "\n", "utf-8")
    summary["layer_bytes"] = sum(sum(x["bytes"] for x in e["layers"].values()) for e in manifest.values())
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

def sdvxin_reader_array(layer_files: dict[str, str | Path]):
    """sdvx.in's layers stacked as the reader expects them: RGBA, colours snapped to sdvx.in's palette.

    The layers are lossy WebP, and the reader (imaging/merged_chart.py) finds lines and notes by exact colours, so
    each pixel is moved to the nearest colour of a palette taken from a lossless sdvx.in picture
    (imaging/sdvxin_palette.json); transparency is kept."""
    import numpy as np
    from PIL import Image

    merged = sdvxin_merge({k: Path(v).read_bytes() for k, v in layer_files.items()})
    snapped = merged.convert("RGB").quantize(palette=_sdvxin_palette(), dither=Image.Dither.NONE).convert("RGBA")
    snapped.putalpha(merged.getchannel("A"))
    return np.asarray(snapped, dtype=np.uint8).copy()


_PALETTE: list[Any] = []


def _sdvxin_palette():
    from importlib.resources import files

    from PIL import Image

    if not _PALETTE:
        colours = json.loads(files("vsrgconv.imaging").joinpath("sdvxin_palette.json").read_text("utf-8"))["colours"]
        pal = Image.new("P", (1, 1))
        pal.putpalette([v for c in colours for v in c])
        _PALETTE.append(pal)
    return _PALETTE[0]


def _chunithm_compare(picture, chart) -> dict[str, Any]:
    """Note heads read from a merged sdvx.in picture (file bytes or an RGBA array) against the stored chart
    (imaging/merged_chart.py)."""
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
            slot: str | None = None, source: str | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Charts that have both a source picture and stored notes (`source`: which manifest, see load_manifest)."""
    pictures = Path(pictures)
    manifest = load_manifest(pictures, game, source)
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
        job = {"game": game, "key": key, "picture": str(pictures / game / e["file"]),
               "chart": str(db.chart_path(game, song_id, s)), "file": e["file"],
               "shared_with": e.get("shared_with", [])}
        if e.get("layers"):          # sdvx.in: read the layers as published, not the lossy merged picture
            job["layers"] = {k: str(pictures / game / v["file"]) for k, v in e["layers"].items()}
        jobs.append(job)
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
            layers = job.get("layers")
            if layers and all(Path(f).exists() for f in layers.values()):
                res.update(reader(sdvxin_reader_array(layers), chart))
            else:
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
