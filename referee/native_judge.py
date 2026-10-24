"""The native judge (native_judge/play.c): one native game played in C with the organisers' exact engine wasm, in
place of `unswbc run`'s Python glue, for the same replay bytes and output at less CPU.

    <toolkit python> native_judge.py build FOLDER
    <toolkit python> native_judge.py play (--binary FILE | --build FOLDER --source FILE...) --cache FOLDER
                     --engine SHA256 --toolkit DIGEST [--shim FILE | --fork-script FILE --shim-build FOLDER]
                     [--seed N] [--debug N] -o REPLAY MAP BOT_A BOT_B -- COMMAND...

`build` compiles the judge for this machine and this toolkit's engine into FOLDER (once: the file is named by the
engine's digest, the sources' digest and the platform) and prints its path, or why it can't. It is the host build
step; the referee and `play` call it themselves when the file is missing. `play` is how a remote host plays a game
natively (host_agent.op_play): it builds or takes the judge, resolves the two executables and replaces itself with
the judge; when anything is off (another toolkit or engine than the referee's, no compiler, a failed build) it says
why on stderr ("native judge: off (...)") and replaces itself with COMMAND, the game as the host would have played it
anyway.

Where it runs (run.native_plan, and Host.play's `native`): native games only, never a sandbox game, a game that records
(record.py hooks the Python bots) or a fork server check game (fork_server.py --check compares its own two paths), on
macOS and Linux (WSL included), not Windows, with wasmtime-py WASMTIME (whose C library the judge loads:
native_judge/wasmtime_min.h declares its ABI) and a toolkit whose glue play.c was read against (KNOWN_TOOLKITS). And
only switched on (run.NATIVE_JUDGE, off until the orchestrator has read a clean 300-game fidelity report) for a `key`
(the engine wasm's sha256, TOOLKIT_FILES' digest, the judge's sources', the fork server shim's, and this file's with
host_agent.native_command's, which make the judge's command line) whose fidelity gate (native_fidelity.py --gate: its
fixed sample identical both ways, replay bytes, result and log) has passed at the place that plays the game (`enabled`,
from <loop home>/native-judge/enabled.json: this Mac, the Mac hosts, each group of Linux hosts that build alike), while
it is the key the process took at its first native game (process_key), and while nothing has switched it off
(run.natives_off: a both-ways check that differed when its plain reference repeats, run.plain_differs, or a judge that
failed or hung on a game unswbc run played). The upkeep gates a new key at each place by itself, hourly
(loop/maintenance.py's native_gates, native_fidelity.py --gate-missing); until a place's gate passes its games play
today's path, and a failed gate keeps them there with an alert.

The judge runs the engine through wasmtime-py's own C library (native_judge/engine_wasmtime.c, loading engine_cache's
compiled copy), behind native_judge/judge_engine.h; native_judge/engine_w2c.c serves the same interface from wasm2c's
translation of the engine, unbuilt by default (its comment says how to build it, and why it isn't: 2026-09-28, 2% of a
game's CPU for a toolchain and minute-long builds per host).

Like fork_server.py's shim and engine_cache.py's compiled engine, the judge is machine code every game runs, so its
FOLDER must be one only the loop writes (<loop home>/native-judge here, the host's .bc-remote/native-judge), and files
there others could have written are refused. Stdlib only, plus the toolkit when present: remote hosts run this file
alone."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import functools
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_DIR = HERE / "native_judge"
SOURCES = ("play.c", "judge_engine.h", "engine_wasmtime.c", "wasmtime_min.h")
# The toolkit files play.c mirrors, and their digests (toolkit_digest) that someone has read play.c against: unswbc
# 1.2.7's (1.1.0-1.2.2's, cd6f852768e80fe9, is gone: play.c now reads the twelve result values engines since 1.2.3
# write, and 1.2.2's writes eight). Any other digest plays today's path (unsupported), gate or not, since the gate's 40 games can miss a
# rare path: re-read play.c against the new files (what run.py's execute prints and when, engine.py's run, turn.py's
# Framer and TurnBot, bot.py's Pool and Bot), fix it, add the digest here, then let the gate run (a new digest is a
# new `key` too).
TOOLKIT_FILES = ("bot.py", "turn.py", "run.py", "cli.py", "engine.py", "color.py", "errors.py")
KNOWN_TOOLKITS = {"f42ab6a7cda63ac2"}
WASMTIME = "49.0.0"
BUILD_S = 120
BUILD_RETRY_S = 3600  # a failed build's `.failed` marker stands this long; the first game after it compiles again


def say(text: str) -> None:
    print(f"native judge: {' '.join(text.split())}", file=sys.stderr, flush=True)


# True when only this user could have written `path`.
def private(path: Path) -> bool:
    status = path.stat()
    return status.st_uid == os.getuid() and not status.st_mode & 0o022


def sources_digest(files: dict[str, Path] | None = None) -> str:
    files = files or {name: SOURCE_DIR / name for name in SOURCES}
    return hashlib.sha256(b"".join(hashlib.sha256(files[name].read_bytes()).digest() for name in SOURCES)).hexdigest()[:16]


def toolkit_digest() -> str | None:
    try:
        import unswbc
    except ImportError:
        return None
    root = Path(unswbc.__file__).parent
    return hashlib.sha256(b"".join(hashlib.sha256((root / name).read_bytes()).digest()
                                   for name in TOOLKIT_FILES)).hexdigest()[:16]


# The toolkit's engine wasm and its sha256, read once a process for each size and time it has had.
def engine_wasm() -> tuple[Path, str] | None:
    try:
        import unswbc.engine as engine
    except ImportError:
        return None
    wasm = Path(engine.WASM_PATH)
    try:
        status = wasm.stat()
    except OSError:
        return None
    return wasm, _digest(str(wasm), status.st_size, status.st_mtime_ns)


@functools.cache
def _digest(path: str, size: int, mtime: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# wasmtime-py's C library, where wasmtime/_ffi.py loads it from.
def library() -> Path | None:
    spec = importlib.util.find_spec("wasmtime")
    if spec is None or not spec.origin:
        return None
    machine = {"AMD64": "x86_64", "arm64": "aarch64", "ARM64": "aarch64"}.get(platform.machine(), platform.machine())
    name = {"darwin": "_libwasmtime.dylib", "linux": "_libwasmtime.so"}.get(sys.platform)
    return Path(spec.origin).parent / f"{sys.platform}-{machine}" / name if name else None


# None when this machine and toolkit can play natively, else why not.
@functools.cache
def unsupported() -> str | None:
    if sys.platform not in ("darwin", "linux"):
        return f"not on {sys.platform}"
    if (found := toolkit_digest()) is None:
        return "no unswbc toolkit"
    if found not in KNOWN_TOOLKITS:
        return (f"unswbc's {', '.join(TOOLKIT_FILES)} changed (digest {found}); re-read play.c against them and add "
                "it to native_judge.KNOWN_TOOLKITS")
    try:
        version = importlib.metadata.version("wasmtime")
    except importlib.metadata.PackageNotFoundError:
        return "no wasmtime"
    if version != WASMTIME:
        return f"wasmtime {version}, not {WASMTIME} (native_judge/wasmtime_min.h)"
    if (lib := library()) is None or not lib.is_file():
        return f"no wasmtime library at {lib}"
    if engine_wasm() is None:
        return "no engine wasm"
    return None


# Where the judge built from `files` for this machine and engine lives in `folder`.
def binary_path(folder: Path, engine: str, files: dict[str, Path] | None = None) -> Path:
    return folder / f"native-judge-{engine[:16]}-{sources_digest(files)}-{sys.platform}-{platform.machine()}"


# The judge in `folder`, compiled there (owner-only, via a temporary name, one build at a time) when missing. Raises
# OSError or subprocess.CalledProcessError when it can't be built, and PermissionError for files others could have
# written. `files` names the sources (a host's uploads), by default this folder's. A failed or hung (past BUILD_S)
# compile is kept beside where the judge would be (`.failed`, its error): for BUILD_RETRY_S those sources aren't
# compiled again for this engine and machine, so every game meanwhile falls back at once instead of queueing on the
# build lock to fail the same way (a compiler missing or hanging for a while, a full disk), and the first after it
# tries again. Raises subprocess.TimeoutExpired for a hung compile.
def build(folder: Path, files: dict[str, Path] | None = None) -> Path:
    wasm = engine_wasm()
    if wasm is None:
        raise FileNotFoundError("no engine wasm")
    binary = binary_path(folder, wasm[1], files)
    failed = binary.with_name(f"{binary.name}.failed")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not private(folder):
        raise PermissionError(f"{folder} could have been written by someone else")
    if not binary.is_file():
        failed_before(failed)
    with open(folder / ".build.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # games starting together on a fresh host build it once
        if binary.is_file():
            if not private(binary):
                raise PermissionError(f"{binary} could have been written by someone else")
            return binary
        failed_before(failed)  # another game's build failed while this one waited
        compiler = next((found for name in ("cc", "clang", "gcc") if (found := shutil.which(name))), None)
        if compiler is None:
            raise FileNotFoundError("no C compiler (cc, clang or gcc) to build the judge")
        files = files or {name: SOURCE_DIR / name for name in SOURCES}
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            for name in SOURCES:
                shutil.copyfile(files[name], Path(scratch, name))
            built = Path(scratch, binary.name)
            flags = ["-mmacosx-version-min=12.0"] if sys.platform == "darwin" else ["-ldl"]
            try:
                subprocess.run([compiler, "-O2", "-std=c11", f"-DENGINE_SHA256=\"{wasm[1]}\"", "-o", str(built),
                                str(Path(scratch, "play.c")), str(Path(scratch, "engine_wasmtime.c")), *flags],
                               check=True, capture_output=True, timeout=BUILD_S)
            except subprocess.CalledProcessError as error:
                failed.write_bytes(error.stderr or b"")
                raise
            except subprocess.TimeoutExpired as error:  # a compiler that hangs, kept as a failure like any other
                failed.write_bytes((error.stderr or b"") + f"\ncompiling took over {BUILD_S} s".encode())
                raise
            built.chmod(0o700)
            os.replace(built, binary)
            failed.unlink(missing_ok=True)
    return binary


# Raises OSError when a build of these sources failed within BUILD_RETRY_S (its `.failed` marker, `failed`).
def failed_before(failed: Path) -> None:
    try:
        age = time.time() - failed.stat().st_mtime
    except FileNotFoundError:
        return
    if age < BUILD_RETRY_S:
        raise OSError(f"its build failed {age / 60:.0f} min ago, kept in {failed} (tried again "
                      f"{BUILD_RETRY_S // 60} min after): {failed.read_text(errors='replace')[-300:]}")


# Referee side, once per run: the judge games can use, or why none can.
def warm(folder: Path, files: dict[str, Path] | None = None) -> tuple[Path | None, str | None]:
    if reason := unsupported():
        return None, reason
    try:
        return build(folder, files), None
    except subprocess.CalledProcessError as error:
        return None, f"cannot compile the judge: {(error.stderr or b'').decode(errors='replace')[-300:]}"
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"{type(error).__name__}: {error}"


# The compiled engine's file in `cache`: engine_cache.cache_path's name (both load the same wasmtime), which the judge
# checks, loads, or writes when missing.
def cwasm_path(cache: Path, engine: str) -> Path:
    return cache / f"{engine}-{WASMTIME}-{sys.platform}-{platform.machine()}.cwasm"


# What `unswbc run` would start for `bot`: its executable and the folder it runs in, resolved as unswbc.run._resolve
# does (building a bot folder that changed, as each game of the toolkit's does; the folder as given, not resolved),
# or None for a bot that isn't one native executable.
def executable(bot: Path) -> tuple[Path, str] | None:
    from unswbc.run import _resolve

    try:
        command, folder, kind = _resolve(str(bot))
    except Exception:  # the toolkit's own errors (no build, not a bot): unswbc run then says what it says
        return None
    return (Path(command[0]), str(folder)) if kind == "c/c++" and len(command) == 1 else None


# The judge's command for one game, team A's bot `first`, or None (said on stderr) when a bot isn't a native
# executable or the engine has gone. `shim`: fork each team's processes from fork_server.py's server.
def command(judge: Path, game_map: Path, first: Path, second: Path, replay: Path, seed: int | None, cache: Path,
            shim: Path | None, debug: int | None = None) -> list[str] | None:
    exes = [executable(bot) for bot in (first, second)]
    if None in exes:
        say(f"off for {first.name} against {second.name}: not both native executables")
        return None
    if (found := engine_wasm()) is None:
        say("off (no engine wasm)")
        return None
    wasm, engine = found
    return [str(judge), "--lib", str(library()), "--wasm", str(wasm), "--cwasm", str(cwasm_path(cache, engine)),
            *(["--shim", str(shim)] if shim else []), *(["--seed", str(seed)] if seed is not None else []),
            *(["--debug", str(debug)] if debug is not None else []),
            "--cwd-a", exes[0][1], "--cwd-b", exes[1][1],
            "-o", str(replay), str(game_map), str(first), str(exes[0][0]), str(second), str(exes[1][0])]


# ---- which engine and toolkit may play natively, and where ----

ENABLED = "enabled.json"  # in <loop home>/native-judge: gate results by key and place (native_fidelity.py --gate)
GATE_ROWS = 20  # results kept in enabled.json's "history" (the latest per key and place stay under "keys")


# What the native judge's fidelity is judged for: the engine wasm's sha256, the toolkit glue's digest, the judge's own
# sources', the fork server shim's the judge's games load, and the launcher's (launcher_digest), first 16 hex digits
# each, or None without a toolkit. Referee side only (it reads fork_server.py and host_agent.py beside this file),
# read afresh each call from what is on disk, but with `loaded` the launcher as this process loaded it (process_key).
# A new engine, toolkit, judge, shim or launcher is a new key, which plays today's path until its gate passes.
def key(loaded: bool = False) -> str | None:
    import fork_server

    wasm, toolkit = engine_wasm(), toolkit_digest()
    shim = hashlib.sha256(fork_server.SHIM_SOURCE.encode()).hexdigest()[:16]
    return (f"{wasm[1][:16]}-{toolkit}-{sources_digest()}-{shim}-{launcher_digest(loaded)}" if wasm and toolkit
            else None)


_LOADED = Path(__file__).resolve().read_bytes()  # this file as this process imported it (launcher_digest's `loaded`)


# What builds the judge's command line: this file (command, host_command, and play's fallback) and
# host_agent.native_command (a host's), as they are on disk, or with `loaded` as this process has them (this module
# and remote.AGENT_SOURCE, which it sends the hosts).
def launcher_digest(loaded: bool = False) -> str:
    if loaded:
        import remote
        agent, own = remote.AGENT_SOURCE.decode(), _LOADED
    else:
        agent, own = (HERE / "host_agent.py").read_text(), Path(__file__).resolve().read_bytes()
    found = next(node for node in ast.parse(agent).body
                 if isinstance(node, ast.FunctionDef) and node.name == "native_command")
    part = ast.get_source_segment(agent, found) or ""
    return hashlib.sha256(own + part.encode()).hexdigest()[:16]


_process_key: list[str | None] = []  # process_key's, once taken
_process_key_lock = threading.Lock()


# The key this process plays under, taken once, at its first native game or gate (run.native_plan,
# native_fidelity.gate_missing), with the launcher as it loaded it: a long-lived process (a filler, a prepared check)
# whose checkout or toolkit changed under it would play its old command lines, its old judge build or its old
# AGENT_SOURCE under the new key's gate, so it plays natively, and gates record, only while key() still equals this.
def process_key() -> str | None:
    with _process_key_lock:
        if not _process_key:
            _process_key.append(key(loaded=True))
        return _process_key[0]


def enabled_path(home: Path) -> Path:
    return home / "native-judge" / ENABLED


# enabled.json, or {} when missing or unreadable (health.native_judge alerts on an unreadable one).
def gates(home: Path) -> dict:
    try:
        return readable_gates(enabled_path(home))
    except OSError:
        return {}


# enabled.json at `path`, {} when missing; OSError when it can't be read or isn't a JSON object with a "keys" object.
def readable_gates(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("keys", {}), dict):
            raise ValueError("not a JSON object with a \"keys\" object")
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise OSError(f"{path} can't be read ({error}); fix or remove it") from error
    return data


# True when this key passed its gate at `place`, where and how a game plays natively: this Mac's platform for its own
# games ("Darwin arm64", `uname -sm`), or remote.Host.native_place's for a host's (Mac hosts together, Linux ones by
# compiler group), and no later gate for them failed.
# `now`: the key as the caller just read it (run.native_plan), else read here.
def enabled(home: Path, place: str, now: str | None = None) -> bool:
    entry = ((gates(home).get("keys") or {}).get(now or key() or "") or {}).get(place) or {}
    return entry.get("passed") is True


# Records a gate's outcome for `gated` (the key it played under) at `place` (the latest replaces the one before).
# Raises OSError, writing nothing, when enabled.json is there but can't be read: it may hold the only record of a
# failed gate.
def record_gate(home: Path, gated: str, place: str, passed: bool, games: int, matched: int, report: str,
                why: str | None = None) -> None:
    path = enabled_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    row = {"key": gated, "platform": place, "passed": passed, "games": games, "matched": matched,
           "report": report, "why": why, "at": time.time()}
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = readable_gates(path)
        data.setdefault("keys", {}).setdefault(row["key"], {})[place] = row
        data["history"] = [*data.get("history", []), row][-GATE_ROWS:]
        write_gates(path, data)


# Keeps the places native_fidelity.py --gate-missing found the loop playing at for key `wanted` under enabled.json's
# "wanted" ({"key", "at": this run, "listed": when the hosts were last asked, "hosts": {name: its place, None for a
# Windows host}, "places": {place: when it was first wanted}}), which health.native_judge reads for places still
# without a gate and gate_missing for whether anything is. `listed` None: now. Raises OSError as record_gate does.
def record_wanted(home: Path, wanted: str, places: list[str], hosts: dict[str, str | None],
                  listed: float | None = None) -> None:
    path = enabled_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = readable_gates(path)
        before = data.get("wanted") if isinstance(data.get("wanted"), dict) else {}
        since = before.get("places", {}) if before.get("key") == wanted else {}
        now = time.time()
        data["wanted"] = {"key": wanted, "at": now, "listed": now if listed is None else listed, "hosts": hosts,
                          "places": {place: since.get(place, now) for place in places}}
        write_gates(path, data)


# The fidelity gate's compared games that matched so far at `place` under key `gated`, which a gate that ended without
# recording (no room, too few compared) keeps for the next run to go on from (native_fidelity.gate): enabled.json's
# "progress" ({"key", "places": {place: rows}}), one key's at a time and at most native_fidelity.FIDELITY_SAMPLE rows
# a place. [] when none, or it can't be read.
def gate_progress(home: Path, gated: str, place: str) -> list[dict]:
    progress = gates(home).get("progress")
    if not isinstance(progress, dict) or progress.get("key") != gated:
        return []
    places = progress.get("places")
    rows = places.get(place) if isinstance(places, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


# Keeps `rows` as gate_progress's for `gated` at `place` ([] drops them), replacing another key's. Raises OSError as
# record_gate does.
def record_progress(home: Path, gated: str, place: str, rows: list[dict]) -> None:
    path = enabled_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = readable_gates(path)
        progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
        places = progress.get("places") if progress.get("key") == gated else {}
        places = places if isinstance(places, dict) else {}  # unreadable progress starts again
        places = {**places, place: rows} if rows else {k: v for k, v in places.items() if k != place}
        data["progress"] = {"key": gated, "places": places}
        write_gates(path, data)


def write_gates(path: Path, data: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=1) + "\n")
    temporary.replace(path)


# The referee's judge for this run and engine, built once in <loop home>/native-judge; None, said once, when it can't
# be used.
def local_judge(folder: Path) -> Path | None:
    return _local_judge(folder, (engine_wasm() or (None, None))[1])


@functools.cache
def _local_judge(folder: Path, engine: str | None) -> Path | None:
    judge, reason = warm(folder)
    if reason:
        say(f"off for this run ({reason}); games play through unswbc run")
    return judge


def load_fork_server(script: Path):
    spec = importlib.util.spec_from_file_location("fork_server_uploaded", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `play` on a host: the judge's command, or None having said why the host's own command plays instead: among the
# reasons, an engine (`--engine`, its sha256) or toolkit glue (`--toolkit`, toolkit_digest) other than the referee's.
def host_command(args: list[str]) -> list[str] | None:
    options, positional, sources = {}, [], {}
    i = 0
    while i < len(args):
        if args[i] in ("--binary", "--build", "--cache", "--shim", "--fork-script", "--shim-build", "--seed", "-o",
                       "--engine", "--toolkit", "--debug"):
            options[args[i]] = args[i + 1]
            i += 2
        elif args[i] == "--source":
            sources[Path(args[i + 1]).name] = Path(args[i + 1])
            i += 2
        else:
            positional.append(args[i])
            i += 1
    if len(positional) != 3 or "--cache" not in options or "-o" not in options:
        say(f"off (bad arguments {args!r})")
        return None
    if reason := unsupported():
        say(f"off ({reason})")
        return None
    here = ((engine_wasm() or (None, ""))[1], toolkit_digest())
    if (options.get("--engine"), options.get("--toolkit")) != here:  # not what the referee's key was gated for
        say(f"off (this host's engine {here[0][:16]} and toolkit glue {here[1]} aren't the referee's "
            f"{str(options.get('--engine'))[:16]} and {options.get('--toolkit')})")
        return None
    if "--binary" in options:
        judge = Path(options["--binary"]).resolve()
        if not judge.is_file() or not private(judge):
            say(f"off ({judge} is missing or could have been written by someone else)")
            return None
        judge.chmod(0o700)  # an upload keeps no mode
    else:
        if sorted(sources) != sorted(SOURCES):
            say(f"off (sources {sorted(sources)} sent, not {sorted(SOURCES)})")
            return None
        judge, reason = warm(Path(options["--build"]).resolve(), sources)
        if reason:
            say(f"off ({reason})")
            return None
    shim = Path(options["--shim"]).resolve() if "--shim" in options else None
    if "--shim-build" in options:
        found, reason = load_fork_server(Path(options["--fork-script"])).warm(Path(options["--shim-build"]).resolve())
        if reason:
            print(f"fork server: off ({reason}); every bot process starts as usual", file=sys.stderr, flush=True)
        shim = found
    game_map, *bots = positional
    seed = int(options["--seed"]) if "--seed" in options else None
    debug = int(options["--debug"]) if "--debug" in options else None
    return command(judge, Path(game_map), Path(bots[0]), Path(bots[1]), Path(options["-o"]), seed,
                   Path(options["--cache"]).resolve(), shim, debug)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "build":
        judge, reason = warm(Path(argv[2]).resolve())
        if reason:
            say(f"off ({reason})")
            return 1
        print(judge)
        return 0
    if len(argv) > 2 and argv[1] == "play" and "--" in argv:
        split = argv.index("--")
        fallback = argv[split + 1:]
        try:
            judged = host_command(argv[2:split])
        except Exception as error:  # the host's own command plays whatever went wrong here
            say(f"off ({type(error).__name__}: {' '.join(str(error).split())[:300]})")
            judged = None
        run = judged or fallback
        if not run:
            return 2
        sys.stdout.flush()
        with contextlib.suppress(AttributeError):
            sys.stderr.flush()
        os.execv(run[0], run)
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
