"""When the orchestrator's REFLECT check is due (loop/REFLECT.md, loop/MISSES.md 6 and 19): EVERY_SECONDS after the
last one, and only then (sig, 2026-09-29 17:45: "can reflect only fire once every 12 hours pls", then "actually no 6
hours actually"; a results count no longer fires it). A clock, not the driver's stdout, since that reaches the
orchestrator only when a `--once` driver exits.

`check` is called by the upkeep tick (about each minute inside every driver and native command) and by the driver
after each result. When due it records the REFLECT in `<loop home>/reflect.json`: when it fired (`at`), the window
it names (`since`, when the previous one fired), the result count then (a record only) and the `reason` ("X h since
the last one" or "none recorded yet"), under a lock, so many processes record it once per period.
health.py alerts when it hasn't fired for longer than a period allows.

`tick`, the upkeep's next step, then makes the list (loop/reports.py): the trusted side runs tools/my_messages.py
itself for the messages since the last list (and again with each of CHECKS: --check-times for times typed from
memory, --check-evalline, --check-asks, --check-answers, --check-sources and --check-length), and a confined read-only
worker reads that output, a copy of loop/MISSES.md and the comments whose code changed on main in the same window (`drift_list`, REFLECT.md step 4) with
REFLECT.md's brief, never the transcript. Its list is saved as `<loop home>/reflect/<time>.md` and host-alerts.log
gets `REFLECT list ready: <path>; judge each F-id and record the decisions in decisions.md (loop/REFLECT.md)`. When the worker can't run (my_messages.py fails, two launches fail, or
a native session orchestrates) the line is the by-hand one instead, naming the drift list saved for it (save_drift,
built for every REFLECT):

    REFLECT due (6.0 h since the last one): have a fresh read-only pass (a read-only subagent if you have one, else
    yourself) check `.venv/bin/python tools/my_messages.py --since 2026-09-26T09:40+10:00` and the doc drift list
    <loop home>/reflect/2026-09-26-1240-drift.txt (item (f)) against loop/MISSES.md, with its --check-times output
    for (b) (MISSES 26) and --check-evalline, --check-asks, --check-answers, --check-sources and --check-length, save
    its list as <loop home>/reflect/<time>.md, then judge each F-id (loop/REFLECT.md)

`<loop home>/reflect-list.json` records the worker: its pending folder, pid and failures, and `covered_at`, when the
last REFLECT it handled fired, so a REFLECT that fires while one is running widens the next window instead of
being lost.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import meta
import reports
from meta import alert, locked

EVERY_SECONDS = 6 * 3600
LATE = EVERY_SECONDS + 3600  # health.py: longer than this without a REFLECT means the check itself is failing
REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "loop/REFLECT.md"
MISSES = REPO / "loop/MISSES.md"
MESSAGES_TIMEOUT = 600
HEADER = re.compile(r"# (.+\.jsonl), since ")  # tools/my_messages.py's stderr line naming the transcript it read
DRIFT_TIMEOUT = 600
DRIFT_LINES = 20  # of each drifted comment and its definition, so a period's list fits DRIFT_CAP
DRIFT_CAP = 200_000  # characters of drift.txt; a busy day lists over 100 candidates, about 200 KB (merged 3 h pairs
# of 29 Sep came to 82-142 KB, so a 6 h window fits)

RUN = """
## This run

