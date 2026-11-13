"""Keep the database current as games release songs and charts: `vsrgconv update` (docs/updating.md).

For each game:

1. **Check.** The files the game's catalog read last time (song lists, master data, ...) are downloaded again and
   compared by sha256 with `data/db/<game>/update.json`. If none changed, the game is up to date and its catalog is
   not rebuilt. A game with no state yet, or whose catalog reads no files (a folder source), is always rebuilt.
2. **Catalog.** The catalog is rebuilt from fresh downloads (every file once per run, whatever the cache holds) and
   compared with the database before it is merged in: songs the database does not have, charts new to a song, songs
   newly flagged removed, and songs whose record changed (levels, constants, ...).
3. **Fetch.** Notes and jackets for the new songs and charts, with the same code as `vsrgconv scrape`. A chart (or
   jacket) that a source was asked for but did not have yet is kept as pending in update.json and looked for again
   on every run for `retry_days` days after it was first seen: chart files often appear some days after a song is
   released (MarisaBot's .c2s files, simfile packs, mirrors). Charts no source knows (no file is even asked for)
   are not kept. The first run of a game looks for every chart without notes once, so charts that were waiting for a
   source before the updater existed become pending too.
4. **Pictures** (optional). Layout pictures (`vsrgconv render`) for the charts stored in this run, and source
   pictures (`vsrgconv pictures`) for the new charts, plus every chart sdvx.in has drawn since (Chunithm, Sekai). A
   machine without the picture folders reads the manifests from the Hugging Face dataset, so only new pictures are
   downloaded.
5. **Publish** (optional). The files this run changed go to the Hugging Face dataset (`vsrgconv publish`).

update.json and `data/db/CHANGELOG.md` (what each run added, newest first) are written only when something changed,
so a run that finds nothing leaves the git tree clean.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import IMAGE_MAX_SIZE, ChartDB, SongRecord
from .sources import GameSource
from .sources.fetch import Fetcher, FetchError, NotFound

STATE_FILE = "update.json"
CHANGELOG = "CHANGELOG.md"
JACKET = "jacket"           # pending key of a song jacket not found yet: "<song_id>/jacket" (no chart slot has
                            # this name)
RETRY_DAYS = 30
SAMPLE = 20                 # titles and keys shown per list in the report


class TrackingFetcher(Fetcher):
    """A Fetcher that records the sha256 of every URL it reads (None when the URL is missing), and with `fresh`
    downloads each URL again the first time it is asked for in this run, whatever the cache holds."""

    def __init__(self, base: Fetcher, fresh: bool = True) -> None:
        super().__init__(cache_dir=base.cache_dir, offline=base.offline, min_interval=base.min_interval,
                         user_agent=base.user_agent, timeout=base.timeout)
        self.fresh = fresh
        self.seen: dict[str, str | None] = {}       # everything read in this run
        self.used: dict[str, str | None] = {}       # read since the last `track()`
        self._track_lock = threading.Lock()

    def track(self) -> None:
        self.used = {}

    def _record(self, url: str, digest: str | None) -> None:
        with self._track_lock:
            self.seen[url] = self.used[url] = digest

    def get(self, url: str, refresh: bool = False) -> bytes:
        first = url not in self.seen
        try:
            data = super().get(url, refresh=refresh or (self.fresh and first and not self.offline))
        except NotFound:
            self._record(url, None)
            raise
        self._record(url, hashlib.sha256(data).hexdigest())
        return data


@dataclass
class UpdateState:
    """`data/db/<game>/update.json`."""

    feeds: dict[str, str | None] = field(default_factory=dict)     # catalog file URL -> sha256 (None: missing)
    pending: dict[str, str] = field(default_factory=dict)          # "song_id/slot" -> date first seen (ISO)
    catalog_changed: str = ""                                       # date the catalog last brought a change
    exists: bool = False

    @classmethod
    def load(cls, db: ChartDB, game: str) -> "UpdateState":
        p = db.root / game / STATE_FILE
        if not p.exists():
            return cls()
        d = json.loads(p.read_text("utf-8"))
        return cls(feeds=d.get("feeds", {}), pending=d.get("pending", {}),
                   catalog_changed=d.get("catalog_changed", ""), exists=True)

    def to_json(self, game: str) -> str:
        d = {"game": game, "catalog_changed": self.catalog_changed, "feeds": dict(sorted(self.feeds.items())),
             "pending": dict(sorted(self.pending.items()))}
        return json.dumps(d, ensure_ascii=False, indent=1) + "\n"

    def save(self, db: ChartDB, game: str) -> bool:
        """Write the state if it differs from the file; True when written."""
        p = db.root / game / STATE_FILE
        text = self.to_json(game)
        if p.exists() and p.read_text("utf-8") == text:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
        return True


@dataclass
class CatalogDiff:
    new_songs: list[SongRecord] = field(default_factory=list)
    new_charts: list[str] = field(default_factory=list)            # "song_id/slot" in songs already there
    removed: list[str] = field(default_factory=list)               # song ids newly flagged removed
    changed: list[str] = field(default_factory=list)               # song ids whose record changes on merge
    not_in_catalog: int = 0                                         # songs in the database the catalog no longer has

    @property
    def any(self) -> bool:
        return bool(self.new_songs or self.new_charts or self.removed or self.changed)


def compare(existing: dict[str, SongRecord], songs: list[SongRecord]) -> CatalogDiff:
    """What merging the catalog `songs` into the database (`existing`, by song id) would add or change."""
    diff = CatalogDiff()
    for rec in songs:
        old = existing.get(rec.song_id)
        if old is None:
            diff.new_songs.append(rec)
            continue
        diff.new_charts += [f"{rec.song_id}/{slot}" for slot in rec.charts if slot not in old.charts]
        if rec.extra.get("removed") and not old.extra.get("removed"):
            diff.removed.append(rec.song_id)
        before = old.to_dict()
        if SongRecord.from_dict(before).merge(rec).to_dict() != before:
            diff.changed.append(rec.song_id)
    diff.not_in_catalog = len(set(existing) - {s.song_id for s in songs})
    return diff


def check_feeds(fetcher: TrackingFetcher, feeds: dict[str, str | None]) -> tuple[list[str], list[str]]:
    """Download each feed again; returns (URLs whose content changed, errors)."""
    changed, errors = [], []
    for url, old in feeds.items():
        try:
            new: str | None = hashlib.sha256(fetcher.get(url)).hexdigest()
        except NotFound:
            new = None
        except FetchError as e:
            errors.append(str(e)[:200])
            continue
        if new != old:
            changed.append(url)
    return changed, errors


def _today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass
class UpdateOptions:
    charts: bool = True
    jackets: bool = True
    retry_days: int = RETRY_DAYS
    retry_all: bool = False
    force: bool = False
    check_only: bool = False
    render: bool = False
    pictures: bool = False
    renders_dir: Path = Path("data/renders")
    pictures_dir: Path = Path("data/pictures")
    hub: str | None = None                  # Hugging Face dataset to read picture manifests from
    workers: int = 1
    image_size: int = IMAGE_MAX_SIZE
    progress: bool = False
    today: date | None = None


def update_game(db: ChartDB, game: str, src: GameSource, base: Fetcher, opts: UpdateOptions,
                log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Run the update steps for one game; returns its report. Files written are listed in report["touched"]."""
    log = log or (lambda m: None)
    today = (opts.today or _today()).isoformat()
    state = UpdateState.load(db, game)
    fetcher = TrackingFetcher(base, fresh=True)
    report: dict[str, Any] = {"status": "", "first_run": not state.exists}
    touched: set[str] = set()       # files under the data root, relative to it ("db/chunithm/...")
    data_root = db.root.parent

    # 1. check
    if state.feeds and not opts.force:
        changed, errors = check_feeds(fetcher, state.feeds)
        report["feeds"] = {"checked": len(state.feeds), "changed": len(changed), "changed_sample": changed[:5]}
        if errors:
            report["feeds"]["errors"] = errors[:5]
        if len(errors) == len(state.feeds):
            report["status"] = "error: no catalog file could be read"
            return report
        rebuild = bool(changed)
    else:
        rebuild = True
    if opts.check_only:
        report["status"] = "would rebuild the catalog" if rebuild else "up to date"
        return report

    # 2. catalog
    existing = {s.song_id: s for s in db.iter_songs(game)}
    diff = CatalogDiff()
    if rebuild:
        log(f"update {game}: catalog")
        fetcher.track()
        try:
            songs, cat_report = src.catalog(fetcher)
        except Exception as e:      # one game's source going away must not stop the others
            report["status"] = f"error: catalog: {type(e).__name__}: {e}"[:300]
            return report
        diff = compare(existing, songs)
        moved: list[tuple[str, str, str]] = []
        if diff.any or not state.exists:
            for rec in diff.new_songs:
                rec.extra.setdefault("first_seen", today)
            db.upsert_songs(songs, moved=moved)
            touched.add(f"db/{game}/index.json")
        for _, old, new in moved:       # a genre change: the song's pictures follow its folder
            _move_pictures(opts, game, old, new)
        report["moved_songs"] = [f"{old} -> {new}" for _, old, new in moved][:SAMPLE]
        if diff.any:
            state.catalog_changed = today
        state.feeds = dict(fetcher.used)
        report["catalog"] = {k: v for k, v in cat_report.items() if not isinstance(v, (list, dict))}
    report["new_songs"] = len(diff.new_songs)
    report["new_charts"] = len(diff.new_charts) + sum(len(s.charts) for s in diff.new_songs)
    report["removed_songs"] = len(diff.removed)
    report["changed_songs"] = len(diff.changed)
    report["not_in_catalog"] = diff.not_in_catalog
    report["new_song_titles"] = [f"{s.song_id} {s.title}" for s in diff.new_songs[:SAMPLE]]
    report["new_chart_keys"] = diff.new_charts[:SAMPLE]

    # 3. fetch notes and jackets
    after = {s.song_id: s for s in db.iter_songs(game)} if rebuild else existing
    new_keys = {f"{s.song_id}/{slot}" for s in diff.new_songs for slot in s.charts} | set(diff.new_charts)
    wanted = _wanted(after, src, state, new_keys, {s.song_id for s in diff.new_songs}, opts, today)
    slots: dict[str, set[str]] = {}
    for key in wanted:
        sid, slot = key.split("/", 1)
        slots.setdefault(sid, set())
        if slot != JACKET:
            slots[sid].add(slot)
    saved_charts: list[str] = []
    saved_jackets: list[str] = []
    asked: set[str] = set()
    if slots and (opts.charts or opts.jackets):
        from .scrape import scrape

        log(f"update {game}: fetching {sum(len(v) for v in slots.values())} charts of {len(slots)} songs")
        res = scrape(db, game, src, base, [after[sid] for sid in sorted(slots) if sid in after],
                     charts=opts.charts, jackets=opts.jackets, workers=opts.workers, image_size=opts.image_size,
                     progress=opts.progress, slots=slots)
        saved_charts, saved_jackets, asked = res.saved_charts, res.saved_jackets, res.asked
        report["fetch"] = {**res.stats, "errors": res.errors[:10], "missing_sample": res.missing[:SAMPLE]}
    # pending: what a source was asked for and did not have yet
    now = {s.song_id: s for s in db.iter_songs(game)} if slots else after
    pending_before = dict(state.pending)
    state.pending, found = {}, set()
    for key in sorted(wanted):
        sid, slot = key.split("/", 1)
        rec = now.get(sid)
        if rec is None:
            continue
        cr = rec.charts.get(slot)
        if ("jacket" in rec.assets) if slot == JACKET else (cr is not None and
                                                           cr.status in ("extracted", "converted")):
            found.add(key)
        elif key in asked:
            state.pending[key] = pending_before.get(key, today)
    gave_up = [k for k in pending_before if k not in state.pending and k not in found]
    report["pending"] = len(state.pending)
    report["found_pending"] = len([k for k in pending_before if k in found])
    report["gave_up"] = len(gave_up)
    report["charts_saved"] = len(saved_charts)
    songs_written = {k.split("/", 1)[0] for k in saved_charts} | set(saved_jackets)

    # 4. pictures
    if opts.render and saved_charts:
        report["render"] = _render(db, game, saved_charts, opts, data_root, touched)
    if opts.pictures:
        report["pictures"] = _pictures(db, game, fetcher, new_keys, opts, data_root, touched, log)

    # state, files touched
    if state.save(db, game):
        touched.add(f"db/{game}/{STATE_FILE}")
    songs_written |= {s.song_id for s in diff.new_songs} | {k.split("/", 1)[0] for k in diff.new_charts}
    songs_written |= set(diff.changed) | set(diff.removed)
    for sid in songs_written:
        rec = now.get(sid)
        if rec is not None:
            folder = db.song_dir(rec)
            touched |= {p.relative_to(data_root).as_posix() for p in folder.rglob("*") if p.is_file()}
    report["status"] = ("updated" if diff.any or saved_charts else
                        "catalog rebuilt, nothing new" if rebuild else "up to date")
    report["touched"] = sorted(touched)
    report["_diff"] = diff
    report["_saved"] = saved_charts
    return report


