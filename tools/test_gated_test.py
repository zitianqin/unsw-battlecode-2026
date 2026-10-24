"""Runs tools/gated_test.sh with fake `sysctl`, `getconf`, `python3`, `uptime` and `nproc` first on PATH (the load and
core count each source reports, or a failure) and a temporary loop home holding load-fraction, so these test the gate
itself, not this machine's load; and, on macOS, under a seatbelt profile like a coder's sandbox, with the real tools.

    .venv/bin/python -m unittest tools.test_gated_test
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "gated_test.sh"


class Gate(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        (self.scratch / "bin").mkdir()
        (self.scratch / "home").mkdir()

    # Runs `command` through the gate with the fake machine reporting `load` through sysctl ("{ l 1 1 }", or nothing)
    # on 10 cores, and load-fraction holding `fraction` (None: no file); GATED_MAX_WAIT 0, so a closed gate exits at
    # once. The other sources fail, except the real python3, which reads the share.
    def gate(self, load: str, fraction: str | None, *command: str) -> subprocess.CompletedProcess:
        loadavg = f"{{ {load} 1.00 1.00 }}" if load else ""
        return self.run_gate({"sysctl": f'case "$2" in hw.ncpu) echo 10;; vm.loadavg) echo "{loadavg}";; esac'},
                             fraction, *command)

    # Runs `command` through the gate with a fake for each tool in `tools` (its shell body), a failing fake for each
    # other source but python3, which stays real unless given (a failing ps: no split measured here, home.measure_now).
    def run_gate(self, tools: dict[str, str], fraction: str | None, *command: str,
                 max_wait: str = "0") -> subprocess.CompletedProcess:
        for name in ("sysctl", "getconf", "uptime", "nproc", "python3", "ps"):
            (self.scratch / "bin" / name).unlink(missing_ok=True)  # a fake from an earlier call
            if name in tools or name != "python3":
                fake = self.scratch / "bin" / name
                fake.write_text(f"#!/bin/sh\n{tools.get(name, 'exit 1')}\n")
                fake.chmod(0o755)
        if fraction is not None:
            (self.scratch / "home/load-fraction").write_text(fraction)
        env = {**os.environ, "PATH": f"{self.scratch / 'bin'}:{os.environ['PATH']}", "GATED_MAX_WAIT": max_wait,
               "BC_LOOP_HOME": str(self.scratch / "home")}
        return subprocess.run([str(SCRIPT), *command], env=env, capture_output=True, text=True, timeout=60)

    def test_it_runs_under_the_gate_and_keeps_the_commands_exit_code(self) -> None:
        done = self.gate("3.00", None, "sh", "-c", "exit 3")  # 3 < 0.7 x 10
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("under 0.7 x 10 cores", done.stdout)

    def test_the_gate_is_the_loops_load_share(self) -> None:
        # sig set load-fraction to 0.4 on 2026-09-27: load 5 on 10 cores passes the default 0.7 but not 0.4.
        self.assertEqual(self.gate("5.00", "0.4\n", "true").returncode, 75)
        self.assertEqual(self.gate("5.00", "0.7", "true").returncode, 0)
        self.assertEqual(self.gate("5.00", "not a number", "true").returncode, 0)  # home.load_share's default
        self.assertEqual(self.gate("8.50", "5", "true").returncode, 0)  # clamped to 0.9

    # E0366's merge turn (29 Sep) waited ~30 min at load 12-19 for its bot tests while the evaluation waited on it: a
    # session the evaluation waits on (BC_GATE_URGENT=1, driver.lane_env) gates only at the hard stop, load >= cores.
    def test_an_urgent_session_gates_only_at_the_hard_stop(self) -> None:
        from unittest.mock import patch
        with patch.dict(os.environ, {"BC_GATE_URGENT": "1"}):
            self.assertEqual(self.gate("9.50", "0.4", "true").returncode, 0)
            self.assertEqual(self.gate("10.00", "0.4", "true").returncode, 75)
        with patch.dict(os.environ, {"BC_GATE_URGENT": "0"}):
            self.assertEqual(self.gate("5.00", "0.4", "true").returncode, 75)

    # The share was read once at the start, so a load-fraction raised while tests waited never reached them.
    def test_a_waiting_gate_reads_the_share_again_at_each_check(self) -> None:
        import threading
        import time
        (self.scratch / "home/load-fraction").write_text("0.4")
        threading.Timer(3, lambda: (self.scratch / "home/load-fraction").write_text("0.7")).start()
        started = time.monotonic()
        done = self.run_gate({"sysctl": 'case "$2" in hw.ncpu) echo 10;; vm.loadavg) echo "{ 5.00 1.00 1.00 }";; esac'},
                             None, "true", max_wait="60")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertLess(time.monotonic() - started, 50)  # passed at the first check after the change

    def test_a_pipe_inside_keeps_its_status_only_with_its_own_pipefail(self) -> None:
        self.assertEqual(self.gate("1.00", None, "sh", "-c", "false | cat").returncode, 0)
        self.assertEqual(self.gate("1.00", None, "bash", "-o", "pipefail", "-c", "false | cat").returncode, 1)

    def test_dev_load_the_loops_games_published_replaces_the_load_average_while_fresh(self) -> None:
        # The loop's games here run at nice 19 and fill what dev work leaves, so the load average reads them as load;
        # the cores busy at normal priority they publish (home.published_split) gate tests instead.
        import json
        import time
        split = self.scratch / "home/cpu-split.json"  # home.split_copy under $BC_LOOP_HOME
        split.write_text(json.dumps({"at": time.time(), "dev": 2.5, "niced": 7.0}))
        done = self.gate("9.50", None, "true")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("load 2.5 under 0.7 x 10 cores", done.stdout)
        split.write_text(json.dumps({"at": time.time() - 60, "dev": 2.5, "niced": 7.0}))  # none published lately
        self.assertEqual(self.gate("9.50", None, "true").returncode, 75)

    # REFLECT 2026-09-28 20:52 F11: with no split published, the total load (mostly the loop's niced games) held tests
    # for hours. A ps whose one nice-19 process gains 100 CPU minutes between its two readings: all the busy CPU is
    # niced, dev is 0, so a load of 9.5 passes, and the line prints the one measurement it judged (two ps readings).
    def test_with_none_published_it_measures_the_dev_load_itself(self) -> None:
        count = self.scratch / "ps-calls"
        ps = (f'n=$(cat {count} 2>/dev/null || echo 0); echo $((n + 1)) > {count}; '
              f'echo "  4242    19 $((n * 100)):00.00"')
        done = self.run_gate({"sysctl": 'case "$2" in hw.ncpu) echo 10;; vm.loadavg) echo "{ 9.50 1 1 }";; esac',
                              "ps": ps}, None, "true")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("load 0.0 under 0.7 x 10 cores", done.stdout)
        self.assertEqual(count.read_text().strip(), "2")

    # A python3 fake answering the load and core-count one-liners (nothing given: that one fails) and failing the
    # share's, which then falls back to 0.7.
    @staticmethod
    def python(load: str = "", cores: str = "") -> str:
        return (f'case "$2" in *getloadavg*) [ -n "{load}" ] && echo {load} || exit 1;; '
                f'*cpu_count*) [ -n "{cores}" ] && echo {cores} || exit 1;; *) exit 1;; esac')

    def test_each_core_count_source_is_tried_in_turn(self) -> None:
        # E0172's sandbox refused `sysctl`, which then printed only an error; each source counts when those before it
        # print nothing numeric.
        refused = 'echo "sysctl: sysctl fmt -1 1024 1: Operation not permitted" >&2; exit 1'
        for tools, cores in (({"getconf": "echo 12"}, 12), ({"python3": self.python("1.00", "14")}, 14),
                             ({"getconf": "echo undefined", "nproc": "echo 16"}, 16)):
            with self.subTest(tools=tools):
                tools = {"sysctl": refused, "python3": self.python(), "uptime": "echo 'load averages: 1.00 1 1'",
                         **tools}
                done = self.run_gate(tools, None, "true")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"x {cores} cores", done.stdout)

    def test_each_load_source_is_tried_in_turn(self) -> None:
        # The real uptime formats: macOS, macOS inside the coder's sandbox (no boot time), Linux procps.
        for tools, load in (({"python3": self.python("3.5")}, "3.5"),
                            ({"uptime": "echo '17:37  up 4 days,  7:34, 2 users, load averages: 3.21 6.90 6.50'"},
                             "3.21"),
                            ({"uptime": "echo '17:37  2 users, load averages: 4.02 7.77 7.83'"}, "4.02"),
                            ({"uptime": "echo '17:51  up 4 days, 2 users, load averages: 3,80 8,24 8,01'"}, "3.80"),
                            ({"uptime": "echo ' 17:37:01 up 4 days,  7:34,  2 users,  load average: 1.00, 0.50, 0.25'"},
                             "1.00"),
                            ({"python3": self.python("None"), "uptime": "echo 'load averages: 2.50 1 1'"}, "2.50")):
            with self.subTest(tools=tools):
                tools = {"python3": self.python(), "getconf": "echo 10", **tools}
                done = self.run_gate(tools, None, "true")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"load {load} under 0.7 x 10 cores", done.stdout)

    @unittest.skipIf(Path("/proc/loadavg").exists(), "the last load source is this machine's own /proc/loadavg")
    def test_a_load_no_source_can_read_never_passes(self) -> None:
        tools = {"python3": self.python(cores="10"), "uptime": "echo '17:40  2 users, no load average information "
                                                               "available'"}
        done = self.run_gate(tools, None, "touch", str(self.scratch / "ran"))
        self.assertEqual(done.returncode, 75, done.stdout + done.stderr)
        self.assertIn("load '' is not under", done.stdout)
        self.assertFalse((self.scratch / "ran").exists())


# Runs the gate with the real tools under a seatbelt profile like a coder's: Claude Code's sandbox allows reading only
# listed sysctls (hw.ncpu and vm.loadavg among them, which the `sysctl` command still can't use) and the lane's
# settings deny reading the loop's state (driver.lane_settings), load-fraction included, so the gate reads the copy the
# upkeep keeps for coders. Its sysctl rule here is a subset of Claude Code's (2.1.281), so what works here works there.
# The real load decides whether the command runs, so these check what the gate read.
@unittest.skipUnless(shutil.which("sandbox-exec"), "macOS seatbelt only")
class Sandboxed(unittest.TestCase):
    def gate(self, sysctls: str, split: str = "") -> subprocess.CompletedProcess:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        home = Path(scratch.name).resolve() / "home"
        home.mkdir()
        if split:
            (home / "cpu-split.json").write_text(split)  # home.split_copy under $BC_LOOP_HOME
        (home / "load-fraction").write_text("0.9")
        (home / "load-fraction-copy").write_text("0.8")  # home.load_fraction_copy under $BC_LOOP_HOME
        profile = Path(scratch.name) / "coder.sb"
        profile.write_text(f"""(version 1)
