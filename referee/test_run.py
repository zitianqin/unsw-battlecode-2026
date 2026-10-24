"""Scheduler tests with fake games (no engine, no SSH). Run: uv run python -m unittest discover -s referee"""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import tempfile
import threading
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import host_agent
import remote
import run

A, B, PY = Path("a"), Path("b"), Path("py")
SCRATCH = tempfile.TemporaryDirectory(prefix="test-run-")  # every file these tests may write, removed at exit
TMP = Path(SCRATCH.name)
NATIVE = {A: Path("a.bin"), B: Path("b.bin"), PY: None}


def raw(job: run.Job) -> run.RawGame:
    return run.RawGame(f"{job.map.name}-{job.a_side}", 0, None, 0.0)


# Results carry their job in stdout, so tests read the order back without a real game.
def fake_parse(job: run.Job, game: run.RawGame) -> str:
    return game.stdout


class FakeHost(remote.Host):
    def __init__(self, fail: bool = False, platform: str = remote.PLATFORM):
        super().__init__("fake", 4)
        self.fail, self.calls, self._platform = fail, [], platform

    def capacity(self) -> int:
        return self.max

    def play(self, game_map, bots, timeout, folder, seed=None, record=None, fork=None, native=False, sandbox=False):
        if self.fail:
            raise remote.RemoteError("down")
        self.calls.append((game_map.name, bots, *(("sandbox",) if sandbox else ())))
        return remote.RemoteGame(f"{game_map.name}-{'A' if bots[0] == Path('a.bin') else 'B'}", 0, None)

    def kill(self, folder):
        pass

    # remote.Host's asks the host (prepare, over ssh) for a host of another platform; the Scheduler asks it before
    # every remote game (native_plan's place).
    def native_place(self) -> str:
        return remote.MAC_HOSTS if self._platform == remote.PLATFORM else f"{self._platform} fake"


def scheduler(hosts, slots=2):
    s = run.Scheduler("auto", False, NATIVE)
    s.hosts = hosts
    return s, patch.object(run, "game_slots", lambda workers, active: slots)


