"""Replay tools for UNSW Battlecode 2026: fetch public replays, and turn any replay into checked, structured facts.

Every number comes from replaying the engine's own events. Dragon bodies are tracked segment by segment and checked
against the engine's head/tail updates, split bodies, death pearls and final standings, so a report either agrees
with the engine or says where it doesn't (the "checks" block).

    uv run python tools/replays.py fetch --top-battles --max-games 20
    uv run python tools/replays.py fetch --team 213 --battles 3 [--ranked]
    uv run python tools/replays.py fetch --match 74273 74272
    uv run python tools/replays.py fetch --battle 476876 [--max-games 15]
    uv run python tools/replays.py battles --team 306 --since 2026-09-27T00:00Z --until 2026-09-28T10:00Z
    uv run python tools/replays.py summary data/replays/74273.replay
    uv run python tools/replays.py deaths data/replays/74273.replay [--team A]
    uv run python tools/replays.py lengths data/replays/74273.replay [--every 50]
    uv run python tools/replays.py profile [--index data/replays/index.jsonl] [--min-games 3]
    uv run python tools/replays.py library [--part online|leaders] [--map Trauma]   # the loop's library, JSON lines

Local replays from `unswbc run` work too (packed Cap'n Proto); server replays are the same, gzipped.
Rounds are the engine's 0-based round numbers: a game that goes the distance ends in round 499.
Teams are the replay's sides A and B; A's first dragon moves first. Names come from the fetch index when known.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import capnp

capnp.remove_import_hook()
# Its own parser, not capnp.load's shared one: referee/run.py loads referee/replay.capnp, a different file with the
# same schema id, and one parser holding both aborts the process ("Duplicate ID", MISSES 97). Apart, the two load
# in one process, in either order.
PARSER = capnp.SchemaParser()
SCHEMA = PARSER.load(str(Path(__file__).with_name("replay.capnp")))

REPO = Path(__file__).resolve().parent.parent
REPLAY_DIR = REPO / "data" / "replays"
INDEX = REPLAY_DIR / "index.jsonl"
SITE = "https://game.battlecode.au"
USER_AGENT = "unsw-bc-2026-replay-tools/0.1 (team 157; 1 request/s)"
DEATH_REASONS = ("hitWall", "hitSelf", "hitOtherBody", "hitHeadToHead", "noValidAction")
TEAMS = ("A", "B")
DIRECTIONS = ("north", "east", "south", "west")  # clockwise, as the replay schema numbers them
# Why the engine killed a dragon for noValidAction, from the action it logged that turn. "suicide" is what the
# engine records when a bot sent no usable action.
NO_VALID_ACTION_CAUSES = {"timeout": "timeout", "suicide": "noAction", "split": "illegalSplit",
                          "move": "unpaidSprintStep", "none": "noAction"}

Point = tuple[int, int]
# An edge between two tiles as mapgen/gen.py's Board keys it: ("h", x, y) is tile (x, y)'s north side, ("v", x, y) its
# west side, so tile (x, y)'s south side is ("h", x, y + 1) and its east side ("v", x + 1, y), wrapping.
Edge = tuple[str, int, int]
# The engine's most living dragons a team may have (docs/unsw/protocol.md: UNIT_LIMIT, default 64; the replay doesn't
# carry it), and how close to it a team counts as at the ceiling (CAP_MARGIN; method M71: rules that act at the cap,
# such as the champion's cap feed at unitLimit - rescueReserve, fire only there, and local games sit there far more
# than online ones).
UNIT_LIMIT = 64
CAP_MARGIN = 4


# ---------------------------------------------------------------- loading


# Reads a replay file (gzipped or not) into a Cap'n Proto reader. Used by every analysis command.
def load_replay(path: Path) -> Any:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return SCHEMA.Replay.from_bytes_packed(raw, traversal_limit_in_words=2**62, nesting_limit=128)


@dataclass
class MapInfo:
    name: str
    width: int
    height: int
    symmetry: str
    dragons: list[tuple[str, list[Point]]]  # (team, body head first), in id order
    portals: frozenset[Edge] = frozenset()  # the portal edges, as ("h" or "v", x, y) (edge_key)
    kelp: frozenset[Edge] = frozenset()  # the kelp edges, which no step crosses
    partner: dict[Edge, Edge] = field(default_factory=dict)  # each portal edge's other end (the same portal id)


# The edge an .map EDGE line's index names (docs/unsw/map-files.md: rows of north sides alternate with rows of west
# sides, each width + 1 long), or None for a border repeat the wrap already names; mapgen/gen.py edge_of_index.
def edge_key(width: int, height: int, index: int) -> Edge | None:
    col, row = index % (width + 1), index // (width + 1)
    if col == width or row >= 2 * height:
        return None
    return ("h", col, row // 2) if row % 2 == 0 else ("v", col, (row - 1) // 2)


# The edge a step `direction` ("north", ...) from `tile` crosses, on a width x height board (mapgen's Board.side).
def edge_of_step(tile: Point, direction: str, width: int, height: int) -> Edge:
    x, y = tile
    return {"north": ("h", x, y), "south": ("h", x, (y + 1) % height), "west": ("v", x, y),
            "east": ("v", (x + 1) % width, y)}[direction]


# Parses the .map text embedded in every replay: size, name, the starting dragons, the kelp and the portal edges.
def parse_map(text: str) -> MapInfo:
    info = MapInfo(name="?", width=0, height=0, symmetry="?", dragons=[])
    portals, kelp, ends = set(), set(), {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "MAP":
            info.width, info.height = int(parts[1]), int(parts[2])
        elif parts[0] == "MAP_NAME":
            info.name = line.split(" ", 1)[1].strip()
        elif parts[0] == "SYMMETRY":
            info.symmetry = parts[1]
        elif parts[0] == "DRAGON":
            count = int(parts[2])
            coords = [int(v) for v in parts[3 : 3 + 2 * count]]
            body = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
            info.dragons.append(("A" if parts[1] == "0" else "B", body))
        elif parts[0] == "EDGE" and len(parts) > 2 and parts[2] in ("1", "2"):
            if (edge := edge_key(info.width, info.height, int(parts[1]))) is None:
                continue
            if parts[2] == "1":
                kelp.add(edge)
            else:
                portals.add(edge)
                ends.setdefault(parts[3] if len(parts) > 3 else "?", set()).add(edge)
    info.portals, info.kelp = frozenset(portals), frozenset(kelp)
    info.partner = {a: b for pair in ends.values() if len(pair) == 2 for a, b in (tuple(pair), tuple(pair)[::-1])}
    return info


# ---------------------------------------------------------------- analysis


# A dragon is idle on a move when its last IDLE_WINDOW heads cover at most IDLE_TILES tiles and it ate nothing in
# them: circling in place. Some of it is waiting beside a pearl about to spawn; long runs with nothing in view are
# a bot that has stopped exploring (game 126659: our last dragon circled a 2x2 for ~47 rounds, pearls 4 tiles away).
IDLE_WINDOW = 8
IDLE_TILES = 4
# The opening, for `behaviour.opening`: rounds before OPENING_END. A dragon sees the 7x7 square around its head
# (VISION, Chebyshev distance on the wrapping board); a move is blind when no pearl is in that square.
OPENING_END = 100
VISION = 3
# The pearl race (method M78: local opponents yield pearls the way we do, so a change to how we contest them was judged
# where nobody contests): each pearl both teams saw (in a head's view at the start of its turn), first seen by both
# in RACE_ROUNDS, by the gap between each team's nearest head to it then (path steps on the wrapping board, kelp
# blocking, portals carrying, bodies ignored: method M95, Manhattan read 40 games of 11259's 2+-lead share as 0.694
# where by path it was 0.925; a team's own nearer by 1 is "1 nearer"; a team with no path is RACE_UNREACHABLE away),
# with how many of those pearls each team ate. Pearls nobody ate are left out, so `ate` over `n` is the team's share
# of the race's pearls.
RACE_ROUNDS = (100, 299)
RACE_BUCKETS = ("2+ nearer", "1 nearer", "level", "1 farther", "2+ farther")
RACE_UNREACHABLE = 10**6


@dataclass
class Dragon:
    id: int
    team: str
    body: deque[Point]
    born: int  # round it appeared; -1 for starting dragons
    parent: int | None = None
    born_length: int = 0  # its length when it appeared
    sprints: list[int] = field(default_factory=list)  # the round of each paid sprint step it took (one length each)
    alive: bool = True
    facing: str | None = None  # from the engine's updates; unknown until the dragon first moves
    recent: deque[tuple[Point, bool]] = field(default_factory=lambda: deque(maxlen=IDLE_WINDOW))  # (head, ate)
    idle_run: int = 0
    last_step: str | None = None  # the final step of its own last move; None before its first


# How a team explores in the opening (rounds before OPENING_END): moves made with a pearl in view, the tiles its
# dragons saw at the start of their turns, and its blind moves' first steps.
@dataclass
class Opening:
    moves: int = 0
    in_view: int = 0
    seen: set[Point] = field(default_factory=set)
    blind: int = 0
    blind_after_move: int = 0  # blind moves by a dragon that had moved before, and those that kept its direction
    blind_straight: int = 0
    blind_dirs: Counter[str] = field(default_factory=Counter)


@dataclass
class TeamStats:
    start_dragons: int = 0
    peak_alive: int = 0
    splits: list[dict[str, int]] = field(default_factory=list)
    pearls_eaten: int = 0
    deaths: Counter[str] = field(default_factory=Counter)
    head_ons_started: int = 0
    turns: int = 0
    moves: int = 0
    sprint_moves: int = 0
    sprint_steps: list[int] = field(default_factory=list)
    sonar_pings: int = 0
    sonar_directions: Counter[str] = field(default_factory=Counter)  # relative to facing
    sonar_hits: Counter[str] = field(default_factory=Counter)
    sonar_values: list[tuple[int, int, int]] = field(default_factory=list)  # (round, sender, value)
    pearls_by_phase: Counter[str] = field(default_factory=Counter)
    idle: int = 0  # moves made while going nowhere (see IDLE_WINDOW)
    idle_longest: int = 0
    timeouts: int = 0
    opening: Opening = field(default_factory=Opening)
    race: dict[str, list[int]] = field(default_factory=lambda: {b: [0, 0] for b in RACE_BUCKETS})  # [n, ate]
    # Sprints (moves of 2+ steps) by whether they started a head-on with an enemy head and whether one was in the
    # mover's view as it chose the move (method M77: the leaders' movement sprints read as rams in the sprint counts).
    sprint_kinds: Counter[tuple[bool, bool]] = field(default_factory=Counter)
    crossings: list[tuple[int, int]] = field(default_factory=list)  # (round, dragon id): each move through a portal


# Each team's dragons at the end of a round: how many, the longest, the second-longest (0 with fewer than two),
# their total length, and `spread`, the mean distance from each dragon's head to its nearest teammate's
# (`mean_nearest`; None with fewer than two dragons).
@dataclass
class Snapshot:
    round: int
    alive: dict[str, int]
    longest: dict[str, int]
    total: dict[str, int]
    second: dict[str, int] = field(default_factory=dict)
    spread: dict[str, float | None] = field(default_factory=dict)


@dataclass
class Checks:
    tail_ok: int = 0
    split_ok: int = 0
    death_pearls_ok: int = 0
    standing_ok: bool | None = None
    problems: list[str] = field(default_factory=list)

    # Records a failed check; keeps the first 20 messages so a broken replay can't flood the output.
    def fail(self, message: str) -> None:
        if len(self.problems) < 20:
            self.problems.append(message)
        elif len(self.problems) == 20:
            self.problems.append("... more problems not shown")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.problems and self.standing_ok is not False,
            "tailMatches": self.tail_ok,
            "splitMatches": self.split_ok,
            "deathPearlMatches": self.death_pearls_ok,
            "finalStandingMatches": self.standing_ok,
            "problems": self.problems,
        }


@dataclass
class Game:
    map: MapInfo
    bot_a: str
    bot_b: str
    end_reason: str
    winner: str | None
    last_round: int
    standing: dict[str, dict[str, int]]
    teams: dict[str, TeamStats]
    deaths: list[dict[str, Any]]
    snapshots: list[Snapshot]
    checks: Checks
    # Every pearl eaten, in engine order: round, the eater's id and team, and `death`, the index in `deaths` of the
    # death that dropped it (None for a spawned pearl). A merge is a teammate's death whose pearls another eats.
    meals: list[dict[str, Any]] = field(default_factory=list)
    dragons: dict[int, Dragon] = field(default_factory=dict)  # every dragon that lived, by id, as the game ended


# Walks one replay's events, tracking every dragon's body, and returns the per-team facts. Used by all reports.
def analyse(replay: Any) -> Game:
    info = parse_map(replay.map)
    walker = _Walker(info)
    for event in replay.events:
        walker.on_event(event)
    walker.finish()
    return game_of(replay, info, walker)


# The Game from a walker (a _Walker or subclass that leaves the base state alone) that has walked all of `replay`'s
# events and finished, so a caller that walks with its own subclass (watch.watch) needn't walk the replay twice.
def game_of(replay: Any, info: MapInfo, walker: _Walker) -> Game:
    result = replay.result
    standing = {
        "A": _standing(result.teamA),
        "B": _standing(result.teamB),
    }
    winner = str(result.winner).upper() if result.which() == "winner" else None
    walker.check_standing(standing)
    return Game(
        map=info,
        bot_a=replay.botA,
        bot_b=replay.botB,
        end_reason=str(result.endReason),
        winner=winner,
        last_round=walker.round,
        standing=standing,
        teams=walker.stats,
        deaths=walker.deaths,
        snapshots=walker.snapshots,
        checks=walker.checks,
        meals=walker.meals,
        dragons=walker.dragons,
    )


def _standing(team: Any) -> dict[str, int]:
    return {"alive": team.dragonCount, "longest": team.longestDragon, "total": team.totalLength}


# Replays events in order. Mirrors engine/src/actions.cc: a step pushes the new head, pops the tail unless a pearl
# was eaten, and pops once more if the step is a paid sprint step; a split hands the rear segments to the child.
class _Walker:
    def __init__(self, info: MapInfo) -> None:
        self.dragons: dict[int, Dragon] = {}
        self.stats = {team: TeamStats() for team in TEAMS}
        for dragon_id, (team, body) in enumerate(info.dragons):
            self.dragons[dragon_id] = Dragon(dragon_id, team, deque(body), born=-1, born_length=len(body))
            self.stats[team].start_dragons += 1
        for team in TEAMS:
            self.stats[team].peak_alive = self.stats[team].start_dragons
        self.width, self.height = info.width, info.height
        self.portals, self.kelp, self.partner = info.portals, info.kelp, info.partner
        self.pearls: set[Point] = set()
        self.dropped: dict[Point, int] = {}  # a pearl's tile -> the index in self.deaths of the death that dropped it
        self.meals: list[dict[str, Any]] = []
        self.round = -1
        self.mover: Dragon | None = None
        self.steps: list[str] = []
        self.action = "none"
        self.step_index = 0
        self.ate = False
        self.head_on_victim: dict[str, Any] | None = None
        self.pending_death: tuple[dict[str, Any], int] | None = None
        self.deaths: list[dict[str, Any]] = []
        self.snapshots: list[Snapshot] = []
        self.checks = Checks()
        self.pearl_seen: dict[Point, set[str]] = {}  # a pearl's tile -> the teams that have seen it (RACE_ROUNDS)
        self.pearl_gap: dict[Point, int] = {}  # a raced pearl's tile -> A's nearest head less B's, at the mutual sighting
        self.sprint: tuple[str, bool] | None = None  # this turn's sprint: its team and whether an enemy head was in view
        self.sprint_head_on = False
        self.turn_crossed = False  # this turn's move went through a portal (TeamStats.crossings)

    def on_event(self, event: Any) -> None:
        kind = event.which()
        if kind != "tileChange" and self.pending_death:
            self._close_death_pearls()
        if kind == "roundStart":
            if self.round >= 0:
                self._snapshot()
            self.round = event.roundStart.round
            self.mover = None
        elif kind == "turnStart":
            self._start_turn(event.turnStart.id)
        elif kind == "dragonAction":
            self._on_action(event.dragonAction)
        elif kind == "tileChange":
            self._on_tile(event.tileChange)
        elif kind == "dragonUpdate":
            self._on_update(event.dragonUpdate)
        elif kind == "dragonSplit":
            self._on_split(event.dragonSplit)
        elif kind == "dragonDeath":
            self._on_death(event.dragonDeath)
        elif kind == "sonarPing":
            self._on_sonar(event.sonarPing)

    def finish(self) -> None:
        if self.pending_death:
            self._close_death_pearls()
        self._close_sprint()
        self._snapshot()

    def _start_turn(self, dragon_id: int) -> None:
        self._close_sprint()
        self.mover = self.dragons[dragon_id]
        self.steps = []
        self.action = "none"
        self.step_index = 0
        self.ate = False
        self.head_on_victim = None
        self.turn_crossed = False
        self.stats[self.mover.team].turns += 1
        if self.round <= RACE_ROUNDS[1]:
            view = self._view(self.mover.body[0])
            if self.round < OPENING_END:
                self.stats[self.mover.team].opening.seen.update(view)
            self._note_sightings(self.mover.team, view)

    # The pearls in a team's view as one of its dragons starts its turn, for the pearl race: the second team to see a
    # pearl fixes its gap (race_gap) when that happens in RACE_ROUNDS.
    def _note_sightings(self, team: str, view: list[Point]) -> None:
        for tile in view:
            if tile not in self.pearls:
                continue
            seen = self.pearl_seen.setdefault(tile, set())
            if team in seen:
                continue
            seen.add(team)
            if len(seen) == 2 and self.round >= RACE_ROUNDS[0] and (gap := self._race_gap(tile)) is not None:
                self.pearl_gap[tile] = gap

    # A's nearest living head to `tile` less B's in path steps (RACE_ROUNDS' comment), or None when a team has no
    # head or neither reaches it.
    def _race_gap(self, tile: Point) -> int | None:
        heads = {team: {d.body[0] for d in self.dragons.values() if d.alive and d.team == team and d.body}
                 for team in TEAMS}
        if not all(heads.values()):
            return None
        nearest: dict[str, int] = {}
        seen, frontier, steps = {tile}, [tile], 0
        while frontier and len(nearest) < len(TEAMS):
            for team in TEAMS:
                if team not in nearest and not heads[team].isdisjoint(frontier):
                    nearest[team] = steps
            steps += 1
            following = []
            for at in frontier:
                for direction in ("north", "south", "west", "east"):
                    if (n := self._step(at, direction)) is not None and n not in seen:
                        seen.add(n)
                        following.append(n)
            frontier = following
        if not nearest:
            return None
        return nearest.get("A", RACE_UNREACHABLE) - nearest.get("B", RACE_UNREACHABLE)

    # Where a step `direction` from `tile` lands: across an open edge, out of a portal's other end, or None into kelp
    # (mapgen/gen.py Board.step).
    def _step(self, tile: Point, direction: str) -> Point | None:
        edge = edge_of_step(tile, direction, self.width, self.height)
        if edge in self.kelp:
            return None
        if edge in self.portals:
            if (end := self.partner.get(edge)) is None:
                return None
            _, x, y = end
            return {"north": (x, (y - 1) % self.height), "south": (x, y), "west": ((x - 1) % self.width, y),
                    "east": (x, y)}[direction]
        dx, dy = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}[direction]
        return ((tile[0] + dx) % self.width, (tile[1] + dy) % self.height)

    # Wrapping Manhattan distance, kelp and portals ignored (loop/behaviour_gaps.py's ram reach).
    def _manhattan(self, a: Point, b: Point) -> int:
        dx, dy = abs(a[0] - b[0]) % self.width, abs(a[1] - b[1]) % self.height
        return min(dx, self.width - dx) + min(dy, self.height - dy)

    # Files the turn's sprint, if it made one, by head-on and enemy head in view (TeamStats.sprint_kinds).
    def _close_sprint(self) -> None:
        if self.sprint:
            team, in_view = self.sprint
            self.stats[team].sprint_kinds[self.sprint_head_on, in_view] += 1
        self.sprint, self.sprint_head_on = None, False

    def _chebyshev(self, a: Point, b: Point) -> int:
        dx, dy = abs(a[0] - b[0]) % self.width, abs(a[1] - b[1]) % self.height
        return max(min(dx, self.width - dx), min(dy, self.height - dy))

    # The tiles in the 7x7 square around `head`, wrapping at the board's edges.
    def _view(self, head: Point) -> list[Point]:
        x, y = head
        width, height = self.width, self.height
        ys = [(y + dy) % height for dy in range(-VISION, VISION + 1)]
        return [(tx, ty) for tx in [(x + dx) % width for dx in range(-VISION, VISION + 1)] for ty in ys]

    def _on_action(self, action: Any) -> None:
        dragon = self.dragons[action.id]
        stats = self.stats[dragon.team]
        if action.tle:
            stats.timeouts += 1
            self.action = "timeout"
            return
        self.action = str(action.action.which()) if action._has("action") else "none"
        if self.action != "move":
            return
        self.steps = [str(step) for step in action.action.move]
        stats.moves += 1
        if len(self.steps) > 1:
            stats.sprint_moves += 1
            stats.sprint_steps.append(len(self.steps))
            head = dragon.body[0]
            self.sprint = (dragon.team, any(
                d.alive and d.team != dragon.team and d.body and self._chebyshev(head, d.body[0]) <= VISION
                for d in self.dragons.values()))
        if self.steps:
            if self.round < OPENING_END:
                self._note_opening_move(stats.opening, dragon)
            dragon.last_step = self.steps[-1]

    # Called before the move's steps are applied, so the head and pearls are what the bot saw when it chose it.
    def _note_opening_move(self, opening: Opening, dragon: Dragon) -> None:
        opening.moves += 1
        if any(tile in self.pearls for tile in self._view(dragon.body[0])):
            opening.in_view += 1
            return
        opening.blind += 1
        opening.blind_dirs[self.steps[0]] += 1
        if dragon.last_step is not None:
            opening.blind_after_move += 1
            opening.blind_straight += self.steps[0] == dragon.last_step

    def _on_tile(self, tile: Any) -> None:
        point = (tile.tile.x, tile.tile.y)
        (self.pearls.add if tile.hasPearl else self.pearls.discard)(point)
        if tile.hasPearl:
            self.dropped.pop(point, None)
            self.pearl_seen.pop(point, None)  # a new pearl: nobody has seen it yet
            self.pearl_gap.pop(point, None)
            if self.pending_death:
                record, count = self.pending_death
                self.pending_death = (record, count + 1)
                self.dropped[point] = len(self.deaths) - 1
            return
        death = self.dropped.pop(point, None)
        self.pearl_seen.pop(point, None)
        gap = self.pearl_gap.pop(point, None)
        # Only a dragon stepping onto a pearl clears one, so this is the mover eating.
        if self.mover is None:
            self.checks.fail(f"round {self.round}: pearl cleared outside a turn")
            return
        if gap is not None:
            for team in TEAMS:
                ours = gap if team == "A" else -gap
                bucket = RACE_BUCKETS[max(-2, min(2, ours)) + 2]
                self.stats[team].race[bucket][0] += 1
                self.stats[team].race[bucket][1] += self.mover.team == team
        self.ate = True
        self.meals.append({"round": self.round, "id": self.mover.id, "team": self.mover.team, "death": death})
        self.stats[self.mover.team].pearls_eaten += 1
        self.stats[self.mover.team].pearls_by_phase[phase(self.round)] += 1

    def _on_update(self, update: Any) -> None:
        point = update.head
        head = (point.x, point.y)
        point = update.tail
        tail = (point.x, point.y)
        update_id = update.id
        if self.mover is None:
            dragon = self.dragons[update_id]
        else:
            dragon = self.mover
            if update_id != dragon.id:
                self.checks.fail(f"round {self.round}: update for {update_id} during {dragon.id}'s turn")
                return
            if self.step_index < len(self.steps):
                self._note_crossing(dragon, dragon.body[0], self.steps[self.step_index])
            dragon.body.appendleft(head)
            if not self.ate:
                dragon.body.pop()
            if self.step_index > 0:
                dragon.body.pop()
                dragon.sprints.append(self.round)
            self.step_index += 1
            self._note_idle(dragon, head, self.ate)
            self.ate = False
        if dragon.body[0] == head and dragon.body[-1] == tail:
            self.checks.tail_ok += 1
        else:
            self.checks.fail(
                f"round {self.round}: dragon {dragon.id} tracked head/tail {dragon.body[0]}/{dragon.body[-1]}, "
                f"engine says {head}/{tail}"
            )
            dragon.body[0] = head
        dragon.facing = str(update.facing)

    # Files the turn's move as a portal crossing (TeamStats.crossings) once, at its first step from `tile` through a
    # portal edge: a step that landed, or the fatal one of a dragon that died where it came out (_on_death).
    def _note_crossing(self, dragon: Dragon, tile: Point, direction: str) -> None:
        if (not self.turn_crossed and direction in DIRECTIONS
                and edge_of_step(tile, direction, self.width, self.height) in self.portals):
            self.turn_crossed = True
            self.stats[dragon.team].crossings.append((self.round, dragon.id))

    def _note_idle(self, dragon: Dragon, head: Point, ate: bool) -> None:
        dragon.recent.append((head, ate))
        window = dragon.recent
        if len(window) == IDLE_WINDOW and len({h for h, _ in window}) <= IDLE_TILES and not any(a for _, a in window):
            stats = self.stats[dragon.team]
            stats.idle += 1
            dragon.idle_run += 1
            stats.idle_longest = max(stats.idle_longest, dragon.idle_run)
        else:
            dragon.idle_run = 0

    # Where a ping went relative to the sender's facing, what stopped it, and its value, for `behaviour`.
    def _on_sonar(self, ping: Any) -> None:
        sender = self.dragons[ping.senderId]
        stats = self.stats[sender.team]
        stats.sonar_pings += 1
        direction = str(ping.direction)
        if sender.facing in DIRECTIONS and direction in DIRECTIONS:
            turn = (DIRECTIONS.index(direction) - DIRECTIONS.index(sender.facing)) % 4
            stats.sonar_directions[("forward", "right", "back", "left")[turn]] += 1
        else:
            stats.sonar_directions["unknown"] += 1
        kind = str(ping.hitKind)
        if kind == "unknown":  # replays before protocol 3: infer it from the hit dragon
            if ping.which() == "hitId" and ping.hitId in self.dragons:
                target = self.dragons[ping.hitId]
                head = target.body and target.body[0] == (ping.end.x, ping.end.y)
                kind = ("ally" if target.team == sender.team else "enemy") + ("Head" if head else "")
            else:
                kind = "empty"
        stats.sonar_hits[kind] += 1
        stats.sonar_values.append((self.round, sender.id, ping.value64 or ping.value))

    def _on_split(self, split: Any) -> None:
        parent = self.dragons[split.parentId]
        parent_body = [(p.x, p.y) for p in split.parentBody]
        child_body = [(p.x, p.y) for p in split.childBody]
        old = list(parent.body)
        expected_child = old[len(parent_body) :][::-1]
        if old[: len(parent_body)] == parent_body and expected_child == child_body:
            self.checks.split_ok += 1
        else:
            self.checks.fail(
                f"round {self.round}: split of {parent.id} (tracked length {len(old)}) does not match the engine's "
                f"parent {len(parent_body)} + child {len(child_body)}"
            )
        parent.body = deque(parent_body)
        child = Dragon(split.childId, str(split.team).upper(), deque(child_body), born=self.round, parent=parent.id,
                       born_length=len(child_body), facing=str(split.childFacing))
        self.dragons[child.id] = child
        stats = self.stats[child.team]
        stats.splits.append({"round": self.round, "parent": len(parent_body), "child": len(child_body),
                             "parentId": parent.id, "childId": child.id})
        stats.peak_alive = max(stats.peak_alive, self._alive(child.team))

    def _on_death(self, death: Any) -> None:
        dragon = self.dragons[death.id]
        reason = str(death.reason)
        record: dict[str, Any] = {
            "round": self.round,
            "id": dragon.id,
            "team": dragon.team,
            "reason": reason,
            "length": len(dragon.body),
            "head": list(dragon.body[0]),
            "born": dragon.born,
            "parent": dragon.parent,
            # its team's longest other living dragon (0 when none): the dead one was its team's longest if not shorter
            "longestTeammate": max((len(d.body) for d in self.dragons.values()
                                    if d.alive and d.team == dragon.team and d.id != dragon.id), default=0),
        }
        mover = self.mover
        if mover is not None and mover.id == dragon.id and self.steps:
            record["step"] = self.step_index + 1
            record["of"] = len(self.steps)
            record["direction"] = self.steps[min(self.step_index, len(self.steps) - 1)]
            if reason != "noValidAction":  # an unpaid sprint step is never taken (docs/unsw/movement.md)
                self._note_crossing(dragon, dragon.body[0], record["direction"])
        if reason == "noValidAction" and mover is not None and mover.id == dragon.id:
            record["cause"] = NO_VALID_ACTION_CAUSES.get(self.action, self.action)
        if reason == "hitHeadToHead":
            self._link_head_on(record, mover)
        dragon.alive = False
        self.stats[dragon.team].deaths[reason] += 1
        self.deaths.append(record)
        self.pending_death = (record, 0)

    # The engine kills the dragon that was hit first, then the mover, so the first of the pair is the victim.
    def _link_head_on(self, record: dict[str, Any], mover: Dragon | None) -> None:
        if mover is None:
            self.checks.fail(f"round {self.round}: head-on death outside a turn")
            return
        if record["id"] != mover.id:
            record["hitBy"] = mover.id
            record["hitByTeam"] = mover.team
            record["hitByLength"] = len(mover.body)
            self.head_on_victim = record
            self.stats[mover.team].head_ons_started += 1
            self.sprint_head_on |= record["team"] != mover.team
            return
        victim = self.head_on_victim
        if victim is None:
            self.checks.fail(f"round {self.round}: head-on death of mover {mover.id} with no victim")
            return
        record["movedInto"] = victim["id"]
        record["movedIntoTeam"] = victim["team"]
        record["movedIntoLength"] = victim["length"]

    # A death drops a pearl on every second segment from the head: ceil(L/2) tileChange events straight after it.
    def _close_death_pearls(self) -> None:
        assert self.pending_death
        record, count = self.pending_death
        self.pending_death = None
        if count == math.ceil(record["length"] / 2):
            self.checks.death_pearls_ok += 1
        else:
            self.checks.fail(
                f"round {record['round']}: dragon {record['id']} tracked length {record['length']} "
                f"but dropped {count} pearls"
            )

    def _alive(self, team: str) -> int:
        return sum(1 for d in self.dragons.values() if d.alive and d.team == team)

    def _snapshot(self) -> None:
        alive, longest, total, second, spread = {}, {}, {}, {}, {}
        for team in TEAMS:
            living = [d for d in self.dragons.values() if d.alive and d.team == team]
            lengths = sorted((len(d.body) for d in living), reverse=True)
            alive[team] = len(lengths)
            longest[team] = lengths[0] if lengths else 0
            second[team] = lengths[1] if len(lengths) > 1 else 0
            total[team] = sum(lengths)
            spread[team] = mean_nearest([d.body[0] for d in living], self.width, self.height)
        self.snapshots.append(Snapshot(self.round, alive, longest, total, second, spread))

    def check_standing(self, standing: dict[str, dict[str, int]]) -> None:
        last = self.snapshots[-1]
        tracked = {t: {"alive": last.alive[t], "longest": last.longest[t], "total": last.total[t]} for t in TEAMS}
        self.checks.standing_ok = tracked == standing
        if not self.checks.standing_ok:
            self.checks.fail(f"final standing: tracked {tracked}, engine says {standing}")


# The mean, over `heads`, of each head's distance to the nearest other one on the wrapping board, or None with fewer
# than two. Distance is Chebyshev, as for VISION: at VISION or less two dragons see each other. Gives the snapshots'
# `spread`.
def mean_nearest(heads: list[Point], width: int, height: int) -> float | None:
    count = len(heads)
    if count < 2:
        return None
    # Each pair once, updating both ends, with the arithmetic inline: a snapshot a round, up to 64 heads a team.
    nearest = [width + height] * count
    for i in range(count - 1):
        x, y = heads[i]
        best = nearest[i]
        for j in range(i + 1, count):
            u, v = heads[j]
            dx = x - u if x >= u else u - x
            if width - dx < dx:
                dx = width - dx
            dy = y - v if y >= v else v - y
            if height - dy < dy:
                dy = height - dy
            d = dx if dx >= dy else dy
            if d < best:
                best = d
            if d < nearest[j]:
                nearest[j] = d
        nearest[i] = best
    return sum(nearest) / count


# ---------------------------------------------------------------- reports


# Game phases for `behaviour`: the opening, the midgame, and the run-in to the round-500 length tiebreak.
def phase(round_: int) -> str:
    return "early" if round_ < 100 else "mid" if round_ < 300 else "late"


# `team`'s king line: the team's longest dragon when the game ended (the lowest id on a tie) and, before it was born,
# the dragon it split from, and so on back to a starting dragon, a king that split out living on in its child. Each
# as (dragon, the round it became king, the round its child took over or inf), the final king first; empty when the
# team was eliminated. A dragon of the line is king from its birth to its child's (a split is its whole turn, so it
# eats and moves nothing in the round it splits).
def king_line(game: Game, team: str) -> list[tuple[Dragon, float, float]]:
    alive = [d for d in game.dragons.values() if d.alive and d.team == team]
    if not alive:
        return []
    line = [max(alive, key=lambda d: (len(d.body), -d.id))]
    while line[-1].parent is not None and line[-1].parent in game.dragons:
        line.append(game.dragons[line[-1].parent])
    return [(d, d.born, line[i - 1].born if i else math.inf) for i, d in enumerate(line)]


# The pearls `team`'s king (king_line) ate, in engine order, each as (its meal record, the death record that dropped
# it or None for a spawned pearl). Empty when the team was eliminated. A teammate's drop from any death but a head-on,
# wherever it happened, that the king ate is a merge (the fold and the feeder die into kelp or their own body near
# it); this is where the king's length came from, summarised by loop/rulecounts.py's growth by source.
def king_meals(game: Game, team: str) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    spans = {d.id: (start, until) for d, start, until in king_line(game, team)}
    return [(m, None if m["death"] is None else game.deaths[m["death"]]) for m in game.meals
            if m["id"] in spans and spans[m["id"]][0] <= m["round"] < spans[m["id"]][1]]


# What `team`'s king line (king_line) lost besides growing, as (the length its starting dragon began with, [(round,
# the length of a piece the king split off: a child, or at a split-out or even split, where the line goes on in the
# child as long as the parent or longer, what the parent kept, its old head)], [(round, what the parent kept at a
# split that handed the line on to a shorter child)], [(round, length the king's sprint steps that round cost)]), or
# None when the team was eliminated. A handover is a change of king at a split: the parent, the longer piece, lives
# on outside the line (docs/unsw/splitting.md: the parent keeps the head and its id). With
# king_meals (one length a pearl) this gives the line's length at any round: its start plus its meals less these, up
# to the end of that round (loop/rulecounts.py's king rows; method note M47: the growth left after the king's meals
# was mostly a change of which dragon was longest, not the king's own losses).
def king_losses(game: Game, team: str) -> tuple[int, list[tuple[int, int]], list[tuple[int, int]],
                                                list[tuple[int, int]]] | None:
    line = king_line(game, team)
    if not line:
        return None
    heirs = {d.id: line[i - 1][0].id if i else None for i, (d, _, _) in enumerate(line)}
    spans = {d.id: (start, until) for d, start, until in line}
    children, handovers = [], []
    for split in game.teams[team].splits:
        if split["parentId"] not in heirs:
            continue
        start, until = spans[split["parentId"]]
        if split["childId"] == heirs[split["parentId"]]:
            (handovers if split["parent"] > split["child"] else children).append((split["round"], split["parent"]))
        elif start <= split["round"] < until:
            children.append((split["round"], split["child"]))
    sprints = Counter(r for d, start, until in line for r in d.sprints if start <= r < until)
    return line[-1][0].born_length, children, handovers, sorted(sprints.items())


# How a team plays, beyond counts: sonar use (direction relative to facing, what pings hit, value patterns;
# `sharedValuePct` is the share of pings whose value another teammate also sent, a sign of relayed or shared
# state), which head-ons it starts against longer or shorter dragons, and when it splits and eats. `hits` uses the
# engine's own hit kinds (kelp, ally, allyHead, enemy, enemyHead, empty); values are the full 64 bits.
def behaviour(game: Game, team: str) -> dict[str, Any]:
    s = game.teams[team]
    senders: dict[int, set[int]] = {}
    for _, sender, value in s.sonar_values:
        senders.setdefault(value, set()).add(sender)
    shared = sum(1 for _, _, value in s.sonar_values if len(senders[value]) > 1)
    bits = [value.bit_length() for _, _, value in s.sonar_values]
    started: Counter[str] = Counter()
    for death in game.deaths:
        if death.get("hitByTeam") != team:
            continue
        if death["team"] == team:
            started["vsAlly"] += 1
        else:
            mover, victim = death["hitByLength"], death["length"]
            started["vsLonger" if mover < victim else "vsEqual" if mover == victim else "vsShorter"] += 1
    return {
        "sonar": {
            "pings": s.sonar_pings,
            "perTurn": round(s.sonar_pings / s.turns, 3) if s.turns else 0.0,
            "direction": dict(s.sonar_directions),
            "hits": dict(s.sonar_hits),
            "distinctValues": len(senders),
            "valueBitsMedian": _median(bits),
            "sharedValuePct": round(100 * shared / s.sonar_pings, 1) if s.sonar_pings else 0.0,
            "byPhase": dict(Counter(phase(r) for r, _, _ in s.sonar_values)),
        },
        "headOnsStarted": dict(started),
        "splitsByPhase": dict(Counter(phase(x["round"]) for x in s.splits)),
        "pearlsByPhase": dict(s.pearls_by_phase),
        "idle": {"moves": s.idle, "pctOfMoves": round(100 * s.idle / s.moves, 1) if s.moves else 0.0,
                 "longestRun": s.idle_longest},
        "opening": opening(game, team),
    }


# Exploration in rounds 0-99: `pearlInViewPct` of moves were made with a pearl in the 7x7 view; `mapSeenPct` of the
# board's tiles were in some dragon's view at the start of its turn by round 100; `blindMoves` had no pearl in view,
# `blindStraightPct` of those (by dragons that had moved before) kept the dragon's last direction, and
# `blindDirPct` is where their first step went (about 25 each unless something steers them one way).
def opening(game: Game, team: str) -> dict[str, Any]:
    o = game.teams[team].opening
    pct = lambda part, whole: round(100 * part / whole, 1) if whole else 0.0  # noqa: E731
    return {
        "moves": o.moves,
        "pearlInViewPct": pct(o.in_view, o.moves),
        "mapSeenPct": pct(len(o.seen), game.map.width * game.map.height),
        "blindMoves": o.blind,
        "blindStraightPct": pct(o.blind_straight, o.blind_after_move),
        "blindDirPct": {d: pct(o.blind_dirs[d], o.blind) for d in DIRECTIONS},
    }


def _median(values: list[int]) -> float | None:
    return statistics.median(values) if values else None


def _alive_at(game: Game, team: str, round_: int) -> int:
    for snap in game.snapshots:
        if snap.round >= round_:
            return snap.alive[team]
    return game.snapshots[-1].alive[team]


# One team's longest dragon and total length at `round_`, or None when the game ended before it. At round 400 it
# shows how concentrated a team was before the endgame (review M21: rivals' longest there was median 18 to our 6).
def _length_at(game: Game, team: str, round_: int) -> dict[str, int] | None:
    snap = next((s for s in game.snapshots if s.round >= round_), None)
    return None if snap is None else {"longest": snap.longest[team], "total": snap.total[team]}


# The round `king_at_fold` reads the king line's length at the end of: the one before foldRound 397, the champion's
# since E0105 (2026-09-26), fixed so every version of ours, and every opponent, is read on the same round.
KING_ROUND = 396
# The round king_at_fold's `build` starts from (method M99: the r396 king gap is built in r251-396, where the online
# decomposition of 55 round-limit games found teammate-drop meals 7.4 to the leaders' 19.5 and 12.5).
BUILD_ROUND = 250


# `team`'s king line (king_line) at the fold, for a game that went to the round limit (review M52: in 37 online
# round-500 losses their king at r396 was 19.5 long against our 4.8): its length at the end of KING_ROUND and, from
# then to the end, the pearls it ate, the pieces it split off, its paid sprint steps and what a parent kept when it
# went on in a shorter child (king_losses), which take it to its length at the end; and `build`, how it got to its
# KING_ROUND length: its length at the end of BUILD_ROUND and, over the rounds after it to KING_ROUND, its meals by
# where the pearl came from (spawned, a teammate's drop, an enemy's) and the same losses, which add up to that length.
# None for a game that ended earlier or a team eliminated.
def king_at_fold(game: Game, team: str) -> dict[str, Any] | None:
    losses = king_losses(game, team)
    if game.end_reason != "roundLimit" or losses is None:
        return None
    born, children, handovers, sprints = losses
    fed = [(m["round"], "spawned" if drop is None else "teammateDrops" if drop["team"] == team else "enemyDrops")
           for m, drop in king_meals(game, team)]
    meals = [(r, 1) for r, _ in fed]

    def part(events: list[tuple[int, int]], after: int, to: int = 10**9) -> int:  # the total over rounds after..to
        return sum(n for r, n in events if after < r <= to)

    def length(to: int) -> int:
        return born + part(meals, -1, to) - sum(part(e, -1, to) for e in (children, handovers, sprints))

    build = {"round": BUILD_ROUND, "length": length(BUILD_ROUND),
             **{kind: sum(BUILD_ROUND < r <= KING_ROUND and k == kind for r, k in fed)
                for kind in ("spawned", "teammateDrops", "enemyDrops")},
             **{name: part(e, BUILD_ROUND, KING_ROUND) for name, e in
                (("splitOff", children), ("sprints", sprints), ("handedOn", handovers))}}
    return {"round": KING_ROUND, "length": length(KING_ROUND), "meals": part(meals, KING_ROUND),
            "splitOff": part(children, KING_ROUND), "sprints": part(sprints, KING_ROUND),
            "handedOn": part(handovers, KING_ROUND), "build": build}


# How spread out a team was in each `phase`: the mean over the phase's rounds of its snapshots' `spread` (each dragon's
# distance to its nearest teammate's head), with how many rounds counted, which are those it had two or more dragons.
# None for a phase with no such round, so loop/maintenance.py's mean_facts averages a phase only over the games that
# have it. A scoring change that trades spreading for pearls shows here and in `at400`.
def spread_by_phase(game: Game, team: str) -> dict[str, dict[str, float] | None]:
    rounds: dict[str, list[float]] = {"early": [], "mid": [], "late": []}
    for snap in game.snapshots:
        if (value := snap.spread.get(team)) is not None:
            rounds[phase(snap.round)].append(value)
    return {name: {"mean": round(statistics.mean(values), 2), "rounds": len(values)} if values else None
            for name, values in rounds.items()}


# The share (percent) of the game's rounds that ended with `team` within CAP_MARGIN of UNIT_LIMIT living dragons.
def cap_rounds_pct(game: Game, team: str) -> float:
    at_cap = sum(snap.alive[team] >= UNIT_LIMIT - CAP_MARGIN for snap in game.snapshots)
    return round(100 * at_cap / len(game.snapshots), 2) if game.snapshots else 0.0


# Portal crossings (method M80: ranks 1, 2, 3 and 5 crossed 107-159 times a game to our 34): `team`'s moves through a
# portal (TeamStats.crossings, a sprint through two once), how many of them its crosser died within CROSSING_WINDOW
# rounds of (the key names it), whatever killed it, and of those how many in the crossing's own round (on the move
# itself, coming out onto a body, a head or kelp, or hit before its next turn).
CROSSING_WINDOW = 10


def portal_crossings(game: Game, team: str) -> dict[str, int]:
    died = {d["id"]: d["round"] for d in game.deaths}
    crossings = game.teams[team].crossings
    return {"crossings": len(crossings),
            "diedWithin10": sum(i in died and 0 <= died[i] - r <= CROSSING_WINDOW for r, i in crossings),
            "diedSameRound": sum(died.get(i) == r for r, i in crossings)}


# In-place deaths (method M84: rank 1 pumps dead ends, a 2-long dragon walks in, eats, splits its rear out and dies
# where it stands): `team`'s split-out parents, as tools/watch.py split_out_parent reads them from the moves (any death
# of a watch.SPLIT_OUT_PARENT-long dragon the round after its own split that no enemy head caused), then its other
# noValidAction deaths (an illegal split or no usable action), and its stations, the tiles where STATION_DEATHS or more
# of those happened in the game: a dead end pumped again and again. A timeout is no pump, and nor is an unpaid sprint
# step (the replay's logged action names it: NO_VALID_ACTION_CAUSES), a dragon too short for its sprint: those are
# counted apart ("unpaidSprint"), in neither the deaths nor the stations.
STATION_DEATHS = 5


def in_place_deaths(game: Game, team: str) -> dict[str, int]:
    import watch  # it imports this module
    splits = {(s["parentId"], s["round"]) for s in game.teams[team].splits}
    idle = parents = unpaid = 0
    tiles: Counter[Point] = Counter()
    for d in game.deaths:
        if d["team"] != team:
            continue
        if (d["length"] <= watch.SPLIT_OUT_PARENT and not watch.killed_by_enemy(d)
                and any((d["id"], r) in splits for r in range(d["round"] - watch.SPLIT_OUT_WINDOW, d["round"]))):
            parents += 1
        elif d["reason"] == "noValidAction" and d.get("cause") == "unpaidSprintStep":
            unpaid += 1
            continue
        elif d["reason"] == "noValidAction" and d.get("cause") != "timeout":
            idle += 1
        else:
            continue
        tiles[tuple(d["head"])] += 1
    return {"deaths": idle + parents, "noValidAction": idle, "splitOutParents": parents, "unpaidSprint": unpaid,
            "stations": sum(n >= STATION_DEATHS for n in tiles.values())}


# The map's size band by tiles (loop/meta.py AREA_BANDS' names, the bands every loop report splits by).
def size_band(width: int, height: int) -> str:
    if (loop := str(REPO / "loop")) not in sys.path:
        sys.path.append(loop)
    from meta import area_band
    return area_band(width * height)


# Builds the `summary` JSON for one team of one game. `profile` aggregates these.
def team_summary(game: Game, team: str) -> dict[str, Any]:
    s = game.teams[team]
    splits = s.splits
    parent_lengths = [x["parent"] for x in splits]
    child_lengths = [x["child"] for x in splits]
    out: dict[str, Any] = {
        "won": game.winner == team,
        "end": {**game.standing[team], "second": game.snapshots[-1].second[team]},  # engine standing + 2nd-longest
        "startDragons": s.start_dragons,
        "peakAlive": s.peak_alive,
        "capRoundsPct": cap_rounds_pct(game, team),
        "portals": portal_crossings(game, team),
        "inPlace": in_place_deaths(game, team),
        "aliveAt100": _alive_at(game, team, 100),
        "aliveAt300": _alive_at(game, team, 300),
        "at400": _length_at(game, team, 400),
        "king": king_at_fold(game, team),
        "spread": spread_by_phase(game, team),
        "splits": {
            "count": len(splits),
            "firstRound": splits[0]["round"] if splits else None,
            "childLenMedian": _median(child_lengths),
            "parentLenMedian": _median(parent_lengths),
            "childLenCounts": dict(sorted(Counter(child_lengths).items())),
            "parentLenCounts": dict(sorted(Counter(parent_lengths).items())),
        },
        "pearlsEaten": s.pearls_eaten,
        "deaths": {"total": sum(s.deaths.values()), **{r: s.deaths[r] for r in DEATH_REASONS}},
        "headOnsStarted": s.head_ons_started,
        "turns": s.turns,
        "moves": s.moves,
        "sprintMoves": s.sprint_moves,
        "sprintPct": round(100 * s.sprint_moves / s.moves, 2) if s.moves else 0.0,
        "sprintLenCounts": dict(sorted(Counter(s.sprint_steps).items())),
        "sprintKinds": {"headOn": {"enemyInView": s.sprint_kinds[True, True],
                                   "noEnemyInView": s.sprint_kinds[True, False]},
                        "noHeadOn": {"enemyInView": s.sprint_kinds[False, True],
                                     "noEnemyInView": s.sprint_kinds[False, False]}},
        "pearlRace": {bucket: {"n": n, "ate": ate} for bucket, (n, ate) in s.race.items()},
        "raceBy": "path",  # M95: summaries saved before it measured the race in Manhattan steps (online.stale_summary)
        "sonarPings": s.sonar_pings,
        "sonarPerTurn": round(s.sonar_pings / s.turns, 3) if s.turns else 0.0,
        "timeouts": s.timeouts,
        "behaviour": behaviour(game, team),
    }
    return out


# The pearl race (RACE_ROUNDS, RACE_BUCKETS) pooled over team summaries: one line giving, per gap, the share of the
# raced pearls the team ate and how many there were, over the summaries that have it, or None when none does. A
# summary whose race isn't by path (`raceBy`, M95: saved before, in Manhattan steps, until online.refresh_summaries
# re-reads it) is skipped and counted in the line, so the two never mix.
def race_text(teams: list[dict[str, Any]]) -> str | None:
    races = [t["pearlRace"] for t in teams if t.get("pearlRace") and t.get("raceBy") == "path"]
    skipped = sum(bool(t.get("pearlRace")) and t.get("raceBy") != "path" for t in teams)
    old = f"; {skipped} older Manhattan summaries skipped" if skipped else ""
    if not races:
        return f"none by path yet ({skipped} older Manhattan summaries skipped)" if skipped else None
    cells = []
    for bucket in RACE_BUCKETS:
        n = sum(r.get(bucket, {}).get("n", 0) for r in races)
        ate = sum(r.get(bucket, {}).get("ate", 0) for r in races)
        cells.append(f"{bucket} {100 * ate / n:.1f}% of {n}" if n else f"{bucket} none")
    return f"{', '.join(cells)} ({len(races)} games{old})"


# Sprints pooled over team summaries (sprintKinds, M77), as one line, or None when none has them.
def sprint_text(teams: list[dict[str, Any]]) -> str | None:
    kinds = [t["sprintKinds"] for t in teams if t.get("sprintKinds")]
    if not kinds:
        return None
    total = lambda ram, view: sum(k[ram][view] for k in kinds)  # noqa: E731
    per = lambda n: f"{n / len(kinds):.2f}"  # noqa: E731
    return (f"a game, head-ons {per(total('headOn', 'enemyInView') + total('headOn', 'noEnemyInView'))}, no head-on "
            f"with an enemy head in view {per(total('noHeadOn', 'enemyInView'))}, no head-on and none in view "
            f"{per(total('noHeadOn', 'noEnemyInView'))} ({len(kinds)} games)")


# Portal crossings and in-place deaths (portal_crossings, in_place_deaths: M80, M84) pooled over team summaries, as
# one line, or None when none has them: a game's means, and the crossings' share dying within CROSSING_WINDOW rounds.
def crossing_text(teams: list[dict[str, Any]]) -> str | None:
    rows = [t for t in teams if t.get("portals") and t.get("inPlace")]
    if not rows:
        return None
    per = lambda group, key: f"{sum(t[group].get(key, 0) for t in rows) / len(rows):.1f}"  # noqa: E731
    crossed = sum(t["portals"]["crossings"] for t in rows)
    soon, landed = (sum(t["portals"][key] for t in rows) for key in ("diedWithin10", "diedSameRound"))
    share = (f"{100 * soon / crossed:.1f}% of them dying within {CROSSING_WINDOW} rounds, {100 * landed / crossed:.1f}% "
             "in the crossing's round") if crossed else "none dying"
    return (f"portal crossings {per('portals', 'crossings')} a game ({share}), "
            f"in-place deaths {per('inPlace', 'deaths')} a game (noValidAction {per('inPlace', 'noValidAction')}, "
            f"split-out parents {per('inPlace', 'splitOutParents')}), stations {per('inPlace', 'stations')} a game, "
            f"unpaid sprint steps apart {per('inPlace', 'unpaidSprint')} ({len(rows)} games)")


# Builds the full `summary` JSON for one replay; `meta` is its index row when the replay was fetched.
def game_summary(game: Game, meta: dict[str, Any] | None) -> dict[str, Any]:
    teams = {}
    for team in TEAMS:
        entry = {}
        if meta:
            entry["name"] = meta.get(f"team{team}Name")
            entry["id"] = meta.get(f"team{team}Id")
        entry.update(team_summary(game, team))
        teams[team] = entry
    out: dict[str, Any] = {
        "map": {"name": game.map.name, "width": game.map.width, "height": game.map.height,
                "symmetry": game.map.symmetry, "sizeBand": size_band(game.map.width, game.map.height)},
        "result": {"endReason": game.end_reason, "endRound": game.last_round, "winner": game.winner},
        "teams": teams,
        "checks": game.checks.as_dict(),
    }
    if meta:
        out = {"match": meta.get("match"), "battle": meta.get("battle"), **out}
    if game.bot_a or game.bot_b:
        out["bots"] = {"A": game.bot_a, "B": game.bot_b}
    return out


# ---------------------------------------------------------------- index


# Reads index.jsonl into {match id: row}; a later row for the same match wins.
def read_index(path: Path) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["match"]] = row
    return rows


# The replays among `paths` kept for their game's result, not blind to it (MISSES 55): those in a
# losses/ folder (an SPRT's runs/E####/losses, a post-mortem's .postmortem/losses) and those whose folder's
# index.json (the loop's online-replays/<version>/) lists them marked "kept" ("loss") or "dev_wins_pruned" (a dev
# game of a version whose dev wins' files were deleted, online.file_selected). Counts per game over them
# describe losses and draws, not games, so the tallies that average over replays say how many there are.
def result_chosen(paths: list[Path]) -> list[Path]:
    marked: dict[Path, set[str]] = {}
    chosen = []
    for path in paths:
        folder = path.resolve().parent
        if folder not in marked:
            try:
                saved = json.loads((folder / "index.json").read_text()).get("games", [])
                # loop/online.py's file_selected, inline: this tool imports nothing of the loop's.
                marked[folder] = {Path(g["replay"]).name for g in saved if isinstance(g, dict)
                                  and (g.get("kept") or g.get("dev_wins_pruned")) and g.get("replay")}
            except (OSError, ValueError, AttributeError, TypeError):
                marked[folder] = set()
        if folder.name == "losses" or path.name in marked[folder]:
            chosen.append(path)
    return chosen


# A line for a tally over `paths` naming the result-chosen replays among them (result_chosen), or None.
def chosen_note(paths: list[Path]) -> str | None:
    chosen = result_chosen(paths)
    if not chosen:
        return None
    return (f"note: {len(chosen)} of these {len(paths)} replays are no result-blind sample (a losses/ folder or "
            "\"kept\" in the folder's index.json: kept because the game was lost; \"dev_wins_pruned\": a dev game's "
            "file left after its version's dev wins were deleted, a loss, draw or unreadable result), so these counts "
            "describe losses and draws, not games; for a rate, count the other replays alone or read the full record")


# Finds the index row for a replay file named <match>.replay, if it was fetched into a directory with an index.
def meta_for(path: Path) -> dict[str, Any] | None:
    if not path.stem.isdigit():
        return None
    return read_index(path.parent / "index.jsonl").get(int(path.stem))


# ---------------------------------------------------------------- fetching


# One polite HTTP client: a fixed User-Agent and at least a second between requests.
class Http:
    def __init__(self) -> None:
        self.last = 0.0
        self.requests = 0

    def get(self, url: str) -> bytes:
        wait = self.last + 1.0 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        finally:
            self.last = time.monotonic()
            self.requests += 1


# Decodes SvelteKit's `devalue` flat format (what `/__data.json` returns) into plain Python values. A deferred
# field is ["Promise", i] with values[i] its chunk id; `chunks` holds the streamed chunk lines by id.
def devalue(values: list[Any], chunks: dict[int, dict[str, Any]] | None = None) -> Any:
    specials = {-1: None, -2: None, -3: math.nan, -4: math.inf, -5: -math.inf, -6: -0.0}
    done: dict[int, Any] = {}

    def hydrate(index: int) -> Any:
        if index in specials:
            return specials[index]
        if index in done:
            return done[index]
        value = values[index]
        if isinstance(value, dict):
            result: Any = {key: hydrate(i) for key, i in value.items()}
        elif isinstance(value, list) and value and value[0] == "Promise":
            result = _resolve_chunk(hydrate(value[1]), chunks or {})
        elif isinstance(value, list) and value and isinstance(value[0], str):
            result = _devalue_tagged(value, hydrate)
        elif isinstance(value, list):
            result = [hydrate(i) for i in value]
        else:
            result = value
        done[index] = result
        return result

    return hydrate(0)


# The value a deferred field resolved to, from its streamed `{"type":"chunk","id":N,"data":[...]}` line.
def _resolve_chunk(chunk_id: int, chunks: dict[int, dict[str, Any]]) -> Any:
    chunk = chunks.get(chunk_id)
    if chunk is None:
        raise ValueError(f"deferred field: chunk {chunk_id} was never streamed")
    if "data" not in chunk:
        raise ValueError(f"deferred field: chunk {chunk_id} failed: {str(chunk.get('error'))[:200]}")
    return devalue(chunk["data"], chunks)


def _devalue_tagged(value: list[Any], hydrate: Any) -> Any:
    tag = value[0]
    if tag == "Date":
        return value[1]
    if tag == "BigInt":
        return int(value[1])
    if tag == "Set":
        return [hydrate(i) for i in value[1:]]
    if tag == "Map":
        return {hydrate(value[i]): hydrate(value[i + 1]) for i in range(1, len(value), 2)}
    if tag == "null":
        return {value[i]: hydrate(value[i + 1]) for i in range(1, len(value), 2)}
    raise ValueError(f"unsupported devalue tag {tag!r}")


# Fetches a site page's server data (the same data the page renders) as Python values, following the site's
# redirects (/top-battles now redirects to /battles?sort=rating). The first line is the page's data; the site streams
# deferred fields (a team page's `ranks`) as `{"type":"chunk"}` lines after it, which are resolved into place.
# Anything else (an error page, a missing chunk) raises ValueError naming the URL.
def page_data(http: Http, path: str, redirects: int = 3) -> Any:
    base, _, query = path.partition("?")
    url = f"{SITE}{base.rstrip('/')}/__data.json" + (f"?{query}" if query else "")
    lines = [json.loads(line) for line in http.get(url).splitlines() if line.strip()]
    payload, rest = (lines[0], lines[1:]) if lines else (None, [])
    if isinstance(payload, dict) and payload.get("type") == "redirect" and redirects:
        return page_data(http, payload["location"], redirects - 1)
    if not isinstance(payload, dict) or payload.get("type") != "data" or not isinstance(payload.get("nodes"), list):
        kind = payload.get("type") if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"{url}: expected page data first, got {kind!r}")
    nodes = [n for n in payload["nodes"] if n and n.get("type") == "data"]
    if not nodes:
        raise ValueError(f"{url}: page data has no data node")
    chunks = {c["id"]: c for c in rest if isinstance(c, dict) and c.get("type") == "chunk"}
    try:
        return devalue(nodes[-1]["data"], chunks)
    except ValueError as error:
        raise ValueError(f"{url}: {error}") from error


AUTOSCRIM_AT = re.compile(r"\d{4}-\d\d-\d\dT(\d\d):00:00\.000Z")


# True when a battle's requestedAt is the site's autoscrim draw: exactly on an even UTC hour, such as
# "2026-09-27T02:00:00.000Z". A battle a team requests carries its request's own time, to the millisecond; of our 215
# ranked battles on 2026-09-27, 82 were requested exactly on an even hour and none on an odd one.
def autoscrim_draw(requested_at: str | None) -> bool | None:
    if not requested_at:  # rows fetched before 2026-09-25 16:55 carry no requestedAt: unknown, not "a team asked"
        return None
    found = AUTOSCRIM_AT.fullmatch(requested_at)
    return found is not None and int(found.group(1)) % 2 == 0


@dataclass
class Fetcher:
    http: Http
    out_dir: Path
    max_games: int | None
    index: dict[int, dict[str, Any]] = field(default_factory=dict)
    downloaded: int = 0
    battles: int = 0  # battles tried, and how many of those failed and were skipped
    skipped: int = 0

    def budget_left(self) -> bool:
        return self.max_games is None or self.downloaded < self.max_games

    def cached(self, match_id: int) -> bool:
        return match_id in self.index and (self.out_dir / f"{match_id}.replay").exists()

    # The match ids to fetch from battle rows (a team page's `recent`, /top-battles' `battles`), in order, at most
    # `limit`. A row's replayId is its battle's first game with a replay, so it is null while every game is pending
    # ("queued", or "live" with every result "pending": 4 of the leaders' 30 rows at 16:04 AEST on 2026-09-29, just
    # after a ranked draw): such a row is passed over for the next one, never fetched as match None (MISSES 92). A
    # finished row without one counts as a skipped battle, so a site that stops sending the field fails the fetch
    # (every battle skipped) instead of quietly fetching nothing (MISSES 77); skipped rows don't use up `limit`.
    def battle_ids(self, rows: list[dict[str, Any]], limit: int | None = None) -> list[int]:
        ids: list[int] = []
        for b in rows:
            if limit is not None and len(ids) >= limit:
                break
            if isinstance(b.get("replayId"), int):
                ids.append(b["replayId"])
            elif b.get("outcome") in ("live", "queued") or "pending" in (b.get("results") or b.get("resultsA") or []):
                print(f"battle {b.get('id')}: passed over, {b.get('outcome')} with no game finished", file=sys.stderr)
            else:
                self.battles += 1
                self.skipped += 1
                print(f"battle {b.get('id')}: skipped, {b.get('outcome')} but no replayId", file=sys.stderr)
        return ids

    # Fetches the games of the battle that `match_id` belongs to (only `only` if given), skipping cached ones. A
    # battle that fails (an odd page, a replay that won't parse) is logged and skipped, so one bad battle never
    # stops the rest of a refresh (cmd_fetch fails only when every battle did).
    def battle(self, match_id: int, only: set[int] | None = None) -> None:
        self.battles += 1
        try:
            self.fetch_battle(match_id, only)
        except Exception as error:  # noqa: BLE001 - any failure is one battle's, not the refresh's
            self.skipped += 1
            print(f"battle of match {match_id}: skipped, {type(error).__name__}: {error}", file=sys.stderr)

    # Each index row carries the battle's submissions (null since the site stopped naming them on 2026-09-28, MISSES
    # 77) and whether the site's draw requested it (autoscrim_draw), so a
    # leader that switches submissions around the draws can be told apart from one that plays a single version.
    def fetch_battle(self, match_id: int, only: set[int] | None = None) -> None:
        vis = page_data(self.http, f"/visualiser?match={match_id}")
        battle = vis.get("battle") or {}
        match = battle.get("match")
        if not match:  # the site sometimes serves a battle without its match; skip it rather than fail the refresh
            raise ValueError("no match details")
        base = {
            "battle": match.get("seriesId"),
            "ranked": match.get("ranked"),
            "requestedAt": match.get("requestedAt"),
            "autoscrim": autoscrim_draw(match.get("requestedAt")),
            "submissionAId": match.get("submissionAId"),
            "submissionBId": match.get("submissionBId"),
            "teamAId": match["teamAId"],
            "teamAName": battle["teamAName"],
            "teamBId": match["teamBId"],
            "teamBName": battle["teamBName"],
        }
        print(f"battle {base['battle']}: {base['teamAName']} (A) vs {base['teamBName']} (B)", file=sys.stderr)
        for game in vis["games"]:
            gid = game["id"]
            if only and gid not in only:
                continue
            # A draw's status is "draw" (online.FINISHED; MISSES 83).
            if not game.get("hasReplay") or game.get("status") not in ("completed", "draw"):
                print(f"  {gid}: skipped, status {game.get('status')}, hasReplay {game.get('hasReplay')}", file=sys.stderr)
                continue
            if self.cached(gid):
                print(f"  {gid}: cached", file=sys.stderr)
                continue
            if not self.budget_left():
                print(f"  stopping: --max-games {self.max_games} reached", file=sys.stderr)
                return
            site_map = battle.get("mapName") if gid == match["id"] else None
            self.game(gid, {**base, "winner": game.get("winner")}, site_map)

    # Downloads one replay, checks it parses, and appends its index row.
    def game(self, gid: int, row: dict[str, Any], site_map: str | None) -> None:
        raw = self.http.get(f"{SITE}/api/matches/{gid}/replay")
        path = self.out_dir / f"{gid}.replay"
        tmp = path.with_suffix(".part")
        tmp.write_bytes(raw)
        replay = load_replay(tmp)
        info = parse_map(replay.map)
        tmp.rename(path)
        replay_winner = str(replay.result.winner) if replay.result.which() == "winner" else None
        if row.get("winner") != replay_winner:
            print(f"  {gid}: WARNING site winner {row.get('winner')} != replay winner {replay_winner}", file=sys.stderr)
        if site_map and site_map.lower() != info.name.lower():
            print(f"  {gid}: WARNING site map {site_map!r} != replay map {info.name!r}", file=sys.stderr)
        full = {"match": gid, **row, "map": info.name,
                "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with (self.out_dir / "index.jsonl").open("a") as f:
            f.write(json.dumps(full, ensure_ascii=False) + "\n")
        self.index[gid] = full
        self.downloaded += 1
        print(f"  {gid}: {info.name}, winner {row.get('winner')}, {len(raw) // 1024} KB", file=sys.stderr)


def cmd_fetch(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(Http(), out_dir, args.max_games, read_index(out_dir / "index.jsonl"))
    if args.match:
        for mid in args.match:
            if fetcher.cached(mid):
                print(f"{mid}: cached", file=sys.stderr)
            elif fetcher.budget_left():
                fetcher.battle(mid, only={mid})
    elif args.battle:
        for mid in args.battle:
            if not fetcher.budget_left():
                break
            fetcher.battle(mid)
    elif args.top_battles:
        for mid in fetcher.battle_ids(page_data(fetcher.http, "/top-battles")["battles"]):
            if not fetcher.budget_left():
                break
            fetcher.battle(mid)
    else:
        recent = page_data(fetcher.http, f"/teams/{args.team}")["recent"]
        if args.ranked:
            recent = [b for b in recent if b.get("ranked")]
        for mid in fetcher.battle_ids(recent, args.battles):
            if not fetcher.budget_left():
                break
            fetcher.battle(mid)
    print(f"downloaded {fetcher.downloaded} replays with {fetcher.http.requests} requests", file=sys.stderr)
    if fetcher.battles and fetcher.skipped == fetcher.battles:
        # Nothing fetched is a failure, not an empty refresh: the study would otherwise rebuild from old replays.
        sys.exit(f"fetch failed: all {fetcher.battles} battles were skipped")


def battle_time(row: dict[str, Any]) -> float:
    return datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00")).timestamp()


# A team's battles of every kind (filter on `ranked`) whose `at` falls in [since, until], oldest first, from the site's
# /battles?teams=<id> list: `page(n)` returns its n-th page's data (`battles`, 10 a page, newest first, and `total`).
# The page holding `until` is found by bisection, then pages are read towards older battles until one passes
# `since`, at most `max_pages` reads in all, so a busy team's long list costs a bounded number of requests. Returns
# the rows and whether the reads reached `since` or the list's end. Battles arriving meanwhile shift the pages a
# little, so rows are kept once by id.
def battles_between(page: Any, since: float, until: float, max_pages: int) -> tuple[list[dict[str, Any]], bool]:
    read: dict[int, list[dict[str, Any]]] = {}

    def rows(n: int) -> list[dict[str, Any]]:
        if n not in read:
            read[n] = [b for b in page(n).get("battles") or [] if isinstance(b, dict) and b.get("at")]
        return read[n]

    first = page(1)
    read[1] = [b for b in first.get("battles") or [] if isinstance(b, dict) and b.get("at")]
    pages = max(1, -(-int(first.get("total") or 0) // max(1, int(first.get("perPage") or 10))))
    low, high = 1, pages
    while low < high and len(read) < max_pages:  # the first page whose oldest battle is at or before `until`
        middle = (low + high) // 2
        found = rows(middle)
        if not found or battle_time(found[-1]) <= until:
            high = middle
        else:
            low = middle + 1
    kept: dict[Any, dict[str, Any]] = {}
    for n in range(low, pages + 1):
        if n not in read and len(read) >= max_pages:
            return sorted(kept.values(), key=battle_time), False
        found = rows(n)
        kept.update((b.get("id"), b) for b in found if since <= battle_time(b) <= until)
        if not found or battle_time(found[-1]) < since:
            break
    return sorted(kept.values(), key=battle_time), True


def cmd_battles(args: argparse.Namespace) -> None:
    http = Http()
    since, until = (datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() for t in (args.since, args.until))
    found, complete = battles_between(lambda n: page_data(http, f"/battles?teams={args.team}&page={n}"), since,
                                      until, args.max_pages)
    print(json.dumps({"battles": found, "complete": complete, "requests": http.requests}, ensure_ascii=False))


# ---------------------------------------------------------------- commands


def cmd_summary(args: argparse.Namespace) -> None:
    path = Path(args.file)
    print(json.dumps(game_summary(analyse(load_replay(path)), meta_for(path)), indent=1, ensure_ascii=False))


# One JSON line per death, in engine order. Fields: round, id, team, reason, length (at death), head [x, y],
# born (round; -1 = starting dragon), parent, longestTeammate (its team's longest other living dragon then, 0 when
# none). Deaths during a move add step/of/direction (the step that failed). Head-ons: the dragon that was hit has
# hitBy/hitByTeam/hitByLength and comes first; the mover has movedInto/movedIntoTeam/movedIntoLength. noValidAction
# deaths add cause (timeout, noAction, illegalSplit, unpaidSprintStep).
def cmd_deaths(args: argparse.Namespace) -> None:
    game = analyse(load_replay(Path(args.file)))
    for record in game.deaths:
        if args.team is None or record["team"] == args.team.upper():
            print(json.dumps(record))
    _report_checks(game)


def cmd_lengths(args: argparse.Namespace) -> None:
    game = analyse(load_replay(Path(args.file)))
    print("round\tA_alive\tA_longest\tA_total\tB_alive\tB_longest\tB_total")
    last = game.snapshots[-1].round
    for s in game.snapshots:
        if s.round % args.every == 0 or s.round == last:
            print(f"{s.round}\t{s.alive['A']}\t{s.longest['A']}\t{s.total['A']}\t"
                  f"{s.alive['B']}\t{s.longest['B']}\t{s.total['B']}")
    _report_checks(game)


def _report_checks(game: Game) -> None:
    checks = game.checks.as_dict()
    status = "ok" if checks["ok"] else "FAILED"
    print(f"checks {status}: {checks['tailMatches']} head/tail, {checks['splitMatches']} splits, "
          f"{checks['deathPearlMatches']} death pearls, final standing {checks['finalStandingMatches']}",
          file=sys.stderr)
    for problem in checks["problems"]:
        print(f"  {problem}", file=sys.stderr)


PROFILE_COLUMNS = (
    "Team | Games | Win% | Wipeout% | Splits | Child/parent len | 1st split (rnd) | Peak alive | At cap % of rounds | "
    "Alive@100 | "
    "Alive@300 | Spread early/mid/late | Pearls eaten | H2H started | Deaths (wall/self/body/h2h/nva) | "
    "Sprint % of moves | Sonar/turn | Longest at r500"
)


# Aggregates per-team summaries across every indexed game into the markdown table from the game analysis.
def cmd_profile(args: argparse.Namespace) -> None:
    index_path = Path(args.index)
    rows: dict[Any, list[dict[str, Any]]] = {}
    names: dict[Any, str] = {}
    failed = 0
    for meta in read_index(index_path).values():
        path = index_path.parent / f"{meta['match']}.replay"
        if not path.exists():
            continue
        summary = game_summary(analyse(load_replay(path)), meta)
        if not summary["checks"]["ok"]:
            failed += 1
        for team in TEAMS:
            t = summary["teams"][team]
            key = t["id"]
            names[key] = t["name"]
            rows.setdefault(key, []).append({**t, "endReason": summary["result"]["endReason"]})
    print("| " + PROFILE_COLUMNS + " |")
    print("|" + " --- |" * len(PROFILE_COLUMNS.split(" | ")))
    for key in sorted(rows, key=lambda k: -len(rows[k])):
        if len(rows[key]) >= args.min_games:
            print(_profile_row(names[key], rows[key]))
    games = sum(len(v) for v in rows.values()) // 2
    print(f"\n{games} games, {len(rows)} teams, {failed} games failed checks", file=sys.stderr)


def _mean(values: list[float]) -> str:
    return f"{statistics.mean(values):.1f}" if values else "-"


def _profile_row(name: str, games: list[dict[str, Any]]) -> str:
    def per_game(get: Any) -> list[float]:
        return [get(g) for g in games]

    child = [c for g in games for c, n in g["splits"]["childLenCounts"].items() for _ in range(n)]
    parent = [p for g in games for p, n in g["splits"]["parentLenCounts"].items() for _ in range(n)]
    firsts = [g["splits"]["firstRound"] for g in games if g["splits"]["firstRound"] is not None]
    r500 = [g["end"]["longest"] for g in games if g["endReason"] == "roundLimit"]
    deaths = "/".join(_mean(per_game(lambda g, r=r: g["deaths"][r])) for r in DEATH_REASONS)
    median = lambda xs: f"{statistics.median(xs):.0f}" if xs else "-"  # noqa: E731
    cells = [
        name,
        str(len(games)),
        f"{100 * sum(g['won'] for g in games) / len(games):.0f}",
        f"{100 * sum(g['endReason'] == 'teamEliminated' for g in games) / len(games):.0f}",
        _mean(per_game(lambda g: g["splits"]["count"])),
        f"{median(child)}/{median(parent)}",
        median(firsts),
        _mean(per_game(lambda g: g["peakAlive"])),
        _mean([g["capRoundsPct"] for g in games if "capRoundsPct" in g]),
        _mean(per_game(lambda g: g["aliveAt100"])),
        _mean(per_game(lambda g: g["aliveAt300"])),
        "/".join(_mean([g["spread"][p]["mean"] for g in games if g["spread"][p]]) for p in ("early", "mid", "late")),
        _mean(per_game(lambda g: g["pearlsEaten"])),
        _mean(per_game(lambda g: g["headOnsStarted"])),
        f"{_mean(per_game(lambda g: g['deaths']['total']))} ({deaths})",
        _mean(per_game(lambda g: g["sprintPct"])),
        f"{statistics.mean(per_game(lambda g: g['sonarPerTurn'])):.2f}",
        _mean(r500),
    ]
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


# The loop's replay library (loop/replay_library.py): every saved online game of ours and of the ladder leaders that
# a rate or read may count (result-blind, no dev games, no losses kept because we lost), which a coder's sandbox reads
# in place.
def library_path() -> Path:
    if (loop := str(REPO / "loop")) not in sys.path:
        sys.path.append(loop)
    from home import loop_home
    return loop_home() / "online-replays/library.json"


# Whether a library row's replay is there to read; a row without one, or whose file can't be checked, is skipped
# rather than ending the read.
def _readable(game: Any) -> bool:
    try:
        return isinstance(game, dict) and os.access(game["replay"], os.R_OK) and Path(game["replay"]).is_file()
    except (KeyError, TypeError, OSError):
        return False


def library_games(part: str | None = None, path: Path | None = None) -> list[dict]:
    """The library's counting games, "online" (ours; `side` ours) or "leaders" (one row per leader side; `side` the
    leader's), both when `part` is None, each marked with its "part"; games whose replay is gone or unreadable (pruned
    since the last refresh) are left out. A rate over "online" is our rate over result-blind non-dev games; its
    `decoy_risk` says the opponent's bot may have been a decoy (unranked, never seen ranked), so leave those out of a
    read of how opponents play. The leaders' possible decoys are never returned: a decoy fakes its own team's play.
    Raises OSError when there is no library."""
    library = json.loads((path or library_path()).read_text())
    return [{**game, "part": name} for name in ("online", "leaders") if part in (None, name)
            for game in library[name]["games"] if _readable(game)]


def cmd_library(args: argparse.Namespace) -> None:
    for game in library_games(args.part):
        if args.map is None or game.get("map") == args.map:
            print(json.dumps(game, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="download public replays into data/replays/")
    pick = fetch.add_mutually_exclusive_group(required=True)
    pick.add_argument("--match", type=int, nargs="+", help="these match (game) ids only")
    pick.add_argument("--top-battles", action="store_true", help="every game of each battle on /top-battles")
    pick.add_argument("--team", type=int, help="every game of this team's recent battles")
    pick.add_argument("--battle", type=int, nargs="+", help="every game of the battles these match ids belong to")
    fetch.add_argument(
        "--battles", type=int, default=3,
        help="with --team: how many recent battles with a finished game (unplayed ones are passed over)",
    )
    fetch.add_argument("--ranked", action="store_true", help="with --team: ranked battles only")
    fetch.add_argument("--max-games", type=int, help="stop after downloading this many replays")
    fetch.add_argument("--out", default=str(REPLAY_DIR))
    fetch.set_defaults(func=cmd_fetch)

    listing = sub.add_parser("battles", help="a team's battles between two times from the site's battle list, as JSON")
    listing.add_argument("--team", type=int, required=True)
    listing.add_argument("--since", required=True, help="ISO time, e.g. 2026-09-27T00:00Z")
    listing.add_argument("--until", required=True, help="ISO time")
    listing.add_argument("--max-pages", type=int, default=30, help="site pages read at most (10 battles each)")
    listing.set_defaults(func=cmd_battles)

    summary = sub.add_parser("summary", help="per-team facts for one replay, as JSON")
    summary.add_argument("file")
    summary.set_defaults(func=cmd_summary)

    deaths = sub.add_parser("deaths", help="one JSON line per death")
    deaths.add_argument("file")
    deaths.add_argument("--team", choices=["A", "B", "a", "b"])
    deaths.set_defaults(func=cmd_deaths)

    lengths = sub.add_parser("lengths", help="per-round alive/longest/total per team (tab-separated)")
    lengths.add_argument("file")
    lengths.add_argument("--every", type=int, default=1, help="print every Nth round (the last is always printed)")
    lengths.set_defaults(func=cmd_lengths)

    profile = sub.add_parser("profile", help="markdown table of per-team stats over all indexed replays")
    profile.add_argument("--index", default=str(INDEX))
    profile.add_argument("--min-games", type=int, default=1)
    profile.set_defaults(func=cmd_profile)

    library = sub.add_parser("library", help="the loop's replay library's counting games, one JSON line each")
    library.add_argument("--part", choices=("online", "leaders"), help="ours or the leaders' only")
    library.add_argument("--map", help="one map's games only (its name)")
    library.set_defaults(func=cmd_library)

    args = parser.parse_args()
    if args.command == "fetch" and args.team is None and args.battles != 3:
        parser.error("--battles needs --team")
    args.func(args)


if __name__ == "__main__":
    main()
