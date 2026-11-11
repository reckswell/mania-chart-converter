# osu!mania mapping reference

What a converted chart has to get right to read and play like a hand-made osu!mania map rather than a
note-for-note copy. This is the yardstick the conversion rules (`src/vsrgconv/convert/`) and the lint
(`src/vsrgconv/convert/lint.py`) are written against.

Last checked: 2026-11-11. Sources are the osu! wiki (read from `ppy/osu-wiki` on GitHub, because osu.ppy.sh is not
reachable from the build machine) and the community knowledge summarised under each heading. Wiki pages are cited as
[W:page]. The list of pages is at the end.

## 1. The file a map is

A map is one `.osu` text file per difficulty, plus the audio and background, zipped together as an `.osz` [W:osu file
format]. The parts that matter for mania:

| Section | Keys that matter for mania | Notes |
|---|---|---|
| `[General]` | `AudioFilename`, `AudioLeadIn`, `PreviewTime`, `Mode: 3`, `SampleSet`, `SpecialStyle` | `SpecialStyle: 1` is the N+1 (scratch) layout. |
| `[Metadata]` | `Title`, `TitleUnicode`, `Artist`, `ArtistUnicode`, `Creator`, `Version` (difficulty name), `Source`, `Tags` | `Source` should name the game the chart came from. |
| `[Difficulty]` | `CircleSize` = **key count**, `OverallDifficulty` (timing windows), `HPDrainRate` | `ApproachRate`, `SliderMultiplier` are unused by mania. |
| `[Events]` | background, video, breaks | |
| `[TimingPoints]` | `time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects` | Red (uninherited) lines carry BPM; green (inherited) lines carry SV as `-100/SV`. Effects bit 0 = kiai, bit 3 = omit first barline. |
| `[HitObjects]` | note `x,y,time,1,hitSound,hitSample`; hold `x,y,time,128,hitSound,endTime:hitSample` | Column is `floor(x * keys / 512)`; the writer uses the column centre `floor((col + 0.5) * 512 / keys)`. `y` is ignored (192 by convention). |

Times are integer milliseconds from the start of the audio. A converted chart is kept in beats (exact fractions) until
the last step and only then turned into milliseconds, so rounding never accumulates.

## 2. Timing comes first

Every guide says the same thing: notes can only be as good as the timing under them [W:Timing][W:How to time songs].

- **One red line per real tempo change, and no more.** Put a red line where the BPM or the metre really changes, and
  on the downbeat. A red line also resets the barlines, so a line that is not on a downbeat breaks the measure grid.
  "It is better to correctly and accurately time the BPM/Offset than follow the Time Signature" [W:How to time songs].
- **Offset is the first downbeat**, nudged until the metronome sits on the beat. Errors under ~3 ms are noise; worry at
  5-6 ms [W:How to time songs].
- **Most songs have an integer BPM.** A source chart's BPM is usually exact; trust it over audio analysis, but check
  the offset against the audio.
- **Variable BPM needs scroll normalisation.** Without it, notes speed up and slow down with the BPM. Add a green line
  at each red line with `SV = main BPM / current BPM`, where the main BPM is the one used for the most time (shown in
  brackets in song select) [W:mapping guide][W:mania RC]. This is *required* on Easy and on a Normal that is the lowest
  difficulty, and expected everywhere.
- **Slider velocity (SV) is an effect, not timing.** Long-term SV changes should stay inside a range per difficulty
  (Normal 0.90-1.05x, Hard 0.8-1.10x, Insane 0.70-1.10x, Expert 0.60-1.10x), follow the music's intensity, and be
  snapped to the beat (1/4, 1/8, 1/16 straight; 1/6, 1/12 swing) [W:mania RC].
- **Stops, reverses and speed gimmicks** in other games (Chunithm/SDVX soflan, DDR stops) have no direct
  equivalent. Teleports and freezes are built from very short green lines: very high SV for a jump, very low SV
  (0.01x) for a near-freeze. Objects right after a short SV effect should arrive at 1.0x [W:mania RC, Hard].

**Converter consequence:** the timing map comes from the source chart (exact BPM and measure positions). The writer
emits red lines only at real BPM or metre changes and adds normalising green lines automatically. Source gimmicks are
kept in the intermediate format as `scroll` events and turned into SV only by an explicit rule.

## 3. Snapping

