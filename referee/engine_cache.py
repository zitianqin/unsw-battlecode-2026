"""Runs `unswbc run` (or a script such as record.py) with the game engine loaded precompiled instead of compiled.

    <toolkit python> engine_cache.py CACHE run MAP BOT_A BOT_B -o REPLAY [--seed N] [--sandbox]
    <toolkit python> engine_cache.py CACHE SCRIPT.py ARGS...

The toolkit compiles its 4.7 MB wasm engine from scratch in every game (about 0.4 s, 4.1 G instructions, some 15%
of a game's engine CPU). This patches unswbc.engine.EngineModule to load a compiled copy from the folder CACHE
instead, compiling it and saving it there (a temporary file, then a rename) when there is none, then runs the
command line after CACHE exactly as `unswbc` (or `python SCRIPT.py`) would. The game is the same either way: the
engine is the same code, compiled by the same wasmtime; only when it is compiled changes (E0085, 2026-09-26:
byte-identical games, ~18% less CPU a native game on this Mac). fork_server.py runs this file as its SCRIPT when a
game both loads the engine precompiled and forks its bots.

The patch replaces EngineModule.__init__, so it only applies to an engine.py whose digest is in KNOWN_ENGINES,
read by hand; on any other one, or when the folder is unusable, it prints why to stderr and runs unpatched. A
compiled copy that won't load (corrupt, another wasmtime) is reported, recompiled and replaced.

wasmtime trusts a compiled file completely (it is machine code), so CACHE must be a folder only the loop writes:
<loop home>/engine-cache here, the host's own .bc-remote/engine-cache on a remote host, never a path a bot or a
coder chooses. Files that others could have written (not ours, or group/world-writable) are never loaded, nor
ones whose content no longer matches the checksum saved with it.
Stdlib and the toolkit only: remote hosts run this file alone."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import os
import platform
import runpy
import sys
from pathlib import Path

# engine.py files (sha256, first 16 hex digits) whose EngineModule.__init__ is the one `patched_init` replaces:
# unswbc 1.1.0's. A new toolkit's engine.py needs reading (does __init__ still only make a default Engine, compile
# WASM_PATH and clear _live?) before its digest is added.
KNOWN_ENGINES = {"2d5b69ffd84a8eb1"}


def say(text: str) -> None:
    print(f"engine cache: {' '.join(text.split())}", file=sys.stderr, flush=True)  # one line: wasmtime's span several


# The toolkit's engine.py digest, or None without a toolkit.
def engine_digest() -> str | None:
    try:
        import unswbc.engine as engine
    except ImportError:
        return None
    return hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()[:16]


# None when this toolkit's engine can be patched, else why not.
def unsupported() -> str | None:
    found = engine_digest()
    if found is None:
        return "no unswbc toolkit"
    if found not in KNOWN_ENGINES:
        return f"unswbc's engine.py changed (digest {found}); re-read it and add it to KNOWN_ENGINES"
    import unswbc.engine as engine
    if engine.WASM_RUNTIME_ERROR is not None:
        return f"no wasm runtime: {engine.WASM_RUNTIME_ERROR}"
    return None


# Where the compiled copy of `wasm` lives in `folder`: named by the wasm's content, the wasmtime version and the
# platform, since a compiled copy only loads in the wasmtime and on the kind of machine that made it.
def cache_path(folder: Path, wasm: bytes) -> Path:
    return folder / (f"{hashlib.sha256(wasm).hexdigest()}-{importlib.metadata.version('wasmtime')}-"
                     f"{sys.platform}-{platform.machine()}.cwasm")


# True when only this user could have written `path` (on systems with owners: not Windows, where this isn't used).
def private(path: Path) -> bool:
    if os.name == "nt":
        return True
    status = path.stat()
    return status.st_uid == os.getuid() and not status.st_mode & 0o022


# A saved copy's first line: this and the sha256 of the rest, so a copy damaged on disk (a power cut or Spot
# eviction can leave zeros under the final name, which pass wasmtime's own header check) is recompiled, not run.
HEADER = b"bc-engine-cache sha256 "


# The compiled bytes saved in `cached`, checked: ours alone could have written it, and they are the bytes saved.
# Raises (PermissionError, ValueError, OSError) otherwise.
def read_copy(cached: Path) -> bytes:
    if not private(cached) or not private(cached.parent):
        raise PermissionError(f"{cached} could have been written by someone else")
    first, _, blob = cached.read_bytes().partition(b"\n")
    if not first.startswith(HEADER) or first[len(HEADER):].decode(errors="replace") != hashlib.sha256(blob).hexdigest():
        raise ValueError("damaged: its content doesn't match its checksum")
    return blob


# Saves compiled bytes as `cached`: owner-only, flushed to disk, then renamed into place whole.
def save_copy(cached: Path, blob: bytes) -> None:
    temporary = cached.with_name(f"{cached.name}.{os.getpid()}.tmp")
    try:
        cached.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Owner-only whatever the umask (Linux users often have 002, which would leave it group-writable, unloadable).
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
            handle.write(HEADER + hashlib.sha256(blob).hexdigest().encode() + b"\n" + blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, cached)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


# The engine's compiled module for `engine` (a wasmtime Engine): loaded from `folder`, or compiled from the wasm at
# `wasm_path` and saved there for the next game. Never fails for the cache's sake: a copy that won't load or can't
# be saved is reported and the module compiled as the toolkit would.
def load_module(engine, wasm_path: Path, folder: Path):
    from wasmtime import Module

    blob = Path(wasm_path).read_bytes()
    cached = cache_path(folder, blob)
    if cached.is_file():
        try:
            return Module.deserialize(engine, read_copy(cached))
        except Exception as error:  # wasmtime raises its own error types
            say(f"cannot load {cached.name} ({error}); compiling the engine and replacing it")
    module = Module(engine, blob)
    try:
        save_copy(cached, module.serialize())
    except Exception as error:  # OSError, or wasmtime's own: the game goes on with the module compiled here
        say(f"cannot save {cached}: {error}")
    return module


# Makes every EngineModule load its module through `folder`. Returns False, having said why, when this toolkit's
# engine isn't one the patch was written for; it is then left alone.
def install(folder: Path) -> bool:
    if reason := unsupported():
        say(f"off ({reason}); compiling the engine as usual")
        return False
    import unswbc.engine as engine
    from unswbc.errors import UserError

    # engine.py's EngineModule.__init__, with Module.from_file replaced by load_module.
    def patched_init(self, wasm_path: Path = engine.WASM_PATH) -> None:
        if not Path(wasm_path).is_file():
            raise UserError(f"this build ships no engine, so no match can run ({wasm_path})")
        self._engine = engine.Engine()
        self._module = load_module(self._engine, Path(wasm_path), folder)
        self._live = None

    engine.EngineModule.__init__ = patched_init
    return True


# Referee side, once per run: makes sure the compiled copy in `folder` exists and loads here, so games don't all
# compile it at once. None when games can use `folder`, else why not (an unknown toolkit, a folder this process
# can't write or read, e.g. inside a coder's sandbox, where the loop home is out of reach).
def warm(folder: Path) -> str | None:
    if reason := unsupported():
        return reason
    import unswbc.engine as engine
    from wasmtime import Engine, Module

    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        cached = cache_path(folder, engine.WASM_PATH.read_bytes())
        probe = folder / f".probe.{os.getpid()}"  # a folder this process can't write or read: say so, don't compile
        probe.write_bytes(b"")
        probe.unlink()
        if cached.is_file():
            cached.open("rb").close()
        load_module(Engine(), engine.WASM_PATH, folder)  # compiles and saves it when missing or broken
        if not cached.is_file():
            return f"cannot save {cached}"
        Module.deserialize(Engine(), read_copy(cached))
    except Exception as error:  # OSError, or wasmtime's own on a copy that won't load
        return " ".join(f"{type(error).__name__}: {error}".split())
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: engine_cache.py CACHE (run ARGS... | SCRIPT.py ARGS...)", file=sys.stderr)
        return 2
    folder, command = Path(argv[1]).resolve(), argv[2:]
    install(folder)
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
