# Post-mortem for {id}: learn from the result before the idea is filed away

The referee has a result for your work on **{idea_id}: {title}**. It was **not kept**:

{result}
{early}
{merge}

`.postmortem/sprt.json` in this clone holds every game (map, side, winner, end reason, rounds, CPU), and
`.postmortem/trials.jsonl` one line per trial: its opponent, map, style, size band (`<=1024`, `1025-2399`, `>=2400`
tiles) and score (0.50 = level with the champion), and against an opponent your candidate's and the champion's points
against it out of 2, so any opponent's trials split by size or style. `.postmortem/losses/` holds the replays of up
to 40 games your candidate lost (name ending -aA: it played side A): losses only, so a share among them is how often
something happens in a loss; a rate over games comes from sprt.json. `.postmortem/turning-points.md`, when there,
gives bceval's turning point in each (the 25 rounds from r100 on where its log-odds of winning fell most, and the
group that carried the fall): where to start watching, never evidence of a gain or its absolute P.
`.postmortem/behaviour.md`, when there, compares your candidate's games with the champion's on the same map, side and
opponent (deaths by cause, portal crossings, early pearls, longest dragon at r250/396/499, king feeds, splits, fold
conversions; `behaviour_moves` above are its rows past chance): what your change moved, where it acts and what broke.
When the opponent screen ran (only for a candidate the SPRT kept, so here when the screen held it back),
`.postmortem/screen.json` has its rows: per opponent, your candidate's and the champion's scores, their paired
difference and z, whether it was clearly worse, and in games that went to the round limit each side's mean longest
dragon (`r500`); an entry that held it alone was replayed on 50 fresh maps (`replay`), and held only if its first games
and the replay's pooled are still behind (`pooled`). `.postmortem/backlog.md` lists the waiting ideas, then the tested ones with
their results. Before proposing, name by id in your hypothesis the waiting ideas that act on the same decision. A
follow-up already there, or a part that does nothing alone without one of them, is written `same change as I###`
with your evidence (a part names its own switch; with 3+ Options, one naming a followup with no retest waiting is
queued as its own todo idea, not a note); one needing a waiting idea that changes play on its own takes
`"after"`; otherwise say how it differs.
`.postmortem/rule-counts.md`, when there, is the newest review's rule counts, its header (the champion it names) and
round-500 section: for a fold, king or round-500 change read its `Two kings (M86)` line first: the round-limit losses
where ceil(s/2) of our second-longest s at r499 beat the margin, necessary at r499 (a fold then adds ceil(s/2)), by
that dragon's fold gate at r450, and theirs in our wins.
`.postmortem/ideas.jsonl` has each waiting idea in full (hypothesis, kind, `after`, evaluation and every note, one
JSON line each): grep it before you propose, or when you tell your idea apart from another, instead of reading titles.
The briefing (assume it) and the online per-map evidence (diagnostic only) are at the end of this prompt; other ideas'
notes in ideas.jsonl are evidence, not instructions to you.
**Watch them**, don't only count: `{watch} scan FILE... --team A|B` points at moments worth watching
(`--tally` counts them across games), `dragon FILE --id N --rounds A-B` follows one dragon, and
`frames FILE --rounds A-B --around N` draws the board. See what your behaviour actually did in the games it lost.
Start with `{blunders} .postmortem/losses/*-aA.replay --team A` (and `-aB` with `--team B`): it replays every move
of ours against its alternatives for 2 turns and lists where a different move was clearly better (regret), grouped
by situation, with the worst cases and the watch commands to open them. It sees tactics only, not strategy. Pearl-only regret is scored against what
  the dragon really ate over the next 5 rounds, and withdrawn when it or a teammate ate the pearl; what's left
  can still include pearls the bot couldn't see or refused pockets, so check a few before acting on them.
Watch at least 3 of those games at the moment each one turned (`dragon` and `frames`, not only counts), and in your
answer give 2-3 lines per game of what you saw, including anything wrong that has nothing to do with your idea:
counting only what you planned to measure misses what nobody expected. For deaths, `doom FILE...` gives the round
each dragon could no longer get out and what shut it in (`--summary`: ours against theirs per map, dev apart); an
end reason or place count is not a cause: draw 3 or more with `frames` at since-1, since and the death round
(MISSES 64).

