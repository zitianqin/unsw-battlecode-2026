"""Watch a replay as text, the way a person scrubs the visualiser: find moments worth watching, follow one
dragon round by round, and draw the board around it.

    uv run python tools/watch.py scan REPLAY... [--team B] [--tally]
    uv run python tools/watch.py dragon REPLAY --id 13 [--rounds 80-123]
    uv run python tools/watch.py frames REPLAY --rounds 90-93 (--around 13 | --at 26,8) [--radius 5]
    uv run python tools/watch.py doom REPLAY... [--team A] [--summary | --json]
    uv run python tools/watch.py why REPLAY --rounds 150-174 [--dragon 13] [--commit SHA] [--side A] [--plain]

`why` plays a recorded game again with our exact submission and its debug line on, against a stand-in sending the
opponent's recorded replies, and prints the decision behind each of our moves: see tools/why.py.
`scan` lists moments by kind (circling in place, a long stretch without eating while pearls were near, deaths
by cause and matchup, a split child dying young, a lead in total length lost) for one team or both, with deaths the
bot's debug log line shows were on purpose (a feed, a split-out's parent) apart as deliberate; `--tally`
counts them per game across many replays, to check whether something seen once is common. A local SPRT
replay's name ends in -aA or -aB: the candidate played that side. Frames: `A`/`B` heads (`@` the followed
dragon), `a`/`b` bodies, `*` pearl, `+` pearl tile (spawns pearls), `.` floor, `?` a tile whose pearl timings the
replay withholds; `|` and `-` are kelp, `:` and `~` portals. The site's replays from 2026-09-27 ~21:10 AEST give every
TILE line gaps 0 0, so pearl tiles are the ones with pearlCountdown events; `?` only without those too.
A round's board is drawn after that round's pearl spawns, as the bots see it when they move (so are `dragon`'s and
`scan`'s pearls in view). The board wraps at its edges. Vision is the 7x7 square around a head (Chebyshev distance 3).

`doom` says how each death came about (MISSES 64: an end reason says only the last step). At each of a dragon's turn
starts it asks whether the head can still get out (`reach`: a timed flood fill as bot/safety.hpp's, then an exact
search of its own moves and sprints); `since` is the first turn from which it never could again, `trigger` what
changed then (Doom, TRIGGERS, tried in that order: its own move, split, ate, a portal; a teammate's body before an
enemy's; born doomed; `never` for a death with room, with its end reason), `room` the tiles it could still reach and
`fed` whether that move ate; for a teammate trap `shutter` names the teammate whose move alone shut it in and
whether that move, or its turn before, was a split (M93); `place` is `pocket` for a room only portals lead out of
(`pockets`), with its id and exits, else `open`. It is judged when the dragon's turn starts, after lower ids moved
this round, while `frames` draws the round's start: draw `since - 1`, `since` and the death round, and read the legend's actions. `--summary`
is a per-map table of our deaths against the opponent's, per game, dev, ladder and local games apart (doom_table,
which loop/rulecounts.py and loop/online.py print too; DOOM_TABLE_NOTE), leaving out replays kept because the game
was lost; a feed or split-out's parent is its own kind (deliberate_deaths, split_out_parent; by its moves a feed
is `feed?`, looks_fed). Our side comes from
the replay's index.json or -aA/-aB name, else `--team`.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replays  # noqa: E402
from mapgen.gen import KELP, parse  # noqa: E402

Point = tuple[int, int]
VISION = 3
IDLE_MIN = 12  # a circling run worth reporting, in moves
HUNGRY_MIN = 40  # rounds without eating, with a pearl in view at some point, worth reporting


@dataclass
class Frame:
    """The board at the start of a round: every living dragon (team, body head first) and every pearl. A replay
    starts each round with that round's pearl spawns, before any dragon moves, so `pearls` is the board after them,
    as the bots see it; `start_pearls` is before them (blunders.py replays the spawns itself)."""
    dragons: dict[int, tuple[str, tuple[Point, ...]]]
    pearls: frozenset[Point]
    start_pearls: frozenset[Point]


@dataclass
class Turn:
    action: str  # e.g. "N", "NNE" (a sprint), "split 3", "suicide", "timeout", "none"
    ate: int = 0
    team: str = ""  # the acting dragon's, set with its action


@dataclass
class Watched:
    game: replays.Game
    board: Any  # mapgen Board: kelp and portal edges, pearl gaps; `unknown`, tiles still unknown after the countdowns
    spawners: set[Point]  # the tiles that spawn pearls, from the map text's gaps and the pearlCountdown events
    frames: dict[int, Frame] = field(default_factory=dict)
    turns: dict[tuple[int, int], Turn] = field(default_factory=dict)  # (round, dragon id)
    countdowns: dict[Point, tuple[int, int]] = field(default_factory=dict)  # tile -> (round seen, countdown)
    logs: list[tuple[int, int, str, str]] = field(default_factory=list)  # (round, dragon id, team, text) per LOG line
    # For each death on the dying dragon's own move (a death record with a `step`), by its index in game.deaths: every
    # living dragon just before it, the dying one included, as {id: (team, body head first)}. What a hitOtherBody hit
    # and where the team's longest was then come from these (loop/rulecounts.py's portal hits and fold-window crashes).
    before_death: dict[int, dict[int, tuple[str, tuple[Point, ...]]]] = field(default_factory=dict)
    dooms: list[Doom] = field(default_factory=list)  # one per game.deaths entry, in order, with watch(doom=True)
    eats: list[tuple[int, int, Point]] = field(default_factory=list)  # (round, eater id, tile) per pearl eaten


# The replay walker, also recording each round's board and each dragon's action, for these views.
class _Recorder(replays._Walker):
    def __init__(self, info: replays.MapInfo) -> None:
        super().__init__(info)
        self.pearls: set[Point] = set()
        self.frames: dict[int, Frame] = {}
        self.turns: dict[tuple[int, int], Turn] = {}
        self.countdowns: dict[Point, tuple[int, int]] = {}
        self.logs: list[tuple[int, int, str, str]] = []
        self.before_death: dict[int, dict[int, tuple[str, tuple[Point, ...]]]] = {}
        self.eats: list[tuple[int, int, Point]] = []

    def on_event(self, event: Any) -> None:
        kind = event.which()
        if kind == "tileChange":
            tile = (event.tileChange.tile.x, event.tileChange.tile.y)
            if not event.tileChange.hasPearl and self.mover is not None:
                self.turns.setdefault((self.round, self.mover.id), Turn("none")).ate += 1
                self.eats.append((self.round, self.mover.id, tile))
            (self.pearls.add if event.tileChange.hasPearl else self.pearls.discard)(tile)
        elif kind == "pearlCountdown":
            c = event.pearlCountdown
            self.countdowns[(c.tile.x, c.tile.y)] = (self.round, c.countdown)
        elif kind == "dragonLog":
            dragon = self.dragons.get(event.dragonLog.id)
            self.logs.append((self.round, event.dragonLog.id, dragon.team if dragon else "?", event.dragonLog.text))
        elif kind == "dragonDeath" and self.mover is not None and self.mover.id == event.dragonDeath.id and self.steps:
            self.before_death[len(self.deaths)] = {d.id: (d.team, tuple(d.body)) for d in self.dragons.values()
                                                   if d.alive}
        super().on_event(event)
        if kind == "roundStart":
            pearls = frozenset(self.pearls)
            self.frames[self.round] = Frame(
                {d.id: (d.team, tuple(d.body)) for d in self.dragons.values() if d.alive}, pearls, pearls)
        elif kind == "tileChange" and self.mover is None and self.round in self.frames:
            self.frames[self.round].pearls = frozenset(self.pearls)  # a spawn, before this round's first turn
        elif kind == "dragonAction":
            action = self.action
            if action == "move":
                action = "".join(s[0].upper() for s in self.steps)
            elif action == "split" and event.dragonAction._has("action"):
                action = f"split {event.dragonAction.action.split}"
            turn = self.turns.setdefault((self.round, event.dragonAction.id), Turn(action))
            turn.action = action
            if dragon := self.dragons.get(event.dragonAction.id):
                turn.team = dragon.team


# ---------------------------------------------------------------- doom: when each death became unavoidable

POCKET_MAX = 40  # a region only portals lead out of, with at most this many tiles, is a pocket (Queen Of Spades': 16)
ROOM_CAP = 128  # a flood fill reaching this many tiles is open space
DIRECTIONS = ("N", "E", "S", "W")
SPRINT_MIN = 3  # a sprint's second or later step needs the dragon this long before it (length 2 cannot pay)
# What made a dragon doomed, in the order `trigger` tries them (the dragon's own move comes first in time):
TRIGGERS = ("split", "ate", "portal", "deeper", "teammate", "enemy", "bodies", "born", "start", "never")


@dataclass
class Doom:
    """How one death came about. `doomed_since` is the first of the dragon's turns from which every turn start to its
    death was doomed (`reach`: its head could no longer reach a tile that frees in time, nor open space), or None if
    it never was. `trigger` (TRIGGERS) is what changed between its previous turn start and that one: its own move
    ("split"; "ate": it grew and would have had room at its old length; "portal": it crossed one; "deeper": any other
    move), else the bodies that moved in after it ("teammate", "enemy", "bodies" when only both together explain it),
    or "born" (a split child doomed at its first turn), "start", or "never". `room` counts the tiles its head could
    still reach then, and `fed` whether its move just before doomed_since ate a pearl. `place` is "pocket" when the
    dying head was in a pocket (`pockets`), with the pocket's id (its first tile, as x,y) and its portal exits, else
    "open"; `split_out` (split_out_parent) whether its own split just before left it to die, `beside_mate`
    (looks_fed) whether its fatal move looks like a feed, and `at_cap` (at_cap) whether its team was at the ceiling.
    For a "teammate" trigger (M93: 39% of 289 teammate-trap dooms had a teammate split beside them, 24% at random),
    `shutter` is the teammate whose move alone shut it in (its body put back as it was, with any child it split off
    since removed, lets it out; the lowest such id), None when no one alone did, and `shut_split` whether that move
    was a split ("that turn"), the shutter's turn before it was ("turn before"), or neither (None)."""
    round: int
    id: int
    team: str
    length: int
    reason: str
    place: str
    pocket: str | None
    exits: tuple[Point, ...]
    doomed_since: int | None
    trigger: str
    room: int | None
    fed: bool = False
    split_out: bool = False  # split_out_parent: left SPLIT_OUT_PARENT long by its own split just before it died
    beside_mate: bool = False  # looks_fed, set by watch()
    at_cap: bool = False  # at_cap, set by watch()
    shutter: int | None = None  # a teammate-trap doom's shutter (see above)
    shut_split: str | None = None  # "that turn" or "turn before": when the shutter split

    @property
    def lead(self) -> int | None:
        return None if self.doomed_since is None else self.round - self.doomed_since


