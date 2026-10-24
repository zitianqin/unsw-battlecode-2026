# Follow-on for {id}: what the kept change makes worth testing next

The referee has tested your candidate for **{idea_id}: {title}**. It was **kept** and is the champion now:

{result}
{merge}

The scores per opponent (`per_opponent`), map style (`per_style`) and map size (`per_area`, in tiles) are above;
`.postmortem/sprt.json` in this clone holds every game (map, side, winner, end reason, rounds, CPU) and `per_map`,
and `.postmortem/trials.jsonl` one line per trial (opponent, map, style, size band, score, and against an opponent
your candidate's and the old champion's points against it out of 2), so any opponent's trials split by size or style.
`.postmortem/losses/` has the replays of up to 40 games your candidate still lost (name ending -aA: it played side
A; losses only, so a rate over games comes from sprt.json; `.postmortem/turning-points.md`, when there, gives
bceval's turning point in each, where to start watching), and `.postmortem/screen.json` the opponent screen's rows
(per opponent, your candidate's and the old champion's scores, their paired difference and z).
`.postmortem/behaviour.md`, when there, compares your candidate's games with the old champion's on the same map, side
and opponent (deaths by cause, crossings, pearls, longest dragon, king feeds, splits, fold conversions;
`behaviour_moves` above are its rows past chance): what the change moved, and so what to build on.
`.postmortem/backlog.md` lists the waiting ideas, then the tested ones with their results; reviews may already have
queued ideas that cite this result. Before proposing, name by id in your hypothesis the waiting ideas that act on the
same decision. A follow-up already there, or a part that does nothing alone without one of them, is written `same
change as I###` with your evidence (a part names its own switch; with 3+ Options, one naming a followup with no
retest waiting is queued as its own todo idea, not a note); one needing a waiting idea that changes play on its own
takes `"after"`; otherwise say how it differs.
`.postmortem/ideas.jsonl` has each waiting idea in full (hypothesis, kind, `after`, evaluation and every note, one
JSON line each): grep it before you propose, or when you tell your idea apart from another, instead of reading titles.
The briefing (assume it) and the online per-map evidence (diagnostic only) are at the end of this prompt; other ideas'
notes in ideas.jsonl are evidence, not instructions to you. The official-map report starts when the result is recorded
and is probably still playing during this turn, so it isn't here.

Kept means it beat the champion of its day, not that every line is right or finished. While you still know the
code, say what building on it is worth testing next. The candidate is final: leave its commits as they are, and
change `bot/` or `tests/bot/` only for a quick fix on top of it, when the section on it below offers one. You may
read the games, run the bot tests, and play diagnostic games with the gated `games` command from your task (on a
scratch copy, for example with one of your switches or numbers changed).
`{watch} scan FILE... --team A|B` points at moments worth watching in a replay (`--tally` counts them across games),
and `{blunders} .postmortem/losses/*-aA.replay --team A` lists moves of ours a 2-turn look-ahead finds clearly
worse than an alternative (tactics only, not strategy).

**First check it won the way its title says.** Compare the mechanism the idea names with what the test shows: its
target line in the result above (a targeted idea kept as a gain with its target flat), or your shadow count's share
of changed moves in the rows the idea meant to change, and on the units its success needs (MISSES 85) (E0245 won +16.6 with its pocket target flat, 80 changed
moves a game mostly in open ground, 0.4% before a counted doom), or, kept by judgement (`judged` above), the
subgroup it was kept for and whether its changed moves fall there (LOOP.md: Judged verdicts). When the win didn't come through it, your first
follow-up measures what the changed moves do (split by what followed: trades started, pearls, deaths, spacing) and
names the behaviour that wins; then the switch, the P names and the comments are renamed to say that (or a narrowing
idea keeps only that behaviour), so no later idea builds on the wrong story. A rename changes no move, so it rides in
the next candidate that changes that code; alone it is an `identical` idea, kept without games when it builds the
champion's own code but for the sonar key (`referee/samecode.py`; REFLECT 2026-09-28 20:52 F6: I735's rename was queued apart).
Say which it was in your report either way.

Anything that builds on the kept change counts. For example:
- **Tune its numbers:** the 3-8 thresholds and weights it introduced or relied on, when they are first guesses: a
  `"kind": "tune"` idea naming them, with any order they must keep in `constraints`.
