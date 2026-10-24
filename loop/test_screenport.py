"""Screen ports (loop/screenport.py, method note M24) on real data: results.jsonl's rows E0090-E0105 as recorded
(ids, ideas, verdicts, SPRT score and Elo, and the screen rows without their r500 blocks,
testdata/screenport-results-sample.jsonl), the real I203 and I261 ideas (the breeder's mergeRound and the hand-queued
hunter port), real params.hpp of v0018 (the hunter's base), v0024 (the breeder's), v0029 (the champion they were
measured against) and v0032 with its sweep's mergeRound=499 games (testdata/screenport-sweep-v0032.jsonl), real idea
titles, and the repo's real tags and main's gauntlet.toml."""

import testhome  # before anything that reads live paths
import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import health
import meta
import screenport

DATA = Path(__file__).resolve().parent / "testdata"
ROWS = [json.loads(line) for line in (DATA / "screenport-results-sample.jsonl").read_text().splitlines()]
IDEAS = (DATA / "screenport-ideas-sample.jsonl").read_text()
WITHOUT_I261 = "".join(line + "\n" for line in IDEAS.splitlines() if '"I261"' not in line)
V0029 = "c4f28ca15c1e446768ba83c0928769964d18633b"
SPECS = {s["name"]: s for s in tomllib.loads((screenport.REPO / "referee/gauntlet.toml").read_text())["opponent"]}
PARAMS = {v: (DATA / f"params-{v}.hpp").read_text() for v in ("v0018", "v0024", "v0029", "v0032")}
V0032 = "7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50"
HUNT = {"hunterMaxLength": 8, "huntMaxDist": 10, "huntMinEdge": -1}
# A made-up sweep score for mergeRound=499 above the hunter's 0.60 in later_result(0.4), so its mergeRound stays out
# of the port as at 0.46, and those tests keep asking only about the rise.
HIGH = {"mergeRound=499": (0.65, 60)}


def state_with(directory: str, ideas: str) -> Path:
    state = Path(directory)
    shutil.copy(DATA / "screenport-results-sample.jsonl", state / "results.jsonl")
    (state / "ideas.jsonl").write_text(ideas)
    return state


# A result tested against v0029 after the sample, in which the champion scored `champion_points` against the hunter.
def later_result(champion_points: float) -> dict:
    row = json.loads(json.dumps(ROWS[-1]))
    row.update(id="E0106", base=V0029, candidate="f" * 40, verdict="discarded", evaluation={"base": V0029})
    next(o for o in row["pool"]["opponents"] if o["name"] == "hunter")["base"] = champion_points
    return row


class Scores(unittest.TestCase):
    def test_the_champions_own_screen_gives_each_entrys_latest_score(self):
        scores = screenport.entry_scores(ROWS, {}, V0029, Path("/nonexistent"))
        # E0105 promoted v0029: its candidate column is v0029 itself (hunter 0.54 for the champion).
        self.assertEqual(scores["hunter"], {"score": 0.46, "games": 100,
                                            "source": "E0105's screen, the champion as its candidate"})
        self.assertEqual(scores["grower"]["score"], 0.07)
        self.assertEqual(scores["breeder"]["score"], 0.45)  # rounded: 1 - 0.55 is 0.44999...

    def test_a_later_result_tested_against_the_champion_replaces_it(self):
        scores = screenport.entry_scores(ROWS + [later_result(0.5)], {}, V0029, Path("/nonexistent"))
        self.assertEqual((scores["hunter"]["score"], scores["hunter"]["source"]),
                         (0.5, "E0106's screen, the champion as its base"))

    def test_history_gives_each_earlier_champion_its_latest_screen_once(self):
        # The seven numbers of method note M24, one a champion. E0095 and E0099 were both tested against f9f3bd7 (E0095
        # rebased onto it) and share its screen games: only E0099's, the later, counts.
        past = [(commit[:7], eid, points) for commit, eid, points, _ in screenport.history(ROWS, "hunter")]
        self.assertEqual(past, [("8d4d03e", "E0090", 0.52), ("9becbb5", "E0089", 0.61), ("4e3da73", "E0092", 0.45),
                                ("f9f3bd7", "E0099", 0.5), ("c7f3fb4", "E0101", 0.49), ("36773e6", "E0102", 0.43),
                                ("1c225be", "E0105", 0.48)])

    def test_only_a_title_naming_the_entry_as_a_screen_entry_counts_as_a_port(self):
        ideas = {json.loads(line)["id"]: json.loads(line) for line in IDEAS.splitlines()}
        self.assertTrue(screenport.names_port(ideas["I261"], "hunter"))  # "Hunt like the hunter screen entry"
        for title in ("Cap the swarm at 40: stop ordinary splits once we have enough dragons, so pearls become length",
                      "Intercept the rammer: a short dragon of ours rams a short enemy head that is closing on our "
                      "long dragon, even at an even trade",
                      "Hunters read remembered lengths: E0091's per-id length memory under the hunter's hunt values "
                      "(stacked test)", ideas["I203"]["title"]):  # I036, I197, I263 and I203
            self.assertFalse(any(screenport.names_port({"title": title}, name) for name in SPECS), title)


