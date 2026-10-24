"""Extra machines that play referee games over SSH, so an evaluation isn't limited to this Mac's cores.

Hosts are listed in ~/.bc-loop/remote.json, e.g. {"hosts": [{"name": "mm", "max": 10}]}, where "name" is an SSH
host alias, "max" is a safety ceiling on its games (each host fills its idle cores up to it; see measure) and an
optional "connect_timeout" (seconds, default 10, raised to fit its measured connect times up to 3x and 120 s:
connect_timeout) how long SSH waits for it to answer, and "shared" (default false)
marks a machine someone else uses too, whose full CPU always gives a game back (load_hosts, measure), and "part_time"
(default false) one its owner switches off for part of each day, whose outages write no line to ALERTS while it
doesn't answer (Host.rest; health.py alerts only once one lasts health.PART_TIME_DOWN). A missing file means no remote
hosts. A remote game plays the same game as a local one: the host gets this Python's exact package
versions and the map. A host on this Mac's platform (e.g. two Apple Silicon Macs, "Darwin arm64") gets the bots'
already-compiled executables, so nothing is rebuilt there; any other host (Linux, or Windows: see WINDOWS.md) gets
each bot's source folder and compiles it once with the pinned toolkit, as `unswbc run` would there, except that of
the hosts whose compilers report the same, only the first compiles it and the others get a copy (Host.build). Both
bots of one game are always built for the machine that plays it, but a build on another platform can decide
differently, so callers that compare games across machines keep to this platform. Only games whose bots are single
native executables go remote. Any SSH, setup or build failure raises RemoteError, and the caller replays that game
locally instead. A host needs SSH and uv (~/.local/bin/uv, or on PATH on macOS and Linux), plus a C++20 compiler if
it isn't a Mac.

Every operation on a host is one call of host_agent.py there (platform, load, prepare, upload, build, install, play,
kill, crashes), so no shell syntax reaches the host beyond starting Python. The helper is uploaded once, named by its
content, and run by the pinned Python through `uv run`. The first contact runs `uname -sm`; a host where that fails
(Windows' cmd.exe or PowerShell) is asked through the helper, started as .\\.local\\bin\\uv.exe from the home folder.

$BC_REMOTE_HOSTS, when set, replaces remote.json (same JSON): the loop hands its sandboxed coding sessions the
host list that way. Inside Claude Code's Bash sandbox, which only lets traffic out through its HTTP proxy
($HTTPS_PROXY), SSH goes through that proxy (`python remote.py tunnel HOST PORT` is the ProxyCommand); a host the ssh
config reaches with `ssh HOP COMMAND` (sz-wsl) keeps that command, with the hop's own SSH tunnelled (proxy_route).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO
from urllib.parse import unquote, urlsplit

import host_agent

try:
    from compression import zstd  # Python 3.14+: replays come back compressed (host_agent.op_play)
except ImportError:
    zstd = None


# The loop's live state folder: $BC_LOOP_HOME, or ~/.bc-loop when it is unset. A copy of loop/home.py's loop_home,
# since the referee runs from a copy that can't import loop/; tests set $BC_LOOP_HOME before importing (testhome.py).
def loop_home() -> Path:
    return Path(os.environ.get("BC_LOOP_HOME") or Path.home() / ".bc-loop").expanduser().resolve()


CONFIG = loop_home() / "remote.json"
STATUS = loop_home() / "remote-status.json"  # each host's games, rests and last error, for the loop
ALERTS = loop_home() / "host-alerts.log"  # a line when a host keeps failing, which the orchestrator watches
# Hosts already reported down, shared by every game process (each makes its own Hosts: load_hosts), so a host that
# stays off all night is reported once, and once more when it plays again.
OUTAGES = loop_home() / "host-outages.json"
# A rest reason saying the host didn't answer at all (switched off, asleep, off the network), not that it answered and
# failed (a broken toolchain, a full disk): only ssh's own exit 255 or the subprocess timeout, read at the start of the
# reason, since a game's own output later in it can say anything. A part-time host's outage stays unalerted only for
# these (Host.rest);
# loop/health.py keeps a copy (UNREACHABLE) for the same test on its status.
UNREACHABLE = re.compile(r"(?:^|rested \d+s: )[^\s:]+: (?:exit 255|timed out after)")


# Records `name` as down (True) or up (False) in OUTAGES and returns whether that changed it, so the caller writes the
# alert line only on a change.
def outage_changed(name: str, down: bool) -> bool:
    import fcntl
    try:
        OUTAGES.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTAGES.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                known = json.loads(OUTAGES.read_text())
            except (OSError, ValueError):
                known = {}
            if (name in known) == down:
                return False
            if down:
                known[name] = time.time()
            else:
                known.pop(name, None)
            OUTAGES.write_text(json.dumps(known))
            return True
    except OSError:
        return down  # can't share the record: alert anyway rather than stay quiet
# Each host's measured SSH connect times and rest times, shared by every game process: {name: {"connects": [[at,
# seconds], ...], "rests": [at, ...]}}, the newest CONNECT_SAMPLES of each (a few KB). A connect time is a fresh
# connection's `exit 0` (Host.probe_connect), timed; a host's connect timeout grows to fit them (connect_timeout), and
# loop/health.py's host_rests reads the rests (REFLECT 2026-09-30 21:15 F5: az-my and az-id took 27-28 s to connect
# against a 30 s limit, found only when sig asked).
CONNECTS = loop_home() / "host-connects.json"
CONNECT_SAMPLES = 20
PROBE_S = 600  # a Host measures its connect time at most this often (after a successful load measurement)
# The measured timeout: CONNECT_SPREAD x the p95 of a host's connect times plus CONNECT_MARGIN seconds, from
# CONNECT_MIN samples; remote.json's connect_timeout stays the floor, and the measured one grows to at most
# CONNECT_CAP seconds and CONNECT_GROWTH x that floor (connect_timeout), so a host that answers ever slower still rests.
CONNECT_SPREAD, CONNECT_MARGIN, CONNECT_MIN = 1.5, 5, 5
CONNECT_CAP, CONNECT_GROWTH, CONNECT_DEFAULT = 120, 3, 10


# Adds one sample to `name`'s `kind` list in CONNECTS ("connects": [at, seconds], "rests": at), keeping the newest
# CONNECT_SAMPLES. Never raises: a process that can't write the loop home (a coder's sandbox) records nothing.
def record_connect(name: str, kind: str, sample: object) -> None:
    import fcntl
    try:
        with open(CONNECTS.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            known = read_connects()
            entry = known.setdefault(name, {})
            entry[kind] = [*(entry.get(kind) or []), sample][-CONNECT_SAMPLES:]
            temporary = CONNECTS.with_name(f"{CONNECTS.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(known))
            temporary.replace(CONNECTS)
    except OSError:
        pass


# CONNECTS as a dict of dicts, {} when missing or of the wrong shape.
def read_connects() -> dict:
    try:
        known = json.loads(CONNECTS.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in known.items() if isinstance(v, dict)} if isinstance(known, dict) else {}


# `name`'s connect timeout from its own measured connect times (CONNECTS): CONNECT_SPREAD x their p95 plus
# CONNECT_MARGIN, rounded up, or 0 with under CONNECT_MIN samples.
def measured_timeout(name: str, known: dict | None = None) -> int:
    rows = ((read_connects() if known is None else known).get(name) or {}).get("connects") or []
    times = sorted(row[1] for row in rows if isinstance(row, list) and len(row) == 2
                   and isinstance(row[1], (int, float)) and row[1] >= 0)
    if len(times) < CONNECT_MIN:
        return 0
    p95 = times[math.ceil(0.95 * len(times)) - 1]
    return math.ceil(CONNECT_SPREAD * p95 + CONNECT_MARGIN)


# The connect timeout of `name`, whose remote.json connect_timeout is `floor`: its measured_timeout when longer, up to
# CONNECT_CAP and CONNECT_GROWTH x `floor`.
def connect_timeout(name: str, floor: int = CONNECT_DEFAULT, known: dict | None = None) -> int:
    return max(floor, min(measured_timeout(name, known), CONNECT_CAP, CONNECT_GROWTH * floor))


ROOT = ".bc-remote"  # relative to the remote home directory
REST_S = 600  # the longest a failing host is skipped; the first rest is 60 s, doubling while it keeps failing
LOAD_S = 10  # a host's capacity is re-measured, in the background, when its last measurement is older than this
STALE_S = 3 * LOAD_S  # past this a measurement starts no more games there (capacity) until a fresh one lands
# A user's process limit (host_agent.process_limit) caps a host's games: GAME_PROCESSES is one game's peak and
# PROCESS_RESERVE stays free for the rest, as loop/home.py's same-named constants (the referee doesn't import the loop).
GAME_PROCESSES = 150
PROCESS_RESERVE = 450
SATURATED = 0.5  # idle cores under this say the host's CPU is full: this process then gives one game back (measure)
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=20"]  # plus each host's ConnectTimeout (Host.ssh)
UNSHARED = ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
# The ProxyCommand that reaches a host through the sandbox's proxy, before ssh's `%h %p` (see tunnel).
TUNNEL = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} tunnel"
SSH_G = ["ssh", "-G"]  # prints the settings ssh would use for a destination, without connecting (ssh_settings)
# Sessions share a few connections ("masters") per host rather than each logging in. sshd allows MaxSessions (10 by
# default, Windows' OpenSSH too) sessions on one connection and refuses the rest ("Session open refused by peer", seen
# on zz and sz once hosts took 16-32 games), so each master carries at most SESSIONS_PER_MASTER and a host gets as
# many masters as its open sessions need (`seat`). Master n lives at /tmp/bc-ssh-<ssh's 40-hex %C>-n, which with the
# 17 characters ssh adds while creating it stays well under macOS's 104-byte socket path limit; an idle master exits
# after ControlPersist seconds.
SESSIONS_PER_MASTER = 9
SEATS = Path("/tmp/bc-ssh-seats")  # one lock file per session place on a master, shared by every process here


def shared(master: int) -> list[str]:
    return ["-o", "ControlMaster=auto", "-o", f"ControlPath=/tmp/bc-ssh-%C-{master}", "-o", "ControlPersist=300"]


PLATFORM = f"{os.uname().sysname} {os.uname().machine}"  # this machine, as `uname -sm` prints it
MAC_HOSTS = f"{PLATFORM} host"  # where the hosts of this platform play natively (Host.native_place)
AGENT_SOURCE = Path(__file__).resolve().with_name("host_agent.py").read_bytes()
RECORDER = Path(__file__).resolve().with_name("record.py")  # uploaded for games that record (Host.play)
ENGINE_LAUNCHER = Path(__file__).resolve().with_name("engine_cache.py")  # uploaded to load the engine precompiled
FORK_LAUNCHER = Path(__file__).resolve().with_name("fork_server.py")  # uploaded to fork bot processes (Host.play)
NATIVE_LAUNCHER = Path(__file__).resolve().with_name("native_judge.py")  # uploaded to play natively (Host.play)
AGENT = f"{ROOT}/agent/{hashlib.sha256(AGENT_SOURCE).hexdigest()[:16]}/host_agent.py"
# Python code that writes stdin to the path given unless that file holds those bytes already (one torn by a crash is
# written again, MISSES 94), via a temporary name flushed to disk first. Double-quoted with no space, $, backquote,
# backslash or %, so sh, cmd.exe and PowerShell all hand it to Python unchanged.
INSTALL = ("o=__import__('os');s=__import__('sys');p=s.argv[1];d=s.stdin.buffer.read();t=p+'.'+str(o.getpid());"
           "o.path.isfile(p)and(open(p,'rb').read()==d)or(o.makedirs(o.path.dirname(p),exist_ok=True),"
           "(f:=open(t,'wb')),f.write(d),f.flush(),o.fsync(f.fileno()),f.close(),o.replace(t,p))")


class RemoteError(Exception):
    """The host, not the game, failed: SSH, setup or a missing result. `returncode` is ssh's exit status, if any."""

    def __init__(self, message: str, returncode: int | None = None):
        super().__init__(message)
        self.returncode = returncode


