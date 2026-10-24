"""Keeps CLAUDE.md and loop/LOOP.md from creeping back past a size cap (CLAUDE.md was 102 KB on 2026-09-27, read in
full on every orchestrator turn), and keeps the docs' pointers into loop/LOOP.md ("LOOP.md: Dev battles, Pearl
seeds") naming headings it has. CLAUDE.md holds the rules and a one-line pointer per mechanism; how a mechanism
works and its numbers go in loop/LOOP.md or the code's docstrings, its incidents in loop/MISSES.md. LIMITS is each file's cap in bytes.

    .venv/bin/python tools/doc_size.py [--root DIR] [--staged | --rev REV] [--json]

--staged (tools/hooks/pre-commit) sizes the index's copy of each capped file the commit stages (in a merge, each one
it changes against HEAD), and when it stages a .md file checks every pointer in the index's tracked .md files
against the index's loop/LOOP.md; it exits 1 when a file is over its cap or a pointer names no heading, saying
what to do. --rev REV checks REV's committed copies (health.py's DOC SIZE alert reads main); with neither, the working
tree's. --json prints [{path, bytes, limit, advice}] for the files over and [{path, pointer, advice}] for each broken
pointer. There is no bypass: move the detail out, or fix the pointer, as the message says.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LIMITS = {"CLAUDE.md": 36_000, "loop/LOOP.md": 120_000}
ADVICE = {
    "CLAUDE.md": "move the mechanism detail to loop/LOOP.md or the code's docstrings, keeping a one-line rule or "
                 "pointer in CLAUDE.md",
    "loop/LOOP.md": "move a mechanism's detail into its module's docstring or an incident into its MISSES.md entry, leaving a pointer",
}


POINTS_AT = "loop/LOOP.md"
# Docs that name things removed on purpose, or aren't ours: their pointers aren't checked.
UNCHECKED = ("docs/unsw/", "research/", "loop/MISSES.md")
POINTER = re.compile(r'LOOP\.md: ([A-Z][^.;)"`]*)')


def git(root: Path, *args: str) -> str | None:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


# A file's size in bytes: the working tree's copy (where None), the index's (":") or a commit's ("main"), or None
# when that copy doesn't exist.
def size(root: Path, path: str, where: str | None) -> int | None:
    if where is None:
        file = root / path
        return file.stat().st_size if file.is_file() else None
    found = git(root, "cat-file", "-s", f"{'' if where == ':' else where}:{path}")
    return int(found) if found else None


# The capped files over their cap, as {path, bytes, limit, advice}; with `only`, just those paths.
def over(root: Path, where: str | None, only: set[str] | None = None) -> list[dict]:
    found = []
    for path, limit in LIMITS.items():
        if only is not None and path not in only:
            continue
        measured = size(root, path, where)
        if measured is not None and measured > limit:
            found.append({"path": path, "bytes": measured, "limit": limit, "advice": ADVICE[path]})
    return found


# A file's text in the same copies as size(), or None when that copy doesn't exist. A symlink (AGENTS.md) is read as
# git stores it, its target's name, so it isn't checked twice.
def read(root: Path, path: str, where: str | None) -> str | None:
    if where is None:
        file = root / path
        return file.read_text() if file.is_file() and not file.is_symlink() else None
    done = subprocess.run(["git", "-C", str(root), "show", f"{'' if where == ':' else where}:{path}"],
                          capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


# Each "LOOP.md: A, B" pointer in the tracked .md files naming a heading loop/LOOP.md lacks, in that copy, as
# {path, pointer, advice}. A pointer may wrap across lines and ends at ".", ";", ")", a quote or a backtick.
def broken_pointers(root: Path, where: str | None) -> list[dict]:
    target = read(root, POINTS_AT, where)
    if target is None:
        return []
    headings = {line.lstrip("#").strip() for line in target.splitlines() if line.startswith("#")}
    listed = git(root, "ls-tree", "-r", "--name-only", where) if where not in (None, ":") else git(root, "ls-files")
    found = []
    for path in sorted(set((listed or "").splitlines())):  # a conflicted file is listed once per merge stage
        if not path.endswith(".md") or path == POINTS_AT or path.startswith(UNCHECKED):
            continue
        text = " ".join((read(root, path, where) or "").split())
        for match in POINTER.finditer(text):
            for name in match.group(1).split(", "):
                if name.strip() not in headings:
                    found.append({"path": path, "pointer": f"LOOP.md: {name.strip()}",
                                  "advice": f"{POINTS_AT} has no heading {name.strip()!r}: point at one it has, or "
                                            "name the renamed heading"})
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--staged", action="store_true", help="the index's copies of the files the commit stages")
    parser.add_argument("--rev", help="a commit's copies instead of the working tree's")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.staged:
        staged = git(root, "diff", "--cached", "--name-only", "--no-renames") or ""
        found = over(root, ":", set(staged.splitlines()))
        if any(path.endswith(".md") for path in staged.splitlines()):
            found += broken_pointers(root, ":")
    else:
        found = over(root, args.rev) + broken_pointers(root, args.rev)
    if args.json:
        json.dump(found, sys.stdout, indent=1)
        print()
    else:
        for item in found:
            if "pointer" in item:
                print(f"{item['path']}: {item['pointer']}: {item['advice']}")
            else:
                print(f"{item['path']}: {item['bytes']:,} bytes, over its {item['limit']:,}-byte cap "
                      f"(doc_size.LIMITS): {item['advice']}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
