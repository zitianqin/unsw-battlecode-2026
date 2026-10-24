"""bceval's reading of our replays for the loop's reports: each game's win chance by round and its turning point.

bceval (xCirno1's MIT model, https://github.com/xCirno1/battlecode-eval) is a logistic model per round over 23
features in 6 groups (bodies, economy, fights, ground, champion, side). It runs in its own venv as a subprocess of
this file (`--child`), never imported into a loop process, from the approved commit of its clone only
(tools/bceval_sync.py: synced with GitHub before each use). `read` gives a replay's rows every 5 rounds (round, P(A
wins), log-odds by group), cached per replay content and approved commit in <loop home>/bceval-cache/ (at most
CACHE_MAX files of about 8 KB, the oldest used deleted first; regenerable), run at nice 19: about 0.1 s for a short
site replay, 1 s for a 13 MB local one with debug lines, killed after CHILD_TIMEOUT. `turning` finds where a game was
lost: the round from START on where our log-odds of winning, the groups' sum without the side term, fell most over
the next WINDOW rounds (log-odds, not P: a P near 0 or 1 hides a fall), and the group whose log-odds fell most then.
`section` tables
turning points by map and opponent for the reports (the hooks: online.md, the audit's context, rule-counts.md,
study.md, the riser study's context, evidence.md and the post-mortem's .postmortem/turning-points.md).

How far to trust it (the study of 2026-09-28 22:45, 1,360 replays; CAVEAT says it in every report): it calls our
games right 80-88% of the time from r101 and is noise before r100; its absolute P is biased by opponent strength
(+0.74 log-odds on our unranked sweeps against weaker teams, none on dev games against stronger ones). So the
reports give only changes and rankings (where the log-odds fell, which group carried the fall), never P or a fixed
offset. A
report, never a gate or an acceptance input.

Site replays since 2026-09-27 list pearl timings as `x y 0 0`, which would zero bceval's five spawn-rate features; the
child fills them from the map file of that name (map_dirs: the site's own copies first) whose spawning tiles hold the
most of the pearls the replay shows appearing, when that is at least FILL_SHARE of them (death drops appear too, so a
right file holds 75-100% of them: Devil 288 of 381 on 2026-09-28), else leaves them empty (`rates` says which). Of
files that tie, the first in map_dirs order and then by name is used, and the tie is alerted once per map (log_tie).

    .venv/bin/python tools/bceval_read.py FILE... --team A|B [--json]   # each game's turning point, then the table
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

READER = 2  # bump when the child's output or `turning` changes, so cached and stored readings are redone
START = 100  # bceval is noise before r100
WINDOW = 25
GROUPS = ("bodies", "economy", "fights", "ground", "champion")  # the side term carries no fall
EVERY = 5
CACHE = "bceval-cache"
CACHE_MAX = 4000
FILL_SHARE = 0.5
# A smaller largest fall (log-odds) from r100 on: decided before r100, or never fell, so no group. 0.2 is a fall of
# 0.05 in P at even odds; in 40 of 9918's losses on 2026-09-28 the largest was 0 twice, 0.67 once and 0.97-5.6 else.
MIN_FALL = 0.2
CHILD_TIMEOUT = 30  # s; the slowest reading, a 13 MB local replay, takes about 1 s
TURN = (f"the {WINDOW} rounds from r{START} on over which its log-odds of winning (bceval's groups summed, the side "
        "term left out) fell most, and the group that carried the fall")
TOP = 8  # rows a section gives each label (the commonest)
REPO = Path(__file__).resolve().parent.parent
CAVEAT = ("bceval is a report, never a gate: it calls games right 80-88% of the time from r101 and is noise before "
          "r100, and its absolute P is biased by opponent strength (+0.74 log-odds against weaker teams on our "
          "unranked sweeps, none against stronger dev teams; study 2026-09-28), so read only where the log-odds fell "
          "and which group carried the fall, never P itself or a fixed offset.")


class BcevalError(RuntimeError):
    pass


def map_dirs(home: Path) -> list[Path]:
    return [home / "ladder-map-files", REPO / "maps/ladder", REPO / "maps/sprint", REPO / "maps/retired"]


# The version stored beside a reading: the approved bceval commit and READER. Syncs (at most every FETCH_EVERY).
def version(home: Path | None = None) -> str:
    return f"{sync_module().sync(home)[:12]}.{READER}"


# tools/bceval_sync, imported only in the parent (the child needs nothing but the standard library and bceval).
def sync_module():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bceval_sync
    return bceval_sync


# A replay's rows as bceval reads them: {"map", "winner", "last_round", "ok", "rates", "rows": [[round, p_a,
# {group: log-odds}], ...]} for the board at the start of each round (1, 5, 10, ... 500; the final board left out).
# Raises BcevalError when bceval can't run (sync.ready's reason) or the child fails.
def read(replay: Path, home: Path | None = None) -> dict:
    bceval_sync = sync_module()
    home = home or bceval_sync.loop_home()
    clone, approved = bceval_sync.ready(home)
    if clone is None:
        raise BcevalError(approved)
    digest = hashlib.sha256(Path(replay).read_bytes()).hexdigest()[:32]
    folder = home / CACHE
    cached = folder / f"{digest}-{approved[:12]}.{READER}.json"
    try:
        value = json.loads(cached.read_text())
        os.utime(cached)
        return value
    except (OSError, ValueError):
        pass
    command = ["nice", "-n", "19", str(bceval_sync.VENV_PY), "-E", "-s", "-B", str(Path(__file__).resolve()),
               "--child", str(clone), str(replay), *map(str, map_dirs(home))]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=CHILD_TIMEOUT,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home())})
    except subprocess.TimeoutExpired as error:
        raise BcevalError(f"timed out after {error.timeout:.0f} s") from None
    if result.returncode != 0:
        raise BcevalError((result.stderr.strip().splitlines() or [f"exit {result.returncode}"])[-1][:300])
    value = json.loads(result.stdout)
    if tie := value.pop("tie", None):
        log_tie(home, value["map"], tie)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")))
    temporary.replace(cached)
    prune(folder)
    return value


def prune(folder: Path, limit: int = CACHE_MAX) -> None:
    entries = list(folder.glob("*.json"))
    if len(entries) <= limit:
        return
    entries.sort(key=lambda p: p.stat().st_mtime)
    for path in entries[:len(entries) - int(limit * 0.9)]:
        path.unlink(missing_ok=True)


# Alerts once per map name (recorded in bceval.json's "ties") that map files tied on the replay's pearl spawns and
# the first was used (fill).
def log_tie(home: Path, name: str, tie: str) -> None:
    bceval_sync = sync_module()
    with bceval_sync.locked(home):
        record = bceval_sync.read_state(home)
        ties = record.setdefault("ties", [])
        if name in ties:
            return
        ties.append(str(name)[:40])
        bceval_sync.save_state(home, record)
    bceval_sync.alert(home, f"bceval: map files named {name!r} tie on a replay's pearl spawns ({tie[:200]}); the "
                            "first in bceval_read.map_dirs order is used. Logged once a map")


# Where `side` lost the game in a reading (read's value): {"round", "to", "fall", "group", "deltas"} for the round r
# from START on where its log-odds of winning (GROUPS summed: the side term, which the round alone sets, left out) fell
# most from r to r + WINDOW (or the game's last row, when it ended sooner), `fall` in log-odds, `group` the group whose
# log-odds fell most over it (None when the fall is under MIN_FALL: decided before r100, or never fell), `deltas` each
# group's change. None when the game has fewer than two rows from START on.
def turning(reading: dict, side: str) -> dict | None:
    sign = 1 if side == "A" else -1
    rows = {}
    for r, _, parts in reading["rows"]:
        if r >= START:
            ours = {g: sign * v for g, v in parts.items()}
            rows[r] = (sum(ours.get(g, 0) for g in GROUPS), ours)
    rounds = sorted(rows)
    best = None
    for r in rounds:
        end = max(t for t in rounds if t <= r + WINDOW)
        if end > r and (best is None or rows[r][0] - rows[end][0] > best[0]):
            best = (rows[r][0] - rows[end][0], r, end)
    if best is None:
        return None
    fall, r, end = best
    deltas = {g: round(rows[end][1].get(g, 0) - rows[r][1].get(g, 0), 3) for g in GROUPS}
    group = min(deltas, key=deltas.get) if fall >= MIN_FALL and min(deltas.values()) < 0 else None
    return {"round": r, "to": end, "fall": round(fall, 3), "group": group, "deltas": deltas}


# Whether the hooks read games with bceval: not when $BCEVAL_OFF is set (loop/testhome.py sets it, so no loop test
# fetches from GitHub or adds a report section).
def enabled() -> bool:
    return not os.environ.get("BCEVAL_OFF")


# One game's stored reading for the reports: {"v": version, "turn": turning or None, "rates"}, or {"v", "error"}
# when bceval couldn't read it; None while it is off (enabled). Never raises.
def game(replay: Path, side: str, home: Path | None = None) -> dict | None:
    if not enabled():
        return None
    v = None
    try:
        v = version(home)
        reading = read(replay, home)
        return {"v": v, "turn": turning(reading, side), "rates": reading.get("rates")}
    except Exception as error:  # a report must never fail for bceval
        return {"v": v, "error": f"{type(error).__name__}: {error}"[:200]}


def short(stored: dict | None) -> str:
    """'turning point r245-270 (log-odds fell 1.31, ground)' for a game's stored reading, or why there is none."""
    if not stored:
        return "no bceval reading"
    if stored.get("error"):
        return f"bceval failed: {stored['error'][:80]}"
    turn = stored.get("turn")
    if not turn:
        return f"no bceval turning point (over before r{START + EVERY})"
    if not turn["group"]:
        return f"no clear bceval turning point (its largest log-odds fall from r{START} was {turn['fall']:.2f})"
    return f"turning point r{turn['round']}-{turn['to']} (log-odds fell {turn['fall']:.2f}, {turn['group']})"


