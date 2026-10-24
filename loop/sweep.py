"""Variant sweep (loop/MISSES.md 16): once per champion, every switch of the champion flipped and every number at 0 and
at 4x (or at an explicit "never" value), each variant alone against the unchanged champion on fresh eval maps. Tunes
never reach these (they keep a value's sign and stay within 4x of its start), so a design choice that is simply wrong
stays in: the `breeder`, v0024 with three split gates at 0 and mergeRound 499, beat v0024 22-16-2. Besides one param at
a time, each decision group in sweep.GROUPS (the values that open one decision fully, like the breeder's three split
gates) gets one variant with all of them at once, since gates in series test flat one at a time (MISSES 21: E0097,
E0101).

A variant plays FIRST_MAPS maps, both sides, against the champion; one scoring at least SECOND_AT plays a second pass:
SECOND_MAPS more maps against the champion (pass 2: eval maps, a farm variant's with FARM_MIX's dead-end and near
shares, method M84) and seat_maps() against driver.ROTATION's seats in turn (pass 3: each seat one eval and one
worldgen map), each seat game paired with the champion's own on that map and side against that seat (BASELINE's, 2 x
seat_maps() games once a sweep). Self-play measures a value against our own code (method M82: E0300 swept 0.65 over 60
and read 0.584 in its SPRT's champion seat, 0.49-0.52 in the others; E0314 swept 0.63, read 0.459 there and lost
-8.2), so a variant is queued as a priority-3 idea (unless an idea already names the param), a hint to test alone by
SPRT (a swept number a hint for which behaviour to build as a rule of what the bot observes, a switch flip tested as it
is: proposal), only when it scores at least QUEUE_AT over its self-play games and its paired seat score (seat_score) is not
below 0.50; the idea prints both halves. A passing variant plays 20 self-play second-pass games and 2 x seat_maps()
against the seats, and each sweep as many more for BASELINE. With games as coin flips, 14% of variants no better than
the champion clear the self-play bars (11% over 60 games before); the seat bar stops some of those. Every game is a
line of <loop home>/sweeps/<champion>/results.jsonl (a seat game with its "seat"); <loop home>/sweep.json holds the
progress (champion, attempt_at, pid, done variants, what each promising one became, error/failures), which health.py
checks.

It is a filler: the upkeep tick (maintenance.tick) starts it in the background only while no evaluation plays on the
remote hosts, and it stops between variants as soon as one plays there (busy: neither a candidate waiting nor the
lock's stages on this Mac stop it), waiting in place until it no longer does (wait_idle), and ends when the champion
moves or a stop marker appears, a later idle tick starting it again. A
tune still playing its games doesn't stop it, nor a late screen (loop/rescreen.py): each plays at half a host, and the
sweep takes what it leaves. Its games go through the frozen referee as filler games (driver.referee with
yielding="filler"): the same load gates and remote hosts, the idle fillers' share of each host (native.host_share: the
whole host, half while a tune or a screen filler asks for slots). While a batch plays, the next one's variants are
exported and built (prepare_ahead), so it starts at once.

    .venv/bin/python loop/sweep.py variants [--commit loop/champion]   # the variants a champion gets, and the skipped
    .venv/bin/python loop/sweep.py run --repo R --state S              # what tick launches
"""

from __future__ import annotations

import argparse
import contextvars
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import home
import meta
import online

REPO = Path(__file__).resolve().parent.parent
FIRST_MAPS = 10  # the first pass: 20 games against the champion
SECOND_MAPS = 10  # the second pass's self-play half, for a variant scoring SECOND_AT: 20 more against the champion
SEAT_SEED = 1000  # pass 3's maps' seeds from the sweep's map_seed, clear of the 30 before them


# Pass 3's maps, its other half (seat_maps: 2 x the seats, so each seat one eval and one worldgen map): each seat
# plays both kinds of ground, whatever the rotation's size (6 seats from 29 Sep, lateking joining: 10 maps gave two
# seats one game each). A sweep keeps the count its maps were made with (run).
def seat_maps() -> int:
    import driver
    return 2 * len(driver.ROTATION)

# Method M84: a farm variant (farm) plays its self-play half with these shares of dead-end and near maps, where farm
# changes act (E0314's farmMinEndLength swept 0.63 and lost -8.2), the rest pass 2's eval maps: 2, 2 and 6 of 10.
FARM_MIX = {"deadend": 0.25, "near": 0.25}
FARM_SEED = 2000  # their seeds from map_seed, each style 1000 on
SECOND_AT = 0.6
QUEUE_AT = 0.56  # over the 40 self-play games
BASELINE = "champion"  # the variant id of the champion's own seat games, which every variant's are paired with
BASELINE_TRIES = 2  # the champion's seat games are played again after a failure, this many times in all
MAX_VARIANTS = 120
BATCH = 8  # the fewest variants a referee call (batch_size)
CALL_ROUNDS = 8  # a referee call's games: this many for every slot the machines have (batch_size)
RETRY_S = 3600  # after a failed run
STALE = 6 * 3600  # a run recorded as running with no variant finished for this long is hung
PAUSED = 3  # `play`'s exit code when busy() stopped it
PLAN_BOUND = 100_000  # bytes sweeps/<champion>/plan.json may hold, rewritten each batch (5 KB on 27 Sep; health)
POINTS = {"a": 1.0, "b": 0.0, "draw": 0.5}

# ------------------------------------------------------------------------------------------------- the variants

LINE = re.compile(r"^\s*(bool|int|double|float)\s+(\w+)\s*=\s*([^;]+);\s*(?://\s*(.*))?$")
SECTION = re.compile(r"^\s*//\s*---\s*(.+?)\s*-{3,}")

# Not swept at all: a local game against the champion can't show what these are for, or they order hard outcomes.
SECURITY = "sonar security against rivals: the variant and the champion are different builds, so neither reads the " \
           "other's sonar either way and a local game can't show it"
SENTINEL = "a sentinel ordering hard outcomes: 0 scores them like ordinary moves and 4x reorders the sentinels"
SKIP = {
    "debugIndicator": "labels dragons in replays; play is unchanged",
    "sonarTeamTag": SECURITY,
    "sonarBuildKey": SECURITY,
    "sonarSipTag": SECURITY,
    "sonarDropStale": "drops replayed sonar records; only a rival replaying our sonar tests it (the replayer entry)",
    "lethalScore": SENTINEL,
    "friendHeadOnScore": SENTINEL,
    "badTradeScore": SENTINEL,
    "doomedScore": SENTINEL,
}
# 0 is illegal or the same as the current value; 4x is still played.
NO_ZERO = {
    "exploreStride": "explore targets step through the board by it (targets.hpp): 0 divides by zero",
    "fastMinResets": "a static_assert needs at least 1 (state.hpp)",
    "enemyHalfLife": "enemy evidence fades by 2^(-age / it) (belief.hpp): 0 gives NaN",
    "foldMinLength": "every dragon is at least 1 long, so 0 plays as the current 1",
}
# Instead of 4x: a value that turns the behaviour off in a 500-round game, or None to leave 4x out.
NOT_4X = {
    # The breeder's value: the merge cascade, its protected king and kingSeeksFeeders act only in the last round.
    # 4x (1800) plays the same game.
    "mergeRound": 499,
    # 4x is past the last round, the same as finalFold = false, which the switch flip plays.
    "foldRound": None,
    # 4x is past the last round, the same as kingBeacon = false.
    "beaconRound": None,
    # The ram search tries every path this long (ram.hpp): 12 steps is 4^12 paths a turn, a timeout, not a strategy.
    "maxRamSteps": None,
}
# Params at 0, where 0 and 4x are both the current value: an "always" and a "never" instead.
AT_ZERO = {
    "riskTradeMinGain": (-100, 100),  # standing beside any enemy head is fine / beside none is
    "attackMinGain": (-100, 100),  # ram any head in reach / no rams
    "evenTradeMinEdge": (-100, 100),  # an even trade whatever the numbers in view (the rammer) / no even trades
    "huntMinEdge": (-100, 100),  # hunt however outnumbered / no hunting
}
# Relevance, for the cap: switches, rounds and gates first, then other integers, then score weights (doubles).
ROUNDS = {"mergeRound", "foldRound", "beaconRound"}
GATES = {
    "splitAtLength", "childLength", "splitEnemyHeadRadius", "splitParentSpace", "splitParentTerritory",
    "splitChildSpace", "trappedTailSpace", "spaceMargin", "minUnitsToTrade", "riskTradeMinGain", "attackMinGain",
    "evenTradeMinEdge", "ramGuardMaxEdge", "maxRamSteps", "protectMinLength", "hunterMaxLength", "huntMaxDist",
    "huntMinEdge", "mergeMinUnits", "mergeRadius", "kingPearlReach", "kingMinLength", "foldMinLength",
    "foldMergeRadius", "maxTargetDist", "spawnSlack", "farmMinPearls", "farmMinEndLength",
}
# An idea's category, from the params.hpp section its param is in (first match).
CATEGORIES = (("split", "split"), ("survival", "safety"), ("head-on", "combat"), ("merge", "endgame"),
              ("fold", "endgame"), ("pearl", "economy"), ("sonar", "sonar"), ("farm", "farm"), ("score", "scoring"))


