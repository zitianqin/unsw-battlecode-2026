"""The idle prewarm (loop/baselines.py): it fills the very map set a test against the champion uses, in the SPRT's
order, plays every stage left as one stream of games (the referee's own scheduler starts a stage's games while the one
before still plays), pauses between stages when an evaluation plays on the hosts, is killed by a stop marker or a new
champion, resumes where it stopped, reads the cache and prediction again each stage, keeps recorded games from being
queued far past their results, starts only when sweep.busy allows (which lets a tune playing its games through, tested
in test_sweep), rewrites a paused reason that changed, and health.py alerts when it keeps failing, hangs or its plan
breaks. The screen opponents are the repo's real gauntlet.toml
and the games' results a real referee row's (testdata/sweep-game.jsonl)."""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import os
import shutil
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import baselines
import driver
import health
import sweep

sys.path.insert(0, str(driver.REPO / "referee"))
import run as referee  # noqa: E402  the referee's Job, GameResult and scheduler, as play() uses them
import sprt  # noqa: E402

GAME = json.loads((Path(__file__).resolve().parent / "testdata/sweep-game.jsonl").read_text())
CHAMPION = "1c225be954742d086a32502a5a30a0a159d95c69"
EPOCH = "unswbc-1.1.0"
MAPS = 700  # past baselines.EARLY, so rotation trials follow the screen's stages
PERIOD = 1 + len(driver.ROTATION)  # the SPRT's seats: the champion, then each rotation opponent


# The rotation games of trials first to last - 1: each opponent trial, both sides (a stage of STEP trials holds 82 or 84
# with five opponents, since STEP is no multiple of PERIOD).
def rotation_games(first: int, last: int) -> int:
    return 2 * sum(1 for i in range(first, last) if i % PERIOD)


PER_STAGE = rotation_games(0, baselines.STEP)  # the first stage's games


def clear_home() -> None:
    for path in driver.STATE.iterdir():
        shutil.rmtree(path) if path.is_dir() else path.unlink()


# The referee's result of `job`, a real row's numbers, or failed with `error`.
def result(job, error: str | None = None) -> referee.GameResult:
    stats = {key: referee.BotStats(**GAME[key]) for key in ("a", "b")}
    return referee.GameResult(**{**GAME, **stats, "map": str(job.map), "a_side": job.a_side, "error": error})


