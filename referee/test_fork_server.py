"""Tests for fork_server.py: its guards and fallbacks, a server's processes as unswbc.bot.Bot uses them, the referee's
choice of forked and checked games, and real games through the launcher byte for byte as `unswbc run` plays them
(needs a C compiler and a C++20 one, no network). Run: .venv/bin/python -m unittest test_fork_server (from referee/)"""

import testhome  # before anything that reads live paths
import contextlib
import errno
import functools
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

import unswbc.bot
import unswbc.run

import fork_server
import record as record_module
import run
import test_record

HERE = Path(__file__).resolve().parent
UNSWBC = str(Path(sys.executable).parent / "unswbc")
# A bot that reports how it started: its open descriptors, its environment's size and whether either preload variable
# is left, whether its own static initialiser ran in its own process and errno then; then exits 7 at the end of its
# input, or dies of SIGSEGV on "segv".
TOY = r"""
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
extern char **environ;
static int initialised_in, initial_errno;
__attribute__((constructor)) static void initialise(void) { initial_errno = errno; initialised_in = getpid(); }
int main(void) {
    int open = 0, count = 0;
    for (int fd = 0; fd < 64; fd++) if (fcntl(fd, F_GETFD) != -1) open |= 1 << (fd < 30 ? fd : 30);
    for (char **e = environ; *e; e++) count++;
    char line[256];
    while (fgets(line, sizeof line, stdin)) {
        if (strcmp(line, "sleep\n") == 0) sleep(30);
        if (strcmp(line, "segv\n") == 0) raise(SIGSEGV);
        printf("fds %x env %d initialised here %d errno %d preload %d control %d: %s", open, count,
               initialised_in == getpid(), initial_errno, getenv("DYLD_INSERT_LIBRARIES") || getenv("LD_PRELOAD"),
               getenv("BC_FORK_SERVER_FD") != NULL, line);
        fflush(stdout);
        fprintf(stderr, "noise\n");
    }
    return 7;
}
"""


def build_toy(folder: Path) -> Path:
    (folder / "toy.c").write_text(TOY)
    subprocess.run(["cc", "-O2", "-o", str(folder / "toy"), str(folder / "toy.c")], check=True)
    return folder / "toy"


# Undoes install(): Pool's methods and unswbc.run.execute as the toolkit has them.
def restore_toolkit(case: unittest.TestCase) -> None:
    pool = unswbc.bot.Pool
    for name in ("__init__", "_launch", "close"):
        case.addCleanup(setattr, pool, name, getattr(pool, name))
    case.addCleanup(setattr, unswbc.run, "execute", unswbc.run.execute)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


