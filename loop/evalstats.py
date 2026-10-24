"""Behaviour stats per evaluation, candidate against champion (sig, 2026-09-30: "create tools to compare stats of your
new bot vs old bot so you know what breaks/what improved - for the evals"): after each SPRT, how the candidate's games
differ from the champion's on the same maps, sides and opponents, in deaths by cause, portal crossings, early pearls,
the longest dragon at rounds 250, 396 and 499, king feeds, splits and fold conversions, pooled and by map style, each
row with its n and the chance luck alone gives its difference.

Where the games come from: referee/sprt.py deletes each game's replay once it is scored (all but the 40 worst losses),
so nothing is left to read after the test. With `--stats-queue DIR` it hard-links the replays of an opponent trial's
games into DIR (a trial's four: the candidate as A then B, then the champion as A then B, against the same opponent on
the same map) with a T#####.json naming them, taking a trial only while fewer than sprt.STATS_BACKLOG trials wait
there and one of its sides has a champion game played in this test (a reused baseline has no replay): the sample
adapts to what the workers keep up with and the cache, never to this trial's result. driver.behaviour_stats starts
WORKERS `worker`s on DIR beside the SPRT (at home.GAME_NICE), each claiming the oldest waiting trial (a rename, so
sprt.py's backlog counts only unclaimed ones) and reading its replays (tools/watch.py's walk, tools/replays.py's facts)
into one row of sprt-stats.jsonl in the run folder, then deleting them; at the SPRT's end they get DRAIN_S to finish
what waits, then stop, and `table` pools the rows. The queue, the rows and the replays are the referee's and the driver's own files in the run folder, out of every
worker's reach (CLAUDE.md: the trusted side never trusts a worker's folder). Head-to-head trials (the candidate
against the champion) are left out: one side's deaths there are the other's kills.

Pairing and chance: a candidate game and the champion's game on the same trial and side are a pair, and a trial's
pairs (its A and B sides share map, opponent and pearl seed, so aren't independent) are one unit: each row counts the
trials with a pair holding the value (a first-crossing round needs a crossing in both), gives each side's mean a game
and the two-sided sign test's chance of that many trials' summed differences going one way by luck alone
(chance.sign_p, ties dropped). A predicted game (the candidate answered every recorded turn as the champion did) is
the champion's game, a tie. `moves` are the rows whose
p times the rows looked at is at most chance.TRIGGER_P (Bonferroni: about 150 rows put 15 past p 0.1 by luck alone),
smallest p first; the RESULT line prints the first MOVES_SHOWN.

Bounds: the queue holds at most sprt.STATS_BACKLOG trials' replays (a median 3.4 MB a game); sprt-stats.jsonl a row
of about 2 KB a trial read; the result row's `behaviour` key at most len(METRICS) x (1 + styles with MIN_TRIALS trials)
rows of about 80 bytes (under 15 KB; results.jsonl rows were about 800 KB each on 2026-09-30). Cost: measured
2026-09-30 on E0378's 40 kept replays (6 MB each, mostly portalrooms), 2.9 s of CPU a game median, 6.1 s at most, 261
MB peak memory, and 2.2x that in wall time at nice 19 under a 1-minute load of 30: one worker reads at most about
1,200 games an hour, fewer on a busy Mac, while E0378's SPRT played about 5.7 games a second (1,482 trials in 16
minutes). With one worker the first 8 SPRTs (E0382-E0400, 30 Sep) read 15-87 trials each (median 30) at 5.6-11.2 s
of CPU a trial (their rows' cpu_s) and skipped 204-1,960 while it was behind, against 0-902 unpairable on reused
baselines: the worker's pace, not the cache, held n down (E0382: 31 read, 566 skipped). WORKERS of them read about
that many times as many, at most WORKERS cores and 261 MB each at game priority, below dev work and building.

    .venv/bin/python loop/evalstats.py worker QUEUE OUT TOKEN   # driver.behaviour_stats starts this
    .venv/bin/python loop/evalstats.py table RUN_FOLDER    # the table of a run's sprt-stats.jsonl, as markdown
    .venv/bin/python loop/evalstats.py game REPLAY SIDE    # one game's stats, as JSON
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import chance  # noqa: E402

QUEUE = "stats-queue"  # in the run folder: sprt.py's links and trial files, the worker's input
ROWS = "sprt-stats.jsonl"  # in the run folder: one row a trial read
DONE = "DONE"  # written into the queue when the SPRT has ended: the worker drains what waits and exits
# The worker's time to finish the queued trials once the SPRT ends, then it is stopped: the evaluation lock waits on
# it when no screen follows (a discard), so it is short; at most sprt.STATS_BACKLOG trials are lost to it.
DRAIN_S = 15
OWNER = "OWNER"  # in the queue: the launch's token; a worker whose token it no longer holds exits (a relaunch)
POLL_S = 1.0
MIN_TRIALS = 5  # a map style's rows are kept when it has this many trials with a pair
# Workers reading a test's queue at once: E0382 read 31 trials with one, 566 skipped; 4 aim n at hundreds for about 4
# game slots' worth of this Mac's CPU at game priority, where the SPRT's own games here get half of its cores.
WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))
MOVES_SHOWN = 3
EARLY = 100  # pearls eaten before this round
LONGEST_AT = (250, 396, 499)
KING_FEEDS = (300, 396)  # the rounds the king's meals from teammates' drops are counted in
FOLD = 397  # from this round a teammate's drop the king eats is a fold conversion (the champion's foldRound)
METRICS = {
    "deaths": "deaths",
    "death_splitout": "deaths: split-out parent",
    "death_wall": "deaths: kelp wall",
    "death_body_own": "deaths: own or teammate body",
    "death_body_enemy": "deaths: enemy body",
    "death_headon": "deaths: head-on",
    "death_trapped": "deaths: no valid move (noValidAction, not a timeout or unpaid sprint)",
    "death_other": "deaths: other (timeout, unpaid sprint, body not placed)",
    "death_pocket": "deaths in a portal pocket, any cause",
    "death_after_crossing": "deaths within 10 rounds of the dragon's portal crossing",
    "crossings": "portal crossings",
    "first_crossing": "first portal crossing's round",
    "pearls_early": "pearls eaten r0-99",
    **{f"longest_{r}": f"longest dragon at r{r}" for r in LONGEST_AT},
    "king_feeds": "king's meals from teammates' drops r300-396",
    "splits": "splits",
    "first_split": "first split's round",
    "fold_conversions": "teammates merged into the king from r397",
}
COLUMNS = ("metric", "group", "n", "candidate", "champion", "higher", "lower", "p")


# `side`'s stats in the replay at `path` (a local game, no debug lines needed: every count is read from the moves), as
# {METRICS key: number, or None where the game has no value: no crossing or split}. A dragon trapped in a room dies
# by its last move's reason (kelp, a body); only a portal pocket's deaths are told apart (death_pocket): a full
# trapped reading (tools/watch.py doom) costs about half again the walk.
def game_stats(path: Path, side: str) -> dict[str, float | None]:
    import replays
    import watch
    w = watch.watch(path)
    game = w.game
    pockets = watch.pockets(w.board, watch.step_table(w.board))
    crossed: dict[int, list[int]] = {}
    for r, i in game.teams[side].crossings:
        crossed.setdefault(i, []).append(r)
    out = dict.fromkeys(("deaths", "death_splitout", "death_wall", "death_body_own", "death_body_enemy",
                         "death_headon", "death_trapped", "death_other", "death_pocket", "death_after_crossing"), 0)
    for index, d in enumerate(game.deaths):
        if d["team"] != side:
            continue
        out["deaths"] += 1
        out[death_cause(w, index, d, side)] += 1
        out["death_pocket"] += tuple(d["head"]) in pockets
        out["death_after_crossing"] += any(0 <= d["round"] - r <= replays.CROSSING_WINDOW
                                           for r in crossed.get(d["id"], ()))
    crossings = game.teams[side].crossings
    splits = game.teams[side].splits
    # The longest at the end of the round, or as the game ended before it (0 for a team gone), so an early end
    # stays in the row rather than dropping the pair.
    at = {r: next((s.longest[side] for s in reversed(game.snapshots) if s.round <= r), None) for r in LONGEST_AT}
    meals = replays.king_meals(game, side)
    teammate = [(m, drop) for m, drop in meals if drop is not None and drop["team"] == side]
    out |= {"crossings": len(crossings), "first_crossing": min(r for r, _ in crossings) if crossings else None,
            "pearls_early": sum(m["round"] < EARLY and m["team"] == side for m in game.meals),
            **{f"longest_{r}": at.get(r) for r in LONGEST_AT},
            "king_feeds": sum(KING_FEEDS[0] <= m["round"] <= KING_FEEDS[1] for m, _ in teammate),
            "splits": len(splits), "first_split": splits[0]["round"] if splits else None,
            "fold_conversions": len({m["death"] for m, _ in teammate if m["round"] >= FOLD})}
    return out


# The METRICS key of `side`'s death `d` (index `index` in w.game.deaths): a split-out's parent (read from the moves,
# tools/watch.py split_out_parent) first, else by the engine's reason, a body by whose it was (rulecounts.body_owner).
def death_cause(w, index: int, d: dict, side: str) -> str:
    import rulecounts
    import watch
    reason = d["reason"]
    if watch.split_out_parent(d, w.turns):
        return "death_splitout"
    if reason == "hitWall":
        return "death_wall"
    if reason == "hitSelf":
        return "death_body_own"
    if reason == "hitOtherBody":
        owner = rulecounts.body_owner(w, index, d)
        return "death_other" if owner is None else "death_body_own" if owner == side else "death_body_enemy"
    if reason == "hitHeadToHead":
        return "death_headon"
    if reason == "noValidAction" and d.get("cause") not in ("timeout", "unpaidSprintStep"):
        return "death_trapped"
    return "death_other"


# One queued trial (a T#####.json sprt.py wrote, queue_stats) as its sprt-stats.jsonl row: the trial's opponent, map
# and each game's stats, a predicted candidate game taking its champion game's; a replay that can't be read gives its
# game an "error" instead. Deletes the trial's replays and file.
def read_trial(queue: Path, spec: Path) -> dict:
    started = time.process_time()
    trial = json.loads(spec.read_text())
    games = []
    for g in trial["games"]:
        row = {k: g.get(k) for k in ("i", "a_side", "role", "predicted")}
        if g.get("replay"):
            path = queue / g["replay"]
            try:
                row["stats"] = game_stats(path, g["a_side"])
            except Exception as error:  # a replay that doesn't parse is one game less, never a stopped worker
                row["error"] = f"{type(error).__name__}: {error}"[:200]
            path.unlink(missing_ok=True)
        games.append(row)
    by_index = {g["i"]: g for g in games}
    for g in games:  # a predicted game is its champion game, move for move
        if g.get("predicted") and "stats" not in g and "stats" in by_index.get(g["i"] + 2, {}):
            g["stats"] = by_index[g["i"] + 2]["stats"]
    spec.unlink(missing_ok=True)
    return {"trial": trial["trial"], "opponent": trial["opponent"], "map": trial["map"], "games": games,
            "cpu_s": round(time.process_time() - started, 2)}


# Reads each trial sprt.py queues in `queue` into a row of `out`, oldest first, until the queue holds DONE and no
# trial; exits too when the queue's OWNER no longer holds `token` (a relaunch made the queue afresh) or its parent,
# the driver, is gone (its stop never ran). At nice 19 from its launch (start), so dev work and building win. Beside
# other workers on the same queue: a trial is its own once its rename to C#####-<pid>.json succeeds, and each row goes
# to `out` in one O_APPEND write (about 2 KB), so rows don't interleave.
def worker(queue: Path, out: Path, token: str) -> None:
    parent = os.getppid()

    def mine() -> bool:
        try:
            return (queue / OWNER).read_text() == token and os.getppid() == parent
        except OSError:
            return False

    while mine():
        specs = sorted(queue.glob("T*.json"))
        if not specs:
            if (queue / DONE).exists():
                return
            time.sleep(POLL_S)
            continue
        claimed = queue / f"C{specs[0].name[1:-5]}-{os.getpid()}.json"
        try:
            specs[0].rename(claimed)
        except FileNotFoundError:  # another worker took it
            continue
        row = read_trial(queue, claimed)
        fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode())
        finally:
            os.close(fd)


# Starts `workers` `worker`s on run folder `run`'s queue and rows (both made afresh) at home.GAME_NICE, as the loop's
# games here run; returns the processes and sprt.py's arguments for them.
def start(run: Path, python: str = sys.executable, workers: int = WORKERS) -> tuple[list[subprocess.Popen], list[str]]:
    import home
    queue = run / QUEUE
    shutil.rmtree(queue, ignore_errors=True)
    queue.mkdir(parents=True)
    (run / ROWS).unlink(missing_ok=True)
    token = os.urandom(8).hex()
    (queue / OWNER).write_text(token)
    processes = []
    with (run / "stats-worker.log").open("a") as log:
        try:
            for _ in range(workers):
                processes.append(subprocess.Popen(
                    ["/usr/bin/nice", "-n", str(home.nice_increment(home.GAME_NICE)), python,
                     str(Path(__file__).resolve()), "worker", str(queue), str(run / ROWS), token],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True))
        except OSError:  # the caller runs no stop for a start that raised: none left polling
            stop(processes, run, 0)
            raise
    return processes, ["--stats-queue", str(queue)]


# Tells `start`'s worker the SPRT has ended (DONE into the queue): it reads what waits, then exits.
def done(run: Path) -> None:
    try:
        (run / QUEUE / DONE).write_text("")
    except OSError:
        pass


# Ends `start`'s workers: what is left of `drain_s` since `done` (called here if it wasn't) for them to read what
# waits, then SIGKILL, and the queue (links to replays the SPRT already deleted, and any not read) goes. Returns the
# seconds from `done` to the last worker's end.
def stop(processes: list[subprocess.Popen], run: Path, drain_s: float = DRAIN_S) -> float:
    marker = run / QUEUE / DONE
    if not marker.exists():
        done(run)
    try:
        since = marker.stat().st_mtime
    except OSError:
        since = time.time()
    for process in processes:
        try:
            process.wait(timeout=max(0.0, drain_s - (time.time() - since)))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):  # it ended meanwhile
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    shutil.rmtree(run / QUEUE, ignore_errors=True)
    return round(time.time() - since, 1)


# The rows in run folder `run`'s sprt-stats.jsonl (a torn last line, the worker killed mid-write, is left out).
def read_rows(run: Path) -> list[dict]:
    rows = []
    try:
        lines = (run / ROWS).read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


# Each trial in `rows` with a pair of games, as (its map style, [(candidate stats, champion stats)]): the candidate's
# game i against the champion's i + 2, the same side against the same opponent on the same map.
def pairs(rows: list[dict], style_of: Callable[[str], str]) -> list[tuple[str, list[tuple[dict, dict]]]]:
    found = []
    for row in rows:
        games = {g["i"]: g for g in row.get("games") or []}
        sides = [(games[i]["stats"], games[i + 2]["stats"]) for i in (0, 1)
                 if "stats" in games.get(i, {}) and "stats" in games.get(i + 2, {})]
        if sides:
            found.append((style_of(row["map"]), sides))
    return found


# One row of the table over `found` trials (pairs) for `metric` and group name `group`: n trials, each side's mean a
# game, the trials whose summed candidate-minus-champion difference is above and below 0, and the sign test's p; None
# with no pair holding the value.
def table_row(metric: str, group: str, found: list[tuple[str, list[tuple[dict, dict]]]]) -> list | None:
    units = [[(c[metric], b[metric]) for c, b in sides if c.get(metric) is not None and b.get(metric) is not None]
             for _, sides in found]
    units = [u for u in units if u]
    if not units:
        return None
    diffs = [sum(c - b for c, b in u) for u in units]
    higher, lower = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    p = min(1.0, 2 * chance.sign_p(max(higher, lower), min(higher, lower)))
    games = [v for u in units for v in u]
    return [metric, group, len(units), round(sum(c for c, _ in games) / len(games), 3),
            round(sum(b for _, b in games) / len(games), 3), higher, lower, round(p, 5)]


# The behaviour table of run folder `run` for the result row: {"trials", "pairs", "cpu_s", "errors", "columns",
# "rows" (COLUMNS: every metric pooled as group "all", then by map style for the styles with MIN_TRIALS), "moves"
# (the rows past the corrected bar, smallest p first, as `moves`' text)}; `extra` (the SPRT's queue counts, the drain)
# goes in as it is. {} when no trial was read. `style_of` names a map file's style (driver.map_style).
def table(run: Path, style_of: Callable[[str], str], extra: dict | None = None) -> dict:
    rows = read_rows(run)
    if not rows:
        return {}
    found = pairs(rows, style_of)
    styles = sorted({s for s, _ in found})
    out = []
    for group in ("all", *styles):
        members = found if group == "all" else [f for f in found if f[0] == group]
        if group != "all" and (len(members) < MIN_TRIALS or len(members) == len(found)):  # one style: "all" has it
            continue
        out += [row for metric in METRICS if (row := table_row(metric, group, members))]
    looks = len(out)
    moves = sorted((r for r in out if r[7] * looks <= chance.TRIGGER_P), key=lambda r: (r[7], -r[2]))
    return {"trials": len(rows), "pairs": sum(len(sides) for _, sides in found),
            "cpu_s": round(sum(r.get("cpu_s", 0) for r in rows), 1),
            "errors": sum("error" in g for r in rows for g in r.get("games") or []), **(extra or {}),
            "columns": list(COLUMNS), "rows": out, "moves": [move_text(r, looks) for r in moves]}


# A row as the moves' text: "portal crossings (all) 41.2 v 33.9 a game, n 212 trials, 150 of 190 changed higher,
# p 3e-05 (x 152 rows)".
def move_text(row: list, looks: int) -> str:
    metric, group, n, cand, champ, higher, lower, p = row
    way = "higher" if higher >= lower else "lower"
    return (f"{METRICS[metric]} ({group}) {cand:g} v {champ:g} a game, n {n} trials, {max(higher, lower)} of "
            f"{higher + lower} changed {way}, p {p:.2g} (x {looks} rows)")


# The first MOVES_SHOWN of `behaviour`'s moves on one line for the RESULT alert, "" when it has none or no table.
def moves_line(behaviour: dict) -> str:
    if not behaviour.get("rows"):
        return ""
    moves = behaviour.get("moves") or []
    if not moves:
        return f"; behaviour: no move past chance over {behaviour.get('pairs')} game pairs"
    return f"; behaviour: {'; '.join(moves[:MOVES_SHOWN])}"


# `behaviour` (table's) as markdown for the judge (behaviour.md) and the post-mortem (.postmortem/behaviour.md).
def markdown(behaviour: dict) -> str:
    if not behaviour.get("rows"):
        why = behaviour.get("error") or "no trial's replays were read"
        return f"# Behaviour stats: none\n\n{why}\n"
    lines = ["# Behaviour stats: candidate v champion, per game", "",
             f"From {behaviour['trials']} opponent trials whose replays were read during the SPRT ("
             f"{behaviour['pairs']} game pairs: the candidate's game and the champion's on the same map, side and "
             "opponent; a sample of the trials, loop/evalstats.py). `n`: trials; `higher`/`lower`: trials where the "
             "candidate's value, summed over the trial's sides, was above/below the champion's; `p`: the two-sided "
             "sign test's chance of that split by luck "
             f"alone. With {len(behaviour['rows'])} rows, luck alone puts about {len(behaviour['rows']) // 10} past p "
             f"0.1: the moves below are the rows past {chance.TRIGGER_P} after multiplying by the rows.", ""]
    lines += ["Moves: " + ("; ".join(behaviour["moves"]) if behaviour["moves"] else "none"), "",
              "| metric | group | n | candidate | champion | higher | lower | p |", "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {METRICS[m]} | {g} | {n} | {c:g} | {b:g} | {h} | {lo} | {p:.2g} |"
              for m, g, n, c, b, h, lo, p in behaviour["rows"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "worker" and len(sys.argv) == 5:
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    elif command == "table" and len(sys.argv) == 3:
        import driver
        print(markdown(table(Path(sys.argv[2]), lambda m: driver.map_style(Path(m)))), end="")
    elif command == "game" and len(sys.argv) == 4:
        print(json.dumps(game_stats(Path(sys.argv[2]), sys.argv[3]), indent=1))
    else:
        sys.exit(__doc__.split("\n\n")[-1])


if __name__ == "__main__":
    main()