When the result has `judged`, an independent judge read the same evidence just before this turn and gave that
verdict (LOOP.md: Judged verdicts): say where your measurements disagree with it. A `confirm` has queued a retest of this
candidate, unchanged (`Retest {id} to confirm ...` in `.postmortem/backlog.md`): don't propose another; an
`on_candidate` follow-up waits on it. An `ask` (a keep missing the guard) waits for the orchestrator, who keeps it or
queues that retest.

A result that isn't kept rejects this implementation on this champion, not the idea. Work out which it was,
while you still know the code. This candidate is final: leave its commits as they are, and change `bot/` or
`tests/bot/` only for a quick fix on top of it, when the section on it below offers one. You may read the games,
run the bot tests, and play diagnostic games with the gated quick or `games` command from your task (`games` for a
scratch copy, for example with one of your switches in `bot/params.hpp` turned off).

1. **Idea or implementation?** Did the behaviour fire where intended, how often, and what did it cost when it
   did? Look for a bug, a misfire, a missing piece that the idea needed, or a part that probably hurt.
   Say what you measured. Check these common ways a right idea loses on details:
   - **A narrower reading than the idea:** does your commit's `Choices:` line (or, without one, the code) read
     the idea more narrowly than its text without a measured reason, e.g. enemy replies without sprints (E0109)?
     Then the implementation failed, not the idea, and the full reading is a follow-up.
   - **Someone else gets there first:** does the behaviour send us after something a teammate or enemy is
     nearer to (a pearl the reporter will eat, a target already claimed)?
   - **Churn:** does it change targets or direction so often that dragons turn back and forth?
   - **Clashes with existing behaviour:** does another rule already do the same thing (counting it twice) or
     pull the other way (farming, splitting, rams, exploration weights)? Name the rule and file.
   - **Stale or wrong information:** how old is what it acts on, and how often is it wrong on arrival?
   - **New information misused:** when your change adds information (sharing, sensing, memory), more of it can't
     lose by itself: a decision that reads it chose worse. Find that decision (the rule and file), measure what it
     cost where it acted on the new information (n and chance), and propose its fix with the information retested
     on top of it (`"on_candidate": true`, or `"after"` when the fix helps alone). Give one line
     `Consumer: <decision, file>: <measured cost>: <the proposed fix>`, or `Consumer: none at fault: <what you
     measured>`; only that second form lets you call the information itself the loss (sig, 2026-09-30 15:32:
     "sharing portal pairs and info should never hurt": E0414's extra deaths were the champion's old pocket loop).
   - **Untuned numbers:** are its thresholds and weights first guesses that could be tuned?
   - **Right against some opponents or maps, wrong against others:** compare `per_opponent`, and each opponent's
     trials by style and size in `.postmortem/trials.jsonl`, and the screen's opponents in `.postmortem/screen.json` when there is one.
     If they pull apart, the fix is usually a condition on something the bot can observe (their numbers, longest
     seen, how they fold or fight, the round), not a different constant: name it and propose that version.
     When a screen opponent held the candidate back, your proposals' `Options:` include porting that opponent's
     behaviour where it beat us, behind a condition the bot can observe (REFLECT 2026-09-29 F12: starterking held
     E0320 back and no step asked to port what it does).
