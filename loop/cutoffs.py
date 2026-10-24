"""The hard cutoffs in a bot, and the ones a candidate adds or removes (loop/MISSES.md 56).

A hard cutoff is a bare number a decision switches on: "fold from round 397", "shed at length 6", "hunt within 6
steps". sig (2026-09-27) wants these to become rules over what the dragon observes (a formula, ratio, fitted curve or
probability over map size, distances, rounds left, lengths, opponent behaviour), and new ones not to creep in. This
module counts them, so the coder step can ask about each new one (loop/PROMPT.md), each recorded experiment carries
what it added and removed (the result's `cutoffs` field, driver.candidate_cutoffs), and stats.md shows whether
removing them wins (meta.cutoff_lines).

    .venv/bin/python loop/cutoffs.py [--repo DIR] [--base REV] [--head REV] [--json]

With no --base it lists the head's cutoffs; with --base it prints what the head adds, removes and changes against
it. The head is the working tree's bot/ unless --head names a commit, so a coder can run it before committing.

    .venv/bin/python loop/cutoffs.py --sweep [--state DIR]

--sweep is for the orchestrator when a standing rule lands (REFLECT 2026-09-28 17:52 F2: E0240, claimed at 12:05
before the generalisation rule reached the prompts at 13:50, went to evaluation with its hand-picked costs): one line
per hit, each with its id, over what was queued or claimed before the rule, so it can hold or note each before its
evaluation. A waiting (todo) idea with a bare threshold in its rule (meta.bare_thresholds, the check new ideas pass),
and an experiment in flight (runs/E####/experiment.json not finished, its idea trying) whose idea has one or whose
committed candidate adds cutoffs against its own base (the clone's HEAD while coding, read only after
meta.clone_intact, hooks and fsmonitor off; uncommitted edits are not read), its merge-base with the current champion
(own_base), so a candidate rebased since its claim isn't charged with the champion's newer numbers.

What counts (three kinds, each keyed for diff by kind and name, not by file or line, so moving code isn't a change):
- `param`: a numeric field of bot/params.hpp (`int shedMinLength = 6;`), except pure weights, whose names end in
  Score, Penalty, Weight, Bonus or Value: those only order moves (the 2026-09-27 inventory skipped them too).
- `compare`: a game quantity compared with a bare number in any bot/*.hpp or *.cpp (`w.round >= 350`,
  `3 < length`), with spaces round the operator as the bot's style writes them.
- `constant`: a named numeric constant outside params.hpp (`int const foldStart = 350;`, `constexpr int BLOCK = 4;`).
A compare or constant whose value is 0, 1 or 2 (or -1, -2) is skipped as an index, sign or sentinel check, and so is
the loop variable a `for (int dir = 0; dir < 4; ...)` header declares. helper.hpp (the toolkit's) and sonarkey.hpp
(hashing) are not read.

Limits of the heuristic, which is a text match, not a C++ parser:
- It misses a number used without a comparison operator (`std::min(reach, 14)`, `round % 50`, a default argument), a
  number away from the operator (`room < length * 3`), and a comparison written without spaces (`x>=7`).
- A number beside `*`, `/` or `%` (`roundsLeft < 3 * path`, `length * 3 > room`) is read as a formula's coefficient
  and not counted; one beside `+` or `-` (`x >= 5 + margin`, `length + 3 > room`) is counted as a threshold
  with an offset.
- It counts some structural numbers as cutoffs: bitset widths (`W <= 64`), direction indices (`d == 3`), named sizes
  (`BLOCK = 4`, `FAR = 100000`). So the inventory is an upper bound for compares and constants, and the diff is what
  the coder step and stats read: a false positive there costs one sentence in a `Choices:` line.
- Values of 0-2 are skipped, so `x > 2` as a real threshold is missed.
- An index or call argument longer than 120 characters (`a[...]`, `f(...)` on the compared side) hides that compare.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

LOOP = Path(__file__).resolve().parent
if str(LOOP) not in sys.path:
    sys.path.append(str(LOOP))  # --sweep imports home and meta, run as a script, with -m or from another folder

SKIPPED_FILES = ("helper.hpp", "sonarkey.hpp")
SOURCE_SUFFIXES = (".hpp", ".h", ".cpp", ".cc")
MAX_FILES = 200            # a bot has about 22 source files; more is not a bot we read
MAX_FILE_BYTES = 256 << 10  # a source file over 256 KB is skipped (the largest bot file is 31 KB; a coder's commit
                            # can hold anything)
SMALL_VALUES = {0.0, 1.0, 2.0, -1.0, -2.0}
WEIGHT_NAME = re.compile(r"(Score|Penalty|Weight|Bonus|Value)$")
# The results.jsonl `cutoffs` field keeps at most this many entries a list, each at most ENTRY_CHARS long: with the
# counts, under about 8 KB a row, typically under 1 KB.
LIST_MAX = 20
ENTRY_CHARS = 120
GIT_SAFE = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat")
GIT_TIMEOUT = 60

NUMBER = r"-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?[fFuUlL]*"
TYPE = r"(?:int|double|float|long|unsigned|short|std::u?int\d+_t|std::size_t|size_t)"
PARAM = re.compile(rf"^[ \t]*{TYPE}\s+(\w+)\s*=\s*({NUMBER})\s*;", re.M | re.ASCII)
OPERATOR = r"(?<![<>=!-])(<=|>=|==|!=|<|>)(?![<>=])"
# An index or call argument is at most 120 characters, so a line of unclosed `[` is not read in quadratic time.
OPERAND = r"[A-Za-z_]\w*(?:(?:\.|->|::)\w+|\[[^\]\n]{0,120}\]|\([^()\n]{0,120}\))*"
# A match starts only where an operand can (not inside a name or after `.`, `->` or `::`), so a long identifier is
# read in linear time; a number beside `*`, `/` or `%` is a formula's coefficient, not a threshold, but one beside
# `+` or `-` is still a threshold with an offset.
LEFT = re.compile(rf"(?<![\w.>:])({OPERAND})\s+{OPERATOR}\s+({NUMBER})(?![\w.])(?!\s*[*/%])", re.ASCII)
RIGHT = re.compile(rf"(?<![\w.])(?<![*/%]\s)(?<![*/%])({NUMBER})\s+{OPERATOR}\s+({OPERAND})", re.ASCII)
CONSTANT = re.compile(rf"\b(?:(?:constexpr|const)\s+{TYPE}|{TYPE}\s+(?:constexpr|const))\s+(\w+)\s*=\s*({NUMBER})\s*;",
                      re.ASCII)
FOR_VAR = re.compile(r"\bfor\s*\(\s*(?:\w+(?:::\w+)*\s+)+(\w+)\s*=")
# An unclosed /* runs to the end of the file and an unclosed quote to the end of its line (so a file of them is not
# read in quadratic time).
COMMENT_OR_STRING = re.compile(r'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*(?:"|$)|\'(?:\\.|[^\'\\\n])*(?:\'|$)',
                               re.S | re.M)
MIRROR = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}


@dataclass(frozen=True)
class Cutoff:
    kind: str   # param | compare | constant
    name: str   # the field or constant's name, or the compared quantity and operator ("w.round >=")
    value: str
    file: str
    line: int

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.name

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.name} {self.value}"


# A number's text as a float (suffixes dropped), for the small-value skip.
def number(text: str) -> float:
    return float(text.rstrip("fFuUlL"))


# `text` with comments and string literals blanked, keeping newlines so line numbers hold.
def code_only(text: str) -> str:
    return COMMENT_OR_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


# The 1-based line number of an offset in `text`, found by bisecting its newline offsets (a file with many matches
# stays linear).
def line_finder(text: str) -> Callable[[int], int]:
    newlines = [m.start() for m in re.finditer("\n", text)]
    return lambda offset: bisect.bisect_left(newlines, offset) + 1


# The loop variables each line's `for (...)` header declares, by line number.
def loop_vars(code: str, line_of: Callable[[int], int]) -> dict[int, set[str]]:
    found: dict[int, set[str]] = {}
    for m in FOR_VAR.finditer(code):
        found.setdefault(line_of(m.start()), set()).add(m.group(1))
    return found


def file_cutoffs(name: str, text: str) -> list[Cutoff]:
    code = code_only(text)
    line_of = line_finder(code)
    found = []
    if name == "params.hpp":
        found += [Cutoff("param", m.group(1), m.group(2), name, line_of(m.start()))
                  for m in PARAM.finditer(code) if not WEIGHT_NAME.search(m.group(1))]
    else:
        found += [Cutoff("constant", m.group(1), m.group(2), name, line_of(m.start()))
                  for m in CONSTANT.finditer(code) if number(m.group(2)) not in SMALL_VALUES]
    loops = loop_vars(code, line_of)
    sides = [(m, m.group(1), m.group(2), m.group(3)) for m in LEFT.finditer(code)]
    sides += [(m, m.group(3), MIRROR[m.group(2)], m.group(1)) for m in RIGHT.finditer(code)]
    for m, operand, op, value in sides:
        line = line_of(m.start())
        if number(value) in SMALL_VALUES or operand in loops.get(line, ()):
            continue
        found.append(Cutoff("compare", f"{operand} {op}", value, name, line))
    return sorted(found, key=lambda c: (c.file, c.line, c.name))


# Every cutoff in `sources` (bot file name -> text), skipping SKIPPED_FILES.
def inventory(sources: dict[str, str]) -> list[Cutoff]:
    return [c for name, text in sorted(sources.items()) if name not in SKIPPED_FILES for c in file_cutoffs(name, text)]


# The bot sources in a folder (a bot/ directory): regular files only, within MAX_FILES and MAX_FILE_BYTES.
def read_dir(bot_dir: Path) -> dict[str, str]:
    files = sorted(p for p in bot_dir.iterdir() if p.suffix in SOURCE_SUFFIXES and p.is_file() and not p.is_symlink())
    return {p.name: p.read_text(errors="replace") for p in files[:MAX_FILES] if p.stat().st_size <= MAX_FILE_BYTES}


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", *GIT_SAFE, *args], cwd=repo, input=stdin, capture_output=True, text=True,
                          errors="replace", check=True, timeout=GIT_TIMEOUT).stdout


# The bot sources at commit `rev` of `repo` (bot/ only, not its subfolders), read with git's object store only, so no
# checkout, hook or filter runs; the same size and count limits as read_dir.
def read_rev(repo: Path, rev: str) -> dict[str, str]:
    blobs = []
    for row in git(repo, "ls-tree", "-l", rev, "--", "bot/").splitlines():
        info, _, path = row.partition("\t")
        mode, kind, sha, size = info.split()
        name = path.removeprefix("bot/")
        if kind == "blob" and mode != "120000" and name.endswith(SOURCE_SUFFIXES) and int(size) <= MAX_FILE_BYTES:
            blobs.append((name, sha))
    blobs = blobs[:MAX_FILES]
    if not blobs:
        return {}
    out = subprocess.run(["git", *GIT_SAFE, "cat-file", "--batch"], cwd=repo, capture_output=True, check=True,
                         input="".join(f"{sha}\n" for _, sha in blobs).encode(), timeout=GIT_TIMEOUT).stdout
    sources, at = {}, 0
    for name, _ in blobs:
        header_end = out.index(b"\n", at)
        size = int(out[at:header_end].split()[2])
        sources[name] = out[header_end + 1:header_end + 1 + size].decode(errors="replace")
        at = header_end + 1 + size + 1
    return sources


# What `head` adds, removes and changes against `base`. Cutoffs match by key (kind and name); within a key, equal
# values cancel, a leftover pair of values is a change (a tune's new number), and the rest are added or removed.
def diff(base: list[Cutoff], head: list[Cutoff]) -> dict[str, list]:
    by_key: dict[tuple[str, str], tuple[list[Cutoff], list[Cutoff]]] = {}
    for side, found in ((0, base), (1, head)):
        for c in found:
            by_key.setdefault(c.key, ([], []))[side].append(c)
    added, removed, changed = [], [], []
    for key in sorted(by_key):
        old, new = by_key[key]
        common = Counter(c.value for c in old) & Counter(c.value for c in new)
        left_old, left_new = unmatched(old, common), unmatched(new, common)
        pairs = min(len(left_old), len(left_new))
        changed += list(zip(left_old[:pairs], left_new[:pairs]))
        added += left_new[pairs:]
        removed += left_old[pairs:]
    return {"added": added, "removed": removed, "changed": changed}


def unmatched(cutoffs: list[Cutoff], common: Counter) -> list[Cutoff]:
    left, spare = [], Counter(common)
    for c in cutoffs:
        if spare[c.value] > 0:
            spare[c.value] -= 1
        else:
            left.append(c)
    return left


def change_text(pair: tuple[Cutoff, Cutoff]) -> str:
    old, new = pair
    return f"{new.file}:{new.line} {new.name} {old.value} -> {new.value}"


# The results.jsonl `cutoffs` field for a candidate against the champion it was tested on (see LIST_MAX):
# `n_added_params` and `n_removed_params` are the params.hpp numbers among `n_added` and `n_removed`, so the rest are
# code compares and constants.
def summary(base: list[Cutoff], head: list[Cutoff]) -> dict:
    d = diff(base, head)
    texts = {"added": [str(c) for c in d["added"]], "removed": [str(c) for c in d["removed"]],
             "changed": [change_text(p) for p in d["changed"]]}
    return {"count": len(head), "base_count": len(base),
            **{f"n_{k}": len(v) for k, v in texts.items()},
            **{f"n_{k}_params": sum(c.kind == "param" for c in d[k]) for k in ("added", "removed")},
            **{k: [t[:ENTRY_CHARS] for t in v[:LIST_MAX]] for k, v in texts.items()}}


# summary() for commits `base` and `head` of `repo`.
def summary_at(repo: Path, base: str, head: str) -> dict:
    return summary(inventory(read_rev(repo, base)), inventory(read_rev(repo, head)))


# The --sweep lines (the module docstring) for loop home `state`, candidates' commits read from `repo` or, before one
# is recorded, from the coder's clone in `clones`.
def sweep(state: Path, repo: Path, clones: Path) -> list[str]:
    import meta
    ideas = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines() if line.strip()]
    lines = [f"{idea['id']} (todo): {bare_text(found)}" for idea in ideas
             if idea.get("status") == "todo" and (found := meta.bare_thresholds(idea))]
    trying = {idea["id"] for idea in ideas if str(idea.get("status")).startswith("trying:")}
    champion = meta.champion_commit(repo)
    for path in sorted((state / "runs").glob("E*/experiment.json")):
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if row.get("finished") or not isinstance(row.get("idea"), dict) or row["idea"].get("id") not in trying:
            continue
        name = f"{row.get('id')} ({row['idea'].get('id')})"
        if found := meta.bare_thresholds(row["idea"]):
            lines.append(f"{name} in flight, its idea: {bare_text(found)}")
        try:
            where, rev = (repo, row["candidate"]) if row.get("candidate") else (clones / row["id"], "HEAD")
            if where != repo:
                meta.clone_intact(where, state)
            base_where, base = own_base(repo, where, rev, champion) or (repo, row["base"])
            added = diff(inventory(read_rev(base_where, base)), inventory(read_rev(where, rev)))["added"]
        except (OSError, RuntimeError, KeyError, ValueError, subprocess.SubprocessError) as error:
            lines.append(f"{name} in flight: its cutoffs couldn't be read ({type(error).__name__}: {str(error)[:120]})")
            continue
        if added:
            lines.append(f"{name} in flight, its candidate adds {len(added)} cutoff(s): "
                         + "; ".join(str(c) for c in added[:5]) + (" ..." if len(added) > 5 else ""))
    return lines


# The commit a candidate `rev` in `where` (the repo or its clone) was last rebased onto, as (where, commit): its
# merge-base with the current `champion`, so one rebased since its claim is read against the champion it now sits on
# and one not yet rebased against its own older base (experiment.json's `base` is the claim's and stays put: on 29 Sep
# E0345-E0350, rebased onto cfe3b39, listed its driftFactor as theirs). The base's sources are read from `repo`, the
# main checkout, whenever it has that commit (the trusted side never trusts a worker's folder, MISSES 17), from the
# clone only for one it lacks. None when there is no champion or `where` lacks it, so the caller reads the recorded
# base.
def own_base(repo: Path, where: Path, rev: str, champion: str) -> tuple[Path, str] | None:
    if not champion:
        return None
    try:
        base = git(where, "merge-base", rev, champion).strip()
    except subprocess.SubprocessError:
        return None
    try:
        git(repo, "cat-file", "-e", f"{base}^{{commit}}")
        return repo, base
    except subprocess.SubprocessError:
        return where, base


def bare_text(found: list[tuple[str, str]]) -> str:
    more = f" (and {len(found) - 1} more)" if len(found) > 1 else ""
    return f"bare threshold {found[0][0]!r} in {found[0][1][:140]!r}{more}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path("."), help="the checkout (default: here)")
    parser.add_argument("--sweep", action="store_true", help="what was queued or claimed before a rule: see above")
    parser.add_argument("--state", type=Path, help="with --sweep, the loop home (default: home.loop_home())")
    parser.add_argument("--base", help="compare against this commit")
    parser.add_argument("--head", help="read this commit instead of the working tree's bot/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.sweep:
        from home import clones_folder, loop_home
        repo = args.repo.resolve()
        print("\n".join(sweep(args.state or loop_home(), repo, clones_folder(repo))) or "nothing found")
        return 0
    head = inventory(read_rev(args.repo, args.head) if args.head else read_dir(args.repo / "bot"))
    kinds = Counter(c.kind for c in head)
    counts = ", ".join(f"{kinds[k]} {k}" for k in ("param", "compare", "constant"))
    if args.base is None:
        if args.json:
            print(json.dumps([asdict(c) for c in head], indent=1))
            return 0
        print(f"{len(head)} cutoffs ({counts}):")
        print("\n".join(f"  {c}" for c in head))
        return 0
    base = inventory(read_rev(args.repo, args.base))
    if args.json:
        print(json.dumps(summary(base, head), indent=1))
        return 0
    d = diff(base, head)
    print(f"{len(head)} cutoffs ({counts}); against {args.base}: {len(d['added'])} added, "
          f"{len(d['removed'])} removed, {len(d['changed'])} changed in value")
    print("\n".join([*(f"  + {c}" for c in d["added"]), *(f"  - {c}" for c in d["removed"]),
                     *(f"  ~ {change_text(p)}" for p in d["changed"])]))
    if d["added"]:
        print("Each added cutoff needs its line in Choices: what it stands in for, and why no formula or measured "
              "chance over what the dragon observes does better (loop/PROMPT.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