You run headless for the loop, with Read, Grep, Glob and Write only: no shell, no web, no network. The messages file
is `{work}/messages.txt` (tools/my_messages.py output since {since}; its first line may name the transcript),
`loop/MISSES.md` is copied to `{work}/MISSES.md`, `{work}/times.txt` is `tools/my_messages.py --check-times` for the
same window (times in the orchestrator's messages that look typed from memory, MISSES 26: list them under (b), as one
item naming each line), `{work}/evalline.txt` its `--check-evalline` output, `{work}/asks.txt` its `--check-asks`
output, `{work}/answers.txt` its `--check-answers` output, `{work}/sources.txt` its `--check-sources` output and
`{work}/length.txt` its `--check-length` output (each
as the brief says), and `{work}/drift.txt` is the doc drift list for (f), each comment shown with its
code as main has it now: you can't read the repo, so judge each from what is shown. A file can run past one Read call
(drift.txt up to {drift_cap:,} characters): read each to its last line in pages (Read's offset and limit). Write
your list (the Output above) to `{work}/report.md` with Write: it is the only file you write, and the loop hands it
to the orchestrator. Your final message need only say it is written.
"""


def results_count(state: Path) -> int:
    path = state / "results.jsonl"
    if not path.exists():
        return 0
    with path.open() as handle:
        return sum(1 for line in handle if line.strip())


def read(state: Path) -> dict:
    try:
        return json.loads((state / "reflect.json").read_text())
    except (OSError, ValueError):
        return {}


def iso(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().isoformat(timespec="minutes")


# The by-hand line, for when the worker can't make the list. `drift`: the doc drift list saved for it (save_drift).
def due_line(reason: str, since: str, drift: str | None = None) -> str:
    listed = f" and the doc drift list {drift} (item (f))" if drift else ""
    return (f"REFLECT due ({reason}): have a fresh read-only pass (a read-only subagent if you have one, else "
            f"yourself) check `.venv/bin/python tools/my_messages.py --since {since}`{listed} against loop/MISSES.md, "
            "with its --check-times output for (b) (MISSES 26) and --check-evalline, --check-asks, --check-answers, "
            "--check-sources and --check-length, "
            "save its list "
            "as <loop home>/reflect/<time>.md, then judge each F-id (loop/REFLECT.md)")


# Records a REFLECT when one is due at `now` and returns a line for the caller's log, else None. The first call on a
# home with no record fires at once, reviewing the last period. The list itself is `tick`'s.
def check(state: Path, now: float) -> str | None:
    with locked(state / "reflect"):
        record, count = read(state), results_count(state)
        last = record.get("at")
        if last is None or now - last >= EVERY_SECONDS:
            reason = "none recorded yet" if last is None else f"{(now - last) / 3600:.1f} h since the last one"
        else:
            return None
        since = datetime.fromtimestamp(last if last is not None else now - EVERY_SECONDS).astimezone()
        temporary = state / "reflect.json.tmp"
        temporary.write_text(json.dumps({"at": now, "results": count, "since": since.isoformat(),
                                         "reason": reason}) + "\n")
        temporary.replace(state / "reflect.json")
        return f"REFLECT due ({reason}): the REFLECT worker lists the items for the orchestrator (loop/reflect.py)"


def done(state: Path, record: dict, report: str) -> tuple[str, dict]:
    path = state / "reflect" / f"{time.strftime('%Y-%m-%d-%H%M', time.localtime(record['until']))}.md"
    reports.publish(path, f"# REFLECT list: messages since {record['since']} ({record['reason']})\n\n{report}")
    return (f"REFLECT list ready: {path}; judge each F-id and record the decisions in decisions.md (loop/REFLECT.md)",
            {"covered_at": record["until"], "list": str(path)})


def gave_up(state: Path, record: dict, why: str) -> tuple[str, dict]:
    failures = "; ".join(record.get("failures", []))[-300:]
    line = due_line(record["reason"], record["since"], record.get("drift"))
    return (f"{line} (the REFLECT worker was abandoned {why}: {failures or 'no failures'})",
            {"covered_at": record["until"]})


TIMEOUT = 90 * 60  # past this the worker is stopped and counts as a failed launch
KIND = reports.Kind("reflect-list", "medium", lambda state: (), done, gave_up,
                    ("covered_at", "list", "finished_at", "last"), TIMEOUT)


# The upkeep tick's step, after check: collects or relaunches the pending list, else starts one for a REFLECT that
# fired since the last one handled. `models` False (a native session orchestrating, which never calls Claude) gives
# the orchestrator the by-hand line instead. Returns a line to log, or None.
def tick(state: Path, models: bool = True) -> str | None:
    return reports.tick(state, KIND, lambda: start(state, models), launch=models)


# Opens the worker's folder for the REFLECTs fired since `covered_at`, the window starting when the last one handled
# fired. With no record yet, it covers the latest REFLECT's own window.
def start(state: Path, models: bool = True) -> str | None:
    fired, record = read(state), reports.read(state, KIND)
    at = fired.get("at")
    if not isinstance(at, (int, float)):
        return None
    covered = record.get("covered_at")
    if covered is None:
        covered = datetime.fromisoformat(fired["since"]).timestamp() if fired.get("since") else at - EVERY_SECONDS
    if at <= covered:
        return None
    since, reason = iso(covered), fired.get("reason", "due")
    drift, drift_path = save_drift(state, since, at)
    if not models:
        return hand_over(state, record, at,
                         f"{due_line(reason, since, drift_path)} (no Claude worker under native orchestration)")
    try:
        messages = subprocess.run([sys.executable, str(REPO / "tools/my_messages.py"), "--since", since], cwd=REPO,
                                  capture_output=True, text=True, timeout=MESSAGES_TIMEOUT)
        if messages.returncode:
            raise RuntimeError((messages.stderr.strip() or f"exit {messages.returncode}")[-300:])
    except Exception as error:
        return hand_over(state, record, at,
                         f"{due_line(reason, since, drift_path)} (the REFLECT worker can't run: tools/my_messages.py "
                         f"failed: {error})")
    if not messages.stdout.strip():  # nothing to list: the default transcript is likely the wrong session
        transcript = next((m.group(1) for line in messages.stderr.splitlines() if (m := HEADER.match(line))),
                          "(no transcript named)")
        return hand_over(state, record, at, f"REFLECT due: 0 orchestrator messages since {since} (wrong transcript?): "
                                            f"tools/my_messages.py read {transcript}; rerun it with "
                                            "--session <the orchestrator's transcript> (and with --check-times for "
                                            "(b), MISSES 26, --check-evalline, --check-asks, --check-answers, "
                                            "--check-sources and --check-length) and "
                                            "judge its items, "
                                            "and the doc drift list "
                                            f"{drift_path} (item (f)), by hand (loop/REFLECT.md)")
    brief = BRIEF.read_text().split("## Brief for the subagent", 1)[1]
    prompt = f"# The REFLECT check{brief.rstrip()}\n{RUN.format(work='{work}', since=since, drift_cap=DRIFT_CAP)}"
    files = {"messages.txt": messages.stderr + messages.stdout, "MISSES.md": MISSES.read_text(),
             "drift.txt": drift, **{file: message_check(flag, since) for file, flag in CHECKS.items()}}
    reports.prepare(state, KIND, prompt, files, {"since": since, "until": at, "reason": reason, "drift": drift_path})
    return None


# The worker's files from tools/my_messages.py's checks over the window, each file with its flag: times.txt, the
# orchestrator's times that look typed from memory (MISSES 26); evalline.txt, its status replies not opening with
# evalline's line; asks.txt, its asks, waits on sig and recommendations that sig's standing grants cover, and
# agreements to a loop change naming no change; answers.txt, its replies to sig's questions that open with something
# else (REFLECT 2026-09-28 14:51 F6, F8, F16; 17:52 F1; 20:52 F1, F5); sources.txt, its answers about the game or
# site citing no docs/unsw/ page or saved Discord message (REFLECT 2026-09-29 14:29 F1); length.txt, its status
# replies and replies to notices over their line cap while sig's terse rule stands (20:42 F6).
CHECKS = {"times.txt": "--check-times", "evalline.txt": "--check-evalline", "asks.txt": "--check-asks",
          "answers.txt": "--check-answers", "sources.txt": "--check-sources", "length.txt": "--check-length"}


# One check's output (a CHECKS flag) over the window, or "None." when it finds nothing. A failure is a one-line note,
# so the list still launches.
def message_check(flag: str, since: str) -> str:
    name = next(file for file, f in CHECKS.items() if f == flag).removesuffix(".txt")
    try:
        done = subprocess.run([sys.executable, str(REPO / "tools/my_messages.py"), flag, "--since", since],
                              cwd=REPO, capture_output=True, text=True, timeout=MESSAGES_TIMEOUT)
        if done.returncode:
            raise RuntimeError((done.stderr.strip() or f"exit {done.returncode}")[-300:])
    except Exception as error:
        return f"No {name} check this time ({type(error).__name__}: {error}).\n"
    return done.stdout if done.stdout.strip() else "None.\n"


# The worker's drift.txt (REFLECT.md step 4): the comments whose definition's code changed on main since `since` (an
# ISO time: the window's start), from `check_refs --drift --since <the last main commit before it> --show`, each with
# its comment and code as main has them now, leaving out test files (in_tests). Cut between candidates at DRIFT_CAP.
# Any failure is a one-line note in its place, so the list still launches.
def drift_list(since: str) -> str:
    try:
        # --first-parent: main's own commits, whose dates are when they landed; a merged branch's are older.
        base = subprocess.run(["git", "rev-list", "-1", "--first-parent", f"--before={since}", "main"], cwd=REPO,
                              capture_output=True, text=True, timeout=60)
        commit = base.stdout.strip()
        if base.returncode or not commit:
            raise RuntimeError(f"no main commit before {since} ({base.stderr.strip()[-200:] or 'none'})")
        done = subprocess.run([sys.executable, str(REPO / "tools/check_refs.py"), "--drift", "--rev", "main",
                               "--since", commit, "--all", "--show", str(DRIFT_LINES)], cwd=REPO,
                              capture_output=True, text=True, timeout=DRIFT_TIMEOUT)
        if done.returncode:
            raise RuntimeError(f"check_refs exited {done.returncode}: {done.stderr.strip()[-200:]}")
    except Exception as error:
        return f"No drift list this time ({type(error).__name__}: {error}); write (f) none checked.\n"
    head = f"# Comments whose code changed on main since {commit[:12]}, the last main commit before {since}\n\n"
    blocks = [block for block in done.stdout.split("\n\n") if block.strip() and not in_tests(block)]
    if not blocks:
        return head + "None.\n"
    kept, size = [], len(head)
    for block in blocks:
        if size + len(block) + 2 > DRIFT_CAP:
            break
        kept.append(block)
        size += len(block) + 2
    if len(kept) < len(blocks):
        kept.append(f"... {len(blocks) - len(kept)} more not shown (cut at {DRIFT_CAP} characters)")
    return head + "\n\n".join(kept) + "\n"


# Builds the window's drift list and saves it as <loop home>/reflect/<until's time>-drift.txt, beside the lists, for
# the by-hand pass whenever the worker doesn't run. Returns (the text, its path).
def save_drift(state: Path, since: str, until: float) -> tuple[str, str]:
    text = drift_list(since)
    path = state / "reflect" / f"{time.strftime('%Y-%m-%d-%H%M', time.localtime(until))}-drift.txt"
    reports.publish(path, text)
    return text, str(path)


# A drift block ("path:line: name: ...") in a test file: test_*.py or under tools/testdata/. Tests change with every
# change they cover, so their comments filled the first real list (16:11 REFLECT, 2026-09-26: cut with 50 unseen).
def in_tests(block: str) -> bool:
    path = block.partition(":")[0]
    return Path(path).name.startswith("test_") and path.endswith(".py") or path.startswith("tools/testdata/")


# Marks the REFLECTs up to `at` handled without a worker and alerts `line`, the orchestrator's by-hand task.
def hand_over(state: Path, record: dict, at: float, line: str) -> str:
    reports.save(state, KIND, {**record, "covered_at": at})
    alert(state, line)
    return line


# health.py: a problem line when REFLECT hasn't fired for longer than LATE (the check fails each tick), else None.
# Quiet in a home with no results, where no loop has run.
def late(state: Path, now: float) -> str | None:
    last = read(state).get("at")
    if (last is not None and now - last <= LATE) or not (state / "results.jsonl").exists():
        return None
    ago = "never" if last is None else f"not since {time.strftime('%d %b %H:%M', time.localtime(last))}"
    return (f"REFLECT due has fired {ago} (every {EVERY_SECONDS // 3600} h is expected); see the upkeep's "
            f"'reflect' errors and {Path(__file__).resolve()}")