The editor's beat snap divisor goes from 1/1 to 1/16; most mapping is done on 1/4 [W:mapping guide][W:Compose].
Common divisors are 1/1, 1/2, 1/4, 1/8, 1/16 (straight) and 1/3, 1/6, 1/12 (triplet or swing). Anything else reads
as a mistake. **Unsnapped notes**, off any of these by more than about 1 ms, are a classic mod comment.

**Converter consequence:** after timing, every note is snapped to the nearest allowed divisor of its beat. A note
whose nearest snap is outside the allowed set is reported, not silently moved. Image-based sources (sdvx.in) are
read in pixels, so snapping is where their rounding is removed.

## 4. Hard rules a chart must pass

From the mania ranking criteria [W:mania RC]. These are machine-checkable, so the lint enforces them:

- Auto must reach 1,000,000: no impossible overlaps.
- **Two notes cannot be in one column at the same time**, and a note cannot start inside a hold in the same column.
- **No column can be left empty.**
- **At most 6 notes pressed at once** in Insane and below (releases do not count).
- Key counts: 4-10, 12, 14, 16 or 18. Above 10 needs a named playstyle (10K2S, DP, EZ2AC, 10K8K, 9K9K).
- Long notes should be held for **at least 1/12 of a beat** (1 beat on Easy, 1/2 on Normal, 1/4 on Hard).

## 5. What makes a map good, beyond the rules

These are guidelines and community practice. They are what separates a map from a transcription.

1. **Every note corresponds to a sound** [W:mania RC]. Simplify when following the music exactly would be
   unplayable, could be represented by a steady snap, or would spike the difficulty.
2. **Consistency.** The same sound gets the same treatment every time: if the snare is a jump in the first chorus, it
   is a jump in the second [W:mapping guide]. Converters break this easily when two source notes that sound alike land
   on different column counts after lane mapping.
3. **Emphasis through chord size.** Heavier sounds (kick plus crash, downbeats of a drop) get bigger chords; light
   sounds get single notes. Chord size is the main way mania expresses intensity.
4. **Long notes for sustained sounds** (vocals, synth pads, held strings). Their release should land on a sound too.
   Shields (a note just before an LN in the same column) and inverse patterns are deliberate techniques, not
   accidents.
5. **Hand balance.** In 4K the left and right hands (columns 1-2 vs 3-4) should carry similar loads over a phrase; in
   7K the middle column is played by a thumb or index and is shared. Long one-hand runs tire players and read as
   lopsided.
6. **Pitch relevancy** (optional, mostly 7K+): lower pitches to the left, higher to the right, particularly in calm
   single-note sections [W:mapping guide].
7. **Pattern vocabulary** has names, and difficulty depends on which appear [W:mania RC glossary]:
   - jack (3+ in one column), minijack (2);
   - trill (alternating two columns), jumptrill;
   - roll and staircase (1234, 4321), split roll (1324);
   - stream (steady single notes, no jacks), jumpstream, handstream, chordjack;
   - bracket (trills on neighbouring columns), anchor (one column over-used in a stream), grace (near-simultaneous
     notes, 1/6 and finer).

   Unintended jacks are the most common converter artefact: two source notes in different lanes that collapse into one
   mania column.
8. **Avoid unjustified difficulty spikes.** Difficulty should follow the song's intensity [W:mania RC, Insane/Expert].

## 6. Difficulty and spread

- Standard names are **Easy, Normal, Hard, Insane, Expert** [W:mania RC], but a chart from another game may use that
  game's names ("BASIC", "MASTER", ...) [W:mapping guide]. This project names osu! difficulties
  `"<GAME> <NATIVE NAME> <level>"`, for example `CHUNITHM MASTER 14+`, so the origin is always visible.
- Per-tier guidelines (around 180 BPM, 4/4):

  | Tier | Densest snap used in runs | Chords | LN minimum |
  |---|---|---|---|
  | Easy | mostly 1/1, some 1/2, at most 5 consecutive 1/2 | at most 2 notes | 1 beat |
  | Normal | 1/1 and 1/2, some 1/4, at most 5 consecutive 1/4 | 4K: 2, 7K: 3 | 1/2 beat |
  | Hard | 1/4 streams with 1/2 rests each phrase; no long 1/8 | 4K: 3, 7K: 4 | 1/4 beat |
  | Insane | 1/8 bursts up to 4 beats; no 1/6+ LN streams | up to 6 | 1/12 beat |
  | Expert | anything justified by the music | | 1/12 beat |

- **Scale by BPM.** Below ~120 BPM, denser snaps are acceptable (90 BPM roughly doubles the allowed density). Toward 240
  BPM, 1/2 becomes the dense snap [W:Scaling BPM].
