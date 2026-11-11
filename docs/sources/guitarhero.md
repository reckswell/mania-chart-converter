# Guitar Hero: catalog, official charts, album art

Everything is under `data/db/guitarhero/<game-slug>/`. The adapter is `src/vsrgconv/sources/guitarhero.py`. The
chart readers are in `src/vsrgconv/formats/gh.py` (`.mid` and `.chart`), which uses `src/vsrgconv/formats/midi.py`, a
small Standard MIDI File reader with no dependencies.

## Games and ids

The genre is **the game**, because that is how the games sort their songs. A song that is in several games gets
one record per game, because each game has its own chart. The song id is `<game-slug>-<title-slug>`.

| Slug | Game | Parts in the game |
|---|---|---|
| `gh1` | Guitar Hero | lead guitar |
| `gh2` | Guitar Hero II | lead, co-op lead, bass or rhythm |
| `gh80s` | Guitar Hero Encore: Rocks the 80s | lead, co-op lead, bass or rhythm |
| `gh3` | Guitar Hero III: Legends of Rock | lead, co-op lead, bass or rhythm |
| `gha` | Guitar Hero: Aerosmith | lead, co-op lead, bass or rhythm |
| `ghwt` | Guitar Hero World Tour | lead, bass, drums (vocals not read) |
| `ghm`, `ghsh`, `gh5`, `ghvh`, `ghwor` | Metallica, Smash Hits, 5, Van Halen, Warriors of Rock | lead, bass, drums with Expert+ |
| `ghl` | Guitar Hero Live | 6-fret lead |

Band Hero is in the catalog source too, but it is not a mainline game, so it is left out. It is one line in `GAMES`
if it is wanted.

## Slots

- Lead guitar uses the ladder: EASY `easy`, MEDIUM `normal`, HARD `hard`, EXPERT `expert`.
- Other parts are prefixed: `coop-<slot>` (co-op lead, `PART GUITAR COOP`), `bass-<slot>`, `rhythm-<slot>` and
  `drums-<slot>`. Drums Expert+ (expert plus the 2x-kick notes) is `drums-master`.
- The native name is kept on each ChartRecord, for example `EXPERT`, `BASS EXPERT` or `DRUMS EXPERT+`.
- The catalog lists every slot the game has, even when no chart file exists (status `listed`, with a note). It
  also lists every slot the chosen chart file has.
- GH2, GH80s, GH3 and GHA have bass **or** rhythm as the co-op part. The adapter takes it from the game's own list:
  the `.dta` audio tracks for GH2 and GH80s, and the `.q` `rhythm_track` for GH3 and GHA. When neither says (most
  DLC), the co-op slots are not guessed. `extra.coop_part` is then `"unknown"`, unless the chart file shows the part.
- Co-op lead (`coop-*`) slots are listed only where a chart file has a separate co-op lead track. Only 16 of the
  114 GH2 Deluxe MIDIs have one (`PART GUITAR COOP`); other songs play the lead chart in co-op.
- GH Live: Clone Hero rips carry four difficulties (EASY..EXPERT) of the 6-fret track `PART GUITAR GHL`. The game
  itself names five (Beginner, Casual, Regular, Advanced, Expert), and the rips do not say which four they are, so
  the four rip names are used.

## Where the data comes from

| What | Source | Notes |
|---|---|---|
| Catalog | `raw.githubusercontent.com/rodbove/score-shots/HEAD/lib/songs.json` | `{game: {disc: [{song, artist}], dlc: [...]}}`; on-disc vs DLC gives `extra.dlc` |
| Years, ids, cover flag, co-op part (GH3 .. WoR) | `nsneverhax/neversoft-script-library` `*songlist*.q`, 360 builds (paths in the manifest) | titles are plain text (GH3, GHA, GHWT) or `qs(0x...)` hashes, see below |
| GH2 / GH80s list | `hmxmilohax/Guitar-Hero-II-Deluxe-Unified` `_ark/config/songs_merge.dta`, `songs_80s.dta` | ids, audio tracks (bass/rhythm), `hopo_threshold` |
| GH2 / GH80s charts | the same repo, `_ark/songs/<id>/<id>.mid` | Harmonix's own MIDI |
| Other charts, album art | Clone Hero rip folders on public GitHub (`notes.mid`/`notes.chart`, `song.ini`, `album.png`/`.jpg`) | listed in `src/vsrgconv/sources/data/guitarhero_rips.json` |

