"""The official-map report played after its result (loop/official.py): readers take the job's official.json in place
of the pending value, the job launches once and is relaunched only after it dies, up to ATTEMPTS, and health.py alerts
on one still missing. The report is E0102's real one (testdata/official-E0102.json, its results.jsonl fields)."""

import testhome  # before anything that reads live paths
import json
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import meta
import official

SAMPLE = json.loads((Path(__file__).resolve().parent / "testdata/official-E0102.json").read_text())
REPORT = SAMPLE["official"]


def clear_home() -> None:
    for path in driver.STATE.iterdir():
        shutil.rmtree(path) if path.is_dir() else path.unlink()


class Official(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        self.row = {**SAMPLE, "official": {"pending": official.PENDING}, "finished": time.time(),
                    "idea": {"id": "I300", "category": "combat", "title": "t"}, "reason": "SPRT accepted",
                    "sprt": {"elo": 41.6}, "session": {}, "changed_files": []}
        (driver.STATE / "results.jsonl").write_text(json.dumps(self.row) + "\n")
        (driver.STATE / "runs/E0102").mkdir(parents=True)
        self.launched: list[int] = []
        official._ticked = 0.0

    def launch(self, *args, **kwargs):
        self.launched.append(4242 + len(self.launched))
        return type("Process", (), {"pid": self.launched[-1], "poll": lambda self: None})()

    def test_readers_take_the_jobs_report_once_it_is_written(self) -> None:
        self.assertEqual(official.report(driver.STATE, self.row), {"pending": official.PENDING})
        self.assertEqual(driver.official_text(official.report(driver.STATE, self.row)), ", official maps pending")
        self.assertIn("E0102", meta.stats_markdown(driver.STATE / "results.jsonl"))
        (driver.STATE / "runs/E0102/official.json").write_text(json.dumps({**REPORT, "played_at": time.time()}))
        self.assertEqual(driver.official_text(official.report(driver.STATE, self.row)),
                         ", official maps 47.5/90 vs champion")
        self.assertIn("Official maps 47.5/90 vs champion.", meta.stats_markdown(driver.STATE / "results.jsonl"))
        self.assertIn("official maps 47.5/90", driver.recent_results())
        # A result from before the change carries its own report.
        self.assertEqual(official.report(driver.STATE, SAMPLE), REPORT)

    def test_the_job_starts_once_and_is_relaunched_only_after_it_dies(self) -> None:
        alive = {"value": True}
        with patch("subprocess.Popen", side_effect=self.launch), \
                patch.object(official, "alive", side_effect=lambda pid: bool(pid) and alive["value"]):
            self.assertIn("pid 4242", official.start(driver.STATE, "E0102"))
            now = time.time()
            self.assertEqual(official.tick(driver.STATE, now), [])  # still running
            alive["value"] = False
            self.assertEqual(official.tick(driver.STATE, now + 60), [])  # checked at most every TICK_EVERY
            self.assertEqual(len(official.tick(driver.STATE, now + official.TICK_EVERY)), 1)  # died: the last attempt
            self.assertEqual(official.tick(driver.STATE, now + 3 * official.TICK_EVERY), [])
        self.assertEqual(self.launched, [4242, 4243])
        job = json.loads((driver.STATE / "runs/E0102/official-job.json").read_text())
        self.assertEqual((job["pid"], job["attempts"]), (4243, 2))

    def test_a_result_still_being_appended_is_skipped_not_a_crash(self) -> None:
        # record() appends a row of up to 2 MB while the upkeep and health.py read results.jsonl unlocked.
        results = driver.STATE / "results.jsonl"
        pool = {"opponents": [{"name": "swarm", "not_reused": "the SPRT left no sprt-rotation.jsonl"}]}
        torn = json.dumps({"pool": pool, **self.row, "id": "E0103"})  # both markers early, before the tear
        results.write_text(json.dumps({**self.row, "pool": pool}) + "\n" + torn[:len(torn) // 2])
        self.assertEqual([row["id"] for row in official.waiting(driver.STATE)], ["E0102"])
        self.assertEqual([a.key for a in health.screen_reuse(driver.STATE)], ["reuse:E0102"])

    def test_the_driver_and_an_upkeep_tick_never_both_start_the_job(self) -> None:
        # Both hold the result folder's lock around check-and-launch: the second finds the first's live pid.
        with patch("subprocess.Popen", side_effect=self.launch), \
                patch.object(official, "alive", side_effect=lambda pid: bool(pid)):
            self.assertIsNotNone(official.start(driver.STATE, "E0102"))
            self.assertIsNone(official.start(driver.STATE, "E0102"))
        self.assertEqual(self.launched, [4242])
        self.assertTrue((driver.STATE / "runs/E0102/official.lock").exists())

    def test_the_job_plays_against_its_base_commit_and_writes_the_report(self) -> None:
        exp = driver.Experiment(id="E0102", idea={"id": "I300", "title": "t"}, lane=1, official={"pending": "x"})
        exp.evaluation["base"] = "65bb2295e3700d632b66825b9302c49bcb671e18"
        driver.save_experiment(exp)
        calls = []

        def play(exp, cand, base=None, yielding=False, status=None):
            calls.append((cand.name, base, yielding, status.name))
            exp.official = dict(REPORT)
        with patch.object(driver, "gauntlet_bot", return_value=Path("/bots/champion-abc")) as build, \
                patch.object(driver, "check_official", side_effect=play):
            self.assertEqual(official.run("E0102"), 0)
        self.assertEqual(build.call_args.args[0], {"name": "champion", "commit": exp.evaluation["base"]})
        self.assertEqual(calls, [("cand", Path("/bots/champion-abc"), True, "remote-status-official.json")])
        self.assertEqual(official.report(driver.STATE, self.row)["games"], 90)
        with patch.object(driver, "gauntlet_bot", side_effect=RuntimeError("git archive failed")):
            (driver.STATE / "runs/E0102/official.json").unlink()
            self.assertEqual(official.run("E0102"), 1)
        job = json.loads((driver.STATE / "runs/E0102/official-job.json").read_text())
        self.assertIn("git archive failed", job["error"])

    def test_a_report_missing_past_the_grace_or_out_of_attempts_alerts(self) -> None:
        now = self.row["finished"]
        self.assertEqual(health.official_reports(driver.STATE, now + 60), [])
        (alert,) = health.official_reports(driver.STATE, now + official.GRACE + 60)
        self.assertEqual(alert.key, "official:E0102")
        self.assertIn("its job never started", alert.text)
        (driver.STATE / "runs/E0102/official-job.json").write_text(json.dumps(
            {"pid": None, "attempts": official.ATTEMPTS, "error": "RuntimeError: git archive failed"}))
        (alert,) = health.official_reports(driver.STATE, now + 60)  # no retry is coming: at once
        self.assertIn("failed 2x: RuntimeError: git archive failed", alert.text)
        (driver.STATE / "runs/E0102/official.json").write_text(json.dumps(REPORT))
        self.assertEqual(health.official_reports(driver.STATE, now + official.GRACE + 60), [])

    def test_a_slow_job_that_keeps_playing_is_quiet_and_one_stuck_for_the_grace_alerts(self) -> None:
        # The job gets only a filler share while evaluations run, so a report 3 h after its result may just be slow.
        now, seen = self.row["finished"], {}
        status = driver.STATE / "runs/E0102/remote-status-official.json"
        (driver.STATE / "runs/E0102/official-job.json").write_text(json.dumps({"pid": 4242, "attempts": 1}))
        with patch.object(official, "alive", return_value=True):
            for hour, games in ((1, 10), (2, 30), (3, 50), (4, 70)):
                status.write_text(json.dumps({"updated": now + hour * 3600, "games": games}))
                self.assertEqual(health.official_reports(driver.STATE, now + hour * 3600, seen), [])
            (alert,) = health.official_reports(driver.STATE, now + 4 * 3600 + official.GRACE + 60, seen)
        self.assertIn("its job started", alert.text)


# M65: near-self-play on official maps is decided by side (E0200-E0239: Dilemma to side B 285 of 290), so the candidate
# scores half there whatever it changed. E0175's real 100 games: Dilemma alone went to one side 90%+.
class SideDecided(unittest.TestCase):
    def test_a_map_one_side_wins_is_flagged_and_its_score_said_to_judge_nothing(self) -> None:
        rows = official.json_rows((Path(__file__).parent / "testdata/official-E0175.jsonl").read_text().splitlines())
        self.assertEqual(official.side_decided(rows), ["dilemma"])
        report = {"score": 0.43, "games": 100, "lost_both": [], "side_decided": ["dilemma"]}
        self.assertEqual(driver.official_text(report), ", official maps 43/100 vs champion (one side won 90%+ on "
                                                       "dilemma: their scores judge nothing)")
        self.assertEqual(driver.official_text({**report, "side_decided": []}), ", official maps 43/100 vs champion")

    def test_just_under_the_share_is_not_side_decided(self) -> None:
        def games(b_wins: int, n: int = 10) -> list[dict]:  # side B wins b_wins of n, the candidate alternating sides
            sides = [("B" if k < b_wins else "A", "AB"[k % 2]) for k in range(n)]
            return [{"map": f"m~{k}.map", "a_side": a_side, "winner": "a" if won == a_side else "b"}
                    for k, (won, a_side) in enumerate(sides)]
        self.assertEqual(official.side_decided(games(9)), ["m"])
        self.assertEqual(official.side_decided(games(8)), [])
        self.assertEqual(official.side_decided(games(1)), ["m"])  # either side


if __name__ == "__main__":
    unittest.main()
