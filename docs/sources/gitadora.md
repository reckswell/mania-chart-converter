# GITADORA: catalog, jackets, .dtx links

Module: `src/vsrgconv/sources/gitadora.py`. Tests: `tests/test_konami.py`.

The catalog is complete for GITADORA GALAXY WAVE DELTA, the game's own music database, removed songs included. The
chart files (`.dtx`) are on a host this machine cannot reach, so every chart is `listed` with its `.dtx` URL.

## Sources

| What | URL | Coverage (checked 2026-11-11) |
|---|---|---|
| Catalog | `raw.githubusercontent.com/9DYnastY/GD-Data/HEAD/public/mdb/M32_mdb_<n>.bin`, `n` = `latest` in `m32_manifest.json` | n = 11. That is GALAXY WAVE DELTA: its newest songs have first version GF 30 / DM 29, the last entries in GD-Data's version map. 2,118 rows. |
| Jackets and Japanese artists | `raw.githubusercontent.com/Haven-Lau/jacket_search/HEAD/song_metadata.json` + `thumbnails/<name>.webp` (128 px) | 1,528 entries. About 25 of them have no thumbnail file. |
| Full-size jackets | `jacket_urls.txt` in the same repo, RemyWiki URLs | 2,208 URLs. RemyWiki is not reachable here. |
| Level cross-check | `raw.githubusercontent.com/zkldi/Tachi/HEAD/db/seeds/songs-gitadora.json`, `charts-gitadora-gita.json`, `charts-gitadora-dora.json` | 1,612 songs, up to FUZZ-UP |
| Charts | `https://gddata-mdb.selundine.top/<id4>/<g\|b\|d>_<bsc\|adv\|ext\|mst>.dtx` | Not reachable. Which files exist is listed in that host's `mdb.json`, which is also unreachable. |

## The mdb

The mdb is a protobuf. Each song is field 1 of the top message. The field numbers come from GD-Data
`src/lib/mdb-parser.ts`, and `parse_mdb` is a small decoder for them.

| Field | Meaning |
|---|---|
| 1 | music id (zigzag sint32; 44 is the fallback) |
| 38 | title |
| 14 | ASCII title (cut at 15 characters) |
| 18 | ASCII artist |
| 12, 13 | BPM, second BPM |
| 8 | first version, packed `[GF, DM]` |
| 3 | 15 level slots × 100 |
| 41 | `disable_area`, packed, 4 areas |
| 32, 33 | music type, genre code |
| 45, 37, 10, 25 | CLASSIC chart set, remaster, long version, session |

**Level slots.** There are three blocks of five: guitar `0–4`, drum `5–9`, bass `10–14`. Inside a block, slots 1–4 are
BASIC, ADVANCED, EXTREME, MASTER, and slot 0 is unused. This matches GD-Data's `song-normalizer.ts`. Checked
against Tachi, this block order is the best of all six permutations.

**Rows without charts.** 298 of the 2,118 rows have no level at all. They are old ids, all disabled, and are skipped.
That leaves **1,820 songs**.

**Unpublished levels.** 14 new songs carry dummy levels: `1, 2, 3, 4, 5` (0.01–0.05) and `999`. They get
`extra.levels_unpublished = true`. Their charts are listed with an empty `level` and a note.

**`disable_area`.**
- **Removed.** All of areas 0–2 set means deleted from the game. This is GD-Data's rule (`isDeleted`). These songs get
  `extra.removed = true`: 181 songs, leaving **1,639 current** songs. The research figure of "1,639 not flagged
  disabled" is the same number.
- **Area 3 only.** 22 songs have only area 3 set. Almost all are licensed J-pop: innocent world, 人にやさしく, アイドル
  and so on. That suggests a region where the license does not apply, but which region area 3 is is not documented.
  These songs stay current, with `extra.disabled_areas = [3]`.

## Slots

GITADORA has three parts and no unprefixed default.

| Native | Slot |
|---|---|
| GUITAR BASIC / ADVANCED / EXTREME / MASTER | guitar-normal / guitar-hard / guitar-expert / guitar-master |
| DRUM ... | drum-normal ... drum-master |
| BASS ... | bass-normal ... bass-master |

The registry keys are `<part>-bsc|adv|ext|mas`. The level is the published value with two decimals, for example
`"8.65"`. The mdb has no note counts. `source.dtx` is the chart file URL; GD-Data's app adds `?v=<revision>` to it as
a cache-buster.

Totals: **19,497 charts** (17,701 on current songs):
- guitar: 6,620;
- drum: 6,536;
- bass: 6,341.

**Levels against Tachi.** The levels agree exactly with Tachi on only about 36% of the charts both have. GITADORA
re-rates levels between versions, mostly on guitar. Drum levels are usually within 0.05. The mdb is the current
game's data, so it wins. The differences are only counted in the catalog report (`tachi_level_mismatches`).

## Song fields

- **Title.** `title` is field 38. `title_romaji` is the game's ASCII title, but only when the title is not ASCII.
- **Artist.** jacket_search writes artists as the game shows them, with kanji and mixed case, while the mdb has only
  uppercase ASCII. So `artist` comes from jacket_search on an exact title match, else it is the mdb's ASCII artist.
  `artist_romaji` is always the mdb's ASCII artist. Tachi's artists are ASCII too, so they add nothing here.
- **Folders.** GITADORA has no genre folders, so the folder (`genre`, also `version`) is the **first version**, using
  GD-Data's labels:
  - `XG`, `GITADORA` … `GALAXY WAVE DELTA` from the point GuitarFreaks and drummania shared versions;
  - `GF3rd / DM2nd` and similar before that;
  - `GF1st / V` style labels for the few songs that came to each game at different times.

  There are 34 folders. `extra.first_version = {gf, dm}`.
- **Extra fields.** `extra` also holds `title_ascii`, `music_type`, `genre_code`, the flags (`classic`, `remaster`,
  `long`, `session`), `removed`, `disabled_areas` and `levels_unpublished`.

## Jackets

**Matching.** jacket_search entries are matched by title, using NFKC, no accents, Cyrillic and Greek look-alikes,
and the simplified/traditional pairs 麗/丽, 郷/乡, 風/风:
- the artist breaks ties;
- `JACKET_ALIASES` fixes one spelling;
- a title with a trailing edition note, such as "(GITADORA Edition)", "(CLASSIC)", "(Long Version)" or
  "～… edition～", falls back to the base song's jacket. A CLASSIC chart set looks the same as its song.

**Thumbnail URLs.** Thumbnail names are percent-encoded the way jacket_search's app does it (`encodeURIComponent`).
For example `40°C_Desert` and `LUCKY?_STAFF`: the `?` would otherwise start a query string.

**Result.**
- 1,618 songs matched, and **1,593 jackets stored** (1,592 of the 1,639 current songs).
- The 25 matched songs without a jacket have a `song_metadata.json` entry but no thumbnail file upstream. Most are
  2025–2026 songs.
- The rest have no entry at all: CLASSIC sets of songs it lacks, and the newest GW DELTA songs.

**Full size.** 1,564 songs also have `source.jacket_full`, the RemyWiki original, for a machine that can reach it.
The thumbnails are only 128 px.

## Waiting for the PC

- Chart notes: the `.dtx` files at `source.dtx`, plus a DTX reader (see GD-Data's DTX viewer, or fisyher/dtxviewertool).
- Full-size jackets from RemyWiki (`source.jacket_full`).