# Where each step leads: {tile: [(destination, through a portal) or None for kelp, in DIRECTIONS order]}.
def step_table(board: Any) -> dict[Point, list[tuple[Point, bool] | None]]:
    table = {}
    for t in board.all_tiles():
        row = []
        for d in DIRECTIONS:
            to = board.step(t, d)
            kind = board.edges.get(board.side(t, d))
            row.append(None if to is None else (to, kind is not None))
        table[t] = row
    return table


# The map's pockets: every region (tiles joined by open edges, portals cut) of at most POCKET_MAX tiles that is not
# the largest and has a portal out, as {tile: (pocket id, its portal exits)}; the id is the region's first tile, as
# "x,y". A room behind a narrow open door is not a pocket here (only portals count as its way out); `reach` still
# sees a dragon sealed in one.
def pockets(board: Any,
            table: dict[Point, list[tuple[Point, bool] | None]]) -> dict[Point, tuple[str, tuple[Point, ...]]]:
    regions: list[list[Point]] = []
    seen: set[Point] = set()
    for t in board.all_tiles():
        if t in seen:
            continue
        region, todo = [t], [t]
        seen.add(t)
        while todo:
            for step in table[todo.pop()]:
                if step and not step[1] and step[0] not in seen:
                    seen.add(step[0])
                    region.append(step[0])
                    todo.append(step[0])
        regions.append(region)
    largest = max(len(r) for r in regions)
    found = {}
    for region in regions:
        if len(region) > POCKET_MAX or len(region) == largest:
            continue
        first = min(region, key=lambda p: (p[1], p[0]))
        exits = tuple(sorted(p for p in region if any(s and s[1] for s in table[p])))
        if not exits:  # sealed: no dragon gets in
            continue
        for p in region:
            found[p] = (f"{first[0]},{first[1]}", exits)
    return found


# Whether dragon `me` can still get out, as (room, open). First a timed flood fill from its head, as bot/safety.hpp
# floodFill counts room: a tile is entered at the step its BFS depth gives, and only once free by then. A body segment
# k from its dragon's tail is gone after k + 1 of that dragon's moves (the engine checks a step against the body
# before the tail advances), so enterable from step k + 2, since every other dragon moves once between two steps of
# ours, whichever id moves first; each pearl on our path holds our own tail back one more step. It is open when it
# reaches ROOM_CAP tiles or a tile that frees in time (from there it follows a tail, safety.hpp reachedFreed); room
# is the tiles it counted. A closed fill is checked turn by turn, sprints included (`escapes`), since a detour can
# reach a tile after it frees where the fill's shortest path came too soon, and a sprint sheds our own tail. Heads
# count like any segment. The others are modelled as moving on one step a turn without eating, so a call can come
# late (one eats and holds its tail) or early: another dragon's death, split or sprint frees its tiles sooner than
# modelled. The review of 2026-09-28 counted 5,256 steps into tiles still modelled occupied over 15 games (4,613 after
# a death, 607 after a split, 36 after a sprint); judging afresh each turn start catches most of them (a later turn
# with a way out ends the doomed streak and `since` restarts), so an early call stands only where the way out they
# opened was gone again by the dragon's next turn start.
def reach(table: dict[Point, list[tuple[Point, bool] | None]], bodies: dict[int, tuple[Point, ...]], me: int,
          pearls: frozenset[Point] | set[Point], cap: int = ROOM_CAP) -> tuple[int, bool]:
    free_at = {p: len(body) + 1 - i for i_, body in bodies.items() if i_ != me for i, p in enumerate(body)}
    mine = bodies[me]
    own = {p: len(mine) + 1 - i for i, p in enumerate(mine)}
    head = mine[0]
    depth, delay = {head: 0}, {head: 0}
    todo = deque([head])
    count = 0
    while todo:
        cur = todo.popleft()
        nxt = depth[cur] + 1
        for step in table[cur]:
            if step is None:
                continue
            to = step[0]
            if to in depth and to != head:  # the head's own tile can be entered again once it frees
                continue
            wait = own[to] + delay[cur] if to in own else free_at.get(to, 0)
            if wait > nxt:
                continue
            if wait:  # an occupied tile that has freed by the time we arrive
                return count + 1, True
            depth[to], delay[to] = nxt, delay[cur] + (to in pearls)
            count += 1
            if count >= cap:
                return count, True
            todo.append(to)
    return count, escapes(table, free_at, mine, pearls, cap)


