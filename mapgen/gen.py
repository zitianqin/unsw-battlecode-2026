"""Random map generator for UNSW Battlecode 2026.

Makes symmetric, engine-valid .map files shaped like the known ladder maps, plus deliberately odd ones, "trap" maps
(pearls walled in a pocket), "deadend" maps (fast pearls at the ends of small dead-end pockets), "fortress" maps
(walled halves joined through a few gates or only portals), "portalrooms" maps (fast pearls in rooms entered only
through portals, a third with the teams sealed apart), "macro" maps (one long starting dragon a team, sometimes coiled
round its own head, beside short ones), the portal bets: "deathportal" maps (portals into kelp-walled one-tile cells
that kill whatever enters), "vault" maps (a walled pearl-rich vault a team that only portals reach) and "portalbet" maps
(nine of the first beside one of the second a team, alike from outside), "sealedstart" maps (Trauma's opening: each
team starts in its own walled room it leaves only by portal, most of the pearls beyond it), "curves" maps (diagonal and
curved kelp walls: rings, cups, 45-degree walls and funnels, or a taichi, as the organisers outline their picture maps),
and "near" maps:
perturbed copies of the ladder maps themselves (maps/ladder, and maps/retired: the maps the ladder dropped, still
organiser maps), since the ladder rating is played there, and of the Sprint-like maps (maps/sprint), each keeping its
source's sealed start rooms (gen.keeps_rooms: Trauma's, Portals'), its portal pockets with their laps and pearls
(Portals' 2x2 rooms, gen.pocket_shape) and teams that never meet (Portals); half of near maps
(repearled) keep the copied layout but redraw the whole pearl field, as the organisers said ladder maps may now come in
variants that look the same with completely different pearl timings. `--style eval`
also makes the same kind of variants of the community maps (maps/community), which `--style near` never does.
`--style sprint` makes only near variants of the Sprint-like maps, for tests aimed at them (an idea's evaluation
"maps", loop/driver.py mixed_maps).
`--style small` and `--style big` make near variants of the ladder (current and retired) and community maps of at most
SMALL_MAX tiles, or of at least BIG_MIN, for tests aimed at one size of map (online, big maps rarely end in an
elimination and small ones often do). `--style maze` makes near variants of the community Portal Labyrinth only (MAZE),
and `--style deadend` random boards with dead-end pockets, both for tests aimed at them. `--style macro` maps are a
ninth of eval (MACRO_REMAINDER), `--style curves` an eighteenth (CURVES_EVERY); `--style portalrooms` isn't part of
eval. `--style worldgen` maps come from an independent generator another competitor wrote (mapgen/worldgen, vendored
unchanged; worldgen_map), a map source whose choices ours never fitted; it isn't part of eval either, nor are the
portal bets, sealedstart, portalrooms, maze or `--style nearportal` (near variants of Trauma, Portals and Trophy only):
those and worldgen are standing shares of every test's maps beside eval (loop/meta.py general_shares).

    uv run mapgen/gen.py --seed 1 --count 50 --out /tmp/gen --style mix
    uv run mapgen/gen.py --seed 1 --count 50 --out /tmp/gen --style eval   # a third are near variants
    uv run mapgen/gen.py --validate /tmp/gen [--bot /tmp/starter]
    uv run mapgen/gen.py --stats maps/ladder maps/extra --stats /tmp/gen
    uv run mapgen/gen.py --deadends maps/ladder /tmp/gen   # each map's fast tiles in dead ends (dead_end)

Library: `from mapgen.gen import generate; text = generate(seed, "normal")`.

The board is a torus (moves wrap). Edges are keyed ("h", x, y) for the north side of tile (x, y) and
("v", x, y) for its west side, matching the engine's EdgeIndex; see engine/src/helpers.cc.

Tried and not added (2026-09-27, branch orch/mapgen-strips, whose first commit keeps the code; moved here from
loop/LOOP.md). The rule was to keep a style only if the champion scored lower on it than on eval maps at
chance.rates_p <= 0.1. The games were v0040 against the starter and the swarm, both sides, 20 maps a set. Long strips
(thin side 10-16, long side 3.5 times it or more; no organiser map is longer than 3:1): the swarm scored 0.875 against
us on strips, 0.850 on eval maps (p = 0.74) and 0.875 on the same builders' ordinary boards. Arenas (each team's own
field beside its start and a richer one between them; of the organiser maps only Autarky has one, and 28 of 400 eval
maps have one by chance, 17 of them near variants of Devil and Autarky): 0.850 (p = 0.62). The starter won no game on
any set. Starts under 3 walking steps apart are 6.3% of the 16,076 eval maps in saved SPRT games; 1.7% of their 7,344
games end by round 5 (0.1% on the rest), so these maps are not rejected. Reach between 60% and 75%: no organiser map
is in that band (only Portals is under 75%, at 47%, and near variants keep its floor), so generation still requires
75%.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import math
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

Tile = tuple[int, int]
Edge = tuple[str, int, int]

KELP = -1  # value stored in Board.edges for kelp; portals store their id (>= 0)
DIRS = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
# Appended styles only: a style's index seeds its maps, so reordering would change every existing map.
STYLES = ("normal", "odd", "mix", "near", "eval", "trap", "sprint", "small", "big", "deadend", "maze", "fortress",
          "portalrooms", "macro", "worldgen", "deathportal", "vault", "portalbet", "sealedstart", "nearportal",
          "curves")
LADDER = Path(__file__).resolve().parent.parent / "maps" / "ladder"
# Maps the ladder used to play (Big Empty, Default Small and Stronghold left its pool 2026-09-27): still organiser maps,
# so near and sized variants keep drawing from them; only the official-map report follows the live pool.
RETIRED = Path(__file__).resolve().parent.parent / "maps" / "retired"
# Maps the organisers said tournament maps will resemble (2026-09-25: the Sprint will have maps with Dilemma and
# Autarky's idea, long starting dragons beside pearl fields); near variants draw from these as well as the ladder.
SPRINT = Path(__file__).resolve().parent.parent / "maps" / "sprint"
NEAR_EVERY = 3  # in "eval", seeds divisible by this are near variants; the rest community, trap, deadend, fortress,
# macro, curves and mix
# Maps players posted in the Discord's #community-maps (downloaded once with sig's go-ahead, 2026-09-26; see its
# README): hand-made structure our generator lacks (a portal maze, a corridor trap, long-dragon starts). In "eval",
# seeds leaving COMMUNITY_REMAINDER when divided by COMMUNITY_EVERY (never a near seed) are variants of them,
# taken from the mix share, so the near third is unchanged.
COMMUNITY = Path(__file__).resolve().parent.parent / "maps" / "community"
COMMUNITY_EVERY, COMMUNITY_REMAINDER = 9, 1
# The one community map the `maze` style varies (an idea's map mix only, not part of eval, where it is about 1 map in
# 36): a 32x32 portal maze, 43% of edges kelp and 8 portal pairs, on which every champion v0001-v0036 lost both sides to
# the toolkit's starter bot (2026-09-27: unseen edges assumed open walk our dragons into dead ends).
MAZE = COMMUNITY / "portal_labyrinth.map"
# The ladder maps whose portals decide games, which `--style nearportal` varies alone: Trauma's sealed starts, Portals'
# rooms and Trophy's cup portal.
PORTAL_LADDER = tuple(LADDER / f"{name}.map" for name in ("trauma", "portals", "trophy"))
# Another ninth of eval seeds, also from the mix share, are `trap` maps (pearls walled in a pocket behind narrow doors;
# the map-ideas worker's run of 2026-09-26 from the Snake Trap and Labyrinth posts): the organisers wall pearls in on 7
# of their 11 maps, mix on 2 of 33.
TRAP_REMAINDER = 5
# And another ninth, from the mix share too (4/9 -> 3/9, 2026-09-27), are `deadend` maps: fast pearl tiles at the
# ends of small dead-end pockets behind 1- or 2-wide doors, which every ladder map from Stronghold on has and trap and
# mix maps almost never do (dead_end below; trap maps: 6 of 3,846 fast tiles).
DEADEND_REMAINDER = 7
# And another, from mix too (3/9 -> 2/9, 2026-09-27), are `fortress` maps: walled halves joined through a few gates or
# only portals, the shape of our five worst ladder maps (fortress_shape below), which mix and trap maps never had.
FORTRESS_REMAINDER = 8
# And another, from mix too (2/9 -> 1/9, 2026-09-27), are `macro` maps: one long starter a team (a third coiled round
# its own head) beside short ones, as the organisers confirmed the Sprint maps start (2026-09-25) and Slithery Fight
# does, which random layouts never had. Portalrooms maps aren't in eval: their portal-only rooms overlap fortress's.
MACRO_REMAINDER = 4
# And half the last mix ninth (1/9 -> 1/18, 2026-09-29), seeds leaving CURVES_REMAINDER when divided by CURVES_EVERY,
# are `curves` maps: diagonal and curved kelp walls, which 6 of the 13 organiser maps outline their pictures in and
# random layouts almost never have (diagonal_wall below).
CURVES_EVERY, CURVES_REMAINDER = 18, 2
# Map sizes (width x height, in tiles) for the small and big styles (review M28, submission 7003's 100 online games: no
# elimination in 34 games on maps of 1152+ tiles, all 14 elimination losses on 1024 or fewer). No source map falls
# between them; a variant keeps its source's size.
SMALL_MAX, BIG_MIN = 1024, 1152
ODD_QUIRKS = ("tiny", "huge", "maze", "portals", "sparse", "far_farms", "close_starts")
MIN_REACH = 0.75  # every dragon must reach at least this share of all tiles


@dataclass
class Board:
    """Mutable map under construction. Every setter also writes the mirror, so the board stays symmetric."""

    w: int
    h: int
    sym: str
    gaps: dict[Tile, tuple[int, int]] = field(default_factory=dict)
    edges: dict[Edge, int] = field(default_factory=dict)
    dragons: list[list[Tile]] = field(default_factory=list)
    next_portal: int = 0
    quirks: set[str] = field(default_factory=set)
    unknown: set[Tile] = field(default_factory=set)  # tiles whose pearl gaps the parsed text didn't give (parse)

    def all_tiles(self) -> list[Tile]:
        return [(x, y) for y in range(self.h) for x in range(self.w)]

    def mirror(self, t: Tile) -> Tile:
        x, y = t
        if self.sym == "x":
            return (x, self.h - 1 - y)
        if self.sym == "y":
            return (self.w - 1 - x, y)
        return (self.w - 1 - x, self.h - 1 - y)

    def mirror_edge(self, e: Edge) -> Edge:
        o, x, y = e
        flip_x = self.sym in ("y", "xy")
        flip_y = self.sym in ("x", "xy")
        if o == "h":
            return ("h", self.w - 1 - x if flip_x else x, (self.h - y) % self.h if flip_y else y)
        return ("v", (self.w - x) % self.w if flip_x else x, self.h - 1 - y if flip_y else y)

    def wrap(self, x: int, y: int) -> Tile:
        return (x % self.w, y % self.h)

    def set_gap(self, t: Tile, gap: tuple[int, int]) -> None:
        self.gaps[t] = gap
        self.gaps[self.mirror(t)] = gap

    def set_kelp(self, e: Edge) -> None:
        self.edges[e] = KELP
        self.edges[self.mirror_edge(e)] = KELP

    def open_edge(self, e: Edge) -> None:
        self.edges.pop(e, None)
        self.edges.pop(self.mirror_edge(e), None)

    def side(self, t: Tile, d: str) -> Edge:
        """The edge on side d of tile t (EdgeOnTileSide)."""
        x, y = t
        if d == "N":
            return ("h", x, y)
        if d == "S":
            return ("h", x, (y + 1) % self.h)
        if d == "W":
            return ("v", x, y)
        return ("v", (x + 1) % self.w, y)

    def step(self, t: Tile, d: str) -> Tile | None:
        """Where a head on t ends up moving d, or None for kelp (TileAfterStep)."""
        kind = self.edges.get(self.side(t, d))
        if kind is None:
            dx, dy = DIRS[d]
            return self.wrap(t[0] + dx, t[1] + dy)
        if kind == KELP:
            return None
        _, px, py = self.portal_partner(self.side(t, d))
        if d in ("N", "S"):
            return self.wrap(px, py if d == "S" else py - 1)
        return self.wrap(px if d == "E" else px - 1, py)

    def portal_partner(self, e: Edge) -> Edge:
        pid = self.edges[e]
        return next(o for o, k in self.edges.items() if k == pid and o != e)

    def plain_step(self, t: Tile, d: str) -> Tile | None:
        """A step across an open edge only (no portal), used to lay out dragon bodies."""
        if self.side(t, d) in self.edges:
            return None
        dx, dy = DIRS[d]
        return self.wrap(t[0] + dx, t[1] + dy)

    def reach(self, start: Tile) -> set[Tile]:
        seen = {start}
        todo = deque([start])
        while todo:
            t = todo.popleft()
            for d in DIRS:
                n = self.step(t, d)
                if n is not None and n not in seen:
                    seen.add(n)
                    todo.append(n)
        return seen

    def kelp_count(self) -> int:
        return sum(1 for k in self.edges.values() if k == KELP)


# ---------------------------------------------------------------- shape


# Picks width, height and symmetry. Normal maps copy the known aspect ratios (1:1, 2:1, ~0.7).
def pick_shape(rng: random.Random, quirks: set[str]) -> tuple[int, int, str]:
    sym = rng.choices(["xy", "y", "x"], weights=[5, 3, 2])[0]
    if "tiny" in quirks:
        return 10, 10, sym
    if "huge" in quirks:
        return 64, 64, sym
    aspect = rng.choices(["square", "wide", "tall"], weights=[6, 3, 2])[0]
    if aspect == "square":
        s = rng.randint(11, 64)
        w, h = s, s
    elif aspect == "wide":
        h = rng.randint(10, 32)
        w = min(64, 2 * h + rng.choice([0, 0, 1]))
    else:
        w = rng.randint(14, 45)
        h = min(64, round(w / 0.7))
    if rng.random() < 0.25:
        w, h = h, w
    return w, h, sym


# ---------------------------------------------------------------- tiles


# Grows a connected blob of about n tiles from a random seed tile (4-neighbour, wrapping).
def blob(board: Board, rng: random.Random, n: int, seed: Tile | None = None) -> list[Tile]:
    start = seed or (rng.randrange(board.w), rng.randrange(board.h))
    out = [start]
    have = {start}
    while len(out) < n:
        x, y = rng.choice(out)
        dx, dy = rng.choice(list(DIRS.values()))
        t = board.wrap(x + dx, y + dy)
        if t not in have:
            have.add(t)
            out.append(t)
    return out


# Fills every tile with slow pearl gaps, then carves non-spawning areas (max 0) and a few slower/faster zones.
def paint_tiles(board: Board, rng: random.Random, quirks: set[str]) -> None:
    area = board.w * board.h
    roll = rng.random()
    if "sparse" in quirks:
        base = (1, rng.randint(1000, 4000))
    elif roll < 0.1 and area <= 400:
        base = (rng.randint(3, 10), rng.randint(15, 40))  # arena-like: the whole map is fast
    elif roll < 0.25:
        base = (20, rng.randint(80, 200))
    elif roll < 0.4:
        base = (1, rng.randint(1000, 4000))
    else:
        base = (1, rng.randint(200, 1000))
    for t in board.all_tiles():
        board.gaps[t] = base

    for _ in range(rng.randint(0, 3)):
        zone = (base[0], max(base[0], int(base[1] * rng.uniform(0.2, 0.7))))
        for t in blob(board, rng, rng.randint(4, max(5, area // 12))):
            board.set_gap(t, zone)

    if "sparse" in quirks:
        dead_share = rng.uniform(0.9, 0.97)
    elif rng.random() < 0.45:
        dead_share = 0.0
    else:
        dead_share = rng.uniform(0.2, 0.88)
    target = int(area * dead_share)
    while sum(1 for g in board.gaps.values() if g[1] == 0) < target:
        size = rng.randint(4, max(5, area // 10))
        if rng.random() < 0.5:
            tiles = blob(board, rng, size)
        else:
            x0, y0 = rng.randrange(board.w), rng.randrange(board.h)
            rw = rng.randint(2, max(2, board.w // 3))
            rh = max(1, size // rw)
            tiles = [board.wrap(x0 + i, y0 + j) for i in range(rw) for j in range(rh)]
        for t in tiles:
            board.set_gap(t, (0, 0))


# Adds small farms of fast tiles (FAST_GAP) to nine maps in ten (2026-09-27: 14 of 33 mix maps had no fast tile, 2 of
# 13 organiser maps, Default and Big Empty). With far=True, farms go as far from every dragon head as possible.
def add_farms(board: Board, rng: random.Random, far: bool, sparse: bool) -> None:
    if not far and (sparse or rng.random() > 0.9):
        return
    heads = [d[0] for d in board.dragons]
    for _ in range(rng.randint(1, 3)):
        gap = (1, 1) if rng.random() < 0.3 else (1, rng.randint(2, 2 * FAST_GAP - 1))
        seed = None
        if far:
            candidates = [(rng.randrange(board.w), rng.randrange(board.h)) for _ in range(40)]
            seed = max(candidates, key=lambda t: min(torus_dist(board, t, h) for h in heads))
        for t in blob(board, rng, rng.randint(2, 12), seed):
            board.set_gap(t, gap)


def torus_dist(board: Board, a: Tile, b: Tile) -> int:
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return min(dx, board.w - dx) + min(dy, board.h - dy)


# ---------------------------------------------------------------- kelp


# A straight kelp wall of n edges starting at tile t. Horizontal walls block north-south movement.
def wall(board: Board, t: Tile, horizontal: bool, n: int) -> list[Edge]:
    x, y = t
    if horizontal:
        return [("h", (x + i) % board.w, y) for i in range(n)]
    return [("v", x, (y + i) % board.h) for i in range(n)]


# Lays kelp features (segments, L-shapes, rooms, border seams) until about `share` of all edges are kelp.
def lay_kelp(board: Board, rng: random.Random, share: float) -> None:
    total = 2 * board.w * board.h
    if rng.random() < 0.3:
        for x in range(board.w):
            board.set_kelp(("h", x, 0))
    if rng.random() < 0.3:
        for y in range(board.h):
            board.set_kelp(("v", 0, y))
    guard = 0
    while board.kelp_count() < share * total and guard < 5000:
        guard += 1
        t = (rng.randrange(board.w), rng.randrange(board.h))
        kind = rng.choices(["segment", "ell", "room"], weights=[5, 3, 2])[0]
        n = rng.randint(2, max(3, min(board.w, board.h) // 3))
        if kind == "segment":
            edges = wall(board, t, rng.random() < 0.5, n)
        elif kind == "ell":
            m = rng.randint(2, n)
            edges = wall(board, t, True, n) + wall(board, t, False, m)
        else:
            m = rng.randint(2, n)
            x, y = t
            edges = (
                wall(board, t, True, n)
                + wall(board, (x, (y + m) % board.h), True, n)
                + wall(board, t, False, m)
                + wall(board, ((x + n) % board.w, y), False, m)
            )
            for _ in range(rng.randint(1, 2)):
                edges.remove(rng.choice(edges))
        for e in edges:
            board.set_kelp(e)


# Cell index for each coordinate along one axis, in cells of about c tiles. On a mirrored axis the cells are laid
# out from both ends (and an odd middle line is its own cell), so cell walls land on mirrored cell walls.
def axis_cells(n: int, c: int, flipped: bool) -> list[int]:
    if not flipped:
        return [min(i // c, max(1, n // c) - 1) for i in range(n)]
    half = n // 2
    left = [i // c for i in range(half)]
    count = 2 * (left[-1] + 1) + n % 2
    middle = [count // 2] if n % 2 else []
    return left + middle + [count - 1 - left[n - 1 - i] for i in range(half + n % 2, n)]


# Carves a random maze over cells of about c tiles; a wall between two cells stays kelp unless the maze opens it.
# The maze and its mirror are both connected, so keeping only kelp present in both keeps it connected.
def lay_maze(board: Board, rng: random.Random) -> None:
    c = rng.choice([1, 2, 2, 3])
    xs = axis_cells(board.w, c, board.sym in ("y", "xy"))
    ys = axis_cells(board.h, c, board.sym in ("x", "xy"))
    cols, rows = max(xs) + 1, max(ys) + 1

    def cell(t: Tile) -> Tile:
        return (xs[t[0]], ys[t[1]])

    boundary: dict[tuple[Tile, Tile], list[Edge]] = {}
    for t in board.all_tiles():
        for d, e in (("N", board.side(t, "N")), ("W", board.side(t, "W"))):
            dx, dy = DIRS[d]
            a, b = cell(t), cell(board.wrap(t[0] + dx, t[1] + dy))
            if a != b:
                boundary.setdefault((min(a, b), max(a, b)), []).append(e)

    opened: set[Edge] = set()
    start = (rng.randrange(cols), rng.randrange(rows))
    seen = {start}
    stack = [start]
    while stack:
        cx, cy = stack[-1]
        nbrs = [(cx + dx, cy + dy) for dx, dy in DIRS.values()]
        nbrs = [n for n in nbrs if 0 <= n[0] < cols and 0 <= n[1] < rows and n not in seen]
        if not nbrs:
            stack.pop()
            continue
        n = rng.choice(nbrs)
        seen.add(n)
        stack.append(n)
        key = (min((cx, cy), n), max((cx, cy), n))
        opened.add(rng.choice(boundary[key]))
    walls = [e for es in boundary.values() for e in es if e not in opened]
    for e in walls:
        if rng.random() < 0.9:  # a few extra openings make loops
            if board.mirror_edge(e) not in opened:
                board.edges[e] = KELP
    # Keep only kelp whose mirror is kelp too.
    for e in [e for e, k in board.edges.items() if k == KELP]:
        if board.edges.get(board.mirror_edge(e)) != KELP:
            del board.edges[e]


# ---------------------------------------------------------------- portals


# Adds n mirrored portal pairs. Both ends share an orientation, lie off the symmetry line, and the mirror pair
# never touches the original pair, so each pair maps to exactly one other pair.
def add_portals(board: Board, rng: random.Random, n: int) -> None:
    for _ in range(n):
        for _attempt in range(200):
            o = rng.choice("hv")
            e1: Edge = (o, rng.randrange(board.w), rng.randrange(board.h))
            e2: Edge = (o, rng.randrange(board.w), rng.randrange(board.h))
            ends = {e1, e2, board.mirror_edge(e1), board.mirror_edge(e2)}
            if len(ends) == 4 and not any(e in board.edges for e in ends):
                board.edges[e1] = board.edges[e2] = board.next_portal
                board.edges[board.mirror_edge(e1)] = board.edges[board.mirror_edge(e2)] = board.next_portal + 1
                board.next_portal += 2
                break


# ---------------------------------------------------------------- connectivity


# Opens kelp edges (and their mirrors) between the largest region and the rest until it covers `reach` of the board
# (MIN_REACH unless given), never one in `keep` (a mirrored set: a deadend map's pocket walls).
def knock_holes(board: Board, rng: random.Random, keep: frozenset[Edge] = frozenset(), reach: float = MIN_REACH) -> None:
    area = board.w * board.h
    for _ in range(area):
        main = largest_region(board)
        if len(main) >= reach * area:
            return
        doors = [
            board.side(t, d)
            for t in sorted(main)
            for d in DIRS
            if board.edges.get(board.side(t, d)) == KELP and board.side(t, d) not in keep
        ]
        if not doors:
            return
        board.open_edge(rng.choice(doors))


def largest_region(board: Board) -> set[Tile]:
    left = set(board.all_tiles())
    best: set[Tile] = set()
    while left:
        region = board.reach(min(left))
        left -= region
        if len(region) > len(best):
            best = region
    return best


# ---------------------------------------------------------------- dragons


# Tiles team A may start on: its half of the board, or a strip beside the symmetry line for close starts.
def team_a_zone(board: Board, close: bool) -> list[Tile]:
    split_x = board.sym in ("y", "xy")
    span = board.w if split_x else board.h
    half = span // 2
    lo, hi = (max(0, half - 3), half - 1) if close else (0, max(0, half - 2))
    out = []
    for t in board.all_tiles():
        c = t[0] if split_x else t[1]
        if lo <= c <= hi and board.mirror(t) != t:
            out.append(t)
    return out


# Places `count` team A dragons in the main region, off the `avoid` tiles (and only on `within`'s, when given), and
# mirrors each as a team B dragon (A, B, A, B order).
def place_dragons(
    board: Board, rng: random.Random, count: int, close: bool, avoid: frozenset[Tile] = frozenset(),
    within: set[Tile] | None = None,
) -> bool:
    main = largest_region(board)
    zone = [t for t in team_a_zone(board, close) if t in main and t not in avoid and (within is None or t in within)]
    if not zone:
        return False
    taken: set[Tile] = set()
    for _ in range(count):
        length = rng.randint(3, 6)
        for _attempt in range(300):
            body = lay_body(board, rng, rng.choice(zone), length, taken)
            if body is not None:
                mirrored = [board.mirror(t) for t in body]
                board.dragons += [body, mirrored]
                taken |= set(body) | set(mirrored)
                break
        else:
            return False
    return True


# Lays one body, head first: straight when possible, otherwise a random self-avoiding walk.
def lay_body(board: Board, rng: random.Random, head: Tile, length: int, taken: set[Tile]) -> list[Tile] | None:
    straight = rng.random() < 0.6
    first = rng.choice(list(DIRS))
    body = [head]
    for _ in range(length - 1):
        options = [first] if straight else rng.sample(list(DIRS), 4)
        nxt = None
        for d in options:
            t = board.plain_step(body[-1], d)
            if t is not None and t not in body and ok_tile(board, t, body, taken):
                nxt = t
                break
        if nxt is None:
            return None
        body.append(nxt)
    if not ok_tile(board, head, body[1:], taken):
        return None
    # The head needs somewhere to go besides its own neck.
    exits = [board.step(head, d) for d in DIRS]
    if not any(t is not None and t not in body and t not in taken for t in exits):
        return None
    return body


def ok_tile(board: Board, t: Tile, body: list[Tile], taken: set[Tile]) -> bool:
    m = board.mirror(t)
    return t not in taken and m not in taken and m != t and m not in body


# ---------------------------------------------------------------- whole map


# Builds one map for `style` ("normal", "odd", "near", "trap", "deadend" or "sprint", near variants of the Sprint-like
# maps only; "maze", of the Portal Labyrinth only; "fortress", "portalrooms" or "macro"; "small" and "big", near
# variants of the ladder and community maps of one size, sized_sources; "worldgen", xCirno1's independent generator
# (worldgen_map); "deathportal", "vault" and "portalbet", the portal bets; "sealedstart", Trauma's sealed start
# rooms; "nearportal", near variants of the portal ladder maps only (PORTAL_LADDER); "curves", diagonal and curved
# walls; "mix" picks normal or odd from the seed, and "eval" makes every third seed near (half of those repearled), a
# ninth community variants, a ninth trap, a ninth deadend, a ninth fortress, a ninth macro, an eighteenth curves and the
# rest mix). Every style in STYLES. Deterministic by seed.
def generate(seed: int, style: str = "normal") -> str:
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}, got {style!r}")
    rng = random.Random(seed * 7919 + STYLES.index(style))
    if style == "eval":
        # The near seeds count up through near_sources() too, so each source gets an equal share.
        if seed % NEAR_EVERY == 0:
            return near(seed, seed // NEAR_EVERY, rng, repearl=repearled(seed))
        if seed % COMMUNITY_EVERY == COMMUNITY_REMAINDER and community_sources():
            return near(seed, community_pick(seed), rng, community_sources())
        if seed % COMMUNITY_EVERY == TRAP_REMAINDER:
            return generate(seed, "trap")
        if seed % COMMUNITY_EVERY == DEADEND_REMAINDER:
            return generate(seed, "deadend")
        if seed % COMMUNITY_EVERY == FORTRESS_REMAINDER:
            return generate(seed, "fortress")
        if seed % COMMUNITY_EVERY == MACRO_REMAINDER:
            return generate(seed, "macro")
        if seed % CURVES_EVERY == CURVES_REMAINDER:
            return generate(seed, "curves")
        return generate(seed, "mix")
    if style == "near":
        return near(seed, seed, rng, repearl=repearled(seed))
    if style == "sprint":
        if not (sources := sorted(SPRINT.glob("*.map"))):
            raise RuntimeError(f"no Sprint-like maps in {SPRINT}")
        return near(seed, seed, rng, sources)
    if style in ("small", "big"):
        return near(seed, seed, rng, sized_sources(style))
    if style == "maze":
        return near(seed, seed, rng, [MAZE])
    if style == "nearportal":
        return near(seed, seed, rng, list(PORTAL_LADDER))
    if style == "worldgen":
        return worldgen_map(seed)
    if style == "mix":
        style = "odd" if rng.random() < 0.3 else "normal"
    for attempt in range(100):
        builder = {"trap": build_trap, "deadend": build_deadend, "fortress": build_fortress,
                   "portalrooms": build_portalrooms, "macro": build_macro, "deathportal": build_deathportal,
                   "vault": build_vault, "portalbet": build_portalbet, "sealedstart": build_sealedstart,
                   "curves": build_curves}.get(style)
        board = builder(rng) if builder else build(rng, style)
        if board is not None:
            return render(board, "-".join(["gen", style, str(seed), *sorted(board.quirks)]))
    raise RuntimeError(f"seed {seed} produced no valid map in 100 attempts")


def pick_quirks(rng: random.Random, style: str) -> set[str]:
    if style != "odd":
        return set()
    quirks = set(rng.sample(ODD_QUIRKS, rng.choice([1, 2, 2, 3])))
    if {"tiny", "huge"} <= quirks:
        quirks.discard(rng.choice(["tiny", "huge"]))
    return quirks


# One attempt at a full map; None when the result fails the reachability check.
def build(rng: random.Random, style: str) -> Board | None:
    quirks = pick_quirks(rng, style)
    w, h, sym = pick_shape(rng, quirks)
    board = Board(w, h, sym, quirks=quirks)
    paint_tiles(board, rng, quirks)

    if "maze" in quirks:
        lay_maze(board, rng)
    else:
        share = 0.0 if rng.random() < 0.1 else rng.uniform(0.03, 0.25)
        lay_kelp(board, rng, share)
    # Half have a walled border, no wrap (2026-09-27: 4 of 33 mix maps had one, against 7 of 13 organiser maps).
    border = frozenset(("h", x, 0) for x in range(w)) | frozenset(("v", 0, y) for y in range(h))
    walled = rng.random() < 0.5
    for e in sorted(border) if walled else ():
        board.set_kelp(e)

    # Each call adds a pair and its mirror pair, so normal maps get 0-12 pairs and portal-heavy ones 8-16.
    sets = rng.randint(4, 8) if "portals" in quirks else rng.choice([0, 0, 0, 1, 1, 2, 3, 4, 6])
    add_portals(board, rng, sets)
    knock_holes(board, rng, border if walled else frozenset())

    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, "close_starts" in quirks):
        return None
    add_farms(board, rng, "far_farms" in quirks, "sparse" in quirks)
    return board if check(board) else None


# ---------------------------------------------------------------- trap: a pearl pocket behind narrow doors
#
# The idea of the Discord's Snake Trap and Labyrinth maps: nearly all pearls are in one walled pocket, which the
# dragons (starting outside it) reach only through a few doors, some at the end of 1-wide kelp corridors, and the
# outside is barren, sometimes a maze. Generated maps otherwise lay pearls and kelp independently.


# One attempt at a trap map; None when it fails the checks.
def build_trap(rng: random.Random) -> Board | None:
    w, h, sym = pick_shape(rng, set())
    board = Board(w, h, sym)
    for t in board.all_tiles():
        board.gaps[t] = (0, 0)
    for _ in range(rng.randint(2, 8)):
        gap = (1, rng.randint(150, 1000)) if rng.random() < 0.8 else (1, rng.randint(20, 100))
        for t in blob(board, rng, rng.randint(1, 4)):
            board.set_gap(t, gap)

    if rng.random() < 0.35:
        board.quirks.add("maze")
        lay_maze(board, rng)
    else:
        lay_kelp(board, rng, rng.uniform(0.0, 0.12))

    pocket = trap_pocket(board, rng)
    gap = (rng.randint(1, 3), rng.randint(7, 60))
    for t in pocket:
        board.set_gap(t, gap)
    walls = [
        (t, d)
        for t in sorted(pocket)
        for d, (dx, dy) in DIRS.items()
        if board.wrap(t[0] + dx, t[1] + dy) not in pocket
    ]
    for e in [e for e in board.edges if inside(board, e, pocket)]:
        del board.edges[e]
    for t, d in walls:
        board.set_kelp(board.side(t, d))
    for _ in range(rng.randint(1, 3)):
        add_door(board, rng, pocket, *rng.choice(walls))

    add_portals(board, rng, rng.choice([0, 0, 1, 2, 4]))
    knock_holes(board, rng)
    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, False, frozenset(pocket)):
        return None
    if any(t in pocket for body in board.dragons for t in body):
        return None
    if not all(pocket & board.reach(body[0]) for body in board.dragons):
        return None
    return board if check(board) else None


# A rectangle of tiles that is its own mirror: centred on each mirrored axis, anywhere on the other, at most half of
# each side.
def trap_pocket(board: Board, rng: random.Random) -> set[Tile]:
    def span(n: int, mirrored: bool) -> list[int]:
        size = rng.randint(max(3, n // 5), max(3, n // 2))
        start = (n - size) // 2 if mirrored else rng.randrange(n)
        if mirrored:
            size = n - 2 * start
        return [(start + i) % n for i in range(size)]

    xs = span(board.w, board.sym in ("y", "xy"))
    ys = span(board.h, board.sym in ("x", "xy"))
    return {(x, y) for x in xs for y in ys}


# Whether edge e lies between two tiles of `area` (so it is inside it, not on its border).
def inside(board: Board, e: Edge, area: set[Tile]) -> bool:
    o, x, y = e
    before = board.wrap(x, y - 1) if o == "h" else board.wrap(x - 1, y)
    return (x, y) in area and before in area


# Opens the pocket wall on side d of pocket tile t (and its mirror). Usually the door leads into a 1-wide corridor of
# 2-6 tiles walled with kelp on both sides, pointing straight out; kept to a plain door where it would reach the
# pocket again round the torus.
def add_door(board: Board, rng: random.Random, pocket: set[Tile], t: Tile, d: str) -> None:
    board.open_edge(board.side(t, d))
    length = rng.choice([0, 2, 3, 4, 6])
    dx, dy = DIRS[d]
    corridor = [board.wrap(t[0] + dx * i, t[1] + dy * i) for i in range(1, length + 1)]
    if any(c in pocket for c in corridor):
        return
    across = ("E", "W") if d in ("N", "S") else ("N", "S")
    for c in corridor:
        board.open_edge(board.side(c, d))
        for s in across:
            board.set_kelp(board.side(c, s))


# ---------------------------------------------------------------- dead ends: fast pearls at the end of small pockets
#
# The lesson of the ladder maps from Stronghold on (measured 2026-09-27 over maps/ladder, maps/retired and maps/sprint):
# their fast pearl tiles sit in dead ends, small pockets reached through one door 1 tile wide or 2 side by side
# (Stronghold and Trauma: an every-round tile at the end of a 1-wide corridor of 1-6 tiles; Dilemma and Autarky: pockets
# of 1-3 tiles; Slithery Fight: 1-wide corridors up to 27). A dragon that goes in to farm one must come back out through
# the door. dead_end is the measure (also `--deadends`); the deadend style builds such pockets on random boards.

FAST_GAP = 26  # a tile is fast when its mean spawn gap, (min + max) / 2, is at most this many rounds
DEAD_END_SHARE = 0.25  # a dead end holds at most this share of the board
Arc = tuple[Tile, str, Tile]  # a move from a tile, in a direction, to where it lands (portals included)
Moves = tuple[dict[Tile, list[Arc]], dict[Tile, list[Arc]]]  # moves(board): every move by its start and by its end


def fast(gap: tuple[int, int]) -> bool:
    return gap[1] > 0 and (gap[0] + gap[1]) / 2 <= FAST_GAP


# Every move on `board` (kelp blocks one, a portal carries it), by the tile it leaves and by the tile it reaches.
def moves(board: Board) -> Moves:
    out = {a: [(a, d, b) for d in DIRS if (b := board.step(a, d)) is not None and b != a] for a in board.all_tiles()}
    into: dict[Tile, list[Arc]] = {}
    for arcs in out.values():
        for arc in arcs:
            into.setdefault(arc[2], []).append(arc)
    return out, into


# The smallest set of tiles holding `sources` that fewer than `cap` moves leave on every way to `sinks` (a minimum cut
# nearest them, each move one unit of flow), with those moves; None when `cap` or more are needed (a source tile in
# the open). `graph` is moves(board).
def pocket(graph: Moves, sources: set[Tile], sinks: set[Tile], cap: int = 4) -> tuple[set[Tile], list[Arc]] | None:
    out, into = graph
    used: set[Arc] = set()
    for _ in range(cap):
        prev: dict[Tile, tuple[Tile, Arc, bool] | None] = dict.fromkeys(sources)
        todo, hit = deque(sources), None
        while todo and hit is None:
            a = todo.popleft()
            steps = [(arc[2], arc, True) for arc in out[a] if arc not in used]
            steps += [(arc[0], arc, False) for arc in into.get(a, []) if arc in used]  # undoing a used move
            for b, arc, forward in steps:
                if b not in prev:
                    prev[b] = (a, arc, forward)
                    if b in sinks:
                        hit = b
                        break
                    todo.append(b)
        if hit is None:
            return set(prev), [arc for a in prev for arc in out[a] if arc[2] not in prev]
        while (link := prev[hit]) is not None:
            a, arc, forward = link
            (used.add if forward else used.discard)(arc)
            hit = a
    return None


# Whether a cut is a door: one move, or two in the same direction from two neighbouring tiles (2 wide).
def door(board: Board, cut: list[Arc]) -> bool:
    if len(cut) == 1:
        return True
    if len(cut) != 2 or cut[0][1] != cut[1][1]:
        return False
    (a, _, _), (b, _, _) = cut
    return any(board.wrap(a[0] + dx, a[1] + dy) == b for dx, dy in DIRS.values())


# Whether tile t is in a dead end: off every dragon's start (`starts`), in a pocket (its smallest cut from all the
# starts) of at most DEAD_END_SHARE of the board whose door is 1 tile or 2 side by side.
def dead_end(board: Board, t: Tile, starts: set[Tile], graph: Moves | None = None) -> bool:
    if t in starts:
        return False
    found = pocket(graph or moves(board), {t}, starts)
    return found is not None and len(found[0]) <= board.w * board.h * DEAD_END_SHARE and door(board, found[1])


# (fast tiles in a dead end, fast tiles) on `board`.
def dead_end_count(board: Board) -> tuple[int, int]:
    starts = {t for body in board.dragons for t in body}
    tiles = [t for t in board.all_tiles() if fast(board.gaps[t])]
    graph = moves(board) if tiles else None
    return sum(dead_end(board, t, starts, graph) for t in tiles), len(tiles)


# One attempt at a deadend map: a random board as `build` makes (its sizes, symmetries, kelp and portals, sometimes
# one of its tiny, huge, maze or portal-heavy quirks; its own fast tiles slowed), several mirrored dead-end pockets with
# fast pearls at their far end, then dragons outside them and now and then a small fast farm in the open, so fast
# doesn't always mean a dead end. None when it fails the reachability check or fewer than half its fast tiles are in
# dead ends (a pocket walled off by another, or a farm too big).
def build_deadend(rng: random.Random) -> Board | None:
    quirks = {rng.choice(("tiny", "huge", "maze", "portals"))} if rng.random() < 0.25 else set()
    w, h, sym = pick_shape(rng, quirks)
    board = Board(w, h, sym, quirks=quirks)
    paint_tiles(board, rng, set())
    for t, (lo, hi) in board.gaps.items():
        if fast((lo, hi)):
            board.gaps[t] = (lo, max(hi, 2 * FAST_GAP + 2 - lo))  # both mirror tiles map alike: still symmetric
    if "maze" in quirks:
        lay_maze(board, rng)
    else:
        lay_kelp(board, rng, 0.0 if rng.random() < 0.1 else rng.uniform(0.03, 0.2))
    add_portals(board, rng, rng.randint(4, 8) if "portals" in quirks else rng.choice([0, 0, 0, 1, 1, 2, 3]))
    knock_holes(board, rng)

    inside: set[Tile] = set()  # pocket tiles, mirrors included
    blocked: set[Tile] = set()  # those and the tiles at their doors
    for _ in range(rng.randint(1, 3) if w * h < 400 else rng.randint(2, 5)):
        for _attempt in range(40):
            if carved := carve_pocket(board, rng, blocked):
                tiles, entrances = carved
                inside |= {m for t in tiles for m in (t, board.mirror(t))}
                blocked |= inside | {m for t in entrances for m in (t, board.mirror(t))}
                break
    if not inside:
        return None
    knock_holes(board, rng, frozenset(board.side(t, d) for t in inside for d in DIRS))

    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, "close_starts" in quirks, frozenset(blocked)):
        return None
    if rng.random() < 0.3:
        gap = (1, rng.randint(1, 20))
        for t in blob(board, rng, rng.randint(1, 3)):
            if t not in inside:
                board.set_gap(t, gap)
    if not check(board):
        return None
    passing, total = dead_end_count(board)
    return board if total and passing >= total / 2 else None


# Carves one pocket (and its mirror) off the `blocked` tiles, walled in kelp but for its door, with fast pearls at
# its far end: mostly a 1-wide corridor of 1-6 tiles, a fifth of those up to 27; a quarter of pockets are 2 wide and 2-5
# rows deep, their fast tiles one row short of the far end (a far-row corner is its own smallest cut there, two moves
# in different directions, so dead_end wouldn't count it). Returns its tiles from the door inward and the tiles just
# outside its door, or None when the spot drawn doesn't fit (or leaves `region`, when given).
def carve_pocket(board: Board, rng: random.Random, blocked: set[Tile],
                 region: set[Tile] | None = None) -> tuple[list[Tile], list[Tile]] | None:
    area = board.w * board.h
    d = rng.choice(list(DIRS))
    dx, dy = DIRS[d]
    entrance = (rng.randrange(board.w), rng.randrange(board.h))
    if rng.random() < 0.25:
        ax, ay = (1, 0) if d in "NS" else (0, 1)
        rows = [[board.wrap(entrance[0] + dx * i + ax * j, entrance[1] + dy * i + ay * j) for j in (0, 1)]
                for i in range(1, rng.randint(2, 5) + 1)]
        tiles, ends = [t for row in rows for t in row], rows[-2]
        entrances = [entrance, board.wrap(entrance[0] + ax, entrance[1] + ay)]
    else:
        length = rng.randint(1, 6) if rng.random() < 0.8 else rng.randint(7, 27)
        if not (tiles := corridor(board, rng, entrance, d, min(length, max(1, area // 8)))):
            return None
        ends, entrances = tiles[-rng.randint(1, min(2, len(tiles))):], [entrance]
    mine = set(tiles) | set(entrances)
    if len(mine) != len(tiles) + len(entrances) or len(tiles) > area * DEAD_END_SHARE / 2:
        return None
    if mine & (blocked | {board.mirror(t) for t in mine}) or (region is not None and not mine <= region):
        return None
    # No portal on a pocket tile's side: it would be a second way out, and walling it would break its pair.
    if any(board.edges.get(board.side(t, s), KELP) != KELP for t in tiles for s in DIRS):
        return None
    doors = {board.side(t, d) for t in entrances}
    for t in tiles:
        for s, (sx, sy) in DIRS.items():
            e = board.side(t, s)
            if board.wrap(t[0] + sx, t[1] + sy) in tiles or e in doors:
                board.open_edge(e)
            else:
                board.set_kelp(e)
    lo = rng.randint(1, 8)
    gap = (1, 1) if rng.random() < 0.57 else (lo, rng.randint(lo, 2 * FAST_GAP - lo))
    for t in ends:
        board.set_gap(t, gap)
    return tiles, entrances


# A 1-wide corridor of `length` tiles from beside `entrance`, heading `d` and turning now and then. Each tile touches
# no earlier one but the one before it, so it has no loops or side openings of its own; None when it would.
def corridor(board: Board, rng: random.Random, entrance: Tile, d: str, length: int) -> list[Tile] | None:
    tiles: list[Tile] = []
    here = entrance
    for _ in range(length):
        if tiles and rng.random() < 0.3:
            d = rng.choice(("E", "W") if d in "NS" else ("N", "S"))
        here = board.wrap(here[0] + DIRS[d][0], here[1] + DIRS[d][1])
        touching = {board.wrap(here[0] + x, here[1] + y) for x, y in DIRS.values()}
        if here in tiles or here == entrance or touching & set(tiles[:-1]):
            return None
        tiles.append(here)
    return tiles


# ---------------------------------------------------------------- fortress: walled halves joined through a few gates
#
# The shape our five worst ladder maps share (Devil, Stronghold, Trauma, Portals, Slithery Fight; 2026-09-27: 44-43
# there against 196-74 on the other eight, per-map win rate 0.47 against 0.62, p = 0.001 of 23 features tried): a
# walled border, the board cut into the two teams' halves joined by only 1-5 passable moves or only through portals,
# most tiles barren, the fast tiles in 1-wide lanes and dead ends, and 2-4 short dragons a team. Random layouts never
# had it (0 of 33 mix and trap maps); fortress_shape is the measure and the fortress style builds it.

FORTRESS_CUT = (1, 5)  # the halves are joined by this many moves (or only through portals)


# (team A's start tiles, team B's): the map's dragons alternate A, B.
def team_tiles(board: Board) -> tuple[set[Tile], set[Tile]]:
    return ({t for body in board.dragons[::2] for t in body}, {t for body in board.dragons[1::2] for t in body})


# The fewest moves that cut every way from team A's start tiles to team B's (portals included), up to `cap`.
def halves_cut(board: Board, cap: int = 12, graph: Moves | None = None) -> int:
    a, b = team_tiles(board)
    found = pocket(graph or moves(board), a, b, cap)
    return cap if found is None else len(found[1])


# Whether team A's start tiles reach none of team B's without portals: the halves meet only through portals.
def portal_only(board: Board) -> bool:
    a, b = team_tiles(board)
    seen, todo = set(a), deque(a)
    while todo:
        t = todo.popleft()
        for d in DIRS:
            if (n := board.plain_step(t, d)) is not None and n not in seen:
                seen.add(n)
                todo.append(n)
    return not seen & b


# (fast tiles in a 1-wide lane or a dead end, fast tiles): a lane tile has at most two moves out.
def lane_count(board: Board, graph: Moves | None = None) -> tuple[int, int]:
    graph = graph or moves(board)
    starts = {t for body in board.dragons for t in body}
    tiles = [t for t in board.all_tiles() if fast(board.gaps[t])]
    return sum(len(graph[0][t]) <= 2 or dead_end(board, t, starts, graph) for t in tiles), len(tiles)


# Whether `board` has the fortress shape: halves joined by FORTRESS_CUT moves or only through portals, and at least
# half its fast tiles (at least one) in lanes or dead ends.
def fortress_shape(board: Board) -> bool:
    graph = moves(board)
    low, high = FORTRESS_CUT
    lanes, total = lane_count(board, graph)
    joined = low <= halves_cut(board, high + 1, graph) <= high or portal_only(board)
    return joined and total > 0 and lanes >= total / 2


# A fortress map (the shape above) on a random board of `build`'s sizes and symmetries, split into the two teams'
# halves across its mirror line. A third are the portal kind (Stronghold, Trauma, Portals): the wall between the halves
# is whole, each team starts in a walled box it leaves only through portals, the halves meet only through portals, and
# the fast tiles sit in small walled boxes entered only through one. The rest are the gate kind (Devil, Slithery
# Fight): the halves meet through 1-5 gaps in the wall, some of them 1-wide lanes of fast tiles, and more fast tiles
# sit in dead-end pockets (carve_pocket). The kind is drawn once, since the portal kind fails more attempts; None when
# 50 attempts at it fail the reachability check or fortress_shape.
def build_fortress(rng: random.Random) -> Board | None:
    portal = rng.random() < 1 / 3
    for _attempt in range(50):
        if board := fortress_attempt(rng, portal):
            return board
    return None


def fortress_attempt(rng: random.Random, portal: bool) -> Board | None:
    w, h, sym = pick_shape(rng, set())
    split_x = sym in ("y", "xy")
    w, h = (w - w % 2, h) if split_x else (w, h - h % 2)  # an even side, so the halves' wall is its own mirror
    board = Board(w, h, sym)
    area, span, other = w * h, (w if split_x else h), (h if split_x else w)
    for t in board.all_tiles():
        board.gaps[t] = (0, 0)
    target = rng.uniform(0.08, 0.3)  # most tiles barren
    for _ in range(200):
        if sum(g[1] > 0 for g in board.gaps.values()) >= target * area:
            break
        gap = (1, rng.randint(150, 1000))
        for t in blob(board, rng, rng.randint(3, max(4, area // 25))):
            board.set_gap(t, gap)
    lay_kelp(board, rng, rng.uniform(0.02, 0.1))
    half = {t for t in board.all_tiles() if (t[0] if split_x else t[1]) < span // 2}
    divider = [("v", span // 2, v) for v in range(h)] if split_x else [("h", v, span // 2) for v in range(w)]
    wall = sorted({("h", x, 0) for x in range(w)} | {("v", 0, y) for y in range(h)} | set(divider))
    for e in wall:
        board.set_kelp(e)
    keep = set(wall)
    blocked: set[Tile] = set()  # team A's tiles taken by a box, lane or pocket
    box: set[Tile] | None = None
    if portal:
        box = walled_box(board, rng, half, blocked, (max(2, span // 8), max(3, span // 4)),
                         (max(4, other // 4), max(4, other // 2)), keep)
        if box is None or len(box) < 24:
            return None
        blocked |= box
        free = half - blocked
        for _ in range(rng.randint(1, 2)):  # the start box's ways out
            if not portal_pair(board, rng, wall_edges(board, box, free), open_edges(board, free)):
                return None
        mirrored = {board.mirror(t) for t in free}
        for _ in range(rng.randint(1, 2)):  # the halves' only ways to each other
            if not portal_pair(board, rng, open_edges(board, free), open_edges(board, mirrored)):
                return None
        for _ in range(rng.randint(1, 3)):  # fast fields, each entered through one portal
            if not (field := walled_box(board, rng, half, blocked, (1, 3), (2, 4), keep)):
                continue
            blocked |= field
            rest = half - blocked
            if not portal_pair(board, rng, wall_edges(board, field, rest), open_edges(board, rest)):
                return None
            gap = pearl_gap(rng)
            for t in field:
                board.set_gap(t, gap)
    else:
        gates = rng.randint(1, 5)
        for _attempt in range(50):
            if gates <= 0:
                break
            e = rng.choice(divider)
            cost = 1 if board.mirror_edge(e) == e else 2  # the move it opens, and its mirror's
            if board.edges.get(e) != KELP or cost > gates:
                continue
            board.open_edge(e)
            gates -= cost
            if rng.random() < 0.5:
                blocked |= lane(board, rng, e, split_x, span // 2, blocked, keep)
        for _ in range(rng.randint(1, 4)):
            for _attempt in range(40):
                if carved := carve_pocket(board, rng, blocked, half):
                    tiles, entrances = carved
                    blocked |= set(tiles) | set(entrances)
                    sides = {board.side(t, d) for t in tiles for d in DIRS}
                    keep |= sides | {board.mirror_edge(e) for e in sides}
                    break
    knock_holes(board, rng, frozenset(keep))
    if not place_dragons(board, rng, rng.choice([2, 3, 4]), False, frozenset(blocked - (box or set())), box):
        return None
    return board if check(board) and fortress_shape(board) else None


# A pearl range for a fast tile: every round 57% of the time (the ladder maps' share), else a mean gap up to FAST_GAP.
def pearl_gap(rng: random.Random) -> tuple[int, int]:
    lo = rng.randint(1, 8)
    return (1, 1) if rng.random() < 0.57 else (lo, rng.randint(lo, 2 * FAST_GAP - lo))


# A rectangle of team A's `half` off `blocked`, `along` tiles along the split axis and `across` across it (each a
# (lowest, highest) range), cleared of kelp inside and walled in kelp with its mirror; its wall edges and their mirrors
# join `keep`. None when the spot drawn doesn't fit or would touch a portal.
def walled_box(board: Board, rng: random.Random, half: set[Tile], blocked: set[Tile], along: tuple[int, int],
               across: tuple[int, int], keep: set[Edge]) -> set[Tile] | None:
    split_x = board.sym in ("y", "xy")
    a, b = rng.randint(*along), rng.randint(*across)
    u0, v0 = rng.randrange((board.w if split_x else board.h) // 2), rng.randrange(board.h if split_x else board.w)
    tiles = {(u0 + i, v0 + j) if split_x else (v0 + j, u0 + i) for i in range(a) for j in range(b)}
    if not tiles <= half or tiles & blocked:
        return None
    if any(board.edges.get(board.side(t, d), KELP) >= 0 for t in tiles for d in DIRS):
        return None
    for t in sorted(tiles):
        for d, (dx, dy) in DIRS.items():
            if board.wrap(t[0] + dx, t[1] + dy) in tiles:
                board.open_edge(board.side(t, d))
    for e in wall_edges(board, tiles, None):
        board.set_kelp(e)
        keep |= {e, board.mirror_edge(e)}
    return tiles


# The edges between `tiles` and their neighbours outside them (only those in `outside`, unless None).
def wall_edges(board: Board, tiles: set[Tile], outside: set[Tile] | None) -> list[Edge]:
    return sorted(board.side(t, d) for t in tiles for d, (dx, dy) in DIRS.items()
                  if (n := board.wrap(t[0] + dx, t[1] + dy)) not in tiles and (outside is None or n in outside))


# The open edges between two tiles of `region`, off the mirror line.
def open_edges(board: Board, region: set[Tile]) -> list[Edge]:
    return sorted(e for t in region for d in ("E", "S")
                  if board.wrap(t[0] + DIRS[d][0], t[1] + DIRS[d][1]) in region
                  and (e := board.side(t, d)) not in board.edges and board.mirror_edge(e) != e)


# Makes a portal pair of an edge from `first` and one of the same orientation from `second` (either may be kelp, which
# it replaces), and the mirror pair; False when no such pair fits in a few tries.
def portal_pair(board: Board, rng: random.Random, first: list[Edge], second: list[Edge]) -> bool:
    for _attempt in range(30):
        if not first or not second:
            return False
        e1 = rng.choice(first)
        options = [e for e in second if e[0] == e1[0]]
        if not options:
            continue
        e2 = rng.choice(options)
        ends = {e1, e2, board.mirror_edge(e1), board.mirror_edge(e2)}
        if len(ends) == 4 and not any(board.edges.get(e, KELP) >= 0 for e in ends):
            board.edges[e1] = board.edges[e2] = board.next_portal
            board.edges[board.mirror_edge(e1)] = board.edges[board.mirror_edge(e2)] = board.next_portal + 1
            board.next_portal += 2
            return True
    return False


# Makes a 1-wide lane of fast tiles through the opened wall edge `e`: 1-4 tiles on each side along the split axis,
# walled on both long sides; its side walls join `keep`. Returns its tiles and its mirror's in team A's half, or none
# when it would cross `blocked` or a portal.
def lane(board: Board, rng: random.Random, e: Edge, split_x: bool, middle: int, blocked: set[Tile],
         keep: set[Edge]) -> set[Tile]:
    reach = rng.randint(1, 4)
    v = e[2] if split_x else e[1]
    tiles = [(u, v) if split_x else (v, u) for u in range(middle - reach, middle + reach)]
    both = set(tiles) | {board.mirror(t) for t in tiles}
    mine = {t for t in both if (t[0] if split_x else t[1]) < middle}
    sides, along = (("N", "S"), "E") if split_x else (("E", "W"), "S")
    if mine & blocked or any(board.edges.get(board.side(t, d), KELP) >= 0 for t in tiles for d in (*sides, along)):
        return set()
    gap = pearl_gap(rng)
    for t in tiles:
        board.set_gap(t, gap)
        if t != tiles[-1]:
            board.open_edge(board.side(t, along))
        for d in sides:
            board.set_kelp(board.side(t, d))
            keep |= {board.side(t, d), board.mirror_edge(board.side(t, d))}
    return mine


# ---------------------------------------------------------------- portal rooms: fast pearls reached only through portals
#
# The lesson of Portals (ladder, 2026-09-27; measured over maps/ladder, maps/retired and maps/sprint): at least half its
# fast tiles are in small walled rooms whose only ways in are portals, as on Queen Of Spades, Schooltime, Stronghold and
# Trauma (5 of 13 organiser maps; eval random layouts 11 of 120), and its two teams are sealed in their own halves and
# never meet (the only such organiser map; eval 0 of 180, since near variants of Portals break the seal). A bot that
# walks only open ground never eats there, and one that plans for combat on a sealed map wastes its opening.
# portal_gated_count and sealed are the measures; the portalrooms style builds both on random boards.


# Tiles reached from `starts` by moves that `through` allows (Board.step: portals too; Board.plain_step: none).
def spread(board: Board, starts: set[Tile], through: Callable[[Tile, str], Tile | None]) -> set[Tile]:
    seen, todo = set(starts), deque(starts)
    while todo:
        t = todo.popleft()
        for d in DIRS:
            if (n := through(t, d)) is not None and n not in seen:
                seen.add(n)
                todo.append(n)
    return seen


# (fast tiles the dragons' start tiles reach only through a portal, fast tiles) on `board`.
def portal_gated_count(board: Board) -> tuple[int, int]:
    starts = {t for body in board.dragons for t in body}
    tiles = [t for t in board.all_tiles() if fast(board.gaps[t])]
    walk, full = spread(board, starts, board.plain_step), spread(board, starts, board.step)
    return sum(t in full and t not in walk for t in tiles), len(tiles)


# Whether team A's start tiles reach none of team B's, portals included: the teams can never meet.
def sealed(board: Board) -> bool:
    a, b = team_tiles(board)
    return not spread(board, a, board.step) & b


# One attempt at a portalrooms map: a random board as `build` makes (its sizes, symmetries and kelp; its own fast tiles
# slowed), with 2-6 walled rooms of 1-9 fast tiles a team, each entered only through a portal from the open, and now and
# then a small fast farm in the open. A third are sealed like Portals: a kelp wall round the board and between the
# teams' halves, every portal inside one half, each team reaching about half the board. None when it fails the
# reachability check or fewer than half its fast tiles are reached only through portals.
def build_portalrooms(rng: random.Random) -> Board | None:
    closed = rng.random() < 1 / 3
    w, h, sym = pick_shape(rng, set())
    split_x = sym in ("y", "xy")
    if closed:
        w, h = (w - w % 2, h) if split_x else (w, h - h % 2)  # an even side, so the halves' wall is its own mirror
    board = Board(w, h, sym, quirks={"sealed"} if closed else set())
    paint_tiles(board, rng, set())
    for t, (lo, hi) in board.gaps.items():
        if fast((lo, hi)):
            board.gaps[t] = (lo, max(hi, 2 * FAST_GAP + 2 - lo))
    lay_kelp(board, rng, 0.0 if rng.random() < 0.1 else rng.uniform(0.03, 0.2))
    span = w if split_x else h
    half = {t for t in board.all_tiles() if (t[0] if split_x else t[1]) < span // 2}
    border = {("h", x, 0) for x in range(w)} | {("v", 0, y) for y in range(h)}
    keep: set[Edge] = set()
    if closed:
        divider = {("v", span // 2, v) for v in range(h)} if split_x else {("h", v, span // 2) for v in range(w)}
        keep = border | divider
    elif rng.random() < 0.5:
        keep = set(border)
    for e in sorted(keep):
        board.set_kelp(e)
    if not closed:
        add_portals(board, rng, rng.choice([0, 0, 1, 2, 3]))

    rooms: set[Tile] = set()  # team A's rooms
    for _ in range(rng.randint(2, 6)):
        for _attempt in range(40):
            if room := walled_box(board, rng, half, rooms, (1, 3), (1, 3), keep):
                break
        else:
            continue
        rooms |= room
        free = (half if closed else set(board.all_tiles())) - rooms - {board.mirror(t) for t in rooms}
        if not portal_pair(board, rng, wall_edges(board, room, free), open_edges(board, free)):
            return None
        gap = pearl_gap(rng)
        for t in room:
            board.set_gap(t, gap)
    if not rooms:
        return None
    if closed:
        free = half - rooms
        for _ in range(rng.randint(0, 3)):  # portals within a half, as Portals has
            portal_pair(board, rng, open_edges(board, free), open_edges(board, free))
    knock_holes(board, rng, frozenset(keep), MIN_REACH / 2 if closed else MIN_REACH)

    inside = frozenset(rooms | {board.mirror(t) for t in rooms})
    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, False, inside, half if closed else None):
        return None
    if rng.random() < 0.3:
        gap = (1, rng.randint(1, 20))
        for t in blob(board, rng, rng.randint(1, 3)):
            if t not in inside:
                board.set_gap(t, gap)
    if not check(board, MIN_REACH / 2 if closed else MIN_REACH) or sealed(board) != closed:
        return None
    gated, total = portal_gated_count(board)
    return board if total and gated >= total / 2 else None


# ---------------------------------------------------------------- macro: one long starter and a few short ones
#
# The lesson of Slithery Fight (ladder, 2026-09-27), after Dilemma and Autarky (the Sprint-like maps; the organisers,
# 2026-09-25: more "macro" maps are coming and maps will stop "making the dragons for you"): each team starts with one
# dragon of 10 or more (11, 14, 25) beside several of 2-7, and on Slithery Fight the long one is coiled round its own
# head, which has no free move until the dragon splits or its tail clears. 3 of 13 organiser maps start a dragon of 10+;
# eval only on near and community copies of those layouts (18 of 180), random layouts never (their starters are 3-6).
# long_start is the measure (boxed_heads the coil's); the macro style builds such starts on random boards.

LONG_START = 10  # a starting dragon this long or longer is a long starter


# The longest starting dragon on `board`.
def long_start(board: Board) -> int:
    return max((len(body) for body in board.dragons), default=0)


# One attempt at a macro map: a random board as `build` makes (sizes, symmetries, kelp, portals, a walled border half
# the time, fast farms), whose teams start with one dragon of LONG_START-25 (longer on bigger boards) at a random place
# in their order and 1-6 of 2-7. A third of the long ones are coiled (coil); the rest are laid as lay_body lays any.
def build_macro(rng: random.Random) -> Board | None:
    w, h, sym = pick_shape(rng, set())
    board = Board(w, h, sym)
    paint_tiles(board, rng, set())
    lay_kelp(board, rng, 0.0 if rng.random() < 0.1 else rng.uniform(0.03, 0.2))
    border = frozenset(("h", x, 0) for x in range(w)) | frozenset(("v", 0, y) for y in range(h))
    walled = rng.random() < 0.5
    for e in sorted(border) if walled else ():
        board.set_kelp(e)
    add_portals(board, rng, rng.choice([0, 0, 0, 1, 1, 2, 3, 4]))
    knock_holes(board, rng, border if walled else frozenset())

    long = rng.randint(LONG_START, max(LONG_START, min(25, w * h // 40)))
    lengths = [rng.randint(2, 7) for _ in range(rng.randint(1, 6))]
    first = rng.randint(0, len(lengths))
    lengths.insert(first, long)
    coiled = rng.random() < 1 / 3
    if coiled:
        board.quirks.add("coil")
    main = largest_region(board)
    zone = [t for t in team_a_zone(board, False) if t in main]
    if not zone:
        return None
    taken: set[Tile] = set()
    for i, length in enumerate(lengths):
        for _attempt in range(300):
            head = rng.choice(zone)
            body = coil(board, rng, head, length, taken) if coiled and i == first else lay_body(
                board, rng, head, length, taken)
            if body is not None:
                mirrored = [board.mirror(t) for t in body]
                board.dragons += [body, mirrored]
                taken |= set(body) | set(mirrored)
                break
        else:
            return None
    add_farms(board, rng, False, False)
    return board if check(board) else None


# A body of `length` coiled outward from `head` in a square spiral (turning one way, from a random first direction), so
# from 9 tiles on its head is walled in by its own body, as Slithery Fight's 25-long starters are; kelp between its
# segments is cleared (with its mirror). None when a tile is taken, meets its mirror or a portal lies between two.
def coil(board: Board, rng: random.Random, head: Tile, length: int, taken: set[Tile]) -> list[Tile] | None:
    order = list(DIRS)  # N E S W: clockwise
    if rng.random() < 0.5:
        order.reverse()
    turn = rng.randrange(4)
    body, run = [head], 1
    while len(body) < length:
        for _ in range(2):
            dx, dy = DIRS[order[turn % 4]]
            for _ in range(run):
                if len(body) < length:
                    body.append(board.wrap(body[-1][0] + dx, body[-1][1] + dy))
            turn += 1
        run += 1
    if len(set(body)) != length or not all(ok_tile(board, t, body, taken) for t in body):
        return None
    joints = [board.side(a, d) for a, b in zip(body, body[1:]) for d in DIRS if board.wrap(
        a[0] + DIRS[d][0], a[1] + DIRS[d][1]) == b]
    if any(board.edges.get(e, KELP) >= 0 for e in joints):
        return None
    for e in joints:
        board.open_edge(e)
    return body


# ---------------------------------------------------------------- portal bets: death portals and vaults
#
# sig, 2026-09-29: "can one of our tests maps always be an autokill portal so if someone goes in they automatically
# die. then another map where theres a portal to a super pearl rich area with lots of space that is otherwise walled
# off?", then a combined map, "9 auto kill portals and 1 good portal". A step through a portal lands beyond its partner
# edge and is checked against the dragon's own body first (docs/unsw/execution-order.md), so a one-tile cell walled in
# kelp on three sides whose fourth side is a portal kills whatever enters: its only other move goes back through the
# portal onto the dragon's own neck. Portals (ladder) has such cells, Trauma the opposite, a big room worth entering
# that only a portal reaches, and a dragon can't see through a portal (docs/unsw/vision.md): telling the two apart takes
# a scout, sonar or memory. death_cells and vault_tiles are the measures; the deathportal, vault and portalbet styles
# build them on ordinary boards.

VAULT_SHARE = (0.075, 0.15)  # each team's vault holds this share of the board, so the two hold 15-30% of it
VAULT_MIN = 20  # a vault is a region of at least this many tiles that the starts reach only through a portal
PORTALBET_DEATHS = 9  # a portalbet map's death portals a team, beside its one vault portal (sig, 2026-09-29)
PORTALBET_MIN_AREA = 1600  # and its smallest board, so ten mouths a team fit apart
VISION = 3  # a dragon sees tiles this far from its head, Chebyshev (docs/unsw/vision.md)


# The tiles a kelp-walled portal cell kills in: kelp on three sides, a portal on the fourth, off every start.
def death_cells(board: Board) -> set[Tile]:
    starts = {t for body in board.dragons for t in body}
    out = set()
    for t in board.all_tiles():
        kinds = [board.edges.get(board.side(t, d)) for d in DIRS]
        if t not in starts and kinds.count(KELP) == 3 and any(k is not None and k >= 0 for k in kinds):
            out.add(t)
    return out


# The tiles in a vault: regions of VAULT_MIN or more tiles, connected by plain steps, that the dragons' start tiles
# reach only through a portal.
def vault_tiles(board: Board) -> set[Tile]:
    starts = {t for body in board.dragons for t in body}
    gated = spread(board, starts, board.step) - spread(board, starts, board.plain_step)
    out: set[Tile] = set()
    for t in sorted(gated):
        if t not in out and len(region := spread(board, {t}, lambda a, d: n if (n := board.plain_step(a, d)) in gated
                                                 else None)) >= VAULT_MIN:
            out |= region
    return out


# The two tiles edge e lies between.
def edge_tiles(board: Board, e: Edge) -> set[Tile]:
    o, x, y = e
    return {(x, y), board.wrap(x, y - 1) if o == "h" else board.wrap(x - 1, y)}


def chebyshev(board: Board, a: Tile, b: Tile) -> int:
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return max(min(dx, board.w - dx), min(dy, board.h - dy))


# Where dragons plausibly go, for a portal's mouth: within 2 tiles of the board's best pearls outside `inside` (its
# fastest-spawning tenth of spawning tiles, at least 4) and on a shortest way from team A's heads to team B's start
# tiles, both mirrored, never in `inside`.
def lure_tiles(board: Board, inside: set[Tile]) -> set[Tile]:
    spawning = sorted((t for t in board.all_tiles() if board.gaps[t][1] > 0 and t not in inside),
                      key=lambda t: (sum(board.gaps[t]), t))
    out = set(spawning[:max(4, len(spawning) // 10)])
    for _ in range(2):
        out |= {board.wrap(x + dx, y + dy) for x, y in out for dx, dy in DIRS.values()}
    heads, (_, b) = {body[0] for body in board.dragons[::2]}, team_tiles(board)
    prev: dict[Tile, Tile | None] = dict.fromkeys(heads)
    todo, hit = deque(sorted(heads)), None
    while todo and hit is None:
        t = todo.popleft()
        for d in DIRS:
            if (n := board.step(t, d)) is not None and n not in prev:
                prev[n] = t
                if n in b:
                    hit = n
                    break
                todo.append(n)
    while hit is not None:
        out.add(hit)
        hit = prev[hit]
    return (out | {board.mirror(t) for t in out}) - inside


# Open edges off the mirror line between two tiles of `region`, one of them in `lure`, that no dragon's body crosses:
# where a portal's mouth may go.
def mouth_edges(board: Board, region: set[Tile], lure: set[Tile]) -> list[Edge]:
    body = body_edges(board)
    return [e for e in open_edges(board, region) if e not in body and edge_tiles(board, e) & lure]


# Makes open edge `mouth` a death portal, and its mirror a mirrored one: its partner is the edge between two one-tile
# cells, each walled in kelp on its other three sides, so it kills from either side (a portal is two-sided,
# docs/unsw/kelp-and-portals.md). The cells are off `blocked`, off every portal and never in one view with either mouth
# (more than 2 * VISION tiles from both, Chebyshev: no dragon between them sees the pair at once), and baited with fast
# pearls (pearl_gap) half the time. Returns team A's cells, or None when no spot fits in a few tries.
def death_portal(board: Board, rng: random.Random, mouth: Edge, blocked: set[Tile]) -> set[Tile] | None:
    ends = edge_tiles(board, mouth) | edge_tiles(board, board.mirror_edge(mouth))
    for _attempt in range(60):
        partner: Edge = (mouth[0], rng.randrange(board.w), rng.randrange(board.h))
        cells = edge_tiles(board, partner)
        mirrored = {board.mirror(t) for t in cells}
        if cells & (blocked | mirrored) or min(torus_dist(board, a, b) for a in cells for b in mirrored) < 2:
            continue
        if min(chebyshev(board, a, b) for a in cells | mirrored for b in ends) <= 2 * VISION:
            continue
        walls = sorted({board.side(t, d) for t in cells for d in DIRS} - {partner})
        pair = {mouth, partner, board.mirror_edge(mouth), board.mirror_edge(partner)}
        if len(pair) < 4 or any(board.edges.get(e, KELP) >= 0 for e in [*walls, partner, board.mirror_edge(mouth)]):
            continue
        for e in walls:
            board.set_kelp(e)
        board.edges[mouth] = board.edges[partner] = board.next_portal
        board.edges[board.mirror_edge(mouth)] = board.edges[board.mirror_edge(partner)] = board.next_portal + 1
        board.next_portal += 2
        gap = pearl_gap(rng) if rng.random() < 0.5 else (0, 0)
        for t in cells:
            board.set_gap(t, gap)
        return cells
    return None


# Team A's vault and its mirror: a rectangle of VAULT_SHARE of the board (both bounds held: a board too narrow for
# the lower one returns None) in team A's `half`, at least a tile short of
# its far side and two of the other side's length, so the half has a way round it, cleared of kelp inside and walled
# in kelp (its walls join `keep`), every tile fast pearls (pearl_gap, with up to three blobs of another fast range).
# Placed before any portal. None on a board too small for one.
def vault_box(board: Board, rng: random.Random, half: set[Tile], keep: set[Edge]) -> set[Tile] | None:
    split_x = board.sym in ("y", "xy")
    span, other = (board.w, board.h) if split_x else (board.h, board.w)
    along, (lo, hi) = span // 2 - 1, (board.w * board.h * share for share in VAULT_SHARE)
    n = rng.uniform(lo, hi)
    if along < 3 or other < 5:
        return None
    a = rng.randint(min(along, max(3, math.ceil(n / (other - 2)))), along)
    b = max(3, min(other - 2, math.floor(hi / a), max(math.ceil(lo / a), round(n / a))))
    if not lo <= a * b <= hi:
        return None
    u0, v0 = rng.randint(0, span // 2 - a), rng.randrange(other)
    box = {(u0 + i, (v0 + j) % other) if split_x else ((v0 + j) % other, u0 + i) for i in range(a) for j in range(b)}
    for t in sorted(box):
        for d, (dx, dy) in DIRS.items():
            if board.wrap(t[0] + dx, t[1] + dy) in box:
                board.open_edge(board.side(t, d))
    for e in wall_edges(board, box, None):
        board.set_kelp(e)
        keep |= {e, board.mirror_edge(e)}
    gap = pearl_gap(rng)
    for t in box:
        board.set_gap(t, gap)
    for _ in range(rng.randint(0, 3)):
        gap = pearl_gap(rng)
        for t in blob(board, rng, rng.randint(3, max(4, len(box) // 4)), rng.choice(sorted(box))):
            if t in box:
                board.set_gap(t, gap)
    return box


# One attempt at a deathportal map: a board as `build` makes it (normal, no quirk), then 1-3 death portals a team
# (death_portal) whose mouths are on lure tiles, beside the best pearls or on the way to the other team.
def build_deathportal(rng: random.Random) -> Board | None:
    if (board := build(rng, "normal")) is None:
        return None
    starts, cells = {t for body in board.dragons for t in body}, set()
    lure = lure_tiles(board, set())
    for _ in range(rng.randint(1, 3)):
        free = set(board.all_tiles()) - cells
        if not (mouths := mouth_edges(board, free, lure)) or not (
                placed := death_portal(board, rng, rng.choice(mouths), starts | cells)):
            return None
        cells |= placed | {board.mirror(t) for t in placed}
    return board if bets_ok(board, cells, set()) else None


# One attempt at a vault map: a random board of `build`'s sizes, symmetries and kelp (a walled border half the time),
# 0-3 ordinary portal pairs, each team's vault (vault_box) in its own half, entered only through 1-2 portals whose
# mouths are on lure tiles of that half, and in two maps of five a decoy: a death portal whose mouth is within 3
# tiles of the first vault portal's.
def build_vault(rng: random.Random) -> Board | None:
    if not (made := vault_board(rng)):
        return None
    board, box, half, lure = made
    vaults = box | {board.mirror(t) for t in box}
    outside = set(board.all_tiles()) - vaults
    mouths = mouth_edges(board, outside & half, lure) or mouth_edges(board, outside & half, outside)
    doors = []
    for _ in range(rng.randint(1, 2)):
        if not (free := [e for e in mouths if e not in board.edges]) or not vault_door(board, rng, box, outside,
                                                                                        (door := rng.choice(free))):
            return None
        doors.append(door)
    starts, cells = {t for body in board.dragons for t in body}, set()
    if rng.random() < 0.4:
        board.quirks.add("decoy")
        near = {t for t in outside if min(torus_dist(board, t, d) for d in edge_tiles(board, doors[0])) <= 3}
        if not (decoys := mouth_edges(board, near, near)) or not (
                placed := death_portal(board, rng, rng.choice(decoys), starts | vaults)):
            return None
        cells = placed | {board.mirror(t) for t in placed}
    return board if bets_ok(board, cells, vaults) else None


# One attempt at a portalbet map: a vault map's board on at least PORTALBET_MIN_AREA tiles, no ordinary portal pair,
# where each team has one
# vault portal and PORTALBET_DEATHS death portals, their ten mouths in its half at least 3 tiles apart and alike from
# outside: drawn from the same lure tiles, kinds dealt at random, and each mouth baited with a fast pearl tile beside it
# half the time, whatever its kind (the death cells' own bait is drawn apart, death_portal).
def build_portalbet(rng: random.Random) -> Board | None:
    if not (made := vault_board(rng, PORTALBET_MIN_AREA, pairs=False)):
        return None
    board, box, half, lure = made
    vaults = box | {board.mirror(t) for t in box}
    outside = set(board.all_tiles()) - vaults
    starts, cells = {t for body in board.dragons for t in body}, set()
    mouths = mouth_edges(board, outside & half, lure)
    rng.shuffle(mouths)
    mouths += [e for e in mouth_edges(board, outside & half, outside) if e not in mouths]  # when the lure runs short
    kinds = ["vault"] + ["death"] * PORTALBET_DEATHS
    rng.shuffle(kinds)
    used: list[Edge] = []
    misses = 0  # mouths tried whose portal didn't fit: a board that keeps refusing is drawn again
    for kind in kinds:
        for mouth in mouths:
            if mouth in board.edges or not edge_tiles(board, mouth).isdisjoint(cells) or any(
                    torus_dist(board, a, b) < 3 for e in used for a in edge_tiles(board, e)
                    for b in edge_tiles(board, mouth)):
                continue
            if kind == "vault":
                if vault_door(board, rng, box, outside, mouth):
                    break
            elif placed := death_portal(board, rng, mouth, starts | vaults | cells):
                cells |= placed | {board.mirror(t) for t in placed}
                break
            if (misses := misses + 1) >= 20:
                return None
        else:
            return None
        used.append(mouth)
    for mouth in used:
        if rng.random() < 0.5:
            beside = sorted(edge_tiles(board, mouth))
            board.set_gap(rng.choice(beside), pearl_gap(rng))
    return board if bets_ok(board, cells, vaults) else None


# The board vault and portalbet maps share, up to their portals' mouths: a random shape of at least `min_area` tiles
# (build's sizes, drawn again while smaller), pearls, kelp and a walled border half the time, team A's vault (vault_box)
# and its mirror, 0-3 ordinary portal pairs off the vaults when `pairs` (not on portalbet maps: sig's 9 and 1 are all
# of a team's portals), holes knocked so the rest reaches MIN_REACH with the vaults, and dragons outside them. Returns
# the board, team A's vault, its half and the lure tiles outside the vaults, or None.
def vault_board(rng: random.Random, min_area: int = 0,
                pairs: bool = True) -> tuple[Board, set[Tile], set[Tile], set[Tile]] | None:
    w, h, sym = pick_shape(rng, set())
    while w * h < min_area:
        w, h, sym = pick_shape(rng, set())
    board = Board(w, h, sym)
    paint_tiles(board, rng, set())
    lay_kelp(board, rng, 0.0 if rng.random() < 0.1 else rng.uniform(0.03, 0.2))
    keep = {("h", x, 0) for x in range(w)} | {("v", 0, y) for y in range(h)} if rng.random() < 0.5 else set()
    for e in sorted(keep):
        board.set_kelp(e)
    split_x = sym in ("y", "xy")
    half = {t for t in board.all_tiles() if (t[0] if split_x else t[1]) < (w if split_x else h) // 2}
    if not (box := vault_box(board, rng, half, keep)):
        return None
    vaults = box | {board.mirror(t) for t in box}
    outside = set(board.all_tiles()) - vaults
    for _ in range(rng.choice([0, 0, 1, 2, 3]) if pairs else 0):
        portal_pair(board, rng, open_edges(board, outside), open_edges(board, outside))
    knock_holes(board, rng, frozenset(keep), MIN_REACH - len(vaults) / (w * h))
    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, False, frozenset(vaults)):
        return None
    return board, box, half, lure_tiles(board, vaults)


# Makes a portal from open edge `mouth` into the vault `box` through one of its wall edges beside `outside` (which
# becomes the portal's far end), and its mirror; False when none fits. As a death portal's cells are, the far end is
# never in one view with either mouth (more than 2 * VISION tiles from both, Chebyshev), so no dragon sees the good
# portal's landing from its mouth (29 Sep, seeds 0-39: 4 of 80 portalbet doors sat within it, none since; on vault
# maps 54 of 198 doors and ordinary pairs did, 26 since, those left all ordinary pairs, which the rule doesn't cover).
def vault_door(board: Board, rng: random.Random, box: set[Tile], outside: set[Tile], mouth: Edge) -> bool:
    near = edge_tiles(board, mouth) | edge_tiles(board, board.mirror_edge(mouth))
    far = [e for e in wall_edges(board, box, outside)
           if min(chebyshev(board, a, b) for a in edge_tiles(board, e) | edge_tiles(board, board.mirror_edge(e))
                  for b in near) > 2 * VISION]
    return portal_pair(board, rng, [mouth], far)


# Whether a portal-bet map is sound: the reachability check, no head boxed in at the start, every body's segments
# adjacent across open edges, the placed cells (`cells`, mirrors included) exactly the board's death cells (none that
# kelp and an ordinary portal made by chance: gen-vault-48-decoy had a second death portal so) and every vault tile in a
# vault.
def bets_ok(board: Board, cells: set[Tile], vaults: set[Tile]) -> bool:
    return (check(board) and not boxed_heads(board) and bodies_adjacent(board) and cells == death_cells(board)
            and vaults <= vault_tiles(board))


# ---------------------------------------------------------------- sealed start rooms: Trauma's opening
#
# Trauma (ladder; our worst live map on 29 Sep, 2-0-13 over the two newest versions) starts each team in its own walled
# strip at one end of the board, 11 of 48 columns and 10 of its 142 spawning tiles, whose only ways out are two portals
# in its wall into a pearl-rich maze between the strips, where the teams meet. A bot that doesn't cross its room's
# portal early starves in the room, and near variants of Trauma kept the seal on only 67 of 231 maps (E0293's judge;
# near keeps it on every one since 2026-09-30, keeps_rooms), so no test measured it. sealed_start is the measure;
# the sealedstart style builds it on every map.

SEALED_ROOM = (0.18, 0.28)  # each team's start room is this share of the board's width (Trauma: 11 of 48 columns)
SEALED_ROOM_PEARLS = 0.1  # at most this share of the spawning tiles lie in the start rooms (Trauma: 10 of 142, 7%)


# Whether every team starts in a room it leaves only by portal: its start tiles reach by plain steps none of the other
# team's and fewer tiles than they reach through portals (Trauma, Portals and Stronghold of the organiser maps).
def sealed_start(board: Board) -> bool:
    for mine, theirs in (team_tiles(board), team_tiles(board)[::-1]):
        plain = spread(board, mine, board.plain_step)
        if plain & theirs or len(plain) >= len(spread(board, mine, board.step)):
            return False
    return True


# One attempt at a sealedstart map: a board of Trauma's shape (2:1, 20-28 tall, split into halves along x), each team's
# start room a full-height strip of SEALED_ROOM of the width at its end, walled in kelp on both sides (the far one on
# the board's seam, so the rooms don't meet round the wrap), open inside with a few slow pearl patches, left only
# through 1-2 portals in its inner wall into the middle; the middle a maze (half the maps) or ordinary kelp, with its
# own pearl field and 1-3 fast farms, holding at least 1 - SEALED_ROOM_PEARLS of the spawning tiles. 1-4 dragons a
# team, all in their room. None when a check fails.
def build_sealedstart(rng: random.Random) -> Board | None:
    h = rng.randint(20, 28)
    w = min(64, 2 * h + rng.choice([-4, -2, 0, 0, 2]))
    board = Board(w, h, rng.choice(["xy", "y"]), quirks={rng.choice(["maze", "open"])})
    paint_tiles(board, rng, set())
    if "maze" in board.quirks:
        lay_maze(board, rng)
    else:
        lay_kelp(board, rng, rng.uniform(0.08, 0.2))
    width = max(3, round(w * rng.uniform(*SEALED_ROOM)))
    room = {(x, y) for x in range(width) for y in range(h)}
    rooms = room | {board.mirror(t) for t in room}
    for e in [e for e, k in board.edges.items() if k == KELP and edge_tiles(board, e) <= rooms]:
        board.open_edge(e)  # the room is open ground, as Trauma's is
    walls = {("v", 0, y) for y in range(h)} | {("v", width, y) for y in range(h)}
    for e in sorted(walls):
        board.set_kelp(e)
    keep = frozenset(walls | {board.mirror_edge(e) for e in walls})
    middle = set(board.all_tiles()) - rooms
    for t in rooms:
        board.gaps[t] = (0, 0)
    for _ in range(rng.randint(0, 3)):
        gap = (rng.randint(1, 20), rng.randint(100, 400))
        for t in blob(board, rng, rng.randint(2, 5), rng.choice(sorted(room))):
            if t in room:
                board.set_gap(t, gap)
    for _ in range(rng.randint(1, 3)):
        gap = (1, 1) if rng.random() < 0.3 else (1, rng.randint(2, 2 * FAST_GAP - 1))
        for t in blob(board, rng, rng.randint(2, 12), rng.choice(sorted(middle))):
            if t in middle:
                board.set_gap(t, gap)
    half = {t for t in middle if t[0] < w // 2}
    for _ in range(rng.randint(1, 2)):
        if not portal_pair(board, rng, [e for e in sorted(walls) if e[1] == width], open_edges(board, half)):
            return None
    knock_holes(board, rng, keep)
    count = rng.choices([1, 2, 3, 4], weights=[20, 40, 25, 15])[0]
    if not place_dragons(board, rng, count, False, frozenset(), room):
        return None
    spawning = [t for t in board.all_tiles() if board.gaps[t][1] > 0]
    if sum(t in rooms for t in spawning) > SEALED_ROOM_PEARLS * len(spawning):
        return None
    ok = check(board) and not boxed_heads(board) and bodies_adjacent(board) and sealed_start(board)
    return board if ok else None


# ---------------------------------------------------------------- curves: diagonal and curved kelp walls
#
# The organisers draw pictures (Devil, Queen Of Spades' spade, Trophy's cup) and outline them in 45-degree kelp
# staircases: 6 of their 13 maps have a diagonal wall of DIAGONAL_MIN edges or more (Queen Of Spades, Schooltime,
# Slithery Fight, Trauma, Trophy, Default Small; measured 2026-09-29), random layouts 14 of 300 eval maps, since
# lay_kelp and lay_maze draw straight walls only; so eval had such walls only where near and community copies keep the
# organisers' own. On 2026-09-29 an organiser named a community map "taichi" (a yin-yang: a disc cut by an S-shaped
# wall into two commas). Every tile along a diagonal wall has kelp on two sides, and two such walls meeting make a
# funnel that narrows to a point. diagonal_wall is the measure; the curves style draws such walls on random boards.

DIAGONAL_MIN = 2 * VISION  # a staircase this many edges long crosses VISION tiles each way: a whole view radius


# The longest diagonal kelp staircase on `board`, in edges: kelp edges alternating horizontal and vertical, each
# sharing a corner with the one before, all stepping the same way along x and along y (a 45-degree wall). The board is
# tiled 2x2 so a staircase may cross the wrap.
def diagonal_wall(board: Board) -> int:
    wide, high = 2 * board.w, 2 * board.h

    def kelp(o: str, x: int, y: int) -> bool:
        return board.edges.get((o, x % board.w, y % board.h)) == KELP

    best = 0
    for dx in (1, -1):
        for dy in (1, -1):
            ends_h: dict[Tile, int] = {}  # corner -> longest staircase ending there with a horizontal edge
            ends_v: dict[Tile, int] = {}  # and with a vertical one
            for y in range(high) if dy == 1 else range(high - 1, -1, -1):
                for x in range(wide) if dx == 1 else range(wide - 1, -1, -1):
                    if 0 <= x - dx < wide and kelp("h", min(x, x - dx), y):
                        ends_h[(x, y)] = ends_v.get((x - dx, y), 0) + 1
                    if 0 <= y - dy < high and kelp("v", x, min(y, y - dy)):
                        ends_v[(x, y)] = ends_h.get((x, y - dy), 0) + 1
                    best = max(best, ends_h.get((x, y), 0), ends_v.get((x, y), 0))
    return best


# One attempt at a curves map: a random board of `build`'s sizes, symmetries and pearls with light straight kelp, then
# curved and diagonal walls: on half the xy-mirrored boards 16 or more tiles a side a taichi (lay_taichi, whose yin
# team A starts in), otherwise 1-4 shapes (curve_shape). Then 0-3 portal pairs, holes knocked through the straight kelp
# only, dragons and farms. None when it fails the reachability check or has no diagonal wall of DIAGONAL_MIN edges.
def build_curves(rng: random.Random) -> Board | None:
    w, h, sym = pick_shape(rng, set())
    taichi = sym == "xy" and min(w, h) >= 16 and rng.random() < 0.5
    if taichi:
        w, h = w - w % 2, h - h % 2  # even sides: no tile is its own mirror, so every disc tile is yin or yang
    board = Board(w, h, sym)
    paint_tiles(board, rng, set())
    lay_kelp(board, rng, 0.0 if rng.random() < 0.3 else rng.uniform(0.02, 0.1))
    walls: set[Edge] = set()  # the curved walls' kelp, mirrors included: knock_holes leaves it
    within = None
    if taichi:
        board.quirks.add("taichi")
        within = lay_taichi(board, rng, walls)
    else:
        for _ in range(rng.randint(1, 4)):
            board.quirks.add(curve_shape(board, rng, walls))
    add_portals(board, rng, rng.choice([0, 0, 1, 2, 3]))
    knock_holes(board, rng, frozenset(walls))
    count = rng.choices([1, 2, 3, 4], weights=[15, 40, 30, 15])[0]
    if not place_dragons(board, rng, count, False, within=within):
        return None
    add_farms(board, rng, False, False)
    return board if check(board) and diagonal_wall(board) >= DIAGONAL_MIN else None


# Offset of coordinate a from c on an axis of n tiles, wrapped into [-n/2, n/2).
def offset(a: float, c: float, n: int) -> float:
    return (a - c + n / 2) % n - n / 2


# The tiles of `area` beside a tile outside it, as (tile, direction) pairs: its outline.
def outline(board: Board, area: set[Tile]) -> list[tuple[Tile, str]]:
    return [(t, d) for t in sorted(area) for d, (dx, dy) in DIRS.items()
            if board.wrap(t[0] + dx, t[1] + dy) not in area]


# Walls `sides` (tile, direction pairs) in kelp, then opens `doors` of them at random (each with its mirror); the
# kelp left joins `walls`.
def wall_in(board: Board, rng: random.Random, sides: list[tuple[Tile, str]], doors: int, walls: set[Edge]) -> None:
    edges = sorted({board.side(t, d) for t, d in sides})
    for e in edges:
        board.set_kelp(e)
        walls |= {e, board.mirror_edge(e)}
    for e in rng.sample(edges, min(doors, len(edges))):
        board.open_edge(e)
        walls -= {e, board.mirror_edge(e)}


# Draws a taichi centred on the board's mirror centre: a disc 0.3-0.48 of the shorter side in radius, turned by a random
# angle and cut by an S-shaped wall into yin (one half of it, plus a half-radius disc at its head, less the one at its
# tail) and yang, yin's mirror. The S has 0-2 gaps a team; two times in three the disc's rim is kelp with 1-3 doors a
# team, else open. Half the time each comma's eye (a small disc in its head) is a walled room of fast pearls with one
# door into its comma. Returns yin's tiles outside its eye: team A starts there.
def lay_taichi(board: Board, rng: random.Random, walls: set[Edge]) -> set[Tile]:
    cx, cy = (board.w - 1) / 2, (board.h - 1) / 2
    r = rng.uniform(0.3, 0.48) * min(board.w, board.h)
    turn = rng.uniform(0, 2 * math.pi)

    def frame(t: Tile) -> tuple[float, float]:  # t in the taichi's own turned frame, centred on it
        dx, dy = offset(t[0], cx, board.w), offset(t[1], cy, board.h)
        return dx * math.cos(turn) + dy * math.sin(turn), dy * math.cos(turn) - dx * math.sin(turn)

    def yin_side(t: Tile) -> bool:
        u, v = frame(t)
        if u * u + (v + r / 2) ** 2 <= (r / 2) ** 2:
            return True
        return u < 0 and u * u + (v - r / 2) ** 2 > (r / 2) ** 2

    disc = {t for t in board.all_tiles() if sum(c * c for c in frame(t)) <= r * r}
    yin: set[Tile] = set()
    for t in sorted(disc):
        m = board.mirror(t)
        if t not in yin and m not in yin:
            mine, theirs = yin_side(t), yin_side(m)
            yin.add(t if (mine if mine != theirs else t < m) else m)  # a tie on the S's centre line: lower sorts yin
    yang = {board.mirror(t) for t in yin}

    def beyond(t: Tile, d: str) -> Tile:
        return board.wrap(t[0] + DIRS[d][0], t[1] + DIRS[d][1])

    wall_in(board, rng, [(t, d) for t, d in outline(board, yin) if beyond(t, d) in yang], rng.randint(0, 2), walls)
    if rng.random() < 2 / 3:
        wall_in(board, rng, outline(board, disc), rng.randint(1, 3), walls)
    eye: set[Tile] = set()
    if rng.random() < 0.5:
        eye = {t for t in yin if (p := frame(t))[0] ** 2 + (p[1] + r / 2) ** 2 <= max(1.0, r / 6) ** 2}
        if doors := [(t, d) for t, d in outline(board, eye) if beyond(t, d) in yin]:
            wall_in(board, rng, outline(board, eye), 0, walls)
            t, d = rng.choice(doors)
            board.open_edge(board.side(t, d))
            walls -= {board.side(t, d), board.mirror_edge(board.side(t, d))}
            gap = pearl_gap(rng)
            for t in eye:
                board.set_gap(t, gap)
            board.quirks.add("eyes")
        else:
            eye = set()
    return yin - eye


# Draws one curved or diagonal wall and its mirror (its kelp joins `walls`) and returns its kind: a "ring" (an ellipse's
# outline, 3 tiles to a third of the shorter side in each radius, with 1-3 doors, half the time a farm of 2-12 fast
# tiles at its middle, as Trophy's cup has), a "cup" (the part of such an outline within a random half to three
# quarters of a turn, open on the rest), a "diagonal" (a 45-degree wall of VISION to half the shorter side steps) or a
# "funnel" (two such walls from one corner, at right angles).
def curve_shape(board: Board, rng: random.Random, walls: set[Edge]) -> str:
    kind = rng.choice(["ring", "cup", "diagonal", "funnel"])
    if kind in ("ring", "cup"):
        cx, cy = rng.randrange(board.w) + rng.choice([0, 0.5]), rng.randrange(board.h) + rng.choice([0, 0.5])
        top = max(3.0, min(board.w, board.h) / 3)
        rx, ry = rng.uniform(3, top), rng.uniform(3, top)
        inside = {t for t in board.all_tiles()
                  if (offset(t[0], cx, board.w) / rx) ** 2 + (offset(t[1], cy, board.h) / ry) ** 2 <= 1}
        sides = outline(board, inside)
        if kind == "ring":
            wall_in(board, rng, sides, rng.randint(1, 3), walls)
            if rng.random() < 0.5:
                gap = pearl_gap(rng)
                for t in blob(board, rng, rng.randint(2, 12), board.wrap(int(cx), int(cy))):
                    if t in inside:
                        board.set_gap(t, gap)
        else:
            start, span = rng.uniform(0, 2 * math.pi), rng.uniform(math.pi, 1.5 * math.pi)
            wall_in(board, rng, [(t, d) for t, d in sides if (math.atan2(
                offset(t[1] + DIRS[d][1] / 2, cy, board.h), offset(t[0] + DIRS[d][0] / 2, cx, board.w)) - start)
                % (2 * math.pi) <= span], 0, walls)
        return kind
    x, y = rng.randrange(board.w), rng.randrange(board.h)
    sx, sy = rng.choice((1, -1)), rng.choice((1, -1))
    top = max(VISION, min(board.w, board.h) // 2)
    arms = [(sx, sy)] if kind == "diagonal" else [(sx, sy), (-sx, sy)]
    for ax, ay in arms:
        px, py = x, y
        for _ in range(rng.randint(VISION, top)):
            for e in (("h", min(px, px + ax) % board.w, py % board.h),
                      ("v", (px + ax) % board.w, min(py, py + ay) % board.h)):
                board.set_kelp(e)
                walls |= {e, board.mirror_edge(e)}
            px, py = px + ax, py + ay
    return kind


# Reachability: every head reaches `reach` of the map (MIN_REACH unless given) and at least one spawning tile.
def check(board: Board, reach: float = MIN_REACH) -> bool:
    area = board.w * board.h
    for body in board.dragons:
        region = board.reach(body[0])
        if len(region) < reach * area:
            return False
        if not any(board.gaps[t][1] > 0 for t in region):
            return False
    return True


# Map-file text. EDGE lines list only kelp and portal edges; open edges are the default.
def render(board: Board, name: str) -> str:
    lines = [f"MAP {board.w} {board.h}", f"SYMMETRY {board.sym}", f"MAP_NAME {name}"]
    lines.append(f"TILE_COUNT {board.w * board.h}")
    for x, y in board.all_tiles():
        lo, hi = board.gaps[(x, y)]
        lines.append(f"TILE {x} {y} {lo} {hi}")
    lines.append(f"EDGE_COUNT {len(board.edges)}")
    for (o, x, y), kind in sorted(board.edges.items(), key=lambda kv: edge_index(board, kv[0])):
        if kind == KELP:
            lines.append(f"EDGE {edge_index(board, (o, x, y))} 1 -1")
        else:
            lines.append(f"EDGE {edge_index(board, (o, x, y))} 2 {kind}")
    lines.append(f"DRAGON_COUNT {len(board.dragons)}")
    for i, body in enumerate(board.dragons):
        coords = " ".join(f"{x} {y}" for x, y in body)
        lines.append(f"DRAGON {i % 2} {len(body)} {coords}")
    lines.append("END")
    return "\n".join(lines) + "\n"


def edge_index(board: Board, e: Edge) -> int:
    o, x, y = e
    row = 2 * y if o == "h" else 2 * y + 1
    return row * (board.w + 1) + x


# ---------------------------------------------------------------- worldgen: an independent generator
#
# mapgen/worldgen/worldgen.py is another competitor's generator (see its README for the origin and pinned version),
# vendored unchanged and never edited: its maps come from choices ours never made (layered climate, districts and
# landmarks), so a behaviour fitted on our maps is tested on ground it wasn't fitted to.
WORLDGEN = Path(__file__).resolve().parent / "worldgen" / "worldgen.py"


# The vendored worldgen module, loaded from its file (it isn't a package, and gen.py runs both as a script and as
# mapgen.gen).
@functools.cache
def worldgen_module():
    spec = importlib.util.spec_from_file_location("worldgen", WORLDGEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# worldgen's world for `seed` (its own random shape, style, landmark, symmetry, border, dragons and pearl supply; `kw`
# fixes any of them, as its generate takes), read back and written as our other styles are (render), named
# gen-worldgen-<seed>-<its style>-<its landmark, or none when it built none>. Deterministic by seed. worldgen sometimes
# lays a dragon's body across a portal edge, which the engine's loader refuses ("dragon 0 segment 9 is not adjacent to
# the one before it": seeds 4703010, 636244281 and 254458972, 3 maps in 6 h on 29 Sep); such a seed's world is picked
# again by adjacent_world, so every seed still gives a map and every other seed the same one.
def worldgen_map(seed: int, **kw) -> str:
    module = worldgen_module()
    world = module.generate(seed, **kw)
    if not bodies_adjacent(parse(world.text())):
        world = adjacent_world(module, seed, **kw)
    landmark = world.landmark_kind if world.lm_built else "none"
    return render(parse(world.text()), f"gen-worldgen-{seed}-{world.style_name}-{landmark}")


# Whether each dragon's segments follow one another across open edges (Board.plain_step, the board wrapping), as the
# engine's loader requires: not through kelp, and not across a portal edge.
def bodies_adjacent(board: Board) -> bool:
    return all(any(board.plain_step(a, d) == b for d in DIRS)
               for body in board.dragons for a, b in zip(body, body[1:]))


# worldgen's generate (its best-scoring of `candidates` valid worlds from sub-seeds of `seed`), counting a world as
# valid only when its dragons' bodies are adjacent too.
def adjacent_world(module, seed: int, candidates: int = 8, **kw):
    skeleton = module.macro(seed, **kw)
    best, valid = None, 0
    for i in range(candidates * 6):
        if valid >= candidates:
            break
        world = module.World(seed * 1009 + i, **skeleton)
        world.origin = (seed, i)
        if not world.build() or not bodies_adjacent(parse(world.text())):
            continue
        valid += 1
        if best is None or world.score > best.score:
            best = world
    if best is None:
        raise RuntimeError(f"no worldgen world with adjacent dragon bodies for seed {seed}")
    return best


# ---------------------------------------------------------------- parsing and stats


# The (min, max) pearl gaps of a TILE line's fields, or None when they are missing or not whole numbers of at least 0
# (a negative gap is no real timing: a withheld one written as a placeholder such as -1).
def tile_gap(f: list[str]) -> tuple[int, int] | None:
    if len(f) < 5 or not all(v.isdecimal() for v in f[3:5]):
        return None
    return int(f[3]), int(f[4])


# Reads a .map file back into a Board (used for stats on real and generated maps, and for replays' map text). From
# 2026-09-27 21:00 AEST the site may withhold pearl timings from replays' map text: a tile whose gaps the text doesn't
# give (its TILE line cut short or not numbers, or no line for it when the TILE lines don't add up to the text's
# TILE_COUNT) gets (0, 0) like a tile without pearls, so every stat still runs, and is listed in board.unknown for
# readers that must tell the two apart (watch.py draws it '?'). The form the site used (every TILE line `x y 0 0`) reads
# here as a map without pearls; watch.py, which has the replay's countdowns, tells it apart.
def parse(text: str) -> Board:
    board = Board(0, 0, "xy")
    declared: int | None = None
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == "END":
            break
        if f[0] == "MAP":
            board.w, board.h = int(f[1]), int(f[2])
        elif f[0] == "SYMMETRY":
            board.sym = f[1]
        elif f[0] == "TILE_COUNT" and len(f) > 1 and f[1].isdecimal():
            declared = int(f[1])
        elif f[0] == "TILE" and len(f) >= 3 and f[1].isdecimal() and f[2].isdecimal():
            t = (int(f[1]), int(f[2]))
            gap = tile_gap(f)
            if gap is None:
                board.unknown.add(t)
            else:
                board.gaps[t] = gap
        elif f[0] == "EDGE":
            e = edge_of_index(board, int(f[1]))
            if e is not None and f[2] != "0":
                board.edges[e] = KELP if f[2] == "1" else int(f[3])
        elif f[0] == "DRAGON":
            n = int(f[2])
            coords = [int(v) for v in f[3 : 3 + 2 * n]]
            body = list(zip(coords[::2], coords[1::2]))
            board.dragons.append(body)
    listed = len(board.gaps) + len(board.unknown)
    for t in board.all_tiles():
        if t not in board.gaps and t not in board.unknown and declared != listed:
            board.unknown.add(t)
        board.gaps.setdefault(t, (0, 0))
    return board


def edge_of_index(board: Board, i: int) -> Edge | None:
    stride = board.w + 1
    col, row = i % stride, i // stride
    if col == board.w or row >= 2 * board.h:
        return None
    return ("h", col, row // 2) if row % 2 == 0 else ("v", col, (row - 1) // 2)


def dragon_teams(text: str) -> list[int]:
    return [int(line.split()[1]) for line in text.splitlines() if line.startswith("DRAGON ")]


# ---------------------------------------------------------------- near: variants of the ladder maps


# The ladder's maps, current and retired, in a fixed order (by file stem).
def ladder_maps() -> list[Path]:
    return sorted([*LADDER.glob("*.map"), *RETIRED.glob("*.map")], key=lambda p: p.stem)


# Ladder map names (file stems), current and retired, in a fixed order.
def ladder_names() -> list[str]:
    return [p.stem for p in ladder_maps()]


# The maps near variants are made from, ladder (current and retired) then sprint, in a fixed order, so consecutive
# near seeds cycle through every one.
def near_sources() -> list[Path]:
    return ladder_maps() + sorted(SPRINT.glob("*.map"))


# The community maps variants are also made from, in a fixed order.
def community_sources() -> list[Path]:
    return sorted(COMMUNITY.glob("*.map"))


# The source maps of one size ("small": at most SMALL_MAX tiles; "big": at least BIG_MIN), from the ladder (current and
# retired, ladder_maps) then the community maps, in that fixed order, so consecutive seeds of a sized style cycle through every one. The Sprint-like
# maps are left out: their one very long starter per team is a different game (the `sprint` style has them).
def sized_sources(size: str) -> list[Path]:
    fits = (lambda area: area <= SMALL_MAX) if size == "small" else (lambda area: area >= BIG_MIN)
    sources = [p for p in ladder_maps() + community_sources() if fits(map_area(p.read_text()))]
    if not sources:
        raise RuntimeError(f"no {size} source maps in {LADDER}, {RETIRED} or {COMMUNITY}")
    return sources


# A map file's area in tiles, from its MAP line.
def map_area(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("MAP "):
            _, w, h = line.split()[:3]
            return int(w) * int(h)
    raise ValueError("no MAP line")


# A variant of source map number `pick` (near_sources unless `sources` names others, cycling), named after it and
# `seed` (near_<map>_<seed>): the whole board moved by a symmetry of the torus, then pearl rates, tile zones, a few kelp
# edges, portals and dragon starts nudged. With `repearl` (named near_<map>_repearl_<seed>, its own family in the
# per-style report) the pearl field is then drawn afresh (redraw_pearls), until at least REPEARL_MIN_CHANGE of its tiles
# differ from the source's by more than a nudge (pearl_change). Every change is mirrored, so the teams stay equal. A
# variant must keep its source's sealed start rooms (keeps_rooms): the nudges opened Trauma's rooms into the middle on
# 16 of 20 copies, or joined the two teams' rooms through the seam, before 2026-09-30 (I1099), and its portal pockets,
# which the nudges leave alone (kept_parts; E0405). advmaps' mutants, which also call near_ok and nudge, may open
# them. NEAR_ATTEMPTS, since keeps_rooms passes few nudges of some sources.
def near(seed: int, pick: int, rng: random.Random, sources: list[Path] | None = None, repearl: bool = False) -> str:
    sources = sources or near_sources()
    path = sources[pick % len(sources)]
    name = f"{path.stem}_repearl" if repearl else path.stem
    source = parse(path.read_text())
    shape = pocket_shape(source)
    for _attempt in range(NEAR_ATTEMPTS):
        board = move_board(source, rng.choice(motions(source)))
        layout = (dict(board.edges), [list(body) for body in board.dragons])
        pearls = dict(board.gaps)
        nudge(board, rng, kept_parts(board))
        if repearl:
            redraw_pearls(board, rng)
            if pearl_change(pearls, board.gaps) < REPEARL_MIN_CHANGE:
                continue  # a uniform source (Big Empty) and a uniform draw can land within a nudge of each other
        # Pearl rates alone don't make a new map: some kelp, portal or dragon must have moved too.
        if (board.edges, board.dragons) != layout and near_ok(board, source) and keeps_rooms(board, source, shape):
            return render(board, f"near_{name}_{seed}")
    raise RuntimeError(f"seed {seed} produced no valid near_{name} map in {NEAR_ATTEMPTS} attempts")


# Tries near makes before failing a seed. keeps_rooms passes 6 of 400 nudges of Portal Labyrinth and 54 of 400 of
# Trauma's (2026-09-30), so 100 would fail about 1 labyrinth seed in 4; 3,000 fail none in practice (0.985^3000 is
# 3e-20; 3e-7 even at a 0.5% pass rate), at about 0.01 s a try (a labyrinth copy takes about 0.7 s, Trauma 0.1 s).
# Other sources pass most tries and never reach it.
NEAR_ATTEMPTS = 3000


# Whether the near map of `seed` is repearled. Half of eval's and `--style near`'s near maps are (the organisers,
# #announcements 2026-09-27: "There may be variants of maps that look the same (in terms of kelp and portals) but have
# completely different pearl timings"). Half, not all: the other half keep the source's pearl field nudged, as our
# copies of the ladder maps have it, and every earlier result's near families were measured on it; either half is about
# a sixth of eval, 400 maps of a 2,400-map set, enough for its own score in the per-style report. Not the sprint,
# small, big or maze styles: map mixes aimed at those keep their sources' pearl fields. Which half: a hash of the seed,
# not the map's place in the near cycle, since the referee hands trial i to opponent i % 4 (a shared set in file-name
# order, a map mix in mix_plan's) and a place-based rule gave two opponents only repearled near maps and two none;
# hashed, each opponent and each source gets both kinds over a set.
def repearled(seed: int) -> bool:
    return hashlib.sha256(f"repearl {seed}".encode()).digest()[0] < 128


# Which community map an eval community seed varies: a hash of the seed, for the reason repearled gives. Picked by the
# place in the cycle (seed // COMMUNITY_EVERY) with four community maps, each went to one opponent seat only: 9 seeds
# apart is one seat further round 4 (loop/test_seat_balance.py, MISSES 59).
def community_pick(seed: int) -> int:
    return int.from_bytes(hashlib.sha256(f"community {seed}".encode()).digest()[:4], "big")


# Replaces `board`'s whole pearl field, keeping its layout: base rate, slower and faster zones and barren areas as
# paint_tiles draws them for random maps, fast farms as add_farms does (nine in ten), then fast pearls in the layout's
# own dead ends (fill_dead_ends). Mirrored, like every setter.
def redraw_pearls(board: Board, rng: random.Random) -> None:
    paint_tiles(board, rng, set())
    add_farms(board, rng, False, False)
    fill_dead_ends(board, rng)


# Puts one fast pearl range (pearl_gap) on 10-36 of `board`'s dead-end tiles (dead_end), far ends first (one move out),
# seven times in ten: of the 10 organiser maps with dead ends, 7 hold 10-36 fast tiles in them (Portals, Queen Of
# Spades, Slithery Fight, Stronghold, Trauma, Autarky, Dilemma; 126 of the 13 maps' 420 fast tiles, DEAD_ENDS_MEASURED
# in test_gen), where mix-drawn farms land in the open. Only corridor tiles, with at most 3 moves out, are tried: that
# skips room interiors inside a dead end (60 of the 13 sources' 833 dead-end tiles have 4 moves: Schooltime 32,
# Slithery Fight 20, Queen Of Spades 4, Trauma 4), since trying every tile took 9.6 s of CPU over the 13 sources against
# 1.7 s (Big Empty 5 s against none; 2026-09-27), per attempt.
def fill_dead_ends(board: Board, rng: random.Random) -> None:
    if rng.random() >= 0.7:
        return
    graph = moves(board)
    starts = {t for body in board.dragons for t in body}
    dead = [t for t in board.all_tiles()
            if len(graph[0][t]) <= 3 and t not in starts and dead_end(board, t, starts, graph)]
    rng.shuffle(dead)
    dead.sort(key=lambda t: len(graph[0][t]))
    want, gap, done = rng.randint(10, 36), pearl_gap(rng), set()
    for t in dead:
        if len(done) >= want:
            break
        if t not in done:
            board.set_gap(t, gap)
            done |= {t, board.mirror(t)}


REPEARL_MIN_CHANGE = 0.25  # a repearled map differs from its source by more than a nudge on at least this tile share


# The share of tiles whose pearls differ between two fields of the same board by more than near's nudge makes them
# (rescale_gaps: 0.8-1.25): spawning on one and not the other, fast on one and not the other (FAST_GAP), or a mean gap
# off by more than that factor. Plain near variants measure 0-13% against their moved source (260 near maps,
# 2026-09-27; swap_tiles and rounding at FAST_GAP), repearled ones mostly over 50%.
def pearl_change(before: dict[Tile, tuple[int, int]], after: dict[Tile, tuple[int, int]]) -> float:
    def differs(a: tuple[int, int], b: tuple[int, int]) -> bool:
        if (a[1] > 0) != (b[1] > 0) or fast(a) != fast(b):
            return True
        return a[1] > 0 and not 0.8 <= (b[0] + b[1]) / max(1, a[0] + a[1]) <= 1.25
    return sum(differs(before[t], after[t]) for t in before) / len(before)


@dataclass(frozen=True)
class Motion:
    """A move of the whole torus that keeps the map's mirror: optional flips, a shift, then an optional transpose.

    Any flip keeps every mirror. A shift along a mirrored axis must be 0 or half the side, or the mirror line
    would move; see motions().
    """

    flip_x: bool
    flip_y: bool
    dx: int
    dy: int
    transpose: bool

    def tile(self, board: Board, t: Tile) -> Tile:
        x, y = t
        x = ((board.w - 1 - x) if self.flip_x else x) + self.dx
        y = ((board.h - 1 - y) if self.flip_y else y) + self.dy
        x, y = x % board.w, y % board.h
        return (y, x) if self.transpose else (x, y)


# Every motion that keeps `board`'s symmetry type (a transpose swaps x and y symmetry, which is still fair).
def motions(board: Board) -> list[Motion]:
    def shifts(n: int, mirrored: bool) -> list[int]:
        if not mirrored:
            return list(range(n))
        return [0, n // 2] if n % 2 == 0 else [0]

    xs = shifts(board.w, board.sym in ("y", "xy"))
    ys = shifts(board.h, board.sym in ("x", "xy"))
    return [
        Motion(fx, fy, dx, dy, tr)
        for fx in (False, True)
        for fy in (False, True)
        for dx in xs
        for dy in ys
        for tr in (False, True)
    ]


TRANSPOSED_SYM = {"x": "y", "y": "x", "xy": "xy"}


# A new board holding `src` moved by `m`: tiles, kelp, portals (same ids) and dragons (same order).
def move_board(src: Board, m: Motion) -> Board:
    if m.transpose:
        dst = Board(src.h, src.w, TRANSPOSED_SYM[src.sym])
    else:
        dst = Board(src.w, src.h, src.sym)
    for t, gap in src.gaps.items():
        dst.gaps[m.tile(src, t)] = gap
    for e, kind in src.edges.items():
        dst.edges[move_edge(src, dst, m, e)] = kind
    dst.dragons = [[m.tile(src, t) for t in body] for body in src.dragons]
    return dst


# The edge of `dst` between the moved images of the two tiles that edge e of `src` separates.
def move_edge(src: Board, dst: Board, m: Motion, e: Edge) -> Edge:
    o, x, y = e
    a = src.wrap(x, y - 1) if o == "h" else src.wrap(x - 1, y)
    ta, tb = m.tile(src, a), m.tile(src, (x, y))
    d = next(d for d, (dx, dy) in DIRS.items() if dst.wrap(ta[0] + dx, ta[1] + dy) == tb)
    return dst.side(ta, d)


# The small, mirrored changes that make a moved source map a new map. `keep` (near: kept_parts) names tiles whose pearls
# stay where they are and edges no kelp or portal is moved onto or off.
def nudge(board: Board, rng: random.Random,
          keep: tuple[frozenset[Tile], frozenset[Edge]] = (frozenset(), frozenset())) -> None:
    tiles, edges = keep
    rescale_gaps(board, rng)
    swap_tiles(board, rng, rng.uniform(0.02, 0.06), tiles)
    shuffle_kelp(board, rng, rng.uniform(0.02, 0.06), edges)
    shift_portals(board, rng, 0.3, edges)
    jitter_dragons(board, rng, 0.6)


# Scales each distinct pearl range by its own factor in 0.8-1.25; tiles that never spawn (max 0) stay that way.
def rescale_gaps(board: Board, rng: random.Random) -> None:
    scaled = {}
    for lo, hi in sorted(set(board.gaps.values())):
        f = rng.uniform(0.8, 1.25)
        new_lo = round(lo * f)
        scaled[(lo, hi)] = (new_lo, max(new_lo, round(hi * f)))
    for t, gap in board.gaps.items():
        board.gaps[t] = scaled[gap]


# Swaps pearl ranges across `share` of the borders between unlike tiles, so zones and farms shift by a tile; tiles in
# `kept` keep theirs.
def swap_tiles(board: Board, rng: random.Random, share: float, kept: frozenset[Tile] = frozenset()) -> None:
    borders = [
        (t, n)
        for t in board.all_tiles()
        for n in (board.wrap(t[0] + 1, t[1]), board.wrap(t[0], t[1] + 1))
        if board.gaps[t] != board.gaps[n] and t not in kept and n not in kept
    ]
    for t, n in rng.sample(borders, round(share * len(borders))):
        a, b = board.gaps[t], board.gaps[n]
        board.set_gap(t, b)
        board.set_gap(n, a)


# Moves, adds or removes about `share` of the kelp edges (with their mirrors) not in `frozen`. New kelp goes on an open
# edge next to the old one, never between two segments of a dragon or on a `frozen` edge.
def shuffle_kelp(board: Board, rng: random.Random, share: float, frozen: frozenset[Edge] = frozenset()) -> None:
    blocked = body_edges(board) | frozen
    for _ in range(round(share * sum(k == KELP and e not in frozen for e, k in board.edges.items()))):
        kelp = sorted(e for e, k in board.edges.items() if k == KELP and e not in frozen)
        if not kelp:
            return
        e = rng.choice(kelp)
        spots = [n for n in near_edges(board, e) if n not in board.edges and n not in blocked]
        op = rng.choices(["move", "add", "remove"], weights=[3, 1, 1])[0]
        if op != "add":
            board.open_edge(e)
        if op != "remove" and spots:
            board.set_kelp(rng.choice(spots))


# Edges touching e: those sharing a corner with it, and the two parallel edges one tile to either side.
def near_edges(board: Board, e: Edge) -> list[Edge]:
    o, x, y = e
    corners = [(x, y), (x + 1, y)] if o == "h" else [(x, y), (x, y + 1)]
    out: list[Edge] = []
    for cx, cy in corners:
        out += [("h", cx - 1, cy), ("h", cx, cy), ("v", cx, cy - 1), ("v", cx, cy)]
    out += [("h", x, y - 1), ("h", x, y + 1)] if o == "h" else [("v", x - 1, y), ("v", x + 1, y)]
    return sorted({(k, ex % board.w, ey % board.h) for k, ex, ey in out} - {e})


# Edges between consecutive segments of any dragon; kelp and portals must stay off them.
def body_edges(board: Board) -> set[Edge]:
    out: set[Edge] = set()
    for body in board.dragons:
        for a, b in zip(body, body[1:]):
            out |= {board.side(a, d) for d, (dx, dy) in DIRS.items() if board.wrap(a[0] + dx, a[1] + dy) == b}
    return out


# Shifts one end of about `share` of the portal pairs by one tile (same orientation), and the matching end of the
# mirror pair to the mirror spot. An end on the symmetry line only moves along it, as the engine requires both
# ends of a pair to be on the line or both off it. A pair with an end on a `frozen` edge stays, and no end moves onto one.
def shift_portals(board: Board, rng: random.Random, share: float, frozen: frozenset[Edge] = frozenset()) -> None:
    blocked = body_edges(board) | frozen
    stay = {board.edges[e] for e in frozen if board.edges.get(e, KELP) >= 0}
    for pid in sorted({k for k in board.edges.values() if k >= 0}):
        if pid in stay or rng.random() >= share:
            continue
        e = rng.choice(sorted(o for o, k in board.edges.items() if k == pid))
        on_line = board.mirror_edge(e) == e
        o, x, y = e
        spots = [(o, (x + dx) % board.w, (y + dy) % board.h) for dx, dy in DIRS.values()]
        spots = [
            n
            for n in spots
            if n not in board.edges and n not in blocked and (board.mirror_edge(n) == n) == on_line
        ]
        if spots:
            move_portal_end(board, e, rng.choice(spots))


def move_portal_end(board: Board, e: Edge, n: Edge) -> None:
    me, mn = board.mirror_edge(e), board.mirror_edge(n)
    pid = board.edges.pop(e)
    mirror_pid = board.edges.pop(me, None)  # None when e is its own mirror
    board.edges[n] = pid
    if mirror_pid is not None:
        board.edges[mn] = mirror_pid


# Moves each team A dragon (with probability `share`) by up to two tiles each way, keeping its shape, and puts its
# team B twin on the mirror spot. A dragon with no free spot nearby stays put. Assumes A/B mirror pairs in order,
# as every ladder map has.
def jitter_dragons(board: Board, rng: random.Random, share: float) -> None:
    for i in range(0, len(board.dragons), 2):
        if rng.random() >= share:
            continue
        others = {t for j, body in enumerate(board.dragons) if j not in (i, i + 1) for t in body}
        for _attempt in range(20):
            dx, dy = rng.randint(-2, 2), rng.randint(-2, 2)
            body = [board.wrap(x + dx, y + dy) for x, y in board.dragons[i]]
            if body != board.dragons[i] and body_fits(board, body, others):
                board.dragons[i : i + 2] = [body, [board.mirror(t) for t in body]]
                break


# Whether a team A body (and so its mirror) can go here: open edges between segments, no overlap, off the mirror
# line, and a free first move for the head.
def body_fits(board: Board, body: list[Tile], others: set[Tile]) -> bool:
    for a, b in zip(body, body[1:]):
        if not any(board.plain_step(a, d) == b for d in DIRS):
            return False
    if not all(ok_tile(board, t, body, others) for t in body):
        return False
    return head_can_move(board, body[0], others | set(body) | {board.mirror(t) for t in body})


def head_can_move(board: Board, head: Tile, taken: set[Tile]) -> bool:
    return any(t is not None and t not in taken for t in (board.step(head, d) for d in DIRS))


# Validity of a finished near map: reachability (check), dragons in mirrored A/B pairs that don't overlap, and
# every head able to move. Given its `source`, a variant needs only be as valid as that map: some organiser maps break
# the rules on purpose (Portals, 2026-09-27: halves joined only by portals, 47% reach; Slithery Fight: a 25-long
# starter coiled round its own head), so the reach floor is the source's lowest and as many heads may be boxed in.
def near_ok(board: Board, source: Board | None = None) -> bool:
    reach, boxed = MIN_REACH, 0
    if source is not None:
        area = source.w * source.h
        reach = min([MIN_REACH] + [len(source.reach(body[0])) / area for body in source.dragons])
        boxed = len(boxed_heads(source))
    if not check(board, reach):
        return False
    tiles = [t for body in board.dragons for t in body]
    if len(tiles) != len(set(tiles)):
        return False
    for a, b in zip(board.dragons[::2], board.dragons[1::2]):
        if b != [board.mirror(t) for t in a]:
            return False
    return len(boxed_heads(board)) <= boxed


# The tiles the head on `head` reaches through open edges (no portal) when they are a sealed room: under half the
# board, with a portal that leads out of it, so the room is left only by portal (Trauma's start strips, Portals'
# halves); else None.
def sealed_room(board: Board, head: Tile) -> set[Tile] | None:
    room = spread(board, {head}, board.plain_step)
    if 2 * len(room) < board.w * board.h and any(
            board.edges.get(board.side(t, d), KELP) != KELP and board.step(t, d) not in room
            for t in room for d in DIRS):
        return room
    return None


# A variant's sealed start room keeps its source room's size within this factor, near's own nudge range (rescale_gaps):
# unbounded, a copy of Trauma's room grew to 2.1x, taking in pearl-rich middle a bot then eats without crossing the
# portal (6 of 234 passing tries, 2026-09-30 review), where opening one wall into a walled pocket grows it by 1.1-1.3x.
ROOM_CHANGE = (0.8, 1.25)


# Whether `board`, a variant of `source` with its dragons in the same order, keeps every sealed start room: each dragon
# sealed in on the source is on the board, in a room within ROOM_CHANGE of the source room's size, and one whose room
# holds no tile of the other team's dragons still holds none (a nudged seam wall can join the two teams' rooms into
# one still small and portal-bound). It also keeps every portal pocket of the source with its lap (pocket_shape;
# `shape`, the source's, when the caller has it), and teams that can never meet on the source (sealed) never meet.
# New pockets may appear, as any other nudge.
def keeps_rooms(board: Board, source: Board, shape: list[tuple[int, int]] | None = None) -> bool:
    if Counter(shape if shape is not None else pocket_shape(source)) - Counter(pocket_shape(board)):
        return False
    if sealed(source) and not sealed(board):
        return False
    for i, (old, new) in enumerate(zip(source.dragons, board.dragons, strict=True)):
        if (room := sealed_room(source, old[0])) is None:
            continue
        if (copy := sealed_room(board, new[0])) is None:
            return False
        if not ROOM_CHANGE[0] <= len(copy) / len(room) <= ROOM_CHANGE[1]:
            return False
        if not room & team_tiles(source)[(i + 1) % 2] and copy & team_tiles(board)[(i + 1) % 2]:
            return False
    return True


# Portal pockets and laps (E0405, I1079, 2026-09-30). Portals holds 12 walled 2x2 rooms of fast pearls, each entered
# only through the portal pair on its inner edge, whose far ends sit side by side in the open; round the room and the
# 2x2 spot beside those ends is a closed lap of 8 tiles, and 99% of our 8+ pocket deaths there online were at length
# exactly 8 in one (a farmer eats round the lap until it fills it), plus 16 one-tile dead ends behind a portal. Exact
# copies of the map reproduce the deaths locally (2.92 a game); near's nudges kept 2-10 of the 12 laps on 20 fresh
# copies and moved most pocket pearls out (84-153 eaten a game v 387), so no local test saw them. near keeps them
# (kept_parts, keeps_rooms); Default, Queen Of Spades, Slithery Fight and the Sprint maps have pockets too.

POCKET_MAX = 16  # a pocket is a room of at most this many tiles left only by portal (Default's rooms: 16)
LAP_MAX = 12  # laps longer than this are not looked for (Portals' are 8)


# The rooms of `board` (tiles joined by open edges, no portal) of at most POCKET_MAX tiles that a move leaves, so only
# through a portal: Portals' 2x2 rooms and one-tile dead ends. Walled rooms no move leaves (Portals' pearl-only strips)
# are never entered and not pockets.
def pockets(board: Board) -> list[set[Tile]]:
    seen: set[Tile] = set()
    out = []
    for t in board.all_tiles():
        if t in seen:
            continue
        room = spread(board, {t}, board.plain_step)
        seen |= room
        if len(room) <= POCKET_MAX and any((n := board.step(r, d)) is not None and n not in room
                                           for r in room for d in DIRS):
            out.append(room)
    return out


# The shortest lap through `room`: a round of distinct tiles, never stepping straight back, that leaves the room and
# comes back to where it began, which a dragon that long fills; None when there is none up to LAP_MAX (a dead end,
# whose one move out is back the way in). Such a round passes a room tile with a move out, so the search starts only
# there. Moves are symmetric (a portal leads both ways), so the distance out from the first tile bounds the way back,
# and no tile of a round up to LAP_MAX is further than half that.
def lap_length(board: Board, room: set[Tile]) -> int | None:
    best: int | None = None
    for first in sorted(t for t in room if any((n := board.step(t, d)) is not None and n not in room for d in DIRS)):
        far = {first: 0}
        todo = deque([first])
        while todo:
            t = todo.popleft()
            for d in DIRS:
                if (n := board.step(t, d)) is not None and n not in far and far[t] < LAP_MAX // 2:
                    far[n] = far[t] + 1
                    todo.append(n)
        limit = LAP_MAX + 1 if best is None else best

        def walk(t: Tile, prev: Tile | None, path: set[Tile], out: bool) -> None:
            nonlocal best, limit
            for d in DIRS:
                n = board.step(t, d)
                if n is None or n == prev:
                    continue
                if n == first:
                    if out and len(path) >= 3 and len(path) < limit:
                        best = limit = len(path)
                elif n not in path and n in far and len(path) + far[n] < limit:
                    path.add(n)
                    walk(n, t, path, out or n not in room)
                    path.discard(n)

        walk(first, None, {first}, False)
    return best


# (size, lap length or 0 for none) of each of `board`'s pockets, sorted: the shape near keeps (Portals: sixteen (1, 0),
# twelve (4, 8)).
def pocket_shape(board: Board) -> list[tuple[int, int]]:
    return sorted((len(room), lap_length(board, room) or 0) for room in pockets(board))


# What near's nudges leave alone on `board` (a moved source): the pockets' tiles (their pearls) and every edge beside
# one (their walls, portals and inside), and when the teams can never meet (sealed: Portals' halves), the kelp between
# the tiles team A reaches and the rest, with its mirror (team B's side). Without it Portals' copies kept 2-10 of 12
# laps, and their teams met on 20 of 20 (2026-09-30).
def kept_parts(board: Board) -> tuple[frozenset[Tile], frozenset[Edge]]:
    tiles = {t for room in pockets(board) for t in room}
    edges = {board.side(t, d) for t in tiles for d in DIRS}
    if sealed(board):
        half = spread(board, team_tiles(board)[0], board.step)
        edges |= {board.side(t, d) for t in half for d, (dx, dy) in DIRS.items()
                  if board.edges.get(board.side(t, d)) == KELP and board.wrap(t[0] + dx, t[1] + dy) not in half}
        edges |= {board.mirror_edge(e) for e in edges}
    return frozenset(tiles), frozenset(edges)


# The dragons whose head has no free neighbouring tile at the start.
def boxed_heads(board: Board) -> list[list[Tile]]:
    taken = {t for body in board.dragons for t in body}
    return [body for body in board.dragons if not head_can_move(board, body[0], taken)]


@dataclass
class Stats:
    """One row of --stats output for a map file. Farm tiles spawn often: max gap <= 100 and min gap < 20."""

    name: str
    w: int
    h: int
    sym: str
    kelp_pct: float
    portal_pairs: int
    dragons_a: int
    dragons_b: int
    lengths: str
    spawn_pct: int
    farm_tiles: int
    reach_pct: int


def map_stats(path: Path) -> Stats:
    text = path.read_text()
    board = parse(text)
    teams = dragon_teams(text)
    area = board.w * board.h
    lengths = [len(b) for b in board.dragons]
    reach = min((len(board.reach(b[0])) for b in board.dragons), default=0)
    return Stats(
        name=path.stem[:22],
        w=board.w,
        h=board.h,
        sym=board.sym,
        kelp_pct=round(100 * board.kelp_count() / (2 * area), 1),
        portal_pairs=len({k for k in board.edges.values() if k >= 0}),
        dragons_a=teams.count(0),
        dragons_b=teams.count(1),
        lengths=f"{min(lengths)}-{max(lengths)}",
        spawn_pct=round(100 * sum(1 for g in board.gaps.values() if g[1] > 0) / area),
        farm_tiles=sum(1 for g in board.gaps.values() if 0 < g[1] <= 100 and g[0] < 20),
        reach_pct=round(100 * reach / area),
    )


def print_stats(dirs: list[str]) -> None:
    header = f"{'map':<22} {'size':>6} {'sym':>3} {'kelp%':>6} {'portals':>7} {'A/B':>5} {'len':>4} {'spawn%':>6} {'farm':>5} {'reach%':>6}"
    for d in dirs:
        rows = [map_stats(p) for p in sorted(Path(d).glob("*.map"))]
        if not rows:
            continue
        print(f"\n## {d} ({len(rows)} maps)\n{header}")
        for r in rows:
            print(
                f"{r.name:<22} {f'{r.w}x{r.h}':>6} {r.sym:>3} {r.kelp_pct:>6} {r.portal_pairs:>7} "
                f"{f'{r.dragons_a}/{r.dragons_b}':>5} {r.lengths:>4} {r.spawn_pct:>6} {r.farm_tiles:>5} {r.reach_pct:>6}"
            )
        print(summary(rows))


def summary(rows: list[Stats]) -> str:
    syms = {s: sum(1 for r in rows if r.sym == s) for s in ("xy", "y", "x")}
    sides = [v for r in rows for v in (r.w, r.h)]
    kelp = [r.kelp_pct for r in rows]
    portals = [r.portal_pairs for r in rows]
    drag = [r.dragons_a for r in rows]
    spawn = [r.spawn_pct for r in rows]
    farms = sum(1 for r in rows if r.farm_tiles > 0)
    return (
        f"summary: sides {min(sides)}-{max(sides)} | sym {syms} | kelp% {min(kelp)}-{max(kelp)} "
        f"(mean {sum(kelp) / len(kelp):.1f}) | portal pairs {min(portals)}-{max(portals)} | "
        f"dragons/team {min(drag)}-{max(drag)} | spawn% {min(spawn)}-{max(spawn)} | maps with farms {farms}/{len(rows)} | "
        f"min reach% {min(r.reach_pct for r in rows)}"
    )


# One line per map in each folder: its fast tiles (FAST_GAP), how many are in dead ends, and that share; then the
# folder's totals and how many maps have at least half their fast tiles in dead ends.
def print_dead_ends(dirs: list[str]) -> None:
    for d in dirs:
        paths = sorted(Path(d).glob("*.map"))
        if not paths:
            continue
        print(f"\n## {d} ({len(paths)} maps)")
        passing = total = half = 0
        for path in paths:
            dead, n = dead_end_count(parse(path.read_text()))
            passing, total, half = passing + dead, total + n, half + (n > 0 and dead >= n / 2)
            print(f"{path.stem[:30]:<30} fast {n:>4}  in dead ends {dead:>4}" + (f"  {dead / n:>4.0%}" if n else ""))
        print(f"total: {passing} of {total} fast tiles in dead ends; {half} of {len(paths)} maps with half or more")


# ---------------------------------------------------------------- engine validation


def unswbc_bin() -> str:
    local = Path(sys.executable).parent / "unswbc"
    return str(local) if local.exists() else shutil.which("unswbc") or "unswbc"


# Waits until the 1-minute load average is under `limit` (this Mac is shared).
def wait_for_load(limit: float) -> None:
    while os.getloadavg()[0] >= limit:
        print(f"  load {os.getloadavg()[0]:.1f} >= {limit}, waiting...", flush=True)
        time.sleep(15)


# Plays one quick game on a map; returns (ok, last line of output).
def play(path: Path, bot: str, load_limit: float) -> tuple[bool, str]:
    wait_for_load(load_limit)
    cmd = [unswbc_bin(), "run", str(path), bot, bot, "--no-replay"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    text = (out.stdout + out.stderr).strip()
    finished = [line for line in text.splitlines() if " after " in line and ("wins" in line or "draw" in line)]
    if out.returncode != 0 or not finished:
        return False, text.splitlines()[-1] if text else f"exit {out.returncode}"
    return True, finished[-1]


# Plays every map in `folder` with the starter bot vs itself, 3 at a time. Returns the number of failures.
def validate(folder: str, bot: str, load_limit: float) -> int:
    if not Path(bot).exists():
        subprocess.run([unswbc_bin(), "init", "cpp", bot], check=True, capture_output=True)
    maps = sorted(Path(folder).glob("*.map"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda p: (p, *play(p, bot, load_limit)), maps))
    bad = 0
    for p, ok, line in results:
        print(f"{'ok  ' if ok else 'FAIL'} {p.name}: {line}")
        bad += not ok
    print(f"\n{len(maps) - bad}/{len(maps)} maps played cleanly")
    return bad


# loop/home.py's WORKER_NICE, the loop's background work (not imported: gen.py runs on its own, as the referee's).
POOL_NICE = 10


# A pool process's initializer: raises its niceness to POOL_NICE, never lowering one it inherited (the driver runs
# gen.py at the games' 19; macOS refuses os.nice(0) at 20, so an increment of 0 isn't asked for).
def lower_priority() -> None:
    if (step := POOL_NICE - os.getpriority(os.PRIO_PROCESS, 0)) > 0:
        os.nice(step)


# The maps for `seeds` in order. Each depends only on its seed, so they are made side by side (2,400 take ~15 s, not
# ~2 min), or one by one where the process pool can't start (a sandbox that denies its semaphores, as the map-ideas
# worker's does). One map a task: a worldgen map takes about 1.5 s and one up to 13 s, so a chunk of them held the
# pool's tail on one worker while the others idled. The pool's processes run at POOL_NICE or the caller's niceness,
# whichever is lower priority: map making is the loop's background work, and sig's dev work wins the CPU over it.
def generate_all(seeds: range | list[int], style: str) -> Iterator[str]:
    try:
        pool = ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1), initializer=lower_priority)
    except OSError:
        yield from (generate(seed, style) for seed in seeds)
        return
    with pool:
        yield from pool.map(generate, seeds, [style] * len(seeds))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0, help="first seed; map i uses seed+i")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seeds", type=lambda text: [int(s) for s in text.split(",")],
                    help="comma-separated seeds instead of --seed and --count (loop/driver.py make_listed)")
    ap.add_argument("--out", help="folder to write maps into")
    ap.add_argument("--style", choices=STYLES, default="mix")
    ap.add_argument("--validate", metavar="DIR", help="play one starter-bot game per map in DIR")
    ap.add_argument("--bot", default="/tmp/starter", help="bot for --validate (created with unswbc init if missing)")
    ap.add_argument("--load-limit", type=float, default=7.0, help="wait while the 1-min load average is above this")
    ap.add_argument("--stats", nargs="+", metavar="DIR", help="print stats for the maps in each DIR")
    ap.add_argument("--deadends", nargs="+", metavar="DIR", help="print each map's fast tiles in dead ends (dead_end)")
    args = ap.parse_args()

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        seeds = args.seeds or range(args.seed, args.seed + args.count)
        for seed, text in zip(seeds, generate_all(seeds, args.style)):
            (out / f"gen_{args.style}_{seed}.map").write_text(text)
        print(f"wrote {len(seeds)} maps to {out}")
    if args.stats:
        print_stats(args.stats)
    if args.deadends:
        print_dead_ends(args.deadends)
    if args.validate:
        sys.exit(1 if validate(args.validate, args.bot, args.load_limit) else 0)
    if not (args.out or args.stats or args.deadends or args.validate):
        ap.print_help()


if __name__ == "__main__":
    main()
