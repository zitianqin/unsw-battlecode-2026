"""When REFLECT is due (loop/reflect.py): every reflect.EVERY_SECONDS, one alert line per period across processes, and
health.py's alert when it stops firing. Temporary loop homes only."""

import testhome  # before anything that reads live paths
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import health
import reflect
import reports

T = 1_790_000_000.0
HOUR = 3600
PERIOD = reflect.EVERY_SECONDS


def add_results(state: Path, n: int) -> None:
    with (state / "results.jsonl").open("a") as handle:
        handle.writelines('{"id": "E"}\n' for _ in range(n))


def alert_lines(state: Path) -> list[str]:
    path = state / "host-alerts.log"
    return [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def check_in(args: tuple[str, float]) -> bool:
    return reflect.check(Path(args[0]), args[1]) is not None


class Reflect(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    def test_first_check_fires_then_waits_a_period(self) -> None:
        add_results(self.state, 4)
        line = reflect.check(self.state, T)
        self.assertIn("REFLECT due (none recorded yet)", line)
        self.assertIsNone(reflect.check(self.state, T + 60))
        self.assertIsNone(reflect.check(self.state, T + PERIOD - 1))
        again = reflect.check(self.state, T + PERIOD)
        self.assertIn(f"{PERIOD / HOUR:.1f} h since the last one", again)
        self.assertEqual(reflect.read(self.state)["reason"], f"{PERIOD / HOUR:.1f} h since the last one")
        self.assertEqual(alert_lines(self.state), [])  # the worker's list, or its by-hand line, is what alerts

    def test_results_alone_never_fire_it(self) -> None:
        reflect.check(self.state, T)
        add_results(self.state, 50)  # sig, 2026-09-29: the clock only, however many results land
        self.assertIsNone(reflect.check(self.state, T + PERIOD - 1))
        self.assertIsNotNone(reflect.check(self.state, T + PERIOD))

    def test_the_record_names_the_window_since_the_previous_one(self) -> None:
        reflect.check(self.state, T)
        reflect.check(self.state, T + PERIOD)
        since = json.loads((self.state / "reflect.json").read_text())["since"]
        self.assertEqual(since[:16], reflect.datetime.fromtimestamp(T).astimezone().isoformat()[:16])

    def test_many_processes_write_it_once(self) -> None:
        reflect.check(self.state, T)
        with multiprocessing.get_context("spawn").Pool(6) as pool:
            fired = pool.map(check_in, [(str(self.state), T + PERIOD)] * 12)
        self.assertEqual(sum(fired), 1)
        self.assertEqual(reflect.read(self.state)["at"], T + PERIOD)

    def test_health_alerts_when_it_stops_firing(self) -> None:
        self.assertIsNone(reflect.late(self.state, T))  # no loop has run here
        add_results(self.state, 1)
        self.assertIn("never", reflect.late(self.state, T))
        reflect.check(self.state, T)
        self.assertIsNone(reflect.late(self.state, T + reflect.LATE))
        self.assertIn("not since", reflect.late(self.state, T + reflect.LATE + 1))
        keys = [alert.key for alert in health.alerts(self.state, self.state, T + reflect.LATE + 1, {})]
        self.assertIn("reflect", keys)


# tools/my_messages.py's output (stdout, with its stderr header first), captured from the orchestrator's transcript on
# 2026-09-26, and the shape of a finished `claude -p --output-format json` session.json (a real one, trimmed).
MESSAGES = (Path(__file__).resolve().parent / "testdata/my-messages-sample.txt").read_text()
HEADER, BODY = MESSAGES.split("\n", 1)
SESSION = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 12, "terminal_reason": "completed",
           "result": "The list is written to report.md."}
LIST = ('- (d) 13:06:34 sig: "do we ever the possibility that a single idea might not improve that much by iutself" | '
        "sig had to raise complements | class: MISSES 21 | fix: sweep groups\n(a) none found\n")


class Worker(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.runs: list[list[str]] = []
        drift = patch.object(reflect, "drift_list", return_value="None.\n")  # DriftList tests it
        drift.start()
        self.addCleanup(drift.stop)

    def fake_run(self, code: int = 0):
        def run(cmd, **kwargs):
            self.runs.append(cmd)
            return subprocess.CompletedProcess(cmd, code, BODY if not code else "", HEADER + "\n" if not code else
                                               "no orchestrator transcript (titled bc-main*) in /x")
        return patch.object(reflect.subprocess, "run", side_effect=run)

    # The drift list saved for the REFLECT fired at T + 3 h, which must exist.
    def drift(self) -> Path:
        stamp = reflect.time.strftime("%Y-%m-%d-%H%M", reflect.time.localtime(T + PERIOD))
        path = self.state / "reflect" / f"{stamp}-drift.txt"
        self.assertEqual(path.read_text(), "None.\n")
        return path

    def io(self, work: Path) -> Path:
        return self.state / "worker-io/reflect-list" / work.name

    def finish(self, work: Path, report: str | None = LIST) -> None:
        if report is not None:
            (work / "report.md").write_text(report)
        (self.io(work) / "session.json").write_text(json.dumps(SESSION))

    # Fires REFLECT at T and T + 3 h; the worker's first tick records nothing to do only when no REFLECT is newer
    # than its window.
    def fire_twice(self) -> None:
        reflect.check(self.state, T)
        reflect.check(self.state, T + PERIOD)

    def test_a_due_reflect_gets_its_messages_from_the_trusted_side_and_a_read_only_worker(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)) as popen, \
                patch.object(reflect, "drift_list", return_value="# drifted comments\n") as drift:
            self.assertIn("launched", reflect.tick(self.state))
        since = reflect.iso(T)
        drift.assert_called_once_with(since)  # the same window as the messages
        self.assertEqual(self.runs[0][1:], [str(reflect.REPO / "tools/my_messages.py"), "--since", since])
        self.assertEqual(self.runs[1][1:], [str(reflect.REPO / "tools/my_messages.py"), "--check-times", "--since",
                                            since])
        work = Path(reports.read(self.state, reflect.KIND)["pending"])
        self.assertEqual((work / "messages.txt").read_text(), MESSAGES)
        self.assertEqual([run[2] for run in self.runs[2:4]], ["--check-evalline", "--check-asks"])
        for name in ("times.txt", "evalline.txt", "asks.txt"):
            self.assertEqual((work / name).read_text(), BODY)  # fake_run gives every run the same output
        self.assertEqual((work / "MISSES.md").read_text(), reflect.MISSES.read_text())
        self.assertEqual((work / "drift.txt").read_text(), "# drifted comments\n")
        prompt = (self.io(work) / "PROMPT.md").read_text()
        steps, brief = reflect.BRIEF.read_text().split("## Brief for the subagent", 1)
        self.assertIn(brief.rstrip(), prompt)  # REFLECT.md's brief for the subagent
        self.assertNotIn(steps.strip().splitlines()[-1], prompt)  # not the orchestrator's own steps
        self.assertIn(f"`{work}/messages.txt`", prompt)
        self.assertIn(f"`{work}/drift.txt`", prompt)
        self.assertIn(f"`{work}/times.txt`", prompt)
        self.assertIn(f"`{work}/evalline.txt`", prompt)
        self.assertIn(f"`{work}/asks.txt`", prompt)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "Read,Grep,Glob,Write")
        self.assertEqual(command[command.index("--effort") + 1], "medium")
        self.assertNotIn("--add-dir", command)  # it reads its own folder only: never the transcript
        self.assertEqual(popen.call_args.kwargs["cwd"], self.io(work))

    def test_the_list_is_saved_out_of_the_workers_reach_and_alerted(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            reflect.tick(self.state)
            work = Path(reports.read(self.state, reflect.KIND)["pending"])
            self.finish(work)
            with patch.object(reports.meta, "alive", return_value=False):
                line = reflect.tick(self.state)
                self.assertIsNone(reflect.tick(self.state))  # nothing newer to list
        record = reports.read(self.state, reflect.KIND)
        path = Path(record["list"])
        self.assertEqual(path.parent, self.state / "reflect")
        self.assertEqual(line, f"REFLECT list ready: {path}; judge each F-id and record the decisions in decisions.md "
                               "(loop/REFLECT.md)")
        self.assertTrue(alert_lines(self.state)[-1].endswith(line))
        self.assertIn(LIST, path.read_text())
        self.assertEqual(record["covered_at"], T + PERIOD)
        self.assertNotIn("pending", record)
        self.assertEqual(len(self.runs), 1 + len(reflect.CHECKS))  # the messages and each check, once

    def test_a_report_linked_to_a_secret_is_refused(self) -> None:
        secret = self.state / "keys.json"
        secret.write_text('{"key": "not for the orchestrator"}')
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            reflect.tick(self.state)
            work = Path(reports.read(self.state, reflect.KIND)["pending"])
            self.finish(work, report=None)
            (work / "report.md").symlink_to(secret)
            with patch.object(reports.meta, "alive", return_value=False):
                self.assertIn("retrying after an hour", reflect.tick(self.state))
        self.assertEqual(list((self.state / "reflect").glob("*.md")), [])  # no list, only the drift list
        self.assertNotIn("not for the orchestrator", (self.state / "host-alerts.log").read_text())

    def test_two_failed_launches_fall_back_to_the_by_hand_line(self) -> None:
        self.fire_twice()
        later = reports.time.time() + reports.RETRY_S + 1
        with self.fake_run(), patch.object(reports.subprocess, "Popen", side_effect=OSError("no claude")):
            self.assertIn("retrying after an hour", reflect.tick(self.state))
            self.assertIsNone(reflect.tick(self.state))  # waits the hour
            with patch.object(reports.time, "time", return_value=later):
                line = reflect.tick(self.state)
                self.assertIsNone(reflect.tick(self.state))  # the REFLECT is handed over: no fresh folder
        self.assertTrue(line.startswith(f"REFLECT due ({PERIOD / HOUR:.1f} h since the last one)"))
        self.assertIn(f"tools/my_messages.py --since {reflect.iso(T)}`", line)
        self.assertIn(f"the doc drift list {self.drift()} (item (f))", line)
        self.assertIn("--check-times", line)
        self.assertIn("the REFLECT worker was abandoned after 2 failed launches", line)
        self.assertTrue(alert_lines(self.state)[-1].endswith(line))
        record = reports.read(self.state, reflect.KIND)
        self.assertEqual((record["covered_at"], len(record["abandoned"])), (T + PERIOD, 1))

    def test_a_failed_times_check_is_a_note_and_the_list_still_launches(self) -> None:
        self.fire_twice()
        def run(cmd, **kwargs):
            if "--check-times" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "Traceback: boom")
            return subprocess.CompletedProcess(cmd, 0, BODY, HEADER + "\n")
        with patch.object(reflect.subprocess, "run", side_effect=run), \
                patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            self.assertIn("launched", reflect.tick(self.state))
        work = Path(reports.read(self.state, reflect.KIND)["pending"])
        self.assertEqual((work / "times.txt").read_text(), "No times check this time (RuntimeError: Traceback: boom).\n")

    def test_my_messages_failing_hands_the_reflect_over_at_once(self) -> None:
        self.fire_twice()
        with self.fake_run(code=1), patch.object(reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = reflect.tick(self.state)
        self.assertIn(f"tools/my_messages.py --since {reflect.iso(T)}`", line)
        self.assertIn("no orchestrator transcript", line)
        self.assertIn(f"the doc drift list {self.drift()} (item (f))", line)
        self.assertEqual(reports.read(self.state, reflect.KIND)["covered_at"], T + PERIOD)

    def test_a_reflect_fired_while_one_runs_widens_the_next_window(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            reflect.tick(self.state)
            first = Path(reports.read(self.state, reflect.KIND)["pending"])
            reflect.check(self.state, T + 2 * PERIOD)
            with patch.object(reports, "running", return_value=True):
                self.assertIsNone(reflect.tick(self.state))  # still running: the new one waits
            self.finish(first)
            with patch.object(reports.meta, "alive", return_value=False):
                self.assertIn("REFLECT list ready", reflect.tick(self.state))
                self.assertIn("launched", reflect.tick(self.state))
        self.assertEqual(self.runs[-1][-1], reflect.iso(T + PERIOD))

    def test_a_worker_past_its_cap_is_stopped_and_counts_as_a_failed_launch(self) -> None:
        self.fire_twice()
        # The real launch chain (meta.in_folder, meta.private_tmp) around a stand-in session that never finishes.
        with self.fake_run(), patch.object(reports.meta, "worker_command", return_value=["sleep", "60"]):
            self.assertIn("launched", reflect.tick(self.state))
        record = reports.read(self.state, reflect.KIND)
        pid, work = record["pid"], Path(record["pending"])
        self.addCleanup(reports.stop, pid)
        self.assertTrue(reports.running(pid, work))
        self.assertIsNone(reflect.tick(self.state))  # within its cap: left running
        reports.save(self.state, reflect.KIND, {**record, "launched_at": record["launched_at"] - reflect.TIMEOUT})
        line = reflect.tick(self.state)
        self.assertIn(f"ran past its {reflect.TIMEOUT // 60} min cap and was stopped", line)
        self.assertFalse(reports.meta.alive(pid))
        self.assertEqual(len(reports.read(self.state, reflect.KIND)["failures"]), 1)

    def test_a_worker_whose_folder_cant_be_read_is_waited_for_never_stopped(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.meta, "worker_command", return_value=["sleep", "60"]):
            reflect.tick(self.state)
        record = reports.read(self.state, reflect.KIND)
        pid = record["pid"]
        self.addCleanup(reports.stop, pid)
        reports.save(self.state, reflect.KIND, {**record, "launched_at": record["launched_at"] - reflect.TIMEOUT})
        with patch.object(reports.os, "readlink", side_effect=OSError("no /proc")), \
                patch.object(reports.subprocess, "run", side_effect=OSError("no lsof")):
            self.assertIsNone(reports.running(pid, Path(record["pending"])))
            self.assertIsNone(reflect.tick(self.state))  # past its cap, but no proof it is ours: left alone
        self.assertTrue(reports.meta.alive(pid))

    def test_a_pid_another_user_owns_is_never_our_worker(self) -> None:
        # pid 1 (launchd, root) looks alive to meta.alive, and lsof can't read its folder.
        self.assertTrue(reports.meta.alive(1))
        self.assertFalse(reports.running(1, self.state))

    def test_a_claude_folder_left_pending_goes_to_the_orchestrator_when_native_takes_over(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", side_effect=OSError("no claude")):
            self.assertIn("retrying after an hour", reflect.tick(self.state))
        with self.fake_run(), patch.object(reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = reflect.tick(self.state, models=False)
            self.assertIn(f"tools/my_messages.py --since {reflect.iso(T)}`", line)
            self.assertIn("abandoned under native orchestration", line)
            self.assertEqual(reports.read(self.state, reflect.KIND)["covered_at"], T + PERIOD)
            reflect.check(self.state, T + 2 * PERIOD)  # the next REFLECT proceeds, by hand under native
            self.assertIn(f"--since {reflect.iso(T + PERIOD)}`", reflect.tick(self.state, models=False))

    def test_a_reused_pid_counts_as_exited_and_is_never_stopped(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            reflect.tick(self.state)
        other = subprocess.Popen(["sleep", "60"], cwd=self.state, start_new_session=True)  # not the worker's folder
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        record = reports.read(self.state, reflect.KIND)
        reports.save(self.state, reflect.KIND, {**record, "pid": other.pid,
                                                "launched_at": record["launched_at"] - reflect.TIMEOUT})
        self.assertIn("no report", reflect.tick(self.state))
        self.assertIsNone(other.poll())  # still running: not ours to stop

    def test_under_native_orchestration_no_worker_runs_and_the_orchestrator_gets_the_by_hand_line(self) -> None:
        self.fire_twice()
        with self.fake_run(), patch.object(reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = reflect.tick(self.state, models=False)
        self.assertIn(f"tools/my_messages.py --since {reflect.iso(T)}`", line)
        self.assertIn("no Claude worker under native orchestration", line)
        self.assertIn(f"the doc drift list {self.drift()} (item (f))", line)
        self.assertEqual(self.runs, [])
        self.assertEqual(reports.read(self.state, reflect.KIND)["covered_at"], T + PERIOD)

    def test_an_empty_window_launches_no_worker(self) -> None:
        self.fire_twice()
        def empty(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", HEADER + "\n")
        with patch.object(reflect.subprocess, "run", side_effect=empty), \
                patch.object(reports.subprocess, "Popen", side_effect=AssertionError("launched")):
            line = reflect.tick(self.state)
        self.assertTrue(line.startswith(f"REFLECT due: 0 orchestrator messages since {reflect.iso(T)} "
                                        "(wrong transcript?)"))
        self.assertIn("8406d943-dd71-49fc-b16b-5c8a82eb997f.jsonl", line)
        for flag in ("--check-times", "--check-evalline", "--check-asks"):
            self.assertIn(flag, line)
        self.assertIn(f"doc drift list {self.drift()} (item (f)), by hand", line)  # built though no messages came
        self.assertTrue(alert_lines(self.state)[-1].endswith(line))

    def test_health_alerts_on_a_reflect_with_no_list_and_a_list_nobody_judged(self) -> None:
        self.fire_twice()
        fired = T + PERIOD
        self.assertEqual(health.reflect_worker(self.state, self.state, fired + health.WORKER_PENDING), [])
        [alert] = health.reflect_worker(self.state, self.state, fired + health.WORKER_PENDING + 1)
        self.assertEqual(alert.key, f"reflect-list:{int(fired)}")
        with self.fake_run(), patch.object(reports.subprocess, "Popen", return_value=Mock(pid=4242)):
            reflect.tick(self.state)
            self.finish(Path(reports.read(self.state, reflect.KIND)["pending"]))
            with patch.object(reports.meta, "alive", return_value=False):
                reflect.tick(self.state)
        listed = Path(reports.read(self.state, reflect.KIND)["list"])
        written = listed.stat().st_mtime
        misses = self.state / "loop/MISSES.md"  # the repo's, here the state folder standing in for it
        misses.parent.mkdir()
        misses.write_text("# Misses\n")
        os.utime(misses, (written - 60, written - 60))
        self.assertEqual(health.reflect_worker(self.state, self.state, written + health.REFLECT_JUDGE_GRACE), [])
        keys = [a.key for a in health.reflect_worker(self.state, self.state, written + health.REFLECT_JUDGE_GRACE + 1)]
        self.assertEqual(keys, [f"reflect-judged:{listed.name}"])
        os.utime(misses, (written + 60, written + 60))  # a reflection line recorded after the list
        self.assertEqual(health.reflect_worker(self.state, self.state, written + PERIOD), [])



DOCUMENTED = """# How many steps findRam looks ahead.
def reach(length: int) -> int:
    return length - 1
"""


class DriftList(unittest.TestCase):
    """drift_list with the real tools/check_refs.py on a temp repo whose commits have fixed dates."""

    def setUp(self) -> None:
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(trace.stop)
        self.repo = Path(directory.name)
        (self.repo / "tools").mkdir()
        (self.repo / "loop").mkdir()
        shutil.copy2(reflect.REPO / "tools/check_refs.py", self.repo / "tools")
        self.git("init", "-q", "-b", "main")
        self.commit(DOCUMENTED, "2026-09-26T10:00:00+10:00")
        self.commit(DOCUMENTED.replace("length - 1", "length - 2"), "2026-09-26T12:00:00+10:00")
        repo = patch.object(reflect, "REPO", self.repo)
        repo.start()
        self.addCleanup(repo.stop)

    def git(self, *args: str, date: str = "") -> None:
        env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else None
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=self.repo, check=True, capture_output=True,
                       env=env)

    def commit(self, source: str, date: str) -> None:
        (self.repo / "loop/hunt.py").write_text(source)
        self.git("add", "-A")
        self.git("commit", "-qm", date, date=date)

    def test_the_comments_whose_code_changed_since_the_window_began_with_their_code(self) -> None:
        text = reflect.drift_list("2026-09-26T11:00+10:00")
        self.assertIn("the last main commit before 2026-09-26T11:00+10:00", text.splitlines()[0])
        self.assertIn("loop/hunt.py:1: reach: comment last changed", text)
        self.assertIn("    3      return length - 2", text)
        self.assertIn("None.", reflect.drift_list("2026-09-26T13:00+10:00"))  # nothing changed after the last commit

    def test_a_test_files_comments_are_left_out(self) -> None:
        test = '"""Tests for hunt."""\n\n' + DOCUMENTED
        (self.repo / "loop/test_hunt.py").write_text(test)
        self.git("add", "-A")
        self.git("commit", "-qm", "test", date="2026-09-26T10:30:00+10:00")
        (self.repo / "loop/test_hunt.py").write_text(test.replace("length - 1", "length - 2"))
        self.git("add", "-A")
        self.git("commit", "-qm", "test body", date="2026-09-26T12:30:00+10:00")
        text = reflect.drift_list("2026-09-26T11:00+10:00")
        self.assertNotIn("loop/test_hunt.py", text)  # neither its module docstring nor its function's comment
        self.assertIn("None.", text)

    def test_a_failure_is_a_one_line_note_and_a_long_list_is_cut(self) -> None:
        self.assertEqual(len(reflect.drift_list("2026-09-26T09:00+10:00").splitlines()), 1)  # no commit before it
        with patch.object(reflect, "DRIFT_CAP", 100):
            self.assertIn("... 1 more not shown (cut at 100 characters)", reflect.drift_list("2026-09-26T11:00+10:00"))
        (self.repo / "tools/check_refs.py").write_text("import sys\nsys.exit(3)\n")
        note = reflect.drift_list("2026-09-26T11:00+10:00")
        self.assertTrue(note.startswith("No drift list this time (RuntimeError: check_refs exited 3"), note)


if __name__ == "__main__":
    unittest.main()
