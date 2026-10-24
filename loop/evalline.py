"""The status line every report to sig opens with (CLAUDE.md "Status lines"), computed now, never carried forward:

    Evaluating: E0118 (I243 At the unit cap, long dragons go ...) since 19:52, 1402/2400 trials, 57 trials/min, 131 games/min, latest finish 20:12; queued ahead: 1 (~40 min), in order: E0119 coded 19:40 (preparing); coding: E0121, E0122; fillers: none; hosts 41/65 slots busy; judge: native 3/3 places, engine cache on, fork on
    Evaluating: E0158 (I401 ...): setup since 12:35 (referee refresh and rebase, before its bot tests); coding: E0159; fillers: none; hosts 0/73 slots busy
    Evaluating: E0127 (I262 Log farm entries ...): screen since 00:06, 40 games/min; coding: E0131; late screen: E0141's champion since 00:02 (1 entries done); fillers: none; hosts ~6/65 slots busy; no game for 10+ min with free slots: sz-wsl (12)
    Idle since 21:49 (last test: E0120 ended 21:49, final: H0, discarded); coding: E0121; fillers: sweep, prewarm; official report: E0120 since 21:50
    live: v0032 (site v31); waiting to upload: v0033 (not on the site), next slot 02:51
    goal: Trauma, Portals and Devil (sig, 30 Sep 19:59): in flight 2 of 4 aim at a goal style (E0406, E0407); top 10 queued 3 (I1079, I1094, I1103)
    agents: Review orch/builder-c since 14:10, Fix round for orch/judged-discard since 14:31; 5 h usage 52%, resets 22:00

    .venv/bin/python loop/evalline.py
    .venv/bin/python loop/evalline.py --eta I915      # its claim and result times from the queue and measured rate (eta)
    .venv/bin/python loop/evalline.py --queue         # under test, queued, P1's flow and P1 in claim order (queue_report)
    .venv/bin/python loop/evalline.py --results-since 20:00   # each result recorded since then (results_since)

The SPRT under way is the experiment whose sprt-progress.json changed in the last 10 minutes while an evaluation
runs (home.evaluation_running); "since" is when that file was created (its first trial), "trials/min" is trials over
that time, and the latest finish is when it reaches its pair cap at that rate (an SPRT can stop sooner), shown only
once it has run ETA_AFTER past its first trial, then the running score over every trial and the champion's, the LLR
against its bound (running_score), and the score of each style the idea's map mix aims at (aimed_scores). "games/min" (the SPRT's and the screen's) is the evaluation's referee
run's own figure in remote-status.json, games since its run began, over that time, while it was written in the last
RECENT: a trial is not a game (REFLECT 2011 F6: a trial rate read as a game rate doubled a test-length estimate). A
decided SPRT says so; one with no trial yet says "no trial yet". While an evaluation runs, a result recorded in the last
LAST_RESULT follows it (last_result: id, end, verdict, estimate). Any other stage (setup, bot tests, CPU gate and maps,
identical check, CPU comparison, screen, merge turn) comes from the run folder's files (throughput.evaluation_now), with
when it began (the end of the stage before it; setup from when the evaluation lock was taken); the screen also says how
its SPRT ended. The lock held with no candidate of its own (a referee refresh) says so. A marker a crashed evaluation
left behind counts as no evaluation (home.evaluation_running). Then the rest of the pipeline: candidates queued for the
evaluation (queue_text: in the order the evaluation lock takes them, and what holds each back),
experiments coding or tuning, the late screen playing (rescreen.json), the game-only fillers whose run is
alive (sweep, prewarm and adversarial maps: FILLERS), the official-map report running, and the game machines' use from
the latest utilisation row (throughput.current_use; "~" when estimated), how games play (engine_mode: the native judge,
engine cache and fork server), and any remote host with free slots that played
no game for throughput.IDLE_HOST_ROWS minutes while the evaluation plays there (throughput.idle_hosts). A second line
appears only while the champion isn't the live submission: what plays online and what waits to be uploaded. A third
lists the orchestrator's own background agents, read from its session transcript (running_agents: Agent calls with no
result yet, at most MAX_AGENTS, none older than AGENT_HOURS), the tests waiting on tools/gated_test.sh's load gate
(gate_waits) and the 5-hour usage share (usage_text). A part-time host that goes down while an evaluation runs is
named once on the first line (down_once). A rejudge holding the lock (a judge's keep: driver.rejudge) is named as
itself, not as a waiting candidate's export. A goal line (goal_line) counts the work in flight and the next queued whose
map mix aims at sig's stated goal (GOAL), and names any idea claimed ahead of sig's recorded ranking (rank_text).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import meta
import quota
import throughput
from home import evaluating_marker, evaluation_running, loop_home

RECENT = 600  # seconds: a progress file changed this recently belongs to the test under way
AGENTS = "orchestrator-agents.json"  # running_agents' place in the orchestrator's transcript and the calls running
MAX_AGENTS = 20  # agents listed, the newest
MAX_WHAT = 120  # characters of an agent's description kept
AGENT_HOURS = 12  # an agent running this long with no result drops off the line (one whose notice was missed)
FILLERS = (("sweep", "sweep.json"), ("prewarm", "baselines.json"), ("adversarial maps", "advmaps.json"))
ETA_AFTER = 300  # seconds past its first trial before an SPRT's rate says when it would reach its cap
REPO = Path(__file__).resolve().parent.parent


# "14:05" for a time `at`, or "?" for one a hand-edited or damaged file gives (not a number, or past time_t).
def hhmm(at: object) -> str:
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return "?"
    try:
        return time.strftime("%H:%M", time.localtime(at))
    except (OverflowError, OSError, ValueError):
        return "?"


def read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# results.jsonl's latest row of each experiment (meta.latest_rows: a rejudged result's earlier row is dropped), of the
# lines that are JSON objects; any other line is skipped.
def results(state: Path) -> list[dict]:
    try:
        lines = (state / "results.jsonl").read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return meta.latest_rows(rows)


# The experiments whose worker is still coding (worker.json "running", from either provider: driver.worker_status,
# native.worker_state), for the idle line.
def coding(runs: Path) -> list[str]:
    return sorted(run.name for run in runs.glob("E*") if read(run / "worker.json").get("status") == "running")


def tuning(runs: Path) -> list[str]:
    return sorted(run.name for run in runs.glob("E*") if read(run / "worker.json").get("status") == "tuning"
                  and not (run / "diff.patch").exists())


# When a file was first written: its birth time where the filesystem keeps one (macOS). sprt-progress.json is
# rewritten in place as trials land, so its birth time stays at the test's first trial; sprt.json is written once, at
# the SPRT's end.
def created(path: Path) -> float:
    stat = path.stat()
    return getattr(stat, "st_birthtime", stat.st_ctime)


def title(run: Path) -> str:
    idea = read(run / "experiment.json").get("idea", {})
    return f"{idea.get('id', '?')} {idea.get('title', '')[:60]}"


# "H1 (a gain)" for SPRT decision "H1" of a test with lower bound `elo0`: a non-inferiority test (noloss, formula and
# targeted ideas: elo0 below 0) reads "H1 (no worse)" or "H0 (worse)", not a gain test's words (REFLECT 2026-09-29
# 03:39 F21: E0285's no-worse H1 was read out as a gain).
def meaning(decided: str, elo0: object) -> str:
    worse = isinstance(elo0, (int, float)) and elo0 < 0
    return f"{decided} ({({'H1': 'no worse', 'H0': 'worse'} if worse else {'H1': 'a gain', 'H0': 'no gain'})[decided]})"


# The decision SPRT progress `done` of the evaluation in `run` shows, or None while it runs.
def decision(done: dict, run: Path) -> str | None:
    bounds, llr = done.get("bounds") or [], done.get("llr")
    if len(bounds) == 2 and isinstance(llr, (int, float)) and not bounds[0] < llr < bounds[1]:
        return meaning("H1" if llr >= bounds[1] else "H0", read(run / "evaluation.json").get("elo0"))
    return None


# When the stage `stage` of the evaluation in `run`, the one running now, began: the end of the stage before it
# (throughput.stages lists the stages so far, the running one with its latest file's time), or the bot's export.
def stage_since(run: Path, stage: str) -> float | None:
    before = []
    for name, _, end, *_ in throughput.stages(run, {}):
        if name == stage:
            break
        before.append(end)
    if before:
        return before[-1]
    try:
        return created(run / "cand")
    except OSError:
        return None


# ", 131 games/min" from the evaluation's own host status file (its referee run's games since it began, over that
# time), or "" when that file wasn't written in the last RECENT or has no rate.
def games_rate(state: Path, now: float) -> str:
    status = read(state / "remote-status.json")
    updated, rate = status.get("updated"), status.get("per_minute")
    if not isinstance(updated, (int, float)) or now - updated > RECENT or not isinstance(rate, (int, float)) \
            or rate <= 0:
        return ""
    return f", {rate:.0f} games/min"


# ", aimed near 0.47 (120 trials), small 0.52 (118 trials)": the running score (0.5 level with the champion) of each
# map style the idea's own map mix aims at (its declared evaluation.maps, the mix's folder names), from the referee's
# sprt-progress.json per_style (referee/sprt.py Standing), or "" (REFLECT 2026-09-30 15:13 F23: sig asked twice for
# the near-Devil score mid-test). A report: the test decides on every trial.
# ", overall 0.50 (champion 0.52), LLR +2.3 of ±2.94": the running score over every trial (from the referee's bucket
# counts, as referee/sprt.py elo_estimate scores them), the champion's share of the rotation, and the log-likelihood
# ratio against its stopping bound, each left out when sprt-progress.json lacks it (sig read E0451's aimed scores,
# all near 0.50, as level while its LLR stood at +1.5 of 2.94). A report: the test decides on every trial.
def running_score(done: dict) -> str:
    score, counts = "", done.get("counts")
    if isinstance(counts, list) and len(counts) > 1 and all(isinstance(c, int) for c in counts) and sum(counts):
        score = f"overall {sum(i * c for i, c in enumerate(counts)) / (sum(counts) * (len(counts) - 1)):.2f}"
    opponents = done.get("per_opponent") if isinstance(done.get("per_opponent"), dict) else {}
    if isinstance(champion := opponents.get("champion"), (int, float)):
        score += f" (champion {champion:.2f})" if score else f"champion {champion:.2f}"
    shown = [score] if score else []
    if isinstance(llr := done.get("llr"), (int, float)):
        bounds, bound = done.get("bounds"), ""
        if isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(b, (int, float)) for b in bounds):
            bound = f" of ±{bounds[1]:.2f}" if -bounds[0] == bounds[1] else f" of {bounds[0]:+.2f}/{bounds[1]:+.2f}"
        shown.append(f"LLR {llr:+.1f}{bound}")
    return "".join(f", {part}" for part in shown)


def aimed_scores(run: Path, done: dict) -> str:
    declared = ((read(run / "experiment.json").get("idea") or {}).get("evaluation") or {}).get("maps") or {}
    styles = done.get("per_style") if isinstance(done.get("per_style"), dict) else {}
    shown = [f"{name} {row['score']:.2f} ({row['trials']} trials)" for name in declared
             if isinstance(row := styles.get(name), dict) and isinstance(row.get("score"), (int, float))]
    return f", aimed {', '.join(shown)}" if shown else ""


def evaluating(state: Path, now: float, live: list[Path]) -> str:
    runs = state / "runs"
    current = throughput.evaluation_now(state, now) or {}
    experiment, stage = current.get("experiment"), current.get("stage") or "setup"
    progress = next((p for p in reversed(live) if p.parent.name == experiment), None) if experiment else \
        (live[-1] if live else None)
    if progress and stage in ("SPRT", "setup"):
        run, done = progress.parent, read(progress)
        cap = read(run / "evaluation.json").get("max_pairs") or 2400
        start, trials = created(progress), done.get("trials", 0)
        if decided := decision(done, run):
            return (f"Evaluating: {run.name} ({title(run)}): the SPRT reached {decided} at {trials} trials, "
                    f"{hhmm(progress.stat().st_mtime)}")
        rate = trials / max(1.0, (now - start) / 60)
        if now - start < ETA_AFTER:
            until = f"latest finish from {hhmm(start + ETA_AFTER)}"
        elif rate > 0:
            eta = now + (cap - trials) / rate * 60
            until = "overdue" if eta < now else f"latest finish {hhmm(eta)}"
        else:
            until = "no rate yet"
        return (f"Evaluating: {run.name} ({title(run)}) since {hhmm(start)}, {trials}/{cap} trials, "
                f"{rate:.0f} trials/min{games_rate(state, now)}, {until}"
                f"{running_score(done)}{aimed_scores(run, done)}")
    held = read(evaluating_marker()).get("since")
    held = held if isinstance(held, (int, float)) else None
    if not experiment:
        return (f"Evaluating: the evaluation lock is held{f' since {hhmm(held)}' if held else ''} with no bot exported "
                "(a referee refresh, or a test's setup)")
    run = runs / experiment
    if stage in ("judge keep", "rejudge"):  # a recorded result's rejudge holds the lock (throughput.evaluation_now)
        return (f"Evaluating: {experiment} ({title(run)}): {stage}" + (f" since {hhmm(held)}" if held else "")
                + f" (its skipped gates and screen, then promotion){games_rate(state, now)}")
    since = held if stage == "setup" else stage_since(run, stage) if stage != "merge turn" else None
    text = f"Evaluating: {experiment} ({title(run)}): {stage}" + (f" since {hhmm(since)}" if since else "")
    if stage == "setup":
        text += " (referee refresh and rebase, before its bot tests)"
    if stage == "SPRT" and not (run / "sprt-progress.json").exists():
        text += ", no trial yet"
    if stage in ("target batch", "screen"):
        text += games_rate(state, now)
    if stage in ("target batch", "screen") and (run / "sprt-progress.json").exists():  # the SPRT before it, decided or at its cap
        done = read(run / "sprt-progress.json")
        ended = decision(done, run) or "no decision (its cap or the probability rule decides)"
        text += f"; the SPRT ended at {done.get('trials', 0)} trials: {ended}"
    return text


# "final: H0, discarded (not champion; its coder writes a post-mortem, code kept on cand/E0120)" for a recorded
# result: its SPRT decision (or its identical check), a targeted idea's target test, then its verdict and what that
# means for its code (meta.fate).
def final(run: Path) -> str:
    row = read(run / "experiment.json")
    verdict = row.get("verdict")
    if not verdict or verdict == "pending":
        return ""
    sprt = row.get("sprt") or {}
    identity = row.get("identity") or {}
    test = meaning(sprt["decision"], sprt.get("elo0")) if sprt.get("decision") in ("H1", "H0") else sprt.get(
        "decision") or ("same code but the sonar key" if identity.get("same_code") else
                                    "identical check" if identity else "")
    if sprt.get("limit_kept"):
        test = "kept by judgement" if sprt["limit_kept"].get("of") == "judged" else "kept on probability"
    if line := (row.get("target") or {}).get("line"):  # a targeted idea's paired test (driver.target_test)
        test += f", target {line}"
    fate = meta.fate(verdict, run.name)
    return f", final: {test + ', ' if test else ''}{verdict}{f' ({fate})' if fate else ''}"


# "last result: E0414 ended 15:16, final: H0 (no gain), discarded (...), -0.2 Elo over 1494 trials" for the newest
# result recorded in the last LAST_RESULT (a run folder's experiment.json with a verdict and `finished`), or "": an
# evaluation starts at once after it, so the "Evaluating" line would otherwise hide it (REFLECT 2026-09-30 15:13 F7:
# E0396's H0 at 14:08 reached sig only when he asked, at 14:14). The idle line names the last test itself.
LAST_RESULT = 1800


def last_result(runs: Path, now: float) -> str:
    found = []
    for path in runs.glob("E*/experiment.json"):
        try:
            if now - path.stat().st_mtime > LAST_RESULT:  # written at the result, so older is older still
                continue
        except OSError:
            continue
        finished = (row := read(path)).get("finished")
        if row.get("verdict") not in (None, "pending") and isinstance(finished, (int, float)) \
                and 0 <= now - finished <= LAST_RESULT:
            found.append((finished, path.parent, row))
    if not found:
        return ""
    finished, run, row = max(found, key=lambda item: item[0])
    sprt = row.get("sprt") or {}
    estimate = (f", {sprt['elo']:+.1f} Elo over {sprt.get('pairs')} trials"
                if isinstance(sprt.get("elo"), (int, float)) and not isinstance(sprt["elo"], bool) else "")
    return f"last result: {run.name} ended {hhmm(finished)}{final(run)}{estimate}"


# "Idle since" the last test ended: the latest `finished` in results.jsonl among the results that were tested (a cand/
# folder: not a "no code" or "blocked" one). Without such a row, the newest tested run's diff.patch, which is written
# after its result is recorded (driver.cleanup), so it alone gave an older run's time during a handover ("Idle since
# 06:16" at 06:47), else the newest SPRT progress file.
def idle(state: Path, progress: list[Path]) -> str:
    runs = state / "runs"
    ended = sorted((row["finished"], runs / row["id"]) for row in results(state)
                   if isinstance(row.get("finished"), (int, float)) and not isinstance(row["finished"], bool)
                   and isinstance(row.get("id"), str) and re.fullmatch(r"E\d+", row["id"])
                   and (runs / row["id"] / "cand").exists())
    ended = ended or sorted((p.stat().st_mtime, p.parent) for p in runs.glob("E*/diff.patch")
                            if (p.parent / "cand").exists())
    last = ended[-1] if ended else (progress[-1].stat().st_mtime, progress[-1].parent) if progress else None
    text = f"Idle since {hhmm(last[0]) if last else '?'}"
    if last:
        text += f" (last test: {last[1].name} ended {hhmm(last[0])}{final(last[1])})"
    return text


# The official-map report playing now (official-job.json started, not finished), as "official report: E0120 since
# 21:50", or "".
def official_running(runs: Path) -> str:
    jobs = [(job["started_at"], path.parent.name) for path in runs.glob("E*/official-job.json")
            if isinstance((job := read(path)).get("started_at"), (int, float)) and not job.get("finished_at")]
    return f"official report: {max(jobs)[1]} since {hhmm(max(jobs)[0])}" if jobs else ""


# The candidates waiting for evaluation (throughput.waiting_now) as "queued ahead: 2 (~80 min), in order: E0231 coded
# 12:10 (preparing), E0244 coded 14:37" (the minutes: their count times median_evaluation; REFLECT 2026-10-01 03:17 F6,
# health.eval_backlog), "queued ahead: 0" behind an evaluation with none, or "": the order the evaluation lock takes them in, its tickets' names in <loop
# home>/evaluation-queue sorted (driver.first_in_queue): a same-moves test (speed or identical, which takes minutes)
# first, then an idea claimed "next" (driver.NEXT_RANK), then coding-done time, then id (driver.queue_ticket), the
# first two marked "(same-moves)" and "(next)"; one moved ahead of all (driver.py --first) is "(first)". A
# candidate without a ticket (the review of
# orch/builder-c: E0349 waited without one) sorts by the name its ticket would have, from its kind and worker.json's
# coded_at (the file's time before coded_at was recorded); a ticket from before the rank sorts by its kind too. REFLECT 2026-09-28 14:51 F24: the lock had no order, and E0231 waited while
# E0236 and E0237 went first. What holds one back: a stop
# marker, or "preparing": its prepare() step (driver.prepare, beside another evaluation; prepared.json with its bot
# tests still running, or passed with no CPU gate recorded within PREPARE_CPU_S while an evaluation plays), which
# keeps its place and stops once the lock frees (a merge turn started there runs to its end). Every candidate with a
# live ticket (live_tickets) is listed, not only those throughput.waiting_now counts: that drops a worker.json over
# throughput.WAITING_S old, so E0398 and E0399 (coded 08:02 and 08:23 on 30 Sep) left the line at about 20:05 while
# their drivers still waited (REFLECT 2026-09-30 21:15 F8). One waiting longer since coding than the evaluations ahead
# of it take (starving: its place in the queue, plus the one under way, times the median evaluation, median_evaluation)
# is marked "(starving: waited 12.3 h, the 2 ahead take ~2.8 h)": the queue isn't moving it.
PREPARE_CPU_S = 1800  # driver.prepare's CPU gate timeout


# The experiments whose evaluation-queue ticket (driver.enter_queue) is live (meta.ticket_live): their driver waits for
# the lock, however old their worker.json. Read-only.
def live_tickets(state: Path) -> list[str]:
    found = []
    for path in sorted((state / meta.QUEUE).glob("*.json")):
        ticket = read(path)
        if isinstance(exp := ticket.get("experiment"), str) and meta.ticket_live(state, ticket):
            found.append(exp)
    return found


# The median time of the last MEDIAN_OF tested evaluations (TESTED: an error or a blocked one ends in minutes), from
# their bot's export (cand/) to the result's `finished` in experiment.json, as health.median_evaluation reads it from
# results.jsonl (184 MB, too slow here), or None without one. Run folders are read newest experiment.json first, only
# those written in the last MEDIAN_DAYS, until MEDIAN_OF tested ones: parsing all ~2,200 took 2-3 s a status line.
MEDIAN_OF, MEDIAN_DAYS = 10, 3


def median_evaluation(state: Path, now: float) -> float | None:
    written = [(throughput.mtime(cand.parent / "experiment.json") or 0, cand)
               for cand in (state / "runs").glob("E*/cand")]
    times = []
    for at, cand in sorted(written, key=lambda pair: pair[0], reverse=True):
        if now - at > MEDIAN_DAYS * 86400 or len(times) == MEDIAN_OF:
            break
        row = read(cand.parent / "experiment.json")
        finished = row.get("finished")
        if row.get("verdict") in TESTED and isinstance(finished, (int, float)) \
                and (spent := finished - throughput.created(cand)) > 0:
            times.append(spent)
    times.sort()
    return times[len(times) // 2] if times else None


# `live` is live_tickets' answer when the caller has it (line reads it once for this and goal_line).
def queue_text(state: Path, now: float, current: str | None, live: list[str] | None = None) -> str:
    runs = state / "runs"
    def coded(run: str) -> float:
        at = read(runs / run / "worker.json").get("coded_at")
        return at if isinstance(at, (int, float)) else throughput.mtime(runs / run / "worker.json") or 0

    tickets: dict[str, list[float]] = {}  # each ticket's numbers: [coding-done time], or [rank, coding-done time]
    for path in sorted((state / meta.QUEUE).glob("*.json")):
        *numbers, run = path.stem.rsplit("-", 2)  # driver.FIRST_RANK -1 has its own "-"
        try:
            tickets.setdefault(run, [float(n) for n in numbers])
        except ValueError:
            continue

    def order(run: str) -> tuple:
        numbers = tickets.get(run) or [coded(run)]
        quick = (read(runs / run / "experiment.json").get("idea") or {}).get("kind") in throughput.IDENTICAL_KINDS
        return (numbers[0] if len(numbers) == 2 else 0 if quick else 1), numbers[-1], run

    live = live_tickets(state) if live is None else live
    waiting = sorted(set(throughput.waiting_now(state, now, current)) | set(live) - {current},
                     key=order)
    if not waiting:
        return "queued ahead: 0" if current else ""
    typical = median_evaluation(state, now)
    stopped = (state / "STOP").exists() or (state / "ASTRA_STOP").exists()

    def entry(run: str) -> str:
        prepared, path = read(runs / run / "prepared.json"), runs / run / "prepared.json"
        age = now - (throughput.mtime(path) or 0)
        tests = prepared.get("bot_tests")
        preparing = bool(prepared and current) and (tests is None or (tests.get("ok") and "cpu" not in prepared
                                                                            and age < PREPARE_CPU_S))
        why = "stop marker" if stopped else "preparing" if preparing else ""
        numbers = tickets.get(run) or []  # [rank, coded] if ranked: driver.FIRST_RANK -1, QUICK_RANK 0, NEXT_RANK 0.5
        kind = {-1.0: "first", 0.0: "same-moves", 0.5: "next"}.get(numbers[0], "") if len(numbers) == 2 else ""
        ahead = waiting.index(run) + (1 if current else 0)
        waited = now - coded(run)
        starving = (f"starving: waited {waited / 3600:.1f} h, the {ahead} ahead take ~{ahead * typical / 3600:.1f} h"
                    if typical and ahead and waited > (ahead + 1) * typical else "")
        notes = ", ".join(filter(None, (kind, why, starving)))
        return f"{run} coded {hhmm(coded(run))}" + (f" ({notes})" if notes else "")

    ahead = f" (~{len(waiting) * typical / 60:.0f} min)" if typical else ""
    return f"queued ahead: {len(waiting)}{ahead}, in order: " + ", ".join(map(entry, waiting))


def pipeline(state: Path, now: float, current: str | None, note_down: bool = True,
             live: list[str] | None = None) -> list[str]:
    runs = state / "runs"
    parts = []
    if queued := queue_text(state, now, current, live):
        parts.append(queued)
    parts.append(f"coding: {', '.join(coding(runs)) or 'nothing'}")
    if tunes := tuning(runs):
        parts.append(f"tuning: {', '.join(tunes)}")
    if late := late_screen(state):
        parts.append(late)
    parts.append(f"fillers: {', '.join(fillers(state)) or 'none'}")
    if report := official_running(runs):
        parts.append(report)
    if use := throughput.current_use(state, now):
        parts.append(use)
    parts.append(engine_mode(state, now))
    if idle := throughput.idle_hosts(state, now):
        parts.append(idle)
    if note_down and (down := down_once(state, now)):
        parts.append(down)
    return parts


# The remote.json hosts marked "part_time" that are down now, each with its outage's start (host-outages.json);
# read-only, so tools/queue_page.py shows them too.
def part_time_down(state: Path) -> dict[str, float]:
    part_time = [h["name"] for h in read(state / "remote.json").get("hosts") or []
                 if isinstance(h, dict) and h.get("part_time") is True and isinstance(h.get("name"), str)]
    outages = read(state / "host-outages.json")
    return {name: outages[name] for name in part_time if isinstance(outages.get(name), (int, float))}


# "down, only sig can restart it: zz-wsl since 19:58 (16 cores)" for each remote.json host marked "part_time" whose
# outage (host-outages.json, referee/remote.py OUTAGES) this line hasn't named yet, while an evaluation runs, or "".
# part_time silences the host's alert (health.hosts), not this: its cores are lost to the test all the same, and only
# sig can switch his PC on (REFLECT 2026-09-28 20:52 F18: zz-wsl was down unmentioned until sig said "zz should be
# up"). Each outage is named once: SHOWN keeps the start of each one named, by host, and drops those that ended (one
# entry a part-time host at most; health.evalline_outages checks it).
SHOWN = "evalline-outages.json"


def down_once(state: Path, now: float) -> str:
    if not evaluation_running(now):
        return ""
    down, shown = part_time_down(state), read(state / SHOWN)
    new = [name for name in down if shown.get(name) != down[name]]
    if down != shown:
        temporary = state / f"{SHOWN}.tmp"
        temporary.write_text(json.dumps(down))
        temporary.replace(state / SHOWN)
    if not new:
        return ""
    cores = (throughput.last_row(state / throughput.LOG) or {}).get("cores") or {}
    return "down, only sig can restart it: " + ", ".join(
        f"{name} since {hhmm(down[name])}" + (f" ({cores[name]} cores)" if name in cores else "") for name in new)


# A check file's "off" entries as run.forks_off reads them: {} when the file is missing or has none, None when it can't
# be read (which switches that path off too).
def off_entries(path: Path) -> dict | None:
    try:
        found = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    off = found.get("off", {}) if isinstance(found, dict) else None
    return off if isinstance(off, dict) else None


# "judge: native 3/3 places, engine cache on, fork on": how the loop's games play now (REFLECT 2026-09-29 17:32 F4: sig
# asked three times in two hours whether the native judge and the faster engine were in use), from the inputs
# run.native_plan decides on. The native judge is off while the referee checkout's run.NATIVE_JUDGE isn't True (read
# as health.native_judge reads it), native-judge-checks.json has an "off" entry or can't be read (run.natives_off: one
# check that differed stops it everywhere), the live key (live_key: native_judge.key() of the referee checkout) can't
# be read, or a check once found that key differing (the file's "differed_keys", run.differed_keys); else it counts
# the places the loop plays at, as the last native_fidelity.py --gate-missing run listed them (enabled.json's
# "wanted"), whose gate for the live key passed: this Mac's own place on its Mac hosts' gate when it has no row of its
# own, a failed one too (run.native_gated), no other place. The engine cache is off while engine-cache-off.json
# stands (the next run that can use the cache removes it, run._engine_launcher); forking is off while
# fork-server-checks.json has an "off" entry or can't be read (run.forks_off), and "fell back hh:mm" while a run's
# fork-server-off.json note is under health.FORK_OFF_AGE old (runs overlap, so it is never removed).
PLATFORM = f"{os.uname().sysname} {os.uname().machine}"  # remote.PLATFORM: this Mac, where evalline runs


# The native judge's key as the referee checkout's native_judge.py computes it now (run.native_plan's "now"), or None
# when that can't be read: about 0.03 s (review of orch/builder-c).
def live_key(state: Path) -> str | None:
    folder = state / "referee" / "referee"
    if not (folder / "native_judge.py").exists():
        return None
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    try:
        import native_judge
        return native_judge.key()
    except Exception:  # a broken checkout never stops the status line
        return None


def engine_mode(state: Path, now: float) -> str:
    import health
    try:
        on = bool(re.search(r"^NATIVE_JUDGE = True\b", (state / "referee/referee/run.py").read_text(), re.M))
    except OSError:
        on = False
    enabled = read(state / "native-judge" / "enabled.json")
    wanted = enabled.get("wanted") if isinstance(enabled.get("wanted"), dict) else {}
    places = wanted.get("places") if isinstance(wanted.get("places"), dict) else {}
    keys = enabled.get("keys") if isinstance(enabled.get("keys"), dict) else {}
    checks = read(state / "native-judge-checks.json")
    differed = checks.get("differed_keys", [])
    key = live_key(state) if on else None
    gated = keys.get(key) if isinstance(keys.get(key), dict) else {}

    def passed(place: str) -> bool:
        row = gated.get(f"{place} host" if place == PLATFORM and place not in gated else place)
        return isinstance(row, dict) and row.get("passed") is True

    if not on:
        native = "native off (run.NATIVE_JUDGE)"
    elif off_entries(state / "native-judge-checks.json") != {}:
        native = "native off (a check switched it off: native-judge-checks.json)"
    elif key is None:
        native = "native off (its key can't be read: native_judge.key)"
    elif not isinstance(differed, list) or key in differed:
        native = "native off (a check found its key differing: native-judge-checks.json differed_keys)"
    elif not places:
        native = "native on, no places listed yet"
    else:
        native = f"native {sum(map(passed, places))}/{len(places)} places" + (
            "" if gated else " (no gate for the live key yet)")
    cache = "engine cache off" if (state / "engine-cache-off.json").exists() else "engine cache on"
    fork = "fork on" if off_entries(state / "fork-server-checks.json") == {} else \
        "fork off (a check mismatched: fork-server-checks.json)"
    fell = read(state / "fork-server-off.json").get("at")
    if fork == "fork on" and isinstance(fell, (int, float)) and now - fell < health.FORK_OFF_AGE:
        fork += f" (a run fell back {hhmm(fell)})"
    return f"judge: {native}, {cache}, {fork}"


# The game-only fillers whose background run is alive now (their records' pid, as the upkeep's ticks keep them).
def fillers(state: Path) -> list[str]:
    return [name for name, file in FILLERS if meta.alive(read(state / file).get("pid"))]


# The late screen playing now (loop/rescreen.py; rescreen.json by champion), as "late screen: E0141's champion since
# 12:24 (2 entries done)", or "".
def late_screen(state: Path) -> str:
    running = [record for record in read(state / "rescreen.json").values()
               if isinstance(record, dict) and meta.alive(record.get("pid"))]
    if not running:
        return ""

    def started(record: dict) -> float:
        value = record.get("started_at")
        return value if isinstance(value, (int, float)) else 0.0

    record = max(running, key=started)
    done = record.get("done")
    return (f"late screen: {record.get('experiment') or '?'}'s champion since {hhmm(record.get('started_at'))} "
            f"({len(done) if isinstance(done, dict) else 0} entries done)")


# The orchestrator's background agents running now, from its own session transcript (<loop home>/orchestrator-session
# names it, as the Stop hook reads it): each Agent call with no result and no stop notice yet (my_messages.agent_events),
# started in the last AGENT_HOURS, the newest MAX_AGENTS, as (description, start). REFLECT 2026-09-29 20:42 F15: the
# --agent-start registration this replaces was never used, so no status line from 17:32 to 20:42 had an agents line.
# The transcript (240 MB on 29 Sep) is read on from where the last call stopped: AGENTS keeps its path, that byte
# offset and the calls still running (at most MAX_AGENTS entries, a few KB), written whole through a temporary file.
# Without it the read starts AGENT_HOURS back (my_messages.tail_start: about 1 s then, where the whole file took 24 s).
# `save` False, for a read-only reader (tools/queue_page.py), leaves the file as it is and reads nothing without it.
AGENT_CALL = re.compile(rb'"name":\s*"(?:Agent|Task)"')  # a line that may hold an Agent call (my_messages.AGENT_TOOLS)


def running_agents(state: Path, now: float, save: bool = True, transcript: Path | None = None) -> list[tuple[str, float]]:
    if str(REPO / "tools") not in sys.path:
        sys.path.insert(0, str(REPO / "tools"))
    import my_messages
    if transcript is None:
        try:
            transcript = my_messages.PROJECT / f"{(state / 'orchestrator-session').read_text().strip()}.jsonl"
        except OSError:
            return []
    saved = read(state / AGENTS)
    running = saved.get("running") if isinstance(saved.get("running"), dict) else {}
    offset = saved.get("offset") if type(saved.get("offset")) is int else 0
    try:
        if saved.get("transcript") != str(transcript) or offset > transcript.stat().st_size:  # none, or another file
            if not save:
                return []
            running = {}
            offset = my_messages.tail_start(transcript, datetime.fromtimestamp(now - AGENT_HOURS * 3600).astimezone())
        calls = None  # the running calls' ids as one pattern, rebuilt when they change
        with transcript.open("rb") as handle:
            handle.seek(offset)
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break  # the session is still writing it
                offset += len(raw)
                if calls is None and running:  # a result names the call, a notice the call or its agent
                    calls = re.compile(b"|".join(re.escape(str(key).encode()) for call, row in running.items()
                                                 for key in (call, row.get("agent")) if key))
                if not AGENT_CALL.search(raw) and not (calls and calls.search(raw)):
                    continue
                try:
                    entry = json.loads(raw)
                    at = datetime_seconds(entry["timestamp"])
                except (ValueError, KeyError, TypeError):
                    continue
                for kind, call, what in [] if entry.get("isSidechain") else my_messages.agent_events(entry):
                    if kind == "start":
                        running[call] = {"what": what[:MAX_WHAT], "started": at}
                    elif kind == "launched" and call in running:
                        running[call]["agent"] = what
                    else:
                        running = {key: row for key, row in running.items()
                                   if key != call and not (what and row.get("agent") == what)}
                    calls = None
    except OSError:
        return []
    running = dict(sorted(((call, row) for call, row in running.items() if isinstance(row, dict)
                           and isinstance(row.get("started"), (int, float))
                           and now - row["started"] <= AGENT_HOURS * 3600),
                          key=lambda item: item[1]["started"])[-MAX_AGENTS:])
    if save:
        temporary = state / f"{AGENTS}.tmp"
        temporary.write_text(json.dumps({"transcript": str(transcript), "offset": offset, "running": running}))
        temporary.replace(state / AGENTS)
    return [(str(row.get("what", "")), row["started"]) for row in running.values()]


def datetime_seconds(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


# "5 h usage 52%, resets 22:00" from <loop home>/usage-share.json, which tools/usage_statusline.py writes from Claude
# Code's status line input (its rate_limits.five_hour: used_percentage, resets_at; REFLECT 2026-09-29 20:42 F23: sig
# had to say "were at 50% of our 5 hour usage"), with its time when it is over USAGE_FRESH old, or "" when there is
# none or its window has reset since.
USAGE = "usage-share.json"
USAGE_FRESH = 600


# The window's level, when it has one (loop/quota.py's marker), follows: "; level 95% since 21:40: the loop is paused:
# ... (coders paused: E0401)".
def usage_text(state: Path, now: float) -> str:
    share = read(state / USAGE)
    used, resets, at = share.get("used_percentage"), share.get("resets_at"), share.get("at")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (used, resets, at)) or resets <= now:
        return ""
    text = f"5 h usage {used:.0f}%" + (f" at {hhmm(at)}" if now - at > USAGE_FRESH else "") + f", resets {hhmm(resets)}"
    if not (level := quota.marker(state, now)):
        return text
    since = level.get("since")
    text += (f"; level {level.get('level')}" + (f" since {hhmm(since)}" if isinstance(since, (int, float)) else "")
             + f": {quota.WAITS.get(min(level['rank'], quota.PAUSE), '')}")
    if level["rank"] >= quota.PAUSE:
        paused = sorted(run.name for run in (state / "runs").glob("E*")
                        if read(run / "worker.json").get("status") == "paused")
        text += f" (coders paused: {', '.join(paused) or 'none'})"
    return text


# "agents: Review orch/builder-c since 14:10, ...; watcher: running (pid 438 since 20:44); tests waiting on the load
# gate: pid 11965 14 min (...); 5 h usage 52%, resets 22:00" for the orchestrator's own background agents
# (running_agents), `watcher` (watcher_text's), `waits` (gate_waits' text) and the usage share (usage_text), or None
# when there are none of them.
def agents_line(state: Path, now: float, waits: str = "", save: bool = True, watcher: str = "") -> str | None:
    rows = running_agents(state, now, save)
    parts = [*([watcher] if watcher else []), *([waits] if waits else []),
             *([usage] if (usage := usage_text(state, now)) else [])]
    if not rows and not parts:
        return None
    return "; ".join(["agents: " + (", ".join(f"{what} since {hhmm(at)}" for what, at in rows) or "none"), *parts])


# The tests held by tools/gated_test.sh for GATE_WAIT or longer (health.gate_waiting, from ps), longest first, as
# "tests waiting on the load gate: pid 11965 14 min (.venv/bin/python -m unittest ...)", or "" (REFLECT 2026-09-28
# 20:52 F11: merges and tests waited unseen until sig asked why they took so long).
GATE_WAIT = 60


# "watcher: running (pid 438 since 20:44)" while the orchestrator's alerts watcher (tools/orch/watch.sh, which runs as
# `bash <tmp>/watch.XXXXXX`, its private copy) runs, else "watcher: not running; re-arm tools/orch/watch.sh": the
# agents line carries it, so a status reply without it fails --check-evalline (REFLECT 2026-09-30 21:15 F4: the
# watcher had lapsed unseen at 19:45).
WATCHER = re.compile(r"^(?:\S*/)?bash \S*/watch\.[A-Za-z0-9]{6}$")


def watcher_text(rows: list | None = None, now: float | None = None) -> str:
    import health
    found = [r for r in (health.ps_rows() if rows is None else rows) if WATCHER.search(r.args)]
    if not found:
        return "watcher: not running; re-arm tools/orch/watch.sh"
    oldest = max(found, key=lambda r: r.alive)
    return f"watcher: running (pid {oldest.pid} since {hhmm((time.time() if now is None else now) - oldest.alive)})"


def gate_waits(rows: list | None = None) -> str:
    import health
    waiting = sorted((r for r in health.gate_waiting(health.ps_rows() if rows is None else rows)
                      if r.alive >= GATE_WAIT), key=lambda r: -r.alive)
    return "tests waiting on the load gate: " + ", ".join(
        f"pid {r.pid} {r.alive / 60:.0f} min ({(r.args.split(maxsplit=2)[2:] or [''])[0][:MAX_WHAT]})"
        for r in waiting) if waiting else ""


# "live: v0032 (site v31); waiting to upload: v0033 (not on the site), next slot 02:51" while the champion isn't the
# verified live submission (publishing.json), or None. The next slot is when the oldest upload in the site's rolling
# window (uploads.jsonl, ladder.UPLOAD_WINDOW_S) leaves it, once they fill its budget (ladder.MAX_UPLOADS). While a
# publishing hold stands (publishing.active_hold) it is "live: v0078 (site v76); held: v0078 until 12:00" instead.
def live_line(state: Path, now: float, repo: Path = REPO) -> str | None:
    import publishing
    verified = read(state / "publishing.json").get("verified") or {}
    live = publishing.site_label(state, repo, verified["commit"]) if verified.get("commit") else "none verified"
    try:
        hold = publishing.active_hold(state, now)
    except ValueError:
        return f"live: {live}; held: {publishing.HOLD} unreadable, uploading nothing"
    if hold:
        return f"live: {live}; held: {hold['version']} until {hhmm(datetime.fromisoformat(hold['until']).timestamp())}"
    if not verified.get("commit"):
        return None
    champion = subprocess.run(["git", "rev-parse", "loop/champion"], cwd=repo, capture_output=True,
                              text=True).stdout.strip()
    if not champion or champion == verified["commit"]:
        return None
    text = f"live: {live}; waiting to upload: {publishing.site_label(state, repo, champion)}"
    try:
        rows = [json.loads(line) for line in (state / "uploads.jsonl").read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        rows = []
    window = publishing.ladder.UPLOAD_WINDOW_S
    recent = sorted(r["at"] for r in rows if isinstance(r.get("at"), (int, float)) and r["at"] >= now - window)
    if len(recent) >= publishing.ladder.MAX_UPLOADS:
        text += f", next slot {hhmm(recent[0] + window)}"
    return text


# sig's stated goal as the map styles work aimed at it declares, and the goal line's count of it (goal_line), and
# the ladder maps it names (GOAL_MAPS, health.goal_maps). 29 Sep ~23:40: "our biggest priority is making sure we start
# doing well on the maps with portals"; 30 Sep 19:59:53: "we need to fix trauma portals and devil. ASAP". Devil (32x16,
# 512 tiles) is a contested-middle map: two walled halves meeting through a few gaps, which the fortress style's gate
# kind builds (mapgen/gen.py build_fortress), and a small map, which the small style varies (near variants of the
# ladder and community maps of up to 1024 tiles, Devil among 9; rusher, the mix opponent that holds the middle, is
# paired with it). "near" isn't one: it varies every ladder and Sprint map alike. A change of goal is sig's: edit these
# with his words and date.
GOAL = "Trauma, Portals and Devil (sig, 30 Sep 19:59)"
GOAL_STYLES = (*meta.PORTAL_STYLES, "fortress", "small")
GOAL_MAPS = ("Trauma", "Portals", "Devil")
GOAL_TOP = 10  # queued ideas counted, in claim order


# Whether experiment `run`'s test aims at a GOAL_STYLES style: its idea declares one in its map mix, or its frozen
# policy's mix gives one more than its general share (meta.general_shares: a coder's own mix, driver.apply_coder_mix).
def aims_at_goal(run: Path) -> bool:
    record = read(run / "experiment.json")
    declared = ((record.get("idea") or {}).get("evaluation") or {}).get("maps") or {}
    frozen = (record.get("evaluation") or {}).get("maps") or {}
    general = meta.general_shares()
    return any(style in declared for style in GOAL_STYLES) or any(
        isinstance(frozen.get(style), (int, float)) and frozen[style] > general.get(style, 0) + 1e-9
        for style in GOAL_STYLES)


# "goal: Trauma, Portals and Devil (sig, 30 Sep 19:59): in flight 2 of 4 aim at a goal style (E0406, E0407); top 10
# queued 3 (I1079,
# ...)": how much of the work under way and next serves sig's stated goal, so a drift away from it shows on every
# status line rather than when sig asks (REFLECT 2026-09-30 09:12 F7, MISSES 104: in-flight work moved step by step
# from portal crossing to king length). In flight is the experiment under evaluation, those queued for it and those
# being coded or tuned; queued is the first GOAL_TOP ready ideas in claim order (claim_sequence) declaring a
# GOAL_STYLES style in their map mix. An aim is what the test plays, not what the idea is about: an idea on portal
# play testing on general maps isn't counted. `flight` (goal_flight) is shared with health.goal_drift.
def goal_line(state: Path, now: float, current: str | None, live: list[str] | None = None) -> str:
    flight, aimed = goal_flight(state, now, current, live)
    sequence = claim_sequence(read_ideas(state / "ideas.jsonl"))
    top = sequence[:GOAL_TOP]
    queued = [i["id"] for i in top if any(style in (((i.get("evaluation") or {}).get("maps")) or {})
                                          for style in GOAL_STYLES)]
    return (f"goal: {GOAL}: in flight {len(aimed)} of {len(flight)} aim at a goal style"
            f"{' (' + ', '.join(aimed) + ')' if aimed else ''}; top {len(top)} queued {len(queued)}"
            f"{' (' + ', '.join(queued) + ')' if queued else ''}{rank_text(state, sequence)}")


# (the experiments in flight, those of them aiming at the goal): under evaluation (`current`), queued for it
# (throughput.waiting_now, and any with a live queue ticket: live_tickets, or `live` when the caller read it), being
# coded or tuned.
def goal_flight(state: Path, now: float, current: str | None,
                live: list[str] | None = None) -> tuple[list[str], list[str]]:
    runs = state / "runs"
    flight = sorted({*([current] if current else []), *throughput.waiting_now(state, now, current),
                     *(live_tickets(state) if live is None else live), *coding(runs), *tuning(runs)} - {None})
    return flight, [run for run in flight if aims_at_goal(runs / run)]


# "; out of sig's ranking of 11:10: I1108 ahead of I970, I1118 ahead of I970" for the ready ideas claimed ahead of a
# ranked one they shouldn't be (meta.out_of_rank over the recorded ranking, meta.RANKING), at most RANK_SHOWN, or ""
# (REFLECT 2026-09-30 15:13 F2: three ideas jumped the 11:10 re-rank unseen). One placed there on purpose, with
# `driver.py --out-of-rank` or any orchestrator note since the ranking (meta.noted_since), isn't marked.
RANK_SHOWN = 5


def rank_text(state: Path, sequence: list[dict]) -> str:
    ranking = meta.read_ranking(state)
    if not (found := meta.out_of_rank(sequence, ranking)):
        return ""
    shown = ", ".join(f"{idea} ahead of {ranked}" for idea, ranked in found[:RANK_SHOWN])
    more = f" and {len(found) - RANK_SHOWN} more" if len(found) > RANK_SHOWN else ""
    return f"; out of sig's ranking of {hhmm(ranking.get('at'))}: {shown}{more}"


# The status line's start ("Evaluating: ..." or "Idle since ...") and the experiment under evaluation, if any.
def head(state: Path, now: float) -> tuple[str, str | None]:
    progress = sorted((state / "runs").glob("E*/sprt-progress.json"), key=lambda p: p.stat().st_mtime)
    live = [p for p in progress if now - p.stat().st_mtime < RECENT]
    if evaluation_running(now):
        return evaluating(state, now, live), (throughput.evaluation_now(state, now) or {}).get("experiment")
    return idle(state, progress), None


# The status line; `waits` is gate_waits' text and `watcher` watcher_text's, which main reads from this Mac's process
# table. `note_down` False,
# for a read-only reader (tools/queue_page.py), leaves out both parts that write: down_once (SHOWN) and the agents
# line's saved transcript offset (AGENTS, via agents_line's `save`).
def line(state: Path, now: float, waits: str = "", note_down: bool = True, watcher: str = "") -> str:
    start, current = head(state, now)
    last = last_result(state / "runs", now) if start.startswith("Evaluating") else ""
    live = live_tickets(state)
    text = "; ".join([start, *([last] if last else []), *pipeline(state, now, current, note_down, live)])
    if second := live_line(state, now):
        text += "\n" + second
    text += "\n" + goal_line(state, now, current, live)
    if third := agents_line(state, now, waits, save=note_down, watcher=watcher):
        text += "\n" + third
    return text


# When idea `idea_id` would be claimed and have its result, from a query run now (REFLECT 2026-09-29 12:53 F2: "none
# of these will land before the Sprint freeze", said without measuring, was wrong): its place among the ready ideas in
# claim order (claim_sequence, a prerequisite lifted to its waiters' place; fillers also go first
# while no candidate waits, so it reads early then), over the results an hour the loop ran in the last ETA_WINDOW
# (results.jsonl's rows finished then, found through the run folders written then: meta.result_rows, over the hours
# the utilisation record has rows, one a minute while a loop runs), plus the median claim-to-result time of those
# tested (TESTED: every row frees a lane, but a no-code or blocked one ends in minutes). Times are the earliest: new
# priority-1 ideas (follow-ups, reviews) queue ahead. Says so when the idea isn't ready (in flight, done, or waiting on
# a prerequisite).
ETA_WINDOW = 24 * 3600
TESTED = ("kept", "promising", "discarded")


# The backlog's rows at `path` that have a string id and status; any other line is skipped.
def read_ideas(path: Path) -> list[dict]:
    ideas = []
    for text in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("status"), str):
            ideas.append(row)
    return ideas


# The ready ideas (driver.ready_ideas) in the order lanes claim them (driver.claim_sequence): ideas marked next and
# re-evaluations first, then driver.claim_order, a prerequisite at its waiters' best priority, rank and next flag
# (driver.waiter_ranks). A lane's own rules (idle fillers, the goal rule: driver.claim_candidates) aren't applied.
def claim_sequence(ideas: list[dict]) -> list[dict]:
    import driver  # lazily: the status line needs none of it
    return driver.claim_sequence(ideas)


def eta(state: Path, idea_id: str, now: float) -> str:
    ideas = read_ideas(state / "ideas.jsonl")
    idea = next((i for i in ideas if i["id"] == idea_id), None)
    if idea is None:
        return f"{idea_id}: not in the backlog"
    order = claim_sequence(ideas)
    if idea not in order:
        state_text = "waiting on a prerequisite or a condition (driver.ready_ideas)" if idea["status"] == "todo" \
            else idea["status"]
        return f"{idea_id}: not ready to claim: {state_text}"
    place = order.index(idea) + 1
    recent = [run.name for run in (state / "runs").glob("E*")
              if (run / "experiment.json").exists() and now - (run / "experiment.json").stat().st_mtime < ETA_WINDOW]
    rows = [row for row in meta.result_rows(state / "results.jsonl", recent).values()
            if isinstance(row.get("finished"), (int, float)) and now - row["finished"] < ETA_WINDOW]
    head = (f"{idea_id} is {place} of {len(order)} ready in claim order (next and re-evaluations first, then "
            f"driver.claim_order, a prerequisite as its waiters; P1-P3 fillers go before the rest while nothing is queued or under evaluation)")
    if not rows:
        return f"{head}; no result in the last {ETA_WINDOW // 3600} h to measure a rate from"
    minutes = len([row for row in throughput.read_rows(state, since=now - ETA_WINDOW) if row.get("at", 0) <= now])
    hours = min(ETA_WINDOW / 3600, minutes / 60) if minutes >= 60 else ETA_WINDOW / 3600
    rate = len(rows) / hours
    spans = sorted(row["finished"] - row["started"] for row in rows if isinstance(row.get("started"), (int, float))
                   and row.get("verdict") in TESTED)
    claimed = now + place / rate * 3600
    text = (f"{head}; {rate:.1f} results an hour over the {hours:.1f} h the loop ran in the last "
            f"{ETA_WINDOW // 3600} h ({len(rows)} results), so claimed in {(claimed - now) / 3600:.1f} h at the "
            f"earliest (~{hhmm(claimed)}; new priority-1 ideas queue ahead)")
    if spans:
        median = statistics.median(spans)
        text += (f", its result about {median / 3600:.1f} h after (the median claim to result of the {len(spans)} "
                 f"tested: {', '.join(TESTED)}), ~"
                 f"{time.strftime('%a %H:%M', time.localtime(claimed + median))}")
    return text


# --queue (REFLECT 2026-09-29 17:32 F4: sig asked four times in two hours for the P1 list in order, what is under
# test and queued, and when P1 clears), measured now: the status line's start (head), the candidates queued for the
# evaluation lock in its order (queue_text), priority 1's flow and every waiting P1 idea in claim order
# (claim_sequence; ideas marked next and re-evaluations, of any priority, go first; a prerequisite counts at its
# waiters' priority: driver.waiter_ranks), then those waiting on a
# prerequisite or a condition. The flow compares the waiting P1 ideas (status todo, priority 1) now with those in the
# review snapshot (each review's before.jsonl, <loop home>/meta/<its ns time>) nearest FLOW_HOURS ago and at least an
# hour old: "in" counts new and raised ideas, "out" claimed, merged, dropped and lowered ones, with the sign test's
# chance of a split that uneven between them by luck (MISSES 34); P1 clears at the net rate when out beats in, else it
# isn't shrinking.
FLOW_HOURS = 6


# (when, the waiting P1 ids then) of the review snapshot --queue measures the flow from, or None.
def p1_snapshot(state: Path, now: float) -> tuple[float, set[str]] | None:
    taken = sorted((int(folder.name) / 1e9, folder / "before.jsonl") for folder in (state / "meta").glob("*")
                   if folder.name.isdigit() and (folder / "before.jsonl").exists())
    old = [row for row in taken if row[0] <= now - 3600]
    if not old:
        return None
    at, path = ([row for row in old if row[0] <= now - FLOW_HOURS * 3600] or old[:1])[-1]
    return at, {i["id"] for i in read_ideas(path) if i["status"] == "todo" and i.get("priority") == 1}


def queue_report(state: Path, now: float) -> str:
    import driver
    start, current = head(state, now)
    ideas = read_ideas(state / "ideas.jsonl")
    by_id = {i["id"]: i for i in ideas}
    sequence = claim_sequence(ideas)
    lifted = driver.waiter_ranks(ideas)  # a prerequisite's priority and next flag, as it is claimed
    priority = lambda i: lifted.get(i["id"], i).get("priority")
    waiting = [i for i in ideas if i["status"] == "todo" and priority(i) == 1]
    order = [i for i in sequence
             if priority(i) == 1 or i.get("next") or i.get("reevaluate") or lifted.get(i["id"], {}).get("next")]
    ready = {i["id"] for i in order}
    held = [i for i in waiting if i["id"] not in ready]
    lines = [f"Under test: {start}", f"Evaluation queue: {queue_text(state, now, current) or 'empty'}"]
    flow = f"P1: {len(waiting)} waiting ({len(waiting) - len(held)} ready, {len(held)} on a prerequisite or condition)"
    if (snapshot := p1_snapshot(state, now)) is None:
        flow += "; no review snapshot an hour old or more to measure its flow from"
    else:
        at, then = snapshot
        hours = (now - at) / 3600
        now_ids = {i["id"] for i in waiting}
        came, went = len(now_ids - then), len(then - now_ids)
        net = (came - went) / hours
        import chance
        flow += (f"; over the {hours:.1f} h since the {time.strftime('%a %H:%M', time.localtime(at))} review "
                 f"snapshot {came} came in and {went} went out (claimed, merged, dropped or lowered; sign p "
                 f"{chance.sign_p(max(came, went), min(came, went)):.2g}): net {net:+.1f} an hour, ")
        if net < 0 and waiting:
            clear = len(waiting) / -net
            flow += (f"clears in about {clear:.0f} h (~{time.strftime('%a %H:%M', time.localtime(now + clear * 3600))}) "
                     "if both rates hold")
        else:
            flow += "not shrinking" if waiting else "empty"
    lines += [flow, "P1 in claim order (lanes claim from the top: ideas marked next and re-evaluations first; then, on "
                    "a supervised claim while nothing is queued or under evaluation, P1-P3 fillers: driver.idle_fillers):"]
    jumped = dict(meta.out_of_rank(sequence, meta.read_ranking(state)))
    for n, idea in enumerate(order, 1):
        marks = [*([f"P{idea.get('priority')}"] if idea.get("priority") != 1 else []),
                 *([f"out of sig's ranking, ahead of {jumped[idea['id']]}"] if idea["id"] in jumped else []),
                 *(["next"] if idea.get("next") else []), *(["re-evaluation"] if idea.get("reevaluate") else []),
                 *([str(idea["kind"])] if idea.get("kind") else [])]
        lines.append(f"  {n}. {idea['id']}{' (' + ', '.join(marks) + ')' if marks else ''}: "
                     f"{str(idea.get('title', ''))[:90]}")
    if held:
        lines.append("Waiting on a prerequisite or condition (driver.ready_ideas):")
        for idea in held:
            on = [name for name in driver.prerequisites(idea, by_id)
                  if (by_id.get(name) or {}).get("status", "").startswith(("todo", "trying:"))]
            lines.append(f"  {idea['id']} waits {', '.join(on) if on else 'on a condition'}: "
                         f"{str(idea.get('title', ''))[:90]}")
    return "\n".join(lines)


# Each result recorded since `since` (a run folder's experiment.json with a verdict and a `finished` at or after it),
# oldest first, one a line: "E0431 (I1139 Retest ...) ended 20:40: discarded, H0 (no gain), -4.6 Elo [-9.9, +0.7]
# over 696 trials", or "no results since 20:00". The answer to "what are the eval results since I last asked", which
# sig asked four times on 30 Sep (REFLECT 2026-09-30 21:15 F6): `--results-since` with his last message's time.
def results_since(state: Path, since: float) -> str:
    found = []
    for path in (state / "runs").glob("E*/experiment.json"):
        if (throughput.mtime(path) or 0) < since:  # written at the result, so an older file holds no newer one
            continue
        row = read(path)
        if row.get("verdict") not in (None, "", "pending") and isinstance(finished := row.get("finished"), (int, float)) \
                and finished >= since:
            found.append((finished, path.parent, row))
    if not found:
        return f"no results since {hhmm(since)}"
    lines = []
    for finished, run, row in sorted(found, key=lambda item: item[0]):
        sprt = row.get("sprt") or {}
        number = lambda key: isinstance(sprt.get(key), (int, float)) and not isinstance(sprt[key], bool)
        estimate = ""
        if number("elo"):
            estimate = f", {sprt['elo']:+.1f} Elo" + (f" [{sprt['elo_lo']:+.1f}, {sprt['elo_hi']:+.1f}]"
                                                      if number("elo_lo") and number("elo_hi") else "")
            estimate += f" over {sprt.get('pairs')} trials"
        lines.append(f"{run.name} ({title(run)}) ended {hhmm(finished)}{final(run).replace(', final:', ':', 1)}"
                     f"{estimate}")
    return "\n".join(lines)


# A --results-since argument as a time: "20:00" (today's, or yesterday's when that is still to come) or an ISO time.
def since_time(text: str, now: float) -> float:
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
        clock = datetime.fromtimestamp(now).astimezone()
        parts = [int(p) for p in text.split(":")] + [0]
        at = clock.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0).timestamp()
        return at - 86400 if at > now else at
    at = datetime.fromisoformat(text)
    return (at if at.tzinfo else at.astimezone()).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eta", metavar="IDEA", help="when an idea would be claimed and have its result, measured now")
    parser.add_argument("--queue", action="store_true",
                        help="what is under test and queued, P1's flow and every waiting P1 idea in claim order")
    parser.add_argument("--results-since", metavar="TIME",
                        help="each result recorded since TIME (HH:MM today, or ISO), oldest first (results_since)")
    args = parser.parse_args()
    if args.results_since:
        print(results_since(loop_home(), since_time(args.results_since, time.time())))
    elif args.queue:
        print(queue_report(loop_home(), time.time()))
    elif args.eta:
        print(eta(loop_home(), args.eta, time.time()))
    else:
        import health
        rows = health.ps_rows()
        print(line(loop_home(), time.time(), gate_waits(rows), watcher=watcher_text(rows)))
    sys.exit(0)


if __name__ == "__main__":
    main()
