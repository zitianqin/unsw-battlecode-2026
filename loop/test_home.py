"""Live paths come only from loop_home, and every test module moves them first. Tests have written the live
~/.bc-loop/runs/test and host-alerts.log and deleted the live evaluation marker, each time through a path the
tests didn't know to patch."""

import testhome  # before anything that reads live paths
import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import home

REPO = Path(__file__).resolve().parent.parent
SOURCES = ("loop", "referee", "tools", "mapgen", "scripts")
LIVE = ("bc-loop",)  # ~/.bc-loop and every /private/tmp/bc-loop-* file
LIVE_HOME = "." + LIVE[0]  # the live home's folder name under ~
# The only definitions that may spell a live path out.
ALLOWED = {("loop/home.py", "loop_home"), ("loop/home.py", "custom_home"), ("loop/home.py", "evaluating_marker"), ("loop/home.py", "load_fraction_copy"),
           ("loop/home.py", "split_copy"), ("referee/remote.py", "loop_home"),
           ("tools/usage_statusline.py", "loop_home")}


# String literals in code naming a live path, as "file:line", outside ALLOWED. Docstrings may mention them.
def live_literals(path: Path) -> list[str]:
    name = str(path.relative_to(REPO))
    tree = ast.parse(path.read_text())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (name, node.name) in ALLOWED:
            skip.update(id(child) for child in ast.walk(node))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skip.add(id(node.value))  # a docstring or bare string
    return [f"{name}:{node.lineno}" for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
            and any(live in node.value for live in LIVE)]


class TestLiveHome(unittest.TestCase):
    def test_only_loop_home_names_a_live_path(self) -> None:
        found = [hit for folder in SOURCES for path in sorted((REPO / folder).rglob("*.py"))
                 if not path.name.startswith("test_") for hit in live_literals(path)]
        self.assertEqual(found, [], "derive live paths from loop_home()/evaluating_marker(), not a literal")

    def test_every_loop_and_referee_test_module_imports_testhome_first(self) -> None:
        for path in sorted([*(REPO / "loop").glob("test_*.py"), *(REPO / "referee").glob("test_*.py")]):
            with self.subTest(path=path.name):
                first = next(node for node in ast.parse(path.read_text()).body
                             if isinstance(node, (ast.Import, ast.ImportFrom)))
                self.assertEqual([alias.name for alias in first.names], ["testhome"])

    # 29 Sep 16:06: a review harness imported meta before testhome and wrote 39 fake alerts into the live home; tools/
    # test_fit, test_why and test_discord_read loaded meta with no testhome at all. testhome now refuses to come
    # second, and a tools/ or mapgen/ test module that loads home (alone, in a fresh process) must import it.
    def test_testhome_refuses_to_follow_meta_and_every_test_module_loading_home_imports_it(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "BC_LOOP_HOME"}
        late = subprocess.run([sys.executable, "-c", "import meta, testhome"], cwd=REPO / "loop", env=env,
                              capture_output=True, text=True)
        self.assertIn("ImportError: testhome imported after home, meta", late.stderr)
        check = "import importlib, sys; importlib.import_module(sys.argv[1]); print('home' in sys.modules, " \
                "'testhome' in sys.modules)"
        for path in sorted([*(REPO / "tools").glob("test_*.py"), *(REPO / "mapgen").glob("test_*.py")]):
            with self.subTest(path=path.name):
                seen = subprocess.run([sys.executable, "-c", check, f"{path.parent.name}.{path.stem}"], cwd=REPO,
                                      env={**env, "PYTHONPATH": str(REPO / "loop")}, capture_output=True, text=True)
                self.assertIn(seen.stdout.split(), (["False", "False"], ["False", "True"], ["True", "True"]),
                              seen.stderr[-300:])

    def test_no_test_clears_the_environment_without_keeping_the_temporary_home(self) -> None:
        # A module first imported inside patch.dict(os.environ, {}, clear=True) would compute live paths. Clearing
        # another mapping (sys.modules, to drop one module) keeps the environment.
        for path in sorted([*(REPO / "loop").glob("test_*.py"), *(REPO / "referee").glob("test_*.py")]):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dict"
                        and any(k.arg == "clear" for k in node.keywords) and node.args
                        and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "environ"):
                    keys = [k.value for k in getattr(node.args[1], "keys", []) if isinstance(k, ast.Constant)]
                    self.assertIn("BC_LOOP_HOME", keys, f"{path.name}:{node.lineno}")

    # health.fresh's child ran with $BC_LOOP_HOME set to the live home, which moved the evaluation marker into it: it
    # saw no evaluation, and called E0414, E0426 and E0429 lock-less and the queue stalled while each was evaluated
    # (MISSES 108). Only a home other than the live one moves the marker.
    def test_the_live_home_given_as_the_setting_keeps_the_live_marker(self) -> None:
        with patch.dict(os.environ, {"BC_LOOP_HOME": str(Path.home() / "Dev" / ".." / LIVE_HOME)}):
            self.assertFalse(home.custom_home())
            self.assertNotEqual(home.evaluating_marker().parent, home.loop_home())
            self.assertEqual(home.clones_folder(REPO), REPO.parent / "bc-wt")
        self.assertTrue(home.custom_home())  # this test's temporary home

    def test_tests_run_in_a_temporary_home(self) -> None:
        self.assertEqual(home.loop_home(), Path(os.environ["BC_LOOP_HOME"]).resolve())
        self.assertNotEqual(home.loop_home(), Path.home() / ".bc-loop")
        self.assertEqual(home.evaluating_marker().parent, home.loop_home())
        self.assertEqual(home.clones_folder(REPO).parent, home.loop_home())