class Port(unittest.TestCase):
    def test_the_hunters_values_land_on_v0029(self):
        change = screenport.port(SPECS["hunter"]["params"], PARAMS["v0018"], PARAMS["v0018"], PARAMS["v0029"])
        self.assertEqual(change["port"], {"hunterMaxLength": (5, 8), "huntMaxDist": (6, 10), "huntMinEdge": (0, -1),
                                          "mergeRound": (471, 100000)})
        self.assertEqual((change["same"], change["missing"]), ([], {}))

    # I968 (29 Sep) set feederking's numbers as they were (beaconRound 405->350, foldRound 397->350): sig asked for
    # formulas of what the bot sees. A port now ports the mechanism behind its own switches, a moved number a hint for
    # a rule and a flipped switch as it is, and the idea still passes validation.
    def test_a_port_builds_its_numbers_as_rules_and_flips_its_switches(self):
        change = {"port": {"mergeMinUnits": (8, 4), "beaconRound": (405, 350), "foldRound": (397, 350),
                           "localFold": (True, False)}, "missing": {}, "same": [], "swept": {}, "unswept": {}}
        spec = {"name": "feederking", "commit": "a5e4552" + "0" * 33}
        idea = screenport.proposal(spec, change, {"score": 0.47, "games": 100, "source": "its late screen"}, [], 53, 56,
                                   V0029, PARAMS["v0029"])
        self.assertIn("feederking screen entry's mechanism onto the champion, each part behind its own switch, from "
                      "its moves off the champion's values (mergeMinUnits 8->4, beaconRound 405->350, foldRound "
                      "397->350, localFold true->false): it scores", idea["title"])
        self.assertTrue(screenport.names_port(idea, "feederking"))
        self.assertEqual(screenport.title_values(idea), {name: new for name, (_, new) in change["port"].items()})
        for text in ("flips localFold true->false, each a switch: a behaviour choice, ported as it is",
                     "ports the behaviour behind the entry's moves off the champion's values, mergeMinUnits 8->4, "
                     "beaconRound 405->350, foldRound 397->350: each number is a hint, not a value to set", "the champion's number kept otherwise",
                     "needs a Constants: reason", "each part behind its own switch, nothing else changed"):
            self.assertIn(text, idea["hypothesis"])
        self.assertNotIn("sets mergeMinUnits", idea["hypothesis"])
        self.assertEqual(meta.valid_proposals([json.dumps(idea)], 1, options=False)[0]["title"], idea["title"])

    def test_values_the_champion_already_has_are_left_out(self):
        # E0101 gave the champion the breeder's split gates; only its mergeRound is left to port.
        change = screenport.port(SPECS["breeder"]["params"], PARAMS["v0024"], PARAMS["v0024"], PARAMS["v0029"])
        self.assertEqual(change["port"], {"mergeRound": (471, 499)})
        self.assertEqual(sorted(change["same"]), ["splitChildSpace", "splitParentSpace", "splitParentTerritory"])

    # M37 on real numbers: v0032's sweep (sweeps/7e4b60b.../results.jsonl, testdata/screenport-sweep-v0032.jsonl)
    # played mergeRound=499 at 0.50 over 20 games; the breeder scored 0.49 against v0034 in E0127's screen (the
    # champion as its candidate, 0.51), the score screenport skipped it at.
    def test_a_value_the_sweep_played_alone_is_left_out_only_when_it_scored_at_least_the_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "sweeps" / V0032).mkdir(parents=True)
            shutil.copy(DATA / "screenport-sweep-v0032.jsonl", state / "sweeps" / V0032 / "results.jsonl")
            scores = screenport.sweep_scores(state, V0032, PARAMS["v0032"])
            unswept = screenport.sweep_scores(state, "0" * 40, PARAMS["v0032"])  # a champion with no sweep yet
        self.assertEqual(scores, {"mergeRound=499": (0.5, 20)})  # every other variant is still to play
        breeder = screenport.port(SPECS["breeder"]["params"], PARAMS["v0024"], PARAMS["v0024"], PARAMS["v0032"])
        self.assertEqual(breeder["port"], {"mergeRound": (471, 499)})
        change = screenport.drop_swept(breeder, PARAMS["v0032"], scores, 0.49)
        self.assertEqual((change["port"], list(change["swept"])), ({}, ["mergeRound"]))
        self.assertIn("mergeRound=499 scored 0.50 over 20 games, at least the entry's 0.49", change["swept"]["mergeRound"])
        # An entry scoring more than the sweep's variant keeps the value: the sweep couldn't see that gain.
        change = screenport.drop_swept(breeder, PARAMS["v0032"], scores, 0.52)
        self.assertEqual((change["port"], change["swept"]), ({"mergeRound": (471, 499)}, {}))
        self.assertIn("scored 0.50 over 20 games, under the entry's 0.52", change["unswept"]["mergeRound"])
        # So does a champion whose sweep hasn't played it yet.
        change = screenport.drop_swept(breeder, PARAMS["v0032"], unswept, 0.49)
        self.assertEqual(change["port"], {"mergeRound": (471, 499)})
        self.assertIn("hasn't finished mergeRound=499 yet", change["unswept"]["mergeRound"])
        # mergeRound 100000 is past the game's end: the sweep's never (mergeRound 499) is its variant.
        hunter = screenport.port(SPECS["hunter"]["params"], PARAMS["v0018"], PARAMS["v0018"], PARAMS["v0032"])
        change = screenport.drop_swept(hunter, PARAMS["v0032"], scores, 0.46)
        self.assertEqual((list(change["port"]), list(change["swept"])), (list(HUNT), ["mergeRound"]))

    # Real titles from the backlog of 2026-09-27: naming a param isn't setting it (I093 tunes the merge cascade, I215
    # only mentions foldRound); I126 sets beaconRound 397.
    def test_an_idea_sets_a_value_only_when_its_title_says_so(self):
        i203 = next(json.loads(line) for line in IDEAS.splitlines() if '"I203"' in line)
        self.assertTrue(screenport.sets_value(i203, "mergeRound", 499))
        self.assertFalse(screenport.sets_value(i203, "mergeRound", 49))
        i126 = {"title": "The king beacon starts with the final fold: beaconRound 405->397 (= foldRound), so the fold "
                         "tuned to r397 actually starts there"}
        self.assertTrue(screenport.sets_value(i126, "beaconRound", 397))
        self.assertFalse(screenport.sets_value(i126, "beaconRound", 195))
        self.assertFalse(screenport.sets_value(i126, "beaconRound", 405))  # the old value it moves from
        self.assertTrue(screenport.sets_value({"title": "Port the longram screen entry's values onto the champion: "
                                                        "maxRamSteps 3->6: it scores 0.52"}, "maxRamSteps", 6))
        self.assertTrue(screenport.sets_value({"title": "Rams from further: maxRamSteps=3->6"}, "maxRamSteps", 6))
        self.assertFalse(screenport.sets_value({"title": "Rams from further: maxRamSteps=3->6"}, "maxRamSteps", 3))
        self.assertTrue(screenport.sets_value({"title": "The beacon from r195: beaconRound to 195"}, "beaconRound", 195))
        for title in ("Tune the merge cascade: when it starts, when it stops, how close a merge partner must be",
                      "The king in the fold doesn't step into its own coil: from foldRound, prefer a move with a safe "
                      "follow-up"):
            self.assertFalse(any(screenport.sets_value({"title": title}, name, value)
                                 for name, value in (("mergeRound", 499), ("foldRound", 200))), title)

    # I203 (the breeder's mergeRound 499 alone, todo in the sample; being coded as E0135 when M37 was written) sets a
    # value of the breeder and, as a never, of the hunter (mergeRound 100000): each port waits for it whole.
    def test_a_port_waits_whole_while_another_idea_sets_one_of_its_values(self):
        ideas = [json.loads(line) for line in IDEAS.splitlines()]
        breeder = screenport.port(SPECS["breeder"]["params"], PARAMS["v0024"], PARAMS["v0024"], PARAMS["v0029"])
        self.assertIn("I203 (todo) sets mergeRound 499, one of its values", screenport.waits_on(breeder, ideas, "breeder"))
        hunter = screenport.port(SPECS["hunter"]["params"], PARAMS["v0018"], PARAMS["v0018"], PARAMS["v0029"])
        self.assertIn("I203 (todo) sets mergeRound 499", screenport.waits_on(hunter, ideas, "hunter"))
        tried = [dict(i, status="discarded") if i["id"] == "I203" else i for i in ideas]
        self.assertIsNone(screenport.waits_on(breeder, tried, "breeder"))  # tested: the port goes ahead whole
        # A todo idea holds it only at WAIT_PRIORITY or better (I203 is priority 2 in the sample); one being tried always.
        for priority, holds in ((screenport.WAIT_PRIORITY, True), (screenport.WAIT_PRIORITY + 1, False)):
            ranked = [dict(i, priority=priority) if i["id"] == "I203" else i for i in ideas]
            self.assertEqual(screenport.waits_on(breeder, ranked, "breeder") is not None, holds, priority)
        claimed = [dict(i, priority=5, status="trying:lane2") if i["id"] == "I203" else i for i in ideas]
        self.assertIn("I203 (trying:lane2) sets mergeRound 499", screenport.waits_on(breeder, claimed, "breeder"))

    # For foldRound and beaconRound a round past the game's end is their switch's flip off (sweep.NOT_4X).
    def test_a_round_past_the_end_is_the_sweeps_never_variant(self):
        self.assertEqual(screenport.sweep_variant("mergeRound", 100000, PARAMS["v0032"]), "mergeRound=499")
        self.assertEqual(screenport.sweep_variant("foldRound", 500, PARAMS["v0032"]), "finalFold=false")
        self.assertEqual(screenport.sweep_variant("beaconRound", 499, PARAMS["v0032"]), "kingBeacon=false")
        self.assertIsNone(screenport.sweep_variant("foldRound", 450, PARAMS["v0032"]))

    def test_a_param_only_the_entrys_code_has_is_marked_missing(self):
        entry = PARAMS["v0018"] + "\n    bool replayEnemyMsgs = false;  // a switch the entry's own code adds\n"
        change = screenport.port({"replayEnemyMsgs": True}, entry, PARAMS["v0018"], PARAMS["v0029"])
        self.assertEqual((change["port"], change["missing"]), ({}, {"replayEnemyMsgs": True}))