class Prewarm(unittest.TestCase):
    def setUp(self) -> None:
        clear_home()
        self.addCleanup(clear_home)
        referee_copy = driver.STATE / "referee-copy"
        (referee_copy / "referee").mkdir(parents=True)
        shutil.copy(driver.REPO / "referee/gauntlet.toml", referee_copy / "referee/gauntlet.toml")
        (referee_copy / "referee/sprt.py").write_text('parser.add_argument("--baselines")')  # one that shares sets
        (referee_copy / "mapgen").mkdir()
        shutil.copy(driver.REPO / "mapgen/gen.py", referee_copy / "mapgen/gen.py")  # its styles (referee_styles)
        self.calls: list[tuple[str, list[str], object]] = []  # driver.referee's
        self.streams: list[list] = []  # the jobs of each stream_games call
        self.busy: list[str | None] = []  # sweep.busy's answers in turn, then None
        self.failing: set[str] = set()  # opponents whose games fail
        for target, value in (("REFEREE", referee_copy), ("SHARED_MAPS", driver.STATE / "shared-maps")):
            patcher = patch.object(driver, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, effect in (("generate_maps", self.generate), ("referee", self.referee),
                               ("gauntlet_bot", self.bot), ("log", lambda text: None)):
            patcher = patch.object(driver, target, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(sweep, "busy", side_effect=lambda *args: self.busy.pop(0) if self.busy else None),
                        patch.object(sprt, "predicting", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        driver.IDEAS.write_text(json.dumps({"id": "I001", "category": "split", "priority": 3, "title": "t",
                                            "hypothesis": "h", "status": "todo"}) + "\n")
        baselines.save(driver.STATE, {"champion": CHAMPION, "epoch": EPOCH, "toolchain": {"epoch": EPOCH}})

    # MAPS maps at most of `style` (of a mix's, `count`), numbered from 0 in their own folder.
    def generate(self, out: Path, seed: int, count: int, style: str = "eval") -> None:
        self.generated = (out, seed, count)
        out.mkdir(parents=True, exist_ok=True)
        for i in range(min(count, MAPS)):
            (out / f"gen_{style}_{i:03d}.map").write_text(f"MAP {i}")

    def bot(self, spec: dict, into: Path | None = None) -> Path:
        folder = (into or driver.STATE / "gauntlet") / spec["name"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "main.cpp").write_text(spec["name"])  # the cache keys a bot by its sources
        return folder

    # driver.referee: `baselines.py play PLAN` runs here, its games from the fake stream (stream). Its `stop`, kept in
    # self.stop, ends the run as driver.stoppable's SIGTERM does (native.play_script exits), returning None.
    def referee(self, script, args, **kwargs):
        self.calls.append((Path(script).name, args, kwargs.get("yielding")))
        self.stop = kwargs.get("stop")
        out, err = io.StringIO(), io.StringIO()
        try:
            with patch.object(referee, "stream_games", self.stream), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = baselines.play(Path(args[1]))
        except SystemExit:
            return None
        return driver.subprocess.CompletedProcess([], code, out.getvalue(), err.getvalue())

    def stream(self, jobs, bots, workers, sandbox):
        self.streams.append([])
        for job in jobs:
            self.streams[-1].append(job)
            yield result(job, "engine error" if job.b.name in self.failing else None)

    def folder(self) -> Path:
        return driver.STATE / "shared-maps" / f"{CHAMPION[:12]}-{EPOCH}"

    # A test claimed now plays the shared set of today's map mix (driver.shared_mix), which tick records for the run.
    def test_it_fills_the_map_set_the_tests_against_the_champion_use(self) -> None:
        baselines.save(driver.STATE, {"champion": CHAMPION, "epoch": EPOCH, "toolchain": {"epoch": EPOCH},
                                      "maps": driver.shared_mix()})
        with patch.object(driver, "MAX_PAIRS", MAPS):  # a set no bigger than the fake generator's
            exp = driver.Experiment(id="E1", idea={}, lane=1)
            exp.evaluation["toolchain"] = {"epoch": EPOCH}
            maps = driver.evaluation_maps(exp, CHAMPION)
            tested = self.generated
            baselines.run(driver.REPO, driver.STATE)
        self.assertEqual(self.generated, tested)  # the same folder, seed and count
        self.assertTrue(exp.evaluation["shared_maps"].endswith(driver.meta.shared_set_name(CHAMPION, EPOCH,
                                                                                            driver.shared_mix())))
        self.assertEqual({Path(m).parent.name for m in maps}, {"eval", *driver.shared_mix()})
        first = self.streams[0][:PER_STAGE]
        rotation = [None, *driver.ROTATION]  # the SPRT's order: trial i plays rotation[i % PERIOD], the champion none
        self.assertEqual([(str(job.map), job.b.name) for job in first[::2]],
                         [(m, rotation[i % PERIOD]) for i, m in enumerate(maps[:baselines.STEP]) if i % PERIOD])
        cache = sprt.BaselineCache(Path(exp.evaluation["shared_maps"]) / "baselines.jsonl")  # what the tests read
        self.assertTrue(all(cache.get(job) for job in first))

    def test_every_stage_left_plays_as_one_stream_in_order_and_the_screen_skips_the_rotation(self) -> None:
        baselines.run(driver.REPO, driver.STATE)
        record = baselines.read(driver.STATE)
        self.assertTrue(record["finished_at"])
        names = [name for name, _ in driver.rotation_bots(list(driver.ROTATION))]
        self.assertEqual(sorted(record["done"]), sorted(baselines.stages(names, self.screen_names(),
                                                                         driver.POOL_PAIRS)))
        # One referee run, one stream: until 2026-09-28 each stage was a run of its own, and the hosts drained between.
        self.assertEqual([(script, yielding) for script, _, yielding in self.calls], [("baselines.py", "filler")])
        (jobs,) = self.streams
        kinds = ["rotation" if job.b.name in driver.ROTATION else "screen" for job in jobs]
        early = rotation_games(0, baselines.EARLY)
        # The screen's stages come between the first 600 trials and the rest.
        self.assertEqual((kinds[:early], kinds[early]), (["rotation"] * early, "screen"))
        self.assertEqual(kinds[-1], "rotation")
        screens = {job.b.name for job in jobs if job.b.name not in driver.ROTATION}
        self.assertEqual(screens, set(self.screen_names()) - set(driver.ROTATION))  # check_pool counts the SPRT's
        all_maps = sorted(str(m) for m in self.folder().glob("maps/*.map"))
        folder_maps = [str(job.map) for job in jobs if job.b.name == "folder"]
        self.assertEqual(folder_maps[::2], all_maps[:driver.POOL_PAIRS])
        known = driver.screen_baselines(self.folder(), driver.STATE / "gauntlet/folder", jobs[0].a)
        self.assertEqual(len(known), 2 * driver.POOL_PAIRS)  # what the next screen reads
        self.calls, self.streams = [], []
        baselines.update(driver.STATE, CHAMPION, finished_at=None)
        baselines.run(driver.REPO, driver.STATE)  # every stage done: nothing is played again
        self.assertEqual(self.calls, [])

    def test_it_pauses_between_stages_for_an_evaluation_and_resumes_where_it_stopped(self) -> None:
        baselines.update(driver.STATE, CHAMPION, pid=7)
        # run's check, then play's before each stage: the evaluation starts during the second stage, and the run
        # waits it out in place (sweep.wait_idle; its first check and one wait), pid kept, then plays the rest.
        hosts = sweep.ON_HOSTS
        self.busy = [None, None, None, hosts, hosts, hosts]
        seen = []
        with patch.object(sweep.time, "sleep", lambda s: seen.append(baselines.read(driver.STATE))):
            baselines.run(driver.REPO, driver.STATE)
        step = baselines.STEP
        self.assertEqual([(r["done"], r["paused"], r["pid"]) for r in seen],
                         [([f"rotation:{step}", f"rotation:{2 * step}"], hosts, 7)])
        record = baselines.read(driver.STATE)
        self.assertEqual((record["paused"], bool(record.get("finished_at"))), (None, True))
        played = len(self.streams[0])
        self.assertEqual(len(self.calls), 2)
        first = self.streams[1][0]  # the third stage's first opponent trial (a trial i % PERIOD == 0 is the champion's)
        third = next(i for i in range(2 * step, 3 * step) if i % PERIOD)
        self.assertEqual(Path(first.map).name, f"gen_eval_{third:03d}.map")
        self.assertEqual(played, rotation_games(0, 2 * step))

    def test_a_game_stopped_mid_stage_is_kept_and_not_played_again(self) -> None:
        stopped = iter(range(10))

        def stream(jobs, bots, workers, sandbox):  # a run that dies after 10 games (a host lost, a crash)
            self.streams.append([])
            for job in jobs:
                if next(stopped, None) is None:
                    raise RuntimeError("killed")
                self.streams[-1].append(job)
                yield result(job)

        with patch.object(self, "stream", stream):
            baselines.run(driver.REPO, driver.STATE)
        record = baselines.read(driver.STATE)
        self.assertEqual((record.get("done"), record["failures"]), (None, 1))
        kept = self.streams[0]
        self.streams = []
        baselines.run(driver.REPO, driver.STATE)
        self.assertFalse({(str(j.map), j.b, j.a_side) for j in kept} & {(str(j.map), j.b, j.a_side)
                                                                         for j in self.streams[0]})

    # A stop marker or a new champion kills the run (driver.referee's stop, every STOP_CHECK_S) rather than letting its
    # queued games play on: with recording on, up to run.AHEAD x every slot of them, 45-90 min, during which a new
    # champion's prewarm couldn't start. The killed run is paused, not failed, and the next plays only what wasn't kept.
    def test_a_stop_marker_or_a_new_champion_kills_the_run_and_the_next_resumes(self) -> None:
        with patch.object(baselines.meta, "champion_commit", return_value=CHAMPION):
            baselines.update(driver.STATE, CHAMPION, pid=7)
            games = iter(range(30))

            def stream(jobs, bots, workers, sandbox):
                self.streams.append([])
                for job in jobs:
                    if next(games, None) is None:
                        driver.STOP.write_text("sig\n")
                    if self.stop():
                        raise SystemExit(143)  # native.play_script's SIGTERM handler
                    self.streams[-1].append(job)
                    yield result(job)

            with patch.object(self, "stream", stream):
                baselines.run(driver.REPO, driver.STATE)
            record = baselines.read(driver.STATE)
            self.assertEqual((record["paused"], record["pid"], record.get("failures"), record.get("finished_at")),
                             ("a stop marker is set", None, None, None))
            kept = {(str(j.map), j.b, j.a_side) for j in self.streams[0]}
            self.assertEqual(len(kept), 30)
            driver.STOP.unlink()
            self.streams = []
            baselines.run(driver.REPO, driver.STATE)
            self.assertTrue(baselines.read(driver.STATE)["finished_at"])
            self.assertFalse(kept & {(str(j.map), j.b, j.a_side) for j in self.streams[0]})
            self.assertFalse(self.stop())
        with patch.object(baselines.meta, "champion_commit", return_value="f" * 40):
            self.assertTrue(self.stop())  # the champion moved
            self.assertEqual(baselines.halted(driver.REPO, CHAMPION), "the champion moved")
        with patch.object(baselines.meta, "champion_commit", return_value=""):  # git failed to answer: no move
            self.assertIsNone(baselines.halted(driver.REPO, CHAMPION))

    # With prediction on (the champion's test copy plays the fixed key, MISSES 99) every rotation game records, and
    # the referee queues a recorded game that finds no slot on this Mac's platform instead of holding it back, up to
    # run.AHEAD x every slot: this stream pulls as greedily. Until the fix every stage's busy check ran before the first
    # result, so an evaluation starting after 100 games still let about 924 more play.
    def test_recorded_games_are_not_queued_far_past_their_results(self) -> None:
        ahead, results = 924, [0]  # run.AHEAD x (this Mac's cores + every host's max) on 28 Sep

        def stream(jobs, bots, workers, sandbox):
            self.streams.append([])
            queue, pending = [], iter(jobs)
            while True:
                while len(queue) < ahead and (job := next(pending, None)) is not None:
                    self.streams[-1].append(job)
                    queue.append(job)
                if not queue:
                    return
                results[0] += 1
                yield result(queue.pop(0))

        def busy(*args):  # a stop marker, as an evaluation on the hosts is waited out in place (sweep.wait_idle)
            return "a stop marker is set" if results[0] >= 100 else None

        with patch.object(self, "stream", stream), patch.object(sweep, "busy", side_effect=busy), \
                patch.object(sprt, "predicting", return_value=True):
            baselines.run(driver.REPO, driver.STATE)
        record = baselines.read(driver.STATE)
        played = [job for jobs in self.streams for job in jobs]
        self.assertLessEqual(len(played), baselines.RECORD_AHEAD + PER_STAGE)
        self.assertEqual(record["paused"], "a stop marker is set")
        self.assertTrue(all(job.record for job in played))
        self.assertGreater(len(self.streams), 1)  # the stream drained at the cap and the next went on
        results[0] = -10 ** 6  # the marker gone: the rest plays, nothing twice
        with patch.object(self, "stream", stream), patch.object(sweep, "busy", side_effect=busy), \
                patch.object(sprt, "predicting", return_value=True):
            baselines.run(driver.REPO, driver.STATE)
        again = [(str(j.map), j.b, j.a_side) for jobs in self.streams for j in jobs]
        self.assertEqual(len(again), len(set(again)))
        self.assertTrue(baselines.read(driver.STATE)["finished_at"])

    # The cache and prediction are read again each stage, as when each stage was a run of its own: games an SPRT cached
    # while the stream played aren't played again, and an audit turning prediction off stops the recording.
    def test_each_stage_reads_the_cache_and_prediction_again(self) -> None:
        step, names = baselines.STEP, list(driver.ROTATION)
        third = f"rotation:{3 * step}"

        def stream(jobs, bots, workers, sandbox):
            self.streams.append([])
            for job in jobs:
                if len(self.streams[-1]) == PER_STAGE:  # stage 2's first game: an SPRT caches stage 3's games
                    folder, cache = self.folder(), sprt.BaselineCache(self.folder() / "baselines.jsonl")
                    maps = sorted(str(m) for m in folder.glob("maps/*.map"))
                    opponents = [bot for _, bot in driver.rotation_bots(names)]
                    for game in baselines.stage_jobs(third, job.a, maps, opponents, {}, sprt.BaselineCache(None),
                                                     False, None, referee):
                        cache.put(game, result(game))
                self.streams[-1].append(job)
                yield result(job)

        with patch.object(self, "stream", stream), \
                patch.object(sprt, "predicting", side_effect=[True, False]) as predicting:
            baselines.run(driver.REPO, driver.STATE)
        (jobs,) = self.streams
        self.assertEqual(predicting.call_count, 2)  # off stays off: not asked again
        self.assertTrue(all(job.record for job in jobs[:PER_STAGE]))
        self.assertFalse(any(job.record for job in jobs[PER_STAGE:]))
        maps = {Path(job.map).name for job in jobs}
        self.assertFalse({f"gen_eval_{i:03d}.map" for i in range(2 * step, 3 * step)} & maps)
        self.assertIn(third, baselines.read(driver.STATE)["done"])

    def test_a_failed_screen_game_leaves_its_stage_to_do_and_the_run_failed(self) -> None:
        self.failing = {"folder"}
        baselines.run(driver.REPO, driver.STATE)
        record = baselines.read(driver.STATE)
        self.assertIn("play exited 4: screen games failed: screen:folder:0: ", record["error"])
        self.assertEqual((record["failures"], record.get("finished_at")), (1, None))
        self.assertNotIn("screen:folder:0", record["done"])
        self.assertIn("screen:v0004:0", record["done"])  # the other stages still played

    # The real referee scheduler on `slots` local slots: stage k + 1's first game starts while stage k's last still
    # plays, so a stage no longer waits for the slowest game of the one before. Played one referee run a stage, as until
    # 2026-09-28, no stage could. A game ends only while every slot plays, the oldest first, one a refill (until the jobs
    # run out), so a stage's last games can end only after the next stage's first ones start when the scheduler streams
    # across stages; behind a barrier the slots never fill and its games end at WAIT_S, after the next stage's start.
    # Starts and ends are counted in order, not timed: a 20 ms sleep and clock readings failed under load.
    def test_the_next_stage_starts_while_the_one_before_still_plays(self) -> None:
        slots, changed, running, starts, ends, busy, clock, exhausted = 6, threading.Condition(), [], {}, {}, [], [0], [False]
        WAIT_S = 20  # only a broken scheduler waits this long
        real = referee.stream_games

        def tick() -> int:
            clock[0] += 1
            return clock[0]

        def play_here(scheduler, job, path, **_):
            key = (str(job.map), job.b.name, job.a_side)
            with changed:
                running.append(key)
                busy.append(len(running))
                starts[key] = tick()
                changed.notify_all()
                changed.wait_for(lambda: running[0] == key and (len(running) == slots or exhausted[0]), WAIT_S)
                running.remove(key)
                ends[key] = tick()
                changed.notify_all()
            return referee.RawGame("", 0, None, 0.02)  # parse_game is patched: only the scheduler reads it

        def stream(jobs, bots, workers, sandbox):
            self.streams.append([])

            def pulled():
                for job in jobs:
                    self.streams[-1].append(job)
                    yield job
                with changed:
                    exhausted[0] = True
                    changed.notify_all()

            yield from real(pulled(), bots, slots, sandbox)

        stages = baselines.stages(list(driver.ROTATION), [], 0)[:3]  # three rotation stages
        baselines.update(driver.STATE, CHAMPION, done=[s for s in baselines.stages(
            list(driver.ROTATION), self.screen_names(), driver.POOL_PAIRS) if s not in stages])
        with patch.object(self, "stream", stream), patch.object(referee, "build_bots", return_value={}), \
                patch.object(referee.Scheduler, "play_here", play_here), \
                patch.object(referee, "parse_game", lambda job, game: result(job)):
            baselines.run(driver.REPO, driver.STATE)
        self.assertLessEqual(set(stages), set(baselines.read(driver.STATE)["done"]))
        (jobs,) = self.streams
        per_stage = len(jobs) // 3
        for k in (1, 2):
            before, after = jobs[(k - 1) * per_stage:k * per_stage], jobs[k * per_stage]
            last_end = max(ends[(str(j.map), j.b.name, j.a_side)] for j in before)
            self.assertLess(starts[(str(after.map), after.b.name, after.a_side)], last_end)
        self.assertGreaterEqual(per_stage, slots)  # else a stage's last games could all end before a refill
        self.assertEqual(max(busy), slots)

    def test_tick_starts_it_only_while_no_evaluation_plays_on_the_hosts(self) -> None:
        now = time.time()
        (driver.STATE / "baselines.json").unlink()  # its first tick for this champion
        with patch.object(baselines, "launch", return_value=4242) as launch, \
                patch.object(baselines.meta, "champion_commit", return_value=CHAMPION), \
                patch("maintenance.toolchain", return_value={"epoch": EPOCH, "python": "/py"}):
            self.busy = ["an evaluation is playing on the hosts"]
            self.assertIsNone(baselines.tick(driver.REPO, driver.STATE, now))
            launch.assert_not_called()
            self.assertIn("launched in the background", baselines.tick(driver.REPO, driver.STATE, now))
            record = baselines.read(driver.STATE)
            self.assertEqual((record["pid"], record["champion"], record["toolchain"]["python"]), (4242, CHAMPION, "/py"))
            with patch.object(baselines.meta, "alive", return_value=True):
                self.assertIsNone(baselines.tick(driver.REPO, driver.STATE, now + 60))
            with patch.object(baselines.meta, "alive", return_value=False):  # it died: retried after an hour
                self.assertIsNone(baselines.tick(driver.REPO, driver.STATE, now + 120))
                self.assertIn("launched", baselines.tick(driver.REPO, driver.STATE, now + 120 + baselines.RETRY_S))
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(baselines.read(driver.STATE)["failures"], 1)

    def test_tick_rewrites_a_paused_reason_that_changed(self) -> None:
        now = time.time()
        baselines.save(driver.STATE, {"champion": CHAMPION, "epoch": EPOCH, "rotation": list(driver.ROTATION),
                                      "maps": driver.shared_mix(), "keys": driver.TEST_KEYS,
                                      "paused": "an evaluation is playing on the hosts", "paused_at": now - 600})
        with patch.object(baselines.meta, "champion_commit", return_value=CHAMPION), \
                patch("maintenance.toolchain", return_value={"epoch": EPOCH, "python": "/py"}):
            self.busy = ["an evaluation is playing on the hosts", "a stop marker is set"]
            baselines.tick(driver.REPO, driver.STATE, now)  # the same reason: left as it was
            self.assertEqual(baselines.read(driver.STATE)["paused_at"], now - 600)
            baselines.tick(driver.REPO, driver.STATE, now + 60)
        record = baselines.read(driver.STATE)
        self.assertEqual((record["paused"], record["paused_at"]), ("a stop marker is set", now + 60))

    # A shared mix naming a style new on main that the referee checkout makes only after the next evaluation's
    # refresh: shared_set_maps would raise and count a failure every RETRY_S; the prewarm pauses instead.
    def test_tick_pauses_while_the_referee_lacks_a_style_of_the_mix(self) -> None:
        now, style = time.time(), sorted(driver.shared_mix())[0]
        (driver.REFEREE / "mapgen/gen.py").write_text('STYLES = ("normal", "eval")')
        with patch.object(baselines, "launch", return_value=4242) as launch, \
                patch.object(baselines.meta, "champion_commit", return_value=CHAMPION), \
                patch("maintenance.toolchain", return_value={"epoch": EPOCH, "python": "/py"}):
            self.assertIsNone(baselines.tick(driver.REPO, driver.STATE, now))
            launch.assert_not_called()
            record = baselines.read(driver.STATE)
            self.assertIn(repr(style), record["paused"])
            self.assertFalse(record.get("failures"))
            shutil.copy(driver.REPO / "mapgen/gen.py", driver.REFEREE / "mapgen/gen.py")  # the refresh
            self.assertIn("launched", baselines.tick(driver.REPO, driver.STATE, now + 60))

    # Stages done on the eval set (a record from before the worldgen mix) say nothing about the mixed set's games: a
    # record of another map mix starts over on today's.
    def test_a_record_of_another_map_mix_starts_over(self) -> None:
        baselines.save(driver.STATE, {"champion": CHAMPION, "epoch": EPOCH, "rotation": list(driver.ROTATION),
                                      "done": ["rotation:50"]})
        with patch.object(baselines.meta, "champion_commit", return_value=CHAMPION), \
                patch("maintenance.toolchain", return_value={"epoch": EPOCH, "python": "/py"}):
            self.busy = ["an evaluation is playing on the hosts"]
            baselines.tick(driver.REPO, driver.STATE, time.time())
        record = baselines.read(driver.STATE)
        self.assertEqual((record["maps"], record.get("done")), (driver.shared_mix(), None))

    def test_health_alerts_on_repeated_failures_and_a_hung_run(self) -> None:
        now = time.time()
        clear_home()
        self.assertEqual(health.prewarm(driver.STATE, now), [])
        baselines.save(driver.STATE, {"champion": CHAMPION, "failures": 2, "failed_at": now - 60,
                                      "error": "RuntimeError: play exited 1: no such map"})
        (alert,) = health.prewarm(driver.STATE, now)
        self.assertEqual(alert.key, "prewarm:failed")
        self.assertIn("no such map", alert.text)
        baselines.save(driver.STATE, {"champion": CHAMPION, "pid": os.getpid(), "progress_at": now - baselines.STALE - 60})
        (alert,) = health.prewarm(driver.STATE, now)
        self.assertEqual(alert.key, "prewarm:stale")
        # A run waiting in place for an evaluation on the hosts (sweep.wait_idle) keeps its pid but isn't hung.
        baselines.update(driver.STATE, CHAMPION, paused=sweep.ON_HOSTS)
        self.assertEqual(health.prewarm(driver.STATE, now), [])

    # prewarm-plan.json (rewritten each run, never appended): a torn or oversized plan alerts, a real run's doesn't.
    def test_health_checks_the_plan(self) -> None:
        baselines.run(driver.REPO, driver.STATE)
        plan = self.folder() / "prewarm-plan.json"
        self.assertLess(plan.stat().st_size, baselines.PLAN_BOUND)
        self.assertEqual(health.prewarm(driver.STATE, time.time()), [])
        plan.write_text('{"stages": [')
        (alert,) = health.prewarm(driver.STATE, time.time())
        self.assertEqual(alert.key, "prewarm:plan")
        plan.write_text(json.dumps({"maps": ["m" * 100] * (baselines.PLAN_BOUND // 100)}))
        (alert,) = health.prewarm(driver.STATE, time.time())
        self.assertIn("bound", alert.text)

    def screen_names(self) -> list[str]:
        import tomllib
        return [spec["name"] for spec in tomllib.loads((driver.REPO / "referee/gauntlet.toml").read_text())["opponent"]]


if __name__ == "__main__":
    unittest.main()
