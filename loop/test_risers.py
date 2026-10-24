"""The riser study (loop/risers.py) on real saved data, without a site call or a model: the pick over the site's
rating history (testdata/ratings-sample.json: GET /ratings at 2026-09-28 20:26 AEST, ranks 1-37 over the 49 h before
it), the window and the hourly check, ranked games only split at the climb's low (testdata/riser-battles-852.json:
six real pages of team 852's battle list, 20:29), bounded downloads, and the trusted side reading the worker's files.

    .venv/bin/python -m unittest test_risers
"""

import testhome  # before anything that reads live paths
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import health
import ladder
import risers

DATA = Path(__file__).resolve().parent / "testdata"
BOARD = json.loads((DATA / "ratings-sample.json").read_text())
BATTLES = [b for page in json.loads((DATA / "riser-battles-852.json").read_text())["pages"] for b in page]


# A row as pick gives it for team 852 (C--), with its low and peak inside the saved battle pages.
def row_852(low: str = "2026-09-28T03:00:00Z", peak: str = "2026-09-28T10:30:00Z") -> dict:
    return {"id": 852, "name": "C--", "rank": 17, "elo": 1940, "battles": 40, "earlier": 30, "net": 120,
            "climb": 150, "low_at": risers.moment(low), "low_elo": 1790, "peak_at": risers.moment(peak),
            "peak_elo": 1940, "path": [(risers.moment(low), 1790.0)], "p": 0.0002, "picked": True}


# tools/replays.py's three calls, served from the saved battle pages: `battles` keeps the requested span, `fetch`
# writes one ranked game per battle into --out (its replay file a stub), `summary` a checked summary.
class Site:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[0] == "battles":
            since, until = (risers.moment(args[args.index(flag) + 1]) for flag in ("--since", "--until"))
            team, pages = int(args[args.index("--team") + 1]), int(args[args.index("--max-pages") + 1])
            rows = [b for b in BATTLES
                    if team in (b["a"]["id"], b["b"]["id"]) and since <= risers.moment(b["at"]) <= until]
            kept = rows[:10 * pages]  # read back from `until`, 10 a page, newest first as the site lists them
            return json.dumps({"battles": kept[::-1], "complete": len(kept) == len(rows)})
        if args[0] == "fetch":
            out = Path(args[args.index("--out") + 1])
            ids = args[args.index("--battle") + 1:args.index("--max-games")]
            with (out / "index.jsonl").open("a") as index:
                for b in BATTLES:
                    if str(b["replayId"]) in ids:
                        (out / f"{b['replayId']}.replay").write_text("x")
                        index.write(json.dumps({"match": b["replayId"], "ranked": b["ranked"], "requestedAt": b["at"],
                                                "teamAId": b["a"]["id"], "teamBId": b["b"]["id"]}) + "\n")
            return ""
        match = int(Path(args[1]).stem)
        b = next(b for b in BATTLES if b["replayId"] == match)
        won = {"a": b["winsA"] > b["winsB"], "b": b["winsB"] > b["winsA"]}
        teams = {s.upper(): {"name": b[s]["name"], "id": b[s]["id"], "won": won[s],
                             "splits": {"count": 3 + 2 * (s == "b")}} for s in "ab"}
        return json.dumps({"checks": {"ok": True}, "map": {"name": "Trophy"}, "teams": teams})


# No test here reaches the site or starts a process whatever the clock says: the site read, tools/replays.py and every
# launch but git (champion_file) raise, so a test missing its stub fails instead of calling the site or a model.
REAL_POPEN = subprocess.Popen
GUARDS = []


def refuse(*args, **kwargs):
    raise RuntimeError("test_risers: no site read or process launch outside a stub")


def only_git(command, *args, **kwargs):
    if not (isinstance(command, list) and command and command[0] == "git"):
        refuse()
    return REAL_POPEN(command, *args, **kwargs)


def setUpModule() -> None:
    GUARDS.extend([patch.object(ladder, "get", refuse), patch.object(risers.maintenance, "command", refuse),
                   patch.object(subprocess, "Popen", only_git)])
    for guard in GUARDS:
        guard.start()


def tearDownModule() -> None:
    for guard in GUARDS:
        guard.stop()


GOOD = {"category": "strategy", "priority": 3, "title": "copy the riser's earlier split",
        "hypothesis": "C-- splits 5 a game after its rise against 3 before (n 6 games a side). Options: (1) split "
                      "earlier when the map is rich; (2) punish its young children; (3) I001 covers part. This is (1)."}


