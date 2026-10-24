"""The judge (loop/judge.py; LOOP.md: Judged verdicts), on three real recorded results, slimmed from the live loop home
on 2026-09-28 (testdata/judge-results-sample.jsonl: each row's SPRT summary and per-trial scores, its idea as claimed
and its coder's final message): E0245 (I548, targeted, H1 +16.6 over 282 pairs, target flat), E0251 (I631, a retest
of E0225's portal-pair sharing on a predeclared portalrooms 0.5 mix, H0 -1.5 over 2304 pairs: portalrooms 0.487 over
1153 trials) and E0254 (I677, H0 -3.8 over 1362 pairs). A situational win is modelled on E0251 by lifting its
portalrooms trials, which is the case sig asked about: a change that helps a lot in one situation and leaves the
rest alone."""

import testhome  # before anything that reads live paths
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import judge
import meta
import publishing

ROWS = {json.loads(line)["id"]: json.loads(line)
        for line in (Path(__file__).parent / "testdata/judge-results-sample.jsonl").read_text().splitlines()}


# E0251 with its portalrooms trials `lift` higher: its per_style bucket and every per-trial score by the share of
# trials that bucket holds, so the other buckets and the trials outside portalrooms read as recorded (0.509 over 1151).
def situational(lift: float = 0.033, shift_trials: bool = True) -> dict:
    row = copy.deepcopy(ROWS["E0251"])
    sprt = row["sprt"]
    n = sprt["per_style"]["portalrooms"]["trials"]
    sprt["per_style"]["portalrooms"]["score"] += lift
    if shift_trials:
        step = lift * n / len(sprt["per_map"])
        sprt["per_map"] = {k: v + step for k, v in sprt["per_map"].items()}
    return row


# `row` as though nothing before its SPRT named portal rooms: no mix, and neither its idea's text nor its coder's
# message mentions them.
def undeclared(row: dict) -> dict:
    row = copy.deepcopy(row)
    row["evaluation"] = {**row["evaluation"], "maps": {"worldgen": 0.5}}
    row["idea"]["history"] = []
    row["coder"] = row["coder"].replace("portalrooms", "those")
    return row


# E0251 with a made-up SPRT: `sub_n` trials at `sub` on portalrooms (its declared mix) and `rest_n` at `rest` elsewhere,
# every per-trial score at the mean, so each reading uses meta.TRIAL_SD.
def split_row(sub_n: int, sub: float, rest_n: int, rest: float) -> dict:
    row = copy.deepcopy(ROWS["E0251"])
    mean = (sub_n * sub + rest_n * rest) / (sub_n + rest_n)
    row["sprt"] = {"decision": "H0", "rotation": ["champion"], "games": [],
                   "per_map": {f"m{i}": mean for i in range(sub_n + rest_n)},
                   "per_style": {"portalrooms": {"trials": sub_n, "score": sub},
                                 "mix": {"trials": rest_n, "score": rest}}}
    return row


def keep(name: str | None) -> dict:
    return {"verdict": "keep", "circumstance": name, "reason": "helps where it acts, loses nowhere else"}


