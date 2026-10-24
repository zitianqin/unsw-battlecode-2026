# Testing methodology: how to know a bot change is real

Goal: an autonomous loop where agents change the bot and we only keep changes that are really better. This file gives formulas, a games-needed table, and a concrete protocol. Numbers marked **[calc]** were computed this session with small Python scripts (normal approximation / Wald SPRT / Monte Carlo); formulas are standard.
Legend: **[V]** verified from cited source this session; **[I]** inference; **UNVERIFIED**.

---

## 1. Elo from win rate

Score p = (wins + ½·draws) / games.

- Elo difference: **D = −400 · log₁₀(1/p − 1)**
- Inverse: **p = 1 / (1 + 10^(−D/400))**
- Same formula the Battlecode ladder uses for expected share (`docs/unsw/elo.md`, K = 96 (now only for a new submission, falling to 24 over its first 10 ranked battles) applied once per 5-game battle) [V].

| win rate p | Elo | | Elo | p |
|---|---|---|---|---|
| 0.51 | +7 | | +5 | 0.507 |
| 0.52 | +14 | | +10 | 0.514 |
| 0.53 | +21 | | +20 | 0.529 |
| 0.55 | +35 | | +35 | 0.550 |
| 0.60 | +70 | | +50 | 0.572 |
| 0.65 | +108 | | +70 | 0.599 |
| 0.70 | +147 | | +100 | 0.640 |
| 0.75 | +191 | | +150 | 0.703 |
| 0.80 | +241 | | +200 | 0.760 |
| 0.90 | +382 | | +400 | 0.909 |
[calc]

### Confidence interval
Standard error of p: **SE = √(p(1−p)/n)** (with draws use the sample variance of per-game scores instead of p(1−p); draws shrink it). 95% CI on p = p ± 1.96·SE, then map both ends through the Elo formula.

Worked examples [calc]:
| result | p | Elo | 95% CI (Elo) |
|---|---|---|---|
| 30 / 50 | 0.60 | +70 | [−25, +178] |
| 60 / 100 | 0.60 | +70 | [+3, +144] |
| 120 / 200 | 0.60 | +70 | [+22, +121] |
| 550 / 1000 | 0.55 | +35 | [+13, +57] |

Takeaway: **50 games cannot even tell a +70 Elo change from a regression.** 100 games barely can.

Rule of thumb near p = 0.5 (no draws): **SE ≈ 347/√n Elo, 95% half-width ≈ 680/√n Elo** (slope of the Elo curve at 0.5 is 400/(ln10·0.25) ≈ 695 Elo per unit score, times SE_p = 0.5/√n) [calc]. So n = 100 → ±68 Elo (95%), n = 400 → ±34, n = 1600 → ±17.

---

## 2. Games needed (fixed sample size)

One-sided test of H0: p = 0.5 vs true p, α = 0.05 (false-accept rate), no draws:
**n = [ (z_α·0.5 + z_β·√(p(1−p))) / (p − 0.5) ]²**, z_α = 1.645, z_β = 0.842 (80% power) or 1.645 (95% power).

| true p | Elo | n (80% power) | n (95% power) | Wald SPRT avg games, α=β=0.05, H1=this p [calc] |
|---|---|---|---|---|
| 0.70 | +147 | 37 | 63 | 32 |
| 0.65 | +108 | 67 | 115 | 58 |
| 0.60 | +70 | 153 | 266 | 132 |
| 0.575 | +53 | 273 | 476 | 235 |
| 0.55 | +35 | 617 | 1,077 | 529 |
| 0.53 | +21 | 1,716 | 3,001 | 1,471 |
| 0.52 | +14 | 3,863 | 6,759 | 3,312 |
| 0.51 | +7 | 15,455 | 27,051 | 13,249 |
[calc]

- **Early regime (big changes, 55–70% vs baseline):** 30–600 games. At 0.5–10 s/game locally this is minutes. Cheap: always test.
- **Small-Elo regime (+5 to +20):** thousands to tens of thousands of games. Only worth it for changes that are cheap to test in parallel, or it's better to stack several small ideas and test the bundle.
- Scaling: n ∝ 1/Δ². Halving the effect size you want to detect quadruples the games.
- Wall clock [I]: with W parallel game workers at t seconds/game, games/hour = 3600·W/t. E.g. W = 4, t = 5 s → 2,880 games/h: a +35 Elo test (~530 games) ≈ 11 min; a +14 Elo test (~3,300) ≈ 70 min. Respect the machine load rule (shared, loaded box).

