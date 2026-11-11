---
description: Run the vsrgconv agent loop for N iterations (default 1)
argument-hint: "[iterations]"
---
Run the agent loop described in `docs/agent-loop.md` for $ARGUMENTS iteration(s) (1 if empty).

For each iteration:

1. **Pick.** Read `agent/BACKLOG.md` and take the top item not marked `blocked`. Read the last 10 lines of
   `agent/LOG.md` and `agent/DECISIONS.md` for context.
2. **Research** (only if the item needs facts we don't have). Use the `researcher` subagent with one precise
   question.
3. **Implement.** Use the `implementer` subagent with the item and its "done when".
4. **Verify the gates** yourself (docs/agent-loop.md "Gates"):
   - `python -m pytest -q`;
   - the demo;
   - `python scripts/metrics.py --compare` on the eval set.

   On a failure, send the failure back to the implementer, at most 3 times. Then mark the item blocked and stop.
5. **Review.** If conversion output changed, use the `chart-reviewer` subagent on the eval-set outputs, before and
   after.
6. **Playtest.** If the item asks for it and the PC is available, use the `playtester` subagent. Otherwise add
   "waiting for PC: playtest <charts>" to the LOG line.
7. **Record.**
   - Append one line to `agent/LOG.md`: `date | item | result | metrics delta | next`.
   - Append metrics to `agent/metrics.jsonl`.
   - Update `agent/DECISIONS.md` if a rule was decided.
   - Remove or tick the item in BACKLOG and add any new items found, each with a "done when".
   - Commit with a message naming the item.

Stop early if two iterations in a row produce no metric improvement and no new capability. Write why in the LOG.