# A report's turning-point table (markdown lines) over `entries`, (labels, stored reading) per loss: a row for all,
# then the commonest TOP of each label in `keys` (e.g. map, opponent), each with the games, how many have a turning
# point, how many of those each group carried, and the median round and fall. `what` names the games.
# No lines while bceval is off (every stored reading None).
def section(entries: list[tuple[dict, dict | None]], keys: tuple[str, ...], what: str) -> list[str]:
    if not enabled() or entries and all(s is None for _, s in entries):
        return []
    if not entries:
        return [f"Turning points (bceval): no {what}.", ""]
    versions = sorted({(s or {}).get("v") or "?" for _, s in entries})
    failed = [s for _, s in entries if s and s.get("error")]
    lines = [f"Turning points (bceval {', '.join(v[:7] for v in versions)}, tools/bceval_read.py) in {len(entries)} "
             f"{what}: in each, {TURN} (none: no fall of {MIN_FALL} log-odds or more from r{START} on, so decided "
             f"before r{START} or never behind). " + CAVEAT
             + (f" {len(failed)} could not be read ({failed[0]['error'][:100]})." if failed else ""), "",
             f"| games | n | with a turning point | {' | '.join(GROUPS)} | none | median round | median fall "
             "(log-odds) |",
             "|---|---|---|" + "---|" * len(GROUPS) + "---|---|---|"]

    def row(name: str, rows: list[dict | None]) -> str:
        turns = [s["turn"] for s in rows if s and s.get("turn")]
        count = {g: sum(t["group"] == g for t in turns) for g in (*GROUPS, None)}
        median = (lambda xs: f"{statistics.median(xs):g}" if xs else "-")
        groups = " | ".join(str(count[g]) for g in GROUPS)
        return (f"| {name} | {len(rows)} | {len(turns)} | {groups} | {count[None]} | "
                f"{median([t['round'] for t in turns])} | "
                f"{median([round(t['fall'], 2) for t in turns])} |")

    lines.append(row("all", [s for _, s in entries]))
    for key in keys:
        values: dict[str, list] = {}
        for labels, stored in entries:
            values.setdefault(str(labels.get(key, "?")), []).append(stored)
        for value, rows in sorted(values.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP]:
            lines.append(row(f"{key} {value}", rows))
    return lines + [""]


