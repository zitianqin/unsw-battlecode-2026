"""The native judge's fidelity gate: plays the same games through today's path (`unswbc run` with the engine cache
and the fork server, as run.run_game plays them) and through the native judge (native_judge.py), and compares them.

    <toolkit python> referee/native_fidelity.py --bots A B [C...] --maps MAP_OR_FOLDER... --out REPORT.json
                     [--pairs first|all|rotate] [--workers N] [--host NAME] [--speed REPEATS] [--limit N]
    <toolkit python> referee/native_fidelity.py --gate --bots CHAMPION OPPONENT... [--host NAME] --out REPORT.json
    <toolkit python> referee/native_fidelity.py --gate-missing --bots CHAMPION OPPONENT... --out FOLDER

Each map is played with the first bot (the champion) against every other bot (`--pairs all`: every pair; `rotate`: the
others in turn, one a map), from both seats, once each way. `--gate` plays the fixed sample (FIDELITY_SAMPLE games on
GATE_MAPS, rotating) and records in <loop home>/native-judge/enabled.json whether this key (native_judge.key: engine,
toolkit, judge, shim and launcher) may play natively at the place that played it (this Mac's platform, or the host's
remote.Host.native_place): only with no mismatch and at least GATE_MIN games compared (native_judge.record_gate; a
failure also writes a host-alerts.log line), under the key it took at its start, and only if that key still holds at
its end (the referee checkout or the toolkit can change under it). `--gate-missing` gates, one at a time, every place
the loop plays at (`places`: one Mac host, one Linux host of each compiler group, and here only with no Mac host, since
this Mac's games go on the Mac hosts' gate, run.native_gated; never Windows) that has no gate for this key: the
upkeep's hourly step (loop/maintenance.py's native_gates) runs it from the referee checkout, with the champion
exported at the run's start (maintenance.pinned_champion: a promotion mid-run would change the bot between a game's
two ways, and the bots are played as given, their sources read at each local game), at a screen filler's share
beside evaluations, so a new toolkit, engine, judge or shim is gated without anyone asking; a run with nothing missing
ends before asking any host. It gates the hosts' places before this Mac's, and a place whose game finds no room (NoRoom) keeps its gate's matched games for the next run to go on from (gate) while the
run goes on to the next place. Under the upkeep's launch a host's games in
flight go to its status file (BC_REMOTE_STATUS, Player.save) for the utilisation record and health's share check.
A native game that didn't say "native judge: playing" fell back to unswbc run (`fell back`), and one that raised (a
host failing) or that this machine failed twice (an OS error: machine_failure) is an `error`: neither is compared. A
game matches when run.compare_games finds its replay bytes identical, run.parse_game reading the same result from both
(seconds aside) and the output the same line for line but for timings, the replay's path and the launchers' own notes
("engine cache: ...", "fork server: ...", "native judge: ..."). A mismatch reports the first replay event that
differs, with its round. A game in which either path ran a turn out of time (10 s of wall time, which a loaded machine
at nice 19 can cause) is played once more both ways and, if its reference does so again, reported apart as `starved`,
never as a match; a native game alone out of time the second time is compared only once played unlowered (check).

Games play on this Mac at nice 19 (GAME_NICE), each waiting for tools/gated_test.sh's load gate and a slot
(run.game_slots) first, or with `--host NAME` on that remote host through remote.Host.play, the way a referee run plays
it there, while the host has room (remote.Host.capacity); both paths then play on that host. Under loop/native.py play
(the upkeep's launch) both follow its shares. `--speed R` also plays every game R more times each way, one at a time,
and reports the median CPU seconds of the whole game and of the engine and glue alone (the game process's own CPU, bots
and fork servers apart), per game and per dragon turn.

The report (JSON) lists every game with its verdict and a summary: games, matches, mismatches (with their first
differing turn), starved, fell-back and errored games, and the counts by map folder, seat and opponent. run.NATIVE_JUDGE
has been on since 29 Sep (its comment says on what evidence); a key still plays natively only where its gate
passed."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import native_judge
import remote
import run

HERE = Path(__file__).resolve().parent
GATE = HERE.parent / "tools" / "gated_test.sh"
PLAYED = run.NATIVE_PLAYED  # what play.c says first on stderr
CPU_LINE = re.compile(r"^cpu self (\S+) children (\S+)", re.M)
# Runs the reference game's Python in this process, so its own CPU (engine and glue) and its children's (bots, fork
# servers) can be told apart at exit; the native judge reports its own with BC_JUDGE_CPU.
MEASURE = r"""
import atexit, os, resource, runpy, sys
cmd = sys.argv[1:]
if "native-judge-" in os.path.basename(cmd[0]):
    os.execv(cmd[0], cmd)
