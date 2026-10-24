"""How well the game machines are used (loop/LOOP.md "Throughput", MISSES 35): games in flight
against each host's capacity every minute, and each result's wall time and games a minute per stage.

The utilisation record, `<loop home>/utilisation.jsonl`: one row a minute (`sample`), written by the upkeep's
sampler thread (`sampling`, started by maintenance.checking, its stand-in and native.py's watch, which run whenever either loop
does, so rows keep coming while nothing plays or a candidate waits), the newest CAP rows kept. Each row says, per
game machine ("here" is this Mac), its capacity (the referee's own notion: this Mac's slots for the loop's niced
games, home.niced_slots, or under the load gate, home.mac_slots; a remote host's cores, which it fills before growing
towards its `max`, else max / 2, since remote.json's `max` is about twice a host's logical CPUs; 0 while it is down),
the games in flight there and
for whom (the evaluation, a coder, the sweep, the prewarm, a tune, a late screen, an official-map report, a review's
rule counts), plus the evaluation under way and its stage, the candidates waiting for it (`waiting`), the experiments
being coded (`coding`, since 29 Sep), the idea priority of each of those (`priorities`) and the backlog's waiting
ideas by priority (`backlog`), which tools/queue_page.py's chart draws, and whether its writer ran older loop code
than the disk's (`stale`, since 29 Sep; a row without it is older still: a current sampler replaces either, `sample`).
In-flight counts come from the status files every referee run writes (referee/run.py Scheduler.save: `running` here, `active` and `cores`
per host, rewritten at least every run.STATUS_S, 10 s, while games run). A status file from a referee older than those fields is
estimated from its games finished since the last row (Little's law, at GAME_S a game), and the row says so. Every
file is checked before use (`cleaned`): a coder's, written from its sandbox, is untrusted, and one bad file is left
out of its row rather than stopping the record.

health.py alerts on a high idle share averaged over several minutes (`idle_spells`); evalline.py shows the latest row (`current_use`); the
review's stats.md lists each recent result's stage times (`stage_lines`), derived from the files each evaluation
already leaves in runs/E####/, never from utilisation.jsonl.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

import meta
from home import CPU, LAPSE_S, MAC_HOSTS, clones_folder, current_code, evaluating_marker, evaluation_running, load_share, \
    mac_slots, niced_slots, published_split, reclaimable_gb, sessions_full

LOG = "utilisation.jsonl"
CAP = 1440  # rows kept: 24 hours of minutes
FUTURE_S = 5  # a status file dated further ahead than this is ignored (a clock can't run ahead; a forged one can)
MOST_GAMES = 10_000  # any count in a status file above this is ignored
SAMPLE_S = 60
LIVE_S = 90  # a status file rewritten this recently belongs to a run playing now (runs rewrite it every 10 s)
GAP_S = 3 * SAMPLE_S  # rows further apart than this don't continue one another (the sampler was down)
# Seconds one game takes, for estimating games in flight from games finished when a status file has no counts: the
# mean of E0127's 3,300 screen games was 11.3 s and of E0125's 1,008 SPRT games 9.7 s (measured 2026-09-27).
GAME_S = 11
# health.py alerts when the capacity's idle share, averaged over IDLE_MINUTES while an evaluation runs or a candidate
# waits, is at least IDLE_SHARE. A mean, not every minute: E0163's SPRT stalled every other minute (busy 21, 58, 18,
# 2, 38, 59 ... of ~60 slots), 0.40 idle over 29 min, and never had 3 minutes in a row at 0.4, the old every-minute
# rule. Every SPRT of 10 minutes or more from E0135 to E0163 was 0.26-0.63 idle on average over its 10-minute windows,
# nearly all of it the scheduler's head-of-line stall (referee/run.py AHEAD): E0143's, E0158's and E0163's games
# replayed at their real durations without it idle at most 0.07-0.18 over any 10 minutes, the last minutes' drain
# included.
IDLE_SHARE = 0.25
IDLE_MINUTES = 10
# evalline.py names a remote host with free slots that played no game in this many rows in a row while the evaluation
# plays on the hosts (REMOTE_STAGES; REFLECT 2011 F7: az-mel and az-hk at 0 games were to be checked "at the next
# status line" and weren't). E0134's screen, 2026-09-27 01:37-02:00, left sz-wsl and six az-* hosts idle for 24 rows.
IDLE_HOST_ROWS = 10
REMOTE_STAGES = ("SPRT", "target batch", "screen")  # health.START_STAGES
WAITING_S = 12 * 3600  # a coder that finished this long ago with nothing recorded is not waiting any more
IDENTICAL_KINDS = ("speed", "identical")  # driver.IDENTICAL_KINDS; not imported, since the driver imports health


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# A status file a coder's sandboxed games wrote into its clone's git folder (native.play_games): the sampler runs
# unsandboxed, so it reads one only through meta.worker_file (a plain file, one link, capped; MISSES 17).
def read_untrusted(path: Path) -> dict:
    try:
        value = json.loads(meta.worker_file(path))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# Every referee run's host status file with the purpose its games serve, as (key, purpose, path, trusted): the
# evaluation's <loop home>/remote-status.json, a coder's (remote-status-E####.json, or in its clone when its sandbox
# can't write the loop home), the sweep's, the prewarm's, the adversarial-map search's, a review's rule counts', the
# evidence refresh's and the native judge's gate's (remote-status-<name>.json),
# a tune's (runs/E####/remote-status.json), an official-map report's (runs/E####/remote-status-official.json) and a
# late screen's (rescreens/<champion>/remote-status.json). `key` names the file in a row.
def status_files(state: Path) -> list[tuple[str, str, Path, bool]]:
    names = {"sweep": "sweep", "prewarm": "prewarm", "advmaps": "adversarial maps", "review": "review rule counts",
             "evidence": "evidence refresh", "native-gate": "native gate"}
    found = [("remote-status.json", "evaluation", state / "remote-status.json", True)]
    for path in sorted(state.glob("remote-status-*.json")):
        name = path.stem.removeprefix("remote-status-")
        found.append((path.name, f"coder {name}" if re.fullmatch(r"E\d+", name) else names.get(name, name), path, True))
    for path in sorted(state.glob("runs/E*/remote-status*.json")):
        run, rest = path.parent.name, path.stem.removeprefix("remote-status").removeprefix("-")
        purpose = f"tune {run}" if not rest else f"official report {run}" if rest == "official" else f"{rest} {run}"
        found.append((f"runs/{run}/{path.name}", purpose, path, True))
    for path in sorted(state.glob("rescreens/*/remote-status.json")):
        found.append((f"rescreens/{path.parent.name}", "late screen", path, True))
    for path in sorted(clones_folder(Path(__file__).resolve().parent.parent).glob("E*/.git/bc-remote-status.json")):
        run = path.parent.parent.name
        found.append((f"clone/{run}", f"coder {run}", path, False))
    return found


def created(path: Path) -> float:
    info = path.stat()
    return getattr(info, "st_birthtime", info.st_ctime)


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


# The stage an evaluation's run folder is in now, from the files evaluate() leaves as it goes (driver.evaluate):
# bot-tests.log once the bot tests end, evaluation.json once the CPU gate and the maps are done, then identical.json
# and cpu-champion.json (a speed or identical idea) or sprt.json (the SPRT, written at its end), then a targeted idea's
# target batch (target-maps/ made at its start, target-units.jsonl at its end; the screen plays beside its last replay
# reads, driver.target_batch), then the screen.
def stage_now(run: Path, kind: str | None) -> str:
    if not (run / "bot-tests.log").exists():
        return "bot tests"
    if not (run / "evaluation.json").exists():
        return "CPU gate and maps"
    if kind in IDENTICAL_KINDS:
        if not (run / "identical.json").exists():
            return "identical check"
        if not (run / "cpu-champion.json").exists():
            return "CPU comparison"
        return "screen"
    if not (run / "sprt.json").exists():
        return "SPRT"
    if (run / "target-maps").is_dir() and not (run / "target-units.jsonl").exists():
        return "target batch"
    return "screen"


# The evaluation holding the lock now as {"experiment", "stage"}, or None when none does. Its run folder is the one
# whose exported bot (cand/) appeared after the lock was taken and that isn't recorded yet (diff.patch is written
# after the result, driver.cleanup). Before the export the lock holder is in its merge turn (a resolve file
# written since), or in "setup": refreshing the referee or rebasing its candidate (named when its driver's worker.json
# says so), or holding the lock for other work, such as a referee refresh before a tune (no experiment). A
# marker a crashed evaluation left behind counts as none (home.evaluation_running). "local": true while the holder is
# in a stage that plays only on this Mac or nowhere (the marker's "hosts": false, driver.host_stage), when the hosts
# are the coders' and fillers' at their usual shares, and MAC_HOSTS in its CPU gates, which play here and on the hosts
# of this Mac's platform (the other hosts then the coders' and fillers'). A rejudge (driver.rejudge: a judge's keep,
# or --rejudge) names its recorded experiment in the marker: stage "judge keep" or "rejudge".
def evaluation_now(state: Path, now: float) -> dict | None:
    if not evaluation_running(now):
        return None
    marker = read_json(evaluating_marker())
    local = {"local": True} if marker.get("hosts") is False else \
        {"local": MAC_HOSTS} if marker.get("hosts") == MAC_HOSTS else {}
    try:
        since = float(marker.get("since"))
    except (TypeError, ValueError):  # the marker went between the check and the read
        since = now
    if isinstance(marker.get("rejudge"), str):  # a recorded result's rejudge (driver.rejudge)
        return {"experiment": marker["rejudge"], "stage": "judge keep" if marker.get("judged") else "rejudge", **local}
    runs = [run for run in (state / "runs").glob("E*") if not (run / "diff.patch").exists()]
    exported = [(created(run / "cand"), run) for run in runs if (run / "cand").is_dir()]
    exported = [(at, run) for at, run in exported if at >= since - 60]
    if exported:
        run = max(exported)[1]
        kind = (read_json(run / "experiment.json").get("idea") or {}).get("kind")
        return {"experiment": run.name, "stage": stage_now(run, kind), **local}
    # Before the export: the lock holder's own candidate, one its driver (worker.json's driver_pid, the marker's pid)
    # finished coding and hasn't recorded (a waiting candidate's merge turn beside it, driver.prepare, isn't this
    # evaluation's); none while the lock does other work (a referee refresh, a promotion's recovery). A merge turn is
    # the holder's only on the marker's lane too: under --lanes N every lane shares one driver pid, and lanes in flight
    # are distinct (driver.free_lane); a marker from before "lane" matches on the pid alone.
    pid, lane = marker.get("pid"), marker.get("lane")
    merging = [run for run in runs if (mtime(run / "resolve.stderr") or 0) >= since
               and read_json(run / "worker.json").get("driver_pid") == pid
               and (lane is None or read_json(run / "experiment.json").get("lane") == lane)]
    if merging:
        return {"experiment": max(merging).name, "stage": "merge turn", **local}
    owned = [run for run in runs if pid and (worker := read_json(run / "worker.json")).get("driver_pid") == pid
             and worker.get("status") == "finished"]
    return {"experiment": max(owned).name if owned else None, "stage": "setup", **local}


# Candidates whose coding finished (worker.json "finished") but that aren't recorded and aren't under evaluation:
# waiting for the evaluation lock, while the driver process that ran the coder, or a native `evaluate` command
# (worker.json's `driver_pid`, meta.evaluator_alive), is alive; a driver that crashed left its candidate waiting for
# nothing (driver.stranded_count). A record from before `driver_pid` counts for WAITING_S, a native tune's handoff to
# its session's evaluate for meta.TUNED_WAIT_S.
def waiting_now(state: Path, now: float, evaluating: str | None) -> list[str]:
    finished = []
    for worker in (state / "runs").glob("E*/worker.json"):
        run = worker.parent
        age = now - (mtime(worker) or 0)
        if run.name == evaluating or (run / "diff.patch").exists() or age > WAITING_S:
            continue
        if (record := read_json(worker)).get("status") == "finished":
            finished.append((run.name, record, age))
    alive = meta.evaluators_alive([(record, age) for _, record, age in finished])  # one ps for them all
    return sorted(name for (name, _, _), live in zip(finished, alive) if live)


# The experiments still being coded (worker.json "starting" or "running", meta.CODING_STATUSES, from either provider)
# for a row's "coding"; a recorded run (diff.patch) is past coding whatever its worker file says. A claim still being
# set up (no worker.json yet, a minute or two) isn't counted, where driver.coding_count counts it.
def coding_now(state: Path) -> list[str]:
    return sorted(run.name for run in (state / "runs").glob("E*") if not (run / "diff.patch").exists()
                  and read_json(run / "worker.json").get("status") in meta.CODING_STATUSES)


# Each experiment's idea priority from its experiment.json (a few KB while unrecorded), for a row's "priorities"; one
# without an integer priority is left out.
def priorities(state: Path, experiments: list[str]) -> dict[str, int]:
    found = {}
    for exp in experiments:
        idea = read_json(state / "runs" / exp / "experiment.json").get("idea")
        priority = idea.get("priority") if isinstance(idea, dict) else None
        if type(priority) is int:
            found[exp] = priority
    return found


# The backlog's rows (<loop home>/ideas.jsonl) read without its lock, raising ValueError on a line that isn't JSON (an
# append caught half done); rows without an id, a status and an integer priority are skipped. tools/queue_page.py
# reads it too.
def read_ideas(state: Path) -> list[dict]:
    try:
        lines = (state / "ideas.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = [json.loads(line) for line in lines if line.strip()]
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("status"), str)
            and isinstance(r.get("priority"), int)]


# The waiting ideas (todo and followup) by priority, as {"1": {"todo": 5, "followup": 1}}: a row's "backlog" and the
# queue page's table.
def backlog(ideas: list[dict]) -> dict[str, dict[str, int]]:
    found = Counter((i["priority"], i["status"]) for i in ideas if i["status"] in ("todo", "followup"))
    return {str(p): {s: found[(p, s)] for s in ("todo", "followup")} for p in sorted({p for p, _ in found})}


# This Mac's slots as native.game_slots gives the loop's niced games: only `ours` while the 1-minute load average
# `load` stops new ones (home.sessions_full), so games already playing still count as capacity; else the cores dev work and the niced games leave idle, plus `ours`, our games
# running here, while the games' last published CPU split is fresh (home.niced_slots, home.published_split); else the
# load gate's (home.mac_slots): the loop's load share of its cores less other work's load (`load` less `ours`). None
# while memory is short.
def local_capacity(state: Path, cores: int, load: float, memory_gb: float, ours: float) -> int:
    try:
        text = (state / "load-fraction").read_text()
    except OSError:
        text = ""
    if sessions_full(load, load_share(text), cores):
        return int(ours)
    if split := published_split():
        return niced_slots(load_share(text), cores, split.dev, split.niced, ours, memory_gb)
    return mac_slots(load_share(text), cores, load, ours, memory_gb)


def down_hosts(state: Path) -> set[str]:
    return set(read_json(state / "host-outages.json"))


# Games each machine finished between `before` (the previous row's copy of this file's counts, None for a run begun
# since) and `status`. A run's counts start at zero, so counts that went down mean a new run replaced the file.
def finished_games(status: dict, before: dict | None) -> dict[str, int]:
    played = {name: int(host.get("played") or 0) for name, host in (status.get("hosts") or {}).items()}
    played["here"] = int(status.get("here") or 0)
    if before is None:
        return played
    if status.get("updated") == before.get("updated"):
        return {name: 0 for name in played}
    old = {**before.get("played", {}), "here": before.get("here", 0)}
    restarted = (int(status.get("games") or 0) < before.get("games", 0)
                 or any(n < old.get(name, 0) for name, n in played.items()))
    return {name: n - (0 if restarted else old.get(name, 0)) for name, n in played.items()}


def counts(status: dict) -> dict:
    return {"updated": status.get("updated"), "games": int(status.get("games") or 0),
            "here": int(status.get("here") or 0),
            "played": {name: int(host.get("played") or 0) for name, host in (status.get("hosts") or {}).items()}}


def count(value, most: float = MOST_GAMES) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and 0 <= value <= most else None
    except OverflowError:  # an int too large for a float
        return None


# A status file reduced to the fields a row reads, each checked, or None when it isn't live (updated LIVE_S ago or
# less, and not in the future). Only hosts in remote.json (`ceilings`: each one's `max`) are kept, their in-flight
# counts capped there, and this Mac's at its `cores`; an untrusted file (a coder's, in its clone) can't mark a host
# resting or report its cores.
def cleaned(status: dict, trusted: bool, ceilings: dict[str, int], now: float, cores: int) -> dict | None:
    updated = count(status.get("updated"), now + FUTURE_S)
    if updated is None or now - updated > LIVE_S:
        return None
    hosts = status.get("hosts") if isinstance(status.get("hosts"), dict) else {}
    clean = {"updated": updated, "games": int(count(status.get("games")) or 0), "here": int(count(status.get("here")) or 0),
             "hosts": {name: {"played": int(count(host.get("played")) or 0),
                              "resting": trusted and host.get("resting") is True}
                       for name, host in hosts.items() if name in ceilings and isinstance(host, dict)}}
    if isinstance(status.get("active"), dict):
        clean["active"] = {name: min(float(ceilings[name]), count(value) or 0.0)
                           for name, value in status["active"].items() if name in ceilings}
        clean["running"] = min(float(cores), count(status.get("running")) or 0.0)
        if trusted and isinstance(status.get("cores"), dict):
            clean["cores"] = {name: int(value) for name, value in status["cores"].items()
                              if name in ceilings and count(value, 1024)}
    return clean


# One minute's row (see the module docstring), given the previous row (None or too old: estimated files are unknown).
# A status file that can't be read or checked is left out and named in the row's `unreadable`.
def build_row(state: Path, now: float, previous: dict | None, cores: int, load: float, memory_gb: float) -> dict:
    if previous and now - previous.get("at", 0) > GAP_S:
        previous = None
    interval = max(30.0, now - previous["at"]) if previous else None
    remote = [h for h in read_json(state / "remote.json").get("hosts", [])
              if isinstance(h, dict) and isinstance(h.get("name"), str)]
    ceilings = {h["name"]: int(count(h.get("max"), 1024) or 0) for h in remote}
    seen_cores = {n: c for n, c in ((previous or {}).get("cores") or {}).items() if n in ceilings}
    busy: dict[str, dict[str, float]] = {}  # machine -> purpose -> games in flight
    unknown: set[str] = set()
    resting: set[str] = set()
    files, estimated, unreadable = {}, False, []
    for key, purpose, path, trusted in status_files(state):
        try:
            status = cleaned(read_json(path) if trusted else read_untrusted(path), trusted, ceilings, now, cores)
            if status is None:
                continue
            hosts = status["hosts"]
            resting |= {name for name, host in hosts.items() if host["resting"]}
            if "active" in status:
                seen_cores.update(status.get("cores", {}))
                flight = {**status["active"], "here": status["running"]}
            else:
                estimated = True
                files[key] = counts(status)
                if interval is None:
                    unknown |= {*hosts, "here"}
                    continue
                before = (previous or {}).get("files", {}).get(key)
                flight = {n: min(float(ceilings.get(n, cores)), done * GAME_S / interval)
                          for n, done in finished_games(status, before).items()}
            for name, games in flight.items():
                if games > 0:
                    busy.setdefault(name, {})[purpose] = round(busy.get(name, {}).get(purpose, 0) + games, 2)
        except Exception as error:  # one bad file never costs the row
            unreadable.append(f"{key}: {type(error).__name__}")
    down = down_hosts(state) | {name for name in resting if name not in busy}
    ours = sum(busy.get("here", {}).values())
    machines = {"here": local_capacity(state, cores, load, memory_gb, ours)}
    for name, ceiling in ceilings.items():
        machines[name] = 0 if name in down else min(ceiling, seen_cores.get(name) or ceiling // 2)
    rows, total, used, known = {}, 0, 0.0, True
    for name, capacity in machines.items():
        entry = {"capacity": capacity}
        if name in down and name != "here":
            entry["down"] = True
        if name in unknown and name not in busy:
            entry["busy"], known = None, known and capacity == 0
        else:
            entry["busy"] = round(min(float(capacity), sum(busy.get(name, {}).values())), 2)
            used += entry["busy"]
        if busy.get(name):
            entry["by"] = busy[name]
        total += capacity
        rows[name] = entry
    evaluation = evaluation_now(state, now)
    current = (evaluation or {}).get("experiment")
    waiting, coders = waiting_now(state, now, current), coding_now(state)
    row = {"at": round(now, 1), "time": time.strftime("%H:%M", time.localtime(now)), "evaluation": evaluation,
           "waiting": waiting, "coding": coders,
           "priorities": priorities(state, [*waiting, *coders, *([current] if current else [])]), "capacity": total,
           "busy": round(used, 2) if known else None,
           "idle": round(1 - used / total, 3) if known and total else None, "estimated": estimated,
           "hosts": rows}
    try:
        row["backlog"] = backlog(read_ideas(state))
    except (OSError, ValueError):  # a torn read: this row has no backlog (a gap in the queue page's chart)
        pass
    if files:
        row["files"] = files
    if unreadable:
        row["unreadable"] = unreadable
    if seen_cores:
        row["cores"] = seen_cores
    return row


def last_row(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().splitlines()
    except OSError:
        return None
    return next((row for _, row in rows_from_end(lines)), None)


# (index, row) for each of `lines` that parses, newest first: a torn line (a writer killed mid-row) is passed over.
def rows_from_end(lines: list[bytes]) -> Iterator[tuple[int, dict]]:
    for index in range(len(lines) - 1, -1, -1):
        try:
            yield index, json.loads(lines[index])
        except ValueError:
            continue


# Rows at or after `since`, oldest first, one per sample: a torn line (a writer killed mid-row) is skipped, and so is
# a row whose "at" an earlier one already has (the record written before 2026-09-27's sample fix holds such twins).
def read_rows(state: Path, since: float = 0) -> list[dict]:
    try:
        text = (state / LOG).read_text()
    except OSError:
        return []
    rows, seen = [], set()
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("at", 0) >= since and row.get("at") not in seen:
            seen.add(row.get("at"))
            rows.append(row)
    return rows


# Appends this minute's row unless another process (every driver and native watcher runs a sampler) wrote one within
# the last SAMPLE_S less a few seconds, and keeps the newest CAP rows. Returns the row written, or None. `cores`,
# `load` and `memory_gb` are this Mac's (measured when not given). A row's age counts either way: "at" is rounded to
# 0.1 s, so a sampler just behind the writer (samplers that wait on one another's lock fall into step) found a row
# up to 0.05 s "ahead" of it, and the old check (age 0 or more) wrote a twin of it: 36 of 822 rows on 2026-09-27.
# The time is read once the lock is held, so a sampler that waited there doesn't carry an older one either.
# A sampler on the disk's code (home.current_code) replaces the minute's row in place when an older one wrote it
# (`stale` true, or no `stale`: code from before it), so the row carries the fields a merge added, and there is still
# one row a minute. On 29 Sep, 35 minutes after 9e928681 added coding, priorities and backlog for the queue page's
# chart, no row had them: drivers started before it won every minute, and their code can't be changed (REFLECT
# 2026-09-29 20:42 F22, MISSES 91). The flag, not a missing field, marks the writer, so a row missing `backlog` from a
# torn ideas.jsonl read is left alone. While every sampler is stale, their rows fill the minutes; the upkeep stand-in,
# on the disk's code, samples too (maintenance.stand_in). current_code hashes every loaded module, so it runs only when
# this sampler is about to write or the row is a replaceable one.
def sample(state: Path, now: float | None = None, cores: int | None = None, load: float | None = None,
           memory_gb: float | None = None) -> dict | None:
    path = state / LOG
    with open(state / f"{LOG}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time() if now is None else now
        previous = last_row(path)
        recent = previous is not None and abs(now - previous.get("at", 0)) < SAMPLE_S - 5
        if recent and previous.get("stale") is False:
            return None
        stale = not current_code(everything=True)
        if recent and stale:
            return None
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except FileNotFoundError:
            lines = []
        if recent:  # an older sampler's row this minute: this one takes its place, built on the row before it
            found = rows_from_end(lines)
            index, _ = next(found)
            previous = next((row for _, row in found), None)
            del lines[index]
        row = build_row(state, now, previous, cores or os.cpu_count() or 1,
                        os.getloadavg()[0] if load is None else load,
                        reclaimable_gb() if memory_gb is None else memory_gb)
        row["stale"] = stale
        line = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        if recent or len(lines) >= CAP:
            temporary = path.with_name(f"{LOG}.{os.getpid()}.tmp")
            temporary.write_bytes(b"".join([*lines, line][-CAP:]))
            temporary.replace(path)
        else:
            with path.open("ab") as handle:
                handle.write(line)
        return row


# Samples every SAMPLE_S in a background thread while the block runs, until `stopped()`; a failing sample is printed
# and the next one still runs. The upkeep ticks can take many minutes, so the sampler doesn't wait for them. It runs
# beside the upkeep tick (maintenance.checking) and native.py's watch.
@contextlib.contextmanager
def sampling(state: Path, stopped: Callable[[], bool] = lambda: False) -> Iterator[None]:
    done = threading.Event()

    def loop() -> None:
        due = 0.0
        while not done.is_set() and not stopped():
            # Keeps this Mac's CPU split published whenever the loop runs: the sampler (home.CpuSplit) starts only
            # when a process reads the split, which before was only a loop game gated here, so with none the coders'
            # sandboxed gated_test.sh fell back to the 1-minute load, niced games included (29 Sep: 57 min waits).
            # Only in a process at normal priority: a niced one (the upkeep's stand-in) taking the sampler's place
            # would sample late under the very dev load the split is for.
            try:
                if os.getpriority(os.PRIO_PROCESS, 0) <= 0:
                    CPU.read()
            except Exception as error:
                print(f"CPU split read failed: {type(error).__name__}: {error}", flush=True)
            if time.monotonic() >= due:
                due = time.monotonic() + SAMPLE_S / 3
                try:
                    sample(state)
                except Exception as error:
                    print(f"utilisation sample failed: {type(error).__name__}: {error}", flush=True)
            done.wait(LAPSE_S)

    import maintenance  # here: it imports this module
    thread = threading.Thread(target=loop, name="utilisation-sampler", daemon=True)
    thread.start()
    try:
        with maintenance.exec_part("utilisation-sampler", done, thread):  # settled before an exec (before_exec)
            yield
    finally:
        done.set()
        thread.join(timeout=10)


# The cause a row's idle machines are charged to: the evaluation's experiment and stage, or the candidates waiting.
def cause(row: dict) -> str | None:
    if evaluation := row.get("evaluation"):
        return f"{evaluation.get('experiment') or 'an evaluation'}:{evaluation.get('stage')}"
    return "waiting" if row.get("waiting") else None


# Stretches of rows where an evaluation ran or a candidate waited (one cause throughout) made of windows of at least
# IDLE_MINUTES whose mean idle share was at least IDLE_SHARE, so a stage that idles every other minute counts as much
# as one idle every minute. Each: its cause, first and last row times, minutes, mean idle share, mean slots busy of
# the capacity, whether estimated, and the hosts idle in at least half its rows (none in flight; down hosts left out).
# `cause_of` names a row's cause (None: the row counts in no spell); health.idle_any passes health.no_evaluation,
# which counts only rows with no evaluation and nothing waiting.
def idle_spells(rows: list[dict], share: float = IDLE_SHARE, minutes: int = IDLE_MINUTES,
                cause_of: Callable[[dict], str | None] = cause) -> list[dict]:
    spells = []

    def add(spell: list[dict]) -> None:
        idle_hosts = [name for name in spell[-1]["hosts"]
                      if sum(1 for r in spell if (h := r["hosts"].get(name, {})).get("capacity")
                             and h.get("busy") == 0) * 2 >= len(spell)]
        spells.append({"cause": cause_of(spell[0]), "evaluation": spell[0].get("evaluation"),
                       "waiting": spell[-1].get("waiting"), "start": spell[0]["at"], "end": spell[-1]["at"],
                       "minutes": round((spell[-1]["at"] - spell[0]["at"]) / 60 + 1),
                       "idle": sum(r["idle"] for r in spell) / len(spell),
                       "busy": sum(r["busy"] for r in spell) / len(spell),
                       "capacity": round(sum(r["capacity"] for r in spell) / len(spell)),
                       "estimated": any(r.get("estimated") for r in spell), "idle_hosts": idle_hosts,
                       "unreadable": sorted({f for r in spell for f in r.get("unreadable", [])})})

    # Each run of rows with one cause and no gap: every row in a window of `minutes` whose mean idle reaches `share`
    # is marked, and each unbroken stretch of marked rows is a spell.
    def close(run: list[dict]) -> None:
        marked = [False] * len(run)
        end = 0
        for start in range(len(run)):
            end = max(end, start)
            while end < len(run) and run[end]["at"] - run[start]["at"] + SAMPLE_S < minutes * 60:
                end += 1
            if end == len(run):
                break
            window = run[start:end + 1]
            if sum(r["idle"] for r in window) / len(window) >= share:
                marked[start:end + 1] = [True] * len(window)
        spell = []
        for row, bad in zip(run, marked):
            if bad:
                spell.append(row)
            elif spell:
                add(spell)
                spell = []
        if spell:
            add(spell)

    run: list[dict] = []
    for row in rows:
        fits = cause_of(row) is not None and isinstance(row.get("idle"), (int, float))
        if run and (not fits or cause_of(row) != cause_of(run[-1]) or row["at"] - run[-1]["at"] > GAP_S):
            close(run)
            run = []
        if fits:
            run.append(row)
    close(run)
    return spells


# The latest row as a short phrase for evalline.py, e.g. "hosts 41/78 slots busy" ("~" when estimated from games
# finished), or None when no row is from the last few minutes.
def current_use(state: Path, now: float) -> str | None:
    row = last_row(state / LOG)
    if not row or now - row.get("at", 0) > GAP_S or row.get("busy") is None:
        return None
    return f"hosts {'~' if row.get('estimated') else ''}{row['busy']:.0f}/{row['capacity']} slots busy"


# "no game for 10+ min with free slots: sz-wsl (12), az-mel (12)" for each remote host that, in the newest rows while
# the evaluation plays on the hosts (a trailing run of REMOTE_STAGES rows, none further apart than GAP_S), had
# capacity and played nothing in the last IDLE_HOST_ROWS rows or more (the count in brackets), or None. A host that is
# down has no capacity and isn't named (remote.json "part_time" hosts are off by plan).
def idle_hosts(state: Path, now: float) -> str | None:
    rows = read_rows(state, since=now - 3 * 3600)
    if not rows or now - rows[-1].get("at", 0) > GAP_S:
        return None
    trail: list[dict] = []
    for row in reversed(rows):
        if ((row.get("evaluation") or {}).get("stage") not in REMOTE_STAGES or row.get("local")
                or (trail and trail[-1]["at"] - row["at"] > GAP_S)):
            break
        trail.append(row)
    idle = []
    for name in rows[-1].get("hosts") or {}:
        if name == "here":
            continue
        count = 0
        for row in trail:
            host = (row.get("hosts") or {}).get(name) or {}
            if not (host.get("capacity") or 0) > 0 or host.get("busy") != 0:
                break
            count += 1
        if count >= IDLE_HOST_ROWS:
            idle.append(f"{name} ({count})")
    return f"no game for {IDLE_HOST_ROWS}+ min with free slots: {', '.join(idle)}" if idle else None


# ---- Stage timing per result, from the files each evaluation leaves in runs/E####/ ----

def jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text()
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def played(rows: list[dict]) -> list[dict]:
    return [r for r in rows if isinstance(r, dict) and not r.get("predicted")]


# The screen's games played in `run` and the seconds they took: the candidate's, and the champion's
# (pool-<entry>-base-played.jsonl and the second stage's pool-<entry>-base-rest-played.jsonl on a shared map set,
# where pool-<entry>-base.jsonl also holds games reused from the set; pool-<entry>-base.jsonl otherwise). A
# single-entry hold's replay (driver.SCREEN_REPLAY_PAIRS) plays its own maps, so its pool-<entry>-replay-base.jsonl
# counts on a shared set too.
def screen_games(run: Path, shared: bool) -> list[dict]:
    games = []
    for path in run.glob("pool-*-candidate.jsonl"):
        games += played(jsonl(path))
    for path in run.glob("pool-*-base*-played.jsonl"):
        games += played(jsonl(path))
    for path in run.glob("pool-*-replay-base.jsonl" if shared else "pool-*-base.jsonl"):
        games += played(jsonl(path))
    return games


# The SPRT's games played: every trial's games (2 against the champion, 4 against a rotation opponent) less the
# baselines reused and the games predicted, with their seconds: the candidate's games carry their own, the champion's
# played rotation games are counted at the candidate games' mean. Not from sprt.json's `hosts`, a copy of the shared
# remote-status.json that other runs overwrite.
def sprt_games(sprt: dict) -> tuple[int, float]:
    trials = int(sprt.get("pairs") or 0)
    needed = sum(2 if meta.trial_opponent(sprt, i) == "champion" else 4 for i in range(trials))
    own = played(sprt.get("games") or [])
    predicted = (sprt.get("predict") or {}).get("predicted") or 0
    count = max(0, needed - int(sprt.get("baselines_reused") or 0) - int(predicted))
    seconds = sum(r.get("seconds") or 0 for r in own)
    mean = seconds / len(own) if own else 0.0
    return count, seconds + max(0, count - len(own)) * mean


CPU_GAMES = re.compile(r"\((\d+) games")


# Each stage of one recorded result as (name, start, end, games, game seconds or None), from its run folder's file
# times (see stage_now) and the result row: bot tests from the bot's export (cand/) to bot-tests.log; the CPU gate and
# the maps to evaluation.json; the identical check to identical.json and its CPU comparison to cpu-champion.json, or
# the SPRT to sprt.json; a target batch to target-units.jsonl (a screen played beside it ends first and is left in
# it); the screen to its last pool file; recording to the result's `finished`; and the official-map
# report, played after the result, from official-job.json. A stage whose files are missing (it didn't run) is left out.
def stages(run: Path, row: dict) -> list[tuple[str, float, float, int, float | None]]:
    try:
        start = created(run / "cand")
    except OSError:
        return []
    marks = [("bot tests", mtime(run / "bot-tests.log"), 0, None)]
    cpu_games = CPU_GAMES.search((row.get("cpu") or {}).get("line") or "")
    marks.append(("CPU gate and maps", mtime(run / "evaluation.json"), int(cpu_games.group(1)) if cpu_games else 0, None))
    identity = row.get("identity") or {}
    if (run / "identical.json").exists():
        marks.append(("identical check", mtime(run / "identical.json"), 2 * int(identity.get("maps") or 0), None))
        checks = [CPU_GAMES.search((side or {}).get("line") or "") for side in (identity.get("cpu") or {}).values()]
        marks.append(("CPU comparison", mtime(run / "cpu-champion.json"), sum(int(m.group(1)) for m in checks if m), None))
    elif (run / "sprt.json").exists():
        count, seconds = sprt_games(row.get("sprt") or {})
        marks.append(("SPRT", mtime(run / "sprt.json"), count, seconds or None))
    if (run / "target-units.jsonl").exists():
        games = [g for path in run.glob("target-*-*.jsonl") if path.name != "target-units.jsonl" for g in jsonl(path)]
        marks.append(("target batch", mtime(run / "target-units.jsonl"), len(games),
                      sum(g.get("seconds") or 0 for g in games) or None))
    pools = [mtime(p) for p in run.glob("pool-*.jsonl")]
    if pools:
        evaluation = row.get("evaluation") or {}
        games = screen_games(run, bool(evaluation.get("screen_shared") or evaluation.get("shared_maps")))
        marks.append(("screen", max(p for p in pools if p), len(games), sum(g.get("seconds") or 0 for g in games) or None))
    if isinstance(row.get("finished"), (int, float)) and row["finished"]:
        marks.append(("recording", row["finished"], 0, None))
    found, at = [], start
    for name, end, games, seconds in marks:
        if end is None or end < at:
            continue
        found.append((name, at, end, games, seconds))
        at = end
    job = read_json(run / "official-job.json")
    if isinstance(job.get("started_at"), (int, float)) and isinstance(job.get("finished_at"), (int, float)):
        games = played(jsonl(run / "official.jsonl"))
        found.append(("official report", job["started_at"], job["finished_at"],
                      len(games) or int(read_json(run / "official.json").get("games") or 0),
                      sum(g.get("seconds") or 0 for g in games) or None))
    return found


def minutes_text(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


# One stage as "SPRT 42 min, 2016 games, 48/min, ~9 in flight" (games in flight: the games' own seconds over the
# stage's wall time, where the files record them).
def stage_text(name: str, start: float, end: float, games: int, seconds: float | None) -> str:
    wall = end - start
    text = f"{name} {minutes_text(wall)}"
    if games:
        text += f", {games} games, {games / max(wall / 60, 1 / 60):.0f}/min"
    if seconds and wall > 0:
        text += f", ~{seconds / wall:.0f} in flight"
    return text


# stats.md's section: the last `count` results that reached an evaluation, each with its stages, then the slowest
# stage over the last day (by wall time).
def stage_lines(rows: list[dict], runs: Path, now: float, count: int = 10) -> list[str]:
    timed = [(row, found) for row in rows[-3 * count:] if (found := stages(runs / row["id"], row))][-count:]
    lines = ["", "## Stage times of recent results (loop/throughput.py)", "",
             "Wall time, games played, games a minute and games in flight per stage, from each result's run files "
             "(the official-map report runs after the result is recorded). Game machines hold about "
             "`capacity` slots in utilisation.jsonl; a stage far below that is where to look first.", ""]
    for row, found in timed:
        wall = found[-1][2] - found[0][1] if found else 0
        lines.append(f"- {row['id']} ({row['verdict']}, {minutes_text(wall)} in all): "
                     + "; ".join(stage_text(*stage) for stage in found))
    if not timed:
        lines.append("- no evaluated results yet")
    day = [(stage, row["id"]) for row, found in timed for stage in found if stage[2] >= now - 86400]
    if day:
        (name, start, end, games, seconds), eid = max(day, key=lambda item: item[0][2] - item[0][1])
        lines += ["", f"Slowest stage over the last day: {eid}'s {stage_text(name, start, end, games, seconds)}."]
    return lines
