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
    info = api.upload_folder(repo_id=repo_id, repo_type="dataset", folder_path=str(data_root),
                             allow_patterns=[f"{p}/**" for p in parts],
                             commit_message=f"vsrgconv publish: {', '.join(parts)}")
    summary["commit"] = str(getattr(info, "commit_url", info))
    if delete:          # after the upload, so a failed upload never leaves the dataset emptier than before
        root = Path(data_root)
        gone = [f for f in api.list_repo_files(repo_id, repo_type="dataset")
                if f.split("/", 1)[0] in parts and not (root / f).is_file()]
        for i in range(0, len(gone), 1000):
            api.delete_files(repo_id, gone[i:i + 1000], repo_type="dataset",
                             commit_message=f"vsrgconv publish: remove {len(gone[i:i + 1000])} files gone locally")
        summary["deleted"] = len(gone)
    return summary
