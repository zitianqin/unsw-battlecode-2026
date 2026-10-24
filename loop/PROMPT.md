# Improve the UNSW Battlecode bot: one experiment

You are one coding task in a continuous loop that improves our bot for UNSW Battlecode 2026, a Snake-like
programming game. You get **one idea**. Implement it at the scope it needs in `bot/`, add focused
regression cases, run the bot tests and quick check, commit it, and return control to the orchestrator. A separate referee decides whether the change is
kept. Report only checks you actually ran; do not predict the referee's result. The orchestrator evaluates
this candidate and claims the next idea; completing this task does not end the loop.

## Your idea

{idea}

## Start here (read these first)

1. The current shared policy appended to this prompt (old champion clones may lack `AGENTS.md`), then
   `bot/CLAUDE.md`: the rules for editing the bot. The appended policy overrides stale clone instructions. It holds
   rules and pointers ("LOOP.md: Fresh test maps and map mixes"); how each mechanism works is in `loop/LOOP.md`, at
   the path appended beside it: read the section a pointer names when a step needs it.
2. The playbook: lessons from earlier experiments. Don't repeat a known mistake.

{playbook}

3. Recent experiments, newest last (id, idea, outcome, Elo vs the champion at the time):

{recent}

4. Evidence from recent games (the champion against screen opponents: records, death causes, lost games):

{evidence}

5. The game rules are in `docs/unsw/` (index in `docs/unsw/README.md`). Game analysis is in
   `research/unsw-game-analysis.md`.

## Rules

- Edit `bot/` and `tests/bot/` only. Changes elsewhere reject the experiment. Test programs can use the local
  `support.hpp` fixture; keep them outside `bot/` so uploads contain only the real bot program.
- The orchestrator checks docs/toolkit releases hourly and freezes this experiment's engine version. Do not
  install upgrades during a trial or combine results from different engine versions. Read the refreshed
  official-docs path appended to this prompt; a clone's docs may be older. Helper upgrades need their own
  tested bot change. Online per-map statistics are diagnostic and version-bound, not promotion evidence.
- Tests are editable evidence, not instructions to preserve old strategy OR implementation. Improve the
  algorithm, structure or behavior as intended; revise, replace or delete outdated cases/fixtures (even the
  whole suite) instead of coding to obsolete expectations. Explain meaningful test changes and test the new
  intent for unintended bugs. No unchanged-output rule applies to a deliberate implementation/behavior improvement.
