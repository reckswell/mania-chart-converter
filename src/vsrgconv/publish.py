"""Mirror the database to a private Hugging Face dataset, so it is not only on one machine (docs/hosting.md).

`vsrgconv publish <user>/<dataset>` uploads `data/db`, `data/renders`, `data/pictures` and `data/games` to the
dataset, keeping the same paths (`db/chunithm/...`, `renders/chunithm/...`); `--parts ab` adds the A/B comparison
pictures. The dataset is created private if it does not exist. The
token comes from $HF_TOKEN (or `hf auth login`). Uploads are incremental and resumable: files already on the Hub are
skipped, and an interrupted upload continues where it stopped when run again. `--delete` also removes files from the
dataset that are gone locally.

Needs `huggingface_hub` (`pip install -e '.[hosting]'`) and network access to huggingface.co.

Files go up in commits of at most `batch_files` files (`--batch-files`, default 1,000), and files whose content is
already on the Hub are skipped. upload_folder is not used: it re-sends a commit whose answer was lost, and through
the cloud machine's proxy, which gives up on an answer after 30 seconds, every large commit's answer is lost, so each
one landed two or three times. Here a lost answer is checked against the dataset's head instead.

`xet=False` (`--no-xet`) uploads through Git LFS instead of Hugging Face's Xet storage. It works when *.hf.co is
unreachable, or when a network secret adds the Hugging Face token to *.hf.co requests (which replaces Xet's own token:
401), but Hugging Face rate-limits it to about 1,000 files per 5 minutes.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import time
from collections.abc import Callable
from typing import Any

PARTS = ["db", "renders", "pictures", "games", "ab"]
DEFAULT_PARTS = ["db", "renders", "pictures", "games"]       # `ab` (comparison pictures) only when asked


def plan(data_root: str | Path, parts: list[str]) -> dict[str, dict[str, int]]:
    """Files and bytes per part, as they are locally."""
    root = Path(data_root)
    out = {}
    for part in parts:
        files = [p for p in (root / part).rglob("*") if p.is_file()] if (root / part).is_dir() else []
        out[part] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
    return out


def publish_hf(repo_id: str, data_root: str | Path = "data", parts: list[str] | None = None, delete: bool = False,
               dry_run: bool = False, api: Any = None, xet: bool = True, batch_files: int = 1000,
               log: Callable[[str], None] | None = None, only: list[str] | None = None) -> dict[str, Any]:
    """Upload the parts' files that differ from the Hub. `only` (paths relative to `data_root`, e.g.
    "db/chunithm/index.json") limits the upload to those files and asks the Hub about those alone, instead of
    listing the whole dataset: `vsrgconv update` passes the files it changed."""
    parts = parts or DEFAULT_PARTS
    unknown = [p for p in parts if p not in PARTS]
    if unknown:
        raise ValueError(f"unknown parts {unknown}; choose from {PARTS}")
    summary: dict[str, Any] = {"repo": f"https://huggingface.co/datasets/{repo_id}",
                               "parts": plan(data_root, parts) if only is None else {"files": len(only)}}
    if dry_run:
        return summary
    log = log or _stderr
    if api is None:
        if not xet:
            os.environ["HF_HUB_DISABLE_XET"] = "1"      # read when huggingface_hub is imported
        try:
            from huggingface_hub import HfApi
        except ImportError:
            raise SystemExit("publish needs huggingface_hub: pip install -e '.[hosting]'") from None
        api = HfApi()
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    summary.update(_upload_in_batches(api, repo_id, Path(data_root), parts, batch_files, log, only))
    if delete:          # after the upload, so a failed upload never leaves the dataset emptier than before
        root = Path(data_root)
        gone = [f for f in api.list_repo_files(repo_id, repo_type="dataset")
                if f.split("/", 1)[0] in parts and not (root / f).is_file()]
        for i in range(0, len(gone), 1000):
            chunk = gone[i:i + 1000]
            _resuming(api, repo_id, lambda chunk=chunk: api.delete_files(
                repo_id, chunk, repo_type="dataset",
                commit_message=f"vsrgconv publish: remove {len(chunk)} files gone locally"), log=log)
        summary["deleted"] = len(gone)
    return summary


def move_folders(repo_id: str, moves: list[tuple[str, str]], api: Any = None,
                 log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Copy every file under each old folder of the dataset to the new folder, on the Hub (no download; pictures are
    stored as LFS/Xet files, which the Hub can copy). Returns the files copied and the old folders that were copied
    whole, which the caller then removes with `delete_folders`."""
    from huggingface_hub import CommitOperationCopy

    log = log or _stderr
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    done: list[str] = []
    files = 0
    for old, new in moves:
        try:
            paths = [e.path for e in api.list_repo_tree(repo_id, path_in_repo=old.rstrip("/"), repo_type="dataset",
                                                        recursive=True) if hasattr(e, "size")]
        except Exception:       # nothing there
            continue
        ops = [CommitOperationCopy(src_path_in_repo=p, path_in_repo=new + p[len(old):]) for p in paths]
        try:
            for i in range(0, len(ops), 1000):
                _commit_landing(api, repo_id, ops[i:i + 1000], f"vsrgconv update: move {old} to {new}", log)
        except Exception as e:  # keep the old folder when the copy did not land
            log(f"publish: could not move {old}: {type(e).__name__}: {str(e)[:160]}")
            continue
        files += len(paths)
        done.append(old)
    return {"files": files, "folders": done}