def _wanted(songs: dict[str, SongRecord], src: GameSource, state: UpdateState, new_keys: set[str],
            new_song_ids: set[str], opts: UpdateOptions, today: str) -> set[str]:
    """Charts (and song jackets, `<id>/#jacket`) to look for in this run: the new ones, the pending ones still in
    their retry window, and on a game's first run (or with `retry_all`) every chart without notes."""
    first_run = not state.exists
    out: set[str] = set()
    if src.chart:
        out |= new_keys
    if src.jacket:
        out |= {f"{sid}/{JACKET}" for sid in new_song_ids if sid in songs and "jacket" not in songs[sid].assets}
    for key, first_seen in state.pending.items():
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(first_seen)).days
        except ValueError:
            age = 0
        if age <= opts.retry_days:
            out.add(key)
    if (first_run or opts.retry_all) and src.chart:
        out |= {f"{s.song_id}/{slot}" for s in songs.values() for slot, cr in s.charts.items()
                if cr.status not in ("extracted", "converted")}
    if not opts.charts:
        out = {k for k in out if k.endswith("/" + JACKET)}
    if not opts.jackets:
        out = {k for k in out if not k.endswith("/" + JACKET)}
    return {k for k in out if k.split("/", 1)[0] in songs}


def _move_pictures(opts: UpdateOptions, game: str, old: str, new: str) -> None:
    """Move a song's layout and source pictures after its folder moved (`old`, `new`: "<game>/<genre>/<song>"), and
    the paths in the picture manifests. Copies on the dataset stay at the old path until `publish --delete`."""
    for base in (opts.renders_dir, opts.pictures_dir):
        if (base / old).is_dir() and not (base / new).exists():
            (base / new).parent.mkdir(parents=True, exist_ok=True)
            (base / old).rename(base / new)
    old_rel, new_rel = old.split("/", 1)[1] + "/", new.split("/", 1)[1] + "/"
    for manifest in (opts.pictures_dir / game).glob("manifest*.json"):
        text = manifest.read_text("utf-8")
        entries = json.loads(text)
        for e in entries.values():
            for item in [e, *(e.get("layers") or {}).values()]:
                if isinstance(item.get("file"), str) and item["file"].startswith(old_rel):
                    item["file"] = new_rel + item["file"][len(old_rel):]
        out = json.dumps(dict(sorted(entries.items())), ensure_ascii=False, indent=1) + "\n"
        if out != text:
            manifest.write_text(out, "utf-8")


