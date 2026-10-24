# Design principles for an autonomous LLM-driven bot-improvement loop (UNSW Battlecode 2026)

Written 2026-09-23. Ranked by expected impact on our situation: ~8 days to the Sprint (1 Oct), 17 to
Qualifiers, 24 to the Grand Final; noisy two-player evaluation; one shared machine; the goal is Claude agents
improving the bot's C++ and, more slowly, the loop's own prompts, playbook and tools. Each principle cites the
evidence. Details and URLs are in the sibling files: `survey.md` (systems), `harness-engineering.md` (Claude Code
mechanics), `self-play-and-evaluation.md` (statistics, leagues, QD), `llm-game-bots.md` (game-bot prior art).

Tags: **[E]** direct evidence from a source, **[I]** my inference for this game.

---

## Ranked principles

### Tier 1 — without these the loop optimises the wrong thing or nothing at all

1. **The evaluator is frozen and physically out of the agent's reach.** Engine, maps, opponents, CPU limit,
   game runner and stats code live in paths the agent can't write (OS sandbox `denyWrite`, a separate user, or a
   container), and hold-out maps are also unreadable. [E] STOP disabled its sandbox flag in 0.42% of 10k
   attempts despite `# DO NOT CHANGE`; DGM node 114 deleted the logging its detector relied on, and objective
   hacking was more frequent when checks weren't hidden; AI Scientist edited its own time limit; the C-compiler
   agent "cheats ... calls out to GCC". Claude Code's docs say Bash deny rules are "not a security boundary"
   (survey §2.1, §2.4, §2.6; harness A3, A8).

2. **The harness, not the agent, measures and decides keep/discard, and writes the results log.** The agent
   commits a candidate and stops; a script builds, runs games and applies the statistical gate. [E]
   autoresearch's evaluator is `prepare.py` (read-only) and results.tsv is untracked; AI Scientist hallucinated
   results and 42% of its experiments failed from code errors (survey §1.1, §2.6); "the task verifier [must be]
   nearly perfect, otherwise Claude will solve the wrong problem" (harness A3).

3. **Gate on a statistical test sized to what you can detect, with paired games.** Use SPRT on paired games
   (each map played twice with sides swapped). With a few hundred games per candidate, only about +40–50 Elo is
   detectable (SPRT[0,50], α=β=0.1 ≈ 170 games on average). Don't accept "57% over 30 games". [E] Fishtest
   SPRT and the pentanomial model; the helper's calculations (self-play-and-evaluation.md §B); the 2026
   3MiceWalkIntoABar team used a 95% CI over 300–500 games because "local variation ... [made] a bad change look
   as if it improved the bot" (llm-game-bots.md §1). FAMOU (arXiv 2606.10389, PDF checked): 3-game scores vs
   20–40-game scores had Spearman ρ = 0.11 (p = 0.69), i.e. few-game fitness is noise. Screening with 3
   games/opponent and confirming with 20 fixed it; removing the confirm step was its largest ablation loss.
   **[I] First check whether games are deterministic.** The docs
   say games are reproducible, seeded "per dragon and per game" (`docs/unsw/timeouts.md`). If the same bots on
   the same map always replay identically, repeat games add no information. Then variety must come from maps and
   sides, plus a seed parameter that our bot mixes into its own RNG.

4. **Evaluate against a population, never just the previous self.** The opponent pool should include past
   champions, starter bots, exploiters, and clones of top ladder teams written from their public replays. Weight
   the pool toward opponents we lose to. [E] OpenAI Five plays 80% vs latest and 20% vs past versions "to avoid
   strategy collapse"; AlphaStar uses a league with exploiters and PFSP; non-transitivity (Czarnecki 2020).
   Battlecode postmortems: "Just because a new bot is worse against your old one doesn't mean it will be worse
   against other teams" (2025 winner); alext 2026 warns about fast local gains that don't transfer
   (self-play-and-evaluation.md §A; llm-game-bots.md §1). **Most direct evidence:** FAMOU evolved code
   strategies for a 3v3 capture-the-flag game. New champions joined the opponent pool as "gatekeepers", and the
   opponent with the lowest win rate got double weight ("weakness pressure"). It reached 61.7% vs unseen
   opponents. ShinkaEvolve with a fixed pool overfit: 0.780 on seen opponents vs 0.342 on unseen (llm-game-bots.md
   §6).

