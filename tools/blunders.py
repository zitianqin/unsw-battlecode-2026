"""Blunder finder: checks every move our team made in saved replays against the other legal moves, the way a chess
engine marks mistakes, and groups the mistakes by situation so new kinds show up without being listed in advance.

    uv run python tools/blunders.py REPLAY... --team A [--horizon 2] [--window 5] [--worst 20] [--json out.json]

For each move of each of our dragons, every single-step alternative is played forward for `--horizon` of that
dragon's turns (default 2), with every other dragon's actual actions from the replay held fixed and this dragon's
later turns chosen as well as possible (single steps). Each line is scored for our team:

    score = our length - their length, over the dragons that could be involved, minus 4 per dragon lost,

and regret = the best alternative's score minus the chosen move's. A move is flagged when regret >= --min-regret
(default 1, one pearl). A local SPRT replay's name ends in -aA or -aB: our candidate played that side.

It only finds tactical mistakes, visible within the horizon: a death it could have avoided, a pearl it could have
had, a sprint that bought nothing, a losing head-on trade. Strategy (where to go, when to split) is out of reach.
To keep false positives down, the chosen move is judged optimistically and alternatives pessimistically:
- others' outcomes: an alternative gets no credit for an enemy that dies or a teammate that survives only in the
  what-if (they would have reacted), and the chosen move is not charged for them;
- an alternative gets no credit for pearls that a death in the what-if dropped (hindsight);
- an alternative whose head ends where an enemy head of at most its length could ram it (one step, or two for an
  enemy that can sprint) is not counted;
- a dragon left with no free neighbouring tile at the horizon counts as dead (for the chosen move only when it is
  also too short to split);
- when the only thing an alternative gains is pearls (the chosen line survives the horizon), the chosen line is
  also credited with every pearl the dragon really ate in the move's round and the `--window` rounds after it
  (default 5; up to a split or its death), since a move toward a pearl often eats it just past the horizon, and
  with each pearl of the better line that a teammate really ate in that time (the team got it anyway).
  Alternatives keep the short horizon. A regret this removes is counted as withdrawn ("ate it later", or "a
  teammate ate it" when its own pearls alone wouldn't have), not flagged;
- a dragon alive only in the what-if (it died in the replay, so it has no next action) stands still; if it is
  within ramming reach of our head, anything runs into it, or a split's outcome depends on the unit limit, that
  line isn't counted;
- deaths our bot chooses on purpose are strategy, not tactics, and aren't flagged: turning into its own neck with a
  free tile available, or a death (not caused by an enemy) whose pearls a teammate ate within 10 rounds (the
  endgame fold feeds the longest dragon, which the length score undervalues).
The rules are implemented from docs/unsw and checked every round: replaying each round's actual actions from its
recorded start must reproduce the next round's recorded bodies and pearls, and nothing is flagged from a round
where it doesn't (the agreement rate is printed).

Pearl spawns come from the replay's pearlCountdown events (a countdown redrawn at zero is a spawn attempt, whether
or not the tile was free). The site's replays zero the map text's pearl gaps from 2026-09-27 ~21:10 AEST but keep
these events (as of 22:00 that day); it may strip them too. A replay without any is replayed
with the spawns it shows instead, so a spawn a body blocked for real never happens in a what-if and pearls about to
spawn aren't credited to alternatives. Pearl regrets from such games are undercounted: the summary opens with a
WARNING naming them, each flagged move of theirs carries the no_countdowns label, and the JSON's games have
"countdowns": 0.

Flagged moves are labelled with the named checks where they apply: died_with_safe_move (it died before its next
turn though an alternative survived; the label says what killed it), walked_from_visible_pearl (it moved further
from a visible pearl it was strictly nearest to within 3 steps, closer to no other, with no enemy head in view),
rammed_longer / rammed_shorter_or_equal (it moved into an enemy head of that length; both always die), circling
(watch.py's definition: the last 8 heads cover at most 4 tiles with nothing eaten, and no enemy head in view).
Then groups: flagged moves bucketed by situation (why the regret, nearest enemy head, pearls in reach, free
neighbouring tiles, sprint), largest total regret first, plus regret by single feature.
Open a flagged move with the printed `watch.py dragon` / `watch.py frames` commands.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import replays  # noqa: E402
import watch  # noqa: E402
from mapgen.gen import parse as parse_board  # noqa: E402

DIRS = "NESW"
DEATH = 4.0  # score lost per dragon death, on top of its length
UNIT_LIMIT = replays.UNIT_LIMIT  # a split needs the team below this many living dragons
MAX_RELEVANT = 40  # a what-if involving more dragons than this is skipped as too slow
WINDOW = 5  # rounds after the move in which the chosen line's real pearls count against a pearl-only regret
PHASES = ((0, 99, "r0-99"), (100, 299, "r100-299"), (300, 449, "r300-449"), (450, 10**9, "r450+"))
LABELS = ("died_with_safe_move", "walked_from_visible_pearl", "rammed_longer", "rammed_shorter_or_equal", "circling")

Action = tuple  # ("move", (dir, ...)) | ("split", n) | ("die", why)


def phase(r: int) -> str:
    return next(name for lo, hi, name in PHASES if lo <= r <= hi)


def parse_action(text: str) -> Action:
    if text and all(c in DIRS for c in text):
        return ("move", tuple(DIRS.index(c) for c in text))
    if text.startswith("split "):
        return ("split", int(text.split()[1]))
    return ("die", text)  # suicide, timeout, none


# ---------------------------------------------------------------- the replay, as a timeline


class _Timeline(watch._Recorder):
    """watch.py's recorder, also keeping each round's pearl spawn attempts (and the spawns seen, for a replay
    without countdowns) and each split's child."""

    def __init__(self, info: replays.MapInfo) -> None:
        super().__init__(info)
        self.attempts: dict[int, list[watch.Point]] = defaultdict(list)
        self.countdown_events = 0
        self.spawned: dict[int, list[watch.Point]] = defaultdict(list)
        self.births: dict[tuple[int, int], tuple[int, tuple[watch.Point, ...]]] = {}  # (round, parent) -> child

    def on_event(self, event: Any) -> None:
        kind = event.which()
        if kind == "pearlCountdown":
            self.countdown_events += 1
        if kind == "pearlCountdown" and self.mover is None:
            # A countdown is redrawn exactly when it reaches zero, whether or not the pearl could spawn.
            self.attempts[self.round].append((event.pearlCountdown.tile.x, event.pearlCountdown.tile.y))
        elif kind == "tileChange" and event.tileChange.hasPearl and self.mover is None:
            self.spawned[self.round].append((event.tileChange.tile.x, event.tileChange.tile.y))
        elif kind == "dragonSplit":
            s = event.dragonSplit
            self.births[(self.round, s.parentId)] = (s.childId, tuple((p.x, p.y) for p in s.childBody))
        super().on_event(event)


