# beatmania IIDX: catalog and textage links

Module: `src/vsrgconv/sources/iidx.py`. Tests: `tests/test_konami.py`.

The catalog is complete for IIDX 34 ZINRAI, and it also includes removed songs and songs that were never in an arcade
version.

- **No jackets:** IIDX shows no per-song jacket art. Neither Tachi, textage nor any repo found has IIDX jackets.
- **No charts:** the only chart source, textage, refuses this machine. Every chart is `listed`, with its textage URL.

## Sources

| What | URL (`raw.githubusercontent.com/...`) | Coverage (checked 2026-11-11) |
|---|---|---|
| Catalog | `chinimuruhi/IIDX-Data-Table/gh-pages/textage/{song-info,chart-info,title,textage-tag,version}.json` | 2,706 textage entries, 1,981 flagged `in_ac` |
| Game ids, cross-check | `zkldi/Tachi/HEAD/db/seeds/songs-iidx.json`, `charts-iidx-sp.json`, `charts-iidx-dp.json` | 2,613 songs. Version `34`: 1,969 songs and 12,154 N/H/A/L charts. |

IIDX-Data-Table is textage's own song list, scraped from `titletbl.js`, `actbl.js` and `datatbl.js`. Things to know
about it (see its `src/fetch/textage_fetcher.py`):

- **Keys.** The keys are textage's ids, not the game's. When two textage tags share an id, the second one gets
  +100000. That marks a collision, not a duplicate: 101643 `thund28r` collided with 1643, an unrelated song.
- **Version index.** `-1` means never in an arcade version (CS or INFINITAS only), `0` is 1st style, `1` is
  substream, and `n` is version n. Textage itself numbers them -1 substream, 0 consumer only, 1 1st style.
- **Flags.** `in_ac` and `in_inf` are textage's flags for the current arcade version and for INFINITAS.
- **Arrays.** `level` and `notes` are `{sp: [B, N, H, A, L], dp: [B, N, H, A, L]}`, with 0 meaning no chart. One entry,
  月下散刃, has no notes and no BPM.

## Songs

**Grouping textage entries.** Textage keeps one entry per chart set: a CS version, a revival with new charts, or a
corrected tag (PALETTE/pallete, Τέλος, TEKNO KEISER, uən). Entries are grouped into one song by normalised title and
artist. `TITLE_ALIASES` also folds 月下散刃 into 月下散刄.

**Which entry gives the charts.** The entry that is in the arcade wins, then INFINITAS, then the newest version, then
the one with most charts, then the newest tag. The others are kept in `extra.textage_variants` (tag, version, flags,
levels).

**Matching to Tachi.** Each group is matched to a Tachi song in three passes:
1. exact normalised title, with the artist breaking ties;
2. a looser form for spelling variants: ♥/♡, Cyrillic or Greek look-alikes, Ø/0, accents;
3. the same artist with one title a prefix of the other, or close to it.

`TITLE_ALIASES` covers textage's romaji SANA MOLLETTE NE ENTE against the game's katakana title. Result: 2,609
matched.

**Song id.** The id is the game's music id, written with 5 digits as the game's files are (`01000` is 5.1.1.):
- the arcade id that Tachi's version-34 charts use;
- else the oldest arcade id;
- else the INFINITAS id (80000 and up).

All ids are kept in `extra.ingame_ids`. Songs Tachi does not have get `tx-<textage tag>`. These are the 7 newest
ZINRAI songs, Scripted Connection⇒, 炸裂！イェーガー電光チョップ!!, first emotion, and Changes, whose Tachi entry has
no charts. **When Tachi adds the new songs, their ids will change. Re-running `catalog` then leaves the old `tx-`
folders behind, so delete those by hand.**

**Tachi-only songs.** Four songs are added from Tachi alone: the two DistorteD "(recut)" songs, jewelry girl* and
HONEY♂PUNCH ～L.E.D.-G STYLE MIX～.

