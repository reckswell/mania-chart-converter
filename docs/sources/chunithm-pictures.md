# Chunithm: reading merged sdvx.in pictures (punipu2)

Status (2026-11-11): **not good enough to extract charts.** The reader in `src/vsrgconv/imaging/merged_chart.py`
reads the layout, the measure lengths and the note heads. It does not build a `Chart`, and `sources/chunithm.py`
does not call it. No picture-only chart has been extracted. The measured accuracy and what blocks it are below.

## Source

`raw.githubusercontent.com/punipu2/chunithm-chart-downloader/main/resized_charts/<sdvxin id><mst|ult|we>.png`. The
adapter records this URL as `source.picture` on MASTER and ULTIMA charts (see chunithm-c2s.md). BASIC, ADVANCED and
EXPERT have no merged picture. The pictures are 1–2 MB each. They are kept in `data/cache` and must never be
committed.

Problems with the 80 target pictures (the `listed` MASTER and ULTIMA charts that have no .c2s). These were checked
by MD5 on 2026-11-11:

- **Only 60 of the 80 files are distinct.** 32 charts share their picture with at least one other chart. This means
  the file is not that chart. For example, `11029mst` … `11035mst` (242 and 3031–3036) are one file, which has 165
  measures. Other groups are 3037/3041, 3038/3042, 3040/3060, 3043/3051/3086, 3044/3045, 3046/3057, 3047/3056,
  3049/3050, 3052/3054/3058/3059, 3055/3064 and 3061/3063. In each group, at most one chart can match. A reader must
  therefore refuse a picture whose notes disagree with the chart's published `note_counts`.
- The 7 `listed` WORLD'S END charts (8321, 8327–8332) have no `source.picture`. The punipu2 repo root has song
  lists (`prev_chunithm_songs.json`, `diff_songs.json`) with sdvx.in ids such as `000we` that could map them by
  title. Note that `000we.png` is 2816×978, which is a different scale from the other pictures.

## Layout (measured on 34 calibration pictures)

- Width is `50 + 200·n` for n columns. Column k covers x `200k … 200k+200`:
  - measure numbers (white, partly transparent): x +20…+50;
  - lane frame: x +50…+200;
  - 16-cell playfield: x +75…+175, with each cell 6.25 px wide.
- Columns are read bottom to top, left to right. Each column holds a variable number of measures. Neighbouring
  columns overlap by 24 rows: the top line of one column is the bottom line of the next.
- Every picture numbers its measures from `01`. The bottom of a measure number sits at the upper row of its measure
  line, ±1 row.
- Lines:
  - measure lines are 2 rows of grey (88,88,87) over (107,107,107), with some drawn yellow;
  - beat lines are 2 rows of (40,40,39);
  - the background is (2,2,2).
  - Lines are often hidden under notes, so hidden lines are restored from beat lines or from the chart's usual
    distance between the number and its line.
- Scale: the commonest beat spacing is 96 px per beat. 120, 144, 72, 60, 80, 78, 104 and 128 also occur. A BPM
  change redraws the following measures at a different spacing, roughly proportional to 1/BPM, because the pictures
  are drawn to time and not to beats. There is no BPM text in a merged picture.
- Picture beat 0 is the first drawn measure line. This is c2s bar 1 in 25 of the 34 charts (beat 4, or beat 3 for
  1033, which is in 3/4). It is bar 2 (beat 8) in 7 charts: 2857, 3004, 2964, 2188, 405, 2616 and 64. It is something
  else in 2544 and ULTIMA 331, two BPM gimmicks whose timeline fails. Without a .c2s the whole-bar offset is unknown.
