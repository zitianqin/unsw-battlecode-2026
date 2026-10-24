"""Plays opponent-screen games as one referee run (driver.play_batch): every group (one bot against one screen entry on
some maps, both sides) in one stream of games, so the game machines stay busy from the first group to the last instead
of idling through each small run's start-up and its slowest game (E0127, 2026-09-27: 26 runs of 100 games, each lasting
about as long as its slowest game, at under half the SPRT's games a minute).

Run by driver.referee inside the referee checkout (native.py play puts referee/ on the path and applies the load gates
and a filler's share), with a plan the driver writes:

    {"workers": "auto", "groups": [{"a": bot, "b": opponent, "maps": [...], "out": path}, ...], "replays": dir}

Each group's rows go to its `out` in map then side order, as referee/run.py writes them, once all its games are in.
With `replays` (a targeted idea's target batch, driver.target_batch) every game's replay is kept in that folder and
its row names it (`replay`), for the driver to measure and delete.
A group with `when` (a two-stage screen's second stage, driver.check_pool) plays only once one of its pools is behind
(`behind`): each pool is a list of parts {"candidate": side, "base": side}, a side {"rows": [rows known before the run],
"groups": [indices of groups whose rows join them]} (`decide`). Its games are queued as soon as every group a pool names
is complete, so the machines play one entry's second stage while the rest of the first finishes (the two runs' barrier
left about a minute of the hosts idle in each screen, 2026-09-28), and it is skipped once all its pools are complete and
none is behind; the skipped groups' indices go to <plan stem>-skipped.json. The driver applies its own rule to the rows
and plays any second stage this missed, so the rule here only decides what plays early (and a referee checkout not yet
refreshed to a run.py that takes "none ready yet" skips them all: the two runs as before). Exits FAILED_GAMES when a
game failed (every group is still written, and the driver names the group), else 0; a traceback exits 1, which the
driver reports as the run failing. The run's native judge report (run.native_report: its native games, and any
switch-off since it started) goes to <plan stem>-native.json, which the driver reads into the screen's result; a check
that differed while its native games were at risk (run.native_fault) stops the run when a group next finishes (it is
read then), exiting FAILED_GAMES, and the driver errors the screen (driver.NativeFault) so no gate stands on those
games.
"""

from __future__ import annotations

import json
import math
import sys
from collections import deque
from pathlib import Path

FAILED_GAMES = 3  # not 1, which a traceback exits with


# The plan's jobs, a list per group: bot a against bot b on each map, as team A then B. `run` is the referee's module.
def plan_jobs(groups: list[dict], run) -> list[list]:
    return [[run.Job(Path(m), Path(group["a"]), Path(group["b"]), side) for m in group["maps"] for side in "AB"]
            for group in groups]


# The mean per-game difference between two bots' scores on the same maps and sides, and its z value (99 with the sign
# when every difference is the same). The referee seeds each map's pearls from its contents, so games are deterministic
# and each map and side is one paired sample.
def paired_difference(cand: list[float], base: list[float]) -> tuple[float, float]:
    diffs = [c - b for c, b in zip(cand, base, strict=True)]
    mean = sum(diffs) / len(diffs)
    variance = sum((d - mean) ** 2 for d in diffs) / max(1, len(diffs) - 1)
    if variance == 0:
        return mean, 0.0 if mean == 0 else math.copysign(99.0, mean)
    return mean, mean / math.sqrt(variance / len(diffs))


# True when the candidate scored below the champion in these paired games ({"candidate": rows, "base": rows}, paired
# by position; the paired z below 0): the two-stage screen then plays the rest of its maps against that opponent.
def behind(games: dict[str, list[dict]]) -> bool:
    scores = {side: [{"a": 1.0, "b": 0.0, "draw": 0.5}[g["winner"]] for g in rows] for side, rows in games.items()}
    return paired_difference(scores["candidate"], scores["base"])[1] < 0


