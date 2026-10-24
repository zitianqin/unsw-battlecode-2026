"""The adversarial-map search: its mutations and perturbations on real maps from maps/ (every result mirrored, valid
and different), the seed pool, the dead-end detector, the margin's order, finds and their perturbation check, the
search's order and budget, the strong pass past the first cap (both sides lost, the worst maps and perturbations only
where a find is possible, a pass the budget can't hold skipped) and its judging by each opponent's own loss rate, the
regression maps' opponents from the real maps/adversarial/README.md (an unknown one an error row, played again once its
row names another), a lost regression map alerting only when the champion before didn't lose it too (the earlier
champions from the version tags), the perturbation reserve, tidy keeping the strong pass's worst climb maps, the
background run's pause, finish, idle-only stages, a tidied search reopened without reading deleted files, and file
bounds with a fake referee writing real-format rows (testdata/sweep-game.jsonl, a real run.to_json line), `play`
stopping between maps, the tick, wanted() and the health check."""

import testhome  # before anything that reads live paths
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import advmaps
import driver
import health
import sweep

sys.path.insert(0, str(driver.REPO / "referee"))
import run as referee  # noqa: E402  the referee's GameResult and to_json, as play() uses them
import unswbc.run  # noqa: E402

DATA = Path(__file__).resolve().parent / "testdata"
GAME = json.loads((DATA / "sweep-game.jsonl").read_text())  # one line of a real run.to_json output
MAPS = driver.REPO / "maps"
SAMPLES = [MAPS / "community/portal_labyrinth.map", MAPS / "ladder/devil.map", MAPS / "ladder/trophy.map",
           MAPS / "sprint/dilemma.map"]
CHAMPION = "4e3da734e842fa0dbedc9323b2e46ba8bd2ae63e"
EARLIER = "9b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c"  # the champion before it
GEN = advmaps.load_gen(driver.REPO)


def board(path: Path):
    return GEN.parse(path.read_text())


# A game row as write_rows makes it: the real referee row, as bot a (us) on `side` of map `name`.
def row(name: str, stage: str, side: str, winner: str, alive: int = 6, rounds: int = 500, end: str = "rounds",
        **extra) -> dict:
    return {**GAME, "name": name, "stage": stage, "map": f"/x/{name}.map", "a_side": side, "winner": winner,
            "end": end, "rounds": rounds, "a": {**GAME["a"], "alive": alive}, **extra}


def clear_home() -> None:
    for path in driver.STATE.iterdir():
        shutil.rmtree(path) if path.is_dir() else path.unlink()


class Maps(unittest.TestCase):
    def test_every_mutation_keeps_real_maps_mirrored_valid_and_readable(self) -> None:
        for path in SAMPLES:
            source = board(path)
            self.assertIsNone(advmaps.valid(GEN, source, source), path.name)
            for name, (mutation, _) in advmaps.MUTATIONS.items():
                accepted = 0
                for seed in range(6):
                    if accepted == 2:  # up to 6 tries for 2 checked children: reach checks are this test's cost
                        break
                    with self.subTest(map=path.stem, mutation=name, seed=seed):
                        child = board(path)
                        if not mutation(GEN, child, random.Random(seed)) or advmaps.valid(GEN, child, source):
                            continue
                        accepted += 1
                        back = GEN.parse(GEN.render(child, "t"))  # as the engine will read it
                        self.assertTrue(advmaps.symmetric(back))
                        self.assertIsNone(advmaps.valid(GEN, back, source))
                        self.assertTrue(GEN.check(back, 0.4))
                moot = {"wall_border": advmaps.features(source)["numbers"]["border_kelp"] == 1,  # walled already
                        "portal_move": not any(k >= 0 for k in source.edges.values())}
                if not moot.get(name):
                    self.assertGreater(accepted, 0, f"{name} never made a valid change to {path.stem}")

    def test_a_mutant_and_each_perturbation_differ_from_the_map_and_are_valid(self) -> None:
        source = board(SAMPLES[0])
        mutant, ops = advmaps.mutate(GEN, source, random.Random(3))
        self.assertTrue(set(ops) <= set(advmaps.MUTATIONS))
        texts = {GEN.render(source, "t"), GEN.render(mutant, "t")}
        rng = random.Random(4)
        for _ in range(advmaps.PERTURBATIONS):
            child = advmaps.perturb(GEN, source, rng)
            self.assertIsNone(advmaps.valid(GEN, child, source))
            texts.add(GEN.render(child, "t"))
        self.assertEqual(len(texts), 2 + advmaps.PERTURBATIONS)

    def test_one_sided_edits_and_rule_breaks_are_rejected(self) -> None:
        source = board(SAMPLES[1])
        free = next(e for e in sorted({source.side(t, d) for t in source.all_tiles() for d in "NW"})
                    if e not in source.edges and source.mirror_edge(e) != e)
        one_sided = board(SAMPLES[1])
        one_sided.edges[free] = GEN.KELP  # without its mirror
        self.assertEqual(advmaps.valid(GEN, one_sided, source), "not symmetric")
        gap = board(SAMPLES[1])
        tile = next(t for t in gap.all_tiles() if gap.mirror(t) != t)
        gap.gaps[tile] = (1, 1)
        self.assertFalse(advmaps.symmetric(gap))
        short = board(SAMPLES[1])
        short.dragons[0:2] = [short.dragons[0][:1], short.dragons[1][:1]]
        self.assertEqual(advmaps.valid(GEN, short, source), "a dragon of length 1")
        portal = board(SAMPLES[1])
        portal.edges[free] = 99  # a portal end with no partner (and no mirror)
        self.assertIsNotNone(advmaps.valid(GEN, portal, source))

    # Real maps from champion ffc2c10's search (29 Sep): c075, a kelp_region mutant of c007, walled each length-3
    # starter into a 12-tile loop-free pocket, and c095, its dragons_move mutant, into 3 tiles; every bot died by r7 and
    # the search bred 8 finds from them. The kept maps' pockets are 927-1015 tiles with loops. Dilemma, whose 11-long
    # starters are walled at both ends but can split, is SAMPLES' and passes above.
    def test_a_dragon_trapped_in_a_loop_free_start_pocket_is_rejected(self) -> None:
        c007 = board(DATA / "advmaps-trapped/c007.map")
        self.assertIsNone(advmaps.valid(GEN, c007, c007))
        for path in sorted((MAPS / "adversarial").glob("*.map")):
            self.assertIsNone(advmaps.valid(GEN, board(path), board(path)), path.name)
        for name in ("c075", "c095"):
            self.assertEqual(advmaps.valid(GEN, board(DATA / f"advmaps-trapped/{name}.map"), c007),
                             "a dragon trapped in a loop-free start pocket", name)
        fed = board(DATA / "advmaps-trapped/c095.map")  # a pearl it can reach lets it grow to 4 and split
        taken = {t for body in fed.dragons for t in body}
        head = fed.dragons[0][0]
        fed.set_gap(next(n for d in GEN.DIRS if (n := fed.step(head, d)) is not None and n not in taken), (1, 40))
        self.assertEqual(advmaps.trapped(GEN, fed), [])

    def test_the_seed_pool_draws_on_every_style_the_community_maps_and_every_knob(self) -> None:
        sources = advmaps.seed_sources(GEN)
        self.assertLessEqual({("style", s) for s in GEN.STYLES} | {("knob", k) for k in advmaps.KNOBS}
                             | {("community", "near")}, set(sources))
        for i in range(len(sources)):
            with self.subTest(source=sources[i]):
                text, origin = advmaps.seed_map(GEN, CHAMPION, i)
                seed = GEN.parse(text)
                # Every style passes the default floor except the two built to break it, which are their own floor as the
                # search treats them (macro's coiled starters are boxed in by design, a sealed portalrooms map reaches half),
                # and a near copy of Portals, whose sealed halves it keeps (gen.keeps_rooms, 2026-09-30), so each team
                # reaches under half the board: it takes its source's floor, as gen.near checks it.
                own = sources[i][0] == "style" and sources[i][1] in ("macro", "portalrooms")
                portals = "MAP_NAME near_portals" in text
                floor = seed if own else GEN.parse((GEN.LADDER / "portals.map").read_text()) if portals else None
                self.assertIsNone(advmaps.valid(GEN, seed, floor), origin)
        self.assertEqual(advmaps.seed_map(GEN, CHAMPION, 3), advmaps.seed_map(GEN, CHAMPION, 3))  # deterministic

    def test_a_carved_pocket_is_a_dead_end_and_no_start_is(self) -> None:
        trophy = board(MAPS / "ladder/trophy.map")
        pocket = advmaps.carve_pocket(GEN, trophy, random.Random(5))
        dead = advmaps.dead_ends(trophy)
        self.assertLessEqual(pocket, dead)
        self.assertFalse(dead & {t for body in trophy.dragons for t in body})
        labyrinth = advmaps.features(board(SAMPLES[0]))
        self.assertIn("teams meet only through portals", labyrinth["flags"])  # its halves join only by portals
        self.assertIn("walled border", labyrinth["flags"])


