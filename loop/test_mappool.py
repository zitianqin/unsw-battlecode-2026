"""The map pool (loop/mappool.py): a new champion's shared set takes never-used pooled maps first and makes only the
rest, so each pooled map lands in one set and leaves the pool; a pool made by other map inputs is emptied, not used;
the fill makes chunks up to one set's worth and the tick starts it only while the pool is short; health alerts past the
size bound. Map making is faked (gen.py's file names, no engine), so these tests play and build nothing."""

import testhome  # before anything that reads live paths
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import mappool

STATE = driver.STATE


def clear_home() -> None:
    for path in STATE.iterdir():
        shutil.rmtree(path) if path.is_dir() else path.unlink()


# Stands in for gen.py (the files it names: gen_<style>_<seed>.map) and counts the maps it made.
class FakeGen:
    def __init__(self) -> None:
        self.made: list[str] = []

    def sh(self, cmd, cwd=None, check=True, timeout=None, env=None):
        args = dict(zip(cmd, cmd[1:]))
        out, seed, count, style = Path(args["--out"]), int(args["--seed"]), int(args["--count"]), args["--style"]
        for s in range(seed, seed + count):
            (out / f"gen_{style}_{s}.map").write_text(f"{style} {s}\n")
            self.made.append(f"gen_{style}_{s}.map")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class Pool(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        self.gen = FakeGen()
        for patcher in (patch.object(driver, "sh", self.gen.sh), patch.object(driver, "check_maps", return_value={}),
                        patch.object(mappool, "inputs", return_value="mapgen@abc 1.2.0"),
                        patch.object(driver, "referee_styles", return_value=("worldgen",))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def pool(self, source: str, seeds: range) -> None:
        folder = mappool.pool_dir(STATE) / source
        folder.mkdir(parents=True, exist_ok=True)
        (mappool.pool_dir(STATE) / "INPUTS").write_text("mapgen@abc 1.2.0\n")
        for s in seeds:
            (folder / f"gen_{source}_{s}.map").write_text(f"pooled {s}\n")

    def test_a_shared_set_takes_pooled_maps_first_and_makes_only_the_rest(self) -> None:
        self.pool("eval", range(900, 910))
        folder = STATE / "shared-maps" / "set"
        folder.mkdir(parents=True)
        with patch.object(driver, "MAX_PAIRS", 25):
            maps = driver.shared_set_maps(folder, 5000)
        self.assertEqual(len(maps), 25)
        names = {Path(m).name for m in maps}
        self.assertEqual({f"gen_eval_{s}.map" for s in range(900, 910)} <= names, True)  # every pooled one, once
        self.assertEqual(len(self.gen.made), 15)  # the seed made only the rest (before the pool: all 25)
        self.assertEqual(mappool.held(STATE, "eval"), [])  # none left to play again
        # A second set of the same champion finds the pool empty and makes all its own.
        other = STATE / "shared-maps" / "other"
        other.mkdir()
        with patch.object(driver, "MAX_PAIRS", 25):
            self.assertEqual(len(driver.shared_set_maps(other, 7000)), 25)
        self.assertEqual(len(self.gen.made), 40)

    def test_a_mixed_set_takes_each_source_from_its_own_pool_lowest_seeds_first(self) -> None:
        self.pool("worldgen", range(100, 130))
        folder = STATE / "shared-maps" / "mixed"
        folder.mkdir(parents=True)
        with patch.object(driver, "MAX_PAIRS", 40), patch.object(driver, "missing_style", return_value=None):
            maps = driver.shared_set_maps(folder, 5000, {"worldgen": 0.5})
        worldgen = sorted(Path(m).name for m in maps if "/worldgen/" in m)
        self.assertEqual(len(worldgen), 20)
        self.assertEqual(worldgen, sorted(f"gen_worldgen_{s}.map" for s in range(100, 120)))
        self.assertEqual(len(mappool.held(STATE, "worldgen")), 10)
        self.assertFalse(any("worldgen" in name for name in self.gen.made))

    def test_maps_made_by_other_map_inputs_are_dropped_not_played(self) -> None:
        self.pool("eval", range(900, 910))
        (mappool.pool_dir(STATE) / "INPUTS").write_text("mapgen@old 1.2.0\n")
        out = STATE / "set"
        self.assertEqual(mappool.take(out, "eval", 5, STATE), 0)
        self.assertEqual(mappool.held(STATE, "eval"), [])
        self.assertEqual(mappool.stamp(STATE), "mapgen@abc 1.2.0")

    def test_the_fill_makes_one_sets_worth_and_the_tick_starts_it_only_while_short(self) -> None:
        (STATE / "ideas.jsonl").write_text("")
        with patch.object(mappool, "targets", return_value={"worldgen": 70, "eval": 10}), \
                patch.object(mappool, "room", return_value=True):
            with patch.object(mappool, "launch", return_value=os.getpid()) as launch:
                self.assertIn("80 maps short", mappool.tick(driver.REPO, STATE))
                self.assertIsNone(mappool.tick(driver.REPO, STATE))  # alive
            mappool.save(STATE, {**mappool.read(STATE), "pid": None})
            mappool.fill(STATE)
            self.assertEqual((len(mappool.held(STATE, "worldgen")), len(mappool.held(STATE, "eval"))), (70, 10))
            self.assertEqual(mappool.short(STATE), {})
            with patch.object(mappool, "launch", return_value=os.getpid()) as launch:
                self.assertIsNone(mappool.tick(driver.REPO, STATE))  # full
            launch.assert_not_called()
        self.assertEqual({re.sub(r"_\d+\.map$", "", n) for n in self.gen.made}, {"gen_worldgen", "gen_eval"})
        self.assertFalse((mappool.pool_dir(STATE) / ".making").exists())

    def test_a_style_the_referee_cant_make_yet_is_skipped_not_failed(self) -> None:
        with patch.object(mappool, "targets", return_value={"worldgen": 70, "newstyle": 10, "eval": 10}), \
                patch.object(driver, "referee_styles", return_value=("worldgen",)):
            self.assertEqual(mappool.short(STATE), {"worldgen": 70, "eval": 10})

    def test_room_needs_the_load_average_under_where_sessions_stop_too(self) -> None:
        import home
        cores = os.cpu_count() or 1
        with patch.object(home, "dev_load", return_value=0.0), patch.object(home, "share_now", return_value=0.5):
            with patch.object(os, "getloadavg", return_value=(0.0, 0.0, 0.0)):
                self.assertTrue(mappool.room())
            with patch.object(os, "getloadavg", return_value=(float(cores), 0.0, 0.0)):
                self.assertFalse(mappool.room())  # niced work fills the cores: sig's sessions see the load

    def test_health_alerts_when_no_chunk_was_made_two_hours_after_the_merge(self) -> None:
        with patch.object(mappool, "targets", return_value={"eval": 10}), \
                patch.object(health, "mappool_landed", return_value=1000.0):
            self.assertEqual(health.map_pool(STATE, 1000.0 + health.FIRST_FIRING - 1, driver.REPO), [])
            late = 1000.0 + health.FIRST_FIRING + 1
            self.assertEqual([a.key for a in health.map_pool(STATE, late, driver.REPO)], ["map-pool:never"])
            mappool.save(STATE, {"made": 10})
            self.assertEqual(health.map_pool(STATE, late, driver.REPO), [])

    def test_health_alerts_past_the_size_bound(self) -> None:
        self.pool("eval", range(3))
        self.assertEqual(health.map_pool(STATE, 0), [])
        with patch.object(mappool, "BOUND_BYTES", 10):
            self.assertEqual([a.key for a in health.map_pool(STATE, 0)], ["map-pool:size"])


if __name__ == "__main__":
    unittest.main()
