"""Focused maintenance safety tests, without network, agents, installs or games."""

import testhome  # before anything that reads live paths
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import contextlib
import io
import digest
import discord_teams
import driver
import ladder_maps
import health
import mapideas
import sweep
import native
import reflect
import risers

import maintenance

TESTDATA = Path(__file__).resolve().parent / "testdata"
LATEST = (TESTDATA / "discord-latest-sample.md").read_text()  # the head of a real latest.md, 2026-09-26 03:36 UTC


class TestMaintenance(unittest.TestCase):
    # The tick's report workers would run tools/my_messages.py and launch claude; tests of them patch them back.
    def setUp(self):
        for module in (digest, reflect):
            worker = patch.object(module, "tick", return_value=None)
            worker.start()
            self.addCleanup(worker.stop)

    def test_first_failed_upgrade_preserves_a_validated_isolated_previous_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "maintenance.json").write_text('{"available":"1.1.0"}')
            def prepare(repo, state, version):
                if version == "1.1.0":
                    raise RuntimeError("new engine incompatible")
                return {"version": "1.0.0", "python": "/isolated/1.0.0/python"}
            with patch.object(maintenance, "prepare_toolchain", side_effect=prepare), \
                    patch.object(maintenance.importlib.metadata, "version", return_value="1.0.0"):
                result = maintenance.apply_pending(state, state, False)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(maintenance.toolchain(state)["python"], "/isolated/1.0.0/python")

    def test_study_weights_the_top_two_and_skips_us(self):
        board = [{"id": i, "rank": r} for i, r in ((7, 3), (157, 1), (9, 2), (1, 4), (2, 5), (3, 6), (4, 7))]
        plan = maintenance.study_plan(board, 157)
        self.assertEqual([team["id"] for team, _, _ in plan], [9, 7, 1, 2, 3, 4][:len(maintenance.STUDY_KEEP)])
        keep = [k for _, k, _ in plan]
        self.assertGreater(min(keep[:2]), max(keep[2:]))  # ranks 1-2 studied most
        self.assertLessEqual(sum(new for _, _, new in plan), 25)  # the hour's download budget

    def test_team_games_keep_the_latest_with_the_side_played(self):
        draw = {"ranked": True, "requestedAt": "2026-09-25T14:00:00.000Z"}  # real format, as the site stamps autoscrims
        rows = [{"match": 1, "teamAId": 5, "teamBId": 6, **draw}, {"match": 2, "teamAId": 6, "teamBId": 5, **draw},
                {"match": 3, "teamAId": 7, "teamBId": 8, **draw}, {"match": 4, "teamAId": 5, "teamBId": 9, **draw},
                # an unranked challenge, where a team may play a decoy bot
                {"match": 5, "teamAId": 5, "teamBId": 157, "ranked": False, "requestedAt": "2026-09-25T16:00:00.000Z"}]
        self.assertEqual([(r["match"], side) for r, side in maintenance.team_games(rows, 5, 2)], [(2, "B"), (4, "A")])

    # REFLECT 2026-09-28 14:51 F29: the study read each leader's latest 10 ranked games, whichever version played them.
    def test_the_study_reads_every_saved_ranked_game_of_a_teams_current_version(self):
        def row(match, sub, ranked=True):
            return {"match": match, "teamAId": 5, "teamBId": 6, "submissionAId": sub, "submissionBId": 60,
                    "ranked": ranked}
        rows = [row(1, 50), *[row(m, 51) for m in range(2, 16)], row(16, 51, ranked=False)]
        self.assertEqual([r["match"] for r, _ in maintenance.version_games(rows, 5, 10)], list(range(2, 16)))
        with patch.object(maintenance, "STUDY_MAX", 3):
            self.assertEqual([r["match"] for r, _ in maintenance.version_games(rows, 5, 10)], [13, 14, 15])
        # MISSES 77: from 2026-09-28 the site names no submission, so the latest games name none. Before, the study
        # kept reading the last named version (2-15) and never the new games; now the games after the last named
        # one, or the latest `keep` while they are fewer (by time: possibly more than one version).
        unnamed = lambda match: {"match": match, "teamAId": 7, "teamBId": 5, "submissionAId": None,
                                 "submissionBId": None, "ranked": True}
        rows += [unnamed(17), unnamed(18)]
        self.assertEqual([r["match"] for r, _ in maintenance.version_games(rows, 5, 10)], [*range(8, 16), 17, 18])
        self.assertEqual([r["match"] for r, _ in maintenance.version_games(rows, 5, 2)], [17, 18])
        self.assertEqual([r["match"] for r, _ in maintenance.version_games(rows, 7, 10)], [17, 18])  # none named

    # Method M69: study.md pooled each team's means over whatever maps its games fell on. Real games
    # (testdata/study-matched-sample.json): rank 1's 10 ranked games on 6 maps and 3 a map of ours on all 10.
    def test_the_map_matched_block_compares_each_stat_on_the_maps_both_played_only(self):
        sample = json.loads((TESTDATA / "study-matched-sample.json").read_text())
        games = lambda rows: [({}, "B", {"map": {"name": r["map"]}, "teams": {"B": r["facts"]}}) for r in rows]
        leader, ours = games(sample["leader"]), games(sample["ours"])
        text = maintenance.matched_text([("rank 1 (team 306)", leader)], None, ours)
        line = next(line for line in text.splitlines() if line.startswith("- rank 1 (team 306)"))
        self.assertIn("on the 6 maps both played (games theirs/ours: Autarky 2/3, Portals 2/3, Prisoners Dilemma 1/3, "
                      "Schooltime 1/3, Trauma 2/3, Trophy 2/3)", line)
        # Each map counts once for each side: our means on its 6 maps, not over all 10 maps we played.
        maps = {r["map"] for r in sample["leader"]}
        per_map = lambda rows, m: [r["facts"]["pearlsEaten"] for r in rows if r["map"] == m]
        theirs = sum(sum(v) / len(v) for m in maps for v in [per_map(sample["leader"], m)]) / len(maps)
        us = sum(sum(v) / len(v) for m in maps for v in [per_map(sample["ours"], m)]) / len(maps)
        self.assertIn(f"pearls eaten a game {theirs:.1f} v our {us:.1f} ({theirs - us:+.1f})", line)
        pooled = sum(r["facts"]["pearlsEaten"] for r in sample["ours"]) / len(sample["ours"])
        self.assertNotAlmostEqual(us, pooled, places=0)  # the pooled mean mixed in maps rank 1 never played
        self.assertIn("pearls per 100 dragon-turns", line)
        self.assertNotIn("rounds at the unit ceiling", line)  # summaries from before capRoundsPct have none
        devil = [g for g in ours if g[2]["map"]["name"] == "Devil"]
        self.assertIn("no map both played", maintenance.matched_text([("rank 2 (team 70)", leader[:1])], None, devil))
        self.assertIn("No games of ours to match yet", maintenance.matched_text([("rank 1", leader)], None, []))

    # M80, M84 on two real games, each side A as the leader and side B as us: Queen Of Spades 95037 (A crossed 18 times,
    # 8 of its crossers dying within 10 rounds; B 22 and 12) and rank 1's Prisoners Dilemma 477675 (A 18 and 0, 29
    # in-place deaths at 3 stations; B 2 and 0, one split-out parent). A map's share is its crossings' (ratio of means).
    def test_the_map_matched_block_has_portal_crossings_and_in_place_deaths(self):
        import replays
        games = [replays.game_summary(replays.analyse(replays.load_replay(TESTDATA.parent.parent / "tools/testdata" / name)), None)
                 for name in ("queen-95037.replay", "dilemma-477675.replay")]
        text = maintenance.matched_text([("rank 1", [({}, "A", g) for g in games])], None, [({}, "B", g) for g in games])
        self.assertIn("portal crossings a game 18.0 v our 12.0 (+6.0); % of crossings dying within 10 rounds 22.2 v our "
                      "27.3 (-5.1); in-place deaths a game 15.5 v our 0.5 (+15.0); dead-end stations a game 1.5 v our "
                      "0.0 (+1.5).", text)

    def test_mean_facts_average_numbers_and_count_missing_as_zero(self):
        facts = [{"won": True, "pearls": 10, "sonar": {"hits": {"kelp": 4}}, "map": "x"},
                 {"won": False, "pearls": 20, "sonar": {"hits": {}}, "map": "y"}]
        self.assertEqual(maintenance.mean_facts(facts), {"pearls": 15.0, "sonar": {"hits": {"kelp": 2.0}}})

    def test_round_400_lengths_average_only_the_games_that_got_there(self):
        # Real summaries: the benchmark game went to the round limit (A 31 long of 522 at r400); the Trophy game
        # ended in round 133, so its at400 is None, which must not count as a length of 0.
        reached = json.loads((TESTDATA / "study-summary-benchmark.json").read_text())["teams"]["A"]
        ended = json.loads((TESTDATA / "study-summary-trophy.json").read_text())["teams"]["A"]
        self.assertIsNone(ended["at400"])
        means = maintenance.mean_facts([reached, ended])
        self.assertEqual(means["at400"], {"longest": 31.0, "total": 522.0, "n": 1})
        self.assertEqual(means["end"]["longest"], (reached["end"]["longest"] + ended["end"]["longest"]) / 2)
        self.assertNotIn("n", maintenance.mean_facts([reached, reached])["at400"])

    def test_failed_leader_fetch_keeps_the_last_study_with_its_age_and_our_current_games(self):
        import ladder
        summary = {"checks": {"ok": True}, "map": {"name": "Devil"},
                   "teams": {s: {"won": s == "A", "pearlsEaten": 50 if s == "A" else 9} for s in "AB"}}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "study-replays"
            folder.mkdir()
            (folder / "index.jsonl").write_text('{"match":1,"teamAId":9,"teamBId":4,"ranked":true,'
                                                '"requestedAt":"2026-09-25T14:00:00.000Z"}\n')
            (folder / "1.replay").write_bytes(b"")
            ours = state / "ours.replay"
            ours.write_bytes(b"")
            (state / "online.json").write_text(json.dumps({"submission_id": 6111, "games": [
                {"submission_id": 6111, "side": "B", "replay": str(ours)},
                {"submission_id": 5143, "side": "A", "replay": str(ours)}]}))
            def get(path):
                return {"team": {"id": 157}} if path == "/team" else [{"id": 9, "rank": 1, "name": "SSS"}]
            def run(args, cwd, **_):
                return json.dumps(summary)
            # Last hour's teams.json saw team 9 as "S3": its title keeps the old name beside the new one.
            maintenance.discord_teams.path_in(state).parent.mkdir()
            maintenance.discord_teams.refresh(state, 1790400000, lambda path: [{"id": 9, "rank": 1, "name": "S3"}],
                                              sleep=lambda _: None)
            maintenance.discord_teams.refresh(state, 1790403600, lambda path: [{"id": 9, "rank": 1, "name": "SSS"}],
                                              sleep=lambda _: None)
            with patch.object(ladder, "get", side_effect=get), patch.object(maintenance, "command", side_effect=run):
                maintenance.study(Path(__file__).resolve().parent.parent, state)
            good = (state / "study.md").read_text()
            self.assertIn('## Rank 1: SSS (team 9, formerly "S3", Elo None)', good)
            self.assertIn("1 games studied, 1 won", good)
            self.assertIn("Us: submission 6111", good)
            self.assertNotIn("Stale", good)
            # Both played Devil: rank 1's side A (50 pearls) against our side B (9), map-matched (M69).
            self.assertIn("- rank 1 (team 9) on the 1 maps both played (games theirs/ours: Devil 1/1): pearls eaten a "
                          "game 50.0 v our 9.0 (+41.0)", good)
            fetched = json.loads((state / "study.json").read_text())["fetched_at"]
            stamp = maintenance.time.strftime("%Y-%m-%d %H:%M UTC", maintenance.time.gmtime(fetched))
            self.assertIn(f"Leaders fetched {stamp}; this file written ", good)

            # An hour later the team page fails to parse: the leader block stays, dated; our games are still there.
            report = json.loads((state / "study.json").read_text())
            report.update(attempt_at=0, fetched_at=report["fetched_at"] - 3 * 3600)
            (state / "study.json").write_text(json.dumps(report))
            def broken(args, cwd, **_):
                if "fetch" in args:
                    raise RuntimeError("replays.py fetch failed: ...\njson.decoder.JSONDecodeError: Extra data")
                return json.dumps(summary)
            with patch.object(ladder, "get", side_effect=get), patch.object(maintenance, "command", side_effect=broken):
                maintenance.study(Path(__file__).resolve().parent.parent, state)
            stale = (state / "study.md").read_text()
            self.assertIn("Stale: this hour's leader fetch failed (json.decoder.JSONDecodeError: Extra data)", stale)
            self.assertIn("the leader blocks below are 3.0 h old", stale)
            stamp = maintenance.time.strftime("%Y-%m-%d %H:%M UTC", maintenance.time.gmtime(report["fetched_at"]))
            self.assertIn(f"Leaders fetched {stamp};", stale)
            self.assertIn("Rank 1: SSS", stale)
            self.assertIn("Us: submission 6111 (online games)\n\n1 games studied, 0 won", stale)
            self.assertTrue(json.loads((state / "study.json").read_text())["summaries"])

    def test_a_study_block_gives_the_teams_pearl_race_and_its_sprints_by_kind(self):
        # M78 and M77: the leaders' pearl contests and movement sprints were invisible in study.md. A real replay
        # (tools/testdata/queen-95037.replay, r499): side A's 13 sprints without a head-on, 5 with an enemy in view.
        import sys
        sys.path.insert(0, str(TESTDATA.parent.parent / "tools"))
        import replays
        summary = replays.game_summary(replays.analyse(replays.load_replay(TESTDATA.parent.parent / "tools/testdata"
                                                                          / "queen-95037.replay")), None)
        text = maintenance.study_section("Rank 1", [({}, "A", summary)], [])
        self.assertIn(f"Pearl race (pearls both teams saw, first seen by both in r100-r299, by its nearest head's gap "
                      f"to the other team's then; its share eaten): {replays.race_text([summary['teams']['A']])}.", text)
        self.assertIn("Sprints a game, head-ons 1.00, no head-on with an enemy head in view 5.00, no head-on and none "
                      "in view 8.00 (1 games).", text)

    def test_a_study_block_gives_the_teams_deaths_by_how_they_came_about(self):
        # M56: study.md's deaths were end reasons only (noValidAction, hitHeadToHead), a count MISSES 64 says is no
        # diagnosis. A real replay read by the real doom tool, cached so the next study reads it no more, within a
        # budget of new reads a study.
        import sys
        sys.path.insert(0, str(TESTDATA.parent.parent / "tools"))
        import replays
        repo, file = TESTDATA.parent.parent, TESTDATA.parent.parent / "tools/testdata/queen-95037.replay"
        dooms, used = {}, set()
        kinds = maintenance.study_dooms(repo, file, dooms, "k", used, [0])
        self.assertEqual(kinds, dooms["k"])
        with patch.object(maintenance, "command", side_effect=AssertionError("read again")):
            self.assertEqual(maintenance.study_dooms(repo, file, dooms, "k", used, [0]), kinds)
            self.assertIsNone(maintenance.study_dooms(repo, file, dooms, "new", used,
                                                      [maintenance.STUDY_DOOM_READS]))
        summary = replays.game_summary(replays.analyse(replays.load_replay(file)), None)
        text = maintenance.study_section("Rank 1", [({}, "A", {**summary, "doom": kinds})], [])
        deaths = sum(kinds["A"].values())
        own = sum(n for kind, n in kinds["A"].items() if kind.split(" ")[-1] in ("ate", "portal", "deeper"))
        self.assertIn(f"Deaths by how they came about (tools/watch.py doom, M56; 1 of 1 games read, {deaths:.1f} a "
                      f"game): own move {own:.2f}/g, {100 * own / deaths:.0f}%, split-out ", text)

    def test_our_study_block_is_our_ladder_games_not_the_dev_teams(self):
        # Before: our latest 10 online games, which dev battles back to back make all dev games, so study-history's
        # gap rows compared the leaders with our dev games from this commit on.
        summary = json.loads((TESTDATA / "study-summary-trophy.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            replay = state / "ours.replay"
            replay.write_bytes(b"")
            games = [{"id": n, "submission_id": 7676, "side": "A", "opponent": 262, "replay": str(replay)}
                     for n in range(4)]
            games += [{"id": 100 + n, "submission_id": 7676, "side": "A", "opponent": 752 if n % 2 else 545,
                       "replay": str(replay), **({"dev": True} if n % 2 else {})} for n in range(20)]
            (state / "online.json").write_text(json.dumps({"submission_id": 7676, "games": games,
                                                          "dev": {"teams": [{"id": 545}]}}))
            _, sid, _, studied = maintenance.our_section(state, lambda path: summary)
            self.assertEqual((sid, studied), (7676, 4))

    def test_two_submissions_of_history_flag_a_gap_no_idea_is_working_on(self):
        import ladder
        # A real summary (tools/testdata/trophy-155732.replay): the leader plays side B, SSS, and we play side A.
        summary = json.loads((TESTDATA / "study-summary-trophy.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "study-replays"
            folder.mkdir()
            games = range(1, maintenance.GAP_MIN_GAMES + 1)
            (folder / "index.jsonl").write_text("".join(f'{{"match":{m},"teamAId":4,"teamBId":9,"ranked":true}}\n'
                                                        for m in games))
            for m in games:
                (folder / f"{m}.replay").write_bytes(b"")
            ours = state / "ours.replay"
            ours.write_bytes(b"")
            # Real rows, the last one torn as by a rewrite read halfway (driver.write_ideas isn't atomic).
            (state / "ideas.jsonl").write_text((TESTDATA / "ideas-sample.jsonl").read_text() + '{"id": "I2')
            def get(path):
                return {"team": {"id": 157}} if path == "/team" else [{"id": 9, "rank": 1, "name": "SSS"}]
            for submission in (6542, 6609):
                (state / "online.json").write_text(json.dumps({"submission_id": submission, "games": [
                    {"submission_id": submission, "side": "A", "replay": str(ours)} for _ in games]}))
                if (state / "study.json").exists():  # the next hour
                    report = json.loads((state / "study.json").read_text())
                    (state / "study.json").write_text(json.dumps({**report, "attempt_at": 0}))
                with patch.object(ladder, "get", side_effect=get), \
                        patch.object(maintenance, "command", side_effect=lambda *a, **k: json.dumps(summary)):
                    maintenance.study(Path(__file__).resolve().parent.parent, state)
                text = (state / "study.md").read_text()
                if submission == 6542:
                    self.assertIn("## Gaps unchanged", text)
                    self.assertIn("Not measurable yet", text)
                    with (state / "study-history.jsonl").open("a") as f:
                        f.write('{"at": "2026-09-26T0')  # a torn append must not stop the next hour's study
            history = maintenance.read_jsonl(state / "study-history.jsonl")
            self.assertEqual([(row["submission"], row["us_games"]) for row in history], [(6542, 5), (6609, 5)])
            self.assertEqual(history[-1]["us"]["pearl in view % of moves, r0-99"], 40.6)
            self.assertEqual(history[-1]["leaders"][0]["stats"]["blind moves keeping their direction %, r0-99"], 64.7)
            self.assertLess(text.index("## Gaps unchanged"), text.index("## Rank 1: SSS"))
            flags = [line for line in text.splitlines() if line.startswith("- **")]
            # Alive at r100 (27 vs 5) is cited by no idea. Keeping direction (35.9 vs 64.7) is cited by I208, queued
            # at priority 1, and early pearls by I109 at priority 3, so they are being worked on; the rest are < 5.
            self.assertEqual(flags, ["- **dragons alive at r100**: us 27 vs rank 1 5 at submission 6609 (gap -22.0); "
                                     "-22.0 at 6542. Ideas citing it: none (none mentions 'alive at r100' / "
                                     "'alive at 100' / 'aliveat100')."])
            # A bug in the flags still leaves a fresh study.md, with the error in their place.
            report = json.loads((state / "study.json").read_text())
            (state / "study.json").write_text(json.dumps({**report, "attempt_at": 0}))
            with patch.object(ladder, "get", side_effect=get), patch.object(maintenance, "gaps_text",
                                                                            side_effect=KeyError("rank")), \
                    patch.object(maintenance, "command", side_effect=lambda *a, **k: json.dumps(summary)):
                maintenance.study(Path(__file__).resolve().parent.parent, state)
            text = (state / "study.md").read_text()
            self.assertIn("## Gaps unchanged\n\nThe gap flags could not be computed: KeyError: 'rank'", text)
            self.assertIn("## Rank 1: SSS", text)

    def test_the_dev_benchmark_is_studied_from_its_side_of_our_sweeps_and_joins_the_gaps(self):
        import ladder
        # A real benchmark game (online-replays/6609/249596.replay, Big Empty): we play A and win, dev test 1 plays B.
        summary = json.loads((TESTDATA / "study-summary-benchmark.json").read_text())
        bench = {"id": 545, "name": "dev test 1 :P", "elo": 1973, "why": "highest-rated dev team, none named frontier"}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            replay = state / "bench.replay"
            replay.write_bytes(b"")

            def game(i: int, submission: int, dev: int = 4525, opponent: int = 545) -> dict:
                return {"id": i, "submission_id": submission, "opponent": opponent, "opponent_submission_id": dev,
                        "side": "A", "map_id": 3, "map_hash": None, "ranked": False,
                        "requested_at": f"2026-09-25T22:26:{i:02d}.889Z", "replay": str(replay)}
            old = [game(i, 6518) for i in range(1, maintenance.GAP_MIN_GAMES + 1)]
            index = state / "online-replays/6518/index.json"
            index.parent.mkdir(parents=True)
            # An older dev submission, and a game against a ladder team, are not the benchmark's current bot.
            index.write_text(json.dumps({"games": [game(0, 6518, dev=4400), *old, game(9, 6518, opponent=801)]}))
            def get(path):
                return {"team": {"id": 157}} if path == "/team" else [{"id": 9, "rank": 1, "name": "SSS"}]
            for submission, games in ((6518, old), (6609, [game(i, 6609) for i in range(11, 11 + len(old))])):
                # online.json's current games are in that version's index too once fetched: each counts once. Our
                # block and its gap rows are our ladder games (team 801 here), never the benchmark games.
                ladder_games = [game(100 + i, submission, opponent=801) for i in range(maintenance.GAP_MIN_GAMES)]
                (state / "online.json").write_text(json.dumps({"submission_id": submission,
                                                               "games": games + ladder_games,
                                                               "benchmark": {"team": bench, "records": []}}))
                if (state / "study.json").exists():
                    report = json.loads((state / "study.json").read_text())
                    (state / "study.json").write_text(json.dumps({**report, "attempt_at": 0}))
                with patch.object(ladder, "get", side_effect=get), \
                        patch.object(maintenance, "command", side_effect=lambda *a, **k: json.dumps(summary)):
                    maintenance.study(Path(__file__).resolve().parent.parent, state)
            text = (state / "study.md").read_text()
            self.assertIn("## Dev benchmark: dev test 1 :P (team 545, Elo 1973, dev), submission 4525\n\n"
                          "10 games studied, 0 won; maps: Big Empty.\nIts side of our unranked benchmark sweeps, "
                          "against our submissions 6518, 6609. Its submissions we have played (games): 4400 (1), "
                          "4525 (10); only the newest is studied.", text)
            self.assertLess(text.index("## Rank 1: SSS"), text.index("## Dev benchmark"))
            self.assertLess(text.index("## Dev benchmark"), text.index("## Us: submission 6609"))
            history = maintenance.read_jsonl(state / "study-history.jsonl")
            self.assertEqual([(row["dev"]["submission"], row["dev"]["games"]) for row in history],
                             [(4525, 5), (4525, 10)])
            self.assertEqual(history[-1]["dev"]["stats"]["map seen % by r100"], 95.7)  # side B's, not ours (54.6)
            # Rank 1 has no games, so the dev benchmark alone is the reference.
            flags = [line for line in text.splitlines() if line.startswith("- **map seen")]
            self.assertEqual(len(flags), 1)
            self.assertTrue(flags[0].startswith("- **map seen % by r100**: us 54.6 vs dev 545 95.7 at submission 6609 "
                                                "(gap +41.1); +41.1 at 6518. Ideas citing it: none"), flags[0])

    def test_the_dev_benchmark_block_leaves_out_replays_kept_only_because_we_lost(self):
        # Version 8023's real games against dev test 1 (online-dev-caps-sample.json): those saved in its replay
        # sample, and its other losses saved as loss replays (online.DEV_LOSS_REPLAYS). Studying the latest
        # DEV_KEEP with a file would otherwise study only games the benchmark won.
        sample = json.loads((TESTDATA / "online-dev-caps-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            games = []
            for g in json.loads(json.dumps(sample["games"]).replace("{home}", str(state))):
                if g["opponent"] != 545:
                    continue
                if "replay" not in g and g["winner"] != g["side"]:
                    g = {**g, "replay": str(state / f"online-replays/8023/{g['id']}.replay"), "kept": "loss",
                         "summary": {"result": {"winner": g["winner"]}}}
                if "replay" in g:
                    Path(g["replay"]).parent.mkdir(parents=True, exist_ok=True)
                    Path(g["replay"]).write_text("x")
                    games.append(g)
            (state / "online-replays/8023/index.json").write_text(json.dumps({"games": games}))
            (state / "online.json").write_text(json.dumps({"submission_id": 8023, "benchmark": {"team": {
                "id": 545, "name": "dev test 1 :P", "elo": 1936}}}))
            summary = json.loads((TESTDATA / "study-summary-benchmark.json").read_text())  # a real benchmark game's
            studied = []
            maintenance.dev_section(state, lambda path: studied.append(path.stem) or summary)
            sampled = [str(g["id"]) for g in games if "kept" not in g]
            self.assertGreaterEqual(len(sampled), maintenance.DEV_KEEP)  # 37 in the sample's first 100 games
            self.assertGreater(len(games), len(sampled))  # the loss replays are there
            self.assertEqual(studied, sampled[-maintenance.DEV_KEEP:])
            # Once 8023 is old, prune deletes its dev wins' files and marks its dev rows (prune.mark_dev_rows): the
            # files left are losses, so none is studied, where the unmarked sample's files were all losses before.
            for g in games:
                g["dev_wins_pruned"] = True
                if g["summary"]["result"]["winner"] == g["side"]:
                    Path(g["replay"]).unlink()
            (state / "online-replays/8023/index.json").write_text(json.dumps({"games": games}))
            studied.clear()
            maintenance.dev_section(state, lambda path: studied.append(path.stem) or summary)
            self.assertEqual(studied, [])

    def test_the_dev_benchmark_block_studies_its_games_after_the_site_stopped_naming_its_submission(self):
        # MISSES 77: newest was the latest *named* submission, so every game after 2026-09-28 19:30 AEST (None) was
        # left out of the block for good.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/9918"
            folder.mkdir(parents=True)
            games = [{"id": n, "submission_id": 9918, "opponent": 545, "side": "A",
                      "opponent_submission_id": 9371 if n < 4 else None,
                      "requested_at": f"2026-09-28T0{n}:00:00Z", "replay": str(folder / f"{n}.replay")}
                     for n in range(1, 7)]
            for g in games:
                Path(g["replay"]).write_text("x")
            (folder / "index.json").write_text(json.dumps({"games": games}))
            (state / "online.json").write_text(json.dumps({"submission_id": 9918, "benchmark": {"team": {
                "id": 545, "name": "dev test 1 :P", "elo": 1936}}}))
            summary = json.loads((TESTDATA / "study-summary-benchmark.json").read_text())
            studied = []
            text, stats = maintenance.dev_section(state, lambda path: studied.append(path.stem) or summary)
            self.assertEqual(studied, ["4", "5", "6"])
            self.assertEqual((stats["submission"], stats["games"]), (None, 3))
            self.assertIn("submission unknown", text)

    def test_a_gap_flags_only_when_it_held_on_one_side_and_its_ideas_wait_at_priority_4_5(self):
        ideas = maintenance.read_jsonl(TESTDATA / "ideas-sample.jsonl")  # real rows citing "pearl in view"
        name = "pearl in view % of moves, r0-99"
        def row(submission, us, rank1, rank2=None, games=10):
            leaders = [{"rank": 1, "games": 10, "stats": {name: rank1}},
                       {"rank": 2, "games": 0 if rank2 is None else 10, "stats": {} if rank2 is None else {name: rank2}}]
            return {"submission": submission, "us": {name: us}, "us_games": games, "leaders": leaders}
        held = [row(6518, 25, 40, 38), row(6542, 26, 41, 36), row(6542, 26, 40, 37)]  # the latest row counts
        self.assertIsNone(maintenance.unchanged_gaps(held[:1], ideas))
        # I109 (priority 3) and I208 (priority 1) are still queued: no flag while either waits there.
        self.assertEqual(maintenance.unchanged_gaps(held, ideas), [])
        for idea in ideas:
            if idea["id"] == "I109":
                idea["priority"] = 4
            if idea["id"] == "I208":
                idea["status"] = "tried"
        self.assertEqual(maintenance.unchanged_gaps(held, ideas), [
            f"- **{name}**: us 26 vs rank 1 40, rank 2 37 at submission 6542 (gap +11.0); +13.0 at 6518. Ideas citing "
            "it: untested I109 (priority 4), I110 (priority 5); tested before I064, I208."])
        self.assertEqual(maintenance.unchanged_gaps([row(6518, 25, 40), row(6542, 26, 36)], ideas), [])  # shrank
        self.assertEqual(maintenance.unchanged_gaps([row(6518, 25, 40), row(6542, 26, 45, 20)], ideas), [])  # split
        self.assertEqual(len(maintenance.unchanged_gaps([row(6518, 25, 40), row(6542, 26, 40)], ideas)), 1)  # rank 1
        # The dev benchmark is a third reference (M20): listed beside the leaders, and on their side of us or no flag.
        def with_dev(rows: list[dict], value: float) -> list[dict]:
            return [{**r, "dev": {"id": 545, "games": 10, "stats": {name: value}}} for r in rows]
        self.assertEqual(maintenance.unchanged_gaps(with_dev([row(6518, 25, 40), row(6542, 26, 40)], 38), ideas), [
            f"- **{name}**: us 26 vs rank 1 40, dev 545 38 at submission 6542 (gap +12.0); +13.0 at 6518. Ideas citing "
            "it: untested I109 (priority 4), I110 (priority 5); tested before I064, I208."])
        self.assertEqual(maintenance.unchanged_gaps(with_dev([row(6518, 25, 40), row(6542, 26, 40)], 20), ideas), [])
        # Right after a version goes live its means are over a game or two: no comparison until each has 5.
        few = [row(6518, 25, 40), row(6542, 26, 40, games=maintenance.GAP_MIN_GAMES - 1)]
        self.assertEqual(maintenance.unchanged_gaps(few, ideas), [])
        thin = [row(6518, 25, 40), row(6542, 26, 40)]
        thin[1]["leaders"][0]["games"] = 2
        self.assertEqual(maintenance.unchanged_gaps(thin, ideas), [])
        ideas[1]["priority"] = 0  # the backlog's top priority, not a missing one
        self.assertEqual(maintenance.unchanged_gaps(held, ideas), [])
        # From 2026-09-27 11:00 UTC a map id may carry other pearl timings: a gap held across the cutoff compared
        # two different pools, so only rows from the latest row's period count.
        ideas[1]["priority"] = 5
        across = [{**row(6518, 25, 40), "at": "2026-09-27T10:00:00Z"},
                  {**row(6542, 26, 40), "at": "2026-09-27T11:05:00Z"}]
        later = [{**r, "at": "2026-09-27T12:00:00Z"} for r in across]
        self.assertEqual(len(maintenance.unchanged_gaps(later, ideas)), 1)
        self.assertIsNone(maintenance.unchanged_gaps(across, ideas))
        self.assertIn("in the current pool period", maintenance.gaps_text(across, ideas, Path("history")))
        ideas[1].update(priority=4, status="trying:lane2")
        self.assertEqual(maintenance.unchanged_gaps(held, ideas), [])

    def test_scout_excludes_our_old_bot_and_keeps_opponent_versions(self):
        import ladder
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "scout-replays"
            folder.mkdir()
            (folder / "index.jsonl").write_text('{"match":1}\n{"match":2}\n')
            def get(path):
                if path == "/team":
                    return {"team": {"id": 157}}
                return {"match": {"id": int(path.rsplit('/', 1)[1]), "status": "completed",
                    "teamAId": 157 if path.endswith('/1') else 4, "teamBId": 5,
                    "submissionAId": 100, "submissionBId": 200, "mapId": 9, "mapHash": "hash", "ranked": True}}
            with patch.object(ladder, "get", side_effect=get), \
                    patch.object(maintenance, "command", return_value='{"checks":{"ok":true},"result":{"winner":"A"}}'):
                maintenance.scout(state, state)
            rows = json.loads((state / "scout.json").read_text())["games"]
            self.assertEqual([r["id"] for r in rows], [2])
            self.assertEqual(rows[0]["submissionAId"], 100)
            self.assertEqual(rows[0]["submissionBId"], 200)

    def test_the_load_allowance_copy_follows_the_real_file_and_goes_when_it_does(self):
        # Coders' sandboxes read only the copy (native.load_fraction); a stale 0.9 must not outlive sig's allowance.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            copy = maintenance.load_fraction_copy()
            (state / "load-fraction").write_text("0.9\n")
            maintenance.mirror_load_fraction(state)
            self.assertEqual(copy.read_text(), "0.9\n")
            (state / "load-fraction").unlink()
            maintenance.mirror_load_fraction(state)
            self.assertFalse(copy.exists())

    def test_common_tick_publishes_before_matches_and_honors_both_stops(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            actions = []
            with patch.object(maintenance, "check", side_effect=lambda *a: actions.append("check") or {}), \
                    patch.object(publishing, "cycle", side_effect=lambda *a: actions.append("publish") or {}), \
                    patch.object(driver.online, "cycle", side_effect=lambda *a: actions.append("matches")), \
                    patch.object(ladder_maps, "refresh", side_effect=lambda *a: actions.append("ladder maps")), \
                    patch.object(maintenance, "scout", side_effect=lambda *a: actions.append("scout"), create=True), \
                    patch.object(maintenance, "study", side_effect=lambda *a: actions.append("study")), \
                    patch.object(risers, "tick", side_effect=lambda state, models: actions.append(
                        "risers" if models else "risers, no worker")), \
                    patch.object(discord_teams, "refresh", side_effect=lambda *a, **k: actions.append("teams")), \
                    patch.object(maintenance, "discord", side_effect=lambda *a: actions.append("discord")), \
                    patch.object(digest, "tick", side_effect=lambda *a: actions.append("digest")), \
                    patch.object(reflect, "tick", side_effect=lambda *a: actions.append("reflect list")), \
                    patch.object(mapideas, "tick", side_effect=lambda *a, models: actions.append(
                        "mapideas" if models else "mapideas, no worker")), \
                    patch.object(sweep, "tick", side_effect=lambda *a: actions.append("sweep")), \
                    patch.object(maintenance, "MAPIDEAS_ENABLED", True), \
                    patch.object(maintenance, "host_crashes", side_effect=lambda *a: actions.append("host crashes")), \
                    patch.object(health, "run", side_effect=lambda *a, **k: actions.append("health") or []):
                maintenance.tick(state, state, state / "STOP")
                order = ["reflect list", "sweep", "check", "publish", "matches", "ladder maps", "scout", "study",
                         "risers", "teams", "discord", "digest", "mapideas", "host crashes", "health"]
                self.assertEqual(actions, order)
                actions.clear()  # a native session's tick starts no map-ideas worker
                maintenance.tick(state, state, state / "STOP", models=False)
                self.assertNotIn("mapideas", actions)
                self.assertIn("mapideas, no worker", actions)
                self.assertIn("risers, no worker", actions)  # risers.tick starts nothing without models
                actions[:] = order
                (state / "ASTRA_STOP").touch()
                maintenance.tick(state, state, state / "STOP")
            self.assertEqual(actions, order)

    def test_a_step_that_raises_is_logged_and_the_rest_still_run(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            actions, err = [], io.StringIO()
            with patch.object(maintenance, "check", return_value={}), \
                    patch.object(publishing, "cycle", side_effect=RuntimeError("upload server down")), \
                    patch.object(driver.online, "cycle", side_effect=AssertionError("unknown bot state")), \
                    patch.object(ladder_maps, "refresh", side_effect=lambda *a: actions.append("ladder maps")), \
                    patch.object(maintenance, "scout", side_effect=lambda *a: actions.append("scout"), create=True), \
                    patch.object(maintenance, "study", side_effect=lambda *a: actions.append("study")), \
                    patch.object(risers, "tick", return_value=None), \
                    patch.object(discord_teams, "refresh", side_effect=RuntimeError("site down")), \
                    patch.object(maintenance, "discord", side_effect=lambda *a: actions.append("discord")), \
                    patch.object(mapideas, "tick", side_effect=ValueError("bad record")), \
                    patch.object(sweep, "tick", return_value=None), \
                    patch.object(maintenance, "MAPIDEAS_ENABLED", True), \
                    patch.object(maintenance, "host_crashes", return_value=None), \
                    patch.object(health, "run", side_effect=lambda *a, **k: actions.append("health") or []), \
                    contextlib.redirect_stderr(err):
                maintenance.tick(state, state, state / "STOP")
            # The map pool is read while publishing fails, when online.cycle doesn't run.
            self.assertEqual(actions, ["ladder maps", "scout", "study", "discord", "health"])
            self.assertIn("maintenance: publishing failed: RuntimeError: upload server down", err.getvalue())
            self.assertIn("maintenance: map ideas failed: ValueError: bad record", err.getvalue())
            self.assertIn("maintenance: Discord teams failed: RuntimeError: site down", err.getvalue())

    def test_the_tick_applies_a_finished_background_audit_and_launches_nothing(self):
        import audit
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "ideas.jsonl").write_text("")
            (state / "results.jsonl").write_text("".join(json.dumps({"id": f"E{i}"}) + "\n" for i in range(5)))
            work = audit.prepare(state)
            finished = subprocess.Popen(["true"])
            finished.wait()
            audit.save(state, {**audit.read(state), "pid": finished.pid})
            (work / "findings.md").write_text("findings\n")
            (work / "ideas.jsonl").write_text(json.dumps({"category": "move", "priority": 2, "title": "explore more",
                                                          "hypothesis": "3 of 12 games circle a 2x2. Options: (1) avoid it (2) turn "
                                                                        "it into a gain (3) combine existing behaviours."}) + "\n")
            err = io.StringIO()
            with patch.object(maintenance, "check", return_value={}), \
                    patch.object(publishing, "cycle", return_value={}), patch.object(driver.online, "cycle"), \
                    patch.object(maintenance, "scout", create=True), patch.object(maintenance, "study"), \
                    patch.object(risers, "tick", return_value=None), \
                    patch.object(ladder_maps, "refresh"), \
                    patch.object(maintenance, "discord"), patch.object(mapideas, "tick", return_value=None), \
                    patch.object(sweep, "tick", return_value=None), patch.object(health, "run", return_value=[]), \
                    patch.object(maintenance, "host_crashes", return_value=None), \
                    patch.object(audit.subprocess, "Popen", side_effect=AssertionError("launched")), \
                    patch.object(audit.meta, "fresh_line",  # every process start fails here, so apply in this one
                                 lambda script, args, outcomes, fallback, repo=None: fallback()), \
                    contextlib.redirect_stderr(err):
                maintenance.tick(state, state, state / "STOP")
                maintenance.tick(state, state, state / "STOP")
            self.assertEqual(err.getvalue().count("audit applied: 1 ideas"), 1)
            self.assertEqual([json.loads(line)["id"] for line in (state / "ideas.jsonl").read_text().splitlines()],
                             ["I001"])
            self.assertIn("audit applied", (state / "host-alerts.log").read_text())

    def test_unsafe_publish_suppresses_online_matches(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(maintenance, "check", return_value={}), \
                    patch.object(publishing, "cycle", return_value={"blocked": True, "error": "source mismatch"}), \
                    patch.object(driver.online, "cycle", side_effect=AssertionError("unsafe bot")), \
                    patch.object(maintenance, "scout", create=True), patch.object(maintenance, "study"), \
                    patch.object(risers, "tick", return_value=None), \
                    patch.object(ladder_maps, "refresh"), \
                    patch.object(maintenance, "discord"), patch.object(discord_teams, "refresh"), \
                    patch.object(mapideas, "tick", return_value=None), \
                    patch.object(sweep, "tick", return_value=None), \
                    patch.object(maintenance, "host_crashes", return_value=None), \
                    patch.object(health, "run", return_value=[]), contextlib.redirect_stderr(io.StringIO()):
                maintenance.tick(state, state, state / "STOP")

    def test_compatibility_bootstraps_pretest_champion_but_respects_deleted_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            champion = Path(directory) / "champion"
            with patch.object(maintenance, "command", return_value=""):
                self.assertEqual(maintenance.compatibility_suite(repo, champion), repo / "tests/bot")
            with patch.object(maintenance, "command", return_value="suite-deletion-commit"):
                self.assertEqual(maintenance.compatibility_suite(repo, champion), champion / "tests/bot")

    # 1.2.0's record.py refused its toolkit for a day, found only by a stale test (28 Sep): the upgrade says so itself,
    # once per version however often it is retried, without failing, as predicted games only save time.
    def test_an_upgrade_record_py_cannot_record_on_alerts_once_and_still_passes(self):
        import sys
        repo, real = Path(maintenance.__file__).resolve().parent.parent, maintenance.command
        head = "toolkit 9.9.9: referee/record.py can't record, so no game can be predicted: "
        # "" runs the real probe on the dev venv's toolkit, so it also fails while that toolkit's digest is missing
        # from record.KNOWN_INTERNALS (test_record's test_the_known_digest_is_this_toolkits says so directly).
        for outcome, alert in (("", None),
                               ("unswbc's run.py changed (digest 0000000000000000)",
                                "unswbc's run.py changed (digest 0000000000000000)"),
                               (RuntimeError("python -c failed: FileNotFoundError:\n  gone.py"),
                                "its probe failed: python -c failed: FileNotFoundError: gone.py"),
                               (subprocess.TimeoutExpired("python", 300), "its probe failed: Command 'python' timed "
                                "out after 300 seconds")):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / "maps-train").mkdir()
                (state / "maps-train" / "a.map").write_text("")
                python = state / "toolchains/9.9.9/bin/python"
                python.parent.mkdir(parents=True)

                def run(args, cwd, **_):
                    if args[1] == "-c" and "record.supported" in args[2]:  # this toolkit's real answer, or `outcome`
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome or real([sys.executable, *args[1:]], cwd)
                    if "play" in args:
                        (python.parent.parent / "compatibility.jsonl").write_text("{}\n{}\n")
                    return "sha\n"
                for _ in range(2):  # the second is an hourly retry after a later step failed
                    with patch.object(maintenance, "install_toolchain", return_value=python), \
                            patch.object(maintenance, "command", side_effect=run), \
                            patch.object(maintenance, "check_compatibility", return_value=True):
                        self.assertEqual(maintenance.prepare_toolchain(repo, state, "9.9.9")["version"], "9.9.9")
                alerts = state / "host-alerts.log"
                lines = alerts.read_text().splitlines() if alerts.exists() else []
                self.assertEqual([line.split(" ", 2)[2] for line in lines], [head + alert] if alert else [])

    def test_both_providers_share_boundary_maintenance_and_never_upgrade_busy_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text(json.dumps({"id": "I1", "status": "trying:lane1", "priority": 1}) + "\n")
            (root / "maintenance.json").write_text(json.dumps({"available": "1.1.0", "active": {"version": "1.0.0"}}))
            with patch.object(driver, "STATE", root), patch.object(driver, "IDEAS", root / "ideas.jsonl"), \
                    patch.object(driver, "STOP", root / "STOP"), \
                    patch.object(native, "STOP", root / "ASTRA_STOP"), \
                    patch.object(maintenance, "tick"), \
                    patch.object(driver.meta, "prepare_review", return_value=None), \
                    patch.object(driver.audit, "prepare", return_value=None), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError, "already"):
                    native.claim(1)
            self.assertEqual(maintenance.read_state(root)["active"]["version"], "1.0.0")

    def test_failed_upgrade_keeps_previous_epoch_and_retries_later(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = {"version": "1.0.0", "python": "/old/python"}
            (root / "maintenance.json").write_text(json.dumps({"available": "1.1.0", "active": active}))
            with patch.object(maintenance, "prepare_toolchain", side_effect=RuntimeError("bad replay")):
                self.assertEqual(maintenance.apply_pending(root, root, False)["status"], "failed")
            self.assertEqual(maintenance.toolchain(root), active)
            self.assertEqual(maintenance.apply_pending(root, root, False)["status"], "retry-later")

    def test_successful_upgrade_records_epoch_only_after_both_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "maintenance.json").write_text(json.dumps({"available": "1.1.0"}))
            candidate = {"version": "1.1.0", "python": "/new/python", "epoch": "unswbc-1.1.0"}
            commands = []
            with patch.object(maintenance, "prepare_toolchain", return_value=candidate), \
                    patch.object(maintenance, "command", side_effect=lambda args, cwd: commands.append(args)):
                result = maintenance.apply_pending(root, root, False)
            self.assertEqual(result["status"], "upgraded")
            self.assertEqual(maintenance.toolchain(root), candidate)
            self.assertEqual(commands, [["uv", "add", "unswbc==1.1.0"],
                                        ["uv", "tool", "install", "--force", "unswbc==1.1.0"]])
            self.assertEqual(json.loads((root / "toolchain-epochs.jsonl").read_text()), candidate)

    # The upgrade itself never gates the native judge (it held the claim lock for the gates' 10-22 min); the hourly
    # step does, in the background, from the referee checkout evaluations play with the active toolchain's Python, as
    # a screen filler (native.py play with BC_YIELD=1), which keeps a share beside an evaluation.
    def test_native_gates_start_hourly_from_the_referee_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "maintenance.json").write_text(json.dumps({"active": {"python": "/toolchain/python"}}))
            referee = state / "referee" / "referee"
            referee.mkdir(parents=True)
            (referee / "native_fidelity.py").write_text("")
            champion = state / "champion"
            (champion / "bot").mkdir(parents=True)
            (champion / "bot" / "main.cpp").write_text("int x;\n")

            def commit() -> str:
                git = ["git", "-C", str(champion), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
                subprocess.run([*git, "init", "-q"], check=True)
                subprocess.run([*git, "add", "-A"], check=True)
                subprocess.run([*git, "commit", "-qm", "c"], check=True)
                return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout[:12]

            first = commit()
            spawned, running = [], set()

            def spawn(args, cwd, log, env=None):
                spawned.append((args, cwd, env))
                running.add(len(spawned))
                return len(spawned)

            with patch("meta.spawn", side_effect=spawn), patch.object(driver, "rotation_bots", return_value=[]), \
                    patch("meta.alive", side_effect=lambda pid: pid in running):
                self.assertIsNotNone(maintenance.native_gates(state, now=1e9))
                self.assertIsNone(maintenance.native_gates(state, now=1e9 + maintenance.INTERVAL))  # still running
                running.clear()
                self.assertIsNone(maintenance.native_gates(state, now=1e9 + maintenance.INTERVAL - 1))
                (champion / "bot" / "main.cpp").write_text("int y;\n")  # a promotion
                second = commit()
                self.assertIsNotNone(maintenance.native_gates(state, now=1e9 + maintenance.INTERVAL))
            self.assertEqual(len(spawned), 2)
            # Each gate plays the champion's commit as it was at its start, exported: never the live checkout, which
            # a promotion swaps mid-gate (29 Sep 15:49); only the newest export is kept.
            bots = state / "native-judge" / "bots"
            self.assertEqual([spawned[0][0][6], spawned[1][0][6]],
                             [str(bots / f"champion-{first}"), str(bots / f"champion-{second}")])
            self.assertEqual(([p.name for p in bots.iterdir()], (bots / f"champion-{second}/main.cpp").read_text()),
                             ([f"champion-{second}"], "int y;\n"))
            args, cwd, env = spawned[0]
            native = str(Path(maintenance.__file__).resolve().with_name("native.py"))
            self.assertEqual((args[:5], cwd, env["BC_YIELD"]),
                             (["/toolchain/python", native, "play", "referee/native_fidelity.py", "--gate-missing"],
                              referee.parent, "1"))

    def test_a_release_changing_only_the_viewer_holds_nothing_and_a_real_change_still_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = {}
            requires = {"1.2.0": ["wasmtime>=25"], "1.2.1": ["wasmtime>=25"], "1.3.0": ["wasmtime>=25"],
                        "1.3.1": ["wasmtime>=50"]}
            for version, viewer, engine in (("1.2.0", b"old", b"wasm"), ("1.2.1", b"new", b"wasm"),
                                            ("1.3.0", b"new", b"changed"), ("1.3.1", b"new", b"wasm")):
                package = root / "site" / version / "unswbc"
                (package / "__pycache__").mkdir(parents=True)
                (package / "__init__.py").write_text(f'__version__ = "{version}"')
                (package / "replay-viewer.vsix").write_bytes(viewer)
                (package / "__pycache__/run.pyc").write_bytes(version.encode())
                (package / "unswbc_engine.wasm").write_bytes(engine)
                packages[f"/toolchains/{version}/bin/python"] = package
            where = lambda args, cwd: json.dumps({"dir": str(packages[args[0]]),
                                                  "requires": requires[args[0].split("/")[2]]})
            installed = lambda repo, state, version: Path(f"/toolchains/{version}/bin/python")
            active = {"version": "1.2.0", "python": "/toolchains/1.2.0/bin/python"}
            record = {"available": "1.2.1", "active": active, "upgrade_attempt_at": 0}
            with patch.object(maintenance, "command", side_effect=where), \
                    patch.object(maintenance, "install_toolchain", side_effect=installed):
                maintenance.compare_engine(root, root, record)
                self.assertTrue(maintenance.same_engine(record))
                (root / "maintenance.json").write_text(json.dumps(record))
                self.assertFalse(maintenance.upgrade_waiting(root))
                self.assertEqual(maintenance.apply_pending(root, root, False), {"status": "unchanged"})
                record["available"] = "1.3.0"
                maintenance.compare_engine(root, root, record)
                self.assertFalse(maintenance.same_engine(record))
                (root / "maintenance.json").write_text(json.dumps(record))
                self.assertTrue(maintenance.upgrade_waiting(root))
                record["available"] = "1.3.1"  # the same files, but it asks for a newer wasmtime, which meters CPU
                maintenance.compare_engine(root, root, record)
                self.assertFalse(maintenance.same_engine(record))
                record["available"], record["active"] = "1.2.1", {"version": "1.3.0",
                                                                  "python": "/toolchains/1.3.0/bin/python"}
                self.assertFalse(maintenance.same_engine(record))  # judged against 1.2.0, not the new active one

    def test_failed_check_is_persisted_without_losing_active_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "maintenance.json").write_text(json.dumps({"active": {"version": "1.0.0"}}))
            with patch.object(maintenance, "fetch_updates", side_effect=RuntimeError("offline")):
                result = maintenance.check(root, root)
            self.assertIn("offline", result["check_error"])
            self.assertEqual(maintenance.read_state(root)["active"]["version"], "1.0.0")
            self.assertFalse(maintenance.due(result, result["checked_at"] + 1))

    def test_checks_survive_restart_and_wait_an_hour_even_after_failure(self):
        self.assertTrue(maintenance.due({}, 10000))
        for record in ({"checked_at": 1000}, {"checked_at": 1000, "error": "offline"}):
            self.assertFalse(maintenance.due(record, 1000 + maintenance.INTERVAL - 1))
            self.assertTrue(maintenance.due(record, 1000 + maintenance.INTERVAL))

    def test_busy_boundary_keeps_pending_upgrade_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = {"available": "1.1.0", "active": {"version": "1.0.0"}}
            (root / "maintenance.json").write_text(json.dumps(pending))
            result = maintenance.apply_pending(root, root, busy=True)
            self.assertEqual(result.get("status"), "deferred")
            self.assertEqual(json.loads((root / "maintenance.json").read_text()), pending)

    def test_identical_docs_do_not_churn_dates_or_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            (old / "sonar.md").write_text("<!-- Mirrored on 2026-09-23. -->\n\n# Sonar\n64 bits\n")
            (new / "sonar.md").write_text("<!-- Mirrored on 2026-09-24. -->\n\n# Sonar\n64 bits\n")
            before = (old / "sonar.md").stat().st_mtime_ns
            self.assertEqual(maintenance.publish_docs(new, old), [])
            self.assertEqual((old / "sonar.md").stat().st_mtime_ns, before)
            (new / "sonar.md").write_text("# Sonar\n128 bits\n")
            self.assertEqual(maintenance.publish_docs(new, old), ["sonar.md"])
            self.assertEqual((old / "sonar.md").read_text(), "# Sonar\n128 bits\n")

    def test_upgrade_requires_two_parsed_crash_free_cpu_safe_games(self):
        good = {"error": None, "a": {"crashes": 0, "timeouts": 0, "points": {"p99": 1, "max": 2}},
                "b": {"crashes": 0, "timeouts": 0, "points": {"p99": 1, "max": 2}}}
        self.assertTrue(maintenance.check_compatibility([good, good]))
        for bad in ([], [good], [good, {**good, "error": "schema failed"}],
                    [good, {**good, "a": {"crashes": 1}}],
                    [good, {**good, "b": {"crashes": 0, "timeouts": 0, "points": {"p99": 51_000_000, "max": 60_000_000}}}]):
            self.assertFalse(maintenance.check_compatibility(bad))


class TestDiscordRead(unittest.TestCase):
    def test_a_failed_read_is_retried_hourly_back_to_the_last_good_reads_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            calls = []
            signed_out, busy = (2, "discord: the browser is signed out; sign in again"), (
                3, "discord: problem: another discord_read.py is running; nothing read")  # a backfill holds its lock
            exits = [signed_out, busy, (0, ""), signed_out]

            def run(cmd, **kwargs):
                calls.append(cmd)
                code, err = exits[len(calls) - 1]
                return subprocess.CompletedProcess(cmd, code, "discord: 0 new messages, 9 stored", err)

            def hours() -> str:
                return calls[-1][calls[-1].index("--hours") + 1]

            def since(**fields) -> None:  # discord.json as the last read left it, with `fields` moved back in time
                report = json.loads((state / "discord.json").read_text())
                (state / "discord.json").write_text(json.dumps({**report, **fields}))
            with patch.object(maintenance.subprocess, "run", side_effect=run):
                maintenance.discord(state, state)
                self.assertEqual(hours(), "24.5")  # no good read yet: a day and the overlap
                report = json.loads((state / "discord.json").read_text())
                self.assertIn("signed out", report["error"])
                self.assertNotIn("fetched_at", report)
                self.assertFalse((state / "host-alerts.log").exists())  # a failed read that stored nothing: no line
                maintenance.discord(state, state)  # within DISCORD_RETRY of a failed read: not read again
                self.assertEqual(len(calls), 1)
                # An hour on it is retried, back to the last good read's start (30 h ago) and the half hour before.
                now = time.time()
                since(attempt_at=now - maintenance.DISCORD_RETRY - 1, fetched_from=now - 30 * 3600)
                maintenance.discord(state, state)  # it read nothing, so it is a failure too, the window unmoved
                self.assertEqual((len(calls), hours()), (2, "30.5"))
                self.assertIn("another discord_read.py is running",
                              json.loads((state / "discord.json").read_text())["error"])
                since(attempt_at=now - maintenance.DISCORD_RETRY - 1, fetched_from=now - 10 * 86400)
                maintenance.discord(state, state)  # a good read, at most DISCORD_MAX_HOURS back
                self.assertEqual(hours(), f"{maintenance.DISCORD_MAX_HOURS:g}")
                self.assertGreaterEqual(json.loads((state / "discord.json").read_text())["fetched_from"], now)
                since(attempt_at=now - maintenance.DISCORD_RETRY - 1)
                maintenance.discord(state, state)  # a good read's next is a DISCORD_EVERY on
                self.assertEqual(len(calls), 3)
            self.assertLess(maintenance.DISCORD_KILL, health.UPKEEP_TICK_HUNG)  # a killed read is no hung tick

    # The reader appends new messages to messages.jsonl; these rows are copied from the live store (2026-09-26).
    ROWS = [{"id": "1548839411465064470", "time": "2026-09-13T23:34:51.738Z", "author": "Maddie",
             "text": "map suggestion: make an underwater map", "channel": "map-ideas"},
            {"id": "1553064711992377457", "time": "2026-09-25T15:24:41.886Z", "author": "Rory Coughlan",
             "text": "our team has nearly 10k games now", "channel": "general"}]

    # Runs discord() against a reader that appends `rows` and exits `code` with the output tools/discord_read.py
    # prints for that exit (its main/read functions' print calls, 2026-09-26).
    def read_with(self, state: Path, rows: list[dict], code: int = 0, problems: tuple[str, ...] = ()) -> dict:
        store = state / "discord/messages.jsonl"
        store.parent.mkdir(parents=True, exist_ok=True)
        if not store.exists():
            store.write_text(json.dumps({"id": "1", "time": "2026-09-01T00:00:00Z", "author": "a", "text": "old",
                                         "channel": "map-ideas"}) + "\n")

        def run(cmd, **kwargs):
            with store.open("a") as handle:
                handle.writelines(json.dumps(row) + "\n" for row in rows)
            (state / "discord/latest.md").write_text(LATEST)  # every read that finishes writes its window
            summary = f"{len(rows)} new messages, 400 stored"
            err = [f"discord: #map-ideas: {len(rows)} in the window, {len(rows)} new", "discord: 57 profiles read"]
            if code == 2:
                err.insert(1, "discord: the browser is signed out; sign in again (browse handoff)")
            err += [f"discord: problem: {problem}" for problem in problems]
            out = f"discord: partial ({len(problems)} problems): {summary}" if problems else f"discord: {summary}"
            return subprocess.CompletedProcess(cmd, code, out + "\n", "\n".join(err) + "\n")
        with patch.object(maintenance.subprocess, "run", side_effect=run):
            maintenance.discord(state, state)
        return json.loads((state / "discord.json").read_text())

    def test_a_read_with_new_messages_tells_the_orchestrator_how_many_are_map_ideas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = self.read_with(state, self.ROWS)
            self.assertIn(f"discord read: 2 new messages (1 in map channels); see {state / 'discord/latest.md'}",
                          (state / "host-alerts.log").read_text())
            self.assertEqual(report["new_at"], report["fetched_at"])
            self.assertEqual((report["error"], report["partial"], report["partial_runs"]), (None, None, 0))

    def test_a_signed_out_read_keeps_its_error_and_still_reports_what_it_stored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = self.read_with(state, self.ROWS, code=2, problems=("signed out at #general",))
            line = (state / "host-alerts.log").read_text()
            self.assertIn("discord read: 2 new messages (1 in map channels) (failed: discord: problem: signed out at "
                          f"#general); see {state / 'discord/latest.md'}", line)
            self.assertIn("the browser is signed out", report["error"])
            self.assertEqual(("fetched_at" in report, "new_at" in report), (False, True))

    def test_a_partial_read_counts_as_fetched_and_says_what_was_missed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            problems = ("#ask-devs: out of time after 3 of 7 threads", "out of time before #general")
            report = self.read_with(state, self.ROWS, code=3, problems=problems)
            self.assertIn("discord read: 2 new messages (1 in map channels) (partial: #ask-devs: out of time after 3 "
                          f"of 7 threads); see {state / 'discord/latest.md'}", (state / "host-alerts.log").read_text())
            self.assertEqual((report["error"], report["summary"]),
                             (None, "discord: partial (2 problems): 2 new messages, 400 stored"))
            self.assertEqual((report["partial"], report["partial_runs"]), ("; ".join(problems), 1))
            self.assertEqual(report["new_at"], report["fetched_at"])
            # Another partial read in a row counts up; a complete one resets it.
            report["attempt_at"] = 0
            (state / "discord.json").write_text(json.dumps(report))
            self.assertEqual(self.read_with(state, [], code=3, problems=problems[:1])["partial_runs"], 2)
            self.assertIn("discord read: 0 new messages, nothing to digest (partial: #ask-devs: out of time",
                          (state / "host-alerts.log").read_text().splitlines()[-1])
            (state / "discord.json").write_text(json.dumps({**json.loads((state / "discord.json").read_text()),
                                                            "attempt_at": 0}))
            self.assertEqual(self.read_with(state, [])["partial_runs"], 0)

    def test_a_killed_read_points_at_the_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            store = state / "discord/messages.jsonl"
            store.parent.mkdir(parents=True)

            def run(cmd, **kwargs):
                store.write_text(json.dumps(self.ROWS[0]) + "\n")
                raise subprocess.TimeoutExpired(cmd, maintenance.DISCORD_KILL)
            with patch.object(maintenance.subprocess, "run", side_effect=run):
                maintenance.discord(state, state)
            report = json.loads((state / "discord.json").read_text())
            self.assertEqual(report["error"], "timed out after 50 min")
            self.assertIn(f"1 new messages (1 in map channels) (failed: timed out after 50 min); see {store}",
                          (state / "host-alerts.log").read_text())

    def test_a_read_with_nothing_new_is_reported_too_but_needs_no_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = self.read_with(state, [])
            self.assertIn("discord read: 0 new messages, nothing to digest", (state / "host-alerts.log").read_text())
            self.assertNotIn("new_at", report)


# The Discord digest worker (loop/digest.py): the upkeep opens a folder after a read that stored new messages and
# launches a confined read-only worker on it; its report is read back with meta.worker_file and saved for the
# orchestrator, who still decides.
class TestDigestWorker(unittest.TestCase):
    SESSION = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 9,
               "terminal_reason": "completed", "result": "The report is written to report.md."}
    REPORT = ("1. **rtrgrd (Admin) says openclaws signed up**; resumes are checked before the Sprint.\n"
              "End: read 5 messages from #ask-devs.\n")

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    def read(self, rows: list[dict]) -> dict:
        return TestDiscordRead.read_with(TestDiscordRead(), self.state, rows)

    def pending(self) -> Path:
        return Path(digest.reports.read(self.state, digest.KIND)["pending"])

    def finish(self, work: Path, report: str | None = REPORT) -> None:
        if report is not None:
            (work / "report.md").write_text(report)
        io_folder = self.state / "worker-io/discord-digest" / work.name
        (io_folder / "session.json").write_text(json.dumps(self.SESSION))

    def test_a_read_with_new_messages_gets_a_read_only_digest_worker(self) -> None:
        (self.state / "study.md").write_text("# Study\n")
        report = self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
            self.assertIn("launched", digest.tick(self.state))
            with patch.object(digest.reports, "running", return_value=True):
                self.assertIsNone(digest.tick(self.state))  # still running: never a second one
        work = self.pending()
        self.assertEqual(digest.reports.read(self.state, digest.KIND)["read_at"], report["new_at"])
        self.assertEqual((work / "latest.md").read_text(), LATEST)
        self.assertEqual((work / "study.md").read_text(), "# Study\n")
        self.assertEqual((work / "briefing.md").read_text(), "(no briefing yet)\n")
        prompt = (self.state / "worker-io/discord-digest" / work.name / "PROMPT.md").read_text()
        self.assertTrue(prompt.startswith("You digest saved Discord messages"))  # the agent's text, no front matter
        self.assertIn(f"The messages are in `{work}/latest.md`", prompt)  # DISCORD.md with its paths filled in
        self.assertNotIn("{latest}", prompt)
        self.assertIn(f"`{work}/report.md`", prompt)
        self.assertIn("must reveal nothing about our bot, strategy, loop, results, maps, tools or plans",
                      " ".join(prompt.split()))  # sig, 2026-09-26
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Write")
        self.assertEqual(command[command.index("--effort") + 1], "low")
        reads = [command[i + 1] for i, arg in enumerate(command) if arg == "--add-dir"]
        self.assertEqual(reads, [str(self.state / "discord"), str(digest.REPO / "docs/unsw")])

    def test_the_alert_says_when_a_report_read_fewer_messages_than_latest_md_holds(self) -> None:
        # A day's latest.md is over one Read call: a worker stopping at its first page still writes a whole-looking
        # report, so its closing count (loop/DISCORD.md, Report) is checked against the headings' (N).
        latest = LATEST + "\n## #general (120)\n\n..."  # the sample's #ask-devs (4), and a day's chat
        self.assertEqual(digest.count_note("Read 124 messages: #ask-devs 4, #general 120.", latest), "")
        self.assertEqual(digest.count_note("Read 40 messages: #ask-devs 4, #general 36.", latest),
                         " (its report says it read 40 of latest.md's 124 messages)")
        self.assertIn("states no message count", digest.count_note("## D1. something\n", latest))

    def test_a_read_with_nothing_new_gets_none(self) -> None:
        self.read([])
        with patch.object(digest.reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            self.assertIsNone(digest.tick(self.state))
        self.assertFalse((self.state / "discord-digest").exists())

    def test_the_report_is_saved_for_the_orchestrator_and_alerted_once(self) -> None:
        report = self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
            digest.tick(self.state)
            self.finish(self.pending())
            with patch.object(digest.reports.meta, "alive", return_value=False):
                line = digest.tick(self.state)
                self.assertIsNone(digest.tick(self.state))  # that read is digested
        path = self.state / "discord" / f"digest-{digest.stamp(report['new_at'])}.md"
        self.assertEqual(line, f"DISCORD DIGEST ready: {path}; check its claims, {digest.DECIDE}")
        self.assertEqual(path.read_text(), self.REPORT)
        self.assertEqual((self.state / "host-alerts.log").read_text().count("DISCORD DIGEST ready"), 1)
        self.assertEqual(popen.call_count, 1)

    def test_a_worker_finishing_after_a_by_hand_digest_of_its_read_keeps_both(self) -> None:
        # The 11:27 read on 2026-09-27: the orchestrator's subagent digested it while the worker waited out a usage
        # limit, and the worker's report landed at 12:29 under the same name; the decisions cite the saved one's ids.
        at = time.time()
        saved = digest.report_path(self.state, at)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text("## D1. by hand\n")
        line, record = digest.done(self.state, {"read_at": at}, "## D1. the worker's\n")
        second = saved.with_name(f"{saved.stem}-2.md")
        self.assertEqual((saved.read_text(), second.read_text()), ("## D1. by hand\n", "## D1. the worker's\n"))
        self.assertEqual(record["report"], str(second))
        self.assertIn(str(second), line)

    def test_a_report_linked_to_a_secret_is_refused(self) -> None:
        secret = self.state / "keys.json"
        secret.write_text('{"key": "not for the orchestrator"}')
        self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            digest.tick(self.state)
            work = self.pending()
            self.finish(work, report=None)
            (work / "report.md").symlink_to(secret)
            with patch.object(digest.reports.meta, "alive", return_value=False):
                self.assertIn("retrying after an hour", digest.tick(self.state))
        self.assertEqual(list((self.state / "discord").glob("digest-*")), [])
        self.assertNotIn("not for the orchestrator", (self.state / "host-alerts.log").read_text())

    def test_two_failed_launches_hand_the_read_to_the_orchestrator(self) -> None:
        report = self.read(TestDiscordRead.ROWS)
        later = digest.reports.time.time() + digest.reports.RETRY_S + 1
        with patch.object(digest.reports.subprocess, "Popen", side_effect=OSError("no claude")):
            self.assertIn("retrying after an hour", digest.tick(self.state))
            with patch.object(digest.reports.time, "time", return_value=later):
                line = digest.tick(self.state)
                self.assertIsNone(digest.tick(self.state))  # not reopened for the same read
        self.assertIn("DISCORD DIGEST abandoned after 2 failed launches", line)
        self.assertIn("by hand with a fresh read-only pass (the discord-digest subagent, another read-only subagent, "
                      "or yourself) following loop/DISCORD.md", line)
        record = digest.reports.read(self.state, digest.KIND)
        self.assertEqual((record["digested_at"], len(record["abandoned"])), (report["new_at"], 1))

    def test_under_native_orchestration_the_read_is_handed_to_the_orchestrator(self) -> None:
        self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = digest.tick(self.state, models=False)
            self.assertIsNone(digest.tick(self.state, models=False))  # handed over once
        self.assertIn("DISCORD DIGEST by hand", line)
        self.assertIn(f"digest {self.state / 'discord/latest.md'} by hand with a fresh read-only pass", line)
        self.assertFalse((self.state / "discord-digest").exists())

    def test_a_claude_folder_left_pending_goes_to_the_orchestrator_when_native_takes_over(self) -> None:
        # A Claude driver's launch failed once (retry in an hour); then a native session runs the upkeep instead.
        report = self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", side_effect=OSError("no claude")):
            self.assertIn("retrying after an hour", digest.tick(self.state))
        work = self.pending()
        with patch.object(digest.reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = digest.tick(self.state, models=False)
            self.assertIn(f"DISCORD DIGEST abandoned under native orchestration, which runs no Claude worker ({work})",
                          line)
            self.assertIn(f"digest {work / 'latest.md'} by hand", line)
            record = digest.reports.read(self.state, digest.KIND)
            self.assertEqual((record.get("pending"), record["digested_at"]), (None, report["new_at"]))
            # health: no worker on it now, so the read with no decisions after it alerts once its grace is over
            self.assertEqual([a.key for a in health.discord_digest(self.state,
                                                                   report["new_at"] + health.DIGEST_GRACE + 1)],
                             ["discord:digest"])
            # The next read gets its by-hand line at once.
            (self.state / "discord.json").write_text(json.dumps({**report, "attempt_at": 0}))
            self.read([dict(TestDiscordRead.ROWS[1], id="1553064711992377459")])
            self.assertIn("DISCORD DIGEST by hand", digest.tick(self.state, models=False))

    def test_a_claude_worker_that_finishes_under_native_is_still_collected(self) -> None:
        self.read(TestDiscordRead.ROWS)
        with patch.object(digest.reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            digest.tick(self.state)
        self.finish(self.pending())
        with patch.object(digest.reports, "running", return_value=False):
            self.assertIn("DISCORD DIGEST ready", digest.tick(self.state, models=False))

    def test_only_one_process_runs_the_upkeep_tick(self) -> None:
        # 28 Sep ~15:50: six `--once` drivers each ran the whole tick every minute, 6-11% of a core apiece.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            held = maintenance.upkeep_owner(state, True)  # another driver's
            self.assertIsNotNone(held)
            self.assertIsNone(maintenance.upkeep_owner(state, True))
            native_upkeep = maintenance.upkeep_owner(state, False)  # its tick differs: its own lock
            self.assertIsNotNone(native_upkeep)
            os.close(native_upkeep)
            asked, ticks, handed, real = threading.Event(), [], [], maintenance.upkeep_owner

            def owner(state: Path, models: bool) -> int | None:
                got = real(state, models)
                asked.set()
                return got

            with patch.object(maintenance, "upkeep_owner", owner), \
                    patch.object(maintenance, "tick", lambda *args: ticks.append(args)), \
                    patch.object(maintenance, "hand_over", lambda *args: handed.append(args) or Mock(pid=4242)), \
                    patch("health.quick", return_value=[]), \
                    patch.object(maintenance.throughput, "sampling", lambda *args: contextlib.nullcontext()), \
                    patch("online.polling", lambda *args: contextlib.nullcontext()), patch("sys.stderr"):
                with maintenance.checking(state, state, state / "STOP"):
                    self.assertTrue(asked.wait(5))
                self.assertEqual(handed, [])
                os.close(held)  # the owner exits: the next driver takes over
                asked.clear()
                with maintenance.checking(state, state, state / "STOP"):
                    self.assertTrue(asked.wait(5))
                # 29 Sep 16:05 (this Mac at load 36): it ticks in a stand-in at home.WORKER_NICE, never in its own
                # process at nice 0.
                self.assertEqual((len(handed), ticks), (1, []))

    def test_with_every_process_on_older_loop_code_the_tick_still_runs_in_a_stand_in_on_current_code(self) -> None:
        # 28 Sep: an owner started before a merge ran the old upkeep for hours (MISSES 76), so a process on older code
        # stopped ticking; 29 Sep 13:45-14:00, after four merges, every driver was one, none ticked and v0066 waited
        # unpublished (MISSES 91). Now it hands the lock to a stand-in on the disk's code, whenever it finds it free
        # (on current code too: the stand-in ticks niced).
        class Fast(threading.Event):  # the tick's minute wait, shortened
            def wait(self, timeout=None) -> bool:
                return super().wait(0.02 if timeout else timeout)

        def until(done) -> None:
            deadline = time.monotonic() + 5
            while not done() and time.monotonic() < deadline:
                time.sleep(0.01)

        with tempfile.TemporaryDirectory() as directory:
            state, current, ticks, stand_ins = Path(directory), [True], [], []

            def hand_over(repo, state_, stop_path, models, owner, parent):
                stand_ins.append(os.dup(owner))  # the stand-in holds the lock until it exits
                return Mock(pid=4242)

            with patch.object(maintenance, "current_code", side_effect=lambda everything=False: current[0]), \
                    patch.object(maintenance, "tick", lambda *args: ticks.append(1)), \
                    patch.object(maintenance, "hand_over", hand_over), \
                    patch.object(maintenance.threading, "Event", Fast), patch("health.quick", return_value=[]), \
                    patch.object(maintenance.throughput, "sampling", lambda *args: contextlib.nullcontext()), \
                    patch("online.polling", lambda *args: contextlib.nullcontext()), patch("sys.stderr"):
                with maintenance.checking(state, state, state / "STOP"):
                    current[0] = False  # a merge rewrites loop code it loaded: no live process is current
                    until(lambda: stand_ins)
                    self.assertEqual(len(stand_ins), 1)
                    time.sleep(0.1)
                    self.assertEqual(ticks, [])  # never in its own process, on older code or not
                    self.assertIsNone(maintenance.upkeep_owner(state, True))  # the stand-in holds the lock
                    os.close(stand_ins[0])  # the stand-in exits (its own code went stale, say, or it crashed)
                    until(lambda: len(stand_ins) > 1)
                    self.assertEqual(len(stand_ins), 2)  # the lock found free goes to a new one
                    self.assertEqual(ticks, [])
                for fd in stand_ins[1:]:
                    os.close(fd)

    # fresh-eval-code's review: driver.hand_off's exec stopped the upkeep block's threads mid-step and closed its
    # stand-ins' pipes, so a stand-in in a Discord read past STAND_IN_GRACE exited hard, orphaning the reader. Now
    # before_exec stops and waits for every part, and keeps the stand-ins' pipes open, inheritable, for the new image.
    def test_before_an_exec_every_upkeep_part_stops_and_the_stand_ins_pipes_stay_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, pipes = Path(directory), []

            def hand_over(repo, state_, stop_path, models, owner, parent):
                pipes.append(os.dup(parent))  # the stand-in's read end: EOF means it would stop
                return Mock(pid=4242)

            self.addCleanup(maintenance.DETACHING.clear)
            self.addCleanup(maintenance.EXEC_KEEP.clear)
            with patch.object(maintenance, "hand_over", hand_over), patch("health.quick", return_value=[]), \
                    patch("online.dev_poll"), patch.object(maintenance.throughput, "sample"), patch("sys.stderr"):
                with maintenance.checking(state, state, state / "STOP"):
                    deadline = time.monotonic() + 5
                    while not pipes and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(sorted(maintenance.EXEC_PARTS),
                                     ["dev-poll", "quick-health", "upkeep-poll", "utilisation-sampler"])
                    self.assertEqual(maintenance.before_exec(5), [])  # all stopped within the wait
                    self.assertFalse(any(thread.is_alive() for _, thread in maintenance.EXEC_PARTS.values()))
                    (write,) = maintenance.EXEC_KEEP
                    self.assertTrue(os.get_inheritable(write))
                    os.set_blocking(pipes[0], False)
                    with self.assertRaises(BlockingIOError):  # still open: no EOF, so the stand-in ticks on
                        os.read(pipes[0], 1)
                self.assertEqual(maintenance.EXEC_PARTS, {})
                os.close(write)
                self.assertEqual(os.read(pipes[0], 1), b"")  # its end, at the new image's exit
                os.close(pipes[0])

    # driver.hand_off waits on TICK_IDLE before its exec; a tick the exec stopped anyway leaves a record whose pid (the
    # same after an exec) stays alive, which health.upkeep_ticks would read as a tick running, so the new image ends it.
    def test_a_tick_clears_tick_idle_and_the_new_image_ends_a_tick_record_its_exec_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, seen = Path(directory), []
            with patch.object(maintenance, "tick", lambda *args: seen.append(maintenance.TICK_IDLE.is_set())):
                maintenance.timed_tick(state, state, state / "STOP", True)
            self.assertEqual((seen, maintenance.TICK_IDLE.is_set()), ([False], True))
            mine, other = maintenance.tick_record(True), maintenance.tick_record(False)
            (state / mine).write_text(json.dumps({"pid": os.getpid(), "started": 1.0, "ended": None}))
            (state / other).write_text(json.dumps({"pid": os.getppid(), "started": 1.0, "ended": None}))
            self.assertEqual(maintenance.end_exec_ticks(state), [mine])
            self.assertIsInstance(json.loads((state / mine).read_text())["ended"], float)
            self.assertIsNone(json.loads((state / other).read_text())["ended"])  # another process's tick runs

    def test_a_stand_in_ticks_on_current_code_hands_over_when_it_goes_stale_and_stops_when_its_pipe_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, ticks, handed = Path(directory), [], []
            for code, ended in (([True, True, False], False), ([True] * 99, True)):
                ticks.clear()
                (read, write), owner = os.pipe(), os.open(os.devnull, os.O_RDONLY)
                with patch.object(maintenance, "current_code", side_effect=lambda everything=False: code.pop(0)), \
                        patch.object(maintenance, "tick", lambda *args: ticks.append(args)), \
                        patch.object(maintenance, "hand_over", lambda *args: handed.append(args) or Mock(pid=1)), \
                        patch.object(maintenance, "TICK_EVERY", 0.01), patch("sys.stderr"):
                    if ended:  # the served process's upkeep block ended, or it died
                        os.close(write)
                        maintenance.stand_in(state, state, state / "STOP", False, owner, read)  # returns, current
                        self.assertLessEqual(len(ticks), 1)  # one if it ticked before its reader saw the end
                        self.assertEqual(len(handed), 1)  # no successor
                    else:
                        maintenance.stand_in(state, state, state / "STOP", False, owner, read)
                        os.close(write)
                        self.assertEqual(ticks, [(state, state, state / "STOP", False)] * 2)
                        self.assertEqual(handed, [(state, state, state / "STOP", False, owner, read)])  # its pipe too
                        record = json.loads((state / maintenance.tick_record(False)).read_text())  # for health
                        self.assertEqual((record["pid"], type(record["ended"])), (os.getpid(), float))
                self.assertFalse(os.get_inheritable(owner) or os.get_inheritable(read))  # a tick's children get neither
                os.close(read)
                os.close(owner)

    def test_a_stand_in_whose_tick_hangs_after_its_parent_goes_exits_hard_after_the_grace(self) -> None:
        # Review (29 Sep): a hung tick kept the stand-in, and so the upkeep lock, forever once its parent had gone.
        with tempfile.TemporaryDirectory() as directory:
            state, exited = Path(directory), threading.Event()
            (read, write), owner = os.pipe(), os.open(os.devnull, os.O_RDONLY)
            codes = []  # the tick hangs, and its parent goes while it runs

            def hard_exit(code: int) -> None:
                codes.append(code)
                exited.set()
                raise SystemExit  # ends the watcher thread

            with patch.object(maintenance, "current_code", return_value=True), \
                    patch.object(maintenance, "tick", lambda *args: (os.close(write), exited.wait(5))), \
                    patch.object(maintenance, "STAND_IN_GRACE", 0.05), \
                    patch.object(maintenance.os, "_exit", hard_exit), patch("sys.stderr"):
                maintenance.stand_in(state, state, state / "STOP", True, owner, read)
            self.assertEqual(codes, [1])
            os.close(read)
            os.close(owner)

    def test_a_real_stand_in_under_a_stop_marker_exits_cleanly_and_frees_the_lock(self) -> None:
        # Review (29 Sep): the stand-in aborted (SIGABRT, "_enter_buffered_busy") at every hand-over or stop, its
        # reader thread blocked in sys.stdin.buffer.read() at interpreter shutdown. The real entry point and pass_fds.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "STOP").touch()  # no tick runs
            owner, (read, write) = maintenance.upkeep_owner(state, True), os.pipe()
            try:
                process = maintenance.hand_over(Path(maintenance.__file__).parents[1], state, state / "STOP", True,
                                                owner, read)
            finally:
                os.close(read)
                os.close(owner)
            self.assertEqual(process.args[:3], ["/usr/bin/nice", "-n", str(maintenance.nice_increment())])  # niced
            try:
                self.assertEqual(process.wait(60), 0, (state / maintenance.STAND_IN_LOG).read_text())
            finally:
                os.close(write)  # open until it exited: it stopped on the marker, not the pipe
            self.assertIsNotNone(fd := maintenance.upkeep_owner(state, True))
            os.close(fd)

    def test_the_minute_health_checks_run_in_every_process_whatever_the_tick_is_doing(self) -> None:
        # Review (28 Sep): with one owner ticking, a slow or hung hourly step in its tick (docs, publishing, online,
        # scouting, Discord) held LANES SHORT and the Mac gate stall alert for every process. They run beside it.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            checked, release, ticks = threading.Event(), threading.Event(), []
            held = maintenance.upkeep_owner(state, True)  # another driver's, whose tick is somewhere in an hour's step
            with patch.object(maintenance, "tick", lambda *args: ticks.append(args)), \
                    patch("health.quick", side_effect=lambda *args, **kwargs: checked.set() or []) as quick, \
                    patch.object(maintenance.throughput, "sampling", lambda *args: contextlib.nullcontext()), \
                    patch("online.polling", lambda *args: contextlib.nullcontext()):
                with maintenance.checking(state, state, state / "STOP"):
                    self.assertTrue(checked.wait(5))
                self.assertEqual((ticks, quick.call_args.kwargs), ([], {"every": maintenance.QUICK_EVERY - 10}))
                os.close(held)
                checked.clear()
                with patch.object(maintenance, "hand_over", lambda *args: release.wait(10) or Mock(pid=4242)), \
                        patch("sys.stderr"), \
                        maintenance.checking(state, state, state / "STOP"):  # the owner, stuck handing over
                    self.assertTrue(checked.wait(5))
                    release.set()

    def test_native_upkeep_ticks_without_model_workers(self) -> None:
        # native.py's upkeep and its commands' checking() pass models=False; the Claude driver's keep the default.
        source = (Path(__file__).resolve().parent / "native.py").read_text()
        # Its watch ticks only through the owner lock, as the commands' checking() does (one process at a time).
        self.assertIn("driver.maintenance.ticking(driver.REPO, driver.STATE, STOP, False, driver.stop_requested)",
                      source)
        self.assertNotIn("maintenance.tick(", source)
        self.assertIn("driver.maintenance.checking(driver.REPO, driver.STATE, STOP, models=False)", source)

    def test_a_read_already_digested_by_hand_is_skipped_only_before_the_first_digest(self) -> None:
        self.read(TestDiscordRead.ROWS)
        (self.state / "discord/digests.md").write_text("## Digest by hand\n")
        with patch.object(digest.reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            self.assertIsNone(digest.tick(self.state))
        # Later, digests.md written after a read may be an older read's decisions (a hung worker's read digested by
        # hand): the newer read still gets its digest.
        report = json.loads((self.state / "discord.json").read_text())
        (self.state / "discord.json").write_text(json.dumps({**report, "attempt_at": 0}))
        self.read([dict(TestDiscordRead.ROWS[1], id="1553064711992377458")])
        (self.state / "discord/digests.md").write_text("## Decisions on the earlier read\n")
        with patch.object(digest.reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            self.assertIn("launched", digest.tick(self.state))


# An owed post-mortem or follow-on that never ran or ended waiting was rerun only by hand (`driver.py --turn`): the
# upkeep reruns it once, when its grace has passed, its driver has exited and nothing works in its clone.
class TestOwedTurns(unittest.TestCase):
    def test_an_owed_turn_is_rerun_once_when_nothing_holds_its_clone(self) -> None:
        import meta
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            now = time.time()
            row = {"id": "E0350", "verdict": "discarded", "sprt": {"decision": "H0"}, "session": {"session_id": "s-1"},
                   "started": now - 9 * 3600, "finished": now - 8 * 3600}
            (state / "results.jsonl").write_text(json.dumps(row) + "\n")
            (state / "runs/E0350").mkdir(parents=True)
            clone = state / "clones/E0350"
            clone.mkdir(parents=True)
            spawned = []
            with patch.dict(os.environ, {"BC_LOOP_HOME": str(state)}), \
                    patch.object(meta, "spawn", side_effect=lambda args, *rest: spawned.append(args) or 4242), \
                    patch.object(meta, "alive", return_value=False), \
                    patch.object(maintenance.subprocess, "run") as lsof:
                lsof.return_value = subprocess.CompletedProcess([], 0, f"p1\nn{clone.resolve()}/bot\n", "")
                self.assertEqual(maintenance.owed_turns(state, state, now), [])  # a coder still works in it
                lsof.return_value = subprocess.CompletedProcess([], 0, "p1\nn/elsewhere\n", "")
                (line,) = maintenance.owed_turns(state, state, now)
                self.assertIn("E0350: its owed postmortem never finished", line)
                self.assertEqual(maintenance.owed_turns(state, state, now), [])  # once only
                (alert,) = health.result_turns(state, now)  # still owed after it: the alert says the rerun failed too
            self.assertEqual([args[-2:] for args in spawned], [["--turn", "E0350"]])
            self.assertEqual(json.loads((state / "runs/E0350/postmortem.auto.json").read_text())["pid"], 4242)
            self.assertIn("the upkeep's one rerun", alert.text)


if __name__ == "__main__":
    unittest.main()
