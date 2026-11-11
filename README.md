# mania-chart-converter (`vsrgconv`)

Converts charts from other vertical scrolling rhythm games into osu!mania maps. It aims for maps that play like
hand-made mania maps, not note-for-note copies. Personal use only.

The project currently has the **framework**: research docs, a lossless intermediate chart format, a chart database
(game > genre > song > difficulty), a Chunithm catalog and chart-picture reader, a placeholder conversion-rules
layer, a lint, a `.osu` writer, orthomania playtest hooks, and an agent loop that keeps improving all of it.

## Quick start

```bash
pip install -e '.[dev]'          # or: PYTHONPATH=src python -m vsrgconv ...
python -m pytest -q
python -m vsrgconv demo          # offline end to end: synthetic sdvx.in picture -> .osu in out/demo
python -m vsrgconv catalog chunithm            # songs from otoge-db (+ sdvx.in lists where reachable)
python -m vsrgconv extract chunithm 201 master # needs sdvx.in access (run on the PC); see docs/sources
python -m vsrgconv convert chunithm 201 master --keys 8
python -m vsrgconv playtest "out/<map>.osu" --dry-run   # orthomania harness command (ORTHO_REPO)
```

## Status

| Game | Catalog | Charts | Rules |
|---|---|---|---|
| Chunithm | ✓ 1,776 songs (otoge-db) in `data/db/chunithm` | reader built, tested on synthetic pictures; **not yet calibrated on real sdvx.in pictures** | placeholder |
| Sekai, Bandori, SDVX, DDR, PIU, Arcaea, IIDX, GH, GITADORA | registry entries | — | — |

## Docs

- `docs/research/osu-mania-mapping.md`: what a good mania map needs (timing, snapping, patterns, tiers, editor
  practice). The converter's yardstick.
- `docs/research/vsrg-mechanics.md`: each source game's mechanics, where its chart data comes from, and what does not
  map to mania. Includes the cross-game difficulty ladder.
- `docs/sources/chunithm-sdvxin.md`: the Chunithm data sources and the picture reader.
- `docs/architecture.md`: modules, and how to add a game.
- `docs/agent-loop.md`: the research → implement → verify → review → playtest → record loop (`/iterate`), with
  subagents in `.claude/agents/`.
- `agent/`: the loop's backlog, log, decisions, eval set and metrics.
