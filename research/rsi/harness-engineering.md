# Harness engineering for a long-running autonomous bot-improvement loop

Written 2026-09-23. Item 5 of the RSI research brief. Part A is what the sources say (VERIFIED = read this
session, with URL). Part B turns it into a concrete harness for the UNSW Battlecode bot (INFERENCE: my design,
not yet tested). Claude Code flags were read from the live docs on 2026-09-23; several need recent versions
(noted inline).

---

## Part A — What the sources say

### A1. Anthropic, "Building effective agents" (19 Dec 2024) — VERIFIED

https://www.anthropic.com/engineering/building-effective-agents
- "find the simplest solution possible, and only increase complexity when needed."
- *Workflows* = "LLMs and tools are orchestrated through predefined code paths"; *agents* = "LLMs dynamically
  direct their own processes and tool usage".
- **Evaluator-optimizer** fits "when we have clear evaluation criteria, and when iterative refinement provides
  measurable value" — this is our case, with the game engine as evaluator.
- Agents need "ground truth" from the environment at each step, and "stopping conditions (such as a maximum
  number of iterations)"; "extensive testing in sandboxed environments, along with the appropriate guardrails".
- Tool design ("agent-computer interface"): apply poka-yoke; example — tools changed "to always require absolute
  filepaths" after the model got relative paths wrong. For us: the eval script should take no fiddly arguments
  and print one easy-to-grep summary.

### A2. Anthropic, "Effective harnesses for long-running agents" (26 Nov 2025) — VERIFIED

https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- "compaction isn't sufficient. Out of the box, even a frontier coding model like Opus 4.5 running on the Claude
  Agent SDK in a loop across multiple context windows will fall short".
- Two roles: an **initializer agent** (first run: sets up `init.sh`, `claude-progress.txt`, a
  `feature_list.json` of 200+ features each with `"passes": false`, first git commit) and a **coding agent**
  (every later session).
- Every coding session starts: "Run pwd", "Read the git logs and progress files to get up to speed", "Read the
  features list file and choose the highest-priority feature that's not yet done", start the app, run a basic
  end-to-end check *before* new work.
- One feature per session; leave "the environment in a clean state at the end of a session"; commit with a
  descriptive message and update the progress file; git lets the model "revert bad code changes and recover
  working states".
- JSON over Markdown for the task list: "the model is less likely to inappropriately change or overwrite JSON
  files compared to Markdown files". Instruction: "It is unacceptable to remove or edit tests".
- Failure modes and fixes: declaring victory early → explicit feature list; one-shotting → one feature per
  session; marking features done without testing → mandatory end-to-end testing "as a human user would".

### A3. Anthropic, "Building a C compiler with a team of parallel Claudes" (Carlini, 5 Feb 2026) — VERIFIED

https://www.anthropic.com/engineering/building-c-compiler
- Loop, per agent, in its own Docker container:
  ```bash
  while true; do
      COMMIT=$(git rev-parse --short=6 HEAD)
      LOGFILE="agent_logs/agent_${COMMIT}.log"
      claude --dangerously-skip-permissions -p "$(cat AGENT_PROMPT.md)" --model claude-opus-X-Y &> "$LOGFILE"
  done
  ```
- Bare upstream repo mounted at `/upstream`; each agent clones to `/workspace`, pulls, merges, pushes. **Task
  locks** = a text file in `current_tasks/` (e.g. `current_tasks/parse_if_statement.txt`); git conflicts force a
  second agent to pick something else.
- 16 agents, ~2,000 sessions, two weeks, "just under $20,000", 100k-line Rust compiler that builds Linux 6.9.
- "Claude will work autonomously to solve whatever problem I give it. So it's important that the task verifier is
  nearly perfect, otherwise Claude will solve the wrong problem."
- Test harness rules for agents: don't print "thousands of useless bytes"; on error print `ERROR` and the reason
  on the same line so grep finds it; print progress rarely; a default `--fast` option that runs a 1% or 10%
  random sample, "deterministic per-agent but random across VMs" (agents are time-blind).
