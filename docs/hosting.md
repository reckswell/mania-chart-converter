# Hosting the database outside one machine

Checked 2026-11-11. Prices and limits change; the links are the sources.

## What there is to host

| Part | Files | Size | Where it lives now |
|---|---|---|---|
| Catalog (`index.json`, `song.json`) | 15,036 | 66 MiB | git (`data/db`) |
| Charts (`*.chart.json.gz`) | 36,839 | 134 MiB | git (`data/db`) |
| Jackets (JPEG, ≤320 px) | 12,694 | 322 MiB | git (`data/db/**/assets`) |
| Layout pictures (`vsrgconv render`) | one per chart with notes | see "Layout pictures" below | `data/renders`, not committed |
| Download cache (full-size pictures, simfiles, audio) | | several GB | `data/cache`, not committed |

On GitHub the repository is about 434 MB packed. GitHub asks for repositories to stay under 1 GB, and strongly under
5 GB, and blocks single files over 100 MiB
([about large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)).
The catalog, charts and jackets fit. Every re-scrape that changes files adds to the history, so the repository grows
over time even when the database does not.

Layout pictures are drawn from the stored charts, so they are always reproducible. They are about 30 KB each, so all
of them together are around 1 GB. That is too much for git, and does not need to be in git: `vsrgconv render all
--workers N` redraws all of them (see the run time below).

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

Once a host is chosen, a `vsrgconv publish` command uploads only what changed since the last upload.
