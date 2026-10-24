"""host_agent.py's operations run here in a temp folder, other systems' branches by patching the system name.
Run: .venv/bin/python -m unittest test_host_agent (from referee/)"""

import testhome  # before anything that reads live paths
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import host_agent

VM_STAT = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:  100000.\n"
           "Pages active:  500000.\nPages inactive:  60000.\nPages speculative:  2144.\n")
# A stand-in for `unswbc run MAP A B -o REPLAY`: prints its bots' names and writes a replay, or with a map named
# "slow" waits to be killed.
FAKE_UNSWBC = """#!{python}
import sys, time
from pathlib import Path
_, _, game_map, a, b, _, replay = sys.argv
if Path(game_map).name == "slow":
    time.sleep(60)
print(Path(a).name, "vs", Path(b).name)
Path(replay).write_bytes(b"replay of " + Path(game_map).read_bytes())
sys.exit(3)
"""


def call(op: str, args: dict, data: bytes = b"") -> tuple[dict, bytes]:
    return host_agent.OPS[op](args, data)


class TestHostAgent(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_load_reports_the_cores_busy_over_a_sample_on_each_system(self) -> None:
        # Not the load average: on 2026-09-27 ll's stood at 6-8 while its CPU was 2-32% busy (a game forks a bot process
        # per dragon), and the referee, filling cores less load, left most of ll idle.
        meminfo = "MemTotal:  16000000 kB\nMemAvailable:    8491804 kB\n"
        stats = iter(["cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3\n", "cpu  400 0 150 1400 50 0 0 0 0 0\n"])
        texts = {"/proc/loadavg": "7.70 7.00 6.50 6/2919 1234\n", "/proc/meminfo": meminfo}
        with patch.object(host_agent, "SYSTEM", "Linux"), \
                patch.object(host_agent, "read_text", lambda path: next(stats) if path == "/proc/stat" else texts[path]), \
                patch.object(host_agent.os, "sched_getaffinity", create=True, return_value=set(range(12))), \
                patch.object(host_agent.time, "sleep"):
            report = call("load", {})[0]
        # 400 of 1000 ticks busy: 40% of 12 cores, whatever the load average says
        self.assertEqual((report["cores"], round(report["busy"], 2), round(report["memory_gb"], 2)), (12, 4.8, 8.1))

        ticks = iter([(1000, 2000), (1000 + 250, 2000 + 1000)])
        with patch.object(host_agent, "SYSTEM", "Darwin"), patch.object(host_agent, "run", return_value=VM_STAT), \
                patch.object(host_agent.os, "cpu_count", return_value=10), \
                patch.object(host_agent, "darwin_times", lambda: next(ticks)), patch.object(host_agent.time, "sleep"):
            report = call("load", {})[0]
        self.assertEqual((report["cores"], report["busy"]), (10, 7.5))
        self.assertAlmostEqual(report["memory_gb"], 16384 * 162144 / 1024**3)

        # Windows: 8 cores 25% busy over the sample are 2 busy.
        samples = iter([(1000, 3000), (1000 + 750, 3000 + 1000)])
        with patch.object(host_agent, "SYSTEM", "Windows"), patch.object(host_agent.os, "cpu_count", return_value=8), \
                patch.object(host_agent, "windows_times", lambda: next(samples)), \
                patch.object(host_agent, "windows_available_bytes", return_value=6 * 1024**3), \
                patch.object(host_agent.time, "sleep"):
            report = call("load", {})[0]
        self.assertEqual((report["cores"], report["busy"], report["memory_gb"]), (8, 2.0, 6.0))

    def test_the_load_report_counts_every_processs_running_games(self) -> None:
        games = self.root / "games"
        done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        for name, pid in (("live", str(os.getpid())), ("ended", done.stdout.strip()), ("broken", "x")):
            (games / name).mkdir(parents=True)
            (games / name / "pid").write_text(pid)
        (games / "starting").mkdir()  # no pid yet
        self.assertEqual(host_agent.running_games(self.root), 1)
        with patch.object(host_agent.time, "sleep"):  # the load report's CPU sample (BUSY_S)
            self.assertEqual(call("load", {"root": str(self.root)})[0]["games"], 1)
            self.assertNotIn("games", call("load", {})[0])
        with patch.object(host_agent, "SYSTEM", "Windows"):  # no signal 0 there: a pid under an hour old counts
            self.assertEqual(host_agent.running_games(self.root), 2)
            self.assertEqual(host_agent.running_games(self.root, time.time() + 3601), 0)

    def test_this_machines_cpu_times_can_be_read(self) -> None:
        times = {"Darwin": host_agent.darwin_times, "Linux": host_agent.linux_times}.get(host_agent.SYSTEM)
        if times is None:
            self.skipTest(host_agent.SYSTEM)
        idle, total = times()
        self.assertGreater(total, idle)
        self.assertGreaterEqual(idle, 0)

    # MISSES 94: zz-wsl's crash left uploads renamed into place with 0 bytes, which every later game trusted.
    def test_an_upload_lands_whole_once_a_corrupt_one_is_refused_and_a_torn_one_rewritten(self) -> None:
        target = self.root / ".bc-remote/files/abc/bot"
        blob = b"\x7fELF binary"
        args = {"path": str(target), "sha256": hashlib.sha256(blob).hexdigest()}
        with self.assertRaises(host_agent.Failure):
            call("upload", args, blob[:-1])
        self.assertFalse(target.parent.exists())
        self.assertNotIn("repaired", call("upload", args, blob)[0])  # a first upload repairs nothing
        self.assertEqual(target.read_bytes(), blob)
        self.assertTrue(os.access(target, os.X_OK))
        with patch.object(host_agent.os, "replace") as replace:  # there already: not rewritten
            call("upload", args, blob)
        replace.assert_not_called()
        self.assertEqual([p.name for p in target.parent.iterdir()], ["bot"])  # no temporary left
        target.write_bytes(b"")  # torn by a crash: written again, and said
        self.assertIn(str(target), call("upload", args, blob)[0]["repaired"])
        self.assertEqual(target.read_bytes(), blob)

    def test_a_file_or_build_asked_for_again_is_kept_from_cleanup(self) -> None:
        # Cleanup drops files/ and builds/ entries past the newest KEEP unused for a day: engine_cache.py, uploaded
        # once and asked for by every game since, was due to vanish mid-tournament on every host.
        old = time.time() - 15 * 86400
        blob = b"launcher"
        target = self.root / ".bc-remote/files/abc/engine_cache.py"
        call("upload", {"path": str(target), "sha256": hashlib.sha256(blob).hexdigest()}, blob)
        build = self.root / ".bc-remote/builds/tc-bot"
        build.mkdir(parents=True)
        (build / ".bc-bot").write_text("bot")
        (build / "bot").write_bytes(b"\x7fELF bot")
        for path in (target, build):
            os.utime(path, (old, old))
        call("upload", {"path": str(target), "sha256": hashlib.sha256(blob).hexdigest()}, blob)
        call("build", {"folder": str(build), "toolchain": "unused"})
        with patch.object(host_agent, "KEEP", 0):
            host_agent.cleanup(self.root / ".bc-remote")
        self.assertTrue(target.is_file())
        self.assertTrue(build.is_dir())

    def test_a_windows_upload_that_lost_the_race_to_an_open_copy_still_succeeds(self) -> None:
        target = self.root / "files/abc/map"
        blob = b"MAP"

        def lose(source, destination):  # the other referee's copy lands first and is in use
            Path(destination).write_bytes(blob)
            raise PermissionError("in use")

        with patch.object(host_agent.os, "replace", side_effect=lose):
            call("upload", {"path": str(target), "sha256": hashlib.sha256(blob).hexdigest()}, blob)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["map"])

    def test_cleanup_keeps_the_newest_files_and_builds_and_anything_used_today(self) -> None:
        # mm held 1.5 GB in 44k files kept 14 days, and every referee run's first call scanned them all (1.6 s of CPU).
        now = time.time()
        paths = {"files/a/old": 2, "files/b/older": 3, "files/c/new": 0.5, "builds/old": 2, "builds/older": 3,
                 "builds/new": 0.5, "games/old": 2 / 24, "games/new": 0.5 / 24, "toolchains/old": 100,
                 "agent/x/host_agent.py": 100}  # age in days
        for relative, days in paths.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir() if relative.startswith(("builds", "games", "toolchains")) else path.write_text("x")
            os.utime(path, (now - days * 86400, now - days * 86400))
        with patch.object(host_agent, "KEEP", 2):  # the newest two of each, however old
            host_agent.cleanup(self.root, now)
        kept = sorted(p for p in paths if (self.root / p).exists())
        self.assertEqual(kept, ["agent/x/host_agent.py", "builds/new", "builds/old", "files/a/old", "files/c/new",
                                "games/new", "toolchains/old"])
        self.assertFalse((self.root / "files/b").exists())  # a file's folder goes with it
        os.utime(self.root / "games/new", (now - 7200, now - 7200))
        host_agent.cleanup(self.root, now + 60)  # within the hour: not scanned again
        self.assertTrue((self.root / "games/new").exists())
        host_agent.cleanup(self.root, now + host_agent.CLEAN_S + 1)
        self.assertFalse((self.root / "games/new").exists())

    def test_venv_programs_follow_each_systems_layout(self) -> None:
        folder = Path("t")
        with patch.object(host_agent, "SYSTEM", "Windows"):
            self.assertEqual(host_agent.venv_program(folder, "unswbc"), Path("t/Scripts/unswbc.exe"))
        with patch.object(host_agent, "SYSTEM", "Linux"):
            self.assertEqual(host_agent.venv_program(folder, "python"), Path("t/bin/python"))

    def test_a_ready_venv_that_lost_its_python_is_made_again(self) -> None:
        folder = self.root / "toolchains/k"
        (folder / "bin").mkdir(parents=True)
        (folder / ".ready").touch()  # as az-mel's was: marked ready, bin/python gone
        made = []

        def run(argv, timeout):
            made.append(argv[1])
            if argv[1] == "venv":
                (folder / "bin").mkdir(parents=True, exist_ok=True)
                (folder / "bin/python").write_text("")
                (folder / "bin/unswbc").write_text("")
            return "1.2.0\n"
        with patch.object(host_agent, "find_uv", return_value="uv"), patch.object(host_agent, "run", side_effect=run), \
                patch.object(host_agent, "cleanup"), patch.object(host_agent, "SYSTEM", "Linux"):
            report, _ = call("prepare", {"folder": str(folder), "python": "3.14.5", "root": str(self.root)}, b"x\n")
            self.assertEqual(report["unswbc"], "1.2.0")
            self.assertEqual(made[:2], ["venv", "pip"])
            self.assertTrue((folder / ".ready").is_file())
            made.clear()  # now whole: the next prepare only asks its version
            call("prepare", {"folder": str(folder), "python": "3.14.5", "root": str(self.root)}, b"x\n")
            self.assertNotIn("venv", made)
            (folder / "bin/unswbc").unlink()  # a lost unswbc is remade too
            call("prepare", {"folder": str(folder), "python": "3.14.5", "root": str(self.root)}, b"x\n")
            self.assertIn("venv", made)

    def test_a_game_returns_output_status_and_replay_and_leaves_no_folder(self) -> None:
        unswbc = self.root / "unswbc"
        unswbc.write_text(FAKE_UNSWBC.format(python=sys.executable))
        unswbc.chmod(0o755)
        (self.root / "m.map").write_bytes(b"MAP")
        folder = self.root / "games/g1"
        report, replay = call("play", {"unswbc": str(unswbc), "map": str(self.root / "m.map"),
                                       "bots": [str(self.root / "a"), str(self.root / "b")], "folder": str(folder)})
        self.assertEqual((report["stdout"], report["returncode"], report["replay"]), ("a vs b\n", 3, True))
        self.assertEqual(replay, b"replay of MAP")
        self.assertFalse(folder.exists())
        with self.assertRaises(host_agent.Failure):  # a toolkit that can't start is the host's failure
            call("play", {"unswbc": str(self.root / "missing"), "map": str(self.root / "m.map"),
                          "bots": ["a", "b"], "folder": str(folder)})

    def test_a_game_can_bring_its_map_and_send_its_replay_compressed(self) -> None:
        # One SSH call a game instead of two, and a 3.4 MB replay sent about 2.9x smaller.
        unswbc = self.root / "unswbc"
        unswbc.write_text(FAKE_UNSWBC.format(python=sys.executable))
        unswbc.chmod(0o755)
        game_map = self.root / "files/ab/m.map"
        play = {"unswbc": str(unswbc), "map": str(game_map), "bots": ["a", "b"], "folder": str(self.root / "games/g"),
                "map_sha256": hashlib.sha256(b"MAP").hexdigest(), "compress": "zstd"}
        self.assertTrue(host_agent.reads_stdin("play", play))
        self.assertFalse(host_agent.reads_stdin("play", {**play, "map_sha256": None}))  # nothing sent: never read
        report, blob = call("play", play, b"MAP")
        self.assertEqual(game_map.read_bytes(), b"MAP")
        from compression import zstd
        self.assertEqual((report["compressed"], zstd.decompress(blob)), ("zstd", b"replay of MAP"))
        with self.assertRaises(host_agent.Failure):  # a map that doesn't match its checksum plays nothing
            call("play", {**play, "map": str(self.root / "files/cd/m.map")}, b"MAP!")

    # MISSES 94: a referee process that stayed up across a host's crash sends nothing it sent before, so a game checks
    # its uploads against their names and its builds whole, and reports a torn one as not found, which the referee
    # answers by sending everything again (remote.Host.play).
    def test_a_game_whose_upload_or_build_was_torn_since_it_was_sent_plays_nothing(self) -> None:
        game_map = self.root / f"files/{hashlib.sha256(b'MAP').hexdigest()[:24]}/m.map"
        build = self.root / "builds/t-abc"
        for path, blob in ((game_map, b"MAP"), (build / ".bc-bot", b"bot\n"), (build / "bot", b"\x7fELF")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        play = {"unswbc": "unused", "map": str(game_map), "bots": [str(build / "bot"), "b"], "folder": "unused"}
        self.assertIsNone(host_agent.torn_input(play))
        for torn in (game_map, build / "bot"):
            with self.subTest(torn=torn.name):
                blob = torn.read_bytes()
                torn.write_bytes(b"")
                report, _ = call("play", play)
                self.assertEqual((report["returncode"], report["replay"]), (1, False))
                self.assertIn(f"not found: {torn}", report["stdout"])
                torn.write_bytes(blob)

    def test_a_build_sends_its_executable_and_another_host_installs_it_as_built(self) -> None:
        built = self.root / "builds/t-abc"
        (built / ".unswbc-build").mkdir(parents=True)
        (built / ".unswbc-build/bot").write_bytes(b"\x7fELF bot")
        (built / ".bc-bot").write_text(".unswbc-build/bot")
        report, blob = call("build", {"folder": str(built), "toolchain": "unused", "send": True})
        self.assertEqual((report["executable"], blob), (".unswbc-build/bot", b"\x7fELF bot"))
        twin = self.root / "other/builds/t-abc"
        args = {"folder": str(twin), "executable": report["executable"], "sha256": hashlib.sha256(blob).hexdigest()}
        self.assertEqual(call("install", args, blob)[0]["executable"], ".unswbc-build/bot")
        self.assertEqual((twin / ".unswbc-build/bot").read_bytes(), blob)
        self.assertTrue(os.access(twin / ".unswbc-build/bot", os.X_OK))
        self.assertEqual(sorted(p.name for p in twin.parent.iterdir()), ["t-abc"])  # no scratch folder left
        self.assertEqual(call("install", args, blob)[0]["executable"], ".unswbc-build/bot")  # there already
        (twin / ".unswbc-build/bot").write_bytes(b"")  # torn by a crash: installed again
        self.assertEqual(call("install", args, blob)[0]["executable"], ".unswbc-build/bot")
        self.assertEqual((twin / ".unswbc-build/bot").read_bytes(), blob)
        for bad in ({**args, "sha256": "0" * 64}, {**args, "folder": str(twin) + "2", "executable": "../../x"}):
            with self.subTest(bad=bad), self.assertRaises(host_agent.Failure):
                call("install", bad, blob)

    def test_the_compiler_identity_names_the_system_compiler_and_c_library(self) -> None:
        answers = {"-c": "/usr/bin/c++\nc++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\n",
                   "--version": "ldd (Ubuntu GLIBC 2.39-0ubuntu8.9) 2.39\nCopyright\n"}
        with patch.object(host_agent, "SYSTEM", "Linux"), patch.object(host_agent.platform, "machine", return_value="x86_64"), \
                patch.object(host_agent, "run", lambda argv, timeout=None: answers[argv[1]]):
            identity = host_agent.compiler_identity("python")
        self.assertEqual(identity, "Linux x86_64\n/usr/bin/c++\nc++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\n"
                                   "ldd (Ubuntu GLIBC 2.39-0ubuntu8.9) 2.39")
        with patch.object(host_agent, "run", side_effect=host_agent.Failure("no compiler")):
            self.assertIsNone(host_agent.compiler_identity("python"))  # each host then builds its own

    def test_a_recorded_game_runs_the_recorder_with_the_toolkits_python_and_returns_both_files(self) -> None:
        toolkit = self.root / "toolchain"
        (toolkit / "bin").mkdir(parents=True)
        (toolkit / "bin" / "python").symlink_to(sys.executable)
        (toolkit / "bin" / "unswbc").write_text("not run: record.py plays the game")
        script = self.root / "record.py"
        script.write_text("import sys\nfrom pathlib import Path\nargs = sys.argv[1:]\n"
                          "Path(args[args.index('-o') + 1]).write_bytes(b'REPLAY')\n"
                          "Path(args[args.index('--recording') + 1])"
                          ".write_bytes(args[args.index('--team') + 1].encode())\n")
        (self.root / "m.map").write_bytes(b"MAP")
        report, blob = call("play", {"unswbc": str(toolkit / "bin" / "unswbc"), "map": str(self.root / "m.map"),
                                     "bots": ["a", "b"], "folder": str(self.root / "games/g"),
                                     "record": {"script": str(script), "team": "B"}})
        self.assertEqual((report["returncode"], report["replay"], report["recording"]), (0, True, 1))
        self.assertEqual(blob, b"REPLAY" + b"B")

    def test_with_engine_the_launcher_runs_the_same_command_with_the_hosts_cache(self) -> None:
        toolkit = self.root / "toolchain"
        (toolkit / "bin").mkdir(parents=True)
        (toolkit / "bin" / "python").symlink_to(sys.executable)
        (toolkit / "bin" / "unswbc").write_text("not run: the launcher plays the game")
        launcher = self.root / "engine_cache.py"  # writes its arguments as the replay, and the recording if asked
        launcher.write_text("import json, sys\nfrom pathlib import Path\nargs = sys.argv[1:]\n"
                            "Path(args[args.index('-o') + 1]).write_text(json.dumps(args))\n"
                            "'--recording' in args and Path(args[args.index('--recording') + 1]).write_bytes(b'R')\n")
        (self.root / "m.map").write_bytes(b"MAP")
        play = {"unswbc": str(toolkit / "bin" / "unswbc"), "map": str(self.root / "m.map"), "bots": ["a", "b"],
                "folder": str(self.root / "games/g"), "seed": 7,
                "engine": {"script": str(launcher), "cache": ".bc-remote/engine-cache"}}
        cache = str(Path(".bc-remote/engine-cache").resolve())
        game = [str((self.root / "m.map").resolve()), str(Path("a").resolve()), str(Path("b").resolve()), "-o"]
        report, blob = call("play", play)
        self.assertEqual(report["returncode"], 0)
        self.assertEqual(json.loads(blob)[:6], [cache, "run", *game])
        self.assertEqual(json.loads(blob)[-2:], ["--seed", "7"])
        report, blob = call("play", {**play, "record": {"script": str(self.root / "record.py"), "team": "A"}})
        args = json.loads(blob[:-report["recording"]])
        self.assertEqual(args[:6], [cache, str((self.root / "record.py").resolve()), *game])
        self.assertIn("--recording", args)
        self.assertEqual(blob[-1:], b"R")
        # With `fork`, fork_server.py (here the same argument-writing fake) runs that command in turn.
        engine = str(launcher.resolve())
        for fork, where in (({"shim": "files/ab/fork-shim.dylib", "check": True},
                             ["--shim", str(Path("files/ab/fork-shim.dylib").resolve()), "--check"]),
                            ({"build": ".bc-remote/fork-server", "check": False},
                             ["--build", str(Path(".bc-remote/fork-server").resolve())])):
            with self.subTest(fork=fork):
                report, blob = call("play", {**play, "fork": {"script": str(launcher), **fork}})
                self.assertEqual(report["returncode"], 0)
                self.assertEqual(json.loads(blob)[:len(where) + 3], [*where, engine, cache, "run"])

    def test_kill_stops_a_running_game_and_removes_its_folder(self) -> None:
        unswbc = self.root / "unswbc"
        unswbc.write_text(FAKE_UNSWBC.format(python=sys.executable))
        unswbc.chmod(0o755)
        (self.root / "slow").write_bytes(b"MAP")
        folder = self.root / "games/g2"
        play = {"unswbc": str(unswbc), "map": str(self.root / "slow"), "bots": ["a", "b"], "folder": str(folder)}
        game = subprocess.Popen([sys.executable, host_agent.__file__, "play", host_agent.encode(play)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(game.kill)
        deadline = time.monotonic() + 20
        while not (folder / "pid").is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        started = time.monotonic()
        self.assertTrue(call("kill", {"folder": str(folder)})[0]["killed"])
        out, _ = game.communicate(timeout=20)
        self.assertLess(time.monotonic() - started, 10)  # the 60 s game was stopped
        report = json.loads(out[len(host_agent.MARK):].split(b"\n")[0])
        self.assertEqual((report["returncode"], report["replay"]), (128 + 15, False))  # SIGTERM, as a shell says
        self.assertFalse(folder.exists())

    # A kill can reach the host before the play it stops has started its game (a scheduler review, 2026-09-27): it
    # found no pid, removed the folder, and the play then started the game anyway, playing to the end unwatched.
    def test_a_kill_before_the_game_starts_stops_it_from_starting(self) -> None:
        folder = self.root / "games/g4"
        self.assertEqual(call("kill", {"folder": str(folder)})[0], {"killed": False})
        self.assertTrue((folder / host_agent.KILLED).is_file())  # left for the play, and for cleanup
        (self.root / "m.map").write_bytes(b"MAP")
        play = {"unswbc": str(self.root / "missing"), "map": str(self.root / "m.map"), "bots": ["a", "b"],
                "folder": str(folder)}  # no toolkit there: running anything would fail the call
        report, blob = call("play", play)
        self.assertEqual((report["returncode"], report["replay"], blob), (128 + 15, False, b""))
        self.assertFalse(folder.exists())

    def test_a_kill_between_the_check_and_the_pid_stops_the_game_the_play_just_started(self) -> None:
        unswbc = self.root / "unswbc"
        unswbc.write_text(FAKE_UNSWBC.format(python=sys.executable))
        unswbc.chmod(0o755)
        (self.root / "slow").write_bytes(b"MAP")
        folder = self.root / "games/g5"
        popen = subprocess.Popen

        def kill_arrives(*args, **kwargs):  # the kill's marker lands as the game starts, before its pid is written
            process = popen(*args, **kwargs)
            self.addCleanup(process.kill)
            self.assertEqual(call("kill", {"folder": str(folder)})[0], {"killed": False})
            return process

        started = time.monotonic()
        with patch.object(host_agent.subprocess, "Popen", side_effect=kill_arrives):
            report, _ = call("play", {"unswbc": str(unswbc), "map": str(self.root / "slow"), "bots": ["a", "b"],
                                      "folder": str(folder)})
        self.assertLess(time.monotonic() - started, 10)  # the 60 s game stopped itself
        self.assertEqual((report["returncode"], report["replay"]), (128 + 15, False))
        self.assertFalse(folder.exists())

    def test_windows_kill_ends_the_whole_process_tree(self) -> None:
        folder = self.root / "games/g3"
        folder.mkdir(parents=True)
        (folder / "pid").write_text("4242")
        with patch.object(host_agent, "SYSTEM", "Windows"), patch.object(host_agent.subprocess, "run") as run:
            call("kill", {"folder": str(folder)})
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        self.assertFalse(folder.exists())

    def test_a_bot_is_built_with_the_toolkit_into_its_folder_once(self) -> None:
        toolchain = Path(sys.executable).parent.parent  # this venv, which has unswbc
        if not host_agent.venv_program(toolchain, "python").exists():
            self.skipTest("not run from a venv")
        sources = io.BytesIO()
        with tarfile.open(fileobj=sources, mode="w") as tar:
            for name, text in (("main.cpp", "int main() { return 0; }\n"),
                               ("bot.toml", '[project]\nlanguage = "c++"\ninclude = ["*.cpp"]\n')):
                info = tarfile.TarInfo(name)
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text.encode()))
        done = self.root / "builds/t-abc"
        args = {"folder": str(done), "toolchain": str(toolchain)}
        executable = call("build", args, sources.getvalue())[0]["executable"]
        self.assertEqual(executable, ".unswbc-build/bot")
        self.assertTrue(os.access(done / executable, os.X_OK))
        self.assertEqual([p.name for p in done.parent.iterdir()], ["t-abc"])  # no scratch folder left
        with patch.object(host_agent, "run") as run:  # built already
            self.assertEqual(call("build", args, b"")[0]["executable"], executable)
        run.assert_not_called()

    # MISSES 94: zz-wsl's crash left a build's marker and executable at 0 bytes, and a marker there was trusted for good.
    # A build is reused only while its executable is there, non-empty and matches the sha256 its marker gives.
    def test_a_torn_build_is_built_again(self) -> None:
        done, bot = self.root / "builds/t-abc", b"\x7fELF bot"
        sources = io.BytesIO()
        tarfile.open(fileobj=sources, mode="w").close()
        args = {"folder": str(done), "toolchain": "t"}

        def build(argv: list[str], timeout: float) -> str:  # the toolkit's build, into the scratch folder given
            (Path(argv[-1]) / ".unswbc-build").mkdir()
            (Path(argv[-1]) / ".unswbc-build/bot").write_bytes(bot)
            return ".unswbc-build/bot\n"

        for torn in ({".bc-bot": b"", ".unswbc-build/bot": b""},  # as zz-wsl's was
                     {".bc-bot": b".unswbc-build/bot", ".bc-bot.sha256": hashlib.sha256(bot).hexdigest().encode(),
                      ".unswbc-build/bot": bot[:4]}):  # cut short: not the bytes its sha256 names
            with self.subTest(torn=torn):
                for name, blob in torn.items():
                    (done / name).parent.mkdir(parents=True, exist_ok=True)
                    (done / name).write_bytes(blob)
                with patch.object(host_agent, "run", side_effect=build) as run:
                    report = call("build", args, sources.getvalue())[0]
                    self.assertEqual(report["executable"], ".unswbc-build/bot")
                    self.assertIn(str(done), report["repaired"])
                    self.assertEqual((done / ".unswbc-build/bot").read_bytes(), bot)
                    # the executable's path alone, as an agent from before reads it; its sha256 beside it
                    self.assertEqual((done / ".bc-bot").read_text(), ".unswbc-build/bot")
                    self.assertEqual([p.name for p in done.parent.iterdir()], ["t-abc"])  # nothing set aside left
                    call("build", args)  # whole now: reused
                run.assert_called_once()

    # Another referee's whole rebuild, landed since this one found the build torn, is put back, not removed.
    def test_discarding_a_whole_build_puts_it_back(self) -> None:
        done = self.root / "builds/t-abc"
        done.mkdir(parents=True)
        (done / "bot").write_bytes(b"\x7fELF bot")
        (done / ".bc-bot").write_text("bot")
        self.assertFalse(host_agent.discard(done))
        self.assertEqual((host_agent.built(done), [p.name for p in done.parent.iterdir()]), ("bot", ["t-abc"]))

    def test_a_failed_build_leaves_nothing(self) -> None:
        toolchain = Path(sys.executable).parent.parent
        sources = io.BytesIO()
        with tarfile.open(fileobj=sources, mode="w") as tar:
            info = tarfile.TarInfo("main.cpp")
            info.size = 6
            tar.addfile(info, io.BytesIO(b"broken"))
        with self.assertRaises(host_agent.Failure):
            call("build", {"folder": str(self.root / "builds/t-bad"), "toolchain": str(toolchain)}, sources.getvalue())
        self.assertEqual(list((self.root / "builds").iterdir()), [])

    # MISSES 86's crash detector read only this Mac's reports while half the games play on the Mac hosts: crashes
    # sends each bot report's build UUID and time since `since` (the header of a real report of E0287's build), and
    # nothing of an older report, another program's, or any report's contents beyond its header.
    def test_crashes_sends_each_new_bot_reports_build_uuid_and_time_alone(self) -> None:
        header = (Path(__file__).parent.parent / "loop/testdata/crash-report-header-sample.ips").read_text()
        uid = json.loads(header)["slice_uuid"]
        reports = self.root / "DiagnosticReports"
        reports.mkdir()
        (reports / "bot-2026-09-29-030016.ips").write_text(header + '{"threads": "secret"}\n')
        (reports / "bot-2026-09-20-010000.ips").write_text(header)
        os.utime(reports / "bot-2026-09-20-010000.ips", (1000, 1000))
        (reports / "Safari-2026-09-29-030016.ips").write_text(header.replace('"bot"', '"Safari"'))
        with patch.object(host_agent, "CRASH_REPORTS", reports):
            result, blob = call("crashes", {"name": "bot", "since": 2000})
            self.assertEqual(call("crashes", {"name": "bot", "since": time.time() + 60})[0], {"reports": []})
        at = (reports / "bot-2026-09-29-030016.ips").stat().st_mtime
        self.assertEqual((result, blob), ({"reports": [{"uuid": uid.lower(), "at": at}]}, b""))
        with patch.object(host_agent, "CRASH_REPORTS", self.root / "none"):  # Linux, Windows: no such folder
            self.assertEqual(call("crashes", {"name": "bot", "since": 0})[0], {"reports": []})

    def test_the_command_line_round_trips_args_and_a_payload(self) -> None:
        target = self.root / "x/y.bin"
        blob = bytes(range(256)) * 4
        args = {"path": str(target), "sha256": hashlib.sha256(blob).hexdigest()}
        encoded = host_agent.encode(args)
        self.assertRegex(encoded, r"^[A-Za-z0-9_-]+$")  # nothing any shell would touch
        done = subprocess.run([sys.executable, host_agent.__file__, "upload", encoded], input=blob,
                              capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith(host_agent.MARK))
        self.assertEqual(target.read_bytes(), blob)
        failed = subprocess.run([sys.executable, host_agent.__file__, "upload", encoded], input=b"x",
                                capture_output=True, timeout=30)
        self.assertEqual(failed.returncode, 1)
        self.assertIn(b"checksum", failed.stderr)


class TestRuntimeBundle(unittest.TestCase):
    def test_the_dlls_a_windows_bot_names_are_copied_beside_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mingw").mkdir()
            compiler = root / "mingw/g++.exe"
            compiler.write_bytes(b"")
            for name in ("libgcc_s_seh-1.dll", "libunused.dll", "libwinpthread-1.dll"):
                (root / "mingw" / name).write_bytes(b"dll")
            (root / "mingw/libstdc++-6.dll").write_bytes(b"dll imports libwinpthread-1.dll")  # needed only by it
            (root / "bot").mkdir()
            exe = root / "bot/bot.exe"
            exe.write_bytes(b"MZ...LIBSTDC++-6.DLL...libgcc_s_seh-1.dll...KERNEL32.dll")
            self.assertEqual(sorted(host_agent.bundle_runtime(exe, str(compiler))),
                             ["libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"])
            self.assertEqual(host_agent.bundle_runtime(exe, str(compiler)), [])  # already there


if __name__ == "__main__":
    unittest.main()
