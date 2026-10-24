"""loop/evalstats.py: one game's stats from a real replay, checked against tools/replays.py's own counts; the queue a
worker reads, with a predicted game and a reused baseline; the paired table and its corrected moves; and the health
check's first firing. Run: PYTHONPATH=loop tools/gated_test.sh .venv/bin/python -m unittest test_evalstats"""

import testhome  # before anything that reads live paths
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import evalstats
import health

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
# An online game on Queen Of Spades to r499 (tools/test_replays.py QUEEN_95037): its small portal rooms hold deaths.
QUEEN = TOOLS / "testdata" / "queen-95037.replay"


class TestGameStats(unittest.TestCase):
    def test_counts_match_the_replay_tools_and_causes_add_up(self) -> None:
        import replays
        game = replays.analyse(replays.load_replay(QUEEN))
        stats = evalstats.game_stats(QUEEN, "A")
        causes = [k for k in evalstats.METRICS if k.startswith("death_") and k not in ("death_pocket",
                                                                                       "death_after_crossing")]
        self.assertEqual(sum(stats[k] for k in causes), stats["deaths"])
        self.assertEqual(stats["deaths"], sum(game.teams["A"].deaths.values()))
        self.assertEqual(stats["death_splitout"], replays.in_place_deaths(game, "A")["splitOutParents"])
        self.assertEqual(stats["crossings"], replays.portal_crossings(game, "A")["crossings"])
        self.assertEqual(stats["first_crossing"], min(r for r, _ in game.teams["A"].crossings))
        self.assertEqual(stats["pearls_early"], game.teams["A"].pearls_by_phase["early"])
        self.assertEqual(stats["longest_499"], next(s.longest["A"] for s in game.snapshots if s.round == 499))
        self.assertEqual(stats["first_split"], game.teams["A"].splits[0]["round"])
        # Queen's rooms only portals lead out of (tools/watch.py pockets) hold 21 of A's 46 deaths, B's 7 of 35
        self.assertEqual((stats["deaths"], stats["death_pocket"]), (46, 21))
        self.assertEqual((stats["death_after_crossing"], stats["fold_conversions"]), (6, 5))


class TestWorker(unittest.TestCase):
    # A trial as sprt.py queues it: the candidate's A game played, its B game predicted (the champion's, move for
    # move), the champion's A game played and its B game a reused baseline with no replay. The worker reads both
    # replays, the predicted game takes the champion's B stats (none here: it has no replay), and everything goes.
    def test_reads_a_queued_trial_and_empties_the_queue(self) -> None:
        run = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, run)
        queue = run / evalstats.QUEUE
        queue.mkdir()
        for i in (0, 2):
            shutil.copy(QUEEN, queue / f"00003-{i}.replay")
        games = [{"i": i, "role": "candidate" if i < 2 else "champion", "a_side": "AB"[i % 2], "predicted": i == 1,
                  "replay": f"00003-{i}.replay" if i in (0, 2) else None} for i in range(4)]
        (queue / "T00003.json").write_text(json.dumps({"trial": 3, "opponent": "swarm", "map": "m.map",
                                                       "games": games}))
        (queue / evalstats.OWNER).write_text("t1")
        evalstats.done(run)
        evalstats.worker(queue, run / evalstats.ROWS, "t1")
        [row] = evalstats.read_rows(run)
        self.assertEqual((row["trial"], row["opponent"]), (3, "swarm"))
        self.assertEqual([("stats" in g) for g in row["games"]], [True, False, True, False])
        self.assertEqual(sorted(p.name for p in queue.iterdir()), [evalstats.DONE, evalstats.OWNER])
        [(style, sides)] = evalstats.pairs([row], lambda m: "eval")
        self.assertEqual(len(sides), 1)  # A with A; B has no champion game to pair with
        self.assertEqual(sides[0][0], sides[0][1])  # the same replay on both sides of the pair

    # E0382 read 31 trials with one worker and skipped 566 while it was behind. Several workers share one queue: each
    # trial is read once, by whichever claimed it, and a claimed one no longer counts toward sprt.py's backlog.
    def test_workers_sharing_a_queue_read_each_trial_once(self) -> None:
        import threading
        from unittest.mock import patch
        run = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, run)
        queue = run / evalstats.QUEUE
        queue.mkdir()
        (queue / evalstats.OWNER).write_text("t1")
        for t in range(12):
            (queue / f"T{t:05d}.json").write_text(json.dumps({"trial": t}))
        waiting = []

        def read(queue, spec):
            waiting.append(len(list(queue.glob("T*.json"))))
            time.sleep(0.01)
            spec.unlink()
            return {"trial": int(spec.name[1:6]), "games": []}

        evalstats.done(run)
        with patch.object(evalstats, "read_trial", side_effect=read):
            workers = [threading.Thread(target=evalstats.worker, args=(queue, run / evalstats.ROWS, "t1"))
                       for _ in range(3)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
        self.assertEqual(sorted(r["trial"] for r in evalstats.read_rows(run)), list(range(12)))
        self.assertLess(waiting[0], 12)  # the one being read is claimed, out of the backlog's count
        self.assertEqual(sorted(p.name for p in queue.iterdir()), [evalstats.DONE, evalstats.OWNER])

    # A relaunch makes the queue afresh with a new token: the old worker exits rather than read beside the new one.
    def test_a_worker_whose_token_is_gone_exits(self) -> None:
        run = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, run)
        queue = run / evalstats.QUEUE
        queue.mkdir()
        (queue / evalstats.OWNER).write_text("new")
        (queue / "T00000.json").write_text("{}")
        evalstats.worker(queue, run / evalstats.ROWS, "old")
        self.assertEqual(evalstats.read_rows(run), [])


