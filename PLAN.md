# Plan: win UNSW Battlecode 2026 with a continuously improved bot

Status (2026-09-26): the loop runs continuously. Two coding tasks are overlapped with one evaluation at a time,
games run on this Mac and sig's SSH game machines, and reviews, replay audits, post-mortems, tunes and a variant
sweep run beside them. Every result is in `~/.bc-loop/results.jsonl` (each review gets a `stats.md` summary of
it), the current champion is the `loop/champion` branch, and accepted champions are uploaded automatically. A
native-session entry path covers times when Claude quota is exhausted.
[`CLAUDE.md`](CLAUDE.md), also available through the `AGENTS.md` symlink, is the authoritative policy for
both paths; it supersedes older proposals
in this plan and the research notes. The architecture and timeline below also contain roadmap items, not
claims that every planned feature is implemented. Research evidence:

- [`docs/unsw/`](docs/unsw/README.md): the official docs and rules, mirrored in full.
- [`research/unsw-game-analysis.md`](research/unsw-game-analysis.md): the rules digest, facts from the engine
  source, 73 top-ladder replays analysed, local timing.
- [`research/mit-battlecode/`](research/mit-battlecode/): 37 postmortems and what winners did.
- [`research/rsi/`](research/rsi/): self-improving systems and how to build the loop.
- [`research/snake-ai-and-eval/`](research/snake-ai-and-eval/): snake-game AI and testing statistics.
- [`CLAUDE.md`](CLAUDE.md): shared operating policy, team decisions and facts the organisers confirmed.
- [`loop/LOOP.md`](loop/LOOP.md): how the loop's mechanisms work, with their history (CLAUDE.md points into it).

## 1. What "self-improving" means here

The model's weights never change. What improves is the **bot's code**, and the **loop that improves it**:

1. **Inner loop, improving the bot.** Each coding task takes one idea ("dragons stop dying to walls"), changes
   the bot in its own clone, checks it, commits, and returns control. The **referee** tests it against the
   current champion. The orchestrator records the outcome and starts the next task; it does not stop after
   one batch. Broader opponent and held-out checks guard against overfitting.
2. **Outer loop, improving the improver.** Every `meta.META_EVERY_S`, or when the backlog runs low, a
   separate review session reads the log of every experiment (what was tried, what won, what it cost) and rewrites
   the playbook, the idea backlog and `method.md` (blind spots it finds in the tests). As built (2026-09-26) it
   can't edit the lane prompt or tools; the orchestrator changes those, and the planned A/B test of prompt versions
   was not built.
3. **The referee is out of every agent's reach, enforced by the operating system, not by instructions.** Every
   self-improving system that could reach its own scoring has been caught gaming it:
   - STOP turned off its own sandbox flag in 0.42% of runs despite a "DO NOT CHANGE" comment;
   - the Darwin Gödel Machine deleted the logging its checker relied on;
   - the AI Scientist tried to raise its own time limit.

   Details in `research/rsi/design-principles.md`.

## 2. Current decisions

Read [`CLAUDE.md`](CLAUDE.md) (also `AGENTS.md`) for commands, continuous operation, model selection, load/memory gates,
acceptance, and promotion. Both Claude and native-session paths follow that policy. sig has authorised automatic
upload and activation of accepted champions and capped unranked map sweeps, and the orchestrator pushes its own
commits on main; merging other branches and force pushes still need his go-ahead for the specific action. Team
details and organiser confirmations remain in [`CLAUDE.md`](CLAUDE.md).

## 3. What we know that matters

**About the game and the competition** (`research/unsw-game-analysis.md`):

- **Deadlines.** Sprint **1 Oct**, Qualifiers **10 Oct** (top 10 eligible go through), Grand Final **17 Oct** at
  UNSW. The final is seeded by **ladder rating on 16 Oct**, and seeds 1–6 skip the play-in match.
