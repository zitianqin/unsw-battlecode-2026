"""Where one game's CPU goes, kept fresh for loop/health.py (loop/LOOP.md "Throughput",
MISSES 35): plays one native game with the engine loaded precompiled through referee/engine_cache.py and the map's own
pearl seed, starting one bot process per dragon as the toolkit does, with timers around the toolkit's phases, and
writes the split to <loop home>/game-profile.json. It no longer matches referee/run.py's launch path: run.py forks
each team's bots from one preloaded copy (referee/fork_server.py, 2026-09-27), so its launch and start-up shares
overstate a referee game's.

    .venv/bin/python tools/game_profile.py [--map maps/ladder/schooltime.map] [--bot ~/.bc-loop/champion/bot]

The split, in CPU seconds and as shares of the game's total (the game process and every bot process it started):
  engine           the wasm engine itself (EngineModule.run less the Python it calls back into)
  Python glue      the rest of the game process: callbacks, framing, start-up, the replay
  bot I/O          the game process talking to its bots (Bot.ask, less any bot start inside it)
  bot launches     the game process starting and stopping bot processes (Pool._launch, Bot.stop): one per dragon
  bot start-up     the bot processes' own start-up: the bot started alone and ended at once (stdin closed),
                   times the game's launches
  bots             the rest of the bot processes' CPU: their play
A timer the toolkit no longer has (a renamed method) is left out and reported as missing, its CPU counted as glue.
It refuses to start above the load gate (this Mac's load at the loop's share of its cores, or under 3 GiB free);
health.py alerts when the file is over a week old or older than the active toolkit, and the orchestrator runs this
then. It is never scheduled inside an evaluation.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "loop"))
from home import MIN_FREE_GB, dev_load, load_share, loop_home, mac_slots, reclaimable_gb  # noqa: E402

DEFAULT_MAP = REPO / "maps/ladder/schooltime.map"
STARTUPS = 20  # bot processes started alone to measure one's start-up CPU
STARTUP_TIMEOUT_S = 5


# The game process: the toolkit's phases wrapped in exclusive CPU timers (a timer's own CPU less the timers inside
# it), then `unswbc run ...` as engine_cache.py runs it. Prints one "PROFILE {json}" line on stderr at the end.
def child(cache: Path, command: list[str]) -> int:
    start_wall = time.perf_counter()
    totals: dict[str, float] = defaultdict(float)
    counts: Counter = Counter()
    stack: list[list] = []
    missing = []

    def timed(name: str, fn):
        def wrapper(*args, **kwargs):
            stack.append([time.process_time(), 0.0])
            try:
                return fn(*args, **kwargs)
            finally:
                began, inner = stack.pop()
                spent = time.process_time() - began
                totals[name] += spent - inner
                counts[name] += 1
                if stack:
                    stack[-1][1] += spent
        return wrapper

    def patch(owner, attribute: str, name: str) -> None:
        if owner is None or not callable(getattr(owner, attribute, None)):
            missing.append(name)
            return
        setattr(owner, attribute, timed(name, getattr(owner, attribute)))

    import wasmtime
    import unswbc.bot as bots
    import unswbc.engine as engine

    define = wasmtime.Linker.define_func

    def timed_define(self, module, name, ty, func, *args, **kwargs):  # every callback the engine makes into Python
        return define(self, module, name, ty, timed("callbacks", func), *args, **kwargs)

    wasmtime.Linker.define_func = timed_define
    patch(getattr(bots, "Pool", None), "_launch", "launch")
    patch(getattr(bots, "Bot", None), "stop", "stop")
    patch(getattr(bots, "Bot", None), "ask", "ask")
    patch(getattr(engine, "EngineModule", None), "run", "engine")
    sys.path.insert(0, str(REPO / "referee"))
    import engine_cache
    cached = engine_cache.install(cache)
    from unswbc.cli import main as unswbc

    sys.argv = ["unswbc", *command]
    code = unswbc(command)
    own, bots_cpu = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    print("PROFILE " + json.dumps({
        "exit": code, "wall_s": time.perf_counter() - start_wall, "engine_cache": cached, "missing": missing,
        "game_cpu_s": own.ru_utime + own.ru_stime, "bots_cpu_s": bots_cpu.ru_utime + bots_cpu.ru_stime,
        "timers": dict(totals), "counts": dict(counts),
        "unswbc": importlib.metadata.version("unswbc")}), file=sys.stderr, flush=True)
    return code


# The CPU one bot process takes to start and end with nothing to read, the median of STARTUPS runs, or None when
# the bot is no single executable or doesn't end at once on a closed stdin.
def startup_cpu(command: list[str] | None) -> float | None:
    if not command:
        return None
    spent = []
    for _ in range(STARTUPS):
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = status
                spent.append(usage.ru_utime + usage.ru_stime)
                break
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                return None
            time.sleep(0.01)
    return sorted(spent)[len(spent) // 2]


# Why this Mac is too busy for a measured game now, or None: the same gate as the loop's games at normal priority
# (home.mac_slots), counting the cores busy at normal priority (home.dev_load), not the loop's niced games, which give
# this one the CPU; the 1-minute load where that can't be measured.
def busy(state: Path) -> str | None:
    cores = os.cpu_count() or 1
    try:
        share = load_share((state / "load-fraction").read_text())
    except OSError:
        share = load_share("")
    load, free = dev_load(), reclaimable_gb()
    load = os.getloadavg()[0] if load is None else load
    if mac_slots(share, cores, load, 0, free):
        return None
    return f"load {load:.2f} against {share:g} x {cores} cores, {free:.1f} GiB reclaimable (under {MIN_FREE_GB} stops it)"


# The split described in the module docstring, from the child's report and one bot process's start-up CPU.
def split(report: dict, startup: float | None) -> dict[str, float]:
    timers, launches = report["timers"], report["counts"].get("launch", 0)
    engine = timers.get("engine", 0.0)
    io, launch = timers.get("ask", 0.0), timers.get("launch", 0.0) + timers.get("stop", 0.0)
    parts = {"engine": engine, "Python glue": report["game_cpu_s"] - engine - io - launch, "bot I/O": io,
             "bot launches": launch}
    start = min(report["bots_cpu_s"], startup * launches) if startup is not None else 0.0
    if startup is not None:
        parts["bot start-up"] = start
    parts["bots"] = report["bots_cpu_s"] - start
    return {name: round(value, 3) for name, value in parts.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--bot", type=Path, default=loop_home() / "champion/bot", help="plays both sides")
    parser.add_argument("--out", type=Path, default=loop_home() / "game-profile.json")
    parser.add_argument("--child", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(Path(args.child[0]), args.child[1:])
    if reason := busy(loop_home()):
        print(f"not now: this Mac is busy ({reason}); try again when it is under the load gate", file=sys.stderr)
        return 1
    sys.path.insert(0, str(REPO / "referee"))
    import run
    bot, game_map = args.bot.resolve(), args.map.resolve()
    native = run.build_bots([bot], False)[bot]
    with tempfile.TemporaryDirectory(prefix="game-profile-") as scratch:
        command = ["run", str(game_map), str(bot), str(bot), "-o", str(Path(scratch) / "game.replay")]
        if (seed := run.game_seed(game_map)) is not None:
            command += ["--seed", str(seed)]
        result = subprocess.run([sys.executable, __file__, "--child", str(loop_home() / "engine-cache"), *command],
                                capture_output=True, text=True, timeout=run.GAME_TIMEOUT_S)
    lines = [line for line in result.stderr.splitlines() if line.startswith("PROFILE ")]
    if not lines or result.returncode:
        print(f"the game failed (exit {result.returncode}): {(result.stdout + result.stderr)[-1500:]}", file=sys.stderr)
        return 1
    report = json.loads(lines[-1].removeprefix("PROFILE "))
    startup = startup_cpu([str(native)] if native else None)
    parts = split(report, startup)
    total = report["game_cpu_s"] + report["bots_cpu_s"]
    profile = {"at": time.time(), "time": time.strftime("%Y-%m-%d %H:%M"), "unswbc": report["unswbc"],
               "python": sys.executable, "map": game_map.name, "bot": str(bot), "engine_cache": report["engine_cache"],
               "wall_s": round(report["wall_s"], 3), "cpu_s": round(total, 3),
               "launches": report["counts"].get("launch", 0), "turns": report["counts"].get("ask", 0),
               "startup_cpu_s": startup, "missing_timers": report["missing"], "split_s": parts,
               "share": {name: round(value / total, 3) for name, value in parts.items()} if total else {}}
    temporary = args.out.with_name(f"{args.out.name}.tmp")
    temporary.write_text(json.dumps(profile, indent=1) + "\n")
    temporary.replace(args.out)
    print(json.dumps(profile, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
