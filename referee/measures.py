"""Per-game numbers a targeted idea's test compares (loop/LOOP.md "Targeted ideas"): each named measure is one side's
number for one game, read from the game's replay, such as our deaths in a pocket or how much of its length at the
fold a team kept in its longest dragon (fold_conversion). A side's number is None in a game the measure can't read
(fold_conversion in one that ended before r499), and the driver's sign test leaves that unit out.

A targeted idea names one of MEASURES and a direction. Once its SPRT passes, the driver plays its target batch
(driver.target_batch): the candidate and the champion against the rotation's opponents on fresh maps the measure reads
(`measures.py maps`), keeping the replays until each is read here (`measures.py read`, read_one), one replay an
interpreter, under a timeout (tools/replays.py's capnp schema and the referee's run.py's share one ID, which aborted a
process loading both until tools/replays.py got its own parser, MISSES 97). Nothing here decides anything: the
numbers are a report the driver's sign test reads. They are read only from replays the referee wrote, never from a
worker's files (MISSES 17). Doom-based measures read tools/watch.py's doom kinds through one adapter (death_counts),
so its kinds can change under it.

    measures.py maps NAME MAP...    the maps NAME reads, one a line
    measures.py read NAME REPLAY [SIDE]    {"A": value, "B": value} as JSON; with SIDE, the other side's is None
"""

from __future__ import annotations

import functools
import json
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRECTIONS = ("down", "up")  # which way a targeted idea's measure counts as better
READ_TIMEOUT = 180  # seconds one replay may take to read; a doom parse was 2-28 s a game (E0204's losses, 2026-09-28)
READERS = 4  # replays read at once, each its own interpreter at nice 10 (read_all, driver.target_batch)


Values = dict[str, float | None]  # {"A": value, "B": value}; None where the game gives the measure nothing


@dataclass(frozen=True)
class Measure:
    about: str  # what it counts, a game, for prompts and reports
    read: Callable[[Path, str | None], Values]  # each side's value in the replay at a path; one side's alone, or None
    pockets: bool = False  # read only on maps with a pocket (elsewhere it is 0 on both sides and says nothing)


# A reader of each side's deaths whose kinds `counts` takes: watch.py doom's kinds with `doom`, else the engine's end
# reasons (death_counts).
def deaths(doom: bool, counts: Callable[[str], bool]) -> Callable[[Path, str | None], Values]:
    def read(path: Path, side: str | None = None) -> Values:
        found = death_counts(path, doom, side)
        return {s: None if found[s] is None else float(sum(n for kind, n in found[s].items() if counts(kind)))
                for s in "AB"}
    return read


# M75's measure for a fold or king idea where our king already wins the round-500 race: each side's longest dragon at
# the end of FOLD_LAST over its total length at the end of FOLD_FROM, the share of its mass the fold turned into the
# length that decides the game (online kings kept 0.84-0.88 of theirs r350-r396, method.md M75). None for both sides
# in a game that ended before FOLD_LAST, and for a side with no length at FOLD_FROM.
FOLD_FROM, FOLD_LAST = 400, 499


def fold_conversion(path: Path, side: str | None = None) -> Values:
    replays, _ = _tools()
    snaps = {s.round: s for s in replays.analyse(replays.load_replay(path)).snapshots}
    start, end = snaps.get(FOLD_FROM), snaps.get(FOLD_LAST)
    if start is None or end is None:
        return {"A": None, "B": None}
    return {side: end.longest[side] / start.total[side] if start.total[side] else None for side in "AB"}


# The measures a targeted idea may name. Each is one side's number in one game.
MEASURES = {
    "pocket_deaths": Measure("our deaths with the head in a pocket (a room only portals lead out of; watch.py doom's "
                             "place), split-out parents aside", deaths(True, lambda kind: kind.startswith("pocket ")),
                             True),
    "pocket_teammate_dooms": Measure("our pocket deaths a teammate's body shut in (watch.py doom kind "
                                     "'pocket teammate')", deaths(True, lambda kind: kind == "pocket teammate"), True),
    "undoomed_wall_deaths": Measure("our steps into kelp in the open with room to spare (watch.py doom kind "
                                    "'open never hitWall')", deaths(True, lambda kind: kind == "open never hitWall")),
    "wall_deaths": Measure("our deaths by moving into kelp (the engine's hitWall)",
                           deaths(False, lambda reason: reason == "hitWall")),
    "fold_conversion": Measure(f"our longest dragon at r{FOLD_LAST} over our total length at r{FOLD_FROM}, in games "
                               f"that reach r{FOLD_LAST} (M75: how much of the team the fold turns into the king)",
                               fold_conversion),
}


def _tools():
    for path in (ROOT / "tools", ROOT):
        if str(path) not in sys.path:
            sys.path.append(str(path))
    import replays
    import watch
    return replays, watch