# The loop's games here run at home.GAME_NICE (native.lower_games), gated by the cores dev work keeps busy at normal
# priority, not by the load average they themselves fill (sig, 2026-09-27: dev work on this Mac comes first).
class TestCpuSplit(unittest.TestCase):
    def setUp(self) -> None:
        home.split_copy().unlink(missing_ok=True)
        home.sampler_lock().unlink(missing_ok=True)

    def test_niced_games_fill_what_dev_work_leaves_and_start_none_once_it_reaches_the_share(self) -> None:
        self.assertEqual(home.niced_slots(0.8, 10, dev=2.0, niced=3.0, active=3, memory_gb=8), 3 + home.NICE_STEP)
        self.assertEqual(home.niced_slots(0.8, 10, dev=2.0, niced=6.5, active=3, memory_gb=8), 5)  # 1.5 idle cores
        self.assertEqual(home.niced_slots(0.8, 10, dev=2.0, niced=8.0, active=3, memory_gb=8), 3)  # full: no more
        self.assertEqual(home.niced_slots(0.8, 10, dev=7.9, niced=0.0, active=0, memory_gb=8), 2)
        self.assertEqual(home.niced_slots(0.8, 10, dev=8.0, niced=0.0, active=0, memory_gb=8), 0)  # dev at the share
        self.assertEqual(home.niced_slots(0.8, 10, dev=0.0, niced=0.0, active=0, memory_gb=home.MIN_FREE_GB - 0.1), 0)
        self.assertEqual(home.niced_slots(0.8, 10, dev=0.0, niced=0.0, active=3, memory_gb=8, cap=-2), 1)  # over a cap
        self.assertTrue(home.sessions_full(7.0, 0.8, 10))
        self.assertFalse(home.sessions_full(6.9, 0.8, 10))
        self.assertTrue(home.sessions_full(4.0, 0.4, 10))  # sig's lower share, where he sets one

    def test_the_split_is_the_busy_share_less_what_the_niced_processes_gained(self) -> None:
        # pid 3 started meanwhile: all its time is new; pid 2 is a player's beside an evaluation, at YIELD_NICE.
        before = (100.0, 1000, 4000, {1: (19, 10.0), 2: (20, 5.0)})
        after = (102.0, 1000 + 1500, 4000 + 2000, {1: (19, 12.0), 2: (20, 6.0), 3: (19, 0.5)})
        dev, niced, yielding = home.measure(before, after, 10)  # 75% of 10 cores busy between the readings
        self.assertAlmostEqual(niced, 3.5 / 2)  # 3.5 CPU seconds over 2 s
        self.assertAlmostEqual(yielding, 1.0 / 2)
        self.assertAlmostEqual(dev, 7.5 - 3.5 / 2)
        self.assertIsNone(home.measure(after, after, 10))

    def test_one_process_samples_the_split_for_every_reader(self) -> None:
        # Every scheduler process ran `ps -A` every 2 s; now the one holding the sampler lock does, and publishes.
        samples = iter([(100.0, 1000, 4000, {1: (19, 10.0)}), (102.0, 2500, 6000, {1: (19, 12.0)})])
        sampler = home.CpuSplit()
        with patch.object(sampler, "sample", side_effect=lambda: next(samples)), \
                patch.object(home.threading, "Thread") as thread, patch.object(home.os, "cpu_count", return_value=10):
            self.assertIsNone(sampler.read())  # nothing published yet: the caller keeps the load average
            thread.assert_called_once()
            self.assertTrue(sampler.sampling)
            self.assertTrue(sampler.step())  # what the thread does every SPLIT_S
            self.assertEqual(home.published_split(), (6.5, 1.0, 0.0))
            other = home.CpuSplit()  # a second process: the lock is held, so it only reads
            with patch.object(other, "sample") as sample:
                self.assertEqual(other.read(), (6.5, 1.0, 0.0))
                with patch.object(home.time, "time", return_value=time.time() + home.LAPSE_S + 1):
                    other.read()  # the copy is old: it offers, but the first sampler still holds the lock
                sample.assert_not_called()
                self.assertFalse(other.sampling)
        os.close(thread.call_args.kwargs["args"][0])  # the sampler exits, its lock with it
        # A process that can't run ps (a coder's sandbox) lets the lock go, and reads the copy while it is fresh.
        with patch.object(home, "niced_times", return_value=None):
            reader = home.CpuSplit()
            reader.offer()
            self.assertFalse(reader.sampling)
            fd = os.open(home.sampler_lock(), os.O_RDWR)
            home.fcntl.flock(fd, home.fcntl.LOCK_EX | home.fcntl.LOCK_NB)  # free for the next process
            os.close(fd)
            with patch.object(home.time, "time", return_value=time.time() + home.SPLIT_COPY_S + 1):
                self.assertIsNone(reader.read())

    def test_a_process_running_older_code_than_the_file_on_disk_never_samples(self) -> None:
        # 28 Sep 19:07: a driver started 15:58 held the sampler with home.py from before 9ba0fd0 (merged 17:04),
        # publishing coders' nice-10 CPU as dev work (9.3 of 10 cores, 3.7 measured): the CPU gate here waited.
        samples = iter([(100.0, 1000, 4000, {}), (102.0, 2500, 6000, {})])
        sampler = home.CpuSplit()
        with patch.object(sampler, "sample", side_effect=lambda: next(samples)), \
                patch.object(home.threading, "Thread") as thread, patch.object(home.os, "cpu_count", return_value=10):
            sampler.read()
            thread.assert_called_once()
            with patch.dict(home._IMPORTED, {home.__file__: "an older home.py"}):  # rewritten since its import
                self.assertFalse(sampler.step())  # it steps down: the thread's loop ends and frees the lock
                os.close(thread.call_args.kwargs["args"][0])
                other = home.CpuSplit()
                with patch.object(other, "sample") as sample:
                    self.assertIsNone(other.read())
                sample.assert_not_called()  # an old process never takes the free lock either
                self.assertFalse(other.sampling)
        newer = home.CpuSplit()  # a process running the file on disk takes it
        with patch.object(newer, "sample", return_value=(104.0, 3000, 7000, {})), \
                patch.object(home.threading, "Thread") as thread:
            newer.read()
            self.assertTrue(newer.sampling)
        os.close(thread.call_args.kwargs["args"][0])

    def test_loop_code_is_current_by_content_across_every_module_this_process_loaded(self) -> None:
        # By content, not mtime: a checkout that rewrites a file with the same text (a branch switch and back) leaves
        # every process current. The upkeep asks about every loop module it loaded (maintenance.ticking).
        with tempfile.TemporaryDirectory() as directory, patch.object(home, "LOOP_DIR", Path(directory).resolve()), \
                patch.dict(home._IMPORTED), patch.dict(sys.modules):
            source = Path(directory) / "upkept.py"
            source.write_text("VALUE = 1\n")
            spec = importlib.util.spec_from_file_location("upkept", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules["upkept"] = module
            self.assertTrue(home.current_code(everything=True))  # stamped at its first check
            self.assertIn(str(source), home._IMPORTED)
            os.utime(source, (1, 1))
            source.write_text("VALUE = 1\n")  # the same text written again
            self.assertTrue(home.current_code(everything=True))
            source.write_text("VALUE = 2\n")
            self.assertFalse(home.current_code(everything=True))
            self.assertTrue(home.current_code())  # home.py alone is unchanged: the sampler keeps sampling
            source.write_text("VALUE = 1\n")  # checked out back
            self.assertTrue(home.current_code(everything=True))

    def test_a_loop_process_imports_its_starts_code_after_a_merge_rewrites_it(self) -> None:
        # 29 Sep 08:01 (MISSES 88): E0306's --once, started 06:56, imported native.py lazily after the 07:52 merge; the
        # new native.py imports home.MAC_HOSTS, which the process's home.py from 06:56 lacked: ImportError, and the
        # coded candidate's experiment ended in error. Here a "merge" rewrites home.py and a module not yet imported,
        # and deletes another; a traceback through the rewritten one shows the lines that run.
        with tempfile.TemporaryDirectory() as directory:
            loop = Path(directory) / "loop"
            loop.mkdir()
            (loop / "home.py").write_text(Path(home.__file__).read_text())
            (loop / "later.py").write_text("from home import loop_home\nVERSION = 'old'\n")
            (loop / "gone.py").write_text("VERSION = 'gone'\n")
            (loop / "lane.py").write_text(
                "import home, linecache\nfrom pathlib import Path\n"
                "here = Path(__file__).parent\n"
                "(here / 'home.py').write_text((here / 'home.py').read_text() + '\\nNEW_NAME = 1\\n')\n"
                "(here / 'later.py').write_text('from home import NEW_NAME\\nVERSION = \"new\"\\n')\n"
                "(here / 'gone.py').unlink()\n"
                "import later, gone\n"
                "linecache.checkcache()\n"
                "print(later.VERSION, gone.VERSION, linecache.getline(later.__file__, 2).strip(), "
                "home.current_code(everything=True), sep='|')\n")
            run = subprocess.run([sys.executable, str(loop / "lane.py")], capture_output=True, text=True,
                                 env={**os.environ, "BC_LOOP_HOME": directory})
            # its start's code, known stale
            self.assertEqual((run.stdout.strip(), run.stderr), ("old|gone|VERSION = 'old'|False", ""))

    def test_the_samplers_lock_is_in_the_loop_home_and_only_the_copy_is_where_coders_read(self) -> None:
        # In /private/tmp any reader, a coder's session too, could flock(LOCK_EX) the lock it can only read and so
        # stop every sample; coders can't reach the loop home.
        with patch.dict(os.environ), patch.object(home.Path, "home", return_value=Path("/Users/x")):
            del os.environ["BC_LOOP_HOME"]  # the live layout, with its home moved to a folder that isn't there
            self.assertEqual(home.sampler_lock(), Path("/Users/x/.bc-loop/cpu-split.lock").resolve())
            self.assertEqual(home.split_copy(), Path("/private/tmp/bc-loop-cpu-split.json"))

    def test_a_published_split_is_refused_when_dated_ahead_or_out_of_range(self) -> None:
        # MISSES 17: a record anyone could plant with a future "at" never went stale, and one past this Mac's cores
        # opened more slots than it has.
        cores = os.cpu_count() or 1
        now = time.time()
        for record, fresh in (({"at": now, "dev": 1.0, "niced": 2.0, "yielding": 1.0}, True),
                              ({"at": now, "dev": 1.0, "niced": 2.0}, True),  # written by code before "yielding"
                              ({"at": now + 3600, "dev": 1.0, "niced": 2.0}, False),
                              ({"at": now, "dev": -1.0, "niced": 2.0}, False),
                              ({"at": now, "dev": 1.0, "niced": cores + 1.0}, False),
                              ({"at": now, "dev": 1.0, "niced": 2.0, "yielding": 3.0}, False),
                              ({"at": "nan", "dev": 1.0, "niced": 2.0}, False), ([], False)):
            with self.subTest(record=record):
                home.split_copy().write_text(json.dumps(record))
                self.assertEqual(home.published_split() is not None, fresh)

    def test_publishing_never_writes_through_a_link_planted_at_the_copy(self) -> None:
        # A predictable temporary name and write_text followed a symlink: a trusted process would have overwritten
        # its target (e.g. ~/.bc-loop/results.jsonl).
        victim = home.split_copy().with_name("victim")
        victim.write_text("keep")
        self.addCleanup(victim.unlink, missing_ok=True)
        home.split_copy().symlink_to(victim)
        for pid in (os.getpid(), os.getpid() + 1):  # the old temporary names, planted too
            planted = home.split_copy().with_name(f".{home.split_copy().name}.{pid}")
            planted.symlink_to(victim)
            self.addCleanup(planted.unlink, missing_ok=True)
        home.publish_split(home.Split(1.0, 2.0, 0.5))
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse(home.split_copy().is_symlink())
        self.assertEqual(home.published_split(), (1.0, 2.0, 0.5))

    def test_the_hosts_port_is_taken_once_a_process(self) -> None:
        # mach_host_self adds a reference to the port on each call; every 2 s in every process that leaked.
        if home.cpu_ticks() is None:
            self.skipTest("no HOST_CPU_LOAD_INFO here")
        port = home._mach[1]
        home.cpu_ticks()
        self.assertEqual(home._mach[1], port)

    def test_ps_cpu_times_are_read_in_every_format_and_only_at_a_workers_niceness_or_lower(self) -> None:
        # A headless worker's commands (home.WORKER_NICE, meta.private_tmp) count with the games, not as dev work
        # (28 Sep 15:50: coders' analysis scripts at nice 0 held every Mac-gated game back for hours).
        out = ("  1   0  39:52.42\n 77  19   0:01.50\n 78  19 1:02:03.25\n 79  20 2-01:00:00.00\n 80   5   9:00.00\n"
               f" 81  {home.WORKER_NICE}   0:02.00\n 82  {home.WORKER_NICE - 1}   0:03.00\n")
        with patch.object(home.subprocess, "run", return_value=home.subprocess.CompletedProcess([], 0, out, "")):
            self.assertEqual(home.niced_times(), {77: (19, 1.5), 78: (19, 3723.25), 79: (20, 49 * 3600.0),
                                                  81: (home.WORKER_NICE, 2.0)})


# 29 Sep 13:01: this Mac reached a load of 51 on 10 cores from coders' and workers' analysis pools, each sized by the
# cores. A pool now takes what the load share leaves above the work at its priority or higher, the games left out.
class TestPools(unittest.TestCase):
    def test_a_workers_pool_counts_the_other_workers_but_not_the_games(self) -> None:
        # Over 2 s of 10 cores, 8 busy: pid 81, a worker's script at WORKER_NICE, gained 4 s (2 cores), pid 77, a game
        # at GAME_NICE, 6 s (3 cores); the rest (3 cores) is dev work.
        before = (0.0, 0, 0, {81: (home.WORKER_NICE, 10.0), 77: (home.GAME_NICE, 1.0)})
        after = (2.0, 8, 10, {81: (home.WORKER_NICE, 14.0), 77: (home.GAME_NICE, 7.0)})
        for nice, load in ((home.WORKER_NICE, 5.0), (0, 3.0)):
            with self.subTest(nice=nice), patch.object(home.CPU, "sample", side_effect=[before, after]), \
                    patch.object(home.os, "getpriority", return_value=nice), \
                    patch.object(home.os, "cpu_count", return_value=10), patch.object(home, "published_split", return_value=None), \
                    patch.object(home.time, "sleep"):
                self.assertAlmostEqual(home.load_at_or_above(), load)

    def test_a_pool_waits_while_the_share_is_reached_unless_told_not_to(self) -> None:
        with patch.object(home, "share_now", return_value=0.9), patch.object(home.os, "cpu_count", return_value=10), \
                patch.object(home.time, "sleep") as slept, patch("sys.stderr"):
            with patch.object(home, "load_at_or_above", side_effect=[9.5, 9.0, 3.0]):
                self.assertEqual(home.pool_size(8), 6)  # 9 of 10 cores less 3 busy, after two waits
            self.assertEqual(slept.call_count, 2)
            with patch.object(home, "load_at_or_above", side_effect=[None]), \
                    patch.object(home.os, "getloadavg", return_value=(51.0, 40.0, 20.0)):
                self.assertEqual(home.pool_size(8, wait=False), 1)  # unmeasurable: the load average, and no wait
            with patch.object(home, "load_at_or_above", return_value=0.0):
                self.assertEqual(home.pool_size(4), 4)
            # A share under a core (load-fraction 0.1 on 10 cores) still gets one worker while under it.
            with patch.object(home, "share_now", return_value=0.1), \
                    patch.object(home, "load_at_or_above", side_effect=[0.5, AssertionError("waited")]):
                self.assertEqual(home.pool_size(4), 1)


# A process table (home.process_table) of `others` processes outside any game, and one game per entry of `games`, of that
# many processes, as a live one reads (28 Sep 06:10, pgid 5333): its leader the toolkit's python (pid == pgid, a child
# of the scheduler that started it, `parent`), the rest its bots, all named home.BOT_NAME: each team's fork server,
# a child of the leader, and the dragons it forked, its children. Plus 63 ssh processes under one zsh, as that
# table's largest non-game group, and one process whose details weren't readable.
def table(others: int, games: list[int], parent: int = 1, first: int = 20000) -> list[home.Process]:
    rows = [home.Process(9000, 1, 9000, "zsh"), *(home.Process(9001 + i, 9000, 9000, "ssh") for i in range(62)),
            home.Process(8999, 0, 0, "")]
    rows += [home.Process(100 + i, 1, 100 + i, "Google Chrome H") for i in range(others - len(rows))]
    for number, size in enumerate(games):
        leader = first + 1000 * number
        rows.append(home.Process(leader, parent, leader, "python"))
        servers = [leader + 1, leader + 2][:size - 1]
        rows += [home.Process(server, leader, leader, "bot") for server in servers]
        rows += [home.Process(leader + 3 + i, servers[i % 2], leader, "bot") for i in range(size - 1 - len(servers))]
    return rows


# 28 Sep 01:17-01:41: the late screen's and the variant sweep's launches, a remote dispatch and a commit hook failed with
# "[Errno 35] Resource temporarily unavailable": this user's processes reached kern.maxprocperuid, mostly games' dragons.
class TestProcessLimit(unittest.TestCase):
    def setUp(self) -> None:
        home.split_copy().unlink(missing_ok=True)
        home.spells_file().unlink(missing_ok=True)

    def test_games_wait_within_the_reserve_and_a_pass_starts_only_as_many_as_fit(self) -> None:
        limit, game = 2666, home.GAME_PROCESSES
        edge = limit - home.PROCESS_RESERVE - game  # the most processes at which one more game may start
        self.assertEqual(home.game_room(0, table(edge, []), limit), 1)
        self.assertEqual(home.game_room(0, table(edge + 1, []), limit), 0)
        self.assertEqual(home.game_room(0, table(edge - 2 * game, []), limit), 3)
        self.assertEqual(home.game_room(0, table(limit + 50, []), limit), 0)
        with patch.object(home, "process_table", return_value=None):
            self.assertIsNone(home.game_room(limit=limit))  # unknown: no gate (it fails open)
        with patch.object(home, "process_limit", return_value=None):
            self.assertIsNone(home.game_room(0, table(100, [])))

    def test_every_live_game_is_charged_at_its_peak_not_its_size_now(self) -> None:
        # 1,200 others and 10 young games of 10 processes: counted as they are, 6 more fit, and 16 games grow to some
        # 2,400 processes beside the 1,200; charged at home.GAME_PROCESSES, none fit.
        young = table(1200, [10] * 10)
        self.assertEqual(len(young), 1300)
        self.assertEqual(home.game_room(0, young, 2666), 0)
        self.assertEqual(home.game_room(0, table(600, [10] * 5), 2666), (2666 - 450 - 600) // 150 - 5)
        grown = table(600, [home.GAME_PROCESSES + 60])  # past the peak: charged at its size
        self.assertEqual(home.game_room(0, grown, 2666), (2666 - 450 - 600 - 210) // 150)

    def test_a_callers_own_games_not_yet_recognised_are_charged_too(self) -> None:
        # One of this process's 3 games has bots; the other two have just started (no bot yet). Another process's
        # game doesn't count as ours.
        mine = table(600, [5], parent=os.getpid()) + table(0, [5], parent=1, first=50000)[64:]
        self.assertEqual(home.game_room(0, mine, 2666), (2666 - 450 - 600) // 150 - 2)
        self.assertEqual(home.game_room(3, mine, 2666), (2666 - 450 - 600) // 150 - 2 - 2)
        self.assertEqual(home.game_room(1, mine, 2666), (2666 - 450 - 600) // 150 - 2)  # its one game is seen

    def test_a_game_is_recognised_in_this_users_real_process_table_without_a_fork(self) -> None:
        # A child of this process in a new session running an executable named home.BOT_NAME is a live game of ours;
        # the same executable under another name is not.
        import shutil
        import subprocess
        import tempfile
        if not shutil.which("cc"):
            self.skipTest("no cc")
        folder = Path(tempfile.mkdtemp(prefix="test-home-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (folder / "s.c").write_text("#include <unistd.h>\nint main(void) { sleep(30); return 0; }\n")
        subprocess.run(["cc", "-o", str(folder / home.BOT_NAME), str(folder / "s.c")], check=True)
        shutil.copy(folder / home.BOT_NAME, folder / "other")
        children = [subprocess.Popen([str(folder / home.BOT_NAME)], start_new_session=True),
                    subprocess.Popen([str(folder / "other")], start_new_session=True)]
        for child in children:
            self.addCleanup(child.wait)
            self.addCleanup(child.kill)
        deadline = time.monotonic() + 5
        while True:
            with patch.object(home.subprocess, "run", side_effect=AssertionError("forked")):
                rows = home.process_table()
            seen = {row.pid: row for row in rows if row.pid in {child.pid for child in children}}
            if len(seen) == 2 and seen[children[0].pid].name == home.BOT_NAME or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual(seen[children[0].pid], home.Process(children[0].pid, os.getpid(), children[0].pid, "bot"))
        self.assertEqual(seen[children[1].pid].name, "other")
        # the game is charged at home.GAME_PROCESSES in place of its one process, and it is ours: `active` 1 adds nothing
        games = {row.pgid for row in rows if row.name == home.BOT_NAME}  # a loop game may be running here too
        held = len(rows) + sum(max(0, home.GAME_PROCESSES - sum(row.pgid == game for row in rows)) for game in games)
        self.assertEqual(home.game_room(1, rows, 10_000), (10_000 - home.PROCESS_RESERVE - held) // home.GAME_PROCESSES)
        with patch.object(home, "process_table", return_value=rows):  # two live reads differ as processes come and go
            self.assertEqual(home.user_processes(), len(rows))

    def test_a_sandbox_refusing_the_limits_sysctl_reads_the_samplers_copy(self) -> None:
        with patch.object(home, "_process_limit", None), patch.object(home, "sysctl_limit", return_value=None):
            self.assertIsNone(home.process_limit())  # nothing published yet
        with patch.object(home, "_process_limit", None), patch.object(home, "sysctl_limit", return_value=2666):
            home.publish_split(home.Split(1.0, 0.0, 0.0))  # the trusted sampler publishes the limit it reads
        with patch.object(home, "_process_limit", None), patch.object(home, "sysctl_limit", return_value=None):
            self.assertEqual(home.process_limit(), 2666)

    @staticmethod
    def spells() -> list:
        path = home.spells_file()
        return json.loads(path.read_text())["spells"] if path.exists() else []

    def test_a_spell_without_room_is_saved_once_it_lasts_and_again_after_room_returns(self) -> None:
        full, room = table(2200, []), table(100, [])
        watch, start = home.ProcessWatch(), 1000.0
        watch.check(start, full, 2666)
        watch.check(start + home.FULL_ALERT_S - 1, full, 2666)
        self.assertEqual(self.spells(), [])  # a burst isn't a spell
        watch.check(start + home.FULL_ALERT_S, full, 2666)
        watch.check(start + home.FULL_ALERT_S + 60, full, 2666)
        self.assertEqual([(s["since"], s["count"], s["groups"][:2]) for s in self.spells()],
                         [(start, 2200, ["63 zsh (pgid 9000)", "1 Google Chrome H (pgid 100)"])])
        watch.check(start + 400, room, 2666)  # room again
        watch.check(start + 600, full, 2666)
        watch.check(start + 600 + home.FULL_ALERT_S, full, 2666)
        self.assertEqual(len(self.spells()), 2)

    def test_a_long_spell_keeps_its_row_current_so_health_alerts_all_along(self) -> None:
        # One save at 5 min and no more left a 6-hour spell's row 5 h 55 min old, past health.REPEAT: it went silent
        # while every game here waited.
        full, watch, start = table(2200, []), home.ProcessWatch(), 1000.0
        for minute in range(0, 6 * 60 + 1):
            watch.check(start + 60 * minute, full, 2666)
        [spell] = self.spells()  # one spell, one row, refreshed in place
        self.assertEqual((spell["since"], spell["at"]), (start, start + 6 * 3600))

    def test_a_spell_of_an_unreadable_count_or_limit_is_saved_after_its_own_wait(self) -> None:
        # The gate fails open while nothing is measured, so a gate that stopped measuring is saved too, once it lasts.
        watch, start = home.ProcessWatch(), 1000.0
        watch.check(start, None, 2666)
        watch.check(start + home.UNREAD_ALERT_S - 1, table(100, []), None)
        self.assertEqual(self.spells(), [])
        watch.check(start + home.UNREAD_ALERT_S, None, None)
        [spell] = self.spells()
        self.assertEqual((spell["since"], spell["unread"], spell["count"], spell["limit"]), (start, True, None, None))
        watch.check(start + home.UNREAD_ALERT_S + 60, table(2200, []), 2666)  # full now: a spell of its own
        watch.check(start + home.UNREAD_ALERT_S + 60 + home.FULL_ALERT_S, table(2200, []), 2666)
        self.assertEqual([s.get("unread") for s in self.spells()], [True, None])

    def test_group_names_that_ps_could_not_decode_never_end_the_sampler(self) -> None:
        # ps's comm is argv[0], any bytes: text=True raised UnicodeDecodeError out of top_groups, ProcessWatch and
        # CpuSplit.step, ending the sampler thread. The groups come from the process table now, names decoded with
        # errors replaced.
        full, watch = table(2200, []) + [home.Process(1, 1, 1, "bot\ufffd")], home.ProcessWatch()
        with patch.object(home.subprocess, "run", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")):
            watch.check(0.0, full, 2666)
            watch.check(float(home.FULL_ALERT_S), full, 2666)
        self.assertEqual(len(self.spells()), 1)

    def test_a_failed_spell_write_leaves_no_temporary_file(self) -> None:
        with patch.object(home.os, "replace", side_effect=OSError("disk full")):
            home.save_spell({"since": 1.0, "at": 2.0})
        self.assertEqual([path.name for path in home.spells_file().parent.glob(f".{home.spells_file().name}.*")], [])
        home.save_spell({"since": 1.0, "at": 2.0})
        self.assertEqual(self.spells(), [{"since": 1.0, "at": 2.0}])

    def test_the_largest_process_groups_are_named_by_their_leader(self) -> None:
        rows = [home.Process(11, 10, 10, "bot"), home.Process(10, 1, 10, "python3"), home.Process(12, 10, 10, "bot"),
                home.Process(20, 1, 20, "zsh"), home.Process(31, 1, 30, "bot"), home.Process(40, 0, 0, "")]
        self.assertEqual(home.top_groups(rows, 2), ["3 python3 (pgid 10)", "1 zsh (pgid 20)"])


# E0510 (1 Oct 16:43): vm_stat's fork refused with EAGAIN ended a whole SPRT; it now reads as no room this tick.
class ReclaimableForkRefused(unittest.TestCase):
    def test_a_refused_fork_reads_as_no_room(self):
        from unittest.mock import patch
        with patch.object(home.subprocess, "run", side_effect=BlockingIOError(35, "Resource temporarily unavailable")):
            self.assertEqual(home.reclaimable_gb(), 0.0)


if __name__ == "__main__":
    unittest.main()