class TestScheduler(unittest.TestCase):
    def setUp(self) -> None:
        unseeded = patch.object(run, "seeded_toolkit", return_value=False)  # these fake maps aren't files
        unseeded.start()
        self.addCleanup(unseeded.stop)

    # E0260's game as `unswbc run` printed it: the map fails the engine's own loader, so the game's error says so in
    # the form the loop reads as the map's fault (loop/prune.py MAP_FAULT); the same output on a map that loads doesn't.
    def test_a_game_on_a_map_the_engine_refuses_says_so_and_no_other_failed_game_does(self) -> None:
        bad = Path(__file__).resolve().parent / "testdata/gen_worldgen_144449864.map"
        good = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        for path, expected in ((bad, f"{run.MAP_REFUSED} {bad}: dragon 0 segment 6 is not adjacent"),
                               (good, "unswbc run exited 1")):
            raw = run.RawGame(f"loaded c/c++ vs c/c++\nerror: {path}: dragon 0 segment 6 is not adjacent to the one "
                              "before it\n", 1, None, 0.1)
            error = run.parse_game(run.Job(path, Path("cand"), Path("base"), "A"), raw).error
            self.assertTrue(error.startswith(expected), error)

    def test_a_game_this_machine_failed_is_tried_again_after_a_pause_but_a_bot_failure_is_not(self) -> None:
        # E0158's game as `unswbc run` printed it at 13:03:40, twice (its try and the old instant replay), when a fork
        # was refused at this user's process limit; the next try, after a pause, plays the same game.
        job = run.Job(Path("gen_big_559869805.map"), Path("rammer"), Path("cand"), "B")
        eagain = run.RawGame("loaded c/c++ vs c/c++\nwithout --sandbox the seed repeats the pearls but not the bots' "
                             "own random numbers\nseed 0x847d1c0660d8d9f6\n"
                             "error: [Errno 35] Resource temporarily unavailable\n", 1, None, 0.4)
        clean = run.RawGame("team A wins after 499 rounds (on length)\n", 0, b"r", 1.0)
        waits = []
        s = run.Scheduler(1, False, {})
        retries = remote.loop_home() / run.GAME_RETRIES
        with patch("sys.stderr"), patch.object(s, "pause", lambda seconds: waits.append(seconds) or False), \
                patch.object(run, "run_game", side_effect=[eagain, eagain, clean]) as game:
            self.assertIs(s.play_here(job, TMP / "x.replay"), clean)
        self.assertEqual((game.call_count, waits), (3, list(run.REPLAY_WAITS_S)))
        (row,) = json.loads(retries.read_text())["games"]
        self.assertEqual((row["map"], row["a_side"], row["tries"], row["recovered"]), (str(job.map), "B", 3, True))
        self.assertEqual(row["why"], "error: [Errno 35] Resource temporarily unavailable")
        # On every try: the last failure is the game's, saying so, and it is kept as never played.
        with patch("sys.stderr"), patch.object(s, "pause", lambda seconds: False), \
                patch.object(run, "run_game", side_effect=[eagain] * 3) as game:
            failed = s.play_here(job, TMP / "x.replay")
        self.assertEqual(game.call_count, 3)
        self.assertIn("unswbc run exited 1", run.parse_game(job, failed).error)
        self.assertIn("failed the game on all 3 tries", run.parse_game(job, failed).error)
        self.assertEqual([r["recovered"] for r in json.loads(retries.read_text())["games"]], [True, False])
        # This process's own fork refused, and the game's process killed by a signal we didn't send, are retried too.
        with patch("sys.stderr"), patch.object(s, "pause", lambda seconds: False), patch.object(
                run, "run_game", side_effect=[BlockingIOError(35, "Resource temporarily unavailable"),
                                              run.RawGame("", -9, None, 1.0), clean]) as game:
            self.assertIs(s.play_here(job, TMP / "x.replay"), clean)
        self.assertEqual(game.call_count, 3)
        self.assertEqual(json.loads(retries.read_text())["games"][-1]["why"],
                         "cannot start the game: [Errno 35] Resource temporarily unavailable")
        # A bot's failure, a game past its time limit and a bot that won't build say something about the bots: never
        # tried again, and nothing is recorded.
        for failure in (run.RawGame("error: bot a exited with status 139\n", 1, None, 1.0),
                        run.RawGame("\ngame exceeded 900s", None, None, 900.0),
                        run.RawGame("error: cannot build cand: cand/main.cpp:3: error: expected ';'\n", 1, None, 1.0)):
            with self.subTest(failure=failure.stdout), patch.object(run, "run_game",
                                                                    side_effect=[failure, clean]) as game:
                self.assertIs(s.play_here(job, TMP / "x.replay"), failure)
                self.assertEqual(game.call_count, 1)
        self.assertEqual(len(json.loads(retries.read_text())["games"]), 3)

    def test_a_lowered_game_that_ran_out_of_time_is_played_again_at_normal_priority(self) -> None:
        # The loop's games here run at nice 19 so dev work wins this Mac's CPU; points, not wall time, drive a game's
        # clock, but a turn's 10 s wall limit could still catch a starved game, which then counts only once replayed.
        # A lowered game comes back from watch_game with no exit code when a turn ran out of time or it ran too long.
        job = run.Job(Path("gen_eval_1.map"), Path("cand"), Path("champion"), "A")
        starved = run.RawGame("round 212: bot 17 (team B) ran out of time\n", None, None, 3.0)
        finished = run.RawGame("round 212: bot 17 (team B) ran out of time\nteam A wins after 499 rounds (on length)\n",
                               0, b"r", 30.0)  # at normal priority the bot's own timeout is the game's
        clean = run.RawGame("team B wins after 499 rounds (on length)\n", 0, b"r", 11.0)
        cpu = run.RawGame("round 212: bot 17 (team B) exceeded CPU limit\nteam A wins after 499 rounds (on length)\n",
                          0, b"r", 11.0)
        s = run.Scheduler(1, False, {})
        slow = run.RawGame("\ngame exceeded 900s", None, None, 900.0)
        retries = remote.loop_home() / run.GAME_RETRIES
        retries.unlink(missing_ok=True)
        for nice, first, calls, niceness in ((19, starved, 2, [19, 0]), (0, finished, 1, [0]), (19, cpu, 1, [19]),
                                             (19, clean, 1, [19]), (19, slow, 2, [19, 0]), (0, slow, 1, [0])):
            with self.subTest(nice=nice, first=first.stdout[:30]), patch.object(run, "GAME_NICE", nice), \
                    patch.object(run, "own_nice", return_value=0), \
                    patch("sys.stderr"), patch.object(run, "run_game", side_effect=[first, clean]) as game:
                self.assertIs(s.play_here(job, TMP / "x.replay"), clean if calls == 2 else first)
                self.assertEqual([c.args[4] if len(c.args) > 4 else 0 for c in game.call_args_list], niceness)
        kept = json.loads(retries.read_text())  # each replay counted, apart from this machine's failures
        self.assertEqual([row["why"] for row in kept[run.NICED_ROWS]],
                         ["a turn ran out of time at nice 19", "the game ran out of time at nice 19"])
        self.assertEqual(kept["games"], [])

    def test_a_game_of_a_process_already_below_normal_priority_is_a_lowered_game(self) -> None:
        # A coder's games play at the home.WORKER_NICE its session inherits (28 Sep), where its sandbox refuses a
        # lower one, so GAME_NICE stays 0: a turn out of time there may be sig's nice-0 work starving it, not the bot.
        # It is watched as it plays and replayed like any lowered game, at the only priority this process has.
        job = run.Job(Path("gen_eval_1.map"), Path("cand"), Path("champion"), "A")
        starved = run.RawGame("round 212: bot 17 (team B) ran out of time\n", None, None, 3.0)
        clean = run.RawGame("team B wins after 499 rounds (on length)\n", 0, b"r", 11.0)
        s = run.Scheduler(1, False, {})
        retries = remote.loop_home() / run.GAME_RETRIES
        retries.unlink(missing_ok=True)
        with patch.object(run, "GAME_NICE", 0), patch.object(run, "own_nice", return_value=10), \
                patch("sys.stderr") as err, patch.object(run, "run_game", side_effect=[starved, clean]) as game:
            self.assertIs(s.play_here(job, TMP / "x.replay"), clean)
        self.assertEqual([(c.args[4:], c.kwargs) for c in game.call_args_list],
                         [((0, True, None), {}), ((), {"native": None})])
        self.assertIn("at this process's own nice 10: a turn ran out of time at nice 10",
                      "".join(c.args[0] for c in err.write.call_args_list))
        self.assertEqual([row["why"] for row in json.loads(retries.read_text())[run.NICED_ROWS]],
                         ["a turn ran out of time at nice 10"])

    def test_a_niced_row_blames_this_mac_only_when_the_replay_at_normal_priority_played_cleanly(self) -> None:
        # A replay that ran a turn out of time at normal priority too is the bot's own timeout, not contention: no
        # "niced" row. One past its time limit at normal priority didn't recover.
        job = run.Job(Path("gen_eval_1.map"), Path("cand"), Path("champion"), "A")
        starved = run.RawGame("round 212: bot 17 (team B) ran out of time\n", None, None, 3.0)
        own = run.RawGame("round 212: bot 17 (team B) ran out of time\nteam A wins after 499 rounds (on length)\n",
                          0, b"r", 30.0)
        slow = run.RawGame("\ngame exceeded 900s", None, None, 900.0)
        s = run.Scheduler(1, False, {})
        retries = remote.loop_home() / run.GAME_RETRIES
        retries.unlink(missing_ok=True)
        for replay in (own, slow):
            with self.subTest(replay=replay.stdout[:30]), patch.object(run, "GAME_NICE", 19), patch("sys.stderr"), \
                    patch.object(run, "own_nice", return_value=0), \
                    patch.object(run, "run_game", side_effect=[starved, replay]):
                self.assertIs(s.play_here(job, TMP / "x.replay"), replay)
        rows = json.loads(retries.read_text())[run.NICED_ROWS]
        self.assertEqual([(row["why"], row["recovered"]) for row in rows], [("a turn ran out of time at nice 19", False)])

    # A lowered game is read as it plays: one starved of CPU stops at its first turn out of time, not after its other
    # turns each wait out the 10 s wall limit (up to GAME_TIMEOUT_S, with the SPRT's results in order behind it).
    def test_a_lowered_game_stops_at_its_first_turn_out_of_time(self) -> None:
        game = TMP / "fake-unswbc"
        job = run.Job(Path("gen_eval_1.map"), Path("a"), Path("b"), "A")
        cases = (("starved", 'printf "\\033[33mround 5: bot 3 (team A) ran out of time\\033[0m\\n"; sleep 30', None),
                 ("plays", 'echo "team A wins after 9 rounds"; echo "said on stderr" >&2', 0),
                 ("too long", "sleep 30", None),
                 ("output closed", "exec >&- 2>&-; sleep 20", None))  # its output ends, the process runs on
        for name, body, code in cases:
            game.write_text(f"#!/bin/sh\n{body}\n")
            game.chmod(0o755)
            with self.subTest(name), patch.object(run, "UNSWBC", str(game)), \
                    patch.object(run, "engine_launcher", return_value=None), \
                    patch.object(run, "fork_plan", return_value=None), patch.object(run, "game_seed", return_value=None), \
                    patch.object(run, "GAME_TIMEOUT_S", 60 if name in ("starved", "plays") else 2):
                started = time.monotonic()
                played = run.run_game(job, False, TMP / "w.replay", nice=19)
                self.assertLess(time.monotonic() - started, 10)
                self.assertEqual(played.returncode, code)
                self.assertEqual(run.out_of_time(played), name == "starved")
                if name == "plays":
                    self.assertIn("said on stderr", played.stdout)
                if name in ("too long", "output closed"):
                    self.assertIn("game exceeded 2s", played.stdout)

    def test_a_lowered_game_starts_through_nice_not_a_preexec_function(self) -> None:
        # preexec_fn runs in the forked child before exec, which in this threaded scheduler can deadlock it.
        job = run.Job(Path("gen_eval_1.map"), Path("a"), Path("b"), "A")
        seen = []

        class Proc:
            pid, returncode = 1, 0

            def __init__(self, cmd, **kwargs):
                seen.append((cmd, kwargs.get("preexec_fn"), (kwargs.get("env") or {}).get("PYTHONUNBUFFERED")))

            def communicate(self, timeout=None):
                return "", ""

        with patch.object(run.subprocess, "Popen", Proc), patch.object(run, "engine_launcher", return_value=None), \
                patch.object(run, "fork_plan", return_value=None), patch.object(run, "game_seed", return_value=None), \
                patch.object(run, "watch_game", return_value=run.RawGame("", 0, None, 0.0)):
            run.run_game(job, False, TMP / "n.replay", nice=19)
            run.run_game(job, False, TMP / "n.replay")
            run.run_game(job, False, TMP / "n.replay", watch=True)  # already lowered: watched, no wrapper
        (lowered, preexec, unbuffered), (normal, preexec_normal, _), (inherited, _, watched) = seen
        self.assertEqual((lowered[:3], preexec, unbuffered), (["/usr/bin/nice", "-n", "19"], None, "1"))
        self.assertEqual((normal[0], preexec_normal), (run.UNSWBC, None))
        self.assertEqual((inherited[0], watched), (run.UNSWBC, "1"))

    def test_a_lowered_games_fork_server_start_timeout_isnt_the_fork_server_off(self) -> None:
        # A lowered game gives dev work the CPU first, so its fork server may miss fork_server.START_S: that falls
        # back to starting bots as usual (the same game), not a fork server that stopped working (a 6 h alert).
        job = run.Job(Path("gen_eval_1.map"), Path("a"), Path("b"), "A")
        timeout = run.RawGame("fork server: off for a (the shim didn't start in a (timed out))\nteam A wins\n", 0,
                              b"r", 1.0)
        other = run.RawGame("fork server: off for a (the shim didn't start in a (it closed its socket))\n", 0, b"r", 1.0)
        s = run.Scheduler(1, False, {})
        for nice, game, noted in ((19, timeout, False), (0, timeout, True), (19, other, True)):
            with self.subTest(nice=nice, game=game.stdout[:60]), patch.object(run, "GAME_NICE", nice), \
                    patch.object(run, "own_nice", return_value=0), \
                    patch.object(run, "run_game", return_value=game), patch.object(run, "note_fork_off") as note:
                s.try_here(job, TMP / "f.replay", lambda proc: None, retry=False)
                self.assertEqual(note.called, noted)

    def test_a_game_waiting_to_replay_here_asks_for_a_slot_outside_the_schedulers_lock(self) -> None:
        # The loop's game_slots reads vm_stat and the CPU split; under the lock every game's thread waited on it.
        s = run.Scheduler(1, False, {})
        held = []

        def slots(workers, work):
            probe = threading.Thread(target=lambda: held.append(not s.changed.acquire(blocking=False) or
                                                                s.changed.release()))
            probe.start()
            probe.join()
            return 1

        with patch.object(run, "game_slots", slots), patch.object(s, "play_here", return_value="played"):
            self.assertEqual(s.replay_here(run.Job(Path("m.map"), A, B, "A"), TMP / "r.replay"), "played")
        self.assertEqual(held, [None])  # the probe took the lock: nobody held it

    def test_a_stopped_run_neither_pauses_out_its_wait_nor_tries_again(self) -> None:
        job = run.Job(Path("m.map"), A, B, "A")
        eagain = run.RawGame("error: [Errno 35] Resource temporarily unavailable\n", 1, None, 0.1)
        s = run.Scheduler(1, False, {})
        threading.Timer(0.2, s.stop).start()
        started = time.monotonic()
        with patch("sys.stderr"), patch.object(run, "run_game", side_effect=[eagain, eagain]) as game:
            s.play_here(job, TMP / "x.replay")
        self.assertLess(time.monotonic() - started, 5)  # the first pause is 10 s
        self.assertEqual(game.call_count, 1)

    def test_games_a_host_drops_replay_here_within_the_slots_not_all_at_once(self) -> None:
        # E0158: zz-wsl dropped its 26 games in flight at 13:03:37 and every one started here at once, beside this Mac's
        # 9, past this user's process limit. Here a host with 4 games in flight fails them all beside 2 local slots.
        class Dropping(FakeHost):
            def play(self, *args, **kwargs):
                time.sleep(0.2)
                raise remote.RemoteError("zz-wsl: exit 255: ")

        peak, running, lock = [0], [0], threading.Lock()

        def game(job, sandbox, path, started=None, nice=0, watch=False, native=None):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.1)
            with lock:
                running[0] -= 1
            return raw(job)

        s, slots = scheduler([Dropping()], slots=2)
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(8)]
        with slots, patch.object(run, "run_game", game), patch.object(run, "parse_game", fake_parse), \
                patch("sys.stderr"):
            self.assertEqual(list(s.play(jobs, TMP)), [f"m{i}-A" for i in range(8)])
        self.assertLessEqual(peak[0], 3)  # the 2 slots, and at most one replay past them so one always progresses
        self.assertEqual((s.local, s.waiting, s.over), (0, 0, 0))

    def test_no_replay_goes_past_the_slots_when_this_users_process_limit_filled_them(self) -> None:
        # The one replay past the slots is for a zero from load: four schedulers each playing one 150-process game
        # past a zero from the process limit (run.fork_room, home.game_room in the loop) reach it (the E0158 pattern).
        class Dropping(FakeHost):
            def play(self, *args, **kwargs):
                time.sleep(0.2)
                raise remote.RemoteError("zz-wsl: exit 255: ")

        peak, running, lock = [0], [0], threading.Lock()

        def game(job, sandbox, path, started=None, nice=0, watch=False, native=None):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.1)
            with lock:
                running[0] -= 1
            return raw(job)

        s, slots = scheduler([Dropping()], slots=2)
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(8)]
        with slots, patch.object(run, "fork_room", lambda active=0: max(0, 2 - active), create=True), \
                patch.object(run, "run_game", game), patch.object(run, "parse_game", fake_parse), patch("sys.stderr"):
            self.assertEqual(list(s.play(jobs, TMP)), [f"m{i}-A" for i in range(8)])
        self.assertLessEqual(peak[0], 2)
        self.assertEqual((s.local, s.waiting, s.over), (0, 0, 0))

    # E0466, 1 Oct 03:31-~03:57: a game a host lost was replayed here at nice 19 at load ~40 while it was the head game,
    # and every host idled ~25 min. A lost game goes to another host with room first; with none, the head game plays
    # here at the run's own priority. The SPRT's rests and replays reach host-alerts.log.
    def test_a_lost_game_goes_to_another_host_first_and_a_head_game_here_plays_unniced(self) -> None:
        class Dropping(FakeHost):
            def play(self, *args, **kwargs):
                raise remote.RemoteError("exit 255: ")

        lost, spare = Dropping(), FakeHost()
        lost.name, spare.name = "lost", "spare"
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(3)]
        s, slots = scheduler([lost, spare], slots=0)
        with slots, patch.object(run, "run_game", side_effect=AssertionError("played here")), \
                patch.object(run, "parse_game", fake_parse), patch("sys.stderr"):
            self.assertEqual(list(s.play(jobs, TMP)), ["m0-A", "m1-A", "m2-A"])
        self.assertIn("m0", [call[0] for call in spare.calls])
        # No other host: the head game plays here at the run's own priority within the slots (at GAME_NICE past
        # them), and says so in host-alerts.log, as does the host's second failure in a row.
        class Freeing(Dropping):  # this Mac's slot frees as the host fails, so the replay gets in within the slots
            def play(self, *args, **kwargs):
                free[0] = 1
                return Dropping.play(self, *args, **kwargs)

        for room, nice in ((1, 0), (0, 19)):
            alerts, nices, free = TMP / f"alerts-{room}.log", [], [0]
            s = run.Scheduler("auto", False, NATIVE, alerts=True)
            s.hosts = [Freeing()]
            s.hosts[0].failures = 1
            with self.subTest(room=room), patch.object(run, "game_slots", lambda workers, active: free[0] * room), \
                    patch.object(run, "GAME_NICE", 19), patch.object(remote, "ALERTS", alerts), \
                    patch.object(run, "parse_game", fake_parse), patch.object(
                        run, "run_game", lambda job, sandbox, path, started=None, nice=0, *rest: nices.append(nice)
                        or raw(job)), patch("sys.stderr"):
                self.assertEqual(list(s.play([run.Job(Path("m0"), A, B, "A")], TMP)), ["m0-A"])
                self.assertEqual(nices, [nice])
        text = (TMP / "alerts-1.log").read_text()
        self.assertIn("SPRT remote fake resting: exit 255", text)
        self.assertIn("SPRT replaying m0 (a A) here at this run's own priority: it is the head game", text)

    def test_games_start_as_soon_as_a_host_measured_in_the_background_has_room(self) -> None:
        # With this Mac full, the scheduler waits up to 15 s for a change; a host's first measurement is one.
        class Measured(FakeHost):
            def capacity(self) -> int:
                return remote.Host.capacity(self)

            def measure(self) -> int:
                time.sleep(0.2)
                with self._measure_lock:
                    self._free = (time.monotonic(), self.max)
                return self.max

        host = Measured()
        s, slots = scheduler([host], slots=0)
        s.hosts[0].wakers.append(s.wake)
        started = time.monotonic()
        with slots, patch.object(run, "parse_game", fake_parse):
            self.assertEqual(list(s.play([run.Job(Path("m0"), A, B, "A")], TMP)), ["m0-A"])
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(host.wakers, [])  # the finished run no longer wakes

    def test_a_failing_host_backs_off_60s_then_doubles_and_recovers_on_a_clean_game(self) -> None:
        host = FakeHost()
        with patch("sys.stderr"):
            for expected in (60, 120, 240):
                before = time.monotonic()
                host.rest("down")
                self.assertAlmostEqual(host.rested_until - before, expected, delta=1)
        host.succeeded()
        self.assertEqual((host.failures, host.played, len(host.rests)), (0, 1, 3))

    def test_host_rests_are_recorded_for_the_loop(self) -> None:
        host = FakeHost(fail=True)
        s, slots = scheduler([host], slots=0)
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse), patch("sys.stderr"):
            list(s.play([run.Job(Path("m0"), A, B, "A")], TMP))
        status = remote.read_status()["hosts"]["fake"]
        self.assertEqual((status["played"], status["rests"]), (0, 1))
        self.assertIn("down", status["last_rest"])

    # A native game the judge failed on a host (killed by a signal) is replayed here through `unswbc run`, not
    # natively, without resting the host, and switches the judge off for every game (run.settle_native); a judge
    # failing the game as `unswbc run` fails it too (the same exit and error line) switches nothing off. SIGKILL (the
    # host reports 128 + 9) is the machine's (out of memory, a spot VM), and so is any failure of a game the host
    # wasn't sent to play natively (RemoteGame.native False), whatever its output: the host rests, nothing is off.
    def test_a_game_the_native_judge_failed_on_a_host_replays_here_through_unswbc_run(self) -> None:
        game_map = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        bots = [TMP / "judged-a", TMP / "judged-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        job = run.Job(game_map, bots[0], bots[1], "A")
        checks = remote.loop_home() / run.NATIVE_CHECKS
        refused = "error: the map: dragon 0 segment 6 is not adjacent"
        won = run.RawGame("team A wins", 0, None, 1.0)
        for said, code, sent, plain, way, off in (("", -11, True, won, False, True),
                                                  (refused, 1, True, run.RawGame(refused, 1, None, 1.0), False, False),
                                                  ("", 137, True, won, None, False),
                                                  ("", -11, False, won, None, False)):
            with self.subTest(code=code, sent=sent):
                checks.unlink(missing_ok=True)
                host = FakeHost()
                host.native_place = lambda: "fake place"
                host.play = lambda *args, **named: remote.RemoteGame(f"{run.NATIVE_PLAYED} (engine x)\n{said}", code,
                                                                     None, native=sent)
                s, _ = scheduler([host])
                s.native = {bot: bot for bot in bots}
                replayed = []
                s.replay_here = lambda job, path, native=None: replayed.append(native) or plain
                with patch.object(run, "NATIVE_JUDGE", True), patch.object(run, "fork_plan", return_value=None), \
                        patch.object(run.native_judge, "enabled", return_value=True), patch("sys.stderr"), \
                        patch.object(remote.Host, "alert"), patch.object(s, "save"):
                    game = s.play_remote(host, job, TMP / "judged.replay")
                    self.assertEqual((game, replayed, host.failures), (plain, [way], int(way is None)))
                    self.assertEqual((bool(run.natives_off()), run.native_plan(job, False, None)), (off, not off))
        checks.unlink(missing_ok=True)

    # A checked native game on a host is played there once more through `unswbc run` (native=False) and the two
    # compared, and the game that counts is the reference, its replay saved: the same game counts as identical; a
    # reference the host killed (128 + 9, as host_agent reports a signal) or whose bots all failed at round 0 checks
    # nothing and the native game counts; a turn out of time only natively is a difference. A switch-off says how many
    # native games counted since the last matching check, and alerts once however many games in flight switch it off.
    def test_a_checked_native_game_on_a_host_is_played_again_through_unswbc_run(self) -> None:
        game_map = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        bots = [TMP / "checked-a", TMP / "checked-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        job = run.Job(game_map, bots[0], bots[1], "A")
        checks = remote.loop_home() / run.NATIVE_CHECKS
        winner = "team A wins after 3 rounds (by elimination) (1.0s)"
        late = "round 2: bot 0 (team A) ran out of time"
        startup = ("round 0: bot 1 (team A) exited with code 1\nround 0: bot 2 (team B) exited with code 1\n"
                   "draw after 1 rounds (both teams eliminated) (0.1s)")  # the host couldn't start the bots
        for native_said, reference, check, off in ((winner, (winner, 0), (1, 1), False),
                                                   (winner, ("killed", 137), None, False),
                                                   (winner, (startup, 0), None, False),
                                                   (f"{late}\n{winner}", (winner, 0), (2, 0), True)):
            with self.subTest(native=native_said, reference=reference):
                checks.unlink(missing_ok=True)
                run._native_counted.update(games=0, since=0, checked=None)
                host, ways, alerts = FakeHost(), [], []
                host.native_place = lambda: "fake place"

                def play(*args, native=False, **named):
                    ways.append(native)
                    if native:
                        return remote.RemoteGame(f"{run.NATIVE_PLAYED} (engine x)\n{native_said}", 0, b"R", cpu=2.0,
                                                 native=True)
                    return remote.RemoteGame(reference[0], reference[1], b"R", cpu=3.0)

                host.play = play
                s, _ = scheduler([host])
                s.native = {bot: bot for bot in bots}
                path = TMP / "checked.replay"
                path.unlink(missing_ok=True)
                with patch.object(run, "NATIVE_JUDGE", True), patch.object(run, "fork_plan", return_value=None), \
                        patch.object(run.native_judge, "enabled", return_value=True), \
                        patch.object(run, "map_refused"), patch.object(remote.Host, "alert", alerts.append), \
                        patch.object(run, "read_replay", return_value=("A", "elimination", {"A": (1, 1, 1),
                                                                                            "B": (0, 0, 0)})), \
                        patch("sys.stderr"):
                    with patch.object(run, "native_check", return_value=False):  # one unchecked native game
                        self.assertTrue(s.play_remote(host, job, path).native)
                    with patch.object(run, "native_check", return_value=True):
                        games = [s.play_remote(host, job, path)]
                    if off:  # another game in flight switching it off again: no second alert
                        run.count_native_check(job, "fake: check differs: again")
                data = json.loads(checks.read_text()) if checks.exists() else {}
                # A check that differs plays its reference's way once more (plain_differs), which here repeats.
                self.assertEqual(ways, [True, True, False, *([False] if off else [])])
                if check is None:  # the native game counts, unchecked
                    self.assertEqual(([g.native for g in games], data.get("checks", 0)), ([True], 0))
                    continue
                self.assertEqual(([(g.native, g.cpu) for g in games], path.read_bytes()), ([(False, 3.0)], b"R"))
                self.assertEqual((data["checks"], data["identical"], bool(data.get("off"))), (*check, off))
                if off:
                    self.assertIn(1, {e["native_since_check"] for e in data["off"].values()})
                    self.assertEqual(len(alerts), 1, alerts)
                    self.assertIn("1 native games counted in this run since its last matching check", alerts[0])
        checks.unlink(missing_ok=True)

    # A native game past its time on a host that still answers (remote.GameTimeout) rests the host and is replayed
    # here through `unswbc run`, not natively again, switching nothing off (a busy host, perhaps; a hang is caught
    # here); a game the host wasn't sent natively, or any other host failure, replays here as usual (natively, where
    # this Mac may).
    def test_a_native_game_past_its_time_on_a_host_replays_here_through_unswbc_run(self) -> None:
        game_map = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        bots = [TMP / "late-a", TMP / "late-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        checks = remote.loop_home() / run.NATIVE_CHECKS
        for error, native in ((remote.GameTimeout("fake: timed out after 300s", native=True), False),
                              (remote.GameTimeout("fake: timed out after 300s"), None),
                              (remote.RemoteError("fake: exit 255: gone", 255), None)):
            with self.subTest(error=type(error).__name__, native=native):
                checks.unlink(missing_ok=True)
                host = FakeHost()
                host.native_place = lambda: "fake place"
                host.play = lambda *args, **named: (_ for _ in ()).throw(error)
                s, _ = scheduler([host])
                s.native = {bot: bot for bot in bots}
                replayed = []

                s.replay_here = lambda job, path, native=None: replayed.append(native) or run.RawGame("won", 0,
                                                                                                    None, 1.0)
                with patch.object(run, "NATIVE_JUDGE", True), patch.object(run, "fork_plan", return_value=None), \
                        patch.object(run.native_judge, "enabled", return_value=True), patch("sys.stderr"), \
                        patch.object(remote.Host, "alert"), patch.object(s, "save"):
                    s.play_remote(host, run.Job(game_map, bots[0], bots[1], "A"), TMP / "late.replay")
                self.assertEqual((replayed, bool(run.natives_off()), host.failures), ([native], False, 1))
        checks.unlink(missing_ok=True)

    # MISSES 86: a native check that differs plays its reference's way once more first; a reference that doesn't
    # repeat is the bots' fault, recorded as nondeterministic, and the judge stays on.
    def test_a_differing_check_whose_reference_does_not_repeat_leaves_the_judge_on(self) -> None:
        game_map = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        bots = [TMP / "nd-a", TMP / "nd-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        job = run.Job(game_map, bots[0], bots[1], "A")
        checks = remote.loop_home() / run.NATIVE_CHECKS
        checks.unlink(missing_ok=True)
        reference, game = (run.RawGame("team A wins after 3 rounds (by elimination) (1.0s)", 0, replay, 1.0)
                           for replay in (b"R1", b"N"))
        differs = patch.object(run, "compare_games", side_effect=lambda job, a, b: {
            "problems": [] if a.replay == b.replay else ["replay differs"]})
        with differs, patch.object(remote.Host, "alert") as alert, patch("sys.stderr"):
            self.assertTrue(run.check_native(job, reference, game, "mm1",
                                             lambda: run.RawGame(reference.stdout, 0, b"R2", 1.0)))
            self.assertEqual(run.natives_off(), {})
            data, pair = json.loads(checks.read_text()), " ".join(run.bot_pair(job))
            self.assertEqual(len(data["nondeterministic"]), 2)
            # each entry names both bots, and the pair is kept whole: either may be the one at fault (run.blamed)
            self.assertEqual({tuple(e["bots"]) for e in data["nondeterministic"].values()},
                             {(run.bot_digest(bots[0]), run.bot_digest(bots[1]))})
            self.assertEqual(list(data["nondeterministic_pairs"]), [pair])
            self.assertIn("doesn't repeat its own games", alert.call_args.args[0])
            run.record_check(run.NATIVE_CHECKS, "native judge", job, None)  # a check that matched clears the pair
            self.assertEqual(list(json.loads(checks.read_text())["repeated"]), [pair])
            self.assertTrue(run.check_native(job, reference, game, "mm1", lambda: reference))  # it repeats: the judge
        self.assertEqual(len(run.natives_off()), 2)
        checks.unlink()

    # A check that was skipped (its reference ran a turn out of time) leaves the pair's first check to its next native
    # game; one that compared doesn't. After a switch-off, sprt.json's since_check counts every native game since the
    # last matching check before it, not since a check that matched later (one in flight). Only a check that differed,
    # not a judge that crashed, marks its key (differed_keys, native_suspect): kept once "off" is cleared, and a bot's
    # crash entry doesn't hide it (review 4, 2026-09-29: the first entry stood, so no alert and baselines reused).
    def test_a_skipped_check_moves_on_and_a_switch_off_counts_every_game_at_risk(self) -> None:
        game_map = Path(__file__).resolve().parent.parent / "maps/ladder/default.map"
        bots = [TMP / "pair-a", TMP / "pair-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        job = run.Job(game_map, bots[0], bots[1], "A")
        checks = remote.loop_home() / run.NATIVE_CHECKS
        checks.unlink(missing_ok=True)
        run._native_pairs.clear()
        run._native_compared.clear()
        run._native_matches.clear()
        run._native_counted.update(games=0, since=0, checked=None)
        game = run.RawGame(f"{run.NATIVE_PLAYED}\nteam A wins after 3 rounds (by elimination) (1.0s)", 0, b"R", 1.0)
        starved = run.RawGame("round 2: bot 0 (team A) ran out of time\n", None, None, 1.0)
        same = patch.object(run, "compare_games", return_value={"problems": []})
        with patch.object(run, "NATIVE_CHECK_EVERY", 1 << 62), patch.object(remote.Host, "alert"), \
                patch("sys.stderr"):
            self.assertTrue(run.native_check(job))
            self.assertFalse(run.check_native(job, starved, game, "here"))
            self.assertTrue(run.native_check(job))  # the skipped check's turn
            started = time.time()
            for _ in range(2):
                run.counted_native(game)
            with same:
                self.assertTrue(run.check_native(job, game, game, "here"))
            run.release_native_check(job)
            self.assertFalse(run.native_check(job))
            for _ in range(3):
                run.counted_native(game)
            crashed = run.Job(game_map, bots[1], bots[1], "B")
            with patch.object(run.native_judge, "process_key", return_value="k1"), \
                    patch.object(remote.Host, "alert") as alert:
                run.count_native_check(crashed, "here: the native judge died by signal 11; unswbc run played it", False)
                self.assertEqual((run.differed_keys(), run.native_suspect("k1")), (set(), False))
                time.sleep(0.01)
                run.count_native_check(job, "here: check differs: replay")
                self.assertEqual(alert.call_count, 2)  # the crash's, then the first check that differed
            self.assertTrue(all(entry["check"] for entry in run.natives_off().values()))  # bots[1]'s crash replaced
            self.assertEqual((run.native_suspect("k1"), run.native_suspect("k2"), run.native_suspect(True)),
                             (True, False, True))
            run.counted_native(game)
            time.sleep(0.01)
            with same:
                run.check_native(job, game, game, "here")  # one in flight, matching after the switch-off
            report = run.native_report(started)
            self.assertEqual(report["since_check"], 4)
            self.assertEqual(run.native_fault(report), "here: check differs: replay")
            self.assertIsNone(run.native_fault({**report, "since_check": 0}))  # none of its games at risk
            checks.write_text(json.dumps({**json.loads(checks.read_text()), "off": {}}))  # someone read why
            self.assertEqual((run.natives_off(), run.native_suspect("k1")), ({}, True))
            # Only a new key plays natively again (review 5, 2026-09-29: k1 did once "off" was cleared); a
            # "differed_keys" that isn't a list is unreadable: nothing plays natively, and a check still records.
            plays = {}
            for key in ("k1", "k2", "k2"):
                with patch.object(run, "NATIVE_JUDGE", True), patch.object(run.native_judge, "enabled",
                                                                            return_value=True), \
                        patch.object(run.native_judge, "key", return_value=key), \
                        patch.object(run.native_judge, "process_key", return_value=key):
                    if key in plays:
                        checks.write_text(json.dumps({"off": {}, "differed_keys": "k1"}))
                        run.count_native_check(job, "here: check differs: replay")
                        self.assertEqual((run.differed_keys(), run._differed_here), (None, {"k2"}))
                    plays.setdefault(key, []).append(run.native_plan(job, False, None))
            self.assertEqual(plays, {"k1": [False], "k2": [True, False]})
        run._off_here.clear()
        run._differed_here.clear()
        checks.unlink(missing_ok=True)

    # A game here the machine failed natively twice (an OS error line) is tried a third time through `unswbc run`; the
    # judge is switched off only when, after that played it, one more native try fails with the same line, and that
    # try plays only while the game would still play natively (native_plan: not once switched off or ungated).
    def test_a_game_failing_natively_twice_is_played_through_unswbc_run(self) -> None:
        bots = [TMP / "twice-a", TMP / "twice-b"]
        for bot in bots:
            bot.write_bytes(bot.name.encode())
        job = run.Job(Path("gen_big_1.map"), bots[0], bots[1], "B")
        eagain = run.RawGame(f"{run.NATIVE_PLAYED}\nerror: cannot write the replay to r: [Errno 28] No space left\n",
                             1, None, 0.4)
        clean = run.RawGame("team A wins after 499 rounds (on length)\n", 0, b"r", 1.0)
        checks = remote.loop_home() / run.NATIVE_CHECKS
        for confirm, off, plays in ((clean, False, True), (eagain, True, True), (eagain, False, False)):
            with self.subTest(off=off, plays=plays):
                checks.unlink(missing_ok=True)
                s = run.Scheduler(1, False, {})
                with patch("sys.stderr"), patch.object(s, "pause", return_value=False), \
                        patch.object(remote.Host, "alert"), patch.object(run, "native_plan", return_value=plays), \
                        patch.object(run, "run_game", side_effect=[eagain, eagain, clean, confirm]) as game:
                    self.assertIs(s.play_here(job, TMP / "x.replay"), clean)
                self.assertEqual([c.args[6] for c in game.call_args_list], [None, None, False, True][:3 + plays])
                self.assertEqual(bool(run.natives_off()), off)
        checks.unlink(missing_ok=True)

    # A switch-off that can't be written (native-judge-checks.json's lock can't be opened) still switches this process
    # off, with an alert, rather than leaving the judge on.
    def test_a_switch_off_that_cannot_be_written_still_switches_this_process_off(self) -> None:
        checks = remote.loop_home() / run.NATIVE_CHECKS
        checks.unlink(missing_ok=True)
        lock = checks.with_suffix(".lock")
        lock.unlink(missing_ok=True)
        lock.mkdir()
        alerts, bot = [], TMP / "unwritten-a"
        bot.write_bytes(b"a")
        try:
            with patch.object(remote.Host, "alert", alerts.append), patch("sys.stderr"):
                run.count_native_check(run.Job(Path("m.map"), bot, bot, "A"), "here: check differs: replay")
            self.assertTrue(run.natives_off())
            self.assertTrue(any("this process only" in text for text in alerts), alerts)
        finally:
            lock.rmdir()
            run._off_here.clear()
            run._differed_here.clear()

    def test_status_shows_progress_and_each_hosts_last_game_while_games_run(self) -> None:
        host = FakeHost()
        s, slots = scheduler([host], slots=0)  # every game goes to the host but the last, a Python bot
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(3)] + [run.Job(Path("m3"), PY, B, "A")]
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse), patch.object(run, "STATUS_S", 0):
            games = s.play(jobs, TMP)
            next(games)
            status = remote.read_status()
            self.assertEqual(status["games"], 1)  # written mid-run, not only at the end
            list(games)
        status = remote.read_status()
        self.assertEqual(status["games"], 4)
        self.assertEqual((status["here"], status["hosts"]["fake"]["played"]), (1, 3))
        self.assertIsNotNone(status["hosts"]["fake"]["last_game"])
        # The games in flight, here and per host, for the loop's utilisation record: none once the run has ended.
        self.assertEqual((status["running"], status["active"], status["cores"]), (0, {"fake": 0}, {"fake": 0}))

    def test_status_counts_games_in_flight_while_none_finishes(self) -> None:
        # A long game on the host: the status is rewritten while it runs (before each wait, at most every STATUS_S),
        # with the game counted there.
        host = FakeHost()
        s, slots = scheduler([host], slots=0)
        seen, play = [], host.play

        def slow(*args, **kwargs):
            time.sleep(0.3)
            seen.append(remote.read_status())
            return play(*args, **kwargs)

        host.play = slow
        with slots, patch.object(run, "parse_game", fake_parse), patch.object(run, "STATUS_S", 0):
            list(s.play([run.Job(Path("m0"), A, B, "A")], TMP))
        self.assertEqual(seen[0]["active"], {"fake": 1})
        self.assertEqual(seen[0]["games"], 0)

    def test_results_come_back_in_job_order_when_games_finish_out_of_order(self) -> None:
        delays = {"m0": 0.3, "m1": 0.2, "m2": 0.1}
        peak, running, lock = [0], [0], threading.Lock()

        def slow(job, sandbox, path, started=None, nice=0, watch=False, native=None):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(delays[job.map.name])
            with lock:
                running[0] -= 1
            return raw(job)

        s, slots = scheduler([], slots=3)
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(3)]
        with slots, patch.object(run, "run_game", slow), patch.object(run, "parse_game", fake_parse):
            self.assertEqual(list(s.play(jobs, TMP)), ["m0-A", "m1-A", "m2-A"])
        self.assertEqual(peak[0], 3)

    def test_with_drop_each_replay_goes_once_the_caller_asks_for_the_next_result(self) -> None:
        # The prewarm (no replay folder) streamed ~1 GB of replays a minute into $TMPDIR until its stream ended.
        def played(job, sandbox, replay_path, *_, **__):
            replay_path.write_bytes(b"replay")
            return raw(job)

        folder = TMP / "drop"
        folder.mkdir()
        s, slots = scheduler([], slots=1)
        seen = []
        with slots, patch.object(run, "run_game", played), patch.object(run, "parse_game", fake_parse):
            for result in s.play([run.Job(Path(f"m{i}"), A, B, "A") for i in range(3)], folder, drop=True):
                seen.append((result, {p.name for p in folder.iterdir()}))
        self.assertEqual([r for r, _ in seen], ["m0-A", "m1-A", "m2-A"])
        self.assertTrue(all(f"000{i}-m{i}-aA.replay" in names and f"000{i - 1}-m{i - 1}-aA.replay" not in names
                            for i, (_, names) in enumerate(seen)))
        self.assertEqual(list(folder.iterdir()), [])

    def test_a_game_reaching_a_host_after_stop_is_never_launched_there(self) -> None:
        # A game thread that reaches play_remote after stop() took its snapshot of remote_games would launch a game
        # nobody kills, playing up to REMOTE_TIMEOUT_S into a deleted scratch folder.
        host = FakeHost()
        s, _ = scheduler([host])
        s.stop()
        game = s.play_remote(host, run.Job(Path("m0"), A, B, "A"), TMP / "m0.replay")
        self.assertEqual((game.stdout, host.calls, s.remote_games), ("stopped", [], set()))

    def test_host_failure_replays_the_game_here_and_rests_the_host(self) -> None:
        host = FakeHost(fail=True)
        s, slots = scheduler([host], slots=0)  # nothing local, so the first game must try the host
        jobs = [run.Job(Path("m0"), A, B, "B")]
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse), patch("sys.stderr"):
            self.assertEqual(list(s.play(jobs, TMP)), ["m0-B"])
        self.assertFalse(host.usable())

    def test_a_game_that_fails_remotely_is_replayed_here(self) -> None:
        host = FakeHost()
        host.play = lambda *a, **k: remote.RemoteGame("error: [Errno 24] Too many open files", 1, None)
        s, slots = scheduler([host], slots=0)
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse), patch("sys.stderr"):
            self.assertEqual(list(s.play([run.Job(Path("m0"), A, B, "A")], TMP)), ["m0-A"])
        self.assertFalse(host.usable())

    def test_only_native_bots_play_remotely_with_team_a_first(self) -> None:
        host = FakeHost()
        s, slots = scheduler([host], slots=0)
        jobs = [run.Job(Path("m0"), A, B, "B"), run.Job(Path("m1"), PY, B, "A")]
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse):
            self.assertEqual(list(s.play(jobs, TMP)), ["m0-B", "m1-A"])
        self.assertEqual(host.calls, [("m0", (Path("b.bin"), Path("a.bin")))])
        self.assertEqual(host.played, 1)

    # So hosts can be compared on the same games: each result says where it played and, from a host that reports it
    # (host_agent.game_cpu, a Mac or Linux host), the game's CPU seconds.
    def test_each_result_names_its_host_and_a_remote_games_cpu_seconds(self) -> None:
        host = FakeHost()
        host.play = lambda *a, **k: remote.RemoteGame("m0-A", 0, None, cpu=4.2)
        s, slots = scheduler([host], slots=0)
        jobs = [run.Job(Path("m0"), A, B, "A"), run.Job(Path("m1"), PY, B, "A")]  # a Python bot plays only here
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", lambda job, game: (game.host, game.cpu)):
            self.assertEqual(list(s.play(jobs, TMP)), [("fake", 4.2), ("here", None)])
        self.assertIsInstance(host_agent.game_cpu(), float)

    # 29 Sep: every CPU gate played only here while this Mac sat at load 13-24 all night, the hosts idle ~10 min an
    # evaluation. With sandbox_hosts (cpu.py --hosts) its games go to a host of this Mac's platform (its sandbox
    # points measured equal) when this Mac has no slot, never to another platform's (nor measuring its room), and come
    # back here when the host fails; without it (a coder's own CPU check) they stay here.
    def test_sandbox_games_play_on_this_platforms_hosts_only_when_asked(self) -> None:
        wasm = {A: Path("a.wasm"), B: Path("b.wasm")}
        for asked, fail, expected in ((True, False, ["fake"] * 2), (True, True, ["here"]), (False, False, ["here"])):
            linux, mac = FakeHost(platform="Linux x86_64"), FakeHost(fail=fail)
            linux.capacity = lambda: self.fail("a host of another platform was measured")
            jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(len(expected))]
            with self.subTest(asked=asked, fail=fail), patch.object(remote, "load_hosts", return_value=[linux, mac]), \
                    patch.object(run, "game_slots", lambda workers, active: 0 if asked else 1), \
                    patch.object(run, "run_game", lambda job, *_: raw(job)), patch("sys.stderr"), \
                    patch.object(run, "parse_game", lambda job, game: game.host):
                self.assertEqual(list(run.Scheduler("auto", True, wasm, sandbox_hosts=asked).play(jobs, TMP)), expected)
            self.assertEqual(linux.calls, [])
            sent = [(f"m{i}", (Path("a.wasm"), Path("b.wasm")), "sandbox") for i in range(2)]
            self.assertEqual(sorted(mac.calls), sent if asked and not fail else [])
            self.assertEqual(mac.usable(), not fail)
        # A sandbox game with a turn out of time on the host (its 10 s wall limit: a busy host, perhaps) is replayed
        # here, as a niced one here is, rather than failing the gate as a timeout; the host isn't rested.
        mac = FakeHost()
        mac.play = lambda *a, **k: remote.RemoteGame("round 5: bot 3 (team A) ran out of time\nteam B wins", 0, None)
        with patch.object(remote, "load_hosts", return_value=[mac]), patch("sys.stderr"), \
                patch.object(run, "game_slots", lambda workers, active: 0), \
                patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", lambda job, game: game.host):
            self.assertEqual(list(run.Scheduler("auto", True, wasm, sandbox_hosts=True).play(jobs[:1], TMP)), ["here"])
        self.assertTrue(mac.usable())
        # What a host is sent of each bot: its wasm build in the sandbox, its executable outside it.
        with patch("unswbc.run._resolve", return_value=(["/x/bot.wasm"], Path("/x"), "wasm")):
            self.assertEqual(run.build_bots([A], True), {A: Path("/x/bot.wasm")})

    def test_a_host_of_another_platform_gets_source_folders_and_a_prebuilt_executable_plays_here(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a, b, prebuilt = Path(directory, "a"), Path(directory, "b"), Path(directory, "prebuilt")
            a.mkdir(), b.mkdir(), prebuilt.touch()
            host = FakeHost(platform="Linux x86_64")
            s, slots = scheduler([host], slots=0)
            s.native = {a: Path("a.bin"), b: Path("b.bin"), prebuilt: prebuilt}
            jobs = [run.Job(Path("m0"), a, b, "B"), run.Job(Path("m1"), prebuilt, b, "A")]
            with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                    patch.object(run, "parse_game", fake_parse):
                list(s.play(jobs, TMP))
            self.assertEqual(host.calls, [("m0", (b, a))])
            self.assertEqual(s.played_here, 1)

    def test_same_platform_runs_skip_hosts_of_another_platform(self) -> None:
        linux, mac = FakeHost(platform="Linux x86_64"), FakeHost()
        s, slots = scheduler([linux, mac], slots=0)
        s.same_platform = True
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(3)]
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse):
            self.assertEqual(list(s.play(jobs, TMP)), ["m0-A", "m1-A", "m2-A"])
        self.assertEqual((len(linux.calls), len(mac.calls)), (0, 3))

    def test_stopping_early_reads_no_more_jobs_than_slots_allow(self) -> None:
        read = []

        def jobs():
            for i in range(100):
                read.append(i)
                yield run.Job(Path(f"m{i}"), A, B, "A")

        s, slots = scheduler([], slots=2)
        s.ahead = 4
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", fake_parse):
            games = s.play(jobs(), TMP)
            self.assertEqual(next(games), "m0-A")
            games.close()
        self.assertLessEqual(len(read), 5)  # the bound plus the one job read ahead
        self.assertTrue(s.stopped)

    # E0163's SPRT idled up to 60 of its 60 slots every other minute: once a game at the head of the queue ran long,
    # 2 x (cores + hosts' max) games finished behind it and nothing new started until it ended.
    def test_games_keep_starting_behind_a_slow_head_game_and_results_stay_in_job_order(self) -> None:
        cores, slots_n = 2, 2
        behind = 4 * cores  # twice the old bound's 2 x cores games, well within AHEAD x cores
        started, parsed, lock = [], [], threading.Lock()
        released = threading.Event()

        def game(job, sandbox, path, started_hook=None, nice=0, watch=False, native=None):
            with lock:
                started.append(job.map.name)
                if len(started) > behind:
                    released.set()
            if job.map.name == "m0":
                self.assertTrue(released.wait(timeout=10), f"only {started} started while the head game ran")
            return raw(job)

        def parse(job, game):
            with lock:
                parsed.append(job.map.name)
            return game.stdout

        with patch.object(run.os, "cpu_count", return_value=cores):
            s, slots = scheduler([], slots=slots_n)
        jobs = [run.Job(Path(f"m{i}"), A, B, "A") for i in range(behind + 2)]
        with slots, patch.object(run, "run_game", game), patch.object(run, "parse_game", parse):
            results = list(s.play(jobs, TMP))
        self.assertEqual(results, [f"m{i}-A" for i in range(behind + 2)])
        self.assertLessEqual(behind + 1, s.ahead)
        # the games that finished behind the head were parsed as they ended, so none holds its replay's bytes
        self.assertGreaterEqual(parsed.index("m0"), behind - 1)

    def test_pairs_group_both_sides_of_each_map(self) -> None:
        s, slots = scheduler([], slots=4)
        with slots, patch.object(run, "Scheduler", lambda *a: s), patch.object(run, "build_bots", lambda *a: NATIVE), \
                patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", lambda job, game: run.GameResult(
                    str(job.map), job.a_side, "a", "rounds", 500, None, None, 0.0, None)):
            pairs = [(m.name, x.a_side, y.a_side) for m, x, y in run.stream_pairs(A, B, [Path("m0"), Path("m1")])]
        self.assertEqual(pairs, [("m0", "A", "B"), ("m1", "A", "B")])



