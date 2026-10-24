# How the loop works

The loop's reference: how each mechanism works, and the fuller wording of rules `CLAUDE.md` states briefly under a
pointer to each heading ("LOOP.md: Dev battles"). Where the two differ, CLAUDE.md's rule stands and this file is
fixed; numbers and lists that live in code are pointers (`driver.ROTATION`), and the code's docstrings are the last
word. The file has a byte cap (`doc_size.LIMITS`): when it is full, move a mechanism's detail into its module's
docstring, or an incident and its measurements into its `loop/MISSES.md` entry, leaving a pointer; never drop a rule.
Its dates and experiment ids are when each thing happened.

## Concurrency and host shares

- Up to `driver.CODING` coding tasks overlap evaluation (its comment has sig's words): ideas are coded while earlier
  candidates play, and finished ones queue for the one evaluation. Test games are the bottleneck: extra coders keep a
  candidate ready when a test ends, and coders' test games take a smaller share while an evaluation runs, so
  evaluations come first on the hosts; on this Mac building does (sig, 29 Sep 18:31: coders', builders' and merges'
  tests run unniced). The players beside an evaluation (the coders, a review's rule-count games, and one more share
  the tune players and the fillers take turns with: the tune players, a tune and the evidence refresh after a
  promotion, `loop/evidence.py`, split it host by host while one plays, else the late screen and official-map reports
  do) together take at most half of each 8+-core host and of this Mac, after the evaluation's own and below sig's dev work (Remote host capacity: niced games).
  Where that half is under a game each, the coders, review and share ration it a game each over
  `native.YIELDING` slots, hosts in turn (`native.coder_slot`, `native.host_offset`, MISSES 57); a post-mortem's or
  follow-on's coder (its clone's lane 0 at the result, `driver.retire_lane`) gets no game on 8+ cores beside an
  evaluation; `health.over_share` alerts when a host's players stay above their share, the coders' total included,
  for 3 minutes in a host stage (its comment: the games counted as a stage begins). A host under 8 cores keeps a
  floor of one game each for the coders, the review and the tune players, none for the fillers. The sweep, the prewarm and the adversarial-map
  search play nothing then. The exact shares: `native.host_share`'s comment. With no evaluation running
  everyone keeps their usual shares, and on the remote hosts also while the evaluation holding the lock is in a stage
  that plays nowhere but this Mac (MISSES 50): its marker (`/private/tmp/bc-loop-evaluating.json`) says `"hosts":
  true` only while `driver.host_stage` holds (its SPRT, then opponent screen, and its identical check),
  which the players beside it (`native.hosts_wanted_now`, `sweep.busy`) step back for within a game each, and
  `"mac"` in its CPU gates (`cpu.py --hosts`, here and on hosts of this Mac's platform: same points, measured 29
  Sep), stepping back there only. While it holds the lock no idle filler plays here (`native.game_slots`). A queued candidate's prepared CPU gate is the next
  evaluation's own check, not a player's: at normal priority, one at a time, only while the evaluation says `"hosts":
  true`, within `native.mac_cap` here and its coder share of the Mac hosts (`driver.prepared_cpu_gate`'s comment).
- A candidate coded on an older champion is rebased onto the current one, and its bot tests and CPU check rerun
  before games. In its merge turn (the Claude driver resumes its coder while its cache is warm, `driver.MERGE_WARM_S`,
  else briefs a fresh agent; beside the evaluation before it when one plays, `driver.prepare`; native `evaluate`
  returns `resolve_required`) it resolves a conflict keeping both changes (`loop/RESOLVE.md`), or after a clean rebase
  checks they still work together (`loop/MERGE.md`), once per champion (`driver.merged_on`). A usage limit's turn
  isn't counted: the candidate leaves lock and queue till the reset, then retries once in place
  (`driver.merge_limited`; a coder's turns too, `driver.limit_retry_due`); a failed turn's uncommitted edits are stashed
  (`driver.set_aside`). A merge turn the
  evaluation waits on (under the lock, or at the queue's head) has `BC_GATE_URGENT=1` in its session's environment
  (`driver.lane_env`), so its tests' `tools/gated_test.sh` gates only at the hard stop (load ≥ cores), not the load
  share: that is for the evaluation's critical path only, never other work.

## Referee refresh

- Changing `referee/`, `mapgen/` or `maps/` goes live by itself: each evaluation (Claude driver or native `evaluate`),
  holding the evaluation lock, first brings `~/.bc-loop/referee` to the `main` branch's tip (not the main checkout's
  HEAD) when main has referee/, mapgen/ or maps/ changes it lacks
  (`driver.refresh_referee`, not for test or doc files other than maps/adversarial/README.md; maps/ too, so a ladder
  map saved to main is played; a map-generator change reaches new map sets only: a set already made, such as the
  champion's shared set, keeps its maps; a tune whose map mix the referee can't make yet does the same, and is blocked
  until it can), so no evaluation straddles it and nothing races for the lock. Coders and queued candidates needn't
  wait: each claim records the referee's commit, and its protected-file check accepts a clean move to a later main
  commit (`driver.refreshed_cleanly`) yet stops on any edit. Queued candidates test on the new referee.
  Loop code too: a `--once` driver taking the lock, or starting a merge turn beside it, on older code than the disk's
  execs itself onto it, keeping pid, lock (or queue place) and frozen policy (`driver.hand_off`, MISSES 100).

## Game machines

- Default to **`--workers auto`**: the referee keeps up to 0.7 × cores games (less other load) running on this Mac,
  starting each as a slot frees, plus games on the SSH hosts in `~/.bc-loop/remote.json` (sig's Mac minis `mm` and
  `mm1`, Linux laptop `ll`, two Windows PCs played in WSL2, `zz-wsl` (`shared`, `part_time`) and `sz-wsl`, how each is
  reached in `referee/WINDOWS.md`, and eight 2-core Azure Spot VMs `az-*` until 18 Oct; whole machines; each needs
  only SSH and `uv`, plus a C++20 compiler off Mac; the entry fields: `referee/remote.py`'s docstring; game
  processes, not model agents). A Mac host gets the same toolchain and compiled binaries, so it plays the identical
  game; a Linux or Windows host builds both bots itself with the same toolkit (a fair game, but possibly not move for
  move the Mac's), so same-moves checks stay on Apple machines.
- `~/.bc-loop/remote-status.json` shows the running test live (games done, games a minute, each host's games, last
  game and rests, and the games in flight here and on each host; rewritten at most every `run.STATUS_S`,
  `run.Scheduler.save`). The SPRT still reads pairs in map order, so decisions are unchanged. `--workers N` caps local
  games at N.

## Game retries

- An SSH, setup or build failure rests the host and replays the game on another, else here as slots allow
  (`run.Scheduler.replay_lost`). A game this Mac fails without a result (an OS error such as a refused fork, or its
  process killed: `run.infrastructure_failure`) is tried again after the pauses in `run.REPLAY_WAITS_S`, same map, side and
  seed, before it can fail a test, for every caller (SPRT, screens, tunes, fillers, coders' games); a bot's crash,
  timeout or bad output is scored as ever, and health.py alerts on a game lost on every try or on replays that recur
  (`game-retries.json`, `health.game_retries`, MISSES 43).
- A map the engine's loader refuses (`referee/mapcheck.py`) never counts: pool maps are checked as made
  (`driver.generate_maps`), the SPRT and screens play a spare (`referee/sprt.py`, `driver.loadable_maps`), a shared
  set's is remade in place, and an error it causes re-evaluates (Recovery; MISSES 81).

## Coders' measurement games

- Coders' own measurement games (`native.py games E#### ...`, sig 2026-09-25) use the current referee and the same
  hosts, at the coder's share (`native.host_share`), so a running evaluation keeps
  the rest; their sandbox reaches only those hosts' SSH ports (`driver.host_domains`), through its proxy; a host
  reached through another (`sz-wsl` through `ssh sz`) keeps its configured command, with the hop's SSH tunnelled and
  the hop's port allowed (`remote.proxy_route`). Whatever their options (and a coder's `play` of run.py, cpu.py or
  identical.py) they use the hosts too (`native.on_the_hosts`, sig 29 Sep).
- The quick check's 24 games (`loop/quick.py`, both providers) play the same way: the hosts and this Mac at the coder's
  share, their status in the coder's file (`quick.pair_games`), with no wait for this Mac before they start. Its CPU
  check plays here or on the Mac hosts, `cpu not checked` after `quick.CPU_WAIT_S` with nothing playing (the
  evaluation's CPU gate plays it); a coder's `games --sandbox` alike exits 75 (`native.batch_slots`). Native `quick` holds no evaluation lock or marker (`native.quick`), so
  it runs while an evaluation plays and nothing waits for it (MISSES 49).

## Engine cache and fork server

- Games load the wasm engine precompiled (`referee/engine_cache.py`, 2026-09-26):
  byte-identical games, ~18% less CPU a native game here; the cache is `<loop home>/engine-cache` (each Mac/Linux host
  keeps its own; Windows hosts compile). An unknown toolkit's engine.py, an unusable cache (a coder's sandbox) or a
  damaged copy plays the plain command and says why on stderr (health.engine_cache alerts). Native games also fork
  each team's bot processes from one copy of the bot started with a small preloaded shim (`referee/fork_server.py`,
  2026-09-27) instead of one per dragon; Mac hosts get this Mac's shim, Linux hosts build their own, Windows and
  sandbox games don't fork; each bot pair's first native game and one in `run.FORK_CHECK_EVERY` play both ways, and a
  differing replay whose plain game repeats there (else the bots are `nondeterministic`, `run.plain_differs`: a
  candidate `run.blamed` finds so is discarded) switches it off for every game (with an alert) until its `off` entries in
  `fork-server-checks.json` are removed (health.fork_server).
- Native judge: `referee/native_judge.py`; this Mac plays on the Mac hosts' gate (`run.native_gated`).

## Coding tasks

- A Claude coder whose session started no reviewer subagent is recorded `review: "skipped"` in its session, result log
  line and stats.md (`driver.coder_review`; a record, not a gate).
- A coder stopped before its result is judged (`driver.WIP_VERDICTS`) or whose candidate went stale leaves its
  `bot/` and `tests/bot/` work, committed or not, as one commit on its champion (or stack), branch `wip/E####` in the
  main repo, with a history line on the idea (`driver.keep_wip`, both providers; not a tune's: `driver.save_wip`).
  The idea's next clone gets each as `origin/wip/E####`, its prompt the newest `driver.WIP_SHOWN` with what each coder
  was given, to take only their change or just merge a handed-in one (`driver.wip_task`, `driver.MERGE_ONLY`). Bound
  and deletion once the idea is judged: `driver.prune_wip` (MISSES 47).
- `meta.py copy-data E#### <paths>` (`--files` for named files) copies study data a coder's sandbox can't read into
  its clone's gitignored `data/`, capped and never through a link (`meta.copy_data`).
- `loop/cutoffs.py` lists the hard cutoffs a change adds, before its review (PROMPT.md); each result's `cutoffs` and
  stats.md count them (MISSES 56). When a rule lands, `--sweep` lists what was queued or claimed before it.
- **A turn that ends while its own work runs** (MISSES 63): headless, nothing re-invokes a session when its work ends,
  Monitor and run_in_background notices included, so every launch carries `meta.FOREGROUND`: wait in one Bash call,
  polling a done file (a coder's sandbox refuses ps, pgrep and kill -0 across calls). A coder whose last message says
  it waits (`prune.still_waiting`) or whose clone holds uncommitted changes, unless it hands in
  no code on purpose (`prune.no_code_on_purpose`), is resumed once (`driver.continue_turn`), as is a waiting merge
  turn (`driver.run_resolve`); a coder still waiting is an `error`, retried with its work as `wip/E####`, a merge turn
  is stale or tested unchecked. `health.uncommitted_no_code` alerts once on a no-code result whose clone holds
  uncommitted bot/ or tests/ work and no `wip/E####`.
- A measure-first stop counts the rows the change acts on (its changed decisions and what followed, or its firing
  rate), else the coder asks its parent or builds, saying why in `Choices:`; a stopping coder puts `Changed: <k>
  decisions in <n> games` just before `Blocked by:` (PROMPT.md, GENERALISE.md; MISSES 61). The prompt lists the idea's
  and its merged ideas' lines worded as ending it on missing evidence, to check (`driver.stop_lines_task`). A
  mechanism's bar names the gap to close (the opponent's value, same games) and the share closed, with its interval;
  only an interval under the share needed stops or strengthens it (MISSES 107).

## Tuning ideas

- **Tuning ideas** (`"kind": "tune"`, `"tune": {"params": [...], "constraints": [...]}`) replace the coding task with
  SPSA over those `bot/params.hpp` numbers (`loop/tune.py`, via the shared `driver.run_tuner` that both providers
  call): a budget of about 10 iterations per value (at least 40), each on 12 fresh maps (`driver.TUNE_DEFAULTS`),
  spread over all game machines in rounds sized to the slots free at each round's start (`tune.batch_size`, at most
  `tune.MAX_ROUND` iterations a round), each perturbation weighted by its own iteration's gain, so the games and gain
  schedule are the classic SPSA's (`loop/tune.py`'s docstring has the measurement). A tune whose result left its
  values unjudged (blocked, or an error before any SPRT game) reuses them when its idea is claimed again, skipping the
  SPSA, while its tune spec, map mix, rotation, starting values and the untuned values its constraints name still
  match and its constraints hold on the reused values (`driver.earlier_tune`). Its nudges are scored against the same
  rotation as the SPRT (plus against minus, then each against the rotation's opponents, `driver.ROTATION`), so a value
  that only wins against itself doesn't win the tune. The tuned values are then an ordinary candidate under the idea's
  predeclared SPRT. Tune one behaviour's 3-8 numbers at a time; larger groups are noisier. Values keep their sign and
  stay within 4x of their start; `bool` switches are never tuned. An order the values must keep goes in the tune's
  `"constraints": ["beaconRound <= foldRound"]`, which every played and final value keeps. If the tuned values fail
  the bot tests, a coder gets one short turn (`loop/TUNETESTS.md`) to rewrite tests that pin a tuned number in terms
  of `P`, leaving any that show a real defect failing, since the SPRT and not the suite judges the values (E0076).
- **Generalisation check** (sig, 2026-09-28): after the SPSA, `driver.check_tune_split` asks of the tune's samples
  whether a value's best differs by map area or style (`loop/tunesplit.py`); a flagged value queues a formula idea.
  A tune's official-map report also goes to the alerts file.

## Fillers

- **Keep the game machines busy** (sig, 2026-09-25): when no candidate is waiting for or under evaluation (every
  in-flight idea is still being coded), the next claim prefers fillers at priority 3 or better: a tuning run (no
  coder, all games; it may take a place beside `driver.CODING` coders) and then a quick cherry-pick retest or quick
  fix (`driver.is_filler`). The orchestrator starts an extra `--once` when the evaluation queue is empty. Beside the
  claims, the upkeep tick keeps three game-only fillers running whenever no evaluation plays on the hosts
  (`sweep.busy`: a candidate waiting, the lock's stages on this Mac, a late screen or a tune don't pause them;
  each stops between variants, maps or stages when a host stage starts and waits it out in its own run,
  `sweep.wait_idle`, playing on within `sweep.WAIT_S` of its end, not at the next tick): the champion's baseline
  prewarm (`loop/baselines.py`), its variant sweep and its adversarial-map search (`loop/advmaps.py`). They split
  each host among those asking for slots (`native.filler_rank`), so one between its calls leaves its part to the
  others (MISSES 50). A tick keeps a paused filler's reason current (`sweep.repause`).

## Variant sweep

- **Variant sweep** (`loop/sweep.py`, its docstring says how; MISSES 16, 26 Sep): tunes keep a value's sign and stay
  within 4x, so a gate or switch that is simply wrong is never played. Once per champion the sweep plays up to
  `sweep.MAX_VARIANTS` variants of it (every `bool` flipped, every number at 0 and 4x or an explicit "never", one
  variant per decision group in `sweep.GROUPS`, since gates in series test flat one at a time, MISSES 21; the order
  and what is skipped: `sweep.py`) against the unchanged champion on fresh eval maps, both sides. A first-pass
  winner's second pass is 20 self-play games (farm variants on dead-end and near maps, M84) and `sweep.seat_maps`
  maps against `driver.ROTATION`'s seats, each seat an eval and a worldgen map, paired with the champion's (M82). At `sweep.QUEUE_AT` in
  self-play and level at the seats it is queued as a priority-3 idea (`source: sweep:<champion>`) unless an idea to
  do, in flight or tested on this champion names the param in its title: a hint for its SPRT (a swept number for a rule of what the bot observes, a
  switch flip as it is), its champion seat read
  apart (`judge.self_play_only`), not evidence. A variant that fails to build or play is skipped. A one-param variant
  whose first pass mirrors the champion on every map is `dead` in `sweep.json` for its direction (M59: a kept change
  can leave params deciding nothing, often one way only); stats.md lists them and a claim notes an idea moving its
  params only their dead ways (`sweep.dead_note`, never a block). A filler, started and stopped as the prewarm is and
  not by a tune still playing, it takes the idle fillers' share of each host (MISSES 35; `native.host_share`,
  `native.idle_games`, `native.IDLE_FILLERS`, `BC_YIELD=filler`), in calls sized to the slots (`sweep.batch_size`),
  each variant a copy of one export of the champion (`sweep.source_export`). Where
  games and progress go: the docstring; health.py alerts on repeated failures, a hung run, games that couldn't be
  played, `sweep.GROUPS` naming params the champion lacks, or a day-old champion with no sweep started although the
  machines were idle. `loop/sweep.py variants` lists a champion's variants and the skipped.

## Adversarial maps

- **Adversarial maps** (`loop/advmaps.py`, sig 2026-09-27; its docstring has why, each step and the budgets): once
  per champion a filler plays the champion against the starter, both sides of each map: first every map in the
  referee checkout's `maps/adversarial/` (its README says what each is and which champion it beat, and its Opponent
  column which bot plays it, the starter unless named, `advmaps.regression_opponents`; a loss or draw, both sides
  against another opponent (`advmaps.lost`), alerts `ADVMAPS REGRESSION` unless the newest earlier champion that
  played the map lost it too, `advmaps.earlier_record`; stats.md lists the record), then a seed pool from every
  `mapgen/gen.py` style, community-map variants and gen's knobs at extremes (`advmaps.seed_sources`), then a
  hill-climb mutating the worst maps (`advmaps.MUTATIONS`; every map mirrored and `advmaps.valid`). A loss or draw is a
  find only when it repeats on `advmaps.REPRODUCE` of `advmaps.PERTURBATIONS` fresh perturbations; finds go to `<loop
  home>/advmaps/<champion>/finds/`, with one `ADVMAPS FIND` alert only at p <= `chance.TRIGGER_P` over the maps and
  opponents searched (`advmaps.find_chance`); a map committed to `maps/adversarial/` later plays at the next pass
  (`advmaps.new_regressions`). Past `advmaps.GAMES_CAP` games it goes on, up to `advmaps.IDLE_CAP`, only on idle
  machines (`advmaps.wanted`) at the spare share (`native.spare_share`), `advmaps.IDLE_BATCH_MAPS` maps a call, with
  a strong pass against each
  `driver.ROTATION` opponent judged by its own loss rate (`advmaps.strong_pass`). Nothing is queued:
  the orchestrator judges each find and commits the ones it keeps to `maps/adversarial/` with a README line. It starts
  and stops as the sweep does; progress in `advmaps.json`, games in `<loop home>/advmaps/<champion>/results.jsonl`;
  health.py alerts on repeated failures, a hung run, unplayable maps, or a day-old champion with no search started
  although the machines were idle. `loop/advmaps.py check` prints the search.