class TestGuards(unittest.TestCase):
    def setUp(self) -> None:
        restore_toolkit(self)
        self.folder = Path(tempfile.mkdtemp(prefix="fork-server-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.folder)])

    def test_this_toolkit_is_known(self) -> None:
        self.assertIsNone(fork_server.unsupported())

    def test_another_toolkit_is_left_alone_with_a_reason(self) -> None:
        methods = {name: getattr(unswbc.bot.Pool, name) for name in ("__init__", "_launch", "close")}
        said = io.StringIO()
        with patch.object(fork_server, "KNOWN_TOOLKITS", {"0123456789abcdef"}), contextlib.redirect_stderr(said):
            self.assertFalse(fork_server.install(self.folder / "shim.dylib", check=True))
            shim, reason = fork_server.warm(self.folder / "shims")
        self.assertEqual({name: getattr(unswbc.bot.Pool, name) for name in methods}, methods)
        self.assertIn(f"digest {fork_server.toolkit_digest()}", said.getvalue())
        self.assertTrue(said.getvalue().startswith("fork server: off ("))
        self.assertEqual((shim, "KNOWN_TOOLKITS" in reason), (None, True))
        self.assertFalse((self.folder / "shims").exists())

    def test_windows_and_a_preload_already_set_are_refused(self) -> None:
        with patch.object(fork_server.sys, "platform", "win32"):
            self.assertIn("win32", fork_server.unsupported())
        with patch.dict(os.environ, {fork_server.PRELOAD: "/somewhere/else.so"}):
            self.assertIn("already set", fork_server.unsupported())


class TestShim(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="fork-server-")) / "fork-server"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.folder.parent)])

    def test_the_shim_is_built_once_owner_only_and_reused(self) -> None:
        shim, reason = fork_server.warm(self.folder)
        self.assertIsNone(reason)
        self.assertEqual(shim, fork_server.shim_path(self.folder))
        self.assertEqual(oct(self.folder.stat().st_mode & 0o777), "0o700")
        self.assertEqual(shim.stat().st_mode & 0o022, 0)
        self.assertEqual([p.name for p in self.folder.iterdir()], [shim.name])  # no temporary left
        with patch.object(fork_server.subprocess, "run", side_effect=AssertionError("compiled again")):
            self.assertEqual(fork_server.warm(self.folder), (shim, None))
        shim.write_bytes(b"")  # torn by a crash (MISSES 94): built again
        self.assertEqual(fork_server.warm(self.folder), (shim, None))
        self.assertGreater(shim.stat().st_size, 0)

    def test_files_others_could_have_written_are_never_used(self) -> None:
        shim, _ = fork_server.warm(self.folder)
        for path in (shim, self.folder):
            with self.subTest(path.name):
                mode = path.stat().st_mode & 0o777
                path.chmod(mode | 0o022)
                self.addCleanup(path.chmod, mode)
                self.assertEqual(fork_server.warm(self.folder)[0], None)
                self.assertIn("could have been written by someone else", fork_server.warm(self.folder)[1])
                path.chmod(mode)

    def test_no_compiler_or_a_folder_out_of_reach_is_a_reason_not_an_error(self) -> None:
        with patch.object(fork_server.shutil, "which", return_value=None):
            self.assertIn("no C compiler", fork_server.warm(self.folder)[1])
        locked = self.folder.parent / "locked"
        locked.mkdir(mode=0o500)  # like the loop home seen from a coder's sandbox
        self.addCleanup(locked.chmod, 0o700)
        self.assertIsNotNone(fork_server.warm(locked / "fork-server")[1])


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="fork-server-"))
        cls.toy = build_toy(cls.root)
        cls.shim, _ = fork_server.warm(cls.root / "fork-server")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["rm", "-rf", str(cls.root)])

    def setUp(self) -> None:
        restore_toolkit(self)
        fork_server._said.clear()

    def server(self) -> fork_server.Server:
        server = fork_server.Server([str(self.toy)], str(self.root), self.shim)
        self.addCleanup(server.close)
        return server

    def test_a_forked_process_starts_as_a_fresh_one_would(self) -> None:
        plain = subprocess.run([str(self.toy)], input=b"hi\n", capture_output=True, cwd=self.root)
        process, stdout, stderr = fork_server.launch(self.server())
        process.stdin.write(b"hi\n")
        process.stdin.close()
        self.assertEqual(process.wait(10), plain.returncode)
        self.assertEqual((stdout.read(1.0), stderr.read(1.0)), (plain.stdout, plain.stderr))
        self.assertIn(b"fds 7 ", plain.stdout)  # stdin, stdout and stderr only, and no preload or control variable
        self.assertIn(b"initialised here 1 errno ", plain.stdout)  # errno as the executable's initialisers see it
        self.assertIn(b"preload 0 control 0", plain.stdout)

    def test_kill_and_wait_answer_as_popen_does(self) -> None:
        server = self.server()
        process, _, _ = fork_server.launch(server)
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(0.05)  # Bot._reason's wait for a process still running
        self.assertIsNone(process.poll())
        process.kill()
        self.assertEqual((process.wait(), process.returncode), (-9, -9))
        self.assertTrue(gone(process.pid))
        process.kill()  # killing it again is a no-op, as with Popen
        ended, _, _ = fork_server.launch(server)
        ended.stdin.close()
        self.assertEqual(ended.wait(10), 7)
        self.assertTrue(gone(ended.pid))

    def test_closing_kills_and_reaps_every_process_and_ends_the_server(self) -> None:
        server = self.server()
        processes = [fork_server.launch(server)[0] for _ in range(5)]
        processes[0].stdin.write(b"sleep\n")
        server.close()
        self.assertEqual(server.process.returncode, 0)
        self.assertTrue(all(gone(p.pid) for p in processes))

    def test_a_game_that_dies_takes_its_server_and_processes_with_it(self) -> None:
        server = self.server()
        processes = [fork_server.launch(server)[0] for _ in range(3)]
        server._socket.close()  # as when the game's process ends without closing its pools
        server.process.wait(10)
        self.assertTrue(all(gone(p.pid) for p in processes))

    def test_a_pool_forks_its_processes_and_closes_its_server(self) -> None:
        self.assertTrue(fork_server.install(self.shim))
        pool = unswbc.bot.Pool([str(self.toy)], cwd=str(self.root), size=2)
        server = pool._server
        taken = pool.take()
        self.assertIsInstance(taken[0], fork_server.Child)
        taken[0].kill()
        pool.close()
        self.assertIsNone(pool._server)
        self.assertEqual(server.process.returncode, 0)
        # Python bots (the toolkit's zygote) are left to the toolkit.
        with patch.object(unswbc.bot.Pool, "_fill"):
            self.assertIsNone(unswbc.bot.Pool([sys.executable, unswbc.bot.ZYGOTE, "main.py"])._server)

    def test_a_bot_the_shim_cannot_start_in_starts_as_usual_said_once(self) -> None:
        self.assertTrue(fork_server.install(self.root / "missing.dylib"))
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            pools = [unswbc.bot.Pool([str(self.toy)], cwd=str(self.root), size=1) for _ in range(2)]
        for pool in pools:
            self.addCleanup(pool.close)
            self.assertIsNone(pool._server)
            self.assertIsInstance(pool.take()[0], subprocess.Popen)
        self.assertEqual(said.getvalue().count("fork server: off for toy (the shim didn't start in toy"), 1)

    def test_a_server_gone_mid_game_leaves_the_team_to_start_as_usual(self) -> None:
        self.assertTrue(fork_server.install(self.shim))
        pool = unswbc.bot.Pool([str(self.toy)], cwd=str(self.root), size=1)
        self.addCleanup(pool.close)
        forked = pool.take()[0]  # the spare, forked; taking it forks the next, while the server lives
        pool._server.process.kill()
        pool._server.process.wait()
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            later = pool.take()[0]
        self.assertIsInstance(later, (fork_server.Child, subprocess.Popen))
        self.assertIsInstance(pool._spares[0][0], subprocess.Popen)
        self.assertIn("fork server: stopped for toy", said.getvalue())
        # Its orphans are never signalled (whoever reaps them may reuse the pid): the end of their input ends them.
        with patch.object(fork_server.os, "kill", side_effect=AssertionError("signalled an orphan's pid")):
            forked.kill()
        self.assertEqual((forked.returncode, forked.stdin.closed), (-9, True))
        deadline = time.monotonic() + 10
        while not gone(forked.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(gone(forked.pid))
        later.kill()

    # A server that doesn't answer within REQUEST_S (here stopped with SIGSTOP) is asked nothing more: a late reply
    # would be read as the next request's. The team starts its processes as usual, and the game goes on.
    def test_a_server_that_hangs_is_asked_nothing_more(self) -> None:
        self.assertTrue(fork_server.install(self.shim))
        with patch.object(fork_server, "REQUEST_S", 0.3):
            pool = unswbc.bot.Pool([str(self.toy)], cwd=str(self.root), size=1)
        self.addCleanup(pool.close)
        forked = pool.take()[0]
        server = pool._server
        os.kill(server.process.pid, signal.SIGSTOP)
        self.addCleanup(os.kill, server.process.pid, signal.SIGCONT)
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            pool.take()
        self.assertIn("fork server: stopped for toy", said.getvalue())
        self.assertIsInstance(pool._spares[0][0], subprocess.Popen)
        with patch.object(server, "_socket", unittest.mock.Mock(side_effect=AssertionError, **{
                "sendall.side_effect": AssertionError("asked again"), "recv.side_effect": AssertionError("read")})):
            forked.kill()
            self.assertIsNone(server.wait(forked.pid, 0.05))
        self.assertTrue(forked.stdin.closed)

    def test_a_process_killed_by_a_signal_exits_as_under_popen(self) -> None:
        plain = subprocess.run([str(self.toy)], input=b"segv\n", capture_output=True, cwd=self.root)
        process, _, _ = fork_server.launch(self.server())
        process.stdin.write(b"segv\n")
        self.assertEqual((process.wait(10), plain.returncode), (-11, -11))

    # A fork refused (EAGAIN at this user's process limit, as on this loaded Mac, E0062) raises OSError as Popen does,
    # so the game fails with the engine's OS error and the referee replays it; it never closes the live server, which
    # would kill the team's dragons in play (and the toolkit would restart them fresh: another game, exit 0).
    def test_a_refused_fork_raises_as_popen_and_leaves_the_server_and_its_processes_alive(self) -> None:
        self.assertTrue(fork_server.install(self.shim))
        pool = unswbc.bot.Pool([str(self.toy)], cwd=str(self.root), size=1)
        self.addCleanup(pool.close)
        playing = pool.take()[0]  # a dragon in play; the pool forked the next spare
        server, ask = pool._server, pool._server._ask
        refuse = lambda op, *a, **k: (-35, 0, 0) if op == "S" else ask(op, *a, **k)  # noqa: E731 (35: EAGAIN on Mac)
        with patch.object(server, "_ask", refuse), self.assertRaises(OSError) as raised:
            pool.take()
        self.assertNotIsInstance(raised.exception, fork_server.ServerGone)
        self.assertEqual(raised.exception.errno, 35)
        self.assertIsNone(server.process.poll())
        self.assertIsNone(playing.poll())  # still in play
        self.assertEqual(pool._forking, True)
        again = pool.take()[0]  # the fork works again: still forked by the same server
        self.assertIsInstance(again, fork_server.Child)
        for process in (playing, again):
            process.kill()
        # A real refusal: a server whose own process limit is below this user's process count.
        import resource

        limited = functools.partial(subprocess.Popen, preexec_fn=lambda: resource.setrlimit(
            resource.RLIMIT_NPROC, (1, resource.getrlimit(resource.RLIMIT_NPROC)[1])))
        with patch.object(fork_server.subprocess, "Popen", limited):
            capped = self.server()
        with self.assertRaises(OSError) as raised:
            fork_server.launch(capped)
        self.assertEqual(raised.exception.errno, errno.EAGAIN)
        self.assertIsNone(capped.process.poll())


class TestCheck(unittest.TestCase):
    # checked() plays plain first (its exit and replay are the game's), then forked into a file kept aside.
    def test_a_check_says_identical_or_mismatch_and_keeps_the_plain_game(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            replay = Path(folder, "game.replay")
            for forked_bytes, verdict in ((b"GAME", "check identical on m.map"), (b"OTHER", "MISMATCH on m.map")):
                plays = []

                def execute(map_arg, bots, verbose, replay_path, no_replay, *rest):
                    plays.append(state.on)
                    print("output")
                    Path(replay_path).write_bytes(b"GAME" if len(plays) == 1 else forked_bytes)
                    return 0

                state = type("State", (), {"on": True})()
                said, shown = io.StringIO(), io.StringIO()
                with contextlib.redirect_stderr(said), contextlib.redirect_stdout(shown):
                    code = fork_server.checked(execute, state)("m.map", ["a", "b"], False, str(replay), False)
                self.assertEqual((code, plays, shown.getvalue()), (0, [False, True], "output\n"))
                self.assertIn(f"fork server: {verdict}", said.getvalue())
                self.assertEqual(replay.read_bytes(), b"GAME")
                self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["game.replay"])

    # The forked half going wrong never costs the plain game: an exception there is a mismatch (the fork server made
    # a game it couldn't finish), an OS error (a fork refused at the process limit) or a team that started its
    # processes as usual gives no verdict, and the plain game's exit is the game's either way.
    def test_a_forked_half_that_fails_or_falls_back_leaves_the_plain_game_standing(self) -> None:
        state = type("State", (), {"on": True, "fell_back": False})()

        def raising(error):
            def forked_half():
                raise error
            return forked_half

        def fell_back():
            state.fell_back = True

        cases = ((raising(ValueError("pipe\nclosed")),
                  "MISMATCH on m.map: the forked game raised ValueError: pipe closed"),
                 (raising(OSError(35, "Resource temporarily unavailable")),
                  "check skipped on m.map: the forked half hit an OS error ([Errno 35]"),
                 (fell_back, "check skipped on m.map: a team's forked half started its processes as usual"))
        with tempfile.TemporaryDirectory() as folder:
            replay = Path(folder, "game.replay")
            for forked_half, verdict in cases:
                plays = []

                def execute(map_arg, bots, verbose, replay_path, no_replay, *rest):
                    plays.append(replay_path)
                    Path(replay_path).write_bytes(b"GAME")
                    if len(plays) == 2:
                        forked_half()
                    return 0

                said = io.StringIO()
                with self.subTest(verdict), contextlib.redirect_stderr(said):
                    code = fork_server.checked(execute, state)("m.map", ["a", "b"], False, str(replay), False)
                self.assertEqual((code, len(plays)), (0, 2))
                self.assertIn(f"fork server: {verdict}", said.getvalue())
                self.assertNotIn("identical", said.getvalue())
                self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["game.replay"])


