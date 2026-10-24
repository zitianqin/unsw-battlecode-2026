"""Health check of the live loop state: one alert line per broken invariant, so a silent failure is noticed by a check
rather than by the orchestrator happening to read the right file (loop/MISSES.md 9-13: the leader study failed for
16 h and audits mislabelled every online game for two days, each visible only in a file nothing read).

It reads the live files only. `run` is called by driver.record() at every result and by the hourly upkeep tick, and
`quick` (the QUICK checks alone) every minute beside it; each writes each alert to `<loop home>/host-alerts.log` (the
file the orchestrator watches) at most once every REPEAT seconds, keeping that and its first-seen times in
`<loop home>/health.json`.

    uv run loop/health.py --dry-run   # print every current alert; writes nothing
    .venv/bin/python loop/health.py --remind 2026-09-29T09:00 "check the first sweep's results"

A reminder (<loop home>/reminders.jsonl) is alerted once by the first check after it is due: within the hour while a
loop's upkeep runs, or at the next result.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import advmaps
import backlog as backlog_audits
import baselines as prewarming
import behaviour_gaps as behaviour_gaps_module
import chance
import decisions
import digest
import judge
import ladder_maps
import maintenance
import mapideas
import mappool
import official
import online
import pack
import prune
import replay_library as replay_library_module
import reflect
import rescreen
import risers
import rulecounts
import screenport
import sweep as sweeps
import quota
import throughput
import home
import meta
from home import browse_server, clones_folder, loop_home
from meta import IDEA_FIELDS, MAX_METHOD_LINES, MAX_PLAYBOOK_LINES, idea_fields_ok, locked, map_buckets, waits_on
from meta import TUNED_WAIT_S, clone_intact
from meta import alive as meta_alive
from meta import stranded as meta_stranded
from publishing import QUARANTINE_STALE, stale_quarantines

REPO = Path(__file__).resolve().parent.parent
CHAMPION_BRANCH = "loop/champion"  # driver.CHAMPION_BRANCH; not imported, since the driver imports this module
EVERY = 3600  # the upkeep tick runs the check at most this often
REPEAT = 3 * 3600  # the same alert is written at most this often
# Each upkeep job's record and how long it may go without a success: online games are collected every two to ten
# minutes (online.DEV_EVERY while dev teams are listed, else online.REQUEST_EVERY), the leader study, public scouting,
# the ladder map pool and the Mac hosts' crash reports hourly, the Discord every maintenance.DISCORD_EVERY.
UPKEEP_JOBS = {"online.json": ("online games", 3600), "study.json": ("leader study", 3 * 3600),
               "scout.json": ("public scouting", 3 * 3600),
               "discord.json": ("Discord channels", maintenance.DISCORD_EVERY + 3600),
               ladder_maps.RECORD: ("ladder map pool", 3 * 3600),
               maintenance.HOST_CRASHES: ("Mac hosts' bot crash reports", 3 * 3600)}
UPKEEP_GRACE = 2 * 3600  # maintenance.json's check or upgrade failing this long is an alert
# A refresh plays 48 games in the background after a promotion, within the hour (driver.start_evidence; beside an
# evaluation its games split the tune's share as a tune player, and it no longer waits for the evaluation lock,
# MISSES 50).
EVIDENCE_GRACE = 3 * 3600
# Publishing retries every upkeep tick and a build takes minutes; the site's 12 uploads an hour cover several
# promotions, so two hours is two or more chances to upload and verify. A spent budget still alerts, since it then
# needs a decision.
PUBLISH_GRACE = 2 * 3600
HOLD_GRACE = 15 * 60  # a hold re-activates its version on the next tick, a minute apart; several failed ones alert
REFEREE_GRACE = 6 * 3600  # a refresh waits for a clean boundary between two evaluations (loop/LOOP.md)
HOST_TEST_GAMES = 200  # a test this long gives every working host games
HOST_REST = 3600
HOST_STATUS_AGE = 3600  # an older remote-status.json is a finished test, already reported while it was recent
# A remote.json host marked "part_time" (sig 2026-09-27: zz is shut down by day and opened at night, when electricity
# is cheaper) alerts as down only once its outage in host-outages.json (referee/remote.py OUTAGES) is this old.
PART_TIME_DOWN = 24 * 3600
# A copy of referee/remote.py's UNREACHABLE (the referee runs from a copy that can't import loop/): a rest reason
# saying the host didn't answer, not that it answered and failed.
UNREACHABLE = re.compile(r"(?:^|rested \d+s: )[^\s:]+: (?:exit 255|timed out after)")
STATUS_LIVE = 600  # another run's host status file this fresh is a test running now (status_files)
AZURE_DOWN = 40 * 60  # an az-* host resting this long has its VM's power state read from Azure (azure_hosts)
AZURE_TIMEOUT = 20  # seconds the one `az vm list` call may take (about 9 s on 2026-09-27)
AZURE_GROUP = "bc-burst"  # the resource group of the burst VMs (memory note azure-burst-hosts)
ENGINE_CACHE_AGE = 24 * 3600  # engine-cache-off.json older than this is from a run long gone
NATIVE_GATE_AGE = 7 * 86400  # a failed native-judge gate alerts this long, or while its key is still the one wanted
NATIVE_MISSING_AGE = 4 * 3600  # a place the loop plays at with no gate for this long, while the switch is on, alerts
FORK_OFF_AGE = 6 * 3600  # fork-server-off.json older than this is from a run long gone (a warm failure recurs per run)
RETRY_AGE = 6 * 3600  # game-retries.json rows this recent alert: games this Mac failed and replayed (game_retries)
RETRY_ALERT_MIN = 3  # replayed games in RETRY_AGE that alert when every one played on a later try
LEADER_REPLAY_AGE = 6 * 3600
HISTORY_LAG = 2 * 3600  # study-history.jsonl may trail the study's last leader fetch this long
METHOD_GRACE = 24 * 3600
DISCORD_PARTIAL_RUNS = 1  # partial Discord reads in a row before an alert: with a read a day, the first (it was 2
# at a read every 4 h, an alert within 8 h)
DIGEST_GRACE = 2 * 3600  # the orchestrator digests a Discord read with new messages within minutes of its alert
DECIDE_GRACE = 2 * 3600  # the orchestrator decides on a report's items within minutes of its alert
WORKER_PENDING = 2 * 3600  # a digest or REFLECT worker takes minutes; one failed launch waits an hour
REFLECT_JUDGE_GRACE = 2 * 3600  # the orchestrator judges a REFLECT list within minutes of its alert
MAPIDEAS_GRACE = 2 * 3600  # a failed map-ideas run retries only after 12 h, so its failure needs a look before then
MAPIDEAS_MERGE_GRACE = 24 * 3600  # the orchestrator reviews a map-ideas branch within a day of its alert
# A ladder map's file kept this long with no run studying it: a run is due within mapideas.EVERY and takes up to
# mapideas.STALE, so past this the trigger is stuck (a branch waiting for review holds it too, alerted on its own).
MAPIDEAS_LESSON_GRACE = 36 * 3600
RISERS_FIRST = 2 * 3600  # the riser study's step first ran this long ago with no check done: its first firing failed
RISERS_OVERDUE = 6 * 3600  # past risers.CHECK_EVERY with no check (a failed read retries hourly: six tries failed)
RISERS_PENDING = 6 * 3600  # a riser run's fetch takes minutes and its worker an hour or two
BACKLOG_FIRST = 6 * 3600  # the backlog audit's step first ran this long ago with no run applied: its first firing failed
BACKLOG_STALE = 2 * 86400  # a daily audit with none applied for this long keeps failing (the orchestrator's ask)
# Past two launches at the worker's cap with a retry between (reports.RETRY_S): a run pending longer is stuck.
BACKLOG_PENDING = 2 * backlog_audits.TIMEOUT + backlog_audits.reports.RETRY_S
# backlog-reports/ (workers/ too): a run's under 1 MB (its report, edits, changed rows and kept worker files), one a
# day to 18 Oct (loop/backlog.py)
BACKLOG_REPORTS_BYTES = 20 << 20
DISK_MIN_GB = 20
BRIEFING_GRACE = 3600  # an hour to fold a new decision into the briefing
BRIEFING_CONFIRM = 24 * 3600
SWEEP_FAILURES = 2  # variant-sweep runs failing in a row (each retried after an hour) before an alert
PREWARM_FAILURES = 2  # baseline-prewarm runs failing in a row (each retried after an hour) before an alert
REVIEW_PENDING = 4 * 3600  # a review, rule counts (40 min) included, takes an hour or two
SWEEP_GRACE = 24 * 3600  # a champion this old should have had its variant sweep started
SWEEP_ERROR_SHARE = 0.2  # more than this share of a sweep's finished variants with a game that couldn't be played ...
SWEEP_ERROR_RUN = 5  # ... or this many finished last in a row (and at least this many finished for the share)
ADVMAPS_FAILURES = 2  # adversarial-map search runs failing in a row (each retried after an hour) before an alert
ADVMAPS_GRACE = 24 * 3600  # a champion this old should have had its adversarial-map search started
ADVMAPS_ERROR_SHARE = 0.2  # more than this share of the search's maps (at least SWEEP_ERROR_RUN) unplayable
LATE_SCREEN_GRACE = 6 * 3600  # a champion missing a screen entry this long with no late screen started
# The screen-port step reruns within a minute of any change to results.jsonl, so a result this much newer than its
# last run, while the upkeep's hourly check still runs, means the step isn't running or keeps raising first.
SCREENPORT_GRACE = 6 * 3600
UPKEEP_ALIVE = 2 * 3600  # maintenance.json's hourly check this recent: the upkeep tick is running
UTILISATION_STALE = 10 * 60  # utilisation.jsonl gets a row a minute while the upkeep runs (throughput.sampling)
PROFILE_AGE = 7 * 86400  # a game's CPU profile (tools/game_profile.py) is re-run weekly and after every upgrade
SIZE_EVERY = 86400  # the loop home's size is measured daily ...
SIZE_ROWS = 30  # ... keeping this many measurements in health.json
GROWTH_GB = 1.0  # growing more than this a day alerts
# The coder's turn after a result (driver.run_postmortem) starts as the result is recorded, so a turn is owed once
# TURN_GRACE_TIMES the 90th percentile of the recent saved turns' own run times (duration_ms; 0.5-50 min on 28 Sep, so
# the floor TURN_GRACE_MIN decides) has passed. It was a fixed RESULT_TURN_GRACE (6 h) before REFLECT 2026-09-28 17:52
# F9, when E0219's missing post-mortem was found by hand 9.9 h after its result; RESULT_TURN_GRACE stays for fewer
# than TURN_GRACE_SAMPLES timed turns. The driver process that runs it records no pid, so the check can't tell a turn
# still running from a lost one.
RESULT_TURN_GRACE = 6 * 3600
TURN_GRACE_MIN = 3600
TURN_GRACE_TIMES = 2
TURN_GRACE_SAMPLES = 5
# A dev battle takes about two minutes and dev_poll looks every online.DEV_POLL_EVERY, so half an hour past the time
# the next dev battle was due (online.dev_due) without a request, while a submission is live, means the poll, the
# cycle or the site's queue has stalled.
DEV_STALL = 30 * 60
# A dev team answering every request with 409 (a battle still pending) for this long, though none of ours against it
# plays in dev_poll's window (online.stuck_teams), has a battle stuck on the site; the latest 409 must be recent
# (stuck_recent).
DEV_STUCK = 30 * 60


# How recent a stuck dev team's latest 409 must be to still count on `maps`: a 409 adds no games and no request row, so
# a stuck team that reached the front of online.dev_requests' order stays there and is tried at every round, one
# online.dev_pace apart, each at most online.DEV_EVERY late (the cycle's round, when no poll runs; a poll's is at most
# online.DEV_POLL_EVERY late), with one DEV_POLL_EVERY to spare. A stuck team due a probe goes first only in a round
# after a share pick (online.after_probe); its own 409 lets a share pick go, so alone it too is tried every round, but
# while another probe is sent in its place it is tried every other round, so two paces, each round late, are allowed.
# A fixed 10 minutes, under the pace, let a stuck team's alert lapse between tries.
def stuck_recent(maps: list[int]) -> float:
    return 2 * (online.dev_pace(maps) + online.DEV_EVERY) + online.DEV_POLL_EVERY


# One broken invariant: `key` identifies it for the rate limit, `text` says what, since when and the file to read.
@dataclass
class Alert:
    key: str
    text: str


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          timeout=60).stdout.strip()


# "26 Sep 01:05 (3.2 h ago)" for a time `at` seen from `now`.
def when(at: float, now: float) -> str:
    return f"{time.strftime('%d %b %H:%M', time.localtime(at))} ({(now - at) / 3600:.1f} h ago)"


# When `branch` last moved (its reflog), or its commit's time without a reflog: when the champion was promoted.
def moved_at(repo: Path, branch: str) -> float:
    entry = git(repo, "log", "-g", "-1", "--format=%gd", "--date=unix", branch)
    if match := re.search(r"@\{(\d+)\}", entry):
        return float(match.group(1))
    return float(git(repo, "log", "-1", "--format=%ct", branch))


# How long a condition has held, as seen by earlier checks: `seen` keeps the first time each active key was seen,
# and drops it once the condition clears. For files that record failures but not their last success.
def held_since(seen: dict, key: str, active: bool, now: float) -> float:
    if not active:
        seen.pop(key, None)
        return now
    return seen.setdefault(key, now)


# Upkeep jobs whose record holds `fetched_at` (the last success), each with no success for longer than its grace,
# whether it keeps failing or has stopped being tried (the tick skips online games while publishing is pending,
# blocked or failing); a record without one is timed from when `seen` first found it so. Quiet under a stop marker,
# since upkeep then stops; discord.json is skipped while the DISCORD_PAUSED marker exists (reads are paused on
# purpose). And maintenance.json's hourly docs/toolkit check or toolkit upgrade failing for
# UPKEEP_GRACE (it records only the latest attempt's error, so how long also comes from `seen`).
def upkeep(state: Path, now: float, seen: dict) -> list[Alert]:
    alerts = []
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists()
    for name, (job, grace) in UPKEEP_JOBS.items():
        path = state / name
        if not path.exists() or name == "discord.json" and (state / maintenance.DISCORD_PAUSED).exists():
            continue
        report = read_json(path)
        fetched, attempt = report.get("fetched_at"), report.get("attempt_at")
        succeeded = isinstance(fetched, (int, float))
        since = fetched if succeeded else held_since(seen, f"upkeep:{name}", True, now)
        if succeeded:
            held_since(seen, f"upkeep:{name}", False, now)
        if stopped or now - since <= grace:
            continue
        last = when(fetched, now) if succeeded else f"none recorded since at least {when(since, now)}"
        tried = f"last attempt {when(attempt, now)}" if isinstance(attempt, (int, float)) else "no attempt recorded"
        error = str(report.get("error") or "").strip().splitlines()
        alerts.append(Alert(f"upkeep:{name}", f"{job}: no success for over {grace / 3600:.0f} h (last success "
                            f"{last}; {tried}); {error[-1][:160] if error else 'no error recorded'}; see {path}"))
    record = read_json(state / "maintenance.json")
    upgrading = record.get("available") != (record.get("active") or {}).get("version") \
        and not maintenance.same_engine(record)
    for field, job, active in (("check_error", "hourly docs and toolkit check", True),
                               ("upgrade_error", f"toolkit upgrade to {record.get('available')}", upgrading),
                               ("compare_error", f"same-engine comparison of {record.get('available')}", upgrading)):
        error = str(record.get(field) or "").strip()
        since = held_since(seen, f"maintenance:{field}", bool(error) and active, now)
        if error and active and now - since > UPKEEP_GRACE:
            alerts.append(Alert(f"maintenance:{field}", f"{job} failing since at least {when(since, now)}: "
                                f"{error.splitlines()[-1][:160]}; see {state / 'maintenance.json'}"))
    return alerts


# The latest audit's context.md labels each online game won or lost; each label must match the replay summary's
# own `won` for our side in online.json (every audit until 2026-09-26 listed every game as lost, MISSES 13).
def audit_labels(state: Path, now: float) -> list[Alert]:
    context = latest_audit(state)
    if context is None:
        return []
    games = {g.get("replay"): g for g in read_json(state / "online.json").get("games", [])}
    wrong, checked = [], 0
    for replay, side, label in re.findall(r"^- (\S+)  side (\S+)  \(online vs [^,]*, (won|lost)\)",
                                          context.read_text(), re.M):
        won = (games.get(replay, {}).get("summary") or {}).get("teams", {}).get(side.upper(), {}).get("won")
        if not isinstance(won, bool):
            continue  # an older submission's game, no longer in online.json
        checked += 1
        if won != (label == "won"):
            wrong.append(f"{Path(replay).stem} listed {label}, its summary says {'won' if won else 'lost'}")
    if not wrong:
        return []
    return [Alert(f"audit:{context.parent.name}", f"audit {context.parent.name} labels {len(wrong)} of {checked} "
                  f"online games wrongly (e.g. {wrong[0]}), written {when(context.stat().st_mtime, now)}; see "
                  f"{context} against {state / 'online.json'}")]


# The newest audit's context.md, or None before the first audit.
def latest_audit(state: Path) -> Path | None:
    contexts = sorted((state / "audits").glob("*/context.md"), key=lambda p: (len(p.parent.name), p.parent.name))
    return contexts[-1] if contexts else None


# Audits compare us with the leaders through the study's replays: the newest audit must list leader games (five
# audits in a row listed none while the study failed, so they compared us only with each game's opponent), and the
# study must have downloaded a new leader replay within LEADER_REPLAY_AGE (the leaders play all day).
def leaders(state: Path, now: float) -> list[Alert]:
    alerts = []
    context = latest_audit(state)
    if context is not None and not re.search(r"^- \S+  side \S+  \(leader ", context.read_text(), re.M):
        alerts.append(Alert(f"leaders:{context.parent.name}", f"audit {context.parent.name} lists no leader games, "
                            f"written {when(context.stat().st_mtime, now)}; see {context} and {state / 'study.md'}"))
    folder = state / "study-replays"
    newest = max((p.stat().st_mtime for p in folder.glob("*.replay")), default=None) if folder.exists() else None
    if newest is not None and now - newest > LEADER_REPLAY_AGE:
        alerts.append(Alert("leaders:replays", f"the leader study's newest replay is from {when(newest, now)}; "
                            f"see {state / 'study.json'} and {folder}"))
    # Each successful leader fetch appends a row to the gap history while our submission has online games.
    fetched = read_json(state / "study.json").get("fetched_at")
    live = read_json(state / "online.json")
    # Ladder and autoscrim games only: the history summarises those, so a version with only dev games writes none.
    dev_teams = online.dev_ids(live)
    playing = any(g.get("submission_id") in online.same_play(live) and not online.against_dev(g, dev_teams)
                  for g in live.get("games", []))
    history = state / "study-history.jsonl"
    written = history.stat().st_mtime if history.exists() else None
    if fetched and now - fetched < UPKEEP_JOBS["study.json"][1] and playing and (
            written is None or fetched - written > HISTORY_LAG):
        alerts.append(Alert("leaders:history", f"the leader study fetched {when(fetched, now)} but its gap history "
                            f"{'was last written ' + when(written, now) if written else 'is missing'}; our games may "
                            f"not summarise; see {history} and {state / 'study.json'}"))
    # The study also reads the dev benchmark's side of our benchmark games (M20): a history row written since then
    # (it has "dev") must have studied some while online.json records games against it.
    rows = maintenance.read_jsonl(history)
    dev = rows[-1].get("dev", False) if rows else False
    played = sum(r.get("games", 0) for r in (live.get("benchmark") or {}).get("records", []))
    if dev is not False and played and not (dev or {}).get("games"):
        cause = f" ({dev['error']})" if (dev or {}).get("error") else ""
        alerts.append(Alert("leaders:dev", f"the leader study's latest gap row, {rows[-1].get('at')}, studied none of "
                            f"our {played} games against the dev benchmark{cause}; see {state / 'study.md'} and "
                            f"{state / 'online.json'}"))
    return alerts


# The replay library (loop/replay_library.py) is rebuilt by the upkeep tick, a minute after the last, whenever a source
# index is newer than it, and then takes the newest source's time; last matched this long before its newest source,
# or missing while its sources exist, the step isn't running or keeps raising, and coders read old games.
LIBRARY_LAG = 2 * 3600


def replay_library(state: Path, now: float) -> list[Alert]:
    newest = max((p.stat().st_mtime for p in replay_library_module.sources(state) if p.exists()), default=None)
    path = state / replay_library_module.LIBRARY
    built = path.stat().st_mtime if path.exists() else None
    if newest is None or built is not None and newest - built < LIBRARY_LAG:
        return []
    state_of = f"was last matched to its sources {when(built, now)}" if built else "is missing"
    return [Alert("replay library", f"the replay library {state_of}, but a source index changed {when(newest, now)}: "
                  f"the upkeep's replay library step isn't running or raises; see {path} and the upkeep's log")]


# behaviour-gaps.md (loop/behaviour_gaps.py) is rewritten by the upkeep's behaviour gaps step, right after its replay
# library step, whenever the library is newer than it; this long behind the library, or missing this long after it
# changed, that step isn't running or raises, and reviews read old action rates.
BEHAVIOUR_GAPS_LAG = 3 * 3600


def behaviour_gaps(state: Path, now: float) -> list[Alert]:
    library, report = state / replay_library_module.LIBRARY, state / behaviour_gaps_module.REPORT
    if not library.exists():
        return []
    built, written = library.stat().st_mtime, report.stat().st_mtime if report.exists() else None
    if (built - written if written is not None else now - built) < BEHAVIOUR_GAPS_LAG:
        return []
    state_of = f"was last written {when(written, now)}" if written else "is missing"
    return [Alert("behaviour gaps", f"{report} {state_of}, but the replay library changed {when(built, now)}: the "
                  "upkeep's behaviour gaps step (behaviour_gaps.main) isn't running or raises; see the upkeep's log")]


UNSTUDIED_REPLAYS = 100  # an opponent version with this many saved replays is worth a study of its own


# Every opponent version ("team:submission") with UNSTUDIED_REPLAYS+ saved replays, ours against it (online-replays)
# and the leaders' games (study-replays) together, that no study read (study.json's "studied", maintenance.study)
# and no idea cites by its submission id (a study the orchestrator ran by hand queues ideas naming it). REFLECT
# 2026-09-28 14:51 F4: dev test 1's old version 4525 left 718 replays nobody studied. Only replays still on disk
# count: a row whose file prune packed away can't be studied (dev teams 609 and 758 alerted with 139 and 146 rows, 0 files).
def unstudied_versions(state: Path) -> list[Alert]:
    ours = {int(p.name) for p in (state / "online-replays").glob("*") if p.name.isdigit()}
    saved: dict[str, int] = {}
    for path in (state / "online-replays").glob("*/index.json"):
        for game in read_json(path).get("games", []):
            on_disk = Path(game.get("replay") or "").is_file()
            if game.get("opponent") and game.get("opponent_submission_id") and on_disk:
                key = f"{game['opponent']}:{game['opponent_submission_id']}"
                saved[key] = saved.get(key, 0) + 1
    for row in maintenance.read_jsonl(state / "study-replays/index.jsonl"):
        for side in ("A", "B"):
            if row.get(f"team{side}Id") and row.get(f"submission{side}Id") not in (None, *ours):
                key = f"{row[f'team{side}Id']}:{row[f'submission{side}Id']}"
                saved[key] = saved.get(key, 0) + 1
    studied = set(read_json(state / "study.json").get("studied", {}))
    cited = " ".join(f"{i.get('title', '')} {i.get('hypothesis', '')}"
                     for i in maintenance.read_jsonl(state / "ideas.jsonl"))
    return [Alert(f"unstudied:{key}", f"opponent team {key.split(':')[0]}'s submission {key.split(':')[1]} has {n} "
                  "saved replays and no study read it; study it (tools/replays.py over its games) and queue what it "
                  "shows, naming the submission")
            for key, n in sorted(saved.items()) if n >= UNSTUDIED_REPLAYS and key not in studied
            and not re.search(rf"\b{key.split(':')[1]}\b", cited)]


# prune.restore_sample's first real firing and its detector: the rows of prunable versions that still carry the
# "kept" marks prune's old mark_dev_losses wrote (prune.old_mark; 603 on 2026-09-28 19:15), which every new prune
# tick removes before this check runs. An alert while any are left (a driver started before the fix's merge runs the
# old code and marks them again at its ticks, or restore_sample isn't running), and one alert the first time none are
# after a new tick ran (a row marked "dev_wins_pruned"; `seen`, health.json's "old kept marks"): then restore_sample,
# its call and this check can go once no driver started before that merge is running.
def old_kept_marks(state: Path, now: float, seen: dict) -> list[Alert]:
    left: dict[str, int] = {}
    ran = False  # a new prune tick marked a row (prune.mark_dev_rows), so restore_sample ran beside it
    for version, games, is_dev in prune.prunable(state):
        if count := sum(prune.old_mark(g, is_dev) for g in games):
            left[version.name] = count
        ran = ran or any(g.get("dev_wins_pruned") for g in games)
    if left:
        return [Alert("old-kept-marks:left", f"{sum(left.values())} dev rows of {len(left)} old online-replays "
                      f"versions ({', '.join(sorted(left, key=int))}) still carry the \"kept\" marks prune's old "
                      "mark_dev_losses wrote before 2026-09-28's fix, so online.sample_games reads those versions' dev "
                      "summaries as wins only: a driver started before the fix's merge marks them again at its ticks "
                      "(the new tick undoes it), or prune.restore_sample isn't running; see prune.tick's \"restored "
                      "N\" line")]
    if seen.get("cleared") or not ran:
        return []
    seen["cleared"] = now
    return [Alert("old-kept-marks:cleared", "prune.restore_sample's first real firing: no old online-replays version's "
                  "dev rows carry prune's old mark_dev_losses' \"kept\" marks now; delete restore_sample, its call in "
                  "prune.mark_dev_rows and health.old_kept_marks once no driver started before the fix's merge is "
                  "running")]


# The behaviour stats (loop/evalstats.py, driver.behaviour_stats) over the BEHAVIOUR_RUNS newest SPRTs (by sprt.json's
# mtime) that queued for them (sprt.json's "stats_queue"): its first real firing, once (`seen`, health.json's
# "behaviour stats": the run, its trials read and its pairs), then an alert for a run that queued no trial, whose
# worker wrote no row (it died: runs/E####/stats-worker.log), or most of whose games' replays didn't parse, leaving
# out an SPRT that ended in error, one with under 5 opponent trials and one ended under 2 x evalstats.DRAIN_S ago.
# Quiet before the referee copy has --stats-queue.
BEHAVIOUR_RUNS = 3


def behaviour_stats(state: Path, now: float, seen: dict) -> list[Alert]:
    import evalstats
    newest = sorted((state / "runs").glob("E*/sprt.json"), key=lambda p: p.stat().st_mtime)[-10:]
    runs = []
    for path in reversed(newest):
        queue = read_json(path).get("stats_queue")  # read again below with its error: sprt.json is a few MB
        if isinstance(queue, dict):
            runs.append((path.parent, queue))
        if len(runs) == BEHAVIOUR_RUNS:
            break
    alerts = []
    for run, queue in runs:
        report = read_json(run / "sprt.json")
        young = now - (run / "sprt.json").stat().st_mtime < 2 * evalstats.DRAIN_S  # its worker may still be reading
        if report.get("error") or young or sum(queue.values()) < 5:  # an error is reported elsewhere; too few trials
            continue
        rows = evalstats.read_rows(run)
        games = [g for r in rows for g in r.get("games") or [] if g.get("replay") or "error" in g or "stats" in g]
        errors = sum("error" in g for g in games)
        if not queue.get("queued"):
            why = f"queued no trial ({queue.get('skipped', 0)} skipped: the worker never kept up)"
        elif not rows:
            why = f"queued {queue['queued']} trials but its worker wrote no row (see {run / 'stats-worker.log'})"
        elif errors * 2 > len(games):
            why = f"{errors} of {len(games)} games' replays didn't parse (their \"error\" in {run / evalstats.ROWS})"
        else:
            continue
        alerts.append(Alert(f"behaviour-stats:{run.name}", f"{run.name}'s behaviour stats (loop/evalstats.py) {why}"))
    read = next(((run, queue, rows) for run, queue in runs if (rows := evalstats.read_rows(run))), None)
    if read and not seen.get("first"):
        run, queue, rows = read
        seen["first"] = {"run": run.name, "at": now}
        alerts.append(Alert("behaviour-stats:first", flat(
            f"behaviour stats' first real firing: {run.name} read {len(rows)} trials (sprt.json stats_queue "
            f"{queue}); check its table (.venv/bin/python loop/evalstats.py table {run}), the result row's "
            "`behaviour` and the RESULT line's moves, and its cost (each row's cpu_s)")))
    return alerts


# The latest evidence refresh's log (runs/E####/evidence.log, driver.start_evidence, the newest by mtime) and its last
# non-empty line, for the evidence alerts: where the refresh failed or stopped.
def evidence_log(state: Path) -> str:
    logs = []
    for log in (state / "runs").glob("E*/evidence.log"):
        try:
            logs.append((log.stat().st_mtime, log))
        except OSError:  # removed while this looked
            continue
    if not logs:
        return "no refresh log in runs/E*/evidence.log"
    latest = max(logs)[1]
    try:
        lines = [line.strip() for line in latest.read_text(errors="replace").splitlines() if line.strip()]
    except OSError as error:
        lines = [f"unreadable: {error}"]
    return f"latest refresh log {latest}: {lines[-1][:200] if lines else '(empty)'}"


# evidence.md, which every lane prompt quotes, must be the champion checkout's (its `Champion commit:` line) within
# EVIDENCE_GRACE of the promotion, and not marked STALE (a failed refresh) for longer than EVIDENCE_GRACE. Both alerts
# name the latest refresh's log and quote its last line (evidence_log).
def evidence(state: Path, repo: Path, now: float) -> list[Alert]:
    path = state / "evidence.md"
    if not path.exists() or not (state / "champion").exists():
        return []
    text, written = path.read_text(), path.stat().st_mtime
    if text.startswith("STALE: "):
        if now - written <= EVIDENCE_GRACE:
            return []
        return [Alert("evidence:stale", f"evidence.md marked stale since {when(written, now)}: "
                      f"{text.splitlines()[0][7:200]}; see {path}; {evidence_log(state)}")]
    champion = git(state / "champion", "rev-parse", "HEAD")
    shown = re.search(r"^Champion commit: (\S+)", text, re.M)
    promoted = moved_at(repo, CHAMPION_BRANCH)
    if (shown and shown.group(1) == champion) or now - promoted <= EVIDENCE_GRACE:
        return []
    return [Alert(f"evidence:{champion[:12]}", f"evidence.md shows champion "
                  f"{shown.group(1)[:7] if shown else '(none)'}, but the champion checkout is {champion[:7]}, "
                  f"promoted {when(promoted, now)}; see {path}; {evidence_log(state)}")]


# The bot playing online must be the verified upload (online.json's submission is publishing.json's), and that
# upload must be the champion branch within PUBLISH_GRACE of its promotion, or while a hold stands
# (publishing.active_hold) the held version within HOLD_GRACE of the marker. A block or an unreadable marker needs a
# person, so it alerts at once. A champion held off the site for spoofing sonar (publishing.spoof_hold) is reported
# with its release time instead of the champion mismatch.
def publishing(state: Path, repo: Path, now: float) -> list[Alert]:
    path = state / "publishing.json"
    report = read_json(path)
    if not report:
        return []
    alerts = []
    error = (str(report.get("error") or report.get("waiting") or "").strip().splitlines() or ["none"])[-1][:200]
    if report.get("blocked"):
        alerts.append(Alert("publishing:blocked", f"publishing blocked since at least "
                            f"{when(path.stat().st_mtime, now)}: {error}; see {path}"))
    verified = report.get("verified") or {}
    import publishing as publisher  # this function's own name hides the module
    try:
        hold = publisher.active_hold(state, now)
    except ValueError as bad:
        hold = {}
        alerts.append(Alert("publishing:hold", str(bad)))
    if hold:
        since = (state / publisher.HOLD).stat().st_mtime
        try:
            held = git(repo, "rev-parse", "--verify", "-q", f"refs/tags/{hold['version']}^{{commit}}")
        except subprocess.CalledProcessError:
            held = None
            alerts.append(Alert(f"publishing:held-{hold['version']}", f"{hold['version']} is held until "
                                f"{hold['until']} (set {when(since, now)}), but it has no tag here; see "
                                f"{state / publisher.HOLD}"))
        if held is not None and verified.get("commit") != held and now - since > HOLD_GRACE:
            alerts.append(Alert(f"publishing:held-{hold['version']}", f"{hold['version']} is held until "
                                f"{hold['until']} (set {when(since, now)}), but the verified online bot is "
                                f"{str(verified.get('commit') or 'none')[:7]}; publishing error: {error}; see {path}"))
    branch = git(repo, "rev-parse", CHAMPION_BRANCH)
    promoted = moved_at(repo, CHAMPION_BRANCH)
    if hold is None and report.get("spoof_held") == branch:
        alerts.append(Alert(f"publishing:spoof-{branch[:12]}", f"{report.get('waiting')} "
                            f"(publishing.spoof_hold; release time in {state / publisher.SPOOF_RELEASE}); the verified "
                            f"online bot is {str(verified.get('commit') or 'none')[:7]}; see {path}"))
    elif hold is None and verified.get("commit") != branch and now - promoted > PUBLISH_GRACE:
        alerts.append(Alert(f"publishing:{branch[:12]}", f"the verified online bot is "
                            f"{str(verified.get('commit') or 'none')[:7]}, but {CHAMPION_BRANCH} is {branch[:7]}, "
                            f"promoted {when(promoted, now)}; publishing error: {error}; see {path}"))
    live, sid, at = read_json(state / "online.json").get("submission_id"), verified.get("submission_id"), verified.get("at")
    if live and sid and live != sid and isinstance(at, (int, float)) and now - at > PUBLISH_GRACE:
        alerts.append(Alert(f"online:{live}-{sid}", f"online.json plays submission {live}, but publishing.json "
                            f"verified {sid} {when(at, now)}; see {state / 'online.json'} and {path}"))
    return alerts


# Dev battles are paced within their own hourly allowance while a submission is live (online.dev_round, sig
# 2026-09-27): while online.json names dev teams for its live submission, a dev battle must have been requested
# (match-requests.json) within DEV_STALL of the latest of the last dev request, when that submission's dev battles
# began, and when the next was due (online.dev_due: its pace, and the hour's allowance freeing); the alert quotes the
# last poll's round or why it sent nothing (dev-poll.json's `skipped`, online.poll_skip). And the API's winner, which
# scores the dev games kept without a replay, must agree with the replay's on every saved dev game that has both
# (online.winner_check). A dev team whose requests all answer 409 past DEV_STUCK alerts too (online.stuck_teams).
# Quiet under a stop marker, when nothing is requested on purpose.
def dev_battles(state: Path, now: float) -> list[Alert]:
    report = read_json(state / "online.json")
    dev, sid = report.get("dev") or {}, report.get("submission_id")
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists() or not sid or dev.get("submission_id") != sid:
        return []
    alerts = []
    try:
        rows = json.loads((state / "match-requests.json").read_text())
    except (OSError, ValueError):
        rows = []
    times = [r["at"] for r in rows if isinstance(r, dict) and r.get("dev") and isinstance(r.get("at"), (int, float))]
    last = max(times + [dev.get("since") or now])
    poll = read_json(state / "dev-poll.json")
    due = online.dev_due([r for r in rows if isinstance(r, dict) and isinstance(r.get("at"), (int, float))],
                         dev.get("maps") or [])
    if dev.get("teams") and now - max(last, due) > DEV_STALL:
        polled = ("no poll recorded" if not isinstance(poll.get("at"), (int, float)) else
                  f"the last poll ({when(poll['at'], now)}) sent nothing: {poll['skipped']}" if poll.get("skipped") else
                  f"the last poll ({when(poll['at'], now)}) found {poll.get('queued', 0)} of ours queued with "
                  f"{poll.get('ahead', 0)} ahead (new ones wait above {online.MAX_AHEAD}), error "
                  f"{poll.get('error') or 'none'}")
        alerts.append(Alert(f"dev battles:{sid}", flat(
            f"no dev battle requested since {when(last, now)} (one was due {when(max(last, due), now)}) while "
            f"submission {sid} is live with "
            f"{len(dev['teams'])} dev teams listed; {polled}; online.json's error {report.get('error') or 'none'}; "
            f"see {state / 'online.json'}, {state / 'dev-poll.json'} and {state / 'match-requests.json'}")))
    stuck = poll.get("stuck") or {}
    for team, times in sorted(stuck.items()):
        if now - times["last"] < stuck_recent(dev.get("maps") or []) and times["last"] - times["since"] > DEV_STUCK:
            alerts.append(Alert(f"dev stuck:{team}", flat(
                f"dev team {team} has answered every request with 409 (a battle still pending) since "
                f"{when(times['since'], now)}, though none of our battles against it in the newest "
                f"{online.POLL_WINDOW} is playing: a battle stuck on the site, and no more dev games against that "
                f"team until it clears; see {state / 'dev-poll.json'} (stuck) and GET /battles")))
    check = dev.get("winner_check") or {}
    if check.get("checked", 0) > check.get("agree", 0):
        alerts.append(Alert(f"dev winners:{sid}", flat(
            f"the API's winner disagrees with the replay's on {check['checked'] - check['agree']} of "
            f"{check['checked']} saved dev games of submission {sid}, and dev games past the replay sample are scored "
            f"from it (online.api_winner); see {state / 'online.json'} (dev.winner_check) and "
            f"{state / 'online-replays' / str(sid) / 'index.json'}")))
    return alerts


# Whether the online cycle still records our games (MISSES 77: the site dropped the fields it filed them by at about
# 19:30 AEST on 2026-09-28, and for 20 minutes every finished game was read and silently skipped). online.json keeps
# the fields of a battle detail the cycle reads that the site no longer sends (api_missing, online.missing_fields) and
# the completed games its details showed that no index records (unrecorded, online.unrecorded_games): a missing field
# alerts while it lasts, and UNRECORDED_GAMES+ games unrecorded UNRECORDED_S after their battle finished alert. A
# battle's games are recorded within a cycle or two (online.DEV_EVERY, online.REQUEST_EVERY), so half an hour is three
# or more cycles; one broken replay (retried hourly, three times) is fewer games than that. A backlog past the
# cycle's replay budget alerts too (on 2026-09-28 at 22:05, 20 games after the 19:30 outage and a version change),
# and keeps its games: a battle that leaves the list unrecorded is still read by id, for up to online.BEHIND_S and then
# alerted (online.owed_battles, MISSES 83).
UNRECORDED_S = 30 * 60
UNRECORDED_GAMES = 5


def online_recording(state: Path, now: float) -> list[Alert]:
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
        return []
    report = read_json(state / "online.json")
    alerts = []
    if fields := (report.get("api_missing") or {}).get("fields"):
        alerts.append(Alert(f"api fields:{','.join(fields)}", flat(
            f"the site's battle details lack {', '.join(fields)} since "
            f"{when(report['api_missing'].get('since') or now, now)}, fields the online cycle reads "
            f"(online.API_MATCH_FIELDS); check every reader and that games are still recorded; see "
            f"{state / 'online.json'}")))
    missed = report.get("unrecorded") or {}
    try:
        oldest = datetime.fromisoformat(str(missed.get("oldest")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        oldest = None
    if missed.get("games", 0) >= UNRECORDED_GAMES and oldest is not None and now - oldest > UNRECORDED_S:
        alerts.append(Alert("online unrecorded", flat(
            f"{missed['games']} of our finished online games are in no online-replays index, the oldest's battle "
            f"finished {when(oldest, now)} (e.g. game {missed.get('example')}): the cycle reads them but doesn't "
            f"record them, or is still catching up a backlog within its per-cycle budgets or waiting out a failed "
            f"game's retry wait; see {state / 'online.json'} (error, unrecorded) and each version's index.json invalid")))
    return alerts


# The online version compare pairing nothing while it has games to pair (REFLECT 2026-09-29 10:36 F14, M73: from the
# site's last named battle on 2026-09-28 09:26 UTC to the M73 fix it paired 0 of every version's games, 10774's 249
# of them, and nothing said so): online.json's `compare_zero_cycles`, the online cycles in a row whose compare had
# more `candidates` (matchups that can pair) than `unread` ones (those no rating read covers yet) and no pair, past
# one. And M73's rating reads going stale: rating-splits.json's last good read over 3 x online.RATINGS_EVERY before
# online.json's last collection (so while the cycle runs), naming the last failure; a failed read retried at the next
# attempt (online.REQUEST_EVERY) doesn't alert on its own.
def online_compare(state: Path, now: float) -> list[Alert]:
    import online
    report = read_json(state / "online.json")
    splits = read_json(state / "rating-splits.json")
    read = (f"the last good rating read {when(splits['read_at'], now)}" if splits.get("read_at")
            else "no good rating read yet")
    read += f", the last try failing: {splits['error']}" if splits.get("error") else ""
    alerts = []
    fetched = report.get("fetched_at") or 0
    if report.get("compare") is not None and fetched - splits.get("read_at", 0) > 3 * online.RATINGS_EVERY:
        alerts.append(Alert("rating reads", flat(
            f"{read}, while the online cycle collected {when(fetched, now)}: the compare pairs games of unnamed "
            f"opponent versions (M73) only up to that read; see {state / 'rating-splits.json'} and "
            "online.refresh_splits")))
    if report.get("compare_zero_cycles", 0) >= 2 and (compare := report.get("compare")):
        alerts.append(Alert("online compare", flat(
            f"the online compare of {report.get('submission_id')} against {compare.get('previous')} has paired none of "
            f"its {compare.get('candidates')} matchups that can pair ({compare.get('unread', 0)} waiting for a rating "
            f"read) for {report['compare_zero_cycles']} cycles in a row "
            f"({(compare.get('unknown_opponent') or {}).get('after', 0)} games with no opponent version named; M73's "
            f"rule pairs those only when rating-splits.json covers both games and no step splits them: {read}), so its "
            f"version alert and re-checks have no input; see {state / 'online.json'} and online.compare_versions")))
    return alerts


# M85's online ram rows (online.refresh_rams, 20 replays a cycle): the live version's sample games (online.sample_games,
# dev games apart, replay on disk) past RAM_GAMES with none read, over half of those read failing, or RAM_GAMES read
# with not one ram found (game 447294 alone has 12) mean the online ram cells have no input.
RAM_GAMES = 40


def online_rams(state: Path, now: float) -> list[Alert]:
    import online
    report = read_json(state / "online.json")
    sid = report.get("submission_id")
    path = state / "online-replays" / str(sid) / "index.json"
    if not sid or (state / "STOP").exists():
        return []
    dev = online.dev_ids(report)
    games = [g for g in online.sample_games(read_json(path).get("games") or []) if not online.against_dev(g, dev)
             and g.get("side") in ("A", "B") and Path(g.get("replay") or "").is_file()]
    read = [g["rams"] for g in games if (g.get("rams") or {}).get("version") == online.RAM_VERSION]
    failed = [r for r in read if r.get("error")]
    rows = sum(len(r.get("ours", [])) + len(r.get("theirs", [])) for r in read if not r.get("error"))
    good = len(read) - len(failed)
    why = (f"none of its {len(games)} sample games has ram rows yet" if len(games) >= RAM_GAMES and not read
           else f"{len(failed)} of {len(read)} replays read failed (e.g. {failed[0]['error']})"
           if 2 * len(failed) > len(read) else f"{good} replays read found no ram" if good >= RAM_GAMES and not rows
           else None)
    return [Alert("online rams", flat(f"online ram rows of submission {sid}: {why}, so rulecounts' and study.md's "
                                      f"online ram cells (M85) have no input; see {path} and online.refresh_rams"))
            ] if why else []


# The sweep's weighting by how much games inform (online.sweep_shares, sig 2026-09-27 "teams that still beat us
# sometimes"), its first firing and after: the loop's sweep requests of the last day that recorded their team's share
# (match-requests.json "share"), SWEEP_GAMES games or more, at a mean share a game no higher than SWEEP_SHARE, what the
# rotation it replaced gave (least recently requested: the 1,961 sweep games of 2026-09-27 04:58 to 09-29 04:50 UTC at
# 0.68, shares from each game's own time; a pick at random over the 547 ranked non-dev teams, 0.27).
SWEEP_GAMES = 100
SWEEP_SHARE = 0.68


def sweep_shares(state: Path, now: float) -> list[Alert]:
    try:
        rows = json.loads((state / "match-requests.json").read_text())
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("share"), (int, float)) and not r.get("dev")
            and r.get("at", 0) > now - 86400 and r.get("status") != "rejected"]
    games = sum(r.get("games", 0) for r in rows)
    share = sum(r["share"] * r.get("games", 0) for r in rows) / max(1, games)
    if games < SWEEP_GAMES or share > SWEEP_SHARE:
        return []
    return [Alert("sweep shares", flat(
        f"the loop's {games} sweep games of the last day went to teams at a mean share of {share:.2f} "
        f"(online.sweep_shares), no more than the {SWEEP_SHARE} the least-recently-requested rotation gave: the "
        f"sweep isn't favouring teams whose games inform; see {state / 'match-requests.json'} and "
        "online.choose_match"))]


# The online fit rows' batch (loop/online_fit.py): its last run's error, a run that died (no finish past RUN_S and
# a margin, its process gone), or games waiting with no run started for three of its periods while the upkeep runs
# (its first firing too: started_at is unset until then).
def online_fit(state: Path, now: float) -> list[Alert]:
    import online_fit as fit
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
        return []
    record = read_json(state / fit.RECORD)
    log = state / "online-fit.log"
    if record.get("error"):
        return [Alert("online fit", f"online fit rows' last run failed: {record['error']}; see {log}")]
    started = record.get("started_at", 0)
    if (started and record.get("finished_at", 0) < started and not fit.alive(record.get("pid"))
            and now - started > fit.RUN_S + fit.VARIANT_S + 600):
        return [Alert("online fit", f"online fit rows' run started {when(started, now)} died without finishing "
                                    f"(pid {record.get('pid')} gone); see {log}")]
    if now - started > 3 * fit.EVERY and (waiting := fit.pending(state, now)):
        return [Alert("online fit", f"{len(waiting)} online games wait for their fit rows and no run started for "
                                    f"{(now - started) / 3600:.0f} h (online_fit.tick, from the upkeep); see "
                                    f"{state / fit.RECORD}")]
    return []


# A map our live bot does worse on than on the others (REFLECT 2026-09-28 20:52 F3: Trauma was 26-34 online, first
# portal crossing at median round 101 against the opponent's 12, and nothing flagged it until sig did). Reads every
# non-dev game (ladder and map sweeps, online.all_games) of our live submission (online.json) and the one before it
# (online.previous_version), each with its play group (online.play_group: identical builds), of the current map pool
# (online.pool_period "from"), and alerts on each map whose games not won (losses and draws) outnumber the other maps'
# at a chance rates_p x maps <= chance.TRIGGER_P (Bonferroni over the maps scanned, since every map is a look), as
# suggestive: each check re-reads every game, so the record firms up or fades as games accumulate. Two looks, each so
# corrected: the live version alone, and pooled with the one before (M94: Devil went 8 of 10 lost on 11181 alone, p
# 0.005, 0.05 over 10 maps, and the pool with 11049 hid it); a map both flag is named once, at its stronger look. Each
# alert prints the map's own record and p alone and corrected (REFLECT 2026-09-29 17:32 F1). A game with no winner
# recorded is left out.
# The online map watchers (map_records, goal_maps: a weak map online, and the weak-maps study it starts). Off since
# sig, 1 Oct: "stop fuckign reading portals. we already know its our worst and we freaking diagnosed it a billion
# times"; set True to turn both back on.
MAP_WATCHERS = False


def map_records(state: Path) -> list[Alert]:
    flagged, group = map_flags(state)
    return [Alert(f"map record:{map_id}:{group}", text)  # an identical upload is no news
            for map_id, (_, _, _, text) in sorted(flagged.items())]


# map_records' looks: ({map id: (corrected p, map name, record text, alert text)}, the live play group's newest id).
def map_flags(state: Path) -> tuple[dict[int, tuple[float, str, str, str]], int | None]:
    report = read_json(state / "online.json")
    if not (sid := report.get("submission_id")):
        return {}, None
    dev, previous = online.dev_ids(report), online.previous_version(state, sid)[0]
    group = online.play_group(state, sid)  # identical builds' games are the live version's
    names = {m.get("id"): m.get("name") for m in read_json(state / "ladder-maps.json").get("maps") or []}
    looks = [("the live version", group)] + ([("the live and previous versions",
                                                 [*group, *online.play_group(state, previous)])] if previous else [])
    flagged: dict[int, tuple[float, str, str, str]] = {}  # map id: (corrected p, name, record, alert text)
    for look, versions in looks:
        records = version_map_records(state, versions, dev)
        for map_id, (wins, draws, losses) in sorted(records.items()):
            rest = [sum(r[i] for m, r in records.items() if m != map_id) for i in range(3)]
            alone = chance.rates_p(draws + losses, wins + draws + losses, rest[1] + rest[2], sum(rest))
            p = min(1.0, alone * len(records))
            if p > chance.TRIGGER_P or p >= flagged.get(map_id, (2.0,))[0]:
                continue
            name = names.get(map_id) or f"map {map_id}"
            record = (f"{wins}-{draws}-{losses} against {rest[0]}-{rest[1]}-{rest[2]} on the other maps, {look}, "
                      f"p = {p:.3f} corrected over {len(records)} maps")
            flagged[map_id] = (p, name, record, flat(
                f"MAP RECORD (suggestive, n = {wins + draws + losses}): {name} may be our worst online map: "
                f"{wins}-{draws}-{losses} (won-drawn-lost) against {rest[0]}-{rest[1]}-{rest[2]} on the other "
                f"{len(records) - 1} maps, {look} ({', '.join(map(str, versions))}), non-dev games; luck alone gives "
                f"its gap p = {alone:.4f}, and one this large on some map p = {p:.3f} ({alone:.4f} x {len(records)} "
                f"maps), re-read each check as games accumulate. Measure what it gives up there (its replays, "
                f"watch.py doom) before calling it a finding, and queue a measure-first idea; see "
                f"{state / 'online-replays'}"))
    return flagged, group[-1]


# A map sig named in his goal (evalline.GOAL_MAPS) that map_records flags (its record below the other maps' at
# chance.TRIGGER_P, corrected over the maps): the weak-maps study starts with no ask (REFLECT 2026-09-30 21:15 F1:
# v0083 lost Trauma 13/33, Devil 16/36 and Portals 18/33 online, and the Devil map alert became a study only at 20:01,
# after sig asked). Once per map and upload.
def goal_maps(state: Path) -> list[Alert]:
    import evalline
    flagged, group = map_flags(state)
    return [Alert(f"goal map:{map_id}:{group}", flat(
        f"GOAL MAP: {name}, one of sig's goal maps ({evalline.GOAL}), is below our other maps online ({record}): "
        f"start the weak-maps study now, with no ask: a read-only study of the live version's replays there (what "
        f"ends our lost games, their longest dragon by round against ours, where the online winners' play differs), "
        f"each finding a measured shortfall with n and p and one conditional idea; "
        f"{state / 'reports' / 'weak-maps-2026-09-30.md'} has its shape"))
        for map_id, (_, name, record, _) in sorted(flagged.items())
        if name.casefold() in {goal.casefold() for goal in evalline.GOAL_MAPS}]


# No experiment in flight (evalline.goal_flight: under evaluation, queued, coded, tuned) aims at sig's goal (its map
# mix, evalline.aims_at_goal) for longer than one evaluation takes (recorded_evaluation; HOST_REST without one), while
# the loop runs (no stop marker): the goal line showed "0 of 7 aim" from 18:26 to 20:02 on 30 Sep and nothing acted
# (REFLECT 2026-09-30 21:15 F1, MISSES 104). `seen` (health.json's "goal drift") keeps when it began.
def goal_drift(state: Path, now: float, seen: dict) -> list[Alert]:
    import evalline
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists()
    current = (throughput.evaluation_now(state, now) or {}).get("experiment")
    flight, aimed = evalline.goal_flight(state, now, current) if not stopped else ([], [])
    since = held_since(seen, "none aimed", bool(flight) and not aimed, now)
    if not flight or aimed:
        return []
    limit = recorded_evaluation(state) or HOST_REST  # only now: reading it takes seconds
    if now - since <= limit:
        return []
    return [Alert(f"goal drift:{int(since)}", flat(
        f"GOAL DRIFT: none of the {len(flight)} experiments in flight ({', '.join(flight[:8])}) has aimed at sig's goal "
        f"({evalline.GOAL}: map styles {', '.join(evalline.GOAL_STYLES)}) since {when(since, now)}, longer than one "
        f"evaluation ({limit / 60:.0f} min): claim the best goal-aimed ready idea next (driver.py --next), or queue "
        "one, and say so in the next status line"))]


# Todo ideas whose `after` can never be met: a prerequisite that isn't in the backlog, or one closed with no code left
# (through merges, a status other than todo, trying, followup or kept) that driver.stack_for would not stack under its
# waiter (it stacks a promising candidate, and a discarded one the waiter corrects or names) and that no experiment in
# flight retests (an idea `trying:*` or a live evaluation-queue ticket whose idea is it, or "Retest E####" of one of
# its results): its coder must build the capability itself, which the link hides (REFLECT 2026-09-30 21:15 F3: I878
# waited on I915, tried with no code, until 15:35; on 1 Oct 19 of the 20 links it named stacked a candidate). A cycle
# is stuck_ideas'.
def dead_prerequisites(state: Path) -> list[Alert]:
    path = state / "ideas.jsonl"
    ideas = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ideas.append(row)
    by_id = {i["id"]: i for i in ideas}
    links = [(i["id"], name, meta.merge_target(name, by_id)) for i in ideas if i.get("status") == "todo"
             for name in i.get("after") or [] if isinstance(name, str)]
    live = ("todo", "trying", "followup", "kept")  # still to come, or left code (kept; promising: followup)
    closed = [(idea, name, target) for idea, name, target in links
              if target not in by_id or not str(by_id[target].get("status", "")).startswith(live)]
    if closed:
        import driver  # lazily: the driver imports this module
        rows = []
        for line in (state / "results.jsonl").read_text().splitlines() if (state / "results.jsonl").exists() else []:
            with suppress(ValueError):
                if isinstance(row := json.loads(line), dict) and isinstance(row.get("idea"), dict):
                    rows.append(row)
        runs = {row["id"]: row["idea"].get("id") for row in rows if isinstance(row.get("id"), str)}
        flight = [i for i in ideas if str(i.get("status", "")).startswith("trying:")]
        for queued in (state / meta.QUEUE).glob("*.json") if (state / meta.QUEUE).is_dir() else []:
            ticket = read_json(queued)
            if isinstance(exp := ticket.get("experiment"), str) and meta.ticket_live(state, ticket):
                flight.append(read_json(state / "runs" / exp / "experiment.json").get("idea") or {})
        retested = {i.get("id") for i in flight} | {runs.get(m.group(1)) for i in flight
                                                    if (m := driver.RETEST.match(i.get("title") or ""))}
        stacks: dict[str, list[dict]] = {}

        # Whether stack_for puts `target`'s code under `idea`: its own result, or the one a retest of it falls back to.
        def stacked(idea: str, target: str) -> bool:
            if idea not in stacks:
                stacks[idea] = driver.stack_for(by_id[idea], by_id=by_id, rows=rows)[0]
            source = target in by_id and driver.RETEST.match(by_id[target].get("title") or "")
            return any(row["idea"].get("id") == target or (source and row["id"] == source.group(1))
                       for row in stacks[idea])
        closed = [(idea, name, target) for idea, name, target in closed
                  if target not in retested and not stacked(idea, target)]
    dead = [f"{idea} after {name}" + ("" if target == name else f" (merged into {target})")
            + (" (not in the backlog)" if target not in by_id else f" ({by_id[target].get('status')}, no code left)")
            for idea, name, target in closed]
    if not dead:
        return []
    return [Alert("dead prerequisites", flat(
        f"{len(dead)} todo ideas wait on a prerequisite that can never deliver: {'; '.join(dead[:8])}. Drop the link "
        "(driver.mark_idea or a locked driver.write_ideas) or queue the missing capability as its own idea, and say "
        f"which; see {path}"))]


# Sleep and wake events in `pmset -g log`'s text (this Mac's power log, read-only): [(slept, woke, why)] for each full
# sleep that ended in a wake, a dark wake between them left in the gap (the loop's processes don't run through it).
PMSET_EVENT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}) (Sleep|Wake)\s*\t\s*(.*)$")


def sleep_gaps(text: str) -> list[tuple[float, float, str]]:
    gaps, slept, why = [], None, ""
    for line in text.splitlines():
        if not (m := PMSET_EVENT.match(line)):
            continue
        at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
        if m.group(2) == "Sleep":
            if slept is None:
                slept, why = at, m.group(3).strip()
        elif slept is not None:
            gaps.append((slept, at, why))
            slept = None
    return gaps


# A sleep of this Mac longer than SLEEP_GAP that ended in the last SLEEP_WINDOW, while the loop had work in flight (the
# last utilisation row before it, at most 5 minutes older: the evaluation and its stage, the coders, the queue), named
# once each: SLEEP_WINDOW is REPEAT, so a sleep is out of the window before its alert may repeat (REFLECT 2026-09-30
# 21:15 F4: it hibernated 19:28-19:43:44 on a flat battery, and the stall was first put down to a slow game on
# az-jp). `text` is pmset's log (read here when None; ~4 s, so a full check only).
SLEEP_GAP, SLEEP_WINDOW = 5 * 60, REPEAT


def mac_sleeps(state: Path, now: float, text: str | None = None) -> list[Alert]:
    if text is None:
        if state.resolve() != loop_home():
            return []  # this Mac's power log says nothing of another home's (a test's) machine
        try:
            text = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return []  # not a Mac, or pmset can't say: nothing to read
    gaps = [(slept, woke, why) for slept, woke, why in sleep_gaps(text)
            if woke - slept > SLEEP_GAP and now - woke < SLEEP_WINDOW]
    if not gaps:
        return []
    rows = [row for row in throughput.jsonl(state / throughput.LOG)
            if isinstance(row, dict) and isinstance(row.get("at"), (int, float))
            and any(slept - 300 <= row["at"] <= slept for slept, _, _ in gaps)]
    alerts = []
    for slept, woke, why in gaps:
        before = [row for row in rows if slept - 300 <= row["at"] <= slept]
        if not before:
            continue  # no loop row just before it: nothing of the loop's was running here
        row = before[-1]
        evaluation = row.get("evaluation") or {}
        work = [f"the evaluation of {evaluation['experiment']} ({evaluation.get('stage', '?')})"
                if evaluation.get("experiment") else "no evaluation",
                f"coding {', '.join(row.get('coding') or []) or 'nothing'}",
                f"{len(row.get('waiting') or [])} queued"]
        alerts.append(Alert(f"mac sleep:{int(slept)}", flat(
            f"MAC SLEPT: this Mac slept from {time.strftime('%H:%M:%S', time.localtime(slept))} to "
            f"{time.strftime('%H:%M:%S', time.localtime(woke))} ({(woke - slept) / 60:.0f} min; {why[:80]}), stalling "
            f"{'; '.join(work)} and every game, shell and watcher here: check that the evaluation, the coders and the "
            f"alerts watcher came back, and name the gap in the next status line (pmset -g log)")))
    return alerts


# A host that rested (referee/remote.py Host.rest, logged in remote.CONNECTS) more than once in the evaluation under
# way (since its marker's "since"), counting only rests of its own: one that most hosts shared within REST_TOGETHER is
# this Mac's or the network's, not the host's, and its rests within REST_TOGETHER of one counted are the same outage
# seen by several game processes (each keeps its own Host). A part-time host's rests are left to `hosts`. Once per
# host and evaluation. Also alerts on CONNECTS of the wrong shape (a writer broke it; the hosts' measured timeouts
# fall back to remote.json's). REFLECT 2026-09-30 21:15 F5: az-my and az-id kept resting on 27-28 s connects,
# unalerted, since host_outages names only outages longer than an evaluation.
REST_TOGETHER = 90


def host_rests(state: Path, now: float) -> list[Alert]:
    path = state / "host-connects.json"
    try:
        known = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        known = None
    if not isinstance(known, dict) or not all(isinstance(v, dict) for v in known.values()):
        return [Alert("host connects", f"{path} isn't a map of host names to their connects and rests, so every "
                      "host's connect timeout falls back to remote.json's (remote.connect_timeout): delete it")]
    marker = home.live_marker(now) or {}
    if not isinstance(since := marker.get("since"), (int, float)):
        return []
    part_time = {h.get("name") for h in read_json(state / "remote.json").get("hosts") or []
                 if isinstance(h, dict) and h.get("part_time") is True}
    rests = {name: [at for at in entry.get("rests") or [] if isinstance(at, (int, float)) and at >= since]
             for name, entry in known.items()}
    everyone = [(name, at) for name, times in rests.items() for at in times]
    alerts = []
    for name, times in sorted(rests.items()):
        own = []
        for at in sorted(times):
            if (not own or at - own[-1] > REST_TOGETHER) and \
                    len({other for other, when_ in everyone if abs(when_ - at) <= REST_TOGETHER}) * 2 < len(known):
                own.append(at)
        if name in part_time or len(own) < 2:
            continue
        alerts.append(Alert(f"host rests:{name}:{int(since)}", flat(
            f"HOST RESTING: {name} rested {len(own)} times in the evaluation under way (since {when(since, now)}; "
            f"last {when(own[-1], now)}), rests most hosts didn't share; its measured connect timeout is "
            f"{remote_timeout(known, name)}. Check it now (`ssh {name} true`, its last rest in remote-status.json) and "
            f"fix or rest it for good; see {path}")))
    return alerts


# "31 s (p95 of 20 connects 17.2 s)" for `name` in CONNECTS' content `known`, as remote.measured_timeout reads it,
# before remote.connect_timeout's floor and cap.
def remote_timeout(known: dict, name: str) -> str:
    remote = referee_remote()
    times = sorted(row[1] for row in (known.get(name) or {}).get("connects") or []
                   if isinstance(row, list) and len(row) == 2 and isinstance(row[1], (int, float)))
    if len(times) < remote.CONNECT_MIN:
        return f"not measured yet ({len(times)} connects)"
    p95 = times[math.ceil(0.95 * len(times)) - 1]
    return f"{remote.measured_timeout(name, known)} s (p95 of {len(times)} connects {p95:.1f} s)"


# referee/remote.py, imported from this checkout (appended to the path, as maintenance.py does: loop/'s modules keep
# their names).
def referee_remote():
    if str(REPO / "referee") not in sys.path:
        sys.path.append(str(REPO / "referee"))
    import remote
    return remote


# {map id: [wins, draws, losses]} over `versions`' non-dev games of the current map pool (map_records).
def version_map_records(state: Path, versions: list[int], dev: set[int]) -> dict[int, list[int]]:
    records: dict[int, list[int]] = {}
    for version in versions:
        index = online.read(state / "online-replays" / str(version) / "index.json", {})
        for game in online.all_games(index):
            if online.against_dev(game, dev) or not online.pool_period(game).startswith("from") \
                    or not isinstance(game.get("map_id"), int):
                continue
            try:
                score = online.game_score(game)
            except (KeyError, TypeError):
                continue
            records.setdefault(game["map_id"], [0, 0, 0])[{1.0: 0, 0.5: 1, 0.0: 2}[score]] += 1
    return records


# online.md's death table (MISSES 64) reads each saved game's "doom", which online.refresh_dooms adds a few a cycle by
# running tools/watch.py doom. A game it gave up on (online.DOOM_TRIES failed tries) means the tool can't read that
# replay: alerted once per game, `seen` (health.json's "death tables") keeping the ids already alerted; one still
# being retried is not an alert. Saved games of the live version with replays requested over DOOM_WAIT ago and none
# done mean the refresh isn't running.
DOOM_WAIT = 2 * 3600


def death_tables(state: Path, now: float, seen: list | None = None) -> list[Alert]:
    report = read_json(state / "online.json")
    sid = report.get("submission_id")
    if not sid:
        return []
    index = state / "online-replays" / str(sid) / "index.json"
    games = [g for g in read_json(index).get("games", []) if isinstance(g, dict)]
    gave_up = [g for g in games if isinstance(g.get("doom_failed"), dict)
               and g["doom_failed"].get("tries", 0) >= online.DOOM_TRIES]
    alerts = []
    if seen is not None:
        seen[:] = [i for i in seen if i in {g.get("id") for g in gave_up}]
    new = [g for g in gave_up if seen is None or g.get("id") not in seen]
    if new:
        if seen is not None:
            seen.extend(g.get("id") for g in new)
        alerts.append(Alert(f"death table:{sid}:{new[0].get('id')}", flat(
            f"tools/watch.py doom gave up on {len(new)} more saved games of submission {sid} after "
            f"{online.DOOM_TRIES} tries each, so online.md's death table leaves them out; the first "
            f"({new[0].get('id')}): {new[0]['doom_failed'].get('error')}; see {index}")))

    def age(game: dict) -> float:
        try:
            return now - datetime.fromisoformat(game["requested_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, AttributeError, ValueError):
            return 0.0

    late = [g for g in games if g.get("replay") and g.get("side") and "doom" not in g and age(g) > DOOM_WAIT]
    if late and not any("doom" in g for g in games):
        alerts.append(Alert(f"death table:{sid}", flat(
            f"none of submission {sid}'s saved games has a death table row (online.refresh_dooms, run each online "
            f"cycle), {len(late)} of them requested over {DOOM_WAIT // 3600} h ago; see {index}")))
    return alerts


# Pairing by start (MISSES 72) reads each saved game's "start", which collect_games writes and online.refresh_starts
# backfills, and the new-layout alert reads online.json's "layouts" (online.layout_news). Saved games of the live
# version with replays requested over DOOM_WAIT ago and none with a start or a failed start, or online.json's games
# with layouts and no "layouts" record, mean one of them isn't running.
def layouts(state: Path, now: float) -> list[Alert]:
    report = read_json(state / "online.json")
    sid = report.get("submission_id")
    if not sid:
        return []
    index = state / "online-replays" / str(sid) / "index.json"
    games = [g for g in read_json(index).get("games", []) if isinstance(g, dict)]

    def age(game: dict) -> float:
        try:
            return now - datetime.fromisoformat(game["requested_at"].replace("Z", "+00:00")).timestamp()
        except (KeyError, AttributeError, ValueError):
            return 0.0

    late = [g for g in games if g.get("replay") and g.get("side") and age(g) > DOOM_WAIT]
    if late and not any(g.get("start") or g.get("start_failed") for g in games):
        return [Alert(f"starts:{sid}", flat(
            f"none of submission {sid}'s saved games has a start (online.start_fields, online.refresh_starts), "
            f"{len(late)} of them requested over {DOOM_WAIT // 3600} h ago, so compare pairs none of them; see "
            f"{index}"))]
    # online.json's games and its "layouts" are written together at the end of a cycle.
    if any(isinstance(g, dict) and g.get("layout") for g in report.get("games", [])) and not report.get("layouts"):
        return [Alert(f"layouts:{sid}", f"online.json records no map layouts though its games carry them "
                                        f"(online.layout_news, MISSES 72); see {state / 'online.json'}")]
    return []


ADVERSARIAL_README = "maps/adversarial/README.md"  # driver.ADVERSARIAL_README


# The referee copy (<loop home>/referee) must not lack main's referee/, mapgen/ or maps/ commits for longer than
# REFEREE_GRACE: every test until the refresh plays with the old referee and maps. Commits changing only tests (test_*.py)
# or notes (*.md) don't change a game and are left out, except ADVERSARIAL_README (driver's, spelled out since health can't
# import driver), whose rows name the opponent each adversarial map plays. The age is the oldest missing commit's commit
# time, so an old branch merged later alerts at once.
def referee_lag(state: Path, repo: Path, now: float) -> list[Alert]:
    if not (state / "referee").exists():
        return []
    head = git(state / "referee", "rev-parse", "HEAD")
    log = git(repo, "log", "--format=%x00%h %ct", "--name-only", f"{head}..HEAD", "--", "referee", "mapgen", "maps")
    missing = [entry.split()[:2] for entry in log.split("\0")[1:]
               if any(not (Path(name).name.startswith("test_") and name.endswith(".py"))
                      and (not name.endswith(".md") or name == ADVERSARIAL_README) for name in entry.split()[2:])]
    if not missing:
        return []
    oldest, at = missing[-1][0], float(missing[-1][1])
    if now - at <= REFEREE_GRACE:
        return []
    return [Alert(f"referee:{head[:12]}", f"the referee copy at {head[:7]} lacks {len(missing)} main commits to "
                  f"referee/, mapgen/ or maps/, the oldest {oldest} from {when(at, now)}; refresh it at a clean boundary; "
                  f"see {state / 'referee'}")]


# The host status files to check: the evaluation's remote-status.json, whatever its age (the checks below date it),
# and every other run's written within STATUS_LIVE, a test running now on the same hosts: each tune's
# (runs/E####/remote-status.json), official-map report's (runs/E####/remote-status-official.json) and prepared CPU
# gate's (runs/E####/remote-status-prepared.json, driver.prepared_cpu_gate), the late
# screen's (rescreens/<champion>/), and in the loop home the sweep's, the prewarm's, the adversarial-map search's,
# each coder's (remote-status-E####.json) and a review's rule counts' (remote-status-review.json). A host resting through their
# games would otherwise go unseen, since none of them write the evaluation's file (BC_REMOTE_STATUS).
def status_files(state: Path, now: float) -> list[tuple[Path, dict]]:
    found = [(state / "remote-status.json", read_json(state / "remote-status.json"))]
    for path in sorted([*state.glob("runs/E*/remote-status*.json"), *state.glob("rescreens/*/remote-status.json"),
                        *state.glob("remote-status-*.json")]):
        status = read_json(path)
        if isinstance(status.get("updated"), (int, float)) and now - status["updated"] < STATUS_LIVE:
            found.append((path, status))
    return found


# loop/evalline.py's SHOWN (evalline-outages.json): the part-time hosts' outages the status line has named, {host:
# outage start}, one entry at most per part_time host in remote.json (down_once rewrites it while an evaluation runs,
# dropping outages that ended). Alerts when it isn't such a map: down_once then reads it as empty and names every
# outage again.
def evalline_outages(state: Path) -> list[Alert]:
    import evalline
    path = state / evalline.SHOWN
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        value = None
    if isinstance(value, dict) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values()):
        return []
    return [Alert("evalline outages", f"{path} isn't a map of host names to outage starts, so the status line names "
                  "every part-time host's outage again (evalline.down_once): delete it")]


# A host in remote.json that played no game in a test of at least HOST_TEST_GAMES games written within
# HOST_STATUS_AGE, or is resting now and has played nothing for HOST_REST (during a test still running), in any live
# status file (status_files). Hosts their owner switches off overnight alert too, except one marked "part_time" in
# remote.json, which is off by plan: while its outage (host-outages.json, from its second failure in a row) is under
# PART_TIME_DOWN old and that status file's last rest says it didn't answer (UNREACHABLE), it is quiet. A part-time
# host that answers and fails (a broken toolchain, a full disk), or isn't recorded as down (it answers, yet plays
# nothing), alerts as any other. Within PART_TIME_DOWN a real outage at night can't be told from one by plan.
def hosts(state: Path, now: float) -> list[Alert]:
    entries = [h for h in read_json(state / "remote.json").get("hosts", []) if isinstance(h, dict)]
    outages = read_json(state / "host-outages.json")
    off = {h.get("name") for h in entries if h.get("part_time") is True
           and isinstance(outages.get(h.get("name")), (int, float)) and now - outages[h["name"]] < PART_TIME_DOWN}

    def quiet(name: str, status: dict) -> bool:
        return name in off and bool(UNREACHABLE.search(str(((status.get("hosts") or {}).get(name) or {})
                                                            .get("last_rest") or "")))

    return [alert for path, status in status_files(state, now)
            for alert in hosts_in(path, status, [h.get("name") for h in entries if not quiet(h.get("name"), status)],
                                  now, path == state / "remote-status.json")]


# A remote.json host recorded down in host-outages.json (referee/remote.py OUTAGES: from its second failure in a row
# to its next game, across every run) for longer than one evaluation takes (recorded_evaluation: the median of the
# last 10; HOST_REST without one), so an outage that outlasts a test is named even though each test's own status
# file starts afresh (REFLECT 2026-09-30 15:13 F15: mm1 was left down until sig said "try mm1 again"). A part-time
# host is left to `hosts` (PART_TIME_DOWN: off by plan).
def host_outages(state: Path, now: float) -> list[Alert]:
    if not (outages := read_json(state / "host-outages.json")):
        return []  # the usual case: no host down, so no results to read
    limit, alerts = recorded_evaluation(state) or HOST_REST, []
    for host in read_json(state / "remote.json").get("hosts") or []:
        if not isinstance(host, dict) or host.get("part_time") is True:
            continue
        name = host.get("name")
        since = outages.get(name) if isinstance(name, str) else None
        if isinstance(since, (int, float)) and now - since > limit:
            alerts.append(Alert(f"host down:{name}:{since:.0f}", f"HOST DOWN: {name} has been down since "
                                f"{when(since, now)}, longer than one evaluation ({limit / 60:.0f} min); retry it now "
                                f"(`ssh {name} true`, then its last rest in remote-status.json) and bring it back or "
                                f"say why not; see {state / 'host-outages.json'}"))
    return alerts


# One status file's host alerts. Every other run's games (a filler's, a tune's, a coder's, a review's) yield to the
# evaluation, so a host the evaluation keeps full (a 2-core az VM) can rightly play none of them: outside the
# evaluation's file a host that played nothing alerts only when it also rested, which is a failure (SSH, setup or
# build) and not its share.
def hosts_in(path: Path, status: dict, names: list, now: float, evaluation: bool = True) -> list[Alert]:
    updated, games, rate = status.get("updated"), status.get("games") or 0, status.get("per_minute") or 0
    if not isinstance(updated, (int, float)):
        return []
    started = updated - games / rate * 60 if rate else updated
    alerts = []
    for name in names:
        host = (status.get("hosts") or {}).get(name)
        if not host:
            continue
        rest = str(host.get("last_rest") or "none").strip()
        if games >= HOST_TEST_GAMES and host.get("played") == 0 and now - updated < HOST_STATUS_AGE \
                and (evaluation or host.get("rests")):
            alerts.append(Alert(f"host:{name}:none", f"{name} played 0 of {games} games in the latest test, "
                                f"status written {when(updated, now)}; last rest: {rest}; see {path}"))
        elif host.get("resting") and now - updated < 600:
            last = clock_time(host["last_game"], updated) if host.get("last_game") else started
            if updated - last > HOST_REST:
                alerts.append(Alert(f"host:{name}:idle", f"{name} is resting and has played no game since "
                                    f"{when(last, now)}; last rest: {rest}; see {path}"))
    return alerts


# The Azure VM name of an az-* SSH alias in remote.json, which holds only the aliases: az-syd is `bc-burst`, the others
# `bc-burst-<suffix>` (memory note azure-burst-hosts).
def azure_vm(host: str) -> str:
    return "bc-burst" if host == "az-syd" else "bc-burst-" + host.removeprefix("az-")


# Each VM's power state ("VM running", "VM deallocated") from `az vm list -d`'s JSON, by VM name.
def power_states(text: str) -> dict[str, str]:
    rows = json.loads(text)
    return {row["name"]: str(row.get("powerState") or "no power state") for row in rows if isinstance(row, dict)
            and isinstance(row.get("name"), str)} if isinstance(rows, list) else {}


# The burst VMs' power states from one read-only `az vm list` call, or ({}, why) when az is missing, times out
# (AZURE_TIMEOUT), fails or prints no JSON. Never starts, stops or changes a VM.
def azure_power() -> tuple[dict[str, str], str | None]:
    command = ["az", "vm", "list", "-d", "-g", AZURE_GROUP, "--query", "[].{name:name, powerState:powerState}",
               "-o", "json"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=AZURE_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {}, "az is not installed"
    except subprocess.TimeoutExpired:
        return {}, f"az timed out after {AZURE_TIMEOUT} s"
    if done.returncode != 0:
        return {}, f"az exited {done.returncode}: {(done.stderr.strip().splitlines() or [''])[-1][:200]}"
    try:
        return power_states(done.stdout), None
    except ValueError:
        return {}, "az printed no JSON"


# An az-* host (remote.json) resting in every live host status file that lists it (status_files, written within
# STATUS_LIVE) for AZURE_DOWN, timed from its last game there (or that run's start) or from when an earlier check first
# saw it resting, gets one alert per outage with its VM's power state from Azure: the orchestrator had guessed "a spot
# eviction" instead of reading it (az-jp at 03:34 on 2026-09-27: "VM deallocated"). `seen` (health.json's "azure")
# keeps each outage's start and whether it alerted; a host seen playing ends it; with no live status file nothing is
# known and nothing changes. An unreadable Azure (`power`, azure_power) is one line for all of them.
def azure_hosts(state: Path, now: float, seen: dict, power: Callable = azure_power) -> list[Alert]:
    names = [h.get("name") for h in read_json(state / "remote.json").get("hosts", [])
             if str(h.get("name", "")).startswith("az-")]
    live = [status for _, status in status_files(state, now)
            if isinstance(status.get("updated"), (int, float)) and now - status["updated"] < STATUS_LIVE]
    down = {}
    for name in names:
        rows = [(status, status["hosts"][name]) for status in live if name in (status.get("hosts") or {})]
        if not rows:
            continue
        if not all(host.get("resting") for _, host in rows):
            seen.pop(name, None)
            continue
        since = min(clock_time(host["last_game"], status["updated"]) if host.get("last_game") else
                    status["updated"] - (status.get("games") or 0) / (status.get("per_minute") or 1e9) * 60
                    for status, host in rows)
        outage = seen.setdefault(name, {"since": since})
        outage["since"] = min(outage["since"], since)
        if now - outage["since"] >= AZURE_DOWN and not outage.get("alerted"):
            down[name] = outage
    if not down:
        return []
    states, error = power()
    for outage in down.values():
        outage["alerted"] = True
    minutes = {name: f"{name} down {(now - outage['since']) / 60:.0f} min" for name, outage in down.items()}
    if error:
        return [Alert("azure:unread", f"{', '.join(minutes.values())}: Azure's power state unread ({error}); "
                      f"see {state / 'remote-status.json'}")]
    return [Alert(f"azure:{name}:{outage['since']:.0f}", f"{minutes[name]}: Azure says "
                  f"{states.get(azure_vm(name), f'no VM {azure_vm(name)}')} (the restart job runs every 20 min)")
            for name, outage in down.items()]


# Games that compile the engine each game instead of loading it precompiled (referee/engine_cache.py): still right,
# just ~18% dearer. A referee run that couldn't use <loop home>/engine-cache (e.g. a toolkit whose engine.py isn't in
# engine_cache.KNOWN_ENGINES) leaves engine-cache-off.json, removed by the next run that can; a host whose own cache
# fell back shows the line its game printed in remote-status.json (a test running now, as `hosts`).
def engine_cache(state: Path, now: float) -> list[Alert]:
    alerts = []
    off = read_json(state / "engine-cache-off.json")
    if isinstance(off.get("at"), (int, float)) and now - off["at"] < ENGINE_CACHE_AGE:
        alerts.append(Alert("engine-cache", f"referee runs compile the engine every game ({when(off['at'], now)}): "
                            f"{str(off.get('reason'))[:200]}; see referee/engine_cache.py and "
                            f"{state / 'engine-cache-off.json'}"))
    for path, status in status_files(state, now):
        updated = status.get("updated")
        if isinstance(updated, (int, float)) and now - updated < HOST_STATUS_AGE:
            for name, host in sorted((status.get("hosts") or {}).items()):
                if note := host.get("engine_cache"):
                    alerts.append(Alert(f"engine-cache:{name}", f"{name}'s engine cache fell back in the latest "
                                        f"test: {str(note)[:200]}; see {path}"))
    return alerts


# Games that start every bot process as the toolkit does instead of forking it (referee/fork_server.py): still right,
# just ~30% dearer a native game. A referee run that couldn't use the shim (another toolkit, no compiler), or one of
# whose games fell back, rewrites fork-server-off.json (never removed: runs overlap), alerting for FORK_OFF_AGE; a
# host whose fork server fell back shows the line its game printed in remote-status.json. A check game whose forked
# replay differed from its plain one recorded its bots in fork-server-checks.json's "off", which switches forking off
# for every game until someone reads why and removes the entries: that alerts for as long as they stand, and so does
# a file that can't be read, which also stops all forking (run.forks_off). Only a difference whose plain game repeats
# when played again there does that; one whose plain game differs too puts both bots under "nondeterministic"
# (nondeterministic_bots), which switches nothing off.
def fork_server(state: Path, now: float) -> list[Alert]:
    alerts = []
    off = read_json(state / "fork-server-off.json")
    if isinstance(off.get("at"), (int, float)) and now - off["at"] < FORK_OFF_AGE:
        alerts.append(Alert("fork-server", f"referee games start every bot process as usual "
                            f"({when(off['at'], now)}): {str(off.get('reason'))[:200]}; see referee/fork_server.py "
                            f"and {state / 'fork-server-off.json'}"))
    path = state / "fork-server-checks.json"
    try:
        checks = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(checks, dict) or not isinstance(checks.get("off", {}), dict):
            raise ValueError("not a JSON object with an \"off\" object")
    except (OSError, ValueError) as error:
        return [*alerts, Alert("fork-server:checks", f"{path} can't be read ({error}), so no game forks its bots; "
                               f"fix or remove it")]
    for digest, why in sorted(checks.get("off", {}).items()):  # any entry stops all forking, whatever it holds
        why = why if isinstance(why, dict) else {"said": why}
        at = why.get("at") if isinstance(why.get("at"), (int, float)) else now
        alerts.append(Alert(f"fork-server:off:{digest}", f"a forked game differed from its plain one "
                            f"({when(at, now)}): {str(why.get('said'))[:200]}; bot {why.get('bot')}; no game forks "
                            f"until every entry under \"off\" in {path} is removed"))
    alerts += nondeterministic_bots("fork-server", path, checks, now)
    for path, status in status_files(state, now):
        updated = status.get("updated")
        if isinstance(updated, (int, float)) and now - updated < HOST_STATUS_AGE:
            for name, host in sorted((status.get("hosts") or {}).items()):
                if note := host.get("fork_server"):
                    alerts.append(Alert(f"fork-server:{name}", f"{name}'s fork server fell back in the latest "
                                        f"test: {str(note)[:200]}; see {path}"))
    return alerts


# One alert a standing "nondeterministic" entry of check file `path` (`found`, its contents; run.count_nondeterministic:
# a check that differed while its plain game, played twice there, differed too): a bot that doesn't repeat its own
# games, so its results are noise (MISSES 86), named by digest and experiment for as long as the entry stands.
def nondeterministic_bots(prefix: str, path: Path, found: dict, now: float) -> list[Alert]:
    entries = found.get("nondeterministic")
    alerts = []
    for digest, why in sorted(entries.items()) if isinstance(entries, dict) else ():
        why = why if isinstance(why, dict) else {"said": why}
        at = why.get("at") if isinstance(why.get("at"), (int, float)) else now
        experiment = re.search(r"\bE\d{4,}\b", str(why.get("bot")))
        alerts.append(Alert(f"{prefix}:nondeterministic:{digest}", f"bot {digest} "
                            f"({experiment.group(0) if experiment else 'no experiment'}: {why.get('bot')}) played a "
                            f"game differently twice ({when(at, now)}): {str(why.get('said'))[:200]}; its results are "
                            f"noise; read why, then remove its entry under \"nondeterministic\" in {path}"))
    return alerts


# Games that play through unswbc run instead of the native judge (referee/native_judge.py): still right, just dearer.
# From <loop home>/native-judge/enabled.json: the latest fidelity gate per key and place (referee/native_fidelity.py
# --gate, which maintenance.native_gates runs hourly wherever the current key has none) that failed within
# NATIVE_GATE_AGE or for the key --gate-missing last wanted (a mismatch means the judge or its build doesn't play the
# organisers' game, so it needs reading, and its key's games there stay off for as long as it is current), and, while
# the referee checkout's run.NATIVE_JUDGE is on, each place --gate-missing last wanted (enabled.json's "wanted") still
# without a gate after NATIVE_MISSING_AGE (this Mac's place counts as gated once its Mac hosts' is), or no
# --gate-missing run for that long. Then native-judge-checks.json's
# "off" (run.natives_off: a both-ways check that differed, or a judge that failed or hung on a game unswbc run
# played), which switches the judge off for every game until someone reads why and removes the entries, alerting for
# as long as they stand, as an unreadable file does and each bot under "nondeterministic" (nondeterministic_bots,
# which switches nothing off); and a host whose native_judge.py fell back shows the line its
# game printed in remote-status.json.
# The key is the live one, native_judge.key() in the referee checkout with the active toolchain (live_native_key),
# not the one --gate-missing last listed under "wanted", which trails a toolkit upgrade or referee refresh by up to
# an hour or, with no room for a gate, longer; when the two differ, each place wanted counts as wanted from that
# listing's time for the live key, and the listed key only when the live one can't be read.
def native_judge(state: Path, now: float) -> list[Alert]:
    alerts = []
    path = state / "native-judge" / "enabled.json"
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        keys = data.get("keys", {})
        rows = [row for places in keys.values() for row in places.values()]
        wanted = dict(data.get("wanted") or {})
        places = dict(wanted.get("places") or {})
    except (OSError, ValueError, AttributeError) as error:
        return [Alert("native-judge:file", f"{path} can't be read ({error}), so no game plays natively; fix or "
                      f"remove it")]
    if (live := live_native_key(state)) and live != wanted.get("key"):
        at = wanted.get("at")
        places = {place: max(since, at) if isinstance(at, (int, float)) and isinstance(since, (int, float)) else since
                  for place, since in places.items()}
        wanted["key"] = live
    here = f"{os.uname().sysname} {os.uname().machine}"  # remote.PLATFORM: this Mac, where health runs

    # A failed Mac hosts' gate keeps this Mac's own games off too while it has no row of its own (run.native_gated).
    def also_here(row: dict) -> str:
        return ("; this Mac's own native games are off too (with no gate of their own they go on this one: "
                "run.native_gated)" if row.get("platform") == f"{here} host"
                and here not in (keys.get(row.get("key")) or {}) else "")

    alerts += [Alert(f"native-judge:{row.get('key')}:{row.get('platform')}",
                     f"the native judge's gate for {row.get('key')} at {row.get('platform')} failed "
                     f"({when(row['at'], now)}): {str(row.get('why'))[:200]}; those games play through unswbc run"
                     f"{also_here(row)}; see {row.get('report')}")
               for row in rows if isinstance(row, dict) and row.get("passed") is False
               and isinstance(row.get("at"), (int, float))
               and (now - row["at"] < NATIVE_GATE_AGE or row.get("key") == wanted.get("key"))]
    switch = state / "referee" / "referee" / "run.py"
    try:
        on = bool(re.search(r"^NATIVE_JUDGE = True\b", switch.read_text(), re.M))
    except OSError:
        on = False
    if on:
        at = wanted.get("at")
        if not isinstance(at, (int, float)) or now - at > NATIVE_MISSING_AGE:
            alerts.append(Alert("native-judge:gates", f"run.NATIVE_JUDGE is on but no native_fidelity.py "
                                f"--gate-missing run has listed where the loop plays "
                                f"({when(at, now) if isinstance(at, (int, float)) else 'never'}); see "
                                f"{state / 'native-judge' / 'gates.log'}"))
        gated = keys.get(wanted.get("key")) or {}
        for place, since in sorted(places.items()):
            if place not in gated and f"{place} host" not in gated and isinstance(since, (int, float)) \
                    and now - since > NATIVE_MISSING_AGE:  # this Mac goes on its Mac hosts' gate (run.native_gated)
                alerts.append(Alert(f"native-judge:missing:{place}", f"no native judge gate for {wanted.get('key')} "
                                    f"at {place} (wanted {when(since, now)}), so its games play through unswbc run; "
                                    f"see {state / 'native-judge' / 'gates.log'}"))
    checks = state / "native-judge-checks.json"
    try:
        found = json.loads(checks.read_text()) if checks.exists() else {}
        if not isinstance(found, dict) or not isinstance(found.get("off", {}), dict) \
                or not isinstance(keys := found.get("differed_keys", []), list) \
                or not all(isinstance(key, str) for key in keys):
            raise ValueError("not a JSON object with an \"off\" object and a \"differed_keys\" list of keys")
    except (OSError, ValueError) as error:
        return [*alerts, Alert("native-judge:checks", f"{checks} can't be read ({error}), so no game plays "
                               f"natively; fix or remove it")]
    for digest, why in sorted(found.get("off", {}).items()):  # any entry stops the judge, whatever it holds
        why = why if isinstance(why, dict) else {"said": why}
        at = why.get("at") if isinstance(why.get("at"), (int, float)) else now
        alerts.append(Alert(f"native-judge:off:{digest}", f"the native judge was switched off ({when(at, now)}): "
                            f"{str(why.get('said'))[:200]}; bot {why.get('bot')}; no game plays natively until every "
                            f"entry under \"off\" in {checks} is removed"))
    alerts += nondeterministic_bots("native-judge", checks, found, now)
    for status_path, status in status_files(state, now):
        updated = status.get("updated")
        if isinstance(updated, (int, float)) and now - updated < HOST_STATUS_AGE:
            for name, host in sorted((status.get("hosts") or {}).items()):
                if note := host.get("native_judge"):
                    alerts.append(Alert(f"native-judge:{name}", f"{name}'s native judge fell back in the latest "
                                        f"test: {str(note)[:200]}; see {status_path}"))
    return alerts


# The native judge's current key (referee/native_judge.key: what the loop's games play under now), computed in the
# referee checkout by the active toolchain's Python, or None when there is no checkout or it can't be read.
def live_native_key(state: Path) -> str | None:
    referee = state / "referee" / "referee"
    python = maintenance.toolchain(state).get("python")
    if not python or not (referee / "native_judge.py").is_file():
        return None
    try:
        found = subprocess.run([python, "-c", "import native_judge; print(native_judge.key() or '')"], cwd=referee,
                               capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.strip() or None if found.returncode == 0 else None


# Games a referee run replayed because this machine, not a bot, failed them (run.infrastructure_failure: an OS error
# such as a fork refused at this user's process limit, or the game's process killed), from <loop home>/game-retries.json
# (run.note_retry). A replay costs a game, but a cause that recurs fails a whole test once a game's tries run out, as
# E0158's SPRT (1,716 trials) was lost, so a game lost on every try, or RETRY_ALERT_MIN replays, in the last RETRY_AGE
# alert, naming the latest and those never played.
def game_retries(state: Path, now: float) -> list[Alert]:
    rows = [row for row in read_json(state / "game-retries.json").get("games") or [] if isinstance(row, dict)
            and isinstance(row.get("at"), (int, float)) and now - row["at"] < RETRY_AGE]
    lost = sum(not row.get("recovered") for row in rows)
    if not rows or (not lost and len(rows) < RETRY_ALERT_MIN):  # a lone replay that played is the retry working
        return []
    last = rows[-1]
    return [Alert("game-retries", f"{len(rows)} referee games in the last {RETRY_AGE // 3600} h failed through this "
                  f"machine and were replayed, {lost} of them on every try; latest {when(last['at'], now)}, "
                  f"{Path(str(last.get('map'))).name} (a {last.get('a_side')}): {str(last.get('why'))[:200]}; find the "
                  f"cause (see run.infrastructure_failure) and {state / 'game-retries.json'}")]


# Maps the engine refused to load in the last RETRY_AGE (bad-maps.jsonl, referee/mapcheck.py's log: those
# driver.generate_maps quarantined as it made them, those an SPRT dropped for a spare, referee/sprt.py, and those a
# screen swapped or a shared set had replaced in place, driver.loadable_maps and repair_shared_map). None
# reaches a score any more, but each is a generator fault (E0260's worldgen dragon, MISSES 81) or a pool made before
# the check, worth reading.
def bad_maps(state: Path, now: float) -> list[Alert]:
    path = state / "bad-maps.jsonl"
    rows = [row for row in official.json_rows(path.read_text().splitlines() if path.exists() else [])
            if isinstance(row.get("at"), (int, float)) and now - row["at"] < RETRY_AGE]
    if not rows:
        return []
    last = rows[-1]
    return [Alert(f"bad-maps:{last['at']:.0f}", flat(
        f"{len(rows)} maps in the last {RETRY_AGE // 3600} h failed the engine's own loader and were kept out of "
        f"games; latest {when(last['at'], now)}, {Path(str(last.get('map'))).name}: {str(last.get('reason'))[:200]} "
        f"({str(last.get('note'))[:160]}); see {path}, and fix what made them (mapgen/)"))]


# A lazy SPRT's map marker (driver.MAPS_PENDING) or the count its SPRT reads beside it (<marker>.read) in a run folder,
# older than LAZY_STALE: the maker touches its marker every few seconds and removes both when its SPRT ends
# (driver.lazy_making), so they were left by a driver that died mid-SPRT. A count waits for LAZY_STALE with no fresh
# marker, since after a maker's failure its SPRT plays out the maps it read (run.AHEAD's bound) first. LAZY_STALE is
# referee/sprt.py's PENDING_STALE, after which that SPRT stopped waiting (a copy: the referee runs from a checkout
# loop/ doesn't import).
LAZY_STALE = 15 * 60


def lazy_markers(state: Path, now: float) -> list[Alert]:
    def age(path: Path) -> float | None:
        try:
            return now - path.stat().st_mtime
        except OSError:
            return None

    out = []
    for marker in sorted((state / "runs").glob("*/maps-pending*")):
        if marker.name not in ("maps-pending", "maps-pending.read"):
            continue
        maker = age(marker.with_name("maps-pending"))
        if (maker is None or maker >= LAZY_STALE) and (old := age(marker)) is not None and old >= LAZY_STALE:
            out.append(Alert(f"lazy marker:{marker}", flat(
                f"{marker} is {old / 60:.0f} min old with no live maker: the driver evaluating "
                f"{marker.parent.name} died mid-SPRT; check `ps` for its orphaned referee/sprt.py (it stops waiting "
                f"for maps after {LAZY_STALE // 60} min), then delete the file (a re-evaluation writes its own)")))
    return out


# An error result the engine's refusal of one of its maps, a native judge check or the loop's own code caused
# (prune.input_fault) whose candidate has no re-evaluation queued (an idea's "reevaluate") or recorded (a row's session
# "reevaluates") after REEVALUATE_GRACE: driver.reevaluate refused it or didn't run (a crash). A cap reached
# (driver.REEVALUATE_CAP) is the orchestrator's call too.
REEVALUATE_GRACE = 600


def input_faults(state: Path, now: float) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    rows = meta.latest_rows(official.json_rows([line for line in lines if '"error"' in line
                                                or '"reevaluates"' in line]))
    again = {(row.get("session") or {}).get("reevaluates") for row in rows}
    ideas = state / "ideas.jsonl"
    again |= {idea.get("reevaluate") for idea in official.json_rows(ideas.read_text().splitlines()
                                                                     if ideas.exists() else [])}
    return [Alert(f"input fault:{row['id']}", flat(
        f"{row['id']} ({row['idea'].get('id')}) ended in error through its inputs or the loop, not its candidate "
        f"({fault}), recorded {when(row['finished'], now)}, and its candidate isn't queued for re-evaluation: run "
        f"`uv run loop/driver.py --reevaluate {row['id']} --rule '<why>'`, which says why when it refuses"))
        for row in rows if row.get("verdict") == "error" and (fault := prune.input_fault(row))
        and row["id"] not in again and now - (row.get("finished") or now) > REEVALUATE_GRACE]


NICED_REPLAY_ALERT = 10  # lowered games replayed at normal priority in RETRY_AGE that alert (niced_replays)


# Lowered games on this Mac (run.GAME_NICE) stopped at a turn out of time and played again at normal priority
# (game-retries.json's "niced" rows, run.Scheduler.try_here): each is a game played twice, and many in RETRY_AGE mean
# dev work keeps every core busy for minutes on end, or the gate lets too many niced games start beside it.
def niced_replays(state: Path, now: float) -> list[Alert]:
    rows = [row for row in read_json(state / "game-retries.json").get("niced") or [] if isinstance(row, dict)
            and isinstance(row.get("at"), (int, float)) and now - row["at"] < RETRY_AGE]
    if len(rows) < NICED_REPLAY_ALERT:
        return []
    return [Alert("niced-replays", f"{len(rows)} lowered games here ran out of time and were played again at normal "
                  f"priority in the last {RETRY_AGE // 3600} h (latest {when(rows[-1]['at'], now)}: "
                  f"{str(rows[-1].get('why'))[:120]}): measure what held this Mac's CPU (tools/devload.sh, "
                  f"utilisation.jsonl) and whether native.game_slots let too many start; see "
                  f"{state / 'game-retries.json'}")]


SPLIT_STALE_ROWS = 3  # utilisation rows in a row with games here and no fresh CPU split that alert (cpu_split)


# This Mac's CPU split (home.split_copy, which one loop process samples for all, home.CpuSplit): a file there that is
# refused (unreadable, dated ahead or out of range: forged or corrupt, so every reader falls back to the load average),
# or none fresh while the loop's games but coders' played here for SPLIT_STALE_ROWS utilisation rows in a row (no
# sampler: every niced game is gated on the load average, which they themselves fill).
def cpu_split(state: Path, now: float) -> list[Alert]:
    path = home.split_copy()
    split, at = home.published()
    if path.exists() and at == float("-inf"):
        return [Alert("cpu-split:refused", f"{path} is refused (unreadable, dated ahead or a value outside 0 to "
                      f"{os.cpu_count()} cores), so every niced game here is gated on the load average: find what "
                      f"wrote it (home.publish_split writes it every {home.SPLIT_S:g} s)")]
    # A coder's games here don't count: its sandbox can neither lower their priority nor sample (they play at normal
    # priority, on the load gate), so with only theirs playing no process may be sampling, rightly.
    def trusted(here: dict) -> bool:
        by = here.get("by")
        return any(n > 0 and not name.startswith(("coder ", "review")) for name, n in by.items()) \
            if isinstance(by, dict) else here.get("busy", 0) > 0

    rows = throughput.read_rows(state, since=now - 600)[-SPLIT_STALE_ROWS:]
    playing = len(rows) == SPLIT_STALE_ROWS and all(trusted((row.get("hosts") or {}).get("here") or {}) for row in rows)
    if playing and now - at > 120:
        return [Alert("cpu-split:stale", f"the loop's games played here for {SPLIT_STALE_ROWS} min but no CPU split "
                      f"was published for {'ever' if at == float('-inf') else when(at, now)} ({path}): no process "
                      f"samples it (home.CpuSplit), so niced games here are gated on the load average they fill")]
    return []


# Spells in the last REPEAT (by their "at", which home.ProcessWatch refreshes every home.FULL_ALERT_S while one
# lasts) when this user's processes left no room for a game here for home.FULL_ALERT_S or more (<loop home>/
# process-limit.json, the newest home.SPELLS_KEPT): every game here waited meanwhile (native.game_slots), and a few
# hundred processes more would have refused every fork of the user's (Errno 35), sig's too. Names the process groups
# that held the most then. And spells of home.UNREAD_ALERT_S or more when the count or the limit couldn't be read:
# the gate was off (home.game_room fails open), so games started here unchecked.
def process_limit(state: Path, now: float) -> list[Alert]:
    path = state / home.spells_file().name
    rows = [row for row in read_json(path).get("spells") or [] if isinstance(row, dict)
            and isinstance(row.get("at"), (int, float)) and now - row["at"] < REPEAT]
    alerts = []
    if full := [row for row in rows if not row.get("unread")]:
        last = full[-1]
        since = last.get("since") if isinstance(last.get("since"), (int, float)) else last["at"]
        groups = ", ".join(str(group) for group in last.get("groups") or []) or "not read"
        alerts.append(Alert("process-limit", f"this user's processes left no room for a game here for {len(full)} "
                            f"spells of {home.FULL_ALERT_S // 60}+ min in the last {REPEAT // 3600} h (latest from "
                            f"{when(since, now)}, {(last['at'] - since) / 60:.0f} min when last seen: "
                            f"{last.get('count')} of kern.maxprocperuid {last.get('limit')}, home.game_room); largest "
                            f"process groups then: {groups}; find what holds them (a leaked game, a stuck batch "
                            f"outside native.game_slots) and see {path}"))
    if unread := [row for row in rows if row.get("unread")]:
        last = unread[-1]
        since = last.get("since") if isinstance(last.get("since"), (int, float)) else last["at"]
        alerts.append(Alert("process-limit-unread", f"this user's process count ({last.get('count')}) or limit "
                            f"({last.get('limit')}) couldn't be read for {(last['at'] - since) / 60:.0f} min from "
                            f"{when(since, now)} (home.process_table, home.process_limit): the process-limit gate was "
                            f"off meanwhile (home.game_room fails open), so games started here unchecked; see {path}"))
    return alerts


# One process from `ps`: its pid, parent, user, niceness, seconds alive, %CPU and command line.
@dataclass(frozen=True)
class PsRow:
    pid: int
    ppid: int
    user: str
    nice: int
    alive: float
    cpu: float
    args: str


def ps_rows(text: str | None = None) -> list[PsRow]:
    if text is None:
        try:
            text = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,user=,nice=,etime=,%cpu=,args="], capture_output=True,
                                  text=True, timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7 or not (m := home.TIME.match(parts[4])):
            continue
        try:
            days, hours, minutes, seconds = (float(g or 0) for g in m.groups())
            rows.append(PsRow(int(parts[0]), int(parts[1]), parts[2], int(parts[3]),
                              ((days * 24 + hours) * 60 + minutes) * 60 + seconds, float(parts[5]), parts[6]))
        except ValueError:
            continue
    return rows


# How long an evaluation takes to play its trials: the newest SPRT's pair cap (its evaluation.json's max_pairs) at its
# current rate (trials over the time since its sprt-progress.json was first written), while one has run ETA_AFTER and
# updated within RECENT_SPRT; else the median time of the last 10 recorded evaluations (recorded_evaluation); None
# when neither is known.
RECENT_SPRT = 600
ETA_AFTER = 300


def evaluation_seconds(state: Path, now: float) -> float | None:
    progress = sorted((state / "runs").glob("E*/sprt-progress.json"), key=lambda p: p.stat().st_mtime)
    if progress and now - progress[-1].stat().st_mtime < RECENT_SPRT:
        started, trials = throughput.created(progress[-1]), read_json(progress[-1]).get("trials")
        cap = read_json(progress[-1].parent / "evaluation.json").get("max_pairs") or 2400
        if now - started >= ETA_AFTER and isinstance(trials, int) and trials > 0:
            return cap * (now - started) / trials
    return recorded_evaluation(state)


# The median time of the last 10 recorded evaluations, from their bot's export (cand/) to the result's `finished`, or
# None. results.jsonl is 184 MB (28 Sep: 1.4 s of CPU and ~600 MB to read), so the answer is kept for as long as the
# file's path, size and mtime stay the same (_recorded: a new result changes them).
_recorded: tuple = (None, None)


def recorded_evaluation(state: Path) -> float | None:
    global _recorded
    path = state / "results.jsonl"
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_size, info.st_mtime_ns)
    if _recorded[0] != key:
        _recorded = (key, median_evaluation(state, throughput.jsonl(path)))
    return _recorded[1]


# That median from results.jsonl's rows `results`, oldest first.
def median_evaluation(state: Path, results: list) -> float | None:
    times = []
    for row in results[::-1]:
        cand = state / "runs" / str(row.get("id")) / "cand" if isinstance(row, dict) else None
        if cand and isinstance(row.get("finished"), (int, float)) and cand.exists():
            if (spent := row["finished"] - throughput.created(cand)) > 0:
                times.append(spent)
        if len(times) == 10:
            break
    return sorted(times)[len(times) // 2] if times else None


# The name of the loop session `pid` runs under: the nearest ancestor started with `--name` (a coder's bc-E####-...,
# a named main), else the nearest `claude` ancestor as "claude pid N", else "no Claude session".
def session_of(pid: int, by_pid: dict[int, PsRow]) -> str:
    seen = set()
    while (row := by_pid.get(pid)) and pid not in seen:
        seen.add(pid)
        words = row.args.split()
        if "--name" in words[:-1]:
            return words[words.index("--name") + 1]
        if words and Path(words[0]).name == "claude":
            return f"claude pid {pid}"
        pid = row.ppid
    return "no Claude session"


# Jobs this Mac's load gate has held for longer than an evaluation takes (evaluation_seconds), with the processes
# busiest at dev priority (below home.WORKER_NICE: what the gate counts) and whose they are. E0240's coder's sandbox
# CPU games waited 2.5 h (28 Sep) and the orchestrator heard of it from the coder. The waiters, as the process table
# shows them: a tools/gated_test.sh not yet running its command (it waits only before it, so since it started), and
# a run that plays only here (MAC_ONLY: sandbox games, the evaluation's CPU gate and identical check) with no child
# process, i.e. no game playing (a game on a host, a CPU gate's with --hosts too, runs through an ssh child), since
# its first such sighting (`seen`, health.json's "mac waits", keeps each waiter's start; every minute, quick).
# quick.py's CPU check isn't one: it gives up after native.CPU_WAIT_S. A gated_test.sh has started its command once it
# has any child but its own helpers (GATE_HELPERS, or a subshell of itself, whose args are its own): the command's
# process needn't show the args it was given (`env X=1 ...`, `nice ...`, a framework python3 showing as
# .../Python.app/Contents/MacOS/Python). Nothing is judged before WAIT_FLOOR, so a short wait never reads
# the recorded evaluations. One alert a spell, keyed by its longest waiter's start.
MAC_ONLY = re.compile(r"native\.py (?:games .*--sandbox|play \S*referee/(?:cpu|identical)\.py)")
GATE_HELPERS = {"sleep", "awk", "sysctl", "getconf", "uptime", "sed", "tr", "cut", "date", "nproc"}
WAIT_FLOOR = 600


def gate_helper(kid: PsRow, parent: PsRow) -> bool:
    words = kid.args.split()
    return kid.args == parent.args or bool(words) and (Path(words[0]).name in GATE_HELPERS
                                                        or words[:2] == ["python3", "-c"])


# The tools/gated_test.sh processes in `rows` still waiting on the gate (no child but its own helpers), each waiting
# since it started (row.alive). loop/evalline.py's agents line names them too (REFLECT 2026-09-28 20:52 F11).
def gate_waiting(rows: list[PsRow]) -> list[PsRow]:
    children: dict[int, list[PsRow]] = {}
    for row in rows:
        children.setdefault(row.ppid, []).append(row)
    return [row for row in rows if is_gate(row) and all(gate_helper(kid, row) for kid in children.get(row.pid, []))]


def is_gate(row: PsRow) -> bool:
    return (words := row.args.split(maxsplit=2))[1:2] != [] and words[1].endswith("tools/gated_test.sh")


# tools/gated_test.sh processes waiting over WAIT_FLOOR while no CPU split has been published for as long
# (home.published): in a coder's sandbox, where ps is refused, such a gate can't measure the split itself and falls
# back to the whole-machine 1-minute load, the loop's niced games included (29 Sep: coders' and merge turns' bot tests
# waited 57 min at load 13-17). The utilisation sampler keeps a CPU sampler running wherever the loop runs
# (throughput.sampling), so this means no loop process could sample.
def gate_fallback(now: float, rows: list[PsRow] | None = None) -> list[Alert]:
    _, at = home.published()
    if now - at <= WAIT_FLOOR:
        return []
    waiting = [row for row in gate_waiting(ps_rows() if rows is None else rows) if row.alive > WAIT_FLOOR]
    if not waiting:
        return []
    since = "never" if at == -math.inf else when(at, now)
    return [Alert("gate-fallback", f"{len(waiting)} tools/gated_test.sh runs have waited over {WAIT_FLOOR // 60} min "
                  f"(longest pid {max(waiting, key=lambda r: r.alive).pid}, {max(r.alive for r in waiting) / 60:.0f} "
                  f"min) while no CPU split has been published ({since}): any in a coder's sandbox gates on the "
                  f"1-minute load, the loop's niced games included; find why no loop process samples (home.CpuSplit, "
                  f"throughput.sampling) and {home.split_copy()}")]


def mac_waits(state: Path, now: float, seen: dict, rows: list[PsRow] | None = None) -> list[Alert]:
    rows = ps_rows() if rows is None else rows
    by_pid, parents = {row.pid: row for row in rows}, {row.ppid for row in rows}
    waiting: dict[int, float] = {}
    for row in gate_waiting(rows):  # ps's times are whole seconds: keep the first
        waiting[row.pid] = seen.setdefault(str(row.pid), now - row.alive)
    for row in rows:
        if MAC_ONLY.search(row.args) and row.pid not in parents and not is_gate(row):
            waiting[row.pid] = seen.setdefault(str(row.pid), now)
    for key in [key for key in seen if not key.isdecimal() or int(key) not in waiting]:
        seen.pop(key)
    floored = any(now - since > WAIT_FLOOR for since in waiting.values())
    known = evaluation_seconds(state, now) if floored else None
    limit = max(WAIT_FLOOR, known) if known else None
    late = sorted((since, pid) for pid, since in waiting.items() if limit and now - since > limit)
    if not late:
        return []
    jobs = "; ".join(f"pid {pid} {(now - since) / 60:.0f} min ({session_of(pid, by_pid)}): {by_pid[pid].args[:120]}"
                     for since, pid in late)
    busy = sorted((row for row in rows if row.nice < home.WORKER_NICE and row.pid not in waiting),
                  key=lambda row: -row.cpu)[:5]
    top = "; ".join(f"pid {row.pid} {row.cpu:.0f}% {row.user} ({session_of(row.pid, by_pid)}): {row.args[:80]}"
                    for row in busy)
    return [Alert(f"mac waits:{int(late[0][0])}",
                  f"MAC GATE STALL: {len(late)} job(s) have waited on this Mac's load gate longer than an evaluation "
                  f"takes ({limit / 60:.0f} min, health.evaluation_seconds): {jobs}. Busiest at dev priority now: "
                  f"{top}; tools/devload.sh shows the gate")]


# Processes the loop didn't start holding more than OTHER_SHARE of this Mac's cores through OTHER_WINDOW (sig, 29 Sep:
# a request to him to stop one, such as Spotlight's mds_stores and corespotlightd or PerfPowerServices, comes with
# evidence). The loop's games here take only the cores other work leaves idle (home.niced_slots, home.mac_slots: a game
# a core), so each core such work holds is a game slot fewer, and with the load share of cores (home.load_share) it
# alone closes every one. Every minute (quick) it reads each process's %cpu, which on macOS is a decaying average over
# the last minute, and sums those not the loop's (loop_process), keeping the last OTHER_WINDOW of readings with each
# command's share in health.json's "other cpu" (a gap over OTHER_GAP starts them again). It alerts when their mean is
# over the share and they span the window, once a spell (the mean over the share without a break), naming the busiest
# commands on average. It never acts on them. Processes that start and end between readings are missed: it reads low.
# The share: this Mac's other work measured 0.5-1.8 cores in 15 s readings on 29 Sep 13:15, 0.3-0.8 of it Tailscale's
# IPNExtension (left out since: NETWORK), so 1.5 cores on average for 20 min is more than that, about two game slots.
OTHER_SHARE = 0.15
OTHER_WINDOW = 20 * 60
OTHER_GAP = 5 * 60
# The loop's ssh masters, markers and workers' private tmp folders (meta.private_tmp: /tmp/bcw...); the gstack
# browser's daemon.
OTHER_MARKS = ("/tmp/bc", "gstack/browse")
# macOS runs some of its own background work niced too (Spotlight's mdbulkimport at 17, 29 Sep), so a niced process
# from these folders isn't taken for the loop's.
SYSTEM_PATHS = ("/System/", "/usr/libexec/", "/usr/sbin/", "/Library/Apple/")


# Tailscale carries sig's SSH and every ssh to the loop's hosts, so its CPU is partly the loop's own traffic, and
# restarting it is never asked for: other_cpu leaves it out.
NETWORK = ("IPNExtension", "tailscaled", "Tailscale")
APP_BINARY = re.compile(r"\.app(?:ex)?/Contents/MacOS/(.+?)(?= -|$)")


# A process's program name from its command line: an app bundle's binary in full (its name may have spaces: "Google
# Chrome for Testing"), else the first word's file name.
def program_name(args: str) -> str:
    if bundle := APP_BINARY.search(args):
        return bundle.group(1).strip()
    return Path(words[0]).name if (words := args.split()) else ""


# Whether process `pid` or an ancestor is the loop's or a Claude session's: a `claude` or a game's bot, with a command
# line naming one of `marks` (the checkout, the loop home, the clones: its drivers, referee and tools, orphans too), or
# at WORKER_NICE or lower (its games and headless workers with all they start, orphans too) but not the system's.
def loop_process(pid: int, by_pid: dict[int, PsRow], marks: tuple[str, ...]) -> bool:
    seen = set()
    while pid > 1 and (row := by_pid.get(pid)) and pid not in seen:
        seen.add(pid)
        name = Path(words[0]).name if (words := row.args.split()) else ""
        if name in ("claude", home.BOT_NAME) or any(mark in row.args for mark in marks) \
                or row.nice >= home.WORKER_NICE and not row.args.startswith(SYSTEM_PATHS):
            return True
        pid = row.ppid
    return False


def other_cpu(state: Path, repo: Path, now: float, seen: dict, rows: list[PsRow] | None = None,
              cores: int | None = None) -> list[Alert]:
    rows = ps_rows() if rows is None else rows
    if not rows:
        return []  # ps refused: nothing measured
    cores = cores or os.cpu_count() or 1
    by_pid = {row.pid: row for row in rows}
    marks = (str(repo), str(state), str(clones_folder(repo)), *OTHER_MARKS)
    busy: dict[str, list] = {}
    for row in rows:
        if row.cpu > 0 and not loop_process(row.pid, by_pid, marks) \
                and (name := program_name(row.args) or f"pid {row.pid}") not in NETWORK:
            entry = busy.setdefault(name, [0.0, row.user])
            entry[0] += row.cpu / 100
    readings = [row for row in seen.get("rows") or [] if now - row[0] <= OTHER_WINDOW]
    if readings and now - readings[-1][0] > OTHER_GAP:
        readings = []  # no reading for a while: the average starts again
    readings.append([now, round(sum(entry[0] for entry in busy.values()), 3),
                     {name: [round(used, 3), user] for name, (used, user) in busy.items() if used >= 0.05}])
    seen["rows"] = readings
    mean = sum(row[1] for row in readings) / len(readings)
    if mean <= OTHER_SHARE * cores:
        seen.pop("since", None)
        return []
    if now - readings[0][0] < 0.9 * OTHER_WINDOW:
        return []  # not sustained yet
    since = seen.setdefault("since", readings[0][0])
    names: dict[str, list] = {}
    for _, _, rows_named in readings:
        for name, (used, user) in rows_named.items():
            names[name] = [names.get(name, [0.0])[0] + used / len(readings), user]
    top = sorted(names.items(), key=lambda item: -item[1][0])[:6]
    named = ", ".join(f"{name} {used:.1f} ({user})" for name, (used, user) in top)
    try:
        share = home.load_share((state / "load-fraction").read_text())
    except OSError:
        share = home.load_share("")
    closed = (f"; that alone reaches the load share ({share * cores:.1f} cores, home.load_share), so no niced game "
              "starts here") if mean >= share * cores else ""
    return [Alert(f"other cpu:{int(since)}", flat(
        f"OTHER CPU: processes the loop didn't start have held {mean:.1f} of this Mac's {cores} cores on average "
        f"(over {OTHER_SHARE:.0%}) over the last {(now - readings[0][0]) / 60:.0f} min (a spell from "
        f"{when(since, now)}), about {round(mean)} game slots here (a game a core other work leaves idle: "
        f"home.niced_slots){closed}. Busiest (cores on average, user): {named}. A "
        f"system service among them (Spotlight: mds_stores, mdworker, corespotlightd; PerfPowerServices) is sig's "
        f"to stop or exclude: send him these numbers; sig's own apps are his work. Never act on the processes"))]


# An experiment clone whose processes hold more than CLONE_SHARE of this Mac's cores through CLONE_WINDOW (REFLECT
# 2026-09-30 09:12 F9: a coder's own measurement games here starve the builders and dev work that come first on this
# Mac; its PROMPT.md says to play them on the hosts). A process is a clone's when it or its nearest such ancestor names
# the clone's folder (<clones folder>/E####, orphaned game runs too) or runs under its coder's session (`--name
# bc-E####-...`). Every minute (quick) it sums each clone's %cpu (a decaying average over the last minute, as
# other_cpu reads it), keeping the last CLONE_WINDOW of readings in `seen` (health.json's "clone cpu"; a gap over
# OTHER_GAP starts them again), and alerts once a spell per clone when the mean is over the share and the readings
# span the window. It never acts on the processes.
CLONE_SHARE = 0.5
CLONE_WINDOW = 10 * 60
CLONE_SESSION = re.compile(r"^bc-(E\d{4})\b")


def clone_of(pid: int, by_pid: dict[int, PsRow], folder: re.Pattern) -> str | None:
    seen = set()
    while pid > 1 and (row := by_pid.get(pid)) and pid not in seen:
        seen.add(pid)
        if found := folder.search(row.args):
            return found.group(1)
        words = row.args.split()
        if "--name" in words[:-1] and (named := CLONE_SESSION.match(words[words.index("--name") + 1])):
            return named.group(1)
        pid = row.ppid
    return None


def clone_cpu(state: Path, repo: Path, now: float, seen: dict, rows: list[PsRow] | None = None,
              cores: int | None = None) -> list[Alert]:
    rows = ps_rows() if rows is None else rows
    if not rows:
        return []  # ps refused: nothing measured
    cores = cores or os.cpu_count() or 1
    by_pid = {row.pid: row for row in rows}
    folder = re.compile(re.escape(str(clones_folder(repo))) + r"/(E\d{4})(?![\w])")
    used: dict[str, float] = {}
    programs: dict[str, dict[str, float]] = {}  # this reading's, by clone: the busiest commands the alert names
    for row in rows:
        if row.cpu > 0 and (exp := clone_of(row.pid, by_pid, folder)):
            used[exp] = used.get(exp, 0.0) + row.cpu / 100
            named = programs.setdefault(exp, {})
            named[program_name(row.args)] = named.get(program_name(row.args), 0.0) + row.cpu / 100
    readings = [r for r in seen.get("rows") or [] if now - r[0] <= CLONE_WINDOW]
    if readings and now - readings[-1][0] > OTHER_GAP:
        readings = []
    readings.append([now, {exp: round(n, 2) for exp, n in used.items() if n >= 0.05}])
    seen["rows"] = readings
    spells, alerts = seen.setdefault("since", {}), []
    for exp in sorted({exp for _, found in readings for exp in found}):
        mean = sum(found.get(exp, 0) for _, found in readings) / len(readings)
        if mean <= CLONE_SHARE * cores:
            spells.pop(exp, None)
            continue
        if now - readings[0][0] < 0.9 * CLONE_WINDOW:
            continue  # not sustained yet
        since = spells.setdefault(exp, readings[0][0])
        top = sorted((programs.get(exp) or {}).items(), key=lambda item: -item[1])[:4]
        alerts.append(Alert(f"clone cpu:{exp}:{int(since)}", flat(
            f"CLONE CPU: {exp}'s processes (its clone {clones_folder(repo) / exp}) have held {mean:.1f} of this Mac's "
            f"{cores} cores on average over the last {(now - readings[0][0]) / 60:.0f} min (over "
            f"{CLONE_SHARE:.0%}; a spell from {when(since, now)}); busiest now (cores): "
            f"{', '.join(f'{name} {n:.1f}' for name, n in top) or 'none'}. Builders and sig's work come first on "
            f"this Mac: tell its coder to play through its runner's games (the hosts take most) and keep --workers "
            f"and parallel analysis here to a few cores (PROMPT.md)")))
    for exp in [exp for exp in spells if not any(exp in found for _, found in readings)]:
        spells.pop(exp)
    return alerts


# The loop's own processes (`loop/native.py games|play`, `loop/driver.py`: IDLE_PROGRAMS, run by a python) that,
# with every process under them, have used no CPU time (under IDLE_CPU seconds more) for IDLE_HOURS (REFLECT
# 2026-10-01 15:21 F6: nine `native.py games` runs left by 30 Sep 20:50's outage sat at 0% for 16+ h, three more from
# 29 Sep for two days). Each check reads every process's CPU time (cpu_rows, ps's `time`) and keeps in `seen`
# (health.json's "idle loop") each such pid's total with when it last grew; a pid gone or reused (another start
# time) starts again. One unchanged for IDLE_HOURS is listed with its pid, age and parent, once a spell. A driver
# waiting on its coder isn't idle, as the coder under it uses CPU. It never acts on the processes: reaping them is
# sig's call.
IDLE_HOURS = 6
IDLE_CPU = 1.0
IDLE_PROGRAMS = re.compile(r"\bloop/native\.py (?:games|play)\b|\bloop/driver\.py\b")


# (pid, parent, seconds alive, CPU seconds, command line) for each process, or [] when ps fails.
def cpu_rows(text: str | None = None) -> list[tuple[int, int, float, float, str]]:
    if text is None:
        try:
            text = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,etime=,time=,args="], capture_output=True, text=True,
                                  timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5 or not (alive := home.TIME.match(parts[2])) or not (used := home.TIME.match(parts[3])):
            continue
        seconds = [((float(d or 0) * 24 + float(h or 0)) * 60 + float(m)) * 60 + float(s)
                   for d, h, m, s in (alive.groups(), used.groups())]
        rows.append((int(parts[0]), int(parts[1]), seconds[0], seconds[1], parts[4]))
    return rows


def idle_loop(now: float, seen: dict, rows: list[tuple[int, int, float, float, str]] | None = None) -> list[Alert]:
    rows = cpu_rows() if rows is None else rows
    if not rows:
        return []  # ps refused: nothing measured
    children: dict[int, list[int]] = {}
    for pid, ppid, *_ in rows:
        children.setdefault(ppid, []).append(pid)
    used = {pid: cpu for pid, _, _, cpu, _ in rows}

    def tree_cpu(pid: int) -> float:
        total, todo, done = 0.0, [pid], set()
        while todo:
            if (next_pid := todo.pop()) not in done:
                done.add(next_pid)
                total += used.get(next_pid, 0.0)
                todo += children.get(next_pid, [])
        return total

    kept, idle = {}, []
    for pid, ppid, alive, _, args in rows:
        words = args.split()
        if not words or not Path(words[0]).name.startswith("python") or not IDLE_PROGRAMS.search(args):
            continue
        cpu, started = tree_cpu(pid), round(now - alive)
        last = seen.get(str(pid))
        if not last or abs(last["started"] - started) > 120 or cpu - last["cpu"] >= IDLE_CPU:
            last = {"started": started, "cpu": cpu, "since": now}
        kept[str(pid)] = last
        if now - last["since"] >= IDLE_HOURS * 3600:
            idle.append((pid, ppid, alive, now - last["since"], IDLE_PROGRAMS.search(args)[0].split("/")[-1], args))
    seen.clear()
    seen.update(kept)
    if not idle:
        return []
    listed = "; ".join(f"pid {pid} ({program} {args.split(program, 1)[1].strip()[:40]}, alive "
                       f"{alive / 3600:.0f} h, parent {ppid})" for pid, ppid, alive, _, program, args in idle[:12])
    return [Alert(f"idle loop:{','.join(str(row[0]) for row in idle)}", flat(
        f"IDLE LOOP PROCESSES: {len(idle)} of the loop's own processes (native.py games/play, driver.py) have used "
        f"no CPU, with all under them, for {IDLE_HOURS} h or more (the longest {max(row[3] for row in idle) / 3600:.0f} h "
        f"as measured): "
        f"{listed}{'; ...' if len(idle) > 12 else ''}. Left over from an "
        f"outage or a stopped coder, they hold processes against the per-user limit (MISSES 67). The loop never kills "
        f"them: tell sig, with these pids, and ask once for a standing grant to reap the loop's own leftovers"))]


PREPARED_REF_AGE = 3600  # a refs/prepared/<id> this long after its experiment was recorded was left behind
PREPARED_WINDOW = 12 * 3600  # prepared_stalled reads the experiments recorded this recently
PREPARED_STALLED = 3  # this many of them stalled, with none landing a prepared CPU gate, alert
# A prepare stopped within this of reaching its checks had no chance to finish them: bot tests 26 s and CPU gate 58 s
# measured (E0180), three and a half times over.
PREPARED_CHANCE_S = 300


# The experiments recorded in the last PREPARED_WINDOW whose prepare reached its checks (it exported the candidate
# into prepared/) and stalled: its bot tests didn't fail, no CPU gate was saved in prepared.json, and it stopped over
# PREPARED_CHANCE_S after reaching them ("checks_at" to "stopped_at", driver.prepare) or has no stop recorded (a
# record from before 2026-09-28's fix, or a driver that died waiting); when there are PREPARED_STALLED or more and none
# of the others there saved a CPU gate, prepare delivers nothing. Delivered, not stalled: a CPU gate saved, bot tests
# that failed (no gate to run), or a stop within PREPARED_CHANCE_S (the evaluation beside it ended first). On 28 Sep
# every prepare waited for the players' half of this Mac until the lock freed (native.wait_for_dev's comment), for
# 17 h with no alert. A candidate on an older champion reaches the checks only after its merge turn there (the Claude
# driver's, driver.prepare); one that doesn't never has prepared/, so it doesn't count.
# Not a count claim needing a chance (CLAUDE.md, Counts come with their chance): each case counted is itself a failure
# of a deterministic step, a prepare that had PREPARED_CHANCE_S beside the evaluation and delivered nothing, not a
# sample of a rate luck could move; the threshold only keeps one slow evening (dev work over the share throughout, the
# gate's games waiting) from alerting, and the alert names the runs to read.
# With `promoted` (champion_times), each evaluation that ran unprepared though it waited longer than a prepare takes
# (unprepared_waits) is named too, one by one: no count threshold, since each is a prepare that had its time.
def prepared_stalled(state: Path, now: float, promoted: dict[str, float] | None = None) -> list[str]:
    waited = unprepared_waits(state, now, promoted) if promoted is not None else []
    stalled, landed = [], 0
    for path in sorted((state / "runs").glob("*/prepared")):
        if not path.is_dir():
            continue
        run = path.parent
        finished = read_json(run / "experiment.json").get("finished")
        if not isinstance(finished, (int, float)) or not 0 < now - finished < PREPARED_WINDOW:
            continue
        record = read_json(run / "prepared.json")
        if "cpu" in record:
            landed += 1
            continue
        tests, reached, stopped = record.get("bot_tests"), record.get("checks_at"), record.get("stopped_at")
        if isinstance(tests, dict) and tests.get("ok") is False:
            continue
        if isinstance(reached, (int, float)) and isinstance(stopped, (int, float)) \
                and stopped - reached <= PREPARED_CHANCE_S:
            continue
        stalled.append(run.name)
    stalled = stalled if len(stalled) >= PREPARED_STALLED and not landed else []
    return [*stalled, *(name for name in waited if name not in stalled)]


# A prepare takes about this long when it has a merge turn to give (1-6 min, driver.prepare) and the evaluation beside
# it must reach its host stage before the prepared CPU gate plays (its bot tests 14-60 s, CPU gate and maps 56-76 s,
# method M89), with the checks themselves (bot tests 26 s, CPU gate 58 s: PREPARED_CHANCE_S).
PREPARE_TAKES_S = 900


# The experiments recorded in the last PREPARED_WINDOW that ran their CPU gate under the lock (no "prepared" on
# their result's gate) although they waited longer than PREPARE_TAKES_S behind another evaluation on the champion
# they were tested against: from when it became champion (`promoted`, {commit: time}), they finished coding or they
# rejoined the queue after waiting out a usage limit on their merge turn (session "requeued_at"), the latest, to when
# they took the lock (session "locked_at"; before 2026-09-29, when their bot was exported, or their
# merge turn began under the lock if earlier). A stack's (its tip is built at evaluate) and a gate that didn't run (a
# same-code identical idea's, failed bot tests) aren't counted. Method M89: E0329 waited 33 minutes behind E0325 on
# d151b77 with its checks prepared for the commit the rebase replaced (MISSES 84's class: a prepare that silently
# saved nothing).
def unprepared_waits(state: Path, now: float, promoted: dict[str, float]) -> list[str]:
    found = []
    for run in sorted((state / "runs").glob("E*")):
        own = read_json(run / "experiment.json")
        finished, cpu, session = own.get("finished"), own.get("cpu"), own.get("session") or {}
        if not isinstance(finished, (int, float)) or not 0 < now - finished < PREPARED_WINDOW \
                or not isinstance(cpu, dict) or "line" not in cpu or cpu.get("prepared") \
                or own.get("stacked_on") or session.get("stack"):
            continue
        locked = session.get("locked_at")
        if not isinstance(locked, (int, float)):
            try:
                locked = throughput.created(run / "cand")
            except OSError:
                continue
            if isinstance(resolve := session.get("resolve"), (int, float)):
                locked = min(locked, resolve)
        coded, requeued = session.get("coded_at"), session.get("requeued_at")
        since = max(promoted.get((own.get("evaluation") or {}).get("base") or "", 0.0),
                    coded if isinstance(coded, (int, float)) else locked,
                    requeued if isinstance(requeued, (int, float)) else 0.0)
        if locked - since > PREPARE_TAKES_S:
            found.append(f"{run.name} (waited {(locked - since) / 60:.0f} min)")
    return found


# driver.make_set_ahead's first real firings and any later miss: the newest champion, promoted in the last
# SET_AHEAD_WINDOW after a real opponent screen (its row's pool names opponents: not skipped or unscreened, nor a
# judged keep, rejudge or target batch), whose queue head (the first candidate coded before the promotion to take the
# lock after it, the one the screen ran beside) reads a shared set (driver.shared_general), yet that set lacks
# driver.MADE_AHEAD: the head made nothing during the screen and the first test waited for the set. Only a head whose
# queue poll ran make_set_ahead counts (its session's "set_ahead_code", written on taking the lock): an older process
# polls on older code until then (driver.hand_off). The experiment files read are those written since the promotion (a
# few, 3-5 MB each).
SET_AHEAD_WINDOW = 6 * 3600


def set_ahead(state: Path, now: float, promoted: dict[str, float] | None) -> str | None:
    import driver  # lazily: the driver imports this module
    if not promoted:
        return None
    champion, at = max(promoted.items(), key=lambda item: item[1])
    if now - at > SET_AHEAD_WINDOW:
        return None
    screened, head = False, None
    for path in (state / "runs").glob("E*/experiment.json"):
        with suppress(OSError):
            if path.stat().st_mtime < at:  # the promoted one and its waiters are saved after the promotion
                continue
        row = read_json(path)
        session = row.get("session") or {}
        if row.get("candidate") == champion:
            screened = bool((row.get("pool") or {}).get("opponents")) \
                and not (row.get("judged") or row.get("rejudged") or row.get("target"))
        coded, locked = session.get("coded_at"), session.get("locked_at")
        if isinstance(coded, (int, float)) and isinstance(locked, (int, float)) and coded < at < locked \
                and (head is None or locked < head[0]):
            head = locked, path.parent.name, row
    if not screened or head is None or not head[2]["session"].get("set_ahead_code"):
        return None
    try:
        found = driver.shared_general(head[2].get("evaluation") or {})
    except Exception:  # an old or partial policy: nothing to check it against
        return None
    folder = state / "shared-maps" / driver.meta.shared_set_name(champion, *found) if found else None
    if folder is None or not folder.is_dir() or (folder / driver.MADE_AHEAD).exists():
        return None
    return (f"champion {champion[:7]}, promoted {when(at, now)} from a screen with {head[1]} at the queue's head, has "
            f"no shared set made during that screen ({driver.MADE_AHEAD} not in {folder.name}): "
            "driver.make_set_ahead didn't run or failed, so the first test on it waited for its maps; read the "
            "head's --once log for its 'champion-to-come' line")


# {commit: when it first became champion} from the main repo's reflog of the champion branch (driver.CHAMPION_BRANCH);
# {} when it can't be read.
def champion_times(repo: Path) -> dict[str, float]:
    try:
        lines = git(repo, "reflog", "show", "--format=%H %gd", "--date=unix", "loop/champion").splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    times = {}
    for line in lines:
        commit, _, ref = line.partition(" ")
        if (stamp := ref.rpartition("@{")[2].rstrip("}")).isdigit():
            times[commit] = min(times.get(commit, math.inf), float(stamp))
    return times


PREPARED_FAILS = 3  # this many prepared checks in a row that failed where evaluate's own didn't, alert


# The latest prepared checks, newest last, when the last PREPARED_FAILS all failed (a prepared.json's bot tests or CPU
# gate "ok": false, a gate failed only for a niced replay aside) while evaluate's own run of that check passed or hasn't
# run yet; else []. A check both failed (a candidate that really fails) counts neither way, and so does a run evaluated
# on another commit than it prepared (a rebase: E0266 prepared 104845e, evaluated d75fb2b) or prepared before the
# checkout layout (no prepared/bot). On 28 Sep every prepared bot-test run failed (E0263, E0265, E0266: a suite laid out
# flat, MISSES 84) and each reran under the lock, unalerted: prepared_stalled counts failed bot tests as delivered. Not
# a count claim needing a chance: evaluate's own bot tests passed in all 45 recorded experiments with a prepared.json
# then, so the same check failing three times in a row on the same commits is a broken step, not luck.
def prepared_failing(state: Path) -> list[str]:
    rows = []
    for path in (state / "runs").glob("*/prepared.json"):
        if not (path.parent / "prepared" / "bot").is_dir():
            continue
        record, own = read_json(path), read_json(path.parent / "experiment.json")
        if own.get("candidate", record.get("commit")) != record.get("commit"):
            continue
        failed = passed = False
        for key in ("bot_tests", "cpu"):
            check = record.get(key)
            if not isinstance(check, dict) or check.get("replayed"):
                continue
            if check.get("ok") is True:
                passed = True
            elif (own.get(key) or {}).get("ok") is not False:
                failed = True
        if failed or passed:
            at = record.get("at")
            rows.append((at if isinstance(at, (int, float)) else 0, path.parent.name, failed))
    last = sorted(rows)[-PREPARED_FAILS:]
    return [name for _, name, _ in last] if len(last) == PREPARED_FAILS and all(row[2] for row in last) else []


CRASH_REPORTS = Path.home() / "Library/Logs/DiagnosticReports"  # macOS's crash reports (.ips) of this user's processes
CRASH_FIRST_S = 2 * 86400  # a first check reads the reports this far back


# The loop's bot builds here by build UUID (macho_uuid): each candidate's (runs/E####/cand's, which the referee built)
# and the champion's, as {uuid: "E0287" or "the champion"}.
def loop_builds(state: Path) -> dict[str, str]:
    found = {}
    for build in [*sorted((state / "runs").glob("E*/cand/.unswbc-build/bot")),
                  state / "champion/bot/.unswbc-build/bot"]:
        if uid := macho_uuid(build):
            found[uid] = "the champion" if build.parts[-4] == "champion" else build.parts[-4]
    return found


# The LC_UUID of the 64-bit Mach-O executable at `path` (what a crash report's slice_uuid names), or None for any
# other file or one that can't be read.
def macho_uuid(path: Path) -> str | None:
    import struct
    import uuid
    try:
        with path.open("rb") as handle:
            head = handle.read(1 << 16)
        if len(head) < 32 or struct.unpack_from("<I", head)[0] != 0xFEEDFACF:
            return None
        at = 32
        for _ in range(struct.unpack_from("<I", head, 16)[0]):
            command, size = struct.unpack_from("<II", head, at)
            if command == 0x1B:  # LC_UUID
                return str(uuid.UUID(bytes=head[at + 8:at + 24]))
            at += size
    except (OSError, struct.error, ValueError):
        return None
    return None


# MISSES 86: a candidate that wrote out of bounds crashed mid-turn in play; the toolkit reran each crashed turn in a
# fresh process (the dragon's memory lost, a normal move in the replay), so two SPRTs took its games as valid, while
# macOS saved a crash report of each. This counts the reports of bot processes (home.BOT_NAME) written here since the
# last check (`seen`, health.json's "bot crashes"), and those the hourly upkeep found on the Mac hosts since then
# (maintenance.host_crashes' rows in its HOST_CRASHES file: they play this Mac's builds), by build UUID, and alerts
# for each build that is a candidate's or the champion's (loop_builds), naming where. A few hundred bytes read a
# report, and 64 KB a build.
def bot_crashes(state: Path, now: float, seen: dict) -> list[Alert]:
    since = seen.get("at", now - CRASH_FIRST_S)
    found = []
    for path in CRASH_REPORTS.glob(f"{home.BOT_NAME}-*.ips"):
        try:
            if path.stat().st_mtime <= since:
                continue
            with path.open() as handle:
                header = json.loads(handle.readline())
        except (OSError, ValueError):
            continue
        if isinstance(header, dict) and header.get("app_name") == home.BOT_NAME and isinstance(
                header.get("slice_uuid"), str):
            found.append((header["slice_uuid"].lower(), "here"))
    hosts = read_json(state / maintenance.HOST_CRASHES).get("reports")
    found += [(row["uuid"].lower(), str(row.get("host"))) for row in hosts if isinstance(row, dict)
              and isinstance(row.get("uuid"), str) and isinstance(row.get("found"), (int, float))
              and row["found"] > since] if isinstance(hosts, list) else []
    seen["at"] = now
    builds = loop_builds(state) if found else {}
    alerts = []
    for uid in sorted({uid for uid, _ in found} & set(builds)):
        places = Counter(where for u, where in found if u == uid)
        at = ", ".join(f"{n} {'here' if where == 'here' else f'on {where}'}" for where, n in sorted(places.items()))
        alerts.append(Alert(f"bot-crash:{uid}", f"{sum(places.values())} crash report(s) of {builds[uid]}'s bot build "
                            f"(UUID {uid}) since {when(since, now)} ({at}; ~/Library/Logs/DiagnosticReports): the "
                            f"toolkit reruns a crashed turn in a fresh process, so its games look normal but the dragon "
                            f"lost its memory (MISSES 86); read the reports' exception and backtrace and judge whether "
                            f"its games stand"))
    return alerts


# driver.prepare's files: a run folder's prepared.json modified in the last 2 days that can't be read (evaluate then
# prepares nothing from it, silently), prepare delivering nothing or an evaluation left unprepared though it waited
# behind another (prepared:stalled, prepared_stalled, MISSES 76, method M89) or
# failing every time (prepared:failing, prepared_failing, MISSES 84), a screened promotion whose shared set wasn't made
# during its screen (prepared:set-ahead, set_ahead), and a refs/prepared/<id> (or prepare_ahead's
# refs/prepared-next/<id>) in the main repo whose
# experiment was recorded (or left pending, neither queued nor evaluated) over PREPARED_REF_AGE ago (its cleanup,
# driver.cleanup, deletes it; one left keeps the candidate's objects). One prepared.json a run folder, of a few
# hundred bytes, and a ref an experiment in flight.
def prepared(state: Path, repo: Path, now: float) -> list[Alert]:
    alerts, broken = [], []
    for path in sorted((state / "runs").glob("*/prepared.json")):
        try:
            if now - path.stat().st_mtime < 2 * 24 * 3600 and not isinstance(json.loads(path.read_text()), dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as error:
            broken.append(f"{path} ({error})")
    if broken:
        alerts.append(Alert("prepared:broken", f"{len(broken)} prepared.json files can't be read, so their "
                            f"evaluations reuse nothing prepared: {'; '.join(broken[:3])}"))
    promoted = champion_times(repo) if (repo / ".git").exists() else None
    if missed := set_ahead(state, now, promoted):
        alerts.append(Alert(f"prepared:set-ahead:{max(promoted, key=promoted.get)[:12]}", missed))
    if stalled := prepared_stalled(state, now, promoted):
        alerts.append(Alert("prepared:stalled", f"prepare had time for {len(stalled)} candidates recorded in the last "
                            f"{PREPARED_WINDOW // 3600} h and they ran their CPU gate under the lock with the hosts "
                            f"waiting ({', '.join(stalled[:5])}): see their runs' prepared.json and the --once logs' "
                            f"'prepar' lines"))
    if failing := prepared_failing(state):
        alerts.append(Alert("prepared:failing", f"the last {len(failing)} prepared checks failed where evaluate's own "
                            f"didn't ({', '.join(failing)}), so each reran a check under the lock: read their runs' "
                            f"prepared/bot-tests.log and prepared.json"))
    if not (repo / ".git").exists():
        return alerts
    left = []
    names = sorted(set(git(repo, "for-each-ref", "--format=%(refname:strip=2)", "refs/prepared/",
                           "refs/prepared-next/").splitlines()))
    # One still waiting for the evaluation lock or under evaluation is in flight however long ago experiment.json was
    # written ("pending" until its result: E0285 at 02:23 and E0294 at 05:25 on 2026-09-29 alerted while queued).
    evaluating = (throughput.evaluation_now(state, now) or {}).get("experiment") if names else None
    flying = set(throughput.waiting_now(state, now, evaluating)) | {evaluating} if names else set()
    if names:  # and any with a live queue ticket, however old its worker.json (waiting_now drops it after 12 h: E0398)
        import evalline
        flying |= set(evalline.live_tickets(state))
    for name in names:
        record = read_json(state / "runs" / name / "experiment.json")
        # "conflict": its merge turn is under way
        recorded = record.get("verdict") not in (None, "", "conflict") and name not in flying
        finished = (state / "runs" / name / "experiment.json").stat().st_mtime if recorded else None
        if finished is not None and now - finished > PREPARED_REF_AGE:
            left.append(name)
    if left:
        alerts.append(Alert("prepared:refs", f"{len(left)} refs/prepared/<id> or refs/prepared-next/<id> outlived "
                            f"their recorded experiments (driver.cleanup deletes each): {', '.join(left[:5])}; delete "
                            f"them with git update-ref -d"))
    return alerts


# The evaluation queue (driver.enter_queue's tickets in <loop home>/evaluation-queue): a candidate still waiting
# (meta.ticket_live) while no evaluation is live (evaluation_live: the lock's holder, or an SPRT playing) for over
# QUEUE_STALL with the same head throughout (`seen` keeps when each head was first found so), naming the head's
# experiment and pid and whether ticket_live counts it: the others wait behind a live-looking head for good. Tickets whose processes are all gone
# hold nobody up (the next to enter removes them). A head takes a free lock within driver.QUEUE_POLL_S (5 s), unless
# it is making a shared map set or map mix in driver.prepare, which finishes its step first: 16-75 s measured (E0180,
# 2026-09-27), with no measurement of a new champion's full shared set recorded, so QUEUE_STALL is eight times the
# longest measured. A merge turn prepare started beside the evaluation also runs to its end once the lock frees (76
# turns in the live runs to 29 Sep: median 52 s, longest 338 s, none resumed; a resumed one is two turns), so while
# the first live waiter's merge turn runs (merge_turn_running) its stall doesn't count: it counts from the turn's end.
# Checked every minute (QUICK), since the whole pipeline stops behind it.
QUEUE_STALL = 10 * 60


# True while experiment `exp_id`'s merge turn runs (driver.run_resolve): its Claude coder session being resumed (a
# process with `--resume <session_id>`, driver.resume_coder; once its coding finished and before its result, that is
# its merge turn), or the fresh agent that takes the turn once the coder's cache is cold (driver.fresh_merge_turn,
# named bc-<id>-merge-<session> by driver.merge_agent, on its launch and on a resume of it that ended waiting).
def merge_turn_running(state: Path, exp_id: str | None) -> bool:
    if not exp_id:
        return False
    session = (read_json(state / "runs" / str(exp_id) / "experiment.json").get("session") or {}).get("session_id")
    return any((session and f"--resume {session}" in row.args) or f"--name bc-{exp_id}-merge-" in row.args
               for row in ps_rows())


# (whether an evaluation is live, the experiments whose SPRT is playing): the evaluation marker names a live holder
# (home.evaluation_running), or an sprt-progress.json changed within RECENT_SPRT, whose experiment is then under
# evaluation whatever the marker says. The one test evaluation_queue and ticketless share: both called E0429 idle
# while its SPRT played (21:17-21:27 on 30 Sep, the queue "stalled"; 21:20, "no ticket"), and E0414 and E0426 before
# it (MISSES 108).
def evaluation_live(state: Path, now: float) -> tuple[bool, set[str]]:
    playing = {path.parent.name for path in state.glob("runs/E*/sprt-progress.json")
               if now - (throughput.mtime(path) or 0) < RECENT_SPRT}
    return bool(playing) or home.evaluation_running(now), playing


def evaluation_queue(state: Path, now: float, seen: dict) -> list[Alert]:
    tickets = [] if evaluation_live(state, now)[0] else sorted((state / meta.QUEUE).glob("*.json"))
    rows = {path: read_json(path) for path in tickets}
    waiting = [path for path in tickets if meta.ticket_live(state, rows[path])]
    key = f"queue:{tickets[0].name}" if waiting else "queue:"
    for old in [k for k in seen if k != key]:
        seen.pop(old)
    since = held_since(seen, key, bool(waiting), now)
    if not waiting or now - since <= QUEUE_STALL:
        return []
    if merge_turn_running(state, rows[waiting[0]].get("experiment")):
        seen[key] = now  # its stall counts from the turn's end
        return []
    head, row = tickets[0], rows[tickets[0]]
    state_of = "live by meta.ticket_live, so its process holds the queue: see why it doesn't take the lock" \
        if head == waiting[0] else f"dead by meta.ticket_live, yet the live waiter {waiting[0].name} hasn't removed it"
    return [Alert(key, flat(
        f"EVALUATION QUEUE STALLED: {len(waiting)} candidates wait with no evaluation holding the lock since at least "
        f"{when(since, now)}; the head is {head} ({row.get('experiment')}, pid {row.get('pid')}, started "
        f"{row.get('started')}), {state_of} (driver.first_in_queue)"))]


# The coded backlog behind a live evaluation (REFLECT 2026-10-01 03:17 F6: sig found "only 1 completed item in the
# queue" himself, nothing measured it): the queue's live tickets hold under one evaluation's length (the recorded
# median, recorded_evaluation), that is none, while coders work (throughput.coding_now), for over BACKLOG_AFTER, so the
# evaluation idles once this one ends until a coder finishes. Quiet under a stop marker or the usage pause. Every
# minute (QUICK); evalline's queue text shows "queued ahead: N (~M min)".
BACKLOG_AFTER = 600


def eval_backlog(state: Path, now: float, seen: dict) -> list[Alert]:
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists() or quota.paused(state, now)
    coders = [] if stopped or not evaluation_live(state, now)[0] else throughput.coding_now(state)
    queued = [path for path in (state / meta.QUEUE).glob("*.json") if meta.ticket_live(state, read_json(path))]
    each = recorded_evaluation(state) if coders else None
    short = bool(coders and each and not queued)
    since = held_since(seen, "since", short, now)
    if not short or now - since < BACKLOG_AFTER:
        return []
    return [Alert(f"eval backlog:{int(since)}", flat(
        f"EVAL BACKLOG: {len(queued)} coded candidates queued behind the live evaluation since "
        f"{time.strftime('%H:%M', time.localtime(since))}, under one evaluation's length (~{each / 60:.0f} min, the "
        f"median of the last 10: health.recorded_evaluation), while {len(coders)} code ({', '.join(coders)}): the "
        "evaluation idles after this one until a coder finishes"))]


# A candidate past coding (worker.json "finished", its driver alive, no result recorded) that neither holds the
# evaluation lock (the live marker's pid and lane) nor has a ticket in the queue (driver.enter_queue) for over
# TICKETLESS_S: nobody takes the lock after it, so it would wait for good, and the queue's order and health's stall
# check can't see it. Named with what it was doing (its worker status, merge state, a merge turn running, a prepare
# saved). On 29 Sep E0349 was reported without a ticket 18:42-18:52; utilisation.jsonl shows it held the lock then
# (setup to 18:51, then its merge turn), so the holder is left out here, as is one whose SPRT is playing
# (evaluation_live).
TICKETLESS_S = 180


def ticketless(state: Path, now: float, seen: dict | None = None) -> list[Alert]:
    marker, playing = home.live_marker(now) or {}, evaluation_live(state, now)[1]
    tickets = {path.stem.rsplit("-", 1)[-1] for path in (state / meta.QUEUE).glob("*.json")}
    alerts, flagged = [], set()
    for path in sorted((state / "runs").glob("E*/worker.json")):
        exp_id = path.parent.name
        try:
            quiet = now - path.stat().st_mtime
        except OSError:
            continue
        worker = read_json(path)
        pid = worker.get("driver_pid")
        if (worker.get("status") != "finished" or quiet <= TICKETLESS_S or exp_id in tickets
                or not isinstance(pid, int) or not meta.alive(pid)):
            continue
        experiment = read_json(path.with_name("experiment.json"))
        if (not experiment or experiment.get("finished") or exp_id in playing
                or marker.get("pid") == pid and marker.get("lane") in (None, experiment.get("lane"))):
            continue
        session = experiment.get("session") or {}
        doing = [f"worker {worker.get('status')} {when(now - quiet, now)}",
                 f"merge {session.get('merge')}" if session.get("merge") else "no merge recorded",
                 "its merge turn running" if merge_turn_running(state, exp_id) else "no merge turn running",
                 "a prepare saved" if path.with_name("prepared.json").exists() else "nothing prepared"]
        flagged.add(exp_id)
        if seen is not None and now - seen.setdefault(exp_id, now) < TICKETLESS_S:  # seen so for a while, not
            continue  # the seconds between a released lock and experiment.json's saved finish
        alerts.append(Alert(f"ticketless:{exp_id}", f"{exp_id} (driver pid {pid}) waits past coding with no "
                            f"evaluation-queue ticket and doesn't hold the lock ({'; '.join(doing)}): nothing puts "
                            f"it back in line, so find why its ticket went (driver.first_in_queue, enter_queue); "
                            f"see {path}"))
    for gone in [k for k in seen or {} if k not in flagged]:
        seen.pop(gone)
    return alerts


# Each merge turn a Claude coder's candidate got (driver.run_resolve) leaves runs/E####/resolve.json, a fresh agent's
# turn also its own resolve-<session>.json (read by the post-mortem, driver.merge_note), and a row in the session's
# merge_turns with its cost (driver.record_merge_turn), which the review reads to judge warm against fresh turns
# (driver.MERGE_WARM_S). Alerts on a turn in the last MERGE_TURN_AGE with any of those missing: resolve.json with no row
# ended within MERGE_TURN_SLACK of it, once experiment.json was saved after it (evaluate saves it after the turn), a
# row whose file is gone, and a fresh row with no cost. Only experiments started since MERGE_TURNS_SINCE (a2cc9314,
# merge_turns' merge), as a driver started earlier runs the old code for its life (home.pin_code); a tune's has no
# coder session and no turn.
MERGE_TURNS_SINCE = datetime.fromisoformat("2026-09-29T12:24:14+10:00").timestamp()
MERGE_TURN_AGE = 6 * 3600
MERGE_TURN_SLACK = 120


def merge_turn_files(state: Path, now: float) -> list[Alert]:
    alerts = []
    for path in sorted((state / "runs").glob("E*/experiment.json")):
        run, resolve = path.parent, path.parent / "resolve.json"
        if now - path.stat().st_mtime > MERGE_TURN_AGE:
            continue
        exp = read_json(path)
        session = exp.get("session") or {}
        if not isinstance(exp.get("started"), (int, float)) or exp["started"] < MERGE_TURNS_SINCE \
                or not session.get("session_id"):
            continue
        # A re-evaluation carries its errored run's turns (driver.reuse_clone): their files are in that run's folder.
        turns = [turn for turn in session.get("merge_turns") or [] if isinstance(turn, dict)
                 and turn.get("run", run.name) == run.name]
        problems = []
        if resolve.exists() and now - (left := resolve.stat().st_mtime) < MERGE_TURN_AGE \
                and path.stat().st_mtime > left and not any(
                    isinstance(turn.get("ended"), (int, float)) and abs(turn["ended"] - left) <= MERGE_TURN_SLACK
                    for turn in turns):
            problems.append(f"resolve.json from {when(left, now)} has no merge_turns row")
        for turn in turns:
            ended = turn.get("ended")
            if not isinstance(ended, (int, float)) or now - ended > MERGE_TURN_AGE:
                continue
            name = f"the {turn.get('path')} turn ended {when(ended, now)}"
            if not resolve.exists():
                problems.append(f"{name} left no resolve.json")
            if turn.get("path") == "fresh":
                if not turn.get("saved") or not (run / str(turn["saved"])).exists():
                    problems.append(f"{name} left no resolve-<session>.json ({turn.get('saved') or 'none named'})")
                if not isinstance(turn.get("cost_usd"), (int, float)):
                    problems.append(f"{name} has no cost_usd")
        if problems:
            alerts.append(Alert(f"merge turn:{run.name}:{len(turns)}", f"{run.name}'s merge turns aren't all "
                                f"recorded: {'; '.join(problems)} (driver.run_resolve, record_merge_turn), so the "
                                f"post-mortem and the review of warm against fresh turns miss it; see {run}"))
    return alerts


# remote-status.json's "HH:MM:SS" (local time, no date) as the latest such time at or before `updated`.
def clock_time(text: str, updated: float) -> float:
    base = datetime.fromtimestamp(updated)
    hour, minute, second = map(int, text.split(":"))
    at = base.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return (at - timedelta(days=1) if at > base else at).timestamp()


# A method.md finding (a "## M<n>" section) is a blind spot in the tests and so work to do, not a report
# (MISSES 13): one still open for METHOD_GRACE alerts. A section is closed when its heading says fixed or done and
# nothing in it is still open ("Change:" or "Still open"). `seen` keeps when each finding, by its number, was first
# seen open, so rewording it doesn't restart the clock; one new to it counts from the file's last change, since it
# was there by then.
def method(state: Path, now: float, seen: dict) -> list[Alert]:
    path = state / "method.md"
    if not path.exists():
        seen.clear()
        return []
    changed = path.stat().st_mtime
    current, alerts = {}, []
    for section in re.split(r"(?m)^(?=## )", path.read_text()):
        heading = section.splitlines()[0] if section.strip() else ""
        number = re.match(r"## (M\d+)", heading)
        closed = (re.search(r"\b(fixed|done)\b", heading, re.I)
                  and not re.search(r"Change:|Still open", section, re.I))
        if not number or closed:
            continue
        since = current[number.group(1)] = seen.get(number.group(1), changed)
        if now - since > METHOD_GRACE:
            alerts.append(Alert(f"method:{number.group(1)}", f"method.md finding '{heading[3:120]}' has been open "
                                f"since at least {when(since, now)}; do it or say why not; see {path}"))
    seen.clear()
    seen.update(current)
    # A review whose notes (or playbook) exceed the caps it must keep is rejected whole, so say so before one runs.
    for name, cap in (("method.md", MAX_METHOD_LINES), ("playbook.md", MAX_PLAYBOOK_LINES)):
        notes = state / name
        if notes.exists() and (lines := len(notes.read_text().splitlines())) > cap:
            alerts.append(Alert(f"method:size:{name}", f"{name} has {lines} lines, over the {cap} a review must keep; "
                                f"the next review will be rejected until it is trimmed; see {notes}"))
    return alerts


# A Discord read that found new messages is digested by the digest worker (loop/digest.py) and the orchestrator's
# decisions appended to discord/digests.md (CLAUDE.md), which reviews read: a read with no decisions after it is news
# nobody looked at. Quiet while the DISCORD_PAUSED marker exists (no reads, so nothing to digest), and while the
# worker is still on it (digest_worker alerts when that takes too long).
def discord_digest(state: Path, now: float) -> list[Alert]:
    if (state / maintenance.DISCORD_PAUSED).exists():
        return []
    read = read_json(state / "discord.json").get("new_at")
    digests = state / "discord" / "digests.md"
    digested = digests.stat().st_mtime if digests.exists() else 0
    if not isinstance(read, (int, float)) or digested >= read or now - read <= DIGEST_GRACE:
        return []
    worker = read_json(state / "discord-digest.json")
    reported = isinstance(worker.get("digested_at"), (int, float)) and worker["digested_at"] >= read
    if worker.get("pending") and not reported:
        return []
    report = Path(worker["report"]) if reported and worker.get("report") else None
    todo = (f"check the digest worker's report {report}, decide, and append the decisions" if report and report.exists()
            else f"digest {state / 'discord' / 'latest.md'} with loop/DISCORD.md, save the report as "
            f"{digest.report_path(state, read)} and append the report and decisions")
    return [Alert("discord:digest", f"the Discord read at {when(read, now)} found new messages but no decisions have "
                  f"been saved since; {todo} to {digests}")]


# A report the orchestrator judges numbers its items (D1 for Discord digests, A1 audits, F1 REFLECT lists, G1 map
# ideas, S1 saved subagent reports; the ideas and method notes the loop applied itself by their own ids;
# loop/decisions.py), and each id needs a decision in its kind's decisions file within DECIDE_GRACE
# of the report appearing: one with none, without a verdict, handed to a later step ("left to the audit") or declined
# as unproven with no idea queued alerts (MISSES 52: two sonar items of a Discord read got none). Every numbered report
# is checked, so an old one left undecided keeps alerting; so is a digest or audit written since numbering began
# (decisions.NUMBERED_SINCE) with no ids. A report's time is the earlier of its modification and inode change times,
# so a worker dating its file ahead can't keep it inside the grace.
# Every result not kept gets the orchestrator's own rule check (sig, 2026-09-28: "you should be reviewing every
# rejection cos u have more brains that the agent just following the rules. You should use ur judgement to know when
# to break and change the rules"): a "Rule check:" line in a decisions.md block naming the experiment, saying whether
# the test measured what the idea needed (maps and opponents it fires on, the right kind: gain, no loss, targeted),
# whether the evidence is against the idea or only missing, and what the orchestrator changed or retests. Results
# recorded before RULE_CHECK_SINCE are left alone. One its independent judge judged (LOOP.md: Judged verdicts) still
# gets one, which may cite that verdict. Rejections that ran no test (an error) still get one: a broken run can hide
# a good idea too.
RULE_CHECK_SINCE = datetime.fromisoformat("2026-09-28T03:50:00+10:00").timestamp()
RULE_CHECK = re.compile(r"^\s*-\s*Rule check:", re.M)


def unchecked_rejections(state: Path, now: float) -> list[Alert]:
    results, record = state / "results.jsonl", state / "decisions.md"
    if not results.exists():
        return []
    text = record.read_text(errors="replace") if record.exists() else ""
    missing = []
    rows = []
    for line in results.read_text(errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    for row in meta.latest_rows(rows):  # a result re-judged kept (driver.rejudge) needs no rule check
        finished = row.get("finished") or 0
        if (row.get("verdict") in (None, "kept", "pending") or finished < RULE_CHECK_SINCE
                or now - finished <= DECIDE_GRACE):
            continue
        if not any(RULE_CHECK.search(block) for block in decisions.blocks(text, row["id"])):
            missing.append(row["id"])
    return [Alert("rule-check", flat(
        f"{len(missing)} results not kept have no rule check in {record}: {', '.join(missing[:10])}; add '- Rule "
        "check: ...' under a '## Orchestrator's decisions on E#### ...' block: did the test measure what the idea "
        "needed (maps, opponents, kind), is the evidence against it or only missing, and what rule or retest "
        "changes (sig 2026-09-28)"))] if missing else []


# The builder list, <loop home>/owed.md: every item there is non-bot work (its header: "Owed loop builds"), and each one
# added from OWED_STARTS_SINCE on carries when it starts, `Starts: now (<agent>)` or `Waits on <id/file>: <why it can't
# start now>` (OWED_STARTS), so nothing waits on a queue when a subagent could start it (REFLECT 2026-09-29 14:29 F6,
# MISSES 11: sig asked at 12:54 and 13:05 why loop items sat on this list instead of going to a subagent at once). An
# item is a line opening "- " with its date first, "(29 Sep 15:10)", "(2026-09-29 15:10)" or "2026-09-29 15:10"
# (OWED_ITEM); one marked DONE after it needs neither line, nor does a dated note that is no work item: a decision,
# an answer or a correction ("Decided: ...", "Answered the second half ...", "Correction to the 08:44 line: ...", owed.md
# 29 Sep 02:23, 10:39, 09:00). Older items, and undated lines, are left alone.
OWED_STARTS_SINCE = datetime.fromisoformat("2026-09-29T15:00:00+10:00")
OWED_ITEM = re.compile(r"^- \(?(?:(\d{4}-\d\d-\d\d)|(\d{1,2} [A-Z][a-z]{2})) (\d{1,2}:\d\d)\)?\s*(.*)")
OWED_CLOSED = r"^\W*(?:DONE|Decided|Answered|Correction to)\b"
OWED_STARTS = re.compile(rf"\bStarts: now \([^)]+\)|\bWaits on [^:]+: \S|{OWED_CLOSED}")


# An owed.md item waiting on branches main has since merged ("Waits on orch/method-88: ...", "after orch/method-88
# merges", "once orch/worldgen and orch/fit-tool merge") has lost its reason to wait: it starts now as a subagent, or
# its line says what else it waits on (REFLECT 2026-09-29 20:42 F2, F5: nine items still waited on orch/method-88 hours
# after its merge, 3c923b6c). Only a wait counts, not a branch named in passing ("LOOP.md is at 119,763 of 120,000
# bytes after orch/worldgen: ..."), and only once every branch the line waits on has merged. Undated items count
# too; one closed as OWED_STARTS closes it (DONE, Decided, Answered, Correction to) doesn't. `merged` is the branch
# names in main's merge commits (merged_branches).
OWED_BRANCH = r"orch/[\w.-]*\w"
OWED_WAIT = re.compile(rf"\b(?:[Aa]fter|[Oo]nce) (?:{OWED_BRANCH}(?:,? and |, ))*{OWED_BRANCH} merges?\b|"
                       rf"\b[Ww]aits on {OWED_BRANCH}")


# The orch/ branches named by main's first-parent merge commits of the last two weeks ("Merge orch/x: ...").
def merged_branches(repo: Path) -> set[str]:
    try:
        subjects = git(repo, "log", "main", "--first-parent", "--merges", "--since=14.days", "--format=%s")
    except (subprocess.SubprocessError, OSError):
        return set()
    return {found[1] for line in subjects.splitlines() if (found := re.match(r"Merge (orch/[\w.-]+?):?(?:\s|$)", line))}


def owed_cleared(state: Path, merged: set[str]) -> list[Alert]:
    path = state / "owed.md"
    if not path.exists():
        return []
    cleared = []
    for line in path.read_text(errors="replace").splitlines():
        text = found[4] if (found := OWED_ITEM.match(line)) else line[2:]
        if not line.startswith("- ") or re.match(OWED_CLOSED, line[2:]) or re.match(OWED_CLOSED, text):
            continue
        waits = {branch for wait in OWED_WAIT.finditer(line) for branch in re.findall(OWED_BRANCH, wait[0])}
        if waits and waits <= merged:
            cleared.append(f"{line[2:70]} ({', '.join(sorted(waits))} merged)")
    return [Alert("owed cleared", flat(
        f"{len(cleared)} items in {path} wait on a branch main has merged: {'; '.join(cleared[:5])}. Start each now "
        "as a subagent, or rewrite its line naming what it still waits on (REFLECT 2026-09-29 20:42 F2)"))] \
        if cleared else []


def owed_starts(state: Path, now: float) -> list[Alert]:
    path = state / "owed.md"
    if not path.exists():
        return []
    year = datetime.fromtimestamp(now).year
    missing = []
    for line in path.read_text(errors="replace").splitlines():
        if not (found := OWED_ITEM.match(line)):
            continue
        iso, day_month, clock, text = found.groups()
        try:
            added = datetime.strptime(f"{iso} {clock}", "%Y-%m-%d %H:%M") if iso else \
                datetime.strptime(f"{day_month} {year} {clock}", "%d %b %Y %H:%M")
        except ValueError:
            continue
        if added.astimezone() >= OWED_STARTS_SINCE and not OWED_STARTS.search(text):
            missing.append(f"({found[2] or found[1]} {clock}) {text[:60]}")
    return [Alert("owed starts", flat(
        f"{len(missing)} items in {path} carry neither 'Starts: now (<agent>)' nor 'Waits on <id/file>: <why>': "
        f"{'; '.join(missing[:5])}. A non-bot item starts now as a subagent (file ownership stated) unless something "
        "it needs isn't there yet (REFLECT 2026-09-29 14:29 F6)"))] if missing else []


def undecided(state: Path, now: float) -> list[Alert]:
    alerts = []
    for report, record, name, missing in decisions.pending(state):
        info = report.lstat()
        at = min(info.st_mtime, info.st_ctime)
        if now - at <= DECIDE_GRACE:
            continue
        key = f"undecided:{report.relative_to(state).as_posix()}"
        if missing[0][0] == "-":
            alerts.append(Alert(key, flat(
                f"{report} (saved {when(at, now)}) has {missing[0][1]}, so none of its items can be decided: number "
                f"them by hand, decide each in {record}, and fix the prompt that wrote it (loop/decisions.py)")))
            continue
        listed = "; ".join(f"{i} {why}" for i, why in missing[:8]) + ("; ..." if len(missing) > 8 else "")
        alerts.append(Alert(key, flat(
            f"{len(missing)} items of {report} (saved {when(at, now)}) have no decision in "
            f"{record}: {listed}; add one per id under '## Orchestrator's decisions on {name} "
            "(<date hh:mm>)': acted: what, declined: why, or check started: which (loop/decisions.py)")))
    return alerts


# Proposals meta.report_refused saved (refused-ideas.jsonl in a run's, audit's or review's protected folder, at most
# meta.REFUSED_KEEP lines each) over DECIDE_GRACE ago, while some line is neither re-added (an idea in ideas.jsonl
# with its title) nor decided: a decisions.md block naming the file's path under the loop home ("## Orchestrator's
# decisions on runs/E0231/refused-ideas.jsonl (<date hh:mm>)", "- re-added as I720, its threshold stated over ...",
# "- declined: ..."). Their alert lines scroll away in host-alerts.log; the file keeps them.
def refused_proposals(state: Path, now: float) -> list[Alert]:
    files = [*state.glob(f"runs/*/{meta.REFUSED_FILE}"), *state.glob(f"worker-io/*/*/{meta.REFUSED_FILE}"),
             *state.glob(f"{rulecounts.root(state).name}/*/{meta.REFUSED_FILE}")]
    files = [path for path in files if now - path.stat().st_mtime > DECIDE_GRACE]
    if not files:
        return []
    record = state / "decisions.md"
    text = record.read_text(errors="replace") if record.exists() else ""
    titles = {flat(idea.get("title", "")) for idea in backlog(state)}
    alerts = []
    for path in sorted(files):
        name = path.relative_to(state).as_posix()
        if decisions.blocks(text, name):
            continue
        rows = [row for row in map(refused_row, path.read_text(errors="replace").splitlines())
                if not row["title"] or row["title"] not in titles]
        if rows:
            alerts.append(Alert(f"refused:{name}", flat(
                f"{len(rows)} refused proposals in {path} (saved {when(path.stat().st_mtime, now)}, from "
                f"{rows[0].get('source')}) are neither re-added nor decided; the first: "
                f"{str(rows[0].get('reason'))[:200]}; fix and re-add each with meta.append_ideas(state, "
                f"meta.valid_proposals([line], 1), source), or decide them under '## Orchestrator's decisions on "
                f"{name} (<date hh:mm>)' in {record}")))
    return alerts


# A refused-ideas.jsonl line as {"source", "reason", "title"}, the title "" where its proposal can't be read (cut at
# meta.REFUSED_TEXT, or not JSON).
def refused_row(line: str) -> dict:
    try:
        row = json.loads(line)
    except ValueError:
        row = {}
    row = row if isinstance(row, dict) else {}
    try:
        title = json.loads(row.get("line") or "").get("title") or ""
    except (ValueError, AttributeError):
        title = ""
    return {"source": row.get("source"), "reason": row.get("reason"), "title": flat(str(title))}


# The numbered prompts' first real firing (MISSES 52): while every Discord digest, audit, REFLECT list and applied/
# file written since decisions.NUMBERED_SINCE (and older than DECIDE_GRACE) parses to no ids, its prompt or
# decisions.applied isn't numbering them, and every later check of that kind would pass it silently. Quiet before
# the first one and once any carries ids.
def numbering(state: Path, now: float) -> list[Alert]:
    alerts = []
    for kind in decisions.KINDS:
        if kind.letter not in (*decisions.ALWAYS_ITEMS, "F", "IM"):
            continue
        since = []
        for report, content in decisions.reports(state, kind):
            info = report.lstat()
            if decisions.written(report) >= decisions.NUMBERED_SINCE and now - min(info.st_mtime, info.st_ctime) \
                    > DECIDE_GRACE:
                since.append((report, bool(decisions.items(content, kind.letter))))
        if since and not any(numbered for _, numbered in since):
            folder = kind.reports.split("/", 1)[0]
            alerts.append(Alert(f"numbering:{folder}", flat(
                f"none of the {len(since)} {folder} reports written since numbering began has a "
                f"{kind.letter[0]}1.-numbered item (the first: {since[0][0]}): the step writing them isn't numbering "
                "its items, so their decisions go unchecked; fix its prompt or decisions.applied (loop/decisions.py)")))
    return alerts


# The digest worker (loop/digest.py): a folder pending over WORKER_PENDING (its worker keeps failing or never exits),
# and a digest abandoned after two failed launches that nobody has digested by hand since (digests.md not written).
def digest_worker(state: Path, now: float) -> list[Alert]:
    path = state / "discord-digest.json"
    record, alerts = read_json(path), []
    prepared = record.get("prepared_at")
    if record.get("pending") and isinstance(prepared, (int, float)) and now - prepared > WORKER_PENDING:
        alerts.append(Alert(f"digest-pending:{Path(record['pending']).name}", f"the Discord digest worker has been "
                            f"pending since {when(prepared, now)} (pid {record.get('pid')}, failures "
                            f"{record.get('failures', [])}); see {path} and "
                            f"{state / 'worker-io/discord-digest' / Path(record['pending']).name / 'session.stderr'}"))
    abandoned = (record.get("abandoned") or [{}])[-1]
    digests = state / "discord" / "digests.md"
    handled = max(record.get("finished_at", 0), digests.stat().st_mtime if digests.exists() else 0)
    if isinstance(abandoned.get("at"), (int, float)) and abandoned["at"] > handled:
        alerts.append(Alert(f"digest-abandoned:{Path(abandoned['work']).name}", f"the Discord digest worker was "
                            f"abandoned {when(abandoned['at'], now)} after {len(abandoned.get('failures', []))} failed "
                            f"launches ({'; '.join(abandoned.get('failures', []))[-300:]}); digest "
                            f"{Path(abandoned['work']) / 'latest.md'} by hand with loop/DISCORD.md and see {path}"))
    return alerts


# The REFLECT worker (loop/reflect.py): a REFLECT that fired over WORKER_PENDING ago with neither a list nor the
# by-hand line since (reflect-list.json's covered_at is older), and a list over REFLECT_JUDGE_GRACE old with
# loop/MISSES.md not written since (each REFLECT check ends with a dated reflection line there, loop/REFLECT.md).
def reflect_worker(state: Path, repo: Path, now: float) -> list[Alert]:
    fired, path = reflect.read(state), state / "reflect-list.json"
    record, alerts = read_json(path), []
    at = fired.get("at")
    covered = record.get("covered_at")
    if isinstance(at, (int, float)) and (not isinstance(covered, (int, float)) or covered < at) \
            and now - at > WORKER_PENDING:
        alerts.append(Alert(f"reflect-list:{int(at)}", f"the REFLECT at {when(at, now)} has no list or by-hand line "
                            f"yet (pending {record.get('pending')}, failures {record.get('failures', [])}); see {path} "
                            "and the upkeep's 'REFLECT list' errors"))
    listed = Path(record["list"]) if record.get("list") else None
    misses = repo / "loop/MISSES.md"
    written = listed.stat().st_mtime if listed and listed.exists() else None
    if written and now - written > REFLECT_JUDGE_GRACE and (not misses.exists() or misses.stat().st_mtime < written):
        alerts.append(Alert(f"reflect-judged:{listed.name}", f"the REFLECT list {listed} ({when(written, now)}) has no "
                            f"reflection line after it in {misses}: judge each item (loop/REFLECT.md)"))
    return alerts


# The map-ideas worker (loop/mapideas.py): its last run failing for longer than MAPIDEAS_GRACE, a run still recorded
# as running mapideas.STALE after it started (hung past its timeout, or its pid reused; the next upkeep tick also
# records it as failed, but only while upkeep runs), and each finished branch that is still in the repo but not merged
# into main MAPIDEAS_MERGE_GRACE after it finished (the orchestrator forgot to review it; later runs wait for it), and
# each ladder map no run has studied or is studying MAPIDEAS_LESSON_GRACE after its file was kept (once the tick has
# recorded which maps were studied, mapideas.seed_studied). Deleting a rejected branch clears its alert.
def map_ideas(state: Path, repo: Path, now: float) -> list[Alert]:
    path = state / "mapideas.json"
    record = read_json(path)
    alerts = []
    started = record.get("attempt_at")
    if record.get("pid") and isinstance(started, (int, float)) and now - started > mapideas.STALE:
        alerts.append(Alert("mapideas:stale", f"the map-ideas run started {when(started, now)} is still recorded as "
                            f"running (pid {record['pid']}), past its {mapideas.TIMEOUT / 3600:g} h limit; see "
                            f"{record.get('work')}/run.log and {path}"))
    failed, error = record.get("failed_at"), str(record.get("error") or "").strip()
    if error and isinstance(failed, (int, float)) and now - failed > MAPIDEAS_GRACE:
        alerts.append(Alert("mapideas:failed", f"the map-ideas run failed {when(failed, now)}: "
                            f"{error.splitlines()[-1][:200]}; see {record.get('work')}/run.log and {path}"))
    for entry in mapideas.unmerged(repo, record):
        at = entry.get("finished_at")
        if isinstance(at, (int, float)) and now - at > MAPIDEAS_MERGE_GRACE:
            alerts.append(Alert(f"mapideas:{entry['branch']}", f"map-ideas branch {entry['branch']}, finished "
                                f"{when(at, now)}, is not merged into main; review {entry.get('report')}, then merge "
                                f"or delete the branch"))
    if "ladder_studied" in record:
        # A failed run's pending maps are left to its own failure alert.
        for m in mapideas.new_ladder_maps(state, set(mapideas.studied(record))):
            kept = ladder_maps.map_file(state, m["content"]).stat().st_mtime
            if now - kept > MAPIDEAS_LESSON_GRACE:
                alerts.append(Alert(f"mapideas:lesson:{m['content'][:12]}", f"the ladder map {m.get('name')} (id "
                                    f"{m.get('id')}) was kept {when(kept, now)} and no map-ideas run has studied it; "
                                    f"see {path} (a waiting branch or map ideas off under native holds it)"))
    return alerts


BCEVAL_DAY = 24 * 3600
BCEVAL_FAILING = 5  # the live index's latest readings that, all errors, mean bceval has stopped reading our games


# bceval's sync state (tools/bceval_sync.py, <loop home>/bceval.json), quiet before its first use: a file not of its
# fixed shape (an approved commit, a held update's commit, files and start), an update held for review over a day,
# alerted again each day it stays held (the sync alerted it when it was found: its key changes daily), a fingerprint
# mismatch (nothing runs until --trust), daily too, a fetch failing for over a day (bceval stays at the approved commit
# meanwhile), the live version's latest BCEVAL_FAILING readings all errors, and the reading cache over its bound
# (bceval_read.CACHE_MAX, which read prunes to).
def bceval(state: Path, now: float) -> list[Alert]:
    import online
    bceval_read = online.bceval_module()
    sync = bceval_read.sync_module()
    path = sync.state_path(state)
    if not path.exists():
        return []
    record = sync.read_state(state)
    held = record.get("held")
    shape = (isinstance(record.get("approved"), str) and bool(sync.SHA.match(record["approved"]))
             and (held is None or isinstance(held, dict) and isinstance(held.get("sha"), str)
                  and isinstance(held.get("files"), list) and isinstance(held.get("since"), (int, float))))
    if not shape:
        return [Alert("bceval state", f"{path} isn't bceval's sync state (tools/bceval_sync.py: an approved 40-hex "
                      "commit, a held update with sha, files and since): bceval readings stop until it is fixed; "
                      "delete it to start again from bceval_sync.PINNED")]
    found = []
    if held and now - held["since"] >= BCEVAL_DAY:
        days = int((now - held["since"]) // BCEVAL_DAY)
        found.append(Alert(f"bceval held:{held['sha'][:12]}:{days}", flat(
            f"BCEVAL UPDATE HELD for {(now - held['since']) / 3600:.0f} h: {', '.join(held['files']) or 'no file'}; "
            f"review with {sync.review_command(sync.CLONE, record['approved'], held['sha'])} and approve with "
            f".venv/bin/python tools/bceval_sync.py --approve {held['sha'][:12]}, or record why not (bceval stays "
            f"at {record['approved'][:7]})")))
    if record.get("mismatch"):
        found.append(Alert(f"bceval tampered:{int(now // BCEVAL_DAY)}", f"BCEVAL TAMPERED: {sync.CLONE}'s .git/config "
                           f"or .git/info/, or {sync.VENV}'s site-packages, changed since trusted, so bceval reads "
                           "nothing: inspect them, then .venv/bin/python tools/bceval_sync.py --trust"))
    live = online.read(state / "online.json", {}).get("submission_id")
    index = online.read(state / "online-replays" / str(live) / "index.json", {}) if live else {}
    readings = [g["bceval"] for g in index.get("games", []) if isinstance(g, dict) and g.get("bceval")]
    if len(readings) >= BCEVAL_FAILING and all(r.get("error") for r in readings[-BCEVAL_FAILING:]):
        found.append(Alert(f"bceval failing:{live}", flat(
            f"bceval's latest {BCEVAL_FAILING} readings of submission {live}'s losses all failed (the last: "
            f"{readings[-1]['error'][:150]}): turning points have stopped; run .venv/bin/python tools/bceval_read.py "
            "on one replay to see why")))
    since = record.get("fetch_failing_since")
    if isinstance(since, (int, float)) and now - since >= BCEVAL_DAY:
        found.append(Alert("bceval fetch", f"bceval's git fetch has failed for {(now - since) / 3600:.0f} h "
                           f"({record.get('fetch_error')}); it stays at {record['approved'][:7]}, so GitHub "
                           f"updates aren't seen: check {sync.CLONE}'s remote"))
    cache = state / bceval_read.CACHE
    if cache.is_dir() and (count := sum(1 for _ in cache.glob("*.json"))) > bceval_read.CACHE_MAX * 1.1:
        found.append(Alert("bceval cache", f"{cache} holds {count} readings, over bceval_read.CACHE_MAX "
                           f"{bceval_read.CACHE_MAX}: its prune isn't running"))
    return found


# The riser study (loop/risers.py), from risers.json: its first firing (the step ran RISERS_FIRST ago, `first_seen`,
# with no check done), a check overdue (none for risers.CHECK_EVERY + RISERS_OVERDUE with no worker pending: its
# rating read keeps failing, or no Claude upkeep ticks it, as under a native session), a worker pending over
# RISERS_PENDING, a run abandoned after its failed launches since the last applied one, and any of risers.run_folders
# (riser-replays/, risers/, worker-io/risers/) holding more runs than it keeps (risers.prune isn't running). Quiet
# before the step first runs.
def riser_study(state: Path, now: float) -> list[Alert]:
    path = state / "risers.json"
    if not path.exists():
        return []
    record, alerts = read_json(path), []
    first, checked = record.get("first_seen"), record.get("checked_at")
    error = (str(record.get("error") or "").splitlines() or ["no error recorded"])[-1][:200]
    if not isinstance(checked, (int, float)):
        if isinstance(first, (int, float)) and now - first > RISERS_FIRST:
            alerts.append(Alert("risers:first", flat(f"the riser study's step first ran {when(first, now)} and no "
                                                     f"check has been done ({error}); see {path} and "
                                                     f"{state / 'risers-fetch.log'}")))
    elif now - checked > risers.CHECK_EVERY + RISERS_OVERDUE and not record.get("pending"):
        alerts.append(Alert("risers:overdue", flat(f"no riser study check since {when(checked, now)}, due every "
                                                   f"{risers.CHECK_EVERY / 3600:.0f} h "
                                                   f"({error}; it runs only in a Claude driver's upkeep, off under a "
                                                   f"native session); see {path} and {state / 'risers-fetch.log'}")))
    prepared = record.get("prepared_at")
    if record.get("pending") and isinstance(prepared, (int, float)) and now - prepared > RISERS_PENDING:
        name = Path(record["pending"]).name
        alerts.append(Alert(f"risers-pending:{name}", flat(
            f"the riser study worker has been pending since {when(prepared, now)} (pid {record.get('pid')}, failures "
            f"{record.get('failures', [])}); see {path} and {state / 'worker-io/risers' / name / 'session.stderr'}")))
    abandoned = (record.get("abandoned") or [{}])[-1]
    if isinstance(abandoned.get("at"), (int, float)) and abandoned["at"] > record.get("finished_at", 0):
        alerts.append(Alert(f"risers-abandoned:{Path(abandoned['work']).name}", flat(
            f"the riser study {abandoned['work']} was abandoned {when(abandoned['at'], now)} after "
            f"{len(abandoned.get('failures', []))} failed launches "
            f"({'; '.join(abandoned.get('failures', []))[-300:]}); the next due check starts a fresh one")))
    for folder, keep in risers.run_folders(state):
        runs = [p for p in folder.glob("*") if p.is_dir()]
        if len(runs) > keep:
            alerts.append(Alert(f"risers:{folder.relative_to(state)}", f"{folder} holds {len(runs)} runs, over "
                                f"the {keep} risers.prune keeps: it isn't keeping it bounded"))
    return alerts


# The daily backlog audit (loop/backlog.py): its first firing (the step first ran BACKLOG_FIRST ago with no run
# applied), no run applied for BACKLOG_STALE since (a refused edit list, failed or abandoned workers, or no Claude
# upkeep running), a worker pending over BACKLOG_PENDING, an abandoned run with none finished since, and the bounds of
# its folders (backlog.run_folders, backlog-reports/). Quiet before its step first runs.
def backlog_audit(state: Path, now: float) -> list[Alert]:
    path = state / "backlog.json"
    if not path.exists():
        return []
    record, alerts = read_json(path), []
    first, applied = record.get("first_seen"), record.get("applied_at")
    since, grace = (applied, BACKLOG_STALE) if isinstance(applied, (int, float)) else (first, BACKLOG_FIRST)
    if isinstance(since, (int, float)) and now - since > grace:
        alerts.append(Alert("backlog:stale", flat(
            f"no backlog audit applied since {when(since, now)}" + ("" if applied else " (its step's first run)")
            + f": last report {record.get('report')}, failures {record.get('failures', [])}; it runs only in a Claude "
            f"driver's upkeep (off under a native session); see {path} and the BACKLOG alert lines")))
    prepared = record.get("prepared_at")
    if record.get("pending") and isinstance(prepared, (int, float)) and now - prepared > BACKLOG_PENDING:
        name = Path(record["pending"]).name
        alerts.append(Alert(f"backlog-pending:{name}", flat(
            f"the backlog audit worker has been pending since {when(prepared, now)} (pid {record.get('pid')}, "
            f"failures {record.get('failures', [])}); see {path} and "
            f"{state / 'worker-io/backlog' / name / 'session.stderr'}")))
    abandoned = (record.get("abandoned") or [{}])[-1]
    if isinstance(abandoned.get("at"), (int, float)) and abandoned["at"] > record.get("finished_at", 0):
        alerts.append(Alert(f"backlog-abandoned:{Path(abandoned['work']).name}", flat(
            f"the backlog audit {abandoned['work']} was abandoned {when(abandoned['at'], now)} "
            f"({'; '.join(abandoned.get('failures', []))[-300:]}); the next daily run takes the same slice, or moves "
            "past it after a second failure (backlog.slice_failed)")))
    for folder in backlog_audits.run_folders(state):
        runs = [p for p in folder.glob("*") if p.is_dir()]
        if len(runs) > backlog_audits.KEEP_FOLDERS:
            alerts.append(Alert(f"backlog:{folder.relative_to(state)}", f"{folder} holds {len(runs)} runs, over the "
                                f"{backlog_audits.KEEP_FOLDERS} backlog.prune keeps: it isn't keeping it bounded"))
    size = sum(p.stat().st_size for p in (state / "backlog-reports").rglob("*") if p.is_file())
    if size > BACKLOG_REPORTS_BYTES:
        alerts.append(Alert("backlog:reports", f"{state / 'backlog-reports'} holds {size >> 20} MB, over the "
                            f"{BACKLOG_REPORTS_BYTES >> 20} MB its runs should reach by 18 Oct: find the run writing "
                            "too much (loop/backlog.py)"))
    return alerts


# The variant sweep (loop/sweep.py): SWEEP_FAILURES failed runs in a row; a run recorded as running with no variant
# finished for sweep.STALE (hung); and a champion promoted over SWEEP_GRACE ago with no sweep started, either because
# the upkeep's sweep step hasn't recorded this champion at all (the step isn't running or keeps raising) or because it
# recorded the machines idle since the promotion without a run starting. Never idle since: quiet, the queue kept them busy.
# Also games that can't be played (sweep_errors) and its plan (plan_file). Quiet without sweep.json: the step writes
# it at its first tick, before this check runs in the same tick.
def sweep(state: Path, repo: Path, now: float) -> list[Alert]:
    path = state / "sweep.json"
    if not path.exists():
        return []
    record = read_json(path)
    champion = git(repo, "rev-parse", CHAMPION_BRANCH)
    swept = record.get("champion") or ""
    log = state / "sweeps" / swept / "run.log"
    alerts = []
    failures, failed = record.get("failures") or 0, record.get("failed_at")
    if isinstance(failures, int) and failures >= SWEEP_FAILURES and isinstance(failed, (int, float)):
        error = str(record.get("error") or "").strip().splitlines()
        alerts.append(Alert("sweep:failed", f"the variant sweep of {swept[:7]} failed {failures} runs in a row, the "
                            f"latest {when(failed, now)}: {error[-1][:200] if error else 'no error recorded'}; see "
                            f"{log} and {path}"))
    alerts += sweep_errors(state / "sweeps" / swept) if swept else []
    progress = record.get("progress_at")
    if record.get("pid") and not record.get("paused") and isinstance(progress, (int, float)) \
            and now - progress > sweeps.STALE:  # a run waiting in place (sweep.wait_idle) isn't hung
        alerts.append(Alert("sweep:stale", f"the variant sweep of {swept[:7]} (pid {record['pid']}) has finished no "
                            f"variant since {when(progress, now)}; see {log} and {path}"))
    alerts += plan_file(state / "sweeps" / swept / "plan.json", sweeps.PLAN_BOUND, "sweep:plan") if swept else []
    promoted = moved_at(repo, CHAMPION_BRANCH)
    if now - promoted > SWEEP_GRACE and not (swept == champion and record.get("started_at")):
        idle = record.get("idle_at")
        if swept != champion:
            alerts.append(Alert("sweep:missing", f"champion {champion[:7]}, promoted {when(promoted, now)}, has no "
                                f"variant-sweep record: the upkeep's sweep step isn't running or keeps failing (look "
                                f"for 'variant sweep failed' in the upkeep log); see {path}"))
        elif isinstance(idle, (int, float)) and idle > promoted:
            alerts.append(Alert("sweep:unstarted", f"champion {champion[:7]}, promoted {when(promoted, now)}, has no "
                                f"variant sweep started although the machines were idle at {when(idle, now)}; see "
                                f"{path}"))
    return alerts


# The adversarial-map search (loop/advmaps.py), as the sweep: ADVMAPS_FAILURES failed runs in a row; a run recorded as
# running with no batch finished for advmaps.STALE (hung); a champion promoted over ADVMAPS_GRACE ago with no search
# started, because the upkeep's step hasn't recorded this champion (not running, or raising) or recorded the machines
# idle since without a run starting; more than ADVMAPS_ERROR_SHARE of its maps unplayable (a broken referee, host or
# map maker would finish it with nothing scored); and its plan (plan_file). Quiet without advmaps.json, which its step
# writes at its first tick.
def adversarial_maps(state: Path, repo: Path, now: float) -> list[Alert]:
    path = state / "advmaps.json"
    if not path.exists():
        return []
    record = read_json(path)
    champion = git(repo, "rev-parse", CHAMPION_BRANCH)
    searched = record.get("champion") or ""
    folder = advmaps.folder_of(state, searched) if searched else state / "advmaps"
    alerts = []
    failures, failed = record.get("failures") or 0, record.get("failed_at")
    if isinstance(failures, int) and failures >= ADVMAPS_FAILURES and isinstance(failed, (int, float)):
        error = str(record.get("error") or "").strip().splitlines()
        alerts.append(Alert("advmaps:failed", f"the adversarial-map search of {searched[:7]} failed {failures} runs in "
                            f"a row, the latest {when(failed, now)}: {error[-1][:200] if error else 'no error recorded'};"
                            f" see {folder / 'run.log'} and {path}"))
    progress = record.get("progress_at")
    if record.get("pid") and not record.get("paused") and isinstance(progress, (int, float)) \
            and now - progress > advmaps.STALE:  # a run waiting in place (sweep.wait_idle) isn't hung
        alerts.append(Alert("advmaps:stale", f"the adversarial-map search of {searched[:7]} (pid {record['pid']}) has "
                            f"finished no batch since {when(progress, now)}; see {folder / 'run.log'} and {path}"))
    alerts += plan_file(folder / "plan.json", advmaps.PLAN_BOUND, "advmaps:plan") if searched else []
    if searched:
        games = advmaps.by_map(advmaps.results(folder))
        errors = [name for name, rows in games.items() if any("winner" not in r for r in rows)]
        if len(games) >= SWEEP_ERROR_RUN and len(errors) > ADVMAPS_ERROR_SHARE * len(games):
            alerts.append(Alert("advmaps:errors", f"{len(errors)} of the adversarial-map search's {len(games)} maps in "
                                f"{searched[:7]} had a game that couldn't be played (the first: {errors[0]}); the "
                                f"referee, a host or the map maker may be broken; see {folder / 'results.jsonl'}"))
    promoted = moved_at(repo, CHAMPION_BRANCH)
    if now - promoted > ADVMAPS_GRACE and not (searched == champion and record.get("started_at")):
        idle = record.get("idle_at")
        if searched != champion:
            alerts.append(Alert("advmaps:missing", f"champion {champion[:7]}, promoted {when(promoted, now)}, has no "
                                f"adversarial-map search record: the upkeep's step isn't running or keeps failing (look "
                                f"for 'adversarial maps failed' in the upkeep log); see {path}"))
        elif isinstance(idle, (int, float)) and idle > promoted:
            alerts.append(Alert("advmaps:unstarted", f"champion {champion[:7]}, promoted {when(promoted, now)}, has no "
                                f"adversarial-map search started although the machines were idle at "
                                f"{when(idle, now)}; see {path}"))
    return alerts


# A filler's plan (the sweep's, the prewarm's, the adversarial-map search's), rewritten each run or batch for its
# `play` to read: an alert when it is over its `bound` in bytes or isn't a JSON object. Quiet when there is none.
def plan_file(path: Path, bound: int, key: str) -> list[Alert]:
    try:
        size = path.stat().st_size
        plan = json.loads(path.read_text()) if size <= bound else {}
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        return [Alert(key, f"{path} can't be read as JSON ({type(error).__name__}: {error}); its next run rewrites it, "
                           f"so one that stays means the writer is broken")]
    if size > bound:
        return [Alert(key, f"{path} is {size:,} bytes, over its {bound:,}-byte bound: the plan its writer makes grew")]
    if not isinstance(plan, dict):
        return [Alert(key, f"{path} holds a JSON {type(plan).__name__}, not the plan object its run reads")]
    return []


# The idle prewarm (loop/baselines.py): PREWARM_FAILURES failed runs in a row, a run recorded as running with no stage
# finished for baselines.STALE (hung), or its plan (plan_file). Quiet without baselines.json, which its step writes at
# its first tick.
def prewarm(state: Path, now: float) -> list[Alert]:
    path = state / "baselines.json"
    if not path.exists():
        return []
    record, alerts = read_json(path), []
    champion, log = (record.get("champion") or "")[:7], state / "baselines.log"
    failures, failed = record.get("failures") or 0, record.get("failed_at")
    if isinstance(failures, int) and failures >= PREWARM_FAILURES and isinstance(failed, (int, float)):
        error = str(record.get("error") or "").strip().splitlines()
        alerts.append(Alert("prewarm:failed", f"the baseline prewarm of {champion} failed {failures} runs in a row, the "
                            f"latest {when(failed, now)}: {error[-1][:200] if error else 'no error recorded'}; see {log} "
                            f"and {path}"))
    progress = record.get("progress_at")
    if record.get("pid") and not record.get("paused") and isinstance(progress, (int, float)) \
            and now - progress > prewarming.STALE:  # a run waiting in place (sweep.wait_idle) isn't hung
        alerts.append(Alert("prewarm:stale", f"the baseline prewarm of {champion} (pid {record['pid']}) has finished no "
                            f"stage since {when(progress, now)}; see {log} and {path}"))
    if record.get("champion") and record.get("epoch"):  # the set's folder, as driver.shared_set names it
        plan = state / "shared-maps" / meta.shared_set_name(record["champion"], record["epoch"],
                                                              record.get("maps")) / "prewarm-plan.json"
        alerts += plan_file(plan, prewarming.PLAN_BOUND, "prewarm:plan")
    return alerts


# Loop code older than the disk's at work (MISSES 100): (1) an evaluation locked within OLD_CODE_S that ran older code,
# its session's "code_current" false (driver.hand_off couldn't hand it over); (2) a shared map set of `champion` that
# today's code names for nothing: neither the prewarm's (today's shared mix, meta.claim_mix(None), or the mix
# baselines.json records it made its set of, as before a mix change) nor the set today's meta.shared_set_name gives the
# frozen policy of an experiment in flight or among its users (driver.set_mix; not imported). Two sets of one champion are no alert by themselves: a claim keeps the mix it froze, so one claimed before
# a mix change plays its own set (driver.EARLIER_MIXES), as E0352 and E0354 did beside v0070's new-mix set.
OLD_CODE_S = 86_400


def old_code(state: Path, champion: str, now: float) -> list[Alert]:
    alerts, rows, runs = [], {}, state / "runs"

    def fresh(path: Path) -> bool:  # saved within 2 OLD_CODE_S: every experiment in flight is (claimed, coded)
        try:
            return now - path.stat().st_mtime < 2 * OLD_CODE_S
        except OSError:
            return False

    # The fields read below, so no parsed experiment.json outlives its read (180 of them made 224 MB, 29 Sep).
    def slim(row: dict) -> dict:
        evaluation, kept = row.get("evaluation") or {}, {}
        if "maps" in evaluation:
            kept["maps"] = evaluation["maps"]
        if "epoch" in (toolchain := evaluation.get("toolchain") or {}):
            kept["toolchain"] = {"epoch": toolchain["epoch"]}
        return {"finished": row.get("finished"), "evaluation": kept}

    for path in sorted(p for p in runs.glob("E*/experiment.json") if fresh(p)):
        row = read_json(path)
        rows[path.parent.name], session = slim(row), row.get("session") or {}
        del row
        locked = session.get("locked_at")
        if session.get("code_current") is False and isinstance(locked, (int, float)) and now - locked < OLD_CODE_S:
            alerts.append(Alert(f"old-code:{path.parent.name}", f"{path.parent.name}'s evaluation, locked "
                                f"{when(locked, now)}, ran older loop code than the disk's: driver.hand_off didn't "
                                f"hand it over (its WARNING line in that driver's log says why); see {path}"))

    def set_name(evaluation: dict) -> str:
        mix = evaluation.get("maps")
        return meta.shared_set_name(champion, (evaluation.get("toolchain") or {}).get("epoch", "unknown"),
                                    meta.general_mix(mix) if meta.aimed(mix) else mix)

    prewarm = read_json(state / "baselines.json")
    for folder in sorted((state / "shared-maps").glob(f"{champion[:12]}-*") if champion else []):
        try:
            users = json.loads((folder / "users.json").read_text())
        except (OSError, ValueError):
            users = []  # none yet: a set made ahead of its first test (prepare, the prewarm)
        users = [u for u in users if isinstance(u, str) and re.fullmatch(r"E\d{4,}", u)] \
            if isinstance(users, list) else []
        using = [row for row in rows.values() if not row.get("finished")] \
            + [rows.get(eid) or slim(read_json(runs / eid / "experiment.json")) for eid in users]
        epochs = {prewarm.get("epoch") or "unknown", *((r.get("evaluation") or {}).get("toolchain", {}).get("epoch")
                                                        or "unknown" for r in using)}
        named = {set_name(r.get("evaluation") or {}) for r in using} \
            | {meta.shared_set_name(champion, epoch, meta.claim_mix(None)) for epoch in epochs}
        if prewarm.get("champion") == champion and prewarm.get("epoch") and prewarm.get("maps"):
            named.add(meta.shared_set_name(champion, prewarm["epoch"], prewarm["maps"]))
        if folder.is_dir() and folder.name not in named:
            alerts.append(Alert(f"old-code:{folder.name}", f"shared map set {folder.name} of champion {champion[:7]} "
                                "is one today's loop code makes for no experiment in flight or among its users, nor "
                                f"for the prewarm: a process on older code made it; see {folder}"))
    return alerts


# Official-map reports played after their result (loop/official.py): a recent result whose report is still missing and
# whose job made no progress for official.GRACE, or whose job failed its last allowed attempt (the upkeep won't retry
# it). The job gets only a filler share while evaluations run, so it may be slow without being stuck: progress is its
# games done (its host status file) or a new attempt, remembered in `seen` from one check to the next.
def official_reports(state: Path, now: float, seen: dict | None = None) -> list[Alert]:
    seen = {} if seen is None else seen
    alerts, waiting = [], official.waiting(state)
    for key in [k for k in seen if k.startswith("official:") and k[9:] not in {r["id"] for r in waiting}]:
        seen.pop(key)
    for row in waiting:
        run_dir = official.folder(state, row["id"])
        job = official.read_json(run_dir / "official-job.json")
        marker = [job.get("attempts", 0), read_json(run_dir / "remote-status-official.json").get("games")]
        # First seen: a job running now is timed from now (it may have been progressing), else from its result.
        start = now if official.alive(job.get("pid")) else (row.get("finished") or now)
        last = seen.setdefault(f"official:{row['id']}", [marker, start])
        if last[0] != marker:
            last[:] = [marker, now]
        spent = job.get("error") and job.get("attempts", 0) >= official.ATTEMPTS and not official.alive(job.get("pid"))
        if spent or now - last[1] > official.GRACE:
            detail = f"its job failed {job['attempts']}x: {str(job['error'])[:200]}" if job.get("error") else (
                "its job never started" if not job else f"its job started {when(job.get('started_at') or 0, now)}")
            alerts.append(Alert(f"official:{row['id']}", f"{row['id']}'s official-map report is still pending, its "
                                f"result recorded {when(row.get('finished') or now, now)}: {detail}; see "
                                f"{run_dir / 'official.log'} (rerun: uv run loop/official.py run {row['id']})"))
    return alerts


# The wait before a missing turn alerts (see TURN_GRACE_MIN), from the timed turns saved for `rows`: seconds.
def turn_grace(state: Path, rows: list[dict]) -> float:
    times = []
    for row in rows:
        for name in ("postmortem", "followon"):
            try:
                output = prune.turn_result((state / "runs" / row["id"] / f"{name}.json").read_text())
            except OSError:
                continue
            if output and isinstance(ms := output.get("duration_ms"), (int, float)) and not isinstance(ms, bool):
                times.append(ms / 1000)
    if len(times) < TURN_GRACE_SAMPLES:
        return RESULT_TURN_GRACE
    times.sort()
    return max(TURN_GRACE_MIN, TURN_GRACE_TIMES * times[min(len(times) - 1, int(0.9 * len(times)))])


# loop/pack.py's status (pack.STATUS, rewritten at each upkeep pack): an alert when a pack failed (its folders stay
# as they were; why is in the file), when finished clones still hold more than pack.LIMIT of unpacked replay folders
# (packing fell behind the ~12 GB an hour coders' games write), or when the file doesn't read. None before the first.
def clone_replays(state: Path) -> list[Alert]:
    path = state / pack.STATUS
    if not path.exists():
        return []
    status = read_json(path)
    if not isinstance(status.get("unpacked"), dict) or not isinstance(status.get("failed"), dict):
        return [Alert("clone-replays:unreadable", f"{path} doesn't read as loop/pack.py's status")]
    alerts = [Alert(f"clone-replays:failed:{name}", f"packing {name}'s replay folders failed (they are kept as they "
                    f"were): {why}; see loop/pack.py") for name, why in sorted(status["failed"].items())]
    left = sum(n for n in status["unpacked"].values() if isinstance(n, int))
    if left > pack.LIMIT:
        alerts.append(Alert("clone-replays:behind", f"finished clones still hold {left / 2**30:.0f} GiB of unpacked "
                            f"replay folders (over {pack.LIMIT / 2**30:.0f} GiB) in {len(status['unpacked'])} clones "
                            f"after the upkeep's pack: packing is behind; run `.venv/bin/python loop/pack.py --apply`"))
    return alerts


# A recent result owed its coder's turn after the result (prune.owed_turn: a post-mortem, or a follow-on when kept)
# with no finished turn saved (postmortem.json or followon.json passing prune.turn_saved) turn_grace after it
# was recorded: the turn never ran (a stop request or a missing clone skips it; an exception is only in the driver's
# output; a native session didn't run native.py followups), printed nothing, or ended in an error result such as a
# usage limit (saved as <turn>.failed.json). Until it is saved, prune keeps the result's losses. The upkeep reruns
# each such turn once by itself (maintenance.owed_turns): while that rerun runs nothing alerts, and after it the alert
# says it didn't save the turn either.
def result_turns(state: Path, now: float) -> list[Alert]:
    path = state / "results.jsonl"
    if quota.paused(state, now):  # the usage pause holds every owed turn until the reset (loop/quota.py)
        return []
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    # rows are up to 2 MB; an id's latest row only (a rejudge's supersedes the one it re-judged, meta.latest_rows)
    alerts, rows = [], meta.latest_rows(official.json_rows([line for line in lines if '"session"' in line]))
    grace = turn_grace(state, rows)
    for row in rows:
        finished, name = row.get("finished") or 0, prune.owed_turn(row)
        output = state / "runs" / row["id"] / f"{name}.json"
        failed = output.with_suffix(".failed.json")
        if not name or now - finished <= grace or prune.turn_saved(output):
            continue
        turn = "follow-on" if name == "followon" else "post-mortem"
        pid = online.read(state / "runs" / row["id"] / "worker.json", {}).get("driver_pid")
        running = meta.alive(pid) and not output.with_suffix(".stderr").exists()
        auto = read_json(output.with_name(maintenance.AUTO_TURN.format(name)))
        if not auto and output.with_name(maintenance.HELD_TURN).exists():
            continue  # the usage pause held it, and the upkeep's rerun, one at a time, hasn't reached it yet
        auto_pid = auto.get("pid") if isinstance(auto, dict) else None
        if auto_pid is not None and meta.alive(auto_pid):  # the upkeep's rerun (maintenance.owed_turns) still runs
            continue
        again = (f"; the upkeep's one rerun ({when(auto['at'], now) if isinstance(auto.get('at'), (int, float)) else 'at an unrecorded time'}, "
                 f"{output.with_name(f'{name}.auto.log')}) didn't save it either" if auto else "")
        why = (f"it may still be running (driver pid {pid} alive; `--turn` refuses until it exits)" if running else
               "it failed (an error result, e.g. a usage limit; the output is in "
               f"{failed if failed.exists() else output})" if failed.exists() or output.exists() else
               "it never ran (a stop request or a missing clone skips it; a native session runs "
               f"native.py followups {row['id']} after it)")
        why += again
        alerts.append(Alert(f"{name}:{row['id']}", f"{row['id']} ({row['verdict']}, recorded {when(finished, now)}) "
                            f"has no {turn} from its coder {grace / 3600:.1f} h on (twice the recent turns' 90th "
                            f"percentile run time, at least {TURN_GRACE_MIN / 3600:.0f} h): {why}; rerun it with "
                            f"`uv run loop/driver.py --turn {row['id']}`; see {output.with_suffix('.stderr')}"))
    return alerts


# The judge's files (loop/judge.py; LOOP.md: Judged verdicts). A recent result owed a judge (judge.owed) with no
# judged.json JUDGE_GRACE after it was recorded: its judge never ran (a stop marker, a crash of the driver that
# recorded it, a native session that didn't do it) or failed (a usage limit, the judge lock held past its cap; its
# JUDGE FAILED alert has why). A judged.json that doesn't read as a record. An ask (a keep missing the guard) the
# orchestrator left undecided JUDGE_GRACE. A judge folder (<loop home>/judge/<E####>) left over JUDGE_GRACE: the
# judge makes it only once it holds the judge lock and runs at most judge.TIMEOUT, and apply removes it, so one left
# is a judge whose answer was never applied (a native one's included), never one waiting on the lock.
JUDGE_GRACE = 2 * 3600


def judge_turns(state: Path, now: float) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    alerts, rows = [], meta.latest_rows(official.json_rows([line for line in lines if '"session"' in line]))
    for row in rows:
        if not judge.owed(row) or now - (row.get("finished") or 0) <= JUDGE_GRACE:
            continue
        saved = state / "runs" / row["id"] / judge.FILE
        if record := judge.saved(state, row["id"]):
            if record["verdict"] == judge.ASK and now - (record.get("at") or 0) > JUDGE_GRACE:
                alerts.append(Alert(f"judge ask:{row['id']}", f"{judge.ask_line(row['id'], record)} (asked "
                                    f"{when(record.get('at') or 0, now)}, undecided)"))
            continue
        native = (row.get("session") or {}).get("provider") == "native-agent"
        how = (f"a native result: the active model does it (uv run loop/native.py judge {row['id']}, then native.py "
               f"judged {row['id']})" if native else f"rerun it with `uv run loop/driver.py --judge {row['id']}`"
               + (f" (it applies the answer a stop marker held, {judge.PENDING}, without a new judge)"
                  if judge.held(state, row["id"]) else ""))
        what = f"{saved} doesn't read as a judge record" if saved.exists() else "it has no judged verdict"
        alerts.append(Alert(f"judge:{row['id']}", f"{row['id']} ({row['verdict']}, recorded "
                            f"{when(row['finished'], now)}) is owed an independent judge but {what}: {how}; its "
                            "JUDGE FAILED alert, if any, says why"))
    folders = state / "judge"
    for work in sorted(folders.iterdir()) if folders.is_dir() else []:
        try:
            age = now - work.lstat().st_mtime
        except OSError:
            continue
        if age > JUDGE_GRACE:
            alerts.append(Alert(f"judge folder:{work.name}", f"{work} (last changed {when(now - age, now)}) is a "
                                "judge whose answer was never applied (applying removes it): rerun `uv run "
                                f"loop/driver.py --judge {work.name}` or remove it"))
    return alerts


# Followup ideas whose promising result (meta.promising_result, the newest one on the idea) its judge discarded
# (runs/E####/judged.json): a followup waits for an unchanged retest, which the judge ruled against, and
# driver.apply_judged marks such an idea tried (retire_discarded), so one left is a discard saved and alerted but not
# applied (MISSES 98: I697, I897 and I846 after E0273, E0318 and E0351). So is a todo "Retest E#### alone" a
# post-mortem queued of a result its judge discarded (meta.postmortem_retest: I914 after E0320), which add_followups
# no longer queues and retire_discarded drops. Reads each named result's own judged.json only.
def judged_followups(state: Path) -> list[Alert]:
    path = state / "ideas.jsonl"
    ideas = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            ideas.append(json.loads(line))
        except ValueError:
            continue  # backlog_rows alerts on it

    def discarded(exp_id: str | None) -> bool:
        return bool(exp_id) and (judge.saved(state, exp_id) or {}).get("verdict") == "discard"

    ideas = [i for i in ideas if isinstance(i, dict)]
    followups = [f"{i.get('id')} ({exp_id})" for i in ideas
                 if i.get("status") == "followup" and discarded(exp_id := meta.promising_result(i))]
    retests = [f"{i.get('id')} ({exp_id})" for i in ideas if discarded(exp_id := meta.postmortem_retest(i))]
    found = ([f"{len(followups)} followup ideas wait for a retest their judge discarded: {', '.join(followups[:10])}; "
              "mark each tried"] if followups else []) + (
             [f"{len(retests)} post-mortem retests of a result its judge discarded are queued: "
              f"{', '.join(retests[:10])}; drop each (a retest that changes something is its own idea)"]
             if retests else [])
    if not found:
        return []
    return [Alert("judged followups", f"{'; '.join(found)} (driver.mark_idea), and find why driver.apply_judged "
                  "(retire_discarded) or add_followups didn't (MISSES 98)")]


# A "no code" result among the last official.RECENT whose clone (in `clones`) still has uncommitted changes in bot/ or
# tests/ and no wip/E#### branch in `repo` (MISSES 63): its coder's work was probably cut off, not finished with
# nothing to hand in. E0197's coder ended "Waiting for the 24 re-run games to finish; I'll be notified when they do."
# with its change uncommitted, and was recorded as a no-code stop that marked I441 tried. driver.continue_turn now
# resumes such a turn once; this catches what still gets through. A result whose coder says it hands in no code on
# purpose (prune.no_code_on_purpose: E0111's "committed nothing, as the orchestrator asked", its measurement edits
# left in the clone) and doesn't also say it is still waiting (prune.still_waiting) is skipped. Once per result, `seen` keeping the ids alerted. git runs in the coder's clone only
# after clone_intact (meta), with fsmonitor and hooks off and no optional locks (MISSES 17).
def uncommitted_no_code(state: Path, repo: Path, clones: Path, seen: list) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    rows = [r for r in official.json_rows([line for line in lines if '"no code"' in line])
            if r.get("verdict") == "no code"]
    seen[:] = [i for i in seen if i in {r["id"] for r in rows}]
    alerts = []
    for row in rows:
        clone = clones / row["id"]
        summary = (row.get("session") or {}).get("summary") or ""
        if (row["id"] in seen or not (clone / ".git").is_dir()
                or prune.no_code_on_purpose(summary) and not prune.still_waiting(summary)):
            continue
        saved = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/heads/wip/{row['id']}"], cwd=repo,
                               capture_output=True, timeout=60).returncode == 0
        if saved:
            continue
        try:
            clone_intact(clone, state)
            left = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "status",
                                   "--porcelain", "--", "bot", "tests"], cwd=clone, capture_output=True, text=True,
                                  check=True, timeout=60, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        except (subprocess.SubprocessError, OSError, RuntimeError):
            continue  # a clone being removed meanwhile, or one whose .git its coder changed (clone_intact)
        if not left.strip():
            continue
        seen.append(row["id"])
        files = [line[3:] for line in left.splitlines()]
        idea = (row.get("idea") or {}).get("id")
        alerts.append(Alert(f"uncommitted:{row['id']}", f"{row['id']} ({idea}) was recorded as no code "
                            f"({str(row.get('reason'))[:160]}) but its clone {clone} has {len(files)} uncommitted "
                            f"files in bot/ and tests/ ({', '.join(files[:5])}): its coder's work was probably cut off "
                            f"(MISSES 63); if it is the idea's unfinished work, save it as wip/{row['id']} and put "
                            f"{idea} back to todo (driver.mark_idea), else say why not"))
    return alerts


# Screen reuse (driver.check_pool) that fell back to playing a rotation opponent's whole screen in a recent result:
# safe, but the time it saves is lost silently (the SPRT left no sprt-rotation.jsonl, or a trial didn't match).
def screen_reuse(state: Path) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    alerts = []
    for row in official.json_rows([line for line in lines if '"not_reused"' in line]):  # rows are up to 2 MB
        missed = [o for o in (row.get("pool") or {}).get("opponents") or [] if o.get("not_reused")]
        if missed:
            alerts.append(Alert(f"reuse:{row['id']}", f"{row['id']}'s screen replayed the SPRT's games against "
                                f"{', '.join(o['name'] for o in missed)}: {missed[0]['not_reused'][:200]}; see "
                                f"driver.rotation_trials and {state / 'runs' / row['id'] / 'sprt-rotation.jsonl'}"))
    return alerts


# How tight the SPRTs' pairs are, from move prediction's checks (sprt.json's "predict" on each result's row): the share
# of the candidate's opponent games that repeat the champion's move for move, pooled over the newest PREDICT_RUNS
# results that checked any. Under PREDICT_FLOOR of at least PREDICT_MIN_CHECKS means something every build carries
# makes every pair differ even where a change can't act, costing every test games (MISSES 99: each build's own sonar
# key, from E0090, gave 0 of 2,230 checks in 22 SPRTs, seen then only as prediction switching itself off). A candidate
# whose change acts in every game repeats none by right, so one result alone never alerts.
PREDICT_RUNS, PREDICT_MIN_CHECKS, PREDICT_FLOOR = 5, 300, 0.05


def pair_tightness(state: Path) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    checked = [(row["id"], predict) for row in official.json_rows([line for line in lines if '"checks"' in line])
               if (predict := (row.get("sprt") or {}).get("predict") or {}).get("checks")][-PREDICT_RUNS:]
    checks = sum(predict["checks"] for _, predict in checked)
    identical = sum(predict.get("identical") or 0 for _, predict in checked)
    if len(checked) < PREDICT_RUNS or checks < PREDICT_MIN_CHECKS or identical >= PREDICT_FLOOR * checks:
        return []
    ids = ", ".join(exp for exp, _ in checked)
    return [Alert(f"pair tightness:{checked[-1][0]}", f"only {identical} of {checks} move-prediction checks in "
                  f"{ids} repeated the champion's game: something every build shares now makes every pair differ, "
                  "costing each test games (MISSES 99's sonar key); compare a champion copy with a comment added "
                  "against the champion (identical.play_digest) and fix it")]


# A recent targeted result whose target batch (driver.target_batch) read under meta.TARGET_READ_SHARE of its replays (a
# measure the referee copy can't read, replays timing out or missing from remote hosts), or whose run still holds
# target-replays/ (the batch deletes them once read: up to 15 MB each). Its target-units.jsonl is TARGET_UNITS rows
# by construction, so it has no size check of its own.
def target_batches(state: Path) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    alerts = []
    for row in official.json_rows([line for line in lines if '"targeted"' in line]):
        target = row.get("target") or {}
        if (row.get("idea") or {}).get("kind") != "targeted":
            continue
        played = target.get("played") or 0
        if played and played - target.get("unreadable", 0) < meta.TARGET_READ_SHARE * played:
            alerts.append(Alert(f"target:{row['id']}", f"{row['id']}'s target batch read "
                                f"{played - target.get('unreadable', 0)} of {played} replays ({target.get('line')}); "
                                "see referee/measures.py read"))
    # A driver killed mid-batch (SIGKILL skips target_batch's cleanup) leaves up to ~4 GB of replays in a run with no
    # result row: any target-replays folder older than the driver's 12 h evaluation timeout is left over.
    for folder in (state / "runs").glob("*/target-replays") if (state / "runs").exists() else []:
        if time.time() - folder.stat().st_mtime > 13 * 3600:
            alerts.append(Alert(f"target-replays:{folder.parent.name}", f"{folder} is still there 13 h on: "
                                "driver.target_batch deletes it, so its run was killed mid-batch; delete it"))
    return alerts


# Rows of the live ideas.jsonl that fail meta.idea_fields_ok or aren't JSON. Every backlog writer refuses while one
# is there (meta.ideas_text), so claims, results and reviews stop until it is fixed by hand (a row appended without
# "status", I340 on 2026-09-27, crashed every driver before the writers checked).
def backlog_rows(state: Path) -> list[Alert]:
    path = state / "ideas.jsonl"
    bad = []
    for number, line in enumerate(path.read_text().splitlines() if path.exists() else [], 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad.append(f"line {number} (not JSON)")
            continue
        if not idea_fields_ok(row):
            name = row.get("id") if isinstance(row, dict) else None
            bad.append(f"{str(name)[:40] if name else 'no id'} (line {number})")
    if not bad:
        return []
    return [Alert("backlog rows", f"{len(bad)} rows of {path} lack a field every reader needs {IDEA_FIELDS} or aren't "
                  f"JSON: {', '.join(bad[:10])}; every backlog writer refuses until they are fixed "
                  "(meta.idea_fields_ok)")]


# <loop home>/ideas-writes.log, a line per backlog write (meta.log_ideas_write, called by meta.ideas_text, which every
# writer of the live ideas.jsonl goes through, just before the write; since 8aec8662, IDEAS_LOG_SINCE): it must exist
# once the backlog was written since, its newest line must be no older than the backlog's last write (IDEAS_LOG_SLACK
# for a slow write), and it stays under the bound log_ideas_write trims it at (2 MB, cut to its last 2000 lines just
# before an append: IDEAS_LOG_BOUND, one line over allowed). log_ideas_write swallows its errors so a diagnostic never
# blocks a write, so a failing log is silent but for this. A loop process started before IDEAS_LOG_SINCE runs the old
# code (home.pin_code) and writes without a line, so the freshness check waits for those to end (LOOP_SCRIPT).
IDEAS_LOG_SINCE = datetime.fromisoformat("2026-09-29T10:38:30+10:00").timestamp()
IDEAS_LOG_SLACK = 60
IDEAS_LOG_BOUND = 2_000_000 + 4096
LOOP_SCRIPT = re.compile(r"\bloop/\w+\.py\b")


def ideas_writes(state: Path, now: float, rows: list[PsRow] | None = None) -> list[Alert]:
    ideas, path = state / "ideas.jsonl", state / "ideas-writes.log"
    try:
        written = ideas.stat().st_mtime
    except OSError:
        return []
    if written < IDEAS_LOG_SINCE or now < IDEAS_LOG_SINCE:
        return []
    if not path.exists():
        return [Alert("ideas log:missing", f"{ideas} was written {when(written, now)} but {path} doesn't exist: "
                      "meta.log_ideas_write, which swallows its errors, logged no backlog write (MISSES 89)")]
    size = path.stat().st_size
    if size > IDEAS_LOG_BOUND:
        return [Alert("ideas log:size", f"{path} is {size / 1e6:.1f} MB, over the 2 MB meta.log_ideas_write trims it "
                      "at: its trim is failing (it swallows its errors)")]
    with path.open("rb") as handle:
        handle.seek(max(0, size - 8192))
        tail = handle.read().decode("utf-8", "replace").splitlines()
    last = None
    for line in reversed(tail):
        with_at = re.match(r'\{"at": ([0-9.]+)', line)
        if with_at:
            last = float(with_at.group(1))
            break
    if last is not None and written - last <= IDEAS_LOG_SLACK:
        return []
    rows = ps_rows() if rows is None else rows
    if any(LOOP_SCRIPT.search(row.args) and now - row.alive < IDEAS_LOG_SINCE for row in rows):
        return []  # an old-code writer still runs: its writes log nothing
    newest = "no readable line" if last is None else f"its newest line from {when(last, now)}"
    return [Alert(f"ideas log:stale:{int(written)}", f"{ideas} was written {when(written, now)} but {path} has "
                  f"{newest}: a backlog write left no line (a writer bypassing meta.ideas_text, or "
                  "meta.log_ideas_write failing silently), so the next lost write can't be traced (MISSES 89)")]


# Todo ideas whose prerequisites can never clear, so driver.ready_ideas never claims them: each waits on itself, through
# its `after` entries followed through merges and other ideas still to do or being built (meta.waits_on), such as an
# `after` naming an idea already merged into it, or A after K while K takes, from a row merged into it, an `after` A.
def stuck_ideas(state: Path) -> list[Alert]:
    path = state / "ideas.jsonl"
    ideas = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # backlog_rows alerts on it
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ideas.append(row)
    by_id = {i["id"]: i for i in ideas}
    stuck = [i["id"] for i in ideas if i.get("status") == "todo" and waits_on(i["id"], i["id"], by_id)]
    if not stuck:
        return []
    return [Alert("stuck ideas", f"{len(stuck)} todo ideas wait on themselves through `after` and merges and can never "
                  f"be claimed: {', '.join(stuck[:10])}; remove the looping `after` entry ({path}, through "
                  "driver.mark_idea or a locked driver.write_ideas)")]


# sig's recorded ranking (meta.RANKING, written by `driver.py --ranking`): alerts when it can't be read as one (the
# status line then marks nothing out of rank), or when none of its ideas is still to do, so it orders nothing and a
# newer ranking sig gave may be unrecorded.
def ranking(state: Path) -> list[Alert]:
    path = state / meta.RANKING
    if not path.exists():
        return []
    raw = read_json(path)
    order = raw.get("order")
    if not (isinstance(order, list) and order and all(isinstance(i, str) for i in order)
            and isinstance(raw.get("at"), (int, float)) and isinstance(raw.get("source"), str) and raw["source"].strip()):
        return [Alert("ranking:malformed", f"{path} is not a ranking ({{at, source, order: [idea ids]}}), so no idea "
                      "claimed out of it is marked; record it again with `driver.py --ranking I###,... --rule "
                      "\"<sig's words, date>\"`")]
    ideas = state / "ideas.jsonl"
    todo = set()
    for line in ideas.read_text().splitlines() if ideas.exists() else []:
        with suppress(ValueError):
            if isinstance(row := json.loads(line), dict) and row.get("status") == "todo":
                todo.add(row.get("id"))
    if todo & set(order):
        return []
    return [Alert(f"ranking:spent:{raw['at']:.0f}", f"none of the {len(order)} ideas in sig's ranking "
                  f"({raw['source']}) is still to do, so it orders nothing: record his newer ranking with "
                  f"`driver.py --ranking`, or delete {path} if he gave none")]


# A post-mortem's or follow-on's worker file (runs/E####/<turn>-worker.json, driver.run_postmortem): alerts when it
# can't be read as that turn's (its experiment, turn and bc-E####-<turn>-<8> name disagree with where it is), or when
# its driver died mid-turn: pid dead with no <turn>.stderr, which the driver writes as the turn ends, so the session
# the file names is not the loop's any more. Only files from the last TURN_WORKER_AGE are read.
TURN_WORKER_AGE = 2 * 86400


def turn_workers(state: Path, now: float) -> list[Alert]:
    alerts = []
    for path in sorted((state / "runs").glob("E*/*-worker.json")):
        turn = path.name.removesuffix("-worker.json")
        if turn not in ("postmortem", "followon") or now - path.stat().st_mtime > TURN_WORKER_AGE:
            continue
        run, raw = path.parent, read_json(path)
        session = raw.get("session_id")
        if not (raw.get("experiment") == run.name and raw.get("turn") == turn and isinstance(session, str)
                and raw.get("name") == f"bc-{run.name}-{turn}-{session[:8]}"
                and isinstance(raw.get("started"), (int, float))):
            alerts.append(Alert(f"turn worker:malformed:{run.name}:{turn}", f"{path} doesn't describe {run.name}'s "
                                f"{turn} turn (experiment, turn, name bc-{run.name}-{turn}-<8>, session_id, started), "
                                "so its session can't be told from any other before messaging it"))
        elif not meta.alive(raw.get("driver_pid")) and not (run / f"{turn}.stderr").exists():
            alerts.append(Alert(f"turn worker:dead:{run.name}:{turn}", f"{run.name}'s {turn} turn "
                                f"({raw['name']}, started {when(raw['started'], now)}) lost its driver (pid "
                                f"{raw.get('driver_pid')} dead, no {turn}.stderr): rerun it with `uv run "
                                f"loop/driver.py --turn {run.name}`; see {path}"))
    return alerts


# In-flight ideas whose newest try is past coding while its evaluator is dead (meta.stranded: a driver or native
# `evaluate` killed before recording): nothing will evaluate it, and it holds its lane. One already in results.jsonl is
# left out: driver.finish_recorded frees its idea at the next claim (dead_lanes alerts if it doesn't). Neither
# provider's driver recovers the rest by itself; native resume and abandon work on either provider's saved work
# (LOOP.md: Recovery). Nothing while a stop marker stands: a native evaluate stopped by one leaves its try so, and
# resume refuses until sig resumes.
def stranded(state: Path) -> list[Alert]:
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
        return []
    path = state / "ideas.jsonl"
    ideas = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # backlog_rows alerts on it
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ideas.append(row)
    results = state / "results.jsonl"
    lines = results.read_text().splitlines() if results.exists() else []
    recorded = {row.get("id") for row in official.json_rows(lines)}
    alerts = []
    for idea, exp, worker in meta_stranded(state, ideas):
        if exp in recorded:
            continue
        lane = idea["status"].removeprefix("trying:")
        who = {"native-agent": "a native lane", "tuner": "a tune (either provider)"}.get(worker.get("provider"),
                                                                                       "the Claude driver")
        resume = (f"after confirming no other writer owns its clone, `uv run loop/native.py resume {exp}` hands it to "
                  f"a native session and `uv run loop/native.py evaluate {exp} '<what changed>'` then tests it")
        abandon = f"`uv run loop/native.py abandon {exp} '<reason>'` records it as an error"
        dead = f"its evaluator, pid {worker.get('driver_pid')}, is dead"
        if idea.get("kind") != "tune":
            fix = (f"{resume} (or `uv run loop/native.py abandon {exp} '<reason>'` records it discarded, marking "
                   f"{idea['id']} tried)")
        elif worker.get("status") == "finished":  # commit_tune committed its values in the clone
            # A tune has no coding to resume (native.resume refuses it): its committed values go to evaluate.
            summary = shlex.quote((worker.get("summary") or "<summary>").splitlines()[0])
            if worker.get("driver_pid") is None:  # native claim's handoff (meta.TUNED_WAIT_S)
                dead = (f"claim returned tuned over {TUNED_WAIT_S // 60} min ago and no evaluate took it up (the "
                        "session died or forgot it)")
            fix = (f"`uv run loop/native.py evaluate {exp} {summary}` tests its tuned values; or {abandon} (only its "
                   f"SPRT judges a tune), so {idea['id']} goes back to the queue and its next claim reuses its tuned "
                   "values while their spec and starting values still match (driver.earlier_tune)")
        else:
            fix = (f"its SPSA died before it ended, and tune.py writes its values only then, so none are left: "
                   f"{abandon} (only its SPRT judges a tune), so {idea['id']} goes back to the queue for a fresh tune "
                   "(a second error retires it, driver.finish_idea)")
        alerts.append(Alert(f"stranded:{exp}", f"{exp} ({idea['id']}, {lane}, run by {who}) is past coding "
                            f"(worker.json {worker.get('status')}) but {dead}, so nothing will evaluate or record it: "
                            f"{fix}; see {state / 'runs' / exp / 'worker.json'}"))
    return alerts


DEAD_LANE_AFTER = 15 * 60


# In-flight ideas (`trying:laneN`) whose current try (meta.current_tries) has had no live loop process (worker.json's
# driver_pid, meta.evaluator_alive) for DEAD_LANE_AFTER, in the cases stranded leaves out: a try already in
# results.jsonl whose idea lacks finish_idea's note (meta.result_note; driver.finish_recorded frees it at the next
# claim, so one still held means no claim ran or it failed), timed from the later of its result and its worker.json's
# last write (a turn after the result, a post-mortem's, keeps the process alive past it), and one whose driver died
# while it was still being coded, timed from its worker.json's last write (nothing records when that process died). A
# recorded try with no worker.json (recorded at setup) counts as having no process. Each holds a lane
# (driver.pipeline_lanes), so claims are refused as full with fewer lanes live: I838 and I855 held lanes 2 and 3 for 25
# min on 29 Sep with no process. results.jsonl is read, rows only (meta.result_rows), only when some try is dead and old
# enough.
def dead_lanes(state: Path, now: float) -> list[Alert]:
    trying = {i["id"]: i for i in backlog(state) if i["status"].startswith("trying:")}
    dead = {}
    for name, (exp, _, folder) in meta.current_tries(state, trying, now).items():
        path = folder / "worker.json"
        worker = read_json(path) if path.exists() else None
        written = meta.record_time(path, 0.0)
        if worker is not None and (now - written < DEAD_LANE_AFTER or meta.evaluator_alive(worker, now - written)):
            continue
        dead[exp] = (name, worker, written, folder)
    rows = meta.result_rows(state / "results.jsonl", dead) if dead else {}
    alerts = []
    for exp, (name, worker, written, folder) in sorted(dead.items()):
        idea, row = trying[name], rows.get(exp)
        if row is not None:
            if meta.result_note(row) in (idea.get("history") or []):
                continue
            age = now - max(row.get("finished") or 0.0, written)
            fate = ("was recorded, but its idea wasn't updated: the next claim frees it (driver.finish_recorded); if "
                    "none can run, set the idea from its result with driver.finish_idea")
        elif worker is None or worker.get("status") not in meta.CODING_STATUSES or any(
                str(line).startswith(f"{exp}:") for line in idea.get("history") or []):
            continue
        else:
            age = now - written
            fate = f"was still being coded (worker.json {worker.get('status')}): recover it (LOOP.md: Recovery)"
        if age < DEAD_LANE_AFTER:
            continue
        pid = (worker or {}).get("driver_pid")
        alerts.append(Alert(f"dead lane:{exp}", f"DEAD LANE: {name} reads {idea['status']} but its try {exp} has had "
                            f"no live process (driver pid {pid}) for {age / 60:.0f} min and holds a lane, so claims "
                            f"count it in flight; {exp} {fate}; see {folder}"))
    return alerts


# A kept or promising result among the last official.RECENT with a map style or size bucket clearly worse than the
# champion (meta.map_buckets: past the bar at which luck alone puts any of its buckets there at most chance.TRIGGER_P
# of the time). A report, never a gate or a demotion; once per result, `seen` keeping the ids already alerted.
def map_splits(state: Path, seen: list) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    rows = [r for r in official.json_rows([line for line in lines if '"per_map"' in line])
            if r.get("verdict") in ("kept", "promising")]
    seen[:] = [i for i in seen if i in {r["id"] for r in rows}]
    alerts = []
    for row in rows:
        buckets, bar = map_buckets(row.get("sprt") or {})
        if row["id"] in seen or not (worse := [b for b in buckets if b["z"] <= -bar]):
            continue
        seen.append(row["id"])
        named = "; ".join(f"{b['name']} {b['score']:.2f} over {b['trials']} trials (z {b['z']:.1f}, p "
                          f"{chance.low_p(b['z']):.2g}, {min(1, len(buckets) * chance.low_p(b['z'])):.2g} over its "
                          f"{len(buckets)} map buckets)" for b in worse)
        alerts.append(Alert(f"map split:{row['id']}", f"{row['id']} ({row['verdict']}) is clearly worse than the "
                            f"champion on {named}: a report, not a gate; consider a version conditioned on the map's "
                            f"size or style (CLAUDE.md, Conditional, not constant); see {path}"))
    return alerts


# A map bucket (meta.map_buckets: a style, a near layout, a pooled family or a size band) the candidate scored below
# the champion (z < 0) in each of the last 2+ results that measured it, the newest among them, with their combined z
# (their sum over the root of their count) past chance.corrected_z over the newest's buckets, so luck alone alerts at
# most chance.TRIGGER_P of the time (REFLECT 2026-09-30 15:13 F24: E0400 and E0401, both portal-crossing changes,
# leaned worse on near_big_empty and nothing checked it; their rows read z -3.5 and -3.8). The results are the
# newest STREAK_RESULTS with an SPRT (their run folders' sprt.json), any verdict, in finish order. A report, never a
# gate; once per bucket and newest result, `seen` keeping those alerted.
STREAK_RESULTS = 10


def style_streaks(state: Path, seen: list) -> list[Alert]:
    runs = sorted((p.parent for p in (state / "runs").glob("E*/sprt.json")),
                  key=lambda run: (run / "sprt.json").stat().st_mtime)[-STREAK_RESULTS:]
    rows = sorted((row for row in meta.result_rows(state / "results.jsonl", [run.name for run in runs]).values()
                   if isinstance(row.get("finished"), (int, float))), key=lambda row: row["finished"])
    measured = [(row["id"], {b["name"]: b for b in map_buckets(row.get("sprt") or {})[0]}) for row in rows]
    if not measured:
        return []
    newest, buckets = measured[-1]
    bar, alerts = chance.corrected_z(len(buckets)), []
    for name in buckets:
        streak = []
        for exp, found in reversed(measured):
            if name not in found:
                continue
            if found[name]["z"] >= 0:
                break
            streak.append((exp, found[name]))
        z = sum(b["z"] for _, b in streak) / math.sqrt(len(streak)) if streak else 0.0
        if len(streak) < 2 or z > -bar or f"{name}:{newest}" in seen:
            continue
        seen.append(f"{name}:{newest}")
        listed = "; ".join(f"{exp} {b['score']:.2f} over {b['trials']} trials (z {b['z']:.1f})" for exp, b in streak)
        alerts.append(Alert(f"style streak:{name}:{newest}", f"STYLE STREAK: {name} read worse than the champion in "
                            f"each of the last {len(streak)} results that measured it ({listed}), combined z {z:.1f} "
                            f"(p {chance.low_p(z):.2g}, {min(1, len(buckets) * chance.low_p(z)):.2g} over the newest's "
                            f"{len(buckets)} map buckets): a report, not a gate; measure what these changes do there "
                            f"and queue a measure-first idea, or say why not; see {state / 'results.jsonl'}"))
    seen[:] = seen[-100:]
    return alerts


# A kept result among the last official.RECENT whose finished official-map report scores below half over the games
# where its moves changed (official.changed_games), with its pairs lost against won at sign p <= chance.TRIGGER_P
# (M53: E0175 went live at 13 pairs lost, 6 won, 19 split there, p 0.084, and nobody read its report), corrected for
# how many kept results were tested (Bonferroni: p x tested <= TRIGGER_P; a result counts as tested only when enough
# pairs were decided for it to reach TRIGGER_P). A report, never a gate or a demotion; once per result, `seen`
# keeping the ids already alerted.
def official_changed(state: Path, seen: list) -> list[Alert]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    rows = [r for r in official.json_rows([line for line in lines if '"kept"' in line]) if r.get("verdict") == "kept"]
    seen[:] = [i for i in seen if i in {r["id"] for r in rows}]
    tested = {}
    for row in rows:
        games = official.folder(state, row["id"]) / "official.jsonl"
        if "score" in official.report(state, row) and games.exists():
            changed = official.changed_games(official.json_rows(games.read_text().splitlines()))
            if 0.5 ** (changed["pairs_lost"] + changed["pairs_won"]) <= chance.TRIGGER_P:  # one that could fire
                tested[row["id"]] = changed
    alerts = []
    for rid, changed in tested.items():
        score = (changed["wins"] + (changed["games"] - changed["wins"] - changed["losses"]) / 2) / max(changed["games"], 1)
        luck = min(1.0, changed["sign_p"] * len(tested))  # any of the tested results this low by luck alone
        if rid in seen or score >= 0.5 or luck > chance.TRIGGER_P:  # alerts fire only at p <= TRIGGER_P (MISSES 34)
            continue
        seen.append(rid)
        verdict = f"{len(tested)} kept results tested, luck {luck:.2g}"
        alerts.append(Alert(f"official changed:{rid}", f"{rid} (kept) scores {score:.2f} against the champion over "
                            f"the {changed['pairs']} official-map pairs where its moves changed: "
                            f"{changed['pairs_lost']} lost, {changed['pairs_won']} won, {changed['pairs_split']} "
                            f"split, sign p {changed['sign_p']:.2g} over pairs ({verdict}; {changed['wins']} won, "
                            f"{changed['losses']} lost of {changed['games']} games; on {', '.join(changed['maps'])}): "
                            f"a report, not a gate; measure why its gain doesn't carry to those maps and consider a "
                            f"version conditioned on what differs there (CLAUDE.md, Conditional, not constant); see "
                            f"{official.folder(state, rid) / 'official.jsonl'}"))
    return alerts


# The current champion missing a current screen entry (loop/rescreen.py): a late screen alive but with no entry finished
# for rescreen.STALE ("running since"), runs failing twice in a row, or none started LATE_SCREEN_GRACE after `seen`
# first found the entry missing. A run that is alive and progressing is quiet.
def late_screen(state: Path, repo: Path, now: float, seen: dict) -> list[Alert]:
    found = rescreen.missing(state, repo)
    key = f"late screen {found[0]}" if found else None  # a new champion starts its own clock
    for old in [k for k in seen if k.startswith("late screen ") and k != key]:
        seen.pop(old)
    if found is None:
        return []
    since = seen.setdefault(key, now)
    champion, row, names = found
    record = rescreen.read(state).get(champion, {})
    lacking = f"champion {champion[:7]} ({row['id']}) has not met the screen's {', '.join(names)}"
    where = f"see {state / 'rescreen.json'} and {state / 'rescreens' / champion / 'run.log'}"
    progress = record.get("progress_at") or record.get("started_at")
    if rescreen.meta.alive(record.get("pid")):
        if isinstance(progress, (int, float)) and now - progress > rescreen.STALE:
            return [Alert("late-screen:stale", f"{lacking}: the late screen (pid {record['pid']}) is running since "
                          f"{when(record.get('started_at') or progress, now)} but has finished no entry since "
                          f"{when(progress, now)}; {where}")]
        return []
    if (record.get("failures") or 0) >= 2:
        return [Alert("late-screen:failed", f"{lacking}: the late screen failed {record['failures']} runs in a row, the "
                      f"latest {when(record.get('failed_at') or now, now)}: {str(record.get('error'))[:200]}; {where}")]
    if not record.get("started_at") and now - since > LATE_SCREEN_GRACE:
        return [Alert("late-screen", f"{lacking} since {when(since, now)}, and no late screen has started: the upkeep's "
                      f"late-screen step isn't running or keeps raising; {where}")]
    return []


# The screen-port step (loop/screenport.py): its last run raised or queued an idea that failed validation, or
# results.jsonl changed after its last run, over SCREENPORT_GRACE ago, although the upkeep is running.
def screen_ports(state: Path, now: float) -> list[Alert]:
    path = state / screenport.RECORD
    record = read_json(path)
    alerts = []
    if error := str(record.get("error") or "").strip():
        alerts.append(Alert("screenport:failed", f"the screen-port step (loop/screenport.py) failed "
                            f"{when(record.get('failed_at') or now, now)}: {error[:200]}; see {path}"))
    try:
        changed = (state / "results.jsonl").stat().st_mtime
    except OSError:
        return alerts
    checked, upkeep_at = record.get("checked_at") or 0, read_json(state / "maintenance.json").get("checked_at")
    if changed > checked and now - changed > SCREENPORT_GRACE and isinstance(upkeep_at, (int, float)) \
            and now - upkeep_at < UPKEEP_ALIVE:
        last = when(checked, now) if checked else "never"
        alerts.append(Alert("screenport:stale", f"the screen-port step (loop/screenport.py) last ran {last}, though "
                            f"results.jsonl changed {when(changed, now)} and the upkeep ran {when(upkeep_at, now)}: "
                            f"its step isn't running or raises first (look for 'screen ports failed' in the upkeep "
                            f"log); see {path}"))
    return alerts


# sweep.GROUPS drifting from the champion: a group skipped because a param it names isn't in the champion's
# params.hpp (renamed or deleted), so that decision's gates are never opened together. Other skips (already open, the
# same as a single variant) are expected and quiet.
def sweep_groups(repo: Path) -> list[Alert]:
    found = subprocess.run(["git", "show", f"{CHAMPION_BRANCH}:bot/params.hpp"], cwd=repo, capture_output=True,
                           text=True, timeout=60)
    if found.returncode != 0:
        return []
    drifted = [f"{s['param']} ({s['reason']})" for s in sweeps.variants(found.stdout)["skipped"]
               if s["reason"].startswith("group needs")]
    if not drifted:
        return []
    return [Alert("sweep:groups", f"sweep.GROUPS names params the champion doesn't have, so these decision groups are "
                  f"never played: {'; '.join(drifted)[:400]}; update GROUPS in {Path(sweeps.__file__)} from the "
                  "champion's bot/params.hpp")]


# tools/check_refs.py's errors on the main checkout (broken paths, `module.name`s and params in docs and comments;
# MISSES 22), in files the checkout hasn't changed since HEAD, since edits under way are the pre-commit hook's to
# check. Quiet when the checkout has no checker.
def doc_refs(repo: Path) -> list[Alert]:
    checker = repo / "tools/check_refs.py"
    if not checker.exists():
        return []
    done = subprocess.run([sys.executable, str(checker), "--root", str(repo), "--json"], capture_output=True,
                          text=True, timeout=300)
    edited = set(git(repo, "diff", "--name-only", "HEAD").splitlines())
    errors = [p for p in json.loads(done.stdout) if p["level"] == "error" and p["path"] not in edited]
    if not errors:
        return []
    shown = "; ".join(f"{p['path']}:{p['line']} {p['kind']} {p['ref']}" for p in errors[:5])
    return [Alert("docs:refs", f"DOC REFS: {len(errors)} broken references in main's docs and comments ({shown}"
                  f"{'; ...' if len(errors) > 5 else ''}); fix them on a branch; list them with "
                  f".venv/bin/python tools/check_refs.py in {repo}")]


# main's committed CLAUDE.md or loop/LOOP.md over its byte cap (tools/doc_size.py's LIMITS), or a doc's "LOOP.md:
# <heading>" pointer naming no LOOP.md heading, which the pre-commit hook blocks on a branch but a merge made with
# hooks off, or a cap lowered later, can still leave. Quiet when the checkout has no tool or no main branch; a tool
# that crashes raises, so health.run alerts on it rather than reading its silence as all clear.
def doc_size(repo: Path) -> list[Alert]:
    tool = repo / "tools/doc_size.py"
    if not tool.exists():
        return []
    done = subprocess.run([sys.executable, str(tool), "--root", str(repo), "--rev", "main", "--json"],
                          capture_output=True, text=True, timeout=60)
    if done.returncode and not done.stdout.strip():
        raise RuntimeError(done.stderr.strip()[-300:] or "doc_size.py failed")
    alerts = []
    for item in json.loads(done.stdout or "[]"):
        if "pointer" in item:
            alerts.append(Alert(f"docs:pointer:{item['path']}:{item['pointer']}",
                                f"DOC POINTER: main's {item['path']} says {item['pointer']!r}: {item['advice']}, "
                                "on a branch"))
        else:
            alerts.append(Alert(f"docs:size:{item['path']}",
                                f"DOC SIZE: main's {item['path']} is {item['bytes']:,} bytes, over its "
                                f"{item['limit']:,}-byte cap (doc_size.LIMITS in {tool}): {item['advice']}, on a "
                                "branch"))
    return alerts


# The newest review's rule counts (loop/rulecounts.py, M17; status.json in its protected run folder): its fresh games
# failed, or its measurement died before finishing, so that review audited the champion's rules without them.
def rule_counts(state: Path, now: float) -> list[Alert]:
    record = read_json(state / "review.json")
    folder = record.get("pending") or record.get("last")
    if not isinstance(folder, str) or not Path(folder).name.isdigit():
        return []
    work = Path(folder)
    if not rulecounts.read_status(state, work):
        return []  # not started yet, or a review from before M17
    run = rulecounts.run_folder(state, work)
    found = rulecounts.status(state, work)
    if found == "failed":
        text = (run / rulecounts.NAME).read_text() if (run / rulecounts.NAME).exists() else ""
        problem = text.splitlines()[0].removeprefix(rulecounts.FAILED).lstrip(": ")[:200] if text else "no note"
    elif found is None:
        problem = "its measurement died before finishing"
    else:
        return []
    written = (run / "status.json").stat().st_mtime
    return [Alert(f"rule-counts:{work.name}", f"review {work.name} has no rule counts: {problem}, written "
                  f"{when(written, now)}; see {run / rulecounts.NAME} and {run / 'games.log'}")]


# A review pending more than REVIEW_PENDING since its folder was made (its name is the time_ns it was prepared): its
# worker keeps failing before or after it starts (e.g. something it left in its folder), or never exits, and every
# relaunch meanwhile only reports it running or deferred.
def review_pending(state: Path, now: float) -> list[Alert]:
    record = read_json(state / "review.json")
    folder = record.get("pending")
    if not isinstance(folder, str) or not Path(folder).name.isdigit():
        return []
    since = int(Path(folder).name) / 1e9
    if now - since <= REVIEW_PENDING:
        return []
    return [Alert(f"review-pending:{Path(folder).name}", f"review {Path(folder).name} has been pending since "
                  f"{when(since, now)} (pid {record.get('pid')}, {record.get('launches', 0)} launches); see "
                  f"{state / 'rule-games' / Path(folder).name / 'session.stderr'}, "
                  f"{state / 'rule-games' / (Path(folder).name + '.log')} and {state / 'review.json'}")]


# A variant whose game can't be played (a timeout, the engine exiting nonzero) is recorded as a game_error row and
# skipped, so a referee or host problem that breaks every game would finish the sweep with nothing scored. Alerts when
# more than SWEEP_ERROR_SHARE of its finished variant passes, or the last SWEEP_ERROR_RUN, are game errors.
def sweep_errors(folder: Path) -> list[Alert]:
    finished: dict[tuple, str | None] = {}  # (variant, pass) -> its game error, in the order they finished
    for row in sweeps.results(folder):
        key = (row.get("variant"), row.get("pass"))
        finished.pop(key, None)
        finished[key] = row.get("game_error")
    errors = [error for error in finished.values() if error]
    last = list(finished.values())[-SWEEP_ERROR_RUN:]
    if len(finished) < SWEEP_ERROR_RUN or not errors or (
            len(errors) <= SWEEP_ERROR_SHARE * len(finished) and not all(last)):
        return []
    return [Alert("sweep:errors", f"{len(errors)} of the variant sweep's {len(finished)} finished variant passes in "
                  f"{folder.name[:7]} had a game that couldn't be played (the last {len(last)}: "
                  f"{sum(map(bool, last))} of them), the first: {errors[0][:200]}; the referee or a host may be broken; "
                  f"see {folder / 'results.jsonl'} and {folder / 'run.log'}")]


# A partial Discord read (tools/discord_read.py exit 3: a channel or thread failed, or it ran out of time) counts as
# fetched, so the upkeep check above stays quiet; DISCORD_PARTIAL_RUNS of them in a row means messages were missed.
# Alerts once a read, within REPEAT of the latest partial one (its fetched_at), not every REPEAT until the next day's.
def discord_partial(state: Path, now: float) -> list[Alert]:
    path = state / "discord.json"
    report = read_json(path)
    runs, last = report.get("partial_runs") or 0, report.get("fetched_at")
    if not isinstance(runs, int) or runs < DISCORD_PARTIAL_RUNS or (
            isinstance(last, (int, float)) and now - last >= REPEAT):
        return []
    at = when(last, now) if isinstance(last, (int, float)) else "at an unknown time"
    reads = f"the last {runs} Discord reads were" if runs > 1 else "the Discord read was"
    return [Alert("discord:partial", f"{reads} partial, the latest {at}: "
                  f"{str(report.get('partial') or '')[:200]}; see {path} and {state / 'discord' / 'latest.md'}")]


TEAMS_AGE = 2 * 24 * 3600  # teams.json refreshes hourly, so two days without a leaderboard read is a broken step
TEAMS_FAILING = 3 * 3600  # a refresh failing this long, with no success since, is more than a blip


# discord/teams.json (loop/discord_teams.py) names each Discord poster's ladder team in latest.md, so the digest can
# weigh a claim by rank: refreshes failing for TEAMS_FAILING, no leaderboard read for TEAMS_AGE, or a file of the
# wrong shape leave those labels stale or missing. Checked while the Discord is read at all (discord.json exists).
def discord_teams(state: Path, now: float) -> list[Alert]:
    import discord_teams as teams
    path = teams.path_in(state)
    if not (state / "discord.json").exists():
        return []
    if not path.exists():
        return [Alert("discord:teams", f"no {path}: the upkeep's Discord teams step has never run, so latest.md "
                      "names no poster's team; see loop/discord_teams.py")]
    report, wrong = teams.load(state)
    if wrong:
        return [Alert("discord:teams-unreadable", f"{path} is unreadable ({wrong}); the next hourly refresh rebuilds "
                      "it, and until then latest.md names no poster's team")]
    alerts, fetched, failing = [], report.get("fetched_at"), report.get("failing_since")
    if report.get("error") and failing is not None and now - failing > TEAMS_FAILING:
        alerts.append(Alert("discord:teams-error", f"Discord teams refreshes failing since {when(failing, now)}, the "
                            f"last: {report['error'][:200]}; see {path}"))
    if fetched is None or now - fetched > TEAMS_AGE:
        last = when(fetched, now) if fetched is not None else "never"
        alerts.append(Alert("discord:teams-old", f"Discord posters' teams last read from the leaderboard {last}, over "
                            f"{TEAMS_AGE / 86400:.0f} days ago: latest.md's team labels and ranks are stale; "
                            f"see {path}"))
    return alerts


# The hand-kept records that label teams by name, the briefing and the digest worker's brief, naming a team by an old
# name (teams.json's `previous`) on a line without its current one: a label copied from a snapshot that went stale
# while the team's id stayed right (Vibing++ -> Cutlery on 2026-09-28, the briefing's "rank 2" too). Only the teams
# whose renames alert (discord_teams.tracked: the top, dev teams, our opponents), since of ~900 teams some old name is
# bound to be a common word ("team", "dragon"), and both names match only as whole words, so a short new name ("ai")
# inside another word hides nothing. Old names under 3 characters are skipped: they match too much text.
def old_team_names(state: Path, repo: Path) -> list[Alert]:
    import discord_teams as teams
    record, alerts = teams.read(state), []
    files = [state / "briefing.md", repo / "loop" / "DISCORD.md"]
    texts = {path: path.read_text(errors="replace").splitlines() for path in files if path.is_file()}
    listed = record.get("teams") or {}
    watched = teams.tracked(state, {}, listed)

    def word(name: str) -> re.Pattern:
        return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")

    for key, team in sorted(listed.items()):
        if key not in watched:
            continue
        current = word(team["name"]) if team["name"] else None
        for old in team.get("previous", []):
            if len(old) < 3 or old == team["name"]:
                continue
            pattern = word(old)
            for path, lines in texts.items():
                hits = [n for n, line in enumerate(lines, 1)
                        if pattern.search(line) and not (current and current.search(line))]
                if hits:
                    alerts.append(Alert(f"teams:old-name:{key}:{path.name}", flat(
                        f"{path} line {', '.join(map(str, hits[:5]))} names team {key} by its old name "
                        f"{teams.quoted_name(old)}; it is now {teams.quoted_name(team['name'])} "
                        f"({teams.place(team)}): relabel it by id and current name ({teams.path_in(state)})")))
    return alerts


# The ladder's active maps (ladder-maps.json, loop/ladder_maps.py) against the main checkout's maps/ladder and
# maps/sprint by content: a map the official-map report and the CPU gate don't play (new, or changed on the server)
# or one they still play (gone), since both play every file there; a pearl-only change, a variant beside a map we
# still match, a map whose pearl timings the site withholds (from 2026-09-27 21:00 AEST), and a local file with no
# spawning tile, each told apart (ladder_maps.differences). Keyed by the differences, so a further change alerts at
# once. Withheld pearl timings alone need nothing done but checking online.POOL_CUTOFF, so they alert once per change,
# not every REPEAT: `seen` (health.json's "ladder pool") keeps the key that alerted. Its staleness is UPKEEP_JOBS'.
def ladder_pool(state: Path, repo: Path, now: float, seen: dict | None = None) -> list[Alert]:
    record = ladder_maps.read(state)
    found = ladder_maps.differences(record, repo)
    if not any(found.values()):
        return []
    parts = "; ".join(f"{kind} {', '.join(names)}" for kind, names in found.items() if names)
    key = "ladder-maps:" + hashlib.sha256(parts.encode()).hexdigest()[:12]
    if seen is not None and set(kind for kind, names in found.items() if names) == {"withheld"}:
        if seen.get("withheld") == key:
            return []
        seen["withheld"] = key
    return [Alert(key, flat(
        f"LADDER MAPS CHANGED: {parts}. The server's active pool (read {when(record.get('fetched_at') or 0, now)}) "
        f"differs from {' and '.join(ladder_maps.LOCAL)} by content (line endings, trailing spaces and MAP_NAME "
        "ignored), and the official-map report and the CPU gate play every file there. Commit a new map's mapText "
        "(ladder.get('/maps')) to maps/ladder; a changed or pearls-changed one's over its existing file (keeping its "
        "name and folder); a variant's as its own file under maps/ladder with a distinct stem (no '~': the "
        "official-map report cuts stems there, driver.official_maps), keeping the file the other map still matches "
        "or the maps/sprint file (the toolkit's Sprint maps, which mapgen's sprint style and the CPU gate play); or move a gone "
        "maps/ladder one out of maps/ (maps/sprint stays: the Sprint's maps). Never commit map text with no spawning "
        "tile (withheld: the site hid its pearl timings; keep the existing file until real timings are known): it "
        "would be played without pearls, and a gapless local file needs its real text back. A pearls-changed or "
        "withheld map means checking online.POOL_CUTOFF against when the change landed (it moves only by hand). "
        "Each commit also changes "
        "mapgen's near maps (loop/test_mapgen_pins.py) and reaches the referee checkout at the next evaluation "
        f"(driver.refresh_referee); see {state / ladder_maps.RECORD}"))]


# CLAUDE.md's sections that state the contest's rules and sig's decisions, which the briefing distils.
RULE_SECTIONS = ("## Confirmed by the organisers", "## Decisions")


# A hash of CLAUDE.md's RULE_SECTIONS (each from its heading to the next "## "), or None when the file has none.
def rules_hash(claude_md: Path) -> str | None:
    kept, keep = [], False
    for line in claude_md.read_text().splitlines():
        if line.startswith("## "):
            keep = line.startswith(RULE_SECTIONS)
        if keep:
            kept.append(line)
    return hashlib.sha256("\n".join(kept).encode()).hexdigest() if kept else None


# briefing.md (the orchestrator's list of what research should assume) must follow what it distils: a digest's
# decisions or a method.md change after it, left an hour; CLAUDE.md's rule sections changing, or a champion promoted,
# after it and left an hour; or a briefing not re-confirmed for a day while the loop runs, is research working from
# stale assumptions. Re-confirming is touching it after checking it. `seen` (health.json's "briefing") keeps the rule
# sections' hash as of the briefing's last update, and a new hash with when it was first seen (CLAUDE.md's time then).
def briefing(state: Path, repo: Path, now: float, seen: dict) -> list[Alert]:
    path = state / "briefing.md"
    if not path.exists():
        return [Alert("briefing:missing", f"no briefing for research; write {path} (CLAUDE.md, the briefing)")]
    written, alerts = path.stat().st_mtime, []
    for source in (state / "discord" / "digests.md", state / "method.md"):
        if source.exists() and source.stat().st_mtime > written + BRIEFING_GRACE and now - source.stat().st_mtime > 0:
            alerts.append(Alert(f"briefing:{source.name}", f"{source} changed {when(source.stat().st_mtime, now)}, after "
                                f"the briefing ({when(written, now)}): fold what it changed into {path}, or touch it"))
    claude_md = repo / "CLAUDE.md"
    if claude_md.exists():
        current = rules_hash(claude_md)
        if current is None:
            alerts.append(Alert("briefing:no-rules", f"{claude_md} has no {' or '.join(RULE_SECTIONS)} section, so "
                                "nothing tells the briefing when the rules change; see health.RULE_SECTIONS"))
        elif seen.setdefault("rules", current) != current:
            if seen.get("changed", {}).get("hash") != current:
                seen["changed"] = {"hash": current, "at": min(claude_md.stat().st_mtime, now)}
            changed = seen["changed"]["at"]
            if written >= changed:
                seen["rules"] = current
                seen.pop("changed")
            elif now - changed > BRIEFING_GRACE:
                alerts.append(Alert(f"briefing:rules:{current[:12]}", f"{claude_md}'s {' / '.join(RULE_SECTIONS)} "
                                    f"sections changed {when(changed, now)}, after the briefing ({when(written, now)}): "
                                    f"fold the change into {path}, or touch it"))
        else:
            seen.pop("changed", None)
    if (state / "champion").exists():
        promoted = moved_at(repo, CHAMPION_BRANCH)
        if promoted > written and now - promoted > BRIEFING_GRACE:
            champion = git(repo, "rev-parse", "--short=12", CHAMPION_BRANCH)
            alerts.append(Alert(f"briefing:champion:{champion}", f"champion {champion[:7]} promoted {when(promoted, now)},"
                                f" after the briefing ({when(written, now)}): add what it showed works to {path} "
                                f"(from {state / 'results.jsonl'}), or touch it"))
    if now - written > BRIEFING_CONFIRM:
        alerts.append(Alert("briefing:old", f"{path} not re-confirmed since {when(written, now)}: check each line against "
                            "its source and update or touch it"))
    return alerts


# Dated reminders, so a promise ("check the first sweep at 09:00") lives in a file rather than the orchestrator's
# memory: <loop home>/reminders.jsonl, one {"due": ISO time, "text": ...} per line, added with --remind. Each due one
# not yet done is an alert; run() marks it done ("done_at") once its alert is written, so it alerts once. An
# unreadable line alerts until it is fixed. While the usage pause holds (loop/quota.py) none alerts: each fires at the
# first check after the reset.
def reminders(state: Path, now: float) -> list[Alert]:
    path = state / "reminders.jsonl"
    if not path.exists() or quota.paused(state, now):
        return []
    alerts = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            due = datetime.fromisoformat(row["due"]).timestamp()
            text = str(row["text"])
        except (ValueError, KeyError, TypeError) as error:
            alerts.append(Alert(f"reminder:bad:{hashlib.sha256(line.encode()).hexdigest()[:12]}",
                                flat(f"{path} line {number} is no reminder ({type(error).__name__}: {error}); "
                                     "fix or delete it")))
            continue
        if due <= now and not row.get("done_at"):
            alerts.append(Alert(reminder_key(row), flat(f"reminder due {when(due, now)}: {text}")))
    return alerts


# `text` on one line: newlines and other control characters (a hand-written reminder, an echoed bad value) become
# spaces, so no alert can start a forged line in host-alerts.log.
def flat(text: str) -> str:
    return " ".join(CONTROL.sub(" ", text).split())


CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")


def reminder_key(row: dict) -> str:
    return "reminder:" + hashlib.sha256(f"{row['due']}\n{row['text']}".encode()).hexdigest()[:12]


# Marks the reminders whose alerts `keys` names done, rewriting reminders.jsonl in place (same lines, same order).
def mark_reminders(state: Path, keys: set[str], now: float) -> None:
    path = state / "reminders.jsonl"
    lines = path.read_text().splitlines()
    for k, line in enumerate(lines):
        try:
            row = json.loads(line)
            if reminder_key(row) in keys and not row.get("done_at"):
                lines[k] = json.dumps({**row, "done_at": now}, ensure_ascii=False)
        except (ValueError, KeyError, TypeError):
            continue
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("".join(line + "\n" for line in lines))
    temporary.replace(path)


# Appends a reminder due at `due` (ISO; without an offset it is local time), under run()'s lock. Returns its row.
def remind(state: Path, due: str, text: str) -> dict:
    moment = datetime.fromisoformat(due)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    if not text.strip():
        raise ValueError("a reminder needs text")
    row = {"due": moment.isoformat(timespec="minutes"), "text": " ".join(text.split())}
    with locked(state / "health"):
        with (state / "reminders.jsonl").open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# A wake at a usage reset (REFLECT 30 Sep 0311 F2: an unattended session had its 03:01 wake set by hand): a reminder
# due quota.RESET_MARGIN_S after `at`, when a provider's usage limit resets (driver.wait_out_limit), unless a "USAGE"
# reminder not yet done is due within USAGE_LATE of it already (the status line's for its window, or another turn's
# for the same limit). Returns the row added, or None.
def wake(state: Path, at: float, text: str) -> dict | None:
    due = at + quota.RESET_MARGIN_S
    path = state / "reminders.jsonl"
    with locked(state / "health"):
        for line in (path.read_text().splitlines() if path.exists() else []):
            try:
                row = json.loads(line)
                if (not row.get("done_at") and str(row["text"]).startswith("USAGE")
                        and abs(datetime.fromisoformat(row["due"]).timestamp() - due) <= USAGE_LATE):
                    return None
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        row = {"due": datetime.fromtimestamp(due).astimezone().isoformat(timespec="minutes"),
               "text": " ".join(f"USAGE {text}".split())}
        with path.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# An SPRT playing slowly for its map mix (MISSES 35; REFLECT 2026-09-30 15:13 F3: at 13:28 the orchestrator called 58
# games/min "sped up" without the morning's 200-246, then blamed the big-map mix, while coders' analysis loaded this
# Mac to 71-82): the running SPRT's pace, its games a minute times its maps' mean size in tiles (a game's cost grows
# with its map), under PACE_SHARE of the median pace of the last PACE_HISTORY finished SPRTs for PACE_HOLD, once it
# has run PACE_AFTER. A run's pace is its games played (reused baselines not counted) over the time from its
# sprt-progress.json's first write to its last; the referee's progress file gives the games and mean tiles (sprt.py
# Standing), and an older run's are worked out from its sprt.json (its trials by opponent, less baselines_reused; the
# mean size of its per_map files), kept in `seen` by run and file time since sprt.json runs to 2 MB. Quiet with fewer
# than PACE_MIN runs to compare. `seen` also holds when the slow spell began.
PACE_SHARE = 0.5
PACE_HISTORY = 10
PACE_MIN = 3
PACE_AFTER = 1200
PACE_HOLD = 600


# (games, mean tiles) of the SPRT in `run`, from its progress file, else its sprt.json; None when unknown.
def sprt_work(run: Path, progress: dict) -> tuple[int, float] | None:
    if isinstance(progress.get("games"), int) and isinstance(progress.get("tiles"), (int, float)):
        return progress["games"], progress["tiles"]
    report = read_json(run / "sprt.json")
    pairs, per_map = report.get("pairs"), report.get("per_map")
    if not isinstance(pairs, int) or not pairs or not isinstance(per_map, dict):
        return None
    rotation = report.get("rotation") or ["champion"]
    names = report.get("schedule") or [rotation[i % len(rotation)] for i in range(pairs)]
    games = sum(2 if name == "champion" else 4 for name in names[:pairs]) - (report.get("baselines_reused") or 0)
    import driver  # lazily: driver imports this module
    sizes = [tiles for path in per_map if (tiles := driver.map_area(Path(path))) is not None]
    return (games, sum(sizes) / len(sizes)) if sizes and games > 0 else None


# Tile-games a minute of the SPRT in `run` with progress file `path`, or None.
def sprt_pace(run: Path, path: Path, progress: dict) -> float | None:
    minutes = (path.stat().st_mtime - throughput.created(path)) / 60
    work = sprt_work(run, progress)
    return work[0] * work[1] / minutes if work and minutes > 1 else None


def slow_sprt(state: Path, now: float, seen: dict) -> list[Alert]:
    paces = seen.setdefault("paces", {})
    done, live = [], None
    for path in sorted((state / "runs").glob("E*/sprt-progress.json"), key=lambda p: p.stat().st_mtime):
        run = path.parent
        if not (run / "sprt.json").exists() or read_json(path).get("updated", 0) > (run / "sprt.json").stat().st_mtime:
            live = path if now - path.stat().st_mtime < RECENT_SPRT else live
        else:
            done.append(path)
    history = []
    for path in done[-PACE_HISTORY:]:
        key = f"{path.parent.name}:{path.stat().st_mtime:.0f}"
        if key not in paces:
            paces[key] = sprt_pace(path.parent, path, read_json(path))
        if paces[key] is not None:
            history.append((path.parent.name, paces[key]))
    kept = {f"{p.parent.name}:{p.stat().st_mtime:.0f}" for p in done[-PACE_HISTORY:]}
    seen["paces"] = {k: v for k, v in paces.items() if k in kept}
    progress = read_json(live) if live else {}
    pace = sprt_pace(live.parent, live, progress) if live and isinstance(progress.get("games"), int) else None
    if len(history) < PACE_MIN or pace is None or now - throughput.created(live) < PACE_AFTER:
        seen.pop("slow since", None)
        return []
    median = sorted(v for _, v in history)[len(history) // 2]
    if pace >= PACE_SHARE * median:
        seen.pop("slow since", None)
        return []
    since = seen.setdefault("slow since", now)
    if now - since < PACE_HOLD:
        return []
    minutes = (live.stat().st_mtime - throughput.created(live)) / 60
    return [Alert(f"slow sprt:{live.parent.name}", f"SLOW SPRT: {live.parent.name} plays {progress['games'] / minutes:.0f} "
                  f"games/min on maps of {progress['tiles']:.0f} tiles on average, {pace / 1000:.0f}k tile-games/min, "
                  f"under {PACE_SHARE:.0%} of the median {median / 1000:.0f}k of the last {len(history)} SPRTs ("
                  + ", ".join(f"{name} {v / 1000:.0f}k" for name, v in history) + f") since {when(since, now)}; find "
                  f"why (hosts, this Mac's load, throughput's utilisation rows) and quote this baseline in any 'sped up' "
                  f"or 'not a fault' claim; see {live}")]


# Game machines left idle while they were needed (MISSES 35): a stretch of utilisation.jsonl's minute rows in the last
# REPEAT seconds where an evaluation ran or a candidate waited and at least throughput.IDLE_SHARE of the capacity sat
# idle on average over throughput.IDLE_MINUTES or more (throughput.idle_spells), one alert per cause (the experiment and its
# stage, or the waiting), which in a stage the fillers may play in (marked local, or none with a candidate waiting)
# says what each filler is doing now (fillers_now, given `repo`); and the record itself going unwritten while the upkeep
# runs (its sampler thread, throughput.sampling, writes a row a minute).
def utilisation(state: Path, now: float, repo: Path | None = None) -> list[Alert]:
    path = state / throughput.LOG
    alerts = {}
    fillers = None  # fillers_now, read once for the first spell in a stage the fillers may play in
    for spell in throughput.idle_spells(throughput.read_rows(state, since=now - REPEAT)):
        evaluation = spell["evaluation"]
        during = (f"while {evaluation.get('experiment') or 'an evaluation'}'s {evaluation.get('stage')} ran"
                  if evaluation else f"while {', '.join(spell['waiting'])} waited for an evaluation")
        if evaluation and evaluation.get("local") is True:  # the coders' and fillers' hosts then (driver.host_stage)
            during += " in a stage that plays only on this Mac, with the hosts free for the coders and fillers"
        elif evaluation and evaluation.get("local"):  # its CPU gate: here and on this Mac's platform's hosts
            during += (" in a stage that plays only on this Mac and the hosts of its platform, with the others free "
                       "for the coders and fillers")
        # Where the fillers may play (a local stage, or no evaluation with a candidate waiting): what each is doing
        # now, as idle_any says, so the line tells a filler that should have played from one with nothing to play.
        now_fillers = ""
        if repo is not None and (not evaluation or evaluation.get("local")):
            if fillers is None:
                try:  # a context line: its failure must not cost the idle alert or the stale-record one below
                    now_text, could, _ = fillers_now(state, repo, git(repo, "rev-parse", CHAMPION_BRANCH), now)
                    fillers = f"; fillers now: {now_text}.{could}"
                except Exception as error:
                    fillers = f"; fillers now: unreadable ({type(error).__name__}: {error})"
            now_fillers = fillers
        counted = ("estimated from games finished, which misses a run's last games when another replaces it"
                   if spell["estimated"] else "the referee's in-flight counts")
        idle_hosts = ", ".join(spell["idle_hosts"]) or "none wholly idle"
        span = f"{time.strftime('%H:%M', time.localtime(spell['start']))}-{time.strftime('%H:%M', time.localtime(spell['end']))}"
        alerts[f"idle:{spell['cause']}"] = Alert(
            f"idle:{spell['cause']}", f"game machines {spell['idle']:.0%} idle on average over {spell['minutes']} min ({span}) "
            f"{during}: {spell['busy']:.0f} of {spell['capacity']} slots busy on average ({counted}); idle hosts: "
            f"{idle_hosts}{'; unreadable status files: ' + ', '.join(spell['unreadable']) if spell['unreadable'] else ''}"
            f"{now_fillers}; find the stage's cause and fix it; see {path}")
    maintained = read_json(state / "maintenance.json").get("checked_at")
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists()
    last = throughput.last_row(path)
    written = last.get("at") if last else None
    if (not stopped and isinstance(maintained, (int, float)) and now - maintained < UPKEEP_ALIVE
            and (not isinstance(written, (int, float)) or now - written > UTILISATION_STALE)):
        seen = f"last row {when(written, now)}" if isinstance(written, (int, float)) else "no rows at all"
        alerts["utilisation:stale"] = Alert("utilisation:stale", f"the utilisation record isn't being written while "
                                            f"the upkeep runs ({seen}); its sampler thread (throughput.sampling in "
                                            f"maintenance.checking and native.py watch) has stopped or keeps failing "
                                            f"(its errors go to the driver's or upkeep.log's output); see {path}")
    return list(alerts.values())


UNMARKED_MINUTES = 3  # rows in a row with an evaluation's own remote games in a stage marked local (local_stages)
LOCAL_PROOF_MINUTES = 30  # local-stage minutes in the record by which the hosts must have played something else
# A row's purposes (throughput.status_files) by the native.host_share role their games yield with (a queued
# candidate's prepared CPU gate its lane's coder share, native.ration_hosts); a tune and the evidence refresh split one
# share, the late screen, the official-map reports and the native judge's gate one filler
# share, the idle fillers theirs. A purpose not named here isn't judged.
SHARE_ROLES = (("coder ", ""), ("prepared ", ""), ("review rule counts", ""), ("tune ", "tune"),
               ("evidence refresh", "tune"),
               ("official report ", "screen"), ("late screen", "screen"), ("native gate", "screen"),
               ("sweep", "idle"), ("prewarm", "idle"), ("adversarial maps", "idle"))


# The groups of `row`'s games on remote hosts above what they may play there beside an evaluation playing on the
# hosts (native.host_share with `evaluating`: each coder's own share, the most any one may have where they ration the
# half, the tune players' one share together, the screen fillers' one share together, the idle fillers' none; and the
# coders and the review together their rationed part of the half, native.coders_share, which two coders on one lane
# broke), as "host: purpose n > share". A host stage holds them to it
# (driver.host_stage), so a local stage's row showing one, a minute or more into the lock (a game takes ~11 s, so what
# the lock found in flight has ended), shows the new local stages at work.
def above_evaluation_share(row: dict) -> list[str]:
    import native  # lazily: native imports the driver, which imports this module
    found = []
    for name, host in row["hosts"].items():
        cores = (row.get("cores") or {}).get(name) or host.get("capacity") or 0
        if name == "here" or not cores:
            continue
        groups: dict[tuple[str, str], float] = {}
        for purpose, n in (host.get("by") or {}).items():
            role = next((r for prefix, r in SHARE_ROLES if purpose.startswith(prefix)), None)
            if role is None:
                continue
            key = (role, purpose if not role else f"{role} {'players' if role == 'tune' else 'fillers'}")  # one share
            groups[key] = groups.get(key, 0) + n
        found += [f"{name}: {purpose} {n:g} > {share}" for (role, purpose), n in groups.items()
                  if n > (share := native.host_share(cores, True, role))]
        if (coders := sum(n for (role, _), n in groups.items() if not role)) > (most := native.coders_share(cores)):
            found.append(f"{name}: coders and review {coders:g} > {most}")
    return found


SHARE_MINUTES = 3  # rows in a row with the same host's players above their share beside a host stage (over_share)
SHARE_WINDOW = 3600  # the rows over_share judges: the last hour's, judged by the shares the code gives now
DRAIN_S = 600  # the longest a remote game plays: referee/run.py's REMOTE_TIMEOUT_S, twice for a check game


# `row` with each remote purpose's games counted only above the fewest it had in any row since its host stage began
# (`fewest`, {(host, purpose): games}, the stage's first row's counts, lowered here): a game started before the stage
# can only end, so those are the fewest games it may still have in flight then, and a purpose the first row lacks
# counts in full. A lower bound on the games started since: a player that replaces each of its earlier games as it
# ends is missed until DRAIN_S.
def started_since(row: dict, fewest: dict[tuple[str, str], float]) -> dict:
    now = {(name, purpose): n for name, host in row["hosts"].items() for purpose, n in (host.get("by") or {}).items()}
    for key in fewest:
        fewest[key] = min(fewest[key], now.get(key, 0))
    return {**row, "hosts": {name: {**host, "by": {p: n - fewest.get((name, p), 0)
                                                   for p, n in (host.get("by") or {}).items()}}
                             for name, host in row["hosts"].items()}}


# An alert per host where the players beside an evaluation's host stage (the marker's "hosts" true: rows whose
# evaluation isn't "local", nor its CPU gates' MAC_HOSTS, a minute or two on two hosts) played above their evaluation
# share (above_evaluation_share) for SHARE_MINUTES rows in a row within the last SHARE_WINDOW: no share allows it, so it
# shows a player the shares miss, as two coders on one lane were (a post-mortem's coder beside the new claim,
# 2026-09-27). For DRAIN_S from a stage's first row (seen in the rows read: the row before it, within GAP_S, not a
# host stage of any experiment, since games in flight then already kept its shares) only the games started since count (started_since): a niced filler game started before
# E0346's SPRT played 234 s on ll beside it, 18:31-18:34 on 29 Sep, which the old rule (past a stage's first minute a
# game has ended) took for a player the shares miss. The DRAIN_S before the window are read only to find where a stage
# began: read from the window's edge, a stage begun just before it had no first row, so the games in flight at its start
# counted in full (E0376's at 04:51, E0384's at 07:11 and E0395's at 08:55 on 30 Sep each alerted exactly an hour
# later, on 1-5 sweep games that were ending; the waiting sweep itself, sweep.wait_idle, plays nothing).
def over_share(state: Path, now: float) -> list[Alert]:
    alerts, streaks = {}, {}  # host: (first row's time, last row's time, rows)
    began, fewest, before, last_at = None, {}, None, None  # the stage's first row's time, started_since's, the last row's
    for row in throughput.read_rows(state, since=now - SHARE_WINDOW - DRAIN_S):
        evaluation = row.get("evaluation") or {}
        stage = bool(evaluation) and not evaluation.get("local")  # host stages back to back are one stage
        close = last_at is not None and row["at"] - last_at <= throughput.GAP_S
        if stage is None or not close:
            began = None  # none, or begun before the rows read or a gap in them: every game counts
        elif not before:
            began = row["at"]
            fewest = {(name, purpose): n for name, host in row["hosts"].items()
                      for purpose, n in (host.get("by") or {}).items()}
        before, last_at = stage, row["at"]
        judged = started_since(row, fewest) if began is not None and row["at"] - began <= DRAIN_S else row
        lines: dict[str, list[str]] = {}
        for line in above_evaluation_share(judged) if stage and row["at"] >= now - SHARE_WINDOW else []:
            lines.setdefault(line.split(":", 1)[0], []).append(line)
        streaks = {name: (s[0], row["at"], s[2] + 1)
                   if (s := streaks.get(name)) and row["at"] - s[1] <= throughput.GAP_S else (row["at"], row["at"], 1)
                   for name in lines}
        for name, (first, _, count) in streaks.items():
            if count >= SHARE_MINUTES:
                alerts[f"share:{name}"] = Alert(
                    f"share:{name}", f"the players beside {evaluation.get('experiment') or 'an evaluation'}'s "
                    f"{evaluation.get('stage')} stayed above their share of {name} for {count} min from "
                    f"{time.strftime('%H:%M', time.localtime(first))} (latest: {', '.join(lines[name])}): find the "
                    f"player native.host_share misses; see {state / throughput.LOG}")
    return list(alerts.values())


# The evaluation lock's local stages (the marker's "hosts": false, driver.host_stage), from utilisation.jsonl's rows
# (their evaluation's "local"): an alert when the evaluation's own games played on the remote hosts for
# UNMARKED_MINUTES rows in a row in a stage marked local (a host stage driver.host_stage misses, whose games then get
# no priority over the fillers there); and its first real firing (MISSES 50): once the record holds
# LOCAL_PROOF_MINUTES of local-stage rows, an alert while in none of them, following another local row, did a coder,
# tune or filler play above its evaluation share on a remote host (above_evaluation_share: what no stage under the lock
# allowed before; a coder's or official-map report's one game there always was), until one does (`seen`, health.json's
# "local stages", then keeps it quiet for good).
def local_stages(state: Path, now: float, seen: dict) -> list[Alert]:
    rows = throughput.read_rows(state, since=now - throughput.CAP * throughput.SAMPLE_S)
    alerts, streak, local, previous = {}, [], 0, None
    for row in rows:
        evaluation = row.get("evaluation") or {}
        remote = [h for name, h in row["hosts"].items() if name != "here"]
        own = sum((h.get("by") or {}).get("evaluation", 0) for h in remote)
        if evaluation.get("local") is not True or own < 1:  # a CPU gate's MAC_HOSTS stage plays on hosts by design
            streak = []
        else:
            streak = [*streak, row] if streak and row["at"] - streak[-1]["at"] <= throughput.GAP_S else [row]
        if len(streak) >= UNMARKED_MINUTES:
            key = f"local-stage:hosts:{evaluation.get('experiment')}:{evaluation.get('stage')}"
            alerts[key] = Alert(key, f"{evaluation.get('experiment') or 'the evaluation'}'s {evaluation.get('stage')} "
                                f"played its own games on the remote hosts for "
                                f"{round((streak[-1]['at'] - streak[0]['at']) / 60) + 1} min from "
                                f"{time.strftime('%H:%M', time.localtime(streak[0]['at']))} in a stage marked local "
                                f"(the marker's \"hosts\": false), so the players beside it didn't step back there: "
                                f"wrap that stage in driver.host_stage; see {state / throughput.LOG}")
        if evaluation.get("local") is True:
            local += 1
            follows = previous is not None and ((previous.get("evaluation") or {}).get("local") is True
                                                and row["at"] - previous["at"] <= throughput.GAP_S)
            if follows and not seen.get("proven") and above_evaluation_share(row):
                seen["proven"] = row["at"]
        previous = row
    if local >= LOCAL_PROOF_MINUTES and not seen.get("proven"):
        alerts["local-stage:unused"] = Alert("local-stage:unused", f"{local} min of the evaluation lock's local stages "
                                             f"in {state / throughput.LOG} and no coder, tune or filler above its "
                                             "evaluation share on a remote host in any of them: check that sweep.busy "
                                             "and native.host_share read the marker's \"hosts\" (driver.host_stage)")
    return list(alerts.values())


FILLER_DONE = "done for this champion"


# What a filler's record (sweep.json, baselines.json, advmaps.json) says it is doing for `champion`, e.g. "done",
# "paused 13:56: an evaluation is playing on the hosts" or "failed 2x (latest 13:10): ...". A start clears neither
# `paused` nor `error`, so a record holding both says whichever happened last (paused_at against failed_at).
def filler_state(record: dict, champion: str, now: float) -> str:
    def at(key: str) -> str:
        value = record.get(key)
        return time.strftime("%H:%M", time.localtime(value if isinstance(value, (int, float)) else now))

    def stamp(key: str) -> float:
        value = record.get(key)
        return value if isinstance(value, (int, float)) else 0.0

    if not record:
        return "no record yet"
    if record.get("champion") != champion:
        return f"last ran for {str(record.get('champion'))[:7]}, not this champion"
    if rescreen.meta.alive(record.get("pid")):
        return "running"
    if record.get("finished_at"):
        return FILLER_DONE
    failed = bool((record.get("failures") or 0) and record.get("error"))
    if record.get("paused") and not (failed and stamp("failed_at") > stamp("paused_at")):
        return f"paused {at('paused_at')}: {record['paused']}"
    if failed:
        error = str(record["error"]).strip().splitlines() or [""]
        return f"failed {record['failures']}x (latest {at('failed_at')}): {error[-1][:100]}"
    return "not started" if not record.get("started_at") else "stopped between runs, not finished"


# Why a filler with work left for `champion` isn't running (idle_any, when no filler is), from its own record: its
# state (filler_state) and what its upkeep step (sweep.tick, baselines.tick, advmaps.tick) waits for before starting
# it, `also` naming a wait of that step's own beside idle machines; None when it runs or is done. REFLECT 2026-09-29 14:29 F11: at 13:50 the hosts sat at 0 of 72 with "no filler
# job has work", while the new champion v0066's sweep and adversarial-map search were both still to play.
def filler_block(record: dict, champion: str, now: float, retry_s: float, also: str = "") -> str | None:
    text = filler_state(record, champion, now)
    if text in ("running", FILLER_DONE):
        return None
    if record.get("champion") != champion:
        return f"{text}: its upkeep step hasn't run since this champion arrived (is the upkeep ticking?)"
    if text.startswith("failed"):
        retry = (record.get("failed_at") or now) + retry_s
        return f"{text}; its step retries it from {time.strftime('%H:%M', time.localtime(retry))}"
    if text.startswith("paused"):
        return f"{text}; its step restarts it once that no longer holds"
    idle = record.get("idle_at")
    last = f"last found the machines idle {when(idle, now)}" if isinstance(idle, (int, float)) else \
        "never found the machines idle"
    return f"{text}: its step starts it at the next upkeep tick with the machines idle{also} ({last})"


# The late screen's state for the current champion (rescreen.json, rescreen.missing).
def late_screen_state(state: Path, repo: Path, champion: str) -> str:
    record = rescreen.read(state).get(champion, {})
    if rescreen.meta.alive(record.get("pid")):
        return "running"
    if not (found := rescreen.missing(state, repo)):
        return "nothing owed"
    owed = f"owes {', '.join(found[2])}"
    if (record.get("failures") or 0) and record.get("error"):
        return f"{owed}; failed {record['failures']}x: {str(record['error']).strip()[:100]}"
    return f"{owed}; not running"


IDLE_RECENT = 2 * 3600  # an idle stretch that ended longer ago than this has had its alert


# A row's cause for idle_any: "no evaluation" when no evaluation runs and no candidate waits, else None, so the rows
# health.utilisation looks at count in no stretch here and no stretch is reported by both.
def no_evaluation(row: dict) -> str | None:
    return None if throughput.cause(row) else "no evaluation"


# Game machines idle with no evaluation running and no candidate waiting (the rows health.utilisation doesn't look at):
# each stretch where at least throughput.IDLE_SHARE of the capacity was idle for throughput.IDLE_MINUTES that ended in
# the last IDLE_RECENT, one alert a stretch (keyed by its start; rows are read from 2 x REPEAT back so a long
# stretch's start stays put). health.utilisation looked only while an evaluation ran or a candidate waited, so every
# host sat idle 12:22-12:35 and 13:55-14:06 on 2026-09-27 with no alert; one fixed key would have held 13:55's back
# behind 12:22's for 3 h. Machines idle because everything is played (every filler done for this champion, the late
# screen owing nothing, no official-map report pending, no idea to do or being coded) alert once per champion
# (`seen`, health.json's "idle nothing"), not every stretch. Each line names each filler's state now, the
# official-map reports pending and how many ideas are being coded, and with no filler running, each that could start
# and why it hasn't (filler_block). Quiet under a stop marker.
def idle_any(state: Path, repo: Path, now: float, seen: dict) -> list[Alert]:
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
        return []
    rows = throughput.read_rows(state, since=now - 2 * REPEAT)
    spells = [s for s in throughput.idle_spells(rows, cause_of=no_evaluation) if s["end"] >= now - IDLE_RECENT]
    if not spells:
        return []
    champion = git(repo, "rev-parse", CHAMPION_BRANCH)
    now_text, could, nothing = fillers_now(state, repo, champion, now)
    if nothing:
        if seen.get("champion") == champion:
            return []
        seen["champion"] = champion
        return [Alert(f"idle:nothing:{champion}", f"game machines idle with nothing left to play for champion "
                      f"{champion[:7]}: {now_text}. Queue ideas or a filler for it; this alerts once per champion; "
                      f"see {state / throughput.LOG}")]
    alerts = []
    for spell in spells:
        span = "-".join(time.strftime("%H:%M", time.localtime(spell[k])) for k in ("start", "end"))
        alerts.append(Alert(f"idle:any:{int(spell['start'])}", f"game machines {spell['idle']:.0%} idle for "
                            f"{spell['minutes']} min ({span}) with no evaluation running and no candidate waiting: "
                            f"{spell['busy']:.0f} of {spell['capacity']} slots busy on average; idle hosts: "
                            f"{', '.join(spell['idle_hosts']) or 'none wholly idle'}. Now: {now_text}.{could} "
                            f"Find why nothing played and fix it (a filler that should run, or more to claim); see "
                            f"{state / throughput.LOG}"))
    return alerts


# What idle_any and utilisation's local-stage lines say of the fillers for `champion` now: (each idle filler's state,
# the late screen's, the official-map reports pending and the ideas being coded and to do; with no filler running,
# each that could start and why it hasn't (filler_block), else ""; whether everything is played: every filler done
# for this champion, the late screen owing nothing, no official-map report pending, no idea to do or being coded).
def fillers_now(state: Path, repo: Path, champion: str, now: float) -> tuple[str, str, bool]:
    searched = f" and, past its first {advmaps.GAMES_CAP} games, nothing else wanting them (advmaps.wanted)"
    records = {name: (read_json(state / file), retry, also) for name, file, retry, also in (
        ("sweep", "sweep.json", sweeps.RETRY_S, ""), ("prewarm", "baselines.json", prewarming.RETRY_S, ""),
        ("adversarial maps", "advmaps.json", advmaps.RETRY_S, searched))}
    fillers = {name: filler_state(record, champion, now) for name, (record, _, _) in records.items()}
    blocked = {name: why for name, (record, retry, also) in records.items()
               if (why := filler_block(record, champion, now, retry, also))}
    late = late_screen_state(state, repo, champion)
    pending = official.waiting(state)
    running = [row["id"] for row in pending
               if official.alive(official.read_json(official.folder(state, row["id"]) / "official-job.json").get("pid"))]
    ideas = []
    for line in (state / "ideas.jsonl").read_text().splitlines() if (state / "ideas.jsonl").exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # backlog_rows alerts on it
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("status"), str):
            ideas.append(row)
    import driver  # lazily: the driver imports this module
    coding = driver.coding_count(ideas, state)
    todo = sum(i.get("status") == "todo" for i in ideas)
    now_text = (f"{'; '.join(f'{name} {text}' for name, text in fillers.items())}; late screen {late}; official-map "
                f"reports {len(pending)} pending ({len(running)} running); {coding} ideas being coded, {todo} to do")
    nothing = (all(text == FILLER_DONE for text in fillers.values()) and late == "nothing owed" and not pending
               and not coding and not todo)
    could = "" if "running" in fillers.values() else " No filler is running; could start: " + (
        "; ".join(f"{name} ({why})" for name, why in blocked.items()) or "none, each done for this champion") + "."
    return now_text, could, nothing


# For each upkeep lock (maintenance.tick_record: the Claude drivers' and the native commands'), no tick started or
# ended in UPKEEP_TICK_LATE while none is running and a loop process that runs that upkeep (UPKEEP_RUNNERS) has been
# alive that long. On 29 Sep 13:45-14:00 every driver ran older loop code than on disk, so none ticked and v0066
# waited unpublished (MISSES 91): each owner hands the tick to a stand-in now (maintenance.hand_over), and this alerts
# if that fails too. A tick's normal gap is TICK_EVERY, and up to twice that when its owner exits and the others try
# each minute, so the alert waits until a tick is two intervals overdue. A tick running (its pid alive) past
# UPKEEP_TICK_HUNG alerts too: no record of whole ticks existed to fit it (29 Sep), but the hourly steps' own records
# show them at seconds to minutes (discord.json's last read 136 s, scout.json 38 s, online.json's cycle 54 s,
# maintenance.json's check 8 s), and the slowest step's cap is the daily Discord read's kill (maintenance.DISCORD_KILL,
# below this), so an hour is a hung tick, not a slow one. One alert a stall (its key names the lock and the tick);
# quiet under a stop marker, when the ticks stop, and before the first record.
UPKEEP_TICK_LATE = 3 * maintenance.TICK_EVERY
UPKEEP_TICK_HUNG = 3600
# What runs each provider's upkeep: a Claude driver, or a native session's claim/evaluate/resume. Coders' own
# `native.py games`/`play`/`watch` runs never tick it (1 Oct: nine measurement runs stuck since an outage read as a
# native loop and raised a false "no upkeep tick" alert).
UPKEEP_RUNNERS = {True: re.compile(r"\bloop/driver\.py\b"),
                  False: re.compile(r"\bloop/native\.py\s+(?:claim|evaluate|resume|quick|abandon)\b")}


def upkeep_ticks(state: Path, now: float, rows: list[PsRow] | None = None) -> list[Alert]:
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
        return []
    alerts = []
    for models, runners in UPKEEP_RUNNERS.items():
        path = state / maintenance.tick_record(models)
        record = read_json(path)
        started, ended = record.get("started"), record.get("ended")
        if not isinstance(started, (int, float)):
            continue
        if not isinstance(ended, (int, float)) and meta_alive(record.get("pid")):  # a tick is running
            if now - started > UPKEEP_TICK_HUNG:
                alerts.append(Alert(f"upkeep ticks:{path.name}:{int(started)}",
                                    f"the upkeep tick in pid {record['pid']} has run since {when(started, now)} "
                                    f"(over {UPKEEP_TICK_HUNG} s): publishing, online games, Discord reads and every "
                                    f"other upkeep step wait behind it; find the step it is in (its children, or "
                                    f"'maintenance:' lines in {state / maintenance.STAND_IN_LOG} or the drivers' "
                                    f"logs) and see {path}"))
            continue
        last = max(started, ended if isinstance(ended, (int, float)) else started)
        if now - last <= UPKEEP_TICK_LATE:
            continue
        rows = ps_rows() if rows is None else rows
        if not any(runners.search(row.args) and row.alive > UPKEEP_TICK_LATE for row in rows):
            continue  # no loop running: the upkeep never starts itself
        alerts.append(Alert(f"upkeep ticks:{path.name}:{int(last)}",
                            f"no upkeep tick since {when(last, now)} though a loop process runs (the tick is due "
                            f"every {maintenance.TICK_EVERY} s): publishing, online games, Discord reads and every "
                            f"other upkeep step wait; look for 'maintenance:' lines in the drivers' logs and "
                            f"{state / maintenance.STAND_IN_LOG} (a stand-in that fails to start, "
                            f"maintenance.hand_over) and see {path}"))
    return alerts


# The evaluator, the one stage that limits experiments an hour, idle over EVALUATOR_IDLE: the utilisation record's
# newest rows, unbroken (no gap over EVALUATOR_GAP) and fresh, show no evaluation and no candidate waiting. idle_any
# counts host slots only, so while fillers kept the hosts busy the evaluator sat idle 11:22-11:42 and 12:22-12:34 on
# 29 Sep with no alert until sig asked (REFLECT 2026-09-29 12:53 F1). One alert a stretch, naming each idea in flight
# with its coder's age and status, and the remedies that cut no coder's measurement short: a ready re-evaluation,
# retest (a judge's confirmation among them) or quick fix to claim first (driver.is_filler, `reevaluate`), else more
# coding slots (driver.CODING). The details are read once a stretch (`seen`, health.json's "evaluator idle").
EVALUATOR_IDLE = 10 * 60
EVALUATOR_GAP = 3 * 60
EVALUATOR_WINDOW = 3 * 3600  # the utilisation rows read; a longer stretch keeps its first start (`seen`)


def evaluator_idle(state: Path, now: float, seen: dict) -> list[Alert]:
    if (state / "STOP").exists() or (state / "ASTRA_STOP").exists() or quota.paused(state, now):  # nothing is coded
        return []
    stretch, edge = [], True  # edge: the stretch reaches back to the oldest row read (EVALUATOR_WINDOW)
    for row in reversed([row for row in throughput.read_rows(state, since=now - EVALUATOR_WINDOW) if row["at"] <= now]):
        if row.get("evaluation") or row.get("waiting") or stretch and stretch[-1]["at"] - row["at"] > EVALUATOR_GAP:
            edge = False
            break
        stretch.append(row)
    if not stretch or now - stretch[0]["at"] > EVALUATOR_GAP or now - stretch[-1]["at"] < EVALUATOR_IDLE:
        seen.clear()
        return []
    start = stretch[-1]["at"]
    if edge and isinstance(seen.get("start"), (int, float)) and seen["start"] <= start:
        start = seen["start"]  # idle longer than the rows read: the stretch keeps the start it was alerted with
    if seen.get("start") != start:
        import driver  # lazily: the driver imports this module
        ideas = backlog(state)
        trying = {i["id"]: i for i in ideas if i["status"].startswith("trying:")}
        tries = meta.newest_tries(state, trying)
        flight = []
        for idea in sorted(trying):
            exp, row, worker = tries.get(idea, ("no experiment yet", {}, None))
            began = row.get("started")
            age = f"{(now - began) / 60:.0f} min" if isinstance(began, (int, float)) else "age unknown"
            flight.append(f"{idea} {exp} {(worker or {}).get('status') or 'claimed'} {age}")
        ready = sorted((i for i in driver.ready_ideas(ideas) if i.get("reevaluate")
                        or driver.is_filler(i) and i.get("kind") != "tune"), key=driver.claim_order)
        remedies = "; ".join(f"{i['id']} ({i.get('category')}, priority {i.get('priority')}): {i['title'][:60]}"
                             for i in ready[:5])
        seen.update(start=start, text=flat(
            f"EVALUATOR IDLE since {when(start, now)}: no evaluation running and no candidate waiting, whatever the "
            f"hosts play. In flight: {', '.join(flight) or 'nothing'}. Remedies that cut no coder short: claim first "
            f"(driver.mark_idea next) {remedies or 'none ready (no re-evaluation, retest or quick fix to do)'}; else "
            f"raise driver.CODING. Status lines showing it carry the action taken"))
    return [Alert(f"evaluator idle:{int(start)}", seen["text"])]


LANES_AFTER = 5 * 60  # fewer experiments in flight than the pipeline holds this long, with a claim open, alerts ...
LANES_REPEAT = 30 * 60  # ... and again this often while the spell lasts (lanes_short)


# The backlog's rows with a string id and status; a line that isn't one is skipped (backlog_rows alerts on it).
def backlog(state: Path) -> list[dict]:
    path = state / "ideas.jsonl"
    rows = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("status"), str):
            rows.append(row)
    return rows


# The waiting ideas (status "todo") outgrowing what the loop evaluates (REFLECT 2026-09-29 17:32 F6): over the last
# BACKLOG_DAY the waiting count grew by at least that day's recorded results while results an hour didn't rise from
# the day before, so each day adds more than a day's evaluations to the wait and nothing is catching up. ideas.jsonl
# keeps no count over time (ideas-writes.log counts all rows, not waiting ones), so `history` (health.json's
# "backlog", [time, waiting] at most every BACKLOG_SAMPLE_S, BACKLOG_KEEP_S kept: under 100 pairs) is its own, and it
# stays quiet until it holds a day. One alert a day. Results come from results.jsonl's tail (result_times).
BACKLOG_DAY = 24 * 3600
BACKLOG_SAMPLE_S = 1800
BACKLOG_KEEP_S = 2 * BACKLOG_DAY


def backlog_growth(state: Path, now: float, history: list) -> list[Alert]:
    waiting = sum(row["status"] == "todo" for row in backlog(state))
    if not history or now - history[-1][0] >= BACKLOG_SAMPLE_S:
        history.append([now, waiting])
    history[:] = [row for row in history if now - row[0] <= BACKLOG_KEEP_S]
    then = [row for row in history if now - row[0] >= BACKLOG_DAY]
    if not then:
        return []
    at, before = then[-1]
    times = result_times(state, at - BACKLOG_DAY)
    recent = sum(t >= at for t in times)
    earlier = sum(at - BACKLOG_DAY <= t < at for t in times)
    grew, hours = waiting - before, (now - at) / 3600
    if grew < max(recent, 1) or recent / hours > earlier / 24:
        return []
    return [Alert(f"backlog growth:{int(now // BACKLOG_DAY)}", flat(
        f"BACKLOG GROWTH: {waiting} ideas wait, {grew:+d} since {when(at, now)} ({before} then), while results "
        f"recorded were {recent} in those {hours:.0f} h ({recent / hours:.1f} an hour) against {earlier} in the day "
        f"before ({earlier / 24:.1f} an hour): each day adds more than a day's evaluations to the wait. Drop or merge "
        f"stale and duplicate ideas (loop/backlog.py's audit), or find what limits evaluations an hour (LOOP.md: "
        f"Throughput); see {state / 'ideas.jsonl'}"))]


# Test modules that passed only when tools/merge.sh reran them alone (<loop home>/merge-reruns.jsonl, a row each:
# time, module, branch; merge.sh keeps the last 200), MERGE_RERUNS or more in the last day: an order- or
# load-dependent test is recurring, not a one-off (REFLECT 2026-09-29 17:32 F20). Names each module with its count
# and branches. One alert a day.
MERGE_RERUNS = 2


def merge_reruns(state: Path, now: float) -> list[Alert]:
    path = state / "merge-reruns.jsonl"
    rows = [row for row in official.json_rows(path.read_text().splitlines()) if isinstance(row, dict)
            and isinstance(row.get("at"), (int, float)) and now - row["at"] <= BACKLOG_DAY] if path.exists() else []
    if len(rows) < MERGE_RERUNS:
        return []
    modules = Counter(str(row.get("module")) for row in rows)
    named = "; ".join(f"{module} x{n} ({', '.join(sorted({str(r.get('branch')) for r in rows if r.get('module') == module}))})"
                      for module, n in modules.most_common())
    return [Alert(f"merge reruns:{int(now // BACKLOG_DAY)}", flat(
        f"MERGE RERUNS: {len(rows)} test modules passed only when tools/merge.sh reran them alone in the last day: "
        f"{named}. Each is an order- or load-dependent test: fix it, or its next failure blocks a merge; see {path}"))]


# The `finished` times of results.jsonl's rows recorded since `since`, newest first, read from the file's end
# (a row is 0.5-1 MB, 270 MB in all on 29 Sep, and `finished` is its last field) without parsing the rows; kept while
# the file's size and mtime stay the same.
_result_times: tuple = (None, [])


def result_times(state: Path, since: float) -> list[float]:
    global _result_times
    import mmap
    path = state / "results.jsonl"
    try:
        info = path.stat()
    except OSError:
        return []
    key = (str(path), info.st_size, info.st_mtime_ns, since // 3600)
    if _result_times[0] == key:
        return _result_times[1]
    found = []
    if info.st_size:
        with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            end = len(data)
            while end > 0:
                start = data.rfind(b"\n", 0, end - 1) + 1
                match = re.search(rb'"finished": ([0-9.]+)', data[max(start, end - 400):end])
                if match:
                    if float(match.group(1)) < since:
                        break
                    found.append(float(match.group(1)))
                end = start - 1 if start else 0
    _result_times = (key, found)
    return found


# An idea marked next (the orchestrator's "go first") that no claim can take (not in driver.ready_ideas) for over
# NEXT_STUCK_S, with why: a followup, which is never claimed, only its retest, with none waiting
# (driver.retest_next_followups queues one; 29 Sep: I848, I446 and I903 sat so), or a todo idea driver.held_back
# holds, with its reasons (prerequisites still waiting, a map style or opponent mix the referee lacks). Waiting ideas
# only: a next flag left on a tried or kept idea is stale, not stuck. `seen` (health.json's "next stuck") keeps when
# each was first found so.
NEXT_STUCK_S = 3600


def next_stuck(state: Path, now: float, seen: dict) -> list[Alert]:
    import driver  # lazily: the driver imports this module
    ideas = backlog(state)
    stuck = {idea["id"]: "; ".join(why) for idea, why in driver.held_back(ideas) if why and idea.get("next")}
    for idea in ideas:
        if idea.get("next") and idea["status"] == "followup" and not meta.open_retests(idea, ideas):
            stuck[idea["id"]] = "a followup, never claimed itself, with no retest waiting"  # else its retest is claimed
    for key in [k for k in seen if k not in stuck]:
        seen.pop(key)
    late = {name: why for name, why in stuck.items() if now - held_since(seen, name, True, now) > NEXT_STUCK_S}
    if not late:
        return []
    named = "; ".join(f"{name} ({why}, since {when(seen[name], now)})" for name, why in sorted(late.items()))
    return [Alert(f"next stuck:{','.join(sorted(late))}", flat(
        f"NEXT STUCK: {len(late)} ideas marked next can't be claimed: {named}. A followup's retest is queued by "
        f"driver.retest_next_followups at the next claim; otherwise clear what holds it or drop the flag; see "
        f"{state / 'ideas.jsonl'}"))]


# The 5-hour usage levels (loop/quota.py; sig, 30 Sep: "so it can turn on and off and on automatically"), checked at
# every firing, the first included: the marker tools/usage_statusline.py writes (usage-level.json) names the level and
# window of the record it writes beside it (usage-share.json's rank and resets_at) and began no later than it; it is
# gone within USAGE_LATE of its reset (the loop already reads a passed one as none, but a status line that stopped
# running would leave it); USAGE_LATE into a pause no coder still runs (driver.pausable stops them); and no coder is
# still "paused" USAGE_LATE after the reset its pause named (driver.unpause resumes them). And the record itself is
# fresh while a coder runs (REFLECT 2026-09-30 09:12 F11): the status line writes it only while a Claude session in the
# repo draws one, so with none open for USAGE_SILENT the loop's coders spend usage no level can pause.
USAGE_LATE = 600
USAGE_SILENT = 1800


def usage_level(state: Path, now: float) -> list[Alert]:
    marker, share = read_json(state / quota.LEVEL), read_json(state / "usage-share.json")
    alerts = []
    if marker:
        rank, resets, since = marker.get("rank"), marker.get("resets_at"), marker.get("since")
        want = min(share.get("rank") or 0, quota.PAUSE)
        if (rank != want or not isinstance(resets, (int, float)) or not isinstance(share.get("resets_at"), (int, float))
                or abs(resets - share["resets_at"]) > USAGE_LATE or not isinstance(since, (int, float))
                or since > (share.get("at") or 0)):
            alerts.append(Alert("usage:mismatch", flat(
                f"USAGE marker {state / quota.LEVEL} ({marker}) doesn't match the status line's record "
                f"{state / 'usage-share.json'} (rank {share.get('rank')}, resets_at {share.get('resets_at')}, at "
                f"{share.get('at')}): tools/usage_statusline.py writes both; find out why")))
        elif now - resets > USAGE_LATE:
            alerts.append(Alert(f"usage:stale:{int(resets)}", flat(
                f"USAGE marker {marker.get('level')} outlived its window's reset at "
                f"{time.strftime('%H:%M', time.localtime(resets))} by {(now - resets) / 60:.0f} min: the loop reads it "
                "as cleared, but no status line has run since (tools/usage_statusline.py, installed in "
                ".claude/settings.local.json); check it runs")))
    elif (share.get("rank") or 0) > 0 and (share.get("resets_at") or 0) > now and now - (share.get("at") or now) > 60:
        alerts.append(Alert("usage:missing", flat(
            f"USAGE: {state / 'usage-share.json'} records level rank {share.get('rank')} this window but there is no "
            f"marker {state / quota.LEVEL}, so the loop isn't acting on it: tools/usage_statusline.py writes both; "
            "find out why")))
    live, coding = quota.marker(state, now), []
    for path in sorted((state / "runs").glob("E*/worker.json")):
        worker = read_json(path)
        status, exp = worker.get("status"), path.parent.name
        if status == "running" and worker.get("pid") and meta_alive(worker["pid"]):
            coding.append(exp)
        if (status == "running" and worker.get("pid") and live.get("rank", 0) >= quota.PAUSE
                and now - live.get("since", now) > USAGE_LATE and meta_alive(worker.get("driver_pid"))
                and meta_alive(worker["pid"])):
            alerts.append(Alert(f"usage:running:{exp}:{int(live['since'])}", flat(
                f"USAGE pause since {time.strftime('%H:%M', time.localtime(live['since']))}, but {exp}'s coder (pid "
                f"{worker['pid']}) still runs: driver.pausable should have stopped it; see {path}")))
        resumes = worker.get("resets_at")
        if (status == "paused" and isinstance(resumes, (int, float)) and now - resumes > USAGE_LATE
                and meta_alive(worker.get("driver_pid"))):
            alerts.append(Alert(f"usage:paused:{exp}:{int(resumes)}", flat(
                f"USAGE: {exp}'s coder is still paused {(now - resumes) / 60:.0f} min after its window reset at "
                f"{time.strftime('%H:%M', time.localtime(resumes))}: driver.unpause should have resumed it (is the "
                f"marker {state / quota.LEVEL} still live?); see {path} and its driver's log")))
    written = share.get("at")
    if coding and isinstance(written, (int, float)) and now - written > USAGE_SILENT:
        alerts.append(Alert(f"usage:silent:{int(written)}", flat(
            f"USAGE unread: {state / 'usage-share.json'} was last written {when(written, now)}, while {', '.join(coding)} code: only a Claude session drawing its status "
            f"line (tools/usage_statusline.py) writes it, so no usage level can pause them now; open or wake one")))
    return alerts


# Fewer than driver.IN_FLIGHT experiments in flight (driver.pipeline_lanes, the count driver.free_lane refuses a claim
# at) for LANES_AFTER while a claim would be taken: ideas are ready (driver.ready_ideas) and fewer than driver.CODING
# are being coded, so free_lane lets one more `--once` through. The orchestrator refills each lane by hand, and twice
# on 2026-09-28 (08:23, 09:00) the evaluator went idle with no candidate waiting because lanes had drained and nothing
# said so (REFLECT 0849 F8). The spell's start is kept in `seen` (health.json's "lanes short"); one alert a spell,
# keyed by that start and by each LANES_REPEAT since, so it repeats that often while the spell lasts. Its text opens
# "LANES SHORT:", which the orchestrator's alert watcher can treat as urgent. It runs every minute beside the upkeep
# tick (quick), not only with the hourly checks. Quiet under a stop marker.
def lanes_short(state: Path, now: float, seen: dict) -> list[Alert]:
    import driver  # lazily: the driver imports this module
    ideas = backlog(state)
    # The usage pause claims nothing (loop/quota.py): lanes refill at the reset, and this alerts then if they don't.
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists() or quota.paused(state, now)
    lanes = driver.pipeline_lanes(ideas)
    coding = driver.coding_count(ideas, state)
    claim_open = not stopped and len(lanes) < driver.IN_FLIGHT and coding < driver.CODING
    ready = driver.ready_ideas(ideas) if claim_open else []
    since = held_since(seen, "since", bool(ready), now)
    if not ready or now - since < LANES_AFTER:
        return []
    repeat = int((now - since - LANES_AFTER) // LANES_REPEAT)
    room = min(driver.IN_FLIGHT - len(lanes), driver.CODING - coding)
    first = ", ".join(i["id"] for i in sorted(ready, key=lambda i: (not i.get("next"), driver.claim_order(i)))[:3])
    busy = ", ".join(f"lane{n}" for n in sorted(lanes)) or "none"
    return [Alert(f"lanes short:{int(since)}:{repeat}",
                  f"LANES SHORT: {len(lanes)} of {driver.IN_FLIGHT} experiments in flight since "
                  f"{time.strftime('%H:%M', time.localtime(since))} ({(now - since) / 60:.0f} min; busy: {busy}; "
                  f"{coding} being coded) with {len(ready)} ideas ready (first: {first}): room for {room} more; start "
                  f"`uv run loop/driver.py --once --parent <main>` for each. Repeats every {LANES_REPEAT // 60} min "
                  f"while short; see {state / 'ideas.jsonl'}")]


# Placeholder clock times ("17:2x", "16:xx": a guessed minute, MISSES 26) in the records the orchestrator writes:
# decisions.md (each "## " block, heading and body), its decision blocks in discord/digests.md (a "## " heading naming
# the orchestrator and decisions, to the next heading) and its notes on ideas (driver.orchestrator_note's
# "orchestrator YYYY-MM-DD hh:mm: ..."), found by tools/my_messages.py's detector (placeholders; a quoted one is a
# quotation). REFLECT 2011 F12: the rule held in messages, which --check-times reads, but broke in a file, which it
# doesn't. Since REFLECT 2026-09-29 20:42 F7, F8 it also reads reminders.jsonl's texts and owed.md's lines, and flags a
# guessed time of one of sig's messages ("minute not logged", "about hh:mm" next to him: my_messages.sig_guesses; the
# transcript logs his to the second). Read-only. Only problems the check didn't find at its first run alert (`seen`,
# health.json's "typed times", keeps those, at most TYPED_OLD; "old-2" the same for what F7 and F8 added, from its
# first run), so old records don't alert forever; each is known by its source and line, so adding to a block doesn't
# re-alert it.
TYPED_OLD = 500
TYPED_LATER = ("reminders.jsonl", "owed.md")  # sources "old-2" baselines


def record_entries(state: Path) -> Iterator[tuple[str, str]]:
    decisions = state / "decisions.md"
    if decisions.exists():
        for block in re.split(r"\n(?=## )", decisions.read_text(errors="replace")):
            yield "decisions.md", block
    digests = state / "discord" / "digests.md"
    if digests.exists():
        for block in re.split(r"\n(?=## )", digests.read_text(errors="replace")):
            heading = block.split("\n", 1)[0].lower()
            if heading.startswith("## ") and "orchestrator" in heading and "decision" in heading:
                yield "digests.md decisions", block
    ideas = state / "ideas.jsonl"
    for line in (ideas.read_text(errors="replace").splitlines() if ideas.exists() else []):
        try:
            idea = json.loads(line)
        except ValueError:
            continue
        if isinstance(idea, dict):
            for note in idea.get("history") or []:
                if isinstance(note, str) and note.startswith("orchestrator "):
                    yield f"{idea.get('id')}'s note", note
    reminders = state / "reminders.jsonl"
    for line in (reminders.read_text(errors="replace").splitlines() if reminders.exists() else []):
        try:
            text = json.loads(line).get("text")
        except (ValueError, AttributeError):
            continue
        if isinstance(text, str):
            yield "reminders.jsonl", text
    owed = state / "owed.md"
    for line in (owed.read_text(errors="replace").splitlines() if owed.exists() else []):
        if line.strip():
            yield "owed.md", line


def typed_times(state: Path, seen: dict) -> list[Alert]:
    tool = Path(__file__).resolve().parent.parent / "tools/my_messages.py"
    spec = importlib.util.spec_from_file_location("my_messages", tool)
    detector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(detector)
    found, later = {}, set()
    for source, text in record_entries(state):
        problems = [(where, clock, f"the placeholder time {clock!r} (a guessed minute, MISSES 26)")
                    for where, clock in detector.placeholders(text)]
        guesses = [(where, what.split(":")[0].strip("'\" "), f"a guessed time of sig's message ({what})")
                   for where, what in detector.sig_guesses(text)]
        for where, short, what in problems + guesses:
            end = text.find("\n", where)
            line = text[text.rfind("\n", 0, where) + 1:end if end >= 0 else len(text)]
            digest = hashlib.sha256(f"{source}\n{line}".encode()).hexdigest()[:12]
            around = flat(text[max(0, where - 60):where + 60])
            key = f"typed-time:{digest}:{short}"
            found[key] = Alert(key, f"{source} has {what}: ...{around}...; write the clock's time (`date`, or strftime "
                               f"in the script; sig's from tools/my_messages.py) in {state}")
            if source in TYPED_LATER or (where, short, what) in guesses:
                later.add(key)
    if "old" not in seen:
        seen["old"] = sorted(key for key in found if key not in later)[:TYPED_OLD]
    if "old-2" not in seen:
        seen["old-2"] = sorted(later)[:TYPED_OLD]
        return []
    return [alert for key, alert in found.items() if key not in seen["old"] and key not in seen["old-2"]]


START_STAGES = ("SPRT", "target batch", "screen")  # the evaluation's stages that play on the remote hosts, as utilisation rows name them
START_STALL_MINUTES = 2  # rows in a row at such a stage's start with none of its games anywhere and the hosts free


# Batches that wait before their first game (MISSES 53): until then every referee run waited in native.play_script for
# room on this Mac before any game, though most of its games play on the hosts (E0143's SPRT, 2026-09-27 03:43-03:44:
# no game in two rows, this Mac full, ~50 remote slots free). From utilisation.jsonl's rows in the last REPEAT: an
# alert for each evaluation stage in START_STAGES that began with START_STALL_MINUTES rows or more in which none of
# its games played anywhere while at least half the remote capacity sat idle. And the fix's first real firing: the
# first such stage to begin after this check first ran (`seen`, health.json's "batch starts") with this Mac full in
# its first row (here capacity 0) alerts once, when the record holds START_STALL_MINUTES rows of it, saying whether its
# games began on the hosts in those rows (a normal start's setup can leave its first row empty: 9 of 25 SPRT and
# screen starts did). That is evidence, not proof: the old wait ended at any moment of room, so starts with this Mac
# full at the next sample happened before the fix too (E0136's SPRT at 02:04, E0167's screen at 15:40); the stall
# alert is the detector.
def batch_starts(state: Path, now: float, seen: dict) -> list[Alert]:
    since = seen.setdefault("since", now)
    rows = throughput.read_rows(state, since=now - REPEAT)
    alerts = []
    for index in range(1, len(rows)):
        row, before = rows[index], rows[index - 1]
        experiment, stage = stage_key(row)
        if stage not in START_STAGES or stage_key(before) == stage_key(row) or row["at"] - before["at"] > throughput.GAP_S:
            continue  # not a start seen by the record (or the sampler was down just before it)
        stalled = 0
        for later in rows[index:]:
            here, there, idle = stage_games(later)
            if stage_key(later) != stage_key(row) or here or there or idle < 0.5:
                break
            stalled += 1
        began = time.strftime("%H:%M", time.localtime(row["at"]))
        if stalled >= START_STALL_MINUTES:
            alerts.append(Alert(f"batch-start:{experiment}:{stage}", f"{experiment or 'the evaluation'}'s {stage} "
                                f"began {began} and played no game anywhere for {stalled} min while the remote hosts "
                                f"sat at least half idle: something before its first game waits (MISSES 53: this "
                                f"Mac's load gate applies per game here, never before a batch); see "
                                f"{state / throughput.LOG}"))
        if ("first" not in seen and row["at"] >= since and row["hosts"].get("here", {}).get("capacity") == 0
                and index + START_STALL_MINUTES <= len(rows)):
            there = next((g for r in rows[index:index + START_STALL_MINUTES]
                          if stage_key(r) == stage_key(row) and (g := stage_games(r)[1])), 0)
            seen["first"] = {"experiment": experiment, "stage": stage, "at": row["at"], "remote": there}
            rows_text = f"in its first {START_STALL_MINUTES} rows"
            verdict = (f"its games began on the hosts {rows_text} ({there:.0f} there), as the fix expects" if there
                       else f"none of its games on the hosts {rows_text}: check native.play_script's wait")
            alerts.append(Alert("batch-start:first", f"first {stage} to begin with this Mac full since the batch-start "
                                f"fix (MISSES 53): {experiment} at {began}; {verdict}; see {state / throughput.LOG}"))
    return alerts


# A utilisation row's evaluation as (experiment, stage).
def stage_key(row: dict) -> tuple:
    evaluation = row.get("evaluation") or {}
    return evaluation.get("experiment"), evaluation.get("stage")


# A utilisation row's evaluation games here and on the remote hosts, and the share of the up hosts' capacity idle.
def stage_games(row: dict) -> tuple[float, float, float]:
    remote = [h for name, h in row["hosts"].items() if name != "here" and not h.get("down")]
    here = (row["hosts"].get("here", {}).get("by") or {}).get("evaluation", 0)
    there = sum((h.get("by") or {}).get("evaluation", 0) for h in remote)
    idle = sum(max(0.0, (h.get("capacity") or 0) - (h.get("busy") or 0)) for h in remote)
    return here, there, idle / max(1, sum(h.get("capacity") or 0 for h in remote))


STALE_WORKTREE = 2 * 3600  # a clean worktree whose HEAD main has, untouched this long, is a finished agent's

# The main repo's worktrees from `git worktree list --porcelain`, the main checkout first: each one's path, HEAD and
# whether it is locked (Claude Code locks a running agent's worktree: "locked claude agent <name> (pid ...)").
def worktrees(repo: Path) -> list[dict]:
    found = []
    for block in git(repo, "worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.partition(" ")[::2] for line in block.splitlines() if line)
        if "worktree" in fields and "HEAD" in fields:
            found.append({"path": Path(fields["worktree"]), "head": fields["HEAD"], "locked": "locked" in fields})
    return found


# When a worktree last changed: its HEAD's reflog (every commit and checkout), else its HEAD file.
def worktree_touched(path: Path) -> float:
    folder = Path(git(path, "rev-parse", "--absolute-git-dir"))
    for name in ("logs/HEAD", "HEAD"):
        try:
            return (folder / name).stat().st_mtime
        except OSError:
            continue
    return 0.0


# A gstack browse server started outside the main checkout: a live one named by the .gstack/browse.json of another
# worktree or of an experiment clone (clones_folder). The browse binary keys its server by the working directory's
# git root, so such a server is a second, signed-out browser, not the main checkout's signed-in one that sig keeps
# open (MISSES 51). The main checkout is git's first worktree, whichever worktree `repo` is. A pid in a leftover file
# that another program reuses doesn't count (home.browse_server).
def stray_browsers(repo: Path) -> list[Alert]:
    if not (repo / ".git").exists():
        return []
    trees = [tree["path"] for tree in worktrees(repo)]
    main = trees[0] if trees else repo
    clones = clones_folder(main)
    others = trees[1:] + (sorted(path for path in clones.iterdir() if path.is_dir()) if clones.is_dir() else [])
    stray = [f"{path} (pid {pid})" for path in others
             if (pid := browse_server(path / ".gstack" / "browse.json")) is not None]
    if not stray:
        return []
    return [Alert("browser:stray", f"a gstack browser runs outside the main checkout: {', '.join(stray[:3])}; it is "
                  f"signed out and not sig's window. Stop it (`browse stop` from that folder) if you started it, and "
                  f"run browse commands from {main} or with BROWSE_STATE_FILE={main / '.gstack' / 'browse.json'}")]


# <state>/discord/browser.json, the browse server pid of the last Discord read that got past the login page
# (tools/discord_read.py's save_signed_in; parsed here, not imported): alerts when it is unreadable, and when the main
# checkout's live browse server is another pid, since gstack restarts sig's browser (after an upgrade) headless and
# signed out, so the next read is likely to fail (REFLECT 2026-09-27 F3, MISSES 51). No file or no live server: quiet
# (the read's own pre-check reports a closed browser).
def discord_browser(state: Path, repo: Path) -> list[Alert]:
    path = state / "discord" / "browser.json"
    if not path.exists():
        return []
    try:
        record = json.loads(path.read_text())
        pid, at = record.get("pid"), record.get("at")
    except (OSError, ValueError, AttributeError):
        pid = at = None
    if not isinstance(pid, int) or isinstance(pid, bool):
        return [Alert("discord:browser-record", f"{path} is unreadable; the next Discord sign-out can't tell a "
                      f"restarted browser from a plain sign-out; the next signed-in read rewrites it")]
    trees = [tree["path"] for tree in worktrees(repo)] if (repo / ".git").exists() else []
    main = trees[0] if trees else repo
    live = browse_server(main / ".gstack" / "browse.json")
    if live is None or live == pid:
        return []
    try:
        read_at = datetime.fromisoformat(at).astimezone().strftime("%d %b %H:%M")
    except (TypeError, ValueError):
        read_at = "time not recorded"
    return [Alert("discord:browser-restarted", f"sig's browser restarted since the last signed-in Discord read "
                  f"({read_at}): pid {pid} → {live}; gstack restarts it headless and signed out, so the next read will "
                  f"likely fail. Ask sig to sign in (browse handoff) unless he has; the next signed-in read clears this")]


# Leftovers of finished work (twelve finished agents' worktrees were left behind on 2026-09-27): each worktree of the
# main repo, other than the main checkout, the loop's champion and referee copies and a locked one (a running agent's),
# that is clean, whose HEAD main already has and that hasn't moved for STALE_WORKTREE, naming the ignored files it
# holds (outputs such as data/ or *.replay, which `git worktree remove` deletes without asking; __pycache__ left out);
# and each wip/E#### branch (a coder's work saved when its try stopped) whose idea is no longer todo or being tried,
# since a wip branch is kept while its idea waits, however long. `git status` runs with optional locks off, so the
# check never refreshes an index and so never makes a worktree look recently used.
def leftovers(state: Path, repo: Path, now: float) -> list[Alert]:
    if not (repo / ".git").exists():
        return []  # no repo here to leave anything in
    alerts = []
    keep = {repo.resolve(), (state / "champion").resolve(), (state / "referee").resolve()}
    stale = []
    for tree in worktrees(repo):
        path = tree["path"]
        if tree["locked"] or path.resolve() in keep or not path.is_dir():
            continue
        try:
            status = subprocess.run(["git", "-c", "core.fsmonitor=false", "status", "--porcelain", "--ignored"],
                                    cwd=path, capture_output=True, text=True, check=True, timeout=60,
                                    env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.splitlines()
            merged = subprocess.run(["git", "merge-base", "--is-ancestor", tree["head"], "main"], cwd=repo,
                                    capture_output=True, timeout=60).returncode == 0
            touched = worktree_touched(path)
        except (subprocess.SubprocessError, OSError):
            continue  # a worktree being removed meanwhile
        ignored = [line[3:] for line in status if line.startswith("!! ") and "__pycache__" not in line]
        dirty = [line for line in status if not line.startswith("!! ")]
        if not dirty and merged and now - touched > STALE_WORKTREE:
            holds = (f", holding ignored {', '.join(ignored[:3])}{f' and {len(ignored) - 3} more' if len(ignored) > 3 else ''}"
                     if ignored else "")
            stale.append(f"{path} ({when(touched, now)}{holds})")
    if stale:
        alerts.append(Alert("stale worktrees", f"{len(stale)} worktrees are clean, unlocked, their HEAD is in main and "
                            f"nothing moved them for {STALE_WORKTREE / 3600:.0f} h: finished work left behind; remove "
                            f"each (git worktree remove <path>, which also deletes the ignored files named) after "
                            f"checking its agent is done and nothing ignored in it is wanted: {'; '.join(stale)}"))
    ideas = {}
    for line in (state / "ideas.jsonl").read_text().splitlines() if (state / "ideas.jsonl").exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # backlog_rows alerts on it
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            ideas[row["id"]] = row
    old = []
    for name in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/wip/").splitlines():
        idea = read_json(state / "runs" / name.removeprefix("wip/") / "experiment.json").get("idea", {}).get("id")
        status = str(ideas.get(idea, {}).get("status", "")) if idea else ""
        if not (status == "todo" or status.startswith("trying:")):
            old.append(f"{name} ({idea or 'no experiment record'} is {status or 'not in the backlog'})")
    if old:
        alerts.append(Alert("wip branches", f"{len(old)} wip branches outlived their use (their idea is no longer to "
                            f"do or being tried): {'; '.join(old)}; delete each (git branch -D) once nothing in it is "
                            f"wanted"))
    return alerts


# The registries of players that split a share (native.register_filler): fillers/ the screen fillers (the late screen,
# official-map reports), fillers-idle/ the idle ones (the sweep, the prewarm, the adversarial-map search), fillers-tune/
# the tune players (a tune, the evidence refresh), a file `<start ns>-<pid>` per player (`.tmp` while it registers),
# locked (flock) for the process's life, so each player's part of a host is its share over the files locked there
# (native.filler_rank). A dead player's file stays until the next count of its kind (native.filler_names) deletes it,
# so a few live files a folder plus those: FILLER_FILES bounds it. A file held locked though the pid in its name is dead
# (an inherited lock, a hand-made file) counts as a live player forever and cuts every other one's part for nothing.
# A count (native.filler_names) locks a dead player's file for a moment before deleting it, so a file that looks held
# for a dead pid is probed again PHANTOM_RECHECK_S later and counts only if it is still there and still held.
FILLER_FOLDERS = ("fillers", "fillers-idle", "fillers-tune")  # native.FILLERS, IDLE_FILLERS, TUNE_PLAYERS
FILLER_FILES = 40
PHANTOM_RECHECK_S = 0.2


# Whether someone holds `path`'s lock: False when it can be taken at once (taken shared and released) or the file is
# gone.
def lock_held(path: Path) -> bool:
    try:
        with path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
    except OSError:
        return False


def filler_registries(state: Path) -> list[Alert]:
    alerts = []
    for name in FILLER_FOLDERS:
        folder = state / name
        paths = sorted(folder.iterdir()) if folder.is_dir() else []
        if len(paths) > FILLER_FILES:
            alerts.append(Alert(f"fillers:size:{name}", f"{folder} holds {len(paths)} files, over {FILLER_FILES}: "
                                f"players register there faster than counts clean the dead ones (native.filler_names)"))

        def dead(path: Path) -> bool:
            pid = path.name.removesuffix(".tmp").rpartition("-")[2]
            return not (pid.isdigit() and meta_alive(int(pid)))

        phantoms = [path for path in paths if dead(path) and lock_held(path)]
        if phantoms:
            time.sleep(PHANTOM_RECHECK_S)
            phantoms = [path.name for path in phantoms if lock_held(path)]
        if phantoms:
            alerts.append(Alert(f"fillers:phantom:{name}", f"{len(phantoms)} file(s) in {folder} are held locked though "
                                f"the pid they name is dead ({', '.join(phantoms[:5])}), so each counts as a live player "
                                f"and cuts the others' share of every host; find the holder (lsof <file>) and delete "
                                f"the file once it is stopped"))
    return alerts


# The map pool (loop/mappool.py; <loop home>/map-pool and mappool.json): over mappool.BOUND_BYTES, a fill failing
# twice in a row, or one alive with no chunk made for mappool.STALE while the pool is short.
def map_pool(state: Path, now: float, repo: Path | None = None) -> list[Alert]:
    alerts, folder = [], mappool.pool_dir(state)
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0
    if size > mappool.BOUND_BYTES:
        alerts.append(Alert("map-pool:size", f"{folder} holds {size / 2**20:.0f} MB, over its bound of "
                            f"{mappool.BOUND_BYTES / 2**20:.0f} MB (mappool.SETS shared sets' worth, mappool.targets)"))
    record = mappool.read(state)
    where = f"see {mappool.record_path(state)} and {state / 'map-pool.log'}"
    if (record.get("failures") or 0) >= 2 and not meta_alive(record.get("pid")):
        alerts.append(Alert("map-pool:failing", f"the map pool's fill failed {record['failures']}x in a row: "
                            f"{str(record.get('error') or '').strip()[:200]}; new champions' first evaluations make "
                            f"their maps themselves meanwhile; {where}"))
    progress = record.get("progress_at") or record.get("started_at")
    if meta_alive(record.get("pid")) and isinstance(progress, (int, float)) and now - progress > mappool.STALE \
            and mappool.room() and mappool.short(state):
        alerts.append(Alert("map-pool:stale", f"the map pool's fill (pid {record['pid']}) has made no chunk since "
                            f"{when(progress, now)} with this Mac under its load share; {where}"))
    # Its first real firing (CLAUDE.md: every new automation gets one): no chunk ever made (mappool.json's "made")
    # FIRST_FIRING after loop/mappool.py last reached main (mappool_landed) while the pool is short.
    if not record.get("made") and repo is not None and (landed := mappool_landed(repo)) is not None \
            and now - landed > FIRST_FIRING and mappool.short(state):
        alerts.append(Alert("map-pool:never", f"the map pool has made no chunk since loop/mappool.py reached main "
                            f"{when(landed, now)}: is maintenance.tick launching mappool.tick, and is this Mac ever "
                            f"under its load share (mappool.room)? {where}"))
    return alerts


FIRST_FIRING = 2 * 3600  # map_pool: a first chunk is due this long after mappool.py reaches main


# When loop/mappool.py last changed on `repo`'s main line (the merge that brought it), or None without git history.
def mappool_landed(repo: Path) -> float | None:
    try:
        stamp = git(repo, "log", "-1", "--first-parent", "--format=%ct", "HEAD", "--", "loop/mappool.py")
    except (OSError, subprocess.SubprocessError):
        return None
    return float(stamp) if stamp else None


# A quarantine repo publishing.fetch_checked left in the main repo's git folder when it crashed
# (publishing.stale_quarantines): the next checked fetch deletes it, so one here means none has run since or the
# deletion failed. A quarantine holds only objects a refused or unfinished fetch brought in.
def fetch_quarantines(repo: Path, now: float) -> list[Alert]:
    if not (repo / ".git").exists():
        return []
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    stale = stale_quarantines(common, now)
    if not stale:
        return []
    return [Alert("fetch quarantines", f"{len(stale)} fetch quarantine repos older than "
                  f"{QUARANTINE_STALE // 60} min, left by a checked fetch that crashed: "
                  f"{'; '.join(str(path) for path in stale[:5])}; no fetch holds their lock, so each is safe to "
                  f"delete (rm -rf <path>) once no fetch is running; the next checked fetch deletes them too")]


# The CPU profile of one game (tools/game_profile.py -> game-profile.json) must be at most PROFILE_AGE old and made on
# the active toolkit (maintenance.json's `active`, which each upgrade replaces), so claims about where a game's CPU
# goes rest on a current measurement. The alert tells the orchestrator to run it: one game, under the load gate.
def game_profile(state: Path, now: float) -> list[Alert]:
    path = state / "game-profile.json"
    if not (state / "maintenance.json").exists():
        return []  # no loop has run its upkeep here
    profile = read_json(path)
    active = read_json(state / "maintenance.json").get("active") or {}
    at = profile.get("at")
    run = f"run `.venv/bin/python tools/game_profile.py` (one game, under the load gate)"
    if not isinstance(at, (int, float)):
        return [Alert("profile:missing", f"no CPU profile of a game yet ({path}); {run}")]
    version, upgraded = active.get("version"), active.get("validated_at")
    if version and profile.get("unswbc") != version or isinstance(upgraded, (int, float)) and at < upgraded:
        return [Alert(f"profile:{version}", f"the game CPU profile ({when(at, now)}) was made on unswbc "
                      f"{profile.get('unswbc')}, but the active toolkit is {version}; {run}; see {path}")]
    if now - at > PROFILE_AGE:
        return [Alert("profile:old", f"the game CPU profile is from {when(at, now)}, over "
                      f"{PROFILE_AGE / 86400:.0f} days old; {run}; see {path}")]
    return []


# The loop home's size, measured once a day (`du -sk` of each top-level entry, those of 1 MB or more kept by name) into
# health.json's `sizes`, the latest SIZE_ROWS measurements; growing more than GROWTH_GB a day between the last two
# alerts once for that measurement, naming the entries that grew most. A `du` that fails without output records nothing and raises.
def home_size(state: Path, now: float, record: dict) -> list[Alert]:
    rows = record.setdefault("sizes", [])
    if not rows or now - rows[-1]["at"] >= SIZE_EVERY:
        entries = [str(path) for path in sorted(state.iterdir()) if not path.is_symlink()]
        du = subprocess.run(["du", "-sk", *entries], capture_output=True, text=True, timeout=600)
        # Exit 1 with output: an entry vanished mid-walk (a finished game's scratch folder); the rest are measured.
        if du.returncode not in (0, 1) or not du.stdout.strip():  # nothing recorded; raised, so it alerts as broken
            raise RuntimeError(f"du exited {du.returncode}: {du.stderr.strip()[-200:] or 'no output'}")
        sizes = {Path(path).name: int(kb) for kb, path in (line.split("\t", 1) for line in du.stdout.splitlines())}
        rows.append({"at": now, "total_kb": sum(sizes.values()),
                     "kb": {name: kb for name, kb in sizes.items() if kb >= 1024}})
        del rows[:-SIZE_ROWS]
    if len(rows) < 2:
        return []
    old, new = rows[-2], rows[-1]
    days = max(1.0, (new["at"] - old["at"]) / 86400)
    growth = (new["total_kb"] - old["total_kb"]) / days / 1024 ** 2
    if growth <= GROWTH_GB:
        return []
    grew = sorted(((kb - old["kb"].get(name, 0), name) for name, kb in new["kb"].items()), reverse=True)[:3]
    top = ", ".join(f"{name} +{kb / 1024 ** 2:.1f} GB" for kb, name in grew if kb > 0)
    return [Alert(f"home-size:{int(new['at'])}", f"the loop home grew {growth:.1f} GB a day to "
                  f"{new['total_kb'] / 1024 ** 2:.1f} GB (measured {when(old['at'], now)} and {when(new['at'], now)}), "
                  f"most in {top or 'many small entries'}; prune what no step reads any more; see {state} and "
                  f"health.json's `sizes`")]


def disk(state: Path, now: float) -> list[Alert]:
    free = shutil.disk_usage(state).free / 1e9
    if free >= DISK_MIN_GB:
        return []
    return [Alert("disk", f"only {free:.0f} GB free on the volume holding {state} (under {DISK_MIN_GB} GB) "
                  f"now; see what {state / 'runs'} and the experiment clones hold")]


# The checks also run every minute beside the upkeep tick (quick), not only with the others.
QUICK = ("lanes short", "mac waits", "gate fallback", "evaluation queue", "eval backlog", "ticketless candidates",
         "dead lanes", "other cpu", "clone cpu", "evaluator idle", "upkeep ticks", "usage level", "reminders", "slow sprt")


# Every check's alerts for the live state at `now`, or only those of the checks named in `only`. `record` is
# health.json's content: the checks keep their first-seen times in it. A check that raises is an alert itself, so a
# broken check isn't a silent one.
def alerts(state: Path, repo: Path, now: float, record: dict, only: tuple[str, ...] | None = None) -> list[Alert]:
    # A worker's lateness checks are quiet while its usage level holds it (quota.HELD): it is late on purpose.
    def unless_held(kind: str, check: Callable[[], list[Alert]]) -> list[Alert]:
        return [] if quota.held(state, kind, now) else check()

    checks: dict[str, Callable[[], list[Alert]]] = {
        "upkeep": lambda: upkeep(state, now, record.setdefault("seen", {})),
        "audit labels": lambda: audit_labels(state, now),
        "leader games": lambda: leaders(state, now),
        "unstudied versions": lambda: unstudied_versions(state),
        "replay library": lambda: replay_library(state, now),
        "behaviour gaps": lambda: behaviour_gaps(state, now),
        "old kept marks": lambda: old_kept_marks(state, now, record.setdefault("old kept marks", {})),
        "behaviour stats": lambda: behaviour_stats(state, now, record.setdefault("behaviour stats", {})),
        "evidence": lambda: evidence(state, repo, now),
        "publishing": lambda: publishing(state, repo, now),
        "dev battles": lambda: dev_battles(state, now),
        "online recording": lambda: online_recording(state, now),
        "online compare": lambda: online_compare(state, now),
        "online fit": lambda: online_fit(state, now),
        "online rams": lambda: online_rams(state, now),
        "sweep shares": lambda: sweep_shares(state, now),
        **({"map records": lambda: map_records(state), "goal maps": lambda: goal_maps(state)} if MAP_WATCHERS else {}),
        "goal drift": lambda: goal_drift(state, now, record.setdefault("goal drift", {})),
        "dead prerequisites": lambda: dead_prerequisites(state),
        "mac sleeps": lambda: mac_sleeps(state, now),
        "host rests": lambda: host_rests(state, now),
        "evalline outages": lambda: evalline_outages(state),
        "death tables": lambda: death_tables(state, now, record.setdefault("death tables", [])),
        "starts": lambda: layouts(state, now),
        "referee copy": lambda: referee_lag(state, repo, now),
        "hosts": lambda: hosts(state, now),
        "host outages": lambda: host_outages(state, now),
        "azure hosts": lambda: azure_hosts(state, now, record.setdefault("azure", {})),
        "engine cache": lambda: engine_cache(state, now),
        "fork server": lambda: fork_server(state, now),
        "native judge": lambda: native_judge(state, now),
        "game retries": lambda: game_retries(state, now),
        "bad maps": lambda: bad_maps(state, now),
        "lazy markers": lambda: lazy_markers(state, now),
        "input faults": lambda: input_faults(state, now),
        "niced replays": lambda: niced_replays(state, now),
        "cpu split": lambda: cpu_split(state, now),
        "process limit": lambda: process_limit(state, now),
        "prepared": lambda: prepared(state, repo, now),
        "bot crashes": lambda: bot_crashes(state, now, record.setdefault("bot crashes", {})),
        "method notes": lambda: method(state, now, record.setdefault("method", {})),
        "disk": lambda: disk(state, now),
        "reflect": lambda: [Alert("reflect", text)] if (text := reflect.late(state, now)) else [],
        "briefing": lambda: briefing(state, repo, now, record.setdefault("briefing", {})),
        "reminders": lambda: reminders(state, now),
        "usage level": lambda: usage_level(state, now),
        "discord digest": lambda: unless_held("discord-digest", lambda: discord_digest(state, now)),
        "digest worker": lambda: unless_held("discord-digest", lambda: digest_worker(state, now)),
        "undecided items": lambda: undecided(state, now),
        "rule checks": lambda: unchecked_rejections(state, now),
        "owed starts": lambda: owed_starts(state, now),
        "owed cleared": lambda: owed_cleared(state, merged_branches(repo)),
        "report numbering": lambda: numbering(state, now),
        "reflect worker": lambda: unless_held("reflect-list", lambda: reflect_worker(state, repo, now)),
        "discord partial": lambda: discord_partial(state, now),
        "stray browsers": lambda: stray_browsers(repo),
        "discord browser": lambda: discord_browser(state, repo),
        "discord teams": lambda: discord_teams(state, now),
        "old team names": lambda: old_team_names(state, repo),
        "map ideas": lambda: unless_held("mapideas", lambda: map_ideas(state, repo, now)),
        "riser study": lambda: unless_held("risers", lambda: riser_study(state, now)),
        "backlog audit": lambda: unless_held("backlog", lambda: backlog_audit(state, now)),
        "bceval": lambda: bceval(state, now),
        "variant sweep": lambda: sweep(state, repo, now),
        "baseline prewarm": lambda: prewarm(state, now),
        "old code": lambda: old_code(state, git(repo, "rev-parse", CHAMPION_BRANCH)
                                     if (state / "shared-maps").is_dir() else "", now),
        "adversarial maps": lambda: adversarial_maps(state, repo, now),
        "official-map reports": lambda: official_reports(state, now, record.setdefault("official", {})),
        "screen reuse": lambda: screen_reuse(state),
        "pair tightness": lambda: pair_tightness(state),
        "target batches": lambda: target_batches(state),
        "result turns": lambda: result_turns(state, now),
        "clone replays": lambda: clone_replays(state),
        "judge turns": lambda: judge_turns(state, now),
        "judged followups": lambda: judged_followups(state),
        "uncommitted no code": lambda: uncommitted_no_code(state, repo, clones_folder(repo),
                                                          record.setdefault("uncommitted", [])),
        "map splits": lambda: map_splits(state, record.setdefault("map splits", [])),
        "style streaks": lambda: style_streaks(state, record.setdefault("style streaks", [])),
        "official changed": lambda: official_changed(state, record.setdefault("official changed", [])),
        "backlog rows": lambda: backlog_rows(state),
        "stuck ideas": lambda: stuck_ideas(state),
        "ranking": lambda: ranking(state),
        "turn workers": lambda: turn_workers(state, now),
        "stranded tries": lambda: stranded(state),
        "dead lanes": lambda: dead_lanes(state, now),
        "sweep groups": lambda: sweep_groups(repo),
        "late screen": lambda: late_screen(state, repo, now, record.setdefault("seen", {})),
        "screen ports": lambda: screen_ports(state, now),
        "rule counts": lambda: rule_counts(state, now),
        "review pending": lambda: review_pending(state, now),
        "doc references": lambda: doc_refs(repo),
        "doc size": lambda: doc_size(repo),
        "ladder map pool": lambda: ladder_pool(state, repo, now, record.setdefault("ladder pool", {})),
        "utilisation": lambda: utilisation(state, now, repo),
        "slow sprt": lambda: slow_sprt(state, now, record.setdefault("slow sprt", {})),
        "local stages": lambda: local_stages(state, now, record.setdefault("local stages", {})),
        "evaluation shares": lambda: over_share(state, now),
        "idle machines": lambda: idle_any(state, repo, now, record.setdefault("idle nothing", {})),
        "leftovers": lambda: leftovers(state, repo, now),
        "fetch quarantines": lambda: fetch_quarantines(repo, now),
        "filler registries": lambda: filler_registries(state),
        "map pool": lambda: map_pool(state, now, repo),
        "batch starts": lambda: batch_starts(state, now, record.setdefault("batch starts", {})),
        "typed times": lambda: typed_times(state, record.setdefault("typed times", {})),
        "game profile": lambda: game_profile(state, now),
        "loop home size": lambda: home_size(state, now, record),
        "lanes short": lambda: lanes_short(state, now, record.setdefault("lanes short", {})),
        "mac waits": lambda: mac_waits(state, now, record.setdefault("mac waits", {})),
        "gate fallback": lambda: gate_fallback(now),
        "evaluation queue": lambda: evaluation_queue(state, now, record.setdefault("evaluation queue", {})),
        "eval backlog": lambda: eval_backlog(state, now, record.setdefault("eval backlog", {})),
        "ticketless candidates": lambda: ticketless(state, now, record.setdefault("ticketless", {})),
        "refused proposals": lambda: refused_proposals(state, now),
        "other cpu": lambda: other_cpu(state, repo, now, record.setdefault("other cpu", {})),
        "clone cpu": lambda: clone_cpu(state, repo, now, record.setdefault("clone cpu", {})),
        "idle loop": lambda: idle_loop(now, record.setdefault("idle loop", {})),
        "ideas log": lambda: ideas_writes(state, now),
        "merge turns": lambda: merge_turn_files(state, now),
        "evaluator idle": lambda: evaluator_idle(state, now, record.setdefault("evaluator idle", {})),
        "backlog growth": lambda: backlog_growth(state, now, record.setdefault("backlog", [])),
        "merge reruns": lambda: merge_reruns(state, now),
        "next stuck": lambda: next_stuck(state, now, record.setdefault("next stuck", {})),
        "upkeep ticks": lambda: upkeep_ticks(state, now),
    }
    if only is not None:
        checks = {name: checks[name] for name in only}
    found = []
    for name, check in checks.items():
        try:
            found += check()
        except Exception as error:
            found.append(Alert(f"broken:{name}", f"health check '{name}' failed: {type(error).__name__}: "
                               f"{str(error).strip()[:200]}; see {Path(__file__).resolve()}"))
    return found


# Checks the live state and returns the alerts to report now: each is also appended to host-alerts.log, and one
# written in the last REPEAT seconds is held back. With `every`, does nothing within `every` seconds of the last
# check (the upkeep tick runs each minute). `dry_run` returns every current alert and writes nothing at all.
def run(state: Path, repo: Path, now: float, every: float = 0, dry_run: bool = False) -> list[str]:
    if dry_run:  # without the alerts remembered as sent once (ladder_pool's "ladder pool"), so none is hidden
        record = {k: v for k, v in read_json(state / "health.json").items() if k != "ladder pool"}
        return [alert.text for alert in alerts(state, repo, now, record)]
    with locked(state / "health"):
        record = read_json(state / "health.json")
        if now - record.get("checked_at", 0) < every:
            return []
        found = alerts(state, repo, now, record)  # before the copy below: checks keep their state in `record`
        return send(state, now, {**record, "checked_at": now}, found)


# The QUICK checks alone, reported as run reports them, leaving the time of the last full check alone. Every process
# running the upkeep asks every minute (maintenance.ticking's own thread, not the tick: an hourly step can take many
# minutes); with `every`, nothing is checked within `every` seconds of the last quick check, so they run once a minute
# between them.
def quick(state: Path, repo: Path, now: float, every: float = 0) -> list[str]:
    with locked(state / "health"):
        record = read_json(state / "health.json")
        if now - record.get("quick_at", 0) < every:
            return []
        found = alerts(state, repo, now, record, only=QUICK)  # before the copy below: checks keep their state in it
        return send(state, now, {**record, "quick_at": now}, found)


# run's alerts for `state` (with `quick_every`, quick's) from a fresh process on the disk's code (`loop/health.py
# --json`), for a caller on older loop code (home.current_code), which would apply its start's rules: at 20:59 on 29
# Sep a driver claimed hours before recorded a result and alerted on the Discord read after 5 h, a 4 h rule main had
# made 25 h at 17:45 (MISSES 100). The child writes the alerts file as run does. None when it can't run, fails or
# prints no list: the caller checks in its own process.
def fresh(state: Path, repo: Path, quick_every: float | None = None) -> list[str] | None:
    command = [sys.executable, str(repo / "loop/health.py"), "--json",
               *([] if quick_every is None else ["--quick", str(quick_every)])]
    try:
        done = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=1800,
                              stdin=subprocess.DEVNULL, env={**os.environ, "BC_LOOP_HOME": str(state)})
        found = json.loads((done.stdout.strip().splitlines() or ["null"])[-1]) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return found if isinstance(found, list) and all(isinstance(text, str) for text in found) else None


# Appends the alerts in `found` not sent in the last REPEAT to host-alerts.log, marks due reminders done, and saves
# `record` with the new sent times as health.json. Returns the alerts written. Called holding the health lock.
def send(state: Path, now: float, record: dict, found: list[Alert]) -> list[str]:
    sent = {key: at for key, at in record.get("sent", {}).items() if now - at < REPEAT}
    # One line per key, though two checks (or two identical reminder lines) give the same one.
    fresh = list({alert.key: alert for alert in found if alert.key not in sent}.values())

    def write(lines: list[Alert]) -> None:
        if lines:
            with (state / "host-alerts.log").open("a") as handle:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                handle.writelines(f"{stamp} health {alert.text}\n" for alert in lines)

    write(fresh)
    due = {alert.key for alert in found
           if alert.key.startswith("reminder:") and not alert.key.startswith("reminder:bad:")}
    if due:  # written now, or within REPEAT (by a run that stopped before marking it): either way it alerted
        try:
            mark_reminders(state, due, now)
        except Exception as error:  # health.json is still saved, so the alerts above wait REPEAT like any other
            broken = Alert("broken:reminders", flat(f"could not mark {len(due)} due reminders done in "
                                                    f"{state / 'reminders.jsonl'} ({type(error).__name__}: "
                                                    f"{error}); they alert again in {REPEAT / 3600:.0f} h"))
            if broken.key not in sent:
                write([broken])
                fresh.append(broken)
    sent.update((alert.key, now) for alert in fresh)
    temporary = state / "health.json.tmp"
    temporary.write_text(json.dumps({**record, "sent": sent}, indent=1) + "\n")
    temporary.replace(state / "health.json")
    return [alert.text for alert in fresh]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print every current alert; write nothing")
    parser.add_argument("--quick", type=float, metavar="EVERY",
                        help="run the quick checks alone, unless run within EVERY seconds (health.quick)")
    parser.add_argument("--json", action="store_true", help="print the alerts as one JSON list (fresh)")
    parser.add_argument("--remind", nargs=2, metavar=("WHEN", "TEXT"),
                        help="add a reminder due at WHEN (ISO local time, e.g. 2026-09-29T09:00, stored with this "
                        "machine's offset on that date; or give an offset), alerted once into host-alerts.log by the "
                        "next check after it")
    args = parser.parse_args()
    if args.remind:
        try:
            row = remind(loop_home(), *args.remind)
        except ValueError as error:
            parser.error(f"--remind: {error}")
        print(f"reminder due {row['due']}: {row['text']}")
        return
    found = run(loop_home(), REPO, time.time(), dry_run=args.dry_run) if args.quick is None \
        else quick(loop_home(), REPO, time.time(), every=args.quick)
    print(json.dumps(found) if args.json else "\n".join(found) if found else "healthy")


if __name__ == "__main__":
    main()