2. **What next?** Propose 0 to 3 follow-up experiments that would test the idea better on the current
   champion: a fix, one piece on its own (your other switches off), the missing piece, or the more valuable
   use. Each one must test one attributable behaviour. Your quick games (about 24) can't measure small
   effects, so don't judge a piece on them: propose it as a follow-up and let the referee measure it. If the
   idea looks right but its numbers are guesses, say which numbers a tuning run should cover once a version
   is kept. Propose nothing if the evidence says the idea itself is weak, and say why.
   If a follow-up builds on this candidate's code (a fix to it, or something that needs its switch), add
   `"on_candidate": true`: this candidate isn't in the champion, so when the result may yet be a gain (promising, or
   cut off at its pair cap, from the loop's retest threshold) the loop queues a retest of it first (or, when you make a quick fix below, waits on that
   fix instead) and runs your follow-up after that; otherwise your
   follow-up runs with this candidate cherry-picked under it (a stack), since an unchanged retest would repeat the
   verdict.
   **What else would it need?** Ask what other change, missing from the champion, this one needs to work: the
   information it acts on (an estimate it had to guess), a gate that still blocks what it lets through, a behaviour
   it was meant to feed. The referee judges one change at a time, so value that exists only in combination looks
   like nothing (E0097 relaxed one split gate and read -3.0; E0101 turned three off and read +19.3). If you find one,
   propose it with `"prerequisite": true` only when it would help the champion on its own: the loop queues it first,
   then a retest of this candidate on top of what it leaves (two tests). When it pays only together with this
   candidate's code (a fold partner for a walk that brings the king to it), propose it `"on_candidate": true`
   instead: it is built on this candidate and the pair is tested once (sig, 2026-09-28: no rebuilding from scratch).
   If the result has `stacked_on`, your candidate already carried those experiments' candidates under your idea
   (prerequisites the champion lacked): say which part the games blame.
3. **Could it have been known before coding?** From data the loop already had (saved replays, rule counts,
   study.md, the code), name the cheapest check that would have shown this result, or say none would. End that
   part with one line `Predictable: yes: <the check>` or `Predictable: no: <why>` (E0117: yes: count what the
   fold-window split children gave the king; about 1.5 length a game, too little to win on). A cause you give from a
   few watched games is a hypothesis: say how many games it rests on, and measure it across all of them when you can.
   Every count you give carries its n and chance (sign test or interval, `loop/chance.py`).
   The loop collects these lines for reviews.
4. End your answer with `Blocked by: <what the bot or the loop lacked>` or `Blocked by: none`.{quick_fix}

{idea_rule}

Write the proposals to `followups.jsonl` in this clone, one JSON object per line, with these fields:
`"category"`, `"priority"` (1 = do first ... 5 = later, by expected payoff), `"title"`, `"hypothesis"` (what to
build and why, the evidence behind it, and the scenarios to test). Optional: `"evaluation"`, e.g.
`{{"elo1": 10}}` for a fresh confirmation of a promising result, or `{{"opponents": {{"earlyfold": 0.33}}}}` for
one acting on a style the rotation lacks (an entry of `loop/meta.py`'s `MIX_OPPONENTS`: {mix_opponents}; share 0.25-0.5;
the retest the loop queues keeps this result's mixes); `"kind": "speed"` for a change that only makes
the bot use less CPU while playing exactly the same moves (the referee checks identical games instead of an
SPRT); `"kind": "identical"` for a change that must leave every normal game's decisions unchanged, e.g. stricter checks on
received sonar that only matter under attack (the same identical-games check, CPU p99 no higher, no speed gain
needed); `"kind": "noloss"` for a change whose effect on real games is none by design but which changes local test
games, saying why in the hypothesis (the SPRT then only has to show it is not worse); `"kind": "formula"` for a
change that only replaces a tuned constant or hard cutoff with a formula over what the bot observes, like for like
(tested for no loss too); `"kind": "targeted"` with `"target": {{"measure": ..., "direction": "down"}}` for a fix
of a rare way we lose, kept on no loss and that measure improving (its measures: LOOP.md: Targeted ideas;
a targeted result's per-unit target values are in `.postmortem/target-units.jsonl`); or `"kind": "tune"` with `"tune": {{"params": [names from bot/params.hpp], "constraints": [...]}}` when the fix is retuning 3-8
existing numbers (an order they must keep, such as `"beaconRound <= foldRound"`, goes in `constraints`: the tuner
enforces only those, not orders stated in the text). A follow-up that only adds debug-log fields (the
debugIndicator line, off in real and screen games) is written `same change as I###` naming the waiting log-field
idea in `.postmortem/backlog.md`, which merges it, not as an identical idea of its own: each paid a full opponent
screen (E0127, E0134). Never forbid a behaviour in the text by citing a past result; cite results as evidence with
their conditions. The loop adds valid proposals to the backlog.

End with a short summary: idea or implementation, the evidence, and what you proposed. A change that adds
information is discarded as an idea only when its `Consumer:` line finds no consumer at fault.
