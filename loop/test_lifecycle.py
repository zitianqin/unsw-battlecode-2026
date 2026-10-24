"""Shared Claude/native lifecycle checks; never starts agents or external actions."""

import testhome  # before anything that reads live paths
import contextlib
import fcntl
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from uuid import UUID
from pathlib import Path
from unittest.mock import Mock, patch

import driver
import native


# Every field driver.write_ideas requires of a backlog row (meta.IDEA_FIELDS) but id and status, as placeholders.
IDEA = {"category": "strategy", "priority": 3, "title": "t", "hypothesis": "h"}


# driver.keep_wip reads, makes and deletes wip/* branches in driver.REPO, the real repo (a worktree shares its refs),
# and run_experiment and native.finish call it; driver.record runs every health check on the real repo (~2.4 s a
# call, and its alerts name the live worktrees). run_experiment also launches a result's judge (driver.run_judge, a real
# headless session) once results are owed one (judge.FROM). Every test here gets stubs, and one that wants any
# patches its own.
def setUpModule() -> None:
    for stub in (patch.object(driver, "keep_wip", return_value=[]), patch.object(driver.health, "run", return_value=[]),
                 patch.object(driver, "run_judge", return_value=None)):
        stub.start()
        unittest.addModuleCleanup(stub.stop)

