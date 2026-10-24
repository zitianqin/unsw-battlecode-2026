"""Focused runner checks without playing games; one compiles a tiny case (hardening)."""

import testhome  # before anything that reads live paths
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_tests


class TestBotRunner(unittest.TestCase):
    def setUp(self) -> None:
        # One case at a time: how many run at once reads this Mac's load (Workers).
        patcher = patch.object(bot_tests, "workers", return_value=1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_candidate_suite_replaces_outdated_shared_cases(self) -> None:
        candidate_case = Path("candidate/tests/bot/test_new_policy.cpp")
        with patch.object(Path, "glob", return_value=[candidate_case]), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(bot_tests.shutil, "which", return_value="clang++"), \
                patch.object(bot_tests.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot_tests.run_tests(Path("candidate/bot"), candidate_case.parent), 0)
        compiles = [call.args[0] for call in run.call_args_list if call.args[0][0] == "clang++"]
        self.assertEqual(len(compiles), 1)
        self.assertIn(str(candidate_case), compiles[0])
        self.assertIn(str(candidate_case.parent), compiles[0])

    def test_intentionally_empty_suite_is_reported_without_reinstating_old_tests(self) -> None:
        with tempfile.TemporaryDirectory() as folder, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bot_tests.run_tests(Path("bot"), Path(folder)), 0)
        self.assertIn("no bot tests selected", output.getvalue())

    def test_failed_case_makes_runner_fail(self) -> None:
        with patch.object(Path, "glob", return_value=[Path("tests/bot/test_failure.cpp")]), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(bot_tests.shutil, "which", return_value="clang++"), \
                patch.object(bot_tests.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CalledProcessError(1, "case")]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bot_tests.run_tests(Path("bot"), Path("tests/bot")), 1)

    def test_failed_protocol_makes_runner_fail(self) -> None:
        with patch.object(Path, "glob", return_value=[]), patch.object(Path, "exists", return_value=True), \
                patch.object(bot_tests.shutil, "which", return_value="clang++"), \
                patch.object(bot_tests.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CalledProcessError(1, "protocol")]) as run, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bot_tests.run_tests(Path("bot"), Path("candidate/tests/bot")), 1)
        self.assertEqual(run.call_args.args[0][1], "candidate/tests/bot/test_protocol.py")

    # MISSES 86: a candidate wrote rooms[-1] (a std::vector) in play. Built with libc++ hardening, a case that indexes
    # out of bounds fails instead of passing with a corrupted heap.
    def test_an_out_of_bounds_index_fails_its_case(self) -> None:
        if not (bot_tests.shutil.which("clang++") or bot_tests.shutil.which("c++")):
            self.skipTest("no C++ compiler")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "test_oob.cpp").write_text(
                "#include <vector>\nint main(int argc, char**) { std::vector<int> v(4); v[argc - 2] = 1; }\n")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as said:
                self.assertEqual(bot_tests.run_tests(Path(folder), Path(folder)), 1)
            self.assertIn(bot_tests.TRAPPED, said.getvalue())  # the failure says what the trap means


class Workers(unittest.TestCase):
    # A coder's sandbox (nice 10, ps refused) can't measure the work at its priority: the published split's dev work
    # stands in, not the 1-minute load, which counts every niced loop game (at 36 it left one worker, and a waiting
    # pool waited for hours). With no split either, the load average: 9.0 of 10 cores under a 0.8 share, one worker.
    def test_workers_in_a_sandbox_read_the_published_dev_work_before_the_load_average(self) -> None:
        home = bot_tests.home
        for split, load, expected in ((home.Split(2.0, 7.5, 0.0), 36.0, 6), (None, 9.0, 1)):
            with self.subTest(split=split), patch.object(home.CPU, "sample", return_value=None), \
                    patch.object(home.os, "getpriority", return_value=home.WORKER_NICE), \
                    patch.object(home, "published_split", return_value=split), \
                    patch.object(home.os, "getloadavg", return_value=(load, load, load)), \
                    patch.object(home.os, "cpu_count", return_value=10), \
                    patch.object(home, "share_now", return_value=0.8):
                self.assertEqual(bot_tests.workers(27), expected)


if __name__ == "__main__":
    unittest.main()