def result(job: run.Job, winner: str = "a") -> run.GameResult:
    return run.GameResult(str(job.map), job.a_side, winner, "rounds", 500, run.BotStats(1, 2, 3), run.BotStats(4, 5, 6),
                          1.0, None)


# Jobs predicted from a recording (record.check is faked: `identical` says how every check comes out, and audits
# match unless `audits_match` is False).
class TestPrediction(unittest.TestCase):
    def setUp(self) -> None:
        for p in (patch.object(run, "seeded_toolkit", return_value=False), patch("sys.stderr")):
            p.start()
            self.addCleanup(p.stop)
        self.played: list[str] = []

    def job(self, name: str, audit: bool = False, winner: str = "a") -> run.Job:
        job = run.Job(Path(name), A, B, "A")
        return run.replace(job, predict=run.Prediction(TMP / f"{name}.zst", result(job, winner), audit))

    def play(self, jobs: list[run.Job], identical: bool = True, games_won_by: str = "a", audits_match: bool = True,
             slots: int = 2, check=None) -> tuple:
        def game(job, *_):
            self.played.append(job.map.name)
            return raw(job)

        done = run.record.Check(identical, 10, 10 if identical else 3, 0.5, None if identical else "round 2, dragon 0")
        s, local = scheduler([], slots=slots)
        same = staticmethod(lambda prediction, got, recording: audits_match and got.winner == prediction.result.winner)
        with local, patch.object(run, "run_game", game), \
                patch.object(run.record, "check", check or (lambda *a: done)), \
                patch.object(run.Scheduler, "same_game", same), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job, games_won_by)):
            results = list(s.play(jobs, TMP))
        return results, s.stats

    def test_an_identical_check_reuses_the_recorded_result_once_one_audit_passed(self) -> None:
        results, stats = self.play([self.job("m0"), self.job("m1"), self.job("m2")])
        self.assertEqual(self.played, ["m0"])  # the run's first identical game is always audited
        self.assertEqual([(r.map, r.predicted, r.seconds) for r in results],
                         [("m0", False, 1.0), ("m1", True, 0.0), ("m2", True, 0.0)])
        self.assertEqual((stats.checks, stats.identical, stats.predicted, stats.audited, stats.check_cpu),
                         (3, 3, 2, 1, 1.5))
        self.played[:] = []
        results, stats = self.play([self.job("m0")], identical=False)
        self.assertEqual((self.played, results[0].predicted, stats.predicted), (["m0"], False, 0))

    def test_an_audit_that_differs_turns_prediction_off_for_every_later_job_and_alerts(self) -> None:
        remote.ALERTS.unlink(missing_ok=True)
        # the audit's game is won by b, the recording said a: something isn't deterministic
        jobs = [self.job("m0", audit=True), self.job("m1"), self.job("m2")]
        results, stats = self.play(jobs, games_won_by="b")
        self.assertEqual(self.played, ["m0", "m1", "m2"])
        self.assertEqual([r.predicted for r in results], [False, False, False])
        self.assertEqual((stats.audited, stats.audit_mismatches, stats.predicted), (1, 1, 0))
        self.assertIn("audit mismatch on m0", stats.off)
        self.assertIn("WARNING predicted games: audit mismatch on m0", remote.ALERTS.read_text())

    def test_an_audit_is_recorded_on_this_platform_and_compared(self) -> None:
        seen = []
        compare = staticmethod(lambda prediction, got, recording: seen.append(recording) or True)
        with patch.object(run.Scheduler, "same_game", compare):
            s, local = scheduler([], slots=2)
            check = run.record.Check(True, 10, 10, 0.5, None)
            with local, patch.object(run, "run_game", lambda job, *_: self.played.append(job.record) or raw(job)), \
                    patch.object(run.record, "check", return_value=check), \
                    patch.object(run, "parse_game", lambda job, raw_game: result(job)):
                results = list(s.play([self.job("m0", audit=True), self.job("m1")], TMP))
        self.assertEqual(self.played, seen)  # the audit game recorded to the file it was compared on
        self.assertEqual(seen[0].parent, TMP)
        self.assertEqual([r.predicted for r in results], [False, True])

    def test_prediction_stops_when_too_few_checks_are_identical(self) -> None:
        with patch.object(run, "FLOOR_CHECKS", 3), patch.object(run, "CHECKS", 1):
            results, stats = self.play([self.job(f"m{i}") for i in range(20)], identical=False)
        self.assertIn("only 0 of the first 3 checks", stats.off)
        self.assertLessEqual(stats.checks, 4)  # the floor, and at most the one check already running then
        self.assertEqual(len(self.played), 20)

    def test_checks_take_local_slots_and_a_job_waiting_for_one_is_played_after_a_while(self) -> None:
        release = threading.Event()

        def slow_check(*a):
            release.wait(5)
            return run.record.Check(True, 10, 10, 0.5, None)

        # one local slot: the first job's check holds it, so the second can neither be checked nor played here yet
        s, local = scheduler([], slots=1)
        with local:
            self.assertTrue(s.check_slot())
            s.local = 1
            self.assertFalse(s.check_slot())  # a local game uses the only slot, whatever CHECKS allows
        with patch.object(run, "CHECK_WAIT_S", 0.2):
            timer = threading.Timer(1.0, release.set)
            timer.start()
            results, stats = self.play([self.job("m0"), self.job("m1"), run.Job(Path("p2"), A, B, "A")], slots=1,
                                       check=slow_check)
        self.assertEqual([r.map for r in results], ["m0", "m1", "p2"])
        self.assertEqual(stats.checks, 1)  # m1 waited past CHECK_WAIT_S for a check and was played instead
        self.assertIn("m1", self.played)

    def test_jobs_needing_no_check_start_while_every_check_is_busy(self) -> None:
        release = threading.Event()

        def slow_check(*a):
            release.wait(5)
            return run.record.Check(False, 10, 3, 0.5, "round 2, dragon 0")

        started = threading.Event()

        def game(job, *_):
            if job.map.name == "p1":
                started.set()
                release.set()  # only the plain game can free the checks: it must start while they wait
            return raw(job)

        s, local = scheduler([], slots=3)
        with local, patch.object(run, "CHECKS", 1), patch.object(run, "run_game", game), \
                patch.object(run.record, "check", slow_check), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            results = list(s.play([self.job("m0"), run.Job(Path("p1"), A, B, "A")], TMP))
        self.assertTrue(started.is_set())
        self.assertEqual([r.map for r in results], ["m0", "p1"])

    def test_an_audit_with_no_recording_is_inconclusive_and_the_next_identical_game_is_audited(self) -> None:
        # real comparisons: each prediction's recording exists with its digest; only m1's audit game records one
        recording = run.record.Recording("A", {0: "ID 0"}, [(0, "ROUND 0\n", "d")])
        jobs = []
        for name in ("m0", "m1", "m2"):
            recording.save(TMP / f"{name}.zst")
            job = self.job(name)
            jobs.append(run.replace(job, predict=run.replace(
                job.predict, digest=run.record.file_digest((TMP / f"{name}.zst").read_bytes()))))

        def game(job, *_):
            self.played.append(job.map.name)
            if job.record and job.map.name == "m1":
                recording.save(job.record)
            return raw(job)

        s, local = scheduler([], slots=2)
        check = run.record.Check(True, 10, 10, 0.5, None)
        with local, patch.object(run, "run_game", game), patch.object(run.record, "check", return_value=check), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            results = list(s.play(jobs, TMP))
        self.assertEqual(self.played, ["m0", "m1"])  # m0's audit had nothing to compare, so m1 was audited too
        self.assertEqual([r.predicted for r in results], [False, False, True])
        self.assertEqual((s.stats.audited, s.stats.audit_mismatches, s.stats.off), (1, 0, None))

    def test_a_replaced_recording_is_never_compared_against(self) -> None:
        recording = run.record.Recording("A", {0: "ID 0"}, [(0, "ROUND 0\n", "d")])
        recording.save(TMP / "cached.zst")
        prediction = run.Prediction(TMP / "cached.zst", result(run.Job(Path("m0"), A, B, "A")), True, "0" * 16)
        recording.save(TMP / "audit.zst")
        self.assertIsNone(run.Scheduler.same_game(prediction, prediction.result, TMP / "audit.zst"))
        right = run.replace(prediction, digest=run.record.file_digest((TMP / "cached.zst").read_bytes()))
        self.assertTrue(run.Scheduler.same_game(right, right.result, TMP / "audit.zst"))
        self.assertFalse(run.Scheduler.same_game(right, result(run.Job(Path("m0"), A, B, "A"), "b"), TMP / "audit.zst"))

    def test_a_remote_recording_that_cannot_be_written_is_skipped(self) -> None:
        mac = FakeHost()
        mac.play = lambda *a, **k: remote.RemoteGame("", 0, b"REPLAY", b"RECORDING")
        locked = TMP / "locked"
        locked.mkdir()
        locked.chmod(0o500)  # the recording can't be written there
        self.addCleanup(locked.chmod, 0o700)
        s, local = scheduler([mac], slots=0)
        job = run.Job(Path("m0"), A, B, "A", record=locked / "m0.zst")
        with local, patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            (got,) = s.play([job], TMP)
        self.assertEqual((got.map, got.recording), ("m0", None))  # the game counts; nothing to pair its row with
        self.assertEqual(list(locked.iterdir()), [])

    def test_a_game_whose_old_recording_cannot_be_cleared_plays_unrecorded(self) -> None:
        blocked = TMP / "not-a-folder"
        blocked.write_text("a file where the recordings folder would go")
        seen = []
        s, local = scheduler([], slots=1)
        with local, patch.object(run, "run_game", lambda job, *_: seen.append(job.record) or raw(job)), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            (got,) = s.play([run.Job(Path("m0"), A, B, "A", record=blocked / "m0.zst")], TMP)
        self.assertEqual((seen, got.recording), ([None], None))

    def test_a_game_that_records_drops_an_older_recording_first(self) -> None:
        stale = TMP / "stale.zst"
        stale.write_text("an older game's recording")
        s, local = scheduler([], slots=1)
        with local, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            list(s.play([run.Job(Path("m0"), A, B, "A", record=stale)], TMP))
        self.assertFalse(stale.exists())  # this game recorded nothing, so nothing may pair with its row

    def test_games_that_record_stay_on_this_platform(self) -> None:
        linux, mac = FakeHost(platform="Linux x86_64"), FakeHost()
        s, slots = scheduler([linux, mac], slots=0)
        jobs = [run.Job(Path(f"m{i}"), A, B, "A", record=TMP / f"m{i}.zst") for i in range(3)]
        with slots, patch.object(run, "run_game", lambda job, *_: raw(job)), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            list(s.play(jobs, TMP))
        self.assertEqual((len(linux.calls), len(mac.calls)), (0, 3))

    def test_a_game_waiting_for_this_platform_does_not_hold_up_the_games_behind_it(self) -> None:
        started = []

        def slow(job, *_):
            started.append(job.map.name)
            time.sleep(0.3)
            return raw(job)

        linux = FakeHost(platform="Linux x86_64")
        linux.play = lambda game_map, *a, **k: started.append(game_map.name) or remote.RemoteGame("", 0, None)
        s, slots = scheduler([linux], slots=1)
        jobs = [run.Job(Path("r0"), A, B, "A", record=TMP / "r0.zst"),
                run.Job(Path("r1"), A, B, "A", record=TMP / "r1.zst"), run.Job(Path("p2"), A, B, "A")]
        with slots, patch.object(run, "run_game", slow), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            self.assertEqual([r.map for r in s.play(jobs, TMP)], ["r0", "r1", "p2"])
        self.assertEqual(started, ["r0", "p2", "r1"])  # p2 went to the Linux host while r1 waited for this Mac


    def test_once_a_game_that_records_finds_no_slot_the_others_are_not_asked_that_pass(self) -> None:
        # Recording entries that can't start stay in `running` (up to `ahead` of them); each asked free_slot, and so
        # game_slots (native.py's gated version runs vm_stat), every pass: O(n^2) asks for n of them.
        asks = []

        def slots(workers, active):
            asks.append(active)
            return 1

        def game(job, *_):
            time.sleep(0.02)  # each game's end is a pass, which asked for every recording entry still waiting
            return raw(job)

        jobs = [run.Job(Path(f"r{i}"), A, B, "A", record=TMP / f"r{i}.zst") for i in range(30)]
        s, _ = scheduler([])
        with patch.object(run, "game_slots", slots), patch.object(run, "run_game", game), \
                patch.object(run, "parse_game", lambda job, raw_game: result(job)):
            self.assertEqual([r.map for r in s.play(jobs, TMP)], [f"r{i}" for i in range(30)])
        self.assertLess(len(asks), 4 * len(jobs))  # about 3 a game; 900 before


