"""The tune generalisation check (loop/tunesplit.py) on simulated tunes played like tune.py's, with win/draw/loss games:
a value whose best setting differs by map area is flagged and gets a formula idea, one whose best is the same on every
map isn't, even when some maps react to it far more (unequal curvature), and luck alone rarely flags anything."""

import testhome  # before anything that reads live paths
import json
import math
import random
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import chance
import driver
import meta
import tune
import tunesplit

SIDES = (10, 20, 32, 45, 64)  # square maps of 100-4096 tiles, the range the recorded tunes' maps span


# `count` maps in play order, each a random size in SIDES and alternately eval (gen-normal) and worldgen by name: one
# file per size and style in `folder`, repeated (the check reads only each map's area and style).
def make_maps(folder: Path, count: int, rng: random.Random) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for side in SIDES:
        for style in ("normal", "worldgen"):
            files[side, style] = path = folder / f"{style}-{side}.map"
            path.write_text(f"MAP {side} {side}\nSYMMETRY x\nMAP_NAME gen-{style}-{side}\nTILE 0 0 .\n")
    return [str(files[rng.choice(SIDES), "worldgen" if i % 2 else "normal"]) for i in range(count)]


# A tune of `params` played as tune.py plays one (nudges from random.Random(seed), `pairs` maps a perturbation,
# rounds of 4 moved by tune.spsa_step), where each game of plus against minus is a win, draw or loss: plus wins with
# chance (1 - DRAW) x logistic(its strength minus minus's), a strength being -sum over params of curvature x (value -
# best)^2 / 2 in nudges. `best(p, area, style)` and `curvature(p, area, style)` give a map's best value (nudges from the
# start) and curvature. Returns the tune.json log (with "trials") and the maps in play order.
DRAW = 0.2