def _render(db: ChartDB, game: str, keys: list[str], opts: UpdateOptions, data_root: Path,
            touched: set[str]) -> dict[str, Any]:
    """Layout pictures for the charts stored in this run (the same pictures as `vsrgconv render`)."""
    from .cli import _render_one

    jobs = []
    for key in keys:
        sid, slot = key.split("/", 1)
        rec = db.get_song(game, sid)
        if rec is None:
            continue
        dst = opts.renders_dir / db.song_dir(rec).relative_to(db.root) / f"{slot}.png"
        jobs.append((str(db.chart_path(game, sid, slot)), str(dst), 1.0, 9.0, False))
    if opts.workers > 1 and len(jobs) > 1:
        from multiprocessing import Pool

        with Pool(opts.workers) as pool:
            results = list(pool.imap_unordered(_render_one, jobs, chunksize=4))
    else:
        results = [_render_one(j) for j in jobs]
    errors = [f"{dst}: {err}" for dst, _, err in results if err]
    for dst, _, err in results:
        if not err:
            _touch(Path(dst), data_root, touched)
    return {"rendered": len(results) - len(errors), "failed": len(errors), "errors": errors[:5]}


def _touch(path: Path, data_root: Path, touched: set[str]) -> None:
    try:
        touched.add(path.resolve().relative_to(data_root.resolve()).as_posix())
    except ValueError:      # a folder outside the data root is not published
        pass


