"""The opponent screen's one referee run (loop/screenbatch.py), on real rows: testdata/screen-games-E0127.jsonl is the
first four lines of E0127's pool-swarm-candidate.jsonl (two maps, both sides), as referee/run.py wrote them."""

import testhome  # before anything that reads live paths
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import screenbatch

sys.path.insert(0, str(driver.REPO / "referee"))
import run as referee  # noqa: E402  the referee's Job, GameResult and to_json, as play() uses them

DATA = Path(__file__).resolve().parent / "testdata"
ROWS = [json.loads(line) for line in (DATA / "screen-games-E0127.jsonl").read_text().splitlines()]


def result(row: dict, **changes) -> referee.GameResult:
    stats = {key: referee.BotStats(**row[key]) for key in ("a", "b")}
    return referee.GameResult(**{**row, **stats, **changes})


class Play(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.maps = list(dict.fromkeys(row["map"] for row in ROWS))
        self.streams: list[list[tuple[str, str, str, str]]] = []

    # Plays `groups` (bot, opponent) on both real maps with the referee's stream faked: every job gets the real row of
    # its map and side, with `error` for the jobs `failing` picks. Returns the exit code and each group's written rows.
    def play(self, groups: list[tuple[str, str]], failing=lambda job: False) -> tuple[int, list[list[dict]]]:
        outs = [self.folder / f"pool-{b}-{a}.jsonl" for a, b in groups]
        plan = {"workers": "auto", "groups": [{"name": b, "a": f"/bots/{a}", "b": f"/gauntlet/{b}", "maps": self.maps,
                                               "out": str(out)} for (a, b), out in zip(groups, outs)]}
        (self.folder / "plan.json").write_text(json.dumps(plan))
        rows = {(row["map"], row["a_side"]): row for row in ROWS}

        def stream(jobs, bots, workers, sandbox):
            self.assertEqual((workers, sandbox), ("auto", False))
            self.streams.append([])
            for job in jobs:
                self.streams[-1].append((job.a.name, job.b.name, str(job.map), job.a_side))
                row = rows[(str(job.map), job.a_side)]
                yield result(row, error="timed out", winner=None) if failing(job) else result(row)

        with patch.object(referee, "stream_games", stream):
            code = screenbatch.play(self.folder / "plan.json")
        return code, [[json.loads(line) for line in out.read_text().splitlines()] if out.exists() else None
                      for out in outs]

    def test_every_groups_games_play_in_one_stream_and_are_written_as_run_py_writes_them(self) -> None:
        code, written = self.play([("cand", "swarm"), ("bot", "swarm"), ("cand", "folder")])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.streams), 1)  # one referee run, not one a group
        self.assertEqual(self.streams[0], [(a, b, m, side) for a, b in (("cand", "swarm"), ("bot", "swarm"),
                                                                         ("cand", "folder"))
                                           for m in self.maps for side in "AB"])
        self.assertEqual(written, [ROWS] * 3)  # the real rows, byte for byte in content and in map then side order

    def test_a_failed_game_marks_the_run_and_every_group_is_still_written(self) -> None:
        code, written = self.play([("cand", "swarm"), ("bot", "swarm")],
                                  failing=lambda job: job.a.name == "bot" and job.a_side == "B")
        self.assertEqual(code, screenbatch.FAILED_GAMES)  # not 1, which a traceback exits with
        self.assertEqual(written[0], ROWS)
        self.assertEqual([row["error"] for row in written[1]], [None, "timed out", None, "timed out"])

    # A native judge check that differed while the run's native games were at risk stops the run there (review 4,
    # 2026-09-29: the screen played on, and its gate stood on those games): its report goes beside the plan for the
    # driver, which errors the screen (driver.NativeFault), and each native row names the key it played under.
    def test_a_native_check_that_differed_stops_the_run_and_reports_it(self) -> None:
        report = {"games": 4, "since_check": 4, "checked": None,
                  "off": {"x": {"at": 1.0, "said": "here: check differs: replay", "check": True}}}
        rows = {(row["map"], row["a_side"]): row for row in ROWS}

        def stream(jobs, bots, workers, sandbox):
            for job in jobs:
                yield result(rows[(str(job.map), job.a_side)], native=True)

        (self.folder / "plan.json").write_text(json.dumps({"workers": "auto", "groups": [
            {"name": b, "a": "/bots/cand", "b": f"/gauntlet/{b}", "maps": self.maps,
             "out": str(self.folder / f"pool-{b}.jsonl")} for b in ("swarm", "folder")]}))
        with patch.object(referee, "stream_games", stream), patch.object(referee, "native_report",
                                                                         return_value=report), \
                patch.object(referee.native_judge, "process_key", return_value="k1"):
            self.assertEqual(screenbatch.play(self.folder / "plan.json"), screenbatch.FAILED_GAMES)
        written = [json.loads(line) for line in (self.folder / "pool-swarm.jsonl").read_text().splitlines()]
        self.assertEqual({row["native"] for row in written}, {"k1"})
        self.assertFalse((self.folder / "pool-folder.jsonl").exists())
        self.assertEqual(json.loads((self.folder / "plan-native.json").read_text()), report)

    # A target batch's plan (driver.target_batch) keeps every replay: each row names the file the scheduler wrote for
    # its game, which the driver then reads and deletes.
    def test_with_replays_each_row_names_its_games_replay(self) -> None:
        replays = self.folder / "target-replays"
        replays.mkdir()
        out = self.folder / "target-swarm-candidate.jsonl"
        plan = {"workers": "auto", "replays": str(replays),
                "groups": [{"name": "swarm candidate", "a": "/bots/cand", "b": "/gauntlet/swarm", "maps": self.maps,
                            "out": str(out)}]}
        (self.folder / "plan.json").write_text(json.dumps(plan))
        rows = {(row["map"], row["a_side"]): row for row in ROWS}

        def stream(jobs, bots, workers, sandbox, replays=None):  # named and written as run.Scheduler.play does
            for index, job in enumerate(jobs):
                (replays / f"{index:04d}-{job.map.stem}-a{job.a_side}.replay").write_text(f"{job.map} {job.a_side}")
                yield result(rows[(str(job.map), job.a_side)])

        with patch.object(referee, "stream_games", stream):
            self.assertEqual(screenbatch.play(self.folder / "plan.json"), 0)
        written = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([{k: v for k, v in row.items() if k != "replay"} for row in written], ROWS)
        self.assertEqual([Path(row["replay"]).read_text() for row in written],
                         [f"{row['map']} {row['a_side']}" for row in ROWS])

    # A two-stage screen's second stage (driver.check_pool's `when` groups) through the real run.Scheduler.play, only
    # each game faked: with the swarm behind, its rest is started while the folder's first-stage games still play (they
    # wait for it) and the folder's, level, is skipped; with neither behind, both are skipped and the stream ends
    # without asking "none ready yet" of a scheduler with no game left running (which raises).
    def test_a_second_stage_is_queued_as_its_entry_decides_and_skipped_when_not_behind(self) -> None:
        pairs = [("cand", "swarm"), ("bot", "swarm"), ("cand", "folder"), ("bot", "folder")]
        groups = [{"name": b, "a": f"/bots/{a}", "b": f"/gauntlet/{b}", "maps": self.maps,
                   "out": str(self.folder / f"pool-{b}-{a}.jsonl")} for a, b in pairs]
        side = lambda i: {"rows": [], "groups": [i]}  # noqa: E731
        for k in (0, 2):  # each entry's rest, candidate then base, on its own first stage
            groups += [{**groups[k + j], "out": str(self.folder / f"rest-{k + j}.jsonl"),
                        "when": [[{"candidate": side(k), "base": side(k + 1)}]]} for j in (0, 1)]
        (self.folder / "plan.json").write_text(json.dumps({"workers": "auto", "groups": groups}))
        rows = {(row["map"], row["a_side"]): row for row in ROWS}
        for behind in (True, False):
            with self.subTest(behind=behind):
                for out in self.folder.glob("*.jsonl"):
                    out.unlink()
                lock, swarm, rest_started, waited = threading.Lock(), [0], threading.Event(), []

                def play_here(scheduler, job, path):
                    if job.b.name == "swarm":
                        with lock:
                            swarm[0] += 1
                            if swarm[0] > 8:  # its first stage is 8 games, all in before its rest is queued
                                rest_started.set()
                    elif behind:
                        waited.append(rest_started.wait(10))
                    return referee.RawGame("", 0, None, 0.0, host="here")

                def parse_game(job, raw):
                    row = rows[(str(job.map), job.a_side)]
                    lost = behind and (job.a.name, job.b.name, job.a_side) == ("cand", "swarm", "A") \
                        and str(job.map) == self.maps[0]
                    return result(row, winner="b") if lost else result(row)

                def stream(jobs, bots, workers, sandbox):  # 16 slots here and no hosts: every first-stage game at once
                    yield from referee.Scheduler(16, False, {bot: None for bot in bots}).play(jobs, self.folder)

                with patch.object(referee, "stream_games", stream), patch.object(referee, "parse_game", parse_game), \
                        patch.object(referee.Scheduler, "play_here", play_here):
                    self.assertEqual(screenbatch.play(self.folder / "plan.json"), 0)
                self.assertEqual(json.loads((self.folder / "plan-skipped.json").read_text()),
                                 [6, 7] if behind else [4, 5, 6, 7])
                self.assertEqual([Path(g["out"]).exists() for g in groups[4:]], [behind, behind, False, False])
                self.assertEqual(swarm[0], 16 if behind else 8)
                self.assertEqual(waited, [True] * 8 if behind else [])  # the rest began before the folder's games ended
                self.assertEqual([json.loads(line) for line in Path(groups[2]["out"]).read_text().splitlines()], ROWS)


