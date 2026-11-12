# Source pictures ("true source") and A/B checks

Alpha asked (2026-11-12) for each chart's picture *as its source publishes it*, kept beside the picture redrawn from
the stored notes, so the framework can compare the two. `vsrgconv pictures` downloads them; `vsrgconv ab` compares.

## Where each game's pictures come from

Checked 2026-11-12 from the cloud machine. "Blocked" means the cloud environment's network policy refuses the host
(proxy 403); adding the host under the environment's Allowed domains, or running on the PC, would reach it.

| Game | Notes in the database come from | Published chart pictures | Reachable from the cloud |
|---|---|---|---|
| Chunithm | the game's `.c2s` files (MarisaBot) | sdvx.in draws EXPERT, MASTER, ULTIMA and WORLD'S END (three layers per chart: lanes, notes, measure numbers; chunithm-sdvxin.md). punipu2/chunithm-chart-downloader mirrors merged MASTER and ULTIMA pictures (`source.picture`, 1,901 charts) | punipu2 yes; sdvx.in blocked |
| SOUND VOLTEX | none (no notes) | RRRGR's sdvxindex clone column pictures (`source.picture`, 7,997 charts); sdvxindex.com (`source.sdvxindex_picture`, 8,597, 667 of them only there); sdvx.in | RRRGR yes; sdvxindex.com and sdvx.in blocked |
| Project SEKAI | the game's SUS files | sdvx.in has a Project SEKAI section (not checked from here) | blocked |
| beatmania IIDX | none (no notes) | textage.cc score pages, drawn by script in the browser: a picture needs a browser screenshot | blocked |
| BanG Dream! | Bestdori JSON | Bestdori draws charts in the browser; there are no picture files | n/a |
| Arcaea | the game's `.aff` files | none published | n/a |
| DDR, Pump It Up | simfiles | none: the simfile is the source | n/a |
| Guitar Hero | MIDI and `.chart` rips | none: the chart file is the source | n/a |
| GITADORA | none yet (`.dtx` host) | none known | n/a |

Where the notes come from the game's own chart files (Chunithm, Sekai, Arcaea, Bandori, DDR, PIU, GH), the stored notes
*are* the source and the redrawn picture is exact by construction. A source picture is then a second, independent
drawing (sdvx.in's), which is what the A/B check compares against. Where there are no notes (SDVX, IIDX), the source
picture is the only picture of the chart.

## `vsrgconv pictures <game|all>`

- Downloads `source.picture`, falling back to other `*_picture` keys, for every chart.
- Writes `data/pictures/<game>/<genre>/<song>/<slot>.<ext>`, the same layout as `data/db` and `data/renders`. The file
  is kept exactly as published (same bytes; hard-linked from the download cache).
- `data/pictures/<game>/manifest.json`: per `<song_id>/<slot>`, the URL, source key, bytes, sha256, size and
  `shared_with` (other charts whose picture is the same file; such a picture is right for at most one of them).
- Resumable: pictures already there are kept unless `--refresh`. `data/pictures` is not committed (`.gitignore`);
  `vsrgconv publish` uploads it to the Hugging Face dataset as `pictures/`.

## `vsrgconv ab <game|all>`

For every chart with both a source picture and stored notes:

- **Metrics** where a reader for the source's pictures exists (`pictures.READERS`). Chunithm's merged sdvx.in
  pictures use `imaging/merged_chart.py`: it reads the note heads (beat, cell, width, colour) and `match_heads`
  counts, per note kind, the stored notes the picture shows exactly. `agreement` = matched / stored notes drawn as
  heads. The reader's own accuracy limits this (chunithm-pictures.md), so a low score is a lead: the chart may have
  been revised since one of the two was made, the picture may belong to another chart (`shared_with`), or the
  reader may have misread it.
- **Comparison pictures** with `--images`: `data/ab/<game>/.../<slot>.png`, the source picture (A) above the stored
  notes redrawn at the source's scale (B): the same column height and the source's measured beat spacing.
- `--report <file>`: every chart's result; the console shows the summary and the 15 lowest charts.

## Results (2026-11-12)

`vsrgconv pictures`, from the cloud:

| Game | Charts with a picture URL | Downloaded | Not downloaded | Size |
|---|---|---|---|---|
| Chunithm (punipu2, MASTER and ULTIMA) | 1,901 | 1,899 | 2 missing upstream (404) | 2.1 GB |
| SDVX (RRRGR column pictures) | 8,664 | 7,948 | 667 only on sdvxindex.com (blocked), 49 missing upstream | 1.2 GB |

Every downloaded file opens as a picture. 32 Chunithm charts share their picture with another chart (the groups in
chunithm-pictures.md).

`vsrgconv ab chunithm --images` on the 1,819 Chunithm charts that have both a picture and notes:

- 1,783 compared; 36 pictures the reader cannot lay out (no measure lines found: mostly the older drawing style with
  light-blue lane walls).
- Agreement: 71% of 1.81 million notes; median chart 79%. 19 charts at 95% or more, 827 at 80–95%, 664 at 50–80%, 273
  under 50%.
- By kind: tap 80%, ExTAP 65%, flick 58%, hold 57%, slide 44%. These match the reader's measured accuracy
  (chunithm-pictures.md), so today the score mostly measures the reader. The charts under 50% that were looked at
  (for example Aleph-0 MASTER, 0.1%) are gimmick charts with many BPM changes or the older drawing style, where the
  reader's timeline fails, not wrong stored charts.
- The comparison pictures (`data/ab`, 2.0 GB) and the report (`data/ab/chunithm/report.json`) are on the Hugging
  Face dataset under `ab/`.

## What is not done yet

- sdvx.in itself (Chunithm EXPERT and WORLD'S END, full-size MASTER and ULTIMA, Project SEKAI, SDVX), sdvxindex.com
  and textage.cc are blocked from the cloud. Either add `sdvx.in`, `sdvxindex.com` and `textage.cc` under the cloud
  environment's Allowed domains, or run `vsrgconv pictures` on the PC.
- sdvx.in's three layers need a fetcher that stores each layer as published plus a merged picture.
- Readers for other pictures (SDVX column pictures, sdvx.in layers) would extend the A/B metrics, and for SDVX would
  give notes where there are none.
