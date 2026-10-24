"""Unit tests for the SPRT statistics (no games). Run: uv run python -m unittest discover -s referee"""

import testhome  # before anything that reads live paths
import hashlib
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sprt import (NELO_PER_T, elo_estimate, elo_from_score, llr_normalized, mean_var, counts_to_pdf,
                  mle_t_value, sprt_bounds)


# The normal-approximation LLR n (t1 - t0) (2 t_hat - t0 - t1) / 2, with t in per-pair units.
def normal_llr(nelo0: float, nelo1: float, counts: list[int]) -> float:
    total, pdf = counts_to_pdf(counts)
    mu, var = mean_var(pdf)
    t_hat = (mu - 0.5) / math.sqrt(var)
    t0, t1 = (math.sqrt(2) * e / NELO_PER_T for e in (nelo0, nelo1))
    return total * (t1 - t0) * (2 * t_hat - t0 - t1) / 2


class TestSprt(unittest.TestCase):
    def test_keeps_the_candidates_worst_losses_first(self) -> None:
        import sprt
        from run import GameResult

        def game(i: int, winner: str) -> GameResult:
            return GameResult(f"m{i // 2}.map", "AB"[i % 2], winner, "rounds", 500, None, None, 1.0, None)

        # pair 0 split, pair 1 lost both, pair 2 won both, pair 3 split the other way
        games = [game(i, w) for i, w in enumerate("abbbaaba")]
        trial = sprt.Trial(Path("m.map"), "champion", None)
        lost = [loss for n in range(0, 8, 2) for loss in sprt.trial_losses(trial, n, games[n:n + 2])]
        with tempfile.TemporaryDirectory() as folder:
            replays, out = Path(folder) / "all", Path(folder) / "losses"
            replays.mkdir()
            for i, g in enumerate(games):
                (replays / f"{i:04d}-{Path(g.map).stem}-a{g.a_side}.replay").write_text(str(i))
            with patch.object(sprt, "KEEP_LOSSES", 3):
                sprt.keep_losses(lost, replays, out)
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ["0001-m0-aB.replay", "0002-m1-aA.replay", "0003-m1-aB.replay"])

    def test_opponent_trials_score_the_candidate_against_the_champion_on_the_same_games(self) -> None:
        import sprt
        from run import GameResult
        game = lambda winner: GameResult("m.map", "A", winner, "rounds", 500, None, None, 1.0, None)
        swarm = sprt.Trial(Path("m.map"), "swarm", Path("swarm"))
        pair = sprt.Trial(Path("m.map"), "champion", None)
        # candidate beat the swarm twice where the champion lost twice: the best trial
        self.assertEqual(sprt.trial_score(swarm, [game(w) for w in "aabb"], 9), 8)
        # both did the same: level, whatever the result
        self.assertEqual(sprt.trial_score(swarm, [game(w) for w in "abab"], 9), 4)
        # candidate drew one the champion won: an eighth below level
        self.assertEqual(sprt.trial_score(swarm, [game(w) for w in ["a", "draw", "a", "a"]], 9), 3)
        # a head-to-head pair keeps its pentanomial meaning, on either bucket scale
        self.assertEqual(sprt.trial_score(pair, [game("a"), game("draw")], 5), 3)
        self.assertEqual(sprt.trial_score(pair, [game("a"), game("draw")], 9), 6)
        cand, base = Path("cand"), Path("base")
        self.assertEqual([(j.a, j.b, j.a_side) for j in swarm.jobs(cand, base)],
                         [(cand, Path("swarm"), "A"), (cand, Path("swarm"), "B"),
                          (base, Path("swarm"), "A"), (base, Path("swarm"), "B")])
        # a loss the champion avoided on the same side comes first
        losses = sprt.trial_losses(swarm, 10, [game(w) for w in "bbab"])
        self.assertEqual([(worse, i) for worse, i, _ in losses], [(False, 10), (True, 11)])

    def test_run_rotates_through_the_champion_and_opponents_and_reports_candidate_games(self) -> None:
        import sprt
        from run import GameResult
        jobs_seen = []

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:
                jobs_seen.append((job.map.name, job.a.name, job.b.name))
                # the candidate wins everything; the champion loses to opponents
                yield GameResult(str(job.map), job.a_side, "a" if job.a.name == "cand" else "b", "rounds", 500,
                                 {}, {}, 1.0, None)

        maps = iter(Path(f"m{i}.map") for i in range(12))
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status", return_value={}):
            report = sprt.run_sprt(Path("cand"), Path("base"), maps, 0, 15, 0.05, 0.05, 12, 1,
                                   losses=out / "losses", opponents=[("swarm", Path("swarm"))])
        self.assertEqual(report["rotation"], ["champion", "swarm"])
        progress = json.loads((out / "sprt-progress.json").read_text())  # the standing after the last check
        self.assertEqual((progress["trials"], progress["per_opponent"]["swarm"]), (report["pairs"], 1.0))
        self.assertEqual(jobs_seen[:6], [("m0.map", "cand", "base"), ("m0.map", "cand", "base"),
                                         ("m1.map", "cand", "swarm"), ("m1.map", "cand", "swarm"),
                                         ("m1.map", "base", "swarm"), ("m1.map", "base", "swarm")])
        self.assertEqual(len(report["pentanomial"]), 9)
        self.assertEqual(report["per_opponent"]["swarm"]["score"], 1.0)
        self.assertTrue(all(g["map"] and g["winner"] == "a" for g in report["games"]))
        self.assertEqual(len(report["games"]), 2 * report["pairs"])

    # REFLECT 2026-09-30 15:13 F23: sig asked twice mid-test for the aimed style's and each size's score. The progress
    # file keeps them by map folder (the mix's style name) and size band, with the games played and mean map size.
    def test_progress_keeps_running_scores_by_style_and_size(self) -> None:
        import sys
        import sprt
        from run import GameResult
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
        import meta
        self.assertEqual(sprt.SIZE_BANDS, tuple((name, low) for name, low, _ in meta.AREA_BANDS))
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        maps = []
        for i, (style, side) in enumerate([("near", 30), ("worldgen", 40)] * 6):
            (out / style).mkdir(exist_ok=True)
            maps.append(out / style / f"m{i}.map")
            maps[-1].write_text(f"MAP_NAME m{i}\nMAP {side} {side}\nTILE 0 0 wall\n")

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:  # the candidate wins on near maps, loses on worldgen ones
                won = job.a.name == "cand" and job.map.parent.name == "near"
                yield GameResult(str(job.map), job.a_side, "a" if won else "b", "rounds", 500, {}, {}, 1.0, None)

        with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status", return_value={}), \
                patch.object(sprt, "map_refused", return_value=None), patch("sys.stderr"):
            sprt.run_sprt(Path("cand"), Path("base"), iter(maps), 0, 15, 0.05, 0.05, 12, 1, losses=out / "losses")
        progress = json.loads((out / "sprt-progress.json").read_text())
        self.assertEqual(progress["per_style"], {"near": {"trials": 6, "score": 1.0},
                                                 "worldgen": {"trials": 6, "score": 0.0}})
        self.assertEqual(progress["per_size"], {"<=1024": {"trials": 6, "score": 1.0},
                                                "1025-2399": {"trials": 6, "score": 0.0}})
        self.assertEqual((progress["games"], progress["tiles"]), (24, (900 + 1600) // 2))

    # --stats-queue (loop/evalstats.py): an opponent trial's replays are linked into the queue before the SPRT deletes
    # them, named by trial and game, each game's role and side in its T#####.json; head-to-head trials never; and with
    # no worker reading, trials past sprt.STATS_BACKLOG waiting are skipped, not queued.
    def test_stats_queue_links_opponent_trials_while_the_backlog_allows(self) -> None:
        import sprt
        from run import GameResult
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        queue = out / "queue"
        queue.mkdir()

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for i, job in enumerate(jobs):
                game = GameResult(str(job.map), job.a_side, "draw", "rounds", 500, {}, {}, 1.0, None)
                (replays / sprt.replay_name(i, game)).write_text(f"{job.a.name} {job.b.name} {job.a_side}")
                yield game

        with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status",
                                                                           return_value={}), patch("sys.stderr"):
            report = sprt.run_sprt(Path("cand"), Path("base"), iter(Path(f"m{i}.map") for i in range(12)), 0, 15,
                                   0.05, 0.05, 12, 1, opponents=[("swarm", Path("swarm"))], stats_queue=queue)
        self.assertEqual(report["stats_queue"], {"queued": sprt.STATS_BACKLOG, "skipped": 6 - sprt.STATS_BACKLOG,
                                                 "unpairable": 0})
        first = json.loads((queue / "T00001.json").read_text())  # trial 0 plays the champion head to head
        self.assertEqual((first["opponent"], first["map"]), ("swarm", "m1.map"))
        self.assertEqual([(g["role"], g["a_side"], g["replay"]) for g in first["games"]],
                         [("candidate", "A", "00001-0.replay"), ("candidate", "B", "00001-1.replay"),
                          ("champion", "A", "00001-2.replay"), ("champion", "B", "00001-3.replay")])
        self.assertEqual([(queue / g["replay"]).read_text() for g in first["games"]],
                         ["cand swarm A", "cand swarm B", "base swarm A", "base swarm B"])
        self.assertEqual(sorted(p.name for p in queue.glob("T*.json")), ["T00001.json", "T00003.json"])
        # a trial whose champion games were reused baselines (no replay) can't pair: never queued, whatever the backlog
        swarm = sprt.Trial(Path("m.map"), "swarm", Path("swarm"))
        games = [GameResult("m.map", side, "draw", "rounds", 500, {}, {}, 1.0, None) for side in "ABAB"]
        empty = out / "empty"
        empty.mkdir()
        self.assertEqual(sprt.queue_stats(empty, 7, swarm, games, [None, None, games[2], games[3]], 0, out),
                         "unpairable")
        self.assertEqual(list(empty.iterdir()), [])

    # A native judge check that differed while the test had native games at risk ends it at the next batch of trials
    # (review 4, 2026-09-29: it played on to 2,400 pairs, all then thrown away); one with none at risk plays on.
    def test_a_native_check_that_differed_stops_the_test_in_error(self) -> None:
        import sprt
        from run import GameResult

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:
                yield GameResult(str(job.map), job.a_side, "draw", "rounds", 500, {}, {}, 1.0, None)

        off = {"x": {"at": 1.0, "said": "here: check differs: replay", "check": True}}
        for at_risk, pairs in ((3, 12), (0, 60)):
            with self.subTest(at_risk=at_risk):
                reports = iter([None, *[{"games": 9, "since_check": at_risk, "checked": None, "off": off}] * 99])
                with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status",
                                                                                   return_value={}), \
                        patch.object(sprt, "native_report", side_effect=lambda started: next(reports)), \
                        patch("sys.stderr"):
                    report = sprt.run_sprt(Path("cand"), Path("base"), iter(Path(f"m{i}.map") for i in range(60)),
                                           0, 15, 0.05, 0.05, 60, 1)
                self.assertEqual(report["pairs"], pairs)
                self.assertEqual("native judge differed" in (report["error"] or ""), bool(at_risk))

    # MISSES 86: E0287's candidate wrote out of bounds on the heap, so its games differed from run to run, and its SPRT
    # played on with only an alert saying so. A check that finds the candidate not repeating its own games (an entry
    # it had before this run counts too) ends the test in error and says so in the report. A candidate-v-X check that
    # differed flagged the candidate even when X was the one at fault: it counts only once X repeated a game with
    # another bot (a champion-v-X check that matched) and differed with none, else the test plays on, reporting both;
    # another bot's older entry isn't this run's.
    def test_a_nondeterministic_candidate_stops_the_test_and_is_reported(self) -> None:
        import run
        import sprt
        from run import GameResult

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:
                yield GameResult(str(job.map), job.a_side, "draw", "rounds", 500, {}, {}, 1.0, None)

        with tempfile.TemporaryDirectory() as folder:
            cand, base = Path(folder) / "cand", Path(folder) / "base"
            for bot, text in ((cand, "cand"), (base, "base")):
                bot.mkdir()
                (bot / "main.cpp").write_text(text)
            ours, theirs = run.bot_digest(cand), run.bot_digest(base)
            checks = run.remote.loop_home() / run.FORK_CHECKS
            self.addCleanup(checks.unlink, missing_ok=True)
            entry, x = {"at": 1.0, "said": "here: MISMATCH; plain twice: replay differs at r63"}, "f" * 16
            key = lambda *bots: " ".join(sorted(bots))  # noqa: E731
            for flagged, differed, repeated, pairs, error in (
                    ({ours: entry, x: entry}, [key(ours, x)], [], 60, False),
                    ({ours: entry, x: entry}, [key(ours, x)], [key(theirs, x)], 6, True),
                    ({ours: entry, x: entry, theirs: entry}, [key(ours, x), key(theirs, x)], [key(theirs, x)], 60,
                     False),
                    ({"other": entry}, [], [], 60, False)):
                with self.subTest(differed=differed, repeated=repeated):
                    checks.write_text(json.dumps({"checks": 3, "off": {}, "nondeterministic": flagged,
                                                  "nondeterministic_pairs": {k: entry for k in differed},
                                                  "repeated": {k: 1.0 for k in repeated}}))
                    with patch.object(sprt, "stream_games", fake_stream), \
                            patch.object(sprt.remote, "read_status", return_value={}), patch("sys.stderr"):
                        report = sprt.run_sprt(cand, base, iter(Path(f"m{i}.map") for i in range(60)), 0, 15, 0.05,
                                               0.05, 60, 1)
                    self.assertEqual(report["pairs"], pairs)
                    self.assertEqual("doesn't repeat its own games: here: MISMATCH" in (report["error"] or ""), error)
                    found = report.get("nondeterministic")
                    if "other" in flagged:
                        self.assertIsNone(found)
                    else:
                        self.assertEqual((found["cand"], found["base"], set(found["bots"]), set(found["pairs"])),
                                         (ours, theirs if theirs in flagged else None, set(flagged) & {ours, theirs},
                                          set(differed)))
                        self.assertEqual(found["bots"][ours]["by"], "fork server")
                        self.assertEqual(bool(found["blamed"]), error)

    # E0260 and E0257 (2026-09-28) each ended in error at pair 734 of their shared set on one worldgen map the engine
    # won't load: the trial now plays a spare from the same folder against the same opponent instead, and the map
    # never counts. A game failing on a map that loads (a bot's crash, a timeout) still ends the test in error.
    def test_a_map_the_engine_refuses_is_swapped_for_a_spare_but_any_other_failed_game_still_errors(self) -> None:
        import sprt
        from run import GameResult
        ladder = Path(__file__).resolve().parent.parent / "maps" / "ladder"
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        for folder in ("maps/worldgen", "maps/eval", "spares/eval", "spares/worldgen"):
            (root / folder).mkdir(parents=True)
        bad = root / "maps/worldgen/gen_worldgen_144449864.map"
        shutil.copy(Path(__file__).resolve().parent / "testdata/gen_worldgen_144449864.map", bad)
        good = [shutil.copy(ladder / name, root / "maps/eval" / name) for name in ("default.map", "devil.map")]
        spares = [shutil.copy(ladder / "portals.map", root / "spares/eval/portals.map"),
                  shutil.copy(ladder / "trophy.map", root / "spares/worldgen/trophy.map")]
        played, crash = [], set()

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:
                played.append((job.map.name, job.b.name))
                error = "unswbc run exited 1: bot 3 (team A) crashed" if job.map.name in crash else None
                yield GameResult(str(job.map), job.a_side, None if error else "a", "rounds", 500, {}, {}, 1.0, error)

        def test() -> dict:
            played.clear()
            with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status",
                                                                               return_value={}), patch("sys.stderr"):
                return sprt.run_sprt(Path("cand"), Path("base"), iter([Path(good[0]), bad, Path(good[1])]), 0, 15,
                                     0.05, 0.05, 3, 1, opponents=[("swarm", Path("swarm"))],
                                     spares=iter(map(Path, spares)))

        report = test()
        (dropped,) = report["dropped_maps"]
        self.assertEqual((dropped["map"], dropped["replaced_by"]), (str(bad), str(spares[1])))  # its folder's spare
        self.assertIn("not adjacent to the one before it", dropped["reason"])
        self.assertEqual((report["error"], report["pairs"]), (None, 3))
        self.assertNotIn(bad.name, {name for name, _ in played})
        self.assertIn(("trophy.map", "swarm"), played)  # the dropped trial's opponent
        self.assertNotIn(str(bad), report["per_map"])
        crash.add("devil.map")
        self.assertIn("game failed on", test()["error"])

    # An opponent mix (loop/driver.py trial_order) names each trial's opponent: trial i plays schedule[i], not the
    # rotation's turn, and the report keeps the played schedule for readers that split trials by opponent.
    def test_a_schedule_names_each_trials_opponent(self) -> None:
        import sprt
        from run import GameResult
        opponents_seen = []

        def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
            for job in jobs:
                if job.a.name == "cand" and job.a_side == "A":
                    opponents_seen.append((job.map.name, job.b.name))
                yield GameResult(str(job.map), job.a_side, "a" if job.a.name == "cand" else "b", "rounds", 500,
                                 {}, {}, 1.0, None)

        schedule = ["champion", "folder", "swarm", "folder", "folder", "champion"]
        with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt.remote, "read_status", return_value={}):
            report = sprt.run_sprt(Path("cand"), Path("base"), iter(Path(f"m{i}.map") for i in range(6)), 0, 15,
                                   0.05, 0.05, 6, 1, opponents=[("swarm", Path("swarm")), ("folder", Path("folder"))],
                                   schedule=schedule)
            self.assertEqual(opponents_seen, [(f"m{i}.map", "base" if n == "champion" else n)
                                              for i, n in enumerate(schedule)])
            self.assertEqual((report["schedule"], report["per_opponent"]["folder"]["trials"]), (schedule, 3))
            with self.assertRaises(ValueError):  # a name that isn't an opponent, or too few names, is refused
                sprt.run_sprt(Path("cand"), Path("base"), iter([]), 0, 15, 0.05, 0.05, 6, 1,
                              opponents=[("swarm", Path("swarm"))], schedule=schedule)

    def test_replays_are_deleted_as_trials_are_scored_but_the_worst_losses_are_kept(self) -> None:
        # A full SPRT kept every game's replay until it ended (951 replays, 4.7 GB, part way through one test).
        import sprt
        from run import GameResult

        def run_test(out: Path) -> int:
            most = 0

            def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
                nonlocal most
                for i, job in enumerate(jobs):  # named and written as run.Scheduler.play does, before the yield
                    (replays / f"{i:04d}-{job.map.stem}-a{job.a_side}.replay").write_text(str(i))
                    most = max(most, len(list(replays.glob("*.replay"))))
                    even = int(job.map.stem[1:]) % 2 == 0  # level: the candidate wins even maps and loses odd ones
                    yield GameResult(str(job.map), job.a_side, "a" if even == (job.a.name == "cand") else "b",
                                     "rounds", 500, {}, {}, 1.0, None)

            maps = iter(Path(f"m{i}.map") for i in range(30))
            out.mkdir()
            with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt, "KEEP_LOSSES", 3), \
                    patch.object(sprt.remote, "read_status", return_value={}):
                report = sprt.run_sprt(Path("cand"), Path("base"), maps, 0, 15, 0.05, 0.05, 30, 1,
                                       losses=out / "losses", opponents=[("swarm", Path("swarm"))])
            self.assertEqual((report["pairs"], report["decision"]), (30, "inconclusive"))
            return most

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        with patch.object(sprt, "drop_replays", lambda *a: None):  # every replay kept to the end, as before
            self.assertEqual(run_test(root / "all"), 90)
        self.assertLessEqual(run_test(root / "dropped"), 3 + 4)  # the kept losses and one trial's games
        names = lambda folder: sorted(p.name for p in (folder / "losses").iterdir())
        self.assertEqual(len(names(root / "all")), 3)
        self.assertEqual(names(root / "dropped"), names(root / "all"))

    def test_a_second_test_on_the_same_maps_reuses_the_champions_opponent_games(self) -> None:
        # The rotation played ~3.3 games a trial; the champion-vs-opponent half repeats exactly on a shared map set.
        import sprt
        from run import BotStats, GameResult
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bot in ("cand", "base", "swarm"):
                (root / bot).mkdir()
                (root / bot / "main.cpp").write_text(bot)
            maps = []
            for i in range(12):
                maps.append(root / f"m{i}.map")
                maps[-1].write_text(f"MAP {i}")
            played = []

            def fake_stream(jobs, bots, workers, sandbox, replays, stats=None, alerts=False):
                for job in jobs:
                    played.append((job.a.name, job.b.name))
                    yield GameResult(str(job.map), job.a_side, "a" if job.a.name == "cand" else "b", "rounds", 500,
                                     BotStats(), BotStats(), 1.0, None)

            def test() -> dict:
                with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt, "map_refused", return_value=None), \
                        patch.object(sprt.remote, "read_status", return_value={}):
                    return sprt.run_sprt(root / "cand", root / "base", iter(maps), 0, 15, 0.05, 0.05, 12, 1,
                                         opponents=[("swarm", root / "swarm")], baselines=root / "baselines.jsonl")

            first = test()
            first_games, played[:] = len(played), []
            rotation = root / "sprt-rotation.jsonl"
            with patch.object(sprt, "stream_games", fake_stream), patch.object(sprt, "map_refused", return_value=None), \
                    patch.object(sprt.remote, "read_status", return_value={}):
                second = sprt.run_sprt(root / "cand", root / "base", iter(maps), 0, 15, 0.05, 0.05, 12, 1,
                                       opponents=[("swarm", root / "swarm")], baselines=root / "baselines.jsonl",
                                       rotation_out=rotation)
            # Every scored opponent trial's four games, the champion's read from the cache with their stats, for the
            # opponent screen to count (driver.check_pool).
            rows = [json.loads(line) for line in rotation.read_text().splitlines()]
            self.assertEqual(len(rows), second["per_opponent"]["swarm"]["trials"])
            self.assertEqual({(r["opponent"], r["bot"], r["cand"], r["base"], r["unswbc"]) for r in rows},
                             {("swarm", str(root / "swarm"), str(root / "cand"), str(root / "base"),
                               sprt.importlib.metadata.version("unswbc"))})
            self.assertEqual([(g["map"], g["a_side"], g["winner"]) for g in rows[0]["games"]],
                             [(rows[0]["map"], side, winner) for side, winner in zip("ABAB", "aabb")])
            self.assertEqual(rows[0]["games"][2]["a"]["crashes"], 0)
            self.assertEqual(first["baselines_reused"], 0)
            self.assertEqual(second["baselines_reused"], 12)  # 6 opponent trials x 2 champion games
            self.assertEqual(len(played), first_games - 12)
            self.assertNotIn(("base", "swarm"), played)
            self.assertEqual(second["per_opponent"], first["per_opponent"])
            self.assertEqual(second["pentanomial"], first["pentanomial"])
            (root / "base" / "main.cpp").write_text("a new champion")  # its games are keyed by its source
            played[:] = []
            self.assertEqual(test()["baselines_reused"], 0)

    def test_nine_bucket_llr_is_mirror_symmetric(self) -> None:
        counts = [1, 3, 5, 9, 40, 12, 6, 2, 1]
        self.assertAlmostEqual(llr_normalized(0, 15, counts), llr_normalized(0, -15, counts[::-1]), places=9)
        self.assertGreater(llr_normalized(0, 15, [1, 2, 3, 5, 40, 12, 10, 5, 2]), 0)  # clearly above level

    def test_bounds(self) -> None:
        lower, upper = sprt_bounds(0.05, 0.05)
        self.assertAlmostEqual(lower, -2.944438979, places=6)
        self.assertAlmostEqual(upper, 2.944438979, places=6)

    def test_mle_hits_the_constraint(self) -> None:
        _, pdf = counts_to_pdf([3, 10, 20, 12, 5])
        for t in (-0.3, 0.0, 0.2, 0.5):
            mu, var = mean_var(mle_t_value(pdf, 0.5, t))
            self.assertAlmostEqual((mu - 0.5) / math.sqrt(var), t, places=6)

    def test_llr_sign(self) -> None:
        self.assertLess(llr_normalized(0, 70, [10, 20, 40, 20, 10]), 0)  # even score favours H0
        self.assertGreater(llr_normalized(0, 70, [5, 10, 30, 30, 25]), 0)
        self.assertLess(llr_normalized(0, 70, [25, 30, 30, 10, 5]), 0)

    def test_llr_mirror_symmetry(self) -> None:
        counts = [4, 9, 30, 15, 7]
        self.assertAlmostEqual(llr_normalized(0, 70, counts), llr_normalized(0, -70, counts[::-1]), places=9)

    def test_llr_matches_normal_approximation_for_small_effects(self) -> None:
        counts = [40, 160, 400, 180, 50]
        exact, approx = llr_normalized(0, 20, counts), normal_llr(0, 20, counts)
        self.assertGreater(exact, 0)
        self.assertAlmostEqual(exact / approx, 1, delta=0.05)

    def test_llr_all_wins_passes(self) -> None:
        # A bot that wins every pair must pass: LLR per pair is ln(0.637/0.5) at nElo 70, about 0.24.
        self.assertAlmostEqual(llr_normalized(0, 70, [0, 0, 0, 0, 10]) / 10, 0.242, delta=0.01)
        self.assertGreater(llr_normalized(0, 70, [0, 0, 0, 0, 13]), sprt_bounds(0.05, 0.05)[1])
        self.assertLess(llr_normalized(0, 70, [13, 0, 0, 0, 0]), sprt_bounds(0.05, 0.05)[0])

    def test_elo_from_score(self) -> None:
        self.assertAlmostEqual(elo_from_score(0.5), 0.0)
        self.assertAlmostEqual(elo_from_score(0.75), 400 * math.log10(3))
        self.assertAlmostEqual(elo_from_score(0.25), -400 * math.log10(3))

    def test_elo_ci(self) -> None:
        # Half the pairs 0-2, half 2-0: score 0.5, sd 0.5 per pair, so the score's 95% CI is 0.5 ± 0.098.
        score, elo, lo, hi, _ = elo_estimate([50, 0, 0, 0, 50])
        self.assertAlmostEqual(score, 0.5, places=6)
        self.assertAlmostEqual(elo, 0.0, places=3)
        self.assertAlmostEqual(hi, elo_from_score(0.5 + 1.959964 * 0.5 / 10), delta=0.1)
        self.assertAlmostEqual(lo, -hi, places=6)

    def test_elo_ci_unbounded_on_a_sweep(self) -> None:
        _, elo, lo, hi, _ = elo_estimate([0, 0, 0, 0, 10])
        self.assertGreater(elo, 1000)
        self.assertIsNone(hi)
        self.assertIsNotNone(lo)


