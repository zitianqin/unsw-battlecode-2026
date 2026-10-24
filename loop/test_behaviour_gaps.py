"""behaviour_gaps' action counts on real replays and its report's incremental reads (loop/behaviour_gaps.py).

Run from loop/: python -m unittest test_behaviour_gaps"""

import testhome  # before anything that reads live paths
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import behaviour_gaps as gaps
import health
import replays
import watch

REPLAYS = Path(__file__).resolve().parent.parent / "tools/testdata"


class TestBehaviourGaps(unittest.TestCase):
    def test_every_first_step_crossing_and_split_is_a_hit_and_a_passed_up_portal_is_not(self):
        # Trauma 392179: A crossed 43 times, each on its move's first step, and split 259 times; B sat beside a
        # portal whose far side it had never seen 22 times and never crossed (the aversion this report exists for).
        path = REPLAYS / "trauma-392179.replay"
        game = replays.analyse(replays.load_replay(path))
        a, b = gaps.game_counts(path, "A")["rows"], gaps.game_counts(path, "B")["rows"]
        self.assertEqual(a[gaps.UNSEEN][1] + a[gaps.SEEN][1], len(game.teams["A"].crossings))
        self.assertEqual(sum(a.get(row, [0, 0])[1] for row in gaps.SPLITS), len(game.teams["A"].splits))
        self.assertEqual(b[gaps.UNSEEN][:2], [22, 0])
        self.assertNotIn(gaps.SEEN, b)

    def test_a_ram_is_a_hit_at_its_chance(self):
        # Default 451387: A's 7 head-ons into enemy heads (watch.rams) all start within RAM_REACH of the victim.
        path = REPLAYS / "default-451387.replay"
        counts = gaps.game_counts(path, "A")["rows"]
        rams = sum(counts.get(row, [0, 0])[1] for row in gaps.NO_AFTERMATH)
        self.assertEqual(rams, len(watch.rams(watch.watch(path), "A")))
        self.assertEqual(rams, 7)

    # M100: a leader's rams on heads 2+ shorter were flagged for us to copy while each lost Cutlery about a segment.
    # A ram's payoff is watch's M51 swing, and a gap whose leader clearly pays less is shown, not flagged.
    def test_a_rate_gap_is_flagged_only_where_the_leaders_action_pays_no_worse(self):
        path = REPLAYS / "default-451387.replay"
        counts = gaps.game_counts(path, "A")["rows"]
        swings = [ram.swing for ram in watch.rams(watch.watch(path), "A")]
        self.assertAlmostEqual(sum(gaps.payoff(row, [{"rows": counts}]) * counts[row][4]
                                   for row in gaps.NO_AFTERMATH if counts.get(row, [0] * 7)[4]), sum(swings))
        self.assertIsNotNone(gaps.payoff(gaps.SPRINT_BLIND, [{"rows": counts}]))  # length 10 rounds on, both kinds

        def games(hits: int, swing: int) -> list[dict]:
            return [{"map": "m", "rows": {gaps.RAM_SHORTER: [200, hits, 0, swing * hits, hits, 0, 0]}}
                    for _ in range(10)]

        for leader_swing, flagged in ((-1, False), (2, True)):
            text = gaps.report_text({"us": games(2, 1), "us ranked": [], 7: games(40, leader_swing)}, {7: "top"}, {},
                                    "", time.time())
            self.assertEqual("**FLAG**" in text, flagged, leader_swing)
            self.assertEqual("their rams swing -1.00 on 400, below 0" in text, not flagged)

        # A sprint the leader takes more, paying less than ours: our payoff on a handful of turns is a coin flip and
        # clears nothing; on PAYOFF_TURNS+ turns, clearly above theirs, it does.
        def sprints(hits: int, gains: list[int]) -> list[dict]:
            return [{"map": "m", "rows": {gaps.SPRINT_BLIND: [200, hits, 0, gain * hits, hits, 0, 200 - hits]}}
                    for gain in gains]

        leader = sprints(40, [0, 1] * 5)  # +0.5 a sprint on 400
        for hits, flagged in ((1, True), (gaps.PAYOFF_TURNS // 5, False)):
            ours = sprints(hits, [2, 3] * 5)  # +2.5 a sprint
            text = gaps.report_text({"us": ours, "us ranked": [], 7: leader}, {7: "top"}, {}, "", time.time())
            self.assertEqual("**FLAG**" in text, flagged, hits)
            self.assertEqual("theirs pays +0.50 v our +2.50" in text, not flagged)

    def test_the_report_reads_within_its_budget_withholds_flags_until_done_and_retries_a_bad_replay(self):
        state = Path(tempfile.mkdtemp(dir=os.environ["BC_LOOP_HOME"]))
        (state / "online-replays").mkdir()
        (state / "bad.replay").write_bytes(b"truncated")
        row = lambda name, side, at, **more: {"replay": str(REPLAYS / name), "side": side, "requested_at": at, **more}
        library = {"online": {"games": [row("trauma-392179.replay", "A", "1"), row("trauma-95034.replay", "A", "2"),
                                        {**row("", "A", "3"), "replay": str(state / "bad.replay")}]},
                   "leaders": {"games": [row("trauma-392179.replay", "B", "1", team_id=7, ranked=True),
                                         row("trauma-95034.replay", "B", "2", team_id=7, ranked=False)]}}
        (state / gaps.LIBRARY).write_text(json.dumps(library))
        (state / "study-history.jsonl").write_text(json.dumps({"leaders": [{"rank": 1, "id": 7, "name": "top"}]}))
        (state / "discord").mkdir()  # a team is its id: the leaderboard's current name labels it (MISSES 75)
        (state / "discord/teams.json").write_text(json.dumps(
            {"teams": {"7": {"name": "renamed", "dev": False, "has_bot": True}}}))
        self.assertIn("read 1 replays, 3 left", gaps.main(state, budget=1))
        self.assertIn("Flags withheld: 3 selected games are not read yet", (state / gaps.REPORT).read_text())
        self.assertIn("read 3 replays, 0 left", gaps.main(state, budget=5))  # the unranked leader game stays out
        text = (state / gaps.REPORT).read_text()
        self.assertNotIn("not read yet", text)
        self.assertIn("| portal crossing | far side no teammate has seen |", text)
        self.assertIn("renamed's (team 7) latest 1 ranked", text)
        self.assertIn("1 could not be read", text)
        self.assertEqual(gaps.main(state), "")  # nothing new since
        for reads in (1, 1, 0):  # the bad replay is read again at the next runs, READ_TRIES (3) reads in all
            os.utime(state / gaps.LIBRARY, (time.time() + 10,) * 2)
            self.assertIn(f"read {reads} replays", gaps.main(state))
        cache = json.loads((state / gaps.CACHE).read_text())
        cache["analyser"] = "older"  # a reset rebuilds the report though the library hasn't changed
        (state / gaps.CACHE).write_text(json.dumps(cache))
        os.utime(state / gaps.REPORT, (time.time() + 20,) * 2)
        self.assertIn("read 4 replays, 0 left", gaps.main(state))

    def test_a_flag_lists_the_ideas_at_work_whose_title_names_its_action(self):
        # I950 (sprints, priority 2) quotes the portal flags in its hypothesis as evidence: it covers sprints only.
        i950 = {"id": "I950", "status": "todo", "priority": 2, "title": "Measure first, then sprint for food",
                "hypothesis": "Evidence (loop/behaviour_gaps.py): portal crossing 5% v 43%; sprint 0% v 1.5%"}
        tried = {**i950, "id": "I1", "status": "tried"}
        param = {**i950, "id": "I2", "title": "Tune params from behaviour gaps"}  # "params" is no ram
        self.assertEqual(gaps.citing([i950, tried, param]), {"sprint": ["I950"]})

    def test_health_alerts_when_the_report_falls_behind_the_library(self):
        state = Path(tempfile.mkdtemp(dir=os.environ["BC_LOOP_HOME"]))
        (state / "online-replays").mkdir()
        (state / gaps.LIBRARY).write_text("{}")
        now = (state / gaps.LIBRARY).stat().st_mtime
        self.assertEqual(health.behaviour_gaps(state, now + 60), [])  # the next upkeep tick writes it
        lag = health.BEHAVIOUR_GAPS_LAG
        self.assertEqual([a.key for a in health.behaviour_gaps(state, now + lag)], ["behaviour gaps"])  # never written
        (state / gaps.REPORT).write_text("")
        os.utime(state / gaps.REPORT, (now - lag + 60,) * 2)
        self.assertEqual(health.behaviour_gaps(state, now), [])
        os.utime(state / gaps.REPORT, (now - lag - 60,) * 2)
        self.assertEqual(len(health.behaviour_gaps(state, now)), 1)


if __name__ == "__main__":
    unittest.main()