def delete_folders(repo_id: str, folders: list[str], api: Any = None,
                   log: Callable[[str], None] | None = None) -> int:
    """Remove every file under these folders of the dataset ("db/sdvx/unknown/2264-x/"), in one commit per 1,000
    files; returns how many were removed. `vsrgconv update` uses it for the old folders of songs that moved."""
    log = log or _stderr
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    gone: list[str] = []
    for folder in folders:
        try:
            gone += [e.path for e in api.list_repo_tree(repo_id, path_in_repo=folder.rstrip("/"), repo_type="dataset",
                                                        recursive=True) if hasattr(e, "size")]
        except Exception:       # not on the dataset (never uploaded): nothing to remove
            continue
    for i in range(0, len(gone), 1000):
        chunk = gone[i:i + 1000]
        _resuming(api, repo_id, lambda chunk=chunk: api.delete_files(
            repo_id, chunk, repo_type="dataset",
            commit_message=f"vsrgconv update: remove {len(chunk)} files of songs that moved"), log=log)
    return len(gone)


def _stderr(msg: str) -> None:
    import sys

    print(msg, file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git_blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _on_hub(path: Path, entry: Any) -> bool:
    """Whether the Hub's file (a RepoFile from list_repo_tree) has the local file's content."""
    if entry is None or getattr(entry, "size", None) != path.stat().st_size:
        return False
    lfs = getattr(entry, "lfs", None)
    if lfs is not None:
        return _sha256(path) == lfs.sha256
    return _git_blob_id(path) == getattr(entry, "blob_id", None)


def _upload_in_batches(api: Any, repo_id: str, root: Path, parts: list[str], batch_files: int,
                       log: Callable[[str], None], only: list[str] | None = None) -> dict[str, Any]:
    """Upload a commit per `batch_files` files, skipping files already on the Hub. Running it again after an
    interruption carries on: committed batches are on the Hub and are skipped."""
    from huggingface_hub import CommitOperationAdd

    if only is not None:
        local = sorted({root / rel for rel in only
                        if rel.split("/", 1)[0] in parts and (root / rel).is_file()})
        rels = [p.relative_to(root).as_posix() for p in local]
        remote = {}
        for i in range(0, len(rels), 500):
            remote.update({e.path: e for e in api.get_paths_info(repo_id, rels[i:i + 500], repo_type="dataset")
                           if hasattr(e, "size")})
    else:
        remote = {e.path: e for e in api.list_repo_tree(repo_id, repo_type="dataset", recursive=True)
                  if e.path.split("/", 1)[0] in parts and hasattr(e, "size")}
        local = sorted(p for part in parts if (root / part).is_dir() for p in (root / part).rglob("*")
                       if p.is_file())
    todo = [p for p in local if not _on_hub(p, remote.get(p.relative_to(root).as_posix()))]
    log(f"publish: {len(local)} files, {len(local) - len(todo)} already on the Hub, {len(todo)} to upload")
    commits = []
    for i in range(0, len(todo), batch_files):
        batch = todo[i:i + batch_files]
        ops = [CommitOperationAdd(path_in_repo=p.relative_to(root).as_posix(), path_or_fileobj=str(p)) for p in batch]
        msg = f"vsrgconv publish: {', '.join(parts)} ({i + len(batch)}/{len(todo)})"
        commits.append(_commit_landing(api, repo_id, ops, msg, log))
        log(f"publish: {i + len(batch)}/{len(todo)} files committed")
    return {"uploaded": len(todo), "skipped": len(local) - len(todo), "commits": len(commits)}


def _commit_landing(api: Any, repo_id: str, ops: list[Any], message: str, log: Callable[[str], None],
                    tries: int = 4, wait_s: float = 90.0, poll_s: float = 5.0) -> str:
    """create_commit, treating a lost answer as success when the commit lands anyway (the proxy gives up after 30 s
    while Hugging Face is still committing). Files already uploaded are not sent again on a retry."""
    head = _head(api, repo_id)
    for attempt in range(1, tries + 1):
        try:
            info = api.create_commit(repo_id, operations=ops, commit_message=message, repo_type="dataset",
                                     num_threads=16)
            return str(getattr(info, "oid", info))
        except Exception as e:
            deadline = time.monotonic() + wait_s
            while True:
                now = _head(api, repo_id)
                if now is not None and now != head:
                    return str(now)
                if time.monotonic() >= deadline:
                    break
                time.sleep(poll_s)
            if attempt == tries:
                raise
            log(f"publish: {type(e).__name__}: {str(e)[:160]}; nothing landed, try {attempt + 1} of {tries}")
    raise AssertionError("unreachable")


def _resuming(api: Any, repo_id: str, step: Callable[[], Any], stalls_allowed: int = 2, log=None) -> Any:
    """Run an upload step again after an error, as long as the dataset keeps moving forward.

    Through the cloud machine's proxy, Hugging Face's answer to a commit can take longer than the proxy waits, so the
    client gets a 502 or a timeout although the commit landed. upload_folder resumes where it stopped (committed files
    are skipped), so the step is re-run while each failed attempt still added a commit; it gives up after
    `stalls_allowed` attempts in a row that added nothing."""
    log = log or _stderr
    head = _head(api, repo_id)
    stalls = 0
    while True:
        try:
            return step()
        except Exception as e:      # HfHubHTTPError, ConnectionError, timeouts: all look the same from here
            now = _head(api, repo_id)
            stalls = 0 if now != head else stalls + 1
            if stalls > stalls_allowed:
                raise
            log(f"publish: {type(e).__name__}: {str(e)[:160]}; dataset at {str(now)[:10]}, resuming")
            head = now


def _head(api: Any, repo_id: str) -> str | None:
    try:
        return api.repo_info(repo_id, repo_type="dataset").sha
    except Exception:
        return None