# run_sprt through the real scheduler with fake games: the candidate plays opponents exactly as the champion does
# (so every recorded game repeats), unless it is in `differs` (then its games there differ, though the faked check
# says identical: what a nondeterministic bot would do), and it differs from the champion head to head.
class TestPredictedSprt(unittest.TestCase):
    def setUp(self) -> None:
        import sprt
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for bot in ("cand", "base", "swarm", "cand2"):
            (self.root / bot).mkdir()
            (self.root / bot / "main.cpp").write_text(bot)
        self.maps = []
        for i in range(60):
            self.maps.append(self.root / f"m{i}.map")
            self.maps[-1].write_text(f"MAP {i}")
        self.played, self.differs = [], set()
        self.recording = sprt.record.Recording("A", {0: "ID 0"}, [(0, "ROUND 0\n", "d")])

    def game(self, job, sandbox, path, started=None, nice=0, watch=False, native=None):
        self.played.append((job.map.name, job.a.name, job.b.name, job.a_side))
        if job.record:
            self.recording.save(job.record)
        return run_module().RawGame("", 0, None, 1.0)

    def parse(self, job, raw_game):
        run = run_module()
        a = job.a.name if job.b.name != "swarm" or job.a.name in self.differs else ""
        h = int(hashlib.sha256(f"{job.map.name}{job.a_side}{job.b.name}{a}".encode()).hexdigest(), 16)
        return run.GameResult(str(job.map), job.a_side, "ab"[h % 2], "rounds", 100 + h % 400,
                              run.BotStats(h % 7, 2, 3), run.BotStats(1, h % 5, 9), 1.0, None)

    def patches(self):
        import sprt
        run = run_module()
        check = sprt.record.Check(True, 1, 1, 0.25, None)
        return [patch.object(run, "run_game", self.game), patch.object(run, "parse_game", self.parse),
                patch.object(run, "build_bots", lambda bots, sandbox: {b: None for b in bots}),
                patch.object(run, "game_slots", lambda workers, active: 3),
                patch.object(run, "seeded_toolkit", return_value=False),
                patch.object(run.record, "check", return_value=check),
                patch.object(sprt.remote, "read_status", return_value={}), patch("sys.stderr"),
                patch.object(sprt, "map_refused", return_value=None)]  # the maps here are placeholders

    def sprt(self, cand: str = "cand", keyed: Path | None = None) -> dict:
        import contextlib
        import sprt
        with contextlib.ExitStack() as stack:
            for p in self.patches():
                stack.enter_context(p)
            return sprt.run_sprt(self.root / cand, self.root / "base", iter(self.maps), 0, 15, 0.05, 0.05, 60, 2,
                                 opponents=[("swarm", self.root / "swarm")], baselines=self.root / "baselines.jsonl",
                                 rotation_out=self.root / "rotation.jsonl", keyed=keyed)

    # With --opponent-cand (the fixed-key test copy, MISSES 99) the opponent trials play that build and the screen's
    # reuse finds it named in their rows; head to head --cand plays (on another fixed key).
    def test_opponent_trials_play_the_opponent_build_and_head_to_head_the_other(self) -> None:
        keyed = self.root / "cand-key"
        keyed.mkdir()
        (keyed / "main.cpp").write_text("cand on the fixed key")
        report = self.sprt(keyed=keyed)
        against = {(b, a) for _, a, b, _ in self.played}
        self.assertEqual({a for b, a in against if b == "base"}, {"cand"})
        self.assertEqual({a for b, a in against if b == "swarm"}, {"cand-key", "base"})
        self.assertEqual(report["opponent_cand"], str(keyed))
        rows = [json.loads(line) for line in (self.root / "rotation.jsonl").read_text().splitlines()]
        self.assertTrue(rows)
        self.assertEqual({row["cand"] for row in rows}, {str(keyed)})
        self.assertIsNone(self.sprt()["opponent_cand"])

    def recordings(self) -> list[Path]:
        return sorted((self.root / "recordings").glob("*.zst"))

    def test_decisions_are_the_same_whether_candidate_games_are_predicted_or_played(self) -> None:
        first = self.sprt()  # plays and records the champion's opponent games
        self.assertEqual(first["predict"]["predicted"], 0)
        self.assertEqual(len(self.recordings()), 60)  # 30 opponent trials, both sides
        self.played[:] = []
        predicted = self.sprt()
        played_then = len(self.played)
        for path in self.recordings():
            path.unlink()
        self.played[:] = []
        control = self.sprt()  # the same cache without recordings: every candidate game is played
        self.assertGreater(predicted["predict"]["predicted"], 30)
        self.assertGreater(predicted["predict"]["audited"], 0)
        self.assertEqual((predicted["predict"]["audit_mismatches"], predicted["predict"]["off"]), (0, None))
        self.assertEqual(control["predict"]["predicted"], 0)
        self.assertEqual(len(self.played) - played_then, predicted["predict"]["predicted"])
        games = lambda report: [{k: v for k, v in g.items() if k not in ("predicted", "seconds", "recording")}
                                for g in report["games"]]
        self.assertEqual(games(predicted), games(control))  # same results, same order
        for key in ("decision", "llr", "pairs", "pentanomial", "per_map", "per_opponent", "baselines_reused"):
            self.assertEqual(predicted[key], control[key], key)

    def test_an_audit_mismatch_fails_the_test_and_stops_prediction_on_the_map_set(self) -> None:
        import sprt
        self.sprt()
        self.differs = {"cand"}  # its games differ from the recordings, though every check says identical
        sprt.remote.ALERTS.unlink(missing_ok=True)
        report = self.sprt()
        self.assertIn("an audit's game differed from its recording", report["error"])
        self.assertEqual(report["predict"]["audit_mismatches"], 1)
        self.assertTrue((self.root / "recordings" / "OFF").exists())
        self.assertIn("WARNING predicted games: audit mismatch", sprt.remote.ALERTS.read_text())
        self.differs = set()
        again = self.sprt()  # a later test on the set predicts nothing, and says why
        self.assertEqual((again["predict"]["checks"], again["predict"]["predicted"]), (0, 0))
        self.assertIn("WARNING predicted games off: an audit on this map set found a difference",
                      sprt.remote.ALERTS.read_text())

    # With the champion's sonarBuildKey on (its real build: every SPRT from E0090 until MISSES 99's test copies)
    # prediction is off by design, and its alert at every SPRT's start was noise: it says why on stderr only, while an
    # unsupported record.py still alerts.
    def test_a_champion_with_its_sonar_key_on_says_why_on_stderr_only(self) -> None:
        import io
        import sprt
        line = "    bool sonarBuildKey = true;   // the tag's key is a hash of this build's source (sonarkey.hpp)\n"
        (self.root / "base" / "params.hpp").write_text(line)
        cache = sprt.BaselineCache(self.root / "baselines.jsonl")
        sprt.remote.ALERTS.unlink(missing_ok=True)
        with patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                patch.object(sprt.record, "supported", side_effect=AssertionError("not asked")):
            self.assertFalse(sprt.predicting(cache, self.root / "base"))
        self.assertIn("has sonarBuildKey on", stderr.getvalue())
        self.assertFalse(sprt.remote.ALERTS.exists())
        (self.root / "base" / "params.hpp").write_text(line.replace("true", "false"))
        with patch("sys.stderr", new_callable=io.StringIO), \
                patch.object(sprt.record, "supported", return_value="unswbc's run.py changed"):
            self.assertFalse(sprt.predicting(cache, self.root / "base"))
        self.assertIn("WARNING predicted games off: unswbc's run.py changed", sprt.remote.ALERTS.read_text())

    def test_a_torn_cache_line_is_skipped_and_the_next_put_starts_a_new_line(self) -> None:
        # The prewarm and a test both append to baselines.jsonl, and one killed mid-row leaves a torn line: the next
        # put glued its row onto it, and the loader then failed on a bad line mid-file.
        import sprt
        from run import BotStats, GameResult, Job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bot in ("base", "swarm"):
                (root / bot).mkdir()
                (root / bot / "main.cpp").write_text(bot)
            (root / "m.map").write_text("MAP 1")
            path = root / "baselines.jsonl"
            row = json.dumps({"key": "k1", "result": {"winner": "a", "end": "rounds", "rounds": 500}})
            path.write_text(row + "\n" + row.replace("k1", "k2")[:30])  # the last row, cut short
            cache = sprt.BaselineCache(path)
            self.assertEqual(list(cache.rows), ["k1"])
            job = Job(root / "m.map", root / "base", root / "swarm", "A")
            cache.put(job, GameResult(str(job.map), "A", "b", "rounds", 500, BotStats(), BotStats(), 1.0, None))
            with patch("sys.stderr"):
                reloaded = sprt.BaselineCache(path)
            self.assertEqual(set(reloaded.rows), {"k1", cache.key(job)})
            self.assertEqual(reloaded.get(job).winner, "b")

    # An aimed mix's SPRT reads the shared set's cache but plays its aimed styles (and spares) on private maps: their
    # games go into neither the set's rows nor its recordings, which no other test could reuse.
    def test_the_cache_keeps_only_its_own_sets_games(self) -> None:
        import sprt
        from run import BotStats, GameResult, Job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bot in ("base", "swarm"):
                (root / bot).mkdir()
                (root / bot / "main.cpp").write_text(bot)
            for folder in ("set/maps", "private"):
                (root / folder).mkdir(parents=True)
                (root / folder / "m.map").write_text(f"MAP {folder}")
            cache = sprt.BaselineCache(root / "set/baselines.jsonl")
            own, private = (Job(root / folder / "m.map", root / "base", root / "swarm", "A")
                            for folder in ("set/maps", "private"))
            for job in (own, private):
                cache.put(job, GameResult(str(job.map), "A", "b", "rounds", 500, BotStats(), BotStats(), 1.0, None))
            self.assertEqual(list(sprt.BaselineCache(root / "set/baselines.jsonl").rows), [cache.key(own)])
            self.assertEqual((cache.recorded(own).record, cache.recorded(private).record),
                             (cache.recording(own), None))

    # A lazily made map is waited for while its maker keeps its marker fresh, and read once made; a marker its maker
    # stopped touching (its driver died) ends the maps at once instead of leaving an orphaned SPRT waiting forever.
    def test_a_pending_map_is_waited_for_only_while_its_maker_lives(self) -> None:
        import os
        import threading
        import time
        import sprt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending, maps = root / "maps-pending", [root / "m0.map", root / "m1.map"]
            pending.write_text("making\n")
            threading.Timer(1.5, maps[0].write_text, ["MAP 0"]).start()
            reader = sprt.distinct_maps(maps, pending=pending)
            self.assertEqual(next(reader), maps[0])
            self.assertEqual(pending.with_name("maps-pending.read").read_text(), "0\n")
            stale = time.time() - sprt.PENDING_STALE - 1
            os.utime(pending, (stale, stale))
            done: list = []
            threading.Thread(target=lambda: done.append(list(reader)), daemon=True).start()
            for _ in range(50):
                if done:
                    break
                time.sleep(0.1)
            self.assertEqual(done, [[]])

    # A baseline the native judge played keeps the key it played under and is reused only while no check has found that
    # key differing from unswbc run (run.native_suspect; a judge that crashed or hung isn't one), even once "off" is
    # cleared for a fixed judge under a new key (review 4, 2026-09-29: every such row was reused then); a row with no
    # key never is. Its game is then played again, and replaces it.
    def test_a_native_baseline_is_played_again_once_a_check_differed(self) -> None:
        import sprt
        from run import NATIVE_CHECKS, BotStats, GameResult, Job
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for bot in ("base", "swarm"):
                (root / bot).mkdir()
                (root / bot / "main.cpp").write_text(bot)
            (root / "m.map").write_text("MAP 1")
            job = Job(root / "m.map", root / "base", root / "swarm", "A")
            with patch.object(sprt.native_judge, "process_key", return_value="k1"):
                sprt.BaselineCache(root / "baselines.jsonl").put(job, GameResult(
                    str(job.map), "A", "b", "rounds", 500, BotStats(), BotStats(), 1.0, None, native=True))
            checks = sprt.remote.loop_home() / NATIVE_CHECKS
            unkeyed = root / "unkeyed.jsonl"
            unkeyed.write_text((root / "baselines.jsonl").read_text().replace('"k1"', "true"))
            try:
                for data, reused in (({}, True), ({"off": {"x": {"check": False}}}, True),
                                     ({"off": {"x": {"check": True}}, "differed_keys": ["k1"]}, False),
                                     ({"off": {}, "differed_keys": ["k1"]}, False),
                                     ({"off": {}, "differed_keys": ["k0"]}, True)):
                    with self.subTest(data=data):
                        checks.write_text(json.dumps(data))
                        self.assertEqual(sprt.BaselineCache(root / "baselines.jsonl").get(job) is not None, reused)
                        self.assertIsNone(sprt.BaselineCache(unkeyed).get(job))
            finally:
                checks.unlink(missing_ok=True)

    def test_a_recording_pairs_only_with_the_row_of_its_own_game(self) -> None:
        import sprt
        self.sprt()
        cache = sprt.BaselineCache(self.root / "baselines.jsonl")
        base = sprt.Job(self.maps[1], self.root / "base", self.root / "swarm", "A")  # trial 1 plays the swarm
        cand = sprt.Job(self.maps[1], self.root / "cand", self.root / "swarm", "A")
        self.assertIsNotNone(cache.prediction(cand, base))
        self.recording.turns.append((0, "ROUND 1\n", "e"))
        self.recording.save(cache.recording(base))  # another game's recording at the same path
        self.assertIsNone(cache.prediction(cand, base))
        del cache.rows[cache.key(base)]  # a recording saved before its row was (a test stopped in between)
        self.assertIsNone(cache.prediction(cand, base))
        self.assertEqual(cache.recorded(base).record, cache.recording(base))  # replaying it always records

    def test_audited_seats_differ_between_candidates(self) -> None:
        import sprt
        self.sprt()
        cache = sprt.BaselineCache(self.root / "baselines.jsonl")
        audited = {}
        for name in ("cand", "cand2"):
            audited[name] = {(m.name, side) for i, m in enumerate(self.maps) if i % 2 for side in "AB"
                             if cache.prediction(sprt.Job(m, self.root / name, self.root / "swarm", side),
                                                 sprt.Job(m, self.root / "base", self.root / "swarm", side)).audit}
        self.assertNotEqual(audited["cand"], audited["cand2"])

    def test_prewarm_rerecords_cached_games_that_have_no_recording(self) -> None:
        import contextlib
        import prewarm
        import sprt
        self.sprt()
        for path in self.recordings():
            path.unlink()  # as for a champion whose baselines were played before recording existed
        cache = sprt.BaselineCache(self.root / "baselines.jsonl")
        with contextlib.ExitStack() as stack:
            for p in self.patches():
                stack.enter_context(p)
            self.played[:] = []
            args = (self.root / "base", self.maps, [self.root / "swarm"], cache, 60, 2)
            self.assertEqual(prewarm.prewarm(*args), (0, 0))  # every game is cached: nothing to play
            self.assertEqual(prewarm.prewarm(*args, rerecord=True), (60, 0))
            self.assertTrue(all(job[1] == "base" for job in self.played))
            self.assertEqual(len(self.recordings()), 60)
            self.assertEqual(prewarm.prewarm(*args, rerecord=True), (0, 0))  # now each has its own
        self.played[:] = []
        self.assertGreater(self.sprt()["predict"]["predicted"], 30)


def run_module():
    import run
    return run


if __name__ == "__main__":
    unittest.main()
