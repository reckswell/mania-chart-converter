# BanG Dream! Girls Band Party! (JP): catalog, exact charts, jackets

Everything comes from GitHub raw files, which the cloud machine can reach. Charts are Bestdori JSON, which is exact.
The code is in `src/vsrgconv/sources/bandori.py` (adapter) and `src/vsrgconv/formats/bestdori.py` (parser).

```
python -m vsrgconv --interval 0.02 catalog bandori
python -m vsrgconv --interval 0.02 scrape bandori --charts --jackets --workers 8 --progress
python -m vsrgconv stats bandori
```

## Where the files come from

| What | URL (`raw.githubusercontent.com/...`) | Coverage (checked 2026-11-11) |
|---|---|---|
| Catalog | `esterTion/bangdream_master_db_diff/HEAD/masterMusicList.json`, `masterMusicDifficultyList.json`, `masterBandMap.json`, `masterMusicJacketMap.json` | The game's own master data (10.2.0.200, 2026-11-09): 819 songs and 3,437 charts (EASY to EXPERT for every song, SPECIAL for 161) |
| BPM, length, Bestdori counts | `Chorus-ACE/Greatdori-OfflineResBundle/HEAD/api/songs/all.7.json` | An offline copy of Bestdori's song list, up to song 742. Newer songs take their BPM from their first mirrored chart. |
| Charts: HARD/EXPERT/SPECIAL | `coatcn1/MaaBanGDream/HEAD/resource/charts/bestdori/<id>/<hard\|expert\|special>.json`, listed in `.../charts/manifest.json` | 1,783 charts of 812 songs. The manifest is dated 2026-10-04. Exact: the file wraps Bestdori's raw chart under `"chart"`, with `source.url` and its sha256. |
| Charts: EASY/NORMAL | `kanon511/BangDreamDifficultyPrediction/HEAD/charts/<id>-<index>_<level>.json` | 1,356 charts (songs up to 704). They are **lossy** (see below). |
| Charts, PC only | `https://bestdori.com/api/charts/<id>/<easy\|normal\|hard\|expert\|special>.json` | Every chart, exact. The cloud proxy refuses bestdori.com. |
| Jackets | the manifest's `songs[].jackets[].path`, for example `.../bestdori/1/jacket-cn-1.png` | 812 songs, 400 px palette PNGs. 761 use CN-server art and 51 use JP art. A JP jacket is chosen when the manifest lists one, then the one named like the JP jacket. |
| Jackets, PC only | `https://bestdori.com/assets/jp/musicjacket/musicjacket<B>_rip/assets-star-forassetbundle-startapp-musicjacket-musicjacket<B>-<jacketImage>-jacket.png`, where B = ceil(id / 10) × 10 | Every song |

Notes on the files:

- `id` is the game's musicId. It is also Bestdori's song id and the database's song_id.
- The kanon511 file name carries the level at the time it was scraped. The adapter tries Bestdori's level from the
  Greatdori copy first, then today's JP level, then ±1 and ±2. In practice every one of the 1,356 files matched on the
  first try.
- The adapter tries chart sources in this order: the MaaBanGDream mirror, then bestdori.com, then kanon511. After the
  first connection failure, the run stops asking bestdori.com. A file dropped into the cache under
  `data/cache/bestdori.com/api/charts/...` is still used.
- `masterMusicDifficultyList` has a row for musicId 99, which is the tutorial. Song 99 is not in the music list, so it
  is skipped.
- The mirror also has songs 1000–1006 and 10001–10005, which are not in JP's music list. They are ignored.

## Catalog fields

