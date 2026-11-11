# Source games: mechanics, charts, and what breaks in osu!mania

For each game: how it plays, what its charts contain, where machine-readable chart data can come from, and what has
no direct mania equivalent. This drives the intermediate format (`src/vsrgconv/model.py`): it has to hold every
mechanic below without loss, so that conversion rules can decide later what to do with each one.

Last checked: 2026-11-11. Written from game documentation and community knowledge. **Gameplay was not observed on
video** for this pass, because YouTube is blocked from the build machine. Items marked *(verify)* should be checked
against footage before a rule depends on them. Viewfield and camera details (scroll speed scales, judgement-line
position) are already researched in the orthomania repo (`Reckswell/osu`, `docs/ortho/research/game-*.md`) and are
not repeated here.

## The core mismatch

osu!mania has **N discrete columns, one key each**, with only two object types: a note and a hold. Every other VSRG
adds at least one of the following:

| Mechanic | Games | Why it does not map 1:1 |
|---|---|---|
| **Variable-width notes** on a continuous lane surface | Chunithm, Sekai, (Bandori: no) | One source note can cover several mania columns, and two notes can overlap partly. |
| **Slides / paths** (a hold that moves sideways) | Chunithm, Sekai, Bandori, SDVX lasers, Arcaea arcs | A mania hold cannot change column. |
| **Directional or flick input** | Chunithm (flick, air), Sekai (flick), Bandori (flick), Arcaea (sky) | There is no gesture in mania. |
| **Vertical input** (air, sky) | Chunithm AIR, Arcaea arcs and arctaps | There is no second input plane. |
| **Analogue controls** | SDVX knobs, IIDX turntable, GH whammy | There is no analogue input. |
| **Special columns** | IIDX scratch, SDVX FX, GH strum, DDR double | These suit N+1 or wider layouts. |
| **Scroll gimmicks** (stops, reverse, soflan) | DDR, PIU, Chunithm, SDVX, IIDX | They map only roughly onto SV. |
| **Mines / damage notes** | Chunithm DAMAGE, PIU mines, DDR shock, Sekai (no) | osu! has no mines. Only lazer rulesets could add them. |
| **Mandatory chord-input semantics** | GH (strum + frets), DDR (jumps by feet) | The physical meaning differs from fingers on keys. |

The intermediate format keeps all of these. Each gets a rule slot in `convert/rules.py` with a placeholder default.

## Chunithm (SEGA, 2015-)

- **Play surface:** a 16-cell touch slider plus an **air sensor** above it (IR beams, triggered by raising hands).
- **Notes** (C2S names in brackets):
  - TAP (TAP): press anywhere on its cells.
  - ExTAP (CHR): always judged JUSTICE CRITICAL, used for emphasis.
  - HOLD (HLD): press and keep.
  - SLIDE (SLD/SLC): a hold whose position moves along a path, with control points.
  - FLICK (FLK): swipe across the note.
  - AIR (AIR/AUR/AUL/ADW/ADR/ADL): raise the hand after or at a ground note, with a direction (up, up-left, down ...).
  - AIR-HOLD and AIR-SLIDE (AHD, ASD/ASC): keep the hand raised. AIR-CRUSH (ALD) is a newer, decorative-to-chain air.
  - DAMAGE (MNE): a mine.
- Width is 1-16 cells, and every note has a start cell and a width.
- **Timing:** BPM changes, time-signature changes, and **soflan**: speed changes (SFL) that are pure visuals,
  including stops and reverses.
- **Difficulties:** BASIC, ADVANCED, EXPERT, MASTER, ULTIMA (extra), WORLD'S END (gimmick charts, rated by kanji type
  and stars instead of a level).
- **Levels:** 1-15 with "+" steps; chart constants to 0.1 (for example 14.6).
- **Genres:** POPS & ANIME, niconico, 東方Project, VARIETY, イロドリミドリ, ゲキマイ, ORIGINAL (WORLD'S END
  separate).
