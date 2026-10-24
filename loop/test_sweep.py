"""The variant sweep: its variants from champion v0024's real params.hpp (testdata/params-v0024.hpp, 4e3da73, the
breeder's base), the pass thresholds and idea queueing on fake game rows, the games' attribution to variants (with
results built from a real referee row, testdata/sweep-game.jsonl, E0089's first swarm screen game), the background
run's pause, finish and failure paths, the filler stopping when an evaluation starts, and its health check."""

import testhome  # before anything that reads live paths
import json
import math
import os
import shutil
import subprocess
import threading
import sys
import tempfile
import time
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import driver
import health
import meta
import sweep

sys.path.insert(0, str(driver.REPO / "referee"))
import run as referee  # noqa: E402  the referee's GameResult and to_json, as play() uses them
import unswbc.run  # noqa: E402

DATA = Path(__file__).resolve().parent / "testdata"
SAMPLE = (DATA / "params-v0024.hpp").read_text()
GAME = json.loads((DATA / "sweep-game.jsonl").read_text())  # one line of a real run.to_json output
CHAMPION = "4e3da734e842fa0dbedc9323b2e46ba8bd2ae63e"


# The builds' pool size measures this Mac for 2 s (home.pool_size): every test here builds with the size asked.
def setUpModule() -> None:
    stub = patch.object(sweep.home, "pool_size", lambda want, wait=True: want)
    stub.start()
    unittest.addModuleCleanup(stub.stop)


def variant(variant_id: str) -> dict:
    return next(v for v in sweep.variants(SAMPLE)["variants"] if v["id"] == variant_id)


# `wins` won games then losses, for one variant's pass, on maps named m<pass>-<i>, both sides; pass 3's against the
# rotation's seats in turn.
def rows(variant_id: str, number: int, wins: int) -> list[dict]:
    maps = {1: sweep.FIRST_MAPS, 2: sweep.SECOND_MAPS, 3: sweep.seat_maps()}[number]
    games = [{"variant": variant_id, "pass": number, "map": f"/x/m{number}-{i}.map", "a_side": side,
              **({"seat": driver.ROTATION[i % len(driver.ROTATION)]} if number == 3 else {})}
             for i in range(maps) for side in "AB"]
    return [dict(game, winner="a" if k < wins else "b") for k, game in enumerate(games)]


# A variant's pass 3 and the champion's own seat games (sweep.BASELINE, `base` wins) it pairs with: `wins` of the
# variant's 2 x seat_maps() against `base` of the champion's, the same games won first, so the paired score is
# 0.5 + (wins - base) / (4 x seat_maps()).
def seated(variant_id: str, wins: int, base: int = 10) -> list[dict]:
    return rows(sweep.BASELINE, 3, base) + rows(variant_id, 3, wins)


def clear_home() -> None:
    for path in driver.STATE.iterdir():
        shutil.rmtree(path) if path.is_dir() else path.unlink()


class Variants(unittest.TestCase):
    def test_the_breeders_changes_are_among_the_capped_variants(self) -> None:
        ids = {v["id"] for v in sweep.variants(SAMPLE)["variants"]}
        self.assertLessEqual({"splitParentTerritory=0", "splitParentSpace=0", "splitChildSpace=0", "mergeRound=499"},
                             ids)

    def test_every_switch_flips_and_every_number_goes_to_zero_and_four_times(self) -> None:
        ids = {v["id"] for v in sweep.variants(SAMPLE, cap=10**6)["variants"]}
        special = set(sweep.SKIP) | set(sweep.NO_ZERO) | set(sweep.NOT_4X) | set(sweep.AT_ZERO)
        for param in sweep.read_params(SAMPLE):
            name, value = param["name"], param["value"]
            with self.subTest(param=name):
                if name in special:
                    continue
                if param["kind"] == "bool":
                    self.assertIn(f"{name}={str(not value).lower()}", ids)
                else:
                    zero, four = (0, 4 * value) if param["kind"] == "int" else (0.0, 4.0 * value)
                    self.assertLessEqual({f"{name}={zero}", f"{name}={four}"}, ids)

    def test_illegal_or_duplicate_values_are_skipped_with_a_reason(self) -> None:
        found = sweep.variants(SAMPLE, cap=10**6)
        ids = {v["id"] for v in found["variants"]}
        for gone in ("exploreStride=0", "fastMinResets=0", "enemyHalfLife=0.0", "foldMinLength=0", "maxRamSteps=12",
                     "mergeRound=1800", "foldRound=1660", "debugIndicator=true", "sonarBuildKey=false",
                     "lethalScore=0.0"):
            self.assertNotIn(gone, ids)
        skipped = {s["param"] for s in found["skipped"]}
        self.assertLessEqual({"exploreStride", "fastMinResets", "maxRamSteps", "debugIndicator", "lethalScore"},
                             skipped)
        self.assertTrue(all(s["reason"] for s in found["skipped"]))
        # A param at 0 gets an always and a never instead of two copies of its value.
        self.assertLessEqual({"attackMinGain=-100", "attackMinGain=100", "evenTradeMinEdge=-100"}, ids)
        self.assertLessEqual({"foldRound=0", "mergeRound=0", "maxRamSteps=0", "exploreStride=16"}, ids)

    def test_the_cap_keeps_the_most_relevant(self) -> None:
        full = sweep.variants(SAMPLE, cap=10**6)["variants"]
        kept = sweep.variants(SAMPLE)["variants"]
        self.assertGreater(len(full), sweep.MAX_VARIANTS)
        self.assertEqual(len(kept), sweep.MAX_VARIANTS)
        tiers = {p["name"]: sweep.tier(p) for p in sweep.read_params(SAMPLE)}
        tier = lambda v: max(tiers[name] for name in v["params"])  # noqa: E731  a group of gates and switches: 0
        dropped = [v for v in full if v not in kept]
        self.assertLessEqual(max(map(tier, kept)), min(map(tier, dropped)))
        self.assertLessEqual({v["id"] for v in full if tier(v) == 0}, {v["id"] for v in kept})

    def test_every_variant_builds_with_its_value_replaced_once(self) -> None:
        write = lambda repo, commit, folder: (folder.mkdir(parents=True), (folder / "params.hpp").write_text(SAMPLE))
        with tempfile.TemporaryDirectory() as directory, patch.object(driver, "export_bot", side_effect=write):
            for v in sweep.variants(SAMPLE, cap=10**6)["variants"]:
                bot = driver.gauntlet_bot({"name": sweep.bot_name(v["id"]), "commit": CHAMPION,
                                           "params": v["params"]}, into=Path(directory))
                changed = [p for p in sweep.read_params((bot / "params.hpp").read_text())
                           if p not in sweep.read_params(SAMPLE)]
                self.assertEqual({p["name"]: p["value"] for p in changed}, v["params"], v["id"])

    def test_the_breeders_split_gates_are_one_group_variant(self) -> None:
        # Gates in series: E0097 relaxed one split gate (-3.0), E0101 turned three off (+19.3); single variants can't
        # see that, so v0024 gets the breeder's three gates as one variant.
        params = {p["name"]: p for p in sweep.read_params(SAMPLE)}
        found = sweep.variants(SAMPLE)["variants"]
        grouped = {v["group"]: v for v in found if "group" in v}
        split = grouped["split room (the breeder's gates, E0101)"]
        self.assertEqual(split["params"], {"splitParentSpace": 0, "splitParentTerritory": 0, "splitChildSpace": 0})
        self.assertEqual(split["starts"]["splitParentSpace"], params["splitParentSpace"]["value"])
        self.assertLess(found.index(split), found.index(variant("splitParentSpace=0")))  # kept, ahead of its members
        self.assertEqual(set(grouped), set(sweep.GROUPS))  # every group's params exist in v0024

    def test_groups_fit_todays_champion_and_skip_what_is_open_missing_or_a_single_variant(self) -> None:
        champion = (DATA / "params-v0028.hpp").read_text()  # 1c225be, which the table was read from
        found = sweep.variants(champion)
        names = {p["name"] for p in sweep.read_params(champion)}
        self.assertLessEqual({param for values in sweep.GROUPS.values() for param in values}, names)
        skipped = {s["param"]: s["reason"] for s in found["skipped"]}
        self.assertIn("already fully open", skipped["split room (the breeder's gates, E0101)"])  # E0101 kept it
        self.assertIn("splitEnemyHeadRadius=0, is a single variant", skipped["split whenever long enough"])
        fold = next(v for v in found["variants"] if v.get("group") == "fold merges wherever it can")
        self.assertNotIn("foldMinLength", fold["params"])  # already 1: only the values that differ change
        self.assertNotIn("finalFold", fold["params"])  # the feature's own switch is a single variant, never grouped
        without = "\n".join(line for line in champion.splitlines() if "huntMaxDist" not in line)
        self.assertIn("group needs huntMaxDist", {s["param"]: s["reason"] for s in
                                                   sweep.variants(without)["skipped"]}["hunt any longer head"])

    def test_categories_follow_the_section(self) -> None:
        self.assertEqual(sweep.category(variant("splitParentSpace=0")), "split")
        self.assertEqual(sweep.category(variant("mergeRound=499")), "endgame")
        self.assertEqual(sweep.category(variant("finalFold=false")), "endgame")
        self.assertEqual(sweep.category(variant("attackMinGain=100")), "combat")


