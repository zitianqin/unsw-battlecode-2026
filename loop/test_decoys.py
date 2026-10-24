"""loop/decoys.py: a team's latest ranked version scoring below its best earlier one beyond luck is a possible decoy
(REFLECT 2026-09-30 09:12 F1, MISSES 103)."""
import testhome  # noqa: F401  before anything that reads live paths
import json
import unittest
from pathlib import Path

import decoys
import maintenance
import risers

# The leader study's ranked index rows of 30 Sep 10:40 for teams 470 and 249 (study-replays/index.jsonl, trimmed to
# the fields read): 470's latest unnamed games won 3 of 17 after submission 7771 won 26 of 43; 249's latest 10 of 20.
ROWS = [json.loads(line) for line in
        (Path(__file__).resolve().parent / "testdata/decoys-index-sample.jsonl").read_text().splitlines()]


class Decoys(unittest.TestCase):
    def test_a_latest_version_below_the_best_earlier_beyond_luck_is_flagged(self) -> None:
        flagged = decoys.check(maintenance.decoy_games(ROWS, 470))
        self.assertEqual((flagged["best"], flagged["best_won"], flagged["best_n"]), ("submission 7771", 26, 43))
        self.assertEqual((flagged["won"], flagged["n"]), (3, 17))
        self.assertTrue(flagged["flagged"], flagged)
        self.assertIn("possible decoy", decoys.line(flagged))
        self.assertFalse(decoys.check(maintenance.decoy_games(ROWS, 470), looks=20)["flagged"])  # over 20 teams
        level = decoys.check(maintenance.decoy_games(ROWS, 249))
        self.assertFalse(level["flagged"], level)

    def test_unnamed_games_are_cut_into_blocks_from_the_newest_and_a_small_best_is_no_evidence(self) -> None:
        # 3 of 3, then 13 of 20: the 3-of-3 version doesn't make 13 of 20 look like a decoy (it did when the best
        # was the highest share, 30 Sep: forgot to mention's and 801's latest).
        games = [(9770, 0.0 + i, 1.0) for i in range(3)] + [(None, 10.0 + i, float(i % 3 != 0)) for i in range(20)]
        self.assertEqual([(len(v[2])) for v in decoys.versions(games)], [3, 20])
        self.assertFalse(decoys.check(games)["flagged"])
        # 45 unnamed games: blocks of 20 from the newest back, the oldest short.
        self.assertEqual([len(v[2]) for v in decoys.versions([(None, float(i), 1.0) for i in range(45)])], [5, 20, 20])
        self.assertIsNone(decoys.check([(None, float(i), 1.0) for i in range(20)]))  # one version: nothing to compare

    def test_the_riser_study_reads_a_battles_games_from_its_side(self) -> None:
        battles = [{"at": "2026-09-30T00:00:00Z", "a": {"id": 1}, "b": {"id": 2},
                    "resultsA": ["win", "loss", "draw", "win", "pending"]}]
        self.assertEqual([score for _, _, score in risers.decoy_games(battles, 2)], [0.0, 1.0, 0.5, 0.0])


if __name__ == "__main__":
    unittest.main()
