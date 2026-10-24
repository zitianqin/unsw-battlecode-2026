"""Whether a tune's best values differ by map area or style (sig, 2026-09-28: tuned values aren't checked for
generalising the way fitted models are, and a tuned constant can fit our generator's mix of maps).

After a tune's SPSA (driver.run_tuner) its samples are split by each map's area and style group (eval: our generator's
own layouts; near: its copies of the official and community maps; worldgen: the independent generator; other), from
the SPSA samples already played: no new games. A perturbation's score times its nudge's sign (+1 raised the value) is
a sample of the value's gradient at the round's value, x nudges from the tuned value. Per value, least squares fits
each group's gradient as a line in x with its own intercept and curvature (gradient = a_g + c_g x); by area the groups
are the maps up to the median area the tune played and those above it, an edge the tune's maps set, not a hand-picked
band. Standard errors are cluster-robust by perturbation (a perturbation's map pairs share its nudges). A group's best
value is where its line crosses zero, tuned + nudge x (-a_g / c_g), placed only where its curvature is clearly concave
(CONCAVE_Z) and short of the clamp (tune.clamp_range), and shown to the formula idea, never tested.

The test asks whether two groups' gradients at the tuned value (a_g) point opposite ways, each clearly. When every
group has its best at one shared value (and each group's score rises toward its best), every group's gradient at any
value points toward that best, whatever its curvature, so a group reacting 12x more has a steeper gradient but not
another sign; and it keeps its sign through a clamped nudge, a non-linear response or a tune that ended short of the
best. So wrong-way luck needs one group's gradient clearly off its true sign: p = groups x the one-sided normal tail at
the weaker of the most positive and most negative z (Bonferroni over the group that could be the wrong-way one). Only
groups with MIN_BAND_PAIRS map pairs or more are tested. Flagged at p <= chance.TRIGGER_P / (values x splits tested)
(Bonferroni again). It sees only bests that lie on both sides of the tuned value, which SPSA ends between, since it
ends near the mix's best. A flagged value's result says so (text), and the loop queues a formula idea replacing the
constant with a formula of what the bot observes (proposals).

Calibration (2026-09-28, loop/test_tunesplit.simulated: 150-500 tunes of 2 and of 5 values, 200 perturbations of 3
map pairs, win/draw/loss games). With one best everywhere, at most 1.7% of tunes flagged at the bar (10% nominal),
3.4% on old records, under curvature 1:4:12 by size band, 1:12 over area and 4x on worldgen, flat, weak or none, a best
the tune travelled 10 nudges to or ended 5 short of, and a best past the clamp. A best falling 6 nudges from the
smallest maps to the largest was flagged in 98-99% of tunes (old records 71-79%), one falling 3 nudges in 66-79%, and
one 3 nudges apart by style in 99-100%. 5eb6f65's test (the Wald chi-square of one shared crossing point, sought inside
the clamp) flagged 8-12% of the 1:4:12 nulls, 29-48% when the tune travelled far, 29-99% of bests past the clamp, and
45-70% of the 6-nudge area differences: a straight line through a response that levels off crosses zero in a
different place per curvature.

Records: tune.py logs each perturbation's nudges, maps and per-map-pair scores since 2026-09-28 ("trials"). Older
tune.json files have only each round's values and each perturbation's score over its `pairs` maps, so their nudges are
replayed from the tune's seed (tune.py draws them in order from random.Random(seed); `nudge_check` confirms each
round's move agrees in sign with them) and each perturbation counts by its maps' mean features (their style shares,
their mean log area, whose median splits them by area): a mix of maps whose gradients all point one way points that
way too. Their map order is rebuilt: sorted file names for a tune without a map mix (run_tuner's order since tunes
began, f5479c6), today's driver.mixed_order for one with a mix, which is marked unverified ("map_order"), since the
order code has changed since (E0234's single-style mix was checked by hand against 9f3e6bc's code on 2026-09-28: the
same 480 maps in the same order).

    uv run loop/tunesplit.py E0180 E0154     # one or more recorded tunes
    uv run loop/tunesplit.py --all           # every tune in the state's runs/
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

import chance
import meta
from tune import clamp_range

MIN_BAND_PAIRS = 30  # a group with fewer map pairs isn't tested: its robust standard error is unreliable
CONCAVE_Z = -1.96  # a group's curvature must be this clearly below zero to place its best value (95%)
STYLE_GROUPS = ("eval", "near", "worldgen", "other")
SPLITS = ("area", "style")
SPLIT_WORDS = {"area": "map area", "style": "style"}


# A map's style group: worldgen, near (mapgen's copies of official and community layouts), eval (the rest of mapgen's
# own layouts: gen-normal, trap, deadend, fortress, portalrooms, macro) or other (official, community and adversarial
# maps as they are; a file without a MAP_NAME counts as eval, as driver.map_style's "mix"). None when the file can't be
# read.
def style_group(path: Path) -> str | None:
    import driver  # lazily: driver imports this module
    if driver.map_area(path) is None:
        return None
    name, style = driver.map_name(path), driver.map_style(path)
    if style == "worldgen":
        return "worldgen"
    if style.startswith("near_"):
        return "near"
    return "eval" if name.startswith("gen-") or not name else "other"


# A map's feature under `split`: its style group, or its area in tiles for "area"; None when its file can't be read.
def feature(path: Path, split: str) -> str | int | None:
    import driver
    return style_group(path) if split == "style" else driver.map_area(path)


# The tune's maps in the order tune.py played them, from its experiment record `exp` (experiment.json), and how that
# order was rebuilt: "sorted" (the sorted fresh maps, run_tuner's order for a tune without a mix since tunes began) or
# "rebuilt" (a map mix's order from today's driver.mixed_order, unverified: the order code has changed since some
# recorded tunes ran).
def played_maps(run_dir: Path, exp: dict) -> tuple[list[str], str]:
    import driver
    tune, evaluation = exp["session"]["tune"], exp.get("evaluation") or {}
    folder = run_dir / "tune-maps"
    if mix := evaluation.get("maps"):
        count = tune["iterations"] * tune["perturbations"] * tune["pairs"]
        return driver.mixed_order(folder, evaluation["tune_seed"], count, mix,
                                  driver.sprt_seat(1 + len(evaluation.get("rotation", [])))), "rebuilt"
    return sorted(str(m) for m in folder.glob("*.map")), "sorted"


# The nudges of `count` perturbations over `params` as tune.py drew them from `seed`: {param: +1 or -1} each.
def replayed_nudges(seed: int, params: list[str], count: int) -> list[dict[str, int]]:
    rng = random.Random(seed)
    return [{p: rng.choice((-1, 1)) for p in params} for _ in range(count)]


# The rounds of a tune.json (`rounds`, or `iterations` before rounds were batched).
def rounds_of(log: dict) -> list[dict]:
    return log.get("rounds") or log.get("iterations") or []


# Each round's starting values: the tune's start (as moved to keep its constraints), then each round's result.
def round_starts(log: dict) -> list[dict[str, float]]:
    start = dict(log["start"])
    for row in log.get("projections") or []:
        if row.get("round") == 0 and row.get("point") == "start":
            start |= {name: moved[1] for name, moved in row["moved"].items()}
    rounds = rounds_of(log)
    return [start] + [r["theta"] for r in rounds[:-1]]


# The share of (round, value) moves whose sign matches the sum of score x nudge that SPSA stepped on (clamped and
# constrained moves are left out: a clamp can stop a move), from `nudges` in play order: 1.0 when the nudges are the
# ones played, about 0.5 when not.
def nudge_check(log: dict, nudges: list[dict[str, int]]) -> float:
    agree = total = i = 0
    starts, projected = round_starts(log), {row.get("round") for row in log.get("projections") or []}
    for k, row in enumerate(rounds_of(log)):
        scores = row["scores"]
        for p in log["params"]:
            pull = sum(s * nudges[i + j][p] for j, s in enumerate(scores))
            moved = row["theta"][p] - starts[k][p]
            low, high = clamp_range("int" if isinstance(log["final"][p], int) else "double", log["start"][p])
            if abs(pull) < 1e-9 or (k + 1) in projected or not low < row["theta"][p] < high:
                continue
            total += 1
            agree += (moved > 0) == (pull > 0) and abs(moved) > 0
        i += len(scores)
    return agree / total if total else 0.0


# The tune's samples for `split`: (cluster, z, x, y), one per map pair when tune.json has "trials", else one per
# perturbation from its maps' mean features, where z is {group: share} for style or {"L": mean log area} for area, x
# the round's value in nudges from the tuned value and y the score times the nudge's sign. `maps` and `nudges` are only
# for records without "trials"; `cache` holds each map's feature across calls. Also returns each feature's map pairs
# (per style group, or per area in tiles).
Sample = tuple[int, dict[str, float], float, float]


def samples(log: dict, param: str, split: str, maps: list[str] | None = None, nudges: list[dict] | None = None,
            pairs: int = 3, cache: dict | None = None) -> tuple[list[Sample], dict]:
    cache = {} if cache is None else cache

    def of(path: str):
        if path not in cache:
            cache[path] = feature(Path(path), split)
        return cache[path]

    def basis(found: list) -> dict[str, float]:
        if split == "style":
            return {g: found.count(g) / len(found) for g in set(found)}
        return {"L": sum(math.log(area) for area in found) / len(found)}

    final, nudge = log["final"][param], log["nudges"][param]
    rows, counts, i = [], {}, 0
    for row, start in zip(rounds_of(log), round_starts(log)):
        x = (start[param] - final) / nudge
        for score in row["scores"]:
            if trials := log.get("trials"):
                trial = trials[i]
                for path, pair_score in zip(trial["maps"], trial["pairs"], strict=True):
                    if (f := of(path)) is not None:
                        rows.append((i, basis([f]), x, pair_score * trial["delta"][param]))
                        counts[f] = counts.get(f, 0) + 1
            else:
                mine = [f for path in maps[i * pairs:(i + 1) * pairs] if (f := of(path)) is not None]
                if mine:
                    rows.append((i, basis(mine), x, score * nudges[i][param]))
                    for f in mine:
                        counts[f] = counts.get(f, 0) + 1
            i += 1
    return rows, counts


# The inverse of a small symmetric matrix (Gauss-Jordan), or None when it is singular.
def inverse(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                factor = a[r][col]
                a[r] = [v - factor * w for v, w in zip(a[r], a[col])]
    return [row[n:] for row in a]


# Least squares y = sum_b w_b (a_b + c_b x) over `rows` (samples), with cluster-robust covariance (by perturbation,
# times G/(G-1) for G clusters). Returns (columns, coefficients, covariance), columns being (basis, "a") and (basis,
# "c") for each basis in `order` with any weight; None when the fit is singular (x never varied, or every row had the
# same shares of the bases).
def fit(rows: list[Sample], order: tuple[str, ...]) -> tuple[list[tuple[str, str]], list[float], list[list[float]]] | None:
    present = [b for b in order if any(w.get(b) for _, w, _, _ in rows)]
    columns = [(b, part) for b in present for part in ("a", "c")]
    design = [[w.get(b, 0.0) * (x if part == "c" else 1.0) for b, part in columns] for _, w, x, _ in rows]
    k = len(columns)
    xtx = [[sum(r[i] * r[j] for r in design) for j in range(k)] for i in range(k)]
    bread = inverse(xtx) if rows else None
    if bread is None:
        return None
    xty = [sum(r[i] * y for r, (_, _, _, y) in zip(design, rows)) for i in range(k)]
    beta = [sum(bread[i][j] * xty[j] for j in range(k)) for i in range(k)]
    scores: dict[int, list[float]] = {}
    for r, (cluster, _, _, y) in zip(design, rows):
        e = y - sum(b * v for b, v in zip(beta, r))
        s = scores.setdefault(cluster, [0.0] * k)
        for i in range(k):
            s[i] += r[i] * e
    g = len(scores)
    meat = [[sum(s[i] * s[j] for s in scores.values()) * g / max(g - 1, 1) for j in range(k)] for i in range(k)]
    half = [[sum(bread[i][m] * meat[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    cov = [[sum(half[i][m] * bread[m][j] for m in range(k)) for j in range(k)] for i in range(k)]
    return columns, beta, cov


# The chance a standard normal is at least `z` by luck alone.
def normal_sf(z: float) -> float:
    return math.erfc(z / math.sqrt(2)) / 2


# Whether `value` sits within half a nudge of the clamp range [low, high] or past it (an unbounded side never).
def at_clamp(value: float, nudge: float, low: float, high: float) -> bool:
    return value - low < nudge / 2 or high - value < nudge / 2


# A gradient line's row for display: its gradient at the tuned value `a` ± se, its curvature `c` ± se with z, and,
# when the curvature is clearly concave, its best value (final + nudge x -a/c), or "past_clamp" when that lies at or
# past the tune's clamp, where no played value measured it. `va` and `vc` are the variances of a and c.
def line_row(a: float, c: float, va: float, vc: float, final: float, nudge: float, low: float, high: float) -> dict:
    z = c / math.sqrt(vc) if vc > 0 else 0.0
    row = {"gradient": round(a, 4), "se": round(math.sqrt(max(va, 0.0)), 4), "curvature": round(c, 4),
           "curve_se": round(math.sqrt(max(vc, 0.0)), 4), "curve_z": round(z, 2)}
    if c < 0 and z <= CONCAVE_Z:
        best = final + nudge * (-a / c)
        row |= {"past_clamp": True} if at_clamp(best, nudge, low, high) else {"best": round(best, 4)}
    return row


# One value under one split: each group's gradient line (line_row; for area, the maps up to the median area played and
# those above it), and whether two groups' gradients at the tuned value point opposite ways, each clearly: "p" is the
# chance of that by luck alone when every group shares one best value (then every gradient points toward it), over
# the groups with MIN_BAND_PAIRS map pairs or more ("tested"). `per_row` is each row's map pairs (a perturbation's, for
# records without "trials"), and `kind` and `start` give the clamp (tune.clamp_range).
def split_test(rows: list[Sample], counts: dict, split: str, final: float, nudge: float, kind: str,
               start: float, per_row: int = 1) -> dict:
    low, high = clamp_range(kind, start)
    xs = [x for _, _, x, _ in rows]
    if not rows or max(xs) - min(xs) < 1e-9:
        return {"bands": {}, "p": None, "why": "the tuned value never moved, so no line can be fitted"}
    if split == "area":
        logs = sorted(w["L"] for _, w, _, _ in rows)
        median = logs[(len(logs) - 1) // 2]
        edge = round(math.exp(median))
        order = (f"up to {edge} tiles", f"over {edge} tiles")
        rows = [(cluster, {order[w["L"] > median]: 1.0}, x, y) for cluster, w, x, y in rows]
        counts = {g: per_row * sum(1 for _, w, _, _ in rows if g in w) for g in order}
    else:
        order = STYLE_GROUPS
    fitted = fit(rows, order)
    if fitted is None:  # E0076's and E0086's perturbations each played 2 eval maps and 1 near map, so no style split
        return {"bands": {}, "p": None, "why": "the samples can't tell the groups apart (every perturbation's maps had "
                                               "the same shares of them)"}
    columns, beta, cov = fitted
    at = {column: i for i, column in enumerate(columns)}
    bands = {g: line_row(beta[at[g, "a"]], beta[at[g, "c"]], cov[at[g, "a"]][at[g, "a"]],
                         cov[at[g, "c"]][at[g, "c"]], final, nudge, low, high) | {"pairs": counts.get(g, 0)}
             for g in order if (g, "a") in at}
    tested = [g for g in bands if counts.get(g, 0) >= MIN_BAND_PAIRS and cov[at[g, "a"]][at[g, "a"]] > 0]
    if len(tested) < 2:
        return {"bands": bands, "p": None}
    z = [beta[at[g, "a"]] / math.sqrt(cov[at[g, "a"]][at[g, "a"]]) for g in tested]
    # each of the two signs is clear at the same level, Bonferroni over the groups that could be the wrong-way one
    return {"bands": bands, "p": min(1.0, len(tested) * normal_sf(min(max(z), -min(z)))), "tested": tested}


# The whole check for a tune: {"params": {param: {split: split_test}}, "tests", "bar", "flagged": [[param, split]],
# "nudge_check"} from tune.json `log`; `maps`, `nudges` and `pairs` for records without "trials" (samples).
def check(log: dict, maps: list[str] | None = None, nudges: list[dict] | None = None, pairs: int = 3) -> dict:
    report: dict = {"params": {}, "values": log["final"], "starts": log["start"], "nudges": log["nudges"]}
    if not log.get("trials"):
        report["nudge_check"] = round(nudge_check(log, nudges), 3)
    for split in SPLITS:
        cache: dict = {}
        for param in log["params"]:
            rows, counts = samples(log, param, split, maps, nudges, pairs, cache)
            kind = "int" if isinstance(log["final"][param], int) else "double"
            report["params"].setdefault(param, {})[split] = split_test(
                rows, counts, split, log["final"][param], log["nudges"][param], kind, log["start"][param],
                1 if log.get("trials") else pairs)
    tests = [(p, s) for p, splits in report["params"].items() for s, t in splits.items() if t["p"] is not None]
    report["tests"] = len(tests)
    report["bar"] = chance.TRIGGER_P / max(len(tests), 1)
    report["flagged"] = [[p, s] for p, s in tests if report["params"][p][s]["p"] <= report["bar"]]
    return report


# check() for the tune recorded in `run_dir` (tune.json and experiment.json), or {"error": why} when its records can't
# support it: no tune.json, maps missing, or replayed nudges that disagree with its moves (nudge_check under 0.9). A
# tune that reused another experiment's values (driver.resume_tune) gets that experiment's check, with "from" naming it.
# A record without "trials" says how its map order was rebuilt ("map_order", played_maps).
def analyse(run_dir: Path) -> dict:
    try:
        log = json.loads((run_dir / "tune.json").read_text())
        exp = json.loads((run_dir / "experiment.json").read_text())
    except (OSError, ValueError) as error:
        return {"error": f"no tune records: {error}"}
    if (origin := log.get("resumed_from")) and origin != run_dir.name:
        return analyse(run_dir.parent / origin) | {"from": origin}
    if log.get("trials"):
        return check(log)
    tune, seed = exp["session"].get("tune") or {}, (exp.get("evaluation") or {}).get("tune_seed")
    count = sum(len(r["scores"]) for r in rounds_of(log))
    if seed is None or not tune or not count:
        return {"error": "no tune seed, spec or rounds recorded"}
    try:
        maps, order = played_maps(run_dir, exp)
    except StopIteration:  # a map mix's folder holds fewer maps than it dealt
        return {"error": "its map mix's maps are missing"}
    if len(maps) < count * tune["pairs"]:
        return {"error": f"{len(maps)} maps found, {count * tune['pairs']} played"}
    nudges = replayed_nudges(seed, log["params"], count)
    if nudge_check(log, nudges) < 0.9:
        return {"error": f"replayed nudges agree with only {nudge_check(log, nudges):.0%} of its moves"}
    return check(log, maps, nudges, tune["pairs"]) | {"map_order": order}


def band_text(name: str, row: dict) -> str:
    extra = [f"{row['pairs']:g}"] if "pairs" in row else []
    extra += [f"best {row['best']:g}"] if "best" in row else ["best past its clamp"] if row.get("past_clamp") else []
    return f"{name} {row['gradient']:+.3f}±{row['se']:.3f}" + (f" ({', '.join(extra)})" if extra else "")


# One flagged (or tested) value under one split, for the result line and the formula idea: "by map area (p 0.0037:
# up to 1024 tiles +0.150±0.040 (360, best 440), over 1024 tiles -0.120±0.040 (240))".
def split_line(split: str, t: dict) -> str:
    bands = ", ".join(band_text(b, t["bands"][b]) for b in t["tested"])
    return f"by {SPLIT_WORDS[split]} (p {t['p']:.2g}: {bands})"


# The words for a record whose map order was rebuilt, not logged (analyse's "map_order"), or "".
def order_text(report: dict) -> str:
    if report.get("map_order") == "rebuilt":
        return " (map order rebuilt with today's driver.mixed_order, unverified)"
    return ""


# One line for a tune's result: " Tune generalisation (...): FLAGGED <param> by map area or style (...)", else its head
# ending " no value's groups clearly want it moved opposite ways." or " no value could be tested.", or " Tune
# generalisation: not checked (<error>)."; "" for no report.
def text(report: dict) -> str:
    if not report:
        return ""
    if "error" in report:
        return f" Tune generalisation: not checked ({report['error']})."
    head = (f" Tune generalisation{' (of ' + report['from'] + ', whose tune it reused)' if report.get('from') else ''}"
            f"{order_text(report)} (does each value's best differ by map area or style? each group's gradient at the "
            f"tuned value, score per nudge ± se (map pairs, best where its own curvature places one); flagged when two "
            f"groups' gradients point opposite ways, each clearly, bar p <= {report['bar']:.4f} over {report['tests']} "
            "tests):")
    if not report["tests"]:
        return head + " no value could be tested."
    if not report["flagged"]:
        return head + " no value's groups clearly want it moved opposite ways."
    parts = [f"{param} " + split_line(split, report["params"][param][split]) for param, split in report["flagged"]]
    return head + " FLAGGED " + "; ".join(parts) + ": each gets a formula idea (source tunesplit:<experiment>)."


# The alert line for a tune's official-map report (driver.check_official: the tuned values against the champion they
# were tested against on renamed copies of every official map, both sides under several pearl seeds), a report and
# never a gate (CLAUDE.md), with its score by map and each map's area. It is not a held-out check: the tune's maps
# (`tune_maps`, its tune-maps folder) include mapgen's near copies of the official maps, counted per map. `maps` is the
# report's official-maps folder, for each map's area.
def official_text(exp_id: str, idea_id: str, official: dict, maps: Path, tune_maps: Path) -> str:
    import driver
    styles = [driver.map_style(path) for path in tune_maps.rglob("*.map")]
    near: dict[str, int] = {}
    for style in styles:
        if style.startswith("near_"):
            near[style.removeprefix("near_")] = near.get(style.removeprefix("near_"), 0) + 1
    per_map = official.get("per_map") or {}
    parts = [f"{stem} {score:.2f} ({driver.map_area(maps / f'{stem}~0.map') or '?'} tiles, {near.get(stem, 0)} near "
             "copies in the tune)" for stem, score in per_map.items()]
    return (f"TUNE OFFICIAL-MAP REPORT: {exp_id} ({idea_id}) tuned values vs the champion on renamed official maps "
            f"{official['score'] * official['games']:g}/{official['games']} (a report, never a gate; 0.50 = level; not "
            f"held out: near copies of the official and community maps were {sum(near.values())}/{len(styles)} of "
            "the tune's maps)" + ("; per map: " + ", ".join(parts) if parts else ""))


QUANTITY = {"area": "the map's area (width x height, known at round 0; the test split the maps at the median area played)",
            "style": "the observed map features that tell these styles apart (measure first on the tune's maps: area, "
                     "wall share, portal count, pearl-field share, path distance between the starts)"}


# The params named by a waiting (todo or being built) idea queued by a tune's split check (source tunesplit:...): a
# second tune flagging one of them adds no idea (check_tune_split), since the first formula already replaces it.
def queued_params(ideas: list[dict]) -> set[str]:
    waiting = [i for i in ideas if str(i.get("source", "")).startswith("tunesplit:")
               and (i.get("status") == "todo" or str(i.get("status", "")).startswith("trying:"))]
    return {m.group(1) for i in waiting for m in re.finditer(r"\bP\.(\w+)", i.get("title") or "")}


# Every fitted group's line under one split, for the formula idea, which a coder builds from these numbers alone (the
# tune's samples stay in the loop home, which a coder's sandbox can't read): "100 tiles: gradient +0.050±0.030,
# curvature -0.310±0.090, best 440; ...".
def fitted_text(t: dict) -> str:
    def best(row: dict) -> str:
        if "best" in row:
            return f"best {row['best']:g}"
        return "best past its clamp" if row.get("past_clamp") else "no clear best (curvature not clearly below zero)"
    return "; ".join(f"{name}{' (' + format(row['pairs'], 'g') + ' map pairs)' if 'pairs' in row else ''}: gradient "
                     f"{row['gradient']:+.3f}±{row['se']:.3f}, curvature {row['curvature']:+.3f}±"
                     f"{row['curve_se']:.3f}, {best(row)}" for name, row in t["bands"].items())


# The formula ideas for `report`'s flagged values of experiment `exp_id` (tune idea `idea`), one per value not in
# `skip` (queued_params), citing every flagged split's numbers; each waits for the tune's own SPRT ("after" its idea)
# and is worded around the champion's P.<value> as it is when claimed, so it stays like for like whether or not the
# tuned value was kept. Each passes meta.valid_proposals (an Options line of 3+ numbered remedies, no bare threshold).
def proposals(report: dict, exp_id: str, idea: dict, skip: set[str] = frozenset()) -> list[dict]:
    by_param: dict[str, list[str]] = {}
    for param, split in report.get("flagged") or []:
        if param not in skip:
            by_param.setdefault(param, []).append(split)
    ideas = []
    for param, splits in by_param.items():
        quantity = " and ".join(QUANTITY[s] for s in splits)
        measured = [split_line(s, report["params"][param][s]) + f" against a bar of p {report['bar']:.4f} over the "
                    f"tune's {report['tests']} tests" for s in splits]
        fitted = [f"by {SPLIT_WORDS[s]}: {fitted_text(t)}" for s, t in report["params"][param].items() if t["bands"]]
        tuned, start, nudge = report["values"][param], report["starts"][param], report["nudges"][param]
        tune_id = idea.get("id")
        ideas.append({
            "category": "formula", "priority": 1, "kind": "formula", "after": [tune_id] if tune_id else [],
            "title": f"Formula for P.{param}: the gradient differs by {' and '.join(SPLIT_WORDS[s] for s in splits)}, "
                     "so a function of what the bot observes replaces the constant",
            "hypothesis": (
                f"{exp_id} tuned P.{param} (idea {tune_id}: {str(idea.get('title', ''))[:120]}) from {start:g} to "
                f"{tuned:g} (a nudge of {nudge:g}), and its SPSA samples (loop/tunesplit.py{order_text(report)}), "
                "say its groups' gradients at the tuned value point opposite ways, so their best values lie on "
                "opposite sides of it: " + "; ".join(measured) + " (gradient: score per nudge ± se at the tuned "
                "value, map pairs; positive: raising it scores more there; best where that group's curvature places "
                "one). Every fitted line (gradient and curvature in score per nudge; a group's best is "
                f"{tuned:g} + {nudge:g} x -gradient / curvature): " + " | ".join(fitted) + ". So one constant fits the mix of maps it was tuned on, not maps of another area or style, and the "
                f"tournaments use unseen maps. Change: replace P.{param} like for like with a formula of {quantity}. "
                f"Like for like is around the champion's P.{param} when this idea is claimed: {tuned:g} if {exp_id} "
                f"(idea {tune_id}) was kept, else still {start:g}; this idea waits for {tune_id}'s test, and it "
                f"doesn't need {tune_id}'s tuned value, so build around whichever the champion has. The formula gives "
                "that value on the tune's mix of maps and moves toward each group's measured best only where the "
                "gradients differ, behind its own switch. Build its coefficients from the fitted lines above (they are "
                "all of the tune's evidence you get: its samples stay in the loop home, outside your clone), check it "
                "against the worldgen line where the style split fitted one (the independent generator's maps, which "
                "our generator's constant wasn't fitted to), and read its changed moves on our generator's maps, "
                "worldgen maps and renamed official maps apart (loop/GENERALISE.md). Options: (1) a formula of "
                f"{quantity} built from the fitted lines above (this idea); (2) a formula of a quantity the area or "
                "style stands in for (the path distance between the starts, pearls per open tile, rounds to the "
                "nearest pearl field), after measuring on fresh maps of each group which one best orders the groups' best values; "
                "(3) one tuned value per group chosen by the group, a lookup whose group edges are constants of their "
                f"own; (4) the behaviour P.{param} scales computed from the game state each turn, so no per-map value "
                "is needed. This idea is option (1) because it is measured here and keeps the change one attributable "
                "formula; the others are siblings if it fails. Stop only if the formula's value, as a logging copy "
                "reads it on fresh maps of our generator's and fresh worldgen maps, stays within one nudge "
                f"({nudge:g}, the step the tune measured with) of the champion's constant on every map: it would then "
                "never depart from the constant by a step the tune could see."),
        })
    return ideas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiments", nargs="*")
    parser.add_argument("--all", action="store_true", help="every tune in the state's runs/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    runs = meta.STATE / "runs"
    names = sorted(p.parent.name for p in runs.glob("E*/tune.json")) if args.all else args.experiments
    for name in names:
        report = analyse(runs / name)
        if args.json:
            print(json.dumps({"id": name, **report}))
            continue
        print(f"{name}:{text(report)}")
        for param, splits in (report.get("params") or {}).items():
            for split, t in splits.items():
                p = (f"p {t['p']:.3g}" if t["p"] is not None
                     else f"untested{' (' + t['why'] + ')' if t.get('why') else ''}")
                print(f"  {param} by {SPLIT_WORDS[split]}: {p}; " + ", ".join(
                    f"{band_text(b, r)} curve {r['curvature']:+.3f} (z {r['curve_z']:+.1f})"
                    for b, r in t["bands"].items()))


if __name__ == "__main__":
    main()
