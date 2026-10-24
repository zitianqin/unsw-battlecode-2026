"""Where the loop keeps its live state. Every live path is derived from these, so tests can move all of it at once:
set $BC_LOOP_HOME to a temporary folder before importing any loop module (loop/testhome.py does). Only a home other
than the live ~/.bc-loop moves the paths kept outside it (custom_home), so a real run may name the live home, but a
real run given another home would put the evaluation marker where coders' sandboxes can't read it.

referee/remote.py keeps its own copy of loop_home, since the referee runs from a copy that can't import loop/.

Also the checks several modules share without importing the driver: whether an evaluation runs (evaluation_running)
and whether it plays on the remote hosts now (evaluation_needs_hosts), the load share and this Mac's slots under the
load gate (load_share, mac_slots, reclaimable_gb), and whether a gstack browse server is live (browse_server). And
the niced games' pieces: the niceness of the loop's games here (GAME_NICE, and YIELD_NICE for the players beside an
evaluation and the fillers), this Mac's CPU ticks (cpu_ticks) and the split of busy cores into dev work, niced games
and yielding ones that one sampler measures and publishes for every process and coders' sandboxes (Split, CpuSplit,
split_copy, published_split, or measured once by a reader, measure_now), the cores busy at normal priority (dev_load), and the niced games a process may start
here (niced_slots, sessions_full). And the workers a pool of heavy analysis here may start (pool_size). And the games this user's process limit leaves room for (game_room:
process_table, process_limit), failing open where they can't be read, with ProcessWatch saving spells of no room or
of no reading for health.process_limit.

Importing home also pins the code (pin_code): a process whose main script is a loop/ module imports every loop/,
referee/, tools/ and mapgen/ module from their text as it was at that first import, so a merge on main mid-run can't
give it modules of two versions (MISSES 88); current_code tells a process running older code than the disk's."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import linecache
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple


# The live state folder: $BC_LOOP_HOME, or ~/.bc-loop when it is unset.
def loop_home() -> Path:
    return Path(os.environ.get("BC_LOOP_HOME") or Path.home() / ".bc-loop").expanduser().resolve()


# Third-party code the trusted side runs unsandboxed from outside the repo (bceval's clone and venv:
# tools/bceval_sync.py). Every headless session is denied writing it (driver.lane_settings, meta.worker_settings):
# a .pth file in its venv or a git filter in its clone would run in the loop's own processes (MISSES 17).
EXTERNAL_CODE = (Path.home() / "Dev/bc-external").resolve()


# Whether $BC_LOOP_HOME names a home other than the live ~/.bc-loop (a test's): only then do the paths the live loop
# keeps outside its home (evaluating_marker, load_fraction_copy, clones_folder, split_copy) move inside it. Set to the
# live home itself, as health.fresh's child had it, they moved too, so that child saw no evaluation running and called
# E0414, E0426 and E0429 lock-less and the queue stalled while each was evaluated (MISSES 108).
def custom_home() -> bool:
    given = os.environ.get("BC_LOOP_HOME")
    return bool(given) and Path(given).expanduser().resolve() != (Path.home() / ".bc-loop").resolve()


# The marker present while an evaluation holds the lock. Live, it sits outside ~/.bc-loop so coders' sandboxes
# (which can't read ~/.bc-loop) can see it; with a custom $BC_LOOP_HOME it moves inside that folder.
def evaluating_marker() -> Path:
    if custom_home():
        return loop_home() / "evaluating.json"
    return Path("/private/tmp/bc-loop-evaluating.json")


# The game machines of `hosts` (remote.load_hosts) in the order the players beside an evaluation lay their runs out
# (native.host_offset), as [[host_key(name), cores], ...]: the always-on ones first in remote.json's order, then the
# part-time ones, cores from each entry's "max" (two games a core). The evaluation writes them into its marker
# (driver.evaluation_lock's "runs"), so every player beside it rations from that one list, whenever its own copy of
# remote.json was read (a coder's $BC_REMOTE_HOSTS is its lane's start's).
def host_runs(hosts: list) -> list[list]:
    return [[host_key(host.name), host.max // 2] for host in sorted(hosts, key=lambda host: host.part_time)]


# A host's name in the marker, which every sandboxed session may read: a digest, since remote.json's SSH names are
# kept from the workers (meta.worker_settings).
def host_key(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:16]


# The copy of <loop home>/load-fraction that coders' sandboxes read, beside the evaluation marker for the same reason.
def load_fraction_copy() -> Path:
    if custom_home():
        return loop_home() / "load-fraction-copy"
    return Path("/private/tmp/bc-loop-load-fraction")


# The folder of experiment clones: bc-wt beside the checkout `repo`, or inside a custom $BC_LOOP_HOME (custom_home).
def clones_folder(repo: Path) -> Path:
    if custom_home():
        return loop_home() / "clones"
    return repo.parent / "bc-wt"


# The share of this Mac's cores the loop's games may load, from the text of <loop home>/load-fraction: sig's number
# (clamped to 0.1-0.9) while sig keeps that file, else 0.7 (CLAUDE.md "Machine, scope, and permissions").
def load_share(text: str) -> float:
    try:
        return min(0.9, max(0.1, float(text)))
    except ValueError:
        return 0.7


MARKER_AGE = 12 * 3600  # an evaluation marker older than this was left behind by a process that died


# The evaluation marker while an evaluation holds the lock, else None: the marker exists, is under MARKER_AGE old and
# names a live process (or one a coder's sandbox may not signal). A marker a crashed evaluation left behind doesn't
# count.
def live_marker(now: float | None = None) -> dict | None:
    try:
        marker = json.loads(evaluating_marker().read_text())
        since = float(marker.get("since", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if (time.time() if now is None else now) - since > MARKER_AGE:
        return None
    try:
        os.kill(int(marker["pid"]), 0)
    except ProcessLookupError:
        return None
    except (PermissionError, KeyError, ValueError, TypeError, OverflowError):
        pass  # a sandbox may not let us signal it; the marker is fresh, so count it
    return marker


# Whether an evaluation holds the lock now (live_marker). This Mac yields to it throughout: its CPU gate plays here
# too.
def evaluation_running(now: float | None = None) -> bool:
    return live_marker(now) is not None


# Whether the evaluation holding the lock plays on the remote hosts now, so the players beside it step back there
# (native.host_share, sweep.busy). The holder's marker says "hosts": false in its stages that play only on this Mac or
# nowhere (setup, merge turn, bot tests, maps, recording, promotion: driver.host_stage marks the others), and MAC_HOSTS
# in its CPU gates, which play here and on the hosts of this Mac's platform only (run.Scheduler), so the players step
# back on those hosts alone (native.hosts_wanted_now) and this says false; a marker without the field (written by
# older code) counts as playing there. marker_needs_hosts: the same for a marker already read (live_marker), so a
# reader taking several fields from it sees one holder's.
MAC_HOSTS = "mac"


def evaluation_needs_hosts(now: float | None = None) -> bool:
    return marker_needs_hosts(live_marker(now))


def marker_needs_hosts(marker: dict | None) -> bool:
    return marker is not None and marker.get("hosts") not in (False, MAC_HOSTS)


MIN_FREE_GB = 3  # reclaimable memory this Mac needs before the loop starts a game here


# Reclaimable memory on this Mac: free, inactive and speculative pages, as `vm_stat` counts them. A refused fork
# (EAGAIN at the per-user process limit, E0510 1 Oct 16:43, whose whole SPRT it ended) reads as no room this tick:
# callers start no game here and ask again.
def reclaimable_gb() -> float:
    try:
        stats = subprocess.run(["vm_stat"], check=True, text=True, capture_output=True).stdout
    except OSError:
        return 0.0
    page_size = int(re.search(r"page size of (\d+) bytes", stats).group(1))
    pages = sum(int(re.search(rf"Pages {kind}:\s+(\d+)", stats).group(1))
                for kind in ("free", "inactive", "speculative"))
    return page_size * pages / 1024**3


GAME_NICE = 19  # the niceness of the loop's games on this Mac (native.lower_games, run.GAME_NICE): dev work wins
# The players beside an evaluation's (native.yield_to_evaluations: coders, a review, a tune, the fillers) and the
# fillers' whole processes (meta.spawn's `nice`): the lowest priority, one step below the
# evaluation's games, so the split tells their CPU apart (Split.yielding) and the kernel favours the evaluation's.
YIELD_NICE = 20
# Every headless Claude worker the loop starts (coders, post-mortems, follow-ons, reviews, audits, REFLECT, the digest,
# map ideas: meta.private_tmp) runs at this niceness, and every command it starts inherits it: sig's dev work (nice 0)
# wins the CPU over the workers' scripts and tests, and they over the loop's games. The split counts their CPU with the
# games' (Split.niced), not as dev work, so a coder's analysis scripts never hold back a Mac-gated game or test.
WORKER_NICE = 10


# The increment /usr/bin/nice needs to run a command at niceness `nice` from this process, never below its own (a
# process can't lower it): /usr/bin/nice adds to the caller's niceness, so a worker launched with `nice -n 10` from a
# process already at WORKER_NICE (the upkeep's stand-in, a review's rule counts) landed at 20, under the games.
def nice_increment(nice: int = WORKER_NICE) -> int:
    return max(0, nice - os.getpriority(os.PRIO_PROCESS, 0))
SPLIT_S = 2.0  # the sampler measures the split this often
SPLIT_COPY_S = 30  # a published split older than this isn't read
LAPSE_S = 3 * SPLIT_S  # a published split this old has no live sampler: a reader offers to take its place
FUTURE_S = 5  # a published split dated further ahead than this is refused
SESSION_SHARE = 0.7  # sig's other sessions start a dev stack only below this x cores of 1-minute load (his rule)
TIME = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")  # ps's cputime: [[dd-]hh:]mm:ss.ss


# The cores this Mac keeps busy above WORKER_NICE (`dev`: sig's own work, his sessions' builds and tests, the loop's
# controllers), at WORKER_NICE or lower (`niced`: the loop's games and its headless workers with every command they
# start), and of those at YIELD_NICE (`yielding`: the players beside an evaluation and the fillers).
class Split(NamedTuple):
    dev: float
    niced: float
    yielding: float


# The published split's file (split_copy), beside the evaluation marker so coders' sandboxes, where ps may be refused,
# can read it. The sampler's lock (sampler_lock) is in the loop home, which coders can't reach: in /private/tmp any
# reader could take it (macOS lets flock(LOCK_EX) lock a file opened only for reading) and so stop every sample.
def split_copy() -> Path:
    if custom_home():
        return loop_home() / "cpu-split.json"
    return Path("/private/tmp/bc-loop-cpu-split.json")


def sampler_lock() -> Path:
    return loop_home() / "cpu-split.lock"


_mach = None  # (libSystem, this host's port): the port is a send right, taken once a process and kept


# This Mac's CPU ticks as (busy, total) since boot: user, system and nice against those and idle (HOST_CPU_LOAD_INFO;
# macOS leaves its nice count at 0, a niced process's time is in user). None where it can't be read.
def cpu_ticks() -> tuple[int, int] | None:
    global _mach
    try:
        import ctypes
        if _mach is None:
            system = ctypes.CDLL(None)
            system.mach_host_self.restype = ctypes.c_uint
            _mach = (system, system.mach_host_self())  # each call adds a reference to the port: one a process
        system, port = _mach
        ticks, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)  # user, system, idle, nice
        if system.host_statistics(port, 3, ticks, ctypes.byref(count)):
            return None
    except (OSError, AttributeError):
        return None
    return ticks[0] + ticks[1] + ticks[3], sum(ticks)


# Each process's (niceness, CPU seconds so far: user and system, `ps -o time`) at or above niceness `nice`, by pid, or
# None when ps can't be run (a sandbox may refuse it).
def niced_times(nice: int = WORKER_NICE) -> dict[int, tuple[int, float]] | None:
    try:
        text = subprocess.run(["ps", "-A", "-o", "pid=,nice=,time="], capture_output=True, text=True, timeout=10,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    times = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or not (m := TIME.match(parts[2])):
            continue
        try:
            pid, niceness = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if niceness >= nice:
            days, hours, minutes, seconds = (float(g or 0) for g in m.groups())
            times[pid] = (niceness, ((days * 24 + hours) * 60 + minutes) * 60 + seconds)
    return times


# The split between two samples (monotonic time, busy ticks, total ticks, niced_times): the busy share of the cores,
# of which `niced` is the CPU the processes at WORKER_NICE or lower gained meanwhile and `yielding` those at YIELD_NICE
# (one that exited between the two is missed, so dev reads high, never low); dev is the rest. None when no time or
# ticks passed.
def measure(before: tuple, after: tuple, cores: int) -> Split | None:
    (at0, busy0, total0, times0), (at1, busy1, total1, times1) = before, after
    if total1 <= total0 or at1 <= at0:
        return None
    busy = cores * (busy1 - busy0) / (total1 - total0)
    gained = {pid: max(0.0, t - times0.get(pid, (n, 0.0))[1]) for pid, (n, t) in times1.items()}
    niced = min(sum(gained.values()) / (at1 - at0), busy)
    yielding = min(sum(g for pid, g in gained.items() if times1[pid][0] >= YIELD_NICE) / (at1 - at0), niced)
    return Split(max(0.0, busy - niced), niced, yielding)


FULL_ALERT_S = 300  # the process limit leaving no room for a game this long is saved for health.py (ProcessWatch)
UNREAD_ALERT_S = 1800  # this user's process count or limit unreadable this long is saved too: the gate is off
SPELLS_KEPT = 20  # process-limit.json keeps this many spells


def spells_file() -> Path:
    return loop_home() / "process-limit.json"


# Watches, every CPU sample, whether this user's processes leave room for a game here (game_room). A spell of none
# lasting FULL_ALERT_S is saved, with the process groups holding the most processes then (top_groups), to spells_file
# (the newest SPELLS_KEPT), and saved again every FULL_ALERT_S while it lasts, so its row stays current (its "at") for
# health.process_limit however long it lasts: no game can start here meanwhile, and sig's own work is close to refused
# forks. A spell of the count or the limit unreadable (game_room's None: the gate is off, failing open) lasting
# UNREAD_ALERT_S is saved the same way, as "unread", so a gate that silently stopped measuring is seen too. The sampler
# only runs while loop processes read the CPU split (native.game_slots before every game here, and every loop
# process's utilisation sampler every LAPSE_S: throughput.sampling).
class ProcessWatch:
    def __init__(self) -> None:
        self.kind: str | None = None  # "full", "unread" or None (room): the current spell's
        self.since = 0.0
        self.saved = -math.inf

    def check(self, now: float, table: list[Process] | None, limit: int | None) -> None:
        room = None if table is None or limit is None else game_room(0, table, limit)
        kind = "full" if room == 0 else "unread" if room is None else None
        if kind != self.kind:
            self.kind, self.since, self.saved = kind, now, -math.inf
        if kind is None or now - self.since < (FULL_ALERT_S if kind == "full" else UNREAD_ALERT_S):
            return
        if now - self.saved >= FULL_ALERT_S:
            self.saved = now
            spell = {"since": self.since, "at": now, "count": None if table is None else len(table), "limit": limit,
                     "groups": top_groups(table or [])}
            save_spell({**spell, "unread": True} if kind == "unread" else spell)


# The process groups in `table` (process_table) with the most processes, as "count name (pgid N)", the name its
# leader's (a game is one group: its engine and bots), or its first member's when the leader has exited.
def top_groups(table: list[Process], limit: int = 5) -> list[str]:
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for process in table:
        if not process.pgid:
            continue
        counts[process.pgid] = counts.get(process.pgid, 0) + 1
        if process.pid == process.pgid or process.pgid not in names:
            names[process.pgid] = process.name
    top = sorted(counts, key=lambda pgid: -counts[pgid])[:limit]
    return [f"{counts[pgid]} {names[pgid]} (pgid {pgid})" for pgid in top]


# Adds `spell` to spells_file, or replaces the last row when it is the same spell saved again (same "since" and kind),
# through a new file of a random name renamed over it, removed if the write fails.
def save_spell(spell: dict) -> None:
    path = spells_file()
    try:
        with open(path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                spells = json.loads(path.read_text()).get("spells")
                spells = spells if isinstance(spells, list) else []
            except (OSError, ValueError, AttributeError):
                spells = []
            if spells and isinstance(spells[-1], dict) and spells[-1].get("since") == spell["since"] and \
                    spells[-1].get("unread") == spell.get("unread"):
                spells = spells[:-1]
            fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(json.dumps({"spells": [*spells, spell][-SPELLS_KEPT:]}) + "\n")
                os.replace(temporary, path)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
    except OSError:
        pass


# The sha256 of the file at `path`'s content, or None where it can't be read: by content, not modification time, so a
# checkout that rewrites a file with the same text (a branch switch and back) leaves every process current.
def file_digest(path: str) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


LOOP_DIR = Path(__file__).resolve().parent
# Each module file this process has loaded, with its content when this process first saw it loaded: those from loop/
# (_stamp: at import for home.py and the modules loaded before it, else at the first current_code(everything=True)
# after its import, the upkeep's minute check (maintenance.ticking), so a file rewritten within that minute goes
# unnoticed), and in a pinned process every PINNED_DIRS module imported from the copy, with the copy's content
# (_PinnedFinder): there a change on disk to referee/, tools/ or mapgen/ makes current_code(everything=True) false too.
_IMPORTED: dict[str, str | None] = {}


def _stamp() -> None:
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if isinstance(file, str) and file.endswith(".py") and file not in _IMPORTED \
                and Path(file).resolve().parent == LOOP_DIR:
            _IMPORTED[file] = file_digest(file)


_IMPORTED[__file__] = file_digest(__file__)
_stamp()


# A loop process imports some modules only when it first needs them (driver.prepare's `import native`, health's), and
# a merge on main meanwhile rewrites them: at 08:01 on 29 Sep E0306's --once, started 06:56, had coded its candidate
# when prepare imported native.py from after the 07:52 merge, which imports home.MAC_HOSTS, a name its home.py from
# 06:56 lacked, and the ImportError ended the experiment in error (MISSES 88). So a process whose main script is a loop/
# module (not a test) keeps the text of every module in PINNED_DIRS as it was when home was first imported and imports
# them from that copy: all its code is of one version, its start's, which current_code(everything=True) then compares
# with the disk as before. A file unchanged since (its size and modification time) loads as usual, its cached bytecode
# too; a changed one compiles from the copy, writing no cache, and one deleted or renamed since imports from the copy
# too. A module added since isn't in the copy and imports from the disk. Tracebacks show the copy's lines (linecache).
# The copy is about 3 MB, read in about 10 ms. A process started from elsewhere (a test, the referee's own checkout,
# `python -c`) imports as usual; pin_code pins one by hand.
PINNED_DIRS = ("loop", "referee", "tools", "mapgen")
_PINNED: dict[str, tuple[bytes, tuple[int, int]]] = {}  # each file's real path: its text and (mtime_ns, size) then
_PINNED_NAMES: set[str] = set()  # their module names
_pinning = False  # pin_code has run: one finder, one copy


class _PinnedLoader(importlib.machinery.SourceFileLoader):
    def get_data(self, path: str) -> bytes:
        if path != self.path:
            return super().get_data(path)  # its cached bytecode, which path_stats let it read
        return _PINNED[os.path.realpath(path)][0]

    def path_stats(self, path: str) -> dict:
        now = os.stat(path)  # gone since: an OSError too
        if (now.st_mtime_ns, now.st_size) != _PINNED[os.path.realpath(path)][1]:
            raise OSError("changed since the pin: compiled from the copy")  # SourceLoader then skips bytecode
        return {"mtime": now.st_mtime, "size": now.st_size}


class _PinnedFinder:
    @staticmethod
    def find_spec(name: str, path=None, target=None):
        if path is not None or name not in _PINNED_NAMES:  # the folders hold no packages
            return None
        spec = importlib.machinery.PathFinder.find_spec(name)  # sys.path's order decides, as without the pin
        if spec is not None and spec.origin:
            origin = spec.origin
        else:  # deleted or renamed since the pin: the first sys.path folder whose copy has it
            origin = next((file for folder in sys.path if isinstance(folder, str) and os.path.realpath(
                file := os.path.join(folder or os.getcwd(), f"{name}.py")) in _PINNED), None)
        if origin is None or (pinned := _PINNED.get(os.path.realpath(origin))) is None:
            return None
        _IMPORTED.setdefault(origin, hashlib.sha256(pinned[0]).hexdigest())  # what current_code compares
        # Never checked against the disk (mtime None), so a traceback shows the lines that run, not a later rewrite's.
        lines = pinned[0].decode("utf-8", "replace").splitlines(keepends=True)
        linecache.cache[origin] = (len(pinned[0]), None, lines, origin)
        return importlib.util.spec_from_file_location(name, origin, loader=_PinnedLoader(name, origin))


def pin_code() -> None:
    global _pinning
    if _pinning:
        return
    _pinning = True
    for folder in PINNED_DIRS:
        for path in (LOOP_DIR.parent / folder).glob("*.py"):
            if not path.name.startswith("test_"):
                with contextlib.suppress(OSError):
                    was = path.stat()  # before the read: a file rewritten between them then compiles from the copy
                    _PINNED[os.path.realpath(path)] = (path.read_bytes(), (was.st_mtime_ns, was.st_size))
                    _PINNED_NAMES.add(path.stem)
    # Just ahead of the path finder, so built-in and frozen modules keep their precedence over a file of their name.
    finders = [finder for finder in sys.meta_path if finder is importlib.machinery.PathFinder]
    sys.meta_path.insert(sys.meta_path.index(finders[0]) if finders else len(sys.meta_path), _PinnedFinder())


def _pinned_process() -> bool:
    main = getattr(sys.modules.get("__main__"), "__file__", None)
    return isinstance(main, str) and Path(main).resolve().parent == LOOP_DIR and not Path(main).name.startswith("test_")


if _pinned_process():
    pin_code()


# Whether this process runs the loop code on disk: home.py alone, or with `everything` every module it has loaded
# (_IMPORTED). A role one process holds for its life (the CPU sampler here, the upkeep tick: maintenance.ticking) and a
# driver waiting in the evaluation queue lives for hours: at 19:07 on 28 Sep the sampler was one started at 15:58,
# before 9ba0fd0 (merged 17:04) counted workers at WORKER_NICE as niced, publishing their CPU as dev work (9.3 of 10
# cores against 3.7 measured), which the CPU gate here is gated on; and the upkeep's owner, started before a merge,
# ran old online, prune and team-name code for hours. So a process running older code never holds the role: the
# sampler gives it up for a current process to take, and the upkeep's owner hands its lock to a fresh stand-in process
# on the disk's code (maintenance.hand_over), since every process may be older (MISSES 76, 91). Hashing every loaded
# module is 1.7 MB, 8 ms (a driver, 28 Sep), so only the upkeep's minute check and the utilisation sampler, when about
# to write or replace the minute's row (throughput.sample), ask for it; the CPU sampler, every SPLIT_S, reads home.py
# alone, the code it samples with.
def current_code(everything: bool = False) -> bool:
    if not everything:
        return file_digest(__file__) == _IMPORTED[__file__]
    _stamp()
    return all(file_digest(file) == digest for file, digest in list(_IMPORTED.items()))


# This Mac's CPU split, measured by one process at a time for every loop process here: the one holding sampler_lock
# samples every SPLIT_S in a thread of its own and publishes each split (publish_split); every process reads the
# published copy (published_split), which a coder's sandbox, where ps fails, can read too. A reader finding the copy
# over LAPSE_S old (no sampler: the last one exited, or none has started) offers to take its place, at most once a
# LAPSE_S; one that can't sample (ps or the ticks refused) lets the lock go. Every loop process's utilisation sampler
# reads it every LAPSE_S (throughput.sampling), so a sampler runs whenever the loop does, not only while a game here
# is gated (29 Sep: with none, coders' sandboxed test gates fell back to the load average for 57 min). So `ps -A` runs once every SPLIT_S on this
# Mac, not in every scheduler process, and no read waits: until a new sampler's first split (SPLIT_S after it starts)
# a reader gets the last copy while under SPLIT_COPY_S old, else None (the caller keeps the 1-minute load average).
# Known limit: the sampler is whichever process offered first, perhaps a filler's at nice 20, so while dev work fills
# every core its samples come late; a copy over SPLIT_COPY_S old reads as none, so the readers fall back to the load
# average rather than trust a stale split.
# Each sample also checks this user's process count (ProcessWatch), as no other process watches it continuously.
class CpuSplit:
    def __init__(self) -> None:
        self.sampling = False
        self.offered = -math.inf
        self.last: tuple | None = None
        self.at = -math.inf  # when the split read last was measured (its record's "at"), so a caller can tell a new one
        self.processes = ProcessWatch()

    def read(self) -> Split | None:
        split, self.at = published()
        if time.time() - self.at > LAPSE_S and not self.sampling and time.monotonic() - self.offered >= LAPSE_S \
                and current_code():
            self.offered = time.monotonic()
            self.offer()
        return split

    # Takes the sampler's place if no process holds it, and starts sampling.
    def offer(self) -> None:
        try:
            fd = os.open(sampler_lock(), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return
        if (first := self.sample()) is None:
            os.close(fd)  # this process can't measure: another may
            return
        self.last, self.sampling = first, True
        threading.Thread(target=self.run, args=(fd,), daemon=True).start()

    def run(self, fd: int) -> None:
        try:
            while self.step():
                time.sleep(SPLIT_S)
        finally:
            self.sampling = False
            os.close(fd)

    # One measurement, its split against the previous one published (none without one), then the process-limit watch
    # (ProcessWatch.check), which runs on every call, the first too; False, watching nothing, once this process can't
    # sample any more or runs older code than this file on disk (current_code).
    def step(self) -> bool:
        now = self.sample() if current_code() else None
        if now is None:
            return False
        split = measure(self.last, now, os.cpu_count() or 1) if self.last else None
        self.last = now
        if split is not None:
            publish_split(split)
        self.processes.check(time.time(), process_table(), process_limit())
        return True

    def sample(self) -> tuple | None:
        ticks, times = cpu_ticks(), niced_times()
        return None if ticks is None or times is None else (time.monotonic(), *ticks, times)


# Writes `split` to split_copy through a new file of a random name beside it, renamed over it: never written through
# a link or file someone put there (a coder's sandbox can't write it, but the trusted side never trusts that).
def publish_split(split: Split) -> None:
    path = split_copy()
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError:
        return
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"at": time.time(), "dev": round(split.dev, 2), "niced": round(split.niced, 2),
                                     "yielding": round(split.yielding, 2), "process_limit": process_limit()}))
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


# The published split (None while over SPLIT_COPY_S old) and when it was measured (-inf when there is none or it is
# refused). A record dated over FUTURE_S ahead, or with a value outside 0..cores (yielding at most niced), is refused,
# so a forged or corrupt one can't stay fresh forever or open more slots than this Mac has. One from code before
# "yielding" was published counts none.
def published() -> tuple[Split | None, float]:
    try:
        record = json.loads(split_copy().read_text())
        at = float(record["at"])
        split = Split(float(record["dev"]), float(record["niced"]), float(record.get("yielding", 0.0)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, -math.inf
    cores = os.cpu_count() or 1
    if not (at <= time.time() + FUTURE_S and 0 <= split.dev <= cores and 0 <= split.yielding <= split.niced <= cores):
        return None, -math.inf
    return (split if time.time() - at <= SPLIT_COPY_S else None), at


# The newest published split, while under SPLIT_COPY_S old, else None (tools/gated_test.sh reads it); a record dated
# ahead or out of range is refused.
def published_split() -> Split | None:
    return published()[0]


CPU = CpuSplit()  # one a process


# The split over the next SPLIT_S, measured by this process without taking the sampler's place (tools/gated_test.sh,
# when none is published: REFLECT 2026-09-28 20:52 F11, a test gate counting the loop's niced games as blocking);
# None where ticks or ps can't be read (a coder's sandbox).
def measure_now() -> Split | None:
    if (before := CPU.sample()) is None:
        return None
    time.sleep(SPLIT_S)
    after = CPU.sample()
    return None if after is None else measure(before, after, os.cpu_count() or 1)


# The cores busy at normal priority now (CpuSplit), for a gate that should count dev work but not the loop's niced
# games or workers; None when it can't be measured or read, and the caller keeps the 1-minute load average.
def dev_load() -> float | None:
    split = CPU.read()
    return None if split is None else split.dev


# The share of this Mac the loop may load now (load_share): <loop home>/load-fraction, or in a coder's sandbox, which
# can't read the loop home, the copy the upkeep keeps (load_fraction_copy); 0.7 without either.
def share_now() -> float:
    try:
        text = loop_home().joinpath("load-fraction").read_text()
    except OSError:
        try:
            text = load_fraction_copy().read_text()
        except OSError:
            text = ""
    return load_share(text)


# The cores this Mac keeps busy with work at this process's priority or higher (niceness at most its own), measured
# over SPLIT_S: for a normal-priority process the dev work, for a headless worker's command (WORKER_NICE) the dev
# work and every worker's commands and games too; work at a higher niceness than its own (for a worker, the loop's
# games at GAME_NICE and YIELD_NICE) isn't counted, as it takes only what is left. Where this process can't measure
# (a coder's sandbox refusing ps) the published split's dev work, which sandboxes read, stands in: the 1-minute load
# average counts every niced game, and at load 36 held a coder's pool until the load fell under the share. None when
# neither can be read.
def load_at_or_above() -> float | None:
    own = os.getpriority(os.PRIO_PROCESS, 0)
    split = published_split()
    if own < WORKER_NICE and split is not None:  # the dev work, published every SPLIT_S
        return split.dev
    measured = None
    if (before := CPU.sample()) is not None:
        time.sleep(SPLIT_S)
        if (after := CPU.sample()) is not None:
            (at0, busy0, total0, times0), (at1, busy1, total1, times1) = before, after
            if total1 > total0 and at1 > at0:
                busy = (os.cpu_count() or 1) * (busy1 - busy0) / (total1 - total0)
                below = sum(max(0.0, t - times0.get(pid, (n, 0.0))[1]) for pid, (n, t) in times1.items() if n > own)
                measured = max(0.0, busy - below / (at1 - at0))
    return measured if measured is not None else split.dev if split is not None else None


POOL_POLL_S = 20  # how often pool_size looks again while this Mac is full
POOL_NOTE_S = 300  # and how often it says so


# How many workers a pool of heavy analysis on this Mac may start (a multiprocessing pool, map making, replay reading,
# compiles): the cores the loop's load share (share_now) leaves above the work at its priority or higher
# (load_at_or_above; the 1-minute load average where neither it nor a published split can be read), at least 1 and at
# most `want`. With none left (the load reaching the share: a share under one core still gets one) it waits, looking every POOL_POLL_S and saying so every POOL_NOTE_S, as tools/gated_test.sh does, unless
# `wait` is False (a step on the loop's own path, which never waits for this Mac: MISSES 53). On 29 Sep at 13:01 this
# Mac reached a load of 51 on 10 cores from coders' and workers' Python analysis, each pool sized by the cores and
# waiting for nothing. Use it as `ProcessPoolExecutor(home.pool_size(8))`.
def pool_size(want: int, wait: bool = True) -> int:
    cores, noted = os.cpu_count() or 1, -math.inf
    while True:
        load = load_at_or_above()
        load = os.getloadavg()[0] if load is None else load
        limit = share_now() * cores
        if load < limit or not wait:
            return max(1, min(want, math.floor(limit - load)))
        if time.monotonic() - noted >= POOL_NOTE_S:
            noted = time.monotonic()
            print(f"pool: waiting, load {load:.2f} at or above this priority reaches {limit:.1f} of "
                  f"{cores} cores", file=sys.stderr, flush=True)
        time.sleep(POOL_POLL_S)


NICE_STEP = 2  # niced games a process may add here per CPU split (niced_slots)


# The games this Mac may run at GAME_NICE for a process that ran `active` of them here when the split was measured:
# none while dev work (`dev`, cores busy at normal priority) reaches `share` x `cores` or memory is short, else
# `active` plus the cores left idle by dev work and the niced games it must leave room for (`niced`), rounded, at most
# NICE_STEP more a split and at most `cap` more, so games fill what dev work leaves and step back as it grows (dev
# work wins the CPU in between). The step bounds what several processes that read the same idle cores before their
# new games show in a split start together; `cap` is what a yielding player beside an evaluation leaves of this Mac's
# half to the others (native.game_slots).
def niced_slots(share: float, cores: int, dev: float, niced: float, active: float, memory_gb: float,
                cap: float = math.inf) -> int:
    if dev >= share * cores or memory_gb < MIN_FREE_GB:
        return 0
    return max(0, math.floor(active + min(NICE_STEP, cores - dev - niced, cap) + 0.5))


# Whether the 1-minute load average `load` stops niced games starting on a Mac of `cores`: at SESSION_SHARE x cores,
# or the loop's `share` if lower. Niced games take only idle CPU, but sig's other sessions gate on the load average
# (his rule: a dev stack starts only below 0.7 x cores), which games filling every core would hold above it.
def sessions_full(load: float, share: float, cores: int) -> bool:
    return load >= min(share, SESSION_SHARE) * cores


# The load gate's games on this Mac (CLAUDE.md "Machine, scope, and permissions"): none while other work's load (the
# 1-minute average less our own `active` games) reaches `share` x `cores` or memory is short, else the room left.
def mac_slots(share: float, cores: int, load: float, active: float, memory_gb: float) -> int:
    limit = share * cores
    other = max(0.0, load - active)
    if other >= limit or memory_gb < MIN_FREE_GB:
        return 0
    return max(1, math.floor(limit - other))


# This user's process limit (kern.maxprocperuid, 2666 on this Mac): past it every fork of the user's fails with EAGAIN
# ("[Errno 35] Resource temporarily unavailable"), a game's and anything else's alike (28 Sep 01:17-01:41: the late
# screen's and the variant sweep's launches, a remote dispatch's ssh, a commit hook). A game is one process a dragon,
# so it grows as dragons split: GAME_PROCESSES is one game's peak (the fork server's group of a 64-a-side game held
# up to 147 processes, 28 Sep, n: MISSES 67), and PROCESS_RESERVE is kept free of new games for
# the rest: the growth of games already running, sig's work, the Claude sessions and the loop's own steps.
#
# A game is recognised by its bots, anyone's (sig's own `unswbc run` too): every native bot process runs the
# executable the toolkit builds, named BOT_NAME (unswbc.toolchain.BOT_NAME), whether its team's fork server (itself one,
# the bot started once with the shim) forked it or the toolkit started it as usual, and it stays in its game's process
# group, which run.run_game starts as a new session led by the toolkit's python. So a live game is a process group
# holding a BOT_NAME process. A sandbox game runs its bots inside the engine and never grows: it counts as itself.
GAME_PROCESSES = 150
PROCESS_RESERVE = 450
BOT_NAME = "bot"
_process_limit: int | None = None
_libproc: tuple | None = None  # libproc and its proc_bsdshortinfo, loaded once a process (process_table)


class Process(NamedTuple):
    pid: int
    ppid: int
    pgid: int  # 0 when its details couldn't be read
    name: str  # its executable's name, at most 16 bytes (p_comm), decoded with errors replaced


def _load_libproc() -> tuple:
    global _libproc
    if _libproc is None:
        import ctypes

        class ShortInfo(ctypes.Structure):  # struct proc_bsdshortinfo, <sys/proc_info.h>: 64 bytes
            _fields_ = [("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                        ("status", ctypes.c_uint32), ("comm", ctypes.c_char * 16), ("rest", ctypes.c_uint32 * 8)]

        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        _libproc = (ctypes, lib, ShortInfo)
    return _libproc


# This user's processes now: libproc's list by real user id (the id the limit counts) and each one's short BSD info,
# with no fork (about 1 ms for 500 processes). None where the list can't be read (then game_room's gate is off). A
# process whose info can't be read (it just exited, or a sandbox refuses it) is kept with pgid 0 and no name, so it
# still counts but joins no game.
def process_table() -> list[Process] | None:
    try:
        ctypes, lib, ShortInfo = _load_libproc()
        size = lib.proc_listpids(5, os.getuid(), None, 0)  # 5: PROC_RUID_ONLY
        if size <= 0:
            return None
        pids = (ctypes.c_int * (size // 4 + 256))()
        got = lib.proc_listpids(5, os.getuid(), pids, ctypes.sizeof(pids))
        if got <= 0:
            return None
        table, info = [], ShortInfo()
        for pid in pids[:got // 4]:
            if not pid:
                continue
            # 13: PROC_PIDT_SHORTBSDINFO
            if lib.proc_pidinfo(pid, 13, 0, ctypes.byref(info), ctypes.sizeof(info)) == ctypes.sizeof(info):
                table.append(Process(pid, info.ppid, info.pgid, info.comm.decode(errors="replace")))
            else:
                table.append(Process(pid, 0, 0, ""))
    except (OSError, AttributeError, ValueError):
        return None
    return table


# This user's processes now (process_table), or None where they can't be read.
def user_processes() -> int | None:
    table = process_table()
    return None if table is None else len(table)


# kern.maxprocperuid (read once a process: sysctl_limit), or where a coder's sandbox refuses that sysctl the copy the
# CPU sampler publishes (publish_split). None where neither can be read.
def process_limit() -> int | None:
    global _process_limit
    if _process_limit is None:
        _process_limit = sysctl_limit()
    if _process_limit is not None:
        return _process_limit
    try:
        published_limit = int(json.loads(split_copy().read_text())["process_limit"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return published_limit if 0 < published_limit < 1_000_000 else None


def sysctl_limit() -> int | None:
    try:
        import ctypes
        value, size = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if ctypes.CDLL(None).sysctlbyname(b"kern.maxprocperuid", ctypes.byref(value), ctypes.byref(size), None, 0):
            return None
    except (OSError, AttributeError):
        return None
    return value.value if value.value > 0 else None


# How many more games fit under this user's process limit now (native.game_slots, and run.fork_room for replay_here),
# for a caller with `active` games of its own here, running or asked for. Every live game (a group holding a BOT_NAME
# process) is charged at GAME_PROCESSES, or its size when larger, not its size now, since it grows as dragons split;
# every other process counts as itself; PROCESS_RESERVE is kept free. The caller's own games not yet recognised (a
# game's start-up, before its bots start: `active` less the games led by this process's children) are charged
# too. None when the table or the limit can't be read: the gate is then off (it fails open), so a sandbox or a libproc
# change never stops every game here; ProcessWatch saves such a spell for health.process_limit's alert.
# Known gap: another process's game in its start-up, before its bots start, isn't seen.
def game_room(active: int = 0, table: list[Process] | None = None, limit: int | None = None) -> int | None:
    table = process_table() if table is None else table
    limit = process_limit() if limit is None else limit
    if table is None or limit is None:
        return None
    sizes: dict[int, int] = {}
    for process in table:
        if process.pgid:
            sizes[process.pgid] = sizes.get(process.pgid, 0) + 1
    games = {process.pgid for process in table if process.pgid and process.name == BOT_NAME}
    ours = sum(1 for process in table if process.pid == process.pgid and process.pgid in games
               and process.ppid == os.getpid())
    held = len(table) + sum(max(sizes[game], GAME_PROCESSES) - sizes[game] for game in games)
    return max(0, (limit - PROCESS_RESERVE - held) // GAME_PROCESSES - max(0, active - ours))


# The pid of the gstack browse server that a browse state file (<checkout>/.gstack/browse.json) names, while that
# process is alive and is a browse server (its command line runs browse/src/server.ts); else None: no file, a dead pid,
# or a pid reused by another program after a hard-killed daemon left its file behind. The Discord reader checks sig's
# browser with it before reading, and health.py's stray_browsers looks for browsers started outside the main checkout.
def browse_server(state_file: Path) -> int | None:
    try:
        pid = json.loads(state_file.read_text()).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return pid if "browse/src/server.ts" in command else None
