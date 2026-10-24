"""SPSA tuner pieces, without games."""

import testhome  # before anything that reads live paths
import io
import json
import sys
import tempfile
import time
import types
import unittest
from collections import namedtuple
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import tune

PARAMS = """struct Params {
    int splitAtLength = 4;       // split once the dragon is at least this long
    double spaceWeight = 2.5;    // per reachable tile
    int penalty = -40;           // a negative score
    bool kingSeeksFeeders = true; // a switch, never tuned
};
"""


class TestTune(unittest.TestCase):
    def test_reads_numbers_only_and_rewrites_them_in_place(self) -> None:
        self.assertEqual(tune.read_params(PARAMS), {"splitAtLength": ("int", 4.0), "spaceWeight": ("double", 2.5),
                                                    "penalty": ("int", -40.0)})
        text = tune.write_params(PARAMS, {"splitAtLength": 5.4, "spaceWeight": 3.0})
        self.assertIn("    int splitAtLength = 5;       // split once the dragon is at least this long\n", text)
        self.assertIn("    double spaceWeight = 3.0;    // per reachable tile\n", text)
        self.assertIn("    int penalty = -40;", text)
        self.assertIn("bool kingSeeksFeeders = true;", text)

    def test_values_keep_their_sign_and_stay_within_4x(self) -> None:
        self.assertEqual(tune.clamp(-3, 4), 1)
        self.assertEqual(tune.clamp(99, 4), 16)
        self.assertEqual(tune.clamp(5, -40), -10)
        self.assertEqual(tune.clamp(0.4, 0.5), 0.4)
        self.assertEqual(tune.clamp(-0.1, 0.5), 0.0)  # a double under 1 may reach 0

    def test_an_int_near_zero_never_plays_0_or_crosses_sign(self) -> None:
        # a quarter of 1 or 2 rounds to 0, so those ints stop at 1 (foldMinLength 1 would have played 0)
        for start in (1, 2, 3, 5, -1, -2, -3):
            with self.subTest(start=start):
                sign = 1 if start > 0 else -1
                low, high = tune.clamp_range("int", start)
                self.assertGreaterEqual(sign * low, 1)
                self.assertGreaterEqual(sign * high, 1)
                for value in (-50.0, -0.4, 0.0, 0.3, 50.0):
                    self.assertGreaterEqual(sign * round(tune.clamp(value, start, "int")), 1)
        self.assertEqual(tune.clamp(0.3, 3, "int"), 0.75)  # 3 and up keep their quarter, which plays 1
        theta = {"foldMinLength": 1.0}
        for _ in range(5):  # the side with it lower keeps winning
            theta = tune.spsa_step(theta, [({"foldMinLength": -1}, 1.0, 2.0)], 1, {"foldMinLength": 1.0},
                                   {"foldMinLength": 1.0}, {"foldMinLength": "int"})
        self.assertEqual(theta, {"foldMinLength": 1.0})

    def test_nudges_are_scored_against_opponents_as_plus_minus_minus(self) -> None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "referee"))
        bots = [(Path("plus0"), Path("minus0")), (Path("plus1"), Path("minus1"))]
        maps = iter(Path(f"m{i}.map") for i in range(4))
        planned = tune.plan_games(bots, 2, maps, [None, Path("swarm")])
        # perturbation 0: a head-to-head pair, then an opponent map; perturbation 1 the same
        self.assertEqual([(w, j.a.name, j.b.name) for w, j in planned[:6]],
                         [((0, "pair"), "plus0", "minus0"), ((0, "pair"), "plus0", "minus0"),
                          ((0, 1), "plus0", "swarm"), ((0, 1), "plus0", "swarm"),
                          ((0, -1), "minus0", "swarm"), ((0, -1), "minus0", "swarm")])
        owner = [w for w, _ in planned]
        # plus wins everything, minus loses to the swarm: the best score, 1
        winners = ["a" if j.a.name.startswith("plus") else "b" for _, j in planned]
        self.assertEqual([d / 4 for d in tune.score_games(owner, winners, 2)], [1.0, 1.0])
        # without opponents the score is the old 2 * points / games - 1
        planned = tune.plan_games(bots[:1], 2, iter(Path(f"m{i}.map") for i in range(2)), [None])
        self.assertEqual(tune.score_games([w for w, _ in planned], ["a", "b", "a", "a"], 1)[0] / 4, 2 * 3 / 4 - 1)
        self.assertIsNone(tune.plan_games(bots, 2, iter([Path("m.map")]), [None]))
        # MISSES 99: with fixed keys, minus plays plus head to head as its copy on another key, opponents as its own
        keyed = [(Path("plus0"), Path("minus0"), Path("minus0-alt"))]
        planned = tune.plan_games(keyed, 2, iter(Path(f"m{i}.map") for i in range(2)), [None, Path("swarm")])
        self.assertEqual([(j.a.name, j.b.name) for _, j in planned],
                         [("plus0", "minus0-alt")] * 2 + [("plus0", "swarm")] * 2 + [("minus0", "swarm")] * 2)

    def test_a_step_moves_toward_the_side_that_won(self) -> None:
        theta, starts = {"a": 10.0, "b": 10.0}, {"a": 10.0, "b": 10.0}
        nudges = {"a": 2.0, "b": 2.0}
        # "plus" (a up, b down) won clearly: a rises, b falls.
        moved = tune.spsa_step(theta, [({"a": 1, "b": -1}, 0.5, 1.0)], 1, nudges, starts, {"a": "int", "b": "double"})
        self.assertEqual(moved, {"a": 11.0, "b": 9.0})
        # Two trials that disagree on b cancel out for b.
        moved = tune.spsa_step(theta, [({"a": 1, "b": 1}, 0.5, 1.0), ({"a": 1, "b": -1}, 0.5, 1.0)], 2, nudges, starts,
                               {"a": "int", "b": "double"})
        self.assertEqual(moved, {"a": 11.0, "b": 10.0})

    def test_a_round_of_several_iterations_weighs_each_perturbation_as_its_iteration_would(self) -> None:
        # E0147's and E0154's tunes kept 7.2 and 10.4 games in flight on 58-76 slots, one 42-game iteration at a time.
        theta = starts = {"a": 10.0}
        nudges, kinds = {"a": 1.0}, {"a": "double"}
        first = [({"a": 1}, 0.25, tune.gain_at(0, 50)), ({"a": -1}, 0.5, tune.gain_at(0, 50))]
        second = [({"a": 1}, 0.75, tune.gain_at(1, 50)), ({"a": 1}, -0.25, tune.gain_at(1, 50))]
        classic = tune.GAIN * ((0.25 - 0.5) / 2)  # the old step: gain * mean(score * delta) * nudge
        self.assertEqual(tune.spsa_step(theta, first, 2, nudges, starts, kinds), {"a": 10.0 + classic})
        # One round of both iterations moves by the sum of their steps, each at its own iteration's gain.
        both = tune.spsa_step(theta, first + second, 2, nudges, starts, kinds)["a"] - 10.0
        self.assertAlmostEqual(both, classic + tune.gain_at(1, 50) * (0.75 - 0.25) / 2)

    def test_a_round_fills_about_three_waves_of_the_free_slots_within_its_bounds(self) -> None:
        # 4 perturbations of 10.5 games each (3 maps, three rotation opponents) is the classic 42-game iteration.
        self.assertEqual(tune.batch_size(0, 10.5, 4, 200), 4)  # nothing measured yet: one iteration
        self.assertEqual(tune.batch_size(38, 10.5, 4, 200), 11)  # E0154's hosts at the tune's half share
        self.assertEqual(tune.batch_size(100, 10.5, 4, 200), 4 * tune.MAX_ROUND)
        self.assertEqual(tune.batch_size(38, 10.5, 4, 3), 3)  # the budget's last perturbations

    def test_the_rotation_seats_a_map_by_its_place_in_the_whole_tune(self) -> None:
        # driver.mixed_maps deals a map mix by sprt_seat, i % the rotation, so a round starting mid-rotation continues it.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "referee"))
        rotation = [None, Path("swarm"), Path("rammer"), Path("breeder")]
        bots = [(Path("plus0"), Path("minus0"))]
        planned = tune.plan_games(bots, 3, iter(Path(f"m{i}.map") for i in range(3)), rotation, first=6)
        self.assertEqual([j.b.name for _, j in planned[::4]], ["rammer", "breeder", "minus0"])  # maps 6, 7 and 8