class TestSandboxCopies(unittest.TestCase):
    # The toolkit keys its wasm cache on the folder name and .cpp files, so a header-only change must rename the copy.
    def test_a_header_change_gets_its_own_copy_and_build_folders_are_left_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = []
            for header in ("int a = 1;", "int a = 1;", "int a = 2;"):
                bot = root / "src" / "cand"
                bot.mkdir(parents=True, exist_ok=True)
                (bot / "main.cpp").write_text('#include "params.hpp"\n')
                (bot / "params.hpp").write_text(header)
                (bot / ".unswbc-build").mkdir(exist_ok=True)
                (bot / ".unswbc-build/old.wasm").write_text(header)
                copy = run.content_named_copy(bot, root / "copies")
                names.append(copy.name)
                self.assertEqual((copy / "params.hpp").read_text(), header)
                self.assertFalse((copy / ".unswbc-build").exists())
            self.assertEqual(names[0], names[1])
            self.assertNotEqual(names[1], names[2])
            self.assertTrue(names[2].startswith("cand-"))


class TestOneSide(unittest.TestCase):
    # Rule counts play each map once per opponent: one game a map, a's side alternating so both sides are covered.
    def test_each_map_is_played_once_with_a_alternating_sides(self):
        played = []

        def games(jobs, workers, sandbox, replays=None):
            played.extend(jobs)
            return [run.GameResult(str(j.map), j.a_side, "a", "roundLimit", 500, None, None, 0.0, None) for j in jobs]

        out = TMP / "one-side.jsonl"
        argv = ["run.py", "--a", "a", "--b", "b", "--maps", "m0", "m1", "m2", "--one-side", "--out", str(out)]
        with patch.object(run, "play_games", games), patch.object(run, "play_pairs", side_effect=AssertionError), \
                patch.object(run.sys, "argv", argv), patch("builtins.print"):
            self.assertEqual(run.main(), 0)
        self.assertEqual([(j.map.name, j.a, j.b, j.a_side) for j in played],
                         [("m0", A, B, "A"), ("m1", A, B, "B"), ("m2", A, B, "A")])
        self.assertEqual(len(out.read_text().splitlines()), 3)


