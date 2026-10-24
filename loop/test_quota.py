"""The 5-hour usage levels the loop acts on (loop/quota.py; sig, 30 Sep 02:03-02:30: at 95% pause every coder and
builder, nothing lost, evaluation going on, and turn back on by itself at the reset): the marker's expiry, the coder
pause and resume (driver.pausable, unpause), the held claim (driver.wait_window), held report workers
(reports.tick) and the health check of each firing (health.usage_level).

    PYTHONPATH=loop .venv/bin/python -m unittest test_quota
"""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import quota
import reports

STATE = driver.STATE


def level(rank: int, resets_at: float, since: float | None = None) -> None:
    name = {quota.AHEAD: "ahead", quota.REVIEWS: "85%", quota.PAUSE: "95%"}[rank]
    (STATE / quota.LEVEL).write_text(json.dumps({"level": name, "rank": rank, "resets_at": resets_at,
                                                 "since": time.time() if since is None else since}))


class OffSwitch(unittest.TestCase):
    # sig 1 Oct: the levels off until a time; past it the marker counts again.
    def test_off_until_a_time(self) -> None:
        STATE.mkdir(parents=True, exist_ok=True)
        self.addCleanup((STATE / quota.LEVEL).unlink, missing_ok=True)
        self.addCleanup((STATE / quota.OFF).unlink, missing_ok=True)
        now = time.time()
        level(quota.PAUSE, now + 3600)
        (STATE / quota.OFF).write_text(json.dumps({"until": now + 60}))
        self.assertFalse(quota.paused(STATE, now))
        self.assertTrue(quota.paused(STATE, now + 120))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        STATE.mkdir(parents=True, exist_ok=True)
        self.addCleanup((STATE / quota.LEVEL).unlink, missing_ok=True)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)


class Marker(Base):
    def test_a_level_holds_what_it_names_and_ends_at_its_reset_by_itself(self) -> None:
        now = time.time()
        level(quota.AHEAD, now + 600)
        self.assertTrue(quota.held(STATE, "backlog") and not quota.held(STATE, "reflect-list"))
        self.assertFalse(quota.paused(STATE))
        level(quota.PAUSE, now + 600)
        self.assertTrue(quota.paused(STATE) and quota.held(STATE, "review") and quota.held(STATE, quota.PAUSE))
        self.assertEqual(quota.held(STATE, "judge"), "")  # not a HELD name: never waits
        self.assertFalse(quota.paused(STATE, now + 601))  # no status line ran at the reset: off anyway