def report():
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    sys.stdout.flush()
    print(f"cpu self {s.ru_utime + s.ru_stime:.6f} children {c.ru_utime + c.ru_stime:.6f}", file=sys.stderr, flush=True)
atexit.register(report)
python = os.path.basename(cmd[0]).startswith("python")
sys.argv = cmd[1:] if python else cmd
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
runpy.run_path(sys.argv[0], run_name="__main__")
"""
GAME_NICE = 19  # this Mac's games (Player.play): the loop's own games' priority, below sig's work


# The comparison both ways (run.compare_games, which the loop's own checks use too).
def compare(job: run.Job, reference: run.RawGame, native: run.RawGame) -> dict:
    return run.compare_games(job, reference, native)


# Waits for tools/gated_test.sh's load gate, `seconds` at most.
def wait_for_gate(seconds: float) -> None:
    if GATE.is_file():
        subprocess.run([str(GATE), "true"], stdout=subprocess.DEVNULL, check=False,
                       env={**os.environ, "GATED_MAX_WAIT": str(max(1, int(seconds)))})


ROOM_POLL_S = 5  # how often a game waiting for room here or on its host asks again
ROOM_WAIT_S = 3600  # a game that waited this long for room (a host down for good) stops its Player (NoRoom)
GATE_S = 4 * 3600  # a gate's games wait for room until this long after it started, at most (gate's `deadline`)


class NoRoom(TimeoutError):
    """A game found no room within ROOM_WAIT_S or its Player's deadline: every game of that Player not yet started
    raises it too, so a gate that can't get room ends at once, recording nothing."""


class Player:
    """Plays one game either way, here or on one host, each once there is room for it (room), until `deadline`
    (time.monotonic()) or the first game that found none (`no_room`)."""

    def __init__(self, bots: list[Path], host: str | None, folder: Path, workers: int = 2,
                 deadline: float = float("inf")):
        self.folder, self.host, self.workers, self.deadline = folder, None, workers, deadline
        self.no_room: str | None = None
        self.native_bots = run.build_bots(bots, False)
        self.lock, self.here = threading.Lock(), 0  # this Player's games playing here
        if host:
            found = [h for h in remote.load_hosts() if h.name == host]
            if not found:
                raise SystemExit(f"no host {host} in {remote.CONFIG}")
            self.host = found[0]

    # `lowered` False plays a game here at this process's own priority (check's replay of a native game out of time).
    def play(self, job: run.Job, native: bool, lowered: bool = True) -> run.RawGame:
        path = self.folder / f"{uuid.uuid4().hex}.replay"
        try:
            with self.room():
                if self.host is None:
                    return run.run_game(job, False, path, nice=max(GAME_NICE, run.GAME_NICE) if lowered else 0,
                                        native=native)
                return self.play_remote(job, native)
        finally:
            path.unlink(missing_ok=True)

    # Holds a slot for one game: here, once the load gate and run.game_slots leave one, or on the host while its
    # capacity (the host's idle cores, or under loop/native.py's yielding share: a screen filler's beside an
    # evaluation playing there) is above the games this process plays there. NoRoom past ROOM_WAIT_S or `deadline`.
    @contextlib.contextmanager
    def room(self):
        host, until = self.host, min(time.monotonic() + ROOM_WAIT_S, self.deadline)
        while True:
            if self.no_room:
                raise NoRoom(self.no_room)
            if host is None:
                wait_for_gate(until - time.monotonic())
            with self.lock:
                if host is None and self.here < run.game_slots(self.workers, self.here):
                    self.here += 1
                    break
                if host is not None and host.active < host.capacity():
                    host.add_active(1)
                    self.save()
                    break
            if time.monotonic() > until:
                self.no_room = (f"no room for a game {'here' if host is None else 'on ' + host.name} in "
                                f"{ROOM_WAIT_S // 60} min or by the gate's deadline")
                raise NoRoom(self.no_room)
            time.sleep(ROOM_POLL_S)
        try:
            yield
        finally:
            with self.lock:
                if host is None:
                    self.here -= 1
                else:
                    host.add_active(-1)
                    self.save()

    # With BC_REMOTE_STATUS set (maintenance.native_gates' launch: remote-status-native-gate.json, which loop/native.py
    # makes remote.STATUS) the host's games in flight go there as a referee run's do (run.Scheduler.save), so
    # throughput's rows and health.above_evaluation_share count them as a screen filler's; never the evaluation's file.
    def save(self) -> None:
        if self.host and os.environ.get("BC_REMOTE_STATUS"):
            remote.save_status([self.host], run={"running": 0, "active": {self.host.name: self.host.active},
                                                 "cores": {self.host.name: self.host.cores}})

    def play_remote(self, job: run.Job, native: bool) -> run.RawGame:
        host = self.host
        first, second = (job.a, job.b) if job.a_side == "A" else (job.b, job.a)
        bots = tuple(self.native_bots[bot] if not host.foreign() else bot for bot in (first, second))
        folder = f"{remote.ROOT}/games/{uuid.uuid4().hex}"
        plan = run.fork_plan(job, False)
        plan = (plan[0], False) if plan else None  # a check game plays neither way natively: compare forked games
        start = time.monotonic()
        game = host.play(job.map, bots, run.REMOTE_TIMEOUT_S, folder, run.game_seed(job.map), fork=plan,
                         native=native)
        return run.RawGame(game.stdout, game.returncode, game.replay, time.monotonic() - start, host.name)


