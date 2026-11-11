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
        │             song.json + <slot>.chart.json                │
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
| `games/registry.py` | Per-game facts: lane count, native difficulties → canonical ladder, genre slugs. |
| `db.py` | The on-disk database and its SQLite index. |
| `sources/fetch.py` | Polite cached HTTP. The cache mirrors URLs so files can be supplied offline. |
| `sources/base.py` | `SourceAdapter`: `list_songs()`, `fetch_chart(song, slot)`. |
| `sources/chunithm.py` | otoge-db and sdvx.in adapters, catalog merge. |
| `imaging/` | sdvx.in picture reader and the synthetic picture drawer for tests. |
| `convert/rules.py` | `Rule`, `Pipeline`, profiles. Placeholder rules for now. |
| `convert/lint.py` | Hard checks (errors) and guidelines (warnings) from the mapping doc, plus metrics. |
| `convert/pipeline.py` | `convert(chart, keys, profile)` → `Conversion` (mania chart, lint, rules report, .osu text). |
| `osu/` | .osu writer and reader, .osz packer. |
| `playtest/orthomania.py` | Commands for orthomania's playtest harness, lazer import, play-log reader. |
| `cli.py` | `catalog`, `extract`, `convert`, `calibrate-sdvxin`, `check-counts`, `demo`, `playtest`, `index`. |

## Adding a game

1. Add its `Game` to `games/registry.py`: lanes, difficulty ladder mapping, genres.
2. Write `sources/<game>.py` with a `SourceAdapter` that yields `SongRecord`s and returns `Chart`s in source lane units,
   using every `NoteKind` that applies. Keep game-specific detail in `Note.extra`.
3. Add fixtures and tests: a small real chart file if the format is text, or synthetic data.
4. Wire a `catalog <game>` branch in `cli.py`.
5. Add eval-set entries in `agent/eval-set.json`.

Conversion rules per game come after, through the agent loop (`docs/agent-loop.md`).
