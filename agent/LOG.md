# Loop log

`date | item | result | metrics delta | next`

2026-11-11 | framework bootstrap | done | baseline recorded (demo: 0 lint errors, 0 count mismatches) | waiting for PC: sdvx.in fetch + reader calibration (BACKLOG 1)
2026-11-11 | Chunithm exact charts (.c2s from MarisaBot) + jackets + removed songs | 7,091/7,397 charts extracted, 1,922/1,922 jackets; 97 charts combo ≠ published count (1–10 notes, likely revised charts) | BACKLOG 0: the 73 newest songs need the picture reader (punipu2 merged PNGs)
2026-11-11 | Bandori: catalog (esterTion master) + Bestdori charts (MaaBanGDream mirror; kanon511 lossy EASY/NORMAL) + jackets | 819 songs, 3,139/3,437 charts (1,356 lossy), 812 jackets; every chart matches Bestdori combo | PC: `scrape bandori --charts --jackets` (bestdori.com fills 298 charts + 7 jackets), then `--refresh` to replace the lossy copies, then `catalog bandori` for new BPMs
2026-11-11 | DDR WORLD: catalog (DDRCardDraw + Tachi) + StepMania .sm/.ssc parser + simfiles (ddr-dan-tool) + jackets | 1,480 songs, 9,962/11,129 charts (99.84% match published combo), 1,480 jackets; TimingMap gained delays and warps | PC (optional): ZiV DDR WORLD pack for the 151 songs with no simfile (adapter needs a local-folder mode)
