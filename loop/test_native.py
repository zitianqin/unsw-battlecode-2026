"""Focused tests for the native session adapter; never starts agents or games."""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import native


# driver.keep_wip reads, makes and deletes wip/* branches in driver.REPO, the real repo (a worktree shares its refs),
# and run_experiment and native.finish call it: every test here gets a stub, and one that wants it patches its own.
# This Mac's CPU split (home.CpuSplit) is read live by game_slots; every test here gets none (the load-average gate,
# as when it can't be measured), and one that wants a split patches its own. Likewise the room this user's process
# limit leaves for games (home.game_room): none measured, so no process gate. A result owed a judge (judge.owed, from
# judge.FROM) gets the judge task from native.finish in place of its post-mortem: none is owed here (test_judge has
# the native judge).
def setUpModule() -> None:
    for stub in (patch.object(native.driver, "keep_wip", return_value=[]), patch.object(native.CPU, "read", return_value=None),
                 patch.object(native, "game_room", return_value=None),
                 patch.object(native.driver.judge, "owed", return_value=None)):
        stub.start()
        unittest.addModuleCleanup(stub.stop)


# A filler process for the tests below: registers in FILLERS (argv 1), says so, then sleeps until killed.
REGISTER = """
import sys, time
sys.path.insert(0, %r)
import native
native.FILLERS = __import__("pathlib").Path(sys.argv[1])
native.register_filler(sys.argv[2])
print("registered", flush=True)
time.sleep(600)
""" % str(Path(__file__).resolve().parent)


# Starts a REGISTER process in `folder`, killed when `test` ends, and waits until it has registered.
def filler(test: unittest.TestCase, folder: str) -> subprocess.Popen:
    child = subprocess.Popen([sys.executable, "-c", REGISTER, folder, "1"], stdout=subprocess.PIPE)
    test.addCleanup(lambda: (child.kill(), child.wait(), child.stdout.close()))
    test.assertEqual(child.stdout.readline(), b"registered\n")
    return child


# Kills a process a test started, if it still runs.
def kill_quietly(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, 9)


VM = "page size of 16384 bytes\nPages free: 100000.\nPages inactive: 100000.\nPages speculative: 100000.\n"


REFEREE = native.SCRIPT.parent.parent / "referee"


# A referee job between two native bots on a fake map `name`, for the scheduler tests below.
def native_job(name: str):
    if str(REFEREE) not in sys.path:
        sys.path.insert(0, str(REFEREE))
    import run
    return run.Job(Path(name), Path("a"), Path("b"), "A")


# A stand-in for driver.evaluation_lock that holds nothing.
def unlocked(**_) -> contextlib.nullcontext:
    return contextlib.nullcontext()


# The game machines as remote.json lists them on 2026-09-29 (name: cores, its "max" two games a core), zz part-time.
FLEET = {"mm": 10, "mm1": 10, "ll": 12, "zz": 16, "sz-wsl": 8, **{f"az-{i}": 2 for i in range(8)}}


def fleet_hosts(fleet: dict[str, int] = FLEET) -> list:
    sys.path.insert(0, str(REFEREE))
    import remote
    return [remote.Host(name, 2 * cores, part_time=name == "zz") for name, cores in fleet.items()]


# FLEET as remote.json's text.
def fleet_json(fleet: dict[str, int] = FLEET) -> str:
    return json.dumps({"hosts": [{"name": name, "max": 2 * cores, **({"part_time": True} if name == "zz" else {})}
                                 for name, cores in fleet.items()]})


# The games each of the coders in `lanes` (all but the `holder`'s, CODING of them, by default), then the review, may
# have of a host with `cores` beside an evaluation playing there (native.host_share by native.coder_slot).
def coder_games(cores: int, offset: int, holder: int | None, lanes: list[int] | None = None) -> list[int]:
    if lanes is None:
        lanes = [lane for lane in range(1, native.driver.IN_FLIGHT + 1) if lane != holder][:native.driver.CODING]
    return [native.host_share(cores, True, offset=offset, slot=slot, slots=slots)
            for slot, slots in (native.coder_slot(lane, holder) for lane in [*lanes, None])]


