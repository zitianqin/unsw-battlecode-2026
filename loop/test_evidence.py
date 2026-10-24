"""evidence.md's text from played games, without playing any.

    cd loop && ../.venv/bin/python -m unittest test_evidence
"""

import testhome  # before anything that reads live paths
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import evidence
from evidence import Played


class TestEvidenceText(unittest.TestCase):
    def test_lists_every_lost_game_and_round_499_margins_per_screen_opponent(self) -> None:
        games = [
            Played("folder", "gen-1", "A", "a", "rounds", 499, 30, 22, Counter(hitWall=3), "", 60, 6, 20, 9, 30),
            Played("folder", "gen-1", "B", "b", "rounds", 499, 18, 25, Counter(hitSelf=1), "never clearly behind",
                   50, 12, 12, 11, 40),
            Played("swarm", "gen-2", "A", "b", "elimination", 212, 0, 9, Counter(hitHeadToHead=4), "fell behind"),
            Played("swarm", "gen-2", "B", "a", "rounds", 499, 20, 20, total=41, alive=3),
        ]
        text = evidence.evidence_text("abc123", "unswbc-1.1.0", 1, games)
        self.assertIn("Champion commit: abc123", text)
        self.assertIn("Toolchain: unswbc-1.1.0", text)
        self.assertIn("screen opponents folder, swarm", text)
        self.assertNotIn("vs the pool", text)
        self.assertIn("- folder: 2 games, 1-1-0 (W-L-D); 2 went to round 499, longest ours - theirs there mean +0.5 "
                      "(-7 to +8), 1 below 0; our second-longest there 16.0, total 55.0, <=8 dragons in 1. "
                      "At round 100 (means): 10.0 alive, 35.0 pearls eaten.", text)
        # Games without a replay give the round-499 facts the referee's results carry, and no opening.
        self.assertIn("- swarm: 2 games, 1-1-0 (W-L-D); 1 went to round 499, longest ours - theirs there mean +0.0 "
                      "(+0 to +0), 0 below 0; our second-longest there -, total 41.0, <=8 dragons in 1.\n", text)
        self.assertIn("Lost games (2):", text)
        self.assertIn("vs folder on gen-1 (we were side B): lost on length in round 499, longest 18 vs 25", text)
        self.assertIn("vs swarm on gen-2 (we were side A): lost by elimination in round 212; fell behind", text)
        self.assertIn("hitHeadToHead 1.0", text)

    def test_round_499_line_shows_each_sides_longest_at_400_and_how_much_ended_in_it(self) -> None:
        # M21's concentration facts. Real games: v0027 against earlyking on near_stronghold_880006, both sides
        # (2026-09-26), the referee's results with their replays' round-400 snapshots.
        games = [
            Played("earlyking", "gen_near_880006", "A", "b", "rounds", 500, 23, 35, total=110, alive=8, second=20,
                   alive100=32, pearls100=69, longest400=4, theirs400=11, total400=140, theirs_total400=161),
            Played("earlyking", "gen_near_880006", "B", "a", "rounds", 500, 33, 30, total=114, alive=7, second=20,
                   alive100=31, pearls100=73, longest400=6, theirs400=8, total400=155, theirs_total400=141),
        ]
        self.assertIn("- earlyking: 2 games, 1-1-0 (W-L-D); 2 went to round 499, longest ours - theirs there mean -4.5 "
                      "(-12 to +3), 1 below 0; our second-longest there 20.0, total 112.0, <=8 dragons in 2; longest at "
                      "round 400 ours 5.0, theirs 9.5; longest(r499)/total(r400) ours 0.19, theirs 0.22. At round 100 "
                      "(means): 31.5 alive, 71.0 pearls eaten.", evidence.evidence_text("abc", "e", 1, games))

    def test_round_400_lengths_come_from_round_400_with_theirs_the_other_side(self) -> None:
        # Real snapshots of tools/testdata/trauma-95034.replay, rounds 399-401 (side A changes each round), written
        # out rather than read from the replay.
        snap = lambda r, a, b: types.SimpleNamespace(round=r, longest={"A": a[0], "B": b[0]},  # noqa: E731
                                                     total={"A": a[1], "B": b[1]})
        snapshots = [snap(399, (3, 20), (4, 39)), snap(400, (4, 21), (4, 39)), snap(401, (3, 21), (4, 39))]
        for side, expected in (("A", (4, 4, 21, 39)), ("B", (4, 4, 39, 21))):
            game = Played("earlyking", "m", side, "a", "rounds", 500, 0, 0)
            evidence.read_round_400(game, snapshots)
            self.assertEqual((game.longest400, game.theirs400, game.total400, game.theirs_total400), expected)
        early = Played("earlyking", "m", "A", "b", "elimination", 399, 0, 3)
        evidence.read_round_400(early, snapshots[:1])  # a game that ended in round 399
        self.assertIsNone(early.longest400)

    def test_a_sample_with_no_losses_says_so(self) -> None:
        text = evidence.evidence_text("abc", "e", 1, [Played("grower", "m", "A", "a", "elimination", 300, 12, 0)])
        self.assertIn("- grower: 1 games, 1-0-0 (W-L-D); 0 went to round 499.", text)
        self.assertIn("No lost games in this sample.", text)