### Hashed titles in the Neversoft lists

From GH:WT on, the `.q` song lists write titles, artists and (GHM, GHSH, GHVH) years as `qs(0x...)`. The key is
the CRC-32 of the UTF-16-LE text with the initial value 0xFFFFFFFF and no final inversion, and the text has the `\L`
case marker in front: `qs_key("\\LJam Session") == 0xab6a4722`. So the adapter hashes each catalog title, as
written and in upper, lower and title case, and looks the hash up. It reverses years with a table of
`"\\L, 1900"` .. `"\\L, 2030"`. Most entries also have a plain `year`/`year_num`. WoR's 360 build also carries the
exported lists of the older games (`gh4_songlist.q` = GHWT, `gh4_1` = GHM, `gh4_2` = GHSH, `gh4_3` = GHVH,
`gh4_dlc` = GHWT DLC, `gh5_0` = GH5, `gh5_dlc` = GH5 DLC, `gh5_1` = Band Hero), and those are used too.

### Rip folders (the manifest)

The manifest is built once with `gh`, so the adapter never needs `gh` at run time:

```
PYTHONPATH=src python -m vsrgconv.sources.guitarhero enumerate     # writes src/vsrgconv/sources/data/guitarhero_rips.json
```

1. GitHub code search `filename:song.ini "icon = <icon>"` is run for every GH icon (`gh1`, `gh2`, `gh2dlc`, `gh80s`,
   `gh3`, `gh3dlc`, `gha`, `ghwt`, `ghwtdlc`, `ghm`, `ghsh`, `gh5`, `gh5dlc`, `ghvh`, `ghwor`, `ghwordlc`, `ghl`,
   `ghtv`). Each query has fewer than 1,000 hits, so no `size:` slicing is needed. Forks are not indexed.
2. Each hit's song.ini is read. The hit is kept only if its `icon` really is a GH icon.
3. The tool probes the same folder for `notes.mid`/`notes.chart` and `album.png`/`.jpg`. Binary files are not
   indexed, but they can be fetched by path.
4. Each chart file is parsed, and the parts and difficulties it has are recorded.

Each manifest entry has the repo, folder, notes file, art file, the song.ini fields that matter (name, artist,
album, year, charter, icon, HOPO/sustain/star-power/drum settings, delay, intensities) and the parts. It also lists
the GH2 Deluxe MIDIs with their parts, and the `.q` song lists to read.

**Only official charts give notes.** A rip is used for notes only when its song.ini `charter` (colour tags removed)
names the studio that charted the game: Neversoft, Harmonix, Beenox (Smash Hits), FreeStyleGames (Live),
Vicarious Visions or Budcat. A rip by anyone else can still give the album art, because the picture does not
depend on who charted. The charter is kept as the ChartRecord's `designer`, and the repo is kept in
`extra.source_repo`. Only the instruments the game has count:

- a GH3 rip's drums are fan-made, so they are ignored;
- for every game but Live, a `PART GUITAR GHL` track is a 6-fret conversion, so it is ignored;
- for Live, the 5-fret `PART GUITAR` is a conversion of the 6-fret chart, so it is ignored.

**Matching.** A rip is matched to the game of its icon by normalised title, then by the title without bracketed
parts, then by a close fuzzy title with the same artist. A rip that does not match its icon's game is tried
against every game with the exact title and artist. A rip with no icon is placed only when its charter names one
studio alone and its title and artist match exactly one song in that studio's games.

**Choosing the file per slot.** Each slot (part and difficulty) takes its file on its own:
1. the GH2 Deluxe MIDI first;
2. then the best official rip that has that part and difficulty.

So one song can mix files: lead from one rip and bass from another. Each ChartRecord's `source.file` names the rip.
Rips are ranked by:
1. whether the co-op state is right;
2. title variant;
3. most parts;
4. `.mid` over `.chart`;
5. collection order (`REPO_ORDER`);
6. having art.

