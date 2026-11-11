# Arcaea: exact charts from the official .aff files

Arcaea stores each chart as an `.aff` text file with times in integer milliseconds. The files are exact. The adapter
is `src/vsrgconv/sources/arcaea.py` and the parser is `src/vsrgconv/formats/aff.py`.

## Where the data comes from

| What | URL (all GitHub raw) | Coverage (checked 2026-11-11, game 7.1.0) |
|---|---|---|
| Song list | `SkyEye-FAST/arcaea_wiki_data/HEAD/output/songlist`, `output/packlist` | The APK's own lists. 560 songs and 1,865 charts (560 PST/PRS/FTR, 68 BYD, 117 ETR). |
| Charts | `HarumiEna/Arcaea-AFF/HEAD/songs/<id>/<id>_<n>.aff` or `songs/<id>/<n>.aff` | 1,833 of the 1,865 charts, plus the removed song particlearts. `n` is the ratingClass: 0 PST, 1 PRS, 2 FTR, 3 BYD, 4 ETR. |
| Constants | `Insouciant21/arconline-ext/HEAD/src/lib/chart-constants.json` | Every chart up to 7.1 except the three lasteternity placeholders. |
| Note counts and constants | `DipTaken/arcaea-b50-web/HEAD/scripts/data/note_count.json`, `cc.json` | A newer copy of arcaea-toolbelt-data. It has counts up to 7.0, which adds diein, balor, dreadarea, un, rivenpilgrim, cataclysmcry, deinosphainein, ember and sacrosanct. |
| Note counts and constants | `DarrenDanielDay/arcaea-toolbelt-data/HEAD/src/data/ChartNotes.json`, `ChartConstant.json`, `chart-data.json` | Game 6.16.1c. Its `chart-data.json` also gives the removed songs. |
| Counts, constants, jacket numbers, length | `Mattyakyou/ArcaeaData/HEAD/data.json` | 542 songs. The rows have no song id, so they are matched by any localized title and then by artist. |
| Jackets | `Mattyakyou/ArcaeaData/HEAD/jacket/<jacket_number>.png` | 543 songs, plus 35 BEYOND/ETERNAL jackets. The files are WebP at 768 px and are stored as ≤320 px JPEG. |

The adapter uses the counts and constants in the order of the table and takes the first source that has a value.
`source.counts_from` and `source.constant_from` on each chart record name the source used. Four charts get different
counts from different sources (aloneandlorn PST, felys PST, onandon PRS, technicolour PRS: Mattyakyou disagrees). For
these charts every count is kept in `source.counts`, and the .aff agrees with the preferred one.

### The file list

The repository API is not reachable from the cloud machine, so the list of .aff files comes from GitHub code search.
Each query is limited to 1,000 hits, so the search is split into size slices. The result is committed as
`src/vsrgconv/sources/data/arcaea_aff.json`. It has 1,875 paths, and 1,847 of them are current charts. Rebuild it with:

```
PYTHONPATH=src python -m vsrgconv.sources.arcaea build-manifest
```

This takes about 3 minutes and needs `gh`. The adapter does not need `gh`.

Older revisions are named `*_old_<ver>_<n>.aff` and are ignored. Some folders in the repository are not in the
songlist: tutorial, the April Fools charts and old separate BEYOND folders.

The repository had a DMCA takedown of its images and audio in 2026-04. The .aff files are at risk too, which is why
the database keeps its own copy of the notes.

### Gaps

- **32 charts with no .aff:** the 8 songs added in 7.0 and 7.1 (chateaudecartes, voyagetofuture, ran, etherdrive,
  schrago, fatality, parallax, trickmaster). These songs have constants but no published note counts.
- **18 songs with no jacket:** Mattyakyou has no number for the same 8 songs, nor for balor, cataclysmcry,
  deinosphainein, diein, dreadarea, ember, rivenpilgrim, sacrosanct, un or particlearts.
- **22 charts with their own jacket (`jacketOverride`) whose image is not stored:** Mattyakyou lists jacket numbers only
  for BEYOND/ETERNAL, so PST/PRS overrides (avril, ii, prism, stager, ...) and a few BYDs have none.
- **lasteternity PST/PRS/FTR:** placeholder files with no notes (`rating` 0, `hidden_until: always`). They are stored
  as empty charts with a note saying so.
- **particlearts:** removed in 6.7.1 and kept with `extra.removed`. Its metadata comes from arcaea-toolbelt-data, which
  has no artist.

## Catalog conventions

- `song_id` is the songlist `id`.
- The genre is the pack name. An append pack goes under its parent pack: Lasting Eden Chapter 2 is filed as
  "Lasting Eden". The songlist set `single` is "Memory Archive". The slugs are in `games/registry.py`.
