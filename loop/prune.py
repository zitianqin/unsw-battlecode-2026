"""Packs saved loss replays (runs/E####/losses/, up to 40 games of 5-12 MB each, written by referee/sprt.py) once
nothing reads them unpacked again, so the loop home stops growing by gigabytes a day, losing nothing: cleanup never
deletes replays, results or partial builds (sig, 2026-09-29), so each goes into a verified archive beside it
(loop/pack.py's pack: runs/E####/replays.tar.zst, zstd -3 about 3.8x; `tar --zstd -xf` in that folder reads it back).
Their only readers unpacked are the post-mortem or follow-on (driver.prepare_postmortem copies them into the coder's
clone each time the turn runs: the post-mortem for a result that isn't kept, right after it is recorded, and the
follow-on for a kept one, once the promotion has started its evidence refresh in the background) and the replay audit
(loop/audit.py: the newest kept result's). So a result's losses are packed once it is recorded, the turn owed_turn
says it is owed has saved a finished turn (postmortem.json or followon.json: the Claude coder's answer that isn't an
error result, or native.py followups' marker; turn_ok), and, for a kept result, KEEP_KEPT newer kept results exist.
An unrecorded run, one still due its turn, or one a pending audit was given (audit.losses_given) keeps them unpacked.

It also packs the replays of the games we won against the organisers' dev teams in online-replays versions other
than the live one and the one before it (old_dev_replays) into that version folder's replays*.tar.zst: they can't be
played again, the site's replays expire. Every index's summaries and results stay, every loss's and draw's replay
(won) stays unpacked, and every dev row of such a version is marked "dev_wins_pruned" (its wins' files are in the
archive, not the folder) so no reader takes the folder's files for a sample (mark_dev_rows; its summaries stay the
sample); for prune.HOLD_S the wins of both versions of an online comparison that alerted stay unpacked
(held_versions). Unpacked, online-replays holds two versions' dev replays (online.DEV_REPLAYS +
online.DEV_LOSS_REPLAYS a dev team each, 0.6-1 MB a replay: 0.8-1.3 GB a version at 14 dev teams), at most two more
versions' wins while held, every older version's dev losses and draws (about 18 MB a version on 2026-09-28), and
every version's ladder and autoscrim replays (5.0 GB for 5,426 on 2026-09-28, growing with our versions). A pack that
fails leaves its files as they were, named in the log line, for the next tick. A tick packs for at most
pack.BUDGET_S.

    .venv/bin/python loop/prune.py        # what it would pack
    .venv/bin/python loop/prune.py --apply
"""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

# How long both versions of an online comparison that alerted keep their dev wins' replays past the usual two versions
# (online.hold_replays records which; losses are never pruned): 9553's alert against 9347 was looked into after 9695
# went live, when 9347's had already gone, and versions went live about hourly that day. Time to read the alert and
# replay its games (tools/why.py).
HOLD_S = 48 * 3600
KEEP_KEPT = 2  # the next audit reads the newest kept result's losses; a pending audit holds those it was given
# Which result is owed which coder's turn lives here, since this module imports nothing of the loop's: driver.py
# (prepare_postmortem) and health.py (result_turns) take it from here.
POSTMORTEM_VERDICTS = ("discarded", "promising")
# A kept result gets the follow-on turn (loop/FOLLOWON.md), unless its idea left no behaviour gain to build on: speed
# and identical ideas play the same moves, a noloss idea changes nothing in real games by design, and a tune has no
# coder that wrote its code (the champion's variant sweep and later reviews probe its numbers). A formula idea is
# tested for no loss like a noloss one but changes behaviour by design, so it gets a follow-on (its coefficients to
# tune, where it differs on other layouts), and so does a targeted one (kept on no loss and its measure).
NO_FOLLOWON_KINDS = ("speed", "identical", "noloss", "tune")
# The error referee/run.py gives a game on a map the engine refuses to load (run.MAP_REFUSED, the map's path, then the
# engine's reason). run.py writes it only after the engine's own loader, with no bot, refused that map, so a bot can
# never cause it: such an error result is re-evaluated with its candidate unchanged (driver.reevaluate), which
# health.input_faults checks. Matched from the start of the text that carries it, the SPRT's error ("game failed on
# <map> (as A): ...") or a failed screen's ("pool game failed: <entry>: <map> (as A): ...", driver.play_batch), in a
# result's reason or its SPRT's error, so a bot's printed output, which only ever follows "unswbc run exited N: ", can't
# fake it (MISSES 17).
MAP_FAULT = re.compile(r"(?:invalid SPRT result: |RuntimeError: )?(?:game failed on|pool game failed: [^:\s]+:) \S+ "
                       r"\(as [AB]\): the engine refused to load the map (\S+\.map): ([^|]+)")


