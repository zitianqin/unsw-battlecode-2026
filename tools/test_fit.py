"""Checks tools/fit.py and tools/value.py: the spec, the rows built from a real logging copy's replay, the fits on
known data, the held-out checks' splits, the changed-decision table and the params block; and the value model's win
rule, its antisymmetry and which saved games it reads.

The replay (testdata/fit-ram-0032-gen_eval_7310003-aA.replay, gzipped) is a logging copy of champion 8f57a4e against
swarm on a 33-round trap map, captured 2026-09-28 for the E0204 refit: its debug line carries `ro 1 rt rx rg rnf rne`
on the six ram-opportunity turns, two of them explored by the C++ draw fit.py's EXPLORE_CPP prints.

    .venv/bin/python -m unittest tools.test_fit
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1] / "loop"))  # testhome, without PYTHONPATH=loop
import testhome  # noqa: E402,F401  (before anything that reads live paths)

import fit  # noqa: E402
import value  # noqa: E402
import watch  # noqa: E402

REPLAY = Path(__file__).resolve().parent / "testdata" / "fit-ram-0032-gen_eval_7310003-aA.replay"
RAM = {"name": "ram", "when": "ro", "features": ["rg", "edge", "rnf", "rne"], "choice": "rt",
       "explore": {"field": "rx", "salt": 204, "percent": 30}, "outcome": {"kind": "ram_swing", "rounds": 0},
       "fixed": {"0": 0}, "params": {"prefix": "ramW", "names": {"bias": "Bias", "rg": "Gain"}}}
# A value model with one weight on the longest-dragon lead, so tests don't read the committed weights.
MODEL = {"kind": "logistic", "features": list(value.FEATURES), "weights": [0, 0, 5, 0, 0, 0, 0, 0]}


class SpecTest(unittest.TestCase):
    def test_defaults_to_winning_over_twenty_rounds(self) -> None:
        spec = fit.parse_spec('{"name": "x", "when": "ro", "features": ["rg"], "choice": "rt"}')
        self.assertEqual(spec.outcome, {"kind": "win", "rounds": 20})
        self.assertEqual((spec.model, spec.better), ("linear", "higher"))

    def test_reads_a_python_dict_and_a_file(self) -> None:
        self.assertEqual(fit.parse_spec("SPEC = " + repr(RAM)).fixed, {"0": 0.0})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ram.json"
            path.write_text(json.dumps(RAM))
            self.assertEqual(fit.parse_spec(str(path)).features, RAM["features"])

    def test_refuses_what_it_cannot_use(self) -> None:
        for bad, word in (({**RAM, "outcome": {"kind": "score"}}, "score"), ({**RAM, "feature": ["rg"]}, "unknown"),
                          ({k: v for k, v in RAM.items() if k != "choice"}, "choice"),
                          ({**RAM, "features": []}, "features"),
                          ({**RAM, "outcome": {"kind": "field", "rounds": 5}}, "field")):
            with self.subTest(word=word), self.assertRaisesRegex(ValueError, word):
                fit.parse_spec(json.dumps(bad))


# The rows fit.run writes for `spec` over `folders` (its report discarded).
def run_rows(spec: dict, folders: list[Path]) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rows.jsonl"
        with contextlib.redirect_stdout(io.StringIO()):
            fit.run(fit.parse_spec(json.dumps(spec)), folders, None, out)
        return [json.loads(line) for line in out.read_text().splitlines()]


class RowsTest(unittest.TestCase):
    def test_rows_of_a_real_logging_copy(self) -> None:
        rows, dropped = fit.game_rows(fit.parse_spec(json.dumps(RAM)), REPLAY, "swarm")
        self.assertEqual((len(rows), dropped), (6, 0))
        self.assertEqual([(r.round, r.dragon, r.choice, r.explored) for r in rows],
                         [(1, 0, "1", False), (2, 4, "1", False), (20, 8, "1", False), (28, 2, "0", True),
                          (32, 11, "1", True), (33, 10, "1", False)])
        self.assertEqual(rows[1].x, [-1.0, 1.0, 1.0, 0.0])
        # An explored turn's rule choice is the other one; the C++ draw picked exactly the flagged turns.
        self.assertEqual([r.rule for r in rows if r.explored], [None, None])
        fit.fill_rules(fit.parse_spec(json.dumps(RAM)), rows)
        self.assertEqual([r.rule for r in rows if r.explored], ["1", "0"])
        self.assertEqual([r.explored for r in rows], [bool(r.drawn) for r in rows])
        # A ram's outcome is its M51 swing (watch.rams); the declined one is the fixed 0.
        swings = {(ram.round, ram.rammer): ram.swing for ram in watch.rams(watch.watch(REPLAY), "A")}
        self.assertEqual([r.y for r in rows], [swings.get((r.round, r.dragon), 0.0) for r in rows])
        self.assertEqual((rows[0].map, rows[0].half, rows[0].opponent), ("gen-trap-7310003", 1, "swarm"))

    def test_win_outcome_is_the_change_in_our_chance(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "outcome": {"kind": "win", "rounds": 20}, "fixed": {}}))
        with patch.object(value, "_loaded", MODEL):
            rows, _ = fit.game_rows(spec, REPLAY, "swarm")
            game = watch.watch(REPLAY).game
            for r in rows:
                self.assertAlmostEqual(r.y, value.game_p(game, r.round + 20, "A") - value.game_p(game, r.round - 1, "A"))
        # We won at r33 (B eliminated): from r13 on, twenty rounds later is the result.
        self.assertEqual(value.game_p(game, 33, "A"), 1.0)
        self.assertTrue(all(-1 <= r.y <= 1 for r in rows))

    def test_length_and_material_read_the_frames(self) -> None:
        w = watch.watch(REPLAY)
        self.assertEqual(fit.length_at(w, 0, 1), 0)  # dragon 0 died ramming in round 1
        self.assertEqual((fit.length_at(w, 2, 4), fit.length_at(w, 2, 5)), (2, 3))  # it ate in round 5
        self.assertEqual(fit.material(w, 33, "A"), 10)  # B has nothing left; A's total is 10

    def test_map_size_round_and_interactions_are_inputs(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg", "tiles_k", "round"],
                                          "interactions": [["rg", "tiles_k"]]}))
        self.assertEqual(spec.inputs, ["rg", "tiles_k", "round", "rg*tiles_k"])
        rows, _ = fit.game_rows(spec, REPLAY, "swarm")
        tiles_k = rows[0].area / 1000
        self.assertEqual([r.x for r in rows[:2]], [[0.0, tiles_k, 1.0, 0.0], [-1.0, tiles_k, 2.0, -tiles_k]])
        with self.assertRaisesRegex(ValueError, "interactions"):
            fit.parse_spec(json.dumps({**RAM, "interactions": [["rg", "len"]]}))  # len isn't a feature

    # Regression (review of 7ee80b5): a game that logged only one choice lost its explored turns' rule choice, so they
    # dropped out of the gain measure; the choices are now every game's.
    def test_an_explored_turn_knows_the_rule_choice_from_other_games(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "swarm"
            folder.mkdir()
            shutil.copy(REPLAY, folder / "0001-gen_eval_7310003-aA.replay")
            text = gzip.decompress(REPLAY.read_bytes())
            self.assertEqual(text.count(b"rt 0"), 1)
            (folder / "0002-gen_eval_7310003-aA.replay").write_bytes(text.replace(b"rt 0", b"rt 1"))  # only 1s
            rows = run_rows({**RAM, "fixed": {}}, [folder])  # no fixed choice names the other
            explored = [r for r in rows if r["explored"]]
            self.assertEqual(len(explored), 4)
            self.assertEqual([(Path(r["replay"]).name[:4], r["choice"], r["rule"]) for r in explored],
                             [("0001", "0", "1"), ("0001", "1", "0"), ("0002", "1", "0"), ("0002", "1", "0")])

    # Regression (review of 7ee80b5, MISSES 55): a folder with the loop's index.json fed its kept losses in.
    def test_kept_losses_are_left_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "9000"
            folder.mkdir()
            for n in (11, 12, 13):
                shutil.copy(REPLAY, folder / f"{n}.replay")
            # 13: a dev loss left on disk after prune deleted its version's dev wins' files (prune.mark_dev_rows).
            (folder / "index.json").write_text(json.dumps({"games": [
                {"id": 11, "side": "A", "replay": "11.replay"},
                {"id": 12, "side": "A", "replay": "12.replay", "kept": "loss"},
                {"id": 13, "side": "A", "replay": "13.replay", "dev": True, "dev_wins_pruned": True}]}))
            rows = run_rows(RAM, [folder])
            self.assertEqual({Path(r["replay"]).name for r in rows}, {"11.replay"})
            self.assertEqual(len(rows), 6)

    def test_explore_draw_takes_its_share(self) -> None:
        draws = [fit.explore_draw(204, 32, 32, False, d, r, 30) for d in range(20) for r in range(500)]
        self.assertAlmostEqual(sum(draws) / len(draws), 0.30, delta=0.02)
        self.assertFalse(any(fit.explore_draw(204, 32, 32, True, 3, r, 0) for r in range(100)))


def row(x: float, choice: str, y: float | None, rule: str | None = None, map_name: str = "gen-normal-2",
        half: int = 0, explored: bool = False) -> fit.Row:
    return fit.Row(map_name, half, "swarm", 1024, "r", 1, 1, [x], choice, rule if rule is not None else choice,
                   explored, None, y)


class FitTest(unittest.TestCase):
    # REFLECT 2026-09-28 14:51 F23: "slightly negative" on new maps was one map, Slithery Fight, within noise.
    def test_a_held_out_group_on_fewer_than_three_maps_is_too_few_to_read(self) -> None:
        one = [row(1, "1", 0.1, map_name="Slithery Fight"), row(1, "1", 0.2, map_name="Slithery Fight ~2")]
        self.assertEqual(fit.group_size(one), "2 decisions on 1 map, too few to read (under 3 maps)")
        three = one + [row(1, "1", 0.1, map_name=m) for m in ("Portals", "Trauma")]
        self.assertEqual(fit.group_size(three), "4 decisions on 3 maps")

    def test_linear_recovers_known_weights(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg"], "fixed": {"0": 0}}))
        rows = [row(x / 4, "1", 1 + 2 * x / 4) for x in range(-40, 40)]
        f = fit.fit(spec, rows)
        self.assertEqual(sorted(f.weights), ["1"])
        for got, want in zip(f.weights["1"], (1.0, 2.0)):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual((f.pick([0.0]), f.pick([-1.0])), ("1", "0"))  # 1 + 2x against the fixed 0

    def test_logistic_recovers_known_weights(self) -> None:
        xs = [[1.0, x / 10] for x in range(-50, 50)]
        ys = [value.sigmoid(0.5 + 1.5 * x[1]) for x in xs]
        for got, want in zip(value.logistic(xs, ys), (0.5, 1.5)):
            self.assertAlmostEqual(got, want, places=4)

    def test_a_choice_with_too_few_rows_gets_no_model(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg"], "fixed": {}}))
        f = fit.fit(spec, [row(1, "1", 1)] * (fit.MIN_ROWS - 1))
        self.assertEqual((f.weights, f.counts), ({}, {"1": fit.MIN_ROWS - 1}))

    def test_changed_decisions_compare_the_two_choices_played(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg"]}))
        f = fit.Fit(spec, {"1": [0.0, 1.0]}, {"1": 0.0}, {"1": 99})  # take when x > 0
        rows = [row(1, "0", 0, rule="0"), row(1, "0", 0, rule="0"),  # the rule declines, the model would take
                row(1, "1", 3, rule="0", explored=True),  # ... and explored, taking paid 3
                row(-1, "1", -2, rule="1"),  # the rule takes, the model declines (fixed 0)
                row(2, "1", 5, rule="1")]  # both take
        result = fit.score(f, rows)
        self.assertEqual((result["judged"], result["agree"]), (5, 1))
        take, decline = result["changed"]["0", "1"], result["changed"]["1", "0"]
        self.assertEqual((take.rows, take.rule_played, take.model_played), (3, [0, 0], [3]))
        self.assertEqual((decline.rows, decline.rule_played, decline.model_played), (1, [-2], [0.0]))
        g, _, unmeasured = fit.gain(result, "higher")
        self.assertEqual(unmeasured, 0)
        self.assertAlmostEqual(g, 3 / 5 * 3 + 1 / 5 * 2)

    def test_params_block_is_the_advantage_over_the_other_choice(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg", "edge"], "fixed": {}}))
        f = fit.Fit(spec, {"0": [0.5, 0.0, 0.25], "1": [1.5, 2.0, 0.0]}, {}, {})
        block = "\n".join(fit.params_block(spec, f, 100))
        self.assertIn("double ramWBias = 1;", block)
        self.assertIn("double ramWGain = 2;", block)
        self.assertIn("double ramWEdge = -0.25;", block)


NEW = ("portals", "slithery fight")


class ChecksTest(unittest.TestCase):
    def where(self, item: tuple[str, int, str | None]) -> value.Where:
        return value.Where(*item)

    def test_odd_generated_games_are_held_out_and_each_official_map_left_out(self) -> None:
        items = [("gen-normal-2", 0, None), ("gen-normal-3", 1, None), ("near_trauma_5", 1, None),
                 ("Default", 0, "2026-09-24"), ("Default ~1", 1, None), ("Trauma", 1, "2026-09-25"),
                 ("Portal Labyrinth", 0, None)]
        found = {name: (train, test) for name, train, test in value.checks(items, self.where, NEW)}
        train, test = found["generated, odd"]
        self.assertEqual(test, [items[1]])
        self.assertEqual(train, [items[0], *items[2:]])  # a near copy trains beside its map
        train, test = found["map default"]
        self.assertEqual(test, [items[3], items[4]])  # a renamed copy is its map
        self.assertNotIn(items[3], train)
        self.assertNotIn(items[1], train)  # odd generated games never train
        self.assertEqual(sorted(found), ["generated, odd", "map default", "map trauma"])

    # Regression (review of 7ee80b5): near copies of an official map trained where that map was the test set, and
    # odd ones were tested in "generated, odd" while their map trained.
    def test_a_maps_near_copies_leave_with_it(self) -> None:
        items = [("Trauma", 0, None), ("near_trauma_6", 0, None), ("Portals ~1", 1, None),
                 ("near_portals_repearl_12", 0, None), ("Prisoners Dilemma ~1", 0, None), ("near_dilemma_9", 1, None),
                 ("near_queen_of_spades_4", 0, None), ("gen-macro-7", 1, None), ("gen-macro-8", 0, None)]
        found = {name: (train, test) for name, train, test in value.checks(items, self.where)}
        self.assertEqual(found["map trauma"], ([i for i in items if "trauma" not in i[0].lower() and i[1] == 0 or
                                                i[0] in ("Portals ~1", "near_dilemma_9")], [items[0]]))
        self.assertNotIn(items[3], found["map portals"][0])
        self.assertNotIn(items[5], found["map prisoners dilemma"][0])
        self.assertIn(items[6], found["map portals"][0])  # another map's copy trains
        new = next(v for k, v in found.items() if k.startswith("new maps"))
        self.assertNotIn(items[3], new[0])
        self.assertEqual(found["generated, odd"][1], [items[7]])  # near copies are never tested there

    def test_near_copies_name_their_map_by_file_stem(self) -> None:
        self.assertEqual([value.near_source(n) for n in ("near_dilemma_9", "near_queen_of_spades_7310019",
                                                         "near_default_small_repearl_7310010", "near_nowhere_3",
                                                         "gen-near-5", "Default")],
                         ["prisoners dilemma", "queen of spades", "default small", None, None, None])
        self.assertEqual(value.family("Default ~2"), "default")
        self.assertEqual(value.map_group("gen-worldgen-7-cave-none"), "worldgen")

    def test_new_maps_are_the_ones_added_last(self) -> None:
        names = {"default": "default", "portals": "portals", "slithery_fight": "slithery fight"}
        added = {"default": "2026-09-23", "portals": "2026-09-27", "slithery_fight": "2026-09-27"}
        self.assertEqual(value.latest_added(added, names), ("portals", "slithery fight"))
        self.assertEqual(value.latest_added({}, names), ())
        # The repo's own history: the newest maps/ladder files are official maps.
        self.assertTrue(value.new_maps())
        self.assertTrue(set(value.new_maps()) <= set(value.map_names(value.MAPS / "ladder").values()))

    def test_worldgen_maps_are_held_out_together_and_their_even_games_train_elsewhere(self) -> None:
        items = [("gen-worldgen-5-cave-none", 1, None), ("gen-worldgen-6-isle-tower", 0, None),
                 ("gen-normal-2", 0, None), ("gen-normal-3", 1, None), ("Default", 0, None), ("Portals", 0, None)]
        found = {name: (train, test) for name, train, test in value.checks(items, self.where, NEW)}
        self.assertEqual(found["worldgen"], ([items[2], items[4], items[5]], items[:2]))
        self.assertEqual(found["generated, odd"], ([items[1], items[2], items[4], items[5]], [items[3]]))
        for name in ("map default", "map portals", "new maps (left out together, no dates)"):
            self.assertIn(items[1], found[name][0])  # an even worldgen game trains
            self.assertNotIn(items[0], found[name][0])  # an odd one never does
        self.assertEqual(fit.style_of("gen-worldgen-5-cave-none"), "worldgen")
        self.assertEqual(fit.style_of("gen-sealedstart-5-maze"), "sealedstart")
        self.assertEqual(fit.style_of("gen-curves-2-eyes-taichi"), "curves")
        self.assertEqual(value.map_group("gen-normal-5"), "generated")

    def test_new_maps_fit_on_games_before_they_joined(self) -> None:
        items = [("Default", 0, "2026-09-25T10:00"), ("Default", 0, "2026-09-27T10:00"),
                 ("Portals", 0, "2026-09-26T14:00"), ("Slithery Fight", 1, "2026-09-27T01:00"),
                 ("gen-normal-2", 0, None)]
        name, train, test = value.checks(items, self.where, NEW)[-1]
        self.assertEqual(name, "new maps (games before 2026-09-26T14:00)")
        self.assertEqual((train, test), ([items[0], items[4]], [items[2], items[3]]))
        # An undated local copy of a new map is tested too; an undated game elsewhere trains.
        items += [("Slithery Fight ~1", 0, None), ("Default ~1", 0, None)]
        name, train, test = value.checks(items, self.where, NEW)[-1]
        self.assertEqual((train, test), ([items[0], items[4], items[6]], [items[2], items[3], items[5]]))
        local = [("Portals ~1", 1, None), ("Default ~2", 0, None)]
        name, train, test = value.checks(local, self.where, NEW)[-1]
        self.assertEqual((name, train, test), ("new maps (left out together, no dates)", [local[1]], [local[0]]))


class NetTest(unittest.TestCase):
    # fit.py's net scales inputs and outcome for training and folds that back: on raw inputs far from 0 it fits a
    # curve a line can't.
    def test_a_net_fits_a_curve_on_unscaled_inputs(self) -> None:
        xs = [[1.0, 1000 + n / 2] for n in range(4000)]  # enough rows for the net's minibatch steps
        ys = [((x[1] - 2000) / 1000) ** 2 * 50 + 7 for x in xs]
        model = fit.fit_net(xs, ys)
        line = value.least_squares(xs, ys)
        err = lambda f: math.sqrt(sum((f(x) - y) ** 2 for x, y in zip(xs, ys)) / len(ys))
        self.assertLess(err(lambda x: value.net_z(model, x)), 0.25 * err(lambda x: line[0] + line[1] * x[1]))

    def test_a_nets_params_block_has_each_choices_weights(self) -> None:
        spec = fit.parse_spec(json.dumps({**RAM, "features": ["rg"], "model": "net"}))
        f = fit.Fit(spec, {"1": {"w1": [[0.5, 1.0], [0.0, -2.0]], "w2": [1.0, 0.25], "b": 0.1}}, {}, {})
        block = "\n".join(fit.params_block(spec, f, 10))
        self.assertIn("double ramWNetW1_1[2][2] = {{0.5, 1}, {0, -2}};", block)
        self.assertIn("double ramWNetB_1 = 0.1;", block)
        self.assertEqual(f.pick([1.0]), "1")  # net(1) = 0.1 + tanh(1.5) - 0.25 tanh(2) > the fixed 0


class ValueTest(unittest.TestCase):
    def test_the_rule_decides_eliminations_and_round_499(self) -> None:
        T = value.Team
        self.assertEqual(value.terminal(40, T(3, 9, 0), T(2, 2, 1)), 0.0)
        self.assertEqual(value.terminal(40, T(0, 0, 0), T(0, 0, 0)), 0.5)
        self.assertEqual(value.terminal(499, T(9, 10, 2), T(8, 30, 5)), 1.0)  # longest first
        self.assertEqual(value.terminal(499, T(9, 10, 2), T(9, 30, 5)), 0.0)  # then total
        self.assertIsNone(value.terminal(498, T(9, 10, 2), T(8, 30, 5)))

    def test_an_even_snapshot_is_the_intercept_and_sides_mirror(self) -> None:
        a, b = value.Team(7, 20, 4), value.Team(5, 26, 7)
        net = {"kind": "net", "features": list(value.FEATURES), **value.net([[0.1] * 8, [-0.2] * 8], [1, 0], epochs=2)}
        for model in (MODEL, {**MODEL, "intercept": 0.8}, net):
            b0 = model.get("intercept", model.get("b", 0.0))
            self.assertAlmostEqual(value.p_win(300, a, a, 1024, model), value.sigmoid(b0))
            # Swapping sides flips every feature: about the intercept, the two chances mirror.
            z = math.log(value.p_win(300, a, b, 1024, model) / (1 - value.p_win(300, a, b, 1024, model)))
            z_swapped = math.log(value.p_win(300, b, a, 1024, model) / (1 - value.p_win(300, b, a, 1024, model)))
            self.assertAlmostEqual(z - b0, b0 - z_swapped)

    def test_reads_online_games_chosen_blind_to_their_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            folder = home / "online-replays" / "9000"
            folder.mkdir(parents=True)
            for n in (11, 12, 13):
                shutil.copy(REPLAY, folder / f"{n}.replay")
            # 13: a dev loss left on disk after prune deleted its version's dev wins' files (prune.mark_dev_rows).
            games = [{"id": 11, "side": "A", "opponent": 5, "replay": str(folder / "11.replay"),
                      "requested_at": "2026-09-27T00:00:00Z"},
                     {"id": 12, "side": "A", "opponent": 5, "replay": str(folder / "12.replay"), "kept": "loss"},
                     {"id": 13, "side": "A", "opponent": 545, "replay": str(folder / "13.replay"), "dev": True,
                      "dev_wins_pruned": True}]
            (folder / "index.json").write_text(json.dumps({"games": games}))
            found = value.sources(home, [])
            self.assertEqual([(Path(s.path).name, s.half, s.date) for s in found],
                             [("11.replay", 1, "2026-09-27T00:00:00Z")])
            cache = home / "cache.jsonl"
            # --jobs 4 takes only what the load share leaves (home.pool_size; 29 Sep 13:01, load 51 on 10 cores): one
            # left reads here, in this process.
            with patch.object(value.home, "pool_size", return_value=1) as size, \
                    patch("multiprocessing.get_context", side_effect=AssertionError("a pool")):
                first = value.read_games(found, cache, jobs=4)
            size.assert_called_once_with(4)
            cache.unlink()
            first = value.read_games(found, cache)
            again = value.read_games(found, cache)
            self.assertEqual(len(cache.read_text().splitlines()), 1)
            self.assertEqual([(g.map, g.label, g.date) for g in again], [(g.map, g.label, g.date) for g in first])
            self.assertEqual((first[0].label, first[0].rows[-1][0]), (1.0, 33))


if __name__ == "__main__":
    unittest.main()
