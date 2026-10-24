"""Checks a candidate plays exactly like the base bot: plays each against itself on every map and compares the
games move by move, both built with one fixed sonar key (shared_key_copies) unless their sonarkey.hpp differ. Games are deterministic, so identical decisions give identical games; a speed-only change
(`"kind": "speed"` ideas), or one meant to leave normal games' decisions unchanged (`"kind": "identical"`), must pass this
instead of an SPRT, which can't tell identical play from no gain.

    uv run referee/identical.py --cand PATH --base PATH --maps PATH... [--workers N|auto] [--out FILE]

Prints one line and exits non-zero unless every game matches. The comparison ignores what isn't play: the bot
names in the header, logs, indicators, debug drawings and CPU readings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import run
from run import REPLAY_SCHEMA, Job, parse_workers, stream_games
from sharedkey import SONAR_KEY

NOT_PLAY = {"engineLog", "dragonLog", "dragonIndicator", "debugDraw"}


# A digest of everything the bots decided in one game: the map, every event that changes the game, the result.
def play_digest(path: Path) -> str:
    replay = REPLAY_SCHEMA.Replay.from_bytes_packed(path.read_bytes(), traversal_limit_in_words=2**62, nesting_limit=128)
    digest = hashlib.sha256(replay.map.encode())
    for event in replay.events:
        kind = event.which()
        if kind in NOT_PLAY:
            continue
        body = getattr(event, kind).to_dict()
        if kind == "dragonAction":
            body.pop("instructions", None)
        digest.update(json.dumps([kind, body], sort_keys=True, default=str).encode())
    digest.update(json.dumps(replay.result.to_dict(), sort_keys=True, default=str).encode())
    return digest.hexdigest()


# Copies of both bots that share one sonar key. With P.sonarBuildKey the key is a hash of the bot's own source, so any
# source change, even a comment, changes which of the other test team's tags collide with ours, and self-play diverges
# (E0116, 2026-09-26: champion plus one comment line failed on 2 of 3 maps). Turning it off in both copies uses the
# fixed key in both. Left as they are when either lacks the switch or the two sonarkey.hpp differ: a change to the key
# itself must show up, and such changes are noloss ideas (E0087).
def shared_key_copies(cand: Path, base: Path, folder: Path) -> tuple[Path, Path]:
    params = [bot / "params.hpp" for bot in (cand, base)]
    keys = [bot / "sonarkey.hpp" for bot in (cand, base)]
    if not all(p.exists() and SONAR_KEY.search(p.read_text()) for p in params) or not all(k.exists() for k in keys) \
            or keys[0].read_bytes() != keys[1].read_bytes():
        return cand, base
    copies = []
    for name, bot in (("cand", cand), ("base", base)):
        copy = folder / name / bot.name
        shutil.copytree(bot, copy, symlinks=True)
        text = (copy / "params.hpp").read_text()
        (copy / "params.hpp").write_text(SONAR_KEY.sub(r"\1false", text))
        copies.append(copy)
    return copies[0], copies[1]


# How many replays check digests at once after its games, which run on this machine: half its cores (8 at most), and
# no more than it may start games now (run.game_slots, which native.play_script points at this Mac's load gate, so
# the digests keep to the loop's load share), but always one, so the check finishes.
def digest_workers(workers: int | str) -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2, run.game_slots(workers)))


# Plays both bots' self-play games and returns (every game matched, one-line report, per-map details).
def check(cand: Path, base: Path, maps: list[Path], workers: int | str) -> tuple[bool, str, list[dict]]:
    # Both games of a comparison must be built by the same compiler, so none go to hosts of another platform.
    with tempfile.TemporaryDirectory(prefix="identical-") as scratch:
        folder = Path(scratch)
        cand, base = shared_key_copies(cand, base, folder / "bots")
        jobs = [Job(m, bot, bot, "A") for bot in (cand, base) for m in maps]
        results = list(stream_games(jobs, [cand, base], workers, sandbox=False, replays=folder,
                                    same_platform=True))
        files = [folder / f"{n:04d}-{job.map.stem}-aA.replay" for n, job in enumerate(jobs)]
        readable = [f for f, r in zip(files, results) if not r.error and f.exists()]
        # Reading a replay takes seconds on a big map, so digest them across this machine's cores; a coder's sandbox
        # can't start a process pool (sysconf is denied: E0116, E0127), so threads there.
        digesters = digest_workers(workers)
        try:
            with ProcessPoolExecutor(max_workers=digesters) as pool:
                digests = dict(zip(readable, pool.map(play_digest, readable)))
        except (OSError, PermissionError, NotImplementedError):
            with ThreadPoolExecutor(max_workers=digesters) as pool:
                digests = dict(zip(readable, pool.map(play_digest, readable)))
        rows = []
        for i, m in enumerate(maps):
            pair = (files[i], files[i + len(maps)])
            if not all(f in digests for f in pair):
                errors = [results[n].error for n in (i, i + len(maps)) if results[n].error]
                rows.append({"map": str(m), "same": False, "error": (errors or ["no replay"])[0]})
                continue
            rows.append({"map": str(m), "same": digests[pair[0]] == digests[pair[1]]})
    same = sum(r["same"] for r in rows)
    first = next((Path(r["map"]).name for r in rows if not r["same"]), None)
    line = f"IDENTICAL {'PASS' if same == len(maps) else 'FAIL'} {same}/{len(maps)} maps" + (
        f"; first difference on {first}" if first else "")
    return same == len(maps), line, rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a candidate plays move for move like the base bot.")
    parser.add_argument("--cand", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--maps", type=Path, nargs="+", required=True)
    parser.add_argument("--workers", type=parse_workers, default="auto")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    passed, line, rows = check(args.cand, args.base, args.maps, args.workers)
    print(line)
    if args.out:
        args.out.write_text(json.dumps({"ok": passed, "line": line, "maps": rows}, indent=1) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
