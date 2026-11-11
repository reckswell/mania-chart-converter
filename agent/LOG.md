# Loop log

`date | item | result | metrics delta | next`

2026-11-11 | framework bootstrap | done | baseline recorded (demo: 0 lint errors, 0 count mismatches) | waiting for PC: sdvx.in fetch + reader calibration (BACKLOG 1)
2026-11-11 | Chunithm exact charts (.c2s from MarisaBot) + jackets + removed songs | 7,091/7,397 charts extracted, 1,922/1,922 jackets; 97 charts combo ≠ published count (1–10 notes, likely revised charts) | BACKLOG 0: the 73 newest songs need the picture reader (punipu2 merged PNGs)
2026-11-11 | Bandori: catalog (esterTion master) + Bestdori charts (MaaBanGDream mirror; kanon511 lossy EASY/NORMAL) + jackets | 819 songs, 3,139/3,437 charts (1,356 lossy), 812 jackets; every chart matches Bestdori combo | PC: `scrape bandori --charts --jackets` (bestdori.com fills 298 charts + 7 jackets), then `--refresh` to replace the lossy copies, then `catalog bandori` for new BPMs
2026-11-11 | DDR WORLD: catalog (DDRCardDraw + Tachi) + StepMania .sm/.ssc parser + simfiles (ddr-dan-tool) + jackets | 1,480 songs, 9,962/11,129 charts (99.84% match published combo), 1,480 jackets; TimingMap gained delays and warps | PC (optional): ZiV DDR WORLD pack for the 151 songs with no simfile (adapter needs a local-folder mode)
2026-11-11 | SDVX / IIDX / GITADORA catalogs + jackets (no reachable chart data) | SDVX 2,369 songs / 8,840 charts (= Tachi ∇ set), 2,327 jackets + 2,026 per-difficulty; IIDX 2,623 songs / 16,700 charts (SP + DP), no jackets exist; GITADORA 1,820 songs / 19,497 charts, 1,593 jackets | PC: textage reader (URLs in source.textage), GITADORA .dtx fetch + reader (URLs recorded), SDVX column-picture reader (RRRGR URLs in source.picture), full-size GITADORA jackets (source.jacket_full)
2026-11-11 | Sekai: catalog (sekai-master-db-diff + BPMs) + SUS parser + charts (pjsekai-decrypted-data, Sekai-Chart-Json fallback) + jackets | 743 songs, 3,821/3,853 charts, 737 jackets; combo = totalNoteCount on 3,821/3,821 | re-run `scrape sekai` after the dumps add 550/652/680/748/810/826 and the APPENDs of 22 and 136; rule work: EASY/NORMAL leave columns empty at 6K, #TIL teleport bursts need a hi-speed rule
2026-11-11 | Arcaea: songlist (APK) + toolbelt/Mattyakyou counts and constants + official .aff charts (HarumiEna) + jackets | 561 songs, 1,836/1,868 charts, combo matches published on 1,808/1,833; 543 jackets | 8 songs from 7.0/7.1 have no .aff anywhere reachable
2026-11-11 | Guitar Hero: catalog of 12 mainline games (score-shots + Neversoft/Harmonix lists) + MIDI/.chart parser + official rips (GH2 Deluxe MIDI, Clone Hero rips by official charters) + album art | 1,720 songs, 5,006/15,065 charts, 531 jackets | the rest needs chorus/enchor full rips (not reachable from the cloud); GH Live almost entirely missing
2026-11-11 | BACKLOG 0: merged-picture reader (imaging/merged_chart.py) | blocked: note starts 83% tap / 39% slide on a 34-chart calibration set, no air/holds/slides yet, no BPM text; 32 of the 80 picture URLs on listed charts point at duplicate files | next: trace hold/slide bodies, read air, refuse charts whose counts differ from published (docs/sources/chunithm-pictures.md)
2026-11-11 | Pump It Up PHOENIX 2: pumpout DB catalog (live + removed) + StepF2/P1 simfiles (rayden) + jackets; StepMania parser now reads delays, warps, speeds and co-op | 969 songs (675 live), 4,332/5,637 live charts, 43 fan-transcribed charts flagged, 688 jackets | PC (optional): a StepF2 PHOENIX 2 pack would fill 51 songs + 650 half-double charts (`build-manifest --paths`)

## 2026-11-11: layout pictures, hosting, other VSRGs

- `vsrgconv render` drew a layout picture for all 36,839 charts with notes (842 MiB in data/renders, not committed;
  every picture checked to open). Gimmick charts (Arcaea teleports, PIU warps) are drawn on a time axis.
- 24 Chunithm charts (12 songs) had `MET` lines with a zero time signature; the .c2s reader now ignores them, and
  the 12 songs were re-extracted.
- 58,514 listed charts have no notes yet, so no picture. 44,903 of them carry a link: SDVX and IIDX to pictures or
  score pages, GITADORA to .dtx files (waiting for the PC: the hosts are blocked here), Chunithm to merged pictures.
- docs/hosting.md: the repository is public; recommended private repo plus a private Hugging Face dataset for the
  database and pictures (`vsrgconv publish`). Waiting for Alpha's choice, an HF token and network access to
  huggingface.co.
- `vsrgconv games --all` / `add-game`: other VSRGs (docs/adding-a-game.md).