# Every default in params.hpp text: {name, kind, value, section, comment}, in header order.
def read_params(text: str) -> list[dict]:
    params, section = [], ""
    for line in text.splitlines():
        if heading := SECTION.match(line):
            section = heading.group(1)
        elif found := LINE.match(line):
            kind, name, raw, comment = found.groups()
            raw = raw.strip()
            value = raw == "true" if kind == "bool" else int(raw) if kind == "int" else float(raw)
            params.append({"name": name, "kind": kind, "value": value, "section": section,
                           "comment": (comment or "").strip()})
    return params


def literal(value: bool | int | float) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def tier(param: dict) -> int:
    if param["kind"] == "bool" or param["name"] in ROUNDS | GATES:
        return 0
    return 1 if param["kind"] == "int" else 2


# Decision groups: each the values that open one decision fully, every gate in series that it checks at once, read
# from champion v0028's params.hpp (1c225be, 2026-09-26). One change at a time never sees gates in series: E0097
# relaxed one split gate (-3.0) where E0101 turned three off (+19.3) (MISSES 21). A feature's own switch is left out:
# turning it off is a single variant already, and it would make the gates opened beside it moot. Directions follow
# each param's comment: a "needs at least" gate opens at 0 (or its AT_ZERO "always"), a reach or radius at 4x.
GROUPS = {
    "split room (the breeder's gates, E0101)": {"splitParentSpace": 0, "splitParentTerritory": 0, "splitChildSpace": 0},
    "split whenever long enough": {"splitEnemyHeadRadius": 0, "splitParentSpace": 0, "splitParentTerritory": 0,
                                   "splitChildSpace": 0},
    "ram every head in reach": {"minUnitsToTrade": 0, "attackMinGain": -100, "evenTradeMinEdge": -100},
    "stand beside any head": {"riskTradeMinGain": -100, "outnumberedRamGuard": False},
    "hunt any longer head": {"hunterMaxLength": 20, "huntMaxDist": 24, "huntMinEdge": -100},
    "merge whenever a teammate is near": {"mergeMinUnits": 0, "mergeRadius": 8},
    "fold merges wherever it can": {"foldMinLength": 1, "foldMergeRadius": 16, "foldMergesOnlyKing": False,
                                    "foldIgnoresMinUnits": True, "foldIgnoresEnemy": True},
    "enter any farm pocket": {"farmMinPearls": 0, "farmMinEndLength": 0},
    "chase every pearl": {"maxTargetDist": 56, "spawnSlack": 8, "yieldToEnemyCloser": False},
    "any dragon can be king": {"kingMinLength": 1, "kingReportAge": 40},
}


# The group variants GROUPS gives params.hpp's `params`, as (order, variant), and the groups skipped with the reason: a
# param the champion doesn't have, a group already fully open, or one whose only change is a single variant already.
# A variant changes only its values that differ from the champion's.
def groups(params: list[dict], singles: set[str]) -> tuple[list[tuple[float, dict]], list[dict]]:
    by_name = {p["name"]: (order, p) for order, p in enumerate(params)}
    found, skipped = [], []
    for name, values in GROUPS.items():
        if missing := [param for param in values if param not in by_name]:
            skipped.append({"param": name, "reason": f"group needs {', '.join(missing)}, not in this params.hpp"})
            continue
        typed = {param: value if isinstance(value, bool) or by_name[param][1]["kind"] == "bool" else
                 int(value) if by_name[param][1]["kind"] == "int" else float(value) for param, value in values.items()}
        change = {param: value for param, value in typed.items() if value != by_name[param][1]["value"]}
        ids = [f"{param}={literal(value)}" for param, value in change.items()]
        if not change:
            skipped.append({"param": name, "reason": "group already fully open in this champion"})
        elif len(change) == 1 and ids[0] in singles:
            skipped.append({"param": name, "reason": f"group's only change, {ids[0]}, is a single variant already"})
        else:
            first = min(by_name[param][0] for param in change)
            found.append((first - 0.5, {"id": "+".join(ids), "group": name, "params": change,
                                        "starts": {param: by_name[param][1]["value"] for param in change},
                                        "section": params[first]["section"], "comment": ""}))
    return found, skipped


# The sweep's variants for params.hpp text, most relevant first and at most `cap`, and what was left out and why:
# {"variants": [...], "skipped": [{param, value?, reason}]}. A single variant is {id, param, value, start, kind,
# section, comment, params}; a group variant (groups) is {id, group, params, starts, section, comment}, sorted into
# tier 0 just ahead of its first member's place in params.hpp, whatever that member's own tier. Every variant's
# `params` is what its bot changes.
def variants(text: str, cap: int = MAX_VARIANTS) -> dict:
    found, skipped = [], []
    params = read_params(text)
    for order, param in enumerate(params):
        name, start = param["name"], param["value"]
        if name in SKIP:
            skipped.append({"param": name, "reason": SKIP[name]})
            continue
        if param["kind"] == "bool":
            values = [not start]
        elif start == 0:
            values = list(AT_ZERO.get(name, ()))
            if not values:
                skipped.append({"param": name, "reason": "at 0, where 0 and 4x are both the current value; give it "
                                                         "an always and a never in sweep.AT_ZERO"})
        else:
            values = []
            if name in NO_ZERO:
                skipped.append({"param": name, "value": 0, "reason": NO_ZERO[name]})
            else:
                values.append(0 if param["kind"] == "int" else 0.0)
            extreme = NOT_4X.get(name, 4 * start)
            if extreme is None:
                skipped.append({"param": name, "value": 4 * start, "reason": "4x: see sweep.NOT_4X"})
            else:
                values.append(int(extreme) if param["kind"] == "int" else float(extreme))
        for value in values:
            found.append((tier(param), order, {"id": f"{name}={literal(value)}", "param": name, "value": value,
                                               "start": start, "kind": param["kind"], "section": param["section"],
                                               "comment": param["comment"], "params": {name: value}}))
    grouped, unplayed = groups(params, {variant["id"] for _, _, variant in found})
    found += [(0, order, variant) for order, variant in grouped]
    skipped += unplayed
    found.sort(key=lambda row: row[:2])
    for _, _, variant in found[cap:]:
        skipped.append({"param": variant.get("param") or variant["id"],
                        **({"value": variant["value"]} if "value" in variant else {}),
                        "reason": f"past the cap of {cap} variants (less relevant: see sweep.tier)"})
    return {"variants": [variant for _, _, variant in found[:cap]], "skipped": skipped}


