"""Claude Code status line command that records the session's 5-hour usage share for loop/evalline.py's agents line
and alerts the orchestrator when it crosses a level (REFLECT 2026-09-29 20:42 F23: the orchestrator had no measure of
its own model usage, and sig had to say "were at 50% of our 5 hour usage"; sig, 29 Sep: alerts instead of checking).
Claude Code gives a status line command its state as JSON on stdin, and only there, with the share as
rate_limits.five_hour (used_percentage, resets_at in epoch seconds); nothing under ~/.claude keeps it. This writes the
two with the time and the window's highest level alerted to <loop home>/usage-share.json (one small object, replaced
whole) and prints "5h 52% (resets 22:00)" as the status line. Input without the field (an API-key session, older
Claude Code) writes nothing and prints nothing; any error prints nothing, so the status line never breaks.

A crossing (crossings) appends one "USAGE ..." line to <loop home>/host-alerts.log (meta.alert's format, written here
so the status line doesn't import the loop), which tools/orch/watch.sh wakes on at once. Levels, each alerted once a
window (the 5 hours before resets_at), highest last: ahead of pace (used more than AHEAD points above the share of the
window gone, and past AHEAD_FLOOR: the upkeep starts no optional workers, lanes stay full), 85% (REFLECT and meta
reviews wait for the new window; write handoff notes), 95% (the full pause, moved from 98% on 30 Sep: no claims,
coders stopped and resumed at the reset, only evaluation goes on; there is no 98% level); and a new window beginning.
The window's level is also the marker <loop home>/usage-level.json, {"level", "rank", "resets_at", "since"}, which
the loop acts on (loop/quota.py): written when the level changes (marker), removed at a new window. Every session in
the repo runs this, so a flock lets one writer at a time.

    installed in the untracked local settings (settings.local.json in the repo's .claude folder):
    {"statusLine": {"type": "command", "command": ".venv/bin/python tools/usage_statusline.py"}}
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

USAGE = "usage-share.json"  # evalline.USAGE
WINDOW_S = 5 * 3600
AHEAD = 5  # points above the window's elapsed share that count as ahead of pace
AHEAD_FLOOR = 50  # ahead of pace counts only past this share used (sig 30 Sep)
NEW_WINDOW_S = 600  # a resets_at this much later than the saved one starts a new window
# (rank, name, used at least, what to do), lowest first; "ahead" has no fixed bar.
# The loop acts on each through the marker: loop/quota.py's AHEAD, REVIEWS and PAUSE are these ranks.
LEVELS = ((1, "ahead", None, "the upkeep starts no optional workers, coding lanes stay full"),
          (2, "85%", 85, "REFLECT and meta reviews wait for the new window; write handoff notes"),
          (3, "95%", 95, "the loop pauses: no claims, coders stopped and resumed at the reset (work kept), post-mortems "
                         "and follow-ons owed; evaluation, judges and results go on; pause your own builders"))
LEVEL = "usage-level.json"  # quota.LEVEL


def hhmm(at: float) -> str:
    return time.strftime("%H:%M", time.localtime(at))


# The level `used` (percent) is at, `now` in the window ending `resets`: its LEVELS row, or None below them all.
def level(used: float, resets: float, now: float) -> tuple | None:
    elapsed = min(1.0, max(0.0, (now - (resets - WINDOW_S)) / WINDOW_S)) * 100
    for row in reversed(LEVELS):
        if row[2] is not None and used >= row[2] or row[2] is None and used > max(elapsed + AHEAD, AHEAD_FLOOR):
            return row
    return None


# The alert lines for a reading (`used`, `resets`) at `now`, given the saved record `saved` (usage-share.json), and the
# record to save: a new window alerts, then any level above the highest this window has alerted ("rank"), once.
def crossings(saved: dict, used: float, resets: float, now: float) -> tuple[list[str], dict]:
    lines, rank = [], min(saved["rank"], len(LEVELS)) if isinstance(saved.get("rank"), int) else 0  # 98% was 4
    before = saved.get("resets_at")
    if not isinstance(before, (int, float)) or resets > before + NEW_WINDOW_S:
        rank = 0
        if isinstance(before, (int, float)):
            lines.append(f"USAGE new 5 h window: {used:.0f}% used, resets {hhmm(resets)}")
    if (row := level(used, resets, now)) and row[0] > rank:
        rank = row[0]
        gone = min(100.0, max(0.0, (now - (resets - WINDOW_S)) / WINDOW_S * 100))
        lines.append(f"USAGE {row[1]}: 5 h usage {used:.0f}% with {gone:.0f}% of the window gone, resets "
                     f"{hhmm(resets)}: {row[3]}")
    return lines, {"used_percentage": used, "resets_at": resets, "at": now, "rank": rank}


# The marker for the record `record` crossings saves, given the one on disk `current` ({} when none): None to remove
# it (no level this window), `current` itself while its level and window hold, else a new one since `now`.
def marker(record: dict, current: dict, now: float) -> dict | None:
    rank = record.get("rank") or 0
    if not rank:
        return None
    resets = record["resets_at"]
    if (current.get("rank") == rank and isinstance(current.get("resets_at"), (int, float))
            and abs(current["resets_at"] - resets) <= NEW_WINDOW_S):
        return current
    return {"level": LEVELS[rank - 1][1], "rank": rank, "resets_at": resets, "since": now}


# A level's wake (REFLECT 30 Sep 0311 F2): the first crossing of a window appends a reminder due RESET_MARGIN_S after
# its reset to <loop home>/reminders.jsonl (health.remind's row, under its lock, health.lock; skipped while a health
# check holds it, and tried again at the next reading), so an unattended orchestrator is woken at the new window: the
# minute health check alerts it, and watch.sh wakes on it. Returns whether it was written.
RESET_MARGIN_S = 120  # quota.RESET_MARGIN_S
WAKE = "USAGE window reset at {at}: the loop turns back on by itself (LOOP.md: Usage levels); check paused coders " \
       "resumed, lanes refilled and owed turns rerun (evalline), and resume your own paused work"


def add_wake(state: Path, resets: float) -> bool:
    due = datetime.fromtimestamp(resets + RESET_MARGIN_S).astimezone().isoformat(timespec="minutes")
    with open(state / "health.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with (state / "reminders.jsonl").open("a") as handle:
            handle.write(json.dumps({"due": due, "text": WAKE.format(at=hhmm(resets))}) + "\n")
    return True


# Replaces `path` with `record` whole, through a temporary file.
def save(path: Path, record: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(path)


# home.loop_home, copied so the status line doesn't import the loop.
def loop_home() -> Path:
    return Path(os.environ.get("BC_LOOP_HOME") or Path.home() / ".bc-loop")


def main() -> int:
    try:
        five = ((json.loads(sys.stdin.read() or "{}").get("rate_limits") or {}).get("five_hour") or {})
        used, resets = five.get("used_percentage"), five.get("resets_at")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (used, resets)):
            return 0
        print(f"5h {used:.0f}% (resets {hhmm(resets)})")
        state = loop_home()
        with open(state / f"{USAGE}.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0  # another session is writing this reading
            try:
                saved = json.loads((state / USAGE).read_text())
            except (OSError, ValueError):
                saved = {}
            now = time.time()
            saved = saved if isinstance(saved, dict) else {}
            lines, record = crossings(saved, used, resets, now)
            woken = saved.get("wake")  # the window whose wake is written (add_wake)
            if isinstance(woken, (int, float)) and abs(woken - resets) <= NEW_WINDOW_S:
                record["wake"] = woken
            if lines:
                with (state / "host-alerts.log").open("a") as handle:
                    handle.writelines(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))} {line}\n"
                                      for line in lines)
            save(state / USAGE, record)
            try:
                current = json.loads((state / LEVEL).read_text())
            except (OSError, ValueError):
                current = {}
            current = current if isinstance(current, dict) else {}
            if (level := marker(record, current, now)) is None:
                (state / LEVEL).unlink(missing_ok=True)
            elif level is not current:
                save(state / LEVEL, level)
            if record["rank"] and "wake" not in record:  # last: a failed wake never costs the marker
                try:
                    if add_wake(state, resets):
                        save(state / USAGE, {**record, "wake": resets})
                except OSError:
                    pass
    except Exception:  # a broken status line never shows an error
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