# Why error result `row` failed through one of its maps rather than its candidate (MAP_FAULT, at the start of its
# reason or its SPRT's error), through a native judge check that differed during its SPRT, its opponent screen or
# its target batch (their "native_fault"), through its SPRT running out of maps because their maker stopped on an
# error (its "maps_fault", driver.lazy_making), or through the loop's own code after its coder finished (its session's
# "loop_fault", driver.loop_fault), or "".
def input_fault(row: dict) -> str:
    if said := (row.get("session") or {}).get("loop_fault"):
        return f"the loop's own code failed after coding: {str(said)[:200]}"
    for text in (row.get("reason") or "", (row.get("sprt") or {}).get("error") or ""):
        if found := MAP_FAULT.match(text):
            return f"the engine refused to load {Path(found.group(1)).name}: {found.group(2).strip()}"
    if said := (row.get("sprt") or {}).get("maps_fault"):  # driver.evaluate's, from its own map maker
        return f"its SPRT's maps stopped being made: {str(said)[:200]}"
    for part, during in (("sprt", "its SPRT"), ("pool", "its opponent screen"), ("target", "its target batch")):
        if said := (row.get(part) or {}).get("native_fault"):  # driver's, from the referee's own check
            return f"the native judge differed from unswbc run during {during}: {str(said)[:200]}"
    return ""


# 2026-09-27 01:41 AEST (the merge): experiments claimed from then on get a follow-on when kept, and native ones save a marker for their turn;
# a driver started before then (a --once already running) does neither.
TURNS_FROM = 1790437266
# When post-mortems began (986ba594, 2026-09-24 15:17 AEST) and when speed and identical results got theirs too
# (d2aaf434, 2026-09-26 04:27): a result claimed before its rule existed owes none, or E0025-E0040 and E0087 stayed
# owed for good, holding their losses (done_with) and clones' replay folders (pack.finished).
POSTMORTEMS_FROM = 1790227068
IDENTITY_TURNS_FROM = 1790360868
# A result not kept that ended before any game on something its coder can explain (MISSES 58): a "no code" stop,
# with the coder's reason ("nothing committed ...", not a session that did no work or a change the champion already
# had), or a candidate the bot tests or the CPU check failed. Blocked, error, stale and the old era's "no commit"
# results are re-queued, not judged, so they owe nothing.
# driver.withdraw's reason for a candidate taken out of the queue before its SPRT (driver.WITHDRAWN).
WITHDRAWN = "withdrawn before its SPRT by the orchestrator"
EARLY_REASONS = ("nothing committed", "bot scenario tests failed", "cpu/crash check failed", WITHDRAWN)
# 2026-09-27 22:28 AEST (this change): results recorded from then on owe that post-mortem; an earlier one gets it only
# when asked for (driver.py --turn E####, owed_turn's `early_from=0`), so the ones before don't all alert at once.
EARLY_TURNS_FROM = 1790512089


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


# Whether a result not kept ended before any game on something its coder can explain (EARLY_REASONS).
def ended_early(row: dict) -> bool:
    return row.get("verdict") in ("no code", "discarded") and str(row.get("reason") or "").startswith(EARLY_REASONS)