# Whether some sequence of turns gets `body` (head first) out: onto a tile that was occupied (another body at the
# start, or its own body or path) and has freed by then, or `cap` turns on. A turn is one step or a sprint
# (docs/unsw/movement.md, execution-order.md): each step is checked against the body as the step before left it, and
# step j >= 2 needs the dragon at least SPRINT_MIN long before it and removes one more tail segment, so a pearl eaten
# on the way pays for one later step. Other dragons don't move within a turn. `free_at` is the turn from which each
# other dragon's tile is free (reach). Tries every walk, up to `budget` steps tried, and says True when that runs out
# (not shown doomed).
def escapes(table: dict[Point, list[tuple[Point, bool] | None]], free_at: dict[Point, int], body: tuple[Point, ...],
            pearls: frozenset[Point] | set[Point], cap: int = ROOM_CAP, budget: int = 20000) -> bool:
    was = set(free_at) | set(body)  # grows by the walk's own path while it is being tried
    left = [budget]

    # The steps from `snake` in turn t + 1, `j` the step's number within that turn; after each one the turn may end
    # there or, paid for, go on.
    def go(snake: tuple[Point, ...], t: int, eaten: frozenset[Point], j: int = 1) -> bool:
        if j == 1 and t >= cap:
            return True
        if j > 1 and len(snake) < SPRINT_MIN:
            return False
        for step in table[snake[0]]:
            if step is None:
                continue
            to = step[0]
            if to in snake or free_at.get(to, 0) > t + 1:
                continue
            if to in was:
                return True
            left[0] -= 1
            if left[0] <= 0:
                return True
            eats = to in pearls and to not in eaten
            after = (to,) + (snake if eats else snake[:-1])
            if j > 1:
                after = after[:-1]
            now_eaten = eaten | {to} if eats else eaten
            was.add(to)
            if go(after, t + 1, now_eaten) or go(after, t, now_eaten, j + 1):
                return True
            was.discard(to)
        return False

    return go(body, 0, frozenset())


# One turn start's facts about a dragon, kept until its next: every living body then, and the pearls.
@dataclass
class _Seen:
    round: int
    bodies: dict[int, tuple[Point, ...]]
    teams: dict[int, str]


# The replay walker, also judging at each turn start whether the dragon about to move is doomed (reach), and when it
# first became so, why (Doom.trigger). `dooms` gives one Doom per death once the walk is done.
class _DoomRecorder(_Recorder):
    def __init__(self, info: replays.MapInfo, board: Any, side: str | None = None) -> None:
        super().__init__(info)
        self.side = side  # judge only this team's dragons (watch's `side`), or every dragon
        self.table = step_table(board)
        self.pocket_of = pockets(board, self.table)
        self.last: dict[int, _Seen] = {}  # each dragon's previous turn start
        self.crossed: set[int] = set()  # dragons whose move since their last turn start crossed a portal
        # dragon id -> (since, trigger, room, fed, shutter, shut_split) while doomed
        self.streak: dict[int, tuple[int, str, int, bool, int | None, str | None]] = {}
        self.at_death: dict[int, tuple[int, str, int, bool, int | None, str | None] | None] = {}  # death index -> streak

    def on_event(self, event: Any) -> None:
        kind = event.which()
        if kind == "dragonUpdate" and self.mover is not None and self.step_index < len(self.steps):
            head, d = self.mover.body[0], self.steps[self.step_index][0].upper()
            step = self.table[head][DIRECTIONS.index(d)]
            if step and step[1]:
                self.crossed.add(self.mover.id)
        super().on_event(event)
        if kind == "turnStart" and self.mover is not None and self.side in (None, self.mover.team):
            self._judge(self.mover.id)
        elif kind == "dragonDeath":
            self.at_death[len(self.deaths) - 1] = self.streak.get(event.dragonDeath.id)

    def _judge(self, me: int) -> None:
        now = _Seen(self.round, {d.id: tuple(d.body) for d in self.dragons.values() if d.alive},
                    {d.id: d.team for d in self.dragons.values() if d.alive})
        pearls = frozenset(self.pearls)
        room, out = reach(self.table, now.bodies, me, pearls)
        before = self.last.get(me)
        if out:
            self.streak.pop(me, None)
        elif me not in self.streak:
            fed = before is not None and self.turns.get((before.round, me), Turn("none")).ate > 0
            trigger = self._trigger(me, before, now, pearls)
            shutter, split = self._shutter(me, before, now, pearls) if trigger == "teammate" else (None, None)
            self.streak[me] = (self.round, trigger, room, fed, shutter, split)
        self.last[me] = now
        self.crossed.discard(me)

    # Why `me` became doomed between turn starts `before` and `now`: first whether its own move alone did it (the
    # others as they were before), then which of the others' moves undoes it when put back.
    def _trigger(self, me: int, before: _Seen | None, now: _Seen, pearls: frozenset[Point]) -> str:
        if before is None:
            return "born" if self.dragons[me].born >= 0 else "start"
        action = self.turns.get((before.round, me), Turn("none")).action
        mine = now.bodies[me]

        def stuck(bodies: dict[int, tuple[Point, ...]]) -> bool:
            return not reach(self.table, bodies, me, pearls)[1]

        others_before = {i: b for i, b in before.bodies.items() if i != me}
        # A split's child is part of the split: it is judged with its parent's own move, before the others'.
        children = {i: b for i, b in now.bodies.items() if i not in before.bodies and self.dragons[i].parent == me}
        if stuck({**others_before, **children, me: mine}):
            if action.startswith("split"):
                return "split"
            grown = len(mine) - len(before.bodies[me])
            if grown > 0 and not stuck({**others_before, me: mine[:len(mine) - grown]}):
                return "ate"
            return "portal" if me in self.crossed else "deeper"
        team = now.teams[me]

        # The others as they are now, but one side's (teammates' with True) as they were; its own children stay.
        def put_back(side: bool) -> dict[int, tuple[Point, ...]]:
            keep = {i: b for i, b in now.bodies.items()
                    if i != me and (i in children or (now.teams[i] == team) != side)}
            back = {i: b for i, b in others_before.items() if (before.teams[i] == team) == side}
            return {**keep, **back, me: mine}

        if not stuck(put_back(True)):
            return "teammate"
        if not stuck(put_back(False)):
            return "enemy"
        return "bodies"

    # A teammate-trap doom's shutter (Doom.shutter) and when it split (Doom.shut_split): each teammate that moved since
    # `before`, by id, is put back alone as it was then, with the children it split off since removed.
    def _shutter(self, me: int, before: _Seen, now: _Seen, pearls: frozenset[Point]) -> tuple[int | None, str | None]:
        team = now.teams[me]
        born = {i for i in now.bodies if i not in before.bodies}
        for mate in sorted(i for i, b in before.bodies.items() if i != me and before.teams[i] == team):
            children = {i for i in born if self.dragons[i].parent == mate}
            if mate not in now.bodies or (now.bodies[mate] == before.bodies[mate] and not children):
                continue
            bodies = {i: body for i, body in now.bodies.items() if i != mate and i not in children}
            if reach(self.table, {**bodies, mate: before.bodies[mate]}, me, pearls)[1]:
                shut = now.round if (now.round, mate) in self.turns else now.round - 1
                split = [self.turns.get((r, mate), Turn("none")).action.startswith("split") for r in (shut, shut - 1)]
                return mate, "that turn" if split[0] else "turn before" if split[1] else None
        return None, None

    def dooms(self, game: replays.Game) -> list[Doom]:
        found = []
        for index, death in enumerate(game.deaths):
            head = tuple(death["head"])
            pocket, exits = self.pocket_of.get(head, (None, ()))
            streak = self.at_death.get(index)
            since, trigger, room, fed, shutter, split = streak if streak else (None, "never", None, False, None, None)
            found.append(Doom(death["round"], death["id"], death["team"], death["length"], death["reason"],
                              "pocket" if pocket else "open", pocket, exits, since, trigger, room, fed,
                              split_out_parent(death, self.turns), shutter=shutter, shut_split=split))
        return found



