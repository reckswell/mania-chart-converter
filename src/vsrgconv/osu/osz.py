"""Packs `.osu` files with their audio and background into an `.osz` (a zip) that osu! imports by opening it."""
from __future__ import annotations

import zipfile
from pathlib import Path


def pack_osz(osu_files: list[Path], out: str | Path, extra_files: list[Path] | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in [*osu_files, *(extra_files or [])]:
            z.write(f, arcname=Path(f).name)
    return out