class TestReferee(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="fork-server-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        run._fork_shim.cache_clear()
        self.addCleanup(run._fork_shim.cache_clear)
        run._fork_pairs.clear()
        self.checks = run.remote.loop_home() / run.FORK_CHECKS
        self.checks.unlink(missing_ok=True)
        self.a, self.b = (test_record.make_bot(self.root / name) for name in ("a", "b"))
        (self.b / "main.cpp").write_text((self.b / "main.cpp").read_text() + "\n// b\n")
        self.map = self.root / "m.map"
        self.map.write_bytes(b"a map")

    # Its note stays when a later run forks: runs overlap, and one's success says nothing of another's fallback.
    def test_a_run_without_the_shim_says_so_once_and_leaves_the_reason(self) -> None:
        note = run.remote.loop_home() / run.FORK_OFF
        said = io.StringIO()
        with patch.object(fork_server, "warm", return_value=(None, "no C compiler")) as warm, \
                contextlib.redirect_stderr(said):
            self.assertEqual([run.fork_plan(run.Job(self.map, self.a, self.b, "A"), False) for _ in range(3)],
                             [None] * 3)
        warm.assert_called_once()
        self.assertEqual(said.getvalue().count("fork server: off for this run (no C compiler)"), 1)
        self.assertEqual(json.loads(note.read_text())["reason"], "no C compiler")
        run._fork_shim.cache_clear()
        self.assertIsNotNone(run.fork_shim())
        self.assertEqual(json.loads(note.read_text())["reason"], "no C compiler")

    # A pair's first game is checked however its map hashes, so a fork difference in a new bot shows at once.
    def test_native_games_fork_and_a_pairs_first_and_one_in_fork_check_every_are_checked_but_never_one_that_records(
            self) -> None:
        maps = []
        for i in range(20 * run.FORK_CHECK_EVERY):
            maps.append(self.root / f"m{i}.map")
            maps[-1].write_bytes(f"map {i}".encode())
        run.fork_check(run.Job(maps[-1], self.a, self.b, "A"))  # the pair seen: a map neither side's share checks
        first = next(m for m in maps if not any(run.fork_check(job) for job in (
            run.Job(m, self.a, self.b, "B"), run.Job(m, self.b, self.a, "A"))))
        run._fork_pairs.clear()
        self.assertEqual(run.fork_plan(run.Job(first, self.a, self.b, "B"), False), (run.fork_shim(), True))
        self.assertEqual(run.fork_plan(run.Job(first, self.b, self.a, "A"), False), (run.fork_shim(), False))
        plans = [run.fork_plan(run.Job(m, self.a, self.b, "A"), False) for m in maps]
        self.assertEqual({plan[0] for plan in plans}, {run.fork_shim()})
        checked = sum(plan[1] for plan in plans)
        self.assertTrue(8 <= checked <= 35, checked)  # 20 expected; outside this range by luck under 1 in 1000
        self.assertEqual(plans, [run.fork_plan(run.Job(m, self.a, self.b, "A"), False) for m in maps])  # fixed
        recorded = [run.fork_plan(run.Job(m, self.a, self.b, "A", record=self.root / "r"), False) for m in maps]
        self.assertFalse(any(plan[1] for plan in recorded))
        self.assertIsNone(run.fork_plan(run.Job(maps[0], self.a, self.b, "A"), True))  # the sandbox never forks

    def test_a_mismatch_switches_forking_off_for_every_game_with_an_alert(self) -> None:
        job = run.Job(self.map, self.a, self.b, "B")
        self.assertIsNone(run.fork_notes(job, "fork server: check identical on m.map\nteam A wins", "here"))
        self.assertEqual(json.loads(self.checks.read_text())["checks"], 1)
        other = run.Job(self.map, self.a, self.a, "A")
        self.assertIsNotNone(run.fork_plan(other, False))
        line = "fork server: MISMATCH on m.map: the forked game's replay differs from the plain one's (forked exit 0)"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(run.fork_notes(job, f"\x1b[33m{line}\x1b[0m\nteam A wins", "ll"))
        data = json.loads(self.checks.read_text())
        self.assertEqual((data["checks"], data["identical"]), (2, 1))
        self.assertEqual(set(data["off"]), {run.bot_digest(self.a), run.bot_digest(self.b)})
        self.assertEqual(data["off"][run.bot_digest(self.b)]["said"], f"ll: {line}")
        self.assertIn(f"WARNING fork server: ll: {line}", run.remote.ALERTS.read_text())
        self.assertIn("sprt.BaselineCache", run.remote.ALERTS.read_text())
        self.assertIsNone(run.fork_plan(job, False))
        c = test_record.make_bot(self.root / "c")
        (c / "main.cpp").write_text((c / "main.cpp").read_text() + "\n// c\n")
        self.assertIsNone(run.fork_plan(run.Job(self.map, c, c, "A"), False))  # games of neither bot too
        self.checks.write_text("{not json")
        self.assertIsNone(run.fork_plan(other, False))  # an unreadable record forks nothing
        # ... and is never written over (it may hold the only record of a mismatch), a top-level list included.
        for broken in ("{not json", "[]", '{"off": []}'):
            self.checks.write_text(broken)
            with self.subTest(broken=broken), contextlib.redirect_stderr(io.StringIO()) as said:
                self.assertIsNone(run.fork_notes(job, "fork server: check identical on m.map", "here"))
            self.assertEqual(self.checks.read_text(), broken)
            self.assertIn("cannot count a check", said.getvalue())
        fallback = "fork server: off for bot (the shim didn't start in bot (it closed its socket))"
        self.assertEqual(run.fork_notes(job, f"{fallback}\nteam A wins", "here"), fallback)

    # MISSES 86: E0287's fork mismatches were blamed on the host, but its bot wrote out of bounds and played each game
    # differently. A check that differs first plays its plain game once more where it played: when that differs too,
    # both bots are recorded as nondeterministic and alerted with their digests and experiments, forking stays on, and
    # the fork server's own checks aren't counted; when it repeats, the mismatch switches forking off as before.
    def test_a_mismatch_whose_plain_game_differs_too_blames_the_bots_not_the_fork_server(self) -> None:
        job = run.Job(self.map, self.a, self.b, "B")
        first = run.RawGame("team A wins after 3 rounds (by elimination) (1.0s)", 0, b"R1", 1.0)
        line = "fork server: MISMATCH on m.map: the forked game's replay differs from the plain one's (forked exit 0)"
        differs = patch.object(run, "compare_games", side_effect=lambda job, a, b: {
            "problems": [] if a.replay == b.replay else ["replay differs: event 7 (move vs move) in round 63"]})
        with differs, contextlib.redirect_stderr(io.StringIO()):
            run.fork_notes(job, line, "mm", first, lambda: run.RawGame(first.stdout, 0, b"R2", 1.0))
            data = json.loads(self.checks.read_text())
            self.assertEqual((data.get("off"), data["checks"]), (None, 0))
            self.assertEqual(set(data["nondeterministic"]), {run.bot_digest(self.a), run.bot_digest(self.b)})
            self.assertIsNotNone(run.fork_plan(job, False))
            alert = run.remote.ALERTS.read_text()
            self.assertIn("doesn't repeat its own games", alert)
            self.assertIn(f"{run.bot_digest(self.a)} ({self.a}, {self.a.name})", alert)
            run.fork_notes(job, line, "mm", first, lambda: first)  # the plain game repeats: the fork server differs
        self.assertEqual(set(json.loads(self.checks.read_text())["off"]), {run.bot_digest(self.a), run.bot_digest(self.b)})
        self.assertIsNone(run.fork_plan(job, False))
        self.assertEqual(run.experiment_of(Path("/Users/x/.bc-loop/runs/E0287/cand")), "E0287")

    # A check game plays twice, so it gets twice the time, here and on a host; other games keep the usual limits.
    def test_a_check_game_gets_twice_the_timeouts(self) -> None:
        shim = run.fork_shim()
        waited, sent = [], []

        class Game:
            pid, returncode = 1, 0

            def __init__(self, cmd, **_):
                pass

            def communicate(self, timeout=None):
                waited.append(timeout)
                return "", ""

        host = run.remote.Host("mm", 4)
        scheduler = run.Scheduler(1, False, {})
        game = run.remote.RemoteGame("team A wins after 152 rounds (by elimination)\n", 0, b"R")
        for plan in ((shim, True), (shim, False), None):
            with self.subTest(plan=plan), patch.object(run, "fork_plan", return_value=plan), \
                    patch.object(run, "seeded_toolkit", return_value=True), \
                    patch.object(run.subprocess, "Popen", Game), \
                    patch.object(host, "play", side_effect=lambda *a, **k: sent.append(a[2]) or game), \
                    patch.object(scheduler, "remote_bot", side_effect=lambda h, bot: bot):
                job = run.Job(self.map, self.a, self.b, "A")
                run.run_game(job, False, self.root / "r.replay")
                scheduler.play_remote(host, job, self.root / "r.replay")
            double = 2 if plan and plan[1] else 1
            self.assertEqual((waited[-1], sent[-1]), (run.GAME_TIMEOUT_S * double, run.REMOTE_TIMEOUT_S * double))

    def test_the_game_command_chains_the_launchers(self) -> None:
        commands = []

        class Started(Exception):
            pass

        def popen(cmd, **_):
            commands.append(cmd)
            raise Started

        shim = run.fork_shim()
        for plan, sandbox in (((shim, True), False), ((shim, False), False), (None, False), (None, True)):
            # The seed check is answered here: under the patched Popen it would ask `unswbc run --help` of it.
            with self.subTest(plan=plan, sandbox=sandbox), patch.object(run, "fork_plan", return_value=plan), \
                    patch.object(run, "seeded_toolkit", return_value=True), \
                    patch.object(run.subprocess, "Popen", popen), self.assertRaises(Started):
                run.run_game(run.Job(self.map, self.a, self.b, "A"), sandbox, self.root / "r.replay")
            cmd = commands[-1]
            launcher = run.engine_launcher()
            if plan:
                expected = [sys.executable, str(run.FORK_LAUNCHER), "--shim", str(shim),
                            *(["--check"] if plan[1] else [])]
                self.assertEqual(cmd[:len(expected)], expected)
                self.assertNotEqual(cmd[len(expected)], "--check")
                self.assertEqual(cmd[cmd.index(str(run.ENGINE_LAUNCHER)):][:3], [*launcher[1:], "run"])
            else:
                self.assertNotIn(str(run.FORK_LAUNCHER), cmd)
            self.assertEqual("--sandbox" in cmd, sandbox)


class TestGames(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="fork-server-"))
        cls.bot = test_record.make_bot(cls.root / "bot")
        cls.shim, _ = fork_server.warm(cls.root / "fork-server")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["rm", "-rf", str(cls.root)])

    def play(self, prefix: list[str], name: str) -> tuple[subprocess.CompletedProcess, bytes]:
        replay = self.root / f"{name}.replay"
        done = subprocess.run([*prefix, "run", str(test_record.MAP), str(self.bot), str(self.bot), "-o", str(replay),
                               "--seed", str(test_record.SEED)], capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done, replay.read_bytes()

    # The same game, byte for byte and line for line, as `unswbc run`: forked, forked through the engine cache, and a
    # check game, which says it compared.
    def test_a_forked_game_is_the_toolkits_game(self) -> None:
        forked = [sys.executable, str(HERE / "fork_server.py"), "--shim", str(self.shim)]
        plain, expected = self.play([UNSWBC], "plain")
        result = [line for line in plain.stdout.splitlines() if " after " in line][0].rsplit(" (", 1)[0]
        cache = [str(HERE / "engine_cache.py"), str(self.root / "cache")]
        for name, prefix in (("forked", forked), ("cached", [*forked, *cache]), ("check", [*forked, "--check"]),
                             ("check cached", [*forked, "--check", *cache])):
            with self.subTest(name):
                done, replay = self.play(prefix, name)
                self.assertEqual(replay, expected)
                self.assertIn(result, done.stdout)
                said = "fork server: check identical on arena.map\n" if name.startswith("check") else ""
                self.assertEqual(done.stderr, said)
                self.assertEqual(done.stdout.count(" after "), 1)  # a check's second game prints nothing

    # A refused fork while the forked half's Pools start (their first spares) comes out of the toolkit's own execute as
    # "error: [Errno 35] ..." and exit 1, not as an exception: that is the machine, so the check is skipped, forking
    # stays on, and the plain game (exit 0, its replay) stands. The reviewer's reproduction, through the real toolkit.
    def test_a_fork_refused_as_the_forked_half_starts_skips_the_check(self) -> None:
        restore_toolkit(self)
        self.assertTrue(fork_server.install(self.shim, check=True))
        replay = self.root / "refused.replay"
        refused = OSError(errno.EAGAIN, os.strerror(errno.EAGAIN))
        said = io.StringIO()
        with patch.object(fork_server.Server, "spawn", side_effect=refused) as spawn, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(said):
            code = unswbc.run.execute(str(test_record.MAP), [str(self.bot), str(self.bot)], False, str(replay), False,
                                      False, unswbc.run.DEBUG_ALL, test_record.SEED)
        spawn.assert_called()
        self.assertEqual(code, 0)
        self.assertTrue(replay.is_file())
        self.assertIn(f"fork server: check skipped on arena.map: the forked half hit an OS error (error: [Errno "
                      f"{errno.EAGAIN}]", said.getvalue())
        self.assertNotIn("MISMATCH", said.getvalue())

    # The referee's own games, recorded (record.py through both launchers) or not, match the unforked ones.
    def test_the_referees_games_forked_match_its_unforked_ones(self) -> None:
        for record in (None, "rec"):
            with self.subTest(record=record):
                games = {}
                for name in ("off", "on"):
                    job = run.Job(test_record.MAP, self.bot, self.bot, "B",
                                  record=self.root / f"{record}-{name}" if record else None)
                    plan = (self.shim, False) if name == "on" else None
                    with patch.object(run, "fork_plan", return_value=plan):
                        game = run.run_game(job, False, self.root / f"{record}-{name}.replay")
                    self.assertEqual(game.returncode, 0, game.stdout)
                    self.assertNotIn("fork server", game.stdout)
                    games[name] = (game.replay, record and record_module.Recording.load(job.record))
                self.assertEqual(games["on"][0], games["off"][0])
                if record:
                    self.assertTrue(record_module.same(games["on"][1], games["off"][1]))


if __name__ == "__main__":
    unittest.main()
