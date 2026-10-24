"""Late screen: a champion promoted without an opponent-screen entry gets it afterwards. The screen plays the referee's
referee/gauntlet.toml as it stood when the candidate's evaluation started (driver.refresh_referee brings the referee
to main first), so an entry added on main after that never plays that candidate, and once it is promoted nothing has
compared it with the champion before it against that opponent (v0028, E0102, and `earlyking`); one claimed earlier but
evaluated after the next referee refresh does play it, since check_pool reads the refreshed gauntlet.toml. The current
champion's missing entries are the gauntlet entries on main that the result promoting it has no screen row for, less
those played here.

The upkeep tick (maintenance.tick) starts it in the background as soon as an entry is missing, beside evaluations:
its games yield to them as a filler's (driver.referee's `yielding`), as a tune's do, so it never waits for idle
machines, which a busy queue never leaves. It stops between entries for a stop marker or a champion move. While an
evaluation plays on the remote hosts it shares one yield share with official-map reports, and gives it to a tune
playing its games (native.host_share). With no evaluation on the hosts it keeps half of each host while it asks for
slots, and the idle fillers (the variant sweep, the prewarm, the adversarial-map search) take the rest: until
2026-09-29 the sweep and the search didn't start while it ran, which left that half idle.
For each entry it plays the entry against the champion and against the baseline, both in one referee run
(driver.play_screens): the baseline is the champion before the earliest promotion since the entry joined main that
lacked it, so every change that skipped the entry is covered.
Games are on the promoting result's screen maps, both sides, on its toolkit when that is still installed, scored like
the screen (driver.screen_row), with the hosts' live status in its own folder. Clearly worse (z <= -2.5) never
demotes: it writes a line to host-alerts.log and queues a priority-1 idea to find and fix what loses. Each champion's
rows are in <loop home>/rescreen.json (stats.md shows them on the promoting result's line); health.py alerts when
nothing has started 6 h after an entry went missing, when a run has hung, or when runs keep failing.

    .venv/bin/python loop/rescreen.py run --repo R --state S   # what tick launches
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import meta
import online

REPO = Path(__file__).resolve().parent.parent
RETRY_S = 3600  # after a failed run
STALE = 2 * 3600  # a run alive with no entry finished for this long is hung (health.py): an entry is ~200 games


def read(state: Path) -> dict:
    return online.read(state / "rescreen.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "rescreen.json", record)


def gauntlet(repo: Path) -> list[dict]:
    text = subprocess.run(["git", "show", "main:referee/gauntlet.toml"], cwd=repo, check=True, text=True,
                          capture_output=True).stdout
    return tomllib.loads(text)["opponent"]


# When entry `name` joined main: the commit time of the first commit on main's first-parent line whose gauntlet.toml
# has it (a merge, when it came on a branch: the branch's own commit can be hours older), or 0.
def entry_added(repo: Path, name: str) -> float:
    found = subprocess.run(["git", "log", "--first-parent", "--reverse", "--format=%H %ct", "main", "--",
                            "referee/gauntlet.toml"], cwd=repo, text=True, capture_output=True)
    for line in found.stdout.splitlines() if found.returncode == 0 else []:
        commit, at = line.split()
        text = subprocess.run(["git", "show", f"{commit}:referee/gauntlet.toml"], cwd=repo, text=True,
                              capture_output=True).stdout
        try:
            names = {spec.get("name") for spec in tomllib.loads(text).get("opponent", [])}
        except tomllib.TOMLDecodeError:
            continue
        if name in names:
            return float(at)
    return 0.0


def results(state: Path) -> list[dict]:
    path = state / "results.jsonl"
    return meta.latest_rows([json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                            if path.exists() else [])


# The result that promoted `champion` (a kept row whose candidate it is), or None for the pre-loop baseline.
def promoted_by(state: Path, champion: str, rows: list[dict] | None = None) -> dict | None:
    return next((row for row in reversed(rows if rows is not None else results(state))
                 if row["verdict"] == "kept" and row.get("candidate") == champion), None)


# The champion a result was tested against (its SPRT's base: a candidate rebased onto a newer champion was tested
# against that one, not its claim's).
def previous(row: dict) -> str:
    return (row.get("evaluation") or {}).get("base") or row["base"]


def screened(row: dict) -> set[str]:
    return {o["name"] for o in (row.get("pool") or {}).get("opponents", [])}


# The champion to compare with for entry `name`: before the earliest promotion, back from `row`, that finished after
# the entry joined main (`added`) and has no screen row for it. For earlyking (merged 12:55) that is 36773e6: E0102
# lacked it, E0101 finished before it existed.
def baseline(state: Path, row: dict, name: str, added: float) -> str:
    rows = results(state)
    while (earlier := promoted_by(state, previous(row), rows)) and name not in screened(earlier) \
            and earlier.get("finished", 0) >= added:
        row = earlier
    return previous(row)


# Whether a kept row's candidate plays every game as its base did: a speed idea, or one kept on the same machine code
# but for the sonar key (driver.SAME_CODE).
def same_play(row: dict) -> bool:
    return row["idea"].get("kind") == "speed" or bool((row.get("identity") or {}).get("same_code"))


# The current champion's screen entries still unplayed: (champion, the result whose screen stands for it, entry names),
# or None when nothing is missing. A champion that plays as its predecessor (same_play) is that predecessor, so the
# result that stands for it is the last promotion back that changed play, and entries played for any champion on
# that line count: until 2026-09-29 v0060 (E0302) and v0070 (E0350) had no screen and played the whole gauntlet
# against their predecessor's identical code (25 and 58+ minutes of half of every host), and a skip of them alone would
# have dropped what their predecessor still owed. The baseline then comes from that result, so never identical code.
def missing(state: Path, repo: Path) -> tuple[str, dict, list[str]] | None:
    champion = meta.champion_commit(repo)
    rows = results(state)
    row = promoted_by(state, champion, rows) if champion else None
    records, done = read(state), set(read(state).get(champion, {}).get("done", {}))
    while row is not None and same_play(row) and (earlier := promoted_by(state, previous(row), rows)):
        done |= set(records.get(previous(row), {}).get("done", {}))
        row = earlier
    if row is None or same_play(row):
        return None
    have = screened(row) | done
    names = [spec["name"] for spec in gauntlet(repo) if spec["name"] not in have]
    return (champion, row, names) if names else None


def stopped(state: Path) -> bool:
    return (state / "STOP").exists() or (state / "ASTRA_STOP").exists()


# What missing() reads, cheaply: results.jsonl's size and mtime (tens of MB, which missing() parses), the champion,
# main's gauntlet.toml and rescreen.json's mtime. The upkeep ticks every minute; missing() reruns only when this changes.
def inputs(state: Path, repo: Path) -> tuple:
    def stamp(path: Path) -> tuple:
        try:
            info = path.stat()
        except OSError:
            return ()
        return info.st_size, info.st_mtime_ns
    blob = subprocess.run(["git", "rev-parse", "main:referee/gauntlet.toml"], cwd=repo, text=True,
                          capture_output=True).stdout.strip()
    return stamp(state / "results.jsonl"), meta.champion_commit(repo), blob, stamp(state / "rescreen.json")


_seen: tuple = ((), None)


# missing(), reusing the last answer while its inputs are unchanged (the tick's check; `run` and health.py call
# missing() itself).
def missing_now(state: Path, repo: Path) -> tuple[str, dict, list[str]] | None:
    global _seen
    key = (str(state), inputs(state, repo))
    if _seen[0] != key:
        _seen = (key, missing(state, repo))
    return _seen[1]


# The upkeep tick's step: starts a background run for the current champion's missing entries unless one is alive, a
# stop marker is set or a failure is under RETRY_S old; never waits for it. Returns a line to log, or None.
def tick(repo: Path, state: Path, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    meta.reap()  # a run that finished clears its own pid, so only this reaps it
    if not (state / "ideas.jsonl").exists() or stopped(state):
        return None
    with meta.locked(state / "rescreen"):
        found = missing_now(state, repo)
        if found is None:
            return None
        champion, row, names = found
        records = read(state)
        record = records.setdefault(champion, {"experiment": row["id"], "done": {}})
        if pid := record.get("pid"):
            try:
                os.waitpid(pid, os.WNOHANG)  # reap it if this process launched it
            except ChildProcessError:
                pass
            if meta.alive(pid):
                return None
            record.update(pid=None, failed_at=now, failures=record.get("failures", 0) + 1,
                          error=record.get("error") or "the run exited without playing every entry; see its run.log")
            save(state, records)
        if now - record.get("failed_at", 0) < RETRY_S:
            return None
        folder = state / "rescreens" / champion
        folder.mkdir(parents=True, exist_ok=True)
        record.update(pid=launch(repo, state, folder), started_at=now, progress_at=now)
        save(state, records)
        return f"late screen of {champion[:7]} ({row['id']}) against {', '.join(names)} launched: {folder}"


def launch(repo: Path, state: Path, folder: Path) -> int:
    return meta.spawn([sys.executable, str(Path(__file__).resolve()), "run", "--repo", str(repo), "--state",
                       str(state)], folder, folder / "run.log")


def update(state: Path, champion: str, **changes) -> None:
    with meta.locked(state / "rescreen"):
        records = read(state)
        records.setdefault(champion, {}).update(changes)
        save(state, records)


# The detached run tick launches: each missing entry in turn, the champion then its baseline against it, until every
# entry is played, a stop marker appears or the champion moves (a later tick resumes).
def run(repo: Path, state: Path) -> None:
    import driver
    import maintenance
    found = missing(state, repo)
    if found is None:
        return
    champion, row, names = found
    folder = state / "rescreens" / champion
    # The row's toolkit, which the cached baseline games on its shared maps came from; when it is gone, the current
    # one, and every game played fresh.
    python = ((row.get("evaluation") or {}).get("toolchain") or {}).get("python")
    same_toolkit = bool(python) and Path(python).exists()
    token = driver.RUNTIME_PYTHON.set(python if same_toolkit else maintenance.toolchain(state)["python"])
    try:
        pairs = (row.get("evaluation") or {}).get("pool_pairs") or driver.POOL_PAIRS
        maps, same_maps = screen_maps(state, row, folder, pairs)
        # A map the engine refuses to load is repaired or swapped for a spare first (driver.loadable_maps).
        saved = state / "runs" / row["id"] / "evaluation.json"
        spares = (json.loads(saved.read_text()) if saved.exists() else {}).get("spare_maps") or []
        maps = driver.loadable_maps(maps, spares, f"the late screen of {champion[:7]}")
        # A map mix's screen played general maps (driver.screen_maps), on the shared set when it could.
        shared = (row.get("evaluation") or {}).get("screen_shared") or (row.get("evaluation") or {}).get("shared_maps")
        specs = {spec["name"]: spec for spec in gauntlet(repo)}
        candidate = driver.gauntlet_bot({"name": "candidate", "commit": champion}, into=folder / "bots")
        for name in names:
            if stopped(state) or meta.champion_commit(repo) != champion:
                print(f"stopped before {name}: a stop marker or a new champion", flush=True)
                break
            base = baseline(state, row, name, entry_added(repo, name))
            # The cache holds the previous champion's games on its own shared set, played on the row's toolkit.
            cache = Path(shared) if shared and same_maps and same_toolkit and base == previous(row) else None
            bots = {"candidate": candidate,
                    "base": driver.gauntlet_bot({"name": "base", "commit": base}, into=folder / "bots")}
            opponent = driver.gauntlet_bot(specs[name])
            # Both bots' games as one referee run (driver.play_screens), the base's from the cache where it can.
            rows = driver.play_screens([driver.Screen(name, bot, opponent, maps, folder / f"{name}-{side}.jsonl",
                                                      cache if side == "base" else None)
                                        for side, bot in bots.items()],
                                       yielding=True, status=folder / "remote-status.json")
            games = dict(zip(bots, rows, strict=True))
            result = {**driver.screen_row(name, games), "baseline": base}
            driver.weak_losses(state, f"the late screen of champion {champion[:7]} ({row['id']})", name, games,
                               bots, opponent, folder, yielding=True)
            with meta.locked(state / "rescreen"):
                records = read(state)
                records.setdefault(champion, {}).setdefault("done", {})[name] = result
                records[champion]["progress_at"] = time.time()
                save(state, records)
            print(f"{name}: champion {result['candidate']:.2f}, baseline {base[:7]} {result['base']:.2f}, "
                  f"z {result['z']:+.1f}", flush=True)
            if result["worse"]:
                regressed(state, champion, row, result, folder)
        update(state, champion, pid=None, error=None, failures=0)
    except Exception as error:
        with meta.locked(state / "rescreen"):
            records = read(state)
            record = records.setdefault(champion, {})
            record.update(pid=None, failed_at=time.time(), failures=record.get("failures", 0) + 1,
                          error=f"{type(error).__name__}: {error}"[-600:])
            save(state, records)
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    finally:
        driver.RUNTIME_PYTHON.reset(token)


# The promoting result's screen maps (its evaluation.json's screen_maps, or the first `pairs` of its evaluation maps
# before those were saved) and True, or fresh eval maps and False when its run folder or maps are gone.
def screen_maps(state: Path, row: dict, folder: Path, pairs: int) -> tuple[list[str], bool]:
    import driver
    path = state / "runs" / row["id"] / "evaluation.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    maps = (saved.get("screen_maps") or saved.get("maps") or [])[:pairs]
    if maps and all(Path(m).exists() for m in maps):
        return maps, True
    driver.generate_maps(folder / "maps", seed=int(previous(row)[:7], 16), count=pairs)
    return sorted(str(m) for m in (folder / "maps").glob("*.map"))[:pairs], False


# A champion clearly worse than its baseline against a screen entry it never met: an alert line and a priority-1
# idea. The champion stays: the loop never demotes, and the fix is a new candidate.
def regressed(state: Path, champion: str, row: dict, result: dict, folder: Path) -> None:
    name, base = result["name"], result["baseline"]
    games = sum(1 for line in (folder / f"{name}-candidate.jsonl").read_text().splitlines() if line.strip())
    text = (f"champion {champion[:7]} ({row['id']} {row['idea'].get('id')}: {row['idea'].get('title', '')}) is clearly "
            f"worse than {base[:7]}, the champion before the promotions that skipped it, against the screen's {name}, "
            f"which joined after {row['id']} was claimed: {result['candidate']:.2f} vs {result['base']:.2f} over "
            f"{games} games each, paired z {result['z']:+.1f}")
    with (state / "host-alerts.log").open("a") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} rescreen {text}; games in {folder}\n")
    idea = {"category": "strategy", "priority": 1,
            "title": f"Fix what the champion since {base[:7]} loses against the screen's {name} (late screen)",
            "hypothesis": (f"The late screen (loop/rescreen.py) found {text}. The promotions since {base[:7]} never met "
                           f"{name}, since the entry was added after they were claimed. Watch the champion's lost games "
                           f"against {name} in {folder} next to {base[:7]}'s on the same maps, find which of those "
                           "changes loses them, and fix it or make it conditional on something the bot can observe "
                           "about the opponent; test it as usual.")}
    meta.append_ideas(state, meta.valid_proposals([json.dumps(idea)], 1, options=False), f"rescreen:{champion[:7]}")


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    running_ = commands.add_parser("run", help="the background run tick launches")
    running_.add_argument("--repo", type=Path, default=REPO)
    running_.add_argument("--state", type=Path, default=loop_home())
    args = parser.parse_args()
    run(args.repo.resolve(), args.state.resolve())


if __name__ == "__main__":
    main()
