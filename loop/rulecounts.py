"""Rule counts for a review's code audit (method note M17): before the review worker starts, the orchestrator's own
(unsandboxed) code plays the current champion's logging copy (debugIndicator on: one LOG line a turn, play unchanged)
on MAPS fresh maps drawn by style (STYLES of each, from POOL generated a style: M66) and across them by size
(SIZES; M36, when 8 random maps held one of 2400+ tiles and one 920-tile map made the split-out means; within a size,
maps inside the official maps' pearl supply first and each map's supply printed: M97), each map
against every one of OPPONENTS, one game each (the logging copy's side alternating by map, so both sides are covered), and writes what those
games show into the review folder's rule-counts.md, which the review's prompt points at. Every opponent meets the same
maps, so its column differs from the others by opponent, not by map (method note M25: when the maps rotated over the
opponents, one 64x64 all-spawn map made the breeder's means), a column per size gives every opponent's games on that
size's maps, and each cell gives the median over maps beside the mean. Reviews run confined (no hosts, no network, a
read-only loop home) and this Mac is usually over its load gate, so a review couldn't play these games itself: the
targets.hpp audit of 2026-09-26 fell back to rule counts from logging copies 20+ promotions old.

Everything it builds, plays, keeps and decides from lives in <loop home>/rule-games/<review folder name>/ (a live
state entry, so the confined worker may read it but never write it): the bots, built fresh from the champion commit
each time, the maps, the kept replays, the canonical rule-counts.md, status.json (measuring, done or failed, with
the measuring process's pid and start time), and the worker's saved prompt (its stdin) and session.json and
session.stderr (its output). The review folder, which the worker controls, only receives a copy of rule-counts.md,
once, before the worker's first run; nothing there is read, built or followed. The measurement runs only then: a
relaunch (meta.run_meta retries a failed one once, then abandons the review) never measures again.

The games go through the coders' measurement path (`native.py games review ...`): the main checkout's referee, the
game hosts at a coding lane's share while an evaluation runs (native.yield_to_evaluations), and this Mac only within
its load gate, never waiting for it. At most DEADLINE_S in all; whatever finished by then is counted. Any failure
leaves a note in rule-counts.md, health.py alerts on it, and the review still runs.

What it counts (per game, or as a share of the logging team's turns): which branch of main.cpp decided each turn (a
one-step ram, a sprint that started a head-on or not, a split, the best-scored step or a hunt's step, or another
single step: feed, fold or king), the target and space and score band of the best-scored move from the log line,
split child lengths, pearls, head-ons started, each team's share of rounds within tools/replays.py CAP_MARGIN of the
unit limit (M71: cap rules fire only there; by size in the size columns), and tools/watch.py scan's moments
(circling, hungry, deaths by cause, young deaths, leads lost, and apart from those the deaths the debug line shows were on purpose: a feed or
a split-out's parent), and how each death came about, ours against the opponent's (tools/watch.py doom: where the
dying head was and what shut it in, MISSES 64, and a teammate trap's shutter and whether it split, M93; our `deeper` ones by how the scorer rated the dooming move, M67; ours
again with our debug lines' deliberate deaths filed first, M63), and where the logging copy's losses turned (bceval's
turning points, tools/bceval_read.py; a report whose absolute P is never read). Any other key a later champion adds to its debug
log line is tallied automatically (M40: numbers were dropped until 2026-09-27): a word by its commonest values ("rule ram"), a 0/1 flag (FLAG_KEYS: farm,
king, feed) as its share set, an id key (ID_KEYS: fold) as its share set, any other number by NUMBER_BANDS, and a
line names every key seen and how it was tallied (log_keys). A second section, the events (method notes M39, M30), gives fixed per-event tallies over every
game before its replay is deleted: each ram's victim gain (by our length before the move), our share of its drops and
its net, their share and the swing (M51: the victim's loss net of its team's pickups, less ours), by victim gain, by
steps and (when the debug line has I352's `edge`) by head edge, and each action's failures on the dragon's own move
over its turns, with its rams and deliberate deaths counted apart, and a portal crossing's deaths by what they hit (M49: teammate's
head or body, enemy head or body, itself, kelp), each folding dragon's outcome (merged by a feed, split out (a
split-out's parent), killed by an enemy, crashed on its own move, died otherwise or alive), each crash from foldRound on by path steps to the king
(M49), and each farm entry's by length and phase (M40), and logged turns by the dragon's age
and birth length with their body < len share and split-out rate (M43), and split children by the rule their parent's
debug line gave on the split turn (split, splitout, shed, threatshed or rule unknown): their first-3-round failures,
repeat split-outs within 5 rounds, feeds and merges into the king (M50). A third
counts what decides round 500 (method note M26), from every game too: each team's longest at the end of r300, as the
champion's foldRound starts and at the end of r499, our growth to r499 and each team's from the fold's start by source
(pearls its king ate, teammates merged into it 2 long or 3+, less its king's own splits and sprint steps, what a
parent kept at a split handing the king on to a shorter child, and the longest's lead over its king at the fold's
start), each team's king's length as the fold starts and what its splits and sprint steps cost it over the game
(method note M47); deaths of our team's longest dragon (8+
and 12+ long) by phase (before foldRound, to mergeRound, from it) and cause (on purpose, head-on into a shorter enemy
head, an equal or longer one, its own body, a teammate's head, trapped or other); the share of our logged turns
whose debug line has body < len, by length band and phase; and two kings (M86): the round-limit losses where half our
second-longest at r499, rounded up, exceeded the margin, by its fold gate at r450, and the wins where theirs did.
Only some replays are kept (keep): KEEP_REPLAYS losses
first, up to KEEP_LAST that reached r499, one of each size none of those is on, and a blind pick for each opponent,
on a map rotated across the sizes and sides by the review's id and whatever its result (blind_picks; M39: losses
first overstate failure). Beside the local rows it sets what the trusted side reads online when the games are counted
(online_counterparts: M72, M75, M78): the live build's result-blind ladder and autoscrim games' ram counts, each
ram cell's swing (ours and theirs on us, by victim gain and head edge: ram_swing_lines, M85) and round-500 king
pair, and the pearl race (tools/replays.py race_text) here, online and for the leaders (ranks 1-2's
studied ranked games, maintenance.leader_facts). The events also tally farm entries by folding dragons apart, our
longest's hitSelf deaths by the cap flag and own-move deaths at the cap by when exact reach (tools/watch.py doom)
and the bot's own flag found them doomed (M76, M83). Each team's portal crossings and in-place deaths (tools/replays.py
portal_crossings, in_place_deaths: M80, M84) sit beside the live build's online games' and the leaders', and our
`deeper` deaths also by how far below it the runner-up move scored and our heads in its room (deeper_split, M81).
Each pearl-target pick's outcome, by target kind (target_picks, M91: eaten by its picker within 30 rounds, episode
turns, switches and returns), is its own section. Beside the local games, the live build's result-blind online games
are played again with our debug line on (M90: online replays carry none; tools/why.py, niced and under the load
gate, at most ONLINE_SAMPLE games in ONLINE_BUDGET_S, start_online) and those that reproduce are a column of their
own in the three tables and the picks.

    .venv/bin/python loop/rulecounts.py measure STATE WORK   # native claim starts this in the background
    .venv/bin/python loop/rulecounts.py review STATE WORK [--measure]   # meta.launch_review: (first launch: measure,)
                                                                        # then become the worker
    python loop/rulecounts.py online STATE OUT SEED DEADLINE PARENT   # start_online's process: the online replays (M90)
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import bceval_read  # noqa: E402
import meta  # noqa: E402
import quota  # noqa: E402

if TYPE_CHECKING:
    import replays

# Fresh maps by style (mapgen/gen.py --style) and, across the styles, by size (meta.AREA_BANDS' names), one game on
# each against each opponent: 24 games. Method note M66: drawn from eval alone, a review's 24 games crossed 29 portals
# where fadc209's crossed 1,050, so no review could audit a portal rule; hence our eval maps, near copies of the
# ladder's, worldgen's (half of every SPRT's), and one each of portalrooms and deadend. M36: 8 maps drawn at random
# held one of 2400+ tiles, and one churning 920-tile map made the split-out means, hence the sizes.
STYLES = {"eval": 2, "near": 2, "worldgen": 2, "portalrooms": 1, "deadend": 1}
SIZES = {"<=1024": 2, "1025-2399": 4, ">=2400": 2}  # what the draw aims at over all its styles
MAPS = sum(STYLES.values())
POOL = 12  # maps generated a style to draw from (seed 777, 2026-09-28: 12 worldgen maps took 8 s and were 11 of <=1024)
OPPONENTS = ("champion", "swarm", "breeder")  # every map is played against each (referee/gauntlet.toml names)
DEADLINE_S = 40 * 60
KEEP_REPLAYS = 6  # replays kept for the review to watch, losses first (2-13 MB each), until the next review's
# At least this many of the kept replays reached r499, when that many games did: the losses-first pick above kept
# 1 of 6 such for one review (method note M29), which then had to judge the round-500 king from selected losses.
KEEP_LAST = 3
NAME = "rule-counts.md"
MEASURING = "# Rule counts: being measured"
FAILED = "# Rule counts: not measured"
KNOWN_KEYS = {"r", "len", "body", "dir", "score", "space", "target"}  # the champion's debug line, counted below
# Any other key of the debug line is tallied as a share of logged turns (method note M40: numeric values were dropped,
# so E0127's farm, king, fold and feed never reached a review): a 0/1 flag as its share set to 1, an id key as its
# share set to an id (not -1), any other number by NUMBER_BANDS, and a word by its commonest values (log_value).
FLAG_KEYS = {"farm", "king", "feed"}  # E0127: 1 when the turn entered a farm, was the king's, or fed a teammate
ID_KEYS = {"fold"}  # E0127: the king a folding dragon walks to, -1 when it isn't folding
FLAG_ON, ID_SET = "1", "set (an id, not -1)"
NUMBER_BANDS = ("<= -2", "-1", "0", "1", ">= 2", "not a finite number")
STEPS = set("NESW")
RAM = "ram (one step that started a head-on with an enemy)"
BRANCHES = (RAM, "sprint that started a head-on with an enemy (a ram)",
            "sprint with no head-on (an escape, or a ram that missed)", "split", "best-scored step (or a hunt's step)",
            "other single step (feed, fold or king)")
CHILD_BUCKETS = ((1, 2), (3, 4), (5, 8), (9, 16), (17, 999))  # split child lengths, reported per bucket
DOOMED = "<= -1e9 (every move lethal or a bad trade)"
PENALISED = "-1e9 to -1e5 (a risk, pocket-shortfall or unknown-portal penalty)"
# What decides round 500 (method note M26): the phase boundaries when the champion's params.hpp has no foldRound or
# mergeRound (2c79f81's values), the rounds lengths are read at besides foldRound's start, the lengths a dying
# longest dragon is counted from, and the length bands of the untracked-body shares (see `band`).
FOLD, MERGE = 397, 471
EARLY, LAST = 300, 499
DEATH_LENGTHS = (8, 12)
BANDS = ("<8", "8-15", "16+")
SHORTER, LONGER = "head-on, shorter enemy head", "head-on, equal or longer enemy head"
SELF, ALLY, TRAPPED = "ran into itself (hitSelf)", "ran into a teammate's head", "trapped or other"
DELIBERATE = "deliberate (a feed or a split-out's parent, from its debug line)"
CAUSES = (DELIBERATE, SHORTER, LONGER, SELF, ALLY, TRAPPED)
CHILD = 2  # the champion's childLength when its params.hpp has none: a merged teammate this long or shorter is a child
SHORTFALL = 1e6  # the champion's P.shortfallPerTile when its params.hpp has none (deeper_split's runner-up, M81)

_games: subprocess.Popen | None = None  # the games being played now, killed with us (see on_term)
_online: subprocess.Popen | None = None  # the online replays being played again now (start_online), killed with us


# ------------------------------------------------------------------------------------------------- counting

# A debug log line "r 12 len 4 body 4 dir N score 20 space 40 target 915" as {key: value}.
def parse_log(text: str) -> dict[str, str]:
    words = text.split()
    return dict(zip(words[0::2], words[1::2]))


# Which branch of the bot's turn produced `action` (a tools/watch.py Turn action), given the turn's log fields and
# whether the turn started a head-on with an enemy head. A hunt plays its move as the best-scored move (main.cpp),
# so the log can't tell the two apart.
def branch(action: str, fields: dict[str, str] | None, head_on: bool) -> str:
    if action.startswith("split"):
        return "split"
    if action and set(action) <= STEPS:
        if len(action) > 1:
            return BRANCHES[1] if head_on else BRANCHES[2]
        if head_on:
            return RAM
        if fields is None or "dir" not in fields:
            return "single step, no log line"
        return BRANCHES[4] if fields["dir"] == action else BRANCHES[5]
    return action or "none"


# The facts rule counts need from one replay, for the team on `side`: its turns (round, id, action, pearls eaten),
# its log lines (round, id, text), the (round, id) turns in which it moved into an enemy head, the result, scan's
# moments by kind, each round's longest dragon (round, ours, theirs, at the round's end), its deaths (round, length,
# its longest living teammate's length, reason, for a head-on the other head's length and whether it was a
# teammate's, else None and False, and why it was deliberate, tools/watch.py deliberate_deaths, else None) and each
# team's king's meals (king_meals) and what its splits and sprint steps cost it (king_losses), and the per-event
# tallies below (rams, actions, portal hits, folds, fold-window crashes from `fold`, the champion's foldRound, farms,
# births, split children by their parent's rule), and `doom`, both teams' deaths by how they came about
# (tools/watch.py death_kinds, MISSES 64), the deliberate ones by their why.
def extract(path: Path, side: str, fold: int = FOLD, shortfall: float = SHORTFALL) -> dict:
    import replays
    import watch
    w = watch.watch(path, doom=True)
    stats = w.game.teams[side]
    other = "B" if side == "A" else "A"
    deliberate = watch.deliberate_deaths(w)
    logs = {(r, i): parse_log(text) for r, i, team, text in w.logs if team == side}
    return {"won": w.game.winner == side, "drew": w.game.winner is None,
            "turns": [[r, i, t.action, t.ate] for (r, i), t in sorted(w.turns.items()) if t.team == side],
            "logs": [[r, i, text] for r, i, team, text in w.logs if team == side],
            "head_ons": [[d["round"], d["hitBy"]] for d in w.game.deaths
                         if d.get("hitByTeam") == side and d["team"] != side],
            "rammed": sum(d.get("hitByTeam") == other and d["team"] == side for d in w.game.deaths),  # M72
            "pearls": stats.pearls_eaten, "head_ons_started": stats.head_ons_started,
            "moments": dict(Counter(kind for kind, _, _ in watch.scan(w, side))),
            "longest": [[s.round, s.longest[side], s.longest[other]] for s in w.game.snapshots],
            "deaths": [[d["round"], d["length"], d["longestTeammate"], d["reason"], *other_head(d, side),
                        deliberate.get((d["round"], d["id"]))] for d in w.game.deaths if d["team"] == side],
            "king": {"ours": king_meals(w.game, side), "theirs": king_meals(w.game, other)},
            "line": {"ours": king_losses(w.game, side), "theirs": king_losses(w.game, other)},
            "rams": ram_events(w, side, logs), "actions": action_deaths(w, side, logs, deliberate),
            "portal_hits": portal_hits(w, side, deliberate), "crashes": fold_crashes(w, side, logs, deliberate, fold),
            "folds": fold_outcomes(w, side, logs, deliberate), "farms": farm_outcomes(w, side, logs),
            "birth": birth_counts(w, side, logs), "children": split_children(w, side, logs, deliberate),
            "picks": target_picks(w, side, logs),  # M91
            # Both sides by moves alone: the opponents' copies log no debug line, so labelling only our feeds from it
            # read one code's feeds as our excess (M57: feed 50.2 v 0.0 a game, their feeds in the "never" rows).
            # Feeds too, as `feed?` (M63: unlabelled they read as `open never hitSelf`, 34.9 v 21.0 a game), and our
            # debug lines check that label: [our feeds, of them labelled, our labels]. Our `deeper` rows split by
            # how the scorer rated the dooming move (M67).
            "doom": watch.death_kinds(w.dooms, side, feed_from=fold),
            "shutters": shutters(w.dooms, side),
            "feed_check": feed_check(w, side, deliberate, fold),
            "deeper": deeper_split(w.dooms, side, logs, fold, w, shortfall),
            # M63: our deaths filed by our debug lines first (deliberate_deaths: a feed or capfeed, or a split-out's
            # parent), the rest by moves, and how the moves-only `capfeed?` label fits our capfeeds.
            "doom_ours": watch.death_kinds(w.dooms, side, deliberate, feed_from=fold)["ours"],
            # M71: each team's share of rounds at the ceiling (tools/replays.py cap_rounds_pct), where cap rules fire.
            "capfeed_check": capfeed_check(w, side, logs, fold),
            "cap": {"ours": replays.cap_rounds_pct(w.game, side), "theirs": replays.cap_rounds_pct(w.game, other)},
            "self_deaths": self_deaths(w, side, logs), "doomed_at_cap": doomed_at_cap(w, side, logs, deliberate),
            # M78: our pearl race ([n, ate] by gap, tools/replays.py RACE_BUCKETS); M75: each king line at r396.
            "race": {bucket: list(v) for bucket, v in stats.race.items()},
            # M80, M84: each team's portal crossings and in-place deaths, as its replays.py summary gives them.
            "crossing": {who: {"portals": replays.portal_crossings(w.game, team), "inPlace": replays.in_place_deaths(
                w.game, team)} for who, team in (("ours", side), ("theirs", other))},
            "king_fold": {"ours": replays.king_at_fold(w.game, side), "theirs": replays.king_at_fold(w.game, other)},
            # M86: each team's second-longest at the end of r499 and our second-longest's fold gate at GATE_ROUND.
            "two_kings": two_kings(w, side, logs)}


# M93: each side's teammate-trap dooms (tools/watch.py Doom.shutter) as {"ours"|"theirs": [n, shut by one teammate
# alone, whose shutting move was a split, whose turn before it was]}.
def shutters(dooms: list, side: str) -> dict[str, list[int]]:
    out = {}
    for who, mine in (("ours", True), ("theirs", False)):
        trapped = [d for d in dooms if d.trigger == "teammate" and (d.team == side) == mine]
        out[who] = [len(trapped), sum(d.shutter is not None for d in trapped),
                    sum(d.shut_split == "that turn" for d in trapped), sum(d.shut_split == "turn before" for d in trapped)]
    return out


GATE_ROUND = 450  # the round two_kings reads our second-longest's fold gate at (its debug line's `gate`)
NO_LINE = f"no line at r{GATE_ROUND}"


# One game's two-king facts (method M86: in 28 of 43 result-blind online round-limit losses our second-longest at r499
# exceeded the losing margin), None unless it went to the round limit: {"second": [ours, theirs], "gate": our
# second-longest's `gate` word at GATE_ROUND}. The gate is read from the debug line at GATE_ROUND of that dragon or,
# when it was born after it, of the dragon it split from, and so on up its line (a dragon going on in its child at a
# split: the line's dragon then); NO_LINE when none has one (no second dragon, a champion without the line, or a
# line born of a dragon that died before it).
def two_kings(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> dict | None:
    game = w.game
    if game.end_reason != "roundLimit" or game.snapshots[-1].round != LAST:
        return None
    last, other = game.snapshots[-1], "B" if side == "A" else "A"
    alive = sorted((d for d in game.dragons.values() if d.alive and d.team == side), key=lambda d: (-len(d.body), d.id))
    dragon, gate = alive[1] if len(alive) > 1 else None, NO_LINE
    while dragon is not None:
        if (fields := logs.get((GATE_ROUND, dragon.id))) is not None:
            gate = fields.get("gate", "no gate key")
            break
        dragon = game.dragons.get(dragon.parent) if dragon.parent is not None else None
    return {"second": [last.second[side], last.second[other]], "gate": gate}


# How our debug lines bear out the doom table's moves-only `capfeed?` label (tools/watch.py death_kind) in one game:
# [our deaths before foldRound whose dying turn's debug line gave a CAP_DEATH_RULES rule, how many of those it
# labelled, how many of ours it labelled in all].
CAP_DEATH_RULES = ("capfeed", "recycle")  # bot/main.cpp's deaths on purpose at the unit cap (tools/watch.py)


def capfeed_check(w, side: str, logs: dict[tuple[int, int], dict[str, str]], fold: int) -> list[int]:
    import watch
    capfeeds = {(d.round, d.id) for d in w.dooms if d.team == side and d.round < fold
                and (logs.get((d.round, d.id)) or {}).get("rule") in CAP_DEATH_RULES}
    labelled = {(d.round, d.id) for d in w.dooms if d.team == side and watch.death_kind(d, None, fold) == "capfeed?"}
    return [len(capfeeds), len(capfeeds & labelled), len(labelled)]


DEEPER_HOW = ("closed", "open unseen", "open known", "no line")  # deeper_split's classes, in doom_section's order
DEBUG_FIRST_TOP = 8  # kinds doom_section lists of our deaths filed by debug line first


# How our debug lines bear out the doom table's moves-only `feed?` label (tools/watch.py looks_fed) in one game:
# [our deaths whose debug line was a feed, how many of those it labelled, how many of ours it labelled in all].
def feed_check(w, side: str, deliberate: dict[tuple[int, int], str], fold: int) -> list[int]:
    import watch
    feeds = {k for k, why in deliberate.items() if why == "feed"}
    labelled = {(d.round, d.id) for d in w.dooms if d.team == side and watch.death_kind(d, None, fold) == "feed?"}
    return [len(feeds), len(feeds & labelled), len(labelled)]


# M67: the doom table's `open deeper` row (our own move shut us in) mixes two scorer failures that need different
# fixes. Each of our deaths the table (moves only) calls "<place> deeper", by our debug line at doomed_since - 1 (the
# turn whose move doomed it; its `dir` was the move played on all 852 such turns in review 1790576608098366000's 10
# kept replays): "closed" (`closed 1`: the scorer rated the move a closed pocket and took it anyway), "open unseen"
# (rated open only through an edge we never saw, `fedge unseen`), "open known" (open through seen edges, or no fill:
# `fedge known` or `nofill`), or "no line" (no debug line, or one from before those keys). {"open deeper closed": n}.
# M81: a "closed" row alone reads as a scorer misfire, where 19 of 26 in 2456ec2's kept replays had every other move
# penalised in a room our own dragons jammed. So each class also counts "<class>: runner-up penalised", the line's
# runner-up (`alt`) scoring `shortfall` (the champion's P.shortfallPerTile, one tile of pocket shortfall) or more below
# the move (`gap`), and "<class>: runner-up close" (within it). The gap is the whole score difference, so it can't tell
# a worse pocket from ram risk, a teammate's exit (riskPenalty, friendExitPenalty: 3e6 each, three tiles' shortfall)
# or a lethal move (about 1e12), nor, when close, an equally short pocket or a head-reach cut (headReachPenalty 2e5)
# from a plain choice: "penalised" is not "forced", and "close" is not "misjudged". With `w` (the watched replay),
# "<class>: heads <band>" (ROOM_HEADS): our other heads in its room at doomed_since (room_heads), a crowd apart from a
# lone dragon.
ROOM_HEADS = (("0", 0, 0), ("1-2", 1, 2), ("3-5", 3, 5), ("6+", 6, math.inf))


def deeper_split(dooms: list, side: str, logs: dict[tuple[int, int], dict[str, str]], fold: int, w=None,
                 shortfall: float = SHORTFALL) -> dict[str, int]:
    import watch
    found = Counter()
    neighbours: dict = {}
    for d in dooms:
        kind = watch.death_kind(d, None, fold)
        if d.team != side or not kind.endswith(" deeper") or d.doomed_since is None:
            continue
        line = logs.get((d.doomed_since - 1, d.id)) or {}
        how = ("no line" if "closed" not in line or "fedge" not in line else "closed" if line["closed"] == "1"
               else "open unseen" if line["fedge"] == "unseen" else "open known")
        row = f"{kind} {how}"
        found[row] += 1
        if (gap := number(line.get("gap"))) is not None:
            found[f"{row}: runner-up {'penalised' if gap >= shortfall else 'close'}"] += 1
        if w is not None and (heads := room_heads(w, d, neighbours)) is not None:
            found[f"{row}: heads {band_of(heads, ROOM_HEADS)}"] += 1
    return dict(found)


# How many of `d`'s team's other heads stood in its room at the start of round d.doomed_since (w.frames): the up to
# watch.ROOM_CAP tiles nearest its head over open edges (kelp and portals cut, bodies ignored), so a room smaller
# than that is the whole room, a pocket or a dead end, and an open field its neighbourhood. None when its head is
# unknown then (a child born that round). `neighbours` caches each tile's open steps for one board.
def room_heads(w, d, neighbours: dict[tuple[int, int], list[tuple[int, int]]]) -> int | None:
    import watch
    frame = w.frames.get(d.doomed_since)
    if frame is None or d.id not in frame.dragons:
        return None
    start = frame.dragons[d.id][1][0]
    room, todo = {start}, [start]
    while todo and len(room) < watch.ROOM_CAP:
        tile = todo.pop(0)
        if tile not in neighbours:
            neighbours[tile] = [w.board.step(tile, step) for step in "NESW" if w.board.side(tile, step)
                                not in w.board.edges]
        for n in neighbours[tile]:
            if n not in room and len(room) < watch.ROOM_CAP:
                room.add(n)
                todo.append(n)
    return sum(team == d.team and i != d.id and body[0] in room for i, (team, body) in frame.dragons.items())


# The pearls `team`'s king ate (tools/replays.py king_meals: its longest when the game ended, and the dragons it split
# from), each as [round, the merged teammate's length]. A merge is a teammate's drop from any death but a head-on,
# wherever it happened, that the king ate (the fold and the feeder die into kelp or their own body near it); length 0
# is a pearl from no merge (a spawn, an enemy's drop, or a teammate's head-on: a fight, not a fold). No distance
# condition: in E0117's 0378 the champion's king ate most merged drops from 3-5 steps away (its foldMergeRadius is
# 4), so one of 2 steps would have called most fold merges something else. A dead dragon of length L drops
# ceil(L/2) pearls.
def king_meals(game: replays.Game, team: str) -> list[list[int]]:
    import replays
    return [[meal["round"], drop["length"] if drop and drop["team"] == team and drop["reason"] != "hitHeadToHead"
             else 0] for meal, drop in replays.king_meals(game, team)]


# What `team`'s king line cost it besides its meals (tools/replays.py king_losses: the longest when the game ended and
# the dragons it split from), as [the length its starting dragon began with, [[round, a piece's length]] for the
# pieces it split off (a child, or its old head at a split-out or even split), [[round, what the parent kept]] for
# the splits that handed it on to a shorter child, [[round,
# length that round's sprint steps cost]]], or None when the team was eliminated. With king_meals, one length a
# pearl, it gives the line's length at the end of any round (line_length).
def king_losses(game: replays.Game, team: str) -> list | None:
    import replays
    losses = replays.king_losses(game, team)
    if losses is None:
        return None
    born, *parts = losses
    return [born, *([list(x) for x in part] for part in parts)]


# The length a list of king losses' [round, cost] events cost after round `after`, to the end of LAST.
def lost(events: list[list[int]], after: int = -1) -> int:
    return sum(cost for r, cost in events if after < r <= LAST)


# The king line's length at the end of round `at`, from extract's king meals and king losses of one team.
def line_length(meals: list[list[int]], losses: list, at: int) -> int:
    born, *parts = losses
    return born + sum(r <= at for r, _ in meals) - sum(cost for part in parts for r, cost in part if r <= at)


# The length of the head a head-on death of `side`'s dragon met (tools/replays.py's hitBy/movedInto fields) and
# whether it was a teammate's; (None, False) when it was no head-on or its other head is unknown.
def other_head(death: dict, side: str) -> tuple[int | None, bool]:
    if death["reason"] != "hitHeadToHead":
        return None, False
    if "hitByTeam" in death:
        return death["hitByLength"], death["hitByTeam"] == side
    if "movedIntoTeam" in death:
        return death["movedIntoLength"], death["movedIntoTeam"] == side
    return None, False


# Each of `side`'s rams in `w` (a move of ours into an enemy head: both die, docs/unsw/death.md), as [victim gain at
# death (the victim's length less ours, from the replay), our pearls eaten from the two drops, the pearls dropped, net
# (those pearls less our length), the ram turn's logged head edge or None, their pearls eaten from the two drops, the
# swing, the ram's steps, victim gain at the start (the victim's length less our length before the move)], from
# tools/watch.py rams, which defines the swing (M51). The edge comes from I352's debug line (`edge` on every turn) and
# is None before it. Method note M39: rams on heads 1-2 longer while outnumbered netted -2.4 a ram, measured by hand
# from the kept replays.
def ram_events(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> list[list]:
    import watch
    return [[ram.victim_length - ram.length, ram.gained, ram.dropped, ram.gained - ram.length,
             number(logs.get((ram.round, ram.rammer), {}).get("edge")), ram.theirs, ram.swing, ram.steps,
             ram.victim_length - ram.start] for ram in watch.rams(w, side)]


ACTIONS = ("step", "sprint", "portal crossing", "split", "split child, first 3 rounds", "farm entry")
# What ended a death on the dragon's own move, as action_deaths counts it: a ram (it moved into an enemy head; the ram
# tallies judge those by what they won), a deliberate death (tools/watch.py deliberate_deaths: a feed, a split-out's
# parent), or a failure, anything else. Only failures make an action's failure rate.
OUTCOMES = ("failures", "rams", "deliberate")


# Whether a move of `steps` ("N", "NE", ...) from `head` goes through a portal on `board` (mapgen's Board: its edges,
# side and step, portals included). A step into kelp ends it: the dragon dies there. A dragon that died on its move
# took only its first `upto` steps (its death record's `step`), the last of them the fatal one.
def crosses_portal(board, head: tuple[int, int], steps: str, upto: int | None = None) -> bool:
    import watch
    at = head
    for step in steps[:upto]:
        kind = board.edges.get(board.side(at, step))
        if kind is not None and kind != watch.KELP:
            return True
        if (at := board.step(at, step)) is None:
            return False
    return False


# Each of `side`'s folding dragons in `w` (one whose debug line named a king to walk to, E0127's `fold` set, on some
# turn; method note M40), as [its length on its first folding turn, its outcome, FOLD_OUTCOMES]. A fold ends when the
# dragon dies beside its king to feed it (feedDirection, a turn logged `rule feed`), so it merged when its death was a
# feed (`deliberate`, watch.deliberate_deaths): split by who ate the drop, the king its last folding line named or
# another teammate or nobody. A split-out's parent (its other deliberate death) is counted apart, not as a crash.
# Otherwise it was killed by an enemy (a head-on with the other team), crashed on its own move (hitOtherBody, hitWall,
# hitSelf: the failure mode to watch), died some other way (a friendly head-on, no valid move) or was alive when the
# game ended. The review of 1790439654199275000 counted 203 folding
# dragons in its 3844-tile game, 86 of them merged, by hand.
FOLD_OUTCOMES = ("merged, the king ate", "merged, another ate", "split out (deliberate)", "killed by an enemy",
                 "crashed on its own move", "died otherwise", "alive")
CRASHES = ("hitOtherBody", "hitWall", "hitSelf")


def fold_outcomes(w, side: str, logs: dict[tuple[int, int], dict[str, str]],
                  deliberate: dict[tuple[int, int], str]) -> list[list]:
    first: dict[int, float | None] = {}
    king: dict[int, int] = {}
    for (r, i), fields in sorted(logs.items()):
        target = number(fields.get("fold"))
        if target is None or target == -1 or not math.isfinite(target):
            continue
        first.setdefault(i, number(fields.get("len")))
        king[i] = int(target)
    deaths = {d["id"]: (k, d) for k, d in enumerate(w.game.deaths) if d["team"] == side}
    eaters: dict[int, set[int]] = {}
    for meal in w.game.meals:
        if meal["death"] is not None:
            eaters.setdefault(meal["death"], set()).add(meal["id"])

    def outcome(i: int) -> str:
        if i not in deaths:
            return "alive"
        k, death = deaths[i]
        why = deliberate.get((death["round"], i))
        if why == "feed":
            return "merged, the king ate" if king[i] in eaters.get(k, ()) else "merged, another ate"
        if why is not None:
            return "split out (deliberate)"
        other = death.get("hitByTeam") or death.get("movedIntoTeam")
        if death["reason"] == "hitHeadToHead" and other is not None and other != side:
            return "killed by an enemy"
        return "crashed on its own move" if death["reason"] in CRASHES else "died otherwise"

    return [[length, outcome(i)] for i, length in first.items()]


# What a dragon lacks at birth (method note M43: split children are nearly all our dragons, and a child starts as a
# blank process; by hand, 660 of 663 body < len turns of our 16+ dragons in r397-498 were dragons born long). Logged
# turns are grouped by the dragon's age (rounds since the replay's `born`; a starting dragon's from round 0) and by its
# birth length (its `len` on its first logged turn), and within each group counted: turns, turns whose line says
# body < len (the `body` and `len` keys), turns of dragons 12+ long and split-outs among them (`rule splitout`), and the
# values of I370's `own` (full, born, reset) and `sym` (known, none) keys once I352 logs them. Split-outs per 100 turns
# of 12+ dragons are grouped by `own` too.
# M91: what a pearl target's pick leads to. A pick is a turn whose debug line has a pearl target (`target` a tile,
# `tk` one of PEARL_KINDS: Targets::consider's kinds) that the dragon's line of the round before didn't have; its
# episode runs while each next round's line keeps that tile, and is read as its kind at the pick: its turns, whether the
# picker ate a pearl on that tile within EAT_WINDOW rounds of the pick (whenever), and how the episode ended (PICK_ENDS:
# the picker ate it; another dragon did, by the end of the round after; its next line names another pearl tile, a
# switch, which returned when a line within RETURN_TURNS turns after names the tile again; a target of another kind or
# none; no next line, the dragon died or the game ended).
PEARL_KINDS = ("visible", "drop", "spawn", "remembered", "fast", "team", "probable")
EAT_WINDOW = 30
RETURN_TURNS = 6
PICK_ENDS = ("eaten", "gone", "switch", "drop", "end")


# `side`'s picks in `w` (see PEARL_KINDS), each [kind, turns, eaten within EAT_WINDOW (0/1), end, returned (0/1 after a
# switch, else None)], from its debug lines `logs` and the replay's eats (tools/watch.py Watched.eats).
def target_picks(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> list[list]:
    width = w.game.map.width
    ate: dict[tuple[int, int], list[int]] = {}
    gone: dict[int, list[int]] = {}
    for r, i, (x, y) in w.eats:
        ate.setdefault((i, y * width + x), []).append(r)
        gone.setdefault(y * width + x, []).append(r)
    target: dict[int, dict[int, tuple[str, int] | None]] = {}  # dragon -> round -> (kind, tile) or None
    for (r, i), fields in logs.items():
        tile = number(fields.get("target"))
        pearl = fields.get("tk") in PEARL_KINDS and tile is not None and tile >= 0
        target.setdefault(i, {})[r] = (fields["tk"], int(tile)) if pearl else None
    picks = []
    for i, rounds in target.items():
        for r in sorted(rounds):
            if rounds[r] is None or (rounds.get(r - 1) or (None, None))[1] == rounds[r][1]:
                continue
            kind, tile = rounds[r]
            last = r
            while (rounds.get(last + 1) or (None, None))[1] == tile:
                last += 1
            mine = ate.get((i, tile), [])
            after = rounds.get(last + 1, "no line")
            end = ("eaten" if any(r <= x <= last for x in mine) else
                   "gone" if any(r <= x <= last + 1 for x in gone.get(tile, [])) else
                   "end" if after == "no line" else "drop" if after is None else "switch")
            back = (int(any((rounds.get(q) or (None, None))[1] == tile for q in range(last + 2, last + 2 + RETURN_TURNS)))
                    if end == "switch" else None)
            picks.append([kind, last - r + 1, int(any(r <= x <= r + EAT_WINDOW for x in mine)), end, back])
    return picks


AGE_BANDS = (("0-5", 0, 5), ("6-20", 6, 20), ("21+", 21, math.inf))
BIRTH_BANDS = (("2", 0, 2), ("3-7", 3, 7), ("8+", 8, math.inf))
BIRTH_KEYS = ("own", "sym")  # I370's word keys, carried by I352: tallied by value when a line has them
LONG = 12


def band_of(value: float, bands: tuple) -> str:
    return next(name for name, low, high in bands if low <= value <= high)


# `side`'s logged turns in `w` by age and birth length (see AGE_BANDS), as counts {"<group>|<label>|<measure>": n}:
# group "age", "birth" or "own" (the line's own value, for the split-out rate by own); measures "turns", "untracked"
# (body < len), "long" (turns 12+ long), "splitouts" (of those, `rule splitout`) and "<key> <value>" for BIRTH_KEYS.
def birth_counts(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> dict[str, int]:
    born = {i: max(d.born, 0) for i, d in w.game.dragons.items()}
    first: dict[int, float] = {}
    counts: Counter = Counter()
    for (r, i), fields in sorted(logs.items()):
        size, body = number(fields.get("len")), number(fields.get("body"))
        if size is None or i not in born:
            continue
        first.setdefault(i, size)
        groups = [("age", band_of(max(r - born[i], 0), AGE_BANDS)), ("birth", band_of(first[i], BIRTH_BANDS))]
        if "own" in fields:
            groups.append(("own", fields["own"]))
        for group, label in groups:
            key = f"{group}|{label}|"
            counts[key + "turns"] += 1
            counts[key + "untracked"] += body is not None and body < size
            if size >= LONG:
                counts[key + "long"] += 1
                counts[key + "splitouts"] += fields.get("rule") == "splitout"
            for name in BIRTH_KEYS:
                if name in fields:
                    counts[f"{key}{name} {fields[name]}"] += 1
    return dict(counts)


# Each of `side`'s farm entries in `w` (a turn whose debug line says `farm 1`, E0127: a step into a closed pocket that
# farmEntryOk accepted; method note M40), as [round, length, outcome, folding]: what the dragon did first within
# FARM_WINDOW rounds, "split-out" (a turn whose rule was splitout: doomed there, it split its tail out) or "died",
# else "alive", and whether the line named a king to walk to (`fold` set: M76, I639's farm entries by folding
# dragons, which a fold's walk makes).
FARM_OUTCOMES = ("split-out", "died", "alive")
FARM_WINDOW = 30


def farm_outcomes(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> list[list]:
    died = {d["id"]: d["round"] for d in w.game.deaths if d["team"] == side}
    splitouts: dict[int, list[int]] = {}
    for (r, i), fields in logs.items():
        if fields.get("rule") == "splitout":
            splitouts.setdefault(i, []).append(r)
    entries = []
    for (r, i), fields in sorted(logs.items()):
        if fields.get("farm") != "1":
            continue
        out = min((x for x in splitouts.get(i, []) if r <= x <= r + FARM_WINDOW), default=None)
        dead = died.get(i) if r <= died.get(i, -1) <= r + FARM_WINDOW else None
        outcome = ("split-out" if out is not None and (dead is None or out <= dead) else
                   "died" if dead is not None else "alive")
        entries.append([r, number(fields.get("len")), outcome, number(fields.get("fold")) not in (None, -1)])
    return entries


# M76 (I719): each death of `side`'s longest dragon (no living teammate longer) into its own body, as [length, the
# dying turn's `cap` flag: "1" at the unit cap, "0", or "no line"].
def self_deaths(w, side: str, logs: dict[tuple[int, int], dict[str, str]]) -> list[list]:
    return [[d["length"], logs.get((d["round"], d["id"]), {}).get("cap", "no line")] for d in w.game.deaths
            if d["team"] == side and d["reason"] == "hitSelf" and d["length"] >= d["longestTeammate"]]


# M76 (I345, I842, I844) and M83: each of `side`'s deaths on its own move (no head it was hit by) whose dying turn's
# debug line says `cap 1` (no unit slot to split into), as [length, lead, the flag's lead, whether a turn of the doom
# said `cap 0`, whether one split out (rule splitout), why it was deliberate or None]. `lead` is how many rounds
# before its death tools/watch.py doom's exact reach first found it doomed for good (Doom.lead: 0 doomed only at the
# dying turn, None never), the flag's lead the same for the bot's own `doom 1` flag (every move lethal or a bad
# trade): its logged turns in a row back from the death that say it, less one, None when the dying turn doesn't. The
# doom's turns are its logged turns from doomed_since to the death: a `cap 0` among them means a slot was free while
# it was doomed, so a split-out could have saved its tail (I845, I345), which the deaths doomed 1+ rounds before ask.
# Deliberate deaths (watch.deliberate_deaths: a feed, a split-out's parent) are the same rows with their why, counted
# apart. M83: read by the flag, champion b85dad1's 394 such deaths of 8+ had "doomed 1+ turns before: 0", while exact
# reach on its 9 kept replays found 164 of 206 doomed 1-6 rounds earlier, a slot free in 78.
def doomed_at_cap(w, side: str, logs: dict[tuple[int, int], dict[str, str]],
                  deliberate: dict[tuple[int, int], str]) -> list[list]:
    turns: dict[int, list[tuple[int, dict[str, str]]]] = {}
    for (r, i), fields in sorted(logs.items()):
        turns.setdefault(i, []).append((r, fields))
    out = []
    for d, doom in zip(w.game.deaths, w.dooms):
        if d["team"] != side or "hitBy" in d or logs.get((d["round"], d["id"]), {}).get("cap") != "1":
            continue
        mine = [(r, fields) for r, fields in turns.get(d["id"], []) if r <= d["round"]]
        flag = -1
        for r, fields in reversed(mine):
            if fields.get("doom") != "1":
                break
            flag += 1
        inside = [fields for r, fields in mine if doom.doomed_since is not None and r >= doom.doomed_since]
        out.append([d["length"], doom.lead, flag if flag >= 0 else None,
                    any(fields.get("cap") == "0" for fields in inside),
                    any(fields.get("rule") == "splitout" for fields in inside), deliberate.get((d["round"], d["id"]))])
    return out


# The outcome (OUTCOMES) of `side`'s death `death` on its own move; `deliberate` is watch.deliberate_deaths' map.
def outcome(death: dict, side: str, deliberate: dict[tuple[int, int], str]) -> str:
    if death["reason"] == "hitHeadToHead" and death.get("movedIntoTeam") not in (None, side):
        return "rams"
    return "deliberate" if (death["round"], death["id"]) in deliberate else "failures"


# `side`'s turns by the action just taken, with the deaths on the dragon's own move (not a head it was hit by) split
# by outcome (method note M30: 7003's portal crossings died on landing 10.8% of the time against 1.0% for other steps,
# which no count showed; in one rule-count game, 15 of 17 step deaths and all 10 sprint deaths were rams), as
# {action: [turns, failures, rams, deliberate]} over ACTIONS. A turn counts under each action that fits it: a move of
# one step or a sprint of several, and a portal crossing when a step it took went through a portal; a split; any turn
# of a dragon in its first 3 rounds, its split's round included; a farm entry when the debug line says `farm 1` (E0127).
def action_deaths(w, side: str, logs: dict[tuple[int, int], dict[str, str]],
                  deliberate: dict[tuple[int, int], str]) -> dict[str, list[int]]:
    died = {(d["round"], d["id"]): d for d in w.game.deaths
            if d["team"] == side and "hitBy" not in d}
    born = {i: d.born for i, d in w.game.dragons.items()}
    counts = {action: [0] * (1 + len(OUTCOMES)) for action in ACTIONS}
    for (r, i), turn in w.turns.items():
        if turn.team != side:
            continue
        death = died.get((r, i))
        kinds = []
        if turn.action and set(turn.action) <= STEPS:
            kinds.append("step" if len(turn.action) == 1 else "sprint")
            if crossed(w, r, i, turn.action, death):
                kinds.append("portal crossing")
        elif turn.action.startswith("split"):
            kinds.append("split")
        if born.get(i, -1) >= 0 and 0 <= r - born[i] < 3:  # a child moves in the round it splits off
            kinds.append("split child, first 3 rounds")
        if logs.get((r, i), {}).get("farm") == "1":
            kinds.append("farm entry")
        for kind in kinds:
            counts[kind][0] += 1
            if death:
                counts[kind][1 + OUTCOMES.index(outcome(death, side, deliberate))] += 1
    return counts


# Split children by the rule their parent's debug line gave on the split turn (method note M50: children failed on
# their own move on 1.63% of their first-3-round turns against 0.53% for all steps, 259 of 15,891 in champion
# 600cd4e's 24 rule-count games, and 12+ dragons split out 5x as often in their first 5 rounds, with no row saying
# which kind of split made them). SPLIT_RULES are bot/main.cpp's rules on a split turn: an ordinary split, a split-out
# and a shed (split.hpp shouldSplit, or splitOutLength's split-out while folding) and shedLength's threat shed.
SPLIT_RULES = ("split", "splitout", "shed", "threatshed")
UNKNOWN_RULE = "rule unknown"  # no debug line on the split turn (a champion without one, or a line lost)
YOUNG = 3  # a child's first rounds for its failures, as action_deaths' "split child, first 3 rounds" counts them
RESPLIT = 5  # the first rounds in which a child splitting out itself counts as a repeat split-out


# Each of `side`'s split children in `w` (the replay's split events), as [its parent's logged `rule` on the split turn
# (UNKNOWN_RULE without a line or a rule key), its turns in its first YOUNG rounds (its split's round included), the
# failures on its own move among them (outcome's: not a ram, not deliberate), whether it split out itself (`rule
# splitout`) in its first RESPLIT rounds, whether it died by a feed (watch.deliberate_deaths), and whether it was
# merged into the king: a feed whose drop its team's king (tools/replays.py king_line: the longest at the end and the
# dragons it split from) ate a pearl of. The turns and failures add up to action_deaths' split-child row. In review
# 1790492425732703000's 10 kept replays 416 of 2,319 children died by a feed and the king ate from 53 of those drops.
def split_children(w, side: str, logs: dict[tuple[int, int], dict[str, str]],
                   deliberate: dict[tuple[int, int], str]) -> list[list]:
    import replays
    turns: dict[int, list[int]] = {}
    for (r, i), turn in w.turns.items():
        if turn.team == side:
            turns.setdefault(i, []).append(r)
    died = {(d["round"], d["id"]): d for d in w.game.deaths if d["team"] == side and "hitBy" not in d}
    drops = {d["id"]: (k, d) for k, d in enumerate(w.game.deaths) if d["team"] == side}
    king_ate = {meal["death"] for meal, _ in replays.king_meals(w.game, side)}
    children = []
    for split in w.game.teams[side].splits:
        born, i = split["round"], split["childId"]
        young = [r for r in turns.get(i, []) if 0 <= r - born < YOUNG]
        failures = sum(outcome(died[r, i], side, deliberate) == "failures" for r in young if (r, i) in died)
        resplit = any(logs.get((r, i), {}).get("rule") == "splitout" for r in turns.get(i, [])
                      if 0 <= r - born < RESPLIT)
        k, death = drops.get(i, (None, None))
        fed = death is not None and deliberate.get((death["round"], i)) == "feed"
        children.append([logs.get((born, split["parentId"]), {}).get("rule", UNKNOWN_RULE), len(young), failures,
                         resplit, fed, fed and k in king_ate])
    return children


# Whether dragon `i`'s move `action` in round `r` of `w` went through a portal, up to its fatal step when `death` (its
# death on that move, or None) ended it. A dragon split off earlier this round isn't in the round's opening frame: its
# head is unknown, so it never counts. A death with no step (noValidAction) moved nowhere, so its path is never scanned.
def crossed(w, r: int, i: int, action: str, death: dict | None) -> bool:
    start = w.frames[r].dragons.get(i) if r in w.frames else None
    return bool(start) and (not death or bool(death.get("step"))) and crosses_portal(
        w.board, start[1][0], action, death and death["step"])


# What a portal crossing's death on the dragon's own move hit (method note M49: the review of 1790489145383106000's
# scratch walker found 41 of 86 landing deaths in its 9 kept replays were head-ons with our own teammate, which the
# failure share alone doesn't say): an enemy head (a ram, as action_deaths counts it), a deliberate death (feed or
# split-out parent, whatever it hit), else a teammate's head, a teammate's body, an enemy's body, its own body
# (hitSelf), kelp (hitWall), or other (no valid move, or a body the replay walk can't place). They add up to the portal
# crossing's deaths in all.
PORTAL_HITS = ("enemy head (a ram)", "deliberate", "teammate's head", "teammate's body", "enemy body",
               "its own body (hitSelf)", "kelp (hitWall)", "other")


# The team of the dragon whose body the hitOtherBody death `death` (index `index` in w.game.deaths) ran into: the tile
# its fatal step led to (through a portal too) in the living bodies just before it (watch's before_death); None when
# that isn't known.
def body_owner(w, index: int, death: dict) -> str | None:
    living = w.before_death.get(index)
    if not living or not death.get("direction"):
        return None
    target = w.board.step(tuple(death["head"]), death["direction"][0].upper())
    return next((team for i, (team, body) in living.items() if i != death["id"] and target in body), None)


# What `side`'s death `death` (index `index`) on its own move hit, PORTAL_HITS' label.
def hit_label(w, index: int, death: dict, side: str, deliberate: dict[tuple[int, int], str]) -> str:
    kind = outcome(death, side, deliberate)
    if kind != "failures":
        return PORTAL_HITS[0] if kind == "rams" else PORTAL_HITS[1]
    reason = death["reason"]
    if reason == "hitHeadToHead" and death.get("movedIntoTeam") == side:
        return PORTAL_HITS[2]
    if reason == "hitOtherBody" and (owner := body_owner(w, index, death)) is not None:
        return PORTAL_HITS[3] if owner == side else PORTAL_HITS[4]
    return {"hitSelf": PORTAL_HITS[5], "hitWall": PORTAL_HITS[6]}.get(reason, PORTAL_HITS[7])


# `side`'s deaths on its own move in `w` whose move went through a portal (action_deaths' portal crossings), counted
# by what they hit, {PORTAL_HITS label: n}.
def portal_hits(w, side: str, deliberate: dict[tuple[int, int], str]) -> dict[str, int]:
    counts: Counter = Counter()
    for index, death in enumerate(w.game.deaths):
        turn = w.turns.get((death["round"], death["id"]))
        if death["team"] != side or "hitBy" in death or not turn or not turn.action or not set(turn.action) <= STEPS:
            continue
        if crossed(w, death["round"], death["id"], turn.action, death):
            counts[hit_label(w, index, death, side, deliberate)] += 1
    return dict(counts)


# Fold-window crashes (method note M49): `side`'s deaths in rounds from `fold` on that ran into a body, kelp or itself
# on its own move (CRASHES; not deliberate: a feed dies into kelp too), by path distance from the dragon's head to its
# team's longest living dragon's head just before it (the king it folds to, measured over the terrain: every step,
# portals included, kelp blocking, bodies ignored). The Trauma study of 2026-09-27 found 58 of our dragons dying in
# the fold window a lost Trauma game, mostly hitWall and hitSelf in the centre maze, the rest of our length a median
# 42 path steps from the king: a crash beside the king is a feed gone wrong, one far away a walk that never arrived.
KING_BANDS = (("<= 5", 0, 5), ("6-20", 6, 20), ("21+", 21, math.inf))
NO_PATH, THE_KING = "no path to the king (walled off by kelp)", "the king itself (our longest)"


def king_band(steps: int) -> str:
    return f"{band_of(steps, KING_BANDS)} path steps from the king"


# The fewest steps from `start` to `goal` on `board` (mapgen's Board: kelp blocks, portals carry), or None when none
# gets there. `neighbours` caches each tile's steps for one board.
def path_steps(board, start: tuple[int, int], goal: tuple[int, int],
               neighbours: dict[tuple[int, int], list[tuple[int, int]]]) -> int | None:
    if start == goal:
        return 0
    seen, frontier, steps = {start}, [start], 0
    while frontier:
        steps += 1
        following = []
        for tile in frontier:
            if tile not in neighbours:
                neighbours[tile] = [n for d in "NESW" if (n := board.step(tile, d)) is not None]
            for n in neighbours[tile]:
                if n == goal:
                    return steps
                if n not in seen:
                    seen.add(n)
                    following.append(n)
        frontier = following
    return None


# Each of `side`'s fold-window crashes in `w` (see KING_BANDS), as [round, label (king_band's, NO_PATH, THE_KING, or
# "not known" without before_death), path steps or None, whether its debug line ever named a king (`fold` set)].
def fold_crashes(w, side: str, logs: dict[tuple[int, int], dict[str, str]],
                 deliberate: dict[tuple[int, int], str], fold: int = FOLD) -> list[list]:
    folding = {i for (_, i), fields in logs.items()
               if (target := number(fields.get("fold"))) is not None and target != -1 and math.isfinite(target)}
    neighbours: dict = {}
    crashes = []
    for index, death in enumerate(w.game.deaths):
        if (death["team"] != side or death["round"] < fold or death["reason"] not in CRASHES
                or (death["round"], death["id"]) in deliberate):
            continue
        living = w.before_death.get(index)
        ours = [(len(body), -i, i, body[0]) for i, (team, body) in (living or {}).items() if team == side]
        steps, label = None, "not known"
        if ours:
            *_, king, head = max(ours)
            if king == death["id"]:
                label = THE_KING
            else:
                steps = path_steps(w.board, tuple(death["head"]), head, neighbours)
                label = NO_PATH if steps is None else king_band(steps)
        crashes.append([death["round"], label, steps, death["id"] in folding])
    return crashes


# How census tallies `value` of an extra debug-line key `key` (FLAG_KEYS, ID_KEYS, NUMBER_BANDS or the word itself).
def log_value(key: str, value: str) -> str:
    amount = number(value)
    if key in FLAG_KEYS:
        return FLAG_ON if amount == 1 else "other"
    if key in ID_KEYS:
        return "unset" if amount == -1 else ID_SET
    if amount is None:
        return value
    if not math.isfinite(amount):
        return NUMBER_BANDS[-1]
    return NUMBER_BANDS[0] if amount <= -1.5 else NUMBER_BANDS[4] if amount >= 1.5 else str(round(amount))


def number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


# The census of `games` (extract's dicts) as {measure: value}, in report order: per-game means, and shares of the
# team's turns (or of its logged turns) in percent. Each extra log label lists its `top` commonest values (all, None).
def census(games: list[dict], top: int | None = 12) -> dict[str, float]:
    n = len(games)
    if not n:
        return {}
    turns = sum(len(g["turns"]) for g in games)
    out = {"games": n, "won %": 100 * sum(g["won"] for g in games) / n,
           "drawn %": 100 * sum(g["drew"] for g in games) / n, "dragon-turns a game": turns / n,
           "pearls eaten a game": sum(g["pearls"] for g in games) / n,
           "head-ons started a game": sum(g["head_ons_started"] for g in games) / n}
    if capped := [g["cap"] for g in games if "cap" in g]:  # M71; facts from before it have none
        import replays
        for who in ("ours", "theirs"):
            out[f"% of rounds within {replays.CAP_MARGIN} of the unit limit ({who})"] = \
                sum(c[who] for c in capped) / len(capped)
    branches, children, extra = Counter(), Counter(), {}
    targets = spaces = logged = 0
    scores = Counter()
    for g in games:
        lines = {(r, i): parse_log(text) for r, i, text in g["logs"]}
        rams = {(r, i) for r, i in g["head_ons"]}
        for r, i, action, _ in g["turns"]:
            branches[branch(action, lines.get((r, i)), (r, i) in rams)] += 1
            if action.startswith("split ") and action.split()[1].isdigit():
                children[int(action.split()[1])] += 1
        for fields in lines.values():
            logged += 1
            targets += fields.get("target") == "-1"
            size, space = number(fields.get("len")), number(fields.get("space"))
            spaces += size is not None and space is not None and space < size
            score = number(fields.get("score"))
            if score is not None:
                scores[DOOMED if score <= -1e9 else PENALISED if score <= -1e5 else "above -1e5"] += 1
            for key, value in fields.items():
                if key not in KNOWN_KEYS:
                    extra.setdefault(key, Counter())[log_value(key, value)] += 1
    for name in dict.fromkeys([*BRANCHES, *branches]):
        out[f"% of turns: {name}"] = 100 * branches[name] / max(1, turns)
    for low, high in CHILD_BUCKETS:
        label = f"{low}-{high}" if high < 999 else f"{low}+"
        out[f"splits a game, child length {label}"] = sum(children[k] for k in range(low, high + 1)) / n
    if logged:
        out["% of logged turns: no pearl target (exploring)"] = 100 * targets / logged
        out["% of logged turns: best move's space below the dragon's length"] = 100 * spaces / logged
        for band in (DOOMED, PENALISED):
            out[f"% of logged turns: best move scored {band}"] = 100 * scores[band] / logged
        for key, values in sorted(extra.items()):
            if key in FLAG_KEYS or key in ID_KEYS:  # one row, there even when never set
                shown = [FLAG_ON if key in FLAG_KEYS else ID_SET]
            elif all(value in NUMBER_BANDS for value in values):
                shown = [band for band in NUMBER_BANDS if band in values]
            else:
                shown = [value for value, _ in values.most_common(top)]
            for value in shown:
                out[f"% of logged turns: {key} {value}"] = 100 * values[value] / logged
    moments = Counter()
    for g in games:
        moments.update(g["moments"])
    for kind, count in sorted(moments.items()):
        out[f"scan moments a game: {kind}"] = count / n
    return out


# Rams by victim gain (the victim's length less ours at the ram's start, computed from the replay's death records; at
# death for facts from before M51), by the ram turn's logged head edge, clipped to EDGE_CLIP each way, and by the ram's
# steps (M51).
VGAIN_BANDS = (("on shorter heads", -math.inf, -1), ("at a tie", 0, 0), ("on heads 1-2 longer", 1, 2),
               ("on heads 3+ longer", 3, math.inf))
EDGE_CLIP = 2
STEP_BANDS = (("1 step", 1, 1), ("2 steps", 2, 2), ("3 steps", 3, 3), ("4+ steps", 4, math.inf))


# A ram_events row's victim gain: at the ram's start, or at death in facts from before M51, which lack it.
def gain(ram: list) -> float:
    return ram[8] if len(ram) > 8 else ram[0]


# A logged head edge's band; "unknown" for one that isn't a finite number (a "nan" or "inf" in the line), so one odd
# line can't lose the tallies of all the games.
def edge_label(edge: float) -> str:
    if not math.isfinite(edge):
        return "unknown"
    clipped = max(-EDGE_CLIP, min(EDGE_CLIP, int(edge)))
    return f"<= {clipped}" if clipped == -EDGE_CLIP else f">= {clipped}" if clipped == EDGE_CLIP else str(clipped)


def edge_order(label: str) -> float:
    return math.inf if label == "unknown" else int(label.split()[-1])


# The fixed per-event tallies over every one of `games` (extract's rams and actions; method notes M39 and M30), as
# {measure: value} in report order: for rams by victim gain, then by logged head edge when the debug line has it, then
# by steps (M51), how many (in all and a game), our share of the pearls their two drops held, the net a ram (our
# pearls from them less our length) and the share netting below 0, and (M51) their share, the swing a ram (ram_events)
# and the share swinging below 0; then how many and the swing a ram by steps and victim gain together; then each
# action's turns a game, the share of them that ended in a failure (a death on the dragon's own move that was neither a
# ram nor deliberate), and its deaths in all by outcome, which add up to every death on its own move. Counted before
# the replays are deleted, so no pick of kept replays biases them.
def events(games: list[dict], fold: int = FOLD, merge: int = MERGE) -> dict[str, float]:
    n = len(games)
    if not n:
        return {}
    out: dict[str, float] = {"games": n}
    rams = [ram for g in games for ram in g.get("rams", [])]
    bands = [(label, [r for r in rams if low <= gain(r) <= high]) for label, low, high in VGAIN_BANDS]
    edges = sorted({edge_label(r[4]) for r in rams if r[4] is not None}, key=edge_order)
    bands += [(f"at logged head edge {e}", [r for r in rams if r[4] is not None and edge_label(r[4]) == e])
              for e in edges]
    crossed = []  # M51: by the ram's steps, and within each by victim gain (only how many and the swing)
    for label, low, high in STEP_BANDS if any(len(r) > 5 for r in rams) else ():
        picked = [r for r in rams if len(r) > 5 and r[7] is not None and low <= r[7] <= high]
        bands.append((f"of {label}", picked))
        crossed += [(f"of {label} {name}", [r for r in picked if lo <= gain(r) <= hi]) for name, lo, hi in VGAIN_BANDS]
    for label, picked in bands:
        out[f"rams {label}: in all"] = len(picked)
        out[f"rams {label}: a game"] = len(picked) / n
        if picked:
            out[f"rams {label}: our share of the drops %"] = 100 * sum(r[1] for r in picked) / max(1, sum(r[2] for r in picked))
            out[f"rams {label}: net a ram"] = statistics.mean(r[3] for r in picked)
            out[f"rams {label}: % netting below 0"] = 100 * sum(r[3] < 0 for r in picked) / len(picked)
        if swung := [r for r in picked if len(r) > 5]:
            out[f"rams {label}: their share of the drops %"] = (
                100 * sum(r[5] for r in swung) / max(1, sum(r[2] for r in swung)))
            out[f"rams {label}: swing a ram"] = statistics.mean(r[6] for r in swung)
            out[f"rams {label}: % swinging below 0"] = 100 * sum(r[6] < 0 for r in swung) / len(swung)
    for label, picked in crossed:
        out[f"rams {label}: in all"] = len(picked)
        if picked:
            out[f"rams {label}: swing a ram"] = statistics.mean(r[6] for r in picked)
    for action in ACTIONS:
        rows = [g["actions"][action] for g in games if "actions" in g]
        turns = sum(row[0] for row in rows)
        out[f"{action}: turns a game"] = turns / n
        if turns:
            deaths = {name: sum(row[1 + k] if len(row) > 1 + k else 0 for row in rows)
                      for k, name in enumerate(OUTCOMES)}
            out[f"{action}: % ending in a failure on its own move"] = 100 * deaths["failures"] / turns
            for name, count in deaths.items():
                out[f"{action}: deaths in all, {name}"] = count
        if action == "portal crossing" and any("portal_hits" in g for g in games):  # M49: what those deaths hit
            hits = Counter()
            for g in games:
                hits.update(g.get("portal_hits", {}))
            for name in PORTAL_HITS:
                out[f"portal crossing: deaths in all, by what it hit: {name}"] = hits[name]
    if any("children" in g for g in games):  # M50: split children by the rule that made them
        children = [c for g in games for c in g.get("children", [])]
        others = sorted({c[0] for c in children} - {*SPLIT_RULES, UNKNOWN_RULE})
        for rule in (*SPLIT_RULES, *others, UNKNOWN_RULE):
            group = [c for c in children if c[0] == rule]
            label = "split children, " + (rule if rule == UNKNOWN_RULE else f"parent's rule {rule}")
            turns = sum(c[1] for c in group)
            out[f"{label}: made"] = len(group)
            out[f"{label}: turns in their first {YOUNG} rounds"] = turns
            out[f"{label}: failures on own move in those turns"] = sum(c[2] for c in group)
            if turns:
                out[f"{label}: % of those turns ending in a failure"] = 100 * sum(c[2] for c in group) / turns
            if group:
                out[f"{label}: % splitting out again in their first {RESPLIT} rounds"] = (
                    100 * sum(c[3] for c in group) / len(group))
                out[f"{label}: % died by a feed"] = 100 * sum(c[4] for c in group) / len(group)
                out[f"{label}: % merged into the king (fed, the king ate its drop)"] = (
                    100 * sum(c[5] for c in group) / len(group))
    folds = [f for g in games for f in g.get("folds", [])]
    if folds:
        out["folding dragons: in all"] = len(folds)
        out["folding dragons: % merged (fed to a teammate)"] = (
            100 * sum(outcome.startswith("merged") for _, outcome in folds) / len(folds))
        for name in FOLD_OUTCOMES:
            group = [length for length, outcome in folds if outcome == name]
            out[f"folding dragons: % {name}"] = 100 * len(group) / len(folds)
            if lengths := [length for length in group if length is not None]:
                out[f"folding dragons {name}: mean length at the fold's start"] = statistics.mean(lengths)
    crashes = [c for g in games for c in g.get("crashes", []) if c[0] >= fold]
    if any("crashes" in g for g in games):
        label = f"fold-window crashes (r{fold}+, into a body, kelp or itself on its own move, not deliberate)"
        out[f"{label}: in all"] = len(crashes)
        out[f"{label}: a game"] = len(crashes) / n
        for name in [*(king_band(low) for _, low, _ in KING_BANDS), NO_PATH, THE_KING, "not known"]:
            group = [c for c in crashes if c[1] == name]
            if name == "not known" and not group:
                continue
            out[f"fold-window crashes, {name}: in all"] = len(group)
            if crashes:
                out[f"fold-window crashes, {name}: % of them"] = 100 * len(group) / len(crashes)
            out[f"fold-window crashes, {name}: of them folding (fold set)"] = sum(c[3] for c in group)
        if steps := [c[2] for c in crashes if c[2] is not None]:
            out["fold-window crashes: median path steps from the king"] = statistics.median(steps)
    farms = [f for g in games for f in g.get("farms", [])]
    if farms:
        out["farm entries: in all"] = len(farms)
        for name, group in [*((f"length {b}", [f for f in farms if f[1] is not None and band(f[1]) == b])
                              for b in BANDS),
                            *((name, [f for f in farms if phase(f[0], fold, merge) == name])
                              for name in phases(fold, merge))]:
            out[f"farm entries, {name}: in all"] = len(group)
            for outcome in FARM_OUTCOMES if group else ():
                out[f"farm entries, {name}: % {outcome} within {FARM_WINDOW} rounds"] = (
                    100 * sum(f[2] == outcome for f in group) / len(group))
    if any(len(f) > 3 for f in farms):  # M76: by folding dragons apart (facts from before it lack the flag)
        for name, folding in (("by folding dragons (fold set)", True), ("by dragons not folding", False)):
            group = [f for f in farms if len(f) > 3 and f[3] == folding]
            out[f"farm entries, {name}: in all"] = len(group)
            for outcome in FARM_OUTCOMES if group else ():
                out[f"farm entries, {name}: % {outcome} within {FARM_WINDOW} rounds"] = (
                    100 * sum(f[2] == outcome for f in group) / len(group))
    if any("self_deaths" in g for g in games):  # M76: our longest's deaths into its own body, by the cap flag
        selfs = [d for g in games for d in g.get("self_deaths", [])]
        for flag in ("1", "0", "no line"):
            group = [length for length, cap in selfs if cap == flag]
            out[f"our longest's hitSelf deaths, cap {flag}: in all"] = len(group)
            if group:
                out[f"our longest's hitSelf deaths, cap {flag}: median length"] = statistics.median(group)
    if any("doomed_at_cap" in g for g in games):  # M76, M83: own-move deaths at the cap, doomed by exact reach
        rows = [d for g in games for d in g.get("doomed_at_cap", [])]
        doomed = [d for d in rows if d[5] is None]
        label = "own-move deaths at the cap (cap 1), not deliberate"
        out[f"{label}: in all"] = len(doomed)
        out[f"{label}: games with any"] = sum(any(d[5] is None for d in g.get("doomed_at_cap", [])) for g in games)
        if doomed:
            out[f"{label}: median length"] = statistics.median(d[0] for d in doomed)
        for name in BANDS:
            group = [d for d in doomed if band(float(d[0])) == name]
            early = [d for d in group if d[1] is not None and d[1] >= 1]
            out[f"{label}, length {name}: in all"] = len(group)
            out[f"{label}, length {name}: the flag's doom 1 on the dying turn"] = sum(d[2] is not None for d in group)
            out[f"{label}, length {name}: doomed 1+ rounds before by exact reach"] = len(early)
            out[f"{label}, length {name}: doomed 1+ turns before by the flag"] = sum(bool(d[2]) for d in group)
            if early:
                out[f"{label}, length {name}: median rounds doomed before, of those"] = statistics.median(
                    d[1] for d in early)
                out[f"{label}, length {name}: % of those with a cap 0 turn inside the doom"] = (
                    100 * sum(d[3] for d in early) / len(early))
                out[f"{label}, length {name}: % of those that split out inside the doom"] = (
                    100 * sum(d[4] for d in early) / len(early))
            out[f"own-move deaths at the cap, deliberate (a feed or a split-out's parent), length {name}: in all"] = (
                sum(d[5] is not None and band(float(d[0])) == name for d in rows))
    births: Counter = Counter()
    for g in games:
        births.update(g.get("birth", {}))
    labels = {"age": [name for name, _, _ in AGE_BANDS], "birth": [name for name, _, _ in BIRTH_BANDS],
              "own": sorted({key.split("|")[1] for key in births if key.startswith("own|")})}
    names = {"age": "dragons aged {} rounds", "birth": "dragons born {} long", "own": "turns logged own {}"}
    for group, values in labels.items():
        for label in values:
            key, title = f"{group}|{label}|", names[group].format(label)
            if not births[key + "turns"]:
                continue
            if group != "own":
                out[f"{title}: logged turns"] = births[key + "turns"]
                out[f"{title}: % body < len"] = 100 * births[key + "untracked"] / births[key + "turns"]
                for name in BIRTH_KEYS:
                    for value in sorted({k.split(" ", 1)[1] for k in births if k.startswith(f"{key}{name} ")}):
                        out[f"{title}: % {name} {value}"] = 100 * births[f"{key}{name} {value}"] / births[key + "turns"]
            if births[key + "long"]:
                out[f"{title}: split-outs per 100 turns {LONG}+ long"] = (
                    100 * births[key + "splitouts"] / births[key + "long"])
    return out


# One line naming every debug-line key `games` logged and how census tallies it (method note M40), so a key a later
# champion adds is seen at once: a numeric flag tallied only by band belongs in FLAG_KEYS, an id in ID_KEYS. A key
# seen but tallied nowhere (none, while every extra key gets rows) is listed last.
def log_keys(games: list[dict]) -> str:
    values: dict[str, set[str]] = {}
    for g in games:
        for _, _, text in g["logs"]:
            for key, value in parse_log(text).items():
                values.setdefault(key, set()).add(log_value(key, value) if key not in KNOWN_KEYS else "")
    if not values:
        return "Debug-line keys: none logged."
    kinds = {"read by the rows above": sorted(k for k in values if k in KNOWN_KEYS),
             "0/1 flags (share set to 1)": sorted(k for k in values if k in FLAG_KEYS),
             "ids (share set, not -1)": sorted(k for k in values if k in ID_KEYS),
             "numbers by band": sorted(k for k in values if k not in KNOWN_KEYS | FLAG_KEYS | ID_KEYS
                                       and values[k] <= set(NUMBER_BANDS)),
             "words by value": sorted(k for k in values if k not in KNOWN_KEYS | FLAG_KEYS | ID_KEYS
                                      and not values[k] <= set(NUMBER_BANDS))}
    untallied = sorted(set(values) - {k for keys in kinds.values() for k in keys})
    return ("Debug-line keys (method note M40): " + "; ".join(f"{name}: {', '.join(keys)}"
                                                           for name, keys in kinds.items() if keys)
            + f"; seen but not tallied: {', '.join(untallied) or 'none'}.")


# `games` (extract's dicts with the "map" play_and_count adds; a game without one is its own map) grouped by map.
def by_map(games: list[dict]) -> list[list[dict]]:
    maps: dict[str, list[dict]] = {}
    for index, game in enumerate(games):
        maps.setdefault(game.get("map") or f"game {index}", []).append(game)
    return list(maps.values())


# The median over maps of each census measure of `games`: every map's games counted alone, with every log label's
# value, so a measure a map lacks happened 0 times there. A share of logged turns has no value on a map with no log
# lines, which is left out.
def medians(games: list[dict]) -> dict[str, float]:
    counts = [(census(group, top=None), any(g["logs"] for g in group)) for group in by_map(games)]
    out = {}
    for row in census(games):
        per_map = [count.get(row, 0.0) for count, logged in counts
                   if logged or not row.startswith("% of logged turns")]
        if per_map:
            out[row] = statistics.median(per_map)
    return out


# Round `r`'s phase for the round-500 tallies: before foldRound, foldRound to the round before mergeRound, and on.
# A champion whose foldRound isn't before its mergeRound gets them in round order (an empty middle phase when equal).
def phase(r: int, fold: int, merge: int) -> str:
    low, high = sorted((fold, merge))
    return f"r<{low}" if r < low else f"r{low}-{high - 1}" if r < high else f"r{high}+"


# The phases `phase` gives, in round order, each once.
def phases(fold: int, merge: int) -> list[str]:
    return list(dict.fromkeys(phase(r, fold, merge) for r in (0, *sorted((fold, merge)))))


# The untracked-body share's band for a debug line's len: BANDS by length, or None for a len that isn't a whole
# number of at least 0 (a line to leave out).
def band(size: float) -> str | None:
    if not size.is_integer() or size < 0:
        return None
    return BANDS[0] if size < 8 else BANDS[1] if size < 16 else BANDS[2]


# Why a team's dragon died, from extract's death facts (the replay's death kinds): on purpose (`why`, from its debug
# line: a feed or a split-out's parent, no enemy's kill), a head-on into a shorter enemy head, into one as long or longer,
# into its own body (hitSelf), into a teammate's head, or anything else (a wall, another dragon's body, whoever's: the
# replay doesn't say, no valid move).
def cause(length: int, reason: str, head: int | None, ally: bool, why: str | None = None) -> str:
    if why:
        return DELIBERATE
    if reason == "hitSelf":
        return SELF
    if reason != "hitHeadToHead" or head is None:
        return TRAPPED
    return ALLY if ally else SHORTER if head < length else LONGER


# What decides round 500 in `games` (method note M26), as {measure: value} in report order: each team's longest at
# the end of EARLY, of the round before `fold` (the length the fold starts from) and of LAST, over the games that
# reached that round, and our longest's growth from each of the first two to LAST; deaths a game of our team's
# longest dragon (no living teammate longer then) of each of DEATH_LENGTHS or more, by phase and cause; and the share
# of our logged turns whose debug line has body < len, by length band and phase. A length or share with no games or
# turns to count is left out. After our longest's growth, each team's from the fold's start by source (king_growth).
def endgame(games: list[dict], fold: int, merge: int, child: int = CHILD) -> dict[str, float]:
    n = len(games)
    if not n:
        return {}
    out = {}
    at = [{r: (ours, theirs) for r, ours, theirs in g["longest"]} for g in games]
    for r, label in ((EARLY, f"r{EARLY}"), (fold - 1, f"r{fold - 1} (foldRound's start)"), (LAST, f"r{LAST}")):
        reached = [a[r] for a in at if r in a]
        if reached:
            out[f"our longest at the end of {label}"] = statistics.mean(ours for ours, _ in reached)
            out[f"their longest at the end of {label}"] = statistics.mean(theirs for _, theirs in reached)
    for start in (EARLY, fold - 1):
        grown = [a[LAST][0] - a[start][0] for a in at if start in a and LAST in a]
        if grown:
            out[f"our longest's growth, r{start} to r{LAST}"] = statistics.mean(grown)
    out.update(king_growth(games, fold, child))
    out[f"% of games over before r{LAST} (a team eliminated)"] = 100 * sum(LAST not in a for a in at) / n
    names = phases(fold, merge)
    deaths = Counter()
    for g in games:
        for r, length, teammate, reason, head, ally, *why in g["deaths"]:  # facts from before M36 have no `why`
            if length >= teammate:
                for low in DEATH_LENGTHS:
                    deaths[low, phase(r, fold, merge), cause(length, reason, head, ally, *why)] += length >= low
    for low in DEATH_LENGTHS:
        for name in names:
            for kind in CAUSES:
                out[f"our longest ({low}+) died a game, {name}: {kind}"] = deaths[low, name, kind] / n
    logged, untracked = Counter(), Counter()
    for g in games:
        for r, _, text in g["logs"]:
            fields = parse_log(text)
            size, body = number(fields.get("len")), number(fields.get("body"))
            if size is not None and body is not None and band(size):
                key = band(size), phase(r, fold, merge)
                logged[key] += 1
                untracked[key] += body < size
    for length in BANDS:
        for name in names:
            if logged[length, name]:
                out[f"% of logged turns, length {length}, {name}: body < len"] = \
                    100 * untracked[length, name] / logged[length, name]
    return out


# Where each team's longest's growth from the end of the round before `fold` to the end of LAST came from, a game,
# over the games that reached both and have extract's king meals (MISSES 32: E0117 stopped the fold window's splits
# as waste without measuring where their children's length went): the pearls its king (king_meals) ate in those
# rounds from no merge, from merged split children (`child`, the champion's childLength, long or shorter) and from
# longer merged teammates, one length each; then, where the facts have king losses (king_losses), as negatives, the
# pieces the king split off in those rounds (a child, or its old head at a split-out or even split, where it went on
# in the child), the king's sprint steps, what a parent kept at a split that handed the king on to a shorter child,
# and how much longer than the king the team's longest was at the fold's start. The last two are changes of king
# (another dragon was or went on longest), not the king's losses: before method note M47 all four were one residual,
# "anything else", which read -17.4 a game in one review and was taken for the king's losses. A team eliminated in
# LAST itself has no king line, so its game is left out of that team's rows (its growth would count, its losses not).
# The parts add up to the growth exactly, since a dragon's length changes only by a pearl, a paid sprint step or a
# split. After them, the king line's own rows: its length at the end of the round before `fold` and what its splits
# and sprint steps cost it over the game.
def king_growth(games: list[dict], fold: int, child: int = CHILD) -> dict[str, float]:
    start, out = fold - 1, {}
    counted = [g for g in games if "king" in g and {start, LAST} <= {r for r, _, _ in g["longest"]}]
    for who, key, column in (("our", "ours", 1), ("their", "theirs", 2)):
        rows: dict[str, list[float]] = {}
        label = f"{who} longest's growth, r{start} to r{LAST}"
        king = f"{who} king (the longest at r{LAST} and the dragons it split from)"
        for g in counted:
            if "line" in g and not g["line"][key]:
                continue
            at = {row[0]: row[column] for row in g["longest"]}
            meals = g["king"][key]
            window = [merged for r, merged in meals if start < r <= LAST]
            parts = {label: at[LAST] - at[start],
                     f"{label}: pearls its king ate, no merge (spawned, an enemy's drop or a head-on's)":
                         sum(merged == 0 for merged in window),
                     f"{label}: teammates merged into its king, {child} long or shorter (split children)":
                         sum(0 < merged <= child for merged in window),
                     f"{label}: teammates merged into its king, {child + 1}+ long":
                         sum(merged > child for merged in window)}
            if losses := g.get("line", {}).get(key):
                _, children, handovers, sprints = losses
                parts |= {
                    f"{label}: less the pieces its king split off (a child, or its old head at a split-out or even "
                    "split)": -lost(children, start),
                    f"{label}: less its king's own sprint steps": -lost(sprints, start),
                    f"{label}: less what the parent kept when its king went on in a shorter child (a change of king)":
                        -lost(handovers, start),
                    f"{label}: less the longest's lead over its king at r{start} (another dragon was longest then)":
                        line_length(meals, losses, start) - at[start],
                    f"{king}: length at the end of r{start}": line_length(meals, losses, start),
                    f"{king}: length lost to the pieces it split off, r0 to r{LAST}": lost(children),
                    f"{king}: length lost to its own sprint steps, r0 to r{LAST}": lost(sprints),
                    f"{king}: length the parent kept when it went on in a shorter child, r0 to r{LAST}":
                        lost(handovers)}
            for name, value in parts.items():
                rows.setdefault(name, []).append(value)
        for name in sorted(rows, key=lambda name: name.startswith(king)):  # the king's own rows after the growth's
            out[name] = statistics.mean(rows[name])
    return out


# The median over maps of each endgame measure of `games`; a measure a map gives no value is left out there.
def endgame_medians(games: list[dict], fold: int, merge: int, child: int = CHILD) -> dict[str, float]:
    counts = [endgame(group, fold, merge, child) for group in by_map(games)]
    return {row: statistics.median(count[row] for count in counts if row in count)
            for row in endgame(games, fold, merge, child)}


# The champion's foldRound and mergeRound from its params.hpp text, and a note saying where each came from.
def phase_rounds(params: str) -> tuple[int, int, str]:
    values, notes = [], []
    for name, default in (("foldRound", FOLD), ("mergeRound", MERGE)):
        found = re.search(rf"\b{name}\s*=\s*(\d+)", params)
        values.append(int(found[1]) if found else default)
        notes.append(f"{name} {values[-1]}" + (" from its params.hpp" if found else
                                               " (not in its params.hpp: 2c79f81's value)"))
    return values[0], values[1], " and ".join(notes)


# The champion's childLength (the segments an ordinary split gives its child) from its params.hpp text, else CHILD.
def child_length(params: str) -> int:
    found = re.search(r"\bchildLength\s*=\s*(\d+)", params)
    return int(found[1]) if found else CHILD


# The champion's shortfallPerTile (a pocket's score penalty a tile short) from its params.hpp text, else SHORTFALL.
def shortfall_of(params: str) -> float:
    found = re.search(r"\bshortfallPerTile\s*=\s*([0-9.eE+]+)", params)
    return float(found[1]) if found else SHORTFALL


def figure(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


# The map a replay kept by the referee was played on, from its name (<index>-<map stem>-a<side>.replay).
def map_of(path: Path) -> str:
    return path.stem.split("-", 1)[-1].rsplit("-a", 1)[0]


# A map file's size band (meta.area_band's name), or None when its MAP line can't be read.
def size_of(path: Path) -> str | None:
    import driver
    area = driver.map_area(path)
    return None if area is None else meta.area_band(area)


# A map file's pearl supply: expected spawn attempts per 100 tiles a round (each TILE with a max gap draws its next
# countdown uniformly from min to max gap, so attempts once in (min + max) / 2 rounds), or None when unreadable.
def supply_of(path: Path) -> float | None:
    area, attempts = None, 0.0
    try:
        for line in path.read_text().splitlines():
            f = line.split()
            if f[:1] == ["MAP"] and len(f) >= 3:
                area = int(f[1]) * int(f[2])
            elif f[:1] == ["TILE"] and len(f) >= 5 and int(f[4]) > 0:
                attempts += 2 / (int(f[3]) + int(f[4]))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return 100 * attempts / area if area else None


# The official maps' (OFFICIAL folders of this checkout's maps/) lowest and highest supply_of: 0.15 (Default) to 4.97
# (Portals) on 2026-10-01's 13. Method M97: a review's <=1024 column held one great_divide copy at 9.95, so its size
# column was one map's pearl supply; draw keeps maps inside this range.
OFFICIAL = tuple(Path(__file__).resolve().parent.parent / "maps" / folder for folder in ("ladder", "retired", "sprint"))


def supply_range() -> tuple[float, float]:
    supplies = [s for folder in OFFICIAL for p in folder.glob("*.map") if (s := supply_of(p))]
    return min(supplies), max(supplies)


# MAPS of `pools` ({style: its generated maps}): STYLES of each style in STYLES' order, each the first by name of the
# size band furthest below its SIZES share so far, so the sizes spread over the styles; a style with too few is
# filled from the others' leftovers, with a note saying so. Within a size, a map whose supply_of is inside the
# official maps' (supply_range) goes first; one outside is drawn only when its style has no other of that size, with a
# note. Returns the maps with their styles, and the note.
def draw(pools: dict[str, list[Path]]) -> tuple[dict[Path, str], str | None]:
    low, high = supply_range()
    style_of = {path: style for style, paths in pools.items() for path in paths}
    outside = {p for p in style_of if not low <= (supply_of(p) or -1) <= high}
    sizes = {path: size_of(path) for path in style_of}
    left = dict(SIZES)
    chosen: dict[Path, str] = {}

    def take(paths: list[Path]) -> None:
        path = max(sorted(paths),
                   key=lambda p: (left.get(sizes[p], -1) / SIZES.get(sizes[p], 1), p not in outside))
        chosen[path] = style_of[path]
        left[sizes[path]] = left.get(sizes[path], 0) - 1

    short = []
    for style, count in STYLES.items():
        for _ in range(count):
            if not (paths := [p for p in pools.get(style, []) if p not in chosen]):
                short.append(f"{sum(s == style for s in chosen.values())} of {count} {style}")
                break
            take(paths)
    while len(chosen) < MAPS and (rest := [p for p in style_of if p not in chosen]):
        take(rest)
    notes = [f"too few maps of a style ({', '.join(short)}); others filled in"] if short else []
    if drawn := [p.stem for p in chosen if p in outside]:
        notes.append(f"{', '.join(drawn)} drawn with a pearl supply outside the official maps' {low:.2f}-{high:.2f}")
    return chosen, "; ".join(notes) or None


# rule-counts.md's line naming each map played with its size band, style and pearl supply (supply_of; M97: a size
# column is a few maps, so a review reads its supply before its means), or nothing when no game says its map.
def map_lines(games: list[dict]) -> list[str]:
    maps = {g["map"]: g for g in games if g.get("map")}
    if not maps:
        return []
    low, high = supply_range()
    cells = [f"{name} ({g.get('size') or '?'} tiles, {g.get('style') or '?'}, "
             + ("?" if g.get("supply") is None else f"{g['supply']:.2f}") + ")" for name, g in sorted(maps.items())]
    return [f"Maps (size band, style, pearl supply in spawn attempts per 100 tiles a round; the official maps span "
            f"{low:.2f}-{high:.2f}): {'; '.join(cells)}.", ""]


# A markdown table with a row for each measure of the "all" column, in its order, and a column for each of
# `columns` ({name: (means, medians over maps)}): each cell the mean with the median in brackets (the games count
# alone), empty where that column has no value.
def table(columns: dict[str, tuple[dict[str, float], dict[str, float]]]) -> list[str]:
    lines = ["| measure | " + " | ".join(columns) + " |", "|---" * (len(columns) + 1) + "|"]
    for row in columns["all"][0]:
        cells = ["" if row not in mean else figure(mean[row]) if row == "games" or row not in median else
                 f"{figure(mean[row])} ({figure(median[row])})" for mean, median in columns.values()]
        lines.append(f"| {row} | " + " | ".join(cells) + " |")
    return lines


# rule-counts.md from each opponent's extracted games, with `notes` (what was skipped or failed), the replays kept
# (keep's (replay, facts)), `logging` (whether the champion has the debugIndicator switch, which picks the note on its
# log lines), `rounds` (phase_rounds' foldRound, mergeRound and where they came from) and `child` (child_length) for
# the round-500 section, `online` (online_counterparts: the live build's result-blind games with their summaries and
# ram rows, and the leaders' facts) for the online rows beside the local ones: rams, the pearl race, portal crossings
# and the round-500 king, and `replayed` (collect_online's: those games played again with our debug line on, M90),
# a column of its own in the three tables and the picks.
def report(champion: str, by_opponent: dict[str, list[dict]], notes: list[str], kept: list[tuple[Path, dict]],
           logging: bool, rounds: tuple[int, int, str], child: int = CHILD, online: dict | None = None,
           replayed: dict | None = None) -> str:
    groups = {f"vs {name}": games for name, games in by_opponent.items() if games}
    groups["all"] = [g for games in by_opponent.values() for g in games]
    for band in SIZES:  # every opponent's games on that size's maps
        if sized := [g for g in groups["all"] if g.get("size") == band]:
            groups[f"{band} tiles"] = sized
    columns = dict(groups)
    if replayed and replayed.get("games"):
        columns[f"online {replayed['sid']}, replayed"] = replayed["games"]
    fold, merge, source = rounds
    counts = table({name: (census(games), medians(games)) for name, games in columns.items()})
    tallies = table({name: (events(games, fold, merge), {}) for name, games in columns.items()})
    decides = table({name: (endgame(games, fold, merge, child), endgame_medians(games, fold, merge, child))
                     for name, games in columns.items()})
    replay_note = (f"The last column, where there is one, is online games played again with our debug line on "
                   f"(M90, tools/why.py: our exact submission against the opponent's recorded replies, kept only "
                   f"where every event matched the recording): {online_replay_text(replayed)}. It is our build "
                   "online, not this champion when they differ, and its maps are the ladder's, so read a rule's "
                   "firing rate there against the local columns rather than as a gain.")
    log_note = ("Its debug log line (debugIndicator on, which only adds that line) gives the best-scored move's "
                "direction, target, space and score, so a single step that differs from it came from another branch."
                if logging else "This champion has no debugIndicator switch, so there are no log lines: only what the "
                                "replays show is counted.")
    styles = ", ".join(f"{count} {style}" for style, count in STYLES.items())
    sizes = ", ".join(f"{count} of {band} tiles" for band, count in SIZES.items())
    lines = [f"# Rule counts for champion {champion[:7]}", "",
             f"The champion's logging copy on {MAPS} fresh maps drawn by map style ({styles}; M66: portal and "
             f"dead-end rules need their maps) and across them by size (aiming at {sizes}; M36), each "
             f"against every one of {', '.join(OPPONENTS)}, one game each (its side alternating by map, A on the "
             f"first), played for this review {time.strftime('%Y-%m-%d %H:%M')}. Counts are the logging copy's team "
             "only. Each cell is the mean a game, then in brackets the median over maps (each map counted alone: its "
             "one game in an opponent's column, its game against each opponent in all and its size's column), so a "
             "mean far from its median comes from a few maps. "
             f"{log_note} A ram is a move into an enemy head (ram.hpp); a sprint without one is usually an escape "
             "(escape.hpp). Per-opponent columns are one game a map on the same maps, and each size column every "
             "opponent's games on that size's maps: read differences between them as leads. A death its debug line "
             "shows was on purpose (the dying turn's rule feed, or a split-out's 2-long parent the round after, unless an enemy head killed it: tools/watch.py "
             "deliberate_deaths) is a scan moment `deliberate death:<why>`, never a young death or a death by cause, "
             "and a deliberate cause among the longest's deaths.", "", *map_lines(groups["all"]), replay_note, "",
             *counts, "",
             "## Every game's events (method notes M39, M30)", "",
             "Fixed tallies over every game, counted before the replays are deleted, so the kept replays' pick (losses "
             "first) doesn't bias them; totals rather than means a game, so no medians. Rams: each move of ours into an "
             "enemy head (both die), by victim gain (the victim's length less ours before the move: our length at "
             "death plus the sprint steps it paid, one a step after the first but the fatal one, less the pearls it "
             "ate on the way), where the debug line has it (I352) by the ram turn's logged head edge, and by the "
             "ram's steps (M51): how many, our share of the pearls the two drops held, the net a ram (our pearls from "
             "those drops less our dragon's length) and the share netting below 0, their share, and the swing a ram "
             "(M51: the victim's length less its team's pearls from the drops, minus our length before the move "
             "less ours; the net leaves out what the victim lost) with the share swinging below 0; then, by "
             "steps and victim gain together, how many and the swing a ram. "
             "Actions (M30): each action's turns a game (a step, a sprint, a portal crossing, a split, any turn of a "
             "child in its first 3 rounds (its split's round included), a farm entry where the debug line says `farm 1`; a turn counts "
             "under each that fits), the share that ended in a failure on the dragon's own move, and its deaths on its "
             "own move in all by outcome: failures, rams (it moved into an enemy head: judged by the ram rows above, "
             "not as failures) and deliberate ones (a feed or a split-out's parent), which add up to every such death. "
             "Compare actions by their failure share, e.g. a portal crossing's against a step's. A portal crossing's "
             "deaths also by what they hit (M49): an enemy head (a ram), deliberate, else a teammate's head or body, "
             "an enemy's body, its own body or kelp, adding up to its deaths in all. Split children (M50) by the rule "
             "their parent's debug line gave on the split turn (split, splitout, shed, threatshed, or rule unknown "
             "without a line; the made counts add up to every split): how many, their turns in their first "
             f"{YOUNG} rounds and the failures on their own move in them (adding up to the split child row), the "
             f"share splitting out again in their first {RESPLIT} rounds, the share dying by a feed, and the share "
             "merged into the king (a feed whose drop the king line ate). Small counts are "
             "leads: give n and the chance (loop/chance.py) before calling one a finding. Folding dragons (M40): each "
             "dragon whose debug line named a king to walk to (`fold` set) on some turn, by what became of it: merged "
             "(its death was a feed, the fold's end), with the drop eaten by the king it walked to or by another "
             "teammate or nobody; split out (a split-out's parent, deliberate); killed by an enemy; crashed on its own move (into a body, kelp or itself: the "
             "failure to watch); died otherwise; or alive at the end, each with its mean length on its first folding "
             "turn. Fold-window crashes (M49): each death from foldRound on into a body, kelp or itself on the "
             "dragon's own move, deliberate ones apart, by the path steps (terrain only: kelp blocks, portals carry, "
             "bodies ignored) from its head to our longest living dragon's head just before it, how many of each "
             "band were folding dragons (`fold` set on some turn), and the median steps. "
             "Farm entries (M40): each turn whose debug line says `farm 1`, by the "
             f"dragon's length and the phase, with what it did first within {FARM_WINDOW} rounds: a split-out, a "
             "death, or neither (alive), and (M76) by folding dragons (`fold` set that turn) apart from the rest. "
             "Our longest's hitSelf deaths (M76, I719) by the dying turn's `cap` flag (1: at the unit cap); deaths on "
             "the dragon's own move whose dying line says `cap 1` (M76, M83), deliberate ones (a feed or a split-out's "
             "parent) apart, by length band: how many, how many the bot's own flag called doomed on the dying turn "
             "(`doom 1`: every move lethal or a bad trade) and 1+ turns before it, how many tools/watch.py doom's "
             "exact reach found doomed 1+ rounds before (M83: the flag read 0 of those where exact reach found 164 of "
             "206), and of those the median rounds, the share with a `cap 0` turn inside the doom (from doomed_since "
             "on: a slot was free while it was doomed) and the share that split out inside it (a doom of the dying "
             "turn alone never had a slot free: the denominator for whether a split-out could have saved it). "
             "Births (M43): logged turns by the dragon's age (rounds since it was born) "
             "and its birth length (its len on its first logged turn): the share whose debug line says body < len, "
             f"split-outs per 100 turns of dragons {LONG}+ long, and once the line carries them I370's own and sym "
             "values (with the split-out rate by own).", "", *tallies, "", *ram_counterparts(groups["all"], online),
             log_keys(groups["all"]), "", *ram_swing_lines(online, local_ram_rows(groups["all"])),
             *race_section(groups, online), *crossing_section(groups, online),
             *picks_section({"here": groups["all"], **({"online, replayed": replayed["games"]}
                                                       if replayed and replayed.get("games") else {})}),
             "## What decides round 500 (method note M26)", "",
             f"The same games and cells (the champion's {source}; phases {', '.join(phases(fold, merge))}"
             + ("" if fold < merge else ": foldRound isn't before mergeRound, so the phases split at the two in round "
                "order") + "). Counted from every game before its replay is deleted:",
             f"- Longest: each team's longest dragon at the end of r{EARLY}, of r{fold - 1} (the length foldRound "
             f"starts from) and of r{LAST} (the length that decides round 500), over the games that reached that "
             f"round; growth is our longest at r{LAST} less ours at r{EARLY} or r{fold - 1} (not always one dragon).",
             f"- King (method note M47): the team's longest at r{LAST} and the dragons it split from, back to a "
             f"starting dragon (tools/replays.py king_line). Its length at the end of r{fold - 1}, and what its own "
             f"splits and sprint steps cost it over the whole game (r0-r{LAST}): the pieces it split off (a child, or "
             "at a split-out or even split, where it went on in the child, the old head the parent kept), and, at a "
             "split that handed it on to a shorter child, what the parent kept (a change of king: the parent, the "
             "longer piece, lives on outside the king's line). A sprint costs one length a step after the first.",
             f"- Growth by source (MISSES 32): each team's growth from r{fold - 1} to r{LAST} split by where its "
             f"king's length came from and went in r{fold}-r{LAST}. Pearls it ate that no merge dropped (spawned, an "
             "enemy's, or a teammate's head-on drop); merges, a teammate's drop (any death but a head-on, wherever "
             "it happened) that the king ate, by the merged teammate's length: "
             f"{child} long or shorter (split children, the champion's childLength {child}; the king's own old head, "
             f"left behind when it splits out and dying beside its child, counts here too) and {child + 1}+; less "
             "the pieces it split off (a child, or its old head at a split-out or even split) and its sprint steps "
             "in those rounds; less what a parent kept at a split that handed the king on to a shorter child; and "
             "less the longest's lead over the king at "
             f"r{fold - 1} (another dragon was the team's longest then, so the growth starts from a length the king "
             "never had). The last two are changes of king, not losses of the king's. A team eliminated in "
             f"r{LAST} itself is left out of its rows. A dead dragon of length L drops "
             "ceil(L/2) pearls, one length each to the king. The means add up exactly (a dragon's length changes "
             "only by a pearl, a paid sprint step or a split); the medians over maps needn't.",
             "- Longest deaths: deaths a game of our team's longest dragon at that moment (no living teammate longer), "
             f"{DEATH_LENGTHS[0]}+ long and, of those, {DEATH_LENGTHS[1]}+, by phase and the replay's death kind: on "
             "purpose (its debug line's feed, or a split-out's parent), a head-on into an enemy head shorter "
             "than it, into one as long or longer, into its own body (hitSelf), "
             "into a teammate's head, or trapped or other (a wall, another dragon's body, whoever's: the replay "
             "doesn't say, no valid move).",
             "- Untracked body: the share of our logged turns whose debug line says body < len (the bot knows fewer "
             "of its own segments than its length), by the line's len and the phase.", "", *decides, "",
             *king_pair_section(groups["all"], online), *two_king_section(groups, online),
             *doom_section(groups), *bceval_section(groups["all"])]
    if notes:
        lines += ["Notes:", *(f"- {note}" for note in notes), ""]
    if kept:
        def label(facts: dict) -> str:
            result = "won" if facts["won"] else "drew" if facts["drew"] else "lost"
            last = max((r for r, _, _ in facts["longest"]), default=0)
            size = f", {facts['size']} tiles" if facts.get("size") else ""
            size += f", {facts['style']}" if facts.get("style") else ""
            blind = ", blind pick" if facts.get("blind") else ""
            return f"{result}, {'to r' + str(LAST) if reached_last(facts) else f'over in r{last}'}{size}{blind}"
        long = sum(reached_last(facts) for _, facts in kept)
        blind = [facts for _, facts in kept if facts.get("blind")]
        lines += [f"Replays kept to watch (tools/watch.py dragon|frames; the logging copy played side a, the name's "
                  f"-aA or -aB): the logging copy's losses first, {KEEP_REPLAYS} in all, plus games that reached "
                  f"r{LAST} until at least {KEEP_LAST} kept ones did when that many games did ({long} of these did), "
                  "plus one of each map size none of those is on, and a blind pick for each opponent, on a map rotated "
                  "over the sizes and sides by this review's id, whatever its result "
                  f"({sum(f['won'] for f in blind)} of those {len(blind)} won). The losses "
                  "first overstate failure: judge how common something is from the blind picks and the tallies above, "
                  "not from the losses: " + ", ".join(f"{p} ({label(facts)})" for p, facts in kept), ""]
    return "\n".join(lines)


# ------------------------------------------------------------------------ online counterparts (M72, M75, M78)

# What rule-counts.md sets beside its local rows (M72: local games held 4-5x the head-ons online ones do; M75: no local
# opponent out-kings us; M78: nobody local contests pearls), read on the trusted side when the games are counted:
# "sid", the live submission; "games", its (and its play group's, online.same_play) ladder and autoscrim games chosen blind to the result (online.sample_games,
# dev games apart as online.against_dev reads them) that have summaries, each as {"ours", "theirs" (team summaries),
# "size" (the map's size band), "result", "end" (the end reason), "rams" (online_rams)}; "leaders",
# maintenance.leader_facts for ranks 1-2, and "leader_games", their whole summaries with their sides
# (maintenance.leader_summaries; neither with `leaders` False: study.md's ram cells). A failed read is empty.
def online_counterparts(repo: Path, state: Path, leaders: bool = True) -> dict:
    import maintenance
    import online
    out: dict = {"sid": None, "games": [], "leaders": []}
    try:
        live = online.read(state / "online.json", {})
        out["sid"], dev = live.get("submission_id"), online.dev_ids(live)
        for g in online.sample_games(live.get("games", [])):
            summary = g.get("summary") or {}
            teams, side = summary.get("teams") or {}, g.get("side")
            if g.get("submission_id") not in online.same_play(live) or online.against_dev(g, dev) or side not in teams:
                continue
            winner = (summary.get("result") or {}).get("winner")
            out["games"].append({"ours": teams[side], "theirs": teams.get("B" if side == "A" else "A", {}),
                                 "size": (summary.get("map") or {}).get("sizeBand"),
                                 "result": "draw" if winner is None else "won" if winner == side else "lost",
                                 "end": (summary.get("result") or {}).get("endReason"), "rams": online_rams(g)})
    except (OSError, ValueError, TypeError, AttributeError) as error:
        out["error"] = f"{type(error).__name__}: {error}"
    try:
        out["leaders"] = maintenance.leader_facts(repo, state) if leaders else []
        out["leader_games"] = maintenance.leader_summaries(repo, state) if leaders else []
    except (OSError, ValueError, TypeError, KeyError) as error:
        out["error"] = f"{type(error).__name__}: {error}"
    return out


def online_source(online: dict | None) -> str:
    if not online or online.get("sid") is None:
        return "no online sample was read" + (f" ({online['error']})" if online and online.get("error") else "")
    return (f"the live build {online['sid']}'s {len(online['games'])} result-blind ladder and autoscrim games "
            "(online.sample_games, dev games apart)")


# The ram rows' online counterpart (M72): head-ons started a game against enemy heads by the victim's length against
# ours (tools/replays.py behaviour.headOnsStarted, at death: shorter, equal, longer), and the enemy's into us, for the
# live build's games beside this review's (rams by victim gain at the ram's start, VGAIN_BANDS pooled the same way).
def ram_counterparts(games: list[dict], online: dict | None) -> list[str]:
    def cells(shorter: float, tie: float, longer: float, on_us: float | None) -> str:
        text = (f"{shorter + tie + longer:.1f} a game (on shorter heads {shorter:.1f}, at a tie {tie:.1f}, on longer "
                f"heads {longer:.1f})")
        return text + (f", the enemy's into us {on_us:.1f} a game" if on_us is not None else "")

    n = len(games)
    rams = [gain(r) for g in games for r in g.get("rams", [])]
    here = cells(sum(x < 0 for x in rams) / n, sum(x == 0 for x in rams) / n, sum(x > 0 for x in rams) / n,
                 sum(g["rammed"] for g in games) / n if all("rammed" in g for g in games) else None) if n else "none"
    rows = [g for g in (online or {}).get("games", []) if (g["ours"].get("behaviour") or {}).get("headOnsStarted")
            is not None]
    if rows:
        started = lambda team, kind: sum((g[team]["behaviour"]["headOnsStarted"] or {}).get(kind, 0)  # noqa: E731
                                         for g in rows) / len(rows)
        theirs = [g for g in rows if (g["theirs"].get("behaviour") or {}).get("headOnsStarted") is not None]
        on_us = (sum(sum(v for k, v in g["theirs"]["behaviour"]["headOnsStarted"].items() if k != "vsAlly")
                     for g in theirs) / len(theirs)) if theirs else None
        there = cells(started("ours", "vsShorter"), started("ours", "vsEqual"), started("ours", "vsLonger"), on_us)
    else:
        there = "none"
    return [f"Ram rows online (M72): our rams on enemy heads here {here}; online ({online_source(online)}, "
            f"{len(rows)} with head-on counts) {there}. A rate a game read here is at local contact density: divide "
            "a ram or guard change's count by the head-ons it acted among before comparing it with online games.", ""]


# A saved online game's rams both ways at the current online.RAM_VERSION ({"ours", "theirs"}: watch.ram_rows), None
# before online.refresh_rams has read it.
def online_rams(game: dict) -> dict | None:
    import online
    rams = game.get("rams") or {}
    return rams if rams.get("version") == online.RAM_VERSION and "ours" in rams else None


# The ram cells' swings (M85, M51's swing), as {label: [swing a ram]}: by victim gain at the ram's start
# (VGAIN_BANDS), by head edge (edge_label), by steps (STEP_BANDS) and by steps and victim gain together, the events
# tallies' cells. `rows` are [gain, edge, swing, steps] (watch.ram_rows' order; local_ram_rows turns ram_events'
# into it); a cell no row falls in is left out.
def ram_cells(rows: list[list]) -> dict[str, list[float]]:
    cells = {f"rams {label}": [r[2] for r in rows if low <= r[0] <= high] for label, low, high in VGAIN_BANDS}
    for e in sorted({edge_label(r[1]) for r in rows if r[1] is not None}, key=edge_order):
        cells[f"rams at head edge {e}"] = [r[2] for r in rows if r[1] is not None and edge_label(r[1]) == e]
    for label, low, high in STEP_BANDS:
        stepped = [r for r in rows if r[3] is not None and low <= r[3] <= high]
        cells[f"rams of {label}"] = [r[2] for r in stepped]
        cells.update((f"rams of {label} {name}", [r[2] for r in stepped if lo <= r[0] <= hi])
                     for name, lo, hi in VGAIN_BANDS)
    return {label: swings for label, swings in cells.items() if swings}


def local_ram_rows(games: list[dict]) -> list[list]:
    return [[gain(r), r[4], r[6], r[7]] for g in games for r in g.get("rams", []) if len(r) > 7]


# rule-counts.md's and study.md's online ram cells (method M85: a5e4552's 3-step trade-downs read -0.37 a ram here and
# +0.79 online): each cell's swing a ram and n, here (our rams in this review's games, the logged head edge) when
# `local` is given, and online (the live build's result-blind games with online_rams: ours on them and theirs on us,
# the head edge rebuilt from the round-start frame), so a ram or guard idea reads the column of the games it will play.
def ram_swing_lines(online: dict | None, local: list[list] | None = None) -> list[str]:
    games = [g for g in (online or {}).get("games", []) if g.get("rams")]
    columns = ([("here, ours", local)] if local is not None else []) + [
        ("online, ours", [r for g in games for r in g["rams"]["ours"]]),
        ("online, theirs on us", [r for g in games for r in g["rams"]["theirs"]])]
    cells = {name: ram_cells(rows) for name, rows in columns}
    order = list(ram_cells([[g, e, 0, s] for _, g, _ in VGAIN_BANDS for e in range(-EDGE_CLIP, EDGE_CLIP + 1)
                            for _, s, _ in STEP_BANDS]))  # every cell, in the events tallies' order
    labels = [label for label in order if any(label in c for c in cells.values())]
    lines = ["## Ram swings here and online (method M85)", "",
             "The swing a ram (M51: the victim's length less its team's pickups, minus the rammer's length before the "
             "move less its pickups) and how many, by victim gain at the ram's start and by head edge (heads of the "
             f"rammer's team in its view less the enemy's), from {online_source(online)}: {len(games)} with ram rows "
             "(online.refresh_rams; online replays carry no debug line, so their head edge is rebuilt from the "
             "round-start frame, a local one is the logged one). 'theirs on us' is the opponents' rams on our heads, "
             "from their side. Local cells are read at local contact density: a ram or guard change is judged by "
             "the column of the games it will play.", "",
             "| cell | " + " | ".join(name for name, _ in columns) + " |",
             "|---|" + "---|" * len(columns)]
    for label in labels:
        shown = [(f"{statistics.mean(v):+.2f} (n {len(v)})" if (v := cells[name].get(label)) else "none")
                 for name, _ in columns]
        lines.append(f"| {label} | " + " | ".join(shown) + " |")
    return lines + [""]


def race_teams(games: list[dict]) -> list[dict]:
    return [{"pearlRace": {b: {"n": n, "ate": ate} for b, (n, ate) in g["race"].items()}, "raceBy": "path"}
            for g in games if "race" in g]  # read by this code, so by path (M95)


# rule-counts.md's pearl race (M78, tools/replays.py race_text): our share of the pearls both teams saw, first seen by
# both in r100-299, by the gap between the two teams' nearest heads then, here by opponent, then online (the live
# build's result-blind games) and the leaders (ranks 1-2's studied ranked games), so a change to how we contest pearls
# is read where opponents contest them.
def race_section(groups: dict[str, list[dict]], online: dict | None) -> list[str]:
    import replays
    lines = ["## Pearl race (method M78)", "",
             f"Pearls both teams saw (in a head's 7x7 view at the start of its turn), first seen by both in "
             f"r{replays.RACE_ROUNDS[0]}-r{replays.RACE_ROUNDS[1]}, by the gap between our nearest head and theirs "
             "then (path steps: kelp blocks, portals carry, bodies ignored), with the share of those eaten that we ate. "
             "Local opponents are our own code and yield pearls as we do; online opponents and the leaders contest "
             "them. A contest change's SPRT declares the crowder or sprinter opponent mix (loop/GENERALISE.md).", ""]
    for name, games in groups.items():
        if not name.endswith(" tiles") and (text := replays.race_text(race_teams(games))):
            lines.append(f"- here, {name}: {text}")
    online_rows = (online or {}).get("games", [])
    lines.append(f"- online, {online_source(online)}: "
                 + (replays.race_text([g["ours"] for g in online_rows]) or "none with the race yet (summaries are "
                    "re-read 25 a cycle within online.SUMMARY_BUDGET_S)"))
    for label, facts in (online or {}).get("leaders", []):
        lines.append(f"- {label}, its studied ranked games: "
                     + (replays.race_text(facts) or "none summarised with the race yet"))
    return [*lines, ""]


# rule-counts.md's portal crossings and in-place deaths (M80, M84; tools/replays.py crossing_text): ours and the
# opponent's here by opponent, then online, our live build's result-blind games and their opponents', and the leaders
# (ranks 1-2's studied ranked games), so a portal or dead-end change is read beside the teams that out-cross and
# out-pump us.
def crossing_section(groups: dict[str, list[dict]], online: dict | None) -> list[str]:
    import replays
    lines = ["## Portal crossings and in-place deaths (method M80, M84)", "",
             "A game's moves through a portal and the share whose crosser died within "
             f"{replays.CROSSING_WINDOW} rounds (and in the crossing's own round); in-place deaths, a dragon dying "
             "where it stands: a split-out's parent (2 long, dead the round after its own split, not by an enemy "
             "head) or noValidAction (timeouts and unpaid sprint steps apart, the latter counted after), the "
             "leaders' dead-end pumps; and stations, tiles with "
             f"{replays.STATION_DEATHS}+ of them in a game. Local opponents are our own code.", ""]
    for name, games in groups.items():
        rows = [g["crossing"] for g in games if "crossing" in g]
        if not name.endswith(" tiles") and rows:
            lines += [f"- here, {name}, ours: {replays.crossing_text([r['ours'] for r in rows])}",
                      f"- here, {name}, theirs: {replays.crossing_text([r['theirs'] for r in rows])}"]
    online_rows = (online or {}).get("games", [])
    missing = "none summarised with it yet (summaries are re-read 25 a cycle within online.SUMMARY_BUDGET_S)"
    lines += [f"- online, ours, {online_source(online)}: "
              + (replays.crossing_text([g["ours"] for g in online_rows]) or missing),
              "- online, their opponents: " + (replays.crossing_text([g["theirs"] for g in online_rows]) or missing)]
    for label, facts in (online or {}).get("leaders", []):
        lines.append(f"- {label}, its studied ranked games: " + (replays.crossing_text(facts) or missing))
    return [*lines, ""]


# M75: the round-500 king pair, each side's king line at the end of tools/replays.py KING_ROUND (king_at_fold) over
# games that went to the round limit: ours v theirs (games), losses apart, and by size band.
def king_pair(pairs: list[tuple[int, int, str | None, bool]]) -> str:
    if not pairs:
        return "none"

    def mean(rows: list) -> str:
        return (f"ours {statistics.mean(o for o, _, _, _ in rows):.1f} v theirs "
                f"{statistics.mean(t for _, t, _, _ in rows):.1f} ({len(rows)})") if rows else "none"

    sizes = "; ".join(f"{band} {mean([p for p in pairs if p[2] == band])}" for band in SIZES
                      if any(p[2] == band for p in pairs))
    return f"{mean(pairs)}, losses {mean([p for p in pairs if p[3]])}; by size: {sizes or 'none'}"


# M86's two-king row over `groups`' games (every game, before its replay is deleted), for each opponent and all: the
# round-limit losses where ceil(s / 2), s our second-longest at the end of r499, was above the margin (their longest
# less ours), by our second-longest's fold gate at GATE_ROUND (two_kings), and the round-limit wins where theirs was
# above ours over theirs. Folding a dragon in adds ceil(length / 2) (a dead dragon's drop), so this is necessary for a
# fold at r499 to turn the game, not sufficient. Beside them, the same counts from the end standings (tools/replays.py
# `end`: longest, second) of the live build's result-blind online games and the leaders' ranked games (`online`,
# online_counterparts), so a fold idea's target is read online too. Fold post-mortems read it (loop/POSTMORTEM.md).
def two_king_section(groups: dict[str, list[dict]], online: dict | None = None) -> list[str]:
    lines = [f"Two kings (M86): round-limit games whose loser's second-longest s at the end of r{LAST} had ceil(s/2) "
             "above the margin it lost by (the winner's longest less its own), ours in our losses, with our "
             f"second-longest's fold gate (debug line `gate`) at r{GATE_ROUND}, and theirs in our wins: necessary at "
             f"r{LAST} (a fold then adds ceil(s/2)), not sufficient.", ""]
    for name, games in groups.items():
        if name.endswith(" tiles"):
            continue
        rows = [(g, g["two_kings"], {r: (o, t) for r, o, t in g["longest"]}[LAST]) for g in games if g.get("two_kings")]
        losses = [(k, o, t) for g, k, (o, t) in rows if not g["won"] and not g["drew"]]
        wins = [(k, o, t) for g, k, (o, t) in rows if g["won"]]
        ours = [k for k, o, t in losses if math.ceil(k["second"][0] / 2) > t - o]
        gates = Counter(k["gate"] for k in ours)
        theirs = sum(math.ceil(k["second"][1] / 2) > o - t for k, o, t in wins)
        lines.append(f"- here, {name}: {len(ours)} of {len(losses)} round-limit losses"
                     + (f" (gate at r{GATE_ROUND}: " + ", ".join(f"{gate} {n}" for gate, n in gates.most_common()) + ")"
                        if gates else "")
                     + f"; theirs in {theirs} of {len(wins)} round-limit wins")
    if online and online.get("sid") is not None:
        rows = [(g["ours"]["end"], g["theirs"]["end"], g["result"]) for g in online["games"]
                if g.get("end") == "roundLimit" and g["ours"].get("end") and g["theirs"].get("end")]
        lines.append(f"- online, {online_source(online)}: {end_kings(rows)}")
    for label, games in (online or {}).get("leader_games", []):
        rows = []
        for summary, side in games:
            result = summary.get("result") or {}
            ends = [(summary["teams"].get(s) or {}).get("end") for s in (side, "B" if side == "A" else "A")]
            if result.get("endReason") == "roundLimit" and all(ends):
                won = result.get("winner")
                rows.append((*ends, "draw" if won is None else "won" if won == side else "lost"))
        lines.append(f"- {label}, its ranked games (theirs is its opponent's): {end_kings(rows)}")
    return [*lines, ""]


# two_king_section's counts from end standings, [(the team's end, its opponent's end, "won"/"lost"/"draw")] of
# round-limit games: its losses whose second-longest, rounded up after halving, exceeded the margin, and its wins
# whose opponent's did. A row whose summary predates the `second` standing is left out.
def end_kings(rows: list[tuple[dict, dict, str]]) -> str:
    rows = [r for r in rows if all(key in end for end in r[:2] for key in ("longest", "second"))]
    losses = [(o, t) for o, t, r in rows if r == "lost"]
    wins = [(o, t) for o, t, r in rows if r == "won"]
    ours = sum(math.ceil(o["second"] / 2) > t["longest"] - o["longest"] for o, t in losses)
    theirs = sum(math.ceil(t["second"] / 2) > o["longest"] - t["longest"] for o, t in wins)
    return f"{ours} of {len(losses)} round-limit losses; theirs in {theirs} of {len(wins)} round-limit wins"


# rule-counts.md's `text` cut to what a post-mortem reads (driver.prepare_postmortem): its header and its "What decides
# round 500" section, which holds the two-king line (two_king_section); None when that line is missing (a review from
# before M86).
def postmortem_excerpt(text: str) -> str | None:
    header, _, rest = text.partition("\n## ")
    decides = next((section for section in rest.split("\n## ") if section.startswith("What decides round 500")), "")
    if not rest or "\nTwo kings (M86)" not in decides:
        return None
    return f"{header.rstrip()}\n\n## {decides.rstrip()}\n"


def king_pair_section(games: list[dict], online: dict | None) -> list[str]:
    import replays
    here = [(g["king_fold"]["ours"]["length"], g["king_fold"]["theirs"]["length"], g.get("size"),
             not g["won"] and not g["drew"]) for g in games
            if (g.get("king_fold") or {}).get("ours") and g["king_fold"].get("theirs")]
    there = [(g["ours"]["king"]["length"], g["theirs"]["king"]["length"], g["size"], g["result"] == "lost")
             for g in (online or {}).get("games", []) if g["ours"].get("king") and g["theirs"].get("king")]
    return [f"Round-500 king pair (M75): each side's king line at the end of r{replays.KING_ROUND} (tools/replays.py "
            "king_at_fold) in games that went to the round limit, mean lengths (games). Here: " + king_pair(here)
            + f". Online ({online_source(online)}): " + king_pair(there) + ".", ""]


# rule-counts.md's death table (MISSES 64): every game's deaths, ours against the opponent's, by where the dying head
# was and what shut the dragon in (tools/watch.py doom), for all games and each size band. The maps are fresh, one
# game each an opponent, so a size band stands in for the per-map rows loop/online.py gives the ladder's maps. Both
# sides are labelled by moves alone, as online (M57): a feed is `feed?` on either side and a cap feed before foldRound
# `capfeed?` (M63), and our debug lines say how well those labels fit (feed_check, capfeed_check); a line after the
# checks files our own deaths with the debug lines' deliberate ones first (extract's doom_ours).
def doom_section(groups: dict[str, list[dict]]) -> list[str]:
    import watch
    rows = [(name, g["doom"]) for name, games in groups.items() if name == "all" or name.endswith(" tiles")
            for g in games if "doom" in g]
    if not rows:
        return []
    games = groups.get("all", [])
    feeds, found, labels = (sum(g["feed_check"][k] for g in games if "feed_check" in g) for k in range(3))
    deeper = Counter()
    for g in games:
        deeper.update(g.get("deeper") or {})

    def detail(row: str) -> str:  # M81: the runner-up penalised or close, and our heads in its room, where facts have them
        parts = [f"{name} {deeper[f'{row}: {name}']}" for name in ("runner-up penalised", "runner-up close")
                 if deeper[f"{row}: {name}"]]
        heads = [f"{name} {deeper[f'{row}: heads {name}']}" for name, _, _ in ROOM_HEADS
                 if deeper[f"{row}: heads {name}"]]
        if heads:
            parts.append("our other heads in its room " + ", ".join(heads))
        return f" ({'; '.join(parts)})" if parts else ""

    split = "; ".join(f"{place}: " + ", ".join(f"{how} {deeper[f'{place} {how}']}{detail(f'{place} {how}')}"
                                               for how in DEEPER_HOW if deeper[f"{place} {how}"])
                      for place in ("open deeper", "pocket deeper") if any(deeper[f"{place} {h}"] for h in DEEPER_HOW))
    caps, cap_found, cap_labels = (sum(g["capfeed_check"][k] for g in games if "capfeed_check" in g) for k in range(3))
    # M63: our own deaths with our debug lines' deliberate ones first, so what is left is what went wrong.
    ours = Counter()
    for g in games:
        ours.update(g.get("doom_ours") or {})
    debug = [g for g in games if "doom_ours" in g]
    first = ", ".join(f"{kind} {count / len(debug):.2f}" for kind, count in ours.most_common(DEBUG_FIRST_TOP)) \
        if debug else "none (facts from before M63)"
    shut = {who: [sum(g["shutters"][who][k] for g in games if "shutters" in g) for k in range(4)]
            for who in ("ours", "theirs")}
    shut_text = "; ".join(f"{who} {n}, {alone} by one teammate alone, whose shutting move was a split {now} "
                          f"({100 * now / n:.0f}%) or came the turn after its split {after} ({100 * after / n:.0f}%)"
                          for who, (n, alone, now, after) in shut.items() if n) or "none"
    return ["## How deaths came about (MISSES 64)", "",
            "Per game, ours against the opponent's (tools/watch.py doom). " + watch.DOOM_TABLE_NOTE + " Both sides "
            "are labelled by their moves alone, since only our copy logs a debug line (M57). Our debug lines check "
            f"`feed?`: it labelled {found} of our {feeds} feeds (a feed, capfeed or recycle rule in any round; it "
            f"labels only from foldRound on), and "
            f"{labels - found} of its {labels} labels of ours were no feed (a crash beside a longer teammate); and "
            f"`capfeed?`: it labelled {cap_found} of our {caps} deaths at the cap before foldRound (capfeed or "
            f"recycle), and {cap_labels - cap_found} of its {cap_labels} labels of ours were neither.", "",
            f"Our deaths a game with our debug lines' deliberate ones filed first (tools/watch.py deliberate_deaths: "
            f"`feed` is a feed or capfeed, `split-out` a split-out's parent), the rest by moves as in the table, "
            f"commonest {DEBUG_FIRST_TOP} over {len(debug)} games (M63: by moves alone our deliberate deaths were a "
            f"false lead): {first}.", "",
            "Teammate traps (M93: tools/watch.py Doom.shutter, the teammate whose move alone shut the doomed dragon "
            f"in, its body put back as it was with any child it split off since removed), over all games: {shut_text}.",
            "",
            "Our `deeper` rows by how the move scorer rated the dooming move, from our debug line at since-1 (M67, "
            "rulecounts.deeper_split: closed, the move was rated a closed pocket; open unseen, open only through "
            "never-seen edges; open known, open through seen ones), each with (M81) how many had the runner-up "
            "penalised (it scored a tile's pocket shortfall, P.shortfallPerTile, or more below the move: a worse "
            "pocket, ram risk, a teammate's exit or lethal, which the logged gap can't tell apart) or close (within "
            "it: a choice, an equally short pocket or a head-reach cut, also not told apart), and our other heads in "
            f"its room at since (the up to {watch.ROOM_CAP} tiles nearest the head by open edges, kelp and portals "
            "cut, bodies ignored: a jam of ours against a lone dragon's pocket), over all games: "
            + (split or "none") + ".",
            "", "```", *watch.doom_table(rows), "```", ""]


# rule-counts.md's pearl-target picks (M91: 38% of consecutive pearl-target turn pairs switched uneaten in a review's
# 8 kept replays, which no count showed): per kind (PEARL_KINDS) and source ({name: games}, here and the replayed
# online games), the picks, the share eaten by their picker within EAT_WINDOW rounds, the episode's turns, how the
# episodes ended (PICK_ENDS) and the share of switches that returned. Over every game, before its replay is deleted.
def picks_section(sources: dict[str, list[dict]]) -> list[str]:
    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.0f}%" if whole else "-"

    lines = ["## Pearl-target picks (method M91)", "",
             "A pick is a turn whose debug line names a pearl target (`target` a tile, `tk` one of "
             f"{', '.join(PEARL_KINDS)}) its line the round before didn't; its episode runs while each next round's line "
             f"keeps that tile, and is read as the kind at the pick. Eaten in {EAT_WINDOW}: the picker ate a pearl on "
             "that tile within that many rounds of the pick, even after switching away. Ends: eaten (by the picker in "
             "the episode), gone (another dragon ate it, by the end of the round after), switch (the next line names "
             "another pearl tile), drop (a target of another kind, or none), end (no next line: it died or the game "
             f"ended); returned is the share of switches whose tile comes back within {RETURN_TURNS} turns. Local "
             "picks are at local contact density: read a change to a picker by the column of the games it will play.",
             "", "| kind | from | picks | eaten in 30 | turns mean (median) | eaten | gone | switch | drop | end | "
             "returned |", "|---" * 11 + "|"]
    for kind in (*PEARL_KINDS, "all"):
        for name, games in sources.items():
            picks = [p for g in games for p in g.get("picks", []) if kind in ("all", p[0])]
            if not picks and kind != "all":
                continue
            ends = Counter(p[3] for p in picks)
            turns = [p[1] for p in picks]
            switches = [p[4] for p in picks if p[3] == "switch"]
            lines.append(f"| {kind} | {name} | {len(picks)} | {pct(sum(p[2] for p in picks), len(picks))} | "
                         + (f"{statistics.mean(turns):.1f} ({statistics.median(turns):g})" if turns else "-") + " | "
                         + " | ".join(pct(ends[e], len(picks)) for e in PICK_ENDS)
                         + f" | {pct(sum(switches), len(switches))} |")
    return lines + [""]


# rule-counts.md's turning points: bceval's (bceval_read.section) in the logging copy's losses and draws, by
# opponent and size; nothing while bceval is off. About 1 s a lost game at nice 19 (local replays carry debug lines), so
# a review's 24 games add under half a minute. A report only.
def bceval_section(games: list[dict]) -> list[str]:
    lost = [({"opponent": g.get("opponent"), "size": g.get("size")}, g.get("bceval")) for g in games if not g["won"]]
    lines = bceval_read.section(lost, ("opponent", "size"), "losses and draws of the logging copy")
    return ["## Turning points (bceval)", "", *lines] if lines else []


# ------------------------------------------------------------------------------------------------- files

def root(state: Path) -> Path:
    return state / "rule-games"


# The newest measured rule-counts.md under the loop home `state` (a finished review's canonical copy, its header naming
# the champion measured), or None. Each post-mortem gets its header and round-500 section (postmortem_excerpt): fold
# changes read its two-king row (M86).
def latest(state: Path) -> Path | None:
    def measured(path: Path) -> bool:
        with path.open() as handle:
            return handle.read(40).startswith("# Rule counts for champion")

    done = [path for path in root(state).glob(f"*/{NAME}") if path.parent.name.isdigit() and measured(path)]
    return max(done, key=lambda path: int(path.parent.name), default=None)


# The protected folder of the review in `work` (its name is the review's time_ns folder name).
def run_folder(state: Path, work: Path) -> Path:
    if not work.name.isdigit():
        raise ValueError(f"not a review folder: {work}")
    return root(state) / work.name


# Removes `path` without following a symlink: a link is unlinked, and a real folder is deleted only when it resolves
# inside `inside` (rmtree itself unlinks, never follows, the links within).
def remove(path: Path, inside: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        resolved, base = path.resolve(), inside.resolve()
        if resolved == base or not resolved.is_relative_to(base):
            raise RuntimeError(f"refusing to delete {path}: it resolves to {resolved}, outside {base}")
        shutil.rmtree(path)


# The review's copy of rule-counts.md, written before the worker's first run (or, for a native review, by its own
# trusted session's measurement). Anything but a plain file there is left alone: the review then reads the
# canonical copy that context.md also names.
def copy_to_work(work: Path, text: str) -> None:
    try:
        fd = os.open(work / NAME, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
        with os.fdopen(fd, "w") as handle:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("not a plain file")
            handle.truncate(0)
            handle.write(text)
    except OSError as error:
        print(f"could not write {work / NAME}: {error}", flush=True)


# The review's prompt, saved by meta.prepare_review in the protected run folder, which the worker can't change. The
# worker is started from this copy, never from work/PROMPT.md.
def save_prompt(state: Path, work: Path, text: str) -> None:
    folder = run_folder(state, work)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "PROMPT.md").write_text(text)


# Makes sure the saved prompt exists: a review prepared before prompts were saved gets its work/PROMPT.md saved
# first, read with meta.worker_file's checks.
def ensure_prompt(state: Path, work: Path) -> None:
    if not (run_folder(state, work) / "PROMPT.md").exists():
        save_prompt(state, work, meta.worker_file(work / "PROMPT.md"))


# When process `pid` started, as ps prints it, or None when there is no such process.
def started(pid: int) -> str | None:
    result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], text=True, capture_output=True)
    return result.stdout.strip() or None


def read_status(state: Path, work: Path) -> dict:
    try:
        value = json.loads((run_folder(state, work) / "status.json").read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_status(folder: Path, value: str) -> None:
    temporary = folder / "status.json.tmp"
    temporary.write_text(json.dumps({"status": value, "pid": os.getpid(), "started": started(os.getpid()),
                                     "at": time.time()}))
    temporary.replace(folder / "status.json")


# "done", "failed", "measuring" (the recorded process is still the one that started measuring: same pid, same start
# time) or None (never measured, or its measurement died).
def status(state: Path, work: Path) -> str | None:
    record = read_status(state, work)
    if record.get("status") in ("done", "failed"):
        return record["status"]
    pid = record.get("pid")
    if record.get("status") == "measuring" and isinstance(pid, int) and record.get("started") \
            and started(pid) == record["started"]:
        return "measuring"
    return None


def stopped(state: Path) -> bool:
    return (state / "STOP").exists() or (state / "ASTRA_STOP").exists()


# ------------------------------------------------------------------------------------------------- playing

# Runs `native.py games review` for one opponent, one game a map (the referee's --one-side: the logging copy `a` as
# team A on the first map, B on the next, ...), killing its process group past `timeout` seconds; returns a note when
# the games didn't all finish, else None.
def play(repo: Path, python: str, a: Path, b: Path, maps: list[str], out: Path, replays: Path,
         timeout: float, log) -> str | None:
    global _games
    command = [python, str(repo / "loop/native.py"), "games", "review", "--a", str(a), "--b", str(b),
               "--maps", *maps, "--one-side", "--out", str(out), "--replays", str(replays)]
    _games = subprocess.Popen(command, cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                              start_new_session=True, env={**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1"})
    try:
        code = _games.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_games()
        return "stopped at the deadline; the games that finished are counted"
    finally:
        _games = None
    return None if code == 0 else f"the games exited {code}; see {log.name}"


def kill_games() -> None:
    games = _games
    if games is None or games.poll() is not None:
        return
    os.killpg(games.pid, signal.SIGTERM)
    try:
        games.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(games.pid, signal.SIGKILL)
        games.wait()


# SIGTERM (a stop of the review process): the games are in their own session, so take them down too.
def on_term(signum, frame) -> None:
    kill_games()
    stop_online()
    raise SystemExit(143)


# ------------------------------------------------------------------------ online replays with our debug line (M90)

# Online replays carry no debug line, so rule counts play the live build's result-blind games again with it on
# (tools/why.py: our exact submission's commit, the opponent's recorded replies, the site's pearl timings from a map
# file; 25 of 31 reproduced on 29 Sep) and count them as a column of their own beside the local ones. ONLINE_SAMPLE
# games at most, drawn blind from the live build's saved games (online.file_selected and dev games left out) by the
# review's id, one at a time in one process at home.YIELD_NICE (a player beside an evaluation), each waiting its turn
# (online_turn: this Mac's gates, then why.py's one-at-a-time lock), all within ONLINE_BUDGET_S (about 20 s a game when the gate is open, 29 Sep) and never past the review's DEADLINE_S. A game that
# no map file reproduces, or that diverges, is left out and counted. Only games that match the recording event for
# event are counted: past a difference the replay is no longer the real game.
ONLINE_SAMPLE = 24
ONLINE_BUDGET_S = 20 * 60
ONLINE = "online"  # the run folder's subfolder: the replayed games and online.jsonl, a row a game tried
GATE_POLL_S = 30  # how long one wait on tools/gated_test.sh lasts before the stop and parent checks run again


# Why the online step must end now, or None: a stop marker (stopped), or `parent`, the review process that started it,
# gone (the step runs in its own session, so a SIGKILLed parent's on_term never stops it).
def online_ended(state: Path, parent: int) -> str | None:
    if stopped(state):
        return "a stop marker is set"
    return None if os.getppid() == parent else f"its parent {parent} is gone"


# Why this Mac's own gates for the loop's niced games hold the next online replay, or None: the 1-minute load where
# sig's sessions stop starting work (home.sessions_full, at the loop's load share) or reclaimable memory under
# home.MIN_FREE_GB (niced_slots' test).
def mac_held(state: Path) -> str | None:
    import home
    try:
        text = (state / "load-fraction").read_text()
    except OSError:
        text = ""
    load, cores = os.getloadavg()[0], os.cpu_count() or 1
    if home.sessions_full(load, home.load_share(text), cores):
        return f"1-minute load {load:.2f} on {cores} cores"
    return f"{home.reclaimable_gb():.1f} GB reclaimable" if home.reclaimable_gb() < home.MIN_FREE_GB else None


# Waits until the next online replay may start: mac_held clear and tools/gated_test.sh's dev-load gate open, checking
# online_ended every GATE_POLL_S. None when it may, else (status, note) for its row: "stopped", "budget" (the deadline
# passed with the gates open) or "gate" (they stayed shut until it).
def online_turn(state: Path, parent: int, deadline: float) -> tuple[str, str] | None:
    held = None
    while True:
        if ended := online_ended(state, parent):
            return "stopped", ended
        left = deadline - time.time()
        if left <= 0:
            return ("gate", f"this Mac's gates stayed shut for the rest of the budget ({held})") if held else \
                ("budget", "the time budget ran out")
        if held := mac_held(state):
            time.sleep(min(GATE_POLL_S, left))
            continue
        gate = subprocess.run([str(meta.REPO / "tools/gated_test.sh"), "true"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              env={**os.environ, "GATED_MAX_WAIT": str(int(max(min(GATE_POLL_S, left), 1)))})
        if not gate.returncode:
            return None
        held = "dev load (tools/gated_test.sh)"


# The live build's saved games a replay can be drawn from, in the blind order `seed` gives: its index's games (every
# game saved for it, online.json's own list is a window) that aren't result-chosen files or dev games, with a replay.
def online_sample(state: Path, seed: int) -> tuple[int | None, list[dict]]:
    import online
    live = online.read(state / "online.json", {})
    sid, dev = live.get("submission_id"), online.dev_ids(live)
    index = online.read(state / "online-replays" / str(sid) / "index.json", {"games": []}) if sid else {"games": []}
    games = [g for g in index.get("games", []) if g.get("submission_id") == sid and g.get("side") in ("A", "B")
             and not online.file_selected(g) and not online.against_dev(g, dev) and g.get("replay")
             and Path(g["replay"]).is_file()]
    random.Random(seed).shuffle(games)
    return sid, games[:ONLINE_SAMPLE]


# The `online` step's body (its own process, started by start_online): plays each sampled game again into
# `out` (<id>-a<side>.replay, with our debug lines; the build it drew from in sample.json: "sid", "commit",
# "sampled") and appends a row a game to out/online.jsonl ({"id", "side",
# "map", "size", "result", "status": reproduced | diverged | not reproducible | error | gate | budget | stopped,
# "note"}), until `deadline` (epoch seconds), a stop marker or the end of `parent` (online_turn).
def replay_online(state: Path, out: Path, seed: int, deadline: float, parent: int) -> None:
    import why
    out.mkdir(parents=True, exist_ok=True)
    sid, games = online_sample(state, seed)
    commit = why.submission_commit(sid, state) if sid else None
    (out / "sample.json").write_text(json.dumps({"sid": sid, "commit": commit, "sampled": len(games)}))
    with (out / "online.jsonl").open("a") as log:
        for g in games:
            summary = g.get("summary") or {}
            winner = (summary.get("result") or {}).get("winner", g.get("api_winner"))
            row = {"id": g["id"], "side": g["side"], "map": (summary.get("map") or {}).get("name"),
                   "size": (summary.get("map") or {}).get("sizeBand"),
                   "result": "draw" if winner is None else "won" if str(winner).upper() == g["side"] else "lost"}
            if held := online_turn(state, parent, deadline):
                row.update(status=held[0], note=held[1])
                log.write(json.dumps(row) + "\n")
                break
            try:
                side, _, game = why.replay_game(Path(g["replay"]), g["side"], commit, debug=True, moves=False)
                if game.difference is None:
                    (out / f"{g['id']}-a{side}.replay").write_bytes(game.blob)
                    row.update(status="reproduced", note=game.map)
                else:
                    row.update(status="diverged", note=f"round {game.difference[0]}")
            except SystemExit as stop:  # why.py's refusals: no map file, not reproducible, no commit
                row.update(status="not reproducible", note=str(stop).splitlines()[0][:200])
            except Exception as error:
                row.update(status="error", note=f"{type(error).__name__}: {error}"[:200])
            log.write(json.dumps(row) + "\n")
            log.flush()


# Starts the `online` step in the background (its own session, at home.YIELD_NICE, with the games' `python`, whose
# engine is the loop's toolchain) into folder/ONLINE, done by `deadline` (epoch seconds).
def start_online(python: str, state: Path, folder: Path, seed: int, deadline: float, log) -> None:
    global _online
    import home
    _online = subprocess.Popen(["nice", "-n", str(home.YIELD_NICE), python, str(Path(__file__).resolve()), "online",
                                str(state), str(folder / ONLINE), str(seed), str(deadline), str(os.getpid())],
                               cwd=meta.REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                               env={**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1"})


def stop_online() -> None:
    proc = _online
    if proc is None or proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


# Waits for the `online` step until `deadline` (epoch seconds), stops it there or at once when a stop marker is set in
# `state`, and reads what it left in `out`:
# {"sid", "commit", "sampled" (sample.json), "rows" (online.jsonl), "games" (extract's facts for each reproduced game, map and size from its
# summary, opponent "online"), "notes"}. The replays are deleted once read.
def collect_online(state: Path, out: Path, deadline: float, fold: int, shortfall: float) -> dict:
    global _online
    proc = _online
    if proc is not None:
        while proc.poll() is None and time.time() < deadline and not stopped(state):
            try:
                proc.wait(timeout=min(max(deadline - time.time(), 0.1), 5))
            except subprocess.TimeoutExpired:
                pass
        stop_online()
        _online = None
    try:
        sample = json.loads((out / "sample.json").read_text())
    except (OSError, ValueError):
        sample = {}
    result = {"sid": sample.get("sid"), "commit": sample.get("commit"), "sampled": sample.get("sampled", 0),
              "rows": [], "games": [], "notes": []}
    try:
        result["rows"] = [json.loads(line) for line in (out / "online.jsonl").read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as error:
        result["notes"].append(f"no online replays read: {type(error).__name__}: {error}")
    for row in result["rows"]:
        path = out / f"{row['id']}-a{row['side']}.replay"
        if row.get("status") != "reproduced" or not path.is_file():
            continue
        try:
            result["games"].append({**extract(path, row["side"], fold, shortfall), "map": row.get("map") or "?",
                                    "size": row.get("size"), "opponent": ONLINE, "online": row["id"]})
        except Exception as error:
            result["notes"].append(f"online game {row['id']} unreadable: {type(error).__name__}: {error}")
    for path in out.glob("*.replay"):  # read, or cut short by the deadline
        path.unlink()
    return result


# What the online column's header says: how many games were tried and reproduced, and why the rest weren't.
def online_replay_text(replayed: dict | None) -> str:
    if not replayed or not replayed.get("sid"):
        return "no live build was read, so no online games were replayed"
    tried = Counter(row.get("status") for row in replayed["rows"])
    rest = ", ".join(f"{status} {n}" for status, n in tried.items() if status != "reproduced")
    commit = (replayed.get("commit") or "unknown commit")[:7]
    return (f"the live build {replayed['sid']} ({commit})'s {len(replayed['rows'])} result-blind ladder and autoscrim "
            f"games tried of {replayed.get('sampled', 0)} drawn (at most {ONLINE_SAMPLE}, online_sample), "
            f"{tried['reproduced']} "
            f"reproduced and counted ({len(replayed['games'])} read)" + (f"; the rest {rest}" if rest else ""))


# Each opponent's blind pick, {opponent: replay}, chosen from play order and `seed` alone, never a result (M39: the
# review of 1790436765968434000 kept 6 losses and 2 more to r499 of 24 games, so watching them overstated failure). The
# k-th opponent (OPPONENTS order) takes the size band (k + seed) mod the number of bands, in SIZES order, and in it its
# (k + seed)-th game by map order, wrapping: with seed 0 and draw's 2, 4 and 2 maps by size, the champion's first small
# map (side A), the swarm's second mid one (B) and the breeder's first big one (A), so each pick is a different map,
# size and side as far as there are. An opponent without a game of its band takes from all its games.
def blind_picks(results: list[tuple[Path, dict]], seed: int = 0) -> dict[str, Path]:
    by_opponent: dict[str, list[tuple[Path, dict]]] = {}
    for path, facts in sorted(results, key=lambda item: item[0].name):
        if facts.get("opponent") is not None:
            by_opponent.setdefault(facts["opponent"], []).append((path, facts))
    names = [n for n in OPPONENTS if n in by_opponent] + sorted(n for n in by_opponent if n not in OPPONENTS)
    bands = [band for band in SIZES if any(facts.get("size") == band for _, facts in results)] or [None]
    picks = {}
    for k, name in enumerate(names):
        games = by_opponent[name]
        band = bands[(k + seed) % len(bands)]
        pool = [item for item in games if band is None or item[1].get("size") == band] or games
        picks[name] = pool[(k + seed) % len(pool)][0]
    return picks


# The replays to keep, as (replay, extract's facts): the logging copy's losses first, then the rest, KEEP_REPLAYS in
# all, then the next that reached r499 (losses first) until KEEP_LAST of those kept did or none is left, then for each
# size in SIZES none of those is on, its first game (losses first), so a review can watch every size (M36). Beside
# those, each opponent's blind pick (blind_picks, from `seed`), marked `blind`.
def keep(results: list[tuple[Path, dict]], seed: int = 0) -> list[tuple[Path, dict]]:
    ordered = sorted(results, key=lambda item: (item[1]["won"], item[0].name))
    kept = ordered[:KEEP_REPLAYS]
    short = KEEP_LAST - sum(reached_last(facts) for _, facts in kept)
    kept += [item for item in ordered[KEEP_REPLAYS:] if reached_last(item[1])][:max(short, 0)]
    for band in SIZES:
        if not any(facts.get("size") == band for _, facts in kept):
            kept += [item for item in ordered if item[1].get("size") == band][:1]
    blind = set(blind_picks(results, seed).values())
    kept += [item for item in results if item[0] in blind and item not in kept]
    return [(path, {**facts, "blind": True} if path in blind else facts) for path, facts in kept]


# Whether a game went to its last round (LAST), from extract's `longest`.
def reached_last(facts: dict) -> bool:
    return any(r == LAST for r, _, _ in facts["longest"])


# Plays and counts the games in `folder` (a fresh protected run folder); returns rule-counts.md's text. The champion's
# copies are built from its commit into a new folder, so nothing already on disk is built; the rotation opponents are
# the driver's own builds. Raises when nothing played.
def play_and_count(repo: Path, state: Path, folder: Path, deadline_s: float) -> str:
    import driver
    import maintenance
    deadline = time.monotonic() + deadline_s
    champion = meta.champion_commit(repo)
    if not champion:
        raise RuntimeError("no loop/champion commit")
    python = maintenance.toolchain(state)["python"]
    bots = Path(tempfile.mkdtemp(prefix="bots-", dir=folder))
    params = subprocess.run(["git", "show", f"{champion}:bot/params.hpp"], cwd=repo, check=True, text=True,
                            capture_output=True).stdout
    logging = bool(re.search(r"\bdebugIndicator\s*=", params))
    rounds, child, shortfall = phase_rounds(params), child_length(params), shortfall_of(params)
    copy = driver.gauntlet_bot({"name": "champion-log", "commit": champion,
                                "params": {"debugIndicator": True} if logging else {}}, into=bots)
    online_deadline = time.time() + min(ONLINE_BUDGET_S, deadline_s)  # M90, beside the local games
    review_id = int(folder.name) if folder.name.isdigit() else 0
    if not stopped(state):
        with (folder / "online.log").open("a") as online_log:
            start_online(python, state, folder, review_id, online_deadline, online_log)
    try:  # a raise before collect_online must not leave the online step playing
        notes, opponents = [], {}
        for name in OPPONENTS:
            try:  # the screen's opponents are the driver's own builds in <loop home>/gauntlet
                opponents[name] = (driver.gauntlet_bot({"name": "champion", "commit": champion}, into=bots)
                                   if name == "champion" else driver.rotation_bots([name])[0][1])
            except Exception as error:
                notes.append(f"no games against {name}: {type(error).__name__}: {error}")
        if not opponents:
            raise RuntimeError("; ".join(notes))
        seed, pools = random.SystemRandom().randrange(1 << 30), {}
        for n, style in enumerate(STYLES):  # each style from its own seeds, so no two maps share a name
            try:
                driver.generate_maps(folder / "maps" / style, seed=seed + n * POOL, count=POOL, style=style)
                pools[style] = list((folder / "maps" / style).glob("*.map"))
            except Exception as error:  # a referee checkout without the style yet: the others fill in
                notes.append(f"no {style} maps: {type(error).__name__}: {error}")
        chosen, short = draw(pools)
        for path in (folder / "maps").rglob("*.map"):  # the maps folder keeps only the maps played
            if path not in chosen:
                path.unlink()
        if short:
            notes.append(short)
        maps = [str(p) for p in chosen]  # the same order for each opponent's sides
        sizes, supplies = {p.stem: size_of(p) for p in chosen}, {p.stem: supply_of(p) for p in chosen}
        styles = {p.stem: style for p, style in chosen.items()}
        results: list[tuple[Path, dict]] = []
        by_opponent: dict[str, list[dict]] = {}
        with (folder / "games.log").open("a") as log:
            for name, bot in opponents.items():
                remaining = deadline - time.monotonic()
                if stopped(state) or remaining < 60:
                    notes.append(f"no games against {name}: " + ("a stop marker is set" if stopped(state)
                                                                 else "the deadline had passed"))
                    continue
                if note := play(repo, python, copy, bot, maps, folder / f"games-{name}.jsonl",
                                folder / "replays" / name, remaining, log):
                    notes.append(f"against {name}: {note}")
                by_opponent[name] = []
                for path in sorted((folder / "replays" / name).glob("*.replay")):
                    side = "A" if path.stem.endswith("-aA") else "B"
                    try:
                        facts = {**extract(path, side, rounds[0], shortfall), "map": map_of(path),
                                 "size": sizes.get(map_of(path)),
                                 "style": styles.get(map_of(path)), "supply": supplies.get(map_of(path)),
                                 "opponent": name}
                    except Exception as error:  # one unreadable replay must not stop the rest
                        notes.append(f"{path.name} unreadable: {type(error).__name__}: {error}")
                        continue
                    if not facts["won"]:  # bceval_section's turning points, before the replay is deleted (never raises)
                        facts["bceval"] = bceval_read.game(path, side, state)
                    by_opponent[name].append(facts)
                    results.append((path, facts))
        remove(bots, folder)
        replayed = collect_online(state, folder / ONLINE, online_deadline, rounds[0], shortfall)
        notes += replayed["notes"]
        if not results:
            raise RuntimeError("no game finished: " + "; ".join(notes))
        kept = keep(results, review_id)  # the review id seeds the blind picks
        for path, _ in results:
            if path not in {p for p, _ in kept}:
                path.unlink(missing_ok=True)
        return report(champion, by_opponent, notes, kept, logging, rounds, child, online_counterparts(repo, state),
                      replayed)
    finally:
        stop_online()


# Measures in the review's protected run folder, recreated empty, and copies the result (a placeholder first) into
# work/rule-counts.md; earlier reviews' kept replays are deleted. Never raises on a failed measurement: the failure
# is the result, written as a note. Returns a line to log.
def measure(repo: Path, state: Path, work: Path, deadline_s: float = DEADLINE_S) -> str:
    base = root(state)
    base.mkdir(parents=True, exist_ok=True)
    folder = run_folder(state, work)
    for other in base.iterdir():
        if other.is_dir() and not other.is_symlink() and other != folder:
            remove(other / "replays", base)
    folder.mkdir(exist_ok=True)
    for entry in folder.iterdir():  # a fresh measurement: everything but the saved prompt
        if entry.name != "PROMPT.md":
            remove(entry, folder)
    save_status(folder, "measuring")
    copy_to_work(work, f"{MEASURING} (started {time.strftime('%H:%M')}, at most {deadline_s // 60:.0f} "
                             "minutes). Read the other files first and come back to this one.\n")
    try:
        text, result = play_and_count(repo, state, folder, deadline_s), "done"
        line = f"rule counts written: {work / NAME}"
    except Exception as error:
        text = (f"{FAILED}: {type(error).__name__}: {str(error)[:600]}\n\nNo fresh games this time. Count what you "
                "can from the champion's replays in the study, audit and evidence folders (context.md), and say in "
                "your report that the rule counts were missing.\n")
        result, line = "failed", f"rule counts failed: {error}"
    (folder / NAME).write_text(text)
    copy_to_work(work, text)
    save_status(folder, result)
    return line


# Starts `measure` in the background unless it is done, failed or running (native claim, which returns the review's
# task at once). The Claude driver measures inside meta.launch_review's process instead, before the worker starts.
def start(state: Path, work: Path) -> None:
    if status(state, work):
        return
    root(state).mkdir(parents=True, exist_ok=True)
    with log_file(state, work) as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "measure", str(state), str(work)],
                         cwd=root(state),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


# The review process's log, beside its protected run folder (which each measurement recreates).
def log_file(state: Path, work: Path):
    return (root(state) / f"{run_folder(state, work).name}.log").open("a")


# The Claude driver's review process: with `measure` (the review's first launch only) plays the rule-count games
# into rule-counts.md, then, unless a stop marker appeared, replaces itself with the review worker, so review.json's
# pid follows both steps. The worker reads the saved prompt and writes session.json and session.stderr in the
# protected run folder; nothing in `work` is opened. Its command and environment are exactly a plain confined launch's.
def review(state: Path, work: Path, measure_first: bool) -> None:
    if measure_first:
        try:
            print(measure(Path(__file__).resolve().parent.parent, state, work), flush=True)
        except Exception as error:  # the review runs without its counts; the log says why
            print(f"rule counts failed before measuring: {type(error).__name__}: {error}", flush=True)
    if stopped(state):
        raise SystemExit("a stop marker is set: not starting the review worker")
    folder = run_folder(state, work)
    folder.mkdir(parents=True, exist_ok=True)  # before the settings list the live entries to Edit-deny
    try:
        command = meta.private_tmp(meta.worker_command(work, meta.worker_settings(state, "meta", work),
                                                       meta.worker_reads(state, "meta"), usage="review"))
    except quota.Held as held:  # a level crossed while it measured: run_meta reads this as a quota stop, uncounted
        (folder / "session.json").write_text(json.dumps({"type": "result", "is_error": True, "result": str(held)}))
        raise SystemExit(str(held)) from None
    ensure_prompt(state, work)
    meta.brief(state, work, folder)
    for fd, target in zip(meta.worker_io(folder), (0, 1, 2)):
        os.dup2(fd, target)
        os.close(fd)
    os.chdir(work)
    for number in (signal.SIGPIPE, signal.SIGXFSZ):  # Python ignores these; the worker starts as a shell would
        signal.signal(number, signal.SIG_DFL)
    os.execvpe(command[0], command, meta.worker_env())


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 6 and args[0] == "online":  # start_online's own process
        replay_online(Path(args[1]), Path(args[2]), int(args[3]), float(args[4]), int(args[5]))
        return
    if len(args) not in (3, 4) or args[0] not in ("measure", "review") or args[3:] not in ([], ["--measure"]):
        raise SystemExit(__doc__)
    signal.signal(signal.SIGTERM, on_term)
    state, work = Path(args[1]), Path(args[2])
    if args[0] == "measure":
        print(measure(Path(__file__).resolve().parent.parent, state, work), flush=True)
    else:
        review(state, work, measure_first=args[3:] == ["--measure"])


if __name__ == "__main__":
    main()