- **Tournament maps are unseen beforehand (confirmed by the organisers).** The bot has to win on maps it has never
  seen, and our testing has to run mostly on freshly generated maps.
- **The meta was a swarm (2026-09-23 replays).** By 2026-09-26 the leaders also out-breed us early, grow one long
  king late and flood sonar (`~/.bc-loop/briefing.md`, `research/leader-sonar/`).
  - Every top-8 team splits a length-2 child off every length-4 dragon from round 0–3, peaks at ~35–45 dragons,
    and trades heads (a head-on collision kills both).
  - Half of top games end in a wipeout.
  - The #1 team wins 80% of its games and dies less than the others. Top bots still lose 25–260 dragons a game to
    walls and their own bodies.
  - Nobody sprints, sonar is barely used, and round 500 is decided by a longest dragon of only 10–23.
- **C++ is compulsory in practice.** The judge meters CPU at 100M points per dragon per turn, and C++ does the
  same work ~60× cheaper than Python. 9 of the top 10 teams use C++.
- **Local games are exact and fast.**
  - The engine source is public, the local engine behaves exactly like the server's, builds are cached, and a
    repeat match takes 0.6 s.
  - **A game with the same seed plays out identically**, so repeating a pairing tells us nothing. Since
    2026-09-25 the engine draws a pearl seed per match; the referee derives one from the map file, so both sides
    and both bots share a pearl schedule. Test variety comes from fresh maps, both sides and varied opponents.
- **Everything is observable.** Every replay is public and contains its map, and a JSON API covers uploads,
  scrims (60 games/hour) and downloads.

**About winning this kind of contest** (the research folders):

