# Keeping the database current

`vsrgconv update` adds the songs and charts the games released since the last run, and fetches only those. Run it by
hand, on a schedule on the PC, or let the GitHub Actions workflow run it every day (below).

```bash
vsrgconv update                          # every game: check, rebuild changed catalogs, fetch new charts and jackets
vsrgconv update chunithm sekai           # some games
vsrgconv update --check                  # which games' sources changed since the last run; writes nothing
vsrgconv update --all --publish Reckswell/vsrgs   # also pictures, and upload what changed to the dataset
```

## What a run does

For each game:

1. **Check.** The files the game's catalog read last time (song lists, master data, ...) are downloaded again and
   compared by sha256 with the hashes in `data/db/<game>/update.json`. If none changed, the game is up to date: its
   catalog is not rebuilt and nothing is written. A game with no `update.json` yet (its first run) is always rebuilt,
   and so is a game whose catalog reads no files (a folder source, docs/adding-a-game.md).
2. **Catalog.** The catalog is rebuilt from fresh downloads (every file once per run, whatever `data/cache` holds)
   and compared with the database before it is merged in. The report lists songs the database did not have, charts
   new to a song already there, songs newly flagged removed from the game, and songs whose details changed (levels,
   chart constants, ...). A new song's `extra.first_seen` is the date of the run that added it.
3. **Notes and jackets** for the new songs and charts, with the same code as `vsrgconv scrape`. Chart files often
   appear some days after a song is released, so a chart a source was asked for but did not have yet stays
   **pending** in `update.json` and is looked for again on every run for 30 days after it was first seen
   (`--retry-days`). Charts no source knows (nothing is even asked for, such as SDVX or IIDX notes) are not kept.
   The first run of a game looks once for every chart without notes, so charts that were waiting for a source before
   the updater existed become pending too. `--retry-all` looks for every chart without notes again.
4. **Pictures** (`--render`, `--pictures`, or `--all` for both):
   - layout pictures (`vsrgconv render`, AR 9) of the charts stored in this run, into `data/renders`;
   - source pictures (`vsrgconv pictures`) of the new charts, and for Chunithm and Project SEKAI every chart sdvx.in
     has drawn since the last run, into `data/pictures`. A picture already in the manifest counts as done even if
     the file is not on this machine. A machine without `data/pictures` (a CI runner, a fresh cloud session) reads
     the manifests from the dataset given with `--publish` or `--hub`, so it downloads only new pictures; without
     either, source pictures are skipped rather than started over.
5. **Publish** (`--publish <user>/<dataset>`): the files this run wrote go to the Hugging Face dataset: the changed
   song folders, `index.json`, `update.json`, the changelog, the new layout and source pictures and the picture
   manifests. Only those paths are compared with the Hub, so a run does not list or hash the whole dataset. The
   token and network notes in docs/hosting.md apply (`HF_TOKEN=placeholder` in cloud sessions).

Then `data/db/CHANGELOG.md` gets a section for the day, newest first: per game, how many songs and charts were
added, how many charts got notes, and the new songs' titles. `update.json` and the changelog are written only when
something changed, so a run that finds nothing leaves `git status` clean. The full report is printed as JSON
(`--report FILE` keeps it).

Exit status: 0, or 1 when a game could not be updated (its source failed); the other games are still updated.

## Where new charts come from

| Game | Watched for new songs | Notes for new charts | Automatic? |
|---|---|---|---|
| Chunithm | otoge-db `music-ex.json`, sdvx.in song lists | MarisaBot's `.c2s` files, usually some weeks after release (pending until then) | yes |
| Project SEKAI | Sekai-World master data | the decrypted SUS files | yes |
| BanG Dream! | esterTion master data, Greatdori, MAA manifest | MAA's Bestdori copies, bestdori.com's API, then the lossy kanon511 copy | yes |
| SOUND VOLTEX | sdvxindex.com's own newest list (named in its script bundle), Tachi | none (no reader); jackets, and sdvxindex's chart pictures with `--pictures` | catalog, jackets, pictures |
| beatmania IIDX | the textage data table, Tachi | none reachable | catalog only |
| GITADORA | GD-Data's GALAXY WAVE DELTA (m32) music database, Tachi | none reachable | catalog and jackets; a new game version needs the next manifest name |
| Arcaea | the game's `songlist` | `.aff` files listed in `sources/data/arcaea_aff.json` | catalog; new charts need `python -m vsrgconv.sources.arcaea build-manifest` |
| DDR WORLD | DDRCardDraw, Tachi | simfiles listed in `sources/data/ddr_simfiles.json` | catalog; new charts need the DDR `build-manifest` |
| Pump It Up | Pumpout's PHOENIX 2 **v1.02** database | simfiles listed in `sources/data/piu_simfiles.json` | no: a new game version needs `TARGET_VERSION` and the database file name changed, then `build-manifest` |
| Guitar Hero | score-shots and the Neversoft song lists | the rip manifest | the series is finished |

The `build-manifest` commands list files through GitHub code search (`gh`), which the cloud sessions cannot reach
for other repositories; run them on the PC. agent/BACKLOG.md has the item for moving them into `update`.

Chunithm charts with no `.c2s` are not read from sdvx.in's pictures: the reader is not calibrated on real pictures yet
(agent/BACKLOG.md items 0 and 1), and a chart stored from it would count as extracted, so the exact `.c2s` would never
replace it. `SDVXIN_READER_READY` in `sources/chunithm.py` turns it on when it is ready.

## Running it on a schedule

### GitHub Actions (every day)

`.github/workflows/update-database.yml` runs `vsrgconv update` every day at 04:17 JST on GitHub's machines, which
reach every source without the cloud environment's domain list. It commits the changes to `data/db` on the branch it
runs on ("Database update 2026-11-14: chunithm +3 songs ..."), shows the changelog section in the run's summary and
keeps the JSON report as an artifact. GitHub runs scheduled workflows from the default branch only, so it starts once
this is merged into `main`. It can also be started by hand (Actions → Update database → Run workflow), with games
and options such as `--force` or `--retry-all`.

Settings (repository Settings → Secrets and variables → Actions):

- secret `HF_TOKEN`: a Hugging Face token with write access to the dataset. With it, the workflow runs `--all
  --publish`, so new pictures are made and everything new is uploaded. Without it, only the database in git is
  updated.
- variable `HF_DATASET` (optional): the dataset, `Reckswell/vsrgs` by default.

A private repository has 2,000 free Actions minutes a month; a day with nothing new takes a few minutes, mostly
checking out `data/db`.

### On the PC (Task Scheduler)

From the repository folder, once a day:

```powershell
schtasks /Create /SC DAILY /ST 05:00 /TN "vsrgconv update" /TR "cmd /c cd /d C:\path\to\mania-chart-converter && python -m vsrgconv update --all --publish Reckswell/vsrgs > data\update.log 2>&1"
```

Commit `data/db` afterwards (or let the workflow do it and pull). Runs on the PC keep `data/cache`, `data/renders`
and `data/pictures`, so the manifests are local and nothing is downloaded twice.

### From a cloud session

`HF_TOKEN=placeholder vsrgconv --interval 0.2 update --all --publish Reckswell/vsrgs --workers 4` in the `vsrgs`
environment (docs/hosting.md: one network secret on huggingface.co, `*.hf.co` allowed), then commit and push
`data/db`.
