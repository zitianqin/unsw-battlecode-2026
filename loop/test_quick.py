"""quick.py's failure count leaves out capfeed fires (P.capFeedInPlace's by-design illegal split, E0411 on
gen_normal_10, 30 Sep), read from a real quick game's replay: tools/testdata/quick-e0411-dilemma-aB.replay, E0411's
candidate as quick.py now plays it (its logging copy, B) against its champion on dilemma, played 30 Sep on the hosts.
No real replay with a capfeed-fire noValidAction death was at hand (24 such quick games and 7 logged rule-game replays
had none), so the positive case relabels one of that game's real deaths of ours; its logged line keys as a real
capfeed fire does (rule-games 1790681595558774000's champion 0001: 'capfeed fire' at r387 on dragon 1318, dying r387).

    cd loop && python -m unittest test_quick
"""

import testhome  # noqa: F401  (before anything that reads live paths)
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quick
from run import BotStats, GameResult

REPLAY = quick.REPO / "tools/testdata/quick-e0411-dilemma-aB.replay"
sys.path[:0] = [str(quick.REPO / "tools")]
import watch  # noqa: E402


def result(side: str, no_action: int) -> GameResult:
    return GameResult("dilemma.map", side, "a", "rounds", 500, BotStats(no_action_deaths=no_action), BotStats(),
                      1.0, None)


class CapfeedDeaths(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.replays = Path(folder)
        shutil.copy(REPLAY, self.replays / "0001-dilemma-aB.replay")  # the game's index and seat, as run.py names it

    def test_a_death_without_a_capfeed_fire_line_stays_a_failure(self) -> None:
        results = [result("A", 0), result("B", 1)]
        found = quick.capfeed_deaths(results, self.replays)
        self.assertEqual(found, {1: 0})  # read, and none of the game's deaths is a capfeed fire
        self.assertEqual(quick.failures(results, found), 1)

    def test_a_no_valid_action_death_on_a_capfeed_fire_turn_is_left_out(self) -> None:
        real = watch.watch(REPLAY)
        ours = next(d for d in real.game.deaths if d["team"] == "B")
        theirs = next(d for d in real.game.deaths if d["team"] == "A")
        for d in (ours, theirs):
            d["reason"] = "noValidAction"
            real.logs.append((d["round"], d["id"], d["team"], "rule capfeed capfeed fire"))
        with patch.object(watch, "watch", return_value=real):
            found = quick.capfeed_deaths([result("A", 0), result("B", 1)], self.replays)
        self.assertEqual(found, {1: 1})  # ours only: the opponent's feeds are never our failures
        self.assertEqual(quick.failures([result("A", 0), result("B", 1)], found), 0)

    def test_the_logging_copy_writes_the_debug_line_in_the_game(self) -> None:
        self.assertTrue(any(team == "B" for _, _, team, _ in watch.watch(REPLAY).logs))


if __name__ == "__main__":
    unittest.main()
