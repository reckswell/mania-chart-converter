# The agent loop

The converter is built and improved by Claude Code agents working in this repo. Each loop iteration researches,
implements, measures and writes down what it learnt, so the next iteration (often a fresh session with no memory)
starts from the files, not from a conversation.

## Files the loop keeps

| File | What | Who writes |
|---|---|---|
| `agent/BACKLOG.md` | Ordered work items, each with a "done when" check | every iteration (top item taken, new items added) |
| `agent/LOG.md` | One line per iteration: date, item, result, metrics delta, next | every iteration |
| `agent/DECISIONS.md` | Conversion-rule decisions with the evidence behind them | when a rule is decided or changed |
| `agent/metrics.jsonl` | Machine-readable metrics per iteration (see Gates) | `scripts/metrics.py` |
| `docs/research/*.md` | Research the rules cite | researcher |

## One iteration (`/iterate`)

```
            ┌──────────────┐
            │ 1. Pick      │  top BACKLOG item that is not blocked
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │ 2. Research  │  only if the item needs facts we don't have → docs/research
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │ 3. Implement │  smallest change that meets "done when"; tests first for rules
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │ 4. Verify    │  pytest · demo · metrics on the eval set · count check
            └──────┬───────┘
          fail │   │ pass
     ◄─────────┘   ▼
  (fix ≤3 tries) ┌──────────────┐
                 │ 5. Review    │  chart-reviewer reads lint metrics + .osu diffs vs the last iteration
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │ 6. Playtest  │  optional: orthomania harness on the PC (L2), or ask Alpha (L3)
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │ 7. Record    │  LOG line, metrics.jsonl, DECISIONS, new BACKLOG items; commit
                 └──────────────┘
```

Roles are Claude Code subagents in `.claude/agents/`:

- **researcher** answers one question with sources and writes or extends a `docs/research` file. It never edits code.
- **implementer** changes code and tests for one backlog item.
- **chart-reviewer** judges converted charts against `docs/research/osu-mania-mapping.md` §9, using metrics and the
  lint, and returns concrete pattern complaints with beat numbers. Read-only.
- **playtester** runs orthomania's harness on the PC and reads its summaries and the play log. It needs the PC
  (Remote Control session).

The orchestrating session (the one running `/iterate`) holds the backlog and decides. Subagents return findings, not
decisions.

## Gates: when a change is accepted

A change is kept only if all of these hold, measured on the **eval set** (`agent/eval-set.json`: a fixed list of
charts across games, tiers and BPM profiles; it grows as games are added):

1. `pytest` passes.
2. `vsrgconv demo` succeeds with `lint ok`.
3. On the eval set:
   - no new lint errors;
   - extraction count mismatches do not increase;
   - for rule changes, the targeted metric moves the intended way. Examples: jacks the source did not have go down;
     hand balance moves toward 0.5; chord sizes per tier stay inside the doc's limits.
4. For rule changes that alter how charts *feel*, a reviewer pass with no unresolved "high" findings.

If the same check fails three times in a row, the iteration stops, writes what it tried to `agent/LOG.md`, and marks
the backlog item `blocked: <reason>`.

## Self-iteration on rules

Conversion rules are where most iterations will go once extraction works. The pattern is:

1. **Hypothesis.** State it in DECISIONS as proposed, for example: "Chunithm → 8K: map 16 cells by centre, but give
   width ≥ 8 notes a 2-note chord on the halves."
2. **Implement** it as a new rule or profile, without changing the existing ones.
3. **A/B on the eval set.** Run `convert` with both profiles, compare metrics and run the reviewer on both outputs.
4. **Decide.** Keep the better profile as default, record the numbers in DECISIONS, and leave the loser in the code
   only if it is a useful option.

Ground truth for "better" is, in order:

- Alpha's play feedback (L3);
- orthomania harness statistics (L2);
- the reviewer and the metrics.

## What needs the PC

- Downloading from sdvx.in, which the cloud is blocked from (`docs/sources/chunithm-sdvxin.md`).
- Running orthomania's playtest harness (`vsrgconv playtest`, `ORTHO_REPO` set to `Documents\Projects\osu!\ortho`).
- Importing `.osz` into lazer for Alpha to play, and reading `ortho-data\maniaortho\plays.csv` afterwards.

From a cloud session these are done through a Remote Control session on the PC. The loop treats them as steps that
can wait: it keeps working on cloud-side items, and lists what is waiting for the PC at the end of its LOG line.

## Starting the loop

In a Claude Code session in this repo:

```
/iterate            # one iteration on the top backlog item
/iterate 5          # up to five iterations, stopping early on a blocked gate
```
