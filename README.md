# mania-chart-converter (`vsrgconv`)

Converts charts from other vertical scrolling rhythm games into osu!mania maps. It aims for maps that play like
hand-made mania maps, not note-for-note copies. Personal use only.

The project has the **framework** and a **chart database** of ten games:
- research docs;
- a lossless intermediate chart format;
- format parsers (Chunithm .c2s, Sekai SUS, Bestdori JSON, StepMania .sm/.ssc, Arcaea .aff, MIDI and .chart);
- a placeholder conversion-rules layer, a lint and a `.osu` writer;
- orthomania playtest hooks;
- an agent loop that keeps improving all of it.

## Quick start

```bash
pip install -e '.[dev]'          # or: PYTHONPATH=src python -m vsrgconv ...
python -m pytest -q
python -m vsrgconv demo          # offline end to end: synthetic sdvx.in picture -> .osu in out/demo
python -m vsrgconv stats         # what the database holds, per game
python -m vsrgconv games --all   # the games, and other VSRGs that can be added (docs/adding-a-game.md)
python -m vsrgconv catalog sekai                        # refresh a game's song list (or: catalog all)
python -m vsrgconv scrape sekai --charts --jackets --workers 8 --interval 0.02   # fetch what is missing
python -m vsrgconv render chunithm --workers 4           # chart layout pictures into data/renders
python -m vsrgconv convert chunithm 2490 master --keys 8
python -m vsrgconv playtest "out/<map>.osu" --dry-run   # orthomania harness command (ORTHO_REPO)
```

## Database

`data/db/<game>/<genre>/<song_id>-<slug>/` holds `song.json` (metadata for every chart), `<slot>.chart.json.gz` (the
notes in the intermediate format) and `assets/` (the jacket, at most 320 px). Slots use one ladder for every game
(easy, normal, hard, expert, master, extra, special). A second play style or instrument is a prefix, such as
`double-expert` or `drum-master`; Pump It Up uses `single-21`. Songs that were removed from a game are kept and
flagged `extra.removed`.

Counts as of 2026-11-11, from `vsrgconv stats`:

| Game | Songs | Genre folders | Charts with notes / listed | Jackets | Notes from | Missing |
|---|---|---|---|---|---|---|
| Chunithm | 1,922 (146 removed) | 8 in-game genres | 7,091 / 7,397 | 1,922 | the game's .c2s files | the 73 newest songs (pictures only) |
| Project SEKAI | 743 | 7 units | 3,821 / 3,853 | 737 | the game's SUS files | 6 songs not in any dump yet |
| BanG Dream! | 819 (13 removed) | 10 bands | 3,139 / 3,437 (1,356 lossy) | 812 | Bestdori JSON | EASY/NORMAL of the newest songs (bestdori.com, PC) |
| DDR WORLD | 1,480 | 20 versions | 9,962 / 11,129 | 1,480 | ZiV simfiles | 151 WORLD songs (ZiV, PC) |
| Pump It Up PHOENIX 2 | 969 (294 removed) | 6 categories | 5,984 / 7,567 | 688 | StepF2/P1 simfiles | 51 PHOENIX 2 songs, most half-doubles |
| Arcaea | 561 (1 removed) | 43 packs | 1,836 / 1,868 | 543 | the game's .aff files | 8 songs from 7.0/7.1 |
| Guitar Hero | 1,720 | 12 games | 5,006 / 15,065 | 531 | Harmonix MIDI, official rips | most DLC and GH Live |
| SOUND VOLTEX ∇ | 2,369 (49 removed) | 10 genres | 0 / 8,840 | 2,327 (+2,026 per chart) | — | all charts (pictures recorded; reader to build) |
| beatmania IIDX 34 | 2,623 (647 not in arcade) | 37 versions | 0 / 16,700 | none exist | — | all charts (textage, PC) |
| GITADORA GW DELTA | 1,820 (181 removed) | 34 versions | 0 / 19,497 | 1,593 | — | all charts (.dtx host, PC) |

Every chart with notes has a layout picture drawn from its notes: `vsrgconv render` writes
`data/renders/<game>/<genre>/<song>/<slot>.png`, the same layout as the database. Pictures are not committed; they
are redrawn from the charts (docs/hosting.md says where they can be hosted). Charts without notes keep links to
pictures elsewhere (sdvx.in, RRRGR, textage) in their `source`; `vsrgconv render --report` lists them.

Each game's sources, coverage and caveats are in `docs/sources/<game>.md`. Conversion rules are still placeholders
(`baseline` profile), so the .osu output is a working test, not a finished map.

## Docs

- `docs/research/osu-mania-mapping.md`: what a good mania map needs (timing, snapping, patterns, tiers, editor
  practice). The converter's yardstick.
- `docs/research/vsrg-mechanics.md`: each source game's mechanics, where its chart data comes from, and what does not
  map to mania. Includes the cross-game difficulty ladder.
- `docs/sources/`: per game, where the catalog, charts and jackets come from, the format, and what is missing.
- `docs/architecture.md`: modules.
- `docs/adding-a-game.md`: listing the games and adding another VSRG.
- `docs/hosting.md`: where the database can live besides this machine and GitHub.
- `docs/agent-loop.md`: the research → implement → verify → review → playtest → record loop (`/iterate`), with
  subagents in `.claude/agents/`.
- `agent/`: the loop's backlog, log, decisions, eval set and metrics.