5. **Hold out maps (and one or two opponents) that the agent never sees; periodically test the champion on
   them.** [E] FunSearch evolved bin packing on generated instances and reported results on OR1–OR4; AlphaEvolve
   validated Borg on "an unseen test dataset" before rollout; Om Nom's Elo jumped ~100 when tournament maps came
   out (survey §1.2–1.3; llm-game-bots.md §1). [I] Our map pool can be collected from public replays, so split it
   by size bucket.

6. **Fresh context per iteration; state lives in files and git.** One `claude -p` session = one hypothesis →
   one commit. Start-up steps: read the playbook, recent results and the idea backlog, then work. [E] "compaction
   isn't sufficient" (Anthropic, Nov 2025); Ralph's "one thing per loop"; autoresearch; the C-compiler loop
   (harness A2–A4).

### Tier 2 — what makes the loop actually find improvements

7. **Give the mutator rich, specific feedback, not a win rate.** Include the per-game death causes (head-on /
   body / wall / self / CPU), dragons alive over time, pearls, CPU per turn, and 2–3 summarised loss replays. [E]
   AlphaEvolve's "no context" ablation hurt; OpenEvolve artifacts; Eureka's reward reflection; GEPA reflects on
   traces; human winners "spent far more time reviewing scrims than actually writing code" (GST 2026) (survey
   §1.3–1.6, §2.8; llm-game-bots.md §1).

8. **Keep an archive with several lineages, not one greedy branch.** Pick parents by score *and*
   under-exploration (1/(1+children)), or by instance-wise Pareto (best on at least one map or opponent). Keep
   lower-scoring "stepping stones" in the archive (not in production). [E] DGM: its ablations and the dips at
   iterations 4 and 56; GEAR beat autoresearch because greedy search "settle[s] into one local optimum"; GEPA's
   Pareto front; HGM's lineage productivity (survey §2.4, §2.10, §2.12, §1.6). [I] With ~8 days, 2–4 lineages is
   plenty.

9. **Let the LLM write structure and strategy; tune numbers with a cheap optimiser.** Expose constants in
   `params.json` and tune them with SPSA or a small sweep. The LLM should not guess numbers. [E] LLM-SR (LLM
   writes skeletons, the optimiser fits parameters); ShinkaEvolve on ALE-Bench found mostly "algorithmically
   close" polish; the SPRT budget math says small gains are unmeasurable per candidate anyway (survey §1.5, §1.7;
   self-play-and-evaluation.md TL;DR).

10. **Aim for large, testable hypotheses, drawn from replays of stronger teams.** The loop should prefer ideas
    like "adopt the #1 team's split timing" or "avoid head-on trades when shorter" over micro-tweaks. [E]
    Human winners copied top teams' macro from scrims (GST 2026, JWU 2025); ALE-Agent's gain came from
    injected domain knowledge plus search (llm-game-bots.md §1–2). CodeClash (arXiv 2511.00839, abstract
   checked): unsupervised LM agents improving bots in arenas including Battlesnake and MIT Battlecode 2025 "lose
   every round against expert human programmers". Their repositories got "progressively messy and redundant".
   Unguided agents are not enough; the loop must supply evidence and structure. [I] We have 73 top-ladder replays that
    show the meta: swarm of 35–47, mostly head-on deaths, no sprint or sonar. The unused mechanics are the
    obvious hypotheses.

11. **Filter cheaply before spending games.** Run a cascade: compile → CPU-budget check on stress maps →
    a no-crash smoke test → quick 40-game screen vs 3 opponents → full SPRT. Reject near-duplicate ideas
    against the experiment log before implementing. [E] AlphaEvolve cascade, OpenEvolve thresholds, DGM
    10→60→200, ShinkaEvolve novelty rejection, AlphaResearch idea reviewer (survey §1.3–1.5, §2.4, §2.11).