# The side we played in the replay at `path`: from its folder's index.json (the loop's online-replays/<version>/),
# else a local SPRT replay's name (-aA or -aB), else None.
def our_side(path: Path) -> str | None:
    side = _index_games(path.resolve().parent).get(path.name, {}).get("side")
    return side or (path.stem[-1] if path.stem[-3:-1] == "-a" and path.stem[-1] in replays.TEAMS else None)


# Which games the replay at `path` is among, for keeping dev games apart (CLAUDE.md, MISSES 55): "dev" or "ladder"
# (ladder and autoscrim) by its folder's index.json `dev` flag, "local" for a replay with no index entry.
def game_kind(path: Path) -> str:
    game = _index_games(path.resolve().parent).get(path.name)
    return "local" if game is None else "dev" if game.get("dev") else "ladder"


# {replay file name: its index.json game} from a folder's index.json, read once a folder.
@functools.cache
def _index_games(folder: Path) -> dict[str, dict]:
    try:
        return {Path(g["replay"]).name: g for g in json.loads((folder / "index.json").read_text())["games"]
                if isinstance(g, dict) and g.get("replay")}
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return {}


# The round from which a folding dragon feeds (feed.hpp: P.foldRound) when a caller gives none: rule counts pass the
# champion's from its params.hpp, loop/online.py the live build's (`doom --feed-from`).
FEED_FROM = 397
FEED_REACH = 4  # P.foldMergeRadius: a folding dragon feeds once the king's head is within this many steps


# Whether a death looks like a feed from the moves alone (M63), so both sides of any replay are labelled alike: the
# dragon died on its own move into its own body or kelp (feed.hpp's two ways to die), and just before the move
# (`before`, Watched.before_death's entry: {id: (team, body head first)}) a teammate at least as long had its head
# within FEED_REACH steps (`distance`: wrapped Manhattan) and no enemy head was beside it; death_kind counts it from
# FEED_FROM on. On the 9 replays review 1790572014577194000 kept it found 283 of our 287 debug-line feeds and 33 other
# deaths (15 capfeeds, feeds too since deliberate_deaths reads `rule capfeed`, and 18 crashes); a teammate within 2
# steps found only 134.
def looks_fed(death: dict, before: dict[int, tuple[str, tuple[Point, ...]]] | None, distance) -> bool:
    if death["reason"] not in ("hitSelf", "hitWall") or not before:
        return False
    head, team = tuple(death["head"]), death["team"]
    near = [(t, len(body), distance(head, body[0])) for i, (t, body) in before.items() if i != death["id"]]
    return (any(t == team and n >= death["length"] and steps <= FEED_REACH for t, n, steps in near)
            and not any(t != team and steps <= 1 for t, _, steps in near))


# Whether the dying dragon's team was at the ceiling just before its fatal move (`before`, as looks_fed's): at least
# replays.UNIT_LIMIT - replays.CAP_MARGIN of its dragons alive, the dying one included, where the champion's cap feed
# (capfeed.hpp, from unitLimit - rescueReserve) fires. False for a death on no move of its own.
def at_cap(death: dict, before: dict[int, tuple[str, tuple[Point, ...]]] | None) -> bool:
    return bool(before) and (sum(team == death["team"] for team, _ in before.values())
                             >= replays.UNIT_LIMIT - replays.CAP_MARGIN)


# A death's kind: a deliberate one's why (`why`, from deliberate_deaths: "feed", or "split-out" for a split-out's
# parent, also when the moves alone show one, Doom.split_out); "feed?" when its moves look like a feed (looks_fed)
# from round `feed_from` on; "capfeed?" before that when they do and its team was at the ceiling (at_cap) with room
# to spare (never doomed), as the champion's cap feed dies (M63: without it capfeeds, most before foldRound, read as
# `open never hitSelf`); else "<place> <trigger>" (Doom), and for one never doomed its end reason too ("open never
# hitWall" is a step into kelp with room to spare, "open never hitHeadToHead" a fight). On 8 logging-copy games
# against the champion (review 1790592506760224000, 7 at the cap) capfeed? took 179 of our 186 debug-line capfeeds
# before foldRound and no other death of ours, where looks_fed and at_cap without the never-doomed condition also took
# 659 crashes of doomed dragons beside a longer teammate; the champion copy it played had 112.
def death_kind(d: Doom, why: str | None = None, feed_from: int = FEED_FROM) -> str:
    if why:
        return "split-out" if why == "split-out parent" else why
    if d.split_out:
        return "split-out"
    if d.beside_mate and d.round >= feed_from:
        return "feed?"
    if d.beside_mate and d.at_cap and d.trigger == "never":
        return "capfeed?"
    return f"{d.place} {d.trigger}" + (f" {d.reason}" if d.trigger == "never" else "")


# One game's deaths by kind (death_kind), {"ours": {kind: n}, "theirs": {kind: n}}: what loop/rulecounts.py and
# loop/online.py keep a game, and doom_table adds up. `deliberate` is deliberate_deaths' {(round, id): why}, from the
# debug lines; without them (the ladder, and both sides alike in rule counts) split-outs and feeds (`feed?`, from
# `feed_from` on) are told by their moves.
def death_kinds(dooms: list[Doom], side: str, deliberate: dict[tuple[int, int], str] | None = None,
                feed_from: int = FEED_FROM) -> dict[str, dict[str, int]]:
    kinds: dict[str, Counter[str]] = {"ours": Counter(), "theirs": Counter()}
    for d in dooms:
        why = (deliberate or {}).get((d.round, d.id))
        kinds["ours" if d.team == side else "theirs"][death_kind(d, why, feed_from)] += 1
    return {who: dict(c) for who, c in kinds.items()}


# Bumped whenever death_kinds would give a saved game other kinds, so loop/online.py refresh_dooms reads it again.
DOOM_VERSION = 4


# What doom_table's columns mean, for every reader that prints it (rulecounts.doom_section, online.doom_text and
# `doom --summary`), each adding what its games can and can't tell apart.
DOOM_TABLE_NOTE = (
    "Each death's kind is a deliberate one's why, else its place (pocket: a room only portals lead out of; open) and "
    "trigger: what shut the dragon in at the first turn from which it could no longer get out, trying its own move "
    "first (split, ate, portal, deeper), then the bodies that moved in, a teammate's before an enemy's (bodies: only "
    "both together); born doomed; never, with the end reason. A split-out is a parent its own split left 2 long to "
    "die next turn (tools/watch.py split_out_parent); `feed?` one from foldRound on whose moves look like a feed "
    "(looks_fed), which takes in crashes beside a longer teammate too: by our debug lines 18 of 316 such labels on 9 "
    "replays (review 1790572014577194000) and 52 of 419 on 10 (1790576608098366000) were no feed; `capfeed?` one "
    "before foldRound that looks like a feed with its team at the unit ceiling and room to spare (never doomed), as "
    "a cap feed dies. Per map: deaths a game each side, with a two-sided sign test "
    "over games of who died more; then per kind its deaths a game (/g) and its share of that side's deaths (%), and "
    "`p`, a two-sided sign test over the games where both sides died of whose share of deaths was higher, so how "
    "often a side dies is apart from what kills it. Each map says how many rows it tests. A count is not a diagnosis: "
    "watch 3 cases with `watch.py frames` at since-1, since and the death round. A report, never a gate.")


