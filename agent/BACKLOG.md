# Backlog

Ordered. Each item has a "done when". Take the top item that is not `blocked`.

0. **Read merged sdvx.in pictures for charts with no .c2s** (cloud). The newest version's MASTER/ULTIMA/WORLD'S END
   charts exist only as pictures; punipu2's merged PNGs are on GitHub (URL recorded as `source.picture`). Calibrate a
   merged-picture reader against charts that have both a picture and a `.c2s` (exact ground truth).
   *Done when:* note counts match `.c2s` on 20 charts with both, then the picture-only charts are extracted.
1. **Calibrate the sdvx.in reader on real pictures** (needs the PC). Fetch the song lists and the three eval-set
   charts' layers on the PC (`catalog chunithm`, `extract chunithm 201 master`, ...). Run `calibrate-sdvxin` and set
   `ReaderConfig` from real data: palette, cell grid, tap thickness, anchor, AIR placement. Save one real chart's
   layers under `tests/fixtures/real/` for a regression test.
   *Done when:* `check-counts chunithm` shows 0 mismatches on the 3 eval charts, or every remaining mismatch is
   explained in docs/sources/chunithm-sdvxin.md.
2. **Read BPM changes from the bar layer.** BPM is drawn as text beside the lane (OCR, or template-match the digit
   glyphs once they are collected from real pictures). Alternative: a hand-kept BPM table per variable-BPM song.
   *Done when:* a variable-BPM eval chart's measure times match its audio within 5 ms at 3 checkpoints.
3. **Handle sdvx.in's special cases:**
   - columns drawn a few rows short (place the top by the measure line);
   - half-size older layers;
   - ULTIMA charts without their own lanes.

   *Done when:* tests with synthetic versions of each case pass.
4. **Audio offset.** sdvx.in has no audio. Find the first downbeat offset from the user's audio file (onset detection,
   then a tap-to-confirm step for Alpha). *Done when:* the eval charts' offsets agree with a hand-timed reference
   within 5 ms.
5. **Measure real ranked mania maps** (researcher). From a corpus of ranked 4K/7K maps (local osu! songs folder on the
   PC), compute per tier: snap distribution, chord sizes, LN length, jack rate, hand balance. Write the numbers into
   docs/research/osu-mania-mapping.md §6 and use them as lint targets. *Done when:* lint warnings use measured ranges
   instead of prose limits.
6. **Decide Chunithm → mania rules** (first real rules, replacing placeholders):
   - key count per tier;
   - width-aware lane mapping;
   - what AIR, FLICK, ExTAP and slides become;
   - jack avoidance;
   - hand balance.

   Proposals go to DECISIONS and are A/B tested per docs/agent-loop.md. *Done when:* the reviewer gives no high
   findings on the eval set and Alpha has played at least one chart.
7. **Second source: Bandori via Bestdori JSON** (exact data, 7 lanes, the cheapest proof that the IR generalises).
   *Done when:* catalog + one chart convert end to end with tests.
8. **Sekai via SUS files.** *Done when:* the same as 7.
9. **DDR / PIU via StepMania .sm/.ssc**, IIDX via BMS or textage, SDVX via KSH, then Arcaea, GH and GITADORA (see
   docs/research/vsrg-mechanics.md "Order of work").
10. **Hitsounds / keysounds** (later): soft sample set by default; keysounds for IIDX/BMS sources.
