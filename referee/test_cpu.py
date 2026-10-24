"""The CPU gate's per-game rows (cpu.check), with the games faked. Run from referee/: python -m unittest test_cpu"""

import testhome  # before anything that reads live paths
import unittest
from pathlib import Path
from unittest.mock import patch

import cpu
from run import BotStats, GameResult, Points


def game(name: str, timeouts: int, top: int, host: str = "here") -> GameResult:
    side = BotStats(timeouts=timeouts, no_action_deaths=timeouts, points=Points(4_000_000, 20_000_000, top, 900))
    other = BotStats(points=Points(4_000_000, 27_700_000, 30_000_000, 900))
    return GameResult(name, "A", "a", "rounds", 500, side, other, 5.0, None, host=host)


class Check(unittest.TestCase):
    # E0186 failed the gate on 7 timeouts in 13 games and its result kept only the one summary line, so nobody could
    # say which maps timed out (MISSES 58). Each game's row names its map, where it played (the loop's gates play on
    # this Mac's platform's hosts too, 2026-09-29) and both sides' worst numbers, and the result counts each machine's.
    def test_each_game_gets_a_row_naming_its_map_its_machine_and_what_broke(self) -> None:
        games = [game("maps/ladder/trophy.map", 7, 99_200_000, "mm"), game("maps/ladder/default.map", 0, 40_000_000),
                 GameResult("maps/sprint/autarky.map", "A", None, None, None, None, None, 1.0, "engine exited 1",
                            host="here")]
        with patch.object(cpu, "play_games", return_value=games) as play:
            passed, line, points = cpu.check(Path("bot"), [Path(g.map) for g in games], "auto", hosts=True)
        self.assertTrue(play.call_args.kwargs["sandbox_hosts"])
        self.assertFalse(passed)
        self.assertIn("timeouts 7", line)
        self.assertEqual(points["games"], [
            {"map": "trophy.map", "host": "mm", "timeouts": 7, "crashes": 0, "no_action_deaths": 7, "p99": 27_700_000,
             "max": 99_200_000},
            {"map": "default.map", "host": "here", "timeouts": 0, "crashes": 0, "no_action_deaths": 0,
             "p99": 27_700_000, "max": 40_000_000},
            {"map": "autarky.map", "host": "here", "error": "engine exited 1"}])
        self.assertEqual(points["hosts"], {"mm": 1, "here": 2})


if __name__ == "__main__":
    unittest.main()