class Passes(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "Something else",
                                            "hypothesis": "h", "status": "todo"}) + "\n")
        self.listing = [variant("splitParentSpace=0"), variant("finalFold=false")]
        self.record = {"champion": CHAMPION}

    def test_a_first_pass_of_at_least_the_threshold_earns_a_second(self) -> None:
        wins = round(sweep.SECOND_AT * 2 * sweep.FIRST_MAPS)
        played = rows("splitParentSpace=0", 1, wins) + rows("finalFold=false", 1, wins - 1)
        todo = sweep.todo(self.listing, played)
        self.assertEqual([(v["id"], n) for v, n in todo],
                         [(sweep.BASELINE, 3), ("splitParentSpace=0", 2), ("splitParentSpace=0", 3)])
        self.assertEqual([(v["id"], n) for v, n in sweep.todo(self.listing, played + rows(sweep.BASELINE, 3, 9))],
                         [("splitParentSpace=0", 2), ("splitParentSpace=0", 3)])  # the champion's seat games once
        self.assertEqual(sweep.done(self.listing, played), ["finalFold=false"])
        self.assertEqual([(v["id"], n) for v, n in sweep.todo(self.listing, played[:5])],
                         [("splitParentSpace=0", 1), ("finalFold=false", 1)])
        # A variant already decided (a sweep begun before the seat half) plays no more passes, nor the champion's.
        considered = {"splitParentSpace=0": "scored 0.600; queued as I900"}
        self.assertEqual(sweep.todo(self.listing, played, considered), [])
        self.assertEqual(sweep.done(self.listing, played, considered), ["splitParentSpace=0", "finalFold=false"])

    # Review of M82's seat half: the champion's seat games failed in the batch where the variant's played. The variant
    # waits for their retry instead of being recorded as having no pairs, and a pair needs the same seat too.
    def test_a_variant_waits_for_the_champions_seat_games_while_a_retry_is_owed(self) -> None:
        folder = driver.STATE / "sweeps" / CHAMPION
        failure = {"variant": sweep.BASELINE, "pass": 3, "game_error": "engine exited 1"}
        played = rows("splitParentSpace=0", 1, 14) + rows("splitParentSpace=0", 2, 14) + \
            rows("splitParentSpace=0", 3, 12) + [failure]
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played, folder), [])
        self.assertNotIn("splitParentSpace=0", self.record["considered"])
        self.assertEqual([(v["id"], n) for v, n in sweep.todo(self.listing, played, self.record["considered"])],
                         [(sweep.BASELINE, 3), ("finalFold=false", 1)])
        base = rows(sweep.BASELINE, 3, 10)
        pairs = 2 * sweep.seat_maps()  # 12 wins against the champion's 10
        self.assertEqual(sweep.seat_score(played + base, "splitParentSpace=0"), (0.5 + 2 / (2 * pairs), pairs))
        other = [dict(r, seat="x") for r in base]  # the same map and side against another seat is no pair
        self.assertIsNone(sweep.seat_score(played + other, "splitParentSpace=0"))
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played + [failure], folder), [])
        self.assertIn("has no seat games paired", self.record["considered"]["splitParentSpace=0"])

    def test_a_variant_that_does_not_build_is_done(self) -> None:
        played = [{"variant": "splitParentSpace=0", "pass": 1, "build_error": "static_assert"}]
        self.assertEqual([v["id"] for v, _ in sweep.todo(self.listing, played)], ["finalFold=false"])

    def test_a_combined_score_at_the_threshold_is_queued_once(self) -> None:
        total = 2 * (sweep.FIRST_MAPS + sweep.SECOND_MAPS)
        wins = math.ceil(Fraction(str(sweep.QUEUE_AT)) * total)  # the fewest wins at the threshold
        played = rows("splitParentSpace=0", 1, 12) + rows("splitParentSpace=0", 2, wins - 12)
        folder = driver.STATE / "sweeps" / CHAMPION
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played, folder), [])  # no seat half yet
        played += seated("splitParentSpace=0", 10)  # level against the seats
        added = sweep.queue(driver.STATE, self.record, self.listing, played, folder)
        self.assertEqual(len(added), 1)
        idea = driver.read_ideas()[-1]
        self.assertEqual((idea["id"], idea["status"], idea["priority"], idea["category"], idea["source"]),
                         (added[0], "todo", 3, "split", f"sweep:{CHAMPION[:7]}"))
        # A swept number is a hint for which behaviour to build as a rule, not a value to set (sig, 29 Sep: I850 set
        # kingReportAge 40 from 12 as swept).
        self.assertTrue(idea["title"].startswith("Turn the sweep's hint splitParentSpace to 0 (from 12) into a rule of "
                                                 f"what the bot observes: it scored {wins / total:.2f}"))
        for text in (f"scored {wins / total:.3f} over {total} games", f"W-L-D {wins}-{total - wins}-0",
                     "won both sides on", "Test it alone by SPRT", "a hint", str(folder / "results.jsonl"),
                     "number is a hint for which behaviour to build, not a value to set (splitParentSpace)",
                     "the champion's numbers kept otherwise", "Constants: reason",
                     "self-play on eval maps", "read with the champion seat apart",
                     f"scored 0.500 over {2 * sweep.seat_maps()} games paired with the champion's own"):
            self.assertIn(text, idea["hypothesis"])
        self.assertEqual(meta.valid_proposals([json.dumps(idea)], 1, options=False)[0]["title"], idea["title"])
        # M82: its SPRT is read with the champion seat apart, which declares no subgroup a judge's keep may pick (six
        # named ones would widen it); a keep needs the other seats pooled not below level.
        import judge
        seats = {"champion": 0.6, **{name: 0.48 for name in driver.ROTATION}}
        sprt = {"rotation": True, "per_style": {"worldgen": {"trials": 60, "score": 0.5}},
                "per_opponent": {name: {"trials": 50, "score": score} for name, score in seats.items()},
                "per_map": {f"m{k}": seats["champion"] if k < 50 else 0.48 for k in range(300)}}
        row = {"idea": idea, "sprt": sprt}
        self.assertEqual(judge.declared(row, ""), {})
        self.assertIn("outside the champion seat it reads 0.480 over 250 trials",
                      judge.self_play_only(row, judge.subgroups(sprt)[0]))
        self.assertIsNone(judge.self_play_only({**row, "idea": {**idea, "source": "meta"}}, judge.subgroups(sprt)[0]))
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played, folder), [])
        self.assertIn(added[0], self.record["considered"]["splitParentSpace=0"])

    # A switch flip stays a switch test: a behaviour choice, never tuned, so its idea sets it as swept.
    def test_a_switch_flip_is_queued_as_a_switch_test(self) -> None:
        played = rows("finalFold=false", 1, 14) + rows("finalFold=false", 2, 14) + seated("finalFold=false", 10)
        (added,) = sweep.queue(driver.STATE, self.record, self.listing, played, driver.STATE / "sweeps" / CHAMPION)
        idea = driver.read_ideas()[-1]
        self.assertEqual(idea["id"], added)
        self.assertTrue(idea["title"].startswith("Set finalFold to false (from true): it scored 0.70"))
        self.assertIn("This is a switch: a flip is a behaviour choice, not a tuned number (switches are never tuned), "
                      "so test it as it is", idea["hypothesis"])
        self.assertNotIn("hint for which behaviour", idea["hypothesis"])
        self.assertEqual(meta.valid_proposals([json.dumps(idea)], 1, options=False)[0]["title"], idea["title"])

    # M82: E0314's farmMinEndLength swept 0.63 over 60 self-play games and lost its SPRT (-8.2; its champion seat 0.459,
    # worldgen 0.475). A variant at the self-play bar that is worse than the champion against the other seats (a game
    # fewer won than the champion's there) is not queued, and the reason names both halves.
    def test_a_self_play_pass_below_level_against_the_seats_is_not_queued(self) -> None:
        folder = driver.STATE / "sweeps" / CHAMPION
        played = rows("splitParentSpace=0", 1, 14) + rows("splitParentSpace=0", 2, 14) + seated("splitParentSpace=0", 9)
        pairs = 2 * sweep.seat_maps()
        self.assertEqual(sweep.seat_score(played, "splitParentSpace=0")[:2], (0.5 - 1 / (2 * pairs), pairs))
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played, folder), [])
        self.assertEqual(self.record["considered"]["splitParentSpace=0"],
                         f"scored 0.700 in self-play but {0.5 - 1 / (2 * pairs):.3f} against the rotation's seats over "
                         f"{pairs} paired games, below 0.50 (M82)")

    def test_under_the_threshold_or_with_an_equivalent_idea_nothing_is_queued(self) -> None:
        folder = driver.STATE / "sweeps" / CHAMPION
        wins = math.ceil(Fraction(str(sweep.QUEUE_AT)) * 2 * (sweep.FIRST_MAPS + sweep.SECOND_MAPS)) - 1  # one win short
        played = rows("splitParentSpace=0", 1, 12) + rows("splitParentSpace=0", 2, wins - 12) + seated("splitParentSpace=0", 12)
        self.assertEqual(sweep.queue(driver.STATE, self.record, self.listing, played, folder), [])
        self.assertIn(f"under {sweep.QUEUE_AT}", self.record["considered"]["splitParentSpace=0"])
        driver.IDEAS.write_text(json.dumps({"id": "I202", "category": "splitting", "priority": 1, "title":
                                            "Split gates off: set splitParentTerritory, splitParentSpace and "
                                            "splitChildSpace to 0", "hypothesis": "h", "status": "todo"}) + "\n")
        record = {"champion": CHAMPION}
        played = rows("splitParentSpace=0", 1, 14) + rows("splitParentSpace=0", 2, 20) + seated("splitParentSpace=0", 12)
        self.assertEqual(sweep.queue(driver.STATE, record, self.listing, played, folder), [])
        self.assertIn("I202 already changes splitParentSpace", record["considered"]["splitParentSpace=0"])
        self.assertEqual(len(driver.read_ideas()), 1)

    def test_a_group_is_queued_naming_every_param_and_only_an_idea_changing_all_of_them_is_equivalent(self) -> None:
        split = next(v for v in sweep.variants(SAMPLE)["variants"] if v.get("group") == "split whenever long enough")
        played = rows(split["id"], 1, 14) + rows(split["id"], 2, 20) + seated(split["id"], 12)
        folder = driver.STATE / "sweeps" / CHAMPION
        # I202 set three of the group's four split gates: a different change, which the group doesn't repeat.
        driver.IDEAS.write_text(json.dumps({"id": "I202", "category": "splitting", "priority": 1, "title":
                                            "Split gates off: set splitParentTerritory, splitParentSpace and "
                                            "splitChildSpace to 0", "hypothesis": "h", "status": "todo"}) + "\n")
        added = sweep.queue(driver.STATE, self.record, [split], played, folder)
        self.assertEqual(len(added), 1)
        idea = driver.read_ideas()[-1]
        for name, value in split["params"].items():
            self.assertIn(f"{name} to {sweep.literal(value)} (from ", idea["title"])
        self.assertIn("together into a rule of what the bot observes: it scored 0.85", idea["title"])
        self.assertIn("all off at once", idea["hypothesis"])
        self.assertEqual(idea["category"], "split")
        self.assertEqual(sweep.equivalent(driver.STATE, driver.read_ideas(), list(split["params"]), CHAMPION), added[0])
        record = {"champion": CHAMPION}
        self.assertEqual(sweep.queue(driver.STATE, record, [split], played, folder), [])
        self.assertIn(f"{added[0]} already changes splitEnemyHeadRadius", record["considered"][split["id"]])

    def test_an_idea_already_tested_on_this_champion_is_equivalent(self) -> None:
        folder = driver.STATE / "sweeps" / CHAMPION
        played = rows("splitParentSpace=0", 1, 14) + rows("splitParentSpace=0", 2, 20) + seated("splitParentSpace=0", 12)
        tested = {"id": "E0095", "base": CHAMPION, "verdict": "discarded", "sprt": {"score": 0.48},
                  "idea": {"id": "I202", "title": "Split gates off: set splitParentSpace to 0", "status": "trying:lane1"}}
        untested = dict(tested, id="E0096", sprt={}, idea=dict(tested["idea"], id="I203"))  # no games: not tested
        (driver.STATE / "results.jsonl").write_text(json.dumps(untested) + "\n")
        self.assertEqual(sweep.queue(driver.STATE, {"champion": "0" * 40}, self.listing, played, folder)[:1],
                         [driver.read_ideas()[-1]["id"]])  # another champion's result doesn't count either
        driver.IDEAS.write_text("")  # drop the idea just queued: only the result is left to match
        (driver.STATE / "results.jsonl").write_text(json.dumps(tested) + "\n")
        record = {"champion": CHAMPION}
        self.assertEqual(sweep.queue(driver.STATE, record, self.listing, played, folder), [])
        self.assertIn("I202 (E0095) already changes", record["considered"]["splitParentSpace=0"])