class TestIdenticalPlay(unittest.TestCase):
    # Writes a one-move game; only the move may decide whether two games match.
    def replay(self, folder: Path, name: str, move: str, log: str, instructions: int) -> Path:
        message = run.REPLAY_SCHEMA.Replay.new_message(map="MAP 4 4\n", botA=name, botB=name)
        events = message.init("events", 2)
        action = events[0].init("dragonAction")
        action.id = 1
        action.action.init("move", 1)[0] = move
        action.instructions.count = instructions
        events[1].init("dragonLog").text = log
        message.result.terminated = True
        message.result.endReason = "roundLimit"
        path = folder / f"{name}-{move}-{instructions}.replay"
        path.write_bytes(message.to_bytes_packed())
        return path

    def test_the_check_keeps_its_games_on_this_platform(self):
        import identical
        seen = {}

        def games(jobs, bots, workers, sandbox, replays=None, same_platform=False):
            seen["same_platform"] = same_platform
            return [run.GameResult(str(j.map), "A", None, None, None, None, None, 0.0, "skipped") for j in jobs]

        with patch.object(identical, "stream_games", games):
            passed, _, _ = identical.check(Path("cand"), Path("base"), [Path("m0")], "auto")
        self.assertEqual((passed, seen["same_platform"]), (False, True))

    def test_bot_names_logs_and_cpu_readings_do_not_count_but_moves_do(self):
        import identical
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            base = identical.play_digest(self.replay(folder, "champion", "north", "thinking", 900))
            self.assertEqual(identical.play_digest(self.replay(folder, "cand-1f2e", "north", "other log", 300)), base)
            self.assertNotEqual(identical.play_digest(self.replay(folder, "champion", "east", "thinking", 900)), base)


