"""Checks the site-data parser on a saved team page (testdata/team-data.json, captured 2026-09-26): the site streams
a deferred field as a second chunk line, which broke the leader study with "Extra data". Also that `fetch` fails
when every battle it tried was skipped, passes over a battle with no game played yet
(testdata/team-264-recent-live.json, MISSES 92), and the opening stats on a real ranked replay
(testdata/trophy-155732.replay: bread first search (A) vs SSS (B) on Trophy, 25x25, which wraps; captured 2026-09-26
from the leader study), and the index row's submissions and draw flag from a real battle detail
(loop/testdata/online-battles-sample.json). And that replays loads beside the referee's run.py in one process, either
first (MISSES 97).

    .venv/bin/python -m unittest tools.test_replays
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import replays  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "testdata" / "team-data.json"
TROPHY = Path(__file__).resolve().parent / "testdata" / "trophy-155732.replay"
TRAUMA = Path(__file__).resolve().parent / "testdata" / "trauma-95034.replay"
# A real local game that went to r499 with fold merges into B's king, gzipped: E0102's kept loss
# 0308-gen_eval_111406294-aA (near_portal_labyrinth, 32x32), captured 2026-09-26.
FOLD = Path(__file__).resolve().parent / "testdata" / "fold-e0102-0308.replay"
# Rank 1 Cutlery (A) against team 470 on Prisoners Dilemma, ranked, 2026-09-28: its dead-end pumps (M84).
DILEMMA = Path(__file__).resolve().parent / "testdata" / "dilemma-477675.replay"
# Team 470 (A) against SSS (B) on Trophy, unranked, 2026-09-24: B's split-out parents that die on a body, and its one
# unpaid sprint step (r398, dragon 353's third step north), of the ~100 in the leader study's replays the one not
# stepping back over the edge its paid step just crossed.
TROPHY_143349 = Path(__file__).resolve().parent / "testdata" / "trophy-143349.replay"


class SavedPage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.urls: list[str] = []

    def get(self, url: str) -> bytes:
        self.urls.append(url)
        return self.body


def lines(*rows: object) -> bytes:
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


class TestPageData(unittest.TestCase):
    def test_team_page_with_a_streamed_chunk_gives_recent_battles_and_the_deferred_field(self) -> None:
        body = FIXTURE.read_bytes()
        self.assertGreater(body.count(b"\n"), 1)  # the fixture keeps the chunk line that broke the study
        http = SavedPage(body)
        data = replays.page_data(http, "/teams/157")
        self.assertEqual(http.urls, [f"{replays.SITE}/teams/157/__data.json"])
        self.assertEqual(data["team"]["id"], 157)
        self.assertTrue(data["recent"])
        self.assertTrue(all(isinstance(b["replayId"], int) for b in data["recent"]))
        self.assertTrue(all("elo" in p for p in data["ranks"]["points"]))  # resolved from the chunk line

    def test_a_missing_chunk_or_an_error_page_is_a_clear_error(self) -> None:
        deferred = {"type": "data", "nodes": [{"type": "data", "data": [{"ranks": 1}, ["Promise", 2], 7]}]}
        with self.assertRaisesRegex(ValueError, r"/teams/9/__data.json: deferred field: chunk 7 was never streamed"):
            replays.page_data(SavedPage(lines(deferred)), "/teams/9")
        failed = {"type": "chunk", "id": 7, "error": {"message": "boom"}}
        with self.assertRaisesRegex(ValueError, "chunk 7 failed"):
            replays.page_data(SavedPage(lines(deferred, failed)), "/teams/9")
        with self.assertRaisesRegex(ValueError, "expected page data first, got 'error'"):
            replays.page_data(SavedPage(lines({"type": "error", "error": {"message": "x"}})), "/teams/9")


class TestFetch(unittest.TestCase):
    # A visualiser page for a battle with no games to download: fetched, nothing new.
    EMPTY_BATTLE = lines({"type": "data", "nodes": [{"type": "data", "data": [
        {"battle": 1, "games": 6}, {"match": 2, "teamAName": 5, "teamBName": 5},
        {"seriesId": 3, "ranked": 4, "teamAId": 3, "teamBId": 3}, 9, False, "x", []]}]})

    def fetch(self, battle_pages: list[bytes]) -> None:
        pages = iter(battle_pages)

        def get(_: replays.Http, url: str) -> bytes:
            return FIXTURE.read_bytes() if "/teams/" in url else next(pages)

        with tempfile.TemporaryDirectory() as out, patch.object(replays.Http, "get", get), patch("sys.stderr"):
            replays.cmd_fetch(argparse.Namespace(out=out, max_games=None, match=None, battle=None, top_battles=False,
                                                 team=157,
                                                 ranked=False, battles=2))

    def test_fetch_fails_when_every_battle_was_skipped(self) -> None:
        error = lines({"type": "error", "error": {"message": "x"}})
        with self.assertRaisesRegex(SystemExit, "all 2 battles were skipped"):
            self.fetch([error, error])

    def test_one_good_battle_is_enough(self) -> None:
        self.fetch([self.EMPTY_BATTLE, lines({"type": "error"})])

    # Team 264's real rows just after a ranked draw (testdata/team-264-recent-live.json): its newest ranked battle is
    # live with every game pending and replayId null, which the leader study's `--ranked --battles 1` fetched as match
    # None and failed on (29 Sep 14:05 and 15:06).
    LIVE = json.loads((Path(__file__).parent / "testdata" / "team-264-recent-live.json").read_text())["recent"]

    def fetched_battles(self, rows: list[dict]) -> list[int]:
        tried: list[int] = []
        with tempfile.TemporaryDirectory() as out, patch("sys.stderr"), \
                patch.object(replays, "page_data", lambda _http, _path: {"recent": rows}), \
                patch.object(replays.Fetcher, "fetch_battle", lambda _self, mid, _only: tried.append(mid)):
            replays.cmd_fetch(argparse.Namespace(out=out, max_games=None, match=None, battle=None, top_battles=False,
                                                 team=264, ranked=True, battles=1))
        return tried

    def test_a_battle_with_no_game_finished_is_passed_over_for_the_next(self) -> None:
        self.assertIsNone(self.LIVE[1]["replayId"])
        self.assertEqual(self.fetched_battles(self.LIVE), [562059])

    def test_finished_battles_without_replay_ids_fail_the_fetch(self) -> None:
        gone = [{**b, "replayId": None} for b in self.LIVE]
        with self.assertRaisesRegex(SystemExit, "all 2 battles were skipped"):
            self.fetched_battles(gone)


class TestBattlesBetween(unittest.TestCase):
    # Six real pages of team 852's battle list (/battles?teams=852, read 2026-09-28 20:29 AEST, newest first, 10 a
    # page), served as a six-page list.
    SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "loop/testdata/riser-battles-852.json").read_text())
    FLAT = [b for page in SAMPLE["pages"] for b in page]

    def listing(self, since: str, until: str, max_pages: int) -> tuple[list[dict], bool, list[int]]:
        reads: list[int] = []

        def page(n: int) -> dict:
            reads.append(n)
            return {"battles": self.SAMPLE["pages"][n - 1], "total": len(self.FLAT), "perPage": 10}

        found, complete = replays.battles_between(page, replays.battle_time({"at": since}),
                                                  replays.battle_time({"at": until}), max_pages)
        return found, complete, reads

    def test_the_battles_in_the_span_are_found_by_bisection_oldest_first(self) -> None:
        since, until = "2026-09-28T03:05:00Z", "2026-09-28T06:50:00Z"
        found, complete, reads = self.listing(since, until, 30)
        within = [b["id"] for b in self.FLAT
                  if replays.battle_time({"at": since}) <= replays.battle_time(b) <= replays.battle_time({"at": until})]
        self.assertEqual([b["id"] for b in found], within[::-1])
        self.assertTrue(complete)
        self.assertNotIn(6, reads)  # page 5 already passes `since`
        self.assertLess(len(set(reads)), 6)

    def test_reads_stop_at_the_page_budget_and_say_the_list_is_partial(self) -> None:
        found, complete, reads = self.listing("2026-09-27T22:00:00Z", "2026-09-28T10:30:00Z", 3)
        self.assertFalse(complete)
        self.assertLessEqual(len(set(reads)), 3)
        self.assertEqual(len(found), len({b["id"] for b in found}))


class TestIndexRow(unittest.TestCase):
    # A real battle detail (GET /battles/335845, loop/testdata/online-battles-sample.json, the fields online.battle_result
    # reads), split as the visualiser page serves it: the battle with its match, and its games beside it.
    DETAIL = json.loads((Path(__file__).resolve().parents[1] / "loop/testdata/online-battles-sample.json")
                        .read_text())["detail_335845"]

    def row(self, requested_at: str) -> dict:
        battle = {k: v for k, v in self.DETAIL.items() if k != "games"}
        battle["match"] = {**battle["match"], "requestedAt": requested_at}
        page = {"battle": battle, "games": self.DETAIL["games"][:1]}
        with tempfile.TemporaryDirectory() as out, patch.object(replays, "page_data", lambda *a: page), \
                patch.object(replays.Http, "get", lambda *a: TROPHY.read_bytes()), patch("sys.stderr"):
            replays.Fetcher(replays.Http(), Path(out), None).fetch_battle(335845)
            return json.loads((Path(out) / "index.jsonl").read_text())

    def test_each_row_keeps_both_submissions_and_whether_the_draw_requested_the_battle(self) -> None:
        row = self.row(self.DETAIL["match"]["requestedAt"])  # 01:41:13.741Z: requested by a team
        self.assertEqual((row["submissionAId"], row["submissionBId"], row["autoscrim"]), (7676, 4525, False))
        self.assertTrue(self.row("2026-09-27T02:00:00.000Z")["autoscrim"])

    def test_the_draw_is_exactly_on_an_even_utc_hour(self) -> None:
        self.assertTrue(replays.autoscrim_draw("2026-09-27T00:00:00.000Z"))
        self.assertIsNone(replays.autoscrim_draw(None))
        for at in ("2026-09-27T03:00:00.000Z", "2026-09-27T02:00:00.001Z", "2026-09-27T02:00:01.000Z"):
            self.assertFalse(replays.autoscrim_draw(at), at)


class TestSnapshot(unittest.TestCase):
    def test_second_longest_is_the_next_dragon_and_zero_when_alone(self) -> None:
        body = lambda n: [(x, 0) for x in range(n)]  # noqa: E731
        info = replays.MapInfo("m", 20, 20, "x", [("A", body(5)), ("A", body(3)), ("A", body(5)), ("B", body(4))])
        walker = replays._Walker(info)
        walker._snapshot()
        snap = walker.snapshots[-1]
        self.assertEqual((snap.longest["A"], snap.second["A"], snap.total["A"]), (5, 5, 13))
        self.assertEqual((snap.longest["B"], snap.second["B"]), (4, 0))


class TestRound400(unittest.TestCase):
    # testdata/trauma-95034.replay: a ranked online game of ours (submission 3182, side B) on Trauma that went to the
    # round limit, captured 2026-09-26. Side A is 3 long of 20 at round 399, 4 of 21 at 400 and 3 of 21 at 401, so
    # reading either neighbouring round gives a different answer.
    def test_the_summary_gives_each_teams_longest_and_total_at_round_400(self) -> None:
        game = replays.analyse(replays.load_replay(TRAUMA))
        rounds = {s.round: (s.longest["A"], s.total["A"]) for s in game.snapshots}
        self.assertEqual((rounds[399], rounds[400], rounds[401]), ((3, 20), (4, 21), (3, 21)))
        summary = replays.game_summary(game, None)
        self.assertEqual(summary["teams"]["A"]["at400"], {"longest": 4, "total": 21})
        self.assertEqual(summary["teams"]["B"]["at400"], {"longest": 4, "total": 39})

    def test_a_game_that_ended_before_round_400_has_none(self) -> None:
        summary = replays.game_summary(replays.analyse(replays.load_replay(TROPHY)), None)  # eliminated in round 133
        self.assertEqual((summary["teams"]["A"]["at400"], summary["teams"]["B"]["at400"]), (None, None))


# testdata/alive-rounds-sample.json: each team's living dragons at the end of every round of a real local game at the
# ceiling (review 1790592506760224000's logging copy, side A, against the champion on a 36x24 worldgen map, 2026-09-28).
ALIVE = json.loads((Path(__file__).resolve().parent / "testdata" / "alive-rounds-sample.json").read_text())


class TestCapRounds(unittest.TestCase):
    @staticmethod
    def game(rows: list[list[int]]) -> object:
        return replays.Game(replays.MapInfo("m", 36, 24, "x", []), "", "", "roundLimit", None, 499, {}, {}, [],
                            [replays.Snapshot(r, {"A": a, "B": b}, {}, {}) for r, a, b in rows], replays.Checks())

    # M71: rules that act at the cap fire only there, and local games sit there far more than online ones.
    def test_a_teams_share_counts_rounds_within_cap_margin_of_the_unit_limit(self) -> None:
        game = self.game(ALIVE["alive"])
        self.assertEqual({t: replays.cap_rounds_pct(game, t) for t in "AB"}, ALIVE["capRoundsPct"])
        edge = replays.UNIT_LIMIT - replays.CAP_MARGIN
        self.assertEqual(replays.cap_rounds_pct(self.game([[0, edge, edge - 1], [1, edge - 1, edge]]), "A"), 50.0)
        summary = replays.game_summary(replays.analyse(replays.load_replay(TRAUMA)), None)  # Trauma: 1,152 tiles
        self.assertEqual((summary["map"]["sizeBand"], summary["teams"]["A"]["capRoundsPct"]), ("1025-2399", 0.0))


class TestSpread(unittest.TestCase):
    # Hand-checked from the moves TestOpening lists: B's only two dragons (ids 1 and 3) end rounds 0-4 at
    # (19,2)/(19,22), (19,1)/(19,21), (19,0)/(18,21), (19,24)/(18,20) and (18,24)/(18,19) on the 25x25 board, so
    # across the wrap they are 5, 5, 4, 4 and 5 apart.
    def test_each_rounds_spread_is_the_wrapped_distance_to_the_nearest_teammate(self) -> None:
        game = replays.analyse(replays.load_replay(TROPHY))
        self.assertEqual([s.alive["B"] for s in game.snapshots[:5]], [2] * 5)
        self.assertEqual([s.spread["B"] for s in game.snapshots[:5]], [5, 5, 4, 4, 5])
        self.assertEqual(replays.mean_nearest([(0, 0), (9, 0), (5, 5)], 10, 10), (1 + 1 + 5) / 3)
        self.assertIsNone(replays.mean_nearest([(0, 0)], 10, 10))

    # Trophy ended in round 133, so it has no late phase; B had two dragons or more for 14 of its mid rounds.
    def test_the_summary_averages_each_phase_over_the_rounds_with_two_dragons(self) -> None:
        game = replays.analyse(replays.load_replay(TROPHY))
        spread = replays.game_summary(game, None)["teams"]["B"]["spread"]
        early = [s.spread["B"] for s in game.snapshots if s.round < 100]
        self.assertEqual(spread["early"], {"mean": round(sum(early) / 100, 2), "rounds": 100})
        self.assertEqual(spread["mid"]["rounds"], 14)
        self.assertIsNone(spread["late"])
        full = replays.game_summary(replays.analyse(replays.load_replay(TRAUMA)), None)["teams"]["A"]["spread"]
        self.assertEqual(set(full), {"early", "mid", "late"})
        self.assertTrue(all(full[p] for p in full))
        row = replays._profile_row("x", [{**replays.game_summary(game, None)["teams"]["B"], "endReason": "x"}])
        self.assertIn(f"| {spread['early']['mean']:.1f}/{spread['mid']['mean']:.1f}/- |", row)


QUEEN_95037 = Path(__file__).resolve().parent / "testdata" / "queen-95037.replay"  # r499, 85 raced pearls, 15 sprints


class TestRaceAndSprints(unittest.TestCase):
    # M78: the pearl race is one set of pearls seen from both sides, so each gap's count mirrors the other team's and
    # the two teams' pearls add up to it.
    def test_each_raced_pearl_is_counted_once_from_both_sides(self) -> None:
        summary = replays.game_summary(replays.analyse(replays.load_replay(QUEEN_95037)), None)
        a, b = (summary["teams"][t]["pearlRace"] for t in "AB")
        for bucket, mirror in zip(replays.RACE_BUCKETS, reversed(replays.RACE_BUCKETS)):
            self.assertEqual(a[bucket]["n"], b[mirror]["n"])
            self.assertEqual(a[bucket]["ate"] + b[mirror]["ate"], a[bucket]["n"])
        self.assertGreater(sum(v["n"] for v in a.values()), 50)
        self.assertIn("level", replays.race_text([summary["teams"]["A"]]))

    # M95: a summary saved before the race went by path holds Manhattan counts; pooled with path ones until
    # online.refresh_summaries re-reads it, it would mix the two, so race_text skips it and says so.
    def test_a_manhattan_race_summary_is_skipped_not_pooled(self) -> None:
        path = {"pearlRace": {"level": {"n": 10, "ate": 5}}, "raceBy": "path"}
        manhattan = {"pearlRace": {"level": {"n": 90, "ate": 90}}}
        self.assertIn("level 50.0% of 10", text := replays.race_text([path, manhattan]))
        self.assertIn("(1 games; 1 older Manhattan summaries skipped)", text)
        self.assertEqual(replays.race_text([manhattan]), "none by path yet (1 older Manhattan summaries skipped)")
        self.assertIsNone(replays.race_text([{}]))

    # M95: the race gap is path steps, not Manhattan. A at (2,1) is 3 from the pearl at (5,1) by Manhattan, B at (9,1)
    # 4, but kelp west of column 3 sends A round the wrap (7 steps); a portal from (2,0)'s north side to (6,0)'s cuts
    # it to 4, level with B.
    def test_the_race_gap_walks_round_kelp_and_through_portals(self) -> None:
        kelp = "".join(f"EDGE {(2 * y + 1) * 11 + 3} 1 -1\n" for y in range(3))
        board = "MAP 10 3\nDRAGON 0 2 2 1 1 1\nDRAGON 1 2 9 1 9 2\n" + kelp
        self.assertEqual(replays._Walker(replays.parse_map(board))._race_gap((5, 1)), 7 - 4)
        ported = replays._Walker(replays.parse_map(board + "EDGE 2 2 0\nEDGE 6 2 0\n"))
        self.assertEqual(ported._race_gap((5, 1)), 0)

    # M77: a sprint that started a head-on is one whose mover died moving into an enemy head on a 2+-step move, so
    # the split is checked against the deaths; the leaders' movement sprints are the rest.
    def test_sprints_split_by_head_on_match_the_deaths_and_add_up(self) -> None:
        for path in (QUEEN_95037, TROPHY, TRAUMA):
            game = replays.analyse(replays.load_replay(path))
            for team in "AB":
                kinds = replays.team_summary(game, team)["sprintKinds"]
                rams = sum(1 for d in game.deaths if d["team"] == team and d.get("movedIntoTeam") not in (None, team)
                           and d.get("of", 1) > 1)
                self.assertEqual(sum(kinds["headOn"].values()), rams)
                self.assertEqual(sum(n for k in kinds.values() for n in k.values()), game.teams[team].sprint_moves)
        kinds = replays.team_summary(replays.analyse(replays.load_replay(QUEEN_95037)), "A")["sprintKinds"]
        self.assertTrue(all(kinds["noHeadOn"].values()))  # A's 13 non-ram sprints have both kinds: 5 in view, 8 not


class TestInPlaceDeaths(unittest.TestCase):
    # M84: rank 1's pumps die where they stand by noValidAction (29 illegal splits: 9, 8 and 7 on three tiles, 5
    # on three more; 25 of them the round after its own split, so split-out parents first), ours as a split-out's
    # 2-long parent by hitWall or hitSelf; E0102's B has 40 of those, only 15 of them the round after its own split
    # (the rest feeds and crashes). A station is a tile with 5+.
    def test_no_valid_action_and_split_out_parents_count_and_five_on_a_tile_make_a_station(self) -> None:
        leader = replays.team_summary(replays.analyse(replays.load_replay(DILEMMA)), "A")
        self.assertEqual(leader["inPlace"], {"deaths": 29, "noValidAction": 4, "splitOutParents": 25,
                                             "unpaidSprint": 0, "stations": 3})
        ours = replays.team_summary(replays.analyse(replays.load_replay(FOLD)), "B")
        self.assertEqual(ours["deaths"]["hitWall"] + ours["deaths"]["hitSelf"], 40)
        self.assertEqual(ours["inPlace"], {"deaths": 15, "noValidAction": 0, "splitOutParents": 15,
                                           "unpaidSprint": 0, "stations": 1})
        self.assertIn("in-place deaths 29.0 a game (noValidAction 4.0, split-out parents 25.0), stations 3.0",
                      replays.crossing_text([leader]))

    # The split-out parents are watch.split_out_parent's, a death on a body included (B's 2 here, where hitWall and
    # hitSelf alone gave 0), and an unpaid sprint step (a dragon too short for its sprint, no pump) is counted apart,
    # where it was one of B's noValidAction in-place deaths.
    def test_split_out_parents_match_watch_and_unpaid_sprint_steps_are_apart(self) -> None:
        import watch
        game = replays.analyse(replays.load_replay(TROPHY_143349))
        turns = watch.watch(TROPHY_143349).turns
        for team, parents in (("A", 1), ("B", 2)):
            self.assertEqual(sum(watch.split_out_parent(d, turns) for d in game.deaths if d["team"] == team), parents)
            self.assertEqual(replays.in_place_deaths(game, team)["splitOutParents"], parents)
        self.assertEqual(replays.in_place_deaths(game, "B"), {"deaths": 47, "noValidAction": 45, "splitOutParents": 2,
                                                              "unpaidSprint": 1, "stations": 0})

    # An unpaid sprint step is never taken (docs/unsw/movement.md), so it crosses no portal: with a portal put on the
    # edge B's r398 dragon 353 died stepping over, that death is still no crossing (the dying step once was one).
    def test_an_unpaid_sprint_step_through_a_portal_is_no_crossing(self) -> None:
        game = replays.analyse(replays.load_replay(TROPHY_143349))
        death = next(d for d in game.deaths if (d["round"], d["id"]) == (398, 353))
        self.assertEqual(death.get("cause"), "unpaidSprintStep")
        edge = replays.edge_of_step(tuple(death["head"]), death["direction"], game.map.width, game.map.height)
        self.assertNotIn(edge, game.map.portals)
        parse = replays.parse_map

        def with_portal(text: str) -> replays.MapInfo:
            info = parse(text)
            info.portals = info.portals | {edge}
            return info

        with patch.object(replays, "parse_map", with_portal):
            ported = replays.analyse(replays.load_replay(TROPHY_143349))
        self.assertIn(edge, ported.map.portals)
        self.assertNotIn((398, 353), ported.teams["B"].crossings)


class TestMeals(unittest.TestCase):
    def test_every_pearl_eaten_is_a_meal_and_a_drop_is_eaten_at_most_once_a_pearl(self) -> None:
        for path in (FOLD, TROPHY):
            game = replays.analyse(replays.load_replay(path))
            self.assertTrue(game.checks.as_dict()["ok"])
            self.assertEqual(len(game.meals), sum(t.pearls_eaten for t in game.teams.values()))
            eaten: dict[int, int] = {}
            for meal in game.meals:
                if meal["death"] is not None:
                    eaten[meal["death"]] = eaten.get(meal["death"], 0) + 1
                    self.assertLessEqual(game.deaths[meal["death"]]["round"], meal["round"])
            for index, count in eaten.items():
                self.assertLessEqual(count, -(-game.deaths[index]["length"] // 2))  # ceil(L/2) pearls a death
        # Trophy's first merge-looking meals are fights: A's king ate teammates' head-on drops in r39 and r41.
        game = replays.analyse(replays.load_replay(TROPHY))
        drops = [(m["round"], d["reason"]) for m, d in replays.king_meals(game, "A") if d and d["team"] == "A"]
        self.assertEqual(drops, [(39, "hitHeadToHead"), (41, "hitHeadToHead")])

    # B's king at the end is dragon 44 (born r443), split from 42 (r410), from 38 (r382), from 23 (r253) and so on
    # back to starting dragon 3: its meals are each ancestor's until its child's birth, among them the kelp and
    # own-body deaths of teammates beside it (the fold), 38's own as a 3-long in r448.
    def test_the_kings_meals_follow_it_back_through_the_dragons_it_split_from(self) -> None:
        game = replays.analyse(replays.load_replay(FOLD))
        meals = replays.king_meals(game, "B")
        line = {44: (443, 500), 42: (410, 443), 38: (382, 410), 23: (253, 382)}
        for meal, _ in meals:
            if meal["id"] in line:
                low, high = line[meal["id"]]
                self.assertTrue(low <= meal["round"] < high, meal)
        self.assertEqual(sum(m["id"] == 23 for m, _ in meals), sum(m["id"] == 23 for m in game.meals))  # all pre-split
        merged = [(m["round"], d["id"], d["length"], d["reason"]) for m, d in meals
                  if d and d["team"] == "B" and m["round"] > 396]
        self.assertEqual(merged, [(398, 33, 3, "hitWall"), (400, 33, 3, "hitWall"), (407, 39, 2, "hitWall"),
                                  (409, 36, 2, "hitWall"), (448, 38, 3, "hitWall"), (450, 38, 3, "hitWall")])
        # A team with no dragon left has no king.
        early = replays.analyse(replays.load_replay(TROPHY))
        self.assertEqual(replays.king_meals(early, "B"), [])

    # Review M52 on E0102's fold game (to r499): each team's king at the fold is the king line's dragon's body at the
    # start of r397 (watch's frames), and its meals less its splits, sprint steps and handovers after take it to its
    # length at the end. Trophy ended in an elimination, so neither side has one.
    def test_the_king_at_the_fold_is_the_lines_body_then_and_adds_up_to_its_end(self) -> None:
        import watch
        w = watch.watch(FOLD)
        for team in "AB":
            king = replays.king_at_fold(w.game, team)
            line = replays.king_line(w.game, team)
            dragon = next(d for d, start, until in line if start <= replays.KING_ROUND < until)
            self.assertEqual(king["length"], len(w.frames[replays.KING_ROUND + 1].dragons[dragon.id][1]))
            end = king["length"] + king["meals"] - king["splitOff"] - king["sprints"] - king["handedOn"]
            self.assertEqual(end, w.game.standing[team]["longest"])
            # M99: its build from the end of r250 adds up to its r396 length, its meals split by where they came from.
            b = king["build"]
            dragon = next(d for d, start, until in line if start <= replays.BUILD_ROUND < until)
            self.assertEqual(b["length"], len(w.frames[replays.BUILD_ROUND + 1].dragons[dragon.id][1]))
            self.assertEqual(b["length"] + b["spawned"] + b["teammateDrops"] + b["enemyDrops"] - b["splitOff"]
                             - b["sprints"] - b["handedOn"], king["length"])
        self.assertEqual(replays.king_at_fold(w.game, "B")["meals"],
                         sum(m["round"] > replays.KING_ROUND for m, _ in replays.king_meals(w.game, "B")))
        trophy = replays.analyse(replays.load_replay(TROPHY))
        self.assertEqual([replays.king_at_fold(trophy, t) for t in "AB"], [None, None])
        self.assertIsNone(replays.game_summary(trophy, None)["teams"]["A"]["king"])


class TestOpening(unittest.TestCase):
    def opening(self, end: int, team: str = "B") -> dict:
        with patch.object(replays, "OPENING_END", end):
            game = replays.analyse(replays.load_replay(TROPHY))
        self.assertTrue(game.checks.as_dict()["ok"])
        return replays.opening(game, team)

    # Hand-checked with `tools/watch.py dragon` on ids 1 and 3, B's two dragons until its first split in round 10.
    # Rounds 0-4: id 1 goes W N N N W from (20,2), wrapping from y 0 to 24, id 3 W N W N N; no pearl in view.
    def test_first_rounds_match_the_hand_count(self) -> None:
        o = self.opening(5)
        self.assertEqual((o["moves"], o["pearlInViewPct"], o["blindMoves"]), (10, 0.0, 10))
        self.assertEqual(o["blindStraightPct"], 37.5)  # 3 of the 8 moves after a first one kept its direction
        self.assertEqual(o["blindDirPct"], {"north": 60.0, "east": 0.0, "south": 0.0, "west": 40.0})
        # Rounds 5-6: id 3 sees the pearl at (20,16) from (18,19) and (18,18); id 1 goes W (straight) then N.
        o = self.opening(7)
        self.assertEqual((o["moves"], o["pearlInViewPct"], o["blindMoves"], o["blindStraightPct"]),
                         (14, 14.3, 12, 40.0))

    def test_map_seen_counts_each_tile_once_across_the_wrap(self) -> None:
        # Round 0: 7x7 around (20,2) and (20,22) share rows 24 and 0 across the edge, 98 - 14 = 84 of 625 tiles.
        self.assertEqual(self.opening(1)["mapSeenPct"], 13.4)

    def test_the_summary_reports_the_first_100_rounds(self) -> None:
        summary = replays.game_summary(replays.analyse(replays.load_replay(TROPHY)), None)
        opening = summary["teams"]["B"]["behaviour"]["opening"]
        self.assertEqual(opening, {"moves": 492, "pearlInViewPct": 44.1, "mapSeenPct": 100.0, "blindMoves": 275,
                                   "blindStraightPct": 64.7,
                                   "blindDirPct": {"north": 29.1, "east": 17.1, "south": 28.7, "west": 25.1}})


class TestResultChosen(unittest.TestCase):
    # MISSES 55: an audit tallied online replays of which a third were kept because we lost, as if a sample.
    def test_replays_kept_because_lost_are_named_and_the_rest_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            version = Path(directory) / "online-replays/8213"
            version.mkdir(parents=True)
            (version / "index.json").write_text(json.dumps({"games": [
                {"id": 1, "replay": str(version / "1.replay")},
                {"id": 2, "replay": str(version / "2.replay"), "kept": "loss"},
                {"id": 3, "replay": str(version / "3.replay"), "dev": True, "dev_wins_pruned": True}]}))
            losses = Path(directory) / "runs/E0168/losses"
            losses.mkdir(parents=True)
            study = Path(directory) / "study-replays"  # a result-blind folder with no index.json
            study.mkdir()
            # 3: a dev loss left on disk after prune deleted its version's dev wins' files (prune.mark_dev_rows).
            paths = [version / "1.replay", version / "2.replay", version / "3.replay", losses / "0081-x-aB.replay",
                     study / "9.replay"]
            self.assertEqual(replays.result_chosen(paths),
                             [version / "2.replay", version / "3.replay", losses / "0081-x-aB.replay"])
            self.assertIn("3 of these 5 replays are no result-blind sample", replays.chosen_note(paths))
            self.assertIsNone(replays.chosen_note([version / "1.replay", study / "9.replay"]))


class TestLibraryGames(unittest.TestCase):
    # The loop's replay library (loop/replay_library.py builds it): ours whatever the opponent, decoy flag kept; the
    # leaders' possible decoys never; and a pruned, unreadable or file-less row skipped rather than ending the read.
    def test_counting_games_with_readable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            files = {name: folder / f"{name}.replay" for name in ("ranked", "decoy", "leader", "fake", "pruned", "shut")}
            for name, path in files.items():
                if name != "pruned":
                    path.write_bytes(b"")
            files["shut"].chmod(0)
            row = lambda name, **extra: {"replay": str(files[name]), "id": name, "side": "A", **extra}
            path = folder / "library.json"
            path.write_text(json.dumps({
                "online": {"games": [row("ranked", decoy_risk=False), row("decoy", decoy_risk=True),
                                     row("pruned", decoy_risk=False), row("shut", decoy_risk=False), {"id": "none"}]},
                "leaders": {"games": [row("leader")], "decoy_risk": [row("fake", decoy_risk=True)]}}))
            games = replays.library_games(path=path)
            self.assertEqual([(g["part"], g["id"], g.get("decoy_risk")) for g in games],
                             [("online", "ranked", False), ("online", "decoy", True), ("leaders", "leader", None)])
            self.assertEqual([g["id"] for g in replays.library_games("leaders", path=path)], ["leader"])


# One process importing both replay schemas, tools/replay.capnp and referee/replay.capnp (one id, different files),
# once in each order: capnp.load's shared parser aborted it (MISSES 97: loop/health.py importing a module that imports
# replays aborted five loop test modules). A child process, since the failure is an abort, not an exception.
class TestBesideTheReferee(unittest.TestCase):
    def test_both_schemas_load_in_one_process_in_either_order(self) -> None:
        tools = Path(__file__).resolve().parent
        script = (f"import sys; sys.path[:0] = [{str(tools)!r}, {str(tools.parent / 'referee')!r}]\n"
                  "for name in sys.argv[1:]: __import__(name)\n"
                  "import replays, run\n"
                  f"print(len(replays.load_replay(__import__('pathlib').Path({str(TROPHY)!r})).events) > 0)\n")
        for order in (["replays", "run"], ["run", "replays"]):
            child = subprocess.run([sys.executable, "-c", script, *order], capture_output=True, text=True, timeout=120)
            self.assertEqual((child.returncode, child.stdout.strip()), (0, "True"), (order, child.stderr[-500:]))


if __name__ == "__main__":
    unittest.main()
