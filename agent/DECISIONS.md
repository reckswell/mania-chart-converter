# Decisions

Conversion and design decisions with the evidence behind them. Newest last. A rule decision records its A/B metrics.

## 2026-11-11: framework

- **Intermediate format keeps everything** (docs/research/vsrg-mechanics.md): beats as exact fractions, lanes in source
  units with widths, every note kind, scroll and stop events. Rules, not readers, decide what to drop.
- **Difficulty ladder** aligned at the top: each game's hardest regular chart is `master`. Chunithm BASIC/ADVANCED/
  EXPERT/MASTER/ULTIMA/WORLD'S END → normal/hard/expert/master/extra/special. osu! difficulty names keep the native
  name: `CHUNITHM MASTER 14+`.
- **Database is files**, game > genre > `<song_id>-<slug>` > `<slot>.chart.json`, committed. SQLite only as a
  rebuildable index.
- **Chunithm song ids are SEGA's** (from otoge-db). sdvx.in ids are attached as `source.sdvxin` by title match.
- **Placeholder rules** (`baseline` profile): snap → kinds (air/mine dropped, flick/ExTAP → tap, slide → hold) →
  slides flattened → centre-band lane mapping → min hold 1/12 → collision fix. Default 8K for Chunithm. None of
  this is a decision about how Chunithm *should* convert. It only makes the pipeline run end to end.
