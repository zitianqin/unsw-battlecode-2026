"""Map pool: never-used generated maps, one folder a map source (eval and each style of driver.shared_mix), made ahead
on this Mac in idle time, from which a champion's new shared set (driver.shared_set_maps) takes its maps. Until
2026-09-29 each new champion's set was made inside the first evaluation after its promotion (its 'CPU gate and maps'
stage, 9-14 min on 29 Sep at 15:50, 16:59, 18:22 and 18:59, most of it the ~975 worldgen maps), with every game
machine waiting; sig asked (19:57) whether maps are pregenerated.

Every map stays unseen: a pooled map is made from a fresh random seed (SystemRandom) by the referee checkout's mapgen,
checked by the engine's loader as every set's are (driver.generate_maps), and moved (renamed) out of the pool into the
one set that takes it, so no other set, champion or test plays it again. A set takes what the pool holds of each
source and makes the rest from its champion's seed, as before (driver.generate_maps makes only the shortfall).

What made the maps is recorded (INPUTS: driver.map_inputs, the referee checkout's mapgen and map sources, and the
toolkit's version); take() empties a pool whose record differs from now (regenerable copies), so a mapgen change on
main reaches the next set at the evaluation's referee refresh, as it did. With the referee checkout edited by hand
(map_inputs "") nothing is pooled or taken.

The upkeep tick (maintenance.tick, tick here) starts a background fill while the pool is short: one CHUNK of the source
furthest below its target at a time, niced (meta.spawn, the map maker at home.GAME_NICE), and only while the work at
normal priority on this Mac (home.dev_load, sig's dev work) is under the load share (home.share_now x cores) and the
1-minute load average under where niced games stop (home.sessions_full), waiting otherwise; it skips a style the
referee checkout can't make yet (driver.missing_style) and a chunk while its inputs read "" (mid-refresh); it stops for a stop marker and exits once the pool is full.

Size bound: targets(), SETS shared sets' worth of each source (driver.MAX_PAIRS maps a set: ffc2c10's is 63 MB), and
a chunk being made (CHUNK maps) beside it; health.map_pool alerts past BOUND_BYTES, and on a fill that keeps failing
or has made nothing for STALE while alive, or none ever health.FIRST_FIRING after mappool.py reached main. mappool.json (a few hundred bytes) is its record, map-pool.log its log (a
line a chunk, started over past LOG_BYTES).

    .venv/bin/python loop/mappool.py fill      # what tick launches
    .venv/bin/python loop/mappool.py status    # each source's maps against its target
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import meta
import online

CHUNK = 64  # maps a fill makes at a time (a gen.py run and its engine check)
# Sets' worth held: a promotion makes today's mix's set (the prewarm's) and, while candidates frozen on an earlier mix
# wait, that mix's too (29 Sep: 327435f, cfe3b39, 46fc10e, c2932ac, d9cc962 and 4e6ee4f each made both, a worldgen0.5
# set of 1,200 worldgen maps beside today's, 8-14 min each, at once); with none, two promotions in a row (18:22, 18:41).
SETS = 2
BOUND_BYTES = 200 * 2**20  # the pool and its chunk being made: a set is ~63 MB
RETRY_S = 3600  # after a failed fill
STALE = 3600  # a fill alive but with no chunk made for this long, while the pool is short, is stuck
WAIT_S = 20  # how often a fill looks again while this Mac is busy


def pool_dir(state: Path) -> Path:
    return state / "map-pool"


def record_path(state: Path) -> Path:
    return state / "mappool.json"


def read(state: Path) -> dict:
    try:
        return online.read(record_path(state), {})
    except ValueError:
        return {}


def save(state: Path, record: dict) -> None:
    online.save(record_path(state), record)


# What the maps are made from now: the referee checkout's map inputs and the toolkit's version, or "" when the
# checkout was edited by hand (nothing is pooled then).
def inputs(state: Path) -> str:
    import driver
    import maintenance
    made_from = driver.map_inputs()
    return f"{made_from} {maintenance.toolchain(state).get('version')}" if made_from else ""


# The maps each source holds when full: SETS shared sets of today's general mix (driver.shared_mix), as a set deals
# them (driver.mix_sources over driver.MAX_PAIRS maps; eval only without a mix). A set of another mix takes from the
# same folders.
def targets() -> dict[str, int]:
    import driver
    mix = driver.shared_mix()
    if not mix:
        return {"eval": SETS * driver.MAX_PAIRS}
    seats = driver.sprt_seat(1 + len(driver.ROTATION))
    return {source: SETS * n for source, n in Counter(driver.mix_sources(driver.MAX_PAIRS, mix, seats)).items()}


def seed_of(path: Path) -> int:
    return int(path.stem.rsplit("_", 1)[1])


# The pooled maps of `source`, lowest seed first (a chunk's seeds run on, so a set takes whole chunks).
def held(state: Path, source: str) -> list[Path]:
    folder = pool_dir(state) / source
    return sorted(folder.glob(f"gen_{source}_*.map"), key=seed_of) if folder.is_dir() else []


def stamp(state: Path) -> str:
    try:
        return (pool_dir(state) / "INPUTS").read_text().strip()
    except OSError:
        return ""


# Empties the pool when what made its maps differs from `now` (inputs()), and records `now` for the maps to come.
# Called under the pool's lock.
def _restamp(state: Path, now: str) -> None:
    if stamp(state) == now:
        return
    for folder in pool_dir(state).iterdir() if pool_dir(state).is_dir() else []:
        if folder.is_dir() and not folder.name.startswith("."):
            shutil.rmtree(folder, ignore_errors=True)
    pool_dir(state).mkdir(parents=True, exist_ok=True)
    (pool_dir(state) / "INPUTS").write_text(now + "\n")


# Moves up to what `out` lacks of `count` maps of `source` from the pool into `out` (driver.shared_set_maps, before it
# makes the rest), lowest seeds first; returns how many: none when the pool's maps were made by other inputs than the
# referee checkout's now (it is emptied) or the checkout is edited by hand.
def take(out: Path, source: str, count: int, state: Path | None = None) -> int:
    import driver
    state = state or driver.STATE
    have = len(list(out.glob("*.map"))) if out.is_dir() else 0
    if have >= count or not (now := inputs(state)):
        return 0
    with meta.locked(pool_dir(state)):
        _restamp(state, now)
        moved = held(state, source)[:count - have]
        if moved:
            out.mkdir(parents=True, exist_ok=True)
        for path in moved:
            path.rename(out / path.name)  # the same disk: a map is in the pool or in this set, never both
    if moved:
        driver.log(f"took {len(moved)} {source} maps from the map pool into {out}")
    return len(moved)


# Each target source's shortfall now, {source: maps missing}, largest first; {} when full (or unpoolable). A style the
# referee checkout's mapgen can't make yet (driver.missing_style: a mix on main ahead of its refresh) is left out, so
# the other sources still fill.
def short(state: Path) -> dict[str, int]:
    import driver
    if not (now := inputs(state)):
        return {}
    fresh = stamp(state) == now  # else take() or the next chunk empties it
    gaps = {source: count - (len(held(state, source)) if fresh else 0) for source, count in targets().items()
            if source == "eval" or not driver.missing_style({"maps": {source: count}})}
    return dict(sorted(((s, n) for s, n in gaps.items() if n > 0), key=lambda item: -item[1]))


def stopped(state: Path) -> bool:
    return (state / "STOP").exists() or (state / "ASTRA_STOP").exists()


# Whether this Mac has room for map making now: the work at normal priority (home.dev_load, else the 1-minute load)
# under the loop's load share of its cores, and the 1-minute load average under where niced games stop
# (home.sessions_full: sig's other sessions gate on the load average, which a niced fill on every idle core holds up).
def room() -> bool:
    import home
    share, cores = home.share_now(), os.cpu_count() or 1
    load = home.dev_load()
    load = os.getloadavg()[0] if load is None else load
    return load < share * cores and not home.sessions_full(os.getloadavg()[0], share, cores)


# The background fill tick launches: a CHUNK of the source furthest below its target, from a fresh random seed, made
# in a scratch folder and moved into the pool once checked, until the pool is full or a stop marker is set. A chunk made
# while the inputs changed is dropped.
def fill(state: Path) -> None:
    import driver
    scratch = pool_dir(state) / ".making"
    shutil.rmtree(scratch, ignore_errors=True)  # a killed fill's; one fill runs at a time (tick)
    try:
        while (gaps := short(state)) and not stopped(state):
            if not room():
                time.sleep(WAIT_S)
                continue
            source, missing = next(iter(gaps.items()))
            if not (now := inputs(state)):  # the referee checkout mid-refresh: its chunk would be dropped
                time.sleep(WAIT_S)
                continue
            count = min(CHUNK, missing)
            driver.generate_maps(scratch, seed=random.SystemRandom().randrange(1 << 30), count=count, style=source)
            with meta.locked(pool_dir(state)):
                if inputs(state) == now:
                    _restamp(state, now)
                    (pool_dir(state) / source).mkdir(parents=True, exist_ok=True)
                    for path in scratch.glob("*.map"):
                        path.rename(pool_dir(state) / source / path.name)
            shutil.rmtree(scratch, ignore_errors=True)
            with meta.locked(record_path(state)):
                save(state, {**read(state), "progress_at": time.time(), "made": read(state).get("made", 0) + count})
        with meta.locked(record_path(state)):
            save(state, {**read(state), "pid": None, "error": None, "failures": 0, "finished_at": time.time()})
    except Exception as error:
        with meta.locked(record_path(state)):
            record = read(state)
            save(state, {**record, "pid": None, "failed_at": time.time(), "failures": record.get("failures", 0) + 1,
                         "error": f"{type(error).__name__}: {error}"[-600:]})
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# The upkeep tick's step: starts a background fill while the pool is short, unless one is alive, a stop marker is set,
# or a failure is under RETRY_S old. Never waits for it. Returns a line to log, or None.
def tick(repo: Path, state: Path, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    meta.reap()
    if not (state / "ideas.jsonl").exists() or stopped(state):
        return None
    with meta.locked(record_path(state)):
        record = read(state)
        if meta.alive(record.get("pid")):
            return None
        if record.get("pid"):  # died without recording its end
            record.update(pid=None, failed_at=now, failures=record.get("failures", 0) + 1,
                          error=record.get("error") or "the fill exited without finishing; see map-pool.log")
            save(state, record)
        if now - (record.get("failed_at") or 0) < RETRY_S or not (gaps := short(state)):
            return None
        pool_dir(state).mkdir(parents=True, exist_ok=True)
        record.update(pid=launch(repo, state), started_at=now, progress_at=now)
        save(state, record)
    return f"map pool fill launched ({sum(gaps.values())} maps short: " + \
        ", ".join(f"{s} {n}" for s, n in gaps.items()) + ")"


LOG_BYTES = 2**20  # map-pool.log (a line a chunk, ~4 KB a set) starts over past this at a launch


def launch(repo: Path, state: Path) -> int:
    log = state / "map-pool.log"
    if log.exists() and log.stat().st_size > LOG_BYTES:
        log.unlink()
    return meta.spawn([sys.executable, str(Path(__file__).resolve()), "fill", "--state", str(state)], repo / "loop",
                      log)


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("fill", "status"))
    parser.add_argument("--state", type=Path, default=loop_home())
    args = parser.parse_args()
    if args.command == "fill":
        fill(args.state.resolve())
        return
    for source, count in targets().items():
        print(f"{source}: {len(held(args.state, source))} of {count}")
    print(json.dumps(read(args.state)))


if __name__ == "__main__":
    main()
