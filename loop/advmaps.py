"""Adversarial maps (sig, 2026-09-27): search map space for the champion's blind spots against a naive bot, instead of
waiting to trip on the next one. Every champion from v0001 to v0036 loses both sides to the toolkit's starter bot (the
`starter` entry of referee/gauntlet.toml) on gen_eval_85163986, a variant of maps/community/portal_labyrinth.map: our
dragons walk into corridors the move scorer rates open, because unseen edges are assumed open. The organisers also said
they plan "adversarial maps". So, once per champion, against the starter, both sides of every map:

1. Regression: every map in the referee checkout's maps/adversarial/ (maps kept from earlier finds, which the
   orchestrator commits to main), and any committed there later, at the next pass, reopening a finished search
   (new_regressions). Each plays the starter unless its README row's Opponent cell names a referee/gauntlet.toml
   entry (regression_opponents; a name with no entry is an error row, regressions; a map whose row names another
   opponent since it was listed plays again against that one, new_regressions). A loss (lost: either side against
   the starter, both against a strong opponent) or a map that couldn't be played alerts, once, but a loss the newest
   earlier champion that played the map also had doesn't (earlier_record: it goes to run.log and the record only).
   stats.md lists the record, a line a map (stats_lines).
2. Seed pool: SEED_MAPS maps, dealt round-robin over seed_sources(): every mapgen/gen.py style (whatever the referee
   checkout's gen.py has, so new styles join by themselves), near variants of the community maps, and KNOBS, gen.py's
   own builders with one knob pushed to its extreme (kelp share, maze, portals, sparse or far farms, close, long or
   many starts, a walled border with halves joined by few gates, food behind a portal).
3. Hill-climb: each round, the KEEP worst maps scored so far (margin, below) each get MUTANTS mutants (MUTATIONS: re-roll a
   region's kelp, add or lengthen 1-wide corridors, move fast pearls into dead ends, add or move portal pairs, food
   behind a portal, move dragons or change their lengths, wall the border, cut the board into halves with few gates).
   The worst maps are chosen again from every map scored, so a mutant scoring worse than its parent takes its place;
   a map is a parent at most PARENT_TRIES times. Every map is mirrored as gen.py makes it (symmetric), and passes
   valid(): gen.near_ok against its parent (reach, overlaps, boxed heads), board and dragon limits (docs/unsw
   map-info.md, map-files.md, overview.md), well-formed portals, and no dragon trapped at its start (trapped: every
   bot loses it, so a find there is the map's, not the champion's; 2026-09-29, all 8 of ffc2c10's finds were such
   auto-draws bred from one kelp_region mutant, c075). A seed map with a trapped dragon is skipped too. A map the
   engine can't play is an error row, never scored.
4. A find: a map we lose or draw on (either side) that also loses or draws on at least REPRODUCE of PERTURBATIONS fresh
   perturbations of it (gen.py's near recipe: a torus motion, then gen.nudge), so it is a region of map space, not one
   lucky layout. A game is the same whenever a Mac plays it (the pearl seed comes from the map file; Linux and Windows
   hosts build the bots themselves, so theirs can differ move for move). A find is copied with its perturbations,
   their results, up to KEEP_REPLAYS replays, our deaths by cause (hitWall deaths in a dead end or 1-wide corridor
   counted apart, dead_ends) and its shape (features, the lessons inventory's flags) to
   <loop home>/advmaps/<champion>/finds/<name>/, with one ADVMAPS FIND line in host-alerts.log when luck alone would
   give that many perturbation losses at most chance.TRIGGER_P of the time (find_chance, corrected over the maps
   searched and over the opponents a search plays, groups(), so a search's chance of any luck-only alert is at most
   chance.TRIGGER_P; run.log and stats.md list the others as suggestive). Nothing is queued: the orchestrator judges
   each find and commits the ones it keeps to maps/adversarial/ with a README line.

Past its first GAMES_CAP games (2026-09-27: all three idle fillers had finished v0038's budgets and every game slot sat
idle 12:06-12:25), the search goes on only on idle machines (wanted: no evaluation on the hosts), checked before
every map, waiting in place when something does (sweep.wait_idle), and at the spare share (native.spare_share):
what the variant sweep and the prewarm leave of each host, given back within a game when either asks for slots (until
2026-09-28 it waited for both to finish, while the prewarm alone left most slots idle); a tune, a late screen or an
official-map report keeps its half of each host while it asks (native.host_share):

5. The strong pass (strong_pass): the champion against each SPRT rotation opponent (driver.ROTATION, the
   referee/gauntlet.toml entries), one at a time, on copies of the seed pool and then of the STRONG_WORST worst climb
   maps the starter search scored (named "<opponent>-<map>"). Against a strong opponent a map counts as lost only when
   we lose or draw both sides (lost): the rotation beats or draws us on one side of 20-57% of maps, but on both sides
   of 2.4-16% (1eae05f's shared-set baselines, 166 maps each). Losing to a strong opponent is expected now and then,
   so its lost maps are judged against that opponent's own loss rate on the seed pool (find_chance's `opponent`), once
   the whole pass against it is in: a lost map gets STRONG_PERTURBATIONS perturbations only when all of them lost
   could clear chance.TRIGGER_P at that rate (perturbations_for; otherwise it is a fluke, "not perturbed"), and is a
   find only when its perturbation losses do clear it. The worst climb maps aren't played when no find could clear it
   (their games could find nothing), and a pass isn't started when the budget can't hold all of it. Measured power, at
   STRONG_WORST + the 60 pool maps searched: at 1-2 of 60 pool maps lost (swarm's and rammer's rate) a find takes 6 of
   10 perturbations lost, a coin flip for a map whose perturbations we lose 55% of the time (80% at 67%); at 9-12 of
   60 (the breeder's) 9 of 10, a coin flip at 84% (80% at 92%); from 20 of 60 none is possible. Its finds go to
   finds/<opponent>-<map>/ with the opponent in find.json; no replays are kept for these full-length games (a few MB
   each; a Mac plays the one wanted again move for move).
6. Then the hill-climb above goes on against the starter, while its MAX_FINDS aren't reached.

A map's margin (margin, map_margin) is the worse side's, so a map is a blind spot when either start is: a loss 0, a
draw 0.5, a win with at most NARROW of our dragons left and fewer than we started with 1-1.6 (on a map starting one
dragon a team, a win with that one left is no near miss), any other win 2-3, more the more of ours are left and the
sooner it ends by elimination. Tiers keep the brief's order (loss, draw, narrow win, then few dragons or a late or
no elimination) whatever the within-tier details; the better side adds at most 0.03, a tie-break.

Budget per champion: GAMES_CAP search games for new seed and climb maps at the usual filler priority, IDLE_CAP in all
counting those (the strong pass and more climbing, idle machines only; IDLE_CAP is 10 GAMES_CAP: v0038's first 600
games took 9 minutes, 13:36-13:45, so about an hour and a half of otherwise idle machines, strong games being longer),
plus at most PERTURB_RESERVE more past either for the perturbations of maps already lost; at most MAX_FINDS finds an
opponent. The starter's MAX_FINDS ends its climb; the search ends when the strong pass and the climb are both done,
or the budget is spent. Regression games are extra (2 a map in maps/adversarial/).

A search that finished (and tidy deleted its maps) is reopened by tick for a map committed to maps/adversarial/ since,
and, when it finished without its idle stages (before they existed, or a run by hand's), for those stages once nothing
else wants the machines. It never reads a deleted file: the strong pass makes a seed map again from the origin saved
with it (remake: gen.py's style and seed, or the knob or community recipe and seed; not its index, which a style
appended to gen.STYLES moves), exact while that style's pin holds (loop/test_mapgen_pins.py; after an intended pin
change it is another map under the same name), and the climb and the perturbations pass over maps whose files are gone. So its strong pass copies only the worst climb maps
tidy kept: the STRONG_WORST worst while `base_done` wasn't set, but for a search tidied before that rule (2026-09-27)
only the KEEP worst seed or climb maps, so at most KEEP of them.

It is a filler like the variant sweep (loop/sweep.py): the upkeep tick (maintenance.tick) starts it in the background
only while no evaluation plays on the remote hosts (sweep.busy; a late screen or an official-map report keeps its half
of each host, native.host_share, and doesn't hold it back since 2026-09-29), and it stops between maps when one does
(the evaluation lock's stages that play only on this Mac don't stop it), waiting in place until it no longer does
(sweep.wait_idle), and ends when the champion moves or a stop marker appears, a later idle tick starting it again;
past GAMES_CAP the tick and the run also wait for wanted(). Its games go
through the frozen referee as filler games (driver.referee with yielding="filler"): load gates, remote hosts and
native.host_share apply, so the idle fillers (the sweep, the prewarm and this) together play on at most each host's
whole capacity, half while a tune or a screen filler asks for slots, split among those playing; past GAMES_CAP its games
(yielding="spare") take only what the other two leave, so alone it has each whole host, less a tune's half. It
reads only files it and the referee wrote (no worker writes under
<loop home>/advmaps), and maps/adversarial/README.md from the referee checkout, which driver.refresh_referee brings
a README change to as it does a map (driver.ADVERSARIAL_README).

Files and their bounds: <loop home>/advmaps.json, the progress (champion, pid, games, finds, flukes, regression
record; a few KB, the flukes a line a lost map). Under <loop home>/advmaps/<champion>/: maps.jsonl, a line a map
(about 0.2 KB; at most (IDLE_CAP + PERTURB_RESERVE) / 2 + the regression maps, under 0.7 MB); results.jsonl, a line a
game (about 0.6 KB; at most IDLE_CAP + PERTURB_RESERVE + 2 a regression map, under 4 MB); maps/, one file a map
(about 1.4 KB, v0038's 302 took 428 KB; under 70 KB for a 64x64 one) while the search runs, so about 4.5 MB at
IDLE_CAP; kept/, the replays (up to 8 MB each, a few hundred KB for a short game) of starter maps lost and not yet
judged, two a map; finds/, at most MAX_FINDS folders an opponent of a map, its perturbations, find.json and (the
starter's) KEEP_REPLAYS replays; run.log, a few lines a batch. When the search ends, or a new champion cuts it short
(retire), tidy leaves the two .jsonl files, the finds, the KEEP worst maps and, while `base_done` isn't set, the
STRONG_WORST worst climb maps (about 28 KB): under 6.5 MB beside the finds' replays (at most MAX_FINDS * KEEP_REPLAYS
of 8 MB). Folders of champions older than the one before the current one keep only the
two .jsonl files, run.log and the finds' maps and find.json (prune): under 6 MB each, 0.1 MB for the first real run's
(98 games, no find), one folder a champion.

    .venv/bin/python loop/advmaps.py check                         # the current search's progress, worst maps, finds
    .venv/bin/python loop/advmaps.py run --repo R --state S        # what tick launches
    .venv/bin/python loop/advmaps.py run --manual [--beside] [--max-load L] [--seed-maps N] [--keep K] [--climb-rounds R]
                                   # a run by hand for today's champion, idle or not; --beside plays beside evaluations
                                   # (and stops at GAMES_CAP: past it the search takes idle machines only)
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, deque
from pathlib import Path

import chance
import meta
import online
from home import evaluation_running

REPO = Path(__file__).resolve().parent.parent
SEED_MAPS = 60  # seed-pool maps: 120 games
KEEP = 5  # the worst maps mutated each climb round
# Mutants each of them gets a round: KEEP x MUTANTS maps (BATCH_MAPS) a round. At one each a round was 10 games of about
# 3 s (median 2.9 s, 29 Sep, 604 games), so the search kept about 3.4 games in flight on 72 slots, each round waiting
# on its slowest game and a new referee process.
MUTANTS = 4
PARENT_TRIES = 3 * MUTANTS  # mutants a map gets before it stops being a parent: three rounds' worth, as before
GAMES_CAP = 600  # search games a champion at the usual filler priority (seed and climb maps; not regression games)
IDLE_CAP = 10 * GAMES_CAP  # search games a champion in all, those past GAMES_CAP only while wanted() is None
STRONG_WORST = 20  # the starter search's worst climb maps the strong pass adds to the seed pool
STARTER = "starter"  # the opponent of every map without one
PERTURBATIONS = 6  # fresh perturbations played for a lost map
STRONG_PERTURBATIONS = 10  # for a strong opponent's lost map (perturbations_for)
REPRODUCE = 3  # of them lost or drawn for a find
MAX_FINDS = 5  # finds a champion an opponent; the starter's climb ends at this many
# Games past either cap that perturbations of lost maps may use: MAX_FINDS maps' worth at the larger count (a strong
# opponent's STRONG_PERTURBATIONS), 2 games each.
PERTURB_RESERVE = MAX_FINDS * max(PERTURBATIONS, STRONG_PERTURBATIONS) * 2
KEEP_REPLAYS = 4  # replays kept with a find: its own two games, then lost perturbation games
BATCH_MAPS = 20  # maps a referee call: 40 games
# Past GAMES_CAP, on idle machines alone: 29 Sep 22:05-22:18, the only filler left, its 20-map calls one opponent at a
# time kept 0-13 of 72 slots busy (14 on average), each call ending on its slowest games. Now a call mixes opponents and
# holds more than every slot's worth, so its tail is a smaller part of it; it still stops between maps (halt).
IDLE_BATCH_MAPS = 60
NARROW = 2  # a win with at most this many of our dragons left is a narrow win
COMFORT = 2.0  # margins under this (a loss, draw or narrow win) get their deaths tallied
FAST_GAP = 26  # a fast pearl tile: mean spawn gap (min + max) / 2 at most this (the lessons inventory's FAST)
MAX_LENGTH = 25  # longest dragon a mutation makes (Slithery Fight starts one of 25)
MAX_TEAM = 64  # dragons a team (docs/unsw/overview.md)
SIDES = (10, 64)  # board width and height (docs/unsw/map-info.md)
RETRY_S = 3600  # after a failed run
STALE = 6 * 3600  # a run recorded as running with no batch finished for this long is hung
PLAN_BOUND = 100_000  # bytes advmaps/<champion>/plan.json may hold, rewritten each batch (BATCH_MAPS maps; health)
PAUSED = 3  # `play`'s exit code when busy() stopped it
SEARCH = ("seed", "climb", "strong", "perturb")  # the stages whose games count against the budget
KNOBS = ("kelp", "maze", "portals", "sparse", "far_farms", "close_starts", "long_starts", "many_starts",
         "walled_halves", "portal_food")


def read(state: Path) -> dict:
    return online.read(state / "advmaps.json", {})


def save(state: Path, record: dict) -> None:
    online.save(state / "advmaps.json", record)


def folder_of(state: Path, champion: str) -> Path:
    return state / "advmaps" / champion


# ------------------------------------------------------------------------------------------------- mapgen

# The referee checkout's mapgen/gen.py (driver.REFEREE, refreshed from main), else this repo's: the maps the loop's
# tests play come from the referee's copy, and its STYLES may already hold styles this checkout lacks. Loaded by path
# under its own module name, so neither copy shadows the other.
def load_gen(root: Path | None = None):
    if root is None:
        import driver
        root = driver.REFEREE if (driver.REFEREE / "mapgen/gen.py").exists() else REPO
    path = root / "mapgen/gen.py"
    key = "advmaps_gen_" + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:10]
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module  # before running it: its dataclasses look their module up
        spec.loader.exec_module(module)
    return sys.modules[key]


def fresh_portal_id(board) -> int:
    return max([k for k in board.edges.values() if k >= 0], default=-1) + 1


# Whether `board` is mirrored as gen.py makes it: every tile's pearl range and every kelp edge equal to its mirror's,
# each portal id on exactly two edges of one orientation, both on the mirror line or both off it, whose mirrors are
# one pair, and the dragons in mirrored A/B pairs.
def symmetric(board) -> bool:
    if any(board.gaps.get(board.mirror(t)) != gap for t, gap in board.gaps.items()):
        return False
    ends: dict[int, list] = {}
    for e, kind in board.edges.items():
        other = board.edges.get(board.mirror_edge(e))
        if other is None or (kind < 0) != (other < 0):
            return False
        if kind >= 0:
            ends.setdefault(kind, []).append(e)
    for pid, pair in ends.items():
        if len(pair) != 2 or pair[0][0] != pair[1][0]:
            return False
        a, b = pair
        if (board.mirror_edge(a) == a) != (board.mirror_edge(b) == b):
            return False
        if board.edges[board.mirror_edge(a)] != board.edges[board.mirror_edge(b)]:
            return False
    if len(board.dragons) % 2:
        return False
    return all(b == [board.mirror(t) for t in a] for a, b in zip(board.dragons[::2], board.dragons[1::2]))


# The dragons on `board` that die whatever any bot does: too short to split (a split leaves both parts at least 2
# long, docs/unsw/splitting.md), with a start pocket (the tiles its head reaches with every dragon's tiles blocked,
# portals followed) that holds no loop, so a dragon that can't reverse walks into a dead end. No size cutoff: any
# loop-free pocket is a tree, however large. Escapes count as the rules allow: a pocket tile that steps into a body tile
# other than the head's own neck (that tile clears as its dragon's tail advances, docs/unsw/execution-order.md step 4),
# or one that spawns pearls (eating one grows the dragon long enough to split; map files place no starting pearls).
# Longer dragons can split and leave from their tail or middle, which the Sprint maps' trapped long starters must.
def trapped(gen, board) -> list:
    taken = {t for body in board.dragons for t in body}
    out = []
    for body in board.dragons:
        if len(body) >= 4:
            continue
        head, neck = body[0], body[1]
        seen, todo, links, escape = {head}, deque([head]), set(), False
        while todo and not escape:
            t = todo.popleft()
            escape |= board.gaps.get(t, (0, 0))[1] > 0
            for d in gen.DIRS:
                n = board.step(t, d)
                if n is None or n == t:
                    continue
                if n in taken and n != head:
                    escape |= (t, n) != (head, neck)
                    continue
                links.add(frozenset((t, n)))
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        if not escape and len(links) < len(seen):  # a connected graph with as many links as tiles has a loop
            out.append(body)
    return out


# Why `board` isn't a fair, playable map, or None: board size, symmetry, dragon count and lengths, bodies joined by open
# edges, no trapped dragon (absolute: no organiser or community map has one), and gen.near_ok (reach, overlaps, heads
# able to move) measured against `source`, the map it was made from (None: gen.py's own floor). A gen.py style's seed
# map is its own source: its style set its floor (macro's coiled starters are boxed in, as on Slithery Fight; a sealed
# portalrooms map's teams reach half the board, as on Portals).
def valid(gen, board, source=None) -> str | None:
    if not (SIDES[0] <= board.w <= SIDES[1] and SIDES[0] <= board.h <= SIDES[1]):
        return f"board {board.w}x{board.h} outside {SIDES}"
    if not board.dragons or len(board.dragons) // 2 > MAX_TEAM:
        return f"{len(board.dragons)} dragons"
    if not symmetric(board):
        return "not symmetric"
    for body in board.dragons:
        if len(body) < 2 or (len(body) > MAX_LENGTH and (source is None or len(body) > max(map(len, source.dragons)))):
            return f"a dragon of length {len(body)}"
        if any(not (0 <= x < board.w and 0 <= y < board.h) for x, y in body):
            return "a dragon off the board"
        for a, b in zip(body, body[1:]):
            if not any(board.plain_step(a, d) == b for d in gen.DIRS):
                return "a dragon's segments not joined by an open edge"
    if not any(gap[1] > 0 for gap in board.gaps.values()):
        return "no spawning tile"
    if trapped(gen, board):
        return "a dragon trapped in a loop-free start pocket"
    return None if gen.near_ok(board, source) else "gen.near_ok: reach, overlap or a boxed head"


# ------------------------------------------------------------------------------------------------- map shape

# The tiles a head can move to from each tile (portals followed), and the undirected moves between them keyed so each
# appears once: (tile, tile, the edges it crosses).
def moves(board) -> tuple[dict, list]:
    partner = {}
    for e, kind in board.edges.items():
        if kind >= 0:
            partner[e] = next(o for o, k in board.edges.items() if k == kind and o != e)
    neighbours: dict = {t: set() for t in board.all_tiles()}
    keys = set()
    for t in board.all_tiles():
        for d in ("N", "E", "S", "W"):
            u = board.step(t, d)
            if u is None or u == t:
                continue
            e = board.side(t, d)
            crossed = frozenset({e, partner[e]}) if e in partner else frozenset({e})
            neighbours[t].add(u)
            keys.add((min(t, u), max(t, u), crossed))
    return neighbours, sorted(keys, key=lambda k: (k[0], k[1], sorted(k[2])))


# Dead-end tiles: those a single move (a bridge of the move graph) cuts off from every dragon's starting tiles, on a
# side of at most a quarter of the board. A 1-wide corridor joining two open areas is not one (both its ends reach a
# start); a corridor or pocket leading nowhere is. pockets.py (research, 2026-09-27) also counts 2-wide doors; this
# counts 1-edge cuts only.
def dead_ends(board) -> set:
    _, links = moves(board)
    adjacent: dict = {t: [] for t in board.all_tiles()}
    for i, (a, b, _) in enumerate(links):
        adjacent[a].append((b, i))
        adjacent[b].append((a, i))
    bridges = find_bridges(adjacent)
    component: dict = {}
    for t in board.all_tiles():
        if t in component:
            continue
        component[t] = t
        todo = deque([t])
        while todo:
            a = todo.popleft()
            for b, i in adjacent[a]:
                if i not in bridges and b not in component:
                    component[b] = t
                    todo.append(b)
    starts = {t for body in board.dragons for t in body}
    tree: dict = {}
    for i in bridges:
        a, b, _ = links[i]
        tree.setdefault(component[a], []).append(component[b])
        tree.setdefault(component[b], []).append(component[a])
    size, has_start = Counter(component.values()), Counter(component[t] for t in starts)
    root = component[next(iter(sorted(starts)))] if starts else None
    if root is None:
        return set()
    # Subtree sums from the root, iteratively: parents first, then sizes and starts bottom up.
    parent, order, todo = {root: None}, [], [root]
    while todo:
        c = todo.pop()
        order.append(c)
        for n in tree.get(c, []):
            if n not in parent:
                parent[n] = c
                todo.append(n)
    below, starting = dict(size), dict(has_start)
    for c in reversed(order):
        if parent[c] is not None:
            below[parent[c]] = below.get(parent[c], 0) + below[c]
            starting[parent[c]] = starting.get(parent[c], 0) + starting.get(c, 0)
    # The bridge above c cuts off c's subtree: a dead end when no start is in it and it is small enough.
    area = board.w * board.h
    dead = {c for c in order if parent[c] is not None and not starting.get(c) and below[c] <= area // 4}
    return {t for t, c in component.items() if c in dead}


# The bridge ids of an undirected multigraph {node: [(neighbour, edge id)]} (Tarjan's lowlink, iteratively).
def find_bridges(adjacent: dict) -> set[int]:
    index, low, bridges, counter = {}, {}, set(), 0
    for root in adjacent:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack = [(root, None, iter(adjacent[root]))]
        while stack:
            node, via, edges = stack[-1]
            advanced = False
            for nxt, i in edges:
                if i == via:
                    continue
                if nxt in index:
                    low[node] = min(low[node], index[nxt])
                else:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append((nxt, i, iter(adjacent[nxt])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                if stack:
                    up = stack[-1][0]
                    low[up] = min(low[up], low[node])
                    if low[node] > index[up]:
                        bridges.add(via)
    return bridges


# Tiles of at most two exits: 1-wide corridors, and dead-end tips.
def corridors(board) -> set:
    neighbours, _ = moves(board)
    return {t for t, near in neighbours.items() if len(near) <= 2}


def fast_tiles(board) -> list:
    return [t for t, (lo, hi) in sorted(board.gaps.items()) if hi > 0 and (lo + hi) / 2 <= FAST_GAP]


# Edge-disjoint paths from tile set `src` to tile set `sink` along `neighbours` (the lessons inventory's halves_cut:
# the fewest moves that separate them), counted up to `cap`.
def min_cut(neighbours: dict, src: set, sink: set, cap: int = 12) -> int:
    flow: Counter = Counter()
    value = 0
    while value < cap:
        prev = {s: None for s in src}
        todo, hit = deque(src), None
        while todo and hit is None:
            a = todo.popleft()
            for b in neighbours[a]:
                if b not in prev and flow[(a, b)] - flow[(b, a)] < 1:
                    prev[b] = a
                    if b in sink:
                        hit = b
                        break
                    todo.append(b)
        if hit is None:
            break
        b = hit
        while prev[b] is not None:
            a = prev[b]
            if flow[(b, a)] > 0:
                flow[(b, a)] -= 1
            else:
                flow[(a, b)] += 1
            b = a
        value += 1
    return value


def reach(neighbours: dict, starts) -> set:
    seen, todo = set(starts), deque(starts)
    while todo:
        for u in neighbours[todo.popleft()]:
            if u not in seen:
                seen.add(u)
                todo.append(u)
    return seen


# A map's shape, as the lessons inventory (research, 2026-09-27: measure.py and flags.py) flagged our five worst ladder
# maps: {flag: bool} and the numbers behind them. Its definitions, ported: fast tiles have a mean gap of at most
# FAST_GAP; the border is the torus seam (x = 0 and y = 0 edges); dead ends are dead_ends' (1-edge cuts).
def features(board) -> dict:
    neighbours, _ = moves(board)
    plain = {t: {u for d in ("N", "E", "S", "W") if (u := board.plain_step(t, d)) is not None and u != t}
             for t in board.all_tiles()}
    a_bodies = {t for body in board.dragons[::2] for t in body}
    b_bodies = {t for body in board.dragons[1::2] for t in body}
    heads = [body[0] for body in board.dragons]
    area = board.w * board.h
    seam = [("h", x, 0) for x in range(board.w)] + [("v", 0, y) for y in range(board.h)]
    border = sum(board.edges.get(e) == -1 for e in seam) / len(seam)
    reached = reach(neighbours, heads)
    narrow = sum(len(neighbours[t]) <= 2 for t in reached) / max(1, len(reached))
    fast = [t for t in fast_tiles(board) if t in reached]
    by_plain = reach(plain, a_bodies)
    pockets = dead_ends(board)
    cut = min_cut(neighbours, a_bodies, b_bodies)
    numbers = {"border_kelp": round(border, 2), "halves_cut": cut, "narrow_share": round(narrow, 2),
               "fast_tiles": len(fast), "fast_in_corridor": sum(len(neighbours[t]) <= 2 for t in fast),
               "fast_dead_end": sum(t in pockets for t in fast), "longest_start": max(map(len, board.dragons)),
               "area": area, "portal_pairs": len({k for k in board.edges.values() if k >= 0})}
    flags = {"walled border": border >= 0.9,
             "chokepoint: halves cut by 1-5 moves": 1 <= cut <= 5,
             "teams meet only through portals": not any(body[0] in by_plain for body in board.dragons[1::2]),
             "fast tiles reached only through a portal": any(t not in by_plain for t in fast),
             "fast tiles in corridors (5+)": numbers["fast_in_corridor"] >= 5,
             "fast tiles in dead ends": numbers["fast_dead_end"] > 0,
             "maze (20%+ of tiles 1-wide)": narrow >= 0.2,
             "long start dragon (10+)": numbers["longest_start"] >= 10,
             "big (2400+ tiles)": area >= 2400}
    return {"flags": [name for name, on in flags.items() if on], "numbers": numbers}


# ------------------------------------------------------------------------------------------------- mutations
# Each takes (gen, board, rng), changes the board in place through its mirrored setters, and returns whether it changed
# anything; valid() judges the result.

def region(board, rng, low: int = 3) -> set:
    w, h = rng.randint(low, max(low, board.w // 2)), rng.randint(low, max(low, board.h // 2))
    x0, y0 = rng.randrange(board.w), rng.randrange(board.h)
    return {board.wrap(x0 + i, y0 + j) for i in range(w) for j in range(h)}


# Clears a random rectangle's kelp and lays a fresh random share (15-50%) of its edges as kelp.
def m_kelp_region(gen, board, rng) -> bool:
    before = dict(board.edges)
    blocked = gen.body_edges(board)
    edges = sorted({board.side(t, d) for t in region(board, rng) for d in ("N", "W")})
    for e in edges:
        if board.edges.get(e) == gen.KELP:
            board.open_edge(e)
    share = rng.uniform(0.15, 0.5)
    for e in edges:
        if e not in board.edges and e not in blocked and rng.random() < share:
            board.set_kelp(e)
    return board.edges != before


# A 1-wide corridor of 3-8 tiles walled on both sides: from a random tile, or (when the map has one) onward from a
# straight corridor already there, so corridors lengthen.
def m_corridor(gen, board, rng) -> bool:
    before = dict(board.edges)
    blocked = gen.body_edges(board)
    neighbours, _ = moves(board)
    straight = [(t, d) for t, near in sorted(neighbours.items()) if len(near) == 2
                for d, back in (("N", "S"), ("E", "W"))
                if board.step(t, d) in near and board.step(t, back) in near]
    if straight and rng.random() < 0.6:
        t, d = rng.choice(straight)
        d = d if rng.random() < 0.5 else {"N": "S", "E": "W"}[d]
    else:
        t, d = (rng.randrange(board.w), rng.randrange(board.h)), rng.choice("NESW")
    dx, dy = gen.DIRS[d]
    across = ("E", "W") if d in "NS" else ("N", "S")
    for i in range(rng.randint(3, 8)):
        cell = board.wrap(t[0] + dx * i, t[1] + dy * i)
        if board.edges.get(board.side(cell, d)) == gen.KELP:
            board.open_edge(board.side(cell, d))
        for s in across:
            e = board.side(cell, s)
            if e not in board.edges and e not in blocked:
                board.set_kelp(e)
    return board.edges != before


# A small pocket (2-3 tiles a side) walled all round, away from the dragons and its own mirror, but for one door
# (gen.add_door: sometimes at the end of a 1-wide corridor) when `door`; or None when none fits. Returns its tiles.
def carve_pocket(gen, board, rng, door: bool = True) -> set | None:
    bodies = {t for body in board.dragons for t in body}
    blocked = gen.body_edges(board)
    for _attempt in range(40):
        w, h, x0, y0 = rng.randint(2, 3), rng.randint(2, 3), rng.randrange(board.w), rng.randrange(board.h)
        pocket = {board.wrap(x0 + i, y0 + j) for i in range(w) for j in range(h)}
        mirrored = {board.mirror(t) for t in pocket}
        if pocket & mirrored or pocket & bodies or len(pocket) < 4:
            continue
        near_mirror = any(board.wrap(x + dx, y + dy) in mirrored for x, y in pocket for dx, dy in gen.DIRS.values())
        walls = [(t, d) for t in sorted(pocket) for d, (dx, dy) in gen.DIRS.items()
                 if board.wrap(t[0] + dx, t[1] + dy) not in pocket]
        touching = {board.side(t, d) for t, d in walls} | {e for e in board.edges if gen.inside(board, e, pocket)}
        if near_mirror or any(board.edges.get(e, -1) >= 0 or e in blocked for e in touching):
            continue
        for e in [e for e in board.edges if gen.inside(board, e, pocket)]:
            board.open_edge(e)
        for t, d in walls:
            board.set_kelp(board.side(t, d))
        if door:
            gen.add_door(board, rng, pocket, *rng.choice(walls))
        return pocket
    return None


# Moves some fast pearl tiles into dead ends (dead_ends; carving a pocket when the map has none): their pearl ranges go
# to dead-end tiles, and the tiles they left take the map's commonest range.
def m_pocket_food(gen, board, rng) -> bool:
    before = dict(board.gaps), dict(board.edges)
    bodies = {t for body in board.dragons for t in body}
    pockets = sorted(dead_ends(board) - bodies)
    if not pockets:
        if carve_pocket(gen, board, rng) is None:
            return False
        pockets = sorted(dead_ends(board) - bodies)
    fast = fast_tiles(board)
    ranges = [board.gaps[t] for t in fast] or [(1, rng.randint(5, FAST_GAP))]
    common = Counter(board.gaps.values()).most_common(1)[0][0]
    moved = rng.sample(fast, min(len(fast), rng.randint(1, max(1, len(pockets)))))
    for t in moved:
        board.set_gap(t, common)
    for t in rng.sample(pockets, min(len(pockets), max(1, len(moved)))):
        board.set_gap(t, rng.choice(ranges))
    return (board.gaps, board.edges) != before


def m_portal_add(gen, board, rng) -> bool:
    count = len(board.edges)
    board.next_portal = fresh_portal_id(board)
    gen.add_portals(board, rng, 1)
    return len(board.edges) > count


def m_portal_move(gen, board, rng) -> bool:
    before = dict(board.edges)
    gen.shift_portals(board, rng, rng.uniform(0.3, 1.0))
    return board.edges != before


# Fast pearls in a small pocket walled all round whose only way in is a portal: one wall edge of the pocket is a portal
# end, paired with a free edge of the same orientation elsewhere.
def m_food_behind_portal(gen, board, rng) -> bool:
    before = dict(board.gaps), dict(board.edges)
    pocket = carve_pocket(gen, board, rng, door=False)
    if pocket is None:
        return False
    walls = sorted(e for e in {board.side(t, d) for t in pocket for d in "NESW"}
                   if not gen.inside(board, e, pocket) and board.edges.get(e) == gen.KELP)
    blocked = gen.body_edges(board)
    for _attempt in range(100):
        e1 = rng.choice(walls)
        e2 = (e1[0], rng.randrange(board.w), rng.randrange(board.h))
        ends = {e1, e2, board.mirror_edge(e1), board.mirror_edge(e2)}
        if len(ends) == 4 and e2 not in board.edges and board.mirror_edge(e2) not in board.edges and e2 not in blocked:
            pid = fresh_portal_id(board)
            board.edges[e1] = board.edges[e2] = pid
            board.edges[board.mirror_edge(e1)] = board.edges[board.mirror_edge(e2)] = pid + 1
            break
    else:  # no way in: undo the sealed pocket
        board.gaps.clear()
        board.gaps.update(before[0])
        board.edges.clear()
        board.edges.update(before[1])
        return False
    gap = (rng.randint(1, 3), rng.randint(5, FAST_GAP))
    for t in pocket:
        board.set_gap(t, gap)
    return (board.gaps, board.edges) != before


def m_dragons_move(gen, board, rng) -> bool:
    before = [list(body) for body in board.dragons]
    gen.jitter_dragons(board, rng, 1.0)
    return board.dragons != before


# Lengthens (by 1-4) or shortens (by 1-3, to at least 2) one team A dragon at its tail, and its team B twin.
def m_dragon_length(gen, board, rng) -> bool:
    i = rng.randrange(0, len(board.dragons), 2)
    body = list(board.dragons[i])
    if rng.random() < 0.4 and len(body) > 2:
        body = body[:-rng.randint(1, min(3, len(body) - 2))]
    else:
        others = {t for j, b in enumerate(board.dragons) if j not in (i, i + 1) for t in b}
        for _ in range(rng.randint(1, 4)):
            if len(body) >= MAX_LENGTH:
                break
            options = [board.plain_step(body[-1], d) for d in rng.sample("NESW", 4)]
            options = [t for t in options if t is not None and t not in body and gen.ok_tile(board, t, body, others)]
            if not options:
                break
            body.append(options[0])
    if body == board.dragons[i]:
        return False
    board.dragons[i:i + 2] = [body, [board.mirror(t) for t in body]]
    return True


# Kelp along the torus seam (every x = 0 and y = 0 edge not between two segments of a dragon or a portal): the
# walled border of Devil, Stronghold, Trauma, Portals and Slithery Fight.
def m_wall_border(gen, board, rng) -> bool:
    before = dict(board.edges)
    blocked = gen.body_edges(board)
    for e in [("h", x, 0) for x in range(board.w)] + [("v", 0, y) for y in range(board.h)]:
        if e not in board.edges and e not in blocked:
            board.set_kelp(e)
    return board.edges != before


# Walls the board into its two mirrored halves (the mirror line and the seam across the mirrored axis), leaving 1-3
# gates in each wall: halves joined by only a few passable edges, as on our worst ladder maps.
def m_cut_halves(gen, board, rng) -> bool:
    before = dict(board.edges)
    blocked = gen.body_edges(board)
    vertical = board.sym == "y" or (board.sym == "xy" and rng.random() < 0.5)
    for line in (board.w // 2 if vertical else board.h // 2, 0):
        edges = [("v", line, y) for y in range(board.h)] if vertical else [("h", x, line) for x in range(board.w)]
        gates = set(rng.sample(edges, rng.randint(1, 3)))
        for e in edges:
            if e in gates or board.mirror_edge(e) in gates:
                if board.edges.get(e) == gen.KELP:
                    board.open_edge(e)
            elif e not in board.edges and e not in blocked:
                board.set_kelp(e)
    return board.edges != before


MUTATIONS = {"kelp_region": (m_kelp_region, 3), "corridor": (m_corridor, 3), "pocket_food": (m_pocket_food, 2),
             "portal_add": (m_portal_add, 1), "portal_move": (m_portal_move, 1),
             "food_behind_portal": (m_food_behind_portal, 1), "dragons_move": (m_dragons_move, 1),
             "dragon_length": (m_dragon_length, 1), "wall_border": (m_wall_border, 1),
             "cut_halves": (m_cut_halves, 1)}


# A mutant of `board`: one or two MUTATIONS applied to a copy, the first valid result that differs from the parent's
# text. Returns (board, the mutations' names), or None after `attempts` tries.
def mutate(gen, board, rng, attempts: int = 40):
    names, weights = list(MUTATIONS), [w for _, w in MUTATIONS.values()]
    parent = gen.render(board, "x")
    for _attempt in range(attempts):
        child = copy.deepcopy(board)
        ops = rng.choices(names, weights, k=rng.choice((1, 1, 2)))
        changed = [MUTATIONS[op][0](gen, child, rng) for op in ops]
        if any(changed) and valid(gen, child, board) is None and gen.render(child, "x") != parent:
            return child, ops
    return None


# A fresh small perturbation of `board`, as gen.near makes a variant of a ladder map: the whole torus moved by one of
# its symmetries, then gen.nudge (pearl rates, tile zones, a few kelp edges, portals and dragon starts), keeping only a
# valid result whose kelp, portals or dragons moved.
def perturb(gen, board, rng):
    for _attempt in range(100):
        child = gen.move_board(board, rng.choice(gen.motions(board)))
        layout = (dict(child.edges), [list(body) for body in child.dragons])
        gen.nudge(child, rng)
        if (child.edges, child.dragons) != layout and valid(gen, child, board) is None:
            return child
    return None


# ------------------------------------------------------------------------------------------------- seed pool

# Where seed map i comes from, dealt round-robin: every style gen.STYLES has, near variants of the community maps, and
# every KNOBS extreme.
def seed_sources(gen) -> list[tuple[str, str]]:
    return ([("style", style) for style in gen.STYLES] + [("community", "near")]
            + [("knob", knob) for knob in KNOBS])


def base_seed(champion: str) -> int:
    return int(hashlib.sha256(f"advmaps:{champion}".encode()).hexdigest()[:7], 16)


# Seed map `i` for `champion`: (map text, where it came from: "gen <style> <seed>", "community near <seed>" or
# "knob <knob> <seed>", which remake reads).
def seed_map(gen, champion: str, i: int) -> tuple[str, str]:
    sources = seed_sources(gen)
    kind, arg = sources[i % len(sources)]
    for retry in range(5):  # a seed that makes no valid map in gen's 100 attempts, or a trapped one: the next one
        seed = base_seed(champion) + i + 100_000 * retry
        origin = f"{'gen' if kind == 'style' else kind} {arg} {seed}"
        try:
            text = remake(gen, origin)
        except RuntimeError:
            continue
        if not trapped(gen, gen.parse(text)):  # gen's styles don't check it; its games would score as our losses
            return text, origin
    raise RuntimeError(f"seed map {i} ({kind} {arg}): no valid map in 5 seeds")


# The seed map `origin` (seed_map's) names, made again. Its index alone won't do: every style appended to gen.STYLES
# shifts seed_sources' round-robin, so a search running across a referee refresh would make another map under the
# same name (as deadend and fortress did).
def remake(gen, origin: str) -> str:
    kind, arg, seed = origin.split(" ")
    if kind == "gen":
        return gen.generate(int(seed), arg)
    if kind == "community":
        rng = random.Random(int(seed) * 7919 + 77)
        return gen.near(int(seed), int(seed), rng, gen.community_sources())
    return extreme(gen, int(seed), arg)


# gen.build with one knob pushed to its extreme, from gen.py's own steps: a kelp share of 30-45% (gen's normal is at
# most 25%), a maze, 8-16 portal sets, sparse or far farms, close starts (gen's odd quirks), 8-14-long starts, 5-8
# dragons a team, a walled border with halves joined by 1-3 gates, or fast pearls behind a portal.
def extreme(gen, seed: int, knob: str) -> str:
    rng = random.Random(f"advmaps:{knob}:{seed}")
    for _attempt in range(100):
        quirks = {knob} & set(gen.ODD_QUIRKS)
        w, h, sym = gen.pick_shape(rng, quirks)
        board = gen.Board(w, h, sym, quirks=quirks)
        gen.paint_tiles(board, rng, quirks)
        if knob == "maze":
            gen.lay_maze(board, rng)
        else:
            gen.lay_kelp(board, rng, rng.uniform(0.3, 0.45) if knob == "kelp" else rng.uniform(0.03, 0.25))
        gen.add_portals(board, rng, rng.randint(8, 16) if knob == "portals" else rng.choice([0, 0, 1, 2, 3]))
        gen.knock_holes(board, rng)
        if knob == "walled_halves":
            m_wall_border(gen, board, rng)
            m_cut_halves(gen, board, rng)
        count = rng.randint(5, 8) if knob == "many_starts" else rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
        if not gen.place_dragons(board, rng, count, "close_starts" in quirks):
            continue
        if knob == "long_starts":
            for i in range(0, len(board.dragons), 2):
                goal = rng.randint(8, 14)
                for _ in range(goal):
                    if len(board.dragons[i]) >= goal:
                        break
                    m_dragon_length_at(gen, board, i, rng)
        if knob == "portal_food":
            m_food_behind_portal(gen, board, rng)
        gen.add_farms(board, rng, "far_farms" in quirks, "sparse" in quirks)
        if valid(gen, board) is None:
            return gen.render(board, f"adv-{knob}-{seed}")
    raise RuntimeError(f"knob {knob} seed {seed}: no valid map in 100 attempts")


# Grows team A dragon `i` (and its twin) by one tile at its tail where a free tile allows.
def m_dragon_length_at(gen, board, i: int, rng) -> None:
    body = board.dragons[i]
    others = {t for j, b in enumerate(board.dragons) if j not in (i, i + 1) for t in b}
    for d in rng.sample("NESW", 4):
        t = board.plain_step(body[-1], d)
        if t is not None and t not in body and gen.ok_tile(board, t, body, others):
            grown = body + [t]
            board.dragons[i:i + 2] = [grown, [board.mirror(u) for u in grown]]
            return


# ------------------------------------------------------------------------------------------------- scoring

# Our margin in one game row (we are bot a, the opponent bot b): lower is nearer a loss. See the module docstring.
# `starts`, our starting dragons (write_rows); a row without it counts every win with NARROW or fewer left as narrow.
def margin(row: dict) -> float:
    if row["winner"] == "b":
        return 0.0
    if row["winner"] == "draw":
        return 0.5
    alive = row["a"]["alive"]
    quick = (500 - row["rounds"]) / 500 if row["end"] == "elimination" else 0.0
    if alive <= NARROW and alive < row.get("starts", NARROW + 1):
        return 1.0 + 0.3 * max(0, alive - 1) + 0.3 * quick
    return 2.0 + 0.5 * min(alive, 20) / 20 + 0.5 * quick


# A map's margin from its two game rows: the worse side's, the better side's adding at most 0.03 as a tie-break.
def map_margin(games: list[dict]) -> float:
    sides = sorted(margin(r) for r in games)
    return sides[0] + sides[-1] / 100


# Team A's starting dragons in a map file's text (team B's are their mirrors).
def starts(text: str) -> int:
    return sum(line.startswith("DRAGON 0 ") for line in text.splitlines())


# Whether a map counts as lost against `opponent`: against the starter, a loss or draw on either side; against a strong
# opponent (the strong pass, a regression map naming one), on both sides, since the SPRT rotation's opponents beat or
# draw us on one side of 20-57% of maps (1eae05f's shared-set baselines, 166 maps each: swarm 39, rammer 34, breeder
# 94) but on both sides of only 2.4-16% (4, 4 and 27), so only the second can single a map out.
def lost(games: list[dict], opponent: str = STARTER) -> bool:
    outcomes = [r["winner"] in ("b", "draw") for r in games]
    return any(outcomes) if opponent == STARTER else bool(outcomes) and all(outcomes)


# Whether a map's games are all in: both rows, or the one error row of a map that couldn't be played.
def finished(games: list[dict]) -> bool:
    return len(games) >= 2 or any("winner" not in g for g in games)


# Whether a map's two games both played and it counts as lost against `opponent` (lost).
def lost_map(games: list[dict], opponent: str = STARTER) -> bool:
    return len(games) == 2 and all("winner" in g for g in games) and lost(games, opponent)


def results(folder: Path) -> list[dict]:
    path = folder / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def listing(folder: Path) -> list[dict]:
    path = folder / "maps.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# The bot a map entry plays: the starter unless the entry names another (the strong pass, its perturbations, and a
# regression map whose README row names one).
def opponent_of(m: dict) -> str:
    return m.get("opponent") or STARTER


# {map name: its game rows}, error rows included (they have no "winner").
def by_map(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["name"], []).append(row)
    return out


# {map name: margin} for every map both of whose games played.
def scored(rows: list[dict]) -> dict[str, float]:
    return {name: map_margin(games) for name, games in by_map(rows).items()
            if len(games) == 2 and all("winner" in g for g in games)}


def search_games(maps: list[dict], rows: list[dict]) -> int:
    stages = {m["name"]: m["stage"] for m in maps}
    return sum(1 if "winner" in r else 2 for r in rows if stages.get(r["name"]) in SEARCH)


# Our deaths over `games` rows by cause, and the hitWall ones in a dead end or 1-wide corridor: "hitWall 12 (9 in a
# dead end or corridor), hitSelf 2". Games whose deaths weren't tallied are left out.
def death_text(games: list[dict]) -> str:
    causes, walls = Counter(), 0
    for row in games:
        causes.update(row.get("deaths") or {})
        walls += row.get("wall_dead_end") or 0
    if not causes:
        return "no deaths tallied"
    parts = [f"{cause} {n}" + (f" ({walls} in a dead end or corridor)" if cause == "hitWall" else "")
             for cause, n in causes.most_common()]
    return ", ".join(parts)


def side_text(row: dict) -> str:
    if "winner" not in row:
        return f"error: {row.get('game_error', '?')[:80]}"
    outcome = {"a": "won", "b": "lost", "draw": "drew"}[row["winner"]]
    return f"{row['a_side']} {outcome} r{row['rounds']} by {row['end']}, {row['a']['alive']} of ours left"


# ------------------------------------------------------------------------------------------------- the search

def write_map(folder: Path, entry: dict, text: str) -> dict:
    (folder / "maps").mkdir(parents=True, exist_ok=True)
    path = folder / "maps" / f"{entry['name']}.map"
    path.write_text(text)
    entry = {**entry, "path": str(path)}
    with (folder / "maps.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def adversarial_dir() -> Path:
    import driver
    return driver.REFEREE / "maps" / "adversarial"


# The maps to play next, generated now (files under folder/maps, lines in maps.jsonl), and why there are none when
# empty: "finds" (every opponent this call may search at settings["max_finds"], MAX_FINDS unless a run's settings
# replace DEFAULTS'), "budget", "climb limit" (a manual run's), or "no parents". In order: the regression maps not
# listed yet (regressions), perturbations of lost maps awaiting a verdict, seed maps, then (with settings["idle"], past
# GAMES_CAP) the strong pass against each of settings["strong"] (strong_pass), then a climb round. settings["games"] is
# the cap in force: GAMES_CAP, or IDLE_CAP past it. run plays every map listed before it calls this again, so each
# step sees the results of the one before. A map file that tidy deleted (a search that finished and was reopened,
# tick) is never read: a seed map is made again for the strong pass (remake, from its saved origin), any other is
# passed over. Perturbations fewer than a call's BATCH_MAPS come with the next of the rest (explore).
def propose(gen, folder: Path, champion: str, record: dict, settings: dict) -> tuple[list[dict], str | None]:
    if regress := regressions(folder):
        return regress, None
    maps, rows = listing(folder), results(folder)
    finds, played = record.get("finds") or {}, by_map(rows)
    strong = list(settings.get("strong") or ()) if settings.get("idle") else []
    found = Counter((f or {}).get("opponent", STARTER) for f in finds.values())
    open_ = [o for o in [STARTER, *strong] if found[o] < settings["max_finds"]]
    if not open_:
        return [], "finds"
    games = search_games(maps, rows)
    decided = set(finds) | set(record.get("flukes") or {})
    perturbed = {m["parent"] for m in maps if m["stage"] == "perturb"}
    judged = [o for o in open_ if o == STARTER or pass_played(maps, played, o)]  # a strong rate is known only then
    margins = scored(rows)
    pending = sorted((m for m in maps if m["stage"] in ("seed", "climb", "strong") and m["name"] not in decided
                      and opponent_of(m) in judged and lost_map(played.get(m["name"], []), opponent_of(m))
                      and Path(m["path"]).exists()),
                     key=lambda m: margins[m["name"]])
    waiting = Counter(opponent_of(m) for m in pending if m["name"] in perturbed)
    new = []
    for m in pending:
        opponent = opponent_of(m)
        if m["name"] in perturbed or found[opponent] + waiting[opponent] >= settings["max_finds"]:
            continue
        count = perturbations_for(maps, played, opponent)
        if not count:  # decide records it as a fluke, not perturbed
            continue
        if games + 2 * count > settings["games"] + PERTURB_RESERVE:
            break
        source = gen.parse(Path(m["path"]).read_text())
        rng = random.Random(f"{champion}:{m['name']}:perturb")
        for k in range(count):
            child, name = perturb(gen, source, rng), f"{m['name']}-p{k}"
            entry = {"name": name, "stage": "perturb", "parent": m["name"], "origin": f"perturbation of {m['name']}",
                     "opponent": opponent}
            if child is not None:
                new.append(write_map(folder, entry, gen.render(child, f"adv_{champion[:7]}_{name}")))
            else:  # listed unmade, so the verdict still counts it (as not reproduced) and nothing retries it
                unmade(folder, entry)
        waiting[opponent] += 1
        games += 2 * count
    if new and len(new) < BATCH_MAPS:  # one lost map's perturbations fill a sixth of a call: explore beside them
        more, _ = explore(gen, folder, champion, settings, maps, played, finds, open_, strong, games, margins, decided)
        return new + more, None
    if new or sum(waiting.values()):  # verdicts first: a find may end the search
        return new, None
    return explore(gen, folder, champion, settings, maps, played, finds, open_, strong, games, margins, decided)


# propose's maps past the perturbations: seed maps, the strong pass, then a climb round, with `games` search games
# played or listed. Beside a lost map's perturbations too (2026-09-30: the search playing alone was 78% idle for 57
# minutes, a perturbation round being one map's 12 games a call): the few maps a find would have made unneeded cost
# less than a call's idle slots.
def explore(gen, folder: Path, champion: str, settings: dict, maps: list[dict], played: dict, finds: dict,
            open_: list[str], strong: list[str], games: int, margins: dict[str, float],
            decided: set) -> tuple[list[dict], str | None]:
    new: list[dict] = []
    room = (settings["games"] - games) // 2
    if room <= 0:
        return [], "budget"
    seeds = [m for m in maps if m["stage"] == "seed"]
    if STARTER in open_ and len(seeds) < settings["seed_maps"]:
        for i in range(len(seeds), min(settings["seed_maps"], len(seeds) + min(BATCH_MAPS, room))):
            text, origin = seed_map(gen, champion, i)
            new.append(write_map(folder, {"name": f"s{i:03d}", "stage": "seed", "origin": origin}, text))
        return new, None
    for opponent in strong:
        if opponent in open_ and (new := strong_pass(gen, folder, champion, opponent, maps, played, margins, room)):
            return new, None
    if STARTER not in open_:
        return [], "finds"
    climbs = [m for m in maps if m["stage"] == "climb"]
    rounds = max([m["round"] for m in climbs], default=0)
    if settings.get("climb_rounds") is not None and rounds >= settings["climb_rounds"]:
        return [], "climb limit"
    tries = Counter(m.get("parent") for m in climbs)
    candidates = [m for m in maps if m["stage"] in ("seed", "climb") and m["name"] in margins
                  and m["name"] not in finds and not (lost_map(played[m["name"]]) and m["name"] not in decided)
                  and tries[m["name"]] < PARENT_TRIES and Path(m["path"]).exists()]
    parents = sorted(candidates, key=lambda m: (margins[m["name"]], m["name"]))[:settings["keep"]]
    mutants = [parent for parent in parents
               for _ in range(min(settings.get("mutants", MUTANTS), PARENT_TRIES - tries[parent["name"]]))]
    for number, parent in enumerate(mutants[:room], start=len(climbs)):
        name = f"c{number:03d}"
        mutated = mutate(gen, gen.parse(Path(parent["path"]).read_text()), random.Random(f"{champion}:{name}"))
        if mutated is not None:
            child, ops = mutated
            new.append(write_map(folder, {"name": name, "stage": "climb", "parent": parent["name"], "round": rounds + 1,
                                          "mutations": ops, "origin": f"{'+'.join(ops)} of {parent['name']}"},
                                 gen.render(child, f"adv_{champion[:7]}_{name}")))
        else:  # counts as a try, so a map no mutation can change stops being a parent
            unmade(folder, {"name": name, "stage": "climb", "parent": parent["name"], "round": rounds + 1})
    return (new, None) if new or parents else ([], "no parents")


# Lists `entry` in maps.jsonl with no map file ("unmade"): a perturbation or mutant that couldn't be made, or a part of
# the strong pass skipped (its "skipped" says why), which nothing plays or retries.
def unmade(folder: Path, entry: dict) -> None:
    with (folder / "maps.jsonl").open("a") as handle:
        handle.write(json.dumps({**entry, "unmade": True}) + "\n")


# The regression maps not listed yet against the opponent their README row names (new_regressions,
# regression_opponents), listed to play it: as r-<map>, or r-<map>-vs-<opponent> when the map was listed before against
# another. One naming an opponent referee/gauntlet.toml has no entry for gets one error row instead of games, which
# decide alerts on once (as a map that couldn't be played), and the others still play; its row fixed later, it plays.
def regressions(folder: Path) -> list[dict]:
    import driver
    if not (new := new_regressions(folder)):
        return []
    opponents = regression_opponents(adversarial_dir())
    known = driver.gauntlet_specs({opponents.get(p.name, STARTER) for p in new})
    taken = {m["name"] for m in listing(folder)}
    out = []
    for p in new:
        opponent = opponents.get(p.name, STARTER)
        name = f"r-{p.stem}"
        if name in taken:  # the opponent in a file name: any character but these made safe
            name += "-vs-" + re.sub(r"[^A-Za-z0-9_-]", "_", opponent)
        entry = write_map(folder, {"name": name, "stage": "regress", "origin": f"maps/adversarial/{p.name}",
                                   "opponent": opponent}, p.read_text())
        if opponent in known:
            out.append(entry)
            continue
        with (folder / "results.jsonl").open("a") as handle:
            handle.write(json.dumps({"name": entry["name"], "stage": "regress",
                                     "game_error": f"README opponent {opponent!r} is not in referee/gauntlet.toml"})
                         + "\n")
    return out


# The strong pass's next maps against `opponent` (copies named "<opponent>-<map>"), or [] once it is listed in full or
# skipped. First copies of the seed pool (marked "pool": find_chance's base rate), started only while the budget has
# room for them and STRONG_WORST more maps, since a pass cut short would judge the opponent on part of its pool. Once
# those are played, copies of the STRONG_WORST worst climb maps the starter search scored (`margins`), unless not even
# STRONG_PERTURBATIONS of STRONG_PERTURBATIONS lost could make one a find at the opponent's own loss rate on the pool
# (perturbations_for): those games could find nothing. A skipped pass or skipped worst maps leave an unmade line saying
# why ("skipped", which stats_lines lists), so the pass counts as listed in full.
def strong_pass(gen, folder: Path, champion: str, opponent: str, maps: list[dict], played: dict,
                margins: dict[str, float], room: int) -> list[dict]:
    mine = [m for m in maps if m["stage"] == "strong" and opponent_of(m) == opponent]
    if any(not m.get("pool") for m in mine):
        return []
    entry = {"stage": "strong", "opponent": opponent}
    if not mine:
        pool = [m for m in maps if m["stage"] == "seed" and not m.get("unmade")]
        if len(pool) + STRONG_WORST <= room:
            return strong_copies(gen, folder, champion, opponent, pool, True)
        unmade(folder, {**entry, "name": f"{opponent}-pass", "skipped": f"not played: {2 * room} games of the budget "
                        f"left, and its pass takes up to {2 * (len(pool) + STRONG_WORST)}"})
        return []
    worst = sorted((m for m in maps if m["stage"] == "climb" and m["name"] in margins and Path(m["path"]).exists()),
                   key=lambda m: (margins[m["name"]], m["name"]))[:STRONG_WORST]
    if worst and perturbations_for(maps, played, opponent, len(worst)):
        return strong_copies(gen, folder, champion, opponent, worst, False)
    n = STRONG_PERTURBATIONS
    p, rate, looks = find_chance(maps, played, n, n, opponent, len(worst))
    unmade(folder, {**entry, "name": f"{opponent}-worst", "skipped": "the worst climb maps not played: " + (
        f"even {n} of {n} perturbations lost would be p {p:.2g} at its loss rate on the seed pool, at most {rate:.1%}, "
        f"over {looks} maps" if worst else "none scored")})
    return []


# Copies of `sources` against `opponent` for the strong pass ("pool" marks the seed pool's). A seed map whose file tidy
# deleted is made again from its saved origin (remake).
def strong_copies(gen, folder: Path, champion: str, opponent: str, sources: list[dict], pool: bool) -> list[dict]:
    out = []
    for m in sources:
        if Path(m["path"]).exists():
            text, origin = Path(m["path"]).read_text(), m["origin"]
        else:
            text, origin = remake(gen, m["origin"]), m["origin"] + ", made again"
        entry = {"name": f"{opponent}-{m['name']}", "stage": "strong", "opponent": opponent, "parent": m["name"],
                 "pool": pool, "origin": f"{m['name']} ({origin}) against {opponent}"}
        out.append(write_map(folder, entry, text))
    return out


# Whether the strong pass against `opponent` is listed in full (its pool and its worst maps, or the line saying they
# were skipped) and every map of it played (or errored), so its loss rate on the seed pool is known before any of its
# lost maps is judged.
def pass_played(maps: list[dict], played: dict, opponent: str) -> bool:
    mine = [m for m in maps if m["stage"] == "strong" and opponent_of(m) == opponent]
    return (any(m.get("pool") for m in mine) and any(not m.get("pool") for m in mine)
            and all(finished(played.get(m["name"], [])) for m in mine if not m.get("unmade")))


# The maps in maps/adversarial/ that this search hasn't listed yet against the opponent their README row now names:
# all of them at its start, then any committed (and refreshed into the referee checkout) while it runs or after it
# finished, or whose row names another opponent since (an unknown name fixed), which tick then reopens it for.
def new_regressions(folder: Path) -> list[Path]:
    listed = {(m["origin"], opponent_of(m)) for m in listing(folder) if m["stage"] == "regress"}
    opponents = regression_opponents(adversarial_dir())
    return [p for p in sorted(adversarial_dir().glob("*.map"))
            if (f"maps/adversarial/{p.name}", opponents.get(p.name, STARTER)) not in listed]


# {map file: opponent} from `folder`'s README.md table: a row whose first cell is a .map file and whose Opponent cell
# names a referee/gauntlet.toml entry plays that entry; every other map, and every map of a table without an Opponent
# column, plays the starter. A name with no entry there is an error row for its map until the row changes
# (regressions).
def regression_opponents(folder: Path) -> dict[str, str]:
    path = folder / "README.md"
    header, out = None, {}
    for line in path.read_text().splitlines() if path.exists() else []:
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
        elif "opponent" in header and cells[0].endswith(".map") and len(cells) > header.index("opponent"):
            out[cells[0]] = cells[header.index("opponent")] or STARTER
    return out


# Maps listed but not played yet, in listing order (a paused batch's rest, first after a restart).
def unplayed(maps: list[dict], rows: list[dict]) -> list[dict]:
    played = by_map(rows)
    return [m for m in maps if not m.get("unmade") and m["name"] not in played]


# Who `opponent` is in an alert line: "the starter", else its gauntlet name.
def who(opponent: str) -> str:
    return "the starter" if opponent == STARTER else opponent


# The champions promoted before `champion`, newest first: the commits of the v* tags (driver.promote tags each
# champion once, in promotion order) below its own, or [] for a commit no v* tag names.
def earlier_champions(champion: str, repo: Path) -> list[str]:
    done = subprocess.run(["git", "for-each-ref", "refs/tags/v*", "--format=%(refname:short) %(objectname) "
                           "%(*objectname)"], cwd=repo, capture_output=True, text=True)
    tags = {}
    for line in done.stdout.splitlines() if done.returncode == 0 else []:
        name, *commits = line.split()
        if name[1:].isdigit() and commits:
            tags[int(name[1:])] = commits[-1]  # an annotated tag's commit, else the lightweight tag's own
    mine = [version for version, commit in tags.items() if commit == champion]
    return [tags[v] for v in sorted(tags, reverse=True) if v < min(mine)] if mine else []


# (champion, lost) for the newest earlier champion whose search finished regression map `origin` against `opponent`
# (its folder's listing and results, which prune keeps), lost as `lost` counts it (a map it couldn't play isn't
# lost), or None when none did: a map new to maps/adversarial/, or none of their folders left.
def earlier_record(state: Path, champion: str, origin: str, opponent: str) -> tuple[str, bool] | None:
    import driver
    for earlier in earlier_champions(champion, driver.REPO):
        folder = folder_of(state, earlier)
        if not folder.is_dir():
            continue
        played = by_map(results(folder))
        mine = [played.get(m["name"], []) for m in listing(folder)
                if m["stage"] == "regress" and m.get("origin") == origin and opponent_of(m) == opponent]
        if done := [games for games in mine if finished(games)]:
            return earlier, all("winner" in g for g in done[-1]) and lost(done[-1], opponent)
    return None


# Records verdicts: each regression map's record (and an ADVMAPS REGRESSION alert, once a map, when it counts as lost
# against its opponent, `lost`, unless the newest earlier champion that played it lost it too, earlier_record: every
# champion lost portal_labyrinth_85163986 from v0001, so each new one alerted on it again; or when it couldn't be
# played), and each lost seed, climb or strong map whose perturbations have all played: a find (REPRODUCE of them
# lost, and against a strong opponent also find_chance at most chance.TRIGGER_P) or a fluke. A strong map lost once
# its opponent's pass is in, where not even STRONG_PERTURBATIONS perturbations all lost could be a find at that
# opponent's own loss rate (perturbations_for), is a fluke unperturbed. Changes `record` in place and returns the
# lines for the run log, alerted or not.
def decide(state: Path, record: dict, gen, folder: Path) -> list[str]:
    maps, rows = listing(folder), results(folder)
    played, champion, lines = by_map(rows), record["champion"], []
    regress = record.setdefault("regress", {})
    for m in maps:
        games, opponent = played.get(m["name"], []), opponent_of(m)
        if m["stage"] != "regress" or not finished(games) or m["name"] in regress:
            continue
        ok = all("winner" in g for g in games)
        regress[m["name"]] = {"lost": ok and lost(games, opponent), "sides": [side_text(g) for g in games]}
        if not ok or lost(games, opponent):
            how = ("could not play" if not ok else "lost or drew" if opponent == STARTER else "lost or drew both sides")
            line = (f"ADVMAPS REGRESSION: champion {champion[:7]} {how} against {who(opponent)} on {m['origin']} "
                    f"({'; '.join(side_text(g) for g in games)}); our deaths: {death_text(games)}. See "
                    f"{folder / 'results.jsonl'}")
            before = earlier_record(state, champion, m["origin"], opponent) if ok else None
            if before and before[1]:
                regress[m["name"]]["lost_before"] = before[0]
                line = f"{line} (not alerted: {before[0][:7]} lost it too)"
            else:
                meta.alert(state, line)
            lines.append(line)
    finds, flukes = record.setdefault("finds", {}), record.setdefault("flukes", {})
    children: dict[str, list[dict]] = {}
    for m in maps:
        if m["stage"] == "perturb":
            children.setdefault(m["parent"], []).append(m)
    for m in maps:
        name, opponent = m["name"], opponent_of(m)
        kids = children.get(name, [])
        if (m["stage"] == "strong" and not kids and name not in finds and name not in flukes
                and lost_map(played.get(name, []), opponent) and pass_played(maps, played, opponent)):
            if not perturbations_for(maps, played, opponent):
                n = STRONG_PERTURBATIONS
                p, rate, looks = find_chance(maps, played, n, n, opponent)
                flukes[name] = (f"not perturbed: even {n} of {n} lost would be p {p:.2g} at {opponent}'s own loss "
                                f"rate on the seed pool, at most {rate:.1%}, over {looks} maps")
            continue
        made = [k for k in kids if not k.get("unmade")]
        if name in finds or name in flukes or not kids or any(not finished(played.get(k["name"], [])) for k in made):
            continue
        again = [k for k in made if lost_map(played[k["name"]], opponent)]
        p, rate, looks = find_chance(maps, played, len(again), len(kids), opponent)
        if len(again) < REPRODUCE or (opponent != STARTER and p > chance.TRIGGER_P):
            flukes[name] = f"lost on {len(again)} of {len(kids)} perturbations" + (
                "" if len(again) < REPRODUCE else f", p {p:.2g} at {opponent}'s own loss rate: no worse than usual")
            for side in "AB":
                (folder / "kept" / f"{name}-a{side}.replay").unlink(missing_ok=True)
            for k in made:
                for side in "AB":
                    (folder / "kept" / f"{k['name']}-a{side}.replay").unlink(missing_ok=True)
            continue
        found = finds[name] = keep_find(gen, folder, m, made, played)
        found.update(p=p, reproduced=f"{len(again)} of {len(kids)}")
        pool = "the seed pool's loss rate" if opponent == STARTER else f"{opponent}'s own loss rate on the seed pool"
        how = "lost or drew" if opponent == STARTER else "lost or drew both sides"
        line = (f"ADVMAPS FIND: champion {champion[:7]} {how} against {who(opponent)} on {name} ({m['origin']}; "
                f"{'; '.join(side_text(g) for g in played[name])}), and on {len(again)} of {len(kids)} perturbations "
                f"(p {p:.3g}: luck alone at {pool}, at most {rate:.1%}, over the {looks} maps searched against "
                f"{who(opponent)}, times the {groups()} opponents a search plays); our deaths: {found['deaths']}; "
                f"shape: {', '.join(found['features']['flags']) or 'none flagged'}. Proposed at {found['folder']}: "
                f"judge it, and commit the ones kept to maps/adversarial/ with a README line")
        if p <= chance.TRIGGER_P:  # MISSES 34: an alert only when luck alone rarely gives it
            meta.alert(state, line)
        lines.append(line)
    return lines


# The opponents a search can play, which find_chance corrects over (Bonferroni over groups): the starter and each of
# driver.ROTATION. Constant, so a search's first finds are judged as its last are.
def groups() -> int:
    import driver
    return 1 + len(driver.ROTATION)


# How likely luck alone makes a lost map a find: the chance that `again` or more of `tried` fresh perturbations are lost
# (lost_map: either side against the starter, both against a strong opponent) if each is at `opponent`'s own loss rate
# on the seed pool (the upper end of its 95% interval, chance.rate_ci: the starter's seed maps, or the strong pass's
# maps marked "pool"), times the maps searched against it (`extra` more: maps about to be listed) and times groups()
# (Bonferroni: any map of any opponent could have been the one). So each opponent's finds are luck alone at most
# chance.TRIGGER_P / groups() of the time, and a search's at most chance.TRIGGER_P. Returns (p, that rate, those maps).
def find_chance(maps: list[dict], played: dict, again: int, tried: int, opponent: str = STARTER,
                extra: int = 0) -> tuple[float, float, int]:
    mine = [m for m in maps if opponent_of(m) == opponent]
    pool = [played[m["name"]] for m in mine if (m["stage"] == "seed" or m.get("pool"))
            and len(played.get(m["name"], [])) == 2 and all("winner" in g for g in played[m["name"]])]
    rate = chance.rate_ci(sum(lost_map(g, opponent) for g in pool), len(pool))[1]
    tail = sum(math.comb(tried, k) * rate ** k * (1 - rate) ** (tried - k) for k in range(again, tried + 1))
    looks = extra + sum(1 for m in mine if m["stage"] in ("seed", "climb", "strong")
                        and len(played.get(m["name"], [])) == 2)
    return min(1.0, groups() * max(1, looks) * tail), rate, looks


# The perturbations a lost map against `opponent` gets: PERTURBATIONS against the starter; against a strong opponent
# STRONG_PERTURBATIONS when all of them lost could be a find (find_chance at most chance.TRIGGER_P, `extra` as there),
# else 0: no find is possible at that opponent's own loss rate. Ten rather than six: at every pool loss rate they find a
# map at a lower true loss rate (at 2 of 60 pool maps lost, 6 of 10 is a find, a coin flip for a map lost 55% of the
# time, where six needed 5 of 6, a coin flip at 74%), and at 9-19 of 60 six can't be a find at all.
def perturbations_for(maps: list[dict], played: dict, opponent: str, extra: int = 0) -> int:
    if opponent == STARTER:
        return PERTURBATIONS
    n = STRONG_PERTURBATIONS
    return n if find_chance(maps, played, n, n, opponent, extra)[0] <= chance.TRIGGER_P else 0


# Copies a find into folder/finds/<name>/: its map, its perturbations, find.json (their results, our deaths, the
# map's features, the opponent) and up to KEEP_REPLAYS replays (its own games, then lost perturbation games; none
# against a strong opponent, whose games keep none). Returns its summary.
def keep_find(gen, folder: Path, m: dict, kids: list[dict], played: dict) -> dict:
    out = folder / "finds" / m["name"]
    (out / "replays").mkdir(parents=True, exist_ok=True)
    shutil.copy2(m["path"], out / f"{m['name']}.map")
    for k in kids:
        shutil.copy2(k["path"], out / f"{k['name']}.map")
    games = played[m["name"]] + [g for k in kids for g in played[k["name"]]]
    kept = 0
    for name in [m["name"]] + [k["name"] for k in kids]:
        for side in "AB":
            source = folder / "kept" / f"{name}-a{side}.replay"
            if source.exists() and kept < KEEP_REPLAYS:
                shutil.move(str(source), out / "replays" / source.name)
                kept += 1
            else:
                source.unlink(missing_ok=True)
    summary = {"map": m["name"], "origin": m["origin"], "opponent": opponent_of(m), "folder": str(out),
               "deaths": death_text(games), "sides": [side_text(g) for g in played[m["name"]]],
               "perturbations": {k["name"]: [side_text(g) for g in played[k["name"]]] for k in kids},
               "features": features(gen.parse(Path(m["path"]).read_text()))}
    (out / "find.json").write_text(json.dumps(summary, indent=1) + "\n")
    return {key: summary[key] for key in ("origin", "opponent", "folder", "deaths", "features")}


# Replaces `record`, the search of an older champion, by a fresh one for `champion`: a search cut short is tidied
# like a finished one, and every champion's folder but the new one's and the replaced one's loses what it no longer
# needs (prune). Returns the new record.
def retire(state: Path, record: dict, champion: str) -> dict:
    if record.get("champion") and not record.get("finished_at"):
        tidy(folder_of(state, record["champion"]), record)
    prune(state, keep={champion, record.get("champion")})
    return {"champion": champion}


# Every champion folder under <loop home>/advmaps/ but those of the champions in `keep` (the current one and the one
# before it, whose finds the orchestrator may still be judging) keeps only its results, its listing, run.log and its
# finds' maps and find.json: its bots, batch and kept replays, map files and the finds' replays go.
def prune(state: Path, keep: set) -> None:
    root = state / "advmaps"
    for folder in [f for f in root.iterdir() if f.is_dir() and f.name not in keep] if root.exists() else []:
        for name in ("bots", "replays", "kept", "maps"):
            shutil.rmtree(folder / name, ignore_errors=True)
        for replays in folder.glob("finds/*/replays"):
            shutil.rmtree(replays, ignore_errors=True)


# The end of a search: deletes built bots, batch and kept replays, and every map file but the finds', the KEEP worst
# and, while `base_done` isn't set (the idle stages not played yet, which tick reopens it for), the STRONG_WORST worst
# climb maps, which the strong pass copies (strong_pass reads only files still there).
def tidy(folder: Path, record: dict) -> None:
    for name in ("bots", "replays", "kept"):
        shutil.rmtree(folder / name, ignore_errors=True)
    margins = scored(results(folder))
    maps = listing(folder)
    stages = {m["name"]: m["stage"] for m in maps}
    order = sorted(margins, key=lambda n: (margins[n], n))
    worst = [n for n in order if stages.get(n) in ("seed", "climb")][:KEEP]
    if not record.get("base_done"):
        worst += [n for n in order if stages.get(n) == "climb"][:STRONG_WORST]
    keep = set(record.get("finds") or {}) | set(worst)
    for m in maps:
        if m.get("path") and m["name"] not in keep and m["stage"] != "regress":
            Path(m["path"]).unlink(missing_ok=True)


# The upkeep tick's step, as sweep.tick: notes a run that died without finishing, then starts one in the background
# when the champion has no finished search, the machines are idle (sweep.busy is None), no
# failure is under RETRY_S old and, once its first GAMES_CAP games are played (`base_done`), nothing else wants the
# machines (wanted) or a map was committed to maps/adversarial/ since. A finished search is reopened for such a map, or,
# when it finished without `base_done` (before its idle stages existed, or a run by hand's), for those stages once
# nothing else wants the machines. Records `idle_at` whenever one could start but for a recent failure or wanted(), for
# health.py, and keeps a paused search's reason current (sweep.repause). Returns a line to log, or None.
def tick(repo: Path, state: Path, now: float | None = None) -> str | None:
    import sweep
    now = time.time() if now is None else now
    meta.reap()  # a run that finished clears its own pid, so only this reaps it
    if not (state / "ideas.jsonl").exists():
        return None
    with meta.locked(state / "advmaps"):
        record = read(state)
        if pid := record.get("pid"):
            if meta.alive(pid):  # reaping it if this process launched it
                return None  # health.py alerts when it stops making progress
            record = failure(record, "the run process exited without finishing; see its run.log", now)
            save(state, record)
        champion = meta.champion_commit(repo)
        if not champion:
            return None
        if record.get("champion") != champion:
            record = retire(state, record, champion)
            save(state, record)  # before busy(), which can raise: health.py then sees the step ran for this champion
        if record.get("finished_at"):
            if not new_regressions(folder_of(state, champion)) and (record.get("base_done")
                                                                   or wanted(state, repo, champion)):
                return None
            record.pop("finished_at")  # a map committed to maps/adversarial/ since, or the idle stages still to play
            save(state, record)
        if reason := sweep.busy(repo):
            sweep.repause(record, reason, now)
            save(state, record)
            return None
        record["idle_at"] = now
        if now - record.get("failed_at", 0) < RETRY_S:
            save(state, record)
            return None
        if record.get("base_done") and not new_regressions(folder_of(state, champion)) \
                and (reason := wanted(state, repo, champion)):
            sweep.repause(record, idle_pause(reason), now)
            save(state, record)
            return None
        record.update(attempt_at=now, progress_at=now, paused=None)
        save(state, record)  # before anything that can fail, so a failure is retried after RETRY_S
        try:
            record["pid"] = start(repo, state, record, [])
        except Exception as error:
            save(state, failure(record, f"could not start the run: {type(error).__name__}: {error}", now))
            return f"adversarial maps: could not start the run: {error}"
        record.setdefault("started_at", now)
        save(state, record)
        return f"adversarial-map search of {champion[:7]} launched in the background: {folder_of(state, champion)}"


# Freezes the toolchain in `record` and launches the background run with `extra` arguments. Returns its pid.
def start(repo: Path, state: Path, record: dict, extra: list[str]) -> int:
    import maintenance
    record.setdefault("toolchain", maintenance.toolchain(state))  # frozen for the search, like an experiment's
    folder = folder_of(state, record["champion"])
    folder.mkdir(parents=True, exist_ok=True)
    save(state, record)
    return launch(repo, state, folder, extra)


def launch(repo: Path, state: Path, folder: Path, extra: list[str]) -> int:
    return meta.spawn([sys.executable, str(Path(__file__).resolve()), "run", "--repo", str(repo), "--state",
                       str(state), *extra], folder, folder / "run.log")


def failure(record: dict, error: str, now: float) -> dict:
    return {**record, "pid": None, "error": error[-1200:], "failed_at": now, "failures": record.get("failures", 0) + 1}


# Rewrites advmaps.json's record for `champion` with `changes`, unless a newer champion's search has replaced it.
def update(state: Path, champion: str, **changes) -> dict:
    with meta.locked(state / "advmaps"):
        record = read(state)
        if record.get("champion") == champion:
            record.update(changes)
            save(state, record)
        return record


# A run's settings: "games" the first cap (GAMES_CAP), "idle_games" the cap past it (IDLE_CAP; no more than "games"
# turns the idle stages off), "strong" the strong pass's opponents (None: driver.ROTATION).
DEFAULTS = {"seed_maps": SEED_MAPS, "keep": KEEP, "games": GAMES_CAP, "idle_games": IDLE_CAP, "max_finds": MAX_FINDS,
            "climb_rounds": None, "strong": None, "beside": False, "max_load": None}


# Why the search must not play now, or None: sweep.busy's reasons (a stop marker, an evaluation playing on the
# hosts, the champion moved), and with `idle` (the state folder, for games past GAMES_CAP) wanted()'s. `beside` (a run
# by hand beside evaluations, at the late screen's share) stops only for a stop marker or a new champion.
def halt(repo: Path, champion: str, beside: bool = False, idle: Path | None = None) -> str | None:
    import driver
    import sweep
    if not beside:
        return wanted(idle, repo, champion) if idle else sweep.busy(repo, champion)
    if driver.stop_requested():
        return "a stop marker is set"
    return "the champion moved" if meta.champion_commit(repo) != champion else None


# Why the search must not play past its first GAMES_CAP games now, or None: sweep.busy's reasons. A tune playing its
# games doesn't hold it (until 2026-09-27 it did), nor since 2026-09-29 a late screen or an official-map report (each
# keeps its half of a host while it asks for slots, native.host_share), nor since 2026-09-28 the variant sweep or the
# baseline prewarm unfinished: those games play at the spare share (driver.referee's
# yielding="spare", native.spare_share), which takes only the slots the other idle fillers leave and gives them back
# within a game once one asks, so the wait for both to finish only left what they didn't use idle (28 Sep 01:20-01:29
# the prewarm alone kept about 20 of 56 slots busy while this waited, its reason naming the sweep, done at 00:03).
def wanted(state: Path, repo: Path, champion: str) -> str | None:
    import sweep
    return sweep.busy(repo, champion)


# advmaps.json's `paused` when wanted() holds the idle stages back for `reason`.
def idle_pause(reason: str, games: int = GAMES_CAP) -> str:
    return f"past the first {games} games, idle machines only: {reason}"


# The detached run `tick` launches: records verdicts, then plays the next maps (propose) BATCH_MAPS a referee call
# (IDLE_BATCH_MAPS in the idle stages), opponents mixed in a call (until 2026-09-29 one a call), until the search ends (finished_at, then tidy), halt() says to stop (paused, resumed by a later
# tick; an evaluation on the hosts is waited out in place, sweep.wait_idle), or a manual run's climb limit is reached
# (paused). When the first cap's work is done ("budget", "finds" or "no parents") it records `base_done` and goes on to
# the idle stages (propose with settings["idle"] and the IDLE_CAP budget) only while wanted() is None, waiting in place
# for it. `settings` overrides DEFAULTS for a run by hand: with
# "beside", its games play beside evaluations as the late screen's do (driver.referee's yielding=True) instead of on
# idle machines only, and it ends at the first cap.
def run(repo: Path, state: Path, settings: dict | None = None) -> None:
    import driver
    import sweep
    settings = {**DEFAULTS, **(settings or {})}
    settings["strong"] = driver.ROTATION if settings["strong"] is None else settings["strong"]
    extend = settings["idle_games"] > settings["games"] and not settings["beside"]
    record = read(state)
    champion = record["champion"]
    folder = folder_of(state, champion)
    token = driver.RUNTIME_PYTHON.set(record.get("toolchain", {}).get("python"))
    bots: dict[str, Path] = {}
    note = lambda **changes: update(state, champion, **changes)  # noqa: E731  (sweep.wait_idle's record)

    def bot(name: str) -> Path:  # built at its first map, so an unused opponent costs nothing
        if name not in bots:
            (_, bots[name]), = driver.rotation_bots([name])
        return bots[name]

    try:
        gen = load_gen()
        base = driver.gauntlet_bot({"name": "champion", "commit": champion}, into=folder / "bots")
        while True:
            with meta.locked(state / "advmaps"):
                current = read(state)
                if current.get("champion") != champion:
                    return
                for line in decide(state, current, gen, folder):
                    print(line, flush=True)
                maps, rows = listing(folder), results(folder)
                current.update(games=search_games(maps, rows), maps=len(maps), progress_at=time.time())
                save(state, current)
            todo = unplayed(maps, rows)
            if not todo:
                todo, why = propose(gen, folder, champion, current, settings)
                if not todo and why in ("budget", "finds", "no parents") and extend:
                    if not current.get("base_done"):
                        current["base_done"] = why
                        update(state, champion, base_done=why)
                    if reason := sweep.wait_idle(lambda: wanted(state, repo, champion), note):
                        update(state, champion, pid=None, paused=idle_pause(reason, settings["games"]),
                               paused_at=time.time())
                        print(f"paused: {reason}", flush=True)
                        return
                    todo, why = propose(gen, folder, champion, current,
                                        {**settings, "games": settings["idle_games"], "idle": True})
                if not todo and why is None:
                    continue  # verdicts are due: decide them first
                if not todo:
                    if why == "climb limit":
                        update(state, champion, pid=None, paused="the run's climb limit", paused_at=time.time())
                    else:
                        tidy(folder, current)
                        update(state, champion, pid=None, finished_at=time.time(), finished=why, error=None,
                               failures=0)
                    print(f"stopped: {why}", flush=True)
                    return
            idle_stage = extend and current.get("base_done")
            batch = todo[:IDLE_BATCH_MAPS if idle_stage else BATCH_MAPS]  # opponents mixed (plan["opponents"])
            idle = state if idle_stage and any(m["stage"] != "regress" for m in batch) else None
            # An evaluation on the hosts is waited out in place (a run by hand beside evaluations never meets one).
            if reason := sweep.wait_idle(lambda: halt(repo, champion, settings["beside"], idle), note):
                update(state, champion, pid=None, paused=reason, paused_at=time.time())
                print(f"paused: {reason}", flush=True)
                return
            # A run by hand's own gate on this Mac's load. Not while an evaluation runs: then the referee already holds
            # this Mac to native.CODER_FRACTION of its cores for yielding games, and waiting would idle the hosts too.
            while settings["max_load"] and os.getloadavg()[0] >= settings["max_load"] and not evaluation_running():
                print(f"waiting: load {os.getloadavg()[0]:.2f} >= {settings['max_load']}", flush=True)
                time.sleep(60)
            shutil.rmtree(folder / "replays", ignore_errors=True)
            opponents = {o: str(bot(o)) for o in sorted({opponent_of(m) for m in batch})}
            plan = {"champion": str(base), "opponents": opponents, "out": str(folder / "results.jsonl"),
                    "maps": batch, "replays": str(folder / "replays"), "kept": str(folder / "kept"),
                    "status": str(state / "remote-status-advmaps.json"), "repo": str(repo), "commit": champion,
                    "beside": settings["beside"], "idle": str(idle) if idle else None}
            online.save(folder / "plan.json", plan)  # whole, so health.plan_file never reads half a plan
            proc = driver.referee(str(Path(__file__).resolve()), ["play", str(folder / "plan.json")], timeout=6 * 3600,
                                  yielding=True if settings["beside"] else "spare" if idle else "filler")
            shutil.rmtree(folder / "replays", ignore_errors=True)
            print(proc.stdout + proc.stderr[-3000:], end="", flush=True)
            if proc.returncode not in (0, PAUSED):
                raise RuntimeError(f"play exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-600:]}")
            update(state, champion, failures=0, error=None)  # a batch played: earlier failures are over
    except Exception as error:
        with meta.locked(state / "advmaps"):
            current = read(state)
            if current.get("champion") == champion:
                save(state, failure(current, f"{type(error).__name__}: {error}", time.time()))
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    finally:
        driver.RUNTIME_PYTHON.reset(token)


# ------------------------------------------------------------------------------------------------- playing

# Our deaths in one replay (`team` is our side): {"deaths": {cause: n}, "wall_dead_end": hitWall deaths whose head
# was on a dead-end or corridor tile}. From `tools/replays.py deaths` in its own process, under a timeout (before
# MISSES 97, loading tools/replay.capnp beside the referee's referee/replay.capnp aborted on their shared schema id).
def tally(replay: Path, team: str, trap: set) -> dict:
    import subprocess
    out = subprocess.run([sys.executable, str(REPO / "tools/replays.py"), "deaths", str(replay)], check=True,
                         capture_output=True, text=True, timeout=300).stdout
    causes, walls = Counter(), 0
    for death in (json.loads(line) for line in out.splitlines() if line.startswith("{")):
        if death["team"] != team:
            continue
        causes[death["reason"]] += 1
        walls += death["reason"] == "hitWall" and tuple(death["head"]) in trap
    return {"deaths": dict(causes), "wall_dead_end": walls}


# The games of `maps` in order, both sides a map: before each map, `stop()` is asked, and a reason ends the games there
# (the map being played finishes). Yields (map, side); `stopped` receives the reason.
def schedule(maps: list[dict], stop, stopped: list[str]):
    for m in maps:
        if reason := stop():
            stopped.append(reason)
            return
        for side in ("A", "B"):
            yield m, side


# `play PLAN`, run by driver.referee inside the referee checkout (native.py play puts referee/ on the path and applies
# the load gates and an idle filler's share): the champion (bot a) against each map's opponent (bot b, from the plan's
# "opponents"), both sides, appending a map's two rows to the results once both are in, with our deaths tallied from the replays of
# a map lost, drawn or narrowly won, and of every perturbation and regression map. A lost map's replays move to the
# plan's kept folder, for its find (the starter's only). Exits PAUSED when it stopped for halt() (with the plan's
# "idle", past GAMES_CAP, wanted()).
def play(plan_path: Path) -> int:
    import remote
    import run as referee
    from unswbc.errors import UserError
    from unswbc.run import _resolve

    plan = json.loads(plan_path.read_text())
    remote.STATUS = Path(plan["status"])  # never the evaluation's live status
    out, champion = Path(plan["out"]), Path(plan["champion"])
    # A plan written before batches mixed opponents names one "opponent" for all its maps.
    opponents = ({name: Path(path) for name, path in plan["opponents"].items()} if "opponents" in plan else
                 {opponent_of(m): Path(plan["opponent"]) for m in plan["maps"]})
    replays, kept = Path(plan["replays"]), Path(plan["kept"])
    idle = Path(plan["idle"]) if plan.get("idle") else None
    stop = lambda: halt(Path(plan["repo"]), plan["commit"], plan.get("beside", False), idle)  # noqa: E731
    if reason := stop():  # before its builds, as between its maps
        print(f"stopped before building: {reason}", file=sys.stderr)
        return PAUSED
    for bot in (champion, *opponents.values()):
        try:
            _resolve(str(bot), False)
        except UserError as error:
            print(f"{bot.name} does not build: {error}", file=sys.stderr)
            return 2
    gen = load_gen()
    stopped: list[str] = []
    planned = schedule(plan["maps"], stop, stopped)
    owners: list[dict] = []

    def jobs():
        for m, side in planned:
            owners.append(m)
            yield referee.Job(Path(m["path"]), champion, opponents[opponent_of(m)], side)

    played, mine = 0, []
    replays.mkdir(parents=True, exist_ok=True)
    for result in referee.stream_games(jobs(), [champion, *opponents.values()], "auto", False, replays=replays):
        played += 1  # results come in job order, and a job is pulled before its result
        mine.append(result)
        if len(mine) < 2:
            continue
        write_rows(owners[played - 2], mine, replays, kept, gen, out)
        mine = []
    if stopped:
        print(f"stopped between maps: {stopped[0]}", file=sys.stderr)
        return PAUSED
    return 0


# One map's two results as rows of `out`, or one game_error row when a game couldn't be played (the map is then
# unscored, never a parent or a find). A lost starter map's replays go to `kept`; strong opponents' full-length games
# keep none (the module docstring's file bounds).
def write_rows(m: dict, games: list, replays: Path, kept: Path, gen, out: Path) -> None:
    import run as referee
    if error := next((r for r in games if r.error), None):
        with out.open("a") as handle:
            handle.write(json.dumps({"name": m["name"], "stage": m["stage"],
                                     "game_error": f"{error.map} ({error.a_side}): {error.error}"[-600:]}) + "\n")
        print(f"  {m['name']}: game failed: {error.error}", file=sys.stderr, flush=True)
        return
    count = starts(Path(m["path"]).read_text())
    rows = [{"name": m["name"], "stage": m["stage"], "starts": count, **json.loads(referee.to_json(r))} for r in games]
    worst = map_margin(rows)
    if worst < COMFORT or m["stage"] in ("perturb", "regress"):
        board = gen.parse(Path(m["path"]).read_text())
        trap = dead_ends(board) | corridors(board)
        for row in rows:
            found = sorted(replays.glob(f"*-{Path(m['path']).stem}-a{row['a_side']}.replay"))
            if found:
                try:
                    row.update(tally(found[-1], row["a_side"], trap))
                except Exception as error:  # a replay that can't be read leaves the game untallied, not unplayed
                    row["tally_error"] = f"{type(error).__name__}: {error}"[:300]
                if lost(rows) and m["stage"] in ("seed", "climb", "perturb") and opponent_of(m) == STARTER:
                    kept.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(found[-1]), kept / f"{m['name']}-a{row['a_side']}.replay")
    with out.open("a") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in rows)
    print(f"  {m['name']} ({m['stage']}): margin {worst:.2f}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------------- reports

# stats.md's section: the latest search's regression record, a line a map in maps/adversarial/, then its progress,
# worst maps, the strong pass's record a line an opponent, and the finds.
def stats_lines(state: Path) -> list[str]:
    record = read(state)
    if not (champion := record.get("champion")):
        return []
    folder = folder_of(state, champion)
    maps, rows = listing(folder), results(folder)
    played, margins = by_map(rows), scored(rows)
    lines = ["", f"## Adversarial maps, champion {champion[:7]} (loop/advmaps.py)", "",
             "Regression, every map in maps/adversarial/ (both sides; a loss or draw on either side against the "
             "starter alerts, on both sides against any other opponent, unless the champion before lost it too):", ""]
    regress = list({m["origin"]: m for m in maps if m["stage"] == "regress"}.values())  # a map's latest opponent
    for m in regress:
        games = played.get(m["name"], [])
        wins = sum(g.get("winner") == "a" for g in games)
        lines.append(f"- {m['origin']}" + (f" against {opponent_of(m)}" if opponent_of(m) != STARTER else "")
                     + f": won {wins} of {len(games)}"
                     + (f" ({'; '.join(side_text(g) for g in games)})" if games else " (not played yet)"))
    if not regress:
        lines.append("- none: maps/adversarial/ has no maps yet, or the search hasn't started")
    stages = Counter(m["stage"] for m in maps if not m.get("unmade"))
    worst = sorted((m["name"] for m in maps if m["stage"] in ("seed", "climb") and m["name"] in margins),
                   key=lambda n: margins[n])[:KEEP]
    origin = {m["name"]: m["origin"] for m in maps if "origin" in m}
    state_text = ("finished (" + str(record.get("finished")) + ")" if record.get("finished_at")
                  else f"paused ({record['paused']})" if record.get("paused") else "running" if record.get("pid")
                  else "waiting")
    lines += ["", f"Search {state_text}: {search_games(maps, rows)} games (the first {GAMES_CAP} as a filler, up to "
              f"{IDLE_CAP} on idle machines only); maps: "
              + ", ".join(f"{n} {s}" for s, n in sorted(stages.items())) + ". Worst against the starter: "
              + ("; ".join(f"{n} {margins[n]:.2f} ({origin.get(n, '?')})" for n in worst) or "none yet") + "."]
    flukes = record.get("flukes") or {}
    for opponent in sorted({opponent_of(m) for m in maps if m["stage"] == "strong"}):
        mine = [m for m in maps if m["stage"] == "strong" and opponent_of(m) == opponent]
        made = [m for m in mine if not m.get("unmade")]
        both = [played[m["name"]] for m in made if m["name"] in margins]
        pool = [played[m["name"]] for m in made if m.get("pool") and m["name"] in margins]
        why = {m["name"]: flukes[m["name"]] for m in made if m["name"] in flukes}
        unperturbed = sum(text.startswith("not perturbed") for text in why.values())
        lines.append(f"- strong pass against {opponent}: {len(both)} of {len(made)} maps played, lost or drew both "
                     f"sides {sum(lost_map(g, opponent) for g in both)} (the seed pool "
                     f"{sum(lost_map(g, opponent) for g in pool)} of {len(pool)}); of those judged, {unperturbed} not "
                     f"perturbed (no find possible at its own loss rate) and {len(why) - unperturbed} perturbed "
                     f"without a find" + "".join(f"; {m['skipped']}" for m in mine if m.get("skipped")))
    finds = record.get("finds") or {}
    lines += [f"- find {name}" + (f" against {f['opponent']}" if f.get("opponent", STARTER) != STARTER else "")
              + f": {f['origin']}; "
              + (f"lost on {f['reproduced']} perturbations, p {f['p']:.3g}"
                 + ("" if f["p"] <= chance.TRIGGER_P else " (suggestive only, not alerted)") + "; " if "p" in f else "")
              + f"deaths {f['deaths']}; shape {', '.join(f['features']['flags'])}; {f['folder']}"
              for name, f in finds.items()]
    listed = {m["name"]: m for m in maps}
    if starter := {n: why for n, why in flukes.items() if opponent_of(listed.get(n, {})) == STARTER}:
        lines.append("- not reproduced: " + "; ".join(f"{n} ({why})" for n, why in starter.items()))
    return lines


def check(state: Path) -> None:
    print("\n".join(stats_lines(state)[1:]) or "no search recorded")
    record = read(state)
    for key in ("pid", "paused", "base_done", "error", "failures", "idle_at", "started_at", "finished_at"):
        if record.get(key) is not None:
            print(f"{key}: {record[key]}")


def main() -> None:
    from home import loop_home
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="print the current search's progress")
    checking.add_argument("--state", type=Path, default=loop_home())
    running = commands.add_parser("run", help="the background run tick launches")
    running.add_argument("--repo", type=Path, default=REPO)
    running.add_argument("--state", type=Path, default=loop_home())
    running.add_argument("--manual", action="store_true", help="a run by hand for today's champion, idle or not")
    running.add_argument("--seed-maps", type=int)
    running.add_argument("--keep", type=int)
    running.add_argument("--climb-rounds", type=int)
    running.add_argument("--max-load", type=float, help="wait before each batch until the 1-minute load is under this")
    running.add_argument("--beside", action="store_true", help="with --manual: play beside evaluations, at the late "
                                                               "screen's share, rather than on idle machines only")
    playing = commands.add_parser("play", help="internal: one batch of games, under native.py play")
    playing.add_argument("plan", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        check(args.state.resolve())
    elif args.command == "run":
        repo, state = args.repo.resolve(), args.state.resolve()
        settings = {key: value for key, value in (("seed_maps", args.seed_maps), ("keep", args.keep),
                                                  ("climb_rounds", args.climb_rounds)) if value is not None}
        settings.update(beside=args.manual and args.beside, max_load=args.max_load)
        if args.manual:
            manual(repo, state)
        run(repo, state, settings)
    else:
        sys.exit(play(args.plan))


# A run by hand: records today's champion (as tick would) and this process as its runner, without the idle check.
# The run itself still stops between maps when an evaluation starts.
def manual(repo: Path, state: Path) -> None:
    import maintenance
    champion = meta.champion_commit(repo)
    with meta.locked(state / "advmaps"):
        record = read(state)
        if record.get("pid") and record["pid"] != os.getpid() and meta.alive(record["pid"]):
            raise SystemExit(f"a search is running already (pid {record['pid']})")
        if record.get("champion") != champion:
            record = retire(state, record, champion)
        record.setdefault("toolchain", maintenance.toolchain(state))
        record.update(pid=os.getpid(), attempt_at=time.time(), progress_at=time.time(), paused=None)
        record.setdefault("started_at", time.time())
        save(state, record)
    folder_of(state, champion).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
