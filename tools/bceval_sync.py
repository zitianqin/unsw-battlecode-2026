"""Keeps bceval (xCirno1's MIT win-probability model, https://github.com/xCirno1/battlecode-eval; sig approved using it
on 2026-09-28) in step with its GitHub repo without ever running code nobody approved.

The clone is CLONE (~/Dev/bc-external/battlecode-eval-git), read by tools/bceval_read.py in its own venv (VENV_PY).
`sync` runs before each use and fetches at most every FETCH_EVERY; after a fetch it compares the approved commit with
the remote branch:
- the same: nothing to do;
- only WEIGHTS changed (bceval/model.json, data read with json.load), as a plain file both sides (`git diff --raw`:
  status M, mode 100644), and its new blob, under MODEL_MAX bytes, parses to a dict with MODEL_KEYS of the shapes
  score.py reads and only finite numbers (model_problem): the clone is fast-forwarded and the new commit approved,
  with a `BCEVAL weights updated` alert (a symlink, deletion, rename or mode change there is held, as code is);
- anything else (code, schema, or a history rewrite): the clone stays at the approved commit and the change is held,
  alerted once per new remote commit as `BCEVAL UPDATE HELD: <files>, review with <review_command>`; health.py
  alerts again each day it stays held (health.bceval). The orchestrator reads the diff and approves it with
  `tools/bceval_sync.py --approve <sha>`, which checks the clone out at it (detached, so a rewritten history works too).
A failed fetch keeps the approved commit (health.bceval alerts after a day of failures). `ready` refuses a clone whose
HEAD isn't the approved commit or whose tree has changes, so only approved code runs. git runs with hooks, fsmonitor
and the pager off (MISSES 17), external diff and textconv too where it diffs. git fetch is the only network call.

What runs code without being in the tree is fingerprinted (fingerprint: the clone's .git/config and .git/info/, the
venv's pyvenv.cfg and its site-packages listing with sizes and each .pth and top-level .py file's text): recorded at
the first sync, it must match before any git call in the clone or any bceval run, else nothing runs and it is
alerted once (health.bceval alerts it daily). No headless session may write home.EXTERNAL_CODE (driver.lane_settings,
meta.worker_settings), so a mismatch is an intrusion or sig's own change: inspect those files, then `--trust`.

State: <loop home>/bceval.json, fixed keys (the approved commit and when, the last fetch and its error, the held
commit with at most MAX_FILES of its files, the last weights update, the trusted fingerprint and its mismatch, the
pearl-fill ties logged, bceval_read.log_tie: one short map name each): under 4 KB.

    .venv/bin/python tools/bceval_sync.py              # sync now (within FETCH_EVERY: the recorded state) and print it
    .venv/bin/python tools/bceval_sync.py --approve SHA  # after reading review_command's diff
    .venv/bin/python tools/bceval_sync.py --trust      # after inspecting the fingerprinted files
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

from home import EXTERNAL_CODE, loop_home  # noqa: E402

CLONE = EXTERNAL_CODE / "battlecode-eval-git"
VENV = EXTERNAL_CODE / ".venv"
VENV_PY = VENV / "bin/python"
BRANCH = "master"
PINNED = "9c70c719e4d53433e8030e51209cf4985545591b"  # the commit the 2026-09-28 22:45 study read and sig approved
WEIGHTS = "bceval/model.json"
MODEL_KEYS = ("knots", "features", "weights", "lin_scale")
MODEL_MAX = 1_000_000  # bytes; the approved model.json is 4 KB
FETCH_EVERY = 600
MAX_FILES = 20
STATE = "bceval.json"
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat")
NO_PROGRAMS = ("--no-ext-diff", "--no-textconv")  # every diff: neither runs a program the config names
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
SHA = re.compile(r"^[0-9a-f]{40}$")


def state_path(home: Path | None = None) -> Path:
    return (home or loop_home()) / STATE


def read_state(home: Path | None = None) -> dict:
    try:
        value = json.loads(state_path(home).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_state(home: Path, value: dict) -> None:
    temporary = state_path(home).with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=1) + "\n")
    temporary.replace(state_path(home))


@contextlib.contextmanager
def locked(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "bceval.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def run_git(clone: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_SAFE, *args], cwd=clone, capture_output=True, timeout=timeout,
                          env={**os.environ, **GIT_ENV})


def git(clone: Path, *args: str, timeout: float = 60) -> str:
    result = run_git(clone, *args, timeout=timeout)
    if result.returncode != 0:
        output = (result.stderr or result.stdout).decode(errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)}: {output[-300:]}")
    return result.stdout.decode(errors="replace").strip()


def is_ancestor(clone: Path, old: str, new: str) -> bool:
    return run_git(clone, "merge-base", "--is-ancestor", old, new).returncode == 0


# The command the orchestrator reviews a held update with: the same safe settings, no external diff or textconv.
def review_command(clone: Path, old: str, new: str) -> str:
    return f"git -C {clone} {' '.join(GIT_SAFE)} diff {' '.join(NO_PROGRAMS)} {old[:12]}..{new[:12]}"


# Why commit `new` isn't a weights-only update of `old` (check), or "" when it is: every `git diff --raw` row is
# WEIGHTS modified in place as a plain file (M, 100644 both sides; a symlink is 120000) and its new blob passes
# model_problem. No row (an empty commit) is safe too.
def weights_problem(clone: Path, old: str, new: str) -> str:
    rows = git(clone, "diff", *NO_PROGRAMS, "--raw", "--no-renames", "--no-abbrev", old, new).splitlines()
    for row in rows:
        fields, _, path = row.partition("\t")
        parts = fields.lstrip(":").split()
        if path != WEIGHTS or len(parts) != 5 or parts[4] != "M" or parts[:2] != ["100644", "100644"]:
            return f"not a plain in-place change to {WEIGHTS}: {row[:120]}"
    return model_problem(clone, new) if rows else ""


# Why WEIGHTS at commit `new` isn't a model score.py can read, or "": at most MODEL_MAX bytes, a JSON dict with
# MODEL_KEYS (knots a list of numbers, features a dict of kinds, weights a dict of number lists, lin_scale a dict of
# numbers) and every number in it finite.
def model_problem(clone: Path, new: str) -> str:
    size = git(clone, "cat-file", "-s", f"{new}:{WEIGHTS}")
    if not size.isdigit() or int(size) > MODEL_MAX:
        return f"{WEIGHTS} is {size} bytes, over MODEL_MAX {MODEL_MAX}"
    blob = run_git(clone, "cat-file", "blob", f"{new}:{WEIGHTS}")
    try:
        value = json.loads(blob.stdout[:MODEL_MAX + 1].decode())
    except ValueError as error:
        return f"{WEIGHTS} isn't JSON: {error}"[:200]
    numbers = lambda xs: all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in xs)  # noqa: E731
    if not (isinstance(value, dict) and all(k in value for k in MODEL_KEYS)
            and isinstance(value["knots"], list) and value["knots"] and numbers(value["knots"])
            and isinstance(value["features"], dict) and all(isinstance(v, str) for v in value["features"].values())
            and isinstance(value["weights"], dict)
            and all(isinstance(v, list) and numbers(v) for v in value["weights"].values())
            and isinstance(value["lin_scale"], dict) and numbers(value["lin_scale"].values())):
        return f"{WEIGHTS} isn't a dict with {', '.join(MODEL_KEYS)} of the shapes bceval/score.py reads"
    if not all(math.isfinite(x) for x in leaves(value)):
        return f"{WEIGHTS} holds a number that isn't finite"
    return ""


def leaves(value) -> list[float]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in leaves(v)]
    if isinstance(value, list):
        return [x for v in value for x in leaves(v)]
    return [value] if isinstance(value, (int, float)) and not isinstance(value, bool) else []


# A hash of what can run code in a bceval run or a git call in the clone without being in its tree (module docstring):
# the clone's .git/config and every file under .git/info/, the venv's pyvenv.cfg, and every entry under its
# site-packages by path, type and size, with the text of each .pth file and each top-level .py file. Links are hashed
# by their target, never followed.
def fingerprint(clone: Path = CLONE, venv: Path = VENV) -> str:
    digest = hashlib.sha256()

    def add(path: Path, base: Path, text: bool) -> None:
        digest.update(str(path.relative_to(base)).encode() + b"\0")
        try:
            info = path.lstat()
        except OSError:
            digest.update(b"missing\0")
            return
        if path.is_symlink():
            digest.update(b"link " + os.readlink(path).encode() + b"\0")
        elif path.is_file():
            digest.update(f"file {info.st_size}\0".encode())
            if text:
                digest.update(path.read_bytes() + b"\0")
        else:
            digest.update(b"other\0")

    def walk(folder: Path, base: Path, text) -> None:
        for root, folders, files in os.walk(folder):
            folders.sort()
            for name in sorted(files) + [f for f in folders if (Path(root) / f).is_symlink()]:
                path = Path(root) / name
                add(path, base, text(path))

    add(clone / ".git/config", clone, True)
    walk(clone / ".git/info", clone, lambda p: True)
    add(venv / "pyvenv.cfg", venv, True)
    for site in sorted(venv.glob("lib/python*/site-packages")):
        walk(site, venv, lambda p, site=site: p.suffix == ".pth" or p.suffix == ".py" and p.parent == site)
    return digest.hexdigest()


# Why the clone or venv doesn't match the trusted fingerprint, or "": records the fingerprint when none is (the first
# sync), and alerts a mismatch once per fingerprint seen. Updates `record` in place.
def tampered(home: Path, clone: Path, record: dict) -> str:
    now_print = fingerprint(clone)
    if not record.get("trusted"):
        record["trusted"] = now_print
        return ""
    if now_print == record["trusted"]:
        record["mismatch"] = None
        return ""
    why = (f"{clone}'s .git/config or .git/info/, or {VENV}'s site-packages or pyvenv.cfg, changed since they were "
           "trusted: no git call or bceval run until inspected and trusted with tools/bceval_sync.py --trust")
    if record.get("mismatch") != now_print:
        record["mismatch"] = now_print
        alert(home, f"BCEVAL TAMPERED: {why}")
    return why


def alert(home: Path, text: str) -> None:
    with (home / "host-alerts.log").open("a") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


# The approved commit (the recorded one, else PINNED) after fetching when FETCH_EVERY has passed since the last try,
# fast-forwarding a weights-only change and holding any other (the module docstring). Never raises for a fetch or
# git failure: the approved commit stays and the error is recorded.
def sync(home: Path | None = None, clone: Path = CLONE, now: float | None = None) -> str:
    home = home or loop_home()
    now = time.time() if now is None else now
    with locked(home):
        record = read_state(home)
        record.setdefault("approved", PINNED)
        if now - record.get("fetched_at", 0) < FETCH_EVERY:
            return record["approved"]
        record["fetched_at"] = now
        if tampered(home, clone, record):  # before any git call: .git/config can name programs git runs
            save_state(home, record)
            return record["approved"]
        try:
            git(clone, "fetch", "--quiet", "origin", BRANCH, timeout=120)
            record.update(fetch_error=None, fetch_failing_since=None)
            check(home, clone, record, git(clone, "rev-parse", f"refs/remotes/origin/{BRANCH}"), now)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            record["fetch_error"] = f"{type(error).__name__}: {error}"[:300]
            record["fetch_failing_since"] = record.get("fetch_failing_since") or now
        save_state(home, record)
        return record["approved"]


# Acts on the fetched remote commit `new` (sync's second half): updates `record` in place.
def check(home: Path, clone: Path, record: dict, new: str, now: float) -> None:
    old = record["approved"]
    if new == old:
        record["held"] = None
        return
    ahead = is_ancestor(clone, old, new)
    files = git(clone, "diff", *NO_PROGRAMS, "--name-only", "--no-renames", old, new).splitlines()
    problem = weights_problem(clone, old, new) if ahead and set(files) <= {WEIGHTS} else "code"
    if not problem:
        git(clone, "merge", "--ff-only", "--quiet", new)
        record.update(approved=new, approved_at=now, held=None,
                      last_update={"from": old, "to": new, "at": now, "files": files})
        alert(home, f"BCEVAL weights updated: {old[:7]}..{new[:7]} changed only {', '.join(files)}; fast-forwarded "
                    f"{clone} and in use (tools/bceval_sync.py)")
        return
    held = record.get("held") or {}
    if held.get("sha") == new:
        return
    shown = files[:MAX_FILES] + ([f"... {len(files) - MAX_FILES} more"] if len(files) > MAX_FILES else [])
    record["held"] = {"sha": new, "files": shown, "since": held.get("since") or now, "fast_forward": ahead}
    alert(home, f"BCEVAL UPDATE HELD: {', '.join(shown) or 'no file changed'}"
                f"{'' if ahead else ' (not a fast-forward: history rewritten)'}"
                f"{f' ({problem})' if problem != 'code' else ''}, review with {review_command(clone, old, new)}, "
                f"then approve with .venv/bin/python tools/bceval_sync.py --approve {new[:12]}; bceval stays at "
                f"{old[:7]} meanwhile")


# Approves commit `sha` (any unique prefix) after the orchestrator read its diff: checks the clone out at it, detached
# (so a rewritten history approves as well as a fast-forward), and records it; the held update is cleared once the
# approved commit is it or contains it. Refuses while the fingerprint doesn't match. Returns a line to print.
def approve(sha: str, home: Path | None = None, clone: Path = CLONE, now: float | None = None) -> str:
    home = home or loop_home()
    now = time.time() if now is None else now
    with locked(home):
        record = read_state(home)
        record.setdefault("approved", PINNED)
        if why := tampered(home, clone, record):
            save_state(home, record)
            raise SystemExit(f"not approved: {why}")
        full = git(clone, "rev-parse", "--verify", f"{sha}^{{commit}}")
        git(clone, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", full)
        old, record["approved"], record["approved_at"] = record["approved"], full, now
        held = record.get("held") or {}
        if held.get("sha") and is_ancestor(clone, held["sha"], full):
            record["held"] = None
        save_state(home, record)
        return f"bceval approved {full[:12]} (was {old[:12]}); held update: {record['held'] or 'none'}"


# Records the clone's and venv's current fingerprint as trusted, after the orchestrator inspected the files it covers.
def trust(home: Path | None = None, clone: Path = CLONE) -> str:
    home = home or loop_home()
    with locked(home):
        record = read_state(home)
        record.update(trusted=fingerprint(clone), mismatch=None)
        save_state(home, record)
        return f"bceval fingerprint trusted: {record['trusted'][:12]}"


# (clone, approved commit) when bceval may run, else (None, why): the fingerprint must match, and after sync the
# clone's HEAD must be the approved commit and its tree unchanged (untracked files too), so no unreviewed code runs.
def ready(home: Path | None = None, clone: Path = CLONE) -> tuple[Path | None, str]:
    home = home or loop_home()
    approved = sync(home, clone)
    with locked(home):
        record = read_state(home)
        before = dict(record)
        why = tampered(home, clone, record)
        if record != before:
            save_state(home, record)
    if why:
        return None, why
    try:
        head = git(clone, "rev-parse", "HEAD")
        dirty = git(clone, "status", "--porcelain")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return None, f"the clone can't be read ({type(error).__name__}: {str(error)[:200]})"
    if head != approved:
        return None, f"{clone} is at {head[:7]}, not the approved {approved[:7]}"
    if dirty:
        return None, f"{clone} has changes git status lists: {dirty.splitlines()[0][:100]}"
    if not VENV_PY.exists():
        return None, f"no venv python at {VENV_PY}"
    return clone, approved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approve", metavar="SHA")
    parser.add_argument("--trust", action="store_true")
    args = parser.parse_args()
    if args.approve:
        print(approve(args.approve))
        return
    if args.trust:
        print(trust())
        return
    print(f"approved {sync()[:12]}; state {state_path()}:")
    print(json.dumps(read_state(), indent=1))


if __name__ == "__main__":
    main()