class CoderPause(Base):
    def experiment(self, exp_id: str) -> driver.Experiment:
        exp = driver.Experiment(id=exp_id, idea={"id": "I1"}, lane=1)
        exp.run_dir.mkdir(parents=True, exist_ok=True)
        exp.session.update(name=f"bc-{exp_id}", session_id="s")
        return exp

    def test_a_running_coder_is_stopped_at_the_pause_and_resumed_on_its_session_at_the_reset(self) -> None:
        exp = self.experiment("E9001")
        proc = subprocess.Popen(["/bin/sh", "-c", "sleep 60"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        threading.Timer(0.3, level, (quota.PAUSE, time.time() + 1.5)).start()
        started = time.monotonic()
        with patch.object(driver, "PAUSE_POLL_S", 0.1):
            driver.pausable(exp, proc, "prompt")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(proc.poll())  # its group was stopped
        self.assertEqual(exp.session["pause"]["level"], "95%")
        resumed = []
        with patch.object(driver, "LIMIT_POLL_S", 0.1), \
                patch.object(driver, "continue_turn", lambda *args, **kw: resumed.append((args, kw))):
            driver.unpause(exp, Path("/nonexistent"), True)
        self.assertEqual(json.loads((exp.run_dir / "worker.json").read_text())["status"], "paused")
        (args, kw), = resumed
        self.assertTrue(args[4].startswith("The loop stopped your last turn at") and kw == {"saved": "pause"})
        self.assertNotIn("pause", exp.session)
        self.assertIsNone(driver.session_failure(exp))

    def test_a_stop_during_the_pause_records_the_try_blocked_with_its_work_kept(self) -> None:
        exp = self.experiment("E9002")
        exp.session["pause"] = {"at": time.time(), "level": "95%", "resets_at": time.time() + 3600}
        level(quota.PAUSE, time.time() + 3600)
        with patch.object(driver, "stop_requested", lambda: True), \
                patch.object(driver, "continue_turn", lambda *a, **k: self.fail("resumed under a stop")):
            driver.unpause(exp, Path("/nonexistent"), True)
        verdict, why = driver.session_failure(exp)
        self.assertEqual(verdict, "blocked")
        self.assertIn(verdict, driver.WIP_VERDICTS)  # keep_wip saves its clone's work as wip/E####

    def test_a_turn_that_ends_before_the_pause_is_left_alone(self) -> None:
        exp = self.experiment("E9003")
        proc = subprocess.Popen(["/bin/sh", "-c", "cat >/dev/null; echo done"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        out, _ = driver.pausable(exp, proc, "prompt")
        self.assertEqual((out, "pause" in exp.session), ("done\n", False))


    def test_a_stop_during_the_pause_launches_no_waiting_coder_or_turn(self) -> None:
        exp = self.experiment("E9004")
        level(quota.PAUSE, time.time() + 3600)
        with patch.object(driver, "stop_requested", lambda: True), \
                patch.object(driver.subprocess, "Popen", side_effect=AssertionError("launched")):
            driver.run_lane(exp, Path("/nonexistent"))
            self.assertEqual(driver.session_failure(exp)[0], "blocked")
            self.assertEqual(driver.resume_coder(exp, Path("/nonexistent"), "go", usage="turn").returncode, 1)

    def test_a_turn_that_finishes_as_it_is_stopped_keeps_its_result(self) -> None:
        exp = self.experiment("E9005")
        done = json.dumps({"type": "result", "result": "Committed.", "is_error": False})
        script = f"trap '' TERM; cat >/dev/null; sleep 0.5; echo '{done}'"
        proc = subprocess.Popen(["/bin/sh", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
        level(quota.PAUSE, time.time() + 3600)
        with patch.object(driver, "PAUSE_POLL_S", 0.1):
            out, _ = driver.pausable(exp, proc, "prompt")
        self.assertEqual((out.strip(), "pause" in exp.session), (done, False))

    def test_a_held_turn_is_marked_for_the_rerun_after_the_reset(self) -> None:
        (STATE / "runs/E9006").mkdir(parents=True, exist_ok=True)
        level(quota.PAUSE, time.time() + 3600)
        self.assertIn("stays owed", driver.turn_held("E9006"))
        self.assertTrue((STATE / "runs/E9006" / driver.HELD_TURN).exists())
        self.assertEqual(driver.HELD_TURN, driver.maintenance.HELD_TURN)  # owed_turns reads the same marker


class Held(Base):
    def test_a_level_crossed_before_a_launch_is_a_quota_stop_retried_after_the_reset(self) -> None:
        import meta
        resets = time.time() + 1800
        level(quota.AHEAD, resets)
        with self.assertRaises(quota.Held) as raised:
            meta.worker_command(Path("/work"), "{}", usage="backlog")
        text = f"could not launch: Held: {raised.exception}"
        self.assertTrue(meta.provider_limited(text))
        record, abandoned = meta.after_failure({}, Path("/work"), text, ())
        self.assertFalse(abandoned or record.get("failures"))  # not counted
        self.assertLessEqual(abs(record["retry_at"] - (resets + meta.RESET_MARGIN_S)), 60)


class Claims(Base):
    def test_a_claim_waits_out_the_pause_and_goes_on_at_the_reset(self) -> None:
        level(quota.PAUSE, time.time() + 0.5)
        started = time.monotonic()
        with patch.object(driver, "LIMIT_POLL_S", 0.1):
            self.assertTrue(driver.wait_window("lane 1's next claim"))
        self.assertGreater(time.monotonic() - started, 0.3)
        self.assertEqual(driver.turn_held("E1"), "")

    def test_under_the_pause_a_lane_claims_nothing(self) -> None:
        level(quota.PAUSE, time.time() + 3600)
        self.assertIn("stays owed", driver.turn_held("E1"))
        stops = iter([False, False, True, True, True])  # the loop's check, then the wait's, then a stop
        with patch.object(driver, "stop_requested", lambda: next(stops)), patch.object(driver, "LIMIT_POLL_S", 0), \
                patch.object(driver, "claim_next", lambda lane: self.fail("claimed under the pause")):
            self.assertEqual(driver.lane_loop(0, once=True), 1)


class Reports(Base):
    def test_a_held_kind_starts_and_launches_nothing_but_still_collects(self) -> None:
        kind = reports.Kind("backlog", "low", lambda state: (), lambda s, r, t: ("done", {}),
                            lambda s, r, w: ("gave up", {}), (), 60)
        level(quota.AHEAD, time.time() + 3600)
        started = []
        with patch.object(reports, "start_worker", lambda *a: self.fail("launched while held")):
            self.assertIsNone(reports.tick(STATE, kind, lambda: started.append(1)))
            self.assertEqual(started, [])
            reports.save(STATE, kind, {"pending": str(STATE / "backlog/1")})  # prepared before the level
            self.assertIsNone(reports.tick(STATE, kind, lambda: started.append(1)))
            reports.save(STATE, kind, {"pending": str(STATE / "backlog/1"), "pid": 999_999_999})  # exited
            with patch.object(reports, "collect", lambda state, k, work: "collected"):
                self.assertEqual(reports.tick(STATE, kind), "collected")


class Health(Base):
    def share(self, rank: int, resets_at: float, at: float) -> None:
        (STATE / "usage-share.json").write_text(json.dumps({"used_percentage": 96, "resets_at": resets_at,
                                                            "at": at, "rank": rank}))

    def test_each_firing_is_checked_against_the_status_lines_record_and_its_reset(self) -> None:
        now = time.time()
        self.addCleanup((STATE / "usage-share.json").unlink, missing_ok=True)
        level(quota.PAUSE, now + 3600, since=now - 60)
        self.share(3, now + 3600, now - 30)
        self.assertEqual(health.usage_level(STATE, now), [])
        self.share(2, now + 3600, now - 30)
        self.assertEqual([a.key for a in health.usage_level(STATE, now)], ["usage:mismatch"])
        self.share(3, now - 900, now - 1000)
        level(quota.PAUSE, now - 900, since=now - 1200)
        self.assertEqual([a.key for a in health.usage_level(STATE, now)], [f"usage:stale:{int(now - 900)}"])

    def test_a_coder_the_pause_missed_or_left_paused_alerts(self) -> None:
        now = time.time()
        self.addCleanup((STATE / "usage-share.json").unlink, missing_ok=True)
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        runs = STATE / "runs"
        for exp_id, worker in (("E9101", {"status": "running", "pid": live.pid, "driver_pid": live.pid}),
                               ("E9102", {"status": "paused", "driver_pid": live.pid, "resets_at": now - 1200})):
            (runs / exp_id).mkdir(parents=True, exist_ok=True)
            (runs / exp_id / "worker.json").write_text(json.dumps(worker))
            self.addCleanup((runs / exp_id / "worker.json").unlink)
        level(quota.PAUSE, now + 3600, since=now - 1200)
        self.share(3, now + 3600, now - 30)
        keys = sorted(a.key.rsplit(":", 1)[0] for a in health.usage_level(STATE, now))
        self.assertEqual(keys, ["usage:paused:E9102", "usage:running:E9101"])

    # REFLECT 2026-09-30 09:12 F11: with no session drawing the status line, no level can pause the coders.
    def test_a_stale_record_alerts_only_while_a_coder_runs(self) -> None:
        now = time.time()
        self.addCleanup((STATE / "usage-share.json").unlink, missing_ok=True)
        self.share(0, now + 3600, now - health.USAGE_SILENT - 60)
        self.assertEqual(health.usage_level(STATE, now), [])  # no coder
        live = subprocess.Popen(["sleep", "30"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        (STATE / "runs/E9103").mkdir(parents=True, exist_ok=True)
        (STATE / "runs/E9103/worker.json").write_text(json.dumps({"status": "running", "pid": live.pid}))
        self.addCleanup((STATE / "runs/E9103/worker.json").unlink)
        (alert,) = health.usage_level(STATE, now)
        self.assertTrue(alert.key.startswith("usage:silent:") and "E9103" in alert.text)
        self.share(0, now + 3600, now - health.USAGE_SILENT + 60)
        self.assertEqual(health.usage_level(STATE, now), [])

    def test_a_limit_reset_writes_one_wake_however_many_turns_wait_on_it(self) -> None:
        # REFLECT 30 Sep 0311 F2: the 03:01 wake after tonight's limit was set by hand.
        self.addCleanup((STATE / "reminders.jsonl").unlink, missing_ok=True)
        at = time.time() + 3600
        self.assertIsNotNone(health.wake(STATE, at, "limit reset: E1's merge turn retries"))
        self.assertIsNone(health.wake(STATE, at + 30, "limit reset: E2's coder's turn retries"))
        (row,) = [json.loads(line) for line in (STATE / "reminders.jsonl").read_text().splitlines()]
        self.assertTrue(row["text"].startswith("USAGE limit reset"))
        self.assertEqual(health.reminders(STATE, at + quota.RESET_MARGIN_S + 60)[0].text.split(": ", 1)[1],
                         row["text"])
        self.assertEqual(health.reminders(STATE, at), [])  # not before the reset
        self.assertIn("reminders", health.QUICK)  # so it fires within the minute, not the hour

    def test_reminders_wait_out_the_pause(self) -> None:
        self.addCleanup((STATE / "reminders.jsonl").unlink, missing_ok=True)
        (STATE / "reminders.jsonl").write_text(json.dumps({"due": "2026-09-01T09:00", "text": "check it"}) + "\n")
        level(quota.PAUSE, time.time() + 3600)
        self.assertEqual(health.reminders(STATE, time.time()), [])
        (STATE / quota.LEVEL).unlink()
        self.assertEqual(len(health.reminders(STATE, time.time())), 1)


if __name__ == "__main__":
    unittest.main()