- **Every 2023–2026 MIT Battlecode winner or runner-up had an automated new-vs-old match runner.** It played both
  sides of every map, and they only merged changes that beat the old version on a statistical test. The 2026
  winner ran 8 parallel workers and printed a p-value. (`research/mit-battlecode/transferable-lessons.md` #1.)
- **Winners studied replays and copied the strongest teams.** One 2020 team (4th that year) said it had never won
  with an original strategy in 6 years. Top teams spent "far more time reviewing scrims than writing code". Micro
  (fight decisions) was worth +30–50% win rate, macro +5%. (#5, #11, #12.)
- **Unguided LLM agents lose to good human bots.** In CodeClash (arenas include MIT Battlecode 2025 and
  Battlesnake), the best model:
  - never beat an expert human bot;
  - tested its own changes in only 56% of rounds;
  - made up loss causes in 46% of rounds;
  - left messy code behind.

  The harness has to force testing and ground every loss analysis in replay data. (`research/rsi/llm-game-bots.md`.)
- **What does work.** The closest precedent is FAMOU, which evolved LLM-written strategies for an adversarial
  capture-the-flag game and won the AAMAS 2026 round-robin. Its recipe:
  - screen each candidate cheaply, then confirm on many more games;
  - add each new champion to the opponent pool;
  - give the hardest opponent extra weight.

  Scores from 3 games barely predicted scores from 20–40 games (rank correlation 0.11). A fixed opponent pool
  overfits: ShinkaEvolve scored 0.78 on opponents it trained on and 0.34 on new ones.
  (`research/rsi/design-principles.md` #3–4.)
- **Statistics limit what a test can detect.** Smaller gains need more evidence. The research's game-count
  estimates are planning approximations, not promises for the referee's normalized-Elo test. Use the current
  bounds and pair cap in `AGENTS.md`, rather than the older early-loop +70 proposal. The ladder's own noise
  is about ±42 Elo, so it is a sanity check. (`research/snake-ai-and-eval/testing-methodology.md`.)
- **Agents can be steered.** A repo whose contributors match the 2026 MIT winning team used Claude with written
  rules in each bot folder's `CLAUDE.md`: new behaviour goes in new strategy classes, refactors must not change
  behaviour, and AI code gets cleaned up before commit. Our current rules distinguish pure refactors from
  deliberate strategy or implementation improvements; tests must evolve with the intended design.

## 4. Bot strategy (the starting point; the loop revises it)

**C++20**, one program, one process per dragon. It's built in layers so an experiment changes one thing at a time,
and every tunable number lives in `bot/params.hpp`, compiled in. This table is the 2026-09-23 starting design; kept
experiments (`~/.bc-loop/results.jsonl`) have changed it since, so read the champion's code (`loop/champion`) for
what the bot does now.

| Layer | Version 1 | Where the loop pushes next |
| --- | --- | --- |
| **Safety** | Never step into kelp, bodies or our own tail (the tail tile is still blocked on the turn it moves). A timing-aware flood fill: a body tile frees up after k tail moves, so never enter a pocket smaller than your length. Head-on only on purpose. | Make wall/self/body deaths near zero. |
| **Move choice** | Score every option (≤3 headings, optional sprint, optional split) on a feature record with an ordered tie-break. This is the most-copied micro pattern in MIT Battlecode. | A small **local search** that uses the sequential turn order: dragons with lower ids have already moved (facts), higher ids move after us (true replies). Only enemies that can reach us within the horizon are searched; the rest get a simple reflex policy. Extend the search when a head is within 2. |
| **Board evaluation** | Territory on the torus (multi-source BFS from all heads; ties go to nobody, since head-ons kill both), a parity-corrected area count, escape tiles, pearls in our territory, and length with diminishing returns. | Weights tuned by the numeric tuner, then fitted from replays. |
| **Economy** | The meta's split-at-4 swarm. Chase only pearls we reach first: nearest by (distance, id), which splits pearls among teammates with no messages. Remember seen pearl countdowns. Every idle dragon gets a default job. | Split size and timing scaled by map area and dragon counts, not fixed constants. |
| **Fights** | Trade heads by value: a length-2 dragon for a long enemy (especially their longest) is a win. Never offer a trade with our king. Our body across an enemy head's only exits is a free kill. | Fight only with local numbers. Sprint to escape or strike (nobody sprints today). |
| **Endgame** | From round ~350 (tunable), name a **king**, stop splitting it, and steer pearls to it. Keep enough dragons alive to avoid a wipeout. Once ahead on the round-500 tiebreak, switch to denial and safety. | When to switch; feeding the king (a dead feeder drops ⌈L/2⌉ pearls on its path). |
| **Unknown maps** | Learn walls and portals as we go, and detect which of the 3 symmetries the map uses, which tells us where the enemy started. | – |
| **Comms** | None. The meta wins without sonar. | Sonar with a keyed tag (round + map), rotated each upload, since every replay is public and fixed encodings get copied. Uses: outward-only relays, distance gradients, parent–child pings after a split. Since built: a team belief map gossiped over sonar, a king beacon for the final fold, and a per-build SipHash tag (v0023). |
| **Compute** | p99 under 50M points per turn; alert at 50% of budget. Never do heavy setup on a dragon's first turn, split children included. MIT teams lost as top seed to a first-turn overrun. | Spend spare budget on search. The Jump Trading award goes to the fastest finalist algorithm, so staying lean has value too. |
| **Known maps (ladder only)** | – | Recognise ladder maps and replay the fixed-seed pearl schedule (impossible since 2026-09-25: schedules are seeded per match), for ladder rating and seeding. Not built: every tournament map is unseen and seeding doesn't matter (sig, 2026-09-30; MISSES 102). |

## 5. The self-improvement system

### 5.1 Pieces

```
  public replays ──► scout: team profiles, meta shifts, map pool, clone targets
  our ladder games ─► loss analyst: death classifier → "why we lost" (cites turns) → idea backlog
                                   │
  idea backlog ◄── review (on a timer / low queue) ──► playbook, method.md; audits, post-mortems queue ideas
        │
        ▼
  two coding lanes: Claude driver or native session, own clone, bot/ + bot test cases
        │ commit
        ▼
  shared bot scenarios + protocol → REFEREE (separate checkout): CPU → SPRT (rotating opponents) → screen
        │ keep                               │ discard
        ▼                                     ▼
  local loop/champion + tag              results log + retained promising candidates
```

### 5.2 The referee (locked)

- **Maps.** Tournament maps are unseen, so **most acceptance games run on freshly generated maps**. A generator
  makes random maps shaped like the 11 known ones:
  - sizes 11–64 per side, square and 2:1;
  - all 3 symmetries (only rotation and left-right mirror seen so far);
  - kelp on 0–24% of edges, 0–12 portal pairs;
  - mostly slow pearl tiles plus small "farms" that spawn every round;
  - 1–4 dragons a side, length 3–6.

  It also makes deliberately odd maps (10×10, 64×64, kelp mazes, portal-heavy, sparse pearls), because MIT finals
  were lost on odd maps. The known server maps are in the pool too, and a held-back set is never shown to agents.
  As built (2026-09-26): acceptance maps are `mapgen/gen.py --style eval`: a third perturbed variants of the 9
  ladder maps and the Sprint-style maps (Dilemma, Autarky), a ninth community-map variants, a ninth trap maps, a
  ninth deadend maps, a ninth fortress maps and a ninth macro maps (all from 2026-09-27), and the rest random `mix` maps (the shares: `mapgen/gen.py`'s `NEAR_EVERY`
  and `*_REMAINDER`). The ladder maps are a 90-game report per result, not part of the
  test. No held-back set is made: the one `--setup` used to generate was never played and was removed on 2026-09-26.
- **Games.** Each pairing is played on a (map, seed) twice with sides swapped, scored as a pair; the seed is
  derived from the map file. That cancels
  map luck and side luck: team A always moves first, and side B won 61% of our replay sample (confounded, see the
  game analysis).
- **Cascade.** Cheap failures are thrown out in seconds:
  1. fast C++ scenarios across all bot decision modules and real two-turn protocol checks, using the shared
     `loop/bot_tests.py` runner against candidate sources and its editable test suite;
  2. the lane's quick screen (currently 20 games against the champion, plus a CPU check);
  3. after commit, the shared evaluator repeats the bot tests before CPU/games; failure blocks acceptance;
  4. CPU check in the judge's sandbox: over 50M p99, over 90M maximum, or any crash/timeout fails;
  5. paired SPRT with rotating opponents, then the opponent screen for every candidate it keeps (from 2026-09-27 not
     one it leaves `promising`; a kept `identical` idea takes the screen without an SPRT; a `speed` idea takes
     none). Fast tests do not prove playing strength.
- **Acceptance.** Both entry paths call the shared evaluator. The current paired-SPRT bounds, pair cap, and
  safety gates are defined in [`CLAUDE.md`](CLAUDE.md#acceptance-and-follow-ups). Declare them before the
  experiment and do not move them mid-test. Existing tests retain their original settings.

  H1 is required for promotion (or, since 2026-09-25, at least a 90% chance the gain is real when the test ends
  without H1 above a 50% score, at the pair cap or at H0); a score above 50% is insufficient. CPU-safe positive H0 or
  inconclusive candidates are retained as `promising`, with their ideas marked `followup`. Retest a promising change
  alone on the current champion with a predeclared gate and fresh maps (sig, 2026-09-25: combining hides which part
  helped); do not keep retrying unchanged deterministic games. E0024 tested the E0020+E0021 combination at [0,35] on
  fresh generated maps: H0 after 258 pairs, 49.61% score; no promotion and no opponent screen because it lacked a
  positive estimate. That result does not rule out smaller effects; it does not establish a benefit either.

  The candidate must also not lose ground against the **pool**:
  - past champions;
  - the toolkit's example bots;
  - archetype bots (rusher, turtle, head-on hunter, a stock split-at-4 swarm);
  - **clones** of top ladder teams.

  The weakest matchup gets double weight ("weakness pressure"). Each new champion joins the pool. Every few
  promotions, the champion is re-tested against older champions and the held-back maps, because accepted gains
  are biased upward.

  As built (2026-09-26): the SPRT's trials rotate the champion with frozen variants of older champions
  (`driver.ROTATION`; `rammer` gave way to `midking` and `reverser` on 2026-09-28), and a kept candidate then plays the opponent screen in
  `referee/gauntlet.toml` (two old champions and variants that swarm, ram, trade down, fold, grow, hunt, replay our
  sonar, out-breed or fold early), plus a late screen for entries added after its claim. Clones of top ladder teams,
  weakness-pressure weights and periodic re-tests on held-back maps were not built, and the toolkit example bots
  (once in referee/pool) were dropped; reviews re-check one older accepted behaviour each time instead.
- **Locked for real.** The referee lives in a separate checkout. Agents run with Claude Code's OS-level sandbox
  (write access to their worktree only), and the harness rejects any candidate whose diff touches anything
  outside `bot/` and `tests/bot/`. Tests and fixtures may be revised, replaced or deleted when strategy OR
  implementation improves; old expectations are not secretly re-run. The runner and game referee remain
  outside experiment scope. Claude Code's own docs say Bash deny rules are "not a security boundary", so they aren't relied
  on.

### 5.3 Inner loop: one lane, one idea, repeat

The live backlog is `~/.bc-loop/ideas.jsonl`, seeded from `loop/ideas.jsonl`. Each lane claims an idea, reads
the playbook and evidence, implements one idea at its natural scope in `bot/` with focused test cases, runs
bot tests and the quick check, gets one fresh-context review of its diff, and commits. The referee
records the result in `~/.bc-loop/results.jsonl` and promotes passing candidates locally. Then the lane claims
another idea. `bot/CLAUDE.md` defines the bot editing rules, including constants in `params.hpp`.

The Claude driver and native-session commands, stop markers, and continuation rules are documented once in
[`CLAUDE.md`](CLAUDE.md#run-continuously). The native path is driven by the active session, not a detached
Codex CLI daemon. Numeric tuning has since been built (`tune` ideas: SPSA over `bot/params.hpp`, `loop/tune.py`),
as has a sweep of every switch of each new champion (`loop/sweep.py`); multiple lineages have not.

### 5.4 Outer loop: improving the improver

Every `meta.META_EVERY_S`, or when fewer than three ideas are waiting, a review session reads the
stats (acceptance rate and Elo gained per idea category, cost per accepted change, failure patterns) and may:

- add one-line "signs" to `PLAYBOOK.md` and prune stale ones;
- edit the lane prompt;
- re-rank the backlog;
- write new **tools** that later lanes can use (a replay-diff viewer, a scenario-test extractor, a profiler).

It can *propose* new opponents or maps, but a separate step adds them and re-baselines. Prompt and playbook
versions are A/B tested (lanes alternate), scored by accepted Elo per lane-hour and per dollar. Losers are
reverted, and every version is archived so a better old one can come back. The referee is never in scope.

As built (2026-09-26), a review edits only the playbook, the backlog and `method.md` (`loop/meta.py`); the lane
prompt and tools are changed by the orchestrator, which also adds screen opponents and map styles itself (sig,
2026-09-25). The prompt A/B test was not built.

### 5.5 Data loops

- **Scout (hourly).** Pull new top-team replays (≤1 request/s). Update team profiles: split timing, peak dragon
  count, death mix, head-on rate, longest dragon. Update the map pool. Flag meta shifts into the backlog. Built as
  the hourly public scout (`scout.md`) and the leader study (`study.md`: the top five teams' ranked games next to
  ours), which reviews read.
- **Loss analyst.** Turns our lost games into a structured death classifier: head-on, body, kelp, self; who moved
  first; length at death. It finds the turning points, and each backlog idea **cites specific turns**
  (CodeClash's agents made up loss causes). Recurring blunders become **scenario tests** ("round 154, dragon 7
  must not go north"). The hand-written `tests/bot/` suite runs before evaluation games now; automated
  replay-to-scenario extraction remains future work. What was built instead (as of 2026-09-26): `evidence.md`
  (the champion's record and death causes against screen opponents, and its lost games there), a replay audit
  every `audit.AUDIT_EVERY_S` (`loop/audit.py`), and a post-mortem by the coder of every result not kept.
- **Clone builder.** Writes C++ approximations of top teams' styles for the pool. A clone is scored on how closely
  its replay statistics match the real team's. Not built (as of 2026-09-26): the screen's opponents are variants of
  our own champion set to play like styles seen online.

### 5.6 Ladder work

sig has since authorised automatic upload and activation of accepted champions and controlled unranked scrims
(CLAUDE.md, "Automatic upkeep and online matches"), so this is no longer "only after specific approval".

- Each new champion is packaged from the `loop/champion` commit, uploaded (within the site's 12 an hour, the only
  limit) and verified against that source once active. Only accepted champions are uploaded, so no test version is ever left active.
- Autoscrims draw every 2 hours on the UTC boundary using whatever is active.
- Scripted unranked **map sweeps** (sig, 2026-09-24): one game on every active map against one opponent, the dev
  benchmark first for each version, then the previous version's opponents on the same maps, capped at 50 loop games per rolling hour. The 2024–25
  MIT winners scrimmed ~2.5× more than the median. The replays come back into the loop.
- Planned: watch our rating and write a status page for you, and the day before each tournament a freeze report
  for you to pick from. (As of 2026-09-26 neither is built; the Sprint freeze report is a dated reminder for 29 Sep,
  not automated.)

### 5.7 Guardrails

| Risk | Guard |
| --- | --- |
| Agent games the score | Referee in a separate checkout; OS-level sandbox; diffs restricted to `bot/` and `tests/bot/`; independent CPU/SPRT gates remain even when cases change; the harness alone measures and logs. |
| Agent skips testing or invents causes (CodeClash) | The harness always tests. Loss analysis comes from the structured classifier and must cite turns. |
| Overfitting to ourselves or to known maps | Fresh generated maps, an SPRT that rotates opponents, an opponent screen of frozen styles, per-map-style reports. (Held-back maps, weakness pressure and clones of real teams were planned but not built, as of 2026-09-26.) The ladder is a sanity check. |
| Noise mistaken for progress | Paired SPRT sized to what's detectable; no "57% over 30 games". |
| Messy code (CodeClash) | `bot/CLAUDE.md` rules, simplicity counts as a win, scheduled clean-up passes through the non-regression test. |
| Engine or rule changes (13 toolkit releases, 21–23 Sep) | Each experiment records its toolkit version. An hourly check spots releases and doc changes; upgrades apply at an idle boundary after a compatibility pair, and results are never pooled across versions. |
| CPU overruns or crashes in the judge | Sandbox CPU check on every candidate; 50% headroom; first-turn setup watched separately. |
| Runaway cost | Subscription access only, never API billing. (No per-task time limit: coding runs until done, per CLAUDE.md.) |
| Interrupted work | Claude's driver manages its tasks; native-session work persists claims and outcomes through `loop/native.py`. Resume from the recorded state. |
| Rules and etiquette | API limits respected (120 requests/min, 12 uploads/h, 60 games/h; the loop caps itself at 50 games per rolling hour (its own 12 uploads a day went on 2026-09-27)), scraping ≤1 request/s. Platform bugs get reported (there's a bug bounty), never exploited. |
| Stopping it | `~/.bc-loop/STOP` stops both paths; leave it in place until resuming is requested. |

### 5.8 Build it ourselves, or use CORAL?

**Decided: our own driver** (`loop/driver.py`, a few hundred lines).
[CORAL](https://github.com/Human-Agent-Society/CORAL) solves the orchestration, but its agents are long-running
sessions steered by heartbeat prompts, while the research favours one fresh session per idea. Its scoring is also
absolute per commit, where ours is a noisy head-to-head test against the current champion. The referee, map
generator, replay tools and ladder job would have been ours either way.

## 6. Timeline

| Dates | Goal | Done when |
| --- | --- | --- |
| **Sep 23** | Research and this plan | You've answered §2 (done) |
| **Sep 24** | **Phase 0** (§7) | One lane runs end to end; bot v1 is on the ladder (done: first upload 23 Sep) |
| Sep 25–29 | Loop runs continuously (2 coding lanes); scout, clones, scenario tests online | Champion clearly beats v1 on generated maps; ladder rank rising (running; no clones, as of 26 Sep) |
| **Sep 30** | Sprint freeze: you pick from the freeze report (pool, held-back maps, top-10 scrims) | Chosen version active 12 h before the Sprint |
| **Oct 1** | Sprint. Harvest every Sprint replay: new maps, and how teams play fresh maps | – |
| Oct 2–8 | Local search, king endgame, sonar, counters to the meta | Gains hold on held-back maps |
| **Oct 9** | Qualifier freeze | – |
| **Oct 10** | Qualifiers | – |
| Oct 11–15 | "One or two impactful improvements, done very well" (MIT advice for this stretch); push ladder Elo | – |
| **Oct 16** | Final freeze; this rating sets Grand Final seeding | – |
| **Oct 17** | Grand Final at UNSW | – |

## 7. Phase 0 (in order, ~1 day)

1. **Layout**: `bot/` (C++ from `unswbc init cpp`, plus `bot/CLAUDE.md`), `referee/` (separate checkout), `loop/`,
   `data/` (maps, replays; replays git-ignored).
2. **Referee v0**:
   - runner calling `unswbc run … --no-replay` in parallel behind a load check;
   - pairs with sides swapped;
   - SPRT;
   - sandbox CPU check;
   - results log.
   Measure real games/hour with a heavy bot before trusting any throughput numbers.
3. **Map generator** from the spec in §5.2, and a held-back split.
4. **Replay tools**: fetcher, pycapnp parser, team profiler, death classifier. The first three were prototyped for
   this research.
5. **Pool v0**: toolkit examples (`dummy`, `suicidal`, `murder`), starter bots, a stock split-at-4 swarm, a rusher,
   a turtle. (The toolkit examples and starters were in referee/pool until 2026-09-26; acceptance stopped playing
   them and the rotation and `referee/gauntlet.toml` replaced them.)
6. **Bot v1**: the §4 version-1 column. Goals: beat the pool, and never lose a dragon to a wall or itself.
7. **Lane driver**: our own driver, with the native-session entry path sharing its evaluator. Tuner and scout
   extensions follow measured need (both since built); outward ladder actions require specific approval (since
   replaced by sig's standing authorisation, §5.6).
8. **First upload** of v1 (with your approval) to start collecting real results.

## 8. How we'll know it's working

- **Referee Elo** of the champion against a frozen anchor (bot v1) on generated and held-back maps, rising daily.
  (Not tracked as of 2026-09-26: each result's Elo is against the champion of its day, and the dev benchmark's
  online games give the steadier curve.)
- **Ladder** rating and rank; the 16 Oct rating sets Grand Final seeding.
- **Death mix**: wall and self deaths per game heading to zero.
- **Loop health**: acceptance rate, accepted Elo per lane-hour and per dollar, experiments per day. If these fall,
  fixing them is the meta session's job.
- **Honesty**: every experiment, failures included, is in the results log. A planned status page would say what's
  running, what was accepted, and what's broken. (Not built as of 2026-09-26: `loop/health.py` writes alerts for
  the orchestrator, and reviews get a `stats.md` of every result.)