- Note heads are bars, read top to bottom in one cell:
  - a pale light row (V ≥ 0.75, S ≤ 0.4);
  - a bright row (the head's beat);
  - a saturated row whose hue gives the kind: tap 335–12°, hold 15–44°, ExTAP / Ex-hold / Ex-slide start 45–72°,
    flick 165–196°, slide 200–245°;
  - a dark row.
  - The rows drift by ±1 px between columns of one picture.
  - Two touching bars of one kind are separated by their end caps: the light row darkens by 0.2 or more at the cell
    boundary, against less than 0.03 inside a bar.
  - A flick has grey arrow caps.
  - AIR arrows are drawn just above a ground head and can cover its light row.
- Slide nodes and ends and hold ends are drawn as bars like heads.
  - A hold body is a translucent purple-to-yellow strip.
  - A slide body is cyan with a bright centre line and blue node bars.
  - An AIR hold is a green line that ends at a magenta marker.

## Reader (`imaging/merged_chart.py`)

- `find_columns` finds the measure numbers, measure lines and beat lines of each column.
- `Timeline` fits each measure's length in beats. It prefers 4 beats, the previous measure's spacing, the chart's
  main spacing and beat lines on the grid. `beat_at` maps a row to a beat.
- `find_heads` finds head bars per column and cuts them at end caps and at changes of kind.
- `read_picture(data)` returns `PictureReading(timeline, heads)`. Head beats are snapped by `snap_beat` to the
  simplest c2s subdivision within 1.5 px.
- `match_heads(reading, chart)` is the calibration against an exact chart. It counts heads read exactly (beat, cell,
  width and colour), heads read with the wrong colour, and heads not read, per note kind. It searches the whole-beat
  offset between the picture and the c2s.

## Measured accuracy (34 calibration charts with both a picture and a .c2s, 2026-11-11)

The calibration set is MASTER 2871, 2857, 3004, 2964, 2957, 2960, 2851, 2985, 982, 2250, 2544, 1033, 2391, 2188, 2942,
2044, 405, 2616, 2525, 970, 342, 294, 590, 2629, 2204, 64, 257 and 2881, plus ULTIMA 321, 331, 63, 83, 317 and 113.
It includes BPM gimmicks, 3/4 and odd meters, dense slides and air-heavy charts.

Measure lengths, for 33 charts (982 was not measured: the run ran out of memory). The reader's measures are
shifted by the found offset and compared with the c2s bars:

| Charts | All measures right | Measures right |
|---|---|---|
| single BPM (25) | 21 | 2,392 / 2,419 |
| variable BPM (8) | 1 (2391, but its beats inside measures are off) | 556 / 866 |

The timeline maps truth taps onto drawn red pixels for 29 of 34 charts (hit rate ≥ 0.98). The 5 failures are the BPM
gimmicks: 2250 0.60, 2544 0.63, 2391 0.81, 2616 0.42 and ULTIMA 331 0.23.

Note heads read exactly (beat, cell, width and colour), as a fraction of the c2s notes of that kind:

| Kind | All 34 | 29 with a working timeline | 10 newest (id ≥ 2800) |
|---|---|---|---|
| tap | 0.827 | 0.918 | 0.934 |
| ExTAP | 0.669 | 0.756 | 0.760 |
| flick | 0.647 | 0.707 | 0.766 |
| hold start | 0.589 | 0.641 | 0.705 |
| slide start | 0.390 | 0.415 | 0.581 |

There are also 13,527 head bars that do not match a c2s head. Most of them are slide nodes, slide ends and hold
ends.

These parts are **not read at all yet**:

- hold durations;
- slide chains, paths and ends;
- AIR and its direction;
- AIR holds and AIR slides;
- mines;
- the ExHOLD and ExSLIDE flags (only the start colour is seen).

## What blocks extraction

1. The head accuracy above is far from "counts right per kind". Main causes:
   - touching bars split or merged wrongly (missing or false cap notches, flick grey caps);
   - heads under hold and slide bodies or under AIR arrows;
   - overlapping identical notes in the c2s that are drawn once;
   - older charts drawn in a different style: ULTIMA 63 taps 0.31, ULTIMA 317 slides 0.03.
2. Slide starts cannot be told from slide nodes and ends, nor hold starts from hold ends, without tracing the bodies.
3. Variable-BPM charts do not map. The spacing changes with tempo and there is no BPM text.
4. The first-bar offset (bar 1 or bar 2) is unknown for picture-only charts.
5. A third of the target pictures belong to another chart (see Source).

## Next steps

Do these in order, and measure each one with `match_heads` on the calibration set:

1. Trace hold and slide bodies, which separates starts from nodes and ends.
2. Read AIR arrows and AIR holds.
3. Assemble a `Chart`, with timing from the catalog BPM via `timing_from_bpm` and meters from the measures.
4. Reject a picture unless its per-kind counts equal the published `note_counts`. Those totals are
   `tap = tap + ExTAP + hold starts + slide starts`, plus `flick`.
5. Only then wire the reader into `_chart` in `sources/chunithm.py`, after the .c2s and before the layered sdvx.in
   reader. It should set `meta.source["extracted_from"] = "punipu2-picture"` and add a warning.

Fixtures: `tests/fixtures/chunithm_picture_2871_master_c10.png` is one column of 2871 MASTER.
`chunithm_picture_2525_master_c10.png` is two columns of 2525 MASTER across its 4/4 → 3/4 change. The expected
measures and heads in `chunithm_picture_truth.json` come from the c2s.
