# Architecture

```
 sources/ (one adapter per game & site)        imaging/ (picture sources only)
 ┌───────────────────────────────┐            ┌──────────────────────────────┐
 │ OtogeDbChunithm   catalog     │            │ sdvxin_layout: columns→strip │
 │ SdvxinChunithm    catalog+charts ─────────►│ sdvxin_notes: pixels→notes   │
 │ (bandori, sekai, ddr … next)  │            │ sdvxin_chart: → Chart        │
 └──────────────┬────────────────┘            └──────────────┬───────────────┘
                │ SongRecord                                  │ Chart (IR)
                ▼                                             ▼
        ┌──────────────────────────────────────────────────────────┐
        │ db.ChartDB  data/db/<game>/<genre>/<id>-<slug>/          │
        │             song.json + <slot>.chart.json.gz             │
        └───────────────────────────┬──────────────────────────────┘
                                    │ Chart (IR, source lanes)
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │ convert/rules.py  Pipeline of Rules (profile "baseline") │
        │ convert/lint.py   ranking-criteria checks + metrics      │
        └───────────────────────────┬──────────────────────────────┘
                                    │ Chart (IR, K columns, tap/hold only)
                                    ▼
        ┌──────────────────────────────────────────────────────────┐
        │ osu/writer.py → .osu   osu/osz.py → .osz                 │
        │ playtest/orthomania.py → harness / lazer / play log      │
        └──────────────────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `model.py` | The intermediate format (IR): `Chart`, `Note`, `NoteKind`, `TimingMap`. Beats are `Fraction`s; ms only via `TimingMap.ms_at`. |
| `games/registry.py` | Per-game facts: lane count, native difficulties → canonical ladder, genre slugs. Games added by users load from definition files; `games/candidates.json` lists known VSRGs not added yet. |
| `db.py` | The on-disk database and its SQLite index. |
| `sources/fetch.py` | Polite cached HTTP. The cache mirrors URLs so files can be supplied offline. |
| `sources/base.py` | `SourceAdapter`: `list_songs()`, `fetch_chart(song, slot)`. |
| `sources/chunithm.py` | otoge-db and sdvx.in adapters, catalog merge. |
| `sources/local.py` | Folder sources: chart files on disk in any format with a reader, for added games. |
| `imaging/` | sdvx.in picture reader, the synthetic picture drawer for tests, and `render.py`, which draws any chart's layout from its notes. |
| `convert/rules.py` | `Rule`, `Pipeline`, profiles. Placeholder rules for now. |
| `convert/lint.py` | Hard checks (errors) and guidelines (warnings) from the mapping doc, plus metrics. |
| `convert/pipeline.py` | `convert(chart, keys, profile)` → `Conversion` (mania chart, lint, rules report, .osu text). |
| `osu/` | .osu writer and reader, .osz packer. |
| `playtest/orthomania.py` | Commands for orthomania's playtest harness, lazer import, play-log reader. |
| `cli.py` | `games`, `add-game`, `catalog`, `scrape`, `extract`, `render`, `convert`, `stats`, `calibrate-sdvxin`, `check-counts`, `demo`, `playtest`, `index`. |

## Adding a game

See `docs/adding-a-game.md`: `vsrgconv games --all` lists the added games and other known VSRGs, and
`vsrgconv add-game` writes a definition file (`data/games/<key>.json`) with a folder source (no code, for formats with a
reader) or an adapter stub. A game that is here to stay gets its `Game` in `games/registry.py` and its adapter in
`sources/<game>.py`, with fixtures, tests, `docs/sources/<game>.md` and eval-set entries in `agent/eval-set.json`.

Conversion rules per game come after, through the agent loop (`docs/agent-loop.md`).
