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

## 2026-11-11: chart data sources and storage

- **Exact data over pictures.** Chunithm notes come from the game's own `.c2s` files (MarisaBot's copy on GitHub),
  parsed by `formats/c2s.py`. The sdvx.in picture reader is only for charts no `.c2s` covers (newest version).
  Evidence: `.c2s` combo totals match otoge-db's published note counts (44/44 sampled); the reader is uncalibrated.
- **Charts are stored gzipped** (`<slot>.chart.json.gz`, compact JSON, default fields left out, gzip mtime 0 so
  bytes are stable). A MASTER chart is ~100–350 KB as JSON but 5–20 KB gzipped; the full Chunithm set would be
  ~700 MB uncompressed.
- **Jackets are committed**, re-encoded to JPEG ≤512 px unless already a small JPEG (Chunithm's are kept as is).
  Full-size originals stay reachable through the recorded URL. Chart pictures (1–2 MB each) are not committed; their
  URLs are recorded on the chart (`source.picture`).
- **Removed songs are kept**, flagged `extra.removed: true`, in their original genre folder.
