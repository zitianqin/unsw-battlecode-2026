"""Numbered reports and the orchestrator's decision on each item (MISSES 52): a report the orchestrator judges numbers
its items with its kind's letter, and the orchestrator records one decision per id; health.py alerts when a report
older than its grace has an id with no decision, no verdict, or only a hand-off to a later step.

A report's item is a line `## D1. <title>` (a main item) or `- D7. <text>` (a minor one). Its decisions go in its
kind's decisions file under a heading that says "decisions" and names the report by its path relative to that file's
folder, `## Orchestrator's decisions on audits/1790479722912203000/findings.md (2026-09-27 16:40)`, one bullet per id
(several may share one; its text runs on indented lines):

    - A1 acted: queued I405 (what was done)
    - A2 declined: <why it isn't worth acting on>
    - A3, A4 check started: <which check, where its result will land>
    - A5-A7 declined: <why> (a range decides each id in it)

A later block naming the same report overrides an id's earlier decision. "Left to the audit", "for the next review",
"the audit watches for it" or a bare "noted" is not a decision. A checkable claim (a strategy, a pattern, an
opponent's habit) gets a check started now or, by default, a measure-first idea queued through
meta.append_ideas(state, meta.valid_proposals(...), source), whose first step is the measurement and whose stop line
says how it rules out luck (loop/chance.py); "hypotheses only, no new idea until a measurement" is not a decline
unless it cites the idea already covering it (I###) or says why the claim isn't worth even a measurement (it doesn't
apply to our game, the rules forbid it).

What the loop applies itself is listed the same way (`applied`): the ideas an automatic source queues, the existing
ideas a review or the daily backlog audit (loop/backlog.py) edits, merges or drops, and the method notes it adds or changes, by their own ids (`## I411. <title>`,
`## M48. <heading>`), in applied/<time>-<source>.md; each is decided `keep`, `edited`, `merged` or `dropped` (or
acted, declined).

KINDS lists each kind: its letter, where its reports live (a glob under the loop home) and its decisions file. A report
first written before NUMBERED_SINCE is checked only if it carries ids, so older unnumbered ones are left alone; a
Discord digest or an audit written since that has none is itself undecided (its prompt failed to number it), and
health.numbering alerts while no report of a kind since then carries ids (the prompts' first real firing).

Sizes, none pruned: decisions.md gets a block of a few KB per report (a few a day, under 1 MB by the contest's end,
18 Oct); reports/ one file per saved subagent report (2-10 KB each); applied/ one file a minute at most per source, a
line per idea or note (under 1 MB).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import meta


@dataclass(frozen=True)
class Kind:
    letter: str  # the ids' letter, or letters ("IM": idea and method-note ids)
    reports: str  # glob under the loop home
    decisions: str  # the decisions file, under the loop home


KINDS = (
    Kind("D", "discord/digest-*.md", "discord/digests.md"),  # Discord digests (loop/DISCORD.md)
    Kind("A", "audits/*/findings.md", "decisions.md"),  # replay audits' findings (loop/AUDIT.md)
    Kind("F", "reflect/*.md", "decisions.md"),  # REFLECT lists (loop/REFLECT.md)
    Kind("G", "mapideas/*/report.md", "decisions.md"),  # map-ideas (generator) reports (loop/MAPIDEAS.md)
    Kind("R", "risers/*/findings.md", "decisions.md"),  # riser studies' findings (loop/RISERS.md)
    Kind("B", "backlog-reports/*.md", "decisions.md"),  # daily backlog audits' reports (loop/BACKLOG.md)
    Kind("S", "reports/*.md", "decisions.md"),  # orchestrator subagents' reports, saved by the orchestrator
    Kind("IM", "applied/*.md", "decisions.md"),  # ideas and method notes the loop applied itself (`applied`)
)
# When the prompts began numbering their items: the orchestrator sets it to the merge of MISSES 52's branch. A digest
# or audit first written since then with no ids is undecided (pending), and health.numbering checks each kind's first.
NUMBERED_SINCE = datetime.fromisoformat("2026-09-27T17:23:00+10:00").timestamp()  # the merge (orchestrator sets it)
# The kinds whose every report has items (a digest quotes what it found, an audit, a riser study or a backlog audit
# reports findings); a REFLECT list, a map-ideas report or a subagent's may have none.
ALWAYS_ITEMS = ("D", "A", "R", "B")
# The ideas.jsonl sources that queue ideas with no orchestrator in between: a review ("meta"), an audit, a
# post-mortem or follow-on, a judge's confirmation, a re-check, a late screen's fix, a screen port, the variant sweep, a
# tune's split check (driver.check_tune_split), a riser study (loop/risers.py). The orchestrator's own additions carry
# other sources. The backlog audit (loop/backlog.py) queues no ideas; it lists what it changed through `applied`.
AUTOMATIC = re.compile(r"(?:meta|review|audit|postmortem|followon|judge|recheck|rescreen|screen-port|sweep|tunesplit"
                       r"|risers)\b")
VERDICT = re.compile(r"[\s*:.—–-]*(acted|declined|check started|keep|kept|edited|merged|dropped)\b"
                     r"\**[\s:.—–-]*", re.I)
# Words that hand an item to a later step instead of deciding it now: "left to the audit's dev losses", "for the
# next review", "the audit watches for it", "deferred to the next read" (not "fixed the deferred read bug").
HANDOFF = re.compile(r"\b(?:left|leave|leaving|leaves|passed|passes|handed|hands) (?:it |this |them )?(?:to|for) "
                     r"(?:the |a )?(?:next|later|audits?|reviews?|future)\b|\bnext review\b|\bfor later\b"
                     r"|\bdefer(?:red|s|ring)?(?: (?:it|this|them))? (?:to|until|for)\b|\brevisit(?:ed)? later\b"
                     r"|\b(?:audits?|reviews?) (?:will )?watch(?:es)?\b", re.I)
NOTED = re.compile(r"\bnoted\b", re.I)
# A checkable claim declined as unproven, "hypotheses only; no new idea until a measurement says ...": its default is
# a measure-first idea (sig, 2026-09-27), so such a decline must cite the idea already covering it (I###) or say why
# the claim isn't worth even a measurement.
UNPROVEN = re.compile(r"\bhypothes[ie]s only\b|\bno (?:new )?ideas? (?:until|unless|before)\b", re.I)
WORTHLESS = re.compile(r"\bI\d{3,}\b|n[o']t appl|forbid|against the rules", re.I)


def items(report: str, letter: str) -> list[str]:
    found = re.findall(rf"^(?:#{{2,4}} |[-*] )\**([{letter}]\d+)\.", report, re.M)
    return list(dict.fromkeys(found))


# The decision blocks in `text` (a decisions file) naming `name`: each from a heading of level 2 or 3 that says
# "decisions" and names it, to the next heading of level 1 to 3.
def blocks(text: str, name: str) -> list[str]:
    found, current = [], None
    for line in text.splitlines():
        if re.match(r"#{1,3} ", line):
            if current is not None:
                found.append("\n".join(current))
            named = re.match(r"#{2,3} ", line) and "decision" in line.lower() and name in line
            current = [] if named else None
        elif current is not None:
            current.append(line)
    if current is not None:
        found.append("\n".join(current))
    return found


# Each id's decision text in `blocks`, the last one written winning. A label in brackets may follow the ids,
# `- D1 (Sponge's roster): acted: ...`; a range, `- D4-D6` or `- I001 to I003`, decides each id in it.
def decided(blocks: list[str], letter: str) -> dict[str, str]:
    one = rf"[{letter}]\d+"
    bullet = re.compile(rf"^[-*] +\**({one}(?:\s*(?:,|/|&|and|-|–|to)\s*{one})*)\**(?:\s*\([^)]*\))?\s*[:.]?\s*(.*)",
                        re.I)
    found: dict[str, str] = {}
    for block in blocks:
        ids: list[str] = []
        for line in block.splitlines():
            if match := bullet.match(line):
                ids = [f"{first}{n:0{len(start)}d}"
                       for first, start, end in re.findall(rf"([{letter}])(\d+)(?:\s*(?:-|–|TO)\s*\1(\d+))?",
                                                           match[1].upper())
                       for n in range(int(start), max(int(start), int(end or 0)) + 1)]
                found.update((i, match[2].strip()) for i in ids)
            elif ids and line.startswith((" ", "\t")) and line.strip():
                found.update((i, f"{found[i]} {line.strip()}") for i in ids)
            else:
                ids = []
    return found


# What is wrong with a decision's text, or None: no verdict word, a hand-off to a later step, a claim declined as
# unproven with no idea or reason, or no reason beyond the verdict (fewer than two words once "noted" is taken out).
def problem(text: str) -> str | None:
    verdict = VERDICT.match(text)
    if not verdict:
        return "no verdict (acted, declined or check started)"
    rest = text[verdict.end():]
    if handoff := HANDOFF.search(rest):
        return f'hands it to a later step ("{handoff[0]}")'
    if (unproven := UNPROVEN.search(rest)) and not WORTHLESS.search(rest):
        return f'declines a claim as unproven ("{unproven[0]}") with no idea queued or reason not to measure it'
    if len(re.findall(r"[A-Za-z0-9]{2,}", NOTED.sub(" ", rest))) < 2:
        return "no reason or action given"
    return None


# The ids in `report` (a report's text) with no acceptable decision in `text` (its decisions file) under a heading
# naming `name`, each with why, in the report's order: [("D2", "no decision"), ...]. [] for a report without ids.
def undecided(report: str, text: str, name: str, letter: str) -> list[tuple[str, str]]:
    made = decided(blocks(text, name), letter)
    return [(i, why) for i in items(report, letter)
            if (why := "no decision" if i not in made else problem(made[i]))]


# When `report` was first written, for NUMBERED_SINCE: its inode change time, which a worker can't set back (it can
# date the modification time anywhere).
def written(report: Path) -> float:
    return report.lstat().st_ctime


# The reports of `kind` under `state` as (report, its text), sorted by name; a link or not a plain file is skipped.
def reports(state: Path, kind: Kind) -> list[tuple[Path, str]]:
    found = []
    for report in sorted(state.glob(kind.reports)):
        try:
            found.append((report, meta.worker_file(report)))  # audits/ and mapideas/ are workers' folders (MISSES 17)
        except OSError:  # not a plain file: nothing the orchestrator was handed
            continue
    return found


# Every report under `state` whose ids aren't all decided, with its decisions file, the name its heading must give
# and what is missing: [(report, decisions file, name, [(id, why), ...]), ...]. A digest or audit written since
# NUMBERED_SINCE with no ids gives [("-", why)]: its prompt failed to number it.
def pending(state: Path) -> list[tuple[Path, Path, str, list[tuple[str, str]]]]:
    found = []
    for kind in KINDS:
        record = state / kind.decisions
        text = record.read_text() if record.exists() else ""
        for report, content in reports(state, kind):
            name = report.relative_to(record.parent).as_posix()
            if kind.letter in ALWAYS_ITEMS and not items(content, kind.letter) and written(report) >= NUMBERED_SINCE:
                found.append((report, record, name, [("-", f"no {kind.letter}1.-numbered items")]))
            elif missing := undecided(content, text, name, kind.letter):
                found.append((report, record, name, missing))
    return found


# Records what the loop applied itself, so each gets a decision like any report's item: the ideas `source` queued
# (rows as written to ideas.jsonl), the existing ideas it changed (each row with what changed, "merged into I375",
# "edited: priority, after") and the method notes it added or changed (their ids, "M48", with their headings),
# appended to applied/<yyyy-mm-dd-hhmm>-<source>.md. Called by meta.append_ideas for an AUTOMATIC source, by
# meta.apply_review and by backlog.apply (its proposals too, "proposed, not applied: ..."); the orchestrator keeps,
# edits, merges or drops each (MISSES 52: a review's I411-I415 got none).
def applied(state: Path, source: str, ideas: list[dict], notes: dict[str, str] | None = None,
            changed: list[tuple[dict, str]] = ()) -> Path | None:
    def title(idea: dict) -> str:  # on one line, so a title can't add items of its own
        return " ".join(str(idea.get("title", "")).split())
    lines = [f"## {i['id']}. {title(i)} ({i.get('category', '?')}, priority {i.get('priority', '?')}"
             + (f", merged into {i['merged_into']}" if i.get("status") == "merged" else "") + ")" for i in ideas]
    lines += [f"## {i['id']}. {title(i)} ({what})" for i, what in changed]
    lines += [f"## {note}. {heading} (method.md)" for note, heading in (notes or {}).items()]
    if not lines:
        return None
    now = time.localtime()
    path = state / "applied" / f"{time.strftime('%Y-%m-%d-%H%M', now)}-{re.sub(r'[^A-Za-z0-9]+', '-', source)}.md"
    path.parent.mkdir(exist_ok=True)
    head = "" if path.exists() else (f"# Applied by {source} ({time.strftime('%Y-%m-%d %H:%M', now)}): keep, edit, "
                                     "merge or drop each (loop/decisions.py)\n\n")
    with path.open("a") as handle:
        handle.write(head + "\n".join(lines) + "\n")
    return path


# The method notes in `new` (method.md's text) that `old` lacks or words differently: {"M48": its heading}. A note
# is a `## M48. ...` section (one heading may name several, `## M44 and M41. ...`), up to the next `## `.
def changed_notes(old: str, new: str) -> dict[str, str]:
    def sections(text: str) -> dict[str, tuple[str, str]]:
        found = {}
        for part in re.split(r"(?m)^(?=## )", text):
            heading = part.split("\n", 1)[0]
            for note in re.findall(r"\bM\d+\b", heading.split(".", 1)[0]):
                found[note] = (heading[3:].split(". ", 1)[-1].strip(), part.strip())
        return found
    before = sections(old)
    return {note: heading for note, (heading, part) in sections(new).items()
            if before.get(note, (None, None))[1] != part}
