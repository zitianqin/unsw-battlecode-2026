"""Learned decisions: replace a hand rule with a fitted model in one experiment (E0204's ram gate, bot/rammodel.hpp,
is the first). A small spec names the decision; fit.py reads a logging copy's replays, builds one row per logged
decision with what followed it, fits a model of that outcome for each choice (linear, logistic or a small net), checks
it on games it wasn't fit on, then fits it on every row and prints a bot/params.hpp block of its weights.

    .venv/bin/python tools/fit.py SPEC DIR [DIR ...] [--side A|B] [--rows FILE.jsonl]
    .venv/bin/python tools/fit.py --explore-cpp          # the exploration draw to paste into the logging copy

How a coder uses it:
1. Log the decision on the debug line (P.debugIndicator; debugline.hpp or appended to its text in main.cpp): on each
   turn the decision is taken, a marker key (`ro 1`), each input as a number (what the dragon sees when it decides;
   Inputs below) and the choice played (`rt 1`/`rt 0`). A key already on the line (`edge`, `len`) can be an input.
2. Explore: on a hashed share of those turns play the other choice, and log that you did (`rx 1`). The share is a
   pure function of the spec's salt, the map's size, our side, the dragon id and the round (EXPLORE_CPP, which
   `--explore-cpp` prints; explore_draw is its Python twin, and fit.py counts rows whose flag disagrees with it), so
   it is independent of the situation and the fit sees what the other choice leads to. 20-30% is typical (E0204: 30).
3. The logging copy is a copy of the clone's bot in a gitignored folder inside the clone (data/ or replays/, both in
   .gitignore; the coder sandbox writes only inside the clone, and the replays stay there for the post-mortem; never
   commit it). Play it as bot a (`games --a <logging copy> --b <opponent>`): the -aA/-aB in a replay's name is bot
   a's side, so as --b it gives 0 rows. Play it against the champion, the rotation (driver.ROTATION) and, where the
   decision meets them, the mix opponents (earlyking, earlyfold, scout in referee/gauntlet.toml), --one-side, on
   fresh eval maps (mapgen/gen.py --style eval), renamed copies of maps/ladder and maps/sprint (MAP_NAME
   "<name> ~k", as driver.official_maps makes them) and worldgen maps
   (`mapgen/gen.py --style worldgen --seed <n> --count N --out DIR`), one replays folder per opponent
   (`--replays DIR/<opponent>`): E0204 played 120 games for 9,668 rams.
4. Run fit.py on those folders and read its checks (below). Paste the block behind a switch (P.<name>Model), keep
   the hand rule as the switch's off path, and state the checks in the commit's measure-first line.

Inputs (sig, 2026-09-28: "Why not just have the whole rl have all the necessary inputs it needs. Also snake count and
length info too no?"): a spec's features should by default include every input the bot observes that could matter,
not only the ones the hand rule reads:
- the map's size and the round: `tiles_k` (W * H / 1000) and `round` (World's round) are filled in by fit.py from the
  replay when the debug line has no key of that name, so they need no logging (the bot computes the same numbers);
- our dragon count and total length;
- the enemy's dragon count and total length as the team has seen them (not the true numbers: the bot can't see those);
- our head's length and the relevant enemy head's (the one the decision is about, or the nearest);
- the local room (free tiles reachable near the head) and portal proximity;
- and the decision's own inputs (E0204's gain, near friends and enemies).
Don't split by map size by hand: give the linear or logistic model map-size interaction terms (`interactions`: each
pair's product is one more input, e.g. [["rg", "tiles_k"]]) and compare it with the small net (`"model": "net"`) on the
held-out checks; keep whichever holds up there, the simpler one on a tie.

The spec is a JSON object (or a Python dict literal) in a file or inline:
    {"name": "ram",                        # names the report
     "when": "ro",                         # the debug-line key present on the decision's turns: a row per such line
     "features": ["rg", "edge", "rnf", "rne", "tiles_k"],   # debug-line keys (or tiles_k, round), numbers
     "interactions": [["rg", "tiles_k"]],  # optional: feature pairs whose products are inputs too
     "choice": "rt",                       # the debug-line key of the choice played
     "rule": "rr",                         # optional: the key of the hand rule's own choice (needed for 3+ choices)
     "explore": {"field": "rx", "salt": 204, "percent": 30},   # optional; salt and percent as in the bot's draw
     "outcome": {"kind": "win", "rounds": 20},   # what a row's choice is judged by: OUTCOMES below
     "fixed": {"0": 0},                    # optional: choices whose outcome is known without data
     "model": "linear",                    # or "logistic", for a 0/1 outcome (death), or "net" (NET below)
     "better": "higher",                   # or "lower" (death)
     "params": {"prefix": "ramW", "names": {"bias": "Bias", "rg": "Gain", "edge": "Edge"}}}
A row missing an input (or with one that isn't a number) is dropped and counted. With two choices and no `rule` key,
an explored turn's rule choice is the other one, of the choices seen in any game or fixed.
Outcomes (the dragon's team is ours; r is the decision's round, k the outcome's "rounds"):
- win (the default, sig 2026-09-28: fit to winning): our chance of winning at the end of round r + k less at the end
  of round r - 1, from tools/value.py's model of a snapshot (the win rule where it decides). Counts both teams, so a
  choice that survives by hiding and giving up pearls scores what it cost.
- material: our total length less theirs at the end of r + k, less the same at the end of r - 1.
- length: the dragon's own length at the end of r + k (0 once dead), less its length at the turn with "delta": true.
- field: the debug-line key "field" on the dragon's first logged turn at or after r + k (no row once it is dead).
- death: 1 when the dragon dies by the end of r + k, else 0; "kinds" narrows it to tools/watch.py doom kinds
  ("open deeper", "pocket enemy", ...). Use "model": "logistic", "better": "lower".
- ram_swing: E0204's M51 swing of the ram the dragon played that turn (tools/watch.py rams); a row without one has
  no outcome unless its choice is fixed (a declined ram swings 0: "fixed": {"0": 0}).
NET: value.net's one hidden layer of HIDDEN tanh units, each with a bias, and a linear output, fit by squared error on
up to NET_ROWS of a choice's rows with inputs and outcome scaled to mean 0 and sd 1 (the scaling is folded back into
the printed weights). Slower than the others in plain Python, but on E0204's 26,550 rows its 12 fits took less time
than reading the 220 replays.

The data plan for every learned idea (sig asked four times on 2026-09-28 which maps train and which validate):
- Training: every game given, official maps included (renamed copies of maps/ladder and maps/sprint), our
  generator's maps (fresh eval maps, near_* copies), worldgen maps and online dev and ladder games; the final fit, the
  one the bot gets, is on every row.
- Held out, each in its own check below: each official map in turn, with its near_* copies out of that fit; the
  worldgen maps as one group; the maps that joined the pool last (value.new_maps); the odd-numbered generated games.
- The official maps are value.OFFICIAL, read from maps/ladder, maps/sprint and maps/retired: the report prints how
  many there are and how many have rows, never a number written here.
- Each check prints its held-out decisions and maps; one on fewer than READ_MAPS maps (a single left-out map, or
  two new maps) is marked too few to read: judge on the pooled per-map, worldgen and generated checks.

The checks (tools/value.py checks; sig 2026-09-28, tournament maps are unseen): "generated, odd" fits on every game but
the odd-numbered ones on gen- maps and tests on those; "map <name>" fits on every other map and tests on that official
map (leave one map out; a renamed copy is its map, and its near copies, near_<stem>[_repearl]_<seed>, are left out of
the fit with it and tested nowhere); "new maps" tests on the maps that joined the pool last (value.new_maps), fit
without them and their near copies (local games have no dates, so they are left out together); "worldgen" tests on the
worldgen style's maps (gen-worldgen-*, an independent generator) fit without any of them (its odd-numbered games train
in no check, its even ones in every other). A fit carries only if the per-map checks, pooled, the new-map check and,
once there are worldgen games, the worldgen check hold up, not on the generated maps alone; the final fit, on every row,
then takes the held-out games too. For each check fit.py prints each fitted choice's held-out error against the training
mean's, how often the model's pick matches the hand rule's, and the changed decisions: for each way the model would
decide differently (the rule's choice to the model's), the realised outcome where the rule's choice was played and where
the model's was (on explored turns, or its fixed value), their difference and z (rows within a game are correlated, so z
reads high), and that times the share of decisions it changes: the gain a decision. The generated and pooled per-map
checks are split by opponent and by map style and size (MISSES 61: E0204's gain came mostly against our own copy). After
the checks the model is fit once more on every row, and the params block is that final fit; for two choices of a linear
or logistic model it is the second's advantage over the first (the first's fixed value, or its fitted weights,
subtracted), so the bot takes the second when bias + weights . inputs > 0; a net's block is each fitted choice's
weights, and the bot takes the choice whose net (or fixed value) is best.

Dev-team games may be used as data too (sig, 2026-09-28): fit.py reads whatever folders it is given, local replays
(side from the -aA/-aB name) or a folder with the loop's index.json, whose games kept because we lost
(online.file_selected, with the dev files prune leaves beside a version's deleted wins) it leaves out, as every
reader of files does (MISSES 55): they were chosen by their result.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

import online  # noqa: E402
import value  # noqa: E402
import watch  # noqa: E402

OUTCOMES = ("win", "material", "length", "field", "death", "ram_swing")
MODELS = ("linear", "logistic", "net")
BUILTIN = ("tiles_k", "round")  # inputs fit.py fills in from the replay when the debug line lacks them
MIN_ROWS = 20  # a choice needs this many training rows with an outcome for a model of its own
NET_ROWS = 20000  # a net is fit on at most this many of a choice's rows
MASK = 0xFFFFFFFF

# The bot's exploration draw, for the logging copy (World's W, H, teamB, myId and round; bot/state.hpp).
EXPLORE_CPP = """\
// tools/fit.py's exploration draw: true on `percent` of turns, a pure function of (salt, map size, side, dragon,
// round), so the replay says which turns explored (fit.py explore_draw is its twin).
inline bool fitExplore(std::uint32_t salt, World const& w, int percent) {
    std::uint32_t h = salt;
    for (std::uint32_t v : {std::uint32_t(w.W), std::uint32_t(w.H), std::uint32_t(w.teamB), std::uint32_t(w.myId),
                            std::uint32_t(w.round)}) {
        h = (h ^ v) * 0x9E3779B1u;
        h ^= h >> 15;
    }
    h *= 0x85EBCA6Bu;
    h ^= h >> 13;
    return h % 100u < std::uint32_t(percent);
}
"""


# EXPLORE_CPP's draw in Python: the same bits for the same inputs.
def explore_draw(salt: int, width: int, height: int, team_b: bool, dragon: int, round_: int, percent: int) -> bool:
    h = salt & MASK
    for v in (width, height, int(team_b), dragon, round_):
        h = ((h ^ (v & MASK)) * 0x9E3779B1) & MASK
        h ^= h >> 15
    h = (h * 0x85EBCA6B) & MASK
    h ^= h >> 13
    return h % 100 < percent


# ------------------------------------------------------------------------------------------------ the spec

@dataclass
class Spec:
    name: str
    when: str
    features: list[str]
    choice: str
    outcome: dict
    rule: str | None = None
    explore: dict | None = None
    fixed: dict[str, float] = field(default_factory=dict)
    model: str = "linear"
    better: str = "higher"
    params: dict = field(default_factory=dict)
    interactions: list[list[str]] = field(default_factory=list)

    # The model's inputs in order: the features, then each interaction's product, named "<a>*<b>".
    @property
    def inputs(self) -> list[str]:
        return [*self.features, *(f"{a}*{b}" for a, b in self.interactions)]


# A Spec from its JSON object or Python dict literal (a file's text or the text itself), checked: a ValueError names
# what is wrong.
def parse_spec(text: str) -> Spec:
    path = Path(text)
    if len(text) < 4096 and path.suffix in (".json", ".py", ".spec") and path.exists():
        text = path.read_text()
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = ast.literal_eval(text.split("=", 1)[1].strip() if text.lstrip().startswith("SPEC") else text)
        except (ValueError, SyntaxError) as error:
            raise ValueError(f"the spec is neither JSON nor a Python dict literal: {error}") from None
    if not isinstance(data, dict):
        raise ValueError("the spec must be an object")
    missing = [k for k in ("name", "when", "features", "choice") if k not in data]
    if missing:
        raise ValueError(f"the spec needs {', '.join(missing)}")
    unknown = set(data) - {f for f in Spec.__dataclass_fields__}
    if unknown:
        raise ValueError(f"unknown spec keys: {', '.join(sorted(unknown))}")
    if not isinstance(data["features"], list) or not data["features"] or \
            not all(isinstance(f, str) for f in data["features"]):
        raise ValueError("features must be a non-empty list of debug-line keys")
    outcome = {"kind": "win", "rounds": 20, **data.get("outcome", {})}
    if outcome["kind"] not in OUTCOMES:
        raise ValueError(f"outcome kind {outcome['kind']!r} is not one of {', '.join(OUTCOMES)}")
    if not isinstance(outcome.get("rounds"), int) or outcome["rounds"] < 0:
        raise ValueError("outcome rounds must be a whole number of rounds, 0 or more")
    if outcome["kind"] == "field" and not isinstance(outcome.get("field"), str):
        raise ValueError("a field outcome names its debug-line key: {\"kind\": \"field\", \"field\": \"len\"}")
    interactions = data.get("interactions", [])
    if not isinstance(interactions, list) or not all(
            isinstance(pair, list) and len(pair) == 2 and all(f in data["features"] for f in pair)
            for pair in interactions):
        raise ValueError("interactions is a list of feature pairs: [[\"rg\", \"tiles_k\"]], each in features")
    explore = data.get("explore")
    if explore is not None and not (isinstance(explore, dict) and isinstance(explore.get("field"), str)):
        raise ValueError("explore needs its debug-line flag: {\"field\": \"rx\", \"salt\": 1, \"percent\": 30}")
    spec = Spec(**{**data, "outcome": outcome, "fixed": {str(k): float(v) for k, v in data.get("fixed", {}).items()}})
    if spec.model not in MODELS or spec.better not in ("higher", "lower"):
        raise ValueError(f"model is one of {', '.join(MODELS)} and better is higher or lower")
    return spec


# ------------------------------------------------------------------------------------------------ rows

@dataclass
class Row:
    """One logged decision: where it was (map, its number's half, opponent, replay), when and by whom, its inputs
    (spec.inputs), the choice played and the hand rule's (None when it can't be told), whether the logged flag says it explored and
    whether the draw says it should have (None without an explore spec), and its outcome (None when it has none)."""
    map: str
    half: int
    opponent: str
    area: int
    replay: str
    round: int
    dragon: int
    x: list[float]
    choice: str
    rule: str | None
    explored: bool
    drawn: bool | None
    y: float | None


# A dragon's length at the end of round r (0 once it is dead): the next round's opening frame, or at the game's end
# its final body.
def length_at(w: watch.Watched, dragon: int, r: int) -> int:
    frame = w.frames.get(r + 1)
    if frame is not None:
        found = frame.dragons.get(dragon)
        return len(found[1]) if found else 0
    final = w.game.dragons.get(dragon)
    return len(final.body) if final and final.alive else 0


# Our total length less theirs at the end of round r (the game's last snapshot once it is over).
def material(w: watch.Watched, r: int, side: str) -> int:
    _, ours, theirs = value.teams_at(w.game, r, side)
    return ours.total - theirs.total


# The outcome of the decision dragon `dragon` took in round r (spec.outcome), or None when it has none.
def outcome_of(spec: Spec, w: watch.Watched, side: str, r: int, dragon: int, later: dict[int, list],
               swings: dict[tuple[int, int], int], deaths: dict[int, tuple[int, str]]) -> float | None:
    kind, k = spec.outcome["kind"], spec.outcome["rounds"]
    if kind == "win":
        return value.game_p(w.game, r + k, side) - value.game_p(w.game, r - 1, side)
    if kind == "material":
        return float(material(w, r + k, side) - material(w, r - 1, side))
    if kind == "length":
        now = len(w.frames[r].dragons[dragon][1]) if r in w.frames and dragon in w.frames[r].dragons else 0
        return float(length_at(w, dragon, r + k) - (now if spec.outcome.get("delta") else 0))
    if kind == "field":
        for at, fields in later.get(dragon, []):
            if at >= r + k:
                try:
                    return float(fields[spec.outcome["field"]])
                except (KeyError, ValueError):
                    return None
        return None
    if kind == "death":
        died = deaths.get(dragon)
        kinds = spec.outcome.get("kinds")
        return float(died is not None and died[0] <= r + k and (not kinds or died[1] in kinds))
    return None if (r, dragon) not in swings else float(swings[r, dragon])


# The rows of one replay for the team on `side` (default: its -aA/-aB name or index.json), and how many logged
# decisions were dropped for a missing or non-numeric input.
def game_rows(spec: Spec, path: Path, opponent: str, side: str | None = None) -> tuple[list[Row], int]:
    doom = spec.outcome["kind"] == "death" and bool(spec.outcome.get("kinds"))
    w = watch.watch(path, doom=doom)
    side = side or watch.our_side(path)
    if side is None:
        raise ValueError(f"{path}: which side is ours? (name it -aA/-aB, give index.json, or pass --side)")
    later: dict[int, list] = defaultdict(list)
    lines = []
    for r, dragon, team, text in w.logs:
        if team == side:
            fields = watch.log_fields(text)
            later[dragon].append((r, fields))
            if spec.when in fields:
                lines.append((r, dragon, fields))
    swings = {(ram.round, ram.rammer): ram.swing for ram in watch.rams(w, side)} \
        if spec.outcome["kind"] == "ram_swing" else {}
    deaths = {}
    if spec.outcome["kind"] == "death":
        why = watch.deliberate_deaths(w) if doom else {}
        for n, d in enumerate(w.game.deaths):
            if d["team"] == side and d["id"] not in deaths:
                deaths[d["id"]] = (d["round"], watch.death_kind(w.dooms[n], why.get((d["round"], d["id"])))
                                   if doom else "")
    area = w.game.map.width * w.game.map.height
    half = value.map_number(path.name) % 2
    rows, dropped = [], 0
    for r, dragon, fields in lines:
        builtin = {"tiles_k": area / 1000, "round": r}
        try:
            x = [float(fields[f]) if f in fields else builtin[f] for f in spec.features]
        except (KeyError, ValueError):
            dropped += 1
            continue
        x += [x[spec.features.index(a)] * x[spec.features.index(b)] for a, b in spec.interactions]
        if not all(math.isfinite(v) for v in x) or spec.choice not in fields:
            dropped += 1
            continue
        choice = fields[spec.choice]
        explored = bool(spec.explore) and fields.get(spec.explore["field"]) == "1"
        drawn = explore_draw(int(spec.explore.get("salt", 0)), w.game.map.width, w.game.map.height, side == "B",
                             dragon, r, int(spec.explore.get("percent", 0))) if spec.explore else None
        rule = fields.get(spec.rule) if spec.rule else None if explored else choice
        y = spec.fixed[choice] if choice in spec.fixed else outcome_of(spec, w, side, r, dragon, later, swings, deaths)
        rows.append(Row(w.game.map.name, half, opponent, area, str(path), r, dragon, x, choice, rule, explored,
                        drawn, y))
    return rows, dropped


# With two choices and no rule key, an explored turn's rule choice is the other one: of the choices every game's
# rows played and the fixed ones, so a game that logged only one of them still knows the other.
def fill_rules(spec: Spec, rows: list[Row]) -> None:
    values = sorted(set(spec.fixed) | {r.choice for r in rows})
    if not spec.rule and len(values) == 2:
        for row in rows:
            if row.explored:
                row.rule = values[1 - values.index(row.choice)]


# Every replay under each folder: [(path, opponent)], the opponent a replay's subfolder (or the folder's own name),
# and how many replays were left out as kept losses (a folder's index.json game online.file_selected: chosen because
# we lost, or a dev file left beside pruned wins, MISSES 55).
def replays_in(folders: list[Path]) -> tuple[list[tuple[Path, str]], int]:
    found, selected = [], 0
    for folder in folders:
        for path in sorted(folder.rglob("*.replay")):
            game = watch._index_games(path.resolve().parent).get(path.name)
            if game is not None and online.file_selected(game):
                selected += 1
                continue
            found.append((path, path.parent.name if path.parent != folder else folder.name))
    return found, selected


# Every row of the replays in `folders` (explored turns' rule choices filled in across all of them), the decisions
# dropped for a missing or bad input, the games read and the kept losses left out.
def load_rows(spec: Spec, folders: list[Path], side: str | None) -> tuple[list[Row], int, int, int]:
    found, selected = replays_in(folders)
    rows, dropped = [], 0
    for path, opponent in found:
        got, lost = game_rows(spec, path, opponent, side)
        rows += got
        dropped += lost
    fill_rules(spec, rows)
    return rows, dropped, len(found), selected


# ------------------------------------------------------------------------------------------------ fitting

# NET's fit of ys on xs (each x starting with a 1, so every hidden unit has a bias): value.net with a linear output on
# up to NET_ROWS rows, inputs and outcome scaled to mean 0 and sd 1, the scaling folded back into its weights, so
# value.net_z(model, x) is the prediction on unscaled x.
def fit_net(xs: list[list[float]], ys: list[float]) -> dict:
    sample = random.Random(1).sample(range(len(xs)), min(len(xs), NET_ROWS))
    cols = list(zip(*(xs[i] for i in sample)))
    mean = [0.0] + [statistics.fmean(c) for c in cols[1:]]
    sd = [1.0] + [statistics.pstdev(c) or 1.0 for c in cols[1:]]
    y_mean = statistics.fmean(ys[i] for i in sample)
    y_sd = statistics.pstdev([ys[i] for i in sample]) or 1.0
    m = value.net([[1.0] + [(v - a) / b for v, a, b in zip(xs[i][1:], mean[1:], sd[1:])] for i in sample],
                  [(ys[i] - y_mean) / y_sd for i in sample], epochs=10, linear=True)
    w1 = [[row[0] - sum(u * a / b for u, a, b in zip(row[1:], mean[1:], sd[1:]))] +
          [u / b for u, b in zip(row[1:], sd[1:])] for row in m["w1"]]
    return {"w1": w1, "w2": [u * y_sd for u in m["w2"]], "b": y_mean + y_sd * m["b"]}


@dataclass
class Fit:
    """Per fitted choice: its weights (bias first, then spec.inputs; a net's fit_net model) and the training rows'
    mean outcome."""
    spec: Spec
    weights: dict[str, list[float] | dict]
    means: dict[str, float]
    counts: dict[str, int]

    def predict(self, choice: str, x: list[float]) -> float | None:
        if choice in self.spec.fixed:
            return self.spec.fixed[choice]
        w = self.weights.get(choice)
        if w is None:
            return None
        if isinstance(w, dict):
            return value.net_z(w, [1.0, *x])
        z = w[0] + sum(a * b for a, b in zip(w[1:], x))
        return value.sigmoid(z) if self.spec.model == "logistic" else z

    # The choice the model picks for features x, among the fitted and fixed ones.
    def pick(self, x: list[float]) -> str | None:
        scored = [(p, c) for c in sorted(set(self.weights) | set(self.spec.fixed))
                  if (p := self.predict(c, x)) is not None]
        if not scored:
            return None
        return (max if self.spec.better == "higher" else min)(scored)[1]


def fit(spec: Spec, rows: list[Row]) -> Fit:
    by_choice = defaultdict(list)
    for row in rows:
        if row.y is not None and row.choice not in spec.fixed:
            by_choice[row.choice].append(row)
    weights, means, counts = {}, {}, {}
    for choice, chosen in sorted(by_choice.items()):
        counts[choice] = len(chosen)
        if len(chosen) < MIN_ROWS:
            continue
        xs = [[1.0, *r.x] for r in chosen]
        ys = [r.y for r in chosen]
        weights[choice] = value.logistic(xs, ys) if spec.model == "logistic" else fit_net(xs, ys) \
            if spec.model == "net" else value.least_squares(xs, ys)
        means[choice] = statistics.fmean(ys)
    return Fit(spec, weights, means, counts)


# ------------------------------------------------------------------------------------------------ the report

def mean_se(values: list[float]) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    mean = statistics.fmean(values)
    return mean, (statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else math.nan)


@dataclass
class Changed:
    """One way the model decides differently from the rule on held-out rows (rule's choice -> model's): the rows,
    and the realised outcomes where the rule's choice and the model's were played."""
    rows: int = 0
    rule_played: list[float] = field(default_factory=list)
    model_played: list[float] = field(default_factory=list)


# The held-out numbers of `f` on `rows`: each fitted choice's error against the training mean's, the pick's
# agreement with the rule, and the changed decisions by direction.
def score(f: Fit, rows: list[Row]) -> dict:
    errors = {}
    for choice, w in f.weights.items():
        held = [r for r in rows if r.choice == choice and r.y is not None]
        if held:
            pred = [f.predict(choice, r.x) for r in held]
            ys = [r.y for r in held]
            if f.spec.model == "logistic":
                errors[choice] = (len(held), value.log_loss(pred, ys), value.log_loss([f.means[choice]] * len(ys), ys))
            else:
                errors[choice] = (len(held), math.sqrt(statistics.fmean((p - y) ** 2 for p, y in zip(pred, ys))),
                                  math.sqrt(statistics.fmean((f.means[choice] - y) ** 2 for y in ys)))
    judged = [(r, f.pick(r.x)) for r in rows if r.rule is not None]
    judged = [(r, m) for r, m in judged if m is not None]
    changed: dict[tuple[str, str], Changed] = defaultdict(Changed)
    for r, m in judged:
        if m == r.rule:
            continue
        c = changed[r.rule, m]
        c.rows += 1
        if m in f.spec.fixed:
            c.model_played.append(f.spec.fixed[m])
        elif r.choice == m and r.y is not None:
            c.model_played.append(r.y)
        if r.choice == r.rule and r.y is not None:
            c.rule_played.append(r.y)
    return {"errors": errors, "judged": len(judged), "agree": sum(m == r.rule for r, m in judged), "changed": changed}


# The gain a decision from the changed decisions (model's realised outcome less the rule's, times the share of
# decisions changed that way, summed), its standard error and the changed rows left out because their direction has
# no rows where one of its two choices was played; None when every changed row is left out that way.
def gain(result: dict, better: str) -> tuple[float, float, int] | None:
    total, var, unmeasured = 0.0, 0.0, 0
    judged = result["judged"]
    for c in result["changed"].values():
        if not c.model_played or not c.rule_played:
            unmeasured += c.rows
            continue
        (mm, ms), (rm, rs) = mean_se(c.model_played), mean_se(c.rule_played)
        share = c.rows / judged
        diff = (mm - rm) if better == "higher" else (rm - mm)
        total += share * diff
        var += share ** 2 * ((0 if math.isnan(ms) else ms) ** 2 + (0 if math.isnan(rs) else rs) ** 2)
    changed = sum(c.rows for c in result["changed"].values())
    return None if changed and unmeasured == changed else (total, math.sqrt(var), unmeasured)


def score_lines(spec: Spec, result: dict, detail: bool) -> list[str]:
    lines = []
    for choice, (n, err, base) in sorted(result["errors"].items()):
        what = "log-loss" if spec.model == "logistic" else "RMSE"
        lines.append(f"  {spec.choice} {choice}: n {n} {what} {err:.4f} (training mean {base:.4f}"
                     f"{', better' if err < base else ', NOT better'})")
    judged = result["judged"]
    if not judged:
        return lines + ["  no rows with the rule's choice known"]
    changed_rows = sum(c.rows for c in result["changed"].values())
    lines.append(f"  model picks the rule's choice on {result['agree']} of {judged} "
                 f"({100 * result['agree'] / judged:.1f}%); changes {changed_rows}")
    if detail:
        for (rule, model), c in sorted(result["changed"].items()):
            (mm, ms), (rm, rs) = mean_se(c.model_played), mean_se(c.rule_played)
            z = (mm - rm) / math.sqrt((0 if math.isnan(ms) else ms) ** 2 + (0 if math.isnan(rs) else rs) ** 2) \
                if c.model_played and c.rule_played and (ms > 0 or rs > 0) else math.nan
            lines.append(f"    {rule} -> {model}: {c.rows} rows; realised where the rule's was played {rm:+.4f} "
                         f"(n {len(c.rule_played)}), where the model's was {mm:+.4f} (n {len(c.model_played)}"
                         f"{', fixed' if model in spec.fixed else ''}); difference {mm - rm:+.4f} z {z:+.2f}")
    g = gain(result, spec.better)
    lines.append("  gain a decision: " + (f"{g[0]:+.5f} (se {g[1]:.5f})" + (f", {g[2]} changed rows unmeasured"
                                                                            if g[2] else "") if g else
                                          "unknown (no changed row's direction has both its choices played)"))
    return lines


READ_MAPS = 3  # a held-out group on fewer maps than this is too few to read (REFLECT 2026-09-28 14:51 F23)


# A held-out group's size, "N decisions on M maps", marked when it has fewer than READ_MAPS maps (a renamed copy is
# its map). One map's noise isn't a finding: 13:53 on 28 Sep read "slightly negative" off Slithery Fight alone.
def group_size(rows: list[Row]) -> str:
    maps = len({value.base_map(r.map) for r in rows})
    return f"{len(rows)} decisions on {maps} map{'' if maps == 1 else 's'}" + (
        f", too few to read (under {READ_MAPS} maps)" if maps < READ_MAPS else "")


# The map style a report splits by: generated maps by mapgen style (meta.SPLIT_STYLES, as driver.map_style; else
# mix), near variants as near, others by map; and a size band (loop/meta.py AREA_BANDS).
def style_of(map_name: str) -> str:
    import meta
    if value.map_group(map_name) == "worldgen":
        return "worldgen"
    if map_name.startswith("gen-"):
        kind = map_name.split("-")[1]
        return kind if kind in meta.SPLIT_STYLES else "mix"
    return "near" if map_name.startswith("near_") else value.base_map(map_name)


def size_of(area: int) -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
        import meta
        return meta.area_band(area)
    except Exception:  # meta can't be imported here: no size split
        return "?"


def split_lines(spec: Spec, f: Fit, rows: list[Row]) -> list[str]:
    lines = []
    for label, key in (("opponent", lambda r: r.opponent), ("style", lambda r: style_of(r.map)),
                       ("size", lambda r: size_of(r.area))):
        groups = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        parts = []
        for name, group in sorted(groups.items()):
            result = score(f, group)
            g = gain(result, spec.better)
            changed = sum(c.rows for c in result["changed"].values())
            parts.append(f"{name} {len(group)} rows, changes {changed}, gain "
                         + (f"{g[0]:+.4f} (se {g[1]:.4f})" if g else "?"))
        lines.append(f"  by {label}: " + "; ".join(parts))
    return lines


def params_block(spec: Spec, f: Fit, rows: int) -> list[str]:
    prefix = spec.params.get("prefix", spec.name)
    names = spec.params.get("names", {})
    kinds = sorted(set(f.weights) | set(spec.fixed))
    name_of = lambda key: prefix + names.get(key, "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", key)))
    where = f"tools/fit.py {spec.name}: {spec.outcome['kind']} over {spec.outcome['rounds']} rounds, {rows} rows"
    if spec.model == "net":
        fixed = ", ".join(f"{c} is {v:g}" for c, v in sorted(spec.fixed.items()))
        lines = [f"    // expected {spec.outcome['kind']} per {spec.choice} by a net ({where}): take the choice with "
                 f"the {spec.better}{'; ' + fixed if fixed else ''}. net(x) = B + sum_j W2[j] * tanh(W1[j][0] + "
                 f"sum_i W1[j][i + 1] * x[i]), x = ({', '.join(spec.inputs)})"]
        for c in sorted(f.weights):
            m = f.weights[c]
            rows_text = ", ".join("{" + ", ".join(f"{v:.4g}" for v in row) + "}" for row in m["w1"])
            lines += [f"    double {name_of('net')}W1_{c}[{len(m['w1'])}][{len(m['w1'][0])}] = {{{rows_text}}};",
                      f"    double {name_of('net')}W2_{c}[{len(m['w2'])}] = {{"
                      + ", ".join(f"{v:.4g}" for v in m["w2"]) + "};",
                      f"    double {name_of('net')}B_{c} = {m['b']:.4g};"]
        return lines
    if len(kinds) == 2 and kinds[1] in f.weights and (kinds[0] in f.weights or kinds[0] in spec.fixed):
        first, second = kinds
        w = list(f.weights[second])
        base = f.weights.get(first) or [spec.fixed[first]] + [0.0] * len(spec.inputs)
        w = [a - b for a, b in zip(w, base)]
        lines = [f"    // {spec.choice} {second} over {first}: take {second} when bias + weights . inputs > 0 "
                 f"({where})"]
        blocks = [("", w)]
    else:
        lines = [f"    // expected {spec.outcome['kind']} per {spec.choice} ({where})"]
        blocks = [(f"_{c}", f.weights[c]) for c in sorted(f.weights)]
    for suffix, w in blocks:
        lines.append(f"    double {name_of('bias')}{suffix} = {w[0]:.4g};")
        for key, v in zip(spec.inputs, w[1:]):
            lines.append(f"    double {name_of(key)}{suffix} = {v:.4g};  // per unit of {key}")
    return lines


def run(spec: Spec, folders: list[Path], side: str | None, rows_out: Path | None) -> int:
    rows, dropped, games, selected = load_rows(spec, folders, side)
    print(f"{spec.name}: {len(rows)} decisions in {games} games ({dropped} dropped: a missing or bad input; "
          f"{selected} kept losses left out), {sum(r.y is not None for r in rows)} with an outcome "
          f"({spec.outcome['kind']} over {spec.outcome['rounds']} rounds)")
    counts = defaultdict(int)
    for r in rows:
        counts[r.choice, r.explored] += 1
    print("  by choice (played, explored): " + ", ".join(f"{c} {'explored' if e else 'rule'} {n}"
                                                         for (c, e), n in sorted(counts.items())))
    if spec.explore:
        wrong = sum(r.explored and not r.drawn for r in rows)
        print(f"  exploration: {sum(r.explored for r in rows)} explored rows, {sum(bool(r.drawn) for r in rows)} "
              f"drawn; {wrong} explored rows the draw didn't pick (0 unless the bot's draw differs from EXPLORE_CPP)")
    if rows_out:
        rows_out.write_text("".join(json.dumps(r.__dict__) + "\n" for r in rows))
    if not rows:
        return 1
    seen = {value.base_map(r.map) for r in rows} & value.OFFICIAL
    print(f"  official maps: {len(value.OFFICIAL)} in the pool (maps/ladder, sprint, retired), {len(seen)} with rows")
    pooled: list[Row] = []
    pooled_fits: list[tuple[Fit, list[Row]]] = []
    for name, train, test in value.checks(rows, lambda r: value.Where(r.map, r.half, None)):
        if not test:
            continue
        f = fit(spec, train)
        per_map = name.startswith("map ")
        result = score(f, test)
        print(f"{name}: fit on {len(train)} rows ({', '.join(f'{c} {n}' for c, n in sorted(f.counts.items()))}), "
              f"tested on {group_size(test)}")
        for line in score_lines(spec, result, detail=not per_map):
            print(line)
        if per_map:
            pooled += test
            pooled_fits.append((f, test))
        elif name == "generated, odd":
            for line in split_lines(spec, f, test):
                print(line)
    if pooled_fits:
        merged = {"errors": {}, "judged": 0, "agree": 0, "changed": defaultdict(Changed)}
        for f, test in pooled_fits:
            result = score(f, test)
            merged["judged"] += result["judged"]
            merged["agree"] += result["agree"]
            for key, c in result["changed"].items():
                m = merged["changed"][key]
                m.rows += c.rows
                m.rule_played += c.rule_played
                m.model_played += c.model_played
        print(f"every official map left out, pooled: {group_size(pooled)}")
        for line in score_lines(spec, merged, detail=True):
            print(line)
    final = fit(spec, rows)
    print(f"final fit, on every row: {len(rows)} rows "
          f"({', '.join(f'{c} {n}' for c, n in sorted(final.counts.items()))})")
    if final.weights:
        print("params (the final fit):")
        for line in params_block(spec, final, len(rows)):
            print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", nargs="?", help="the spec: a .json file, or its JSON text")
    parser.add_argument("folders", nargs="*", type=Path, help="replay folders (one subfolder per opponent)")
    parser.add_argument("--side", choices=("A", "B"), help="our side, when replay names don't say")
    parser.add_argument("--rows", type=Path, help="also write every row as JSON lines here")
    parser.add_argument("--explore-cpp", action="store_true", help="print the bot's exploration draw and exit")
    args = parser.parse_args()
    if args.explore_cpp:
        print(EXPLORE_CPP, end="")
        return 0
    if not args.spec or not args.folders:
        parser.error("give a spec and at least one replay folder")
    try:
        spec = parse_spec(args.spec)
    except ValueError as error:
        parser.error(str(error))
    return run(spec, args.folders, args.side, args.rows)


if __name__ == "__main__":
    sys.exit(main())