| Database | Source |
|---|---|
| `song_id` | `musicId` |
| `title` | `musicTitle`. The game's font draws two badge glyphs: `甅` is written as `[FULL] ` and `躄` as `[原曲] `, the way Bestdori writes them. The raw title is kept in `extra.title_raw`. |
| `artist`, `genre` | the band name (`masterBandMap[bandId].bandName`) |
| `bpm` | Greatdori: one value, or `min-max`. Otherwise the BPM events of the song's first mirrored chart. |
| `date_added` | `publishedAt`, as a JST date |
| `extra` | `lyricist`, `composer`, `arranger`, `tag` (normal/anime/tie_up), `published_at`, `closed_at` (ms), `music_data_type`, `ruby`, `title_en` (EN server title, from Greatdori), `length_s`. `removed` (with `closed_date`) when `closedAt` is in the past: 13 songs, mostly time-limited event SPECIALs. `unreleased` when `publishedAt` is in the future: none today. |
| chart slot | easy, normal, hard, expert; SPECIAL → `master` (`games/registry.py`) |
| chart `level` | `playLevel`, today's JP value |
| chart `note_counts` | `{"combo": n}`: Bestdori's count, taken from the manifest's `expected_notes` or Greatdori's `notes` |
| chart `source` | `notesQuantity` (see below), `published_at` (SPECIAL), `special_notes` (the master's `enableSpecialNotes`: exactly the 112 JP charts with Directional flicks or hidden slide points; never on EASY/NORMAL), `bestdori` (`level`, `notes`), `maa` (`path`, `bestdori_url`, `raw_sha256`) |

### Genres

Genre folders follow the band filter in the game:

- `poppin-party`, `afterglow`, `hello-happy-world`, `pastel-palettes`, `roselia`, `raise-a-suilen`, `morfonica`,
  `mygo`, `ave-mujica`;
- `other` for every one-off unit or collaboration: "Afterglow×香澄", 夢ノ結唱, hololive collaborations,
  CRYCHIC and others.

The `genre` field always holds the real band name. Every current band name is listed in the registry, so that a
collaboration does not slugify into a main band's folder. A band that appears later is listed in the catalog report as
`unmapped_bands`; add it to the registry.

### `notesQuantity` is not the combo

The master's `notesQuantity` is `1000` on 3,281 of 3,441 rows, which is a placeholder. The other 160 rows belong to
songs 1–50. Of those, songs 41–50 all share 64/141/261/397, a template, and only 23 of the stored charts match their
combo. No counting rule reproduces these values: holds counted once or twice, or singles only, all fail. They are
launch-era leftovers, and the game counts the chart itself. So:

- each chart is checked against **Bestdori's** count (`note_counts.combo`). All 3,139 stored charts match it.
- `notesQuantity` is kept in the chart record's source. When it is not 1000 and differs from the combo, the chart
  gets a warning: 137 charts, all from songs 1–50.

## The Bestdori format

A chart is a JSON list of entries. The module docstring of `formats/bestdori.py` has the full mapping. In short:

| Entry | IR |
|---|---|
| `BPM` (beat, bpm) | `BpmChange`. Beat 0 is the start of the audio, so the offset is 0. There is no metre; the chart is 4/4 throughout. |
| `System` (beat, data) | `meta.source.bestdori_system` as `[beat, data]` pairs: `bgm*.wav` (also `meta.audio`) and `cmd_fever_ready/start/end.wav` |
| `Single` (lane, flick?, skill?, charge?) | tap, or flick with `extra.direction: "up"`. `skill` and `charge` are kept in `extra`. |
| `Directional` (lane, direction, width) | flick with `extra.direction` "left" or "right", and `width` in lanes. `lane` is the anchor tile: the rightmost tile for Left and the leftmost for Right. The IR lane is the left edge. |
| `Long` (connections: 2 points) | hold |
| `Slide` (connections, each with flick?, skill?, charge?, hidden?) | slide, whose `path` holds every later point: offset from the head, lane, and easing (`linear` for a judged tick, `control` for a hidden point). A slide that never changes lane becomes a hold with `extra.static_slide`, and its ticks go in `extra.ticks`. |

- **Lanes.** Lanes are 0..6 in all 1,805 mirrored charts. No file uses the old 1..7 numbering. Only hidden slide points
  are fractional (−0.5..6.5). Float noise such as `0.35000000000000003` is rounded to 6 decimals.
- **Flags on a line's points.** A flag on the head is stored as `extra.<flag>`. A flag on the tail is stored as
  `extra.<flag>_end`; `flick_end` is the end flick. A flag on a judged tick is stored as `extra.<flag>_ticks`
  (offsets).
- **`charge`.** These are the fever-chance notes. 45,454 of the 45,466 charge flags lie between `cmd_fever_ready` and
  `cmd_fever_start`.
- **`skill`.** Every chart has 6 skill notes, except one chart, which has 17.
- **Like Bestdori's simulator:**
  - hidden points at either end of a line are trimmed;
  - a line with one visible point is played as a Single, with `extra.line` (53 one-point Slides: 52 in song 786
    SPECIAL, 1 in song 792 EXPERT);
  - a line with no visible point is dropped, with a warning.

### Beats: float → Fraction

The beats in the files are doubles. `exact_beat` applies these steps in order:

1. Use the nearest 1/192 beat, if the double is within 1e-9 of it. This covers 99.99 % of beats.
2. Otherwise, use the fraction with the smallest denominator (up to 1000) that is within 1e-9. These are tuplets that
   come from the game's measure-slot charts. Examples: 336.6 = 1683/5, 290.11764705882354 = 4932/17,
   14.844919786096256 = 2776/187. Denominators seen: 5, 7, 10, 11, 13, 14, 17, 18, 20, 21, 23, 28, 30, 35, 39, 55,
   105, 187.
3. Otherwise, use the smallest-denominator fraction (up to 192) within 1e-6. This handles floats printed with few
   digits, such as 10.333333 → 31/3.
4. Otherwise, use the nearest 1/192 beat, and add a warning with the count and the largest error.

Over the whole mirror, every beat resolves in step 1 or step 2, with errors under 2e-15 beats. No chart has a beat
warning.

### Combo

Each Single and each Directional counts 1. Each visible point of a Long or Slide counts 1: the head, every judged tick
and the tail. Hidden points count 0. This reproduces Bestdori's count on all 1,805 mirrored charts.

- `meta.source.combo` holds the count from the raw entries.
- `formats.bestdori.chart_combo(chart)` recounts it from the IR. The two always agree, so the IR keeps every judged
  point.

### The kanon511 copies are lossy

kanon511 saved its files with the `bestdori-api` Python package's `Chart.json()`. Compared with Bestdori's raw file,
that output has these differences:

- every `Long` is written as `Slide`;
- `System` entries are dropped (no fever events, no audio name);
- `charge` flags are dropped.

Everything else is the same. A sample of 40 ordinary HARD/EXPERT/SPECIAL charts was compared with the mirror: once
those three changes are allowed for, 39 are identical, and the 40th has no kanon511 file. kanon511's downloader also
skipped the "SP rhythm" charts (`enableSpecialNotes`), which are the ones with Directional flicks or hidden points. No
EASY/NORMAL chart is one of those. In the IR, a Long and a static Slide are both holds, so the only things lost are
`static_slide` (which is not set on these copies), `charge` and the System list.
Charts from this source carry the warning `lossy copy (kanon511, ...)` and
`meta.source.extracted_from = "kanon511-bestdori-api"`. Every one of them still matches Bestdori's combo.

## Coverage and gaps (2026-11-11)

| | |
|---|---|
| Songs | 819 (13 removed, 0 unreleased), in 10 genre folders |
| Charts | 3,437: 3,139 `extracted` (1,783 exact from the mirror, 1,356 lossy from kanon511), 298 `listed` |
| Jackets | 812 |

The 298 charts with no source:

- EASY/NORMAL for songs 705–831 (126 songs);
- EASY/NORMAL for 15 older songs that kanon511 skipped:
  - [FULL] 217, 223, 229, 235, 243, 249 and 412;
  - the removed super-hard event songs 486, 487 and 596–598, and 689;
  - 698 (潜在表明) and 702 (愛唄);
- every chart of songs 824–828, 830 and 831, which are newer than the mirror;
- SPECIAL of 650 (TARINAI), which was published on 2026-10-31.

The 7 songs 824–831 also have no jacket and no BPM.

**On the PC** (bestdori.com is reachable there), run:

```
python -m vsrgconv scrape bandori --charts --jackets
```

This fills all 298 charts and the 7 jackets exactly. To replace the 1,356 lossy EASY/NORMAL copies with exact ones,
run it with `--refresh`. That also re-saves the mirrored charts, which come out identical byte for byte. Then run
`catalog bandori` again so that the newest songs get their BPM.

## For conversion

- **EASY uses 5 lanes.** Bandori EASY charts use only lanes 1–5 (672 of 678), so a straight 7K conversion fails the
  "empty column" lint.
- **SPECIAL is not always the hardest chart.** SPECIAL is mapped to `master`, as the top regular chart, but 39 SPECIALs
  are rated below their song's EXPERT and 18 are rated equal.