# Between rounds no game runs on a host, and its last measurement is the round's start, older than remote.STALE_S, so
# Host.capacity alone allowed min(free, active) = 0 there: every round after the first was one iteration.
class TestFreeSlots(unittest.TestCase):
    def test_a_stale_host_is_measured_before_the_round_is_sized(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "referee"))
        import remote
        stale, failing, resting = remote.Host("mm", 16), remote.Host("ll", 16), remote.Host("zz", 16)
        for host in (stale, failing, resting):
            host._free = (time.monotonic() - 10 * remote.STALE_S, 6)  # measured at the last round's start: capacity 0

        def fresh(host):
            def measure() -> int:
                with host._measure_lock:
                    host._free = (time.monotonic(), 16)
                return 16
            return measure

        def fail() -> int:
            raise remote.RemoteError("ll: timed out after 30s")

        resting.rested_until = time.monotonic() + 60
        run = types.SimpleNamespace(game_slots=lambda workers, active=0: 1)  # this Mac under load
        with patch.object(remote, "load_hosts", return_value=[stale, failing, resting]), \
                patch.object(stale, "measure", fresh(stale)), patch.object(failing, "measure", fail), \
                patch.object(resting, "measure") as asked, redirect_stderr(io.StringIO()):
            free = tune.free_slots(run)
        self.assertEqual(free, 1 + 16)
        self.assertFalse(failing.usable())  # rested, as the scheduler rests a host whose measurement failed
        self.assertFalse(asked.called)  # a resting host isn't asked
        self.assertEqual(tune.batch_size(free, 10.5, 4, 200), 5)  # a round bigger than one iteration's 4

    def test_a_round_is_sized_by_each_hosts_room_where_native_gives_one(self) -> None:
        # Between E0180's rounds the idle fillers filled its half of every host; its capacity then read those hosts
        # full. Under native.py a host's room (its capacity plus the fillers' games there, which step back) sizes it.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "referee"))
        import remote
        host = remote.Host("mm", 16)
        run = types.SimpleNamespace(game_slots=lambda workers, active=0: 0)
        with patch.object(remote, "load_hosts", return_value=[host]), patch.object(host, "measure", lambda: 0), \
                patch.object(host, "capacity", lambda: 0), patch.object(remote.Host, "room", lambda self: 5, create=True):
            self.assertEqual(tune.free_slots(run), 5)