class Grid:
    """The board as tile numbers (y * width + x): where each step leads (-1 for kelp), portals included."""

    def __init__(self, board: Any) -> None:
        self.w, self.h = board.w, board.h
        self.nxt = [-1] * (board.w * board.h * 4)
        self.rev: list[list[int]] = [[] for _ in range(board.w * board.h)]
        for y in range(board.h):
            for x in range(board.w):
                for d, name in enumerate(DIRS):
                    to = board.step((x, y), name)
                    if to is not None:
                        t = to[1] * board.w + to[0]
                        self.nxt[(y * board.w + x) * 4 + d] = t
                        self.rev[t].append(y * board.w + x)

    def tile(self, p: watch.Point) -> int:
        return p[1] * self.w + p[0]

    def point(self, t: int) -> watch.Point:
        return (t % self.w, t // self.w)

    def cheb(self, a: int, b: int) -> int:
        return max(abs(watch.wrap_delta(a % self.w, b % self.w, self.w)),
                   abs(watch.wrap_delta(a // self.w, b // self.w, self.h)))

    # Tiles within `depth` steps of `start` (portals included, bodies ignored).
    def reach(self, start: int, depth: int) -> set[int]:
        seen, edge = {start}, [start]
        for _ in range(depth):
            edge = [n for t in edge for n in self.nxt[t * 4:t * 4 + 4] if n >= 0 and n not in seen]
            seen.update(edge)
        return seen


@dataclass
class Game:
    path: Path
    team: str
    info: replays.MapInfo
    grid: Grid
    last_round: int
    frames: dict[int, watch.Frame]
    turns: dict[tuple[int, int], watch.Turn]
    attempts: dict[int, list[int]]
    countdowns: int  # pearlCountdown events in the replay; 0: `attempts` are only the spawns it shows
    births: dict[tuple[int, int], tuple[int, tuple[int, ...]]]
    eats: dict[int, list[tuple[int, int]]]  # tile -> (round, dragon) for every pearl eaten there
    deaths: list[dict[str, Any]]
    teams: dict[int, str]
    parent: dict[int, int]  # child -> parent
    born: dict[int, int]  # child -> round
    born_in: dict[int, list[int]]  # round -> children
    actions: dict[tuple[int, int], Action]  # (round, id)
    died_at: dict[int, tuple[int, int]]  # id -> (round, whose turn it was)
    _bodies: dict[int, dict[int, tuple[int, ...]]] = field(default_factory=dict)
    _pearls: dict[int, frozenset[int]] = field(default_factory=dict)
    _index: dict[int, dict[int, list[int]]] = field(default_factory=dict)
    _fp: dict[tuple[int, int], frozenset[int]] = field(default_factory=dict)

    def action(self, r: int, i: int) -> Action | None:
        return self.actions.get((r, i))

    def bodies(self, r: int) -> dict[int, tuple[int, ...]]:
        """Every living dragon's body (head first) at the start of round r, before pearls spawn."""
        if r not in self._bodies:
            frame = self.frames[r]
            self._bodies[r] = {i: tuple(self.grid.tile(p) for p in b) for i, (_, b) in frame.dragons.items()}
        return self._bodies[r]

    def pearls(self, r: int) -> frozenset[int]:
        if r not in self._pearls:
            self._pearls[r] = frozenset(self.grid.tile(p) for p in self.frames[r].start_pearls)
        return self._pearls[r]

    # Tiles dragon i touches in round r: its body at the round's start (or birth) and every step of its action,
    # including steps it didn't live to take.
    def footprint(self, r: int, i: int) -> frozenset[int]:
        key = (r, i)
        if key not in self._fp:
            tiles: set[int] = set()
            body = self.bodies(r).get(i) if r in self.frames else None
            if body is None and self.born.get(i) == r:
                body = self.births[(r, self.parent[i])][1]
            if body:
                tiles.update(body)
                act = self.action(r, i)
                if act and act[0] == "move":
                    head = body[0]
                    for d in act[1]:
                        head = self.grid.nxt[head * 4 + d]
                        if head < 0:
                            break
                        tiles.add(head)
            self._fp[key] = frozenset(tiles)
        return self._fp[key]

    # tile -> dragons whose round-r footprint covers it.
    def index(self, r: int) -> dict[int, list[int]]:
        if r not in self._index:
            idx: dict[int, list[int]] = defaultdict(list)
            ids = set(self.bodies(r)) if r in self.frames else set()
            ids.update(self.born_in.get(r, ()))
            for i in ids:
                for t in self.footprint(r, i):
                    idx[t].append(i)
            self._index[r] = idx
        return self._index[r]


def load(path: Path, team: str) -> Game:
    replay = replays.load_replay(path)
    info = replays.parse_map(replay.map)
    walker = _Timeline(info)
    for event in replay.events:
        walker.on_event(event)
    walker.finish()
    last = walker.round
    # The state after the last round, as a frame, so every round has a recorded start and end.
    pearls = frozenset(walker.pearls)
    walker.frames[last + 1] = watch.Frame({d.id: (d.team, tuple(d.body)) for d in walker.dragons.values() if d.alive},
                                          pearls, pearls)
    grid = Grid(parse_board(replay.map))
    births = {k: (c, tuple(grid.tile(p) for p in b)) for k, (c, b) in walker.births.items()}
    born_in: dict[int, list[int]] = defaultdict(list)
    for (r, _), (c, _) in births.items():
        born_in[r].append(c)
    eats: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for r, i, p in walker.eats:
        eats[grid.tile(p)].append((r, i))
    return Game(
        path=path, team=team, info=info, grid=grid, last_round=last, frames=walker.frames, turns=walker.turns,
        attempts={r: [grid.tile(p) for p in ts]
                  for r, ts in (walker.attempts if walker.countdown_events else walker.spawned).items()},
        countdowns=walker.countdown_events, births=births,
        eats=dict(eats),
        deaths=walker.deaths, teams={d.id: d.team for d in walker.dragons.values()},
        parent={c: p for (_, p), (c, _) in births.items()}, born={c: r for (r, _), (c, _) in births.items()},
        born_in=born_in,
        actions={k: parse_action(t.action) for k, t in walker.turns.items()},
        died_at={d["id"]: (d["round"], d.get("hitBy", d["id"])) for d in walker.deaths})


# ---------------------------------------------------------------- the rules


class Unknown(Exception):
    """The what-if left what the replay can tell us (a dragon needs an action it never took)."""


class Mismatch(Exception):
    """Replaying the actual actions didn't reproduce the replay."""


class State:
    """Bodies (head first), which dragon is on each tile, pearls, and what happened so far in a line of play."""

    __slots__ = ("bodies", "occ", "pearls", "deaths", "eaten", "paid", "path", "dropped", "found", "log", "ghosts",
                 "bites")

    def __init__(self) -> None:
        self.bodies: dict[int, deque[int]] = {}
        self.occ: dict[int, int] = {}
        self.pearls: set[int] = set()
        self.deaths: dict[int, tuple[str, int | None, int, int]] = {}  # id -> (reason, other, round, whose turn)
        self.eaten: dict[int, int] = {}
        self.paid: dict[int, int] = {}
        self.path: tuple[Action, ...] = ()  # the watched dragon's actions in this line
        self.dropped: set[int] = set()  # pearls dropped by deaths in this line
        self.found: dict[int, int] = {}  # pearls eaten that some death in this line dropped
        self.log: list[tuple[str, int, int]] | None = None  # the replay's own line: ("drop"/"eat", tile, dragon)
        # In a what-if: when each dragon died in the replay, (round, whose turn). One still alive after that is a
        # ghost: where it would really have been is unknown, so anything that runs into it is too.
        self.ghosts: dict[int, tuple[int, int]] | None = None
        self.bites: tuple[tuple[int, int, int], ...] = ()  # (dragon, tile, round) per pearl eaten, not dropped here

    def copy(self) -> State:
        s = State.__new__(State)
        s.bodies = {i: deque(b) for i, b in self.bodies.items()}
        s.occ = dict(self.occ)
        s.pearls = set(self.pearls)
        s.deaths = dict(self.deaths)
        s.eaten = dict(self.eaten)
        s.paid = dict(self.paid)
        s.path = self.path
        s.dropped = set(self.dropped)
        s.found = dict(self.found)
        s.log = None
        s.ghosts = self.ghosts
        s.bites = self.bites
        return s

    def add(self, i: int, body: tuple[int, ...] | list[int]) -> None:
        self.bodies[i] = deque(body)
        for t in body:
            self.occ[t] = i

    def die(self, i: int, reason: str, other: int | None, r: int, turn: int) -> None:
        body = self.bodies.pop(i)
        for t in body:
            del self.occ[t]
        # Every second segment from the head becomes a pearl.
        drop = list(body)[::2]
        self.pearls.update(drop)
        self.dropped.update(drop)
        if self.log is not None:
            self.log.extend(("drop", t, i) for t in drop)
        self.deaths[i] = (reason, other, r, turn)

    def spawn(self, tiles: list[int] | set[int]) -> None:
        for t in tiles:
            if t not in self.occ:
                self.pearls.add(t)


# Applies one dragon's action (engine order: each step checks payment, kelp, own body, other dragons; a head-on
# kills the other dragon first). `can_split`: whether the team is below the unit limit.
def apply(st: State, nxt: list[int], i: int, act: Action, r: int, child: tuple[int, tuple[int, ...]] | None,
          can_split: bool) -> None:
    kind = act[0]
    if kind == "die":
        st.die(i, "noValidAction", None, r, i)
        return
    body = st.bodies[i]
    if kind == "split":
        n = act[1]
        keep = len(body) - n
        if n < 2 or keep < 2 or not can_split:
            st.die(i, "noValidAction", None, r, i)
            return
        if child is None:
            raise Unknown
        segs = list(body)
        st.bodies[i] = deque(segs[:keep])
        tail = segs[keep:][::-1]
        if tuple(tail) != child[1]:
            raise Unknown
        st.add(child[0], tail)
        return
    occ, pearls = st.occ, st.pearls
    for k, d in enumerate(act[1]):
        if k > 0 and len(body) <= 2:
            st.die(i, "noValidAction", None, r, i)
            return
        to = nxt[body[0] * 4 + d]
        if to < 0:
            st.die(i, "hitWall", None, r, i)
            return
        o = occ.get(to)
        if o is not None:
            if st.ghosts and o in st.ghosts and (r, i) > st.ghosts[o]:
                raise Unknown
            if o == i:
                st.die(i, "hitSelf", None, r, i)
            elif st.bodies[o][0] == to:
                st.die(o, "hitHeadToHead", i, r, i)
                st.die(i, "hitHeadToHead", o, r, i)
            else:
                st.die(i, "hitOtherBody", o, r, i)
            return
        body.appendleft(to)
        occ[to] = i
        if to in pearls:
            pearls.discard(to)
            st.eaten[i] = st.eaten.get(i, 0) + 1
            if to in st.dropped:
                st.found[i] = st.found.get(i, 0) + 1
            else:
                st.bites += ((i, to, r),)
            if st.log is not None:
                st.log.append(("eat", to, i))
        else:
            del occ[body.pop()]
        if k > 0:
            del occ[body.pop()]
            st.paid[i] = st.paid.get(i, 0) + 1


def state_at(game: Game, r: int) -> State:
    st = State()
    for i, body in game.bodies(r).items():
        st.add(i, body)
    st.pearls = set(game.pearls(r))
    return st


# ---------------------------------------------------------------- one move, played out


REAL, CHOSEN, ALT = "real", "chosen", "alt"


@dataclass
class Leaf:
    values: dict[int, float]  # per starting dragon: length of it and its children alive, minus DEATH per death
    alive: bool  # the watched dragon
    length: int
    eaten: int
    found: int  # of those, pearls some death in this line dropped
    bites: tuple[tuple[int, int], ...]  # (tile, round) of each one it ate that no death in this line dropped
    paid: int
    doomed: bool
    death: tuple[str, int | None, int, int] | None
    path: tuple[Action, ...]
    score: float = 0.0


class Whatif:
    """Everything needed to play one of our moves forward: the dragons that could be involved and the tiles they
    could touch. Dragons that can't reach those tiles in the window can't change its outcome and are left out."""

    def __init__(self, game: Game, full: State, r: int, y: int, horizon: int) -> None:
        self.game, self.r, self.y, self.h = game, r, y, horizon
        self.team = game.teams[y]
        g = game.grid
        rounds = [t for t in range(r, r + horizon + 1) if t in game.frames]
        self.rounds = rounds
        head = full.bodies[y][0]
        region = g.reach(head, horizon + 1) | set(full.bodies[y])
        for t in rounds:
            region |= game.footprint(t, y)
        relevant = {y}
        frontier = set(region)
        later = {c for b in range(r, r + horizon + 1) for c in game.born_in.get(b, ())
                 if b > r or game.parent[c] > y}  # born after this turn
        while frontier:
            new: set[int] = set()
            for t in rounds:
                idx = game.index(t)
                for tile in frontier:
                    for i in idx.get(tile, ()):
                        if i not in relevant and (i in full.bodies or i in later):
                            new.add(i)
            # A child born in the window needs its parent played, and a played parent brings its children.
            grow = set(new)
            while grow:
                i = grow.pop()
                for j in ([game.parent[i]] if i in later else []) + [c for c in later if game.parent[c] == i]:
                    if j not in relevant and j not in new:
                        new.add(j)
                        grow.add(j)
            frontier = set()
            for i in new:
                relevant.add(i)
                for t in rounds:
                    f = game.footprint(t, i) - region
                    region |= f
                    frontier |= f
            if len(relevant) > MAX_RELEVANT:
                break
        self.relevant = relevant
        self.region = region
        self.sub = State()
        for i in relevant:
            if i in full.bodies:
                self.sub.add(i, full.bodies[i])
        self.sub.pearls = full.pearls & region
        # Our own dragon alive after its real death is the point of the what-if, not a ghost.
        self.ghosts = {i: game.died_at[i] for i in relevant if i in game.died_at and i != y}
        self.roots = {i: i for i in self.sub.bodies}
        for c in sorted(later & relevant):
            self.roots[c] = self.roots.get(game.parent[c], game.parent[c])
        # The team's size outside the played dragons, for split legality.
        self.others_alive = {t: sum(1 for i in full.bodies if game.teams[i] == t and i not in relevant) for t in "AB"}
        self.enemy_heads_outside = [(full.bodies[i][0], len(full.bodies[i])) for i in full.bodies
                                    if i not in relevant and game.teams[i] != self.team]
        self.real_values: dict[int, float] = {}

    def root(self, i: int) -> int:
        while i not in self.roots:
            i = self.game.parent[i]
        return self.roots[i]

    def spawns(self, t: int) -> list[int]:
        return [x for x in self.game.attempts.get(t, ()) if x in self.region]

    def can_split(self, st: State, i: int) -> bool:
        team = self.game.teams[i]
        return self.others_alive[team] + sum(1 for j in st.bodies if self.game.teams[j] == team) < UNIT_LIMIT - 2

    # Splits follow the replay on the actual line. In a what-if a split that happened for real happens again unless
    # the team might be near the unit limit, where the replay can't say.
    def act(self, st: State, t: int, i: int, act: Action, mode: str) -> None:
        child = self.game.births.get((t, i))
        allowed = child is not None
        if act[0] == "split" and mode != REAL:
            body = st.bodies[i]
            if act[1] >= 2 and len(body) - act[1] >= 2 and (child is None or not self.can_split(st, i)):
                raise Unknown
        apply(st, self.game.grid.nxt, i, act, t, child, allowed)

    # Continues a line of play after dragon k's turn in round t, until the watched dragon's turn `h` rounds after
    # this move (or the game's end), branching on its own later turns. Appends the finished lines to `leaves`.
    def run(self, st: State, t: int, k: int, mode: str, leaves: list[Leaf]) -> None:
        game, y = self.game, self.y
        while True:
            i = min((j for j in st.bodies if j > k), default=None)
            if t == self.r + self.h and (i is None or i >= y):
                leaves.append(self.leaf(st, mode))
                return
            if i is None:
                if mode == REAL:
                    self.check(st, t + 1)
                t, k = t + 1, -1
                if t > game.last_round:
                    leaves.append(self.leaf(st, mode))
                    return
                st.spawn(self.spawns(t))
                continue
            if i == y and mode != REAL:
                for d in range(4):
                    s2 = st.copy()
                    s2.path += (("move", (d,)),)
                    try:
                        self.act(s2, t, y, ("move", (d,)), mode)
                        if mode == ALT and self.threatened(s2, False):
                            continue
                        self.run(s2, t, y, mode, leaves)
                    except Unknown:
                        pass
                return
            act = game.action(t, i)
            if act is None:
                if mode == REAL:
                    raise Mismatch(f"round {t}: dragon {i} alive but took no action")
                # Alive only in the what-if, so the replay has no action for it. It stays where it is, unless it
                # is an enemy that could ram our head from there.
                if game.teams[i] != self.team and self.can_ram(st, st.bodies[i][0], len(st.bodies[i])):
                    raise Unknown
                k = i
                continue
            if i == y:
                st.path += (act,)
            self.act(st, t, i, act, mode)
            k = i

    # The played dragons' bodies and the region's pearls must match the replay at the start of round t.
    def check(self, st: State, t: int) -> None:
        bodies = self.game.bodies(t)
        for i in self.relevant:
            got = tuple(st.bodies[i]) if i in st.bodies else None
            if got != bodies.get(i):
                raise Mismatch(f"round {t}: dragon {i} body differs")
        if st.pearls != self.game.pearls(t) & self.region:
            raise Mismatch(f"round {t}: pearls differ")

    def threatened(self, st: State, first: bool) -> bool:
        heads = [(b[0], len(b)) for i, b in st.bodies.items() if self.game.teams[i] != self.team]
        if first:  # dragons outside the what-if only matter where they stand now
            heads += self.enemy_heads_outside
        return any(self.can_ram(st, h, m) for h, m in heads)

    # Could an enemy head at h of length m, no longer than ours, reach our head in one step, or two if it can pay?
    def can_ram(self, st: State, h: int, m: int) -> bool:
        if self.y not in st.bodies:
            return False
        body = st.bodies[self.y]
        head = body[0]
        if m > len(body):
            return False
        nxt = self.game.grid.nxt
        for d in range(4):
            a = nxt[h * 4 + d]
            if a == head:
                return True
            if m >= 3 and a >= 0 and a not in st.occ and head in nxt[a * 4:a * 4 + 4]:
                return True
        return False

    def leaf(self, st: State, mode: str) -> Leaf:
        values = {root: 0.0 for root in set(self.roots.values())}
        for i, b in st.bodies.items():
            values[self.root(i)] += len(b)
        for i in st.deaths:
            values[self.root(i)] -= DEATH
        y = self.y
        alive = y in st.bodies
        length = len(st.bodies[y]) if alive else 0
        doomed = False
        if alive:
            nxt, head = self.game.grid.nxt, st.bodies[y][0]
            free = any(nxt[head * 4 + d] >= 0 and nxt[head * 4 + d] not in st.occ for d in range(4))
            doomed = not free and (mode == ALT or length < 4)
        return Leaf(values, alive, length, st.eaten.get(y, 0), st.found.get(y, 0),
                    tuple((t, u) for i, t, u in st.bites if i == y), st.paid.get(y, 0), doomed, st.deaths.get(y),
                    st.path)

    def score(self, leaf: Leaf, mode: str) -> float:
        total = 0.0
        pick = min if mode == ALT else max  # teammates: pessimistic for alternatives, optimistic for the choice
        for root, v in leaf.values.items():
            real = self.real_values.get(root, v)
            if root == self.y:
                total += v - ((leaf.length + DEATH) if leaf.doomed else 0)
                if mode == ALT:  # pearls that only a death in the what-if put there are hindsight
                    total -= leaf.found
            elif self.game.teams[root] == self.team:
                total += pick(v, real)
            else:
                total -= (max if mode == ALT else min)(v, real)
        return total


# An upper bound on a move's regret without playing it out. An alternative gains nothing from other dragons
# (their outcomes are scored pessimistically) or from pearls deaths drop, so it can beat the actual line only by
# pearls already within reach or spawning there, or by paying for fewer sprint steps. When the dragon lived
# through its next turn (so it wasn't trapped) and its own actions were all moves, regret is at most
# (pearls it could reach) - (pearls it ate) + (sprint steps it paid). Returns infinity when that doesn't apply.
def regret_bound(game: Game, full: State, r: int, y: int, horizon: int) -> float:
    end = min(r + horizon + 1, game.last_round + 1)
    if y not in game.bodies(end):
        return float("inf")
    eaten = paid = 0
    for t in range(r, min(r + horizon, game.last_round + 1)):
        a = game.action(t, y)
        if a is None or a[0] != "move":
            return float("inf")
        eaten += game.turns[(t, y)].ate
        paid += len(a[1]) - 1
    reach = game.grid.reach(full.bodies[y][0], horizon)
    pearls = len(reach & full.pearls)
    pearls += sum(1 for t in range(r + 1, r + horizon) for x in game.attempts.get(t, ()) if x in reach)
    return pearls - eaten + paid


@dataclass
class Verdict:
    regret: float
    chosen: Leaf | None = None  # None: nothing to compare, regret 0
    best: Leaf | None = None
    # Set when the window lowered a pearl-only regret: the regret at the horizon, the pearls the dragon really ate
    # in the window, and how many of the better line's pearls a teammate really ate in it.
    horizon_regret: float | None = None
    window_ate: int | None = None
    teammate_ate: int | None = None


# Pearls dragon y really ate in rounds r..r+window, stopping at its first split, give-up or death (a round it
# didn't survive counts nothing: it kept none of that round's pearls).
def window_ate(game: Game, r: int, y: int, window: int) -> int:
    ate = 0
    for t in range(r, min(r + window, game.last_round) + 1):
        act = game.action(t, y)
        if act is None or act[0] != "move" or y not in game.bodies(t + 1):
            break
        ate += game.turns[(t, y)].ate
    return ate


# How many of the tiles where the better line ate a pearl a teammate of y really ate a pearl on in rounds r..r+window.
def teammate_ate(game: Game, r: int, y: int, bites: tuple[tuple[int, int], ...], window: int) -> int:
    team = game.teams[y]
    return sum(1 for tile in {t for t, _ in bites}
               if any(r <= u <= r + window and i != y and game.teams[i] == team for u, i in game.eats.get(tile, ())))


# Plays one of our moves and its alternatives forward. Returns a Verdict, or why the move wasn't judged. Raises
# Mismatch when the actual line doesn't reproduce the replay.
def judge(game: Game, full: State, r: int, y: int, horizon: int, min_regret: float,
          window: int = WINDOW) -> Verdict | str:
    act = game.action(r, y)
    if act is None or act[0] == "split":
        return "split"
    if regret_bound(game, full, r, y, horizon) < min_regret:
        return Verdict(0.0)
    w = Whatif(game, full, r, y, horizon)
    if len(w.relevant) > MAX_RELEVANT:
        return f"more than {MAX_RELEVANT} dragons involved"
    real: list[Leaf] = []
    s = w.sub.copy()
    s.path = (act,)
    w.act(s, r, y, act, REAL)
    after = s.copy()
    try:
        w.run(s, r, y, REAL, real)
    except Unknown:
        return "actual line left the replay"

    (real_leaf,) = real
    w.real_values = dict(real_leaf.values)
    chosen = real_leaf
    chosen.score = w.score(real_leaf, REAL)
    if horizon > 1 and y in after.bodies:
        more: list[Leaf] = []
        try:
            w.run(after, r, y, CHOSEN, more)
        except Unknown:
            pass
        for leaf in more:
            leaf.score = w.score(leaf, CHOSEN)
            if leaf.score > chosen.score:
                chosen = leaf
    best = None
    for d in range(4):
        if act == ("move", (d,)):
            continue
        s = w.sub.copy()
        s.path = (("move", (d,)),)
        s.ghosts = w.ghosts
        leaves: list[Leaf] = []
        try:
            w.act(s, r, y, ("move", (d,)), ALT)
            if w.threatened(s, True):
                continue
            w.run(s, r, y, ALT, leaves)
        except Unknown:
            continue
        for leaf in leaves:
            leaf.score = w.score(leaf, ALT)
            if best is None or leaf.score > best.score:
                best = leaf
    if best is None:
        return "no alternative could be played out"
    verdict = Verdict(best.score - chosen.score, chosen, best)
    # Pearls only (the chosen line lives through the horizon): the actual line also gets the pearls it really ate
    # past the horizon, up to `window` rounds after the move, and each pearl of the better line that a teammate
    # really ate in that time (the team got it anyway). A line that died in the horizon eats nothing more.
    if chosen.alive and not chosen.doomed and best.eaten > chosen.eaten and verdict.regret >= min_regret:
        ate = window_ate(game, r, y, window)
        took = teammate_ate(game, r, y, best.bites, window)
        later = real_leaf.score + ate - real_leaf.eaten + took
        if later > chosen.score:
            verdict.horizon_regret, verdict.window_ate, verdict.teammate_ate = verdict.regret, ate, took
            verdict.regret = best.score - later
    return verdict


# Why the window withdrew a verdict: its own later pearls alone would have, or it needed a teammate's.
def withdrawn_reason(v: Verdict, min_regret: float) -> str:
    return "a teammate ate it" if v.regret + (v.teammate_ate or 0) >= min_regret else "ate it later"


# ---------------------------------------------------------------- describing a flagged move


def bfs_free(g: Grid, st: State, start: int, depth: int) -> dict[int, int]:
    dist, edge = {start: 0}, [start]
    for k in range(1, depth + 1):
        nxt_edge = []
        for t in edge:
            for n in g.nxt[t * 4:t * 4 + 4]:
                if n >= 0 and n not in dist and n not in st.occ:
                    dist[n] = k
                    nxt_edge.append(n)
        edge = nxt_edge
    return dist


# Distance to `goal` from every tile within `depth` steps of it, bodies ignored: a lower bound for any dragon.
def bfs_to(g: Grid, goal: int, depth: int) -> dict[int, int]:
    dist, edge = {goal: 0}, [goal]
    for k in range(1, depth + 1):
        grown = []
        for t in edge:
            for p in g.rev[t]:
                if p not in dist:
                    dist[p] = k
                    grown.append(p)
        edge = grown
    return dist


def features(game: Game, st: State, r: int, y: int, act: Action) -> dict[str, Any]:
    g, team = game.grid, game.teams[y]
    body = st.bodies[y]
    head, n = body[0], len(body)
    enemy = min(((g.cheb(head, b[0]), len(b)) for i, b in st.bodies.items() if game.teams[i] != team), default=None)
    allies = sum(1 for i, b in st.bodies.items() if i != y and game.teams[i] == team and g.cheb(head, b[0]) <= 2)
    near = bfs_free(g, st, head, 3)
    pearls = sum(1 for t in near if t in st.pearls)
    free = sum(1 for d in range(4) if g.nxt[head * 4 + d] >= 0 and g.nxt[head * 4 + d] not in st.occ)
    if enemy is None or enemy[0] > 3:
        enemy_text = "no enemy head in view"
    else:
        rel = "longer" if enemy[1] > n else "equal" if enemy[1] == n else "shorter"
        enemy_text = f"enemy head {min(enemy[0], 3) if enemy[0] > 1 else '<=1'} away, {rel}"
    return {
        "enemy": enemy_text,
        "allies_within_2": "2+" if allies >= 2 else str(allies),
        "pearls_within_3": "2+" if pearls >= 2 else str(pearls),
        "free_neighbours": str(free),
        "length": "2" if n == 2 else "3-4" if n <= 4 else "5-9" if n <= 9 else "10-19" if n <= 19 else "20+",
        "phase": phase(r),
        "map": game.info.name,
        "sprint": act[0] == "move" and len(act[1]) > 1,
    }


def walked_from_pearl(game: Game, st: State, r: int, y: int, act: Action) -> str | None:
    g, team = game.grid, game.teams[y]
    head = st.bodies[y][0]
    if act[0] != "move" or any(game.teams[i] != team and g.cheb(head, b[0]) <= 3 for i, b in st.bodies.items()):
        return None
    after = st.copy()
    apply(after, g.nxt, y, act, r, None, False)
    if y not in after.bodies or after.eaten.get(y):
        return None
    before = bfs_free(g, st, head, 6)
    now = bfs_free(g, after, after.bodies[y][0], 7)
    seen = [p for p in st.pearls if g.cheb(head, p) <= 3 and p in before]
    if any(now.get(p, 99) < before[p] for p in seen):
        return None
    heads = [b[0] for i, b in st.bodies.items() if i != y]
    for p in sorted(seen, key=lambda p: before[p]):
        if before[p] > 3 or now.get(p, 99) <= before[p]:
            continue
        rivals = bfs_to(g, p, before[p])
        if not any(h in rivals for h in heads):
            x, yy = g.point(p)
            return f"pearl at {x},{yy} {before[p]} steps away, {now.get(p, '>7')} after the move"
    return None


def death_text(game: Game, y: int, death: tuple[str, int | None, int, int] | None) -> str:
    if death is None:
        return "survived"
    reason, other, _, turn = death
    team = game.teams[y]
    if reason == "hitWall":
        return "died: kelp"
    if reason == "hitSelf":
        return "died: own body"
    if reason == "hitOtherBody":
        return f"died: {'teammate' if game.teams.get(other) == team else 'enemy'} body ({other})"
    if reason == "hitHeadToHead":
        who = "teammate" if game.teams.get(other) == team else "enemy"
        return f"died: head-on with {who} {other} ({'we moved in' if turn == y else 'it moved in'})"
    return "died: no valid action"


def act_text(act: Action) -> str:
    if act[0] == "move":
        return "".join(DIRS[d] for d in act[1])
    return f"split {act[1]}" if act[0] == "split" else str(act[1])


def describe(game: Game, full: State, r: int, y: int, v: Verdict, circling: set[tuple[int, int]],
             window: int = WINDOW) -> dict[str, Any]:
    act = game.action(r, y)
    assert act is not None
    g = game.grid
    labels: list[str] = []
    feats = features(game, full, r, y, act)
    ch, best = v.chosen, v.best
    assert ch is not None and best is not None
    early = ch.death is not None and (ch.death[2] == r or (ch.death[2] == r + 1 and ch.death[3] < y))
    kind = death_text(game, y, ch.death)
    if ch.death:
        kind += f" in r{ch.death[2]}"
    elif ch.doomed:
        kind = "trapped: every move after it is fatal"
    if early and best.alive and not best.doomed:
        labels.append("died_with_safe_move")
    real_death = next((d for d in game.deaths if d["id"] == y and d["round"] == r and ch.death
                       and ch.death[3] == y), None)  # died on this very move
    if real_death and real_death.get("movedIntoTeam") and real_death["movedIntoTeam"] != game.teams[y]:
        other = real_death["movedIntoLength"]
        labels.append("rammed_longer" if other > real_death["length"] else "rammed_shorter_or_equal")
        kind += f" (enemy length {other} vs our {real_death['length']})"
    if real_death and real_death.get("cause"):
        kind += f" ({real_death['cause']})"
    pearl = walked_from_pearl(game, full, r, y, act)
    if pearl:
        labels.append("walked_from_visible_pearl")
    if (r, y) in circling and feats["enemy"] == "no enemy head in view":
        labels.append("circling")
    if not ch.alive or ch.doomed:
        cause = "death"
    elif best.eaten > ch.eaten:
        cause = "pearls"
    elif best.paid < ch.paid:
        cause = "sprint cost"
    else:
        cause = "other dragons"
    head = g.point(full.bodies[y][0])
    line = (f"at {head[0]},{head[1]} length {len(full.bodies[y])}: chose {'/'.join(act_text(a) for a in ch.path)} -> {kind}"
            f"{', ate ' + str(ch.eaten) if ch.eaten else ''}{', paid ' + str(ch.paid) if ch.paid else ''}; "
            f"better {'/'.join(act_text(a) for a in best.path)} -> "
            f"{'survives' if best.alive and not best.doomed else 'dies'}"
            f"{', ate ' + str(best.eaten) if best.eaten else ''}")
    if pearl:
        line += f"; {pearl}"
    if v.horizon_regret is not None:
        line += (f"; really ate {v.window_ate} by r{r + window}"
                 + (f", a teammate ate {v.teammate_ate} of its pearls" if v.teammate_ate else "")
                 + f", regret {v.horizon_regret:.0f} at the horizon")
    lo, hi = max(0, r - 5), r + 2
    out = {
        "file": str(game.path), "round": r, "id": y, "regret": round(v.regret, 2), "cause": cause, "labels": labels,
        "features": feats, "chosen": act_text(act), "better": act_text(best.path[0]), "text": line,
        "watch": [f"uv run python tools/watch.py dragon {game.path} --id {y} --rounds {lo}-{hi}",
                  f"uv run python tools/watch.py frames {game.path} --rounds {r}-{r + 1} --around {y}"],
    }
    if v.horizon_regret is not None:  # the window lowered it, not below --min-regret
        out["horizonRegret"], out["windowAte"] = round(v.horizon_regret, 2), v.window_ate
        out["teammateAte"] = v.teammate_ate
    return out


# ---------------------------------------------------------------- a whole game


@dataclass
class Report:
    file: str
    map: str
    rounds: int
    moves: int = 0
    judged: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    agreed: int = 0
    checked: int = 0
    disagreements: list[str] = field(default_factory=list)
    flagged: list[dict[str, Any]] = field(default_factory=list)
    withdrawn: list[dict[str, Any]] = field(default_factory=list)  # pearl regrets the window took below the bar
    seconds: float = 0.0
    countdowns: int = 0  # pearlCountdown events; 0 undercounts its pearl regrets (see the module docstring)


# Dragons whose death pearls a teammate ate within FEED_ROUNDS: our bot's merges and folds die on purpose beside
# a teammate so it grows. That trade is strategy (the round-500 tiebreak is the longest dragon), not a tactical
# mistake, so such a death isn't flagged unless an enemy caused it.
FEED_ROUNDS = 10


def fed_teammates(game: Game, log: dict[int, list[tuple[str, int, int]]]) -> set[int]:
    drops: dict[int, tuple[int, int]] = {}  # tile -> (round, dragon), latest
    fed = set()
    for r in sorted(log):
        for kind, tile, i in log[r]:
            if kind == "drop":
                drops[tile] = (r, i)
            elif tile in drops:
                dr, d = drops.pop(tile)
                if r - dr <= FEED_ROUNDS and d != i and game.teams[d] == game.teams[i]:
                    fed.add(d)
    return fed


# A dragon that turns back into its own neck while it had a free tile to go to meant to die: no safety check
# picks its own neck. Our bot does it to fold into the king late in the game.
def into_neck(game: Game, st: State, i: int, act: Action | None) -> bool:
    body = st.bodies[i]
    return (act is not None and act[0] == "move" and len(body) >= 2
            and game.grid.nxt[body[0] * 4 + act[1][0]] == body[1])


def circling_moves(game: Game) -> set[tuple[int, int]]:
    heads: dict[int, list[tuple[int, watch.Point]]] = defaultdict(list)
    for r in sorted(game.frames):
        for i, (_, b) in game.frames[r].dragons.items():
            heads[i].append((r, b[0]))
    out = set()
    for i, hs in heads.items():
        if game.teams[i] == game.team:
            out.update((r, i) for (r, _), f in zip(hs, watch.circling_flags(game.turns, i, hs)) if f)
    return out


def analyse(path: Path, team: str, horizon: int, min_regret: float, window: int = WINDOW) -> Report:
    started = time.monotonic()
    game = load(path, team)
    rep = Report(str(path), game.info.name, game.last_round + 1, countdowns=game.countdowns)
    circling = circling_moves(game)
    pending: list[tuple[int, int, Verdict, State]] = []
    bad: set[int] = set()
    nxt = game.grid.nxt
    log: dict[int, list[tuple[str, int, int]]] = {}
    for r in range(game.last_round + 1):
        st = state_at(game, r)
        st.log = log[r] = []
        st.spawn(game.attempts.get(r, ()))
        order = sorted(st.bodies)
        k = 0
        try:
            while k < len(order):
                i = order[k]
                k += 1
                if i not in st.bodies:
                    continue
                act = game.action(r, i)
                if act is None:
                    raise Mismatch(f"round {r}: dragon {i} alive but took no action")
                if game.teams[i] == team:
                    rep.moves += 1
                    try:
                        v = judge(game, st, r, i, horizon, min_regret, window)
                    except Mismatch:
                        v = "actual line did not reproduce the replay"
                    if isinstance(v, str):
                        rep.skipped[v] += 1
                    else:
                        rep.judged += 1
                        if max(v.regret, v.horizon_regret or 0.0) >= min_regret:
                            pending.append((r, i, v, st.copy()))
                t = game.teams[i]
                count = sum(1 for j in st.bodies if game.teams[j] == t)
                apply(st, nxt, i, act, r, game.births.get((r, i)), count < UNIT_LIMIT)
                if act[0] == "split" and (r, i) in game.births:
                    bisect.insort(order, game.births[(r, i)][0])
            rep.checked += 1
            want = game.bodies(r + 1)
            got = {i: tuple(b) for i, b in st.bodies.items()}
            if got != want or st.pearls != game.pearls(r + 1):
                raise Mismatch(f"round {r}: end state differs from the replay")
            rep.agreed += 1
        except (Mismatch, Unknown) as error:
            bad.add(r)
            if len(rep.disagreements) < 5:
                rep.disagreements.append(str(error) or f"round {r}")
    fed = fed_teammates(game, log)
    for r, i, v, st in pending:
        if any(t in bad for t in range(r, r + horizon + 1)):
            rep.skipped["round disagreed with the replay"] += 1
            continue
        death = v.chosen.death if v.chosen else None
        if death and i in fed and not (death[1] is not None and game.teams.get(death[1]) != team):
            rep.skipped["died feeding a teammate"] += 1
            continue
        if death and death[0] == "hitSelf" and death[2:] == (r, i) and into_neck(game, st, i, game.action(r, i)):
            rep.skipped["died on purpose (turned into its own neck)"] += 1
            continue
        if v.regret < min_regret:
            assert v.horizon_regret is not None
            rep.withdrawn.append({"file": str(path), "round": r, "id": i, "reason": withdrawn_reason(v, min_regret),
                                  "horizonRegret": round(v.horizon_regret, 2), "regret": round(v.regret, 2),
                                  "windowAte": v.window_ate, "teammateAte": v.teammate_ate})
            continue
        rep.flagged.append(describe(game, st, r, i, v, circling, window))
        if not game.countdowns:
            rep.flagged[-1]["labels"].append("no_countdowns")
    rep.seconds = time.monotonic() - started
    return rep


# ---------------------------------------------------------------- output


def summary_lines(reports: list[Report], worst: int, team: str, horizon: int, window: int = WINDOW) -> list[str]:
    games = len(reports)
    flagged = [f for rep in reports for f in rep.flagged]
    withdrawn = [f for rep in reports for f in rep.withdrawn]
    own = [f for f in withdrawn if f["reason"] == "ate it later"]
    mate = [f for f in withdrawn if f["reason"] == "a teammate ate it"]
    lowered = [f for f in flagged if "horizonRegret" in f]
    moves = sum(r.moves for r in reports)
    judged = sum(r.judged for r in reports)
    checked, agreed = sum(r.checked for r in reports), sum(r.agreed for r in reports)
    skipped: Counter[str] = Counter()
    for rep in reports:
        skipped.update(rep.skipped)
    secs = [r.seconds for r in reports]
    blind = [Path(r.file).name for r in reports if not r.countdowns]
    out = [f"WARNING: {len(blind)} of {games} games have no pearlCountdown events (pearl timings withheld?): their "
           f"spawns are the ones the replay shows, so their pearl regrets are undercounted; their flagged moves are "
           f"labelled no_countdowns: {', '.join(blind[:5])}{' ...' if len(blind) > 5 else ''}"] if blind else []
    out += [f"{games} games, team {team}, horizon {horizon}: {moves} moves, {judged} judged, {len(flagged)} flagged "
           f"(total regret {sum(f['regret'] for f in flagged):.0f})",
           f"rules check: {agreed}/{checked} rounds reproduced exactly"
           + (f" ({100 * agreed / checked:.2f}%)" if checked else ""),
           "not judged: " + (", ".join(f"{k} {n}" for k, n in skipped.most_common()) or "none"),
           f"pearl regret withdrawn within {window} rounds after the move: ate it later {len(own)} moves "
           f"(regret {sum(f['horizonRegret'] for f in own):.0f}), a teammate ate it {len(mate)} "
           f"(regret {sum(f['horizonRegret'] for f in mate):.0f}); lowered but still flagged: {len(lowered)}",
           f"time: {sum(secs):.1f}s, {sum(secs) / max(games, 1):.2f}s per game, slowest {max(secs, default=0):.1f}s",
           ""]
    names = ["all flagged"] + [f"cause: {c}" for c in sorted({f["cause"] for f in flagged})] + list(LABELS)
    out.append(f"{'kind':32s} {'total':>6s} {'/game':>6s} {'regret':>7s} " + " ".join(f"{p:>9s}" for _, _, p in PHASES))
    for name in names:
        if name == "all flagged":
            rows = flagged
        elif name.startswith("cause: "):
            rows = [f for f in flagged if f["cause"] == name[7:]]
        else:
            rows = [f for f in flagged if name in f["labels"]]
        by_phase = Counter(phase(f["round"]) for f in rows)
        out.append(f"{name:32s} {len(rows):6d} {len(rows) / max(games, 1):6.2f} {sum(f['regret'] for f in rows):7.0f} "
                   + " ".join(f"{by_phase[p]:9d}" for _, _, p in PHASES))
    out.append("")
    out.append("largest groups by total regret (why, nearest enemy head, pearls within 3, free neighbours, sprint):")
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for f in flagged:
        x = f["features"]
        groups[(f["cause"], x["enemy"], x["pearls_within_3"], x["free_neighbours"], x["sprint"])].append(f)
    for key, rows in sorted(groups.items(), key=lambda kv: -sum(f["regret"] for f in kv[1]))[:12]:
        eg = max(rows, key=lambda f: f["regret"])
        out.append(f"  {sum(f['regret'] for f in rows):6.0f} regret, {len(rows):4d} moves: {key[0]}; {key[1]}; "
                   f"{key[2]} pearls; {key[3]} free; {'sprint' if key[4] else 'step'}  "
                   f"e.g. {Path(eg['file']).name} r{eg['round']} id {eg['id']}")
    for feat in ("length", "allies_within_2", "phase", "map"):
        tally: dict[str, list[float]] = defaultdict(list)
        for f in flagged:
            tally[str(f["features"][feat])].append(f["regret"])
        cells = sorted(tally.items(), key=lambda kv: -sum(kv[1]))[:8]
        out.append(f"  by {feat}: " + ", ".join(f"{k} {sum(v):.0f} ({len(v)})" for k, v in cells))
    out.append("")
    out.append(f"{min(worst, len(flagged))} worst:")
    for f in sorted(flagged, key=lambda f: -f["regret"])[:worst]:
        out.append(f"  {Path(f['file']).name} r{f['round']} id {f['id']} regret {f['regret']:.0f} [{f['cause']}"
                   f"{'; ' + ', '.join(f['labels']) if f['labels'] else ''}] {f['text']}")
        out.append(f"      {f['watch'][0]}")
        out.append(f"      {f['watch'][1]}")
    bad = [(Path(r.file).name, r.disagreements) for r in reports if r.disagreements]
    if bad:
        out.append("")
        out.append("rounds the rules didn't reproduce (first few per game):")
        for name, msgs in bad[:10]:
            out.append(f"  {name}: " + "; ".join(msgs))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replays", type=Path, nargs="+")
    ap.add_argument("--team", choices=replays.TEAMS, required=True, help="our side in every replay given")
    ap.add_argument("--horizon", type=int, default=2, choices=(1, 2, 3), help="our dragon's turns played forward")
    ap.add_argument("--min-regret", type=float, default=1.0)
    ap.add_argument("--window", type=int, default=WINDOW,
                    help="rounds after the move in which the pearls our dragon really ate count against a regret "
                         "that is only pearls (0: judge pearls at the horizon only)")
    ap.add_argument("--worst", type=int, default=20)
    ap.add_argument("--json", type=Path, help="write every flagged move and per-game stats here")
    args = ap.parse_args()
    reports = []
    for path in args.replays:
        try:
            reports.append(analyse(path, args.team, args.horizon, args.min_regret, args.window))
        except Exception as error:  # one broken replay must not stop the rest
            print(f"{path}: unreadable: {type(error).__name__}: {error}", file=sys.stderr)
    print("\n".join(summary_lines(reports, args.worst, args.team, args.horizon, args.window)))
    if note := replays.chosen_note(args.replays):
        print(note)
    if args.json:
        args.json.write_text(json.dumps({
            "team": args.team, "horizon": args.horizon, "window": args.window, "minRegret": args.min_regret,
            "deathWeight": DEATH,
            "games": [{"file": r.file, "map": r.map, "rounds": r.rounds, "moves": r.moves, "judged": r.judged,
                       "skipped": dict(r.skipped), "roundsChecked": r.checked, "roundsAgreed": r.agreed,
                       "disagreements": r.disagreements, "flagged": len(r.flagged),
                       "withdrawn": dict(Counter(f["reason"] for f in r.withdrawn)),
                       "seconds": round(r.seconds, 2), "countdowns": r.countdowns}
                      for r in reports],
            "flagged": [f for r in reports for f in r.flagged],
            "withdrawn": [f for r in reports for f in r.withdrawn],
        }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
