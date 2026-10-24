"""The decoy check (REFLECT 2026-09-30 09:12 F1, MISSES 103; sig, 30 Sep 03:32: "some teams have begun switching to
much shottier bots even in ranked games to protect their ideas"): a team's latest ranked version that scores below its
best earlier one by more than luck is flagged as a possible decoy, and the readers that fit or average a team's play
(the leader study's gap stats, maintenance.leader_sections; the riser study's per-game means, risers.team_block)
leave its games out and say so.

A version is a submission while the site names them. It has named none since ~19:30 AEST on 28 Sep (online.API_GONE),
so after the last named game a team's ranked games are cut, from the newest back, into blocks of BLOCK games, which
stand in for versions: a switch shows as its block scoring below an earlier one, blurred by a block that spans it.
Scores are raw: a team whose rating climbed meets stronger opponents and scores lower with no decoy, so a flag is a
possible decoy to weigh, not a finding (the study index keeps no opponent ratings to correct for it).

    games: [(submission id or None, time, score)] oldest first, score 1 a win, 0.5 a draw, 0 a loss
"""

from __future__ import annotations

import time

import chance

BLOCK = 20  # games a stand-in version once submissions are unnamed: four ranked battles of five


# The team's versions oldest first as (label, first game's time, scores): named games by submission id (a return to
# an old id joins it), the unnamed after them in BLOCK-game blocks from the newest back (the oldest block may be short).
def versions(games: list[tuple[object, float, float]]) -> list[tuple[str, float, list[float]]]:
    named = max((i for i, (sid, _, _) in enumerate(games) if sid is not None), default=-1)
    out: dict[object, tuple[str, float, list[float]]] = {}
    for sid, at, score in games[:named + 1]:
        if sid is not None:
            out.setdefault(sid, (f"submission {sid}", at, []))[2].append(score)
    unnamed = games[named + 1:]
    blocks = []
    for end in range(len(unnamed), 0, -BLOCK):
        chunk = unnamed[max(0, end - BLOCK):end]
        blocks.append((f"unnamed games from {stamp(chunk[0][1])} to {stamp(chunk[-1][1])}", chunk[0][1],
                       [score for _, _, score in chunk]))
    return sorted(out.values(), key=lambda v: v[1]) + blocks[::-1]


def stamp(at: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(at))


# The check of `games` (sorted by time here), `looks` the teams checked beside it (the p is corrected over them): None
# with fewer than two versions, else {latest, first_at, won, n, best, best_won, best_n, p,
# flagged}: the best earlier version is the one least likely to score this far above the latest by luck
# (chance.rates_p, draws rounded; a 3-of-3 version is no evidence against 13 of 20), p that chance times the earlier
# versions it was picked from, flagged at p <= chance.TRIGGER_P.
def check(games: list[tuple[object, float, float]], looks: int = 1) -> dict | None:
    found = versions(sorted(games, key=lambda g: g[1]))
    if len(found) < 2:
        return None
    (label, first_at, latest), earlier = found[-1], found[:-1]

    def above(version: tuple[str, float, list[float]]) -> float:
        return chance.rates_p(round(sum(version[2])), len(version[2]), round(sum(latest)), len(latest))

    best = min(earlier, key=above)
    p = min(1.0, max(1, looks) * len(earlier) * above(best))
    return {"latest": label, "first_at": first_at, "won": sum(latest), "n": len(latest), "best": best[0],
            "best_won": sum(best[2]), "best_n": len(best[2]), "p": p, "flagged": p <= chance.TRIGGER_P}


# One line saying what check's `result` found, for a report.
def line(result: dict) -> str:
    verdict = ("possible decoy, its games left out of fits" if result["flagged"]
               else f"not below it beyond luck (flagged at p <= {chance.TRIGGER_P})")
    return (f"Decoy check: its latest ranked version ({result['latest']}) won {result['won']:g} of {result['n']} "
            f"games, its best earlier ({result['best']}) {result['best_won']:g} of {result['best_n']}; p = "
            f"{result['p']:.3f} that the best earlier is this far above by luck: {verdict}.")
