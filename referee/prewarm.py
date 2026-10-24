"""Plays a champion's baseline games ahead of time, while the game machines would otherwise sit idle.

Experiments tested against one champion share a map set, and the champion's games against the SPRT rotation's
opponents repeat exactly across them (sprt.BaselineCache). This plays those games for the first `--trials` trials,
in the SPRT's own order (trial i plays rotation[i % len(rotation)]; the champion trials, which need a candidate,
are skipped), into the same cache, so the next tests only play the candidate's games. While predicting is on, its
games record the champion's side on this Mac's platform (sprt.BaselineCache), so those tests can predict the
candidate's games that repeat the champion's instead of playing them. Run it with the runtime the tests use, so the
cache keys (toolkit version included) match. The upkeep's idle prewarm (loop/baselines.py) plays the same games in the
same order and with the same recording rule itself, every stage in one stream (baselines.stage_jobs); this is for a
run by hand:

    ~/.bc-loop/toolchains/<version>/bin/python referee/prewarm.py --champion BOT --maps MAP... \\
        --opponent swarm=PATH --opponent breeder=PATH ... --baselines FILE --trials 600 [--rerecord]

--rerecord also replays, recording them, the cached games that have no recording (a champion whose baselines were
played before recording existed), so tests against it can predict too.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from run import Job, parse_workers, stream_games
from sprt import BaselineCache, predicting


# Plays the baseline games of the first `trials` trials that the cache lacks, recording them while predicting is on;
# with `rerecord`, also replays (on this Mac's platform) the cached ones that have no recording of their own, so a
# champion whose baselines predate recording can predict too. Returns (games played, rows whose result changed): a
# replayed row can differ when its first game was played on a host of another platform.
def prewarm(champion: Path, maps: list[Path], opponents: list[Path], cache: BaselineCache, trials: int,
            workers: int | str, rerecord: bool = False) -> tuple[int, int]:
    rotation = [None, *opponents]
    jobs = [Job(game_map, champion, rotation[i % len(rotation)], side)
            for i, game_map in enumerate(maps[:trials]) if rotation[i % len(rotation)] is not None
            for side in "AB"]
    record = predicting(cache, champion)
    if rerecord and not record:
        raise SystemExit("prewarm --rerecord: recording is off here (see the warning above)")
    jobs = [job for job in jobs if cache.get(job) is None or (rerecord and cache.paired(job) is None)]
    if record:
        jobs = [cache.recorded(job) for job in jobs]
    changed = 0
    for job, result in zip(jobs, stream_games(iter(jobs), [champion, *opponents], workers, False)):
        old = cache.get(job)
        if old and not result.error and (old.winner, old.end, old.rounds) != (result.winner, result.end,
                                                                              result.rounds):
            changed += 1
            print(f"{job.map.name} (a {job.a_side}) vs {job.b.name}: was {old.winner}/{old.end}/{old.rounds}, "
                  f"now {result.winner}/{result.end}/{result.rounds}")
        cache.put(job, result)
    return len(jobs), changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--champion", type=Path, required=True)
    parser.add_argument("--maps", type=Path, nargs="+", required=True, help="the shared map set, in the SPRT's order")
    parser.add_argument("--opponent", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=600)
    parser.add_argument("--workers", type=parse_workers, default="auto")
    parser.add_argument("--rerecord", action="store_true",
                        help="also replay and record cached games that have no recording of their own")
    args = parser.parse_args()
    cache = BaselineCache(args.baselines)
    played, changed = prewarm(args.champion, args.maps, [Path(t.split("=", 1)[1]) for t in args.opponent], cache,
                              args.trials, args.workers, args.rerecord)
    print(f"prewarmed {played} baseline games ({changed} cached results changed); the cache holds {len(cache.rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
