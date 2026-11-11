"""Mirror the database to a private Hugging Face dataset, so it is not only on one machine (docs/hosting.md).

`vsrgconv publish <user>/<dataset>` uploads `data/db`, `data/renders` and `data/games` to the dataset, keeping the
same paths (`db/chunithm/...`, `renders/chunithm/...`). The dataset is created private if it does not exist. The
token comes from $HF_TOKEN (or `hf auth login`). Uploads are incremental and resumable: files already on the Hub are
skipped, and an interrupted upload continues where it stopped when run again. `--delete` also removes files from the
dataset that are gone locally.

Needs `huggingface_hub` (`pip install -e '.[hosting]'`) and network access to huggingface.co.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

PARTS = ["db", "renders", "games"]


def plan(data_root: str | Path, parts: list[str]) -> dict[str, dict[str, int]]:
    """Files and bytes per part, as they are locally."""
    root = Path(data_root)
    out = {}
    for part in parts:
        files = [p for p in (root / part).rglob("*") if p.is_file()] if (root / part).is_dir() else []
        out[part] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
    return out


def publish_hf(repo_id: str, data_root: str | Path = "data", parts: list[str] | None = None, delete: bool = False,
               dry_run: bool = False, api: Any = None) -> dict[str, Any]:
    parts = parts or PARTS
    unknown = [p for p in parts if p not in PARTS]
    if unknown:
        raise ValueError(f"unknown parts {unknown}; choose from {PARTS}")
    summary: dict[str, Any] = {"repo": f"https://huggingface.co/datasets/{repo_id}", "parts": plan(data_root, parts)}
    if dry_run:
        return summary
    if api is None:
        try:
            from huggingface_hub import HfApi
        except ImportError:
            raise SystemExit("publish needs huggingface_hub: pip install -e '.[hosting]'") from None
        api = HfApi()
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    info = _resuming(api, repo_id, lambda: api.upload_folder(
        repo_id=repo_id, repo_type="dataset", folder_path=str(data_root), allow_patterns=[f"{p}/**" for p in parts],
        commit_message=f"vsrgconv publish: {', '.join(parts)}"))
    summary["commit"] = str(getattr(info, "commit_url", info))
    if delete:          # after the upload, so a failed upload never leaves the dataset emptier than before
        root = Path(data_root)
        gone = [f for f in api.list_repo_files(repo_id, repo_type="dataset")
                if f.split("/", 1)[0] in parts and not (root / f).is_file()]
        for i in range(0, len(gone), 1000):
            chunk = gone[i:i + 1000]
            _resuming(api, repo_id, lambda chunk=chunk: api.delete_files(
                repo_id, chunk, repo_type="dataset",
                commit_message=f"vsrgconv publish: remove {len(chunk)} files gone locally"))
        summary["deleted"] = len(gone)
    return summary


def _resuming(api: Any, repo_id: str, step: Callable[[], Any], stalls_allowed: int = 2, log=None) -> Any:
    """Run an upload step again after an error, as long as the dataset keeps moving forward.

    Through the cloud machine's proxy, Hugging Face's answer to a commit can take longer than the proxy waits, so the
    client gets a 502 or a timeout although the commit landed. upload_folder resumes where it stopped (committed files
    are skipped), so the step is re-run while each failed attempt still added a commit; it gives up after
    `stalls_allowed` attempts in a row that added nothing."""
    import sys

    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
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