class TestChampionMoved(unittest.TestCase):
    def test_games_a_promotion_straddled_write_nothing(self) -> None:
        # The refresh holds no evaluation lock since MISSES 50, so a promotion can move the champion checkout while
        # its games play: they may then have played either champion, and its promotion's own refresh follows.
        game = Played("grower", "m", "A", "a", "elimination", 300, 12, 0)
        maps = tempfile.TemporaryDirectory()
        self.addCleanup(maps.cleanup)
        (Path(maps.name) / "m.map").write_text("MAP 5 5\nEND\n")
        for commits, expected in ((["abc", "abc"], True), (["abc", "def"], False)):
            with self.subTest(commits=commits), patch.object(evidence.driver, "git", side_effect=commits), \
                    patch.object(evidence.driver, "rotation_bots", return_value=[("grower", Path("grower"))]), \
                    patch.object(evidence, "play", return_value=[game]), \
                    patch.object(evidence, "TRAIN_MAPS", Path(maps.name)), \
                    patch.object(evidence.driver.maintenance, "toolchain", return_value={"epoch": "e"}):
                text = evidence.build_evidence(1)
                self.assertEqual(text is not None, expected)
                if text:
                    self.assertTrue(text.startswith("Champion commit: abc"))

    def test_games_that_fail_while_a_promotion_rewrites_the_champion_write_nothing(self) -> None:
        # A promotion checking the new champion out mid-build fails the games; that is the move, not a broken
        # refresh, so evidence.md isn't marked STALE for it. A failure with the champion unmoved still raises.
        maps = tempfile.TemporaryDirectory()
        self.addCleanup(maps.cleanup)
        (Path(maps.name) / "m.map").write_text("MAP 5 5\nEND\n")
        failed = RuntimeError("evidence games against grower failed: bot does not build")
        for commits, expected in ((["abc", "def"], None), (["abc", "abc"], failed)):
            with self.subTest(commits=commits), patch.object(evidence.driver, "git", side_effect=commits), \
                    patch.object(evidence.driver, "rotation_bots", return_value=[("grower", Path("grower"))]), \
                    patch.object(evidence, "play", side_effect=failed), \
                    patch.object(evidence, "TRAIN_MAPS", Path(maps.name)):
                if expected is None:
                    self.assertIsNone(evidence.build_evidence(1))
                else:
                    with self.assertRaises(RuntimeError):
                        evidence.build_evidence(1)


class TestShare(unittest.TestCase):
    def test_the_refresh_splits_a_tunes_share_so_it_neither_starves_nor_adds_a_player(self) -> None:
        # At the late screen's share (cores // 10 during an SPRT, shared with the official-map report) it had no game
        # on the 8-core and Azure hosts, and none anywhere while a tune played: 48 games could reach the hour's
        # timeout and leave evidence.md STALE. With a tune's share of its own it was a sixth player on mm's and mm1's
        # ten cores beside the coders, the review and a tune (MISSES 50). It now splits the tune's one share: with a
        # tune playing too, some hosts give it none, so this sums over the fleet.
        import native
        calls = []

        def referee(script, args, **options):
            calls.append(options)
            return types.SimpleNamespace(returncode=1, stderr="stopped")
        with tempfile.TemporaryDirectory() as folder, patch.object(evidence, "STATE", Path(folder)), \
                patch.object(evidence.driver, "referee", side_effect=referee), self.assertRaises(RuntimeError):
            evidence.play("grower", Path("grower"), [Path("m.map")], "auto")
        role = native.ROLES[calls[0]["yielding"]]
        fleet = {"mm": 10, "mm1": 10, "ll": 12, "zz": 16, "sz-wsl": 8, **{f"az-{i}": 2 for i in range(8)}}
        alone = beside = 0
        for offset, (name, cores) in enumerate(fleet.items()):
            refresh, tune = (native.host_share(cores, True, role, rank=rank, count=2, offset=offset) for rank in (0, 1))
            with self.subTest(host=name):
                self.assertEqual(refresh + tune, native.host_share(cores, True, role))  # one share, not two
            alone += native.host_share(cores, True, role)
            beside += refresh
        self.assertGreaterEqual(alone, 5)  # alone it plays on every host but sz-wsl
        self.assertGreaterEqual(beside, 1)  # beside a tune it still plays somewhere
        self.assertEqual(native.host_share(8, True, native.ROLES["1"], tuning=True), 0)  # the late screen's


if __name__ == "__main__":
    unittest.main()
