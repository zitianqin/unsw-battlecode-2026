"""Packs each finished experiment clone's replay folders (`data/`, `replays*/` and `.postmortem/` in <clones>/E####,
written by its coder's games and its post-mortem turn) into one `replays.tar.zst` in that clone, losslessly: cleanup
never loses replays, results or partial builds (sig, 2026-09-29). On 2026-09-29 they grew about 12 GB an hour and held
385 GiB of the clones folder with 42 GiB of disk left; zstd -3 packs them about 3.8x. To read one again, in the clone:

    tar --zstd -xf replays.tar.zst

Nothing in the loop reads a clone's `data/` or `replays*/`, and its `.postmortem/` is read only by its own post-mortem
turn. A clone is finished (`finished`) when the latest result recorded on it (its own id's, or a re-evaluation's that
reused it, `driver.clone_dir`) is not an error (an error's clone may be re-evaluated), none of those results owes a
coder turn it hasn't saved (`prune.owed_turn`, `prune.turn_saved`), no live process has its working directory in it,
and none of its files changed in QUIET_S. An archive is verified (zstd -t, and its tar listing has one entry per path)
before the folders go; a later pack of the same clone gets the next free name (`replays-2.tar.zst`).

The bound: the upkeep packs every EVERY_S (`tick`, for up to BUDGET_S), so a clone's replays stay unpacked only while
it is in flight or owed a turn, for QUIET_S after its last write, and until the next tick with budget: at 12 GB an
hour, about 40 GB outside the in-flight clones. `health.clone_replays` alerts when finished clones still hold more than
LIMIT unpacked, or a pack failed. Each tick rewrites STATUS (a few KB: the time, what it packed, what is left and why).

    .venv/bin/python loop/pack.py          # what it would pack
    .venv/bin/python loop/pack.py --apply
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import prune

QUIET_S = 3 * 3600  # a file written this recently: its clone may still be in use (a rerun turn, sig reading it)
EVERY_S = 30 * 60  # between upkeep packs: each reads results.jsonl (270 MB on 2026-09-29)
BUDGET_S = 5 * 60  # a tick starts no clone past this (it holds the upkeep tick); the rest wait for the next
LIMIT = 20 * 2**30  # unpacked bytes left in finished clones past which health alerts
STATUS = "pack.json"
CLONE = re.compile(r"E\d{4}")


# A clone's folders to pack: data/, .postmortem/ and replays*/ (replays/, replays_v1/, ...), never a link.
def targets(clone: Path) -> list[Path]:
    return sorted(p for p in clone.iterdir() if (p.name in ("data", ".postmortem") or p.name.startswith("replays"))
                  and p.is_dir() and not p.is_symlink())


# The clones folder's clone names a live process has its working directory in (lsof).
def live_clones(clones: Path) -> set[str]:
    out = subprocess.run(["lsof", "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    return {m.group(1) for m in re.finditer(rf"^n{re.escape(str(clones))}/([^/\n]+)", out, re.M)}


# Whether a regular file under `folder` was modified at or after `since`.
def changed_since(folder: Path, since: float) -> bool:
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                if os.lstat(os.path.join(root, name)).st_mtime >= since:
                    return True
            except OSError:
                continue
    return False


# Each clone with folders to pack, and why it isn't finished ("" when it is), from results.jsonl's rows (file order).
def finished(state: Path, clones: Path, rows: list[dict], now: float) -> dict[Path, str]:
    last = {row["id"]: k for k, row in enumerate(rows)}  # an id's latest row only: a rejudge's (meta.latest_rows)
    on: dict[str, list[dict]] = {}  # clone name -> its results, in the order their latest rows were recorded
    for row in (row for k, row in enumerate(rows) if last[row["id"]] == k):
        on.setdefault((row.get("session") or {}).get("clone") or row["id"], []).append(row)
    live, found = live_clones(clones), {}
    for clone in sorted(p for p in clones.iterdir() if CLONE.fullmatch(p.name) and p.is_dir()):
        if not targets(clone):
            continue
        results = on.get(clone.name, [])
        owed = [r["id"] for r in results if (turn := prune.owed_turn(r))
                and not prune.turn_saved(state / "runs" / r["id"] / f"{turn}.json")]
        found[clone] = ("no result recorded (in flight)" if not results else
                        f"its latest result {results[-1]['id']} is an error" if results[-1].get("verdict") == "error"
                        else f"owed a coder turn: {', '.join(owed)}" if owed else
                        "a live process works in it" if clone.name in live else
                        f"a file changed in the last {QUIET_S // 3600} h" if changed_since(clone, now - QUIET_S)
                        else "")
    return found


# Packs `folders` of `clone` into its next free replays*.tar.zst, verified, then removes them. Returns (archive,
# bytes before, bytes after); raises with why on a failure, leaving the folders as they were.
def pack(clone: Path, folders: list[Path]) -> tuple[Path, int, int]:
    names = [f.name for f in folders]
    archive = next(clone / name for k in range(1, 1000)
                   if not (clone / (name := "replays.tar.zst" if k == 1 else f"replays-{k}.tar.zst")).exists())
    temporary = archive.with_name(archive.name + ".tmp")
    paths = len(folders) + sum(len(dirs) + len(files) for folder in folders for _, dirs, files in os.walk(folder))
    before = sum(prune.folder_size(f) for f in folders)
    try:
        tar = subprocess.Popen(["tar", "-cf", "-", *names], cwd=clone, stdout=subprocess.PIPE)
        zstd = subprocess.run(["nice", "-n", "19", "zstd", "-3", "-T2", "-q", "-o", str(temporary)], stdin=tar.stdout,
                              capture_output=True, text=True)
        tar.stdout.close()
        if tar.wait() or zstd.returncode:
            raise RuntimeError(f"tar exited {tar.returncode}, zstd {zstd.returncode}: {zstd.stderr.strip()[:200]}")
        if subprocess.run(["nice", "-n", "19", "zstd", "-t", "-q", str(temporary)]).returncode:
            raise RuntimeError("zstd -t failed")
        unzip = subprocess.Popen(["zstd", "-dc", "-q", str(temporary)], stdout=subprocess.PIPE)
        listed = subprocess.run(["tar", "-tf", "-"], stdin=unzip.stdout, capture_output=True).stdout.count(b"\n")
        # tar stops at the end-of-archive marker; drain the padding after it, or zstd dies of SIGPIPE on close
        while unzip.stdout.read(1 << 16):
            pass
        unzip.stdout.close()
        if unzip.wait() or listed != paths:
            raise RuntimeError(f"the archive lists {listed} entries for {paths} paths, zstd -dc exited {unzip.returncode}")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.rename(archive)
    for folder in folders:
        subprocess.run(["rm", "-rf", str(folder)], check=True)
    return archive, before, archive.stat().st_size


# The upkeep's step (maintenance.tick): at most every EVERY_S, packs finished clones for up to BUDGET_S and rewrites
# STATUS with what it packed, what it couldn't (failed) and the finished clones' bytes still unpacked. Returns a log
# line, or "" when it didn't run or packed nothing.
def tick(repo: Path, state: Path, now: float | None = None) -> str:
    from home import clones_folder
    now = time.time() if now is None else now
    status = state / STATUS
    try:
        if now - json.loads(status.read_text())["at"] < EVERY_S:
            return ""
    except (OSError, ValueError, KeyError, TypeError):
        pass
    path = state / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    ready = [clone for clone, why in finished(state, clones_folder(repo), rows, now).items() if not why]
    packed, failed, left, freed = [], {}, {}, 0
    start = time.monotonic()
    for clone in ready:
        folders = targets(clone)
        if time.monotonic() - start < BUDGET_S:
            try:
                _, before, after = pack(clone, folders)
                packed.append(clone.name)
                freed += before - after
                continue
            except Exception as error:
                failed[clone.name] = f"{type(error).__name__}: {error}"[:300]
        left[clone.name] = sum(prune.folder_size(f) for f in folders)
    temporary = status.with_name(STATUS + ".tmp")
    temporary.write_text(json.dumps({"at": now, "packed": packed, "failed": failed, "unpacked": left}) + "\n")
    temporary.replace(status)
    return f"packed the replay folders of {len(packed)} finished clones ({freed / 2**30:.1f} GiB freed): " \
           f"{', '.join(packed)}" if packed else ""


if __name__ == "__main__":
    from home import clones_folder, loop_home
    home, repo = loop_home(), Path(__file__).resolve().parent.parent
    if "--apply" in sys.argv:
        (home / STATUS).unlink(missing_ok=True)
        print(tick(repo, home) or "nothing to pack")
    else:
        results = home / "results.jsonl"
        rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
        for clone, why in finished(home, clones_folder(repo), rows, time.time()).items():
            print(f"{clone.name}: {why or 'ready'}")
