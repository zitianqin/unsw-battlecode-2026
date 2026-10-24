"""Checks watch.py's board frames on a real ladder replay (testdata/trophy-155732.replay): a round's frame shows the
pearls that spawned at its start, as the bots see them when they move, not the board before the spawns, and a replay
whose map text withholds pearl timings (as the site's do from 2026-09-27 ~21:10 AEST, testdata/portals-381305.replay)
still reads, drawing its pearl tiles from the replay's pearlCountdown events, or '?' without them.

    .venv/bin/python -m unittest tools.test_watch
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replays  # noqa: E402
import watch  # noqa: E402

REPLAY = Path(__file__).resolve().parent / "testdata/trophy-155732.replay"
WITHHELD_REPLAY = Path(__file__).resolve().parent / "testdata/portals-381305.replay"


# The pearls on the board when each round's first dragon starts its turn, straight from the replay's events.
def pearls_at_first_turn(path: Path) -> dict[int, frozenset[watch.Point]]:
    pearls: set[watch.Point] = set()
    seen: dict[int, frozenset[watch.Point]] = {}
    r = -1
    for event in replays.load_replay(path).events:
        kind = event.which()
        if kind == "roundStart":
            r = event.roundStart.round
        elif kind == "tileChange":
            tile = (event.tileChange.tile.x, event.tileChange.tile.y)
            (pearls.add if event.tileChange.hasPearl else pearls.discard)(tile)
        elif kind == "turnStart" and r not in seen:
            seen[r] = frozenset(pearls)
    return seen


class Frames(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.w = watch.watch(REPLAY)

    # Found by a review on 2026-09-26: round 2's two new pearls were missing from its frame.
    def test_a_rounds_frame_draws_the_pearls_that_spawned_at_its_start(self) -> None:
        frame = self.w.frames[2]
        self.assertEqual(frame.start_pearls, frozenset())
        self.assertEqual(frame.pearls, {(4, 16), (20, 16)})
        drawn = watch.frame_lines(self.w, 2, (4, 16), 2, None)
        self.assertEqual(drawn[1 + 2 * 2 + 1][1 + 2 * 2], "*")  # the centre tile, row 3 of 5 drawn

    def test_every_frame_is_the_board_its_first_mover_saw(self) -> None:
        seen = pearls_at_first_turn(REPLAY)
        spawned = [r for r, frame in self.w.frames.items() if frame.pearls != frame.start_pearls]
        self.assertGreater(len(spawned), 20)  # the check covers real spawns, not only quiet rounds
        for r, pearls in seen.items():
            self.assertEqual(self.w.frames[r].pearls, pearls, f"round {r}")

    # watch takes its Game from its own recorder's walk (replays.game_of) instead of walking the replay a second time
    # (2026-10-01, for speed): it must be exactly replays.analyse's, doom walk included.
    def test_the_game_is_what_analyse_gives(self) -> None:
        expected = replays.analyse(replays.load_replay(REPLAY))
        for w in (self.w, watch.watch(REPLAY, doom=True)):
            self.assertTrue(w.game.checks.as_dict()["ok"])
            self.assertEqual((w.game.deaths, w.game.snapshots, w.game.teams, w.game.meals, w.game.last_round),
                             (expected.deaths, expected.snapshots, expected.teams, expected.meals, expected.last_round))


# Real deaths of champion aa92d8b's logging copy and each one's last two debug lines (testdata/deliberate-deaths-
# sample.json, from two replays a review of 2026-09-27 kept: rule-games/1790436765968434000/replays/champion/0005 and
# breeder/0002), up to two of each kind: head-ons, doomed moves into walls or bodies, young children dying, feeds,
# deaths on a fold step and split-out parents.
SAMPLE = json.loads((Path(__file__).resolve().parent / "testdata/deliberate-deaths-sample.json").read_text())


class Deliberate(unittest.TestCase):
    @staticmethod
    def classify(deaths: list[dict]) -> dict[tuple[int, int], str]:
        w = SimpleNamespace(logs=[tuple(line) for d in deaths for line in d["logs"]],
                            game=SimpleNamespace(deaths=[d["death"] for d in deaths]))
        return watch.deliberate_deaths(w)

    def test_feeds_and_split_out_parents_are_deliberate_and_nothing_else(self) -> None:
        found = Counter()
        for game in SAMPLE:
            deliberate = self.classify(game["deaths"])
            for d in game["deaths"]:
                why = deliberate.get((d["death"]["round"], d["death"]["id"]))
                found[why] += 1
                if why == "feed":
                    self.assertIn("rule feed", d["logs"][-1][3])
                elif why == "split-out parent":
                    self.assertIn("rule splitout", d["logs"][0][3])
                    self.assertEqual(d["logs"][0][0], d["death"]["round"] - 1)
                    self.assertEqual(d["death"]["length"], watch.SPLIT_OUT_PARENT)
        self.assertEqual(found, {None: 19, "feed": 4, "split-out parent": 4})
        # The two deaths on a fold step (breeder/0002, r409, ids 935 and 963) were head-ons with a 3-long enemy.
        folds = [d for g in SAMPLE for d in g["deaths"] if "rule fold" in d["logs"][-1][3]]
        self.assertEqual([(d["death"]["round"], d["death"]["id"]) for d in folds], [(409, 935), (409, 963)])
        self.assertEqual(self.classify(folds), {})

    # Since E0231 a dragon feeding the king at the unit cap logs `rule capfeed` (capfeed.hpp): the same death on
    # purpose, so it is a "feed" like `rule feed`, not a failure.
    def test_a_capfeed_is_a_feed(self) -> None:
        feed = next(d for g in SAMPLE for d in g["deaths"] if "rule feed" in d["logs"][-1][3])
        *rest, (r, i, team, text) = feed["logs"]
        capfed = {**feed, "logs": [*rest, [r, i, team, text.replace("rule feed", "rule capfeed")]]}
        self.assertIn("rule capfeed", capfed["logs"][-1][3])
        self.assertEqual(self.classify([capfed]), {(feed["death"]["round"], feed["death"]["id"]): "feed"})

    def test_a_feed_an_enemy_head_ends_or_a_split_out_two_rounds_back_is_not_deliberate(self) -> None:
        feed = next(d for g in SAMPLE for d in g["deaths"] if "rule feed" in d["logs"][-1][3])
        enemy = "A" if feed["death"]["team"] == "B" else "B"
        rammed = {**feed, "death": {**feed["death"], "reason": "hitHeadToHead", "hitByTeam": enemy, "hitByLength": 3}}
        self.assertEqual(self.classify([rammed]), {})
        parent = next(d for g in SAMPLE for d in g["deaths"] if "rule splitout" in d["logs"][0][3])
        (r, i, team, text), dying = parent["logs"][0], parent["logs"][1:]
        self.assertEqual(self.classify([{**parent, "logs": [[r - 1, i, team, text], *dying]}]), {})

    def test_scan_lists_a_deliberate_death_apart_and_never_as_a_young_death(self) -> None:
        w = watch.watch(REPLAY)  # a ladder game: no debug lines, so nothing deliberate
        self.assertFalse(any(kind.startswith("deliberate") for kind, _, _ in watch.scan(w, None)))
        young = next(d for d in w.game.deaths if 0 <= d.get("born", -1) and d["round"] - d["born"] <= 10)

        def deaths() -> list[str]:  # that dragon's death moments
            return [kind for kind, r, text in watch.scan(w, young["team"])
                    if r == young["round"] and f"dragon {young['id']} " in text]

        self.assertEqual(deaths()[0], "young death")  # a failure, and a death by cause after it
        self.assertEqual(len(deaths()), 2)
        with patch.object(watch, "deliberate_deaths", return_value={(young["round"], young["id"]): "feed"}):
            self.assertEqual(deaths(), ["deliberate death:feed"])


# Real deaths of ours from FEED_FROM on, from the 9 replays review 1790572014577194000 kept (testdata/feed-deaths-
# sample.json): each with its debug line's rule, what the moves-only label made of it when the sample was cut, and
# every dragon whose head was within FEED_REACH of the dying head just before its fatal move.
FEEDS = json.loads((Path(__file__).resolve().parent / "testdata/feed-deaths-sample.json").read_text())


# M63: without the label our fold's feeds read as `open never hitSelf` (fadc209's rule counts: 34.9 v 21.0 a game).
class MovesFeed(unittest.TestCase):
    @staticmethod
    def fed(case: dict, before: dict | None = None) -> bool:
        board = SimpleNamespace(board=SimpleNamespace(w=case["board"][0], h=case["board"][1]))
        if before is None:
            before = {int(i): (t, tuple(map(tuple, body))) for i, (t, body) in case["before"].items()}
        return watch.looks_fed(case["death"], before, lambda a, b: watch.manhattan(board, a, b))

    def test_debug_line_feeds_are_told_from_their_moves_and_so_are_some_capfeeds_and_crashes(self) -> None:
        labelled = {"feed found", "capfeed labelled", "crash labelled"}
        for case in FEEDS:
            self.assertEqual(self.fed(case), case["kind"] in labelled, case["kind"])
        self.assertEqual(Counter(c["rule"] for c in FEEDS if c["kind"] == "feed found"), {"feed": 6})
        # The 4 feeds it misses (of 287) and the crashes it takes are the price of reading moves alone.
        self.assertEqual(sum(c["kind"] == "feed missed" for c in FEEDS), 4)

    def test_an_enemy_head_beside_it_a_shorter_teammate_or_a_head_on_is_no_feed(self) -> None:
        case = next(c for c in FEEDS if c["kind"] == "feed found")
        death = case["death"]
        before = {int(i): (t, tuple(map(tuple, body))) for i, (t, body) in case["before"].items()}
        self.assertTrue(self.fed(case, before))
        x, y = death["head"]
        enemy = "B" if death["team"] == "A" else "A"
        self.assertFalse(self.fed(case, {**before, -1: (enemy, ((x + 1, y), (x + 2, y)))}))
        shorter = {i: (t, body[:death["length"] - 1] if t == death["team"] and i != death["id"] else body)
                   for i, (t, body) in before.items()}
        self.assertFalse(self.fed(case, shorter))
        self.assertFalse(self.fed({**case, "death": {**death, "reason": "hitHeadToHead"}}, before))

    def test_it_counts_as_a_feed_only_from_feed_from_on(self) -> None:
        d = watch.Doom(watch.FEED_FROM, 1, "A", 3, "hitSelf", "open", None, (), None, "never", None,
                       beside_mate=True)
        self.assertEqual(watch.death_kind(d), "feed?")
        self.assertEqual(watch.death_kind(d, feed_from=watch.FEED_FROM + 1), "open never hitSelf")
        self.assertEqual(watch.death_kind(d, "feed"), "feed")  # a debug line's why still wins


# Real deaths of ours at the unit ceiling from review 1790592506760224000's logging-copy games against the champion
# (testdata/capfeed-deaths-sample.json): each with its debug line's rule, what the labels made of it when the sample
# was cut, its doom place and trigger, and every living dragon just before its fatal move (the ones whose head was
# within FEED_REACH + 1 of the dying head whole, the rest by their head only, which is all at_cap and looks_fed read).
CAPFEEDS = json.loads((Path(__file__).resolve().parent / "testdata/capfeed-deaths-sample.json").read_text())


# M63: before foldRound our cap feeds (capfeed.hpp) read as `open never hitSelf`, 129 of 154 on cfaceea's 8 replays.
class MovesCapFeed(unittest.TestCase):
    @staticmethod
    def kind(case: dict, before: dict | None = None, round_: int | None = None) -> str:
        board = SimpleNamespace(board=SimpleNamespace(w=case["board"][0], h=case["board"][1]))
        if before is None:
            before = {int(i): (t, tuple(map(tuple, body))) for i, (t, body) in case["before"].items()}
        death = {**case["death"], "round": case["death"]["round"] if round_ is None else round_}
        d = watch.Doom(death["round"], death["id"], death["team"], death["length"], death["reason"],
                       case["doom"]["place"], None, (), None, case["doom"]["trigger"], None,
                       beside_mate=watch.looks_fed(death, before, lambda a, b: watch.manhattan(board, a, b)),
                       at_cap=watch.at_cap(death, before))
        return watch.death_kind(d)

    def test_cap_feeds_are_told_from_their_moves_and_doomed_crashes_beside_the_king_are_not(self) -> None:
        for case in CAPFEEDS:
            expected = {"capfeed found": "capfeed?", "feed? from foldRound": "feed?"}.get(case["kind"])
            self.assertEqual(self.kind(case) == expected, expected is not None, (case["kind"], self.kind(case)))
        self.assertEqual(Counter(c["rule"] for c in CAPFEEDS if c["kind"].startswith("capfeed")), {"capfeed": 6})
        # The misses were doomed when they fed (trigger teammate), as are the crashes the label must not take.
        self.assertEqual({c["doom"]["trigger"] for c in CAPFEEDS if c["kind"] == "capfeed missed"}, {"teammate"})

    def test_below_the_ceiling_or_from_foldround_it_is_no_capfeed(self) -> None:
        case = next(c for c in CAPFEEDS if c["kind"] == "capfeed found")
        before = {int(i): (t, tuple(map(tuple, body))) for i, (t, body) in case["before"].items()}
        mates = [i for i, (t, body) in before.items() if t == case["death"]["team"] and len(body) == 1]
        alive = sum(t == case["death"]["team"] for t, _ in before.values())
        spare = alive - (replays.UNIT_LIMIT - replays.CAP_MARGIN)  # teammates it can lose and stay at the ceiling
        self.assertEqual(self.kind(case, {i: v for i, v in before.items() if i not in mates[:spare]}), "capfeed?")
        self.assertNotEqual(self.kind(case, {i: v for i, v in before.items() if i not in mates[:spare + 1]}),
                            "capfeed?")
        self.assertEqual(self.kind(case, round_=watch.FEED_FROM), "feed?")


# Ways the site's replays may withhold pearl timings after 2026-09-27 21:00 AEST: "zeroed" is the form they took
# (every TILE line kept as `TILE x y 0 0`, testdata/portals-381305.replay); the other three were guesses made before,
# kept in case the form changes: every TILE line removed, cut to `TILE x y`, or its gaps not numbers.
WITHHELD = {
    "zeroed": lambda text: re.sub(r"^(TILE \d+ \d+) .*$", r"\1 0 0", text, flags=re.M),
    "removed": lambda text: re.sub(r"^TILE \d+ \d+ .*\n", "", text, flags=re.M),
    "short": lambda text: re.sub(r"^(TILE \d+ \d+) .*$", r"\1", text, flags=re.M),
    "not numbers": lambda text: re.sub(r"^(TILE \d+ \d+) .*$", r"\1 ? ?", text, flags=re.M),
}


# A copy of a real replay at `out` with its map text rewritten by `edit` and its pearlCountdown events kept or not.
def rewritten(path: Path, out: Path, edit=lambda text: text, countdowns: bool = True) -> Path:
    replay = replays.load_replay(path)
    copy = replay.as_builder()
    copy.map = edit(replay.map)
    if not countdowns:
        events = [e for e in replay.events if e.which() != "pearlCountdown"]
        kept = copy.init("events", len(events))
        for i, e in enumerate(events):
            kept[i] = e
    out.write_bytes(copy.to_bytes_packed())
    return out


class WithheldPearls(unittest.TestCase):
    # A pearl tile without a pearl in round 2 is drawn '+' from the real map text and '?' from withheld text.
    def test_withheld_tiles_draw_as_unknown_not_as_floor(self) -> None:
        w = watch.watch(REPLAY)
        self.assertEqual(w.board.unknown, set())
        tile = next(t for t, gap in sorted(w.board.gaps.items()) if gap != (0, 0) and t not in w.frames[2].pearls)
        self.assertEqual(watch.frame_lines(w, 2, tile, 0, None)[2][1], "+")
        with tempfile.TemporaryDirectory() as tmp:
            for how, edit in WITHHELD.items():
                with self.subTest(how):
                    cut = watch.watch(rewritten(REPLAY, Path(tmp) / "cut.replay", edit, countdowns=False))
                    self.assertEqual(len(cut.board.unknown), w.board.w * w.board.h)
                    self.assertEqual(cut.board.edges, w.board.edges)  # the layout is still read
                    self.assertEqual(watch.frame_lines(cut, 2, tile, 0, None)[2][1], "?")
                    self.assertEqual(cut.frames[2].pearls, w.frames[2].pearls)  # pearls seen still come from events

    # The site's real withheld form (a dev battle of ours on Portals, requested 2026-09-27 11:14 UTC): every TILE line
    # is `x y 0 0`, but the pearlCountdown events still name every spawning tile. Before, the frames read the zeroed
    # text as a map without pearls and drew every pearl tile '.', with or without the countdowns.
    def test_the_sites_zeroed_tiles_draw_from_countdowns_or_as_unknown(self) -> None:
        w = watch.watch(WITHHELD_REPLAY)
        text = replays.load_replay(WITHHELD_REPLAY).map
        self.assertEqual({tuple(line.split()[3:]) for line in text.splitlines() if line.startswith("TILE ")},
                         {("0", "0")})
        self.assertEqual(len(w.countdowns), 104)  # of the board's 512 tiles
        self.assertEqual(w.board.unknown, set())
        free = [t for t in w.board.all_tiles() if t not in w.frames[0].pearls
                and all(t not in body for _, body in w.frames[0].dragons.values())]
        drawn = {}
        for tile in (next(t for t in free if t in w.countdowns), next(t for t in free if t not in w.countdowns)):
            drawn[tile] = watch.frame_lines(w, 0, tile, 0, None)[2][1]
        self.assertEqual([drawn[t] for t in drawn], ["+", "."])
        with tempfile.TemporaryDirectory() as tmp:
            cut = watch.watch(rewritten(WITHHELD_REPLAY, Path(tmp) / "cut.replay", countdowns=False))
            self.assertEqual(len(cut.board.unknown), 512)
            self.assertEqual([watch.frame_lines(cut, 0, t, 0, None)[2][1] for t in drawn], ["?", "?"])
            self.assertEqual(cut.frames[0].pearls, w.frames[0].pearls)


# Our online Queen Of Spades loss 403920 (submission 8636, side A, against dev team 722, eliminated r174), the game
# sig watched on 2026-09-28 (MISSES 64): our dragons eating their way into the pocket's dead ends and alcoves until
# they could not turn, and dying there again and again. Each case below was checked against watch.py frames.
DOOM_REPLAY = Path(__file__).resolve().parent / "testdata/queen-403920.replay"


# A ring of n tiles, (0,0) to (n-1,0), each joined to the next east and the last back to the first; nothing else.
def ring(n: int) -> dict[watch.Point, list[tuple[watch.Point, bool] | None]]:
    return {(i, 0): [None, (((i + 1) % n, 0), False), None, (((i - 1) % n, 0), False)] for i in range(n)}


class Doom(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.w = watch.watch(DOOM_REPLAY, doom=True)
        cls.by_id = {d.id: d for d in cls.w.dooms}

    # The engine checks a step against the body before the tail moves, so a dragon filling a ring has nowhere to go,
    # one in a ring a tile longer follows its tail forever, and a pearl on that tile holds the tail back a step.
    def test_a_ring_as_long_as_the_dragon_is_a_trap_and_one_tile_longer_is_not(self) -> None:
        for length in (2, 3, 6):
            body = tuple((i, 0) for i in range(length - 1, -1, -1))  # head at the east end, heading east
            self.assertEqual(watch.reach(ring(length), {0: body}, 0, set()), (0, False))
            self.assertTrue(watch.reach(ring(length + 1), {0: body}, 0, set())[1])
            self.assertFalse(watch.reach(ring(length + 1), {0: body}, 0, {(length, 0)})[1])

    # Room counts tiles, but a dead-end corridor holds a dragon that cannot turn round however many tiles are left.
    def test_a_dead_end_longer_than_the_dragon_is_still_a_trap(self) -> None:
        corridor = {(i, 0): [None, ((i + 1, 0), False) if i < 7 else None, None, ((i - 1, 0), False) if i else None]
                    for i in range(8)}
        room, out = watch.reach(corridor, {0: ((2, 0), (1, 0), (0, 0))}, 0, set())
        self.assertEqual((room, out), (5, False))
        self.assertTrue(watch.reach(corridor, {0: ((2, 0), (1, 0), (0, 0))}, 0, set(), cap=5)[1])  # or open space

    def test_the_maps_pockets_are_its_two_portal_rooms(self) -> None:
        found = watch.pockets(self.w.board, watch.step_table(self.w.board))
        self.assertEqual(Counter(pocket for pocket, _ in found.values()), {"6,31": 16, "13,1": 16})
        self.assertEqual(found[(8, 32)][1], ((8, 33), (9, 33)))  # the door's two sides

    def test_every_death_gets_one_doom_and_pocket_deaths_are_counted_as_sig_saw(self) -> None:
        self.assertEqual(len(self.w.dooms), len(self.w.game.deaths))
        ours = [d for d in self.w.dooms if d.team == "A"]
        self.assertEqual((len(ours), sum(d.place == "pocket" for d in ours)), (35, 32))
        self.assertEqual(sum(d.place == "pocket" and d.reason == "hitWall" and d.doomed_since is not None
                             for d in ours), 20)  # every pocket hitWall of ours was doomed before it moved

    # Dragon 7 crossed the door into the alcove at 8,33 (kelp west and south, the portal back its own neck); dragon
    # 2, moving first in round 29, put its head on 8,32, the only way out.
    def test_a_teammate_closing_the_only_exit_is_the_trigger(self) -> None:
        d = self.by_id[7]
        self.assertEqual((d.round, d.reason, d.pocket, d.doomed_since, d.trigger), (29, "hitWall", "6,31", 29,
                                                                                    "teammate"))
        self.assertEqual((d.shutter, d.shut_split), (2, None))
        self.assertEqual(self.by_id[10].shutter, None)  # never shut in: no shutter

    # Dragon 15 ate the corner pearl at 6,31 in round 88 (kelp north and west): at length 3 its only way on was south
    # onto another pearl and into the alcove at 6,33, its tail held back; at length 2 it could have turned east.
    def test_growing_into_a_corner_is_ate(self) -> None:
        d = self.by_id[15]
        self.assertEqual((d.doomed_since, d.trigger, d.lead, d.room), (89, "ate", 3, 2))
        child = self.by_id[35]  # split off in the alcove in round 91 and born with no way out
        self.assertEqual((child.doomed_since, child.trigger, child.lead), (91, "born", 0))

    # Regression: the flood alone called dragon 54 doomed from round 131, when teammate 51's head sat below it, and
    # blamed its own move; but 51 was moving on, and 54 could still go round by 7,31 and 7,32 to reach 8,32 and the
    # door after 51 left it. It was shut in only from round 132, when 51 moved into 7,32 beneath it.
    def test_a_detour_that_reaches_a_tile_after_it_frees_is_a_way_out(self) -> None:
        d = self.by_id[54]
        self.assertEqual((d.doomed_since, d.trigger), (132, "teammate"))

    def test_a_death_never_shut_in_says_so(self) -> None:
        d = self.by_id[10]  # 8 long, rammed by teammate 11 crossing the door
        self.assertEqual((d.reason, d.doomed_since, d.trigger, d.lead), ("hitHeadToHead", None, "never", None))
        self.assertEqual(watch.death_kind(d), "pocket never hitHeadToHead")

    def test_the_summary_counts_each_games_kinds_by_side(self) -> None:
        kinds = watch.death_kinds(self.w.dooms, "A")
        self.assertEqual(sum(kinds["ours"].values()), 35)
        self.assertEqual(sum(kinds["theirs"].values()), 18)
        table = watch.doom_table([("Queen Of Spades", kinds), ("Queen Of Spades", kinds)])
        self.assertTrue(table[0].startswith("Queen Of Spades: 2 games, deaths a game ours 35.0, theirs 18.0"))
        row = next(line for line in table if "pocket deeper" in line).split()
        self.assertEqual(row[2:6], ["4.00", "2.00", "11%", "11%"])

    # Regression (review of 2026-09-28): 9 of our 35 deaths here were split-outs' parents, left 2 long by their own
    # split to die on their next turn, which the table had filed as traps (pocket deeper and the like).
    def test_a_split_outs_parent_is_its_own_kind(self) -> None:
        kinds = watch.death_kinds(self.w.dooms, "A")
        self.assertEqual((kinds["ours"]["split-out"], kinds["theirs"]["split-out"]), (9, 2))
        parents = [d for d in self.w.dooms if d.split_out]
        self.assertTrue(all(d.length == watch.SPLIT_OUT_PARENT for d in parents))
        # A debug line's why wins: a feed is a feed, whatever its place and trigger.
        feed = parents[0]
        self.assertEqual(watch.death_kinds([feed], feed.team, {(feed.round, feed.id): "feed"})["ours"], {"feed": 1})

    def test_a_split_out_parent_is_read_from_its_own_split_the_turn_before(self) -> None:
        death = {"id": 5, "round": 100, "length": 2, "team": "A", "reason": "hitWall"}
        split = {(99, 5): watch.Turn("split 4")}
        self.assertTrue(watch.split_out_parent(death, split))
        self.assertFalse(watch.split_out_parent({**death, "length": 3}, split))  # the rule leaves exactly 2
        self.assertFalse(watch.split_out_parent(death, {(98, 5): watch.Turn("split 4")}))  # it lived on
        self.assertFalse(watch.split_out_parent(death, {(99, 5): watch.Turn("N")}))
        rammed = {**death, "reason": "hitHeadToHead", "hitByTeam": "B"}
        self.assertFalse(watch.split_out_parent(rammed, split))  # an enemy head killed it

    # Kinds are compared by their share of each side's deaths, so a side that dies more often isn't read as dying
    # more of everything, with a two-sided sign test, since either side can come out ahead.
    def test_the_table_compares_shares_with_a_two_sided_sign_test(self) -> None:
        game = {"ours": {"X": 2, "Y": 8}, "theirs": {"X": 1, "Y": 1}}  # more X deaths of ours, a smaller share
        table = watch.doom_table([("M", game)] * 5)
        self.assertIn("more in 5-0 games, p 0.062", table[0])  # one-sided it would be 0.031
        self.assertIn("2 kinds tested over the 5 games both sides died", table[0])
        row = next(line for line in table if line.split()[0] == "X").split()
        self.assertEqual(row[1:], ["2.00", "1.00", "20%", "50%", "0-5", "theirs", "0.062"])
        lone = watch.doom_table([("M", {"ours": {"X": 1}, "theirs": {}})])
        self.assertIn("over the 0 games both sides died", lone[0])
        self.assertEqual(lone[2].split()[1:], ["1.00", "0.00", "100%", "-", "0-0", "even", "1"])



# Regression (review of 2026-09-28): in online game 95037 (Queen Of Spades) dragon 15 split its tail off in the pocket
# at 13,1 in round 248 and was shut in from 249, which the tool blamed on the split without checking the others. Its
# child (15,1-15,2) doesn't close it in: teammate 50's head moving onto 20,31, where the pocket's portal at 16,1
# comes out, did. A split's child counts with the split, and only when it alone explains the doom.
class SplitTrigger(unittest.TestCase):
    def test_a_split_is_the_trigger_only_when_the_split_explains_it(self) -> None:
        w = watch.watch(Path(__file__).resolve().parent / "testdata/queen-95037.replay", doom=True)
        d = next(d for d in w.dooms if d.id == 15)
        self.assertEqual((d.round, d.doomed_since, d.trigger), (250, 249, "teammate"))
        self.assertEqual(w.turns[(248, 15)].action, "split 2")
        self.assertEqual((d.shutter, d.shut_split), (50, None))  # M93: the shutter, which didn't split

    # M93: dragon 21 was shut in at its round-95 turn start by teammate 22's round-94 split (child 24), its last move
    # (21 moves before 22 in a round); 22's own move the turn after a split is "turn before".
    def test_a_shutter_that_split_on_its_shutting_move_says_so(self) -> None:
        w = watch.watch(Path(__file__).resolve().parent / "testdata/queen-95037.replay", doom=True)
        by_id = {d.id: d for d in w.dooms}
        self.assertEqual((by_id[21].doomed_since, by_id[21].shutter, by_id[21].shut_split), (95, 22, "that turn"))
        self.assertEqual(w.turns[(94, 22)].action, "split 2")
        self.assertEqual((by_id[9].shutter, by_id[9].shut_split), (6, "turn before"))
        self.assertIn("22, split that turn", next(line for line in watch.doom_lines(w, "A") if " 21 " in line[:9]))


# Our online Trauma loss 392179 (submission 8636 on A, dev team 758, 247 deaths of ours and none of theirs), where 147
# of our deaths were split-outs' parents and a sprint could have got several dragons out that the tool called doomed.
SPRINT_REPLAY = Path(__file__).resolve().parent / "testdata/trauma-392179.replay"


class Sprints(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.w = watch.watch(SPRINT_REPLAY, doom=True)
        cls.by_id = {d.id: d for d in cls.w.dooms}

    def test_its_split_out_parents_are_their_own_kind(self) -> None:
        kinds = watch.death_kinds(self.w.dooms, "A")
        self.assertEqual((sum(kinds["ours"].values()), kinds["ours"]["split-out"], kinds["theirs"]), (247, 147, {}))

    # A 2x2 room (0,0)-(1,1) with one door south of (0,1). Four long with its head in the door's tile, every
    # single-step walk runs into its own body; the sprint E, N (paying one segment) and then W (another) leaves it 2
    # long in the room, and it circles from there.
    def test_a_sprint_that_sheds_the_tail_is_a_way_out(self) -> None:
        room = {(0, 0): [None, ((1, 0), False), ((0, 1), False), None],
                (1, 0): [None, None, ((1, 1), False), ((0, 0), False)],
                (0, 1): [((0, 0), False), ((1, 1), False), ((0, 2), False), None],
                (1, 1): [((1, 0), False), None, None, ((0, 1), False)],
                (0, 2): [((0, 1), False), None, ((0, 3), False), None],
                (0, 3): [((0, 2), False), ((1, 3), False), None, None],
                (1, 3): [None, None, None, ((0, 3), False)]}
        body = ((0, 1), (0, 2), (0, 3), (1, 3))
        self.assertTrue(watch.escapes(room, {}, body, set()))
        self.assertEqual(watch.reach(room, {0: body}, 0, set()), (3, True))

    # Regression: dragon 91, 4 long at r394 with its head in the door of the 2x2 room at 24,6-25,7, could sprint N
    # (eating 24,6's pearl), E and S, 3 long in the room and circling it for good; it was called doomed from r394.
    # It ate instead and was 5 long at r395, when no sprint was left (then split out, dying 2 long at r398).
    def test_dragon_91_was_doomed_only_once_it_ate_its_way_in(self) -> None:
        d = self.by_id[91]
        self.assertEqual((d.round, d.length, d.doomed_since, d.trigger, d.split_out), (398, 2, 395, "ate", True))


class Summary(unittest.TestCase):
    # CLAUDE.md: every reader keeps dev games apart. A folder's index.json marks dev games; a replay it doesn't list is
    # a local one.
    def test_the_summary_keeps_dev_ladder_and_local_games_apart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("1.replay", "2.replay"):
                (folder / name).write_bytes(DOOM_REPLAY.read_bytes())
            index = {"games": [{"replay": str(folder / "1.replay"), "side": "A", "dev": True},
                               {"replay": str(folder / "2.replay"), "side": "A"}]}
            (folder / "index.json").write_text(json.dumps(index))
            self.assertEqual([watch.game_kind(folder / n) for n in ("1.replay", "2.replay", "3.replay")],
                             ["dev", "ladder", "local"])
            args = SimpleNamespace(replays=[folder / "1.replay", folder / "2.replay"], team=None, summary=True,
                                   json=False, feed_from=watch.FEED_FROM)
            with patch("sys.stdout.write") as out:
                watch.doom_main(args)
        text = "".join(call.args[0] for call in out.call_args_list)
        ladder, dev = text.split("ladder and autoscrim games:")[1].split("dev games:")
        self.assertIn("Queen Of Spades: 1 games", ladder)
        self.assertIn("Queen Of Spades: 1 games", dev)


if __name__ == "__main__":
    unittest.main()