class TestLifecycle(unittest.TestCase):
    def test_mismatched_champion_stops_evaluation_and_clean_checkout_is_repaired(self):
        with patch.object(driver, "git", side_effect=["stray"]):
            with self.assertRaisesRegex(RuntimeError, "checkout"):
                driver.require_champion("accepted")
        with patch.object(driver, "git", side_effect=["accepted", "stray", "", ""]) as git:
            driver.repair_champion()
        self.assertEqual(git.call_args.args, ("checkout", "--detach", "accepted"))
        self.assertNotIn("--force", git.call_args.args)

    def test_champion_recovery_never_overwrites_dirty_work(self):
        with patch.object(driver, "git", side_effect=["accepted", "stray", " M bot/main.cpp"]):
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                driver.repair_champion()

    def test_recovery_finalizes_accepted_promotion_without_retesting(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "runs/E0001"
            folder.mkdir(parents=True)
            exp = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1, candidate="new", verdict="kept",
                                    cpu={"ok": True}, bot_tests={"ok": True}, sprt={"decision": "H1"})
            (state / "ideas.jsonl").write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
            (folder / "experiment.json").write_text(json.dumps(driver.asdict(exp)))
            with patch.object(driver, "STATE", state), patch.object(driver, "IDEAS", state / "ideas.jsonl"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "WORKTREES", state / "clones"), patch.object(driver, "git", return_value="new"), \
                    patch.object(driver, "start_evidence") as evidence:
                driver.recover_promotions()
            self.assertEqual(json.loads((state / "results.jsonl").read_text())["verdict"], "kept")
            # Its evidence refresh too, which the interrupted process never started: evidence.md kept showing the
            # champion before it.
            self.assertEqual([call.args[0].id for call in evidence.call_args_list], ["E0001"])
            self.assertEqual(json.loads((state / "ideas.jsonl").read_text())["status"], "kept")

    def test_a_claim_does_not_wait_for_an_evaluation_in_another_process(self):
        # E0053's games held the lock for an hour while the next idea's claim waited behind them.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "results.jsonl").write_text("")
            with open(state / "astra-evaluation.lock", "w") as other, patch.object(driver, "STATE", state), \
                    patch.object(driver, "repair_champion") as repair:
                fcntl.flock(other, fcntl.LOCK_EX)  # another open file stands in for another process
                self.assertEqual(driver.recover_promotions(wait=False), [])
                repair.assert_not_called()
                with driver.evaluation_lock(wait=False) as held:
                    self.assertFalse(held)
            with patch.object(driver, "STATE", state), driver.evaluation_lock(wait=False) as held:
                self.assertTrue(held)  # free again once the other holder is gone

    def test_same_lane_cannot_claim_twice_across_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.jsonl"
            path.write_text('\n'.join(json.dumps(i) for i in [
                {**IDEA, "id": "I1", "priority": 1, "status": "trying:lane1"},
                {**IDEA, "id": "I2", "priority": 1, "status": "todo"}]))
            with patch.object(driver, "IDEAS", path):
                with self.assertRaisesRegex(RuntimeError, "already"):
                    driver.claim_idea(1)
            self.assertEqual(json.loads(path.read_text().splitlines()[1])["status"], "todo")

    def test_supervised_claims_allow_coding_coders_and_one_more_in_flight(self):
        # sig's counts (driver.CODING's comment) live in driver.CODING and IN_FLIGHT; this reads them, not a number.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "ideas.jsonl"
            path.write_text('\n'.join(json.dumps(i) for i in [
                {**IDEA, "id": "I1", "priority": 1, "status": "trying:lane1"},
                *({**IDEA, "id": f"I{n}", "priority": min(n, 5), "status": "todo"}
                  for n in range(2, driver.IN_FLIGHT + 3))]))

            def coded(experiment, idea):  # its worker has finished: the candidate waits for evaluation
                folder = state / "runs" / experiment
                folder.mkdir(parents=True)
                (folder / "experiment.json").write_text(json.dumps({"id": experiment, "idea": {"id": idea}}))
                (folder / "worker.json").write_text(json.dumps({"status": "finished"}))

            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state):
                idea = driver.claim_idea(0)
                self.assertEqual((idea["id"], driver.lane_of(idea)), ("I2", 2))
                for lane in range(3, driver.CODING + 1):
                    self.assertEqual(driver.lane_of(driver.claim_idea(0)), lane)
                with self.assertRaisesRegex(RuntimeError, f"{driver.CODING} experiments already being coded"):
                    driver.claim_idea(0)  # lanes 1 to CODING are all still being coded
                coded("E0001", "I1")  # under test (or queued): one more may be coded, in lane IN_FLIGHT
                self.assertEqual(driver.lane_of(driver.claim_idea(0)), driver.IN_FLIGHT)
                coded("E0002", "I2")  # one under test, one queued, CODING - 1 coding: IN_FLIGHT in flight
                with self.assertRaisesRegex(RuntimeError, f"{driver.IN_FLIGHT} experiments already in flight"):
                    driver.claim_idea(0)
            self.assertEqual(json.loads(path.read_text().splitlines()[driver.IN_FLIGHT + 1])["status"], "todo")

    def test_an_idea_marked_next_is_claimed_before_higher_priorities_and_fillers(self):
        # sig, 2026-09-27: the `reverser` port (I385) sat 13th among priority-1 ideas, which run in id order.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "ideas.jsonl"
            path.write_text('\n'.join(json.dumps(i) for i in [
                {**IDEA, "id": "I1", "priority": 1, "status": "todo"},
                {**IDEA, "id": "I2", "priority": 1, "status": "todo", "kind": "tune",
                 "tune": {"params": ["foldRound"]}},
                {**IDEA, "id": "I9", "priority": 2, "status": "todo", "next": True}]))
            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state):
                self.assertEqual(driver.claim_idea(0)["id"], "I9")
                self.assertEqual(driver.claim_idea(0)["id"], "I2")  # then the idle filler, as before

    # Method M70: 94 of 315 waiting ideas were at priority 1, claimed oldest first. Six real ones (their text cut,
    # testdata/p1-ideas-sample.jsonl) with ranks set by mark_idea: ranked ones first, lower first, then the rest oldest
    # first by number (I1000 after I754, not before I119 as its id string sorts); a next mark still goes before all,
    # and a rank never lifts an idea over a better priority.
    def test_within_a_priority_ranked_ideas_are_claimed_first_lowest_rank_first(self):
        rows = [json.loads(line) for line in (Path(__file__).parent / "testdata/p1-ideas-sample.jsonl").read_text()
                .splitlines()]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "ideas.jsonl"
            path.write_text("".join(json.dumps(i) + "\n" for i in rows + [
                {**IDEA, "id": "I900", "priority": 2, "status": "todo", "rank": -5},
                {**IDEA, "id": "I1000", "priority": 1, "status": "todo"}]))
            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state), \
                    patch.object(driver, "CODING", 10), patch.object(driver, "IN_FLIGHT", 10):
                driver.mark_idea("I784", rank=1)
                driver.mark_idea("I414", rank=2.5)
                driver.mark_idea("I275", rank=3)
                driver.mark_idea("I275", rank=None)  # dropped again: unranked
                driver.mark_idea("I777", next=True)
                with self.assertRaises(ValueError):
                    driver.mark_idea("I119", rank="first")
                claimed = [driver.claim_idea(lane)["id"] for lane in range(1, 9)]
            self.assertNotIn("rank", json.loads(path.read_text().splitlines()[1]))
        self.assertEqual(claimed, ["I777", "I784", "I414", "I119", "I275", "I754", "I1000", "I900"])

    def test_a_waiting_upgrade_holds_the_next_claim_until_the_pipeline_is_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ideas = state / "ideas.jsonl"
            ideas.write_text('\n'.join(json.dumps(i) for i in [
                {**IDEA, "id": "I1", "priority": 1, "status": "trying:lane1"},
                {**IDEA, "id": "I2", "priority": 2, "status": "todo"}]))
            applied = []

            def finish_other(seconds):  # the experiment in flight finishes while we wait
                ideas.write_text('\n'.join(json.dumps(i) for i in [
                    {**IDEA, "id": "I1", "priority": 1, "status": "tried"},
                    {**IDEA, "id": "I2", "priority": 2, "status": "todo"}]))

            with patch.object(driver, "STATE", state), patch.object(driver, "IDEAS", ideas), \
                    patch.object(driver, "STOP", state / "STOP"), patch.object(driver, "recover_promotions"), \
                    patch.object(driver.maintenance, "tick"), \
                    patch.object(driver.maintenance, "upgrade_waiting", return_value=True), \
                    patch.object(driver.maintenance, "apply_pending",
                                 side_effect=lambda repo, st, busy: applied.append(busy) or {"status": "upgraded"}), \
                    patch.object(driver.time, "sleep", side_effect=finish_other) as sleep, \
                    contextlib.redirect_stdout(io.StringIO()):
                idea = driver.claim_next(0)
            self.assertEqual((idea["id"], applied, sleep.call_count), ("I2", [False], 1))

    def test_post_mortem_follow_ups_are_validated_and_queued_with_fresh_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ideas = root / "ideas.jsonl"
            ideas.write_text(json.dumps({**IDEA, "id": "I060", "priority": 1, "status": "tried", "title": "old"}) + "\n")
            clone = root / "E0041"
            clone.mkdir()
            options = "Options: (1) steer away; (2) hunt them; (3) bait them. This is (1)."
            good = {"category": "sonar", "priority": 2, "title": "act on enemy sightings",
                    "hypothesis": "E0041 stored enemy sightings but never used them; steer short dragons away. "
                                  + options}
            (clone / "followups.jsonl").write_text("\n".join([
                json.dumps(good),
                json.dumps({**good, "priority": 9}),  # invalid priority
                "not json",
                json.dumps({**good, "title": "pearls only",
                            "hypothesis": "Never add avoidance; it lost in E0026. " + options}),
                json.dumps({**good, "title": "fourth, over the cap"})]))
            exp = driver.Experiment(id="E0041", idea={"id": "I060"}, lane=1, verdict="discarded")
            with patch.object(driver, "STATE", root), patch.object(driver, "IDEAS", ideas), \
                    patch.object(driver, "finish_idea") as finish:
                self.assertIn("queued I061", driver.add_followups(exp, clone))
            rows = [json.loads(line) for line in ideas.read_text().splitlines()]
            self.assertEqual([(r["id"], r["title"], r["status"], r["source"]) for r in rows[1:]],
                             [("I061", "act on enemy sightings", "todo", "postmortem:E0041")])
            finish.assert_called_once()

    def test_only_a_refereed_result_gets_a_post_mortem_or_when_kept_a_follow_on(self):
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory)
            idea = {"id": "I1", "title": "t"}
            refereed = {"decision": "H0", "pairs": 66, "nelo": -72.3}
            post, follow = "# Post-mortem", "# Follow-on"
            for verdict, sprt, wanted in (("discarded", refereed, post), ("promising", refereed, post),
                                          ("kept", refereed, follow), ("discarded", {}, None),
                                          ("stale", {}, None), ("error", {}, None)):
                exp = driver.Experiment(id="E0036", idea=idea, lane=1, verdict=verdict, sprt=sprt)
                with patch.object(driver, "STATE", clone):
                    prompt = driver.prepare_postmortem(exp, clone)
                self.assertEqual(prompt and prompt[:prompt.index(" for ")], wanted, verdict)
            self.assertIn('"nelo": -72.3', prompt or driver.postmortem_prompt(
                driver.Experiment(id="E0036", idea=idea, lane=1, verdict="discarded", sprt=refereed)))

    def test_guard_detects_changed_referee_for_native_too(self):
        exp = driver.Experiment(id="E0001", idea={}, lane=1, session={"protected": "original"})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(driver, "STOP", state / "STOP"), patch.object(driver, "STATE", state), \
                    patch.object(driver, "protected_fingerprint", return_value="changed"):
                with self.assertRaisesRegex(RuntimeError, "PROTECTED"):
                    driver.guard_candidate(exp, state)
            self.assertTrue((state / "STOP").exists())

    def test_native_watcher_exits_when_result_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0001").mkdir(parents=True)
            (state / "results.jsonl").write_text('{"id":"E0001"}\n')
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver.maintenance, "tick", side_effect=AssertionError("finished"), create=True):
                native.watch("E0001")
            self.assertEqual(json.loads((state / "runs/E0001/upkeep.json").read_text())["status"], "stopped")

    def test_finishing_idea_is_idempotent_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.jsonl"
            path.write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
            with patch.object(driver, "IDEAS", path):
                driver.finish_idea("I1", "todo", "E0001: error (failed)")
                driver.finish_idea("I1", "todo", "E0001: error (failed)")
            self.assertEqual(json.loads(path.read_text())["status"], "todo")
            self.assertEqual(len(json.loads(path.read_text())["history"]), 1)

    def test_stop_during_coding_preserves_candidate_without_starting_games(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "ideas.jsonl").write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "IDEAS", state / "ideas.jsonl"), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_fingerprint", return_value="same"), \
                    patch.object(driver, "run_lane", side_effect=lambda exp, tree: (state / "STOP").touch()), \
                    patch.object(driver, "evaluate", side_effect=AssertionError("must not start games")), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
            self.assertEqual(exp.verdict, "blocked")
            self.assertIn("stop requested", exp.reason)
            self.assertTrue((state / "STOP").exists())
            idea = json.loads((state / "ideas.jsonl").read_text())  # back to be coded again, with its note (MISSES 89)
            self.assertEqual((idea["status"], idea["history"][-1]), ("todo", f"{exp.id}: blocked ({exp.reason})"))

    def test_tuning_ideas_run_the_tuner_instead_of_a_coding_session(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ran = []
            (state / "ideas.jsonl").write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "IDEAS", state / "ideas.jsonl"), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_fingerprint", return_value="same"), \
                    patch.object(driver, "run_lane", side_effect=AssertionError("no coding session")), \
                    patch.object(driver, "run_tuner", side_effect=lambda exp, tree: (ran.append(exp.id),
                                                                                  (state / "STOP").touch())), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "t", "kind": "tune", "tune": {"params": ["x"]}})
            self.assertEqual(ran, [exp.id])
            self.assertEqual(exp.verdict, "blocked")

    def test_a_kept_results_evidence_refresh_holds_no_evaluation_lock_and_the_follow_on_doesnt_wait_for_it(self):
        # E0167's refresh held the lock 15:52-16:02 on 2026-09-27 while E0168 waited and 0-6 of ~55 host slots were
        # busy (MISSES 50): the next evaluation may start while it plays. It plays in a process of its own, so the
        # follow-on after it doesn't wait for its 48 games either (up to the hour's timeout beside back-to-back SPRTs).
        from home import evaluation_running
        held = []

        def evaluate(exp, worktree):
            held.append(("evaluate", evaluation_running()))
            exp.verdict, exp.reason = "kept", "H1"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_baseline", return_value={}), patch.object(driver, "run_lane"), \
                    patch.object(driver, "session_failure", return_value=None), \
                    patch.object(driver, "own_commits", return_value=(["c"], "")), \
                    patch.object(driver, "refresh_referee", return_value=True), \
                    patch.object(driver, "guard_candidate"), patch.object(driver, "evaluate", side_effect=evaluate), \
                    patch.object(driver, "promote"), patch.object(driver, "record"), patch.object(driver, "cleanup"), \
                    patch.object(driver, "finish_idea"), patch.object(driver, "start_official"), \
                    patch.object(driver, "run_postmortem", side_effect=lambda *a: held.append(("follow-on",))), \
                    patch.object(driver, "refresh_evidence", side_effect=AssertionError("played in this process")), \
                    patch.object(driver.meta, "spawn", side_effect=lambda args, cwd, log: held.append(
                        ("evidence", evaluation_running(), args[2])) or 4242), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
        self.assertEqual(exp.verdict, "kept")
        self.assertEqual(held, [("evaluate", True), ("evidence", False, "--refresh-evidence"), ("follow-on",)])

    def test_a_no_code_result_frees_its_idea_and_gets_its_post_mortem(self):
        # MISSES 89: `return exp` inside run_experiment's try skipped everything after its finally, so E0322 (no code)
        # and E0315 (blocked) left their idea 'trying' with no note, and no-code results got no post-mortem.
        ran = []
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_baseline", return_value={}), patch.object(driver, "run_lane"), \
                    patch.object(driver, "session_failure", return_value=None), \
                    patch.object(driver, "own_commits", return_value=([], "")), \
                    patch.object(driver, "no_code", return_value=("no code", "nothing committed")), \
                    patch.object(driver, "evaluate", side_effect=AssertionError("nothing to test")), \
                    patch.object(driver, "record"), patch.object(driver, "cleanup"), \
                    patch.object(driver, "finish_idea", side_effect=lambda *a, **k: ran.append(("finish", a[2]))), \
                    patch.object(driver, "start_official"), patch.object(driver, "run_judge", return_value=None), \
                    patch.object(driver, "run_postmortem", side_effect=lambda *a: ran.append(("post-mortem",))), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
        self.assertEqual(exp.verdict, "no code")
        self.assertEqual(ran, [("finish", f"{exp.id}: no code (nothing committed)"), ("post-mortem",)])

    # E0395 (30 Sep 08:08) was handed in below the bar its orchestrator set, with no way but its SPRT. Withdrawn while
    # it waits behind another evaluation, it leaves the queue, is recorded discarded and gets its post-mortem; nothing
    # plays. With no driver waiting for it, --withdraw refuses.
    def test_a_withdrawn_candidate_leaves_the_queue_for_its_post_mortem(self):
        ran = []
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with self.assertRaisesRegex(SystemExit, "no live evaluation-queue ticket"), \
                    patch.object(driver, "STATE", state):
                driver.withdraw("E0001", "missed its bar")

            def waiting(exp, *rest):  # beside the other evaluation: the orchestrator withdraws it
                ran.append("waited")
                if not (exp.run_dir / driver.WITHDRAW).exists():
                    ran.append(driver.withdraw(exp.id, "its own portalrooms count missed the bar"))

            with open(state / "astra-evaluation.lock", "w") as other, \
                    patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "QUEUE_POLL_S", 0.01), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_baseline", return_value={}), patch.object(driver, "run_lane"), \
                    patch.object(driver, "session_failure", return_value=None), \
                    patch.object(driver, "own_commits", return_value=(["c"], "")), \
                    patch.object(driver, "git", return_value="abc"), patch.object(driver, "fetch_clone") as kept, \
                    patch.object(driver, "prepare_beside", side_effect=waiting), \
                    patch.object(driver, "evaluate", side_effect=AssertionError("nothing plays")), \
                    patch.object(driver, "record"), patch.object(driver, "cleanup"), \
                    patch.object(driver, "finish_idea", side_effect=lambda *a, **k: ran.append(("finish", a[1]))), \
                    patch.object(driver, "start_official"), \
                    patch.object(driver, "run_postmortem", side_effect=lambda *a: ran.append("post-mortem")), \
                    contextlib.redirect_stdout(io.StringIO()):
                fcntl.flock(other, fcntl.LOCK_EX)  # another evaluation holds the lock
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
                self.assertEqual(list((state / driver.meta.QUEUE).glob("*.json")), [])
        self.assertEqual((exp.verdict, exp.reason), ("discarded", f"{driver.WITHDRAWN}: its own portalrooms count "
                                                                  "missed the bar"))
        self.assertIn("withdrawn; its driver", ran[1])
        self.assertEqual(ran[-2:], [("finish", "tried"), "post-mortem"])
        # Its code is kept on cand/, and its post-mortem is owed (prune.ended_early), with its own early task.
        self.assertEqual((kept.call_args.args[1], exp.candidate), ("HEAD:cand/E0001", "abc"))
        self.assertTrue(driver.prune.ended_early(driver.asdict(exp)))
        self.assertIn("withdrawn before its SPRT", driver.EARLY_WITHDRAWN.format(reason=exp.reason))

    def test_uncommitted_worker_changes_never_reach_the_referee(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "ideas.jsonl").write_text(json.dumps({**IDEA, "id": "I1", "title": "test", "status": "trying:lane1"}))
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "IDEAS", state / "ideas.jsonl"), \
                    patch.object(driver, "make_worktree", return_value=state), \
                    patch.object(driver, "protected_fingerprint", return_value="same"), \
                    patch.object(driver, "run_lane"), \
                    patch.object(driver, "git", return_value=" M bot/main.cpp"), \
                    patch.object(driver, "evaluate", side_effect=AssertionError("must not start games")), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
            self.assertEqual(exp.verdict, "error")
            self.assertIn("uncommitted", exp.reason)

    # E0260 (I605, 2026-09-28): its SPRT errored at pair 734 on a map the engine won't load and the idea went back to
    # be recoded. An error on such a map (run.MAP_REFUSED's text) now queues its candidate, unchanged, as its idea's next
    # claim, ahead of a higher priority idea: the claim codes nothing and evaluates in the candidate's own clone and
    # coder session, from the champion it was last tested on, under the errored run's frozen policy less its maps.
    def test_an_error_on_a_map_the_engine_refused_re_evaluates_the_same_candidate_without_recoding(self):
        fault = ("invalid SPRT result: game failed on /s/maps/worldgen/gen_worldgen_144449864.map (as A): the engine "
                 "refused to load the map /s/maps/worldgen/gen_worldgen_144449864.map: dragon 0 segment 6 is not "
                 "adjacent to the one before it")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            clone, ideas = state / "clones/E0001", state / "ideas.jsonl"
            (clone / ".git/info").mkdir(parents=True)
            ideas.write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane2", "evaluation": {"elo1": 10}})
                             + "\n" + json.dumps({**IDEA, "id": "I2", "priority": 1, "status": "todo"}) + "\n")
            seen, turns = [], []

            def errors(exp, tree):
                exp.candidate, exp.verdict, exp.reason = "c1", "error", fault
                exp.evaluation.update(base="b0", map_seed=5)

            def retests(exp, tree):
                seen.append((tree, exp.base, exp.session["session_id"], exp.session["reevaluates"],
                             exp.evaluation.get("map_seed"), exp.evaluation["elo1"],
                             (tree / ".git/info/bc-lane").read_text().strip()))
                # Its coder's hand-in time, summary and merge turns, and its final message from the run that coded it
                # (a fresh merge agent's brief): not the claim's time, which made a cold session look warm.
                seen.append((exp.session["coded_at"], exp.session["summary"], driver.coder_final(exp),
                             driver.coder_idle(exp, 2060.0)))
                exp.candidate, exp.verdict, exp.reason = "c1", "discarded", "SPRT H0"

            def patches(evaluate, **stubs):
                return [patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"),
                        patch.object(driver, "RESULTS", state / "results.jsonl"), patch.object(driver, "IDEAS", ideas),
                        patch.object(driver, "WORKTREES", state / "clones"),
                        patch.object(driver, "git", lambda *a, **k: "c1" if a[0] == "rev-parse" else ""),
                        patch.object(driver, "protected_baseline", return_value={}),
                        patch.object(driver, "own_commits", return_value=(1, False)),
                        patch.object(driver, "refresh_referee", return_value=True),
                        patch.object(driver, "guard_candidate"), patch.object(driver, "cleanup"),
                        patch.object(driver, "start_official"), patch.object(driver, "evaluate", side_effect=evaluate),
                        patch.object(driver, "run_postmortem", side_effect=lambda exp, tree: turns.append(tree)),
                        *(patch.object(driver, name, side_effect=stub) for name, stub in stubs.items()),
                        contextlib.redirect_stdout(io.StringIO())]

            with contextlib.ExitStack() as stack:
                def codes(exp, tree):
                    exp.session.update(session_id="s1", name="bc-E0001-s1", coded_at=1000.0, summary="done",
                                       merge_turns=[{"path": "resumed", "ended": 2000.0, "cache_read": 5}])
                    (exp.run_dir / "session.json").write_text(json.dumps({"type": "result", "result": "all of it"}))
                for p in patches(errors, make_worktree=lambda exp: clone, run_lane=codes):
                    stack.enter_context(p)
                driver.run_experiment(2, json.loads(ideas.read_text().splitlines()[0]))
                queued = json.loads(ideas.read_text().splitlines()[0])
                self.assertEqual((queued["status"], queued["reevaluate"]), ("todo", "E0001"))
                claimed = driver.claim_idea(0)
            self.assertEqual(claimed["id"], "I1")
            with contextlib.ExitStack() as stack:
                for p in patches(retests, make_worktree=AssertionError("recoded"), run_lane=AssertionError("recoded")):
                    stack.enter_context(p)
                exp = driver.run_experiment(driver.lane_of(claimed), claimed)
            self.assertEqual(seen, [(clone, "b0", "s1", "E0001", None, 10, str(driver.lane_of(claimed))),
                                    (1000.0, "done", "all of it", 60.0)])
            self.assertEqual((exp.id, exp.verdict, turns[-1]), ("E0002", "discarded", clone))  # its coder's post-mortem
            self.assertNotIn("reevaluate", json.loads(ideas.read_text().splitlines()[0]))

    # E0306 (I466, 29 Sep 08:01, MISSES 88): its coder finished, then prepare's lazy `import native` raised an
    # ImportError (a newer native.py on disk than the process's home.py) before evaluate fetched a candidate; the error
    # sent the idea back to be recoded, its commit kept only as wip/E0306. A loop code fault after coding now keeps the
    # clone's HEAD on cand/E#### and queues it for re-evaluation, and that error never counts toward retiring the idea;
    # but not a HEAD rebased since that its coder never checked (a fault in its merge turn), nor a candidate a fault
    # while promoting may have left champion.
    def test_a_loop_code_fault_after_coding_keeps_the_candidate_and_re_evaluates_it(self):
        def conflict(exp, tree):  # evaluate's rebase onto a newer champion, before its coder's merge turn
            refs["HEAD"], exp.verdict = "c2", "conflict"

        def kept(exp, tree):
            refs["cand/E0001"] = exp.candidate = "c1"
            exp.verdict = "kept"

        cases = {"prepare": {"prepare": ImportError("cannot import name 'MAC_HOSTS'"),
                             "evaluate": AssertionError("prepare raised first")},
                 "merge turn": {"evaluate": conflict, "run_resolve": TypeError("resume_coder() got 'x'")},
                 "promote": {"evaluate": kept, "promote": TypeError("push_champion() takes 0 arguments")}}
        for case, faults in cases.items():
            with self.subTest(case), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                clone, ideas = state / "clones/E0001", state / "ideas.jsonl"
                (clone / ".git").mkdir(parents=True)
                ideas.write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
                refs = {"HEAD": "c1"}  # the clone's HEAD (the commit its coder handed in) and the repo's cand/E####
                git = lambda *a, cwd=None, **k: refs.get(a[-1].removesuffix("^{commit}"), "") \
                    if a[0] == "rev-parse" and (a[-1] != "HEAD" or cwd == clone) else ""
                stubs = {"STATE": state, "STOP": state / "STOP", "RESULTS": state / "results.jsonl", "IDEAS": ideas,
                         "WORKTREES": state / "clones", "git": git,
                         "fetch_clone": lambda tree, spec: refs.update({spec[5:]: refs["HEAD"]}),
                         "protected_baseline": Mock(return_value={}), "make_worktree": Mock(return_value=clone),
                         "run_lane": lambda exp, tree: exp.session.update(session_id="s1"),
                         "own_commits": Mock(return_value=(1, False)), "evaluation_running": Mock(return_value=True),
                         "refresh_referee": Mock(return_value=True), "guard_candidate": Mock(), "prepare": Mock(),
                         "cleanup": Mock(), "start_official": Mock(), "run_postmortem": Mock(),
                         **{name: Mock(side_effect=fault) for name, fault in faults.items()}}
                with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
                    for name, stub in stubs.items():
                        stack.enter_context(patch.object(driver, name, stub))
                    exp = driver.run_experiment(1, json.loads(ideas.read_text()))
                    idea = json.loads(ideas.read_text())
                    if case == "prepare":
                        self.assertIn("ImportError", exp.session["loop_fault"])
                        self.assertEqual((exp.candidate, refs.get("cand/E0001")), ("c1", "c1"))
                        self.assertEqual((idea["status"], idea["reevaluate"]), ("todo", "E0001"))  # not recoded
                        refs["HEAD"] = "c3"  # the clone moved since: reevaluate would refuse
                        self.assertIn("moved off", driver.reevaluation_refusal(driver.result_row("E0001")))
                        driver.clear_reevaluate("I1")
                        driver.finish_idea("I1", "todo", "E0002: error (RuntimeError: another host)")
                        self.assertEqual(json.loads(ideas.read_text())["status"], "todo")  # E0001's never counts
                    else:  # an unchecked rebase, or perhaps champion already: the orchestrator's call
                        self.assertEqual((exp.candidate, "reevaluate" in idea), ("" if case == "merge turn" else "c1",
                                                                                False))
                        self.assertEqual(case == "merge turn", "loop_fault" in exp.session)
                    self.assertEqual((exp.verdict, exp.session.get("handed_in")), ("error", "c1"))

    # A re-evaluation that errors again was rebased onto a newer champion first, so its candidate is a new commit: the
    # cap counts errors along the chain of re-evaluations, or a map fault on every champion would loop it for ever.
    def test_the_re_evaluation_cap_counts_errors_along_the_chain_not_per_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "clones/E0001/.git").mkdir(parents=True)
            session = {"session_id": "s1", "clone": "E0001"}
            rows = [{"id": "E0001", "verdict": "error", "candidate": "c1", "session": session},
                    {"id": "E0002", "verdict": "error", "candidate": "c2",
                     "session": {**session, "reevaluates": "E0001"}}]
            (state / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            with patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "WORKTREES", state / "clones"), patch.object(driver, "git", return_value="c2"):
                refusal = driver.reevaluation_refusal(rows[1])
            self.assertIn("E0002, E0001) has 2 error results", refusal)

    def test_failed_clone_setup_is_recorded_instead_of_losing_the_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "ideas.jsonl").write_text(json.dumps({**IDEA, "id": "I1", "title": "test", "status": "trying:lane1"}))
            with patch.object(driver, "STATE", state), patch.object(driver, "WORKTREES", state / "clones"), \
                    patch.object(driver, "RESULTS", state / "results.jsonl"), \
                    patch.object(driver, "IDEAS", state / "ideas.jsonl"), \
                    patch.object(driver, "make_worktree", side_effect=OSError("clone failed")), \
                    contextlib.redirect_stdout(io.StringIO()):
                exp = driver.run_experiment(1, {"id": "I1", "title": "test"})
            row = json.loads((state / "results.jsonl").read_text())
            self.assertEqual(row["id"], exp.id)
            self.assertEqual(row["verdict"], "error")
            self.assertIn("clone failed", row["reason"])

    def test_a_stopped_coders_work_is_saved_before_its_idea_goes_back_to_todo(self):
        # E0161 at 13:32 on 2026-09-27: a usage limit stopped its coder; the save must come before the idea is back to
        # todo, where another process could claim it and look for the branch first, on both providers.
        def usage_limit(exp, tree):
            exp.session.update(exit=1, summary="You've hit your usage limit")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ideas = state / "ideas.jsonl"
            seen = []
            keep = lambda exp, tree: seen.append((exp.verdict, json.loads(ideas.read_text())["status"])) or []
            for provider in ("claude", "native"):
                ideas.write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
                with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                        patch.object(driver, "RESULTS", state / "results.jsonl"), patch.object(driver, "IDEAS", ideas), \
                        patch.object(driver, "WORKTREES", state / "clones"), \
                        patch.object(driver, "make_worktree", return_value=state), \
                        patch.object(driver, "protected_fingerprint", return_value="same"), \
                        patch.object(driver, "run_lane", side_effect=usage_limit), \
                        patch.object(driver, "cleanup"), patch.object(driver, "prepare_postmortem", return_value=None), \
                        patch.object(driver.health, "run", return_value=[]), \
                        patch.object(driver, "keep_wip", side_effect=keep), contextlib.redirect_stdout(io.StringIO()):
                    if provider == "claude":
                        driver.run_experiment(1, {"id": "I1", "title": "test"})
                    else:
                        (state / "runs/E0009").mkdir(parents=True, exist_ok=True)
                        native.finish(driver.Experiment(id="E0009", idea={"id": "I1"}, lane=1, verdict="blocked",
                                                        reason="provider usage limit"))
                self.assertEqual(seen.pop(), ("blocked", "trying:lane1"), provider)
            # native.finish then puts it back (the Claude driver's lane_loop does, after run_experiment returns).
            self.assertEqual(json.loads(ideas.read_text())["status"], "todo")

    def test_worker_keeps_the_last_result_when_messaging_adds_another_turn(self):
        first = {"type": "result", "result": "temporary failure", "is_error": True}
        last = {"type": "result", "result": "final handback", "is_error": False}
        for output in (json.dumps(first) + "\n" + json.dumps(last),
                       json.dumps([{"type": "system"}, first, last])):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / "runs/E0001").mkdir(parents=True)
                exp = driver.Experiment(id="E0001", idea={}, lane=1)
                process = Mock(returncode=0, pid=123)
                process.communicate.return_value = (output, "")
                with patch.object(driver, "STATE", state), patch.object(driver, "lane_prompt", return_value="task"), \
                        patch.object(driver.subprocess, "Popen", return_value=process), \
                        contextlib.redirect_stdout(io.StringIO()):
                    driver.run_lane(exp, state)
                self.assertEqual(exp.session["summary"], "final handback")
                self.assertIsNone(driver.session_failure(exp))

    def test_once_cli_rejects_multiple_writers_before_launching(self):
        with patch("sys.argv", ["driver.py", "--once", "--lanes", "2"]), \
                patch.object(driver, "lane_loop", side_effect=AssertionError("must not start")), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                driver.main()
        self.assertEqual(error.exception.code, 2)

    def test_once_cli_passes_parent_to_the_supervised_experiment(self):
        with patch("sys.argv", ["driver.py", "--once", "--parent", "bc-main-test"]), \
                patch.dict(driver.os.environ, {"BC_LOOP_HOME": driver.os.environ["BC_LOOP_HOME"]}, clear=True), patch.object(driver, "stop_requested", return_value=False), \
                patch.object(driver.maintenance, "checking", return_value=contextlib.nullcontext()), \
                patch.object(driver, "detached_start", return_value=None), \
                patch.object(driver, "lane_loop", return_value=1) as lane:
            with self.assertRaises(SystemExit) as error:
                driver.main()
        self.assertEqual(error.exception.code, 1)
        lane.assert_called_once_with(0, once=True, parent="bc-main-test")

    # REFLECT 2026-09-30 15:13 F28: lanes started with & at 14:09 and 15:22 ran with nobody reading their end. The
    # process chains are this session's own, from ps: a background Bash task under claude, then the same `uv run`
    # re-parented to launchd once its shell ended.
    def test_a_supervised_once_started_detached_is_refused_and_says_so(self):
        me = driver.os.getpid()
        row = lambda pid, ppid, args: driver.health.PsRow(pid, ppid, "u", 0, 5.0, 0.0, args)
        tracked = [row(me, 30001, "python3 loop/driver.py --once --parent bc-main-fresh"),
                   row(30001, 30028, "uv run loop/driver.py --once --parent bc-main-fresh"),
                   row(30028, 43861, "/bin/zsh -c source /Users/u/.claude/shell-snapshots/snapshot-zsh.sh"),
                   row(43861, 2059, "/Users/u/.local/bin/claude --session-id 8406d943")]
        # Independent of how the test itself runs (under nohup, or with stdout to /dev/null)
        with patch.object(driver.os, "fstat", return_value=driver.os.stat(__file__)), \
                patch.object(driver.signal, "getsignal", return_value=driver.signal.SIG_DFL):
            self.assertIsNone(driver.detached_start(tracked, settle=0))
            detached = [tracked[0], row(30001, 1, tracked[1].args)]
            self.assertIn("no claude process among its ancestors", driver.detached_start(detached, settle=0))
            # A ps hiccup (no table, or one without this process) never refuses a lane
            self.assertIsNone(driver.detached_start([], settle=0))
            self.assertIsNone(driver.detached_start(detached[1:], settle=0))
        with patch.object(driver.os, "fstat", return_value=driver.os.stat(__file__)), \
                patch.object(driver.signal, "getsignal", return_value=driver.signal.SIG_IGN):
            self.assertIn("nohup", driver.detached_start(tracked, settle=0))
        with tempfile.TemporaryDirectory() as directory, \
                patch("sys.argv", ["driver.py", "--once", "--parent", "bc-main-test"]), \
                patch.object(driver, "STATE", Path(directory)), patch.object(driver, "stop_requested", return_value=False), \
                patch.object(driver, "detached_start", return_value="SIGHUP is ignored (nohup)"), \
                patch.object(driver, "lane_loop", side_effect=AssertionError("must not start")):
            with self.assertRaises(SystemExit) as error:
                driver.main()
            self.assertIn("refused: SIGHUP is ignored", str(error.exception.code))
            self.assertIn("LANE REFUSED", (Path(directory) / "host-alerts.log").read_text())

    def test_worker_identity_is_available_while_coding_and_exit_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0001").mkdir(parents=True)
            exp = driver.Experiment(id="E0001", idea={}, lane=1,
                                    session={"parent": "bc-main-test"})
            process = Mock(returncode=0, pid=123)

            def complete(*args, **kwargs):
                worker = state / "runs/E0001/worker.json"
                self.assertTrue(worker.exists(), "parent needs worker identity before coding finishes")
                running = json.loads(worker.read_text())
                self.assertEqual(running["status"], "running")
                self.assertEqual(running["parent"], "bc-main-test")
                self.assertEqual(running["pid"], 123)
                self.assertTrue(running["name"].startswith("bc-E0001-"))
                UUID(running["session_id"])
                return '{"result":"finished","is_error":false}', ""

            process.communicate.side_effect = complete
            with patch.object(driver, "STATE", state), patch.object(driver, "lane_prompt", return_value="task"), \
                    patch.dict(driver.os.environ, {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "not-for-workers"}), \
                    patch.object(driver.subprocess, "Popen", return_value=process) as launch:
                driver.run_lane(exp, state)
            args = launch.call_args.args[0]
            self.assertEqual(args[args.index("--name") + 1], exp.session["name"])
            self.assertEqual(args[args.index("--session-id") + 1], exp.session["session_id"])
            self.assertNotIn("CLAUDECODE", launch.call_args.kwargs["env"])
            self.assertNotIn("ANTHROPIC_API_KEY", launch.call_args.kwargs["env"])
            settings = json.loads(args[args.index("--settings") + 1])
            self.assertEqual(settings["crossSessionInbound"], "accept")
            self.assertIn("SendMessage", settings["permissions"]["allow"])
            saved = json.loads((state / "runs/E0001/worker.json").read_text())
            self.assertEqual(saved["status"], "finished")
            self.assertEqual(saved["exit"], 0)

    def test_failed_launch_is_visible_without_claiming_coding_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0001").mkdir(parents=True)
            exp = driver.Experiment(id="E0001", idea={}, lane=1)
            with patch.object(driver, "STATE", state), patch.object(driver, "lane_prompt", return_value="task"), \
                    patch.object(driver.subprocess, "Popen", side_effect=OSError("cannot launch")):
                with self.assertRaises(OSError):
                    driver.run_lane(exp, state)
            worker = state / "runs/E0001/worker.json"
            self.assertTrue(worker.exists(), "launch failures need a persisted status")
            saved = json.loads(worker.read_text())
            self.assertEqual(saved["status"], "launch_failed")
            self.assertIn("cannot launch", saved["error"])

    def test_once_returns_after_one_result_even_when_more_ideas_exist(self):
        exp = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1, verdict="discarded")
        with patch.object(Path, "exists", return_value=False), \
                patch.object(driver, "claim_next", side_effect=[{"id": "I1"}, AssertionError("second claim")]), \
                patch.object(driver, "run_experiment", return_value=exp), \
                patch.object(driver, "finish_idea"), patch.object(driver, "maybe_run_meta"), \
                patch.object(driver.time, "sleep", side_effect=AssertionError("must return to parent")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(driver.lane_loop(1, once=True, parent="bc-main-test"), 0)
        self.assertIn("E0001 discarded", output.getvalue())

    def test_once_reports_a_provider_failure_without_retrying_or_sleeping(self):
        exp = driver.Experiment(id="E0001", idea={"id": "I1"}, lane=1, verdict="blocked")
        with patch.object(Path, "exists", return_value=False), \
                patch.object(driver, "claim_next", return_value={"id": "I1"}), \
                patch.object(driver, "run_experiment", return_value=exp), \
                patch.object(driver, "finish_idea"), \
                patch.object(driver.time, "sleep", side_effect=AssertionError("must return to parent")):
            self.assertEqual(driver.lane_loop(1, once=True), 1)

    def test_once_distinguishes_stop_during_claim_from_an_empty_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            def stopped_claim(lane):
                (state / "STOP").touch()
                return None
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(driver, "claim_next", side_effect=stopped_claim), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(driver.lane_loop(1, once=True), 1)
            self.assertIn("stopped", output.getvalue())

    def test_json_error_is_not_a_successful_coding_session(self):
        exp = driver.Experiment(id="E0001", idea={}, lane=1,
                                session={"exit": 0, "is_error": True, "summary": "usage limit"})
        self.assertEqual(driver.session_failure(exp), ("blocked", "provider usage limit"))

    def test_coding_session_has_no_wall_clock_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0001").mkdir(parents=True)
            exp = driver.Experiment(id="E0001", idea={}, lane=1)
            process = Mock(returncode=0, pid=123)
            # Its only timeout is the usage pause's poll (driver.pausable), retried for as long as the turn runs.
            polls = [driver.subprocess.TimeoutExpired("claude", driver.PAUSE_POLL_S)] * 3
            process.communicate.side_effect = [*polls, ('{"result":"finished"}', "")]
            with patch.object(driver, "STATE", state), patch.object(driver, "lane_prompt", return_value="task"), \
                    patch.object(driver.subprocess, "Popen", return_value=process):
                driver.run_lane(exp, state)
            self.assertEqual(process.communicate.call_count, 4)
            self.assertEqual(exp.session["summary"], "finished")

    def test_recording_an_outcome_twice_does_not_duplicate_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            exp = driver.Experiment(id="E0001", idea={}, lane=1, verdict="discarded")
            (state / "results.jsonl").write_text("\n")
            with patch.object(driver, "RESULTS", state / "results.jsonl"):
                driver.record(exp)
                driver.record(exp)
            self.assertEqual(len([line for line in (state / "results.jsonl").read_text().splitlines() if line.strip()]), 1)

    def test_cleanup_preserves_work_and_saves_uncommitted_changes_as_a_patch_that_applies(self):
        # Every non-empty runs/*/diff.patch saved by 2026-09-28 (144, E0197's 410 lines among them) lacked git's last
        # newline, so `git apply` failed with "corrupt patch" at its last line: the `git` helper strips its output.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            clone = state / "clone"
            (clone / "bot").mkdir(parents=True)
            (clone / "bot/work.cpp").write_text("int a;\nint b;\nint c;\n")
            run = lambda *args: driver.subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True,
                                                      text=True).stdout.strip()
            run("init", "-q")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            base = run("rev-parse", "HEAD")
            (clone / "bot/work.cpp").write_text("int a;\nint unfinished;\nint c;\n")
            (state / "runs/E0001").mkdir(parents=True)
            exp = driver.Experiment(id="E0001", idea={}, lane=1, base=base, verdict="discarded")
            real, elsewhere = driver.git, []

            def git(*args, **kwargs):  # the clone's diff runs; the main repo's ref deletion is only recorded
                return real(*args, **kwargs) if args[0] == "diff" else elsewhere.append(args)

            with patch.object(driver, "STATE", state), patch.object(driver, "git", side_effect=git):
                driver.cleanup(exp, clone)
            self.assertIn("unfinished", (clone / "bot/work.cpp").read_text())  # the clone keeps its work
            patch_file = state / "runs/E0001/diff.patch"
            self.assertIn("+int unfinished;", patch_file.read_text())
            run("checkout", "-q", "--", "bot/work.cpp")
            run("apply", str(patch_file))
            self.assertIn("unfinished", (clone / "bot/work.cpp").read_text())
            self.assertEqual(elsewhere, [("update-ref", "-d", "refs/prepared/E0001"),  # prepare's and prepare_ahead's refs
                                         ("update-ref", "-d", "refs/prepared-next/E0001")])

    # E0197's real session output (testdata/session-E0197-waiting.json): its coder ended "Waiting for the 24 re-run
    # games to finish; I'll be notified when they do." and was recorded as no code, marking I441 tried (MISSES 63).
    WAITING = (Path(__file__).parent / "testdata/session-E0197-waiting.json").read_text()

    # run_lane with the first session printing `first`, and each resumed turn (driver.resume_coder) printing the next
    # of `resumed`; returns the experiment and the resumed turns' prompts.
    def lane_with_turns(self, state: Path, clone: Path, first: str, resumed: list[str]):
        (state / "runs/E0197").mkdir(parents=True)
        exp = driver.Experiment(id="E0197", idea={}, lane=1)
        process = Mock(returncode=0, pid=123)
        process.communicate.return_value = (first, "")
        outputs, prompts, popen = iter(resumed), [], driver.subprocess.Popen

        def resume(exp, worktree, prompt, running=False):  # its argv and text: test_launches
            prompts.append(prompt)
            return driver.subprocess.CompletedProcess(["claude"], 0, next(outputs), "")

        def launch(command, *args, **kwargs):  # the coder; git in the clone (driver.uncommitted) runs
            return process if "claude" in command else popen(command, *args, **kwargs)

        with patch.object(driver, "STATE", state), patch.object(driver, "lane_prompt", return_value="task"), \
                patch.object(driver.subprocess, "Popen", side_effect=launch), \
                patch.object(driver, "resume_coder", side_effect=resume), \
                contextlib.redirect_stdout(io.StringIO()):
            driver.run_lane(exp, clone)
        return exp, prompts

    def test_a_coder_that_ends_waiting_for_its_own_games_is_resumed_once_to_finish(self):
        done = json.dumps({"type": "result", "result": "Committed.\n\nBlocked by: none", "is_error": False,
                           "num_turns": 5, "total_cost_usd": 0.5})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            exp, prompts = self.lane_with_turns(state, state, self.WAITING, [done])
            (prompt,) = prompts
            self.assertIn("Wait for that work now in the foreground", prompt)
            self.assertEqual(exp.session["continued"], "its final message says it is waiting for its own work")
            self.assertEqual(exp.session["summary"], "Committed.\n\nBlocked by: none")
            self.assertEqual(exp.session["turns"], 89)  # E0197's 84 and the continued turn's 5
            # A resumed session reports its running total, the whole session's cost, never added to the first's.
            self.assertAlmostEqual(exp.session["cost_usd_list_price"], 0.5)
            self.assertEqual(exp.session["review"], "subagent")  # E0197's first turn started its reviewer
            self.assertIsNone(driver.session_failure(exp))
            self.assertTrue((state / "runs/E0197/continue.json").exists())

    def test_a_coder_still_waiting_after_its_one_continue_is_an_error_not_no_code(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            exp, prompts = self.lane_with_turns(state, state, self.WAITING, [self.WAITING])
            self.assertEqual(len(prompts), 1)  # once, never a loop
            verdict, reason = driver.session_failure(exp)
            self.assertEqual(verdict, "error")  # retried, and keep_wip saves its clone's work (driver.WIP_VERDICTS)
            self.assertIn(verdict, driver.WIP_VERDICTS)
            self.assertIn("MISSES 63", reason)

    def test_a_finished_coder_is_not_resumed_but_one_with_uncommitted_work_is(self):
        done = json.dumps({"type": "result", "result": "Nothing I started is still running.\n\nBlocked by: none",
                           "is_error": False, "num_turns": 40})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            clone = state / "clone"
            (clone / "bot").mkdir(parents=True)
            (clone / "bot/a.cpp").write_text("int a;\n")
            run = lambda *args: driver.subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True)
            run("init", "-q")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            exp, prompts = self.lane_with_turns(state, clone, done, [])
            self.assertEqual(prompts, [])
            self.assertNotIn("continued", exp.session)
            (clone / "bot/a.cpp").write_text("int half_done;\n")  # E0045: "Still waiting on the quick check."
            shutil.rmtree(state / "runs")
            exp, prompts = self.lane_with_turns(state, clone, done, [done])
            self.assertEqual(len(prompts), 1)
            self.assertEqual(exp.session["continued"], "its clone has 1 uncommitted paths")

    def test_a_coder_that_hands_in_no_code_on_purpose_is_not_resumed_for_its_uncommitted_edits(self):
        # E0111's real final message (testdata/result-E0111-no-code.json): stopped by the orchestrator, it "committed
        # nothing" and left its measurement edits uncommitted, which is its answer, not cut-off work.
        said = json.loads((Path(__file__).parent / "testdata/result-E0111-no-code.json").read_text())["session"]
        done = json.dumps({"type": "result", "result": said["summary"], "is_error": False, "num_turns": 40})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            clone = state / "clone"
            (clone / "bot").mkdir(parents=True)
            (clone / "bot/params.hpp").write_text("bool deadEndFarms = true;\n")
            run = lambda *args: driver.subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True)
            run("init", "-q")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            (clone / "bot/params.hpp").write_text("bool deadEndFarms = false;\n")
            exp, prompts = self.lane_with_turns(state, clone, done, [])
            self.assertEqual(prompts, [])
            self.assertNotIn("continued", exp.session)
            self.assertIn("No code: <why>", driver.CONTINUE)  # how a resumed coder says it on purpose

    def test_the_continued_turn_records_its_own_pid_so_no_second_writer_gets_in(self):
        # worker.json kept the first claude's dead pid while the continued turn edited the clone, and native.py resume
        # lets a second writer in once that pid is gone.
        done = json.dumps({"type": "result", "result": "Committed.\n\nBlocked by: none", "is_error": False})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0197").mkdir(parents=True)
            exp = driver.Experiment(id="E0197", idea={}, lane=1,
                                    session={"session_id": "s-1", "name": "bc-E0197-s", "pid": 123})
            seen = []

            def communicate(text=None, timeout=None):
                seen.append(json.loads((state / "runs/E0197/worker.json").read_text()))
                return done, ""
            process = Mock(returncode=0, pid=456, args=["claude"])
            process.communicate.side_effect = communicate
            with patch.object(driver, "STATE", state), patch.object(driver, "lane_settings", return_value="{}"), \
                    patch.object(driver, "lane_env", return_value={}), patch.object(driver.meta, "unlock"), \
                    patch.object(driver.subprocess, "Popen", return_value=process), \
                    contextlib.redirect_stdout(io.StringIO()):
                driver.continue_turn(exp, state, "waiting", True)
            ((status, pid),) = [(w["status"], w["pid"]) for w in seen]
            self.assertEqual((status, pid), ("running", 456))

    def test_a_merge_turn_that_ends_waiting_is_resumed_once_to_finish(self):
        done = json.dumps({"type": "result", "result": "Merged and checked.", "is_error": False})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "runs/E0197").mkdir(parents=True)
            exp = driver.Experiment(id="E0197", idea={}, lane=1,
                                    session={"session_id": "s-1", "merge": "clean", "coded_at": time.time()})
            prompts = []

            def resume(exp, worktree, prompt, running=False, session_id=None, name=None, urgent=False):
                prompts.append(prompt)
                resumed.append((session_id, name))
                return driver.subprocess.CompletedProcess(["claude"], 0, [self.WAITING, done][len(prompts) - 1], "")
            resumed = []
            with patch.object(driver, "STATE", state), patch.object(driver, "resolve_prompt", return_value="merge"), \
                    patch.object(driver, "resume_coder", side_effect=resume), patch.object(driver, "git_run"), \
                    contextlib.redirect_stdout(io.StringIO()):
                driver.run_resolve(exp, state)
                self.assertEqual(len(prompts), 2)
                self.assertIn("Wait for that work now in the foreground", prompts[1])
                self.assertTrue(driver.prune.turn_saved(state / "runs/E0197/resolve.json"))
                # A fresh merge agent's waiting turn is resumed under its own name, which health.merge_turn_running
                # looks for: its session id is in no saved file until the turn ends (the review of 3b4b1199).
                exp.session.pop("coded_at")
                prompts[:] = ["the fresh agent's"]  # its resume is the second turn: it finishes
                with patch.object(driver, "fresh_merge_turn",
                                  return_value=driver.subprocess.CompletedProcess(["claude"], 0, self.WAITING, "")), \
                        patch.object(driver, "merge_brief", return_value=""):
                    driver.run_resolve(exp, state)
            fresh = exp.session["merge_turns"][-1]["session_id"]
            self.assertEqual(resumed[-1], (fresh, driver.merge_agent("E0197", fresh)))

    def test_a_candidate_whose_merge_turn_hit_a_usage_limit_leaves_the_lock_and_comes_back_in_its_place(self):
        # 29 Sep 21:37: E0355, E0356 and E0358 went stale on a usage limit instead of waiting out its 22:00 reset.
        for handed in (False, True):
            with self.subTest(handed=handed):
                self.merge_limit_turns(handed)

    # One candidate's run_experiment whose first merge turn a usage limit stops. `handed`: a process an older image
    # handed its evaluation to (hand_off), holding the lock's fd 7 already.
    def merge_limit_turns(self, handed: bool) -> None:
        entered, inside, waited, handoffs = [], [False], [], []

        @contextlib.contextmanager
        def lock(**kwargs):
            entered.append((kwargs["queue"][0], kwargs.get("inherited")))
            inside[0] = True
            try:
                yield True
            finally:
                inside[0] = False

        def evaluate(exp, worktree):
            exp.verdict = "discarded" if driver.merged_on(exp) else "conflict"

        def resolve(exp, worktree, champion=None, locked=False):
            if exp.session.get("merge_limit_retries"):
                exp.session["resolve"] = 1.0  # the retry's turn counts
            else:
                exp.session.update(merge_limit_retries=1, merge_retry_at=driver.time.time() + 600)

        def wait(exp, at, what):
            waited.append(inside[0])
            exp.session["merge_retry_at"] = 0.0  # the reset passed
            return True
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ideas = state / "ideas.jsonl"
            ideas.write_text(json.dumps({**IDEA, "id": "I1", "status": "trying:lane1"}) + "\n")
            patches = {"STATE": state, "STOP": state / "STOP", "RESULTS": state / "results.jsonl", "IDEAS": ideas,
                       "WORKTREES": state / "clones", "evaluation_lock": lock}
            returns = {"make_worktree": state, "protected_fingerprint": "same", "own_commits": (["c1"], None),
                       "git": "c1", "experiment_runtime": contextlib.nullcontext(), "refresh_referee": True,
                       "guard_candidate": None, "cleanup": None, "keep_wip": [], "run_judge": None,
                       "run_postmortem": None}
            effects = {"run_lane": lambda exp, tree: exp.session.update(coded_at=100.0), "evaluate": evaluate,
                       "run_resolve": resolve, "wait_out_limit": wait,
                       "hand_off": lambda exp: handoffs.append(exp.session.get("locked_at")) or True}
            idea = {**IDEA, "id": "I1", "title": "test"}
            with contextlib.ExitStack() as stack:
                for name, value in patches.items():
                    stack.enter_context(patch.object(driver, name, value))
                for name, value in returns.items():
                    stack.enter_context(patch.object(driver, name, return_value=value))
                for name, value in effects.items():
                    stack.enter_context(patch.object(driver, name, side_effect=value))
                stack.enter_context(patch.object(driver, "clone_dir", return_value=state))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                given = driver.Experiment(id="E0001", idea=idea, lane=1, session={"coded_at": 100.0})
                given.run_dir.mkdir(parents=True)  # saved by the process that handed it over
                exp = (driver.run_experiment(1, idea, handed=given, lock_fd=7) if handed
                       else driver.run_experiment(1, idea))
        self.assertEqual(waited, [False])  # the lock was free for the others while it waited
        # Back with its coding-done time; a handed process's fd went with its first turn's lock, so it queues again.
        self.assertEqual(entered, [(100.0, 7 if handed else None), (100.0, None)])
        # Each turn it queued for hands off to the disk's code (a handed one's first ran on it already).
        self.assertEqual(len(handoffs), 1 if handed else 2)
        self.assertEqual(exp.verdict, "discarded")  # the retried turn resolved it; evaluated as usual

    def test_a_coder_a_usage_limit_stopped_is_resumed_once_after_the_reset(self):
        # E0357, E0360-E0362 (29 Sep) were recorded blocked mid-coding and recoded from wip/E####.
        limit = json.dumps({"type": "result", "is_error": True, "total_cost_usd": 2.5,
                            "result": "You've hit your session limit · resets 10pm (Australia/Sydney)"})
        done = json.dumps({"type": "result", "is_error": False, "result": "Committed.\n\nBlocked by: none"})
        for retry, verdict in ((done, None), (limit, driver.LIMITED)):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / "runs/E0357").mkdir(parents=True)
                exp = driver.Experiment(id="E0357", idea={}, lane=1)
                process = Mock(returncode=1, pid=123)
                process.communicate.return_value = (limit.replace("2.5", "1.0"), "")
                prompts, waits = [], []
                with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                        patch.object(driver, "lane_prompt", return_value="task"), \
                        patch.object(driver.subprocess, "Popen", return_value=process), \
                        patch.object(driver, "wait_out_limit",
                                     side_effect=lambda exp, at, what: waits.append(at) or True), \
                        patch.object(driver, "resume_coder", side_effect=lambda exp, tree, prompt, **kw: (
                            prompts.append(prompt), driver.subprocess.CompletedProcess(["claude"], 0, retry, ""))[1]), \
                        patch.object(driver, "still_working", return_value=""), \
                        patch.object(driver, "unjustified_cutoffs", return_value=[]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    driver.run_lane(exp, state)
                self.assertEqual(len(waits), 1)
                self.assertIn("usage limit", prompts[0])
                self.assertEqual(driver.session_failure(exp), verdict)  # a failed retry: blocked, as before
                if verdict:  # but what the retry spent is counted
                    self.assertEqual(exp.session["cost_usd_list_price"], 2.5)

    def test_a_limit_retry_that_ends_waiting_gets_its_continue_turn_and_a_continue_turn_a_limit_stops_is_retried(self):
        # Review of the limit retry (29 Sep): a retry ending "still waiting" was recorded blocked, and a limit hitting
        # a continue turn (not the first turn) had no retry at all.
        limit = json.dumps({"type": "result", "is_error": True, "result": "You've hit your session limit · resets 10pm"})
        waiting = json.dumps({"type": "result", "is_error": False, "result": "Still waiting on the quick check."})
        done = json.dumps({"type": "result", "is_error": False, "result": "Committed.\n\nBlocked by: none"})
        for first, resumed, turns in (
                (limit, [waiting, done], [driver.LIMIT_CONTINUE[:40], driver.CONTINUE[:40]]),
                (waiting, [limit, done], [driver.CONTINUE[:40], driver.LIMIT_CONTINUE[:40]])):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / "runs/E0357").mkdir(parents=True)
                exp = driver.Experiment(id="E0357", idea={}, lane=1)
                process = Mock(returncode=int(first == limit), pid=123)
                process.communicate.return_value = (first, "")
                replies, prompts = iter(resumed), []
                with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                        patch.object(driver, "lane_prompt", return_value="task"), \
                        patch.object(driver.subprocess, "Popen", return_value=process), \
                        patch.object(driver, "wait_out_limit", return_value=True), \
                        patch.object(driver, "resume_coder", side_effect=lambda exp, tree, prompt, **kw: (
                            prompts.append(prompt[:40]),
                            driver.subprocess.CompletedProcess(["claude"], 0, next(replies), ""))[1]), \
                        patch.object(driver, "unjustified_cutoffs", return_value=[]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    driver.run_lane(exp, state)
                self.assertEqual(prompts, turns)
                self.assertIsNone(driver.session_failure(exp))

    def test_native_honors_common_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "STOP").touch()
            with patch.object(driver, "STATE", state), patch.object(driver, "STOP", state / "STOP"), \
                    patch.object(native, "STOP", state / "ASTRA_STOP"), \
                    patch.object(driver, "claim_next", side_effect=AssertionError("must not claim")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                native.claim(1)
            self.assertEqual(json.loads(output.getvalue())["status"], "stopped")


class TestUnblocking(unittest.TestCase):
    def test_a_kept_result_raises_the_waiting_ideas_that_cite_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.jsonl"
            path.write_text("\n".join(json.dumps(i) for i in [
                {**IDEA, "id": "I141", "priority": 1, "status": "trying:lane1", "hypothesis": "echo radar"},
                {**IDEA, "id": "I156", "priority": 4, "status": "todo", "hypothesis": "retry the fold after I141/I151"},
                {**IDEA, "id": "I999", "priority": 3, "status": "todo", "hypothesis": "unrelated, mentions I1410 only"}]))
            exp = driver.Experiment(id="E0080", idea={"id": "I141"}, lane=1, verdict="kept")
            with patch.object(driver, "IDEAS", path):
                self.assertEqual(driver.unblock(exp), ["I156"])
            ideas = {i["id"]: i for i in map(json.loads, path.read_text().splitlines())}
            self.assertEqual(ideas["I156"]["priority"], 3)
            self.assertIn("E0080 kept I141", ideas["I156"]["history"][-1])
            self.assertEqual(ideas["I999"]["priority"], 3)

    # The real I341 (testdata/unblock-I341.json, as it was queued: priority 2, before the history notes unblock and the
    # orchestrator's revert added): a screen port queued just after E0132 (I313) was promoted, naming E0132 as the
    # source of its score. unblock raised it to 1 at 2026-09-27 01:4x.
    def test_a_screen_port_or_a_recheck_is_not_raised(self):
        port = json.loads((Path(__file__).resolve().parent / "testdata/unblock-I341.json").read_text())
        port.update(priority=2)
        port.pop("history")
        self.assertIn("E0132", port["hypothesis"])
        exp = driver.Experiment(id="E0132", idea={"id": "I313"}, lane=1, verdict="kept", finished=1790436645.897896)
        cases = {"I341": port,
                 "I900": dict(port, id="I900", source="recheck:E0132"),
                 "I901": dict(port, id="I901", source="postmortem:E0131")}  # the same text from elsewhere may wait on it
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.jsonl"
            path.write_text("".join(json.dumps(i) + "\n" for i in cases.values()))
            with patch.object(driver, "IDEAS", path):
                self.assertEqual(driver.unblock(exp), ["I901"])
            ideas = {i["id"]: i for i in map(json.loads, path.read_text().splitlines())}
        self.assertEqual({name: idea["priority"] for name, idea in ideas.items()}, {"I341": 2, "I900": 2, "I901": 1})

    def test_an_idea_after_others_waits_until_they_are_tested(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ideas.jsonl"
            path.write_text("\n".join(json.dumps(i) for i in [
                {**IDEA, "id": "I151", "priority": 3, "status": "todo"},
                {**IDEA, "id": "I156", "priority": 1, "status": "todo", "after": ["I151"]}]))
            with patch.object(driver, "IDEAS", path):
                self.assertEqual(driver.claim_idea(1)["id"], "I151")  # the higher-priority dependent waits
                ideas = [json.loads(line) for line in path.read_text().splitlines()]
                ideas[0]["status"] = "tried"
                path.write_text("\n".join(json.dumps(i) for i in ideas))
                self.assertEqual(driver.claim_idea(2)["id"], "I156")

    # REFLECT 2026-09-30 21:15 F3: I1126 (rank 3, next) waited on I1125, unranked, which sat behind every ranked idea.
    # A prerequisite claims as its best waiter would, through a chain too.
    def test_a_prerequisite_takes_the_rank_and_next_flag_of_the_ideas_waiting_on_it(self):
        ideas = [{**IDEA, "id": "I5", "priority": 1, "rank": 1, "status": "todo"},
                 {**IDEA, "id": "I1124", "priority": 2, "status": "todo"},
                 {**IDEA, "id": "I1125", "priority": 2, "status": "todo", "after": ["I1124"]},
                 {**IDEA, "id": "I1126", "priority": 1, "rank": 3, "next": True, "status": "todo", "after": ["I1125"]},
                 {**IDEA, "id": "I7", "priority": 1, "rank": 2, "status": "todo"}]
        lifted = driver.waiter_ranks(ideas)
        self.assertEqual(lifted["I1125"], {"priority": 1, "rank": 3, "next": True})
        self.assertEqual(lifted["I1124"], lifted["I1125"])
        self.assertEqual(driver.claim_candidates(ideas, 1), ("next", [ideas[1]]))  # I1124: the one ready
        ideas[1]["status"] = "tried"
        self.assertEqual([i["id"] for i in driver.claim_sequence(ideas)], ["I1125", "I5", "I7"])
        ideas[3].pop("next")  # ranked, no longer next: by its rank among the priority 1 ideas
        self.assertEqual([i["id"] for i in driver.claim_candidates(ideas, 1)[1]], ["I5", "I7", "I1125"])

    # REFLECT 2026-09-30 21:15 F1: "in flight 0 of 7/8 aim at a portal style" for 1.5 h. Below GOAL_CLAIM_SHARE in
    # flight, a claim takes the best goal-aimed ready idea of priority 3 or better (as fillers) ahead of a better general
    # one; at the share, the usual order.
    def test_a_claim_takes_a_goal_aimed_idea_first_while_few_in_flight_aim_at_the_goal(self):
        portal = {"maps": {"portalrooms": 0.5}}
        ideas = [{**IDEA, "id": "I1", "priority": 1, "rank": 1, "status": "todo"},
                 {**IDEA, "id": "I2", "priority": 2, "status": "todo", "evaluation": portal},
                 {**IDEA, "id": "I3", "priority": 1, "status": "trying:lane1"},
                 {**IDEA, "id": "I4", "priority": 1, "status": "trying:lane2"},
                 {**IDEA, "id": "I5", "priority": 4, "status": "todo", "evaluation": portal}]
        self.assertEqual(driver.claim_candidates(ideas, 3), ("goal", [ideas[1]]))
        ideas[1]["status"] = "tried"  # only a priority-4 goal-aimed idea is left: the usual order
        self.assertEqual(driver.claim_candidates(ideas, 3)[0], "order")
        ideas[1]["status"] = "todo"
        ideas[3]["evaluation"] = portal  # 1 of 2 in flight aimed: the share is met
        self.assertEqual(driver.claim_candidates(ideas, 3)[1][0]["id"], "I1")

    def test_a_tune_is_claimed_while_a_late_screen_runs(self):
        # A late screen doesn't hold a tune back: while an evaluation runs the tune takes the filler share from it
        # (native.host_share), and otherwise both get their usual share.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "ideas.jsonl"
            path.write_text("\n".join(json.dumps(i) for i in [
                {**IDEA, "id": "I4", "priority": 1, "status": "todo", "kind": "tune", "tune": {"params": ["a"]}},
                {**IDEA, "id": "I5", "priority": 2, "status": "todo"}]))
            (state / "rescreen.json").write_text(json.dumps({"c" * 40: {"pid": os.getpid()}}))
            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state):
                self.assertEqual(driver.claim_idea(1)["id"], "I4")

    def test_fillers_go_first_while_nothing_waits_for_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "ideas.jsonl"
            def write(rows):
                path.write_text("\n".join(json.dumps(i) for i in rows))
            rows = [*({**IDEA, "id": f"I{10 + n}", "priority": 1, "status": f"trying:lane{n}"}
                      for n in range(1, driver.CODING + 1)),
                    {**IDEA, "id": "I3", "priority": 1, "status": "todo"},
                    {**IDEA, "id": "I4", "priority": 2, "status": "todo", "kind": "tune", "tune": {"params": ["a"]}},
                    {**IDEA, "id": "I5", "priority": 2, "status": "todo", "category": "retest"}]
            write(rows)
            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state):
                # every coder coding and nothing queued: a tune may take the last place
                self.assertEqual(driver.claim_idea(0)["id"], "I4")
                # with the tune running, IN_FLIGHT are in flight: nothing more
                with self.assertRaisesRegex(RuntimeError, "in flight"):
                    driver.claim_idea(0)
            rows = [{**IDEA, "id": "I1", "priority": 1, "status": "trying:lane1"},
                    {**IDEA, "id": "I3", "priority": 1, "status": "todo"},
                    {**IDEA, "id": "I5", "priority": 2, "status": "todo", "category": "retest"}]
            write(rows)
            with patch.object(driver, "IDEAS", path), patch.object(driver, "STATE", state):
                self.assertEqual(driver.claim_idea(0)["id"], "I5")  # idle: the quick retest before the priority-1 build


if __name__ == "__main__":
    unittest.main()
