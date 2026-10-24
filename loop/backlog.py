"""Daily backlog audit (sig, 2026-09-29 ~16:20: "do we do regular audits of things to close that we've already done?
lets do it once a day? in the loop"; MISSES 93). Every maintenance.BACKLOG_EVERY while a Claude loop's upkeep runs
(off under a native session, like the riser study), a confined read-only worker (loop/reports.py: Read, Grep, Glob and
Write in its own folder, no shell or network) reads the day's slice of waiting ideas with loop/BACKLOG.md's brief and
finds the ideas to close (already live in the champion, covered by a kept result, reversed by a measurement), the
duplicates and complements to merge, the dead or wrong `after`s and the notes worth adding. It writes report.md
(B1, B2, ...: loop/decisions.py) and edits.jsonl; the trusted side (`done`, this module, reading both only through
meta.worker_file) applies the edits whose citations it checks and lists each idea it changed, and each closure or
merge it only proposes, in applied/<time>-backlog.md for the orchestrator's decisions.

The slice (`day_slice`): the waiting ideas (to do, followup, being tried) in priority order, P1 first, then by id,
from where the last applied run stopped (`cursor`, wrapping to P1 after the last), until SLICE_CHARS of their rows
(half that, `budget`, after a slice failed twice), led by any waiting P1 idea the rotation already passed without
covering (`covered`: one raised or added since); P1 rows carry every history note, the others their last LAST_NOTES,
each cut at NOTE_CHARS, as the one-off passes of 29 Sep read them. So the whole backlog is covered each day it fits,
and over a few days' rotation when it doesn't (29 Sep: 284 waiting ideas, 1.05 MB in full, P1 0.58 MB). The worker
also gets every waiting idea in full (waiting.jsonl, for lookups), backlog.md (meta.waiting_ideas), results.md (one
line a result), the review's sibling clusters (meta.sibling_lines), and reads the champion checkout
(<loop home>/champion, whose commit the record keeps as `commit`: the tree its "live" citations are checked in),
runs/ and the repo.

The edit list, one JSON object a line, each naming its report item (loop/BACKLOG.md has the worked forms): `merge`
(into, note), `close` (reason, evidence), `after` (after, note), `note` (note), `priority` (priority, note); any may
add a `note`, kept on the idea's history. `parse` refuses the whole list, nothing applied, when a line doesn't parse
or names an action or field it can't, or one idea gets two status edits; `judge` then takes each edit alone:
- skipped: its idea, or a merge's target, is being tried, or is no longer waiting (the worker's snapshot moved);
- proposed (listed in applied/ for the orchestrator, never applied): a close whose citation `close_fault` can't
  check (`live`: a bot/<file>:<line> in that commit's tree with an identifier the evidence quotes within NEAR lines,
  and no param the evidence names at 0 or false there, I211's dead lever; `kept`: a kept experiment of this idea, one
  merged into it, or one its text names; `reversed`: a sample size (n = N, N of M, over N games) or a chance at or
  below chance.TRIGGER_P, in evidence naming no experiment that wasn't kept, since a discarded result rejects one
  implementation, not the idea: CLAUDE.md), any close of a priority-1 idea, and closes and merges past the run's cap
  (`cap`);
- refused (alerted; the others still apply): an edit citing a B-id report.md lacks, or on an idea outside the day's
  slice (a merge's target may sit outside it), a priority that lowers a priority-1 idea (sig's rule, M70) or is
  outside 1-5, an after naming an idea not in the backlog or dropping one still waiting (only one no longer waiting,
  or re-pointed to its merge target), a merge into itself, into an idea not to do or that the list merges or closes
  too, or between two ideas one of which waits on the other (a prerequisite goes alone first: the combine policy),
  a status edit on a followup (notes only: its retest takes merges, meta.twin_target), and, in `apply`, a merge or
  after that would leave any idea waiting on itself (meta.waits_on, through merges) and an edit on an idea the
  review's validator names (meta.backlog_fault_ids).
The edited backlog passes meta.valid_backlog (the review's validator) before it is written, under the backlog's lock,
through meta.ideas_text. Merges follow meta.apply_review's: status "merged", "merged_into", meta.absorb for the kept
idea's priority and afters, and the merge note on the kept idea's history, which its coder reads beside the merged
idea's text (driver.idea_text). Every note reads "backlog <run> (B#): ..." (driver.note_source "backlog": evidence,
never a correction).

Undo: every changed idea's row before the run is saved first in backlog-reports/<report>.before.jsonl, and
`revert` puts back each one's status, priority, rank, after, evaluation and merged_into (history kept, with a note):

    .venv/bin/python loop/backlog.py revert ~/.bc-loop/backlog-reports/<report>.before.jsonl

Records and sizes: <loop home>/backlog.json (fixed keys: the pending folder, pid and failures, `first_seen`,
`started_at`, `applied_at` (the last run whose edit list was applied), `cursor`, `covered` (the rotation's ids so far,
at most the backlog's), `slice_failures`, `budget`, the pending slice's `next_cursor`, `next_covered`, `slice_ids` and
`commit`, at most 10 abandoned folders); backlog/ one folder a run (about 2 MB of copies) and worker-io/backlog/ its
prompt and output, each kept to the newest KEEP_FOLDERS (`prune`, which first keeps each run's notes.md and
session.json, cut at KEPT_BYTES, in backlog-reports/workers/); backlog-reports/ a run's report, its edit list, the
rows it changed and what the loop did with each edit (under 1 MB a run, most of it the changed P1 rows, one run a day:
under health.BACKLOG_REPORTS_BYTES by the contest's end, 18 Oct, kept for the decisions); applied/ one file a run
(decisions.applied). health.backlog_audit checks the first firing, a run pending too long or abandoned, no applied run
for health.BACKLOG_STALE, and those bounds.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import chance
import decisions
import maintenance
import meta
import reports

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "loop/BACKLOG.md"
SOURCE = "backlog"  # the applied/ listing's source (applied/<time>-backlog.md)
SLICE_CHARS = 500_000  # a day's slice rows: the 29 Sep passes each read 0.45-0.58 MB of rows and finished
NOTE_CHARS = 1500  # a P2+ history note is cut here, as the 29 Sep P2 pass read them
LAST_NOTES = 6  # a P2+ idea's newest notes kept (P1 keeps all)
TIMEOUT = 4 * 3600  # a worker still running this long is stopped and counts as a failed launch (reports.step)
KEEP_FOLDERS = 7  # backlog/ and worker-io/backlog/ runs kept: a week's, regenerable copies and the worker's output
KEPT_BYTES = 100_000  # a pruned run's notes.md or session.json kept up to this (the audits' session.json: under 30 KB)
MAX_EDITS = 400  # more lines than this is no audit of a ~300-idea slice: the list is refused
NEAR = 5  # a "live" citation's quoted identifier within this many lines of it: the call or the branch head around it
FIELDS = ("id", "category", "priority", "status", "kind", "title", "hypothesis", "after", "source", "evaluation",
          "history")
# Each action's fields beside "item" and "id" (and an optional "note" on any).
ACTIONS = {"merge": ("into", "note"), "close": ("reason", "evidence"), "after": ("after", "note"), "note": ("note",),
           "priority": ("priority", "note")}
CLOSE_REASONS = ("live", "kept", "reversed")
STATUS_EDITS = ("merge", "close")
IDEA_ID = re.compile(r"I\d+")
IDEA_REF = re.compile(r"\bI\d+\b")
ITEM_ID = re.compile(r"B\d+")
SOURCE_LINE = re.compile(r"\b(?:bot/)?([A-Za-z_][\w/.-]*\.(?:hpp|cpp|h))\s*:\s*(\d+)(?:\s*[-–]\s*(\d+))?")
EXPERIMENT = re.compile(r"\bE\d{4,}\b")
# An identifier in evidence: `backticked`, camelCase (P.kingSuccession, badRammer) or snake_case; plain words aren't.
CODE_WORD = re.compile(r"`(\w+)`|\b([a-z]+[A-Z]\w*|[A-Za-z]+_\w+)\b")
PARAM = re.compile(r"^\s*(?:bool|int|double|float)\s+(\w+)\s*=\s*([^;]+);", re.M)
OFF = ("0", "false", "0.0", "0.0f", "0.f")
# A measurement's sample size: n = N (or "n 733"), N of M, or a count over/across/from/of N things counted (up to
# three words between: "over 441 result-blind online games"). "It lost 2 games" and "seen on 3 maps" give none.
SAMPLE = re.compile(r"\bn\s*=?\s*\d|\b\d[\d,]*\s+(?:of|out of)\s+\d"
                    r"|\b(?:over|across|from|of)\s+\d[\d,]*\s+(?:[\w-]+\s+){0,3}?(?:games|pairs|trials|battles|rows|"
                    r"turns|replays|firings|sightings|rams|deaths|maps|dragons|cells|decisions)\b", re.I)
CHANCE = re.compile(r"\bp\s*(?:=|<=|<|≤)\s*(\d*\.?\d+(?:e-?\d+)?)", re.I)


def reads(state: Path) -> tuple[Path, ...]:
    return REPO, state / "champion", state / "runs"


# An idea's place in the rotation: priority (a malformed one last), then id number.
def order(idea: dict) -> tuple[int, int]:
    priority = idea.get("priority")
    return (priority if type(priority) is int else 9), meta.idea_number(idea.get("id")) or 0


# An idea's slice.jsonl row: FIELDS, P1 with every history note, the rest with their last LAST_NOTES cut at NOTE_CHARS.
def slice_row(idea: dict) -> dict:
    row = {k: idea[k] for k in FIELDS if k in idea}
    notes = [str(note) for note in idea.get("history") or []]
    if idea.get("priority") != 1:
        notes = [note if len(note) <= NOTE_CHARS else note[:NOTE_CHARS] + " [cut]" for note in notes[-LAST_NOTES:]]
    row["history"] = notes
    return row


# The day's slice of `ideas` (the live backlog): its waiting ideas in `order` from the first after `cursor` (the last
# run's last idea, or the start), wrapping, until `budget` characters of rows (at least one row), led by each waiting
# P1 idea before `cursor` that `covered` (the ids this rotation audited) lacks: one raised or added after the rotation
# passed its place. Returns the rows in `order` (P1 first), the cursor the next run starts from (None when the slice
# holds every waiting idea) and the rotation's ids covered after it.
def day_slice(ideas: list[dict], cursor: list | None, budget: int = SLICE_CHARS, covered: list | tuple = ()
              ) -> tuple[list[dict], list | None, list[str]]:
    waiting = sorted((i for i in ideas if meta.is_waiting(i)), key=order)
    start = next((n for n, i in enumerate(waiting) if cursor and list(order(i)) > list(cursor)), 0)
    seen = set(covered) if start else set()  # starting at 0 starts a new rotation
    behind = [i for i in waiting[:start] if i.get("priority") == 1 and i["id"] not in seen]
    rows, used, last, taken = [], 0, (list(cursor) if start else None), set()
    wrap = len(behind) + len(waiting) - start
    for n, idea in enumerate(behind + waiting[start:] + waiting[:start]):
        if start and n == wrap:  # a new rotation begins: of today's rows, only the P1s it starts with count for it
            seen = taken & {i["id"] for i in behind}
        if idea["id"] in taken:
            continue
        row = slice_row(idea)
        size = len(json.dumps(row)) + 1
        if rows and used + size > budget:
            break
        rows.append(row)
        taken.add(idea["id"])
        seen.add(idea["id"])
        used += size
        if n >= len(behind):
            last = list(order(idea))
    done = len(rows) == len(waiting)
    return sorted(rows, key=order), (None if done else last), sorted(seen)


# Every experiment's latest row in results.jsonl (a re-judged id's later row wins, meta.latest_rows), compact:
# {id: {idea, title, verdict, elo, pairs, reason}}. Read line by line; a line being appended now doesn't parse and is
# skipped.
def results(path: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not path.exists():
        return found
    with path.open() as rows:
        for line in rows:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            idea = row.get("idea") if isinstance(row.get("idea"), dict) else {}
            sprt = row.get("sprt") if isinstance(row.get("sprt"), dict) else {}
            found[row["id"]] = {"idea": idea.get("id"), "title": meta.flat(idea.get("title") or "")[:160],
                                "verdict": row.get("verdict"), "elo": sprt.get("elo"), "pairs": sprt.get("pairs"),
                                "reason": meta.flat(row.get("reason") or "")[:300]}
    return found


def results_text(found: dict[str, dict]) -> str:
    lines = []
    for exp, r in found.items():
        score = f" {r['elo']:+.1f}/{r['pairs']}" if isinstance(r["elo"], (int, float)) else ""
        lines.append(f"- {exp} ({r['idea']}) {r['verdict']}{score}: {r['title']} ({r['reason']})")
    return "# Results, one line an experiment (latest row per id)\n\n" + "\n".join(lines) + "\n"


# A lookup of the champion's source at `commit` (the tree the worker read, `start` records it): bot/<name>'s lines,
# None when that tree has no such file or `commit` is unknown. Cached per call of this function.
def champion_source(commit: str | None) -> Callable[[str], list[str] | None]:
    cache: dict[str, list[str] | None] = {}

    def source(name: str) -> list[str] | None:
        if name not in cache:
            cache[name] = None
            if commit and re.fullmatch(r"[0-9a-f]{7,40}", commit):
                done = subprocess.run(["git", "show", f"{commit}:bot/{name.removeprefix('bot/')}"], cwd=REPO,
                                      capture_output=True, text=True)
                cache[name] = done.stdout.splitlines() if done.returncode == 0 else None
        return cache[name]
    return source


# The commit the champion checkout (<state>/champion, which the worker reads) is at, or "unknown". rev-parse runs no
# hooks and reads no fsmonitor.
def champion_commit(state: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=state / "champion", capture_output=True, text=True) \
        if (state / "champion").is_dir() else None
    return done.stdout.strip() if done and done.returncode == 0 and done.stdout.strip() else "unknown"


def code_words(text: str) -> set[str]:
    return {a or b for a, b in CODE_WORD.findall(text)}


# The params the champion's bot/params.hpp sets to 0 or false: a line behind one is off.
def params_off(source: Callable[[str], list[str] | None]) -> set[str]:
    text = "\n".join(source("params.hpp") or [])
    return {name for name, value in PARAM.findall(text) if value.strip() in OFF}


# Whether `text` states a measurement's sample size or a chance at or below chance.TRIGGER_P.
def measured(text: str) -> bool:
    return bool(SAMPLE.search(text)) or any(float(p) <= chance.TRIGGER_P for p in CHANCE.findall(text))


# Why close edit `edit` of `idea` lacks a citation this side can check, or None (the module docstring's three kinds).
# `by_id` is the live backlog, `found` the results, `source` the champion's (champion_source).
def close_fault(edit: dict, idea: dict, by_id: dict[str, dict], found: dict[str, dict],
                source: Callable[[str], list[str] | None]) -> str | None:
    evidence = edit["evidence"]
    if edit["reason"] == "live":
        words = code_words(evidence)
        if off := sorted(words & params_off(source)):
            return (f"its evidence names {', '.join('P.' + w for w in off)}, 0 or false in the champion: code behind "
                    "it is off")
        cited = SOURCE_LINE.findall(evidence)
        for name, first, last in cited:
            lines, low, high = source(name), int(first), int(last or first)
            near = (lines or [])[max(0, low - 1 - NEAR):high + NEAR] if lines and 1 <= low <= high <= len(lines) else []
            if any(re.search(rf"\b{re.escape(word)}\b", line) for word in words for line in near):
                return None
        places = ", ".join(f"{f}:{n}" for f, n, _ in cited)
        return (f"closed as live without a bot/<file>:<line> in the champion's tree with an identifier the evidence "
                f"quotes within {NEAR} lines" + (f" (cited {places})" if cited else ""))
    if edit["reason"] == "kept":
        text = f"{idea.get('title', '')} {idea.get('hypothesis', '')}"
        merged = (i["id"] for i in by_id.values() if i.get("status") == "merged" and i.get("merged_into") == idea["id"])
        ideas = {idea["id"], *IDEA_REF.findall(text), *merged}
        named = set(EXPERIMENT.findall(text))
        if not any(found.get(e, {}).get("verdict") == "kept" and (e in named or found[e].get("idea") in ideas)
                   for e in EXPERIMENT.findall(evidence)):
            return ("closed as covered by a kept result without an experiment kept for this idea, one merged into it, "
                    "or one its text names")
        return None
    if unkept := sorted({e for e in EXPERIMENT.findall(evidence) if found.get(e, {}).get("verdict") != "kept"}):
        return (f"closed as reversed on evidence naming {', '.join(unkept)}, not kept (a discarded or unproven result "
                "rejects one implementation, not the idea)")
    if not measured(evidence):
        return (f"closed as reversed with no sample size (n = N, N of M, over N games) or chance at or below "
                f"{chance.TRIGGER_P}")
    return None


# The edit list's rows, or ValueError naming the first fault that refuses the whole list: a line that isn't a JSON
# object, an unknown action, a missing, unknown or ill-typed field, two status edits (or two afters, or two priorities)
# for one idea, or more than MAX_EDITS lines.
def parse(text: str) -> list[dict]:
    edits, seen = [], set()
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            edit = json.loads(line)
        except ValueError:
            raise ValueError(f"line {n} is not JSON") from None
        if not isinstance(edit, dict):
            raise ValueError(f"line {n} is not a JSON object")
        action = edit.get("action")
        if action not in ACTIONS:
            raise ValueError(f"line {n}: action {action!r} is not one of {', '.join(ACTIONS)}")
        need = {"item", "id", "action", *ACTIONS[action]}
        if set(edit) != need and set(edit) != need | {"note"}:
            raise ValueError(f"line {n}: a {action} takes {', '.join(sorted(need))}, not {', '.join(sorted(edit))}")
        texts = [k for k in ("note", "evidence") if k in edit]
        if not (isinstance(edit["item"], str) and ITEM_ID.fullmatch(edit["item"])
                and isinstance(edit["id"], str) and IDEA_ID.fullmatch(edit["id"])
                and all(isinstance(edit[k], str) and edit[k].strip() for k in texts)
                and (action != "merge" or isinstance(edit["into"], str) and IDEA_ID.fullmatch(edit["into"]))
                and (action != "close" or edit["reason"] in CLOSE_REASONS)
                and (action != "after" or isinstance(edit["after"], list)
                     and all(isinstance(a, str) and IDEA_ID.fullmatch(a) for a in edit["after"]))
                and (action != "priority" or type(edit["priority"]) is int)):
            raise ValueError(f"line {n}: a field of its {action} is missing or ill-formed (item B#, ids I#, "
                             f"reason one of {', '.join(CLOSE_REASONS)}, non-empty note and evidence)")
        key = (edit["id"], "status" if action in STATUS_EDITS else action)
        if action != "note" and key in seen:
            raise ValueError(f"line {n}: a second {key[1]} edit for {edit['id']}")
        seen.add(key)
        edits.append(edit)
    if len(edits) > MAX_EDITS:
        raise ValueError(f"{len(edits)} edits, over {MAX_EDITS}")
    return edits


# The closures and merges one run applies: a quarter of its slice's ideas (at least one). The 29 Sep passes, the
# whole backlog read by hand, found about 84 stale or duplicate ideas of about 370 (23%): a run past that is closing
# more than any full pass found, so the rest go to the orchestrator as proposals.
def cap(slice_ids: list[str] | set[str]) -> int:
    return max(1, len(slice_ids) // 4)


# The entries an `after` edit drops that still wait: an entry may go only when it resolves (meta.merge_target) to an
# idea no longer waiting, or when the new list names that idea (re-pointed to what it merged into).
def dropped_waiting(old: list, new: list, by_id: dict[str, dict]) -> list[str]:
    gone = []
    for entry in old:
        target = meta.merge_target(entry, by_id)
        if entry not in new and target not in new and meta.is_waiting(by_id.get(target, {})):
            gone.append(entry)
    return gone


# Splits parsed `edits` against `live` (the backlog now) into those to apply, skipped, refused and proposed, each of
# the last three with why (the module docstring's rules). `items` are report.md's B-ids, `slice_ids` the day's slice.
def judge(edits: list[dict], live: list[dict], found: dict[str, dict], source: Callable[[str], list[str] | None],
          items: list[str], slice_ids: list[str]
          ) -> tuple[list[dict], list[tuple[dict, str]], list[tuple[dict, str]], list[tuple[dict, str]]]:
    by_id, today = {i["id"]: i for i in live}, set(slice_ids)
    ending = {e["id"] for e in edits if e["action"] in STATUS_EDITS}
    plan, skipped, refused, proposed = [], [], [], []
    for edit in edits:
        idea, action = by_id.get(edit["id"]), edit["action"]
        status = str((idea or {}).get("status", ""))
        if edit["item"] not in items:
            refused.append((edit, f"it cites {edit['item']}, not an item of report.md"))
        elif idea is None:
            refused.append((edit, f"{edit['id']} is not in the backlog"))
        elif status.startswith("trying"):
            skipped.append((edit, f"{edit['id']} is being tried ({status})"))
        elif not meta.is_waiting(idea):
            skipped.append((edit, f"{edit['id']} is no longer waiting ({status})"))
        elif edit["id"] not in today:
            refused.append((edit, f"{edit['id']} is not in today's slice"))
        elif action != "note" and status != "todo":
            refused.append((edit, f"{edit['id']} is a {status}: it takes notes only (its retest takes the rest)"))
        elif action == "merge":
            target = by_id.get(edit["into"])
            into = str((target or {}).get("status", ""))
            if into.startswith("trying"):
                skipped.append((edit, f"its target {edit['into']} is being tried ({into})"))
            elif target is None or edit["into"] == edit["id"] or into != "todo" or edit["into"] in ending:
                refused.append((edit, f"its target {edit['into']} is " + (
                    "not in the backlog" if target is None else "itself" if edit["into"] == edit["id"] else
                    f"{into}, not to do" if into != "todo" else "merged or closed by this list too")))
            elif meta.waits_on(edit["id"], edit["into"], by_id) or meta.waits_on(edit["into"], edit["id"], by_id):
                refused.append((edit, f"one of {edit['id']} and {edit['into']} waits on the other: a prerequisite "
                                      "goes alone first (the combine policy)"))
            else:
                plan.append(edit)
        elif action == "close" and (why := close_fault(edit, idea, by_id, found, source)):
            proposed.append((edit, why))
        elif action == "close" and idea.get("priority") == 1:
            proposed.append((edit, "a priority-1 idea: the orchestrator closes those"))
        elif action == "after" and (missing := [a for a in edit["after"] if a not in by_id]):
            refused.append((edit, f"its after names {', '.join(missing)}, not in the backlog"))
        elif action == "after" and (gone := dropped_waiting(idea.get("after") or [], edit["after"], by_id)):
            refused.append((edit, f"its after drops {', '.join(gone)}, still waiting (only one no longer waiting "
                                  "goes, or one re-pointed to what it merged into)"))
        elif action == "priority" and not 1 <= edit["priority"] <= 5:
            refused.append((edit, f"priority {edit['priority']} is outside 1-5"))
        elif action == "priority" and idea.get("priority") == 1 and edit["priority"] > 1:
            refused.append((edit, f"it lowers priority-1 {edit['id']} (sig's rule: no priority-1 idea is lowered)"))
        else:
            plan.append(edit)
    ending_plan = [e for e in plan if e["action"] in STATUS_EDITS]
    for edit in ending_plan[cap(slice_ids):]:
        plan.remove(edit)
        proposed.append((edit, f"past today's cap of {cap(slice_ids)} closures and merges (loop/backlog.py cap)"))
    return plan, skipped, refused, proposed


# `live` with `plan`'s edits made (a copy): notes, afters, priorities and closures, then merges, whose meta.absorb
# runs once every other edit is in, as apply_review's do.
def edited(live: list[dict], plan: list[dict], run: str) -> list[dict]:
    rows = json.loads(json.dumps(live))
    by_id = {i["id"]: i for i in rows}

    def note(idea: dict, edit: dict, text: str) -> None:
        idea.setdefault("history", []).append(f"backlog {run} ({edit['item']}): {meta.flat(text)}")
    for edit in plan:
        idea = by_id[edit["id"]]
        if edit["action"] == "note":
            note(idea, edit, edit["note"])
        elif edit["action"] == "after":
            note(idea, edit, f"after {idea.get('after') or []} -> {edit['after']}: {edit['note']}")
            idea["after"] = edit["after"]
        elif edit["action"] == "priority":
            note(idea, edit, f"priority {idea.get('priority')} -> {edit['priority']}: {edit['note']}")
            idea["priority"] = edit["priority"]
        elif edit["action"] == "close":
            note(idea, edit, f"dropped, {edit['reason']}: {edit['evidence']}"
                 + (f" Note: {edit['note']}" if edit.get("note") else ""))
            idea["status"] = "dropped"
    merges = [e for e in plan if e["action"] == "merge"]
    for edit in merges:
        idea, kept = by_id[edit["id"]], by_id[edit["into"]]
        idea.update(status="merged", merged_into=edit["into"])
        note(idea, edit, f"merged into {edit['into']}" + (f": {edit['note']}" if edit.get("note") else ""))
        note(kept, edit, f"{edit['id']} merged into this: {edit['note']}")
    for edit in merges:
        meta.absorb(by_id[edit["into"]], by_id[edit["id"]], by_id)
    return rows


# The waiting ideas in `rows` that wait on themselves (meta.waits_on, through merges): never claimed.
def self_waiting(rows: list[dict]) -> set[str]:
    by_id = {r["id"]: r for r in rows}
    return {r["id"] for r in rows if meta.is_waiting(r) and meta.waits_on(r["id"], r["id"], by_id)}


# What an unapplied edit would have done, for its applied/ proposal line.
def described(edit: dict) -> str:
    if edit["action"] == "close":
        return f"drop as {edit['reason']}: {edit['evidence']}"
    if edit["action"] == "merge":
        return f"merge into {edit['into']}: {edit.get('note', '')}"
    return f"{edit['action']}: {edit.get('note', '')}"


# Applies edit list `text` from run `run` to the live backlog: parse (ValueError refuses it whole), judge, then, one
# at a time, each merge or after that leaves no new idea waiting on itself, then meta.valid_backlog, refusing the edits
# on the ideas meta.backlog_fault_ids names until it passes (ValueError when it names none), and the write, all under
# the backlog's lock. Every changed idea's row before is saved first to `before` (revert's input; default
# backlog-reports/<run>.before.jsonl). `report` is report.md (its B-ids), `slice_ids` the day's slice, `source` the
# champion's (champion_source). Lists each idea it merged, dropped, re-pointed or re-prioritised, and each proposal,
# in applied/. Returns {applied, skipped, refused, proposed (edits with why), listing (the applied/ file or None),
# before (the saved rows' file or None)}.
def apply(state: Path, text: str, report: str, run: str, slice_ids: list[str],
          source: Callable[[str], list[str] | None], found: dict[str, dict] | None = None,
          before: Path | None = None) -> dict:
    edits = parse(text)
    found = results(state / "results.jsonl") if found is None else found
    before = before or state / "backlog-reports" / f"{run}.before.jsonl"
    with meta.locked(state / "ideas.jsonl"):
        live = meta.live_ideas(state)
        plan, skipped, refused, proposed = judge(edits, live, found, source, decisions.items(report, "B"), slice_ids)
        looped, kept = self_waiting(live), []
        for edit in plan:
            loops = edit["action"] in ("merge", "after")
            if loops and (new := self_waiting(edited(live, [*kept, edit], run)) - looped):
                refused.append((edit, f"it would leave {', '.join(sorted(new))} waiting on itself"))
            else:
                kept.append(edit)
        plan = kept
        while True:
            rows = edited(live, plan, run)
            after = "\n".join(json.dumps(row) for row in rows)
            if meta.valid_backlog(live, after) is not None:
                break
            bad = set(meta.backlog_fault_ids(live, after) or [])
            if not (named := [e for e in plan if e["id"] in bad or e.get("into") in bad]):
                raise ValueError(f"the edited backlog fails the review's validator: {meta.backlog_faults(live, after)}")
            refused += [(e, "the review's validator refuses what it makes of the idea (meta.backlog_faults)")
                        for e in named]
            plan = [e for e in plan if e not in named]
        was = {i["id"]: {k: json.dumps(i.get(k)) for k in meta.EDITED} for i in live}
        old = {i["id"]: i for i in live}
        touched = [old[r["id"]] for r in rows if r["id"] in old and old[r["id"]] != r]
        if touched:
            reports.publish(before, "".join(json.dumps(row) + "\n" for row in touched))
        temporary = state / "ideas.jsonl.tmp"
        temporary.write_text(meta.ideas_text(rows))
        temporary.replace(state / "ideas.jsonl")
    changed = meta.review_changes(rows, was)  # merges, drops and edited fields; notes alone go by the report's B-ids
    by_id = {r["id"]: r for r in rows}
    changed += [(by_id[e["id"]], meta.flat(f"proposed, not applied: {described(e)} ({why})")[:600])
                for e, why in proposed]
    listing = None
    try:
        listing = decisions.applied(state, SOURCE, [], changed=changed)
    except OSError as error:
        meta.alert(state, f"could not list the backlog audit's edits for a decision: {error}; decide on "
                          f"{sorted(i['id'] for i, _ in changed)} by hand")
    return {"applied": plan, "skipped": skipped, "refused": refused, "proposed": proposed, "listing": listing,
            "before": before if touched else None}


# Puts back each idea in `saved` (an apply's before.jsonl) as it was before that run: its status, priority, rank,
# after, evaluation and merged_into, keeping its history with a note that says so; one being tried now is left, since
# its coder has it. Returns (the ids reverted, [(id, why left)]). Usage (the module docstring):
#     .venv/bin/python loop/backlog.py revert ~/.bc-loop/backlog-reports/<report>.before.jsonl
def revert(state: Path, saved: Path) -> tuple[list[str], list[tuple[str, str]]]:
    rows = [json.loads(line) for line in saved.read_text().splitlines() if line.strip()]
    done, left = [], []
    with meta.locked(state / "ideas.jsonl"):
        live = meta.live_ideas(state)
        by_id = {i["id"]: i for i in live}
        for row in rows:
            idea = by_id.get(row["id"])
            if idea is None or str(idea.get("status", "")).startswith("trying"):
                left.append((row["id"], "not in the backlog" if idea is None else f"being tried ({idea['status']})"))
                continue
            for key in (*meta.EDITED, "merged_into"):
                if key in row:
                    idea[key] = row[key]
                else:
                    idea.pop(key, None)
            idea.setdefault("history", []).append(f"backlog revert {time.strftime('%Y-%m-%d %H:%M')}: put back as "
                                                  f"before {saved.name}")
            done.append(row["id"])
        temporary = state / "ideas.jsonl.tmp"
        temporary.write_text(meta.ideas_text(live))
        temporary.replace(state / "ideas.jsonl")
    return done, left


def stamp() -> str:
    return time.strftime("%Y-%m-%d-%H%M")


# The first free backlog-reports/<stamp>.md (then -2, -3 ...).
def report_path(state: Path) -> Path:
    folder, name, n = state / "backlog-reports", stamp(), 2
    path = folder / f"{name}.md"
    while path.exists():
        path, n = folder / f"{name}-{n}.md", n + 1
    return path


def outcome_text(outcome: dict | None, fault: str | None) -> str:
    lines = ["", "## What the loop did with the edit list (loop/backlog.py)", ""]
    if fault:
        return "\n".join([*lines, f"Refused whole, nothing applied: {fault}", ""])
    for label, rows in (("applied", [(e, "") for e in outcome["applied"]]), ("skipped", outcome["skipped"]),
                        ("refused", outcome["refused"]), ("proposed to the orchestrator", outcome["proposed"])):
        lines += [f"- {e['id']} ({e['item']}, {e['action']}) {label}" + (f": {why}" if why else "") for e, why in rows]
    if outcome["before"]:
        lines += ["", f"Undo: .venv/bin/python loop/backlog.py revert {outcome['before']}"]
    return "\n".join(lines) + "\n"


# The record fields after a run's slice failed (a refused edit list, or an abandoned worker), and what happens next:
# the first time the same slice comes round again; the second in a row moves past it (`next_cursor`) with half the
# budget, so a slice too big for the worker's cap can't hold the rotation for good; its uncovered P1s lead the next.
def slice_failed(record: dict) -> tuple[dict, str]:
    failures = record.get("slice_failures", 0) + 1
    if failures < 2:
        return {"slice_failures": failures}, "the same slice comes round at the next daily run"
    budget = max(SLICE_CHARS // 8, (record.get("budget") or SLICE_CHARS) // 2)
    return ({"slice_failures": 0, "cursor": record.get("next_cursor"), "budget": budget},
            f"its second failure in a row: the next run moves past it with a {budget}-character budget, its P1 ideas "
            "first again")


# reports.Kind's `done`: saves the report with what the loop did with its edit list, and applies the list (`apply`).
# A refused or missing list keeps the report for the orchestrator and leaves `applied_at`; the cursor moves as
# slice_failed says.
def done(state: Path, record: dict, report: str) -> tuple[str, dict]:
    work, run, path = Path(record["pending"]), stamp(), report_path(state)
    outcome, fault, text = None, None, ""
    try:
        text = meta.worker_file(work / "edits.jsonl")
        outcome = apply(state, text, report, run, record.get("slice_ids") or [],
                        champion_source(record.get("commit")), before=path.with_suffix(".before.jsonl"))
    except FileNotFoundError:
        fault = "the worker wrote no edits.jsonl"
    except (OSError, ValueError) as error:
        fault = f"{type(error).__name__}: {error}"
    reports.publish(path, report.rstrip() + "\n" + outcome_text(outcome, fault))
    reports.publish(path.with_suffix(".edits.jsonl"), text)
    decide = f"decide each B-id in {path}"
    if fault:
        fields, then = slice_failed(record)
        return (f"BACKLOG refused: {fault}; nothing applied; {then}. {decide} and apply what holds by hand "
                f"(driver.mark_idea; the list is {path.with_suffix('.edits.jsonl')})", {"report": str(path), **fields})
    counts = {a: sum(e["action"] == a for e in outcome["applied"]) for a in ACTIONS}
    refused = "; ".join(f"{e['id']} ({e['item']}): {why}" for e, why in outcome["refused"][:5])
    line = (f"BACKLOG applied: {len(outcome['applied'])} edits ({', '.join(f'{n} {a}' for a, n in counts.items() if n)}"
            f"), skipped {len(outcome['skipped'])}, proposed {len(outcome['proposed'])}, refused "
            f"{len(outcome['refused'])}" + (f" ({refused}{'; ...' if len(outcome['refused']) > 5 else ''})" if refused
                                             else "")
            + f"; {decide}" + (f" and each id in {outcome['listing']}" if outcome["listing"] else "")
            + " (loop/decisions.py)" + (f"; undo: loop/backlog.py revert {outcome['before']}" if outcome["before"]
                                        else ""))
    return line, {"report": str(path), "applied_at": time.time(), "cursor": record.get("next_cursor"),
                  "covered": record.get("next_covered") or [], "slice_failures": 0, "budget": None}


def gave_up(state: Path, record: dict, why: str) -> tuple[str, dict]:
    failures = "; ".join(record.get("failures", []))[-400:]
    fields, then = slice_failed(record)
    return f"BACKLOG abandoned {why} ({record['pending']}): {then}; failures: {failures or 'none'}", fields


KIND = reports.Kind("backlog", "medium", reads, done, gave_up,
                    ("first_seen", "started_at", "applied_at", "cursor", "covered", "slice_failures", "budget",
                     "report", "finished_at", "last"), TIMEOUT)


# The run folders `prune` bounds: the worker's folders and their prompts and output.
def run_folders(state: Path) -> tuple[Path, ...]:
    return state / "backlog", state / "worker-io/backlog"


# Keeps run `name`'s notes.md (the worker's running notes) and session.json (its session's result) in
# backlog-reports/workers/, each cut at KEPT_BYTES, before `prune` removes its folders.
def keep_worker_files(state: Path, name: str) -> None:
    folder = state / "backlog-reports/workers"
    for path, kept in ((state / "backlog" / name / "notes.md", f"{name}.notes.md"),
                       (state / "worker-io/backlog" / name / "session.json", f"{name}.session.json")):
        if (folder / kept).exists():
            continue
        try:
            text = meta.worker_file(path)
        except (OSError, ValueError):
            continue
        reports.publish(folder / kept, text[:KEPT_BYTES] + ("\n[cut]\n" if len(text) > KEPT_BYTES else ""))


def prune(state: Path) -> None:
    names = sorted({p.name for folder in run_folders(state) for p in folder.glob("*")
                    if p.is_dir() and not p.is_symlink()})
    for name in names[:-KEEP_FOLDERS]:
        keep_worker_files(state, name)
        for folder in run_folders(state):
            old = folder / name
            if not old.is_dir() or old.is_symlink():
                continue
            try:
                meta.unlock(old)
            except OSError:
                pass
            shutil.rmtree(old, ignore_errors=True)


def prompt(state: Path, commit: str) -> str:
    text = BRIEF.read_text()
    for key, value in {"champion": str(state / "champion"), "commit": commit, "runs": str(state / "runs"),
                       "repo": str(REPO)}.items():
        text = text.replace("{" + key + "}", value)
    return text


def context(rows: list[dict], waiting: int, cursor: list | None, commit: str, clusters: list[str]) -> str:
    counts = {p: sum(r.get("priority") == p for r in rows) for p in sorted({r.get("priority") for r in rows}, key=str)}
    span = f"from {rows[0]['id']} to {rows[-1]['id']}" if rows else "empty"
    return "\n".join([
        "# Backlog audit: this run", "",
        f"Today's slice: {len(rows)} of {waiting} waiting ideas "
        f"({', '.join(f'P{p}: {n}' for p, n in counts.items())}), "
        f"{span} in priority then id order, in slice.jsonl. Edit only these ideas (a merge's target may be any idea "
        f"to do); at most {cap(rows)} closures and merges apply today, the rest go to the orchestrator."
        + (" It is every waiting idea." if cursor is None else
           f" The rest come in the next daily runs, from after P{cursor[0]} I{cursor[1]:03d}."),
        f"Champion: loop/champion at {commit}; its source is the champion checkout named in your brief.",
        *clusters, ""])


# The upkeep tick's step, after the riser study: collects or relaunches the pending run, else opens one when
# maintenance.BACKLOG_EVERY has passed since the last opened. `models` False (a native session) opens none and hands
# a folder a Claude driver left to the orchestrator (reports.hand_over). Returns a line to log, or None.
def tick(state: Path, models: bool = True) -> str | None:
    return reports.tick(state, KIND, (lambda: start(state)) if models else None, launch=models)


def start(state: Path, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    record = reports.read(state, KIND)
    if "first_seen" not in record:  # health.backlog_audit's first-firing check starts here
        record = {**record, "first_seen": now}
        reports.save(state, KIND, record)
    if now - record.get("started_at", 0) < maintenance.BACKLOG_EVERY:
        return None
    ideas = meta.live_ideas(state)
    rows, cursor, covered = day_slice(ideas, record.get("cursor"), record.get("budget") or SLICE_CHARS,
                                      record.get("covered") or [])
    if not rows:
        reports.save(state, KIND, {**record, "started_at": now})
        return None
    commit = champion_commit(state)
    files = {"slice.jsonl": "".join(json.dumps(r) + "\n" for r in rows),
             "waiting.jsonl": meta.waiting_ideas_full(state), "backlog.md": meta.waiting_ideas(state),
             "results.md": results_text(results(state / "results.jsonl")),
             "context.md": context(rows, sum(map(meta.is_waiting, ideas)), cursor, commit, meta.sibling_lines(ideas))}
    work = reports.prepare(state, KIND, prompt(state, commit), files, {
        "started_at": now, "next_cursor": cursor, "next_covered": covered, "slice_ids": [r["id"] for r in rows],
        "commit": commit, "slice": len(rows)})
    prune(state)
    return f"backlog audit prepared: {len(rows)} ideas ({work})"


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "revert":
        sys.exit("usage: loop/backlog.py revert <backlog-reports/...before.jsonl>")
    reverted, kept_as_is = revert(meta.STATE, Path(sys.argv[2]).expanduser())
    print(f"reverted {', '.join(reverted) or 'none'}" + "".join(f"; left {i}: {why}" for i, why in kept_as_is))