# A per-map table of deaths by kind (death_kinds), ours against the opponent's, from `games`: [(map name,
# death_kinds)] (DOOM_TABLE_NOTE says what it shows). Games are the sign tests' unit (loop/chance.py sign_p, made
# two-sided), since a kind's deaths within one game are not independent. `top` keeps each map's most common kinds,
# the rest added up in one line.
def doom_table(games: list[tuple[str, dict[str, dict[str, int]]]], top: int | None = None) -> list[str]:
    sys.path.append(str(Path(__file__).resolve().parent.parent / "loop"))
    from chance import sign_p

    def two_sided(a: list[float], b: list[float]) -> tuple[int, int, float]:
        more, fewer = sum(x > y for x, y in zip(a, b)), sum(x < y for x, y in zip(a, b))
        return more, fewer, min(1.0, 2 * sign_p(max(more, fewer), min(more, fewer)))

    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.0f}%" if whole else "-"

    lines = []
    for name in sorted({m for m, _ in games}):
        rows = [k for m, k in games if m == name]
        n = len(rows)
        kinds = sorted({kind for k in rows for who in k.values() for kind in who},
                       key=lambda kind: -sum(k[who].get(kind, 0) for k in rows for who in k))
        shown, rest = (kinds[:top], kinds[top:]) if top is not None else (kinds, [])
        ours_n = [sum(k["ours"].values()) for k in rows]
        theirs_n = [sum(k["theirs"].values()) for k in rows]
        ours_all, theirs_all = sum(ours_n), sum(theirs_n)
        more, fewer, p = two_sided(ours_n, theirs_n)
        both = [(k, a, b) for k, a, b in zip(rows, ours_n, theirs_n) if a and b]
        lines.append(f"{name}: {n} games, deaths a game ours {ours_all / n:.1f}, theirs {theirs_all / n:.1f} (more "
                     f"in {more}-{fewer} games, p {p:.2g}); {len(shown)} kinds tested over the {len(both)} games both "
                     f"sides died, so about {0.05 * len(shown):.1f} rows get p < 0.05 by luck alone")
        lines.append(f"  {'kind':34s} {'ours/g':>7s} {'theirs/g':>8s} {'ours%':>6s} {'theirs%':>7s}  "
                     f"share higher (ours-theirs)  p")
        for kind in shown:
            ours = sum(k["ours"].get(kind, 0) for k in rows)
            theirs = sum(k["theirs"].get(kind, 0) for k in rows)
            more, fewer, p = two_sided([k["ours"].get(kind, 0) / a for k, a, _ in both],
                                       [k["theirs"].get(kind, 0) / b for k, _, b in both])
            who = "ours" if more > fewer else "theirs" if fewer > more else "even"
            lines.append(f"  {kind:34s} {ours / n:7.2f} {theirs / n:8.2f} {pct(ours, ours_all):>6s} "
                         f"{pct(theirs, theirs_all):>7s}  {more:>4d}-{fewer:<4d} {who:6s}          {p:.2g}")
        if rest:
            ours = sum(k["ours"].get(kind, 0) for k in rows for kind in rest)
            theirs = sum(k["theirs"].get(kind, 0) for k in rows for kind in rest)
            lines.append(f"  {f'{len(rest)} other kinds':34s} {ours / n:7.2f} {theirs / n:8.2f} "
                         f"{pct(ours, ours_all):>6s} {pct(theirs, theirs_all):>7s}")
    return lines


def doom_lines(w: Watched, side: str | None, feed_from: int = FEED_FROM) -> list[str]:
    lines = [f"{'team':4s} {'id':>3s} {'round':>5s} {'len':>3s} {'reason':13s} {'place':6s} {'pocket':7s} "
             f"{'since':>5s} {'lead':>4s} {'trigger':8s} room fed shutter"]
    for d in w.dooms:
        if side and d.team != side:
            continue
        since = "-" if d.doomed_since is None else str(d.doomed_since)
        lead = "-" if d.lead is None else str(d.lead)
        lines.append(f"{d.team:4s} {d.id:3d} {d.round:5d} {d.length:3d} {d.reason:13s} {d.place:6s} "
                     f"{d.pocket or '-':7s} {since:>5s} {lead:>4s} {d.trigger:8s} "
                     f"{'-' if d.room is None else d.room:>4} {'yes' if d.fed else '-':3s} {shut_text(d)}")
    for team in ([side] if side else list(replays.TEAMS)):
        mine = [d for d in w.dooms if d.team == team]
        tally = Counter(death_kind(d, None, feed_from) for d in mine)
        own = [d for d in mine if d.trigger in ("ate", "portal", "deeper")]
        shut = [d for d in mine if d.trigger == "teammate"]
        lines.append(f"team {team}: {len(mine)} deaths; " + ", ".join(f"{k} {n}" for k, n in tally.most_common())
                     + f"; of {len(own)} doomed by their own move, {sum(d.fed for d in own)} ate a pearl on it"
                     + (f"; of {len(shut)} shut in by a teammate, {sum(d.shutter is not None for d in shut)} by one "
                        f"alone; that shutter split on the shutting move {sum(d.shut_split == 'that turn' for d in shut)}, "
                        f"on its turn before {sum(d.shut_split == 'turn before' for d in shut)}" if shut else ""))
    return lines


# A doom's shutter column: "-" but for a teammate trap, its shutter's id ("several" when none alone) and when it split.
def shut_text(d: Doom) -> str:
    if d.trigger != "teammate":
        return "-"
    who = "several" if d.shutter is None else str(d.shutter)
    return who + (f", split {d.shut_split}" if d.shut_split else "")

# The replay at `path` as frames, turns and logs; with `doom`, also every death's Doom (w.dooms, see `dooms`), which
# costs a flood fill a turn (about 1 s a Queen Of Spades game).
# With `side` ("A" or "B"), a doom walk judges that team's dragons only and keeps only its dooms: a dragon's judgement
# reads every body but only its own earlier judgements, so its dooms come out the same, for about half the work (a
# target batch reads one side a replay, referee/measures.py).
def watch(path: Path, doom: bool = False, side: str | None = None) -> Watched:
    replay = replays.load_replay(path)
    info = replays.parse_map(replay.map)
    board = parse(replay.map)
    walker = _DoomRecorder(info, board, side) if doom else _Recorder(info)
    for event in replay.events:
        walker.on_event(event)
    walker.finish()
    game = replays.game_of(replay, info, walker)  # the recorders only add to the base walk, so it is analyse's Game
    # The site's replays from 2026-09-27 ~21:10 AEST keep every TILE line as `x y 0 0`, which parse reads as a map
    # without pearls: text giving no tile a spawn withholds every tile's timings. Every spawning tile gets a
    # pearlCountdown before round 0 (4275 online replays with timings, 2026-09-27), so countdowns make it known again.
    if walker.countdowns:
        board.unknown = set()
    elif not any(hi for _, hi in board.gaps.values()):
        board.unknown = set(board.all_tiles())
    spawners = {t for t, (_, hi) in board.gaps.items() if hi} | set(walker.countdowns)
    w = Watched(game, board, spawners, walker.frames, walker.turns, walker.countdowns, walker.logs,
                walker.before_death, eats=walker.eats)
    if doom:
        w.dooms = walker.dooms(game)
        for index, d in enumerate(w.dooms):
            d.beside_mate = looks_fed(game.deaths[index], w.before_death.get(index), lambda a, b: manhattan(w, a, b))
            d.at_cap = at_cap(game.deaths[index], w.before_death.get(index))
        w.dooms = [d for d in w.dooms if side in (None, d.team)]
    return w


