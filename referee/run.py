"""Plays two bots against each other on a list of maps, each map twice with sides swapped (or, with --one-side, once,
a's side alternating by map).

    uv run referee/run.py --a PATH --b PATH --maps PATH [PATH...] [--workers N|auto] [--sandbox] [--one-side]
                          [--replays DIR] --out FILE.jsonl

Writes one JSON line per game. Other scripts import `stream_games`, `play_pairs` or `play_games` instead.

Results come from two places, because neither has everything:
- the replay (parsed with pycapnp): who won, how the game ended, and each team's dragons and lengths;
- `unswbc run`'s stdout: rounds, deaths, bot timeouts and crashes, and (with --sandbox) CPU points.
The two are cross-checked on the winner; a mismatch is recorded as an error, never guessed around.

A game outside the sandbox may play through the native judge (native_judge.py: the organisers' engine wasm driven in
C, same replay bytes and output) instead of `unswbc run`, here or on a host, while NATIVE_JUDGE is on and its key
passed the fidelity gate at that place (native_plan, native_gated); a row it played carries `native`.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import capnp

import engine_cache
import fork_server
import mapcheck
import native_judge
import record
import remote

HERE = Path(__file__).resolve().parent
UNSWBC = str(Path(sys.executable).parent / "unswbc")
GAME_TIMEOUT_S = 900
# The niceness this process's games here start at (0: as this process; loop/native.py's lower_games sets 19 for every
# loop game but the evaluation's CPU gate and identical check, 20 for the players beside an evaluation's), through
# /usr/bin/nice (nice_command), so builds, tests and other work at normal priority win this Mac's CPU and the games
# take what is idle. A game's clock advances with the points its bots spend, not wall time (docs/unsw/timeouts.md
# "Determinism"), but a turn still has a wall limit of 10 s (native games' only limit, the sandbox's backstop), and a
# whole game GAME_TIMEOUT_S (900 s against a median 11 s), which a game starved of CPU while dev work fills every core
# could reach: a lowered game is stopped at its first turn out of time (watch_game), or past GAME_TIMEOUT_S, and played
# again at normal priority (Scheduler.try_here), and that game counts; each such replay is kept in game-retries.json's
# "niced" rows (note_retry), unless its replay ran a turn out of time too (the bot's own timeout, not this Mac's). The fork server's START_S and REQUEST_S only fall back to starting bots as usual, which
# changes no result, so a lowered game's start timeout isn't kept as the fork server being off. A game of a process
# already below normal priority (own_nice: a headless worker's commands inherit loop/home.py's WORKER_NICE, where its
# sandbox may refuse a lower one, so GAME_NICE stays 0) is a lowered game too; its replay can't regain normal priority
# and plays at the process's own niceness.
GAME_NICE = 0
# A remote game past this is treated as lost and replayed here. Games take a median 11 s and at most ~120 s
# (E0040), but an SSH session whose far end vanished in a network drop never ends on its own; waiting the full
# game limit for one stalled the whole evaluation for 17 minutes. A genuinely slow game only costs a replay.
REMOTE_TIMEOUT_S = 300
# How often a run rewrites ~/.bc-loop/remote-status.json with its progress. 30 s before 2026-09-27: a run's first
# games went unseen for that long, so the utilisation record (loop/throughput.py) showed the opponent screen's
# 60-second runs idle in most of its minutes.
STATUS_S = 10
RECORDER = HERE / "record.py"
ENGINE_LAUNCHER = HERE / "engine_cache.py"
FORK_LAUNCHER = HERE / "fork_server.py"
# One native game in this many is played both ways by fork_server.py --check (fork_check), besides each new pair of
# bots' first. 200, was 20 (orchestrator, 2026-09-28, as meta controller): 25,827 of 25,827 checks were identical
# (fork-server-checks.json) and they cost ~7% extra game CPU. 1 in 200 checks about 2-10 of a candidate's own games an
# SPRT, so the first game of each pair this process plays is checked too: a fork difference in a new bot's code shows
# at its first game, not hundreds later.
FORK_CHECK_EVERY = 200
# The switch for the native judge (native_judge.py): native games here and on Mac and Linux hosts played by
# native_judge/play.c instead of `unswbc run`, each only where its key passed native_fidelity.py's gate at that place
# (native_plan, native_gated), and not once a check, a judge crash or hang has switched it off (natives_off).
# On 29 Sep (sig: "can u try and switch on the native judge pls everywhere"): gates passed at every place for key
# 579a6ad16cf2ab97-cd6f852768e80fe9-1fd0d3103ba28f57-215772dad6f22e37-5555ea208504d09c (the Mac hosts on mm and the
# one Linux compiler group on ll, all 11 Linux hosts, 40 of 40 games each); this Mac goes on the Mac hosts' gate
# (native_gated); 300-game fidelity reports on mm and ll (<loop home>/native-judge/fidelity-{mm,ll}-300-20260929.json),
# read by the orchestrator before merge. native_judge.py's docstring still says the switch is off until such a report
# is read: its bytes are in the key (native_judge.launcher_digest), so it is corrected with the next change that makes
# a new key anyway.
NATIVE_JUDGE = True
# One native game in this many is also played through `unswbc run` and the two compared (native_check), besides each
# pair of bots' first native game (its second game: the first is a fork server check, which never plays natively).
# 1 in 1000 (sig, 2026-09-29): the gate and 406 fidelity games were identical; a check costs one more game.
NATIVE_CHECK_EVERY = 1000
CHECKS = 4  # predicted jobs checked at once, on this Mac (see Prediction); a check is a fraction of a game's CPU
CHECK_WAIT_S = 30  # a job waiting this long for a check to start (this Mac is busy) is played instead
# Prediction switches itself off for the rest of a run when fewer than FLOOR_SHARE of its first FLOOR_CHECKS checks
# found an identical game: the checks would then cost more than the games they save.
FLOOR_CHECKS, FLOOR_SHARE = 100, 0.05
# The waits before each replay of a game this machine, not a bot, failed (infrastructure_failure), so a game is tried
# up to 1 + len(REPLAY_WAITS_S) times: a fork refused at this user's process limit (E0158's "[Errno 35] Resource
# temporarily unavailable") clears as other games end, which E0158's instant replay didn't wait for. Same map, side and
# seed each time, so a try that plays is the game the first would have been.
REPLAY_WAITS_S = (10, 60)
GAME_RETRIES = "game-retries.json"  # in the loop home: the newest RETRIES_KEPT games replayed that way, for health.py
RETRIES_KEPT = 50
NICED_ROWS = "niced"  # game-retries.json's list of lowered games replayed at normal priority (Scheduler.try_here)
# Results come back in job order, so games finished behind a slow head game wait for it; new games keep starting while
# fewer than AHEAD x (this Mac's cores + every host's max) are started but not yet yielded (Scheduler.ahead, 924
# with today's hosts). At 2x (308, 60-90 s of games) a head game longer than that stalled every free slot until it
# ended: E0163's SPRT, its 5,988 games replayed at their real durations on its 60 slots, took 27.4 min at 0.66 busy
# (measured: 28 min, 0.61), against 19.1 min at 0.96 with no bound, and the ten SPRTs E0141-E0163 took 140 min against
# 101. 6x matches no bound on all ten; the cost is the games past a decision the SPRT drops, 329 a test against 198 at
# 2x (all ten: 3,291 against 1,981), about 24 s of the machines a test against the ~4 min saved. A game a host lost
# (after its 300 s REMOTE_TIMEOUT_S, say) can still stall the rest: E0466's SPRT on 1 Oct 03:31-~03:57 waited ~25 min,
# every host idle, on one replayed here at nice 19 at load ~40. So a lost game goes to another host with room first
# (Scheduler.replay_lost), and one replayed here while it is the head game plays at the run's own priority
# (Scheduler.replay_here).
AHEAD = 6
NOT_READY = object()  # Scheduler.play's reading of a None job: none ready yet
PARSE_LOCK = threading.Lock()

capnp.remove_import_hook()
REPLAY_SCHEMA = capnp.load(str(HERE / "replay.capnp"))

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# The toolkit or the judge hit an OS error (a fork refused, a full disk, too many open files), not a bot: the toolkit's
# "error: [Errno N] ...", or the judge's "error: cannot write the replay to ...: [Errno N] ...".
ENGINE_OS_ERROR = re.compile(r"^error: (?:.*: )?\[Errno \d+\].*$", re.M)
# unswbc 1.2.3+ names the tiebreak that decided a game on length ("longer queen, 6 to 0", "equal length: queen 0,
# longest 9, total 30 each"); the older "on length" / "equal length" forms still match.
WIN_LINE = re.compile(r"^team (A|B) wins after (\d+) rounds \((by elimination|on length|(?:longer queen|longest dragon|total length), \d+ to \d+)\)")
DRAW_LINE = re.compile(r"^draw after (\d+) rounds \((both teams eliminated|equal length(?:: queen \d+, longest \d+, total \d+ each)?)\)")
DEATH_LINE = re.compile(r"^round \S+: bot \d+ \(team (A|B)\) died: (.+)$")
BOT_ERROR_LINE = re.compile(r"^round \S+: bot \d+ \(team (A|B|\?)\) (.+)$")
POINTS_LINE = re.compile(
    r"^team (A|B) points per turn: p50 (\S+)\s+p99 (\S+)\s+mean (\S+)\s+max (\S+)\s+\((\d+) turns\)")
TIMEOUT_ERRORS = ("exceeded CPU limit", "ran out of time")
END_NAMES = {"teamEliminated": "elimination", "roundLimit": "rounds"}


# Points one bot spent per turn in one game (the sandbox's CPU price), as `unswbc run` summarises them.
@dataclass
class Points:
    p50: int
    p99: int
    max: int
    turns: int


# One bot's side of one game. `alive`/`longest`/`total` are its dragons at the end. `timeouts` counts turns
# that ran out of time or CPU; `crashes` counts any other failed turn (exit, sandbox error). Both leave the
# dragon with no action, which `no_action_deaths` counts.
@dataclass
class BotStats:
    alive: int = 0
    longest: int = 0
    total: int = 0
    timeouts: int = 0
    crashes: int = 0
    no_action_deaths: int = 0
    points: Points | None = None


# One game. `winner` is "a", "b" or "draw"; `a_side` is the side ("A" moves first) bot a played.
# When `error` is set the game could not be run or read, and the outcome fields are None. `predicted`: not played,
# but the recorded game bot a was checked to repeat turn for turn (see Prediction).
@dataclass
class GameResult:
    map: str
    a_side: str
    winner: str | None
    end: str | None
    rounds: int | None
    a: BotStats | None
    b: BotStats | None
    seconds: float
    error: str | None
    predicted: bool = False
    recording: str | None = None  # a game played to record (Job.record): record.file_digest of its recording
    # Where it was played ("here", this Mac, or a remote host's name; None for a result reused unplayed) and, where that
    # host reports it (a remote Mac or Linux host: host_agent.game_cpu), at least its CPU seconds, so hosts compare on
    # the same games. With `native`, about 50 bytes of each game's ~600 in sprt.json, which writes all three for every
    # game (to_json's lines drop the empty ones).
    host: str | None = None
    cpu: float | None = None
    native: bool = False  # played by the native judge (native_judge.py), not `unswbc run`


# A recorded game this job may repeat: another bot a in the same seat (map, side, bot b, toolkit) whose side of the
# game `recording` holds (record.py), and that game's `result`. Before playing, the scheduler checks bot a against the
# recording; when bot a answers every turn as recorded, the job's game is that game and `result` is reused unplayed.
# `audit`: play it anyway, on this platform, and compare, since any difference means something isn't deterministic.
# `digest`: the recording's file_digest as the cache paired it with `result`; a file that no longer matches (another
# game recorded over it since) is never checked or compared against.
@dataclass
class Prediction:
    recording: Path
    result: GameResult
    audit: bool = False
    digest: str | None = None


# One game to play: bot a on side `a_side` against bot b on `map`. `record`: also save bot a's side of the game
# there (record.py), which only games on this Mac's platform do. `predict`: see Prediction.
@dataclass
class Job:
    map: Path
    a: Path
    b: Path
    a_side: str
    record: Path | None = None
    predict: Prediction | None = None


# What predicting did in one run, for the report.
@dataclass
class PredictStats:
    checks: int = 0            # predicted jobs whose bot was checked against the recording
    identical: int = 0         # checks where it answered every turn as recorded
    predicted: int = 0         # games not played: the recorded result was reused
    audited: int = 0           # identical ones played anyway and compared
    audit_mismatches: int = 0  # audits whose game differed from the recording: prediction then stops
    check_cpu: float = 0.0     # CPU seconds of the checks, their bots' processes included
    off: str | None = None     # why prediction stopped partway through the run, if it did


# What `unswbc run` left behind for one game, before parsing.
@dataclass
class RawGame:
    stdout: str
    returncode: int | None
    replay: bytes | None
    seconds: float
    host: str | None = None  # GameResult.host and .cpu
    cpu: float | None = None

    # Whether the native judge played it (GameResult.native), by what it says first.
    @property
    def native(self) -> bool:
        return NATIVE_PLAYED in self.stdout


# Parses a points figure as `unswbc run` prints it: "3.7M" or "12,345".
def parse_points(text: str) -> int:
    if text.endswith("M"):
        return round(float(text[:-1]) * 1_000_000)
    return int(text.replace(",", ""))


# Reads the final result of a replay: (winner "A"|"B"|None, end reason, standings for A and B).
def read_replay(blob: bytes) -> tuple[str | None, str, dict[str, tuple[int, int, int]]]:
    replay = REPLAY_SCHEMA.Replay.from_bytes_packed(blob, traversal_limit_in_words=2**62)
    result = replay.result
    if not result.terminated:
        raise ValueError("replay says the game did not terminate")
    winner = str(result.winner).upper() if result.which() == "winner" else None
    standings = {
        team: (s.dragonCount, s.longestDragon, s.totalLength)
        for team, s in (("A", result.teamA), ("B", result.teamB))
    }
    return winner, END_NAMES[str(result.endReason)], standings


# Collects per-team timeouts, crashes, no-action deaths and points from `unswbc run`'s stdout.
def read_team_stats(lines: list[str]) -> dict[str, BotStats]:
    stats = {"A": BotStats(), "B": BotStats()}
    for line in lines:
        if m := DEATH_LINE.match(line):
            if m.group(2) == "no valid action":
                stats[m.group(1)].no_action_deaths += 1
        elif m := POINTS_LINE.match(line):
            p50, p99, _mean, top = (parse_points(m.group(i)) for i in range(2, 6))
            stats[m.group(1)].points = Points(p50, p99, top, int(m.group(6)))
        elif m := BOT_ERROR_LINE.match(line):
            team = m.group(1)
            if team == "?":
                raise ValueError(f"bot error with unknown team: {line}")
            if any(e in m.group(2) for e in TIMEOUT_ERRORS):
                stats[team].timeouts += 1
            else:
                stats[team].crashes += 1
    return stats


# Reads the outcome line: (winner "A"|"B"|None, rounds), or None if the game never finished.
def read_outcome(lines: list[str]) -> tuple[str | None, int] | None:
    for line in lines:
        if m := WIN_LINE.match(line):
            return m.group(1), int(m.group(2))
        if m := DRAW_LINE.match(line):
            return None, int(m.group(1))
    return None


# True when bots of both teams exited at round 0 and the game ended in its first round: a host that can't run the
# bots (e.g. missing runtime libraries), not a result. A remote game like that is replayed here, where a bot that
# really crashes at once crashes again and is recorded as such.
def startup_failure(stdout: str) -> bool:
    lines = [ANSI.sub("", line).rstrip() for line in stdout.splitlines()]
    failed = {m.group(1) for line in lines if line.startswith("round 0:") and (m := BOT_ERROR_LINE.match(line))
              and "exited with code" in m.group(2)}
    ended = next((int(m.group(2) if m.re is WIN_LINE else m.group(1)) for line in lines
                  if (m := WIN_LINE.match(line) or DRAW_LINE.match(line))), None)
    return failed >= {"A", "B"} and ended is not None and ended <= 1


# The error a game on a map the engine refuses to load gets (parse_game): MAP_REFUSED, the map's path, ": " and the
# engine's reason. Only map_refused's loader check writes it, so a bot can never cause it; loop/prune.py's
# MAP_FAULT reads it back.
MAP_REFUSED = "the engine refused to load the map"


# The engine's reason for refusing to load the map at `path` (mapcheck.refused: its own loader, no bot), or None when
# it loads or can't be checked (no toolkit here, an unreadable file, a checker that broke): a broken check fails open,
# so games behave as before it existed, and says so once a process on stderr.
def map_refused(path: Path) -> str | None:
    try:
        return mapcheck.refused(path)
    except Exception as error:
        if not _CHECK_FAILED:
            _CHECK_FAILED.append(True)
            print(f"run: the map check failed, so maps are played unchecked: {type(error).__name__}: {error}",
                  file=sys.stderr)
        return None


_CHECK_FAILED: list[bool] = []


# Turns a finished `unswbc run` into a GameResult, or a GameResult with `error` set.
def parse_game(job: Job, raw: RawGame) -> GameResult:
    def failed(reason: str) -> GameResult:
        return GameResult(str(job.map), job.a_side, None, None, None, None, None, raw.seconds, reason,
                          host=raw.host, cpu=raw.cpu, native=raw.native)

    lines = [ANSI.sub("", line).rstrip() for line in raw.stdout.splitlines()]
    tail = " | ".join(lines[-5:])
    if raw.returncode not in (0, None) and (reason := map_refused(job.map)):
        return failed(f"{MAP_REFUSED} {job.map}: {reason}")
    if raw.returncode != 0:
        return failed(f"unswbc run exited {raw.returncode}: {tail}")
    outcome = read_outcome(lines)
    if outcome is None:
        return failed(f"no result line in output: {tail}")
    if raw.replay is None:
        return failed("no replay written")
    try:
        winner, end, standings = read_replay(raw.replay)
        teams = read_team_stats(lines)
    except (ValueError, KeyError, capnp.KjException) as error:
        return failed(f"cannot read game: {error}")
    if winner != outcome[0]:
        return failed(f"replay winner {winner} disagrees with printed winner {outcome[0]}")

    for team, (alive, longest, total) in standings.items():
        teams[team].alive, teams[team].longest, teams[team].total = alive, longest, total
    b_side = "B" if job.a_side == "A" else "A"
    if winner is None:
        who = "draw"
    else:
        who = "a" if winner == job.a_side else "b"
    return GameResult(str(job.map), job.a_side, who, end, outcome[1], teams[job.a_side], teams[b_side],
                      raw.seconds, None, host=raw.host, cpu=raw.cpu, native=raw.native)


# Why a finished `unswbc run` failed through the machine that played it rather than through either bot, or None: the
# toolkit hit an OS error (ENGINE_OS_ERROR's line, such as a fork refused at the process limit) or its process died by
# a signal (died_by). A bot's crash, timeout or bad output is scored inside a game that exits 0, and a game past
# GAME_TIMEOUT_S (returncode None) or one the toolkit refused (a bot that doesn't build) is neither: those stay errors
# at once.
def infrastructure_failure(game: RawGame) -> str | None:
    if game.returncode in (0, None):
        return None
    if found := ENGINE_OS_ERROR.search(ANSI.sub("", game.stdout)):
        return found.group(0).strip()
    if signum := died_by(game):
        return f"the game's process died by signal {signum}"
    return None


# The signal that ended a game's process, or None: a negative exit code here (Popen's), or past 128 from a remote host
# (`host` set: host_agent.op_play reports a signal as a shell does, 128 + n).
def died_by(game: RawGame) -> int | None:
    if game.returncode is None:
        return None
    if game.returncode < 0:
        return -game.returncode
    return game.returncode - 128 if game.host not in (None, "here") and game.returncode > 128 else None


# Keeps one game this machine failed (infrastructure_failure) in <loop home>/game-retries.json, the newest RETRIES_KEPT,
# which health.game_retries alerts on: `causes` is each failed try's reason, `recovered` whether a later try played it.
# `rows`: the file's list it goes in, "games" or NICED_ROWS (a lowered game replayed at normal priority, try_here), each
# list kept to its newest RETRIES_KEPT so neither crowds the other out.
def note_retry(job: Job, causes: list[str], recovered: bool, rows: str = "games") -> None:
    import fcntl

    path = remote.loop_home() / GAME_RETRIES
    row = {"at": time.time(), "map": str(job.map), "a_side": job.a_side, "a": str(job.a), "b": str(job.b),
           "tries": len(causes) + recovered, "recovered": recovered, "why": causes[0][:300]}
    try:
        with open(path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(path.read_text())
                data = {key: kept for key in ("games", NICED_ROWS) if isinstance(kept := data.get(key), list)}
            except (OSError, ValueError, AttributeError):  # none yet, or unreadable: the newest rows start it again
                data = {}
            data[rows] = [*data.get(rows, []), row][-RETRIES_KEPT:]
            temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps({"updated": time.time(), "games": [], **data}) + "\n")
            temporary.replace(path)
    except OSError:  # a coder's sandbox can't reach the loop home; the game's own result is unaffected
        pass


# The pearl seed every game on `game_map` uses, from the map file's contents: both sides, the candidate and the
# champion, and the rotation's opponents then all share one pearl schedule, as when the engine's seed was fixed,
# so paired games stay paired (unswbc 1.1.0 draws a random seed per game unless given one). None for a toolkit
# without --seed.
def game_seed(game_map: Path) -> int | None:
    if not seeded_toolkit():
        return None
    return int.from_bytes(hashlib.sha256(game_map.read_bytes()).digest()[:8], "big")


# Whether this toolkit's `unswbc run` takes --seed, asked once: only an answer is kept, so one failed or timed-out
# check leaves the next game to ask again instead of a whole process unseeded (E0183's check, 2026-09-28). The
# first failure in a process is said on stderr, since that game plays unseeded and its pair may not share pearls.
def seeded_toolkit() -> bool:
    global _SEEDED, _SEED_CHECK_FAILED
    if _SEEDED is None:
        try:
            done = subprocess.run([UNSWBC, "run", "--help"], capture_output=True, text=True, timeout=60)
            failure = f"exit {done.returncode}" if done.returncode != 0 else None
        except (OSError, subprocess.TimeoutExpired) as error:
            failure = str(error)
        if failure:
            if not _SEED_CHECK_FAILED:
                _SEED_CHECK_FAILED = True
                print(f"WARNING seed check: `unswbc run --help` failed ({failure}); games play unseeded until it "
                      "answers", file=sys.stderr, flush=True)
            return False
        _SEEDED = "--seed" in done.stdout
    return _SEEDED


_SEEDED: bool | None = None
_SEED_CHECK_FAILED = False


_LAUNCHER_LOCK = threading.Lock()
ENGINE_CACHE_NOTE = re.compile(r"^engine cache: .*$", re.M)  # what engine_cache.py says when it falls back


# How this run starts a game with the engine loaded precompiled from <loop home>/engine-cache (engine_cache.py):
# the command before `run ...` or `record.py ...`. None, said once on stderr, when the cache can't be used here
# (another toolkit, or a coder's sandbox, which can't reach the loop home): games then compile the engine as usual.
# The reason is also kept in <loop home>/engine-cache-off.json for health.py, and removed by the next run that can use
# the cache; a coder's sandbox can write neither, and expects to fall back.
@functools.cache
def _engine_launcher() -> tuple[str, ...] | None:
    folder = remote.loop_home() / "engine-cache"
    note = remote.loop_home() / "engine-cache-off.json"
    reason = engine_cache.warm(folder)
    try:
        if reason:
            temporary = note.with_name(f"{note.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps({"at": time.time(), "reason": reason}) + "\n")
            temporary.replace(note)
        else:
            note.unlink(missing_ok=True)
    except OSError:
        pass
    if reason:
        print(f"engine cache: off for this run ({reason}); every game compiles the engine", file=sys.stderr,
              flush=True)
        return None
    return (sys.executable, str(ENGINE_LAUNCHER), str(folder))


def engine_launcher() -> tuple[str, ...] | None:
    with _LAUNCHER_LOCK:  # games start from several threads; the first warms the cache, once
        return _engine_launcher()


FORK_NOTE = re.compile(r"^fork server: .*$", re.M)  # what fork_server.py says: a fallback, or a check's outcome
FORK_OFF = "fork-server-off.json"  # in the loop home: why this run's games (or one of them) start bots as usual
FORK_CHECKS = "fork-server-checks.json"  # in the loop home: checks played, and the bots a mismatch switched off
# The newest pairs of bots a check file keeps under "repeated" (~60 bytes each) and "nondeterministic_pairs" (~400
# bytes each, one per pair whose check and plain game both differed: rare, the fork server's file had cleared 2 by 29
# Sep). blamed() clears a candidate's opponent by a champion-v-opponent repeat, which a champion may have played only
# at its promotion days before, so a week of pairs must fit: 28 Sep played about 2,300 (measured: 84 results' SPRT
# pairs 314 and the variant sweeps' 850 variants; estimated: under 1,100 from their screens and coders' builds), so
# this holds about 10 days, 1.5 MB of "repeated", rewritten with each check (about 7 a minute).
PAIRS_KEEP = 25_000
NATIVE_CHECKS = "native-judge-checks.json"  # the same for the native judge: its checks, and what switched it off
NATIVE_PLAYED = "native judge: playing"  # what native_judge/play.c says first on stderr
NATIVE_NOTE = re.compile(r"^native judge: off.*$", re.M)  # what native_judge.py says when it falls back


# The shim this run's native games fork their bots with (fork_server.py), built once in <loop home>/fork-server. None,
# said once on stderr, when it can't be used here (another toolkit or system, no compiler, or a coder's sandbox, which
# can't reach the loop home): games then start every bot process as the toolkit does. The reason is kept in
# <loop home>/fork-server-off.json for health.py.
@functools.cache
def _fork_shim() -> Path | None:
    shim, reason = fork_server.warm(remote.loop_home() / "fork-server")
    if reason:
        note_fork_off(reason)
        print(f"fork server: off for this run ({reason}); every bot process starts as usual", file=sys.stderr,
              flush=True)
    return shim


def fork_shim() -> Path | None:
    with _LAUNCHER_LOCK:
        return _fork_shim()


# Keeps `reason` in fork-server-off.json for health.py, which alerts on it for health.FORK_OFF_AGE. Never removed by a
# run that forks: several referee processes run at once, and one's success says nothing of another's fallback.
def note_fork_off(reason: str) -> None:
    note = remote.loop_home() / FORK_OFF
    try:
        temporary = note.with_name(f"{note.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps({"at": time.time(), "reason": reason}) + "\n")
        temporary.replace(note)
    except OSError:
        pass


# What names a bot in fork-server-checks.json: its source folder's digest (remote.source_digest), or a file's. Read
# afresh each game (a millisecond or two): a long-lived process sees the champion's folder change at a promotion.
def bot_digest(bot: Path) -> str:
    if bot.is_dir():
        return remote.source_digest(bot)
    return hashlib.sha256(bot.read_bytes()).hexdigest()[:16]


# The bots of the check games that mismatched (digest: what happened), or None when that can't be read (health.py
# alerts on an unreadable file). Any entry, or None, switches forking off for every game (fork_plan).
def forks_off(name: str = FORK_CHECKS) -> dict | None:
    try:
        found = json.loads((remote.loop_home() / name).read_text()).get("off", {})
    except FileNotFoundError:
        found = {}
    except (OSError, ValueError, AttributeError):
        return None
    return {**found, **_off_here.get(name, {})} if isinstance(found, dict) else None


# The "off" entries record_check couldn't write, by file name: they switch this process off all the same.
_off_here: dict[str, dict] = {}


# The same for the native judge (native-judge-checks.json): any entry, from a check that differed or a judge that
# crashed, or an unreadable file, switches it off for every game (native_plan).
def natives_off() -> dict | None:
    return forks_off(NATIVE_CHECKS)


# The keys (native_judge.process_key) under which a check found the native judge differing: native-judge-checks.json's
# "differed_keys", which removing the entries under "off" leaves (count_native_check), with this process's own that
# couldn't be written; None when the file can't be read or that isn't a list of keys. Read again only when the file
# changed (its modification time and size), as sprt.BaselineCache asks for every saved native row.
def differed_keys() -> set[str] | None:
    path = remote.loop_home() / NATIVE_CHECKS
    try:
        stat = path.stat()
        seen = (str(path), stat.st_mtime_ns, stat.st_size)
        if not _differed_read or _differed_read[0][0] != seen:
            found = json.loads(path.read_text()).get("differed_keys", [])
            keys = set(found) if isinstance(found, list) and all(isinstance(k, str) for k in found) else None
            _differed_read[:] = [(seen, keys)]
        found = _differed_read[0][1]
    except FileNotFoundError:
        found = set()
    except (OSError, ValueError, AttributeError):
        return None
    return None if found is None else {*found, *_differed_here}


_differed_here: set[str] = set()  # differed keys record_check couldn't write
_differed_read: list[tuple[tuple, set[str] | None]] = []  # differed_keys' last read: (the file's stat, its keys)


# Whether a saved game (sprt.BaselineCache's rows, the screen's baselines: their "native", the key the judge played it
# under, to_json) may be wrong, so it is played again rather than reused: one the judge played under a key a check
# found differing (differed_keys), or with no key (a row from before keys), or while that can't be read.
def native_suspect(native: object) -> bool:
    if not native:
        return False
    keys = differed_keys()
    return not isinstance(native, str) or keys is None or native in keys


# What a check that differed from native_report(started)'s games at risk says, or None: an entry under "off" from a
# check (its `check`, not a judge that failed or hung, whose game unswbc run replayed) while this run counted native
# games since its last matching check (`since_check`; none at risk means none of its results came from the judge).
# sprt.py stops its test on one, and driver.native_fault reads the same from sprt.json.
def native_fault(report: dict | None) -> str | None:
    if not report or not report.get("since_check"):
        return None
    return next((str(entry.get("said"))[:300] for entry in report["off"].values() if entry.get("check")), None)


# This process's native games that counted (the judge's own result, from run_game or play_remote: a checked game
# counts its reference instead) since its last check that matched, which a switch-off and native_report say:
# `games` all of them, `since` those since the last match, `checked` when that was. _native_matches: (when, `games`
# then) of each matching check, the newest MATCHES_KEPT, for native_report.
_native_counted = {"games": 0, "since": 0, "checked": None}
_native_matches: list[tuple[float, int]] = []
MATCHES_KEPT = 1000
_native_counted_lock = threading.Lock()


def counted_native(game: RawGame) -> RawGame:
    if game.native and game.returncode == 0:
        with _native_counted_lock:
            _native_counted["games"] += 1
            _native_counted["since"] += 1
    return game


# sprt.py's and screenbatch.py's report of the native judge in their run: this process's counts (_native_counted) and
# the switch-offs since `started` (time.time()), or None when no game played natively and nothing switched it off.
# With a switch-off, `since_check` is every native game at risk: those counted since the last matching check before
# the first of them (a check in flight that matched later resets `since`, not this).
def native_report(started: float) -> dict | None:
    off = {digest: entry for digest, entry in (natives_off() or {}).items()
           if isinstance(entry, dict) and isinstance(entry.get("at"), (int, float)) and entry["at"] >= started}
    with _native_counted_lock:
        counted, matches = dict(_native_counted), list(_native_matches)
    if not counted["games"] and not off:
        return None
    since = counted["since"]
    if off:
        first = min(entry["at"] for entry in off.values())
        since = counted["games"] - next((games for at, games in reversed(matches) if at <= first), 0)
    return {"games": counted["games"], "since_check": since, "checked": counted["checked"], "off": off}


# The "nondeterministic" entries (count_nondeterministic) of the fork server's and the native judge's check files made
# since `started` (time.time()), or naming a bot in `digests` (bot_digest's) whenever made (keep_first keeps a bot's
# first), by digest, each with the check that found it (`by`), for sprt.py's report. A file that can't be read gives
# none (health.py alerts on it).
def nondeterministic_since(started: float, digests: Iterable[str] = ()) -> dict[str, dict]:
    wanted, found = set(digests), {}
    for label, data in check_files():
        entries = data.get("nondeterministic", {})
        for digest, entry in entries.items() if isinstance(entries, dict) else ():
            if isinstance(entry, dict) and (digest in wanted or isinstance(entry.get("at"), (int, float))
                                            and entry["at"] >= started):
                found.setdefault(digest, {**entry, "by": label})
    return found


# The fork server's and the native judge's check files, as (label, contents); one that can't be read is left out.
def check_files() -> list[tuple[str, dict]]:
    files = []
    for name, label in ((FORK_CHECKS, "fork server"), (NATIVE_CHECKS, "native judge")):
        try:
            data = json.loads((remote.loop_home() / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            files.append((label, data))
    return files


# What the check files say of which bots don't repeat their own games, for sprt.py's report: the pairs of bots (each
# "a b", bot_pair's digests) whose check differed while its plain game did too (record_check's
# "nondeterministic_pairs"), made since `started` or naming a bot in `digests`, as {pair: entry with "by"}, and every
# pair whose check repeated ("repeated", whenever made: a digest is one build).
def check_pairs(started: float, digests: Iterable[str] = ()) -> tuple[dict[str, dict], set[str]]:
    wanted, flagged, repeated = set(digests), {}, set()
    for label, data in check_files():
        pairs, same = data.get("nondeterministic_pairs", {}), data.get("repeated", {})
        for pair, entry in pairs.items() if isinstance(pairs, dict) else ():
            if isinstance(entry, dict) and (wanted & set(pair.split()) or isinstance(entry.get("at"), (int, float))
                                            and entry["at"] >= started):
                flagged.setdefault(pair, {**entry, "by": label})
        repeated |= set(same) if isinstance(same, dict) else set()
    return flagged, repeated


# Whether check pairs (check_pairs) show bot `bot` not repeating its own games: a pair of it and another bot whose
# check and plain replay differed, where that other bot is cleared, having repeated a checked game with a third bot
# (a champion-v-X check that matched, for a candidate-v-X one that didn't) and differed with none but `bot`. Either bot
# of a differing pair may be the one at fault (MISSES 86); a bot flagged beside cleared partners only is. Returns what
# its check said, or None.
def blamed(bot: str, flagged: dict[str, dict], repeated: set[str]) -> str | None:
    for pair, entry in flagged.items():
        bots = pair.split()
        if bot not in bots:
            continue
        other = next((b for b in bots if b != bot), bot)
        cleared = other == bot or any(other in key.split() and bot not in key.split() for key in repeated)
        if cleared and not any(other in key.split() and bot not in key.split() for key in flagged):
            return str(entry.get("said") or "a check found it")
    return None


_fork_pairs: set[tuple[str, str]] = set()  # the pairs of bots (digests, either side) fork_check has seen here
_fork_pairs_lock = threading.Lock()


# True for the games that fork_server.py plays both ways and compares: the first of each pair of bots this process
# plays, and a fixed share of the rest (1 in FORK_CHECK_EVERY, by the map's contents, the side and both bots). Never a
# game that records: record.py's recording would take both.
def fork_check(job: Job) -> bool:
    if job.record:
        return False
    a, b = bot_digest(job.a), bot_digest(job.b)
    pair = (min(a, b), max(a, b))
    with _fork_pairs_lock:
        new = pair not in _fork_pairs
        _fork_pairs.add(pair)
    key = hashlib.sha256(job.map.read_bytes() + job.a_side.encode() + a.encode() + b.encode()).digest()
    return new or int.from_bytes(key[:8], "big") % FORK_CHECK_EVERY == 0


_native_pairs: set[tuple[str, str]] = set()  # the pairs of bots whose first check native_check has given out here
_native_compared: set[tuple[str, str]] = set()  # those whose check compared (check_native)
_native_pairs_lock = threading.Lock()


def bot_pair(job: Job) -> tuple[str, str]:
    a, b = bot_digest(job.a), bot_digest(job.b)
    return min(a, b), max(a, b)


# True for the native games also played through `unswbc run` and compared (check_native): each pair of bots' first
# native game in this process (again after a check that was skipped: release_native_check), and 1 in
# NATIVE_CHECK_EVERY of the rest (by the map, the side and both bots).
def native_check(job: Job) -> bool:
    a, b = bot_digest(job.a), bot_digest(job.b)
    pair = (min(a, b), max(a, b))
    with _native_pairs_lock:
        new = pair not in _native_pairs
        _native_pairs.add(pair)
    key = hashlib.sha256(b"native" + job.map.read_bytes() + job.a_side.encode() + a.encode() + b.encode()).digest()
    return new or int.from_bytes(key[:8], "big") % NATIVE_CHECK_EVERY == 0


# A check of `job` skipped (its reference didn't finish, ran a turn out of time, or its host failed): until one of
# its pair compares, the pair's next native game is checked instead.
def release_native_check(job: Job) -> None:
    pair = bot_pair(job)
    with _native_pairs_lock:
        if pair not in _native_compared:
            _native_pairs.discard(pair)


# Whether `job`'s game forks its bots (fork_server.py): (the shim, whether it is a check game), or None when it starts
# every bot process as the toolkit does: a sandbox game, no shim this run, or any check mismatch so far (a mismatch
# says the fork server can change a game, which may not be the two bots' doing alone, so it stays off everywhere until
# someone has read why and removed the entries from fork-server-checks.json).
def fork_plan(job: Job, sandbox: bool) -> tuple[Path, bool] | None:
    if sandbox or (shim := fork_shim()) is None:
        return None
    try:
        if forks_off() != {}:
            return None
        return shim, fork_check(job)
    except OSError:  # a bot or map that can't be read: the game itself will say so
        return None


# Reads what fork_server.py said in a game's output (`where`: "here" or a host's name): a check's outcome goes to
# fork-server-checks.json, and a mismatch also switches the fork server off for every game, with an alert line.
# Returns the last fallback line ("off ..." or "stopped ..."), if any, for the caller to keep (the run's note or
# the host's status).
# Whether a fork_notes fallback line says a fork server didn't answer within fork_server.START_S: in a lowered game
# (GAME_NICE) that is this Mac's CPU going to dev work first, not the fork server failing (the bots start as usual).
def start_timeout(fallback: str) -> bool:
    return "didn't start" in fallback and "timed out" in fallback


# `game` (its replay and output are the check's plain half) and `repeat`, which plays it once more the plain way where
# it played, go to plain_differs before a MISMATCH is blamed on the fork server.
def fork_notes(job: Job, output: str, where: str, game: RawGame | None = None,
               repeat: Callable[[], RawGame] | None = None) -> str | None:
    fallback = None
    for line in FORK_NOTE.findall(ANSI.sub("", output)):
        if line.startswith("fork server: check identical"):
            count_fork_check(job, None)
        elif line.startswith("fork server: MISMATCH"):
            if game is not None and (said := plain_differs(job, game, repeat)):
                count_nondeterministic(job, FORK_CHECKS, "fork server", where, line.strip(), said)
            else:
                count_fork_check(job, f"{where}: {line.strip()}")
        elif not line.startswith("fork server: check skipped"):
            fallback = line.strip()
    return fallback


# Before a check that differed switches the fork server or the native judge off for everyone, the check's plain game
# (`first`) is played once more the same plain way on the same machine (`repeat`): what differs between the two, when
# something does, says the bots don't repeat their own games, which is neither path's fault (MISSES 86: E0287's room
# exit wrote out of bounds on the heap, whose layout differs from process to process, and its fork mismatches were
# blamed on the host). None when they match, or the replay can't say (it failed, ran past its time or a turn out of
# time, or no `repeat`): then the check's difference stands.
def plain_differs(job: Job, first: RawGame, repeat: Callable[[], RawGame] | None) -> str | None:
    if repeat is None or first.returncode is None or out_of_time(first):
        return None
    try:
        again = repeat()
    except (OSError, remote.RemoteError) as error:
        print(f"plain replay of {job.map.name} (a {job.a_side}) failed: {error}", file=sys.stderr, flush=True)
        return None
    if again.returncode is None or infrastructure_failure(again) or out_of_time(again):
        return None
    problems = compare_games(job, first, again)["problems"]
    return "; ".join(problems)[:400] if problems else None


# The experiment a bot belongs to, from its path (<loop home>/runs/E####/cand, a clone bc-wt/E####), else its path.
def experiment_of(bot: Path) -> str:
    found = re.search(r"\bE\d{4,}\b", str(bot))
    return found.group(0) if found else str(bot)


# A check (in `name`, the fork server's or the native judge's file, `label`) that differed on `where` (`mismatch`)
# while the plain game played twice there differed too (`said`): both bots go under "nondeterministic" in that file
# (each entry naming both, "bots") and their pair under "nondeterministic_pairs" (blamed reads which one it was),
# which switches nothing off, and every such check alerts, naming each bot's digest and experiment, since one of them
# plays a game differently each time and its results are noise.
def count_nondeterministic(job: Job, name: str, label: str, where: str, mismatch: str, said: str) -> None:
    path, _ = record_check(name, label, job, f"{where}: {mismatch}; plain twice: {said}", counted=False,
                           key="nondeterministic")
    bots = ", ".join(f"{bot_digest(bot)} ({experiment_of(bot)}, {bot.name})" for bot in (job.a, job.b))
    text = (f"WARNING {label}: a check of {job.map.name} (a {job.a_side}) differed on {where} ({mismatch[:200]}), "
            f"and so did its plain game played twice there ({said[:200]}): one of the bots {bots} doesn't repeat its "
            f"own games (a memory fault, uninitialised state, timing), so its results are noise; the {label} stays on "
            f"(\"nondeterministic\" in {path})")
    print(text, file=sys.stderr, flush=True)
    remote.Host.alert(text)


# Adds one check to fork-server-checks.json; `mismatch` (what was said) records both bots under "off", which switches
# forking off for every game (fork_plan), with an alert.
def count_fork_check(job: Job, mismatch: str | None) -> None:
    path, _ = record_check(FORK_CHECKS, "fork server", job, mismatch)
    if mismatch:
        text = (f"WARNING fork server: {mismatch}; {job.a.name} against {job.b.name} (a {job.a_side}): every game "
                f"starts its bot processes as usual until the entries under \"off\" in {path} are removed. Forked "
                f"games already saved (sprt.BaselineCache's prewarmed baselines among them) stay in use: judge "
                f"whether they must be replayed")
        print(text, file=sys.stderr, flush=True)
        remote.Host.alert(text)


# The same in native-judge-checks.json for the native judge (natives_off): `counted` False for a judge that crashed
# or hung (settle_native), which switches it off without being a check (each entry's `check` says which). A match
# restarts this process's count of native games since its last check (_native_counted); a switch-off keeps that count
# in its entry and says it, and only the one that found the judge on, or its first check that differed, alerts (the
# games in flight then may each switch it off again). A check that differed also keeps this process's key in
# "differed_keys", whose saved games are then played again (native_suspect).
def count_native_check(job: Job, mismatch: str | None, counted: bool = True) -> None:
    with _native_counted_lock:
        since, checked = _native_counted["since"], _native_counted["checked"]
        if counted and mismatch is None:
            _native_counted["since"], _native_counted["checked"] = 0, time.time()
            _native_matches[:] = [*_native_matches, (time.time(), _native_counted["games"])][-MATCHES_KEPT:]
    differed = native_judge.process_key() if counted and mismatch else None
    path, first = record_check(NATIVE_CHECKS, "native judge", job, mismatch, counted,
                               {"native_since_check": since, "check": counted}, differed)
    if mismatch:
        last = time.strftime("%H:%M:%S", time.localtime(checked)) if checked else "none yet"
        saved = (f"; saved games it played under key {differed} are played again from now on (\"differed_keys\", "
                 f"which stays when \"off\" is cleared), and an SPRT or screen with native games at risk errors to "
                 f"re-evaluate" if differed else "")
        text = (f"WARNING native judge: {mismatch}; {job.a.name} against {job.b.name} (a {job.a_side}): every game "
                f"plays through unswbc run until the entries under \"off\" in {path} are removed. {since} native "
                f"games counted in this run since its last matching check ({last}){saved}")
        print(text, file=sys.stderr, flush=True)
        if first:
            remote.Host.alert(text)


# Adds one check (`counted`) to <loop home>/`name`, and with `mismatch` both bots under `key` (each entry with
# `extra`): "off", or "nondeterministic" (count_nondeterministic), which switches nothing off; and `differed` (a native
# judge key) to its "differed_keys" for good. Returns its path and whether this switched it off (nothing was under
# `key` before) or is its first check that differed (`check` in `extra`: a judge that crashed first mustn't hide one,
# keep_first). An "off" mismatch it can't write still switches this process off (_off_here, _differed_here), with an
# alert.
def record_check(name: str, label: str, job: Job, mismatch: str | None, counted: bool = True,
                 extra: dict | None = None, differed: str | None = None, key: str = "off") -> tuple[Path, bool]:
    import fcntl

    path = remote.loop_home() / name
    pair = " ".join(bot_pair(job))
    entries = {bot_digest(bot): {"at": time.time(), "bot": str(bot), "map": str(job.map), "a_side": job.a_side,
                                 "said": mismatch, "bots": [bot_digest(job.a), bot_digest(job.b)], **(extra or {})}
               for bot in (job.a, job.b)} if mismatch else {}
    first = False
    try:
        with open(path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(path.read_text()) if path.exists() else {}
                if not isinstance(data, dict) or not isinstance(data.get("off", {}), dict) \
                        or not isinstance(data.get("differed_keys", []), list) \
                        or not all(isinstance(data.get(k, {}), dict) for k in ("repeated", "nondeterministic_pairs")):
                    raise ValueError("not a JSON object with an \"off\" object and a \"differed_keys\" list")
            except ValueError as error:  # never written over: it may hold the only record of a mismatch
                raise OSError(f"it can't be read ({error}), so this check isn't recorded; fix or remove it") from error
            data["checks"] = data.get("checks", 0) + counted
            data["identical"] = data.get("identical", 0) + (counted and mismatch is None)
            data["updated"] = time.time()
            if mismatch:
                first = keep_first(data.setdefault(key, {}), entries)
                if differed and differed not in data.setdefault("differed_keys", []):
                    data["differed_keys"].append(differed)
            if mismatch and key == "nondeterministic":  # by pair too: a bot's first entry alone hides its others
                pairs = data.setdefault("nondeterministic_pairs", {})
                pairs.setdefault(pair, entries[bot_digest(job.a)])
                for old in list(pairs)[:-PAIRS_KEEP]:
                    del pairs[old]
            elif counted and not mismatch:  # both bots played this game the same twice: blamed() clears them by it
                repeated = data.setdefault("repeated", {})
                repeated.pop(pair, None)
                repeated[pair] = time.time()
                for old in list(repeated)[:-PAIRS_KEEP]:
                    del repeated[old]
            temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(data, indent=1) + "\n")
            temporary.replace(path)
    except OSError as error:
        # health.py alerts on an unreadable file (and no game forks, or plays natively, meanwhile); a mismatch alerts
        print(f"WARNING {label}: cannot count a check in {path}: {error}", file=sys.stderr, flush=True)
        if mismatch and key == "off":  # nothing else would switch it off
            first = keep_first(_off_here.setdefault(name, {}), entries)
            if differed:
                _differed_here.add(differed)
            if first:
                remote.Host.alert(f"WARNING {label}: switched off in this process only: its \"off\" entry can't be "
                                  f"written to {path} ({error}); other processes still use it")
    return path, first


# Adds `entries` to `off` (record_check's "off"): a bot's first entry stands, why it was switched off, unless it wasn't
# a check that differed (`check`) and the new one is, which replaces it. True when `off` was empty or this is its first
# such check (what alerts).
def keep_first(off: dict, entries: dict) -> bool:
    def checked(entry: object) -> bool:
        return isinstance(entry, dict) and bool(entry.get("check"))

    first = not off or any(map(checked, entries.values())) and not any(map(checked, off.values()))
    for digest, entry in entries.items():
        if digest not in off or checked(entry) and not checked(off[digest]):
            off[digest] = entry
    return first


# Runs one game through `unswbc run` (through engine_cache.py's launcher when the engine cache is on, and
# fork_server.py's when the game forks its bots: fork_plan), writing its replay to `replay_path`. `started` gets the
# process, which leads its own process group (with the bots it spawns and their fork servers), so a scheduler that
# stops early can kill the whole game.
# A job with `record` runs record.py, which plays the same game and also writes the recording. A lowered game (`nice`
# above 0, or `watch`: this process's own niceness is) is read as it plays (watch_game).
# `native` None (the loop's games) plays it through the native judge where native_plan says so; a judge that fails
# the game its own way is replayed through `unswbc run` (settle_native), and a checked game (native_check) is played
# both ways and compared (check_native). True or False (native_fidelity.py's two ways) plays it that way alone.
def run_game(job: Job, sandbox: bool, replay_path: Path,
             started: Callable[[subprocess.Popen], None] | None = None, nice: int = 0,
             watch: bool = False, native: bool | None = None, fork: bool = True) -> RawGame:
    first, second = (job.a, job.b) if job.a_side == "A" else (job.b, job.a)
    launcher = engine_launcher()
    plan = fork_plan(job, sandbox) if fork else None
    seed = game_seed(job.map)

    # The `unswbc run` command writing its replay to `path`.
    def plain(path: Path) -> list[str]:
        game = [str(job.map), str(first), str(second), "-o", str(path)]
        if job.record and not sandbox:
            tail = [str(RECORDER), *game, "--team", job.a_side, "--recording", str(job.record)]
        else:
            tail = ["run", *game]
        if launcher:
            tail = [*launcher[1:], *tail]
        if plan:
            tail = [str(FORK_LAUNCHER), "--shim", str(plan[0]), *(["--check"] if plan[1] else []), *tail]
        cmd = [UNSWBC, *tail] if tail[0] == "run" else [sys.executable, *tail]
        return [*cmd, *(["--seed", str(seed)] if seed is not None else []), *(["--sandbox"] if sandbox else [])]

    timeout = GAME_TIMEOUT_S * (2 if plan and plan[1] else 1)  # a check game plays twice
    # A lowered game's toolkit prints unbuffered, so watch_game sees each turn's line as it happens.
    watch = watch or bool(nice)

    def play(cmd: list[str], path: Path = replay_path) -> RawGame:
        start = time.monotonic()
        proc = subprocess.Popen(nice_command(cmd, nice), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"} if watch else None)
        if started:
            started(proc)
        if watch:
            return watch_game(proc, path, timeout, start)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_game(proc)
            out, _ = proc.communicate()
            return RawGame((out or "") + f"\ngame exceeded {timeout}s", None, None, time.monotonic() - start)
        replay = path.read_bytes() if path.is_file() else None
        return RawGame(out + err, proc.returncode, replay, time.monotonic() - start)

    judged = None
    if native_plan(job, sandbox, plan, native) and (judge := native_judge.local_judge(remote.loop_home()
                                                                                        / "native-judge")):
        judged = native_judge.command(judge, job.map, first, second, replay_path, seed,
                                      remote.loop_home() / "engine-cache", plan and plan[0])
    if judged is None:
        return play(plain(replay_path))
    game = play(judged)
    if native is not None:
        return game
    # A judge that failed, or ran past its time (not a turn out of time: a lowered game's starving, which try_here
    # replays at normal priority), plays once through unswbc run. Past its time lowered (`watch`) may be starving too:
    # it comes back as it is, for try_here to replay at normal priority, natively again; and at this process's own
    # priority it blames the judge only when that isn't lowered either.
    hung = game.returncode is None and not out_of_time(game)
    if hung and watch:
        return game
    if why := judge_failure(game) or (hung and f"the native judge ran past {timeout}s"):
        with contextlib.suppress(OSError):
            replay_path.unlink(missing_ok=True)
        return settle_native(job, game, play(plain(replay_path)), why, "here", blame=not hung or own_nice() <= 0)
    if game.native and game.returncode == 0 and native_check(job):
        path = replay_path.with_name(f"{replay_path.name}.{uuid.uuid4().hex[:8]}.check")
        try:
            reference = play(plain(path), path)

            def repeat() -> RawGame:
                again = path.with_name(f"{path.name}.again")
                try:
                    return play(plain(again), again)
                finally:
                    again.unlink(missing_ok=True)

            if check_native(job, reference, game, "here", repeat):  # the check's own game counts: unswbc run's
                if reference.replay is None:
                    replay_path.unlink(missing_ok=True)
                else:
                    os.replace(path, replay_path)
                return reference
        finally:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
    return counted_native(game)


# Whether a game plays through the native judge: a native game (not the sandbox), not recording, not a fork server
# check game (`plan`: fork_plan's, whose two paths fork_server.py compares itself), and `native`, or when that is None
# NATIVE_JUDGE, nothing under "off" in native-judge-checks.json (natives_off), this key's fidelity gate passed at
# `place`: this Mac's platform, or remote.Host.native_place for a host's game (native_gated), the key is
# still the one this process took at its first native game (native_judge.process_key: its code is the gated code), and
# no check ever found it differing (differed_keys, readable): clearing "off" lets only a changed build, a new key with
# its own gate, play natively again.
def native_plan(job: Job, sandbox: bool, plan: tuple[Path, bool] | None, native: bool | None = None,
                place: str = remote.PLATFORM) -> bool:
    if sandbox or job.record or (plan and plan[1]):
        return False
    if native is not None:
        return native
    if not NATIVE_JUDGE or natives_off() != {}:
        return False
    now = native_judge.key()  # and only while it is the key this process took (native_judge.process_key)
    if now is None or now != native_judge.process_key() or (differed := differed_keys()) is None or now in differed:
        return False
    return native_gated(place, now)


# Whether key `now` passed its gate at `place` (native_judge.enabled). This Mac's own games (remote.PLATFORM) go on
# its own row when it has one, a failed one too, else on its Mac hosts' (remote.MAC_HOSTS): a Mac host is sent this
# Mac's judge binary and must have its engine and toolkit glue (remote.Host.native_files), so its gate played the same
# build on the same inputs, OS and CPU family, and this Mac's own gate, waiting on its load share, may never find room
# (29 Sep). Here, not in native_judge.py, whose own bytes are part of the key (native_judge.launcher_digest).
def native_gated(place: str, now: str) -> bool:
    home = remote.loop_home()
    if place == remote.PLATFORM and place not in ((native_judge.gates(home).get("keys") or {}).get(now) or {}):
        place = remote.MAC_HOSTS
    return native_judge.enabled(home, place, now)


# Why a native game failed through the judge itself, or None: it died by a signal (died_by) other than
# MACHINE_SIGNALS, or exited with an error that isn't an OS error (ENGINE_OS_ERROR: infrastructure_failure replays
# those, as it does a game killed by MACHINE_SIGNALS). A game past its time (returncode None: its callers replay one
# through unswbc run), a game the judge didn't play (it fell back itself) or one that played is none.
def judge_failure(game: RawGame) -> str | None:
    if not game.native or game.returncode in (0, None):
        return None
    if signum := died_by(game):
        return None if signum in MACHINE_SIGNALS else f"the native judge died by signal {signum}"
    if ENGINE_OS_ERROR.search(ANSI.sub("", game.stdout)):
        return None
    return f"the native judge exited {game.returncode}: {error_line(game) or game.stdout.strip()[-200:]}"


MACHINE_SIGNALS = (signal.SIGKILL, signal.SIGTERM)  # usually the machine's (out of memory, a spot VM going)


def error_line(game: RawGame) -> str | None:
    return next((line.strip() for line in reversed(ANSI.sub("", game.stdout).splitlines())
                 if line.startswith("error: ")), None)


# The game a judge failed or ran past its time with (`why`) as `unswbc run` played it (`plain`), which is the result.
# When `unswbc run` failed it the same way (exit code and error line: a map or bot the toolkit refuses too), the judge
# only said what the toolkit says, and when it didn't finish the game either (past its time, stopped, or failed through
# the machine: infrastructure_failure) nothing is known of the judge; otherwise the judge is at fault, and it is
# switched off for every game with an alert (count_native_check), until someone reads why. `where`: "here" or the
# host's name. `blame` False (a game past its time at a lowered priority, which may have starved) switches nothing
# off.
def settle_native(job: Job, game: RawGame, plain: RawGame, why: str, where: str, blame: bool = True) -> RawGame:
    if not blame or plain.returncode is None or infrastructure_failure(plain):
        return plain
    if (plain.returncode, error_line(plain)) != (game.returncode, error_line(game)):
        played = "played it" if plain.returncode == 0 else f"exited {plain.returncode}"
        count_native_check(job, f"{where}: {why}; unswbc run {played}", counted=False)
    return plain


# Compares a checked game played through `unswbc run` (`reference`) and natively: counted in
# native-judge-checks.json, and any difference in replay bytes, result or log (compare_games) switches the judge off
# for every game. True when compared: the caller then counts the reference, never the native game. A game whose
# reference ran a turn out of time (starved, perhaps), ran past its time or failed through the machine
# (infrastructure_failure) isn't compared, and the native game counts, the pair's next game checked instead
# (release_native_check); a turn out of time only natively is a difference like any other. A difference is first
# checked against the reference played again (`repeat`, plain_differs): the bots' own, it switches nothing off.
def check_native(job: Job, reference: RawGame, game: RawGame, where: str,
                 repeat: Callable[[], RawGame] | None = None) -> bool:
    if reference.returncode is None or infrastructure_failure(reference) or out_of_time(reference):
        release_native_check(job)
        return False
    pair = bot_pair(job)
    with _native_pairs_lock:
        _native_compared.add(pair)
    problems = compare_games(job, reference, game)["problems"]
    mismatch = f"check differs: {'; '.join(problems)[:400]}"
    if problems and (said := plain_differs(job, reference, repeat)):  # `repeat` plays the reference's way once more
        count_nondeterministic(job, NATIVE_CHECKS, "native judge", where, mismatch, said)
        return True
    count_native_check(job, f"{where}: {mismatch}" if problems else None)
    return True


# Lines that aren't the game's: the launchers' notes, and the toolkit's own update and replay-viewer notices (a
# run without UNSWBC_NO_UPDATE and UNSWBC_NO_VSCODE, which the loop's own launches set: driver.py, maintenance.py).
NOTES = re.compile(r"^(engine cache|fork server|native judge): |^unswbc \S+ is out|^  uv |^cpu self |"
                   r"^installed the replay viewer |^replay viewer: |^no VS Code, Cursor or VSCodium found|^  \S+ -m pip install --upgrade unswbc$")
TIMINGS = [(re.compile(r"last: \S+  eta: \S+"), "last: T  eta: T"), (re.compile(r"\) \(\S+\)$"), ") (T)"),
           (re.compile(r"^wrote replay: .*"), "wrote replay: R")]


# A game's output as compared both ways: no colour, notes or blank lines, timings and the replay's path masked.
def normal_log(stdout: str) -> list[str]:
    lines = []
    for line in ANSI.sub("", stdout).splitlines():
        line = line.rstrip()
        if not line or NOTES.match(line):
            continue
        for pattern, text in TIMINGS:
            line = pattern.sub(text, line)
        lines.append(line)
    return lines


# Where two replays first differ: the first event (index, kind, round) or the part of the replay that differs.
def first_difference(a: bytes | None, b: bytes | None) -> str:
    if a is None or b is None:
        return f"replay missing ({'reference' if a is None else 'native'})"
    try:
        ra = REPLAY_SCHEMA.Replay.from_bytes_packed(a, traversal_limit_in_words=2**62)
        rb = REPLAY_SCHEMA.Replay.from_bytes_packed(b, traversal_limit_in_words=2**62)
    except Exception as error:  # pycapnp raises its own KjException
        return f"unreadable replay: {error}"
    for field in ("map", "botA", "botB", "formatVersion"):
        if getattr(ra, field) != getattr(rb, field):
            return f"header field {field}"
    round_now = None
    for index, (ea, eb) in enumerate(zip(ra.events, rb.events)):
        if ea.which() == "roundStart":
            round_now = ea.roundStart.round
        if str(ea) != str(eb):
            return f"event {index} ({ea.which()} vs {eb.which()}) in round {round_now}"
    if len(ra.events) != len(rb.events):
        return f"event count {len(ra.events)} vs {len(rb.events)} (same up to the shorter), last round {round_now}"
    return "result or bytes only (every event reads the same)"


# parse_game's result for comparing two ways of one game: where, how long and which way it played left out.
def result_dict(job: Job, game: RawGame) -> dict:
    with PARSE_LOCK:
        parsed = asdict(parse_game(job, game))
    for key in ("seconds", "host", "cpu", "native"):
        parsed.pop(key)
    return parsed


# One game played two ways (`reference` through `unswbc run`, `native`): the same replay bytes, the same result as
# parse_game reads it (seconds aside) and the same log line for line but for timings, the replay's path and the
# launchers' notes. {"match", "problems", "result": the reference's parsed result}.
def compare_games(job: Job, reference: RawGame, native: RawGame) -> dict:
    problems = []
    if reference.replay != native.replay:
        with PARSE_LOCK:
            problems.append(f"replay differs: {first_difference(reference.replay, native.replay)}")
    if (ra := result_dict(job, reference)) != (rb := result_dict(job, native)):
        problems.append(f"result differs: {ra} vs {rb}")
    la, lb = normal_log(reference.stdout), normal_log(native.stdout)
    if la != lb:
        at = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
        problems.append(f"log differs at line {at}: {la[at] if at < len(la) else '<end>'!r} vs "
                        f"{lb[at] if at < len(lb) else '<end>'!r}")
    return {"match": not problems, "problems": problems, "result": ra}


# `command` run at niceness `nice` through /usr/bin/nice (as it was for 0), which the engine, the bots and their fork
# server inherit. Where a sandbox refuses it, nice warns and runs the command at the priority it had (loop/native.py's
# lower_games probes for that first). A child's preexec_fn can't do it: this scheduler runs threads, and a forked
# child of a threaded process may deadlock before exec.
def nice_command(command: list[str], nice: int) -> list[str]:
    return ["/usr/bin/nice", "-n", str(nice), *command] if nice else command


# This process's own niceness, which its games inherit (/usr/bin/nice adds GAME_NICE to it).
def own_nice() -> int:
    return os.getpriority(os.PRIO_PROCESS, 0)


# Reads a lowered game's output as it comes and stops the game at its first turn out of time (out_of_time's line):
# starved at a lowered priority, perhaps, and with every later turn waiting out its 10 s wall limit the game would hold
# the results behind it (an SPRT reads them in order) for up to GAME_TIMEOUT_S. Such a game, or one past `timeout`,
# comes back with no exit code or replay, and the scheduler plays it again at normal priority (Scheduler.try_here), a
# headless worker's at its own niceness, which it can't raise. The replay is noted in game-retries.json's "niced" rows
# (health.niced_replays) unless it ran a turn out of time too (the bot's own); a lowered game's fork-server start
# timeout doesn't count as the fork server off. A sandbox refusing `nice` keeps the priority it has.
def watch_game(proc: subprocess.Popen, replay_path: Path, timeout: float, start: float) -> RawGame:
    out: list[str] = []
    err: list[str] = []
    starved, seen = [], threading.Event()  # seen: a turn out of time, or the end of the output

    def read_out() -> None:
        try:
            for line in proc.stdout:
                out.append(line)
                if not starved and turn_out_of_time(line):
                    starved.append(line)
                    seen.set()
        finally:
            seen.set()

    readers = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)]
    for reader in readers:
        reader.start()
    ended = seen.wait(max(0.0, start + timeout - time.monotonic()))
    if starved or not ended:
        kill_game(proc)
    try:  # a game that closed its output still has the rest of its time limit to exit
        proc.wait(timeout=30 if starved or not ended else max(5.0, start + timeout - time.monotonic()))
    except subprocess.TimeoutExpired:
        if ended and not starved:
            ended = False
            kill_game(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=30)
    for reader in readers:  # a process that left the game's group could hold its pipes open: never wait for it
        reader.join(timeout=2)
    if not any(reader.is_alive() for reader in readers):
        proc.stdout.close()
        proc.stderr.close()
    text = "".join(out) + "".join(err)
    if starved or not ended:
        return RawGame(text + ("" if starved else f"\ngame exceeded {timeout}s"), None, None, time.monotonic() - start)
    replay = replay_path.read_bytes() if replay_path.is_file() else None
    return RawGame(text, proc.returncode, replay, time.monotonic() - start)


# True when a game's output shows a turn that ran out of time (the toolkit's wall limit, unswbc/turn.py's "ran out of
# time", printed as "round N: bot X (team T) ran out of time"): at a lowered priority that can be starvation, not the
# bot.
def out_of_time(game: RawGame) -> bool:
    return any(map(turn_out_of_time, game.stdout.splitlines()))


def turn_out_of_time(line: str) -> bool:
    return bool((m := BOT_ERROR_LINE.match(ANSI.sub("", line).rstrip("\n"))) and "ran out of time" in m.group(2))


def kill_game(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# Builds each bot once, the way `unswbc run` would, so parallel games don't race on `.unswbc-build/`.
# Returns what a remote host runs of each bot: its native executable, or with `sandbox` its wasm build; None for bots
# that compile to neither (a Python bot), which play only here. Uses the toolkit's own resolver (private, but the
# toolkit version is pinned per experiment).
def build_bots(bots: list[Path], sandbox: bool) -> dict[Path, Path | None]:
    from unswbc.errors import UserError
    from unswbc.run import _resolve

    native: dict[Path, Path | None] = {}
    for bot in dict.fromkeys(bots):
        try:
            command, _, kind = _resolve(str(bot), sandbox)
        except UserError as error:
            raise SystemExit(f"cannot build {bot}: {error}") from error
        native[bot] = Path(command[0]) if kind in ("c/c++", "wasm") and len(command) == 1 else None
    return native


# How many games may run on this machine now, counting the `active` ones already running there: a fixed
# number, or for "auto" max(1, min(8, floor(7 - other load))). Never blocks; native.py swaps in the loop's
# gated version, which answers 0 while this Mac is busy with other work.
def game_slots(workers: int | str, active: int = 0) -> int:
    if workers == "auto":
        return max(1, min(8, math.floor(7 - max(0.0, os.getloadavg()[0] - active))))
    return int(workers)


# How many more games this user's process limit (kern.maxprocperuid) leaves room for here, for a scheduler with
# `active` games of its own here, or None where it isn't measured. None here; native.py swaps in the loop's
# (home.game_room), whose zero game_slots also answers 0 for: Scheduler.replay_here's one game past the slots is only
# for a zero from load, since past the process limit every fork of the user's fails.
def fork_room(active: int = 0) -> int | None:
    return None


# record.file_digest of a saved recording, or None when there is none.
def saved_digest(path: Path) -> str | None:
    try:
        return record.file_digest(path.read_bytes())
    except OSError:
        return None


# One job in the scheduler's queue: a game started (`playing`), a predicted job waiting for a check to start (since
# `queued`) or being checked, whose record.Check `checked` holds once done, or (no `future`) a job waiting for a
# slot. `forced`: audited as the run's first identical game. `play` is the job as played (an audit records to `audit`).
@dataclass
class Entry:
    job: Job
    path: Path
    future: Future | None = None
    playing: bool = False
    queued: float | None = None
    checked: record.Check | None = None
    forced: bool = False
    play: Job | None = None
    audit: Path | None = None

    def audited(self) -> bool:
        return self.forced or self.job.predict.audit

    # True when the entry's game must now be played: it is waiting for a slot, or its check is in (Scheduler.settle)
    # and it differed, it is an audit, or prediction has stopped since.
    def to_play(self, predicting: bool) -> bool:
        if self.queued is not None:
            return False
        if self.future is None:
            return True
        if self.playing or self.checked is None:
            return False
        return not (self.checked.identical and not self.audited() and predicting)


# Plays games here and on remote hosts ("auto" runs only), starting each game as soon as a slot is
# free, including while an earlier game still runs (up to `ahead` games past it, AHEAD). `play` yields results in job
# order however the games finish, so callers see exactly the results a serial run would give. Stopping early (closing
# the generator) kills every game still running.
# `same_platform` keeps games off hosts whose platform differs from this Mac's, for callers that compare games
# move for move across machines (a build there can decide differently).
# Sandbox games play on hosts only with `sandbox_hosts` (the loop's CPU gates, referee/cpu.py --hosts, whose callers
# keep the hosts' shares), and then only on hosts of this Mac's platform, where this Mac's wasm builds were measured to
# price the same (2026-09-29: the champion's 13 CPU-gate games on mm and mm1 gave this Mac's per-game p99, max and
# p50, printed to 0.1M, and its 253,814 turns and results exactly: the toolkit meters points inside the wasm, not by a
# clock); otherwise here only, as before, so a coder's own CPU check never takes a host's whole room.
class Scheduler:
    def __init__(self, workers: int | str, sandbox: bool, native: dict[Path, Path | None],
                 same_platform: bool = False, sandbox_hosts: bool = False, alerts: bool = False):
        self.workers, self.sandbox, self.native = workers, sandbox, native
        # `alerts` (the SPRT's run): its host rests and game replays also go to host-alerts.log (say), so a stall
        # behind a lost head game shows while it happens. `head` is the replay path of the game the results wait on.
        self.alerts, self.head = alerts, None
        self.same_platform = same_platform or sandbox
        self.hosts = remote.load_hosts() if workers == "auto" and (sandbox_hosts or not sandbox) else []
        self.local = 0
        self.changed = threading.Condition()
        for host in self.hosts:  # a host measured in the background may have room now (remote.Host.capacity)
            host.wakers.append(self.wake)
        self.procs: set[subprocess.Popen] = set()
        self.remote_games: set[tuple[remote.Host, str]] = set()
        self.stopped = False
        # Games a host couldn't play that wait for a slot here (replay_here), counted as this Mac's work so new games
        # don't take the next free slot first, and those of them started past the slots (at most one at a time in each referee process, so a Mac running several can go a few over).
        self.waiting, self.over = 0, 0
        # Games started but not yet yielded (AHEAD): the slots bound the games in flight, this bounds how far past a
        # slow head game the rest may run, and so the games a caller that stops at a decision (the SPRT) drops.
        self.ahead = AHEAD * ((os.cpu_count() or 1) + sum(host.max for host in self.hosts))
        self.began, self.finished, self.played_here, self.saved = time.monotonic(), 0, 0, 0.0
        self.stats, self.checking, self.first_audit = PredictStats(), 0, False
        self.save()  # a fresh record per run, so no one reads an earlier run's rests

    # Rewrites the status file: always on a rest or at the start and end, else at most every STATUS_S, also while
    # no game finishes. Besides the run's progress it holds the games in flight now, here (`running`, checks
    # included) and on each host (`active`), and each host's cores from its last load report (`cores`, 0 before
    # one), which the loop's utilisation record reads (loop/throughput.py).
    def save(self, force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self.saved < STATUS_S:
            return
        self.saved = now
        minutes = max(now - self.began, 1.0) / 60
        remote.save_status(self.hosts, run={"games": self.finished, "per_minute": round(self.finished / minutes, 1),
                                            "here": self.played_here, "running": self.local + self.checking,
                                            "active": {h.name: h.active for h in self.hosts},
                                            "cores": {h.name: h.cores for h in self.hosts}})

    # Jobs with a Prediction are first checked here (at most CHECKS at once, each taking a local slot, so this Mac's
    # load gate and --workers N count them): a job whose bot answers every recorded turn as recorded yields the
    # recorded result unplayed, once it reaches the head of the queue, unless prediction has stopped by then;
    # anything else, every audit and the run's first identical game (audited too, so every run that predicts has
    # one) is played. Audits are compared as they are yielded, so a mismatch stops prediction for every job after
    # it in job order. A job whose check can't start within CHECK_WAIT_S is played; other jobs never wait for checks.
    # `jobs` may yield None for "none ready yet" (loop/screenbatch.py: a stage-2 game waiting for its entry's stage-1
    # results): it is asked again each pass, so after every result, and yields a job or ends once one is decided. A
    # stream still not ready with no game left running would wait forever, so that raises. With `drop`, each game's
    # replay (and an audit's recording) is deleted once the caller asks for the next result, having taken its own.
    def play(self, jobs: Iterable[Job | None], folder: Path, drop: bool = False) -> Iterator[GameResult]:
        stream, count = iter(jobs), 0

        def pull() -> tuple[int, Job] | object | None:  # (index, job), NOT_READY, or None once the jobs are done
            nonlocal count
            try:
                job = next(stream)
            except StopIteration:
                return None
            if job is None:
                return NOT_READY
            count += 1
            return count - 1, job

        upcoming = pull()  # read one job ahead, so an exhausted list ends the loop without a slot
        running: deque[Entry] = deque()
        try:
            while upcoming or running:
                if upcoming is NOT_READY:
                    upcoming = pull()
                    if upcoming is NOT_READY and not running:
                        raise RuntimeError("the job stream is waiting for results with no game running")
                moved = False  # anything started or yielded this pass: then look again before waiting
                self.head = running[0].path if running else None
                for entry in running:
                    self.settle(entry)
                for entry in running:  # jobs waiting for a check, earliest first
                    if entry.queued is None:
                        continue
                    if not self.predicting() or time.monotonic() - entry.queued > CHECK_WAIT_S:
                        entry.queued = None  # played instead
                    elif self.check_slot():
                        entry.queued, entry.future, moved = None, self.start_check(entry.job), True
                # A game that records and finds no slot waits for one on this platform: every other one that records
                # needs the same, so the rest aren't asked this pass (up to `ahead` of them, each asking game_slots,
                # which native.py's gated version answers by running vm_stat).
                recording_full = False
                for entry in running:  # jobs waiting to be played go first, in job order
                    if entry.to_play(self.predicting()):
                        if self.start_entry(entry, recording_full):
                            moved = True
                        elif entry.play.record is None:
                            break  # no slot at all; one that records may only be waiting for this platform's
                        else:
                            recording_full = True
                while upcoming and upcoming is not NOT_READY and len(running) < self.ahead:
                    index, job = upcoming
                    entry = Entry(job, folder / f"{index:04d}-{job.map.stem}-a{job.a_side}.replay")
                    if job.predict and self.predicting() and not self.sandbox:
                        if self.check_slot():
                            entry.future = self.start_check(job)
                        else:
                            entry.queued = time.monotonic()
                    elif not self.start_entry(entry, recording_full):
                        if job.record is None:
                            break
                        recording_full = True
                    running.append(entry)  # started, being checked, or recording and waiting for this platform
                    upcoming, moved = pull(), True
                while running and (result := self.finish(running[0])) is not None:
                    done = running.popleft()
                    self.finished += 1
                    self.save(force=False)
                    moved = True
                    yield result
                    if drop:
                        for path in (done.path, done.path.with_suffix(".recording")):
                            path.unlink(missing_ok=True)
                if moved:
                    continue
                self.save(force=False)  # the in-flight counts stay current while every running game is long
                with self.changed:
                    if running and running[0].future is not None and not running[0].future.done():
                        self.changed.wait(timeout=5)
                    elif running:
                        self.changed.wait(timeout=1)  # the head waits for a slot to be played in
                    elif upcoming:
                        self.changed.wait(timeout=15)  # nothing may start yet: this Mac is busy
        finally:
            self.stop()

    def predicting(self) -> bool:
        return self.stats.off is None

    # True when a check may start now: fewer than CHECKS running, and a local slot free (checks run on this Mac).
    def check_slot(self) -> bool:
        work = self.local + self.checking + self.waiting
        return self.checking < CHECKS and work < game_slots(self.workers, work)

    # Starts the entry's game if a slot is free; False if none is, or if it records and `recording_full` (a game that
    # records found no slot this pass). An audit is played on this platform and recorded.
    def start_entry(self, entry: Entry, recording_full: bool = False) -> bool:
        if entry.checked is not None and entry.checked.identical and entry.audited():
            entry.audit = entry.path.with_suffix(".recording")
            entry.play = replace(entry.job, predict=None, record=entry.audit)
        else:
            entry.play = replace(entry.job, predict=None)
        if entry.play.record and recording_full:
            return False
        slot = self.free_slot(entry.play)
        if slot is None:
            return False
        if entry.play.record:  # drop any older file there first, so a file there afterwards is this game's
            try:
                entry.play.record.unlink(missing_ok=True)
            except OSError as error:
                print(f"{entry.job.map.name} (a {entry.job.a_side}) plays unrecorded: cannot clear "
                      f"{entry.play.record}: {error}", file=sys.stderr, flush=True)
                entry.play = replace(entry.play, record=None)
        entry.future, entry.playing = self.start(slot, entry.play, entry.path), True
        return True

    def settle(self, entry: Entry) -> None:
        if not entry.playing and entry.checked is None and entry.future is not None and entry.future.done():
            entry.checked = entry.future.result()

    # The head entry's result once it has one, else None.
    def finish(self, entry: Entry) -> GameResult | None:
        if entry.future is None or not entry.future.done():
            return None
        if not entry.playing:
            self.settle(entry)
            if entry.checked.identical and not self.first_audit and self.predicting():
                entry.forced = True  # nothing predicted before an audit has passed
            if not (entry.checked.identical and not entry.audited() and self.predicting()):
                return None  # it must be played; the loop starts it
            self.stats.predicted += 1
            return replace(entry.job.predict.result, map=str(entry.job.map), a_side=entry.job.a_side, seconds=0.0,
                           predicted=True)
        result = entry.future.result()
        if entry.play.record and result.error:
            entry.play.record.unlink(missing_ok=True)
        elif entry.play.record and (digest := saved_digest(entry.play.record)):
            result = replace(result, recording=digest)
        if entry.audit is not None and not result.error:
            same = self.same_game(entry.job.predict, result, entry.audit)
            if same is None:  # nothing to compare: not an audit, so the next identical game at the head is one
                print(f"audit of {entry.job.map.name} (a {entry.job.a_side}) inconclusive: no recording to compare",
                      file=sys.stderr, flush=True)
                return result
            self.stats.audited += 1
            self.first_audit = True
            if not same:
                self.stats.audit_mismatches += 1
                self.stop_predicting(f"audit mismatch on {entry.job.map.name} (a {entry.job.a_side}): the game played "
                                     f"differed from the recording it was checked against", alert=True)
        return result

    # Counts a finished check as soon as it is done (whether a game is predicted or played changes no result, so
    # prediction may stop at any point, and the sooner the floor is seen the fewer checks are wasted).
    def count_check(self, check: record.Check) -> None:
        self.stats.checks += 1
        self.stats.identical += check.identical
        self.stats.check_cpu += check.cpu
        if (self.stats.checks == FLOOR_CHECKS and self.stats.identical < FLOOR_SHARE * FLOOR_CHECKS
                and self.predicting()):
            self.stop_predicting(f"only {self.stats.identical} of the first {FLOOR_CHECKS} checks were identical")

    # True when the audit game is the recorded one: the same result and the same turns on bot a's side; False when
    # either differs; None when there is nothing to compare the turns with (the audit's recording couldn't be saved or
    # read, or the prediction's was replaced since).
    @staticmethod
    def same_game(prediction: Prediction, result: GameResult, recording: Path) -> bool | None:
        fields = lambda r: (r.winner, r.end, r.rounds, r.a and (r.a.alive, r.a.longest, r.a.total),
                            r.b and (r.b.alive, r.b.longest, r.b.total))
        if fields(result) != fields(prediction.result):
            return False
        try:
            expected = record.Recording.load(prediction.recording, prediction.digest)
            played = record.Recording.load(recording)
        except (OSError, ValueError, record.RecordError):
            return None
        return record.same(expected, played)

    def stop_predicting(self, reason: str, alert: bool = False) -> None:
        self.stats.off = reason
        print(f"WARNING predicted games: {reason}; every game from here on is played", file=sys.stderr, flush=True)
        if alert:
            try:
                with remote.ALERTS.open("a") as handle:
                    handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} WARNING predicted games: {reason}; "
                                 "prediction is off for the rest of this test\n")
            except OSError:
                pass

    # Checks the job's bot against its recording in a thread of its own; the future gives the record.Check.
    def start_check(self, job: Job) -> Future:
        future: Future = Future()
        with self.changed:
            self.checking += 1

        def check() -> None:
            try:
                done = record.check(job.predict.recording, job.a, lambda: self.stopped, job.predict.digest)
            except Exception as error:  # a broken check only costs the prediction: the game is played
                print(f"check of {job.map.name} (a {job.a_side}) failed: {error}", file=sys.stderr, flush=True)
                done = record.Check(False, 0, 0, 0.0, f"check failed: {error}")
            with self.changed:
                self.count_check(done)
                self.checking -= 1
                future.set_result(done)
                self.changed.notify_all()

        threading.Thread(target=check, daemon=True).start()
        return future

    # "local", a remote host with room, or None when no game may start now. A game that records stays on hosts of
    # this Mac's platform, which run this Mac's executables, so its recording holds what a game here would. Such a
    # game never asks a host known to be of another platform for its room, so it isn't measured again every LOAD_S
    # (a CPU gate's run measured all 13 hosts for the 2 Macs that can take its games).
    def free_slot(self, job: Job | None = None) -> str | remote.Host | None:
        work = self.local + self.checking + self.waiting  # checks and games waiting to replay here are local work too
        if work < game_slots(self.workers, work):
            return "local"
        same_platform = self.same_platform or (job is not None and job.record is not None)
        for host in self.hosts:
            if not host.usable() or same_platform and host.known_foreign():
                continue
            try:  # a host with room has been measured, so its platform is known and asking it waits for nothing
                if host.active < host.capacity() and not (same_platform and host.foreign()):
                    return host
            except remote.RemoteError as error:
                self.rest(host, str(error))
        return None

    # Plays the job in a thread of its own; the future gives its parsed GameResult. Parsing there, as soon as the game
    # ends, lets a finished game waiting behind a slow head hold its small result rather than its replay's bytes
    # (a mean 3.0 MB over 49 kept replays, up to 15 MB, with up to `ahead` games waiting).
    def start(self, slot: str | remote.Host, job: Job, path: Path) -> Future:
        future: Future = Future()
        with self.changed:
            if slot == "local":
                self.local += 1
            else:
                slot.add_active(1)

        def play() -> None:
            try:
                game = self.play_here(job, path) if slot == "local" else self.play_remote(slot, job, path)
                game = game if game.host else replace(game, host="here")  # a remote game replayed here too
                with PARSE_LOCK:  # pycapnp objects aren't documented as safe across threads: one parse at a time
                    future.set_result(parse_game(job, game))
            except BaseException as error:  # the consumer re-raises it from future.result()
                future.set_exception(error)
            finally:
                with self.changed:
                    if slot == "local":
                        self.local -= 1
                    else:
                        slot.add_active(-1)
                    self.changed.notify_all()

        threading.Thread(target=play, daemon=True).start()
        return future

    # `native`: run_game's (False: a game the native judge failed on a host, replayed through `unswbc run`). A game the
    # machine failed natively twice (ENGINE_OS_ERROR's line, a signal) is tried through `unswbc run` after that
    # (native_os_error: the judge is switched off only when the fault reproduces natively).
    def play_here(self, job: Job, path: Path, native: bool | None = None, nice: int | None = None) -> RawGame:
        def track(proc: subprocess.Popen) -> None:
            with self.changed:
                self.procs.add(proc)
                if self.stopped:
                    kill_game(proc)

        try:
            causes: list[str] = []
            natively: list[str | None] = []  # each failed try the judge played: its error line
            game, why = self.try_here(job, path, track, retry=False, native=native, nice=nice)
            for wait in REPLAY_WAITS_S:
                if why is None or self.stopped:
                    break
                causes.append(why)
                if game.native:
                    natively.append(error_line(game))
                self.say(f"replaying {job.map.name} (a {job.a_side}) in {wait} s: this machine failed the game ({why})")
                if self.pause(wait):
                    break
                game, why = self.try_here(job, path, track, retry=True,
                                          native=False if len(natively) >= 2 else native, nice=nice)
            if len(natively) >= 2 and why is None and not self.stopped:
                self.native_os_error(job, path, track, natively)
            if why is not None and not self.stopped:
                causes.append(why)
                if len(causes) > 1:
                    game = replace(game, stdout=f"{game.stdout.rstrip()}\nreferee: this machine failed the game on "
                                                f"all {len(causes)} tries ({why})")
            if causes and not self.stopped:
                note_retry(job, causes, why is None)
            with self.changed:
                self.played_here += 1
            return game
        finally:
            with self.changed:
                self.procs = {p for p in self.procs if p.poll() is None}

    # After a game here failed natively twice with one OS error line (`natively`) and `unswbc run` then played it: one
    # more native try (its replay apart, never counted), only while native_plan still plays the game natively; the same
    # line again switches the judge off for every game (count_native_check), since unswbc run doesn't fail that way. A
    # signal (no line) or a new line is the machine's.
    def native_os_error(self, job: Job, path: Path, track: Callable[[subprocess.Popen], None],
                        natively: list[str | None]) -> None:
        line = natively[0]
        if not line or not ENGINE_OS_ERROR.match(line) or any(other != line for other in natively):
            return
        if not native_plan(job, self.sandbox, None):  # switched off, or its key or gate gone, since: none plays
            return
        again_path = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.native")
        try:
            again, _ = self.try_here(job, again_path, track, retry=True, native=True)
        finally:
            again_path.unlink(missing_ok=True)
        if not self.stopped and again.native and error_line(again) == line:
            count_native_check(job, f"here: the native judge failed {len(natively) + 1} times with {line!r}; unswbc "
                                    "run played it", counted=False)

    # One try at the job's game here, at GAME_NICE unless `nice` says otherwise: the game, and why it failed through
    # this machine (infrastructure_failure), if it did. A try that couldn't start (this process's own fork refused) is such a failure too. A retry first clears
    # the failed try's files, so none is read as its own; the first try leaves them, since a baseline recording at its
    # shared path may be another process's finished one.
    def try_here(self, job: Job, path: Path, track: Callable[[subprocess.Popen], None],
                 retry: bool, native: bool | None = None, nice: int | None = None) -> tuple[RawGame, str | None]:
        for leftover in (path, job.record) if retry else ():
            if leftover is not None:
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)
        try:
            own, level = own_nice(), GAME_NICE if nice is None else nice
            lowered = bool(level) or own > 0
            game = run_game(job, self.sandbox, path, track, level, lowered, native)
            if lowered and game.returncode is None and not self.stopped:  # starved, perhaps (watch_game)
                why = (f"{'a turn' if out_of_time(game) else 'the game'} ran out of time at nice "
                       f"{min(20, own + level)}")
                again = "normal priority" if own <= 0 else f"this process's own nice {own}"
                self.say(f"replaying {job.map.name} (a {job.a_side}) at {again}: {why}")
                for leftover in (path, job.record):
                    if leftover is not None:
                        with contextlib.suppress(OSError):
                            leftover.unlink(missing_ok=True)
                game, lowered = run_game(job, self.sandbox, path, track, native=native), False
                # A turn out of time at normal priority too is the bot's own, not contention: no row blames the Mac.
                # A replay past GAME_TIMEOUT_S didn't recover.
                if not self.stopped and not out_of_time(game):
                    note_retry(job, [why], game.returncode is not None, NICED_ROWS)
        except OSError as error:
            return RawGame(f"cannot start the game: {error}", 1, None, 0.0), f"cannot start the game: {error}"

        def repeat() -> RawGame:  # the check's plain game once more, not forking (plain_differs)
            again = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.again")
            try:
                return run_game(job, self.sandbox, again, track, level if lowered else 0, lowered, False, False)
            finally:
                again.unlink(missing_ok=True)

        fallback = fork_notes(job, game.stdout, "here", game, repeat)
        if fallback and not (lowered and start_timeout(fallback)):
            note_fork_off(fallback)
        return game, infrastructure_failure(game)

    # Waits `seconds` unless the run stops first; True when it has stopped.
    def pause(self, seconds: float) -> bool:
        with self.changed:
            return self.changed.wait_for(lambda: self.stopped, timeout=seconds)

    # Plays here a game a host couldn't play, once this Mac has a slot for it (its wait counts as local work, so new
    # games leave the next free slot to it), or past the slots while no other such game is, so one always progresses,
    # unless the slots ran out because this user's process limit has no room (fork_room 0): then it waits, since
    # several schedulers each playing one past it can reach the limit. E0158's SPRT failed when zz-wsl dropped its 26
    # games in flight at 13:03:37 and each started here at once, beside this Mac's 9, past this user's process limit
    # (kern.maxprocperuid): 15 tries failed in the next 6 s.
    # game_slots is asked outside the lock: the loop's gated version reads vm_stat and the CPU split, and every other
    # game's thread waits on this lock to finish. The head game (every result behind it waits) plays at this run's own
    # priority, not GAME_NICE, unless it got in past the slots: at nice 19 beside load ~40, E0466's took ~25 min with
    # every host idle (AHEAD's comment). Head is read as the replay starts: a niced game that becomes head later stays
    # niced, since a process can't lower its children's niceness without root.
    def replay_here(self, job: Job, path: Path, native: bool | None = None) -> RawGame:
        with self.changed:
            self.waiting += 1
        try:
            while True:
                with self.changed:
                    work = self.local + self.checking
                over = work >= game_slots(self.workers, work)
                forks_full = over and fork_room(work) == 0  # no game past the slots at the process limit
                with self.changed:
                    if self.stopped:
                        return RawGame("stopped", None, None, 0.0)
                    if not over or not self.over and not forks_full:
                        self.local, self.over = self.local + 1, self.over + over
                        break
                    self.changed.wait(timeout=5)
        finally:
            with self.changed:
                self.waiting -= 1
        head = path == self.head and not over
        if head:
            self.say(f"replaying {job.map.name} (a {job.a_side}) here at this run's own priority: it is the head game")
        try:
            return self.play_here(job, path, native, 0 if head else None)
        finally:
            with self.changed:
                self.local, self.over = self.local - 1, self.over - over
                self.changed.notify_all()

    # Plays on `host` when both bots are native: their executables, or on a host of another platform their
    # source folders, which it builds; a sandbox game (a CPU gate's) sends this Mac's wasm builds. Only a clean remote
    # run counts: a host failure, or a game that failed there, is replayed here as a slot allows (replay_here), so any
    # game error in a result comes from this machine. So is a sandbox game with a turn out of time there (the 10 s wall
    # limit, a busy host perhaps), as a niced one here is (try_here), so no CPU gate fails on a host's contention.
    # A game the native judge failed there (judge_failure) is replayed here through `unswbc run` without resting the
    # host (settle_native switches the judge off everywhere when it was at fault); one that ran past its time there
    # natively (remote.GameTimeout) rests the host and is replayed here through `unswbc run`, switching nothing off (a
    # busy host, perhaps: hangs are caught here, run_game); and a checked native game (native_check) is played there
    # once more through `unswbc run` and compared (check_native, check_remote), the reference then counting. A game is
    # native only when the host was sent it so (RemoteGame.native).
    # A host failure (a RemoteError, a game exiting non-zero, every bot failing at round 0) replays the game on another
    # host with room first (replay_lost); `again` is that second host's try, whose failure replays it here.
    def play_remote(self, host: remote.Host, job: Job, path: Path, again: bool = False) -> RawGame:
        lost = (lambda: self.replay_here(job, path)) if again else (lambda: self.replay_lost(host, job, path))
        first, second = (job.a, job.b) if job.a_side == "A" else (job.b, job.a)
        bots = tuple(self.remote_bot(host, bot) for bot in (first, second))
        if None in bots:
            return self.replay_here(job, path)
        folder = f"{remote.ROOT}/games/{uuid.uuid4().hex}"
        # stop() kills only the games registered here: one reaching this after it has run is never launched, and one
        # registered just before whose play hasn't started the game on the host yet never starts it (the host agent's
        # kill leaves a marker its play checks)
        with self.changed:
            if self.stopped:
                return RawGame("stopped", None, None, 0.0)
            self.remote_games.add((host, folder))
        start = time.monotonic()
        plan = fork_plan(job, self.sandbox)
        timeout = REMOTE_TIMEOUT_S * (2 if plan and plan[1] else 1)  # a check game plays twice
        native = False
        try:
            native = NATIVE_JUDGE and native_plan(job, self.sandbox, plan, place=host.native_place())
            game = host.play(job.map, bots, timeout, folder, game_seed(job.map),
                             record=job.a_side if job.record else None, fork=plan, native=native,
                             **({"sandbox": True} if self.sandbox else {}))
        except remote.RemoteError as error:
            if self.stopped:
                return RawGame("stopped", None, None, time.monotonic() - start)
            self.rest(host, str(error))
            if isinstance(error, remote.GameTimeout) and error.native:  # the judge may have hung: not natively again
                return self.replay_here(job, path, native=False)
            return lost()
        finally:
            with self.changed:
                self.remote_games.discard((host, folder))
        if notes := ENGINE_CACHE_NOTE.findall(ANSI.sub("", game.stdout)):  # the host's cache fell back: its status says
            host.engine_cache = f"{time.strftime('%H:%M:%S')} {notes[-1].strip()[:300]}"
        raw = RawGame(game.stdout, game.returncode, game.replay, time.monotonic() - start, host.name, game.cpu)
        if fallback := fork_notes(job, game.stdout, host.name, raw,  # its fork server fell back: its status says
                                  lambda: self.play_again(host, job, bots, None)):
            host.fork_server = f"{time.strftime('%H:%M:%S')} {fallback[:300]}"
        if notes := NATIVE_NOTE.findall(ANSI.sub("", game.stdout)):  # its native judge fell back: its status says
            host.native_judge = f"{time.strftime('%H:%M:%S')} {notes[-1].strip()[:300]}"
        if game.returncode != 0 and not self.stopped and map_refused(job.map):  # the map's fault, not the host's
            return RawGame(game.stdout, game.returncode, None, time.monotonic() - start)
        if self.sandbox and out_of_time(raw) and not self.stopped:  # a busy host, perhaps: a CPU gate judges here
            self.say(f"replaying {job.map.name} (a {job.a_side}) here: a turn ran out of time on {host.name}")
            return self.replay_here(job, path)
        native = native and game.native
        if native and not self.stopped and (why := judge_failure(raw)):  # the judge's fault, perhaps: not the host's
            replayed = self.replay_here(job, path, native=False)
            return replayed if self.stopped else settle_native(job, raw, replayed, why, host.name)
        if game.returncode != 0 and not self.stopped:
            self.rest(host, f"game exited {game.returncode}: {game.stdout.strip()[-200:]}")
            return lost()
        if startup_failure(game.stdout):  # the host can't run our bots at all: its fault, not theirs
            self.rest(host, f"every bot of both teams failed at round 0: {game.stdout.strip()[-200:]}")
            return lost()
        host.succeeded()
        # Neither file decides the game's result, so a write failure (a full disk) only loses that file.
        try:
            if game.replay is not None:
                path.write_bytes(game.replay)
        except OSError as error:
            print(f"cannot save the replay of {job.map.name} (a {job.a_side}): {error}", file=sys.stderr, flush=True)
        if job.record and game.recording is not None:
            temporary = job.record.with_name(f"{job.record.name}.{uuid.uuid4().hex}.tmp")
            try:
                job.record.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_bytes(game.recording)
                temporary.replace(job.record)
            except OSError as error:
                print(f"no recording of {job.map.name} (a {job.a_side}): cannot save it: {error}", file=sys.stderr,
                      flush=True)
            finally:
                temporary.unlink(missing_ok=True)
        if native and raw.native and native_check(job):
            return self.check_remote(host, job, bots, plan, raw, path)
        return counted_native(raw)

    # Plays a checked native game (`game`) on `host` once more through `unswbc run` and compares the two
    # (check_native): the game that counts is that reference (its replay saved at `path`; one that failed is played
    # here instead), or the native one when a host failure (its bots all failing at round 0 too) skips the check,
    # which says nothing of the judge.
    def check_remote(self, host: remote.Host, job: Job, bots: tuple, plan: tuple[Path, bool] | None,
                     game: RawGame, path: Path) -> RawGame:
        if self.stopped:
            return game
        try:
            reference = self.play_again(host, job, bots, plan)
        except remote.RemoteError as error:
            print(f"native judge: check of {job.map.name} (a {job.a_side}) on {host.name} skipped: {error}",
                  file=sys.stderr, flush=True)
            release_native_check(job)
            return counted_native(game)
        if startup_failure(reference.stdout):  # the host couldn't start our bots this time: nothing is checked
            print(f"native judge: check of {job.map.name} (a {job.a_side}) on {host.name} skipped: every bot failed "
                  "at round 0", file=sys.stderr, flush=True)
            release_native_check(job)
            return counted_native(game)
        if self.stopped or not check_native(job, reference, game, host.name,
                                            lambda: self.play_again(host, job, bots, plan)):
            return counted_native(game)
        if reference.returncode != 0:  # a failed game counts from this machine only, as every host's does
            return self.replay_here(job, path, native=False)
        try:
            if reference.replay is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(reference.replay)
        except OSError as error:
            print(f"cannot save the replay of {job.map.name} (a {job.a_side}): {error}", file=sys.stderr, flush=True)
        return reference

    # `job`'s game played on `host` through `unswbc run` (`bots` as play_remote sends them, forking by `plan`, never
    # natively), registered for stop() as play_remote's are: a stopped run's is "stopped", unplayed. RemoteError when
    # the host fails.
    def play_again(self, host: remote.Host, job: Job, bots: tuple, plan: tuple[Path, bool] | None) -> RawGame:
        folder = f"{remote.ROOT}/games/{uuid.uuid4().hex}"
        with self.changed:
            if self.stopped:
                return RawGame("stopped", None, None, 0.0)
            self.remote_games.add((host, folder))
        start = time.monotonic()
        try:
            again = host.play(job.map, bots, REMOTE_TIMEOUT_S, folder, game_seed(job.map), fork=plan)
        finally:
            with self.changed:
                self.remote_games.discard((host, folder))
        return RawGame(again.stdout, again.returncode, again.replay, time.monotonic() - start, host.name, again.cpu)

    # Plays a game `lost_by` couldn't on another usable host with room (free_slot's platform rules), taking its slot,
    # else here (replay_here).
    def replay_lost(self, lost_by: remote.Host, job: Job, path: Path) -> RawGame:
        same_platform = self.same_platform or job.record is not None
        other, failed = None, []
        with self.changed:  # picked and counted under one lock, so two lost games can't take one free slot
            for host in self.hosts:
                if host is lost_by or not host.usable() or same_platform and host.known_foreign():
                    continue
                try:  # capacity never waits on the host (remote.Host.capacity)
                    if host.active < host.capacity() and not (same_platform and host.foreign()):
                        other = host
                        break
                except remote.RemoteError as error:
                    failed.append((host, str(error)))
            if other is not None and not self.stopped:
                other.add_active(1)
        for host, why in failed:
            self.rest(host, why)
        if other is None or self.stopped:
            return self.replay_here(job, path)
        self.say(f"replaying {job.map.name} (a {job.a_side}) on {other.name}: {lost_by.name} lost it")
        try:
            return self.play_remote(other, job, path, again=True)
        finally:
            with self.changed:
                other.add_active(-1)
                self.changed.notify_all()

    # A line to stderr, and with `alerts` to host-alerts.log too (remote.Host.alert's format).
    def say(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)
        if self.alerts:
            remote.Host.alert(f"SPRT {line}")

    # Rests `host` (remote.Host.rest) and saves the status; with `alerts`, the rest reaches host-alerts.log too, as
    # remote.Host.rest's own alert does: from its second failure in a row, never a part-time host's or an unreachable
    # one's (remote.UNREACHABLE).
    def rest(self, host: remote.Host, reason: str) -> None:
        host.rest(reason)
        self.save()
        if self.alerts and host.failures == 2 and not host.part_time and not remote.UNREACHABLE.search(reason):
            remote.Host.alert(f"SPRT remote {host.name} resting: {' '.join(reason.split())[:200]}")

    # What `host` is given for `bot`: this Mac's executable, or the source folder when the host is foreign (a
    # prebuilt executable given as the bot can't be rebuilt there). None when the game must play here.
    def remote_bot(self, host: remote.Host, bot: Path) -> Path | None:
        if self.native.get(bot) is None:
            return None
        if not host.foreign():  # known: the host's platform is asked before its first measurement gives it room
            return self.native[bot]
        return bot if bot.is_dir() else None

    def wake(self) -> None:
        with self.changed:
            self.changed.notify_all()

    def stop(self) -> None:
        self.save()
        for host in self.hosts:
            if self.wake in host.wakers:
                host.wakers.remove(self.wake)
        with self.changed:
            self.stopped = True
            procs, games = list(self.procs), list(self.remote_games)
            self.changed.notify_all()  # replays waiting for a slot or pausing between tries (pause) end now
        for proc in procs:
            kill_game(proc)
        for host, folder in games:
            host.kill(folder)


# The toolkit caches sandbox (wasm) builds by folder name and .c/.cpp sources only, so a bot that changed only
# headers reused an older build: every candidate is exported to a folder named `cand`, and E0043/E0044's CPU
# checks measured E0042's build. Copies `bot` into `into` under a name that hashes every file, headers too.
def content_named_copy(bot: Path, into: Path) -> Path:
    copy = into / f"{bot.name}-{remote.source_digest(bot)}"
    if not copy.exists():
        shutil.copytree(bot, copy, ignore=shutil.ignore_patterns(".*"))
    return copy


# Plays the jobs (built first) and yields their results in job order; a None job is "none ready yet" (Scheduler.play).
# `sandbox_hosts`: sandbox games may play on hosts too (Scheduler).
# Replays go to `replays` if given, else to a temp dir where each is deleted once the caller has its result (the
# prewarm streamed ~1 GB of replays a minute into $TMPDIR until its stream ended). `same_platform` keeps games on
# machines of this Mac's platform (see Scheduler). `stats` receives what predicting did (see Prediction), for the
# caller's report. `alerts`: host rests and replays also go to host-alerts.log (the SPRT's; Scheduler.say).
def stream_games(jobs: Iterable[Job | None], bots: list[Path], workers: int | str, sandbox: bool,
                 replays: Path | None = None, same_platform: bool = False,
                 stats: PredictStats | None = None, sandbox_hosts: bool = False,
                 alerts: bool = False) -> Iterator[GameResult]:
    with tempfile.TemporaryDirectory(prefix="referee-") as scratch:
        if sandbox:
            copies = {bot: content_named_copy(bot, Path(scratch)) for bot in dict.fromkeys(bots)}
            jobs = (job and Job(job.map, copies[job.a], copies[job.b], job.a_side) for job in jobs)
            bots = list(copies.values())
        scheduler = Scheduler(workers, sandbox, build_bots(bots, sandbox), same_platform, sandbox_hosts, alerts)
        if stats is not None:
            scheduler.stats = stats
        folder = replays or Path(scratch) / "replays"
        folder.mkdir(parents=True, exist_ok=True)
        yield from scheduler.play(jobs, folder, drop=replays is None)


def play_games(jobs: list[Job], workers: int | str, sandbox: bool,
               replays: Path | None = None, sandbox_hosts: bool = False) -> list[GameResult]:
    bots = [bot for job in jobs for bot in (job.a, job.b)]
    return list(stream_games(jobs, bots, workers, sandbox, replays, sandbox_hosts=sandbox_hosts))


# Plays bot a against bot b on each map twice, a as team A then as team B, and yields (map, first, second) in map
# order. `maps` may be lazy: a map is only read when a slot is free for its games. `play_pairs` uses this.
def stream_pairs(a: Path, b: Path, maps: Iterable[Path], workers: int | str = "auto", sandbox: bool = False,
                 replays: Path | None = None) -> Iterator[tuple[Path, GameResult, GameResult]]:
    jobs = (Job(m, a, b, side) for m in maps for side in ("A", "B"))
    results = stream_games(jobs, [a, b], workers, sandbox, replays)
    try:
        for first in results:
            yield Path(first.map), first, next(results)
    finally:
        results.close()


# Plays bot a against bot b on every map twice: a as team A, then a as team B. Returns 2 results per map,
# in map order.
def play_pairs(a: Path, b: Path, maps: list[Path], workers: int | str = "auto", sandbox: bool = False,
               replays: Path | None = None) -> list[GameResult]:
    return [r for _, first, second in stream_pairs(a, b, maps, workers, sandbox, replays) for r in (first, second)]


# One game a map, a as team A on the first map, B on the second and so on: a review's rule counts (loop/rulecounts.py)
# play each map against several opponents at one game each, with both sides covered over the maps.
def one_side_jobs(a: Path, b: Path, maps: list[Path]) -> list[Job]:
    return [Job(m, a, b, "AB"[i % 2]) for i, m in enumerate(maps)]


# Serialises a result as one JSON line; `points` is dropped for games played outside the sandbox, `host` and `cpu`
# where unknown, and `native` unless the native judge played it, when it is the key it played under
# (native_judge.process_key: a saved row, the screen's baselines among them, is reused only while native_suspect clears
# it).
def to_json(result: GameResult) -> str:
    data = asdict(result)
    for bot in ("a", "b"):
        if data[bot] is not None and data[bot]["points"] is None:
            del data[bot]["points"]
    for key in ("host", "cpu", "native"):
        if data[key] in (None, False):
            del data[key]
    if "native" in data:
        data["native"] = native_judge.process_key() or True
    return json.dumps(data)


def parse_workers(text: str) -> int | str:
    if text == "auto":
        return text
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("--workers takes a positive number or 'auto'")
    return int(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Play paired games (sides swapped) between two bots.")
    parser.add_argument("--a", type=Path, required=True, help="bot a (directory or file)")
    parser.add_argument("--b", type=Path, required=True, help="bot b (directory or file)")
    parser.add_argument("--maps", type=Path, nargs="+", required=True)
    parser.add_argument("--workers", type=parse_workers, default="auto")
    parser.add_argument("--sandbox", action="store_true", help="run in the judge's sandbox and price CPU")
    parser.add_argument("--one-side", action="store_true", help="each map once, a as team A on the first, then B, ...")
    parser.add_argument("--replays", type=Path, help="keep replays in this directory")
    parser.add_argument("--out", type=Path, required=True, help="JSON lines, one per game")
    args = parser.parse_args()

    if args.one_side:
        results = play_games(one_side_jobs(args.a, args.b, args.maps), args.workers, args.sandbox, args.replays)
    else:
        results = play_pairs(args.a, args.b, args.maps, args.workers, args.sandbox, args.replays)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(to_json(r) + "\n" for r in results))

    wins = sum(r.winner == "a" for r in results)
    losses = sum(r.winner == "b" for r in results)
    draws = sum(r.winner == "draw" for r in results)
    errors = [r for r in results if r.error]
    print(f"{len(results)} games: a won {wins}, b won {losses}, drew {draws}, errors {len(errors)}")
    for r in errors:
        print(f"error on {r.map} (a as {r.a_side}): {r.error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
