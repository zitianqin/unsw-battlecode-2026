"""The Discord digest worker: after a Discord read that stored new messages (maintenance.discord records its time as
discord.json's `new_at`), the upkeep tick opens a digest folder and launches a confined read-only worker on it
(loop/reports.py), in the background. Its prompt is the discord-digest agent's instructions and loop/DISCORD.md's
brief; its folder holds copies of that read's latest.md, teams.json and roles.json and of study.md and briefing.md,
and it may also read <loop home>/discord/ (messages.jsonl, digests.md) and the repo's docs/unsw/. Its report is saved
as <loop home>/discord/digest-<read time>.md with one line in host-alerts.log (a subagent's report, which the
orchestrator saves by hand, takes the same name; the worker never overwrites one, `free`). Its items are numbered D1,
D2, ... (loop/DISCORD.md, Report); the orchestrator checks their claims and appends one decision per id to
discord/digests.md under a heading naming the report's file, and health.py alerts on an id still without one
(loop/decisions.py). Records live in <loop home>/discord-digest.json: the folder pending, its pid and failures, and
`digested_at`, the last read handled.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import meta
import reports

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "loop/DISCORD.md"
AGENT = REPO / ".claude/agents/discord-digest.md"

RUN = """
## This run

You run headless for the loop, with Read, Grep, Glob and Write only: no shell, no web, no network. Your files:
- `{work}/latest.md`: the read to digest (the upkeep's read at {read}), with `teams.json` and `roles.json` beside it
  as they were then.
- `{store}`: every stored message; `{digests}`: earlier digests and the orchestrator's decisions on them (don't report
  again what a decision already settled unless something changed).
- `{docs}`: the official docs and site pages.
- `{work}/study.md`: our and the leaders' game statistics; `{work}/briefing.md`: what research currently assumes.