- "New features and bugfixes frequently broke existing functionality" → a CI pipeline "so that new commits can't
  break existing code". Specialist agents: one de-duplicates code, one improves performance, others critique
  design and write docs. "maintain extensive READMEs and progress files that should be updated frequently".
- Failure: all agents hit the same Linux-kernel bug and overwrote each other → fixed with an **oracle** (GCC) so
  work could split by file. Also: an agent once ran `pkill -9 bash` and killed its own loop; "Claude simply cheats
  here and calls out to GCC for this phase" (for a phase it couldn't do).

### A4. Geoffrey Huntley, "Ralph Wiggum as a software engineer" (14 Jul 2025) — VERIFIED

https://ghuntley.com/ralph/
- The loop: `while :; do cat PROMPT.md | claude-code ; done` (in current Claude Code: `claude -p`).
- **One thing per loop.** State lives in files: `fix_plan.md` (prioritised TODO), `specs/*`, `AGENT.md` (how to
  build/test, learned procedures).
- Parallel subagents for reading/searching, but "only 1 subagent for build/tests" (serialise validation).
- **Backpressure:** after a change, run the tests for that unit; type checks and static analysis reject bad
  generations.
- Tuning "like a guitar": when Ralph makes a recurring mistake, add a "sign" to the prompt (e.g. "don't assume
  not implemented" — it often re-implements things that exist).
- Failure modes: quality drops as context fills (~147k–152k tokens reported); placeholder implementations;
  compounding errors → `git reset --hard` and restart.

### A5. OpenAI, "Harness engineering" (Lopopolo, 11 Feb 2026) — via secondary summary, original 403'd

Original https://openai.com/index/harness-engineering/ returned HTTP 403 to WebFetch; details below are from
https://businessdatasolutions.github.io/ai-wiki/sources/2026-02-11-lopopolo-codex-harness-engineering
(PARTIALLY VERIFIED — secondary source).
- ~1M lines, ~1,500 PRs, 7 engineers, 5 months, "zero manually-written code".
- A ~100-line top-level `AGENTS.md` as a *map*, pointing to deeper docs; "Anything it can't access in-context
  while running effectively doesn't exist" → the repo is the system of record.
- Rules enforced **mechanically** (custom linters, structural tests), not by prose.
- "Agent legibility": per-worktree ephemeral logs/metrics so the agent can inspect behaviour directly.
- Background "garbage collection" agents scan for drift and open cleanup PRs.

### A6. Cursor, "Scaling long-running autonomous coding" (14 Jan 2026) — VERIFIED

https://cursor.com/blog/scaling-agents
- Shared file + locks failed: "Agents would hold locks for too long, or forget to release them"; "Twenty agents
  would slow down to the effective throughput of two or three". Optimistic concurrency made agents
  "risk-averse" — they "made small, safe changes instead".
- What worked: **planners** create tasks, **workers** complete them, a **judge** decides whether to continue.
- "A surprising amount of the system's behavior comes down to how we prompt the agents". Model note (theirs):
  "Opus 4.5 tends to stop earlier and take shortcuts" vs GPT-5.2 for long autonomous work.

### A7. Karpathy autoresearch and CORAL — see survey.md §1.1 and §2.13

- autoresearch: one editable file, read-only evaluator, `results.tsv` kept *untracked* so resets don't erase
  the log, `> run.log 2>&1` then `grep` a single metric line, "NEVER STOP", simplicity criterion.
- CORAL: worktree per agent, shared `.coral/public/` notes/attempts/skills, grader daemon scoring every commit,
  heartbeat prompts, and agents run as an unprivileged user that cannot read `.coral/private/`.

### A8. Claude Code headless mode and Agent SDK (docs read 2026-09-23) — VERIFIED

Pages: https://code.claude.com/docs/en/headless.md , cli-reference.md, permissions.md, sandboxing.md, hooks.md,
agent-sdk/python.md.

**Headless (`claude -p`)**
```bash
claude --bare -p "$(cat PROMPT.md)" \
  --model opus --effort high \
  --allowedTools "Read,Edit,Bash(./eval/quick.sh *),Bash(git diff *),Bash(git commit *)" \
  --permission-mode dontAsk \
  --max-turns 200 --max-budget-usd 20 \
  --output-format json > logs/session_$(date +%s).json
jq -r '.result, .total_cost_usd, .session_id' logs/session_*.json
```
- `--bare`: skip auto-discovery of hooks, skills, subagents, plugins, MCP, auto memory and CLAUDE.md;
  "recommended mode for scripted and SDK calls, and will become the default for `-p`". Without it, `-p` runs the
  project's `.claude/settings.json` hooks and `.mcp.json` servers with no trust prompt. (If you *want* your
  hooks, don't pass `--bare`, or pass them via `--settings`.)
- `--output-format json` → `result`, `session_id`, `total_cost_usd`; `stream-json --verbose` for live events.
  `--json-schema '<schema>'` → validated `structured_output` (good for a "report" step).
- `--continue` / `--resume <session_id>` to continue a session.
- `--max-turns N` (exits with an error at the limit), `--max-budget-usd X` (subagent spend counts).
- `--append-system-prompt[-file]`, `--system-prompt[-file]`.
- `--worktree/-w <name>`: start in `<repo>/.claude/worktrees/<name>`.
- `--settings <file|json>`, `--add-dir`, `--agents '<json>'` (define subagents inline), `--disallowedTools`,
  `--tools "Bash,Edit,Read"` (restrict the built-in tool set), `--no-session-persistence`, `--fallback-model`.
- `--restricted`: "Use it when an evaluation harness drives `claude` on a shared machine" — removes built-in
  tools that run commands unless named in `--tools`.
- SIGTERM exits 143 and drops the in-progress turn; send SIGINT to end the turn cleanly.
- Background Bash tasks are killed ~5 s after the result; background subagents keep `-p` open until they finish.

**Permission modes for unattended runs**: `dontAsk` (deny anything not pre-allowed — best for a locked-down
loop), `acceptEdits`, `auto` (a classifier reviews actions), plus `--permission-prompts none` (v2.1.259+) so
nothing blocks waiting on a human. `--dangerously-skip-permissions` is what the C-compiler run used, *inside a
container*.

**Permission rules are not a security boundary.** Rules evaluate deny → ask → allow. `Edit(path)` / `Read(path)`
rules gate the file tools, and a `Read` deny also blocks Edit/Write on that path. But the docs say a Bash deny
rule "covers the invocation Claude usually produces and isn't a security boundary around the program" — it
doesn't match `sh -c` or other forms. **For the evaluator you need the OS sandbox or a separate user/container.**

**OS-level sandbox** (Seatbelt on macOS, bubblewrap on Linux):
```json
{ "sandbox": { "enabled": true, "allowUnsandboxedCommands": false,
               "filesystem": { "denyWrite": ["./eval", "./engine", "./opponents", "./maps"] } } }
```
"These paths are enforced at the OS level, so all commands running inside the sandbox, including their child
processes, respect them." `allowUnsandboxedCommands: false` disables the `dangerouslyDisableSandbox` escape hatch.

**Hooks** (settings.json `hooks`):
- `PreToolUse` exit code 2 → blocks the tool call (stderr goes to Claude). Use it to reject edits outside
  `bot/` and any command touching `eval/`.
- `Stop` exit code 2 → "Prevents Claude from stopping, continues the conversation"; input has
  `stop_hook_active`; Claude Code overrides after **8 consecutive blocks**. This is an in-session Ralph loop; an
  external `while` loop with fresh context is more robust.
- `SubagentStop`, `SessionStart`, `PostToolBatch` also exist. Exit 1 is *non-blocking* — policy hooks must use
  exit 2.

**Agent SDK (Python)**: `pip install claude-agent-sdk`; `query(prompt, options=ClaudeAgentOptions(...))` yields
messages; options include `cwd`, `model`, `allowed_tools`, `disallowed_tools`, `tools`, `permission_mode`,
`max_turns`, `max_budget_usd`, `system_prompt`, `settings`, `add_dirs`, `resume`, `output_format`,
`enable_file_checkpointing`. Use the SDK when the orchestrator needs to react to messages (e.g. kill a session
that edits a forbidden path); use `claude -p` in a shell loop otherwise.

---

## Part B — A concrete harness for the Battlecode loop (INFERENCE)

### B1. Principles distilled from Part A

1. **Fresh context per iteration, state in files.** (A2, A3, A4.) Each iteration is one `claude -p` session
   with one hypothesis. Long sessions degrade and compaction "isn't sufficient".
2. **The evaluator is not the agent's to touch** (survey.md: STOP 0.42% sandbox disabling, DGM token-log
   deletion, AI Scientist extending its own time limit; A3 "verifier nearly perfect"). Enforce with the OS
   sandbox or a separate container/user, not with prose or Bash deny rules.
