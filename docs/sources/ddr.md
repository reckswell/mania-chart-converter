# DanceDanceRevolution (WORLD): catalog, simfiles and jackets

The DDR database (`data/db/ddr/`) holds every song in DanceDanceRevolution WORLD, with every SINGLE and DOUBLE chart.
Charts come from StepMania simfiles, which are exact text. They are parsed by `src/vsrgconv/formats/stepmania.py`, the
same parser Pump It Up will use. The adapter is `src/vsrgconv/sources/ddr.py`.

```
python -m vsrgconv catalog ddr
python -m vsrgconv scrape ddr --charts --jackets --workers 8 --interval 0.02
python -m vsrgconv stats ddr
```

## Sources

All sources are GitHub raw files, so the cloud machine can reach them. Coverage was checked on 2026-11-11.

| What | URL | Coverage |
|---|---|---|
| Catalog | `raw.githubusercontent.com/noahm/DDRCardDraw/HEAD/src/songs/ddr_world.json` | 1,480 songs, 11,129 charts (SINGLE and DOUBLE). Each song has its version folder; each chart has its level and `maxScore` (the EX score maximum). Some songs also have an in-game genre (586), flags or a jacket name. |
| Ids, counts | `raw.githubusercontent.com/zkldi/Tachi/HEAD/db/seeds/songs-ddr.json`, `charts-ddr-sp.json`, `charts-ddr-dp.json` | 1,450 songs with Konami's `basename`, in-game id and song hash (`ddrSongHash`). `stepCount` is given for 5,796 SINGLE and 4,444 DOUBLE charts. |
| Charts | `raw.githubusercontent.com/blackalice/ddr-dan-tool/HEAD/public/sm/<Folder>/<Dir>/<Dir>.sm` (15 are `.ssc`) | 1,748 simfiles in 21 folders, one per Zenius-I-vanisher arcade pack from 1st to WORLD. They match 1,329 of the 1,480 songs and 9,962 of the 11,129 charts. |
| Jackets | `<Folder>/<Dir>/<Dir>-jacket.png` beside the simfile (512 px PNG), else `raw.githubusercontent.com/noahm/DDRCardDraw/HEAD/src/assets/jackets/<jacket>` (128–200 px) | All 1,480 songs. 1,199 come from simfile folders. 281 come from DDRCardDraw: the 151 songs with no simfile, plus 129 songs in the SN1 and SN2 folders and one A20 song, which have no jacket images. The stored copies are JPEGs of at most 320 px (`IMAGE_MAX_SIZE`); `url` keeps the original. |

The list of simfiles is the committed manifest `src/vsrgconv/sources/data/ddr_simfiles.json`. The adapter needs only
raw files; it does not need `gh`. Rebuild the manifest when the repo changes:

```
python -m vsrgconv.sources.ddr build-manifest            # song-ids.json + `gh api search/code` per folder
python -m vsrgconv.sources.ddr build-manifest --no-code-search
```

The builder works in four steps:

1. It reads the repo's `data/song-ids.json`, which lists 2,141 paths. 393 of them are stale and return 404.
2. It adds the code-search hits for each folder. Today that is 1,748 hits, all already in song-ids.json.
3. It fetches every candidate path and drops the ones that return 404.
4. For each simfile that remains, it stores the title, transliteration, artist, the `//----- song ID: xxxx -----//`
   header (present in 1,239 files) and, for every DDR chart, `[meter, steps, freezes, freeze rows, shock rows]`.
   With these, the catalog can match songs to files offline.

## How the catalog is built