class Decide(unittest.TestCase):
    # `swept`: the champion's sweep scores (sweep_scores). By default mergeRound=499 at v0032's real 0.50 over 20 games
    # (testdata/screenport-sweep-v0032.jsonl), since v0029 had no sweep: at least the hunter's 0.46 and the breeder's
    # 0.45, so both leave their mergeRound out.
    def decide(self, ideas: str, rows: tuple[dict, ...] = (), swept: dict | None = None
               ) -> tuple[dict, list[dict], list[dict]]:
        swept = {"mergeRound=499": (0.5, 20)} if swept is None else swept
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, ideas)
            with (state / "results.jsonl").open("a") as handle:
                handle.writelines(json.dumps(row) + "\n" for row in rows)
            with patch.object(meta, "champion_commit", return_value=V0029), \
                    patch.object(screenport, "sweep_scores", return_value=swept):
                decisions = {d["entry"]: d["decision"] for d in screenport.decide(screenport.REPO, state, queue=True)}
                again = screenport.decide(screenport.REPO, state, queue=True)
            backlog = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines()]
        return decisions, again, backlog

    def test_on_todays_data_nothing_is_queued(self):
        decisions, _, backlog = self.decide(IDEAS)
        self.assertIn("I261 (todo) names it as a screen entry", decisions["hunter"])
        self.assertIn("nothing left to port", decisions["breeder"])  # 0.45: the sweep's mergeRound=499 scored 0.50
        self.assertIn("1 promotions old", decisions["earlyfold"])  # 0.51, but built on v0028
        self.assertIn("under 0.45", decisions["replayer"])  # 0.43
        self.assertIn("no param diff from its base v0004", decisions["v0004"])
        self.assertEqual(len(backlog), 2)

    def test_with_no_sweep_yet_the_breeder_waits_for_i203(self):
        decisions, _, backlog = self.decide(IDEAS, swept={})
        self.assertIn("I203 (todo) sets mergeRound 499, one of its values", decisions["breeder"])
        self.assertEqual(len(backlog), 2)

    def test_a_bot_that_isnt_ours_has_nothing_to_port(self):
        import rescreen  # the starter (tag gauntlet-starter): its params.hpp is only a comment
        with patch.object(rescreen, "gauntlet", return_value=[SPECS["starter"], SPECS["hunter"]]):
            decisions, _, _ = self.decide(IDEAS)
        self.assertEqual(decisions["starter"], "its bot has no params to port (not ours: the toolkit's starter)")
        self.assertIn("I261 (todo) names it as a screen entry", decisions["hunter"])

    def test_an_entry_marked_port_false_is_never_ported(self):
        # greedyram (not our bot, though its history holds v0001) and boxed (a handicapped champion) opt out.
        import rescreen
        hunter = {**SPECS["hunter"], "port": False}
        with patch.object(rescreen, "gauntlet", return_value=[hunter]):
            decisions, _, backlog = self.decide(IDEAS)
        self.assertEqual(decisions["hunter"], "its entry says port = false")

    # REFLECT 2026-09-28 20:52 F7: harvester (v0053 plus P.harvestDrops, port = false) won 29 of 48 against v0053 and
    # waited for sig to ask. On the repo's real params.hpp: against v0053 it is queued at priority 1, marked next;
    # today's 0.52 over 50 isn't above half at p <= TRIGGER_P; v0056, which has harvestDrops on, needs no port; and
    # loneking, not ours (none of its v0001 base's params), adds no switch to port.
    def test_a_code_switch_entry_beating_the_champion_is_ported_whatever_its_age(self):
        spec = SPECS["harvester"]
        show = lambda rev: screenport.git(screenport.REPO, "show", f"{rev}:bot/params.hpp")  # noqa: E731
        switches = screenport.new_switches(show(spec["commit"]), show("v0053"))
        self.assertEqual(switches, {"harvestDrops": True})
        self.assertEqual(screenport.new_switches(show(SPECS["loneking"]["commit"]), show("v0001")), {})
        v0053 = screenport.git(screenport.REPO, "rev-parse", "v0053^{commit}").strip()

        def port(score: float, games: int, champion: str = "v0053"):
            return screenport.code_port(spec, switches, {"score": score, "games": games, "source": "a screen"}, 53,
                                        int(champion[1:]), v0053, show(champion), [], [], lambda commit: None)

        idea = port(29 / 48, 48)
        self.assertEqual((idea["priority"], idea["next"]), (1, True))
        self.assertEqual(idea["screen_port"], {"entry": "harvester", "champion": v0053, "score": 29 / 48, "games": 48,
                                               "values": {"harvestDrops": True}, "code": True})
        self.assertIsInstance(port(0.52, 50), str)  # a reason, no idea
        self.assertIsInstance(port(29 / 48, 48, "v0056"), str)

    def test_checking_takes_no_lock_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, IDEAS)
            before = sorted(p.name for p in state.iterdir())
            with patch.object(meta, "champion_commit", return_value=V0029):
                screenport.decide(screenport.REPO, state)
            self.assertEqual(sorted(p.name for p in state.iterdir()), before)

    def test_without_i261_the_hunters_values_are_queued_once(self):
        decisions, again, backlog = self.decide(WITHOUT_I261)
        self.assertRegex(decisions["hunter"], r"queued as I\d+")
        idea = backlog[-1]
        self.assertEqual((idea["priority"], idea["source"], idea["status"]),
                         (2, f"screen-port:hunter:{V0029[:7]}", "todo"))
        self.assertIn("hunterMaxLength 5->8, huntMaxDist 6->10, huntMinEdge 0->-1): it scores", idea["title"])
        self.assertIn("0.460 against it over 100 games", idea["hypothesis"])
        self.assertIn("9becbb5 0.61 (E0089), 4e3da73 0.45 (E0092)", idea["hypothesis"])
        self.assertIn("Left out, since the variant sweep of this champion measured each alone", idea["hypothesis"])
        self.assertNotIn("Kept although", idea["hypothesis"])
        self.assertEqual(idea["screen_port"], {"entry": "hunter", "champion": V0029, "score": 0.46, "games": 100,
                                               "values": HUNT})
        self.assertTrue(screenport.names_port(idea, "hunter"))
        self.assertEqual(len(backlog), 2)  # nothing of the breeder is left to port
        self.assertIn(f"{idea['id']} (todo) ports it already",
                      next(d["decision"] for d in again if d["entry"] == "hunter"))

    def test_a_tried_port_is_queued_again_only_when_the_entry_scores_clearly_higher(self):
        tried = {"id": "I300", "category": "combat", "priority": 2, "status": "discarded", "hypothesis": "h",
                 "source": "screen-port:hunter:1c225be", "title": "Port the hunter screen entry's values",
                 "screen_port": {"score": 0.46, "values": HUNT}}
        ideas = WITHOUT_I261 + json.dumps(tried) + "\n"
        decisions, _, _ = self.decide(ideas)
        self.assertIn("I300 (discarded) tried the same values, the entry scoring 0.46 when it was queued",
                      decisions["hunter"])
        # 0.50 is within 100 games' noise of 0.46; 0.60 is a real rise, worth another port.
        decisions, _, _ = self.decide(ideas, (later_result(0.5),))
        self.assertIn("it scores 0.50 now, under 0.05 more", decisions["hunter"])
        decisions, _, backlog = self.decide(ideas, (later_result(0.4),), swept=HIGH)
        self.assertRegex(decisions["hunter"], r"queued as I\d+")
        self.assertEqual(backlog[-1]["screen_port"]["score"], 0.6)

    def test_a_tried_idea_named_after_the_entry_blocks_the_values_its_title_sets(self):
        i261 = next(json.loads(line) for line in IDEAS.splitlines() if '"I261"' in line)
        self.assertEqual(screenport.title_values(i261), HUNT)  # its real title: "hunterMaxLength 5->8, ..."
        tried = WITHOUT_I261 + json.dumps({**i261, "status": "discarded"}) + "\n"
        # Given up without a result: blocked against today's score.
        decisions, _, _ = self.decide(tried)
        self.assertIn("I261 (discarded) tried the same values, the entry scoring 0.46 now", decisions["hunter"])
        # Tested against v0028 (1c225be), where the entry's latest screen was E0105's 0.48; today's 0.46 is no rise.
        result = {"id": "E0107", "idea": {"id": "I261", "title": i261["title"]}, "base": ROWS[-1]["base"],
                  "evaluation": {"base": ROWS[-1]["base"]}, "candidate": "e" * 40, "verdict": "discarded",
                  "sprt": {"score": 0.5}}
        decisions, _, _ = self.decide(tried, (result,))
        self.assertIn("scoring 0.48 against 1c225be (E0107)", decisions["hunter"])
        decisions, _, backlog = self.decide(tried, (result, later_result(0.4)), swept=HIGH)
        self.assertRegex(decisions["hunter"], r"queued as I\d+")

    def test_a_failing_run_is_recorded_for_health_and_not_retried_until_its_inputs_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, IDEAS)
            with patch.object(screenport, "decide", side_effect=RuntimeError("git show failed")) as decide, \
                    patch.object(screenport, "_seen", ()):
                self.assertEqual(screenport.tick(screenport.REPO, state, now=1000.0), [])
                screenport.tick(screenport.REPO, state, now=1060.0)
                self.assertEqual(decide.call_count, 1)
            record = json.loads((state / screenport.RECORD).read_text())
            self.assertEqual((record["error"], record["failed_at"]), ("RuntimeError: git show failed", 1000.0))
            alerts = health.screen_ports(state, 2000.0)
            self.assertEqual([a.key for a in alerts], ["screenport:failed"])
            self.assertIn("git show failed", alerts[0].text)

    def test_health_alerts_when_the_step_stops_running_while_the_upkeep_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, IDEAS)
            changed = (state / "results.jsonl").stat().st_mtime
            (state / screenport.RECORD).write_text(json.dumps({"checked_at": changed - 60, "error": None}))
            later = changed + health.SCREENPORT_GRACE + 60
            self.assertEqual(health.screen_ports(state, later), [])  # no upkeep running: quiet
            (state / "maintenance.json").write_text(json.dumps({"checked_at": later - 600}))
            self.assertEqual([a.key for a in health.screen_ports(state, later)], ["screenport:stale"])
            (state / screenport.RECORD).write_text(json.dumps({"checked_at": changed + 30, "error": None}))
            self.assertEqual(health.screen_ports(state, later), [])  # it ran after the change

    def test_the_tick_decides_again_only_when_its_inputs_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, IDEAS)
            with patch.object(screenport, "decide", return_value=[]) as decide, \
                    patch.object(screenport, "_seen", ()):
                screenport.tick(screenport.REPO, state)
                screenport.tick(screenport.REPO, state)
                self.assertEqual(decide.call_count, 1)
                with (state / "results.jsonl").open("a") as handle:
                    handle.write(json.dumps(ROWS[-1]) + "\n")
                screenport.tick(screenport.REPO, state)
                self.assertEqual(decide.call_count, 2)