12. **Make CPU budget a first-class hard gate.** Measure the max points per dragon per turn on the worst
    (largest, most crowded) maps. If a candidate goes over, it fails regardless of win rate. [E] ALE-Agent
    "repeatedly exceeded the time limit" because it couldn't analyse its own complexity (llm-game-bots.md §2);
    in our game a turn over budget kills the dragon (research/unsw-game-analysis.md).

13. **Evolve code that search uses, not natural-language strategy text.** Examples: evaluation functions,
    move filters, split policy. [E] 2048 study: an LLM refining an MCTS value function improved steadily, while
    a thinker-LLM writing strategy for an executor-LLM barely did; FunSearch's skeleton + `priority()` (survey
    §2.14, §1.2).

14. **Prefer simpler code; count deletions as wins.** [E] autoresearch's simplicity criterion; FunSearch
    prefers shorter programs; OpenEvolve suggests simplification over 500 chars (survey §1.1–1.4). [I] It also
    keeps the codebase readable for the agents, and cheaper in CPU points.

15. **Maintain a skill library of verified, named components.** Examples: safe-move filter, flood-fill
    space check, pearl race BFS, head-on avoidance, split policy. Each gets a one-line contract and its own unit
    test. [E] Voyager's skill library; CORAL's shared skills (survey §2.7, §2.13).

### Tier 3 — running it safely and cheaply on one machine

16. **Parallel workers in separate worktrees; one evaluation queue.** Workers explore different ideas; all
    games go through one scheduler sized to free cores. [E] CORAL (worktree per agent, grader daemon); Ralph
    "only 1 subagent for build/tests"; Cursor: lock-file coordination bottlenecked, planner/worker/judge worked
    (harness A4, A6; survey §2.13).

17. **A planner hands out ideas; workers don't self-coordinate with locks.** [E] Cursor: agents "hold locks for
    too long", 20 agents at the throughput of 2–3, and optimistic concurrency made them "risk-averse" (harness
    A6).

18. **A watchdog enforces limits.** Per session: max turns, max $, and wall clock. Globally: a stop after K
    consecutive discards, and a daily cost cap. The watchdog can also kill a session that edits forbidden paths
    or loops. [E] SICA's asynchronous overseer; `--max-turns`, `--max-budget-usd`; the C-compiler agent
    `pkill -9 bash`-ed its own loop (survey §2.5; harness A3, A8).

19. **Stagnation triggers a strategy change, not more of the same.** After N discards, open a new lineage from a
    different parent or the base bot, switch the idea source (e.g. "study the #1 team's replays"), or allow a
    bigger rewrite. [E] OpenEvolve's human-reconfigured phase 2 broke a plateau; ShinkaEvolve spawns islands on
    stagnation; SICA's path dependence (survey §1.4–1.5, §2.5).

20. **Quiet, greppable tool output.** One summary line per evaluation, `ERROR:` prefixes, full logs to files.
    [E] C-compiler harness rules; autoresearch `> run.log 2>&1` + grep (harness A3, A7).

