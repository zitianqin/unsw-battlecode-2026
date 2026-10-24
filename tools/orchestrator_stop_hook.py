"""Claude Code Stop hook for the orchestrating session only: blocks the stop when its last reply breaks the status
rules tools/my_messages.py checks (--check-evalline: a status reply that doesn't open with loop/evalline.py's line;
--check-times: a clock time typed from memory, MISSES 26, or a capacity or finish estimate from no source run that
turn, REFLECT 2026-09-30 15:13 F21; --check-sources: an answer about the game, engine, site or server citing no
docs/unsw/ page or saved Discord message, REFLECT 2026-09-29 14:29 F1), leaves a question of sig's unanswered
(--check-answers for the turn, my_messages.unanswered: a question since the last reply that no reply of this turn
opens by answering, REFLECT 2026-09-29 17:32 F5, or an error trace he pasted that no reply quotes with the command
that produced it, 2026-09-30 15:13 F1), hands him work his standing grants cover (--check-asks: "Want me to queue
it...?", "Say if you want either", 17:32 F12, F13; MISSES 19) or puts work off naming no blocker and writing nothing
down (--check-asks' deferrals, 20:42 F2, F5), or sets itself a standing rule nothing but memory holds ("From now on,
...", --check-asks' PROMISE, REFLECT 2026-09-30 09:12 F3), or leaves a problem "worth watching" with no check started
(--check-asks' CAUTION, 15:13 F4), or runs a status or notice reply over the terse rule's line cap (--check-length,
15:13 F20), or started a study of opponents' games with no Sources line (check_briefs, 15:13 F5; the Agent call is in
the turn's transcript, so the hook sees its prompt, but only after it went out), or started a `--once` lane or
tools/orch/watch.sh detached (&, nohup, disown or stdout redirected), which refuses itself (check_detached, REFLECT
2026-10-01 15:21 F2: three times in 2 h; a PreToolUse hook could refuse the call before it ran). A Stop hook runs after the reply is
shown, so it can't hold the reply back: it asks for a short follow-up with only the missing status line, the
corrected time, the source line (after grepping docs/unsw/ and the saved Discord) or the answer, never a second copy
of the reply (REFLECT 2026-09-29 10:36 F1, F2; 12:53 F8: three blocked replies reached sig twice), and for an ask, to
do the work now or name what blocks it. The sources and answers checks read from my_messages.QUESTION_MINUTES before
the turn, as the question may have come before it, but flag only this turn's replies.

    installed in the untracked local settings (settings.local.json in the repo's .claude folder): {"hooks": {"Stop": [{"hooks": [{"type": "command",
        "command": ".venv/bin/python tools/orchestrator_stop_hook.py"}]}]}}

It reads the hook's JSON from stdin and does nothing (exit 0) unless its session_id is the one in
<loop home>/orchestrator-session, which the orchestrator writes; so sig's and every other session here are untouched.
It runs on every stop, so the checks read the transcript from a byte offset near its end (my_messages.tail_start),
not the whole file (234 MB on 29 Sep, about 0.6 s a check), and write nothing. The reply being stopped on isn't in
the transcript yet when the hook runs, so it comes from the input's last_assistant_message (pending_reply). A turn starts at sig's typed line or
the previous stop, whichever is later (turn_start), so a turn a child's message starts never re-checks replies an earlier stop
already checked (review of orch/builder-c, 29 Sep: 17:12's block came back at 17:14). A stop already blocked once
(stop_hook_active) passes, so a reply it can't fix never loops. Any error passes too."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

CHUNK = 4_000_000  # bytes read back from the transcript's end at a time


# The start of this turn: the time of the transcript's last message sig typed (a user line whose content is text, not
# a tool result) or of its previous stop (a stop_hook_summary line), whichever is later, or None. Checking from there
# covers every reply of this turn and never re-flags one an earlier stop checked, even when a child's message (isMeta)
# started this turn.
def turn_start(transcript: Path) -> datetime | None:
    last = None
    with transcript.open("rb") as handle:  # the tail only: an orchestrator's transcript runs to hundreds of MB
        handle.seek(max(0, transcript.stat().st_size - CHUNK))
        tail = handle.read().decode("utf-8", "replace")
    for line in tail.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content")
        typed = isinstance(content, str) or isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "text" for part in content)
        stopped = row.get("type") == "system" and row.get("subtype") == "stop_hook_summary"
        if (stopped or row.get("type") == "user" and typed and not row.get("isMeta")) and not row.get("isSidechain"):
            last = row.get("timestamp") or last
    return datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone() if last else None


# The transcript from byte `offset` (a line's start) on, for my_messages' checks: they only call open() on it. `last`,
# a transcript line for the reply being stopped on (pending_reply), follows the file's own lines.
@dataclass(frozen=True)
class Tail:
    path: Path
    offset: int
    last: str = ""

    def open(self) -> TextIO | WithLast:
        handle = self.path.open(encoding="utf-8", errors="replace")
        handle.seek(self.offset)
        return WithLast(handle, self.last) if self.last else handle


# A transcript handle whose lines end with one more (Tail.last).
@dataclass
class WithLast:
    handle: TextIO
    last: str

    def __enter__(self) -> WithLast:
        return self

    def __exit__(self, *_: object) -> None:
        self.handle.close()

    def __iter__(self) -> Iterator[str]:
        yield from self.handle
        yield self.last


# The reply Claude Code stops on, as a transcript line, when the transcript doesn't hold it yet, else "". The Stop hook
# runs before the session writes its final message to the transcript, so every check missed the reply it was stopping
# on (REFLECT 2026-09-30 09:12 F21: replayed on the transcript cut at each stop, all 29 status replies not opening
# with evalline's line would have blocked, and the one at 03:12:25 blocked as unanswered because the answer it
# stopped on, "Mostly no. ...", wasn't there yet). The hook's input carries it as last_assistant_message.
def pending_reply(transcript: Path, text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    import my_messages
    if (written := my_messages.last_reply(transcript)) and written[1].strip() == text.strip():
        return ""  # the file's latest reply is the one stopped on
    return json.dumps({"type": "assistant", "timestamp": datetime.now(timezone.utc).isoformat(),
                       "message": {"content": [{"type": "text", "text": text}]}}) + "\n"


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        from home import loop_home
        mine = (loop_home() / "orchestrator-session").read_text().strip()
        if not mine or event.get("session_id") != mine or event.get("stop_hook_active"):
            return 0
        transcript = Path(event["transcript_path"])
        at = turn_start(transcript)
        if at is None:
            return 0
        import my_messages
        asked_from = at - timedelta(minutes=my_messages.QUESTION_MINUTES)  # a question asked before this turn
        path = Tail(transcript, my_messages.tail_start(transcript, asked_from),
                    pending_reply(transcript, event.get("last_assistant_message")))
        status = [*my_messages.check_evalline(path, at), *my_messages.check_times(path, at),
                  *my_messages.check_sources(path, asked_from, flag_from=at)]
        unanswered = list(my_messages.unanswered(path, asked_from, at))
        asks = list(my_messages.check_asks(path, at, blocking=True))
        long = list(my_messages.check_length(path, at, my_messages.MEMORY))
        briefs = list(my_messages.check_briefs(path, at))
        detached = list(my_messages.check_detached(path, at))
        if reason := block_reason(status, unanswered, asks, long, briefs, detached):
            print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:  # a broken hook never stops the session
        return 0
    return 0


# What the blocked stop tells the session to do about each kind of problem found, or "" when none was.
def block_reason(status: list[str], unanswered: list[str], asks: list[str], long: list[str], briefs: list[str],
                 detached: list[str]) -> str:
    import my_messages
    parts = []
    if detached:
        parts.append("You started a lane or the watcher detached, and it refuses to run that way, so nothing is running "
                     "(REFLECT 2026-10-01 15:21 F2, MISSES 66): start it again now as a background task "
                     "(run_in_background, with no &, nohup, disown or output redirection; `uv run loop/driver.py --once "
                     "--parent <name>` or `bash tools/orch/watch.sh`), and say so in one short line:\n"
                     + "\n".join(detached))
    if status:
        parts.append("Send one short follow-up with only what's missing (the line `.venv/bin/python loop/evalline.py` "
                     "prints, the corrected hh:mm read from `date`, or, for an answer about the game or site, the "
                     "source line: grep docs/unsw/ and the saved Discord (<loop home>/discord/) for its key terms, then "
                     "quote the hit with its path, or say \"no hit in docs/unsw or Discord\", and correct the answer if "
                     "the hit contradicts it; or, for a capacity or finish estimate, the figure again from evalline.py "
                     "or a results.jsonl rate run now, naming which):\n" + "\n".join(status))
    traces = [line for line in unanswered if " pasted an error trace " in line]
    if questions := [line for line in unanswered if line not in traces]:
        parts.append("sig's question has no answer yet: answer it in a short follow-up that opens with the answer "
                     "(CLAUDE.md's \"Answer every question\"):\n" + "\n".join(questions))
    if traces:
        parts.append("sig pasted an error trace and no reply has dealt with it: read what failed first, then in a short "
                     "follow-up quote its failing line and name, in backticks, the command that produced it (which "
                     "hook or tool, from the trace or its settings), and say what you did about it (REFLECT "
                     "2026-09-30 15:13 F1):\n" + "\n".join(traces))
    deferred = [line for line in asks if " defers work, " in line]
    rules = [line for line in asks if " sets a standing rule, " in line]
    cautions = [line for line in asks if " cautions without a check, " in line]
    options = [line for line in asks if " waits on sig, but its message recommends nothing " in line]
    if handed := [line for line in asks if line not in deferred + rules + cautions + options]:
        parts.append("Your reply hands sig work his standing grants cover: do it now and say \"starting it now\", or "
                     "name what blocks it (a permission he hasn't granted: outward, money, destructive, his accounts), "
                     "in a short follow-up (CLAUDE.md's \"Before anything goes on sig's list\"):\n" + "\n".join(handed))
    if options:
        parts.append("Your reply leaves sig an option with no recommendation: say in one short line which you recommend "
                     "and why, ending \"starting it now\" if his grants cover it (REFLECT 2026-09-30 15:13 F14):\n"
                     + "\n".join(options))
    if deferred:
        parts.append("Your reply puts work off with no blocker named and nothing written down: start it now (a "
                     "subagent), or name what blocks it and add its reminders.jsonl (`loop/health.py --remind`) or "
                     "owed.md line, then say which in one short line (CLAUDE.md's \"Waiting is a cost\"):\n"
                     + "\n".join(deferred))
    if rules:
        parts.append("Your reply sets yourself a standing rule that only memory holds, and memory-only rules broke "
                     "three times on 30 Sep (MISSES 66): name the code, check or hook that enforces it, or add its "
                     "owed.md line with its builder, in one short line:\n" + "\n".join(rules))
    if cautions:
        parts.append("Your reply leaves a problem to watch instead of checking it (CLAUDE.md's \"A caution is not a "
                     "fix\", MISSES 9): start the check now (a subagent or the command that reads it) and name it in "
                     "one short line, or add its reminders.jsonl line if it can only be read later:\n"
                     + "\n".join(cautions))
    if briefs:
        parts.append("A study you started reads opponents' games without the Sources rules (MISSES 103, REFLECT "
                     "2026-09-30 15:13 F5): SendMessage it now: \"Sources: ranked games only, by submission id, "
                     "loop/decoys.py's flags applied; report how many games you kept and dropped, and why\", and say so "
                     "in one short line:\n" + "\n".join(briefs))
    if long:
        parts.append("Your reply ran over the line cap while sig's terse rule stands (quota-habits memory note; REFLECT "
                     "2026-09-30 15:13 F20): don't resend or shorten it now; if nothing else here needs a follow-up, "
                     "end with no text at all, and keep the next status or notice reply within "
                     f"{my_messages.LENGTH_LINES} lines besides evalline's own:\n" + "\n".join(long))
    return "Your reply is already shown, so don't resend it.\n" + "\n".join(parts) if parts else ""


if __name__ == "__main__":
    sys.exit(main())