## Screen ports

- **Screen ports** (`loop/screenport.py`, whose docstring has the rules in full, method note M24): a screen entry
  built on a champion 2+ promotions older that still scores 0.45+ against the current champion over 100+ games (on its
  shared map set, else its latest screen of the champion, whichever has more; `screenport.set_scores`) found values
  worth having, as `hunter` did. It gets one priority-2 idea porting the mechanism behind its param diff from its base
  champion onto the current champion, each part behind its own switch (a flipped switch as it is, a moved number a
  hint for a rule of what the bot observes) (`source: screen-port:<entry>:<champion>`), less the values the variant sweep played alone and
  scored at least the entry's score with (`screenport.drop_swept`, M37); nothing is queued when none is left. It waits
  while a port of the entry, an idea naming it, or an idea at `screenport.WAIT_PRIORITY` or better setting one of its
  values is queued or in flight (`screenport.waits_on`), and a tried port is queued again only when the entry scores
  higher. The upkeep tick runs it when a result, a late screen, a prewarm stage or the champion is new; health.py
  alerts on a failed or stalled run (`screenport.json`); stats.md lists every entry's latest score.
  `.venv/bin/python loop/screenport.py check` prints what it decides today. Code-switch entries: `screenport.code_port`.

## Speed and identical ideas

- **Speed ideas** (`"kind": "speed"`, sig 2026-09-24) make the bot use less CPU while playing exactly the same moves:
  a faster bot buys more test games (E0085) and judge CPU margin. An
  SPRT can't tell identical play from no gain, so the referee (`referee/identical.py`) plays candidate and champion
  against themselves on 50 fresh maps and requires every game identical move for move (bot names, logs and CPU
  readings aside), plus sandbox CPU p50 at least 5% lower and p99 no higher; the usual tests and CPU gate still apply.
  A behaviour change fails it and belongs in an ordinary idea. Both bots are built with `sonarBuildKey` off (one
  fixed key) unless their `sonarkey.hpp` differ (the key hashes the source, E0116). So a pass shows identical
  decisions, move for move on its maps, not identical games: a new key can change games against sonar senders
  (E0183), and the starter's games differ between Mac and Linux (std::shuffle).
- **Identical ideas** (`"kind": "identical"`, sig 2026-09-26) are for changes that must leave every normal game's
  decisions unchanged (e.g. stricter checks on received sonar that only matter under attack): the speed ideas' test
  without the p50 gain (p99 no higher), then the opponent screen, since what they accept from others can differ. No
  SPRT or official-map report. A change to what received sonar is accepted can't pass it: the test teams' tags
  collide now and then, and the change alters which collisions get through (E0087), so such changes are `noloss`.
  One building the champion's own code but for the build's sonar key is kept with no games (`driver.same_code`).

## No-loss ideas