# Each side's deaths in the replay at `path`, {"A": {kind: n}, "B": {kind: n}}: watch.py doom's death kinds with
# `doom`, else the engine's end reasons. Kinds come from the moves alone (no deliberate_deaths): the debug line a bot
# writes would let a candidate relabel its own deaths. With `side`, a doom walk judges that side alone and the other's
# counts are None. The one place this module reads watch.py's deaths.
def death_counts(path: Path, doom: bool, side: str | None = None) -> dict[str, dict[str, int] | None]:
    replays, watch = _tools()
    if doom:
        kinds = watch.death_kinds(watch.watch(path, doom=True, side=side).dooms, "A")
        found = {"A": kinds["ours"], "B": kinds["theirs"]}
        return found if side is None else {s: found[s] if s == side else None for s in "AB"}
    counts: dict[str, dict[str, int]] = {"A": {}, "B": {}}
    for death in replays.analyse(replays.load_replay(path)).deaths:
        side = counts[death["team"]]
        side[death["reason"]] = side.get(death["reason"], 0) + 1
    return counts


# Whether the map at `path` has a pocket (tools/watch.py doom's pockets).
@functools.cache
def has_pockets(path: str) -> bool:
    _, watch = _tools()
    from mapgen.gen import parse
    board = parse(Path(path).read_text())
    return bool(watch.pockets(board, watch.step_table(board)))


# Whether measure `name` reads the map at `path`. Imports tools/: only in a process that never loads referee/run.py.
def applies(name: str, path: Path) -> bool:
    return not MEASURES[name].pockets or has_pockets(str(path))


# {"A": value, "B": value}: measure `name` for each side of the replay at `path`, or with `side` for that side alone
# (the other None), which a doom measure reads in about half the time. Imports tools/, as applies does.
def measure_file(name: str, path: Path, side: str | None = None) -> Values:
    values = MEASURES[name].read(path, side)
    return values if side is None else {s: values[s] if s == side else None for s in "AB"}


# `python` running the checkout `root`'s copy of this file with `args`, at nice 10 (a batch's readers wait on the Mac's
# CPU behind sig's work and ahead of the fillers' games at 19). The driver passes its referee checkout, the frozen
# code that plays the games.
def _command(python: str, root: Path, args: list[str]) -> list[str]:
    return ["nice", "-n", "10", python, str(root / "referee/measures.py"), *args]


# The maps of `maps` that measure `name` reads, in order, from a separate interpreter (applies). Raises when that
# fails: a batch can't pick its maps without it.
def maps_read(name: str, maps: list[str], python: str = sys.executable, root: Path = ROOT) -> list[str]:
    if not MEASURES[name].pockets:
        return list(maps)
    proc = subprocess.run(_command(python, root, ["maps", name, *maps]), cwd=root, capture_output=True, text=True,
                          timeout=READ_TIMEOUT + len(maps))
    if proc.returncode:
        raise RuntimeError(f"measures.py maps {name} failed: {proc.stderr[-500:]}")
    return [line for line in proc.stdout.splitlines() if line]


# Whether the checkout `root`'s copy of this file reads one side (`read NAME REPLAY SIDE`): a referee checkout
# refreshed before it did reads both. Read each call, not cached: a long-running driver's referee is refreshed (or
# rolled back) under it.
def _reads_a_side(root: Path) -> bool:
    try:
        return "read NAME REPLAY [SIDE]" in (root / "referee/measures.py").read_text()
    except OSError:
        return False


# Measure `name` in one replay from a separate interpreter: its {"A", "B"} values (a number or None each), or the
# error text when it failed, timed out (`timeout` s) or printed something else. Never raises. With `side`, only that
# side's value is read (the other is None) where `root`'s copy can, which halves a doom measure's read (E0375's 288
# replays kept the evaluation 32 min past its games and screen, 2026-09-30).
def read_one(name: str, path: Path, python: str = sys.executable, root: Path = ROOT,
             timeout: float = READ_TIMEOUT, side: str | None = None) -> dict | str:
    sided = [side] if side and _reads_a_side(root) else []
    try:
        proc = subprocess.run(_command(python, root, ["read", name, str(path), *sided]), cwd=root,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout:g} s"
    except OSError as error:
        return f"{type(error).__name__}: {error}"[:300]
    try:
        values = json.loads(proc.stdout) if proc.returncode == 0 else None
    except ValueError:
        values = None
    if isinstance(values, dict) and all(side in values and type(values[side]) in (int, float, type(None))
                                        for side in "AB"):
        return {side: None if values[side] is None else float(values[side]) for side in "AB"}
    return (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}, no values"])[-1][:300]


# read_one over `paths`, READERS at a time: a result a path, in order.
def read_all(name: str, paths: list[Path], python: str = sys.executable, root: Path = ROOT,
             timeout: float = READ_TIMEOUT) -> list[dict | str]:
    with ThreadPoolExecutor(READERS) as pool:
        return list(pool.map(lambda path: read_one(name, path, python, root, timeout), paths))


def main(args: list[str]) -> int:
    if len(args) >= 2 and args[0] == "maps" and args[1] in MEASURES:
        for path in args[2:]:
            if applies(args[1], Path(path)):
                print(path)
        return 0
    if len(args) in (3, 4) and args[0] == "read" and args[1] in MEASURES and args[3:] in ([], ["A"], ["B"]):
        print(json.dumps(measure_file(args[1], Path(args[2]), args[3] if len(args) == 4 else None)))
        return 0
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
