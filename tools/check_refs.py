"""Finds references in comments and docs that no longer resolve, so a rename, move, deletion or retune that left
its comments behind shows up:

- path: a repo path (`loop/driver.py`, referee/gauntlet.toml) that isn't tracked. Backticked folders count; plain
  words need a file extension, since prose writes "referee/quick" for "referee or quick". Bare file names count only
  in bot/ and tests/bot/ (`shed.hpp`); elsewhere they are mostly live state (`study.md`) or the toolkit's. A path
  under a top folder the repo never had is prose or live state (`discord/latest.md`); one the history had counts.
- name: `driver.check_pool`, `sprt.BaselineCache.key`, where the module is a .py in loop/, referee/, tools/ or
  mapgen/ and doesn't define that name at top level (or the class has no such member). Read with ast, not imported.
- param: `P.<name>` that params.hpp lacks, or any camelCase word that was a param in params.hpp's git history and
  isn't one now (feed.hpp's old round gate, say).
- value (a warning, never an error): a param quoted with a value ("`someRound` 450", "P.<name> = 450") that
  differs from params.hpp's. Lines with a date or an E#### id are skipped: history quotes old values.

In a history file (HISTORY: a dated log of past events, which names removed things on purpose) every problem is a
warning, so neither the pre-commit hook nor health.py's DOC REFS alert stops on it.

    .venv/bin/python tools/check_refs.py [--root DIR] [--params PATH] [--json] [--hook] [PATH ...]

It reads `#` comments and docstrings in .py, `//` and `/* */` comments in .hpp/.cpp, and all of every .md, in the
root's tracked files (or only those under the given PATHs), leaving out the official docs' mirror (docs/unsw/) and
external research. The bot lives on `loop/champion`, not in main's tree: params come from its bot/params.hpp and
paths under bot/ and tests/bot/ resolve in its tree (by git, so main needs no bot/), unless --params names a
params.hpp, when both come from the root instead (checking a bot checkout: --root CLONE --params
CLONE/bot/params.hpp bot tests/bot). A branch name (`loop/champion`) is not a path. Live state
(`~/.bc-loop/...`, `<loop home>/...`), placeholders (`E####`, `{clone}`, `*`) and URLs are not checked. It prints
`path:line: <kind> <reference> (why)` per problem, in line order within a file, and exits 1 when there are errors;
--json prints a list of {path, line, kind, ref, why, level}. --hook (tools/hooks/pre-commit) makes an error in a file
the commit doesn't stage (a merge: a file the merge doesn't change) a warning, "fix on main", unless the commit changes
what it points at (a renamed module or path, params.hpp): one broken reference on main, or a promotion that removed a
param, shouldn't stall every worktree's unrelated commits, but a rename that breaks a doc elsewhere still blocks.

    .venv/bin/python tools/check_refs.py --drift [--rev REV] [--since REV] [--top N | --all] [--show N] [--json]
        [PATH ...]

--drift lists comments that may have gone stale instead: each comment block documenting a definition (the `#`
comment above a Python def, class or top-level constant, a docstring, the `//` comment above a C++ function, struct
or params.hpp field group) whose definition's lines, by `git blame`, were last changed in commits after the block's
own last change. Most drifted first (later commits, then lines). A work list for re-reading, never an error: it
misses comments that were wrong when written or went stale because other code changed (a caller, a sibling file).
--since REV keeps only candidates whose definition changed in a commit REV lacks (or uncommitted), so a periodic
check sees what is new since its last run (each REFLECT, loop/REFLECT.md step 4) rather than the same few hundred.
--show N prints under each candidate the first N lines of its comment and definition as they are now (a module's
docstring only), numbered and followed by a blank line, so a reader without the repo (the REFLECT worker) can judge it.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import threading
import time
import tokenize
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

# The official docs' mirror, external research, and this checker's own fixture (broken on purpose).
SKIP = ("docs/unsw/", "research/mit-battlecode/", "research/rsi/", "research/snake-ai-and-eval/",
        "tools/testdata/check-refs/")
MODULE_DIRS = ("loop", "referee", "tools", "mapgen")
CHAMPION = "loop/champion"
# Dated logs of past events: loop/MISSES.md records each miss with the names and paths it had then. PLAN.md, the
# research notes and the playbook describe the present, so a removed name there is still an error.
HISTORY = ("loop/MISSES.md",)
BOT_DIRS = ("bot/", "tests/bot/")
# A `name.ext` with one of these is a file name, not a module attribute.
EXTENSIONS = {"py", "md", "json", "jsonl", "toml", "log", "hpp", "cpp", "h", "txt", "map", "replay", "capnp", "sh",
              "yml", "yaml", "csv", "lock", "html", "pid", "tsv", "cfg", "ini", "wasm", "zip", "tar", "gz", "png"}
PATH_TOKEN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?")
QUALIFIED = re.compile(r"(?<![\w./-])([a-z_][a-z0-9_]*)\.([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?")
PARAM_REF = re.compile(r"(?<![\w.])P\.([a-z]\w*)")
BARE_SOURCE = re.compile(r"(?<![\w/.<>*-])(\w+\.(?:hpp|cpp))\b")
CAMEL_WORD = re.compile(r"(?<![\w.])([a-z]+[A-Z][A-Za-z0-9]*)")
PARAM_DECL = re.compile(r"^\s*(?:int|double|bool|float|long)\s+([a-z]\w*)\s*=\s*([^;]+);")
# "`someRound` 450", "P.<name> = 450", "P.<name> (3e6)", but not "P.<name> 430 -> 415" (made-up names: real ones
# here would be checked too).
QUOTED_VALUE = re.compile(r"(?:`([a-z]\w*)`|(?<![\w.])P\.([a-z]\w*))\s*(?:=\s*|\(\s*)?"
                          r"(-?\d+(?:\.\d+)?(?:e-?\d+)?|true|false)(?!\w|\.\d)(?!\s*(?:->|→|to\b|and\b|or\b|,\s*\d))")
DATED = re.compile(r"\b20\d\d-\d\d-\d\d\b|\bE\d{4}\b")
SPLITTERS = re.compile(r"[\s`'\"()\[\]{}<>,;|=]+")


@dataclass
class Problem:
    path: str
    line: int
    kind: str
    ref: str
    why: str
    level: str = "error"

    def text(self) -> str:
        why = f"warning: {self.why}" if self.level == "warning" else self.why
        return f"{self.path}:{self.line}: {self.kind} {self.ref} ({why})"


@dataclass
class Params:
    names: set[str]
    values: dict[str, str]


@dataclass
class Context:
    root: Path
    files: set[str]
    dirs: set[str]
    bot_files: set[str]  # the bot's tree: bot/ and tests/bot/ paths resolve here, not in the root
    bot_dirs: set[str]
    bot_names: set[str]  # its file names, for bare `shed.hpp`
    branches: set[str]
    modules: dict[str, list[dict[str, set[str]]]]
    former_params: set[str]
    params: Params
    former_dirs: set[str]  # top-level folders the history ever had


def git(root: Path, *args: str) -> str | None:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def folders_of(files: set[str]) -> set[str]:
    dirs = set()
    for name in files:
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return dirs


def parse_params(source: str) -> Params:
    values = {}
    for line in source.splitlines():
        match = PARAM_DECL.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return Params(set(values), values)


# Every param name any version of params.hpp on main or the champion declared, from git history.
def historical_params(root: Path) -> set[str]:
    names: set[str] = set()
    for ref in (CHAMPION, "HEAD"):
        log = git(root, "log", "-p", "--format=", ref, "--", "bot/params.hpp") or ""
        for line in log.splitlines():
            if line[:1] in "+-":
                match = PARAM_DECL.match(line[1:])
                if match:
                    names.add(match.group(1))
    return names


# Top-level names a module defines (defs, classes, assignments, imports, also inside top-level if/try), each
# class mapped to the names its body and its methods' `self.x =` define.
def module_names(source: str) -> dict[str, set[str]]:
    tree = ast.parse(source)
    names: dict[str, set[str]] = {}

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                names[node.name] = class_names(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names[node.name] = set()
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names[(alias.asname or alias.name).split(".")[0]] = set()
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for leaf in ast.walk(target):
                        if isinstance(leaf, ast.Name):
                            names[leaf.id] = set()
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                visit(node.body)
                visit(getattr(node, "orelse", []))
                visit(getattr(node, "finalbody", []))
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)

    visit(tree.body)
    return names


def class_names(node: ast.ClassDef) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(child.name)
        elif isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            found.add(child.id)
        elif isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Store):
            found.add(child.attr)
    return found


def load_modules(root: Path, files: set[str]) -> dict[str, list[dict[str, set[str]]]]:
    modules: dict[str, list[dict[str, set[str]]]] = {}
    for name in sorted(files):
        folder, _, leaf = name.partition("/")
        if folder in MODULE_DIRS and "/" not in leaf and leaf.endswith(".py"):
            try:
                defined = module_names((root / name).read_text())
            except (OSError, SyntaxError):
                continue
            modules.setdefault(leaf[:-3], []).append(defined)
    return modules


def bot_basenames(files: set[str]) -> set[str]:
    return {name.rsplit("/", 1)[-1] for name in files if name.startswith(BOT_DIRS)}


# What a check needs to know about a tree: its files, the bot's (whose bot/ and tests/bot/ references resolve),
# branch names (`loop/champion` is one, not a path), param names ever declared, the current params, and the
# top-level folders the tree's history ever had (a path into one since deleted is broken, not prose).
def make_context(root: Path, files: set[str], bot_files: set[str], branches: set[str], former: set[str],
                 params: Params, former_dirs: set[str] | None = None) -> Context:
    bot_files = {name for name in bot_files if name.startswith(BOT_DIRS)}
    return Context(root, files, folders_of(files), bot_files, folders_of(bot_files), bot_basenames(bot_files),
                   branches, load_modules(root, files), former, params, former_dirs or set())


def historical_dirs(root: Path) -> set[str]:
    names = (git(root, "log", "--format=", "--name-only", "HEAD") or "").splitlines()
    return {name.split("/", 1)[0] for name in names if "/" in name}


# The bot lives on `loop/champion`, not in main's tree, so its files and params come from that branch. With
# --params (checking a bot checkout itself) they are the root's own; so they are when the branch is missing.
def load_context(root: Path, params_path: Path | None) -> Context:
    files = set((git(root, "ls-files") or "").splitlines())
    branches = set((git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads") or "").splitlines())
    champion = git(root, "ls-tree", "-r", "--name-only", CHAMPION) if params_path is None else None
    bot_files = set(champion.splitlines()) if champion is not None else files
    if params_path is not None:
        source = params_path.read_text()
    elif champion is not None:
        source = git(root, "show", f"{CHAMPION}:bot/params.hpp") or ""
    else:
        own = root / "bot/params.hpp"
        source = own.read_text() if own.exists() else ""
    return make_context(root, files, bot_files, branches, historical_params(root), parse_params(source),
                        historical_dirs(root))


# (line number, text) pieces of a Python file's comments and docstrings.
def python_text(source: str) -> list[tuple[int, str]]:
    pieces = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                pieces.append((token.start[0], token.string))
        tree = ast.parse(source)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return pieces
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str) and first.end_lineno is not None):
                for number in range(first.lineno, first.end_lineno + 1):
                    pieces.append((number, lines[number - 1]))
    return pieces


# (line number, text) pieces of a C++ file's comments, skipping string and character literals.
def cpp_text(source: str) -> list[tuple[int, str]]:
    pieces: list[tuple[int, str]] = []
    line, seen = 1, 0
    for start, end in cpp_spans(source):
        line += source.count("\n", seen, start)
        seen = start
        if source.startswith(("//", "/*"), start):
            pieces += [(line + offset, text) for offset, text in enumerate(source[start:end].split("\n"))]
    return pieces


# A file's checked pieces in line order (a .py file's comments are found before its docstrings).
def text_of(path: str, source: str) -> list[tuple[int, str]]:
    if path.endswith(".py"):
        return sorted(python_text(source), key=lambda piece: piece[0])
    if path.endswith((".hpp", ".cpp", ".h")):
        return cpp_text(source)
    return list(enumerate(source.splitlines(), start=1))


def path_exists(ctx: Context, path: str) -> bool:
    path = path.rstrip("/")
    if path.startswith(BOT_DIRS) or path in ("bot", "tests/bot"):
        return path in ctx.bot_files or path in ctx.bot_dirs
    return path in ctx.files or path in ctx.dirs


def check_path(ctx: Context, where: str, token: str, backticked: bool) -> str | None:
    token = re.sub(r":\d+(?:-\d+)?$", "", token.rstrip(".:"))
    if not PATH_TOKEN.fullmatch(token) or "/" not in token.rstrip("/") or token.startswith(("./", "../")):
        return None
    first = token.split("/")[0]
    folder = str(Path(where).parent)
    local = f"{folder}/{first}" if folder != "." else first
    gone = first in ctx.former_dirs and first not in ctx.dirs and first not in ctx.bot_dirs
    if not gone and not any(name in ctx.dirs or name in ctx.bot_dirs for name in (first, local)):
        return None  # prose and live state (`discord/latest.md`) name folders the repo never had
    has_ext = "." in token.rsplit("/", 1)[-1].lstrip(".")
    if not backticked and not has_ext:
        return None
    if token in ctx.branches:
        return None
    stem, dot, attr = token.rpartition(".")
    if dot and path_exists(ctx, f"{stem}.py") and attr not in EXTENSIONS:
        return None  # `loop/discord_teams.refresh` names a function: names_in checks it as discord_teams.refresh
    if path_exists(ctx, token) or (folder != "." and path_exists(ctx, f"{folder}/{token}")):
        return None
    return f"no such file or folder: {first}/ was deleted" if gone else "no such file or folder"


def paths_in(ctx: Context, where: str, text: str) -> list[tuple[str, str]]:
    found = []
    ticked = {span.strip() for span in re.findall(r"`([^`]+)`", text)}
    for token in SPLITTERS.split(text):
        if not token or "~" in token or "#" in token or "*" in token or "$" in token or "://" in token:
            continue
        if token.startswith("/") or token.startswith("-"):
            continue
        why = check_path(ctx, where, token, token in ticked)
        if why:
            found.append((token.rstrip(".:"), why))
    return found


# Bare `shed.hpp` names in the bot's own files and tests: another file of the bot (or its tests). Elsewhere a bare
# file name is too often live state (`study.md`) or the toolkit's (`engine.py`) to check.
def bot_files_in(ctx: Context, where: str, text: str) -> list[tuple[str, str]]:
    if not where.startswith(BOT_DIRS):
        return []
    return [(name, "no such file in bot/ or tests/bot/") for name in BARE_SOURCE.findall(text)
            if name not in ctx.bot_names]


def names_in(ctx: Context, text: str) -> list[tuple[str, str]]:
    found = []
    for match in QUALIFIED.finditer(text):
        module, attr, sub = match.groups()
        if module not in ctx.modules or attr in EXTENSIONS:
            continue
        versions = ctx.modules[module]
        if not any(attr in defined for defined in versions):
            found.append((f"{module}.{attr}", f"{module}.py defines no {attr}"))
        elif sub and sub not in EXTENSIONS:
            members = [defined[attr] for defined in versions if attr in defined]
            if all(member for member in members) and not any(sub in member for member in members):
                found.append((f"{module}.{attr}.{sub}", f"{module}.{attr} has no {sub}"))
    return found


def params_in(ctx: Context, params: Params, text: str) -> list[tuple[str, str]]:
    found = []
    for name in PARAM_REF.findall(text):
        if name not in params.names:
            found.append((f"P.{name}", "not in params.hpp"))
    for name in CAMEL_WORD.findall(text):
        if name in ctx.former_params and name not in params.names:
            found.append((name, "a former param, not in params.hpp"))
    return found


def values_in(params: Params, text: str) -> list[tuple[str, str]]:
    if DATED.search(text):
        return []
    found = []
    for match in QUOTED_VALUE.finditer(text):
        name = match.group(1) or match.group(2)
        quoted = match.group(3)
        current = params.values.get(name)
        if current is not None and not same_value(quoted, current):
            found.append((f"{name} {quoted}", f"params.hpp has {current}"))
    return found


def same_value(quoted: str, current: str) -> bool:
    if quoted in ("true", "false") or current in ("true", "false"):
        return quoted == current
    try:
        return float(quoted) == float(current)
    except ValueError:
        return True


def check_file(ctx: Context, path: str) -> list[Problem]:
    try:
        source = (ctx.root / path).read_text()
    except (OSError, UnicodeDecodeError):
        return []
    params = ctx.params
    problems = []
    for number, text in text_of(path, source):
        paths = paths_in(ctx, path, text) + bot_files_in(ctx, path, text)
        for kind, hits in (("path", paths), ("name", names_in(ctx, text)), ("param", params_in(ctx, params, text))):
            problems += [Problem(path, number, kind, ref, why) for ref, why in hits]
        problems += [Problem(path, number, "value", ref, why, "warning") for ref, why in values_in(params, text)]
    if path in HISTORY:
        for problem in problems:
            if problem.level == "error":
                problem.level, problem.why = "warning", f"{problem.why}; a history file"
    return problems


def under(path: str, only: list[str]) -> bool:
    return not only or any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in only)


def scanned(ctx: Context, only: list[str]) -> list[str]:
    chosen = []
    for path in sorted(ctx.files):
        if path.startswith(SKIP) or not path.endswith((".py", ".hpp", ".cpp", ".h", ".md")):
            continue
        if (ctx.root / path).is_symlink():
            continue  # AGENTS.md is CLAUDE.md
        if under(path, only):
            chosen.append(path)
    return chosen


# --- Drift: comments whose definition changed after them ------------------------------------------------------
# A comment block documenting a definition (the `#` comment above a Python def or class and its docstring, the `//`
# comment above a C++ function, struct or params.hpp field group) is a drift candidate when commits after the
# block's own last change touched the definition's lines, by `git blame`. It is a work list for re-reading, not a
# check: the body can change without the comment going wrong.

CPP_KEYWORDS = {"if", "for", "while", "switch", "return", "else", "case", "do", "catch", "sizeof", "new", "delete",
                "throw", "using", "typedef", "static_assert", "decltype", "alignof", "co_return", "operator"}
CPP_STRUCT = re.compile(r"\s*(?:template\s*<[^>]*>\s*)?(?:struct|class)\s+(\w+)")
CPP_FUNCTION = re.compile(r"\s*(?:template\s*<[^>]*>\s*)?([\w:<>,*&~\s]*?)(~?\w+)\s*\(")
HEX40 = re.compile(r"[0-9a-f]{40}")
MODULE = "(module)"  # a module docstring's Doc name
SECTION = re.compile(r"\s*//\s*---")


@dataclass
class Doc:
    name: str
    doc: list[int]   # the comment's and docstring's line numbers
    body: list[int]  # the definition's own lines


@dataclass
class Drift:
    path: str
    line: int
    name: str
    comment_sha: str
    comment_time: int
    later: list[str]  # the commits after the comment that last changed a body line, newest first
    lines: int        # body lines they changed
    start: int = 0    # the first and last lines of the comment and definition together (a module's: its docstring)
    end: int = 0

    def text(self) -> str:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.comment_time))
        shas = ", ".join(self.later[:5]) + (f", +{len(self.later) - 5} more" if len(self.later) > 5 else "")
        return (f"{self.path}:{self.line}: {self.name}: comment last changed {when} ({self.comment_sha}), body "
                f"changed in {len(self.later)} later commits ({shas})")


# Line numbers of the comment block (lines starting with `marker`) directly above line `start`.
def comment_above(lines: list[str], start: int, marker: str) -> list[int]:
    found = []
    number = start - 1
    while number >= 1 and lines[number - 1].strip().startswith(marker):
        found.append(number)
        number -= 1
    return sorted(found)


DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_lines(node: ast.AST) -> list[int]:
    body = node.body if isinstance(node, DOCUMENTED) else []
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str) and body[0].end_lineno is not None:
        return list(range(body[0].lineno, body[0].end_lineno + 1))
    return []


def python_docs(source: str) -> list[Doc]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    docs: list[Doc] = []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.end_lineno:
                start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
                own = docstring_lines(node)
                span = set(range(start, node.end_lineno + 1)) - set(own)
                if isinstance(node, ast.ClassDef):  # a class's body is its own lines; each method is its own entry
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.end_lineno:
                            span -= set(range(child.lineno + 1, child.end_lineno + 1))
                    first = len(docs)
                    visit(node.body, f"{prefix}{node.name}.")
                    for inner in docs[first:]:
                        span -= set(inner.doc)
                doc = comment_above(lines, start, "#") + own
                if doc:
                    docs.append(Doc(prefix + node.name, doc, sorted(span)))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix and node.end_lineno:
                doc = comment_above(lines, node.lineno, "#")
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if doc and isinstance(target, ast.Name):
                    docs.append(Doc(target.id, doc, list(range(node.lineno, node.end_lineno + 1))))
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                visit(node.body, prefix)
                visit(getattr(node, "orelse", []), prefix)

    visit(tree.body, "")
    own = docstring_lines(tree)
    if own:  # the module docstring documents the whole file
        docs.append(Doc(MODULE, own, [n for n in range(1, len(lines) + 1) if n not in own]))
    return docs


# The source with comments and string literals blanked (newlines kept), so braces and semicolons are code; with
# `strings`, only comments are blanked.
def cpp_code(source: str, strings: bool = False) -> str:
    out = list(source)
    for start, end in cpp_spans(source):
        if strings and not source.startswith(("//", "/*"), start):
            continue
        for i in range(start, end):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


# (start, end) offsets of every comment and string or character literal.
def cpp_spans(source: str) -> list[tuple[int, int]]:
    spans = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c in "\"'":
            start, i = i, i + 1
            while i < n and source[i] != c and source[i] != "\n":
                i += 2 if source[i] == "\\" else 1
            i += 1
            spans.append((start, min(i, n)))
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end < 0 else end
            spans.append((i, end))
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            spans.append((i, end))
            i = end
        else:
            i += 1
    return spans


# The line of the brace closing the first `{` at or after `offset`, when a `{` comes before any `;`.
def block_end(code: str, offset: int) -> int | None:
    brace, semi = code.find("{", offset), code.find(";", offset)
    if brace < 0 or 0 <= semi < brace:
        return None
    depth = 0
    for i in range(brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code.count("\n", 0, i) + 1
    return None


# (name, last line) of the definition starting on line `number`, or None.
def cpp_definition(lines: list[str], code_lines: list[str], code: str, offsets: list[int],
                   number: int) -> tuple[str, int] | None:
    raw, text = lines[number - 1], code_lines[number - 1]
    param = PARAM_DECL.match(raw)
    if param:
        end = number
        while end < len(lines) and PARAM_DECL.match(lines[end]):
            end += 1
        return param.group(1), end
    match = CPP_STRUCT.match(text)
    if not match:
        match = CPP_FUNCTION.match(text)
        if not match or match.group(2) in CPP_KEYWORDS:
            return None
        prefix = match.group(1)
        if set(prefix.split()) & CPP_KEYWORDS or (prefix and not prefix[-1].isspace() and prefix[-1] not in "*&"):
            return None
    end = block_end(code, offsets[number - 1] + match.end())
    name = match.group(1) if match.re is CPP_STRUCT else match.group(2)
    return (name, end) if end else None


def cpp_docs(source: str) -> list[Doc]:
    lines = source.splitlines()
    code = cpp_code(source)
    code_lines = code.splitlines()
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    depth = [0]  # brace depth at the start of each line (1-based)
    for text in code_lines:
        depth.append(depth[-1] + text.count("{") - text.count("}"))
    docs = []
    for number in range(2, len(lines) + 1):
        if not lines[number - 2].strip().startswith("//") or lines[number - 1].strip().startswith("//"):
            continue
        found = cpp_definition(lines, code_lines, code, offsets, number)
        if found:
            name, end = found
            body = list(range(number, end + 1))
            if CPP_STRUCT.match(code_lines[number - 1]):  # a struct's body is its members, not their bodies
                body = [n for n in body if depth[n - 1] <= depth[number - 1] + 1]
            # A section header ("// --- Survival ---") titles what follows; it documents no one definition.
            doc = [n for n in comment_above(lines, number, "//") if not SECTION.match(lines[n - 1])]
            if doc:
                docs.append(Doc(name, doc, body))
    for outer in docs:  # a struct's methods and fields with their own comments are their own entries
        inside = [d for d in docs if d is not outer and outer.body[0] < d.body[0] <= outer.body[-1]]
        if inside:
            nested = {line for d in inside for line in d.doc + d.body}
            outer.body = [line for line in outer.body if line not in nested]
    return docs


# Each documented definition, its body without the lines that carry no code (blank lines, comments, docstrings),
# so an edit to those never counts as the definition changing.
def file_docs(path: str, source: str) -> list[Doc]:
    docs = python_docs(source) if path.endswith(".py") else cpp_docs(source)
    quiet = quiet_lines(path, source)
    for doc in docs:
        doc.body = [line for line in doc.body if line not in quiet]
    return docs


def quiet_lines(path: str, source: str) -> set[int]:
    if path.endswith(".py"):
        lines = source.splitlines()
        quiet = {n for n, line in enumerate(lines, 1) if not line.strip() or line.strip().startswith("#")}
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return quiet
        for node in ast.walk(tree):
            quiet |= set(docstring_lines(node))
        return quiet
    return {n for n, line in enumerate(cpp_code(source, strings=True).splitlines(), 1) if not line.strip()}


# Lines holding code and a comment (`x = 1  # why`): a commit that last changed one may have changed only its
# comment, which History.comment_only settles.
def commented_lines(path: str, source: str) -> set[int]:
    python = path.endswith(".py")  # text_of also gives a .py file's docstring lines, which are quiet anyway
    comments = {n for n, text in text_of(path, source) if not python or text.lstrip().startswith("#")}
    return comments - quiet_lines(path, source)


# The file's code with comments, docstrings and layout dropped: equal for two versions that differ only in those.
def code_of(path: str, source: str) -> str | None:
    if not path.endswith(".py"):
        return " ".join(cpp_code(source, strings=True).split())
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, DOCUMENTED) and docstring_lines(node):
            node.body = node.body[1:]
    return ast.dump(tree)


# Answers, for one drift run, whether a commit changed only comments, docstrings or layout in a file (the all-zero
# commit: the uncommitted edits against HEAD). Such a commit is never drift, even where it touched a line with code
# on it. File versions come from one `git cat-file --batch`, since a run asks about hundreds.
class History:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.answers: dict[tuple[str, str], bool] = {}
        self.cat = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE)

    def close(self) -> None:
        if self.cat.stdin:
            self.cat.stdin.close()
        self.cat.wait()

    def show(self, spec: str) -> str | None:
        assert self.cat.stdin and self.cat.stdout
        self.cat.stdin.write(spec.encode() + b"\n")
        self.cat.stdin.flush()
        header = self.cat.stdout.readline().split()
        if len(header) != 3:  # "<spec> missing"
            return None
        data = self.cat.stdout.read(int(header[2]) + 1)[:-1]
        return data.decode(errors="replace") if header[1] == b"blob" else None

    def comment_only(self, sha: str, path: str) -> bool:
        with self.lock:
            if (sha, path) not in self.answers:
                if sha == "0" * 40:
                    after = (self.root / path).read_text() if (self.root / path).is_file() else None
                    before = self.show(f"HEAD:{path}")
                else:
                    after, before = self.show(f"{sha}:{path}"), self.show(f"{sha}^:{path}")
                code = code_of(path, after) if after is not None and before is not None else None
                self.answers[(sha, path)] = code is not None and code == code_of(path, before or "")
            return self.answers[(sha, path)]


# Per line, the commit that last changed it, and each commit's committer time, from `git blame --porcelain`.
def blame(root: Path, path: str, rev: str | None) -> tuple[dict[int, str], dict[str, int]]:
    out = git(root, "blame", "--porcelain", *([rev] if rev else []), "--", path) or ""
    shas: dict[int, str] = {}
    times: dict[str, int] = {}
    sha = ""
    for row in out.splitlines():
        head = row.split(" ")
        if len(head) >= 3 and HEX40.fullmatch(head[0]):
            sha = head[0]
            shas[int(head[2])] = sha
        elif row.startswith("committer-time "):
            times[sha] = int(row.split(" ", 1)[1])
    return shas, times


# Each commit's place in `rev`'s history, newest first (0): a line's change is later than another's when its
# commit comes first, whatever the clocks said (rebases rewrite them). Uncommitted lines are the newest of all.
def commit_order(root: Path, rev: str | None) -> dict[str, int]:
    listing = git(root, "rev-list", "--topo-order", rev or "HEAD") or ""
    order = {sha: i for i, sha in enumerate(listing.split())}
    order["0" * 40] = -1
    return order


# `commented` holds the file's lines with code and a comment on them: a later commit that last changed only such
# lines of the body may have changed only their comments, so git is asked; any other body line it changed is code.
# With `new` (full shas), a candidate needs at least one of its later commits in it.
def drift_of(history: History, path: str, doc: Doc, shas: dict[int, str], times: dict[str, int],
             order: dict[str, int], commented: set[int], new: set[str] | None = None) -> Drift | None:
    def rank(sha: str) -> int:
        return order.get(sha, len(order))

    doc_shas = [shas[line] for line in doc.doc if line in shas]
    if not doc_shas:
        return None
    last = min(doc_shas, key=rank)
    touched: dict[str, list[int]] = {}
    for line in doc.body:
        if line in shas and rank(shas[line]) < rank(last):
            touched.setdefault(shas[line], []).append(line)
    after = [sha for sha, lines in touched.items() for _ in lines
             if not set(lines) <= commented or not history.comment_only(sha, path)]
    if not after or (new is not None and not new & set(after)):
        return None
    later = sorted(set(after), key=rank)
    span = doc.doc if doc.name == MODULE else doc.doc + doc.body  # a module's body is the whole file
    return Drift(path, doc.doc[0], doc.name, last[:7], times.get(last, 0), [sha[:7] for sha in later], len(after),
                 min(span), max(span))


def file_drift(history: History, path: str, rev: str | None, order: dict[str, int],
               new: set[str] | None = None) -> list[Drift]:
    root = history.root
    if rev:
        source = git(root, "show", f"{rev}:{path}")
    else:  # the index still lists a file deleted but not staged
        source = (root / path).read_text() if (root / path).is_file() else None
    if source is None:
        return []
    shas, times = blame(root, path, rev)
    commented = commented_lines(path, source)
    found = (drift_of(history, path, doc, shas, times, order, commented, new) for doc in file_docs(path, source))
    return [drift for drift in found if drift]


def drift_files(root: Path, rev: str | None, only: list[str]) -> list[str]:
    listing = git(root, "ls-tree", "-r", "--name-only", rev) if rev else git(root, "ls-files")
    return [path for path in sorted((listing or "").splitlines())
            if path.endswith((".py", ".hpp", ".cpp", ".h")) and not path.startswith(SKIP) and under(path, only)]


# The commits `rev` (the working tree when None) has and `since` lacks, the uncommitted edits' all-zero sha
# included, and the files they changed. Raises ValueError for a `since` git doesn't know.
def changes_since(root: Path, rev: str | None, since: str) -> tuple[set[str], set[str]]:
    listing = git(root, "rev-list", f"{since}..{rev or 'HEAD'}")
    files = git(root, "diff", "--name-only", since, *([rev] if rev else []), "--")
    if listing is None or files is None:
        raise ValueError(f"git doesn't know {since!r}")
    return set(listing.split()) | (set() if rev else {"0" * 40}), set(files.splitlines())


# Every drift candidate under `only` at `rev` (the working tree when None), most drifted first: by how many later
# commits touched the body, then how many body lines they changed. With `since`, only those a commit after it
# (changes_since) touched.
def drift(root: Path, rev: str | None, only: list[str], since: str | None = None) -> list[Drift]:
    order = commit_order(root, rev)
    files = drift_files(root, rev, only)
    new = None
    if since:
        new, changed = changes_since(root, rev, since)
        files = [path for path in files if path in changed]
    history = History(root)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            per_file = pool.map(lambda path: file_drift(history, path, rev, order, new), files)
            found = [candidate for candidates in per_file for candidate in candidates]
    finally:
        history.close()
    return sorted(found, key=lambda d: (-len(d.later), -d.lines, d.path, d.line))


# Whether the commit's `staged` files (old and new names of a rename) include what a problem points at: the path (or
# a file under it, or a bare bot file name's file), the module a `module.name` names, or params.hpp for a param.
def points_into(problem: Problem, staged: set[str]) -> bool:
    ref = problem.ref.rstrip("/")
    if problem.kind == "param":
        return any(path.endswith("params.hpp") for path in staged)
    if problem.kind == "name":
        module = ref.split(".", 1)[0]
        return any(path in staged for path in (f"{folder}/{module}.py" for folder in MODULE_DIRS))
    return any(path == ref or path.startswith(ref + "/") or ("/" not in ref and path.endswith("/" + ref))
               for path in staged)


# For the pre-commit hook: an error in a file the commit doesn't touch blocks only when the commit changes what it
# points at (a rename or deletion breaking a doc elsewhere: "broken by this commit"); otherwise it was already on
# main, and it becomes a warning to fix there, so one broken reference on main never stalls every worktree.
def for_commit(problems: list[Problem], staged: set[str]) -> list[Problem]:
    for problem in problems:
        if problem.level != "error" or problem.path in staged:
            continue
        if points_into(problem, staged):
            problem.why = f"{problem.why}; broken by this commit: {problem.path}:{problem.line}"
        else:
            problem.level, problem.why = "warning", f"{problem.why}; already on main: fix on main"
    return problems


# The first `limit` lines of a candidate's comment and definition at `rev` (the working tree when None), numbered.
def definition_text(root: Path, rev: str | None, candidate: Drift, limit: int) -> str:
    if rev:
        source = git(root, "show", f"{rev}:{candidate.path}") or ""
    else:
        source = (root / candidate.path).read_text() if (root / candidate.path).is_file() else ""
    lines = source.splitlines()[candidate.start - 1:candidate.end]
    shown = [f"{candidate.start + i:5}  {line}" for i, line in enumerate(lines[:limit])]
    if len(lines) > limit:
        shown.append(f"       ... {len(lines) - limit} more lines")
    return "\n".join(shown)


def main_drift(args: argparse.Namespace) -> int:
    try:
        found = drift(args.root.resolve(), args.rev, args.paths, args.since)
    except ValueError as error:
        print(f"check_refs --drift --since: {error}", file=sys.stderr)
        return 2
    shown = found if args.all else found[:args.top]
    if args.json:
        json.dump([asdict(candidate) for candidate in shown], sys.stdout, indent=1)
        print()
    else:
        for candidate in shown:
            print(candidate.text())
            if args.show:
                print(definition_text(args.root.resolve(), args.rev, candidate, args.show) + "\n")
        if len(shown) < len(found):
            print(f"... {len(found) - len(shown)} more (--all)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", help="only files under these repo-relative paths")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--params", type=Path, help="params.hpp to check every file against")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--hook", action="store_true", help="errors outside the staged files warn only")
    parser.add_argument("--drift", action="store_true", help="list comments whose definition changed after them")
    parser.add_argument("--rev", help="with --drift: a commit to read instead of the working tree")
    parser.add_argument("--since", help="with --drift: only definitions changed in commits after this one")
    parser.add_argument("--top", type=int, default=30, help="with --drift: how many to list (default 30)")
    parser.add_argument("--all", action="store_true", help="with --drift: list every candidate")
    parser.add_argument("--show", type=int, default=0, help="with --drift: print each one's first N lines")
    args = parser.parse_args()
    if args.drift:
        return main_drift(args)
    ctx = load_context(args.root.resolve(), args.params)
    problems = [problem for path in scanned(ctx, args.paths) for problem in check_file(ctx, path)]
    if args.hook:
        staged = git(ctx.root, "diff", "--cached", "--name-only", "--no-renames") or ""  # a rename's old name too
        problems = for_commit(problems, set(staged.splitlines()))
    if args.json:
        json.dump([asdict(problem) for problem in problems], sys.stdout, indent=1)
        print()
    else:
        for problem in problems:
            print(problem.text())
    return 1 if any(problem.level == "error" for problem in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
