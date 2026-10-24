# Riser study: what changed in the teams whose rating jumped

Some of the ladder's top teams gained far more rating over the window than their battles would give by chance, and
won more games than their ratings predicted (a new bot's rating moves a lot on small wins, so rating alone isn't
enough); or a team just took rank 1. Something in their play changed. Your job is to find what, from their ranked
games before and after the rise, and to turn what we can use into ideas for our bot: copying what works, and
exploiting what their new play gives away.

`context.md` gives the pick (every top team's climb, battle count and chance, and which were picked), then one
section per studied team (why it was studied: its rise or a new rank 1): when its climb began (its low) and peaked,
its rating path, its ranked records before and after (games won, the rating gained, games won against its ratings'
prediction with the chance of that by luck, opponents met on both sides), the replays (file, their side, period,
opponent, map, result) and per-game means of their side before and after from `tools/replays.py summary`. `teams/<id>.json` holds every game's
full summary, both sides. The site names no submissions since ~19:30 AEST 2026-09-28, so before and after are split by
time at the low: a team may have changed bots more than once on either side, and a few games near the low may be
the other version. Only ranked games are listed: teams play old or weaker builds in unranked games, and some even
in ranked ones; a team's "Decoy check" line says whether its latest games score below an earlier version's beyond
luck (then left out of its means; it reads only the games up to the rating peak, so play since then is unchecked): weigh a style change there as possibly a decoy, not the team's real play.

What we already do: `champion-params.hpp` (every behaviour's switches and numbers, with comments) and the champion's
code (its path is in context.md), `bot-rules.md` (how the bot is built), `playbook.md` (what the loop has learned),
`briefing.md` (what research assumes: read it first; it overrides older notes and `earlier/` findings) and `study.md` (the top five and the dev benchmark next to us).
`backlog.md` lists the waiting ideas, then the tested ones with their results. `earlier/<id>.md` holds the findings
of the last riser study of a team studied before (its section in context.md says so): for that team report only what
changed since those findings, and don't re-measure what they already settled.

## Tools (run with the absolute paths given in context.md)

- `replays.py summary|deaths|lengths REPLAY`: whole-game numbers, every death, per-round lengths.
- `watch.py scan REPLAY... --team A|B [--tally]`, `watch.py dragon REPLAY --id N --rounds A-B`, `watch.py frames
  REPLAY --rounds A-B --around N`, `watch.py doom REPLAY... [--summary]`: moments, one dragon round by round, the
  board around it, and when and why each death became unavoidable.
- `blunders.py REPLAY... --team A|B`: moves where another was clearly better, grouped by situation.

## Method

1. Per team, compare before and after on the same measures, their side only, and say which moved: splits (count,
   first round, child and parent lengths), deaths by kind (noValidAction apart: an illegal split, an unpaid sprint, a
   timeout; illegal-split culls are a style, not a mistake), units alive (r100, r300, peak), longest and second
   longest at r400 and at the end (r499), whether and when they fold into one king, pearls by phase, head-ons started
   by relative length, sprints, sonar volume (what it carries only as the Sources line below allows), portal crossings, and the opening (first moves,
   map seen, pearls in view). Every difference comes with its n (games per side) and how likely luck alone gives it
   (`loop/chance.py`: `rates_p` for counts of games, a sign test or interval otherwise); above p 0.1 it is
   "suggestive, n = ...", not a finding.
2. Watch the moments behind each difference that holds (`dragon`, `frames`, `doom`), at least two games a side: what
   they now do, in which situation, and what they did there before. A watched moment is a hypothesis until counted
   over all the listed games.
3. Say what won them the games: which opponents and maps improved (context.md's records by opponent, and the replays'
   maps), and how the games they now win are won (the end reason, the round the lead appeared, the lengths at r400).
   A rise against weaker opponents only is a different finding from one against teams near us.
4. Check each change against what we already do (params, code, playbook, backlog): we may already do it, have tested
   it (name the experiment and its conditions), or do the opposite for a measured reason.
5. Look for what their new play gives away: every habit an opponent adopts is one we can see and punish.

{idea_rule}

## Output (write only these two files in this folder)

`findings.md`: number the findings R1, R2, ..., each under a heading `## R1. <team>: <the change>` (the orchestrator
decides on each by its id; loop/decisions.py). Each opens with the measured change (before -> after, n, chance),
then the watched evidence (file, round, dragon id), what it won them, whether we already do it (with the param,
function or idea id), and your confidence. A team with no change you could measure gets one finding saying what you
checked. A finding about several teams ("the risers all split earlier") gives a per-team table (team, n games
before and after, the submission ids they come from where context.md gives them, that team's own measure) and names
the teams it rests on: a team with a game or two counts for nothing. List what you checked and found unchanged,
unnumbered, at the end, then one `Sources:` line: the replays used, by submission id where context.md gives one
(else by period), ranked games only, each submission or period used or ruled out with a reason (too few games, a
possible decoy: a ranked build weaker than the team's best recent one, an old build). Decode sonar only when the
briefing says sig asked for it, and then as understanding of their play, never as an idea's input.

`ideas.jsonl`: up to 5 experiments, one JSON object per line: `"category"`, `"priority"` (1 = do first ... 5, by
expected payoff), `"title"`, `"hypothesis"` (the measured change and its evidence, what to change in our bot and why,
the scenarios a coder should test with a boundary), and an `Options:` line naming at least three numbered remedies,
`(1) ... (2) ... (3) ...`, the one you propose first. Before writing one, check `backlog.md` and name by id in your
hypothesis the waiting ideas that act on the same decision. A change already waiting, or a part that does nothing
alone without one of them, is `same change as I###` with your new evidence (the loop merges it into that idea; a part
names its own switch); one needing a waiting idea that changes play on its own takes `"after"`; otherwise say how it
differs. For a lever already tested name the experiment(s), their result and conditions, and what differs now. Never propose what the champion already does (check params and code); never forbid a behaviour by citing a past result. No
hand-set numbers: a rule depends on what the bot observes, or its constant is fitted and says what it depends on
(a bare threshold is refused unless the text says "bare threshold because ..."). A change whose payoff you can't
measure yet is proposed measure-first (the measurement first, with a stop that fires on evidence against it) at
priority 3 or lower. At least one idea, when the evidence allows, exploits a habit a riser now shows. Optional
fields as the backlog's other sources use them: `"kind"` (`"speed"`, `"identical"`, `"noloss"`, `"formula"`,
`"targeted"` with `"target"`, or `"tune"` with `"tune": {"params": [...], "constraints": [...]}`) and `"after"`.

End with a short summary of the findings.