class Pick(unittest.TestCase):
    def test_the_first_run_picks_the_top_teams_whose_climb_luck_rarely_gives(self):
        rows, end, alpha = risers.pick(BOARD, risers.FIRST_WINDOW)
        self.assertEqual(len(rows), risers.TOP)  # dev teams and us left out, so rank 36 is looked at too
        ids = {r["id"] for r in rows}
        self.assertNotIn(157, ids)  # us
        self.assertNotIn(545, ids)  # dev test 1
        self.assertEqual(alpha, risers.chance.TRIGGER_P / risers.TOP)
        picked = {r["id"]: r for r in rows if r["picked"]}
        # Cutlery, C--, python.io and cheji bt rose 339-369 over the 48 h; cheji bt fell first, so its climb (+435)
        # is larger than its net (+244).
        self.assertEqual(set(picked), {306, 852, 147, 70})
        self.assertEqual((picked[70]["climb"], picked[70]["net"]), (435, 244))
        self.assertTrue(all(r["p"] <= alpha for r in picked.values()))
        # BloodBath's +260 climb over 155 battles is within luck's reach at 48 h (p about 0.01) ...
        blood = next(r for r in rows if r["id"] == 130)
        self.assertGreater(blood["p"], alpha)
        # ... and passes over the last 24 h alone, where it rose +239 over 103.
        day = {r["id"] for r in risers.pick(BOARD, risers.WINDOW)[0] if r["picked"]}
        self.assertIn(130, day)
        self.assertEqual([r["p"] for r in rows], sorted(r["p"] for r in rows))

    def test_a_steady_team_and_a_falling_team_are_not_picked(self):
        # The null the pick tests: the same rating changes with random signs. A team whose changes alternate climbs
        # no further than chance allows; one that only fell has no climb. The read's own last point, the current
        # rating again, is no battle.
        start = 1_790_000_000
        steps = [(start + 600 * k, 1900 + (12 if k % 2 else 0)) for k in range(60)]
        falling = [(start + 600 * k, 1900 - 5 * k) for k in range(60)]
        board = {"ladder": [{"rank": 1, "id": 1, "name": "flat", "elo": 1912}, {"rank": 2, "id": 2, "name": "down",
                                                                               "elo": 1605}],
                 "histories": {str(i): [{"date": risers.iso(t), "elo": e} for t, e in path]
                               for i, path in ((1, steps), (2, falling))}}
        rows, _, _ = risers.pick(board, risers.WINDOW)
        self.assertEqual([r["picked"] for r in rows], [False, False])
        self.assertEqual(next(r for r in rows if r["id"] == 2)["p"], 1.0)
        history = [{"date": "2026-09-28T01:00:00Z", "elo": 1500}, {"date": "2026-09-28T02:00:00Z", "elo": 1520},
                   {"date": "2026-09-28T10:26:38Z", "elo": 1520}]
        self.assertEqual(len(risers.points(history)), 2)