class Scoring(unittest.TestCase):
    def test_a_loss_draw_narrow_win_and_comfortable_win_are_in_that_order(self) -> None:
        loss, draw = row("m", "seed", "A", "b"), row("m", "seed", "A", "draw")
        narrow = row("m", "seed", "A", "a", alive=advmaps.NARROW, rounds=10, end="elimination")  # fast, but narrow
        slow = row("m", "seed", "A", "a", alive=advmaps.NARROW + 1)  # no elimination, just past narrow
        big = row("m", "seed", "A", "a", alive=20, rounds=60, end="elimination")
        margins = [advmaps.margin(r) for r in (loss, draw, narrow, slow, big)]
        self.assertEqual(margins, sorted(margins))
        self.assertEqual(len(set(margins)), 5)
        self.assertLess(advmaps.map_margin([loss, big]), advmaps.map_margin([draw, draw]))  # the worse side decides
        # A map starting one dragon a team (s013 of the first real run, a sparse knob map): that one left is no near
        # miss; down to one of two is.
        whole = row("m", "seed", "A", "a", alive=1, rounds=107, end="elimination", starts=1)
        halved = row("m", "seed", "A", "a", alive=1, rounds=107, end="elimination", starts=2)
        self.assertGreaterEqual(advmaps.margin(whole), advmaps.COMFORT)
        self.assertLess(advmaps.margin(halved), advmaps.COMFORT)


