# Hosting the database outside one machine

Checked 2026-11-11. Prices and limits change; the links are the sources.

## What there is to host

| Part | Files | Size | Where it lives now |
|---|---|---|---|
| Catalog (`index.json`, `song.json`) | 15,036 | 66 MiB | git (`data/db`) |
| Charts (`*.chart.json.gz`) | 36,839 | 134 MiB | git (`data/db`) |
| Jackets (JPEG, ≤320 px) | 12,694 | 322 MiB | git (`data/db/**/assets`) |
| Layout pictures (`vsrgconv render`) | 36,839, one per chart with notes | 2.0 GiB | `data/renders`, not committed |
| Download cache (full-size pictures, simfiles, audio) | | several GB | `data/cache`, not committed |

On GitHub the repository is about 434 MB packed. GitHub asks for repositories to stay under 1 GB, and strongly under
5 GB, and blocks single files over 100 MiB
([about large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)).
The catalog, charts and jackets fit. Every re-scrape that changes files adds to the history, so the repository grows
over time even when the database does not.

Layout pictures are drawn from the stored charts, so they are always reproducible. They follow sdvx.in's layout,
spaced as at osu! approach rate 9 (README). They average 20 KB (DDR) to 115 KB (Chunithm) each, 2.0 GiB in all; long
Guitar Hero songs reach 16,212 x 9,714 px. That is too much for git, and does not need to be in git: `vsrgconv render
all --workers 4` redraws all of them in about 40 minutes on a 4-core machine, and only draws pictures that are
missing unless given `--refresh`. (The older compact layout, `--compact`, was 842 MiB.)

## The repository is public

`reckswell/mania-chart-converter` is a public repository. It holds charts and jacket art whose rights belong to the
games' publishers. Publishers do send takedowns for this: the Arcaea chart repository this project reads from was taken
down by a DMCA notice from lowiro in April 2026. Since the database is for personal use, the repository should be
private: on GitHub, Settings → General → Danger Zone → Change repository visibility. A private repository keeps
everything else working (the cloud sessions and the PC both clone it with Alpha's GitHub access).

## Options

| Option | Free | Beyond free | Private | Browse pictures in a web page | Notes |
|---|---|---|---|---|---|
| GitHub repository (as now) | yes | | after the change above | yes, one file at a time | Best place for the catalog and charts: versioned, diffable, already set up. |
| [Git LFS on GitHub](https://docs.github.com/en/billing/concepts/product-billing/git-lfs) | 10 GiB storage and 10 GiB download a month | paid | with the repository | yes | Every clone downloads LFS files, which uses the monthly allowance quickly. |
| GitHub Release assets | no total limit | | with the repository | no (zip files) | Good for a zip of pictures per game. Each upload replaces the whole zip. |
| [Cloudflare R2](https://developers.cloudflare.com/r2/pricing/index.md) | 10 GB-month, 1M writes and 10M reads a month, downloads free | $0.015 per GB-month | yes (S3 keys) | no, needs a viewer or public links | Cheapest object storage for many downloads. |
| [Backblaze B2](https://www.backblaze.com/pricing.md) | first 10 GB | $6.95 per TB-month; downloads free up to 3× what is stored | yes | no | S3-compatible, like R2. |
| [Hugging Face dataset](https://huggingface.co/docs/hub/storage-limits.md) | 100 GB private storage | 1 TB with PRO | yes | yes, the file browser shows images | Git-based. Advice: under 100k files per repository and under 10k per folder; the database's folders per game and genre stay under that. |

## Recommendation

1. Make the GitHub repository private (Alpha, in the GitHub settings).
2. Keep the catalog, charts and jackets in git, as now. If the repository passes about 1 GB, move the jackets out first.
3. Do not commit layout pictures. Redraw them with `vsrgconv render` on any machine that has the repository.
4. For pictures that can be opened from anywhere (a phone, a PC without the repository), mirror `data/db` and
   `data/renders` to a private Hugging Face dataset. It is free at this size, and its file browser shows the pictures
   with no viewer to build. Cloudflare R2 is the alternative when the pictures should be served to a program (a
   viewer, a bot) rather than browsed.

Uploading needs:

- an access token for the chosen host, saved as a secret in the cloud environment or on the PC
  (`HF_TOKEN`, or `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_ACCOUNT_ID`);
- network access to the host. The cloud environment currently reaches only GitHub, so either add the host
  (`huggingface.co`, or `<account>.r2.cloudflarestorage.com`) to the environment's allowed domains, or upload from the
  PC.

For the Hugging Face option, `vsrgconv publish` does the upload (`pip install -e '.[hosting]'` first). Alpha's
dataset is `Reckswell/vsrgs` (private):

```bash
vsrgconv publish --dry-run Reckswell/vsrgs                # what would go up: files and bytes per folder
vsrgconv publish Reckswell/vsrgs --parts db,games         # the database (64,569 files, 522 MiB)
vsrgconv publish Reckswell/vsrgs --parts renders          # the layout pictures (36,839 files, 2.0 GiB)
```

A dataset is created private if it does not exist. Uploads skip files already on the Hub and resume after an
interruption; `--delete` also removes files that are gone locally. Together the parts are about 101,400 files, just
over the 100,000 Hugging Face advises per repository; if the Hub complains, put the pictures in a second dataset.

### From a cloud session

The cloud environment reaches Hugging Face through a network secret that adds the token to requests:

- there must be one secret, on `huggingface.co` only. Uploads go to Hugging Face's Xet storage (`*.hf.co`) with a
  short-lived token of their own; if any secret covers `*.hf.co`, the proxy replaces that token and every upload
  fails with 401. Add `*.hf.co` under Allowed domains instead;
- the token comes from the proxy, but `huggingface_hub` wants one locally: run with `HF_TOKEN=placeholder`;
- until the secret is fixed, `publish --no-xet` uploads through Git LFS, which works with the secret on `*.hf.co`.
  It is slow: Hugging Face rate-limits LFS to about 1,000 files per 5 minutes per account, about 8 hours for the
  whole database and pictures. It commits 1,000 files at a time and skips files already on the Hub, so it can be
  stopped and run again, and a later run without `--no-xet` carries on from where it stopped;
- the proxy gives up on an answer after 30 seconds, and Hugging Face can take longer to answer a large commit, so the
  client sees a 502 although the commit landed. `publish` re-runs the upload while the dataset keeps advancing, and
  the re-run skips what is already there.
