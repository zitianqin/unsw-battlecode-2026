"""Runs `unswbc run` (or a script such as engine_cache.py or record.py) with each native bot process forked from a copy
of the bot started once per team, instead of launched afresh for every dragon.

    <toolkit python> fork_server.py (--shim FILE | --build FOLDER) [--check] run MAP BOT_A BOT_B -o REPLAY [--seed N]
    <toolkit python> fork_server.py (--shim FILE | --build FOLDER) [--check] SCRIPT.py ARGS...

The toolkit starts one process per dragon (unswbc.bot.Pool, 8 spares ahead): some 800 a 500-round game, each paying
for Python's fork and exec from a 144 MB process and the bot's own start (dyld loading libc++) and exit, about 2.7 ms
together, some 35% of a native game's CPU. Here each team's Pool starts the bot once more with SHIM (a small
library, SHIM_SOURCE below) preloaded (DYLD_INSERT_LIBRARIES, LD_PRELOAD): its constructor runs before the
executable's own static initialisers and, told a control socket by BC_FORK_SERVER_FD, never returns; it serves the
Pool instead. For each process the Pool asks for, it forks, and the child takes the pipes the Pool made (sent over the
socket) as its stdin, stdout and stderr, closes everything else, clears errno and returns from the constructor: the
bot's static initialisers and main then run in it as in a freshly exec'd process, with the same argv, environment
(the two variables removed first), working folder, signal dispositions and open files (0-2 only). The server reaps
its children, killing one when asked (Bot.stop), and kills and reaps any left when the Pool closes or the game dies.
What differs from a fresh process:
- every process of a team shares one address layout (ASLR draws one per exec), and one stack-protector canary and
  the other values seeded from AT_RANDOM (drawn once, at the server's exec);
- the parent pid is the server's, not the game's;
- the shim stays loaded in every child (an extra image, inert without BC_FORK_SERVER_FD);
- the initialisers of libraries the dynamic loader runs before the shim's (libSystem always; on Linux usually
  libstdc++, libm and libgcc_s too, which glibc initialises before a preloaded library) ran once, in the server, and
  their state is inherited, not rebuilt;
- they start sooner.
A bot whose play depended on any of these would already play differently from game to game or machine to machine,
but `--check` plays a share of games both ways anyway (below). Measured 2026-09-27: byte-identical replays on 60 of 60
test pairs, ~31% less CPU a native game on this Mac.

The patch replaces Pool's __init__, _launch and close and wraps unswbc.run.execute, so it only applies to a toolkit
whose TOOLKIT_FILES digest to one in KNOWN_TOOLKITS, read by hand; on any other one, on Windows, for Python or wasm
bots and in the sandbox (SandboxPool isn't a Pool), it leaves the toolkit alone and says why on stderr once. A server
that doesn't start (the shim won't load into that bot, no reply within START_S) leaves that team to start its
processes as the toolkit does, said once. One that fails a request mid-game (it closed its socket, a send failed, or
no answer within REQUEST_S) is asked nothing more, neither spawns nor kills nor waits, since a late reply would be
read as the next one's: the team's new processes start as usual, and its forked ones are ended by closing their
stdin, never by signalling a pid the game can no longer vouch for. It is never closed before its Pool either: closing
it kills every process it started, dragons in play included, which would change the game. A fork that fails (EAGAIN
at this user's process limit) raises OSError as Popen does, so the game fails with the engine's OS error and the
referee replays it after a pause (run.infrastructure_failure, run.REPLAY_WAITS_S).

`--check` plays the game twice in one process: first as the toolkit would (its output, replay and exit status are the
game's), then forked, with its output discarded and its replay kept aside, and says on stderr whether the two
replays are identical, byte for byte ("fork server: check identical ..." or "fork server: MISMATCH ...", which an
exception in the forked half also gives). A forked half that hit an OS error (a fork refused at the process limit,
raised or reported as the toolkit's "error: [Errno N] ...") or in which a team started its processes as usual
compares nothing ("check skipped"), and neither changes the plain game. The referee asks for it on a fixed share of
games (run.fork_check) and switches the fork server off after any mismatch whose plain game repeats when played
again there (run.plain_differs: one that doesn't is the bots' own nondeterminism).

SHIM is loaded into every bot process, so like engine_cache.py's compiled engine it must be a file only the loop
writes: --build compiles it (with cc) into FOLDER, <loop home>/fork-server here or the host's own .bc-remote/fork-server
on a Linux host, and refuses files there that others could have written; a Mac host is sent this Mac's (--shim), as
it is sent this Mac's bot executables. Stdlib and the toolkit only: remote hosts run this file alone."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import platform
import re
import runpy
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# The toolkit files the patch was written for (sha256 of their sha256s, first 16 hex digits): unswbc 1.1.0's. A new
# toolkit's files need reading (does Pool still launch through _launch with the same pipes, does Bot, with
# turn.TurnBot, still use only pid, stdin, kill(), wait() and wait(timeout) of the process, does the toolkit still call
# unswbc.run.execute by attribute, with the replay path as its fourth argument?) before their digest is added.
TOOLKIT_FILES = ("bot.py", "turn.py", "run.py", "cli.py")
KNOWN_TOOLKITS = {"8a91afda435cca09"}
CONTROL = "BC_FORK_SERVER_FD"
PRELOAD = "DYLD_INSERT_LIBRARIES" if sys.platform == "darwin" else "LD_PRELOAD"
START_S = 10.0  # a server that hasn't said hello by then isn't one: that team starts its processes as usual
REQUEST_S = 30.0  # a server that takes longer to answer a request is asked nothing more (Server._ask)
OS_ERROR = re.compile(r"^error: \[Errno \d+\].*$", re.M)  # the toolkit failing on an OS error, as run.ENGINE_OS_ERROR
MESSAGE = struct.Struct("=iii")  # request (op, pid, timeout ms), reply (ok, pid, wait status): SHIM_SOURCE's layout

SHIM_SOURCE = r"""
/* fork_server.py's shim: see its docstring. Requests are three int32s (op, pid, timeout ms), replies three int32s
   (ok, pid, wait status): 'S' (with three fds attached: stdin, stdout, stderr) forks a child and replies its pid;
   'K' kills the child and reaps it; 'W' waits up to timeout ms (-1: until it exits) for it to exit on its own.
   ok is 1 done, 0 still running, or -errno. End of file on the socket (the Pool closing, or the game gone): kill and
   reap every child, then exit 0. Any other socket error: exit at once (111, 112), leaving the children to the game,
   which ends them by closing their stdin, never by killing a pid it can no longer vouch for. */
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifdef __APPLE__
#define PRELOAD "DYLD_INSERT_LIBRARIES"
#else
#define PRELOAD "LD_PRELOAD"
#endif
#define MAX_CHILDREN 4096

