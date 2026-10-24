"""Checks tools/queue_page.py on a small temporary loop home: its claim order is the order driver.claim_idea takes
the ideas in (next first, then priority and rank), and its next supervised claim is claim_idea(0)'s, fillers first;
held-back ideas are listed apart with why; building leaves every loop-home file as it was, with an evaluation and a
part-time host outage on; a half-written backlog serves the last good payload marked stale, cached like a good one;
/history.json draws rows older than a field as gaps and newer ones by priority, thinned to HISTORY_POINTS newest
first; the server answers every route and refuses other Host names.

    PYTHONPATH=loop .venv/bin/python -m unittest tools.test_queue_page
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import testhome  # noqa: E402,F401  before anything that reads live paths
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from io import StringIO  # noqa: E402
from unittest import mock  # noqa: E402

import driver  # noqa: E402
import queue_page  # noqa: E402
import throughput  # noqa: E402
from home import loop_home  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "loop/testdata/throughput"
IDEAS = [
    {"id": "I1", "priority": 1, "status": "todo"},
    {"id": "I2", "priority": 2, "status": "todo", "next": True},
    {"id": "I3", "priority": 1, "status": "todo", "rank": 1},
    {"id": "I4", "priority": 1, "status": "todo", "after": ["I5"]},
    {"id": "I5", "priority": 2, "status": "todo"},
    {"id": "I6", "priority": 1, "status": "followup"},
    {"id": "I7", "priority": 1, "status": "trying:lane9"},
    {"id": "I8", "priority": 1, "status": "todo", "category": "retest"},
    {"id": "I10", "priority": 1, "status": "todo", "waits_for_style": "no-such-style"},
]


class QueuePage(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loop_home()
        for path in self.state.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        rows = [{"category": "strategy", "title": f"idea {i['id']}", "hypothesis": "h", "source": "test",
                 "history": [], **i} for i in IDEAS]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        for exp, idea, priority, status in (("E0001", "I7", 1, "running"), ("E0002", "I9", 2, "finished")):
            (self.state / "runs" / exp).mkdir(parents=True, exist_ok=True)
            (self.state / "runs" / exp / "experiment.json").write_text(
                json.dumps({"idea": {"id": idea, "title": "t", "priority": priority}}))
            (self.state / "runs" / exp / "worker.json").write_text(json.dumps({"status": status}))
        (self.state / "evaluation-queue").mkdir(exist_ok=True)
        self.coded = round(time.time(), 3)
        (self.state / "evaluation-queue" / f"{self.coded:017.3f}-E0002.json").write_text(
            json.dumps({"pid": os.getpid(), "experiment": "E0002"}))
        queue_page._last.clear()
        queue_page._history.clear()

    def test_claim_order_is_the_claims_order_and_blocked_ideas_are_apart(self) -> None:
        body = queue_page.payload(self.state)
        order = [r["id"] for r in body["claim_order"]]
        # next at priority 2 first, then rank, then id; I5 (priority 2) claims at priority 1 as I4, waiting on it, would
        self.assertEqual(order, ["I2", "I3", "I1", "I5", "I8"])
        with redirect_stdout(StringIO()):
            claimed = [driver.claim_idea(lane)["id"] for lane in range(1, len(order) + 1)]
            self.assertIsNone(driver.claim_idea(len(order) + 1))  # the rest wait on I5 or a map style
        self.assertEqual(claimed, order)
        self.assertEqual([(r["id"], r["why"]) for r in body["blocked"]],
                         [("I4", "after I5"), ("I10", "map style no-such-style, which the referee's mapgen lacks")])
        self.assertEqual([(r["experiment"], r["idea"], r["live"]) for r in body["queue"]], [("E0002", "I9", True)])
        self.assertEqual([(r["id"], r["experiment"], r["worker"]) for r in body["coding"]], [("I7", "E0001", "running")])
        self.assertEqual(body["counts"]["1"], {"todo": 5, "followup": 1})

    # driver.enter_queue names tickets <rank>-<coded>-E####: the rank was read as the coding-done time.
    def test_a_ranked_ticket_reads_its_time_and_experiment(self) -> None:
        (self.state / "evaluation-queue" / f"0-{self.coded - 60:017.3f}-E0001.json").write_text(
            json.dumps({"pid": os.getpid()}))
        self.assertEqual([(r["experiment"], round(r["coded_at"], 3)) for r in queue_page.queue(self.state)],
                         [("E0001", round(self.coded - 60, 3)), ("E0002", self.coded)])

    # 1 Oct: a stop marker ends waiting and just-coded experiments as "blocked ...; candidate preserved" with no
    # ticket; sig asked to see them on the page.
    def test_stopped_candidates_are_listed_and_other_blocks_are_not(self) -> None:
        for exp_id, reason in (("E0007", "stop requested after coding; candidate preserved"),
                               ("E0008", "a prerequisite failed")):
            run = self.state / "runs" / exp_id
            run.mkdir(parents=True, exist_ok=True)
            (run / "experiment.json").write_text(json.dumps(
                {"verdict": "blocked", "reason": reason, "finished": time.time(), "idea": {"id": "I0001"}}))
        rows = queue_page.stopped(self.state, time.time(), [{"id": "I0001", "status": "todo"}])
        self.assertEqual([(r["experiment"], r["idea_status"]) for r in rows], [("E0007", "todo")])

    def test_the_next_supervised_claim_takes_fillers_first_as_claim_idea_does(self) -> None:
        ideas = driver.read_ideas()
        del next(i for i in ideas if i["id"] == "I2")["next"]
        driver.write_ideas(ideas)
        claim = queue_page.payload(self.state)["next_claim"]
        self.assertEqual((claim["rule"], [r["id"] for r in claim["ideas"]]), ("fillers", ["I8"]))
        with redirect_stdout(StringIO()):
            self.assertEqual(driver.claim_idea(0)["id"], "I8")

    def test_building_changes_no_loop_home_file(self) -> None:
        (self.state / "remote.json").write_text(json.dumps({"hosts": [{"name": "zz-pc", "part_time": True}]}))
        (self.state / "host-outages.json").write_text(json.dumps({"zz-pc": time.time() - 600}))
        (self.state / "evaluating.json").write_text(json.dumps({"pid": os.getpid(), "since": time.time()}))

        def files() -> dict[str, tuple[int, bytes]]:
            return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in self.state.rglob("*") if p.is_file()}

        before = files()
        body = queue_page.payload(self.state)
        self.assertEqual([r["host"] for r in body["outages"]], ["zz-pc"])
        self.assertIsNotNone(body["evaluating"])
        after = files()
        self.assertEqual(sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)), [])

    def test_a_half_written_backlog_serves_the_last_good_payload_marked_stale(self) -> None:
        now = time.time()
        good = queue_page.payload(self.state, now)
        with (self.state / "ideas.jsonl").open("a") as handle:
            handle.write('{"id": "I10", "prio')
        stale = queue_page.payload(self.state, now + queue_page.CACHE_S + 1)
        self.assertTrue(stale["stale"])
        self.assertIn("JSONDecodeError", stale["error"])
        self.assertEqual(stale["claim_order"], good["claim_order"])
        with mock.patch.object(queue_page, "build", side_effect=AssertionError("rebuilt")):
            self.assertIs(queue_page.payload(self.state, now + queue_page.CACHE_S + 2), stale)

    def test_history_draws_old_rows_as_gaps_and_new_rows_by_priority(self) -> None:
        # The live record's rows of 27 Sep 19:54-19:58 (one twin), from before coding, priorities and the backlog were
        # recorded, then the row the sampler writes now: E0001 being coded (I7, P1), E0002 queued (I9, P2).
        (self.state / throughput.LOG).write_text((DATA / "utilisation-twin-rows-sample.jsonl").read_text())
        throughput.sample(self.state, cores=8, load=0.0, memory_gb=16.0)
        points = queue_page.history(self.state)["points"]
        unsplit = [1, *[None] * len(queue_page.PRIORITIES)]
        # Each point is [at, backlog, queued, coding, evaluating].
        self.assertEqual((len(points), points[0][1:]), (6, [None, unsplit, None, unsplit]))
        self.assertEqual(points[-1][1:], [[8, 6, 2, 0, 0, 0], [1, 0, 1, 0, 0, 0], [1, 1, 0, 0, 0, 0], [0] * 6])
        with mock.patch.object(queue_page, "HISTORY_POINTS", 4):
            queue_page._history.clear()
            thinned = queue_page.history(self.state)
        self.assertEqual((thinned["every"], [p[0] for p in thinned["points"]]), (2, [p[0] for p in points[1::2]]))

    def test_the_server_answers_its_own_host_only(self) -> None:
        server = queue_page.HTTPServer(("127.0.0.1", 0), queue_page.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        self.assertIn(b"/queue.json", urllib.request.urlopen(url + "/").read())
        self.assertIn("claim_order", json.loads(urllib.request.urlopen(url + "/queue.json").read()))
        self.assertIn("points", json.loads(urllib.request.urlopen(url + "/history.json").read()))
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(urllib.request.Request(url + "/queue.json", headers={"Host": "evil.example"}))
        self.assertEqual(refused.exception.code, 403)
        refused.exception.close()

if __name__ == "__main__":
    unittest.main()
