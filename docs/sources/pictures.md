# Source pictures ("true source") and A/B checks

Alpha asked (2026-11-12) for each chart's picture *as its source publishes it*, kept beside the picture redrawn from
the stored notes, so the framework can compare the two. `vsrgconv pictures` downloads them; `vsrgconv ab` compares.

## Where each game's pictures come from

Checked 2026-11-12 from the cloud machine, after Alpha added the source sites under the cloud environment's Allowed
domains. "Blocked" means the network policy still refuses the host (proxy 403).

| Game | Notes in the database come from | Published chart pictures | Reachable from the cloud |
|---|---|---|---|
| Chunithm | the game's `.c2s` files (MarisaBot) | sdvx.in draws ADVANCED (a few), EXPERT, MASTER, ULTIMA and WORLD'S END, three layers per chart (lanes, notes, measure numbers; chunithm-sdvxin.md). punipu2/chunithm-chart-downloader mirrors merged MASTER and ULTIMA pictures (`source.picture`, 1,901 charts) | yes, both |
| SOUND VOLTEX | none (no notes) | RRRGR's sdvxindex clone column pictures (`source.picture`, 7,997 charts); sdvxindex.com (`source.sdvxindex_picture`, 8,597, 667 of them only there); sdvx.in | RRRGR yes; sdvxindex.com yes, but its pictures moved to `sdvxindexasset.com` (blocked) |
| Project SEKAI | the game's SUS files | sdvx.in draws EXPERT, MASTER and APPEND, three layers per chart | yes |
| beatmania IIDX | none (no notes) | textage.cc score pages, drawn by script in the browser: a picture needs a browser screenshot | textage.cc yes; no picture files to fetch |
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

## `vsrgconv pictures <chunithm|sekai|all> --from sdvxin`