# The coder's turn a recorded result is owed and whose output is saved as runs/E####/<turn>.json: "postmortem" for a
# refereed result not kept (an SPRT or an identical check, claimed once its rule began: POSTMORTEMS_FROM) or one that
# ended early (ended_early, started from `early_from`), "followon" for a kept one of a kind with a gain to build on,
# else None. Only a coder the loop can
# resume (a Claude session) or a native one (the active model does the turn, then native.py followups saves the
# marker) owes one; a native result claimed before TURNS_FROM saved none.
def owed_turn(row: dict, early_from: float = EARLY_TURNS_FROM) -> str | None:
    session, kind = row.get("session") or {}, (row.get("idea") or {}).get("kind")
    started = row.get("started") or 0
    native = session.get("provider") == "native-agent"
    coder = session.get("session_id") or native and started >= TURNS_FROM
    if row.get("verdict") == "kept":
        return "followon" if kind not in NO_FOLLOWON_KINDS and started >= TURNS_FROM and coder else None
    decided = (row.get("sprt") or {}).get("decision")
    refereed = (decided or row.get("identity")) and started >= (POSTMORTEMS_FROM if decided else IDENTITY_TURNS_FROM)
    if coder and (row.get("verdict") in POSTMORTEM_VERDICTS and refereed
                  or ended_early(row) and started >= early_from):
        return "postmortem"
    return None


# A final message announcing that the worker is waiting for its own work (MISSES 63): a sentence starting "Waiting
# for", "I'll be notified", or "once they finish ... I'll". In a headless `claude -p` session nothing re-invokes the
# worker when its background task ends, so such a message is a turn that stopped early, not its result. E0197's coder
# ended "Waiting for the 24 re-run games to finish; I'll be notified when they do." and E0068's post-mortem "Waiting
# for the variant games to finish."; no other result, post-mortem, follow-on or merge turn saved by 2026-09-28 matches
# (finished ones say "worth waiting for", "turns spent waiting for", "nothing I started is still running").
WAITING = re.compile(
    r"(?:^|[.!?;:]\s+|\n\s*)(?:I'm |I am |Now |Still )?waiting (?:for|on|until)\b"
    r"|\bI(?:'ll| will) (?:be notified|get notified|be woken)\b"
    r"|\b(?:once|when) (?:they|it|those|these|the [\w-]+(?: [\w-]+){0,3}) (?:finish|finishes|complete|completes"
    r"|(?:are|is) done|come back|comes back)\b[^.\n]{0,80}\bI(?:'ll| will)\b", re.I)
WAITING_TAIL = 600  # only the message's end: a finished summary may narrate waiting earlier on
# A labelled line of a coder's report ("Blocked by: waiting on I440's scan", "Choices: waiting until round 450",
# "Follow-up I500: Waiting for pearls ...", "No code: waiting on I440's ..."): it describes the bot or the queue,
# never the coder's own turn.
LABELLED = re.compile(r"^[ \t>*_-]*(?:Blocked by|Predictable|Choices|Constants|Changed|No code"
                      r"|Follow-up\b[^:\n]{0,40})[*_]*:.*$",
                      re.I | re.M)


# Whether a worker's final message says it is still waiting for its own work (WAITING), outside its labelled lines.
def still_waiting(text: str) -> bool:
    return bool(WAITING.search(LABELLED.sub("", text.replace("’", "'"))[-WAITING_TAIL:]))


# A coder's final message saying it hands in no code on purpose (a measure-first stop, or its parent asked it to
# stop): PROMPT.md and driver.CONTINUE ask for a `No code:` line, and E0111's coder, stopped by the orchestrator,
# wrote "I abandoned E0111 and committed nothing, as the orchestrator (bc-main-fresh) asked". Its uncommitted edits are
# then not cut-off work (driver.still_working, health.uncommitted_no_code). "Left ... uncommitted" is no sign: a coder
# cut off mid-work says that too ("I have left the fix uncommitted until the quick check finishes").
NO_CODE_ON_PURPOSE = re.compile(r"^[ \t>*_-]*No code[*_]*:|\bcommitted nothing\b", re.I | re.M)


def no_code_on_purpose(text: str) -> bool:
    return bool(NO_CODE_ON_PURPOSE.search(text.replace("’", "'")))


# A coder's turn output (the text of runs/E####/<turn>.json) as its Claude result, or None when it is no result or an
# error one: an empty file, unreadable JSON or an error result (a usage limit: E0141-E0145's turns at 04:14 on
# 2026-09-27 each saved "You've hit your session limit" and queued nothing).
def turn_result(text: str) -> dict | None:
    try:
        output = json.loads(text)
    except ValueError:
        return None
    if isinstance(output, list):  # a stream: its last result row
        output = next((row for row in reversed(output) if isinstance(row, dict) and row.get("type") == "result"), None)
    return output if isinstance(output, dict) and output and not output.get("is_error") else None