# Both seats of each map: the first bot against every other (`first`), every pair (`all`), or against the others in
# turn, one a map (`rotate`).
def jobs_for(bots: list[Path], maps: list[Path], pairs: str) -> list[run.Job]:
    if pairs == "rotate":
        return [run.Job(game_map, bots[0], bots[1 + i % (len(bots) - 1)], side)
                for i, game_map in enumerate(maps) for side in ("A", "B")]
    matchups = ([(bots[0], other) for other in bots[1:]] if pairs == "first"
                else [(a, b) for i, a in enumerate(bots) for b in bots[i + 1:]])
    return [run.Job(game_map, a, b, side) for game_map in maps for a, b in matchups for side in ("A", "B")]


# The gate's fixed sample (--gate): FIDELITY_SAMPLE games, both seats of the first maps of GATE_MAPS in order, the
# champion against the other bots in turn. The same games for every engine, so gates compare.
FIDELITY_SAMPLE = 40
GATE_MAPS = ("ladder", "sprint", "community", "extra", "gen-fixed")


def gate_maps() -> list[Path]:
    root = HERE.parent / "maps"
    return [game_map for folder in GATE_MAPS for game_map in sorted((root / folder).glob("*.map"))][
        :FIDELITY_SAMPLE // 2]


def expand_maps(items: list[str]) -> list[Path]:
    maps = []
    for item in items:
        path = Path(item)
        maps += sorted(path.glob("*.map")) if path.is_dir() else [path]
    return maps


VERDICTS = ("match", "MISMATCH", "starved", "fell back", "error")


# One game both ways: its report row. A turn out of time either way plays both ways once more; then only the
# reference's leaves the game out (`starved`). A native game alone out of time, or past its time, the second time is
# played once more at this process's own priority where that isn't lowered (here, run by hand) and then compared like
# any difference, so a mismatch is one that reproduces unlowered; where it can't be (on a host, which plays unlowered
# already, it is compared at once; here under a niced process) it is `starved` too. A reference that didn't finish
# (past its time, killed, an OS error: run.infrastructure_failure) is an `error`, and so is a native game this machine
# failed (machine_failure) twice.
def check(player: Player, job: run.Job) -> dict:
    row = {"map": str(job.map), "folder": job.map.parent.name, "a": job.a.name, "b": job.b.name, "a_side": job.a_side}

    def late(game: run.RawGame) -> bool:
        return run.out_of_time(game) or game.returncode is None

    try:
        for attempt in range(2):
            reference, native = player.play(job, False), player.play(job, True)
            if not run.out_of_time(reference) and not run.out_of_time(native):
                break
        if player.host is None and not run.out_of_time(reference) and late(native):
            if run.own_nice() > 0:
                return {**row, "verdict": "starved", "problems": ["the native game ran out of time at a lowered "
                                                                  "priority, which this process can't leave"]}
            native = player.play(job, True, lowered=False)
        if machine_failure(native):
            native = player.play(job, True)
    except Exception as error:  # a host that failed says nothing of the judge: left out, as a starved game is
        return {**row, "verdict": "error", "problems": [f"{type(error).__name__}: {str(error)[:300]}"]}
    row["seconds"] = [round(reference.seconds, 2), round(native.seconds, 2)]
    if run.out_of_time(reference):
        return {**row, "verdict": "starved", "problems": ["a turn ran out of time in both tries"]}
    failed = run.infrastructure_failure(reference)
    if reference.returncode is None or failed:  # the machine's doing
        return {**row, "verdict": "error", "problems": [f"the reference didn't finish: {failed or 'past its time'}"]}
    if failed := machine_failure(native):
        return {**row, "verdict": "error", "problems": [f"the native game didn't finish twice: {failed}"]}
    if PLAYED not in native.stdout:  # it fell back to unswbc run, so the two ways were one: nothing was compared
        notes = [line for line in native.stdout.splitlines() if line.startswith("native judge:")]
        return {**row, "verdict": "fell back", "problems": [f"the native game didn't play natively: {notes[:2]}"]}
    compared = compare(job, reference, native)
    return {**row, "verdict": "match" if compared["match"] else "MISMATCH", "problems": compared["problems"],
            "winner": compared["result"]["winner"], "rounds": compared["result"]["rounds"]}


# Why a native game failed through this machine (run.infrastructure_failure: an OS error such as a fork refused under
# load, or a kill by run.MACHINE_SIGNALS), or None. Any other signal is the judge crashing (run.judge_failure), which
# is compared like any difference.
def machine_failure(game: run.RawGame) -> str | None:
    signum = run.died_by(game)
    return None if signum and signum not in run.MACHINE_SIGNALS else run.infrastructure_failure(game)


# CPU seconds of one game played `native` or not: (whole game, the game process alone), from MEASURE's line or the
# judge's BC_JUDGE_CPU line.
def measured(player: Player, job: run.Job, native: bool) -> tuple[float, float, int] | None:
    game = player.play(job, native)
    found = CPU_LINE.search(run.ANSI.sub("", game.stdout))
    if not found or game.returncode != 0 or game.replay is None:
        return None
    replay = run.REPLAY_SCHEMA.Replay.from_bytes_packed(game.replay, traversal_limit_in_words=2**62)
    turns = sum(1 for event in replay.events if event.which() == "turnStart")
    own, children = float(found.group(1)), float(found.group(2))
    return own + children, own, turns


def speed(player: Player, jobs: list[run.Job], repeats: int) -> dict:
    original = run.nice_command
    run.nice_command = lambda command, nice: original([sys.executable, "-c", MEASURE, *command], nice)
    os.environ["BC_JUDGE_CPU"] = "1"
    rows = []
    try:
        for job in jobs:
            for _ in range(repeats):
                for native in (False, True):
                    if (got := measured(player, job, native)) is not None:
                        rows.append({"map": job.map.name, "a_side": job.a_side, "b": job.b.name, "native": native,
                                     "game_cpu": got[0], "own_cpu": got[1], "turns": got[2]})
    finally:
        run.nice_command = original
        os.environ.pop("BC_JUDGE_CPU", None)

    def median(native: bool, key: str, per_turn: bool = False) -> float | None:
        values = [row[key] / row["turns"] * (1e6 if per_turn else 1) for row in rows
                  if row["native"] == native and row["turns"]]
        return round(statistics.median(values), 4 if not per_turn else 1) if values else None

    # Paired by game: native CPU over reference CPU for each (map, seat, opponent), then the median ratio.
    def ratio(key: str) -> float | None:
        by = {}
        for row in rows:
            by.setdefault((row["map"], row["a_side"], row["b"], row["native"]), []).append(row[key])
        ratios = [statistics.median(by[(m, s, b, True)]) / statistics.median(values)
                  for (m, s, b, native), values in by.items() if not native and (m, s, b, True) in by]
        return round(statistics.median(ratios), 3) if ratios else None

    return {"repeats": repeats, "games": len(jobs), "measured": len(rows),
            "reference": {"game_cpu_s": median(False, "game_cpu"), "own_cpu_s": median(False, "own_cpu"),
                          "own_us_per_turn": median(False, "own_cpu", True)},
            "native": {"game_cpu_s": median(True, "game_cpu"), "own_cpu_s": median(True, "own_cpu"),
                       "own_us_per_turn": median(True, "own_cpu", True)},
            "native_over_reference": {"game_cpu": ratio("game_cpu"), "own_cpu": ratio("own_cpu")}, "rows": rows}


def summary(rows: list[dict]) -> dict:
    def counts(key: str) -> dict:
        out: dict = {}
        for row in rows:
            entry = out.setdefault(row[key], {"games": 0, **dict.fromkeys(VERDICTS, 0)})
            entry["games"] += 1
            entry[row["verdict"]] += 1
        return out

    return {"games": len(rows), "match": sum(r["verdict"] == "match" for r in rows),
            "mismatch": sum(r["verdict"] == "MISMATCH" for r in rows),
            "starved": sum(r["verdict"] == "starved" for r in rows),
            "fell_back": sum(r["verdict"] == "fell back" for r in rows),
            "errors": sum(r["verdict"] == "error" for r in rows),
            "mismatches": [r for r in rows if r["verdict"] == "MISMATCH"],
            "by_folder": counts("folder"), "by_seat": counts("a_side"), "by_opponent": counts("b")}


# Plays `jobs` both ways with `player` (`workers` at once) and returns the report, which names `played` as its key
# (native_judge.key when not given).
def play_all(player: Player, jobs: list[run.Job], workers: int, played: str | None = None) -> dict:
    lock, rows = threading.Lock(), []

    def one(job: run.Job) -> None:
        row = check(player, job)
        with lock:
            rows.append(row)
            if row["verdict"] != "match":
                print(f"{row['verdict']} {Path(row['map']).name} {row['a']} vs {row['b']} (a {row['a_side']}): "
                      f"{row['problems']}", file=sys.stderr, flush=True)
            if len(rows) % 10 == 0:
                print(f"{len(rows)}/{len(jobs)} games compared", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, jobs))
    return {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "host": player.host.name if player.host else remote.PLATFORM,
            "platform": player.host.native_place() if player.host else remote.PLATFORM,
            "engine": native_judge.engine_wasm()[1][:16], "toolkit": native_judge.toolkit_digest(),
            "key": played or native_judge.key(), "summary": summary(rows), "games": rows}