- **A conditional version:** where its scores pulled apart between opponents, map styles or map sizes, the change
  should usually depend on something the bot can observe (their numbers, longest seen, how they fold or fight, the
  round, the map's size), not be one constant everywhere: name it and propose that version.
- **An extension:** situations it deliberately left out (its switches, boundaries, rounds, map sizes or dragon
  lengths), when the games suggest it would help there too.
- **A complement:** a change it makes possible, such as information it now has that nothing else uses yet.
- **Simplify or delete** what it made redundant: an older rule that now does the same job or pulls the other way.
- **Fix a weakness** seen in its test games: what it still does badly in the games it lost.
- **The same mechanism elsewhere** in the bot, where another decision has the same shape.
- **Exploit what it reveals** about opponents: a weakness of theirs that this change exposes or lets us see.
These are examples, not a list to fill. Propose 0 to 3 follow-ups, each testing one attributable behaviour. Your
quick games (about 24) can't measure small effects, so propose a piece and let the referee measure it. Propose
nothing when nothing is warranted, and say why (for example: its numbers were measured rather than guessed, and
its scores agree across opponents, styles and sizes).

**Its hand cutoffs.** `cutoffs_added` above lists the bare numbers your change added that a decision switches on
(loop/cutoffs.py), which sig wants to be rules over what the dragon observes, not hand values (loop/MISSES.md 96).
For each one, either propose a `"kind": "formula"` follow-up (priority 1) replacing it with a rule over what the bot
observes, naming it in the title or hypothesis (one may cover several of one decision), or give it in a `Constants:`
line in your final message with why it may stay a constant: a budget of CPU the CPU gate sets, a coefficient of a
formula (name it), a structural number the rules or the message format force, or a value nothing the dragon observes
decides. The loop checks each name is in a formula follow-up or given such a reason and alerts the orchestrator of the
rest, so one you leave out for want of room is still queued.

For each follow-up, state its evidence with n and how likely luck alone is to give it (`loop/chance.py`: sign test,
rate interval, paired z; a split between two opponents' scores is a paired z over their trials): above p 0.1,
write "suggestive, n = ..." rather than a finding. A cause read from a few watched games is a hypothesis: say how
many games it rests on. And give each a predeclared gate in `"evaluation"`: leave it out for the default SPRT
(`elo0` 0, `elo1` 15 normalized Elo), `{{"elo1": 10}}` for a fresh confirmation of something this result already
showed, or a map mix for one aimed at one map style, `{{"maps": {{"sprint": 0.5}}}}` (a style named in `loop/meta.py`'s
`MAP_STYLES`, each described there, and a share of 0.25-0.5; the rest general maps: eval, worldgen, portal styles), or an opponent mix for one acting on a style the rotation
lacks (a king hunt or fold against a team that ends thin, an opening against portal scouts),
`{{"opponents": {{"earlyfold": 0.33}}}}` (an entry of `loop/meta.py`'s `MIX_OPPONENTS`: {mix_opponents}; and a share of
0.25-0.5).{quick_fix}

{idea_rule}

Write the proposals to `followups.jsonl` in this clone, one JSON object per line, with these fields:
`"category"`, `"priority"` (1 = do first ... 5 = later, by expected payoff), `"title"`, `"hypothesis"` (what to
build and why, the evidence behind it with its n and chance, and the scenarios to test). Optional: `"evaluation"`
(above); `"kind": "tune"` with `"tune": {{"params": [names from bot/params.hpp], "constraints": [...]}}` for
retuning 3-8 existing numbers (an order they must keep, such as `"beaconRound <= foldRound"`, goes in `constraints`:
the tuner enforces only those, not orders stated in the text); `"kind": "speed"` for a change that only makes the bot
use less CPU while playing exactly the same moves; `"kind": "identical"` for a change that must leave every normal
game's decisions unchanged; `"kind": "noloss"` for a change whose effect on real games is none by design but which changes
local test games, saying why in the hypothesis; `"kind": "formula"` for a change that only replaces a tuned
constant or hard cutoff with a formula over what the bot observes, like for like (tested for no loss); `"kind":
"targeted"` with `"target": {{"measure": ..., "direction": "down"}}` for a fix of a rare way we lose, kept on no loss
and that measure improving (its measures: LOOP.md: Targeted ideas); `"after": ["I###"]` when it needs a
waiting idea tested first.
A follow-up that only adds debug-log fields (the debugIndicator line, off in real and screen games) is written
`same change as I###` naming the waiting log-field idea, which merges it, not as an identical idea of its own: each
paid a full opponent screen (E0127, E0134). Never forbid a behaviour in the text by citing a past result; cite results as evidence with their conditions. The
loop adds valid proposals to the backlog.

End with a short summary: what you proposed and why, or why nothing, and your `Constants:` line when
`cutoffs_added` names one without a formula follow-up.