# M59: since E0239 some params decide nothing one way. The real first passes of riskTradeMinGain=-100 and =100 from
# champion 0aa3c59's sweep (testdata/sweep-dead-sample.json): -100 played every map's two games as exact mirrors (the
# champion's moves), 100 changed 4 games, so the param is dead downwards only.
DEAD = json.loads((DATA / "sweep-dead-sample.json").read_text())


class Dead(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)

    def test_a_variant_playing_the_champions_moves_on_every_map_is_dead_in_its_direction(self) -> None:
        self.assertEqual(sweep.dead(DEAD["variants"], DEAD["rows"]),
                         {"riskTradeMinGain=-100": {"param": "riskTradeMinGain", "value": -100, "start": 0,
                                                    "direction": "down"}})
        mirrors = [r for r in DEAD["rows"] if r["variant"] == "riskTradeMinGain=-100"]
        self.assertEqual(sweep.dead(DEAD["variants"], mirrors[1:]), {})  # a map short: not shown yet
        errored = [{"variant": "riskTradeMinGain=-100", "pass": 1, "game_error": "timeout"}]
        self.assertEqual(sweep.dead(DEAD["variants"], mirrors + errored), {})

    def test_stats_list_dead_variants_and_a_claim_notes_an_idea_moving_params_only_their_dead_way(self) -> None:
        sweep.save(driver.STATE, {"champion": "0aa3c59b487f", "dead": sweep.dead(DEAD["variants"], DEAD["rows"])})
        self.assertIn("- riskTradeMinGain=-100 (down from 0)", sweep.dead_lines(driver.STATE))
        names = ["riskTradeMinGain", "outnumberedRamGuard", "ramGuardMaxEdge"]
        dead_only = {"title": "Price the risk trade", "hypothesis": "Lower P.riskTradeMinGain to trade more."}
        note = sweep.dead_note(driver.STATE, dead_only, names)
        self.assertIn("riskTradeMinGain down (riskTradeMinGain=-100)", note)
        self.assertIn("A note, not a block.", note)
        self.assertTrue(sweep.dead_note(driver.STATE, {"title": "t", "hypothesis": "riskTradeMinGain to -40"}, names))
        # Upwards the param still acts (=100 changed 4 games), and a way the text doesn't give could be either.
        for live in ("Raise riskTradeMinGain to trade less.", "riskTradeMinGain from 0 to 60", "riskTradeMinGain"):
            self.assertEqual(sweep.dead_note(driver.STATE, {"title": "t", "hypothesis": live}, names), "", live)
        tune = {"title": "t", "hypothesis": "h", "tune": {"riskTradeMinGain": [-50, 50]}}
        self.assertEqual(sweep.dead_note(driver.STATE, tune, names), "")  # a tune moves both ways
        both = {"title": "t", "hypothesis": "lower riskTradeMinGain with outnumberedRamGuard off"}
        self.assertEqual(sweep.dead_note(driver.STATE, both, names), "")  # one of its params still acts
        self.assertEqual(sweep.dead_note(driver.STATE, {"title": "t", "hypothesis": "no params"}, names), "")
        # The claim's prompt carries it, the names read from the experiment's base params.hpp.
        exp = driver.Experiment(id="E0001", idea=dead_only, lane=1, base="0aa3c59")
        with patch.object(driver, "git", return_value="    int riskTradeMinGain = 0;  // gate\n") as git:
            self.assertEqual(driver.dead_task(exp), note)
        git.assert_called_once_with("show", "0aa3c59:bot/params.hpp")

    def test_a_switch_is_dead_the_one_way_it_can_move(self) -> None:
        sweep.save(driver.STATE, {"champion": "0e7c458a6e7a", "dead": {"portalFallback=false": {
            "param": "portalFallback", "value": False, "start": True, "direction": "off"}}})
        names = ["portalFallback"]
        for text in ("turn portalFallback off", "disable portalFallback", "portalFallback"):
            self.assertIn("portalFallback off", sweep.dead_note(driver.STATE, {"title": text}, names), text)
        self.assertEqual(sweep.dead_note(driver.STATE, {"title": "old", "hypothesis": "h"}, names), "")