# Whether a coder's turn output is a finished turn: a result (turn_result, or native.py followups' marker) that doesn't
# say it is still waiting for its own work (still_waiting).
def turn_ok(text: str) -> bool:
    return (output := turn_result(text)) is not None and not still_waiting(str(output.get("result") or ""))


# Whether a coder's turn output is a result that ended waiting for its own work (driver.run_resolve resumes it once).
def turn_waiting(text: str) -> bool:
    return (output := turn_result(text)) is not None and still_waiting(str(output.get("result") or ""))


# turn_ok for the saved file at `path`, False when there is none.
def turn_saved(path: Path) -> bool:
    try:
        return turn_ok(path.read_text())
    except OSError:
        return False


# The losses folders nothing will read again, oldest first, per the rule in the module docstring.
def done_with(state: Path) -> list[Path]:
    path = state / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    last = {row["id"]: k for k, row in enumerate(rows)}  # an id's latest row only: a rejudge's (meta.latest_rows)
    rows = [row for k, row in enumerate(rows) if last[row["id"]] == k]
    try:  # a pending audit's (audit.losses_given)
        audit = json.loads((state / "audit.json").read_text())
    except (OSError, ValueError):
        audit = {}
    given = set(audit.get("losses_given") or []) if isinstance(audit, dict) and audit.get("pending") else set()
    kept = [row["id"] for row in rows if row.get("verdict") == "kept"]
    newest_kept = set(kept[-KEEP_KEPT:])
    found = []
    for row in rows:
        losses = state / "runs" / row["id"] / "losses"
        if not losses.is_dir() or losses.is_symlink() or row["id"] in given:
            continue
        turn = owed_turn(row)
        if turn and not turn_saved(state / "runs" / row["id"] / f"{turn}.json"):
            continue
        if row.get("verdict") != "kept" or row["id"] not in newest_kept:
            found.append(losses)
    return found


# The dev team ids online.json knows (online.dev_ids; read here, since this module imports nothing of the loop's).
def dev_teams(report: dict) -> set[int]:
    teams = {t.get("id") for t in (report.get("dev") or {}).get("teams", [])}
    return (teams | {((report.get("benchmark") or {}).get("team") or {}).get("id")}) - {None}


# A version folder's saved games, [] when its index is missing or unreadable.
def version_games(version: Path) -> list[dict]:
    try:
        return json.loads((version / "index.json").read_text()).get("games", [])
    except (OSError, ValueError):
        return []


# The versions whose dev replays stay: online.json's live `submission_id` and the one before it, each with its play
# group (identical builds, whose games are that version's), as the readers pick them: online.previous_version (the
# newest earlier group with an unranked game; read here, since online.json's `compare.previous` is dropped while each
# cycle collects and after a failed one) and online.play_group. Keyed on the live version, not the highest ids, so an
# older submission activated by hand keeps the replays it is downloading. Empty without a live one.
def kept_versions(state: Path, report: dict) -> set[str]:
    import online  # here, not at the top: prune's other steps need none of it
    sid = report.get("submission_id")
    if not isinstance(sid, int):
        return set()
    previous = online.previous_version(state, sid)[0]
    return {str(s) for s in [sid, *online.play_group(state, sid), *(online.play_group(state, previous) if previous
                                                                    else [])]}


# The online-replays versions whose dev wins go: every version but kept_versions' and held_versions', each with its
# saved games and whether a game is against a dev team (marked "dev" when collected, or against a team online.json
# lists as one). None while online.json names no live submission.
def prunable(state: Path) -> Iterator[tuple[Path, list[dict], Callable[[dict], bool]]]:
    try:
        report = json.loads((state / "online.json").read_text())
    except (OSError, ValueError):
        return
    folder = state / "online-replays"
    versions = [p for p in folder.iterdir() if p.name.isdigit()] if folder.is_dir() else []
    keep, dev = kept_versions(state, report), dev_teams(report)
    if not keep:
        return
    keep |= held_versions(report)
    for version in versions:
        if version.name not in keep:
            yield version, version_games(version), lambda g: bool(g.get("dev") or g.get("opponent") in dev)