class TestStartupFailure(unittest.TestCase):
    def test_every_bot_of_both_teams_failing_at_round_0_is_the_hosts_fault(self) -> None:
        # The first Windows host: bots couldn't load their runtime DLLs and every game was a round-1 draw.
        log = ("loaded c/c++ vs c/c++\nround 0: bot 0 (team A) exited with code 3221226684\n"
               "round 0: bot 0 (team A) died: no valid action\nround 0: bot 1 (team B) exited with code 3221226684\n"
               "round 0: bot 1 (team B) died: no valid action\ndraw after 1 rounds (both teams eliminated) (0.0s)\n")
        self.assertTrue(run.startup_failure(log))
        one_side = log.replace("bot 1 (team B) exited with code 3221226684", "bot 1 (team B) died: hit a wall")
        self.assertFalse(run.startup_failure(one_side))  # only one team's bots failed: a real crash to record
        later = log.replace("draw after 1 rounds", "draw after 40 rounds")
        self.assertFalse(run.startup_failure(later))


class TestSeeds(unittest.TestCase):
    def test_every_game_on_a_map_shares_one_pearl_seed_when_the_toolkit_takes_one(self) -> None:
        # unswbc 1.1.0 seeds pearls randomly per game unless told; paired games must share a schedule.
        folder = TMP
        (folder / "a.map").write_text("MAP 8 8\n")
        (folder / "b.map").write_text("MAP 9 9\n")
        with patch.object(run, "seeded_toolkit", return_value=True):
            first, again, other = (run.game_seed(folder / n) for n in ("a.map", "a.map", "b.map"))
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertTrue(0 <= first < 1 << 64)
        with patch.object(run, "seeded_toolkit", return_value=False):
            self.assertIsNone(run.game_seed(folder / "a.map"))  # a 1.0.x toolkit has no --seed

    def test_a_failed_seed_check_is_asked_again(self) -> None:
        # E0183's check, 2026-09-28: the answer was cached, so one timed-out `unswbc run --help` left a whole
        # process unseeded and its paired games on different pearl schedules.
        answers = [subprocess.TimeoutExpired("unswbc", 60), subprocess.CompletedProcess([], 0, "  --seed N\n", "")]
        def help_(*args, **kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with patch.object(run, "_SEEDED", None), patch.object(run.subprocess, "run", side_effect=help_):
            self.assertFalse(run.seeded_toolkit())
            self.assertTrue(run.seeded_toolkit())
            self.assertTrue(run.seeded_toolkit())  # now kept: asked twice in all
        self.assertEqual(answers, [])

    def test_a_failed_seed_check_is_said_once(self) -> None:
        # An unseeded game's pair may not share pearls, so the failure is said, but once, not per game.
        with patch.object(run, "_SEEDED", None), patch.object(run, "_SEED_CHECK_FAILED", False), \
                patch.object(run.subprocess, "run", side_effect=OSError("no unswbc")), \
                contextlib.redirect_stderr(io.StringIO()) as said:
            self.assertFalse(run.seeded_toolkit())
            self.assertFalse(run.seeded_toolkit())
        self.assertEqual(said.getvalue().count("WARNING seed check:"), 1, said.getvalue())
        self.assertIn("no unswbc", said.getvalue())


# E0485 (1 Oct): the toolkit's update notice gave a pip line ("  <python> -m pip install --upgrade unswbc") that one
# way printed and the other didn't, so the native judge's check failed on a game that played the same.
class NormalLogNotes(unittest.TestCase):
    def test_update_notice_lines_are_dropped(self):
        out = ("unswbc 1.2.1 is out (you have 1.2.0); update with:\n"
               "  /Users/x/.venv/bin/python -m pip install --upgrade unswbc\nround 1\n")
        self.assertEqual(run.normal_log(out), ["round 1"])


if __name__ == "__main__":
    unittest.main()