class Batch(unittest.TestCase):
    """driver.play_batch reads each group's rows back and names the group of a failed or missing game."""

    def test_it_names_the_group_with_a_failed_or_missing_game_and_returns_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            maps = list(dict.fromkeys(row["map"] for row in ROWS))
            groups = [{"name": name, "a": "/bots/cand", "b": f"/gauntlet/{name}", "maps": maps,
                       "out": str(folder / f"pool-{name}-candidate.jsonl")} for name in ("swarm", "folder", "rammer")]
            (folder / "pool-rammer-candidate.jsonl").write_text("stale\n")  # an earlier run's file is never read

            def referee(script, args, **kwargs):
                self.assertEqual(json.loads(Path(args[0]).read_text())["groups"], groups)
                Path(groups[0]["out"]).write_text("".join(json.dumps(row) + "\n" for row in ROWS))
                Path(groups[1]["out"]).write_text("".join(
                    json.dumps({**row, "error": "timed out"} if i == 2 else row) + "\n" for i, row in enumerate(ROWS)))
                return driver.subprocess.CompletedProcess([], screenbatch.FAILED_GAMES, "", "")

            with patch.object(driver, "referee", side_effect=referee):
                (swarm, ok), (folder_rows, failed), (rammer, missing) = driver.play_batch(groups)
        self.assertEqual((swarm, ok), (ROWS, ""))
        self.assertEqual(folder_rows, [])
        self.assertIn("pool game failed: folder: ", failed)
        self.assertIn("(as A): timed out", failed)
        self.assertEqual(rammer, [])
        self.assertIn("pool games against rammer are missing", missing)

    def test_a_run_that_died_raises_with_its_traceback_rather_than_blaming_an_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            group = {"name": "swarm", "a": "/bots/cand", "b": "/gauntlet/swarm", "maps": ["/m/one.map"],
                     "out": str(Path(directory) / "pool-swarm-candidate.jsonl")}
            died = driver.subprocess.CompletedProcess([], 1, "", "Traceback ...\nKeyError: 'groups'")
            with patch.object(driver, "referee", return_value=died), \
                    self.assertRaisesRegex(RuntimeError, r"(?s)screen games failed \(exit 1\): Traceback .*KeyError"):
                driver.play_batch([group])


if __name__ == "__main__":
    unittest.main()
