# Orchestrator runbook

How a supervising main Claude session runs the loop, step by step, with the exact commands. The rules themselves are
in CLAUDE.md ("Supervised from a main Claude session") and the mechanics in loop/LOOP.md ("Supervised mode details");
this file is the order of operations and the tools that are only in `tools/orch/`. Run everything from the main
checkout. Commands marked **background** go through the Bash tool's `run_in_background` with no `&`, `nohup`,
`disown` or output redirection: a detached lane or watcher refuses itself (`LANE REFUSED`, `WATCHER REFUSED`) and
the Stop hook flags the command.

## Start of a session

1. Name the session `bc-main-<unique>` (`claude --name`, or `/rename`).
2. Read `~/.bc-loop/briefing.md`, the last handoff notes, `~/.bc-loop/reminders.jsonl` (due items) and
   `.venv/bin/python loop/evalline.py` (what is evaluating, queued, coding, live).
3. If `~/.bc-loop/STOP` exists, the loop is paused: leave it until sig asks to resume (see Resume below).
4. Arm the watcher (**background**): `bash tools/orch/watch.sh`.
5. Fill the lanes (**background**, one call per lane, up to `driver.IN_FLIGHT` in flight):
   `uv run loop/driver.py --once --parent bc-main-<unique>; echo "once exit $?"; date +%H:%M`.
   Then `bash tools/orch/claim-line.sh <that task's output file>` prints what the lane claimed.

## Every firing

Each completion (a lane's Bash task, the watcher, a subagent, a child's `SendMessage`) gets a decision in the same
turn, written with `tools/orch/decide.py`:

```sh
printf '%s\n' "- E0123: kept, new champion v0100 (...)" | .venv/bin/python tools/orch/decide.py E0123
printf '%s\n' "- E0124: discarded, SPRT H0, -2.0 over 900 pairs." \
  "- Rule check: E0124: did the test fit; is the evidence against it or missing; what changes" \
  | .venv/bin/python tools/orch/decide.py E0124
printf '%s\n' "- I1234: acted: kept P2 (why)" | .venv/bin/python tools/orch/decide.py applied/2026-10-01-1200-postmortem-E0124.md
```

- Bullets read `- ID: verdict: why`; verdicts: acted, declined, check started, keep, kept, edited, merged, dropped.
- A result not kept needs a `- Rule check: E####: ...` line. An `applied/` file's items go under its path as the name.
- `decide.py` prints what is still undecided (`decisions.pending`): keep going until it says `none pending`.
- Child messages (`bc-E####-...`): a start notice needs only a decision line. A hand-in or question gets a reply
  with `SendMessage` to the message's `from=` address; check that the send succeeded (a stale socket means the coder
  exited).
- After each watcher firing, read its output file for new alert lines, decide them, and **re-arm** it
  (**background**, as above).
- After a lane's Bash task completes, read its result (`results.jsonl` or the lane's output), decide it, decide its
  post-mortem or follow-on `applied/` file when it lands, and start a new lane for the freed slot.

## Status replies

- Open with what `.venv/bin/python loop/evalline.py` prints that turn, and read every hh:mm from `date` that turn.
  A finish estimate names evalline or a results.jsonl rate run that turn.
- While sig's terse rule stands: evalline's line plus at most 3 short lines.
- A deferral to a clock time needs a reminder: `.venv/bin/python loop/health.py --remind <ISO time> "<text>"`.
- The Stop hook (`tools/orchestrator_stop_hook.py`) enforces these and asks for a short follow-up when one is missed;
  the prompt hook (`tools/orchestrator_prompt_hook.py`) adds context such as a results digest.

## Queue control

- Put a waiting candidate first: `uv run loop/driver.py --first E#### --rule "<why>"`. Within the front tier the
  lock takes tickets by coding-done time (the timestamp in `~/.bc-loop/evaluation-queue/<rank>-<time>-E####.json`),
  so ordering two front-tier candidates means renaming one's timestamp. A waiting driver can reset the name, so
  check the order again after its next poll.
- Take a candidate out without testing it: `uv run loop/driver.py --withdraw E#### --rule "<why>"` (recorded
  discarded, gets a post-mortem).
- Re-run an error that wasn't the candidate's: `uv run loop/driver.py --reevaluate E#### --rule "<why>"`.
- Mark an idea next, or add a note on it, only through `driver.mark_idea` / `driver.orchestrator_note`; add ideas
  only through `meta.valid_proposals` then `meta.append_ideas` (the proposal needs an `Options:` line with 3+ numbered
  remedies).

## Pause and resume

- Pause: write `~/.bc-loop/STOP` (one line saying why). Drivers stop after their current step. An evaluation playing
  finishes. A waiting or just-coded candidate is recorded `blocked, ... candidate preserved` and drops its queue
  ticket. Coders keep coding and are blocked the same way when they hand in. Publishing and the upkeep stop too.
- While STOP stands, a held candidate's idea can still be claimed again (a known fault: E0521/E0522 on 1 Oct). To
  start one specific claim during a pause, clear `next` on every held candidate's idea first, remove STOP, start one
  lane, wait for its claim line, then write STOP again.
- Resume (only on sig's word): remove STOP (`uv run loop/driver.py --resume` also starts a driver in the
  foreground, so plain `rm ~/.bc-loop/STOP` is usual), re-arm the watcher and refill the lanes. A blocked result
  returns its idea to `todo` (`driver.idea_status`), and its work is saved as `wip/E####`, so the next claim of that
  idea starts a new experiment whose coder finds `origin/wip/E####` and hands it in. (`driver.py --handed` is the
  driver's internal hand-off to newer loop code, not a resume command.) Before resuming, check each held
  candidate's idea is `todo` and has a `wip/E####` branch: a duplicate claim's no-code result can mark it `tried`
  (I459 on 1 Oct), and a candidate committed only in its clone needs saving
  (`git fetch <clone> <commit> && git branch wip/E#### FETCH_HEAD`). A candidate that handed back no code under STOP
  is marked `tried` by hand so it isn't claimed again.

## Merging the orchestrator's own branches

Make the change on a branch in a worktree, run its focused tests (`tools/gated_test.sh`), then
`tools/merge.sh <branch> "<message>" <test modules>`. A loop or hook change beyond a simple one gets a separate,
isolated review subagent first. `tools/orch/brief-rules.md` holds the standing rules to append to every subagent
brief.

## Never

Kill a process you didn't start; read `~/.unswbc`; enable Remote Control; arm `tools/orch/wake.sh` wakes; pass
`--author`, `-c user.*` or `--no-verify`; run evaluation games on this Mac; or upload while a publishing hold stands.