class GameTimeout(RemoteError):
    """A game ran past its time on a host that answered the kill that followed: the game, perhaps its native judge,
    hung, or the host was too busy to finish it, not the connection (Host.play_once). `native`: whether it was sent to
    play natively (RemoteGame.native)."""

    def __init__(self, message: str, native: bool = False):
        super().__init__(message)
        self.native = native


# What a finished remote game left behind, as `run.RawGame` fields.
@dataclass
class RemoteGame:
    stdout: str
    returncode: int
    replay: bytes | None
    recording: bytes | None = None  # with `record`: record.py's file, when it wrote one
    cpu: float | None = None  # the game's CPU seconds, where the host reports them (host_agent.game_cpu)
    native: bool = False  # sent to play through native_judge.py (Host.play's `native`, when it could be sent)


# One SSH host. `active` counts the games this process is running there; `capacity` says how many it may run.
@dataclass(eq=False)
class Host:
    name: str
    max: int
    active: int = 0
    rested_until: float = 0.0
    played: int = 0
    last_game: float = 0.0  # wall-clock time of its last clean game, so a stalled host shows
    failures: int = 0  # consecutive; a clean game resets it
    rests: list[str] = field(default_factory=list)
    _files: set[str] = field(default_factory=set)
    _built: dict[str, str] = field(default_factory=dict)
    _platform: str | None = None
    _toolchain: str | None = None
    _agent: bool = False  # host_agent.py is on the host
    compiler: str | None = None  # what builds bots there, on a foreign host (prepare), naming its builds' twins
    connect_timeout: int = 10  # seconds SSH waits for the host to answer (remote.json's "connect_timeout")
    shared: bool = False  # someone else uses it too (remote.json's "shared"): a full CPU always gives a game back
    part_time: bool = False  # switched off part of each day (remote.json's "part_time"): its unanswered outages
    # aren't alerted (Host.rest)
    probes: bool = False  # measures its connect time (probe_connect): set by load_hosts, so a test's Host never does
    _probed: float = -math.inf  # when it last did (monotonic)
    _down_alerted: bool = False  # this process wrote its "failing" line, so a success writes "playing again"
    _free: tuple[float, int] = (0.0, 0)  # (when measured, monotonic; the games this process may run there then)
    _measuring: bool = False
    _error: Exception | None = None  # a background measurement's failure, for capacity() to raise
    wakers: list = field(default_factory=list)  # called after each measurement (run.Scheduler: a game may start)
    cores: int = 0  # logical CPUs, from its last load report (0 until then)
    engine_cache: str | None = None  # the last "engine cache: ..." fallback line a game there printed, with its time
    fork_server: str | None = None  # the same for "fork server: ..." (fork_server.py starting bots as usual)
    native_judge: str | None = None  # the same for "native judge: off ..." (native_judge.py playing it as usual)
    repaired: str | None = None  # the same for the last torn cache entry the host repaired (Host.repair, MISSES 94)
    _lock: threading.Lock = field(default_factory=threading.Lock)  # the toolchain and the helper, once per host
    _measure_lock: threading.Lock = field(default_factory=threading.Lock)
    _keys: dict = field(default_factory=dict)  # a lock per uploaded file or build folder (`once`)

    # Runs one command there as one SSH session on a shared connection with room (see `seat`). A session the host
    # refuses on a shared connection anyway (a lower MaxSessions there) never ran, so it is run once more on a
    # connection of its own; only that one failing says the host is at fault. A new connection waits
    # `connect_timeout` for the host to answer, and `timeout` grows by what that adds past the usual 10 s.
    def ssh(self, command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
        timeout += max(0, self.connect_timeout - 10)

        def run(options: list[str]) -> subprocess.CompletedProcess:
            try:
                # Without a payload ssh gets empty stdin, never the referee's own (a stray read must not block or
                # eat it).
                return subprocess.run([*ssh_command(self.name), "-o", f"ConnectTimeout={self.connect_timeout}",
                                       *options, self.name, command],
                                      input=b"" if data is None else data, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise RemoteError(f"{self.name}: timed out after {timeout:.0f}s") from error

        if proxy_url():
            done = run([])
        else:
            with seat(self.name) as master:
                done = run(UNSHARED if master is None else shared(master))
            if done.returncode == 255 and b"session request failed: Session open refused" in done.stderr:
                done = run(UNSHARED)
        if done.returncode != 0:
            raise RemoteError(f"{self.name}: exit {done.returncode}: {done.stderr.decode(errors='replace')[-300:]}",
                              done.returncode)
        return done.stdout

    # How the host starts the pinned Python: uv from the home folder, which every SSH session starts in. On macOS
    # and Linux uv may also be on PATH; Windows' shells (cmd.exe, PowerShell) take one program and its arguments.
    @staticmethod
    def python(windows: bool) -> str:
        run = f"run -q --no-project --python {'.'.join(map(str, sys.version_info[:3]))} python"
        if windows:
            return rf".\.local\bin\uv.exe {run}"
        return f"U=$HOME/.local/bin/uv; command -v $U >/dev/null || U=uv; $U {run}"

    # Runs one host_agent.py operation (installing the helper on first use) and returns its result and the bytes
    # after it. `windows` is only given while the platform is being found.
    def agent(self, op: str, args: dict | None = None, data: bytes | None = None, timeout: float = 120,
              windows: bool | None = None) -> tuple[dict, bytes]:
        python = self.python(self.windows() if windows is None else windows)
        if not self._agent:
            self.ssh(f'{python} -c "{INSTALL}" {AGENT}', data=AGENT_SOURCE)
            self._agent = True
        out = self.ssh(f"{python} {AGENT} {op} {host_agent.encode(args or {})}", data, timeout)
        start = out.find(host_agent.MARK)
        end = out.find(b"\n", start)
        try:
            if start < 0 or end < 0:
                self._agent = False  # perhaps torn by a crash (an empty one prints nothing): installed again next call
                raise ValueError("no result line")
            result = json.loads(out[start + len(host_agent.MARK):end])
            blob = out[end + 1:]
            if len(blob) != result["blob"]:
                raise ValueError(f"{len(blob)} of {result['blob']} bytes")
        except (ValueError, KeyError, TypeError) as error:
            raise RemoteError(f"{self.name}: unreadable {op} reply ({error}): {out[:200]!r}") from error
        if note := result.get("repaired"):  # a torn upload or build the host replaced (host_agent.op_upload, discard)
            self.repair(str(note))
        return result, blob

    # Says a torn cache entry the host repaired (MISSES 94) on stderr and in its status (`repaired`), so each firing,
    # the first above all, can be checked.
    def repair(self, said: str) -> None:
        said = " ".join(said.split())[:300]
        print(f"{self.name}: repaired {said}", file=sys.stderr, flush=True)
        self.repaired = f"{time.strftime('%H:%M:%S')} {said}"

    def usable(self) -> bool:
        return time.monotonic() >= self.rested_until

    # The host's crash reports of `name` processes (the bots' executable) since `since` (its clock), as [{"uuid", "at"}]
    # (host_agent's crashes: build UUIDs and times alone). A Mac host plays this Mac's builds, so a UUID names ours.
    def crash_reports(self, name: str, since: float) -> list[dict]:
        reports = self.agent("crashes", {"name": name, "since": since}, timeout=60)[0].get("reports")
        if not isinstance(reports, list):
            raise RemoteError(f"{self.name}: unreadable crashes reply: {reports!r:.200}")
        return reports

    # The host's platform, e.g. "Darwin arm64", "Linux x86_64" or "Windows AMD64", asked once: `uname -sm`, or where
    # there is no uname (or only Git's, "MINGW64_NT-..."), the helper as Windows runs it.
    def platform(self) -> str:
        if self._platform is None:
            try:
                found = self.ssh("uname -sm", timeout=30).decode().strip()
            except RemoteError as error:
                if error.returncode in (None, 255):  # timed out, or SSH itself failed
                    raise
                found = ""
            if not found or re.match(r"(MINGW|MSYS|CYGWIN)", found):
                try:
                    found = str(self.agent("platform", timeout=30, windows=True)[0]["platform"])
                except (RemoteError, KeyError) as error:
                    raise RemoteError(f"{self.name}: no uname, and no Windows helper: {error}") from error
            if found.split(" ")[0] not in ("Darwin", "Linux", "Windows"):
                raise RemoteError(f"{self.name}: unsupported platform {found!r}")
            self._platform = found
        return self._platform

    def windows(self) -> bool:
        return self.platform().startswith("Windows")

    # True when this Mac's executables can't run there, so bots are built on the host instead.
    def foreign(self) -> bool:
        return self.platform() != PLATFORM

    # Whether the host is known to be of this Mac's platform, without asking it: False until its platform is asked.
    def of_this_platform(self) -> bool:
        return self._platform == PLATFORM

    # Whether it is known to be of another platform, without asking it: False until its platform is asked.
    def known_foreign(self) -> bool:
        return self._platform is not None and self._platform != PLATFORM

    # Skips the host for a while after a failure (60 s, doubling to REST_S while it keeps failing) and records why.
    def rest(self, reason: str) -> None:
        seconds = min(REST_S, 60 * 2 ** self.failures)
        self.failures += 1
        self.rested_until = time.monotonic() + seconds
        with self._measure_lock:  # what it last measured says nothing of the host after its rest: measure again
            self._free = (0.0, 0)
        self.rests.append(f"{time.strftime('%H:%M:%S')} rested {seconds}s: {reason[:200]}")
        if self.probes:
            record_connect(self.name, "rests", round(time.time(), 1))
        print(f"remote {self.name} resting {seconds}s: {reason}", file=sys.stderr, flush=True)
        # Failed twice running, about 3 minutes gone: say so now, not at the test's end, unless already reported. A
        # part-time host's outage is recorded (OUTAGES, which health.py times) but not alerted while it doesn't answer
        # (UNREACHABLE): it is off by plan. One that answers and fails alerts as any other.
        if self.failures == 2 and outage_changed(self.name, True) \
                and not (self.part_time and UNREACHABLE.search(reason)):
            self._down_alerted = True
            self.alert(f"{self.name} failing: {' '.join(reason.split())[:200]}")

    def succeeded(self) -> None:
        self.played += 1
        self.last_game = time.time()
        self.failures = 0
        if outage_changed(self.name, False) and (not self.part_time or self._down_alerted):
            self.alert(f"{self.name} playing again")
        self._down_alerted = False

    @staticmethod
    def alert(text: str) -> None:
        try:
            with ALERTS.open("a") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def status(self) -> dict:
        return {"played": self.played, "last_game": time.strftime("%H:%M:%S", time.localtime(self.last_game))
                if self.last_game else None, "rests": len(self.rests),
                "last_rest": self.rests[-1] if self.rests else None, "resting": not self.usable(),
                "engine_cache": self.engine_cache, "fork_server": self.fork_server, "native_judge": self.native_judge,
                "repaired": self.repaired}

    # How many games this process may run on the host now, counting its `active` ones there, from the host's last
    # measure(). Never waits on the host: a measurement older than LOAD_S starts a fresh one in the background (a dead
    # host's 10 s connect timeout, or the far Azure hosts' 0.4 s calls, done one after another, held up every game
    # start), and until the first one is in the host gets nothing, nor more than it runs while its last one is over
    # STALE_S old (a host nobody asked for a while). A failed measurement is raised here once, so the caller rests the
    # host, and the host gets nothing until one succeeds (a rest forgets the last one too). Each of `wakers` is called
    # when a measurement ends.
    def capacity(self) -> int:
        with self._measure_lock:
            if self._error is not None:
                error, self._error = self._error, None
                raise error
            checked, free = self._free
            age = time.monotonic() - checked
            if not self._measuring and age > LOAD_S:
                self._measuring = True
                try:
                    threading.Thread(target=self._measure_in_background, name=f"load-{self.name}", daemon=True).start()
                except RuntimeError:  # no thread to be had: the next call tries again
                    self._measuring = False
            return min(free, self.active) if age > STALE_S else free

    # Counts a game this process starts (1) or ends (-1) there. Under the host's own lock, since one process's Hosts
    # are shared by every scheduler it runs (load_hosts), each of which holds only its own lock.
    def add_active(self, change: int) -> None:
        with self._measure_lock:
            self.active += change

    def _measure_in_background(self) -> None:
        measured = False
        try:
            self.measure()
            measured = True
        except Exception as error:  # anything the host said wrong rests it, as a failed game does
            with self._measure_lock:
                self._free = (time.monotonic(), 0)
                self._error = error if isinstance(error, RemoteError) else RemoteError(f"{self.name}: {error}")
        finally:
            with self._measure_lock:
                self._measuring = False
            for wake in list(self.wakers):
                wake()
        if measured:
            self.probe_connect()  # after the wakers: a 27 s connect holds up no measurement or scheduler

    # Times one fresh SSH connection's `exit 0` (a word sh, cmd.exe and PowerShell all take) and records it in
    # CONNECTS, at most every PROBE_S, only for a Host load_hosts made (`probes`) and outside the sandbox (its proxy
    # adds its own time). The shared connections (`seat`) hide connect times from every other call. A failed probe
    # records nothing: the next call on the host fails and rests it as before.
    def probe_connect(self) -> None:
        if not self.probes or proxy_url() or time.monotonic() - self._probed < PROBE_S:
            return
        self._probed = time.monotonic()
        started = time.monotonic()
        try:
            done = subprocess.run([*SSH, *UNSHARED, "-o", f"ConnectTimeout={self.connect_timeout}", self.name, "exit 0"],
                                  stdin=subprocess.DEVNULL, capture_output=True, timeout=self.connect_timeout + 10)
        except (OSError, subprocess.SubprocessError):
            return
        if done.returncode == 0:
            record_connect(self.name, "connects", [round(time.time(), 1), round(time.monotonic() - started, 2)])

    # Asks the host (its platform first, once) how busy it is and how many games run there in all (every referee
    # process's: host_agent.running_games), and sets the games this process may run there: as many more as it has idle
    # cores (cores less the cores busy over the last second), rounded to the nearest (a 2-core host running one game
    # with the other core idle takes a second), up to `max`, a safety ceiling, and none when it is short of memory.
    # Past one game a core in all it adds one a measurement, and only while a whole core sits idle (a game waits on SSH
    # and uploads too, so a host can take a little more than one a core; rounding there put three games on 2-core az
    # hosts). A busy share can't pass 100%, so a full CPU (under SATURATED idle) gives one of this process's games back
    # each measurement while the host runs more than one game a core in all (overfilled, e.g. by two processes that
    # found the same cores idle; this process's own count alone missed an evaluation's 2 beside a tune's 1 on a 2-core
    # host) or fewer than cores less one (other work fills the rest, and keeps its share, as under the load rule).
    # With cores or cores less one in all the host is just full of our games, and giving one back would only make it
    # alternate (a 2-core az host between 2 and 1), unless the host is `shared` (someone else's PC, such as zz):
    # there it gives one back whenever it has any, and alternates. The host's games in all stay within its user's
    # process limit (`proc_limit`; mm and mm1 refused forks at 20-21 games, 1 Oct). Returns it.
    def measure(self) -> int:
        self.platform()
        report, _ = self.agent("load", {"root": ROOT}, timeout=30)
        try:
            cores, busy, memory_gb = int(report["cores"]), float(report["busy"]), float(report["memory_gb"])
        except (KeyError, TypeError, ValueError) as error:
            raise RemoteError(f"{self.name}: unreadable load report: {report!r:.200}") from error
        self.cores = cores
        active, idle = self.active, cores - busy
        try:  # every referee process's games there, ours included; a helper that doesn't count them gives ours
            games = max(active, int(report.get("games", active)))
        except (TypeError, ValueError):
            games = active
        if idle < SATURATED and active and (self.shared or games > cores or games < cores - 1):
            total = active - 1
        else:
            new = math.floor(idle + 0.5)
            if games + new > cores:  # past one a core only for a whole idle core, and one game a measurement
                new = max(cores - games, min(math.floor(idle), 1))
            total = active + max(0, new)
        try:
            limit = int(report.get("proc_limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit:
            total = min(total, active + max(0, (limit - PROCESS_RESERVE) // GAME_PROCESSES - games))
        free = 0 if memory_gb < 2 else max(0, min(self.max, total))
        with self._measure_lock:
            self._free = (time.monotonic(), free)
        return free

    # Installs this Python's exact packages on the host once per toolchain (Python version and package list),
    # then checks the host's unswbc reports our version before any game runs there, and on a foreign host learns
    # which compiler builds there (`compiler`, see build). The host also drops old uploads and builds then, at most
    # hourly (host_agent.cleanup); a run re-uploads anything it still needs.
    def prepare(self) -> str:
        packages = sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions())
        python = ".".join(map(str, sys.version_info[:3]))
        key = hashlib.sha256((python + "\n" + "\n".join(packages)).encode()).hexdigest()[:16]
        folder = f"{ROOT}/toolchains/{key}"
        with self._lock:
            if self._toolchain != key:
                report, _ = self.agent("prepare", {"folder": folder, "python": python, "root": ROOT,
                                                   "compiler": self.foreign()},
                                       data="\n".join(packages).encode() + b"\n", timeout=900)
                found = report.get("unswbc")
                if found != importlib.metadata.version("unswbc"):
                    raise RemoteError(f"{self.name}: unswbc {found!r} there, {importlib.metadata.version('unswbc')} here")
                self.compiler = report.get("compiler") or None
                self._toolchain = key
        return f"{folder}/Scripts/unswbc.exe" if self.windows() else f"{folder}/bin/unswbc"

    # A lock for one uploaded file or build folder, so a slow upload or a build holds up only the games that need
    # it, not every game starting on the host (sz's 75 s uploads, ll's 5.7 s builds).
    def once(self, key: str) -> threading.Lock:
        with self._measure_lock:
            return self._keys.setdefault(key, threading.Lock())

    # Copies a file to the host once, named by its content, and returns its remote path.
    def upload(self, path: Path) -> str:
        blob = path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        name = f"{ROOT}/files/{digest[:24]}/{path.name}"
        if name not in self._files:
            with self.once(name):
                if name not in self._files:
                    self.agent("upload", {"path": name, "sha256": digest}, data=blob)
                    self._files.add(name)
        return name

    # Compiles a bot's source folder on the host with its toolkit, once per source and toolchain, and returns the
    # executable's remote path. A compile error is a RemoteError, so the game is replayed here. Hosts whose compiler
    # reports the same (prepare's `compiler`: platform, compiler version, C library) build byte-identical executables,
    # so in one process only the first of them compiles a bot and the rest get its executable (`shared_build`).
    def build(self, bot: Path, unswbc: str) -> str:
        toolchain = Path(unswbc).parent.parent
        folder = f"{ROOT}/builds/{toolchain.name}-{source_digest(bot)}"
        if folder not in self._built:
            with self.once(folder):
                if folder not in self._built:
                    self._built[folder] = f"{folder}/{self.build_once(bot, folder, toolchain)}"
        return self._built[folder]

    # The executable's path in `folder`: installed from another host's build, or built here, and then shared.
    def build_once(self, bot: Path, folder: str, toolchain: Path) -> str:
        key = (self.compiler, folder) if self.compiler else None
        with shared_build(key) as (found, share):
            if found is not None:
                relative, blob = found
                self.agent("install", {"folder": folder, "executable": relative,
                                       "sha256": hashlib.sha256(blob).hexdigest()}, data=blob)
                return relative
            sources = io.BytesIO()
            with tarfile.open(fileobj=sources, mode="w") as tar:
                for path in source_files(bot):
                    tar.add(path, arcname=path.relative_to(bot).as_posix())
            report, blob = self.agent("build", {"folder": folder, "toolchain": toolchain.as_posix(),
                                                "send": share is not None}, data=sources.getvalue(), timeout=600)
            relative = str(report.get("executable") or "")
            if not relative:
                raise RemoteError(f"{self.name}: build of {bot} named no executable")
            if share is not None and blob:
                share(relative, blob)
            return relative

    # Plays one game in the remote `folder` (see `kill`), team A first. `bots` are the two native executables, or
    # on a foreign host their source folders, built there. A game that ran gives its exit status, even a failed
    # one; a host that couldn't run it (e.g. a broken toolkit) raises RemoteError. `record` ("A" or "B"): play it
    # through record.py instead, which also records that team's side (run.Job.record). Mac and Linux hosts load the
    # engine precompiled from their own ROOT/engine-cache (engine_cache.py); Windows ones compile it each game, as
    # the toolkit does, until the cache is tried there (files another game has open can't be replaced on Windows).
    # `fork` (run.fork_plan: this Mac's shim, and whether the game is a check): Mac and Linux hosts fork each bot
    # process from a server (fork_server.py), a Mac host with this Mac's shim, as it runs this Mac's executables, a
    # Linux one with its own, built in ROOT/fork-server.
    # `native` (run.native_plan): Mac and Linux hosts play it through native_judge.py, which runs the native judge (a
    # Mac host this Mac's build, as it runs this Mac's executables, a Linux one its own, built in ROOT/native-judge from
    # the sources sent) or, when that can't, the game as they would have played it; RemoteGame.native (or
    # GameTimeout.native) says whether it was sent that way (never with `record`, a check, on Windows or with no judge).
    # `sandbox`: the judge's sandbox (`unswbc run --sandbox`), with `bots` this Mac's wasm builds, which the host runs
    # as they are (run.Scheduler plays these only on hosts of this Mac's platform: the CPU gate, referee/cpu.py).
    # A game whose uploaded script, map or bot had gone (Python's "can't open file", the toolkit's "not found:":
    # deleted there since this process sent it, e.g. by the host's cleanup) or was torn by a crash since (the host's
    # "not found:", host_agent.torn_input) is played once more after uploading and building everything again; the
    # torn one is said (`repair`), and still torn then is a RemoteError, never a game run.judge_failure could read.
    def play(self, game_map: Path, bots: tuple[Path, Path], timeout: float, folder: str,
             seed: int | None = None, record: str | None = None,
             fork: tuple[Path, bool] | None = None, native: bool = False, sandbox: bool = False) -> RemoteGame:
        game = self.play_once(game_map, bots, timeout, folder, seed, record, fork, native, sandbox)
        if game.returncode != 0 and ("can't open file" in game.stdout or "not found:" in game.stdout):
            if torn := self.torn(game):
                self.repair(f"{torn}: sending everything again")
            with self._lock:
                self._files.clear()
                self._built.clear()
            game = self.play_once(game_map, bots, timeout, folder, seed, record, fork, native, sandbox)
            if torn := self.torn(game):
                raise RemoteError(f"{self.name}: {torn} after sending everything again")
        return game

    # The host's line saying a game's input was torn since it was sent (host_agent.torn_input), or "".
    @staticmethod
    def torn(game: RemoteGame) -> str:
        return next((line.strip() for line in game.stdout.splitlines() if host_agent.TORN in line), "")

    def play_once(self, game_map: Path, bots: tuple[Path, Path], timeout: float, folder: str,
                  seed: int | None, record: str | None, fork: tuple[Path, bool] | None = None,
                  native: bool = False, sandbox: bool = False) -> RemoteGame:
        unswbc = self.prepare()
        map_bytes = game_map.read_bytes()
        digest = hashlib.sha256(map_bytes).hexdigest()
        remote_map = f"{ROOT}/files/{digest[:24]}/{game_map.name}"  # as upload names it, but sent with the game
        sent = remote_map not in self._files
        first, second = (self.build(bot, unswbc) if bot.is_dir() else self.upload(bot) for bot in bots)
        args = {"unswbc": unswbc, "map": remote_map, "bots": [first, second], "folder": folder, "seed": seed,
                **({"map_sha256": digest} if sent else {}), **({"compress": "zstd"} if zstd else {}),
                **({"sandbox": True} if sandbox else {})}
        if record:
            args["record"] = {"script": self.upload(RECORDER), "team": record}
        if not self.windows():
            args["engine"] = {"script": self.upload(ENGINE_LAUNCHER), "cache": f"{ROOT}/engine-cache"}
        if fork and not self.windows():
            shim, check = fork
            args["fork"] = {"script": self.upload(FORK_LAUNCHER), "check": check,
                            **({"build": f"{ROOT}/fork-server"} if self.foreign() else {"shim": self.upload(shim)})}
        if native and not record and not (fork and fork[1]) and not self.windows() \
                and (judge := self.native_judge_files()):
            args["native"] = judge
        try:
            report, blob = self.agent("play", args, data=map_bytes if sent else None, timeout=timeout)
            if sent:
                self._files.add(remote_map)
            if report.get("compressed") == "zstd":
                blob = zstd.decompress(blob)
            recorded = int(report.get("recording", 0))
            replay, recording = blob[:len(blob) - recorded], blob[len(blob) - recorded:]
            cpu = report.get("cpu")
            return RemoteGame(str(report["stdout"]), int(report["returncode"]), replay if report["replay"] else None,
                              recording if recorded else None, float(cpu) if isinstance(cpu, (int, float)) else None,
                              "native" in args)
        except (KeyError, TypeError, ValueError, *((zstd.ZstdError,) if zstd else ())) as error:
            self.kill(folder)
            raise RemoteError(f"{self.name}: unreadable game report: {error}") from error
        except RemoteError as error:
            answered = self.kill(folder)
            if answered and error.returncode is None and "timed out after" in str(error):
                raise GameTimeout(str(error), "native" in args) from error
            raise

    # native_files with the uploaded native_judge.py, or None having said why when they can't be had here (a file that
    # can't be read): the game then plays as it would without the judge. A host failure still raises RemoteError.
    def native_judge_files(self) -> dict | None:
        try:
            files = self.native_files()
            return {"script": self.upload(NATIVE_LAUNCHER), **files} if files else None
        except RemoteError:
            raise
        except Exception as error:
            said = f"native judge: off ({type(error).__name__}: {' '.join(str(error).split())[:200]})"
            print(f"{self.name}: {said}", file=sys.stderr, flush=True)
            self.native_judge = f"{time.strftime('%H:%M:%S')} {said}"
            return None

    # Where this host's native games are gated (native_judge.enabled; native_fidelity.py --gate --host gates it): Mac
    # hosts together (MAC_HOSTS, "Darwin arm64 host": each runs this Mac's judge through native_judge.py, so this Mac's
    # own games go on their gate too when it has none of its own, run.native_gated), a Linux host with the
    # others whose compiler reports the same (prepare's `compiler`: they build the judge alike), or by its own name
    # when that can't be told.
    def native_place(self) -> str:
        if not self.foreign():
            return MAC_HOSTS
        self.prepare()
        group = hashlib.sha256(self.compiler.encode()).hexdigest()[:8] if self.compiler else self.name
        return f"{self.platform()} {group}"

    # What a host is sent to play natively: this Mac's judge for a Mac host (None when it has none), the sources for a
    # Linux host, which builds its own in ROOT/native-judge (native_judge.py play), and the engine's sha256 and toolkit
    # glue's digest this Mac's key was gated for, which the host's must be (native_judge.host_command).
    def native_files(self) -> dict | None:
        import native_judge

        engine, toolkit = native_judge.engine_wasm(), native_judge.toolkit_digest()
        if engine is None or toolkit is None:
            return None
        expected = {"engine": engine[1], "toolkit": toolkit}
        if self.foreign():
            return {"build": f"{ROOT}/native-judge", **expected,
                    "sources": [self.upload(native_judge.SOURCE_DIR / name) for name in native_judge.SOURCES]}
        judge = native_judge.local_judge(loop_home() / "native-judge")
        return {"binary": self.upload(judge), **expected} if judge else None

    # Stops a game's processes and removes its folder; True when the host answered.
    def kill(self, folder: str) -> bool:
        try:
            self.agent("kill", {"folder": folder}, timeout=30)
            return True
        except RemoteError:
            return False


BUILD_WAIT_S = 120  # a host waits this long for another host's build of the same bot before building it itself
SHARED_BUILDS = 64  # executables kept for the other hosts, the newest (about 140 KB each)
_builds: dict[tuple[str, str], tuple[str, bytes] | threading.Event] = {}
_builds_lock = threading.Lock()


# Shares one build of a bot between the hosts whose compilers report the same (`key`: that and the build folder).
# Gives (found, share): `found` is (the executable's path in the folder, its bytes) when another host has built it;
# else the caller builds, and `share`, when not None, takes its (path, bytes) for the others. A host needing a build
# another is making waits for it (up to BUILD_WAIT_S, then builds without sharing); if that build fails, the next host
# to ask builds it. A None key shares nothing.
@contextlib.contextmanager
def shared_build(key: tuple[str, str] | None) -> Iterator[tuple[tuple[str, bytes] | None, object]]:
    if key is None:
        yield None, None
        return
    claimed = None
    with _builds_lock:
        entry = _builds.get(key)
        if entry is None:
            claimed = _builds[key] = threading.Event()
    if isinstance(entry, threading.Event):
        entry.wait(BUILD_WAIT_S)
        with _builds_lock:
            entry = _builds.get(key)
            if entry is None:
                claimed = _builds[key] = threading.Event()
    if isinstance(entry, tuple):
        yield entry, None
        return

    def share(relative: str, blob: bytes) -> None:
        with _builds_lock:
            _builds[key] = (relative, blob)
            done = [k for k, v in _builds.items() if isinstance(v, tuple)]
            for old in done[:-SHARED_BUILDS]:
                del _builds[old]

    try:
        yield None, share if claimed is not None else None
    finally:
        if claimed is not None:
            with _builds_lock:
                if _builds.get(key) is claimed:  # it failed: the next host to ask builds it
                    del _builds[key]
            claimed.set()


# Records the hosts' games and rests for this run, plus the run's progress (`run`: games finished, per minute,
# games played here), so a host that fails or stalls is visible to the loop, not silent. Written whole via a
# rename, so a reader never sees half a file.
def save_status(hosts: list[Host], path: Path | None = None, run: dict | None = None) -> None:
    if not hosts:
        return
    path = path or STATUS
    temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps({"updated": time.time(), **(run or {}),
                                         "hosts": {h.name: h.status() for h in hosts}}, indent=1))
        temporary.replace(path)
    except OSError:
        pass


def read_status(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATUS).read_text())
    except (OSError, ValueError):
        return {}


# A bot folder's files, leaving out hidden ones such as the toolkit's `.unswbc-build/`.
def source_files(bot: Path) -> list[Path]:
    return sorted(p for p in bot.rglob("*")
                  if p.is_file() and not any(part.startswith(".") for part in p.relative_to(bot).parts))


# Names a bot folder by every file's path and content, headers too.
def source_digest(bot: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(bot):
        digest.update(path.relative_to(bot).as_posix().encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


_known: dict[str, Host] = {}
_known_lock = threading.Lock()


# The hosts in remote.json ($BC_REMOTE_HOSTS when set), each entry's "max", optional "connect_timeout" (seconds; a
# host that must wake first, such as WSL on a PC nobody is logged in to, can take more than the usual 10; raised to
# fit the host's measured connect times, within a cap: connect_timeout) and optional
# "shared" (true for a machine someone else uses too, so a full CPU always gives a game back: Host.measure) and
# "part_time" (true for one switched off part of each day: Host.rest). One Host a
# name for the whole process, so a process that plays several batches (a tune's iterations) keeps each host's
# platform, toolchain, uploads, builds, measurements and rests rather than asking again every batch. A file of the
# wrong shape (not {"hosts": [...]}) gives no hosts and an entry of it (not an object, or a "max" or "connect_timeout"
# that isn't a number) is left out, each with a warning once a process (bad_hosts), so an edit never stops a batch.
def load_hosts(config: Path = CONFIG, environment: bool = True) -> list[Host]:
    given = os.environ.get("BC_REMOTE_HOSTS") if environment else None  # False: `config` alone (driver.host_domains)
    source = "$BC_REMOTE_HOSTS" if given else config
    try:
        listed = json.loads(given or config.read_text())
    except (OSError, ValueError):
        return []
    entries = listed.get("hosts", []) if isinstance(listed, dict) else None
    if not isinstance(entries, list):
        bad_hosts(f'{source}: not {{"hosts": [...]}}, so no remote hosts')
        return []
    hosts, measured = [], read_connects()
    with _known_lock:
        for number, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                bad_hosts(f"{source}: entry {number} is not an object, left out")
                continue
            if not entry.get("name"):
                continue
            try:
                most, timeout = int(entry.get("max", 8)), int(entry.get("connect_timeout", CONNECT_DEFAULT))
            except (TypeError, ValueError, OverflowError):
                bad_hosts(f"{source}: entry {number}'s \"max\" or \"connect_timeout\" is not a number, left out")
                continue
            host = _known.setdefault(str(entry["name"]), Host(str(entry["name"]), 8))
            host.max, host.connect_timeout = most, connect_timeout(host.name, timeout, measured)
            host.probes = True
            host.shared = entry.get("shared") is True
            host.part_time = entry.get("part_time") is True
            hosts.append(host)
    return hosts


_warned: set[str] = set()


# load_hosts' warning about the host list, on stderr once a process (a yielding player reads the list before every
# game: native.host_runs_now).
def bad_hosts(problem: str) -> None:
    if problem not in _warned:
        _warned.add(problem)
        print(f"remote hosts {problem}", file=sys.stderr, flush=True)


# The SSH command for host `name` here, before its connection options: direct, or when a proxy is set (inside the
# sandbox) through it, with no shared connection (its socket would sit outside the writable folder).
def ssh_command(name: str) -> list[str]:
    return [*SSH, *UNSHARED, "-o", f"ProxyCommand={proxy_route((name,))[0]}"] if proxy_url() else SSH


# How ssh reaches `target` (a destination, after any options for it) through the proxy: (the ProxyCommand to give it,
# the host:port the proxy is asked for, "" when `ssh -G` can't say). A host the ssh config reaches with `ssh HOP
# COMMAND` (sz-wsl: `ssh sz "wsl ..."`), which the proxy can't resolve itself, keeps that command, with the tunnel as
# its inner ssh's ProxyCommand, so the proxy is asked for HOP and COMMAND runs as configured; a HOP reached the same
# way is followed `depth` hops deep. Any other host (ssh_hop gives None) tunnels to its own HostName, overriding any
# other ProxyCommand and any ProxyJump it has.
def proxy_route(target: tuple[str, ...], depth: int = 3) -> tuple[str, str]:
    settings = ssh_settings(target)
    hop = ssh_hop(settings.get("proxycommand", "none")) if depth > 0 else None
    if hop is None:
        return f"{TUNNEL} %h %p", f"{settings['hostname']}:{settings['port']}" if settings else ""
    program, rest, hop_target = hop
    inner, endpoint = proxy_route(hop_target, depth - 1)
    # The outer ssh expands %-tokens in its ProxyCommand once, so the inner one's are doubled; the configured words
    # stay as written, for the outer ssh to expand as it would have.
    ours = shlex.join([*SSH[1:], *UNSHARED, "-o", "ProxyCommand=" + inner.replace("%", "%%")])
    return f"{program} {ours}{rest}", endpoint


# The host:port entries the sandbox must allow for `ssh <alias>` through its proxy: the one proxy_route asks for, or
# none when `ssh -G` can't say (the host then fails in the sandbox and its games replay here).
def ssh_names(alias: str) -> list[str]:
    endpoint = proxy_route((alias,))[1]
    return [endpoint] if endpoint else []


# ssh's settings for `target`, as `ssh -G` prints them (lowercase names), or {} when it can't say. Kept in
# SSH_SETTINGS for the process, failures excepted; driver.host_domains clears it for each lane, so a config edit
# reaches the next lane.
SSH_SETTINGS: dict[tuple[str, ...], dict[str, str]] = {}


def ssh_settings(target: tuple[str, ...]) -> dict[str, str]:
    if target in SSH_SETTINGS:
        return SSH_SETTINGS[target]
    try:
        found = subprocess.run([*SSH_G, *target], text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    settings: dict[str, str] = {}
    for line in found.stdout.splitlines() if found.returncode == 0 else []:
        key, _, value = line.partition(" ")
        settings.setdefault(key, value.strip())
    if "hostname" not in settings or "port" not in settings:
        return {}
    SSH_SETTINGS[target] = settings
    return settings


SSH_ARGUMENT_FLAGS = set("BbcDEeFIiJLlmOoPpQRSWw")  # ssh's options that take an argument (`-p 22` or `-p22`)


# Splits a ProxyCommand of the form `ssh [OPTIONS] HOP [COMMAND...]` into (the program word, the rest of the command
# as written, (OPTIONS..., HOP) for `ssh -G`), or gives None for any other command, including one shlex can't split.
# `-W host:port` is left out of the options: it says what to forward, not how to reach HOP, and `ssh -G` refuses
# its unexpanded `%h:%p`. None too for a hop given a jump (beside our ProxyCommand ssh refuses -J and silently
# drops -o ProxyJump, so the hop would skip its jump) or options after HOP (`ssh sz -p 2222`, which ssh honours but `ssh -G HOP` wouldn't see).
def ssh_hop(command: str) -> tuple[str, str, tuple[str, ...]] | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    first = re.match(r"\s*(\S+)", command)
    if not words or not first or first[1] != words[0] or Path(words[0]).name != "ssh":
        return None
    at, options = 1, []
    while at < len(words) and words[at].startswith("-") and words[at] != "-":
        word, at = words[at], at + 1
        if word == "--":  # the destination is next
            options.append(word)
            break
        taking = next((i for i, flag in enumerate(word[1:], 1) if flag in SSH_ARGUMENT_FLAGS), None)
        given = words[at:at + 1] if taking == len(word) - 1 else []  # its argument is the next word
        at += len(given)
        option = " ".join(["-" + word[taking:], *given]) if taking is not None else ""  # "-J x", "-oProxyJump=x"
        if option.startswith("-J") or re.match(r"-o\s*proxyjump\b", option, re.IGNORECASE):
            return None
        if taking is not None and word[taking] == "W":
            word = word[:taking]
            given = []
        options += [word, *given] if word != "-" else []
    if at >= len(words) or words[at + 1:at + 2] and words[at + 1].startswith("-"):
        return None
    return words[0], command[first.end():], (*options, words[at])


# Holds a place for one SSH session to `name` and gives the number of the master it goes on: the first with fewer
# than SESSIONS_PER_MASTER sessions open, so a master is only started when the ones before it are full. Every process
# here shares the masters, so the places are locks on files in SEATS, which also free themselves if a process dies.
# Gives None when the lock files can't be used, and the session then opens its own connection.
@contextlib.contextmanager
def seat(name: str) -> Iterator[int | None]:
    try:
        handle, master = take_seat(re.sub(r"[^A-Za-z0-9_.-]", "_", name))
    except OSError:
        yield None
        return
    try:
        yield master
    finally:
        handle.close()  # releases the lock


def take_seat(prefix: str) -> tuple[IO[str], int]:
    import fcntl
    SEATS.mkdir(parents=True, exist_ok=True)
    master = 0
    while True:
        for place in range(SESSIONS_PER_MASTER):
            handle = open(SEATS / f"{prefix}.{master}.{place}", "a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle, master
            except BlockingIOError:
                handle.close()
            except OSError:
                handle.close()
                raise
        master += 1


def proxy_url() -> str:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""


# ssh's ProxyCommand behind an HTTP proxy: opens a CONNECT tunnel to host:port, logging in with the URL's user and
# password, then relays stdin to it and its replies to stdout until either side closes.
def tunnel(host: str, port: str) -> None:
    proxy = urlsplit(proxy_url())
    connection = socket.create_connection((proxy.hostname or "localhost", proxy.port or 80), timeout=15)
    request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if proxy.username:
        login = f"{unquote(proxy.username)}:{unquote(proxy.password or '')}".encode()
        request += f"Proxy-Authorization: Basic {base64.b64encode(login).decode()}\r\n"
    connection.sendall((request + "\r\n").encode())
    reply = b""
    while b"\r\n\r\n" not in reply:
        chunk = connection.recv(4096)
        if not chunk:
            sys.exit(f"proxy closed the connection to {host}:{port}")
        reply += chunk
    head, early = reply.split(b"\r\n\r\n", 1)
    status = head.split(b"\r\n")[0]
    if status.split()[1:2] != [b"200"]:
        sys.exit(f"proxy refused {host}:{port}: {status.decode(errors='replace')}")
    connection.settimeout(None)
    out = sys.stdout.buffer
    out.write(early)
    out.flush()

    def send() -> None:
        while data := os.read(0, 65536):
            connection.sendall(data)
        connection.shutdown(socket.SHUT_WR)

    threading.Thread(target=send, daemon=True).start()
    while data := connection.recv(65536):
        out.write(data)
        out.flush()


if __name__ == "__main__" and sys.argv[1:2] == ["tunnel"] and len(sys.argv) == 4:
    tunnel(sys.argv[2], sys.argv[3])