# The fold numbers as champion v0029 has them: the fold (397) starts before the beacon (405), so nobody is king yet.
FOLD = """struct Params {
    int beaconRound = 405;       // the king reports from this round
    int foldRound = 397;         // ... dragons walk to it from this round
    int mergeRound = 450;
    int foldMergeRadius = 4;
    int kingReportAge = 10;
    double foldWeight = 2.5;
    double feedWeight = 3.0;
    bool kingHoldsSplits = true;
};
"""


class TestConstraints(unittest.TestCase):
    kinds = tune.param_kinds(FOLD)

    def orders(self, *texts: str, tuned=("beaconRound", "foldRound", "mergeRound")) -> list:
        return tune.parse_orders(list(texts), self.kinds, list(tuned))

    def test_orders_read_both_directions_and_strict_needs_ints(self) -> None:
        self.assertEqual(self.orders("beaconRound <= foldRound", "mergeRound>foldRound"),
                         [tune.Order("beaconRound", "foldRound", 0, "beaconRound <= foldRound"),
                          tune.Order("foldRound", "mergeRound", 1, "mergeRound>foldRound")])
        for bad, why in (("beaconRound =< foldRound", "not 'a <= b'"), ("beaconRound == foldRound", "not 'a <= b'"),
                         ("beaconRound <= foldRound <= mergeRound", "not 'a <= b'"),
                         ("beaconRound <= fold", "not in params.hpp"), ("kingHoldsSplits <= foldRound", "bool"),
                         ("foldRound <= foldRound", "itself"), ("foldWeight <= feedWeight", "no tuned param"),
                         ("foldWeight < foldRound", "strict order needs two ints")):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, why):
                self.orders(bad, tuned=("foldRound",))

    def bounds(self, orders, fixed=(), starts=None):
        starts = starts or {"beaconRound": 405, "foldRound": 397, "mergeRound": 450}
        kinds = {n: "int" for n in starts}
        ranges = {n: (v, v) if n in fixed else tune.clamp_range("int", v) for n, v in starts.items()}
        return tune.feasible_bounds(orders, ranges, kinds), kinds

    def test_a_nudge_that_breaks_an_order_meets_at_the_midpoint_rounded_so_it_holds(self) -> None:
        orders = self.orders("beaconRound <= foldRound")
        bounds, kinds = self.bounds(orders)
        # 410 and 401 (an int pair) meet: 405 + 406 keeps their sum and the order
        self.assertEqual(tune.project({"beaconRound": 410.0, "foldRound": 401.0}, orders, kinds, bounds),
                         {"beaconRound": 405, "foldRound": 406})
        # rounding: 405.4 plays 405 and 404.6 plays 405, so the order already holds and nothing moves
        self.assertEqual(tune.project({"beaconRound": 405.4, "foldRound": 404.6}, orders, kinds, bounds),
                         {"beaconRound": 405.4, "foldRound": 404.6})
        # a strict order between ints keeps a gap of 1 on the values played
        orders = self.orders("beaconRound < foldRound")
        moved = tune.project({"beaconRound": 405.4, "foldRound": 404.6}, orders, kinds, bounds)
        self.assertLess(round(moved["beaconRound"]), round(moved["foldRound"]))

    def test_a_fixed_param_never_moves_and_the_tuned_one_goes_to_it(self) -> None:
        orders = self.orders("beaconRound <= foldRound", tuned=("foldRound",))
        bounds, kinds = self.bounds(orders, fixed=("beaconRound",))
        self.assertEqual(tune.project({"beaconRound": 405, "foldRound": 390.3}, orders, kinds, bounds),
                         {"beaconRound": 405, "foldRound": 405})

    def test_a_chain_of_doubles_ends_with_every_order_holding(self) -> None:
        kinds = {"a": "double", "b": "double", "c": "double"}
        orders = tune.parse_orders(["a <= b", "b <= c"], kinds, ["a", "b", "c"])
        bounds = tune.feasible_bounds(orders, {n: (-100.0, 100.0) for n in kinds}, kinds)
        moved = tune.project({"a": 10.0, "b": 5.0, "c": 0.0}, orders, kinds, bounds)
        self.assertLessEqual(moved["a"], moved["b"])
        self.assertLessEqual(moved["b"], moved["c"])

    def test_constraints_the_clamps_make_impossible_fail_before_any_game(self) -> None:
        # foldRound tuned from 90 reaches at most 360, below a fixed beacon at 405
        orders = self.orders("beaconRound <= foldRound", tuned=("foldRound",))
        with self.assertRaisesRegex(ValueError, "can't hold within the 4x clamps"):
            self.bounds(orders, fixed=("beaconRound",), starts={"beaconRound": 405, "foldRound": 90})
        # a chain of three strict orders needs three values; ints between 1 and 2 have two
        kinds = {"a": "int", "b": "int", "c": "int"}
        orders = tune.parse_orders(["a < b", "b < c"], kinds, ["a", "b", "c"])
        with self.assertRaisesRegex(ValueError, "can't hold"):
            tune.feasible_bounds(orders, {n: (1, 2) for n in kinds}, kinds)
        with self.assertRaisesRegex(ValueError, "cycle"):
            tune.feasible_bounds(tune.parse_orders(["a < b", "b < a"], kinds, ["a"]), {n: (1, 9) for n in kinds},
                                 kinds)