class TestCheck(unittest.TestCase):
    def test_a_situational_win_on_a_predeclared_mix_with_no_loss_elsewhere_can_be_kept(self) -> None:
        row = situational()
        record = judge.check(row, keep("portalrooms"), row["coder"])
        self.assertEqual((record["asked"], record["verdict"]), ("keep", "keep"), record.get("why"))
        self.assertIn("its idea's map mix, frozen at claim (portalrooms)", record["declared"]["portalrooms"])
        stats = record["stats"]
        self.assertEqual((stats["trials"], round(stats["score"], 3)), (1153, 0.52))
        self.assertLessEqual(stats["p_gain"] * record["looks"], judge.P)
        self.assertGreaterEqual(stats["rest"]["no_loss"], judge.NO_LOSS)  # the other 1151 trials lose nothing
        # The overall gain is small, which is why the SPRT alone couldn't keep it.
        self.assertLess(record["overall"]["score"], 0.52)

    # MISSES 86: a candidate that doesn't repeat its own games (a check played one twice and it differed) is a hard
    # gate like a crash: the judge's keep is a discard. The champion flagged too says nothing of which one it is.
    def test_a_keep_of_a_nondeterministic_candidate_is_a_discard(self) -> None:
        row = situational()
        said = {"said": "mm: MISMATCH; plain twice: replay differs at r259"}
        for base, verdict in ((None, "discard"), ("b" * 16, "keep")):
            with self.subTest(base=base):
                row["sprt"]["nondeterministic"] = {"bots": {"a" * 16: said, "b" * 16: said}, "cand": "a" * 16,
                                                   "base": base}
                self.assertEqual(judge.check(row, keep("portalrooms"), row["coder"])["verdict"], verdict)
        self.assertEqual(judge.nondeterministic({"nondeterministic": {"bots": {"a" * 16: said}, "cand": "a" * 16,
                                                                      "base": None}}), said["said"])
        # Since 29 Sep the report says whom run.blamed found: a candidate flagged beside an opponent nothing cleared
        # isn't the candidate's fault.
        for blamed in (None, said["said"]):
            self.assertEqual(judge.nondeterministic({"nondeterministic": {"bots": {"a" * 16: said}, "cand": "a" * 16,
                                                                          "base": None, "blamed": blamed}}), blamed)

    def test_a_keep_missing_the_guard_is_an_ask_for_the_orchestrator_naming_what_it_missed(self) -> None:
        # The same subgroup found after the test, however the answer says it was declared: the list is the row's.
        row = undeclared(situational())
        answer = {**keep("portalrooms"), "declared": ["portalrooms"],
                  "acts_where": "portalrooms, as the coder said before the SPRT"}
        record = judge.check(row, answer, row["coder"])
        self.assertEqual((record["asked"], record["verdict"]), ("keep", judge.ASK))
        self.assertTrue(record["why"].startswith("post hoc: portalrooms was neither declared nor measured"))
        self.assertIn("guard: post hoc", judge.ask_line("E0251", record))
        # No gain where it was declared to act (E0251 as recorded: portalrooms 0.487 over 1153 trials, on its mix).
        real = ROWS["E0251"]
        self.assertIn("its gain on portalrooms isn't clear: 0.487 over 1153 trials",
                      judge.check(real, keep("portalrooms"), real["coder"])["why"])
        # A thin declared subgroup: E0245's idea named Queen Of Spades before its SPRT, 0.625 over 8 trials.
        real = ROWS["E0245"]
        self.assertIn("x 2 declared subgroups", judge.check(real, keep("near_queen_of_spades"), real["coder"])["why"])
        # A confirmation of E0251's undeclared near_portals would play its family, the portalrooms style.
        self.assertEqual(judge.confirm_mix(ROWS["E0251"], "near_portals"), {"maps": {"portalrooms": meta.MAP_SHARE[1]}})

    # MISSES 90: E0310's judge asked a confirm on near_trauma (runs/E0310/judged.json, copied in
    # testdata/circumstance-claims-sample.jsonl), recorded with {"near": 0.5}: Trauma's copies about 1 in 30 of half the
    # trials. Its confirmation now plays Trauma's family, sealedstart, at the top share, a quarter or more of the maps.
    def test_a_confirm_on_a_near_layout_plays_its_family(self) -> None:
        row = next(r for r in map(json.loads, (Path(__file__).parent / "testdata/circumstance-claims-sample.jsonl")
                                  .open()) if r["experiment"] == "E0310")
        self.assertEqual(row["judged"]["confirm"], {"maps": {"near": 0.5}})
        confirm = judge.as_confirm(row, row["judged"], "")
        self.assertEqual(confirm["verdict"], "confirm")
        frozen = meta.claim_mix(confirm["confirm"]["maps"])
        self.assertGreaterEqual(frozen["sealedstart"], 0.25)
        self.assertTrue(meta.map_mix_ok(frozen))
        self.assertEqual(judge.confirm_mix(row, "near_devil"), {"maps": {"small": meta.MAP_SHARE[1]}})

    # MISSES 90 review (probe texts): only a declared circumstance or a count of changed decisions names styles, by
    # whole map or style names; not our team UNSW, "Default" steps, "dead ends", "death portal maps" (a general
    # style's), a score or a win count. Portal Labyrinth named alone is maze, as confirm_mix plays it; with the other
    # portal maps, the portal family.
    def test_only_a_declared_circumstance_or_a_count_of_changed_decisions_names_styles(self) -> None:
        cases = {"Circumstance for the judge: games against UNSW ladder teams that fold early.": [],
                 "Circumstance: ties on the Default step order, 58 a game.": [],
                 "Circumstance: dragons trapped in dead ends, 3 a game.": [],
                 "They beat us 30 of 40 on Trauma last night.": [],
                 # I522's own words: a score on one map.
                 "E0184 already read near_trophy 0.626 (about 100 trials) and 9 of 10 on the official Trophy map.": [],
                 "The rule changed 40 of 50 moves on Devil copies.": ["small"],
                 "Circumstance for the judge: long starters, as on the Sprint maps.": ["sprint"],
                 "Circumstance: portal maps, e.g. Portals and Trauma.": ["sealedstart", "portalrooms"],
                 "Circumstance: Death portal maps, a portal that kills.": [],  # not the deathportal token
                 "Circumstance: maps of 2400+ tiles.": ["big"],  # I908's words
                 "Circumstance: Portal Labyrinth copies.": ["maze"],
                 "Circumstance: Portal Labyrinth and Trauma.": ["maze", "sealedstart"],
                 "Circumstance: Portal Labyrinth and Portals.": ["portalrooms"],
                 "Circumstance: sealed start maps.": ["sealedstart"],
                 "Declared before this test (LOOP.md: Judged verdicts): its circumstance is `near_trauma`, which its "
                 "mix {\"maps\": {\"near\": 0.5}} aims at.": ["sealedstart"]}
        for text, styles in cases.items():
            with self.subTest(text):
                self.assertEqual(judge.stated_where({"hypothesis": text})[0], styles)
        self.assertIn("e.g. Portals and Trauma", judge.stated_where({"hypothesis": "Circumstance: portal maps, e.g. "
                                                                                   "Portals and Trauma."})[1])
        self.assertEqual(judge.confirm_mix({}, "near_portal_labyrinth"), {"maps": {"maze": meta.MAP_SHARE[1]}})

    def test_a_loss_elsewhere_is_an_ask(self) -> None:
        # The same portalrooms lift with the overall score left as recorded: the other 1151 trials fall to 0.476.
        row = situational(shift_trials=False)
        record = judge.check(row, keep("portalrooms"), row["coder"])
        self.assertEqual(record["verdict"], judge.ASK)
        self.assertTrue(record["why"].startswith("outside portalrooms (1151 trials, 0.476"), record["why"])
        # Over 3000 trials outside it, -5 normalized Elo passes the no-loss bound but reads as a loss beyond its own
        # noise (z -1.1); -4 (z -0.9) doesn't. An overall score at level is no keep either.
        for rest_nelo, verdict in ((-5, judge.ASK), (-4, "keep")):
            row = split_row(1000, 0.56, 3000, 0.5 + rest_nelo * 2 ** 0.5 * meta.TRIAL_SD / meta.NELO_PER_T)
            record = judge.check(row, keep("portalrooms"), "")
            self.assertEqual(record["verdict"], verdict, record.get("why"))
            self.assertGreaterEqual(record["stats"]["rest"]["no_loss"], judge.NO_LOSS)
        record = judge.check(split_row(50, 0.6, 3000, 0.5 - 0.1 * 50 / 3000), keep("portalrooms"), "")
        self.assertEqual(record["why"], "overall 0.5000 over 3050 trials is not above level")

    def test_each_subgroup_and_its_rest_read_their_own_trials_spread(self) -> None:
        # 10 portalrooms trials close together (0.55/0.65), 20 others far apart (0.2/0.8), two of them small maps.
        scores = [0.55, 0.65] * 5 + [0.2, 0.8] * 10
        sprt = {"rotation": ["champion"], "per_map": {f"m{i}": v for i, v in enumerate(scores)},
                "per_style": {"portalrooms": {"trials": 10, "score": 0.6}, "mix": {"trials": 20, "score": 0.5}},
                "per_area": {"<=1024": {"trials": 2, "score": 0.5}}}
        trials = [{"trial": i, "opponent": "champion", "score": v, "style": "portalrooms" if i < 10 else "mix",
                   "size": "<=1024" if i >= 28 else "1025-2399"} for i, v in enumerate(scores)]
        groups, overall = judge.subgroups(sprt, trials)
        own = judge.spread(scores[:10], 0)
        self.assertEqual(groups["portalrooms"]["sd"], round(own, 4))
        self.assertEqual(groups["portalrooms"]["z"], round(0.1 / (own / 10 ** 0.5), 2))
        self.assertEqual(groups["mix"]["rest"]["z"], groups["portalrooms"]["z"])  # the rest of mix is portalrooms
        self.assertNotEqual(groups["portalrooms"]["sd"], overall["sd"])
        self.assertEqual(groups["<=1024 tiles"]["sd"], overall["sd"])  # two trials: the whole test's spread
        self.assertEqual(judge.subgroups(sprt)[0]["portalrooms"]["sd"], overall["sd"])  # no trial rows

    def test_a_confirmation_is_not_confirmed_again_and_answers_are_checked(self) -> None:
        row = copy.deepcopy(ROWS["E0254"])
        row["idea"]["source"] = "judge:E0200"
        record = judge.check(row, {"verdict": "confirm", "circumstance": "vs swarm", "reason": "r"}, row["coder"])
        self.assertEqual(record["verdict"], "discard")
        self.assertIn("a judged confirmation already", record["why"])
        for bad in ({"verdict": "promote", "reason": "r"}, {"verdict": "keep"}, ["keep"]):
            with self.assertRaises(ValueError):
                judge.check(row, bad, "")
        # A subgroup the result doesn't have is dropped, never guessed.
        record = judge.check(ROWS["E0254"], {"verdict": "confirm", "circumstance": "portalrooms", "reason": "r"}, "")
        self.assertEqual((record["circumstance"], record["confirm"]), (None, {}))

    def test_what_is_owed_a_judge(self) -> None:
        row = dict(ROWS["E0254"], finished=judge.FROM + 1)
        self.assertEqual(judge.owed(row), "judge")
        self.assertIsNone(judge.owed(dict(row, finished=judge.FROM - 1)))  # rule-checked by the orchestrator
        self.assertEqual(judge.owed(dict(row, verdict="kept")), "review")  # a keep on H1 too
        self.assertIsNone(judge.owed(dict(row, verdict="kept", idea={"kind": "speed"})))  # a move-for-move test
        self.assertIsNone(judge.owed(dict(row, rejudged={"of": "discarded"})))
        self.assertIsNone(judge.owed(dict(row, verdict="no code", sprt={})))
        on_probability = dict(row, verdict="kept", sprt={**row["sprt"], "limit_kept": {"probability": 0.95}})
        self.assertEqual(judge.owed(on_probability), "review")
        self.assertEqual(judge.check(on_probability, {"verdict": "flag", "reason": "r"}, "")["verdict"], "flag")


