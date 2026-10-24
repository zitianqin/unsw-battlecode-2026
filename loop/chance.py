"""How likely chance alone is to give a count: the one place loop code gets it (MISSES 34). A trigger or a claim drawn
from counts says n and its chance from here, and a trigger fires only at p <= TRIGGER_P:

    sign_p(13, 5)          # 0.048: 13 of 18 changed pairs went worse, one-sided
    rate_ci(7, 40)         # (0.08, 0.32): a 95% interval for 7 of 40
    rates_p(23, 59, 13, 74)  # 0.005: 23 of 59 above 13 of 74 by luck alone, one-sided (review M41)
    paired_z([0.5, 1, 0])  # a paired score's mean over its standard error
    cluster_mean([(3, 2), (-1, 1), (0, 1)])  # (0.5, 0.61, 4): a mean per turn from per-game sums, its variance, turns
    corrected_z(18)        # 2.54: the z any of 18 looks must pass for luck alone to give one past it 10% of the time
    strata_gap([([(3, 40), (5, 60)] * 3, [(20, 50), (30, 70)] * 3)])  # (0.08, 0.42, 0.001): one map, 6 games each
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

TRIGGER_P = 0.1  # the loop's alerts and automatic follow-ups fire only when luck alone gives this rarely


# One-sided sign test: the chance of at least `worse` of `worse + better` changed pairs going worse by luck alone.
def sign_p(worse: int, better: int) -> float:
    n = worse + better
    return sum(math.comb(n, k) for k in range(worse, n + 1)) / 2 ** n if n else 1.0


# A 95% Wilson interval for `hits` of `n`: how wide the truth can be around a count. `hits` may be a score's points,
# a draw counting half (online.record).
def rate_ci(hits: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, round(centre - half, 3)), min(1.0, round(centre + half, 3)))


# One-sided chance that `hits_a` of `n_a` is at least this far above `hits_b` of `n_b` by luck alone: Fisher's exact
# test (the hypergeometric tail), since the counts it judges are often a handful of games a size band, where a normal
# approximation reads 2 of 12 against 0 of 25 as 0.018 rather than 0.099. 1.0 when either count is empty.
def rates_p(hits_a: int, n_a: int, hits_b: int, n_b: int) -> float:
    if not n_a or not n_b:
        return 1.0
    hits, n = hits_a + hits_b, n_a + n_b
    top = min(hits, n_a)
    return sum(math.comb(n_a, k) * math.comb(n_b, hits - k) for k in range(hits_a, top + 1)) / math.comb(n, hits)


# A paired sample's mean over its standard error (score differences, one per pair); 0 when too few to say.
def paired_z(diffs: list[float]) -> float:
    n = len(diffs)
    if n < 2:
        return 0.0
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    return mean / math.sqrt(var / n) if var > 0 else 0.0


# A mean per turn from per-game (sum, turns) pairs, with its variance clustered by game, since one game's turns are not
# independent: (mean, variance, turns). The variance is inf with fewer than 2 games holding turns, so one game's turns
# never read as certain; (0.0, inf, 0) with no turns.
def cluster_mean(games: list[tuple[float, int]]) -> tuple[float, float, int]:
    games = [(s, n) for s, n in games if n]
    turns = sum(n for _, n in games)
    if not turns:
        return 0.0, math.inf, 0
    mean, k = sum(s for s, _ in games) / turns, len(games)
    if k < 2:
        return mean, math.inf, turns
    return mean, sum((s - mean * n) ** 2 for s, n in games) / turns ** 2 * k / (k - 1), turns


STRATUM_GAMES = 2  # games with the chance each team needs in a stratum for it to count (strata_gap)
PERMUTATIONS = 2000  # strata_gap's label shuffles at most: its p is never below 1 / (PERMUTATIONS + 1), about 5e-4
PERMUTATION_STOP = 10  # shuffles at least as extreme as the real split that end strata_gap's early (Besag-Clifford)


# Two teams' rates of one action per chance at it, compared within strata (maps), so a different mix of them makes no
# gap, and clustered by game, since the turns of one game are not independent (loop/behaviour_gaps.py). `strata` holds,
# per stratum, each team's games as (hits, chances). Returns (rate_a, rate_b, p): each team's mean rate over the strata
# where both have STRATUM_GAMES+ games with chances, each stratum weighted by n_a n_b / (n_a + n_b) of their chances,
# and the two-sided chance of a gap this large by luck alone. p comes from shuffling which games are whose within each
# stratum (a permutation test of the gap over its standard error), not the normal tail: that tail, fed a variance from
# a few games a map, flagged a gap that isn't there 27 times too often at 2 games a team a map, 6 at 5, 3 at 8 (the
# behaviour gaps branch's review, simulated on 10 maps).
# Seeded, so the same counts give the same p. (0.0, 0.0, 1.0) when no stratum qualifies.
def strata_gap(strata: list[tuple[list[tuple[int, int]], list[tuple[int, int]]]], permutations: int = PERMUTATIONS,
               seed: int = 0) -> tuple[float, float, float]:
    pooled = []
    for a, b in strata:
        a, b = [g for g in a if g[1]], [g for g in b if g[1]]
        if len(a) >= STRATUM_GAMES and len(b) >= STRATUM_GAMES:
            pooled.append((a + b, len(a)))
    if not pooled:
        return 0.0, 0.0, 1.0
    rate_a, rate_b, z = _gap_z([(games[:k], games[k:]) for games, k in pooled])
    shuffle, beyond = random.Random(seed).shuffle, 0
    for done in range(1, permutations + 1):
        split = []
        for games, k in pooled:
            games = games[:]
            shuffle(games)
            split.append((games[:k], games[k:]))
        beyond += abs(_gap_z(split)[2]) >= abs(z) * (1 - 1e-9)
        if beyond >= PERMUTATION_STOP:
            return rate_a, rate_b, beyond / done
    return rate_a, rate_b, (beyond + 1) / (permutations + 1)


# strata_gap's statistic on strata that all qualify: (rate_a, rate_b, gap over its standard error). Each rate's
# variance is the cluster-robust ratio variance, floored at a binomial one (the rate pulled half a hit towards 50%) so
# a stratum at 0% or 100% on both sides doesn't read as certain.
def _gap_z(strata: list[tuple[list[tuple[int, int]], list[tuple[int, int]]]]) -> tuple[float, float, float]:
    def rate(games: list[tuple[int, int]]) -> tuple[float, float, int]:
        chances, k = sum(n for _, n in games), len(games)
        r = sum(h for h, _ in games) / chances
        clustered = sum((h - r * n) ** 2 for h, n in games) / chances ** 2 * k / (k - 1)
        pulled = (r * chances + 0.5) / (chances + 1)
        return r, max(clustered, pulled * (1 - pulled) / chances), chances

    sum_a = sum_b = weights = variance = 0.0
    for a, b in strata:
        ra, rb = rate(a), rate(b)
        w = ra[2] * rb[2] / (ra[2] + rb[2])
        sum_a, sum_b = sum_a + w * ra[0], sum_b + w * rb[0]
        variance += w * w * (ra[1] + rb[1])
        weights += w
    return sum_a / weights, sum_b / weights, (sum_a - sum_b) / math.sqrt(variance)


# One-sided chance of a z-score this low or lower by luck alone (normal approximation).
def low_p(z: float) -> float:
    return NormalDist().cdf(z)


# The z that any one of `looks` scores must pass (at or beyond, one side) for luck alone to give any of them past it
# at most `p` of the time (Bonferroni; conservative when the looks overlap): a report scanning many buckets of one
# result fires on a bucket only past this.
def corrected_z(looks: int, p: float = TRIGGER_P) -> float:
    return -NormalDist().inv_cdf(p / max(looks, 1))