- The song record keeps the artist, BPM, version and date. `date_added` is the songlist `date` (UTC). The rest of the
  songlist entry goes in `extra.songlist`, and the actual append pack goes in `extra.pack` and `extra.pack_name`.
- Slots:

  | Native name | Slot |
  |---|---|
  | PAST | normal |
  | PRESENT | hard |
  | FUTURE | expert |
  | BEYOND | master |
  | ETERNAL | extra |

- The level is `rating` plus "+" when `ratingPlus` is set, for example "9+".
- A difficulty's own fields go in `source.songlist` on the chart record. These include `title_localized`, `artist`,
  `bpm`, `jacketOverride`, `audioOverride`, `hidden_until` and `ratingClassAlias`.
- `chart_meta` uses the BEYOND title, artist and BPM overrides. For example, the pragmatism BYD is "PRAGMATISM
  -RESURRECTION-". The song's own title is then kept in `meta.source.song_title`.
- The published count is `note_counts.combo`, which is the in-game combo.

## The .aff format

The parser follows ArcCreate's reader (Arcthesia/ArcCreate, `Assets/Scripts/ChartFormat/Aff/AffChartReader.*.cs`). It
was checked against every official file: all 1,847 files parse with no unknown lines.

| Line | Meaning | IR |
|---|---|---|
| `AudioOffset:n`, `TimingPointDensityFactor:f`, then `-` | header. Chart time = audio time − AudioOffset. | `TimingMap.offset_ms` = AudioOffset; the density factor is in `meta.source.aff.density` |
| `timing(t,bpm,beats);` | tempo from t; `beats` = beats per bar line | `BpmChange` and `MeterChange` (see Timing) |
| `(t,lane);` | floor tap | tap |
| `hold(t1,t2,lane);` | floor hold | hold |
| `arc(t1,t2,x1,x2,ease,y1,y2,color,fx,trace[,smooth])` | sky arc; `trace` is false (playable), true (trace) or designant | air_hold if playable and t2 > t1, else other |
| `…[arctap(t),…];` | arctaps on the arc, almost always on a trace | air, with its x and y on the trace in extra |
| `timinggroup(props){ … };` | events with their own timing list; props are joined with `_` | `extra.group`; `noinput` → other with `extra.noinput` |
| `scenecontrol(…);`, `camera(…);` | visual effects | kept verbatim in `meta.source.aff.scenecontrol`, `.camera` |

Details:

- Arc fields:
  - x = 0 is the boundary between lanes 1 and 2, and x = 1 is the boundary between lanes 3 and 4. The floor spans
    x = −0.5 to 1.5.
  - y = 0 is the floor and y = 1 is the top.
  - `ease` is one of `s b si so sisi siso sosi soso`. The first pair is for x and the second for y. `si` is a sine
    curve, fast at the start. `so` is 1 − cos, slow at the start. `b` is a cubic, flat at both ends.
  - Colours are 0 blue, 1 red, 2 green and 3 grey.
  - `fx` is a hit sound such as `glass_wav`.
  - `smooth` is an optional 11th field, a render resolution, and appears on 1,794 arcs.
- Each arc is one IR note:
  - `lane` is its start position and `path` has one point at its end.
  - The path point's easing follows the shape of the x curve: `si` → "out", `so` → "in", `b` → "inout", `s` →
    "linear".
  - The raw values are in `extra`: `x`, `y`, `ease`, `color`, and `fx` and `smooth` when present.
  - An arc that continues another has `extra.continues` (the chaining rule is under Combo).
  - 12,795 non-trace arcs have zero length. These are instant moves between arcs and are stored as other with
    `extra.aff` "arc".
  - 40 arcs end before they start. They get duration 0, and their path point keeps the negative span.
- Traces are kept as other notes with `extra.aff` "trace" (or "designant"), so the IR stays lossless.
- `designant` traces only appear in Designant. and Lament Rain. Their arctaps give no combo, so they are other with
  `extra.designant`.
- The group properties used in the files are `noinput` (not judged), `fadingholds`, `anglex<n>`/`angley<n>` and
  `tracecol<rgb>`. Only `noinput` changes what the IR or the combo does; the props are kept raw.

### Lanes

- A chart that only uses floor lanes 1–4 has `Chart.lanes = 4` and IR lane = aff lane − 1.
- A chart with any floor note on lane 0 or 5 has `Chart.lanes = 6` and IR lane = aff lane. These charts turn on the
  extra lanes with `scenecontrol(…,enwidenlanes,…)`. There are 24 such charts: every difficulty of arcanaeden,
  deinosphainein, dreadarea, pentiment, rivenpilgrim and testify. `meta.source.aff.lanes` is "1-4" or "0-5".