# One line for a report block (study.md's teams, the riser study's periods): "Turning points (bceval) in its 12 losses
# and draws: bodies 5, ground 3, none 2, ...; median r245, log-odds fall 1.31.", or "" with no games or while bceval
# is off.
def brief(stored: list[dict | None], what: str) -> str:
    if not enabled() or all(s is None for s in stored):
        return ""
    turns = [s["turn"] for s in stored if s and s.get("turn")]
    count = {g: sum(t["group"] == g for t in turns) for g in (*GROUPS, None)}
    groups = ", ".join(f"{g or 'none'} {n}" for g, n in sorted(count.items(), key=lambda kv: -kv[1]) if n)
    failed = sum(bool(s and s.get("error")) for s in stored)
    text = f"Turning points (bceval, the group carrying the largest fall from r{START}) in {len(stored)} {what}: "
    if not turns:
        return text + "none found" + (f" ({failed} unreadable)." if failed else ".")
    return (text + f"{groups}; median r{statistics.median(t['round'] for t in turns):g}, log-odds fall "
            f"{statistics.median(t['fall'] for t in turns):.2f}" + (f"; {failed} unreadable." if failed else "."))


# ------------------------------------------------------------------------------------------------ the child

# The child's map-text fill (module docstring): (rates, source, tie) from the best file named like the replay's map,
# the first in `dirs` order and then by name among files that tie; `tie` names the tied files, or "".
def fill(features, rep, dirs: list[str]) -> tuple[dict, str, str]:
    name = features._map_name(rep.map)
    spawns: dict[tuple[int, int], int] = {}
    for event in rep.events:
        if event.which() == "tileChange" and event.tileChange.hasPearl:
            tile = (event.tileChange.tile.x, event.tileChange.tile.y)
            spawns[tile] = spawns.get(tile, 0) + 1
    total, best, tied = sum(spawns.values()), None, []
    for folder in dirs:
        for path in sorted(Path(folder).glob("*.map")) if Path(folder).is_dir() else []:
            text = path.read_text()
            if features._map_name(text) != name or not (rates := features._spawn_rates(text)):
                continue
            covered = sum(n for tile, n in spawns.items() if tile in rates)
            if best is None or covered > best[0]:
                best, tied = (covered, rates, path), []
            elif covered == best[0] and rates != best[1]:
                tied.append(path)
    if best is None:
        return {}, f"none (no map file named {name!r} gives pearl timings)", ""
    if total and best[0] < FILL_SHARE * total:
        return {}, f"none ({best[2].name} holds {best[0]} of the {total} spawns seen)", ""
    tie = f"{best[2]} kept over {', '.join(map(str, tied))}, each {best[0]} of {total} spawns" if tied else ""
    return best[1], f"filled from {best[2]} ({best[0]} of {total} spawns on its tiles)", tie