sdvx.in itself, for Chunithm and Project SEKAI. Which charts it draws comes from its own song lists (Chunithm: the
per-version list pages, matched to the database by the legacy id, WORLD'S END by title, kanji and stars; Sekai:
`/prsk/_/json/songs0001.json` and `songs_removed.json`, matched by title, then artist; sdvx.in's Sekai ids are its
own, not the game's). Per chart, in `data/pictures/<game>/<genre>/<song>/sdvxin/`:

- `<slot>.bg.webp`, `<slot>.obj.webp`, `<slot>.bar.webp`: the three layers exactly as sdvx.in publishes them (lanes,
  notes, measure numbers; same bytes, hard-linked from the download cache). This is the true source. The viewer
  stacks them in that order. They are lossy WebP (VP8 with an alpha plane), so colours are not exact.
- `<slot>.webp`: the layers stacked on black, saved at WebP quality 90, for looking at. Made here, not published.
- `manifest-sdvxin.json` per game: per `<song_id>/<slot>`, sdvx.in's id and difficulty key, the viewer URL, each
  layer's URL, bytes and sha256, the merged picture's size and sha256, and `missing_layers` / `layer_sizes_differ`
  where that happens.
- A chart whose merged picture is already there is skipped (its layers may have been removed locally once they were
  on the Hugging Face dataset). sdvx.in answers a missing picture with a 302 to its index page; the PNG names of the
  old site do the same.

The download waits 1 s between requests (all workers together), so Chunithm takes about four hours and Sekai about
an hour. sdvx.in has put a human check in front of heavy traffic before.

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
- `--from sdvxin` compares sdvx.in's pictures (`manifest-sdvxin.json`) instead. The reader is given the layers as
  published, not the merged WebP: they are stacked, every colour is snapped to the 256 colours of a lossless
  sdvx.in picture (`imaging/sdvxin_palette.json`, from punipu2's copy of 01189 MASTER), and the alpha is restored.
  Without the snapping the reader, which matches exact colours, found 0.6% of the notes; with it, about 80%. Where
  the notes layer has been removed locally (after upload), the merged picture is read instead.

## Results (2026-11-12)

`vsrgconv pictures`, from the cloud:

| Game | Charts with a picture URL | Downloaded | Not downloaded | Size |
|---|---|---|---|---|
| Chunithm (punipu2, MASTER and ULTIMA) | 1,901 | 1,899 | 2 missing upstream (404) | 2.1 GB |
| SDVX (RRRGR column pictures) | 8,664 | 7,948 | 667 only on sdvxindex.com (now on sdvxindexasset.com, blocked), 49 missing upstream | 1.2 GB |
| Chunithm (sdvx.in, `--from sdvxin`) | 3,854 drawn (1,787 EXPERT, 1,787 MASTER, 114 ULTIMA, 129 WORLD'S END, 37 ADVANCED) | 3,853 | 1 EXPERT listed but not published yet (01037) | 16.5 GB of layers (16.2 GB the notes layers), 3.9 GB merged |
| Project SEKAI (sdvx.in, `--from sdvxin`) | 1,633 drawn (738 EXPERT, 739 MASTER, 156 APPEND) | 1,631 | 2 EXPERT listed but not published (sdvx.in 0240, 0329) | 1.5 GB of layers, 0.5 GB merged |

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

`vsrgconv ab chunithm --from sdvxin --images` on the 3,691 sdvx.in Chunithm charts that have notes (162 have none:
songs MarisaBot has no `.c2s` for):

- 3,681 compared; 10 pictures the reader cannot lay out (no measure lines found).
- Agreement: 73% of 3.09 million notes; median chart 81%. 39 charts at 95% or more, 1,962 at 80–95%, 1,231 at
  50–80%, 449 under 50%. By difficulty (median chart): EXPERT 84%, ULTIMA 80%, ADVANCED 80%, MASTER 79%, WORLD'S
  END 72%.
- By kind: tap 84%, hold 60%, ExTAP 59%, flick 59%, slide 51%.
- On the 1,783 MASTER and ULTIMA charts compared both ways, sdvx.in's lossy layers and punipu2's lossless PNGs score
  the same (median 79.1% against 79.3%), so the colour snapping loses almost nothing; the reader is still the limit.
- The comparison pictures (5.7 GB) and `data/ab/chunithm/report-sdvxin.json` are on the Hugging Face dataset under
  `ab/chunithm/`, beside the pictures under `pictures/chunithm/.../sdvxin/`.
- To fit the cloud machine's disk, the run went in batches of about 300 charts: download, A/B, upload, check each
  notes layer's sha256 on the Hub, then delete the local notes layer. The notes layers are on the Hub only.

Project SEKAI: sdvx.in's lists matched 742 of the database's 743 songs (4 by artist and a close title, such as
"Peaky Peaky" listed as "Peaky"; スロウライト is not on sdvx.in). There is no reader for these pictures yet, so `vsrgconv
ab sekai --from sdvxin --images` only drew the 1,619 comparison pictures (12 charts have no stored notes; 1.1 GB).
The redrawn half uses the same beat spacing as sdvx.in (96 px a beat) but breaks columns every 5 measures where
sdvx.in breaks every 4.

## What is not done yet

- Project SEKAI's sdvx.in pictures, its comparison pictures and `ab/chunithm/report-sdvxin.json` are not on the
  Hugging Face dataset yet: the session that made them lost its Hugging Face access when the cloud environment
  changed (agent/LOG.md).
- SDVX: 667 charts whose only picture is on sdvxindex.com wait for `sdvxindexasset.com` under Allowed domains (the
  site became a single-page app and serves its pictures from there; the old picture URLs answer with the app's
  `index.html`, which `vsrgconv pictures` now refuses). sdvx.in's own SDVX pictures are not fetched.
- IIDX: textage.cc draws charts in the browser, so a true-source picture needs browser screenshots, and notes need a
  reader for textage's script data. Neither is started.
- BanG Dream!: Bestdori draws in the browser too; its JSON is already the source of the stored notes.
- No reader for Project SEKAI's sdvx.in pictures yet, so its A/B is comparison pictures only, no metrics.
- The Chunithm reader is the limit on the A/B metrics (slides, holds, flicks and ExTAPs are read worse than taps).