# Plays tune.main on FOLD without games: the fake scheduler reads each bot's params.hpp, so the test sees every value
# played, and the bot folding earlier always wins, pushing foldRound down toward (and past) the beacon.
class TestTuneReplay(unittest.TestCase):
    def tune(self, *extra: str, free: int = 0) -> tuple[int, list[dict], Path]:
        played: list[dict] = []
        Job = namedtuple("Job", "map a b side")
        Result = namedtuple("Result", "winner error")

        def values(bot: Path) -> dict:
            return {n: v for n, (_, v) in tune.read_params((bot / "params.hpp").read_text()).items()}

        def stream_games(jobs, bots, workers, local):
            for job in jobs:
                a, b = values(job.a), values(job.b)
                played.extend((a, b))
                yield Result("a" if a["foldRound"] < b["foldRound"] else "b" if b["foldRound"] < a["foldRound"]
                             else "draw", None)
        # `free` game slots here and no game hosts: 0 plays the classic one iteration a round
        run = types.SimpleNamespace(Job=Job, stream_games=stream_games, game_slots=lambda workers, active=0: free)
        remote = types.SimpleNamespace(load_hosts=lambda: [], RemoteError=RuntimeError)
        unswbc_run = types.SimpleNamespace(_resolve=lambda bot, sandbox: None)
        folder = Path(tempfile.mkdtemp())
        (folder / "bot").mkdir()
        (folder / "bot/params.hpp").write_text(FOLD)
        argv = ["tune.py", "--bot", str(folder / "bot"), "--params", "foldRound", "mergeRound", "foldMergeRadius",
                "kingReportAge", "--iterations", "12", "--seed", "3", "--out", str(folder / "tune.json"),
                "--maps", *(f"m{i}.map" for i in range(200)), *extra]
        with patch.dict(sys.modules, {"run": run, "remote": remote, "unswbc": types.ModuleType("unswbc"),
                                      "unswbc.run": unswbc_run}), \
                patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()), \
                redirect_stderr(err := io.StringIO()):
            code = tune.main()
        self.stderr = err.getvalue()
        return code, played, folder

    def test_i237_never_plays_the_fold_before_a_fixed_beacon(self) -> None:
        code, played, folder = self.tune()
        self.assertEqual(code, 0)
        self.assertTrue(any(v["foldRound"] < v["beaconRound"] for v in played))  # unconstrained, it goes below
        code, played, folder = self.tune("--constraint", "beaconRound <= foldRound")
        self.assertEqual(code, 0)
        self.assertTrue(played)
        self.assertEqual([v for v in played if v["foldRound"] < v["beaconRound"]], [])
        final = tune.read_params((folder / "bot/params.hpp").read_text())
        self.assertGreaterEqual(final["foldRound"][1], final["beaconRound"][1])
        log = json.loads((folder / "tune.json").read_text())
        self.assertEqual(log["constraints"], ["beaconRound <= foldRound"])
        self.assertEqual(log["projections"][0], {"round": 0, "point": "start", "moved": {"foldRound": [397, 405]}})
        # each round's log line counts the played points moved, not the estimate's own move
        for k in range(1, 13):
            played_moves = sum(row["round"] == k and row["point"] != "theta" for row in log["projections"])
            line = next(l for l in self.stderr.splitlines() if l.strip().startswith(f"round {k}:"))
            self.assertIn(f", {played_moves} played points moved" if played_moves else "score", line)
            if not played_moves:
                self.assertNotIn("moved", line)

    def test_rounds_sized_to_free_slots_play_the_same_games_and_keep_the_constraints(self) -> None:
        classic = self.tune("--constraint", "beaconRound <= foldRound")
        rounds = self.tune("--constraint", "beaconRound <= foldRound", free=100)
        self.assertEqual((classic[0], rounds[0]), (0, 0))
        self.assertEqual(len(rounds[1]), len(classic[1]))  # 12 iterations of 4 perturbations on 3 maps: 288 games
        self.assertEqual([v for v in rounds[1] if v["foldRound"] < v["beaconRound"]], [])
        log = json.loads((rounds[2] / "tune.json").read_text())
        self.assertEqual(([len(r["scores"]) for r in log["rounds"]], log["games"]), ([12] * 4, 288))
        # each perturbation's nudges, maps and map scores are logged for loop/tunesplit.py; its scores are their mean,
        # and its nudges are the seed's draws in order, which tunesplit replays for tunes logged before "trials"
        import tunesplit
        scores = [s for r in log["rounds"] for s in r["scores"]]
        self.assertEqual([len(t["maps"]) for t in log["trials"]], [3] * 48)
        for trial, score in zip(log["trials"], scores, strict=True):
            self.assertAlmostEqual(sum(trial["pairs"]) / 3, score, places=3)
        self.assertEqual([t["delta"] for t in log["trials"]], tunesplit.replayed_nudges(3, log["params"], 48))
        self.assertEqual(log["trials"][0]["maps"], ["m0.map", "m1.map", "m2.map"])
        final = tune.read_params((rounds[2] / "bot/params.hpp").read_text())
        self.assertGreaterEqual(final["foldRound"][1], final["beaconRound"][1])
        self.assertTrue(all(v["foldRound"] >= 405 / 4 and v["mergeRound"] <= 450 * 4 for v in rounds[1]))

    def test_an_impossible_constraint_fails_before_any_game(self) -> None:
        # kingReportAge tuned from 10 reaches at most 40, never the fixed beacon's 405
        code, played, _ = self.tune("--constraint", "kingReportAge >= beaconRound")
        self.assertEqual((code, played), (2, []))
        code, played, _ = self.tune("--constraint", "kingHoldsSplits <= foldRound")
        self.assertEqual((code, played), (2, []))


if __name__ == "__main__":
    unittest.main()