3. **The harness, not the agent, decides keep/discard and writes the results log.** Agents' self-reports are
   unreliable (AI Scientist: hallucinated results).
4. **Quiet, greppable output** (A3, A7): one summary line per eval, `ERROR:` lines, full logs to files.
5. **Deterministic quick subset + expensive full eval** (A3 `--fast`, AlphaEvolve cascade, DGM 10→60→200).
6. **Backpressure before games** (A4): compile, CPU budget check, crash/timeout smoke test.
7. **Parallelism by worktree/container, serialized evaluation per machine** (A4 "only 1 subagent for
   build/tests"; our machine is shared).
8. **Git as memory, with an untracked or separate results log** so resets never erase history (A7).
9. **Signs over rewrites**: when the agent repeats a mistake, add one line to the playbook (A4).
10. **Watchdog** — a supervisor that kills a session over its turn/budget/time limit (A8 `--max-turns`,
    `--max-budget-usd`; SICA's overseer).

### B2. Repository layout

```
bot/                      # the ONLY tree the agent may edit (C++ source, params.json)
eval/                     # frozen: build.sh, quick.sh, full.sh, sprt.py, cpu_check.sh   (OS-level denyWrite)
engine/                   # pinned unswbc engine/toolkit version                          (denyWrite)
opponents/                # frozen binaries: champions/, exploiters/, replay_clones/, starter/ (denyWrite)
maps/train/  maps/holdout/  # holdout is also denyRead for the agent
loop/
  PROMPT.md               # iteration prompt (human/outer-loop edited, not by the inner agent)
  PLAYBOOK.md             # distilled lessons, "signs" (outer loop edits; inner agent may PROPOSE additions)
  ideas.json              # backlog: {id, hypothesis, status: todo|trying|kept|discarded, parent, notes}
  results.tsv             # appended by the HARNESS only: commit, parent, elo_delta, ci, games, status, desc
  experiments/<id>/       # per-iteration: diff, eval summary, loss replay notes (harness-written)
```

### B3. One iteration (driver script, shell or Agent SDK)

```bash
# loop/run.sh — one iteration; an outer `while` calls it. Human starts it, human stops it.
set -euo pipefail
ID=$(date +%Y%m%d-%H%M%S); PARENT=$(git rev-parse HEAD)
git worktree add -q "wt/$ID" "$PARENT"          # isolate the attempt
( cd "wt/$ID" && claude --bare -p "$(cat ../../loop/PROMPT.md)" \
    --settings ../../loop/agent-settings.json \   # sandbox + denyWrite eval/engine/opponents/maps
    --allowedTools "Read,Edit,Bash(./eval/quick.sh),Bash(git diff *),Bash(git commit *)" \
    --permission-mode dontAsk --max-turns 150 --max-budget-usd 15 \
    --output-format json > "../../loop/experiments/$ID.session.json" ) || true
# Harness (not the agent) evaluates the committed candidate:
./eval/build.sh "wt/$ID" && ./eval/cpu_check.sh "wt/$ID" || { log crash; cleanup; exit; }
./eval/sprt.py --candidate "wt/$ID" --baseline champion --elo0 0 --elo1 15 --max-games 800 \
    > "loop/experiments/$ID.eval.txt"
# keep  -> fast-forward main, promote to champion, add old champion to opponents/champions/
# discard -> record reason + loss replays summary in results.tsv/ideas.json; delete worktree
```
- The agent inside the session may run `./eval/quick.sh` (e.g. 40 paired games on 4 train maps vs 3 fixed
  opponents, prints one line `quick: W-L-D winrate=0.57 cpu_max=41M crashes=0`) as backpressure, but the
  keep/discard call comes only from the harness's `sprt.py` on a larger, partly hidden opponent/map set.
- `PROMPT.md` skeleton (mirrors A2 start-up steps):
  1. Read `loop/PLAYBOOK.md`, the last 30 lines of `loop/results.tsv`, and `loop/ideas.json`.
  2. Pick ONE `todo` idea (or write a new one that is not a near-duplicate of any tried idea; say which prior
     experiments it differs from).
  3. Look at 2–3 loss replays / death-cause stats the harness left in `loop/experiments/last_losses.md`.
  4. Implement the smallest change that tests the hypothesis, in `bot/` only. Prefer deleting code.
  5. Run `./eval/quick.sh`. If it crashes or exceeds CPU, fix or abandon. Don't tune constants by hand —
     expose them in `bot/params.json`.
  6. Commit with message `exp(<idea-id>): <hypothesis>`. Append a short note to `ideas.json`. Stop.
- Signs to pre-load (from failure modes seen elsewhere): "Never edit files outside `bot/`"; "Never change time
  or CPU limits, the engine, maps or opponents"; "Don't claim results — the harness measures them"; "One idea
  per session"; "If quick.sh looks worse, that is information, not a reason to keep tweaking for hours".

### B4. Parallelism on this machine

- N workers = N worktrees (or containers) each running the iteration above on a *different* idea; evaluation
  goes through **one queue** so matches don't oversubscribe CPU (brief: machine is shared and loaded; a match is
  0.5–10 s). Size N from free cores, not from ambition.
- Avoid lock-file task claiming across many agents (A6: bottlenecks, stale locks); a single planner/harness
  hands each worker its idea (A6 planner/worker/judge).
- Merge policy: only the harness fast-forwards `main` to an accepted candidate; other in-flight candidates are
  re-based and re-tested against the *new* champion (their earlier result is stale).

### B5. Watchdogs and wind-down

- Per session: `--max-turns`, `--max-budget-usd`, a wall-clock `timeout 45m` around `claude`.
- Global: stop after K consecutive discards and ask the outer loop to change strategy (OpenEvolve needed a
  human-reconfigured phase 2 to break a plateau; ShinkaEvolve spawns a new island after stagnation).
- Log every session's `total_cost_usd`; hard daily cap.
- Kill only processes the harness started (track PIDs); clean worktrees after each iteration.

### B6. Outer loop (improves prompts/playbook/tools) — keep slow

- Every ~20 iterations, a separate session reads `results.tsv` + session logs and proposes edits to
  `PROMPT.md`, `PLAYBOOK.md`, or `eval/quick.sh` *as a diff for review* (GEPA/Promptbreeder-style; SICA/DGM
  show agents can improve their own scaffolding). Fitness of a prompt version = accepted Elo per hour/$ over its
  window.
- The evaluator (`eval/sprt.py`, opponents, hold-out maps) stays human-owned. If the outer loop wants a new
  opponent or map, it proposes it; a human (or a strictly separate, non-editing job) adds it.