# A gate passes with no mismatch and at least GATE_MIN of its FIDELITY_SAMPLE games compared: a game starved at nice
# 19 on a busy machine (its reference's turn out of time in both tries), one that fell back to unswbc run (the judge
# couldn't build or start there) or one a host failed is left out, neither passing nor failing it, and a gate with too
# few compared games but no mismatch records nothing, so the next --gate-missing run plays it again.
GATE_MIN = 36


# The gate at one place (`host`, or this Mac): plays the fixed sample, writes the report to `out` and records the
# verdict (native_judge.record_gate; a failure also alerts) for `gated` (this process's key, native_judge.process_key,
# unless given), unless the key on disk isn't that by its end (the referee checkout refreshed in place, or the toolkit
# upgraded, under it): then nothing is recorded and the next run gates the new key. Nor when a game found no room
# (Player.no_room: waits end GATE_S after its start) or too few compared: the games that matched are kept
# (native_judge.record_progress) and the next hourly run plays only the rest of the sample, so a gate at a place
# whose hosts have no room beside back-to-back evaluations (a screen filler's share is none on a host under 8 cores,
# or while a tune plays) still completes over several runs. The judge's build here is the one this process first
# found. True when it passed, None when it found no room.
def gate(bots: list[Path], host: str | None, out: Path, workers: int, gated: str | None = None) -> bool | None:
    gated = gated or native_judge.process_key()
    home = remote.loop_home()
    with tempfile.TemporaryDirectory() as scratch:
        player = Player(bots, host, Path(scratch), workers, time.monotonic() + GATE_S)
        place = player.host.native_place() if player.host else remote.PLATFORM
        kept = native_judge.gate_progress(home, gated, place) if gated else []
        done = {(row.get("map"), row.get("a_side"), row.get("b")) for row in kept}
        jobs = [job for job in jobs_for(bots, gate_maps(), "rotate")[:FIDELITY_SAMPLE]
                if (str(job.map), job.a_side, job.b.name) not in done]
        report = play_all(player, jobs, workers, gated)
    report["games"] = [*kept, *report["games"]]
    report["summary"] = s = summary(report["games"])
    write(out, report)

    def keep(why: str) -> None:  # the games that matched, for the next run to go on from
        native_judge.record_progress(home, gated, place, [row for row in report["games"] if row["verdict"] == "match"])
        print(f"gate at {place}: {why}: not recorded; {s['match']} matched games kept, so the next hourly run plays "
              f"the rest ({out})", flush=True)

    if gated is None or (now := native_judge.key()) != gated:
        print(f"gate at {place}: the key isn't the one this process plays ({gated} -> {now}): not "
              f"recorded, so the new key is gated next ({out})", flush=True)
        return None if player.no_room else False
    if player.no_room and not s["mismatch"]:  # a mismatch fails it whatever else found no room
        keep(player.no_room)
        return None
    passed = s["mismatch"] == 0 and s["match"] >= GATE_MIN
    why = None if passed else (f"{s['mismatch']} mismatched, {s['starved']} starved, {s['fell_back']} fell back, "
                               f"{s['errors']} errors of {s['games']} games")
    if not passed and not s["mismatch"]:
        keep(f"only {s['match']} games compared ({why}), under {GATE_MIN}")
        return False
    native_judge.record_progress(home, gated, place, [])
    native_judge.record_gate(home, gated, report["platform"], passed, s["games"], s["match"], str(out), why)
    if not passed:
        remote.Host.alert(f"native judge: the fidelity gate for {gated} at {report['platform']} "
                          f"failed ({why}); its games play through unswbc run; see {out}")
    print(f"gate at {report['platform']}: {s['games']} games, {s['match']} match, {s['mismatch']} mismatch, "
          f"{s['starved']} starved -> {'passed' if passed else 'FAILED'} ({out})", flush=True)
    return passed