class Filler(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "todo"}) + "\n")
        champion = patch.object(sweep.meta, "champion_commit", return_value=CHAMPION)
        champion.start()
        self.addCleanup(champion.stop)

    def evaluate(self) -> None:
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": time.time()}))

    def test_games_stop_between_variants_once_an_evaluation_starts(self) -> None:
        items = [{"id": "a", "pass": 1}, {"id": "b", "pass": 1}, {"id": "c", "pass": 1}]
        stopped = []
        games = []
        for item, game_map, side in sweep.schedule(items, {"1": ["m1", "m2"]}, lambda: sweep.busy(Path(".")),
                                                   stopped):
            games.append((item["id"], game_map, side))
            if len(games) == 1:
                self.evaluate()  # starts while variant a plays: a finishes, b never starts
        self.assertEqual(games, [("a", "m1", "A"), ("a", "m1", "B"), ("a", "m2", "A"), ("a", "m2", "B")])
        self.assertEqual(stopped, ["an evaluation is playing on the hosts"])

    def test_a_moved_champion_is_busy_but_a_candidate_waiting_for_evaluation_is_not(self) -> None:
        self.assertIsNone(sweep.busy(Path(".")))
        self.assertEqual(sweep.busy(Path("."), "0" * 40), "the champion moved")
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "trying:lane1"}) + "\n")
        run = driver.STATE / "runs/E0001"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I001"}}))
        (run / "worker.json").write_text(json.dumps({"status": "running"}))
        self.assertIsNone(sweep.busy(Path(".")))  # still being coded
        (run / "worker.json").write_text(json.dumps({"status": "finished"}))
        # Waiting for the lock, or in a turn before it (E0180's tune test-fix turn, 21:03-21:06 with every remote host
        # idle): nothing plays for it on the hosts yet, so nothing pauses for it.
        self.assertIsNone(sweep.busy(Path(".")))

    def test_only_an_evaluation_playing_on_the_hosts_stops_or_holds_a_filler(self) -> None:
        # E0167's evidence refresh held the lock 15:52-16:02 with 0-6 of ~55 host slots busy while E0168 waited and
        # every filler was paused (MISSES 50); after that fix a filler still couldn't start under the lock, so E0180's
        # setup, merge turn, bot tests and CPU gate (21:06-21:09) left every remote host at 0. The holder's marker says
        # "hosts": false outside driver.host_stage: fillers start and play then; only a host stage stops them.
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "trying:lane1"}) + "\n")
        run = driver.STATE / "runs/E0001"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I001"}}))
        (run / "worker.json").write_text(json.dumps({"status": "finished"}))  # waiting for the lock
        self.assertIsNone(sweep.busy(Path(".")))
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": time.time(), "hosts": False}))
        self.assertIsNone(sweep.busy(Path(".")))
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": time.time(), "hosts": True}))
        self.assertEqual(sweep.busy(Path(".")), "an evaluation is playing on the hosts")

    def test_a_candidate_whose_evaluator_died_holds_no_filler_claim(self) -> None:
        # native.operate writes "finished" (driver_pid its own) before it waits for the evaluation lock; one killed
        # while waiting left its candidate counted as waiting, which held the sweep, prewarm, adversarial-map search
        # and every filler claim with no alert (throughput.waiting_now already dropped it). The idle fillers no
        # longer pause for a waiting candidate at all; the claims' rule (idle_fillers) still does.
        idea = {"category": "split", "priority": 3, "title": "t", "hypothesis": "h"}
        driver.IDEAS.write_text(json.dumps({**idea, "id": "I001", "status": "trying:lane1"}) + "\n"
                                + json.dumps({**idea, "id": "I002", "category": "retest", "status": "todo"}) + "\n")
        run = driver.STATE / "runs/E0001"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I001"}}))
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()  # a pid that has exited
        # A live evaluator, a record from before driver_pid or a hand-edited one that isn't a pid (it raised
        # TypeError here) still waits.
        for pid, waiting in ((dead.pid, False), (os.getpid(), True), (None, True), ("12x", True)):
            with self.subTest(pid=pid):
                (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": pid}))
                self.assertIsNone(sweep.busy(Path(".")))
                self.assertEqual([i["id"] for i in driver.idle_fillers(driver.read_ideas())],
                                 [] if waiting else ["I002"])

    def test_a_tune_playing_its_games_or_its_candidate_waiting_leaves_room_for_fillers(self) -> None:
        # E0105's tune was the only game work while mm, mm1, sz and the az-* hosts sat idle.
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "trying:lane3", "kind": "tune"}) + "\n")
        run = driver.STATE / "runs/E0001"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I001"}, "finished": 0.0}))
        (run / "worker.json").write_text(json.dumps({"status": "tuning", "pid": os.getpid()}))
        self.assertIsNone(sweep.busy(Path(".")))
        self.assertEqual(driver.tuning_count(driver.read_ideas()), 1)
        (run / "worker.json").write_text(json.dumps({"status": "finished"}))  # its tuned values wait for the SPRT
        self.assertIsNone(sweep.busy(Path(".")))

    def test_a_tune_whose_process_died_is_not_counted_as_tuning(self) -> None:
        # A driver that died mid-tune leaves worker.json at "tuning"; counted, it held the late screen and official
        # reports at nothing during every evaluation (native.host_share gives a live tune the filler share).
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "trying:lane3", "kind": "tune"}) + "\n")
        run = driver.STATE / "runs/E0001"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I001"}, "finished": 0.0}))
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()  # a pid that has exited
        for pid in (dead.pid, None):  # and a record from before tunes recorded their pid
            with self.subTest(pid=pid):
                (run / "worker.json").write_text(json.dumps({"status": "tuning", "pid": pid}))
                self.assertEqual(driver.tuning_count(driver.read_ideas()), 0)
        (run / "worker.json").write_text(json.dumps({"status": "tuning", "pid": os.getpid()}))
        self.assertEqual(driver.tuning_count(driver.read_ideas()), 1)

    def test_a_dead_tune_whose_pid_was_reused_hides_no_waiting_candidate(self) -> None:
        # tuning_count read a reused pid as alive (meta.alive) while stranded reads it as dead (meta.loop_process), so
        # sweep.busy subtracted the dead tune twice and a real candidate waiting beside it vanished: fillers played.
        # The claims' rule (idle_fillers) still counts both.
        idea = {"category": "split", "priority": 3, "title": "t", "hypothesis": "h"}
        driver.IDEAS.write_text(json.dumps({**idea, "id": "I001", "status": "trying:lane3", "kind": "tune"}) + "\n"
                                + json.dumps({**idea, "id": "I002", "status": "trying:lane1"}) + "\n")
        stranger = subprocess.Popen(["sleep", "60"])  # alive, but no loop entry point
        self.addCleanup(stranger.wait)
        self.addCleanup(stranger.kill)
        for exp, idea_id, worker in (("E0001", "I001", {"status": "tuning", "pid": stranger.pid,
                                                         "driver_pid": stranger.pid}),
                                     ("E0002", "I002", {"status": "finished", "driver_pid": os.getpid()})):
            run = driver.STATE / "runs" / exp
            run.mkdir(parents=True)
            (run / "experiment.json").write_text(json.dumps({"idea": {"id": idea_id}}))
            (run / "worker.json").write_text(json.dumps(worker))
        ideas = driver.read_ideas()
        self.assertEqual((driver.tuning_count(ideas), driver.stranded_count(ideas)), (0, 1))
        self.assertEqual(driver.idle_fillers(ideas), [])

    def test_tick_launches_only_while_idle_and_retries_a_dead_run_after_an_hour(self) -> None:
        now = time.time()
        with patch.object(sweep, "launch", return_value=4242) as launch:
            self.evaluate()
            self.assertIsNone(sweep.tick(Path("."), driver.STATE, now))
            launch.assert_not_called()
            self.assertNotIn("idle_at", sweep.read(driver.STATE))
            driver.EVALUATING.unlink()
            self.assertIn("launched in the background", sweep.tick(Path("."), driver.STATE, now))
            record = sweep.read(driver.STATE)
            self.assertEqual((record["pid"], record["champion"], record["started_at"], record["idle_at"]),
                             (4242, CHAMPION, now, now))
            with patch.object(sweep.meta, "alive", return_value=True):
                self.assertIsNone(sweep.tick(Path("."), driver.STATE, now + 60))
            self.assertEqual(launch.call_count, 1)
            with patch.object(sweep.meta, "alive", return_value=False):  # it died without recording an outcome
                self.assertIsNone(sweep.tick(Path("."), driver.STATE, now + 120))
                self.assertEqual(sweep.read(driver.STATE)["failures"], 1)
                self.assertEqual(launch.call_count, 1)
                self.assertIn("launched", sweep.tick(Path("."), driver.STATE, now + 120 + sweep.RETRY_S))
            sweep.update(driver.STATE, CHAMPION, pid=None, finished_at=now)
            self.assertIsNone(sweep.tick(Path("."), driver.STATE, now + 3 * sweep.RETRY_S))
            self.assertEqual(launch.call_count, 2)

    def test_tick_records_a_new_champion_before_anything_that_can_fail(self) -> None:
        sweep.save(driver.STATE, {"champion": "0" * 40, "finished_at": 1.0})
        with patch.object(sweep, "busy", side_effect=ValueError("unreadable ideas")), \
                self.assertRaises(ValueError):
            sweep.tick(Path("."), driver.STATE)
        self.assertEqual(sweep.read(driver.STATE), {"champion": CHAMPION})