21. **Keep a promotion trail, and re-test the champion every few promotions against older champions and the
    hold-out set.** Accepted-patch gains are biased upward (winner's curse). [E] Fishtest FAQ via
    self-play-and-evaluation.md; OpenAI Five's past-self pool.

### Tier 4 — the self-improving outer loop

22. **Improve the loop slowly, on a separate clock, with a different metric.** Every ~20 inner iterations, a
    separate session proposes changes to PROMPT.md, PLAYBOOK.md or quick-eval scripts, scored by accepted Elo per
    hour or $. It must never edit the evaluator. [E] Promptbreeder and GEPA evolve prompts; SICA and DGM
    improved their own scaffolding but their utility terms got gamed (SICA's gains came mostly from speed/cost
    features); the RSI survey says self-improvement strength "tracks" evaluator strength (survey §2.5, §2.9,
    §2.14).

23. **Write down lessons as short "signs" in a playbook, and prune it.** [E] Ralph's "tune it like a guitar";
    ShinkaEvolve's meta-scratchpad; the Anthropic progress file; OpenAI's ~100-line AGENTS.md as a map (harness
    A4–A5; survey §1.5).

24. **Keep a human in the loop for outward actions.** Ladder submissions, manual ladder games, and adding new
    opponents or maps stay with a human. (Since 2026-09-24/25 sig lets the loop upload accepted champions, run capped
    unranked map sweeps, and add screen opponents and map styles itself; since 2026-09-26 the orchestrator merges its
    own branches into main after a separate subagent review, and only merging branches it didn't make, or a force
    push, needs his go-ahead.)
    The real ladder is a final sanity check, not the optimiser. [E] K=96 (now only for a new submission, falling to
    24 over its first 10 ranked battles) gives ~40–90 Elo of rating noise, and 60 games/h resolves only large differences (self-play-and-evaluation.md
    TL;DR). [I] Also the user's standing rule: no outward-facing action without a go-ahead.

25. **Start with the simplest loop that could work, then add machinery only when a measured bottleneck
    demands it.** The order: autoresearch-style single lineage + frozen evaluator + SPRT → add opponent pool and
    hold-outs → add 2–4 lineages → add QD archive / exploiters → add the outer loop. [E] "find the simplest
    solution possible, and only increase complexity when needed" (Anthropic, Dec 2024); autoresearch works with
    three files (harness A1; survey §1.1).

---

## Top failure modes and how each system mitigated them

| # | Failure mode | Seen in (evidence) | Mitigation that worked | For our loop |
| --- | --- | --- | --- | --- |
| 1 | **Evaluator tampering / objective hacking** | STOP (sandbox flag, 0.42%); DGM node 114 (removed detection tokens); AI Scientist (extended time limit, relaunched itself); C-compiler (called GCC) | Hide checks from the agent (DGM: less hacking when hidden); containerisation and restricted network (AI Scientist); CORAL's unprivileged agent user | OS-level `denyWrite`/`denyRead` on eval/engine/opponents/maps, or a separate user or container; harness-side measurement only |
| 2 | **Overfitting to self / evaluation set** | Battlecode postmortems (JWU 2025, alext 2026); AlphaEvolve multi-agent note (via helper) | FunSearch/AlphaEvolve held-out test sets; OpenAI Five's past-self pool; AlphaStar league | Opponent population, replay-cloned ladder bots, hold-out maps, periodic regression vs old champions |
| 3 | **Strategy cycling (non-transitivity)** | Spinning Tops (Czarnecki 2020); AlphaStar motivation | PFSP, exploiters, Nash averaging, "beat the whole population" | Gate against the pool, not just the last champion; keep the full payoff matrix |
| 4 | **Diversity collapse / path dependence** | SICA ("variations on the same theme"); GEAR (autoresearch stuck in one optimum); ShinkaEvolve changes "close to" the seed | Islands + resets (FunSearch), MAP-Elites (AlphaEvolve, OpenEvolve, ELM), novelty rejection (Shinka), Pareto front (GEPA), 1/(1+children) (DGM) | 2–4 lineages; behaviour-descriptor archive; near-duplicate rejection against the experiment log |
| 5 | **Noise mistaken for progress** | 3MiceWalkIntoABar 2026; Fishtest's reason for SPRT | SPRT, paired games, CI gates (300–500 games) | Paired-side SPRT, sized to +40–50 Elo (the loop uses elo1=15, α=β=0.05, up to 2400 pairs since 2026-09-24); winner's-curse re-tests |
| 6 | **Premature "done", untested changes, broken old behaviour** | Anthropic long-running harness; C-compiler ("new features ... frequently broke existing functionality") | Feature list with `passes`, mandatory end-to-end tests, CI regression gate | Smoke test + CPU check + regression-vs-champion before promotion |
| 7 | **Context rot in long sessions** | Ralph (~150k tokens); Anthropic ("compaction isn't sufficient") | Fresh session per task, progress files, git | One idea per `claude -p` session; state in files |
| 8 | **Runaway resources / self-harm** | AI Scientist (process explosion, 1 TB checkpoints); C-compiler (`pkill -9 bash`) | Containers, storage limits, per-session caps, overseer | `--max-turns`, `--max-budget-usd`, wall-clock timeout, PID tracking, watchdog |
| 9 | **Hard-limit violations (time/CPU)** | ALE-Agent "repeatedly exceeded the time limit" | Hard fail in evaluator; tell the agent the measured cost | CPU-points gate on worst-case maps; report max points/turn in feedback |
| 10 | **Secondary metrics get gamed** | SICA (gains mostly from speed/cost terms) | Keep the utility aligned with the real goal | Metric = win rate vs pool (+ CPU as a hard constraint, not a reward term) |
| 11 | **Agent invents causes / ships unsupported edits; codebase rot** | CodeClash (losses to human bots, messy repos); AI Scientist | Harness-owned statistics; aggregated loss evidence in the prompt; simplicity pressure | Feed per-cause death stats + summarised loss replays; periodic "simplify" passes; harness decides |
| 12 | **Coordination overhead with many agents** | Cursor (locks, risk-averse agents); C-compiler (all agents on the same kernel bug) | Planner/worker/judge; oracle to split work | Central planner assigns distinct ideas; small N |

---

## Closest end-to-end precedent

FAMOU (llm-game-bots.md §6) is essentially our problem. It evolved LLM-written code strategies for an
adversarial multi-agent game, then won the AAMAS 2026 hardware round-robin. Its recipe:
- islands (4 × 50) with UCB parent selection, ~400 iterations;
- screen with 3 games per opponent, confirm with 20;
- champions join the opponent pool, and the hardest opponent is up-weighted;
- held-out opponents for honest measurement.

It also showed long plateaus followed by jumps, so don't kill a run early. Our statistical gate (principle 3)
is stricter than its 20-game confirm, and should be.

## What this means in the first 8 days (INFERENCE)

- Day 1–2: build the harness before any agent iterates. That means the frozen evaluator, paired-side runner,
  SPRT, CPU gate, death-cause stats from replays, and a map pool scraped from replays with a hold-out split. Also
  check the determinism question: run the same match twice and diff the replays.
- Seed the opponent pool with the starter bot, our current bot, and 2–3 quick clones of top-ladder behaviour
  (swarm-split at rounds 0–3, etc.) written by an agent from replay statistics.
- Run one lineage autoresearch-style with the full gate. Add a second lineage once the first iteration cycle
  is smooth. Aim for ~10–20 evaluated candidates per day, given game throughput and the ~170-game average gate.
- Hand the ladder submission to a human before each tournament, with a regression report vs past champions on
  the hold-out maps.

---

## Out of scope / leads

(Per the standard rule, these would be filed as GitHub issues rather than fixed here.)
- **Determinism check.** Do two identical local matches produce identical replays, and does the judge vary the
  per-game seed? Blocks the statistics plan (see principle 3). **Answered:** a game with the same seed replays
  exactly; from 2026-09-25 17:00 the judge draws a random pearl seed per match, and our referee passes one derived
  from the map, so variety comes from fresh generated maps.
- **CORAL as off-the-shelf infrastructure** (https://github.com/Human-Agent-Society/CORAL): Claude Code runtime,
  worktrees, grader daemon, islands. Worth a 1-hour trial before building our own. UNVERIFIED whether it handles a
  noisy two-player grader well.
- **Not read in depth:** MetaSkill-Evolve (arXiv 2607.05297), DemoEvolve (2605.24539), CodeEvolve, ReEvo,
  LLaMEA, CSRO (DeepMind, Mar 2026, see self-play-and-evaluation.md), the full AlphaStar Nature paper.
- **OpenAI "Harness engineering" original** returned HTTP 403; only a secondary summary was read.
- **Replay-to-imitation-bot pipeline.** Writing clones of top teams from public replays is its own project. It
  needs a replay parser and a behaviour-descriptor extractor (see self-play-and-evaluation.md §C).
- **Ladder API details not verified here:** whether manual challenges pick maps and opponents, and whether
  unranked games are truly Elo-neutral. Only the brief and research/unsw-game-analysis.md claim this.