static pid_t children[MAX_CHILDREN];
static int count;

static int find(pid_t pid) {
    for (int i = 0; i < count; i++)
        if (children[i] == pid) return i;
    return -1;
}

static void forget(int i) { children[i] = children[--count]; }

static void reply(int control, int32_t ok, int32_t pid, int32_t status) {
    int32_t message[3] = {ok, pid, status};
    char *at = (char *)message;
    size_t left = sizeof message;
    while (left) {
        ssize_t sent = write(control, at, left);
        if (sent < 0 && errno == EINTR) continue;
        if (sent <= 0) _exit(111);
        at += sent;
        left -= (size_t)sent;
    }
}

static pid_t reap(pid_t pid, int options, int *status) {
    pid_t got;
    do got = waitpid(pid, status, options); while (got < 0 && errno == EINTR);
    return got;
}

static void finish(void) {
    for (int i = 0; i < count; i++) kill(children[i], SIGKILL);
    for (int i = 0; i < count; i++) {
        int status;
        reap(children[i], 0, &status);
    }
    _exit(0);
}

/* 1 when the child exited within timeout_ms (-1: no limit), 0 when not yet, -1 on an error. */
static int wait_for(pid_t pid, int timeout_ms, int *status) {
    if (timeout_ms < 0) return reap(pid, 0, status) == pid ? 1 : -1;
    struct timespec step = {0, 1000000};
    for (int waited = 0;; waited++) {
        pid_t got = reap(pid, WNOHANG, status);
        if (got == pid) return 1;
        if (got < 0) return -1;
        if (waited >= timeout_ms) return 0;
        nanosleep(&step, NULL);
    }
}

