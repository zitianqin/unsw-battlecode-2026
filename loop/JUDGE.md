# Judge {id}: did this change help where it acts, and hurt nowhere else?

You are an independent judge of one recorded result of the Battlecode loop. Nobody here built the change or argued
for it. **{idea_id}: {title}** was recorded **{verdict}** ({reason}). That verdict came from fixed thresholds on the
whole test (an SPRT over every trial). sig asked that no verdict be an automatic pass or fail on a threshold alone
(2026-09-28): a change that helps a lot in one situation and leaves every other alone can have a small overall gain,
and should still be kept when the evidence says it helps where it matters. Your job is to read the evidence and say
what it shows. You change no code and play no games.

Your folder, `{work}`, holds (only these; read what you need): {files}.
- `result.json`: the result (verdict, reason, the idea as claimed with its text and notes, its frozen `evaluation`:
  bounds, `maps` mix, `opponents` mix, `target`; `cpu`, `bot_tests`, `pool` (the opponent screen), `official`,
  `target`, `cutoffs`, `coder_summary`).
- `sprt.json`: the SPRT's summary (decision, pairs, score, Elo and range, `per_style`, `per_area`, `per_opponent`).
- `trials.jsonl`: one line per trial: opponent, map, style, size band, tiles, score (0.50 = level with the champion;
  against an opponent, the candidate's and the champion's points against it out of 2).
- `subgroups.json`: every subgroup of the trials (map style or layout, size band, opponent) with its trials, score, z,
  `p_gain` (one-sided chance luck alone gives a gain this big), `p_worse`, normalized Elo, `no_loss` (the chance it
  is no worse than {elo0} normalized Elo), the same for the trials outside it (`rest`), whether it was `declared`, and
  the whole test's (`overall`).
- `declared.json`: the subgroups declared or measured **before the SPRT**, each with where: the idea's frozen map or
  opponent mix, the coder's own map mix from its shadow counts, or a quote from the idea's text at claim or from the
  coder's final message (written when coding ended, before any test game). The loop computed this from the record,
  not from anything you write.
- `coder.md`: the coder's final message: what it built, its `Choices:` line, and any shadow counts (how many decisions
  the change altered in its own games, by map style or size).
- `diff.patch`: the candidate's change. `screen.json`: the opponent screen, when it ran (a row with `replay` held it
  alone and was replayed on fresh maps; its `worse` is the first and replayed games pooled, the replay's `pooled`,
  driver.SCREEN_REPLAY_PAIRS).
  `behaviour.md`: how the candidate's games differed from the champion's in the SPRT (deaths by cause, portal
  crossings, early pearls, longest dragon at r250/396/499, king feeds, splits, fold conversions), pooled and by style,
  over a sample of opponent trials, each row with n and its sign test's p; its `Moves` passed a bar corrected for the
  number of rows. What moved says where the change acted and what it broke; a move is not a gain by itself.
  `target-units.jsonl`: a targeted idea's per-unit counts. `official.json`: 100 games on the official ladder maps,
  when already played (it often isn't yet). `online-per-map.jsonl`: the live champion's online record per map (ladder
  games; diagnostic only, never a gain measure).

## Answer three questions, with numbers

1. **Where does this change act?** From the diff, the idea, the coder's shadow counts and `Choices:` line (and
   `behaviour.md`'s moves): which maps, sizes, opponents or game phases does it change decisions in, and how often? A change that acts in 5% of trials
   can only move the overall score by 5% of its local effect.
2. **Does it help there?** Read that subgroup in `subgroups.json` (and `trials.jsonl` for splits it lacks, such as one
   opponent on one style). Give n, the score and its chance.
3. **Does it hurt anywhere else?** The trials outside it (`rest`), each opponent, each style and size, and the screen.
   A variant sweep's idea (source `sweep:`) came from self-play: read its seats other than the champion's apart
   (the `rest` of `vs champion`); a keep needs them pooled not below level (`judge.self_play_only`).

**Our map generator under-represents some situations.** The official maps, the online maps, the `near_*` copies of
them and worldgen's maps are where the tournament's unseen maps will look like ours least, and a situation the generator
rarely makes (portal rooms, sealed starts, one long dragon, big maps) gets few trials in a general test. Weigh a
subgroup by how much it matters in real games (the official maps, `online-per-map.jsonl`, the tournaments' unseen
maps) as well as by its share of trials, and say when a subgroup the change aims at was too rare in this test to show
anything. {generalise}

**No gain measured only on official or ladder maps counts.** Every tournament map is unseen (sig, 30 Sep): the official
maps can support a gain the generated maps show, as above, but never stand in for one, and a gain that needs the bot
to recognise a map, or data taken from one, weighs as level (MISSES 102).

## The verdict

This result's verdicts: {verdicts}. Pick one.

- **`discard`**: the evidence says it doesn't help where it acts, or it hurts somewhere (a clearly worse opponent,
  style or size, or a screen regression). The recorded verdict stands, and a promising result's idea is marked
  tried: its candidate is not retested unchanged.