def category(variant: dict) -> str:
    section = variant["section"].lower()
    return next((name for key, name in CATEGORIES if key in section), "strategy")


# ------------------------------------------------------------------------------------------------- scoring

def results(folder: Path) -> list[dict]:
    path = folder / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# The rows of `rows` for one variant, first pass, second pass or both (`passes`); build failures excluded.
def games(rows: list[dict], variant_id: str, passes: tuple[int, ...] = (1, 2)) -> list[dict]:
    return [r for r in rows if r.get("variant") == variant_id and r.get("pass") in passes and "winner" in r]


def score(rows: list[dict]) -> float:
    return sum(POINTS[r["winner"]] for r in rows) / len(rows) if rows else 0.0


# Whether a variant failed to build or one of its games couldn't be played (a timeout, the engine exiting nonzero):
# `play` then records a build_error or game_error row instead of its games, and it is done unscored.
def failed(rows: list[dict], variant_id: str) -> bool:
    return any(r.get("variant") == variant_id and (r.get("build_error") or r.get("game_error")) for r in rows)


# The (variant, pass) games still to play: the champion's seat games (BASELINE) while an undecided variant earned a
# second pass and they are owed (baseline_owed), then second passes (they are the promising ones: pass 2, then pass
# 3), then first passes in relevance order. A variant is done when it is decided (in `considered`, queue's record, so
# one decided before the seat half plays none), failed (see failed), scored under SECOND_AT or played every pass.
def todo(listing: list[dict], rows: list[dict], considered: dict | None = None,
         seat_n: int | None = None) -> list[tuple[dict, int]]:
    second, first, seated, seat_n = [], [], False, seat_n or seat_maps()
    for variant in listing:
        if variant["id"] in (considered or {}) or failed(rows, variant["id"]):
            continue
        played = games(rows, variant["id"], (1,))
        if len(played) < 2 * FIRST_MAPS:
            first.append((variant, 1))
        elif score(played) >= SECOND_AT:
            seated = True
            second += [(variant, n) for n, maps in ((2, SECOND_MAPS), (3, seat_n))
                       if len(games(rows, variant["id"], (n,))) < 2 * maps]
    return [({"id": BASELINE}, 3)] * (seated and baseline_owed(rows, seat_n)) + second + first


# Whether the champion's seat games (BASELINE) are still to play: not all in, and failed fewer than BASELINE_TRIES
# times. A variant's seat score waits for them (decide).
def baseline_owed(rows: list[dict], seat_n: int | None = None) -> bool:
    tries = sum(r.get("variant") == BASELINE and bool(r.get("game_error") or r.get("build_error")) for r in rows)
    return tries < BASELINE_TRIES and len(games(rows, BASELINE, (3,))) < 2 * (seat_n or seat_maps())


# Whether `variant` changes a farm param (named farm*, or in params.hpp's farm section): its pass 2 plays FARM_MIX.
def farm(variant: dict) -> bool:
    return "farm" in variant.get("section", "").lower() or any(name.startswith("farm") for name in variant["params"])


# The plan's map list a variant's pass `number` plays: "2f" for a farm variant's pass 2, else the pass's own.
def maps_key(variant: dict, number: int) -> str:
    return "2f" if number == 2 and farm(variant) else str(number)


# The seat each of pass 3's maps plays (driver.ROTATION in turn), {map path: seat}: each seat one of each kind. A map
# the champion's own seat games (BASELINE's, in `rows`) already played keeps the seat they played, while it is still in
# the rotation, so a sweep that outlives a rotation change still pairs every game (29 Sep: lateking's joining moved
# the seat of every worldgen map of a 5-seat sweep).
def seats(maps: list[str], rows: list[dict] = ()) -> dict[str, str]:
    import driver
    kept = {r["map"]: r["seat"] for r in rows if r.get("variant") == BASELINE and r.get("pass") == 3
            and r.get("seat") in driver.ROTATION}
    return {game_map: kept.get(game_map) or driver.ROTATION[i % len(driver.ROTATION)]
            for i, game_map in enumerate(maps)}


# A variant's pass-3 score against the rotation's seats, paired: 0.5 plus half its mean points over the champion's
# (BASELINE's) on the same map and side against the same seat, as (score, pairs); None without a pair. Pooled over the
# seats: the queued idea names none, since a named seat is a subgroup its judge would count as declared (M82).
def seat_score(rows: list[dict], variant_id: str) -> tuple[float, int] | None:
    key = lambda r: (r["map"], r["a_side"], r.get("seat"))  # noqa: E731
    base = {key(r): POINTS[r["winner"]] for r in games(rows, BASELINE, (3,))}
    diffs = [POINTS[r["winner"]] - base[key(r)] for r in games(rows, variant_id, (3,)) if key(r) in base]
    return (0.5 + sum(diffs) / (2 * len(diffs)), len(diffs)) if diffs else None


def done(listing: list[dict], rows: list[dict], considered: dict | None = None, seat_n: int | None = None) -> list[str]:
    waiting = {variant["id"] for variant, _ in todo(listing, rows, considered, seat_n)}
    return [variant["id"] for variant in listing if variant["id"] not in waiting]


# M59: a kept change can leave older params deciding nothing (since E0239 riskTradeMinGain, outnumberedRamGuard and
# ramGuardMaxEdge; E0035's portal fallback never fires), and ideas and re-checks kept targeting them. A one-param
# variant is `dead` when its first pass played every map's two games as exact mirrors of each other
# (official.changed_games): the same moves as the champion, as far as FIRST_MAPS maps show. Deadness is per
# direction, not per param: riskTradeMinGain=-100 is dead while =100 changed 4 games (0aa3c59's sweep), and
# 0e7c458's trappedTailSpace=32 is dead while =0 changes play. {variant id: {param, value, start, direction}}.
def dead(listing: list[dict], rows: list[dict]) -> dict[str, dict]:
    import official
    found = {}
    for variant in listing:
        first = games(rows, variant["id"], (1,))
        if "param" in variant and not failed(rows, variant["id"]) and len(first) == 2 * FIRST_MAPS \
                and len({r["map"] for r in first}) == FIRST_MAPS and official.changed_games(first)["games"] == 0:
            found[variant["id"]] = {"param": variant["param"], "value": variant["value"], "start": variant["start"],
                                    "direction": direction(variant["value"], variant["start"])}
    return found


# Which way a variant moves its param from the champion's value: "on"/"off" for a switch, else "up"/"down".
def direction(value: bool | int | float, start: bool | int | float) -> str:
    if isinstance(start, bool):
        return "on" if value else "off"
    return "up" if value > start else "down"


# The latest sweep's dead variants, {param: {direction: (variant id, start)}}, with the champion it swept. A record
# from before directions were kept ({id: param}) gives none.
def dead_params(state: Path) -> tuple[str | None, dict[str, dict[str, tuple[str, bool | int | float]]]]:
    record, found = read(state), {}
    for variant_id, entry in (record.get("dead") or {}).items():
        if isinstance(entry, dict):
            found.setdefault(entry["param"], {})[entry["direction"]] = (variant_id, entry["start"])
    return record.get("champion"), found


