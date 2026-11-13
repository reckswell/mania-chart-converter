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

## 2026-11-12: pictures in sdvx.in's layout at AR 9, Hugging Face

- Layout pictures redrawn for all 36,839 charts in sdvx.in's look (black, gold rails, measure numbers by each column),
  spaced as at osu! approach rate 9 (0.8 px per ms) instead of 24 px per beat; 2.0 GiB, every picture checked to
  open. Stops and delays over 2 s are drawn 2 s long (Pump It Up's 999-second end stops made hundreds of empty
  columns). `render --approach-rate N` / `--compact` for other spacing.
- Hugging Face dataset `Reckswell/vsrgs` (private) holds the whole database and every picture: 101,408 files
  (db 64,569, renders 36,839), checked file by file (sizes; hashes on a 3,000-file sample). Network: one secret on
  `huggingface.co`, `*.hf.co` allowed with no secret. `publish` makes its own 1,000-file commits (upload_folder
  re-sent commits whose answer the proxy dropped), about 1,500 files a minute; `--no-xet` is the slow LFS fallback.

## 2026-11-12: source pictures ("true source") and A/B

- `vsrgconv pictures` keeps each chart's published picture in data/pictures (not committed; on the HF dataset):
  Chunithm 1,899 (punipu2 merged sdvx.in MASTER/ULTIMA), SDVX 7,948 (RRRGR). `vsrgconv ab` compares them with the
  stored notes: Chunithm 1,783 charts, median 79% of notes read back from the picture; limited by the picture reader
  (docs/sources/pictures.md).
- Waiting for Alpha: `sdvx.in`, `sdvxindex.com`, `textage.cc` under Allowed domains, or a PC run, for Chunithm
  EXPERT/WORLD'S END and full-size pictures, Sekai, the 667 sdvxindex-only SDVX charts and IIDX.

## 2026-11-12 (evening): sdvx.in pictures as the true source

- `vsrgconv pictures <chunithm|sekai> --from sdvxin` keeps sdvx.in's three layers per chart exactly as published,
  plus a merged WebP for viewing (docs/sources/pictures.md). Chunithm: 3,853 of 3,854 drawn charts (16.5 GB of
  layers); Project SEKAI: 1,631 of 1,633 (1.5 GB). The missing three are listed on sdvx.in but answer 302.
- `vsrgconv ab chunithm --from sdvxin`: stacked layers snapped to a lossless sdvx.in palette, then read with
  merged_chart.py. 3,681 charts compared, median 81% of stored notes read back (73% of 3.09 million notes);
  the same as punipu2's lossless pictures on the charts both have, so the reader is still the limit.
- Chunithm went to Hugging Face in batches of about 300 (download, A/B, upload, sha256 check, delete the local notes
  layer) to fit the disk; all 15,412 files and 3,691 comparison pictures checked on the Hub.
- Fixed: the cache wrote files in place, so a thread could read a layer shared by two charts while another thread
  was still writing it (SEKAI stopped at 1,300); cache files are now renamed into place whole.
- Waiting: the SEKAI pictures, its 1,619 comparison pictures and `ab/chunithm/report-sdvxin.json` are not uploaded.
  The environment this session ran in lost its Hugging Face secret when Alpha set up the new `vsrgs` environment;
  they were handed to a session on `vsrgs` (project files, `handoff/sekai-sdvxin.tar`). sdvxindexasset.com (667 SDVX
  charts) is allowed on `vsrgs` only. bilibili and niconico need `*.bilibili.com` and `*.nicovideo.jp`.

## 2026-11-12 (night): SEKAI pictures on Hugging Face, SDVX from sdvxindexasset.com

- The SEKAI handoff archive (3 GB) never reached the project folder (only its .sha256 did), so the `vsrgs` session
  downloaded sdvx.in's SEKAI layers again (1,631 of 1,633, the same two EXPERTs missing) and redrew the 1,619
  comparison pictures. Published with `ab/chunithm/report-sdvxin.json` and the dataset README; all 8,148 files and
  the 6,524 hashes in `manifest-sdvxin.json` match the Hub.
- sdvxindex.com's column pictures are now same-size PNG layers on sdvxindexasset.com
  (`/img/<dir>/<stem>_xl_columns/<layer>.png`). `vsrgconv pictures` rewrites the old URLs, keeps the eight default
  layers as published (`<song>/sdvxindex/`) and stacks them on the old grey as `<slot>.png` (96% of pixels identical
  to RRRGR's copy of an old picture). All 716 SDVX charts without a picture (667 only on sdvxindex.com, 49 missing on
  RRRGR) fetched and published; the SDVX picture set is complete at 8,664 charts, every hash checked on the Hub.
- Network on `vsrgs`: sdvxindexasset.com, www.bilibili.com, api.bilibili.com, search.bilibili.com and
  www.nicovideo.jp answer.
- Next: `catalog sdvx` from sdvxindex.com's own list (1.4.9) for about 81 newer charts; a reader for SEKAI's sdvx.in
  pictures; IIDX pictures need browser screenshots.


## 2026-11-13: `vsrgconv update` (docs/updating.md)

- New command `vsrgconv update [game ...]`: re-downloads the files each catalog read last time and compares their
  sha256 with `data/db/<game>/update.json`; rebuilds only changed catalogs, reports new songs, new charts, removed
  and changed songs, fetches notes and jackets for the new ones only, keeps charts a source did not have yet pending
  for 30 days, draws layout pictures (`--render`) and sdvx.in/sdvxindex pictures (`--pictures`) of what is new, and
  with `--publish` uploads only the files it wrote. Writes `data/db/CHANGELOG.md`. A daily GitHub Actions workflow
  (`.github/workflows/update-database.yml`) runs it from `main` once merged; it needs the `HF_TOKEN` secret to publish.
- Song folders keep their name when a later catalog spells the title differently (`extra.folder`): re-cataloguing
  Chunithm would have renamed 1,608 of 1,922 folders and orphaned their pictures on the dataset. A genre change still
  moves a folder; `update` moves its pictures (locally, or on the Hub with copy + delete) and rewrites the manifests.
- SDVX's catalog now reads sdvxindex.com's own newest list (the script bundle names `songsv<ver>.json`); the GitHub
  1.4.2 copy is the fallback. Chunithm charts with no `.c2s` are not read from sdvx.in pictures until the reader is
  calibrated (`SDVXIN_READER_READY`), since an extracted chart would never be replaced by the exact `.c2s`.
- First run, published to `Reckswell/vsrgs` (3,465 files uploaded in 4 commits, 9,435 unchanged):
  - Bandori: notes for the 298 charts bestdori.com had (the PC item from 2026-11-11), drawn;
  - Chunithm: 227 songs from sdvx.in's lists not in the database (mostly removed songs), 106 new WORLD'S END charts,
    sdvx.in references on 1,914 songs; 412 charts pending (waiting for a `.c2s`); 135 new sdvx.in pictures (115/expert
    failed);
  - SEKAI: 11 charts saved and drawn, 21 pending (sdvx.in 260/expert and 360/expert failed); DDR: 22 pending;
  - Arcaea, GITADORA, Guitar Hero, IIDX, PIU: catalog rebuilt, nothing new;
  - SDVX failed on a bug (fixed, with a test) and was reset; its first run (sdvxindex 1.4.9 list: new songs, jackets,
    16 genre moves) waits for Alpha's go-ahead to publish to the dataset.
- Waiting for the PC: Arcaea, DDR and PIU new charts need `build-manifest` (GitHub code search; BACKLOG 14).