def child(clone: str, replay: str, dirs: list[str]) -> None:
    sys.path.insert(0, clone)
    from bceval import features
    from bceval.replay import load
    from bceval.score import logit_parts
    rep = load(replay)
    source, tie = "replay", ""
    if not features._spawn_rates(rep.map):
        rates, source, tie = fill(features, rep, dirs)
        original = features._spawn_rates
        features._spawn_rates = lambda text: rates if text == rep.map else original(text)
    features.load = lambda _path: rep  # parsed once
    game, rows = features.replay_features(replay, every=EVERY)
    out = []
    for row in rows:
        if row["round"] == 501:  # the board after the last round
            continue
        parts = logit_parts(row["round"], row["a"], row["b"])
        z = max(-40.0, min(40.0, sum(parts.values())))
        out.append([row["round"], round(1 / (1 + math.exp(-z)), 4), {g: round(v, 4) for g, v in parts.items()}])
    print(json.dumps({"map": game["map"], "winner": game["winner"], "last_round": game["last_round"],
                      "ok": game["ok"], "rates": source, "rows": out, **({"tie": tie} if tie else {})}))


def main() -> None:
    if sys.argv[1:2] == ["--child"]:
        child(sys.argv[2], sys.argv[3], sys.argv[4:])
        return
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--team", required=True, choices=("A", "B"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    stored = [(f, game(f, args.team)) for f in args.files]
    if args.json:
        print(json.dumps({str(f): s for f, s in stored}, indent=1))
        return
    for f, s in stored:
        print(f"{f}: {short(s)}")
    print("\n".join(section([({"file": f.name}, s) for f, s in stored], (), "games")))


if __name__ == "__main__":
    main()