- **No-loss ideas** (`"kind": "noloss"`, sig 2026-09-26) are for changes whose effect on real games is none by design
  but which change local test games (e.g. ending the artefact where both test teams read each other's sonar); the idea
  says why real games are unaffected. The rotating SPRT runs as a non-inferiority test, `elo0=-15`, `elo1=0` (power, and
  an idea may only narrow elo0: above `meta.NOLOSS_ELO0`): H1 keeps it whatever the score, H0 or the pair cap discards
  it, unless kept on the chance of being no worse than elo0 (`meta.noloss_probability`). The opponent screen, CPU,
  test and crash gates apply as usual.

## Formula ideas

- `"kind": "formula"` (sig 2026-09-28): a tuned constant or cutoff replaced like for like by a formula over what the
  bot observes, on the noloss test (our maps fitted the constant). Each step: above `driver.FORMULA_TASK`. A cutoff
  added with no `Constants:` reason of `driver.JUSTIFIED`'s kinds gets a Claude coder one turn at hand-in to make it
  a formula (`driver.constants_turn`; not a retest), and once kept a formula follow-up (MISSES 96).

## Targeted ideas

- `"kind": "targeted"` with `"target": {"measure": <name>, "direction": "down"|"up"}` (sig 2026-09-28, after E0207's
  closed-pocket gate ended -0.1 Elo over 1,590 pairs): a fix for a rare way we lose, which can't show a gain on
  general maps. Only the idea sets both (a review, post-mortem, follow-on, or the orchestrator's `driver.mark_idea`);
  they freeze at claim. Kept when (a) the SPRT shows no loss on the noloss bounds (H1, or their probability rule),
  (b) its target batch shows the measure moving the declared way at one-sided sign-test p <= `meta.TARGET_P` and (c)
  every other gate passes; or, predeclared from 2026-09-28 (the policy's `target_gain`), (a) and (c) with the same
  SPRT read as a gain by the probability rule at `driver.TARGET_GAIN`, above the ordinary threshold because it reads
  a non-inferiority SPRT that may have stopped early (`driver.target_verdict`: "kept as a gain"; E0245, MISSES 73).
  (a) with neither is promising on a positive estimate, else discarded; (b) never rescues a failed (a), and a failed (a) plays no batch. The SPRT and the prewarm
  measure nothing. The batch (`driver.target_batch`), after (a): fresh maps the measure reads (`measures.maps_read`),
  the candidate and the champion each against the rotation's opponents in both seats (never each other), on the remote
  hosts; our side of each replay is read in its own interpreter as its games come in (`measures.read_one`; an unreadable
  one is left out), then deleted, keeping `target-units.jsonl`. The screen plays beside the last reads, which wait off
  the hosts (`driver.off_hosts`, MISSES 50), and counts only for a keep. Its stage is `target batch` in the
  utilisation rows. A unit is (map, seat, opponent); fewer differing units than `driver.target_needed` is a target
  fail the result states. Size and power: `driver.TARGET_UNITS`. A batch that finds too few maps or reads under
  `meta.TARGET_READ_SHARE` of its replays is an error (the idea re-queues); `health.target_batches` alerts on replays
  a killed run left. Measures: `measures.MEASURES`. Each step: above `driver.TARGETED_TASK`. Its coder measures the
  share of the change's firings that land in what the measure counts and reports it, and abandons, rather than spend
  an SPRT, only when the change can't move the count and its games show no gain elsewhere (MISSES 68, 73).

## Supervised mode details

The continuous command (CLAUDE.md, Claude driver) runs standalone; a supervising main runs `--once`.

- `--once` runs one complete experiment and returns. It takes the first free lane (1 to `driver.IN_FLIGHT`), so the
  main Claude keeps `driver.CODING` children coding: it starts the next `--once` whenever a child reports coding done
  or a result arrives (evaluations queue behind the lock), and reads each result when its Bash task completes. A claim
  fails while `driver.IN_FLIGHT` are in flight, or while `driver.CODING` are being coded unless it can take a tune
  (`driver.free_lane`). While fewer are in flight for 5 min with a claim open, `health.lanes_short` writes a
  `LANES SHORT:` alert (`health.quick`): refill a lane. An idea the
  orchestrator marks `"next": true` is claimed before every other ready idea and the fillers (sig, 2026-09-27), the
  rest in `driver.claim_order` (M70); only the orchestrator and a winning screen port set it, as
  `driver.mark_idea`'s comment says. There is **no coding time limit**.
- `driver.py --ranking I###,... --rule "<sig's words, date>"` records sig's queue order (`meta.RANKING`); evalline
  marks a ready idea claimed ahead of it with no orchestrator note since (`meta.out_of_rank`). `--out-of-rank I###
  --rule "<why>"` places one there with that note and a decisions.md line (`driver.place_out_of_rank`;
  `health.ranking`).
- A supervised `--once` started detached (stdout to /dev/null, nohup, no `claude` ancestor) refuses, alerting
  `LANE REFUSED:` (`driver.detached_start`; an unreadable process table allows it).
- Each child has a unique `bc-E####-<suffix>` session name and UUID; the driver logs its name at once and keeps its
  PID, `driver_pid`, parent, status and exit result in `~/.bc-loop/runs/E####/worker.json`. The child announces
  itself to the named parent with `SendMessage`, reports blockers and sends a final coding summary; the main, which
  learns of it through its `--once` Bash completion and the alerts watcher (not idle notices), may send questions or
  scoped steering with `SendMessage`, answered between tool calls.
- Worker settings enable incoming messages on the child only, not access control: it is told to act only on its
  named parent's steering. Cross-session messaging needs a current Claude Code; parent settings or managed policies
  may still hold or refuse messages.
- Starting the next `--once` beside an evaluation is intended. `--once` exits nonzero on provider/setup errors (quota
  limits too), so its Bash completion reports even a child that failed before announcing itself; an empty backlog
  returns without a child.
- `driver.py --first E#### --rule "<why>"` moves a waiting candidate to the queue's front with a decisions.md line
  (`driver.move_first`): it writes FIRST and renames the ticket (`driver.jump_queue`) if its waiter re-reads it each
  poll (`driver.own_ticket`), else a driver since 519011bb re-enters first at its next poll; one evaluating, recorded
  or ticketless is refused.
- A stop marker blocks evaluation and new claims after coding stops; clones and unfinished files are kept.
- The alerts monitor the usage-limit rule restarts (CLAUDE.md; MISSES 30) is the background shell waiting on
  `host-alerts.log`.

Message behaviour: https://code.claude.com/docs/en/cross-session-messaging. Without `--once`/`--parent` the continuous driver notifies no main session.

## Usage levels

The 5-hour model usage turns the loop down and back up by itself (sig, 30 Sep 02:03-02:30; off until a time: quota.OFF). `tools/usage_statusline.py`
(the status line of every Claude session in the repo) alerts `USAGE ...` once per level per window and keeps the
window's level in `<loop home>/usage-level.json`, which `loop/quota.py` reads; a marker past its `resets_at` reads as
none, so the loop turns back on at the reset even with no status line running. Each level holds what the lower ones do:
- **ahead** (past `AHEAD_FLOOR` and `AHEAD` points above the window's elapsed share): the upkeep's optional workers
  (`quota.HELD`: backlog and replay audits, Discord digest, riser study, map ideas) start nothing; one already running
  is still collected, and their lateness checks are quiet. Coding lanes stay full. Like every level it holds to the
  window's reset, even if usage falls back behind pace.
- **85%**: the REFLECT list and meta reviews wait too (a REFLECT still fires; its list covers the longer window).
  The watcher (`tools/orch/watch.sh`) asks the orchestrator for handoff notes.
- **95%**, the full pause (from 98%; why: `loop/quota.py`'s docstring):
  `--once` waits before claiming (`driver.wait_window`); each running coding turn is stopped and its lane waits,
  worker.json `paused`, then resumes the same session at the reset (`driver.pausable`, `unpause`; its clone and
  transcript lose nothing; a stop marker meanwhile records it blocked and `keep_wip` saves `wip/E####`); new
  post-mortems and follow-ons are marked held (`driver.turn_held`) and stay owed; reminders wait; LANES SHORT,
  evaluator-idle and owed-turn alerts are quiet. Workers already running (a post-mortem, a review) finish. The
  evaluation queue, SPRTs, screens, judges, promotions, results and merge turns go on. The orchestrator pauses its own
  builders and fix rounds.
- **New window**: the status line removes the marker; paused coders and waiting claims go on within
  `driver.LIMIT_POLL_S`, held turns at the next upkeep tick (`maintenance.owed_turns`, one at a time, with no grace),
  reminders at the minute check. `health.usage_level` checks every firing (marker against `usage-share.json`, a marker past its
  reset, a coder the pause missed or left paused, a record over 30 min old while a coder runs); evalline's agents line names the level and the paused coders.
- **Wakes**: a window's first level, and any provider limit a turn waits out past `driver.WAKE_AFTER_S`, add one
  `USAGE` reminder due `quota.RESET_MARGIN_S` after the reset (`health.wake`, one per reset), which the minute health
  check alerts, so an unattended orchestrator is woken at the new window.
- **One gate**: every headless launch names its kind to `quota.gate` (`meta.worker_command`'s `usage`, the driver's
  `usage_gate`); `quota.NEVER` (judges, merge turns) never waits, a kind in neither it nor `quota.HELD` raises, and
  `test_launches` fails a launcher that skips the gate. A level crossed between a caller's check and its launch reads
  as a quota stop (`quota.Held`): retried after the reset, never counted as a failed launch.
- The level is only as fresh as the last status line drawn: a session that draws none (headless coders) never
  updates it, so it moves only while an interactive session runs.

## Native session details

A native lane's review is a self-review, marked `Review: self` in its final message (it can't start a fresh-context
subagent); `loop/ASTRA.md` is the lane brief.

Lanes start from the command in `loop/ASTRA.md`'s header (`codex exec --ignore-user-config`), its prompt the part
after its `---` line. For each lane, repeat claim → implement → quick → commit → evaluate (`claim` gives the
experiment id and clone; overlap and stop markers as CLAUDE.md says). This is session orchestration, not an
unattended Codex CLI daemon. Abandoning a tune records an error, not a discard, so its idea goes back to the queue
once (a second error retires it) and its next claim reuses the values a finished SPSA left (`driver.earlier_tune`);
abandon refuses a tune still playing its SPSA. `claim` returns `error` when `driver.new_experiment` refuses an idea's
settings (it sets that idea to error, with an alert).

`evaluate` can instead return `resolve_required` when the champion moved since the candidate's base: resolve the
conflict or check the clean merge as the returned prompt says, then run its `evaluate` again. Nothing is
recorded until then.

`evaluate` returns a `judge` task first when one is owed (Judged verdicts): write `verdict.json` in its
`judge_folder`, then run its `then` (`native.py judged E####`), which returns the post-mortem or follow-on. The judge
is the session that coded the change, so it isn't independent; the trusted side's check is the same.

`claim` can instead return `review_required`. Do the review in the returned `PROMPT.md` with the active model,
editing only the scratch `ideas.jsonl`, `playbook.md` and `method.md`, then run the returned `native.py review <folder>`
command. Fix any rejected output, then claim again. This never invokes Claude when Astra is orchestrating.
`audit_required` works the same way, ending with the returned `native.py audited <folder>`.

## Reviews

Both providers prepare and validate the same review. The Claude driver keeps claiming while its review worker runs
in the background; the review merges with outcomes written meanwhile. A review is due `meta.META_EVERY_S` after the
last finished, or an hour after when under three todo ideas remain (`meta.prepare_review`). Pending reviews survive
restarts. They inspect champion code, research, docs, experiment evidence, our exact uploaded version's replays and
version-labeled public-opponent observations, and turn promising corrections and combinations into new, predeclared
experiments. A review may merge a waiting idea that makes the
same change as another into it (`"status": "merged"`, `"merged_into"`); the kept idea's coder gets both texts. Ideas
that change different things stay separate. A new idea written `same change as` a followup (never claimed) merges into
its waiting retest, else stays its own todo idea (`meta.merge_twin`, `meta.open_retests`); a
post-mortem's or follow-on's same-change row with no Options is noted on that idea (`driver.attach_same_change`). A
review may set a todo idea's `rank` but never lowers a priority-1 idea (sig's rule; `meta.apply_review`, M70).
Failed/rejected Claude reviews, like the upkeep's workers, wait an hour before another model launch, or until the reset
a quota error names if sooner (`meta.when_due`); their scratch edits are retained. A review whose launch fails twice (a
quota error doesn't count, `meta.provider_limited`) is abandoned (`abandoned` in `review.json`, with the reasons) and
the next call prepares a fresh one, so no worker can hold reviews up; health.py alerts on a review pending over 4 hours.

## Recovery

New work is saved in `runs/E####/experiment.json`, its coder's state in `worker.json` as the Claude driver's is
(`native.worker_state`: `running` while coding, `finished` from `evaluate`, or from a tune's `claim`, which waits for
its session's `evaluate` for `meta.TUNED_WAIT_S` before it counts as stranded). If a native session disappears
mid-task, inspect that record and clone, then `uv run loop/native.py resume E####` after confirming no other writer
owns it; a tune has no coding to resume (resume refuses it), so its candidate goes to `evaluate` or `abandon`. A main
Claude session may recover either provider's saved work this way, doing the returned task with the shared quick and
evaluate commands; a running Claude child blocks takeover. An idea still trying after its current try's result, with
no live process, is freed by the next claim (`driver.finish_recorded`; `health.dead_lanes`). Old records
without a protected-referee baseline can't be evaluated as if it were known. Both paths recover
interrupted accepted promotions from saved acceptance proof before another claim. A champion checkout that differs
from its branch is restored only when clean, without force; evaluation refuses a mismatched one.

A recorded result is final, except under a changed verdict rule (sig 2026-09-28): `uv run loop/driver.py --rejudge
E#### --rule "<the changed rule>" [--dry-run]` (`driver.rejudge`, MISSES 73) re-judges its saved SPRT on the champion
it played, plays only the gates its old verdict skipped, under the lock at the queue's head, and records a later row
for its id with the rule in its "rejudged", which readers take over the old (`meta.latest_rows`); a keep promotes as
any does and drops its todo retests (one being built continues). A judge's keep applies through it (Judged verdicts).

An error its candidate didn't cause re-evaluates it unchanged (`driver.reevaluate`: automatic for a refused map, a
native judge difference, a map maker's error or a loop code fault after coding (`driver.loop_fault`),
`prune.input_fault`, else `driver.py --reevaluate E#### --rule "<why>"`; capped along its chain,
`driver.REEVALUATE_CAP`). Loop processes import their start's code (`home.pin_code`, MISSES 88). A handed-in
candidate still waiting for the lock whose own numbers missed its bar (E0395) is withdrawn with `driver.py --withdraw
E#### --rule "<why>"`: its driver leaves the queue and records it discarded, with its post-mortem, and nothing plays.

## Upkeep

- While either loop works, scripts check official docs and the stable `unswbc` release hourly. Native claim
  automatically starts a small upkeep process during coding or review, including between commands. It exits when that
  experiment/review is recorded or either stop marker appears; it has no deadline. A resumed task restarts it. Its
  PID/status and log sit beside the experiment or review (`upkeep.json`, `upkeep.log`). These checks use no model
  tokens; the upkeep's only model calls are its background workers (map ideas, from new map-channel messages or a map
  the ladder pool gains (sig, 2026-09-27: each teaches a lesson, which the worker names, measures against our eval
  styles and, when they lack it, builds as a style on random layouts); the Discord digest; the REFLECT list; the riser
  study, every `risers.CHECK_EVERY`, each team at most daily; the daily backlog audit), none of which runs under a
  native session: it gets the digest's and REFLECT's by-hand lines instead, and one line saying map ideas are off.
  A usage level holds them and the owed coder turns (see Usage levels).
  The docs mirror: `maintenance.fetch_updates`; `~/.bc-loop/maintenance.json`: `maintenance.check`.
- One process at a time owns the tick (`maintenance.upkeep_owner`'s lock; native processes hold their own) and hands
  it to a stand-in on current code at `home.WORKER_NICE` (`maintenance.hand_over`, MISSES 76, 91), which logs to `upkeep-stand-in.log` and exits once its parent's
  pipe closes (hard, `STAND_IN_GRACE` later, if a tick hangs); `health.upkeep_ticks` alerts on a stalled or hung tick
  per lock. The minute checks (`health.QUICK`) run beside it in every process, never held by an hourly step.
- This upkeep does not start itself when no loop is running, and never commits, pushes or merges changes. An
  unfinished native task keeps its watcher until it ends or a stop marker is set. Hourly public scouting:
  `maintenance.scout`.

## Toolkit upgrades and engine versions

- An upgrade stages an isolated versioned environment, runs the checks CLAUDE.md names in it, then updates the
  project dependency/lockfile and standalone CLI. A toolkit not in `record.KNOWN_INTERNALS` (or a failed probe) gets
  one alert line per version, not a failure.
- Every new experiment records its engine version, runtime path and toolchain epoch, which its quick checks and
  referee games use; old runtimes stay available. Results without an engine label are unknown-version evidence.
- A release whose unswbc package differs from the active one only in `maintenance.NOT_ENGINE` files and the version
  string, with the same declared requirements (1.2.1, 2026-09-28: only the VS Code viewer), plays identical games, so
  it is never applied: the hourly check records it in `engine_compared` (a failed comparison in `compare_error`, the
  version then waiting as before), and no claim waits for an idle boundary for it (`maintenance.same_engine`).

## Publishing

- The upkeep packages the pinned `loop/champion` commit (committed source only) and checks the downloaded active
  archive against it; identical active source needs no upload. Each upload reserves one of the site's
  `ladder.MAX_UPLOADS` slots a rolling hour before sending (who waits: the `publishing` docstring). Unknown outcomes
  are reconciled by a unique saved name; explicit rejections retry after an hour, keeping the spent slot. Each
  champion after the baseline needs its proof (`publishing.accepted`). Builds are checked
  without waiting inside a coding task. A build may auto-activate on the server, else the loop activates a ready one;
  it verifies archive and activation, and restores the previous verified archive on a source mismatch. Build/source
  failures block publishing and matches; resolve the cause in `publishing.json` first. No script commits or pushes.
- **Hold** (sig's tournament pick): `publishing.py hold v00NN --until <ISO time+offset> --why ...` writes
  `PUBLISH_HOLD`, `release` removes it. Meanwhile the upkeep uploads nothing and keeps that version live, re-activating
  its saved submission once its archive matches, even while blocked (it alerts so); champions still promote locally.
  Past `until` the marker is ignored: use the offset in force then (Sydney: +11:00 from 4 Oct). `evalline` shows
  the hold; health alerts while another bot is live.
- **Spoof hold** (sig, 1 Oct): a champion defaulting `bool spoof` true waits for `spoof-release.json`
  (`publishing.spoof_hold`).

## Online map sweeps and requests

- Manual online matches are **unranked map sweeps only** (sig, 2026-09-24): one game on every active map (IDs from
  the server) against one opponent, by how much its games inform (`online.sweep_weight`), least-covered maps first on a
  short budget (`online.choose_match`). Ranked games come from the autoscrim draw, which already sets our rating;
  ranked requests only if sig asks. Until it expires, `<loop home>/online-targets.json`'s teams are swept first, in
  order, once per version (`online.sweep_targets`). The rotation sweeps non-dev teams only, replaying
  the previous version's opponents (share `online.SWEEP_PAIR_WEIGHT`+) on the maps first played: the same opponent version, map, start and pool period
  pair a before/after sample (`compare` in `online.json`; no exact replay since per-match pearl seeds, 2026-09-25).
  The start is where we started (`online.start_key`, from each saved replay): from `online.LAYOUT_CUTOFF` the site
  swaps start tiles in some games, so a side names none (MISSES 72); a later game with no replay read is `unpairable`,
  and a new map layout alerts once (`online.layout_news`). **The site stopped naming battles' submissions**
  (`online.API_GONE`, MISSES 77): ours comes from the replay (`online.replay_submission`) or our request row
  (`online.collect_games`, `submission_from`; a game whose replay isn't ready waits without an attempt), the
  opponent's is None; `compare` pairs such games when no rating read splits them (`online.unsplit`, M73;
  `health.online_compare`), `online.version_news` never. Inferred pairs hold replays but never take
  `online.version_alert` past a confirming sample, nor re-checks (`online.named_view`).
- Non-dev battles share a cap of **`online.GAMES_AN_HOUR` loop-requested games per rolling hour**, at most one request
  per `online.REQUEST_EVERY`; dev battles are marked `dev` in match-requests.json and count only in their own
  allowance (Dev battles, below; its rows go after `online.REQUEST_ROWS_DAYS`). Count one game per selected map.
  Reserve budget before POST; ambiguous/failed requests spend it for an hour. An unclear outcome counts as played;
  one the server refused (an HTTP error but 409 or 429: nothing queued) is asked again after its hour. The server's
  own limits count matches started elsewhere too.
- `~/.bc-loop/online.json` holds current evidence and per-map counts; `online-replays/<submission>/` keeps each
  replay; a same-code build's games are read with its parent's (`online.play_group`). A version change invalidates
  the report. Bad replays are logged apart, retried hourly up to three times; one stops no other work.
  A battle-detail field the cycle reads going missing alerts once (`online.api_news`); a battle with unrecorded games
  is read by id after it leaves the list for up to `online.BEHIND_S`, then alerted (`online.owed_battles`), and
  `health.online_recording` alerts past `health.UNRECORDED_S` (MISSES 77, 83).
- The ladder map pool (CLAUDE.md, Decisions) is read hourly into `<loop home>/ladder-maps.json`
  (`loop/ladder_maps.py`), and health.py alerts `LADDER MAPS CHANGED` when it differs from `maps/ladder/` and
  `maps/sprint/` by content; that list only alerts, and the official-map report and the CPU gate play the referee
  checkout's files. Retired
  maps (`maps/retired/`) still seed near and sized test maps and are played by the CPU gate and the identical check
  (Big Empty is our largest map), not by the official-map report.
- **Pearl timings withheld from 2026-09-27** (the announcement: `online.POOL_CUTOFF`'s comment): replays may lose
  their map text's pearl gaps, a map may appear as variants, and its pearls may change under its id. So every
  before/after pairing and per-map tally in online.py (compare, and through it `version_alert`, `margin_fell`,
  `raise_rechecks`; `per_map_stats`, `map_totals`, the dev `map_compare`) also keys on `online.pool_period`, each game's
  `requested_at` against `online.POOL_CUTOFF` (moved only by hand: check it against a `pearls changed` or `withheld`
  LADDER MAPS alert's change). A game requested within `online.POOL_STRADDLE` before the cutoff may have played after it: it pairs with nothing
  (`online.pairable`), and `choose_match` replays only the previous version's games of the current period.
  `online.map_ids_by_name` leaves out a name two maps share (such a game's map id comes from its record).
  `ladder_maps.differences` sorts each change (its comment: never commit gapless text or overwrite `maps/sprint/`); the
  map-ideas worker skips a layout already studied unless its pearls changed, and gapless text
  (`mapideas.new_ladder_maps`).

## Dev battles

- **Dev battles play while a submission is live, paced, only while they inform** (sig, 2026-09-27, his words at
  `online.DEV_GAMES_AN_HOUR`: every dev battle is public, so each dev game is another public replay of our live bot).
  The organisers' dev teams (`"dev": true` on `/leaderboard`, `online.dev_opponents`) have their own 60 games an hour
  on the site, of which the loop requests at most `online.DEV_GAMES_AN_HOUR` a rolling hour, one battle each
  `online.dev_pace` (`online.dev_due`): a team due a probe first, else the one whose games lag its measured
  usefulness (`online.dev_weight`). Every result is kept and every dev loss's and draw's replay stays; every reader
  keeps dev games apart (`online.against_dev`), a rate counting every game (`online.all_games`, MISSES 55); the dev
  benchmark is a report, never a gate. How it picks, paces, polls, keeps replays, reports and alerts: the comment
  above `online.DEV_GAMES_AN_HOUR`.
- **Every dev game has us as side A** (review M48; `POST /battles` can't ask for the other side, docs/unsw/api.md; the
  count and E0167's check: `online.local_sides`' comment), so a dev map row mixes the map with the side. online.md's
  dev section gives each record's games by our side (`online.side_counts`), and a compare whose games are all one side
  adds to each map row our uploaded commit's local score on that map as that side (`online.side_rows`, from the newest
  official-map report it played in, `online.local_sides`).

## Discord reads and digests

`maintenance.DISCORD_PAUSED` in the loop home pauses both the read and the digest until deleted (sig, 2026-09-30); health stays quiet while it exists.

- **Read the Discord** (sig, 2026-09-26). Once a day (`maintenance.DISCORD_EVERY`, a failed read hourly) the upkeep
  reads from half an hour before the last good read's start (`maintenance.discord_hours`) the channels in
  `discord_read.CHANNELS` and the threads active then in its `FORUMS` (ask-devs is where the organisers answer) through
  gstack's browser, signed in once by sig: the main checkout's daemon (`<repo>/.gstack/browse.json`), which every call
  names with `BROWSE_STATE_FILE`, since browse otherwise picks a daemon by the working directory's git root and a
  worktree gets a new, signed-out one (MISSES 51; health.py alerts `browser:stray` on a live one in a worktree or
  experiment clone, and `discord:browser-restarted` when the main checkout's server isn't the one the last signed-in
  read used), and with `BROWSE_NO_AUTOSTART=1`, so a closed window is reported, never replaced by a new signed-out
  browser (`tools/discord_read.py`, read-only; it clicks only authors' names, to read their roles from the profile
  popout, and the member-list toggle), into `discord/latest.md` (replaced at each read; `messages.jsonl` keeps
  everything; a `--all` backfill or a `--channel` read writes `discord/history/` instead, leaving latest.md alone).
  Claims there can be out of date: the rules, toolkit and docs changed during the contest, hence checking a claim's date
  against later changelog posts and docs/unsw/. When the browser is signed out, isn't running or isn't answering, reads
  fail (exit 2) and health.py alerts past `health.UPKEEP_JOBS`' limit without a good read (which of them in its last
  line); sig signs in again (`browse handoff`) or reopens his window. After each read with new messages the upkeep's
  digest worker (`loop/digest.py`: confined, read-only, `--effort low`) digests `latest.md` with `loop/DISCORD.md` (a
  starting watch list, not exhaustive; every relevant message quoted verbatim; items numbered D1, D2, ...) into
  `discord/digest-<read time>.md` and alerts `DISCORD DIGEST ready`; when it can't run (two failed launches, each capped
  at `digest.TIMEOUT`, or a native session orchestrating), the orchestrator has a fresh read-only pass do it (the
  `discord-digest` subagent, another read-only subagent, or itself) and saves that report under the same name (a
  subagent can't write; a worker finishing later saves its own beside it as `-2`, `digest.free`). The orchestrator
  decides itself what becomes an idea, a loop change or news for sig (sig, 2026-09-26). It checks the digest's claims
  against docs/unsw/ (site pages included), study.md, the champion's code and the backlog, acts, and appends the report
  and its decisions, dated, one per D-id, to `discord/digests.md` (a digest without decisions is unfinished; MISSES 14;
  the form is under Report items and decisions; bug reports as `loop/DISCORD.md` says); reviews get those digests plus
  the latest read as `discord.md`, and health.py alerts when a read with new messages has no decisions after it, the
  digest worker is pending over 2 h or abandoned, or an id is undecided.

## Report items and decisions

- **Every item a report hands the orchestrator gets a decision, and a check counts them** (MISSES 52, sig 2026-09-27).
  A report the orchestrator judges numbers its items with its kind's letter: D for Discord digests
  (`loop/DISCORD.md`), A for replay audits' findings.md (`loop/AUDIT.md`), F for REFLECT lists (`loop/REFLECT.md`), G
  for map-ideas reports (`loop/MAPIDEAS.md`), R for riser studies (`loop/RISERS.md`), B for backlog audits
  (`loop/BACKLOG.md`), S for the orchestrator's read-only subagents' reports (checks, measurements, source reads, studies),
  which it has number their findings and saves as `<loop home>/reports/<yyyy-mm-dd-hhmm>-<name>.md` when they
  return. The decisions go in the kind's file (`discord/digests.md` for D, `<loop home>/decisions.md` for the rest)
  under a heading naming the report, one bullet per id with a verdict: `acted:` what, `declined:` why, or `check
  started:` which. A hand-off to a later step ("left to the audit", "the next review", "noted") is not a decision, and
  a checkable claim's default is a measure-first idea queued now (`meta.append_ideas`, its stop line saying how it
  rules out luck); a decline as "hypotheses only" cites the idea covering it or why it isn't worth a measurement.
  health.py alerts `undecided:<report>` 2 h after a numbered report appears while any id lacks an acceptable decision,
  and on a digest or audit written since `decisions.NUMBERED_SINCE` (set at the merge) with no numbered items;
  `numbering:<folder>` alerts while none of a kind's reports since then (digests, audits, REFLECT lists, applied/
  files) carries ids, the prompts' first firing. A range, `- D4-D6 declined: ...`, decides each id in it. The form,
  the kinds and the size bounds are in `loop/decisions.py`'s docstring. What the loop applies itself needs a decision
  too: each idea a review, audit, post-mortem, follow-on, re-check, late screen, screen port, variant sweep or riser
  study queues, each existing idea a review or backlog audit edits, merges or drops, and each method note a review
  adds or changes is listed by its own id (I411, M48) in `<loop home>/applied/<time>-<source>.md`
  (`decisions.applied`, called by `meta.append_ideas`, `meta.apply_review` and `backlog.apply`; a listing that fails
  alerts and leaves the ideas and the review recorded once), decided `keep`, `edited`, `merged` or `dropped` (or
  acted/declined) the same way.

## Replay audits

- **Watch our games, not just count them.** Every `audit.AUDIT_EVERY_S` a replay audit (`loop/audit.py`, `loop/AUDIT.md`) has a
  model watch our current submission's online games (losses first, then `audit.DEV_LOSSES` dev losses and draws,
  `audit.dev_picks`: half to teams we beat heavily, a 95% lower bound of at least `online.DEV_HEAVY` over the games
  that set the team's share (`online.dev_report`), labelled with our score against them, a flaw that score hides, then
  the dev benchmark's, each kind filling from the other; sig, 2026-09-27), the
  promoted candidate's lost test games and the leaders' games, like a coach: anything pointless, wasteful or weak, not
  a fixed checklist, `audit.blind_picks` watched blind first. It uses `tools/watch.py` (`scan` finds moments, `--tally` counts them, `dragon` and `frames` show
  them, `doom` says when and why each death became unavoidable, `why` replays a game with our debug line on:
  tools/why.py), must measure how common a problem is before reporting it (a death finding: `doom`'s table and 3+
  cases drawn at since-1, since and the death round, never an end reason or place count, MISSES 64), and queues up
  to 5 validated ideas; findings go to `audits/<id>/findings.md` (latest in
  `audits/latest.md`, read by reviews). One shortfall seen on several maps is one finding and one
  conditional idea per mechanism; a rule choosing between mechanisms is a stack
  tested after them (sig, 2026-09-27, MISSES 41): every idea-writing prompt carries `loop/GENERALISE.md`
  (`meta.IDEA_PROMPTS`), and stats.md's "Split findings" (`meta.sibling_clusters`) lists waiting ideas that look like
  one finding split up and don't name each other, for reviews to merge (true duplicates) or tell apart. The Claude
  driver runs it in the background from its claim step; native `claim` returns `audit_required`, then `native.py
  audited <folder>`. Each SPRT keeps up to 40 of the candidate's lost games in `runs/E####/losses/`, and its
  post-mortem or follow-on watches them (every other replay goes once scored: `sprt.drop_replays`, and
  `run.stream_games` with no replay folder); the upkeep packs them, and old versions' dev-win replays, losslessly
  once nothing reads them unpacked (`loop/prune.py`), as it packs finished clones' replay folders (`loop/pack.py`).
  Like every headless worker (reviews, audits, map ideas), its prompt and output live out of its reach (`<loop
  home>/worker-io/<kind>/<folder>/`; reviews' in `rule-games/`), and the unsandboxed side reads what the worker left
  in its folder only through `meta.worker_file`; an audit failing twice is abandoned and a fresh one prepared.
- Replays withhold pearl timings since 2026-09-27 (Online map sweeps and requests): TILE lines `x y 0 0` and no
  pearlCountdown events, so pearl stats (eaten, in view, blunders' spawns) come from tileChange. `watch.py frames` shows `?` for a
  pearl tile no countdown gives; `why` reads timings from the map file or a rebuilt variant (loop/online_fit.py). `mapgen.gen.parse` reads a removed, shortened
  or non-numeric TILE line as unknown (`board.unknown`); `blunders.py` replays a game without countdowns on the spawns
  it shows, with a WARNING, labelling its flagged moves `no_countdowns` (pearl regrets undercounted).

- **Where games turned** (sig, 2026-09-28): online.md, audits, rule counts, study.md, risers, evidence.md and
  post-mortems add bceval's turning points (`tools/bceval_read.py`, synced by `tools/bceval_sync.py`; their
  docstrings say how, and how to approve a held update). Changes and rankings only, never its P; never gates.

## Leader study

- **Study the leaders, not just recent battles.** Hourly upkeep also studies the ladder's top five teams by rank (us
  excluded, dev teams included): every saved ranked game of each one's current version, up to `maintenance.STUDY_MAX`
  (`maintenance.version_games`), downloading at most 25 new replays an hour into `~/.bc-loop/study-replays/` (each index
  row with both submissions and `replays.autoscrim_draw`). `study.md` gives per-game means of each team's behaviour
  (`tools/replays.py summary`) and deaths by kind (M56) next to our current submission's games. Reviews open
  their replays and turn the largest gaps into ideas. study.md is dated, and stale leader sections say their age
  (review M11, `maintenance.study`). study.md also has a block for the dev benchmark (M20): its side of our latest 30
  benchmark games against its newest submission, with the same stats (dev teams never fake games). Leaders' unranked
  games stay out (sig, 2026-09-26: teams fake them), and so does a ranked version that `loop/decoys.py` flags as a
  possible decoy (below the team's best earlier one at p <= `chance.TRIGGER_P`; sig, 30 Sep: some teams play worse
  bots even ranked; MISSES 103): its section says so and its gap stats are left out. Its "Gaps unchanged" section (M18) opens study.md: from
  `study-history.jsonl`, a gap to ranks 1-2 and the dev benchmark (each with 5+ games, all on one side of us) that held
  over our last two submissions (5+ games each) while no idea citing it is in flight or queued at priority 1-3, over
  history rows of the latest row's pool period only. Its "Map-matched gaps" (M69, `maintenance.matched_text`) compare
  each with us on shared maps only, portal crossings and in-place deaths among them (M80, M84).
- **Behaviour gaps** (REFLECT 2026-09-29 F3): `behaviour-gaps.md` (`loop/behaviour_gaps.py`, run after the upkeep's
  replay library step) gives our rate of each engine action (portal crossings, sprints, splits, rams) per turn
  offering it and what it paid beside ranks 1-2's, map-matched, flagged unless theirs clearly pays less (M100). Reviews
  give each flag an idea or a stated reason.
- **A new opponent version is news** (REFLECT 2026-09-28 F4). The online cycle
  alerts when a dev team or one of the top `online.WATCHED_TOP` teams plays a new submission (`online.version_news`);
  study.json records which versions a study read, and `health.unstudied_versions` lists an opponent version with
  `health.UNSTUDIED_REPLAYS`+ saved replays that no study read and no idea names. Both went blind when the site stopped
  naming submissions (MISSES 77).
- **A team is its id, not its name** (MISSES 75: a renamed rank 1). `discord/teams.json` (hourly leaderboard read,
  `loop/discord_teams.py`) keeps each team's current name and rank and its last `discord_teams.PREVIOUS` names, adding
  those saved elsewhere (`discord_teams.seen_names`) at each read; study.md titles ("formerly ..."), `.leaders/`
  samples and latest.md name teams from it. Each refresh alerts `teams: team N renamed ...` for
  `discord_teams.tracked` teams and one `teams: top N changed` line when rank 1 changes hands or a team crosses the top
  `discord_teams.ALERT_RANKS`; `health.old_team_names` flags briefing.md and DISCORD.md lines naming a tracked team by
  an old name.

## Riser study

- **What changed in the teams that jumped** (sig, 2026-09-28; REFLECT 20:52 F9): every `risers.CHECK_EVERY` in a
  Claude loop's upkeep, `loop/risers.py` has one worker (`loop/RISERS.md`) study the top teams whose rating rose
  beyond chance and their games' predicted wins, and any new rank 1, each at most daily: R-numbered findings, up to 5
  ideas (`risers:<run>`), alerts starting `RISERS`. Its docstring has the test and bounds (`health.riser_study`). A
  possible decoy (`loop/decoys.py`, as in the leader study) is named in its team's block and left out of the means.

## Backlog audit

- **Close what is done, merge what repeats** (sig, 2026-09-29: "lets do it once a day? in the loop"; MISSES 93):
  every `maintenance.BACKLOG_EVERY` in a Claude loop's upkeep, one read-only worker (`loop/BACKLOG.md`) judges the
  day's slice of waiting ideas (P1 first, rotating past `backlog.SLICE_CHARS`) and writes B-numbered findings and an
  edit list. `loop/backlog.py` applies an edit on the slice where it checks the citation (never a discarded result
  alone), within `backlog.cap`, through `meta.valid_backlog`; P1 closures are only proposed; `backlog.revert` undoes
  a run. Changes and proposals go in applied/; alerts start `BACKLOG` (`health.backlog_audit`).

## Bot tests

- **Sandbox builds are cached by folder name and .c/.cpp files only** (the toolkit's `clangtool.build`), so `unswbc
  run --sandbox` on a bot whose change is only in headers plays an older build. The referee copies each bot to a
  folder named after all its files first (`run.content_named_copy`, whose comment has the incident); anyone else
  running sandbox games (coders, subagents, the orchestrator) does the same or uses the referee/quick scripts.
  Non-sandbox builds hash every included file and are safe.
- Run `.venv/bin/python loop/bot_tests.py` from the current main checkout. It compiles the actual C++ bot headers and
  main program, runs focused scenarios under `tests/bot/` (several at once, `bot_tests.workers`), and checks two-turn
  protocol output for both teams and a fresh late-game child. It needs a C++20 compiler, not the game engine or network.
- For old champion clones, use the absolute shared runner path supplied in the lane prompt with `--bot bot --tests
  tests/bot`. Coder tasks run it through the main checkout's `tools/gated_test.sh` (`driver.GATE`), which reads the
  load inside their sandbox (MISSES 54). New lanes based on pre-test champions receive a local bootstrap commit of the
  current suite, so even
  deleting every test leaves history and is respected by later lanes; the candidate's own suite is authoritative
  thereafter. Native `quick` runs it automatically; both providers' shared evaluator repeats it before any CPU/SPRT
  games and records `bot-tests.log` and `bot_tests` in the result.
- Behind CLAUDE.md's test rules: loop and tool code that reads real files (replays, online.json, the site's pages) is
  tested on a sample captured from the real data, not a hand-written fixture (MISSES 13). Boundaries go through the
  parameter (build the smallest dragon `P.splitAtLength` and `P.childLength` let split, and one segment shorter), so a
  tune or a param change doesn't fail a test that still holds (E0076, Tuning ideas). Scenario tests cover
  state/pathfinding, targeting, safety, move selection, splitting and protocol I/O, not every game state.
- Tests can go out of date for strategy and implementation alike: improve or replace algorithms, structure and
  behaviour freely, in the same experiment as the tests they outdate. Tests confirm the new intent and catch
  unintended bugs; they don't prescribe how to build it. CPU/SPRT gates remain.

## SPRT bounds

- Until the Sprint freeze (1 Oct 09:00) SPRTs stop at `driver.SPRINT_PAIRS` pairs, not 2400, and the judge decides a
  test that ends at the cap (sig, 1 Oct: first 1200, then "lets cut evals down to 400"); it reverts by itself.
- The default bound (sig, 2026-09-24, CLAUDE.md) is the alternative the test weighs against zero, not a minimum gain:
  a change worth nothing still passes only 5% of the time, while a true +15 passes 95%. Most real gains are +5 to +20,
  which the old +35 default rejected about half the time (`driver.ELO1`'s comment). Keep +15 for big changes too;
  never raise the bound because a change is large. Predeclare +10 for a fresh confirmation of a promising result.
  Normalized Elo accounts for result variance; it is not the ladder's Elo rating or a fixed win-rate target. At about
  50 pairs a minute a full test takes under an hour. Smaller gains still count.

## SPRT rotation

- **The SPRT's trials rotate opponents** (sig, 2026-09-24, review finding M1): the champion, then each
  `driver.ROTATION` entry of `referee/gauntlet.toml`, one map each in turn (why each is there, and who left: the
  comment at `driver.ROTATION`). Against the champion a trial is the usual pair; against an opponent the candidate and
  the champion each play it on the same map and side, scored 0.5 + (candidate's points − champion's) / 4. So the test
  credits doing better than the champion against other styles, not only beating it: a copy that kept splitting to
  round 450 was even against the champion (13/24) but won 17/24 against the swarm where the champion won 13. Results
  show each opponent's score (0.50 = level with the champion). No idea opts out. An opponent trial costs 4 games. Each seat gets 1 / (1 + the rotation's length) of the trials;
  a running experiment keeps the rotation frozen at its claim.
- The SPRT, screen, target batch and prewarm play fixed sonar keys (`driver.test_bots`, `driver.opponent_bot`; from
  29 Sep): the champion and the candidate against opponents one, each opponent its own, head to head the candidate
  another, so pairs differ only where the change acts, Mac and Linux alike (MISSES 99). A tune's SPSA too (`tune.py
  --fixed-keys`): its plus and minus variants on the fixed key, minus head to head on the other, its rotation
  opponents on their own.

## Promotion gates

- Promotion requires the referee's **H1** decision and all correctness/CPU gates: no crashes, timeouts or
  nondeterminism, **p99 ≤50M and maximum ≤90M** CPU points (`referee/cpu.py`: the candidate against itself in the
  judge's sandbox on every map file in the referee checkout's `maps/ladder/`, `maps/retired/` and `maps/sprint/`).

## Opponent screen

- **Opponent screen** (sig, 2026-09-24): a candidate that is kept after its SPRT then plays the frozen opponents in
  `referee/gauntlet.toml` (old champions and champion variants, each described in its entry) on 50 maps, both sides, as
  the champion does. It stays `promising` instead of promoting only when clearly worse than the champion against one of
  them (paired z ≤ −2.5), so a neutral change passes, or over the ram family pooled (`driver.RAM_FAMILY`'s comment;
  policy `screen_family`, M62). A hold on one entry alone replays it on 50 fresh maps and stands only if still at z ≤ −2.5
  with the replay pooled in (policy `screen_replay`, frozen at claim; luck alone holds 1 level candidate in 7 on one
  entry: `driver.SCREEN_REPLAY_PAIRS`, M88). A candidate the SPRT leaves `promising` isn't screened
  (`driver.UNSCREENED`): the screen can't change its verdict. **Two stages** (sig, 2026-09-27; policy
  `screen_two_stage`, frozen at claim): each opponent is first judged on its reused SPRT trials (Screen reuse) and its
  own maps up to 25 maps in all (`driver.SCREEN_FIRST`; every reused trial when it has more, since they are already
  played); a candidate not behind the champion there (paired z ≥ 0) passes it (`stopped_at` in its screen row, shown in
  stats.md), and only the others (every ram family member while the family is behind) play the rest, to the same gate
  over all 50 (why: `driver.SCREEN_FIRST`'s comment). The late screen keeps full screens. Both stages play as one
  referee run, an entry's second queued once its first (a ram member's: the family's) is in and behind
  (`driver.check_pool`, `loop/screenbatch.py`); the late screen runs one per entry. Beating our own last version alone
  can reward a change other styles punish (E0048). Add opponents as new entries; never edit a used one.

## Screen reuse

- Screen reuse (2026-09-26; policy `screen_reuse`, frozen at claim; not speed or identical ideas, which run no SPRT, nor
  an aimed map mix, whose rotation played its mixed maps): against an opponent the SPRT's rotation also plays, the
  screen first counts the SPRT's trials against it (`sprt-rotation.jsonl`: candidate and champion on one map, both
  sides, same bot folders and toolkit, checked by `driver.rotation_trials`) and plays only the shortfall to 50 maps on
  its own maps: the same gate over the same 100 games, or 50 at a first-stage stop. A fallback to the full screen is an
  alert.

## Late screen

- An entry added after a candidate's evaluation began never plays it (`loop/rescreen.py`'s docstring has the case),
  so the **late screen** (`loop/rescreen.py`) plays each entry the promoting result lacks against the champion and
  against the champion before the earliest promotion since the entry joined that skipped it, on that result's screen
  maps and toolkit. It starts at the next upkeep tick and runs beside evaluations as a filler (it yields the filler
  share to a tune), stopping for a stop marker or a new champion; the idle fillers take what its half leaves
  (`native.screen_asking_now`). A speed or same-code champion owes what its predecessor owed (`rescreen.missing`). Clearly worse writes an alert and queues a priority-1 fix, never a demotion; results in
  `rescreen.json` and stats.md; health.py alerts when none started 6 h after an entry went missing, a run hangs, or
  runs keep failing.

## Official-map report and map splits

- Every result whose SPRT ran also reports 100 official-map games against the champion, a ladder report, never a
  gate, played after the result is recorded as a filler (which maps, its `pending` value, relaunches and alerts:
  `loop/official.py`'s docstring; seeds by copies differing only in `MAP_NAME`; a map changes only when committed to
  main, `driver.refresh_referee`). A map where one side
  won `official.SIDE_DECIDED` of the games is named as judging nothing (M65: near-self-play there goes by side). It also reports the
  SPRT's score per map style (mix, and each near-ladder family), since a gain on ladder-like maps can be diluted by
  the mix maps (E0050, `meta.MAP_STYLES`' comment), and per map size (`meta.AREA_BANDS`, whose comment has M28's
  reason), with `small` and `big` map mixes for ideas aimed at one size. Also a report, not a gate. Trap, deadend,
  fortress, portalrooms, macro, curves, worldgen and portal-bet maps are each their own style and the community maps' near variants are
  pooled apart from the ladder's (older results: `driver.style_scores`' comment). A layout's repearled near maps (`near_<map>_repearl`, from 2026-09-27)
  count in its own family, and all of them pooled as one `repearl` family too (`meta.by_layout`), so a layout keeps
  its trials; the one repearl family adds one look to the split bar below. Each style or size bucket of
  `meta.SPLIT_MIN_TRIALS`+ trials gets a z against level from the result's own per-trial SD, and counts only past the
  bar at which luck alone puts any of the result's buckets there at most `chance.TRIGGER_P` of the time
  (`meta.map_buckets`, `chance.corrected_z`): one clearly worse while another is clearly better is a map `SPLIT:`
  line, and a kept or promising result with one clearly worse alerts once (`health.map_splits`), as does a bucket
  worse in consecutive results (`health.style_streaks`), a report pointing at
  a version conditioned on map size or style, never a gate or a demotion (its replay over past results:
  `meta.map_buckets`' comment).
- A kept result whose official-map games where its moves changed (a map file's two games that aren't exact mirrors,
  `official.changed_games`) score below half, with its pairs lost against won at sign p <= `chance.TRIGGER_P` (a
  pair's two games aren't independent), alerts once (`health.official_changed`, whose comment has M53's E0175 case).
  It fires only when that p times the kept results tested (those with enough decided pairs to reach it) is at most
  `chance.TRIGGER_P`. A report, never a gate.

## Test blind spots

- **Question the tests too** (sig, 2026-09-25): a referee verdict is only as good as what it plays, so the
  orchestrator acts on CLAUDE.md's warnings without waiting for sig. `WARNING online:` is the live version clearly
  worse than the previous one in the same matchups (with p above the bar over the versions checked, after a
  confirming sample); `kept without`/`promising without` a result missing its screen, rotation or official-map
  report; `METHOD NOTES CHANGED` new blind spots in `~/.bc-loop/method.md`. The blind spots found so far, what closed each and what is still missing (pearl sprinters, a harvester at the leaders' 64-79% of drops) are in MISSES 69; each screen
  entry's comment in `referee/gauntlet.toml` says what it models and why.

## Rule counts

- **Accepted code is not correct code.** Passing the SPRT means a change beat the champion of its day, not that every
  line is right: measure any heuristic, weight or rule in real games, and fix, replace or delete it. The biggest gain so
  far, E0027 (+280), fixed a dead-end check that marked every move closed on up to 56% of turns. Every review audits one
  area of the champion's code (see `loop/meta.py`), with rule counts from fresh games of the champion's logging copy
  (`debugIndicator` on) that the orchestrator's code plays for it before the confined review starts, through the coders'
  `native.py games` path (`loop/rulecounts.py`, M17: reviews can't reach the hosts): `rulecounts.MAPS` maps drawn by
  style (`rulecounts.STYLES`, M66: portal and dead-end maps too) and across them by size (`rulecounts.SIZES`, within a
  size the official maps' pearl supply first, M97; unit-ceiling rounds, M71), each against every `rulecounts.OPPONENTS`
  entry, the side alternating by map. What they count and keep is in its docstring; all are reports. It runs once,
  before the worker's first run, in `<loop home>/rule-games/<review>/` (the worker can read it, not write it); the
  review folder gets a copy of the counts.
- **How deaths came about** (MISSES 64): rule-counts.md ends with the death table (`rulecounts.doom_section`,
  `watch.DOOM_TABLE_NOTE`; both sides' feeds `feed?` by their moves from foldRound, `watch.looks_fed`, checked against
  our debug lines, where `rule capfeed` is a feed too, M63; `capfeed?` before it, `rulecounts.capfeed_check`), with
  our `deeper` rows split by how the scorer rated the dooming move, how far below the runner-up scored and our heads
  in its room (`rulecounts.deeper_split`, M67, M81); a review's online.md has it per map for the live version's
  result-blind games, dev apart (`online.doom_text`, `online.refresh_dooms`). Health's `death tables` alerts once per
  replay given up, and when no game has a row after `health.DOOM_WAIT`. Reports only.
- **Two kings** (M86): its round-500 section counts, over every game, round-limit losses whose our second-longest at
  r499 exceeded the margin (by its fold gate at r450) and wins whose theirs did (`rulecounts.two_king_section`);
  post-mortems get the newest counts (`rulecounts.latest`).

## REFLECT and review lenses

- **When a miss is caught, by sig or by us** (sig, 2026-09-25; widened 2026-09-26, MISSES 12), don't fix only the
  instance shown; CLAUDE.md has the five steps. A defect the orchestrator finds itself is a discarded result the loop,
  not the idea, caused, a wrong tool or a lost slot. A class names what predicts the loop ("it reasons only about our
  own play", not "it missed fold hunting"); a detector measures the general quantity (e.g. a move's regret against
  every alternative); every review reads `loop/MISSES.md`. Don't wait for sig to ask: every `reflect.EVERY_SECONDS` the
  upkeep runs `tools/my_messages.py` since the last check itself (and with each of `reflect.CHECKS`, whose comment says
  what each flags: times typed from memory, MISSES 26, unanswered questions and more) and a confined read-only worker
  (`--effort medium`) lists, from that output and MISSES.md, what was handed to sig, judged from a partial read, reported
  but not acted on, raised by sig, or promised; `REFLECT list ready: <path>` then reaches the alerts file (`REFLECT due`
  instead when the worker can't run, including under a native session: then a fresh read-only pass, a subagent or the
  active model itself, makes the list, `loop/REFLECT.md`). Judge each item (it, or the worker, can be wrong: skip a
  one-off or an overreaction and say why), ask what sig is most likely to catch next, and record the classes in
  MISSES.md. Each review also takes a first-principles lens in turn (rules/API inventory, opponent model, pre-mortem,
  method assumptions, champion hazards and the rest of `meta.LENSES`, which says what each asks), since what the bot
  never does leaves no gap in any statistic; and acts on stats.md's "Never played" list (waiting ideas no result played
  games for, oldest first, and categories never played; MISSES 24).

## Throughput

- **Throughput is measured, not assumed** (sig, 2026-09-27, MISSES 35): game-machine time is watched like any
  result (`loop/throughput.py`). Every minute the upkeep's sampler
  (`throughput.sampling`) writes a row to `<loop
  home>/utilisation.jsonl` (the newest `throughput.CAP` kept): each host's games in flight against its capacity, by
  purpose (the evaluation and its stage, each coder, the fillers, a tune, a late screen, an official-map report), read
  from the referee's status files (`run.Scheduler.save`; coders': `native.coder_status`). `health.py` alerts when
  `throughput.IDLE_SHARE` of the capacity idles on average over `throughput.IDLE_MINUTES` while an evaluation runs or a
  candidate waits, naming the experiment, stage, idle hosts and, in a filler stage, each filler's state
  (`health.utilisation`, `health.fillers_now`); when the record stops; and when an SPRT's pace (games a minute x mean
  map tiles) stays under half the last SPRTs' median (`health.slow_sprt`). stats.md lists each recent result's wall
  time, games, games a minute and games in flight per stage, and the day's slowest (`throughput.stage_lines`);
  evalline shows the current use and idle hosts; `tools/game_profile.py` writes one game's CPU split, re-run weekly
  and after each toolkit upgrade (`health.game_profile`). Each game's row names its host and a remote one's CPU
  seconds (`run.GameResult.host`). Causes found: MISSES 35, 45, `loop/baselines.py`.
- `health.idle_any` applies the idle rule to minutes with no evaluation and no candidate waiting
  (`health.no_evaluation`), never both checks, once per stretch ended in the last 2 h, naming each filler's state
  (none running: which could start, and why not), the pending official-map reports and the ideas being coded; idle
  with everything played alerts once per champion; quiet under a stop marker.
- **Local stages** (MISSES 50): only the evaluation's host stages pause the fillers (the identical check is one); a
  wait inside one that plays nothing on the hosts is marked local (`driver.off_hosts`: a target batch's last replay
  reads). The evidence refresh after a promotion runs lock-free in the background (`driver.start_evidence`): the next
  evaluation and the follow-on don't wait for its games. Rows carry `"local": true` in a local stage
  (`throughput.evaluation_now`), where the idle alert asks why the coders and fillers didn't play;
  `health.local_stages` alerts when the evaluation's own games play on the hosts in a stage marked local (a host stage
  `driver.host_stage` misses) and, until it first works, when 30 local minutes pass with no coder, tune or filler game
  above its evaluation share on a remote host (`health.above_evaluation_share`).

- **The next test is prepared during the current one** (`driver.prepare`, both providers): a candidate coded while
  another holds the lock makes its maps, takes its rebase and merge turn if on an older champion (Claude driver), and
  runs its bot tests and CPU gate on its exact commit (prepared.json) as the evaluation's own
  (`driver.prepared_cpu_gate`; limits: `driver.prepare`'s comment), again for each new champion while it
  waits (M89, `driver.prepare_beside`), when a referee/ merge changes a passed gate's inputs on main
  (`driver.prepared_gate_stale`), and in a kept candidate's screen on its merge, after making its map set
  (`driver.make_set_ahead`, `driver.prepare_ahead`, `health.set_ahead`). `evaluate` reuses only passed checks for the
  same commit, engine, runner and gate inputs (`driver.prepared_checks`), else runs them locked.
  `health.prepared_stalled` alerts when prepares with time deliver nothing or a test waited over
  `health.PREPARE_TAKES_S` behind another unprepared, `health.prepared_failing` when they keep failing.
- **A `--first` candidate, a same-moves test, an idea claimed `"next"`, then the oldest coded, goes first** (REFLECT
  2026-09-28 F24; next from 30 Sep): each waiting or preparing candidate holds a ticket named by rank and coding-done
  time (`driver.queue_ticket`); the lock goes to the first live one (`driver.first_in_queue`), evalline lists every
  live one in that order, marking a starving one. A waiting SPRT ticket ages to next once it has waited longer than
  the queue ahead takes (`driver.median_evaluation_s` per ticket ahead and running evaluation; `driver.age_up`),
  never past a live older ticket that can't move. A ticket is live while its writer runs (pid and start time,
  `meta.ticket_live`); each poll removes the dead ones ahead, lock free or not. `health.evaluation_queue` alerts when
  candidates wait with no evaluation live (`health.evaluation_live`) over `health.QUEUE_STALL`, a waiter's merge turn
  aside; `health.eval_backlog` when none waits behind a live one while coders work. Native `evaluate` recovers an interrupted promotion only once it holds the lock (`driver.recover_held`), so
  it takes its place and prepares first.

## Health checks

- **Every file the loop writes has a check** (sig, 2026-09-26). A silent failure lives in a file nothing reads (MISSES
  13). `loop/health.py` checks the live state's invariants hourly and at every result, into the alerts file the
  orchestrator watches: the checks listed in `health.alerts`, each alert at most every 3 h; a check that
  raises is an alert itself. Dated promises go in `<loop home>/reminders.jsonl` (`health.py --remind`), each alerted
  once when due, not in the orchestrator's memory. The minute checks include `health.clone_cpu` (a clone's processes
  over half this Mac's cores for 10 min) beside `health.other_cpu`. `uv run loop/health.py --dry-run` prints every current alert and
  writes nothing. At each `REFLECT due`, add a health check for any file without one. Ideas are added only through
  `meta.append_ideas(state, meta.valid_proposals(...), source)`, which refuses, as `meta.refusal` does every source's
  (a review's too), a bare threshold without "bare threshold because" and an idea without `meta.MIN_OPTIONS`+
  `Options:` (the rules: their comments, `meta.bare_thresholds`, `meta.VALUE_SWAP`; MISSES 71); each refusal is alerted, and a coder's, audit's or review's saved
  in its run folder to re-add (`meta.report_refused`); `health.refused_proposals` alerts on one left 2 hours neither
  re-added nor decided. Ideas are edited only through `driver.mark_idea` or a locked
  `driver.read_ideas`/`driver.write_ideas`, which refuses a row missing a field every reader needs
  (`meta.idea_fields_ok`, MISSES 36); one that gets in another way (a hand edit) halts every writer, so
  `health.backlog_rows` alerts on it. Notes on ideas: `driver.orchestrator_note`; coders follow only the
  orchestrator's and sig's as corrections (`driver.idea_text`, MISSES 46); those written after a claim reach its
  running Claude coder within a minute as `.orchestrator-notes.md` in its clone (`driver.notes_forwarder`), so no
  message is needed. Each promotion flags todo ideas naming a
  param it changed (`driver.flag_live_ideas`, M61).
- `health.leftovers` alerts on unlocked, clean worktrees whose HEAD is in main and that haven't moved for 2 h (naming
  the ignored files `git worktree remove` would delete; a locked worktree is a running agent's), and on wip/E####
  branches whose idea is neither to do nor being tried.

## Briefing

- **The briefing** (sig, 2026-09-26). `~/.bc-loop/briefing.md` is the short list of what every piece of research
  should assume: the contest's rules, which references to trust, rivals' tricks, what has worked. Every worker
  launched through `meta.worker_io` gets it in its folder (`meta.brief`) and coders in their prompt, so they needn't
  ask the orchestrator (a worker that can message it is an injection path); fresh merge turns and tune test-update
  turns don't. The orchestrator updates it in the same response as any decision
  that changes what research should assume (each line with its source and date), and `health.briefing` alerts when what it
  distils (its comment lists them) is an hour newer or it goes a day unconfirmed.

## Prerequisites and stacks

- **Test prerequisites first** (sig, 2026-09-25): an idea that needs another's information or code carries `"after":
  [ids]` and isn't claimed until those are tested, nor while one's coder measured it delivers 0 (its `Delivery:`
  line, `driver.note_delivery`, method M87; the reader's prompt gets each prerequisite's line); when a result is kept, waiting ideas citing it move up
  (`driver.unblock`, MISSES 7; not screen ports or re-checks, which cite it without waiting on it); a prerequisite
  claims at its waiters' best priority, rank and next flag (`driver.waiter_ranks`). Don't bundle a
  prerequisite that changes play and its strategy into one untested candidate: order them as a **stack** (MISSES 21):
  the prerequisite is measured alone first, and when it wasn't kept but left a candidate, its dependent's coder
  cherry-picks that candidate and the pair is tested as one (`stacked_on`, `driver.stack_for`); one that left none, or
  whose code the champion already has (`git cherry`), isn't stacked, and a missing one is built by the dependent
  behind its own switch. Only commits after the coder's `stack-tip` tag count as the idea's (`driver.STACK_TIP_TASK`).
- **Combining** (sig, 2026-09-29): parts that do nothing alone are one candidate, each behind its own switch (I952);
  near-duplicates merge; independent changes are never bundled; formula swaps share a no-loss test only on one
  decision, like for like. New ideas are flagged against waiting and in-flight ones as they join
  (`meta.intake_siblings`, MISSES 95).

## Post-mortems and follow-ons

- **Every result that isn't kept gets a post-mortem from the coder that built it**, while it still knows the code: the
  Claude driver resumes that session once (`loop/POSTMORTEM.md`), and native `evaluate` returns the same task,
  followed by `native.py followups E####`, both after the result's judge (Judged verdicts). It says whether the idea
  or the implementation failed, with measurements, and proposes up to 3 attributable follow-ups (`followups.jsonl`,
  validated like review ideas, `source: postmortem:E####`). A result that ended before any game gets one too (MISSES
  58, `prune.ended_early`: a "no code" stop with its coder's reason, or a candidate the bot tests or CPU gate failed),
  told what it has in place of games. A no-code one is asked whether its stop counted the rows the change acts on, and
  if not to propose it again with a stop on them, told the coder's changed-decision count when over 0.1 a game
  (`meta.stop_changed`, MISSES 61), and ends `Reruns: <its id, or the follow-up above> after <prereq>` or `No rerun:
  <why>`. The lane is freed first. A turn ending in an error (a usage limit) or waiting (`prune.still_waiting`) stays
  owed as `<turn>.failed.json` (`prune.turn_ok`), its losses kept; the upkeep reruns it once when nothing holds its
  clone (`maintenance.owed_turns`), then health.py alerts; `driver.py --turn E####` reruns it by hand.
  The turn runs as `bc-E####-postmortem-<8>` (or `-followon-`), told that name and recorded in
  `runs/E####/<turn>-worker.json` (`driver.run_postmortem`; `health.turn_workers`). It ends with `Predictable: yes/no: <the check>` (could data we had have shown it?; stats.md lists
  them), and the orchestrator puts a check that recurs into the step that should have run it (MISSES 32). A change
  adding information names in `Consumer:` the decision that misused it, and is discarded only when none is at fault. It also asks
  what missing change the idea needed; one flagged `"prerequisite": true` is queued first, then the candidate's retest
  stacked on it. A kept result gets the same turn with `loop/FOLLOWON.md` instead (sig, 2026-09-27; `followon.json`,
  `source: followon:E####`): up to 3 follow-ups that build on it (a tune of its new numbers, a conditional version
  where its opponent, style or size scores pulled apart, an extension, complement or simplification), each with its
  gate and its evidence's n and chance, and a formula follow-up or `Constants:` reason per cutoff it added
  (`driver.cutoffs_note`); none for the kinds in `prune.NO_FOLLOWON_KINDS`,
  which leave no behaviour gain to build on. Either turn may build one **quick fix** (sig, 2026-09-27): a small change
  to its own candidate aimed at a defect or condition a check measured (the turn, a `SPLIT:` line, the screen's
  `promising`, a style or size split). The coder commits it on the candidate in that turn (no coding slot; its rules:
  `driver.QUICK_FIX_TASK`) and marks one proposal `"quick_fix"` with the defect, n and p (at most `chance.TRIGGER_P`).
  `driver.quick_fix_idea` checks the commits (those folders only, small), saves them as branch `fix/E####` and queues
  a priority-1 idea whose coder only cherry-picks them: on the champion after a kept result; after one not kept,
  stacked on that candidate (`quick_fix.of`, `stack-tip`) in place of its unchanged retest, and what waited on that
  retest waits on the fix. It gets a fresh SPRT with the default gate (or `elo1` 10 to confirm) on maps its source
  never played, its own result and post-mortem. At most `driver.QUICK_FIX_CAP` quick fixes along a lineage
  (`driver.quick_fix_lineage`: through sources, retests and merges, not stacks), then ordinary follow-ups only (why:
  `driver.QUICK_FIX_TASK`).

## Kept on probability and re-checks

- **Kept on probability** (sig, 2026-09-25): the SPRT stops early for clear results, but a real +2 to +5 gain runs out
  of trials. So a test that ends without H1 still promotes when the chance its gain is real is at least 90%
  (`driver.sprt_verdict`), with every other gate as for H1. A noloss
  or formula test uses the chance it is no worse than its elo0 (`meta.noloss_probability`; sig, 2026-09-28). It applies
  to experiments claimed after the rule (their frozen `limit_probability`), never to old results, which get fresh
  retests instead.
- **Re-checks only on evidence** (sig, 2026-09-29: "they seem rather wasteful"): a switch-off re-check (the champion
  with a kept behaviour's switches off, priority 1, `driver.queue_recheck`) is queued only on measured evidence of
  harm, which its hypothesis carries, never at promotion or as a review's routine pick. Triggers: the online
  compare's longest margin falling (10+ pairs, sign test p <= `chance.TRIGGER_P`, M33) queues or raises one for each
  change kept between the two live builds, not identical or formula keeps, with `online.RECHECK_SHARE` of its trials
  against the rammer tradedown (`online.recheck_mix`, M62), one alert (`online.raise_rechecks`), a new one only at
  p <= `chance.TRIGGER_P` over `online.versions_checked`; a review or audit on a `SPLIT:` line, an online drop or rule
  counts against it (and a formula keep's on off-generator evidence); the orchestrator on a judge's flag (a measured
  loss; the flag only alerts). A kept stack's re-check switches off the whole stack. Reviews retest the results `stats.md` lists as not revisited.
- A promising change its judge didn't discard is retested alone because a bundle hides which part helped (sig, 2026-09-25); a stack isn't a
  bundle: its prerequisite was measured alone first.
  A post-mortem queues that retest itself only for an estimate of at least `driver.RETEST_MIN_ELO`
  (a test cut off at its pair cap too; a no-loss kind's there whatever its
  estimate); below that its follow-ups on the candidate stack on it directly. A
  quick fix (Post-mortems and follow-ons) takes the retest's place: its test is the candidate tested again, with the
  fix.

## Judged verdicts

- **No verdict is an automatic pass or fail on a threshold alone** (sig, 2026-09-28: "it shouldnt be a automatic pass
  or fail based on some threshold. the ai agent should reiew it ... not just with the portal examples - but
  eveyrhtign as well"): a change that helps a lot in one situation gains little overall. Every result whose SPRT ran,
  kept or not, gets an **independent judge** (prompt
  `loop/JUDGE.md`), a confined session over the result's copied evidence: `discard`, `keep` or `confirm`
  (`agree` or `flag` for a keep). `loop/judge.py`'s docstring has the rules: a keep applies by itself only within its
  guard, else it is an **ask** (sig, 2026-09-28: "keep can run even after the rest its fine - or it discusses with
  u") for the orchestrator's `driver.py --judge-keep E####` or `--judge-confirm E####`, each with `--rule '<why>'`;
  hard gates (crash, timeout, nondeterminism, CPU, tests, the skipped screen) always hold; a confirm retests the
  candidate on that subgroup's family's fresh maps (`judge.family`), once a candidate and lineage; a flag only alerts;
  a discard makes a promising result's idea tried and drops its unchanged retest (MISSES 98).
- The recording driver judges before the coder's turn, which sees the verdict. `JUDGED` alerts carry each verdict and
  ask; the orchestrator's `Rule check:` is still written and may cite it. A failed or skipped judge, or an ask
  undecided 2 h, stays owed (`health.judge_turns`; `driver.py --judge E####`). Native sessions: Native session details.
- The judge reads the result's behaviour stats too (`behaviour.md`, Behaviour stats).

## Behaviour stats

- Each SPRT's result row carries `behaviour` (sig, 2026-09-30: "create tools to compare stats of your new bot vs old
  bot so you know what breaks/what improved - for the evals"): the candidate's games against the champion's on the
  same map, side and opponent, in deaths by cause, deaths within 10 rounds of a portal crossing, crossings and the
  first one's round, pearls r0-99, longest dragon at r250/396/499, king feeds r300-396, splits and the first one's
  round and fold conversions, pooled and by map style, each row with n and a paired sign test's p (`loop/evalstats.py`
  has the rules). The rows past `chance.TRIGGER_P` once multiplied by the number of rows (`moves`) end the RESULT
  line; the table goes to the judge (`behaviour.md`), the post-mortem and follow-on (`.postmortem/behaviour.md`,
  `behaviour_moves`) and stats.md. A rejudge keeps the row's table.
- The SPRT deletes each replay once scored, so `referee/sprt.py --stats-queue` links opponent trials' replays for
  `evalstats.WORKERS` workers at nice 19 beside it (`driver.behaviour_stats`; one read 15-87 trials a test on 30 Sep),
  a trial only while fewer than `sprt.STATS_BACKLOG` wait unclaimed and a champion game of it played in this test (a
  reused baseline has no replay): a sample of what the workers keep up with, never picked by result; a trial's two sides are one unit of its sign test. It drains at most
  `evalstats.DRAIN_S` after the SPRT, beside the screen. A report, never a gate; `health.behaviour_stats` alerts on
  its first real firing and on a run it read nothing from.

## Shared map sets and reused baselines

- **Shared map sets and reused baselines** (sig, 2026-09-25): the rotation plays the champion against each opponent
  beside the candidate's opponent games (3.5-3.6 games a trial instead of 2). Experiments tested against the same
  champion and toolkit on the same general map mix therefore share one fresh map set
  (`~/.bc-loop/shared-maps/<champion>-<epoch>[-<mix>]/`, `meta.shared_set_name`: today's `driver.shared_mix`, in
  mixed_maps' order; an experiment frozen before worldgen plays the eval set named without a mix, so a
  baseline is never read on another kind of map), and the champion's opponent games, in the SPRT and the screen, are
  played once and reused (`sprt.BaselineCache`, keyed by both bots' sources, the map and side, and the toolkit
  version; the set's maps only). A test citing an experiment already measured on any of the champion's sets under that
  toolkit gets private maps, so a retest never replays its discovery sample; one admitted keeps its set on a restart. So do a mix of another share (`driver.shareable`; Fresh
  test maps and map mixes) and one frozen on a rotation of another length (the set is dealt over today's seats); an
  aimed mix's style maps alone are private. The upkeep tick plays those baselines ahead of time as a
  filler (`loop/baselines.py`, 2026-09-26; progress in `baselines.json`, whose `maps` is the set's mix: a record of
  another starts over): it makes the champion's set as a test would (`driver.shared_set`, same folder, seed and maps;
  a champion's new set drops only other champions' recordings), then plays the rotation's champion games for the first trials
  (`referee/prewarm.py`'s order), the champion's screen games against the opponents the rotation doesn't play, then
  more rotation trials, in stages (`baselines.py`: `EARLY`, `LATE`, `STEP`, `SCREEN_MAPS`) played as one stream
  (`baselines.play`), paused between them while an evaluation plays on the hosts and killed by a stop marker or new
  champion. Tests then play only the candidate's games, the same either way. Baseline games also record the champion's
  moves, and a candidate game in the same seat answering every recorded turn as the champion did reuses its result
  unplayed (`sprt.BaselineCache`; 1 in 20 is played to check, and a mismatch stops prediction on that set). It needs
  the candidate on the champion's sonar key (`sprt.predicting`; off from E0090 to MISSES 99: MISSES 35).
- **Map pool** (`loop/mappool.py`, sig 2026-09-29): new sets take never-used maps made in idle time first.

## Pearl seeds

- **Pearl seeds** (from unswbc 1.1.0, 2026-09-25): the engine seeds pearl respawns randomly per game unless told
  otherwise, so the referee passes `--seed` derived from the map file's contents (`run.game_seed`). Every game on a
  map (both sides, candidate and champion, the rotation's opponents, local or remote) shares one pearl schedule,
  keeping pairs as tight as with the old fixed seed; identical-move checks stay exact. Toolkits before 1.1.0 get no
  seed.

## Fresh test maps and map mixes

- Fresh test maps are `mapgen/gen.py --style eval` (sig, 2026-09-24; its docstring and `NEAR_EVERY` and
  `*_REMAINDER` comments give each kind, share and why): a third `near`, perturbed
  variants of the ladder maps, current and retired, and of the Sprint maps, so the ladder's map types count without
  replaying the ladder maps themselves; each keeps its source's sealed start rooms (`gen.keeps_rooms`, since
  2026-09-30), its portal pockets with their laps and pearls (Portals' 8-tile laps, `gen.pocket_shape`,
  `gen.kept_parts`) and teams that never meet (`gen.sealed`). Half of those (`gen.repearled` says which and why) keep the layout but draw
  the pearl field afresh (`gen.redraw_pearls`, `near_<map>_repearl_<seed>`, pooled as `repearl` in the per-style
  report). A ninth each go to near variants of the community maps
  (`gen.community_pick`, MISSES 59), `trap`, `deadend`, `fortress` and `macro` maps, an eighteenth to `curves` (diagonal and curved kelp walls,
  `gen.diagonal_wall`), the rest to random `mix` maps.
  `worldgen` and the portal styles (`meta.PORTAL_STYLES`) are outside eval.
- **Worldgen** (2026-09-28, sig's go-ahead): another competitor's generator, vendored unchanged in `mapgen/worldgen/`
  (README: origin, pinned hash; `gen.worldgen_map` wraps it), ground none of our generator's choices made (E0204's ram
  gate helped on generated big maps only). **Portal bets** (sig, 2026-09-29): `deathportal`, `vault` and `portalbet`:
  death portals and walled vaults alike from outside (gen.py's portal-bets section); with them, every Trauma and portal
  style is a portal style (sig, 2026-09-29). **General maps are eval's and
  `meta.GENERAL_STYLES`'**: every experiment with an SPRT on fresh maps, a tune too (not speed or identical ideas),
  freezes at claim a mix of `meta.general_shares` (worldgen, the portal styles `meta.PORTAL_SHARE`), eval the rest, and
  a declared mix keeps its styles at their shares (a portal style's replacing its general one), the rest split in the
  same proportion (`meta.claim_mix`). Earlier
  claims keep theirs (`driver.EARLIER_MIXES`). Every map is made at `home.GAME_NICE` (a worldgen map ~1.5 s of CPU), one
  a pool task (`gen.generate_all`).
- An idea aimed at map styles may predeclare a **map mix**, `"evaluation": {"maps": {"sprint": 0.5}}` (styles in
  `meta.MAP_STYLES`, each described there; each aimed share within `meta.MAP_SHARE`, together at most its top,
  `meta.map_mix_ok`; M5/M9): its SPRT and tune play those shares of fresh style maps interleaved with general maps on
  a private set (`driver.mix_sources`, `driver.mix_plan`: balanced over the seats and through the SPRT, MISSES 59), its general maps and its screen's from the champion's shared set of today's mix when
  it may (`driver.aimed_general` by seat, their champion games prewarmed; `driver.screen_maps`, `meta.general_mix`),
  else private. A private set is listed in full up front (`driver.mixed_paths`) but made in play order as its SPRT
  plays (`driver.lazy_making`, `driver.LAZY_LEAD` ahead of the maps read, `sprt.py --pending`; stale:
  `health.lazy_markers`), a refused map refilled at its path. The referee's mapgen must make every style a mix names
  (`driver.missing_style`; tunes, prepare and the prewarm wait, `waits_for_style`). A mix without an aimed style plays
  the champion's shared set of that mix (Shared map sets and reused baselines), its screen reusing its SPRT's trials.
  Retests, re-checks and quick fixes carry the aimed styles (`driver.same_mix`); an idea without one (or a quick fix of
  a keep) whose declared circumstance or stated count puts its change on named styles claims their family
  (`driver.circumstance_mix`, MISSES 90); claims add the general styles at their day's shares. A target batch's maps are
  in the SPRT's mix (`driver.target_maps`; eval maps for a mix frozen without worldgen); the CPU gate (organiser maps),
  the quick check (fixed maps), the identity check (eval) and the late screen (its result's screen maps) keep theirs. A
  coder may set one aimed style before any game, on its shadow count's evidence: in place of none or a claim's, or
  beside its idea's own mix, which stays (E0378); and an opponent mix when its idea declared none (when and how:
  `driver.apply_coder_mix`'s comment).
- An idea acting on a style the rotation lacks (a king hunt or fold against a thin-ending team, an opening against
  portal scouts, drop eaters, crowders: M44, or a team holding the contested middle first: `rusher`) may predeclare an **opponent mix**,
  `"evaluation": {"opponents": {"earlyfold": 0.33}}` (an entry in `meta.MIX_OPPONENTS`, each with its measurement,
  at a share within `meta.MAP_SHARE`, `meta.opponent_mix_ok`; not for speed, identical or tune ideas; method M58):
  that share of its SPRT trials play that gauntlet entry, frozen at claim, the rest their usual seats (how:
  `driver.trial_order`'s and `driver.drawn_plan`'s comments; MISSES 59). Retests, re-checks and quick fixes keep it
  (`driver.same_mix`); the screen counts its trials as the rotation's (Screen reuse). Coders can't set one: nothing
  counts changed moves by opponent yet.
- **Tried and not added** (2026-09-27): long-strip and arena styles and two map checks, and the rule a style is
  kept by (the end of `mapgen/gen.py`'s docstring).

## Remote host capacity

- Reclaimable memory is free + inactive + speculative (`sysctl -n hw.ncpu`, `uptime`, `vm_stat`). The load gate
  (CLAUDE.md) counts the referee's own running games as its load, not other work, and binds auto and fixed workers alike and is this Mac's, so it applies to each game or prediction check the
  scheduler would start here (`native.game_slots`), not to a batch: a batch starts its remote games at once and, with
  no host usable, waits here game by game (sig, 2026-09-27, MISSES 53), and a separate process
  counts an SPRT's games here as other load. A tune and a sweep batch don't wait first
  either: their variant builds here take about a second each. identical.py's replay digests after its games take at
  most this Mac's game slots (`identical.digest_workers`). `health.batch_starts` alerts on an SPRT or screen that
  begins with 2 min of no game while the hosts sit half idle. When sig sets another share for a while,
  `~/.bc-loop/load-fraction` holding a number from 0.1 to 0.9 (`home.load_share` clamps it) replaces 0.7 for the
  loop's games (read on every check); delete it when that permission ends. Remote hosts use their whole machine,
  taking games while they have idle cores (CPU busy share, not load average: MISSES 35), never past their `max`
  in `remote.json` or process limit nor under 2 GiB reclaimable, measured in the background (the rule: `remote.Host.measure`'s
  comment; the entry fields, `shared` and `connect_timeout` included: `referee/remote.py`'s docstring).
- A host only sig can switch on (zz-wsl, sz-wsl: his PCs) down over an hour goes into the next status line once, with
  its cost in game slots (MISSES 19); a `part_time` one, alert off, evalline names instead (`evalline.down_once`).
  Any other host down longer than one evaluation alerts `HOST DOWN:` (`health.host_outages`).

- **Niced games on this Mac** (sig, 2026-09-27; his words: MISSES 62). Every loop game here runs through
  `/usr/bin/nice` (`run.nice_command`, `native.lower_games`) at `home.GAME_NICE`, or `home.YIELD_NICE` for
  the players beside an evaluation, but the evaluation's own CPU gate and identical check (`native.NORMAL_PRIORITY`)
  and a queued candidate's prepared CPU gate, within half this Mac below dev work (Concurrency and host shares).
  Every headless Claude worker runs at `home.WORKER_NICE` (`meta.private_tmp`), which its commands and games
  inherit (a sandbox refuses a lower one; `native.worker_niced`), gated as niced: dev work beats their scripts, they
  beat the games. The fillers' and the evidence refresh's processes run at `home.YIELD_NICE` (`meta.spawn`, MISSES 50). A niced game
  out of time (a turn past the 10 s wall limit, or `run.GAME_TIMEOUT_S`) is replayed at normal priority, a worker's
  at its own (`run.watch_game`; `health.niced_replays`); a host-lost head game replayed within the slots plays
  unniced (`run.Scheduler.replay_here`). Niced games take the cores normal-priority work leaves idle,
  within the load share, memory and sig's sessions' load gate (`home.niced_slots`, `home.sessions_full`); the players
  beside an evaluation step back for its (`native.mac_half`, `native.players_full`). The split of busy cores comes
  from one process (`home.py`'s docstring); `health.cpu_split` alerts when it is stale while
  games play, `health.mac_waits` when a job this gate holds waits longer than an evaluation takes.
- **This user's process limit** (kern.maxprocperuid, MISSES 67): every game here also waits for room under it
  (`home.game_room` in `native.game_slots`; why, how games count and when it fails open: its and
  `home.PROCESS_RESERVE`'s comments). A dropped game: `Scheduler.replay_here`; spells of no room or no reading:
  `process-limit.json` (`home.ProcessWatch`, `health.process_limit`); a fork refused anyway is replayed (Game retries).

## Clone protections

- Experiment agents edit `bot/` and `tests/bot/` only; changes elsewhere fail evaluation. Of the loop's state they read
  only `driver.READABLE_STATE` and write none. They can't write their clone's `.git/config`, `config.worktree`,
  `commondir`, `hooks/` or `info/`, or the user's git config (`driver.lane_settings`: the driver runs git in the clone
  unsandboxed, and those run programs); the driver's git runs with hooks, fsmonitor, pager, editors and signing off, and
  refuses a clone whose git folder or config changed or whose git folder holds a symlink or other special file
  (`driver.clone_intact`). Clones are made with `--no-hardlinks`, and the main repo's objects pass
  `publishing.repo_intact` (fsck, no hard-linked files in `.git`) before every export and promotion. Every fetch from a
  coder's or worker's clone goes through `publishing.fetch_checked`: into a quarantine repo borrowing main's objects,
  checked there by fetch.fsckObjects and repo_intact's fsck, then into main (the next fetch deletes one a crash left;
  `health.fetch_quarantines` alerts). Before git diffs or rebases a coder's commits, their number and each one's paths
  are capped (`driver.too_many_files_in`): a few objects can name billions of paths.

## Reference checks

- **A change updates what describes it** (2026-09-26, MISSES 22; the rule is CLAUDE.md's, pointers like
  `health.alerts` included). `tools/hooks/pre-commit` blocks a commit while `tools/check_refs.py` finds a broken
  reference in a file it stages or to something it changes (`git config core.hooksPath tools/hooks`),
  `tools/hooks/pre-push` a push to main changing code with no pass marker (only `tools/merge.sh` writes one), a Claude
  Code session's commit on main, and `tools/hooks/reference-transaction` its other moves of main (merge.sh's aside;
  accidents only, bypasses exist: pre-push keeps untested code off origin), health.py alerts on main's broken
  references (`DOC REFS`), and each REFLECT judges the comments whose code changed since the last one (`check_refs.py
  --drift --since`, `loop/REFLECT.md` step 4). History files (`check_refs.HISTORY`) name removed things on purpose:
  there a broken reference is only a warning.
- The same hook blocks a commit that stages CLAUDE.md or this file over its byte cap (`doc_size.LIMITS`), or a .md
  commit leaving a "LOOP.md: <heading>" pointer in a tracked doc that names no heading here
  (`doc_size.broken_pointers`: rename a heading and its pointers together); health.py alerts `DOC SIZE` and
  `DOC POINTER` on main's (`health.doc_size`).

## Why the rules say what they say

Fuller wording of CLAUDE.md's brief rules as of 2026-09-27 (incidents: MISSES.md); CLAUDE.md's wording is the rule.

- **Conditional, not constant** (sig, 2026-09-25). A result that helps against one opponent or situation and hurts
  against another (the SPRT's per-opponent scores and map styles and sizes, the screen, online results by opponent,
  leaders playing opposite styles) is strategy evidence, not test noise: the behaviour should usually depend on
  something the bot can observe (their numbers, longest seen, how hard they fold, the round, the map), not on a better
  constant. Results pulling apart are logged as `SPLIT:`; reviews, audits, post-mortems, follow-ons, coders and the
  orchestrator turn them into conditional versions (MISSES 2).
- **Before anything goes on sig's list** (sig, 2026-09-26, MISSES 19): a verdict's source may be a blog, a thread, a
  log or a set of games; a blocking rule whose absence carries a risk (the only copy of something, stale state) is
  raised once, when first hit; a recommendation is never re-asked in later status lines (REFLECT 2026-09-27); the grep
  before a loop or method change covers its name and key terms (MISSES 15, 35); a speed idea says what the same
  machines were doing instead; a new prompt rule is checked against the last 10 results (which would it have stopped,
  and were any kept?); a landed decision's queued checks go with its reminders, in the same response.
- **Answer every question** (REFLECT, 2026-09-26): a question lost among status updates is a miss.
- **Status lines name and time things** ("started 11:52", "done 12:02"): the evalline format is in its docstring
  (MISSES 28); every hh:mm in a record is read from the clock (MISSES 26). The queue live in a browser:
  `tools/queue_page.py`. Its goal line (`evalline.goal_line`) counts the work in
  flight and next queued whose map mix aims at sig's stated goal, so a drift shows before sig asks (MISSES
  104); act on a falling count or say the goal is being traded. While under `driver.GOAL_CLAIM_SHARE` of those in
  flight aim at it (`driver.goal_short`), a claim takes a goal-aimed idea of priority 3 or better first, after
  next ones and idle fillers. The agents line says whether the alerts watcher runs; `evalline.py --results-since
  HH:MM` lists the results since then (the prompt hook's note when sig returns after 1 h).
- **Counts come with their chance** (sig, 2026-09-27, MISSES 34): counts are games, pairs, deaths or watched cases;
  `loop/chance.py` has the sign test, rate interval and paired z.
- **An observation is a trigger, not a report** (sig, 2026-09-25, MISSES 8): "matters more than it looks" is one too;
  check and change the queue and rules instead of only telling sig. `stats.md` lists coders' `Blocked by:` lines.
- **A caution is not a fix; ask sig's next question first** (sig, 2026-09-25; MISSES 9, 10): known weaknesses are
  the "caveat" or "it can overshoot" one would write; sig's next question may also be "why not order it?".
- **The trusted side never trusts a worker's folder** (2026-09-26, MISSES 17): `meta.worker_file` reads a plain file
  with one link and no links on its path, capped; a worker's stdin, output and builds live where it can't write.
- **Walk every new route through every step** (2026-09-26): tunes had no coder, so nobody could update the tests they
  broke (E0076).
- **Waiting is a cost** (sig, 2026-09-25): X may start now as a subagent or a measurement from saved data; a reason
  to wait may be removable (a check that can't tell a refresh from an edit). Waiting on a human or an unneeded
  boundary wastes idle machines and coding slots.
- **Everything that reaches the orchestrator gets a decision** (sig, 2026-09-26, MISSES 14): that covers Discord and
  forum reads, map ideas, audits, reviews, post-mortems and idle notices; check claims against the messages,
  study.md, the code and the results; acting is queueing an idea, changing the loop or a test, replying, or reviewing
  and merging. A report without a decision is unfinished.
- **Exploit the opponent, not only fix ourselves** (sig, 2026-09-25, MISSES 3): the loop is driven by our own
  losses, so each finding tends to become "fix our weakness"; an opponent doing the same can be punished.
- **A discarded result rejects one implementation on one champion, not the idea**: bundles hide which part hurt
  (E0026 bundled four sonar behaviours and lost −45, most likely from one of them). Each review sees how many
  promotions every result predates.
- **Ideas never ban a behaviour by citing a result**: the review validator rejects that.
- Sig's standing push go-ahead (2026-09-24) covers loop code, prompts and docs; promotions push through
  `driver.push_champion` (sig 2026-09-26; a failed push is logged).
- A new outward action sig grants (posting, publishing, a public push) gets what must never go out written down and
  shown to him before its first use (MISSES 27); every permission he grants (a standing one especially) goes into a
  memory note in that response, or the next compaction loses it (MISSES 31).