- **Chart data sources:**
  - sdvx.in (`/chunithm/`) has every chart as **pictures**, which must be read with image processing. See
    `docs/sources/chunithm-sdvxin.md`.
  - otoge-db (GitHub `zvuc/otoge-db`) has song metadata, levels, constants, designers and **per-type note counts**,
    useful to check the picture reader.
  - The game's own `.c2s` text files are exact, if available locally. A reader is a planned adapter.
- **Mania mismatch:** 16 cells with widths, slides, air, flicks, damage, and soflan. Candidate key modes are 8K (2
  cells per column), 4K or 7K (by density), and 16K for gimmick fidelity. Air notes have no key. Options are to drop
  them, fold them into the ground note as a chord, or use one extra column (N+1). *Rule TBD.*

## Project Sekai: Colorful Stage (SEGA / Colorful Palette, 2020-)

- **Play surface:** a touchscreen, **12 lanes**, variable-width notes (1-12).
- **Notes:**
  - tap; critical tap (gold);
  - flick (up, up-left, up-right), critical flick;
  - hold/slide with a path, with visible and invisible relay points, and a curved or straight easing per segment;
  - trace notes (touch-and-drag "friction", added 2023);
  - guide slides (no input).
- **Timing:** BPM changes, hi-speed changes (`#TIL` speed layers). Some charts have very fast flick chains.
- **Difficulties:** EASY, NORMAL, HARD, EXPERT, MASTER, APPEND (6-finger extra since 2023).
- **Genres:** unit-based: VIRTUAL SINGER, Leo/need, MORE MORE JUMP!, Vivid BAD SQUAD, ワンダーランズ×ショウタイム,
  25時、ナイトコードで。, plus "other".
- **Chart data sources:** the game's charts are SUS-format text, published by the fan databases (Sekai Viewer /
  sekai.best asset mirrors) with the music list as JSON. This is the cleanest source after Bandori. *(verify current
  URLs)*
- **Mania mismatch:** widths, slide paths and flick direction. Lanes 12 → 6K and 12K are both natural.

## BanG Dream! Girls Band Party! ("Poppin'Party", Bushiroad / Craft Egg, 2017-)

- **Play surface:** a touchscreen, **7 fixed lanes**, each note one lane.
- **Notes:**
  - single;
  - skill note (visual);
  - flick;
  - directional flick (left/right, can span lanes);
  - long note;
  - slide (a long note whose path crosses lanes, with relay ticks).
- **Difficulties:** EASY, NORMAL, HARD, EXPERT, SPECIAL.
- **Genres:** by band (Poppin'Party, Afterglow, Pastel＊Palettes, Roselia, ハロー、ハッピーワールド！, Morfonica, RAISE A
  SUILEN, MyGO!!!!!, Ave Mujica, other), plus cover / original.
- **Chart data sources:** Bestdori serves every chart as JSON (`bestdori.com/api/charts/<id>/<diff>.json`) and the
  song list as JSON. Machine-readable and exact. *(verify the API form)*
- **Mania mismatch:** 7 lanes map straight onto **7K**, the easiest conversion of all. Slides need a lane-change rule;
  flicks become notes, or short holds.

## Sound Voltex (KONAMI, 2012-)

- **Controller:** 4 BT buttons, 2 FX buttons below them, and 2 analogue knobs (VOL-L, VOL-R).
- **Notes:**
  - BT chip and long;
  - FX chip and long;
  - lasers: knob paths across a 0-1 range, including **slams** (instant jumps) and "wide" lasers.
- **Timing:** BPM changes; camera tilts and zooms (visual).
- **Difficulties:** NOVICE, ADVANCED, EXHAUST, plus a 4th slot (MAXIMUM / INFINITE / GRAVITY / HEAVENLY / VIVID /
  EXCEED, depending on the song). Levels 1-20.