---

## 3. SPRT (Stockfish Fishtest style)

Wald's Sequential Probability Ratio Test: play games one at a time, keep a running log-likelihood ratio (LLR), stop as soon as it crosses a bound. Needs on average ~15–50% fewer games than a fixed-n test with the same error rates, and **it is legitimate to look after every game** (unlike a fixed-n test, where peeking and stopping when it "looks good" inflates false positives).

- Hypotheses: H0: Elo = elo0, H1: Elo = elo1. Convert to scores s0 = p(elo0), s1 = p(elo1).
- Bounds: **A = ln(β/(1−α)), B = ln((1−β)/α)**. With α = β = 0.05: **A = −2.944, B = +2.944** [V: Fishtest `sprt.py` sets `self.a = log(beta/(1-alpha))`, `self.b = log((1-beta)/alpha)`, defaults alpha=beta=0.05, elo0=0, elo1=5] https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/stats/sprt.py
- Per-game LLR increment (no draws): win → ln(s1/s0); loss → ln((1−s1)/(1−s0)). Accept H1 (pass) when LLR ≥ B; accept H0 (fail) when LLR ≤ A.
- With draws, Fishtest uses a **generalized SPRT** where the unknown outcome distribution is replaced by its maximum-likelihood estimate under each hypothesis (`LLRcalc.py`, `MLE_expected`) [V] https://github.com/official-stockfish/fishtest/blob/master/server/fishtest/stats/LLRcalc.py ; https://github.com/official-stockfish/fishtest/wiki/Fishtest-Mathematics
- Simple approximation that works for small Elo (from the same GSPRT idea): LLR ≈ (s1 − s0)·(2·x̄ − s0 − s1)·n / (2·σ̂²), where x̄ = mean score, σ̂² = per-game (or per-pair) score variance. [I; standard normal-approximation form]