class Clock(unittest.TestCase):
    def test_the_first_check_covers_48_hours_and_each_later_one_24_checked_hourly(self):
        now = 1_790_600_000
        self.assertTrue(risers.due({}, now))
        self.assertEqual(risers.window_of({}), 48 * 3600)
        ran = {"checked_at": now}
        self.assertEqual(risers.window_of(ran), 24 * 3600)
        self.assertFalse(risers.due(ran, now + risers.CHECK_EVERY - 60))
        self.assertTrue(risers.due(ran, now + risers.CHECK_EVERY))
        self.assertFalse(risers.due({**ran, "pending": "/w"}, now + 2 * risers.EVERY))
        self.assertFalse(risers.due({**ran, "failed_at": now + risers.EVERY}, now + risers.EVERY + 60))
        # A check that died after its ratings read (no checked_at, no failed_at) waits RETRY_S, not a minute.
        died = {"checked_at": now - 2 * risers.CHECK_EVERY, "attempt_at": now}
        self.assertFalse(risers.due(died, now + 60))
        self.assertTrue(risers.due(died, now + risers.RETRY_S))

    def test_a_team_is_studied_at_most_once_a_day_and_a_new_rank_1_as_soon_as_it_takes_the_top(self):
        now = 1_790_600_000
        rows = [{"id": 70, "picked": True}, {"id": 306, "picked": False}, {"id": 852, "picked": True}]
        seen = {"leader": 306, "seen": {"852": now - risers.EVERY + 60}}  # 852 studied 23:59 ago
        looked = lambda record, top: [(r["id"], why) for r, why in risers.candidates(record, rows, top, now)]  # noqa: E731
        self.assertEqual(looked(seen, 306), [(70, "rise")])
        # Rank 1 changed hands from 306 to 470: studied first though its own rise didn't pass, once a day.
        rows.append({"id": 470, "picked": False})
        self.assertEqual(looked(seen, 470), [(470, "new rank 1"), (70, "rise")])
        # Its rise passing too, or judged K alone earlier today, still makes it the new rank 1, which the wins test
        # never skips.
        rows[-1]["picked"] = True
        self.assertEqual(looked(seen, 470), [(470, "new rank 1"), (70, "rise")])
        self.assertEqual(looked({**seen, "k_only": {"470": now - 60}}, 470), [(470, "new rank 1"), (70, "rise")])
        self.assertEqual(looked({**seen, "leader": 470, "k_only": {"470": now - 60}}, 470), [(70, "rise")])
        again = {**seen, "seen": {**seen["seen"], "470": now - 60}}
        self.assertEqual(looked(again, 470), [(70, "rise")])
        self.assertEqual(looked({}, 470), [(70, "rise"), (852, "rise"), (470, "rise")])  # no leader known

    def test_teams_studied_by_hand_are_skipped_for_a_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now = Path(directory), 1_790_600_000
            risers.save(root, {"seen": {"852": now - 60}})
            risers.mark_studied(root, ["306", "70"], now)
            record = risers.read(root)
            self.assertEqual(set(record["seen"]), {"852", "306", "70"})
            self.assertFalse(risers.fresh(record, 306, now + risers.EVERY - 60))
            self.assertTrue(risers.fresh(record, 306, now + risers.EVERY))

    def test_a_day_with_nobody_to_study_is_said_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now = Path(directory), 1_790_600_000
            flat = {"ladder": [{"rank": 1, "id": 1, "name": "x", "elo": 1500}],
                    "histories": {"1": [{"date": "2026-09-28T01:00:00Z", "elo": 1500}]}}
            for hour in range(3):
                self.assertIsNone(risers.prepare(root, now + hour * risers.CHECK_EVERY, board=lambda: flat,
                                                 tool=Site()))
            self.assertEqual(risers.window_of(risers.read(root)), risers.WINDOW)
            self.assertEqual((root / "host-alerts.log").read_text().count("RISERS none"), 1)
            self.assertFalse((root / "risers").exists())
            risers.prepare(root, now + risers.EVERY, board=lambda: flat, tool=Site())
            self.assertEqual((root / "host-alerts.log").read_text().count("RISERS none"), 2)

    def test_a_failed_rating_read_retries_after_an_hour_and_alerts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def broken() -> dict:
                raise OSError("site down")

            self.assertIsNone(risers.prepare(root, 1_790_600_000, board=broken))
            self.assertFalse(risers.due(risers.read(root), 1_790_600_000 + 60))
            self.assertIsNone(risers.prepare(root, 1_790_600_000 + risers.RETRY_S, board=broken))
            self.assertEqual((root / "host-alerts.log").read_text().count("RISERS failed"), 1)
            self.assertEqual(risers.window_of(risers.read(root)), risers.FIRST_WINDOW)  # the day's run is still owed


class Wins(unittest.TestCase):
    def test_a_rise_is_judged_by_games_won_against_the_ratings_prediction(self):
        after = [b for b in BATTLES if b["ranked"] and risers.settled(b)]
        mine = lambda b: "win" if b["a"]["id"] == 852 else "loss"  # noqa: E731  (results are side A's)
        shares = [(b["resultsA"].count(mine(b)) + b["resultsA"].count("draw") / 2) / len(b["resultsA"]) for b in after]
        level = risers.beats_prediction(after, 852, lambda team, at: 1800)  # equal ratings: 0.5 expected
        self.assertEqual(level["n"], len(after))
        self.assertAlmostEqual(level["mean"], sum(shares) / len(shares) - 0.5)
        # The same games by a team rated 400 above its opponents (a new bot's K lifts it far on small wins) beat far
        # less than they did at level ratings.
        favoured = risers.beats_prediction(after, 852, lambda team, at: 2200 if team == 852 else 1800)
        self.assertAlmostEqual(favoured["mean"], level["mean"] + 0.5 - risers.expected(2200, 1800))
        self.assertGreater(favoured["p"], level["p"])

    def test_a_rise_on_K_alone_is_listed_not_studied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            level = {"n": 6, "mean": 0.01, "z": 0.1, "p": 0.46}
            with patch.object(risers, "pick", return_value=([row_852()], risers.moment("2026-09-28T10:30:00Z"),
                                                            0.003)), \
                    patch.object(risers, "beats_prediction", return_value=level):
                self.assertIsNone(risers.prepare(root, 1_790_600_000, board=lambda: {}, tool=Site()))
            self.assertIn("a new bot's K more than its play", (root / "host-alerts.log").read_text())
            record = risers.read(root)
            self.assertFalse(risers.fresh(record, 852, 1_790_600_000 + 60, "k_only"))  # not rechecked as a rise today
            self.assertTrue(risers.fresh(record, 852, 1_790_600_000 + 60))  # but not studied: rank 1 would study it
            self.assertFalse((root / "risers").exists())


