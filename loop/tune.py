"""SPSA tuning of named numbers in a bot's params.hpp, played through the referee's game scheduler.

    python loop/native.py play loop/tune.py --bot DIR --params a b c --maps MAP... --out tune.json
                                            [--iterations 60] [--perturbations 4] [--pairs 3] [--seed N]
                                            [--constraint 'beaconRound <= foldRound' ...]

Run from the referee checkout (native.py puts its `referee/` on the path and applies the load gates). Each
perturbation nudges every tuned value up or down together (a random ±1 per value), plays the "plus" bot against
the "minus" bot on `pairs` fresh maps with sides swapped, and scores which won. The budget is `iterations` times
`perturbations` perturbations, played in rounds: each round plays as many perturbations as fill about WAVES waves
of the game slots free at its start (batch_size; at least `perturbations`, the classic iteration, and at most
MAX_ROUND of them), waits for all their games, and moves toward the winners, each perturbation weighted by its own
iteration's gain (spsa_step). So the games and the gain schedule are the classic SPSA's, and the final values' noise
nearly so (MAX_ROUND), in fewer, fuller rounds: E0147's and E0154's iterations of 42 games kept 7.2 and 10.4 games
in flight on 58-76 slots (2026-09-27).
With `--opponent NAME=PATH` the maps rotate through that head-to-head and the opponents: on an opponent's map
plus and minus each play it as team A and B, scored by plus's points minus minus's, so a value that only wins
against itself doesn't win the tune (a folding bot tuned against folding bots, review finding M2).
The log (--out) keeps each round's values and scores and each perturbation's nudges, maps and per-map scores
("trials", pair_scores), from which loop/tunesplit.py asks whether the best values differ by map area or style.
The final values are written into DIR's params.hpp; they still have to pass the normal SPRT to be kept. Values
never change sign and stay within 4x of where they started. `bool` switches are never tuned. Each `--constraint`
("beaconRound <= foldRound"; <=, <, >=, >) orders two params.hpp numbers, tuned or not: every value played and the
final values keep it (`project`), and a set the 4x clamps make impossible fails before any game (constraints came
after I237's tune stated its order in words only and put the fold at r397 before the r405 beacon).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
import tempfile
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PARAM = re.compile(r"^(\s*)(int|double|float)(\s+)(\w+)(\s*=\s*)([-+0-9.eE]+)(\s*;.*)$")
BOOL = re.compile(r"^\s*bool\s+(\w+)\s*=")
CONSTRAINT = re.compile(r"^\s*(\w+)\s*(<=|<|>=|>)\s*(\w+)\s*$")
STEP = 0.15  # each nudge, as a share of the starting value
GAIN = 2.0  # first iteration's move per unit of score, in nudges


# Reads every tunable number: name -> (type, value).
def read_params(text: str) -> dict[str, tuple[str, float]]:
    return {m.group(4): (m.group(2), float(m.group(6))) for line in text.splitlines() if (m := PARAM.match(line))}


# Rewrites the defaults of `values` in params.hpp text, keeping layout and comments.
def write_params(text: str, values: dict[str, float]) -> str:
    def line(raw: str) -> str:
        m = PARAM.match(raw)
        if not m or m.group(4) not in values:
            return raw
        value = values[m.group(4)]
        shown = str(int(round(value))) if m.group(2) == "int" else repr(float(value))
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{shown}{m.group(7)}"
    return "\n".join(line(raw) for raw in text.splitlines()) + ("\n" if text.endswith("\n") else "")


# The size of one nudge for a value that started at `start`.
def nudge(kind: str, start: float) -> float:
    if kind == "int":
        return max(1.0, round(STEP * abs(start)))
    return STEP * abs(start) if start else 0.1


# Keeps a value on the side of zero it started on and within 4x of its start (down to 0 for a double under 1 in
# size); a value that starts at 0 is left free. An int starting at +-1 or +-2 stops at +-1, since a quarter of it
# (0.25, 0.5) rounds to 0: it would play 0, not a smaller value of its own sign.
def clamp(value: float, start: float, kind: str = "double") -> float:
    size = abs(start)
    floor = 1.0 if kind == "int" and 0 < size <= 2 else 0.0 if size < 1 else size / 4
    if start > 0:
        return min(max(value, floor), start * 4)
    if start < 0:
        return max(min(value, -floor if floor else 0.0), start * 4)
    return value


# The lowest and highest value `clamp` allows a value that started at `start` (ints: the whole numbers inside).
def clamp_range(kind: str, start: float) -> tuple[float, float]:
    low, high = sorted((clamp(-math.inf, start, kind), clamp(math.inf, start, kind)))
    return (math.ceil(low), math.floor(high)) if kind == "int" and start else (low, high)


# One constraint, read as `lo + gap <= hi`: gap 1 for a strict order (only between ints), else 0.
@dataclass(frozen=True)
class Order:
    lo: str
    hi: str
    gap: int
    text: str


# Every name params.hpp defines, with its type (bool switches included, which read_params leaves out).
def param_kinds(text: str) -> dict[str, str]:
    kinds = {m.group(1): "bool" for line in text.splitlines() if (m := BOOL.match(line))}
    return kinds | {name: kind for name, (kind, _) in read_params(text).items()}


# Parses constraint texts against params.hpp's `kinds`; `tuned` are the values being tuned. Raises ValueError naming
# the first bad one: not "a <op> b", a name params.hpp lacks or a bool, a param ordered against itself, neither side
# tuned, or a strict order with a non-int side (between doubles, write <=).
def parse_orders(texts: list[str], kinds: dict[str, str], tuned: list[str]) -> list[Order]:
    orders = []
    for text in texts:
        m = CONSTRAINT.match(text) if isinstance(text, str) else None
        if not m:
            raise ValueError(f"constraint {text!r} is not 'a <= b' (or <, >=, >) between two params.hpp names")
        left, op, right = m.groups()
        for name in (left, right):
            if kinds.get(name) is None:
                raise ValueError(f"constraint {text!r}: {name} is not in params.hpp")
            if kinds[name] == "bool":
                raise ValueError(f"constraint {text!r}: {name} is a bool switch, not a number")
        if left == right:
            raise ValueError(f"constraint {text!r} orders a param against itself")
        if left not in tuned and right not in tuned:
            raise ValueError(f"constraint {text!r} names no tuned param")
        strict = op in ("<", ">")
        if strict and not (kinds[left] == kinds[right] == "int"):
            raise ValueError(f"constraint {text!r}: a strict order needs two ints; use <= or >= between doubles")
        lo, hi = (left, right) if op.startswith("<") else (right, left)
        orders.append(Order(lo, hi, int(strict), text))
    return orders


# The params in `orders` lowest first (each after every param it must stay above). Raises ValueError on a cycle.
def order_of(orders: list[Order]) -> list[str]:
    names = sorted({o.lo for o in orders} | {o.hi for o in orders})
    placed: list[str] = []
    while len(placed) < len(names):
        ready = [n for n in names if n not in placed and all(o.lo in placed for o in orders if o.hi == n)]
        if not ready:
            raise ValueError(f"constraints form a cycle: {', '.join(o.text for o in orders)}")
        placed += ready
    return placed


# Each constrained param's (lowest, highest) value that some assignment meeting every order and range can give it,
# from `ranges` (name -> (low, high): a fixed param's is its value twice). Raises ValueError when there is none: the
# clamps (or the fixed values) make the constraints impossible.
def feasible_bounds(orders: list[Order], ranges: dict[str, tuple[float, float]],
                    kinds: dict[str, str]) -> dict[str, tuple[float, float]]:
    sequence = order_of(orders)
    low = {n: ranges[n][0] for n in sequence}
    high = {n: ranges[n][1] for n in sequence}
    for n in sequence:
        low[n] = max([low[n]] + [low[o.lo] + o.gap for o in orders if o.hi == n])
        low[n] = math.ceil(low[n]) if kinds[n] == "int" and math.isfinite(low[n]) else low[n]
    for n in reversed(sequence):
        high[n] = min([high[n]] + [high[o.hi] - o.gap for o in orders if o.lo == n])
        high[n] = math.floor(high[n]) if kinds[n] == "int" and math.isfinite(high[n]) else high[n]
    if stuck := [n for n in sequence if low[n] > high[n]]:
        raise ValueError(f"constraints {', '.join(o.text for o in orders)} can't hold within the 4x clamps: {stuck[0]} "
                         f"would need at least {low[stuck[0]]:g} and at most {high[stuck[0]]:g}")
    return {n: (low[n], high[n]) for n in sequence}


# `values` (every constrained param, fixed ones included) moved as little as simply possible so every order holds
# on the values actually played (ints rounded): each broken pair meets at its midpoint (the lower one rounded down,
# the upper up, so the order holds and an int pair keeps its sum), inside `bounds` (feasible_bounds, so within the
# clamps and never moving a fixed param), rechecking until none is broken. Chains of doubles can creep toward the
# midpoint forever, so after 20 passes one pass lowest-first lifts each param to what its lower neighbours need,
# which `bounds` guarantees ends with every order holding. Unbroken values come back untouched (ints unrounded), so
# a tune without a broken order moves exactly as before; a param it moves comes back as the value played.
def project(values: dict[str, float], orders: list[Order], kinds: dict[str, str],
            bounds: dict[str, tuple[float, float]]) -> dict[str, float]:
    shown = {n: round(v) if kinds[n] == "int" else v for n, v in values.items()}

    def fit(name: str, value: float, up: bool) -> float:
        if kinds[name] == "int":
            value = math.ceil(value) if up else math.floor(value)
        return min(max(value, bounds[name][0]), bounds[name][1])

    def broken() -> list[Order]:
        return [o for o in orders if shown[o.lo] + o.gap > shown[o.hi]]

    if not broken():
        return values
    for _ in range(20):
        for o in broken():
            if shown[o.lo] + o.gap <= shown[o.hi]:
                continue  # an earlier fix this pass mended it
            meet = (shown[o.lo] + shown[o.hi] - o.gap) / 2  # the lower one's value where the pair meets
            shown[o.lo] = fit(o.lo, meet, up=False)
            shown[o.hi] = fit(o.hi, max(meet, shown[o.lo]) + o.gap, up=True)
    if broken():
        for name in order_of(orders):
            shown[name] = fit(name, max([shown[name]] + [shown[o.lo] + o.gap for o in orders if o.hi == name]), up=True)
    return {n: (values[n] if shown[n] == (round(values[n]) if kinds[n] == "int" else values[n]) else shown[n])
            for n in values}


# The gain of iteration `k` of `iterations` (0-based): GAIN, shrinking as SPSA's usual k^-0.602 after a tenth of them.
def gain_at(k: int, iterations: int) -> float:
    return GAIN / (1 + k / max(1, iterations / 10)) ** 0.602


# One SPSA move from a round's trials (delta, score, gain), scores in [-1, 1] for "plus": per value, theta += the sum
# over trials of gain * score * delta / `base` (an iteration's perturbations), times its nudge. A round of `base`
# trials at one gain is the classic step, gain * mean(score * delta) * nudge; a bigger round adds each further `base`
# trials' step at its own iteration's gain, so every perturbation weighs what it would have in `base`-sized
# iterations (batch_size).
def spsa_step(theta: dict[str, float], trials: list[tuple[dict[str, int], float, float]], base: int,
              nudges: dict[str, float], starts: dict[str, float], kinds: dict[str, str]) -> dict[str, float]:
    moved = {}
    for name, value in theta.items():
        by_gain: dict[float, float] = {}
        for delta, score, gain in trials:
            by_gain[gain] = by_gain.get(gain, 0.0) + score * delta[name]
        step = sum(gain * (total / base) for gain, total in by_gain.items())
        moved[name] = clamp(value + step * nudges[name], starts[name], kinds[name])
    return moved


# A round waits for its slowest game, so it holds about WAVES waves of the free slots' games, but at most MAX_ROUND
# iterations' perturbations: each round's values move once, from where the round started, so bigger rounds lose a
# little of SPSA's pull back toward the best values between moves. On a toy objective (5 values, 50 iterations of
# 4 perturbations on 3 maps, 6,000 runs each) the final values' SD was 0.553 in the classic 4-a-round, 0.559 at 7,
# 0.562 at 10, 0.566 at 12 (MAX_ROUND's cap) and 0.578 at 20.
WAVES = 3
MAX_ROUND = 3


# How many perturbations the next round plays, from the `free` game slots and `games_each` games a perturbation: about
# WAVES waves of the slots, never fewer than `base` (one iteration's, the size before batching) nor more than
# MAX_ROUND iterations' or the `left` of the budget.
def batch_size(free: int, games_each: float, base: int, left: int) -> int:
    return min(left, max(base, min(math.ceil(WAVES * free / games_each), MAX_ROUND * base)))


MEASURE_S = 20.0  # the longest free_slots waits for the hosts' measurements (Host.connect_timeout is 10-30 s)


# Games this process may start now: this Mac's slots and each usable game host's capacity, both of which native.py
# gates (the load gate, and this process's share of each host). Host.capacity alone only starts a background
# measurement once the last is STALE_S old and meanwhile allows no more than the games running there, which between
# rounds is none, so every usable host is measured here first, all at once, waiting at most MEASURE_S: one that hasn't
# answered by then counts what capacity() allows, and a failed measurement rests the host, as the scheduler does.
# Under native.py a host's `room` replaces its capacity here: that plus the idle fillers' games there, which take the
# tune's share between its rounds and give it back within a game once it asks (native.yield_to_evaluations).
def free_slots(run) -> int:
    import remote
    hosts = [host for host in remote.load_hosts() if host.usable()]
    errors: dict[str, Exception] = {}

    def measure(host) -> None:
        try:
            host.measure()
        except Exception as error:  # anything the host said wrong rests it, as a background measurement does
            errors[host.name] = error

    threads = [threading.Thread(target=measure, args=(host,), name=f"tune-load-{host.name}", daemon=True)
               for host in hosts]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + MEASURE_S
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    free = run.game_slots("auto", 0)
    for host in hosts:
        try:
            if error := errors.get(host.name):
                raise error if isinstance(error, remote.RemoteError) else remote.RemoteError(f"{host.name}: {error}")
            free += host.room() if hasattr(host, "room") else host.capacity()
        except remote.RemoteError as error:
            host.rest(str(error))
    return free


# This round's games as ((perturbation, weight), job), taking one map per pair from `maps` and rotating the pairs
# through `rotation` (None: plus against minus; else a frozen opponent both play) by the map's place in the whole
# tune, `first` being the first map's, as sprt.py deals its maps. None if maps run out.
# A bots entry may carry a third build, minus as it plays plus head to head (on another fixed sonar key than plus's,
# --fixed-keys: sharedkey.test_copy's "alt", as the SPRT's candidate plays the champion).
def plan_games(bots: list[tuple[Path, ...]], pairs: int, maps, rotation: list[Path | None],
               first: int = 0) -> list | None:
    import run
    planned = []
    for index, (plus, minus, *head) in enumerate(bots):
        for pair in range(pairs):
            game_map = next(maps, None)
            if game_map is None:
                return None
            opponent = rotation[(first + index * pairs + pair) % len(rotation)]
            games = ([(plus, head[0] if head else minus, "pair")] if opponent is None
                     else [(plus, opponent, 1), (minus, opponent, -1)])
            for a, b, weight in games:
                planned += [((index, weight), run.Job(game_map, a, b, side)) for side in ("A", "B")]
    return planned


# Plus's points minus minus's per perturbation: a game of plus against minus counts 2 * points - 1 (points being
# plus's), a game against an opponent +points for plus and -points for minus. Divided by 2 * pairs it is in [-1, 1].
def score_games(owner: list[tuple[int, int | str]], winners: list[str], perturbations: int) -> list[float]:
    diff = [0.0] * perturbations
    for (index, weight), winner in zip(owner, winners, strict=True):
        points = {"a": 1.0, "b": 0.0, "draw": 0.5}[winner]
        diff[index] += 2 * points - 1 if weight == "pair" else weight * points
    return diff


# score_games per map: each perturbation's {map: score} in play order, a map's score being its games' share of the
# perturbation's diff over 2, so in [-1, 1], and the perturbation's score (diff / 2 pairs) is their mean. The tune's
# log keeps them, so loop/tunesplit.py can split a tune's samples by each map's area and style.
def pair_scores(owner: list[tuple[int, int | str]], maps: list, winners: list[str],
                perturbations: int) -> list[dict[str, float]]:
    scores: list[dict[str, float]] = [{} for _ in range(perturbations)]
    for (index, weight), game_map, winner in zip(owner, maps, winners, strict=True):
        points = {"a": 1.0, "b": 0.0, "draw": 0.5}[winner]
        key = str(game_map)
        scores[index][key] = scores[index].get(key, 0.0) + (2 * points - 1 if weight == "pair" else weight * points) / 2
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description="SPSA-tune params.hpp values with paired games.")
    parser.add_argument("--bot", type=Path, required=True, help="bot directory; its params.hpp gets the result")
    parser.add_argument("--params", nargs="+", required=True, help="names of the values to tune")
    parser.add_argument("--maps", type=Path, nargs="+", required=True, help="fresh maps, used in order")
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--perturbations", type=int, default=4)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--opponent", action="append", default=[], metavar="NAME=PATH",
                        help="also score each nudge against these frozen opponents, in rotation")
    parser.add_argument("--constraint", action="append", default=[], metavar="'A <= B'",
                        help="an order every played and final value keeps (<=, <, >=, >; params tuned or not)")
    parser.add_argument("--fixed-keys", action="store_true",
                        help="play test copies on fixed sonar keys (referee/sharedkey.py), as the SPRT's test builds do")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    import run  # the referee's scheduler; native.py has put referee/ on the path
    from unswbc.run import _resolve
    try:
        import sharedkey  # a referee checkout from before 2026-09-29 has none: the real builds play
    except ImportError:
        sharedkey = None

    source = (args.bot / "params.hpp").read_text()
    known = read_params(source)
    missing = [p for p in args.params if p not in known]
    args.params = [p for p in args.params if p in known]  # a later experiment may have removed or renamed some
    if missing:
        print(f"skipping params not in params.hpp (or not numeric): {missing}", file=sys.stderr)
    if not args.params:
        print("none of the named params exist; nothing to tune", file=sys.stderr)
        return 2
    kinds = {p: known[p][0] for p in args.params}
    starts = {p: known[p][1] for p in args.params}
    nudges = {p: nudge(kinds[p], starts[p]) for p in args.params}
    try:
        orders = parse_orders(args.constraint, param_kinds(source), args.params)
        constrained = {n for o in orders for n in (o.lo, o.hi)}
        fixed = {n: known[n][1] for n in constrained - set(args.params)}
        bounds = feasible_bounds(orders, {n: (fixed[n], fixed[n]) if n in fixed else clamp_range(kinds[n], starts[n])
                                          for n in constrained}, {n: known[n][0] for n in constrained})
    except ValueError as error:
        print(f"tune not started: {error}", file=sys.stderr)
        return 2
    projections: list[dict] = []

    # `values` with every constraint holding (project), noting each move as `point` of round `k` (0: the start).
    def keep(values: dict[str, float], k: int, point: str) -> dict[str, float]:
        if not orders:
            return values
        mine = {n: values[n] for n in constrained if n in values} | fixed
        moved = project(mine, orders, {n: known[n][0] for n in constrained}, bounds)
        if changes := {n: [mine[n], moved[n]] for n in moved if n not in fixed and moved[n] != mine[n]}:
            projections.append({"round": k, "point": point, "moved": changes})
        return values | {n: moved[n] for n in changes}

    theta = keep(dict(starts), 0, "start")
    if orders:
        print(f"constraints: {'; '.join(args.constraint)}", file=sys.stderr)
        if projections:
            print(f"  start moved to keep them: {projections[0]['moved']}", file=sys.stderr)
    rng = random.Random(args.seed)
    maps = iter(args.maps)
    opponents = [Path(text.split("=", 1)[1]) for text in args.opponent]
    rotation: list[Path | None] = [None, *opponents]  # None: plus against minus
    log = {"params": args.params, "start": starts, "nudges": nudges, "constraints": args.constraint,
           "fixed": fixed, "projections": projections, "rounds": [], "trials": []}
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="tune-") as scratch:
        built: dict[str, Path] = {}

        def variant(values: dict[str, float]) -> Path:
            shown = {p: (round(v) if kinds[p] == "int" else v) for p, v in values.items()}
            key = hashlib.sha256(json.dumps(shown, sort_keys=True).encode()).hexdigest()[:16]
            if key not in built:
                folder = Path(scratch) / key
                shutil.copytree(args.bot, folder, ignore=shutil.ignore_patterns(".unswbc-build"))
                (folder / "params.hpp").write_text(write_params(source, shown))
                built[key] = folder
            return built[key]

        # With --fixed-keys, `folder`'s test copy on the fixed sonar key (`kind` "fixed", or "alt" for minus head
        # to head), so a pair's Mac and Linux games agree and differ only by the values (MISSES 99); else, or for a
        # bot without P.sonarBuildKey, `folder` itself.
        def keyed(folder: Path, kind: str = "fixed") -> Path:
            if not (args.fixed_keys and sharedkey):
                return folder
            copy = sharedkey.test_copy(folder, Path(scratch) / "keyed", kind)
            if copy is not None:
                built[copy.name] = copy  # removed with the round's variants
            return copy or folder

        # The budget is `iterations` iterations of `perturbations` each; rounds take it in batches (batch_size).
        budget, used, round_, games = args.iterations * args.perturbations, 0, 0, 0
        games_each = args.pairs * 2 * (1 + 2 * len(opponents)) / len(rotation)  # 2 a head-to-head map, 4 an opponent's
        while used < budget:
            round_ += 1
            began = time.monotonic()
            size = batch_size(free_slots(run), games_each, args.perturbations, budget - used)
            sized = time.monotonic()
            deltas = [{p: rng.choice((-1, 1)) for p in args.params} for _ in range(size)]
            gains = [gain_at((used + i) // args.perturbations, args.iterations) for i in range(size)]
            bots = []
            for index, delta in enumerate(deltas):
                plus = keep({p: clamp(theta[p] + delta[p] * nudges[p], starts[p], kinds[p]) for p in args.params},
                            round_, f"plus {index}")
                minus = keep({p: clamp(theta[p] - delta[p] * nudges[p], starts[p], kinds[p]) for p in args.params},
                             round_, f"minus {index}")
                bots.append((keyed(variant(plus)), keyed(variant(minus)), keyed(variant(minus), "alt")))
            with ThreadPoolExecutor(max_workers=8) as pool:  # compile this round's variants side by side
                list(pool.map(lambda bot: _resolve(str(bot), False), {b for pair in bots for b in pair}))
            compiled = time.monotonic()
            planned = plan_games(bots, args.pairs, maps, rotation, first=used * args.pairs)
            if planned is None:
                print("ran out of maps", file=sys.stderr)
                return 2
            owner = [weight for weight, _ in planned]
            results = list(run.stream_games((job for _, job in planned), [b for pair in bots for b in pair] + opponents,
                                            "auto", False))
            played = time.monotonic()
            if failed := next((r for r in results if r.error), None):
                print(f"game failed: {failed.error}", file=sys.stderr)
                return 2
            diff = score_games(owner, [r.winner for r in results], len(bots))
            trials = [(delta, diff[i] / (2 * args.pairs), gains[i]) for i, delta in enumerate(deltas)]
            by_map = pair_scores(owner, [job.map for _, job in planned], [r.winner for r in results], len(bots))
            log["trials"] += [{"delta": delta, "maps": list(by_map[i]),
                               "pairs": [round(s, 4) for s in by_map[i].values()]} for i, delta in enumerate(deltas)]
            theta = keep(spsa_step(theta, trials, args.perturbations, nudges, starts, kinds), round_, "theta")
            used, games = used + size, games + len(planned)
            for folder in built.values():  # the next round's values differ; keep the scratch disk small
                shutil.rmtree(folder, ignore_errors=True)
            built.clear()
            log["rounds"].append({"theta": dict(theta), "scores": [s for _, s, _ in trials]})
            moved = sum(row["round"] == round_ and row["point"].startswith(("plus", "minus")) for row in projections)
            kept = f", {moved} played points moved to keep the constraints" if moved else ""
            # The phases' seconds, so the gaps between rounds, when the tune plays nothing, are measured: sizing (the
            # hosts' measurements), builds (the variants' copies and compiles here), games (the scheduler's, remote
            # builds included) and the rest.
            timing = (f"{sized - began:.0f}s sizing, {compiled - sized:.0f}s builds, {played - compiled:.0f}s games, "
                      f"{time.monotonic() - played:.0f}s after")
            print(f"  round {round_}: {size} perturbations, {len(planned)} games, {used}/{budget} done: mean score "
                  f"{sum(s for _, s, _ in trials) / len(trials):+.2f}{kept} ({timing})", file=sys.stderr, flush=True)

    final = {p: (round(v) if kinds[p] == "int" else round(v, 4)) for p, v in theta.items()}
    final = keep(final, round_, "final")  # 4-place rounding can cross a fixed double's value
    changed = {p: [starts[p], v] for p, v in final.items() if v != starts[p]}
    (args.bot / "params.hpp").write_text(write_params(source, final))
    log.update(final=final, changed=changed, games=games, seconds=round(time.time() - started))
    args.out.write_text(json.dumps(log, indent=1) + "\n")
    print(json.dumps({"changed": changed, "games": log["games"], "seconds": log["seconds"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
