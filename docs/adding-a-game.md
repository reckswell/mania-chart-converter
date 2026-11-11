# Adding a game

The framework has ten built-in games (`vsrgconv games`). Any other VSRG can be added without changing the package:
a definition file says what the game is, and a source says where its charts come from.

## See what is there

```bash
vsrgconv games              # added games: lanes, status, what their source can fetch, songs and charts stored
vsrgconv games --all        # also other known VSRGs that are not added yet, with their chart format
vsrgconv games maimai       # one game in full: difficulties, genres, source, database coverage
```

The known-but-not-added list is `src/vsrgconv/games/candidates.json`. It gives each game's lanes, difficulty names on
the ladder and chart format. `readable: true` means a reader for that format exists already, so the game can be
added from a folder of chart files with no code. Most of it is from general knowledge; check the facts when adding a
game (`checked` lists what was verified, and when).

## Add one

```bash
# a known VSRG whose charts the framework can read: point it at the files
vsrgconv add-game itg --folder "D:/Games/ITGmania/Songs" --format stepmania
vsrgconv catalog itg
vsrgconv scrape itg --charts --jackets
vsrgconv render itg

# any other game: describe it, and write an adapter
vsrgconv add-game mygame --name "My Game" --lanes 6 --difficulty Normal=normal --difficulty Lunatic=extra --adapter
```

`add-game` writes `data/games/<key>.json` (and `data/games/<key>.py` with `--adapter`). Commit both, like `data/db`.
Every `vsrgconv` command loads the definitions in `data/games` (another folder: `--games`), in each folder listed in
`$VSRGCONV_GAMES`, and from installed packages that declare a `vsrgconv.games` entry point.

### The definition file

```json
{
 "key": "itg",
 "name": "In The Groove / ITGmania",
 "lanes": 4,
 "variable_width": false,
 "difficulties": {"beginner": "easy", "easy": "normal", "medium": "hard", "hard": "expert", "challenge": "master",
                  "double hard": "double-expert"},
 "native_names": {"challenge": "EXPERT"},
 "genres": {"Fixture Pack": "fixture-pack"},
 "status": "in-progress",
 "chart_format": "stepmania",
 "source": {"type": "folder", "folder": "D:/Games/ITGmania/Songs", "format": "stepmania"}
}
```

- `key`: lower-case letters, digits, `-` or `_`. It names the database folder. A built-in game's key cannot be reused.
- `lanes`: the width of the play surface in the game's own lane units (16 for Chunithm, 4 for DDR).
- `difficulties`: native name (lower-cased) → ladder slot. Slots are `easy normal hard expert master extra special`,
  with a prefix for a second play style or part (`double-expert`, `bass-hard`). See
  docs/research/vsrg-mechanics.md, "Cross-game difficulty ladder".
- `genres`: native genre → folder name. Genres not listed are slugified.
- `source`: optional. A folder source reads chart files from disk; a relative folder is relative to where `vsrgconv`
  runs (the repository root).

### Folder sources (no code)

Every folder that holds chart files is one song; when songs sit one level down (`Songs/<pack>/<song>/`) the pack is the
genre. Formats with a reader:

| `format` | Files | Title and artist |
|---|---|---|
| `stepmania` | `.ssc`, else `.sm`: every chart of the song | from the file |
| `c2s`, `aff`, `bestdori` (`.json`) | one file per difficulty, named by it (`master.c2s`; Arcaea's `2.aff`) | `song.json`, else the folder name |
| `sus` | one file per difficulty | `#TITLE`/`#ARTIST`, else as above |
| `gh` | `notes.chart` or `notes.mid` (+ `song.ini`): every part and difficulty | `song.ini`, else the .chart `[Song]` |

`song.json` in a song folder can give `title`, `artist`, `title_romaji`, `artist_romaji`, `genre`, `bpm` and `levels`
(`{"master": "13+"}`). A picture whose name contains jacket, cover, album, banner or bg becomes the jacket. The code
is `src/vsrgconv/sources/local.py`.

### Adapters (code)

For a game whose charts are online or in a format with no reader yet, `--adapter` writes a stub with three functions
to fill in: `catalog` (every song and difficulty), `chart` (one chart's notes as a `Chart`) and `jacket`. They work
like the built-in adapters in `src/vsrgconv/sources/`; the contract is in `sources/__init__.py`. A new chart format
gets a reader in `src/vsrgconv/formats/` with a small real file as a test fixture; once it is there, add the format
to `FORMATS` in `sources/local.py` so folders of that format need no code either.

Built into the package instead (for a game that is here to stay): add its `Game` to `games/registry.py` and its
adapter to `sources/<game>.py`, with fixtures, tests and `docs/sources/<game>.md`, as the ten built-in games do.

Conversion rules for a new game come after, through the agent loop (`docs/agent-loop.md`).