(deny default)
(allow process-exec) (allow process-fork) (allow signal (target same-sandbox))
(allow file-read*) (deny file-read* (literal "{home / 'load-fraction'}"))
(allow file-write* (literal "/dev/null"))
(allow sysctl-read {sysctls})
""")
        env = {**os.environ, "GATED_MAX_WAIT": "0", "BC_LOOP_HOME": str(home)}
        return subprocess.run(["sandbox-exec", "-f", str(profile), str(SCRIPT), "true"], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_it_reads_the_load_and_cores_in_a_coders_sandbox(self) -> None:
        done = self.gate('(sysctl-name "hw.ncpu") (sysctl-name "vm.loadavg")')
        self.assertRegex(done.stdout, r"load '?[0-9]+(\.[0-9]+)?'? (is not )?under 0\.8 x [1-9][0-9]* cores",
                         done.stdout + done.stderr)  # 0.8: the copy's, as the sandbox can't read load-fraction's 0.9

    def test_a_coders_sandbox_reads_the_dev_load_the_loops_games_published(self) -> None:
        import json
        import time
        done = self.gate('(sysctl-name "hw.ncpu")', json.dumps({"at": time.time(), "dev": 2.5, "niced": 6.0}))
        self.assertIn("load 2.5 under 0.8 x", done.stdout, done.stdout + done.stderr)

    def test_a_sandbox_hiding_the_load_never_runs_the_command(self) -> None:
        done = self.gate('(sysctl-name "hw.ncpu")')
        self.assertEqual(done.returncode, 75, done.stdout + done.stderr)
        self.assertIn("load '' is not under", done.stdout)


if __name__ == "__main__":
    unittest.main()
