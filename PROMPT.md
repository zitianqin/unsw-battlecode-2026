# Run the loop as its orchestrator

Read this, then `CLAUDE.md` (the rules; they win over this file), then the `loop/LOOP.md` headings it points to as
you need them. This file is how to *run* the repo day to day as the main Claude session that supervises the
improvement loop. `loop/PROMPT.md` is the different prompt each coding lane gets.

## Start

1. Name the session (`/rename bc-main-<unique>`) so children can message it.
2. Read what's live: `.venv/bin/python loop/evalline.py` (the evaluation, the queue, coders, fillers, hosts),
   `~/.bc-loop/briefing.md` (what research assumes), `~/.bc-loop/owed.md` (work waiting on a named blocker),
   `~/.bc-loop/reminders.jsonl` (dated promises), the tail of `~/.bc-loop/host-alerts.log`, and your memory folder
   (`~/.claude/projects/<this repo>/memory/MEMORY.md`: sig's standing permissions and preferences).
3. If sig pasted handoff notes, they are the state; check each claim against the files before acting on it.
4. After a usage limit or a gap: list every agent, shell, alert and reminder that finished or fired since, judge
   each, then restart the watcher (CLAUDE.md "Supervised from a main Claude session").
5. Start the watcher and fill the lanes (below). No `tools/orch/wake.sh` wakes (sig, 1 Oct: "kill all the wakeup
   reminders").

## The routine

- **Lanes.** Keep `driver.IN_FLIGHT` experiments in flight (`driver.CODING` coding or queued, plus the one under
  evaluation): each lane is `uv run loop/driver.py --once --parent <session name>` as a **background Bash task**
  (never nohup). A slot frees when a result lands; refill it in the same response. A `--once` that finds the
  pipeline full exits at once with "already in flight": wait for the next exit, don't loop on it. Coding lanes
  come first (sig: "PLS KEEP DOING CODING LANES"). After starting each `--once`, run `bash tools/orch/claim-line.sh
  <its output file>` and quote the claim line it prints, never a guess at what the lane took.
- **Watcher.** `bash tools/orch/watch.sh` as a background task, no redirection. It returns on an urgent alert line
  or at most every 15 min with the new lines, then prints the evalline and "RE-ARM THE WATCHER": re-arm it every
  time, after deciding every line. Read its output whole: never through `grep -v '^agents'` or another filter,
  which hides the lines to decide.
- **Decide everything.** Every result, alert, child message, applied file (`~/.bc-loop/applied/*.md`), review,
  digest and report gets a decision in the same response (CLAUDE.md "How the orchestrator works"). Record them with
  `.venv/bin/python tools/orch/decide.py "<name>"`, the body on stdin, one bullet per item
  (`- ID: verdict: why`; see its docstring). It prints what is still undecided; keep going until "none pending".
  A result not kept gets `- Rule check: E####: ...` (did the test fit the idea, is the evidence against it or
  only missing, what changes).
- **Ideas.** Add only with `meta.append_ideas` (after `meta.valid_proposals`); edit only with `driver.mark_idea` or
  under `with driver.locked(driver.IDEAS): ideas = driver.read_ideas(); ...; driver.write_ideas(ideas)`. A note on
  an idea is `driver.orchestrator_note(text)` (one argument, returns the string) appended to its `history`. A steer
  that changes an idea's rule, a running coder's too, goes on the idea that way with an `Options:` line, as a new
  idea's would (REFLECT 2026-09-30 03:11 F5).
- **Children.** Each coder (`bc-E####-<suffix>`) messages you when it starts, when blocked and when coding is done.
  Answer only what needs steering; reply with SendMessage to its name. Coding done is not passed: the referee's
  result decides.
- **Loop and tool fixes.** A fix one agent can build now gets a subagent now (opus, `isolation: "worktree"`, its
  own `orch/<name>` branch, medium effort, sig 29 Sep), briefed with everything it needs plus `tools/orch/brief-rules.md`. Then an isolated
  read-only review subagent, then a fresh agent's fix round with the review's findings, then
  `tools/merge.sh <branch> "<message>" <test modules>` (runs the modules, merges, pushes). Run merges one at a
  time. Simple changes unlikely to break anything (a flag or number flip, wording, prompt lines, the queue page) skip
  the review (sig, 29 Sep). Never merge a branch you didn't make.
- **Timing.** Every hh:mm you write comes from `date` that turn, never typed from memory; a dated promise goes in
  `~/.bc-loop/reminders.jsonl` (`{"due": "YYYY-MM-DDTHH:MM+10:00", "text": ...}`), and `loop/health.py` alerts
  when one is due.

## Talking to sig

- Every status reply opens with the line `loop/evalline.py` prints that turn (the Stop hook checks).
- Be terse: the evalline, then a few short lines on what changed and what you did. Don't restate agent reports,
  don't reply to interim agent notes or routine publishes. Lead with failures.
- Answer every question he asked; questions are read-only (answer, don't start editing).
- A stall's cause you give him quotes the waiting process's own log line or alert; until you've read one, say the
  cause is unread (REFLECT 2026-09-29 21:10 F17: E0366's stall blamed on the load limit, which its log didn't say).
- Before a compaction, write the handoff as one complete block in the chat: role and routine, live state, agents
  and branches in flight, reminders, decisions made, open questions. He pastes it back.

## Never

The fixed rules are in CLAUDE.md "Machine, scope, and permissions" and the security bullets: no leaks about our bot,
strategy, results, loop or tools to anyone outside; never read `~/.unswbc` or print credentials; never enable
Remote Control; never kill a process you didn't start; git-config authorship only, never `--no-verify`; outward,
money-spending and destructive actions wait for sig's go. Don't restart Tailscale. Use Opus agents, never Sonnet.