# Where the loop plays native games: one host for each remote.Host.native_place of the Mac and Linux hosts that
# answer (Windows hosts never play natively), and this Mac's platform unless a Mac host answered (this Mac's games then
# go on the Mac hosts' gate, run.native_gated), as ({place: host name or None}, {host name: its place, None for a
# Windows host}); a host that fails to answer is in neither, and is asked again next run.
def places() -> tuple[dict[str, str | None], dict[str, str | None]]:
    found: dict[str, str | None] = {remote.PLATFORM: None}
    hosts: dict[str, str | None] = {}
    for host in remote.load_hosts():
        try:
            hosts[host.name] = None if host.windows() else host.native_place()
        except Exception as error:  # gated at the next run that reaches it
            print(f"{host.name} skipped: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            continue
        if hosts[host.name] is not None:
            found.setdefault(hosts[host.name], host.name)
    if remote.MAC_HOSTS in found:
        del found[remote.PLATFORM]
    return found, hosts


LIST_S = 86400  # --gate-missing asks the hosts for their places again after this long (a new compiler), else only for
# a host it has no place for


# True when the last listing (enabled.json's "wanted") is for `gated`, under LIST_S old, has a place for every host
# in the config, and every place it wanted has a gate row for `gated`: then there is nothing to gate, known without
# asking a host.
def nothing_missing(gated: str) -> bool:
    data = native_judge.gates(remote.loop_home())
    wanted = data.get("wanted") if isinstance(data.get("wanted"), dict) else {}
    rows = (data.get("keys") or {}).get(gated) or {}
    listed = wanted.get("listed")
    return (wanted.get("key") == gated and isinstance(listed, (int, float)) and time.time() - listed < LIST_S
            and {host.name for host in remote.load_hosts()} <= set(wanted.get("hosts") or {})
            and all(place in rows for place in wanted.get("places") or {}))


# --gate-missing: gates, one after another, every place (places) with no gate row for this key, writing each report
# into `folder`, unless nothing is missing (nothing_missing: then it only notes the run for health.native_judge). Keeps
# what it wanted in enabled.json (native_judge.record_wanted, for health.native_judge). One run at a time: a second
# exits at once. The key is this process's (native_judge.process_key), taken at its start: when the key on disk isn't
# that already (the checkout changed as it started), it gates nothing, and once it changes (checked before each
# place: a run can take hours) it gates no more. The hosts' places go first, this Mac's last; a gate that raises is
# reported, and one that found no room keeps its matched games for the next run (gate), and the rest still play. 0
# when every gate it ran passed and none was left for a changed key.
def gate_missing(bots: list[Path], folder: Path, workers: int) -> int:
    import fcntl

    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(folder / "gate-missing.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another --gate-missing run is gating", flush=True)
            return 0
        home, gated = remote.loop_home(), native_judge.process_key()
        if gated is None:
            print("no key (no toolkit or engine wasm): nothing to gate", flush=True)
            return 1
        if native_judge.key() != gated:
            print(f"the key on disk isn't this process's {gated}: nothing gated; the next run gates it", flush=True)
            return 1
        if nothing_missing(gated):
            wanted = native_judge.gates(home)["wanted"]
            native_judge.record_wanted(home, gated, list(wanted["places"]), wanted["hosts"], wanted["listed"])
            print(f"every place the loop plays at has a gate for {gated}", flush=True)
            return 0
        wanted, hosts = places()
        native_judge.record_wanted(home, gated, list(wanted), hosts)
        rows = (native_judge.gates(home).get("keys") or {}).get(gated) or {}
        failed = 0
        # The hosts' places first: they have room beside an evaluation, which this Mac, over its load share for hours
        # on 2026-09-29, may not; its no-room then ended the run before any host was gated.
        for place, host in sorted(wanted.items(), key=lambda item: item[1] is None):
            if native_judge.key() != gated:  # the checkout changed while an earlier place was gated: the next run's
                print(f"the key on disk changed from {gated}: the rest are left to the next run", flush=True)
                failed += 1
                break
            if place not in rows:
                out = folder / f"gate-{time.strftime('%Y%m%d-%H%M%S')}-{host or 'here'}.json"
                try:
                    passed = gate(bots, host, out, workers, gated)
                except Exception as error:  # one place's failure never stops the others' gates
                    print(f"gate at {place} ({host or 'here'}) failed to run: {type(error).__name__}: {error}",
                          file=sys.stderr, flush=True)
                    passed = False
                failed += not passed  # None, no room: that place's matched games are kept for the next hourly run
        return 1 if failed else 0


def write(out: Path, report: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1) + "\n")