# A pool's paired games ({"candidate": rows, "base": rows}), None while a group it names still plays, or {} when it
# can't be judged (a failed game, or a part whose sides don't pair: the driver raises on those anyway). A pool is a list
# of parts, one an opponent (the ram family's has one a member), each paired within itself by map and side.
def pool_games(pool: list[dict], rows) -> dict[str, list[dict]] | None:
    games: dict[str, list[dict]] = {"candidate": [], "base": []}
    for part in pool:
        played = {side: [rows(i) for i in part[side]["groups"]] for side in games}
        if any(found is None for side in played.values() for found in side):
            return None
        sides = {}
        for side in games:
            known = [*part[side]["rows"], *(g for found in played[side] for g in found)]
            sides[side] = {(g["map"], g["a_side"]): g for g in known}
        every = [g for side in sides.values() for g in side.values()]
        if set(sides["candidate"]) != set(sides["base"]) \
                or any(g.get("error") or g.get("winner") not in ("a", "b", "draw") for g in every):
            return {}
        for side in games:
            games[side] += [sides[side][key] for key in sorted(sides["candidate"])]
    return games


# Whether a `when` group plays: True once one of its pools is complete and behind, False once every pool is complete
# and none is, else None. `rows(i)` is group i's rows, or None while it isn't complete.
def decide(when: list[list[dict]], rows) -> bool | None:
    found = [pool_games(pool, rows) for pool in when]
    if any(games and games["candidate"] and behind(games) for games in found):
        return True
    return None if any(games is None for games in found) else False


def play(plan_path: Path) -> int:
    import time

    import run
    from sprt import replay_name

    started = time.time()
    plan = json.loads(plan_path.read_text())
    groups = plan["groups"]
    planned = plan_jobs(groups, run)
    bots = list(dict.fromkeys(bot for jobs in planned for job in jobs for bot in (job.a, job.b)))
    results: list[list[str]] = [[] for _ in groups]
    parsed: list[list[dict]] = [[] for _ in groups]  # the same rows read back, for `decide`
    order: deque[int] = deque()  # each job's group, in the order handed to the scheduler (its results' order)
    skipped: list[int] = []

    def rows(i: int) -> list[dict] | None:
        return parsed[i] if len(results[i]) == len(planned[i]) else None

    # Every unconditional group's games, then each `when` group's as it is decided; None while one waits undecided
    # (run.Scheduler.play asks again after the next result, and raises on a None with no game running).
    def jobs():
        for i, group in enumerate(groups):
            if not group.get("when"):
                order.extend([i] * len(planned[i]))
                yield from planned[i]
        waiting = [i for i, group in enumerate(groups) if group.get("when")]
        if not hasattr(run, "NOT_READY"):  # a referee from before "none ready yet": the driver plays them after
            skipped.extend(waiting)
            return
        while waiting:
            ready = []
            for i in list(waiting):
                verdict = decide(groups[i]["when"], rows)
                if verdict is not None:
                    waiting.remove(i)
                    (ready if verdict else skipped).append(i)
            if not ready and waiting:  # with none left waiting the stream ends: no game may be running to wait on
                yield None
            for i in ready:
                order.extend([i] * len(planned[i]))
                yield from planned[i]

    failed = False
    replays = Path(plan["replays"]) if plan.get("replays") else None
    games = run.stream_games(jobs(), bots, run.parse_workers(str(plan["workers"])), False,
                             **({"replays": replays} if replays else {}))
    faulted = None
    for index, result in enumerate(games):
        i = order.popleft()
        row = run.to_json(result)
        if replays:  # the name run.Scheduler.play gave it (sprt.replay_name)
            row = json.dumps({**json.loads(row), "replay": str(replays / replay_name(index, result))})
        results[i].append(row)
        parsed[i].append(json.loads(row))
        failed |= bool(result.error)
        if len(results[i]) == len(planned[i]):
            Path(groups[i]["out"]).write_text("".join(r + "\n" for r in results[i]))
            if faulted := run.native_fault(run.native_report(started)):  # its games may be wrong: stop them now
                games.close()
                break
    if native := run.native_report(started):
        plan_path.with_name(f"{plan_path.stem}-native.json").write_text(json.dumps(native) + "\n")
    if any(group.get("when") for group in groups):
        plan_path.with_name(f"{plan_path.stem}-skipped.json").write_text(json.dumps(sorted(skipped)) + "\n")
    return FAILED_GAMES if failed or faulted else 0


if __name__ == "__main__":
    sys.exit(play(Path(sys.argv[1])))
