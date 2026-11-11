# Chunithm sources: otoge-db and sdvx.in

## otoge-db (catalog)

`https://raw.githubusercontent.com/zvuc/otoge-db/master/chunithm/data/music-ex.json` is one JSON array, one row per
song (about 1,778 rows on 2026-11-11). The fields used are:

- `id` (SEGA's song id) and `catname` (genre);
- `title`, `artist`, `bpm`, `version`, `date_added`;
- per difficulty `bas|adv|exp|mas|ult`:
  - `lev_<d>` (level), `lev_<d>_i` (constant);
  - `lev_<d>_designer`;
  - `lev_<d>_notes_{tap,hold,slide,air,flick}` (published note counts);
  - `lev_<d>_chart_link` (sdvx.in's *old* page path, for example `09/09001mst`);
- WORLD'S END rows: `we_kanji`, `we_star`.

`OtogeDbChunithm` reads it, and `vsrgconv catalog chunithm` writes it into `data/db/chunithm/`. This is reachable from
the cloud build machine.

## sdvx.in (charts as pictures)

sdvx.in (CHUNITHM譜面保管所) has every chart as a hand-drawn picture, not as note data. **The cloud build machine and
the PC's sandbox VM both get HTTP 403 from the egress proxy for sdvx.in**, so nothing below has been fetched live. The
URL scheme comes from two working open-source consumers (beer-psi/chuni-penguin `dbutils/sdvxin.py` and
`chart_view.py`; Trilleo/web `packages/chunithm/src/sources/sdvx.ts`). One song list was read through a web reader to
confirm its JSON shape (`tests/fixtures/sdvxin_songs01_sample.json`).

### Song lists

- `https://sdvx.in/chunithm/_/json/songs<NN>.json`, `NN` = 01, 02, ... (one per folder). A missing folder redirects
  to an HTML page.
- `songs_del.json` (removed songs). `songs_we.json`, `songs_wedel.json`, `songs_wecourse.json`, `songs_weuso.json`
  hold WORLD'S END charts with their own 3-digit ids.
- Row: `id` (5 digits, first two = folder), `title`, `artist`, `genre`, `bpm`, `levels` {bsc, adv, exp, mst, ult},
  per-difficulty counts {tap, hold, slide, air, flick}, `designer`, `title_search_abc` (romaji), `youtube` ids.
- A level written with a leading underscore (`"_4"`) is a chart **sdvx.in has not drawn**. The database marks it
  `not-drawn`.
- WORLD'S END rows have `levels` as a string: `<kanji><stars>`, with `_` again meaning not drawn.

### Picture layers

For chart `<id>` and key `<k>` (bsc, adv, exp, mst, ult; WORLD'S END uses folder `end` and key `end`):

| Layer | URL (WebP now; PNG on the older site) |
|---|---|
| lanes | `/chunithm/<id[:2]>/bg/<id>bg.webp` |
| notes | `/chunithm/<id[:2]>/obj/data<id><k>.webp` (mirrored: `obj/mrr<id><k>`) |
| measure numbers | `/chunithm/<id[:2]>/bg/<id>bar.webp` |

The three layers are the same size. The layout, from Trilleo's notes measured on 1,904 column pairs:

- columns 200 px apart, each a 50 px gutter then a 150 px lane;
- read bottom-up, with 48 rows shared between neighbouring columns;
- each measure's number label in the gutter has its bottom row on the measure line;
- a few charts mix in a half-size older drawing, and some ULTIMA charts lack their own lanes.

The last two are **not handled yet**.

### Reading notes (`src/vsrgconv/imaging/`)

1. `find_layout` checks the column grid in the lanes layer.
2. `lane_strip` / `find_measures` unroll the columns into one strip and find each measure's position.
3. `Timeline` turns strip pixels into beats. A measure's length in beats is inferred from its height relative to the
   median measure: sdvx.in draws a constant height per beat *(verify)*.
4. `read_notes`:
   - classifies opaque pixels by nearest palette colour;
   - labels connected components per class;
   - joins hold and slide bodies cut by taps drawn over them;
   - reads cell range (16 cells) and start and end beats.
5. `count_mismatches` compares per-type counts against the published counts. This is the main accuracy check.

**What is guessed and must be calibrated on a real picture** (`ReaderConfig`):

- the palette;
- the cell grid inside the 150 px lane (auto-detected from the widest notes by default);
- tap thickness and anchor (bottom edge vs centre);
- how AIR arrows sit relative to their ground note;
- slide easing.

BPM changes are drawn as text and are **not read**. Songs with variable BPM get a warning and a single BPM.

### Getting the pictures in

Any of these fills `data/cache/sdvx.in/...` (it mirrors URL paths), after which every command works with `--offline`:

1. **Run on the PC** (recommended). A Remote Control session runs `python -m vsrgconv catalog chunithm` and
   `extract` there with the PC's own network, then commits the extracted `*.chart.json` files (not the pictures).
2. Save the files from a browser into the matching cache paths.
3. Give the cloud environment network access to `sdvx.in` (environment network settings).

Be polite: the fetcher waits 1 s between requests by default. sdvx.in has been known to put a human check in front of
heavy traffic.
