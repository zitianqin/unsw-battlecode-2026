"""The game host's side of remote.py: one operation per call, in Python, so the referee sends no shell syntax and
the same calls work on macOS, Linux and Windows whatever shell SSH starts there.

    <python> host_agent.py OP ARGS    ARGS: a JSON object, URL-safe base64 without padding; any payload on stdin

Prints MARK, one JSON line (the result, whose "blob" says how many bytes follow) and then those bytes, e.g. a
replay. A failure exits non-zero with the reason on stderr. Paths are relative to the working directory, which
an SSH session starts in the user's home. Stdlib only, Python 3.10+. Ops:

    platform   "Darwin arm64", "Linux x86_64", "Windows AMD64"
    load       cores, the cores busy (the busy share of them all over a second: busy_cores) and reclaimable GiB;
               with `root`, the games running there, every referee process's (running_games)
    prepare    a venv at `folder` with the pinned Python and the packages on stdin, once; cleanup; unswbc's version;
               with `compiler`, what builds bots here
    upload     stdin to `path`, checked against `sha256`, via a flushed temporary name so no reader sees half a file;
               a file there already is kept only while it still matches `sha256`
    build      a bot's sources (a tar on stdin) built into `folder` with the toolkit at `toolchain`; the executable,
               with `send` its bytes too
    install    another host's build of a bot (stdin) into `folder`, as build would have left it; both keep a build
               there already only while it is whole (`built`)
    play       `unswbc run MAP A B -o replay` in the game `folder`, then removes it: output, exit status, replay
               (zstd-compressed with `compress`), at least the game's CPU seconds where the host reports them (`cpu`,
               game_cpu); with `map_sha256` the map comes on stdin;
               with `record`, record.py plays it instead and its recording follows the replay; with `engine`,
               through engine_cache.py, loading the engine precompiled from the host's own cache folder; with
               `fork`, through fork_server.py, forking each bot process from a server (the shim sent, or built here);
               with `native`, through native_judge.py, which plays it with the native judge where it can; with
               `sandbox`, in the judge's sandbox (`--sandbox`, the bots wasm builds: a CPU gate's game); an upload or
               build it names that is torn since it was sent (`torn_input`) plays nothing and is reported not found
    kill       stops the game in `folder` (its process group; a process tree on Windows) and removes the folder; a
               game not started yet is left a `killed` marker there instead, and its play never runs it
    cleanup    drops uploaded files and builds past the newest KEEP unused for a day, and game folders over an hour old
    crashes    the build UUID (slice_uuid) and time of each macOS crash report of a `name` process (the bots'
               executable) in ~/Library/Logs/DiagnosticReports modified after `since`, from its header line alone
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

MARK = b"__BC_AGENT__ "
SYSTEM = platform.system()  # "Darwin", "Linux" or "Windows"; tests swap it to run another system's branch
DAY = 86400
KEEP = 2000  # uploaded files, and builds, cleanup keeps however old
CLEAN_S = 3600  # cleanup runs at most this often
ZSTD_LEVEL = 3  # a game's replay compressed at 3: about 2.9x smaller, in about 10 ms
KILLED = "killed"  # op_kill's marker in a game folder whose game hadn't started
# What op_play reports for a game killed before it started: as for one its kill stopped (SIGTERM, no replay).
KILLED_REPORT = {"returncode": 128 + signal.SIGTERM, "stdout": "killed before the game started\n", "replay": False,
                 "recording": 0}
# Run by the toolkit's Python: builds the bot folder given like `unswbc run` does and prints the executable's path
# relative to that folder.
BUILD = """import os, pathlib, sys
from unswbc.run import _resolve
command, _, kind = _resolve(sys.argv[1])
if kind != "c/c++" or len(command) != 1:
    sys.exit(f"not a native bot: {kind}")