- **Chart data sources:**
  - sdvx.in (pictures, the same family as Chunithm's);
  - K-Shoot MANIA `.ksh` community charts (text, exact) for many songs;
  - the game's `.vox` files, if available locally.
- **Mania mismatch:** knobs. Common community practice is 6K (4 BT + 2 FX on the outside) or 4K with FX folded in.
  Lasers are either dropped, or slams are turned into notes. *Rule TBD.*

## Dance Dance Revolution (KONAMI, 1998-)

- **Pad:** 4 arrows (Single), 8 (Double).
- **Notes:** step, jump (two arrows), freeze arrow (hold), shock arrow (all arrows, avoid; a mine).
- **Timing:** heavy use of BPM changes and **stops**. Speed gimmicks are only in some charts.
- **Difficulties:** BEGINNER, BASIC, DIFFICULT, EXPERT, CHALLENGE; levels 1-19.
- **Chart data sources:** StepMania simfiles (`.sm`/`.ssc`) are the de-facto public format for DDR charts (community
  packs, for example on Zenius-I-vanisher). Exact text.
- **Mania mismatch:**
  - It maps directly to 4K (or 8K for Double).
  - The danger is that feet-charts are too easy for hands. They are full of crossovers and jumps that are trivial on
    a keyboard.
  - Stops need SV.
  - Shock arrows have no equivalent.

## Pump It Up (Andamiro, 1999-)

- **Pad:** 5 panels (Single), 10 (Double), and "Half-Double" (6 of 10).
- **Notes:** taps, holds (judged by combo ticks while held), and mines and fakes in StepF2 / UCS.
- **Timing:** BPM changes, stops (delays), **warps**, and per-section speed (scroll) changes, used heavily in recent
  charts.
- **Difficulties:** by level. S1-S26+ / D1-D28+, plus CO-OP. There is no named-tier system, so tiers here come from
  level bands.
- **Chart data sources:** StepMania `.ssc` (StepF2 / "PIU" packs) and `.ucs`. *(verify)*
- **Mania mismatch:**
  - It maps to 5K (Single) or 10K (Double).
  - Combo-tick holds do not matter.
  - Warps map to very short SV jumps.
  - Fakes and mines are dropped.

## Arcaea (lowiro, 2017-)

- **Play surface:** a touchscreen with **4 floor lanes** plus a **sky area** above them (a 2D space).
- **Notes:**
  - floor tap and hold;
  - arcs: curved paths in sky space, red or blue for left and right hand, followed by finger position;
  - arctaps (sky notes, on traces);
  - traces (decoration).
- **Timing:** BPM changes, timing groups with separate scroll, and camera and scene events.
- **Difficulties:** PAST, PRESENT, FUTURE, BEYOND, ETERNAL.
- **Chart data sources:** `.aff` text is the native format. It is not published by a public database. Community charts
  and local game data are the only paths. Treat as **limited**.
- **Mania mismatch:** arcs are continuous 2D paths. Candidate approach: 4K floor plus 2-4 "sky" columns (6K/8K), with
  arcs as holds on the nearest sky column and arctaps as notes. *Rule TBD.*

## beatmania IIDX (KONAMI, 1999-)

- **Controller:** 7 keys plus a **turntable** (scratch) per side. SP = 7+1, DP = 14+2.
- **Notes:**
  - note;
  - scratch;
  - charge note (CN: hold, judged on release);
  - hell charge note (HCN: must keep holding, gauge drains);
  - backspin scratch (BSS: scratch hold), multi-spin;
  - **keysounds**: every note plays its own sample, and empty presses play the next sample in that column.
- **Timing:** BPM changes, plus a few charts with stops or soflan.
- **Difficulties:** BEGINNER, NORMAL, HYPER, ANOTHER, LEGGENDARIA. Levels 1-12.
- **Chart data sources:**
  - textage.cc has every chart encoded in JavaScript, exact and machine-readable with some work. *(verify the format)*
  - BMS (`.bms`/`.bme`) is the community format and ships with keysound samples.
- **Mania mismatch:**
  - It maps almost directly to **7K+1** (8K with `SpecialStyle: 1`), the original N+1 playstyle.
  - Keysounds can become per-note hitsound samples.
  - BSS becomes an LN on the scratch column. HCN behaves as an LN.

## Guitar Hero (Activision / Neversoft, 2005-2015; Clone Hero community)

- **Controller:** 5 frets (6 in GH Live, 3x2) plus a strum bar and a whammy bar.
- **Notes:**
  - single and chord gems;
  - sustains;
  - HOPOs (hammer-on/pull-off, no strum needed);
  - tap notes; open notes (strum with no fret);
  - star power phrases.
- **Difficulties:** EASY, MEDIUM, HARD, EXPERT. Clone Hero adds Expert+ (drums double-kick).
- **Chart data sources:** Clone Hero `.chart` and `.mid` files (community charts, with many official-track rips).
  Exact.
- **Mania mismatch:**
  - It maps to 5K, or 6K with a strum or open column. Strumming is implicit, so every gem is a note.
  - Sustains become holds. HOPOs and taps lose meaning.
  - Drums need a separate mapping: 4 pads + kick → 5K, with kick as a lane.

## GITADORA (KONAMI, 2013-)

- **GuitarFreaks:** 5 frets, pick, wailing, and open picks. DrumMania: 9 pads + 2 pedals.
- **Difficulties:** BASIC, ADVANCED, EXTREME, MASTER. Levels 0.10-9.99.
- **Chart data sources:** DTXMania `.dtx` community charts. *(verify coverage)*
- **Mania mismatch:** guitar is the same story as GH. Drums map to 9K/10K with pedals as columns.

## Cross-game difficulty ladder

All games' tiers are put on one ladder so that the database and file names are consistent. The native name is always
kept beside it (`src/vsrgconv/games/registry.py`).

| Canonical | Chunithm | Sekai | Bandori | SDVX | DDR | IIDX | Arcaea | GH | GITADORA |
|---|---|---|---|---|---|---|---|---|---|
| `easy` | — | EASY | EASY | — | BEGINNER | BEGINNER | — | EASY | — |
| `normal` | BASIC | NORMAL | NORMAL | NOVICE | BASIC | NORMAL | PAST | MEDIUM | BASIC |
| `hard` | ADVANCED | HARD | HARD | ADVANCED | DIFFICULT | HYPER | PRESENT | HARD | ADVANCED |
| `expert` | EXPERT | EXPERT | EXPERT | EXHAUST | EXPERT | ANOTHER | FUTURE | EXPERT | EXTREME |
| `master` | MASTER | MASTER | SPECIAL | MXM/INF/GRV/HVN/VVD/XCD | CHALLENGE | LEGGENDARIA | BEYOND | EXPERT+ | MASTER |
| `extra` | ULTIMA | APPEND | — | — | — | — | ETERNAL | — | — |
| `special` | WORLD'S END | — | — | — | — | — | — | — | — |

The top tiers are aligned (each game's hardest regular chart is `master`) rather than the bottom tiers, because the
hardest charts are converted first and are compared across games most often. Pump It Up has no named tiers. Its
charts get `single`/`double` plus the level, and the ladder slot is chosen by level band.

## Order of work suggested by data availability

1. **Bandori** and **Sekai**: exact chart data in JSON/SUS, few mechanics to resolve. They also test the pipeline
   cheaply.
2. **Chunithm**: the user's first pick. The pictures need a reader, and otoge-db gives note counts to check it.
   Started in this pass.
3. **DDR / PIU** via StepMania simfiles, and **IIDX** via BMS or textage: exact data and few mismatches.
4. **SDVX**: sdvx.in pictures share the Chunithm reader's machinery; KSH where available.
5. **Arcaea, GH, GITADORA**: each needs a format reader and real rules for continuous input.