def _pictures(db: ChartDB, game: str, fetcher: TrackingFetcher, new_keys: set[str], opts: UpdateOptions,
              data_root: Path, touched: set[str], log: Callable[[str], None]) -> dict[str, Any]:
    """Source pictures of the new charts, and sdvx.in's pictures of charts it drew since the last run."""
    from .pictures import SDVXIN_GAMES, fetch_pictures, fetch_sdvxin, load_manifest

    out: dict[str, Any] = {}
    plain = Fetcher(cache_dir=fetcher.cache_dir, offline=fetcher.offline, min_interval=fetcher.min_interval,
                    user_agent=fetcher.user_agent, timeout=fetcher.timeout)
    runs: list[tuple[str | None, Callable[[], dict[str, Any]]]] = []
    if new_keys:
        runs.append((None, lambda: fetch_pictures(db, plain, game, opts.pictures_dir, keys=new_keys,
                                                  workers=min(opts.workers, 8), keep_listed=True)))
    if game in SDVXIN_GAMES:
        runs.append(("sdvxin", lambda: fetch_sdvxin(db, fetcher, opts.pictures_dir, workers=min(opts.workers, 4),
                                                    game=game, keep_listed=True)))
    for source, run in runs:
        name = source or "db"
        manifest = opts.pictures_dir / game / (f"manifest-{source}.json" if source else "manifest.json")
        if not manifest.exists() and not _manifest_from_hub(opts.hub, manifest, data_root):
            out[name] = {"skipped": f"no {manifest.name} here or on the dataset: `vsrgconv pictures` fetches the "
                                    f"full set; give --publish/--hub to read the manifest from the dataset"}
            continue
        before = load_manifest(opts.pictures_dir, game, source)
        log(f"update {game}: {name} pictures")
        summary = run()
        after = load_manifest(opts.pictures_dir, game, source)
        added = [k for k, e in after.items() if before.get(k) != e]
        for k in added:
            for f in _entry_files(after[k]):
                _touch(opts.pictures_dir / game / f, data_root, touched)
        if added or not before:
            _touch(manifest, data_root, touched)
        out[name] = {"added": len(added), "failed": summary.get("failed", 0),
                     "failed_sample": list(summary.get("failed_charts", {}))[:5]}
    return out