1. **Tachi.** A DDRCardDraw song (`id` is Konami's song hash) is joined to Tachi by `ddrSongHash` (1,358 songs). If
   that fails, it is joined by title and artist to a Tachi song that has no hash (4 songs).
2. **Simfile.** The adapter first looks for a simfile whose `song ID` header equals the Tachi basename (983 songs).
   If there is none, it matches by title, transliteration or folder name (346 songs). Titles are compared after
   folding case, width, accents and look-alike letters, so "TЁЯRA" matches "TËЯRA" and "RЁVOLUTIФN" matches
   "RЁVOLUTIΦN".
   - A title match is rejected if the file's `song ID` belongs to another catalog song. It is also rejected if the
     artist differs and the step counts do not agree. For example, "Happy" by Pharrell Williams is a different song
     from "Happy" by Fischerspooner.
   - When several files match, they are ranked by these criteria in order:
     1. chart counts that agree with Tachi or DDRCardDraw;
     2. levels that agree;
     3. how many of the song's charts the file has;
     4. exact title;
     5. exact artist;
     6. the folder is named like the title. This rules out the `(Chinese)`, `(AC)` and `(CS)` variants in X2.
     7. the newest pack.

     The 10 songs this applies to are listed in the catalog report under `ambiguous`.
3. **song_id** is Konami's song basename, e.g. `albi` for ALBIDA. It comes from Tachi, else from the simfile's
   `song ID` header. The 118 songs that have neither get `h` + the first 10 characters of the song hash, lower-cased
   (`fallback_id`). All 118 are WORLD songs with no simfile, so if a basename for one of them appears later and its id
   changes, only catalog metadata and a jacket move. Two Tachi basenames are garbage (`ʉ\x1a`, `o*,`); ids must match
   `^[a-z0-9]{2,8}$`.

## Database conventions

- **Genre** is the version folder the song was added in, DDRCardDraw's `folder` (for example "DanceDanceRevolution
  A3"). It is the same as `version`. The slugs in `games/registry.py` sort in release order: `01-1st-mix` …
  `19-a3`, `20-world`. DDR shows an in-game genre for only 586 songs; that genre is kept in `extra.ingame_genre`.
- **Slots.** SINGLE charts use the ladder slot. DOUBLE charts use `double-<slot>` and have 8 lanes. DDR has no DOUBLE
  BEGINNER.

  | DDR | SINGLE slot | DOUBLE slot | StepMania name |
  |---|---|---|---|
  | BEGINNER | `easy` | — | Beginner |
  | BASIC | `normal` | `double-normal` | Easy |
  | DIFFICULT | `hard` | `double-hard` | Medium |
  | EXPERT | `expert` | `double-expert` | Hard |
  | CHALLENGE | `master` | `double-master` | Challenge (old files: Hard with description SMANIAC) |

- **ChartRecord** fields:
  - `native`: "EXPERT" / "DOUBLE EXPERT";
  - `level`: the WORLD level;
  - `note_counts.combo`: Tachi's `stepCount`;
  - `source.ddrcarddraw`: `maxScore`, `sanbaiTier` and flags;
  - `source.tachi.id`;
  - `source.simfile`: the simfile chart and its counts.

  A chart file's `meta.source.ddr_counts` holds `steps`, `jumps`, `freezes`, `shocks`, `combo` and `ex_max`.
- **Song extra**: `song_hash`, `flags` (euLocked, unlock, grandPrixPack …), `remywiki`, `flare_category`.

## Step counts: what is checked

Tachi's `stepCount` is DDR's **max combo**. It is not the number of arrows. The combo is made of:

- **steps**: rows with at least one tap or freeze head. A jump counts once.
- **freeze rows**: a freeze jump gives one O.K.
- **shock arrow rows**: one per row, on 4 or 8 panels.

DDRCardDraw's `maxScore` is 3 × the same number, the EX score with every step MARVELOUS and every freeze or shock
O.K. Before this definition was settled, other counts were tried: steps alone matched Tachi on 1,147 of 9,582 charts,
and steps + freeze arrows on 3,922.

On the 9,962 extracted charts:

| Check | Agree | Disagree |
|---|---|---|
| Tachi `stepCount` (9,612 charts) | 9,610 | 2: Get it BASIC (224 vs 225) and I'm Flying Away BASIC (157 vs 159). DDRCardDraw agrees with the simfile in both. |
| DDRCardDraw `maxScore` / 3 (9,962) | 9,800 | 162. Tachi agrees with the simfile on 148 of them, mostly DOUBLE charts with shock arrows, which DDRCardDraw seems to count per side. 14 charts have no Tachi count, so there is no third source. |
| Either source (Tachi first, then DDRCardDraw) | **9,946 (99.84%)** | **16**: the 2 Tachi cases above, plus 14 charts with no Tachi count where DDRCardDraw differs. |

The 14 are all CHALLENGE or DOUBLE, mostly gimmick charts, and 8 of them are `.ssc` files: ACE FOR ACES DSP-EXP,
Continue to the real world? CSP and CDP, DIABLOSIS::Nāga CDP, カゲロウ CDP, リリーゼと炎龍レーヴァテイン CDP,
LOGICAL DASH CDP, MAX 360 CDP, Monsters Den DSP-DIF, Puberty Dysthymia DSP-DIF, Stand Alone Beat Masta CSP,
粛聖!! ロリ神レクイエム☆ CSP, Steps for Victory CSP and CDP. Each chart carries a warning naming both numbers.

In 13 charts the simfile's meter differs from the WORLD level. These charts were re-rated, and the WORLD level is the
one kept.

## Gaps

- **151 songs (1,141 charts) have no simfile.** All of them are WORLD additions: only 140 of the 291 WORLD songs are in
  the repo. They stay `listed` with their levels, published counts and a DDRCardDraw jacket. To list them:
  `python -m vsrgconv stats ddr`, or `jq` over `song.json` files with no `source.simfile`.
- **26 CHALLENGE charts** (13 songs, SINGLE and DOUBLE) were added after the pack their simfile comes from:
  - AFTER THE GAME OF LOVE, Bloody Tears(IIDX EDITION), CUTIE CHASER, FIRE, GENOM SCREAMS, Healing Vision ～Angelic
    mix～;
  - La Bamba, rainbow flyer, Saturday Night Love, THE SHINING POLARIS, DROP THE BOMB, CaptivAte～誓い～, Votum
    stellarum -forest #25 DDR RMX-.
- **407 simfiles are of songs no longer in WORLD** (removed licenses, the X `Xmix` medleys, and so on). They are not
  catalogued. They could be added later as `extra.removed` songs, but their meters are on the old 1–10 scale.
- The simfiles have no audio, and 1,584 of them have no `#OFFSET`. The reader uses 0, as StepMania does, and records
  `offset_given: false` in `meta.source.stepmania`. Before a chart is paired with audio, its offset has to be found.

## Format notes (simfiles in this repo)

- Steps types:
  - dance-single (7,271 charts) and dance-double (5,643) are used.
  - dance-solo, dance-couple, dance-threepanel and techno-single8 also appear; the adapter ignores them.
- Timing in use:
  - `#BPMS`, `#STOPS` (374 files) and `#TIMESIGNATURES` (6 files).
  - The 15 `.ssc` files exist because their charts have their own BPMs and stops. ACE FOR ACES CSP stops for 11.4 s
    at beat 0.
  - No delays, warps, speeds, scrolls or fakes occur.
- Note characters in use: `1 2 3 M`. `//` comments occur inside note data.
- Encoding: UTF-8, 276 files with a BOM. Titles escape `#` and `:` with `\`.