**Common bounds** [V, CPW https://www.chessprogramming.org/Sequential_Probability_Ratio_Test]:
| usage | gainer bounds | non-regression bounds |
|---|---|---|
| Stockfish LTC | [0.5, 2.5] | [−1.75, 0.25] |
| Stockfish STC | [0, 2] | [−1.75, 0.25] |
| top-30 engines | [0, 3] | [−3, 1] |
| top-200 engines | [0, 5] | [−5, 0] |
| all other engines | [0, 10] | [−10, 0] |
(Stockfish's bounds are in *normalized* Elo, see below.) Fishtest process: pass STC first, then LTC; never stop a running SPRT early [V https://github.com/official-stockfish/fishtest/wiki/Creating-my-first-test]. **Non-regression bounds** are used for simplifications: "does removing this code lose less than X Elo?" — a good fit for our "delete machinery" taste.

**Behaviour of an SPRT with bounds [0, 50] (α=β=0.05)**, Monte Carlo, 2,000 runs each [calc]:
| true Elo | pass rate | mean games |
|---|---|---|
| −25 | 0.00 | 144 |
| 0 | 0.04 | 260 |
| +25 | 0.52 | 440 |
| +50 | 0.94 | 272 |
| +100 | 1.00 | 100 |
Note the slowest case is a true effect *between* the bounds (+25 → 440 games, coin-flip outcome). Pick elo1 near the smallest gain you'd bother keeping.

**Pentanomial statistics** [V https://github.com/official-stockfish/fishtest/wiki/Fishtest-Mathematics]: Fishtest plays games in **pairs** (same opening, colours swapped) and models the pair result as one of 5 outcomes (LL, LD+DL, LW+DD+WL, DW+WD, WW) instead of per-game W/D/L. "Leads to a substantial saving of testing resources compared to the trinomial model." Why [I]: if the opening/side favours one side, the two games in a pair are anti-correlated, so the pair's score has lower variance than two independent games; the pentanomial model measures that lower variance instead of assuming independence. The gap between the two models even estimates how biased the opening book is.

**Normalized Elo** [V same page]: Fishtest bounds are expressed in normalized Elo (nElo = (score − 0.5)/σ_pair · 800/ln10 ≈ 347·t-value per pair) so "expected duration of a test depends only on the bounds, independent of draw ratio or opening book". Useful for us if our draw rate or per-map variance shifts over time.

**Battlecode analogue of the opening book + colour swap [I]:** a "game" = (map, seed, side assignment). Play every (map, seed) **twice with sides swapped** (team A gets the other spawn / other id ordering). Maps are symmetric (mirror x/y or 180° rotation per the rules digest), but turn order by id is not symmetric: starting ids are assigned in map-file order (`docs/unsw/map-files.md`: "Ids are assigned in file order from 0, which is also turn order") [V], so on a given map one team's dragons may systematically act first. Whether that is an edge is UNVERIFIED. Pairing cancels any side/map bias, exactly as colour swaps cancel the first-move edge in chess. Score pairs as 0 / 0.5 / 1 / 1.5 / 2 and run the SPRT on pair scores.

---

## 4. Variance reduction in practice
1. **Paired side swap** (above). Largest, free.
2. **Common random numbers**: use the same fixed list of (map, seed) for every candidate. The engine is deterministic given seeds (rules digest: "RNG is seeded per dragon per game") so a candidate vs baseline difference is then due to the code, not luck of the draw. When comparing candidates B and C against baseline A, both see identical games.
3. **Stratify by map**: report per-map score, not just the total. A +50 Elo total that is +300 on two maps and −20 on the rest is an overfit, not an improvement (see §6).
4. **Measure more than win/loss where it is honest**: final longest-dragon length, total length, survival count, death causes. These have lower variance than W/L and explain *why*, but only W/L (or the ladder's game share) is the objective — never accept a change on a proxy alone.
5. **Scenario regression tests** (cheap, deterministic): replay positions mined from losses ("round 154, dragon 7 must not move N"). Used by Battlesnake (Nettogrof https://nettogrof.github.io/step5/) and Hungry Geese 2nd place (see hungry-geese-and-tron.md). These catch blunders quickly but do not measure strength.

---

## 5. Parameter tuning

### SPSA (what Stockfish uses) [V CPW https://www.chessprogramming.org/SPSA ; Fishtest math page]
Per iteration k, perturb **all** p parameters at once with a random ±1 vector Δ:
```
a_k = a / (k + 1 + A)^0.602      c_k = c / (k + 1)^0.101
θ+ = θ + c_k·Δ ;  θ− = θ − c_k·Δ
play a game pair θ+ vs θ−  → result r (e.g. +1 / 0 / −1)
θ ← θ + a_k · r / (c_k · Δ)      (per component)
```
- Cost per iteration is 2 games regardless of p (vs p+1 for finite differences).
- Fishtest picks per-parameter c_i so that θ ± c_i gives "a small, measurable Elo gap (≈ a few Elo)" and uses wins−losses as the gradient signal [V].
- Practical [I]: SPSA needs thousands of game pairs to converge; fine for 3–10 continuous knobs (e.g. eval weights, split-length thresholds) if games are ~1 s. Always confirm the final values with an SPRT vs the old values — SPSA output is not itself proof of gain.

### CLOP [V CPW https://www.chessprogramming.org/CLOP]
Rémi Coulom (2011) "Confident Local Optimization": fits a quadratic regression of win probability over parameter space and samples where it is confident the optimum lies. Good for noisy black-box tuning of a few parameters. Stockfish moved from CLOP to SPSA in 2014 ("Goodbye CLOP, hello SPSA", Gary Linscott, FishCooking, per CPW forum list) [V title].

### Bayesian optimisation / Optuna
Used by CodinGame players (e.g. _H3R0_ #12 SnakeByte tuned 10 heuristic weights with Optuna; jacek #17 used CLOP/Optuna but "ended up mostly hand-picking") — see competitive-bot-culture.md. [I] The risk is the same for all tuners: each objective evaluation is a noisy match of N games; with N = 20 the noise (±150 Elo at 95%) swamps most parameter effects. Shapeshifter's GA used 20 games/individual/generation (battlesnake.md §3) — likely mostly noise-driven selection [I].

### Recommendation for our loop [I]
- Few discrete choices (e.g. split at length 4 vs 5 vs never): just SPRT each against baseline.
- 3–10 continuous weights: SPSA with side-swapped pairs on a fixed map/seed pool, then SPRT confirm.
- Never let the tuner see the hold-out maps (see §6).

---

## 6. Pitfalls: gauntlets, self-play, intransitivity, inflation, overfitting

- **Self-play inflation.** Testing v(n+1) only against v(n) exaggerates gains: the new version is tuned to beat the old version's specific weaknesses. Chess engine testers widely report self-play Elo gains being larger than gains measured against other engines (UNVERIFIED as a verbatim Stockfish statement — a search snippet attributes "self-play testing will tend to over-exaggerate the benefit of a patch" to the Stockfish FAQ, but I could not find the sentence in the FAQ source). Direction is well established; magnitude for our game unknown.
- **Intransitivity (rock-paper-scissors).** A beats B, B beats C, C beats A. Asymptotic Labs saw "strategy collapse" in Battlesnake self-play and fixed it by playing 20% of games against older bests, and gating promotion on a 3,000-game match against the **3 previous bests** [V battlesnake.md §2]. Game-level example for us [I]: an aggressive head-on trader might beat a greedy grower, which beats a turtle, which beats the trader.
- **Gauntlet vs self-play.** A gauntlet = candidate plays a fixed pool of *different* opponents; score = average. Use a pool of: previous best, 2–3 older versions, and "archetype" bots that copy the current ladder meta (e.g. split-2+2-early swarm, greedy grower, turtle/tail-chaser, aggressive head-on hunter). Promote only if the candidate is not worse against any pool member by more than a margin and better on average. CodinGame players note a pool full of similar bots "doesn't reflect real arena" (0x6E0FF, SnakeByte, in competitive-bot-culture.md).
- **Ladder as the final judge.** Our local gauntlet is only a proxy for the real opponents. Public replays let us check whether the local archetypes behave like the real top teams.
- **Ladder noise** [calc]: simulating the ladder rule (K = 96, 5-game battles, score = games won/5) for a team exactly equal to its opponents gives a **rating standard deviation of ~42 Elo** at equilibrium. So ladder rank differences under ~80–100 rating points between neighbours are mostly noise, and a single battle moves ±10 to ±48 (the doc's own examples). Don't react to one ladder battle; use local SPRT for decisions and the ladder as a slow sanity check.
- **Map-pool overfitting.** Known maps + fixed-seed pearls (fixed until 2026-09-25; now seeded per match) make map-specific plans tempting; tournament maps may be unseen (rules digest). Keep a **hold-out set** of maps (and randomly generated maps, if the engine CLI can make them) that the loop never tunes on; require no regression there. Also check per-map results (§4.3).
- **Multiple comparisons.** If the loop tries 20 candidates and keeps the best-looking one from a fixed-n test at p < 0.05, about one "winner" is a false positive by chance. SPRT per candidate controls this per test (α = 5% false-pass each); with many candidates, use tighter α (e.g. 0.01–0.02) or a follow-up confirmation run on fresh seeds [I].
- **Stale baseline drift.** Always compare against the *current* champion on the *same* seed list; re-baseline after each accepted change.

---

## 7. Suggested protocol for the autonomous loop [I]
1. **Build + smoke**: compile, run scenario regression tests (seconds). Fail fast.
2. **Quick screen**: 50 side-swapped pairs (100 games) vs champion on the fixed pool of maps. Kill obvious regressions (score < 0.45) immediately.
3. **SPRT gate** on pair scores vs champion: bounds **[0, 70] Elo early** (≈130 games avg; for 55–70% changes, where we are now), tighten to **[0, 35]** (~530 games) once wins get small, and **[0, 15]** only for cheap-to-run late tuning. Use **non-regression [−20, 0]** for simplifications/deletions. (Replaced 2026-09-24: the loop uses [0, 15] normalized Elo, α=β=0.05, up to 2400 pairs, for every change, since real gains are mostly +5 to +20; since 2026-09-26 no-loss changes use [−15, 0].)
4. **Gauntlet check**: candidate vs pool (older versions + meta archetypes), ≥100 games each; reject if any matchup drops by > ~50 Elo (CI-aware).
5. **Hold-out maps**: no significant regression.
6. **Ladder**: submit; watch 5–10 battles for crashes/timeouts only.
7. Log everything (seeds, commit, per-map, death causes) so the loop's prompts/playbook can learn which kinds of change tend to pass.

---

## Out of scope / leads
- Measure per map whether team A or B (i.e. lower starting ids) wins more often in mirror matches; if so, side swaps are essential.
- Whether the local engine CLI can generate random maps for hold-out testing. (Resolved: our own `mapgen/gen.py` makes them.)
- vdbergh's pentanomial tools https://github.com/vdbergh/pentanomial and the Fishtest SPRT calculator https://tests.stockfishchess.org/sprt_calc could be ported for our pair-score SPRT (not read in detail).