def _entry_files(entry: dict[str, Any]) -> list[str]:
    files = [entry["file"]] if entry.get("file") else []
    return files + [x["file"] for x in (entry.get("layers") or {}).values() if x.get("file")]


def _manifest_from_hub(repo: str | None, manifest: Path, data_root: Path) -> bool:
    """Copy a picture manifest from the dataset (`pictures/<game>/manifest*.json`) when this machine has none."""
    if not repo:
        return False
    try:
        rel = manifest.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError:
        return False
    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.errors import EntryNotFoundError
    except ImportError:
        return False
    try:
        got = hf_hub_download(repo, rel, repo_type="dataset")
    except EntryNotFoundError:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("{}\n", "utf-8")     # the dataset has no pictures of this kind yet: start a manifest
        return True
    except Exception:
        return False
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(Path(got).read_bytes())
    return True


def write_changelog(db: ChartDB, reports: dict[str, dict[str, Any]], today: str) -> bool:
    """Add today's additions to `data/db/CHANGELOG.md`, newest first. Nothing is written when nothing was added."""
    from .games.registry import GAMES

    lines = []
    for game, r in sorted(reports.items()):
        diff: CatalogDiff | None = r.get("_diff")
        saved = r.get("_saved") or []
        if not diff or not (diff.any or saved):
            continue
        name = GAMES[game].name if game in GAMES else game
        bits = []
        if diff.new_songs:
            bits.append(f"{len(diff.new_songs)} new songs")
        if diff.new_charts:
            bits.append(f"{len(diff.new_charts)} new charts in songs already listed")
        if diff.removed:
            bits.append(f"{len(diff.removed)} songs now removed from the game")
        if diff.changed:
            bits.append(f"{len(diff.changed)} songs with changed details")
        if saved:
            bits.append(f"notes for {len(saved)} charts")
        lines.append(f"- **{name}**: {', '.join(bits)}.")
        if diff.new_songs:
            titles = [s.title for s in diff.new_songs[:SAMPLE]]
            more = len(diff.new_songs) - len(titles)
            lines.append(f"  New: {', '.join(titles)}{f' and {more} more' if more > 0 else ''}.")
    if not lines:
        return False
    p = db.root / CHANGELOG
    head = "# Database changes\n\nWritten by `vsrgconv update` (docs/updating.md), newest first.\n"
    old = p.read_text("utf-8") if p.exists() else head
    body = old[len(head):] if old.startswith(head) else "\n" + old
    p.write_text(f"{head}\n## {today}\n\n" + "\n".join(lines) + "\n" + body, "utf-8")
    return True