class TestTable(unittest.TestCase):
    @staticmethod
    def rows(cand: list[int], champ: list[int], style_every: int = 0) -> list[dict]:
        out = []
        for t, (c, b) in enumerate(zip(cand, champ)):
            base = dict.fromkeys(evalstats.METRICS, 1)
            games = [{"i": i, "stats": {**base, "crossings": [c, c, b, b][i], "first_split": None if i == 3 else 5}}
                     for i in range(4)]
            out.append({"trial": t, "opponent": "swarm", "map": f"{'b' if style_every and t % style_every else 'a'}.map",
                        "games": games, "cpu_s": 2.0})
        return out

    def table(self, rows: list[dict]) -> dict:
        run = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, run)
        (run / evalstats.ROWS).write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"torn')
        return evalstats.table(run, lambda m: Path(m).stem, {"queued": len(rows)})

    def row(self, table: dict, metric: str, group: str = "all") -> list:
        return next(r for r in table["rows"] if r[0] == metric and r[1] == group)

    def test_pairs_by_side_and_corrects_the_moves_for_every_row(self) -> None:
        table = self.table(self.rows([5] * 30, [3] * 30))  # the candidate crosses more in all 60 pairs
        self.assertEqual((table["trials"], table["pairs"], table["queued"]), (30, 60, 30))
        metric, group, n, cand, champ, higher, lower, p = self.row(table, "crossings")
        self.assertEqual((n, cand, champ, higher, lower), (30, 5, 3, 30, 0))  # a trial's two sides are one unit
        self.assertLess(p * len(table["rows"]), 0.1)
        self.assertEqual(self.row(table, "first_split")[2:5], [30, 5, 5])  # B's champion has no split: A only
        self.assertEqual(self.row(table, "deaths")[7], 1.0)  # all ties
        self.assertEqual(len(table["moves"]), 1)
        self.assertTrue(table["moves"][0].startswith("portal crossings (all) 5 v 3 a game, n 30 trials, 30 of 30"))
        self.assertIn("portal crossings (all) 5 v 3", evalstats.moves_line(table))

    # 5 of 7 changed trials one way is p 0.45 two-sided: not a move, and a style under MIN_TRIALS has no rows.
    def test_luck_sized_splits_are_no_moves(self) -> None:
        table = self.table(self.rows([5, 5, 5, 5, 5, 3, 3] + [4] * 3, [4] * 10, style_every=4))
        self.assertEqual(self.row(table, "crossings")[5:7], [5, 2])
        self.assertEqual(table["moves"], [])
        self.assertEqual({r[1] for r in table["rows"]}, {"all", "b"})  # "a" has 3 trials: under 5
        self.assertIn("no move past chance over 20 game pairs", evalstats.moves_line(table))
        self.assertIn("| portal crossings | all | 10 |", evalstats.markdown(table))


class TestHealth(unittest.TestCase):
    def test_first_firing_once_then_a_run_that_read_nothing(self) -> None:
        state = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, state)
        run = state / "runs" / "E0001"
        run.mkdir(parents=True)
        (run / "sprt.json").write_text(json.dumps({"stats_queue": {"queued": 2, "skipped": 9}}))
        (run / evalstats.ROWS).write_text(json.dumps({"trial": 0, "map": "m", "games": [{"i": 0, "stats": {}}]}) + "\n")
        old = time.time() - 600  # past the drain window, when its worker may still be reading
        os.utime(run / "sprt.json", (old, old))
        seen: dict = {}
        self.assertEqual([a.key for a in health.behaviour_stats(state, time.time(), seen)], ["behaviour-stats:first"])
        self.assertEqual(health.behaviour_stats(state, time.time(), seen), [])
        later = state / "runs" / "E0002"
        later.mkdir()
        (later / "sprt.json").write_text(json.dumps({"stats_queue": {"queued": 0, "skipped": 40}}))
        self.assertEqual(health.behaviour_stats(state, time.time(), seen), [])  # just ended: not judged yet
        os.utime(later / "sprt.json", (old + 1, old + 1))
        [alert] = health.behaviour_stats(state, time.time(), seen)
        self.assertEqual(alert.key, "behaviour-stats:E0002")
        self.assertIn("queued no trial (40 skipped", alert.text)


if __name__ == "__main__":
    unittest.main()
