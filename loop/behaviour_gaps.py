"""Behaviour gaps (REFLECT 2026-09-29 14:29 F3): how often we take each action the engine offers, beside how often the
ladder's ranks 1-2 do, each as a share of the turns that offered it, so a constant that keeps our dragons off an
action shows up before sig sees it. The miss: scoring.hpp's unknownPortalPenalty (5e5, a 23 Sep placeholder) kept us
off portals no teammate had seen through (1-22% of the turns beside one, the leaders 33-100%, their crossings safe),
and nothing compared our action rates with the strong teams'.

    .venv/bin/python loop/behaviour_gaps.py [--budget N]   # writes <loop home>/behaviour-gaps.md

Games (the replay library, loop/replay_library.py): our latest OURS_MAX result-blind non-dev games, and each rank 1-2
team's (the latest study-history.jsonl row's, by team id) latest LEADER_MAX ranked games (their unranked ones may be
decoys). Each row of the report is one action and the turn that offered it (ROWS, `Actions` reads them from the
replay's moves):

- portal crossing: a turn starting beside a portal (the step not onto our own neck), by whether any teammate's view
  at a turn's start has held the far portal's edge; a hit is the move's first step going through one such portal;
- sprint: a turn at length 3+ (a 2-step sprint is payable), by whether an enemy head is in the 7x7 view; a hit is a
  move of 2+ steps;
- split: a turn at length 4+ with the team under the 64-dragon cap, by length; a hit is a split that happened;
- ram: a turn with an enemy head within RAM_REACH steps (Manhattan), by that head's length less ours (the nearest,
  longest first); a hit is the move killing an enemy head-on (tools/watch.py rams' event).

What followed: the share of hits whose dragon (a split's child) died within replays.CROSSING_WINDOW rounds; none for
a ram, where both heads die. What it paid (method M100: the leaders' rams on heads 2+ shorter were flagged for us to
copy while each of their rams lost about a segment): a ram's M51 swing (tools/watch.py Ram.swing: the victim's length
less its team's pickups of the two drops, less our length before the move less ours), and for the other rows the
mover's length PAYOFF_ROUNDS rounds on (its split child's added, 0 when dead) less its length at the turn, on the
turns that acted less on those that didn't. A gap is not flagged where the leader's payoff is clearly below ours, or
for a ram clearly below 0 (unflag_reason: one-sided p <= chance.TRIGGER_P, clustered by game, on PAYOFF_TURNS+ acting
turns, so a payoff on a handful of turns clears nothing), so a review is never asked to copy an action that cost the
team taking it; the row says why. Each leader's gap to us is map-matched and game-clustered, p from shuffling whose
games are whose within each map (chance.strata_gap), with n the chances and games; a gap is flagged "needs an idea or a
stated reason" at p <= chance.TRIGGER_P over all the report's looks (Bonferroni, as chance.corrected_z), so luck alone
flags any row at most TRIGGER_P of the time, and only when one team's odds of taking the action are FLAG_ODDS times
the other's. Each flag names the ideas at work citing its action (citing), so a review answers a covered one by id.
Our games are mostly unranked sweeps against rotating opponents and the leaders' ranked, so the header says how many
and a column gives our rate in our ranked games alone.

Files: behaviour-gaps.md, about 7 KB (one line a row, rewritten whole); behaviour-gaps.json, each selected game's
counts (about 1.5 KB a game, at most OURS_MAX + 2 x LEADER_MAX games, so under 1 MB), pruned to the selection at each
run and reset when this file or tools/replays.py changes. The upkeep runs main() right after its replay library step
(loop/maintenance.py). A run reads at most `budget` new replays (about 1-2 s each), so the first run after a reset
takes several upkeep ticks, says how many games it has yet to read and flags nothing until all are read. A replay that
fails to read is retried at later runs, READ_TRIES reads in all, and counted as unreadable meanwhile.
health.behaviour_gaps alerts when the report falls behind the library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import chance  # noqa: E402
import discord_teams  # noqa: E402
import maintenance  # noqa: E402
import replays  # noqa: E402
from home import loop_home  # noqa: E402
from replay_library import LIBRARY  # noqa: E402

OURS_MAX = 300  # our latest result-blind non-dev games read
LEADER_MAX = 150  # each rank 1-2 team's latest ranked games read
BUDGET = 60  # new replays a run reads at most
READ_TRIES = 3  # reads of a replay that fails (a truncated download may be fetched again) before its error stands
REPORT = Path("behaviour-gaps.md")
CACHE = Path("behaviour-gaps.json")
UNSEEN, SEEN = "portal crossing | far side no teammate has seen", "portal crossing | far side a teammate has seen"
SPRINT_VIEW, SPRINT_BLIND = "sprint | length 3+, an enemy head in view", "sprint | length 3+, no enemy head in view"
SPLIT_BANDS = ((4, 5), (6, 10), (11, 19), (20, 10**6))
SPLITS = tuple(f"split | length {lo}-{hi}" if hi < 10**6 else f"split | length {lo}+" for lo, hi in SPLIT_BANDS)
RAM_REACH = 3  # an enemy head this near (Manhattan) is a ram's chance: it held 161 of 168 rams in 5 test replays (2: 142)
RAM_LONGER, RAM_LEVEL, RAM_SHORTER = ("ram | enemy head within 3, 2+ longer than ours",
                                      "ram | enemy head within 3, within 1 of our length",
                                      "ram | enemy head within 3, 2+ shorter than ours")
ROWS = (UNSEEN, SEEN, SPRINT_VIEW, SPRINT_BLIND, *SPLITS, RAM_LONGER, RAM_LEVEL, RAM_SHORTER)
NO_AFTERMATH = (RAM_LONGER, RAM_LEVEL, RAM_SHORTER)
# A flagged gap also has this odds ratio or more between the teams' map-matched rates (the odds of acting, r / (1 - r),
# one team's over the other's; about the rate ratio for rare actions, fair to common ones): with hundreds of thousands
# of turns a row, gaps of a fifth are past luck too, and a style detail rather than a missing behaviour (the first run
# flagged 16 rows at p alone).
FLAG_ODDS = 2
PAYOFF_ROUNDS = 10  # the other rows' payoff: length this many rounds after the turn
PAYOFF_TURNS = 30  # acting turns a payoff needs before it can clear a gap's flag (unflag_reason)
# Each action's words in an idea's title that, beside "behaviour gaps" in it or its hypothesis, make it cite the action
# (citing): word starts, since "ram" is inside "param" and "program".
CITES = {"portal crossing": r"\bportal", "sprint": r"\bsprint", "split": r"\bsplit",
         "ram": r"\bram(s|med|ming)?\b|\bhead-?ons?\b"}


# Each portal edge's partner (the edge sharing its id), from the .map text's EDGE lines (docs/unsw/map-files.md).
def partners(info: replays.MapInfo, text: str) -> dict:
    by_id: dict[int, list] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[0] == "EDGE" and parts[2] == "2":
            if (edge := replays.edge_key(info.width, info.height, int(parts[1]))) is not None:
                by_id.setdefault(int(parts[3]), []).append(edge)
    pairs = {}
    for edges in by_id.values():
        if len(edges) == 2:
            pairs[edges[0]], pairs[edges[1]] = edges[1], edges[0]
    return pairs


# Walks one replay (replays._Walker) and counts, for `side`'s dragons, each ROWS row's chances and hits and the hits
# whose dragon died within replays.CROSSING_WINDOW rounds, with the payoff sums (the docstring's What it paid): `rows`
# after finish(), {row: [chances, hits, died, hits' payoff sum, hits paid, other turns' payoff sum, other turns paid]}.
class Actions(replays._Walker):
    def __init__(self, info: replays.MapInfo, text: str, side: str) -> None:
        super().__init__(info)
        self.side = side
        self.partner = partners(info, text)
        self.beside: dict = {}  # tile -> [(direction, portal edge)] on its sides
        for tile_edge in self.partner:
            orientation, x, y = tile_edge
            if orientation == "v":  # between (x - 1, y) and (x, y)
                sides = (((x - 1) % self.width, y), "east"), ((x, y), "west")
            else:  # between (x, y - 1) and (x, y)
                sides = ((x, (y - 1) % self.height), "south"), ((x, y), "north")
            for tile, direction in sides:
                self.beside.setdefault(tile, []).append((direction, tile_edge))
        self.seen: set = set()  # portal edges beside a tile some head of `side` had in view at its turn's start
        self.living = {team: {i for i, d in self.dragons.items() if d.team == team} for team in replays.TEAMS}
        self.rows = {row: [0] * 7 for row in ROWS}
        self.open: dict[str, set[str] | None] = {}  # this turn's rows: a portal row's directions, else None
        self.hit: set[str] = set()
        self.after: list[tuple[str, int, int]] = []  # (row, round, dragon id) of each hit whose aftermath counts
        self.turn: tuple[list[str], list[int], int, int] | None = None  # paid rows, its dragons, start length, due
        self.pending: deque[tuple[int, list[str], set[str], list[int], int]] = deque()  # (due, rows, hits, ids, start)
        self.ram_hits: list[tuple[str, int, int, int, int]] = []  # (row, round, rammer, victim's death index, start)

    # Where a step `direction` through the portal edge `edge` comes out: the partner's far side, same direction.
    def landing(self, edge, direction: str):
        orientation, x, y = self.partner[edge]
        if orientation == "v":
            return (x % self.width, y) if direction == "east" else ((x - 1) % self.width, y)
        return (x, y % self.height) if direction == "south" else (x, (y - 1) % self.height)

    def _start_turn(self, dragon_id: int) -> None:
        self._close_turn()
        super()._start_turn(dragon_id)
        self.open, self.hit = {}, set()
        dragon = self.mover
        if dragon.team != self.side or not dragon.body:
            return
        head, length = dragon.body[0], len(dragon.body)
        neck = dragon.body[1] if length > 1 else None
        for tile in self._view(head):
            self.seen.update(edge for _, edge in self.beside.get(tile, ()))
        for direction, edge in self.beside.get(head, ()):
            if self.landing(edge, direction) != neck:
                self.open.setdefault(SEEN if self.partner[edge] in self.seen else UNSEEN, set()).add(direction)
        enemies = [self.dragons[i] for i in self.living["B" if self.side == "A" else "A"]]
        if length >= 3:
            in_view = any(self._chebyshev(head, e.body[0]) <= replays.VISION for e in enemies if e.body)
            self.open[SPRINT_VIEW if in_view else SPRINT_BLIND] = None
        if length >= 4 and len(self.living[self.side]) < replays.UNIT_LIMIT:
            self.open[next(row for row, (lo, hi) in zip(SPLITS, SPLIT_BANDS) if lo <= length <= hi)] = None
        near = [(self._manhattan(head, e.body[0]), -len(e.body)) for e in enemies if e.body]
        if near and (closest := min(near))[0] <= RAM_REACH:
            gain = -closest[1] - length
            self.open[RAM_LONGER if gain >= 2 else RAM_SHORTER if gain <= -2 else RAM_LEVEL] = None
        for row in self.open:
            self.rows[row][0] += 1
        self.turn = ([row for row in self.open if row not in NO_AFTERMATH], [dragon.id], length,
                     self.round + PAYOFF_ROUNDS)

    # Queues the finished turn's payoff, read PAYOFF_ROUNDS rounds on (on_event).
    def _close_turn(self) -> None:
        if self.turn and self.turn[0]:
            rows, ids, start, due = self.turn
            self.pending.append((due, rows, set(self.hit), ids, start))
        self.turn = None

    def on_event(self, event) -> None:
        super().on_event(event)
        if event.which() == "roundStart":
            while self.pending and self.pending[0][0] <= self.round:
                _, rows, hits, ids, start = self.pending.popleft()
                gain = sum(len(self.dragons[i].body) for i in ids if self.dragons[i].alive) - start
                for row in rows:
                    at = 3 if row in hits else 5
                    self.rows[row][at] += gain
                    self.rows[row][at + 1] += 1

    # Files a hit on `row` if this turn offered it, once a turn; `dragon_id` is whose death within the window counts.
    def _hit(self, row: str, dragon_id: int | None) -> None:
        if row in self.open and row not in self.hit:
            self.hit.add(row)
            self.rows[row][1] += 1
            if dragon_id is not None:
                self.after.append((row, self.round, dragon_id))

    def _note_crossing(self, dragon, tile, direction: str) -> None:
        if self.step_index == 0 and dragon is self.mover:
            for row in (UNSEEN, SEEN):
                if direction in (self.open.get(row) or ()):
                    self._hit(row, dragon.id)
        super()._note_crossing(dragon, tile, direction)

    def _on_action(self, action) -> None:
        super()._on_action(action)
        if self.action == "move" and len(self.steps) > 1:
            for row in (SPRINT_VIEW, SPRINT_BLIND):
                self._hit(row, self.mover.id)

    def _on_split(self, split) -> None:
        super()._on_split(split)
        self.living[str(split.team).upper()].add(split.childId)
        if self.mover is not None and split.parentId == self.mover.id:
            if self.turn:
                self.turn[1].append(split.childId)
            for row in SPLITS:
                self._hit(row, split.childId)

    def _on_death(self, death) -> None:
        super()._on_death(death)
        record = self.deaths[-1]
        self.living[record["team"]].discard(record["id"])
        if self.mover is not None and record.get("hitBy") == self.mover.id and record["team"] != self.mover.team:
            for row in NO_AFTERMATH:
                if row in self.open and row not in self.hit and self.turn:
                    self.ram_hits.append((row, self.round, self.mover.id, len(self.deaths) - 1, self.turn[2]))
                self._hit(row, None)

    def finish(self) -> None:
        self._close_turn()
        super().finish()
        for row, round_, rammer, victim, start in self.ram_hits:  # M51's swing (watch.Ram.swing)
            ours = next((k for k, d in enumerate(self.deaths) if d["id"] == rammer and d["round"] == round_), None)
            drops = {victim, ours} - {None}
            mine = sum(m["death"] in drops and m["team"] == self.side for m in self.meals)
            theirs = sum(m["death"] in drops and m["team"] != self.side for m in self.meals)
            self.rows[row][3] += (self.deaths[victim]["length"] - theirs) - (start - mine)
            self.rows[row][4] += 1
        died = {d["id"]: d["round"] for d in self.deaths}
        for row, round_, dragon_id in self.after:
            if dragon_id in died and 0 <= died[dragon_id] - round_ <= replays.CROSSING_WINDOW:
                self.rows[row][2] += 1


# One game's counts for `side`: {"map", "rows"}, or {"error"} when the replay can't be read or its tracking fails.
def game_counts(path: Path, side: str) -> dict:
    try:
        replay = replays.load_replay(path)
        info = replays.parse_map(replay.map)
        walker = Actions(info, replay.map, side)
        for event in replay.events:
            walker.on_event(event)
        walker.finish()
    except Exception as error:  # a corrupt or truncated download: main retries it at later runs, READ_TRIES in all
        return {"error": f"{type(error).__name__}: {error}"[:200]}
    if walker.checks.problems:
        return {"error": f"tracking failed: {walker.checks.problems[0]}"[:200]}
    return {"map": info.name, "rows": {row: counts for row, counts in walker.rows.items() if counts[0]}}


# The rank 1-2 teams of the latest study-history.jsonl row, as {team id: its current name}, rank order: a team is its
# id (MISSES 75), its name the latest leaderboard read's (discord_teams.current_name), else the study's.
def leaders(state: Path) -> dict[int, str]:
    rows = maintenance.read_jsonl(state / "study-history.jsonl")
    top = [t for t in (rows[-1].get("leaders") or [] if rows else []) if t.get("rank") in (1, 2)]
    teams = discord_teams.read(state)
    return {int(t["id"]): str(discord_teams.current_name(teams, t["id"], t.get("name")))
            for t in sorted(top, key=lambda t: t["rank"])}


# The games each group reads, {group: [library row]}, latest last: "us", then each leader's team id.
def selection(state: Path, teams: dict[int, str]) -> dict[str | int, list[dict]]:
    games = replays.library_games(path=state / LIBRARY)
    latest = lambda rows, n: sorted(rows, key=lambda g: str(g.get("requested_at") or ""))[-n:]
    chosen: dict[str | int, list[dict]] = {"us": latest([g for g in games if g["part"] == "online"], OURS_MAX)}
    for team_id in teams:
        chosen[team_id] = latest([g for g in games if g["part"] == "leaders" and g.get("team_id") == team_id
                                  and g.get("ranked") is True], LEADER_MAX)
    return chosen


# The live ideas at work (maintenance.working_on: in flight, or waiting at priority 1-3) whose title names a row's
# action and whose title or hypothesis names this report, {action: [idea id]}: a flag lists them, so a review answers
# it once. The title, since a hypothesis quotes other rows as evidence: I950's (sprints) quotes the portal flags.
def citing(ideas: list[dict]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for idea in ideas:
        title = str(idea.get("title", ""))
        if maintenance.working_on(idea) and re.search(r"behaviou?r[ _-]gaps?", f"{title} {idea.get('hypothesis', '')}",
                                                      re.I):
            for action, words in CITES.items():
                if re.search(words, title, re.I):
                    found.setdefault(action, []).append(str(idea.get("id")))
    return found


def analyser() -> str:
    tools = Path(replays.__file__)
    return hashlib.sha256(Path(__file__).read_bytes() + tools.read_bytes()).hexdigest()[:12]


def _pct(rate: float) -> str:
    return f"{100 * rate:.2g}%" if rate < 0.1 else f"{100 * rate:.0f}%"


def _p(p: float) -> str:
    return f"{p:.0e}" if p < 0.01 else f"{p:.2f}"


# The odds ratio of two rates, the larger over the smaller (inf when one is 0 or 1 and the other isn't).
def odds_ratio(a: float, b: float) -> float:
    odds = [r / (1 - r) if 0 < r < 1 else 0.0 if r <= 0 else math.inf for r in (a, b)]
    low, high = sorted(odds)
    return 1.0 if low == high else math.inf if low == 0 or high == math.inf else high / low


# One group's rate cell for `row`: its share of the chances, n, and what followed; `by_map` gathers the games' (hits,
# chances) under `group` for strata_gap when given.
def _cell(row: str, games: list[dict], group: str | int | None = None, by_map: dict | None = None) -> str:
    chances = hits = died = n = 0
    for game in games:
        c, h, d = game["rows"].get(row, (0, 0, 0))[:3]
        chances, hits, died, n = chances + c, hits + h, died + d, n + (c > 0)
        if by_map is not None:
            by_map.setdefault(game["map"], {}).setdefault(group, []).append((h, c))
    after = "" if row in NO_AFTERMATH or not hits else f"; died <={replays.CROSSING_WINDOW}: {_pct(died / hits)}"
    paid = "" if (pay := payoff(row, games)) is None else f"; pays {pay:+.2f}"
    return f"{_pct(hits / chances) if chances else '-'} of {chances:,} ({n} games{after}{paid})"


# What `row`'s action paid a group (the docstring's What it paid): a ram's mean swing, else the acting turns' mean
# length gain PAYOFF_ROUNDS rounds on less the other turns'; None when a side of it has no turns.
def payoff(row: str, games: list[dict]) -> float | None:
    return None if (stat := payoff_stat(row, games)) is None else stat[0]


# payoff with its variance clustered by game (chance.cluster_mean) and the acting turns it rests on, or None.
def payoff_stat(row: str, games: list[dict]) -> tuple[float, float, int] | None:
    rows = [g["rows"].get(row) or [0] * 7 for g in games]
    acted = chance.cluster_mean([(r[3], r[4]) for r in rows])
    if not acted[2]:
        return None
    if row in NO_AFTERMATH:
        return acted
    other = chance.cluster_mean([(r[5], r[6]) for r in rows])
    return None if not other[2] else (acted[0] - other[0], acted[1] + other[1], acted[2])


# One-sided chance that payoff `high` is at least this far above `low` by luck alone (normal tail).
def _above_p(high: tuple[float, float, int], low: tuple[float, float, int]) -> float:
    gap, variance = high[0] - low[0], high[1] + low[1]
    return 1.0 if gap <= 0 else 0.0 if variance == 0 else chance.low_p(-gap / math.sqrt(variance))


# Why a rate gap is not flagged (M100: never copy an action that cost its taker), or "" when it is: the leader's payoff
# clearly below ours (on PAYOFF_TURNS+ of our acting turns, one-sided p <= chance.TRIGGER_P), or for a ram the
# leader's swing clearly below 0 (on PAYOFF_TURNS+ of its rams, the same p). A payoff on fewer turns is a coin flip,
# so it clears nothing.
def unflag_reason(row: str, mine: tuple | None, leader: tuple | None) -> str:
    if leader is None:
        return ""
    if mine is not None and mine[2] >= PAYOFF_TURNS and (p := _above_p(mine, leader)) <= chance.TRIGGER_P:
        return f"theirs pays {leader[0]:+.2f} v our {mine[0]:+.2f} on {mine[2]} turns, p {_p(p)}"
    if row in NO_AFTERMATH and leader[2] >= PAYOFF_TURNS and (p := _above_p((0.0, 0.0, 0), leader)) <= chance.TRIGGER_P:
        return f"their rams swing {leader[0]:+.2f} on {leader[2]}, below 0 at p {_p(p)}"
    return ""


# The report's text from each group's per-game counts ("us", "us ranked", then each leader's team id), the leaders'
# names, the ideas citing each action (cited), what they were read from, and how many selected games are unread:
# while any is, the flags are withheld, since a group read ahead of the others compares uneven samples. A gap is also
# withheld, its reason printed, when unflag_reason finds the leader's action paying clearly worse (payoff_stat).
def report_text(counts: dict[str | int, list[dict]], names: dict[int, str], cited: dict[str, list[str]],
                header: str, now: float, unread: int = 0) -> str:
    looks = len(ROWS) * len(names)
    lines, flagged = [], []
    for row in ROWS:
        action, opportunity = row.split(" | ")
        by_map: dict = {}
        cells = [_cell(row, counts["us"], "us", by_map), _cell(row, counts["us ranked"])]
        for team_id, name in names.items():
            cells.append(_cell(row, counts[team_id], team_id, by_map))
            ours, theirs, p = chance.strata_gap([(m.get("us", []), m.get(team_id, [])) for m in by_map.values()])
            shared = sum(min(sum(1 for g in m.get(t, []) if g[1]) for t in ("us", team_id)) >= chance.STRATUM_GAMES
                         for m in by_map.values())
            gap = not unread and shared and p <= chance.TRIGGER_P / looks and odds_ratio(ours, theirs) >= FLAG_ODDS
            why = gap and unflag_reason(row, payoff_stat(row, counts["us"]), payoff_stat(row, counts[team_id]))
            flag = gap and not why
            mark = " **FLAG**" if flag else f" (a gap, but {why}: not flagged)" if gap else ""
            cells.append(f"{_pct(ours)} v {_pct(theirs)} on {shared} maps, p {_p(p)}{mark}" if shared
                         else "no shared map")
            if flag:
                ideas = ", ".join(cited.get(action, [])) or "none"
                flagged.append(f"- {action}, {opportunity}: us {_pct(ours)} v {name} {_pct(theirs)} on the same "
                               f"{shared} maps (p {_p(p)}). Ideas at work citing it: {ideas}")
        lines.append(f"| {action} | {opportunity} | " + " | ".join(cells) + " |")
    head = ("| Action | Opportunity (a turn with) | Us | Us, ranked games only | "
            + " | ".join(f"{name} | us v {name}, map-matched" for name in names.values())
            + " |\n" + "|---" * (4 + 2 * len(names)) + "|\n")
    if unread:
        flags = f"Flags withheld: {unread} selected games are not read yet, so the groups' samples are uneven."
    elif flagged:
        flags = ("Flagged, each needs an idea or a stated reason (one naming an idea at work citing it is enough):\n\n"
                 + "\n".join(flagged))
    else:
        flags = "Flagged: none."
    return (f"# Behaviour gaps\n\nWritten {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(now))} by "
            f"loop/behaviour_gaps.py. {header}\n\nEach rate is the action's share of the turns that offered it "
            f"(the opportunity); n is those turns and the games holding any. \"died <=N\" is the share of those "
            f"actions whose dragon (a split's child) died within N = {replays.CROSSING_WINDOW} rounds. \"pays\" is "
            f"what the action paid: a ram's mean swing (the victim's length less its team's pickups of the drops, "
            f"less our length before less ours, M51), else the mean length {PAYOFF_ROUNDS} rounds on less at the "
            f"turn (a split's child added, 0 when dead), acting turns less the others. Each leader's "
            f"column pair compares us map by map (maps where both have {chance.STRATUM_GAMES}+ games with the chance, "
            f"weighted by both teams' chances there), p from shuffling whose games are whose within each map "
            f"(chance.strata_gap; at least 1/{chance.PERMUTATIONS + 1}). A gap is flagged at p <= {chance.TRIGGER_P} "
            f"/ {looks} looks with an odds ratio of {FLAG_ODDS}+ between the two (FLAG_ODDS), unless the leader's pays "
            f"is clearly below ours, or for a ram below 0 (M100: never copy an action that cost its taker): one-sided "
            f"p <= {chance.TRIGGER_P}, the payoff's variance clustered by game, on {PAYOFF_TURNS}+ acting turns of "
            f"ours (of theirs for the ram's 0), since a payoff on a few turns is a coin flip. An idea at work citing "
            f"a flag is one in flight or queued at priority 1-3 naming behaviour gaps, with the action in its title (citing)."
            f"\n\n{flags}\n\n"
            + head + "\n".join(lines) + "\n")


def main(state: Path | None = None, budget: int = BUDGET) -> str:
    """Reads up to `budget` new replays of the selection (and retries failed ones, READ_TRIES reads in all), rewrites
    behaviour-gaps.md and the counts cache, and returns a line to log (empty with no library yet, or when nothing
    changed: no game left to read, the cache not just reset, and the report newer than the library)."""
    state = state or loop_home()
    library, report, cache_path = state / LIBRARY, state / REPORT, state / CACHE
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    reset = cache.get("analyser") != analyser()
    if reset:
        cache = {"analyser": analyser(), "games": {}}
    if not library.exists() or (not reset and report.exists() and report.stat().st_mtime >= library.stat().st_mtime
                                and not cache.get("unread")):
        return ""
    names = leaders(state)
    chosen = selection(state, names)
    keys = {group: [f"{g['replay']}:{g['side']}" for g in games] for group, games in chosen.items()}
    old, games, read = cache["games"], {}, 0
    for group, rows in chosen.items():
        for key, game in zip(keys[group], rows):
            tries = old.get(key, {}).get("tries", 0)
            if (key not in old or "error" in old[key] and tries < READ_TRIES) and read < budget:
                old[key] = game_counts(Path(game["replay"]), game["side"])
                if "error" in old[key]:
                    old[key]["tries"] = tries + 1
                read += 1
            if key in old:
                games[key] = old[key]
    counts = {group: [games[k] for k in ks if k in games and "rows" in games[k]] for group, ks in keys.items()}
    ranked = {f"{g['replay']}:{g['side']}" for g in chosen["us"] if g.get("ranked") is True}
    counts["us ranked"] = [games[k] for k in keys["us"] if k in ranked and k in games and "rows" in games[k]]
    unread = sum(k not in games for ks in keys.values() for k in ks)
    errors = sum("error" in games[k] for ks in keys.values() for k in ks if k in games)
    ours = sorted({g.get("submission_id") for g in chosen["us"]} - {None})
    unranked = sum(g.get("ranked") is not True for g in chosen["us"])
    header = (f"Games from {library} (result-blind, non-dev): our latest {len(counts['us'])} of {OURS_MAX} "
              f"(submissions {', '.join(map(str, ours[-6:])) or '?'}{' and older' if len(ours) > 6 else ''}), "
              + "; ".join(f"{names[t]}'s (team {t}) latest {len(counts[t])} ranked" for t in names)
              + " (ranks 1-2 in study-history.jsonl's latest row). The opponents differ: "
              f"{unranked} of our {len(chosen['us'])} are unranked (our map sweeps, each against one rotating "
              f"opponent) while the leaders' are all ranked, so a gap may be whom we played rather than how; \"Us, "
              f"ranked games only\" is our rate in the other {len(chosen['us']) - unranked}."
              + (f" {unread} selected games are not read yet (at most {budget} a run)." if unread else "")
              + (f" {errors} could not be read (a bad file or failed tracking; each is retried at later runs, "
                 f"{READ_TRIES} reads in all)." if errors else ""))
    text = report_text(counts, names, citing(maintenance.read_jsonl(state / "ideas.jsonl")), header, time.time(),
                       unread)
    temporary = cache_path.with_name(f".{cache_path.name}.tmp")
    temporary.write_text(json.dumps({"analyser": cache["analyser"], "unread": unread, "games": games},
                                    separators=(",", ":")) + "\n")
    temporary.replace(cache_path)
    report.write_text(text)
    return f"behaviour gaps: read {read} replays, {unread} left; {text.count('**FLAG**')} gaps flagged"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--budget", type=int, default=BUDGET, help="new replays to read at most")
    print(main(budget=parser.parse_args().budget))
