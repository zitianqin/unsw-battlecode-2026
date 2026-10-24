"""Fit rows from our online games (loop/online_fit.py): which games a run replays, what it saves and skips, that a
second run redoes nothing, and that only the newest versions' folders stay. The replay itself (tools/why.py) is
checked by why's own tests and the measurement in online_fit's docstring; here it is a fake."""

import testhome  # before anything that reads live paths
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import online
import online_fit

# A real sweep row (online-replays/10774/index.json, 2026-09-29) and the two the run must leave out: a loss kept because
# we lost (online.selected) and a dev game (online.against_dev).
ROW = {"id": 530208, "submission_id": 10774, "submission_from": "replay", "opponent": 384, "opponent_submission_id": None,
       "side": "A", "map_id": 4, "map_hash": None, "ranked": False, "requested_at": "2026-09-28T22:57:52.901Z",
       "summary": {"result": {"endReason": "teamEliminated", "endRound": 477, "winner": "A"}}}


class TestOnlineFit(unittest.TestCase):
    def test_a_run_saves_or_skips_each_result_blind_ladder_game_once_and_keeps_the_newest_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for sid in (10711, 10751, 10774):
                (state / "online-replays" / str(sid)).mkdir(parents=True)
            games = [{**ROW, "id": 530208 + k, **extra} for k, extra in
                     enumerate(({}, {}, {"kept": "loss"}, {"opponent": 545, "dev": True}))]
            for game in games:
                game["replay"] = str(state / "online-replays/10774" / f"{game['id']}.replay")
                Path(game["replay"]).write_bytes(b"replay")
            online.save(state / "online-replays/10774/index.json", {"games": games})
            online.save(state / "online-replays/10751/index.json", {"games": []})
            online.save(state / "online-replays/10711/index.json", {"games": []})
            (state / "online-replays/fit/10711").mkdir(parents=True)  # an old version's folder
            self.assertEqual([g["id"] for _, g in online_fit.pending(state)], [530208, 530209])
            outcomes = iter([("saved", b"gzipped"), ("not reproducible", "layout-1")])
            calls = []
            fake = lambda state_, game, commit, built, work: calls.append(game["id"]) or next(outcomes)  # noqa: E731
            with patch.object(online_fit, "replay_one", fake), patch("why.submission_commit", lambda *a: "a5e45523"), \
                    patch("why.wait_turn", lambda: None):
                online_fit.run(state)
                online_fit.run(state)  # nothing left: replays nothing
            self.assertEqual(calls, [530208, 530209])
            saved = json.loads((state / "online-replays/fit/10774/index.json").read_text())
            (row,) = saved["games"]
            self.assertEqual((row["id"], row["commit"], row["side"], "summary" in row), (530208, "a5e45523", "A", False))
            self.assertEqual(Path(row["replay"]).read_bytes(), b"gzipped")
            self.assertEqual(saved["skipped"]["530209"]["reason"], "not reproducible")
            self.assertFalse((state / "online-replays/fit/10711").exists())  # not among the newest two
            record = json.loads((state / online_fit.RECORD).read_text())
            self.assertEqual(record["last"]["counts"], {})  # the second run's
            self.assertIsNone(record["error"])

    # Review of online_fit: one game raising (tools/why.py exits on some) or a version's build failing stopped the batch
    # for good, and the old folders' deletion came after both, so the 2 GB bound stopped holding. Now each is a skip
    # tried again RETRY_S later, RETRIES times, a failed build skips the version's other games without building again,
    # and a variant pass that raises still leaves only the newest folders.
    def test_a_bad_game_or_build_is_a_retried_skip_and_the_old_folders_go_whatever_happens(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online-replays/10774").mkdir(parents=True)
            games = [{**ROW, "id": 530208 + k, "replay": str(state / f"online-replays/10774/{530208 + k}.replay")}
                     for k in range(3)]
            for game in games:
                Path(game["replay"]).write_bytes(b"replay")
            online.save(state / "online-replays/10774/index.json", {"games": games})
            (state / "online-replays/fit/10711").mkdir(parents=True)
            replay = type("Replay", (), {"map": "MAP 5 5\n", "seed": type("Seed", (), {"which": lambda self: "value"})()})()
            builds = []
            def build(*args, **kwargs):
                builds.append(1)
                raise SystemExit("params.hpp has no `bool debugIndicator = false;` to switch on")
            def play(replay_, wasm, side, game_map):
                return type("Played", (), {"difference": None})()
            first = iter([SystemExit("not reproducible")])
            def load(path):
                if path.name.startswith("530208"):
                    raise next(first)
                return replay
            with patch("why.submission_commit", lambda *a: "a5e45523"), patch("why.wait_turn", lambda: None), \
                    patch("replays.load_replay", load), patch("why.candidate_maps", lambda *a: [("m", "t")]), \
                    patch("why.play", play), patch("why.prepare_source", build), \
                    patch.object(online_fit, "rebuild_variants", side_effect=RuntimeError("fit")):
                with self.assertRaises(RuntimeError):
                    online_fit.run(state)
            skipped = json.loads((state / "online-replays/fit/10774/index.json").read_text())["skipped"]
            self.assertEqual([skipped[str(g["id"])]["reason"][:22] for g in games],
                             ["error: SystemExit: not", "build failed: SystemEx", "build failed: SystemEx"])
            self.assertEqual(len(builds), 1)
            self.assertFalse((state / "online-replays/fit/10711").exists())
            at = skipped["530208"]["at"]
            self.assertEqual(online_fit.pending(state, at + 60), [])
            self.assertEqual(len(online_fit.pending(state, at + online_fit.RETRY_S + 1)), 3)
            done = online_fit.index_of(state, 10774)
            done["skipped"] = {k: {**v, "tries": online_fit.RETRIES} for k, v in done["skipped"].items()}
            online.save(online_fit.folder(state, 10774) / "index.json", done)
            self.assertEqual(online_fit.pending(state, at + online_fit.RETRY_S + 1), [])

    # A layout whose rebuild failed was refitted every run on its same games; now it waits for VARIANT_GROWTH times as
    # many, and one that raises is recorded as tried with its error.
    def test_a_failed_variant_layout_waits_for_its_games_to_grow(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for where in ("online-replays/10774", "online-replays/fit/10774"):
                (state / where).mkdir(parents=True)
            games = [{**ROW, "id": 530208 + k} for k in range(12)]
            online.save(state / "online-replays/10774/index.json", {"games": games})
            skip = lambda n: online.save(online_fit.folder(state, 10774) / "index.json", {"games": [], "skipped": {
                str(g["id"]): {"reason": "not reproducible", "layout": "L"} for g in games[:n]}})
            record = {"variants": {"L": {"games": 8, "result": "no fit: no pearl range fits the owner at (3, 4)"}}}
            tried = []
            def rebuild(state_, layout, waiting, last, deadline):
                tried.append(len(waiting))
                raise ValueError("bad seed")
            with patch.object(online_fit, "rebuild_layout", rebuild):
                skip(11)
                self.assertEqual(online_fit.rebuild_variants(state, record), [])
                skip(12)
                (line,) = online_fit.rebuild_variants(state, record)
            self.assertEqual(tried, [12])
            self.assertEqual(record["variants"]["L"]["games"], 12)
            self.assertIn("error: ValueError: bad seed", line)

    # The upkeep ticks every minute: with nothing waiting, the indexes are read again only CHECK_S later; with games
    # waiting, one background run starts, and none while it lives.
    def test_the_tick_starts_one_run_while_games_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(online_fit, "pending", lambda s, now=None: []), patch("subprocess.Popen") as popen:
                self.assertIsNone(online_fit.tick(state, 1e6))
                with patch.object(online_fit, "pending", lambda s, now=None: [(1, {})]):
                    self.assertIsNone(online_fit.tick(state, 1e6 + online_fit.CHECK_S - 1))
                    popen.return_value.pid = 4242
                    self.assertIn("pid 4242", online_fit.tick(state, 1e6 + online_fit.CHECK_S))
                    with patch.object(online_fit, "alive", lambda pid: pid == 4242):
                        self.assertIsNone(online_fit.tick(state, 1e6 + online_fit.CHECK_S + online_fit.EVERY))
            self.assertEqual(popen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