- **OD and HP** stay at or below 7 (Easy), 7.5 (Normal), 8 (Hard). LN-heavy maps use lower OD. Common values for hard
  converts are OD 8 and HP 8.
- **Star rating is not difficulty.** One dense section can inflate it; judge spread by patterns [W:mapping guide].

## 7. Judgement, which decides how strict timing must be

From [W:mania judgement] (stable; lazer is close):

| Judgement | Max error (ms) |
|---|---|
| PERFECT (300g) | 16 |
| GREAT (300) | 64 - 3·OD |
| GOOD (200) | 97 - 3·OD |
| OK (100) | 127 - 3·OD |
| MEH (50) | 151 - 3·OD |
| MISS | 188 - 3·OD |

- Hold notes are judged on the head and the release together. Releasing early during the body caps the judgement at
  MEH.
- Chunithm's JUSTICE CRITICAL window is about ±33 ms, wider than PERFECT. A chart that is fair in Chunithm can be harsh
  in mania when it relies on dense slides that Chunithm judges loosely.

## 8. Editor workflow and tricks worth copying

- **Timing setup** (F6): red lines for BPM, green lines for SV, volume, sample set and kiai. Ctrl+P adds a red line at
  the cursor. Tap T to the beat to estimate BPM [W:mapping guide].
- **Beat snap divisor**: Ctrl+wheel. Map at 1/4 by default, switch to 1/3 or 1/6 for triplets.
- **Copying a difficulty** ("Open Difficulty → for reference") and deleting notes is the usual way to build lower
  difficulties from a finished top one. The converter's equivalent is generating lower tiers from the source's own
  lower charts, not from thinning the top chart.
- **Kiai** marks the chorus, with one segment per chorus and no spamming [W:mania RC].
- **Hitsounding**: soft sample set by default. Do not stack the same hitsound on several notes of one chord.
  Keysounded maps (BMS/IIDX style) need hitnormal support. Converted charts start without hitsounds. Keysounds from
  the source game could be added later.
- **Testing**: play-test at the intended scroll speed, and use Auto to check for impossible overlaps. Use the editor's
  AIMod/Verify tab for unsnapped notes and overlaps. orthomania's playtest harness covers this for us (see
  `docs/agent-loop.md`).

## 9. Checklist the converter's output is held to

1. Timing: red lines only at real BPM/metre changes, offset on the first downbeat, normalising green lines with
   variable BPM.
2. Every note snapped to an allowed divisor.
3. No same-column overlaps, no empty column, no hold shorter than 1/12 beat. Chord limits per tier.
4. No new jacks or minijacks that the source did not have: a jack must come from repeated identical sounds.
5. Chord size follows the source's emphasis (source note width or simultaneous count), consistently.
6. Hand balance over each 4-measure phrase, within a tolerance.
7. Metadata: native title and artist in the Unicode fields, romanised in the plain fields, `Source` set to the game,
   tags with the game, genre and chart designer.

Items 1-3 are hard checks in `lint.py`. Items 4-6 are reported as scores, because they need judgement and are where
the rules will be tuned.

## Wiki pages read

- [W:osu file format] `wiki/Client/File_formats/osu_(file_format)`
- [W:mania RC] `wiki/Ranking_criteria/osu!mania`
- [W:Difficulty naming] `wiki/Ranking_criteria/Difficulty_naming`
- [W:Scaling BPM] `wiki/Ranking_criteria/Scaling_BPM`
- [W:mapping guide] `wiki/Guides/osu!mania_mapping_guide`
- [W:Timing] `wiki/Beatmapping/Timing`
- [W:How to time songs] `wiki/Guides/How_to_time_songs`
- [W:Compose] `wiki/Client/Beatmap_editor/Compose`
- [W:mania judgement] `wiki/Gameplay/Judgement/osu!mania`
- `wiki/Beatmapping/osu!mania_10K_plus_playstyles`, `wiki/Gameplay/Hit_object/Hold_note`, `wiki/Beatmap/Overall_difficulty`

## Not done yet

- **Observing real maps in the editor.** osu! cannot run in the cloud machine, and the agent did not drive the desktop
  editor. The next step is to parse a corpus of ranked mania maps and measure what is written above: snap
  distribution, chord sizes per tier, LN lengths, jack frequency. That gives numeric targets instead of prose.
  `vsrgconv.osu.reader` reads `.osu` files for this.
- **Video observation** of the source games. YouTube is not reachable from the cloud machine either.
