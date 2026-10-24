"""Checks a bot stays inside the judge's CPU budget: plays it against itself in the judge's sandbox.

    uv run referee/cpu.py --bot PATH [--maps PATH...] [--games N] [--workers N|auto] [--hosts] [--out FILE]

Prints one line and exits non-zero if any game's p99 is over 50M points, the max is over 90M, or any turn
ran out of time or CPU or crashed (each of which kills the dragon with "no valid action"). `--out` also writes the
numbers as JSON, with one row per game (map, where it played, both sides' timeouts, crashes, no-action deaths, worst
p99 and max) and the games each machine played ("hosts"), so a result that failed here tells its post-mortem which
maps broke the gate (MISSES 58).
`--hosts` (with `--workers auto`) also plays on the loop's remote hosts of this Mac's platform (run.Scheduler): the
loop's own CPU gates only (loop/driver.py), which keep the hosts' shares; without it every game plays here.
"no valid action" deaths with no timeout or crash behind them are trapped dragons that chose not to move;
they are reported but don't fail the check.

The sandbox prices CPU on a virtual clock, so a game's points are deterministic: replaying the same map
gives the same numbers, on any Mac (measured 2026-09-29 on mm and mm1 against this Mac, run.Scheduler).
`--games N` uses the first N maps; there is nothing to gain from repeats.
p50 and p99 are per game as `unswbc run` reports them; the check takes the worst game.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from run import Job, parse_workers, play_games

REPO = Path(__file__).resolve().parent.parent
# Every ladder map in this checkout, current and retired, and the Sprint two (their 11-14-long starters may be the
# worst case): all files in maps/ladder, maps/retired and maps/sprint. The official-map report plays the same less
# maps/retired (ladder_maps.local_files, which this copy can't import); the retired ones stay here because Big Empty
# (64x64) is still our largest map. The loop's CPU gate plays these from the referee checkout, so they change only
# when a map committed to main reaches it (driver.refresh_referee). 13 maps from 2026-09-27.
DEFAULT_MAPS = sorted([*(REPO / "maps/ladder").glob("*.map"), *(REPO / "maps/retired").glob("*.map")]) \
    + sorted((REPO / "maps/sprint").glob("*.map"))
P99_LIMIT = 50_000_000
MAX_LIMIT = 90_000_000


def millions(points: int) -> str:
    return f"{points / 1e6:.1f}M"


# Plays the games and returns (passed, one-line report, the worst p50/p99/max points, the turns counted, the
# per-game rows and the games each machine played). `hosts`: --hosts.
def check(bot: Path, maps: list[Path], workers: int | str, hosts: bool = False) -> tuple[bool, str, dict]:
    results = play_games([Job(m, bot, bot, "A") for m in maps], workers, sandbox=True, sandbox_hosts=hosts)
    problems: list[str] = []
    games: list[dict] = []
    p50 = p99 = top = turns = timeouts = crashes = no_action = 0
    for r in results:
        if r.error or r.a is None or r.b is None:
            problems.append(f"{Path(r.map).name}: game error: {r.error}")
            games.append({"map": Path(r.map).name, "host": r.host, "error": str(r.error)[:300]})
            continue
        sides = (r.a, r.b)
        games.append({"map": Path(r.map).name, "host": r.host, **{key: sum(getattr(side, key) for side in sides)
                                                   for key in ("timeouts", "crashes", "no_action_deaths")},
                      **{key: max((getattr(side.points, key) for side in sides if side.points), default=None)
                         for key in ("p99", "max")}})
        for side in sides:
            timeouts += side.timeouts
            crashes += side.crashes
            no_action += side.no_action_deaths
            if side.points is None:
                problems.append(f"{Path(r.map).name}: no CPU points reported")
                continue
            p50, p99, top = max(p50, side.points.p50), max(p99, side.points.p99), max(top, side.points.max)
            turns += side.points.turns
    if p99 > P99_LIMIT:
        problems.append(f"p99 over {millions(P99_LIMIT)}")
    if top > MAX_LIMIT:
        problems.append(f"max over {millions(MAX_LIMIT)}")
    if timeouts or crashes:
        problems.append("bot failed to answer")
    verdict = "FAIL" if problems else "PASS"
    line = (f"CPU {verdict} p50 {millions(p50)} p99 {millions(p99)} max {millions(top)} timeouts {timeouts} "
            f"crashes {crashes} no-action-deaths {no_action} ({len(results)} games, {turns} turns)")
    return (not problems, line + (f": {'; '.join(problems)}" if problems else ""),
            {"p50": p50, "p99": p99, "max": top, "turns": turns, "games": games,
             "hosts": dict(Counter(r.host or "here" for r in results))})


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a bot's CPU use in the judge's sandbox (self-play).")
    parser.add_argument("--bot", type=Path, required=True)
    parser.add_argument("--maps", type=Path, nargs="+", default=DEFAULT_MAPS)
    parser.add_argument("--games", type=int, help="play only the first N maps")
    parser.add_argument("--workers", type=parse_workers, default="auto")
    parser.add_argument("--hosts", action="store_true", help="also play on the remote hosts of this Mac's platform")
    parser.add_argument("--out", type=Path, help="also write the numbers as JSON")
    args = parser.parse_args()

    maps = args.maps[: args.games] if args.games else args.maps
    if args.games and args.games > len(args.maps):
        parser.error(f"--games {args.games} but only {len(args.maps)} maps (games are deterministic)")
    passed, line, points = check(args.bot, maps, args.workers, args.hosts)
    print(line)
    if args.out:
        args.out.write_text(json.dumps({"ok": passed, "line": line, **points}) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