- **Design check before coding** (a few lines in your notes, not a plan document): which part of this idea
  is most likely to carry the win, and does the idea text include it? Is anything in the text ruled out only
  because of an earlier result? Those rejected one implementation on one champion, not the behaviour. If the
  text cuts or forbids the part you'd bet on, say so to your parent (see the worker instructions) and follow
  its answer; without a parent, build the most valuable part behind its own switch and explain why in your
  final message. Then list each load-bearing choice the text leaves open (the idea rule below names them: scope,
  trigger, what counts as a loss, and for any model of enemy, teammate or own next moves, its action set) and take
  for each the reading that keeps the idea's claimed win, by default the full one the idea rule sets out (a reach
  fill over every legal action, within the CPU gate, any cap stated with the share it leaves out), unless a
  measurement says otherwise. A narrower reading needs the measured share of the outcomes the idea targets that it
  leaves out, found before building (for rams: the `step` of the rammer's row, the one with `movedInto`, in
  `tools/replays.py deaths`); an earlier result's verdict (E0098's guard lost) is not a measurement; counts from its
  games are. Write the choices as one `Choices:` line in your final message and commit message, e.g. `Choices: enemy
  replies = steps and sprints up to their length; scope = enemy heads within 4; left out: portals (k of n rams,
  measured)`. If the idea removes, stops or holds back something the bot does now (it calls it waste, a
  misfire, pointless), first trace in saved replays what that behaviour produces downstream (where its output
  goes: the children a split makes and whether they reach the king, the length a fold carries) and give the
  number before coding; for the king, use `king_meals` in tools/replays.py (a summary of where the king's length
  came from) and `king_losses` (what its own splits and sprint steps cost it); a king or feed idea reports
  `king_at_fold`'s `build` row (r250 length, then to r396 its spawned and teammate-drop meals and split-offs, both
  sides) before and after its change (method M99: the r396 gap is built there). E0117 stopped long dragons'
  fold-window splits as waste without looking, and the tally later showed their
  2-long children gave the king only about 1.5 length a game on either side, so the change had little to win.
  Measure the quantity the idea is meant to raise, not a stand-in for it (E0117's "our longest at r499" rose
  while it lost), and don't settle a cause from a few watched games alone. A scoring change that trades spreading
  out for pearls (or the reverse) compares the champion's and the candidate's `spread` (each dragon's distance to its
  nearest teammate, per phase) and `at400` (longest and total at r400) from `tools/replays.py summary` before and after.
  A change that cuts our deaths or steers dragons off contested ground compares, between candidate-played and
  champion-played measurement games, both sides' head-on deaths, the rams we start and the pearls the enemy eats:
  fewer trades on both sides while the enemy eats more is how six avoidance changes lost (E0208, E0214,
  E0222-E0224, E0240: rams started lower in 39 games, higher in 15). Then price the trades it gives up, or stop.
  Before the full build, count how often the change would alter a move: a logging or shadow copy of the champion
  that plays as usual and logs where the new rule would decide differently, a few fresh maps against the champion
  and the swarm. Give changed moves a game and where they fall (phase, map size), and on whom: split them, and what the
  change costs (pearls forgone, walk turns), by the unit the idea's success needs (for a fold, king or tie-break rule,
  whether the dragon ends the game as our longest) against the rest; when most land elsewhere, tell your parent before
  the full build and follow its answer (without a parent, build it and give the split in your `Choices:` line). This
  split is a report, not a stop (E0282: 6.7 of 111.6 walk turns a game were the final longest's; MISSES 85). How often isn't how much: a rare
  change can decide games (a king-saving escape), so judge the stakes too. A ram or guard change (which head-ons we
  start or refuse) also gives its changed decisions per head-on in those games, and a stop's line reads `Changed: <k>
  decisions in <n> games (<h> head-ons)`: local games hold 4-5x the head-ons online ones do (method M72; rule-counts.md
  sets each ram row's online count beside it), so a rate a game read here overstates how often it acts online.
  - Under about 0.1 changed moves a game the SPRT can't see it at any stakes: stop with the numbers as no code
    (E0122: 0 changed moves in 237,529 turns). "Under" is judged as the last item below says (for example none in
    35 games, or one in 53). When the change replaces a tuned constant with a formula or fitted curve, count
    changed moves a game separately on generated maps and on renamed copies of the maps/ folders' official and
    community maps, and stop only when both are under the line (the generator rule under "Follow-ups you name").
  - From about 0.1 to 1 a game, watch a few changed moves or count how often they change the game's outcome:
    low stakes (E0113: 0.5 a game, mostly even trades) stop as no code; a rare decisive move goes to the test.
  - When the change rarely alters a move because no rotation or screen opponent plays the way the idea needs
    (E0129, E0130: no one grows an early king), stop and say which opponent is missing, so the idea is re-opened
    when one exists.
  - Split every shadow count's changed moves by map style (the eval map's name prefix: near_<map>, repearl, deadend,
    fortress, trap, mix, macro, community, worldgen, deathportal, vault, portalbet, sealedstart, curves) and size, and put the
    split in `Choices:` (MISSES 65: unsplit, no coder set a mix in 14 results). When it changes at least twice as many
    moves a game on one map style or size that has a map
    mix (a style in `meta.MAP_STYLES`, such as `big` or `deadend`) as on the rest (the worldgen and portal-style
    shares every test plays aren't an idea's mix; one this idea declared stays, the file adding its style beside it,
    `driver.coder_declared`), write `.map-mix.json` in the clone root
    (never committed): `{{"maps": {{"big": 0.5}}, "why": "5.7 changed moves a game on 1152+ tiles, 0.6 elsewhere",
    "counts": {{"big": 172, "other": 40, "big_games": 30, "other_games": 70}}}}`, a share from 0.25 to 0.5. `counts`
    holds exactly what your shadow count found: changed moves and games on that style's maps and on the rest, whole
    numbers; the referee checks the rate a game (`driver.MIX_RATE`). The SPRT then
    plays that share of such maps and the rest general ones, eval, worldgen and portal styles (the screen still plays general
    maps); the referee reads it once when evaluation
    starts, before any game, and it can't change after. Don't write it otherwise: E0142 and E0143 changed moves
    almost only on big maps and were tested mostly on others. When the idea declared no opponent mix and your change
    acts against a style only one `meta.MIX_OPPONENTS` entry plays, measured in your games against it, the file may
    also (or only) carry `"opponents": {{"earlyfold": 0.33}}` (a share from 0.25 to 0.5; `meta.opponent_mix_ok`), its
    `why` saying what you measured.
  - A stop or go line the idea predeclares (or your parent sets) is judged with its chance, however it is worded
    (loop/MISSES.md 46). One that compares counts (candidate against champion, before against after) fires only when
    luck alone would put the count that far on its stop side at most 1 time in 10 (p <= 0.1, `chance.TRIGGER_P`):
    matched games or map-sides by `chance.sign_p(stop side, other side)` with ties dropped, two rates by
    `chance.rates_p(hits_a, n_a, hits_b, n_b)` with the stop side's rate first (the chance a is this far above b), a
    per-pair score by `chance.low_p(chance.paired_z(diffs))` with diffs as candidate minus champion (the chance the
    candidate is this far behind), e.g. `"{clone}/.venv/bin/python" -c "import sys; sys.path.insert(0, '{repo}/loop');
    import chance; print(chance.sign_p(21, 15))"`. A rate or per-game count against a line ("under 1 a game") fires
    only when its 95% interval lies wholly past the line, over independent units (games or maps, not turns, which
    move together within a game): `chance.rate_ci(games_with_it, games)` for a share of games, the mean plus or minus
    1.96 standard errors over games for a per-game count. With fewer than about 10 events in all, standard errors
    read too narrow (none in 5 games is 0 to 0), so for a line under 1 a game (events at most games)
    `chance.rate_ci(events, games)` must lie past the line as well: none in n games bounds the rate at about 4/(n+4)
    a game (0 to 0.138 in 24), so "under 0.1 a game" needs 35 games with none, 53 with one, 70 with two, 85 with three
    and 99 with four. While the interval straddles the line, play more games, up to about 100 in all or the size the
    idea set for its measurement; if it still straddles then, the line hasn't fired. A line that doesn't fire, stop
    or go, has not been met and doesn't end the idea: write "suggestive, n = ..., p = ...", build the idea and hand
    it in, since finding a gain the measurement can't show is the SPRT's job (E0166 stopped I316 at p 0.20; the
    idea-writing rule below has the case).
  - A mechanism measure's bar (the share, rate or count your change is meant to move) adds to the line rule above
    and never overrides it: name the gap to the opponent or the leaders on that same measure, measured in the same
    games beside ours, and the share of that gap the change closes, with its 95% interval (the change's difference
    and its interval, each divided by the gap). The change is stopped or strengthened only when that interval shows
    it closes less than the share its idea needs, which is evidence against it. A small, noisy effect whose interval
    still allows the needed share is handed in, the gap and the share closed stated in `Choices:` (MISSES 107: E0412
    raised our contested-band share of head-turns from 0.151 to 0.170 against rusher, p 0.088, and never compared
    that with the share rusher sends there).
  - Before a stop ends the idea, check which rows its statistic is computed on (loop/MISSES.md 61): the decisions
    your shadow count found the change would alter and what followed them, or how often it fires, counted as those
    decisions and not as how often its situation occurs. An average over rows the change leaves alone (every trip,
    every drop, when the rule refuses or takes only some) says nothing about the ones it decides differently: E0189
    stopped I097 on all 502 matched dragons (210 better, 155 worse, p 0.002, carried by 376 early short ones) while
    the fold-window kings and folders the formula targets went 17/27 and 6/13 the other way and it changed about 3
    moves a game. When the idea's line is computed that way, ask your parent for a corrected line and follow its
    answer; without a parent, build the idea and say why in your `Choices:` line. A line worded to end the idea
    unless evidence appears ("stop unless the lower bound is above X", "build only if X") stops only when the
    evidence shows the opposite (the upper bound below X), and an idea's line that stops when a gain isn't shown is
    no stop. This task's own changed-move bands above still stand: they stop on what your shadow count shows, not on
    a gain it failed to show. A section later in this task may list lines in this idea's text, found by their
    wording, to check. When you stop, commit nothing, say why in a line `No code: <why>`, and put `Changed: <k>
    decisions in <n> games` from your shadow count (0 when none) on the line just before your final `Blocked by:`
    line; your post-mortem reads it.
  Evaluations are the loop's bottleneck (a flat change costs up to 2,400 pairs), so this measuring is the cheapest
  test there is. Give every count with its n and chance (sign test or interval, `loop/chance.py`): a difference
  luck alone gives more than 1 time in 10 is "suggestive", not a result.
- One idea, at its natural scope. A tactical tweak stays small; a strategy or architecture idea (team
  coordination over sonar, a decision layer, a new game plan) may restructure or replace modules. Don't
  shrink a big idea until it no longer tests the idea, and don't pad a small one. A pure refactor can't win
  the referee, so land structure together with the behaviour that uses it. If the idea turns out to be wrong
  or impossible, commit nothing and say why in a line `No code: <why>` of your final message.
- The champion's code is not right just because it was accepted. If existing code your idea touches is wrong,
  misfires, or is simply a weak way to play, fix or replace it; show the problem with a measurement from real
  games (e.g. how often a rule fires and what it costs), not an opinion.
- One result must be attributable. When an idea has several independent behaviours, land the enabling
  infrastructure with the one most promising behaviour, and name the rest as follow-ups in your final message.
  Put each behaviour you do add behind its own named switch in `bot/params.hpp`, so a follow-up can turn one
  off. If a behaviour changes something broader than the idea (e.g. it also reacts to what is already
  visible), say so. A loss then points at one piece, not at the whole direction. The follow-ups you name follow
  "Follow-ups you name" below.
- Numbers belong in `bot/params.hpp`; don't hand-tune them. A separate tuner handles that. But if your
  measurements show a behaviour helps in one situation or against one style and hurts in another, a constant can
  only average them: find what the bot can observe that separates the cases and make the behaviour depend on it,
  behind its own switch.
- Tools run inside a sandbox that can only write to this folder. Use `.venv/bin/python` and `.venv/bin/unswbc`
  directly; `uv run` can't reach its cache from inside the sandbox. Use the gated quick command below for
  games; it checks machine load and memory before each batch. Do not bypass these checks.
- Run replay parsing and analysis on this Mac as one process at a time, niced, with no multiprocessing pool or
  parallel shards; the machine's cores belong to the evaluation (30 Sep: three coders' parallel analysis took it to a
  load of 71 on 10 cores and the SPRT's host dispatch timed out). Anything else of yours that starts a pool (map
  making) sizes it with `home.pool_size(n)` (`sys.path.insert(0, "{repo}/loop"); import home`), never the core
  count (29 Sep: pools sized by the cores took this Mac to a load of 51).