print(pathlib.Path(os.path.relpath(os.path.realpath(command[0]), os.path.realpath(sys.argv[1]))).as_posix())
"""


class Failure(Exception):
    """The operation failed; the message goes to stderr."""


# A program installed in a venv: bin/NAME, or Scripts\NAME.exe on Windows.
def venv_program(folder: Path, name: str) -> Path:
    return folder / "Scripts" / f"{name}.exe" if SYSTEM == "Windows" else folder / "bin" / name


def run(argv: list[str], timeout: float | None = None) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
    if done.returncode != 0:
        raise Failure(f"{Path(argv[0]).name} exited {done.returncode}: {(done.stdout + done.stderr)[-2000:]}")
    return done.stdout


def find_uv() -> str:
    home = Path.home() / ".local" / "bin" / ("uv.exe" if SYSTEM == "Windows" else "uv")
    for candidate in (os.environ.get("UV"), str(home), shutil.which("uv")):  # `uv run` sets UV to itself
        if candidate and Path(candidate).is_file():
            return candidate
    raise Failure(f"no uv at {home} or on PATH")


def remove(path: Path) -> None:
    for _ in range(5):  # Windows can hold a just-killed game's files open for a moment
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.5)


def op_platform(args: dict, data: bytes) -> tuple[dict, bytes]:
    return {"platform": f"{SYSTEM} {platform.machine()}"}, b""


def read_text(path: str) -> str:
    return Path(path).read_text()


# Reclaimable memory on macOS: free, inactive and speculative pages, as `vm_stat` counts them.
def darwin_memory_gb(vm_stat: str) -> float:
    page = int(re.search(r"page size of (\d+) bytes", vm_stat).group(1))
    pages = sum(int(re.search(rf"Pages {kind}:\s+(\d+)", vm_stat).group(1)) for kind in ("free", "inactive", "speculative"))
    return page * pages / 1024**3


# (idle, total) CPU time so far, summed over every core, in each system's own units: /proc/stat's first line on Linux
# (iowait counts as idle), host_statistics' ticks on macOS, GetSystemTimes on Windows (whose kernel time includes idle).
def linux_times() -> tuple[int, int]:
    user, nice, system, idle, iowait, irq, softirq, steal = map(int, read_text("/proc/stat").split("\n")[0].split()[1:9])
    return idle + iowait, user + nice + system + idle + iowait + irq + softirq + steal


def darwin_times() -> tuple[int, int]:
    import ctypes

    system = ctypes.CDLL(None)
    system.mach_host_self.restype = ctypes.c_uint
    ticks, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)  # user, system, idle, nice
    if system.host_statistics(system.mach_host_self(), 3, ticks, ctypes.byref(count)):  # 3: HOST_CPU_LOAD_INFO
        raise Failure("host_statistics failed")
    return ticks[2], sum(ticks)


def windows_times() -> tuple[int, int]:
    import ctypes

    idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise Failure("GetSystemTimes failed")
    return idle.value, kernel.value + user.value


def windows_available_bytes() -> int:
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), *(
            (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available",
                                                    "virtual_total", "virtual_available", "extended"))]

    status = Status(length=ctypes.sizeof(Status))
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise Failure("GlobalMemoryStatusEx failed")
    return status.available


BUSY_S = 1.0  # how long the load report samples the CPU


# The cores busy now: the busy share of them all over `seconds`, times `cores`. Not the load average, which lags a
# minute and counts runnable processes rather than CPU: a game forks one bot process per dragon (fork_server.py), so on
# 2026-09-27 ll's load stood at 6-8 while its CPU was 2-32% busy, and the referee, filling cores less load, left
# most of it idle.
def busy_cores(cores: int, times, seconds: float = BUSY_S) -> float:
    idle0, total0 = times()
    time.sleep(seconds)
    idle1, total1 = times()
    total = total1 - total0
    return cores * (total - (idle1 - idle0)) / total if total > 0 else 0.0


def op_load(args: dict, data: bytes) -> tuple[dict, bytes]:
    if SYSTEM == "Linux":
        cores = len(os.sched_getaffinity(0))  # what `nproc` reports
        busy = busy_cores(cores, linux_times)
        memory_gb = int(re.search(r"MemAvailable:\s+(\d+) kB", read_text("/proc/meminfo")).group(1)) / 1024**2
    elif SYSTEM == "Darwin":
        cores = os.cpu_count() or 1
        busy = busy_cores(cores, darwin_times)
        memory_gb = darwin_memory_gb(run(["vm_stat"]))
    elif SYSTEM == "Windows":
        cores = os.cpu_count() or 1
        busy = busy_cores(cores, windows_times)
        memory_gb = windows_available_bytes() / 1024**3
    else:
        raise Failure(f"unsupported system {SYSTEM}")
    report = {"cores": cores, "busy": busy, "memory_gb": memory_gb}
    if limit := process_limit():
        report["proc_limit"] = limit
    if args.get("root"):
        report["games"] = running_games(Path(args["root"]))
    return report, b""


# This user's process limit, past which every fork fails ("[Errno 35]"; Host.measure keeps games under it):
# kern.maxprocperuid on macOS, the soft RLIMIT_NPROC on Linux; None on Windows, when unlimited or unreadable.
def process_limit() -> int | None:
    try:
        if SYSTEM == "Darwin":
            return int(run(["sysctl", "-n", "kern.maxprocperuid"]))
        if SYSTEM == "Linux":
            import resource

            soft = resource.getrlimit(resource.RLIMIT_NPROC)[0]
            return None if soft == resource.RLIM_INFINITY else soft
    except (OSError, ValueError, Failure):
        pass
    return None


# The games running on this host now, every referee process's: game folders under ROOT/games whose `pid` (op_play
# writes it as the game starts and removes the folder when it ends) names a live process. On Windows, where signal 0
# can't ask, any folder with a pid under an hour old (what cleanup leaves) counts.
def running_games(root: Path, now: float | None = None) -> int:
    now = time.time() if now is None else now
    count = 0
    for pid_file in (root / "games").glob("*/pid") if (root / "games").is_dir() else ():
        try:
            pid, age = int(pid_file.read_text()), now - pid_file.stat().st_mtime
        except (OSError, ValueError):
            continue
        if SYSTEM == "Windows":
            count += age < 3600
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        count += 1
    return count


# Keeps the newest KEEP uploaded files and the newest KEEP builds, and anything else used within a day (a run re-uploads
# or rebuilds what it still needs), and drops game folders left behind over an hour ago; at most every CLEAN_S, since
# a scan of mm's 44k files took 1.6 s of CPU and ran at the start of every referee run. Toolchains and this helper
# stay. Before 2026-09-27 anything used within 14 days stayed: mm held 1.5 GB of files.
def cleanup(root: Path, now: float | None = None) -> None:
    now = time.time() if now is None else now
    stamp = root / ".cleaned"
    try:
        if now - stamp.stat().st_mtime < CLEAN_S:
            return
    except OSError:
        pass
    root.mkdir(parents=True, exist_ok=True)
    stamp.touch()

    def used(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return now

    files = [p for p in (root / "files").rglob("*") if p.is_file()] if (root / "files").is_dir() else []
    builds = list((root / "builds").iterdir()) if (root / "builds").is_dir() else []
    for paths, drop in ((files, remove_file), (builds, lambda p: shutil.rmtree(p, ignore_errors=True))):
        for path in sorted(paths, key=used, reverse=True)[KEEP:]:
            if now - used(path) > DAY:
                drop(path)
    for path in (root / "games").iterdir() if (root / "games").is_dir() else ():
        if now - used(path) > 3600:
            shutil.rmtree(path, ignore_errors=True)


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()  # its folder, named by the content, once empty
    except OSError:
        pass


def op_cleanup(args: dict, data: bytes) -> tuple[dict, bytes]:
    cleanup(Path(args["root"]))
    return {}, b""


# Holds `path` (a lock file) exclusively until the block ends, waiting for any other holder: two referee processes
# preparing one host at once removed each other's half-made venv (az-mel's 1.2.0 one lost bin/python, 28 Sep).
class FileLock:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(path, "a+b")

    def __enter__(self) -> None:
        if SYSTEM == "Windows":
            import msvcrt
            while True:
                try:
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    time.sleep(1)
        import fcntl
        fcntl.flock(self.file, fcntl.LOCK_EX)

    def __exit__(self, *exc: object) -> None:
        if SYSTEM == "Windows":
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)  # Windows frees a closed file's lock only lazily
        self.file.close()  # closing releases the lock


# Installs the packages listed on stdin into a venv at `folder` with Python `python`, unless an earlier call
# finished (`.ready`), then reports the unswbc there, and with `compiler` what builds bots here (`compiler_identity`).
def op_prepare(args: dict, data: bytes) -> tuple[dict, bytes]:
    folder = Path(args["folder"])
    python = str(venv_program(folder, "python"))
    with FileLock(folder.parent / f"{folder.name}.lock"):
        # A venv marked ready with its Python or unswbc gone (az-mel's 1.2.0 one, 28 Sep: every game then failed to
        # start) is made again.
        whole = Path(python).exists() and venv_program(folder, "unswbc").exists()
        if not (folder / ".ready").is_file() or not whole:
            uv = find_uv()
            shutil.rmtree(folder, ignore_errors=True)
            run([uv, "venv", "-q", "--python", args["python"], str(folder)], timeout=600)
            (folder / "requirements.txt").write_bytes(data)
            run([uv, "pip", "install", "-q", "--python", python, "-r", str(folder / "requirements.txt")], timeout=600)
            if hasattr(os, "sync"):  # the venv on disk before its marker, so a crash can't leave a torn one marked ready
                os.sync()
            (folder / ".ready").touch()
    cleanup(Path(args["root"]))
    version = run([python, "-c", "import importlib.metadata as m; print(m.version('unswbc'))"], timeout=60)
    return {"unswbc": version.strip(), **({"compiler": compiler_identity(python)} if args.get("compiler") else {})}, b""


# Run by the toolkit's Python: the C++ compiler the toolkit builds with, and what it says of its version.
COMPILER = """import subprocess
from unswbc import toolchain
argv = toolchain.find_compiler(False)
done = subprocess.run([*argv, "--version"], capture_output=True, text=True, errors="replace", timeout=60)
print(" ".join(argv)); print(done.stdout + done.stderr)
"""


# What a bot's build here depends on besides its sources and the toolkit: the system, the compiler the toolkit finds
# and its version, and on Linux the C library's version (`ldd --version`). Hosts that report the same build
# byte-identical executables (the ten Ubuntu 24.04 ones, ~1900 pairs checked on 2026-09-27), so the referee builds a bot
# on one and copies it to the rest (remote.shared_build). None when it can't be told: each host then builds its own.
def compiler_identity(python: str) -> str | None:
    try:
        found = run([python, "-c", COMPILER], timeout=90).strip()
        libc = run(["ldd", "--version"], timeout=30).splitlines()[0] if SYSTEM == "Linux" else ""
    except (Failure, OSError, subprocess.TimeoutExpired, IndexError):
        return None
    return f"{SYSTEM} {platform.machine()}\n{found}\n{libc}".strip()


# Flushes the file at `path` to disk. A rename of a file not flushed can outlive a crash with the file's bytes lost:
# zz-wsl, down at 13:52 on 29 Sep, came back with 16 uploaded maps and a bot build's files at 0 bytes (MISSES 94).
def flush(path: Path) -> None:
    handle = os.open(path, os.O_RDWR if SYSTEM == "Windows" else os.O_RDONLY)  # Windows flushes only a writable one
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


# Whether `path` holds exactly the bytes whose sha256 is `digest`: an upload there is reused only then, never for
# being there (MISSES 94). Hashing is cheap next to a game: 0.1 ms for the largest map (190 KB) or a bot executable,
# about 1 ms for 2 MB, on this Mac.
def holds(path: Path, digest: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except OSError:
        return False


# Saves stdin at `path` unless the bytes of `sha256` are there already. A torn file written again is said in the
# reply's "repaired", which the referee prints and keeps in the host's status (remote.Host.agent).
def op_upload(args: dict, data: bytes) -> tuple[dict, bytes]:
    target = Path(args["path"])
    if hashlib.sha256(data).hexdigest() != args["sha256"]:
        raise Failure(f"{target}: received {len(data)} bytes that don't match their checksum")
    repaired = {}
    if holds(target, args["sha256"]):
        os.utime(target)  # in use again: cleanup drops only files no run has asked for in a day
    else:  # missing, or torn by a crash: written again
        if target.is_file():
            repaired["repaired"] = f"{target}: torn upload ({target.stat().st_size} bytes) written again"
        target.parent.mkdir(parents=True, exist_ok=True)
        # A temporary name per process: another referee may be sending the same file right now.
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        temporary.chmod(0o755)
        flush(temporary)
        try:
            os.replace(temporary, target)
        except PermissionError:  # Windows: the other copy won and is open; it has the same content
            temporary.unlink(missing_ok=True)
            if not holds(target, args["sha256"]):
                raise
    return {"path": target.as_posix(), **repaired}, b""


def extract(archive: bytes, into: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(into, filter="data")
        else:
            tar.extractall(into)


# The executable a whole build in `folder` names (its path inside), or None: a build is reused only while its `.bc-bot`
# marker names a non-empty executable that matches the sha256 in `.bc-bot.sha256` beside it (builds before 29 Sep
# have none: the size alone), never for being there (MISSES 94: zz-wsl's crash left a build's files, executable and
# marker, at 0 bytes). `.bc-bot` stays the executable's path alone: an agent from before reads it as that.
def built(folder: Path) -> str | None:
    try:
        relative = (folder / ".bc-bot").read_text().strip()
        blob = (folder / relative).read_bytes() if relative else b""
        sidecar = folder / ".bc-bot.sha256"
        digest = sidecar.read_text().strip() if sidecar.is_file() else ""
    except OSError:
        return None
    if not blob or digest and hashlib.sha256(blob).hexdigest() != digest:
        return None
    return relative


# Makes the build in the scratch `folder` whole on disk before it is renamed into place: the executable at `relative`
# flushed, then its sha256 (`.bc-bot.sha256`), then the marker naming it, each written and flushed in that order, so
# a crash leaves a build `built` accepts or one it rejects, never a torn one it trusts.
def seal(folder: Path, relative: str) -> None:
    executable = folder / relative
    flush(executable)
    sidecar, marker = folder / ".bc-bot.sha256", folder / ".bc-bot"
    sidecar.write_text(hashlib.sha256(executable.read_bytes()).hexdigest() + "\n")
    flush(sidecar)
    marker.write_text(relative)
    flush(marker)


# Moves a torn build out of the way (renamed first, so a reader sees it whole or not at all) and removes it, unless
# what it moved is whole after all: another referee's rebuild, landed since this one looked, goes back. True when it
# removed a torn build.
def discard(folder: Path) -> bool:
    aside = folder.with_name(f"{folder.name}.torn{os.getpid()}")
    try:
        os.replace(folder, aside)
    except OSError:  # not there, or another referee moved it first
        return False
    if built(aside) is not None:
        try:
            os.replace(aside, folder)
        except OSError:  # a third build took the place meanwhile: this copy isn't needed
            shutil.rmtree(aside, ignore_errors=True)
        return False
    shutil.rmtree(aside, ignore_errors=True)
    return True


# The executable of the whole build at `done` (`built`), else None once a torn one there is set aside (`discard`),
# which `repaired` records for the reply (remote.Host.agent prints it and keeps it in the host's status).
def whole_build(done: Path, repaired: dict) -> str | None:
    if (executable := built(done)) is None:
        if discard(done):
            repaired["repaired"] = f"{done}: torn build set aside, built again"
        executable = built(done)  # whole after all: another referee's build, put back
    return executable


# Moves the sealed `scratch` build into place at `done` and returns its executable, or the build another referee put
# there first.
def install_build(scratch: Path, done: Path, relative: str) -> str:
    try:
        os.replace(scratch, done)
        return relative
    except OSError:  # another referee finished the same build first
        shutil.rmtree(scratch, ignore_errors=True)
        if (found := built(done)) is None:
            raise
        return found


# Builds once per `folder`: in a scratch folder moved into place whole, so a failed build leaves nothing.
def op_build(args: dict, data: bytes) -> tuple[dict, bytes]:
    done = Path(args["folder"])
    repaired = {}
    executable = whole_build(done, repaired)
    if executable is None:
        scratch = done.with_name(f"{done.name}.tmp{os.getpid()}")
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        try:
            extract(data, scratch)
            relative = run([str(venv_program(Path(args["toolchain"]), "python")), "-c", BUILD, str(scratch)],
                           timeout=600).strip()
            if not relative:
                raise Failure("the build named no executable")
            seal(scratch, relative)
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        executable = install_build(scratch, done, relative)
    else:
        os.utime(done)  # in use again, as op_upload: cleanup must not drop a build a run still uses
    if os.name == "nt":
        bundle_runtime(done / executable)
    return {"executable": executable, **repaired}, (done / executable).read_bytes() if args.get("send") else b""


# Puts another host's build of the same bot (stdin, checked against `sha256`) at `executable` in `folder`, as op_build
# would have left it, unless a whole build (`built`) is there already.
def op_install(args: dict, data: bytes) -> tuple[dict, bytes]:
    done = Path(args["folder"])
    relative = Path(args["executable"])
    if relative.is_absolute() or ".." in relative.parts:
        raise Failure(f"{relative}: not a path inside the build folder")
    if hashlib.sha256(data).hexdigest() != args["sha256"]:
        raise Failure(f"{done}: received {len(data)} bytes that don't match their checksum")
    repaired = {}
    executable = whole_build(done, repaired)
    if executable is None:
        scratch = done.with_name(f"{done.name}.tmp{os.getpid()}")
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            (scratch / relative).parent.mkdir(parents=True)
            (scratch / relative).write_bytes(data)
            (scratch / relative).chmod(0o755)
            seal(scratch, relative.as_posix())
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        executable = install_build(scratch, done, relative.as_posix())
    else:
        os.utime(done)
    if os.name == "nt":
        bundle_runtime(done / executable)
    return {"executable": executable, **repaired}, b""


# On Windows a MinGW-built bot needs its compiler's runtime DLLs (libstdc++, libgcc, winpthread), and the engine
# starts bots with its own environment, where they aren't on PATH: every bot then exits at round 0 (seen on the
# first Windows host). Copies next to the executable, where Windows always looks first, each DLL in the compiler's
# folder that it names, and the ones those name in turn (libstdc++ needs libwinpthread).
def bundle_runtime(executable: Path, compiler: str | None = None) -> list[str]:
    found = compiler or shutil.which(os.environ.get("CXX") or "g++") or shutil.which("c++")
    if not found or not executable.is_file():
        return []
    available = {dll.name.lower(): dll for dll in Path(found).parent.glob("*.dll")}
    needed: set[str] = set()
    images = [executable.read_bytes().lower()]
    while images:
        image = images.pop()
        for name, dll in available.items():
            if name not in needed and name.encode() in image:
                needed.add(name)
                images.append(dll.read_bytes().lower())
    copied = []
    for name in sorted(needed):
        target = executable.parent / available[name].name
        if not target.exists():
            shutil.copy2(available[name], target)
            copied.append(available[name].name)
    return copied


# A non-interactive SSH session allows 256 open files; a game with 128 dragons needs far more.
def raise_open_files() -> None:
    if SYSTEM == "Windows":
        return
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = hard
    if SYSTEM == "Darwin":  # macOS refuses more than kern.maxfilesperproc, even under an unlimited hard limit
        want = int(run(["sysctl", "-n", "kern.maxfilesperproc"]))
        want = want if hard == resource.RLIM_INFINITY else min(want, hard)
    if want != soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))


# Plays one game in `folder` with the toolkit `unswbc`, team A's bot first, and removes the folder. The game leads
# its own process group (process tree on Windows), recorded in `pid` for `kill`; a folder a kill reached first (its
# KILLED marker) plays nothing and gets KILLED_REPORT. With `record` ({"script", "team"}),
# the toolkit's Python runs that uploaded record.py instead, which plays the same game and records the team's side:
# the blob is then the replay followed by the recording, whose length the result's "recording" gives. With `engine`
# ({"script", "cache"}), that uploaded engine_cache.py runs the same command, loading the engine precompiled from the
# host's cache folder. With `fork` ({"script", "shim" or "build", "check"}), that uploaded fork_server.py runs it in
# turn, forking each bot process from a server with the shim sent (a Mac host) or one it builds in `build` (a Linux
# host), and with `check` also plays the game unforked and compares (see fork_server.py). With `map_sha256`, stdin is
# the map, saved at `map` first as op_upload would (one call a game fewer). With `compress` ("zstd"), the blob is
# compressed with Python's compression.zstd at ZSTD_LEVEL where this Python has it (the result's "compressed" says so;
# "recording" stays the uncompressed length): a replay is a median 3.4 MB, and the far Azure hosts took 0.4-1.9 s of
# the game's slot to send one. With `sandbox`, `unswbc run --sandbox`: the bots are wasm builds (a CPU gate's game,
# priced in the sandbox's points).
def op_play(args: dict, data: bytes) -> tuple[dict, bytes]:
    repaired = {}  # the map's upload's note, if it rewrote a torn one (op_upload)
    if args.get("map_sha256"):
        if note := op_upload({"path": args["map"], "sha256": args["map_sha256"]}, data)[0].get("repaired"):
            repaired["repaired"] = note
    if (torn := torn_input(args)) is not None:  # the referee sends everything again and replays it (remote.Host.play)
        return {"returncode": 1, "stdout": f"not found: {torn} whole ({TORN}, MISSES 94)\n",
                "replay": False, "recording": 0, **repaired}, b""
    folder = Path(args["folder"]).resolve()
    game_map, first, second = (str(Path(p).resolve()) for p in (args["map"], *args["bots"]))
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / KILLED).exists():  # killed before it started (op_kill): reported as a game its kill stopped
        remove(folder)
        return KILLED_REPORT, b""
    raise_open_files()
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if SYSTEM == "Windows"
             else {"start_new_session": True})
    unswbc = Path(args["unswbc"]).resolve()
    python = str(venv_program(unswbc.parent.parent, "python"))
    game = [game_map, first, second, "-o", str(folder / "game.replay")]
    if record := args.get("record"):
        tail = [str(Path(record["script"]).resolve()), *game, "--team", record["team"],
                "--recording", str(folder / "game.recording")]
    else:
        tail = ["run", *game]
    if engine := args.get("engine"):  # the same command through engine_cache.py, with this host's own cache
        tail = [str(Path(engine["script"]).resolve()), str(Path(engine["cache"]).resolve()), *tail]
    if fork := args.get("fork"):  # and through fork_server.py, with the shim sent or this host's own
        where = ["--shim", str(Path(fork["shim"]).resolve())] if fork.get("shim") else \
            ["--build", str(Path(fork["build"]).resolve())]
        tail = [str(Path(fork["script"]).resolve()), *where, *(["--check"] if fork.get("check") else []), *tail]
    command = [str(unswbc), *tail] if tail[0] == "run" else [python, *tail]
    command += ["--seed", str(args["seed"])] if args.get("seed") is not None else []
    command += ["--sandbox"] if args.get("sandbox") else []  # the judge's sandbox: a CPU gate's game (Host.play)
    if native := args.get("native"):  # native_judge.py plays it natively, or runs `command` when it can't
        command = native_command(native, args, python, game_map, first, second, folder, command)
    try:
        process = subprocess.Popen(
            command,
            cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1"}, **group)
    except OSError as error:
        remove(folder)
        raise Failure(f"cannot run unswbc: {error}") from error
    (folder / "pid").write_text(str(process.pid))
    if (folder / KILLED).exists():  # a kill that came since the check above found no pid to stop
        stop_game(process.pid)
    out, _ = process.communicate()
    cpu = game_cpu()
    replay, recording = folder / "game.replay", folder / "game.recording"
    blob = replay.read_bytes() if replay.is_file() else None
    recorded = recording.read_bytes() if recording.is_file() else b""
    remove(folder)
    code = process.returncode if process.returncode >= 0 else 128 - process.returncode  # as a shell reports a signal
    report = {"returncode": code, "stdout": out.decode(errors="replace"), "replay": blob is not None,
              "recording": len(recorded), **({"cpu": cpu} if cpu is not None else {}), **repaired}
    payload = (blob or b"") + recorded
    if args.get("compress") == "zstd" and payload:
        try:
            from compression import zstd
        except ImportError:  # before Python 3.14, or built without it: sent as it is
            pass
        else:
            payload, report["compressed"] = zstd.compress(payload, ZSTD_LEVEL), "zstd"
    return report, payload


TORN = "torn since it was sent"  # in op_play's "not found:" line for a torn input: remote.Host.play says and checks it
UPLOADED = re.compile(r"[0-9a-f]{24}")  # an uploaded file's folder name: its sha256's start (remote.Host.upload)


# The first of a game's inputs a crash has torn since the referee sent it, or None: every uploaded file `args` names
# (files/<its sha256[:24]>/<name>: the map, the bots sent, the scripts) must still match its folder's name, and every
# bot built here (builds/<folder>/...) still be whole (`built`). A referee process remembers what it sent, so without
# this one that stayed up across a host's crash sent none of it again (MISSES 94). About 0.1 ms a file of 190 KB.
def torn_input(args: dict) -> str | None:
    def named(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, (list, dict)):
            for item in value.values() if isinstance(value, dict) else value:
                yield from named(item)

    for text in named(args):
        path = Path(text)
        if path.parent.parent.name == "files" and UPLOADED.fullmatch(path.parent.name):
            try:
                if not hashlib.sha256(path.read_bytes()).hexdigest().startswith(path.parent.name):
                    return text
            except OSError:  # gone: the game itself says what it couldn't find
                continue
    for bot in args["bots"]:
        folder = next((p for p in Path(bot).parents if p.parent.name == "builds"), None)
        if folder is not None and folder.is_dir() and built(folder) is None:
            return bot
    return None


# The command that plays a game through the uploaded native_judge.py (`native`: {"script", "binary"} on a Mac host,
# {"script", "build", "sources"} on a Linux one, each with the referee's "engine" and "toolkit", which the host's must
# be), with the same fork server shim `args["fork"]` gives the plain game, and `command`, that plain game, to run
# instead whenever the native judge can't.
def native_command(native: dict, args: dict, python: str, game_map: str, first: str, second: str, folder: Path,
                   command: list[str]) -> list[str]:
    where = (["--binary", str(Path(native["binary"]).resolve())] if native.get("binary") else
             ["--build", str(Path(native["build"]).resolve()),
              *(part for source in native.get("sources", []) for part in ("--source", str(Path(source).resolve())))])
    fork = args.get("fork") or {}
    shim = (["--shim", str(Path(fork["shim"]).resolve())] if fork.get("shim") else
            ["--fork-script", str(Path(fork["script"]).resolve()), "--shim-build", str(Path(fork["build"]).resolve())]
            if fork.get("build") else [])
    cache = str(Path(args["engine"]["cache"]).resolve()) if args.get("engine") else str(folder / "engine-cache")
    return [python, str(Path(native["script"]).resolve()), "play", *where, "--cache", cache,
            "--engine", str(native.get("engine")), "--toolkit", str(native.get("toolkit")), *shim,
            *(["--seed", str(args["seed"])] if args.get("seed") is not None else []),
            "-o", str(folder / "game.replay"), game_map, first, second, "--", *command]


# At least the CPU seconds (user and system) of the game this call ran, or None on Windows, which has no resource
# module: this process runs one game (one op a call), so its waited-for children's usage is that game's, to a tenth of
# a second (run.GameResult.cpu, for comparing hosts on the same games). A lower bound: a bot process its parent never
# reaped (one left running, or orphaned when the game was killed) isn't counted.
def game_cpu() -> float | None:
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return round(usage.ru_utime + usage.ru_stime, 1)


# Ends the game process `pid` and everything it started: its process group (op_play starts it leading one), or on
# Windows its process tree.
def stop_game(pid: int) -> None:
    if SYSTEM == "Windows":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        if sig == signal.SIGTERM:
            time.sleep(1)


# The game op_play started in `folder`, or None before it wrote the pid.
def game_pid(folder: Path) -> int | None:
    try:
        return int((folder / "pid").read_text())
    except (OSError, ValueError):
        return None


# Stops the game in `folder` and removes the folder. A kill can reach a host before its play has started the game
# (the referee kills its games when a run stops, or when a play's SSH call fails, while that call may still be
# starting on the host): it then finds no pid, so it leaves the folder with a KILLED marker, which op_play checks before
# starting the game and again after writing its pid, so the game never starts or stops itself at once; cleanup removes
# a marked folder no play came for, with the other game folders over an hour old. Marker first, pid second here, and
# pid first, marker second there, so one of the two always sees the other.
def op_kill(args: dict, data: bytes) -> tuple[dict, bytes]:
    folder = Path(args["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / KILLED).touch()
    if (pid := game_pid(folder)) is None:
        return {"killed": False}, b""
    stop_game(pid)
    remove(folder)
    return {"killed": True}, b""


CRASH_REPORTS = Path.home() / "Library/Logs/DiagnosticReports"  # macOS's crash reports (.ips) of this user's processes


# The crash reports of `name` processes modified after `since` (seconds), as {"reports": [{"uuid", "at"}]}: only each
# report's first line (its JSON header) is read, and only its build UUID and time leave the host. None elsewhere than
# macOS, whose folder doesn't exist.
def op_crashes(args: dict, data: bytes) -> tuple[dict, bytes]:
    name, since, reports = str(args["name"]), float(args["since"]), []
    for path in sorted(CRASH_REPORTS.glob(f"{name}-*.ips")) if CRASH_REPORTS.is_dir() else []:
        try:
            at = path.stat().st_mtime
            if at <= since:
                continue
            with path.open() as handle:
                header = json.loads(handle.readline())
        except (OSError, ValueError):
            continue
        if isinstance(header, dict) and header.get("app_name") == name and isinstance(header.get("slice_uuid"), str):
            reports.append({"uuid": header["slice_uuid"].lower(), "at": at})
    return {"reports": reports}, b""


OPS = {"platform": op_platform, "load": op_load, "prepare": op_prepare, "upload": op_upload, "build": op_build,
       "install": op_install, "play": op_play, "kill": op_kill, "cleanup": op_cleanup, "crashes": op_crashes}
# Only these read stdin, and play only when it sends the map: other calls may inherit a stdin that never closes.
READS_STDIN = {"prepare", "upload", "build", "install"}


def reads_stdin(op: str, args: dict) -> bool:
    return op in READS_STDIN or op == "play" and bool(args.get("map_sha256"))


def encode(args: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(args).encode()).decode().rstrip("=")


def decode(text: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in OPS:
        print(f"usage: host_agent.py {{{','.join(OPS)}}} ARGS", file=sys.stderr)
        return 2
    try:
        args = decode(argv[2])
        result, blob = OPS[argv[1]](args, sys.stdin.buffer.read() if reads_stdin(argv[1], args) else b"")
    except Failure as error:
        print(f"{argv[1]}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(MARK + json.dumps({**result, "blob": len(blob)}).encode() + b"\n" + blob)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
