"""Fit rows from our online games: our own result-blind ladder and autoscrim games played again on this Mac with our
bot's debug line on (tools/why.py's deterministic replay, on the exact commit behind the version: why.submission_commit),
saved where coders can read them, so tools/fit.py trains on real online decisions, not only local ones.

    .venv/bin/python loop/online_fit.py run STATE [--limit N]   # what the upkeep starts in the background (tick)
    .venv/bin/python loop/online_fit.py pending STATE            # how many games wait, by version

What it writes: <loop home>/online-replays/fit/<version>/, one gzipped replay a game (<game id>.replay: the replayed
game, event for event the recorded one, with our dragons' debug lines; the per-move terms left out, which only why's
own report reads) and index.json {"games": [the online index's row, with "replay" pointing here and "seconds"],
"skipped": {game id: {"reason", "layout", "seconds", "at", "tries"}}}, which fit.py reads as any index folder (DIR
arguments: fit.py SPEC ~/.bc-loop/online-replays/fit/10774; its side from the index, none of them chosen by result).
Only the sample (online.sample_games: no loss kept because we lost) and no dev game (online.against_dev): dev games
are public replays of the live bot and read apart everywhere (CLAUDE.md, MISSES 55). A game is done once
(idempotent: saved or skipped for good), newest version first, the newest KEEP_VERSIONS versions only; older fit
folders are deleted at the end of every run, however it ends (regenerable copies of games whose own replays stay).
Size: 1.7-3.2 MB a game gzipped (the online replay itself 0.3-1.7 MB), so KEEP_VERSIONS x MAX_GAMES games stay
under 2 GB.

Skipped, with why: a game no map file reproduces with every dragon a stand-in (the site's pearl variants, below), a
game our bot plays differently (the first diverging round: then the replay is not the real game), and, tried again
RETRY_S later up to RETRIES times (RETRYABLE), a replay that can't be read, a version whose commit is unknown or whose
build failed (then this run's other games of it skip too), or a game whose replay raised.

The site's pearl variants (ladder_maps' docstring): games skipped as not reproducible are grouped by map layout, and
once VARIANT_MIN of one layout wait, ladder_maps.rebuild_variant fits its timings from their seeds and spawns; the
rebuilt map is kept in ladder-map-variants/ (VARIANT_FILES a layout at most) only when the engine plays at least
VARIANT_SHARE of those games again event for event, and they are tried again; a layout that fails waits until
VARIANT_GROWTH times as many of its games wait. On 2026-09-29 that took 10774's unreproducible replays from 98 of 433
to 31 (Schooltime 24 of 24, Devil 20 of 20, Queen Of Spades 23 of 23; Slithery Fight's 17 and the 10-dragon
Prisoners Dilemma's 14 fit wrong, so no file).

Cost (10774's games, 2026-09-29, one core): the stand-in check 7.7 s a game on average (every candidate map), the
debug bot's game 2-26 s (13 s on average for full-length games), the build once a commit (cached by the judge's
sandbox): about 20 s a game, so 10774's 294 result-blind non-dev games take about 100 minutes of one core. Where: this
Mac, at nice 19 and one replay at a time under the load gate (why.wait_turn, the way audits' why runs wait), since the
remote hosts run only whole games of two bots (referee/remote.py), not a replay's stand-ins. The upkeep starts a
run at most every EVERY while games wait (tick), each stopping its games after RUN_S and its variant pass VARIANT_S
later (a layout's fit itself, about 1 s for 12 games, is not cut); online-fit.json records it (pid, times, the last
run's counts, each layout's rebuild and kept files) and health.online_fit checks it.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ladder_maps  # noqa: E402
import online  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECORD = "online-fit.json"
FOLDER = Path("online-replays") / "fit"
KEEP_VERSIONS = 2
MAX_GAMES = 300  # a version's games saved at most (10774 had 294 result-blind non-dev games)
EVERY = 3600
RUN_S = 40 * 60
VARIANT_MIN = 8  # games of one layout waiting as not reproducible before its timings are rebuilt
VARIANT_SHARE = 0.8  # the share of them the rebuilt map must play again
TIMINGS = ("no map file", "not reproducible")  # the skips a pearl variant explains
# Skips that may pass: a commit uploads.jsonl names later, a replay file read again, a build or step that failed. Each
# is tried again RETRY_S after its last try, RETRIES tries in all; the others (timings, a divergence) are for good.
RETRYABLE = ("no commit known", "unreadable", "build failed", "error")
RETRIES = 3
RETRY_S = 6 * 3600
VARIANT_S = 15 * 60  # the variant pass's own time after RUN_S, checked between layouts and between their games
VARIANT_GROWTH = 1.5  # a layout whose rebuild failed is tried again once this many times its games wait
VARIANT_FILES = 3  # rebuilt files kept a layout, the oldest dropped first
ROW = ("id", "submission_id", "opponent", "side", "map_id", "ranked", "requested_at")  # kept from the online row


def folder(state: Path, sid: int) -> Path:
    return state / FOLDER / str(sid)


def index_of(state: Path, sid: int) -> dict:
    return online.read(folder(state, sid) / "index.json", {"games": [], "skipped": {}})


# The newest KEEP_VERSIONS versions with an online index, newest first.
def versions(state: Path) -> list[int]:
    root = state / "online-replays"
    found = sorted((int(p.parent.name) for p in root.glob("*/index.json") if p.parent.name.isdigit()), reverse=True)
    return found[:KEEP_VERSIONS]


# Whether a skip is for good at `now`: any but RETRYABLE's, or one tried RETRIES times, or last tried under RETRY_S ago.
def settled(skip: dict, now: float) -> bool:
    return (not str(skip.get("reason", "")).startswith(RETRYABLE) or skip.get("tries", 1) >= RETRIES
            or now - skip.get("at", 0) < RETRY_S)


# Each version's games still to replay at `now`, newest version first: its index's sample games (online.sample_games)
# not against a dev team (dev teams from online.json, as every reader keeps them apart) with a replay on disk, neither
# saved nor skipped here for good (settled), up to MAX_GAMES a version counting those done.
def pending(state: Path, now: float | None = None) -> list[tuple[int, dict]]:
    now = time.time() if now is None else now
    dev = online.dev_ids(online.read(state / "online.json", {}))
    out = []
    for sid in versions(state):
        saved = online.read(state / "online-replays" / str(sid) / "index.json", {"games": []})
        done = index_of(state, sid)
        seen = {g["id"] for g in done["games"]} | {int(k) for k, v in done["skipped"].items() if settled(v, now)}
        room = MAX_GAMES - len(done["games"])
        for game in online.sample_games(saved["games"]):
            if room <= 0:
                break
            if (game["id"] in seen or online.against_dev(game, dev) or game.get("side") not in ("A", "B")
                    or not Path(game.get("replay") or "").is_file()):
                continue
            out.append((sid, game))
            room -= 1
    return out


def map_dirs(state: Path) -> tuple[Path, ...]:
    import why
    return (state / ladder_maps.VARIANTS, *why.MAP_DIRS)


# One game played again with our debug line (why's steps, the build shared through `built`): ("saved", gzipped
# replay) or (the skip's reason, layout).
def replay_one(state: Path, game: dict, commit: str, built: dict, work: Path) -> tuple[str, bytes | str]:
    import replays
    import why
    from unswbc import clangtool
    try:
        replay = replays.load_replay(Path(game["replay"]))
    except Exception as error:
        return f"unreadable: {type(error).__name__}", ""
    layout = ladder_maps.teamless_layout(replay.map)
    if replay.seed.which() != "value":
        return "no seed", layout
    maps = why.candidate_maps(replay.map, map_dirs(state))
    if not maps:
        return "no map file", layout
    good = [m for m in maps if why.play(replay, None, None, m).difference is None]
    if not good:
        return "not reproducible", layout
    if commit not in built:
        try:
            built[commit] = clangtool.build(why.prepare_source(commit, work, True, moves=False))
        except (Exception, SystemExit) as error:  # why exits on a params.hpp it can't switch
            built[commit] = f"build failed: {type(error).__name__}: {error}"[:300]
    if isinstance(built[commit], str):  # this run's other games of the version skip without building again
        return built[commit], layout
    played = why.play(replay, built[commit], game["side"], good[0])
    if played.difference:
        return f"our bot diverged in round {played.difference[0]}", layout
    return "saved", gzip.compress(played.blob, 6)


# The pearl-variant pass: every layout with VARIANT_MIN or more games skipped for their timings across the kept fit
# folders gets rebuild_variant from those games, once, and again only when VARIANT_GROWTH times the games it was last
# tried on wait; a file the engine confirms goes to VARIANTS and its games' skips are cleared so the next run replays
# them. At most VARIANT_FILES files a layout (the oldest dropped: a later one was fitted on games no earlier file
# played), so VARIANTS holds at most VARIANT_FILES x the layouts ever tried (the ladder's 10 maps, a few dragon counts
# each: tens of files under 110 KB). Stops starting layouts, or playing a layout's games, past `deadline` (monotonic);
# a layout that raises is recorded as tried with its error. Returns one line a layout tried.
def rebuild_variants(state: Path, record: dict, deadline: float = math.inf) -> list[str]:
    waiting: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for sid in versions(state):
        saved = {g["id"]: g for g in online.read(state / "online-replays" / str(sid) / "index.json",
                                                  {"games": []})["games"]}
        for gid, skip in index_of(state, sid)["skipped"].items():
            if skip.get("reason") in TIMINGS and int(gid) in saved:
                waiting[skip.get("layout")].append((sid, saved[int(gid)]))
    tried = record.setdefault("variants", {})
    lines = []
    for layout, games in waiting.items():
        last = tried.get(layout, {})
        if len(games) < max(VARIANT_MIN, VARIANT_GROWTH * last.get("games", 0)):
            continue
        if time.monotonic() > deadline:
            lines.append("online fit: pearl variants: out of time, the rest wait for the next run")
            break
        try:
            result, name = rebuild_layout(state, layout, games, last, deadline)
        except (Exception, SystemExit) as error:  # tools/why.py exits on some failures
            result, name = f"error: {type(error).__name__}: {error}"[:300], layout[:12]
        lines.append(f"online fit: pearl variant of {name}: {result}")
        if result.startswith("out of time"):  # not a try: the next run takes it up again
            break
        tried[layout] = {**last, "games": len(games), "at": time.time(), "result": result}
    return lines


# One layout's rebuild (rebuild_variants): (its result line, the map's name). Writes the file and clears the games'
# skips when the engine plays VARIANT_SHARE of them again, and drops the layout's oldest files past VARIANT_FILES
# (`last`'s "files", which it updates).
def rebuild_layout(state: Path, layout: str, games: list[tuple[int, dict]], last: dict,
                   deadline: float) -> tuple[str, str]:
    import replays
    import why
    loaded = [replays.load_replay(Path(g["replay"])) for _, g in games]
    name = next((line[len("MAP_NAME "):] for line in loaded[0].map.splitlines() if line.startswith("MAP_NAME ")),
                layout[:12])
    try:
        ranges = ladder_maps.rebuild_variant([(r.seed.value, spawned(r), last_round(r)) for r in loaded],
                                             loaded[0].map, base_text(state, loaded[0].map))
    except ValueError as error:
        return f"no fit: {error}", name
    good = 0
    for r in loaded:
        if time.monotonic() > deadline:
            return f"out of time after {good} played again", name
        good += why.play(r, None, None, ("variant", ladder_maps.with_ranges(r.map, ranges))).difference is None
    result = f"{good} of {len(games)} played again"
    if good < VARIANT_SHARE * len(games):
        return result, name
    text = ladder_maps.with_ranges(loaded[0].map, ranges)
    path = state / ladder_maps.VARIANTS / f"{ladder_maps.content_hash(text)}.map"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    files = [f for f in last.get("files", []) if f != path.name] + [path.name]
    for old in files[:-VARIANT_FILES]:
        (path.parent / old).unlink(missing_ok=True)
    last["files"] = files[-VARIANT_FILES:]
    for sid in {sid for sid, _ in games}:
        done = index_of(state, sid)
        done["skipped"] = {k: v for k, v in done["skipped"].items()
                           if not (v.get("layout") == layout and v.get("reason") in TIMINGS)}
        online.save(folder(state, sid) / "index.json", done)
    return result + f": kept as {path.name}", name


# The map file whose layout, dragons aside, is `text`'s (the timings a variant most likely keeps), else `text`: the
# newest such file of the map folders (a map's latest version from the site, ladder-map-files, before an older copy),
# rebuilt variants left out.
def base_text(state: Path, text: str) -> str:
    def shape(t: str) -> str:
        return ladder_maps.layout_hash("\n".join(line for line in t.splitlines()
                                                 if line.split()[:1] not in (["DRAGON"], ["DRAGON_COUNT"])))
    want, found = shape(text), []
    for root in map_dirs(state):
        if root.name == ladder_maps.VARIANTS or not root.is_dir():
            continue
        for path in root.glob("*.map"):
            other = path.read_text(errors="replace")
            if not ladder_maps.gapless(other) and shape(other) == want:
                found.append((path.stat().st_mtime, other))
    return max(found)[1] if found else text


# Each tile's spawns in a replay, {tile: [rounds]}: a pearl appearing at a round's start, before any dragon moves.
def spawned(replay) -> dict[tuple[int, int], list[int]]:
    found: dict = defaultdict(list)
    r, mover = -1, None
    for event in replay.events:
        kind = event.which()
        if kind == "roundStart":
            r, mover = event.roundStart.round, None
        elif kind == "turnStart":
            mover = event.turnStart.id
        elif kind == "tileChange" and event.tileChange.hasPearl and mover is None:
            found[(event.tileChange.tile.x, event.tileChange.tile.y)].append(r)
    return found


def last_round(replay) -> int:
    return max((e.roundStart.round for e in replay.events if e.which() == "roundStart"), default=0)


# One run: pending games newest first, each waiting its turn (why.wait_turn), until RUN_S or `limit`; then the
# variant pass, until VARIANT_S more; and, however it ends, the old folders' deletion. A game that raises is skipped
# with its error (RETRYABLE), so one bad replay never stops the batch. Updates online-fit.json as it goes.
def run(state: Path, limit: int | None = None) -> dict:
    import why
    started = time.monotonic()
    record = online.read(state / RECORD, {})
    counts: dict[str, int] = defaultdict(int)
    built: dict = {}
    commits: dict[int, str | None] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="online-fit-") as work:
            for sid, game in pending(state):
                if (limit is not None and sum(counts.values()) >= limit) or time.monotonic() - started > RUN_S:
                    break
                if sid not in commits:
                    commits[sid] = why.submission_commit(sid, state)
                done, at = index_of(state, sid), time.monotonic()
                if not commits[sid]:
                    status, out = "no commit known for this version", ""
                else:
                    turn = why.wait_turn()
                    try:
                        status, out = replay_one(state, game, commits[sid], built, Path(work))
                    except (Exception, SystemExit) as error:
                        status, out = f"error: {type(error).__name__}: {error}"[:300], ""
                    finally:
                        if turn:
                            turn.close()
                seconds = round(time.monotonic() - at, 1)
                folder(state, sid).mkdir(parents=True, exist_ok=True)  # a version's first game may be a skip
                if status == "saved":
                    path = folder(state, sid) / f"{game['id']}.replay"
                    path.write_bytes(out)
                    done["games"].append({**{k: game.get(k) for k in ROW}, "replay": str(path), "seconds": seconds,
                                          "commit": commits[sid]})
                else:
                    tries = done["skipped"].get(str(game["id"]), {}).get("tries", 0) + 1
                    done["skipped"][str(game["id"])] = {"reason": status, "layout": out or None, "seconds": seconds,
                                                        "at": time.time(), "tries": tries}
                online.save(folder(state, sid) / "index.json", done)
                counts["saved" if status == "saved" else status.split(" in round")[0].split(":")[0]] += 1
            lines = rebuild_variants(state, record, started + RUN_S + VARIANT_S)
    finally:  # the size bound holds whatever stopped the run
        keep = {str(v) for v in versions(state)}
        for old in (state / FOLDER).iterdir() if (state / FOLDER).is_dir() else []:
            if old.is_dir() and old.name not in keep:
                shutil.rmtree(old)
    record.update(finished_at=time.time(), error=None, last={"counts": dict(counts), "variants": lines,
                                                             "seconds": round(time.monotonic() - started)})
    online.save(state / RECORD, record)
    return record


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# The upkeep's step: a run in the background (nice 19) at most every EVERY while games wait and none is running; with
# none waiting, looked for again CHECK_S later (the tick comes every minute; pending reads two indexes).
CHECK_S = 600


def tick(state: Path, now: float) -> str | None:
    record = online.read(state / RECORD, {})
    if (alive(record.get("pid")) or now - record.get("started_at", 0) < EVERY
            or now - record.get("checked_at", 0) < CHECK_S):
        return None
    if not pending(state, now):
        online.save(state / RECORD, {**record, "checked_at": now})
        return None
    with (state / "online-fit.log").open("w") as log:
        proc = subprocess.Popen(["nice", "-n", "19", sys.executable, str(Path(__file__).resolve()), "run", str(state)],
                                cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    online.save(state / RECORD, {**record, "pid": proc.pid, "started_at": now})
    return f"online fit rows started in the background (pid {proc.pid}, {state / 'online-fit.log'})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "pending"))
    parser.add_argument("state", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command == "pending":
        by = defaultdict(int)
        for sid, _ in pending(args.state):
            by[sid] += 1
        print(json.dumps(by))
        return 0
    try:
        print(json.dumps(run(args.state, args.limit).get("last")))
    except (Exception, SystemExit) as error:
        record = online.read(args.state / RECORD, {})
        online.save(args.state / RECORD, {**record, "finished_at": time.time(),
                                          "error": f"{type(error).__name__}: {error}"[:500]})
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
