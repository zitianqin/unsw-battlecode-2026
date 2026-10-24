# UNSW Battlecode 2026

This repository contains **zitian**'s solo UNSW Battlecode 2026 bot, built up to the Sprint, plus the full automation stack used to improve, test, evaluate, and publish it continuously. After the Sprint, Zitian joined three other teams to form **larp stock zi balls**; that team's later bot is not in this repository.

## Background and results

[UNSW Battlecode 2026](https://game.battlecode.au/) ran for four weeks with 973 teams from 41 countries.

- **Sprint (1 October, 591 teams).** Zitian entered solo, peaking at #6 on the ladder beforehand, and was seeded 13th. The run went through five rounds without dropping more than one game, then beat 4th seed SSS 4–3 before losing 2–5 in the quarter-finals to chad gdp, the eventual Grand Final champions.
- **New team.** After the Sprint, Zitian merged with three other strong solo teams: stockfish, PPP and larpmaxxing.
- **Qualifiers (9 October, 280 teams).** Seeded 2nd. We reached the final, beating Cutlery 5–1 in the semi-final, and lost the final 4–3 to aAah, finishing 2nd.
- **Grand Final (17 October, UNSW Sydney, top 10).** Seeded 1st. We beat test4 in the quarter-final, lost the semi-final to aAah, then beat Cutlery 9–4 in the third-place match to finish **3rd**. We ended the season #1 on the ladder (Elo 2671).
- **Jump Trading Grand Award: Fastest Algorithm.** The fewest instructions per turn across a finalist's Grand Final games: ours averaged **5.38 million**.

At a high level, the project combines:
- a C++ game bot (`/bot`),
- a Python orchestration loop that proposes and evaluates changes (`/loop`),
- a referee/evaluation subsystem (`/referee`),
- map generation and curated map sets (`/mapgen`, `/maps`),
- research, analysis, and operational tooling (`/research`, `/tools`, `/scripts`).

## What this project does

- Builds and runs a competitive Battlecode bot in C++.
- Continuously tests candidate changes against a champion bot with statistical evaluation.
- Uses generated and curated maps to reduce overfitting.
- Tracks outcomes, follow-ups, and operational decisions for long-running autonomous improvement.
- Supports both Claude-driven and native session workflows for coding/evaluation lanes.

## Repository structure

### Core bot and tests

- **`/bot`** — Main C++ bot implementation and parameters.
  - `main.cpp`: primary bot logic entrypoint.
  - `params.hpp`: tunable constants used by strategy/runtime logic.
  - `safety.hpp`, `scoring.hpp`, `split.hpp`, `targets.hpp`, `state.hpp`: strategy components.
  - `helper.hpp`: protocol/helper integrations.
  - `bot.toml`: bot build/runtime metadata.
- **`/tests/bot`** — Focused bot behavior tests.

### Autonomous improvement loop

- **`/loop`** — Main orchestration system.
  - `driver.py`: primary experiment driver.
  - `native.py`: native-session workflow entrypoint.
  - `quick.py`: quick candidate checks.
  - `judge.py`, `sprt.py` (via `/referee`), `screen*` modules: evaluation pipeline pieces.
  - `online.py`, `publishing.py`, `maintenance.py`: online match analysis, champion publishing, and upkeep automation.
  - `reflect.py`, `health.py`, `throughput.py`: governance/quality checks and operational monitoring.
  - `ideas.jsonl`: backlog/idea source for experiments.
  - `bot_tests.py`: shared test runner for bot-focused tests.
  - Extensive `test_*.py` coverage for loop logic.
- Key loop documentation:
  - `LOOP.md`: mechanism-level reference.
  - `MISSES.md`: historical incidents and lessons.
  - `PLAYBOOK.md`, `JUDGE.md`, `POSTMORTEM.md`, `FOLLOWON.md`: operating guides.

### Referee and evaluation engine

- **`/referee`** — Evaluation infrastructure and match execution.
  - `run.py`: game execution orchestration.
  - `cpu.py`: CPU gate checks.
  - `sprt.py`: statistical test machinery.
  - `gauntlet.toml`: fixed opponent screen config.
  - `remote.py`, `host_agent.py`: remote host scheduling/execution.
  - `engine_cache.py`, `fork_server.py`: performance infrastructure.
  - `native_judge.py`: native judging support.
  - `native_judge/`: native judge runtime sources, including the `engine_wasmtime.c` and `engine_w2c.c` engine backends plus shared judge headers/runtime.
  - `native_fidelity.py`: native-vs-standard engine fidelity checks.
  - `samecode.py`, `identical.py`: helpers for same-moves/identical behavior checks.
  - `test_*.py`: referee subsystem tests.

### Maps and map generation

- **`/mapgen`** — Map generators and map-generation tests.
  - `gen.py`: main generator entrypoint/styles.
  - `worldgen/`: vendored generator integration (documented in `worldgen/README.md`).
- **`/maps`** — Curated map collections.
  - `ladder/`, `sprint/`: known competition-related map sets.
  - `community/`: community-provided maps.
  - `adversarial/`: discovered failure-case maps used for regression pressure.
  - `gen-fixed/`, `extra/`, `retired/`: additional fixed pools.

### Docs, research, and policy

- **`/docs/unsw`** — Mirror of official UNSW Battlecode docs and related site rules/schedule pages.
- **`/research`** — Strategy analysis, replay studies, sonar analysis, and planning reports.
- **`/PLAN.md`** — Program-level plan and architecture context.
- **`/CLAUDE.md`** (and symlink `AGENTS.md`) — detailed operating policy/rules for autonomous runs.
- **`/PROMPT.md`** — prompt/process instructions tied to loop workflows.
- **`/loop/PROMPT.md`** — loop-specific prompt guidance used by automated coding/evaluation runs.

### Operational tools and scripts

- **`/tools`** — utility tooling for orchestration, replay analysis, sonar/keyed protocol work, hooks, and diagnostics.
  - `gated_test.sh`: load-aware test gate wrapper.
  - `merge.sh`: controlled merge helper for orchestrator-managed branches.
  - `check_refs.py`, `doc_size.py`: documentation/reference integrity checks used by hooks/tests.
  - `watch.py`, `usage_statusline.py`: alert monitoring and usage/status reporting helpers.
  - `orch/README.md`: supervising orchestrator runbook.
  - `sonar/`, `keyed/`: enemy sonar format discovery/verification toolchains.
- **`/scripts`** — standalone scripts for doc mirroring and replay/research dossier generation.
  - `mirror_docs.py`: refreshes mirrored official docs under `/docs/unsw`.
  - `build_full_top*.py`, `deep_decrypt_top*.py`, `fetch_top*.py`: large replay-analysis/dossier pipelines.

### Project config

- **`pyproject.toml`** — Python project metadata/dependencies (`unswbc`, `pycapnp`).
- **`uv.lock`** — lockfile for reproducible Python environments.
- **`.gitignore`** and per-folder ignore files for generated/ephemeral artifacts.

## Typical workflows

### 1) Set up environment

```bash
uv sync
```

### 2) Run bot-focused test suite

```bash
.venv/bin/python loop/bot_tests.py
```

### 3) Run selected module tests

```bash
tools/gated_test.sh .venv/bin/python -m unittest loop.test_driver
```

### 4) Run loop driver (example)

```bash
uv run loop/driver.py --lanes 1
```

### 5) Native session flow (example)

```bash
uv run loop/native.py claim 1
uv run loop/native.py quick E####
uv run loop/native.py evaluate E#### "what changed and why"
```

## Notes on replay artifacts

Finished experiment clones (`../bc-wt/E####`) may have replay folders packed into `replays.tar.zst` by `loop/pack.py`.
Unpack in that clone with:

```bash
tar --zstd -xf replays.tar.zst
```

## Where to read next

- Start with `PLAN.md` for overall architecture and goals.
- Read `loop/LOOP.md` for mechanism-level behavior and evaluation flow.
- Read `docs/unsw/README.md` for mirrored official competition documentation.
- Read `tools/orch/README.md` if supervising long-running orchestrator sessions.
