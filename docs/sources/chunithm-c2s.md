# Chunithm: exact charts from .c2s files

CHUNITHM stores each chart as a `.c2s` text file. These files are exact: they give tick-level timing, cells,
widths, slide control points, air notes and BPM changes. sdvx.in's pictures have none of that precision, so a
`.c2s` file always wins over a picture.

## Where the files come from

| What | URL | Coverage (checked 2026-11-11) |
|---|---|---|
| Charts | `raw.githubusercontent.com/QingQiz/MarisaBot/master/Marisa.Frontend/public/assets/chunithm/chart/{id:04d}_{dd}.c2s` | 6,692 charts in total: 1,629 songs × BASIC..MASTER, 106 ULTIMA and 70 WORLD'S END. This covers everything up to X-VERSE-X. |
| Catalog | `raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/data/music-ex.json` and `music-ex-deleted.json` | 1,778 current rows and 161 removed rows |
| Jackets | `raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/jacket/<image>` | Every song in music-ex.json. The jacket name is the row's `image` field. |
| Pictures | `raw.githubusercontent.com/punipu2/chunithm-chart-downloader/main/resized_charts/<sdvxin id><mst\|ult\|we>.png` | Merged sdvx.in pictures, MASTER, ULTIMA and WORLD'S END only, including the newest version |

How the URLs work:

- `id` is SEGA's song id. This is the same id otoge-db uses and the database uses.
- `dd` is 00 for BASIC, 01 for ADVANCED, 02 for EXPERT, 03 for MASTER, 04 for ULTIMA and 05 for WORLD'S END. WORLD'S END songs have ids of 8000 and up.
- The sdvx.in id comes from otoge-db's `lev_<d>_chart_link` field. For example, `09/09001mst` means id `09001`. The adapter records the picture URL as `source.picture` on the chart, but it does not download the picture, because the merged pictures are 1–2 MB each.

The 6,692 charts were found by asking for every (id, difficulty) pair in the catalog. These charts are not in MarisaBot:

- the newest version's songs;
- the second WORLD'S END chart of an id. otoge-db lists 8303 and 8304 twice, and these are stored as `special-2`.

Those charts stay at status `listed`. They keep their published note counts and their picture URL, so the sdvx.in
reader (docs/sources/chunithm-sdvxin.md) can fill them in later. A reader for the merged punipu2 pictures is
started but cannot extract charts yet. Its layout, measured accuracy and blockers are in
docs/sources/chunithm-pictures.md.

## Format

The parser is `src/vsrgconv/formats/c2s.py`. The file is tab-separated text with three parts:

- a header (`VERSION`, `CREATOR`, `BPM_DEF`, `RESOLUTION` ...);
- one line per event;
- a footer of `T_*` totals.

Timing works like this:

- An event's tick is `measure * 384 + offset`. RESOLUTION is always 384, so there are 96 ticks per beat.
- The IR beat is `tick / 96`, an exact fraction.
- `MET` only moves the drawn bar lines. The measure column is always a 384-tick block.

| Tag | Columns after measure, offset | IR |
|---|---|---|
| `BPM` | bpm | `BpmChange` |
| `MET` | denominator, numerator (reversed) | `MeterChange` |
| `SFL`, `DCM` | duration, multiplier | two `ScrollChange`s (start, back to 1.0) |
| `STP` | duration | scroll 0, then 1.0 |
| `TAP` / `CHR` / `FLK` / `MNE` | cell, width [, effect] | tap / ex_tap / flick / mine |
| `HLD`, `HXD` | cell, width, duration | hold (`HXD`: `extra.ex_start`) |
| `SLD`, `SLC`, `SXD`, `SXC` | cell, width, duration, end cell, end width | one **slide** per chain of segments, path point per segment (`linear` at SLD nodes, `control` at SLC) |
| `AIR`, `AUR`, `AUL`, `ADW`, `ADR`, `ADL` | cell, width, target note | air, `extra.direction` |
| `AHD`, `AHX` | cell, width, target, duration | air_hold (`AHX`, a newer variant with the same columns: `extra.variant`) |
| `ASC`, `ASD` | cell, width, target, h0, duration, end cell, end width, h1, colour | one air_hold per chain (heights in `extra.heights`); colour `NON` = invisible → `other` |
| `ALD` | cell, width, interval, h0, duration, end cell, end width, h1, colour | air crush: air_hold if interval > 0 and visible, else `other` |
| `SLP`, `SLA`, `CLK` | per-lane scroll; clock | not in the IR yet; kept verbatim in `meta.source.c2s_unrepresented` |

Some details of the format:

- **Chaining.** A slide segment continues another when its start tick, cell and width equal the other segment's end tick, cell and width. Air slides also match on height, and only a segment whose target is `ASC` or `ASD` can continue a chain.
- **Duplicates.** Exact duplicate lines are common: one MASTER file has 446 duplicate `SLC` lines. They are dropped, and the drop is reported as a warning. The game's `T_REC_SLD` counts them, but they draw on top of each other.
- **Footer.** `T_JUDGE_ALL` is the combo, and it matched otoge-db's note total on 44 of 44 sampled charts. The adapter compares the two and warns when they differ. A difference means the chart was probably revised after the file was copied. The `T_JUDGE_*` and `T_REC_*` lines are kept in `meta.source.c2s_totals`.
- **Header ids.** Newer files have `MUSIC`, `DIFFICULT` and `LEVEL` set to 0, so the id and difficulty always come from the file name.

Other parsers, for cross-checking: MarisaBot's `parser.ts`, chuni-chart-rs, ChuConverter and MuConvert.
