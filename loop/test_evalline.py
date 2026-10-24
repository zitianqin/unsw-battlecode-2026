import testhome  # before anything that reads live paths
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

import evalline
import quota
from home import evaluating_marker, loop_home

DATA = Path(__file__).resolve().parent / "testdata/evalline"


class EvalLine(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loop_home()
        for path in self.state.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        self.run = self.state / "runs" / "E0118"
        self.run.mkdir(parents=True, exist_ok=True)
        (self.run / "experiment.json").write_text(json.dumps({"idea": {"id": "I243", "title": "Crowd-aware targets"}}))
        (self.run / "evaluation.json").write_text(json.dumps({"max_pairs": 2400}))
        (self.run / "sprt.json").write_text("{}")
        (self.run / "sprt-progress.json").write_text(json.dumps({"trials": 600}))
        self.addCleanup(evaluating_marker().unlink, missing_ok=True)

    def test_a_running_test_gets_its_rate_and_the_time_it_reaches_its_cap(self) -> None:
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": time.time() - 900}))
        started = evalline.created(self.run / "sprt-progress.json")
        progress = self.run / "sprt-progress.json"
        with progress.open("r+") as handle:  # rewritten in place as trials land: "since" must not move
            handle.write(json.dumps({"trials": 600}))
        (self.run / "sprt.json.tmp").write_text("{}")
        (self.run / "sprt.json.tmp").replace(self.run / "sprt.json")  # sprt.json is renamed into place
        text = evalline.line(self.state, started + 600)  # 600 trials in 10 minutes: 60 a minute, 30 minutes to go
        self.assertIn("Evaluating: E0118 (I243 Crowd-aware targets) since", text)
        self.assertIn("600/2400 trials, 60 trials/min, latest finish " + evalline.hhmm(started + 600 + 1800), text)
        # Games a minute come from the referee run's own figure, labelled apart: a trial is not a game (REFLECT 2011
        # F6).
        (self.state / "remote-status.json").write_text(json.dumps({"updated": started + 590, "per_minute": 131.4}))
        self.assertIn("600/2400 trials, 60 trials/min, 131 games/min, latest finish",
                      evalline.line(self.state, started + 600))
        self.assertNotIn("games/min", evalline.line(self.state, started + 590 + evalline.RECENT + 1))

    def test_a_host_with_free_slots_and_no_game_for_ten_minutes_is_named(self) -> None:
        # The live rows of E0134's screen, 2026-09-27 01:37-01:48: sz-wsl and five az-* hosts played nothing in all 12
        # while the screen played; az-hk, az-jp and az-jpw played at 01:48, mm at 01:47 (REFLECT 2011 F7).
        sample = (Path(__file__).parent / "testdata/throughput/utilisation-idle-hosts-sample.jsonl").read_text()
        (self.state / "utilisation.jsonl").write_text(sample)
        rows = [json.loads(line) for line in sample.splitlines()]
        text = evalline.line(self.state, rows[-1]["at"] + 30)
        self.assertIn("no game for 10+ min with free slots: sz-wsl (12), az-syd (12), az-mel (12), az-nz (12), "
                      "az-id (12), az-my (12)", text)
        (self.state / "utilisation.jsonl").write_text("".join(line + "\n" for line in sample.splitlines()[3:]))
        self.assertNotIn("no game for", evalline.line(self.state, rows[-1]["at"] + 30))  # 9 rows: not yet

    def test_a_decided_sprt_says_so_instead_of_a_falling_rate(self) -> None:
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": time.time() - 900}))
        (self.run / "sprt-progress.json").write_text(json.dumps({"trials": 504, "llr": 3.062, "bounds": [-2.944, 2.944]}))
        text = evalline.line(self.state, time.time())
        self.assertIn("the SPRT reached H1 (a gain) at 504 trials", text)
        # E0285's formula test (elo0 -15, elo1 0) reached H1 at 1134 trials: no worse, not a gain (REFLECT F21).
        (self.run / "evaluation.json").write_text(json.dumps({"elo0": -15, "elo1": 0, "max_pairs": 2400}))
        (self.run / "sprt-progress.json").write_text(json.dumps({"trials": 1134, "llr": 2.947,
                                                                 "bounds": [-2.944, 2.944]}))
        self.assertIn("the SPRT reached H1 (no worse) at 1134 trials", evalline.line(self.state, time.time()))
        (self.run / "cand").mkdir()
        (self.state / "results.jsonl").write_text(json.dumps({"id": "E0118", "verdict": "kept",
                                                              "finished": time.time() - 60}) + "\n")
        (self.run / "experiment.json").write_text(json.dumps({"verdict": "kept", "sprt": {
            "decision": "H1", "elo0": -15.0, "elo1": 0.0, "pairs": 1134}}))
        evaluating_marker().unlink()
        self.assertIn("final: H1 (no worse), kept", evalline.line(self.state, time.time()))

    def test_nothing_running_says_idle_and_what_is_being_coded(self) -> None:
        (self.state / "runs" / "E0121").mkdir(exist_ok=True)
        (self.state / "runs" / "E0121" / "worker.json").write_text(json.dumps({"status": "running"}))
        text = evalline.line(self.state, time.time())
        self.assertTrue(text.startswith("Idle since "), text)
        self.assertIn("last test: E0118", text)
        self.assertIn("coding: E0121", text)


    # REFLECT 2026-09-30 15:13 F23: sig asked twice for the near-Devil score mid-test. E0412's idea aimed at small and
    # near maps; worldgen, not aimed, stays off the line.
    def test_a_running_test_shows_its_aimed_styles_running_scores(self) -> None:
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": time.time() - 900}))
        (self.run / "experiment.json").write_text(json.dumps({"idea": {"id": "I1122", "title": "Middle band",
                                                                        "evaluation": {"maps": {"small": 0.25, "near": 0.25},
                                                                                       "opponents": {"rusher": 0.33}}}}))
        (self.run / "sprt-progress.json").write_text(json.dumps({"trials": 600, "per_style": {
            "near": {"trials": 150, "score": 0.472}, "small": {"trials": 149, "score": 0.51},
            "worldgen": {"trials": 120, "score": 0.5}}}))
        text = evalline.line(self.state, time.time())
        self.assertIn(", aimed small 0.51 (149 trials), near 0.47 (150 trials)", text)
        self.assertNotIn("worldgen", text)
        # REFLECT 2026-10-01 03:17 F14: E0451's line showed only its aimed styles, and misled sig and the orchestrator:
        # an SPRT line with aimed styles and no overall score or LLR fails.
        progress = json.loads((self.run / "sprt-progress.json").read_text())
        progress.update(counts=[0, 0, 10, 0, 280, 0, 10, 0, 0], llr=-0.52, bounds=[-2.944, 2.944])
        (self.run / "sprt-progress.json").write_text(json.dumps(progress))
        text = evalline.line(self.state, time.time())
        self.assertIn(", aimed small 0.51", text)
        self.assertRegex(text, r", overall 0\.50[^;]*, LLR -0\.5 of ±2\.94")

    # E0451 leaned positive (LLR +1.5 of 2.94) while its aimed styles all read near 0.50, and sig took it for level.
    # Its real sprt-progress.json at 2130 trials, trimmed.
    def test_a_running_test_shows_its_overall_score_and_llr(self) -> None:
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": time.time() - 900}))
        (self.run / "sprt-progress.json").write_text(json.dumps({
            "trials": 2130, "counts": [19, 0, 37, 0, 1990, 0, 57, 0, 27], "llr": 2.293, "bounds": [-2.944, 2.944],
            "per_opponent": {"champion": 0.522, "swarm": 0.501}, "per_style": {"near": {"trials": 533, "score": 0.501}}}))
        self.assertIn(", overall 0.50 (champion 0.52), LLR +2.3 of ±2.94", evalline.line(self.state, time.time()))
        (self.run / "sprt-progress.json").write_text(json.dumps({"trials": 12, "llr": -0.41}))  # older fields only
        text = evalline.line(self.state, time.time())
        self.assertIn(", LLR -0.4", text)
        self.assertNotIn("overall", text)

    # REFLECT 2026-09-30 15:13 F7: E0396's H0 (14:08) reached sig only when he asked; the next evaluation's line hid it.
    # E0414's recorded fields (15:16).
    def test_a_result_in_the_last_half_hour_follows_the_evaluating_line(self) -> None:
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": time.time() - 60}))
        done = self.state / "runs" / "E0414"
        (done / "cand").mkdir(parents=True)
        finished = time.time() - 120
        (done / "experiment.json").write_text(json.dumps({"verdict": "discarded", "finished": finished, "sprt": {
            "decision": "H0", "elo": -0.23255335153618537, "pairs": 1494, "elo0": 0.0}}))
        text = evalline.line(self.state, time.time())
        self.assertIn(f"; last result: E0414 ended {evalline.hhmm(finished)}, final: H0 (no gain), discarded", text)
        self.assertIn("-0.2 Elo over 1494 trials", text)
        self.assertNotIn("last result", evalline.line(self.state, finished + evalline.LAST_RESULT + 1))

    # REFLECT 2026-09-28 17:52 F13: sig read E0245's "H1" then "promising" as discarded; each verdict word says what
    # happens to the code.
    def test_the_last_results_verdict_says_what_happens_to_its_code(self) -> None:
        (self.run / "cand").mkdir()
        (self.state / "results.jsonl").write_text(json.dumps({"id": "E0118", "verdict": "promising",
                                                              "finished": time.time() - 60}) + "\n")
        (self.run / "experiment.json").write_text(json.dumps({"verdict": "promising", "target": {"line": "p 0.3"},
                                                              "sprt": {"decision": "H1"}}))
        self.assertIn("final: H1 (a gain), target p 0.3, promising (not champion; code kept on cand/E0118, its idea waits for "
                      "a retest)", evalline.line(self.state, time.time()))

    # 06:47 on 2026-09-28 printed "Idle since 06:16": the newest result was recorded but its diff.patch, written after
    # the record (driver.cleanup), wasn't yet, so the newest diff.patch was an older run's.
    def test_idle_since_is_the_last_tested_results_finish_during_a_handover(self) -> None:
        now = time.time()
        older = self.state / "runs" / "E0117"
        (older / "cand").mkdir(parents=True)
        (older / "diff.patch").write_text("")
        os.utime(older / "diff.patch", (now - 1860, now - 1860))
        (self.run / "cand").mkdir()  # E0118: recorded, its diff.patch not written yet
        untested = self.state / "runs" / "E0119"
        untested.mkdir()  # a "no code" result has no cand/ and played no test
        rows = [{"id": "E0117", "verdict": "discarded", "finished": now - 1860},
                {"id": "E0118", "verdict": "kept", "finished": now - 60},
                {"id": "E0119", "verdict": "no code", "finished": now - 30}]
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        text = evalline.line(self.state, now)
        self.assertTrue(text.startswith(f"Idle since {evalline.hhmm(now - 60)} (last test: E0118 ended "), text)
        (self.state / "results.jsonl").unlink()  # no results: the newest tested run's diff.patch, as before
        self.assertTrue(evalline.line(self.state, now).startswith(f"Idle since {evalline.hhmm(now - 1860)} "
                                                                  "(last test: E0117"))

    def test_a_stage_without_sprt_progress_is_named_with_when_it_began(self) -> None:
        # An identical idea's run folder after its identical check (E0127 at 00:05:37), before its CPU comparison.
        run = self.state / "runs" / "E0127"
        (run / "cand").mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I262", "kind": "identical",
                                                                    "title": "Log farm entries"}}))
        start = time.time() - 600
        os.utime(run / "cand", (start, start))
        for name, after in (("bot-tests.log", 16), ("evaluation.json", 62), ("identical.json", 181)):
            (run / name).write_text("{}")
            os.utime(run / name, (start + after, start + after))
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": start - 5}))
        (self.run / "worker.json").write_text(json.dumps({"status": "finished"}))  # E0118 coded, waiting
        coded = evalline.hhmm((self.run / "worker.json").stat().st_mtime)
        text = evalline.line(self.state, time.time())
        self.assertTrue(text.startswith(f"Evaluating: E0127 (I262 Log farm entries): CPU comparison since "
                                        f"{evalline.hhmm(start + 181)}; queued ahead: 1, in order: E0118 coded {coded}; "
                                        "coding: nothing"), text)

    # REFLECT 2026-09-28 14:51 F24: E0231 waited after E0236 and E0237 ran; the lock takes its tickets by name
    # (driver.first_in_queue), the oldest coded first today, and one with no ticket by worker.json's coded_at where it
    # has one.
    def test_the_queue_is_in_the_locks_order_with_what_holds_each_back(self) -> None:
        now = time.time()
        for name, coded, prepared in (("E0237", now - 60, None), ("E0231", now - 9000, {"commit": "c"}),
                                      ("E0236", now - 3000, {"commit": "c", "bot_tests": {"ok": True},
                                                             "cpu": {"ok": True}})):
            run = self.state / "runs" / name
            run.mkdir(parents=True)
            at = {"coded_at": coded} if name == "E0236" else {}  # coded_at wins over the file's time
            (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": os.getpid(), **at}))
            os.utime(run / "worker.json", (coded - 7000 if at else coded,) * 2)
            if prepared:
                (run / "prepared.json").write_text(json.dumps(prepared))
        self.assertEqual(evalline.queue_text(self.state, now, "E0118"),
                         f"queued ahead: 3, in order: E0231 coded {evalline.hhmm(now - 9000)} (preparing), "
                         f"E0236 coded {evalline.hhmm(now - 3000)}, E0237 coded {evalline.hhmm(now - 60)}")
        # The lock's tickets decide, whatever their names encode (a candidate given an earlier one goes first), and
        # one without a ticket sorts by the name it would have (the review of orch/builder-c: E0349 had none and was
        # listed last); a same-moves test goes first (driver.queue_ticket's rank), with a ticket or without.
        (self.state / "evaluation-queue").mkdir()
        for ticket in ("0000000000001.000-E0237.json", f"1-{now - 3000:017.3f}-E0236.json"):
            (self.state / "evaluation-queue" / ticket).write_text("{}")
        self.assertTrue(evalline.queue_text(self.state, now, "E0118").startswith(
            f"queued ahead: 3, in order: E0237 coded {evalline.hhmm(now - 60)}, E0231 coded {evalline.hhmm(now - 9000)} "
            f"(preparing), E0236 coded"))
        (self.state / "runs/E0236/experiment.json").write_text(json.dumps({"idea": {"kind": "identical"}}))
        (self.state / "evaluation-queue" / f"1-{now - 3000:017.3f}-E0236.json").rename(
            self.state / "evaluation-queue" / f"0-{now - 3000:017.3f}-E0236.json")
        self.assertTrue(evalline.queue_text(self.state, now, "E0118").startswith("queued ahead: 3, in order: E0236 coded"))
        self.assertIn(f"E0236 coded {evalline.hhmm(now - 3000)} (same-moves)", evalline.queue_text(self.state, now, "E0118"))
        # driver.py --first: FIRST_RANK -1 puts its own "-" in the name, once read as no numbers at all.
        (self.state / "evaluation-queue" / f"-1-{now - 60:017.3f}-E0237.json").write_text("{}")
        self.assertTrue(evalline.queue_text(self.state, now, "E0118").startswith(
            f"queued ahead: 3, in order: E0237 coded {evalline.hhmm(now - 60)} (first), E0236"))
        (self.state / "evaluation-queue" / f"-1-{now - 60:017.3f}-E0237.json").unlink()
        self.assertNotIn("preparing", evalline.queue_text(self.state, now, None))  # prepare runs only beside one
        (self.state / "STOP").write_text("")
        self.assertIn("E0237 coded", evalline.queue_text(self.state, now, "E0118"))
        self.assertEqual(evalline.queue_text(self.state, now, "E0118").count("stop marker)"), 3)  # E0236's reads "(same-moves, stop marker)"

    # REFLECT 2026-09-30 21:15 F8: E0398 and E0399 (coded 08:02 and 08:23) left the queue line at about 20:05, their
    # worker.json past throughput.WAITING_S, while their drivers still waited; and 12 h in line behind newer candidates
    # was never marked. A live ticket lists its candidate; one waiting longer than the evaluations ahead take starves.
    def test_every_live_ticket_is_listed_and_a_starving_one_marked(self) -> None:
        import meta
        now = time.time()
        for name, spent in (("E0425", 1140), ("E0431", 1500), ("E0433", 60)):  # E0433 an error: minutes, not counted
            run = self.state / "runs" / name
            (run / "cand").mkdir(parents=True)
            (run / "experiment.json").write_text(json.dumps({
                "verdict": "error" if name == "E0433" else "discarded", "finished": evalline.created(run / "cand") + spent}))
        self.assertEqual(evalline.median_evaluation(self.state, now), 1500)
        from unittest.mock import patch
        os.utime(self.state / "runs/E0431/experiment.json", (now - 60,) * 2)  # newest first, E0433 not a tested one
        os.utime(self.state / "runs/E0425/experiment.json", (now - 120,) * 2)
        with patch.object(evalline, "MEDIAN_OF", 1):
            self.assertEqual(evalline.median_evaluation(self.state, now), 1500)
        run = self.state / "runs/E0398"
        run.mkdir(parents=True)
        (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": os.getpid(),
                                                     "coded_at": now - 13.6 * 3600}))
        os.utime(run / "worker.json", (now - 13.6 * 3600,) * 2)
        self.assertEqual(evalline.queue_text(self.state, now, "E0118"), "queued ahead: 0")  # past WAITING_S, no ticket
        (self.state / meta.QUEUE).mkdir()
        (self.state / meta.QUEUE / f"1-{now - 13.6 * 3600:017.3f}-E0398.json").write_text(json.dumps({
            "pid": os.getpid(), "started": meta.process_started(os.getpid()), "experiment": "E0398"}))
        self.assertEqual(evalline.live_tickets(self.state), ["E0398"])
        self.assertEqual(evalline.queue_text(self.state, now, "E0118"),
                         f"queued ahead: 1 (~25 min), in order: E0398 coded {evalline.hhmm(now - 13.6 * 3600)} (starving: waited 13.6 h, "
                         "the 1 ahead take ~0.4 h)")
        self.assertNotIn("starving", evalline.queue_text(self.state, now - 13.1 * 3600, "E0118"))  # 0.5 h: not yet

    # REFLECT 2026-09-30 21:15 F6: "what are the eval results so far for the evals since i last asked", four times.
    def test_results_since_a_time_list_each_with_its_verdict_and_range(self) -> None:
        now = time.time()
        rows = {"E0425": (now - 7000, {"verdict": "discarded", "sprt": {"decision": "H0", "elo0": 0.0, "pairs": 1050,
                                                                        "elo": -1.65, "elo_lo": -8.0, "elo_hi": 4.7}}),
                "E0426": (now - 3000, {"verdict": "kept", "sprt": {"decision": "inconclusive", "pairs": 2400,
                                                                   "elo": 3.76, "elo_lo": 0.02, "elo_hi": 7.5,
                                                                   "limit_kept": {"of": "probability"}}}),
                "E0434": (now - 1200, {"verdict": "no code"})}
        for name, (finished, row) in rows.items():
            run = self.state / "runs" / name
            run.mkdir(parents=True)
            (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I1", "title": "t " + name}, **row,
                                                             "finished": finished}))
        text = evalline.results_since(self.state, now - 3600)
        self.assertEqual(text.splitlines()[0], f"E0426 (I1 t E0426) ended {evalline.hhmm(now - 3000)}: kept on "
                                               "probability, kept (the new champion), +3.8 Elo [+0.0, +7.5] over 2400 "
                                               "trials")
        self.assertTrue(text.splitlines()[1].startswith(f"E0434 (I1 t E0434) ended {evalline.hhmm(now - 1200)}: no code"))
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(evalline.results_since(self.state, now), f"no results since {evalline.hhmm(now)}")
        clock = time.strftime("%H:%M", time.localtime(now - 3600))
        self.assertLessEqual(abs(evalline.since_time(clock, now) - (now - 3600)), 60)

    # REFLECT 2026-09-30 21:15 F4: the watcher (its private copy, as ps showed it at 21:23) is a field of the agents line.
    def test_the_alerts_watcher_is_a_field_of_the_agents_line(self) -> None:
        import health
        rows = [health.PsRow(431, 1, "u", 0, 1500.0, 0.0, "/bin/zsh -c ... bash tools/orch/watch.sh' < /dev/null"),
                health.PsRow(438, 431, "u", 0, 1400.0, 0.0,
                             "bash /var/folders/3d/tqt777y96hg63_9p0kk8j6d40000gn/T//watch.x03JTb")]
        now = time.time()
        self.assertEqual(evalline.watcher_text(rows, now), f"watcher: running (pid 438 since {evalline.hhmm(now - 1400)})")
        self.assertEqual(evalline.watcher_text(rows[:1], now), "watcher: not running; re-arm tools/orch/watch.sh")
        self.assertIn("agents: none; watcher: not running", evalline.line(self.state, now, watcher=evalline.watcher_text(
            rows[:1], now)))

    def test_a_screen_after_a_capped_sprt_names_the_screen_from_its_start(self) -> None:
        # The SPRT ran to its cap undecided (its progress file still changing minutes ago), then the screen began at
        # sprt.json's time; a pool file finished since must not move "since", nor the SPRT's line come back.
        start = time.time() - 600
        (self.run / "cand").mkdir()
        os.utime(self.run / "cand", (start, start))
        progress = self.run / "sprt-progress.json"
        progress.write_text(json.dumps({"trials": 2400, "llr": 1.2, "bounds": [-2.944, 2.944]}))
        for name, after in (("bot-tests.log", 20), ("evaluation.json", 80), (progress.name, 280),
                            ("sprt.json", 290), ("pool-v0004-candidate.jsonl", 500)):
            if not (self.run / name).exists():
                (self.run / name).write_text("{}")
            os.utime(self.run / name, (start + after, start + after))
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": start - 5}))
        text = evalline.line(self.state, time.time())
        self.assertTrue(text.startswith(f"Evaluating: E0118 (I243 Crowd-aware targets): screen since "
                                        f"{evalline.hhmm(start + 290)}; the SPRT ended at 2400 trials: no decision"),
                        text)

    def test_the_setup_before_a_bots_export_names_its_experiment(self) -> None:
        # E0158 took the lock at 12:35 on 2026-09-27 and exported its bot seconds later; before the export the
        # utilisation rows read "setup" with no experiment (12:21, 13:25, 14:07).
        since = time.time() - 120
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": since}))
        run = self.state / "runs" / "E0158"
        run.mkdir()
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I401", "title": "Quick fix"}}))
        (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": os.getpid()}))
        text = evalline.line(self.state, time.time())
        self.assertTrue(text.startswith(f"Evaluating: E0158 (I401 Quick fix): setup since {evalline.hhmm(since)}"), text)
        # Its bot exported and its CPU gate done, before the SPRT's first trial lands.
        (run / "cand").mkdir()
        for name in ("bot-tests.log", "evaluation.json"):
            (run / name).write_text("{}")
        self.assertIn("Evaluating: E0158 (I401 Quick fix): SPRT since", evalline.line(self.state, time.time()))
        self.assertIn(", no trial yet", evalline.line(self.state, time.time()))
        # The lock held for other work (a referee refresh before a tune): no candidate of its own.
        shutil.rmtree(run)
        old = time.time() - 3600
        os.utime(self.run / "sprt-progress.json", (old, old))
        self.assertTrue(evalline.line(self.state, time.time()).startswith(
            f"Evaluating: the evaluation lock is held since {evalline.hhmm(since)} with no bot exported"))
        # 29 Sep: E0349's judge-keep screen held the lock and a waiting candidate's fresh export showed instead.
        (self.state / "runs/E0351/cand").mkdir(parents=True)
        (self.state / "runs/E0349").mkdir()
        (self.state / "runs/E0349/experiment.json").write_text(json.dumps({"idea": {"id": "I551", "title": "Doors"}}))
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": since, "rejudge": "E0349",
                                                   "judged": True}))
        self.assertTrue(evalline.line(self.state, time.time()).startswith(
            f"Evaluating: E0349 (I551 Doors): judge keep since {evalline.hhmm(since)} (its skipped gates and screen, "
            "then promotion)"))

    def test_the_late_screen_and_the_fillers_playing_are_named(self) -> None:
        self.assertIn("fillers: none", evalline.line(self.state, time.time()))
        (self.state / "sweep.json").write_text(json.dumps({"pid": os.getpid()}))  # a live run
        (self.state / "advmaps.json").write_text(json.dumps({"pid": None, "finished_at": 1.0}))
        started = time.time() - 600
        (self.state / "rescreen.json").write_text(json.dumps(
            {"0e962ec": {"experiment": "E0141", "pid": None, "done": {}},
             "1eae05f": {"experiment": "E0157", "pid": os.getpid(), "started_at": started, "done": {"boxed": {}}}}))
        text = evalline.line(self.state, time.time())
        self.assertIn(f"late screen: E0157's champion since {evalline.hhmm(started)} (1 entries done)", text)
        self.assertIn("fillers: sweep;", text)  # not the finished adversarial maps

    # REFLECT 2026-09-29 20:42 F15 and F23: the agents line lists the orchestrator's Agent calls with no result yet,
    # read from its transcript as Claude Code writes it (a background agent's launch note is no result; its stop
    # notice is), and the 5-hour usage share tools/usage_statusline.py recorded.
    def test_the_orchestrators_agents_and_usage_get_their_own_line(self) -> None:
        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(evalline.REPO / "tools"))
        now = time.time()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 60))
        (self.state / "orchestrator-session").write_text("s1\n")
        transcript = self.state / "s1.jsonl"

        def call(tool_id: str, what: str) -> dict:
            return {"type": "assistant", "timestamp": stamp, "message": {"content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {"description": what}}]}}

        def result(tool_id: str, text: str) -> dict:
            return {"type": "user", "timestamp": stamp, "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}]}]}}

        rows = [call("toolu_a", "Review orch/builder-c"), result("toolu_a", "Async agent launched successfully."),
                call("toolu_b", "Read one file"), result("toolu_b", "It says 3."),
                call("toolu_c", "Fix round"), result("toolu_c", "Async agent launched successfully.\nagentId: a400e"),
                # a notice naming only the agent, queued while a turn ran (29 Sep 06:33:30's)
                {"type": "attachment", "timestamp": stamp, "attachment": {"type": "queued_command", "prompt":
                 "<task-notification>\n<task-id>a400e</task-id>\n<output-file>o</output-file>\n"
                 "<status>completed</status>\n</task-notification>"}},
                {"type": "user", "timestamp": stamp, "origin": {"kind": "task-notification"}, "message": {"content":
                 "<task-notification>\n<task-id>x</task-id>\n<tool-use-id>toolu_z</tool-use-id>\n<output-file>o"
                 "</output-file>\n<status>completed</status>\n</task-notification>"}}]
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with patch("my_messages.PROJECT", self.state):
            self.assertEqual(evalline.line(self.state, now).splitlines()[-1],
                             f"agents: Review orch/builder-c since {evalline.hhmm(now - 60)}")
            self.assertGreater(json.loads((self.state / evalline.AGENTS).read_text())["offset"], 0)  # read on from
            with transcript.open("a") as handle:
                handle.write(json.dumps({**rows[-1], "message": {"content": rows[-1]["message"]["content"].replace(
                    "toolu_z", "toolu_a")}}) + "\n")
            self.assertNotIn("agents:", evalline.line(self.state, now))
            (self.state / evalline.USAGE).write_text(json.dumps({"used_percentage": 52.4, "resets_at": now + 3600,
                                                                 "at": now - 30}))
            self.assertEqual(evalline.line(self.state, now).splitlines()[-1],
                             f"agents: none; 5 h usage 52%, resets {evalline.hhmm(now + 3600)}")
            self.assertNotIn("agents:", evalline.line(self.state, now + 3601))  # its window has reset
            # The usage pause (loop/quota.py) names itself and the coders it holds.
            (self.state / quota.LEVEL).write_text(json.dumps({"level": "95%", "rank": quota.PAUSE,
                                                              "resets_at": now + 3600, "since": now - 120}))
            (self.state / "runs/E0401").mkdir(parents=True)
            (self.state / "runs/E0401/worker.json").write_text(json.dumps({"status": "paused"}))
            self.assertEqual(evalline.line(self.state, now).splitlines()[-1],
                             f"agents: none; 5 h usage 52%, resets {evalline.hhmm(now + 3600)}; level 95% since "
                             f"{evalline.hhmm(now - 120)}: {quota.WAITS[quota.PAUSE]} (coders paused: E0401)")

    # REFLECT 2026-09-28 20:52 F11 and F18. A test held by tools/gated_test.sh (a row of the live process table of 28
    # Sep ~16:00, test_health.MacWaits, waiting 14 min) joins the agents line; zz-wsl, part_time and so never alerted,
    # went down unmentioned: named once per outage while a test runs, the always-on az-jpw left to its alert.
    def test_a_waiting_test_and_a_part_time_host_going_down_are_named(self) -> None:
        import health
        ps = ("11965 29094 zitianqin 0 14:00 1.2 /bin/bash /Users/me/Dev/unsw-battlecode-2026/tools/gated_test.sh "
              ".venv/bin/python loop/bot_tests.py --bot bot --tests tests/bot\n"
              "11990 11965 zitianqin 0 00:05 0.0 sleep 20\n")
        rows = health.ps_rows(ps)
        self.assertEqual([r.pid for r in health.gate_waiting(rows)], [11965])
        waits = evalline.gate_waits(rows)
        now = time.time()
        self.assertTrue(evalline.line(self.state, now, waits).endswith(waits))
        evaluating_marker().write_text(json.dumps({"pid": os.getpid(), "since": now - 900}))
        (self.state / "remote.json").write_text(json.dumps({"hosts": [{"name": "zz-wsl", "part_time": True},
                                                                      {"name": "az-jpw"}]}))
        (self.state / "host-outages.json").write_text(json.dumps({"zz-wsl": now - 600, "az-jpw": now - 900}))
        shown = lambda: json.loads((self.state / evalline.SHOWN).read_text())  # noqa: E731
        evalline.line(self.state, now)  # the status line names it (down_once)
        self.assertEqual(shown(), {"zz-wsl": now - 600})
        self.assertFalse(evalline.down_once(self.state, now))  # once
        (self.state / "host-outages.json").write_text(json.dumps({"zz-wsl": now}))  # it came back and went down again
        self.assertTrue(evalline.down_once(self.state, now))
        self.assertEqual(shown(), {"zz-wsl": now})
        self.assertEqual(health.evalline_outages(self.state), [])  # its health check: quiet on a good file, not a bad
        (self.state / evalline.SHOWN).write_text("[1]")
        self.assertEqual([a.key for a in health.evalline_outages(self.state)], ["evalline outages"])


    # REFLECT 2026-09-29 17:32 F4: sig asked three times in two hours whether the native judge and the faster engine
    # were in use. enabled.json's shape is the live file's of 29 Sep 18:12, trimmed: this Mac with no gate of its own
    # goes on its Mac hosts' (run.native_gated), and the Linux group's gate failed.
    def test_the_line_says_how_games_play(self) -> None:
        from unittest.mock import patch
        now = time.time()
        self.assertIn("; judge: native off (run.NATIVE_JUDGE), engine cache on, fork on", evalline.line(self.state, now))
        (self.state / "referee/referee").mkdir(parents=True)
        (self.state / "referee/referee/run.py").write_text("FORK_CHECK_EVERY = 200\nNATIVE_JUDGE = True\n")
        self.assertIn("judge: native off (its key can't be read", evalline.engine_mode(self.state, now))
        key = "579a6ad16cf2ab97-cd6f852768e80fe9-1fd0d3103ba28f57-215772dad6f22e37-5555ea208504d09c"
        live = patch.object(evalline, "live_key", return_value=key)
        live.start()
        self.addCleanup(live.stop)
        mac = patch.object(evalline, "PLATFORM", "Darwin arm64")
        mac.start()
        self.addCleanup(mac.stop)
        self.assertIn("judge: native on, no places listed yet,", evalline.engine_mode(self.state, now))
        (self.state / "native-judge").mkdir()
        (self.state / "native-judge/enabled.json").write_text(json.dumps({
            "keys": {key: {"Darwin arm64 host": {"key": key, "platform": "Darwin arm64 host", "passed": True},
                           "Linux x86_64 f67bc685": {"key": key, "platform": "Linux x86_64 f67bc685",
                                                     "passed": False}},
                     "an-older-key": {"Linux x86_64 f67bc685": {"passed": True}}},
            "wanted": {"key": key, "at": now - 600, "hosts": {"mm": "Darwin arm64 host", "ll": "Linux x86_64 f67bc685"},
                       "places": {"Darwin arm64": now - 9000, "Darwin arm64 host": now - 9000,
                                  "Linux x86_64 f67bc685": now - 9000}}}))
        (self.state / "native-judge-checks.json").write_text(json.dumps({"checks": 115, "identical": 115}))
        (self.state / "fork-server-checks.json").write_text(json.dumps({"checks": 31828, "off": {}}))
        self.assertEqual(evalline.engine_mode(self.state, now), "judge: native 2/3 places, engine cache on, fork on")
        # The review of orch/builder-c: the inputs run.native_plan uses. Only this Mac's own place goes on its Mac
        # hosts' gate; a key other than the live one counts for nothing, and a key a check found differing is off.
        with patch.object(evalline, "PLATFORM", "Linux x86_64"):
            self.assertIn("native 1/3 places,", evalline.engine_mode(self.state, now))
        with patch.object(evalline, "live_key", return_value="a-newer-key"):
            self.assertIn("native 0/3 places (no gate for the live key yet),", evalline.engine_mode(self.state, now))
        (self.state / "native-judge-checks.json").write_text(json.dumps({"off": {}, "differed_keys": [key]}))
        self.assertIn("native off (a check found its key differing", evalline.engine_mode(self.state, now))
        (self.state / "native-judge-checks.json").write_text(json.dumps({"off": {"abc": {"said": "differed"}}}))
        (self.state / "engine-cache-off.json").write_text(json.dumps({"at": now - 60, "reason": "another toolkit"}))
        (self.state / "fork-server-off.json").write_text(json.dumps({"at": now - 60, "reason": "no compiler"}))
        self.assertEqual(evalline.engine_mode(self.state, now), "judge: native off (a check switched it off: "
                         f"native-judge-checks.json), engine cache off, fork on (a run fell back {evalline.hhmm(now - 60)})")
        (self.state / "fork-server-checks.json").write_text("{not json")  # unreadable: run.forks_off forks nothing
        (self.state / "native-judge-checks.json").write_text("[]")
        self.assertEqual(evalline.engine_mode(self.state, now), "judge: native off (a check switched it off: "
                         "native-judge-checks.json), engine cache off, fork off (a check mismatched: "
                         "fork-server-checks.json)")

    def test_odd_files_never_crash_the_line(self) -> None:
        # The status line opens every report to sig, so a hand-edited or damaged file must not stop it: the review's
        # fuzz cases on 2026-09-27 (a string or fractional pid, pid -1, which os.kill(-1, 0) takes as every process,
        # `done` not a dict, a start past time_t).
        pid = os.getpid()
        cases = {"rescreen.json": ["[]", '{"a": "text"}', '{"a": {"pid": "123"}}', '{"a": {"pid": -1}}',
                                   f'{{"a": {{"pid": {pid}, "started_at": "x"}}}}', f'{{"a": {{"pid": {pid}, "done": 5}}}}',
                                   f'{{"a": {{"pid": {pid}}}, "b": {{"pid": {pid}, "started_at": 1e20}}}}'],
                 "sweep.json": ['{"pid": "123"}', '{"pid": 1.5}', '{"pid": -1}', '{"pid": true}', '{"pid": 1e30}', "[1]"],
                 "orchestrator-agents.json": ['{"a": 1}', '[{"name": "x", "started": "y"}]', '[{"name": 1}]',
                                              '[1, "a"]', '[{"name": "x", "what": null, "started": 1e20}]',
                                              f'[{{"name": "x", "what": 5, "started": {time.time()}}}]']}
        for name, texts in cases.items():
            for text in texts:
                with self.subTest(file=name, text=text):
                    (self.state / name).write_text(text)
                    line = evalline.line(self.state, time.time())
                    (self.state / name).unlink()
                    if name == "sweep.json" or '"pid": -1' in text or '"pid": "123"' in text:
                        self.assertNotIn("late screen:", line)  # no pid there is a live run
                        self.assertIn("fillers: none", line)
        for text in ('{"pid": "12", "since": "abc"}', f'{{"pid": {pid}, "since": 1e20}}', "[]"):
            with self.subTest(marker=text):
                evaluating_marker().write_text(text)
                evalline.line(self.state, time.time())

    # REFLECT 2026-09-30 09:12 F7: the work in flight drifted from sig's portal goal to king length unsaid. The mixes are
    # 30 Sep's: E0406's declared (I1073), E0405's frozen general shares only (I1079, no mix of its own), and a coder's
    # own portalrooms share above its general one.
    def test_the_goal_line_counts_work_whose_mix_aims_at_the_goal(self) -> None:
        import meta
        general = meta.general_shares()
        for run, record in (("E0406", {"idea": {"evaluation": {"maps": {"near": 0.25, "sealedstart": 0.25}}}}),
                            ("E0405", {"idea": {}, "evaluation": {"maps": general}}),
                            ("E0407", {"idea": {}, "evaluation": {"maps": {**general, "portalrooms": 0.25}}})):
            (self.state / "runs" / run).mkdir()
            (self.state / "runs" / run / "worker.json").write_text(json.dumps({"status": "running"}))
            (self.state / "runs" / run / "experiment.json").write_text(json.dumps(record))
        ideas = [{"id": "I1103", "status": "todo", "priority": 2, "title": "a",
                  "evaluation": {"opponents": {"crowder": 0.25}}},
                 {"id": "I1079", "status": "todo", "priority": 1, "title": "b",
                  "evaluation": {"maps": {"portalrooms": 0.5}}},
                 {"id": "I922", "status": "todo", "priority": 1, "title": "c", "evaluation": {"maps": {"big": 0.4}}}]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        lines = evalline.line(self.state, time.time()).splitlines()  # top 2: driver.ready_ideas takes two of the three here
        self.assertIn(f"goal: {evalline.GOAL}: in flight 2 of 3 aim at a goal style (E0406, E0407); top 2 queued 1 "
                      "(I1079)", lines)

    # REFLECT 2026-09-30 15:13 F2: I1108 jumped the 11:10 re-rank (I970 first, I1096 last) unseen. I1118, marked next
    # but without a rank, comes after the ranked ones, so it isn't out of order.
    def test_an_idea_claimed_ahead_of_sigs_ranking_is_marked(self) -> None:
        import meta
        ideas = [{"id": "I970", "status": "todo", "priority": 1, "next": True, "rank": 1, "title": "in-view pearls"},
                 {"id": "I1096", "status": "todo", "priority": 1, "next": True, "rank": 15, "title": "feed and hold"},
                 {"id": "I1108", "status": "todo", "priority": 1, "next": True, "rank": 0.5, "title": "portal fix"},
                 {"id": "I1118", "status": "todo", "priority": 1, "next": True, "title": "later"}]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        self.assertNotIn("out of sig's ranking", evalline.line(self.state, time.time()))  # no ranking recorded
        at = time.time() - 3600
        (self.state / meta.RANKING).write_text(json.dumps({"at": at, "source": "sig, 30 Sep",
                                                          "order": ["I970", "I1096"]}))
        self.assertIn(f"; out of sig's ranking of {evalline.hhmm(at)}: I1108 ahead of I970",
                      evalline.line(self.state, time.time()))
        self.assertIn("1. I1108 (out of sig's ranking, ahead of I970, next): portal fix",
                      evalline.queue_report(self.state, time.time()))
        # A note from before the ranking, or an automatic one, doesn't say why; one since it (--out-of-rank's) does
        stamp = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
        ideas[2]["history"] = [f"orchestrator {stamp(at - 600)}: portal first",
                               f"orchestrator {stamp(at + 60)}: automatic at claim (MISSES 90): map mix {{}}"]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        self.assertIn("I1108 ahead of I970", evalline.line(self.state, time.time()))
        ideas[2]["history"].append(f"orchestrator {stamp(at + 60)}: placed next with --out-of-rank, overriding "
                                   "sig's ranking of 11:10: the Trauma fix sig asked for")
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        self.assertNotIn("out of sig's ranking", evalline.line(self.state, time.time()))

    def test_a_champion_not_yet_live_gets_a_second_line(self) -> None:
        # publishing.json and uploads.jsonl as they were at 00:5x on 2026-09-27 (loop/testdata/evalline): the day's 12
        # uploads spent, the oldest at 02:51 the day before. The champion commits are this test's own repo's.
        repo = self.state / "repo"
        repo.mkdir()
        git = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout
        git("init", "-q")
        for n in (1, 2):
            git("commit", "-q", "--allow-empty", "-m", str(n))
        live, champion = git("rev-parse", "HEAD~1").strip(), git("rev-parse", "HEAD").strip()
        git("tag", "v0032", live)
        git("tag", "v0034", champion)
        git("branch", "loop/champion", champion)
        report = json.loads((DATA / "publishing.json").read_text())
        report["verified"]["commit"] = live
        (self.state / "publishing.json").write_text(json.dumps(report))
        (self.state / "site-versions.json").write_text(json.dumps({live[:7]: report["verified"]["site_version"]}))
        # The real rows, moved 5 minutes apart so the site's 12 an hour are all spent.
        rows = [json.loads(line) for line in (DATA / "uploads.jsonl").read_text().splitlines()]
        oldest = min(row["at"] for row in rows)
        rows = [{**row, "at": oldest + 300 * n} for n, row in enumerate(rows)]
        (self.state / "uploads.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        now = oldest + 3600 - 60
        text = evalline.live_line(self.state, now, repo)
        self.assertEqual(text, f"live: v0032 (site v31); waiting to upload: v0034 (not on the site), next slot "
                               f"{evalline.hhmm(oldest + 3600)}")
        self.assertNotIn("next slot", evalline.live_line(self.state, oldest + 3600 + 1, repo))
        git("branch", "-f", "loop/champion", live)
        self.assertIsNone(evalline.live_line(self.state, now, repo))
        # sig's pick held for the Sprint: shown even while the champion is live.
        (self.state / "PUBLISH_HOLD").write_text(json.dumps({"version": "v0032", "until": "2099-01-01T12:00+10:00"}))
        self.assertEqual(evalline.live_line(self.state, now, repo),
                         f"live: v0032 (site v31); held: v0032 until {evalline.hhmm(4070916000)}")



class Eta(unittest.TestCase):
    """evalline.eta: an idea's claim and result times from its place in claim order and the last day's results."""

    def setUp(self) -> None:
        self.state = loop_home()
        for path in self.state.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        self.now = time.time()
        ideas = [{"id": "I10", "status": "todo", "priority": 2, "title": "b"},
                 {"id": "I11", "status": "todo", "priority": 1, "title": "a"},
                 {"id": "I12", "status": "todo", "priority": 3, "title": "c", "next": True},
                 {"id": "I13", "status": "todo", "priority": 1, "title": "d", "after": ["I14"]},
                 {"id": "I14", "status": "trying:lane1", "priority": 1, "title": "e"}]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        rows = []
        for n, (hours_ago, verdict, took) in enumerate([(2, "kept", 2.0), (5, "no code", 0.1), (8, "discarded", 1.0),
                                                       (30, "kept", 9.0)]):  # the last is outside the day
            (self.state / f"runs/E{n:04d}").mkdir(parents=True)
            (self.state / f"runs/E{n:04d}/experiment.json").write_text("{}")
            finished = self.now - hours_ago * 3600
            rows.append({"id": f"E{n:04d}", "verdict": verdict, "started": finished - took * 3600,
                         "finished": finished})
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_place_rate_and_the_median_tested_result_time(self) -> None:
        text = evalline.eta(self.state, "I10", self.now)
        # I12 is marked next, I11 outranks I10: third of three ready; 4 results in 24 h, the day-old one included by
        # its folder's mtime but not its finish: 3, so 0.125 an hour with no utilisation record (the whole day),
        # claimed in 24 h; tested spans 1 and 2 h.
        self.assertIn("I10 is 3 of 3 ready in claim order", text)
        self.assertIn("0.1 results an hour over the 24.0 h the loop ran in the last 24 h (3 results), so claimed "
                      "in 24.0 h at the earliest", text)
        self.assertIn("its result about 1.5 h after (the median claim to result of the 2 tested", text)  # of 1 and 2
        # The loop ran 6 h of the day (a utilisation row a minute): 3 results in 6 h.
        (self.state / "utilisation.jsonl").write_text("".join(
            json.dumps({"at": self.now - 60 * n}) + "\n" for n in range(1, 361)))
        self.assertIn("0.5 results an hour over the 6.0 h the loop ran", evalline.eta(self.state, "I10", self.now))
        self.assertEqual(evalline.eta(self.state, "I13", self.now),
                         "I13: not ready to claim: waiting on a prerequisite or a condition (driver.ready_ideas)")
        self.assertEqual(evalline.eta(self.state, "I14", self.now), "I14: not ready to claim: trying:lane1")

    # REFLECT 2026-09-29 17:32 F4: "giv eme the full p1 list in order", "whast int he eval queue, currnelty beign
    # evaled", "how long will all of p1 take to clear", each answered by hand.
    def test_the_queue_report_lists_p1_in_claim_order_and_its_flow(self) -> None:
        then = [{"id": "I11", "status": "todo", "priority": 1, "title": "a"},
                {"id": "I15", "status": "todo", "priority": 1, "title": "claimed since"},
                {"id": "I16", "status": "todo", "priority": 1, "title": "merged since"},
                {"id": "I17", "status": "todo", "priority": 1, "title": "lowered since"}]
        snapshot = self.state / "meta" / str(int((self.now - 7 * 3600) * 1e9))
        snapshot.mkdir(parents=True)
        (snapshot / "before.jsonl").write_text("".join(json.dumps(i) + "\n" for i in then))
        recent = self.state / "meta" / str(int((self.now - 1800) * 1e9))  # too new to measure a flow over
        recent.mkdir()
        (recent / "before.jsonl").write_text("")
        text = evalline.queue_report(self.state, self.now)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Under test: Idle since "), text)
        self.assertEqual(lines[1], "Evaluation queue: empty")
        # Now: I11 stayed; I13 is new at P1 (waiting on I14); I15-I17 left. 1 in, 3 out over 7 h: 2 waiting, 1 ready.
        self.assertEqual(lines[2], f"P1: 2 waiting (1 ready, 1 on a prerequisite or condition); over the 7.0 h since the "
                                   f"{time.strftime('%a %H:%M', time.localtime(self.now - 7 * 3600))} review snapshot 1 "
                                   "came in and 3 went out (claimed, merged, dropped or lowered; sign p 0.31): net -0.3 "
                                   "an hour, clears in about 7 h "
                                   f"(~{time.strftime('%a %H:%M', time.localtime(self.now + 7 * 3600))}) if both rates hold")
        # I12, marked next, goes first whatever its priority; I10 (P2) isn't listed.
        self.assertEqual(lines[4:], ["  1. I12 (P3, next): c", "  2. I11: a",
                                     "Waiting on a prerequisite or condition (driver.ready_ideas):", "  I13 waits I14: d"])
        now = [*then, {"id": "I20", "status": "todo", "priority": 1, "title": "x"}]  # 1 in, none out
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in now))
        self.assertIn("net +0.1 an hour, not shrinking", evalline.queue_report(self.state, self.now))

    # A P2 prerequisite (I10) that a P1 idea waits on claims at P1 (driver.waiter_ranks), so the P1 list shows it.
    def test_the_queue_report_counts_a_prerequisite_at_its_waiters_priority(self) -> None:
        ideas = [{"id": "I10", "status": "todo", "priority": 2, "title": "b"},
                 {"id": "I11", "status": "todo", "priority": 1, "title": "a", "rank": 1},
                 {"id": "I13", "status": "todo", "priority": 1, "title": "d", "after": ["I10"]}]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        lines = evalline.queue_report(self.state, self.now).splitlines()
        self.assertTrue(lines[2].startswith("P1: 3 waiting (2 ready, 1 on a prerequisite or condition)"), lines[2])
        self.assertEqual(lines[4:6], ["  1. I11: a", "  2. I10 (P2): b"])


if __name__ == "__main__":
    unittest.main()