# A referee GameResult like the captured one, on `game_map` and `side`, won by `winner`, or failed with `error`.
def game(game_map: str, side: str, winner: str, error: str | None = None) -> referee.GameResult:
    stats = {key: referee.BotStats(**GAME[key]) for key in ("a", "b")}
    return referee.GameResult(**{**GAME, **stats, "map": game_map, "a_side": side, "winner": winner, "error": error})


class Play(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.out = self.folder / "results.jsonl"
        self.built: list[str] = []
        self.streamed: list[list[str]] = []
        self.stops: list[str | None] = []  # busy()'s answers in turn, then None
        self.asked = 0  # busy() calls

    def play(self, items: list[dict], outcome, seats: dict | None = None) -> int:
        plan = {"champion": str(self.folder / "champion"), "out": str(self.out), "repo": ".", "commit": CHAMPION,
                "status": str(self.folder / "status.json"), "items": items, "seats": seats or {},
                "maps": {"1": ["/m/one.map", "/m/two.map"], "2": ["/m/three.map"], "3": list(seats or {})}}
        (self.folder / "plan.json").write_text(json.dumps(plan))

        def resolve(bot: str, sandbox: bool):
            self.built.append(Path(bot).name)
            if bot.endswith("bad"):
                from unswbc.errors import UserError
                raise UserError("static_assert failed")
            return ["bin"], None, "c/c++"

        def stream(jobs, bots, workers, sandbox):
            self.streamed.append([Path(b).name for b in bots])
            for job in jobs:
                yield outcome(job)

        with patch.object(unswbc.run, "_resolve", resolve), patch.object(referee, "stream_games", stream), \
                patch.object(sweep, "busy", side_effect=self.busy):
            return sweep.play(self.folder / "plan.json")

    def busy(self, *args) -> str | None:
        self.asked += 1
        return self.stops.pop(0) if self.stops else None

    def written(self) -> list[dict]:
        return [json.loads(line) for line in self.out.read_text().splitlines()] if self.out.exists() else []

    def test_each_variant_gets_its_own_games_and_a_build_failure_a_row(self) -> None:
        items = [{"id": "x=0", "pass": 1, "bot": "/b/x"}, {"id": "bad=0", "pass": 1, "bot": "/b/bad"},
                 {"id": "y=0", "pass": 2, "bot": "/b/y"}]
        code = self.play(items, lambda job: game(str(job.map), job.a_side, "a" if job.a.name == "x" else "b"))
        self.assertEqual(code, 0)
        self.assertEqual(self.streamed, [["champion", "x", "y"]])
        rows = self.written()
        self.assertEqual(rows[0], {"variant": "bad=0", "pass": 1, "build_error": "static_assert failed"})
        self.assertEqual([(r["variant"], r["pass"], r["map"], r["a_side"], r["winner"]) for r in rows[1:]],
                         [("x=0", 1, "/m/one.map", "A", "a"), ("x=0", 1, "/m/one.map", "B", "a"),
                          ("x=0", 1, "/m/two.map", "A", "a"), ("x=0", 1, "/m/two.map", "B", "a"),
                          ("y=0", 2, "/m/three.map", "A", "b"), ("y=0", 2, "/m/three.map", "B", "b")])
        self.assertEqual(set(rows[1]) - {"variant", "pass"}, set(GAME))  # the referee's own row
        self.assertEqual(rows[1]["a"], GAME["a"])
        self.assertEqual(sweep.todo([{"id": "bad=0"}], rows), [])  # done, unscored

    # M82: pass 3 plays each map against its seat, the champion's own seat games (sweep.BASELINE) too, and each row
    # names its seat.
    def test_the_seat_pass_plays_each_maps_seat_and_records_it(self) -> None:
        seats = {"/m/four.map": ["swarm", "/g/swarm-1"], "/m/five.map": ["breeder", "/g/breeder-2"]}
        items = [{"id": sweep.BASELINE, "pass": 3, "bot": str(self.folder / "champion")},
                 {"id": "x=0", "pass": 3, "bot": "/b/x"}]
        opponents = []
        code = self.play(items, lambda job: opponents.append(job.b.name) or game(str(job.map), job.a_side, "a"), seats)
        self.assertEqual(code, 0)
        self.assertEqual(self.streamed, [["champion", "swarm-1", "breeder-2", "x"]])
        self.assertEqual(opponents, ["swarm-1", "swarm-1", "breeder-2", "breeder-2"] * 2)
        self.assertEqual([(r["variant"], r["seat"]) for r in self.written()],
                         [(v, seat) for v in (sweep.BASELINE, "x=0") for seat in ("swarm",) * 2 + ("breeder",) * 2])

    def test_a_game_that_cannot_be_played_marks_its_variant_and_the_rest_play_on(self) -> None:
        items = [{"id": "x=0", "pass": 1, "bot": "/b/x"}, {"id": "y=0", "pass": 1, "bot": "/b/y"}]
        failing = lambda job: game(str(job.map), job.a_side, None if job.a.name == "x" and job.map.stem == "two" else "a",
                                   "timed out" if job.a.name == "x" and job.map.stem == "two" else None)
        self.assertEqual(self.play(items, failing), 0)
        rows = self.written()
        self.assertEqual(rows[0], {"variant": "x=0", "pass": 1, "game_error": "/m/two.map (A): timed out"})
        self.assertEqual([r["variant"] for r in rows[1:]], ["y=0"] * 4)
        variants = [{"id": "x=0"}, {"id": "y=0"}]
        self.assertEqual(sweep.todo(variants, rows), [(variants[1], 1)])  # y has 4 of its 20 games; x is done

    def test_it_waits_before_building_and_stops_between_variants(self) -> None:
        items = [{"id": "x=0", "pass": 1, "bot": "/b/x"}, {"id": "y=0", "pass": 1, "bot": "/b/y"}]
        self.stops = ["an evaluation is playing on the hosts"]
        self.assertEqual(self.play(items, lambda job: game(str(job.map), job.a_side, "a")), sweep.PAUSED)
        self.assertEqual((self.built, self.written()), ([], []))
        self.stops = [None, None, "an evaluation is playing on the hosts"]  # build, x, then y
        self.asked = 0
        self.assertEqual(self.play(items, lambda job: game(str(job.map), job.a_side, "a")), sweep.PAUSED)
        self.assertEqual([r["variant"] for r in self.written()], ["x=0"] * 4)
        self.assertEqual(self.asked, 3)


class Run(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        driver.IDEAS.write_text("")
        self.listing = {"variants": [variant("splitParentSpace=0"), variant("finalFold=false"),
                                     variant("mergeRound=499")], "skipped": []}
        self.batches: list[list[tuple[str, int]]] = []
        self.busy: list[str | None] = []  # run's busy() answers in turn, then None
        self.rate = {"splitParentSpace=0": 0.7}  # each variant's share of wins; 0.4 otherwise (the champion's too)
        self.plans: list[dict] = []

    def start(self, **record) -> None:
        sweep.save(driver.STATE, {"champion": CHAMPION, "toolchain": {"python": "/usr/bin/true"}, "pid": 1, **record})
        (driver.STATE / "sweeps" / CHAMPION).mkdir(parents=True)

    def referee(self, script: str, args: list[str], timeout: float = 0, yielding: bool = False):
        plan = json.loads(Path(args[1]).read_text())
        self.batches.append([(item["id"], item["pass"]) for item in plan["items"]])
        self.assertTrue(yielding)
        self.assertEqual(driver.RUNTIME_PYTHON.get(), "/usr/bin/true")
        self.plans.append(plan)
        with open(plan["out"], "a") as out:
            for item in plan["items"]:
                sides = [(m, s) for m in plan["maps"][item["maps"]] for s in "AB"]
                wins = round(self.rate.get(item["id"], 0.4) * len(sides))
                out.writelines(json.dumps({**GAME, "variant": item["id"], "pass": item["pass"], "map": m, "a_side": s,
                                           "winner": "a" if k < wins else "b"}) + "\n"
                               for k, (m, s) in enumerate(sides))
        return subprocess.CompletedProcess([], 0, "", "")

    def run_sweep(self, referee=None) -> dict:
        def maps(out: Path, seed: int, count: int, style: str = "eval") -> None:
            out.mkdir(parents=True, exist_ok=True)
            for i in range(count):
                (out / f"gen_{style}_{seed + i}.map").write_text("map")

        bot = lambda spec, into, source=None: into / spec["name"]  # noqa: E731
        seats = lambda names: [(name, driver.STATE / "gauntlet" / name) for name in names]  # noqa: E731
        with patch.object(sweep, "variants", return_value=self.listing), \
                patch.object(sweep, "champion_params", return_value=SAMPLE), \
                patch.object(sweep, "source_export", return_value=driver.STATE / "source"), \
                patch.object(driver, "gauntlet_bot", side_effect=bot), patch.object(driver, "generate_maps", maps), \
                patch.object(driver, "rotation_bots", side_effect=seats), \
                patch.object(driver, "referee", side_effect=referee or self.referee), \
                patch.object(sweep, "busy", side_effect=lambda *a: self.busy.pop(0) if self.busy else None):
            sweep.run(driver.REPO, driver.STATE)
        self.assertIsNone(driver.RUNTIME_PYTHON.get())  # the runtime is set for the sweep's games only
        return sweep.read(driver.STATE)

    def test_it_plays_every_pass_queues_the_winner_and_finishes(self) -> None:
        self.start(failures=1, error="earlier")
        record = self.run_sweep()
        self.assertEqual(self.batches, [[("splitParentSpace=0", 1), ("finalFold=false", 1), ("mergeRound=499", 1)],
                                        [(sweep.BASELINE, 3), ("splitParentSpace=0", 2), ("splitParentSpace=0", 3)]])
        # Pass 3's maps: eval then worldgen, each seat one of each; a farm variant's pass 2 has dead-end and near maps.
        maps, seats = self.plans[-1]["maps"], self.plans[-1]["seats"]
        styles = lambda names: [Path(name).name.split("_")[1] for name in names]  # noqa: E731
        # Each seat one eval and one worldgen map, whatever the rotation's size (6 seats from 29 Sep).
        self.assertEqual(styles(maps["3"]), ["eval"] * len(driver.ROTATION) + ["worldgen"] * len(driver.ROTATION))
        self.assertEqual([seats[m][0] for m in maps["3"]], list(driver.ROTATION) * 2)
        self.assertEqual(sorted(styles(maps["2f"])), ["deadend"] * 2 + ["eval"] * 6 + ["near"] * 2)
        self.assertEqual(sweep.maps_key({"section": "Fast tiles and dead-end farms", "params": {"farmMinEndLength": 0}},
                                        2), "2f")
        self.assertEqual((record["pid"], record["failures"], record["error"]), (None, 0, None))
        self.assertIn("finished_at", record)
        self.assertEqual(record["done"], ["splitParentSpace=0", "finalFold=false", "mergeRound=499"])
        self.assertEqual(record["queued"], [driver.read_ideas()[0]["id"]])

    def test_the_next_batch_is_exported_and_built_while_one_plays(self) -> None:
        # 29 Sep: each batch exported and built its variants (~56 s for 8) before its games, with its share of every
        # machine idle, so the sweep played in bursts. Now the next batch's are ready when a batch ends.
        self.start()
        self.rate = {}  # no second passes: three first-pass batches
        built, building = [], threading.Event()

        def referee(script: str, args: list[str], timeout: float = 0, yielding: bool = False):
            if args[0] == "build":
                self.assertEqual(driver.RUNTIME_PYTHON.get(), "/usr/bin/true")  # the referee's runtime, as play's
                built.append([Path(bot).name for bot in args[1:]])
                building.set()
                return subprocess.CompletedProcess([], 0, "", "")
            if not self.batches:  # the first batch: the second's builds come while it plays
                self.assertTrue(building.wait(10))
            return self.referee(script, args, timeout, yielding)

        with patch.object(sweep, "BATCH", 1):
            self.run_sweep(referee)
        self.assertEqual(built, [["finalFold_false"], ["mergeRound_499"]])
        self.assertEqual(self.batches, [[("splitParentSpace=0", 1)], [("finalFold=false", 1)], [("mergeRound=499", 1)]])

    def test_the_next_batch_leaves_out_a_variant_this_batch_builds(self) -> None:
        # Pass 2 and pass 3 of a winner straddling the batch boundary: play builds it, so prepare_ahead must not.
        self.start()
        built = []

        def referee(script: str, args: list[str], timeout: float = 0, yielding: bool = False):
            if args[0] == "build":
                built.append([Path(bot).name for bot in args[1:]])
                return subprocess.CompletedProcess([], 0, "", "")
            return self.referee(script, args, timeout, yielding)

        with patch.object(sweep, "BATCH", 2):
            self.run_sweep(referee)
        self.assertIn([(sweep.BASELINE, 3), ("splitParentSpace=0", 2)], self.batches)
        self.assertFalse([names for names in built if "splitParentSpace_0" in names])

    def test_a_sweep_begun_on_a_smaller_rotation_keeps_its_seat_maps(self) -> None:
        # Its variants' pass 3 already played on them: a larger count would play every one of them again.
        self.start()
        for name in ("maps-3", "maps-3w"):
            (driver.STATE / "sweeps" / CHAMPION / name).mkdir()
            for i in range(5):
                (driver.STATE / "sweeps" / CHAMPION / name / f"old_{i}.map").write_text("map")
        (driver.STATE / "sweeps" / CHAMPION / "maps-3" / "SEED").write_text("1")  # maps-3w's making was cut short
        self.run_sweep()
        self.assertEqual(len(self.plans[-1]["maps"]["3"]), 2 * len(driver.ROTATION))  # no short pass fixed by it
        clear_home()
        driver.IDEAS.write_text("")
        self.plans, self.batches = [], []
        self.start()
        for name in ("maps-3", "maps-3w"):
            (driver.STATE / "sweeps" / CHAMPION / name).mkdir()
            (driver.STATE / "sweeps" / CHAMPION / name / "SEED").write_text("1")
            for i in range(5):
                (driver.STATE / "sweeps" / CHAMPION / name / f"old_{i}.map").write_text("map")
        self.run_sweep()
        maps = self.plans[-1]["maps"]["3"]
        self.assertEqual(len(maps), 10)
        self.assertEqual(self.batches[-1][-1], ("splitParentSpace=0", 3))
        # The champion's seat games played on the old rotation: its maps keep their seats, so every game pairs.
        rotation = list(driver.ROTATION)[:5]
        played = [{"variant": sweep.BASELINE, "pass": 3, "map": m, "seat": rotation[i % 5]} for i, m in enumerate(maps)]
        self.assertEqual(list(sweep.seats(maps, played).values()), rotation * 2)
        self.assertEqual(list(sweep.seats(maps).values())[5:], [driver.ROTATION[k % len(driver.ROTATION)]
                                                                for k in range(5, 10)])

    def test_a_pause_keeps_the_progress_and_a_played_batch_clears_old_failures(self) -> None:
        self.start(failures=1, error="earlier")
        self.busy = [None, "an evaluation is running"]
        record = self.run_sweep()
        self.assertEqual(len(self.batches), 1)
        self.assertEqual((record["pid"], record["paused"], record["failures"], record["error"]),
                         (None, "an evaluation is running", 0, None))
        self.assertNotIn("finished_at", record)
        self.assertEqual(sweep.todo(self.listing["variants"], sweep.results(driver.STATE / "sweeps" / CHAMPION))[1:],
                         [(self.listing["variants"][0], 2), (self.listing["variants"][0], 3)])  # resumes: second pass

    def test_an_evaluation_on_the_hosts_is_waited_out_in_place(self) -> None:
        # Until 30 Sep a filler exited at a host stage and only a later upkeep tick (up to 17 min each) started it
        # again: its first game came 0-11 min after the stage ended. It waits in its run now, pid kept and marked
        # paused (so no tick starts a second run, nor health.py calls it hung), and plays on as the stage ends.
        self.start()
        self.busy = [None, sweep.ON_HOSTS, sweep.ON_HOSTS, sweep.ON_HOSTS]  # the second batch's check waits twice
        seen = []
        with patch.object(sweep.time, "sleep", lambda s: seen.append((s, sweep.read(driver.STATE)))):
            record = self.run_sweep()
        self.assertEqual([s for s, _ in seen], [sweep.WAIT_S] * 2)
        self.assertEqual([(r["pid"], r["paused"]) for _, r in seen], [(1, sweep.ON_HOSTS)] * 2)
        self.assertEqual(len(self.batches), 2)  # played on after the wait, and finished
        self.assertIsNone(record["paused"])
        self.assertIn("finished_at", record)

    def test_a_stop_marker_during_the_wait_ends_the_run(self) -> None:
        self.start()
        self.busy = [None, sweep.ON_HOSTS, "a stop marker is set"]
        with patch.object(sweep.time, "sleep", lambda s: None):
            record = self.run_sweep()
        self.assertEqual(len(self.batches), 1)
        self.assertEqual((record["pid"], record["paused"]), (None, "a stop marker is set"))

    def test_variants_are_copies_of_one_export(self) -> None:
        # Each export checks the whole repository first (publishing.repo_intact's fsck, ~6 s here on 30 Sep), which
        # made the next batch's exports slower than a batch's games and the sweep alone kept 45% of the slots busy.
        exports, bots = [], driver.STATE / "bots"

        def export(repo: Path, commit: str, destination: Path) -> None:
            exports.append(commit)
            destination.mkdir(parents=True)
            (destination / "params.hpp").write_text("inline int a = 1;\ninline bool b = true;\n")
            (destination / "main.cpp").write_text("int main() {}\n")

        with patch.object(driver, "export_bot", side_effect=export):
            source = sweep.source_export(bots, CHAMPION)
            one = driver.gauntlet_bot({"name": "a_0", "commit": CHAMPION, "params": {"a": 0}}, into=bots, source=source)
            two = driver.gauntlet_bot({"name": "b_false", "commit": CHAMPION, "params": {"b": False}}, into=bots,
                                      source=source)
            self.assertEqual(sweep.source_export(bots, CHAMPION), source)
        self.assertEqual(exports, [CHAMPION])
        self.assertEqual((one / "params.hpp").read_text(), "inline int a = 0;\ninline bool b = true;\n")
        self.assertEqual((two / "params.hpp").read_text(), "inline int a = 1;\ninline bool b = false;\n")
        self.assertEqual((two / "main.cpp").read_text(), "int main() {}\n")
        self.assertEqual((source / "params.hpp").read_text(), "inline int a = 1;\ninline bool b = true;\n")

    def test_a_waiting_run_ends_on_a_new_champion_but_not_on_a_failed_read(self) -> None:
        # Asked every WAIT_S through a host stage: a git blip mustn't end the run, and an old champion's run must end
        # rather than wait on, holding the pid that keeps the tick from recording the new champion (review of b098630a).
        with patch("native.evaluation_needs_hosts", return_value=True):
            with patch.object(sweep.meta, "champion_commit", return_value=""):
                self.assertEqual(sweep.busy(Path("."), CHAMPION), sweep.ON_HOSTS)
            with patch.object(sweep.meta, "champion_commit", return_value="0" * 40):
                self.assertEqual(sweep.busy(Path("."), CHAMPION), "the champion moved")

    def test_a_call_holds_enough_variants_for_every_slot(self) -> None:
        # 30 Sep: 8 variants (160 games) a call was about 30 s of games on 72 slots, in 1.9 min of its scheduler.
        (driver.STATE / "utilisation.jsonl").write_text(json.dumps({"at": 1, "capacity": 72}) + "\n")
        self.assertEqual(sweep.batch_size(driver.STATE), math.ceil(sweep.CALL_ROUNDS * 72 / (2 * sweep.FIRST_MAPS)))
        (driver.STATE / "utilisation.jsonl").write_text(json.dumps({"at": 1, "capacity": 4}) + "\n")
        self.assertEqual(sweep.batch_size(driver.STATE), sweep.BATCH)
        (driver.STATE / "utilisation.jsonl").unlink()
        self.assertEqual(sweep.batch_size(driver.STATE), sweep.BATCH)

    def test_a_new_champion_ends_the_run_without_touching_its_record(self) -> None:
        self.start()

        def promoted(*args, **kwargs):
            sweep.save(driver.STATE, {"champion": "0" * 40})  # the tick replaced the record meanwhile
            return self.referee(*args, **kwargs)

        self.assertEqual(self.run_sweep(promoted), {"champion": "0" * 40})
        self.assertEqual(len(self.batches), 1)

    def test_a_failed_batch_is_recorded_for_a_retry(self) -> None:
        self.start(failures=1)
        record = self.run_sweep(lambda *a, **k: subprocess.CompletedProcess([], 2, "", "the champion does not build"))
        self.assertEqual((record["pid"], record["failures"]), (None, 2))
        self.assertIn("play exited 2: the champion does not build", record["error"])
        self.assertIn("failed_at", record)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class Health(unittest.TestCase):
    def setUp(self) -> None:
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})  # see test_health
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo, self.state = Path(directory.name) / "repo", Path(directory.name) / "state"
        self.repo.mkdir()
        self.state.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "fixture")
        git(self.repo, "branch", "loop/champion")
        self.champion = git(self.repo, "rev-parse", "loop/champion")
        self.promoted = time.time()

    def check(self, record: dict, now: float) -> list[str]:
        sweep.save(self.state, record)
        return [alert.key for alert in health.sweep(self.state, self.repo, now)]

    def test_repeated_failures_and_a_hung_run_alert(self) -> None:
        record = sweep.failure({"champion": self.champion, "started_at": self.promoted}, "boom", self.promoted)
        self.assertEqual(self.check(record, self.promoted), [])
        record = sweep.failure(record, "RuntimeError: play exited 2: game failed", self.promoted)
        self.assertEqual(self.check(record, self.promoted), ["sweep:failed"])
        self.assertIn("play exited 2", health.sweep(self.state, self.repo, self.promoted)[0].text)
        running = {"champion": self.champion, "started_at": self.promoted, "pid": 4242, "progress_at": self.promoted}
        self.assertEqual(self.check(running, self.promoted + sweep.STALE - 60), [])
        self.assertEqual(self.check(running, self.promoted + sweep.STALE + 60), ["sweep:stale"])

    # plan.json, rewritten each batch for play: torn, or over sweep.PLAN_BOUND, alerts (health.plan_file).
    def test_a_broken_plan_alerts(self) -> None:
        record = {"champion": self.champion, "started_at": self.promoted}
        folder = self.state / "sweeps" / self.champion
        folder.mkdir(parents=True)
        (folder / "plan.json").write_text(json.dumps({"maps": {"1": ["m"] * 10}, "items": []}, indent=1) + "\n")
        self.assertEqual(self.check(record, self.promoted), [])
        (folder / "plan.json").write_text('{"maps": ')
        self.assertEqual(self.check(record, self.promoted), ["sweep:plan"])
        (folder / "plan.json").write_text(json.dumps(["x" * sweep.PLAN_BOUND]))
        self.assertEqual(self.check(record, self.promoted), ["sweep:plan"])

    # Real-format rows: games as the referee writes them (GAME, from run.to_json) and game_error rows as play writes them.
    def results(self, outcomes: str) -> None:
        folder = self.state / "sweeps" / self.champion
        folder.mkdir(parents=True, exist_ok=True)
        lines = []
        for k, outcome in enumerate(outcomes):
            if outcome == "e":
                lines.append({"variant": f"v{k}=0", "pass": 1, "game_error": f"/m/gen_eval_{k}.map (A): timed out"})
            else:
                lines += [{"variant": f"v{k}=0", "pass": 1, **GAME, "a_side": side} for side in "AB"]
        (folder / "results.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    def test_many_or_consecutive_unplayable_games_alert(self) -> None:
        record = {"champion": self.champion, "started_at": self.promoted}
        for outcomes, alerts in (("e", []), ("eggggggggg", []), ("eeggggggg", ["sweep:errors"]),
                                 ("ggggggggggggggggggggggggeeeee", ["sweep:errors"]),
                                 ("gggggggggggggggggggggggggeeee", [])):
            with self.subTest(outcomes=outcomes):
                self.results(outcomes)
                self.assertEqual(self.check(record, self.promoted), alerts)
        self.results("geeggg")
        text = health.sweep(self.state, self.repo, self.promoted)[0].text
        self.assertIn("2 of the variant sweep's 6 finished variant passes", text)
        self.assertIn("the first: /m/gen_eval_1.map (A): timed out", text)

    def test_a_group_naming_a_param_the_champion_lacks_alerts_and_other_skips_stay_quiet(self) -> None:
        champion = (DATA / "params-v0028.hpp").read_text()
        (self.repo / "bot").mkdir()
        (self.repo / "bot/params.hpp").write_text(champion)  # v0028: two groups skipped, as open or a single variant
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "v0028 params")
        git(self.repo, "branch", "-f", "loop/champion")
        self.assertEqual(health.sweep_groups(self.repo), [])
        (self.repo / "bot/params.hpp").write_text(champion.replace("huntMaxDist", "huntReach"))  # a rename
        git(self.repo, "commit", "-qam", "renamed")
        git(self.repo, "branch", "-f", "loop/champion")
        (alert,) = health.sweep_groups(self.repo)
        self.assertEqual(alert.key, "sweep:groups")
        self.assertIn("hunt any longer head (group needs huntMaxDist", alert.text)
        self.assertIn("sweep:groups", [a.key for a in health.alerts(self.state, self.repo, time.time(), {})])

    def test_a_day_old_champion_without_a_sweep_alerts_when_the_machines_were_idle(self) -> None:
        later = self.promoted + health.SWEEP_GRACE + 60
        self.assertEqual(self.check({"champion": self.champion, "idle_at": self.promoted + 600}, self.promoted + 3600),
                         [])
        self.assertEqual(self.check({"champion": self.champion}, later), [])  # never idle: the queue kept them busy
        self.assertEqual(self.check({"champion": self.champion, "idle_at": self.promoted + 600}, later),
                         ["sweep:unstarted"])
        self.assertEqual(self.check({"champion": "0" * 40, "finished_at": self.promoted}, later), ["sweep:missing"])
        self.assertEqual(self.check({"champion": self.champion, "idle_at": self.promoted + 600,
                                     "started_at": self.promoted + 600}, later), [])


if __name__ == "__main__":
    unittest.main()