# stats.md's section on dead variants (M59).
def dead_lines(state: Path) -> list[str]:
    champion, found = dead_params(state)
    if not champion:
        return []
    listed = [f"- {variant_id} ({way} from {literal(start)})" for _, ways in sorted(found.items())
              for way, (variant_id, start) in sorted(ways.items())]
    return ["", f"## Dead variants: params moved one way that played the champion's moves (variant sweep of "
                f"{champion[:7]}, M59)",
            "", f"Each played every one of its {FIRST_MAPS} first-pass maps move for move as the champion (both sides "
            "exact mirrors), so moving the param that way decides nothing there (the other way may still act): an "
            "idea or re-check moving it that way should first show where it acts.", ""] + (listed or ["- none so far"])


DOWN_WORDS = ("lower", "lowers", "lowering", "reduce", "reduces", "reducing", "decrease", "decreases", "decreasing",
              "cut", "cuts", "cutting", "shrink", "shrinks", "shrinking", "drop", "drops", "dropping")
UP_WORDS = ("raise", "raises", "raising", "increase", "increases", "increasing", "grow", "grows", "growing", "widen",
            "widens", "widening", "lengthen", "lengthens", "lengthening")
OFF_WORDS = ("disable", "disables", "disabling", "remove", "removes", "removing")
ON_WORDS = ("enable", "enables", "enabling")


# The directions an idea's text moves param `name` (from the sweep's start value), or an empty set when it can't tell: an
# explicit value after the name ("name to 100", "name = 0", "name: false", "name from 3 to 1"), a verb just before
# it ("lower P.<name>", "raise the <name>"), or a switch's "off"/"on" just after it. A tune moves both ways.
def moved(text: str, name: str, start: bool | int | float) -> set[str]:
    ways = set()
    value = r"(-?\d+(?:\.\d+)?|true|false)"
    for match in re.finditer(rf"\b{name}\b\s*(?:=|:|->|→|\bto\b|\bfrom\b\s*\S+\s*(?:to|->|→))\s*{value}", text,
                             re.IGNORECASE):
        raw = match.group(1).lower()
        target = raw == "true" if raw in ("true", "false") else float(raw)
        if isinstance(start, bool) and isinstance(target, bool):
            ways.add(direction(target, start))
        elif not isinstance(start, bool) and not isinstance(target, bool) and target != start:
            ways.add(direction(target, start))
    before = r"\b(\w+)\s+(?:the\s+)?(?:P\.)?" + name + r"\b"
    for match in re.finditer(before, text, re.IGNORECASE):
        word = match.group(1).lower()
        if isinstance(start, bool):
            ways |= {"off"} if word in OFF_WORDS else {"on"} if word in ON_WORDS else set()
        else:
            ways |= {"down"} if word in DOWN_WORDS else {"up"} if word in UP_WORDS else set()
    if isinstance(start, bool):
        for match in re.finditer(rf"\b{name}\b\s+(off|on)\b", text, re.IGNORECASE):
            ways.add(match.group(1).lower())
    return ways


# The claim's note (M59) for an idea that moves every params.hpp name of its text only in directions whose sweep
# variant is dead, else "": a note to its coder, never a block. A name whose direction the text doesn't give (a
# tune moves both ways) counts only when every way it can move is dead (both "up" and "down"; a switch has one).
# `names` is every params.hpp name (read_params).
def dead_note(state: Path, idea: dict, names: list[str]) -> str:
    champion, found = dead_params(state)
    prose = f"{idea.get('title', '')} {idea.get('hypothesis', '')}"
    tune = json.dumps(idea.get("tune") or {})
    named = [n for n in names if re.search(rf"\b{n}\b", f"{prose} {tune}")]
    if not named or not all(n in found for n in named):
        return ""
    hits = []
    for name in named:
        ways, start = found[name], next(iter(found[name].values()))[1]
        asked = set() if re.search(rf"\b{name}\b", tune) else moved(prose, name, start)
        if not asked:
            asked = {"on", "off"} & set(ways) if isinstance(start, bool) else {"up", "down"}
        if not asked <= set(ways):
            return ""
        hits.append((name, sorted(asked)))
    moves = "; ".join(f"{name} {'/'.join(ways)} ({', '.join(found[name][w][0] for w in ways)})" for name, ways in hits)
    return (f"\n\n**Dead variants (M59).** Every param this idea names moves only in a direction whose variant "
            f"played the champion's moves exactly on the variant sweep of {champion[:7]} ({moves}): moving it that "
            "way decided nothing on those maps. Before building on it, check that the rule it feeds still acts that "
            "way (count its firings in real games); if it doesn't, say so and aim at what decides instead. A note, "
            "not a block.")


# "W-L-D" and the per-map line of a variant's games: its points of 2 on each map (both sides), and how many maps it won
# on both sides, split, and lost on both.
def per_map(rows: list[dict]) -> tuple[str, str]:
    wins = sum(r["winner"] == "a" for r in rows)
    losses = sum(r["winner"] == "b" for r in rows)
    points: dict[str, float] = {}
    for row in rows:
        name = Path(row["map"]).stem
        points[name] = points.get(name, 0.0) + POINTS[row["winner"]]
    both = sum(p == 2 for p in points.values())
    none = sum(p == 0 for p in points.values())
    listed = ", ".join(f"{name} {p:g}" for name, p in sorted(points.items()))
    return (f"{wins}-{losses}-{len(rows) - wins - losses}",
            f"won both sides on {both} of {len(points)} maps, lost both on {none}, split the rest ({listed})")