class TestResourceGate(unittest.TestCase):
    def test_quick_stops_before_games_when_bot_cases_fail(self) -> None:
        metadata = {"id": "E9999", "idea": {}, "lane": 1, "base": "base"}
        with patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "read_text", return_value=json.dumps(metadata)), \
                patch.object(native.driver, "evaluation_lock", return_value=contextlib.nullcontext()), \
                patch.object(native.driver, "check_bot_tests", return_value=CompletedProcess([], 1, "FAIL case", "")), \
                patch.object(native.subprocess, "run") as games, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                native.operate("quick", "E9999", "")
        self.assertEqual(result.exception.code, 1)
        games.assert_not_called()

    def test_quick_runs_the_main_checkouts_script_in_the_clone(self) -> None:
        # The clone's loop/quick.py is loop/champion's, from 2026-09-23: no Sprint maps and a 600-line-older referee.
        metadata = {"id": "E9999", "idea": {}, "lane": 1, "base": "base"}
        with patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "read_text", return_value=json.dumps(metadata)), \
                patch.object(native.driver, "evaluation_lock", return_value=contextlib.nullcontext()), \
                patch.object(native.driver, "check_bot_tests", return_value=CompletedProcess([], 0, "ok", "")), \
                patch.object(native.subprocess, "run") as games, contextlib.redirect_stdout(io.StringIO()):
            native.operate("quick", "E9999", "")
        clone = native.driver.WORKTREES / "E9999"
        self.assertEqual(games.call_args.args[0], [str(clone / ".venv/bin/python"), str(native.SCRIPT), "play",
                                                   "loop/quick.py", "--clone", str(clone)])
        self.assertEqual(games.call_args.kwargs["cwd"], native.driver.REPO)

    def test_quick_holds_no_evaluation_lock_or_marker_while_its_games_play(self) -> None:
        # E0167's quick held both from 14:58 on 2026-09-27 while its games waited out this Mac's load: E0168's quick
        # queued behind it, the sweep and prewarm paused and none of the 56 host slots played (MISSES 49).
        run = native.driver.STATE / "runs" / "E9998"
        run.mkdir(parents=True, exist_ok=True)
        (run / "experiment.json").write_text(json.dumps({"id": "E9998", "idea": {}, "lane": 1, "base": "base"}))
        seen = []

        def games(*args, **kwargs):
            seen.append(native.evaluation_running())
            with native.driver.evaluation_lock(wait=False) as free:  # what an evaluation or refresh would take
                seen.append(free)
        with patch.object(native.driver, "check_bot_tests", return_value=CompletedProcess([], 0, "ok", "")), \
                patch.object(native.subprocess, "run", side_effect=games), contextlib.redirect_stdout(io.StringIO()):
            native.operate("quick", "E9998", "")
        self.assertEqual(seen, [False, True])

    def test_quick_runs_while_an_evaluation_holds_the_lock(self) -> None:
        # E0164's quick waited 28 of its 30 minutes behind E0163's SPRT on 2026-09-27 (MISSES 49).
        run = native.driver.STATE / "runs" / "E9996"
        run.mkdir(parents=True, exist_ok=True)
        (run / "experiment.json").write_text(json.dumps({"id": "E9996", "idea": {}, "lane": 1, "base": "base"}))
        held, release, played = threading.Event(), threading.Event(), []

        def evaluation():
            with native.driver.evaluation_lock():
                held.set()
                release.wait(30)
        holder = threading.Thread(target=evaluation)
        holder.start()
        self.addCleanup(lambda: (release.set(), holder.join()))
        self.assertTrue(held.wait(10))

        def quick():
            with patch.object(native.driver, "check_bot_tests", return_value=CompletedProcess([], 0, "ok", "")), \
                    patch.object(native.subprocess, "run", side_effect=lambda *a, **k: played.append(True)), \
                    contextlib.redirect_stdout(io.StringIO()):
                native.operate("quick", "E9996", "")
        lane = threading.Thread(target=quick, daemon=True)
        lane.start()
        lane.join(10)
        self.assertEqual(played, [True])

    def test_a_script_in_main_plays_with_the_working_folders_referee(self) -> None:
        # A tune (driver.run_tuner) and the sweep run main's script in ~/.bc-loop/referee: the pinned referee.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for checkout in ("main", "pinned"):
                (root / checkout / "referee").mkdir(parents=True)
                (root / checkout / "referee" / "run.py").write_text(f"WHERE = {checkout!r}\n")
            (root / "main" / "loop").mkdir()
            script = root / "main" / "loop" / "tune.py"
            script.write_text(f"import pathlib, run\npathlib.Path({str(root / 'seen')!r}).write_text(run.WHERE)\n")
            modules = {k: v for k, v in sys.modules.items() if k != "run"}
            with patch.dict(sys.modules, modules, clear=True), \
                    patch.object(sys, "path", list(sys.path)), patch.object(sys, "argv", list(sys.argv)), \
                    patch.dict(os.environ, {"BC_YIELD": "", "BC_REMOTE_STATUS": ""}):
                cwd = os.getcwd()
                os.chdir(root / "pinned")
                try:
                    native.play_script(str(script), [])
                finally:
                    os.chdir(cwd)
            self.assertEqual((root / "seen").read_text(), "pinned")

    def test_quick_adds_the_two_sprint_maps_and_plays_the_clones_bots(self) -> None:
        with patch.object(sys, "path", list(sys.path)):
            import quick
        names = [f"{p.parent.name}/{p.name}" for p in quick.quick_maps()]
        self.assertLessEqual({"sprint/dilemma.map", "sprint/autarky.map"}, set(names))
        self.assertEqual(len(names), len(quick.LADDER) + len(quick.SPRINT) + 5)  # and quick_maps' five gen-fixed maps
        self.assertTrue(all(p.exists() for p in quick.quick_maps()))
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory).resolve() / "E9997"
            (clone / ".champion/bot").mkdir(parents=True)
            (clone / "bot").mkdir()
            (clone / "bot/params.hpp").write_text("    bool debugIndicator = false;  // the debug line\n")
            (clone / ".git/info").mkdir(parents=True)
            (clone / native.driver.LANE_FILE).write_text("4\n")  # make_worktree's
            status = []
            played = []

            def play_pairs(cand, base, *args, **kwargs):  # the copy is gone once main returns
                played.append((cand.name, (cand / "params.hpp").read_text()))
                status.append(quick.remote.STATUS)
                return []

            with patch.object(sys, "argv", ["quick.py", "--clone", str(clone)]), \
                    patch.object(quick, "play_pairs", side_effect=play_pairs) as play, \
                    patch.object(quick, "cpu_line", return_value="cpu ok"), \
                    patch.object(native, "yield_to_evaluations") as yielding, \
                    patch.object(quick.remote, "STATUS", quick.remote.STATUS), \
                    contextlib.redirect_stdout(io.StringIO()):
                quick.main()
        # The candidate plays as its logging copy (P.debugIndicator on, which only adds the debug line), so a death's
        # line can say it was a capfeed fire (quick.capfeed_deaths); the champion as it is.
        ((name, params),) = played
        self.assertTrue(name.startswith("cand-"))
        self.assertIn("bool debugIndicator = true;", params)
        self.assertEqual(play.call_args.args[1], clone / ".champion/bot")
        # On the game machines too, yielding to an evaluation as a coder's own games do, with their status in the
        # coder's file (until 2026-09-27: workers=3, this Mac only; MISSES 49).
        self.assertEqual(play.call_args.kwargs["workers"], "auto")
        self.assertIs(quick.native, native)
        yielding.assert_called_once_with(quick.remote, lane=4)  # its slot beside an evaluation (coder_slot)
        self.assertEqual(status, [native.coder_status("E9997")])

    def test_play_gives_a_script_importing_native_the_running_module(self) -> None:
        # quick.py's yielding (pair_games) must set the module whose game_slots gate this Mac, not a second copy.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "referee").mkdir()
            (root / "referee" / "run.py").write_text("")
            script = root / "uses_native.py"
            script.write_text(f"import native, pathlib\npathlib.Path({str(root / 'seen')!r}).write_text("
                              f"str(native.game_slots is __import__('run').game_slots))\n")
            # As under `python loop/native.py play`: the running module is __main__ and nothing is named native yet.
            modules = {k: v for k, v in sys.modules.items() if k not in ("run", "native")}
            modules["__main__"] = native
            with patch.dict(sys.modules, modules, clear=True), \
                    patch.object(native, "__name__", "__main__"), \
                    patch.object(sys, "path", list(sys.path)), patch.object(sys, "argv", list(sys.argv)), \
                    patch.dict(os.environ, {"BC_YIELD": "", "BC_REMOTE_STATUS": ""}):
                cwd = os.getcwd()
                os.chdir(root)
                try:
                    native.play_script(str(script), [])
                finally:
                    os.chdir(cwd)
            self.assertEqual((root / "seen").read_text(), "True")

    def test_a_terminated_play_stops_the_games_it_started(self) -> None:
        # driver.stoppable ends a prepared CPU gate with SIGTERM once the lock frees. Each game leads a process group
        # of its own (run.run_game), so a play process dying at the signal left its games playing on.
        script = Path(tempfile.mkdtemp()) / "one_game.py"
        self.addCleanup(__import__("shutil").rmtree, script.parent)
        script.write_text("""
import subprocess
from pathlib import Path
import run
run.game_slots = lambda workers, active=0: 1
run.build_bots = lambda bots, sandbox: {bot: None for bot in bots}
def game(job, sandbox, path, started=None, nice=0, watch=False, native=None):
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    started(proc)
    print(proc.pid, flush=True)
    proc.wait()
    return run.RawGame("", 0, None, 0.0)
run.run_game = game
run.play_games([run.Job(Path("m.map"), Path("a"), Path("b"), "A")], 1, False)
""")
        child = subprocess.Popen([sys.executable, str(native.SCRIPT), "play", str(script)], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, cwd=native.SCRIPT.parent.parent)
        game = int(child.stdout.readline())
        self.addCleanup(kill_quietly, game)
        child.terminate()
        self.assertEqual(child.wait(timeout=30), 143)
        child.stdout.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(game, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the game played on after its play process was terminated")

    # Plays `jobs` through the referee's real scheduler inside native.play_script, as `native.py play referee/sprt.py`
    # would, with this Mac full (load 10 on 10 cores) until `mac_free` is set and `hosts` as the game hosts. Returns
    # the results and the jobs that played here, in order.
    def play_batch(self, jobs, hosts, sandbox=False, mac_free=None, started=None):
        native_job("")  # the referee on the path
        import remote
        import run
        results, here = [], []
        mac_free = mac_free or threading.Event()

        def batch(*_args, **_kwargs):
            scheduler = run.Scheduler("auto", sandbox, {Path("a"): Path("a.bin"), Path("b"): Path("b.bin")})
            scheduler.hosts = hosts
            if started is not None:
                started.append(scheduler)
            with tempfile.TemporaryDirectory() as folder:
                results.extend(scheduler.play(jobs, Path(folder)))

        def game(job, *_args):
            here.append(job.map.name)
            return run.RawGame(f"{job.map.name} here", 0, None, 0.0)

        with patch.object(native.runpy, "run_path", side_effect=batch), \
                patch.object(native.os, "cpu_count", return_value=10), \
                patch.object(native.os, "getloadavg", side_effect=lambda: (2.0 if mac_free.is_set() else 10.0, 0, 0)), \
                patch.object(native, "reclaimable_gb", return_value=8.0), \
                patch.object(run, "game_slots", run.game_slots), patch.object(run, "seeded_toolkit", return_value=False), \
                patch.object(run, "run_game", side_effect=game), patch.object(run, "parse_game", lambda job, g: g.stdout), \
                patch.object(remote, "STATUS", Path(tempfile.mkdtemp()) / "status.json"), \
                patch.object(sys, "argv", list(sys.argv)), patch.object(sys, "path", list(sys.path)), \
                patch.dict(os.environ, {"BC_YIELD": "", "BC_REMOTE_STATUS": ""}), contextlib.redirect_stderr(io.StringIO()):
            native.play_script("referee/sprt.py", [])
        return results, here

    def test_a_batch_starts_its_remote_games_while_this_mac_is_full(self) -> None:
        # MISSES 53: every referee run waited in play_script for room here before any game, though
        # most of its games play on the hosts; the gate is this Mac's and applies per game here.
        native_job("")
        import remote

        class Host(remote.Host):
            def __init__(self):
                super().__init__("fake", 4)
                self._platform = remote.PLATFORM

            def capacity(self):
                return self.max

            def play(self, game_map, bots, *_args, **_kwargs):
                return remote.RemoteGame(f"{game_map.name} on the host", 0, None)

            def kill(self, folder):
                pass

        jobs = [native_job(f"m{i}") for i in range(3)]
        results, here = self.play_batch(jobs, [Host()])
        self.assertEqual(results, [f"m{i} on the host" for i in range(3)])
        self.assertEqual(here, [])

    def test_with_no_host_every_game_waits_here_until_this_mac_has_room(self) -> None:
        # The CPU gate's sandbox games never go to a host (run.Scheduler), nor any game with no host usable: each waits
        # for this Mac's load gate, per game, as before.
        for sandbox in (True, False):
            with self.subTest(sandbox=sandbox):
                mac_free, started, box = threading.Event(), [], {}
                batch = threading.Thread(target=lambda: box.setdefault("out", self.play_batch(
                    [native_job("m0"), native_job("m1")], [], sandbox, mac_free, started)), daemon=True)
                batch.start()
                deadline = time.monotonic() + 10
                while not started and time.monotonic() < deadline:
                    time.sleep(0.05)
                time.sleep(0.5)
                self.assertTrue(batch.is_alive())  # waiting per game while this Mac is full
                mac_free.set()
                started[0].wake()
                batch.join(20)
                self.assertFalse(batch.is_alive())
                self.assertEqual(box["out"], (["m0 here", "m1 here"], ["m0", "m1"]))

    def test_recorded_skips_a_result_being_written(self) -> None:
        # quick reads results.jsonl without the lock: a half-written last line must not crash it.
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.jsonl"
            results.write_text(json.dumps({"id": "E0001"}) + "\n" + '{"id": "E0002", "ver')
            with patch.object(native.driver, "RESULTS", results):
                self.assertTrue(native.recorded("E0001"))
                self.assertFalse(native.recorded("E0002"))
                self.assertFalse(native.recorded("E0003"))

    def test_quick_cpu_check_plays_on_the_mac_hosts_and_waits_at_most_ten_minutes_with_none_playing(self) -> None:
        # E0317's and E0321's quick checks (29 Sep) passed a wait for one slot here, then their CPU game waited on this
        # Mac's gate from about 10:10 for hours. Now its two sandbox games may play on the hosts of this Mac's
        # platform, and the check gives up after quick.CPU_WAIT_S in all with nothing playing anywhere.
        with patch.object(sys, "path", list(sys.path)):
            import quick
        now, calls = [0.0], []
        clock = types.SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))

        def games(jobs, workers, sandbox, replays=None, sandbox_hosts=False):  # waits here for a slot, 15 s a pass
            calls.append(([job.a_side for job in jobs], workers, sandbox, sandbox_hosts))
            while not quick.run.game_slots("auto", 0):
                clock.sleep(15)
            return []

        for slots, expect in (([0] * 1000, quick.UNCHECKED), ([0, 0, 1], "cpu FAIL")):
            now[0] = 0.0
            with patch.object(quick.run, "play_games", games), patch.object(quick.remote, "load_hosts", return_value=[]), \
                    patch.object(native, "time", clock), patch.object(native, "game_slots", side_effect=slots), \
                    patch.object(quick.run, "game_slots", quick.run.game_slots), contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(quick.cpu_line(Path("cand")).startswith(expect))
            self.assertEqual(calls[-1], (["A", "B"], "auto", True, True))
            if expect == quick.UNCHECKED:
                self.assertGreaterEqual(now[0], quick.CPU_WAIT_S)
                self.assertLess(now[0], quick.CPU_WAIT_S + 30)

    def test_games_here_wait_while_this_users_process_limit_leaves_no_room(self) -> None:
        # 28 Sep 01:17-01:41: forks failed with Errno 35 at kern.maxprocperuid, each game one process a dragon. At load
        # 0 on 10 cores the load gate gives 7 slots; the process limit's room (home.game_room) bounds the new games.
        for room, requested, active, expected in ((None, "auto", 0, 7), (0, "auto", 0, 0), (0, "auto", 3, 0),
                                                  (2, "auto", 1, 3), (20, "auto", 0, 7), (0, 4, 0, 0), (1, 4, 0, 1)):
            said = io.StringIO()
            with self.subTest(room=room, requested=requested, active=active), \
                    patch.object(native, "game_room", return_value=room) as gate, \
                    patch.object(native, "user_processes", return_value=2300), \
                    patch.object(native, "process_limit", return_value=2666), \
                    patch.object(native, "_last_wait_note", -60.0), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(active, 0, 0)), \
                    patch.object(native.subprocess, "run", return_value=CompletedProcess([], 0, VM, "")), \
                    contextlib.redirect_stderr(said):
                self.assertEqual(native.game_slots(requested, active), expected)
                gate.assert_called_once_with(active)  # its own games not yet seen are charged too (home.game_room)
                if room == 0 and active == 0:
                    self.assertIn("2300 of this user's processes", said.getvalue())

    def test_auto_slots_count_our_own_games_as_ours_not_other_load(self) -> None:
        # (load, games we already run here, slots): our 5 games at load 6 leave 6 slots, not 1.
        for load, active, expected in ((0, 0, 7), (5, 0, 2), (6.9, 0, 1), (6, 5, 6), (7.5, 0, 0), (9, 1, 0)):
            with self.subTest(load=load, active=active), patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native.subprocess, "run", return_value=CompletedProcess([], 0, VM, "")), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native.game_slots("auto", active), expected)

    def test_niced_games_fill_the_cores_dev_work_leaves_and_the_evaluations_own_keep_the_gate(self) -> None:
        # sig, 2026-09-27: dev work on this Mac (coders' builds and tests, builders', his own) comes first. At load 8.6
        # on 10 cores the load-average gate at 0.8 let about one game play here, though most of that load was games.
        # Niced games take what the cores busy at normal priority leave; the evaluation's CPU gate, at normal priority,
        # counts only dev work as other load, not the niced games filling the rest.
        Split = __import__("home").Split
        for niced, dev, games_elsewhere, active, expected in (
                (True, 2.0, 3.0, 0, 2), (True, 2.0, 3.0, 4, 5), (True, 2.0, 0.0, 4, 6), (True, 8.5, 0.0, 0, 0),
                (False, 2.0, 7.0, 1, 7), (False, 8.5, 0.0, 0, 0)):
            with self.subTest(niced=niced, dev=dev, active=active), patch.object(native, "_niced", niced), \
                    patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                    patch.object(native, "_split_base", (None, 0)), \
                    patch.object(native.CPU, "read", return_value=Split(dev, games_elsewhere + active, 0.0)), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(5.0, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=0.8), patch("sys.stderr"):
                self.assertEqual(native.game_slots("auto", active), expected)
        with patch.object(native, "_niced", True), patch.object(native.CPU, "read", return_value=Split(1.0, 0.0, 0.0)), \
                patch.object(native.os, "cpu_count", return_value=10), \
                patch.object(native, "reclaimable_gb", return_value=__import__("home").MIN_FREE_GB - 0.5), \
                patch("sys.stderr"):
            self.assertEqual(native.game_slots("auto"), 0)  # memory keeps its floor
        # Until the next split this process adds at most home.NICE_STEP games, however idle the last one read: every
        # process reads the same idle cores, and its new games show only in the next split.
        with patch.object(native, "_niced", True), patch.object(native, "_coder", False), \
                patch.object(native, "_split_base", (None, 0)), \
                patch.object(native.CPU, "read", return_value=Split(1.0, 0.0, 0.0)), \
                patch.object(native.os, "cpu_count", return_value=10), patch.object(native, "reclaimable_gb", return_value=8.0), \
                patch.object(native.os, "getloadavg", return_value=(1.0, 0, 0)), \
                patch.object(native, "base_share", return_value=0.8):
            self.assertEqual([native.game_slots("auto", active) for active in range(4)], [2, 2, 2, 2])

    def test_niced_games_stop_starting_where_sigs_other_sessions_gate_on_the_load_average(self) -> None:
        # sig's rule for his sessions: a dev stack starts only below 0.7 x cores of 1-minute load. Niced games take
        # only idle CPU, but filling every core they would hold that load above his gate all day.
        Split = __import__("home").Split
        for load, share, expected in ((6.9, 0.8, 2), (7.0, 0.8, 0), (5.0, 0.5, 0), (4.9, 0.5, 2)):
            note = io.StringIO()
            with self.subTest(load=load, share=share), patch.object(native, "_niced", True), \
                    patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                    patch.object(native, "_split_base", (None, 0)), patch.object(native, "_last_wait_note", -1e9), \
                    patch.object(native.CPU, "read", return_value=Split(1.0, 0.0, 0.0)), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=share), contextlib.redirect_stderr(note):
                self.assertEqual(native.game_slots("auto"), expected)
                if not expected:
                    self.assertIn(f"1-minute load {load:.2f} reaches", note.getvalue())

    def test_beside_an_evaluation_the_players_niced_games_together_keep_to_half_of_this_mac(self) -> None:
        # CLAUDE.md: what plays beside an evaluation gets at most half of this Mac. Each yielding process was capped at
        # half by itself, so five coders' games could fill it and push the evaluation's own off it. The players' games
        # at YIELD_NICE together keep to mac_half; the evaluation's own (at GAME_NICE) leave room only for each other.
        Split = __import__("home").Split
        base = dict(dev=1.0, niced=6.0)
        for coder, yielding, active, expected in (
                (True, 4.0, 1, 2),  # one core of the half left
                (True, 5.0, 1, 1),  # the half is full: keep what plays, start none
                (True, 6.0, 1, 0),  # over it (the evaluation's grew): step back
                (False, 4.0, 1, 3)):  # the evaluation's: the players' 4 cores don't count against it
            with self.subTest(coder=coder, yielding=yielding), patch.object(native, "_niced", True), \
                    patch.object(native, "_coder", coder), patch.object(native, "_role", ""), \
                    patch.object(native, "_split_base", (None, 0)), \
                    patch.object(native, "evaluating_now", return_value=True), \
                    patch.object(native.CPU, "read", return_value=Split(base["dev"], base["niced"], yielding)), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(3.0, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=0.8), patch("sys.stderr"):
                self.assertEqual(native.game_slots("auto", active), expected)
        self.assertEqual(native.mac_half(10), 5)

    def test_the_evaluations_games_get_slots_while_coders_nice_0_games_load_this_mac_to_half(self) -> None:
        # Coders' sandboxes refuse a lower priority, so their games play at nice 0 (home.WORKER_NICE since 28 Sep),
        # not yielding: the half cap never saw them, and fillers could fill up to sig's 0.7 gate on top, where
        # the evaluation's own niced games start none. Beside an evaluation every yielding player, coders included,
        # stops at a 1-minute load of half (players_full); the band from there to 0.7 is the evaluation's.
        Split = __import__("home").Split
        for load, niced, coder, expected in (
                (5.0, True, False, 2),  # the evaluation's niced games: coders' half leaves them room
                (6.5, True, False, 2),  # still under sig's 0.7 x 10
                (5.0, True, True, 0),  # a filler at YIELD_NICE: the half is full
                (5.0, False, True, 0),  # a coder's own nice-0 games
                (4.5, True, True, 2), (4.5, False, True, 1)):  # under half, both may start
            with self.subTest(load=load, niced=niced, coder=coder), patch.object(native, "_niced", niced), \
                    patch.object(native, "_coder", coder), patch.object(native, "_role", ""), \
                    patch.object(native, "_split_base", (None, 0)), \
                    patch.object(native, "evaluating_now", return_value=True), \
                    patch.object(native.CPU, "read", return_value=Split(4.0, 0.0, 0.0)), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=0.8), patch("sys.stderr"):
                self.assertEqual(native.game_slots("auto"), expected)

    def test_a_capped_normal_priority_gate_takes_what_dev_work_leaves_never_more_than_its_cap(self) -> None:
        # Review (28 Sep): driver.prepare's CPU gate at a fixed --workers 5 started 5 normal-priority games whenever
        # the gate allowed one, beside 7.5 cores of sig's work. With auto and BC_MAC_CAP (native.mac_cap) it takes
        # floor(the share - dev work) as the evaluation's own gate does, at most the cap.
        Split = __import__("home").Split
        for dev, cap, requested, expected in ((7.5, "5", "auto", 1), (2.0, "5", "auto", 5), (2.0, None, "auto", 6),
                                              (7.5, None, "5", 5), (5.0, "5", "auto", 3), (9.0, "5", "auto", 0)):
            env = {"BC_MAC_CAP": cap} if cap else {}
            with self.subTest(dev=dev, cap=cap, requested=requested), patch.dict(native.os.environ, env), \
                    patch.object(native, "_niced", False), patch.object(native, "_coder", False), \
                    patch.object(native, "_role", ""), patch.object(native, "game_room", return_value=None), \
                    patch.object(native.CPU, "read", return_value=Split(dev, 0.0, 0.0)), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(dev, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=0.8), patch("sys.stderr"):
                if not cap:
                    native.os.environ.pop("BC_MAC_CAP", None)
                self.assertEqual(native.game_slots(requested), expected)

    def test_prepared_bot_tests_wait_for_dev_work_only_not_the_players_half(self) -> None:
        # 28 Sep: coders' workers held the 1-minute load at 15-25 of 10 cores, so every prepare waited for the players'
        # half (load under 5) until the lock freed. The next evaluation's checks wait only for dev work, as
        # tools/gated_test.sh does; with no split published they keep the 1-minute load.
        Split = __import__("home").Split
        for split, load, expected in ((Split(3.7, 5.0, 0.0), 20.0, True), (Split(8.0, 0.0, 0.0), 8.0, False),
                                      (None, 7.9, True), (None, 20.0, False)):
            waits = iter([True, True, False])
            with self.subTest(split=split, load=load), patch.object(native.CPU, "read", return_value=split), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native, "base_share", return_value=0.8), patch.object(native.time, "sleep"):
                self.assertEqual(native.wait_for_dev(lambda: next(waits)), expected)

    def test_without_a_split_niced_games_still_stop_at_sigs_sessions_gate(self) -> None:
        # With no CPU split the games fall back to the load gate at the loop's share (0.8), which let niced games
        # start up to 0.8 x cores, past the 0.7 where sig's other sessions stop starting work.
        for niced, expected in ((True, 0), (False, 1)):  # the evaluation's normal-priority gate keeps the share
            with self.subTest(niced=niced), patch.object(native, "_niced", niced), \
                    patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                    patch.object(native.CPU, "read", return_value=None), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(7.2, 0, 0)), \
                    patch.object(native, "reclaimable_gb", return_value=8.0), \
                    patch.object(native, "base_share", return_value=0.8), patch("sys.stderr"):
                self.assertEqual(native.game_slots("auto"), expected)

    def test_every_loop_game_here_is_niced_but_the_evaluations_cpu_gate_and_identical_check(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import run
        runner = types.SimpleNamespace(__file__=str(Path("referee/run.py").resolve()), game_slots=None, main=lambda: 0,
                                       GAME_NICE=0, nice_command=run.nice_command)
        for script, flag, nice in (("referee/sprt.py", None, native.GAME_NICE), ("referee/cpu.py", None, 0),
                                   ("referee/identical.py", None, 0), ("referee/cpu.py", "1", native.YIELD_NICE),
                                   ("loop/tune.py", "tune", native.YIELD_NICE)):
            runner.GAME_NICE = 0
            with self.subTest(script=script, flag=flag), patch.dict(sys.modules, {"run": runner}), \
                    patch.object(native, "_niced", False), patch.object(native, "_coder", bool(flag)), \
                    patch.object(native, "yield_to_evaluations"), \
                    patch.dict(os.environ, {"BC_YIELD": flag} if flag else {}), \
                    patch.object(native.runpy, "run_path"), patch.object(sys, "path", list(sys.path)), \
                    patch.object(sys, "argv", list(sys.argv)):
                if not flag:
                    os.environ.pop("BC_YIELD", None)
                try:
                    native.play_script(script, [])
                except SystemExit:
                    pass
                self.assertEqual((runner.GAME_NICE, native._niced), (nice, bool(nice)))

    def test_a_workers_own_cpu_gate_games_are_gated_as_niced_games(self) -> None:
        # Review of the worker niceness (28 Sep): a coder's `native.py play referee/cpu.py` or `identical.py` is one
        # of NORMAL_PRIORITY, so play_script skipped lower_games and gated it by the dev load alone. Its games play at
        # the coder's inherited home.WORKER_NICE, so the split counts them as niced, not dev: with dev 0.5 and niced
        # 9 of 10 cores and a 1-minute load of 22 beside an evaluation, it started 7-8 more. The evaluation's own
        # (the driver's, at nice 0) keep their normal-priority gate.
        Split = __import__("home").Split
        runner = types.SimpleNamespace(__file__=str(Path("referee/run.py").resolve()), game_slots=None, main=lambda: 0,
                                       GAME_NICE=0, nice_command=lambda command, nice: command)
        for script, priority, expected in (("referee/cpu.py", native.WORKER_NICE, 0),
                                           ("referee/identical.py", native.WORKER_NICE, 0),
                                           ("referee/cpu.py", 0, 7)):
            with self.subTest(script=script, priority=priority), patch.dict(sys.modules, {"run": runner}), \
                    patch.object(native, "_niced", False), patch.object(native, "_coder", False), \
                    patch.object(native, "_role", ""), patch.object(native, "_split_base", (None, 0)), \
                    patch.dict(os.environ, {}), patch.object(native.runpy, "run_path"), \
                    patch.object(sys, "path", list(sys.path)), patch.object(sys, "argv", list(sys.argv)), \
                    patch.object(native.os, "getpriority", return_value=priority):
                os.environ.pop("BC_YIELD", None)
                native.play_script(script, [])
                self.assertEqual(native._niced, bool(priority))
                with patch.object(native, "evaluating_now", return_value=True), \
                        patch.object(native.CPU, "read", return_value=Split(0.5, 9.0, 0.0)), \
                        patch.object(native.os, "cpu_count", return_value=10), \
                        patch.object(native.os, "getloadavg", return_value=(22.0, 0, 0)), \
                        patch.object(native, "reclaimable_gb", return_value=8.0), \
                        patch.object(native, "base_share", return_value=0.8), \
                        patch.object(native, "game_room", return_value=None), patch("sys.stderr"):
                    self.assertEqual(native.game_slots("auto"), expected)

    def test_games_are_niced_only_where_the_referee_and_the_sandbox_let_them_be(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import run
        runner = types.SimpleNamespace(GAME_NICE=0, nice_command=run.nice_command)
        calls = []

        def probe(command, **kwargs):
            calls.append((command, kwargs.get("preexec_fn")))
            return CompletedProcess(command, 0, answer, "")

        # /usr/bin/nice runs the command at the priority it had where a sandbox refuses setpriority, with a warning:
        # the probe reads the priority the child got. It runs through nice, never a preexec_fn (a threaded parent's
        # forked child can deadlock before exec).
        for answer, coder, expected in (("19\n", False, 19), ("20\n", True, 20), ("0\n", False, 0), ("", False, 0)):
            runner.GAME_NICE = 0
            with self.subTest(answer=answer), patch.object(native, "_niced", False), \
                    patch.object(native, "_coder", coder), patch.object(native.subprocess, "run", probe), \
                    patch.object(native.os, "getpriority", return_value=0):
                native.lower_games(runner)
                self.assertEqual((runner.GAME_NICE, native._niced), (expected, bool(expected)))
        self.assertTrue(all(command[:2] == ["/usr/bin/nice", "-n"] and preexec is None for command, preexec in calls))
        # A coder at home.WORKER_NICE (meta.private_tmp) whose sandbox refuses a lower one: its games play at the
        # niceness they inherit, with no wrapper, gated as niced games (the split counts them so, not as dev work).
        for answer, niced in ((f"{native.WORKER_NICE}\n", True), (f"{native.WORKER_NICE - 1}\n", False)):
            runner.GAME_NICE = 0
            with self.subTest(answer=answer), patch.object(native, "_niced", False), \
                    patch.object(native, "_coder", True), patch.object(native.subprocess, "run", probe), \
                    patch.object(native.os, "getpriority", return_value=int(answer)):
                native.lower_games(runner)
                self.assertEqual((runner.GAME_NICE, native._niced), (0, niced))
        # A referee checkout from before niced games (version skew: ~/.bc-loop/referee trails main) can't run them,
        # so its games stay at normal priority, gated as such, not counted as dev work by a niced gate.
        # A worker's games with it still play at the niceness the worker has, so they are niced games all the same.
        old = types.SimpleNamespace(GAME_NICE=0)
        for priority in (0, native.WORKER_NICE):
            with self.subTest(priority=priority), patch.object(native, "_niced", False), \
                    patch.object(native.subprocess, "run") as never, \
                    patch.object(native.os, "getpriority", return_value=priority):
                native.lower_games(old)
                never.assert_not_called()
                self.assertEqual((old.GAME_NICE, native._niced), (0, bool(priority)))
        # And the real thing, on this Mac; loop/quick.py then yields (play_script lowered its games first), so they
        # count as a player's beside an evaluation.
        runner.GAME_NICE = 0
        remote = types.SimpleNamespace(Host=types.SimpleNamespace(capacity=None, measure=None))
        with patch.object(native, "_niced", False), patch.object(native, "_coder", False), \
                patch.object(native, "_role", ""), patch.object(native, "_lane", None), \
                patch.object(native, "_lowered", []):
            native.lower_games(runner)
            self.assertEqual((runner.GAME_NICE, native._niced), (native.GAME_NICE, True))
            native.yield_to_evaluations(remote, lane=3)
            self.assertEqual(runner.GAME_NICE, native.YIELD_NICE)

    def test_load_fraction_file_raises_the_share_up_to_nine_tenths(self) -> None:
        # (file text, load, slots): 0.9 of 10 cores at load 2 leaves 7; above 0.9 is capped; junk means 0.7.
        folder = Path(tempfile.mkdtemp())
        for text, load, expected in (("0.9", 2, 7), ("0.9", 9.2, 0), ("1.5", 0, 9), ("junk", 0, 7)):
            (folder / "load-fraction").write_text(text)
            with self.subTest(text=text, load=load), patch.object(native, "LOAD_FRACTION", folder / "load-fraction"), \
                    patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native.subprocess, "run", return_value=CompletedProcess([], 0, VM, "")), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native.game_slots("auto"), expected)

    def test_a_sandboxed_coder_reads_the_copy_but_a_removed_allowance_ends(self) -> None:
        # A coder's sandbox can't read the loop home, so it reads the upkeep's copy; with the file gone, 0.7 again
        # even while a stale copy still says 0.9.
        folder = Path(tempfile.mkdtemp())
        (folder / "copy").write_text("0.9")
        unreadable = types.SimpleNamespace(read_text=lambda: (_ for _ in ()).throw(PermissionError("sandbox")))
        with patch.object(native, "LOAD_FRACTION_COPY", folder / "copy"):
            with patch.object(native, "LOAD_FRACTION", folder / "load-fraction"):
                self.assertEqual(native.load_fraction(), 0.7)
            with patch.object(native, "LOAD_FRACTION", unreadable):
                self.assertEqual(native.load_fraction(), 0.9)

    def test_coder_games_step_back_within_a_game_of_an_evaluation_starting(self) -> None:
        # While an evaluation runs, the coders, a review's rule-count games and the tune-or-filler share (YIELDING
        # players) take at most half of each host of 8+ cores (a host under 8 still gives each coder one game), and each
        # coder at most half of this Mac; otherwise the usual share split over the coders playing there (none else
        # here: the whole host), re-checked before every game.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        import run
        seen = {}
        with patch.object(remote.Host, "capacity", lambda host: 10), \
                patch.object(run, "main", side_effect=lambda: seen.update(capacity=remote.Host.capacity) or 0), \
                patch.object(native, "_coder", False), patch.object(sys, "path", list(sys.path)), \
                self.assertRaises(SystemExit):
            native.play_games("E0001", ["--out", "x"])
        host = remote.Host("mm", 10)
        # Its slot of the half (no holder's lane in a marker here): a game or none, rotated by the host.
        slot, slots = native.coder_slot(native.coder_lane(native.driver.WORKTREES / "E0001"), None)
        offset = native.host_offset("mm", native.host_runs(remote.load_hosts()), slots)
        rationed = native.filler_part(10 // 2, slot, slots, offset)
        # The middle case is an evaluation in a stage that plays only on this Mac (driver.host_stage): E0167's
        # evidence refresh and the CPU gates held the lock with ~95% of the host slots idle; the hosts are the
        # coders' again then, this Mac stays yielded.
        for evaluating, hosts, host_share, mac_share in (
                (True, True, rationed, native.CODER_FRACTION),
                (True, False, 10, native.CODER_FRACTION),
                (False, False, 10, 0.7)):
            with self.subTest(evaluating=evaluating, hosts=hosts), patch.object(native, "_coder", True), \
                    patch.object(native, "evaluating_now", return_value=evaluating), \
                    patch.object(native, "hosts_wanted_now", return_value=hosts):
                self.assertEqual(seen["capacity"](host), host_share)
                self.assertEqual(native.load_fraction(), mac_share)

    def test_a_coders_sandbox_games_give_up_rather_than_wait_out_this_macs_load(self) -> None:
        # E0240 (28 Sep): its sandbox CPU games waited 2.5 h on this Mac's gate, and its coder waited on them. Now the
        # batch's waits with nothing playing add up to at most CPU_WAIT_S, as quick.py's CPU check waits, across all
        # its games (not only before the first), then it says so and writes nothing.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        import run
        now = [0.0]
        clock = types.SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))

        def main() -> int:  # the scheduler's asks for three games, one at a time, 15 s apart while there is no room
            for _ in range(3):
                while not run.game_slots("auto", 0):
                    clock.sleep(15)
                self.assertEqual(run.game_slots("auto", 1), 1)  # full while it plays: not a wait
                clock.sleep(60)
            return 0

        def unbounded() -> int:  # games that may play on the hosts keep native's own gate
            self.assertIs(run.game_slots, native.game_slots)
            return 0

        sandbox = ["--a", "x", "--b", "y", "--maps", "m.map", "n.map", "o.map", "--sandbox", "--out", "o.jsonl"]
        long, short = [0] * 27, [0] * 10  # 405 s and 150 s of waiting
        for args, slots, code in ((sandbox, [0] * 1000, 75),  # never room: gives up before the first game
                                  (sandbox, [1, 1] + long + [1, 1] + long + [1] * 4, 75),  # 405 s twice: past 600
                                  (sandbox, short + [1, 1] + short + [1, 1] + short + [1, 1], 0),  # 450 s in all
                                  (sandbox[:8] + sandbox[9:], [0] * 1000, 0)):  # hosts may play: native's own gate
            now[0] = 0.0
            with self.subTest(args=args, slots=len(slots)), patch.object(native, "yield_to_evaluations"), \
                    patch.object(native, "coder_lane", return_value=1), patch.object(native, "lower_games"), \
                    patch.object(remote, "STATUS", remote.STATUS), patch.object(run, "game_slots", run.game_slots), \
                    patch.object(run, "fork_room", run.fork_room), patch.object(native, "time", clock), \
                    patch.object(native, "game_slots", side_effect=slots), \
                    patch.object(run, "main", side_effect=main if "--sandbox" in args else unbounded), \
                    patch.object(sys, "argv", list(sys.argv)), \
                    contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as exit:
                native.play_games("E0240", args)
            self.assertEqual(exit.exception.code, code)
            self.assertEqual(native.CPU_UNCHECKED in out.getvalue(), code == 75)
        # The second case gave up on its second wait, 195 s in: 405 + 195 reaches CPU_WAIT_S.
        with patch.object(native, "time", clock), patch.object(native, "game_slots", side_effect=[1] + long + [1]
                                                                                                    + [0] * 20):
            now[0], slots = 0.0, native.batch_slots()
            self.assertEqual(slots("auto", 0), 1)
            while not slots("auto", 0):  # 405 s
                clock.sleep(15)
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                while not slots("auto", 0):
                    clock.sleep(15)
            self.assertEqual(now[0], 405 + 195)

    def test_a_coders_games_play_on_the_hosts_whatever_its_options(self) -> None:
        # E0323 (29 Sep): its self-play with `--workers 4` waited 1.7 h on this Mac's load gate (load 9-15) with the
        # hosts free, since a fixed --workers kept run.Scheduler here, as --sandbox and cpu.py without --hosts did.
        # Now N caps this Mac alone; a move-for-move comparison keeps to this Mac's platform with --same-platform.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        import run
        streams, asked, now, room = [], [], [0.0], [7]
        mm = remote.Host("mm", 10)

        def record(*given, **options) -> None:
            streams.append(options)

        with patch.object(run, "stream_games", record), \
                patch.object(run, "game_slots", run.game_slots), patch.object(remote, "load_hosts", return_value=[mm]), \
                patch.object(native, "game_slots", side_effect=lambda requested, active=0: asked.append(requested) or
                             room[0]), \
                patch.object(native, "time", types.SimpleNamespace(monotonic=lambda: now[0])):
            self.assertEqual(native.on_the_hosts(run, remote, ["--a", "x", "--workers", "4", "--out", "o"]),
                             ["--a", "x", "--out", "o"])
            self.assertEqual(run.game_slots("auto", 0), 4)  # the gate's room, capped at 4
            room[0] = 2
            self.assertEqual((run.game_slots("auto", 0), asked), (2, ["auto", "auto"]))  # less where it has less
            room[0] = 0
            self.assertEqual(native.on_the_hosts(run, remote, ["--workers=2", "--same-platform"]), [])
            run.stream_games([], [], "auto", False)
            self.assertEqual(streams[-1], {"same_platform": True, "sandbox_hosts": False})
            run.stream_games = record  # a process makes one call
            self.assertEqual(native.on_the_hosts(run, remote, ["--sandbox"]), ["--sandbox"])
            run.stream_games([], [], "auto", True)
            self.assertEqual(streams[-1], {"same_platform": False, "sandbox_hosts": True})
            # A sandbox batch gives up only after CPU_WAIT_S with nothing playing anywhere: a game on mm isn't a wait.
            mm.active = 1
            now[0] = native.CPU_WAIT_S + 1
            self.assertEqual(run.game_slots("auto", 0), 0)
            mm.active = 0
            self.assertEqual(run.game_slots("auto", 0), 0)  # waiting from now
            now[0] += native.CPU_WAIT_S
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                run.game_slots("auto", 0)
            self.assertEqual(native.on_the_hosts(run, remote, ["--bot", "b", "--workers", "3"], "cpu.py"),
                             ["--bot", "b", "--hosts"])
            self.assertEqual(native.on_the_hosts(run, remote, ["--workers", "3"], "diag.py"), ["--workers", "3"])

    def test_a_coders_play_script_plays_as_its_lanes_games(self) -> None:
        # A coder's `native.py play referee/identical.py` (or run.py, cpu.py) yielded to no evaluation and kept a
        # fixed --workers here: it now plays as its lane's coder, on the hosts too, its status in the coder's file.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "referee").mkdir()
            (root / "referee" / "run.py").write_text("")
            (root / "referee" / "remote.py").write_text("STATUS = None\n")
            script = root / "referee" / "identical.py"
            script.write_text(f"import pathlib, sys\npathlib.Path({str(root / 'seen')!r}).write_text(' '.join(sys.argv[1:]))\n")
            clone = root / "E9996"
            (clone / ".git/info").mkdir(parents=True)
            (clone / native.driver.LANE_FILE).write_text("3\n")
            modules = {k: v for k, v in sys.modules.items() if k not in ("run", "remote")}
            with patch.dict(sys.modules, modules, clear=True), patch.object(native, "lower_games"), \
                    patch.object(native, "yield_to_evaluations") as yielding, \
                    patch.object(sys, "path", list(sys.path)), patch.object(sys, "argv", list(sys.argv)), \
                    patch.dict(os.environ, {"BC_YIELD": "", "BC_REMOTE_STATUS": "", "BC_BESIDE_LANE": "",
                                            native.CODER_CLONE: str(clone)}):
                cwd = os.getcwd()
                os.chdir(root)
                try:
                    native.play_script(str(script), ["--cand", "c", "--workers", "2"])
                    status = sys.modules["remote"].STATUS
                finally:
                    os.chdir(cwd)
            self.assertEqual((root / "seen").read_text(), "--cand c")
            self.assertEqual(yielding.call_args.kwargs, {"lane": 3})
            self.assertEqual(status, native.coder_status("E9996"))

    def test_filler_games_leave_a_tune_asking_for_slots_half_of_each_host(self) -> None:
        # E0105's tune kept 8-11 games in flight while mm, mm1, sz and eight az-* hosts sat idle; the sweep and the
        # prewarm may now play beside it, but together they take a quarter of a host each (a tune's share is half)
        # while it asks for slots, so one playing alone takes those two quarters, and half this Mac; with no tune, or
        # between a tune's rounds (E0180's, 20:47-21:03: its half idle for one to two minutes a round while the
        # fillers stayed at the other half), it takes the whole host, and this Mac's usual share.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 10)
        for flag, tuning, asking, host_share, mac_share in (
                ("filler", True, True, 2 * (10 // 4), native.CODER_FRACTION),
                ("filler", True, False, 10, 0.7), ("filler", False, False, 10, 0.7),
                ("tune", True, True, 5, 0.7)):
            with self.subTest(flag=flag, tuning=tuning, asking=asking), \
                    patch.object(remote.Host, "capacity", lambda host: 10), \
                    patch.object(remote.Host, "measure", remote.Host.measure), \
                    patch.object(remote.Host, "room", None, create=True), \
                    patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                    patch.object(native, "hosts_wanted_now", return_value=False), \
                    patch.object(native, "evaluating_now", return_value=False), \
                    patch.object(native, "tuning_now", return_value=tuning), \
                    patch.object(native, "tune_asking_now", return_value=asking), \
                    patch.object(native, "screen_asking_now", return_value=False):
                native.yield_to_evaluations(remote, flag)
                self.assertEqual(remote.Host.capacity(host), host_share)
                self.assertEqual(native.load_fraction(), mac_share)

    def test_a_tune_asks_for_slots_while_its_scheduler_looks_for_room_and_not_between_rounds(self) -> None:
        # A tune player marks its registration file whenever it looks for room (game_slots, a host's capacity or
        # measure); the fillers read a mark under ASKING_S old as asking. A mark from an earlier round has expired.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 10)
        with tempfile.TemporaryDirectory() as tunes, patch.object(native, "TUNE_PLAYERS", Path(tunes)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_role", ""), \
                patch.object(native, "_coder", False), patch.object(native, "_marked_at", -1e9), \
                patch.object(remote.Host, "capacity", lambda host: 3), \
                patch.object(remote.Host, "measure", lambda host: 3), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "hosts_wanted_now", return_value=False):
            native.yield_to_evaluations(remote, "tune")
            mine = Path(tunes) / native._filler_lock[1]
            for ask in (lambda: remote.Host.capacity(host), lambda: remote.Host.measure(host),
                        lambda: native.game_slots("auto")):
                with self.subTest(ask=ask):
                    old = time.time() - native.ASKING_S - 1
                    os.utime(mine, (old, old))
                    native._asking_seen, native._marked_at = (-1e9, False), -1e9
                    self.assertFalse(native.tune_asking_now())  # between rounds: nothing asked lately
                    ask()
                    native._asking_seen = (-1e9, False)
                    self.assertTrue(native.tune_asking_now())
            native._filler_lock[0].close()

    def test_idle_fillers_leave_a_late_screen_asking_for_slots_its_half_and_take_its_tails(self) -> None:
        # 29 Sep 18:42-19:11: v0070's late screen held 2-36 of 72 slots (its half, less in its tails) through E0349's
        # stages on this Mac while the sweep and the adversarial-map search waited for it to finish. Now they play
        # beside it: a screen filler marks its file when its scheduler looks for room, the idle fillers keep to the
        # other half while that mark is fresh, and take the whole host once it has expired.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 10)
        with tempfile.TemporaryDirectory() as fillers, patch.object(native, "FILLERS", Path(fillers)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_role", ""), \
                patch.object(native, "_coder", False), patch.object(native, "_marked_at", -1e9), \
                patch.object(native, "_screen_asking_seen", (-1e9, False)), \
                patch.object(remote.Host, "capacity", lambda host: 10), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "hosts_wanted_now", return_value=False), \
                patch.object(native, "evaluating_now", return_value=False), \
                patch.object(native, "tune_asking_now", return_value=False):
            native.yield_to_evaluations(remote, "1")  # the late screen
            mine = Path(fillers) / native._filler_lock[1]
            old = time.time() - native.ASKING_S - 1
            os.utime(mine, (old, old))
            self.assertFalse(native.screen_asking_now())  # between entries: nothing asked lately
            self.assertEqual(remote.Host.capacity(host), 5)  # its half, and asking from now
            native._screen_asking_seen = (-1e9, False)
            self.assertTrue(native.screen_asking_now())
            with patch.object(native, "_role", "idle"):  # the sweep's share of the same host
                self.assertEqual(remote.Host.capacity(host), 2 * (10 // 4))
                self.assertEqual(native.load_fraction(0.7), native.CODER_FRACTION)
                os.utime(mine, (old, old))  # the late screen's tail: it asks for nothing
                native._screen_asking_seen = (-1e9, False)
                self.assertEqual(remote.Host.capacity(host), 10)
            native._filler_lock[0].close()

    def test_the_spare_share_takes_what_the_idle_fillers_leave_and_gives_way_when_one_asks(self) -> None:
        # 2026-09-28 01:20-01:29: the adversarial-map search waited for the prewarm to finish while the prewarm alone
        # kept about 20 of 56 slots busy. Past its first cap the search plays at the spare share: unregistered, so a
        # filler at the usual share keeps its whole share, it has what their games leave of a host, and nothing while
        # one of them asks for slots, which one does whenever its scheduler looks for room.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 10)
        host.cores = 10
        now = time.time()
        for name, active in (("remote-status-prewarm.json", {"mm": 4}), ("remote-status-advmaps.json", {"mm": 3})):
            (native.driver.STATE / name).write_text(json.dumps({"updated": now, "active": active}))
            self.addCleanup((native.driver.STATE / name).unlink, missing_ok=True)
        with tempfile.TemporaryDirectory() as idle, patch.object(native, "IDLE_FILLERS", Path(idle)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_role", ""), \
                patch.object(native, "_spare", False), patch.object(native, "_coder", False), \
                patch.object(native, "_marked_at", -1e9), \
                patch.object(remote, "STATUS", native.driver.STATE / "remote-status-advmaps.json"), \
                patch.object(remote.Host, "capacity", lambda host: 10), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "hosts_wanted_now", return_value=False), \
                patch.object(native, "evaluating_now", return_value=False), \
                patch.object(native, "screen_asking_now", return_value=False), \
                patch.object(native, "tuning_now", return_value=False), \
                patch.object(native, "tune_asking_now", return_value=False):
            native.yield_to_evaluations(remote, "spare")
            self.assertEqual((list(Path(idle).iterdir()), native._filler_lock), ([], None))  # not registered
            native._fillers_asking_seen, native._others_seen = (-1e9, False), (-1e9, {})
            self.assertEqual(remote.Host.capacity(host), 10 - 4)  # the prewarm's 4 there; its own 3 aren't counted
            other = Path(idle) / "1-99"  # a live filler at the usual share (its lock held), asking lately
            with other.open("w") as handle:
                native.fcntl.flock(handle, native.fcntl.LOCK_EX)
                native._fillers_asking_seen = (-1e9, False)
                self.assertEqual(remote.Host.capacity(host), 0)
                self.assertEqual(native.game_slots("auto"), 0)  # nor on this Mac
                old = time.time() - native.ASKING_S - 1
                os.utime(other, (old, old))  # its last ask expired: in its tail, or between runs
                native._fillers_asking_seen = (-1e9, False)
                self.assertEqual(remote.Host.capacity(host), 6)
        with tempfile.TemporaryDirectory() as idle, patch.object(native, "IDLE_FILLERS", Path(idle)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_role", ""), \
                patch.object(native, "_spare", False), patch.object(native, "_coder", False), \
                patch.object(native, "_marked_at", -1e9), \
                patch.object(remote.Host, "capacity", lambda host: 3), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "hosts_wanted_now", return_value=False), \
                patch.object(native, "tune_asking_now", return_value=False):
            native.yield_to_evaluations(remote, "filler")  # the prewarm: registered, and asking when it looks for room
            mine = Path(idle) / native._filler_lock[1]
            old = time.time() - native.ASKING_S - 1
            os.utime(mine, (old, old))
            native._fillers_asking_seen = (-1e9, False)
            self.assertFalse(native.fillers_asking_now())
            self.assertEqual(remote.Host.capacity(host), 3)  # its whole share: no spare filler splits it
            native._fillers_asking_seen = (-1e9, False)
            self.assertTrue(native.fillers_asking_now())
            native._filler_lock[0].close()

    def test_a_tunes_round_counts_the_idle_fillers_games_as_its_room(self) -> None:
        # Between rounds the fillers fill the tune's half; sized by its capacity alone the next round would see those
        # hosts full and play its smallest round. Its room is its capacity plus their games there, up to its share.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 20)
        host.cores = 10
        with tempfile.TemporaryDirectory() as tunes, patch.object(native, "TUNE_PLAYERS", Path(tunes)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_role", ""), \
                patch.object(native, "_coder", False), \
                patch.object(remote.Host, "capacity", lambda host: 1), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "hosts_wanted_now", return_value=False):
            now = time.time()
            for name, active, updated in (("remote-status-sweep.json", {"mm": 3, "ll": 9}, now),
                                          ("remote-status-prewarm.json", {"mm": 2}, now),
                                          ("remote-status-advmaps.json", {"mm": 7}, now - native.IDLE_STATUS_S - 1)):
                (native.driver.STATE / name).write_text(json.dumps({"updated": updated, "active": active}))
                self.addCleanup((native.driver.STATE / name).unlink, missing_ok=True)
            self.assertEqual(native.idle_games(), {"mm": 5, "ll": 9})  # the stale search's file is left out
            native.yield_to_evaluations(remote, "tune")
            self.assertEqual(remote.Host.capacity(host), 1)  # what it may start now
            self.assertEqual(remote.Host.room(host), 5)  # 1 free + 5 fillers' games, at most its half of 10
            (native.driver.STATE / "remote-status-sweep.json").write_text(json.dumps({"updated": now, "active": {}}))
            self.assertEqual(remote.Host.room(host), 3)
            native._filler_lock[0].close()

    def test_during_an_evaluation_a_tune_keeps_its_share_and_idle_fillers_play_nothing(self) -> None:
        # The review: one split filler share took a tune from ~34 to ~16 games a minute, and to ~2 once an
        # official-map report was live too. A tune is a claimed experiment, not a filler: the screen fillers don't
        # split its share (test_a_tune_beside_a_live_official_job_keeps_its_share); only the evidence refresh does.
        one = native.YIELDING * 2
        # 8, 10: the most it may have, a game where its slot of the rationed half comes up
        # (test_the_share_takes_its_turn_in_the_ration_of_half_a_host)
        for cores, tune in ((2, 1), (8, 1), (10, 1), (16, 1)):
            with self.subTest(cores=cores):
                self.assertEqual(native.host_share(cores, True, "tune"), tune)
                self.assertEqual(tune, max(1, cores // one))
                self.assertEqual(native.host_share(cores, True, "idle"), 0)  # the sweep and the prewarm wait
                self.assertEqual(native.host_share(cores, True, "screen", tuning=True), 0)  # the tune has the share
        with patch.object(native, "_role", "idle"), patch.object(native, "evaluating_now", return_value=True), \
                patch.object(native, "hosts_wanted_now", return_value=False):
            self.assertEqual(native.game_slots("auto"), 0)  # nor on this Mac, even in a stage that plays only here

    def test_idle_fillers_split_what_two_had_so_a_tune_keeps_its_half(self) -> None:
        # The review of loop/advmaps.py: a third idle filler at cores // 4 each took 6 of an 8-core host beside the
        # tune's 4. Together they now keep what the sweep and the prewarm had, however many play: one left alone
        # takes it all (capped at one's share it left half of every host idle once the other two had finished).
        for cores in (2, 8, 10):  # 2: the az-* hosts, where a floor of one game each overfilled the host beside a tune
            tune = native.host_share(cores, False, "tune")
            for tuning in (True, False):
                alone = native.host_share(cores, False, "idle", tuning)  # what the sweep and the prewarm had together
                self.assertEqual(alone, max(1, cores // (4 if tuning else 2) * 2))
                for count in (1, 2, 3, 4):
                    with self.subTest(cores=cores, tuning=tuning, count=count):
                        parts = [native.host_share(cores, False, "idle", tuning, rank, count, offset=7)
                                 for rank in range(count)]
                        self.assertEqual(sum(parts), alone)
                        if tuning:
                            self.assertLessEqual(sum(parts), cores - tune)  # 2 cores: 1 beside 1; 8: 4 beside 4
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 8)
        host.cores = 8
        with tempfile.TemporaryDirectory() as directory, patch.object(native, "IDLE_FILLERS", Path(directory)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                patch.object(remote.Host, "capacity", lambda host: 8), patch.object(native, "_role", ""), \
                patch.object(native, "hosts_wanted_now", return_value=False), \
                patch.object(native, "tuning_now", return_value=True), \
                patch.object(native, "tune_asking_now", return_value=True):
            for _ in range(2):  # the sweep and the prewarm playing, registered in `directory` as IDLE_FILLERS here
                filler(self, directory)
            native.yield_to_evaluations(remote, "filler")  # the search joins them
            self.assertEqual(native.filler_rank(), (2, 3))
            self.assertEqual(len(list(Path(directory).iterdir())), 3)
            offset = native.host_offset("mm", native.host_runs(remote.load_hosts()), native.YIELDING)
            self.assertEqual(remote.Host.capacity(host), native.filler_part(4, 2, 3, offset))
            native._filler_lock[0].close()

    def test_an_idle_filler_between_its_calls_leaves_its_part_to_the_others(self) -> None:
        # 30 Sep 02:54-03:02: the prewarm held 36 of 72 slots while the sweep, registered but between its calls' games
        # (a call's setup and last games), alternated about 34 and 2 of the other 36. The idle fillers split a host
        # only among those that asked for slots within native.ASKING_S, and one that asks again gets its part back.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm", 10)
        host.cores = 10
        with tempfile.TemporaryDirectory() as directory, patch.object(native, "IDLE_FILLERS", Path(directory)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                patch.object(remote.Host, "capacity", lambda host: 10), patch.object(native, "_role", ""), \
                patch.object(native, "hosts_wanted_now", return_value=False), \
                patch.object(native, "half_asking_now", return_value=False):
            filler(self, directory)  # the sweep, registered first
            quiet = next(Path(directory).iterdir())
            os.utime(quiet, (time.time() - native.ASKING_S - 1,) * 2)  # it asked for nothing lately
            native.yield_to_evaluations(remote, "filler")  # the prewarm, asking
            self.assertEqual(native.filler_rank(), (0, 1))
            self.assertEqual(remote.Host.capacity(host), 10)  # the whole host, not half
            os.utime(quiet)  # the sweep's next call asks
            native._fillers_seen = (0.0, 0, (0, 1))  # recounted at once (every 10 s at most)
            self.assertEqual(native.filler_rank(), (1, 2))
            self.assertEqual(remote.Host.capacity(host), 5)
            native._filler_lock[0].close()

    def test_a_tune_beside_a_live_official_job_keeps_its_share(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("zz", 32)
        host.cores = 16
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as tunes, \
                patch.object(native, "FILLERS", Path(directory)), patch.object(native, "TUNE_PLAYERS", Path(tunes)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                patch.object(native, "_coder", False), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(remote.Host, "capacity", lambda host: 32), patch.object(native, "_role", ""), \
                patch.object(native, "hosts_wanted_now", return_value=True), \
                patch.object(native, "tuning_now", return_value=True):
            filler(self, directory)  # a live official-map report
            self.assertEqual(len(native.filler_names()), 1)
            native.yield_to_evaluations(remote, "tune")
            self.assertEqual(remote.Host.capacity(host), 16 // (2 * native.YIELDING))
            native._filler_lock[0].close()

    def test_the_evidence_refresh_joins_a_tunes_share_and_the_screen_fillers_step_aside(self) -> None:
        # With a share of its own the refresh after every promotion was a sixth player on mm and mm1 (10 cores)
        # beside the coders, the review and a tune, and the official-map report started with it kept its share too:
        # a screen filler's `tuning` read only the backlog's tunes (MISSES 50).
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        slots = native.share_slot(None)[1]  # no marker's lane here: a slot for every lane
        cores = 2 * slots  # the fewest cores giving each player beside an evaluation a game of its own
        host = remote.Host("zz", 2 * cores)
        host.cores = cores
        for flag in ("tune", "1"):  # a tune joining the refresh, then an official-map report beside it
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tunes, \
                    tempfile.TemporaryDirectory() as fillers, patch.object(native, "TUNE_PLAYERS", Path(tunes)), \
                    patch.object(native, "FILLERS", Path(fillers)), patch.object(native, "_filler_lock", None), \
                    patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                    patch.object(native, "_tuning_seen", (0.0, False)), patch.object(native, "_coder", False), \
                    patch.object(native, "_asking_seen", (-1e9, False)), \
                    patch.object(remote.Host, "capacity", lambda host: cores), patch.object(native, "_role", ""), \
                    patch.object(remote.Host, "measure", remote.Host.measure), \
                    patch.object(remote.Host, "room", None, create=True), \
                    patch.object(native, "hosts_wanted_now", return_value=True), \
                    patch.object(native.driver, "read_ideas", return_value=[]):  # no tune claimed
                filler(self, tunes)  # the refresh, playing at a tune's share
                native.yield_to_evaluations(remote, flag)
                if flag == "tune":
                    self.assertEqual(len(native.filler_names(Path(tunes))), 2)
                    self.assertEqual(native.filler_rank(), (1, 2))
                    offset = native.host_offset("zz", native.host_runs(remote.load_hosts()), slots)
                    self.assertEqual(remote.Host.capacity(host), native.filler_part(1, 1, 2, offset))
                else:
                    self.assertTrue(native.tuning_now())
                    self.assertEqual(remote.Host.capacity(host), 0)
                native._filler_lock[0].close()

    def test_beside_a_host_stage_the_players_take_at_most_half_of_every_host_of_8_or_more_cores(self) -> None:
        # CLAUDE.md: what plays beside an evaluation gets at most half of each 8+-core host. The coders and the review
        # (CODING + 1 players at role ""), a tune and the evidence refresh (splitting one share) and an official-map
        # report all playing: mm and mm1 had 6 of 10 with the refresh's own share, and sz-wsl (8 cores, where
        # cores // 10 is 0) had 5 of 8 from each player's floor of one game, even without it (MISSES 50).
        fleet = {**FLEET, "nine": 9}
        got = [0, 0]  # the tune's and the refresh's games, summed over the fleet
        for name, cores in fleet.items():
            share = native.share_slot(1)
            offset = native.host_offset(name, native.host_runs(fleet_hosts(fleet)), share[1])
            coders = sum(coder_games(cores, offset, holder=1))
            tunes = [native.host_share(cores, True, "tune", rank=rank, count=2, offset=offset, slot=share[0],
                                       slots=share[1]) for rank in range(2)]
            report = native.host_share(cores, True, "screen", tuning=True, offset=offset, slot=share[0],
                                       slots=share[1])
            with self.subTest(host=name):
                self.assertEqual(native.host_share(cores, True), 1)  # the most one coder may have (health.py's check)
                self.assertEqual(report, 0)  # the tune players have the share
                # With no tune player the report has that share, except on a 2-core VM, which stays the evaluation's.
                alone = native.host_share(cores, True, "screen", offset=offset, slot=share[0], slots=share[1])
                self.assertEqual(alone, sum(tunes) if cores >= 8 else 0)
                if cores >= 8:
                    self.assertLessEqual(coders + sum(tunes) + report, cores // 2)
                    self.assertLessEqual(coders, native.coders_share(cores))
                else:  # the 2-core az-* VMs keep their floor: a game each, one for the tune players together
                    self.assertEqual((coders, sum(tunes)), (native.driver.CODING + 1, 1))
            got = [got[0] + tunes[0], got[1] + tunes[1]]
        self.assertTrue(all(got), got)  # the host offsets hand each of them the share somewhere: neither starves

    def test_the_share_takes_its_turn_in_the_ration_of_half_a_host(self) -> None:
        # With five coders the half of every 8- to 13-core host went to the coders and the review first, which left
        # the tune and screen share none on mm, mm1, ll and sz-wsl (every live big host on 2026-09-27; zz-wsl is
        # part-time), so the late screen and official-map reports played nothing there beside an evaluation. As the
        # last of the YIELDING slots it plays at 5 of the YIELDING offsets of a 10-core host, and the half stays exact.
        for cores in range(8, 17):
            for holder in (3, None):
                shares = []
                for offset in range(8):
                    coders = sum(coder_games(cores, offset, holder))
                    slot, slots = native.share_slot(holder)
                    shares.append(native.host_share(cores, True, "tune", offset=offset, slot=slot, slots=slots))
                    with self.subTest(cores=cores, holder=holder, offset=offset):
                        self.assertLessEqual(coders + shares[-1], cores // 2)
                        if holder is not None:  # every slot filled, the half given out exactly
                            self.assertEqual(coders + shares[-1], min(cores // 2, native.YIELDING))
                with self.subTest(cores=cores, holder=holder):
                    self.assertTrue(any(shares), shares)
        self.assertEqual(sum(native.host_share(10, True, "tune", offset=offset, slot=native.YIELDING - 1)
                             for offset in range(native.YIELDING)), 5)
        self.assertEqual(native.host_share(10, True, "tune"), 1)  # the most it may have (health.py's check)
        self.assertEqual(native.coders_share(10), 5)
        self.assertEqual(native.coders_share(12), min(native.driver.CODING + 1, 6))
        self.assertEqual(native.coders_share(16), min(native.driver.CODING + 1, 8))

    def test_beside_an_evaluation_the_share_goes_to_a_playing_tune_even_between_its_rounds(self) -> None:
        # Between a tune's rounds it asks for no slots (tune_asking_now false), and the fillers take its half of each
        # host. Beside an evaluation that is the one share of the rationed half: a screen filler taking it too put both
        # on it at once, above the half. There the screen fillers step back for any tune playing (tuning_now).
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("zz", 32)
        host.cores = 16
        with tempfile.TemporaryDirectory() as fillers, patch.object(native, "FILLERS", Path(fillers)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                patch.object(remote.Host, "capacity", lambda host: 16), \
                patch.object(native, "tuning_now", return_value=True), \
                patch.object(native, "tune_asking_now", return_value=False), \
                patch.object(native, "holder_lane", return_value=2):
            native.yield_to_evaluations(remote, "1")  # an official-map report
            for evaluating, expected in ((True, 0), (False, native.host_share(16, False, "screen"))):
                with self.subTest(evaluating=evaluating), patch.object(native, "hosts_wanted_now",
                                                                       return_value=evaluating):
                    self.assertEqual(remote.Host.capacity(host), expected)
            native._filler_lock[0].close()

    def test_a_tunes_room_beside_an_evaluation_is_its_turn_in_the_rationed_half(self) -> None:
        # B's handoff (the idle fillers fill a tune's half between its rounds, and its room counts their games back)
        # meets five-coders' ration: beside an evaluation the idle fillers play nothing on the hosts, and a tune's
        # room is its rationed turn in the half (share_slot), never the half a host it has without an evaluation.
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        with tempfile.TemporaryDirectory() as tunes, patch.object(native, "TUNE_PLAYERS", Path(tunes)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))), \
                patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                patch.object(remote.Host, "capacity", lambda host: 10), \
                patch.object(remote.Host, "measure", remote.Host.measure), \
                patch.object(remote.Host, "room", None, create=True), \
                patch.object(native, "idle_games", return_value={f"h{i}": 5 for i in range(8)}), \
                patch.object(native, "holder_lane", return_value=1), \
                patch.object(native, "hosts_wanted_now", return_value=True):
            native.yield_to_evaluations(remote, "tune")
            slot, slots = native.share_slot(1)
            for i in range(8):
                host = remote.Host(f"h{i}", 20)
                host.cores = 10
                offset = native.host_offset(host.name, native.host_runs(remote.load_hosts()), slots)
                with self.subTest(host=host.name):
                    rationed = native.host_share(10, True, "tune", offset=offset, slot=slot, slots=slots)
                    self.assertEqual(remote.Host.room(host), rationed)
                    self.assertLessEqual(rationed, 1)
                    self.assertEqual(native.host_share(10, True, "idle", tuning=False), 0)
            native._filler_lock[0].close()

    def test_the_coders_and_the_review_ration_half_a_host_by_lane(self) -> None:
        # sig, 2026-09-27: "ok lets do 5 coding (6 in flight)". At a game each the six players beside an evaluation
        # (five coders, the review) took 6 of a 10-core host and 6 of 8 on sz-wsl; at cores // 14 they took none.
        # Rationed by lane with the share, 5 of the YIELDING play on 10 cores, and every coder plays on some always-on
        # host (mm, mm1, ll, sz-wsl), whichever lane is under test. With seven (sig, 2026-09-29) a crc32 of each
        # host's name as its offset left one lane none on all four; host_offset lays their runs end to end.
        for coding in sorted({5, native.driver.CODING}):
            with patch.object(native.driver, "CODING", coding), patch.object(native.driver, "IN_FLIGHT", coding + 1), \
                    patch.object(native, "YIELDING", coding + 2):
                self.lanes_share_half_of_every_host(coding)
        self.assertEqual(native.coder_slot(4, 2), (2, native.YIELDING))  # lanes 1, 3, 4: third
        self.assertEqual(native.coder_slot(None, 2), (native.driver.CODING, native.YIELDING))  # the review
        self.assertEqual(native.share_slot(2), (native.YIELDING - 1, native.YIELDING))  # the share, last
        self.assertEqual(native.coder_slot(4, None), (3, native.YIELDING + 1))

    def lanes_share_half_of_every_host(self, coding: int) -> None:
        hosts = fleet_hosts()
        for holder in (*range(1, coding + 2), None):
            lanes = [lane for lane in range(1, coding + 2) if lane != holder][:coding]
            played = dict.fromkeys([*lanes, None], 0)  # games per lane on the always-on big hosts; None the review's
            for host in hosts:
                cores, (slot, slots) = host.max // 2, native.share_slot(holder)
                offset = native.host_offset(host.name, native.host_runs(hosts), slots)
                parts = dict(zip([*lanes, None], coder_games(cores, offset, holder, lanes)))
                share = native.host_share(cores, True, "tune", offset=offset, slot=slot, slots=slots)
                with self.subTest(coding=coding, holder=holder, host=host.name):
                    if cores >= 8:
                        self.assertLessEqual(sum(parts.values()) + share, cores // 2)  # all CODING + 1 playing
                    else:
                        self.assertEqual(set(parts.values()), {1})  # the floor of a game each
                    if cores == 10 and holder is not None:
                        self.assertEqual(sum(parts.values()) + share, 5)
                if cores >= 8 and not host.part_time:
                    for lane, n in parts.items():
                        played[lane] += n
            with self.subTest(coding=coding, holder=holder):
                self.assertTrue(all(played.values()), played)

    def test_a_post_mortem_coder_plays_no_rationed_game_beside_the_new_coder_on_its_old_lane(self) -> None:
        # run_experiment frees the lane before the post-mortem or follow-on, and the next claim often takes it: from
        # the same lane both coders had one slot, so a 10-core host gave six games of its half of five
        # (utilisation.jsonl 2026-09-27: post-mortem coders of 9 experiments played 74 of 876 coder host-rows after
        # their result, 66 beside an evaluation). Its clone says lane 0 once the result is recorded
        # (driver.retire_lane): none on 8+ cores.
        lanes = list(range(2, native.driver.CODING + 2))  # lane 1 under test, lane 3 claimed again while E0190's
        # post-mortem plays
        hosts = fleet_hosts()
        for host in hosts:
            cores, (slot, slots) = host.max // 2, native.coder_slot(0, 1)
            offset = native.host_offset(host.name, native.host_runs(hosts), slots)
            pm = native.host_share(cores, True, offset=offset, slot=slot, slots=slots)
            with self.subTest(host=host.name):
                self.assertEqual(pm, 1 if cores < 8 else 0)
                if cores >= 8:
                    self.assertLessEqual(sum(coder_games(cores, offset, 1, lanes)) + pm, cores // 2)
                # With no evaluation on the hosts, a coder's usual share: alone there, the whole host.
                self.assertEqual(native.host_share(cores, False, slot=native.NO_SLOT), cores)

    def test_a_coders_games_take_their_lanes_slot_beside_the_candidate_the_marker_names(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("sz-wsl", 8)
        host.cores = 8
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "E0200"
            (clone / ".git/info").mkdir(parents=True)
            self.assertEqual(native.coder_lane(clone), 200 % native.driver.IN_FLIGHT + 1)  # a clone from before
            (clone / native.driver.LANE_FILE).write_text("5\n")
            self.assertEqual(native.coder_lane(clone), 5)
        for holder, marker in ((2, {"pid": os.getpid(), "since": time.time(), "hosts": True, "lane": 2}),
                               (None, {"pid": os.getpid(), "since": time.time(), "hosts": True})):
            for lane in (5, 0):
                with self.subTest(holder=holder, lane=lane), patch.object(remote.Host, "capacity", lambda host: 8), \
                        patch.object(native, "_evaluating_seen", (0.0, False, False, None, None)), \
                        patch.object(native, "live_marker", return_value=marker):
                    native.yield_to_evaluations(remote, lane=lane)
                    self.assertEqual(native.holder_lane(), holder)
                    slot, slots = native.coder_slot(lane, holder)
                    offset = native.host_offset("sz-wsl", native.host_runs(remote.load_hosts()), slots)
                    rationed = native.filler_part(4, slot, slots, offset)
                    self.assertEqual(remote.Host.capacity(host), 0 if lane == 0 else rationed)
                    self.assertEqual(slots, native.YIELDING + (holder is None))

    # 29 Sep: the CPU gates play on the hosts of this Mac's platform too (the marker's MAC_HOSTS), so the players beside
    # step back on those hosts alone; and a queued candidate's prepared gate, the next evaluation's own, takes its
    # lane's coder share of a host beside the SPRT (a game at most on a 10-core Mac), not all its room.
    def test_a_cpu_gate_stage_holds_back_only_this_platforms_hosts_and_a_prepared_gate_its_lanes_share(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        mac, linux = remote.Host("mm", 20), remote.Host("ll", 24)
        mac._platform, linux._platform, mac.cores = remote.PLATFORM, "Linux x86_64", 10
        gate = {"pid": os.getpid(), "since": time.time(), "hosts": native.MAC_HOSTS, "lane": 2}
        with patch.object(native, "_evaluating_seen", (0.0, False, False, None, None)), \
                patch.object(native, "live_marker", return_value=gate):
            self.assertEqual([native.hosts_wanted_now(h) for h in (mac, linux, None)], [True, False, False])
        sprt = {**gate, "hosts": True}
        with patch.object(native, "_evaluating_seen", (0.0, False, False, None, None)), \
                patch.object(native, "live_marker", return_value=sprt), \
                patch.object(remote.Host, "capacity", lambda host: 10):
            native.ration_hosts(remote, 4)
            slot, slots = native.coder_slot(4, 2)
            self.assertEqual(remote.Host.capacity(mac),
                             native.host_share(10, True, slot=slot, slots=slots, offset=native.host_offset(
                                 "mm", native.host_runs(remote.load_hosts()), slots)))
            self.assertLessEqual(remote.Host.capacity(mac), 1)

    # Seven coders' review (29 Sep): the offsets depend on the whole host list, which a coder reads from
    # $BC_REMOTE_HOSTS as its lane started while a tune, the late screen or a prepared CPU gate re-reads remote.json,
    # so an edit (mm's max 20 -> 24) laid their runs out differently for hours. Every player beside an evaluation takes
    # the list from its marker ("runs", driver.evaluation_lock); an older marker without one, its own.
    def test_every_player_beside_an_evaluation_rations_from_the_markers_host_list(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        host = remote.Host("mm1", 20)
        host.cores = 10
        lanes, edited, wanted = range(2, native.driver.IN_FLIGHT + 1), {**FLEET, "mm": 12}, []
        for fleet, runs in ((FLEET, {}), (edited, {"runs": native.host_runs(fleet_hosts(edited))})):
            marker = {"pid": os.getpid(), "since": time.time(), "hosts": True, "lane": 1, **runs}
            shares = [native.host_share(10, True, slot=slot, slots=slots, offset=native.host_offset(
                "mm1", native.host_runs(fleet_hosts(fleet)), slots))
                for slot, slots in (native.coder_slot(lane, 1) for lane in lanes)]
            wanted.append(shares)
            with self.subTest(runs=bool(runs)), patch.object(remote, "_known", {}), \
                    patch.dict(os.environ, {"BC_REMOTE_HOSTS": fleet_json()}), \
                    patch.object(native, "_evaluating_seen", (0.0, False, False, None, None)), \
                    patch.object(native, "live_marker", return_value=marker), patch.object(native, "_coder", False), \
                    patch.object(native, "_role", ""), patch.object(native, "_lane", None), \
                    patch.object(native, "_spare", False):
                for lane, share in zip(lanes, shares):
                    for install in (lambda: native.yield_to_evaluations(remote, lane=lane),  # a coder's games
                                    lambda: native.ration_hosts(remote, lane)):  # a prepared CPU gate's
                        with patch.object(remote.Host, "capacity", lambda host: 20):
                            install()
                            self.assertEqual(remote.Host.capacity(host), share, lane)
        self.assertNotEqual(*wanted)  # the edit moves mm1's run: the lists would disagree
        self.assertEqual(native.host_offset("mm1", native.host_runs(fleet_hosts()), native.YIELDING), 5)

    # sig 29 Sep: with no evaluation each coder had max(1, cores // CODING) of a host, so at seven coders a 10-core
    # host gave each 1 game (2 of 10 with two playing) where five coders had 2. The coders playing there (their status
    # files: a sandboxed coder's in its clone, a native one's in the loop home) split it, a game at least each.
    def test_with_no_evaluation_the_coders_playing_on_a_host_split_it(self) -> None:
        sys.path.insert(0, str(native.driver.REPO / "referee"))
        import remote
        hosts = {name: remote.Host(name, 2 * cores) for name, cores in (("mm", 10), ("ll", 12), ("az-0", 2))}
        for host in hosts.values():
            host.cores = host.max // 2
        for coding in (5, 7):
            for playing in (2, 5, 7):
                with self.subTest(coding=coding, playing=playing), tempfile.TemporaryDirectory() as directory, \
                        patch.object(native.driver, "CODING", coding), \
                        patch.object(native.driver, "IN_FLIGHT", coding + 1), \
                        patch.object(native, "YIELDING", coding + 2), \
                        patch.object(native.driver, "STATE", Path(directory) / "state"), \
                        patch.object(native.driver, "WORKTREES", Path(directory) / "clones"), \
                        patch.object(native, "_coder", False), patch.object(native, "_role", ""), \
                        patch.object(native, "_lane", None), patch.object(native, "_spare", False), \
                        patch.object(native, "hosts_wanted_now", return_value=False), \
                        patch.object(remote, "STATUS", remote.STATUS), patch.object(native, "_coders_seen", None):
                    own = [Path(directory) / (f"state/remote-status-E{n:04d}.json" if n % 2 else
                                              f"clones/E{n:04d}/.git/bc-remote-status.json") for n in range(playing)]
                    idle = [(Path(directory) / "clones/E0900/.git/bc-remote-status.json", 0),  # no game there
                            (Path(directory) / "state/remote-status-E0901.json", 1)]  # an hour old
                    for path, games in [*((path, 1) for path in own), *idle]:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps({"updated": time.time() - 3600 * ("E0901" in path.name),
                                                    "active": {"mm": games, "az-0": games, "ll": 0}}))
                    got = {name: [] for name in hosts}
                    for lane, path in enumerate(own, 1):
                        remote.STATUS = path
                        native._coders_seen = (-1e9, {})
                        with patch.object(remote.Host, "capacity", lambda host: 20):
                            native.yield_to_evaluations(remote, lane=lane)
                            for name, host in hosts.items():
                                got[name].append(remote.Host.capacity(host))
                    self.assertEqual(sum(got["mm"]), 10)  # the whole host between them
                    self.assertEqual(max(got["mm"]) - min(got["mm"]), 1 if 10 % playing else 0)
                    self.assertEqual(got["az-0"], [1] * playing)  # 2 cores: a game at least each
                    self.assertEqual(got["ll"], [12] * playing)  # none of the others there: each has it all

    def test_one_read_of_the_marker_gives_whether_it_plays_on_the_hosts_and_its_lane(self) -> None:
        # Three reads, each of the marker as it then was: at a switch between evaluations a process could take the
        # old holder's "hosts" with the new holder's "lane".
        old = {"pid": os.getpid(), "since": time.time(), "hosts": False, "lane": 2}
        new = {"pid": os.getpid(), "since": time.time(), "hosts": True, "lane": 4}
        with patch.object(native, "_evaluating_seen", (0.0, False, False, None, None)), \
                patch.object(native, "live_marker", side_effect=[old, new, new]) as read:
            seen = (native.evaluating_now(), native.hosts_wanted_now(), native.holder_lane())
            self.assertEqual(seen, (True, False, 2))
            self.assertEqual(read.call_count, 1)

    def test_a_coders_lane_file_is_read_without_following_a_link_or_waiting_on_a_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clone = Path(directory) / "E0203"
            (clone / ".git/info").mkdir(parents=True)
            target = Path(directory) / "elsewhere"
            target.write_text("4\n")
            (clone / native.driver.LANE_FILE).symlink_to(target)
            fallback = 203 % native.driver.IN_FLIGHT + 1
            self.assertEqual(native.coder_lane(clone), fallback)  # the link refused: the experiment number's lane
            (clone / native.driver.LANE_FILE).unlink()
            os.mkfifo(clone / native.driver.LANE_FILE)
            done = []
            reader = threading.Thread(target=lambda: done.append(native.coder_lane(clone)), daemon=True)
            reader.start()
            reader.join(5)
            self.assertEqual(done, [fallback])  # read_text would still be waiting for a writer

    def test_screen_fillers_hand_out_whole_hosts_round_robin(self) -> None:
        # The fleet's shares: mm, mm1, ll, zz, sz and eight 2-core az VMs. Split per host with no floor, three fillers
        # got 0 games everywhere; round-robin keeps the whole share in play.
        fleet = {"mm": 10, "mm1": 10, "ll": 12, "zz": 16, "sz": 8, **{f"az-{i}": 2 for i in range(8)}}
        share = {name: cores // (2 * native.YIELDING) for name, cores in fleet.items()}
        for count in (1, 2, 3, 5):
            with self.subTest(count=count):
                parts = [{name: native.filler_part(share[name], rank, count, offset)
                          for offset, name in enumerate(fleet)} for rank in range(count)]
                for name in fleet:  # each host's share exactly, never more
                    self.assertEqual(sum(part[name] for part in parts), share[name])
                self.assertEqual(sum(map(sum, (p.values() for p in parts))), sum(share.values()))
        self.assertEqual(sum(share.values()), sum(cores // (2 * native.YIELDING) for cores in fleet.values()))
        self.assertEqual([native.host_share(2, True, "screen", rank=r, count=3, offset=5) for r in range(3)],
                         [0, 0, 0])  # a 2-core VM stays the evaluation's

    def test_a_filler_is_live_while_it_holds_its_lock_and_dead_once_killed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(native, "FILLERS", Path(directory)), \
                patch.object(native, "_filler_lock", None), patch.object(native, "_fillers_seen", (0.0, 0, (0, 1))):
            children = [filler(self, directory) for _ in range(2)]
            native.register_filler("1")  # this process, registered last: the third of three
            self.assertEqual(native.filler_rank(), (2, 3))
            children[0].kill()  # SIGKILL: no atexit, but the lock goes with the process
            children[0].wait()
            native._fillers_seen = (0.0, 0, (0, 1))  # past the 10 s re-count
            self.assertEqual(native.filler_rank(), (1, 2))  # its lock could be taken: dead
            self.assertEqual(len(list(Path(directory).iterdir())), 2)  # and its file is gone
            filler(self, directory)  # one registering within the 10 s: FILLERS changed, so it counts at once
            self.assertEqual(native.filler_rank(), (1, 3))
            native._filler_lock[0].close()

    def test_a_tmp_file_left_by_a_filler_killed_mid_registering_is_removed(self) -> None:
        import fcntl
        with tempfile.TemporaryDirectory() as directory, patch.object(native, "FILLERS", Path(directory)), \
                patch.object(native, "_filler_lock", None):
            folder = Path(directory)
            old = time.time() - native.TMP_AGE - 5
            left, young, held = (folder / f"{name}.tmp" for name in ("1-11", "2-22", "3-33"))
            for path in (left, young, held):
                path.write_text("")
            for path in (left, held):
                os.utime(path, (old, old))
            with held.open("r+") as handle:  # a filler that is still registering holds its .tmp's lock
                fcntl.flock(handle, fcntl.LOCK_EX)
                self.assertEqual(native.filler_names(), [])  # none of them is a live filler
                self.assertEqual(sorted(p.name for p in folder.iterdir()), [young.name, held.name])

    # 2026-09-27: test_native, test_sweep and test_driver runs ended with "ResourceWarning: unclosed file
    # .../fillers-idle/<id>.tmp": the pid file's handle, held for the process's life, was left to interpreter teardown.
    def test_a_filler_exiting_normally_closes_and_removes_its_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = REGISTER.replace("time.sleep(600)", "")
            done = subprocess.run([sys.executable, "-W", "default", "-c", script, directory, "1"], capture_output=True,
                                  text=True, timeout=60)
            self.assertEqual((done.returncode, done.stdout), (0, "registered\n"))
            self.assertNotIn("ResourceWarning", done.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_direct_runner_uses_the_gated_module(self) -> None:
        runner = types.SimpleNamespace(__file__=str(Path("referee/run.py").resolve()), game_slots=None, main=lambda: 0)
        with patch.dict(sys.modules, {"run": runner}), \
                patch.object(native.runpy, "run_path") as run_path, \
                patch.object(sys, "argv", []), patch.object(sys, "path", list(sys.path)):
            with self.assertRaises(SystemExit) as result:
                native.play_script("referee/run.py", ["--help"])
        self.assertEqual(result.exception.code, 0)
        self.assertIs(runner.game_slots, native.game_slots)
        run_path.assert_not_called()

    def test_an_explicit_count_is_honoured_while_load_and_memory_allow_and_none_start_otherwise(self) -> None:
        # (load, vm_stat text, slots): an explicit 6 above the load gate's auto slots is honoured; a full load or
        # short memory (the vm_stat parse through reclaimable_gb) starts none yet.
        for load, vm, expected in ((2, VM, 6), (10, VM, 0), (2, VM.replace("100000", "1000"), 0)):
            with self.subTest(load=load, short_memory=vm != VM), patch.object(native.os, "cpu_count", return_value=10), \
                    patch.object(native.os, "getloadavg", return_value=(load, 0, 0)), \
                    patch.object(native.subprocess, "run", return_value=CompletedProcess([], 0, vm, "")), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native.game_slots(6), expected)

    def test_stop_does_not_claim_an_idea(self) -> None:
        with patch.object(Path, "exists", return_value=True), \
                patch.object(native.driver, "claim_next") as claim, contextlib.redirect_stdout(io.StringIO()) as out:
            native.claim(1)
        claim.assert_not_called()
        self.assertIn("stopped", out.getvalue())

    def test_evaluate_refreshes_the_referee_holding_the_lock_before_the_candidate_is_checked(self) -> None:
        # As the Claude driver does: a referee/ change on main goes live before this test starts, never mid-test.
        steps = []

        @contextlib.contextmanager
        def lock(**_):
            steps.append("lock")
            yield
            steps.append("unlock")

        def evaluate(exp, worktree):
            steps.append("evaluate")
            exp.verdict, exp.reason = "discarded", "test"

        folder = native.driver.STATE / "runs" / "E9998"
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: (folder / "experiment.json").unlink(missing_ok=True))
        (folder / "experiment.json").write_text(json.dumps({"id": "E9998", "idea": {}, "lane": 1, "base": "base"}))
        with patch.object(native.driver, "recover_held", return_value=[]), \
                patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "evaluation_lock", side_effect=lock), \
                patch.object(native.driver, "refresh_referee", side_effect=lambda: steps.append("refresh") or True), \
                patch.object(native.driver, "guard_candidate", side_effect=lambda *_: steps.append("guard")), \
                patch.object(native.driver, "evaluate", side_effect=evaluate), \
                patch.object(native, "finish"):
            native.operate("evaluate", "E9998", "summary")
        self.assertEqual(steps, ["lock", "refresh", "guard", "evaluate", "unlock"])
        # A refresh whose restore failed too: the candidate isn't tested on a half-synced referee, nor charged.
        steps.clear()
        with patch.object(native.driver, "recover_held", return_value=[]), \
                patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "evaluation_lock", side_effect=lock), \
                patch.object(native.driver, "refresh_referee", return_value=False), \
                patch.object(native.driver, "evaluate", side_effect=evaluate), patch.object(native, "finish") as done:
            native.operate("evaluate", "E9998", "summary")
        self.assertEqual(steps, ["lock", "unlock"])
        self.assertEqual(done.call_args.args[0].verdict, "blocked")


class TestEvidenceAfterTheLock(unittest.TestCase):
    def test_a_kept_results_evidence_refresh_starts_once_the_lock_is_released(self) -> None:
        # E0167's native evaluate refreshed its evidence inside the lock, 15:52-16:02 on 2026-09-27, while E0168
        # waited and the hosts sat idle (MISSES 50).
        steps = []

        @contextlib.contextmanager
        def lock(**_):
            steps.append("lock")
            yield
            steps.append("unlock")

        def evaluate(exp, worktree):
            exp.verdict, exp.reason = "kept", "H1"

        folder = native.driver.STATE / "runs" / "E9997"
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: (folder / "experiment.json").unlink(missing_ok=True))
        (folder / "experiment.json").write_text(json.dumps({"id": "E9997", "idea": {}, "lane": 1, "base": "base"}))
        out = io.StringIO()
        with patch.object(native.driver, "recover_held", return_value=[]), \
                patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "evaluation_lock", side_effect=lock), \
                patch.object(native.driver, "refresh_referee", return_value=True), \
                patch.object(native.driver, "guard_candidate"), \
                patch.object(native.driver, "evaluate", side_effect=evaluate), patch.object(native.driver, "promote"), \
                patch.object(native, "finish", side_effect=lambda exp: steps.append("finish")), \
                patch.object(native.driver, "start_evidence",
                             side_effect=lambda exp: steps.append("evidence") or print("evidence refresh playing")), \
                contextlib.redirect_stdout(out):
            native.operate("evaluate", "E9997", "summary")
        self.assertEqual(steps, ["lock", "finish", "unlock", "evidence"])
        self.assertEqual(out.getvalue(), "")  # its log line goes to stderr: stdout stays finish's one JSON object


class TestFinishReport(unittest.TestCase):
    # native evaluate's report names a result's mixes as the post-mortem prompt does (driver.mix_fields): an opponent
    # mix without a map mix too.
    def test_the_report_names_an_opponent_mix_without_a_map_mix(self) -> None:
        entry = next(iter(native.driver.meta.MIX_OPPONENTS))
        # Frozen before the worldgen share, so it has no map mix (one claimed now has worldgen's).
        exp = native.driver.Experiment(id="E9996", idea={"id": "I1", "evaluation": {"opponents": {entry: 0.33}}},
                                       lane=1, verdict="discarded", reason="H0",
                                       evaluation={"elo1": 15, "opponents": {entry: 0.33}})
        out = io.StringIO()
        with patch.multiple(native.driver, record=Mock(), keep_wip=Mock(return_value=None), finish_idea=Mock(),
                            cleanup=Mock(), save_experiment=Mock(), start_official=Mock(),
                            prepare_postmortem=Mock(return_value=None), missing_checks=Mock(return_value=[])), \
                contextlib.redirect_stdout(out):
            native.finish(exp)
        report = json.loads(out.getvalue())
        self.assertEqual(report["opponent_mix"], {entry: 0.33})
        self.assertNotIn("map_mix", report)
        self.assertNotIn("per_style", report)


class TestFollowups(unittest.TestCase):
    def test_followups_saves_the_marker_prune_waits_for(self) -> None:
        # Native results not kept never wrote postmortem.json, so prune kept their losses forever.
        with tempfile.TemporaryDirectory() as folder, patch.object(native.driver, "STATE", Path(folder)), \
                patch.object(native.driver, "add_followups", return_value="E0150: post-mortem queued I401"):
            for eid, verdict, name in (("E0150", "discarded", "postmortem"), ("E0151", "kept", "followon")):
                exp = native.driver.Experiment(id=eid, idea={"id": "I1"}, lane=1, verdict=verdict)
                exp.run_dir.mkdir(parents=True)
                self.assertEqual(native.followups(exp), "E0150: post-mortem queued I401")
                row = {**native.driver.asdict(exp), "session": {"provider": "native-agent"},
                       "started": native.driver.prune.TURNS_FROM + 60, "sprt": {"decision": "H0"}}
                self.assertEqual(native.driver.prune.owed_turn(row), name)
                self.assertTrue((exp.run_dir / f"{name}.json").exists())


# E0163's experiment.json as native claim saved it at 13:55 on 2026-09-27, before native.py wrote worker.json.
E0163 = json.loads((Path(__file__).resolve().parent / "testdata/native-E0163-experiment.json").read_text())


class TestNativeWorker(unittest.TestCase):
    """A native lane's worker.json (native.worker_state) as the Claude driver's readers read it: from 14:00 to 14:10
    on 2026-09-27 evalline said "coding: nothing" while Astra coded E0163 and E0164, which wrote none, and a native
    candidate waiting for the evaluation lock was never "queued"."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        state = patch.object(native.driver, "STATE", self.state)
        state.start()
        self.addCleanup(state.stop)
        self.ideas = [{"id": E0163["idea"]["id"], "status": "trying:lane1"}]
        self.run = self.state / "runs" / "E0163"

    def worker(self) -> dict:
        return json.loads((self.run / "worker.json").read_text())

    def seen(self) -> dict:
        import evalline
        import throughput
        now = time.time()
        return {"coding": evalline.coding(self.state / "runs"), "coding_count": native.driver.coding_count(self.ideas),
                "waiting": throughput.waiting_now(self.state, now, None),
                "pipeline": evalline.pipeline(self.state, now, None)[:2]}

    def claim(self, idea: dict | None = None, claim_next=None) -> dict:
        session = {k: E0163["session"][k] for k in ("stack", "protected", "referee_commit", "protected_maps")}
        with patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver.meta, "prepare_review", return_value=None), \
                patch.object(native.driver.audit, "read", return_value={}), \
                patch.object(native.driver.audit, "prepare", return_value=None), \
                patch.object(native.driver, "claim_next", side_effect=claim_next,
                             return_value=idea or E0163["idea"]), \
                patch.object(native.driver, "next_experiment_id", return_value="E0163"), \
                patch.object(native.driver, "make_worktree",
                             side_effect=lambda exp: exp.run_dir.mkdir(parents=True) or Path("/clone")), \
                patch.object(native.driver, "protected_baseline", return_value=session), \
                patch.object(native, "start_watch"), patch.object(native.driver, "lane_prompt", return_value="task"), \
                patch.object(native.driver.online, "version_alert", return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            native.claim(1)
        return json.loads(out.getvalue())  # stdout stays one JSON object: the worker's log line went to stderr

    # Runs `evaluate` on the saved record, with the evaluation lock replaced by one that records what the readers see
    # while the candidate waits for it (and then calls `waiting`); the test's own evaluate ends with `verdict`.
    def evaluate(self, verdict: str = "discarded", waiting=None) -> list[dict]:
        seen = []

        @contextlib.contextmanager
        def lock(**_):
            seen.append(self.seen())
            if waiting:
                waiting()
            yield

        def evaluate(exp, worktree):
            exp.verdict, exp.reason = verdict, "test"

        with patch.object(native.driver, "recover_held", return_value=[]), \
                patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "evaluation_lock", side_effect=lock), \
                patch.object(native.driver, "experiment_runtime", side_effect=lambda exp: contextlib.nullcontext()), \
                patch.object(native.driver, "refresh_referee", return_value=True), \
                patch.object(native.driver, "guard_candidate"), \
                patch.object(native.driver, "evaluate", side_effect=evaluate), \
                patch.object(native.driver, "resolve_prompt", return_value="resolve"), patch.object(native, "finish"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            native.operate("evaluate", "E0163", "summary")
        return seen

    def test_claim_writes_running_and_evaluate_finished_as_the_claude_driver_does(self) -> None:
        self.assertEqual(self.claim()["status"], "claimed")
        worker = self.worker()
        self.assertEqual((worker["experiment"], worker["status"], worker["name"], worker["provider"]),
                         ("E0163", "running", "native-E0163-lane1", "native-agent"))
        # No process of its own: resume must not take a pid for a live Claude child's, nor a reader a driver's.
        self.assertIsNone(worker["pid"])
        self.assertIsNone(worker["driver_pid"])
        self.assertEqual(json.loads((self.run / "experiment.json").read_text())["session"]["name"],
                         "native-E0163-lane1")
        self.assertEqual(self.seen(), {"coding": ["E0163"], "coding_count": 1, "waiting": [],
                                       "pipeline": ["coding: E0163", "fillers: none"]})
        with patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "recover_promotions", return_value=[]), \
                patch.object(native, "start_watch"), patch.object(native.driver, "lane_prompt", return_value="task"), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            native.resume("E0163")  # its own "running" record doesn't look like a live Claude child
        self.assertEqual(json.loads(out.getvalue())["status"], "resumed")
        # Waiting for the lock, the candidate is queued, no longer coding, by its coding-done time (its place in the
        # evaluation queue, driver.queue_ticket); the driver pid is evaluate's own.
        import evalline
        seen = self.evaluate()
        coded = evalline.hhmm(self.worker()["coded_at"])
        self.assertEqual(seen, [{"coding": [], "coding_count": 0, "waiting": ["E0163"],
                                 "pipeline": [f"queued ahead: 1, in order: E0163 coded {coded}", "coding: nothing"]}])
        self.assertEqual((self.worker()["status"], self.worker()["driver_pid"]), ("finished", os.getpid()))

    # Review of 702bbb9: operate waited for the lock (recover_promotions) before stamping coded_at and taking a
    # ticket, so while another evaluation played it neither prepared nor held its place, and candidates coded after it
    # went first.
    def test_evaluate_takes_its_queue_place_and_prepares_while_another_evaluation_holds_the_lock(self) -> None:
        import threading
        self.claim()
        prepared, evaluated = [], []

        def evaluate(exp, worktree):
            evaluated.append(exp.id)
            exp.verdict, exp.reason = "discarded", "test"

        def run() -> None:
            native.operate("evaluate", "E0163", "summary")

        queue = self.state / "evaluation-queue"
        with patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "recover_held", return_value=[]), \
                patch.object(native.driver, "QUEUE_POLL_S", 0.01), \
                patch.object(native.driver, "prepare_beside", side_effect=lambda exp, _: prepared.append(exp.id)), \
                patch.object(native.driver, "experiment_runtime", side_effect=lambda exp: contextlib.nullcontext()), \
                patch.object(native.driver, "refresh_referee", return_value=True), \
                patch.object(native.driver, "guard_candidate"), \
                patch.object(native.driver, "evaluate", side_effect=evaluate), patch.object(native, "finish"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with native.driver.evaluation_lock():  # another evaluation plays
                thread = threading.Thread(target=run)
                thread.start()
                deadline = time.monotonic() + 10
                while not (list(queue.glob("*.json")) and prepared) and time.monotonic() < deadline:
                    time.sleep(0.01)
                (ticket,) = list(queue.glob("*.json"))
                self.assertEqual(ticket.name,
                                 f"{native.driver.SPRT_RANK}-{self.worker()['coded_at']:017.3f}-E0163.json")
                self.assertEqual((set(prepared), evaluated), ({"E0163"}, []))  # prepare_beside, at every poll
            thread.join(10)
        self.assertEqual((evaluated, list(queue.glob("*.json"))), (["E0163"], []))

    def resume(self) -> str:
        with patch.object(native.driver, "stop_requested", return_value=False), \
                patch.object(native.driver, "recover_promotions", return_value=[]), \
                patch.object(native, "start_watch"), patch.object(native.driver, "lane_prompt", return_value="task"), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            native.resume("E0163")
        return json.loads(out.getvalue())["status"]

    def test_resume_never_takes_a_clone_from_an_evaluation_waiting_for_the_lock(self) -> None:
        # resume refused only a live "running" pid, so while evaluate waited for (or held) the lock it rewrote the
        # record to "running" and handed the clone to a second writer mid-evaluation.
        self.claim()
        refused = []

        def resume_while_waiting():
            with self.assertRaisesRegex(RuntimeError, "waiting for or holding the lock"):
                self.resume()
            refused.append(self.worker()["status"])

        self.evaluate(waiting=resume_while_waiting)
        self.assertEqual(refused, ["finished"])
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()  # an evaluate that died before recording: its saved work can be resumed
        (self.run / "worker.json").write_text(json.dumps({**self.worker(), "driver_pid": dead.pid}))
        self.assertEqual(self.resume(), "resumed")
        self.assertEqual(self.worker()["status"], "running")

    def test_resume_names_how_to_clear_a_reused_pid(self) -> None:
        # A pid the OS handed to another program after the evaluator died used to read as a live evaluation for good.
        self.claim()
        self.evaluate(waiting=lambda: None)
        stranger = subprocess.Popen(["sleep", "60"])  # alive, but no loop entry point
        self.addCleanup(stranger.wait)
        self.addCleanup(stranger.kill)
        (self.run / "worker.json").write_text(json.dumps({**self.worker(), "driver_pid": stranger.pid}))
        self.assertEqual(self.resume(), "resumed")
        # One that is a loop process (or can't be read) is refused, with the check and the override named.
        (self.run / "worker.json").write_text(json.dumps({**self.worker(), "status": "finished",
                                                          "driver_pid": "12x"}))
        with self.assertRaisesRegex(RuntimeError, r"ps -o command= -p 12x.*remove driver_pid from"):
            self.resume()

    # Claims a tune whose run_tuner ends as commit_tune does (worker.json "finished" with claim's pid, here one that has
    # exited), committing `own` commits; returns claim's output.
    def claim_tune(self, own: int = 1, summary: str = "SPSA over 3 params: a 1->2") -> dict:
        dead = subprocess.Popen([sys.executable, "-c", ""])
        dead.wait()  # claim's pid, gone once it returns

        def run_tuner(exp, worktree):
            exp.session.update(provider="tuner", name=f"bc-{exp.id}-tuner", summary=summary)
            native.driver.worker_status(exp, "finished")

        tune = {**E0163["idea"], "kind": "tune", "tune": {"params": ["a"]}}
        self.ideas = [{"id": tune["id"], "status": "trying:lane1", "kind": "tune"}]
        (self.state / "ideas.jsonl").write_text(json.dumps(self.ideas[0]) + "\n")
        with patch.object(native.driver, "run_tuner", side_effect=run_tuner), \
                patch.object(native.driver, "own_commits", return_value=(own, False)), \
                patch.object(native, "finish", side_effect=lambda exp: print(json.dumps(
                    {"id": exp.id, "verdict": exp.verdict, "reason": exp.reason}))), \
                patch.object(native.driver.os, "getpid", return_value=dead.pid):
            return self.claim(tune)

    # A native tune's SPSA runs inside `claim`; commit_tune wrote "finished" with claim's pid, which exits when claim
    # returns "tuned", so until the session ran evaluate the candidate read as stranded: the fillers played beside a
    # candidate waiting and health raised a false alert. The Claude driver evaluates in the process that tuned.
    def test_a_tuned_candidate_waits_for_evaluate_after_claim_exits(self) -> None:
        import health
        import throughput
        self.assertEqual(self.claim_tune()["status"], "tuned")
        self.assertEqual(self.worker()["status"], "finished")
        self.assertEqual(native.driver.meta.stranded(self.state, self.ideas), [])
        self.assertEqual(health.stranded(self.state), [])
        self.assertEqual(throughput.waiting_now(self.state, time.time(), None), ["E0163"])
        self.assertEqual(self.evaluate()[0]["waiting"], ["E0163"])  # evaluate then owns it
        self.assertEqual(self.worker()["driver_pid"], os.getpid())

    # That handoff had no end: a session that died after claim returned "tuned" left its candidate waiting for good,
    # which held every filler ("an evaluation is waiting") with no alert, and throughput counted it waiting for 12 h.
    def test_a_tuned_candidate_no_evaluate_took_up_is_stranded_after_the_handoff_wait(self) -> None:
        import health
        import throughput
        self.claim_tune()
        now = time.time()
        for age, stranded in ((native.driver.meta.TUNED_WAIT_S - 60, False), (native.driver.meta.TUNED_WAIT_S + 60, True)):
            with self.subTest(age=age):
                os.utime(self.run / "worker.json", (now - age, now - age))
                self.assertEqual([exp for _, exp, _ in native.driver.meta.stranded(self.state, self.ideas)],
                                 ["E0163"] if stranded else [])
                self.assertEqual(native.driver.stranded_count(self.ideas), int(stranded))
                self.assertEqual(throughput.waiting_now(self.state, now, None), [] if stranded else ["E0163"])
        (alert,) = health.stranded(self.state)
        self.assertIn("claim returned tuned over 60 min ago and no evaluate took it up", alert.text)
        self.assertIn("`uv run loop/native.py evaluate E0163 'SPSA over 3 params: a 1->2'`", alert.text)
        self.assertIn("`uv run loop/native.py abandon E0163 '<reason>'` records it as an error", alert.text)
        self.assertNotIn("resume", alert.text)  # a tune has no coding to resume
        # A Claude driver's record from before driver_pid, not a tune, still counts as waiting however old.
        (self.run / "worker.json").write_text(json.dumps({"status": "finished", "provider": "claude"}))
        os.utime(self.run / "worker.json", (now - 2 * native.driver.meta.TUNED_WAIT_S,) * 2)
        self.assertEqual(native.driver.meta.stranded(self.state, self.ideas), [])

    # A tune's SPSA that changed nothing printed "tuned" with nothing to evaluate; a session abandoning it recorded an
    # error, which sent the idea back to the queue to tune the same values again (a second error retires it).
    def test_a_tune_that_kept_every_value_is_recorded_no_code_at_claim(self) -> None:
        out = self.claim_tune(own=0, summary="SPSA kept every value; nothing to evaluate")
        self.assertEqual((out["id"], out["verdict"]), ("E0163", "no code"))
        self.assertIn("SPSA kept every value", out["reason"])

    # resume handed a tune's clone to the session with a coding prompt, though a tune has no coder.
    def test_resume_refuses_a_tune(self) -> None:
        self.claim_tune()
        with self.assertRaisesRegex(RuntimeError, r"a tune has no coding to resume: .*native.py evaluate E0163 .*"
                                                  r"native.py abandon E0163"):
            self.resume()
        self.assertEqual(self.worker()["status"], "finished")  # untouched

    # abandon under a live SPSA would record the tune and let finish's cleanup delete the clone the tuner plays from.
    def test_abandon_refuses_a_tune_still_playing_its_games(self) -> None:
        self.claim_tune()
        stranger = subprocess.Popen(["sleep", "60"])  # a pid the OS reused for another program
        self.addCleanup(stranger.wait)
        self.addCleanup(stranger.kill)
        recorded = []
        for pid, refused in ((os.getpid(), True), (stranger.pid, False)):
            with self.subTest(pid=pid):
                (self.run / "worker.json").write_text(json.dumps({**self.worker(), "status": "tuning", "pid": pid}))
                with patch.object(native.driver, "recover_held", return_value=[]), \
                        patch.object(native.driver, "stop_requested", return_value=False), \
                        patch.object(native.driver, "evaluation_lock", side_effect=unlocked), \
                        patch.object(native.driver, "experiment_runtime",
                                     side_effect=lambda exp: contextlib.nullcontext()), \
                        patch.object(native, "finish", side_effect=recorded.append), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if refused:
                        with self.assertRaisesRegex(RuntimeError, "still playing its SPSA games"):
                            native.operate("abandon", "E0163", "its SPSA died")
                    else:
                        native.operate("abandon", "E0163", "its SPSA died")
        self.assertEqual([exp.verdict for exp in recorded], ["error"])

    # claim_next's toolchain lines ("toolchain maintenance: ...", "toolchain upgrade waiting: ...") went to stdout
    # ahead of claim's JSON, so a session reading it as one object failed.
    def test_claim_next_log_lines_leave_claims_stdout_one_json_object(self) -> None:
        def claim_next(lane, stop, reevaluations=True):
            native.driver.log("toolchain maintenance: {'status': 'retry-later'}")
            return E0163["idea"]
        self.assertEqual(self.claim(claim_next=claim_next)["status"], "claimed")

    # An idea whose settings the Experiment refuses (a gain test's elo1 on a formula) is set to error, with no clone
    # and no record, instead of the ValueError leaving it trying:lane1.
    def test_a_claim_whose_settings_are_refused_sets_its_idea_to_error(self) -> None:
        idea = {**E0163["idea"], "kind": "formula", "evaluation": {"elo1": 15}}
        with patch.object(native.driver, "finish_idea") as finish:
            self.assertEqual(self.claim(idea=idea)["status"], "error")
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[:2], (idea["id"], "error"))
        self.assertFalse(self.run.exists())
        self.assertIn("not started: evaluation requires", (self.state / "host-alerts.log").read_text())

    # abandon recorded every idea "discarded", which marks it tried: for a tune, whose values only its SPRT judges, a
    # dead SPSA then lost its idea, and a finished one's tune.json was never reused (driver.earlier_tune wants an error).
    def test_abandoning_a_tune_records_an_error_so_its_idea_is_tried_again(self) -> None:
        self.claim()
        recorded = []
        for kind, verdict in ((None, "discarded"), ("tune", "error")):
            with self.subTest(kind=kind):
                saved = json.loads((self.run / "experiment.json").read_text())
                saved["idea"] = {k: v for k, v in saved["idea"].items() if k != "kind"} | (
                    {"kind": kind} if kind else {})
                (self.run / "experiment.json").write_text(json.dumps(saved))
                with patch.object(native.driver, "recover_held", return_value=[]), \
                        patch.object(native.driver, "stop_requested", return_value=False), \
                        patch.object(native.driver, "evaluation_lock", side_effect=unlocked), \
                        patch.object(native.driver, "experiment_runtime",
                                     side_effect=lambda exp: contextlib.nullcontext()), \
                        patch.object(native, "finish", side_effect=recorded.append), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    native.operate("abandon", "E0163", "its SPSA died")
                exp = recorded[-1]
                self.assertEqual((exp.verdict, exp.reason), (verdict, "its SPSA died"))
        self.assertEqual(native.driver.idea_status("error", "its SPSA died"), "todo")
        self.assertIn("error", native.driver.UNJUDGED)  # what earlier_tune reuses

    def test_a_merge_turn_is_coding_again(self) -> None:
        self.claim()
        self.evaluate("conflict")  # resolve_required: the session resolves it, then runs evaluate again
        self.assertEqual((self.worker()["status"], native.driver.coding_count(self.ideas)), ("running", 1))
        self.assertEqual(self.evaluate()[0]["waiting"], ["E0163"])

    def test_a_record_claimed_before_worker_json_counts_as_coding_until_it_is_recorded(self) -> None:
        # E0163 and E0164 were in flight when this landed: no worker.json, no session name.
        self.run.mkdir(parents=True)
        (self.run / "experiment.json").write_text(json.dumps(E0163))
        self.assertEqual(native.driver.coding_count(self.ideas), 1)
        self.assertEqual(self.evaluate()[0]["waiting"], ["E0163"])  # its evaluate names it and writes "finished"
        self.assertEqual(self.worker()["name"], "native-E0163-lane1")
        (self.run / "worker.json").unlink()
        (self.run / "experiment.json").write_text(json.dumps({**E0163, "finished": time.time()}))
        self.assertEqual(native.driver.coding_count(self.ideas), 0)


class TestUnknownHolderHalf(unittest.TestCase):
    def test_six_coders_with_no_lane_under_test_stay_within_half_of_a_14_core_host(self) -> None:
        # A native `claim N` can make a sixth coder; with no lane in the marker there are 8 rationed slots, and one
        # game each (14 // 14) would give 8 games where the half is 7.
        lanes = list(range(1, native.driver.IN_FLIGHT + 1))
        for cores in (14, 15, 28):
            for offset in range(8):
                share = native.evaluation_shares(cores, *native.share_slot(None), offset=offset)[0]
                self.assertLessEqual(sum(coder_games(cores, offset, None, lanes)) + share, cores // 2, (cores, offset))


if __name__ == "__main__":
    unittest.main()
