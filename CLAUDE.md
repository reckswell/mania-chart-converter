# vsrgconv: working rules for agents

- Read `docs/architecture.md` before changing code and `docs/agent-loop.md` before running the loop (`/iterate`).
- Keep the boundaries:
  - sources produce `Chart`s in source lane units;
  - rules transform `Chart`s and log what they changed;
  - only `osu/writer.py` writes .osu.
- Beats stay `Fraction`s until the writer. Never round time in a reader or rule.
- Conversion rules are decided through `agent/DECISIONS.md` with eval-set metrics, not ad hoc. Keep the `baseline`
  profile working as the reference.
- Tests: `python -m pytest -q`. The demo (`python -m vsrgconv --db /tmp/d demo --out /tmp/o`) must stay lint-clean.
  Never skip or weaken a test to pass.
- Data:
  - commit `data/db` (catalog, extracted `*.chart.json.gz`, jackets shrunk to ≤320 px JPEG);
  - commit `data/games` (definitions of games added with `vsrgconv add-game`, docs/adding-a-game.md);
  - never commit `data/cache` (downloaded pictures, audio), `data/renders` (redrawn by `vsrgconv render`) or `out/`.
- Network (cloud environment "vsrgs", Allowed domains, checked 2026-11-12; the older "Default" environment lacks the
  Hugging Face secret and the newer domains):
  - reachable: GitHub raw files (osu-wiki, otoge-db, MarisaBot), sdvx.in, sdvxindex.com, sdvxindexasset.com,
    textage.cc, bestdori.com, gddata-mdb.selundine.top, remywiki.com, zenius-i-vanisher.com, enchor.us, osu.ppy.sh,
    YouTube, Hugging Face (`*.hf.co`; the token is a network secret on huggingface.co only, so run with
    `HF_TOKEN=placeholder`, docs/hosting.md);
  - bilibili (`*.bilibili.com`) and niconico (`*.nicovideo.jp`). Alpha's rule: search bilibili in Chinese and
    niconico in Japanese;
  - Chunithm notes come from exact .c2s files (docs/sources/chunithm-c2s.md); sdvx.in's pictures are the "true
    source" kept beside them (docs/sources/pictures.md);
  - playtests and anything signed in run on Alpha's PC. Record what is waiting for the PC in `agent/LOG.md`.
- The orthomania game repo (for playtests) is `Reckswell/osu`, cloned on the PC at `Documents\Projects\osu!\ortho`. Its
  viewfield research is in `docs/ortho/research/game-*.md`.