def main() -> int:
    os.environ.setdefault("UNSWBC_NO_UPDATE", "1")  # as every game launch the loop makes: no update check or
    os.environ.setdefault("UNSWBC_NO_VSCODE", "1")  # replay-viewer install from the toolkit in either way's games
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bots", nargs="+", type=Path, required=True)
    parser.add_argument("--maps", nargs="+", default=[])
    parser.add_argument("--out", type=Path, help="the report (with --gate-missing: the folder for each gate's)")
    parser.add_argument("--pairs", choices=("first", "all", "rotate"), default="first")
    parser.add_argument("--gate", action="store_true", help="play the fixed sample and record the verdict")
    parser.add_argument("--gate-missing", action="store_true", help="gate every place this key has no gate for")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--host")
    parser.add_argument("--speed", type=int, default=0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.out is None:
        parser.error("--out is required")
    # No game plays fork_server.py's check (it would play today's path both ways here): every game forks, both ways.
    run.FORK_CHECK_EVERY = 1 << 62
    if reason := native_judge.unsupported():
        print(f"the native judge can't play here: {reason}", file=sys.stderr)
        return 1
    bots = [bot.resolve() for bot in args.bots]
    if args.gate_missing:
        return gate_missing(bots, args.out, args.workers)
    if args.gate:
        return 0 if gate(bots, args.host, args.out, args.workers) else 1
    jobs = jobs_for(bots, expand_maps(args.maps), args.pairs)[:args.limit]
    with tempfile.TemporaryDirectory() as scratch:
        player = Player(bots, args.host, Path(scratch), args.workers)
        report = play_all(player, jobs, args.workers)
        if args.speed:
            report["speed"] = speed(player, jobs, args.speed)
    write(args.out, report)
    s = report["summary"]
    print(f"{s['games']} games: {s['match']} match, {s['mismatch']} mismatch, {s['starved']} starved -> {args.out}")
    return 0 if s["mismatch"] == 0 and s["games"] else 1


if __name__ == "__main__":
    sys.exit(main())