class Games(unittest.TestCase):
    def test_only_ranked_battles_split_at_the_climbs_low(self):
        row = row_852()
        before, after, complete = risers.team_battles(row, Site())
        self.assertTrue(complete)
        self.assertTrue(before and after)
        self.assertTrue(all(b["ranked"] is True for b in before + after))
        self.assertTrue(all(risers.moment(b["at"]) < row["low_at"] for b in before))
        self.assertTrue(all(risers.moment(b["at"]) >= row["low_at"] for b in after))
        span = [b for b in BATTLES if row["low_at"] - risers.WINDOW <= risers.moment(b["at"]) <= row["peak_at"]]
        self.assertGreater(sum(not b["ranked"] for b in span), 0)  # the sample has unranked ones to leave out

    def test_a_busy_after_side_leaves_the_before_side_its_own_pages(self):
        # 2026-09-28: one 30-page read back from Cutlery's peak found only after-side battles, most of its list being
        # unranked challenges. Here the after side fills more pages than a side's share, and the before side is still
        # read from the low.
        row = row_852(low="2026-09-28T00:30:00Z")
        with patch.object(risers, "MAX_PAGES", 4):  # one read of 4 pages back from the peak: after-side rows only
            before, after, complete = risers.team_battles(row, Site())
        self.assertTrue(before)
        self.assertTrue(after)
        self.assertFalse(complete)  # the after side's read stopped at its pages

    def test_downloads_are_bounded_to_the_newest_battles_each_side(self):
        with tempfile.TemporaryDirectory() as directory:
            site, row = Site(), row_852()
            before, after, _ = risers.team_battles(row, site)
            games = risers.fetch_games(row, before, after, Path(directory), site)
            (fetch,) = [c for c in site.calls if c[0] == "fetch"]
            battles = fetch[fetch.index("--battle") + 1:fetch.index("--max-games")]
            self.assertLessEqual(len(battles), 2 * risers.BATTLES_PER_SIDE)
            self.assertEqual(int(fetch[fetch.index("--max-games") + 1]), len(battles) * risers.GAMES_PER_BATTLE)
            self.assertEqual({g["period"] for g in games}, {"before", "after"})


