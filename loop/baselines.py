"""Idle prewarm (loop/LOOP.md "Shared map sets and reused baselines"): plays the current champion's baseline games
into its shared map set before any test needs them, so the next tests play only the candidate's games. The results are the
same games a test would play: the set is the one driver.evaluation_maps makes for the champion, toolkit and shared map
mix (driver.shared_mix: eval, worldgen and portal-bet maps; same folder, seed and maps), the rotation's games go into its baselines.jsonl (sprt.BaselineCache, in referee/prewarm.py's
order and recording rule), and the screen's champion games into its pool-<opponent>[-fixedkey-<digest>]-base.jsonl
(driver.screen_baselines).

In `stages` order, each STEP trials (80 games) or SCREEN_MAPS maps (50 games): the rotation's first 600 trials,
the champion against each screen opponent the rotation doesn't play (the first POOL_PAIRS maps, both sides), then the
rotation up to 1,200 trials. The rotation opponents' own screen maps aren't played: a screen needs them only when its
SPRT stops before 200 trials (check_pool counts the rotation's games first), which no kept result has done. Every stage
not done plays in one referee run (play), a stage's games starting as the one before's free their slots. Until
2026-09-28 each stage was a referee run of its own, which drained the hosts before the next started: 12 stages of about
72 games took 01:19-01:31 on 28 Sep, and the prewarm alone kept 17 of 57 slots busy on average (utilisation.jsonl, its
25 such minutes). Every game is kept as it comes in and a stage recorded done once its last is in, so a stopped run
resumes where it stopped.

It is a filler like the variant sweep: the upkeep tick (maintenance.tick) starts it in the background while no
evaluation plays on the remote hosts (sweep.busy, which lets a tune playing its games, a candidate waiting and the
lock's stages on this Mac through), and it stops between stages when one does, waiting in place until it ends
(sweep.wait_idle); a stage caught by one waits at no host share until it ends. A stop marker or a new champion
(halted) kills the run at once, its games in flight with it, so a new champion's prewarm can start; the next run plays what wasn't saved. Its games
are filler games (driver.referee with yielding="filler").
<loop home>/baselines.json holds the progress (champion, epoch, rotation, maps: the set's mix, done stages, pid,
progress_at, paused, error/failures),
which health.py checks; the run's output is <loop home>/baselines.log, its plan the set's prewarm-plan.json (rewritten
each run, never appended: the set's MAX_PAIRS map paths and the stages, 228 KB of paths on 28 Sep, PLAN_BOUND at most, which
health.prewarm checks).

    .venv/bin/python loop/baselines.py run --repo R --state S   # what tick launches
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
from pathlib import Path

import meta
import online

REPO = Path(__file__).resolve().parent.parent
STEP = 50  # rotation trials a stage (41-42 opponent trials, 82-84 champion games at 5); EARLY and LATE are multiples
EARLY, LATE = 600, 1200  # rotation trials played before the screen's stages, and after them
SCREEN_MAPS = 25  # screen maps a stage: 50 games
RETRY_S = 3600  # after a failed run
STALE = 3 * 3600  # a run recorded as running with no stage finished for this long is hung
PAUSED, FAILED = 3, 4  # play's exit codes: stopped for sweep.busy; a screen game failed (not 1, a traceback's)
PLAN_BOUND = 1_000_000  # bytes prewarm-plan.json may hold (health.prewarm)
# Recorded games (a rotation stage's while sprt.predicting is on) pulled ahead of their results before play lets its
# stream drain and starts the next. The referee queues a recorded game that finds no slot on this Mac's platform
# rather than holding it back, up to run.AHEAD x every slot (924 with today's hosts, 45-90 min of Apple-only games),
# so without this every stage's busy check and cache read ran at the start. 300 is 15-30 min of them; the cost is
# the drain's tail, about half the Apple slots for one game each 300 games.
RECORD_AHEAD = 300


def read(state: Path) -> dict:
    return online.read(state / "baselines.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "baselines.json", record)


# The stages, in order, for the rotation's opponents and the screen's (gauntlet.toml) opponents, each screen opponent
# the rotation doesn't play getting `pairs` maps: "rotation:<trials>" and "screen:<opponent>:<first map>".
def stages(rotation: list[str], screen: list[str], pairs: int) -> list[str]:
    return ([f"rotation:{n}" for n in range(STEP, EARLY + 1, STEP)]
            + [f"screen:{name}:{first}" for name in screen if name not in rotation
               for first in range(0, pairs, SCREEN_MAPS)]
            + [f"rotation:{n}" for n in range(EARLY + STEP, LATE + 1, STEP)])


# The upkeep tick's step, as sweep.tick: notes a run that died, then starts one in the background when the champion's
# set isn't done, the referee checkout makes its mix's styles (driver.missing_style: a style new on main arrives with
# the next evaluation's refresh; paused till then, not failed), the machines are idle (sweep.busy) and no failure is
# under RETRY_S old. Returns a line to log, or None.
def tick(repo: Path, state: Path, now: float | None = None) -> str | None:
    import driver
    import maintenance
    import sweep
    now = time.time() if now is None else now
    meta.reap()  # a run that finished clears its own pid, so only this reaps it
    if not (state / "ideas.jsonl").exists():
        return None
    with meta.locked(state / "baselines"):
        record = read(state)
        if pid := record.get("pid"):
            if meta.alive(pid):  # reaping it if this process launched it
                return None
            record = failure(record, "the run process exited without finishing; see baselines.log", now)
            save(state, record)
        champion = meta.champion_commit(repo)
        if not champion:
            return None
        toolchain = maintenance.toolchain(state)
        # A new champion, engine, SPRT rotation, shared map mix (driver.shared_mix, a set of its own) or test builds
        # (driver.TEST_KEYS: from 2026-09-29 fixed-key test copies of the champion and the opponents, MISSES 99)
        # starts over: stage names (rotation:N) don't say whose games they hold. A record from before mixes had none.
        if (record.get("champion"), record.get("epoch"), record.get("rotation", list(driver.ROTATION)),
                record.get("maps"), record.get("keys")) != (champion, toolchain.get("epoch"), list(driver.ROTATION),
                                                            driver.shared_mix(), driver.TEST_KEYS):
            record = {"champion": champion, "epoch": toolchain.get("epoch"), "toolchain": toolchain,
                      "rotation": list(driver.ROTATION), "maps": driver.shared_mix(), "keys": driver.TEST_KEYS}
            save(state, record)
        if record.get("finished_at"):
            return None
        if style := driver.missing_style({"maps": record.get("maps")}):
            reason = f"the referee checkout's mapgen can't make {style!r} maps until the next evaluation's refresh"
            if record.get("paused") != reason:
                record.update(paused=reason, paused_at=now)
                save(state, record)
            return None
        if reason := sweep.busy(repo):
            if sweep.repause(record, reason, now):
                save(state, record)
            return None
        record["idle_at"] = now
        if now - record.get("failed_at", 0) < RETRY_S:
            save(state, record)
            return None
        record.update(attempt_at=now, progress_at=now, paused=None)
        save(state, record)  # before anything that can fail, so a failure is retried after RETRY_S
        try:
            record["pid"] = launch(repo, state)
        except Exception as error:
            save(state, failure(record, f"could not start the run: {type(error).__name__}: {error}", now))
            return f"baseline prewarm: could not start the run: {error}"
        save(state, record)
        return f"baseline prewarm of {champion[:7]} launched in the background: {state / 'baselines.log'}"


def launch(repo: Path, state: Path) -> int:
    return meta.spawn([sys.executable, str(Path(__file__).resolve()), "run", "--repo", str(repo), "--state",
                       str(state)], state, state / "baselines.log")


def failure(record: dict, error: str, now: float) -> dict:
    return {**record, "pid": None, "error": error[-1200:], "failed_at": now, "failures": record.get("failures", 0) + 1}


# Rewrites the record for `champion` with `changes`, unless a newer champion's has replaced it.
def update(state: Path, champion: str, **changes) -> dict:
    with meta.locked(state / "baselines"):
        record = read(state)
        if record.get("champion") == champion:
            record.update(changes)
            save(state, record)
        return record


# Why a running prewarm must end now, its games in flight killed, or None: a stop marker, or the champion it plays for
# moved (its record is replaced, and a new champion's run can't start while this one holds the pid). An evaluation
# only pauses it between stages (sweep.busy), its games in flight keeping theirs. A failed read of the champion
# isn't a move: asked every driver.STOP_CHECK_S, a blip would kill the games in flight.
def halted(repo: Path, champion: str) -> str | None:
    import driver
    if driver.stop_requested():
        return "a stop marker is set"
    if (current := meta.champion_commit(repo)) and current != champion:
        return "the champion moved"
    return None


# The detached run tick launches: plays every stage not done yet in one referee run (play), until all are
# (finished_at) or sweep.busy says to stop (paused, before the run or between two of its stages; an evaluation on the
# hosts it waits out in place, sweep.wait_idle), or halted kills it (paused too: the next idle tick resumes it, or the
# new champion's starts over).
def run(repo: Path, state: Path) -> None:
    import driver
    import sweep
    record = read(state)
    champion = record["champion"]
    token = driver.RUNTIME_PYTHON.set(record.get("toolchain", {}).get("python"))
    try:
        folder = driver.shared_set(champion, record["epoch"], record.get("maps"))
        maps = driver.shared_set_maps(folder, driver.shared_seed(champion), record.get("maps"))
        base = driver.gauntlet_bot({"name": "champion", "commit": champion}, into=folder / "bots")  # = the checkout
        base = driver.prewarm_champion(base)  # the builds the tests' opponent trials play (driver.test_bots)
        rotation = driver.rotation_bots(list(driver.ROTATION), keyed=True)
        status = state / "remote-status-prewarm.json"  # never the evaluation's; health.py reads it while fresh
        screen = {spec["name"]: spec for spec in
                  tomllib.loads((driver.REFEREE / "referee/gauntlet.toml").read_text())["opponent"]}
        while todo := [stage for stage in stages([name for name, _ in rotation], list(screen), driver.POOL_PAIRS)
                       if stage not in read(state).get("done", [])]:
            if reason := sweep.wait_idle(lambda: sweep.busy(repo, champion),
                                         lambda **changes: update(state, champion, **changes)):
                update(state, champion, pid=None, paused=reason, paused_at=time.time())
                print(f"paused before {todo[0]}: {reason}", flush=True)
                return
            opponents = dict.fromkeys(stage.split(":")[1] for stage in todo if stage.startswith("screen:"))
            plan = {"state": str(state), "repo": str(repo), "commit": champion, "shared": str(folder),
                    "champion": str(base), "maps": maps, "rotation": [str(bot) for _, bot in rotation],
                    "screen": {name: str(driver.opponent_bot(screen[name], keyed=True)) for name in opponents},
                    "stages": todo}
            online.save(folder / "prewarm-plan.json", plan)  # whole, so health.plan_file never reads half a plan
            proc = driver.referee(str(Path(__file__).resolve()), ["play", str(folder / "prewarm-plan.json")],
                                  timeout=12 * 3600, yielding="filler", status=status,
                                  stop=lambda: halted(repo, champion) is not None)
            if proc is None:
                reason = halted(repo, champion) or "stopped"
                update(state, champion, pid=None, paused=reason, paused_at=time.time())
                print(f"stopped: {reason}", flush=True)
                return
            print(proc.stdout + proc.stderr[-2000:], end="", flush=True)
            if proc.returncode not in (0, PAUSED):
                raise RuntimeError(f"play exited {proc.returncode}: {(proc.stderr or proc.stdout)[-600:]}")
        update(state, champion, pid=None, finished_at=time.time())
        print(f"finished: {folder}", flush=True)
    except Exception as error:
        with meta.locked(state / "baselines"):
            current = read(state)
            if current.get("champion") == champion:
                save(state, failure(current, f"{type(error).__name__}: {error}", time.time()))
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    finally:
        driver.RUNTIME_PYTHON.reset(token)


# One stage's games not yet played, in order (`run` is the referee's module, `cache` the set's sprt.BaselineCache):
# "rotation:N" the STEP trials before N, trial i on map i against rotation[i % len(rotation)] (the champion's own
# trials skipped), both sides, each recording while `record` (referee/prewarm.py's rule); "screen:<name>:<first>" the
# champion against that opponent (`screen`) on SCREEN_MAPS maps from `first`, both sides of each map that `known`
# (the opponent's screen_baselines) lacks a side of.
def stage_jobs(stage: str, champion: Path, maps: list[str], opponents: list[Path], screen: dict[str, Path], cache,
               record: bool, known, run) -> list:
    import driver
    kind, arg, *first = stage.split(":")
    if kind == "rotation":
        rotation = [None, *opponents]
        jobs = [run.Job(Path(maps[i]), champion, rotation[i % len(rotation)], side)
                for i in range(int(arg) - STEP, min(int(arg), len(maps))) if i % len(rotation) for side in "AB"]
        jobs = [job for job in jobs if cache.get(job) is None]
        return [cache.recorded(job) for job in jobs] if record else jobs
    have, start = known(screen[arg]), int(first[0])
    return [run.Job(Path(m), champion, screen[arg], side)
            for m in maps[start:min(start + SCREEN_MAPS, driver.POOL_PAIRS)]
            if (m, "A") not in have or (m, "B") not in have for side in "AB"]


# `play PLAN`, run by driver.referee inside the referee checkout (native.py play puts referee/ on the path and applies
# the load gates and an idle filler's share): the plan's stages as one stream of games, so a stage's games start as
# the one before's free their slots. Before each stage sweep.busy is asked, and a reason ends the games there (the
# stage being played finishes), and the set's baselines.jsonl and sprt.predicting are read again, so games an SPRT
# cached meanwhile aren't played twice and an audit that turned prediction off stops the recording (off stays off).
# Once RECORD_AHEAD recorded games wait for their results, the stream is let drain and the next starts with the stages
# left, so those checks never run far ahead of the games. Each rotation game goes into baselines.jsonl and each screen
# game into the set's pool-<opponent>[-fixedkey-<digest>]-base.jsonl (driver.base_build) as it comes in, and a stage
# is recorded done in baselines.json once its last game is in (a screen stage only when none failed). Exits FAILED
# when a screen game failed, PAUSED when it stopped for sweep.busy.
def play(plan_path: Path) -> int:
    import driver
    import run as referee
    import sprt
    import sweep

    plan = json.loads(plan_path.read_text())
    state, repo, champion = Path(plan["state"]), Path(plan["repo"]), plan["commit"]
    folder, base, maps = Path(plan["shared"]), Path(plan["champion"]), plan["maps"]
    opponents = [Path(bot) for bot in plan["rotation"]]
    screen = {name: Path(bot) for name, bot in plan["screen"].items()}
    remaining = list(plan["stages"])  # not yet recorded done
    recording = [True]  # until sprt.predicting says off, which it then stays (its marker is never removed mid-run)
    stopped: list[str] = []
    failed: dict[str, str] = {}  # a screen stage's first failed game

    def done(stage: str) -> None:
        remaining.remove(stage)
        finished = read(state).get("done", [])
        update(state, champion, done=[*finished, stage], progress_at=time.time(), failures=0, error=None)
        print(f"{stage}: done", flush=True)

    while remaining and not stopped:
        owners: list[tuple[str, object, object]] = []  # (stage, job, cache) of each game pulled, in order
        left: dict[str, int] = {}  # each stage's games not yet in
        received, capped = [0], [False]

        def jobs():
            for stage in list(remaining):
                if reason := sweep.busy(repo, champion):
                    stopped.append(reason)
                    return
                cache = sprt.BaselineCache(folder / "baselines.jsonl")
                recording[0] = recording[0] and sprt.predicting(cache, base)
                todo = stage_jobs(stage, base, maps, opponents, screen, cache, recording[0],
                                  lambda opponent: driver.screen_baselines(folder, opponent, base), referee)
                if not todo:
                    done(stage)
                    continue
                left[stage] = len(todo)
                for job in todo:
                    if job.record is not None and len(owners) - received[0] >= RECORD_AHEAD:
                        capped[0] = True
                        return
                    owners.append((stage, job, cache))
                    yield job

        pulled = referee.stream_games(jobs(), [base, *opponents, *screen.values()], "auto", False)
        for result in pulled:
            stage, job, cache = owners[received[0]]  # results come in job order, and a job is pulled before its result
            received[0] += 1
            if stage.startswith("rotation:"):
                cache.put(job, result)  # a failed game isn't kept (BaselineCache.put): a test plays it
            elif result.error:
                failed.setdefault(stage, f"{result.map} ({result.a_side}): {result.error}")
            else:
                driver.add_screen_baselines(folder, job.b, base, [json.loads(referee.to_json(result))])
            left[stage] -= 1
            if not left[stage] and stage not in failed:
                done(stage)
        if not capped[0]:
            break
        remaining[:] = [stage for stage in remaining if stage not in failed]  # a failed screen stage waits a run
    if failed:
        print("screen games failed: " + "; ".join(f"{s}: {e}" for s, e in failed.items())[-1200:], file=sys.stderr)
        return FAILED
    if stopped:
        print(f"stopped between stages: {stopped[0]}", file=sys.stderr)
        return PAUSED
    return 0


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    running = commands.add_parser("run", help="the background run tick launches")
    running.add_argument("--repo", type=Path, default=REPO)
    running.add_argument("--state", type=Path, default=loop_home())
    playing = commands.add_parser("play", help="internal: the stages' games as one referee run, under native.py play")
    playing.add_argument("plan", type=Path)
    args = parser.parse_args()
    if args.command == "play":
        sys.exit(play(args.plan))
    run(args.repo.resolve(), args.state.resolve())


if __name__ == "__main__":
    main()
