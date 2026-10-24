"""The value model: a team's chance of winning from a game snapshot, which tools/fit.py's default outcome (`win`)
reads; and the fitting pieces fit.py shares (the regressions, map groups and held-out checks).

The win rule (docs/unsw/structure.md): a team whose dragons all die loses (both at once: a draw); otherwise at the
end of round 499 the longest living dragon wins, then the greater total length, else a draw. p_win gives those
exactly: an eliminated team, or a snapshot at the end of round 499, is scored by the rule, and every other snapshot
by the fitted model over what the snapshot shows of both teams (the round, each team's longest dragon, total length
and living dragons, and the map's area; `features`). Its features are antisymmetric (each is its negative from the
other side); the intercept is our edge at an even snapshot, since every row is one of our games (without it the
per-map check said 50% at r100 where we won 70%). p_win is our side's chance: the other side's is 1 less it.

Rows: each game's snapshot at the end of every EVERY-th round, labelled 1 (the side won), 0 (lost) or 1/2 (draw),
from games chosen blind to their result: every saved online replay but the ones kept because we lost and a pruned
version's dev files (loop/online.py `file_selected`, MISSES 55), dev and ladder, all on official maps, and local
replays given with --folder (a run's replays folder, one subfolder per opponent, or replays straight in it; the
loop's evidence-replays by default: generated maps). Two kinds of model are fit: a logistic regression (`logistic`, IRLS) and a one-hidden-layer net (`net`,
HIDDEN tanh units with no biases, an output bias as the intercept, minibatch Adam). Plain Python: no numpy in the
venv, and no new dependency.

The held-out checks (`checks`, sig 2026-09-28; tournament maps are unseen, so a fit passes only if the per-map and
new-map checks hold up, not on its in-sample fit):
- generated, odd: fit on every game but the odd-numbered ones on our generator's gen- maps (a local game's number is
  its map's, so both sides of a pair stay together), test on those;
- per map: for each official map (`map_group`; a renamed copy counts as its map), fit on every other map and test on
  that one (leave one map out); the map's near copies (near_<stem>[_repearl]_<seed>, `near_source`) are left out of
  its fit too, and never tested, since a near copy is mostly its map;
- new maps: test on the maps that joined the pool last (`new_maps`: the maps/ladder files git first added on the
  latest day, Portals and Slithery Fight on 2026-09-27), fit without them, their near copies and the online games
  from the first on them on, the nearest stand-in for an unseen tournament map; without dates (local games only) the
  maps are left out together instead;
- worldgen: test on the worldgen style's maps (gen-worldgen-*, an independent generator), fit on the rest; its
  odd-numbered games train in no check, its even ones in every other check (worldgen is training data too).
The report gives log-loss and Brier for each check, calibration by round band (ROUND_BANDS), each left-out map's
score and the split by game kind (dev, ladder, local) and by opponent. The net is fit on a sample of --net-rows rows
(plain Python is slow), so each check also fits the logistic model on that same sample: the like-for-like comparison.
After the checks the chosen kind is fit once more on every row, and `--write` saves that final fit to WEIGHTS: the net
only when its log-loss is lower than the logistic model's (fit on every row) on every check but the single maps.

    .venv/bin/python tools/value.py fit [--home DIR] [--no-online] [--folder DIR ...] [--cache FILE]
                                        [--jobs N] [--write]
    .venv/bin/python tools/value.py p REPLAY [--side A] [--every 50]   # a game's P(win) round by round

Reading a replay takes ~0.3 s (a logging copy's ~2 s); --cache keeps each replay's rows (keyed by path, size and
mtime) so a refit reads only new ones; --jobs reads in up to that many processes at nice 19 (home.pool_size).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import random
import re
import subprocess
import sys
import zlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

import home  # noqa: E402
import online  # noqa: E402
import replays  # noqa: E402

WEIGHTS = Path(__file__).with_name("value_weights.json")
LAST_ROUND = 499
EVERY = 10  # the rounds a game gives rows at: its snapshots are strongly correlated from one round to the next
ROUND_BANDS = (100, 250, 400, 480)  # calibration is read on the snapshots at these rounds
HIDDEN = 12


@dataclass(frozen=True)
class Team:
    """One team at a snapshot: its longest living dragon, total length and living dragons."""
    longest: int
    total: int
    alive: int


# The rule's answer where it decides (an elimination, or the end of round 499), else None.
def terminal(r: int, ours: Team, theirs: Team) -> float | None:
    if ours.alive == 0 or theirs.alive == 0:
        return 0.5 if ours.alive == theirs.alive else float(ours.alive > 0)
    if r < LAST_ROUND:
        return None
    for a, b in ((ours.longest, theirs.longest), (ours.total, theirs.total)):
        if a != b:
            return float(a > b)
    return 0.5


FEATURES = ("longest_sd", "total_sd", "longest", "log_total", "log_alive", "log_total_late", "log_alive_area",
            "lead_sd")


# The model's inputs for `ours` against `theirs` at the end of round r on a map of `area` tiles, in FEATURES order.
# `_sd` divides by the square root of the rounds left (a lead that has little time to be undone counts for more),
# `log_` is the log ratio of ours to theirs (plus one each), `_late` scales by the share of the game gone and
# `_area` by the area in 1000s of tiles; lead_sd is +1, -1 or 0 by whom the round-499 rule would pick now.
def features(r: int, ours: Team, theirs: Team, area: int) -> list[float]:
    sd = math.sqrt(max(1, LAST_ROUND - r))
    t = (r + 1) / (LAST_ROUND + 1)
    lead = terminal(LAST_ROUND, ours, theirs)
    log_total = math.log((ours.total + 1) / (theirs.total + 1))
    log_alive = math.log((ours.alive + 1) / (theirs.alive + 1))
    return [(ours.longest - theirs.longest) / sd, (ours.total - theirs.total) / sd / 10,
            (ours.longest - theirs.longest) / 10, log_total, log_alive, log_total * t, log_alive * area / 1000,
            (2 * lead - 1) / sd * 10 if lead is not None else 0.0]


# ------------------------------------------------------------------------------------------------ regressions

# Solves a x = b (a square, lists of lists) by Gaussian elimination with partial pivoting.
def solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [v] for row, v in zip(a, b)]
    for c in range(n):
        p = max(range(c, n), key=lambda i: abs(m[i][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            raise ValueError(f"singular system: column {c} is a combination of the others (a constant feature?)")
        for i in range(n):
            if i != c and m[i][c]:
                f = m[i][c] / m[c][c]
                m[i] = [x - f * y for x, y in zip(m[i], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


# Weighted least squares with a small ridge on every column but the first (the bias, when `bias`): the weights w
# minimising sum(wt * (x.w - y)^2) + ridge * n * |w[1:]|^2.
def least_squares(xs: list[list[float]], ys: list[float], ridge: float = 1e-6, bias: bool = True,
                  wts: list[float] | None = None) -> list[float]:
    k = len(xs[0])
    a = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for x, y, wt in zip(xs, ys, wts or [1.0] * len(xs)):
        for i in range(k):
            xi = x[i] * wt
            b[i] += xi * y
            row = a[i]
            for j in range(i, k):
                row[j] += xi * x[j]
    for i in range(k):
        for j in range(i):
            a[i][j] = a[j][i]
        if i or not bias:
            a[i][i] += ridge * len(xs)
    return solve(a, b)


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


# Logistic regression by iteratively reweighted least squares: labels in [0, 1] (a draw's 1/2 works as a label),
# ridge and bias as least_squares'. Returns the weights over xs' columns.
def logistic(xs: list[list[float]], ys: list[float], ridge: float = 1e-6, bias: bool = True,
             iterations: int = 25) -> list[float]:
    w = [0.0] * len(xs[0])
    for _ in range(iterations):
        wts, zs = [], []
        for x, y in zip(xs, ys):
            z = sum(a * b for a, b in zip(w, x))
            p = sigmoid(z)
            v = max(p * (1 - p), 1e-6)
            wts.append(v)
            zs.append(z + (y - p) / v)
        new = least_squares(xs, zs, ridge, bias, wts)
        done = max(abs(a - b) for a, b in zip(new, w)) < 1e-7
        w = new
        if done:
            break
    return w


def log_loss(ps: list[float], ys: list[float]) -> float:
    eps = 1e-6
    return -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in zip(ps, ys)) / len(ys)


def brier(ps: list[float], ys: list[float]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


# A one-hidden-layer net, tanh units without biases (an even snapshot gives every unit 0) and an output bias `b`:
# {"w1": [HIDDEN rows of len(x)], "w2": [HIDDEN], "b": intercept}, P = sigmoid(net_z) = sigmoid(b + w2 . tanh(w1 x)).
# (fit.py's nets give their units biases by starting every x with a 1, and read net_z itself: a linear output.)
def net_z(model: dict, x: list[float]) -> float:
    hidden = [math.tanh(sum(a * b for a, b in zip(row, x))) for row in model["w1"]]
    return model["b"] + sum(a * b for a, b in zip(model["w2"], hidden))


def net_p(model: dict, x: list[float]) -> float:
    return sigmoid(net_z(model, x))


# Fits net_p's weights by minibatch Adam on the log-loss over `epochs` shuffled passes, from small random weights
# (all seeded); `linear` fits net_z to ys by squared error instead (the same gradient, prediction less label).
def net(xs: list[list[float]], ys: list[float], hidden: int = HIDDEN, epochs: int = 8, batch: int = 128,
        rate: float = 0.01, seed: int = 0, linear: bool = False) -> dict:
    rng = random.Random(seed)
    k = len(xs[0])
    w1 = [[rng.gauss(0, 1 / math.sqrt(k)) for _ in range(k)] for _ in range(hidden)]
    w2 = [rng.gauss(0, 0.5) for _ in range(hidden)]
    m1, s1 = [[0.0] * k for _ in range(hidden)], [[0.0] * k for _ in range(hidden)]
    m2, s2 = [0.0] * hidden, [0.0] * hidden
    b = mb = sb = 0.0
    order = list(range(len(xs)))
    step = 0
    for _ in range(epochs):
        rng.shuffle(order)
        for start in range(0, len(order), batch):
            chunk = order[start:start + batch]
            g1 = [[0.0] * k for _ in range(hidden)]
            g2 = [0.0] * hidden
            gb = 0.0
            for n in chunk:
                x = xs[n]
                h = [math.tanh(sum(a * b for a, b in zip(row, x))) for row in w1]
                z = b + sum(u * v for u, v in zip(w2, h))
                d = (z if linear else sigmoid(z)) - ys[n]
                gb += d
                for j in range(hidden):
                    g2[j] += d * h[j]
                    dh = d * w2[j] * (1 - h[j] * h[j])
                    row = g1[j]
                    for i in range(k):
                        row[i] += dh * x[i]
            step += 1
            c1, c2 = 1 - 0.9 ** step, 1 - 0.999 ** step
            g = gb / len(chunk)
            mb, sb = 0.9 * mb + 0.1 * g, 0.999 * sb + 0.001 * g * g
            b -= rate * mb / c1 / (math.sqrt(sb / c2) + 1e-8)
            for j in range(hidden):
                g = g2[j] / len(chunk)
                m2[j] = 0.9 * m2[j] + 0.1 * g
                s2[j] = 0.999 * s2[j] + 0.001 * g * g
                w2[j] -= rate * m2[j] / c1 / (math.sqrt(s2[j] / c2) + 1e-8)
                for i in range(k):
                    g = g1[j][i] / len(chunk)
                    m1[j][i] = 0.9 * m1[j][i] + 0.1 * g
                    s1[j][i] = 0.999 * s1[j][i] + 0.001 * g * g
                    w1[j][i] -= rate * m1[j][i] / c1 / (math.sqrt(s1[j][i] / c2) + 1e-8)
    return {"w1": w1, "w2": w2, "b": b}


# ------------------------------------------------------------------------------------------------ maps and checks

# {file stem: MAP_NAME, lower case} of a maps/ folder's maps.
def map_names(folder: Path) -> dict[str, str]:
    found = {}
    for path in sorted(folder.glob("*.map")):
        for line in path.read_text().splitlines():
            if line.startswith("MAP_NAME "):
                found[path.stem] = line.split(maxsplit=1)[1].strip().lower()
                break
    return found


# The official maps (every MAP_NAME in maps/ladder, maps/sprint and maps/retired, lower case: the site's Stronghold is
# maps/retired's "stronghold"), and STEMS, the maps a near_ copy can be made from (mapgen's near and community sources:
# those and maps/community) by file stem, which is what a near copy's name carries.
MAPS = Path(__file__).resolve().parent.parent / "maps"
OFFICIAL_STEMS = {stem: name for folder in ("ladder", "sprint", "retired")
                  for stem, name in map_names(MAPS / folder).items()}
OFFICIAL = frozenset(OFFICIAL_STEMS.values())
STEMS = {**map_names(MAPS / "community"), **OFFICIAL_STEMS}
WORLDGEN = "gen-worldgen-"  # the worldgen style's map names: gen-worldgen-<seed>-<its style>-<its landmark>


# The newest of the days on which each map in `added` ({stem: day}) was first committed, and the MAP_NAMEs (`names`,
# {stem: name}) of the maps added on it, sorted.
def latest_added(added: dict[str, str], names: dict[str, str]) -> tuple[str, ...]:
    if not added:
        return ()
    last = max(added.values())
    return tuple(sorted(names[stem] for stem, day in added.items() if day == last and stem in names))


# The maps that joined the ladder pool last, for the "new maps" check: the MAP_NAMEs (lower case) of the maps/ladder
# files git first added on the latest day any of them was added (2026-09-27: Portals and Slithery Fight), so a map
# committed there when the pool gains it (ladder_maps' LADDER MAPS CHANGED alert) becomes the new map; () without git
# history, and then no new-map check.
@functools.cache
def new_maps(folder: Path = MAPS / "ladder") -> tuple[str, ...]:
    try:
        out = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(folder), "log", "--diff-filter=A",
                              "--format=@%cs", "--name-only", "--", "."], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ()
    added, day = {}, None
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        if line.startswith("@"):
            day = line[1:]
        elif line.strip() and day:
            added[Path(line).stem] = day  # newest first: the last day seen is the file's first add
    names = map_names(folder)
    return latest_added({stem: d for stem, d in added.items() if stem in names}, names)


# A map's name, lower case, without a renamed copy's suffix (driver.official_maps names copy k "<name> ~k").
def base_map(map_name: str) -> str:
    return re.sub(r" ~\d+$", "", map_name).lower()


# The map a near copy was made from (mapgen names it near_<stem>_<seed> or near_<stem>_repearl_<seed>), as its
# MAP_NAME in lower case; None for any other map, or a near copy of a map no longer in maps/.
def near_source(map_name: str) -> str | None:
    found = re.fullmatch(r"near_(.+?)(?:_repearl)?_\d+", map_name)
    return STEMS.get(found.group(1)) if found else None


# "worldgen" for the worldgen style's maps (an independent generator: off our generator, held out as a group),
# "generated" for our generator's maps (mapgen: gen-<style>-<seed>, near_<map>_<seed>), "official" for an official map
# or a renamed copy of one, else "other" (community, adversarial: off-generator too, MISSES 61).
def map_group(map_name: str) -> str:
    if map_name.startswith(WORLDGEN):
        return "worldgen"
    if map_name.startswith(("gen-", "near_")):
        return "generated"
    return "official" if base_map(map_name) in OFFICIAL else "other"


# The map a game's rows go with in the leave-one-map-out checks: an official map (a renamed copy's too), the map a
# near copy was made from (a near copy is mostly its map: it trains only where its map does), else None.
def family(map_name: str) -> str | None:
    return base_map(map_name) if map_group(map_name) == "official" else near_source(map_name)


# The number in a local replay's map part (0007-gen_eval_509916568-aB.replay: 509916568; default~2: 2), else a
# stable hash: its parity is the game's half on generated maps.
def map_number(name: str) -> int:
    stem = re.sub(r"^\d+-", "", Path(name).stem)
    stem = re.sub(r"-a[AB]$", "", stem)
    found = re.findall(r"\d+", stem)
    return int(found[-1]) if found else zlib.crc32(stem.encode())


@dataclass(frozen=True)
class Where:
    """What the checks need to know of a row's game: its map's name, its number's parity (1 odd) and when it was
    requested (an online game's ISO time, None for a local one)."""
    map: str
    half: int
    date: str | None


T = TypeVar("T")


# The held-out checks over `items` (value rows or fit.py's decision rows; `where` says each one's game): [(name,
# training items, test items)], in order:
# - "generated, odd": test on the odd-numbered games on our generator's gen- maps, fit on every other game but the odd
#   worldgen ones (the odd games of both never train in any check; the final fit, on every row, takes them);
# - "map <name>" for each official map with items: test on that map, fit without it and without its near copies;
# - "new maps" when there are items on `new` (default new_maps()) and training items for it: fit without the new maps,
#   their near copies and any dated game from the first dated one on them on (online games; an undated local game on
#   another map stays), else, with no dates at all, only without the maps and their copies;
# - "worldgen" when there are worldgen items: test on all of them, fit on every other kept game (no worldgen row).
# Near copies are never tested here: each trains beside its map and is left out with it.
def checks(items: list[T], where: Callable[[T], Where],
           new: tuple[str, ...] | None = None) -> list[tuple[str, list[T], list[T]]]:
    new = new_maps() if new is None else new
    at = [(item, where(item)) for item in items]
    odd = lambda w: w.half and map_group(w.map) == "generated" and not w.map.startswith("near_")
    kept = [(i, w) for i, w in at if not odd(w) and not (w.half and map_group(w.map) == "worldgen")]
    found = [("generated, odd", [i for i, _ in kept], [i for i, w in at if odd(w)])]
    for name in sorted({base_map(w.map) for _, w in at if map_group(w.map) == "official"}):
        found.append((f"map {name}", [i for i, w in kept if family(w.map) != name],
                      [i for i, w in at if map_group(w.map) == "official" and base_map(w.map) == name]))
    on_new = [(i, w) for i, w in at if map_group(w.map) == "official" and base_map(w.map) in new]
    if on_new:
        dated = [w.date for _, w in on_new if w.date]
        cutoff = min(dated) if dated else None
        train = [i for i, w in kept if family(w.map) not in new and
                 (cutoff is None or w.date is None or w.date < cutoff)]
        if train:
            found.append((f"new maps ({'games before ' + cutoff if cutoff else 'left out together, no dates'})",
                          train, [i for i, _ in on_new]))
    worldgen = [i for i, w in at if map_group(w.map) == "worldgen"]
    if worldgen:
        found.append(("worldgen", [i for i, w in kept if map_group(w.map) != "worldgen"], worldgen))
    return found


# ------------------------------------------------------------------------------------------------ the value model

_loaded: dict | None = None


# The saved model (WEIGHTS): {"kind": "logistic", "features": FEATURES, "intercept": b, "weights": [...]} or
# {"kind": "net", "features": FEATURES, "w1", "w2", "b"}.
def load(path: Path = WEIGHTS) -> dict:
    global _loaded
    if path != WEIGHTS:
        return json.loads(path.read_text())
    if _loaded is None:
        _loaded = json.loads(path.read_text())
    return _loaded


def model_p(model: dict, x: list[float]) -> float:
    if model["kind"] == "net":
        return net_p(model, x)
    return sigmoid(model.get("intercept", 0.0) + sum(a * b for a, b in zip(model["weights"], x)))


# The chance `ours` wins from the snapshot at the end of round r: the rule where it decides, else the model.
def p_win(r: int, ours: Team, theirs: Team, area: int, model: dict | None = None) -> float:
    decided = terminal(r, ours, theirs)
    if decided is not None:
        return decided
    model = model or load()
    if list(model["features"]) != list(FEATURES):
        raise ValueError(f"the value model was fit on features {model['features']}, not {list(FEATURES)}: refit it")
    return model_p(model, features(r, ours, theirs, area))


def other(side: str) -> str:
    return "B" if side == "A" else "A"


# Both teams at the end of round r of a replays.Game (its last snapshot once the game is over), from `side`.
def teams_at(game: replays.Game, r: int, side: str) -> tuple[int, Team, Team]:
    s = game.snapshots[min(max(r, 0), len(game.snapshots) - 1)]
    return (s.round, Team(s.longest[side], s.total[side], s.alive[side]),
            Team(s.longest[other(side)], s.total[other(side)], s.alive[other(side)]))


# `side`'s chance of winning at the end of round r of `game`: from its last round on, the result; before round 0,
# round 0's end stands in for the start.
def game_p(game: replays.Game, r: int, side: str, model: dict | None = None) -> float:
    if r >= game.snapshots[-1].round:
        return 0.5 if game.winner is None else float(game.winner == side)
    at, ours, theirs = teams_at(game, r, side)
    return p_win(at, ours, theirs, game.map.width * game.map.height, model)


# ------------------------------------------------------------------------------------------------ data

@dataclass
class Game:
    """One saved game's rows: `rows` is [round, our longest, total, alive, theirs ...] every EVERY rounds from our
    side, `label` our result (1, 0, 1/2), `map` its map's name; `opponent`, `kind` (dev, ladder, local), `half` and
    `date` come from its source (Source), not the replay."""
    path: str
    map: str
    side: str
    label: float
    area: int
    rows: list[list[int]]
    opponent: str = ""
    kind: str = ""
    half: int = 0
    date: str | None = None


@dataclass(frozen=True)
class Source:
    """A saved game to read: its replay, our side, the opponent, its kind, its number's parity and its date."""
    path: str
    side: str
    opponent: str
    kind: str
    half: int
    date: str | None = None


# The saved games to read: every online replay chosen blind to its result under `home`'s online-replays, not kept
# because we lost nor a dev file left by prune beside deleted wins (online.file_selected) (half: the game id's
# parity), and every replay under each local folder (opponent: its subfolder; half: its map number's).
def sources(home: Path | None, folders: list[Path]) -> list[Source]:
    found = []
    for index in sorted(home.glob("online-replays/*/index.json")) if home else []:
        try:
            games = json.loads(index.read_text()).get("games", [])
        except (OSError, ValueError):
            continue
        for g in games:
            if (not isinstance(g, dict) or online.file_selected(g) or not g.get("replay")
                    or g.get("side") not in replays.TEAMS):
                continue
            path = index.parent / Path(g["replay"]).name
            if path.exists():
                found.append(Source(str(path), g["side"], str(g.get("opponent")), "dev" if g.get("dev") else "ladder",
                                    int(g["id"]) % 2, g.get("requested_at")))
    for folder in folders:
        for path in sorted(folder.rglob("*.replay")):
            side = path.stem[-1] if path.stem[-3:-1] == "-a" else None
            if side in replays.TEAMS:
                opponent = path.parent.name if path.parent != folder else folder.name
                found.append(Source(str(path), side, opponent, "local", map_number(path.name) % 2))
    return found


def read_game(source: Source) -> Game | None:
    try:
        game = replays.analyse(replays.load_replay(Path(source.path)))
    except Exception:  # a damaged or unreadable replay gives no rows
        return None
    side = source.side
    label = 0.5 if game.winner is None else float(game.winner == side)
    rows = [[s.round, s.longest[side], s.total[side], s.alive[side],
             s.longest[other(side)], s.total[other(side)], s.alive[other(side)]]
            for s in game.snapshots if s.round % EVERY == 0 or s is game.snapshots[-1]]
    return Game(source.path, game.map.name, side, label, game.map.width * game.map.height, rows)


def _nice() -> None:
    os.nice(19)


REPLAY_FIELDS = ("path", "map", "side", "label", "area", "rows")  # what the cache keeps of a Game


# Every source's Game, from `cache` (JSON lines keyed by path, size and mtime) where it is current, else read (in
# `jobs` processes at nice 19, at most what the load share leaves free here: home.pool_size, which waits while this Mac
# is full, as tools/gated_test.sh does; 29 Sep 13:01 this Mac reached load 51 from coders' analysis) and added to it.
def read_games(found: list[Source], cache: Path | None, jobs: int = 1) -> list[Game]:
    known: dict[str, dict] = {}
    if cache and cache.exists():
        for line in cache.read_text().splitlines():
            row = json.loads(line)
            known[row["key"]] = {k: row[k] for k in REPLAY_FIELDS}
    keys = {}
    for s in found:
        st = Path(s.path).stat()
        keys[s] = f"{s.path}:{st.st_size}:{int(st.st_mtime)}"
    todo = [s for s in found if keys[s] not in known]
    if todo:
        if jobs > 1 and (jobs := home.pool_size(jobs)) > 1:
            import multiprocessing
            with multiprocessing.get_context("spawn").Pool(jobs, initializer=_nice) as pool:
                read = pool.map(read_game, todo, chunksize=8)
        else:
            read = [read_game(s) for s in todo]
        with cache.open("a") if cache else open(os.devnull, "w") as out:
            for s, g in zip(todo, read):
                if g is not None:
                    known[keys[s]] = {k: getattr(g, k) for k in REPLAY_FIELDS}
                    out.write(json.dumps({"key": keys[s], **known[keys[s]]}) + "\n")
    return [Game(**known[keys[s]], opponent=s.opponent, kind=s.kind, half=s.half, date=s.date)
            for s in found if keys[s] in known and known[keys[s]]["side"] == s.side]


@dataclass
class Row:
    x: list[float]
    y: float
    round: int
    game: Game


# A Row for every row of `games` the rule doesn't decide.
def rows_of(games: list[Game]) -> list[Row]:
    out = []
    for g in games:
        for r, *v in g.rows:
            ours, theirs = Team(*v[:3]), Team(*v[3:])
            if terminal(r, ours, theirs) is None:
                out.append(Row(features(r, ours, theirs, g.area), g.label, r, g))
    return out


# ------------------------------------------------------------------------------------------------ the report

def calibration(ps: list[float], ys: list[float]) -> str:
    bins = defaultdict(list)
    for p, y in zip(ps, ys):
        bins[min(int(p * 5), 4)].append((p, y))
    return "  ".join(f"{b * 20}-{b * 20 + 20}%: n {len(v)} said {100 * sum(p for p, _ in v) / len(v):.0f} "
                     f"won {100 * sum(y for _, y in v) / len(v):.0f}" for b, v in sorted(bins.items()))


# Report lines for predictions `ps` on held-out `rows`: log-loss and Brier, by round band, by kind and by opponent.
def report(label: str, ps: list[float], rows: list[Row], detail: bool = True) -> list[str]:
    ys = [r.y for r in rows]
    lines = [f"{label}: {len(rows)} rows of {len({id(r.game) for r in rows})} games; log-loss "
             f"{log_loss(ps, ys):.4f} (a coin 0.6931), Brier {brier(ps, ys):.4f}"]
    if not detail:
        return lines
    for band in ROUND_BANDS:
        at = [(p, r.y) for p, r in zip(ps, rows) if r.round == band]
        if at:
            bp, by = zip(*at)
            lines.append(f"  r{band}: n {len(at)} log-loss {log_loss(bp, by):.4f}; {calibration(bp, by)}")
    kinds, opponents = defaultdict(list), defaultdict(list)
    for p, r in zip(ps, rows):
        kinds[r.game.kind].append((p, r.y))
        opponents[f"{r.game.kind} {r.game.opponent}"].append((p, r.y, id(r.game)))
    for kind, v in sorted(kinds.items()):
        bp, by = zip(*v)
        lines.append(f"  {kind}: n {len(v)} log-loss {log_loss(bp, by):.4f} Brier {brier(bp, by):.4f}")
    lines.append("  by opponent (8+ games, log-loss): " + "; ".join(
        f"{name} {len({k for *_, k in v})}g {log_loss([p for p, *_ in v], [y for _, y, _ in v]):.3f}"
        for name, v in sorted(opponents.items(), key=lambda kv: -len(kv[1])) if len({k for *_, k in v}) >= 8))
    return lines


# The models fit on `train`: "logistic" on every row, and with a net (args.hidden) "net" on a sample of args.net_rows
# rows and "logistic, sample" on the same sample, so the two kinds are compared on the same data; `only` fits just
# that kind.
def fit_models(train: list[Row], args: argparse.Namespace, only: str | None = None) -> dict[str, dict]:
    xs, ys = [r.x for r in train], [r.y for r in train]

    def logistic_model(rows: list[int]) -> dict:
        b, *w = logistic([[1.0, *xs[i]] for i in rows], [ys[i] for i in rows])
        return {"kind": "logistic", "features": list(FEATURES), "intercept": b, "weights": w}

    models = {"logistic": logistic_model(list(range(len(xs))))} if only in (None, "logistic") else {}
    if args.hidden and only in (None, "net"):
        sample = random.Random(1).sample(range(len(xs)), min(len(xs), args.net_rows))
        if only is None:
            models["logistic, sample"] = logistic_model(sample)
        models["net"] = {"kind": "net", "features": list(FEATURES),
                         **net([xs[i] for i in sample], [ys[i] for i in sample], args.hidden, args.epochs)}
    return models


def where_of(row: Row) -> Where:
    return Where(row.game.map, row.game.half, row.game.date)


def fit_main(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
    from home import loop_home
    home_dir = Path(args.home).expanduser() if args.home else loop_home()
    home = None if args.no_online else home_dir
    folders = [Path(f).expanduser() for f in args.folder or [str(home_dir / "evidence-replays")]]
    games = read_games(sources(home, folders), Path(args.cache) if args.cache else None, args.jobs)
    rows = rows_of(games)
    print(f"{len(games)} games, {len(rows)} rows (every {EVERY} rounds, rule-decided ones left out)")
    losses: dict[tuple[str, str], float] = {}
    pooled: dict[str, tuple[list[float], list[Row]]] = defaultdict(lambda: ([], []))
    for name, train, test in checks(rows, where_of):
        if not test:
            continue
        for kind, model in fit_models(train, args).items():
            ps = [model_p(model, r.x) for r in test]
            losses[kind, name] = log_loss(ps, [r.y for r in test])
            one_map = name.startswith("map ")
            if one_map:
                pooled[kind][0].extend(ps)
                pooled[kind][1].extend(test)
            for line in report(f"{kind}, {name} (fit on {len(train)} rows)", ps, test, detail=not one_map):
                print(line)
    for kind, (ps, test) in pooled.items():
        losses[kind, "every map left out"] = log_loss(ps, [r.y for r in test])
        for line in report(f"{kind}, every map left out, pooled", ps, test):
            print(line)
    if not losses:
        print("no held-out rows: nothing fit")
        return 1
    judged = sorted({name for _, name in losses if not name.startswith("map ")})
    print("log-loss on the generated, new-map, worldgen and pooled per-map checks (logistic on every row; on the "
          "net's sample; net):")
    for name in judged:
        print(f"  {name}: " + ", ".join(f"{kind} {losses[kind, name]:.4f}" for kind in
                                        ("logistic", "logistic, sample", "net") if (kind, name) in losses))
    net_wins = bool(args.hidden) and all(losses["net", name] < losses["logistic", name] for name in judged)
    kind = "net" if net_wins else "logistic"
    print(f"chosen: {kind} (the net only when its log-loss is lower than the logistic model's fit on every row on each "
          "of those checks); the final fit is on every row")
    final = fit_models(rows, args, only=kind)[kind]
    if kind == "logistic":
        print(f"logistic weights: intercept {final['intercept']:+.4f} " +
              " ".join(f"{n} {w:+.4f}" for n, w in zip(FEATURES, final["weights"])))
    if args.write:
        WEIGHTS.write_text(json.dumps({**final, "fit": {
            "games": len({id(r.game) for r in rows}), "rows": len(rows), "every": EVERY, "hidden": args.hidden,
            "net_rows": args.net_rows, "final": "every row",
            "log_loss": {f"{k[0]}, {k[1]}": round(v, 4) for k, v in sorted(losses.items())}}}, indent=1) + "\n")
        print(f"wrote {WEIGHTS}")
    return 0


def p_main(args: argparse.Namespace) -> int:
    path = Path(args.replay)
    game = replays.analyse(replays.load_replay(path))
    side = args.side or (path.stem[-1] if path.stem[-3:-1] == "-a" else "A")
    for s in game.snapshots:
        if s.round % args.every == 0 or s is game.snapshots[-1]:
            print(f"r{s.round} {side} longest {s.longest[side]}-{s.longest[other(side)]} total "
                  f"{s.total[side]}-{s.total[other(side)]} alive {s.alive[side]}-{s.alive[other(side)]} "
                  f"P(win) {game_p(game, s.round, side):.3f}")
    print(f"winner {game.winner}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--home", help="the loop's home (default: loop/home.py loop_home)")
    f.add_argument("--no-online", action="store_true", help="only the --folder replays")
    f.add_argument("--folder", action="append", help="local replays (default: <home>/evidence-replays)")
    f.add_argument("--cache")
    f.add_argument("--jobs", type=int, default=1)
    f.add_argument("--hidden", type=int, default=HIDDEN, help="the net's hidden units (0: no net)")
    f.add_argument("--epochs", type=int, default=20)
    f.add_argument("--net-rows", type=int, default=40000, help="training rows the net is fit on (plain Python)")
    f.add_argument("--write", action="store_true")
    p = sub.add_parser("p")
    p.add_argument("replay")
    p.add_argument("--side")
    p.add_argument("--every", type=int, default=50)
    args = parser.parse_args()
    return fit_main(args) if args.command == "fit" else p_main(args)


if __name__ == "__main__":
    sys.exit(main())