class TestApply(unittest.TestCase):
    """driver.apply_judged on a keep judge.check let stand: through driver.rejudge on the champion it played, else a
    confirmation of the same candidate."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        for name, value in (("STATE", self.state), ("IDEAS", self.state / "ideas.jsonl"),
                            ("RESULTS", self.state / "results.jsonl"), ("EVALUATING", self.state / "evaluating.json"),
                            ("WORKTREES", self.state / "clones")):
            patched = patch.object(driver, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        row = situational()
        self.coder = row.pop("coder")
        row["finished"] = judge.FROM + 60
        self.exp = driver.Experiment.load({**row, "idea": {**row["idea"], "status": "trying:lane1"}})
        self.exp.run_dir.mkdir(parents=True)
        (self.exp.run_dir / "cand").mkdir()
        (self.exp.run_dir / "sprt.json").write_text(json.dumps(self.exp.sprt))
        (self.exp.run_dir / "evaluation.json").write_text(json.dumps({"screen_maps": ["s1.map"]}))
        driver.RESULTS.write_text(json.dumps(driver.asdict(self.exp)) + "\n")
        driver.IDEAS.write_text(json.dumps({**row["idea"], "priority": 1, "status": "discarded"}) + "\n")
        self.champion = self.exp.evaluation["base"]
        self.record = judge.check(driver.asdict(self.exp), keep("portalrooms"), self.coder)
        self.assertEqual(self.record["verdict"], "keep")
        judge.folder(self.state, self.exp.id).mkdir(parents=True)

    # Runs `call` (apply_judged on self.record by default) with rejudge's champion, toolkit and screen faked: the screen
    # passes unless `worse`. Returns (its result, the promote mock, the queue_recheck mock).
    def apply(self, call=None, worse: bool = False):
        def screen(exp, cand, maps):
            exp.pool = {"ok": not worse,
                        "opponents": [{"name": "swarm", "worse": worse, "candidate": 0.4, "base": 0.6}]}
        with patch.object(driver, "git", return_value=self.champion), \
                patch.object(driver, "is_ancestor", return_value=True), \
                patch.object(driver.maintenance, "toolchain",
                             return_value={"epoch": self.exp.evaluation["toolchain"]["epoch"]}), \
                patch.object(driver, "refresh_referee", return_value=True), patch.object(driver, "require_champion"), \
                patch.object(driver, "check_pool", side_effect=screen), \
                patch.object(driver, "promote") as promote, patch.object(driver, "start_evidence"), \
                patch.object(driver, "queue_recheck") as recheck, patch.object(driver, "unblock"), \
                patch.object(driver.health, "run", return_value=[]):
            record = call() if call else driver.apply_judged(self.exp, dict(self.record))
        return record, promote, recheck

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in driver.RESULTS.read_text().splitlines()]

    def alerts(self) -> str:
        return (self.state / "host-alerts.log").read_text()

    def test_a_keep_on_the_champion_it_played_promotes_through_rejudge(self) -> None:
        record, promote, recheck = self.apply()
        promote.assert_called_once()
        recheck.assert_not_called()  # re-checks only on evidence (sig 2026-09-29), not at promotion
        old, new = self.rows()
        self.assertEqual((old["verdict"], new["verdict"]), ("discarded", "kept"))
        self.assertTrue(new["reason"].startswith("kept by judgement: helps on portalrooms (0.520 over 1153 trials"))
        self.assertEqual((new["sprt"]["limit_kept"]["of"], new["sprt"]["limit_kept"]["circumstance"]),
                         ("judged", "portalrooms"))
        self.assertEqual(new["judged"]["verdict"], "keep")
        self.assertTrue(new["rejudged"]["rule"].startswith("kept by judgement (LOOP.md: Judged verdicts)"))
        self.assertTrue(publishing.proven(new))
        self.assertFalse(publishing.proven({**new, "judged": {}}))  # the judged record is its proof
        self.assertEqual(judge.saved(self.state, self.exp.id)["verdict"], "keep")
        self.assertIn(f"JUDGED: {self.exp.id} keep on portalrooms", self.alerts())
        self.assertFalse(judge.folder(self.state, self.exp.id).exists())

    # Review of 2026-09-29: 3 of 8 judged keeps were flagged (E0300, E0309, E0331), all for doubt about the gain, not a
    # measured loss, so a flag queuing a priority-1 re-check would spend a full SPRT on doubt. A flag alerts only, and
    # the judge's prompt asks it for a measured loss at the loop's chance bar.
    def test_a_flag_alerts_only_and_asks_for_a_measured_loss(self) -> None:
        self.exp.verdict = "kept"
        flag = {"asked": "flag", "verdict": "flag", "reason": "loses on swarm: 0.41 over 300 trials, p 0.002"}
        record = driver.apply_judged(self.exp, dict(flag))
        self.assertEqual([i for i in driver.read_ideas() if i.get("source") == f"recheck:{self.exp.id}"], [])
        self.assertIn("alert only: the orchestrator decides", record["why"])
        self.assertIn(f"JUDGED: {self.exp.id} flag (alert only", self.alerts())
        text = judge.prompt(driver.asdict(self.exp), {}, self.state)
        self.assertIn(f"a chance of luck at most {judge.chance.TRIGGER_P}", text)
        self.assertNotIn("a gain resting on one subgroup", text)

    def test_a_keep_off_the_current_champion_becomes_a_confirmation_of_the_same_candidate(self) -> None:
        self.champion = "a1b2c3d"
        record, promote, _ = self.apply()
        promote.assert_not_called()
        self.assertEqual(len(self.rows()), 1)  # nothing re-recorded
        self.assertEqual((record["asked"], record["verdict"]), ("keep", "confirm"))
        self.assertIn("the keep couldn't be applied", record["why"])
        (idea,) = [i for i in driver.read_ideas() if i["id"] in record["queued"]]
        self.assertEqual((idea["source"], idea["category"]), (f"judge:{self.exp.id}", "retest"))
        self.assertEqual((idea["priority"], idea.get("next")), (1, True))  # sig 2026-09-29: confirmations go next
        self.assertTrue(idea["title"].startswith(f"Retest {self.exp.id} to confirm on portalrooms"))
        self.assertEqual(idea["evaluation"], {"elo1": 10, "maps": {"portalrooms": meta.MAP_SHARE[1]}})
        self.assertIn(f"cand/{self.exp.id}", idea["hypothesis"])
        # Its own judge reads the subgroup as declared before its test, so it can be kept on it.
        retested = {"idea": idea, "evaluation": {"maps": meta.claim_mix(idea["evaluation"]["maps"])},
                    "sprt": self.exp.sprt}
        self.assertIn("portalrooms", judge.declared(retested, ""))
        # And it can't be confirmed again.
        self.assertEqual(judge.as_confirm({**retested, "candidate": "abc"}, {}, "")["verdict"], "discard")
        self.assertEqual(judge.saved(self.state, self.exp.id)["verdict"], "confirm")
        self.assertIn(f"JUDGED: {self.exp.id} confirm on portalrooms", self.alerts())

    def test_a_keep_whose_screen_regresses_is_a_discard_and_leaves_the_result_as_recorded(self) -> None:
        record, promote, _ = self.apply(worse=True)
        promote.assert_not_called()
        self.assertEqual(record["verdict"], "discard")
        self.assertIn("its opponent screen regressed: Screen: swarm 0.40 vs champion 0.60 (worse)", record["why"])
        (row,) = self.rows()  # no "promising" row recorded over the discard
        self.assertEqual(row["verdict"], "discarded")

    # MISSES 98: E0273, E0318 and E0351 were promising, judged discard, and their ideas stayed followup, waiting for
    # the unchanged retest the judge ruled against.
    # The retest its post-mortem queued before the discard (the judge failed, a later --judge discarded) is dropped;
    # one after a prerequisite tests a stack and stays.
    def test_a_discard_of_a_promising_result_marks_its_followup_idea_tried_and_leaves_one_that_moved_on(self) -> None:
        self.exp.verdict, self.exp.reason = "promising", "SPRT H0; positive estimate, needs fresh confirmation"
        note = meta.result_note(driver.asdict(self.exp))
        idea_id = self.exp.idea["id"]
        discard = {"asked": "discard", "verdict": "discard", "reason": "no declared subgroup clears the bar"}
        postmortem = lambda: driver.finish_idea(  # noqa: E731
            idea_id, "followup", f"{self.exp.id}: follow-ups", ends_claim=False, result=self.exp.id)
        driver.IDEAS.write_text(json.dumps({**self.exp.idea, "priority": 1, "status": "todo", "history": [note]}) + "\n")
        record = driver.apply_judged(self.exp, dict(discard))  # moved on (back to todo): left alone
        self.assertEqual(([i["status"] for i in driver.read_ideas()], record.get("idea")), (["todo"], None))
        postmortem()  # so is the post-mortem's status, which used to overwrite it
        self.assertEqual(driver.read_ideas()[0]["status"], "todo")
        retests = [{"id": f"I{n}", "category": "retest", "priority": 2, "status": "todo", "hypothesis": "h",
                    "source": f"postmortem:{self.exp.id}", "title": f"Retest {self.exp.id} {how} on the current champion"}
                   for n, how in ((901, "alone"), (902, "after I900"))]
        driver.IDEAS.write_text("".join(json.dumps(i) + "\n" for i in [
            {**self.exp.idea, "priority": 1, "status": "followup", "history": [note]}, *retests]))
        record = driver.apply_judged(self.exp, dict(discard))
        idea, *after = driver.read_ideas()
        self.assertEqual((idea["status"], record["idea"], record["dropped"]), ("tried", "tried", ["I901"]))
        self.assertEqual([i["status"] for i in after], ["dropped", "todo"])
        self.assertIn(f"{self.exp.id}'s judge discarded it", after[0]["history"][0])
        self.assertIn(f"JUDGED: {self.exp.id} discard (its idea {idea_id} is tried", self.alerts())
        # The post-mortem's note, after the judge, doesn't make it a followup again, even with the followup it read
        # before the judge's discard was saved (a --judge rerun during it).
        postmortem()
        self.assertEqual(driver.read_ideas()[0]["status"], "tried")

    # E0320's post-mortem queued I914, "Retest E0320 alone", after its judge's discard.
    def test_a_post_mortem_after_a_judged_discard_queues_no_retest(self) -> None:
        self.exp.verdict, self.exp.reason = "promising", "opponent screen regressed; not promoted"
        self.exp.sprt["elo"] = driver.RETEST_MIN_ELO + 0.7
        driver.IDEAS.write_text(json.dumps({**self.exp.idea, "priority": 1, "status": "followup",
                                            "history": [meta.result_note(driver.asdict(self.exp))]}) + "\n")
        clone = self.state / "clone"
        clone.mkdir()
        (clone / "followups.jsonl").write_text(json.dumps(
            {"category": "portals", "priority": 2, "title": "on its code", "on_candidate": True,
             "hypothesis": "builds on its frontier. Options: (1) avoid it; (2) turn it into a gain; (3) mirror it on "
                           "them. This is (1)."}) + "\n")

        def queued() -> list[dict]:
            batches = []
            with patch.object(driver.meta, "append_ideas", side_effect=lambda state, ideas, source: batches.append(
                    ideas) or [f"I9{len(batches)}"]), patch.object(driver, "wait_for_retest"):
                driver.add_followups(self.exp, clone)
            return [i for batch in batches for i in batch]
        self.assertTrue(queued()[0]["title"].startswith(f"Retest {self.exp.id} alone"))  # before any judge
        driver.apply_judged(self.exp, {"asked": "discard", "verdict": "discard", "reason": "r"})
        (followup,) = queued()
        self.assertEqual((followup["title"], followup["after"]), ("on its code", [self.exp.idea["id"]]))
        self.assertEqual(driver.read_ideas()[0]["status"], "tried")

    def test_a_rerun_judge_runs_the_follow_on_of_its_keep(self) -> None:
        with patch.object(driver, "run_judge", return_value=self.record), \
                patch.object(driver, "rerun_turn", return_value=f"{self.exp.id}: followon saved") as turn:
            self.assertTrue(driver.judge_again(self.exp.id).endswith(f"{self.exp.id}: followon saved"))
        turn.assert_called_once_with(self.exp.id)

    def test_an_ask_waits_for_the_orchestrator_whose_keep_applies_it_with_its_follow_on(self) -> None:
        ask = {**self.record, "verdict": judge.ASK, "why": "post hoc: portalrooms was neither declared nor measured"}
        driver.apply_judged(self.exp, ask)
        self.assertEqual(len(self.rows()), 1)  # nothing applied by itself
        self.assertIn(f"JUDGED ask: {self.exp.id} keep on portalrooms (helps where it acts", self.alerts())
        later = max(judge.FROM + 60, time.time()) + health.JUDGE_GRACE + 1
        self.assertEqual([a.key for a in health.judge_turns(self.state, later)], [f"judge ask:{self.exp.id}"])
        self.assertIn("--rule must give", driver.decide_ask(self.exp.id, "keep", " "))
        with patch.object(driver, "rerun_turn", return_value="followon saved") as turn:
            line, promote, _ = self.apply(lambda: driver.decide_ask(self.exp.id, "keep", "portal rooms decide games"))
        promote.assert_called_once()
        turn.assert_called_once_with(self.exp.id)
        new = self.rows()[-1]
        self.assertEqual(new["verdict"], "kept")
        self.assertIn("the orchestrator's call on its ask: portal rooms decide games", new["reason"])
        self.assertTrue(publishing.proven(new))
        self.assertEqual(judge.saved(self.state, self.exp.id)["orchestrator"], "portal rooms decide games")

    def test_a_native_result_gets_the_judge_task_then_its_post_mortem(self) -> None:
        import native
        task = native.judge_task(self.exp)
        work = Path(task["judge_folder"])
        self.assertEqual(task["then"], f"uv run loop/native.py judged {self.exp.id}")
        self.assertIn(f"# Judge {self.exp.id}", task["judge"])
        self.assertTrue({"subgroups.json", "declared.json", "trials.jsonl", "coder.md"} <= {p.name for p in work.iterdir()})
        (work / judge.ANSWER).write_text(json.dumps({"verdict": "discard", "reason": "no gain where it acts"}))
        with patch.object(driver, "prepare_postmortem", return_value="explain it"):
            done = json.loads(native.judged(self.exp))
        self.assertEqual((done["judged"], done["postmortem"]), ("discard: no gain where it acts", "explain it"))
        self.assertEqual(done["then"], f"uv run loop/native.py followups {self.exp.id}")
        self.assertEqual(judge.saved(self.state, self.exp.id)["by"], "native")

    def test_an_owed_judge_alerts_and_a_judged_result_still_owes_its_rule_check(self) -> None:
        later = max(judge.FROM + 60, time.time()) + health.JUDGE_GRACE + 1  # the result's time, and its folder's
        self.assertEqual([a.key for a in health.judge_turns(self.state, later)], [f"judge:{self.exp.id}",
                                                                                   f"judge folder:{self.exp.id}"])
        self.assertEqual([a.key for a in health.unchecked_rejections(self.state, later)], ["rule-check"])
        self.champion = "a1b2c3d"
        self.apply()
        self.assertEqual(health.judge_turns(self.state, later), [])
        self.assertEqual([a.key for a in health.unchecked_rejections(self.state, later)], ["rule-check"])


if __name__ == "__main__":
    unittest.main()
