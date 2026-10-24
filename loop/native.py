"""Native-agent session commands. Agents repeat claim/quick/commit/evaluate; this is not a model daemon.

uv run loop/native.py claim 1
uv run loop/native.py quick E0024
uv run loop/native.py evaluate E0024 'change and verified checks'
uv run loop/native.py judged E0024      # after writing the verdict.json that evaluate's `judge` task asks for
                                        # (loop/JUDGE.md, in its judge_folder; `judge E0024` makes it again): applies
                                        # it and returns the post-mortem or follow-on
uv run loop/native.py followups E0024   # after doing the post-mortem (a result not kept) or follow-on (kept) that
                                        # evaluate returns, including a quick fix committed in the clone
                                        # (driver.quick_fix_idea). A follow-on adds its reasons after the id:
                                        # followups E0024 'reachPriorSteps coefficient of <formula>; ...'
                                        # evaluate can instead return resolve_required: rebase as its prompt says,
                                        # then run the returned evaluate again (a second conflict is stale)
uv run loop/native.py audited FOLDER   # after doing the replay audit that claim returns as audit_required
.venv/bin/python loop/native.py games E0024 --a BOT --b BOT --maps MAP... --out FILE  # a coder's own test games
                                        # (games review ...: a review's rule counts, loop/rulecounts.py), here and
                                        # on the hosts whatever the options (on_the_hosts); --same-platform keeps
                                        # them to machines that play move for move as this Mac
Defaults (options before the action): --workers auto --elo1 15 --max-pairs 2400, on fresh generated maps.
Auto runs games here at nice 19 (20 beside an evaluation; a worker's at its inherited 10, worker_niced) in the cores
normal-priority work leaves idle, none while
that work or the 1-minute load average reaches 0.7 × cores (half for the players beside an evaluation, coders' games
included: players_full; ~/.bc-loop/load-fraction replaces 0.7 with any share from
0.1 to 0.9 while sig sets one; the evaluation's CPU gate and identical check keep normal priority and that share less
other load: game_slots), and none while memory is short or, since MISSES 67, while this user's process limit has no
room for one more game (game_slots), starting each as a slot frees, plus games on the SSH hosts in
~/.bc-loop/remote.json; --workers N caps local games at N and stays local (a coder's `games` and `play` of run.py,
cpu.py or identical.py keep the hosts, N capping this Mac: on_the_hosts). Gates always apply.
The internal `play` action also gives the Claude driver's frozen referee the same resource gates.
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import fcntl
import json
import math
import os
import re
import shlex
import runpy
import signal
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import asdict
from pathlib import Path

import driver
import meta
import rulecounts
# evaluation_running: whether an evaluation holds the lock now (driver.EVALUATING, readable from a coder's sandbox);
# evaluation_needs_hosts: whether it also plays on the remote hosts now (driver.host_stage); live_marker: its marker,
# and marker_needs_hosts the same question of a marker already read; MAC_HOSTS its value in the holder's CPU gates
from home import (CPU, GAME_NICE, GAME_PROCESSES, MAC_HOSTS, PROCESS_RESERVE, SESSION_SHARE, WORKER_NICE, YIELD_NICE,
                  evaluation_needs_hosts, evaluation_running, game_room, host_key, host_runs, live_marker,
                  load_fraction_copy, load_share,
                  mac_slots, marker_needs_hosts, niced_slots, process_limit, reclaimable_gb, sessions_full,
                  user_processes)

STOP = driver.STOP
SCRIPT = Path(__file__).resolve()


LOAD_FRACTION = STOP.parent / "load-fraction"
# Its copy where coders' sandboxes can read it (they can't read the loop home), like the evaluation marker.
LOAD_FRACTION_COPY = load_fraction_copy()


CODER_FRACTION = 0.5  # a coder's own test games (`games`, quick.py's) while an evaluation runs: evaluations first
_coder = False  # set by yield_to_evaluations: this process plays yielding games, whose share follows the marker
_role = ""  # set by yield_to_evaluations: "tune", "screen" or "idle" for a tune's or a filler's games (host_share)
_spare = False  # set by yield_to_evaluations: an idle filler playing at the spare share (BC_YIELD=spare, spare_share)
_niced = False  # set by lower_games or worker_niced: this process's games here run niced (game_slots)
_split_base: tuple = (None, 0)  # (when the CPU split game_slots last read was measured, this process's games here then)
_lowered: list = []  # the referee module lower_games lowered, so a later yield_to_evaluations lowers it to YIELD_NICE
_evaluating_seen = (0.0, False, False, None, None)  # _marker_now's (checked at, evaluating_now, hosts, lane, runs)
_lane: int | None = None  # set by yield_to_evaluations: a coder's lane (coder_lane), None for the review's games
_tuning_seen = (0.0, False)


def load_fraction(base: float | None = None) -> float:
    """The share of this Mac's cores the loop may load (base_share, or `base` as it just read), except that yielding
    games (yield_to_evaluations) get at most CODER_FRACTION while an evaluation runs, and an idle filler's also while
    a tune player or a screen filler asks for slots (half_asking_now, as on the hosts: host_share)."""
    share = base_share() if base is None else base
    return min(share, CODER_FRACTION) if _coder and (evaluating_now() or _role == "idle" and half_asking_now()) \
        else share


# The share of this Mac's cores the loop may load: 0.7, unless sig sets another share for a while by writing a number
# from 0.1 to 0.9 to ~/.bc-loop/load-fraction (home.load_share clamps it; read on every check; delete the file to go
# back).
def base_share() -> float:
    try:
        text = LOAD_FRACTION.read_text()
    except PermissionError:  # a coder's sandbox: the upkeep keeps a copy in sync (maintenance.mirror_load_fraction)
        try:
            text = LOAD_FRACTION_COPY.read_text()
        except OSError:
            text = ""
    except OSError:  # no file: sig's own share has ended
        text = ""
    return load_share(text)


# evaluation_running, re-read at most every 10 seconds: the scheduler asks before every game. This Mac yields to an
# evaluation for as long as it holds the lock (its CPU gate plays here too).
def evaluating_now() -> bool:
    return _marker_now()[1]


# home.evaluation_needs_hosts, read with evaluating_now: whether the evaluation holding the lock plays on the remote
# hosts now, so the players beside it take their evaluation shares there (yield_to_evaluations). In its CPU gates
# (home.MAC_HOSTS) it plays only on hosts of this Mac's platform: true for `host` only when it is one, as far as its
# platform is known yet (a host not measured yet has no room anyway, remote.Host.capacity).
def hosts_wanted_now(host=None) -> bool:
    wanted = _marker_now()[2]
    if wanted == MAC_HOSTS:
        return host is not None and host.of_this_platform()
    return wanted is True


# The lane of the candidate the evaluation holding the lock tests (the marker's "lane", driver.evaluation_lock), read
# with evaluating_now; None with no evaluation or one of no lane's (a referee refresh, a promotion's recovery).
def holder_lane() -> int | None:
    lane = _marker_now()[3]
    return lane if isinstance(lane, int) and not isinstance(lane, bool) and lane > 0 else None


# The host list the evaluation holding the lock wrote into its marker ("runs": home.host_runs), which every player
# beside it rations from (host_offset), read with evaluating_now; with none (no evaluation, or a marker from older
# code) this process's own `remote.load_hosts()`.
def host_runs_now(remote) -> list:
    runs = _marker_now()[4]
    return runs if isinstance(runs, list) else host_runs(remote.load_hosts())


# All four from one read of the marker, so at a switch between evaluations none mixes one holder's "hosts" with the
# next one's "lane". Its hosts: True (all of them), MAC_HOSTS or False.
def _marker_now() -> tuple[float, bool, bool | str, object, object]:
    global _evaluating_seen
    if time.monotonic() - _evaluating_seen[0] > 10:
        marker = live_marker() or {}
        hosts = MAC_HOSTS if marker.get("hosts") == MAC_HOSTS else marker_needs_hosts(marker or None)
        _evaluating_seen = (time.monotonic(), bool(marker), hosts, marker.get("lane"), marker.get("runs"))
    return _evaluating_seen


# Whether a tune player is playing, re-read at most every 10 seconds like evaluating_now: a claimed tune's SPSA games
# (driver.tuning_count) or a live process in TUNE_PLAYERS, such as the evidence refresh after a promotion, which the
# backlog doesn't show. The screen and idle fillers step back for either (host_share).
def tuning_now() -> bool:
    global _tuning_seen
    checked, value = _tuning_seen
    if time.monotonic() - checked > 10:
        try:
            value = driver.tuning_count(driver.read_ideas()) > 0
        except (OSError, ValueError):
            value = False
        value = value or bool(filler_names(TUNE_PLAYERS))
        _tuning_seen = (time.monotonic(), value)
    return _tuning_seen[1]


# Whether a tune player asks for game slots now: one whose scheduler looked for room (game_slots, a host's capacity)
# or whose round is being sized (a host's measure) within ASKING_S, as its registration file's mtime says (want_slots).
# A tune plays in rounds and between them, and in each round's tail, it asks for nothing: the screen and idle fillers
# then take its share (host_share's `tuning`), and give it back within a game once it asks again (2026-09-27,
# 20:47-21:03: E0180's rounds filled its half of every host for about a minute, then held 0-3 games for one to two
# while the fillers, capped at the other half, never grew into it). Re-read at most every ASKING_CHECK_S.
ASKING_S = 20
ASKING_CHECK_S = 3
MARK_S = 5  # a tune player or idle filler marks itself asking at most this often
_asking_seen = (-ASKING_CHECK_S, False)
_fillers_asking_seen = (-ASKING_CHECK_S, False)
_screen_asking_seen = (-ASKING_CHECK_S, False)
_marked_at = -MARK_S


# Whether a live player registered in `folder` marked itself asking within ASKING_S (want_slots).
def asked_lately(folder: Path) -> bool:
    return any(asked_within(folder / name) for name in filler_names(folder))


# Whether the player registered as `path` marked itself asking within ASKING_S (want_slots touches its file).
def asked_within(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < ASKING_S
    except OSError:
        return False


def tune_asking_now() -> bool:
    global _asking_seen
    if time.monotonic() - _asking_seen[0] > ASKING_CHECK_S:
        _asking_seen = (time.monotonic(), asked_lately(TUNE_PLAYERS))
    return _asking_seen[1]


# The same for the idle fillers at their usual share (IDLE_FILLERS): the spare share gives way while one asks.
def fillers_asking_now() -> bool:
    global _fillers_asking_seen
    if time.monotonic() - _fillers_asking_seen[0] > ASKING_CHECK_S:
        _fillers_asking_seen = (time.monotonic(), asked_lately(IDLE_FILLERS))
    return _fillers_asking_seen[1]


# The same for the screen fillers (FILLERS: the late screen, an official-map report). With no evaluation on a host the
# idle fillers leave one its half while it asks, as they do a tune's (host_share's `tuning`), and take the whole host
# in its tails and between its entries. Until 2026-09-29 the sweep and the adversarial-map search didn't start at all
# while a late screen ran, which left the other half of every host idle (MISSES 50).
def screen_asking_now() -> bool:
    global _screen_asking_seen
    if time.monotonic() - _screen_asking_seen[0] > ASKING_CHECK_S:
        _screen_asking_seen = (time.monotonic(), asked_lately(FILLERS))
    return _screen_asking_seen[1]


# Whether a tune player or a screen filler asks for slots now: the idle fillers then keep to half a host (host_share).
def half_asking_now() -> bool:
    return tune_asking_now() or screen_asking_now()


# Marks this tune player, idle filler or screen filler as asking for slots (tune_asking_now, fillers_asking_now,
# screen_asking_now): touches its registration file, at most every MARK_S. A spare-share filler isn't registered, so
# it never asks.
def want_slots() -> None:
    global _marked_at
    if _role not in ("tune", "idle", "screen") or _filler_lock is None or time.monotonic() - _marked_at < MARK_S:
        return
    _marked_at = time.monotonic()
    with contextlib.suppress(OSError):
        os.utime((TUNE_PLAYERS if _role == "tune" else IDLE_FILLERS if _role == "idle" else FILLERS) / _filler_lock[1])


# The idle fillers' games on each remote host now, from their live status files (run.Scheduler.save rewrites each at
# least every 10 s while it plays): what a tune's round may count on getting back there (yield_to_evaluations' room),
# and what the spare share leaves the others (spare_share; `besides` its own file).
IDLE_STATUS = ("remote-status-sweep.json", "remote-status-prewarm.json", "remote-status-advmaps.json")
IDLE_STATUS_S = 30


def idle_games(besides: str = "") -> dict[str, int]:
    games: dict[str, int] = {}
    for name in IDLE_STATUS:
        if name == besides:  # a filler's own status file (spare_share)
            continue
        try:
            status = json.loads((driver.STATE / name).read_text())
            if time.time() - float(status["updated"]) > IDLE_STATUS_S:
                continue
            active = status.get("active") or {}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for host, count in active.items() if isinstance(active, dict) else ():
            if isinstance(count, int) and 0 < count < 1000:
                games[host] = games.get(host, 0) + count
    return games


_last_wait_note = -60.0


def game_slots(requested: int | str, active: int = 0) -> int:
    """How many games this Mac may run now, counting the `active` games we already run here (their load is
    ours, not other work). 0 means start none yet; the referee's scheduler asks again as games finish.
    Games at GAME_NICE (`_niced`: every loop game here but the evaluation's CPU gate and identical check and a
    queued candidate's prepared CPU gate, lower_games) fill the cores dev work (whatever runs at normal priority) leaves idle, and start none while dev
    work reaches the load share, memory is short (home.niced_slots) or the 1-minute load average reaches the gate
    sig's other sessions use (home.sessions_full). The evaluation's own niced games come first: they leave room
    only for the other niced games at GAME_NICE, not the yielding players' at YIELD_NICE, which step back as the
    evaluation's grow; beside an evaluation the yielding players together keep to CODER_FRACTION of this Mac's cores
    (mac_half), and all of them, coders included, start none once the 1-minute load reaches that (players_full). The
    games at normal priority keep the load gate, counting dev work, not the niced games, as other load, and a
    prepared CPU gate at most mac_cap games. Where the
    split can't be read (home.CpuSplit), both keep the 1-minute load average, as before 2026-09-27, and niced games
    also sig's sessions' gate. Every game here also waits while this user's process limit (kern.maxprocperuid) has
    no room for one more, each live game charged at home.GAME_PROCESSES and home.PROCESS_RESERVE kept free, and a pass
    starts at most as many as fit (home.game_room), so a fork of sig's, a hook's or a loop step's isn't refused (28 Sep
    01:17-01:41). Where the process table or the limit can't be read, that gate is off (it fails open, and
    health.process_limit alerts after home.UNREAD_ALERT_S of it)."""
    if _role == "idle" and (evaluating_now() or _spare and fillers_asking_now()):  # idle machines only (host_share)
        return 0
    want_slots()  # the scheduler asks this before every start: a tune player with a game to start asks for slots
    cores = os.cpu_count() or 1
    base = base_share()
    share, memory_gb = load_fraction(base), reclaimable_gb()
    split = CPU.read()
    if split is not None and _niced:
        global _split_base
        if _split_base[0] != CPU.at:  # a new measurement: this process's games here when it was taken
            _split_base = (CPU.at, active)
        others = split.niced if _coder else split.niced - split.yielding
        cap = mac_half(cores) - split.yielding if _coder and evaluating_now() else math.inf
        slots = niced_slots(share, cores, split.dev, others, _split_base[1], memory_gb, cap)
        why = f"dev load {split.dev:.2f}/{share * cores:.1f}"
    else:
        load = os.getloadavg()[0] if split is None else split.dev
        slots = mac_slots(share, cores, load, active, memory_gb)
        why = f"other load {max(0.0, load - active):.2f}/{share * cores:.1f}"
    if slots and (_niced or _coder and evaluating_now()):
        if split is not None:  # else `load` is the load average already
            load = os.getloadavg()[0]
        if _coder and evaluating_now() and players_full(load, base, cores):
            slots, why = 0, (f"1-minute load {load:.2f} reaches {min(base, CODER_FRACTION) * cores:.1f}, the players' "
                             f"part of this Mac beside an evaluation")
        elif _niced and sessions_full(load, base, cores):
            slots, why = 0, (f"1-minute load {load:.2f} reaches {min(base, SESSION_SHARE) * cores:.1f}, where sig's "
                             f"other sessions stop starting work")
    room = game_room(active) if slots else None  # every game here, at any priority: the fork limit is the user's
    if room == 0:
        slots, why = 0, (f"{user_processes()} of this user's processes, with each game at {GAME_PROCESSES} and "
                         f"{PROCESS_RESERVE} kept free, leave no room for a game under its limit {process_limit()} "
                         f"(home.game_room)")
    if not slots:
        global _last_wait_note
        if active == 0 and time.monotonic() - _last_wait_note >= 60:  # the scheduler asks per game; say it once a minute
            _last_wait_note = time.monotonic()
            print(f"waiting: {why}, reclaimable {memory_gb:.2f} GiB", file=sys.stderr, flush=True)
        return 0
    cap = math.inf if room is None else active + room  # a pass starts no more games than the limit has room for
    cap = min(cap, mac_cap())
    if requested == "auto":
        return min(slots, cap)
    return min(max(1, min(cores, int(requested))), cap)


# BC_MAC_CAP (driver.referee's `mac_cap`): the most games this process may run here at once, whatever its gate
# allows, for driver.prepare's CPU gate, which plays at normal priority beside another evaluation and keeps within
# mac_half. Unset or unreadable, no cap.
def mac_cap() -> float:
    try:
        return max(1, int(os.environ["BC_MAC_CAP"]))
    except (KeyError, ValueError):
        return math.inf


# The most cores of this Mac the yielding players' niced games may keep busy together beside an evaluation (CLAUDE.md:
# at most half of this Mac), so its own games there come first.
def mac_half(cores: int) -> int:
    return math.floor(CODER_FRACTION * cores)


# Whether the 1-minute load average `load` stops the yielding players (yield_to_evaluations: coders, a review, a tune,
# the fillers) starting work on a Mac of `cores` beside an evaluation: at CODER_FRACTION x cores, or
# the loop's `base` share if lower. The split can't count coders' games as yielding (a coder's sandbox refuses a lower
# priority, so they play at the home.WORKER_NICE they inherit, not YIELD_NICE), but the load counts every game here,
# coders' and the evaluation's: so the players together stop at half, and the band from there to
# home.SESSION_SHARE, where niced games stop (home.sessions_full), is left to the evaluation's own games.
def players_full(load: float, base: float, cores: int) -> bool:
    return load >= min(base, CODER_FRACTION) * cores


# Waits until dev work (the cores busy above WORKER_NICE: CPU.read's split, or the 1-minute load where none is
# published) is under the loop's load share of this Mac, the gate tools/gated_test.sh keeps, for driver.prepare's bot
# tests: the next evaluation's own checks, run beside the one playing below sig's work and not as a player's, whose
# half of this Mac coders' workers fill (28 Sep: all 20 prepares logged 02:07-18:46 stopped before their bot tests
# there, E0251's after 2.5 h). Stops waiting when `going()` turns false, returning False.
def wait_for_dev(going) -> bool:
    cores = os.cpu_count() or 1
    while going():
        split = CPU.read()
        if (os.getloadavg()[0] if split is None else split.dev) < base_share() * cores:
            return True
        time.sleep(5)
    return False


# Plays this process's games here at GAME_NICE, or YIELD_NICE for a yielding player's (run.GAME_NICE, through
# run.nice_command's /usr/bin/nice), and gates them as niced games (game_slots). Every loop game but the evaluation's
# own local stages (the CPU gate and the identical check, on the critical path while every host waits: play_script
# leaves them at normal priority). A referee checkout from before niced games (no run.nice_command) keeps them at
# normal priority, as does a sandbox that refuses a lower priority (/usr/bin/nice then warns and runs the command
# as it was, so the probe reads the priority it got). A headless worker's (a coder's, at home.WORKER_NICE through
# meta.private_tmp, which its sandbox can't lower further) are niced games all the same, at the niceness they inherit:
# the split counts their CPU as niced, not dev work, so they are gated as niced games, or each coder's would count
# only its own games as its load and fill this Mac.
def lower_games(run) -> None:
    global _niced
    _niced = _niced or worker_niced()
    if not hasattr(run, "nice_command"):
        return
    nice = YIELD_NICE if _coder else GAME_NICE
    # os.getpriority, not os.nice(0): at niceness 20 macOS refuses nice(0) (it sets the priority it reads).
    probe = run.nice_command([sys.executable, "-c", "import os; print(os.getpriority(os.PRIO_PROCESS, 0))"], nice)
    try:
        got = int(subprocess.run(probe, capture_output=True, text=True, check=True, timeout=30).stdout.split()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return
    if got < min(YIELD_NICE, os.getpriority(os.PRIO_PROCESS, 0) + nice):
        if got >= WORKER_NICE:  # refused, but already a worker's: no wrapper, the niceness it inherits
            _niced = True
        return
    _niced = True
    run.GAME_NICE = nice
    _lowered.append(run)


# Whether this process already runs at home.WORKER_NICE or lower priority (a headless worker's command, through
# meta.private_tmp): its games inherit that niceness whatever the script, so the split counts their CPU as niced, not
# dev, and they must be gated as niced games (game_slots). A coder's own `play referee/cpu.py` or `identical.py`
# (NORMAL_PRIORITY, which lower_games skips) was gated by the dev load alone, which leaves its games out: at dev 0.5
# of 10 cores it started 7-8 beside a 1-minute load of 22.
def worker_niced() -> bool:
    return os.getpriority(os.PRIO_PROCESS, 0) >= WORKER_NICE


# Runs `script` under the load gate with the working folder's referee/, whichever checkout the script is in: a tune
# (driver.run_tuner) and the sweep run the main checkout's script in ~/.bc-loop/referee, the pinned referee. The gate
# is this Mac's, so it applies to what plays here: the scheduler asks game_slots before every game or check it would
# start here and starts the rest on the hosts meanwhile, and with no host usable every game waits here, one by one.
# So a batch doesn't wait for this Mac before it starts (MISSES 53): a separate process counts an SPRT's local games
# as other load, so that wait lasted as long as the SPRT filled this Mac, with the hosts idle for it (quick.py's,
# MISSES 49). A tune's and a sweep's variant builds here (~1 s each) don't wait either. Its games here run at
# GAME_NICE (lower_games) unless it is one of NORMAL_PRIORITY run without BC_YIELD: the evaluation's own CPU gate and
# identical check (driver.evaluate, check_speed), on the critical path, and a queued candidate's prepared CPU gate
# (driver.prepared_cpu_gate, capped by BC_MAC_CAP here and on the hosts rationed by BC_BESIDE_LANE: ration_hosts), the
# next evaluation's own. The CPU gates (referee/cpu.py --hosts) also play on the hosts of this Mac's platform.
NORMAL_PRIORITY = ("cpu.py", "identical.py")


def play_script(script: str, args: list[str]) -> None:
    global _niced
    # A script that imports native (loop/quick.py, for its pair_games) gets this running module, whose game_slots the
    # scheduler calls below, not a second copy whose yielding settings this Mac's gate would never see.
    sys.modules.setdefault("native", sys.modules[__name__])
    # SIGTERM (driver.stoppable) unwinds as an exit, so the referee's scheduler kills its games (Scheduler.stop), which
    # lead process groups of their own and would otherwise play on. Only a main thread may set it (tests call this
    # from others).
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.path.insert(0, str(Path.cwd() / "referee"))
    import run
    if os.environ.get("BC_YIELD") in ROLES:  # driver.referee(..., yielding=...): a tune's or a filler's games
        import remote
        yield_to_evaluations(remote, os.environ["BC_YIELD"])
    if lane := os.environ.get("BC_BESIDE_LANE"):  # driver.referee(..., lane=...): a queued candidate's CPU gate
        import remote
        ration_hosts(remote, int(lane))
    # driver.referee(..., status=...): a filler's or tune's own host status file, so the evaluation's
    # <loop home>/remote-status.json, which health.py reads, isn't overwritten.
    if status := os.environ.get("BC_REMOTE_STATUS"):
        import remote
        remote.STATUS = Path(status)
    run.game_slots, run.fork_room = game_slots, game_room
    if os.environ.get("BC_YIELD") or Path(script).name not in NORMAL_PRIORITY:
        lower_games(run)
    elif worker_niced():  # a worker's own CPU gate or identical check: normal priority isn't its to keep
        _niced = True
    if (clone := os.environ.get(CODER_CLONE)) and not os.environ.get("BC_YIELD") and not lane:
        import remote
        yield_to_evaluations(remote, lane=coder_lane(Path(clone)))  # a coder's own run.py, cpu.py or identical.py
        if not status:
            remote.STATUS = coder_status(Path(clone).name)
        args = on_the_hosts(run, remote, args, Path(script).name)
    sys.argv = [script, *args]
    if Path(script).resolve() == Path(run.__file__).resolve():
        raise SystemExit(run.main())
    runpy.run_path(script, run_name="__main__")


# The players that yield to an evaluation at once, each at most its evaluation_shares of a host while it plays there
# (driver.host_stage): the coding lanes, a review's rule-count games (loop/rulecounts.py, `games review`), and one share
# that the tune players and the fillers take turns with: while a tune player plays (a tune's SPSA games, or the
# evidence refresh after a promotion, loop/evidence.py: TUNE_PLAYERS) they split it host by host; otherwise the
# screen fillers (the late screen, official-map reports: FILLERS) do. Together that is at most half of every host of
# 8+ cores (evaluation_shares, which rations it among the coders, the review and the share by coder_slot and share_slot
# where it is under a game each); smaller hosts keep a floor of one game for each coder, the review and the tune
# players. A post-mortem's or follow-on's coder (lane 0, driver.retire_lane) has no place in the ration.
# The idle fillers (the sweep, the prewarm and the adversarial-map search) play nothing on the hosts while an evaluation
# plays there, nor on this Mac while one holds the lock: they only use idle machines.
YIELDING = driver.CODING + 2
FILLERS = driver.STATE / "fillers"  # a file per screen filler process while it plays (register_filler)
IDLE_FILLERS = driver.STATE / "fillers-idle"  # the same for the idle fillers but a spare one, which split their share
TUNE_PLAYERS = driver.STATE / "fillers-tune"  # the same for the tune players, which split the tune share
# (each folder's bound and its live-lock check: health.filler_registries)
_filler_lock = None  # this process's pid file, held locked for its whole life
TMP_AGE = 60  # a registering filler's .tmp file older than this, and unlocked, was left by one killed mid-registering
_fillers_seen = (0.0, 0, (0, 1))  # (checked at, FILLERS' mtime, (rank, count))


# Registers this process as a live filler until it dies: FILLERS/<start ns>-<pid>, locked (flock) for as long as the
# process lives, so a counter probing the lock sees it live and a SIGKILL releases it. A unique name, so a reused pid
# never meets an old file.
def register_filler(kind: str, folder: Path | None = None) -> None:
    global _filler_lock
    if _filler_lock is not None:  # once a process: a second file would count this process twice
        return
    folder = folder or FILLERS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.time_ns()}-{os.getpid()}"
    temporary = path.with_name(path.name + ".tmp")  # locked before it is visible, so no counter takes it for dead
    handle = temporary.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    handle.write(json.dumps({"kind": kind, "pid": os.getpid()}) + "\n")
    handle.flush()
    temporary.rename(path)  # the same file, still locked
    _filler_lock = (handle, path.name)
    atexit.register(handle.close)  # runs after the unlink below (last-first), before interpreter teardown
    atexit.register(path.unlink, missing_ok=True)


# The live fillers' names in `folder` (FILLERS unless given), oldest first: a file whose lock can be taken belongs to a
# process that died, and is removed. So is a `.tmp` one (a filler killed before it renamed its file into view) over
# TMP_AGE old and unlocked; a younger one may be a filler registering now.
def filler_names(folder: Path | None = None) -> list[str]:
    folder = folder or FILLERS
    live = []
    for path in sorted(folder.glob("*")) if folder.exists() else []:
        if _filler_lock is not None and path.name == _filler_lock[1]:
            live.append(path.name)
            continue
        try:
            if path.suffix == ".tmp" and time.time() - path.stat().st_mtime < TMP_AGE:
                continue
            with path.open("r+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if path.suffix != ".tmp":
                        live.append(path.name)
                    continue
                path.unlink(missing_ok=True)
        except OSError:
            continue
    return live


# This process's (rank, count) among the live players of its kind (IDLE_FILLERS for an idle filler, TUNE_PLAYERS for a
# tune player, else FILLERS), re-counted when one registers or leaves (the folder's mtime changes) and at least every 10
# seconds. The idle fillers count only those that asked for slots within ASKING_S (want_slots), and this one: one
# between its referee calls (its next batch's setup, a call's last games, or its maps being made) asks for none, and
# its part of a host would sit idle while the others were held to theirs. Until 2026-09-30 every live one counted: 30
# Sep 02:54-03:02 the prewarm held 36 of 72 slots while the sweep alternated about 34 and 2 of the other 36
# (utilisation.jsonl; MISSES 50). One that asks again gets its part back as the others' games there end.
def filler_rank() -> tuple[int, int]:
    global _fillers_seen
    folder = {"idle": IDLE_FILLERS, "tune": TUNE_PLAYERS}.get(_role, FILLERS)
    checked, mtime, seen = _fillers_seen
    try:
        now_mtime = folder.stat().st_mtime_ns
    except OSError:
        now_mtime = 0
    if now_mtime != mtime or time.monotonic() - checked > 10:
        names = filler_names(folder)
        mine = _filler_lock[1] if _filler_lock is not None else ""
        if _role == "idle":
            names = [name for name in names if name == mine or asked_within(folder / name)]
        seen = (names.index(mine) if mine in names else 0, max(1, len(names)))
        _fillers_seen = (time.monotonic(), now_mtime, seen)
    return seen


# Filler `rank` of `count`'s part of a host's `share` of whole games: share // count each, and the remainder to the
# fillers whose rank, rotated by the host's `offset`, comes first. Summed over the fillers it is `share` exactly, so on a
# host with one filler game and three fillers one of them plays it, not none.
def filler_part(share: int, rank: int, count: int, offset: int) -> int:
    return share // count + ((rank - offset) % count < share % count)


# What the players beside an evaluation may have of a host with `cores` while it plays there (driver.host_stage), as
# (a coder's or the review's, the one share the tune players or else the screen fillers split): together at most half
# of every host of 8+ cores (LOOP.md: Concurrency and host shares). From 2 * YIELDING cores each of the YIELDING players
# gets cores // (2 * slots) (slots at least YIELDING). Below, where that is 0 (8+ cores under 2 * YIELDING), the half
# is rationed like the fillers' share (filler_part): a game to each `slot` of `slots` (coder_slot for a coder or the
# review, share_slot for the share, the last slot) whose place, rotated by the host's `offset` (host_offset), comes
# first, so on 10 cores 5 of the YIELDING play, the next host's run starting where this one's ended. `slot` None: the
# most any one of them may have (health.py's check); NO_SLOT (a post-mortem's or follow-on's coder, lane 0): none on
# 8+ cores, whose half the others fill. Under 8 cores (the 2-core az-* VMs) a floor of one game each for the coders,
# the review and the tune players' share, and none for the screen fillers (host_share); the host's own load check
# (remote.Host.capacity) caps what starts there.
def evaluation_shares(cores: int, slot: int | None = None, slots: int = YIELDING, offset: int = 0) -> tuple[int, int]:
    if cores < 8:
        return 1, 1
    slots = max(slots, YIELDING)
    if one := cores // (2 * slots):
        return (0 if slot == NO_SLOT else one), one
    if slot is None:
        return 1, 1
    half = cores // 2
    mine = 0 if slot == NO_SLOT else filler_part(half, slot, slots, offset)
    return mine, filler_part(half, slots - 1, slots, offset)


# Where host `name`'s run of games starts among a ration's `slots` (evaluation_shares' `offset`): the hosts of `runs`
# (host_runs_now: the evaluation marker's list) that ration their half a game a slot (8+ cores, under 2 * slots) laid
# end to end round the slots, in home.host_runs' order (always-on first). So the always-on hosts' halves (20 games on
# mm, mm1, ll and sz-wsl, 2026-09-29) go round every slot before any slot has two, and every player plays on some
# always-on host whichever lane is under test; a crc32 of each host's name had left one of seven coders none on all
# four. Cores come from each entry's "max", and the list from the marker, so every process agrees on the offsets,
# before it has measured a host and whenever it read remote.json, and the half stays exact; a host's measured cores
# still size its own run. A host not listed: 0.
def host_offset(name: str, runs: list, slots: int) -> int:
    offset, slots, key = 0, max(slots, YIELDING), host_key(name)
    for run in runs:
        if not (isinstance(run, list) and len(run) == 2 and isinstance(run[1], int)):
            continue
        if run[0] == key:
            return offset
        if 8 <= run[1] < 2 * slots:
            offset += run[1] // 2
    return 0


# The most the coders and the review together may have of a host with `cores` beside an evaluation playing there:
# CODING + 1 players at their evaluation_shares, which ration the half where it is under a game each (health.py's
# check).
def coders_share(cores: int) -> int:
    players = driver.CODING + 1
    if cores < 8:
        return players
    return min(players * max(1, cores // (2 * YIELDING)), cores // 2)


# The games a yielding process may have on a host with `cores` logical CPUs, by its `role`: "" a coder or the review's
# rule counts, "tune" (a tune or the evidence refresh), "screen" (the late screen, an official-map report: a filler that
# plays beside evaluations) or "idle" (the sweep, the prewarm, the adversarial-map search). While an evaluation plays on
# the hosts (`evaluating`: hosts_wanted_now, so not in its stages that play only on this Mac, driver.host_stage, and in
# its CPU gates only on hosts of this Mac's platform): a
# coder and the review their evaluation_shares by their `slot` of `slots` (coder_slot; none given, the most one may
# have); the tune players (`rank` of `count`) each their `filler_part` of the one share (its `slot` of `slots`,
# share_slot; none given, the most it may have), a screen filler its `filler_part` of it unless a tune player plays
# (`tuning`: tuning_now, so the two never hold the share at once) or the host has under 8 cores; an idle filler none.
# With no evaluation, the usual shares: a coder or the review its `filler_part` of the host (`rank` of the `count`
# coders playing there, coder_rank), a game at least, a tune player or screen filler half a host, and the idle
# fillers (`rank` of the `count` asking for slots, filler_rank) together the whole host, or half while a tune player
# or a screen filler asks for slots (`tuning`: half_asking_now; what the sweep and the prewarm had before the third
# came, 2026-09-27), each its
# `filler_part` of it: one left alone (the others done for this champion) takes it all, where capped at half a host it
# left half of every host idle. Between a tune's rounds or a late screen's entries, and in their tails, neither asks,
# and the idle fillers have the whole host. The floor of one game is the fillers' together, so a 2-core host beside a tune plays one of theirs
# and the tune's one.
def host_share(cores: int, evaluating: bool, role: str = "", tuning: bool = False, rank: int = 0, count: int = 1,
               offset: int = 0, slot: int | None = None, slots: int = YIELDING) -> int:
    if evaluating:
        each, share = evaluation_shares(cores, slot, slots, offset)
        if role == "idle":
            return 0
        if role == "tune":
            return filler_part(share, rank, count, offset)
        if role == "screen":
            return 0 if tuning or cores < 8 else filler_part(share, rank, count, offset)
        return each
    if not role:  # a coder's or a review's own games: the coders playing there split the host between them
        return max(1, filler_part(cores, rank, count, offset))
    # A tune and a screen filler take half a host; the idle fillers the whole host, or half beside either asking.
    # Fixed, not from CODING: with more coders (three from 2026-09-26) that would have cut a tune to a third of a host.
    if role == "idle":
        return filler_part(max(1, cores // (4 if tuning else 2) * 2), rank, count, offset)
    return max(1, cores // 2)


# The spare share (BC_YIELD=spare: the adversarial-map search past its first advmaps.GAMES_CAP games) of `host`, whose
# idle-filler share alone is `share` (host_share with no split): nothing while an idle filler at the usual share asks
# for slots (fillers_asking_now), so it gives them back within a game, else what the other idle fillers' games there
# leave of it (idle_games but its own status file `own`, re-read at most every ASKING_CHECK_S). It isn't registered in
# IDLE_FILLERS, so the others split their share as if it weren't there (2026-09-28: it used to wait until the sweep and
# the prewarm had finished, while the prewarm alone left most slots idle).
_others_seen: tuple[float, dict[str, int]] = (-ASKING_CHECK_S, {})


def spare_share(share: int, host: str, own: str) -> int:
    global _others_seen
    if not share or fillers_asking_now():
        return 0
    if time.monotonic() - _others_seen[0] > ASKING_CHECK_S:
        _others_seen = (time.monotonic(), idle_games(own))
    return max(0, share - _others_seen[1].get(host, 0))


# BC_YIELD's values (driver.referee's `yielding`) and the roles they play: "tune" a tune's and the evidence refresh's,
# "filler" and "spare" an idle filler's at its usual share and at the spare share (spare_share).
ROLES = {"tune": "tune", "1": "screen", "filler": "idle", "spare": "idle"}


# Makes this process's games yield to a running evaluation, re-checked before every game (host_share, and this Mac at
# CODER_FRACTION, nothing for an idle filler). For coders' and a review's rule-count games (`flag` ""), a tune
# player's (a tune or the evidence refresh), and the fillers' (driver.referee's `yielding`); a screen filler registers
# in FILLERS while it plays, an idle one at its usual share in IDLE_FILLERS (one at the spare share never does: the
# others split their share as if it weren't there, spare_share), a tune player in TUNE_PLAYERS. `lane`: a coder's
# (coder_lane), None for the review's rule counts; their slot beside an evaluation comes from it (coder_slot).
def yield_to_evaluations(remote, flag: str = "", lane: int | None = None) -> None:
    global _coder, _role, _lane, _spare
    _coder, _role, _lane, _spare = True, ROLES.get(flag, ""), lane, flag == "spare"
    for run in _lowered:  # lowered before this (loop/quick.py yields once play_script has run it): a player's now
        run.GAME_NICE = YIELD_NICE
    if _role == "screen":
        register_filler(flag)
    elif _role == "idle" and not _spare:  # the spare share isn't counted in the others' split (spare_share)
        register_filler(flag, IDLE_FILLERS)
    elif _role == "tune":
        register_filler(flag, TUNE_PLAYERS)
    capacity, measure = remote.Host.capacity, remote.Host.measure

    def share_of(host) -> int:
        evaluating = hosts_wanted_now(host)  # not in the holder's stages that play only on this Mac (driver.host_stage)
        splitting = (_role in ("screen", "tune") and evaluating) or (_role == "idle" and not evaluating and not _spare)
        rank, count = filler_rank() if splitting else (0, 1)
        # Beside an evaluation a screen filler steps back for any playing tune player (tuning_now), so both never hold
        # the one share at once; with none, the idle fillers take a tune's or a screen filler's half while it isn't
        # asking (half_asking_now).
        tuning = _role in ("screen", "idle") and (tuning_now() if evaluating else half_asking_now())
        slot, slots = (None, YIELDING) if not evaluating or _role == "idle" else \
            share_slot(holder_lane()) if _role else coder_slot(_lane, holder_lane())
        # With no evaluation there the coders playing on the host split it (coder_rank), the remainder rotated by a
        # crc32 of its name, which every process agrees on without a host list.
        if not _role and not evaluating:
            (rank, count), offset = coder_rank(host.name, remote.STATUS), zlib.crc32(host.name.encode())
        else:
            offset = host_offset(host.name, host_runs_now(remote), slots)
        share = host_share(host.cores or host.max, evaluating, _role, tuning, rank, count, offset, slot, slots)
        return spare_share(share, host.name, remote.STATUS.name) if _spare else share

    def yielding_capacity(host) -> int:
        want_slots()  # asked only when the scheduler has a game to start
        free = capacity(host)  # 0 until the host's first measurement, which also gives its cores for the share
        return min(free, share_of(host))

    # What a tune's round may count on here (tune.free_slots sizes rounds by it): its room now plus the idle fillers'
    # games there, which step back within a game once it asks (host_share), up to its share (rationed beside an
    # evaluation, share_of). Its capacity alone saw a host the fillers had filled between rounds as full, and would
    # size the round at its smallest.
    def room(host) -> int:
        return min(capacity(host) + idle_games().get(host.name, 0), share_of(host))

    def asking_measure(host) -> int:
        want_slots()  # a tune measures every host to size its next round (tune.free_slots): it asks from then on
        return measure(host)

    remote.Host.capacity = yielding_capacity
    if _role == "tune":
        remote.Host.room, remote.Host.measure = room, asking_measure


_coders_seen: tuple[float, dict[str, set[str]]] = (-ASKING_CHECK_S, {})


# This process's (rank, count) among the coders playing on host `name` with no evaluation there (host_share): itself
# (its status file `own`, remote.STATUS) and the coders_playing there, ranked by experiment id (status_id), re-read at
# most every ASKING_CHECK_S. So at seven coders two playing on a 10-core host have 5 games each, where cores // CODING
# gave each 1 and left 8 idle.
def coder_rank(name: str, own: Path) -> tuple[int, int]:
    global _coders_seen
    if time.monotonic() - _coders_seen[0] > ASKING_CHECK_S:
        _coders_seen = (time.monotonic(), coders_playing())
    players = sorted(_coders_seen[1].get(name, set()) | {status_id(own)})
    return players.index(status_id(own)), len(players)


# The coders with a game on each remote host, {host: their status_ids}: every coder status file (coder_status: the
# loop home's remote-status-E####.json, which a coder's sandbox can't read, and each clone's
# .git/bc-remote-status.json) written within IDLE_STATUS_S, read through meta.worker_file (a clone's is a coder's).
def coders_playing() -> dict[str, set[str]]:
    paths, playing, now = [], {}, time.time()
    for folder, pattern in ((driver.STATE, "remote-status-E*.json"),
                            (driver.WORKTREES, "E*/.git/bc-remote-status.json")):
        with contextlib.suppress(OSError):
            paths += list(folder.glob(pattern))
    for path in paths:
        try:
            if now - path.stat().st_mtime > IDLE_STATUS_S:
                continue
            status = json.loads(meta.worker_file(path))
            active = status.get("active") if now - float(status["updated"]) <= IDLE_STATUS_S else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        for host, count in active.items() if isinstance(active, dict) else ():
            if isinstance(count, int) and count > 0:
                playing.setdefault(host, set()).add(status_id(path))
    return playing


# A status file's experiment id (coder_status's two places), or the rest of its name for another player's.
def status_id(path: Path) -> str:
    return path.parent.parent.name if path.name == "bc-remote-status.json" else path.stem.removeprefix("remote-status-")


NO_SLOT = -1  # coder_slot of lane 0: a recorded experiment's coder (driver.retire_lane), outside the ration


# A queued candidate's prepared CPU gate (driver.prepared_cpu_gate, BC_BESIDE_LANE `lane`): the next evaluation's own
# gate, so here it keeps normal priority and its mac_cap (game_slots), but on the hosts it plays beside the evaluation
# as its lane's coder, done coding, would (host_share by coder_slot), within the half the players beside it share
# (CLAUDE.md: Run continuously).
def ration_hosts(remote, lane: int) -> None:
    capacity = remote.Host.capacity

    def rationed(host) -> int:
        free = capacity(host)  # 0 until the host's first measurement, which also gives its cores for the share
        (slot, slots), evaluating = coder_slot(lane, holder_lane()), hosts_wanted_now(host)
        if evaluating:
            (rank, count), offset = (0, 1), host_offset(host.name, host_runs_now(remote), slots)
        else:  # as its lane's coder would (yield_to_evaluations' share_of)
            (rank, count), offset = coder_rank(host.name, remote.STATUS), zlib.crc32(host.name.encode())
        return min(free, host_share(host.cores or host.max, evaluating, rank=rank, count=count, slot=slot,
                                    slots=slots, offset=offset))

    remote.Host.capacity = rationed


# A coder's or the review's slot among the players that ration a host beside an evaluation (evaluation_shares), as
# (slot, slots): a lane's place among the IN_FLIGHT lanes but the one whose candidate is under test (`holder`, the
# marker's "lane"), then the review's (`lane` None), then the share's (share_slot): YIELDING slots, one a player. With
# the holder unknown, a slot for every lane, so however many play they never take more than the half. Lanes are unique
# while claimed (driver.free_lane); a native lane above IN_FLIGHT (`claim N` past it) wraps around and may share a
# slot. Lane 0 is a post-mortem's or follow-on's coder, whose lane the next claim may have: NO_SLOT.
def coder_slot(lane: int | None, holder: int | None) -> tuple[int, int]:
    slots = driver.IN_FLIGHT + 1 + (holder is None)
    if lane is None:
        return slots - 2, slots
    if lane == 0:
        return NO_SLOT, slots
    place = (lane - 1) % driver.IN_FLIGHT
    if holder is not None:
        place -= place > (holder - 1) % driver.IN_FLIGHT
    return place, slots


# The share's slot (the tune players' or else the screen fillers'), the last of coder_slot's.
def share_slot(holder: int | None) -> tuple[int, int]:
    slots = coder_slot(None, holder)[1]
    return slots - 1, slots


# A coder's lane, from its clone's driver.LANE_FILE, which the claim wrote and the result's record sets to 0
# (driver.retire_lane), read without following a link or waiting on a FIFO put there (meta.worker_file).
# A clone made before that file (2026-09-27) goes by its experiment number instead, which two coders' games may share
# for as long as such clones code.
def coder_lane(clone: Path) -> int:
    try:
        return int(meta.worker_file(clone / driver.LANE_FILE))
    except (OSError, ValueError):
        return int(re.sub(r"\D", "", clone.name) or 0) % driver.IN_FLIGHT + 1


# Where a coder's own games write their hosts' live status, which the utilisation record reads by purpose
# (throughput.status_files): <loop home>/remote-status-E####.json, or, from a coder's sandbox, which can't write the
# loop home, a file in its clone's git folder, where git never sees it as part of the candidate.
def coder_status(exp_id: str) -> Path:
    path = driver.STATE / f"remote-status-{exp_id}.json"
    probe = path.with_name(f".{path.name}.{os.getpid()}.probe")
    try:
        probe.touch()
        probe.unlink()
        return path
    except OSError:
        return driver.WORKTREES / exp_id / ".git" / "bc-remote-status.json"


def play_games(exp_id: str, args: list[str]) -> None:
    """A coding session's own measurement games (`games E#### --a BOT --b BOT --maps ... --out FILE`): the main
    checkout's current referee rather than the clone's copy. While an evaluation holds the lock, this Mac gives them
    CODER_FRACTION of its cores, and while it plays on the hosts (driver.host_stage) each SSH game machine at most its
    evaluation_shares (the host also checks its own load), so the coders, a review's rule-count games and the one
    share the tune players or the fillers take together take at most half of a host of 8+ cores and the evaluation
    keeps the rest (on 8+ cores under 2 * YIELDING the coders, the review and the share ration the half a game each,
    coder_slot, and a post-mortem's or follow-on's coder gets none there; on a smaller host each coder, the review and
    the tune players together still get one game, the fillers none); otherwise they may use the machines' usual share,
    each host split over the coders playing there (coder_rank). It doesn't wait for this Mac first: the scheduler
    gates each local game, and the hosts can play meanwhile, whatever its options (on_the_hosts: a fixed --workers
    caps this Mac alone, --sandbox plays on the Mac hosts too)."""
    sys.path.insert(0, str(driver.REPO / "referee"))
    import remote
    import run
    yield_to_evaluations(remote, lane=None if exp_id == "review" else coder_lane(driver.WORKTREES / exp_id))
    remote.STATUS = coder_status(exp_id)  # never the evaluation's live status
    run.game_slots, run.fork_room = game_slots, game_room
    lower_games(run)
    sys.argv = ["run.py", *on_the_hosts(run, remote, args)]
    raise SystemExit(run.main())


CODER_CLONE = "BC_CODER_CLONE"  # a coder's clone, in its environment (driver.lane_env): its `play` scripts' lane


# A coder's games play on the game machines too, never here only (sig, 29 Sep: measurements spread across all the
# machines; E0323's self-play with `--workers 4` waited 1.7 h on this Mac's load gate with the hosts free, since a
# fixed --workers keeps run.Scheduler here). Returns `args` for `script` (run.py, cpu.py, identical.py) with:
# - `--workers N` dropped, so the hosts play, and N kept as this Mac's cap (game_slots);
# - `--same-platform` (run.py) taken out and passed on: games only on machines of this Mac's platform, which play move
#   for move as here (a Linux or Windows host builds its own bots), for a coder comparing games move by move;
# - `--sandbox` (run.py) also on the hosts of this Mac's platform, where a sandbox game prices as here (the CPU gates',
#   referee/cpu.py --hosts), still giving up after CPU_WAIT_S in all with nothing playing anywhere (batch_slots);
# - cpu.py given `--hosts`, the same.
# The hosts take the coder's share of each (yield_to_evaluations, already set), so the evaluation keeps the rest.
# Another script's arguments are its own, returned as they are.
def on_the_hosts(run, remote, args: list[str], script: str = "run.py") -> list[str]:
    if script not in ("run.py", "cpu.py", "identical.py"):
        return args
    args, cap = list(args), None
    for n, arg in enumerate(args):
        value = arg.removeprefix("--workers=") if arg.startswith("--workers=") else \
            args[n + 1] if arg == "--workers" and n + 1 < len(args) else None
        if value is not None and value.isdecimal() and int(value) > 0:
            cap, args = int(value), args[:n] + args[n + (1 if arg != "--workers" else 2):]
            break
    same = "--same-platform" in args and script == "run.py"
    args = [arg for arg in args if not (same and arg == "--same-platform")]
    sandbox = "--sandbox" in args and script == "run.py"
    # At most `cap` here, never more than the gate's own "auto" answer (a fixed number is all or nothing there).
    slots = game_slots if cap is None else lambda requested, active=0: min(game_slots("auto", active), cap)
    if sandbox or same:
        stream = run.stream_games

        def stream_games(*given, **options):
            return stream(*given, **{**options, "same_platform": same or options.get("same_platform", False),
                                     "sandbox_hosts": sandbox or options.get("sandbox_hosts", False)})

        run.stream_games = stream_games
    if sandbox:
        hosts = remote.load_hosts()  # the scheduler's own Hosts (one per name a process)
        slots = batch_slots(slots=slots, elsewhere=lambda: any(host.active for host in hosts))
    run.game_slots = slots
    return [*args, "--hosts"] if script == "cpu.py" and "--hosts" not in args else args


CPU_WAIT_S = 600  # how long quick's CPU check and a coder's sandbox batch wait in all with nothing playing (batch_slots)
CPU_UNCHECKED = "cpu not checked (this Mac over its load gate; the evaluation's CPU gate plays it)"


# game_slots (`slots`) for a coder's sandbox games (on_the_hosts), which play here and on the hosts of this Mac's
# platform: the time the batch spends with none of its games playing, here or on a host (`elsewhere`), and no room
# here for one adds up over the whole batch, not only before its first game, and past `budget` in all it prints
# CPU_UNCHECKED and exits 75 with no results written (run.main writes --out at the end; its scheduler stops its games
# on the way out; `say` False leaves the saying to the caller, quick.cpu_line). Only the scheduler's own thread (the
# main one) gives up: a SystemExit in a host game's replay thread (run.Scheduler.replay_here) would end only that
# thread.
def batch_slots(budget: float = CPU_WAIT_S, slots=None, elsewhere=lambda: False, say: bool = True):
    waited, since, ask = 0.0, None, slots or game_slots

    def slots(requested: int | str, active: int = 0) -> int:
        nonlocal waited, since
        got, now = ask(requested, active), time.monotonic()
        if got or active or elsewhere():  # a game may start, or one is playing: not waiting on this Mac
            if since is not None:
                waited, since = waited + now - since, None
            return got
        since = now if since is None else since
        if waited + now - since >= budget and threading.current_thread() is threading.main_thread():
            if say:  # quick.cpu_line says it in its own line
                print(f"games: {CPU_UNCHECKED}; stopped after {(waited + now - since) / 60:.0f} min waiting in all, "
                      f"no results written", flush=True)
            raise SystemExit(75)
        return got

    return slots


# Writes a native coder's worker.json through the Claude driver's driver.worker_status, so every reader of it
# (evalline's coding and queued lists, throughput.waiting_now, driver.coding_count, sweep.busy) sees native lanes as
# it sees the driver's: "running" while the session codes (after claim, resume and a resolve_required), "finished"
# once evaluate starts (the candidate waits for or holds the evaluation lock) or abandon records the idea. A native
# coder has no process of its own, so "running" carries no pid (resume refuses a live one as a Claude child's) and no
# driver_pid; "finished" has this command's pid as driver_pid, alive until the result is recorded (resume refuses it
# too while it lives; once it is dead, driver.stranded_count keeps the candidate from holding the fillers), unless
# `evaluating` is False: a tune's claim, which exits before the session runs evaluate, so its candidate waits with no
# driver_pid instead of reading as stranded. The driver's log line goes to stderr: claim's stdout is one JSON object.
def worker_state(exp: driver.Experiment, status: str, evaluating: bool = True) -> None:
    exp.session.setdefault("name", f"native-{exp.id}-lane{exp.lane}")
    with contextlib.redirect_stdout(sys.stderr):
        driver.worker_status(exp, status, pid=None,
                             **({"driver_pid": None} if status == "running" or not evaluating else {}))


def claim(lane: int) -> None:
    if driver.stop_requested():
        print(json.dumps({"status": "stopped"}))
        return
    if work := driver.meta.prepare_review(driver.REPO, driver.STATE):
        start_upkeep("review", work)
        rulecounts.start(driver.STATE, work)  # the review's fresh games, into rule-counts.md while it reads
        print(json.dumps({"status": "review_required", "work": str(work),
                          "prompt": str(work / "PROMPT.md"), "then": f"uv run loop/native.py review {work}"}))
        return
    record = driver.audit.read(driver.STATE)
    if not driver.audit.alive(record.get("pid")) and (
            work := Path(record["pending"]) if record.get("pending") else driver.audit.prepare(driver.STATE)):
        print(json.dumps({"status": "audit_required", "work": str(work), "prompt": str(work / "PROMPT.md"),
                          "then": f"uv run loop/native.py audited {work}"}))
        return
    with contextlib.redirect_stdout(sys.stderr):  # its toolchain lines: stdout stays one JSON object
        idea = driver.claim_next(lane, STOP, reevaluations=False)  # a re-evaluation resumes a Claude coder's session
    if idea is None:
        print(json.dumps({"status": "empty"}))
        return
    with contextlib.redirect_stdout(sys.stderr):  # its log line, if it refuses the idea's settings
        exp = driver.new_experiment(idea, lane)
    if exp is None:  # its idea is set to error, with the reason in its history
        print(json.dumps({"status": "error", "idea": idea["id"],
                          "reason": "its evaluation settings were refused; see its history and host-alerts.log"}))
        return
    exp.session.update(provider="native-agent", model="gpt-6-astra", reasoning_effort="xhigh")
    try:
        worktree = driver.make_worktree(exp)
    except Exception as error:
        exp.verdict, exp.reason = "error", f"setup: {type(error).__name__}: {error}"
        finish(exp)
        raise
    exp.session.update(driver.protected_baseline())
    driver.save_experiment(exp)
    start_watch(exp)
    if idea.get("kind") == "tune":  # no coding: the shared tuner writes the candidate, as in the Claude driver
        with contextlib.redirect_stdout(sys.stderr):  # its log lines: stdout stays one JSON object
            driver.run_tuner(exp, worktree)
        driver.save_experiment(exp)
        if failure := driver.session_failure(exp):
            exp.verdict, exp.reason = failure
            finish(exp)
            return
        if not driver.own_commits(exp, worktree)[0]:  # the SPSA kept every value: recorded as the Claude driver does
            exp.verdict, exp.reason = driver.no_code(exp, bool(exp.session.get("stack")))
            finish(exp)
            return
        # commit_tune wrote "finished" with this claim's pid, which exits now: the Claude driver evaluates in the
        # process that tuned, but here the session's evaluate does, so the candidate waits with no driver_pid.
        worker_state(exp, "finished", evaluating=False)
        summary = exp.session["summary"]
        print(json.dumps({"status": "tuned", "experiment": exp.id, "worktree": str(worktree), "summary": summary,
                          "then": f"uv run loop/native.py evaluate {exp.id} {shlex.quote(summary)}"}))
        return
    worker_state(exp, "running")
    driver.save_experiment(exp)
    print(json.dumps({"status": "claimed", "experiment": asdict(exp), "worktree": str(worktree),
                      "policy": str(driver.REPO / "AGENTS.md"), "prompt": driver.lane_prompt(exp),
                      "warning": driver.online.version_alert(driver.STATE)}))


def finish(exp: driver.Experiment) -> None:
    driver.record(exp)
    wip = driver.keep_wip(exp, driver.WORKTREES / exp.id)  # a stopped coder's work, saved before its idea is back
    driver.finish_idea(exp.idea["id"], driver.idea_status(exp.verdict, exp.reason),
                       f"{exp.id}: {exp.verdict} ({exp.reason})")
    driver.cleanup(exp, driver.WORKTREES / exp.id)
    driver.save_experiment(exp)
    driver.start_official(exp)
    report = {"id": exp.id, "verdict": exp.verdict, "reason": exp.reason, "stacked_on": exp.stacked_on, "cpu": exp.cpu,
              "bot_tests": exp.bot_tests,
              "decision": exp.sprt.get("decision"), "pairs": exp.sprt.get("pairs"),
              "score": exp.sprt.get("score"), "elo": exp.sprt.get("elo"), "pool": exp.pool, "identity": exp.identity,
              "official": exp.official, "missing_checks": driver.missing_checks(exp),
              "hosts": exp.sprt.get("hosts", {}), **({"wip": wip} if wip else {}),
              **driver.mix_fields(exp),
              **({"per_style": exp.sprt.get("per_style"), "per_area": exp.sprt.get("per_area")}
                 if exp.evaluation.get("maps") else {}),
              **({"map_mix_refused": exp.session["map_mix"]["refused"]} if "map_mix" in exp.session else {})}
    # The independent judge the Claude driver launches first (LOOP.md: Judged verdicts), done by the active model in
    # the folder `judge` returns; `judged` then applies it and returns the post-mortem or follow-on.
    if driver.judge.owed(driver.asdict(exp)) and not driver.judge.saved(driver.STATE, exp.id):
        report.update(judge_task(exp))
    else:
        report.update(turn_task(exp))
    print(json.dumps(report), flush=True)


# The same post-mortem (a result not kept) or follow-on (kept) the Claude driver resumes its coder for: do it in the
# clone, then queue follow-ups. {} when the result gets neither.
def turn_task(exp: driver.Experiment) -> dict:
    if (prompt := driver.prepare_postmortem(exp, driver.WORKTREES / exp.id)):
        return {"followon" if exp.verdict == "kept" else "postmortem": prompt,
                "then": f"uv run loop/native.py followups {exp.id}"
                        + (" '<each added cutoff with its reason, or none>'" if exp.verdict == "kept" else "")}
    return {}


# A native result's judge: its folder made afresh with its inputs, and its prompt (loop/JUDGE.md), for the active
# model, which writes verdict.json there and runs `native.py judged E####` (a native session can't start a fresh
# model, so this judge isn't independent of the session: the trusted side's check still applies).
def judge_task(exp: driver.Experiment) -> dict:
    work = driver.judge.prepare(driver.STATE, driver.asdict(exp), driver.judge_files(exp))
    prompt = (driver.meta.protected_io(driver.STATE, "judge", work) / "PROMPT.md").read_text()
    return {"judge": prompt, "judge_folder": str(work), "then": f"uv run loop/native.py judged {exp.id}"}


# Applies the verdict the active model wrote in the judge folder (driver.apply_answer), then returns the post-mortem
# or follow-on task, as JSON.
def judged(exp: driver.Experiment) -> str:
    record = driver.apply_answer(exp, driver.judge.folder(driver.STATE, exp.id), by="native")
    if record is None:
        return json.dumps({"id": exp.id, "judged": None,
                           "error": "not applied: no valid verdict.json, or held by a stop marker (host-alerts.log)"})
    row = driver.result_row(exp.id)
    exp = driver.Experiment.load(row) if row else exp  # kept by judgement: its follow-on
    return json.dumps({"id": exp.id, "judged": driver.judge.line(record), **turn_task(exp)})


# Queues the follow-ups of the post-mortem or follow-on that finish returned, then saves the marker prune.owed_turn
# waits for before it deletes the result's losses. The active model's answer itself isn't saved, so stats.md reads
# its Predictable line as not stated; `text`, what was given after the id, is read as a follow-on's Constants: line
# (driver.cutoffs_note), its label optional.
def followups(exp: driver.Experiment, text: str = "") -> str:
    clone = driver.WORKTREES / exp.id
    text = text if not text.strip() or driver.constants_said(text) else f"{driver.CONSTANTS} {text}"
    line = driver.add_followups(exp, clone) + driver.cutoffs_note(exp, clone, text)
    name = "followon" if exp.verdict == "kept" else "postmortem"
    (exp.run_dir / f"{name}.json").write_text(json.dumps({"native": True, "followups": line}) + "\n")
    return line


# Whether results.jsonl holds exp_id's result. quick reads it without the evaluation lock (native.quick), so a line
# that doesn't parse (a result being appended now) is skipped rather than read as an error.
def recorded(exp_id: str) -> bool:
    if not driver.RESULTS.exists():
        return False
    for line in driver.RESULTS.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id") == exp_id:
            return True
    return False


# A native lane's quick check: its bot tests, then the main checkout's quick.py (the clone's is loop/champion's old
# copy) on its bots with the clone's Python and toolkit, as the Claude coders run it. It holds no evaluation lock
# and writes no evaluation marker, and doesn't wait for one (operate returns before taking it): it plays
# nothing from ~/.bc-loop/referee, which is all a refresh moves, uses no experiment runtime (the clone's Python is
# named here), and its games yield to evaluations as a coder's own (quick.pair_games). Holding the lock (until
# 2026-09-27) kept every evaluation and filler waiting and the hosts idle while its games waited for this Mac's
# load, and kept a quick waiting behind a whole SPRT (MISSES 49).
def quick(exp: driver.Experiment, worktree: Path) -> None:
    if recorded(exp.id):
        raise RuntimeError(f"{exp.id} is already recorded")
    tests = driver.check_bot_tests(worktree / "bot", worktree / "tests/bot")
    print(tests.stdout, end="", flush=True)
    if tests.returncode != 0:
        print(tests.stderr, file=sys.stderr, end="", flush=True)
        raise SystemExit(tests.returncode)
    subprocess.run([str(worktree / ".venv/bin/python"), str(SCRIPT), "play", "loop/quick.py",
                    "--clone", str(worktree)], cwd=driver.REPO, check=True,
                   env={**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1"})


def operate(action: str, experiment_id: str, summary: str) -> None:
    if not re.fullmatch(r"E\d{4,}", experiment_id):
        raise ValueError("expected an experiment ID such as E0024")
    if driver.stop_requested():
        raise SystemExit("STOP or legacy ASTRA_STOP exists; not starting another step")
    metadata = driver.STATE / "runs" / experiment_id / "experiment.json"
    if not metadata.exists():
        metadata = metadata.with_name("astra.json")
    if action == "quick":  # without the evaluation lock (native.quick)
        exp = driver.Experiment.load(json.loads(metadata.read_text()))
        quick(exp, driver.WORKTREES / exp.id)
        return
    exp = driver.Experiment.load(json.loads(metadata.read_text()))
    worktree = driver.WORKTREES / exp.id
    worker = driver.online.read(exp.run_dir / "worker.json", {}) if action == "abandon" else {}
    if worker.get("status") == "tuning" and isinstance(pid := worker.get("pid"), int) and driver.meta.loop_process(pid):
        # finish's cleanup would delete the clone under the running SPSA (driver.tuning_count reads it so too)
        raise RuntimeError(f"its tune is still playing its SPSA games (pid {pid}); abandon it once that ends")
    if action == "evaluate":  # coding is done: the candidate now waits for the lock (throughput.waiting_now)
        worker_state(exp, "finished")
    # An abandon evaluates nothing, so it takes no place in the evaluation queue (driver.enter_queue); an evaluate
    # prepares in it while another evaluation plays. An interrupted promotion is recovered only once the lock is held
    # (driver.recover_held): waiting for the lock first (recover_promotions) left an evaluate unstamped and out of the
    # queue until the evaluation playing ended, so it prepared nothing and later candidates went ahead.
    queue = None if action == "abandon" else driver.queue_ticket(exp)
    meanwhile = (lambda: driver.prepare_beside(exp, worktree)) if action == "evaluate" else None
    with driver.evaluation_lock(lane=exp.lane, queue=queue, meanwhile=meanwhile), driver.experiment_runtime(exp):
        exp.session["locked_at"] = time.time()  # the end of its wait (health.unprepared_waits)
        if meanwhile:
            exp.session["set_ahead_code"] = True  # the queue poll it ends ran make_set_ahead (health.set_ahead)
        if driver.stop_requested():
            raise SystemExit("STOP or legacy ASTRA_STOP exists; not starting another step")
        if experiment_id in driver.recover_held():
            print(json.dumps({"id": experiment_id, "verdict": "kept", "reason": "recovered accepted promotion"}))
            return
        if recorded(exp.id):
            raise RuntimeError(f"{exp.id} is already recorded")
        exp.session.update(summary=summary, workers=driver.WORKERS)
        if action == "abandon":
            # Only a tune's SPRT judges its values, so abandoning one (its SPSA died, or its candidate can't be
            # evaluated) is an error, not evidence: its idea goes back to the queue (a second error retires it,
            # driver.finish_idea), and the next claim reuses the values a finished SPSA left (driver.earlier_tune).
            exp.verdict = "error" if exp.idea.get("kind") == "tune" else "discarded"
            exp.reason = summary or "idea abandoned without a candidate"
            worker_state(exp, "finished")  # only under the lock: waiting for it, an abandon is no candidate waiting
            finish(exp)
            return
        try:
            if not driver.refresh_referee():  # as the Claude driver does, holding the lock before evaluating
                exp.verdict, exp.reason = "blocked", driver.REFEREE_BROKEN
                metadata.write_text(json.dumps(asdict(exp)) + "\n")
                finish(exp)
                return
            driver.guard_candidate(exp, worktree)
            driver.evaluate(exp, worktree)
            if exp.verdict == "conflict":
                # The same one-time resolve the Claude driver resumes its coder for; nothing is recorded yet.
                prompt = driver.resolve_prompt(exp)
                exp.verdict, exp.reason = "pending", ""
                worker_state(exp, "running")  # the session codes again, until its next evaluate
                metadata.write_text(json.dumps(asdict(exp)) + "\n")
                print(json.dumps({"id": exp.id, "status": "resolve_required", "prompt": prompt,
                                  "then": f"uv run loop/native.py evaluate {exp.id} {shlex.quote(summary)}"}))
                return
            if exp.verdict == "kept":
                driver.promote(exp)
        except Exception as error:
            exp.verdict, exp.reason = "error", f"{type(error).__name__}: {error}"[:500]
        metadata.write_text(json.dumps(asdict(exp)) + "\n")
        finish(exp)
    # After the lock and in the background, as the Claude driver does: neither the next evaluation nor this command's
    # caller waits for its games (driver.start_evidence).
    if exp.verdict == "kept":
        with contextlib.redirect_stdout(sys.stderr):  # its log line: stdout stays one JSON object
            driver.start_evidence(exp)


def completed(experiment_id: str) -> bool:
    return recorded(experiment_id)


def watch(experiment_id: str) -> None:
    """Non-model upkeep while the session codes; ends on a result or either stop marker."""
    if experiment_id == "review":
        pending = driver.online.read(driver.STATE / "review.json", {}).get("pending")
        if not pending:
            return
        folder = Path(pending)
    else:
        folder = driver.STATE / "runs" / experiment_id
    def finished() -> bool:
        if experiment_id == "review":
            return driver.online.read(driver.STATE / "review.json", {}).get("pending") != str(folder)
        return completed(experiment_id)
    with (folder / "upkeep.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        driver.online.save(folder / "upkeep.json", {"pid": os.getpid(), "status": "running"})
        try:
            # The tick only while this process owns it (maintenance.ticking: one process at a time), the minute health
            # checks beside it, and utilisation.jsonl's minute rows in a thread of their own: a tick can take many
            # minutes.
            with driver.maintenance.ticking(driver.REPO, driver.STATE, STOP, False, driver.stop_requested), \
                    driver.maintenance.throughput.sampling(driver.STATE, driver.stop_requested):
                while not driver.stop_requested() and not finished():
                    time.sleep(5)
        finally:
            driver.online.save(folder / "upkeep.json", {"pid": os.getpid(), "status": "stopped"})


def start_watch(exp: driver.Experiment) -> None:
    start_upkeep(exp.id, exp.run_dir)


def start_upkeep(target: str, folder: Path) -> None:
    with (folder / "upkeep.log").open("a") as log:
        subprocess.Popen([sys.executable, str(SCRIPT), "watch", target], cwd=driver.REPO,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def resume(experiment_id: str) -> None:
    """Recover saved work, not a new experiment; never take a clone from a running Claude child or from an evaluation
    still waiting for or holding the lock (worker.json "finished" with a live `driver_pid`, meta.evaluator_alive), and
    never a tune's, which has no coding to hand back."""
    if driver.stop_requested():
        raise RuntimeError("experiment is stopped")
    if experiment_id in driver.recover_promotions():
        print(json.dumps({"id": experiment_id, "verdict": "kept", "reason": "recovered accepted promotion"}))
        return
    if completed(experiment_id):
        raise RuntimeError("experiment is stopped or already recorded")
    folder = driver.STATE / "runs" / experiment_id
    exp = driver.Experiment.load(json.loads((folder / "experiment.json").read_text()))
    if exp.idea.get("kind") == "tune":  # no coder: run_tuner writes its candidate, and only the SPRT is left to do
        raise RuntimeError(f"a tune has no coding to resume: once its SPSA finished (worker.json \"finished\"), "
                           f"`uv run loop/native.py evaluate {experiment_id} '<summary>'` tests its committed values; "
                           f"else `uv run loop/native.py abandon {experiment_id} '<reason>'` records it as an error "
                           "and sends its idea back to the queue")
    worker = driver.online.read(folder / "worker.json", {})
    if worker.get("status") == "running" and worker.get("pid"):
        try:
            os.kill(worker["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("Claude child may still be running; do not create a second writer")
    pid = worker.get("driver_pid")
    if worker.get("status") == "paused" and driver.meta.alive(pid):
        raise RuntimeError(f"its driver (pid {pid}) holds its coder through the usage pause and resumes it at the "
                           "reset; do not create a second writer")
    if worker.get("status") == "finished" and pid is not None and driver.meta.evaluator_alive(worker):
        raise RuntimeError(f"its evaluation (pid {pid}) is waiting for or holding the lock; do not create a second "
                           f"writer. If `ps -o command= -p {pid}` shows no `native.py evaluate {experiment_id}` or "
                           f"`driver.py` (a reused pid), remove driver_pid from {folder / 'worker.json'} and resume "
                           "again")
    worker_state(exp, "running")
    driver.save_experiment(exp)
    start_watch(exp)
    print(json.dumps({"status": "resumed", "experiment": asdict(exp),
                      "worktree": str(driver.WORKTREES / exp.id), "prompt": driver.lane_prompt(exp)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", default=driver.WORKERS)
    parser.add_argument("--elo1", type=float, default=driver.ELO1)
    parser.add_argument("--max-pairs", type=int, default=driver.MAX_PAIRS)
    parser.add_argument("action", choices=("claim", "quick", "evaluate", "abandon", "play", "games", "watch", "resume",
                                           "review", "followups", "audited", "judge", "judged"))
    parser.add_argument("target")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.workers != "auto" and (not args.workers.isdecimal() or int(args.workers) < 1):
        parser.error("--workers takes a positive number or 'auto'")
    if not math.isfinite(args.elo1) or args.elo1 <= 0 or args.max_pairs < 1:
        parser.error("--elo1 and --max-pairs must be positive")
    driver.WORKERS, driver.ELO1, driver.MAX_PAIRS = args.workers, args.elo1, args.max_pairs
    if args.action == "claim":
        if not args.target.isdecimal() or int(args.target) < 1:
            parser.error("lane must be a positive integer")
        claim(int(args.target))
    elif args.action == "play":
        play_script(args.target, args.rest)
    elif args.action == "games":
        if args.target != "review" and not re.fullmatch(r"E\d{4,}", args.target):  # review: loop/rulecounts.py
            parser.error("expected experiment id")
        play_games(args.target, args.rest)
    elif args.action == "watch":
        if args.target != "review" and not re.fullmatch(r"E\d{4,}", args.target):
            parser.error("expected experiment id")
        watch(args.target)
    elif args.action == "resume":
        if not re.fullmatch(r"E\d{4,}", args.target):
            parser.error("expected experiment id")
        resume(args.target)
    elif args.action == "followups":
        if not re.fullmatch(r"E\d{4,}", args.target):
            parser.error("expected experiment id")
        exp = driver.Experiment.load(json.loads((driver.STATE / "runs" / args.target / "experiment.json").read_text()))
        print(followups(exp, "\n".join(args.rest)))
    elif args.action in ("judge", "judged"):
        if not re.fullmatch(r"E\d{4,}", args.target):
            parser.error("expected experiment id")
        row = driver.result_row(args.target)
        if row is None or not driver.judge.owed(row) or driver.judge.saved(driver.STATE, args.target):
            parser.error(f"{args.target} is not owed a judge (not recorded, not judged by the rule, or judged)")
        exp = driver.Experiment.load(row)
        print(json.dumps(judge_task(exp)) if args.action == "judge" else judged(exp))
    elif args.action == "audited":
        print(driver.audit.apply(driver.STATE, Path(args.target)))
    elif args.action == "review":
        if driver.stop_requested():
            raise SystemExit("STOP exists")
        print(driver.meta.apply_review(driver.STATE, Path(args.target)))
    else:
        if driver.stop_requested():
            raise SystemExit("STOP or legacy ASTRA_STOP exists; not starting maintenance or experiments")
        with driver.maintenance.checking(driver.REPO, driver.STATE, STOP, models=False):
            operate(args.action, args.target, " ".join(args.rest))


if __name__ == "__main__":
    main()