class Run(unittest.TestCase):
    def prepared(self, root: Path) -> Path:
        (root / "ideas.jsonl").write_text("")
        (root / "study.md").write_text("# Top-team study\n")
        beats = {"n": 6, "mean": 0.2, "z": 3.1, "p": 0.001}
        with patch.object(risers, "pick", return_value=([row_852()], risers.moment("2026-09-28T10:30:00Z"), 0.003)), \
                patch.object(risers, "beats_prediction", return_value=beats):
            work = risers.prepare(root, 1_790_600_000, board=lambda: {}, tool=Site())
        self.assertIsNotNone(work)
        return work

    def test_the_workers_prompt_is_out_of_its_reach_and_it_reads_the_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.prepared(root)
            prompt = root / "worker-io/risers" / work.name / "PROMPT.md"
            self.assertIn("One finding, one general rule", prompt.read_text())
            self.assertFalse((work / "PROMPT.md").exists())
            self.assertIn("riser-replays", {p.name for p in risers.meta.worker_reads(root, "risers")})

    def test_apply_queues_valid_ideas_lists_them_and_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.prepared(root)
            self.assertIn("RISERS incomplete", risers.apply(root, work))  # no findings yet
            (work / "findings.md").write_text("## R1. C--: splits earlier\n")
            (work / "ideas.jsonl").write_text(json.dumps(GOOD) + "\n" + json.dumps({**GOOD, "hypothesis": "no options"})
                                              + "\n")
            line = risers.apply(root, work)
            self.assertTrue(line.startswith("RISERS applied: 1 ideas ['I001']"), line)
            (idea,) = [json.loads(x) for x in (root / "ideas.jsonl").read_text().splitlines()]
            self.assertEqual(idea["source"], f"risers:{work.name}")
            self.assertTrue(any((root / "applied").glob("*-risers-*.md")))  # for the orchestrator's decisions
            refused = root / "worker-io/risers" / work.name / "refused-ideas.jsonl"
            self.assertIn("Options", refused.read_text())
            record = risers.read(root)
            self.assertNotIn("pending", record)
            self.assertEqual(record["last"], str(work))

    def test_a_linked_ideas_file_is_refused_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.prepared(root)
            secret = root / "secret.txt"
            secret.write_text(json.dumps(GOOD) + "\n")
            (work / "findings.md").write_text("## R1. x\n")
            os.symlink(secret, work / "ideas.jsonl")
            self.assertTrue(risers.applied_line(root, work).startswith("RISERS incomplete"))
            self.assertEqual((root / "ideas.jsonl").read_text(), "")
            self.assertTrue(risers.read(root).get("pending"))

    def test_the_tick_starts_the_check_and_the_worker_in_the_background_and_applies_once_it_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root, now = Path(directory), 1_790_600_000
            with patch.object(risers.meta, "fresh_line", lambda s, a, o, fallback, repo=None: fallback()), \
                    patch.object(risers.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
                self.assertIn("check started", risers.tick(root, now=now))
                self.assertIn("prepare", popen.call_args.args[0])
                self.assertIsNone(risers.tick(root, now=now + 60))  # one check an hour, never waited for
                with patch.object(risers.subprocess, "Popen", only_git):  # champion_file's git show
                    self.prepared(root)  # what the check's process does
                with patch.object(risers, "alive", return_value=False):
                    self.assertIn("launched", risers.tick(root, now=now + 120))
                with patch.object(risers, "alive", return_value=True):
                    self.assertIsNone(risers.tick(root, now=now + 180))  # never waits
                work = Path(risers.read(root)["pending"])
                (work / "findings.md").write_text("## R1. x\n")
                with patch.object(risers, "alive", return_value=False):
                    self.assertIn("RISERS applied", risers.tick(root, now=now + 240))
                    self.assertIsNone(risers.tick(root, now=now + 300))  # the next check waits an hour, by our clock
                    self.assertEqual(popen.call_count, 2)
                    self.assertIn("check started", risers.tick(root, now=now + risers.CHECK_EVERY))
            self.assertIn("RISERS applied", (root / "host-alerts.log").read_text())
            self.assertIsNone(risers.tick(root, models=False, now=now + 2 * risers.CHECK_EVERY))  # off under native


class Health(unittest.TestCase):
    def test_first_firing_overdue_pending_and_unpruned_replays_alert(self):
        now = 1_790_600_000
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(health.riser_study(root, now), [])  # before the step first runs
            risers.save(root, {"first_seen": now - health.RISERS_FIRST - 60})
            self.assertEqual([a.key for a in health.riser_study(root, now)], ["risers:first"])
            risers.save(root, {"first_seen": now - 3 * risers.EVERY, "checked_at": now - 60})
            self.assertEqual(health.riser_study(root, now), [])
            late = now + risers.CHECK_EVERY + health.RISERS_OVERDUE + 60
            (overdue,) = health.riser_study(root, late)
            self.assertEqual(overdue.key, "risers:overdue")
            self.assertIn("due every 8 h", overdue.text)  # risers.CHECK_EVERY since 28 Sep, not hourly
            risers.save(root, {"checked_at": now, "pending": str(root / "risers/9"), "prepared_at": now})
            for name in ("1", "2", "3"):
                (root / "riser-replays" / name).mkdir(parents=True)
            keys = [a.key for a in health.riser_study(root, now + health.RISERS_PENDING + 60)]
            self.assertEqual(keys, ["risers-pending:9", "risers:riser-replays"])
            risers.prune(root)  # each folder back to the runs it keeps
            self.assertEqual(sorted(p.name for p in (root / "riser-replays").iterdir()), ["2", "3"])


if __name__ == "__main__":
    unittest.main()