# The idea a variant that beat the champion becomes: one attributable change (or one decision group's gates, all off
# together), a hint for an SPRT. A variant of switches alone (a bool flipped) is a switch test, played as it is: a flip
# is a behaviour choice, and switches are never tuned. A swept number (0, 4x, a never) is a hint for which behaviour
# to build as a rule of what the bot observes, not a value to set (sig, 29 Sep: no magic numbers; I850 set
# kingReportAge to 40 from 12 as it was swept).
def proposal(variant: dict, rows: list[dict], champion: str, swept: int, folder: Path, seat_n: int) -> dict:
    first, both = games(rows, variant["id"], (1,)), games(rows, variant["id"])
    second = games(rows, variant["id"], (2,))
    seat, pairs = seat_score(rows, variant["id"]) or (0.5, 0)
    record, maps = per_map(both)
    starts = variant.get("starts") or {variant["param"]: variant["start"]}
    change = ", ".join(f"{name} to {literal(value)} (from {literal(starts[name])})"
                       for name, value in variant["params"].items())
    group = len(variant["params"]) > 1
    numbers = [name for name, value in variant["params"].items() if not isinstance(value, bool)]
    switches = [name for name in variant["params"] if name not in numbers]
    if group:
        what = (f"the gates and switches of one decision ({variant['group']} in params.hpp) all off at once, "
                f"{', '.join(f'{n} = {literal(v)}' for n, v in variant['params'].items())} (their values are "
                f"{', '.join(literal(v) for v in starts.values())}), against the unchanged champion. Each alone may "
                "test flat: gates in series each still block what the others let through (E0097 relaxed one split "
                "gate, -3.0; E0101 turned three off, +19.3; MISSES 21).")
    else:
        comment = f" Its comment in params.hpp: {variant['comment'].rstrip('. ')}." if variant["comment"] else ""
        what = (f"only {variant['param']} = {literal(variant['value'])} (its value is {literal(variant['start'])}) "
                f"against the unchanged champion.{comment}")
    if numbers:
        build = (f"Turn the sweep's hint {change}{' together' if group else ''} into a rule of what the bot observes",
                 f"The swept {'numbers are hints' if len(numbers) > 1 else 'number is a hint'} for which behaviour to "
                 f"build, not {'values' if len(numbers) > 1 else 'a value'} to set ({', '.join(numbers)}): 0, 4x and a "
                 "never are the sweep's probes, so a gain says the decision they gate is wrong somewhere, not that the probe is "
                 "right. Find where the variant's moves part from the champion's in its games and what the bot could "
                 "see there, then build that behaviour as a rule over what it observes (map size, rounds left, "
                 "lengths, distances, counts, the opponent's behaviour; a param as its formula, 'P.x = <...>'), "
                 "behind its own switch, the champion's numbers kept otherwise. Where the gain is the gate being off "
                 "altogether, removing the gate behind a switch is that rule. A swept number set as it is needs a "
                 "Constants: reason of loop/PROMPT.md's kinds in the commit."
                 + (f" Its switches ({', '.join(switches)}) are behaviour choices and flip as they are."
                    if switches else "")
                 + " Test it alone by SPRT (the champion with just this behaviour changed).")
    else:
        build = (f"Set {change}{' together' if group else ''}",
                 f"{'These are switches' if group else 'This is a switch'}: a flip is a behaviour choice, not a tuned "
                 "number (switches are never tuned), so test it as it is, alone by SPRT (the champion with just "
                 f"{'these switches' if group else 'this switch'} flipped).")
    return {
        "category": category(variant),
        "priority": 3,  # below evidence-backed ideas: about 1 in 10 neutral variants passes by chance
        "title": (f"{build[0]}: it scored {score(both):.2f} against the champion over {len(both)} variant-sweep games "
                  f"and {seat:.2f} paired against the rotation's seats"),
        "hypothesis": (
            f"The variant sweep of champion {champion[:7]} (loop/sweep.py, MISSES 16) played the champion with {what} "
            f"The variant scored {score(both):.3f} over {len(both)} games on {len(both) // 2} fresh eval maps, both "
            f"sides (W-L-D {record}; first pass {score(first):.2f} over {len(first)} games, second pass's self-play "
            f"half {score(second):.2f} over {len(second)}). Per map: {maps}. Its second pass's other half, against the "
            f"rotation's seats in turn on {seat_n} maps (half of them the other generator's), scored {seat:.3f} "
            f"over {pairs} games paired with the champion's own on the same map and side. The games are in "
            f"{folder / 'results.jsonl'}. "
            f"This is a hint from a small sample, chosen as one of the best of {swept} variants, not a measured "
            f"gain (about 1 in 10 variants no better than the champion clear the thresholds by chance). {build[1]} "
            f"Most of the sweep is self-play on eval maps, so the hint measures the value against our own code on our own "
            f"generator's maps (method M82: E0300's sweep 0.65 held only in its SPRT's champion seat, 0.584 over 83 "
            f"trials, with 0.49-0.52 in the other seats). Declared before the SPRT: its result is read with the "
            f"champion seat apart, and a keep needs the other seats pooled not below level (judge.self_play_only). If the SPRT's "
            f"per-opponent scores pull apart, make the behaviour depend on something the bot can see rather than a "
            f"new constant."
            + (" If it is kept, a follow-up can test which of its parts carry it." if group else "")),
    }


# A todo or in-flight idea already changing `param`: its title names it, or it tunes it.
def changes(idea: dict, param: str) -> bool:
    return bool(re.search(rf"\b{re.escape(param)}\b", idea.get("title", ""))) or \
        param in (idea.get("tune") or {}).get("params", [])


# An idea already making the change of `params`: one changing every one of them (an idea changing only some tests
# something else: gates in series pass together or not at all), todo or in flight, or one whose experiment was tested
# against `champion` (its recorded result has SPRT games on that base). "I202" or "I202 (E0095)", or None.
def equivalent(state: Path, ideas: list[dict], params: list[str], champion: str) -> str | None:
    for idea in ideas:
        if (idea.get("status") == "todo" or str(idea.get("status", "")).startswith("trying:")) \
                and all(changes(idea, param) for param in params):
            return idea["id"]
    path = state / "results.jsonl"
    for line in path.read_text().splitlines() if path.exists() else []:
        row = json.loads(line) if line.strip() else {}
        if row.get("base") == champion and (row.get("sprt") or {}).get("score") is not None \
                and all(changes(row.get("idea") or {}, param) for param in params):
            return f"{row['idea'].get('id')} ({row.get('id')})"
    return None


# Decides every variant that has played every pass and isn't decided yet: queues it as an idea when it scored QUEUE_AT
# in self-play and its seat score (seat_score) is not below 0.50, unless an equivalent idea exists. Records each
# decision in record["considered"]; returns the new ids.
def queue(state: Path, record: dict, listing: list[dict], rows: list[dict], folder: Path,
          seat_n: int | None = None) -> list[str]:
    record.setdefault("considered", {})
    added: list[str] = []
    with meta.locked(state / "ideas.jsonl"):  # read, decide and append as one step, so no claim or review interleaves
        ideas_path = state / "ideas.jsonl"
        ideas = [json.loads(line) for line in ideas_path.read_text().splitlines() if line.strip()] \
            if ideas_path.exists() else []
        for variant in listing:
            decide(state, record, variant, rows, listing, folder, ideas, added, seat_n or seat_maps())
    record.setdefault("queued", []).extend(added)
    return added


# queue's decision on one variant, under the backlog lock: records it in record["considered"], and appends its idea to
# the backlog (and to `ideas` and `added`) when it is queued.
def decide(state: Path, record: dict, variant: dict, rows: list[dict], listing: list[dict], folder: Path,
           ideas: list[dict], added: list[str], seat_n: int) -> None:
    considered = record["considered"]
    both = games(rows, variant["id"])
    if variant["id"] in considered or len(games(rows, variant["id"], (2,))) < 2 * SECOND_MAPS \
            or len(games(rows, variant["id"], (3,))) < 2 * seat_n:
        return
    if score(both) < QUEUE_AT:
        considered[variant["id"]] = f"scored {score(both):.3f} over {len(both)} games, under {QUEUE_AT}"
        return
    if baseline_owed(rows, seat_n):  # the champion's seat games are still to play (todo): no pairs yet is not a result
        return
    seats = seat_score(rows, variant["id"])
    if seats is None or seats[0] < 0.5:
        considered[variant["id"]] = (f"scored {score(both):.3f} in self-play but " + (
            f"{seats[0]:.3f} against the rotation's seats over {seats[1]} paired games, below 0.50 (M82)" if seats
            else "has no seat games paired with the champion's (M82)"))
        return
    if same := equivalent(state, ideas, list(variant["params"]), record["champion"]):
        considered[variant["id"]] = f"scored {score(both):.3f}; {same} already changes {', '.join(variant['params'])}"
        return
    idea = proposal(variant, rows, record["champion"], len(listing), folder, seat_n)
    valid = meta.valid_proposals([json.dumps(idea)], 1, options=False)
    if not valid:
        considered[variant["id"]] = f"scored {score(both):.3f}; its idea failed validation"
        return
    ids = meta.append_ideas_locked(state, valid, f"sweep:{record['champion'][:7]}")
    considered[variant["id"]] = f"scored {score(both):.3f}; queued as {ids[0]}"
    ideas.append({**valid[0], "id": ids[0], "status": "todo"})
    added += ids


# ------------------------------------------------------------------------------------------------- scheduling

