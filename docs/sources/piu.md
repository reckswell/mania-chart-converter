# PUMP IT UP (PHOENIX 2 v1.02.0): catalog, simfiles and jackets

The PIU database (`data/db/piu/`) holds every song live in PUMP IT UP PHOENIX 2 v1.02.0, with every chart: SINGLE,
DOUBLE, HALF-DOUBLE and CO-OP. Songs removed in earlier versions are catalogued too, flagged `extra.removed`. Charts
come from StepMania simfiles (exact text), parsed by `src/vsrgconv/formats/stepmania.py`, the parser DDR uses. The
adapter is `src/vsrgconv/sources/piu.py`.

```
python -m vsrgconv --interval 0.02 catalog piu
python -m vsrgconv --interval 0.02 scrape piu --charts --jackets --workers 8 --progress
python -m vsrgconv stats piu
```

## Sources

All sources are GitHub raw files, so the cloud machine can reach them. Coverage was checked on 2026-11-11.

| What | URL | Coverage |
|---|---|---|
| Catalog | `raw.githubusercontent.com/aaaaaa2493/pumpout-db-updates/HEAD/db/pumpout-phoenix-2-1.02.db` (Pumpout's SQLite database, 9 MB) | Every song, chart, level, stepmaker, category, title and card of every PIU version, with a version history. 675 songs and 5,637 charts are live at PHOENIX 2 v1.02.0; 294 removed songs have 1,930 charts. |
| Charts | `raw.githubusercontent.com/rayden-61/PIU-Simfiles/HEAD/<folder>/<dir>/<file>.ssc` | The StepF2/StepP1 "The Resistance" simfiles: 1,440 files in 17 folders, `01 - 1ST~3RD` to `17 - PHOENIX 2`. They cover 624 of the 675 live songs and 4,332 of the 5,637 live charts. |
| Jackets | `raw.githubusercontent.com/kalinathalie/piu-charts/HEAD/app/assets/thumbs/<slug>.jpg` (256 px), else Pumpout's song card `.../pumpout-db-updates/HEAD/img/<card>` (250 px), else `img_big/<card>` | 664 of the 675 live songs: 603 from kalinathalie, which covers songs up to PHOENIX, and 61 Pumpout cards. Pumpout's repo has cards only for PHOENIX-era songs. 24 of the 294 removed songs have a jacket. Stored copies are JPEGs of at most 320 px (`IMAGE_MAX_SIZE`); `url` keeps the original. |

The list of simfiles is the committed manifest `src/vsrgconv/sources/data/piu_simfiles.json`. It stores one line per
file, with the file's headers and, for each chart, its steps type, description, meter, difficulty, ACTIVE flag,
credit, chart name, computed combo and note count. The catalog is matched from the manifest alone; the adapter fetches
a simfile only to extract its charts, and it does not need `gh`. rayden-61/PIU-Simfiles is a fork, which code search
does not index, so the manifest is enumerated three ways. Each candidate path is then fetched from rayden; paths that
no longer exist are dropped (91 on 2026-11-11):

1. `gh api search/code` over thedaryen/PIU-Simfiles, the indexed copy of the same tree: 1,381 paths. The index has
   few PHOENIX files and no PHOENIX 2 folder.
2. The `ssc_file` metadata of the maxwshen/piu-vis-ss chart JSONs (`public/chart-jsons/050726/`), which name paths in
   snapshots of rayden: 638 paths.
3. Guessed paths (`guess_candidates`) for PHOENIX and PHOENIX 2 songs that neither list covers:
   - arcade songs: `<id> - <Title>/<Title>.ssc` for ids 18000-18400 and 19000-19400 in steps of 5;
   - short cuts: `[Short Cut] <Title>` with ids 18F00-18F99 and 19F00-19F99.

   5,446 guesses found 6 files: 5 PHOENIX 2 songs and PRiMA MATERiA's short cut.

```
python -m vsrgconv.sources.piu build-manifest                       # re-check the paths already listed
python -m vsrgconv.sources.piu build-manifest --code-search --piu-vis --guess
```

## How the catalog is built

1. **Live set.** Each Pumpout `*Version` table records an operation per version: INSERT, DELETE, REVIVE, EXISTS,
   UPDATE or CROSS. A song, chart, card, label or game id is live at PHOENIX 2 v1.02.0 when its latest operation
   among that version's ancestors (`_derived_versionAncestor`) is not DELETE. Ratings, titles, BPMs and categories
   have no operation; their latest row among the ancestors wins. This gives:

   | Mode | SINGLE | DOUBLE | HALF-DOUBLE | CO-OP | Total |
   |---|---|---|---|---|---|
   | Charts | 2,850 | 1,854 | 790 | 143 | 5,637 |

   A count of 676 songs and 5,638 charts includes song 74, "I'll Give You All My Love". Pumpout deleted it in XX
   v1.00.1 but never deleted its chart 3518, which is still live. The adapter follows the song, so the chart is
   counted with the removed songs (`live_charts_of_removed_songs: 1` in the catalog report).
2. **Removed songs.** A song that was in an earlier version and is gone at PHOENIX 2 is catalogued with the charts it
   had in the version just before the one that removed it, plus `extra.removed`, `removed_in` and `revived_in`. Songs
   that were only in branch versions (Prime JE and so on) and never in the mainline are left out.
3. **Simfile.** Candidate files come from two places: files whose folder number equals Pumpout's game id, and files
   whose title, transliteration or folder name equals the song's English or internal title. Titles are compared
   with "- SHORT CUT -", "[Full Song]" and similar markers removed, after folding case and accents.
   - A file must be of the song's cut. The cut comes from `#SONGTYPE`, else from the folder or title.
   - A title match also needs the artist to agree, or at least half the charts to match.
   - When several files qualify, they are ranked by these criteria in order:
     1. exactly matched charts;
     2. matched charts;
     3. the id matches;
     4. the artist agrees;
     5. the newest folder.

     The 14 songs this applies to are listed under `ambiguous`.
4. **Charts.** Each Pumpout chart is paired with one simfile chart (`match_charts`). The mode comes from the steps
   type and description:
   - `pump-single` is S and `pump-double` is D;
   - `pump-halfdouble`, or a description with HALFDOUBLE, is HDB;
   - a description with COOP is CO-OP; its level is the number of players ("DP COOP 3P" is CO-OP x3).

   Only simfile charts that are `#CHARTSTYLE:ACTIVE` (StepF2 marks the current arcade charts) or have a plain
   description take part. A non-ACTIVE chart whose description has HIDDEN, QUEST, PRO, UCS, INFINITY, TRAIN, ... is
   a mission, user or old hidden chart, not the arcade's. The pairing passes run in order:
   1. `level`: same mode and level. Candidates are ranked in this order:
      1. the chart is ACTIVE;
      2. its description is plain;
      3. its credit names Pumpout's stepmaker.

      4,007 charts are paired this way.
   2. `re-rated`: a level Pumpout says the chart had in another version (302 charts). Simfiles often keep an older
      rating.
   3. `near-level`: the nearest left-over ACTIVE chart of the same mode, within 2 levels (23 charts, 17 of them
      HDB). This is a guess. Such a chart carries a warning.
5. **song_id** is the game's song id (`song_id_for`):
   - Pumpout's `gameIdentifier`, e.g. `1617` or `F20`;
   - else the simfile folder's number, if it has the game's id form (3-4 hex digits). Examples: `14C6` for PRIME
     songs Pumpout has no id for, and `1701` for 18 PHOENIX songs where Pumpout has only a placeholder such as
     `17XX_1502`;
   - else `po` + Pumpout's song number (`fallback_id`). This applies to 170 live songs: 114 PHOENIX and all 56
     PHOENIX 2 songs. Pumpout has only placeholders for them (`18XX_0101`). rayden numbers their folders with its
     own sort keys (`19135`, `18044.5`, two folders both `18242.6`), which it renumbers, so those numbers are kept in
     `source.simfile.id` only. If the game's ids for these songs turn up, only their folder names change.

   Ids are unique case-insensitively. Live songs are numbered first, so a removed song never takes a live song's id.

## Database conventions

- **Genre** is Pumpout's category: ORIGINAL, K-POP, WORLD MUSIC, J-MUSIC, XROSS or RISE (the in-game song categories).
  `version` is the mix the song first came in.
- **Slots** are `<style>-<level>`:
  - the style is `single`, `double`, `halfdouble` or `coop`;
  - `-2`, `-3` ... are added for a second chart of the same style and level. No live song has one.

  PIU has no difficulty names, only levels, so `difficulties` in `games/registry.py` is empty. The ladder slot from
  `registry.slot_for_level(level)` is kept in `ChartRecord.source["ladder"]`. CO-OP charts have no ladder slot.
- **ChartRecord** fields:
  - `native`: "S17", "D22", "HDB12", "CO-OP x2";
  - `level`: the PHOENIX 2 level (players for CO-OP);
  - `designer`: Pumpout's stepmakers;
  - `note_counts.combo`: the max combo computed from the simfile (below);
  - `source.pumpout`: `chart_id`, `mode`, labels;
  - `source.simfile`: the simfile chart's index, steps type, description, meter, ACTIVE flag, credit, combo,
    `match` (level / re-rated / near-level) and `fan_transcription`.
- **Song fields:**
  - `extra.cut`: Arcade, Short Cut, Full Song or Remix;
  - `extra.titles`: the Korean and other titles;
  - `extra.artists` and `extra.labels`, e.g. UNLOCK REQUIRED;
  - `source.pumpout`: song number, game id, card and first version;
  - `source.kalinathalie.slug`.
- A chart file's `meta.source.piu_counts` holds `combo`, `rows`, `checkpoints`, `holds`, `notes` and, for CO-OP,
  `combo_p1`, `combo_p2` and so on.

## Combo: what is computed

No public source lists PIU max combos per chart, so `note_counts.combo` is computed by `pump_counts`. It follows
StepMania 5's `pump` game rules, which are meant to play like the arcade:

- one per judged row (a jump counts once);
- one per hold checkpoint row that has no tap. Checkpoints fall on the `#TICKCOUNTS` grid, after the head up to and
  including the end;
- times the `#COMBOS` multiplier in force at the row;
- rows inside warps and `#FAKES`, and StepF2 fake notes, are not judged.

The judged-note part was checked against piu-vis-ss: R.I.P. S22 has 926 notes, Alice in Misanthrope S22 has 1,082,
and Lepton Strike D23 has 874, the same in both. piu-vis-ss models hold ticks its own way, so its hold counts are not
comparable: Lepton Strike D23 has 626 hold ticks there and 842 checkpoints by StepMania's rules.

Only one chart can be checked against the arcade. Beethoven Virus D18 is an INFOBAR TITLE chart whose in-game title
names its judgments: "B.P.M FOLLOWER - Perfect 927 / Great 1 / Miss 2", 930 in all. `pump_counts` also gives 930. No
other chart in the repo states its total.

## Fan transcriptions

Some PHOENIX-era files in the repo are "The Copycat Steps" transcriptions: fans re-creating the arcade charts from
videos, not Andamiro's chart data. Such a chart is flagged in three places:

- `source.simfile.fan_transcription` in the catalog;
- `meta.source.fan_transcription = "The Copycat Steps"` in the chart file;
- a warning in the chart file.

A chart counts as a transcription when "The Copycat Steps" appears in the song or chart credit. Live songs have 43 such
charts:

| Song | Version | Charts flagged |
|---|---|---|
| CALL ME BACK | PHOENIX 2 | 10 of 11 |
| DIE ANOTHER DAY | PHOENIX 2 | 10 of 10 |
| PUPA | PHOENIX | 11 of 15 |
| PRiMA MATERiA | PHOENIX | 7 of 7 |
| Euphorianic (Short Cut) | PHOENIX | 5 of 6 |

Two removed songs also have some.

## Gaps

Extracted live charts, out of all live charts, on 2026-11-11:

| | SINGLE | DOUBLE | HALF-DOUBLE | CO-OP | Total |
|---|---|---|---|---|---|
| Extracted | 2,595 / 2,850 | 1,547 / 1,854 | 53 / 790 | 137 / 143 | 4,332 / 5,637 (76.9%) |

Removed songs add 1,652 extracted charts out of 1,930. The rest stay `listed`, with level, stepmakers and labels.

- **51 PHOENIX 2 songs (522 charts) have no simfile.** rayden has only 5 of the 56 PHOENIX 2 songs: Ghost Bloody Train,
  B3, CALL ME BACK, DIE ANOTHER DAY and L (PIU Edit). The others stay `listed`, with levels and stepmakers.
- **650 HALF-DOUBLE charts are missing from songs that have a simfile.** PHOENIX added HDB charts to many old songs,
  and the simfiles do not have them: the whole repo has only 135 ACTIVE half-double charts. In all, 53 of the 790
  live HDB charts are extracted.
- **133 DOUBLE charts are missing from songs that have a simfile** (42 of them in PHOENIX songs). These are charts
  added or re-made after the file was written.
- `near-level` pairs (23) may be a different chart than the arcade's. `re-rated` pairs (302) are the right chart,
  but possibly an older version of it.
- **11 live songs have no jacket.** Each has no kalinathalie thumbnail, and its Pumpout card is an old one
  (`/img/card/59.png`, ...) that is not in Pumpout's repo. The songs are:
  - B.P Classic Remix 2, Up & Up (Produced by AWAL), Visual Dream II (In Fiction), Conflict;
  - Close Your Eye, Radetzky Can Can, DIGNITY FULL SONG MIX, Canon D FULL Song MIX;
  - Love is a danger zone (try to B.P.M.), Bullfighter's Song, Banya-P Classic Remix.
- The simfiles have no audio. Before a chart is paired with audio, its `#OFFSET` has to be checked against it.

## Format notes (simfiles in this repo)

- All 1,440 files are `.ssc` (one is named `.SSC`). Their 15,646 charts are:

  | Steps type | Charts |
  |---|---|
  | `pump-single` | 8,481 |
  | `pump-double` | 5,841 |
  | `pump-halfdouble` | 928 |
  | `pump-routine` | 256 |
  | `pump-couple` | 136 |
  | `lights-cabinet` | 4 |

  - CO-OP charts are `pump-double` with description "DP COOP <n>P" and meter 99. Their players' notes are `X Y Z` /
    `x y z` (290 charts).
  - Routine charts separate the players' note data with `&` (250 charts).
- 14,927 charts carry their own timing (`#BPMS`, `#STOPS`, `#DELAYS`, `#WARPS`, `#SPEEDS`, `#SCROLLS`,
  `#TICKCOUNTS`, `#COMBOS`, `#FAKES`). The parser's mapping is in docs/sources/ddr.md and the `formats/stepmania.py`
  docstring.
- 6 charts use StepF2's `6`, a hold head: with it read as one, FIESTA EX "Everybody Got 2 Know" S21 has the 948 taps
  and 109 holds piu-vis-ss lists. Whether StepF2 judges that head is not known, so it is kept in `extra.sm_head`.
- 572 charts use StepF2's note syntax `{c|appearance|fake|0}`. Its third field is 1 for a fake: judged-note counts
  match piu-vis-ss only when those notes are dropped.
- Charts that are not in the arcade (QUEST, HIDDEN, UCS, PRO, INFINITY, ...) are not catalogued.