class SharedSet(unittest.TestCase):
    """The champion's games on its shared map set, on real data: champion d52bfb4 (promoted by E0125), E0127's
    screen of it (testdata/screenport-results-d52bfb4.jsonl, the rows as recorded less r500) and its set's real
    pool-hunter-1deaf0c33acf-base.jsonl (testdata/screenport-set-hunter.jsonl, 100 games)."""

    CHAMPION = "d52bfb4cac1a2706f791636d1bc8970b0718d59e"
    ROWS = [json.loads(line) for line in (DATA / "screenport-results-d52bfb4.jsonl").read_text().splitlines()]

    def state(self, directory: str) -> Path:
        state = Path(directory)
        folder = state / "shared-maps" / f"{self.CHAMPION[:12]}-unswbc-1.1.0"
        folder.mkdir(parents=True)
        shutil.copy(DATA / "screenport-set-hunter.jsonl", folder / "pool-hunter-1deaf0c33acf-base.jsonl")
        return state

    def test_a_first_stage_stop_or_no_screen_still_leaves_the_sets_100_games(self):
        rows = json.loads(json.dumps(self.ROWS))
        hunter = next(o for o in rows[1]["pool"]["opponents"] if o["name"] == "hunter")
        self.assertEqual(round(1 - hunter["base"], 2), 0.36)  # E0127's full screen: the same games as the set's
        hunter["stopped_at"] = 25  # as a two-stage screen that stopped at its first stage would record it
        self.assertEqual(screenport.entry_scores(rows, {}, self.CHAMPION, Path("/nonexistent"))["hunter"]["games"], 50)
        with tempfile.TemporaryDirectory() as directory:
            scores = screenport.entry_scores(rows, {}, self.CHAMPION, self.state(directory))
            # And with no screen of the champion as base at all (a promising result has none), the set still counts.
            alone = screenport.entry_scores(rows[:1], {}, self.CHAMPION, self.state(directory + "/x"))
        self.assertEqual(scores["hunter"], {"score": 0.36, "games": 100, "source":
                                            "the champion's games on its shared map set d52bfb4cac1a-unswbc-1.1.0"})
        self.assertEqual(alone["hunter"]["games"], 100)
        self.assertEqual(scores["swarm"]["source"], "E0127's screen, the champion as its base")  # no set file for it

    def test_a_torn_line_or_lock_is_not_counted_and_another_build_of_the_entry_is_never_merged(self):
        real = (DATA / "screenport-set-hunter.jsonl").read_text()
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            folder = next((state / "shared-maps").iterdir())
            with (folder / "pool-hunter-1deaf0c33acf-base.jsonl").open("a") as handle:
                handle.write('{"map": "x", "a_si')
            (folder / "pool-hunter-1deaf0c33acf-base.jsonl.lock").write_text("")
            # Another build of the hunter (another spec key) on the same maps, which the champion always beat.
            (folder / "pool-hunter-000000000000-base.jsonl").write_text(real.replace('"winner": "b"', '"winner": "a"'))
            either = screenport.set_scores(state, self.CHAMPION)
            mains = screenport.set_scores(state, self.CHAMPION, {"hunter": "1deaf0c33acf"})
            other = screenport.set_scores(state, self.CHAMPION, {"hunter": "000000000000"})
        self.assertEqual(list(either), ["hunter"])
        self.assertEqual(either["hunter"]["games"], 100)  # each build's 100, never one merged set
        self.assertEqual((mains["hunter"]["score"], mains["hunter"]["games"]), (0.36, 100))
        self.assertLess(other["hunter"]["score"], 0.36)

    def test_the_current_toolkit_epochs_set_is_preferred_over_a_fuller_older_one(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.state(directory)
            (state / "maintenance.json").write_text(json.dumps({"active": {"epoch": "unswbc-9.9.9"}}))
            self.assertEqual(screenport.set_scores(state, self.CHAMPION)["hunter"]["games"], 100)  # no current set
            current = state / "shared-maps" / meta.shared_set_name(self.CHAMPION, "unswbc-9.9.9",
                                                                   meta.claim_mix(None))  # today's
            current.mkdir()
            rows = (DATA / "screenport-set-hunter.jsonl").read_text().splitlines()[:4]
            (current / "pool-hunter-1deaf0c33acf-base.jsonl").write_text("\n".join(rows) + "\n")
            scores = screenport.set_scores(state, self.CHAMPION)
        self.assertEqual(scores["hunter"]["games"], 4)
        self.assertIn(current.name, scores["hunter"]["source"])


class Stats(unittest.TestCase):
    def test_stats_md_lists_each_entrys_score_against_the_current_champion(self):
        with tempfile.TemporaryDirectory() as directory:
            state = state_with(directory, IDEAS)
            text = meta.stats_markdown(state / "results.jsonl")
        self.assertIn("## Screen entries against the current champion", text)
        self.assertIn("- hunter 0.46 over 100 games (E0105's screen, the champion as its candidate)", text)


if __name__ == "__main__":
    unittest.main()