class Search(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        self.folder = advmaps.folder_of(driver.STATE, CHAMPION)
        self.folder.mkdir(parents=True)
        self.adversarial = driver.STATE / "adv-maps"
        self.adversarial.mkdir()
        shutil.copy(MAPS / "community/snake_trap.map", self.adversarial / "snake_trap.map")
        where = patch.object(advmaps, "adversarial_dir", return_value=self.adversarial)
        where.start()
        self.addCleanup(where.stop)
        self.record = {"champion": CHAMPION}
        self.settings = {**advmaps.DEFAULTS, "seed_maps": 4, "keep": 2}

    def propose(self) -> tuple[list[dict], str | None]:
        return advmaps.propose(GEN, self.folder, CHAMPION, self.record, self.settings)

    # Our games on `maps`: those in `losing` lost as B, those in `both` lost on both sides, the rest won.
    def play(self, maps: list[dict], losing=(), both=(), **extra) -> None:
        with (self.folder / "results.jsonl").open("a") as out:
            for m in maps:
                for side in "AB":
                    lose = m["name"] in both or (m["name"] in losing and side == "B")
                    out.write(json.dumps(row(m["name"], m["stage"], side, "b" if lose else "a", **extra)) + "\n")

    def test_regression_seeds_perturbations_then_a_find_with_its_alert(self) -> None:
        regress, why = self.propose()
        self.assertEqual(([m["stage"] for m in regress], why), (["regress"], None))
        self.play(regress, losing={"r-snake_trap"})
        self.assertEqual(len(advmaps.decide(driver.STATE, self.record, GEN, self.folder)), 1)  # the regression alert
        self.assertEqual(advmaps.decide(driver.STATE, self.record, GEN, self.folder), [])  # once
        seeds, _ = self.propose()
        self.assertEqual([m["name"] for m in seeds], ["s000", "s001", "s002", "s003"])
        self.play(seeds, losing={"s001"}, deaths={"hitWall": 2, "hitSelf": 1}, wall_dead_end=1)
        proposed, why = self.propose()  # the lost map's perturbations first, a climb round beside them (explore)
        kids = [m for m in proposed if m["stage"] == "perturb"]
        self.assertEqual(({m["parent"] for m in kids}, len(kids), why), ({"s001"}, advmaps.PERTURBATIONS, None))
        self.assertEqual(proposed[:len(kids)], kids)
        self.assertEqual({m["stage"] for m in proposed[len(kids):]}, {"climb"})
        self.assertNotIn("s001", {m["parent"] for m in proposed[len(kids):]})  # a map awaiting its verdict breeds none
        self.assertEqual(self.propose(), ([], None))  # nothing new while they wait for their games
        self.play(kids[:advmaps.REPRODUCE], losing={k["name"] for k in kids}, deaths={"hitWall": 1}, wall_dead_end=1)
        self.play(kids[advmaps.REPRODUCE:])
        (self.folder / "kept").mkdir()
        (self.folder / "kept" / "s001-aB.replay").write_bytes(b"replay")
        # 1 of 4 seed maps lost: luck alone gives 3 of 6 too often to alert on, which the next test does
        self.assertGreater(advmaps.find_chance(advmaps.listing(self.folder), advmaps.by_map(
            advmaps.results(self.folder)), advmaps.REPRODUCE, advmaps.PERTURBATIONS)[0], advmaps.chance.TRIGGER_P)
        with patch.object(advmaps, "find_chance", return_value=(0.02, 0.088, 40)):
            lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(list(self.record["finds"]), ["s001"])
        self.assertIn("(p 0.02: luck alone at the seed pool's loss rate, at most 8.8%, over the 40 maps searched against "
                      f"the starter, times the {advmaps.groups()} opponents a search plays)", lines[0])
        self.assertIn("ADVMAPS FIND", lines[0])
        self.assertIn("s001", lines[0])
        self.assertIn(f"on {advmaps.REPRODUCE} of {advmaps.PERTURBATIONS} perturbations", lines[0])
        # s001's two games (2 + 2 hitWall, 1 + 1 in a dead end) and its perturbations' six tallied (1 each)
        self.assertIn("hitWall 10 (8 in a dead end or corridor), hitSelf 2", lines[0])
        alerts = (driver.STATE / "host-alerts.log").read_text()
        self.assertIn("ADVMAPS REGRESSION", alerts)
        self.assertIn("ADVMAPS FIND", alerts)
        find = Path(self.record["finds"]["s001"]["folder"])
        self.assertTrue((find / "s001.map").exists() and (find / "replays" / "s001-aB.replay").exists())
        self.assertEqual(len(list(find.glob("s001-p*.map"))), advmaps.PERTURBATIONS)
        self.assertEqual(json.loads((find / "find.json").read_text())["map"], "s001")

    # An earlier champion as its search left snake_trap's regression games (the live maps.jsonl and results.jsonl
    # shape): lost as B when `lose`, else won both sides; `finished` False leaves only side A's game played.
    def earlier_search(self, lose: bool, champion: str = EARLIER, finished: bool = True) -> None:
        folder = advmaps.folder_of(driver.STATE, champion)
        folder.mkdir(parents=True)
        entry = {"name": "r-snake_trap", "stage": "regress", "origin": "maps/adversarial/snake_trap.map",
                 "opponent": "starter", "path": str(folder / "maps" / "r-snake_trap.map")}
        (folder / "maps.jsonl").write_text(json.dumps(entry) + "\n")
        (folder / "results.jsonl").write_text("".join(
            json.dumps(row("r-snake_trap", "regress", side, "b" if lose and side == "B" else "a")) + "\n"
            for side in ("AB" if finished else "A")))

    # Every champion lost portal_labyrinth_85163986 from v0001, so each new one alerted on it again (five alerts on
    # 2026-09-27): a map the champion before also lost stays in the record, unalerted; one it won alerts.
    def test_a_regression_map_alerts_only_when_the_champion_before_did_not_lose_it(self) -> None:
        for earlier_lost in (False, True):
            with self.subTest(earlier_lost=earlier_lost), \
                    patch.object(advmaps, "earlier_champions", return_value=[EARLIER]) as earlier:
                self.setUp()
                self.earlier_search(lose=earlier_lost)
                regress, _ = self.propose()
                self.play(regress, losing={"r-snake_trap"})
                lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
                earlier.assert_called_once_with(CHAMPION, driver.REPO)
                alerts = driver.STATE / "host-alerts.log"
                alerted = "ADVMAPS REGRESSION" in (alerts.read_text() if alerts.exists() else "")
                self.assertEqual(alerted, not earlier_lost)
                self.assertEqual(len(lines), 1)  # the run log gets it either way
                self.assertEqual(self.record["regress"]["r-snake_trap"]["lost"], True)
                self.assertEqual(self.record["regress"]["r-snake_trap"].get("lost_before"),
                                 EARLIER if earlier_lost else None)
                self.assertEqual("not alerted" in lines[0], earlier_lost)

    # The newest earlier champion with a finished record decides: one that won alerts though an older one lost, and
    # one whose regression games never finished is passed over for the one before it.
    def test_the_newest_earlier_champion_with_a_finished_record_decides(self) -> None:
        older = "8a" * 20
        for newest_finished, alerted in ((True, True), (False, False)):
            with self.subTest(newest_finished=newest_finished), \
                    patch.object(advmaps, "earlier_champions", return_value=[EARLIER, older]):
                self.setUp()
                self.earlier_search(lose=False, finished=newest_finished)  # won, or cut short after side A
                self.earlier_search(lose=True, champion=older)
                regress, _ = self.propose()
                self.play(regress, losing={"r-snake_trap"})
                advmaps.decide(driver.STATE, self.record, GEN, self.folder)
                alerts = driver.STATE / "host-alerts.log"
                self.assertEqual("ADVMAPS REGRESSION" in (alerts.read_text() if alerts.exists() else ""), alerted)
                self.assertEqual(self.record["regress"]["r-snake_trap"].get("lost_before"),
                                 None if newest_finished else older)

    def test_the_earlier_champions_come_from_the_version_tags_newest_first(self) -> None:
        tags = "v0001 t1 c1\nv0002 t2 c2\nv0003 c3\nvnext t4 c4\n"  # annotated, annotated, lightweight, not a version
        done = subprocess.CompletedProcess([], 0, stdout=tags, stderr="")
        with patch.object(advmaps.subprocess, "run", return_value=done):
            self.assertEqual(advmaps.earlier_champions("c3", driver.REPO), ["c2", "c1"])
            self.assertEqual(advmaps.earlier_champions("c1", driver.REPO), [])
            self.assertEqual(advmaps.earlier_champions("untagged", driver.REPO), [])

    def test_a_find_luck_could_give_is_kept_but_not_alerted(self) -> None:
        self.play(self.propose()[0])
        seeds, _ = self.propose()
        self.play(seeds, losing={"s001"})
        kids, _ = self.propose()
        self.play(kids, losing={k["name"] for k in kids[:advmaps.REPRODUCE]})
        lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(list(self.record["finds"]), ["s001"])
        self.assertGreater(self.record["finds"]["s001"]["p"], advmaps.chance.TRIGGER_P)
        self.assertIn("ADVMAPS FIND", lines[0])  # printed to the run log
        alerts = driver.STATE / "host-alerts.log"
        self.assertNotIn("ADVMAPS FIND", alerts.read_text() if alerts.exists() else "")
        advmaps.save(driver.STATE, self.record)
        self.assertTrue(any("(suggestive only, not alerted)" in line for line in advmaps.stats_lines(driver.STATE)))

    def test_the_chance_of_a_find_is_the_binomial_tail_at_the_seed_pools_worst_rate_times_the_maps(self) -> None:
        maps = [{"name": f"s{i:03d}", "stage": "seed"} for i in range(40)]
        played = {m["name"]: [row(m["name"], "seed", s, "b" if m["name"] == "s000" and s == "B" else "a")
                              for s in "AB"] for m in maps}
        rate = advmaps.chance.rate_ci(1, 40)[1]
        p, seen, looks = advmaps.find_chance(maps, played, 5, 6)
        self.assertEqual((seen, looks, advmaps.groups()), (rate, 40, 1 + len(driver.ROTATION)))
        tail = sum(advmaps.math.comb(6, k) * rate ** k * (1 - rate) ** (6 - k) for k in range(5, 7))
        self.assertAlmostEqual(p, advmaps.groups() * 40 * tail)  # Bonferroni over the maps and the opponents
        # At 1 lost of 40 seed maps (at most 12.9% by its interval) over 40 maps and 4 opponents: 5 of 6 alerts (p
        # 0.03), 4 of 6 may be luck
        self.assertLessEqual(p, advmaps.chance.TRIGGER_P)
        self.assertGreater(advmaps.find_chance(maps, played, 4, 6)[0], advmaps.chance.TRIGGER_P)
        self.assertAlmostEqual(advmaps.find_chance(maps, played, 6, 6)[0], advmaps.groups() * 40 * rate ** 6)
        self.assertEqual(advmaps.find_chance(maps, played, 6, 6, extra=10)[2], 50)  # maps about to be listed

    def test_a_map_committed_after_the_search_began_is_played_next(self) -> None:
        self.play(self.propose()[0])
        seeds, _ = self.propose()
        self.play(seeds)
        shutil.copy(MAPS / "community/unsw.map", self.adversarial / "unsw.map")
        regress, why = self.propose()
        self.assertEqual(([m["name"] for m in regress], why), (["r-unsw"], None))
        self.play(regress, losing={"r-unsw"})
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertTrue(self.record["regress"]["r-unsw"]["lost"])
        self.assertEqual(advmaps.new_regressions(self.folder), [])
        advmaps.save(driver.STATE, self.record)
        self.assertTrue(any(line.startswith("- maps/adversarial/unsw.map: won 1 of 2")
                            for line in advmaps.stats_lines(driver.STATE)))

    def test_a_loss_that_does_not_reproduce_is_a_fluke_and_its_replays_go(self) -> None:
        self.settings["seed_maps"] = 2
        self.propose()  # the regression map
        self.play(advmaps.listing(self.folder))
        seeds, _ = self.propose()
        self.play(seeds, losing={"s000"})
        (self.folder / "kept").mkdir()
        (self.folder / "kept" / "s000-aB.replay").write_bytes(b"replay")
        kids, _ = self.propose()
        self.play(kids[:advmaps.REPRODUCE - 1], losing={k["name"] for k in kids})
        self.play(kids[advmaps.REPRODUCE - 1:])
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(self.record["flukes"], {"s000": f"lost on {advmaps.REPRODUCE - 1} of 6 perturbations"})
        self.assertFalse((self.folder / "kept" / "s000-aB.replay").exists())
        climb, _ = self.propose()  # a fluke may still be a parent: it is among the worst maps
        self.assertIn("s000", {m["parent"] for m in climb})

    def test_the_climb_mutates_the_worst_maps_and_stops_at_its_limits(self) -> None:
        shutil.rmtree(self.adversarial)
        self.adversarial.mkdir()
        seeds, _ = self.propose()
        for m in seeds:  # s003 closest to a loss, then s002
            alive = {"s003": 1, "s002": 3}.get(m["name"], 12)
            self.play([m], alive=alive)
        climb, _ = self.propose()
        # Each of the KEEP worst gets MUTANTS a round, so a round keeps the machines busier (29 Sep: at one each it
        # averaged 3.4 games in flight).
        self.assertEqual([(m["parent"], m["round"]) for m in climb],
                         [("s003", 1)] * advmaps.MUTANTS + [("s002", 1)] * advmaps.MUTANTS)
        for m in climb:
            parent = GEN.parse((self.folder / "maps" / f"{m['parent']}.map").read_text())
            self.assertIsNone(advmaps.valid(GEN, GEN.parse(Path(m["path"]).read_text()), parent))
        self.play(climb, alive=12)
        self.settings["climb_rounds"] = 1
        self.assertEqual(self.propose(), ([], "climb limit"))
        self.settings.update(climb_rounds=None, games=len(advmaps.results(self.folder)))
        self.assertEqual(self.propose(), ([], "budget"))

    def test_the_search_ends_at_max_finds(self) -> None:
        self.play(self.propose()[0])  # the regression maps always play
        self.record["finds"] = {f"s{i:03d}": {} for i in range(advmaps.MAX_FINDS)}
        self.assertEqual(self.propose(), ([], "finds"))

    # The first cap spent on 10 seed maps and one climb round, as run leaves it before the idle stages.
    def spend_first_cap(self) -> dict:
        shutil.rmtree(self.adversarial)
        self.adversarial.mkdir()
        self.settings.update(seed_maps=10, games=24)
        seeds, _ = self.propose()
        self.play(seeds)
        climb, _ = self.propose()
        self.play(climb)
        self.assertEqual(self.propose(), ([], "budget"))
        return {**self.settings, "games": 400, "idle": True, "strong": ("swarm",)}

    def strong(self, idle: dict) -> tuple[list[dict], str | None]:
        return advmaps.propose(GEN, self.folder, CHAMPION, self.record, idle)

    def test_the_strong_pass_plays_the_seed_pool_then_the_worst_climb_maps(self) -> None:
        idle = self.spend_first_cap()
        pool, why = self.strong(idle)
        self.assertIsNone(why)
        self.assertEqual([m["name"] for m in pool], [f"swarm-s{i:03d}" for i in range(10)])
        self.assertEqual({(m["stage"], m["opponent"], m["pool"]) for m in pool}, {("strong", "swarm", True)})
        self.assertEqual(Path(pool[0]["path"]).read_text(), (self.folder / "maps" / "s000.map").read_text())
        # Lost on one side only: against a strong opponent that is usual, not a loss (lost)
        self.play(pool, losing={"swarm-s001"})
        worst, _ = self.strong(idle)  # the pool in and no loss in it: a find is possible, so the worst maps play
        self.assertEqual([(m["name"], m["pool"]) for m in worst], [("swarm-c000", False), ("swarm-c001", False)])
        # Nothing judged while the pass is only partly in: its opponent's loss rate isn't known yet.
        self.play(worst[:1], both={"swarm-c000"})
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(self.record["flukes"], {})
        self.assertFalse(advmaps.pass_played(advmaps.listing(self.folder),
                                             advmaps.by_map(advmaps.results(self.folder)), "swarm"))
        self.play(worst[1:])
        proposed, _ = self.strong(idle)
        kids = [m for m in proposed if m["stage"] == "perturb"]
        self.assertEqual({m["parent"] for m in kids}, {"swarm-c000"})
        self.assertEqual(len(kids), advmaps.STRONG_PERTURBATIONS)
        self.assertEqual({m["stage"] for m in proposed if m not in kids}, {"climb"})  # beside them (explore)
        self.play(proposed)
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        climb, _ = self.strong(idle)  # the pass judged: the starter's climb goes on
        self.assertEqual({m["stage"] for m in climb}, {"climb"})

    def test_a_strong_opponents_usual_losses_are_not_perturbed_nor_its_worst_maps_played(self) -> None:
        idle = self.spend_first_cap()
        pool, _ = self.strong(idle)
        lost = {f"swarm-s00{i}" for i in range(4)}  # 4 of the 10 pool maps, both sides: its usual rate is high
        self.play(pool, both=lost)
        self.assertEqual({m["stage"] for m in self.strong(idle)[0]}, {"climb"})  # no worst maps: they'd find nothing
        skipped = [m for m in advmaps.listing(self.folder) if m.get("skipped")]
        self.assertEqual([(m["name"], m["unmade"]) for m in skipped], [("swarm-worst", True)])
        self.assertIn("even 10 of 10 perturbations lost would be p", skipped[0]["skipped"])
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(set(self.record["flukes"]), lost)
        self.assertTrue(all(why.startswith("not perturbed: even 10 of 10 lost would be p")
                            for why in self.record["flukes"].values()))
        alerts = driver.STATE / "host-alerts.log"
        self.assertFalse(alerts.exists() and "ADVMAPS" in alerts.read_text())
        advmaps.save(driver.STATE, self.record)
        line = next(line for line in advmaps.stats_lines(driver.STATE) if "strong pass against swarm" in line)
        self.assertTrue(line.startswith("- strong pass against swarm: 10 of 10 maps played, lost or drew both sides 4 "
                                        "(the seed pool 4 of 10); of those judged, 4 not perturbed (no find possible "
                                        "at its own loss rate) and 0 perturbed without a find; the worst climb maps "
                                        "not played: even 10 of 10"), line)

    def test_a_strong_find_must_beat_its_opponents_own_loss_rate(self) -> None:
        idle = self.spend_first_cap()
        pool, _ = self.strong(idle)
        self.play(pool)  # the pool all won: losses there are rare
        worst, _ = self.strong(idle)
        self.play(worst, both={"swarm-c000", "swarm-c001"})
        kids, _ = self.strong(idle)
        self.assertEqual({(m["parent"], m["opponent"]) for m in kids}, {("swarm-c000", "swarm"), ("swarm-c001", "swarm")})
        found, usual = ([k for k in kids if k["parent"] == parent] for parent in ("swarm-c000", "swarm-c001"))
        self.assertEqual(len(found), advmaps.STRONG_PERTURBATIONS)
        self.play(found, both={k["name"] for k in found})
        self.play(usual, both={k["name"] for k in usual[:advmaps.REPRODUCE]})
        lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(list(self.record["finds"]), ["swarm-c000"])
        # 3 of 10 reproduced, enough against the starter, but no worse than swarm's usual rate
        self.assertIn("no worse than usual", self.record["flukes"]["swarm-c001"])
        self.assertIn("lost or drew both sides against swarm on swarm-c000", lines[0])
        self.assertIn("luck alone at swarm's own loss rate on the seed pool", lines[0])
        self.assertIn("ADVMAPS FIND", (driver.STATE / "host-alerts.log").read_text())
        find = Path(self.record["finds"]["swarm-c000"]["folder"])
        self.assertEqual((find.name, json.loads((find / "find.json").read_text())["opponent"]), ("swarm-c000", "swarm"))
        self.assertLessEqual(self.record["finds"]["swarm-c000"]["p"], advmaps.chance.TRIGGER_P)

    def test_a_strong_pass_the_budget_cannot_hold_is_skipped_whole(self) -> None:
        idle = {**self.spend_first_cap(), "games": 24 + 2 * (10 + advmaps.STRONG_WORST) - 2}  # a map short
        climb, _ = self.strong(idle)
        self.assertEqual({m["stage"] for m in climb}, {"climb"})
        self.assertEqual([m["name"] for m in advmaps.listing(self.folder) if m["stage"] == "strong"], ["swarm-pass"])
        advmaps.save(driver.STATE, self.record)
        self.assertTrue(any("strong pass against swarm: 0 of 0 maps played" in line and "not played: " in line
                            for line in advmaps.stats_lines(driver.STATE)))

    def test_a_regression_map_plays_the_opponent_its_readme_row_names(self) -> None:
        self.assertEqual(advmaps.regression_opponents(MAPS / "adversarial"),  # the real README: its Opponent cells
                         {**{p.name: "starter" for p in (MAPS / "adversarial").glob("*.map")},
                          "adv_cfe3b39_c067.map": "swarm"})
        shutil.copy(MAPS / "community/unsw.map", self.adversarial / "unsw.map")
        shutil.copy(MAPS / "ladder/devil.map", self.adversarial / "devil.map")
        (self.adversarial / "README.md").write_text(
            "| File | What it is | Opponent |\n|---|---|---|\n| snake_trap.map | a find | `breeder` |\n"
            "| unsw.map | a find | nosuchbot |\n| devil.map | a find | |\n")
        regress, _ = self.propose()
        self.assertEqual([(m["name"], m["opponent"]) for m in regress], [("r-devil", "starter"),
                                                                         ("r-snake_trap", "breeder")])
        # Lost on one side against the breeder: usual, no alert; the unknown opponent's map: one error row and alert
        self.play(regress, losing={"r-snake_trap"})
        lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(len(lines), 1)
        self.assertIn("could not play against nosuchbot on maps/adversarial/unsw.map (error: README opponent "
                      "'nosuchbot' is not in referee/gauntlet.toml)", lines[0])
        self.assertEqual(advmaps.decide(driver.STATE, self.record, GEN, self.folder), [])  # once
        self.assertEqual(self.propose()[0][0]["stage"], "seed")  # the rest of the search goes on
        self.assertFalse(self.record["regress"]["r-snake_trap"]["lost"])
        shutil.copy(MAPS / "ladder/trophy.map", self.adversarial / "trophy.map")
        (self.adversarial / "README.md").write_text(
            "| File | Opponent |\n|---|---|\n| snake_trap.map | breeder |\n| unsw.map | nosuchbot |\n"
            "| trophy.map | breeder |\n")
        regress, _ = self.propose()
        self.play(regress, both={"r-trophy"})  # both sides against the breeder: a regression
        self.assertIn("lost or drew both sides against breeder on maps/adversarial/trophy.map",
                      advmaps.decide(driver.STATE, self.record, GEN, self.folder)[0])
        (self.adversarial / "README.md").write_text("| File | What it is |\n|---|---|\n| snake_trap.map | a find |\n")
        self.assertEqual(advmaps.regression_opponents(self.adversarial), {})  # no Opponent column: the starter

    def test_a_regression_map_plays_again_when_its_readme_row_names_another_opponent(self) -> None:
        # An unknown name left its map an error row for the champion for good: fixing the README never replayed it.
        shutil.copy(MAPS / "community/unsw.map", self.adversarial / "unsw.map")
        readme = self.adversarial / "README.md"
        readme.write_text("| File | Opponent |\n|---|---|\n| unsw.map | nosuchbot |\n")
        regress, _ = self.propose()
        self.assertEqual([m["name"] for m in regress], ["r-snake_trap"])  # unsw.map: an error row
        self.play(regress)
        advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(advmaps.new_regressions(self.folder), [])
        readme.write_text("| File | Opponent |\n|---|---|\n| unsw.map | breeder |\n")
        self.assertEqual(advmaps.new_regressions(self.folder), [self.adversarial / "unsw.map"])  # tick reopens for it
        regress, _ = self.propose()
        self.assertEqual([(m["name"], m["opponent"]) for m in regress], [("r-unsw-vs-breeder", "breeder")])
        self.play(regress, both={"r-unsw-vs-breeder"})
        lines = advmaps.decide(driver.STATE, self.record, GEN, self.folder)
        self.assertEqual(len(lines), 1)
        self.assertIn("lost or drew both sides against breeder on maps/adversarial/unsw.map", lines[0])
        self.assertEqual(advmaps.new_regressions(self.folder), [])
        advmaps.save(driver.STATE, self.record)
        rows = [line for line in advmaps.stats_lines(driver.STATE) if line.startswith("- maps/adversarial/unsw.map")]
        self.assertEqual(len(rows), 1)  # a line a map: its latest opponent's
        self.assertTrue(rows[0].startswith("- maps/adversarial/unsw.map against breeder: won 0 of 2"), rows)

    def test_tidy_keeps_the_worst_climb_maps_for_a_strong_pass_still_to_play(self) -> None:
        # 1eae05f's search, tidied before its idle stages, kept KEEP maps: its reopened strong pass could copy only
        # those of its STRONG_WORST worst climb maps (strong_pass reads only files still there).
        for i in range(advmaps.STRONG_WORST + 10):  # c000 the worst: the longest game
            m = advmaps.write_map(self.folder, {"name": f"c{i:03d}", "stage": "climb"}, "x")
            self.play([m], rounds=490 - 10 * i, end="elimination")
        left = lambda: sorted(p.stem for p in (self.folder / "maps").glob("*.map"))
        advmaps.tidy(self.folder, {"champion": CHAMPION})
        self.assertEqual(left(), [f"c{i:03d}" for i in range(advmaps.STRONG_WORST)])
        advmaps.tidy(self.folder, {"champion": CHAMPION, "base_done": "budget"})  # its idle stages played: KEEP
        self.assertEqual(left(), [f"c{i:03d}" for i in range(advmaps.KEEP)])

    def test_the_perturbation_reserve_holds_max_finds_maps_at_either_count(self) -> None:
        # Sized for six perturbations a map, it held only 3 strong maps' ten past the cap.
        self.assertGreaterEqual(advmaps.PERTURB_RESERVE, advmaps.MAX_FINDS * 2 * advmaps.STRONG_PERTURBATIONS)
        self.assertGreaterEqual(advmaps.PERTURB_RESERVE, advmaps.MAX_FINDS * 2 * advmaps.PERTURBATIONS)

    def test_stats_list_the_regression_record_a_line_a_map_and_the_finds(self) -> None:
        shutil.copy(MAPS / "community/unsw.map", self.adversarial / "unsw.map")
        regress, _ = self.propose()
        self.play(regress, losing={"r-unsw"}, rounds=63, end="elimination", alive=0)
        advmaps.save(driver.STATE, {**self.record, "finds": {"s009": {"origin": "gen trap 7", "deaths": "hitWall 3",
                                                                        "features": {"flags": ["maze"]},
                                                                        "folder": "/f"}}})
        lines = advmaps.stats_lines(driver.STATE)
        self.assertIn("- maps/adversarial/snake_trap.map: won 2 of 2 (A won r63 by elimination, 0 of ours left; "
                      "B won r63 by elimination, 0 of ours left)", lines)
        self.assertIn("- maps/adversarial/unsw.map: won 1 of 2 (A won r63 by elimination, 0 of ours left; "
                      "B lost r63 by elimination, 0 of ours left)", lines)
        self.assertTrue(any(line.startswith("- find s009: gen trap 7; deaths hitWall 3; shape maze") for line in lines))
        import meta
        self.assertIn("## Adversarial maps, champion", meta.stats_markdown(driver.STATE / "results.jsonl"))
        with patch.object(advmaps, "stats_lines", side_effect=ValueError("bad row")):
            text = meta.stats_markdown(driver.STATE / "results.jsonl")
        self.assertIn("## Adversarial maps: the report failed (ValueError: bad row)", text)
        self.assertIn("## Acceptance by category", text)  # the rest of stats.md still written


class Run(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        driver.IDEAS.write_text("")
        self.adversarial = driver.STATE / "adv-maps"
        self.adversarial.mkdir()
        where = patch.object(advmaps, "adversarial_dir", return_value=self.adversarial)
        where.start()
        self.addCleanup(where.stop)
        self.batches: list[list[str]] = []
        self.busy: list[str | None] = []  # run's busy() answers in turn, then None
        self.wants: list[str | None] = []  # wanted()'s answers in turn, then None
        self.plans: list[dict] = []
        self.texts: dict[str, str] = {}  # each map played's file, as the referee read it
        advmaps.save(driver.STATE, {"champion": CHAMPION, "toolchain": {"python": "/usr/bin/true"}, "pid": 1})
        self.folder = advmaps.folder_of(driver.STATE, CHAMPION)
        self.folder.mkdir(parents=True)

    # The fake referee: s001 and three of its perturbations lose as B, s002 loses once (a fluke); the rest win.
    def referee(self, script: str, args: list[str], timeout: float = 0, yielding: bool | str = False):
        plan = json.loads(Path(args[1]).read_text())
        # Past the first cap at the spare share: only what the other idle fillers leave (native.spare_share).
        self.assertEqual((yielding, driver.RUNTIME_PYTHON.get()), ("spare" if plan["idle"] else "filler", "/usr/bin/true"))
        self.batches.append([m["name"] for m in plan["maps"]])
        self.plans.append(plan)
        self.texts.update((m["name"], Path(m["path"]).read_text()) for m in plan["maps"])
        losing = {"s001", "s001-p0", "s001-p1", "s001-p2", "s002"}
        with open(plan["out"], "a") as out:
            for m in plan["maps"]:
                for side in "AB":
                    out.write(json.dumps(row(m["name"], m["stage"], side,
                                             "b" if m["name"] in losing and side == "B" else "a")) + "\n")
        return subprocess.CompletedProcess([], 0, "", "")

    def search(self, **settings) -> dict:
        with patch.object(driver, "gauntlet_bot", side_effect=lambda spec, into: into / spec["name"]), \
                patch.object(driver, "rotation_bots", side_effect=lambda names: [(n, Path(f"/b/{n}")) for n in names]), \
                patch.object(driver, "referee", side_effect=self.referee), \
                patch("sweep.busy", side_effect=lambda *a: self.busy.pop(0) if self.busy else None), \
                patch.object(advmaps, "wanted", side_effect=lambda *a: self.wants.pop(0) if self.wants else None):
            advmaps.run(driver.REPO, driver.STATE, {"seed_maps": 6, "keep": 2, "games": 40, "idle_games": 40,
                                                    **settings})
        self.assertIsNone(driver.RUNTIME_PYTHON.get())  # the runtime is set for the search's games only
        return advmaps.read(driver.STATE)

    def test_it_finds_rejects_the_fluke_finishes_within_budget_and_tidies(self) -> None:
        record = self.search()
        self.assertEqual((list(record["finds"]), list(record["flukes"])), (["s001"], ["s002"]))
        self.assertEqual((record["pid"], record["finished"]), (None, "budget"))
        rows = advmaps.results(self.folder)
        self.assertLessEqual(advmaps.search_games(advmaps.listing(self.folder), rows), 40 + advmaps.PERTURB_RESERVE)
        left = sorted(p.stem for p in (self.folder / "maps").glob("*.map"))
        self.assertLessEqual(len(left), 1 + advmaps.KEEP)  # the find and the worst maps
        self.assertIn("s001", left)
        self.assertFalse((self.folder / "kept").exists() or (self.folder / "replays").exists())
        self.assertTrue((self.folder / "finds" / "s001" / "s001.map").exists())

    @patch.object(advmaps, "BATCH_MAPS", 3)
    def test_past_the_first_cap_it_waits_in_place_for_idle_machines_then_plays_bigger_calls(self) -> None:
        # Until 30 Sep it exited there and waited for a later upkeep tick; now it waits in its run (sweep.wait_idle),
        # its pid kept and its record paused, and plays on once nothing else wants the machines.
        self.wants = [sweep.ON_HOSTS, sweep.ON_HOSTS]
        waits = []
        with patch("sweep.time.sleep", lambda s: waits.append(advmaps.read(driver.STATE))):
            record = self.search(idle_games=200, strong=("swarm", "rammer"))
        self.assertEqual([(r["base_done"], r["pid"], r["paused"]) for r in waits], [("budget", 1, sweep.ON_HOSTS)])
        self.assertEqual((record["finished"], record["paused"]), ("budget", None))
        first = sum(plan["idle"] is None for plan in self.plans)
        maps = advmaps.listing(self.folder)
        self.assertLessEqual(advmaps.search_games(maps, advmaps.results(self.folder)), 200 + advmaps.PERTURB_RESERVE)
        opponent = {m["name"]: advmaps.opponent_of(m) for m in maps}
        self.assertEqual({opponent[m["name"]] for m in maps if m["stage"] == "strong"}, {"swarm", "rammer"})
        # Each map's opponent's bot in its call, and past the cap bigger calls (IDLE_BATCH_MAPS: on 29 Sep 22:05-22:18
        # 20-map calls kept 14 of 72 slots busy) stopping for wanted().
        for plan in self.plans:
            self.assertEqual({opponent[m["name"]]: f"/b/{opponent[m['name']]}" for m in plan["maps"]}, plan["opponents"])
        self.assertLessEqual(max(len(plan["maps"]) for plan in self.plans[:first]), 3)
        self.assertGreater(max(len(plan["maps"]) for plan in self.plans[first:]), 3)
        self.assertTrue(all(plan["idle"] == str(driver.STATE) for plan in self.plans[first:]))
        self.assertTrue(all(plan["idle"] is None for plan in self.plans[:first]))

    def test_a_tidied_search_reopened_plays_on_without_reading_the_files_tidy_deleted(self) -> None:
        # As 1eae05f's search on 2026-09-27: finished at its first cap by code with no idle stages (no base_done),
        # and tidied, so most seed and climb map files are gone.
        record = self.search()
        self.assertEqual((record["finished"], record.get("base_done")), ("budget", None))
        maps = advmaps.listing(self.folder)
        gone = {m["name"] for m in maps if m["stage"] == "seed" and not Path(m["path"]).exists()}
        self.assertTrue(gone)
        # Reopened as tick reopens it (for a map committed since, and for its idle stages): the regression map, then
        # the strong pass on the seed pool made again, and the climb on the maps still there.
        shutil.copy(MAPS / "community/unsw.map", self.adversarial / "unsw.map")
        advmaps.save(driver.STATE, {**{k: v for k, v in record.items() if k != "finished_at"}, "pid": 1})
        first = len(self.batches)
        record = self.search(idle_games=200, strong=("swarm",))
        self.assertEqual((record["finished"], record["base_done"], record.get("error")), ("budget", "budget", None))
        self.assertEqual(self.batches[first], ["r-unsw"])
        maps = {m["name"]: m for m in advmaps.listing(self.folder)}
        for name in gone:
            copy = maps[f"swarm-{name}"]
            self.assertTrue(copy["pool"] and copy["origin"].endswith(", made again) against swarm"), copy)
            self.assertEqual(self.texts[f"swarm-{name}"], self.texts[name])  # the very map the starter played
        self.assertTrue(any(m["stage"] == "climb" and m["round"] > 1 for m in maps.values()))

    def test_a_deleted_seed_map_is_made_again_from_its_origin_whatever_styles_gen_py_gains(self) -> None:
        # A knob's map and a macro one: a style appended to STYLES shifts seed_sources' round-robin (as deadend and
        # fortress did), so the index alone would make another map under the same name.
        macro = advmaps.seed_sources(GEN).index(("style", "macro"))
        for i in (macro, len(GEN.STYLES) + 1):
            text, origin = advmaps.seed_map(GEN, CHAMPION, i)
            gone = {"name": f"s{i:03d}", "stage": "seed", "origin": origin, "path": str(self.folder / "gone.map")}
            with patch.object(GEN, "STYLES", GEN.STYLES + ("added",)):
                made, = advmaps.strong_copies(GEN, self.folder, CHAMPION, "swarm", [gone], True)
            self.assertEqual(Path(made["path"]).read_text(), text, origin)
            self.assertIn(f"({origin}, made again)", made["origin"])

    def test_a_pause_between_batches_resumes_where_it_stopped(self) -> None:
        self.busy = [None, "an evaluation is running"]
        record = self.search()
        self.assertEqual((len(self.batches), record["paused"], record["pid"]), (1, "an evaluation is running", None))
        self.assertNotIn("finished_at", record)
        first = list(self.batches[0])
        record = self.search()
        self.assertIn("finished_at", record)
        played = [name for batch in self.batches for name in batch]
        self.assertEqual(len(played), len(set(played)), "no map played twice")
        self.assertEqual(played[:len(first)], first)

    def test_a_manual_runs_climb_limit_pauses_rather_than_finishes(self) -> None:
        record = self.search(climb_rounds=0, games=600)
        self.assertEqual((record["paused"], record.get("finished_at")), ("the run's climb limit", None))

    def test_a_new_champion_ends_the_run_and_a_failed_batch_is_recorded(self) -> None:
        with patch.object(driver, "referee", side_effect=lambda *a, **k: subprocess.CompletedProcess([], 2, "", "boom")), \
                patch.object(driver, "gauntlet_bot", side_effect=lambda spec, into: into / spec["name"]), \
                patch.object(driver, "rotation_bots", side_effect=lambda names: [(n, Path(f"/b/{n}")) for n in names]), \
                patch("sweep.busy", return_value=None):
            advmaps.run(driver.REPO, driver.STATE, {"seed_maps": 2})
        record = advmaps.read(driver.STATE)
        self.assertEqual((record["failures"], record["pid"]), (1, None))
        self.assertIn("play exited 2", record["error"])
        advmaps.save(driver.STATE, {"champion": "0" * 40})
        advmaps.update(driver.STATE, CHAMPION, pid=5)  # an old run's update never touches the new record
        self.assertEqual(advmaps.read(driver.STATE), {"champion": "0" * 40})


# A referee GameResult like the captured one, on `game_map` and `side`, won by `winner`, or failed with `error`.
def game(game_map: str, side: str, winner: str | None, error: str | None = None) -> referee.GameResult:
    stats = {key: referee.BotStats(**GAME[key]) for key in ("a", "b")}
    return referee.GameResult(**{**GAME, **stats, "map": game_map, "a_side": side, "winner": winner, "error": error})


class Play(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.stops: list[str | None] = []
        self.asked = 0  # sweep.busy calls
        self.maps = []
        for name, source in (("s000", SAMPLES[2]), ("s001", SAMPLES[1]), ("s002", SAMPLES[3])):
            shutil.copy(source, self.folder / f"{name}.map")
            self.maps.append({"name": name, "stage": "seed", "path": str(self.folder / f"{name}.map")})

    def play(self, outcome, old: bool = False) -> int:
        starter = str(self.folder / "starter")
        # old: a plan written before batches mixed opponents, one "opponent" for every map
        plan = {"champion": str(self.folder / "champion"),
                **({"opponent": starter} if old else {"opponents": {"starter": starter}}),
                "out": str(self.folder / "results.jsonl"), "maps": self.maps, "replays": str(self.folder / "replays"),
                "kept": str(self.folder / "kept"), "status": str(self.folder / "status.json"), "repo": ".",
                "commit": CHAMPION}
        (self.folder / "plan.json").write_text(json.dumps(plan))

        def stream(jobs, bots, workers, sandbox, replays):
            for index, job in enumerate(jobs):
                (replays / f"{index:04d}-{job.map.stem}-a{job.a_side}.replay").write_bytes(b"r")
                yield outcome(job)

        with patch.object(unswbc.run, "_resolve", return_value=(["bin"], None, "c/c++")), \
                patch.object(referee, "stream_games", stream), \
                patch.object(advmaps, "tally", return_value={"deaths": {"hitWall": 1}, "wall_dead_end": 1}), \
                patch("sweep.busy", side_effect=self.busy):
            return advmaps.play(self.folder / "plan.json")

    def busy(self, *args) -> str | None:
        self.asked += 1
        return self.stops.pop(0) if self.stops else None

    def rows(self) -> list[dict]:
        return advmaps.results(self.folder)

    def test_each_map_gets_its_two_rows_and_a_lost_maps_replays_are_kept(self) -> None:
        self.assertEqual(self.play(lambda job: game(str(job.map), job.a_side,
                                                    "b" if job.map.stem == "s001" and job.a_side == "B" else "a")), 0)
        rows = self.rows()
        self.assertEqual([(r["name"], r["a_side"], r["winner"]) for r in rows],
                         [("s000", "A", "a"), ("s000", "B", "a"), ("s001", "A", "a"), ("s001", "B", "b"),
                          ("s002", "A", "a"), ("s002", "B", "a")])
        self.assertEqual(set(rows[0]) - {"name", "stage", "starts"}, set(GAME))  # the referee's row, untallied: a sure win
        self.assertEqual(rows[0]["starts"], advmaps.starts(SAMPLES[2].read_text()))
        self.assertEqual(rows[3]["deaths"], {"hitWall": 1})  # a lost map's deaths are tallied
        self.assertEqual(sorted(p.name for p in (self.folder / "kept").iterdir()), ["s001-aA.replay", "s001-aB.replay"])

    def test_a_plan_from_before_mixed_opponents_still_plays(self) -> None:
        self.assertEqual(self.play(lambda job: game(str(job.map), job.a_side, "a"), old=True), 0)
        self.assertEqual(len(self.rows()), 6)

    def test_deaths_are_tallied_from_a_real_replay_in_a_process_of_their_own(self) -> None:
        # This process has loaded referee/replay.capnp (import run), as play's has; tally reads the replay in a
        # process of its own.
        replay = driver.REPO / "tools/testdata/trophy-155732.replay"
        every = {(x, y) for x in range(64) for y in range(64)}
        sides = {team: advmaps.tally(replay, team, every) for team in "AB"}
        self.assertGreater(sum(n for side in sides.values() for n in side["deaths"].values()), 0)
        for side in sides.values():
            self.assertEqual(side["wall_dead_end"], side["deaths"].get("hitWall", 0))  # every tile counts as a trap
            self.assertLessEqual(set(side["deaths"]), {"hitWall", "hitSelf", "hitOtherBody", "hitHeadToHead",
                                                       "noValidAction"})
        self.assertEqual(advmaps.tally(replay, "A", set())["wall_dead_end"], 0)

    def test_it_stops_between_maps_and_an_unplayable_game_is_one_error_row(self) -> None:
        self.stops = [None, None, "an evaluation is playing on the hosts"]  # before building, s000, then s001
        failing = lambda job: game(str(job.map), job.a_side, None, "timed out") if job.a_side == "B" \
            else game(str(job.map), job.a_side, "a")  # noqa: E731
        self.assertEqual(self.play(failing), advmaps.PAUSED)
        self.assertEqual(self.rows(), [{"name": "s000", "stage": "seed",
                                        "game_error": f"{self.maps[0]['path']} (B): timed out"}])
        self.assertEqual(self.asked, 3)  # before the build, then between built maps
        self.stops = ["a stop marker is set"]
        self.assertEqual(self.play(failing), advmaps.PAUSED)  # nothing built or played
        self.assertEqual(len(self.rows()), 1)


class Tick(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "todo"}) + "\n")
        champion = patch.object(advmaps.meta, "champion_commit", return_value=CHAMPION)
        champion.start()
        self.addCleanup(champion.stop)

    def test_it_launches_only_while_idle_and_retries_a_dead_run_after_an_hour(self) -> None:
        now = time.time()
        with patch.object(advmaps, "start", return_value=4242) as start:
            driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": now}))
            self.assertIsNone(advmaps.tick(Path("."), driver.STATE, now))
            self.assertEqual((start.call_count, advmaps.read(driver.STATE)), (0, {"champion": CHAMPION}))
            driver.EVALUATING.unlink()
            self.assertIn("launched in the background", advmaps.tick(Path("."), driver.STATE, now))
            record = advmaps.read(driver.STATE)
            self.assertEqual((record["pid"], record["started_at"], record["idle_at"]), (4242, now, now))
            with patch.object(advmaps.meta, "alive", return_value=True):
                self.assertIsNone(advmaps.tick(Path("."), driver.STATE, now + 60))
            with patch.object(advmaps.meta, "alive", return_value=False):
                self.assertIsNone(advmaps.tick(Path("."), driver.STATE, now + 120))
                self.assertEqual((advmaps.read(driver.STATE)["failures"], start.call_count), (1, 1))
                self.assertIn("launched", advmaps.tick(Path("."), driver.STATE, now + 120 + advmaps.RETRY_S))
            self.assertEqual(start.call_args.args[0], Path("."))  # the tick's own repo
            advmaps.update(driver.STATE, CHAMPION, pid=None, finished_at=now, base_done="budget")
            self.assertIsNone(advmaps.tick(Path("."), driver.STATE, now + 3 * advmaps.RETRY_S))
            self.assertEqual(start.call_count, 2)
            adversarial = driver.STATE / "adv-maps"  # a map committed after the search finished reopens it
            adversarial.mkdir()
            shutil.copy(MAPS / "community/unsw.map", adversarial / "unsw.map")
            with patch.object(advmaps, "adversarial_dir", return_value=adversarial), \
                    patch.object(advmaps.meta, "alive", return_value=False):
                self.assertIn("launched", advmaps.tick(Path("."), driver.STATE, now + 4 * advmaps.RETRY_S))
            self.assertEqual(start.call_count, 3)
            self.assertNotIn("finished_at", advmaps.read(driver.STATE))

    def test_past_the_first_cap_it_launches_only_when_nothing_else_wants_the_machines(self) -> None:
        now = time.time()
        advmaps.save(driver.STATE, {"champion": CHAMPION, "base_done": "budget", "paused": "x", "paused_at": 1.0})
        with patch.object(advmaps, "start", return_value=4242) as start:
            with patch.object(advmaps, "wanted", return_value="an evaluation is playing on the hosts"):
                self.assertIsNone(advmaps.tick(Path("."), driver.STATE, now))
            self.assertEqual(start.call_count, 0)
            record = advmaps.read(driver.STATE)  # the reason it waits now, not the one it paused for
            self.assertEqual((record["paused"], record["paused_at"]),
                             (f"past the first {advmaps.GAMES_CAP} games, idle machines only: an evaluation is playing on the hosts", now))
            with patch.object(advmaps, "wanted", return_value=None):
                self.assertIn("launched", advmaps.tick(Path("."), driver.STATE))
            self.assertIsNone(advmaps.read(driver.STATE)["paused"])

    def test_the_28_sep_search_plays_past_its_cap_beside_the_unfinished_prewarm(self) -> None:
        # advmaps.json at 01:20 on 2026-09-28: past its first cap (base_done), paused since 22:56 for the sweep, which
        # finished at 00:03, and still held for the prewarm, which kept about 20 of 56 slots busy until 01:30.
        import baselines
        import sweep
        advmaps.save(driver.STATE, {"champion": CHAMPION, "base_done": "finds", "pid": None, "paused_at": 1790513813.9,
                                    "paused": "past the first 600 games, idle machines only: the variant sweep of "
                                              "4e3da73 hasn't finished"})
        sweep.save(driver.STATE, {"champion": CHAMPION, "finished_at": 1790517780.0})
        baselines.save(driver.STATE, {"champion": CHAMPION, "done": ["rotation:50"], "pid": 4321})
        with patch.object(advmaps, "start", return_value=4242) as start:
            self.assertIn("launched", advmaps.tick(Path("."), driver.STATE))
        self.assertEqual(start.call_count, 1)
        self.assertIsNone(advmaps.read(driver.STATE)["paused"])

    def test_a_search_finished_before_its_idle_stages_is_reopened_for_them_only_when_nothing_else_wants_them(self):
        advmaps.save(driver.STATE, {"champion": CHAMPION, "finished_at": 1.0, "finished": "budget"})
        with patch.object(advmaps, "start", return_value=4242) as start:
            with patch.object(advmaps, "wanted", return_value="a tune is playing"):
                self.assertIsNone(advmaps.tick(Path("."), driver.STATE))
            self.assertEqual((start.call_count, advmaps.read(driver.STATE)["finished_at"]), (0, 1.0))
            with patch.object(advmaps, "wanted", return_value=None):
                self.assertIn("launched", advmaps.tick(Path("."), driver.STATE))
            self.assertNotIn("finished_at", advmaps.read(driver.STATE))
            advmaps.save(driver.STATE, {"champion": CHAMPION, "finished_at": 1.0, "base_done": "budget"})
            with patch.object(advmaps, "wanted", return_value=None):
                self.assertIsNone(advmaps.tick(Path("."), driver.STATE))  # its idle stages are done
            self.assertEqual(start.call_count, 1)

    def test_wanted_names_whatever_else_wants_the_machines(self) -> None:
        import baselines
        import sweep
        repo = Path(".")
        # The sweep and the prewarm unfinished don't hold it: its games there take only what they leave
        # (native.spare_share). Nor, since 29 Sep, a late screen or an official-map report, which keep their half of
        # each host while they ask (native.host_share), nor a tune.
        sweep.save(driver.STATE, {"champion": CHAMPION})
        baselines.save(driver.STATE, {"champion": "0" * 40, "finished_at": 1.0})  # an older champion's
        job = driver.STATE / "runs" / "E0001" / "official-job.json"
        job.parent.mkdir(parents=True)
        job.write_text(json.dumps({"pid": os.getpid()}))
        with patch.object(driver, "tuning_count", return_value=1):
            self.assertIsNone(advmaps.wanted(driver.STATE, repo, CHAMPION))
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": time.time()}))
        self.assertEqual(advmaps.wanted(driver.STATE, repo, CHAMPION), "an evaluation is playing on the hosts")

    def test_only_the_newest_champions_keep_their_bulky_files(self) -> None:
        for age, champion in enumerate(("c" * 40, "b" * 40, "a" * 40)):  # a the oldest
            folder = advmaps.folder_of(driver.STATE, champion)
            for part in ("bots", "maps", "finds/s001/replays"):
                (folder / part).mkdir(parents=True)
            (folder / "results.jsonl").write_text("{}\n")
            (folder / "finds/s001/s001.map").write_text("MAP")
            os.utime(folder, (time.time() - 3600 * age, time.time() - 3600 * age))
        advmaps.save(driver.STATE, {"champion": "c" * 40, "finished_at": 1.0})
        with patch.object(advmaps, "start", return_value=4242):
            advmaps.tick(Path("."), driver.STATE)
        kept = {f.name[0] for f in (driver.STATE / "advmaps").iterdir() if (f / "bots").exists()}
        self.assertEqual(kept, {"c"})  # the one just replaced (the new champion's has no bots yet)
        for champion in ("b" * 40, "a" * 40):
            folder = advmaps.folder_of(driver.STATE, champion)
            self.assertFalse((folder / "maps").exists() or (folder / "finds/s001/replays").exists())
            self.assertTrue((folder / "results.jsonl").exists() and (folder / "finds/s001/s001.map").exists())

    def test_a_manual_run_for_a_new_champion_tidies_the_old_search(self) -> None:
        old = advmaps.folder_of(driver.STATE, "0" * 40)
        (old / "kept").mkdir(parents=True)
        advmaps.save(driver.STATE, {"champion": "0" * 40, "paused": "the run's climb limit"})
        with patch("maintenance.toolchain", return_value={"python": "/usr/bin/true"}):
            advmaps.manual(Path("."), driver.STATE)
        self.assertFalse((old / "kept").exists())
        self.assertEqual((advmaps.read(driver.STATE)["champion"], advmaps.read(driver.STATE)["pid"]),
                         (CHAMPION, os.getpid()))

    def test_a_search_cut_short_by_a_new_champion_is_tidied(self) -> None:
        old = advmaps.folder_of(driver.STATE, "0" * 40)
        (old / "kept").mkdir(parents=True)
        (old / "kept" / "s001-aA.replay").write_bytes(b"replay")
        (old / "bots").mkdir()
        advmaps.save(driver.STATE, {"champion": "0" * 40, "paused": "an evaluation is running"})
        with patch.object(advmaps, "start", return_value=4242):
            advmaps.tick(Path("."), driver.STATE)
        self.assertFalse((old / "kept").exists() or (old / "bots").exists())
        self.assertEqual(advmaps.read(driver.STATE)["champion"], CHAMPION)

    def test_a_run_beside_evaluations_stops_only_for_a_stop_marker_or_a_new_champion(self) -> None:
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": time.time()}))
        self.assertEqual(advmaps.halt(Path("."), CHAMPION), "an evaluation is playing on the hosts")
        self.assertIsNone(advmaps.halt(Path("."), CHAMPION, beside=True))
        self.assertEqual(advmaps.halt(Path("."), "0" * 40, beside=True), "the champion moved")
        driver.STOP.write_text("")
        self.assertEqual(advmaps.halt(Path("."), CHAMPION, beside=True), "a stop marker is set")


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
        advmaps.save(self.state, record)
        return [alert.key for alert in health.adversarial_maps(self.state, self.repo, now)]

    # plan.json, rewritten each batch for play: torn, or over advmaps.PLAN_BOUND, alerts (health.plan_file).
    def test_a_broken_plan_alerts(self) -> None:
        started = {"champion": self.champion, "started_at": self.promoted}
        folder = advmaps.folder_of(self.state, self.champion)
        folder.mkdir(parents=True)
        (folder / "plan.json").write_text(json.dumps({"maps": ["m"] * advmaps.BATCH_MAPS}, indent=1) + "\n")
        self.assertEqual(self.check(started, self.promoted), [])
        (folder / "plan.json").write_text("[1, 2]")
        self.assertEqual(self.check(started, self.promoted), ["advmaps:plan"])
        (folder / "plan.json").write_text(json.dumps({"maps": ["x" * advmaps.PLAN_BOUND]}))
        self.assertEqual(self.check(started, self.promoted), ["advmaps:plan"])

    def test_failures_a_hung_run_unplayable_maps_and_a_day_old_champion_alert(self) -> None:
        started = {"champion": self.champion, "started_at": self.promoted}
        record = advmaps.failure(advmaps.failure(started, "boom", self.promoted), "RuntimeError: play exited 2",
                                 self.promoted)
        self.assertEqual(self.check(record, self.promoted), ["advmaps:failed"])
        running = {**started, "pid": 4242, "progress_at": self.promoted}
        self.assertEqual(self.check(running, self.promoted + advmaps.STALE - 60), [])
        self.assertEqual(self.check(running, self.promoted + advmaps.STALE + 60), ["advmaps:stale"])
        folder = advmaps.folder_of(self.state, self.champion)
        folder.mkdir(parents=True)
        lines = [row(f"s{i:03d}", "seed", s, "a") for i in range(8) for s in "AB"]
        lines += [{"name": f"e{i}", "stage": "seed", "game_error": "x (A): timed out"} for i in range(3)]
        (folder / "results.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
        self.assertEqual(self.check(started, self.promoted), ["advmaps:errors"])
        (folder / "results.jsonl").unlink()
        later = self.promoted + health.ADVMAPS_GRACE + 60
        self.assertEqual(self.check({"champion": self.champion}, later), [])  # never idle: the queue kept them busy
        self.assertEqual(self.check({"champion": self.champion, "idle_at": self.promoted + 600}, later),
                         ["advmaps:unstarted"])
        self.assertEqual(self.check({"champion": "0" * 40, "finished_at": self.promoted}, later), ["advmaps:missing"])
        self.assertIn("advmaps:missing", [a.key for a in health.alerts(self.state, self.repo, later, {})])


if __name__ == "__main__":
    unittest.main()
