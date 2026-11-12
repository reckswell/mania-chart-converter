# SOUND VOLTEX: catalog, jackets, chart pictures

Module: `src/vsrgconv/sources/sdvx.py`. Tests: `tests/test_konami.py`.

The catalog is complete for SOUND VOLTEX ∇ (nabla, version 7). No chart notes are reachable from the cloud machine, so
every chart is at status `listed`. Where a chart picture exists, its URL is on the chart.

## Sources

| What | URL (all `raw.githubusercontent.com/...`) | Coverage (checked 2026-11-11) |
|---|---|---|
| Catalog backbone | `Kizuruki/sdvxindex/HEAD/songsv1.4.2.json` | 2,306 songs, 8,597 charts, up to 2026-08-20 |
| Catalog cross-check and newest songs | `zkldi/Tachi/HEAD/db/seeds/songs-sdvx.json`, `charts-sdvx.json` | 2,369 songs, 8,840 charts. 2,320 songs and 8,658 charts are in `nabla`, the current arcade version. |
| Older snapshot | `RRRGR/unnamed-sdvxindex-clone/HEAD/songsv1.1.9.json` | 2,161 songs and 7,997 charts. It has the image paths the clone repo actually hosts, and 17 songs that were removed later. |
| Jackets (676 px) | `RRRGR/unnamed-sdvxindex-clone/HEAD/img/<id4>_<ascii>/jk_<id4>_<n>_b.webp` | 2,161 songs, with a variant per difficulty |
| Jackets (108 px, fallback) | `noahm/DDRCardDraw/HEAD/src/assets/jackets/<jacket>`, names from `src/songs/sdvx_nabla.json` | 2,307 songs, with per-difficulty variants |
| Chart pictures | `RRRGR/.../img/<id4>_<ascii>/<id4>_<ascii>_<n><t>_column_rendered.png` | 7,997 charts. These are large and are not downloaded. |

How the catalog is built:

1. Every sdvxindex song is a record. The song id is the game's 4-digit id (`0001`), which is the same as Tachi's
   `inGameID`.
2. RRRGR's songs that sdvxindex lacks but Tachi has are added from RRRGR's rows (same format). There are 17 of these
   (ELECT, the 1446–1458 remixes, 1911, 2035, 2036), all removed from ∇. Tachi's level wins for them, because
   RRRGR's snapshot is older.
3. Tachi's songs that neither has are added from Tachi: title, artist, version and levels only. There are 46. Their
   genre is unknown, so they go in the `unknown` folder.
4. Tachi's charts that a record lacks are added. Five NABLA charts were added to older songs: 0067, 0146, 0175, 0302
   and 0801.
5. A song is current when any of its Tachi charts lists `nabla` in `versions`. Other songs get `extra.removed = true`
   (49 songs).

Results: 2,369 songs (2,320 current, 49 removed) and 8,840 charts (8,658 current). That is 100% of the reference
totals. Levels agree with Tachi on every chart both sources have.

## Difficulty slots

| Native | Slot |
|---|---|
| NOV NOVICE | normal |
| ADV ADVANCED | hard |
| EXH EXHAUST | expert |
| 4th chart: MXM MAXIMUM, INF INFINITE, GRV GRAVITY, HVN HEAVENLY, VVD VIVID, XCD EXCEED, NBL NABLA | master |
| ULT ULTIMATE (5th chart, one song: 0636 Everlasting Message, GRV + ULT) | extra |

- NABLA is the new 4th-chart name on ∇. It is on 8 songs in sdvxindex, and Tachi has 5 more.
- No song has two 4th charts. This was checked over all of sdvxindex and Tachi.
- **Collision rule** (in `place_chart`): if a song ever does have two charts for one slot, the second goes to
  `<slot>-2`, then `-3`, and so on. This is the CHUNITHM WORLD'S END convention. The catalog report counts these as
  `slot_collisions`; today there are 0.
- The tutorial songs 0840, 1219 and 1751 have a single level-1 GRAVITY, HEAVENLY or MAXIMUM chart, on `master`.

## Fields

- **ChartRecord:**
  - `level` is as published: integers, and from 17 up on ∇ a decimal such as `"18.3"`. `constant` is left empty.
  - `designer` is the effector.
  - `note_counts = {chip, hold, tsumami}`. These are **chain counts**, not object counts: hold and tsumami count
    their ticks, and the three add up to the max chain.
  - `source.picture` is the RRRGR column render. `source.sdvxindex_picture` is the same picture on sdvxindex.com
    (not reachable here).
  - `source.jacket` is `{url, base}` when the difficulty has its own jacket.
  - `source.tachi` is the Tachi chart id.
- **SongRecord:**
  - `genre` is the first sdvxindex genre, which names the folder. `extra.genres` keeps all of them; 420 songs have 2
    or 3.
  - `version` is BOOTH, II -infinite infection-, III GRAVITY WARS, IV HEAVENLY HAVEN, VIVID WAVE, EXCEED GEAR or ∇.
    `extra.version_number` is 1 to 7.
  - `title_romaji` and `artist_romaji` are sdvxindex's yomigana romaji, filled only when the title or artist is not
    ASCII.
  - `extra` also holds `ascii` (the game's ascii key), the yomigana, `tachi_versions`, `unlock_flags` (DDRCardDraw:
    omegaDimension, variantgate, otherEvents, jpOnly) and `charts.<slot>` (illustrator, max_exscore, max_chain,
    radar, versions).
- Genre folders: floor, bemani, touhou-arrange, sdvx-original, vocaloid, other (その他), exit-tunes,
  hinabita-banmeshi, pops-anime, and unknown (songs only Tachi has).

## Jackets

- The song jacket is the first chart's (NOVICE) jacket from RRRGR (2,161 songs), else DDRCardDraw's (166 songs).
  That gives 2,327 of 2,369 songs. The other 42 have no jacket in any reachable source: 32 removed songs and 10 ∇
  songs newer than both repos.
- Per-difficulty variants are stored as chart assets named `jacket-<slot>`
  (`assets/jacket-<slot>.jpg`, `rec.charts[slot].assets`).
  - A variant is stored only when its bytes differ from the song jacket from the same source.
  - 2,026 variants are stored: 1,915 from RRRGR and 111 from DDRCardDraw.
  - 113 were identical to the song jacket and were skipped.
  - 29 RRRGR variant files are missing upstream.
- `scrape --jackets` only stores song jackets, so the variants are a separate, resumable step:
  `python -m vsrgconv.sources.sdvx --db data/db --workers 8` (`--offline` re-uses the cache, `--refresh` redoes
  them).
- All images are stored at the database's committed size (`db.IMAGE_MAX_SIZE`, 320 px). In total that is 4,353
  images, about 138 MB.

## Waiting for the PC

- Chart notes. The RRRGR column pictures (`source.picture`, about 2320×786 PNG) would need a reader. There is no
  exact chart file source.
- sdvxindex.com pictures and jackets for the songs RRRGR lacks (`source.sdvxindex_picture`).