# Replays of games we won (won) against dev teams in prunable versions. Nothing reads them again: study.md's dev block
# reads the benchmark's newest submission's latest games that still have a file, the audit and coders' samples the
# live version's, and every report reads the index's summaries and results, which stay; a loss is studied long after
# (idea I666). Only regular files inside their version's folder.
def old_dev_replays(state: Path) -> list[Path]:
    found = []
    for version, games, is_dev in prunable(state):
        for game in games:
            path = Path(game.get("replay") or "")
            mine = path.name and path.parent.resolve() == version.resolve() and path.is_file() and not path.is_symlink()
            if mine and is_dev(game) and won(game):
                found.append(path)
    return found


# Marks every dev game of a prunable version "dev_wins_pruned" in its index before tick packs the wins'
# files, so a reader of replay files (online.file_selected: tools/value.py's sources, maintenance.dev_section,
# tools/replays.result_chosen, tools/fit.py) leaves out the losses and draws left: 9695's sample on 2026-09-28 had 37
# dev games with replays, 25 won and 12 lost, so its files would read as 12 losses in 12. It never touches "kept",
# which says a game was chosen by its result: the rows, summaries included, stay the result-blind sample for
# online.sample_games and the API record (online.all_games). In the same pass (no index read twice) it undoes the old
# marks (restore_sample). Returns how many rows it marked and how many it restored. Only under online_lock, as
# online.cycle writes the indexes under it.
def mark_dev_rows(state: Path) -> tuple[int, int]:
    marked = restored = 0
    for version, games, is_dev in prunable(state):
        undone = restore_sample(games, is_dev)
        fresh = [g for g in games if is_dev(g) and not g.get("dev_wins_pruned")]
        for game in fresh:
            game["dev_wins_pruned"] = True
        if fresh or undone:
            write_games(version, games)
        marked += len(fresh)
        restored += undone
    return marked, restored


# Whether `game`, a row of a prunable version (is_dev: prunable's dev test), carries exactly what this module's
# mark_dev_losses wrote from merge 2d635e6 (2026-09-28 17:46) until mark_dev_rows replaced it: "kept": "loss" on a dev
# game we didn't win, added after "replay" (online.collect_games sets its "kept" before it adds "replay", and later
# writers only add keys after both). A copy of every live index, 2026-09-28 19:15: 603 such rows in 33 versions, all
# dev losses or draws of prunable versions, none leaving more than online.DEV_REPLAYS unmarked a team.
def old_mark(game: dict, is_dev: Callable[[dict], bool]) -> bool:
    keys = list(game)
    return (game.get("kept") == "loss" and "replay" in game and keys.index("kept") > keys.index("replay")
            and is_dev(game) and not won(game))


# Deletes old_mark's "kept" from a prunable version's `games` (in place), which made online.sample_games read those
# versions' dev summaries as wins only (vs team 722, 8023-9695 showed 0 losses unmarked and 2-62 in their full
# records); returns how many. A driver started before this fix's merge runs the old code and marks them again each of
# its ticks, which the next new tick undoes. Delete this, its call in mark_dev_rows and health.old_kept_marks once no
# driver (driver.py, a --once) started before that merge is running and health.old_kept_marks has reported 0 left.
def restore_sample(games: list[dict], is_dev: Callable[[dict], bool]) -> int:
    marked = [g for g in games if old_mark(g, is_dev)]
    for game in marked:
        del game["kept"]
    return len(marked)


# Rewrites version folder `version`'s index.json with these games, the rest of it as it was, in online.save's format.
def write_games(version: Path, games: list[dict]) -> None:
    index = json.loads((version / "index.json").read_text())
    index["games"] = games
    temporary = version / "index.json.tmp"
    temporary.write_text(json.dumps(index, indent=2) + "\n")
    temporary.replace(version / "index.json")