__attribute__((constructor)) static void serve(void) {
    const char *value = getenv("BC_FORK_SERVER_FD");
    if (value == NULL) return;
    int control = atoi(value);
    unsetenv("BC_FORK_SERVER_FD");
    unsetenv(PRELOAD);
    reply(control, 1, (int32_t)getpid(), 0);
    for (;;) {
        int32_t request[3];
        union { struct cmsghdr header; char space[CMSG_SPACE(3 * sizeof(int))]; } control_data;
        struct iovec part = {request, sizeof request};
        struct msghdr message;
        memset(&message, 0, sizeof message);
        message.msg_iov = &part;
        message.msg_iovlen = 1;
        message.msg_control = control_data.space;
        message.msg_controllen = sizeof control_data.space;
        ssize_t got = recvmsg(control, &message, 0);
        if (got < 0 && errno == EINTR) continue;
        if (got == 0) finish();
        if (got < 0) _exit(112); /* not the game closing its end: leave the children to it, as reply() does */
        int fds[3], passed = 0;
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&message); c != NULL; c = CMSG_NXTHDR(&message, c))
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
                int n = (int)((c->cmsg_len - CMSG_LEN(0)) / sizeof(int));
                int *given = (int *)CMSG_DATA(c);
                for (int i = 0; i < n; i++)
                    if (passed < 3) fds[passed++] = given[i]; else close(given[i]);
            }
        for (size_t have = (size_t)got; have < sizeof request;) { /* the rest of a split request, never with fds */
            ssize_t more = read(control, (char *)request + have, sizeof request - have);
            if (more < 0 && errno == EINTR) continue;
            if (more == 0) finish();
            if (more < 0) _exit(112);
            have += (size_t)more;
        }
        pid_t pid = request[1];
        int status = 0, at;
        switch (request[0]) {
        case 'S':
            if (passed != 3 || count == MAX_CHILDREN) {
                for (int i = 0; i < passed; i++) close(fds[i]);
                reply(control, passed != 3 ? -EINVAL : -EAGAIN, 0, 0);
                break;
            }
            pid = fork();
            if (pid == 0) { /* the new bot process: on to its own initialisers and main */
                for (int i = 0; i < 3; i++) dup2(fds[i], i);
                for (int i = 0; i < 3; i++) close(fds[i]);
                close(control);
                errno = 0;
                return;
            }
            int failed = errno;
            for (int i = 0; i < 3; i++) close(fds[i]);
            if (pid < 0) { reply(control, -failed, 0, 0); break; }
            children[count++] = pid;
            reply(control, 1, pid, 0);
            break;
        case 'K':
            if ((at = find(pid)) < 0) { reply(control, -ESRCH, pid, 0); break; }
            kill(pid, SIGKILL);
            if (reap(pid, 0, &status) == pid) reply(control, 1, pid, status); else reply(control, -errno, pid, 0);
            forget(at);
            break;
        case 'W':
            if ((at = find(pid)) < 0) { reply(control, -ESRCH, pid, 0); break; }
            switch (wait_for(pid, request[2], &status)) {
            case 1: forget(at); reply(control, 1, pid, status); break;
            case 0: reply(control, 0, pid, 0); break;
            default: reply(control, -errno, pid, 0);
            }
            break;
        default:
            reply(control, -EINVAL, pid, 0);
        }
    }
}
"""

_said: set[str] = set()


def say(text: str) -> None:
    print(f"fork server: {' '.join(text.split())}", file=sys.stderr, flush=True)


# Says `text` the first time only: a fallback is reported once a game, not once a team or a process.
def say_once(text: str) -> None:
    if text not in _said:
        _said.add(text)
        say(text)


# The digest of the toolkit files the patch depends on, or None without a toolkit.
def toolkit_digest() -> str | None:
    try:
        import unswbc
    except ImportError:
        return None
    root = Path(unswbc.__file__).parent
    return hashlib.sha256(b"".join(hashlib.sha256((root / name).read_bytes()).digest()
                                   for name in TOOLKIT_FILES)).hexdigest()[:16]


# None when this machine and toolkit can use the fork server, else why not.
def unsupported() -> str | None:
    if sys.platform not in ("darwin", "linux"):
        return f"not on {sys.platform}"
    found = toolkit_digest()
    if found is None:
        return "no unswbc toolkit"
    if found not in KNOWN_TOOLKITS:
        return (f"unswbc's {', '.join(TOOLKIT_FILES)} changed (digest {found}); re-read them and add it to "
                "KNOWN_TOOLKITS")
    if os.environ.get(PRELOAD) or os.environ.get(CONTROL):
        return f"{PRELOAD} or {CONTROL} is already set"
    return None


# True when only this user could have written `path`.
def private(path: Path) -> bool:
    status = path.stat()
    return status.st_uid == os.getuid() and not status.st_mode & 0o022


# Where the shim built from SHIM_SOURCE lives in `folder`: named by the source and the machine it runs on.
def shim_path(folder: Path) -> Path:
    digest = hashlib.sha256(SHIM_SOURCE.encode()).hexdigest()[:16]
    suffix = "dylib" if sys.platform == "darwin" else "so"
    return folder / f"fork-shim-{digest}-{sys.platform}-{platform.machine()}.{suffix}"


# The shim in `folder`, compiled and saved there (owner-only, via a temporary name flushed to disk first) when missing
# or empty (torn by a crash, MISSES 94). Raises OSError or subprocess.CalledProcessError when it can't be built, and
# PermissionError for files others could have written.
def build_shim(folder: Path) -> Path:
    shim = shim_path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not private(folder):
        raise PermissionError(f"{folder} could have been written by someone else")
    if shim.is_file() and shim.stat().st_size:
        if not private(shim):
            raise PermissionError(f"{shim} could have been written by someone else")
        return shim
    compiler = next((found for name in ("cc", "clang", "gcc") if (found := shutil.which(name))), None)
    if compiler is None:
        raise FileNotFoundError("no C compiler (cc, clang or gcc) to build the shim")
    with tempfile.TemporaryDirectory(dir=folder) as scratch:
        source, built = Path(scratch, "shim.c"), Path(scratch, shim.name)
        source.write_text(SHIM_SOURCE)
        shared = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        subprocess.run([compiler, "-O2", *shared, "-o", str(built), str(source)], check=True, capture_output=True,
                       timeout=120)
        built.chmod(0o700)
        with open(built, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(built, shim)
    return shim


# Referee side, once per run: the shim path games can be given, or why none can (another toolkit or system, no
# compiler, or a folder this process can't write, e.g. inside a coder's sandbox, where the loop home is out of reach).
def warm(folder: Path) -> tuple[Path | None, str | None]:
    if reason := unsupported():
        return None, reason
    try:
        return build_shim(folder), None
    except subprocess.CalledProcessError as error:
        return None, f"cannot compile the shim: {(error.stderr or b'').decode(errors='replace')[-300:]}"
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"{type(error).__name__}: {error}"


class ServerGone(OSError):
    """The fork server stopped answering."""


# A bot process the fork server started, as unswbc.bot.Bot uses a subprocess.Popen: pid, stdin, kill(), wait().
class Child:
    def __init__(self, server: Server, pid: int, stdin, stdout, stderr) -> None:
        self._server, self.pid, self.stdin, self.stdout, self.stderr = server, pid, stdin, stdout, stderr
        self.args, self.returncode = server.argv, None

    # With its server gone, the process is told to end by the end of its input instead (it is past its turn, or the
    # turn's limit would end it): its pid is no longer ours to signal, since whoever reaps it may reuse the number.
    def kill(self) -> None:
        if self.returncode is None:
            code = self._server.kill(self.pid)
            if code is None:
                with contextlib.suppress(OSError):
                    self.stdin.close()
            self.returncode = -9 if code is None else code

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            code = self._server.wait(self.pid, timeout)
            if code is None and timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = -9 if code is None else code  # None: its server went and took it (or left it)
        return self.returncode

    def poll(self) -> int | None:
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None


# One team's fork server: the bot started once with the shim, answering over a unix socket.
class Server:
    def __init__(self, argv: list[str], cwd: str | None, shim: Path) -> None:
        self.argv = argv
        self._lock = threading.Lock()
        self._socket, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            env = {**os.environ, CONTROL: str(theirs.fileno()), PRELOAD: str(shim)}
            self.process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, pass_fds=(theirs.fileno(),), env=env)
        except BaseException:
            self._socket.close()
            raise
        finally:
            theirs.close()
        try:
            self._socket.settimeout(START_S)
            ok, pid, _ = self._receive()
            if (ok, pid) != (1, self.process.pid):
                raise ServerGone(f"it answered {(ok, pid)} to hello")
            self._socket.settimeout(REQUEST_S)
        except (OSError, ValueError) as error:
            self.close()
            raise ServerGone(f"the shim didn't start in {Path(argv[0]).name} ({error})") from error
        self._broken: str | None = None

    def _receive(self) -> tuple[int, int, int]:
        data = b""
        while len(data) < MESSAGE.size:
            more = self._socket.recv(MESSAGE.size - len(data))
            if not more:
                raise ServerGone("it closed its socket")
            data += more
        return MESSAGE.unpack(data)

    # The server's answer (ok, pid, wait status) to one request; ServerGone when it can't be asked. After one failure
    # (it closed its socket, a send failed, no answer within REQUEST_S) it is asked nothing more: a reply that comes
    # late would be read as the answer to the next request.
    def _ask(self, op: str, pid: int = 0, timeout_ms: int = 0, fds: list[int] | None = None) -> tuple[int, int, int]:
        with self._lock:
            if self._broken:
                raise ServerGone(self._broken)
            try:
                message = MESSAGE.pack(ord(op), pid, timeout_ms)
                if fds:
                    socket.send_fds(self._socket, [message], fds)
                else:
                    self._socket.sendall(message)
                return self._receive()
            except OSError as error:  # ServerGone included
                self._broken = str(error) or type(error).__name__
                raise ServerGone(self._broken) from error

    # A new bot process with these ends of its pipes as stdin, stdout and stderr: its pid. A fork that failed raises
    # OSError with its errno, as Popen does (EAGAIN at the process limit); ServerGone when the server can't be asked.
    def spawn(self, stdin: int, stdout: int, stderr: int) -> int:
        ok, pid, _ = self._ask("S", fds=[stdin, stdout, stderr])
        if ok != 1:
            raise OSError(-ok, os.strerror(-ok))
        return pid

    # Kills a child and returns its exit code, as Popen.returncode gives it (-9 when killed), or None when the server
    # can't be asked (Child.kill then closes the child's stdin).
    def kill(self, pid: int) -> int | None:
        try:
            ok, _, status = self._ask("K", pid)
        except ServerGone:
            return None
        return os.waitstatus_to_exitcode(status) if ok == 1 else -9

    # A child's exit code once it has exited on its own within `timeout` seconds (None: however long), else None,
    # also when that can't be known any more (the server is gone).
    def wait(self, pid: int, timeout: float | None) -> int | None:
        try:
            ok, _, status = self._ask("W", pid, -1 if timeout is None else round(timeout * 1000))
        except ServerGone:
            return None
        return os.waitstatus_to_exitcode(status) if ok == 1 else None

    # Ends the server: it kills and reaps any child left, then exits.
    def close(self) -> None:
        self._socket.close()
        try:
            self.process.wait(START_S)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


# Pool._launch's pipes and readers, with the process forked by `server` instead of started by Popen.
def launch(server: Server) -> tuple:
    import unswbc.bot as bot

    pair = bot._record_pair()
    stdin_read, stdin_write = os.pipe()
    stderr_read, stderr_write = os.pipe()
    stdout_read, stdout_write = os.pipe() if pair is None else (None, pair[1].fileno())
    try:
        pid = server.spawn(stdin_read, stdout_write, stderr_write)
    except BaseException:
        for fd in (stdin_write, stderr_read, stdout_read):
            if fd is not None:
                os.close(fd)
        if pair is not None:
            pair[0].close()
        raise
    finally:
        for fd in (stdin_read, stderr_write):
            os.close(fd)
        if pair is None:
            os.close(stdout_write)
        else:
            pair[1].close()
    stdout = io.open(stdout_read, "rb", 0) if pair is None else None
    process = Child(server, pid, io.open(stdin_write, "wb", 0), stdout, io.open(stderr_read, "rb", 0))
    if pair is None:
        return (process, bot._Reader(process.stdout), bot._Reader(process.stderr))
    return (process, bot._Reader(pair[0]), bot._Reader(process.stderr))


# Makes every native Pool fork its processes from a server started with `shim`; with `check`, also makes every game
# play both ways (see the module's docstring). Returns False, having said why, when this toolkit or machine isn't one
# the patch was written for; it is then left alone.
def install(shim: Path, check: bool = False) -> bool:
    if reason := unsupported():
        say(f"off ({reason}); every bot process starts as usual")
        return False
    import unswbc.bot as bot
    import unswbc.run as run

    pool = bot.Pool
    original_init, original_launch, original_close = pool.__init__, pool._launch, pool.close
    # on: off while a checked game plays its plain half; fell_back: a team started its processes as usual
    forking = threading.local()
    forking.on, forking.fell_back = True, False

    def patched_init(self, argv, cwd=None, size=bot.WARM_SPARES, key="a") -> None:
        self._server, self._forking = None, False
        if getattr(forking, "on", True) and bot.ZYGOTE not in argv and len(argv) == 1:
            try:
                self._server, self._forking = Server(argv, cwd, shim), True
            except (OSError, subprocess.SubprocessError) as error:
                forking.fell_back = True
                say_once(f"off for {Path(argv[0]).name} ({error}); its processes start as usual")
        original_init(self, argv, cwd, size, key)

    def patched_launch(self) -> tuple:
        if self._forking:
            try:
                return launch(self._server)
            except ServerGone as error:  # kept for its children's kill and wait; closed with the Pool
                self._forking, forking.fell_back = False, True
                say_once(f"stopped for {Path(self._argv[0]).name} ({error}); its processes start as usual from here")
        return original_launch(self)

    def patched_close(self) -> None:
        try:
            original_close(self)
        finally:
            if self._server is not None:
                self._server.close()
                self._server = None

    pool.__init__, pool._launch, pool.close = patched_init, patched_launch, patched_close
    if check:
        run.execute = checked(run.execute, forking)
    return True


# `execute` playing each game twice, plain then forked, and saying whether the replays are identical.
def checked(execute, forking: threading.local):
    import unswbc.bot as bot
    import unswbc.run as run

    def both(map_arg, bots, verbose, replay_path, no_replay, *rest, **named) -> int:
        if run.Bot is not bot.Bot:  # record.py's RecordingBot: a second game would add its turns to the recording
            say(f"check skipped on {Path(map_arg).name}: the game records")
            return execute(map_arg, bots, verbose, replay_path, no_replay, *rest, **named)
        forking.on = False
        try:
            code = execute(map_arg, bots, verbose, replay_path, no_replay, *rest, **named)
        finally:
            forking.on = True
        replay = Path(replay_path) if replay_path else None
        if code != 0 or no_replay or replay is None or not replay.suffix or not replay.is_file():
            say(f"check skipped on {Path(map_arg).name}: nothing to compare (exit {code})")
            return code
        aside = replay.with_name(f"{replay.stem}.forked{replay.suffix}")
        forking.fell_back, skipped, errors = False, None, io.StringIO()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                forked = execute(map_arg, bots, verbose, str(aside), no_replay, *rest, **named)
            same = forked == 0 and aside.is_file() and aside.read_bytes() == replay.read_bytes()
            why = f"the forked game's replay differs from the plain one's (forked exit {forked})"
            if forked != 0 and (os_error := OS_ERROR.search(errors.getvalue())):  # e.g. a Pool's first forks refused
                skipped = f"the forked half hit an OS error ({os_error.group(0).strip()[:200]})"
        except OSError as error:  # the machine, not the bots (EAGAIN from a fork at the process limit): no verdict
            same, skipped = False, f"the forked half hit an OS error ({error})"
        except Exception as error:  # the plain game stands: its exit and replay are the game's
            same, why = False, f"the forked game raised {type(error).__name__}: {' '.join(str(error).split())[:200]}"
        finally:
            aside.unlink(missing_ok=True)
        if getattr(forking, "fell_back", False):
            skipped = "a team's forked half started its processes as usual"
        if skipped:
            say(f"check skipped on {Path(map_arg).name}: {skipped}")
        elif same:
            say(f"check identical on {Path(map_arg).name}")
        else:
            say(f"MISMATCH on {Path(map_arg).name}: {why}")
        return code

    return both


def main(argv: list[str]) -> int:
    usage = "usage: fork_server.py (--shim FILE | --build FOLDER) [--check] (run ARGS... | SCRIPT.py ARGS...)"
    if len(argv) < 4 or argv[1] not in ("--shim", "--build"):
        print(usage, file=sys.stderr)
        return 2
    where, command = Path(argv[2]).resolve(), argv[3:]
    check = command[0] == "--check"
    command = command[1:] if check else command
    if not command:
        print(usage, file=sys.stderr)
        return 2
    shim = where
    if argv[1] == "--build":
        shim, reason = warm(where)
        if reason:
            say(f"off ({reason}); every bot process starts as usual")
    elif not shim.is_file():
        say(f"off (no shim at {shim}); every bot process starts as usual")
        shim = None
    if shim is not None:
        install(shim, check)
    # Imports resolve as when the command runs directly: not from this file's folder (the referee's, locally).
    if sys.path and Path(sys.path[0] or ".").resolve() == Path(__file__).resolve().parent:
        sys.path.pop(0)
    if command[0].endswith(".py"):
        sys.argv = command
        sys.path.insert(0, str(Path(command[0]).resolve().parent))
        runpy.run_path(command[0], run_name="__main__")  # the script's sys.exit ends the process
        return 0
    from unswbc.cli import main as unswbc

    sys.argv = ["unswbc", *command]
    return unswbc(command)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