def wrap_delta(a: int, b: int, n: int) -> int:
    d = (b - a) % n
    return d - n if d > n // 2 else d


def chebyshev(w: Watched, a: Point, b: Point) -> int:
    return max(abs(wrap_delta(a[0], b[0], w.board.w)), abs(wrap_delta(a[1], b[1], w.board.h)))


def manhattan(w: Watched, a: Point, b: Point) -> int:
    return abs(wrap_delta(a[0], b[0], w.board.w)) + abs(wrap_delta(a[1], b[1], w.board.h))


def team_of(w: Watched, dragon_id: int) -> str | None:
    for frame in w.frames.values():
        if dragon_id in frame.dragons:
            return frame.dragons[dragon_id][0]
    return None


# ---------------------------------------------------------------- dragon: one dragon's timeline

def dragon_lines(w: Watched, dragon_id: int, first: int, last: int) -> list[str]:
    lines = ["round head    len action ate | in view: pearls (nearest), enemy heads id:len@dist, allies | nearest pearl"]
    for r in sorted(w.frames):
        if not first <= r <= last or dragon_id not in w.frames[r].dragons:
            continue
        frame = w.frames[r]
        team, body = frame.dragons[dragon_id]
        head = body[0]
        seen = [p for p in frame.pearls if chebyshev(w, head, p) <= VISION]
        near = min(seen, key=lambda p: manhattan(w, head, p), default=None)
        enemies = sorted((chebyshev(w, head, b[0]), i, len(b)) for i, (t, b) in frame.dragons.items()
                         if t != team and chebyshev(w, head, b[0]) <= VISION)
        allies = sum(1 for i, (t, b) in frame.dragons.items()
                     if t == team and i != dragon_id and chebyshev(w, head, b[0]) <= VISION)
        nearest = min((manhattan(w, head, p) for p in frame.pearls), default=None)
        turn = w.turns.get((r, dragon_id), Turn("-"))
        view = f"{len(seen)} ({near[0]},{near[1]} d{manhattan(w, head, near)})" if near else "0"
        foes = " ".join(f"{i}:{n}@{d}" for d, i, n in enemies) or "no enemies"
        lines.append(f"{r:5d} {head[0]:2d},{head[1]:<2d} {len(body):5d} {turn.action:6s} {turn.ate or '':3} | "
                     f"{view}, {foes}, {allies} allies | {nearest if nearest is not None else '-'}")
    return lines


# ---------------------------------------------------------------- frames: the board around a point

def frame_lines(w: Watched, r: int, center: Point, radius: int, focus: int | None) -> list[str]:
    frame = w.frames[r]
    cells: dict[Point, str] = {}
    for dragon_id, (team, body) in frame.dragons.items():
        for k, p in enumerate(body):
            cells[p] = ("@" if dragon_id == focus else team) if k == 0 else team.lower()
    W, H = w.board.w, w.board.h
    xs = [(center[0] + dx) % W for dx in range(-radius, radius + 1)]
    ys = [(center[1] + dy) % H for dy in range(-radius, radius + 1)]
    out = [f"round {r}, around {center[0]},{center[1]} (x {xs[0]}..{xs[-1]}, y {ys[0]}..{ys[-1]}, wrapping)"]
    for y in ys:
        edges = ""
        for x in xs:
            e = w.board.edges.get(("h", x, y))
            edges += " " + ("-" if e == KELP else "~" if e is not None else " ")
        out.append(edges)
        row = ""
        for x in xs:
            e = w.board.edges.get(("v", x, y))
            row += "|" if e == KELP else ":" if e is not None else " "
            tile = (x, y)
            row += cells.get(tile) or ("*" if tile in frame.pearls else "?" if tile in w.board.unknown
                                       else "+" if tile in w.spawners else ".")
        out.append(row)
    legend = [f"{i}:{t}{len(b)}@{b[0][0]},{b[0][1]} {w.turns.get((r, i), Turn('-')).action}"
              for i, (t, b) in sorted(frame.dragons.items()) if chebyshev(w, center, b[0]) <= radius]
    out.append("dragons here (id:team+length@head action this round): " + ", ".join(legend))
    return out


# ---------------------------------------------------------------- scan: moments worth watching

# Circling, per move: the last 8 heads (this round's and the 7 before) cover at most 4 tiles and nothing was eaten
# in those rounds. `heads` is one dragon's (round, head) at the start of each round it lived, in order; returns one
# flag per entry. Used by `scan` and by tools/blunders.py.
def circling_flags(turns: dict[tuple[int, int], Turn], dragon_id: int, heads: list[tuple[int, Point]]) -> list[bool]:
    flags = []
    for k in range(len(heads)):
        window = heads[max(0, k - 7):k + 1]
        ate = any(turns.get((r, dragon_id), Turn("")).ate for r, _ in window)
        flags.append(len(window) == 8 and len({h for _, h in window}) <= 4 and not ate)
    return flags


# A debug log line "r 12 len 4 body 4 dir N ... rule feed ..." as {key: value}.
def log_fields(text: str) -> dict[str, str]:
    words = text.split()
    return dict(zip(words[0::2], words[1::2]))


@dataclass
class Ram:
    """One of a team's rams: a move of ours into an enemy head (both die, docs/unsw/death.md), in round `round` by
    dragon `rammer`. `length` is ours at death, `start` ours before the move, `gained` and `theirs` each team's
    pearls eaten from the two drops, `dropped` those drops' pearls, `steps` the move's (None without our death)."""
    round: int
    rammer: int
    victim_length: int
    length: int
    start: int
    gained: int
    theirs: int
    dropped: int
    steps: int | None

    @property
    def swing(self) -> int:
        """Method note M51: the victim's length less its team's pickups, minus our length before the move less our
        pickups (a pearl the move ate on its way counts as a pickup). A net that left out the victim's loss read
        every bucket negative."""
        return (self.victim_length - self.theirs) - (self.start - self.gained)