# online.cycle's lock (meta.locked(state / "online"), <state>/online.lock), held while it reads and writes the version
# indexes; taken without waiting, since a cycle holds it for minutes: yields False while a cycle has it.
@contextmanager
def online_lock(state: Path) -> Iterator[bool]:
    with open(state / "online.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


# Whether a saved game was a win of ours, read from its summary's winner against our side; False for a loss, a draw
# or a game whose result can't be read, whose replays old_dev_replays keeps: every dev loss's replay is kept
# (CLAUDE.md), and until 2026-09-28 this deleted them with the wins (game 436945 of 9347, a Prisoners Dilemma loss to
# dev team 545 that idea I666 cites, and 148 replays downloaded again to measure it).
def won(game: dict) -> bool:
    try:
        winner = game["summary"]["result"]["winner"]
    except (KeyError, TypeError):
        return False
    return winner in ("A", "B") and winner == game.get("side")


# The versions online.json's "held" names (online.hold_replays: both sides of a comparison that alerted, for HOLD_S
# after it last did), whose dev wins old_dev_replays keeps too; empty once the hold has run out.
def held_versions(report: dict) -> set[str]:
    held = report.get("held")
    if not isinstance(held, dict) or not isinstance(held.get("at"), (int, float)) or time.time() - held["at"] >= HOLD_S:
        return set()
    return {str(v) for v in held.get("versions", []) if isinstance(v, int)}


# A file's size, 0 once it is gone (another process pruning at the same time).
def size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


# Packs done_with's folders and old_dev_replays' files (pack.pack, verified before anything goes), after marking the
# dev rows whose wins it packs and restoring old "kept" marks (mark_dev_rows), within pack.BUDGET_S; returns a log
# line naming what it packed, marked, restored or failed to pack (restored old dev losses are named even when nothing
# was packed), or "" when there was nothing.
def tick(state: Path) -> str:
    import pack  # here: it imports this module
    start, failed = time.monotonic(), []
    freed, runs = 0, []
    for losses in done_with(state):
        if time.monotonic() - start > pack.BUDGET_S:
            break
        try:
            _, before, after = pack.pack(losses.parent, [losses])
        except Exception as error:
            failed.append(f"{losses}: {type(error).__name__}: {error}"[:200])
            continue
        freed += before - after
        runs.append(losses.parent.name)
    lines = [f"packed the saved losses of {len(runs)} recorded results ({freed / 1e9:.1f} GB freed): "
             f"{', '.join(runs)}"] if runs else []
    # The wins are packed only once their version's dev rows are marked (mark_dev_rows), all while no online cycle is
    # writing the indexes; a busy tick leaves them for the next.
    packed, total = 0, 0
    with online_lock(state) as free:
        marked, restored = mark_dev_rows(state) if free else (0, 0)
        by_version: dict[Path, list[Path]] = {}
        for path in old_dev_replays(state) if free else []:
            if path.is_file():  # not one another process packed meanwhile
                by_version.setdefault(path.parent, []).append(path)
        for version, files in sorted(by_version.items()):
            if time.monotonic() - start > pack.BUDGET_S:
                break
            before = sum(map(size, files))
            try:
                _, _, after = pack.pack(version, files)
            except Exception as error:
                failed.append(f"{version}: {type(error).__name__}: {error}"[:200])
                continue
            packed, total = packed + len(files), total + before - after
    if restored:
        lines.append(f"restored {restored} old dev losses and draws to the result-blind sample (the \"kept\" marks "
                     "mark_dev_losses wrote before 2026-09-28's fix)")
    if packed or marked:
        lines.append(f"packed {packed} dev-team replays of games we won in online-replays versions other than the "
                     f"live one and the one before it ({total / 1e9:.1f} GB freed; their summaries stay in each index, "
                     f"and losses and draws keep their replays unpacked; {marked} more dev rows marked "
                     "dev_wins_pruned)")
    if failed:
        lines.append(f"WARNING {len(failed)} packs failed, their files left as they were: {'; '.join(failed)}")
    return "; ".join(lines)


if __name__ == "__main__":
    from home import loop_home
    home = loop_home()
    if "--apply" in sys.argv:
        print(tick(home) or "nothing to prune")
    else:
        found = done_with(home)
        print(f"{len(found)} folders, {sum(map(folder_size, found)) / 1e9:.1f} GB: "
              + ", ".join(p.parent.name for p in found))
        replays = old_dev_replays(home)
        print(f"{len(replays)} old dev-team replays, {sum(map(size, replays)) / 1e9:.1f} GB")