- Arc and arctap positions use the same units. The centre is 2x + 2 − first_lane, and the note is 1 lane wide around
  it, so a centre of 2.0 gives `lane` 1.5 and `width` 1. Positions outside the surface are kept as they are.

### Timing

- **Source times are ms-quantised.** A 1/4 note at 189 BPM falls on 2222 ms rather than 2222.2 ms.
- Beats are exact `Fraction`s of the integer ms and the decimal BPM text (`Fraction("189.00")`):
  beat = segment beat + (t − t0) · bpm / 60000. So beats are exact, but they are a little off the musical grid
  (6.9993 instead of 7). Snapping is left to a rule.
- The main group's timing list (group 0) defines the beat axis.
- `TimingMap.ms_at(beat)` gives back the file's ms (+ AudioOffset) exactly after rounding. `aff_to_chart` checks this
  for every note time and warns if it fails; it never does on the official files. The raw timing lines are also kept
  in `meta.source.aff.timing` as `[t, "bpm", "beats"]`.
- **BPM ≤ 0** is a visual effect (stop or reverse). It appears 3,697 times in all groups. The beat axis keeps the
  previous positive tempo over such a segment, and the effect becomes a `ScrollChange` with speed bpm / axis bpm (0 or
  negative), which goes back to 1.0 at the next normal segment. Chart warnings say how many there were.
- Huge positive BPMs, such as `99999999.00`, are used for jumps and are kept as they are.
- A timing group's own timing list only changes that group's scroll and its combo tick interval. Notes in the group
  still get their beat from the main axis. Each group's props and timing are kept in `meta.source.aff.groups`.
- Meters come from main-group timing lines whose `beats` is n/4, n/8 or n/16. Arcaea restarts the bar lines at every
  timing line, as an osu! red line does, and `beats` 0 (no bar lines) is skipped.

## Combo (published note counts)

`combo_counts` implements the in-game combo:

- Each tap and arctap counts 1.
- Holds and arcs count ticks. Take bpm as |BPM| at the note's start in the note's own group. Then the interval is
  (60000 if bpm ≥ 255 else 30000) / bpm / TimingPointDensityFactor, and n = floor(duration / interval), computed
  exactly.
- A hold counts n − 1, and at least 1.
- An arc that starts a chain counts n − 1, and at least 1. An arc that continues a chain counts n, and at least 1. An
  arc continues another when an arc of the same trace-ness ends within 10 ms of its start, at the same x and y, in any
  group and any colour.
- These count 0: traces, zero-length arcs, notes whose start BPM is 0, designant arctaps and anything in a `noinput`
  group.

This rule comes from ArcCreate (`Hold.cs`, `Arc.Judgement.cs`, `ArcNoteGroup.cs`), with two changes fitted to the
published counts:

| Variant | Charts matching (of 1,833 with a published count) |
|---|---|
| ArcCreate's 1 ms chain tolerance | 1,658 |
| 3–15 ms chain tolerance (10 is used) | 1,808 |
| designant arctaps counted | 1,800: Designant. ×4 and Lament Rain ×4 go wrong |
| 32-bit float tick maths | 1,803 |
| ticks from the tempo integrated over the note | worse (333 of 481 multi-tempo charts) |
| chains only within a timing group, or only of the same colour | worse |

So **1,808 of 1,833 charts (98.6%) match**. The scrape stores `meta.source.combo_check` as match, mismatch or
unpublished, and adds a warning on a mismatch. The 25 mismatches:

- **15 charts off by 1–2:** cryogenic PST, logos PST, raysofremnant PST, manicjeer PST, arcanaeden PST, flashback FTR,
  mahoroba FTR, grandspell FTR, quon FTR, edenwacca FTR, ichirin FTR, withu FTR, deinosphainein PRS, sacrosanct PRS,
  shadesoflight BYD. Eight of them (the first four, then flashback to quon) have one tempo and no timing groups, and
  no chain tolerance from 0 to 60 ms changes them. The cause is probably an edge case of hold/arc ticks at exact ms
  boundaries, or a chart revised since the count was published. Wherever Mattyakyou has a count, it agrees with the
  published number.
- **Charts with timing-group or tempo-effect gimmicks:** diein ETR (−12), wereallgonnadie ETR (−4), bethere FTR (−4),
  arcahv PRS (−2), snowwhite BYD (−2), pragmatism BYD (−1), alterego FTR (+2), andrevivemelody PRS (+2), axiumcrisis
  BYD (+1). In these, holds and arcs start inside 0-BPM, 20-BPM or 41-BPM effect segments, or inside many groups,
  so the tempo the game uses for their ticks is unclear.
- **yourbestnightmare BYD (+24):** both the current file and `_old_6.16` give 1,475 against the published 1,451. Its
  `noinput` and plain groups overlap, and the game must judge some of them differently.