def simulated(folder: Path, best, curvature, perturbations: int = 160, pairs: int = 3, seed: int = 7,
              params: tuple[str, ...] = ("a", "b")) -> tuple[dict, list[str]]:
    rng = random.Random(seed + 1)
    maps = make_maps(folder, perturbations * pairs, rng)
    features = [(driver.map_area(Path(m)), tunesplit.style_group(Path(m))) for m in maps]
    params, starts = list(params), dict.fromkeys(params, 10.0)
    kinds = dict.fromkeys(params, "double")
    nudges = {p: tune.nudge("double", starts[p]) for p in params}
    deltas = tunesplit.replayed_nudges(seed, params, perturbations)

    def strength(values: dict, area: int, style: str) -> float:
        return -sum(curvature(p, area, style) * ((values[p] - starts[p]) / nudges[p] - best(p, area, style)) ** 2 / 2
                    for p in params)

    theta, rounds, trials = dict(starts), [], []
    for k in range(0, perturbations, 4):
        batch = []
        for i in range(k, k + 4):
            plus = {p: tune.clamp(theta[p] + deltas[i][p] * nudges[p], starts[p], "double") for p in params}
            minus = {p: tune.clamp(theta[p] - deltas[i][p] * nudges[p], starts[p], "double") for p in params}
            scores = []
            for area, style in features[i * pairs:(i + 1) * pairs]:
                win = (1 - DRAW) / (1 + math.exp(strength(minus, area, style) - strength(plus, area, style)))
                points = [1.0 if (r := rng.random()) < win else 0.5 if r < win + DRAW else 0.0 for _ in range(2)]
                scores.append(sum(2 * s - 1 for s in points) / 2)
            trials.append({"delta": deltas[i], "maps": maps[i * pairs:(i + 1) * pairs], "pairs": scores})
            batch.append((deltas[i], sum(scores) / pairs, tune.gain_at(k // 4, perturbations // 4)))
        theta = tune.spsa_step(theta, batch, 4, nudges, starts, kinds)
        rounds.append({"theta": dict(theta), "scores": [s for _, s, _ in batch]})
    return {"params": params, "start": starts, "nudges": nudges, "projections": [], "rounds": rounds,
            "trials": trials, "final": dict(theta)}, maps


# Nulls: the same best everywhere (2 nudges above the start) with unequal curvature. The reviewer's (2026-09-28):
# curvature 1:4:12 over the three old size bands (meta.AREA_BANDS), which 521eef4's equal-slope test flagged in 22.7% of
# its null tunes and in 26.8% of 400 of these (0.10 is the bar); and a smooth one, 1:12 from the smallest maps to the
# largest and 4x on worldgen.
def same_best(p: str, area: int, style: str) -> float:
    return 2.0


def band_curvature(p: str, area: int, style: str) -> float:
    return 0.1 * (1 if area <= 1024 else 4 if area < 2400 else 12)


def unequal_curvature(p: str, area: int, style: str) -> float:
    return 0.08 * (area / 100) ** 0.67 * (4 if style == "worldgen" else 1)


# a's best falls from +3 nudges on the smallest maps to -3 on the largest; every other param's is the same everywhere.
def best_by_area(p: str, area: int, style: str) -> float:
    return 3 - 6 * math.log(area / 100) / math.log(4096 / 100) if p == "a" else 2.0


def flat_curvature(p: str, area: int, style: str) -> float:
    return 0.3


# The old-record form of a simulated log: no "trials", the maps and nudges passed apart (analyse's path for tunes
# logged before 2026-09-28).
def old_check(log: dict, maps: list[str], seed: int = 7) -> dict:
    old = {k: v for k, v in log.items() if k != "trials"}
    count = sum(len(r["scores"]) for r in log["rounds"])
    return tunesplit.check(old, maps, tunesplit.replayed_nudges(seed, log["params"], count), 3)


class TestTuneSplit(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())

    def test_a_value_whose_best_differs_by_area_is_flagged_and_gets_a_valid_formula_idea(self) -> None:
        log, _ = simulated(self.folder, best_by_area, flat_curvature, perturbations=240)
        report = tunesplit.check(log)
        self.assertIn(["a", "area"], report["flagged"])
        self.assertNotIn("b", [p for p, _ in report["flagged"]])  # its best is the same everywhere
        small, large = report["params"]["a"]["area"]["tested"]  # the maps up to the median area, and the rest
        self.assertTrue(small.startswith("up to") and large.startswith("over"))
        bands = report["params"]["a"]["area"]["bands"]
        self.assertGreater(bands[small]["gradient"], 0)  # small maps want it higher, big maps lower
        self.assertLess(bands[large]["gradient"], 0)
        self.assertIn("FLAGGED a by map area", tunesplit.text(report))
        ideas = tunesplit.proposals(report, "E9001", {"id": "I9", "title": "Tune a and b"})
        self.assertEqual(len(ideas), 1)
        valid = meta.valid_proposals([json.dumps(i) for i in ideas], 5)
        self.assertEqual(len(valid), 1)
        idea = valid[0]
        self.assertEqual((idea["kind"], idea["priority"], idea["after"]), ("formula", 1, ["I9"]))
        self.assertIn("the gradient differs by map area", idea["title"])
        text = idea["hypothesis"]
        self.assertIn("map's area", text)
        # like for like around the champion's value when claimed, whether or not the tune was kept
        self.assertIn(f"{log['final']['a']:g} if E9001 (idea I9) was kept, else still 10", text)
        # the coder builds it from the fitted numbers in the text: its sandbox can't read the loop home's runs/
        self.assertNotIn("runs/", text)
        self.assertIn(f"by map area: {small} ({bands[small]['pairs']:g} map pairs): gradient "
                      f"{bands[small]['gradient']:+.3f}±{bands[small]['se']:.3f}, curvature", text)
        self.assertIn("by style: eval (", text)
        self.assertIn("worldgen line", text)
        self.assertIn(f"within one nudge ({log['nudges']['a']:g}", text)
        self.assertGreaterEqual(len(set(re.findall(r"\((\d)\)", text.partition("Options:")[2]))), 3)
        # a second tune flagging a value a waiting tunesplit idea already names adds none
        waiting = [{**idea, "id": "I10", "status": "todo", "source": "tunesplit:E9001"}]
        self.assertEqual(tunesplit.queued_params(waiting), {"a"})
        self.assertEqual(tunesplit.proposals(report, "E9006", {"id": "I11"}, tunesplit.queued_params(waiting)), [])
        self.assertEqual(tunesplit.queued_params([{**waiting[0], "status": "tried"}]), set())

    def test_the_same_best_with_unequal_curvature_is_not_flagged(self) -> None:
        # bigger and worldgen maps react to the value up to 48x more: a slope test would read that as a different
        # best whenever the tune ends off the best
        log, maps = simulated(self.folder, same_best, unequal_curvature, perturbations=240)
        report = tunesplit.check(log)
        self.assertEqual(report["flagged"], [])
        self.assertIn("no value's groups clearly want it moved opposite ways", tunesplit.text(report))
        self.assertEqual(report["tests"], 4)  # a and b, by area and by style (eval and worldgen)
        self.assertAlmostEqual(report["bar"], chance.TRIGGER_P / 4)
        self.assertEqual(old_check(log, maps)["flagged"], [])

    def test_luck_alone_flags_at_most_the_nominal_rate_at_the_bar_on_both_record_paths(self) -> None:
        # Two-value tunes under the reviewer's null (band_curvature, one best everywhere), at the Bonferroni bar the
        # loop uses: tunes flagged (TRIGGER_P nominal) and the area test alone at its bar (a quarter of it). Over 500
        # simulated tunes these flagged 1.0% (3.4% on old records; module docstring); 5eb6f65's shared-best test
        # flagged 11.7% (its area test rejected 4.8% at the bar, review of 2026-09-28), the equal-slope test 27%.
        for path in ("new", "old"):
            flagged = area_hits = area_tests = 0
            for s in range(25):
                log, maps = simulated(self.folder / path / str(s), same_best, band_curvature, perturbations=200,
                                      seed=100 + s)
                report = tunesplit.check(log) if path == "new" else old_check(log, maps, 100 + s)
                flagged += bool(report["flagged"])
                area = [t["p"] for t in (splits["area"] for splits in report["params"].values()) if t["p"] is not None]
                area_hits += sum(p <= report["bar"] for p in area)
                area_tests += len(area)
            self.assertLessEqual(flagged / 25, chance.TRIGGER_P, (path, flagged))
            self.assertEqual(area_tests, 50, path)
            self.assertLessEqual(area_hits / area_tests, chance.TRIGGER_P / 4, (path, area_hits))

    def test_a_tune_that_travelled_far_to_a_shared_best_is_not_flagged_by_its_path(self) -> None:
        # The best 10 nudges above the start: the tune's path toward it lies on one side, where the large maps' gradient
        # levels off, so a line's crossing through those samples differed by group, and 5eb6f65's shared-best test
        # flagged 48% of such tunes. The groups' gradients at the tuned value still point the same way.
        flagged = 0
        for s in range(10):
            log, _ = simulated(self.folder / str(s), lambda p, area, style: 10.0, band_curvature, perturbations=200,
                               seed=200 + s)
            flagged += bool(tunesplit.check(log)["flagged"])
        self.assertEqual(flagged, 0)

    def test_a_best_past_the_clamp_flags_nothing_and_is_never_cited(self) -> None:
        # One best everywhere 8 nudges below the start, past the clamp's floor (a quarter of the start, 5 nudges down):
        # a shared best searched only inside the clamp flagged 84% of these tunes (5eb6f65). At the floor every group's
        # gradient still points down, clamped nudges and all, and no text gives the floor as a group's best.
        for s in range(3):
            log, _ = simulated(self.folder / str(s), lambda p, area, style: -8.0, band_curvature, perturbations=200,
                               seed=300 + s)
            report = tunesplit.check(log)
            self.assertEqual(report["flagged"], [])
            self.assertEqual(report["tests"], 4)
            floor = tune.clamp_range("double", 10.0)[0]
            for splits in report["params"].values():
                for t in splits.values():
                    self.assertTrue(all(r.get("best") != floor for r in t["bands"].values()))
                    self.assertTrue(all(t["bands"][g]["gradient"] < 0 for g in t["tested"]))

    def test_old_records_replay_their_nudges_and_split_by_each_perturbations_maps(self) -> None:
        log, maps = simulated(self.folder, best_by_area, flat_curvature, perturbations=320)
        old = {k: v for k, v in log.items() if k != "trials"}
        right = tunesplit.replayed_nudges(7, log["params"], 320)
        self.assertEqual(tunesplit.nudge_check(old, right), 1.0)
        self.assertLess(tunesplit.nudge_check(old, tunesplit.replayed_nudges(8, log["params"], 320)), 0.75)
        report = tunesplit.check(old, maps, right, 3)
        self.assertIn(["a", "area"], report["flagged"])
        self.assertEqual(report["nudge_check"], 1.0)

    def test_an_old_mix_records_rebuilt_order_is_marked_unverified(self) -> None:
        run = self.folder / "E9007"
        log, played = simulated(self.folder / "pool", same_best, flat_curvature, perturbations=40)
        (run / "tune-maps").mkdir(parents=True)
        for i, path in enumerate(played):  # one file per map played, as a real tune's folder holds
            (run / "tune-maps" / f"gen_eval_{i:04d}.map").write_text(Path(path).read_text())
        (run / "tune.json").write_text(json.dumps({k: v for k, v in log.items() if k != "trials"}))
        exp = {"session": {"tune": {"iterations": 10, "perturbations": 4, "pairs": 3}}, "evaluation": {"tune_seed": 7}}
        (run / "experiment.json").write_text(json.dumps(exp))
        report = tunesplit.analyse(run)
        self.assertEqual(report["map_order"], "sorted")  # run_tuner's order without a mix since tunes began
        self.assertNotIn("unverified", tunesplit.text(report))
        exp["evaluation"]["maps"] = {"big": 0.5}
        (run / "experiment.json").write_text(json.dumps(exp))
        sorted_maps = sorted(str(m) for m in (run / "tune-maps").glob("*.map"))
        with mock.patch.object(driver, "mixed_order", return_value=sorted_maps):  # today's order code, whatever it is
            report = tunesplit.analyse(run)
        self.assertEqual(report["map_order"], "rebuilt")
        self.assertIn("map order rebuilt with today's driver.mixed_order, unverified", tunesplit.text(report))

    def test_a_split_the_maps_cannot_separate_is_left_untested(self) -> None:
        # E0076: every perturbation played 2 eval maps and 1 near map, so its style split has nothing to compare
        rows = [(i, {"eval": 2 / 3, "near": 1 / 3}, float(i % 3), 0.1) for i in range(60)]
        result = tunesplit.split_test(rows, {"eval": 120, "near": 60}, "style", 10, 1, "int", 10)
        self.assertIsNone(result["p"])
        self.assertIn("can't tell the groups apart", result["why"])

    def test_the_driver_records_the_check_and_queues_one_formula_idea_per_flagged_value(self) -> None:
        exp = driver.Experiment(id="E9002", idea={"id": "I9", "title": "Tune a and b", "kind": "tune"}, lane=1)
        exp.run_dir.mkdir(parents=True)
        log, _ = simulated(exp.run_dir / "tune-maps", best_by_area, flat_curvature, perturbations=240)
        (exp.run_dir / "tune.json").write_text(json.dumps(log))
        (exp.run_dir / "experiment.json").write_text(json.dumps({"session": {}, "evaluation": {}}))
        (driver.STATE / "ideas.jsonl").write_text("")
        driver.check_tune_split(exp)
        self.assertIn(["a", "area"], exp.session["tune_split"]["flagged"])
        ideas = [json.loads(line) for line in (driver.STATE / "ideas.jsonl").read_text().splitlines()]
        self.assertEqual([(i["kind"], i["source"], i["after"]) for i in ideas], [("formula", "tunesplit:E9002", ["I9"])])
        self.assertEqual(exp.session["tune_split_ideas"], [ideas[0]["id"]])
        self.assertIn("FLAGGED", meta.split_text({"session": exp.session}))
        # the same tune checked again (or another flagging value a) queues nothing while that idea waits
        driver.check_tune_split(exp)
        self.assertEqual(len((driver.STATE / "ideas.jsonl").read_text().splitlines()), 1)
        exp.verdict = "kept"
        self.assertNotIn("tune generalisation check (check_tune_split)", driver.missing_checks(exp))
        del exp.session["tune_split"]
        self.assertIn("tune generalisation check (check_tune_split)", driver.missing_checks(exp))

    def test_a_reused_tune_reports_the_original_check_without_queueing_again(self) -> None:
        origin = driver.STATE / "runs" / "E9003"
        origin.mkdir(parents=True)
        log, _ = simulated(origin / "tune-maps", best_by_area, flat_curvature, perturbations=240)
        (origin / "tune.json").write_text(json.dumps(log))
        (origin / "experiment.json").write_text(json.dumps({"session": {}, "evaluation": {}}))
        exp = driver.Experiment(id="E9004", idea={"id": "I9", "kind": "tune"}, lane=1)
        exp.run_dir.mkdir(parents=True)
        (exp.run_dir / "tune.json").write_text(json.dumps({**log, "resumed_from": "E9003"}))
        (exp.run_dir / "experiment.json").write_text(json.dumps({"session": {}, "evaluation": {}}))
        (driver.STATE / "ideas.jsonl").write_text("")
        driver.check_tune_split(exp)
        self.assertEqual(exp.session["tune_split"]["from"], "E9003")
        self.assertIn(["a", "area"], exp.session["tune_split"]["flagged"])
        self.assertEqual((driver.STATE / "ideas.jsonl").read_text(), "")

    def test_the_official_line_gives_each_map_and_says_near_copies_were_in_the_tune(self) -> None:
        maps, tuned = self.folder / "official-maps", self.folder / "tune-maps"
        maps.mkdir()
        tuned.mkdir()
        (maps / "devil~0.map").write_text("MAP 20 20\nMAP_NAME Devil\n")
        (maps / "schooltime~0.map").write_text("MAP 60 60\nMAP_NAME Schooltime\n")
        for i, name in enumerate(["near_devil_1", "near_devil_2", "gen-normal-3", "gen-trap-4"]):
            (tuned / f"m{i}.map").write_text(f"MAP 20 20\nMAP_NAME {name}\n")
        official = {"score": 0.45, "games": 20, "per_map": {"devil": 0.6, "schooltime": 0.3}}
        line = tunesplit.official_text("E9005", "I9", official, maps, tuned)
        self.assertIn("9/20", line)
        self.assertIn("never a gate", line)
        self.assertNotIn("HELD-OUT", line)
        self.assertIn("not held out: near copies of the official and community maps were 2/4", line)
        self.assertIn("devil 0.60 (400 tiles, 2 near copies in the tune), schooltime 0.30 (3600 tiles, 0 near", line)


if __name__ == "__main__":
    unittest.main()
