# Self-play, league training, evaluation statistics, and quality-diversity for an LLM-improved Battlecode bot

Scope: items 3 and 6 of the RSI research task. Target game: UNSW Battlecode 2026 (torus snake game, up to 64 dragons/team, 500 rounds, ladder Elo per 5-game battle, K=96 for a new submission falling to 24 over its first 10 ranked battles as of 2026-09-26, 60 manual games/hour).
Legend: **VERIFIED** = I read the primary source this session. **INFERENCE** = my reasoning. **UNVERIFIED** = could not confirm.

## TL;DR
- **Budget reality (my SPRT calcs, B4):** with hundreds of games per candidate we can only detect gains of about **+40-50 Elo or more** (56-57% win rate). SPRT[0,50] at α=β=0.1 averages ~170 games (worst case ~240); a +20 Elo gain needs ~1,000-2,600 games; +5 needs ~25k. So push the LLM toward big strategic changes and leave small numeric tuning to a cheap separate tuner.
- **Use Fishtest-style SPRT, but scaled to our budget.** Fishtest: α=β=0.05, normalized-Elo bounds [0,2] STC, [0.5,2.5] LTC, [-1.75,0.25] non-regression; games in pairs scored with the pentanomial model; the SPRT code is `stats/sprt.py` (VERIFIED from source). For us: SPRT[0,50] (α=β=0.1) as the main gate, [0,100] early, and [-30,0] non-regression for simplifications. (Replaced 2026-09-24: the loop uses [0,15] normalized Elo, α=β=0.05, up to 2400 pairs, and since 2026-09-26 [-15,0] for no-loss changes; with more game machines the budget is far larger than assumed here.)
- **Always play each map twice with sides swapped** (the pentanomial idea). In my simulation this saves 17-54% of games when map/side bias is moderate to large. **First check whether games are deterministic**: if they are, repeated games add no information until per-game seeds are added.
- **Don't gate only against the last champion.** Non-transitivity (Czarnecki 2020) causes cycling. OpenAI Five: 80% vs. latest, 20% vs. a past-self pool weighted by softmax quality scores. AlphaStar: main agent plus main and league exploiters that reset to a base policy, with prioritized fictitious self-play (PFSP) weighting f=(1−x)^p. Czarnecki's protocol: a new member must beat the *whole population* on average.
- **The closest prior art to our loop is Code-Space Response Oracles (CSRO; DeepMind, Mar 2026) / LLM-PSRO.** In it, the LLM writes a best-response *program* against the Nash mixture of saved programs, refined in an inner loop (LinearRefinement or AlphaEvolve). Showing the LLM **natural-language opponent summaries beat showing raw opponent code** (63.5 vs −54.3 aggregate score in repeated RPS).
- **Score candidates by Nash averaging** (win rate vs. the max-entropy Nash mixture of the pool), not a uniform average. Our pool will be full of near-duplicate LLM versions, and Nash averaging is immune to that redundancy. Keep the full payoff matrix; one Elo number hides cycles.
- **Opponent pool (~10-16 bots):** current champion, 3-5 past champions, 2-3 main exploiters started from a *simple base bot*, 1-2 league exploiters, 3-5 **imitation bots of top ladder teams written from their public replays**, and 1-2 starter-bot anchors.
- **Guard against overfitting:** train/held-out map split stratified by size, held-out results never shown to the LLM, a periodic 1,000-game regression test vs. the champion from 10 promotions ago (accepted-patch Elo estimates are biased upward, per the Fishtest FAQ), and periodic "simplify the champion" passes (the LLM tends to over-fit its training set, per AlphaEvolve's multi-agent algorithm-discovery results).
- **Quality-diversity archive:** a MAP-Elites grid over *game behaviour*, not code: peak swarm size × aggression (head-on rate) × map-size bucket, plus ~8 more replay-derived descriptors. Use it to aim exploiters (an LLM "interestingness" director à la OMNI picks empty cells), to feed multi-elite prompts, and possibly to build a map-size dispatcher bot.
- **Ladder = final sanity check, not optimiser.** K=96 per battle gives ~40-90 Elo rating noise (my simulation; the real K falls to 24 after a submission's first 10 ranked battles, as of 2026-09-26). A 30-game manual A/B only resolves ~175 Elo differences. Its main value is free public replays for imitation bots and behaviour fingerprints.

## A. Self-play, league training, and game-theoretic evaluation

One idea runs through this whole section. In games with **non-transitive** structure, where A beats B, B beats C and C beats A, "keep the version that beats the previous version" can **cycle forever** and forget how to beat old strategies. Every method below is a different fix for that: play against a *population*, not just the last self.

### A1. AlphaGo Zero / AlphaZero (self-play with or without gating)
- **Mechanism.** The current network plays itself. MCTS-improved move choices become the training targets.
- **AlphaGo Zero gating (VERIFIED via search summary of the Nature paper, https://discovery.ucl.ac.uk/10045895/1/agz_unformatted_nature.pdf).** A new network becomes "best player" only if it wins **>55% of 400 games** against the current best. My calc: 400 games give SE ≈ 2.5%, so 55% is ~2 SE, about +35 Elo. It is a crude fixed-N gate.
- **AlphaZero** (https://arxiv.org/abs/1712.01815, abstract read) **dropped the gate**. It always self-plays with the latest network and reached superhuman chess, shogi and Go "within 24 hours". Dropping the gate was safe because Go and chess are mostly transitive near the top and each update is tiny.
- **Anti-cycling.** None explicit. It relies on the game being mostly transitive, plus search.
- **Transfer (INFERENCE).** Our updates are big discrete code edits, not tiny gradient steps, so a gate is needed. The AGZ gate (fixed N vs. the single best) is the simplest baseline. Section B's SPRT is a strictly better version of the same idea.

### A2. OpenAI Five (80/20 past-self sampling) (VERIFIED, https://arxiv.org/abs/1912.06680, Appendix N, read in full text)
- "80% of the games are played against the latest set of parameters, and 20% play against past versions ... to avoid strategy collapse in which the agent forgets how to play against a wide variety of opponents because it only requires a narrow set of strategies to defeat its immediate past version."
- **Opponent Manager.** Each past opponent i has a quality score q_i, and is sampled with probability p_i ∝ e^{q_i}.
  - Every 10 iterations the current agent is added to the pool with q = max(existing q).
  - When the current agent **beats** past opponent i: q_i ← q_i − η/(N·p_i), with η = 0.01.
  - When it loses, nothing changes.
  - Net effect: the pool automatically concentrates on the past versions that still beat us.
- **Transfer.** This is the cheapest useful pool sampler for us: a softmax over "still-dangerous" past champions. Run 80% of candidate-evaluation games vs. the current champion and 20% vs. the pool, sampled by these q-scores.

### A3. AlphaStar league (Vinyals et al., Nature 2019) and PFSP
Primary Nature paper not read this session. Details are VERIFIED from two secondary sources that summarise it: TStarBot-X (https://arxiv.org/abs/2011.13729) and the self-play survey (https://arxiv.org/abs/2408.01072).
- **Roles.**
  - **Main Agent (MA).** Never reset; the final product. It trains against itself and everything in the pool via PFSP.
  - **Main Exploiter (ME).** Trains only against the current MA to find its weaknesses. It is added to the pool, then **reset** to the supervised (imitation) model when it hits >70% win rate or a step limit (~every 24h in AlphaStar).
  - **League Exploiter (LE).** Plays PFSP against the *whole league* to find global blind spots. Added when its win rate is high; reset with probability 25% (~every 2 days).
- **MA opponent mix.** 35% self-play, 50% PFSP vs. all past players, 15% PFSP vs. forgotten main players and exploiters. This is my recollection of the Nature paper; TStarBot-X's table shows 35/50/15 bars for AlphaStar and uses 25/60/15 for its own surrogate. VERIFIED only via TStarBot-X's table.
- **PFSP (VERIFIED, survey §3.2.5).** Pick opponent n with probability ∝ f(P[beat n]).
  - f_hard(x) = (1−x)^p focuses on opponents we lose to.
  - f_var(x) = x(1−x) focuses on even matchups.
- **Why it works (Czarnecki 2020, below).** Exploiters deliberately *expand* coverage of the non-transitive "ring" at the current skill level, and the MA must beat all of them.
- **Transfer.** Map each role to an LLM agent with a different brief (details in "Transfer" below). Resetting exploiters to a *base* bot, not to the champion, is what keeps them diverse.

### A4. PSRO (Lanctot et al. 2017, https://arxiv.org/abs/1711.00832, VERIFIED abstract and full text grep)
- **Loop.**
  1. Keep a set of policies and an empirical **payoff matrix** from simulated matches.
  2. A **meta-solver** computes a mixture σ over the policies (Nash, uniform = fictitious play, regret-matching, Hedge, projected replicator dynamics).
  3. An **oracle** computes an approximate best response to σ and adds it to the set.
  4. Repeat.

  This generalises self-play (σ = latest only), fictitious play (σ = uniform) and double oracle (σ = Nash).
- **Overfitting evidence.** Their "joint-policy correlation" metric shows independently trained policies lose **34.2%** of reward when paired with partners they did not train with, rising to **71.7%** on larger maps. Their scalable variant DCH reduces this by 28.7-56.7%.
- **Anti-cycling.** The best response is against a *mixture* of the whole population, never just the last policy. With a Nash meta-solver the population can't be exploited by a policy that only beats the latest entry.

### A5. LLM/code-based PSRO (the closest prior art to our loop)
- **LLM-PSRO** (Bachrach et al., IJCAI 2025, "Combining code generating large language models and self-play to iteratively refine strategies in games"). The LLM is the best-response oracle: open-loop best-of-N sampling given the opponents' *full source code*. VERIFIED only via the CSRO paper's description.
- **CSRO — Code-Space Response Oracles** (Hennes, Li, Schultz, Lanctot, Mar 2026, https://arxiv.org/abs/2603.10098, VERIFIED from HTML full text).
  - The prompt contains the game rules, an environment API, and the source code **or LLM-written natural-language summaries** of the opponents that have non-zero weight in the Nash mixture σ (filtered to top-k or by minimum support).
  - Oracle variants: ZeroShot; LinearRefinement (M=10 inner iterations); and AlphaEvolve with score = estimated E_{π'~σ}[u(π,π')].
  - Settings: K=20 PSRO iterations, Gemini 2.5 Pro.
  - Findings:
    - The AlphaEvolve oracle gives the lowest exploitability.
    - **Giving opponent descriptions beats giving raw opponent code** (ZeroShot desc. AggScore 63.5±11.4 vs code −54.3±118.7 in repeated RPS).
    - The inner refinement loop "tactically hardens" candidates before they enter the pool.
- **COvolve** (https://arxiv.org/abs/2603.28386, abstract). LLM-generated policies *and environments*, both as Python code, in a zero-sum game. It uses the mixed-strategy Nash of the policy-vs-environment matrix to avoid forgetting. Relevant as a template for **adversarial map generation** (see leads).
- **AlphaEvolve discovering PSRO/CFR variants** (Li, Schultz, Hennes, Lanctot 2026, https://arxiv.org/abs/2602.16928, abstract). Relevant warning: "because the LLM optimizes for fitness on a specific training set, it often constructs highly synergistic, complex mechanisms tailored to those environments". Distilled minimal versions generalised better. **Takeaway: periodically ask the LLM to simplify the champion and re-test on held-out maps.**

### A6. Fictitious self-play / NFSP (Heinrich & Silver 2016, https://arxiv.org/abs/1603.01121, abstract)
- Best-respond to the **average** of all past policies, not the latest one. NFSP approximates this with a best-response net plus an average-policy net.
- NFSP "approached a Nash equilibrium" in Leduc poker "whereas common reinforcement learning methods diverged".
- **Transfer.** A uniform mixture over all saved champions is the fictitious-play meta-solver. It is simple and robust, but slow to drop obsolete opponents.

### A7. Population-based training (Jaderberg et al. 2017, https://arxiv.org/abs/1711.09846, VERIFIED full text)
- **Mechanism.** A population trains in parallel. When a member is "ready":
  - **Exploit:** truncation selection. If it is in the bottom 20%, copy weights and hyperparameters from a uniformly sampled top-20% member.
  - **Explore:** perturb each hyperparameter by ×0.8 or ×1.2, or resample it.
- **Transfer (INFERENCE).** For us, the "hyperparameters" are the bot's numeric knobs: split threshold, target swarm size, aggression radius, sprint conditions. A cheap non-LLM inner loop is PBT/SPSA over a parameter file, with the LLM reserved for structural changes. The bottom-20%-copies-top-20% rule maps directly onto islands of LLM agents: kill weak islands and fork strong ones.

### A8. Non-transitivity: "Real World Games Look Like Spinning Tops" (Czarnecki et al. 2020, https://arxiv.org/abs/2004.09468, VERIFIED full text)
- **Claim.** Real games (Go, StarCraft II, Tic-Tac-Toe, ...) have a transitive "skill" axis plus a non-transitive radial axis. The non-transitive width is largest at *medium* skill and shrinks at the top.
- **Result.** Proposition 3: fixed-size fictitious play converges if the population is at least as large as the non-transitive "layer" it sits in. Experiment: "once the population is big enough to deal with the non transitivity, the system converges to the strongest policy". Too small a population leads to cycling.
- **Practical rule, VERIFIED as their experimental protocol:** replace the oldest member with a strategy that **beats the whole population on average**.
- **Implication for us (INFERENCE).** The Battlecode meta is exactly the mid-skill region where cycles are widest: swarm vs. anti-swarm vs. long-snake. Keep a population of ~5-10 diverse past bots, not just "the last champion".

### A9. Nash averaging (Balduzzi et al. 2018, "Re-evaluating Evaluation", https://arxiv.org/abs/1806.02643, abstract read; mechanism from memory, UNVERIFIED in detail)
- **Mechanism.** Build the antisymmetric matrix A_ij = logit(P[i beats j]). Solve for the maximum-entropy Nash equilibrium p* of the zero-sum game A. Rate each agent by its expected payoff against p*.
- **Key property (VERIFIED, abstract).** It "automatically adapts to redundancies ... results are not biased by the incorporation of easy tasks or weak agents". Adding 10 near-copies of one weak bot does not inflate anyone's score, which Elo averages would do.
- **Transfer.** Our pool will be full of near-duplicate LLM versions. Score candidates against the **Nash mixture of the pool**, not against a uniform average, or duplicates will dominate the signal.

### A10. α-Rank (Omidshafiei et al. 2019, https://arxiv.org/abs/1903.01373, abstract)
- Ranks agents by the stationary distribution of an evolutionary Markov chain over the empirical game (the Markov–Conley chain solution concept). It is polynomial-time and, unlike Nash, handles cycles by putting mass on the cycle.
- **Transfer.** It is overkill for a pool of <30 bots. The max-entropy Nash from a small LP (scipy `linprog`) is enough. Mention α-Rank only if we go multi-player (we are 1v1).

### A11. Transfer: a league for LLM-edited code bots (INFERENCE, the design I recommend)
| AlphaStar / PSRO concept | Our analogue |
|---|---|
| Policy | A git commit or tag of the bot source (`bots/champ-017/`) |
| Payoff matrix | SQLite table of (botA, botB, map, side, seed) → result, plus replay path |
| Meta-solver | Max-entropy Nash over the pool payoff matrix (small LP), or PFSP f_hard weights; uniform as fallback |
| Main agent | "Champion" branch. The LLM *improver* gets: current code, loss replays, win-rate table vs. the Nash-weighted pool |
| Main exploiter | An LLM agent briefed "write a bot that beats champion X, anything goes", starting from a **simple base bot** (reset). Accepted into the pool once it reaches >60-70% vs. the champion |
| League exploiter | LLM agent briefed "beat the Nash mixture of the pool"; periodic reset |
| Fixed human-style opponents | **Imitation bots** hand-written by an LLM from *public replays* of the top ladder teams: extract swarm size, split timing, head-on behaviour, then write a bot that reproduces them. These are frozen and never removed |
| Anchor | Starter/example bot(s) from the organisers, to catch catastrophic regressions |
| Oracle refinement | Inner loop: the LLM edits, runs ~20-50 games vs. σ, repeats up to M times, *then* submits to the SPRT gate (CSRO's LinearRefinement) |
| Opponent info to the LLM | Natural-language behaviour **summaries** of σ's members, not raw code (CSRO finding), plus 1-3 loss replays |

## B. Evaluation statistics for keep/discard decisions

### B1. SPRT as used by Stockfish Fishtest (VERIFIED from the fishtest source, cloned 2026-09-23)
- **Mechanism.** H0: Elo = elo0, H1: Elo = elo1. After each result, update the log-likelihood ratio (LLR). Stop and **reject** when LLR <= A = ln(beta/(1-alpha)). Stop and **accept** when LLR >= B = ln((1-beta)/alpha). Source: `server/fishtest/stats/sprt.py` (`self.a = math.log(beta / (1 - alpha))`, `self.b = math.log((1 - beta) / alpha)`), https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/stats/sprt.py
- **Defaults.** alpha = beta = 0.05 are hard-coded in `views_run.py` (lines ~559-560). With alpha = beta = 0.05, the bounds are LLR in [-2.94, +2.94].
- **Preset bounds** (`templates/tests_run.html.j2`, elo_model default `"normalized"`):
  - Standard STC (10+0.1 s): **[0.0, 2.0]** nElo.
  - Standard LTC (60+0.6 s): **[0.5, 2.5]** nElo.
  - Non-regression STC and LTC (simplifications and bug fixes): **[-1.75, 0.25]** nElo.
  - The API rejects `elo1 < elo0 + 0.5` and `|elo0| > 10` (`api.py` ~561).
- **Gating pipeline.** A patch has to pass STC, then LTC, before anyone opens a PR. Simplifications only need to pass the non-regression bounds, meaning "not worse". Source: https://github.com/official-stockfish/fishtest/wiki/Creating-my-first-test
- **Normalized Elo.** nElo = (score - 0.5) / sigma_per_game × 800/ln10 (constant `nelo_divided_by_nt = 800/ln(10) = 347.4` in `stats/LLRcalc.py`). The point: the expected test length depends only on the bounds, not on draw rate or opening book. Source: https://github.com/official-stockfish/fishtest/wiki/Fishtest-Mathematics and https://www.cantate.be/Fishtest/normalized_elo_practical.pdf
- **GSPRT.** Unknown nuisance parameters, such as the draw rate, are replaced by their MLEs. The result is asymptotically equivalent to SPRT (Fishtest-Mathematics wiki).
- **Selection bias warning (VERIFIED, Fishtest FAQ).** Elo estimates for patches that passed are biased upward. A later regression test usually measures less than the sum of the individual gains. **Our analogue: do not add up the "+X Elo" claims of accepted LLM edits. Re-measure champion vs. the champion from N versions ago.**
- **"Unions" (VERIFIED, FAQ).** Patches that failed SPRT with near-positive scores are sometimes bundled and retested as one. This is useful for LLM edits that each help a little.

### B2. OpenBench (VERIFIED from source, https://github.com/AndyGrant/OpenBench)
- `OpenBench/stats.py` provides `TrinomialSPRT([L,D,W], elo0, elo1)` and `PentanomialSPRT([LL,LD,DD,DW,WW], elo0, elo1)`. The comment says the pentanomial version is "taken directly from Fishtest" and implements normalized_elo_practical.pdf. `models.py` records "Switching all future tests to Pentanomial".
- Alpha and beta are per-test fields. Common chessprogramming-wiki example: `elo0=0 elo1=5 alpha=0.05 beta=0.05` (https://www.chessprogramming.org/Sequential_Probability_Ratio_Test). The same page lists the Fishtest bounds [0,2] gainer and [-1.75,0.25] non-regression.

### B3. Pentanomial model and paired games
- **Pentanomial model.** Games are played in **pairs**: the same opening, with colours swapped. The pair score takes one of 5 values (LL, LD, DD/WL, WD, WW), i.e. 0, 0.5, 1, 1.5 or 2 points. The variance is computed over pairs, not over single games. This cancels opening/colour bias and "leads to a substantial saving of testing resources compared to the trinomial model". The gap between the pentanomial and trinomial variances estimates the RMS bias of the openings. Source: https://github.com/official-stockfish/fishtest/wiki/Fishtest-Mathematics and https://www.chessprogramming.org/Match_Statistics
- **Transfer (INFERENCE).** Our "opening" is a (map, spawn side) pair. Turns run sequentially by dragon id, so there may be a first-mover or side bias even on symmetric maps. Always play each map twice with sides swapped and score the **pair** as 0 / 0.5 / 1.
- **How much pairing saves (my simulation, `/tmp/sprt_calc.py`).** Model each map as having a logit bias toward one side, with SD s.

  | Map/side logit-bias SD | Games saved by pairing |
  |---|---|
  | 0 | 0% |
  | 1 | 17% |
  | 2 | 39% |
  | 3 | 54% |

  Large map-dependence is likely in a game where map size ranges from 10 to 64 tiles, so pairing is nearly free variance reduction.
- **Determinism caveat (INFERENCE, important).** If the judge, the pearl RNG and both bots are all deterministic, replaying the same (map, side) pair gives the **same game**. The number of *independent* samples is then capped at #maps × 2 no matter how many games we "run". Two fixes:
  1. Give each bot a per-game random seed, passed in or derived from something that varies.
  2. Count distinct (map, side, seed) triples as the sample size.

  Check this empirically: run the same pairing 3 times and diff the replays.

### B4. How many games to detect a given Elo gain (my calcs; logistic Elo, no draws, Wald ASN approximation)
Win rate p = 1/(1+10^(-Elo/400)). Script: `/tmp/sprt_calc.py`, a small Python calc using Wald's OC/ASN formulas.

| True gain (Elo) | Win rate | SPRT[0,E] a=b=0.05: avg games if true=E | worst case (true=E/2) | Fixed-N z-test a=0.05, power 0.8 |
|---|---|---|---|---|
| +5 | 50.72% | 25,600 | 42,000 | 29,900 |
| +10 | 51.44% | 6,400 | 10,500 | 7,500 |
| +20 | 52.88% | 1,600 | 2,600 | 1,870 |
| +50 | 57.15% | 260 | 420 | 300 |
| +100 | 64.01% | 67 | 107 | 77 |

Looser error rates cut the cost roughly linearly in ln(1/alpha):

| Bounds | ASN @ true=E | Worst case |
|---|---|---|
| a=b=0.1, E=50 | 172 | 234 |
| a=b=0.1, E=20 | 1,063 | 1,458 |
| a=b=0.2, E=50 | 81 | 93 |
| a=b=0.2, E=100 | 21 | 24 |

95% CI half-width of an Elo estimate after N games at ~50%:

| N | Half-width |
|---|---|
| 50 | ±96 |
| 100 | ±68 |
| 200 | ±48 |
| 400 | ±34 |
| 1,000 | ±21 |
| 4,000 | ±11 |

SPRT(0,50) pass probability vs true Elo (operating characteristic):

| True Elo | Pass probability |
|---|---|
| -10 | 1.6% |
| 0 | 5% |
| 12.5 | 19% |
| 25 | 50% |
| 37.5 | 81% |
| 50 | 95% |

**Consequence for our budget ("hundreds of games per candidate", 0.5-10 s per game; by 2026-09-26 the loop plays up to 2400 pairs per candidate over several machines):**
- We can only reliably detect gains of roughly **>= +40-50 Elo** (about a 56-57% win rate) per candidate. Gains of +5 to +10 Elo are not measurable at this budget; they need 10^4 games.
- So the LLM should be pushed toward *big* strategic changes. Chess-style micro-tuning at +2 Elo is out of reach, except through SPSA-style parameter tuning that spreads the noise over many parameters (Fishtest uses SPSA for this, per the "Creating my first test" wiki).
- Early in the loop (weak bot) gains are large, and SPRT(0,100) finishes in ~70 games.

### B5. Elo vs win-rate, ladder noise
- Conversions: 100 Elo ≈ 64% win rate, 200 ≈ 76%, 400 ≈ 91%. The #1 team winning ~80% corresponds to ~+240 Elo over the average opponent they face.
- **Ladder noise (my simulation, `/tmp/ladder.py`).** Assumptions: K=96 per battle, opponents rated ±150 around us, and the score is either the fraction of 5 games won or a binary battle win. Result: a team's rating random-walks with a stationary **SD of ~42 Elo** (fractional score) or **~86 Elo** (binary battle score). A single ladder snapshot therefore cannot distinguish versions less than ~100 Elo apart. Average over many battles, or better, use the raw game results against known opponents.
- ~~**UNVERIFIED:** whether the ladder's Elo scale is 400-based and whether a battle's score is fractional or binary.~~ Answered by `docs/unsw/elo.md`: 400-based, and the score is the fraction of the 5 games won (a draw counts half).

### B6. Ranking a pool: Bradley–Terry, Elo, TrueSkill
- **Bradley–Terry (BT).** P(i beats j) = e^{r_i}/(e^{r_i}+e^{r_j}), fit by MLE over the whole results table. Unlike online Elo updates it does not depend on game order, which makes it the right tool for an offline pool of saved bot versions. BayesElo and Ordo, the chess rating tools, are BT fits. Tool page: https://www.remi-coulom.fr/Bayesian-Elo/ (UNVERIFIED this session: exact BayesElo priors).
- **TrueSkill** (Herbrich, Minka, Graepel, NeurIPS 2006; https://www.microsoft.com/en-us/research/publication/trueskill-a-bayesian-skill-rating-system/). Each player has a Gaussian (mu, sigma). The conservative rank is mu - 3·sigma. It is useful for **choosing which match to play next** (play pairs whose outcome is most uncertain) and for ranking new entrants after few games. For a small fixed pool with plentiful games, BT is simpler and good enough.
- **Limitation.** Every one-number rating assumes transitivity. Czarnecki et al. and Balduzzi et al. (section A) show that real games have a non-transitive component, so keep the full **payoff matrix** as well as the ratings.

### B7. Overfitting to fixed maps and opponents
- **Inference, drawn from the Fishtest practice of fixed large opening books** (FAQ: "unbalanced books are definitely better than balanced books for engine tests") **and from ML generally.** An LLM that repeatedly edits against the same 10 maps and 3 opponents will learn map-specific hacks. Mitigations:
  1. A **train map set** (visible to the LLM, used for gating).
  2. A **held-out map set** that is never shown to the LLM and is only used for promotion decisions. Rotate a few maps from held-out to train each day.
  3. Stratify maps by size bucket (10-20, 21-40, 41-64). Size probably changes optimal swarm size the most.
  4. Keep the opponent pool **diverse** (section A) and report **worst-case** as well as mean win rate over opponents. A candidate that gains +80 vs one opponent but loses -60 vs another is a cycle, not progress.

## C. Quality-diversity (QD) and open-endedness: keeping a diverse archive of strategies

Why QD belongs here: Section A says the opponent pool must *cover* the non-transitive ring. QD is the machinery for producing that coverage on purpose, so it doesn't depend on luck.

### C1. MAP-Elites (Mouret & Clune 2015, https://arxiv.org/abs/1504.04909, abstract; algorithm well known)
- **Mechanism.**
  1. Choose 2-3 **behaviour descriptors** (BDs) and split each into bins; each grid cell stores one **elite**, the best solution with that behaviour.
  2. Loop: pick a random elite, mutate it, evaluate its fitness and BD, and put it in its cell if the cell is empty or the new one is fitter.
- **Anti-collapse.** Solutions compete only *within* their cell, so a mediocre "long-snake" bot survives even if swarm bots dominate. That makes it a stepping stone for later.
- **Numbers.** None load-bearing. Cell count = bins^dims, so 3 BDs × 5 bins = 125 cells. That is already more than our game budget can fill with SPRT-grade fitness. Use coarse bins (3-4 per dimension).

### C2. CVT-MAP-Elites (Vassiliades, Chatzilygeroudis, Mouret 2016, https://arxiv.org/abs/1610.05729, abstract)
- Replaces the grid with a **fixed number k of Voronoi regions** (centroidal Voronoi tessellation), so cell count doesn't grow exponentially with the number of dimensions.
- **Transfer.** Use it if we want 5-8 BDs. Set k ≈ 20-30 so each niche gets a meaningful number of games.

### C3. Novelty search (Lehman & Stanley 2008/2011, "Abandoning Objectives", Evolutionary Computation 19(2); http://eplex.cs.ucf.edu/noveltysearch/userspage/ ; details from memory, UNVERIFIED this session)
- **Mechanism.** Select for *behavioural novelty* instead of fitness. Novelty = mean distance to the k nearest neighbours (current population plus an archive of past novel individuals) in BD space. Individuals above a novelty threshold go into the archive.
- **Transfer.** Pure novelty is wasteful for us because fitness matters. Novelty is worth using as a **tie-breaker or bonus for exploiter slots**: prefer the exploiter candidate whose BD is farthest from every existing pool member.

### C4. POET (Wang, Lehman, Clune, Stanley 2019, https://arxiv.org/abs/1901.01753, abstract)
- **Mechanism.** Co-evolve (environment, agent) pairs.
  - New environments are mutated from old ones and admitted only if they meet a **minimal criterion**: not too easy and not too hard for current agents.
  - Agents are periodically **transferred** between environments when they beat the incumbent.
  - Many problems were solved only via stepping stones from other environments, "cannot be solved by direct optimization alone".
- **Transfer.** Maps are our environments. Beyond the official map set, generate custom **stress maps** (tiny 10×10 torus, huge 64×64, sparse pearls, dense pearls) where the champion is at 30-70% vs. the pool, i.e. the minimal criterion. Only useful if the local runner accepts custom maps (UNVERIFIED).

### C5. OMNI and OMNI-EPIC (Zhang, Lehman, Stanley, Clune 2023; Faldor et al. 2024; https://arxiv.org/abs/2306.01711 , https://arxiv.org/abs/2405.15568, abstracts)
- **Mechanism.** A foundation model acts as a **model of interestingness**: out of many *learnable* tasks, it picks the ones humans would find *interesting* (worthwhile and novel) rather than trivial variations. OMNI-EPIC has the FM write new tasks *as code* (environment plus reward), filtered for being learnable and interesting.
- **Transfer.** An LLM "director" reads the archive (BD summaries plus the payoff matrix) and proposes the **next exploiter brief**, such as "nobody has tried a 4-dragon kill-squad that hunts heads on small maps". This is the key to not generating 50 minor variations of the champion. It is cheap: one LLM call per exploiter.

### C6. QDAIF (Bradley et al. 2023, https://arxiv.org/abs/2310.13032, abstract)
- **Mechanism.** MAP-Elites where the LLM does both the mutation *and* the evaluation: it judges quality and places candidates on **LLM-judged diversity axes** (for creative writing, e.g. genre or tone).
- **Transfer.** When a behaviour is hard to compute from replay data, e.g. "does it play for territory or for kills?", let an LLM classify a replay summary into a coarse bin. Numeric BDs are still preferred where they exist: they're cheaper and deterministic.

### C7. In-context QD (Lim et al. 2024, https://arxiv.org/abs/2404.15794) and LLMatic (Nasir et al. 2023, https://arxiv.org/abs/2306.01102), abstracts
- **In-context QD.** Put *many* diverse archive elites into the prompt (few- or many-shot) and let the LLM *combine* them, instead of mutating one parent. They report it beats standard QD baselines.
- **LLMatic.** LLM code mutation combined with QD over two archives (networks and prompts). Competitive NAS results after evaluating just 2,000 candidates.
- **Transfer.** The improver prompt should show 2-4 elites *from different cells* with their BDs and win rates ("the swarm elite wins 70% on big maps; the kill-squad elite wins 65% on small maps; combine"). This is cheap crossover.

### C8. FunSearch / AlphaEvolve / OpenEvolve island + MAP-Elites (brief; covered in depth by another agent)
- **OpenEvolve default config (VERIFIED, https://github.com/codelion/openevolve/blob/main/configs/default_config.yaml).**
  - `num_islands: 5`, `migration_interval: 50`, `migration_rate: 0.1`, `population_size: 1000`, `archive_size: 100`.
  - MAP-Elites `feature_dimensions` default to `["complexity", "diversity"]`: code length and code edit-distance, with `feature_bins: 10`. Custom evaluator metrics can be used as features.
- **Transfer.** Replace the code-level features with **game-behaviour features** (below). Code diversity is a poor proxy: two very different-looking codebases can play identical swarm strategies.

### C9. Concrete behaviour descriptors for UNSW Battlecode 2026 (INFERENCE; all computable from a replay's per-turn state)
Game facts assumed from the task brief: torus, ≤64 dragons, sonar ping (since 2026-09-24: up to four uint64 values a turn, one per direction, with echo counts), split, sprint, head-on means both die, 500 rounds. Whether replays expose per-dragon actions or only positions is UNVERIFIED; if only positions, infer splits and sprints from length and position deltas.

| # | Descriptor | Definition | Suggested bins |
|---|---|---|---|
| 1 | **Peak swarm size** | max over rounds of our live dragon count | 1-4 / 5-15 / 16-35 / 36-64 |
| 2 | **Split timing** | round of the first split (or of reaching 50% of peak count) | <50 / 50-150 / >150 / never |
| 3 | **Mean dragon length** | time-averaged mean length of live dragons | short / medium / long (quantiles over the archive) |
| 4 | **Aggression** | head-on collisions we are part of, plus moves that end adjacent to an enemy head, per 100 dragon-turns | low / med / high |
| 5 | **Death mix** | fraction of our deaths that are head-on vs. into-body vs. self/own-team | categorical |
| 6 | **Sprint rate** | fraction of dragon-moves that are sprints | 0 / <5% / >5% |
| 7 | **Sonar usage** | pings per dragon-turn; also the *entropy* of ping values (spoof-resistant protocols look random) | none / low / high |
| 8 | **Pearl share** | our pearls eaten / all pearls eaten | <40% / 40-60% / >60% |
| 9 | **Territory / spread** | mean pairwise torus distance between our heads, or the fraction of the grid within d of one of our heads | clumped / spread |
| 10 | **Win-path** | how we win: wipeout vs. longest-dragon tiebreak vs. total-length tiebreak | categorical |
| 11 | **Survival curve** | our dragon count at rounds 100/250/500, normalised | early-collapse / stable / late-surge |

- **Recommended MAP-Elites axes** (3 × 3-4 bins, ≈36-48 cells): **peak swarm size** × **aggression** × **map-size bucket**. The last axis makes the elite per cell a *specialist per map size*, which the final bot can dispatch to at runtime, since map size is known at game start. The other descriptors are logged for novelty and LLM prompts.
- **Fitness inside a cell:** Nash-averaged win rate vs. the current pool (A9) over the train maps.
- The same BD extractor also **fingerprints ladder opponents** from their public replays. Place each top team in the BD space, and aim imitation bots and exploiters at those cells.

## Recommended evaluation protocol for our loop (INFERENCE, built from A-C)

### 0. Prerequisites (check once, before any loop runs)
1. **Determinism check.** Run the same (botA, botB, map, side) three times and diff the replays. If they are identical, the effective sample size is #maps × 2. Add a per-game seed to our bot, and to opponents we control, so repeated games differ. Without this, every statistic below is fake.
2. **Side-bias check.** Champion vs. itself on every map, both sides. If one side wins noticeably more than 50%, pairing (B3) is mandatory; it is recommended anyway.
3. **Throughput.** Measure games/hour at safe parallelism (machine is shared; watch load). Example: at ~3 s/game and 6 workers, ~7,000 games/h. A 300-game SPRT then costs ~3 minutes, so the bottleneck is LLM edit time, not games.

### 1. Gating tiers (each tier is cheap enough that most candidates die early)
| Tier | What | Games | Pass rule |
|---|---|---|---|
| T0 smoke | Builds; no crash or timeout on 3 maps (small, medium, large) vs. starter bot | 3-6 | 0 errors, 0 timeouts, wins every game vs. starter bot |
| T1 quick screen | vs. current champion, paired (map × both sides), train maps | 40 (20 pairs) | Score ≥ 18/40 (45%). Binomial calc: pass rate is 15% at true −100 Elo, 45% at −50, 79% at 0, 96% at +50. So it removes big regressions cheaply and rarely kills real gains |
| T2 SPRT vs. pool | Pentanomial-style pairs vs. the **Nash- or PFSP-weighted pool** (A11), train maps, 80% champion / 20% pool (A2) | SPRT(elo0=0, elo1=50, α=0.1, β=0.1): ~170 games average, worst case ~240 (B4). Cap at 600 games; at the cap, take the sign of the estimate | LLR ≥ upper bound |
| T3 held-out confirmation | vs. the same pool on **held-out maps** never shown to the LLM | 100-200 fixed | Win rate ≥ 52% *and* no single pool opponent worse than −100 Elo (≤ 36%) vs. the old champion's record against it |
| T4 ladder truth (sparingly) | Manual ladder games vs. top-5 real teams | ~30-60 per promoted champion (≤ 1 hour of quota) | Compare to the previous champion's paired record on the same opponents and maps. With 30 games per version, the 95% CI on the *difference* is about ±176 Elo, so only very large shifts show up. Use it as a sanity check (no collapse against real teams), not as a fine-grained gate |

- **Bounds rationale.** SPRT[0,50] at α=β=0.1 means: a true +50 passes 90% of the time, a true 0 passes 10%, a true +25 is a coin flip. Early on, when gains are large, use SPRT[0,100] at α=β=0.1 (~45 games). Tighten to [0,30] later if the budget allows (~500 games).
- **Non-regression bounds for simplifications** (Fishtest's [-1.75, 0.25] idea, scaled): SPRT[-30, 0]. Lets the LLM delete complexity when "not worse" is enough. Also use it to accept the "distil/simplify champion" step (A5).
- **Unions** (B1): keep near-misses, meaning SPRT fails with a positive estimate. Every ~5 near-misses, ask the LLM to combine them and re-test as one candidate.
- **Never sum the claimed gains** (B1 selection bias). Every ~10 promotions, run a fixed 1,000-game regression test: new champion vs. the champion from 10 promotions ago, on held-out maps.

### 2. Opponent pool composition (target ~10-16 bots, fixed maximum)
| Slot | Count | Source | Lifetime |
|---|---|---|---|
| Current champion | 1 | Promoted main line | Replaced on promotion |
| Past champions | 3-5 | Previous champions, sampled via OpenAI-Five q-scores (A2) | FIFO beyond 5, but keep any with Nash weight > 0 |
| Main exploiters | 2-3 | LLM brief "beat the champion", starting from a **simple base bot** (AlphaStar reset, A3). Admitted at ≥ 60% vs. champion over 100 paired games | Retired when the champion beats them ≥ 65% and their Nash weight is 0 |
| League exploiters | 1-2 | LLM brief "beat the Nash mixture of the pool"; OMNI-style LLM director picks an unfilled behaviour cell (C5) | Same as above |
| Ladder imitations | 3-5 | Hand-written by an LLM from **public replays** of the top ladder teams (BD fingerprint plus a behaviour description). Validate that the imitation's BD matches its target's BD from real replays | Frozen; refreshed when a top team visibly changes strategy |
| Anchors | 1-2 | Organisers' starter/example bot(s) | Permanent (catch catastrophic regressions) |

- **Weighting.** Candidate score = expected win rate vs. the max-entropy Nash mixture of the pool (A9). Report the per-opponent worst case alongside it. The LLM improver gets per-opponent win rates, 1-3 loss replays against the highest-weighted opponent, and **natural-language summaries** of opponents, not their code (CSRO, A5).
- **Payoff matrix upkeep.** Every new pool member plays ~40 paired games vs. every existing member. For 16 bots that is ~120 pairings × 40 ≈ 5k games, under an hour at the throughput above.

### 3. Map sampling
- Split the official maps into **train (~70%)** and **held-out (~30%)**, stratified by size bucket (10-20, 21-40, 41-64) and by any other visible property (pearl density, symmetry type).
- Pairs: every game is played twice on the same map with sides swapped (B3).
- Each day, move ~2 maps from held-out to train and add new ones as they become known, so held-out never goes stale. The LLM never sees held-out results in its prompts, only the gate decisions.
- Optional POET-style stress maps (C4), only if the runner supports custom maps.

### 4. Using the real ladder (60 manual games/hour, K from 96 down to 24 per 5-game battle)
- Treat the ladder as the **final truth signal**, not an optimiser. Its noise is large: a stationary rating SD of ~40-90 Elo (B5), so single snapshots mean little.
- Use manual games as **paired A/B tests against real top teams**: the old and new champion each play the same (opponent, map) list. 30 games each fits in one hour of quota. That only resolves differences of ~175 Elo or more (95% CI on the difference, my calc). Repeat over several hours, or pool results across opponents, when a decision needs more precision.
- Harvest replays from **all** public top-team games (free, no quota) to update imitation bots and BD fingerprints. This is the main value of the ladder.
- Submit a new ladder version only after T3, and at most every few hours so the 2-hourly auto-scrimmages accumulate evidence per version. Freeze a verified champion ahead of each deadline (1, 10, 17 Oct) to avoid a last-minute regression.
- UNVERIFIED: whether manual games affect the rating, whether opponents and maps can be chosen, and the exact Elo formula. Read the ladder docs or API before relying on this.

### 5. Behaviour-descriptor archive
- After every evaluated candidate, compute BDs (C9) from its T2 replays and store (commit, BDs, Nash win rate, per-opponent win rates) in the archive DB.
- MAP-Elites grid: peak swarm size × aggression × map-size bucket. Each cell keeps its best bot, even when that bot is worse than the champion overall.
- Uses:
  1. Exploiter seeds and targets.
  2. Multi-elite prompts for the improver (C7).
  3. A final **meta-bot** that dispatches to the best cell elite by map size at game start, but only if it passes SPRT against the single champion.

## Out of scope / leads (would be filed as GitHub issues, not acted on here)
1. **Engine determinism and seeding.** Check whether the WASM judge and bots are deterministic per (map, side), and whether the runner lets us inject a per-game seed. This blocks all the statistics above (Protocol §0). **Answered:** the same seed replays exactly; from 2026-09-25 each match draws a random pearl seed and `unswbc run --seed` sets one, which our referee derives from the map.
2. **Ladder API semantics.** Do manual games affect Elo? Can we choose opponent and map? What are the Elo formula and scale (400?), and is a battle score the fraction of 5 games or binary win/loss? Is there a rate limit beyond 60/h?
3. **Replay format.** Does a replay expose per-dragon actions (split, sprint, sonar value) or only board states? This decides which BDs in C9 are exact and which must be inferred.
4. **Custom maps in the local runner** (POET-style stress maps, C4).
5. **SPSA parameter tuning track.** Fishtest tunes numeric parameters with SPSA (see the Fishtest wiki "Creating my first test", SPSA section). A non-LLM tuner over a `params.json` could capture the small gains that SPRT can't see at our budget.
6. **Read the AlphaStar Nature Methods directly** (https://www.nature.com/articles/s41586-019-1724-z) to confirm the 35/50/15 main-agent mix and the exact PFSP weighting exponent.
7. **Read Bachrach et al. 2025 (LLM-PSRO, IJCAI)** in full. Only its CSRO summary was read here.
8. **Adversarial map generation à la COvolve** (https://arxiv.org/abs/2603.28386): an LLM writes maps that break the champion, and a Nash mixture over maps prevents forgetting.
9. **Fishtest SPRT calculator** (https://tests.stockfishchess.org/sprt_calc) and the `vdbergh/pentanomial` repo (https://github.com/vdbergh/pentanomial). The Fishtest `stats/` Python could be vendored directly (GPL, check the licence) rather than reimplemented.