def update(db: ChartDB, games: list[str], sources: dict[str, GameSource], base: Fetcher, opts: UpdateOptions,
           publish: str | None = None, xet: bool = True, log: Callable[[str], None] | None = None
           ) -> dict[str, Any]:
    """Update each game in turn, then the changelog, then (with `publish`) the dataset."""
    reports: dict[str, dict[str, Any]] = {}
    for game in games:
        if game not in sources:
            reports[game] = {"status": "error: no source for this game"}
            continue
        try:
            reports[game] = update_game(db, game, sources[game], base, opts, log)
        except Exception as e:      # report it and carry on with the other games
            reports[game] = {"status": f"error: {type(e).__name__}: {e}"[:300]}
    out: dict[str, Any] = {"games": {}}
    today = (opts.today or _today()).isoformat()
    touched = sorted({t for r in reports.values() for t in r.get("touched", [])})
    if not opts.check_only and write_changelog(db, reports, today):
        touched.append(f"db/{CHANGELOG}")
    for game, r in reports.items():
        out["games"][game] = {k: v for k, v in r.items() if not k.startswith("_") and k != "touched"}
        out["games"][game]["files_written"] = len(r.get("touched", []))
    if publish and touched:
        from .publish import publish_hf

        out["publish"] = publish_hf(publish, db.root.parent, ["db", "renders", "pictures"], xet=xet, only=touched,
                                    log=log)
    out["files_written"] = len(touched)
    return out