**Arcade status.**
- `extra.in_arcade` is true for songs in ZINRAI. Every other song has `extra.not_in_arcade = true`.
- `extra.removed = true` marks songs that were in an arcade version once.
- 158 songs were never in an arcade version (CS or INFINITAS only).

Totals: **2,623 songs**:
- 1,976 in ZINRAI. That is the 1,981 `in_ac` entries less 5 duplicate tags, which equals Tachi's 1,969 plus the 7
  new songs.
- 489 removed from the arcade.
- 158 CS or INFINITAS only.

**16,700 charts**, 12,829 of them on ZINRAI songs. That covers every one of Tachi's 12,154 version-34 N/H/A/L charts.

**Folders by version.** IIDX's in-game genre is per song and very fine-grained: 1,482 distinct values over 2,706
entries. So the folder (`SongRecord.genre`, also `version`) is the song's **first arcade version** over all its
entries:
- `1st style`, `substream`, `2nd style` … `10th style`;
- then `11 IIDX RED` … `34 ZINRAI`;
- `INFINITAS` or `CS` for songs never in an arcade version.

The in-game genre is kept in `extra.genre`. Version names come from `version.json`, so new versions name themselves.

## Charts

**Slots.** SP uses the ladder; DP uses `double-<slot>`, the project convention for a second play style.

| Native | SP slot | DP slot |
|---|---|---|
| BEGINNER | easy | double-easy (12 charts exist) |
| NORMAL | normal | double-normal |
| HYPER | hard | double-hard |
| ANOTHER | expert | double-expert |
| LEGGENDARIA | master | double-master |

The native names are `SP ANOTHER`, `DP HYPER` and so on. The registry keys are `spb spn sph spa spl dpb dpn dph dpa
dpl`.

**Fields.**
- `level` is 1–12.
- `note_counts = {"notes": n}` comes from textage, else from Tachi. 16,648 of 16,700 charts have a count.
- `source.textage` is the textage URL. `source.tachi` is `{id, versions}`.

**Checked against Tachi.**
- 39 note-count mismatches and 76 level mismatches. Almost all are on removed songs, where the chosen textage entry is
  an older chart set.
- 14 charts are added from Tachi alone (note "from Tachi only").
- **CS, INFINITAS and omnimix charts.** Textage lists every platform's charts, so 62 charts on arcade songs are not in
  ZINRAI by Tachi's versions. Old CS LEGGENDARIAs are an example. They are kept with a note and listed in
  `extra.charts_not_in_arcade`.

**Skipped.** Tachi's 2dxtra charts (Kiraku, Kichiku, All Scratch) are not standard charts and are left out.

## textage URL

`https://textage.cc/score/<folder>/<tag>.html?<side><diff><level>00`

| Part | Values |
|---|---|
| folder | textage's version: `s` for substream, `0` for consumer-only (CS/INFINITAS) songs, `1` for 1st style, `n` for version n |
| tag | the entry's textage tag |
| side | `1` for SP on the 1P side (`2` would be the 2P side), `D` for DP |
| diff | `B` BEGINNER, `N` NORMAL, `H` HYPER, `A` ANOTHER, `X` LEGGENDARIA |
| level | base 36: 1–9, then `A` = 10, `B` = 11, `C` = 12 |
| `00` | options; every client found uses `00` |

Example: `https://textage.cc/score/34/teknoksr.html?1AC00` is TEKNO KEISER SP ANOTHER 12.

The format comes from three independent clients that build these URLs: katagaki/DJDX (`TextageChart.swift`),
SniperPigeon/azusa-bot (`iidx.py`) and HarroYuz/iidx-chart-viewer (`TextageParser.kt`). The last also links
`score/s/...` and `score/0/...` pages.

**Uncertain:** BEGINNER's `B`. Two clients use it, and the third has no beginner support. It could not be checked
against textage from here.

38 charts have no textage URL because they come only from Tachi.

## Waiting for the PC

Textage is reachable from the PC. A reader for its score pages, which are JavaScript chart tables (see
HarroYuz/iidx-chart-viewer's `TextageParser.kt`), would give exact notes for every chart with a `source.textage`.