The song's `source.rip` is the rip that gives the lead Expert chart. Every rip used is in `source.rips`, and the
official rips not used are in `source.rip_alternatives`. Year and album come from the main rip's song.ini, or else
from any other rip matched to the song.

**Co-op folders.** A `(Co-op)` rip folder (GH2 .. GHA) stores the co-op lead as `PART GUITAR`. This was checked on
GH2 "Free Bird": the co-op folder's lead has 2,150 notes, which is GH2 Deluxe's `PART GUITAR COOP`, while the lead
has 2,154. So that folder's guitar fills the `coop-*` slots, and its bass or rhythm fills the matching slots as
usual.

## Chart rules (formats/gh.py)

These follow TheNathannator/GuitarGame_ChartFormats (`docs/Chart-File-Formats/...`,
`docs/Implementation-Specific/Guitar-Hero-1-and-2/...`) and YARG.Core (`MoonscraperChartParser/IO/Midi/MidReader*.cs`,
`IO/Chart/ChartReader.cs`, `Song/Entries/Ini/SongEntry.IniBase.cs`).

- **Time.** A beat is `tick / resolution`, as an exact Fraction. BPM is 60,000,000 / µs per quarter (.mid) or
  `B` / 1000 (.chart). Time signatures come from the file. The song.ini `delay` (ms, higher = later notes) and the
  .chart `Offset` (s) give `TimingMap.offset_ms`.
- **Notes.** 5-fret notes are on lanes 0..4 (green, red, yellow, blue, orange). The .mid ranges are easy 60–64,
  medium 72–76, hard 84–88 and expert 96–100. A chord is several notes at one beat. An open note is a single note
  across the highway: lane 0, width 5, `extra.open`. It comes from the Phase Shift SysEx (`PS\0 00 <diff> 01`),
  from note base−1 after `[ENHANCED_OPENS]`, or from .chart `N 7`. GH Live 6-fret notes are on lanes 0..5 (black
  1–3, then white 1–3); its open note is lane 0, width 6.
- **Sustains.** In .mid, a note shorter than the sustain cutoff is a tap (`NoteKind.TAP`). The cutoff is
  `resolution / 3` ticks, a 1/12 note, which is a third of a beat. This is not 1/12 of a beat. song.ini
  `sustain_cutoff_threshold` overrides it, but in .mid never below `resolution / 12` ticks. Many rips set it to 0,
  and their 1–5 tick note-ons would otherwise all become holds. GH1/GH2 game files use 160 ticks at 480. A .chart
  keeps every sustain longer than 0. A longer note is a `HOLD` with `duration`.
- **HOPOs.** A single note is a natural HOPO when all of these hold:
  - it is within the threshold of the previous note or chord (inclusive);
  - it is on a different fret from the previous single note;
  - in .mid only, it is not one of the previous chord's frets.

  Chords are never natural HOPOs. The threshold is:
  - .mid: `resolution/3 + 1` ticks;
  - .chart: `resolution × 65/192`;
  - GH1/GH2 game files: Harmonix's 170 ticks at 480 (or the `.dta` `hopo_threshold`, 250 for some songs);
  - song.ini `hopo_frequency`, `eighthnote_hopo` or `hopofreq` replace it, with YARG's +1 / +res/192 leniency.

  Forced HOPO and forced strum markers override the natural state. In .mid they are base+5 and base+6 (expert
  101/102), and a marker does not cover its own end tick. In .chart, `N 5` flips the natural state. A tap (.mid note
  104, which applies to every difficulty, or the Phase Shift SysEx `04`, whose end tick counts; .chart `N 6`)
  overrides both. In `extra`:
  - every guitar note that is not a strum has `hopo: true` or `tap: true`;
  - a note whose type came from a marker has `forced: "hopo" | "strum"`.
- **Star power.** In .mid, note 116 marks it for every difficulty. GH1/GH2 game files mark it per difficulty on
  base+7 (67/79/91/103). This layout is detected when a file has no 116 and has 67/79/91. A file with no 116 at all
  treats 103 as legacy star power; otherwise 103 is a solo. song.ini `multiplier_note`/`star_power_note` force the
  choice. In .chart, star power is `S 2`. Notes inside a phrase (start included, end excluded) get
  `extra.star_power`. Notes in a solo get `extra.solo`, and .chart `soloend` includes its own tick.