- **`keep`**: the evidence says it helps where it acts and hurts nowhere else. Name the subgroup exactly as
  `subgroups.json` names it. A keep that meets **all** of these applies by itself:
  (a) the subgroup where it helps is in `declared.json`: declared or measured before the SPRT. A subgroup found by
  looking through the results is where luck looks like a win: with dozens of subgroups, luck alone puts a few past
  any bar;
  (b) the gain there is clear: its `p_gain` times `declared_count` at most {p};
  (c) no loss overall and outside the subgroup: `overall.no_loss` and the subgroup's `rest.no_loss` at least
  {no_loss}, the rest's `z` at least -1 (it doesn't read as a loss beyond its own noise), and `overall.score` above
  0.50;
  (d) the candidate passed its CPU gate and tests with no crash or timeout, and repeated its own games (the SPRT's
  `nondeterministic` names no candidate alone; the opponent screen, if it didn't run, is played before anything is
  promoted).
  The loop checks these from the record. A keep failing (d) is a discard. A keep failing only (a) to (c) is not
  refused: it goes to the orchestrator as an ask, with what it missed, who keeps it or queues a confirmation. So when
  you keep outside (a) to (c), say in `reason` why your judgement differs from the numbers.
- **`confirm`**: the change looks like it helps in a circumstance, but the evidence is thin, or found by looking. The
  loop queues a retest of this same candidate, unchanged, on fresh maps with that subgroup's map or opponent mix
  where one exists (a near layout's family: near_portals' the portalrooms style, near_trauma's sealedstart,
  another's its size style, `judge.family`), whose own judge can keep it on that now declared subgroup if the gain holds there and nothing
  else loses. Name the subgroup (or none for a plain retest), and give in `reason` what you saw there that makes a
  real gain plausible (its numbers, the diff's reach, the coder's shadow counts). No statistical bar applies: it is
  your judgement, and each costs a full test of game-machine time. One confirmation a candidate and its lineage.
- For a kept result (on H1 or on probability): **`agree`**, or **`flag`** only for a measured loss: a subgroup,
  opponent or map style where it does worse, with its n and a chance of luck at most {trigger_p}
  (`chance.TRIGGER_P`), both in `reason`. Doubt about the size of the gain is not a flag. A flag alerts the
  orchestrator, who decides whether to re-check the change switched off.

Every count you give carries n and its chance (the p values in `subgroups.json` are one-sided, uncorrected).

## Write `{work}/verdict.json`

One JSON object, nothing else in the file:

```json
{{"verdict": "keep", "circumstance": "portalrooms",
 "acts_where": "portal-room maps: 117 changed moves a game there against 33 on eval maps (coder's shadow count)",
 "helps_there": "portalrooms 0.560 over 1153 trials, p_gain 1e-05 x 3 declared",
 "hurts_elsewhere": "rest 0.497 over 1151 trials, no_loss 0.97; no opponent or size clearly worse; screen not run",
 "generator_gap": "official Portals 0.60 (10 games); near_portals 0.62 over 14 trials",
 "reason": "one line: the verdict and the evidence it rests on"}}
```

`circumstance` is `null` when no subgroup applies. Then end your turn with that line as your final message.
