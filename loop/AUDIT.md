# Replay audit: watch our games like a coach

You are auditing how our UNSW Battlecode 2026 bot actually plays. Numbers only catch what someone thought to
measure; your job is to watch the games and find what nobody has thought of yet: dragons doing something
pointless, wasteful, dangerous or simply weaker than they could, and situations the bot handles badly. Look at
everything: movement, targeting, exploring, eating, splitting, head-ons, rams, sprints, crowding, teammates
getting in each other's way, sonar, the endgame, how games are lost.

`context.md` lists the games to watch, which side to watch in each (ours, or the leader's in a leader game), with
bceval's turning point for that side (where to start watching, not a finding), and where the champion's code is.
`briefing.md` is the orchestrator's short list of what every piece of research should assume; read it first, after
the blind watch (step 0).
`study.md` is how the ladder's top five teams (and the dev benchmark) play next to our current submission, with the
largest gaps between us.

## Tools (run with the absolute paths given in context.md)

- `watch.py scan REPLAY... --team B`: moments worth watching (circling in place, hungry stretches, deaths by
  cause and matchup, young split children dying, leads lost; in a game with our debug lines, a death on purpose, a
  feed or a split-out's parent that no enemy head killed, is `deliberate death` instead). `--tally` counts them per game across many
  replays. The scan is a pointer, not the audit: look beyond what it flags.
- `watch.py dragon REPLAY --id N --rounds A-B`: one dragon round by round: where it was, what it did, what it
  could see (pearls, enemy heads with length and distance, allies) and the nearest pearl anywhere.
- `watch.py frames REPLAY --rounds A-B --around N`: the board around a dragon, with kelp, portals, pearls and
  pearl tiles (`?` where neither the map text nor pearl countdowns give them, as in every site replay since
  2026-09-27 ~22:40 AEST: they zero the map text's timings and carry no countdowns).
  Use it to see what a dragon faced.
- `watch.py doom REPLAY... [--summary]`: for each death, the round from which the dragon could no longer get out
  (`since`), what shut it in then (its own move, ate, a portal, a teammate's or enemy's body, born doomed; or never:
  killed with room) and whether it was in a portal pocket; a feed (from debug lines only) and a split-out's parent
  are their own kinds. `--summary` is a per-map table of our deaths against the opponent's, dev and ladder games
  apart: deaths a game and each kind's share of them, with two-sided sign tests over games.
- `watch.py why REPLAY --rounds A-B [--dragon N] --side A|B --commit SHA`: plays one of our games again with our
  exact build and its debug line on (under a minute; only for the listed games of the live submission, with the side and
  commit context.md names: another version's game played with that commit diverges at once) and prints, for each of our moves, the rule that took it and every move's score
  term by term. After `watch.py doom` names the round a dragon was doomed (or `dragon` and `frames` find the move
  that lost it), read that move's scores against the others. It stops at the first round the game plays out
  differently (DIVERGED), and refuses a game no map file here plays again even with every dragon's recorded replies
  ("not reproducible": the site played pearl timings we don't have), which says nothing about our bot.
- `replays.py summary|deaths|lengths REPLAY`: whole-game numbers and every death.
- `blunders.py REPLAY... --team B [--worst N]`: every move of ours replayed against its legal alternatives for 2
  turns (others' moves as recorded): moves where another was clearly better (regret), grouped by situation so new
  kinds of mistake show up, with the worst cases and the `watch.py` commands to open them. Tactics only. Pearl-only regret is scored against what
  the dragon really ate over the next 5 rounds, and withdrawn when it or a teammate ate the pearl; what's left
  can still include pearls the bot couldn't see or refused pockets, so check a few before acting on them.

## Method

0. Blind watch first (MISSES 64 level 3: sig found by watching one loss what every count had missed). Before
   reading briefing.md, study.md, earlier-findings.md or any tally, watch context.md's "Blind watch" games turn
   by turn with `dragon` and `frames`, following several dragons from the start to the loss, and write what you
   saw in findings.md under `## Blind watch` (plain notes, no counts). Only then read the rest, and mark each note
   covered (the earlier finding, study.md line or waiting idea that already names it) or uncovered. An uncovered
   one is an analyser miss: measure how common it is (step 3), make it a numbered finding saying which count or
   tool should have caught it, and queue its fix.
1. `scan --tally` and `blunders.py` over all our games, then over the leaders' games, to see what is common for
   us and not them; open the largest regret groups first.
2. Pick our worst losses and the moments where the game turned. Watch them with `dragon` and `frames`, and
   follow the dragons around those moments, not only the one the scan flagged.
3. For each suspected problem, **measure how common it is** across all the listed games (by `--tally`, or by
   checking several games yourself) before reporting it. One odd moment is an anecdote; a pattern in many
   games is a finding. Compare with the leaders: if they do the same, it may be fine. A count of end reasons or
   death places (hitWall, deaths in a pocket) is never a diagnosis (and noValidAction is an unpaid sprint, an
   illegal split, a malformed command or no output, never a boxed-in head: docs/unsw/death.md): a finding about deaths gives `doom --summary`'s
   table and at least 3 cases drawn with `frames` at since-1, since and the death round,
   and names the mechanism with its count (MISSES 64: a study filed 11 hitWalls a game in our Queen Of Spades pocket
   as the split-out pattern; watched, they were our own dragons walling each other into its dead ends). The listed
   games are chosen losses first, `runs/E####/losses` holds only lost games, and an online index's `games` holds replays kept because
   we lost (`"kept": "loss"`): watch those, never read a rate or mean from them. Our online win and loss rates, by
   map or opponent too, come from every game (context.md's records: the index's `results` and `games` together,
   online.all_games), and a rate or mean from replay summaries from the unmarked replays (online.sample_games). An
   audit that counted `games` alone read 8213 losing 60% to the dev teams; over every game it lost 24%, as before
   (MISSES 55).
4. Find the cause in the champion's code (file and function), and say what the bot saw and decided there.
   If the same behaviour is right in some games and wrong in others (folding early beats a swarm but loses a
   tiebreak to a folder), don't just pick a side: say what the bot could observe to tell those games apart.
5. Compare by situation, move by move: for each of our three largest regret groups, find how often the leaders'
   dragons get into that situation per game (the same blunders.py label, or the moments before it) and watch at
   least two leader moments of it with `frames`: what do they do instead, earlier or in the moment? Their answer
   is often the idea. If no leader games are listed, say so at the top of findings.md as a problem, not a footnote.
6. Watch what the leaders do well that we don't do at all, since a missing behaviour leaves no blunder to find: how
   they open, when and how they fold, how they hunt or avoid head-ons, what their sonar carries and how their dragons
   act on it, how they use pockets, portals and fast tiles. Start from the largest gaps with us (study.md in your
   folder has them; check a gap with `replays.py summary` over their games and ours, e.g. head-ons started on longer
   enemies, sonar volume by phase) and watch the moments behind each number with `dragon` and
   `frames`. For each behaviour worth copying: what they do, in which situations (it may only pay against some styles
   or maps), how often per game, what our bot does there instead, and what it would need to observe and decide to do
   the same. Queue the best as ideas to copy it, behind a switch, conditional where the evidence is. A finding
   "leaders do X, we don't" also queues a replay study of what the leaders observe when they do X and when they
   don't (their rule), beside or before our remedy (REFLECT 2026-09-29 F14: I918 found the leaders cross portals
   and nobody studied how they choose which).
7. Watch the opponents' mistakes too, in our games and the leaders': what does each one give away (folding
   early, leaving its king alone, crowding, predictable paths), and how could we see it and punish it? Every
   weakness of ours you find also asks: when an opponent does this, can we exploit it?
8. Generalise before you write it up. When the same shortfall shows on several maps or in several situations (our
   dragons reach the rich ground late on Devil, Trophy, Trauma and Schooltime), it is one finding, not one per map:
   measure the quantity that is behind in all of them, the same way on every listed game (fast pearls eaten by r100,
   ours against the opponent's; rounds until our first dragon leaves its home region or crosses a portal), and say
   what the bot can observe that tells those maps apart from the ones where it plays well. The 27 Sep 02:35 audit
   measured the maps one by one and queued four map-shaped remedies for one finding.

{idea_rule}

## Output (write only these two files in this folder)

`findings.md`: number the findings A1, A2, ..., each under a heading `## A1. <the general quantity>` (the
orchestrator decides on each by its id; loop/decisions.py). Each finding opens with the general quantity it measures
(step 8: stated for any map, with its n and chance), then what happens, evidence (game file, round, dragon id), how
often (per game, across how many games, and for the leaders if relevant), the likely cause in the code, and your
confidence. Per-map or per-case sections are that
finding's evidence, under it, not findings of their own.
Also list anything you checked and found to be fine, unnumbered, so the next audit doesn't redo it.

`ideas.jsonl`: up to 5 experiments, one JSON object per line: `"category"`, `"priority"` (1 = do first ... 5,
by expected payoff), `"title"`, `"hypothesis"` (the general quantity, then what to change and why, the evidence, how
often it happens, and the scenarios a coder should test, including a boundary; the per-map cases go here as
scenarios). Each tests one attributable behaviour, and one finding gets one idea per mechanism: its conditional
rule, as the shared rule above says. Before calling
something the bot does wasteful, trace where its output goes in the replays and give the number (E0117 stopped the
fold-window "shredding" unmeasured; its children turned out to give the king about 1.5 length a game); every count in a finding carries its n and how likely luck alone gives it (a sign test or interval, as
`loop/chance.py` computes; "suggestive" above p 0.1); a finding whose payoff you can't measure yet is proposed as measure-first (the first step is the
measurement, with a stop condition) at priority 3 or lower, not as a build. `backlog.md` lists the waiting ideas,
then the tested ones with their results. Before proposing, name by id in your hypothesis the waiting ideas that act on
the same decision. A change already there, or a part that does nothing alone without one of them, is `same change as
I###` with your evidence (a part names its own switch); one needing a waiting idea that changes play on its own takes
`"after"`; otherwise say how it differs. Never forbid a
behaviour by citing a past result; cite results as evidence with their conditions. The loop validates these and
adds them to the backlog. Optional: `"kind": "speed"` for a change that only makes the bot use less CPU while playing
exactly the same moves (the referee checks identical games instead of an SPRT), `"kind": "identical"` for a change
that must leave every normal game's decisions unchanged, e.g. stricter checks on received sonar that only matter under attack
(the same identical-games check, no speed gain needed), `"kind": "noloss"` for a change whose effect on real games
is none by design but which changes local test games, saying why in the hypothesis (the SPRT then only has to show
it is not worse), `"kind": "formula"` for a change that only replaces a tuned constant or hard cutoff
with a formula over what the bot observes, like for like (tested for no loss too), `"kind": "targeted"` with
`"target": {"measure": ..., "direction": "down"}` for a fix of a rare way we lose, kept on no loss and that measure
improving (its measures: LOOP.md: Targeted ideas), or `"kind": "tune"` with
`"tune": {"params": [names from bot/params.hpp], "constraints": [...]}` when the fix is retuning 3-8 existing numbers
(an order they must keep, such as `"beaconRound <= foldRound"`, goes in `constraints`: the tuner enforces only those,
not orders stated in the text).

End with a short summary of the findings.
