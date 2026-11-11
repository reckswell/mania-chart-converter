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
  - commit `data/db` (catalog, extracted `*.chart.json.gz`, jackets shrunk to ≤512 px);
  - never commit `data/cache` (downloaded pictures, audio) or `out/`.
- Network: the cloud machine cannot reach sdvx.in, osu.ppy.sh or YouTube. GitHub raw files (osu-wiki, otoge-db,
  MarisaBot) work. Chunithm notes come from exact .c2s files (docs/sources/chunithm-c2s.md); sdvx.in is the fallback.
  Work that needs those sites runs on Alpha's PC (see docs/sources/chunithm-sdvxin.md). Record what is waiting for
  the PC in `agent/LOG.md`.
- The orthomania game repo (for playtests) is `Reckswell/osu`, cloned on the PC at `Documents\Projects\osu!\ortho`. Its
  viewfield research is in `docs/ortho/research/game-*.md`.