def read(state: Path) -> dict:
    return online.read(state / "sweep.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "sweep.json", record)


# Why the sweep (or another filler, loop/baselines.py, loop/advmaps.py) must not play now, or None: a stop marker, an
# evaluation playing on the remote hosts (its SPRT with the screen after it, or its identical check: driver.host_stage),
# or the champion moved from `champion` (when given). Asked before every start (a tick's launch, a run's next batch or
# stage, a batch's build) and between a built batch's variants or maps, which then stops there, its games in flight
# keeping theirs. Neither a candidate waiting for evaluation, the lock's stages that play only on this Mac or nowhere
# (setup, merge turn, bot tests and maps), nor its CPU gates (here and on this Mac's platform's hosts, where the
# players step back alone: native.hosts_wanted_now) pause the fillers: until 2026-09-27 the first two did, and E0180's
# test-fix turn and local stages (21:02-21:09) left every remote host idle (the throughput build). A filler's games
# never play on this Mac while the lock is held (native.game_slots), which stays the evaluation's; its builds here are
# two at a time, at nice 20 like everything a filler does (meta.spawn). A tune still playing its games is filler
# work too, so it doesn't stop them.
def busy(repo: Path, champion: str | None = None) -> str | None:
    import driver  # lazily: the driver imports maintenance, whose tick imports this
    import native
    if driver.stop_requested():
        return "a stop marker is set"
    # The champion before the hosts, so a run for an old one ends rather than waiting in place (wait_idle) through a
    # host stage; a failed read ("") is no move, since a waiting run asks every WAIT_S (baselines.halted likewise).
    if champion and (current := meta.champion_commit(repo)) and current != champion:
        return "the champion moved"
    if native.evaluation_needs_hosts():
        return ON_HOSTS
    return None


ON_HOSTS = "an evaluation is playing on the hosts"  # busy()'s reason that a filler waits out in place (wait_idle)
WAIT_S = 10  # how often a filler waiting in place asks busy() again: players re-read the marker as often (native)


# What a filler's run (this sweep's, loop/baselines.py's, loop/advmaps.py's) does where it asks `ask()` (busy's
# reasons: busy, or advmaps.halt) before its next batch or stage: None to play on, else the reason that ends the run (a
# stop marker, the champion moved). While the only reason is ON_HOSTS it waits in place, asking every WAIT_S, its
# record (`record(**changes)`, the filler's own update) `paused` meanwhile with its pid kept, so no tick starts a
# second run and health.py's hung checks pass it over, and progress_at set as it resumes. Until 2026-09-30 a filler
# exited there and only a later upkeep tick started it again, and a tick can take 17 min (03:19-03:37 on 30 Sep): over
# the 24 h to 03:30 the first filler game after a host stage came 0-11 min after it ended, 69 minutes with every filler
# still to start and 73% of the slots idle (utilisation.jsonl; MISSES 50).
def wait_idle(ask, record) -> str | None:
    if (reason := ask()) != ON_HOSTS:
        return reason
    record(paused=reason, paused_at=time.time())
    print(f"waiting in place: {reason}", flush=True)
    while (reason := ask()) == ON_HOSTS:
        time.sleep(WAIT_S)
    if reason is None:
        record(paused=None, progress_at=time.time())
        print("resumed", flush=True)
    return reason


# At a tick, a filler's record (sweep.json, baselines.json, advmaps.json) that is paused and still held back, now by
# `reason`: its `paused` is rewritten when the reason changed (paused_at with it), so health.py and evalline never show
# a reason that no longer holds (advmaps showed "the variant sweep hasn't finished" from 22:56 on 27 Sep until past
# 01:30, though the sweep finished at 00:03). A launch clears it. True when it changed the record.
def repause(record: dict, reason: str, now: float) -> bool:
    if not record.get("paused") or record.get("pid") or record["paused"] == reason:
        return False
    record.update(paused=reason, paused_at=now)
    return True


# The upkeep tick's step: notes a run that died without finishing, then starts one in the background when the
# champion has no finished sweep, the machines are idle (busy() is None) and no failure is under RETRY_S old. A late
# screen doesn't hold it back: until 2026-09-29 it did, and v0070's (E0350, from 18:42) left half of every host idle
# through E0349's stages on this Mac (the late screen keeps its half, native.host_share). Never waits for the run.
# Records `idle_at` whenever one could start but for a recent failure, for health.py. Returns a line to log, or None.
def tick(repo: Path, state: Path, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    meta.reap()  # a run that finished clears its own pid, so only this reaps it
    if not (state / "ideas.jsonl").exists():
        return None
    with meta.locked(state / "sweep"):
        record = read(state)
        if pid := record.get("pid"):
            if meta.alive(pid):  # reaping it if this process launched it
                return None  # health.py alerts when it stops making progress
            record = failure(record, "the run process exited without finishing; see its run.log", now)
            save(state, record)
        champion = meta.champion_commit(repo)
        if not champion:
            return None
        if record.get("champion") != champion:
            record = {"champion": champion}
            save(state, record)  # before busy(), which can raise: health.py then sees the step ran for this champion
        if record.get("finished_at"):
            return None
        if reason := busy(repo):
            repause(record, reason, now)
            save(state, record)
            return None
        record["idle_at"] = now
        if now - record.get("failed_at", 0) < RETRY_S:
            save(state, record)
            return None
        # Saved before anything that can fail, so a failure is retried after RETRY_S, not at every tick.
        record.update(attempt_at=now, progress_at=now, paused=None)
        save(state, record)
        try:
            import maintenance
            record.setdefault("toolchain", maintenance.toolchain(state))  # frozen for the sweep, like an experiment's
            folder = state / "sweeps" / champion
            folder.mkdir(parents=True, exist_ok=True)
            record["pid"] = launch(repo, state, folder)
        except Exception as error:
            save(state, failure(record, f"could not start the run: {type(error).__name__}: {error}", now))
            return f"variant sweep: could not start the run: {error}"
        record.setdefault("started_at", now)
        save(state, record)
        return f"variant sweep of {champion[:7]} launched in the background: {folder}"


def launch(repo: Path, state: Path, folder: Path) -> int:
    return meta.spawn([sys.executable, str(Path(__file__).resolve()), "run", "--repo", str(repo), "--state",
                       str(state)], folder, folder / "run.log")


def failure(record: dict, error: str, now: float) -> dict:
    return {**record, "pid": None, "error": error[-1200:], "failed_at": now,
            "failures": record.get("failures", 0) + 1}


# Rewrites sweep.json's record for `champion` with `changes`, unless a newer champion's sweep has replaced it.
def update(state: Path, champion: str, **changes) -> dict:
    with meta.locked(state / "sweep"):
        record = read(state)
        if record.get("champion") == champion:
            record.update(changes)
            save(state, record)
        return record


# Fixed per champion, and not the SPRT's shared maps (seeded by the champion's hash alone): map i uses seed + i.
def map_seed(champion: str) -> int:
    return int(hashlib.sha256(f"sweep:{champion}".encode()).hexdigest()[:7], 16)


def champion_params(repo: Path, commit: str) -> str:
    return subprocess.run(["git", "show", f"{commit}:bot/params.hpp"], cwd=repo, check=True, text=True,
                          capture_output=True).stdout


def bot_name(variant_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", variant_id.replace("=", "_"))


# The detached run `tick` launches: plays batch_size variants a referee call until every variant is done (then records
# finished_at), or until busy() says to stop (paused, resumed by a later tick; an evaluation on the hosts it waits out
# in place, wait_idle), queueing ideas as variants finish.
def run(repo: Path, state: Path) -> None:
    import driver
    record = read(state)
    champion = record["champion"]
    folder = state / "sweeps" / champion
    token = driver.RUNTIME_PYTHON.set(record.get("toolchain", {}).get("python"))
    ahead = None  # the thread preparing the next batch's variants (prepare_ahead)
    try:
        listing = variants(champion_params(repo, champion))
        (folder / "variants.json").write_text(json.dumps(listing, indent=1) + "\n")
        bots = folder / "bots"
        base = driver.gauntlet_bot({"name": "champion", "commit": champion}, into=bots)
        seed = map_seed(champion)
        # A sweep begun on another rotation keeps its seat maps, so no variant's pass 3 plays again: those whose making
        # finished (driver.generate_maps writes SEED last), so an interrupted one never fixes a short seat pass.
        made_before = [len(list((folder / name).glob("*.map"))) if (folder / name / "SEED").exists() else 0
                       for name in ("maps-3", "maps-3w")]
        kept = all(made_before)
        seat_n = sum(made_before) if kept else seat_maps()
        worldgen = made_before[1] if kept else seat_n // 2
        farm_counts = {style: int(SECOND_MAPS * share) for style, share in FARM_MIX.items()}
        sets = [("maps-1", seed, FIRST_MAPS, "eval"), ("maps-2", seed + FIRST_MAPS, SECOND_MAPS, "eval"),
                ("maps-3", seed + SEAT_SEED, seat_n - worldgen, "eval"),
                ("maps-3w", seed + SEAT_SEED, worldgen, meta.WORLDGEN),
                *((f"maps-2f-{style}", seed + FARM_SEED + 1000 * k, n, style)
                  for k, (style, n) in enumerate(farm_counts.items()))]
        for name, first, count, style in sets:
            driver.generate_maps(folder / name, first, count, style)
        # A sweep begun before the seat half made 20 maps in maps-2: its first SECOND_MAPS are this pass's.
        made = lambda name, count: sorted(str(m) for m in (folder / name).glob("*.map"))[:count]  # noqa: E731
        maps = {"1": made("maps-1", FIRST_MAPS), "2": made("maps-2", SECOND_MAPS),
                "3": made("maps-3", seat_n - worldgen) + made("maps-3w", worldgen)}
        maps["2f"] = maps["2"][:SECOND_MAPS - sum(farm_counts.values())] + [
            game_map for style, n in farm_counts.items() for game_map in made(f"maps-2f-{style}", n)]
        opponents = dict(driver.rotation_bots(list(driver.ROTATION)))
        source = source_export(bots, champion)
        variant_bot = lambda variant: base if variant["id"] == BASELINE else driver.gauntlet_bot(  # noqa: E731
            {"name": bot_name(variant["id"]), "commit": champion,
             "params": variant.get("params") or {variant["param"]: variant["value"]}}, into=bots, source=source)
        while True:
            if ahead is not None:  # its exports finish before this batch exports any (gauntlet_bot's folders)
                ahead.join()
            rows = results(folder)
            with meta.locked(state / "sweep"):
                current = read(state)
                if current.get("champion") != champion:
                    return
                added = queue(state, current, listing["variants"], rows, folder, seat_n)
                considered = dict(current.get("considered") or {})
                current.update(done=done(listing["variants"], rows, considered, seat_n), variants=len(listing["variants"]),
                               dead=dead(listing["variants"], rows), progress_at=time.time())
                save(state, current)
            if added:
                print(f"queued {added}", flush=True)
            items = todo(listing["variants"], rows, considered, seat_n)
            if not items:
                shutil.rmtree(bots, ignore_errors=True)  # built variants, about 1 MB each; the results stay
                update(state, champion, pid=None, finished_at=time.time(), error=None, failures=0)
                print(f"finished: {len(listing['variants'])} variants", flush=True)
                return
            if reason := wait_idle(lambda: busy(repo, champion), lambda **changes: update(state, champion, **changes)):
                update(state, champion, pid=None, paused=reason, paused_at=time.time())
                print(f"paused: {reason}", flush=True)
                return
            batch = batch_size(state)
            plan = {"champion": str(base), "out": str(folder / "results.jsonl"), "maps": maps,
                    "seats": {game_map: [seat, str(opponents[seat])]
                              for game_map, seat in seats(maps["3"], rows).items()},
                    "status": str(state / "remote-status-sweep.json"), "repo": str(repo), "commit": champion,
                    "items": [{"id": variant["id"], "pass": n, "maps": maps_key(variant, n),
                               "bot": str(variant_bot(variant))} for variant, n in items[:batch]]}
            online.save(folder / "plan.json", plan)  # whole, so health.plan_file never reads half a plan
            playing = {variant["id"] for variant, _ in items[:batch]}  # its play builds these (pass 2 and 3 of one)
            ahead = prepare_ahead([variant for variant, _ in items[batch:2 * batch]
                                   if variant["id"] != BASELINE and variant["id"] not in playing], variant_bot)
            proc = driver.referee(str(Path(__file__).resolve()), ["play", str(folder / "plan.json")],
                                  timeout=6 * 3600, yielding="filler")
            print(proc.stdout + proc.stderr, end="", flush=True)
            if proc.returncode not in (0, PAUSED):
                raise RuntimeError(f"play exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-600:]}")
            update(state, champion, failures=0, error=None)  # a batch played: earlier failures are over
    except Exception as error:
        detail = str(getattr(error, "stderr", "") or "").strip()
        with meta.locked(state / "sweep"):
            current = read(state)
            if current.get("champion") == champion:
                save(state, failure(current, f"{type(error).__name__}: {error} {detail[-300:]}".strip(), time.time()))
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    finally:
        if ahead is not None:
            ahead.join()
        driver.RUNTIME_PYTHON.reset(token)


# The variants (plan items) a referee call plays: CALL_ROUNDS games for every slot in the newest utilisation row
# (throughput.LOG's capacity: every host's and this Mac's), BATCH at least. A call's first games wait for its
# scheduler to measure the hosts and its last for its slowest games (p99 79 s, the longest 143 s, over the 2,252 games
# of e286215's sweep), with its share idle meanwhile: on 30 Sep 8 variants (160 games, about 30 s of games on the 72
# slots) took 1.9 min in the scheduler (remote-status-sweep.json's 83 games a minute), so a call is sized to the
# machines rather than to a fixed count.
def batch_size(state: Path) -> int:
    import throughput
    slots = (throughput.last_row(state / throughput.LOG) or {}).get("capacity")
    slots = slots if isinstance(slots, int) and slots > 0 else 0
    return max(BATCH, math.ceil(CALL_ROUNDS * slots / (2 * FIRST_MAPS)))


# The one export of `champion`'s bot/ in `bots` that every variant is copied from (driver.gauntlet_bot's `source`),
# made once a run and never built in, so no build of its own lands in a variant's copy. Each export checks the whole
# repository first (publishing.repo_intact's fsck: 6 s here on 30 Sep, 3.2-4.1 s a variant on 29 Sep), so the next
# batch's exports (prepare_ahead) took longer than a batch's games: the sweep alone kept 45% of the slots busy,
# alternating a minute near its share with a minute near none (30 Sep 02:54-03:02, utilisation.jsonl). Made under a
# temporary name, so a killed run never leaves half of one.
def source_export(bots: Path, champion: str) -> Path:
    import driver
    folder = bots / f".source-{champion[:12]}"
    if not folder.exists():
        partial = folder.with_name(folder.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        driver.export_bot(driver.REPO, champion, partial)
        partial.rename(folder)
    return folder


# Exports the next batch's `variants` (`bot`: run's variant_bot) and builds them (`build` under the referee's runtime),
# in a thread while the current batch plays, so the next plays at once (its own builds then hit the toolkit's cache in
# each bot's folder). On 29 Sep the sweep played in bursts: 40 of its 83 minutes with no evaluation on the hosts had
# none of its games, since each batch first exported its 8 variants (3.2-4.1 s each, in turn) and built them (5.9-7.0 s
# each, two at a time), about 56 s with its share of every machine idle; with the exports done ahead they still took
# longer than a batch's games, until each variant became a copy (source_export). A variant that fails to export or
# build is left to play, which records its build error. The thread runs in a copy of this context (RUNTIME_PYTHON).
def prepare_ahead(variants: list[dict], bot) -> threading.Thread | None:
    import driver
    if not variants:
        return None

    def prepare() -> None:
        paths = []
        for variant in variants:
            try:
                paths.append(str(bot(variant)))
            except Exception as error:  # play exports it again and records what failed
                print(f"preparing {variant['id']} failed: {type(error).__name__}: {error}", flush=True)
        if paths:
            done = driver.referee(str(Path(__file__).resolve()), ["build", *paths], timeout=1800)
            if done.returncode:
                print(f"preparing the next batch's builds exited {done.returncode}: {done.stderr.strip()[-300:]}",
                      flush=True)

    thread = threading.Thread(target=contextvars.copy_context().run, args=(prepare,), daemon=True)
    thread.start()
    return thread


# `build BOT...`, run by prepare_ahead under the referee's runtime (native.py play): each bot built as play builds its
# batch; a failure is play's to record.
def build(bots: list[str]) -> int:
    from concurrent.futures import ThreadPoolExecutor

    from unswbc.errors import UserError
    from unswbc.run import _resolve

    def one(path: str) -> None:
        try:
            _resolve(path, False)
        except UserError:
            pass

    with ThreadPoolExecutor(max_workers=home.pool_size(2, wait=False)) as pool:  # as play's (never waiting)
        list(pool.map(one, bots))
    return 0


# The plan's maps item `item` plays: its "maps" list (maps_key), else its pass's.
def item_maps(item: dict, maps: dict) -> list[str]:
    return maps[item.get("maps") or str(item["pass"])]


# The games of `items` in order, variant by variant: before each variant, `stop()` is asked, and a reason ends the
# games there (the variant being played finishes). Yields (item, map, side); `stopped` receives the reason.
def schedule(items: list[dict], maps: dict, stop, stopped: list[str]):
    for item in items:
        if reason := stop():
            stopped.append(reason)
            return
        for game_map in item_maps(item, maps):
            for side in ("A", "B"):
                yield item, game_map, side


# `play PLAN`, run by driver.referee inside the referee checkout (native.py play puts referee/ on the path and applies
# the load gates and an idle filler's share, native.host_share): builds the plan's variants, then plays them against
# the champion, or on pass 3 against each map's seat (the plan's "seats": {map: [name, bot]}), appending each variant's
# games to the results once all of them are in (a seat game with its "seat"). Exits PAUSED when it stopped for busy().
def play(plan_path: Path) -> int:
    from concurrent.futures import ThreadPoolExecutor

    import remote
    import run as referee
    from unswbc.errors import UserError
    from unswbc.run import _resolve

    plan = json.loads(plan_path.read_text())
    remote.STATUS = Path(plan["status"])  # never the evaluation's live status
    out, champion = Path(plan["out"]), Path(plan["champion"])
    seats = plan.get("seats") or {}
    opponents = list(dict.fromkeys(Path(bot) for _, bot in seats.values()))
    rival = lambda item, game_map: Path(seats[game_map][1]) if item["pass"] == 3 else champion  # noqa: E731

    def build(bot: str) -> str | None:
        try:
            _resolve(bot, False)
        except UserError as error:
            return str(error)
        return None

    if reason := busy(Path(plan["repo"]), plan["commit"]):  # nothing built or played for a batch that must wait
        print(f"stopped before building: {reason}", file=sys.stderr)
        return PAUSED
    # Two at a time, one while this Mac is full (home.pool_size, never waiting): compiling is this Mac's CPU, outside
    # the scheduler's load gate. The scheduler's build then hits the cache.
    with ThreadPoolExecutor(max_workers=home.pool_size(2, wait=False)) as pool:
        errors = list(pool.map(build, [str(champion), *map(str, opponents), *(item["bot"] for item in plan["items"])]))
    if broken := next((error for error in errors[:1 + len(opponents)] if error), None):
        print(f"the champion or a seat does not build: {broken}", file=sys.stderr)
        return 2
    errors = [errors[0], *errors[1 + len(opponents):]]
    items = []
    with out.open("a") as handle:
        for item, error in zip(plan["items"], errors[1:], strict=True):
            if error:
                handle.write(json.dumps({"variant": item["id"], "pass": item["pass"], "build_error": error[-600:]}) + "\n")
            else:
                items.append(item)
    stopped: list[str] = []
    planned = schedule(items, plan["maps"], lambda: busy(Path(plan["repo"]), plan["commit"]), stopped)
    owners: list[dict] = []

    def jobs():
        for item, game_map, side in planned:
            owners.append(item)
            yield referee.Job(Path(game_map), Path(item["bot"]), rival(item, game_map), side)

    played, mine = 0, []
    bots = [champion, *opponents, *(Path(item["bot"]) for item in items)]
    for result in referee.stream_games(jobs(), list(dict.fromkeys(bots)), "auto", False):
        item = owners[played]  # results come in job order, and a job is pulled before its result
        played += 1
        mine.append(result)
        if len(mine) < 2 * len(item_maps(item, plan["maps"])):
            continue
        with out.open("a") as handle:  # a variant's games are written together, once all are in
            if error := next((r for r in mine if r.error), None):
                # Unscored and done (todo skips it), so a variant whose games can't be played never blocks the sweep.
                handle.write(json.dumps({"variant": item["id"], "pass": item["pass"], "game_error":
                                         f"{error.map} ({error.a_side}): {error.error}"[-600:]}) + "\n")
                print(f"  {item['id']} pass {item['pass']}: game failed: {error.error}", file=sys.stderr, flush=True)
            else:
                handle.writelines(json.dumps({"variant": item["id"], "pass": item["pass"],
                                              **({"seat": seats[r.map][0]} if item["pass"] == 3 else {}),
                                              **json.loads(referee.to_json(r))}) + "\n" for r in mine)
                print(f"  {item['id']} pass {item['pass']}: {score([{'winner': r.winner} for r in mine]):.2f}",
                      file=sys.stderr, flush=True)
        mine = []
    if stopped:
        print(f"stopped between variants: {stopped[0]}", file=sys.stderr)
        return PAUSED
    return 0


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("variants", help="print the variants a champion gets, and the skipped")
    listing.add_argument("--commit", default="loop/champion")
    running = commands.add_parser("run", help="the background run tick launches")
    running.add_argument("--repo", type=Path, default=REPO)
    running.add_argument("--state", type=Path, default=loop_home())
    playing = commands.add_parser("play", help="internal: one batch of games, under native.py play")
    playing.add_argument("plan", type=Path)
    building = commands.add_parser("build", help="internal: the next batch's builds, under native.py play")
    building.add_argument("bots", nargs="+")
    args = parser.parse_args()
    if args.command == "variants":
        found = variants(champion_params(REPO, args.commit))
        for variant in found["variants"]:
            starts = variant.get("starts") or {variant["param"]: variant["start"]}
            print(f"{variant['id']:32} (from {', '.join(literal(value) for value in starts.values())})")
        for skip in found["skipped"]:
            print(f"skipped {skip['param']}{'=' + literal(skip['value']) if 'value' in skip else ''}: {skip['reason']}")
        print(f"{len(found['variants'])} variants, {len(found['skipped'])} skipped")
    elif args.command == "run":
        run(args.repo.resolve(), args.state.resolve())
    elif args.command == "build":
        sys.exit(build(args.bots))
    else:
        sys.exit(play(args.plan))


if __name__ == "__main__":
    main()
