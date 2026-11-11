"""Eval-set metrics for the agent loop's gates (docs/agent-loop.md).

    python scripts/metrics.py                    # print metrics for agent/eval-set.json
    python scripts/metrics.py --compare          # also diff against the last recorded entry
    python scripts/metrics.py --record "note"    # append to agent/metrics.jsonl
    python scripts/metrics.py --profile NAME     # use another rules profile
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vsrgconv.convert.pipeline import convert  # noqa: E402
from vsrgconv.db import ChartDB  # noqa: E402
from vsrgconv.imaging.sdvxin_chart import count_mismatches  # noqa: E402


def demo_chart():
    import tempfile

    from vsrgconv.cli import main

    import contextlib
    import io

    tmp = Path(tempfile.mkdtemp())
    with contextlib.redirect_stdout(io.StringIO()):
            main(["--db", str(tmp / "db"), "demo", "--out", str(tmp / "out")])
    return ChartDB(tmp / "db").load_chart("chunithm", "demo", "master")


def measure(profile: str) -> dict:
    spec = json.loads((ROOT / "agent" / "eval-set.json").read_text("utf-8"))
    db = ChartDB(ROOT / spec.get("db", "data/db"))
    out: dict[str, dict] = {}
    for item in spec["charts"]:
        key = f"{item['game']}/{item['song_id']}/{item['slot']}"
        try:
            chart = demo_chart() if item["song_id"] == "demo" else db.load_chart(item["game"], item["song_id"],
                                                                                 item["slot"])
        except (KeyError, FileNotFoundError) as e:
            out[key] = {"missing": str(e)}
            continue
        conv = convert(chart, keys=item.get("keys"), profile=profile)
        out[key] = {
            "lint_errors": len(conv.lint.errors),
            "lint_warnings": len(conv.lint.warnings),
            "count_mismatches": len(count_mismatches(chart)),
            "dropped": sum(sum(r.get("counts", {}).values()) for r in conv.report if r["rule"] == "kinds"),
            "moved_by_collisions": sum(r.get("moved", 0) for r in conv.report if r["rule"] == "collisions"),
            **{k: v for k, v in conv.lint.metrics.items() if k in ("notes", "holds", "jacks_half_beat",
                                                                   "left_hand_share")},
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="baseline")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--record", metavar="NOTE")
    a = ap.parse_args()
    now = measure(a.profile)
    print(json.dumps(now, indent=1))
    log = ROOT / "agent" / "metrics.jsonl"
    if a.compare and log.exists() and log.read_text().strip():
        last = json.loads(log.read_text().strip().splitlines()[-1])["charts"]
        for key, m in now.items():
            for k, v in m.items():
                old = last.get(key, {}).get(k)
                if isinstance(v, (int, float)) and isinstance(old, (int, float)) and v != old:
                    print(f"{key} {k}: {old} -> {v}")
    if a.record:
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "profile": a.profile, "note": a.record, "charts": now}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