# Each of `side`'s rams in `w`. A k-step sprint pays one segment a step after the first except the fatal one (k-2 by
# the ram's step; `step` is the death's, its `of` unless the sprint was cut short), so our length at the start is the
# length at death plus those, less the pearls the move ate on its way: a sprint shortens the rammer, which bucketing
# by length at death put in a longer-victim band. Steps are None and nothing is paid when the replay has no death
# of ours for it. loop/rulecounts.py ram_events and tools/fit.py's ram_swing outcome read these.
def rams(w: Watched, side: str) -> list[Ram]:
    index = {id(d): k for k, d in enumerate(w.game.deaths)}
    eaten = Counter((m["death"], m["team"]) for m in w.game.meals if m["death"] is not None)
    other = "B" if side == "A" else "A"
    found = []
    for victim in w.game.deaths:
        if victim.get("hitByTeam") != side or victim["team"] == side or victim["reason"] != "hitHeadToHead":
            continue
        ours = next((d for d in w.game.deaths if d["team"] == side and d["round"] == victim["round"]
                     and d.get("movedInto") == victim["id"]), None)
        length = ours["length"] if ours else victim["hitByLength"]
        drops = [index[id(victim)]] + ([index[id(ours)]] if ours else [])
        gained, theirs = (sum(eaten[k, team] for k in drops) for team in (side, other))
        dropped = sum(-(-d["length"] // 2) for d in (victim, ours) if d)
        steps = ours.get("step") if ours else None
        paid = max(0, steps - 2) if steps else 0
        turn = w.turns.get((victim["round"], victim["hitBy"]))
        found.append(Ram(victim["round"], victim["hitBy"], victim["length"], length,
                         length + paid - (turn.ate if turn else 0), gained, theirs, dropped, steps))
    return found


# Each of `side`'s rams (rams) as [victim gain at its start (the victim's length less ours before the move), a head
# edge, the swing, the steps]: the rows rulecounts' online ram cells read for replays without our debug line (method
# M85; loop/online.py refresh_rams keeps them per game). The edge is an estimate of Safety::headEdge, which counts
# every dragon the rammer sees at its own turn (+1 ours, itself included, -1 theirs): here the heads within wrapped
# Chebyshev 3 of its head (the 7x7 view) on the round-start frame, before the round's earlier movers; None when the
# frame lacks the rammer. On the 876 rams of 10 local debug replays (~/.bc-loop/rule-games' 9 of 2026-09-29 and
# tools/testdata's fit-ram replay) it equalled the logged `edge` in 563 (64%) and was within 1 in 832 (95%).
def ram_rows(w: Watched, side: str) -> list[list]:
    rows = []
    for ram in rams(w, side):
        frame = w.frames.get(ram.round)
        body = frame.dragons.get(ram.rammer) if frame else None
        edge = None if body is None else sum(1 if team == body[0] else -1 for team, other in frame.dragons.values()
                                             if other and chebyshev(w, body[1][0], other[0]) <= 3)
        rows.append([ram.victim_length - ram.start, edge, ram.swing, ram.steps])
    return rows


# bot/main.cpp's `rule` for a turn spent dying on purpose beside a teammate: "capfeed" (E0231, feeding the king at the
# unit cap, capfeed.hpp) and "recycle" (E0262's candidate, dying at the unit cap so teammates eat the drop) are the same
# death for other reasons, so deliberate_deaths calls all three "feed".
DELIBERATE_RULES = ("feed", "capfeed", "recycle")
SPLIT_OUT_PARENT = 2  # the engine's MIN_SIZE: a doomed dragon splits all but 2 out (split.hpp splitOutLength)
SPLIT_OUT_WINDOW = 1  # split_out_parent: the parent dies on its next turn (split.hpp: "only a length-2 parent dies")


# Whether an enemy head killed the dragon of `death` (a head-on with the other team): never a deliberate death,
# whatever its turn meant to do.
def killed_by_enemy(death: dict) -> bool:
    other = death.get("hitByTeam") or death.get("movedIntoTeam")
    return death["reason"] == "hitHeadToHead" and other is not None and other != death["team"]


# Why each death in `w` was deliberate, {(round, dragon id): why}, read from the dying dragon's own debug log lines
# (P.debugIndicator; `rule <name>` from E0116; E0127's `feed 1` flag says the same as `rule feed`): its dying turn's
# rule was feed or capfeed (dying beside a teammate so it eats our pearls; both are "feed"), or it died
# SPLIT_OUT_PARENT long the round after a split-out (the doomed parent left behind while its child walks away), and no
# enemy head killed it. A `fold` turn is only the scored move while folding toward the king, so a death on one is a
# real death (the review of 2026-09-27: two in the sample were head-ons with a 3-long enemy). Method note M36: the
# rule counts called 48% of split-out children young deaths, while in 152 online games 198 of 225 such deaths were
# hitWall at age 3-4 after r397, feeds to the folding king. A game with no log lines (the ladder, an old champion) has
# none, so its deaths count as before.
def deliberate_deaths(w: Watched) -> dict[tuple[int, int], str]:
    own: dict[int, dict[int, dict[str, str]]] = {}
    for r, dragon_id, _, text in w.logs:
        own.setdefault(dragon_id, {})[r] = log_fields(text)
    found = {}
    for death in w.game.deaths:
        if killed_by_enemy(death):
            continue
        lines = own.get(death["id"], {})
        dying, before = lines.get(death["round"], {}), lines.get(death["round"] - 1, {})
        if dying.get("rule") in DELIBERATE_RULES:
            found[death["round"], death["id"]] = "feed"
        elif death["length"] <= SPLIT_OUT_PARENT and before.get("rule") == "splitout":
            found[death["round"], death["id"]] = "split-out parent"
    return found


# Whether `death` was a split-out's parent, read from the moves alone, for replays without debug lines (the ladder's,
# either team): its own split in the SPLIT_OUT_WINDOW rounds before left it SPLIT_OUT_PARENT long, and no enemy head
# killed it. A bot splits out when every move is fatal (split.hpp splitOutLength: all but the engine's 2-segment
# minimum go to the child, our tail reversed), so the parent dies on its next turn. An ordinary split that leaves a
# 2-long parent is counted too when that parent dies at once (deliberate_deaths, from the debug lines, tells them
# apart). A feed is told from its moves apart from this (looks_fed).
def split_out_parent(death: dict, turns: dict[tuple[int, int], Turn]) -> bool:
    if death["length"] > SPLIT_OUT_PARENT or killed_by_enemy(death):
        return False
    return any(turns.get((r, death["id"]), Turn("")).action.startswith("split")
               for r in range(death["round"] - SPLIT_OUT_WINDOW, death["round"]))


def scan(w: Watched, team: str | None) -> list[tuple[str, int, str]]:
    """(kind, round, description) for one team (or both), in round order. A deliberate death (deliberate_deaths) is
    "deliberate death:<why>", never a young death or a death by cause, which are failures."""
    found: list[tuple[str, int, str]] = []
    rounds = sorted(w.frames)
    ids = {i for f in w.frames.values() for i in f.dragons}
    for dragon_id in sorted(ids):
        t = team_of(w, dragon_id)
        if team and t != team:
            continue
        heads: list[tuple[int, Point, int]] = []  # (round, head, length)
        for r in rounds:
            if dragon_id in w.frames[r].dragons:
                body = w.frames[r].dragons[dragon_id][1]
                heads.append((r, body[0], len(body)))
        run_start, run = None, 0
        flags = circling_flags(w.turns, dragon_id, [(r, h) for r, h, _ in heads])
        for k in range(len(heads)):
            window = heads[max(0, k - 7):k + 1]
            circling = flags[k]
            if circling:
                run_start = window[0][0] if run == 0 else run_start
                run += 1
            if run and (not circling or k == len(heads) - 1):
                if run >= IDLE_MIN:
                    r0, head = run_start, heads[k][1]
                    near = min((manhattan(w, head, p) for p in w.frames[r0].pearls), default=None)
                    found.append(("circling", r0, f"dragon {dragon_id} (team {t}, length {heads[k][2]}) went round "
                                  f"{len({h for _, h, _ in heads[k - run:k + 1]})} tiles near {head[0]},{head[1]} for "
                                  f"{run + 7} rounds, eating nothing; nearest pearl then {near} steps away"))
                run = 0
        # Hungry: a long stretch without eating although pearls came into view.
        last_ate = heads[0][0] if heads else 0
        saw = 0
        for r, head, _ in heads:
            if w.turns.get((r, dragon_id), Turn("")).ate:
                if r - last_ate >= HUNGRY_MIN and saw:
                    found.append(("hungry", last_ate, f"dragon {dragon_id} (team {t}) ate nothing for {r - last_ate} "
                                  f"rounds from round {last_ate} though pearls were in view on {saw} of them"))
                last_ate, saw = r, 0
            elif any(chebyshev(w, head, p) <= VISION for p in w.frames[r].pearls):
                saw += 1
    deliberate = deliberate_deaths(w)
    for death in w.game.deaths:
        if team and death["team"] != team:
            continue
        reason, length = death["reason"], death["length"]
        detail = f"dragon {death['id']} (team {death['team']}, length {length}) died: {reason}"
        if why := deliberate.get((death["round"], death["id"])):
            found.append((f"deliberate death:{why}", death["round"], detail + f" at {death['head'][0]},"
                          f"{death['head'][1]}, on purpose (its debug line: {why})"))
            continue
        if reason == "hitHeadToHead":
            other = death.get("hitByLength", death.get("movedIntoLength"))
            same = (death.get("hitByTeam") or death.get("movedIntoTeam")) == death["team"]
            detail += f" with {'a teammate' if same else 'an enemy'} of length {other}"
            kind = "death:friendly head-on" if same else ("death:head-on vs shorter or equal"
                                                           if other is not None and other <= length else "death:head-on")
        else:
            kind = f"death:{reason}"
        if 0 <= death.get("born", -1) and death["round"] - death["born"] <= 10:
            found.append(("young death", death["round"], detail + f", {death['round'] - death['born']} rounds after "
                          f"splitting off {death.get('parent')}"))
        found.append((kind, death["round"], detail + f" at {death['head'][0]},{death['head'][1]}"))
    for side in ([team] if team else list(replays.TEAMS)):
        other = next(x for x in replays.TEAMS if x != side)
        if w.game.winner == other:
            led = [s.round for s in w.game.snapshots if s.total[side] > s.total[other]]
            if led and led[-1] >= 100:
                found.append(("lead lost", led[-1], f"team {side} led in total length until round {led[-1]} and lost"))
    return sorted(found, key=lambda f: f[1])


def parse_rounds(text: str | None, last: int) -> tuple[int, int]:
    if not text:
        return 0, last
    a, _, b = text.partition("-")
    return int(a), int(b or a)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", help="moments worth watching, by kind")
    s.add_argument("replays", type=Path, nargs="+")
    s.add_argument("--team", choices=replays.TEAMS)
    s.add_argument("--tally", action="store_true", help="count each kind per game instead of listing moments")
    d = sub.add_parser("dragon", help="one dragon's timeline")
    d.add_argument("replay", type=Path)
    d.add_argument("--id", type=int, required=True)
    d.add_argument("--rounds")
    m = sub.add_parser("doom", help="when and why each death became unavoidable")
    m.add_argument("replays", type=Path, nargs="+")
    m.add_argument("--team", choices=replays.TEAMS, help="our side (default: from the replay's index or name)")
    m.add_argument("--summary", action="store_true", help="a per-map table of our deaths against theirs, per game")
    m.add_argument("--json", action="store_true", help="each replay's deaths as one JSON line")
    m.add_argument("--feed-from", type=int, default=FEED_FROM,
                   help="our bot's P.foldRound, from which a feed is labelled by its moves (default %(default)s)")
    f = sub.add_parser("frames", help="the board around a dragon or point")
    f.add_argument("replay", type=Path)
    f.add_argument("--rounds", required=True)
    f.add_argument("--around", type=int, help="dragon id to centre on and mark with @")
    f.add_argument("--at", help="x,y to centre on (with --around, still centred here)")
    f.add_argument("--radius", type=int, default=5)
    y = sub.add_parser("why", help="our bot's debug lines for a recorded game, played again (tools/why.py)")
    y.add_argument("replay", type=Path)
    y.add_argument("--rounds", required=True)
    y.add_argument("--dragon", type=int, help="only this dragon of ours")
    y.add_argument("--side", choices=replays.TEAMS, help="our side, if the replay's bot names don't say")
    y.add_argument("--commit", help="our bot's commit, if the submission isn't in the loop home's upload records")
    y.add_argument("--plain", action="store_true", help="play the exact build, no debug line: only the check")
    args = ap.parse_args()

    if args.command == "why":
        import why
        first, last = parse_rounds(args.rounds, 10**9)
        print("\n".join(why.report(args.replay, first, last, args.dragon, args.side, args.commit, args.plain)))
        return 0
    if args.command == "scan":
        tally: Counter[str] = Counter()
        for path in args.replays:
            try:
                found = scan(watch(path), args.team)
            except Exception as error:  # one broken replay must not stop the rest
                print(f"{path}: unreadable: {error}", file=sys.stderr)
                continue
            if args.tally:
                tally.update(kind for kind, _, _ in found)
                continue
            print(f"== {path.name}: {len(found)} moments")
            for kind, r, text in found:
                print(f"  r{r:<4d} {kind:32s} {text}")
        if args.tally:
            games = len(args.replays)
            print(f"{games} games, team {args.team or 'both'}: moments per game")
            for kind, n in tally.most_common():
                print(f"  {kind:32s} {n / games:6.2f}")
            if note := replays.chosen_note(args.replays):
                print(note)
        return 0
    if args.command == "doom":
        return doom_main(args)
    w = watch(args.replay)
    first, last = parse_rounds(args.rounds, max(w.frames))
    if args.command == "dragon":
        print("\n".join(dragon_lines(w, args.id, first, last)))
        return 0
    for r in range(first, last + 1):
        if r not in w.frames:
            continue
        if args.at:  # with --around too, the board stays put and marks that dragon
            x, y = args.at.split(",")
            center = (int(x), int(y))
        elif args.around is not None:
            if args.around not in w.frames[r].dragons:
                print(f"round {r}: dragon {args.around} is not alive")
                continue
            center = w.frames[r].dragons[args.around][1][0]
        else:
            ap.error("frames needs --around ID or --at X,Y")
        print("\n".join(frame_lines(w, r, center, args.radius, args.around)) + "\n")
    return 0


def doom_main(args: argparse.Namespace) -> int:
    chosen = set(replays.result_chosen(args.replays)) if args.summary else set()
    games: dict[str, list[tuple[str, dict[str, dict[str, int]]]]] = {}  # by game_kind
    for path in args.replays:
        if path in chosen:
            continue
        side = args.team or our_side(path)
        try:
            w = watch(path, doom=True)
        except Exception as error:  # one broken replay must not stop the rest
            print(f"{path}: unreadable: {error}", file=sys.stderr)
            continue
        deliberate = deliberate_deaths(w)
        if args.summary:
            if side is None:
                print(f"{path}: our side unknown (give --team)", file=sys.stderr)
                continue
            games.setdefault(game_kind(path), []).append((w.game.map.name,
                                                          death_kinds(w.dooms, side, deliberate, args.feed_from)))
        elif args.json:
            print(json.dumps({"replay": str(path), "map": w.game.map.name, "side": side,
                              "kinds": death_kinds(w.dooms, side, deliberate, args.feed_from) if side else None,
                              "deaths": [{**vars(d), "lead": d.lead} for d in w.dooms]}))
        else:
            print(f"== {path.name}: {w.game.map.name}, {len(w.dooms)} deaths, winner {w.game.winner}"
                  + (f", we were {side}" if side else ""))
            print("\n".join("  " + line for line in doom_lines(w, None, args.feed_from)))
    if args.summary:
        if chosen:
            print(f"left out {len(chosen)} replays kept because the game was lost (replays.result_chosen)")
        print(DOOM_TABLE_NOTE + " Without debug lines (online replays) a feed is told by its moves alone (feed?).")
        for kind in ("ladder", "dev", "local"):
            if kind in games:
                print(f"{kind} games:" if kind != "ladder" else "ladder and autoscrim games:")
                print("\n".join(doom_table(games[kind])))
        if not games:
            print("no games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
