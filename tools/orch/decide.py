#!/usr/bin/env python3
"""Appends one decision block to ~/.bc-loop/decisions.md (or discord/digests.md with --digest), headed
"## Orchestrator's decisions on <name> (<clock time>)", then runs decisions.pending. Refuses a guessed time
("08:2x", MISSES 26: three slips on 2026-09-28) anywhere in the text. Usage: .venv/bin/python tools/orch/decide.py [--digest] <name> < body. Bullets read
"- ID: verdict: why" (acted, declined, check started, keep, kept, edited, merged, dropped); an applied/ file's
items go under its path as the name; a result not kept gets "- Rule check: E####: ..." under a name naming it."""
import re, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
digest = args[:1] == ["--digest"]
name = " ".join(args[1:] if digest else args)
body = sys.stdin.read().strip("\n")
if not body.strip() or not name or name.startswith("-"):
    sys.exit("refused: decide.py needs a name and a body on stdin (nothing written)")
text = f"\n## Orchestrator's decisions on {name} ({time.strftime('%Y-%m-%d %H:%M')})\n{body}\n"
if bad := re.findall(r"\b\d{1,2}:\d?x\b", text):
    sys.exit(f"refused: guessed time {bad}; write the clock's time; sig's and our messages are logged to the second (tools/my_messages.py --since ...), so never '(minute not logged)'")
repo = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, f"{repo}/loop")
import home  # noqa: E402

path = home.loop_home() / ("discord/digests.md" if digest else "decisions.md")
with path.open("a") as handle:
    handle.write(text)
subprocess.run([f"{repo}/.venv/bin/python", "-c", "import sys; sys.path.insert(0,'loop'); import decisions, home; "
                "left=[(n,m[:3]) for r,rec,n,m in decisions.pending(home.loop_home())]; print(left or 'none pending')"],
               cwd=repo, check=False)
