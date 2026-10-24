"""Claude Code UserPromptSubmit hook for the orchestrating session only: when sig types, and his terse rule stands
(my_messages.TERSE in the quota-habits memory note), a last reply over my_messages.LENGTH_LINES lines (evalline's
own lines not counted, as --check-length counts them: my_messages.counted_lines) gets a note in the new turn's
context naming its line count, so the next reply is short (REFLECT 2026-09-30 09:12 F20: 37 replies over the cap in
six hours, the length block decided at 03:29 never built). A note, never a block: sig's message always goes through
(the Stop hook blocks a long status or notice reply itself, 15:13 F20).

When sig is back after more than AWAY_HOURS since his previous message (my_messages.last_typed), the note also holds
every result recorded since then as `loop/evalline.py --results-since` prints them (evalline.results_since), for the
reply to open with after evalline's line (REFLECT 2026-10-01 15:21 F10: on 1 Oct at 13:44 he asked for the results
since 09:00, then about two discards the list hadn't made clear; MISSES 109 records four such asks on 30 Sep). Those
lines count as evalline's own against the line cap (my_messages.EVALLINE_LINE).

    installed beside the Stop hook in the untracked local settings (settings.local.json in the repo's .claude
    folder): {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
        "command": ".venv/bin/python tools/orchestrator_prompt_hook.py"}]}]}}

As the Stop hook does, it reads the hook's JSON from stdin, does nothing unless its session_id is the one in
<loop home>/orchestrator-session, reads only the transcript's tail, writes nothing and passes on any error."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

AWAY_HOURS = 1  # how long since sig's previous message makes a new one a return that gets the results digest


# The note for a last reply of `lines` non-blank lines sent at `at` (hh:mm:ss), or "" within the cap.
def note(lines: int, at: str, cap: int, memory: str) -> str:
    if lines <= cap:
        return ""
    return (f"Your last reply ({at}) ran {lines} lines besides evalline's own, over the {cap}-line cap while sig's "
            f"terse rule stands ({memory}): keep this one to evalline's lines plus at most {cap} short lines.")


# The digest note for sig's return at `now` after his previous message at `last` (None: none found), with `results`
# (evalline.results_since's lines), or "" within AWAY_HOURS.
def digest(last: datetime | None, now: datetime, results: str) -> str:
    if last is None or now - last <= timedelta(hours=AWAY_HOURS):
        return ""
    return (f"sig is back after {(now - last).total_seconds() / 3600:.1f} h away (his previous message {last:%H:%M}): "
            f"after evalline's line, open your reply with every result since then, one line each, as `.venv/bin/python "
            f"loop/evalline.py --results-since {last.isoformat(timespec='minutes')}` prints them now:\n{results}")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        from home import loop_home
        mine = (loop_home() / "orchestrator-session").read_text().strip()
        if not mine or event.get("session_id") != mine:
            return 0
        import my_messages
        transcript = Path(event["transcript_path"])
        notes = []
        memory = my_messages.MEMORY.read_text() if my_messages.MEMORY.exists() else ""
        if my_messages.TERSE.search(memory) and (last := my_messages.last_reply(transcript)):
            at, text = last
            notes.append(note(len(my_messages.counted_lines(text)), f"{at:%H:%M:%S}", my_messages.LENGTH_LINES,
                              my_messages.MEMORY.name))
        now = datetime.now().astimezone()
        if (back := my_messages.last_typed(transcript, str(event.get("prompt") or ""))) \
                and now - back > timedelta(hours=AWAY_HOURS):
            import evalline
            notes.append(digest(back, now, evalline.results_since(loop_home(), back.timestamp())))
        if context := "\n\n".join(part for part in notes if part):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": context}}))
    except Exception:  # a broken hook never holds up sig's message
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
