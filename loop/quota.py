"""The loop's side of the 5-hour usage levels (sig, 30 Sep 02:03-02:30: at the top level "pause everything even agents
currently working (ensuring nothing is lost) ... only let the eval-related agents continue", "so it can turn on and
off and on automatically", "at 95% we do that instead of 98%").

tools/usage_statusline.py, the Claude Code status line, writes <loop home>/usage-level.json, {"level": name, "rank",
"resets_at", "since"}, when the window's highest level (its LEVELS) changes, and removes it at a new window. Readers
here take a marker whose resets_at has passed as no level, so the loop turns itself back on at the reset even when no
session draws a status line then; health.usage_level alerts on a marker that outlives it. What each rank holds, and
what never waits (the evaluation queue, SPRTs, screens, judges, promotions, results, merge turns of queued candidates):

    AHEAD   (rank 1, ahead of pace)  the upkeep's optional workers (HELD: backlog and replay audits, the Discord
                                     digest, the riser study, map ideas) start nothing; coding lanes stay full
    REVIEWS (rank 2, 85%)            the REFLECT list and meta reviews also wait for the new window
    PAUSE   (rank 3, 95%)            claims wait, running coders are stopped and resumed at the reset (driver.pausable),
                                     post-mortems and follow-ons stay owed (maintenance.owed_turns reruns them after),
                                     reminders wait (health.reminders)

PAUSE was at 98% before: there the E0378 and E0368 judges still hit the limit. Each level holds everything a lower
one does. Nothing here writes; the status line is the only writer."""

from __future__ import annotations

import json
import time
from pathlib import Path

LEVEL = "usage-level.json"
# sig's off switch (1 Oct 01:40: "temporarily turn off the 5h usage budgeting ... until like 10am"): while
# <loop home>/usage-levels-off.json's {"until": epoch} lies ahead, every level reads as none; past it, the file is inert.
OFF = "usage-levels-off.json"
AHEAD, REVIEWS, PAUSE = 1, 2, 3
RESET_MARGIN_S = 120  # a wake reminder is due this long after the reset it waits for (health.wake, the status line's)
# What waits at each rank, for evalline's agents line.
WAITS = {AHEAD: "optional workers wait", REVIEWS: "optional workers, REFLECT and meta reviews wait",
         PAUSE: "the loop is paused: no claims, coders stopped, post-mortems and follow-ons owed; evaluation goes on"}
# The rank from which each launch kind (a worker's reports/record name, or the driver's "coder" and "turn") waits for
# the new window.
HELD = {"backlog": AHEAD, "audit": AHEAD, "discord-digest": AHEAD, "risers": AHEAD, "mapideas": AHEAD,
        "reflect-list": REVIEWS, "review": REVIEWS, "coder": PAUSE, "turn": PAUSE}
# The launches that never wait: evaluation's own sessions (an SPRT's judge, a queued candidate's merge turn).
NEVER = ("judge", "merge")


# A Claude launch that went ahead while its usage level held it (meta.worker_command raises it; its caller had
# checked held first, so only a level crossed in between gets here). Its text reads as a provider's quota error with
# its reset (meta.provider_limited, limit_reset), so a caller's failure path retries it after the reset, uncounted.
class Held(RuntimeError):
    pass


def held_error(state: Path, kind: str, why: str) -> Held:
    resets = marker(state).get("resets_at") or time.time()
    clock = time.strftime("%I:%M%p", time.localtime(resets)).lstrip("0").lower()
    return Held(f"{kind} held by the loop's 5 h usage level ({why}): usage limit, resets {clock}")


# The one usage gate every headless Claude launch calls with its kind (test_launches checks each does): why its level
# holds it now, or "". A kind in neither HELD nor NEVER raises KeyError, so a new launcher has to say which it is.
def gate(state: Path, kind: str, now: float | None = None) -> str:
    if kind in NEVER:
        return ""
    if kind not in HELD:
        raise KeyError(f"{kind!r} is no usage kind: add it to quota.HELD or quota.NEVER")
    return held(state, kind, now)


# The live marker, or {} when there is none, it is unreadable, its window has reset by `now`, or sig's OFF switch
# holds.
def marker(state: Path, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        until = json.loads((state / OFF).read_text()).get("until")
        if isinstance(until, (int, float)) and now < until:
            return {}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        record = json.loads((state / LEVEL).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    rank, resets = record.get("rank"), record.get("resets_at")
    if not isinstance(rank, int) or isinstance(rank, bool) or not isinstance(resets, (int, float)):
        return {}
    return record if now < resets else {}


def rank(state: Path, now: float | None = None) -> int:
    return marker(state, now).get("rank", 0)


def paused(state: Path, now: float | None = None) -> bool:
    return rank(state, now) >= PAUSE


# "usage 95% until 22:00" when the live level holds `what` (a HELD name, or a rank), else "".
def held(state: Path, what: str | int, now: float | None = None) -> str:
    level = marker(state, now)
    at = what if isinstance(what, int) else HELD.get(what, PAUSE + 1)  # a name not in HELD never waits
    if level.get("rank", 0) < at:
        return ""
    return f"usage {level.get('level', level['rank'])} until {time.strftime('%H:%M', time.localtime(level['resets_at']))}"
