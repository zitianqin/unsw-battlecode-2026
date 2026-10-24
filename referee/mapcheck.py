"""Checks maps with the game engine's own loader (the toolkit's unswbc.engine, the wasm every game runs), no bot
involved.

    <toolkit python> referee/mapcheck.py MAP_OR_FOLDER... [--quarantine DIR] [--log FILE] [--note TEXT]

A map the engine refuses to load fails every game on it at load, whichever bots play (E0260 and E0257, 2026-09-28:
`error: .../gen_worldgen_144449864.map: dragon 0 segment 6 is not adjacent to the one before it`), so it is a fault
of the map, never of a bot. Prints `REFUSED <path>: <the engine's reason>` for each refused map and exits 1 when any
was refused, 0 when all load, 2 when the check itself failed (no toolkit, an unreadable map, a refused map it
couldn't move: never read as all good); a folder means its *.map files. With --quarantine each refused map is moved
into DIR (named `<sha256 12>-<name>`, never deleted), and with --log each gets a JSON line in FILE (at, map, moved,
reason, engine, note), which keeps its newest LOG_KEEP lines.

The check stops the engine at its first request for a bot's move: by then it has read and checked the whole map
(a broken dragon listed last is refused before any dragon spawns, measured 2026-09-28), so nothing is played; a map
takes about 0.9 ms. Stdlib and the toolkit only: sprt.py imports it, the driver runs it as a script
(loop/driver.py check_maps)."""

from __future__ import annotations

import argparse
import atexit
import fcntl
import functools
import hashlib
import importlib.metadata
import json
import shutil
import sys
import time
from pathlib import Path

LOG_KEEP = 500


class _Loaded(Exception):
    """Raised from the first bot turn the engine asks for: the map has loaded."""


def _stop(*_args) -> bytes:
    raise _Loaded


# The engine, compiled once a process (about 0.1 s) and dropped at exit while wasmtime can still free it (at
# interpreter shutdown its __del__ raised TypeError on stderr).
@functools.cache
def _module():
    from unswbc import engine  # the toolkit this process runs, the one its games run

    atexit.register(_module.cache_clear)
    return engine.EngineModule()


# The engine's reason for refusing to load map text `data`, or None when it loads. ImportError without a toolkit.
# Each run makes a wasm store that reserves 4 GB of address space, which nothing frees while wasmtime's callbacks
# refer to it (a collection didn't: 26,000 checks reached 109 TB and "mmap failed to reserve", 2026-09-28), so the
# store is closed as the check ends (_close_stores).
def engine_fault(data: bytes) -> str | None:
    module = _module()
    try:
        module.run(data, bot_reply=_stop, on_notice=lambda _line: None)
    except _Loaded as loaded:
        _close_stores(loaded.__traceback__)
        return None
    except RuntimeError as error:  # engine.EngineModule.run's: ubc_run refused the map
        _close_stores(error.__traceback__)
        return str(error) or "the engine refused the map"
    # Played to the end without asking a bot (no dragons), but it loaded; run kept its store for a replay (_live).
    live, module._live = getattr(module, "_live", None), None
    _closed(_close(live[0] if live else None))
    return None


# Closes the wasm store in each frame of traceback `tb` (EngineModule.run's local `store`), freeing its memory.
def _close_stores(tb) -> None:
    closed = 0
    while tb is not None:
        closed += _close(tb.tb_frame.f_locals.get("store"))
        tb = tb.tb_next
    _closed(closed)


def _close(store) -> int:
    if callable(getattr(store, "close", None)):
        store.close()
        return 1
    return 0


# Warns once a process when a check found no store to close: a toolkit that names it otherwise, whose stores would
# then pile up until address space ran out (engine_fault).
def _closed(count: int) -> None:
    if not count and not _WARNED:
        _WARNED.append(True)
        print("mapcheck: WARNING no wasm store found to close after a check (a changed toolkit?); each one left open "
              "reserves 4 GB of address space", file=sys.stderr)


_WARNED: list[bool] = []


# engine_fault of the map file at `path`, remembered per path and content for this process (sprt.py asks once a
# trial).
def refused(path: Path) -> str | None:
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest not in _SEEN:
        _SEEN[digest] = engine_fault(data)
    return _SEEN[digest]


_SEEN: dict[str, str | None] = {}


def map_files(targets: list[Path]) -> list[Path]:
    return [m for t in targets for m in (sorted(t.glob("*.map")) if t.is_dir() else [t])]


# Moves refused map `path` into `quarantine` as `<sha256 12>-<name>` and returns where it went (the same content
# arriving again, e.g. a regenerated seed, lands on the same name).
def quarantine_map(path: Path, quarantine: Path) -> Path:
    quarantine.mkdir(parents=True, exist_ok=True)
    target = quarantine / f"{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}-{path.name}"
    shutil.move(str(path), str(target))
    return target


# Appends `rows` to the JSON-lines log at `path` under its lock, keeping its newest LOG_KEEP lines.
def log_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = path.read_text().splitlines() if path.exists() else []
        lines = [*old, *(json.dumps(row) for row in rows)][-LOG_KEEP:]
        path.write_text("".join(f"{line}\n" for line in lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", type=Path, help="map files, or folders of them")
    parser.add_argument("--quarantine", type=Path, help="move each refused map into this folder")
    parser.add_argument("--log", type=Path, help="append a JSON line for each refused map here")
    parser.add_argument("--note", default="", help="what made the maps, kept in each log line")
    args = parser.parse_args(argv)
    try:
        version = importlib.metadata.version("unswbc")
        found = [(m, reason) for m in map_files(args.targets) if (reason := refused(m))]
    except Exception as error:  # no toolkit, an unreadable map, a broken engine: the check failed
        print(f"mapcheck: the check failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    rows, unmoved = [], False
    for path, reason in found:
        try:
            moved = quarantine_map(path, args.quarantine) if args.quarantine else None
        except OSError as error:
            print(f"mapcheck: refused {path} wasn't moved to {args.quarantine}: {error}", file=sys.stderr)
            moved, unmoved = None, True
        print(f"REFUSED {path}: {reason}" + (f" (moved to {moved})" if moved else ""))
        rows.append({"at": time.time(), "map": str(path), "moved": str(moved) if moved else None, "reason": reason,
                     "engine": version, "note": args.note})
    if args.log and rows:
        log_rows(args.log, rows)
    return 2 if unmoved else 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
