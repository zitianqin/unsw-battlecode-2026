"""Tests for the targeted ideas' per-game measures (measures.py). Run from referee/: python -m unittest test_measures

Real replays are read the way a target batch reads them, each in its own interpreter (`measures.py read`), which
keeps the read under a timeout (the two replay schemas load in one process since tools/replays.py got its own
parser, MISSES 97)."""

import testhome  # before anything that reads live paths
import shutil
import tempfile
import unittest
from pathlib import Path

import measures

ROOT = Path(__file__).resolve().parent.parent
TESTDATA = ROOT / "tools/testdata"


class TestMeasures(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    # The engine's own end reasons, one side's apiece: in online game 403920 we (A) stepped into kelp 20 times, the
    # opponent never; in E0102's SPRT game 0308, a local replay, the candidate (A) 3 times and the opponent 32.
    def test_wall_deaths_count_each_sides_kelp_deaths_in_real_replays(self) -> None:
        found = measures.read_all("wall_deaths", [TESTDATA / "queen-403920.replay", TESTDATA / "fold-e0102-0308.replay"])
        self.assertEqual(found, [{"A": 20.0, "B": 0.0}, {"A": 3.0, "B": 32.0}])

    def test_pocket_measures_read_watch_doom_kinds_for_each_side(self) -> None:
        path = TESTDATA / "queen-403920.replay"
        # watch.py doom there (2026-09-28): ours 23 pocket deaths, 5 shut in by a teammate; theirs 12 and 2. Each of
        # our 20 kelp deaths was doomed first, so none is an undoomed wall death.
        self.assertEqual([measures.read_one(name, path) for name in ("pocket_deaths", "pocket_teammate_dooms",
                                                                     "undoomed_wall_deaths")],
                         [{"A": 23.0, "B": 12.0}, {"A": 5.0, "B": 2.0}, {"A": 0.0, "B": 0.0}])

    # A target batch reads only our side of each replay (E0375, 2026-09-30: 288 replays read for 32 min after its
    # games): judging one team's dragons gives that side's count unchanged, the other's None.
    def test_one_sides_read_gives_that_sides_count_alone(self) -> None:
        path = TESTDATA / "queen-403920.replay"
        self.assertEqual([measures.read_one("pocket_deaths", path, side=side) for side in "AB"],
                         [{"A": 23.0, "B": None}, {"A": None, "B": 12.0}])
        self.assertEqual(measures.read_one("pocket_teammate_dooms", path, side="B"), {"A": None, "B": 2.0})
        self.assertEqual(measures.read_one("wall_deaths", path, side="A"), {"A": 20.0, "B": None})

    # M75: online game 95037 went to r499 with A 20 long in all at r400 and its longest 6 at r499, B 78 and 7 (B won
    # the round-500 race on a king that kept 9% of its team); trophy 143349 ended in r413, so neither side has one.
    def test_fold_conversion_is_the_longest_at_r499_over_the_total_at_r400_or_none(self) -> None:
        found = measures.read_all("fold_conversion", [TESTDATA / "queen-95037.replay",
                                                      TESTDATA / "trophy-143349.replay"])
        self.assertEqual(found, [{"A": 6 / 20, "B": 7 / 78}, {"A": None, "B": None}])

    def test_pocket_measures_read_only_maps_with_a_pocket(self) -> None:
        maps = [str(ROOT / "maps/ladder" / name) for name in ("queen_of_spades.map", "trophy.map")]
        self.assertEqual(measures.maps_read("pocket_deaths", maps), maps[:1])
        self.assertEqual(measures.maps_read("wall_deaths", maps), maps)  # read everywhere: no check to run

    # A replay the code can't read, or one that takes too long, is an error text, never an exception: the batch
    # leaves it out and says so.
    def test_an_unreadable_or_timed_out_replay_is_an_error_not_a_raise(self) -> None:
        broken = self.folder / "0001-broken-aA.replay"
        broken.write_bytes(b"not a replay")
        self.assertIsInstance(measures.read_one("wall_deaths", broken), str)
        slow = measures.read_one("pocket_deaths", TESTDATA / "queen-403920.replay", timeout=0.05)
        self.assertEqual(slow, "timed out after 0.05 s")


if __name__ == "__main__":
    unittest.main()