- **GH1/GH2 game files.** Notes 10 ticks or less apart are snapped into one chord at the earliest tick, as the
  games do. The count is reported as a chart warning. GH1/GH2 have no forcing markers, so base+5 and base+6 are
  not read there.
- **Drums.** 4-lane drums are red, yellow, blue, green on lanes 0..3. 5-lane drums are red, yellow, blue, orange,
  green on lanes 0..4. The file is 5-lane when song.ini `five_lane_drums` says so, or when the 5-lane green (base+5,
  .chart `N 5`) is used.
  - The kick is a full-width note with `extra.kick`.
  - Expert+ adds the 2x kicks (.mid 95, .chart `N 32`), which get `extra.double_kick`.
  - Accents and ghosts (velocity 127/1 after `[ENABLE_CHART_DYNAMICS]`; .chart 34–38/40–44) go in `extra`.
  - With `pro_drums`, 4-lane yellow, blue and green are cymbals unless a tom marker (110–112) covers them.
- **Not read.** Vocals, practice sections (kept in `GhSong.sections`), animation and venue tracks, trill and
  tremolo lanes, BREs, face-off phrases, and GH1/GH2's "chords share one length" rule. Each note keeps its own
  sustain.

## Coverage

Scrape of 2026-11-11 (`catalog guitarhero`, then `scrape guitarhero --charts --jackets`):
- 1,720 songs and 15,065 chart slots;
- 5,006 charts with notes (`extracted`), 10,059 slots `listed` only;
- 531 jackets;
- charts_failed 0.

"Songs with notes" means at least one slot has notes. Every one of those songs also has lead Expert.

| Game | Songs | DLC | Songs with notes | Charts with notes / slots | Art | Year |
|---|---|---|---|---|---|---|
| `gh1` | 47 | 0 | 13 | 52 / 188 | 13 | 12 |
| `gh2` | 98 | 24 | 77 | 684 / 768 | 83 | 77 |
| `gh80s` | 30 | 0 | 30 | 240 / 240 | 3 | 3 |
| `gh3` | 140 | 67 | 72 | 558 / 953 | 73 | 101 |
| `gha` | 41 | 0 | 24 | 201 / 345 | 24 | 41 |
| `ghwt` | 244 | 158 | 61 | 738 / 2,934 | 71 | 233 |
| `ghm` | 52 | 0 | 32 | 399 / 676 | 37 | 52 |
| `ghsh` | 48 | 0 | 8 | 100 / 624 | 17 | 48 |
| `gh5` | 249 | 164 | 70 | 847 / 3,237 | 76 | 238 |
| `ghvh` | 47 | 0 | 34 | 397 / 611 | 35 | 47 |
| `ghwor` | 177 | 84 | 63 | 766 / 2,301 | 62 | 126 |
| `ghl` | 547 | 312 | 6 | 24 / 2,188 | 37 | 7 |
| total | 1,720 | 809 | 490 | 5,006 / 15,065 | 531 | 985 |

Size on disk:
- charts: 29 MB gz;
- jackets: 14 MB, JPEG of 320 px or less;
- `song.json`: 7 MB.

Gaps:
- **GH1.** There is no GH1 game-file repo. The only official GH1 MIDIs found are 13 Harmonix rips, and GH1 has no
  `.q`/`.dta` list for years.
- **GH Live.** Only 6 FreeStyleGames rips are public on GitHub. GH TV (the 312 "DLC" songs) is almost all missing,
  and so are its years.
- **Smash Hits.** 8 of 48 songs: Beenox rips are rare.
- **DLC.** DLC for GH3, World Tour, GH5 and WoR is mostly missing. Most public rips are on-disc songs.
- **GH80s art.** 3 of 30: its notes come from GH2 Deluxe, which has no album art, and few rips exist.
- **Not placed.** 21 manifest rips are not placed:
  - Nine Inch Nails "March of the Pigs" is tagged `gh5`, but it is in no GH5 list;
  - 2 RiffRealmsVR folders have a song.ini with no name, artist or charter;
  - 18 are GH2 tutorial lessons, in two copies.