Write the report (the Report section above) to `{work}/report.md` with Write: it is the only file you write, and the
loop hands it to the orchestrator. Your final message need only say it is written.
"""


def reads(state: Path) -> tuple[Path, ...]:
    return state / "discord", REPO / "docs/unsw"


def stamp(at: float) -> str:
    return time.strftime("%Y-%m-%d-%H%M", time.localtime(at))


# Where a read's report is saved: discord/digest-<read time>.md, the name a by-hand digest takes too.
def report_path(state: Path, read_at: float) -> Path:
    return state / "discord" / f"digest-{stamp(read_at)}.md"


# `path`, or the first free digest-<read time>-2.md, -3.md ... beside it: a worker that finishes after the orchestrator
# saved its own digest of the same read keeps both, since the decisions cite the saved one's ids.
def free(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate, n = path.with_name(f"{path.stem}-{n}.md"), n + 1
    return candidate


DECIDE = "decide on each D-id and append the decisions to discord/digests.md (loop/decisions.py)"


def done(state: Path, record: dict, report: str) -> tuple[str, dict]:
    path = free(report_path(state, record["read_at"]))
    reports.publish(path, report)
    try:  # the worker's copy, which it could have replaced: read without following it (meta.worker_file)
        note = count_note(report, meta.worker_file(Path(record["pending"]) / "latest.md"))
    except (KeyError, OSError):
        note = ""
    return (f"DISCORD DIGEST ready: {path}{note}; check its claims, {DECIDE}",
            {"digested_at": record["read_at"], "report": str(path)})


# What the report's closing line (loop/DISCORD.md, Report: "Read N messages: ...") says against latest.md's
# `## #channel (N)` headings: "" when it read them all, else a note for the alert line. A day's latest.md is over one
# Read call, so a worker that stops at the first page still writes a report that looks whole.
def count_note(report: str, latest: str) -> str:
    total = sum(int(n) for n in re.findall(r"(?m)^## #\S+ \((\d+)\)$", latest))
    stated = re.findall(r"(?i)\bread ([\d,]+) messages", report)
    if not stated:
        return f" (its report states no message count; latest.md holds {total})"
    read = int(stated[-1].replace(",", ""))
    return f" (its report says it read {read} of latest.md's {total} messages)" if read < total else ""


# The orchestrator's by-hand task, worded for either provider (a native session has no discord-digest agent). A
# subagent can't write, so the orchestrator saves its report, returned in chat, as `report`.
def by_hand(latest: Path, report: Path) -> str:
    return (f"digest {latest} by hand with a fresh read-only pass (the discord-digest subagent, another read-only "
            f"subagent, or yourself) following loop/DISCORD.md, save its report as {report}, then {DECIDE}")


def gave_up(state: Path, record: dict, why: str) -> tuple[str, dict]:
    failures = "; ".join(record.get("failures", []))[-400:]
    latest = Path(record["pending"]) / "latest.md"
    return (f"DISCORD DIGEST abandoned {why} ({record['pending']}): "
            f"{by_hand(latest, report_path(state, record['read_at']))}; failures: {failures or 'none'}",
            {"digested_at": record["read_at"], "report": None})


TIMEOUT = 60 * 60  # past this the worker is stopped and counts as a failed launch; a day's read is 150-360 KB
KIND = reports.Kind("discord-digest", "low", reads, done, gave_up, ("digested_at", "report", "finished_at", "last"),
                    TIMEOUT)


# The worker's stdin: the agent's instructions (its file without the front matter), then loop/DISCORD.md's brief
# with its paths filled in, then this run's files and where to write.
def prompt(state: Path, read_at: float) -> str:
    agent = re.sub(r"\A---\n.*?\n---\n", "", AGENT.read_text(), flags=re.S).strip()
    import maintenance
    paths = {"latest": "{work}/latest.md", "store": str(state / "discord/messages.jsonl"),
             "roles": "{work}/roles.json", "toolkit": maintenance.toolchain(state).get("version") or "unknown"}
    brief = BRIEF.read_text()
    for key, value in paths.items():
        brief = brief.replace("{" + key + "}", value)
    run = RUN.format(work="{work}", read=time.strftime("%Y-%m-%d %H:%M", time.localtime(read_at)),
                     store=paths["store"], digests=state / "discord/digests.md", docs=REPO / "docs/unsw")
    return f"{agent}\n\n{brief.rstrip()}\n{run}"


def text_or(path: Path, missing: str) -> str:
    return path.read_text() if path.exists() else missing


# The upkeep tick's step, after the Discord read: collects or relaunches the pending digest, else opens and launches
# one for a read with new messages not handled yet. `models` False (a native session orchestrating, which never calls
# Claude) hands such a read to the orchestrator instead. Returns a line to log, or None.
def tick(state: Path, models: bool = True) -> str | None:
    return reports.tick(state, KIND, lambda: start(state, models), launch=models)


# Opens a digest folder for the latest read when it stored new messages (discord.json's `new_at`) that no digest has
# handled. On the first run (no digest recorded yet), a read the orchestrator already digested by hand (digests.md
# written since) is marked handled; after that a digests.md write says nothing about which read it decided (it may be
# an older read's decisions). A read killed before it wrote latest.md is handed to the orchestrator.
def start(state: Path, models: bool = True) -> str | None:
    record = reports.read(state, KIND)
    read = json.loads(text_or(state / "discord.json", "{}")).get("new_at")
    if not isinstance(read, (int, float)) or read <= record.get("digested_at", 0):
        return None
    digests, latest = state / "discord/digests.md", state / "discord/latest.md"
    handled = "digested_at" not in record and digests.exists() and digests.stat().st_mtime >= read
    stale = not latest.exists() or latest.stat().st_mtime < read - 1800  # a killed read writes none
    if handled or stale:
        reports.save(state, KIND, {**record, "digested_at": read, "report": None})
        if handled:
            return None
        line = (f"DISCORD DIGEST skipped: the read at {stamp(read)} wrote no latest.md; "
                f"{by_hand(state / 'discord/messages.jsonl', report_path(state, read))} (its new messages)")
        meta.alert(state, line)
        return line
    if not models:
        reports.save(state, KIND, {**record, "digested_at": read, "report": None})
        line = (f"DISCORD DIGEST by hand: the read at {stamp(read)} stored new messages and no Claude worker runs under "
                f"native orchestration; {by_hand(latest, report_path(state, read))}")
        meta.alert(state, line)
        return line
    files = {"latest.md": latest.read_text(),
             "teams.json": text_or(state / "discord/teams.json", "{}\n"),
             "roles.json": text_or(state / "discord/roles.json", "{}\n"),
             "study.md": text_or(state / "study.md", "(no study yet)\n"),
             "briefing.md": text_or(state / "briefing.md", "(no briefing yet)\n")}
    reports.prepare(state, KIND, prompt(state, read), files, {"read_at": read})
    return None
