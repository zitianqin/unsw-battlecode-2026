"""The official-map report, off the critical path (2026-09-26): a tested candidate against the champion it was tested
against on every map file in the referee checkout's maps/ladder and maps/sprint (ladder_maps.local_files), both sides,
under driver.OFFICIAL_SEEDS pearl seeds each (driver.check_official): 2 x 5 x 10 = 100 games with the 10 files of
2026-09-27. It is a report, never a gate, so an evaluation no longer waits for it: the result is recorded with
`official: {"pending": ...}`, the machines go on to the next test, and this plays the games afterwards as a filler
that yields to evaluations (native.host_share), into runs/<id>/official.json. Every
reader takes that file in place of the pending value (`report`). For a tune its line (by map, with each map's area and
how many of its near copies the tune played, tunesplit.official_text) also goes to the alerts file: not a held-out
check, since a tune's maps include mapgen's near copies of the official maps.

`start` launches the job in the background right after the result is recorded (both providers); its state is
runs/<id>/official-job.json (pid, attempts, error) and its output runs/<id>/official.log. The upkeep tick (`tick`)
relaunches a job that died, up to ATTEMPTS, and health.py alerts on a report still missing after GRACE or failed.

    uv run loop/official.py run E0102   # the job start() launches; also by hand
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import chance

PENDING = "played after the result was recorded (loop/official.py); the report is runs/<id>/official.json"
ATTEMPTS = 2
GRACE = 3 * 3600  # a pending report whose job made no progress this long (health.py) needs a look
RECENT = 40  # results the upkeep tick and health.py look back over
TICK_EVERY = 600
_ticked = 0.0


def folder(state: Path, exp_id: str) -> Path:
    return state / "runs" / exp_id


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


# The JSON rows of JSONL `lines`, skipping blank lines and only a final line that doesn't parse: one a writer is still
# appending (results.jsonl, a shared set's screen games). An unparsable line before the last raises, unless the caller
# passes `bad`: then it is skipped and its 1-based line number appended there, for a reader every cycle depends on
# (online.upload_rows) to alert on rather than fail.
def json_rows(lines: list[str], bad: list[int] | None = None) -> list[dict]:
    rows = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            if i < len(lines) - 1:
                if bad is None:
                    raise
                bad.append(i + 1)
    return rows


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=1) + "\n")
    temporary.replace(path)


SIDE_DECIDED = 0.9  # one side winning this share of a map's decided games decides it, whichever bot plays it (M65)


# The maps of official-map rows (runs/<id>/official.jsonl: the candidate is `a`, on `a_side`) where one side won at
# least SIDE_DECIDED of the decided games. Near-self-play there is decided by side (M65: E0200-E0239's 290 Dilemma
# games went 285 to side B), so the candidate scores half whatever it changed and the map's score judges nothing.
def side_decided(rows: list[dict]) -> list[str]:
    wins: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("winner") in ("a", "b") and row.get("a_side") in ("A", "B"):
            side = row["a_side"] if row["winner"] == "a" else "AB".replace(row["a_side"], "")
            count = wins.setdefault(Path(row["map"]).stem.split("~")[0], {"A": 0, "B": 0})
            count[side] += 1
    return sorted(m for m, c in wins.items() if max(c.values()) >= SIDE_DECIDED * sum(c.values()))


# "; one side won 90%+ on dilemma, trophy: their scores judge nothing" for a report with side-decided maps, else "".
def side_text(report: dict) -> str:
    maps = report.get("side_decided")
    return f"; one side won {SIDE_DECIDED:.0%}+ on {', '.join(maps)}: their scores judge nothing" if maps else ""


# A result row's official-map report: its own when the evaluation played it (before 2026-09-26), else the deferred
# job's runs/<id>/official.json once written, else the row's {"pending": ...}.
def report(state: Path, row: dict) -> dict:
    official = row.get("official") or {}
    if not official.get("pending"):
        return official
    return read_json(folder(state, row.get("id", "")) / "official.json") or official


# The games of an official-map report (runs/<id>/official.jsonl rows) where the candidate's moves changed. A map
# file's two games (the candidate on each side, one pearl seed) are exact mirrors when it moved as the champion
# throughout, since both bots are deterministic, so a pair whose sides' numbers don't swap exactly holds a changed
# move (M53: E0175's race fires on some maps only). A game with no winner (it errored) is left out, and so is its
# partner: a pair left with one game can't be told from a mirror's half, so none of its games count. Returns {"games", "wins", "losses", "pairs",
# "pairs_lost", "pairs_won", "pairs_split", "maps": [stems], "sign_p"}: a pair's two games share a map and a seed, so
# they aren't independent, and sign_p is chance.sign_p over the pairs the candidate lost and won on points (a split
# pair, one win each or two draws, counts neither).
def changed_games(rows: list[dict]) -> dict:
    pairs: dict[str, dict[str, dict]] = {}
    for row in rows:
        if row.get("winner") in ("a", "b", "draw"):
            pairs.setdefault(row["map"], {})[row["a_side"]] = row
    swapped = {"a": "b", "b": "a", "draw": "draw"}
    games, wins, losses, pairs_lost, pairs_won, split, maps = 0, 0, 0, 0, 0, 0, set()
    for name, pair in pairs.items():
        first, second = pair.get("A"), pair.get("B")
        if not (first and second):  # a lone game (its partner errored) can't be told from a mirror's half: left out
            continue
        if all(first[k] == second[k] for k in ("rounds", "end")) \
                and swapped[first["winner"]] == second["winner"] and first["a"] == second["b"] \
                and first["b"] == second["a"]:
            continue
        maps.add(Path(name).stem.split("~")[0])
        won = sum(row["winner"] == "a" for row in pair.values())
        lost = sum(row["winner"] == "b" for row in pair.values())
        games, wins, losses = games + len(pair), wins + won, losses + lost
        pairs_lost, pairs_won, split = pairs_lost + (lost > won), pairs_won + (won > lost), split + (won == lost)
    return {"games": games, "wins": wins, "losses": losses, "pairs": pairs_lost + pairs_won + split,
            "pairs_lost": pairs_lost, "pairs_won": pairs_won, "pairs_split": split, "maps": sorted(maps),
            "sign_p": chance.sign_p(pairs_lost, pairs_won)}


# Results whose report is pending and not yet written, newest last, among the last RECENT rows of results.jsonl, each
# id's latest row only (a rejudge's row supersedes the one it re-judged, meta.latest_rows). Rows are up to 2 MB each
# (their SPRT games), so only those naming PENDING are parsed, the id read from the line's start (RESULT_ID).
RESULT_ID = re.compile(r'\{"id": "([^"]+)"')


def waiting(state: Path) -> list[dict]:
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-RECENT:] if path.exists() else []
    last = {m.group(1): k for k, line in enumerate(lines) if (m := RESULT_ID.match(line))}
    rows = json_rows([line for k, line in enumerate(lines) if PENDING in line
                      and (m := RESULT_ID.match(line)) and last[m.group(1)] == k])
    return [row for row in rows if (row.get("official") or {}).get("pending")
            and not (folder(state, row["id"]) / "official.json").exists()]


def alive(pid: int | None) -> bool:
    import meta  # here: meta imports this module
    return meta.alive(pid)


# The results whose report job is alive now (runs/<id>/official-job.json's pid, cleared when the job ends) and whose
# report isn't written yet, for the fillers that give way to it (advmaps.wanted).
def running(state: Path) -> list[str]:
    return [job.parent.name for job in sorted((state / "runs").glob("E*/official-job.json"))
            if not (job.parent / "official.json").exists() and alive(read_json(job).get("pid"))]


# Launches the job for a result whose report is pending, unless one is running or it is written already, under a lock
# on the result's folder, so the driver's start and an upkeep tick (another process) never both launch. Returns a line
# to log, or None.
def start(state: Path, exp_id: str, workers: str = "auto") -> str | None:
    import meta  # here: meta imports this module
    run_dir = folder(state, exp_id)
    with meta.locked(run_dir / "official"):
        if (run_dir / "official.json").exists():
            return None
        job = read_json(run_dir / "official-job.json")
        if alive(job.get("pid")):
            return None
        pid = meta.spawn([sys.executable, str(Path(__file__).resolve()), "run", exp_id, "--workers", workers],
                         run_dir, run_dir / "official.log")
        write_json(run_dir / "official-job.json", {**job, "pid": pid, "started_at": time.time(),
                                                    "attempts": job.get("attempts", 0) + 1})
    return f"{exp_id}: official-map report playing in the background (pid {pid}), see {run_dir / 'official.log'}"


# The upkeep tick's step, at most every TICK_EVERY seconds (results.jsonl is tens of MB): relaunches the job of a
# recent result whose report is still pending and whose job is not running, up to ATTEMPTS launches each. Returns the
# lines to log.
def tick(state: Path, now: float | None = None) -> list[str]:
    global _ticked
    import meta  # here: meta imports this module
    meta.reap()  # a job that finished clears its own pid, so only this reaps it
    now = time.time() if now is None else now
    if now - _ticked < TICK_EVERY:
        return []
    _ticked = now
    lines = []
    for row in waiting(state):
        job = read_json(folder(state, row["id"]) / "official-job.json")
        if not alive(job.get("pid")) and job.get("attempts", 0) < ATTEMPTS and (line := start(state, row["id"])):
            lines.append(line)
    return lines


# The job: the candidate snapshot against its base commit (a fresh export, since the champion checkout may have moved
# on), on the experiment's toolkit, yielding to evaluations; writes official.json, or the error in official-job.json.
def run(exp_id: str, workers: str = "auto") -> int:
    import driver
    driver.WORKERS = workers  # the driver's --workers, as its own games use
    run_dir = folder(driver.STATE, exp_id)
    exp = driver.Experiment.load(json.loads((run_dir / "experiment.json").read_text()))
    try:
        base = driver.gauntlet_bot({"name": "champion", "commit": exp.evaluation["base"]}, into=run_dir)
        with driver.experiment_runtime(exp):
            driver.check_official(exp, run_dir / "cand", base=base, yielding=True,
                                  status=run_dir / "remote-status-official.json")
    except Exception as error:
        exp.official = {"error": f"{type(error).__name__}: {error}"[-300:]}
    job = read_json(run_dir / "official-job.json")
    if "score" not in exp.official:
        write_json(run_dir / "official-job.json", {**job, "pid": None, "error": exp.official.get("error"),
                                                    "failed_at": time.time()})
        print(f"official-map report failed: {exp.official.get('error')}", flush=True)
        return 1
    write_json(run_dir / "official.json", {**exp.official, "played_at": time.time()})
    write_json(run_dir / "official-job.json", {**job, "pid": None, "error": None, "finished_at": time.time()})
    print(f"official-map report{driver.official_text(exp.official)}", flush=True)
    if exp.idea.get("kind") == "tune":  # a tune's official-map line (loop/tunesplit.py), to the orchestrator's watcher
        import meta
        import tunesplit
        try:
            line = tunesplit.official_text(exp_id, exp.idea.get("id", ""), exp.official, run_dir / "official-maps",
                                           run_dir / "tune-maps")
            print(line, flush=True)
            meta.alert(driver.STATE, line)
        except Exception as error:  # the report is written; a failed line is printed, never raised
            print(f"tune official-map line not written: {type(error).__name__}: {error}", flush=True)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    running = commands.add_parser("run", help="play one result's pending official-map report")
    running.add_argument("experiment")
    running.add_argument("--workers", default="auto")
    args = parser.parse_args()
    sys.exit(run(args.experiment, args.workers))


if __name__ == "__main__":
    main()