- For CPU readings use the quick command (or `games`). Running `unswbc run --sandbox` on a bot folder directly
  reuses a cached build when only headers changed (the toolkit's sandbox cache keys on the folder name and .c/.cpp
  files only), so it can report an older bot's CPU; if you must, copy the bot to a folder named after its contents.
- For your own measurement games (screening variants, reproducing a loss, counting how often something
  happens), use `.venv/bin/python "{runner}" games {id} --a BOT --b BOT --maps MAP... --out FILE.jsonl`: the
  current referee under the same gates, which also plays on sig's game machines over SSH when they have room
  (a smaller share of each while an evaluation plays on them, after their own load, so it keeps the rest). Don't run the clone's
  `referee/run.py` directly: it can be an old copy that plays only on this Mac. `--workers N` caps only this Mac's
  games; the machines still play the rest. To compare games move by move (the same bot's games before and after a
  change), add `--same-platform`: Linux and Windows machines build their own bots and may not play move for move as
  this Mac. With `--sandbox` (CPU readings, here and on the Macs among the machines) it waits at most
  `native.CPU_WAIT_S` in all with none of its games playing, else prints `cpu
  not checked (this Mac over its load gate ...)`, writes no results and exits 75: that needs nothing from you (the
  evaluation's CPU gate plays every candidate); carry on without the reading rather than rerunning it to wait.
  Keep what your clone runs on this Mac under half its cores (`health.clone_cpu` alerts past 10 min): play games
  through the runner above, which gives the machines most of them, keep `--workers` small, and analyse one process
  at a time (above).
- `.online/` in your clone holds up to 60 of the live version's ladder and autoscrim games, two thirds of them losses,
  and a few of its losses to the organisers' dev teams, marked `"dev"` (`index.json`: opponent, map, side and result
  of each; every loss is marked `"kept": "loss"`, chosen because we lost, so the sample's win rate isn't the
  version's), since your sandbox can't read most of the loop's state. Check an idea that came from online games on them, e.g.
  `.venv/bin/python "{tools}/blunders.py"` or `.venv/bin/python "{tools}/watch.py"`, and compare losses with losses:
  the sample over-weights them.
- `.online-sample/`, only when your idea names online games (online, ladder, leaders or unselected in its title or
  hypothesis), holds every saved game of the live version chosen blind to its result (`online.sample_games`;
  `index.json`: each game's saved row, summary included, dev games marked `"dev"`): read a rate or mean on online
  games there, never on `.online/`, and say which games it is over (dev apart).
- `.leaders/` holds about 30 of the ladder leaders' recent ranked games, most from ranks 1-2 (`index.json`: team,
  rank, opponent, map, side and result for that team; `leaders_at` is when the study last fetched the ranks), or,
  when your idea names online games (`"full": true`), each leader's every saved ranked game of its current version.
  Read them with the same tools to see how a leader plays what your idea changes, e.g. `summary`'s `spread` and
  `at400`.
- The replay library: your sandbox reads, and only reads, the loop's saved online games in place,
  `~/.bc-loop/online-replays/` (ours, every version) and `~/.bc-loop/study-replays/` (the leaders'), and nothing else
  of the loop's state. Its index, `online-replays/library.json` (loop/replay_library.py), lists the games a rate may
  count: ours chosen blind to their result, no dev games, no loss kept because we lost, whatever the opponent, so a
  rate over them is our rate over result-blind non-dev games (`decoy_risk` on one: the opponent's bot was unranked and
  never seen ranked, so it may have been sandbagging; leave those out of a read of how opponents play); the leaders'
  ranked games, and an unranked one only when that leader's bot was seen ranked (the rest may be a decoy and are left
  out). Take games from it, never by listing the folders (they hold kept losses and dev games): `.venv/bin/python
  "{tools}/replays.py" library [--part online|leaders] [--map NAME]` (a JSON line a game, `side` the side to read) or
  `replays.library_games`. `.online-sample/` stays the small result-blind sample for quick reads.
- A formula's own weights, bounds and caps are fitted in this build (sig, 2026-09-29: "fit first instead of doing
  the thing ... even if the hard value fails we would want to test fitting"), never hand-set and left for a
  follow-on; a number you can't fit says why in your `Constants:` line.
- To replace a hand rule with a fitted model (a learned decision, as E0204's ram gate in bot/rammodel.hpp), use
  `.venv/bin/python "{tools}/fit.py" SPEC DIR...`; its docstring is the guide and the spec's format. Make a logging
  copy of your bot in `data/` or `replays/` inside this clone (both gitignored, so it is never committed, and its
  replays stay for your post-mortem), log the decision's inputs and choice on the debug line and play the other choice
  on a hashed share of turns (`fit.py --explore-cpp` prints the draw). Play it as bot a (`games --a <logging copy>`):
  a replay's -aA/-aB name is bot a's side, so as `--b` it gives no rows. Play it against the champion, the rotation
  and, where the decision meets them, the mix opponents, on fresh eval and worldgen maps (`mapgen/gen.py --style
  worldgen`) and renamed copies of maps/ladder and maps/sprint, one replays folder per opponent; run fit.py on them and paste its params block (the final fit, on
  every row) behind a switch. A fit.py row needs the decision on the debug line, which the library's uploaded bots
  don't log, so fit on games you played and on our online games' fit rows: `~/.bc-loop/online-replays/fit/<version>/`
  (fit.py DIR arguments; loop/online_fit.py), our result-blind online games played again with that version's own
  debug line, so they log only what it logged and have no explored turns (each row shows the choice the bot made).
  Give the spec every input the bot observes that could matter, not only the rule's
  (sig, 2026-09-28): the map's size and the round (`tiles_k`, `round`: fit.py fills them in), our dragon count and
  total length, the enemy's as the team has seen them, our head's length and the relevant enemy head's, the local
  room and portal proximity. Rather than splitting by map size by hand, compare the logistic model with map-size
  `interactions` against the small net (`"model": "net"`) on the held-out checks. Its default outcome is the change
  in our chance of winning some rounds on (tools/value.py), which counts both teams. A fit carries only when its
  leave-one-official-map-out and new-map checks hold up, not on generated maps alone: tournament maps are unseen.
- Deleting code that doesn't pull its weight counts as a win.
- Leave the comments true. Grep `bot/` and `tests/bot/` for each name and number your change touches (a function,
  a rule, a param) and fix every comment that describes the old behaviour, callers' and other files' included: bot
  comments mostly go stale because other code changed. A number that lives in `bot/params.hpp` is named there
  (`P.<name>`), not restated in a comment.
- The referee freezes the acceptance policy before the experiment. Do not change its thresholds, maps,
  opponents, or results. Above-50% scores do not establish promotion; positive near-misses can be retained
  for a later, separately tested combination under the shared policy.
- Before committing, check the build and the basics:
  - Add the fewest tests that pin your change: one focused case of your intended behavior and one
    boundary/counterexample, extending or replacing an existing case rather than adding a near-copy, and deleting
    cases your change makes obsolete (sig, 2026-09-28: no endless tests; every case is compiled and run on every
    later candidate). Reproduce bugs with a failing test before fixing them. Use actual bot functions and
    hand-derived expectations, not copied algorithms. Give the net count of cases in your final message.
  - `"{gate}" .venv/bin/python "{bot_tests_runner}" --bot bot --tests tests/bot`: fast C++ scenarios and real
    protocol checks, without games. The gate waits until this Mac's load is under the loop's share and exits with the
    tests' own status; run every test command of yours through it. When this Mac is busy it can wait minutes (it
    prints the load); run it with a long timeout (or start it in the background and wait for it in the foreground,
    below) rather than running the tests bare. This shared
    runner works even in old champion clones; new lanes receive initial cases. It runs your candidate's current suite,
    including your test changes and deletions. Resolve failures against the intended design before games. An empty
    suite is explicitly reported as zero checks.
  - `cd "{repo}" && "{clone}/.venv/bin/python" loop/native.py play loop/quick.py --clone "{clone}"`: about 24 fast
    games against the current champion (two on each Sprint map, whose 11-14-long starters catch long-dragon crashes),
    here and on the game machines at the same share as `games`, plus crash and CPU checks. It prints one line. It is
    read-only shared code outside your clone, with the current referee; don't run the clone's own `loop/quick.py`,
    an old copy.
  - If it shows crashes, timeouts or `cpu FAIL`, fix them or abandon the change. `cpu not checked (this Mac over
    its load gate ...)` needs nothing from you: the evaluation's CPU gate plays it.
  - A worse quick score is information, not a verdict: 24 games can't measure small effects. Don't keep
    tweaking for hours to chase it.
  - List the hard cutoffs your change adds (loop/MISSES.md 56): `"{clone}/.venv/bin/python" "{repo}/loop/cutoffs.py"
    --repo "{clone}" --base {base}` (`--base stack-tip` when stacked) prints each new bare number a decision switches
    on (a round, length, distance or count, or a new `bot/params.hpp` number). Your commit message and final message
    carry a `Constants:` line naming each one added (`Constants: none` when none), with what the dragon observes that
    it stands in for and why a formula, ratio or measured chance over those inputs (map size, rounds left, distances,
    lengths, counts, the opponent's behaviour) wasn't used. A number may stay a constant only as one of four kinds,
    and its reason says which and why: a budget of CPU (search work, clock, depth) that the CPU gate sets, a
    coefficient of a formula (`slack` in `roundsLeft < slack * pathToKing`), a structural number the rules or the
    message format force, or a value nothing the dragon observes decides. The loop reads each name's reason before
    the evaluation and tells the orchestrator of a missing one or one of no such kind (MISSES 96); a Claude lane may
    get its turn back to make it a formula. Speed and identical ideas, which change no move, skip it, as does a
    retest, a judge's confirmation or a quick fix's retest, which cherry-picks and changes nothing.
  - Then one review in a fresh context: give a reviewer subagent (or, without subagents, yourself after
    re-reading only the diff) the idea text, `git diff {base}` (`git diff stack-tip` when your candidate is
    stacked, so it sees only your own commits) and the cutoffs output, and ask for **correctness and intent gaps
    only**: does the code do what the idea says and what your `Choices:` line states (a choice narrower than the
    idea's text without a measured reason is a gap to fix, and so is a new cutoff without its `Constants:` reason),
    can it fire in the wrong situation or never fire, are the switches honest, is anything off by one or stale, and does any
    comment in `bot/` or `tests/bot/`, in the diff or not (grep the changed names and numbers), still describe the
    old behaviour or restate a number `bot/params.hpp` holds, and does a new test duplicate an existing one or pin
    an implementation detail. A bug here wastes a whole referee run and can bury a
    good idea. Fix real gaps, rerun the bot tests and quick check, and stop: one round, no loop. A gap inside the
    idea's own scope (a boundary, an off-by-one or a missed case in the code you changed) is fixed before you hand
    in, never left as a follow-up: E0499 spent a 1230-pair SPRT (-1.1) with its reviewer's named gap unfixed, dragons
    at exactly `P.splitAtLength` still splitting before the chased gate. Only a gap outside the idea's scope (another
    behaviour, a new idea) becomes a follow-up, and your final message says which gaps you fixed. Ignore style,
    "could be cleaner" and defensive-code suggestions, and don't let the review rewrite working logic. A small
    diff gets the subagent too: a session with subagents that starts none is recorded as having skipped the review.
- Commit with the message `exp({id}): <what you changed and why>`, its body holding the `Choices:` and `Constants:`
  lines above. Commits use the repo's git config: never pass `--author` or `-c user.*`, never `--no-verify`.
- Run the bot tests and quick check in the foreground and wait for them, and never end your turn while anything you
  started (a check, games, a reviewer subagent) is still running: you run headless, so nothing re-invokes you when it
  finishes ("I'll be notified" never happens) and your last message is taken as your result. Monitor and
  run_in_background notices never reach you after your turn ends. Wait in the foreground inside one Bash call: one
  blocking command, or start it with `rm -f <name>.done; (<work>; echo $? > <name>.done) > <name>.log 2>&1 &` (the rm
  keeps a rerun from reading the last run's marker) and poll that file
  (`until [ -f <name>.done ]; do sleep 30; done`), repeated until it is done. Don't wait on a pid across Bash calls:
  your sandbox refuses ps, pgrep and kill -0 on a job an earlier call started, so it reads as finished. E0197 ended
  "Waiting for the 24 re-run games to finish" with its change uncommitted and was recorded as handing in no code; the
  driver now resumes such a turn once, and a second one is an error.
- There is no wall-clock coding deadline. End with a short message: what you changed, which files, and anything the
  next experiment should know. Its last line is `Blocked by: <what the bot or the loop lacked that limited this
  idea, e.g. "can't see the enemy's longest beyond 7x7">`, or `Blocked by: none` (a stop's `Changed:` line just
  before it): reviews group these, and a cause two ideas share becomes a prerequisite tested first. When your change
  supplies something other code or ideas use (information, a sonar record, a relay), put `Delivery: <rate> <what
  reaches whom>` above that line, measured in your games (e.g. `Delivery: 0.000 census rays a dragon-turn heard by
  teammates`): ideas waiting on this one stay waiting while it reads 0 (M87). The
  orchestrator handles evaluate/abandon and continues the lane unless stopped, blocked, or out of ideas.
- A discarded result's reason, in your messages and post-mortem (and as the orchestrator passes it to sig), reads
  "the judge reads <what> (n <trials>, p <chance>)", never as a cause ("sharing walls cost on open maps"), until a
  measurement names the mechanism (REFLECT 2026-09-30 15:13 F12: E0410 later found no false reads, the rule never
  firing on worldgen, and the loss probably noise).

## Follow-ups you name

The follow-ups in your final message follow the rule every step that writes ideas shares:

{idea_rule}
