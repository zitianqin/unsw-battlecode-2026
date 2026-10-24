"""The live-state health check on samples captured from the real files (loop/testdata/health, trimmed from the live
loop home on 2026-09-26 01:06, paths rewritten to {home}): one test per invariant that fires, healthy data that
stays quiet, and the rate limit."""

import testhome  # before anything that reads live paths
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import dataclasses
import time
import unittest
from collections import namedtuple
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import audit
import decisions
import health
import maintenance
import mapideas
import meta
import online
from home import browse_server, evaluating_marker
import throughput
import prune
import replay_library

DATA = Path(__file__).resolve().parent / "testdata/health"
DECISIONS = DATA.parent / "decisions"
# The captured sample's clock: its remote-status.json was written 30 s before this.
NOW = json.loads((DATA / "remote-status.json").read_text())["updated"] + 30
BUGGY_AUDIT = "1790344222548092000"  # the audit whose context.md the capture holds, written before 9029a7f's fix
Usage = namedtuple("Usage", "total used free")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# A loop home under `root` built from the captured files, beside a git repo whose champion branch and referee copy
# are current (evidence.md and publishing.json name its champion commit). Returns (state, repo).
def build(root: Path) -> tuple[Path, Path]:
    repo, state = root / "repo", root / "state"
    for folder in (repo / "referee", repo / "bot", state):
        folder.mkdir(parents=True)
    (repo / "referee/run.py").write_text("# referee\n")
    (repo / "bot/main.cpp").write_text("// bot\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    git(repo, "branch", "loop/champion")
    git(repo, "worktree", "add", "-q", "--detach", str(state / "champion"), "loop/champion")
    git(repo, "worktree", "add", "-q", "--detach", str(state / "referee"), "HEAD")
    champion = git(repo, "rev-parse", "loop/champion")
    places = {"context.md": f"audits/{BUGGY_AUDIT}/context.md", "study-index.jsonl": "study-replays/index.jsonl"}
    for sample in DATA.iterdir():
        target = state / places.get(sample.name, sample.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sample.read_text().replace("{home}", str(state)).replace("{repo}", str(repo))
                          .replace("{champion}", champion))
        os.utime(target, (NOW - 3600, NOW - 3600))
    games = json.loads((state / "online.json").read_text())["games"]
    for replay in [Path(g["replay"]) for g in games] + [state / f"study-replays/{row['match']}.replay" for row in
                   map(json.loads, (state / "study-replays/index.jsonl").read_text().splitlines())]:
        replay.parent.mkdir(parents=True, exist_ok=True)
        replay.touch()
        os.utime(replay, (NOW - 600, NOW - 600))
    return state, repo


def edit(path: Path, change) -> None:
    value = json.loads(path.read_text())
    change(value)
    path.write_text(json.dumps(value))


class Health(unittest.TestCase):
    def setUp(self) -> None:
        # No git trace2 events: a daemon listening for them (git-ai here) writes into a fixture repo's .git after
        # its commits, racing the folder's removal.
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state, self.repo = build(Path(directory.name))
        self.write_history(json.loads((self.state / "study.json").read_text())["fetched_at"])
        disk = patch.object(health.shutil, "disk_usage", return_value=Usage(926e9, 507e9, 390e9))
        disk.start()
        self.addCleanup(disk.stop)

    # The captured state with its two real faults repaired: zz played no game in the captured test, and the captured
    # audit listed every online game as lost and no leader games. The audit is rewritten by today's audit code.
    def heal(self) -> None:
        def zz_played(status: dict) -> None:
            status["hosts"]["zz"].update(played=40, last_game="01:06:10", rests=0, last_rest=None, resting=False)
        edit(self.state / "remote-status.json", zz_played)
        shutil.rmtree(self.state / "audits" / BUGGY_AUDIT)
        fixed = self.state / "audits/1790348000000000000/context.md"
        fixed.parent.mkdir(parents=True)
        fixed.write_text(audit.context(self.state))
        # The utilisation sampler's row for the captured status files, and a current game profile (the fields the
        # check reads, as tools/game_profile.py writes them).
        throughput.sample(self.state, now=NOW - 30, cores=10, load=0.0, memory_gb=16.0)
        (self.state / "game-profile.json").write_text(json.dumps({"at": NOW - 3600, "unswbc": "1.1.0"}))
        replay_library.refresh(self.state)  # the upkeep's index of the saved games, matched to its sources

    # study-history.jsonl as the study appends it (the capture predates it), last written at `at`.
    def write_history(self, at: float) -> None:
        history = self.state / "study-history.jsonl"
        history.write_text('{"at": "2026-09-26T01:05:53Z", "submission": 6542, "us": {}, "leaders": []}\n')
        os.utime(history, (at, at))

    def keys(self, now: float = NOW, record: dict | None = None) -> list[str]:
        return [a.key for a in health.alerts(self.state, self.repo, now, {} if record is None else record)]

    def test_healthy_state_is_quiet(self) -> None:
        self.heal()
        self.assertEqual(health.alerts(self.state, self.repo, NOW, {}), [])

    def test_the_captured_audit_mislabels_online_games_and_lists_no_leaders(self) -> None:
        # The bug in MISSES 13, live on 2026-09-26: 10 of the 12 games listed lost were wins.
        (alert,) = health.audit_labels(self.state, NOW)
        self.assertIn(f"audit {BUGGY_AUDIT} labels 10 of 12 online games wrongly", alert.text)
        self.assertIn("listed lost, its summary says won", alert.text)
        self.assertEqual([a.key for a in health.leaders(self.state, NOW)], [f"leaders:{BUGGY_AUDIT}"])

    def test_leader_replays_older_than_six_hours_alert(self) -> None:
        self.heal()
        self.assertEqual(health.leaders(self.state, NOW + health.LEADER_REPLAY_AGE - 3600), [])
        self.assertEqual([a.key for a in health.leaders(self.state, NOW + health.LEADER_REPLAY_AGE)],
                         ["leaders:replays"])

    def test_a_leader_fetch_without_a_gap_history_row_alerts(self) -> None:
        self.heal()
        fetched = json.loads((self.state / "study.json").read_text())["fetched_at"]
        self.write_history(fetched - health.HISTORY_LAG + 60)
        self.assertEqual(health.leaders(self.state, NOW), [])
        self.write_history(fetched - health.HISTORY_LAG - 60)
        self.assertEqual([a.key for a in health.leaders(self.state, NOW)], ["leaders:history"])
        (self.state / "study-history.jsonl").unlink()
        (alert,) = health.leaders(self.state, NOW)
        self.assertIn("its gap history is missing", alert.text)
        # Before: a new version with only dev games (which the history leaves out) alerted after HISTORY_LAG.
        edit(self.state / "online.json", lambda live: live.update(games=[{**g, "dev": True} for g in live["games"]]))
        self.assertEqual(health.leaders(self.state, NOW), [])

    def test_a_gap_row_that_studied_no_dev_benchmark_game_alerts_while_we_have_played_it(self) -> None:
        self.heal()
        fetched = json.loads((self.state / "study.json").read_text())["fetched_at"]
        # online.json's benchmark as online.benchmark_report writes it (live, 2026-09-26), trimmed to two records.
        edit(self.state / "online.json", lambda live: live.update(benchmark={
            "team": {"id": 545, "name": "dev test 1 :P", "elo": 1973,
                     "why": "highest-rated dev team, none named frontier"},
            "records": [{"submission_id": 6518, "dev_submission_id": 4525, "games": 11, "wins": 3, "draws": 0,
                         "losses": 8, "score": 0.273},
                        {"submission_id": 6609, "dev_submission_id": 4525, "games": 11, "wins": 5, "draws": 0,
                         "losses": 6, "score": 0.455}]}))
        self.assertEqual(health.leaders(self.state, NOW), [])  # a row from before the dev section has no "dev"
        history = self.state / "study-history.jsonl"
        row = {"at": "2026-09-26T01:05:53Z", "submission": 6542, "us": {}, "leaders": []}
        for dev, alerting in (({"id": 545, "submission": 4525, "games": 22, "stats": {}}, False),
                              ({"games": 0, "error": "KeyError: 'side'"}, True), (None, True)):
            history.write_text(json.dumps({**row, "dev": dev}) + "\n")
            os.utime(history, (fetched, fetched))
            alerts = health.leaders(self.state, NOW)
            self.assertEqual([a.key for a in alerts], ["leaders:dev"] if alerting else [], dev)
        self.assertIn("studied none of our 22 games against the dev benchmark", alerts[0].text)

    # REFLECT 2026-09-28 14:51 F4: dev test 1's 4525 left 718 saved replays that no study read.
    def test_an_opponent_version_with_many_saved_replays_and_no_study_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/7676"
            folder.mkdir(parents=True)
            games = [{"id": n, "opponent": 545, "opponent_submission_id": 4525, "replay": str(folder / f"{n}.replay")}
                     for n in range(health.UNSTUDIED_REPLAYS - 1)]
            for game in games:
                Path(game["replay"]).write_bytes(b"x")
            games += [{"id": 900, "opponent": 545, "opponent_submission_id": 4525}]  # a result with no replay
            # Rows whose file prune deleted count for nothing: 609 and 758 alerted on 2026-09-28 with 139 and 146
            # rows and no file.
            games += [{"id": 1000 + n, "opponent": 545, "opponent_submission_id": 4525,
                       "replay": str(folder / f"{1000 + n}.replay")} for n in range(5)]
            (state / "online-replays/7676/index.json").write_text(json.dumps({"games": games}))
            self.assertEqual(health.unstudied_versions(state), [])
            # A leader's game against it counts too, and a row naming one of our versions never does.
            (state / "study-replays").mkdir()
            (state / "study-replays/index.jsonl").write_text(
                json.dumps({"teamAId": 545, "submissionAId": 4525, "teamBId": 157, "submissionBId": 7676}) + "\n")
            (alert,) = health.unstudied_versions(state)
            self.assertEqual(alert.key, "unstudied:545:4525")
            self.assertIn(f"has {health.UNSTUDIED_REPLAYS} saved replays", alert.text)
            (state / "ideas.jsonl").write_text(json.dumps({"id": "I1", "title": "t", "hypothesis": "dev 4525's "
                                                           "splits"}) + "\n")
            self.assertEqual(health.unstudied_versions(state), [])  # an idea names it: studied by hand
            (state / "ideas.jsonl").unlink()
            (state / "study.json").write_text(json.dumps({"studied": {"545:4525": 30}}))
            self.assertEqual(health.unstudied_versions(state), [])

    # prune.restore_sample's detector and first real firing: an old version's dev loss still marked "kept" by the old
    # mark_dev_losses in prune alerts until a prune tick removes it, then the check says so once.
    def test_old_kept_marks_alert_while_left_and_once_when_cleared(self) -> None:
        _, marked, _ = json.loads((Path(prune.__file__).parent / "testdata/prune-restore-rows-sample.json").read_text())
        with tempfile.TemporaryDirectory() as folder:
            state, seen = Path(folder), {}
            (state / "online.json").write_text(json.dumps({"submission_id": 9918, "dev": {"teams": [{"id": 545}]}}))
            for version in (9695, 9762, 9918):
                (state / "online-replays" / str(version)).mkdir(parents=True)
                ladder = [{"id": version, "opponent": 262, "ranked": False}]
                (state / "online-replays" / str(version) / "index.json").write_text(json.dumps({"games": ladder}))
            (state / "online-replays/9695/index.json").write_text(json.dumps({"games": [{**marked, "kept": None}]}))
            self.assertEqual(health.old_kept_marks(state, 500.0, {}), [])  # no new tick yet, nothing left: quiet
            (state / "online-replays/9695/index.json").write_text(json.dumps({"games": [marked]}))
            (alert,) = health.old_kept_marks(state, 1000.0, seen)
            self.assertEqual(alert.key, "old-kept-marks:left")
            self.assertIn("1 dev rows of 1 old online-replays versions (9695)", alert.text)
            self.assertIn("restored 1", prune.tick(state))
            (alert,) = health.old_kept_marks(state, 2000.0, seen)
            self.assertEqual(alert.key, "old-kept-marks:cleared")
            self.assertEqual(health.old_kept_marks(state, 3000.0, seen), [])

    # online.refresh_dooms's rows for online.md's death table (MISSES 64): a replay the doom tool gave up on, or saved
    # games none of which has a row two hours after the fetch, alert; one row done is enough to show it runs.
    def test_the_death_table_alerts_when_its_rows_fail_or_never_come(self) -> None:
        self.heal()
        sid = json.loads((self.state / "online.json").read_text())["submission_id"]
        index = self.state / "online-replays" / str(sid) / "index.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        requested = datetime.fromtimestamp(NOW - 60).astimezone().isoformat()
        game = {"id": 403920, "side": "A", "replay": str(index.parent / "403920.replay"), "requested_at": requested}
        index.write_text(json.dumps({"games": [game, {**game, "id": 2}]}))
        self.assertEqual(health.death_tables(self.state, NOW), [])  # just played
        self.assertEqual(health.death_tables(self.state, NOW + health.DOOM_WAIT)[0].key, f"death table:{sid}")
        index.write_text(json.dumps({"games": [{**game, "doom": {"ours": {}, "theirs": {}}}, {**game, "id": 2}]}))
        self.assertEqual(health.death_tables(self.state, NOW + health.DOOM_WAIT), [])
        # Regression (review of 2026-09-28): a failure alerted every interval forever. One still being retried is no
        # alert; one given up alerts once, and a second given up later alerts too.
        failing = {**game, "doom": {}, "doom_failed": {"version": 2, "tries": 1, "error": "CalledProcessError: 1"}}
        index.write_text(json.dumps({"games": [failing]}))
        seen: list = []
        self.assertEqual(health.death_tables(self.state, NOW, seen), [])
        gave_up = {**failing, "doom_failed": {**failing["doom_failed"], "tries": health.online.DOOM_TRIES}}
        index.write_text(json.dumps({"games": [gave_up]}))
        (alert,) = health.death_tables(self.state, NOW, seen)
        self.assertIn("gave up on 1 more saved games", alert.text)
        self.assertEqual(health.death_tables(self.state, NOW + health.REPEAT, seen), [])
        index.write_text(json.dumps({"games": [gave_up, {**gave_up, "id": 2}]}))
        self.assertEqual(len(health.death_tables(self.state, NOW, seen)), 1)
        self.assertEqual(seen, [403920, 2])

    # MISSES 72: compare pairs on each saved game's start, and new layouts alert from online.json's record; saved games
    # none of which has a start two hours on, or games with layouts and no record, mean that isn't running.
    def test_starts_and_layouts_alert_when_they_never_come(self) -> None:
        self.heal()
        report = json.loads((self.state / "online.json").read_text())
        index = self.state / "online-replays" / str(report["submission_id"]) / "index.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        requested = datetime.fromtimestamp(NOW - 60).astimezone().isoformat()
        game = {"id": 447294, "side": "A", "replay": str(index.parent / "447294.replay"), "requested_at": requested}
        index.write_text(json.dumps({"games": [game]}))
        self.assertEqual(health.layouts(self.state, NOW), [])  # just played
        self.assertEqual([a.key for a in health.layouts(self.state, NOW + health.DOOM_WAIT)],
                         [f"starts:{report['submission_id']}"])
        index.write_text(json.dumps({"games": [{**game, "start_failed": True}]}))  # tried: its replay is unreadable
        self.assertEqual(health.layouts(self.state, NOW + health.DOOM_WAIT), [])
        played = {**game, "start": "a" * 16, "layout": "b" * 16}
        (self.state / "online.json").write_text(json.dumps({**report, "games": [played]}))
        self.assertEqual([a.key for a in health.layouts(self.state, NOW)], [f"layouts:{report['submission_id']}"])
        (self.state / "online.json").write_text(json.dumps({**report, "games": [played], "layouts": {"4": ["b" * 16]}}))
        self.assertEqual(health.layouts(self.state, NOW), [])

    # REFLECT F14 (M73): the live online.json of 2026-09-29 13:07 AEST, 10964 against 10774, paired 0 with 249 and 10
    # games of unnamed opponent versions, and nothing alerted; one such cycle is a new version's first games.
    # The review of M73 found candidates counting games against unread's matchups; both count matchups now. And the
    # rating reads M73 pairs by alert once their last good read is 3 x online.RATINGS_EVERY behind the cycle.
    def test_an_online_compare_pairing_nothing_for_two_cycles_alerts(self) -> None:
        import online
        compare = {"previous": 10774, "pairs": 0, "inferred": 0, "candidates": 10, "unread": 0,
                   "unknown_opponent": {"before": 249, "after": 10}}
        report = {"submission_id": 10964, "compare": compare, "compare_zero_cycles": 1, "fetched_at": NOW}
        (self.state / "online.json").write_text(json.dumps(report))
        (self.state / "rating-splits.json").write_text(json.dumps({"read_at": NOW - 60, "teams": {}}))
        self.assertEqual(health.online_compare(self.state, NOW), [])
        (self.state / "online.json").write_text(json.dumps({**report, "compare_zero_cycles": 2}))
        (alert,) = health.online_compare(self.state, NOW)
        self.assertIn("paired none of its 10 matchups that can pair", alert.text)
        (self.state / "rating-splits.json").write_text(json.dumps({
            "read_at": NOW - 3 * online.RATINGS_EVERY - 60, "attempt_at": NOW - 60, "error": "HTTPError: HTTP Error 429"}))
        (self.state / "online.json").write_text(json.dumps(report))
        (alert,) = health.online_compare(self.state, NOW)
        self.assertEqual(alert.key, "rating reads")
        self.assertIn("the last try failing: HTTPError", alert.text)

    # M85's first firing: the live version's sample games get ram rows (online.refresh_rams). The real row of game
    # 447294 (9553 on Default, 7 rams ours and 5 theirs) copied 40 times, none read, then all read failing, then read.
    def test_online_ram_rows_alert_when_the_sample_gets_none(self) -> None:
        import online
        replay = Path(__file__).resolve().parent.parent / "tools/testdata/default-447294.replay"
        row = {"id": 447294, "submission_id": 9553, "opponent": 34, "side": "A", "map_id": 4, "ranked": False,
               "replay": str(replay), "summary": {"result": {"winner": "A"}}}
        folder = self.state / "online-replays/9553"
        folder.mkdir(parents=True, exist_ok=True)
        (self.state / "online.json").write_text(json.dumps({"submission_id": 9553}))
        save = lambda rams: (folder / "index.json").write_text(json.dumps({"games": [
            {**row, "id": k, **({"rams": rams} if rams else {})} for k in range(health.RAM_GAMES)]}))
        save(None)
        (alert,) = health.online_rams(self.state, NOW)
        self.assertIn("none of its 40 sample games has ram rows yet", alert.text)
        save({"version": online.RAM_VERSION, "error": "KeyError: 'frames'"})
        self.assertIn("40 of 40 replays read failed", health.online_rams(self.state, NOW)[0].text)
        save({"version": online.RAM_VERSION, "ours": [[0, 0, 3, 2]], "theirs": []})
        self.assertEqual(health.online_rams(self.state, NOW), [])

    # The sweep weighting's first firing (online.sweep_shares): a day's sweep games at a mean share no higher than the
    # rotation it replaced gave (0.68 on 1,961 games) alert; dev rows and rows before the weighting (no share) don't count.
    def test_sweep_games_no_better_spread_than_the_old_rotation_alert(self) -> None:
        rows = [{"at": NOW - 3600, "games": 10, "opponent": 106, "share": 0.3, "status": "requested"}] * 10
        rows += [{"at": NOW - 3600, "games": 10, "opponent": 545, "dev": True, "share": 1.0}] * 30
        (self.state / "match-requests.json").write_text(json.dumps(rows))
        (alert,) = health.sweep_shares(self.state, NOW)
        self.assertIn("100 sweep games of the last day went to teams at a mean share of 0.30", alert.text)
        rows[:10] = [{**r, "share": 0.9} for r in rows[:10]]
        (self.state / "match-requests.json").write_text(json.dumps(rows))
        self.assertEqual(health.sweep_shares(self.state, NOW), [])

    # loop/online_fit.py's first firing and failures: a run's error alerts, and so do games waiting with no run for
    # three periods; a run in its period doesn't.
    def test_online_fit_rows_alert_on_a_failed_run_and_on_games_left_waiting(self) -> None:
        import online_fit
        (self.state / online_fit.RECORD).write_text(json.dumps({"started_at": NOW - 60, "finished_at": NOW - 30,
                                                                "error": "ValueError: no pearl range fits"}))
        (alert,) = health.online_fit(self.state, NOW)
        self.assertIn("no pearl range fits", alert.text)
        (self.state / online_fit.RECORD).write_text(json.dumps({"started_at": NOW - 60, "finished_at": NOW - 30}))
        with patch.object(online_fit, "pending", lambda state, now=None: [(10774, {"id": 530208})]):
            self.assertEqual(health.online_fit(self.state, NOW), [])
            (alert,) = health.online_fit(self.state, NOW + 3 * online_fit.EVERY)
        self.assertIn("1 online games wait", alert.text)

    # MISSES 77: the site dropped the fields the cycle filed games by (online.API_GONE), and for 20 minutes every
    # finished game was read and skipped without a word. The real new-shape sweep of WCTW, as online.cycle would record it.
    def test_finished_games_left_unrecorded_or_a_missing_api_field_alert(self) -> None:
        sample = json.loads((DATA.parent / "online-api-no-submissions-sample.json").read_text())
        detail = sample["detail_475476"]
        finished = datetime.fromisoformat(sample["battles"][0]["at"].replace("Z", "+00:00")).timestamp()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            nine = {**detail, "games": [{**detail["games"][0], "id": n} for n in range(475476, 475485)]}
            missed = online.unrecorded_games(sample["battles"], {475476: nine}, set())
            self.assertEqual((missed["games"], missed["oldest"]), (9, sample["battles"][0]["at"]))
            (state / "online.json").write_text(json.dumps({"unrecorded": missed}))
            self.assertEqual(health.online_recording(state, finished + health.UNRECORDED_S - 60), [])  # a cycle late
            self.assertEqual([a.key for a in health.online_recording(state, finished + health.UNRECORDED_S + 60)],
                             ["online unrecorded"])
            few = online.unrecorded_games(sample["battles"], {475476: detail}, set())  # 3: like one bad replay's
            (state / "online.json").write_text(json.dumps({"unrecorded": few}))
            self.assertEqual(health.online_recording(state, finished + 3 * health.UNRECORDED_S), [])
            gone = {**detail, "match": {k: v for k, v in detail["match"].items() if k != "winner"}}
            record, _ = online.api_news(None, online.missing_fields([gone]), finished)
            (state / "online.json").write_text(json.dumps({"api_missing": record, "unrecorded": None}))
            (alert,) = health.online_recording(state, finished + 60)
            self.assertEqual(alert.key, "api fields:match.winner")
            self.assertIn("lack match.winner", alert.text)

    # REFLECT 2026-09-28 20:52 F3: Trauma was 26-34 online before sig flagged it. The live and previous versions' real
    # non-dev games (testdata/map-records-sample.json): Trauma 2-0-5 against 30-0-9 elsewhere, raw p 0.028, is luck
    # across 10 maps (x10 = 0.28), so quiet; sig's 26-34 on Trauma alerts.
    def test_a_map_clearly_below_our_others_online_alerts(self) -> None:
        sample = json.loads((DATA.parent / "map-records-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online.json").write_text(json.dumps(sample["online"]))
            (state / "ladder-maps.json").write_text(json.dumps({"maps": sample["maps"]}))
            for version, index in sample["indexes"].items():
                (state / "online-replays" / version).mkdir(parents=True)
                (state / "online-replays" / version / "index.json").write_text(json.dumps(index))
            self.assertEqual(health.map_records(state), [])
            live = sample["indexes"]["10115"]
            trauma = {k: v for k, v in next(g for g in live["games"] if g["map_id"] == 15).items() if k != "summary"}
            other = {"A": "B", "B": "A"}[trauma["side"]]
            live["results"] += [{**trauma, "id": n, "winner": trauma["side"] if n < 26 else other} for n in range(60)]
            (state / "online-replays/10115/index.json").write_text(json.dumps(live))
            (alert,) = health.map_records(state)
        self.assertEqual(alert.key, "map record:15:10115")
        self.assertIn("the live and previous versions (10115, ", alert.text)

    # REFLECT 2026-09-29 17:32 F6: nothing said when the waiting ideas outgrew what the loop evaluates.
    def test_waiting_ideas_growing_a_day_while_results_an_hour_stay_flat_alert(self) -> None:
        day = health.BACKLOG_DAY
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)

            def backlog(waiting: int) -> None:
                rows = [{"id": f"I{n}", "status": "todo" if n < waiting else "tried"} for n in range(100)]
                (state / "ideas.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

            def results(earlier: int, recent: int) -> None:
                times = [NOW - 2 * day + (n + 0.5) * day / earlier for n in range(earlier)]
                times += [NOW - day + (n + 0.5) * day / recent for n in range(recent)]
                (state / "results.jsonl").write_text("".join(json.dumps({"id": "E1", "pool": {"x": [1] * 50},
                                                                         "finished": t}) + "\n" for t in times))

            backlog(60)
            results(24, 20)
            history = [[NOW - day - 60, 30]]
            (alert,) = health.backlog_growth(state, NOW, history)
            self.assertIn("60 ideas wait, +30 since", alert.text)
            self.assertIn("results recorded were 20 in those 24 h (0.8 an hour) against 24 in the day before", alert.text)
            self.assertEqual(len(history), 2)  # this check's sample kept for tomorrow's
            results(24, 40)  # evaluations picked up: catching up
            self.assertEqual(health.backlog_growth(state, NOW + 60, [[NOW - day - 60, 30]]), [])
            results(24, 20)
            backlog(45)  # grew by less than the day's results
            self.assertEqual(health.backlog_growth(state, NOW + 120, [[NOW - day - 60, 30]]), [])
            self.assertEqual(health.backlog_growth(state, NOW, [[NOW - 3600, 30]]), [])  # no day of history yet

    # REFLECT 2026-09-29 17:32 F20: a module passing only on merge.sh's lone rerun is a flaky test; two in a day alert.
    def test_two_merge_reruns_in_a_day_alert_naming_the_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            rows = [{"at": NOW - 2 * health.BACKLOG_DAY, "module": "test_old", "branch": "orch/a"},
                    {"at": NOW - 600, "module": "test_baselines", "branch": "orch/b"}]
            (state / "merge-reruns.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual(health.merge_reruns(state, NOW), [])  # one in the day
            with (state / "merge-reruns.jsonl").open("a") as handle:
                handle.write(json.dumps({"at": NOW - 60, "module": "test_baselines", "branch": "orch/c"}) + "\n")
            (alert,) = health.merge_reruns(state, NOW)
        self.assertIn("2 test modules passed only when tools/merge.sh reran them alone in the last day: "
                      "test_baselines x2 (orch/b, orch/c)", alert.text)

    # 29 Sep: I848 sat as a next followup with no retest waiting, so no claim could take it and nothing said so.
    def test_a_next_idea_no_claim_can_take_for_an_hour_alerts_with_why(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            rows = [{"id": "I848", "status": "followup", "next": True, "priority": 1, "title": "t",
                     "history": ["E0310: promising (SPRT inconclusive)"]},
                    {"id": "I400", "status": "tried", "next": True, "priority": 1, "title": "stale flag"}]
            held = [{"id": "I991", "status": "todo", "next": True, "priority": 1, "title": "u", "after": ["I992"]},
                    {"id": "I992", "status": "todo", "priority": 2, "title": "v"}]
            (state / "ideas.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [*rows, *held]))
            seen: dict = {}
            self.assertEqual(health.next_stuck(state, NOW, seen), [])  # found now; an hour's grace
            (alert,) = health.next_stuck(state, NOW + health.NEXT_STUCK_S + 60, seen)
            self.assertIn("I848 (a followup, never claimed itself, with no retest waiting, since", alert.text)
            self.assertIn("I991 (after I992, since", alert.text)  # driver.held_back's reason
            self.assertNotIn("I400", alert.text)
            retest = {"id": "I990", "status": "todo", "priority": 1, "title": "Retest E0310 alone on the current "
                      "champion: t", "after": []}
            (state / "ideas.jsonl").write_text("".join(json.dumps(row) + "\n" for row in [*rows, retest]))
            self.assertEqual(health.next_stuck(state, NOW + 2 * health.NEXT_STUCK_S, seen), [])  # its retest waits
            self.assertEqual(seen, {})

    # M94: Devil went 2-0-8 on 11181 alone against 60-0-29 on its other 9 maps (the live counts, 29 Sep), p 0.0052,
    # 0.052 over 10 maps, yet the pool with 11049 (where Devil did well) raised nothing. The live version is its own look.
    def test_a_map_the_live_version_alone_loses_alerts_with_its_record_and_p_alone_and_corrected(self) -> None:
        def games(version: int, devil: tuple[int, int], rest: tuple[int, int]) -> dict:
            rows, n = [], 0
            for map_id, (won, lost) in [(13, devil), *((m, rest) for m in range(1, 10))]:
                for k in range(won + lost):
                    n += 1
                    rows.append({"id": version * 1000 + n, "map_id": map_id, "side": "A", "ranked": False,
                                 "winner": "A" if k < won else "B", "requested_at": "2026-09-29T01:00:00Z"})
            return {"games": rows[:1], "results": rows[1:]}

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online.json").write_text(json.dumps({"submission_id": 11181}))
            (state / "ladder-maps.json").write_text(json.dumps({"maps": [{"id": 13, "name": "Devil"}]}))
            for version, index in ((11181, games(11181, (2, 8), (7, 3))), (11049, games(11049, (40, 0), (7, 3)))):
                (state / "online-replays" / str(version)).mkdir(parents=True)
                (state / "online-replays" / str(version) / "index.json").write_text(json.dumps(index))
            (alert,) = health.map_records(state)
        self.assertEqual(alert.key, "map record:13:11181")
        self.assertIn("Devil may be our worst online map: 2-0-8 (won-drawn-lost) against 63-0-27 on the other 9 maps, "
                      "the live version (11181)", alert.text)
        self.assertIn("its gap p = 0.0031, and one this large on some map p = 0.031 (0.0031 x 10 maps)", alert.text)

    def test_no_dev_battle_for_half_an_hour_while_a_submission_is_live_alerts(self) -> None:
        self.heal()
        sid = json.loads((self.state / "online.json").read_text())["submission_id"]
        # online.json's dev block as online.cycle writes it, for the captured live submission, and a dev test 1 row of
        # the live match-requests.json (loop/testdata, 2026-09-27) marked dev as online.send writes it now.
        dev = {"submission_id": sid, "since": NOW - 3 * 3600, "maps": [4, 7, 9],
               "teams": [{"id": 545, "name": "dev test 1 :P", "elo": 1939, "weight": 1.0}],
               "winner_check": {"checked": 12, "agree": 12}}
        edit(self.state / "online.json", lambda live: live.update(dev=dev))
        (row,) = [r for r in json.loads((DATA.parent / "match-requests-sample.json").read_text())
                  if r["submission_id"] == 7676 and r["opponent"] == 545]
        requests = self.state / "match-requests.json"
        requests.write_text(json.dumps([{**row, "submission_id": sid, "dev": True, "at": NOW - 600}]))
        self.assertEqual(health.dev_battles(self.state, NOW), [])
        pace = online.dev_pace(dev["maps"])  # the next was due a pace after it (online.dev_due)
        requests.write_text(json.dumps([{**row, "submission_id": sid, "dev": True,
                                         "at": NOW - health.DEV_STALL - pace - 60},
                                        {**row, "at": NOW - 60}]))  # a non-dev request doesn't count
        (self.state / "dev-poll.json").write_text(json.dumps({"at": NOW - 20, "ahead": 5, "queued": 3, "sent": []}))
        (alert,) = health.dev_battles(self.state, NOW)
        self.assertEqual(alert.key, f"dev battles:{sid}")
        self.assertIn("3 of ours queued with 5 ahead", alert.text)
        # Before: a poll that sent nothing (here, on a dev list the cycle last read 20 minutes ago) left no reason.
        stale = {"submission_id": sid, "dev": {**dev, "at": NOW - 1200}}
        skipped = online.poll_skip({}, stale, NOW)
        self.assertTrue(skipped.startswith("stale dev list: online.json's dev teams were read 20 min ago"))
        (self.state / "dev-poll.json").write_text(json.dumps({"at": NOW - 20, "skipped": skipped}))
        (alert,) = health.dev_battles(self.state, NOW)
        self.assertIn(f"sent nothing: {skipped}", alert.text)
        requests.unlink()  # none requested yet: timed from when the submission's dev battles began
        self.assertEqual(len(health.dev_battles(self.state, NOW)), 1)
        self.assertEqual(health.dev_battles(self.state, NOW - 3 * 3600 + 600), [])
        # The hour's dev allowance spent 31 minutes ago (online.remaining_dev_games): nothing can be requested yet.
        at = NOW - health.DEV_STALL - 60
        spent = {**row, "submission_id": sid, "dev": True, "at": at, "games": online.DEV_GAMES_AN_HOUR}
        requests.write_text(json.dumps([spent]))
        self.assertEqual(health.dev_battles(self.state, NOW), [])
        # Before: the allowance freeing an hour after that request found it long past DEV_STALL and alerted before
        # the next poll could send. Now the stall counts from when the next battle was due.
        requests.write_text(json.dumps([{**spent, "at": NOW - 3600 - 10}]))
        self.assertEqual(health.dev_battles(self.state, NOW), [])
        self.assertEqual(len(health.dev_battles(self.state, NOW - 10 + health.DEV_STALL + 1)), 1)
        # Paced: a battle is owed a pace after the last, so the stall counts from then.
        requests.write_text(json.dumps([{**row, "submission_id": sid, "dev": True, "at": at, "games": 3}]))
        self.assertEqual(health.dev_battles(self.state, NOW), [])
        self.assertEqual(len(health.dev_battles(self.state, NOW + pace)), 1)
        # A row without `games` counts none, rather than crashing the check.
        requests.write_text(json.dumps([{k: v for k, v in spent.items() if k != "games"}]))
        self.assertEqual(len(health.dev_battles(self.state, NOW + pace)), 1)
        (self.state / "STOP").touch()
        self.assertEqual(health.dev_battles(self.state, NOW), [])
        (self.state / "STOP").unlink()
        # The API's winner, which scores the games past the replay sample, disagreeing with a replay alerts too.
        edit(self.state / "online.json", lambda live: live["dev"].update(since=NOW, winner_check={"checked": 12,
                                                                                                 "agree": 11}))
        (alert,) = health.dev_battles(self.state, NOW)
        self.assertIn("disagrees with the replay's on 1 of 12", alert.text)
        edit(self.state / "online.json", lambda live: live["dev"].update(submission_id=sid - 1))  # an older version's
        self.assertEqual(health.dev_battles(self.state, NOW), [])

    def test_a_dev_team_answering_409_for_half_an_hour_alerts(self) -> None:
        # A battle stuck "running" past dev_poll's window drew a 409 every 30 s and nothing said so.
        self.heal()
        sid = json.loads((self.state / "online.json").read_text())["submission_id"]
        edit(self.state / "online.json", lambda live: live.update(dev={"submission_id": sid, "since": NOW, "teams": [],
                                                                       "maps": [4, 7, 9, 11, 13, 15, 17, 19, 20, 21]}))
        poll = self.state / "dev-poll.json"
        stuck = {"545": {"since": NOW - health.DEV_STUCK - 60, "last": NOW - 30}}
        poll.write_text(json.dumps({"at": NOW - 30, "stuck": stuck}))
        (alert,) = health.dev_battles(self.state, NOW)
        self.assertEqual(alert.key, "dev stuck:545")
        self.assertIn("dev team 545 has answered every request with 409", alert.text)
        # Re-review of 74ad386: a stuck team is tried once a round, a dev_pace (720 s on these 10 maps) apart, so a
        # latest 409 a pace and a minute old, past the old fixed 10 minutes, is still a stuck team's; one due a probe
        # while other probes go first is tried every other round, so two paces and a minute old too (re-review of
        # aae839d).
        pace = online.dev_pace([4, 7, 9, 11, 13, 15, 17, 19, 20, 21])
        for last in (NOW - pace - 60, NOW - 2 * pace - 60):
            poll.write_text(json.dumps({"at": NOW - 30, "stuck": {"545": {"since": NOW - 3 * 3600, "last": last}}}))
            self.assertEqual([a.key for a in health.dev_battles(self.state, NOW)], ["dev stuck:545"])
        # Not for long enough, or no longer tried: quiet.
        for times in ({"since": NOW - 600, "last": NOW - 30},
                      {"since": NOW - 3 * 3600,
                       "last": NOW - 2 * (pace + online.DEV_EVERY) - online.DEV_POLL_EVERY - 1}):
            poll.write_text(json.dumps({"at": NOW - 30, "stuck": {"545": times}}))
            self.assertEqual(health.dev_battles(self.state, NOW), [])

    def test_a_discord_read_with_news_and_no_digest_after_it_alerts(self) -> None:
        # discord.json as maintenance.discord writes it (live, 2026-09-26), for a read that found new messages.
        read = NOW - health.DIGEST_GRACE - 60
        (self.state / "discord.json").write_text(json.dumps({"attempt_at": read, "fetched_at": read, "error": None,
            "summary": "discord: 12 new messages, 3983 stored", "new_at": read}))
        (self.state / "discord").mkdir()
        digests = self.state / "discord" / "digests.md"
        digests.write_text("## Digest of the full history\n")
        os.utime(digests, (read - 60, read - 60))
        self.assertEqual([a.key for a in health.discord_digest(self.state, NOW)], ["discord:digest"])
        self.assertEqual(health.discord_digest(self.state, read + 3600), [])  # still within the grace
        os.utime(digests, (read + 600, read + 600))  # the digest saved after the read
        self.assertEqual(health.discord_digest(self.state, NOW), [])

    def test_the_digest_worker_pending_too_long_or_abandoned_alerts(self) -> None:
        # discord-digest.json as loop/reports.py writes it: while a worker runs, when it reported, when abandoned.
        read = NOW - health.DIGEST_GRACE - 60
        (self.state / "discord.json").write_text(json.dumps({"attempt_at": read, "fetched_at": read, "error": None,
            "summary": "discord: 12 new messages, 3983 stored", "new_at": read}))
        (self.state / "discord").mkdir()
        digests = self.state / "discord" / "digests.md"
        digests.write_text("## Digest of the full history\n")
        os.utime(digests, (read - 60, read - 60))
        path, work = self.state / "discord-digest.json", self.state / "discord-digest/1790399000000000000"
        path.write_text(json.dumps({"digested_at": read - 4 * 3600, "pending": str(work), "prepared_at": read + 10,
                                    "read_at": read, "pid": 4242, "launched_at": read + 11}))
        self.assertEqual(health.discord_digest(self.state, NOW), [])  # the worker is on it
        self.assertEqual(health.digest_worker(self.state, read + 10 + health.WORKER_PENDING), [])
        (alert,) = health.digest_worker(self.state, read + 11 + health.WORKER_PENDING)
        self.assertEqual(alert.key, f"digest-pending:{work.name}")
        report = self.state / "discord/digest-2026-09-26-1405.md"
        report.write_text("1. nothing new from the organisers\n")
        path.write_text(json.dumps({"digested_at": read, "report": str(report), "finished_at": read + 600,
                                    "last": str(work)}))
        (alert,) = health.discord_digest(self.state, NOW)
        self.assertIn(f"check the digest worker's report {report}, decide, and append the decisions", alert.text)
        self.assertEqual(health.digest_worker(self.state, NOW), [])
        failures = ["could not launch: OSError: no claude", "could not launch: OSError: no claude"]
        path.write_text(json.dumps({"digested_at": read, "report": None, "abandoned": [
            {"work": str(work), "at": read + 3600, "failures": failures}]}))
        (alert,) = health.digest_worker(self.state, NOW)
        self.assertEqual(alert.key, f"digest-abandoned:{work.name}")
        self.assertIn(f"digest {work / 'latest.md'} by hand", alert.text)
        self.assertIn(f"digest {self.state / 'discord' / 'latest.md'} with loop/DISCORD.md",
                      health.discord_digest(self.state, NOW)[0].text)
        os.utime(digests, (read + 4000, read + 4000))  # digested by hand, decisions appended
        self.assertEqual(health.digest_worker(self.state, NOW), [])
        self.assertEqual(health.discord_digest(self.state, NOW), [])

    # A numbered report and its decisions from loop/testdata/decisions (a digest's items numbered D1.. as
    # loop/DISCORD.md now asks; the decision blocks copied from the live digests.md), saved long enough ago to alert.
    def decide(self, report: str, *blocks: str) -> list[health.Alert]:
        (self.state / "discord").mkdir(exist_ok=True)
        saved = self.state / "discord" / report
        saved.write_text((DECISIONS / report).read_text())
        os.utime(saved, (NOW - health.DECIDE_GRACE - 60,) * 2)
        (self.state / "discord/digests.md").write_text("".join((DECISIONS / b).read_text() + "\n" for b in blocks))
        return health.undecided(self.state, NOW)

    def test_a_digest_whose_items_have_no_decision_alerts_until_each_id_gets_one(self) -> None:
        # The live 11:47 and 12:30 decisions on the 11:27 read cite no ids, so every item reads as undecided.
        (alert,) = self.decide("digest-2026-09-27-1127.md", "digests-1127-as-written.md")
        self.assertEqual(alert.key, "undecided:discord/digest-2026-09-27-1127.md")
        self.assertIn("11 items of", alert.text)
        self.assertIn("D1 no decision; D2 no decision", alert.text)
        self.assertIn("'## Orchestrator's decisions on digest-2026-09-27-1127.md", alert.text)
        self.assertEqual(self.decide("digest-2026-09-27-1127.md", "digests-1127-as-written.md",
                                     "digests-1127-numbered.md"), [])
        os.utime(self.state / "discord/digest-2026-09-27-1127.md", (NOW - 600,) * 2)  # within the grace
        (self.state / "discord/digests.md").write_text("")
        self.assertEqual(health.undecided(self.state, NOW), [])

    def test_the_sonar_items_the_1540_decisions_left_open_alert(self) -> None:
        # MISSES 52: at 15:40 the vision-sonar and no-distance items got no decision, Kieran's radio-spam claim was
        # "left to the audit's dev losses", the organiser hints "the audit watches for it" and the spinners "noted".
        (alert,) = self.decide("digest-2026-09-27-1529.md", "digests-1529-1540.md")
        missing = dict(re.findall(r"(D\d+) ([^;]+)", alert.text.split("digests.md: ", 1)[1]))
        self.assertEqual(missing, {"D3": 'hands it to a later step ("audit watches")',
                                   "D4": "no decision",
                                   "D5": 'hands it to a later step ("left to the audit")',
                                   "D6": "no reason or action given",
                                   "D12": "no decision"})
        # The 16:14 revisit decides the three sonar items; the hints and the spinners are still open.
        (alert,) = self.decide("digest-2026-09-27-1529.md", "digests-1529-1540.md", "digests-1529-1614.md")
        self.assertIn("2 items of", alert.text)
        self.assertEqual(re.findall(r"\b(D\d+) ", alert.text.split("digests.md: ", 1)[1]), ["D3", "D6"])

    def test_an_unproven_decline_needs_an_idea_or_a_reason(self) -> None:
        self.assertIn("unproven", decisions.problem("declined: hypotheses only; no new idea until it is measured"))
        self.assertIsNone(decisions.problem("declined: hypotheses only; already queued as I402 (sprint reach)"))
        self.assertIsNone(decisions.problem("declined: hypotheses only; it doesn't apply to our game (no command)"))
        self.assertIn("no verdict", decisions.problem("checkable in our dev replays"))
        self.assertIn("later step", decisions.problem("acted: briefing updated; left for the next review"))
        self.assertIsNone(decisions.problem("check started: dev test 1's sonar count before its attacks, n and p"))

    def test_audit_reflect_and_subagent_reports_with_ids_need_decisions_too(self) -> None:
        old = self.state / "audits/1790479722912203000/findings.md"
        old.parent.mkdir(parents=True)
        old.write_text("## 1. Our dragons reach rich ground late\n")  # before numbering: not checked
        reports = {"audits/1790479722912203000/findings.md": "## A1. Rounds until our first dragon leaves home\n",
                   "reflect/2026-09-27-1600.md": "- F1. (d) 16:08 sig: \"so what did u do with the sonar...\"\n",
                   "reports/2026-09-27-1620-dev-sonar.md": "## S1. Dev test 1's sonar count before attacks\n"}
        self.assertEqual(health.undecided(self.state, NOW), [])
        for name, text in reports.items():
            path = self.state / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            os.utime(path, (NOW - health.DECIDE_GRACE - 60,) * 2)
        self.assertEqual(sorted(a.key for a in health.undecided(self.state, NOW)),
                         sorted(f"undecided:{name}" for name in reports))
        (self.state / "decisions.md").write_text(
            "## Orchestrator's decisions on audits/1790479722912203000/findings.md (2026-09-27 16:40)\n"
            "- A1 (late departures): acted: queued I406, measure first.\n\n"
            "## Orchestrator's decisions on reflect/2026-09-27-1600.md (2026-09-27 16:41)\n"
            "- F1 acted: MISSES 52 and the undecided check.\n\n"
            "## Orchestrator's decisions on findings.md (2026-09-27 16:42)\n"  # names no report: doesn't count
            "- S1 declined: counts too small to tell.\n")
        (alert,) = health.undecided(self.state, NOW)
        self.assertEqual(alert.key, "undecided:reports/2026-09-27-1620-dev-sonar.md")
        self.assertIn(f"have no decision in {self.state / 'decisions.md'}: S1 no decision", alert.text)
        # A worker's report replaced by a link is read through meta.worker_file, never followed.
        link = self.state / "mapideas/20260927-060554/report.md"
        link.parent.mkdir(parents=True)
        (self.state / "elsewhere.md").write_text("- G1. a file outside the worker's folder\n")
        link.symlink_to(self.state / "elsewhere.md")
        os.utime(link, (NOW - health.DECIDE_GRACE - 60,) * 2, follow_symlinks=False)
        self.assertEqual(len(health.undecided(self.state, NOW)), 1)

    def test_ideas_and_notes_a_review_applied_itself_need_a_decision_each(self) -> None:
        # MISSES 52: review 1790489145383106000 (16:35) queued I411-I415 and changed method.md; the orchestrator
        # decided on the notes and not on the five ideas until sig asked. Its rows and method files as they were.
        rows = [json.loads(line) for line in (DECISIONS / "review-1790489145383106000-ideas.jsonl").read_text().splitlines()]
        notes = decisions.changed_notes((DECISIONS / "review-1790489145383106000-method.before.md").read_text(),
                                        (DECISIONS / "review-1790489145383106000-method.md").read_text())
        report = decisions.applied(self.state, "review:1790489145383106000", rows, notes)
        os.utime(report, (NOW - health.DECIDE_GRACE - 60,) * 2)
        (alert,) = health.undecided(self.state, NOW)
        self.assertEqual(re.findall(r"\b([IM]\d+) no decision", alert.text),
                         ["I414", "I415", "I411", "I412", "I413", "M46", "M48", "M49"])
        (self.state / "decisions.md").write_text(
            f"## Orchestrator's decisions on applied/{report.name} (2026-09-27 16:50)\n"
            "- M48 acted: the dev battles ask for side B half the time (orch/dev-sides).\n"
            "- M49 acted: rule counts split landing deaths and fold crashes (orch/rule-rows).\n"
            "- M46 kept: its change waits on I355, which is being coded.\n"
            "- I411, I415 keep: re-checks of accepted behaviour, as the rules ask each review.\n"
            "- I412 keep: a stack after I365 and I378, whose tests are in.\n"
            "- I413 merged: into I375, the same opening change.\n")
        (alert,) = health.undecided(self.state, NOW)
        self.assertIn("I414 no decision", alert.text)
        with (self.state / "decisions.md").open("a") as handle:
            handle.write("- I414 dropped: the dev benchmark already plays both sides on every map.\n")
        self.assertEqual(health.undecided(self.state, NOW), [])

    def test_a_digest_or_audit_written_since_numbering_began_with_no_ids_alerts(self) -> None:
        # Review of 89177a3: an old-style `## 1.` digest or an audit's `## A6:` gave no ids, so nothing was checked.
        saved = NOW - health.DECIDE_GRACE - 60
        reports = {"discord/digest-2026-09-28-0900.md": "## 1. nothing new from the organisers\n",
                   "audits/1790600000000000000/findings.md": "## A6: rounds until our first dragon leaves home\n",
                   "reflect/2026-09-28-0900.md": "(c) none found\n"}  # a REFLECT list may have no items
        for name, text in reports.items():
            path = self.state / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            os.utime(path, (saved, saved))
        with patch.object(decisions, "NUMBERED_SINCE", time.time() + 3600):  # all written before numbering began
            self.assertEqual(health.undecided(self.state, NOW), [])
        with patch.object(decisions, "NUMBERED_SINCE", saved - 3600):
            alerts = health.undecided(self.state, NOW)
        self.assertEqual([a.key for a in alerts], ["undecided:discord/digest-2026-09-28-0900.md",
                                                   "undecided:audits/1790600000000000000/findings.md"])
        self.assertIn("has no D1.-numbered items", alerts[0].text)
        self.assertIn("has no A1.-numbered items", alerts[1].text)

    def test_the_first_reports_since_numbering_began_must_carry_ids(self) -> None:
        # The numbered prompts' first real firing: a REFLECT list or an applied/ file whose items parse to no ids
        # would pass every later check silently.
        saved = NOW - health.DECIDE_GRACE - 60
        def write(name: str, text: str) -> None:
            path = self.state / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            os.utime(path, (saved, saved))
        with patch.object(decisions, "NUMBERED_SINCE", saved - 3600):
            self.assertEqual(health.numbering(self.state, NOW), [])  # nothing written yet
            write("reflect/2026-09-28-0900.md", "1. (d) 08:40 sig: \"why\" | what happened\n")
            write("applied/2026-09-28-0900-review-1.md", "## I0411 dev side B (path, priority 2)\n")
            write("discord/digest-2026-09-28-0900.md", "- D1. nothing new from the organisers\n")
            self.assertEqual([a.key for a in health.numbering(self.state, NOW)],
                             ["numbering:reflect", "numbering:applied"])
            write("reflect/2026-09-28-1300.md", "- F1. (d) 12:40 sig: \"why\" | what happened\n")
            (alert,) = health.numbering(self.state, NOW)
            self.assertEqual(alert.key, "numbering:applied")
            self.assertIn("applied/2026-09-28-0900-review-1.md", alert.text)
            self.assertEqual(health.numbering(self.state, saved + 60), [])  # within the grace
        with patch.object(decisions, "NUMBERED_SINCE", time.time() + 3600):  # all written before numbering began
            self.assertEqual(health.numbering(self.state, NOW), [])

    def test_a_range_of_ids_decides_each_id_in_it(self) -> None:
        # Review of 89177a3: `- D4-D6 declined: ...` recorded only D4.
        made = decisions.decided(["- D4-D6 declined: rating chatter, nothing about the game.\n"
                                  "- D7 – D8 acted: briefing line added for both.\n"
                                  "- I001 to I003 keep: re-checks the rules ask each review for.\n"
                                  "- I411-M48 keep: two different letters are two ids, not a range.\n"], "DIM")
        self.assertEqual(sorted(made), ["D4", "D5", "D6", "D7", "D8", "I001", "I002", "I003", "I411", "M48"])
        self.assertTrue(made["D5"].startswith("declined: rating chatter"))

    def test_a_deferral_is_a_hand_off_but_the_word_deferred_alone_is_not(self) -> None:
        self.assertIsNone(decisions.problem("acted: fixed the deferred read bug in discord_read"))
        self.assertIn("later step", decisions.problem("declined: deferred to the next audit"))
        self.assertIn("later step", decisions.problem("acted: briefing updated; defer it until the freeze report"))

    def test_a_modification_time_set_ahead_does_not_keep_a_report_inside_the_grace(self) -> None:
        # Review of 89177a3: a worker dating its findings.md a year ahead was never checked.
        path = self.state / "audits/1790479722912203000/findings.md"
        path.parent.mkdir(parents=True)
        path.write_text("## A1. Rounds until our first dragon leaves home\n")
        os.utime(path, (time.time() + 365 * 86400,) * 2)
        (alert,) = health.undecided(self.state, time.time() + health.DECIDE_GRACE + 60)
        self.assertEqual(alert.key, "undecided:audits/1790479722912203000/findings.md")

    def test_a_title_with_a_newline_adds_no_items_to_an_applied_file(self) -> None:
        report = decisions.applied(self.state, "audit:1790479722912203000", [
            {"id": "I0500", "title": "real\n## I0999. fake", "category": "path", "priority": 2}])
        self.assertEqual(decisions.items(report.read_text(), "IM"), ["I0500"])

    def test_a_partial_discord_read_alerts_once(self) -> None:
        # discord.json as maintenance.discord writes it after a partial read (tools/discord_read.py exit 3). With a
        # read a day, the first partial one alerts, while it is under health.REPEAT old, not every REPEAT all day.
        report = {"attempt_at": NOW - 600, "fetched_at": NOW - 600, "error": None, "partial_runs": 1,
                  "summary": "discord: partial (1 problems): 4 new messages, 4120 stored",
                  "partial": "#ask-devs: out of time after 3 of 7 threads"}
        (self.state / "discord.json").write_text(json.dumps(report))
        (alert,) = health.discord_partial(self.state, NOW)
        self.assertEqual(alert.key, "discord:partial")
        self.assertIn("the Discord read was partial", alert.text)
        self.assertIn("#ask-devs: out of time after 3 of 7 threads", alert.text)
        self.assertEqual(health.discord_partial(self.state, NOW + health.REPEAT), [])
        (self.state / "discord.json").write_text(json.dumps({**report, "partial_runs": 0}))
        self.assertEqual(health.discord_partial(self.state, NOW), [])

    @unittest.skipUnless((Path(__file__).resolve().parent / "testdata/discord-teams").exists(),
                         "the Discord captures are not published")
    def test_a_failed_or_old_discord_teams_refresh_alerts(self) -> None:
        # teams.json as loop/discord_teams.refresh writes it, around a real leaderboard's member lists.
        import discord_teams
        (self.state / "discord.json").write_text(json.dumps({"attempt_at": NOW - 600, "fetched_at": NOW - 600}))
        self.assertEqual([a.key for a in health.discord_teams(self.state, NOW)], ["discord:teams"])
        board = json.loads((DATA.parent / "discord-teams/leaderboard.json").read_text())
        read = NOW - health.TEAMS_FAILING - 1200
        discord_teams.refresh(self.state, read, lambda path: board, sleep=lambda _: None)
        self.assertEqual(health.discord_teams(self.state, NOW), [])
        self.assertEqual([a.key for a in health.discord_teams(self.state, read + health.TEAMS_AGE + 1)],
                         ["discord:teams-old"])

        def down(path: str) -> object:
            raise OSError("HTTP Error 503: Service Unavailable")

        failed = NOW - health.TEAMS_FAILING - 60
        discord_teams.refresh(self.state, failed, down, every=0)
        self.assertEqual(health.discord_teams(self.state, failed + 3600), [])  # one failed hourly refresh: a blip
        discord_teams.refresh(self.state, NOW - 60, down, every=0)
        (alert,) = health.discord_teams(self.state, NOW)
        self.assertEqual(alert.key, "discord:teams-error")
        self.assertIn("OSError: HTTP Error 503", alert.text)
        discord_teams.refresh(self.state, NOW, lambda path: board, sleep=lambda _: None, every=0)
        self.assertEqual(health.discord_teams(self.state, NOW), [])
        discord_teams.path_in(self.state).write_text('{"teams": [')
        self.assertEqual([a.key for a in health.discord_teams(self.state, NOW)], ["discord:teams-unreadable"])

    @unittest.skipUnless((Path(__file__).resolve().parent / "testdata/discord-teams").exists(),
                         "the Discord captures are not published")
    def test_a_record_naming_a_team_by_its_old_name_alerts(self) -> None:
        # The real reads either side of team 306's rename (Vibing++ -> Cutlery, 2026-09-28), and main's DISCORD.md
        # lines of then that still said "Vibing++ (rank 2)".
        import discord_teams
        reads = json.loads((DATA.parent / "discord-teams/renames-2026-09-28.json").read_text())
        # Team 314 (#12: not tracked) renamed from a common word, which the briefing uses about everyone's dragons.
        before = [{**r, "name": "dragon"} if r["id"] == 314 else r for r in reads["before"]]
        for n, board in enumerate([before, reads["after"]]):
            discord_teams.refresh(self.state, NOW + n, lambda path, board=board: board, sleep=lambda _: None, every=0)
        repo = self.state / "repo"
        (repo / "loop").mkdir(parents=True)
        (repo / "loop/DISCORD.md").write_text(
            "- **Lachy Dauth of Vibing++** (team 306, ranked 2 on 2026-09-27)\n"
            "  leader's. \"[jane-river]LachyDauthFan\" is a different person, not on Vibing++.\n")
        (self.state / "briefing.md").write_text(
            "- team 306 (renamed from Vibing++ to Cutlery, sig 2026-09-28; rank 1)\n"  # gives the current name: fine
            "- Vibing++2 is another team; Vibing++ plays kings\n"
            "- a long dragon splits late\n"  # team 314's old name, but it is below the tracked ranks
            "- Vibing++ copies CutleryBot\n")  # the current name only inside another word: still the old name
        found = {a.key: a.text for a in health.old_team_names(self.state, repo)}
        self.assertEqual(sorted(found), ["teams:old-name:306:DISCORD.md", "teams:old-name:306:briefing.md"])
        self.assertIn("briefing.md line 2, 4 names team 306 by its old name \"Vibing++\"; it is now \"Cutlery\" (#1)",
                      found["teams:old-name:306:briefing.md"])
        self.assertIn("DISCORD.md line 1, 2 names", found["teams:old-name:306:DISCORD.md"])
        (self.state / "briefing.md").write_text("- Vibing++2 is another team\n")  # not the old name as a whole name
        (repo / "loop/DISCORD.md").write_text("- **Lachy Dauth of team 306**\n")
        self.assertEqual(health.old_team_names(self.state, repo), [])

    @unittest.skipUnless((Path(__file__).resolve().parent / "testdata/discord-teams").exists(),
                         "the Discord captures are not published")
    def test_team_pages_that_all_failed_alert_after_the_grace_though_later_runs_try_none(self) -> None:
        import discord_teams
        (self.state / "discord.json").write_text(json.dumps({"attempt_at": NOW - 600, "fetched_at": NOW - 600}))
        bare = [{k: v for k, v in row.items() if k != "members"}
                for row in json.loads((DATA.parent / "discord-teams/leaderboard.json").read_text())]

        def pages_down(path: str) -> object:
            if path == "/leaderboard":
                return bare
            raise OSError("HTTP Error 502")

        start = NOW - health.TEAMS_FAILING - 3600
        for hour in range(5):  # the first run tries every page; the next four try none (they wait a day)
            discord_teams.refresh(self.state, start + hour * 3600, pages_down, sleep=lambda _: None)
        (alert,) = health.discord_teams(self.state, start + 4 * 3600 + 60)
        self.assertEqual(alert.key, "discord:teams-error")
        self.assertIn("6 of 6 team pages failed", alert.text)

    def test_a_briefing_older_than_what_it_distils_alerts(self) -> None:
        self.heal()
        briefing = self.state / "briefing.md"
        briefing.write_text("# Briefing\n- tournament maps are unseen (organisers, 2026-09-23)\n")
        os.utime(briefing, (NOW - 7200, NOW - 7200))
        os.utime(self.state / "method.md", (NOW - 60, NOW - 60))  # a new finding after the briefing, over an hour on
        with patch.object(health, "moved_at", return_value=NOW - 3 * 86400):  # promoted long before
            self.assertEqual([a.key for a in self.briefing(NOW)], ["briefing:method.md"])
            os.utime(briefing, (NOW, NOW))  # folded in (or re-confirmed)
            self.assertEqual(self.briefing(NOW), [])
            self.assertEqual([a.key for a in self.briefing(NOW + health.BRIEFING_CONFIRM + 60)], ["briefing:old"])

    def briefing(self, now: float, seen: dict | None = None) -> list:
        return health.briefing(self.state, self.repo, now, {} if seen is None else seen)

    # A CLAUDE.md with both rule sections (health.RULE_SECTIONS) and one other, edited by `change` if given.
    def write_claude_md(self, at: float, change=None) -> Path:
        text = ("# Rules\n\n## Confirmed by the organisers\n\n- **Tournament maps are unseen beforehand.**\n\n"
                "## Decisions (sig, 2026-09-23)\n\n- **Fake sonar messages are allowed.**\n\n## Where things are\n\n"
                "- `docs/unsw/`: the official docs.\n")
        if change:
            text = change(text)
        path = self.repo / "CLAUDE.md"
        path.write_text(text)
        os.utime(path, (at, at))
        return path

    def test_a_rules_change_in_claude_md_after_the_briefing_alerts_until_it_is_folded_in(self) -> None:
        self.heal()
        briefing = self.state / "briefing.md"
        os.utime(briefing, (NOW - 7200, NOW - 7200))
        seen: dict = {}
        with patch.object(health, "moved_at", return_value=NOW - 3 * 86400):
            self.write_claude_md(NOW - 7200)
            self.assertEqual(self.briefing(NOW, seen), [])  # the first check records the sections as the briefing's
            # An edit outside the two sections changes nothing.
            self.write_claude_md(NOW - 5400, lambda t: t.replace("## Where things are", "## Where things are\n\n- x"))
            self.assertEqual(self.briefing(NOW, seen), [])
            sonar = "- **Fake sonar messages are allowed.**"
            decided = lambda t: t.replace(sonar, f"- **Tournament versions are frozen on 30 Sep.**\n{sonar}")
            self.write_claude_md(NOW - 1800, decided)
            self.assertEqual(self.briefing(NOW, seen), [])  # changed half an hour ago: within the grace
            (alert,) = self.briefing(NOW + 1900, seen)
            self.assertTrue(alert.key.startswith("briefing:rules:"))
            self.assertIn("Confirmed by the organisers / ## Decisions sections changed", alert.text)
            os.utime(briefing, (NOW + 2000, NOW + 2000))  # folded in
            self.assertEqual(self.briefing(NOW + 2100, seen), [])
            self.assertEqual(self.briefing(NOW + 9000, seen), [])  # and stays quiet: the new hash is the briefing's
            # A renamed section is a change too, and one with neither section can't be watched at all.
            self.write_claude_md(NOW + 3000, lambda t: decided(t).replace("## Confirmed by the organisers", "## Organisers"))
            self.assertIn("briefing:rules:", " ".join(a.key for a in self.briefing(NOW + 9000, seen)))
            self.write_claude_md(NOW + 3000, lambda t: re.sub(r"^## (Confirmed|Decisions).*$", "## Other", t, flags=re.M))
            self.assertEqual([a.key for a in self.briefing(NOW + 9000, seen)], ["briefing:no-rules"])

    def test_a_champion_promoted_after_the_briefing_alerts_after_an_hour(self) -> None:
        self.heal()
        briefing = self.state / "briefing.md"
        os.utime(briefing, (NOW - 7200, NOW - 7200))
        champion = git(self.repo, "rev-parse", "--short=12", "loop/champion")
        with patch.object(health, "moved_at", return_value=NOW - 1800):
            self.assertEqual(self.briefing(NOW), [])
            self.assertEqual([a.key for a in self.briefing(NOW + 1900)], [f"briefing:champion:{champion}"])
            os.utime(briefing, (NOW + 2000, NOW + 2000))
            self.assertEqual(self.briefing(NOW + 2100), [])

    def test_notes_over_a_reviews_cap_alert(self) -> None:
        # My own additions took method.md to 42 lines, over the 40 a review must keep, which rejects the review whole.
        self.heal()
        (self.state / "method.md").write_text("# Open findings about the tests\n" + "- line\n" * health.MAX_METHOD_LINES)
        self.assertIn("method:size:method.md", [a.key for a in health.method(self.state, NOW, {})])
        (self.state / "method.md").write_text("# Open findings about the tests\n")
        self.assertNotIn("method:size:method.md", [a.key for a in health.method(self.state, NOW, {})])

    def test_upkeep_jobs_failing_past_the_grace_alert(self) -> None:
        # The leader study failed for 16 h with nothing but study.json saying so.
        edit(self.state / "study.json", lambda r: r.update(fetched_at=r["attempt_at"] - 16 * 3600,
                                                           error="Traceback\nJSONDecodeError: Extra data"))
        edit(self.state / "scout.json", lambda r: r.pop("fetched_at"))
        seen: dict = {}
        (alert,) = health.upkeep(self.state, NOW, seen)
        self.assertIn("leader study: no success for over 3 h (last success", alert.text)
        self.assertIn("JSONDecodeError: Extra data; see", alert.text)
        # A record without a success yet (a collection in progress, or never) is timed from when it was first seen.
        def scouting(now: float) -> list[str]:
            return [a.text for a in health.upkeep(self.state, now, seen) if a.key == "upkeep:scout.json"]
        self.assertEqual(scouting(NOW + 3 * 3600 - 60), [])
        (text,) = scouting(NOW + 3 * 3600 + 60)
        self.assertIn("public scouting: no success for over 3 h (last success none recorded since at least", text)

    def test_a_job_that_stops_being_attempted_alerts_unless_stopped(self) -> None:
        # The tick skips online games while publishing is pending, blocked or failing: no attempt, no error.
        online = json.loads((self.state / "online.json").read_text())
        later = online["fetched_at"] + 3600 + 60
        self.assertEqual([a.key for a in health.upkeep(self.state, later, {})], ["upkeep:online.json"])
        (self.state / "ASTRA_STOP").touch()
        self.assertEqual(health.upkeep(self.state, later, {}), [])

    def test_a_maintenance_error_alerts_once_it_has_held_past_the_grace(self) -> None:
        edit(self.state / "maintenance.json", lambda r: r.update(check_error="RuntimeError: gstack browse unavailable"))
        seen: dict = {}

        def checks(now: float) -> list[str]:  # online games' own staleness aside
            return [a.text for a in health.upkeep(self.state, now, seen) if a.key.startswith("maintenance:")]
        self.assertEqual(checks(NOW), [])
        (text,) = checks(NOW + health.UPKEEP_GRACE + 1)
        self.assertIn("hourly docs and toolkit check failing since at least", text)
        edit(self.state / "maintenance.json", lambda r: r.update(check_error=None))
        self.assertEqual(checks(NOW + health.UPKEEP_GRACE + 2), [])
        self.assertEqual(seen, {})

    def test_evidence_for_an_older_champion_or_stale_alerts_after_the_grace(self) -> None:
        path = self.state / "evidence.md"
        path.write_text(path.read_text().replace(git(self.repo, "rev-parse", "loop/champion"), "0" * 40))
        promoted = time.time()  # the fixture repo's champion branch moved just now
        self.assertEqual(health.evidence(self.state, self.repo, promoted + 3600), [])
        (alert,) = health.evidence(self.state, self.repo, promoted + health.EVIDENCE_GRACE + 60)
        self.assertIn("evidence.md shows champion 0000000, but the champion checkout is", alert.text)
        # Each alert names the latest refresh's log and quotes its last line: why it failed or where it stopped.
        logs = []
        for number, last in ((8, "evidence refreshed"), (9, "RuntimeError: evidence games against folder failed")):
            log = self.state / f"runs/E999{number}/evidence.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"2026-09-27 16:00 starting\n{last}\n\n")
            os.utime(log, (NOW + number, NOW + number))
            logs.append(log)
        (alert,) = health.evidence(self.state, self.repo, promoted + health.EVIDENCE_GRACE + 60)
        self.assertIn(f"latest refresh log {logs[1]}: RuntimeError: evidence games against folder failed", alert.text)
        # The refresh no longer waits for the evaluation lock (MISSES 50), so a running evaluation buys it no time.
        marker = evaluating_marker()
        marker.write_text(json.dumps({"pid": os.getpid(), "since": promoted}))
        self.addCleanup(marker.unlink, missing_ok=True)
        self.assertEqual(len(health.evidence(self.state, self.repo, promoted + health.EVIDENCE_GRACE + 60)), 1)
        marker.unlink()
        path.write_text("STALE: the refresh for champion abc1234 failed (timed out); the games below are an older "
                        "champion's.\n\n" + path.read_text())
        os.utime(path, (NOW, NOW))
        self.assertEqual(health.evidence(self.state, self.repo, NOW + 3600), [])
        (alert,) = health.evidence(self.state, self.repo, NOW + health.EVIDENCE_GRACE + 60)
        self.assertEqual(alert.key, "evidence:stale")
        self.assertIn(f"latest refresh log {logs[1]}: RuntimeError", alert.text)
        for log in logs:
            log.unlink()
        (alert,) = health.evidence(self.state, self.repo, NOW + health.EVIDENCE_GRACE + 60)
        self.assertIn("no refresh log", alert.text)

    def test_an_unpublished_champion_or_mismatched_live_submission_alerts(self) -> None:
        edit(self.state / "publishing.json", lambda r: r.update(error=None, waiting="upload budget exhausted")
             or r["verified"].update(commit="1" * 40))
        promoted = time.time()
        self.assertEqual(health.publishing(self.state, self.repo, promoted + 3600), [])
        (alert,) = health.publishing(self.state, self.repo, promoted + health.PUBLISH_GRACE + 60)
        self.assertIn("the verified online bot is 1111111, but loop/champion is", alert.text)
        self.assertIn("publishing error: upload budget exhausted", alert.text)  # a wait, shown the same
        self.heal()
        champion = git(self.repo, "rev-parse", "loop/champion")
        edit(self.state / "publishing.json", lambda r: r.update(blocked=True, error="uploaded source mismatch")
             or r["verified"].update(commit=champion))
        edit(self.state / "online.json", lambda r: r.update(submission_id=6200))
        verified = json.loads((self.state / "publishing.json").read_text())["verified"]["at"]
        keys = [a.key for a in health.publishing(self.state, self.repo, verified + health.PUBLISH_GRACE + 60)]
        self.assertEqual(keys, ["publishing:blocked", "online:6200-6111"])

    def test_a_held_version_that_is_not_live_alerts_in_place_of_the_champion(self) -> None:
        git(self.repo, "tag", "v0078", "loop/champion")
        hold = self.state / "PUBLISH_HOLD"
        hold.write_text(json.dumps({"version": "v0078", "until": "2099-01-01T12:00+10:00", "why": "Sprint"}))
        edit(self.state / "publishing.json", lambda r: r["verified"].update(commit="1" * 40))
        now = hold.stat().st_mtime
        self.assertEqual(health.publishing(self.state, self.repo, now + 60), [])
        (alert,) = health.publishing(self.state, self.repo, now + health.PUBLISH_GRACE + 60)
        self.assertEqual(alert.key, "publishing:held-v0078")
        git(self.repo, "tag", "-d", "v0078")  # a held tag that is gone alerts at once, not raises
        (alert,) = health.publishing(self.state, self.repo, now + 60)
        self.assertIn("has no tag here", alert.text)
        hold.write_text("{")
        self.assertEqual([a.key for a in health.publishing(self.state, self.repo, now)], ["publishing:hold"])

    def test_a_champion_held_for_spoofing_is_reported_at_once_with_its_release_time(self) -> None:
        champion = git(self.repo, "rev-parse", "loop/champion")
        waiting = f"champion {champion[:7]} spoofs sonar (P.spoof true); held until 2099-10-10T12:00:00+11:00"
        edit(self.state / "publishing.json", lambda r: r.update(error=None, waiting=waiting, spoof_held=champion)
             or r["verified"].update(commit="1" * 40))
        (alert,) = health.publishing(self.state, self.repo, time.time())
        self.assertEqual(alert.key, f"publishing:spoof-{champion[:12]}")
        self.assertIn("held until 2099-10-10T12:00:00+11:00", alert.text)

    def test_a_referee_copy_missing_the_adversarial_readme_alerts(self) -> None:
        (self.repo / "maps/adversarial").mkdir(parents=True, exist_ok=True)
        (self.repo / "maps/adversarial/README.md").write_text("| Map | Opponent |\n| c116 | starter |\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "an adversarial map's opponent")
        (alert,) = health.referee_lag(self.state, self.repo, time.time() + health.REFEREE_GRACE + 60)
        self.assertIn("lacks 1 main commits to referee/, mapgen/ or maps/", alert.text)

    def test_a_referee_copy_missing_referee_commits_alerts_after_six_hours(self) -> None:
        (self.repo / "bot/main.cpp").write_text("// bot 2\n")
        (self.repo / "referee/test_run.py").write_text("# a referee test\n")
        (self.repo / "referee/WINDOWS.md").write_text("notes\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "bot, a referee test and notes: no game changes")
        later = time.time() + health.REFEREE_GRACE + 60
        self.assertEqual(health.referee_lag(self.state, self.repo, later), [])
        (self.repo / "referee/run.py").write_text("# referee 2\n")
        git(self.repo, "commit", "-qam", "referee change")
        self.assertEqual(health.referee_lag(self.state, self.repo, time.time() + 3600), [])
        (alert,) = health.referee_lag(self.state, self.repo, later)
        self.assertIn("lacks 1 main commits to referee/, mapgen/ or maps/", alert.text)
        # A ladder map committed to main is played only once the copy has it (the official report, the CPU gate).
        (self.repo / "maps/sprint").mkdir(parents=True, exist_ok=True)
        (self.repo / "maps/sprint/autarky.map").write_text("MAP 54 18\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "a ladder map")
        (alert,) = health.referee_lag(self.state, self.repo, later)
        self.assertIn("lacks 2 main commits", alert.text)

    def test_a_host_without_games_in_a_long_test_or_resting_an_hour_alerts(self) -> None:
        # As captured: zz (off overnight) played none of the test's 669 games.
        (alert,) = health.hosts(self.state, NOW)
        self.assertEqual(alert.key, "host:zz:none")
        self.assertIn("zz played 0 of 669 games in the latest test", alert.text)
        self.assertIn("Operation timed out", alert.text)
        self.assertEqual(health.hosts(self.state, NOW + health.HOST_STATUS_AGE), [])  # a finished, old test

        def mm1_resting(minutes: int) -> None:  # its last game `minutes` before the status was written
            last = time.strftime("%H:%M:%S", time.localtime(NOW - 30 - minutes * 60))
            edit(self.state / "remote-status.json", lambda s: s.update(games=150) or s["hosts"]["mm1"].update(
                resting=True, last_game=last))
        mm1_resting(36)
        self.assertEqual(health.hosts(self.state, NOW), [])
        mm1_resting(76)  # the day before, on the captured clock (01:06 AEST)
        self.assertEqual([a.key for a in health.hosts(self.state, NOW)], ["host:mm1:idle"])

    def test_a_part_time_host_alerts_only_once_down_a_day(self) -> None:
        # sig 2026-09-27: zz is shut down by day and opened at night. Marked "part_time", its 0 of 669 games is quiet
        # while its outage is younger than PART_TIME_DOWN, alerts past it, and alerts as before when no outage is
        # recorded (it answers, yet plays nothing).
        edit(self.state / "remote.json", lambda s: next(h for h in s["hosts"] if h["name"] == "zz")
             .update(part_time=True))
        outages = self.state / "host-outages.json"
        outages.write_text(json.dumps({"zz": NOW - 6 * 3600}))
        self.assertEqual(health.hosts(self.state, NOW), [])
        outages.write_text(json.dumps({"zz": NOW - health.PART_TIME_DOWN - 60}))
        self.assertEqual([a.key for a in health.hosts(self.state, NOW)], ["host:zz:none"])
        outages.write_text("{}")
        self.assertEqual([a.key for a in health.hosts(self.state, NOW)], ["host:zz:none"])
        # Down within the day but answering and failing (a full disk) is no plan: it alerts as any other.
        outages.write_text(json.dumps({"zz": NOW - 6 * 3600}))
        edit(self.state / "remote-status.json", lambda s: s["hosts"]["zz"].update(
            last_rest="01:03:45 rested 240s: zz: exit 1: tar: write error: No space left on device"))
        self.assertEqual([a.key for a in health.hosts(self.state, NOW)], ["host:zz:none"])

    # 03:34 on 2026-09-27: az-jp stopped answering SSH and the orchestrator guessed a spot eviction; `az vm list -d`
    # (loop/testdata/azure-vms-sample.json, captured then) said "VM deallocated". A host down 40+ minutes now gets its state.
    def test_an_az_host_resting_40_minutes_gets_its_azure_power_state_once(self) -> None:
        sample = (Path(__file__).resolve().parent / "testdata/azure-vms-sample.json").read_text()
        self.assertEqual(health.power_states(sample)["bc-burst-jp"], "VM deallocated")
        calls = []
        def power():
            calls.append(1)
            return health.power_states(sample), None
        def resting(name: str, minutes: int) -> None:  # its last game `minutes` before the status was written
            last = time.strftime("%H:%M:%S", time.localtime(NOW - 30 - minutes * 60))
            edit(self.state / "remote-status.json", lambda s: s["hosts"][name].update(resting=True, last_game=last))
        resting("az-jp", 45)
        resting("az-syd", 32)  # under AZURE_DOWN
        resting("mm1", 90)  # not an Azure VM
        seen = {}
        at = NOW - 30  # when the status was written
        (alert,) = health.azure_hosts(self.state, at, seen, power)
        self.assertEqual(alert.text, "az-jp down 45 min: Azure says VM deallocated (the restart job runs every 20 min)")
        self.assertEqual(health.azure_hosts(self.state, at + 60, seen, power), [])  # once per outage
        self.assertEqual(len(calls), 1)
        (alert,) = health.azure_hosts(self.state, at + 9 * 60, seen, power)  # az-syd reaches 41 minutes
        self.assertEqual((alert.text.split(":")[0], health.azure_vm("az-syd")), ("az-syd down 41 min", "bc-burst"))
        edit(self.state / "remote-status.json", lambda s: s["hosts"]["az-jp"].update(resting=False))
        health.azure_hosts(self.state, at + 9 * 60 + 30, seen, power)
        self.assertNotIn("az-jp", seen)  # seen playing: that outage ended
        # A new outage, with Azure unreadable: one quiet line, no traceback.
        resting("az-jp", 50)
        (alert,) = health.azure_hosts(self.state, at, {}, lambda: ({}, "az timed out after 20 s"))
        self.assertEqual((alert.key, alert.text.split(": ")[0]), ("azure:unread", "az-jp down 50 min"))
        self.assertIn("unread (az timed out after 20 s)", alert.text)
        with patch.object(health.subprocess, "run", side_effect=subprocess.TimeoutExpired("az", 20)):
            self.assertEqual(health.azure_power(), ({}, "az timed out after 20 s"))
        with patch.object(health.subprocess, "run", side_effect=FileNotFoundError("az")):
            self.assertEqual(health.azure_power(), ({}, "az is not installed"))

    def test_hosts_are_checked_in_tunes_and_the_late_screens_live_status_files_too(self) -> None:
        # A real filler status file (the sweep's of 2026-09-26 10:41: az-hk and zz timed out on SSH and played none),
        # as a tune and the late screen now write beside their runs instead of the evaluation's remote-status.json.
        sample = json.loads((Path(__file__).resolve().parent / "testdata/remote-status-sweep-sample.json").read_text())
        tune = self.state / "runs/E0200/remote-status.json"
        screen = self.state / "rescreens/1c225be954742d086a32502a5a30a0a159d95c69/remote-status.json"
        for path in (tune, screen):
            path.parent.mkdir(parents=True)
        note = "10:40:40 engine cache: cannot save /home/bc/.bc-remote/engine-cache/x.cwasm: [Errno 28] No space left"
        tune.write_text(json.dumps({**sample, "updated": NOW - 60, "games": 240}))
        sample["hosts"]["az-mel"]["engine_cache"] = note
        screen.write_text(json.dumps({**sample, "updated": NOW - 60}))
        alerts = health.hosts(self.state, NOW)
        self.assertIn(("host:az-hk:none", str(tune)), {(a.key, a.text.rsplit("see ", 1)[1]) for a in alerts})
        self.assertIn("Operation timed out", next(a.text for a in alerts if a.key == "host:az-hk:none"))
        self.assertEqual([a.key for a in health.engine_cache(self.state, NOW)], ["engine-cache:az-mel"])
        # A host the evaluation kept full plays none of a filler's games and hasn't failed: no alert in a filler file.
        edit(tune, lambda s: s["hosts"]["az-hk"].update(rests=0, last_rest=None))
        self.assertNotIn(("host:az-hk:none", str(tune)),
                         {(a.key, a.text.rsplit("see ", 1)[1]) for a in health.hosts(self.state, NOW)})
        edit(tune, lambda s: s.update(updated=NOW - health.STATUS_LIVE - 60))  # a tune that finished: not live
        self.assertNotIn("host:az-hk:none", [a.key for a in health.hosts(self.state, NOW)])
        self.assertEqual(health.status_files(self.state, NOW)[0][0], self.state / "remote-status.json")

    def test_coders_reviews_prewarm_and_official_status_files_are_checked_while_fresh(self) -> None:
        # A real rule-count status file (remote-status-review.json, 2026-09-26 13:24). Coders, reviews, the prewarm and
        # official-map reports each write their own, so a host resting through their games went unseen.
        sample = json.loads((Path(__file__).resolve().parent / "testdata/remote-status-review-sample.json").read_text())
        sample["hosts"]["mm1"].update(resting=True, rests=1, last_rest="ssh: connect to host mm1: Operation timed out",
                                      last_game=time.strftime("%H:%M:%S", time.localtime(NOW - 30 - 76 * 60)))
        files = [self.state / "remote-status-E0200.json", self.state / "remote-status-review.json",
                 self.state / "remote-status-prewarm.json", self.state / "runs/E0201/remote-status-official.json"]
        files[3].parent.mkdir(parents=True)
        for path in files:
            path.write_text(json.dumps({**sample, "updated": NOW - 30}))
        self.assertLessEqual(set(files), {path for path, _ in health.status_files(self.state, NOW)})
        idle = {a.text.rsplit("see ", 1)[1] for a in health.hosts(self.state, NOW) if a.key == "host:mm1:idle"}
        self.assertEqual(idle, set(map(str, files)))
        for path in files:  # written over 10 minutes ago: finished, so not a test running now
            path.write_text(json.dumps({**sample, "updated": NOW - health.STATUS_LIVE - 60}))
        self.assertEqual({path for path, _ in health.status_files(self.state, NOW)} & set(files), set())

    def test_games_that_compile_the_engine_alert(self) -> None:
        self.assertEqual(health.engine_cache(self.state, NOW), [])  # as captured, before the cache existed
        # A referee run on a toolkit the cache wasn't written for (run._engine_launcher's file).
        reason = "unswbc's engine.py changed (digest 0123456789abcdef); re-read it and add it to KNOWN_ENGINES"
        (self.state / "engine-cache-off.json").write_text(json.dumps({"at": NOW - 60, "reason": reason}))
        (alert,) = health.engine_cache(self.state, NOW)
        self.assertEqual(alert.key, "engine-cache")
        self.assertIn("digest 0123456789abcdef", alert.text)
        self.assertEqual(health.engine_cache(self.state, NOW + health.ENGINE_CACHE_AGE), [])
        (self.state / "engine-cache-off.json").unlink()
        # A host whose own cache fell back, in the running test's status (remote.Host.status).
        note = "05:01:12 engine cache: cannot save /home/bc/.bc-remote/engine-cache/x.cwasm: [Errno 28] No space left"
        edit(self.state / "remote-status.json", lambda s: s["hosts"]["mm"].update(engine_cache=note))
        (alert,) = health.engine_cache(self.state, NOW)
        self.assertEqual(alert.key, "engine-cache:mm")
        self.assertIn("No space left", alert.text)
        self.assertEqual(health.engine_cache(self.state, NOW + health.HOST_STATUS_AGE), [])  # an old, finished test

    def test_games_that_start_bots_as_usual_or_a_forked_game_that_differed_alert(self) -> None:
        self.assertEqual(health.fork_server(self.state, NOW), [])  # as captured, before the fork server existed
        # A referee run that couldn't build the shim (run._fork_shim's file).
        (self.state / "fork-server-off.json").write_text(json.dumps({"at": NOW - 60, "reason": "no C compiler"}))
        (alert,) = health.fork_server(self.state, NOW)
        self.assertEqual(alert.key, "fork-server")
        self.assertIn("no C compiler", alert.text)
        self.assertEqual(health.fork_server(self.state, NOW + health.FORK_OFF_AGE), [])
        (self.state / "fork-server-off.json").unlink()
        # A check game that differed (run.count_fork_check's file, captured from a real referee run with one entry
        # added as a mismatch writes it) alerts for as long as the entry stands, and a clean count doesn't.
        sample = json.loads((Path(__file__).resolve().parent / "testdata/fork-server-checks-sample.json").read_text())
        (self.state / "fork-server-checks.json").write_text(json.dumps(sample))
        self.assertEqual(health.fork_server(self.state, NOW), [])
        sample["off"] = {"0123456789abcdef": {"at": NOW - 7200, "bot": "/x/champion", "map": "/x/m.map",
                                              "a_side": "A", "said": "here: fork server: MISMATCH on m.map: ..."}}
        (self.state / "fork-server-checks.json").write_text(json.dumps(sample))
        (alert,) = health.fork_server(self.state, NOW)
        self.assertEqual(alert.key, "fork-server:off:0123456789abcdef")
        self.assertIn("bot /x/champion; no game forks", alert.text)
        self.assertEqual(len(health.fork_server(self.state, NOW + 30 * 86400)), 1)  # until someone removes it
        sample["off"]["fedcba9876543210"] = "hand-edited"  # any entry stops forking (run.forks_off), whatever it holds
        (self.state / "fork-server-checks.json").write_text(json.dumps(sample))
        self.assertEqual(len(health.fork_server(self.state, NOW)), 2)
        # MISSES 86: a check whose plain game, played twice, differed too names both bots under "nondeterministic"
        # (run.count_nondeterministic), which switches nothing off but alerts, per bot and for as long as it stands,
        # with its digest and experiment.
        sample["off"], sample["nondeterministic"] = {}, {
            digest: {"at": NOW - 7200, "bot": bot, "map": "/x/m.map", "a_side": "A",
                     "said": "mm: fork server: MISMATCH on m.map; plain twice: replay differs at r259"}
            for digest, bot in (("0123456789abcdef", "/h/runs/E0287/cand"), ("fedcba9876543210", "/x/champion"))}
        (self.state / "fork-server-checks.json").write_text(json.dumps(sample))
        alerts = health.fork_server(self.state, NOW + 30 * 86400)
        self.assertEqual([a.key for a in alerts], ["fork-server:nondeterministic:0123456789abcdef",
                                                   "fork-server:nondeterministic:fedcba9876543210"])
        self.assertIn("bot 0123456789abcdef (E0287: /h/runs/E0287/cand) played a game differently", alerts[0].text)
        self.assertIn("(no experiment: /x/champion)", alerts[1].text)
        # A file that can't be read stops all forking (run.forks_off), so it alerts rather than passing silently.
        for broken in ("{not json", "[]", '{"checks": 1, "off": []}'):
            with self.subTest(broken=broken):
                (self.state / "fork-server-checks.json").write_text(broken)
                self.assertEqual([a.key for a in health.fork_server(self.state, NOW)], ["fork-server:checks"])
        (self.state / "fork-server-checks.json").unlink()
        # A host whose fork server fell back, in the running test's status (remote.Host.status).
        note = "05:01:12 fork server: off for bot (the shim didn't start in bot (it closed its socket))"
        edit(self.state / "remote-status.json", lambda s: s["hosts"]["mm"].update(fork_server=note))
        (alert,) = health.fork_server(self.state, NOW)
        self.assertEqual(alert.key, "fork-server:mm")
        self.assertIn("didn't start", alert.text)
        self.assertEqual(health.fork_server(self.state, NOW + health.HOST_STATUS_AGE), [])  # an old, finished test

    # The native judge's reasons to play through unswbc run: a failed gate; with the switch on, a place the loop plays
    # at still without a gate; a check or crash that switched it off (native-judge-checks.json's "off", until
    # removed); and a host whose native_judge.py fell back.
    def test_native_games_that_play_through_unswbc_run_alert(self) -> None:
        self.assertEqual(health.native_judge(self.state, NOW), [])
        folder = self.state / "native-judge"
        folder.mkdir()
        row = {"key": "k1", "platform": "Linux x86_64 1a2b3c4d", "passed": False, "why": "1 mismatched",
               "report": "gate.json", "at": NOW - 60}
        wanted = {"key": "k1", "at": NOW - 60, "places": {"Darwin arm64": NOW - 60,
                                                          "Darwin arm64 host": NOW - health.NATIVE_MISSING_AGE - 60}}
        (folder / "enabled.json").write_text(json.dumps({"keys": {"k1": {row["platform"]: row}}, "wanted": wanted}))
        self.assertEqual([a.key for a in health.native_judge(self.state, NOW)],
                         ["native-judge:k1:Linux x86_64 1a2b3c4d"])
        later = NOW + health.NATIVE_GATE_AGE  # past its age, a failed gate still alerts while its key is wanted
        self.assertEqual(len(health.native_judge(self.state, later)), 1)
        (folder / "enabled.json").write_text(json.dumps({"keys": {"k1": {row["platform"]: row}},
                                                         "wanted": {**wanted, "key": "k2"}}))
        self.assertEqual(health.native_judge(self.state, later), [])
        (folder / "enabled.json").write_text(json.dumps({"keys": {"k1": {row["platform"]: row}}, "wanted": wanted}))
        switch = self.state / "referee" / "referee" / "run.py"
        switch.parent.mkdir(parents=True, exist_ok=True)
        switch.write_text("NATIVE_JUDGE = True  # on\n")
        self.assertEqual([a.key for a in health.native_judge(self.state, NOW)][1:],
                         ["native-judge:missing:Darwin arm64 host"])  # the other only just wanted
        passed = {**row, "platform": "Darwin arm64 host", "passed": True}  # this Mac goes on its Mac hosts' gate
        rows = {row["platform"]: row, passed["platform"]: passed}
        (folder / "enabled.json").write_text(json.dumps({"keys": {"k1": rows}, "wanted": wanted}))
        later = NOW + health.NATIVE_MISSING_AGE  # "Darwin arm64" too is now wanted past its age
        self.assertEqual([a.key for a in health.native_judge(self.state, later) if ":missing:" in a.key], [])
        # The live key, not the one last listed: a new key k2 (a toolkit upgrade) has no gate anywhere, and k1's
        # failed gate is old news once past its age.
        with patch.object(health, "live_native_key", return_value="k2"):
            self.assertEqual([a.key for a in health.native_judge(self.state, later) if ":missing:" in a.key],
                             ["native-judge:missing:Darwin arm64", "native-judge:missing:Darwin arm64 host"])
            self.assertEqual([a.key for a in health.native_judge(self.state, NOW + health.NATIVE_GATE_AGE)
                              if ":k1:" in a.key], [])
        # A failed Mac hosts' gate says this Mac's own games are off too, unless this Mac has a row of its own.
        failed = {**passed, "passed": False}
        for own, said in (({}, True), ({"Darwin arm64": {**passed, "platform": "Darwin arm64"}}, False)):
            (folder / "enabled.json").write_text(json.dumps({"keys": {"k1": {"Darwin arm64 host": failed, **own}},
                                                             "wanted": wanted}))
            (alert,) = [a for a in health.native_judge(self.state, NOW) if a.key == "native-judge:k1:Darwin arm64 host"]
            self.assertEqual("this Mac's own native games are off too" in alert.text, said)
        switch.write_text("NATIVE_JUDGE = False\n")
        (folder / "enabled.json").unlink()
        off = {"0123456789abcdef": {"at": NOW - 7200, "bot": "/x/champion", "said": "ll: check differs: ..."}}
        (self.state / "native-judge-checks.json").write_text(json.dumps({"checks": 3, "identical": 2, "off": off}))
        note = "05:01:12 native judge: off (cannot compile the judge: ...)"
        edit(self.state / "remote-status.json", lambda s: s["hosts"]["mm"].update(native_judge=note))
        self.assertEqual([a.key for a in health.native_judge(self.state, NOW)],
                         ["native-judge:off:0123456789abcdef", "native-judge:mm"])
        self.assertEqual(len(health.native_judge(self.state, NOW + 30 * 86400)), 1)  # the off entry, until removed
        (self.state / "native-judge-checks.json").write_text(json.dumps({"checks": 3, "nondeterministic": off}))
        self.assertEqual([a.key for a in health.native_judge(self.state, NOW)],
                         ["native-judge:nondeterministic:0123456789abcdef", "native-judge:mm"])

    def test_games_this_machine_failed_and_replayed_alert_for_a_while(self) -> None:
        self.assertEqual(health.game_retries(self.state, NOW), [])  # as captured, before games were retried
        # Written by run.note_retry for two of E0158's games at 13:03: one played on its third try, one never.
        sample = json.loads((Path(__file__).resolve().parent / "testdata/game-retries-sample.json").read_text())
        for row in sample["games"]:
            row["at"] += NOW - 600 - sample["updated"]
        (self.state / "game-retries.json").write_text(json.dumps(sample))
        (alert,) = health.game_retries(self.state, NOW)
        self.assertEqual(alert.key, "game-retries")
        self.assertIn("2 referee games in the last 6 h", alert.text)
        self.assertIn("1 of them on every try", alert.text)
        self.assertIn("gen_big_559869805.map (a B): error: [Errno 35]", alert.text)
        self.assertEqual(health.game_retries(self.state, NOW + health.RETRY_AGE), [])
        recovered = {**sample, "games": [row for row in sample["games"] if row.get("recovered")]}
        (self.state / "game-retries.json").write_text(json.dumps(recovered))
        self.assertEqual(health.game_retries(self.state, NOW), [])  # one replay that played: the retry working
        recovered["games"] = recovered["games"] * health.RETRY_ALERT_MIN
        (self.state / "game-retries.json").write_text(json.dumps(recovered))
        self.assertEqual(len(health.game_retries(self.state, NOW)), 1)  # they recur: find the cause
        (self.state / "game-retries.json").write_text("{not json")  # half-written: quiet, the next replay rewrites it
        self.assertEqual(health.game_retries(self.state, NOW), [])

    # A lazy SPRT's map marker, or its count, that no maker keeps fresh was left by a driver that died mid-SPRT; a count
    # beside a live maker's fresh marker (its SPRT read every map long ago) isn't.
    def test_a_lazy_marker_no_maker_touches_alerts(self) -> None:
        import os
        for run, (marker, count) in {"E1": (NOW - 60, NOW - 3600), "E2": (NOW - 3600, NOW - 3600),
                                     "E3": (None, NOW - 3600), "E4": (None, NOW - 60)}.items():
            (self.state / "runs" / run).mkdir(parents=True, exist_ok=True)
            for name, at in (("maps-pending", marker), ("maps-pending.read", count)):
                if at is not None:
                    (self.state / "runs" / run / name).write_text("0\n")
                    os.utime(self.state / "runs" / run / name, (at, at))
        found = sorted(Path(alert.key.split(":", 1)[1]).relative_to(self.state / "runs").as_posix()
                       for alert in health.lazy_markers(self.state, NOW))
        self.assertEqual(found, ["E2/maps-pending", "E2/maps-pending.read", "E3/maps-pending.read"])

    # MISSES 81: a map the engine refused alerts while it is recent, and an error it caused alerts until its candidate
    # is queued for re-evaluation (driver.reevaluate); an error any other way doesn't, even with a bot's output quoting
    # the refusal (prune.MAP_FAULT reads it only where the referee writes it).
    def test_refused_maps_and_an_unrequeued_map_fault_alert(self) -> None:
        (self.state / "bad-maps.jsonl").write_text(json.dumps({
            "at": NOW - 60, "map": "/p/gen_worldgen_144449864.map", "moved": "/q", "note": "generate_maps",
            "reason": "dragon 0 segment 6 is not adjacent to the one before it"}) + "\n")
        (alert,) = health.bad_maps(self.state, NOW)
        self.assertIn("gen_worldgen_144449864.map: dragon 0 segment 6", alert.text)
        self.assertEqual(health.bad_maps(self.state, NOW + health.RETRY_AGE), [])
        fault = ("RuntimeError: pool game failed: swarm: m.map (as B): the engine refused to load the map /p/m.map: "
                 "dragon 0 segment 6")  # a screen's (driver.play_batch); test_lifecycle's is the SPRT's
        rows = [{"id": "E0001", "idea": {"id": "I1"}, "verdict": "error", "reason": fault, "finished": NOW - 3600},
                {"id": "E0002", "idea": {"id": "I2"}, "verdict": "error", "finished": NOW - 3600,
                 "reason": "invalid SPRT result: game failed on m.map (as A): unswbc run exited 1: bot 3 printed: "
                           "(as A): the engine refused to load the map /p/m.map: fake"}]  # a bot can't fake a map fault
        (self.state / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        (alert,) = health.input_faults(self.state, NOW)
        self.assertIn("--reevaluate E0001", alert.text)
        (self.state / "ideas.jsonl").write_text(json.dumps({"id": "I1", "reevaluate": "E0001"}) + "\n")
        self.assertEqual(health.input_faults(self.state, NOW), [])

    def test_lowered_games_replayed_at_normal_priority_are_kept_apart_and_alert_when_many(self) -> None:
        # run.Scheduler.try_here keeps each in game-retries.json's "niced" rows, which neither crowd out nor count as
        # games this machine failed.
        row = {"at": NOW - 60, "map": "m.map", "a_side": "A", "tries": 1, "recovered": True,
               "why": "a turn ran out of time at nice 19"}
        for count, alerts in ((health.NICED_REPLAY_ALERT - 1, 0), (health.NICED_REPLAY_ALERT, 1)):
            with self.subTest(count=count):
                (self.state / "game-retries.json").write_text(json.dumps({"games": [], "niced": [row] * count}))
                self.assertEqual(len(health.niced_replays(self.state, NOW)), alerts)
                self.assertEqual(health.game_retries(self.state, NOW), [])
        self.assertEqual(health.niced_replays(self.state, NOW + health.RETRY_AGE), [])

    def test_the_cpu_split_alerts_when_refused_or_stale_while_games_play_here(self) -> None:
        import home
        path = home.split_copy()
        self.addCleanup(path.unlink, missing_ok=True)
        path.unlink(missing_ok=True)
        self.assertEqual(health.cpu_split(self.state, NOW), [])  # none, and no games here
        path.write_text(json.dumps({"at": time.time() + 3600, "dev": 1.0, "niced": 1.0}))  # dated ahead: refused
        self.assertEqual([a.key for a in health.cpu_split(self.state, NOW)], ["cpu-split:refused"])
        path.unlink()
        for minute in range(health.SPLIT_STALE_ROWS):  # games here for 3 rows, none sampling the split
            with (self.state / throughput.LOG).open("a") as log:
                log.write(json.dumps({"at": NOW - 60 * (2 - minute), "hosts": {"here": {
                    "capacity": 7, "busy": 4, "by": {"coder E0400": 2, "evaluation": 2 * (minute > 0)}}}}) + "\n")
        self.assertEqual(health.cpu_split(self.state, NOW), [])  # at first only a coder's games (they can't sample)
        with (self.state / throughput.LOG).open("a") as log:
            log.write(json.dumps({"at": NOW + 60, "hosts": {"here": {"capacity": 7, "busy": 2, "by": {"sweep": 2}}}})
                      + "\n")
        self.assertEqual([a.key for a in health.cpu_split(self.state, NOW + 60)], ["cpu-split:stale"])
        home.publish_split(home.Split(1.0, 4.0, 0.0))
        self.assertEqual(health.cpu_split(self.state, time.time()), [])

    def test_a_spell_without_room_for_a_game_alerts_naming_the_largest_process_groups(self) -> None:
        # home.ProcessWatch saves a spell once this user's processes left no room for a game for home.FULL_ALERT_S.
        self.assertEqual(health.process_limit(self.state, NOW), [])  # no file
        spell = {"since": NOW - 400, "at": NOW - 100, "count": 2250, "limit": 2666,
                 "groups": ["910 python (pgid 4521)", "70 zsh (pgid 88)"]}
        (self.state / "process-limit.json").write_text(json.dumps({"spells": [spell]}))
        [alert] = health.process_limit(self.state, NOW)
        self.assertEqual(alert.key, "process-limit")
        self.assertIn("2250 of kern.maxprocperuid 2666", alert.text)
        self.assertIn("910 python (pgid 4521), 70 zsh (pgid 88)", alert.text)
        self.assertEqual(health.process_limit(self.state, NOW - 100 + health.REPEAT), [])  # an old spell

    def test_a_spell_lasting_hours_alerts_all_along_and_an_unmeasured_gate_alerts_too(self) -> None:
        # A 6-hour spell saved once at 5 min went silent 3 h in (health.REPEAT) while every game here waited; the
        # sampler now saves it again every home.FULL_ALERT_S, so it still alerts at 6 h.
        import home
        start, now = NOW - 6 * 3600, NOW
        watch = home.ProcessWatch()
        saved = patch.object(home, "spells_file", return_value=self.state / "process-limit.json")
        saved.start()
        self.addCleanup(saved.stop)
        full = [home.Process(pid, 1, 7, "zsh") for pid in range(1, 2300)]
        for at in range(int(start), int(now) + 1, 60):
            watch.check(float(at), full, 2666)
        [alert] = health.process_limit(self.state, now)
        self.assertIn("360 min when last seen", alert.text)
        # Unreadable for home.UNREAD_ALERT_S: the gate was off (it fails open), which the full spell's alert can't say.
        for at in range(int(now), int(now + home.UNREAD_ALERT_S) + 1, 60):
            watch.check(float(at), None, 2666)
        keys = [alert.key for alert in health.process_limit(self.state, now + home.UNREAD_ALERT_S)]
        self.assertEqual(keys, ["process-limit", "process-limit-unread"])

    def test_prepared_files_left_behind_or_unreadable_alert(self) -> None:
        run = self.state / "runs/E0400"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"verdict": "", "idea": {"id": "I1"}}))
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/prepared/E0400", head)
        self.assertEqual(health.prepared(self.state, self.repo, NOW), [])  # in flight
        (run / "experiment.json").write_text(json.dumps({"verdict": "conflict", "idea": {"id": "I1"}}))
        os.utime(run / "experiment.json", (NOW - 7200, NOW - 7200))
        self.assertEqual(health.prepared(self.state, self.repo, NOW), [])  # its merge turn: not recorded yet
        # "pending" (driver.Experiment's default) while it waits for the lock, however old experiment.json is.
        (run / "experiment.json").write_text(json.dumps({"verdict": "pending", "idea": {"id": "I1"}}))
        os.utime(run / "experiment.json", (NOW - 7200, NOW - 7200))
        with patch.object(health.throughput, "waiting_now", return_value=["E0400"]):
            self.assertEqual(health.prepared(self.state, self.repo, NOW), [])
        with patch.object(health.throughput, "evaluation_now", return_value={"experiment": "E0400", "stage": "SPRT"}):
            self.assertEqual(health.prepared(self.state, self.repo, NOW), [])
        # Neither queued nor evaluated: a pending run its driver left behind.
        self.assertEqual([a.key for a in health.prepared(self.state, self.repo, NOW)], ["prepared:refs"])
        (run / "experiment.json").write_text(json.dumps({"verdict": "discarded", "idea": {"id": "I1"}}))
        os.utime(run / "experiment.json", (NOW - 7200, NOW - 7200))
        self.assertEqual([a.key for a in health.prepared(self.state, self.repo, NOW)], ["prepared:refs"])
        git(self.repo, "update-ref", "-d", "refs/prepared/E0400")
        git(self.repo, "update-ref", "refs/prepared-next/E0400", head)  # prepare_ahead's, left the same way
        self.assertEqual([a.key for a in health.prepared(self.state, self.repo, NOW)], ["prepared:refs"])
        git(self.repo, "update-ref", "-d", "refs/prepared-next/E0400")
        (run / "prepared.json").write_text("{half")
        self.assertEqual([a.key for a in health.prepared(self.state, self.repo, time.time())], ["prepared:broken"])

    # MISSES 86: E0287's build crashed mid-turn in play and nothing read macOS's crash reports. testdata/crash-report-
    # header-sample.ips is the first line of a real one of E0287's build (03:00 on 2026-09-29): a report of a
    # candidate's build since the last check alerts, naming it; an unknown build's, an old report and a second check
    # with nothing new don't.
    def test_a_crash_report_of_a_candidates_bot_build_alerts(self) -> None:
        import struct
        import uuid
        header = (Path(__file__).parent / "testdata/crash-report-header-sample.ips").read_text()
        uid = json.loads(header)["slice_uuid"]
        build = self.state / "runs/E0287/cand/.unswbc-build/bot"
        build.parent.mkdir(parents=True)
        build.write_bytes(struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 1, 24, 0, 0)
                          + struct.pack("<II", 0x1B, 24) + uuid.UUID(uid).bytes)
        reports = self.state / "DiagnosticReports"
        reports.mkdir()
        (reports / "bot-2026-09-29-030016.ips").write_text(header + "{}\n")
        (reports / "bot-2026-09-29-030017.ips").write_text(header.replace(uid, str(uuid.UUID(int=1))) + "{}\n")
        (reports / "bot-2026-09-20-010000.ips").write_text(header + "{}\n")
        os.utime(reports / "bot-2026-09-20-010000.ips", (NOW - 9 * 86400, NOW - 9 * 86400))
        seen = {}
        with patch.object(health, "CRASH_REPORTS", reports):
            for path in reports.glob("bot-2026-09-29-*.ips"):
                os.utime(path, (NOW - 60, NOW - 60))
            [alert] = health.bot_crashes(self.state, NOW, seen)
            self.assertEqual(alert.key, f"bot-crash:{uid}")
            self.assertIn("1 crash report(s) of E0287's bot build", alert.text)
            self.assertEqual(health.bot_crashes(self.state, NOW + 3600, seen), [])  # nothing since
            # The Mac hosts play this Mac's builds, and their crashes are theirs to report: the hourly upkeep asks
            # each (a fake here) for its reports since the newest it gave, a Linux host not at all.
            sys.path.append(str(health.REPO / "referee"))
            import maintenance
            import remote

            class Host:
                def __init__(self, name: str, platform: str, reports: list[dict]) -> None:
                    self.name, self.system, self.reports, self.asked = name, platform, reports, []

                def platform(self) -> str:
                    return self.system

                def crash_reports(self, name: str, since: float) -> list[dict]:
                    self.asked.append((name, since))
                    if self.system != remote.PLATFORM:
                        raise AssertionError("a Linux host has no crash reports of our builds")
                    return [r for r in self.reports if r["at"] > since]

            mm = Host("mm", remote.PLATFORM, [{"uuid": uid.upper(), "at": NOW + 3000}])
            ll = Host("ll", "Linux x86_64", [])
            with patch.object(remote, "load_hosts", return_value=[mm, ll]):
                self.assertIn("1 bot crash reports", maintenance.host_crashes(health.REPO, self.state, NOW + 3700))
                self.assertIsNone(maintenance.host_crashes(health.REPO, self.state, NOW + 3800))  # hourly
                self.assertIsNone(maintenance.host_crashes(health.REPO, self.state, NOW + 7400))  # nothing new
            self.assertEqual(mm.asked, [("bot", NOW + 3700 - health.CRASH_FIRST_S), ("bot", NOW + 3000)])
            self.assertEqual(ll.asked, [])
            [alert] = health.bot_crashes(self.state, NOW + 7400, seen)
            self.assertIn("1 crash report(s) of E0287's bot build", alert.text)
            self.assertIn("(1 on mm;", alert.text)
            self.assertEqual(health.bot_crashes(self.state, NOW + 9000, seen), [])  # found before the last check
            # A full health run while the hosts are asked (every driver.record runs one) moves the check past the
            # step's start: a row stamped with that start was never alerted.
            clock = [NOW + 11000]

            def asked_during_a_health_run(name: str, since: float) -> list[dict]:
                clock[0] += 5
                self.assertEqual(health.bot_crashes(self.state, clock[0], seen), [])
                clock[0] += 1
                return [{"uuid": uid, "at": NOW + 10000}]

            mm.crash_reports = asked_during_a_health_run
            with patch.object(remote, "load_hosts", return_value=[mm]), patch("time.time", lambda: clock[0]):
                maintenance.host_crashes(health.REPO, self.state, clock[0])
            [alert] = health.bot_crashes(self.state, clock[0] + 60, seen)
            self.assertIn("(1 on mm;", alert.text)

    # E0468 -> E0469 on 1 Oct: E0469 was coded (04:10) before E0468's promotion (04:20:30) and locked after it (04:26),
    # the new champion's set made meanwhile. Once driver.make_set_ahead runs, such a set carries MADE_AHEAD; one
    # without it alerts, but not for a promotion no candidate waited through, one without a real screen (skipped, or a
    # judged keep), a head whose queue poll predates make_set_ahead (no "set_ahead_code"), or a later waiter's set.
    def test_a_screened_promotion_whose_set_was_not_made_ahead_alerts(self) -> None:
        import driver
        at = datetime(2026, 10, 1, 8).timestamp()
        mix = driver.shared_mix()
        policy = {"fresh": True, "rotation": list(driver.ROTATION), "max_pairs": 2400, "maps": mix,
                  "toolchain": {"epoch": "unswbc-1.2.0"}}
        champion = "67aa6a05e50febb3"
        folder = self.state / "shared-maps" / driver.meta.shared_set_name(champion, "unswbc-1.2.0", mix)
        folder.mkdir(parents=True)

        def run(name: str, **row) -> None:
            (self.state / "runs" / name).mkdir(parents=True, exist_ok=True)
            (self.state / "runs" / name / "experiment.json").write_text(json.dumps(row))
            os.utime(self.state / "runs" / name / "experiment.json", (at + 1, at + 1))  # saved after the promotion

        def check() -> str | None:
            with patch.object(driver, "missing_style", return_value=None):
                return health.set_ahead(self.state, at + 400, {"old": at - 9000, champion: at})

        screened = {"candidate": champion, "pool": {"ok": True, "opponents": ["ram"]}}
        run("E0468", **screened)
        self.assertIsNone(check())  # no candidate waited through it
        run("E0469", evaluation=policy, session={"coded_at": at - 300, "locked_at": at + 330})
        self.assertIsNone(check())  # its poll ran older code
        run("E0469", evaluation=policy, session={"coded_at": at - 300, "locked_at": at + 330, "set_ahead_code": True})
        self.assertIn("E0469 at the queue's head", check())
        run("E0470", evaluation={}, session={"coded_at": at - 200, "locked_at": at + 300, "set_ahead_code": True})
        self.assertIsNone(check())  # the head was E0470, which reads no shared set: E0469's set isn't its to make
        (self.state / "runs/E0470/experiment.json").unlink()
        (folder / "made-ahead.json").write_text("{}")
        self.assertIsNone(check())
        (folder / "made-ahead.json").unlink()
        run("E0468", candidate=champion, pool={"ok": True})  # screen skipped: nothing to make it beside
        self.assertIsNone(check())
        run("E0468", **screened, judged={"verdict": "kept"})  # a judged keep's screen isn't the SPRT's
        self.assertIsNone(check())

    def test_prepare_reaching_its_checks_and_landing_no_cpu_gate_alerts(self) -> None:
        # 28 Sep: 12 recorded candidates in 12 h had their prepared/cand exported and no prepared CPU gate, unalerted.
        def run(name: str, finished: float, cpu: bool) -> None:
            folder = self.state / "runs" / name
            (folder / "prepared/cand").mkdir(parents=True)
            (folder / "experiment.json").write_text(json.dumps({"verdict": "discarded", "finished": finished}))
            (folder / "prepared.json").write_text(json.dumps({"referee": "r", **({"cpu": {"ok": True}} if cpu else {})}))

        for n in range(health.PREPARED_STALLED - 1):
            run(f"E050{n}", NOW - 3600, cpu=False)
        run("E0510", 0.0, cpu=False)  # still in flight: not counted
        run("E0511", NOW - health.PREPARED_WINDOW - 60, cpu=False)  # too old
        self.assertEqual(health.prepared_stalled(self.state, NOW), [])
        run("E0520", NOW - 60, cpu=False)
        self.assertEqual(len(health.prepared_stalled(self.state, NOW)), health.PREPARED_STALLED)
        self.assertIn("prepared:stalled", [a.key for a in health.prepared(self.state, self.repo, NOW)])
        run("E0521", NOW - 60, cpu=True)  # one landed: prepare works
        self.assertEqual(health.prepared_stalled(self.state, NOW), [])

    def test_a_prepare_with_nothing_left_to_run_or_no_time_to_run_it_is_not_stalled(self) -> None:
        # Review (28 Sep): bot tests that failed leave no CPU gate to run, and one whose evaluation beside it ended
        # within PREPARED_CHANCE_S of its reaching the checks had no time: neither is prepare failing.
        def run(name: str, **record) -> None:
            folder = self.state / "runs" / name
            (folder / "prepared/cand").mkdir(parents=True)
            (folder / "experiment.json").write_text(json.dumps({"verdict": "discarded", "finished": NOW - 60}))
            (folder / "prepared.json").write_text(json.dumps({"referee": "r", **record}))

        reached = NOW - 7200
        run("E0601", bot_tests={"ok": False, "line": "1 failed"}, checks_at=reached)
        run("E0602", checks_at=reached, stopped_at=reached + health.PREPARED_CHANCE_S - 1)
        run("E0603", bot_tests={"ok": True, "line": "ok"}, checks_at=reached, stopped_at=reached + 30)
        self.assertEqual(health.prepared_stalled(self.state, NOW), [])
        run("E0604", checks_at=reached, stopped_at=reached + health.PREPARED_CHANCE_S + 1)  # waited, then the lock freed
        run("E0605", bot_tests={"ok": True, "line": "ok"}, checks_at=reached, stopped_at=reached + 3600)
        run("E0606", checks_at=reached)  # no stop recorded: a driver that died waiting, or a record from before
        self.assertEqual(health.prepared_stalled(self.state, NOW), ["E0604", "E0605", "E0606"])

    # Method M89, from the real records (runs/E0325, E0329, E0319's experiment.json; the reflog's d151b77 at
    # 1790653503), moved into the window: E0329 waited 33 minutes behind E0325 on d151b77 and ran its CPU gate under
    # the lock; E0325 took the lock 7 s after d151b77's promotion (no time to prepare); E0319's gate was prepared.
    def test_an_evaluation_that_waited_behind_another_and_ran_its_gate_unprepared_is_named(self) -> None:
        shift = NOW - 1790656113 - 60

        def run(name: str, coded: float, locked: float, base: str, prepared: bool = False, **session: float) -> None:
            folder = self.state / "runs" / name
            folder.mkdir(parents=True)
            (folder / "experiment.json").write_text(json.dumps({
                "verdict": "discarded", "finished": 1790656113 + shift, "evaluation": {"base": base},
                "cpu": {"ok": True, "line": "CPU PASS", **({"prepared": True} if prepared else {})},
                "session": {"coded_at": coded + shift, "locked_at": locked + shift,
                            **{key: at + shift for key, at in session.items()}}}))

        run("E0325", 1790649276, 1790653510, "d151b77")
        run("E0329", 1790650783, 1790655471, "d151b77")
        run("E0319", 1790641674, 1790644094, "a5e4552", prepared=True)
        # Out of the queue for an hour, waiting out a usage limit on its merge turn: that is no wait unprepared.
        run("E0330", 1790650000, 1790655471, "d151b77", requeued_at=1790655400)
        promoted = {"d151b77": 1790653503 + shift, "a5e4552": 1790636168 + shift}
        self.assertEqual(health.prepared_stalled(self.state, NOW, promoted), ["E0329 (waited 33 min)"])
        self.assertEqual(health.prepared_stalled(self.state, NOW), [])  # no reflog read: the threshold rule alone

    def test_prepared_checks_that_keep_failing_where_evaluates_pass_alert(self) -> None:
        # MISSES 84: E0263, E0265 and E0266's prepared bot tests failed (a flat suite) and evaluate's passed, unalerted.
        # Their records' shape (runs/E####/prepared.json, experiment.json), in the checkout layout.
        def run(name: str, at: float, ok: bool, layout: str = "bot", evaluated: str = "") -> None:
            folder = self.state / "runs" / name
            (folder / "prepared" / layout).mkdir(parents=True, exist_ok=True)
            commit = f"{name}-commit"
            (folder / "prepared.json").write_text(json.dumps({
                "referee": "f1c53e7", "maps_from": "693cc55", "python": "python", "runner": "9b3ddf6",
                "commit": commit, "checks_at": at - 60, "at": at,
                "bot_tests": {"ok": ok, "line": f"bot tests: 29 scenario binaries + 1 protocol suite; {int(not ok)} failed"}}))
            (folder / "experiment.json").write_text(json.dumps({
                "candidate": evaluated or commit, "finished": at + 1500,
                "bot_tests": {"ok": True, "line": "bot tests: 29 scenario binaries + 1 protocol suite; 0 failed"},
                "cpu": {"ok": True, "line": "CPU PASS p50 4.2M p99 6.5M max 8.3M timeouts 0 crashes 0"}}))

        for name, at in (("E0263", 1790595872.3), ("E0265", 1790596868.2), ("E0266", 1790597539.8)):
            run(name, at, ok=False)
        run("E0267", 1790598000.0, ok=True, layout="cand")  # prepared before the fix: not counted
        self.assertEqual(health.prepared_failing(self.state), ["E0263", "E0265", "E0266"])
        self.assertIn("prepared:failing", [a.key for a in health.prepared(self.state, self.repo, NOW)])
        run("E0266", 1790597539.8, ok=False, evaluated="d75fb2b")  # rebased: evaluate checked another commit
        self.assertEqual(health.prepared_failing(self.state), [])
        run("E0266", 1790597539.8, ok=False)
        run("E0268", 1790599000.0, ok=True)  # a newer prepared pass: prepare works
        self.assertEqual(health.prepared_failing(self.state), [])

    def test_a_method_finding_open_for_a_day_alerts(self) -> None:
        path = self.state / "method.md"
        record: dict = {}
        self.assertEqual(health.method(self.state, NOW, record), [])  # the file last changed an hour ago
        keys = [a.key for a in health.method(self.state, NOW + health.METHOD_GRACE, record)]
        # Open as captured: M5 and M9 ("Still open"), M8, M13, and M15 (its heading says done, but it has a Change).
        # "M11, M12, M14 fixed" is closed.
        self.assertEqual(keys, ["method:M5", "method:M8", "method:M9", "method:M15", "method:M13"])
        # Rewording M5 keeps its clock; marking it fixed closes it.
        path.write_text(path.read_text().replace("Mix maps dilute", "Mixed maps dilute"))
        os.utime(path, (NOW + 7200, NOW + 7200))
        self.assertIn("method:M5", [a.key for a in health.method(self.state, NOW + health.METHOD_GRACE, record)])
        path.write_text(path.read_text().replace("## M5. Mixed maps dilute", "## M5 fixed. Mixed maps dilute")
                        .replace("- Still open: eval style", "- Was open: eval style"))
        self.assertNotIn("method:M5", [a.key for a in health.method(self.state, NOW + health.METHOD_GRACE, record)])
        self.assertNotIn("M5", record)

    def test_a_failed_map_ideas_run_or_an_unreviewed_branch_alerts_after_its_grace(self) -> None:
        # mapideas.json as loop/mapideas.py writes it: one run finished with a branch, a later one failed.
        work = self.state / "mapideas/20260925-120000"
        work.mkdir(parents=True)
        git(self.repo, "branch", "mapideas/20260925-120000")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
        git(self.repo, "checkout", "-q", "mapideas/20260925-120000")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "a new style")
        git(self.repo, "checkout", "-q", "main")
        mapideas.save(self.state, {"attempt_at": NOW - 3600, "work": str(work), "pending": ["1548839411465064470"]})
        mapideas.finished(self.state, work, 1, NOW)
        mapideas.save(self.state, {**mapideas.read(self.state), "work": str(self.state / "mapideas/20260926-000000")})
        with patch.object(mapideas, "alert"):
            mapideas.failed(self.state, self.state / "mapideas/20260926-000000", "worker exited 1: out of quota", NOW)
        self.assertEqual(health.map_ideas(self.state, self.repo, NOW + health.MAPIDEAS_GRACE - 60), [])
        failure, = health.map_ideas(self.state, self.repo, NOW + health.MAPIDEAS_GRACE + 60)
        self.assertEqual(failure.key, "mapideas:failed")
        self.assertIn("worker exited 1: out of quota", failure.text)
        later = NOW + health.MAPIDEAS_MERGE_GRACE + 60
        self.assertEqual([a.key for a in health.map_ideas(self.state, self.repo, later)],
                         ["mapideas:failed", "mapideas:mapideas/20260925-120000"])
        git(self.repo, "merge", "-q", "--no-edit", "mapideas/20260925-120000")
        self.assertEqual([a.key for a in health.map_ideas(self.state, self.repo, later)], ["mapideas:failed"])

    def test_a_map_ideas_run_recorded_running_past_its_limit_alerts(self) -> None:
        # mapideas.json as mapideas.tick writes it at a launch.
        (self.state / "mapideas.json").write_text(json.dumps({"attempt_at": NOW, "pending": ["1548839411465064470"],
            "error": None, "failed_at": None, "pid": 4242, "work": str(self.state / "mapideas/20260926-000000")}))
        self.assertEqual(health.map_ideas(self.state, self.repo, NOW + mapideas.STALE - 60), [])
        stale, = health.map_ideas(self.state, self.repo, NOW + mapideas.STALE + 60)
        self.assertEqual(stale.key, "mapideas:stale")
        self.assertIn("still recorded as running (pid 4242), past its 3 h limit", stale.text)

    def test_low_disk_alerts(self) -> None:
        with patch.object(health.shutil, "disk_usage", return_value=Usage(926e9, 912e9, 14e9)):
            (alert,) = health.disk(self.state, NOW)
        self.assertIn("only 14 GB free", alert.text)

    def test_a_stale_or_superseded_game_profile_asks_for_a_new_one(self) -> None:
        self.heal()
        profile = self.state / "game-profile.json"
        profile.write_text(json.dumps({"at": NOW - 8 * 86400, "unswbc": "1.1.0"}))  # before the upgrade to 1.1.0
        self.assertIn("profile:1.1.0", self.keys())
        edit(self.state / "maintenance.json", lambda m: m["active"].update(validated_at=NOW - 9 * 86400))
        self.assertIn("profile:old", self.keys())
        profile.write_text(json.dumps({"at": NOW - 3600, "unswbc": "1.0.2"}))  # made before the upgrade to 1.1.0
        self.assertIn("profile:1.1.0", self.keys())
        profile.unlink()
        self.assertIn("profile:missing", self.keys())

    def test_the_utilisation_record_going_unwritten_while_the_upkeep_runs_alerts(self) -> None:
        self.heal()
        self.assertNotIn("utilisation:stale", self.keys())
        self.assertIn("utilisation:stale", self.keys(now=NOW + 20 * 60))  # the upkeep ran within the hour
        (self.state / "STOP").touch()
        self.assertNotIn("utilisation:stale", self.keys(now=NOW + 20 * 60))

    # The live rows of 12:21-12:40 and 13:55-14:12 on 2026-09-27 (every host idle), written as the record.
    def idle_rows(self, rows: list[dict] | None = None) -> list[dict]:
        sample = Path(__file__).resolve().parent / "testdata/throughput/utilisation-idle-sample.jsonl"
        found = [json.loads(line) for line in sample.read_text().splitlines()]
        (self.state / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in (found if rows is None else rows)))
        return found

    def test_machines_idle_with_no_evaluation_alert_once_a_stretch_with_what_each_filler_is_doing(self) -> None:
        # 12:22-12:35 (a usage limit, then one coder) and 13:55-14:06 (the fillers paused for coders counted as
        # candidates, MISSES 44): no evaluation ran, so health.utilisation stayed quiet.
        self.heal()
        rows = self.idle_rows()
        self.idle_rows(rows[1:1 + throughput.IDLE_MINUTES - 1])  # one minute short
        self.assertEqual(health.idle_any(self.state, self.repo, rows[10]["at"] + 30, {}), [])
        self.idle_rows()
        now = rows[-1]["at"] + 60
        champion = git(self.repo, "rev-parse", "loop/champion")
        paused = next(r for r in rows if r["time"] == "13:56")
        (self.state / "sweep.json").write_text(json.dumps({"champion": champion, "finished_at": paused["at"]}))
        (self.state / "baselines.json").write_text(json.dumps(
            {"champion": champion, "pid": None, "paused": "an evaluation is playing on the hosts",
             "paused_at": paused["at"], "error": "an old failure", "failures": 1, "failed_at": paused["at"] - 3600}))
        (self.state / "advmaps.json").write_text(json.dumps({"champion": "0e962ecb223d8ec55e9cfbda883e3f4a4cfae671"}))
        (self.state / "ideas.jsonl").write_text(json.dumps({"id": "I278", "status": "trying:lane1"}) + "\n"
                                                + json.dumps({"id": "I300", "status": "todo"}) + "\n")
        run = self.state / "runs/E0163"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I278"}, "finished": 0.0}))  # a native coder
        first, second = health.idle_any(self.state, self.repo, now, {})
        self.assertEqual([first.key, second.key], [f"idle:any:{int(r['at'])}" for r in rows if r["time"] in ("12:22", "13:55")])
        self.assertIn("for 14 min (12:22-12:35) with no evaluation running", first.text)
        self.assertIn("for 12 min (13:55-14:06)", second.text)
        for part in ("sweep done for this champion",
                     "prewarm paused 13:56: an evaluation is playing on the hosts",  # its older failure isn't its state
                     "adversarial maps last ran for 0e962ec, not this champion", "late screen nothing owed",
                     "official-map reports 0 pending", "1 ideas being coded, 1 to do"):
            self.assertIn(part, second.text)
        # REFLECT 2026-09-29 14:29 F11: with no filler running, it names each that could start and why it hasn't.
        self.assertIn("No filler is running; could start: prewarm (paused 13:56: an evaluation is playing on the "
                      "hosts; its step restarts it once that no longer holds); adversarial maps (last ran for 0e962ec, "
                      "not this champion: its upkeep step hasn't run since this champion arrived", second.text)
        self.assertNotIn("sweep (", second.text)  # done for this champion
        (self.state / "sweep.json").write_text(json.dumps({"champion": champion, "pid": os.getpid()}))
        self.assertNotIn("could start", health.idle_any(self.state, self.repo, now, {})[1].text)  # one is running
        (self.state / "sweep.json").write_text(json.dumps({"champion": champion, "finished_at": paused["at"]}))
        # The search's step also waits, past its first GAMES_CAP games, for nothing else to want the machines.
        (self.state / "advmaps.json").write_text(json.dumps({"champion": champion, "idle_at": paused["at"]}))
        self.assertIn("adversarial maps (not started: its step starts it at the next upkeep tick with the machines "
                      f"idle and, past its first {health.advmaps.GAMES_CAP} games, nothing else wanting them "
                      "(advmaps.wanted) (last found the machines idle",
                      health.idle_any(self.state, self.repo, now, {})[1].text)
        # A stretch over 2 h old has had its alert.
        self.assertEqual(len(health.idle_any(self.state, self.repo, rows[15]["at"] + health.IDLE_RECENT + 60, {})), 1)
        (self.state / "STOP").touch()
        self.assertEqual(health.idle_any(self.state, self.repo, now, {}), [])

    def test_a_second_idle_stretch_is_not_held_back_by_the_first(self) -> None:
        # With one fixed key, 13:55's stretch waited behind 12:22's alert for the 3 h repeat (until ~16:32).
        self.heal()
        rows = self.idle_rows()
        self.idle_rows(rows[:20])
        lines = [t for t in health.run(self.state, self.repo, rows[19]["at"] + 60) if "Find why nothing played" in t]
        self.assertEqual(len(lines), 1)
        self.idle_rows()
        lines = [t for t in health.run(self.state, self.repo, rows[-1]["at"] + 60) if "Find why nothing played" in t]
        self.assertEqual(len(lines), 1)
        self.assertIn("(13:55-14:06)", lines[0])

    def test_an_idle_stretch_the_evaluation_check_reports_is_not_reported_again(self) -> None:
        # 01:33-02:04 on 2026-09-27: one idle stretch across E0134's SPRT and screen, which the evaluation check
        # reports (as the screen's 01:37-02:00); matching exact start and end reported it here too.
        self.heal()
        rows = self.idle_rows()
        during = [{**r, "evaluation": {"experiment": "E0134", "stage": "SPRT" if r["time"] < "14:00" else "screen"}}
                  for r in rows[20:]]
        self.idle_rows(during)
        self.assertTrue(throughput.idle_spells(during))  # the evaluation check's
        self.assertEqual(health.idle_any(self.state, self.repo, rows[-1]["at"] + 60, {}), [])

    def test_machines_idle_with_nothing_left_to_play_alert_once_per_champion(self) -> None:
        self.heal()
        rows = self.idle_rows()
        champion = git(self.repo, "rev-parse", "loop/champion")
        for name in ("sweep.json", "baselines.json", "advmaps.json"):
            (self.state / name).write_text(json.dumps({"champion": champion, "finished_at": rows[0]["at"]}))
        (self.state / "ideas.jsonl").write_text(json.dumps({"id": "I278", "status": "followup"}) + "\n")
        seen: dict = {}
        (alert,) = health.idle_any(self.state, self.repo, rows[-1]["at"] + 60, seen)
        self.assertEqual(alert.key, f"idle:nothing:{champion}")
        self.assertIn("nothing left to play", alert.text)
        self.assertEqual(health.idle_any(self.state, self.repo, rows[-1]["at"] + 60, seen), [])  # not every stretch

    def test_finished_worktrees_and_wip_branches_left_behind_alert(self) -> None:
        # Twelve finished agents' worktrees were left behind on 2026-09-27, found by eye.
        self.heal()
        now = time.time()
        root = self.repo.parent
        for name, *args in (("done", "--detach"), ("fresh", "--detach"), ("dirty", "--detach"), ("ahead", "-b", "ahead"),
                            ("agent", "--detach"), ("outputs", "--detach")):
            git(self.repo, "worktree", "add", "-q", *args, str(root / name), "main")
        (root / "dirty/notes.txt").write_text("unsaved work\n")
        (root / "ahead/bot/main.cpp").write_text("// a change main lacks\n")
        git(root / "ahead", "commit", "-qam", "unmerged")
        git(self.repo, "worktree", "lock", "--reason", "claude agent agent-1 (pid 1)", str(root / "agent"))  # running
        exclude = Path(git(root / "outputs", "rev-parse", "--git-common-dir")) / "info/exclude"  # a .gitignore would be dirty
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text(exclude.read_text() + "data/\n" if exclude.exists() else "data/\n")
        (root / "outputs/data").mkdir()
        (root / "outputs/data/games.jsonl").write_text("{}\n")
        for name in ("done", "dirty", "ahead", "agent", "outputs"):  # untouched for 3 hours
            folder = Path(git(root / name, "rev-parse", "--absolute-git-dir"))
            for file in (folder / "HEAD", folder / "logs/HEAD"):
                if file.exists():
                    os.utime(file, (now - 3 * 3600, now - 3 * 3600))
        for exp, idea in (("E0160", "I278"), ("E0163", "I314")):
            git(self.repo, "branch", f"wip/{exp}", "main")
            (self.state / "runs" / exp).mkdir(parents=True, exist_ok=True)
            (self.state / "runs" / exp / "experiment.json").write_text(json.dumps({"idea": {"id": idea}}))
        (self.state / "ideas.jsonl").write_text(json.dumps({"id": "I278", "status": "followup"}) + "\n"
                                                + json.dumps({"id": "I314", "status": "todo"}) + "\n")
        found = {a.key: a.text for a in health.leftovers(self.state, self.repo, now)}
        self.assertEqual(set(found), {"stale worktrees", "wip branches"})
        self.assertIn("2 worktrees are clean", found["stale worktrees"])
        self.assertIn(str(root / "done") + " (", found["stale worktrees"])  # not fresh, dirty, ahead or locked agent
        self.assertNotIn(str(root / "agent"), found["stale worktrees"])
        self.assertIn(f"{root / 'outputs'} (", found["stale worktrees"])
        self.assertIn("holding ignored data/", found["stale worktrees"])  # which git worktree remove would delete
        self.assertIn("wip/E0160 (I278 is followup)", found["wip branches"])
        self.assertNotIn("wip/E0163", found["wip branches"])  # its idea waits, however long
        later = {a.key: a.text for a in health.leftovers(self.state, self.repo, now + 30 * 86400)}
        self.assertNotIn("wip/E0163", later["wip branches"])

    def test_a_fetch_quarantine_a_crash_left_alerts_but_a_running_fetchs_does_not(self) -> None:
        # Fix round 4: a crash inside publishing.fetch_checked leaves its fetch-quarantine-* repo in main's .git.
        import fcntl
        import publishing
        now = time.time()
        common = Path(git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        self.assertEqual(health.fetch_quarantines(self.repo, now), [])
        crashed, running, fresh = (common / f"{publishing.QUARANTINE_PREFIX}{n}" for n in ("a", "b", "c"))
        for folder in (crashed, running, fresh):
            folder.mkdir()
            self.addCleanup(shutil.rmtree, folder, True)
        old = now - publishing.QUARANTINE_STALE - 60
        for folder in (crashed, running):
            os.utime(folder, (old, old))
        held = os.open(running, os.O_RDONLY)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        (alert,) = health.fetch_quarantines(self.repo, now)
        self.assertIn(f"1 fetch quarantine repos older than {publishing.QUARANTINE_STALE // 60} min", alert.text)
        self.assertIn(str(crashed), alert.text)
        self.assertIn("safe to delete (rm -rf <path>)", alert.text)
        self.assertNotIn(str(running), alert.text)
        self.assertNotIn(str(fresh), alert.text)

    def test_the_loop_home_growing_over_a_gigabyte_a_day_alerts_once_per_measurement(self) -> None:
        self.heal()
        record: dict = {}
        self.assertNotIn("home-size", " ".join(self.keys(record=record)))  # the first measurement
        measured = record["sizes"][-1]
        record["sizes"][-1] = {**measured, "at": NOW - 86400, "total_kb": measured["total_kb"] - 3 * 1024 ** 2}
        (alert,) = [a for a in health.home_size(self.state, NOW, record) if a.key.startswith("home-size")]
        self.assertIn("grew 3.0 GB a day", alert.text)
        self.assertEqual(len(record["sizes"]), 2)
        self.assertEqual(health.home_size(self.state, NOW + 60, record)[0].key, alert.key)  # the same measurement

    def test_a_failed_size_measurement_records_nothing_and_alerts_as_a_broken_check(self) -> None:
        self.heal()
        record: dict = {}
        failed = subprocess.CompletedProcess(["du"], 1, "", "du: permission denied")
        with patch.object(health.subprocess, "run", return_value=failed):
            self.assertRaises(RuntimeError, health.home_size, self.state, NOW, record)
            self.assertIn("broken:loop home size", self.keys(record=record))
        self.assertEqual(record.get("sizes"), [])
        # Exit 1 with output: an entry vanished during the walk; what it measured is kept.
        partly = subprocess.CompletedProcess(["du"], 1, f"2048\t{self.state / 'runs'}\n", "du: no such file")
        with patch.object(health.subprocess, "run", return_value=partly):
            health.home_size(self.state, NOW, record)
        self.assertEqual(record["sizes"][-1]["kb"], {"runs": 2048})

    def test_a_check_that_raises_is_an_alert(self) -> None:
        self.heal()
        (self.state / "referee/.git").unlink()
        self.assertIn("broken:referee copy", self.keys())


# E0349 was reported waiting with no queue ticket on 29 Sep 18:42-18:52 (it held the lock: utilisation.jsonl). A
# candidate past coding with neither a ticket nor the lock would wait for good: it alerts, naming what it was doing.
class Ticketless(unittest.TestCase):
    def test_a_candidate_with_no_ticket_that_doesnt_hold_the_lock_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            run = state / "runs/E0349"
            run.mkdir(parents=True)
            (run / "worker.json").write_text(json.dumps({"experiment": "E0349", "status": "finished",
                                                         "driver_pid": os.getpid(), "merge": "conflict"}))
            (run / "experiment.json").write_text(json.dumps({"id": "E0349", "lane": 3, "session": {"merge": "conflict"}}))
            now = time.time() + health.TICKETLESS_S + 60
            (alert,) = health.ticketless(state, now)
            self.assertEqual(alert.key, "ticketless:E0349")
            self.assertIn("merge conflict", alert.text)
            seen: dict = {}  # health.json's record: only once seen so for TICKETLESS_S (a lock just released)
            self.assertEqual(health.ticketless(state, now, seen), [])
            self.assertEqual(len(health.ticketless(state, now + health.TICKETLESS_S, seen)), 1)
            marker = evaluating_marker()
            marker.write_text(json.dumps({"pid": os.getpid(), "since": time.time(), "lane": 3}))
            self.addCleanup(marker.unlink, missing_ok=True)
            self.assertEqual(health.ticketless(state, now), [])  # it holds the lock
            marker.write_text(json.dumps({"pid": os.getpid(), "since": time.time(), "lane": 1}))  # another lane does
            self.assertEqual(len(health.ticketless(state, now)), 1)
            (state / meta.QUEUE).mkdir()
            (state / meta.QUEUE / "1-0001790000000.000-E0349.json").write_text("{}")
            self.assertEqual(health.ticketless(state, now), [])  # in line
            (state / meta.QUEUE / "1-0001790000000.000-E0349.json").unlink()
            (run / "experiment.json").write_text(json.dumps({"id": "E0349", "finished": 1.0}))
            self.assertEqual(health.ticketless(state, now), [])  # recorded


# 29 Sep: coders' and merge turns' bot tests waited 57 min in their sandboxes, where gated_test.sh can't measure the
# CPU split and, with none published (no loop game gated here), fell back to the 1-minute load, niced games included.
class GateFallback(unittest.TestCase):
    def test_a_long_gate_wait_with_no_published_split_alerts(self) -> None:
        now = time.time()
        rows = [health.PsRow(501, 1, "u", 0, 700.0, 0.0, "bash /r/tools/gated_test.sh python -m unittest x"),
                health.PsRow(502, 501, "u", 0, 5.0, 0.0, "sleep 20")]
        with patch.object(health.home, "published", return_value=(None, -float("inf"))):
            (alert,) = health.gate_fallback(now, rows)
            self.assertIn("pid 501, 12 min", alert.text)
            self.assertEqual(health.gate_fallback(now, [dataclasses.replace(rows[0], alive=60.0)]), [])  # not long yet
        with patch.object(health.home, "published", return_value=(None, now - 60)):  # a sampler published lately
            self.assertEqual(health.gate_fallback(now, rows), [])


class RuleChecks(unittest.TestCase):
    # sig 2026-09-28: every rejection gets the orchestrator's own rule check. E0209's real discard (a formula kept
    # only on H1 then) alerts until a decisions block naming it has a "Rule check:" line; kept results and ones
    # recorded before the rule never do.
    def test_a_rejection_alerts_until_its_rule_check_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            late = health.RULE_CHECK_SINCE + 60
            now = late + health.DECIDE_GRACE + 60
            # E0245's promising row is superseded by its rejudged keep (driver.rejudge): only an id's latest row counts.
            rows = [{"id": "E0209", "verdict": "discarded", "finished": late},
                    {"id": "E0204", "verdict": "kept", "finished": late},
                    {"id": "E0207", "verdict": "discarded", "finished": health.RULE_CHECK_SINCE - 60},
                    {"id": "E0245", "verdict": "promising", "finished": late},
                    {"id": "E0245", "verdict": "kept", "finished": late, "rejudged": {"of": "promising (...)"}}]
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            (state / "decisions.md").write_text(
                "## Orchestrator's decisions on E0209 (I098) result (2026-09-28 03:40)\n- keep (discarded): recorded\n")
            alerts = health.unchecked_rejections(state, now)
            self.assertEqual([a.key for a in alerts], ["rule-check"])
            self.assertIn("E0209", alerts[0].text)
            self.assertNotIn("E0204", alerts[0].text)
            self.assertNotIn("E0207", alerts[0].text)
            self.assertNotIn("E0245", alerts[0].text)
            with (state / "decisions.md").open("a") as handle:
                handle.write("\n## Orchestrator's decisions on E0209 rule check (2026-09-28 03:55)\n"
                             "- Rule check: the no-loss test lacked the probability rule; changed (fec7e56), retest queued\n")
            self.assertEqual(health.unchecked_rejections(state, now), [])


class OwedStarts(unittest.TestCase):
    # REFLECT 2026-09-29 14:29 F6: owed.md's real lines (trimmed) from before the rule stay quiet; a dated item added
    # after it alerts until it says when it starts, now by whom or what it waits on; a decision, answer or correction
    # isn't an item.
    def test_a_new_owed_item_says_when_it_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            old = ("# Owed loop builds (the orchestrator's list; moved from the session scratchpad 2026-09-28)\n"
                   "- (29 Sep 13:49) M86: rulecounts row counting the two-king loss; give to rulecounts after "
                   "orch/online-data merges.\n"
                   "- 2026-09-29 14:36 orphans of limit-killed coders at 0% CPU and holding no host slots.\n"
                   "- DONE 04:35: I569 (E0209 retest), I579 (E0207 targeted retest).\n")
            owed = state / "owed.md"
            owed.write_text(old)
            now = health.OWED_STARTS_SINCE.timestamp() + 3600
            self.assertEqual(health.owed_starts(state, now), [])
            owed.write_text(old + "- (29 Sep 15:10) Filler gaps: each filler keeps its next batch prepared.\n"
                            "- (2026-09-29 15:12) Fit rows from online games. Waits on I943: the ladder map variants "
                            "aren't saved yet.\n"
                            "- 2026-09-29 15:14 A pre-push hook. Starts: now (orch/reflect-1429 builder)\n"
                            "- (29 Sep 15:15) DONE: the owed.md health line (orch/reflect-1429).\n"
                            # Dated notes that are no work item, owed.md's own lines of 02:23, 02:47, 09:00 and 10:39
                            # (trimmed, dated after the rule): they pass.
                            "- (29 Sep 15:20) Decided: MISSES 84 first-firing reminder satisfied by E0279.\n"
                            "- (29 Sep 15:21) Decided alerts: (a) Portals MAP RECORD (26-0-22 v 296-0-93).\n"
                            "- (29 Sep 15:22) Correction to the 08:44 line: runs/E0311/cpu.json now reads hosts "
                            "{here: 9, mm: 4}, so the gate did use mm.\n"
                            "- (29 Sep 15:23) Answered the second half of the line above: finish_recorded runs before "
                            "free_lane in claim_next.\n")
            (alert,) = health.owed_starts(state, now)
            self.assertIn("1 items", alert.text)
            self.assertIn("(29 Sep 15:10) Filler gaps", alert.text)


class OwedCleared(unittest.TestCase):
    # owed.md's 16:41 and 17:12 lines of 29 Sep (trimmed) waited on orch/method-88 after it merged (REFLECT 2026-09-29
    # 20:42 F2): an item waiting on a merged branch alerts; one on an open branch, or marked DONE, doesn't.
    def test_an_item_waiting_on_a_merged_branch_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "owed.md").write_text(
                "- (29 Sep 16:41) Next loop builder, after orch/method-88 merges (it touches rulecounts): M90.\n"
                "- (29 Sep 17:12) Evaluation queue kind rank. Waits on orch/method-88: the next loop builder.\n"
                "- (29 Sep 17:20) Screen ports. Waits on orch/ports-and-share: it edits the same templates.\n"
                "- DONE (29 Sep 18:00) testhome guard. Waits on orch/method-88: merged.\n")
            self.assertEqual(health.owed_cleared(state, {"orch/other"}), [])
            (alert,) = health.owed_cleared(state, {"orch/method-88", "orch/other"})
            self.assertIn("2 items", alert.text)
            self.assertNotIn("Screen ports", alert.text)

    # testdata/owed-waits-sample.txt: every owed.md line naming an orch/ branch at 21:20 on 29 Sep, all of whose
    # branches main had merged. Only waits count, and only once every branch waited on has merged: not "LOOP.md is at
    # 119,763 of 120,000 bytes after orch/worldgen:", nor a DONE line, nor the "## After orch/fit-tool merges" heading.
    def test_on_the_live_list_only_waits_on_merged_branches_alert(self) -> None:
        sample = (Path(__file__).resolve().parent / "testdata/owed-waits-sample.txt").read_text()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "owed.md").write_text(sample)
            (alert,) = health.owed_cleared(state, {"orch/worldgen"})
            self.assertIn("1 items", alert.text)
            self.assertIn("DEFAULT WORLDGEN MIX", alert.text)
            self.assertEqual(health.owed_cleared(state, {"orch/method-2047", "orch/fit-tool"}), [])
            (alert,) = health.owed_cleared(state, {"orch/worldgen", "orch/fit-tool"})
            self.assertIn("2 items", alert.text)
            (alert,) = health.owed_cleared(state, {"orch/method-88"})
            self.assertIn("5 items", alert.text)

    # Only main's first-parent merges of an orch/ branch count, not a merge into another branch.
    def test_merged_branches_reads_mains_merge_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            unsigned = ("-c", "commit.gpgsign=false")
            git(repo, "init", "-q", "-b", "main")
            git(repo, *unsigned, "commit", "-q", "--allow-empty", "-m", "first")
            for branch in ("orch/method-88", "orch/open"):
                git(repo, "checkout", "-q", "-b", branch, "main")
                git(repo, *unsigned, "commit", "-q", "--allow-empty", "-m", branch)
            git(repo, "checkout", "-q", "-b", "orch/side", "main")
            git(repo, *unsigned, "merge", "-q", "--no-ff", "-m", "Merge orch/open: into a side branch", "orch/open")
            git(repo, "checkout", "-q", "main")
            git(repo, *unsigned, "merge", "-q", "--no-ff", "-m", "Merge orch/method-88: items", "orch/method-88")
            self.assertEqual(health.merged_branches(repo), {"orch/method-88"})


class BcevalFailing(unittest.TestCase):
    # A bceval that fails every game (a broken venv, a changed model schema) would leave the turning-point tables empty
    # with no word: the live version's latest BCEVAL_FAILING readings all errors alert; one reading since that works
    # quiets it.
    def test_the_live_versions_latest_readings_all_failing_alert(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            sync = online.bceval_module().sync_module()
            sync.save_state(state, {"approved": sync.PINNED})
            (state / "online.json").write_text(json.dumps({"submission_id": 7}))
            (state / "online-replays/7").mkdir(parents=True)
            failed = {"v": "x", "error": "BcevalError: exit 1", "tries": 1}
            games = [{"id": 1, "bceval": {"v": "x", "turn": None}}] + [{"id": i, "bceval": failed} for i in range(2, 7)]
            (state / "online-replays/7/index.json").write_text(json.dumps({"games": games}))
            self.assertEqual([a.key for a in health.bceval(state, NOW)], ["bceval failing:7"])
            games.append({"id": 7, "bceval": {"v": "x", "turn": None}})
            (state / "online-replays/7/index.json").write_text(json.dumps({"games": games}))
            self.assertEqual(health.bceval(state, NOW), [])


class BatchStarts(unittest.TestCase):
    # The live rows of 03:41-03:46 and 04:06-04:09 on 2026-09-27: E0143's SPRT began 03:43 with this Mac full and
    # played nothing for two rows with ~50 remote slots free (the Mac wait before a batch, MISSES 53); E0142's began
    # 04:08 with this Mac full and 15 games on the hosts in its first row. 04:06 follows a 20-minute gap: no start.
    def test_a_stage_that_waits_before_its_first_game_alerts_and_the_first_full_mac_start_reports_once(self) -> None:
        sample = (Path(__file__).parent / "testdata/throughput/utilisation-batch-starts-sample.jsonl").read_text()
        rows = [json.loads(line) for line in sample.splitlines()]
        at = {row["time"]: row["at"] for row in rows}
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / throughput.LOG).write_text(sample)
            now = rows[-1]["at"] + 60
            seen = {"since": at["03:41"]}
            found = health.batch_starts(state, now, seen)
            self.assertEqual([a.key for a in found], ["batch-start:E0143:SPRT", "batch-start:first"])
            self.assertIn("E0143's SPRT began 03:43 and played no game anywhere for 2 min", found[0].text)
            self.assertIn("E0143 at 03:43; none of its games on the hosts in its first 2 rows", found[1].text)
            self.assertEqual([a.key for a in health.batch_starts(state, now, seen)], ["batch-start:E0143:SPRT"])
            later = {"since": at["04:07"]}  # the check first ran after E0143's start
            found = health.batch_starts(state, now, later)
            self.assertEqual(found[-1].key, "batch-start:first")
            self.assertIn("E0142 at 04:08; its games began on the hosts in its first 2 rows (15 there), as the fix expects", found[-1].text)
            self.assertEqual(later["first"]["experiment"], "E0142")
            # One row without games is a stage's setup, not a wait; and nothing older than REPEAT alerts.
            (state / throughput.LOG).write_text("".join(line + "\n" for line in sample.splitlines()
                                                        if json.loads(line)["time"] != "03:44"))
            self.assertEqual([a.key for a in health.batch_starts(state, now, {"first": {}})], [])
            self.assertEqual(health.batch_starts(state, now + health.REPEAT, {"first": {}}), [])

    def test_the_first_full_mac_start_reports_only_once_the_record_holds_its_first_rows(self) -> None:
        # 9 of 25 SPRT and screen starts had no game anywhere in their first row (setup): E0142 with its 04:08 row's
        # host games gone still began on the hosts at 04:09, and with only its first row recorded, nothing is said yet.
        rows = [json.loads(line) for line in
                (Path(__file__).parent / "testdata/throughput/utilisation-batch-starts-sample.jsonl").read_text()
                .splitlines()]
        for name, host in next(r for r in rows if r["time"] == "04:08")["hosts"].items():
            if name != "here" and host.get("by"):
                host["by"].pop("evaluation", None)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            now = rows[-1]["at"] + 60
            (state / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))
            seen = {"since": rows[-3]["at"]}
            found = health.batch_starts(state, now, seen)
            self.assertIn("E0142 at 04:08; its games began on the hosts in its first 2 rows", found[-1].text)
            (state / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in rows if r["time"] != "04:09"))
            seen = {"since": rows[-3]["at"]}
            self.assertEqual([a.key for a in health.batch_starts(state, now, seen)], ["batch-start:E0143:SPRT"])
            self.assertNotIn("first", seen)

    def test_a_twin_row_is_not_a_second_minute_of_stall(self) -> None:
        # The live rows of 19:54-19:58 on 2026-09-27: E0178's SPRT began 19:56 with no game yet (its setup) and had 61
        # games in flight at 19:57, but its 19:56 row was written twice (throughput.sample), which alerted a 2-minute
        # stall at 20:16:59.
        sample = (Path(__file__).parent / "testdata/throughput/utilisation-twin-rows-sample.jsonl").read_text()
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / throughput.LOG).write_text(sample)
            now = json.loads(sample.splitlines()[-1])["at"] + 60
            self.assertEqual(health.batch_starts(state, now, {"first": {}}), [])


class TypedTimes(unittest.TestCase):
    # REFLECT 2011 F12: placeholder times ("17:2x") went into the decisions record, which --check-times doesn't read.
    # On 2026-09-27 the live records held older ones in all three (20:0x and 20:1x in decisions.md's bodies, 15:5x,
    # 15:3x, 16:4x in digests.md's decisions, 15:2x, 17:1x and 20:5x in idea notes): the first run keeps what it
    # finds, so only new ones alert. decisions.md is read in "## " blocks: its placeholders were in body lines.
    def test_a_new_placeholder_in_an_orchestrator_record_alerts_and_old_ones_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "discord").mkdir()
            (state / "decisions.md").write_text("## Orchestrator's decisions on E0170 (2026-09-27 17:2x)\n- D1 acted\n")
            (state / "discord/digests.md").write_text(
                "## 2026-09-27 16:14 decisions (orchestrator): the 15:29 read\n- our saved-data check (15:3x) found\n"
                "## Digest 2026-09-27 16:19\n- a worker's report at 16:1x\n")
            ideas = [{"id": "I286", "history": ["orchestrator 2026-09-27 15:31: correction to my 15:2x note",
                                                "review 2026-09-27: at 15:4x"]}]
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            seen: dict = {}
            self.assertEqual(health.typed_times(state, seen), [])  # the first run: all three are old
            self.assertEqual(len(seen["old"]), 3)
            self.assertEqual(health.typed_times(state, seen), [])
            with (state / "decisions.md").open("a") as handle:
                handle.write('## Orchestrator\'s decisions on 20:30 alerts (2026-09-27 20:3x)\n'
                             '## Orchestrator\'s decisions on "17:2x"-style times (2026-09-27 20:41)\n'
                             '- F14: acted. Told sig at 20:1x with the slot cost.\n')
            ideas[0]["history"].append("orchestrator 2026-09-27 20:41: queued at 20:4x")
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            found = health.typed_times(state, seen)
            self.assertEqual(sorted(a.text.split(" has ")[0] for a in found),
                             ["I286's note", "decisions.md", "decisions.md"])
            self.assertEqual(sorted(a.key.split(":", 2)[2] for a in found), ["20:1x", "20:3x", "20:4x"])
            # REFLECT 2026-09-29 20:42 F7, F8: reminders and owed.md lines, and a guessed time of sig's message, count
            # from here on; the lines there at the first run with them don't.
            del seen["old-2"]  # a record from before them
            (state / "owed.md").write_text("- (29 Sep 19:01) Usage cut; sig's message came at about 19:02\n")
            (state / "reminders.jsonl").write_text(json.dumps({"due": "2026-09-29T22:05+10:00", "text": "at 19:0x"})
                                                   + "\n")
            self.assertEqual(health.typed_times(state, seen), [])
            self.assertEqual(len(seen["old-2"]), 2)
            with (state / "owed.md").open("a") as handle:
                handle.write("- (29 Sep 20:14) sig asked at about 20:10 (minute not logged)\n")
            with (state / "reminders.jsonl").open("a") as handle:
                handle.write(json.dumps({"due": "2026-09-30T09:10+10:00", "text": "first firing, due 09:1x"}) + "\n")
            new = sorted(a.key.split(":", 2)[2] for a in health.typed_times(state, seen))
            self.assertEqual(new, ["09:1x", "20:1x", "20:3x", "20:4x", "about 20", "not logged"])


class MapSplits(unittest.TestCase):
    # The three real results in testdata/map-splits-sample.jsonl (E0068 promising with near_default 0.35 over 75
    # trials; E0125 and E0132 kept, no bucket past the bar), plus E0132 again with a size band far below level.
    def test_a_kept_or_promising_result_clearly_worse_on_a_map_bucket_alerts_once(self) -> None:
        rows = (Path(__file__).parent / "testdata/map-splits-sample.jsonl").read_text().splitlines()
        worse = json.loads(rows[2])
        worse.update(id="E0199")
        worse["sprt"]["per_area"]["<=1024"]["score"] = 0.40
        discarded = dict(worse, id="E0200", verdict="discarded")
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "results.jsonl").write_text("\n".join(rows + [json.dumps(worse), json.dumps(discarded)]) + "\n")
            seen: list = []
            found = health.map_splits(state, seen)
            self.assertEqual([a.key for a in found], ["map split:E0068", "map split:E0199"])
            self.assertIn("E0199 (kept) is clearly worse than the champion on <=1024 tiles 0.40 over 216 trials",
                          found[1].text)
            self.assertIn("over its 23 map buckets", found[1].text)
            self.assertIn("not a gate", found[1].text)
            self.assertEqual(health.map_splits(state, seen), [])  # once per result
            self.assertEqual(seen, ["E0068", "E0199"])


class HostOutages(unittest.TestCase):
    # REFLECT 2026-09-30 15:13 F15: mm1 stayed down until sig said "try mm1 again". The live remote.json's first four
    # hosts (zz-wsl part-time); an outage past one evaluation alerts, a shorter one or a part-time host's doesn't.
    def test_a_host_down_longer_than_one_evaluation_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, now = Path(folder), 1790745600.0
            (state / "remote.json").write_text(json.dumps({"hosts": [
                {"name": "mm", "max": 20}, {"name": "mm1", "max": 20}, {"name": "ll", "max": 24},
                {"name": "zz-wsl", "max": 32, "shared": True, "connect_timeout": 30, "part_time": True}]}))
            (state / "host-outages.json").write_text(json.dumps({"mm1": now - 5500, "ll": now - 600,
                                                                  "zz-wsl": now - 9000}))
            with patch.object(health, "recorded_evaluation", return_value=5400):
                (alert,) = health.host_outages(state, now)
            self.assertIn("HOST DOWN: mm1 has been down since", alert.text)
            self.assertIn("longer than one evaluation (90 min); retry it now", alert.text)
            (state / "host-outages.json").write_text("{}")  # none down: results.jsonl isn't read
            with patch.object(health, "recorded_evaluation", side_effect=AssertionError("read")):
                self.assertEqual(health.host_outages(state, now), [])


class RankingAndTurnWorkers(unittest.TestCase):
    # ranking.json (driver.set_ranking) and a post-mortem's worker file (driver.run_postmortem) are written by the
    # loop, so each has a check (CLAUDE.md): unreadable, a ranking with nothing left to do, a turn whose driver died.
    def test_a_bad_or_spent_ranking_and_a_bad_or_orphaned_turn_worker_alert(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, now = Path(folder), 1790745600.0
            self.assertEqual(health.ranking(state), [])  # none recorded
            (state / "ranking.json").write_text('{"order": "I970"}')
            self.assertEqual([a.key for a in health.ranking(state)], ["ranking:malformed"])
            (state / "ranking.json").write_text(json.dumps({"at": now, "source": "sig, 30 Sep", "order": ["I970", "I1"]}))
            (state / "ideas.jsonl").write_text('{"id": "I970", "status": "done"}\n{"id": "I1", "status": "todo"}\n')
            self.assertEqual(health.ranking(state), [])
            (state / "ideas.jsonl").write_text('{"id": "I970", "status": "done"}\n{"id": "I1", "status": "trying:lane2"}\n')
            (alert,) = health.ranking(state)
            self.assertIn("none of the 2 ideas in sig's ranking (sig, 30 Sep) is still to do", alert.text)

            run = state / "runs/E0412"
            run.mkdir(parents=True)
            worker = {"experiment": "E0412", "turn": "postmortem", "name": "bc-E0412-postmortem-8406d943",
                      "session_id": "8406d943-dd71", "started": now - 600, "driver_pid": os.getpid()}
            (run / "postmortem-worker.json").write_text(json.dumps(worker))
            self.assertEqual(health.turn_workers(state, now), [])  # its driver still runs it
            (run / "postmortem-worker.json").write_text(json.dumps({**worker, "driver_pid": 0}))
            self.assertEqual([a.key for a in health.turn_workers(state, now)], ["turn worker:dead:E0412:postmortem"])
            (run / "postmortem.stderr").write_text("")  # the turn ended
            self.assertEqual(health.turn_workers(state, now), [])
            (run / "postmortem-worker.json").write_text(json.dumps({**worker, "name": "e0412-01"}))
            self.assertEqual([a.key for a in health.turn_workers(state, now)],
                             ["turn worker:malformed:E0412:postmortem"])


class SlowSprt(unittest.TestCase):
    # REFLECT 2026-09-30 15:13 F3: the real games, mean tiles and minutes of E0407, E0409 and E0414 (376k, 434k and
    # 296k tile-games a minute), and E0412's while this Mac sat at load 71-82 (106k): under half their median.
    def test_an_sprt_under_half_the_recent_pace_for_its_maps_alerts_once_it_holds(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state, now = Path(folder), 1790743563.0
            births = {}
            for name, games, tiles, minutes, ended in (("E0407", 9140, 1295, 31, now - 9000),
                                                        ("E0409", 6828, 1609, 25, now - 7000),
                                                        ("E0414", 5548, 1320, 25, now - 5000),
                                                        ("E0412", 3638, 1101, 38, now - 60)):
                run = state / "runs" / name
                run.mkdir(parents=True)
                path = run / "sprt-progress.json"
                path.write_text(json.dumps({"updated": ended, "trials": 600, "games": games, "tiles": tiles}))
                os.utime(path, (ended, ended))
                births[path] = ended - minutes * 60
                if name != "E0412":  # finished: the report is written at the end
                    (run / "sprt.json").write_text("{}")
                    os.utime(run / "sprt.json", (ended + 5, ended + 5))
            seen: dict = {}
            with patch.object(health.throughput, "created", births.__getitem__):
                self.assertEqual(health.slow_sprt(state, now, seen), [])  # not yet held PACE_HOLD
                live, later = state / "runs/E0412/sprt-progress.json", now + health.PACE_HOLD
                os.utime(live, (later - 60, later - 60))  # 48 minutes in, no faster
                (alert,) = health.slow_sprt(state, later, seen)
                self.assertIn("E0412 plays 76 games/min on maps of 1101 tiles on average, 83k tile-games/min, under "
                              "50% of the median 382k of the last 3 SPRTs (E0407 382k, E0409 439k, E0414 293k)",
                              alert.text)
                live.write_text(json.dumps({"games": 12000, "tiles": 1101}))
                os.utime(live, (later - 60, later - 60))
                self.assertEqual(health.slow_sprt(state, later, seen), [])  # back to pace


class StyleStreaks(unittest.TestCase):
    # REFLECT 2026-09-30 15:13 F24: E0400, E0401 and E0402's real rows (per_style, per_area) all read worse on
    # near_big_empty; E0403, finished after them, reads it better, which ends the streak.
    def test_a_bucket_worse_in_consecutive_results_alerts_once(self) -> None:
        rows = (Path(__file__).parent / "testdata/style-streaks-sample.jsonl").read_text().splitlines()
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            for line in rows:
                (state / "runs" / json.loads(line)["id"]).mkdir(parents=True)
                (state / "runs" / json.loads(line)["id"] / "sprt.json").write_text("{}")
            (state / "results.jsonl").write_text("\n".join(rows[:3]) + "\n")
            seen: list = []
            found = {a.key: a for a in health.style_streaks(state, seen)}
            alert = found["style streak:near_big_empty:E0402"]
            self.assertIn("near_big_empty read worse than the champion in each of the last 3 results that measured it "
                          "(E0402 0.", alert.text)
            self.assertIn("E0400 0.", alert.text)
            self.assertEqual(health.style_streaks(state, seen), [])  # once per bucket and newest result
            (state / "results.jsonl").write_text("\n".join(rows) + "\n")
            self.assertNotIn("style streak:near_big_empty:E0403", {a.key for a in health.style_streaks(state, seen)})


class TargetBatches(unittest.TestCase):
    # A target batch that read too few of its replays was judged on too little, and one that left its replays behind
    # holds up to 15 MB a game: the first real firing of the batch says so.
    def test_a_batch_that_read_too_few_replays_or_kept_them_alerts(self) -> None:
        def row(eid: str, played: int, unreadable: int) -> dict:
            return {"id": eid, "idea": {"kind": "targeted"}, "verdict": "promising",
                    "target": {"played": played, "unreadable": unreadable, "line": "pocket_deaths ..."}}

        rows = [row("E0301", 288, 50), row("E0302", 288, 60), row("E0303", 0, 0),  # 238 read, 228 read, none played
                {"id": "E0304", "idea": {"kind": "formula"}, "verdict": "kept", "sprt": {}}]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            (state / "runs/E0301/target-replays").mkdir(parents=True)
            (state / "runs/E0399/target-replays").mkdir(parents=True)  # a run killed mid-batch: no result row
            old = time.time() - 14 * 3600
            os.utime(state / "runs/E0399/target-replays", (old, old))
            found = health.target_batches(state)
        # A fresh leftover (E0301's, maybe a batch still reading) waits for the 13 h mark; the killed run's alerts.
        self.assertEqual([a.key for a in found], ["target:E0302", "target-replays:E0399"])
        self.assertIn("read 228 of 288 replays", found[0].text)


class PairTightness(unittest.TestCase):
    # The missed case (MISSES 99): E0091-E0098's real "predict" records, each build playing its own sonar key, repeated
    # 0 of about 100 checks each; the alert fires on five of them. One candidate repeating nothing among others that
    # repeat plenty (a change acting in every game) doesn't.
    def test_every_build_differing_from_the_champion_alerts_and_one_broad_change_does_not(self) -> None:
        real = {"checks": 100, "identical": 0, "predicted": 0, "off": "only 0 of the first 100 checks were identical"}
        rows = [{"id": f"E00{n}", "sprt": {"predict": {**real, "checks": checks}}}
                for n, checks in ((91, 100), (92, 100), (95, 102), (97, 101), (98, 102))]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            found = health.pair_tightness(state)
            self.assertEqual([a.key for a in found], ["pair tightness:E0098"])
            self.assertIn("only 0 of 505 move-prediction checks in E0091, E0092, E0095, E0097, E0098", found[0].text)
            healthy = {"checks": 400, "identical": 120, "predicted": 110, "off": None}
            rows = [*rows[:1], *({"id": f"E04{n}", "sprt": {"predict": healthy}} for n in range(10, 14))]
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(health.pair_tightness(state), [])


class OfficialChanged(unittest.TestCase):
    # E0175's and E0178's real official-map games (testdata/official-E0175.jsonl, E0178): E0175 (kept, 0.43 overall)
    # went live having lost 13 of the 38 pairs that aren't exact mirrors, won 6 and split 19 (M53); E0178 lost 3 and
    # won 5 of its 23. A discarded copy of E0175 and a kept one whose report is still pending stay quiet. With two kept
    # results tested, E0175's p 0.084 is luck's about 0.17 of the time, past chance.TRIGGER_P: no alert; alone, 0.084.
    def test_a_kept_result_worse_where_its_moves_changed_alerts_once(self) -> None:
        import official
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            rows = [{"id": "E0175", "verdict": "kept"}, {"id": "E0178", "verdict": "kept"},
                    {"id": "E0190", "verdict": "discarded"}, {"id": "E0191", "verdict": "kept"}]
            for row, sample in zip(rows, ["E0175", "E0178", "E0175", "E0175"]):
                row["official"] = {"pending": official.PENDING}
                run = state / "runs" / row["id"]
                run.mkdir(parents=True)
                shutil.copy(Path(__file__).parent / f"testdata/official-{sample}.jsonl", run / "official.jsonl")
                if row["id"] != "E0191":
                    (run / "official.json").write_text(json.dumps({"score": 0.43, "games": 100}))
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            seen: list = []
            self.assertEqual(health.official_changed(state, seen), [])
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows if r["id"] != "E0178"))
            found = health.official_changed(state, seen)
            self.assertEqual([a.key for a in found], ["official changed:E0175"])
            self.assertIn("E0175 (kept) scores 0.41 against the champion over the 38 official-map pairs where its "
                          "moves changed: 13 lost, 6 won, 19 split, sign p 0.084 over pairs (1 kept results tested, "
                          "luck 0.084; 31 won, 45 lost of 76 games", found[0].text)
            self.assertIn("not a gate", found[0].text)
            self.assertEqual(health.official_changed(state, seen), [])  # once per result
            self.assertEqual(seen, ["E0175"])

    # Only pairs that differ count: 27 of E0178's 50 pairs (all five on Default) are exact mirrors, one win each. A
    # side-swapped pair's two games share a map and a seed, so the sign test counts pairs, not games.
    def test_mirror_pairs_are_left_out_and_pairs_are_counted(self) -> None:
        import official
        rows = official.json_rows((Path(__file__).parent / "testdata/official-E0178.jsonl").read_text().splitlines())
        changed = official.changed_games(rows)
        self.assertEqual((changed["games"], changed["wins"], changed["losses"]), (46, 25, 21))
        self.assertEqual((changed["pairs"], changed["pairs_lost"], changed["pairs_won"], changed["pairs_split"]),
                         (23, 3, 5, 15))
        mirror = [r for r in rows if r["map"].endswith("default~0.map")]
        self.assertEqual(official.changed_games(mirror)["games"], 0)
        flipped = [dict(mirror[0], winner="b" if mirror[0]["winner"] == "a" else "a"), mirror[1]]
        self.assertEqual(official.changed_games(flipped)["games"], 2)
        e0175 = official.json_rows((Path(__file__).parent / "testdata/official-E0175.jsonl").read_text().splitlines())
        changed = official.changed_games(e0175)
        self.assertEqual((changed["pairs_lost"], changed["pairs_won"], changed["pairs_split"]), (13, 6, 19))
        self.assertAlmostEqual(changed["sign_p"], 0.0835, places=3)

    # A game that errored has no winner: it is left out rather than raising, and its lone partner with it.
    def test_an_errored_game_is_skipped(self) -> None:
        import official
        rows = official.json_rows((Path(__file__).parent / "testdata/official-E0178.jsonl").read_text().splitlines())
        mirror = [r for r in rows if r["map"].endswith("default~0.map")]
        errored = [dict(mirror[0], winner=None, error="engine exited"), mirror[1]]
        changed = official.changed_games(errored)
        self.assertEqual((changed["games"], changed["pairs"]), (0, 0))


class LocalStages(unittest.TestCase):
    # loop/testdata/throughput/utilisation-e0167-sample.jsonl: the real rows 15:40-16:04 on 2026-09-27, E0167's
    # screen and then its evidence refresh holding the lock (experiment None, "setup") with 0-6 of ~55 slots busy.
    def rows(self, local_from: str = "15:52") -> list[dict]:
        sample = Path(__file__).resolve().parent / "testdata/throughput/utilisation-e0167-sample.jsonl"
        rows = [json.loads(line) for line in sample.read_text().splitlines()]
        for row in rows:
            if row["time"] >= local_from and row["evaluation"]:
                row["evaluation"]["local"] = True  # as the marker's "hosts": false now records it
        return rows

    def check(self, rows: list[dict], seen: dict) -> list:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))
            return health.local_stages(state, rows[-1]["at"] + 60, seen)

    def test_the_evaluations_own_host_games_in_a_stage_marked_local_alert(self) -> None:
        self.assertEqual(self.check(self.rows(), {}), [])  # one row with 6 of its games (16:00) is a stage's tail
        # Its screen (9-61 games in flight 15:40-15:45) as though marked local: a host stage host_stage missed.
        (alert,) = self.check(self.rows(local_from="15:40"), {})
        self.assertEqual(alert.key, "local-stage:hosts:E0167:screen")
        self.assertIn("E0167's screen played its own games on the remote hosts for 6 min from 15:40", alert.text)
        # A CPU gate's stage (the marker's MAC_HOSTS, 29 Sep) plays its own games on this Mac's platform's hosts by
        # design: not a stage host_stage missed.
        gate = self.rows(local_from="15:40")
        for row in gate:
            if row["evaluation"].get("local"):
                row["evaluation"]["local"] = throughput.MAC_HOSTS
        self.assertEqual(self.check(gate, {}), [])

    def test_local_stages_alert_until_a_game_above_its_evaluation_share_plays_on_the_hosts_in_one(self) -> None:
        import native
        # These rows were recorded with three coders, whose shares they are judged by.
        with patch.object(native.driver, "CODING", 3), patch.object(native, "YIELDING", 5):
            self.local_stages_until_proven()

    def local_stages_until_proven(self) -> None:
        rows = self.rows()
        local = [r for r in rows if r["evaluation"].get("local") and not any(
            p != "evaluation" for h in r["hosts"].values() for p in (h.get("by") or {}))]
        # 36 local minutes with nothing but the evaluation's own games: the fillers never took the free hosts.
        unused = [{**r, "at": rows[0]["at"] + 60 * i} for i, r in enumerate(local * 3)]
        self.assertGreaterEqual(len(unused), health.LOCAL_PROOF_MINUTES)
        seen: dict = {}
        self.assertEqual([a.key for a in self.check(unused, seen)], ["local-stage:unused"])
        self.assertEqual(self.check(unused[:health.LOCAL_PROOF_MINUTES - 1], seen), [])  # too few to judge yet
        # Rows every stage under the lock produced before MISSES 50 prove nothing: 15:56's official-map report game
        # on mm1 and ll is within the one filler share it always had beside an evaluation, and E0152's lock found
        # coder E0143's 2 games on ll in flight at 07:49 (its first minute, marked local as its CPU gate now is).
        start = self.real("utilisation-e0152-lock-start.jsonl")
        self.assertEqual(health.above_evaluation_share(start[1]), ["ll: coder E0143 2 > 1"])
        self.assertEqual((self.check(rows, seen), self.check(start, seen)), ([], []))  # under 30 local minutes each
        self.assertNotIn("proven", seen)
        # A coder at its usual share (cores // CODING, 3 on mm's 10) a minute on in a local stage: only the new
        # marker allows it, so this is the first firing, and from then on it is quiet for good.
        later = {**start[1], "at": start[1]["at"] + 60, "time": "07:50",
                 "hosts": {**start[1]["hosts"], "mm": {"capacity": 10, "busy": 3.0, "by": {"coder E0151": 3.0}}}}
        self.assertEqual(health.above_evaluation_share(later), ["mm: coder E0151 3 > 1", "ll: coder E0143 2 > 1"])
        self.assertEqual(self.check([start[0], start[1], later], seen), [])
        self.assertEqual(seen["proven"], later["at"])
        self.assertEqual(self.check(unused, seen), [])

    def test_the_idle_fillers_have_no_share_and_the_screen_fillers_and_the_tune_players_one_each(self) -> None:
        # az-syd: a tune and the evidence refresh split one share (native.TUNE_PLAYERS), so 2 on a 2-core VM is over.
        # zz: 16 cores, where the screen fillers together may have one game at most (native.host_share with no slot).
        row = {"hosts": {"mm": {"by": {"sweep": 1.0}}, "zz": {"by": {"official report E0168": 1.0, "late screen": 1.0}},
                         "az-syd": {"by": {"tune E0147": 1.0, "evidence refresh": 1.0, "coder E0151": 1.0}},
                         "here": {"by": {"sweep": 4.0}}},
               "cores": {"mm": 10, "zz": 16, "az-syd": 2}}
        self.assertEqual(health.above_evaluation_share(row), ["mm: idle fillers 1 > 0", "zz: screen fillers 2 > 1",
                                                             "az-syd: tune players 2 > 1"])

    def real(self, name: str) -> list[dict]:
        sample = Path(__file__).resolve().parent / "testdata/throughput" / name
        rows = [json.loads(line) for line in sample.read_text().splitlines()]
        for row in rows:
            if row["evaluation"] and row["evaluation"]["stage"] not in ("SPRT", "screen", "identical check"):
                row["evaluation"]["local"] = True  # as the marker's "hosts": false now records it
        return rows


class EvaluationShares(unittest.TestCase):
    # The real rows of E0167's screen, 15:40-15:44 on 2026-09-27 (testdata/throughput/utilisation-e0167-sample.jsonl,
    # 15:42's twin row read once), a host stage, with mm's 10 cores holding five coders, a post-mortem coder on one of
    # their lanes (each at the one game the most one may have, so no single coder is over) and the evaluation's 4.
    def rows(self, coders: int) -> list[dict]:
        sample = Path(__file__).resolve().parent / "testdata/throughput/utilisation-e0167-sample.jsonl"
        rows = [json.loads(line) for line in sample.read_text().splitlines()][:6]
        self.assertEqual({(r["evaluation"]["stage"], r["evaluation"].get("local")) for r in rows}, {("screen", None)})
        for row in rows:
            by = {"evaluation": 4.0, **{f"coder E{190 + i:04d}": 1.0 for i in range(coders)}}
            row["hosts"]["mm"] = {"capacity": 10, "busy": sum(by.values()), "by": by}
        return rows

    def check(self, rows: list[dict]) -> list:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))
            return health.over_share(state, rows[-1]["at"] + 60)

    def test_the_coders_together_above_their_part_of_the_half_is_found_though_each_is_within_its_own(self) -> None:
        # Two coders on one lane (a post-mortem's beside the new claim) each took the one game a coder may have, so
        # the check of each coder alone never saw the host get 6 of its half of 5.
        over, within = self.rows(6), self.rows(5)
        self.assertEqual(health.above_evaluation_share(over[0]), ["mm: coders and review 6 > 5"])
        self.assertEqual(health.above_evaluation_share(within[0]), [])
        (alert,) = self.check(over)
        self.assertEqual(alert.key, "share:mm")
        self.assertIn("E0167's screen stayed above their share of mm for 5 min from 15:40", alert.text)
        self.assertIn("mm: coders and review 6 > 5", alert.text)
        self.assertEqual(self.check(within), [])
        self.assertEqual(self.check(over[:health.SHARE_MINUTES - 1]), [])  # a stage's first minutes: in flight
        for row in over:
            row["evaluation"]["local"] = True  # the hosts are the coders' in a stage playing only on this Mac
        self.assertEqual(self.check(over), [])

    # The real rows of E0346's SPRT, 18:30-18:36 on 2026-09-29 (testdata/throughput/utilisation-e0346-drain.jsonl), whose
    # 18:37 alert read "ll: idle fillers 1 > 0" for 4 min from 18:31: the sweep's last batch, begun in the CPU gate
    # (18:30, local), drained 7, 4, 1, 1 on ll, one niced game there lasting 234 s beside the SPRT's 14-18 (its
    # results.jsonl), and started none (the other hosts' sweep games were gone by 18:32). Games in flight when the
    # stage began are no player the shares miss; one more game started after them is.
    def test_games_in_flight_when_the_stage_began_are_not_over_its_share(self) -> None:
        sample = Path(__file__).resolve().parent / "testdata/throughput/utilisation-e0346-drain.jsonl"
        rows = [json.loads(line) for line in sample.read_text().splitlines()]
        self.assertEqual([r["hosts"]["ll"].get("by", {}).get("sweep", 0) for r in rows], [6, 7, 4, 1, 1, 0, 0])
        self.assertEqual(self.check(rows), [])
        (alert,) = self.check(rows[1:])  # with the stage's start not in the rows read, every game counts, as before
        # An hour on, the window's edge just past the row before the stage: that row is still read for where the stage
        # began (E0376's, E0384's and E0395's alerts on 30 Sep came exactly an hour after their stages began).
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / throughput.LOG).write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(health.over_share(Path(folder), rows[1]["at"] - 1 + health.SHARE_WINDOW), [])
        self.assertIn("E0346's SPRT stayed above their share of ll for 4 min from 18:31", alert.text)
        for row in rows[4:]:  # 18:34-18:36: two sweep games started after the stage began, the fewest since then 1
            row["hosts"]["ll"]["by"]["sweep"] = row["hosts"]["ll"]["by"].get("sweep", 0) + 2
        (alert,) = self.check(rows)
        self.assertIn("for 3 min from 18:34 (latest: ll: idle fillers 1 > 0)", alert.text)
        source = (Path(__file__).resolve().parent.parent / "referee/run.py").read_text()
        self.assertEqual(health.DRAIN_S, 2 * int(re.search(r"^REMOTE_TIMEOUT_S = (\d+)", source, re.M)[1]))


class FillerRegistries(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        finished = subprocess.Popen(["true"])
        finished.wait()
        self.dead = finished.pid  # a pid no process has now

    # A player's file as native.register_filler writes it (the live fillers/ held "1790504236233960000-12007",
    # {"kind": "1", "pid": 12007}), locked for the test's life when `held`.
    def player(self, folder: str, pid: int, kind: str = "1", held: bool = False, suffix: str = "") -> Path:
        path = self.state / folder / f"{time.time_ns()}-{pid}{suffix}"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"kind": kind, "pid": pid}) + "\n")
        if held:
            handle = path.open("r+")
            self.addCleanup(handle.close)
            health.fcntl.flock(handle, health.fcntl.LOCK_EX)
        return path

    def test_the_folders_are_the_ones_native_registers_players_in(self) -> None:
        import native
        self.assertEqual(health.FILLER_FOLDERS, tuple(p.name for p in (native.FILLERS, native.IDLE_FILLERS,
                                                                        native.TUNE_PLAYERS)))

    def test_live_players_and_dead_ones_awaiting_the_next_count_are_quiet(self) -> None:
        self.player("fillers", self.dead)  # as the live 12007: dead, unlocked, deleted at the next count
        self.player("fillers-idle", os.getpid(), "filler", held=True)
        self.player("fillers-tune", os.getpid(), "tune", held=True, suffix=".tmp")  # registering
        self.assertEqual(health.filler_registries(self.state), [])

    def test_a_file_held_locked_for_a_dead_pid_is_a_phantom_player(self) -> None:
        phantom = self.player("fillers-tune", self.dead, "tune", held=True)
        self.player("fillers-tune", os.getpid(), "tune", held=True)
        (alert,) = health.filler_registries(self.state)
        self.assertEqual(alert.key, "fillers:phantom:fillers-tune")
        self.assertIn(phantom.name, alert.text)

    def test_a_dead_file_a_count_is_deleting_is_no_phantom(self) -> None:
        dying = self.player("fillers", self.dead)
        handle = dying.open("r+")
        self.addCleanup(handle.close)
        health.fcntl.flock(handle, health.fcntl.LOCK_EX)  # native.filler_names' lock, just before its unlink

        def counted(seconds: float) -> None:
            dying.unlink()
            handle.close()

        with patch.object(health.time, "sleep", side_effect=counted) as sleep:
            self.assertEqual(health.filler_registries(self.state), [])
        sleep.assert_called_once_with(health.PHANTOM_RECHECK_S)

    def test_a_folder_past_its_bound_alerts(self) -> None:
        for _ in range(health.FILLER_FILES):
            self.player("fillers", self.dead)
        self.assertEqual(health.filler_registries(self.state), [])
        self.player("fillers", self.dead)
        self.assertEqual([a.key for a in health.filler_registries(self.state)], ["fillers:size:fillers"])


class BacklogRows(unittest.TestCase):
    # testdata/ideas-sample.jsonl holds real backlog rows; I340 is the real row as appended without "status".
    def test_a_live_row_missing_a_required_field_or_not_json_is_named(self) -> None:
        sample = (Path(__file__).parent / "testdata/ideas-sample.jsonl").read_text()
        i340 = (Path(__file__).parent / "testdata/idea-I340-without-status.json").read_text()
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.assertEqual(health.backlog_rows(state), [])  # no backlog yet
            (state / "ideas.jsonl").write_text(sample)
            self.assertEqual(health.backlog_rows(state), [])
            (state / "ideas.jsonl").write_text(sample + i340 + '{"id": "I2\n')
            found = health.backlog_rows(state)
            self.assertEqual([a.key for a in found], ["backlog rows"])
            rows = len(sample.splitlines())
            self.assertIn(f"I340 (line {rows + 1}), line {rows + 2} (not JSON)", found[0].text)

    # An idea that waits on itself is never claimed (driver.ready_ideas): an `after` naming an idea merged into it,
    # or two ideas each after the other, as a merge's absorbed `after` could make before meta.absorb checked it.
    def test_a_todo_idea_that_waits_on_itself_is_named(self) -> None:
        rows = [json.loads(line) for line in (Path(__file__).parent / "testdata/ideas-sample.jsonl").read_text().splitlines()]
        by_id = {r["id"]: r for r in rows}
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            def write() -> list[health.Alert]:
                (state / "ideas.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
                return health.stuck_ideas(state)
            by_id["I110"]["after"] = ["I109"]  # an ordinary order: I109 is claimed first
            self.assertEqual(write(), [])
            by_id["I109"].update(status="merged", merged_into="I110")  # I110 now waits on its own change
            (alert,) = write()
            self.assertIn("1 todo ideas wait on themselves", alert.text)
            self.assertIn(": I110;", alert.text)
            by_id["I109"].update(status="todo", merged_into=None, after=["I110"])  # a cycle of two
            self.assertIn(": I109, I110;", write()[0].text)
            by_id["I109"]["after"] = ["I064"]  # after a tried idea: it clears
            self.assertEqual(write(), [])

    # MISSES 98, on the real records: I697, I897 and I846 stayed followup after their promising results' judges
    # discarded them (E0273, E0318, E0351); I415, dropped after E0295's discard, had moved on. E0320's post-mortem
    # queued I914, its retest alone, after its judge's discard.
    def test_a_followup_whose_promising_result_was_judged_discard_is_named(self) -> None:
        data = json.loads((Path(__file__).parent / "testdata/judged-discard-followups.json").read_text())
        cases, retests = data["cases"], data["retests"]
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            for case in cases + retests:
                (state / "runs" / case["result"]["id"]).mkdir(parents=True)
                (state / "runs" / case["result"]["id"] / "judged.json").write_text(json.dumps(case["judged"]))
            for case in cases:
                self.assertIn(meta.result_note(case["result"]), case["idea"]["history"])  # the note it is found by
            ideas = [case["idea"] for case in cases + retests]
            def write() -> list[health.Alert]:
                (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
                return health.judged_followups(state)
            (alert,) = write()
            self.assertIn("3 followup ideas wait for a retest their judge discarded: I697 (E0273), I897 (E0318), "
                          "I846 (E0351); mark each tried; 1 post-mortem retests of a result its judge discarded are "
                          "queued: I914 (E0320);", alert.text)
            for idea in ideas:  # the orchestrator's fix
                idea["status"] = {"followup": "tried", "todo": "dropped"}.get(idea["status"], idea["status"])
            self.assertEqual(write(), [])


class Stranded(unittest.TestCase):
    """health.stranded: an in-flight try past coding whose evaluator died, named with the commands that recover it."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.e0163 = json.loads((Path(__file__).parent / "testdata/native-E0163-experiment.json").read_text())
        self.run = self.state / "runs/E0163"
        self.run.mkdir(parents=True)
        (self.run / "experiment.json").write_text(json.dumps(self.e0163))
        dead = subprocess.Popen(["true"])
        dead.wait()  # a pid that has exited
        self.dead = dead.pid

    def worker(self, status: str, driver_pid: int | None, kind: str | None = None, **session) -> list[health.Alert]:
        idea = {"id": self.e0163["idea"]["id"], "status": "trying:lane1", **({"kind": kind} if kind else {})}
        (self.state / "ideas.jsonl").write_text(json.dumps(idea) + "\n")
        (self.run / "worker.json").write_text(json.dumps({"experiment": "E0163", **self.e0163["session"], "pid": None,
                                                          "name": "native-E0163-lane1", "status": status,
                                                          "driver_pid": driver_pid, **session}))
        return health.stranded(self.state)

    # native.operate writes "finished" before it waits for the evaluation lock; an evaluate killed while it waited left
    # E0163's candidate with no evaluator: neither coding nor waiting (driver.stranded_count), holding its lane.
    def test_a_try_whose_evaluator_died_is_named_with_its_fix(self) -> None:
        self.assertEqual(self.worker("running", None), [])  # still being coded
        self.assertEqual(self.worker("finished", os.getpid()), [])  # its evaluate waits for or holds the lock
        self.assertEqual(self.worker("finished", None), [])  # from before driver_pid: can't tell, counts as waiting
        (alert,) = self.worker("finished", self.dead)
        self.assertEqual(alert.key, "stranded:E0163")
        self.assertIn("E0163 (I278, lane1, run by a native lane)", alert.text)
        self.assertIn("`uv run loop/native.py resume E0163` hands it to a native session and "
                      "`uv run loop/native.py evaluate E0163", alert.text)
        self.assertIn("records it discarded, marking I278 tried", alert.text)
        (self.state / "results.jsonl").write_text(json.dumps({"id": "E0163", "verdict": "discarded"}) + "\n")
        self.assertEqual(health.stranded(self.state), [])  # recorded: the next claim frees its idea

    # The first version told a dead tune's owner that `native.py abandon` "frees" its idea for a fresh claim, while
    # abandon recorded "discarded", which marks the idea tried and leaves earlier_tune nothing to reuse. It also named
    # every tune "the Claude driver": run_tuner sets provider "tuner" for both providers.
    def test_a_dead_tune_is_told_how_to_go_back_to_the_queue(self) -> None:
        (alert,) = self.worker("tuning", self.dead, kind="tune", provider="tuner", pid=self.dead)
        self.assertIn("run by a tune (either provider)", alert.text)
        self.assertIn("`uv run loop/native.py abandon E0163 '<reason>'` records it as an error", alert.text)
        self.assertIn("I278 goes back to the queue", alert.text)
        self.assertNotIn("frees", alert.text)
        (alert,) = self.worker("finished", self.dead, kind="tune", provider="tuner")  # its values are committed
        self.assertIn("`uv run loop/native.py evaluate E0163", alert.text)
        self.assertIn("reuses its tuned values", alert.text)
        self.assertNotIn("resume", alert.text)  # native.resume refuses a tune: it has no coding to hand back

    # A native evaluate that stops for a stop marker inside the lock leaves its try "finished" with a dead pid, and
    # resume refuses while the marker stands: nothing to do until sig resumes, so no alert every 3 h.
    def test_no_alert_while_a_stop_marker_stands(self) -> None:
        for marker in ("STOP", "ASTRA_STOP"):
            with self.subTest(marker=marker):
                (self.state / marker).touch()
                self.assertEqual(self.worker("finished", self.dead), [])
                (self.state / marker).unlink()
        self.assertEqual(len(self.worker("finished", self.dead)), 1)

    # I838 and I855 (29 Sep) read trying:lane2/lane3 25 min after their results with no process on either lane, and
    # nothing alerted: stranded leaves a recorded try out, expecting a claim to free it. I838's history also held its
    # post-mortem's "E0304: follow-ups I894", which hid it from a check skipping any "E0304:" line, and its worker.json
    # was last written when coding ended, long before the result: the grace runs from the result.
    def test_a_lane_held_with_no_live_process_alerts_after_its_grace(self) -> None:
        now, results, ideas = time.time(), self.state / "results.jsonl", self.state / "ideas.jsonl"

        def recorded(ago: float, *history: str) -> list[health.Alert]:
            results.write_text(json.dumps({"id": "E0163", "verdict": "no code", "reason": "nothing committed",
                                           "finished": now - ago}) + "\n")
            ideas.write_text(json.dumps({**json.loads(ideas.read_text()), "history": list(history)}) + "\n")
            os.utime(self.run / "worker.json", (now - 3 * 3600,) * 2)
            return health.dead_lanes(self.state, now)

        self.worker("finished", self.dead)
        self.assertEqual(recorded(60), [])  # within its grace
        (alert,) = recorded(health.DEAD_LANE_AFTER + 60, "E0163: follow-ups I894")
        self.assertEqual(alert.key, "dead lane:E0163")
        self.assertIn("DEAD LANE: I278 reads trying:lane1 but its try E0163", alert.text)
        self.assertIn("driver.finish_recorded", alert.text)
        self.assertEqual(recorded(health.DEAD_LANE_AFTER + 60, "E0163: no code (nothing committed)"), [])  # updated
        self.worker("finished", os.getpid())
        self.assertEqual(recorded(health.DEAD_LANE_AFTER + 60), [])  # its driver lives
        results.unlink()
        self.worker("finished", self.dead)
        os.utime(self.run / "worker.json", (now - 3 * 3600,) * 2)
        self.assertEqual(health.dead_lanes(self.state, now), [])  # stranded's alert, not a second one


class MacWaits(unittest.TestCase):
    """health.mac_waits: jobs this Mac's load gate holds for longer than an evaluation takes. The process table is
    the live one of 28 Sep ~16:00 (trimmed): E0240's coder's sandbox CPU games, orphaned under a zsh, still waiting
    3 h 07 min with no game child, a gated_test.sh just started, coders' nice-0 analysis scripts and the main
    session's claude."""

    PS = """\
    1     0 root              0 5-05:43:00   0.0 /sbin/launchd
43861 43800 zitianqin         0 5-02:10:00 179.8 /Users/me/.local/bin/claude --session-id 8406d943-dd71-49fc-b16b-5c8a82eb997f --settings /var/folders/x.json
29094 29090 zitianqin         0    01:10:00   8.7 claude -p --model opus --effort high --dangerously-skip-permissions --name bc-E0245-3f2a9c1d --session-id 3f2a9c1d
93196 29094 zitianqin         0       00:40  53.5 .venv/bin/python data/analyse.py data/an_sh_champ.jsonl data/rep_sh_champ
91281     1 root              0 5-05:40:00  54.7 /usr/libexec/PerfPowerServices
15233     1 zitianqin         0    03:07:36   0.0 /bin/zsh -c source /Users/me/.claude/shell-snapshots/snapshot-zsh-1790561146909-866c7s.sh 2>/dev/null || true && export TMPDIR=/tmp/bcwJ7rTf7wL/claude-501 && .venv/bin/python /Users/me/Dev/unsw-battlecode-2026/loop/native.py games E0240 --sandbox
15235 15233 zitianqin         0    03:07:36   0.0 .venv/bin/python /Users/me/Dev/unsw-battlecode-2026/loop/native.py games E0240 --a .scratch/cand_926d87fcd2 --b .scratch/champion --maps .scratch/maps/gen_eval_924010308.map --sandbox --out .scratch/cpu.jsonl
11965 29094 zitianqin         0       00:00   1.2 /bin/bash /Users/me/Dev/unsw-battlecode-2026/tools/gated_test.sh .venv/bin/python /Users/me/Dev/unsw-battlecode-2026/loop/bot_tests.py --bot bot --tests tests/bot
"""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        run = self.state / "runs/E0246"
        run.mkdir(parents=True)
        (run / "evaluation.json").write_text(json.dumps({"max_pairs": 2400}))
        (run / "sprt-progress.json").write_text(json.dumps({"trials": 1200}))
        self.progress = run / "sprt-progress.json"
        created = patch.object(health.throughput, "created", return_value=NOW - 3600)
        self.created = created.start()
        self.addCleanup(created.stop)

    # The check at `now`, with (`live`) the SPRT at 1200 of its 2400 trials an hour after its first and updated a
    # minute ago: an evaluation takes two hours at that rate.
    def waits(self, now: float, seen: dict, text: str = PS, live: bool = True) -> list:
        if live:
            os.utime(self.progress, (now - 60, now - 60))
            self.created.return_value = now - 3600
        return health.mac_waits(self.state, now, seen, health.ps_rows(text))

    def test_a_sandbox_run_with_no_game_alerts_once_it_has_waited_longer_than_an_evaluation_takes(self) -> None:
        seen: dict = {}
        table = "".join(line for line in self.PS.splitlines(True) if not line.startswith("11965"))  # no test waiting
        self.assertEqual(self.waits(NOW, seen, table), [])  # first seen waiting now, not 3 h ago: its age isn't its wait
        self.assertEqual(seen, {"15235": NOW})
        self.assertEqual(self.waits(NOW + 2 * 3600 - 60, seen, table), [])
        (alert,) = self.waits(NOW + 2 * 3600 + 60, seen, table)
        self.assertEqual(alert.key, f"mac waits:{int(NOW)}")
        self.assertIn("MAC GATE STALL: 1 job(s) have waited on this Mac's load gate longer than an evaluation takes "
                      "(120 min, health.evaluation_seconds): pid 15235 121 min (no Claude session): ", alert.text)
        # The busiest at dev priority, with whose they are: the main's claude, a coder's script by its session name.
        self.assertIn("Busiest at dev priority now: pid 43861 180% zitianqin (claude pid 43861)", alert.text)
        self.assertIn("pid 93196 54% zitianqin (bc-E0245-3f2a9c1d): .venv/bin/python data/analyse.py", alert.text)
        # A game started (its child): no longer waiting, and its next wait counts from scratch.
        playing = table + "15300 15235 zitianqin 0 00:05 99.0 /Users/x/.bc-loop/toolchains/1.2.0/bin/python\n"
        self.assertEqual(self.waits(NOW + 3 * 3600, seen, playing), [])
        self.assertEqual(seen, {})

    def test_a_gated_test_waits_its_whole_life_until_it_runs_its_command(self) -> None:
        seen: dict = {}
        old = self.PS.replace("       00:00   1.2 /bin/bash", "    02:30:00   0.0 /bin/bash")
        waiting = old + "11990 11965 zitianqin 0 00:05 0.0 sleep 20\n"
        (alert,) = self.waits(NOW, seen, waiting)
        self.assertIn("pid 11965 150 min (bc-E0245-3f2a9c1d): /bin/bash /Users/me/Dev/unsw-battlecode-2026/"
                      "tools/gated_test.sh", alert.text)
        running = old + ("11990 11965 zitianqin 0 00:05 90.0 .venv/bin/python /Users/me/Dev/"
                         "unsw-battlecode-2026/loop/bot_tests.py --bot bot --tests tests/bot\n")
        self.assertEqual([a for a in self.waits(NOW, seen, running) if "11965" in a.text], [])
        self.assertEqual(self.waits(NOW, {}, self.PS), [])  # just started: not yet

    def test_a_gated_test_whose_command_shows_other_args_has_started(self) -> None:
        # Review (28 Sep): matched by its exact args, `.venv/bin/python -m unittest ...` running as the framework
        # python3 (.../Python.app/Contents/MacOS/Python), an `env X=1 ...` or a `nice ...` looked still waiting, a
        # false stall alert. Any child but the script's own helpers (its sleep, awk, python3 -c, a subshell) is it.
        old = self.PS.replace("       00:00   1.2 /bin/bash", "    02:30:00   0.0 /bin/bash")
        subshell = old.splitlines()[-1].split(None, 6)[-1].strip()
        for child, started in (
                ("/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python "
                 "loop/bot_tests.py --bot bot --tests tests/bot", True),
                ("env BC_X=1 .venv/bin/python loop/bot_tests.py", True), ("nice -n 5 make test", True),
                ("sleep 20", False), ("python3 -c import sys; sys.path.insert(0, sys.argv[1])", False),
                ("awk -v l=5.51 -v c=10 -v s=0.8 BEGIN {", False), (subshell, False)):
            table = old + f"11990 11965 zitianqin 0 00:01 50.0 {child}\n"
            with self.subTest(child=child[:40]):
                alerts = [a for a in self.waits(NOW, {}, table) if "pid 11965" in a.text]
                self.assertEqual(alerts == [], started)

    def test_the_recorded_evaluations_are_read_once_per_version_of_results_jsonl_and_not_for_short_waits(self) -> None:
        # results.jsonl is 184 MB: reading it every minute while anything waits cost 1.4 s of CPU and ~600 MB.
        os.utime(self.progress, (NOW - 3600, NOW - 3600))  # no live SPRT: the recorded ones set the limit
        (self.state / "runs/E001/cand").mkdir(parents=True)
        results = self.state / "results.jsonl"
        results.write_text(json.dumps({"id": "E001", "finished": NOW - 3600 + 7200}) + "\n")
        real = health.throughput.jsonl
        with patch.object(health.throughput, "jsonl", side_effect=real) as read:
            table = "".join(line for line in self.PS.splitlines(True) if not line.startswith("11965"))
            self.assertEqual(health.mac_waits(self.state, NOW, {}, health.ps_rows(table)), [])  # just seen: no read
            self.assertEqual(read.call_count, 0)
            for _ in range(3):
                self.assertEqual(health.evaluation_seconds(self.state, NOW), 7200)
            self.assertEqual(read.call_count, 1)
            with results.open("a") as handle:  # a new result
                handle.write(json.dumps({"id": "E001", "finished": NOW - 3600 + 10800}) + "\n")
            self.assertEqual(health.evaluation_seconds(self.state, NOW), 10800)  # the median of 2 h and 3 h
            self.assertEqual(read.call_count, 2)

    def test_the_quick_checks_run_once_a_minute_between_every_process_asking(self) -> None:
        # Every process running the upkeep asks each minute (maintenance.ticking); `every` keeps it to one run.
        with patch.object(health, "alerts", return_value=[]) as checks:
            self.assertEqual(health.quick(self.state, self.state, NOW, every=50), [])
            self.assertEqual(health.quick(self.state, self.state, NOW + 20, every=50), [])  # another process's ask
            self.assertEqual(checks.call_count, 1)
            health.quick(self.state, self.state, NOW + 60, every=50)
            self.assertEqual(checks.call_count, 2)
            self.assertEqual(checks.call_args.kwargs, {"only": health.QUICK})

    def test_without_a_live_sprt_the_limit_is_the_median_recorded_evaluation(self) -> None:
        self.waits(NOW, {})
        self.assertEqual(health.evaluation_seconds(self.state, NOW), 2 * 3600)
        os.utime(self.progress, (NOW - 3600, NOW - 3600))  # the SPRT ended an hour ago
        rows = []
        for n, hours in enumerate((1, 3, 2)):
            (self.state / f"runs/E00{n}/cand").mkdir(parents=True)
            rows.append({"id": f"E00{n}", "finished": NOW - 3600 + hours * 3600})
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"id": "E9", "fin')
        self.assertEqual(health.evaluation_seconds(self.state, NOW), 2 * 3600)
        (self.state / "results.jsonl").unlink()
        self.assertIsNone(health.evaluation_seconds(self.state, NOW))
        self.assertEqual(self.waits(NOW + 10 * 3600, {"15235": NOW}, live=False), [])  # nothing to measure by: quiet


class LanesShort(unittest.TestCase):
    """health.lanes_short, as the upkeep tick runs it each minute (health.quick): fewer experiments in flight than
    driver.IN_FLIGHT for LANES_AFTER while a claim would be taken (REFLECT 0849 F8: lanes drained twice on 2026-09-28
    with the evaluator idle and no alert)."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    # `lanes` ideas in flight (claimed, no experiment yet: each counts as being coded) and `todo` to do, the first of
    # them `after` an idea in flight when `waiting`.
    def backlog(self, lanes: int, todo: int, waiting: bool = False) -> None:
        ideas = [{"id": f"I{n}", "status": f"trying:lane{n}", "priority": 2} for n in range(1, lanes + 1)]
        ideas += [{"id": f"I{100 + n}", "status": "todo", "priority": 1 + n, **({"after": ["I1"]} if waiting else {})}
                  for n in range(todo)]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))

    def sent(self, now: float) -> list[str]:
        return health.quick(self.state, self.state, now)

    def test_fewer_in_flight_for_five_minutes_alerts_once_and_again_every_half_hour(self) -> None:
        import driver
        self.backlog(lanes=2, todo=3)
        self.assertEqual(self.sent(NOW), [])  # the spell starts
        self.assertEqual(self.sent(NOW + health.LANES_AFTER - 60), [])
        (text,) = self.sent(NOW + health.LANES_AFTER)
        self.assertTrue(text.startswith(f"LANES SHORT: 2 of {driver.IN_FLIGHT} experiments in flight since "), text)
        self.assertIn("busy: lane1, lane2; 2 being coded) with 3 ideas ready (first: I100, I101, I102): room for "
                      f"{min(driver.IN_FLIGHT, driver.CODING) - 2} more", text)
        self.assertEqual(self.sent(NOW + health.LANES_AFTER + 60), [])  # once a spell ...
        self.assertEqual(len(self.sent(NOW + health.LANES_AFTER + health.LANES_REPEAT)), 1)  # ... then every 30 min
        log = (self.state / "host-alerts.log").read_text()
        self.assertEqual(log.count(" health LANES SHORT: "), 2)
        # Refilled, the spell ends; drained again, a new one waits its own five minutes.
        self.backlog(lanes=driver.IN_FLIGHT, todo=3)
        self.assertEqual(self.sent(NOW + 3600), [])
        self.backlog(lanes=2, todo=3)
        self.assertEqual(self.sent(NOW + 3660), [])
        self.assertEqual(len(self.sent(NOW + 3660 + health.LANES_AFTER)), 1)

    def test_a_full_pipeline_no_ready_idea_every_coder_busy_or_a_stop_is_quiet(self) -> None:
        import driver
        cases = {"full pipeline": dict(lanes=driver.IN_FLIGHT, todo=3), "empty queue": dict(lanes=2, todo=0),
                 "todo waits on a prerequisite": dict(lanes=2, todo=1, waiting=True),
                 "CODING being coded": dict(lanes=driver.CODING, todo=3), "stop marker": dict(lanes=2, todo=3)}
        for name, case in cases.items():
            with self.subTest(name):
                (self.state / "health.json").unlink(missing_ok=True)
                if name == "stop marker":
                    (self.state / "STOP").touch()
                self.backlog(**case)
                self.assertEqual(self.sent(NOW), [])
                self.assertEqual(self.sent(NOW + health.LANES_AFTER + 60), [])
        self.assertFalse((self.state / "host-alerts.log").exists())


class EvalBacklog(unittest.TestCase):
    """health.eval_backlog (REFLECT 2026-10-01 03:17 F6): nothing coded queued behind a live evaluation while coders
    work alerts after BACKLOG_AFTER; one live ticket, or no coder, is quiet."""

    def test_an_empty_backlog_behind_a_live_evaluation_alerts(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state, seen = Path(directory.name), {}
        (state / "evaluation-queue").mkdir()
        (state / "runs/E0472").mkdir(parents=True)
        (state / "runs/E0472/worker.json").write_text(json.dumps({"status": "running"}))
        with patch.object(health.home, "evaluation_running", return_value=True), \
                patch.object(health, "recorded_evaluation", return_value=2400.0):
            self.assertEqual(health.eval_backlog(state, NOW, seen), [])
            (alert,) = health.eval_backlog(state, NOW + health.BACKLOG_AFTER, seen)
            self.assertTrue(alert.text.startswith("EVAL BACKLOG: 0 coded candidates"), alert.text)
            self.assertIn("~40 min", alert.text)
            self.assertIn("while 1 code (E0472)", alert.text)
            ticket = {"pid": os.getpid(), "started": health.meta.process_started(os.getpid()), "experiment": "E0471"}
            (state / "evaluation-queue/1-E0471.json").write_text(json.dumps(ticket))
            self.assertEqual(health.eval_backlog(state, NOW + 2 * health.BACKLOG_AFTER, seen), [])
            (state / "runs/E0472/worker.json").write_text(json.dumps({"status": "finished"}))
            (state / "evaluation-queue/1-E0471.json").unlink()
            self.assertEqual(health.eval_backlog(state, NOW + 4 * health.BACKLOG_AFTER, seen), [])


class EvaluationQueue(unittest.TestCase):
    """health.evaluation_queue, every minute (health.quick): review of 702bbb9, a dead ticket that looked live stalled
    the queue for good with nothing said."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.queue = self.state / "evaluation-queue"
        self.queue.mkdir()

    def ticket(self, coded: float, exp_id: str, started: str | None) -> Path:
        path = self.queue / f"{coded:017.3f}-{exp_id}.json"
        path.write_text(json.dumps({"pid": os.getpid(), "started": started, "experiment": exp_id}))
        return path

    def sent(self, now: float, running: bool = False) -> list[str]:
        with patch.object(health.home, "evaluation_running", return_value=running):
            return health.quick(self.state, self.state, now)

    def test_a_live_looking_head_with_the_lock_free_alerts_after_the_stall_grace(self) -> None:
        own = health.meta.process_started(os.getpid())
        self.ticket(100.0, "E0231", own)  # live by ticket_live: this process, as it started
        self.ticket(200.0, "E0236", own)
        self.assertEqual(self.sent(NOW, running=True), [])  # waiting behind a running evaluation is the queue's job
        self.assertEqual(self.sent(NOW + 60), [])  # the lock frees: the stall starts
        self.assertEqual(self.sent(NOW + 60 + health.QUEUE_STALL), [])
        # The head's merge turn, started by driver.prepare beside the evaluation, still runs: its stall counts from the
        # turn's end (a resumed session of another experiment's is no excuse).
        run = self.state / "runs/E0231"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"session": {"session_id": "s-0231"}}))
        resumed = lambda session: [health.PsRow(1, 1, "u", 5, 60.0, 1.0, f"claude -p --resume {session}")]  # noqa
        with patch.object(health, "ps_rows", return_value=resumed("s-0231")):
            self.assertEqual(self.sent(NOW + 120 + health.QUEUE_STALL), [])
        with patch.object(health, "ps_rows", return_value=resumed("s-0236")):
            self.assertEqual(self.sent(NOW + 120 + 2 * health.QUEUE_STALL), [])
            (text,) = self.sent(NOW + 180 + 2 * health.QUEUE_STALL)
        self.assertTrue(text.startswith("EVALUATION QUEUE STALLED: 2 candidates wait"), text)
        self.assertIn(f"0000000000100.000-E0231.json (E0231, pid {os.getpid()}", text)
        self.assertIn("live by meta.ticket_live", text)
        # An evaluation taking the lock ends the spell; a new stall waits its own grace.
        self.assertEqual(self.sent(NOW + 7200, running=True), [])
        self.assertEqual(self.sent(NOW + 7260), [])

    # 30 Sep 21:27:55: "EVALUATION QUEUE STALLED ... since at least 21:17" while E0429's SPRT played (since 21:04,
    # 1482/2400 trials by 21:37): a fresh sprt-progress.json is a live evaluation whatever the marker says.
    def test_a_playing_sprt_is_a_live_evaluation(self) -> None:
        own = health.meta.process_started(os.getpid())
        self.ticket(100.0, "E0398", own)
        self.ticket(200.0, "E0399", own)
        stalled = datetime.fromisoformat("2026-09-30T21:17:00+10:00").timestamp()
        alerted = datetime.fromisoformat("2026-09-30T21:27:55+10:00").timestamp()
        progress = self.state / "runs/E0429/sprt-progress.json"
        progress.parent.mkdir(parents=True)
        progress.write_text("{}")
        for at in (stalled, alerted):
            os.utime(progress, (at - 20, at - 20))
            self.assertEqual(self.sent(at), [])
        self.assertEqual(health.evaluation_live(self.state, alerted), (True, {"E0429"}))
        os.utime(progress, (stalled - health.RECENT_SPRT, stalled - health.RECENT_SPRT))  # it ended
        self.assertEqual(health.evaluation_live(self.state, alerted), (False, set()))

    def test_a_fresh_merge_agent_and_its_resumed_turn_count_as_the_heads_merge_turn(self) -> None:
        # driver.fresh_merge_turn names its agent, and a resume of it that ends waiting (run_resolve) keeps that
        # name: its --resume id is the fresh session's, which experiment.json doesn't hold until the turn ends.
        run = self.state / "runs/E0231"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"session": {"session_id": "s-0231"}}))
        row = lambda args: [health.PsRow(1, 1, "u", 5, 60.0, 1.0, args)]  # noqa: E731
        for args in ("claude -p --name bc-E0231-merge-1a2b3c4d --session-id 1a2b3c4d-0000",
                     "claude -p --resume 1a2b3c4d-0000 --name bc-E0231-merge-1a2b3c4d"):
            with self.subTest(args), patch.object(health, "ps_rows", return_value=row(args)):
                self.assertTrue(health.merge_turn_running(self.state, "E0231"))
        with patch.object(health, "ps_rows", return_value=row("claude -p --name bc-E02310-merge-1a2b3c4d")):
            self.assertFalse(health.merge_turn_running(self.state, "E0231"))  # another experiment's agent

    def test_a_dead_head_before_a_live_waiter_is_named_and_dead_tickets_alone_are_quiet(self) -> None:
        self.ticket(100.0, "E0231", "Mon Sep 28 01:02:03 2026")  # another process that had this pid
        self.assertEqual(self.sent(NOW), [])
        self.assertEqual(self.sent(NOW + 2 * health.QUEUE_STALL), [])  # nobody waits: nothing is held up
        self.ticket(200.0, "E0236", health.meta.process_started(os.getpid()))
        self.assertEqual(self.sent(NOW + 3 * health.QUEUE_STALL), [])
        (text,) = self.sent(NOW + 4 * health.QUEUE_STALL + 60)
        self.assertIn("1 candidates wait", text)
        self.assertIn("(E0231, pid", text)
        self.assertIn("dead by meta.ticket_live, yet the live waiter 0000000000200.000-E0236.json hasn't removed it",
                      text)


class RefusedProposals(unittest.TestCase):
    """health.refused_proposals: a proposal meta.report_refused saved is re-added or decided within DECIDE_GRACE (its
    host-alerts.log line scrolls away; the file keeps it)."""

    def test_saved_refusals_alert_until_re_added_or_decided(self) -> None:
        import meta
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            proposals = [{"category": "strategy", "priority": 2, "title": f"Fold from r{n}00", "hypothesis": "h"}
                         for n in (2, 3)]
            meta.report_refused(state, state / "runs" / "E0231", "E0231 post-mortem",
                                [(json.dumps(p), "bare threshold") for p in proposals])
            saved = state / "runs" / "E0231" / meta.REFUSED_FILE
            at = saved.stat().st_mtime
            (state / "ideas.jsonl").write_text("")
            self.assertEqual(health.refused_proposals(state, at + health.DECIDE_GRACE - 60), [])
            (alert,) = health.refused_proposals(state, at + health.DECIDE_GRACE + 60)
            self.assertEqual(alert.key, "refused:runs/E0231/refused-ideas.jsonl")
            self.assertIn("2 refused proposals", alert.text)
            self.assertIn("from E0231 post-mortem", alert.text)
            # One re-added (its title kept), one still owed.
            (state / "ideas.jsonl").write_text(json.dumps({"id": "I720", "status": "todo", "title": "Fold from r200"}))
            (alert,) = health.refused_proposals(state, at + health.DECIDE_GRACE + 60)
            self.assertIn("1 refused proposals", alert.text)
            # A decision block naming the file settles the rest.
            (state / "decisions.md").write_text("## Orchestrator's decisions on runs/E0231/refused-ideas.jsonl "
                                                "(2026-09-28 17:10)\n- declined: I580 covers the r300 hold\n")
            self.assertEqual(health.refused_proposals(state, at + health.DECIDE_GRACE + 60), [])


class Run(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        study = json.loads((DATA / "study.json").read_text())
        study.update(fetched_at=study["attempt_at"] - 16 * 3600, error="JSONDecodeError: Extra data")
        (self.state / "study.json").write_text(json.dumps(study))
        (self.state / "briefing.md").write_text((DATA / "briefing.md").read_text())
        disk = patch.object(health.shutil, "disk_usage", return_value=Usage(926e9, 507e9, 390e9))
        disk.start()
        self.addCleanup(disk.stop)

    def test_an_alert_is_written_at_most_once_every_three_hours(self) -> None:
        first = health.run(self.state, self.state, NOW)
        self.assertEqual(len(first), 1)
        self.assertIn("leader study: no success", first[0])
        log = self.state / "host-alerts.log"
        self.assertEqual(log.read_text().count(" health leader study: no success"), 1)
        self.assertEqual(health.run(self.state, self.state, NOW + 60), [])
        again = health.run(self.state, self.state, NOW + health.REPEAT + 1)
        self.assertEqual([text.split(" for over")[0] for text in again], ["leader study: no success"])
        self.assertEqual(log.read_text().count(" health leader study: no success"), 2)

    def test_the_upkeep_tick_checks_at_most_hourly(self) -> None:
        self.assertEqual(len(health.run(self.state, self.state, NOW, every=health.EVERY)), 1)
        (self.state / "health.json").write_text(json.dumps({"checked_at": NOW, "sent": {}}))
        self.assertEqual(health.run(self.state, self.state, NOW + 60, every=health.EVERY), [])
        self.assertEqual(len(health.run(self.state, self.state, NOW + health.EVERY, every=health.EVERY)), 1)

    def test_a_dry_run_reports_every_alert_and_writes_nothing(self) -> None:
        before = sorted(p.name for p in self.state.iterdir())
        self.assertEqual(len(health.run(self.state, self.state, NOW, dry_run=True)), 1)
        self.assertEqual(len(health.run(self.state, self.state, NOW, dry_run=True)), 1)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), before)

    def reminders_sent(self, now: float) -> list[str]:
        return [text for text in health.run(self.state, self.state, now) if text.startswith("reminder")]

    def test_a_due_reminder_alerts_once_and_is_marked_done(self) -> None:
        due = datetime.fromtimestamp(NOW + 600).astimezone().isoformat(timespec="minutes")
        row = health.remind(self.state, due, "check the variant sweep's first\nresults (sweeps/<champion>)")
        health.remind(self.state, "2026-10-20T09:00+10:00", "after the Grand Final")
        self.assertEqual(row["text"], "check the variant sweep's first results (sweeps/<champion>)")
        self.assertEqual(self.reminders_sent(NOW), [])  # not due yet
        (text,) = self.reminders_sent(NOW + 3600)
        self.assertTrue(text.startswith("reminder due "))
        self.assertIn("check the variant sweep's first results", text)
        dry = health.run(self.state, self.state, NOW + 3600, dry_run=True)
        self.assertFalse([t for t in dry if t.startswith("reminder")])  # done: not even a dry run lists it again
        self.assertEqual(self.reminders_sent(NOW + 2 * health.REPEAT), [])  # once only, past the rate limit too
        rows = [json.loads(line) for line in (self.state / "reminders.jsonl").read_text().splitlines()]
        self.assertEqual([bool(r.get("done_at")) for r in rows], [True, False])
        self.assertEqual((self.state / "host-alerts.log").read_text().count("health reminder due"), 1)

    def test_a_reminder_needs_a_time_and_text_and_a_bad_line_alerts(self) -> None:
        with self.assertRaises(ValueError):
            health.remind(self.state, "next tuesday", "x")
        with self.assertRaises(ValueError):
            health.remind(self.state, "2026-09-29T09:00", "  ")
        (self.state / "reminders.jsonl").write_text('{"due": "soon", "text": "x"}\n')
        (alert,) = health.reminders(self.state, NOW)
        self.assertTrue(alert.key.startswith("reminder:bad:"))
        self.assertIn("line 1 is no reminder", alert.text)

    def alert_lines(self) -> list[str]:
        return (self.state / "host-alerts.log").read_text().splitlines()

    def test_two_identical_reminder_lines_alert_once(self) -> None:
        for _ in range(2):
            health.remind(self.state, "2026-09-20T09:00", "check the sweep")
        self.assertEqual(len(self.reminders_sent(NOW)), 1)
        self.assertEqual(sum("reminder due" in line for line in self.alert_lines()), 1)

    def test_a_reminder_or_a_bad_line_cannot_forge_an_alert_line(self) -> None:
        forged = "\n2026-09-26 01:00:00 health all clear"
        (self.state / "reminders.jsonl").write_text(
            json.dumps({"due": "2026-09-20T09:00", "text": f"check it{forged} \x85"}) + "\n" +
            json.dumps({"due": f"soon{forged}", "text": "x"}) + "\n")
        health.run(self.state, self.state, NOW)
        lines = self.alert_lines()
        self.assertFalse([line for line in lines if line.startswith("2026-09-26 01:00:00")])
        self.assertEqual(sum("health all clear" in line for line in lines), 2)  # each kept, inside its own line

    def test_reminders_that_cant_be_marked_done_are_one_alert_not_a_repeat_each_tick(self) -> None:
        health.remind(self.state, "2026-09-20T09:00", "check the sweep")
        with patch.object(health, "mark_reminders", side_effect=PermissionError("read-only")):
            sent = health.run(self.state, self.state, NOW)
            self.assertEqual(len([t for t in sent if t.startswith(("reminder", "could not mark"))]), 2)
            self.assertEqual(health.run(self.state, self.state, NOW + 60), [])  # health.json kept the rate limit
        self.assertIn("could not mark 1 due reminders done", "\n".join(self.alert_lines()))



# check_refs.py stand-in for doc_refs: one error in a committed file, one in a file being edited, one warning.
REFS_STUB = """import json
print(json.dumps([
    {"path": "CLAUDE.md", "line": 7, "kind": "name", "ref": "sprt.limit_kept", "why": "", "level": "error"},
    {"path": "PLAN.md", "line": 3, "kind": "path", "ref": "loop/status.md", "why": "", "level": "error"},
    {"path": "CLAUDE.md", "line": 15, "kind": "value", "ref": "mergeRound 499", "why": "", "level": "warning"},
]))
"""


class DocRefs(unittest.TestCase):
    """doc_refs on a temp repo whose tools/check_refs.py is a stand-in."""

    def setUp(self) -> None:
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state, self.repo = Path(directory.name) / "state", Path(directory.name) / "repo"
        self.state.mkdir()
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "tools/check_refs.py").write_text(REFS_STUB)
        for name in ("CLAUDE.md", "PLAN.md"):
            (self.repo / name).write_text("notes\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "commit.gpgsign=false", "commit", "-qm", "first")

    def test_broken_references_alert_but_not_warnings_or_files_being_edited(self) -> None:
        (self.repo / "PLAN.md").write_text("an edit under way\n")
        [alert] = health.doc_refs(self.repo)
        self.assertEqual(alert.key, "docs:refs")
        self.assertIn("DOC REFS: 1 broken references in main's docs and comments (CLAUDE.md:7 name sprt.limit_kept)",
                      alert.text)
        health.run(self.state, self.repo, NOW)  # registered in health.alerts
        self.assertIn(f"health {alert.text}", (self.state / "host-alerts.log").read_text())
        (self.repo / "tools/check_refs.py").unlink()
        self.assertEqual(health.doc_refs(self.repo), [])  # a checkout without the checker


class DocSize(unittest.TestCase):
    """doc_size on a temp repo with the real tools/doc_size.py: main's committed CLAUDE.md over its cap alerts,
    an oversized copy only in the working tree (an edit under way, the hook's to block) doesn't."""

    def setUp(self) -> None:
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state, self.repo = Path(directory.name) / "state", Path(directory.name) / "repo"
        self.state.mkdir()
        (self.repo / "tools").mkdir(parents=True)
        shutil.copy(health.REPO / "tools/doc_size.py", self.repo / "tools/doc_size.py")
        self.limit = runpy.run_path(str(self.repo / "tools/doc_size.py"))["LIMITS"]["CLAUDE.md"]
        (self.repo / "CLAUDE.md").write_text("rules\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "first")

    def test_main_over_the_cap_alerts_and_a_working_tree_edit_does_not(self) -> None:
        big = "x" * (self.limit + 1)
        (self.repo / "CLAUDE.md").write_text(big)
        self.assertEqual(health.doc_size(self.repo), [])
        git(self.repo, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qam", "big")
        [alert] = health.doc_size(self.repo)
        self.assertEqual(alert.key, "docs:size:CLAUDE.md")
        self.assertIn(f"DOC SIZE: main's CLAUDE.md is {len(big):,} bytes, over its {self.limit:,}-byte cap",
                      alert.text)
        self.assertIn("loop/LOOP.md", alert.text)  # says where the detail goes
        health.run(self.state, self.repo, NOW)  # registered in health.alerts
        self.assertIn(f"health {alert.text}", (self.state / "host-alerts.log").read_text())
        (self.repo / "tools/doc_size.py").unlink()
        self.assertEqual(health.doc_size(self.repo), [])  # a checkout without the tool

    def test_a_crashing_tool_raises_rather_than_reading_as_all_clear(self) -> None:
        (self.repo / "tools/doc_size.py").write_text("raise SystemExit('doc_size broke')\n")
        with self.assertRaisesRegex(RuntimeError, "doc_size broke"):
            health.doc_size(self.repo)


class ResultTurns(unittest.TestCase):
    # testdata/results-kept-sample.jsonl's real rows (see test_driver.KEPT), as if claimed after the follow-on landed.
    def test_a_kept_result_without_its_follow_on_alerts_after_the_grace(self) -> None:
        state = Path(tempfile.mkdtemp())
        rows = [json.loads(line) for line in (DATA.parent / "results-kept-sample.jsonl").read_text().splitlines()]
        started = prune.TURNS_FROM + 60
        finished = started + 3600
        (state / "results.jsonl").write_text("".join(json.dumps({**row, "started": started, "finished": finished})
                                                     + "\n" for row in rows))
        now = finished + health.RESULT_TURN_GRACE + 1
        self.assertEqual(health.result_turns(state, finished + 600), [])  # its turn may still be waiting for the lock
        # E0118 and E0125 were owed one; E0127, an identical idea, wasn't.
        self.assertEqual([a.key for a in health.result_turns(state, now)], ["followon:E0118", "followon:E0125"])
        for run, text in (("E0118", '{"result": "none warranted"}'), ("E0125", "")):
            (state / "runs" / run).mkdir(parents=True)
            (state / "runs" / run / "followon.json").write_text(text)
        (alert,) = health.result_turns(state, now)
        self.assertIn("E0125 (kept, recorded", alert.text)
        self.assertIn("it failed", alert.text)
        self.assertIn("driver.py --turn E0125", alert.text)
        # An error result saved as the turn (the usage limit at 04:14 on 2026-09-27) is no turn either.
        (state / "runs" / "E0118" / "followon.json").write_text((DATA.parent / "postmortem-usage-limit.json").read_text())
        self.assertEqual([a.key for a in health.result_turns(state, now)], ["followon:E0118", "followon:E0125"])
        # Claimed before the follow-on turn existed, by a driver that never runs one, though recorded after it landed
        # (the --once runs E0131-E0134 had loaded the old driver): never owed one.
        (state / "results.jsonl").write_text("".join(json.dumps({**row, "finished": finished}) + "\n" for row in rows))
        self.assertEqual(health.result_turns(state, now), [])


    # REFLECT 2026-09-28 17:52 F9: E0219's post-mortem never ran and was found by hand 9.9 h later. The grace comes from
    # the saved turns' own run times: the last 32 timed turns' real duration_ms on 28 Sep (E0212-E0249, 0.9-50 min),
    # whose 90th percentile is 32 min, so a missing turn alerts after 64 min, not 6 h.
    def test_the_grace_is_twice_the_recent_turns_90th_percentile_run_time(self) -> None:
        state = Path(tempfile.mkdtemp())
        timed = [51562, 431718, 377495, 536387, 741860, 663671, 149335, 938210, 1920465, 1484322, 1061833, 1171279,
                 602213, 2440224, 51312, 85294, 829392, 58897, 326273, 2088899, 950438, 1007733, 966393, 2981926,
                 371538, 579568, 338323, 69940, 1280911, 736869, 1101356, 812047]
        finished = prune.TURNS_FROM + 3600
        rows = [{"id": f"E{100 + n:04d}", "verdict": "discarded", "session": {"session_id": "s"},
                 "sprt": {"decision": "H0"}, "started": finished - 60, "finished": finished}
                for n in range(len(timed) + 1)]
        (state / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        for row, ms in zip(rows, timed):
            (state / "runs" / row["id"]).mkdir(parents=True)
            (state / "runs" / row["id"] / "postmortem.json").write_text(json.dumps({"result": "r", "duration_ms": ms}))
        self.assertAlmostEqual(health.turn_grace(state, rows), 2 * 1920.465)
        missing = rows[-1]["id"]
        self.assertEqual(health.result_turns(state, finished + 3600), [])
        (alert,) = health.result_turns(state, finished + 3900)
        self.assertEqual(alert.key, f"postmortem:{missing}")
        self.assertIn("from its coder 1.1 h on (twice the recent turns' 90th percentile run time, at least 1 h)",
                      alert.text)
        # Short turns: the floor. Fewer than TURN_GRACE_SAMPLES timed turns: the old fixed 6 h.
        self.assertEqual(health.turn_grace(state, rows[:health.TURN_GRACE_SAMPLES]), health.TURN_GRACE_MIN)
        self.assertEqual(health.turn_grace(state, rows[:health.TURN_GRACE_SAMPLES - 1]), health.RESULT_TURN_GRACE)


class UncommittedNoCode(unittest.TestCase):
    # E0197's real row (testdata/result-E0197-waiting.json, its idea trimmed): no code, its coder's reason "Waiting for
    # the 24 re-run games to finish; I'll be notified when they do.", with its change uncommitted in its clone.
    def test_a_no_code_result_whose_clone_holds_uncommitted_work_alerts_once_until_saved(self) -> None:
        result = json.loads((DATA.parent / "result-E0197-waiting.json").read_text())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, repo, clones = root / "state", root / "repo", root / "clones"
            state.mkdir()
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            for tree in (repo, clones / "E0197"):
                (tree / "bot").mkdir(parents=True)
                (tree / "bot/main.cpp").write_text("// bot\n")
                git(tree, "init", "-q", "-b", "main")
                git(tree, "add", ".")
                git(tree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            seen: list = []
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, seen), [])  # a clean clone
            (clones / "E0197/bot/main.cpp").write_text("// half of I441's change\n")
            (clones / "E0197/tests/bot").mkdir(parents=True)
            (clones / "E0197/tests/bot/test_new.cpp").write_text("// new test\n")
            (alert,) = health.uncommitted_no_code(state, repo, clones, seen)
            self.assertEqual(alert.key, "uncommitted:E0197")
            self.assertIn("E0197 (I441) was recorded as no code", alert.text)
            self.assertIn("2 uncommitted files", alert.text)
            self.assertIn("save it as wip/E0197", alert.text)
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, seen), [])  # once per result
            git(repo, "branch", "wip/E0197")  # saved by hand, as the orchestrator did
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, []), [])
            (state / "results.jsonl").write_text(json.dumps({**result, "verdict": "error"}) + "\n")
            git(repo, "branch", "-D", "wip/E0197")
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, []), [])  # keep_wip saves those
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            self.assertEqual(len(health.uncommitted_no_code(state, repo, clones, [])), 1)
            # A clone whose git config changed since it was made (driver.make_worktree's digest): no git runs in it.
            (state / "runs/E0197").mkdir(parents=True)
            (state / "runs/E0197/git-config.sha256").write_text("0" * 64 + "\n")
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, []), [])
            (state / "runs/E0197/git-config.sha256").unlink()
            (clones / "E0197/.git/commondir").write_text("/somewhere/else\n")
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, []), [])

    def test_a_coder_that_hands_in_no_code_on_purpose_is_not_alerted(self) -> None:
        # E0111's real row: stopped by the orchestrator, "committed nothing" and left its measurement edits in the clone.
        result = json.loads((DATA.parent / "result-E0111-no-code.json").read_text())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, repo, clones = root / "state", root / "repo", root / "clones"
            state.mkdir()
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            for tree in (repo, clones / "E0111"):
                (tree / "bot").mkdir(parents=True)
                (tree / "bot/params.hpp").write_text("// bot\n")
                git(tree, "init", "-q", "-b", "main")
                git(tree, "add", ".")
                git(tree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
            (clones / "E0111/bot/params.hpp").write_text("// deadEndFarms switch\n")
            self.assertEqual(health.uncommitted_no_code(state, repo, clones, []), [])
            # One that also ends waiting for its own work was cut off all the same.
            result["session"]["summary"] += "\n\nWaiting for the quick check to finish."
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            self.assertEqual(len(health.uncommitted_no_code(state, repo, clones, [])), 1)


class StrayBrowsers(unittest.TestCase):
    # A process whose command line runs browse/src/server.ts, as a live gstack browse server's does.
    def fake_server(self) -> int:
        server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "browse/src/server.ts"])
        self.addCleanup(server.wait)
        self.addCleanup(server.kill)
        return server.pid

    # A state file that names no live browse server, however it is broken, is None, never an exception.
    def test_a_broken_state_file_names_no_browser(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, text in (("garbled", "{not json"), ("list", "[4242]"), ("text pid", '{"pid": "4242"}'),
                           ("minus one", '{"pid": -1}'), ("true", '{"pid": true}')):
            (folder / name).write_text(text)
        (folder / "folder").mkdir()
        for name in ("missing", "garbled", "list", "text pid", "minus one", "true", "folder"):
            with self.subTest(name=name):
                self.assertIsNone(browse_server(folder / name))

    # MISSES 51: a browse command run from a worktree started a second, signed-out browser there.
    def test_a_live_browser_outside_the_main_checkout_alerts_wherever_health_runs(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        main, tree, clones = folder / "main", folder / "tree", folder / "bc-wt"
        clone, dead, reused = clones / "E0001", folder / "dead", folder / "reused"
        live = self.fake_server()
        # A pid that no longer runs, and one another program took after a hard-killed server left its file behind.
        for path, pid in ((main, live), (tree, live), (clone, live), (dead, 2 ** 22 + 12345), (reused, os.getpid())):
            (path / ".gstack").mkdir(parents=True)
            (path / ".gstack" / "browse.json").write_text(json.dumps({"pid": pid}))
        (main / ".git").mkdir()
        (tree / ".git").write_text(f"gitdir: {main}/.git/worktrees/tree\n")
        listed = [{"path": p, "head": "0" * 40, "locked": False} for p in (main, tree, dead, reused)]
        with patch.object(health, "worktrees", return_value=listed), \
                patch.object(health, "clones_folder", return_value=clones) as clones_of:
            for repo in (main, tree):  # run from the main checkout or from a worktree
                with self.subTest(repo=repo.name):
                    alerts = health.stray_browsers(repo)
                    self.assertEqual([a.key for a in alerts], ["browser:stray"])
                    self.assertIn(f"{tree} (pid {live})", alerts[0].text)
                    self.assertIn(f"{clone} (pid {live})", alerts[0].text)
                    for quiet in (f"{main} (pid", str(dead), str(reused)):
                        self.assertNotIn(quiet, alerts[0].text)
                    self.assertIn(f"BROWSE_STATE_FILE={main / '.gstack' / 'browse.json'}", alerts[0].text)
            clones_of.assert_called_with(main)
            for path in (tree, clone):
                (path / ".gstack" / "browse.json").unlink()
            self.assertEqual(health.stray_browsers(tree), [])


# REFLECT 2026-09-27 F3 (a hole in MISSES 51): gstack restarted sig's browser headless and signed out, and nothing said
# so until the next Discord read failed.
class DiscordBrowser(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state, self.main, self.tree = folder / "home", folder / "main", folder / "tree"
        (self.state / "discord").mkdir(parents=True)
        (self.tree / ".git").mkdir(parents=True)
        self.record = self.state / "discord" / "browser.json"
        listed = [{"path": p, "head": "0" * 40, "locked": False} for p in (self.main, self.tree)]
        self.enterContext(patch.object(health, "worktrees", return_value=listed))

    def check(self, live):
        with patch.object(health, "browse_server", return_value=live) as server:
            alerts = health.discord_browser(self.state, self.tree)
        if server.called:  # the main checkout's browser, whichever worktree health runs from
            server.assert_called_once_with(self.main / ".gstack" / "browse.json")
        return alerts

    def test_no_record_is_quiet(self):
        self.assertEqual(self.check(4242), [])

    def test_an_unreadable_record_alerts(self):
        for text in ("{not json", "[1111]", "{}", '{"pid": "1111"}', '{"pid": true}'):
            with self.subTest(text=text):
                self.record.write_text(text)
                [alert] = self.check(4242)
                self.assertEqual(alert.key, "discord:browser-record")
                self.assertIn(f"{self.record} is unreadable", alert.text)

    def test_a_restarted_browser_alerts_and_the_same_or_no_browser_is_quiet(self):
        self.record.write_text(json.dumps({"pid": 1111, "at": "2026-09-27T07:10:00+00:00"}))
        self.assertEqual(self.check(1111), [])
        self.assertEqual(self.check(None), [])  # closed: the read's own pre-check reports it
        [alert] = self.check(4242)
        self.assertEqual(alert.key, "discord:browser-restarted")
        self.assertIn("pid 1111 → 4242", alert.text)
        self.assertIn("Ask sig to sign in (browse handoff)", alert.text)



class OtherCpu(unittest.TestCase):
    """health.other_cpu: processes the loop didn't start holding this Mac's cores. The table is this Mac's ps on 29 Sep
    13:09-13:20 (trimmed): the loop's engine cache, an orphaned rulecounts review, coders' niced scripts, this main
    session's claude and its shell, the ssh master, the gstack browser, and the system's and sig's own processes
    (Tailscale, fseventsd, Spotlight's mds_stores and its niced mdbulkimport, suggestd, PerfPowerServices)."""

    PS = (DATA.parent / "ps-other-cpu-sample.txt").read_text()
    REPO = Path("/Users/me/Dev/unsw-battlecode-2026")

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    # One reading at `now` of the sample, with Spotlight indexing (mds_stores and mdbulkimport at `spotlight` %CPU
    # each, their real readings replaced) when given.
    def reading(self, now: float, seen: dict, spotlight: float | None = None) -> list:
        rows = health.ps_rows(self.PS.replace("{home}", str(self.state)).replace("{repo}", str(self.REPO)))
        if spotlight is not None:
            rows = [replace(row, cpu=spotlight) if row.pid in (596, 1296) else row for row in rows]
        return health.other_cpu(self.state, self.REPO, now, seen, rows, cores=10)

    def test_only_what_the_loop_and_claude_sessions_did_not_start_is_counted(self) -> None:
        seen: dict = {}
        self.assertEqual(self.reading(NOW, seen), [])  # 0.6 cores: under the share
        (_, held, named), = seen["rows"]
        # Tailscale is left out: it carries the loop's own ssh to its hosts and sig's SSH (NETWORK).
        self.assertEqual(sorted(named), ["PerfPowerServices", "fseventsd", "mds_stores", "opendirectoryd",
                                         "suggestd"])
        self.assertAlmostEqual(held, (20.8 + 9.4 + 0.1 + 16.0 + 6.5 + 6.6 + 0.6) / 100, places=3)

    def test_an_apps_program_name_keeps_its_spaces(self) -> None:
        chrome = next(line for line in self.PS.splitlines() if "Chrome for Testing" in line)
        self.assertEqual(health.program_name(chrome.split(None, 6)[6]), "Google Chrome for Testing")
        self.assertEqual(health.program_name("/Applications/Tailscale.app/Contents/PlugIns/IPNExtension.appex/"
                                             "Contents/MacOS/IPNExtension -AppleLanguages (\"en-AU\")"),
                         "IPNExtension")
        self.assertEqual(health.program_name("/usr/libexec/PerfPowerServices"), "PerfPowerServices")

    def test_a_sustained_spell_alerts_once_naming_the_busiest_and_its_cost_in_game_slots(self) -> None:
        seen: dict = {}
        minutes = int(0.9 * health.OTHER_WINDOW // 60)
        for minute in range(minutes):
            self.assertEqual(self.reading(NOW + 60 * minute, seen, spotlight=150.0), [], minute)
        (alert,) = self.reading(NOW + 60 * minutes, seen, spotlight=150.0)
        self.assertIn("held 3.5 of this Mac's 10 cores", alert.text)
        self.assertIn("about 4 game slots here", alert.text)
        self.assertIn("mds_stores 1.5 (_mds_stores), mdbulkimport 1.5 (zitianqin), fseventsd 0.2", alert.text)
        self.assertNotIn("IPNExtension", alert.text)
        self.assertEqual(self.reading(NOW + 60 * (minutes + 1), seen, spotlight=150.0)[0].key, alert.key)
        # Readings stopping for over OTHER_GAP start the average again: no alert until the window is covered anew.
        self.assertEqual(self.reading(NOW + 60 * minutes + health.OTHER_GAP + 120, seen, spotlight=150.0), [])


class IdleLoop(unittest.TestCase):
    """health.idle_loop (REFLECT 2026-10-01 15:21 F6): this Mac's ps on 1 Oct 15:40 (trimmed): an orphaned `native.py
    games` from 29 Sep and one under a dead coder's shell, both at 0:58 and 0:18 CPU, and a `--once` driver whose
    coder under it keeps working. Only the two idle ones are listed, after IDLE_HOURS unchanged, never before."""

    PS = ("84062     1 02-01:34:25   0:58.99 /Users/z/Dev/bc-wt/E0337/.venv/bin/python /Users/z/Dev/unsw/loop/native.py "
          "games E0337 --a data/champ --b data/opp\n"
          "39947 28018    18:59:36   0:18.99 .venv/bin/python /Users/z/Dev/unsw/loop/native.py games E0433 --a data/arm100\n"
          "28018     1    19:22:10   0:00.00 /bin/zsh -c source snapshot.sh && .venv/bin/python loop/native.py games\n"
          "35792 35787    07:37:58   0:26.38 /Users/z/Dev/unsw/.venv/bin/python3 /Users/z/Dev/unsw/loop/driver.py --once "
          "--handed E0510\n"
          "35800 35792    07:30:00  {coder}   claude -p --name bc-E0510-1\n")

    def test_idle_loop_processes_alert_after_idle_hours(self) -> None:
        seen: dict = {}
        hours = health.IDLE_HOURS * 3600
        self.assertEqual(health.idle_loop(NOW, seen, health.cpu_rows(self.PS.format(coder="1:00.00"))), [])
        later = self.PS.replace("02-01:34:25", "02-07:34:26").replace("18:59:36", "1-00:59:37").replace(
            "19:22:10", "1-01:22:11").replace("07:37:58", "13:37:59").replace("07:30:00", "13:30:01")
        self.assertEqual(health.idle_loop(NOW + hours - 60, seen, health.cpu_rows(later.format(coder="9:00.00"))), [])
        (alert,) = health.idle_loop(NOW + hours + 1, seen, health.cpu_rows(later.format(coder="9:30.00")))
        self.assertEqual(alert.key, "idle loop:84062,39947")
        self.assertIn("pid 84062 (native.py games E0337 --a data/champ --b data/opp, alive 56 h, parent 1)", alert.text)
        self.assertIn("pid 39947 (native.py games E0433", alert.text)
        self.assertNotIn("35792", alert.text)


class CloneCpu(unittest.TestCase):
    """health.clone_cpu: an experiment clone's processes holding over half this Mac's cores. The table is this Mac's ps
    on 30 Sep 10:36 (trimmed): E0405's coder (claude under the driver's sh, named bc-E0405-...) and E0406's orphaned
    data/play.sh running native.py games from its clone, one with an ssh child."""

    PS = (DATA / "ps-clone-cpu-sample.txt").read_text()
    REPO = Path("/Users/me/Dev/unsw-battlecode-2026")

    def reading(self, now: float, seen: dict, games: float) -> list:
        text = self.PS.replace("{clones}", str(health.clones_folder(self.REPO))).replace("{repo}", str(self.REPO))
        rows = [replace(row, cpu=games) if row.pid == 27746 else row for row in health.ps_rows(text)]
        return health.clone_cpu(Path("/nonexistent"), self.REPO, now, seen, rows, cores=10)

    def test_a_clone_over_half_the_cores_through_the_window_alerts_once_a_spell(self) -> None:
        seen: dict = {}
        self.assertEqual(self.reading(NOW, seen, 0.5), [])
        self.assertEqual(seen["rows"][-1][1], {})  # as sampled, 0.5% a clone: under the 0.05 cores kept
        seen = {}
        minutes = int(0.9 * health.CLONE_WINDOW // 60)
        for minute in range(minutes):
            self.assertEqual(self.reading(NOW + 60 * minute, seen, 600.0), [], minute)
        (alert,) = self.reading(NOW + 60 * minutes, seen, 600.0)
        self.assertTrue(alert.key.startswith("clone cpu:E0406:"))
        self.assertIn("held 6.0 of this Mac's 10 cores", alert.text)
        self.assertIn("busiest now (cores): python 6.0", alert.text)
        self.assertEqual(self.reading(NOW + 60 * (minutes + 1), seen, 600.0)[0].key, alert.key)

    def test_a_process_is_its_clones_by_the_folder_or_the_coders_session_name(self) -> None:
        text = self.PS.replace("{clones}", str(health.clones_folder(self.REPO))).replace("{repo}", str(self.REPO))
        by_pid = {row.pid: row for row in health.ps_rows(text)}
        folder = re.compile(re.escape(str(health.clones_folder(self.REPO))) + r"/(E\d{4})(?![\w])")
        self.assertEqual({pid: health.clone_of(pid, by_pid, folder) for pid in (34560, 31420, 27741, 13337)},
                         {34560: "E0405", 31420: "E0406", 27741: None, 13337: None})


class IdeasWrites(unittest.TestCase):
    """health.ideas_writes: ideas-writes.log (meta.log_ideas_write) has a line for the backlog's last write and stays
    under its bound. The log's lines are the live one's of 29 Sep 13:08."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.log = self.state / "ideas-writes.log"
        self.log.write_text((DATA.parent / "ideas-writes-sample.log").read_text())
        self.last = json.loads(self.log.read_text().splitlines()[-1])["at"]
        self.ideas = self.state / "ideas.jsonl"
        self.ideas.write_text("")

    def check(self, written: float, rows: list | None = None) -> list:
        os.utime(self.ideas, (written, written))
        return health.ideas_writes(self.state, written + 600, [] if rows is None else rows)

    def test_a_backlog_write_with_no_line_alerts_unless_an_old_code_writer_runs(self) -> None:
        self.assertEqual(self.check(self.last + 0.05), [])  # the write just after its line
        (alert,) = self.check(self.last + 300)
        self.assertTrue(alert.key.startswith("ideas log:stale:"), alert.key)
        old = health.PsRow(4242, 1, "zitianqin", 0, self.last + 960 - health.IDEAS_LOG_SINCE, 0.0,
                           ".venv/bin/python3 loop/driver.py --once --parent bc-main-fresh")
        self.assertEqual(self.check(self.last + 300, [old]), [])  # started before the logger: pinned old code

    def test_a_missing_or_oversized_log_alerts(self) -> None:
        with self.log.open("a") as handle:
            handle.write("x" * health.IDEAS_LOG_BOUND + "\n")
        self.assertEqual([a.key for a in self.check(self.last)], ["ideas log:size"])
        self.log.unlink()
        self.assertEqual([a.key for a in self.check(self.last)], ["ideas log:missing"])


class MergeTurnFiles(unittest.TestCase):
    """health.merge_turn_files: each merge turn left resolve.json, a fresh one its resolve-<session>.json and a cost in
    merge_turns. The turn's output is E0331's resolve.json (29 Sep 13:08, trimmed), recorded as the driver does."""

    OUT = (DATA.parent / "resolve-E0331-sample.json").read_text()

    def setUp(self) -> None:
        import driver
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.run_dir = self.state / "runs/E0331"
        self.run_dir.mkdir(parents=True)
        self.exp = driver.Experiment(id="E0331", idea={"id": "I1", "title": "t"}, lane=1, candidate="abc")
        self.exp.session.update(session_id="b03250c0-b615-4554-80ec-64e8d5355c9a", resolve_on="02fafc3b")
        self.started = time.time() - 900

    # One merge turn recorded by driver.record_merge_turn, leaving resolve.json (and a fresh turn's copy when `saved`),
    # with experiment.json saved after it.
    def turn(self, path: str, out: str, saved: bool = True) -> list:
        import driver
        session = "5f0c9e1a-0000-4000-8000-000000000000" if path == "fresh" else self.exp.session["session_id"]
        (self.run_dir / "resolve.json").write_text(out)
        name = f"resolve-{session[:8]}.json" if path == "fresh" else None
        if name and saved:
            (self.run_dir / name).write_text(out)
        driver.record_merge_turn(self.exp, out, path, session, 3900.0, self.started, ("a..b", "a..c"), saved=name)
        (self.run_dir / "experiment.json").write_text(json.dumps({"started": self.started, "session": self.exp.session}))
        return health.merge_turn_files(self.state, time.time())

    def test_a_recorded_turn_with_its_files_and_cost_is_quiet(self) -> None:
        self.assertEqual(self.turn("fresh", self.OUT), [])
        self.assertEqual(self.turn("resumed", self.OUT), [])

    def test_a_fresh_turn_without_its_file_or_cost_alerts(self) -> None:
        (alert,) = self.turn("fresh", self.OUT, saved=False)
        self.assertIn("left no resolve-<session>.json (resolve-5f0c9e1a.json)", alert.text)
        self.exp.session["merge_turns"].clear()
        (alert,) = self.turn("fresh", "")  # a turn that printed nothing: no cost
        self.assertIn("has no cost_usd", alert.text)

    def test_turns_a_re_evaluation_carried_from_its_errored_run_are_that_runs(self) -> None:
        # driver.reuse_clone copies E0331's merge_turns into its re-evaluation E0340, whose folder has no resolve file.
        self.assertEqual(self.turn("fresh", self.OUT), [])
        again = self.state / "runs/E0340"
        again.mkdir()
        (again / "experiment.json").write_text(json.dumps({"started": self.started, "session": self.exp.session}))
        self.assertEqual(health.merge_turn_files(self.state, time.time()), [])

    def test_resolve_json_with_no_row_alerts_unless_the_driver_predates_merge_turns(self) -> None:
        (self.run_dir / "resolve.json").write_text(self.OUT)
        os.utime(self.run_dir / "resolve.json", (time.time() - 600, time.time() - 600))
        for started, expected in ((self.started, 1), (health.MERGE_TURNS_SINCE - 60, 0)):
            (self.run_dir / "experiment.json").write_text(json.dumps({"started": started,
                                                                      "session": self.exp.session}))
            with self.subTest(started=started):
                alerts = health.merge_turn_files(self.state, time.time())
                self.assertEqual(len(alerts), expected)
                self.assertTrue(all("has no merge_turns row" in a.text for a in alerts))



class EvaluatorIdle(unittest.TestCase):
    """health.evaluator_idle: the evaluator idle with no candidate waiting, whatever the hosts play. The rows are the
    live utilisation record's of 29 Sep 11:15-11:46 (trimmed to the fields the check reads): E0313's SPRT ended at
    11:21 and nothing was evaluated until E0327 at 11:42, while fillers and coders played."""

    ROWS = [json.loads(line) for line in (DATA.parent / "utilisation-evaluator-idle-sample.jsonl").read_text()
            .splitlines()]

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / throughput.LOG).write_text("".join(json.dumps(row) + "\n" for row in self.ROWS))
        idle_from = next(row["at"] for row in self.ROWS if row["time"] == "11:23")
        ideas = [{"id": "I900", "status": "trying:lane1", "priority": 1, "title": "portal scout", "category": "x"},
                 {"id": "I901", "status": "todo", "priority": 2, "title": "retest E0313 on fresh maps",
                  "category": "retest"},
                 {"id": "I902", "status": "todo", "priority": 1, "title": "a new behaviour", "category": "combat"}]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        run = self.state / "runs/E0319"
        run.mkdir(parents=True)
        (run / "experiment.json").write_text(json.dumps({"idea": {"id": "I900"}, "started": idle_from - 1800}))
        (run / "worker.json").write_text(json.dumps({"status": "coding"}))
        self.at = {row["time"]: row["at"] for row in self.ROWS}

    def test_it_alerts_ten_minutes_into_a_stretch_naming_coders_and_remedies_and_is_quiet_once_one_is_evaluated(self):
        seen: dict = {}
        self.assertEqual(health.evaluator_idle(self.state, self.at["11:30"] + 30, seen), [])  # 7 min in
        (alert,) = health.evaluator_idle(self.state, self.at["11:33"] + 30, seen)
        self.assertEqual(alert.key, f"evaluator idle:{int(self.at['11:23'])}")
        self.assertIn("In flight: I900 E0319 coding 40 min", alert.text)
        self.assertIn("claim first (driver.mark_idea next) I901 (retest, priority 2)", alert.text)
        self.assertNotIn("I902", alert.text)  # it needs coding
        self.assertEqual(health.evaluator_idle(self.state, self.at["11:40"] + 30, seen)[0].key, alert.key)
        self.assertEqual(health.evaluator_idle(self.state, self.at["11:46"] + 30, seen), [])  # E0327 evaluated

    def test_a_stretch_longer_than_the_rows_read_keeps_its_start(self) -> None:
        # Four hours of idle rows, a minute apart, checked every minute: one alert key throughout, though after
        # EVALUATOR_WINDOW the oldest row read moves on each minute (review of 68b6497b).
        start = self.ROWS[0]["at"]
        rows = [{"at": start + 60 * n, "evaluation": None, "waiting": [], "capacity": 72, "busy": 60.0, "idle": 0.17}
                for n in range(4 * 60)]
        (self.state / throughput.LOG).write_text("".join(json.dumps(row) + "\n" for row in rows))
        seen: dict = {}
        keys = {alert.key for row in rows[10:] for alert in health.evaluator_idle(self.state, row["at"] + 30, seen)}
        self.assertEqual(keys, {f"evaluator idle:{int(start)}"})


class OldCode(unittest.TestCase):
    # The loop home at 20:37 on 29 Sep (MISSES 100): v0072 (4e6ee4ff) had two shared sets, the prewarm's of today's mix
    # and one of worldgen 0.5 alone, while E0352 and E0354, claimed before the portal-bet mix merged (18:22), waited
    # with their frozen mixes; E0345 (claimed 14:38, kept 20:36) played v0071's (d9cc962) worldgen 0.5 set with E0354.
    # Each mix, epoch and users list is the real record's. Today's shared mix has changed since (50af1294), so the
    # prewarm's set is named by the mix baselines.json records, as after any mix change.
    def test_a_set_no_experiment_or_the_prewarm_names_alerts_and_the_2037_sets_do_not(self) -> None:
        v0071, v0072, epoch = "d9cc96264744", "4e6ee4ff4a76ba5350b75952b81d34610dfba99a", "unswbc-1.2.0"
        new = "-deathportal0.03125-portalbet0.0625-vault0.03125-worldgen0.5"
        runs = {"E0345": ({"big": 0.3, "worldgen": 0.35}, 1790678180.7),
                "E0352": ({"near": 0.5, "worldgen": 0.25}, 0.0), "E0354": ({"big": 0.4, "worldgen": 0.3}, 0.0)}
        now = 1790678220.0
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for eid, (mix, finished) in runs.items():
                (state / "runs" / eid).mkdir(parents=True)
                (state / "runs" / eid / "experiment.json").write_text(json.dumps(
                    {"id": eid, "finished": finished, "session": {},
                     "evaluation": {"maps": mix, "toolchain": {"epoch": epoch}}}))
            for name, users in ((f"{v0071}-{epoch}-worldgen0.5", ["E0345", "E0354"]),
                                (f"{v0072[:12]}-{epoch}{new}", []), (f"{v0072[:12]}-{epoch}-worldgen0.5", [])):
                (state / "shared-maps" / name / "maps").mkdir(parents=True)
                if users:
                    (state / "shared-maps" / name / "users.json").write_text(json.dumps(users))
            (state / "baselines.json").write_text(json.dumps({"champion": v0072, "epoch": epoch, "maps": {
                "worldgen": 0.5, "portalbet": 0.0625, "deathportal": 0.03125, "vault": 0.03125}}))
            self.assertEqual(health.old_code(state, v0072, now), [])
            self.assertEqual(health.old_code(state, v0071, now), [])  # E0345's mix was frozen at its claim
            # With E0352 and E0354 done, nothing today's code runs names v0072's worldgen-only set.
            for eid in ("E0352", "E0354"):
                path = state / "runs" / eid / "experiment.json"
                path.write_text(path.read_text().replace('"finished": 0.0', '"finished": 1790679000.0'))
            (alert,) = health.old_code(state, v0072, now)
            self.assertEqual(alert.key, f"old-code:{v0072[:12]}-{epoch}-worldgen0.5")
            # An evaluation driver.hand_off couldn't hand to the disk's code, and only while recent.
            path = state / "runs" / "E0352" / "experiment.json"
            path.write_text(json.dumps({**json.loads(path.read_text()),
                                        "session": {"code_current": False, "locked_at": now - 60}}))
            self.assertEqual([a.key for a in health.old_code(state, v0072, now)][0], "old-code:E0352")
            self.assertEqual(len(health.old_code(state, v0072, now + health.OLD_CODE_S)), 1)


class Fresh(unittest.TestCase):
    # A process on older loop code gets the disk's rules from a child (`loop/health.py --json [--quick EVERY]`): the
    # quick checks here, once and then within their minute (none), and None from a child that can't run.
    def test_the_child_process_reports_the_disks_alerts_as_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, repo = Path(directory), Path(health.__file__).resolve().parent.parent
            self.assertIsInstance(health.fresh(state, repo, 50), list)
            self.assertIn("quick_at", json.loads((state / "health.json").read_text()))  # the child's, in this home
            self.assertEqual(health.fresh(state, repo, 50), [])
            self.assertIsNone(health.fresh(state, state, 50))  # no loop/health.py there


class UpkeepTicks(unittest.TestCase):
    """health.upkeep_ticks: 29 Sep 13:45-14:00 every driver ran older loop code, none ticked, v0066 waited unpublished
    and nothing alerted (MISSES 91)."""

    def test_a_stalled_or_hung_tick_alerts_once_while_a_loop_runs_and_a_running_or_recent_one_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, now, late = Path(directory), 1_800_000_000.0, health.UPKEEP_TICK_LATE
            driver = [health.PsRow(4242, 1, "u", 0, 3600.0, 1.0, "python loop/driver.py --once --parent bc-main")]
            record = state / maintenance.tick_record(True)
            self.assertEqual(health.upkeep_ticks(state, now, driver), [])  # no record yet
            record.write_text(json.dumps({"pid": 999_999_999, "started": now - late - 30, "ended": now - late - 5}))
            (alert,) = health.upkeep_ticks(state, now, driver)
            self.assertIn("no upkeep tick since", alert.text)
            self.assertEqual(health.upkeep_ticks(state, now, [replace(driver[0], alive=late - 1)]), [])  # just begun
            self.assertEqual(health.upkeep_ticks(state, now, []), [])  # no loop running
            native = [replace(driver[0], args="python loop/native.py quick E0001")]
            self.assertEqual(health.upkeep_ticks(state, now, native), [])  # a native command runs the other lock
            # A coder's measurement run (native.py games) is no native session: a stale native tick beside it
            # alone stays quiet (1 Oct: nine runs stuck since an outage raised a false alert).
            (state / maintenance.tick_record(False)).write_text(json.dumps(
                {"pid": 999_999_999, "started": now - late - 30, "ended": now - late - 5}))
            games = [replace(driver[0], args="python loop/native.py games E0433 --a data/champ --b data/opp/swarm")]
            self.assertEqual(health.upkeep_ticks(state, now, games), [])
            self.assertEqual(len(health.upkeep_ticks(state, now, native)), 1)
            (state / maintenance.tick_record(False)).unlink()
            self.assertEqual(health.upkeep_ticks(state, now - 10, driver), [])  # within the allowance
            # A tick still running (its pid alive) is a long one, not a missing one, until UPKEEP_TICK_HUNG; its pid
            # gone, it is missing.
            record.write_text(json.dumps({"pid": os.getpid(), "started": now - late - 30, "ended": None}))
            self.assertEqual(health.upkeep_ticks(state, now, driver), [])
            record.write_text(json.dumps({"pid": os.getpid(), "started": now - health.UPKEEP_TICK_HUNG - 1,
                                          "ended": None}))
            (alert,) = health.upkeep_ticks(state, now, driver)
            self.assertIn("has run since", alert.text)
            record.write_text(json.dumps({"pid": 999_999_999, "started": now - late - 30, "ended": None}))
            self.assertEqual(len(health.upkeep_ticks(state, now, driver)), 1)
            (state / "STOP").touch()
            self.assertEqual(health.upkeep_ticks(state, now, driver), [])


# REFLECT 2026-09-30 21:15 F1: sig's goal (30 Sep 19:59: "we need to fix trauma portals and devil. ASAP") drifted with
# nothing acting: a goal map below the others online names the weak-maps study, and a pipeline with nothing aimed at
# the goal for longer than one evaluation alerts.
class Goal(unittest.TestCase):
    def test_a_goal_map_below_the_others_online_names_the_weak_maps_study(self) -> None:
        import evalline
        sample = json.loads((DATA.parent / "map-records-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online.json").write_text(json.dumps(sample["online"]))
            (state / "ladder-maps.json").write_text(json.dumps({"maps": sample["maps"]}))
            for version, index in sample["indexes"].items():
                (state / "online-replays" / version).mkdir(parents=True)
                (state / "online-replays" / version / "index.json").write_text(json.dumps(index))
            self.assertEqual(health.goal_maps(state), [])  # Trauma 2-0-5 is luck across 10 maps
            live = sample["indexes"]["10115"]
            trauma = {k: v for k, v in next(g for g in live["games"] if g["map_id"] == 15).items() if k != "summary"}
            other = {"A": "B", "B": "A"}[trauma["side"]]
            live["results"] += [{**trauma, "id": n, "winner": trauma["side"] if n < 26 else other} for n in range(60)]
            (state / "online-replays/10115/index.json").write_text(json.dumps(live))
            (alert,) = health.goal_maps(state)
            self.assertEqual(alert.key, "goal map:15:10115")
            self.assertIn("GOAL MAP: Trauma", alert.text)
            self.assertIn("weak-maps-2026-09-30.md has its shape", alert.text)
            with patch.object(evalline, "GOAL_MAPS", ("Devil",)):
                self.assertEqual(health.goal_maps(state), [])  # the same record on a map outside the goal

    def test_nothing_in_flight_aimed_at_the_goal_for_an_evaluation_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, now, seen = Path(directory), time.time(), {}
            run = state / "runs/E0427"
            run.mkdir(parents=True)
            (run / "experiment.json").write_text(json.dumps({"idea": {"evaluation": {"maps": {"big": 0.4}}}}))
            with patch.object(health, "recorded_evaluation", side_effect=AssertionError("read")):
                self.assertEqual(health.goal_drift(state, now, {}), [])  # nothing in flight: its seconds not spent
            with patch.object(health, "recorded_evaluation", return_value=1800):
                (run / "worker.json").write_text(json.dumps({"status": "running"}))
                self.assertEqual(health.goal_drift(state, now, seen), [])  # just seen
                (alert,) = health.goal_drift(state, now + 1801, seen)
                self.assertIn("GOAL DRIFT: none of the 1 experiments in flight (E0427)", alert.text)
                (run / "experiment.json").write_text(json.dumps({"idea": {"evaluation": {"maps": {"fortress": 0.5}}}}))
                self.assertEqual(health.goal_drift(state, now + 1802, seen), [])  # a Devil-style mix aims at it
                self.assertEqual(seen, {})


# REFLECT 2026-09-30 21:15 F3: I878 waited on I915, tried with no code left, so its link could never deliver.
class DeadPrerequisites(unittest.TestCase):
    def test_a_prerequisite_closed_without_code_or_missing_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ideas = [{"id": "I878", "status": "todo", "after": ["I915"]}, {"id": "I915", "status": "tried"},
                     {"id": "I1129", "status": "todo", "after": ["I922"]}, {"id": "I922", "status": "followup"},
                     {"id": "I1126", "status": "todo", "after": ["I1125"]}, {"id": "I1125", "status": "todo"},
                     {"id": "I900", "status": "todo", "after": ["I899"]},
                     {"id": "I901", "status": "todo", "after": ["I902"]},
                     {"id": "I902", "status": "merged", "merged_into": "I903"}, {"id": "I903", "status": "kept"}]
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            (alert,) = health.dead_prerequisites(state)
        self.assertIn("2 todo ideas wait on a prerequisite that can never deliver: I878 after I915 (tried, no code "
                      "left); I900 after I899 (not in the backlog)", alert.text)

    # The live backlog on 1 Oct, trimmed: I291 waits on I213, dropped after E0115 was judged promising at +0.9 with its
    # branch cand/E0115 kept, so driver.stack_for stacks E0115 under I291. It alerted as dead (19 of its 20 links).
    def test_a_prerequisite_whose_candidate_stacks_is_not_dead(self) -> None:
        import driver
        ideas = [{"id": "I213", "status": "dropped", "priority": 1, "title": "No even trades while the swarm "
                  "outnumbers us"},
                 {"id": "I291", "status": "todo", "priority": 2, "after": ["I213"], "source": "postmortem:E0115",
                  "title": "Avoid, not just decline: while E0115's met-id guard refuses an even ram"}]
        candidate = "ffa292245b1cbde1b87ed7d3d20f4c680e5c611e"
        result = {"id": "E0115", "idea": {"id": "I213", "title": ideas[0]["title"]}, "verdict": "promising",
                  "candidate": candidate, "base": "2c79f81d0d90038543c82dff930ce243cc4b16e8",
                  "sprt": {"elo": 0.935}, "cpu": {"ok": True}, "bot_tests": {"ok": True}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(driver, "git", side_effect=lambda *args, **kwargs: candidate
                             if args[-1] == "refs/heads/cand/E0115" else ""), \
                patch.object(driver, "in_champion", return_value=False):
            state = Path(directory)
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            self.assertEqual(health.dead_prerequisites(state), [])
            (state / "results.jsonl").write_text(json.dumps({**result, "verdict": "discarded",
                                                             "sprt": {"elo": -4.1}}) + "\n")
            ideas[1]["source"] = "audit"  # a measured loss stacks only under a follow-up built on it (corrects)
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            (alert,) = health.dead_prerequisites(state)
            self.assertIn("I291 after I213 (dropped, no code left)", alert.text)

    # I1153 waits on I1144, tried after E0440 was discarded with no estimate (the one real dead link on 1 Oct); a
    # retest of E0440 in flight, as a lane's idea or a live queue ticket's, can still deliver.
    def test_a_prerequisite_being_retested_is_not_dead(self) -> None:
        ideas = [{"id": "I1144", "status": "tried", "priority": 1, "title": "Don't feed their majority"},
                 {"id": "I1153", "status": "todo", "priority": 1, "after": ["I1144"], "source": "postmortem:E0440",
                  "title": "Retreat to numbers"}]
        retest = {"id": "I1200", "status": "trying:lane3", "priority": 1, "title": "Retest E0440 alone on the current "
                  "champion"}
        result = {"id": "E0440", "idea": {"id": "I1144", "title": ideas[0]["title"]}, "verdict": "discarded",
                  "candidate": "6cc387fe02b6d1b35c029cad2f6db19b7732533d",
                  "base": "4e7a7a7258dd9674d7e70d6910faf527caa9f029", "sprt": {}}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "results.jsonl").write_text(json.dumps(result) + "\n")
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            (alert,) = health.dead_prerequisites(state)
            self.assertIn("I1153 after I1144 (tried, no code left)", alert.text)
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in [*ideas, retest]))
            self.assertEqual(health.dead_prerequisites(state), [])
            (state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
            (state / meta.QUEUE).mkdir()
            (state / meta.QUEUE / "1-0001790774951.957-E0460.json").write_text(json.dumps(
                {"pid": os.getpid(), "started": meta.process_started(os.getpid()), "experiment": "E0460"}))
            (state / "runs/E0460").mkdir(parents=True)
            (state / "runs/E0460/experiment.json").write_text(json.dumps({"idea": {**retest, "status": "tried"}}))
            self.assertEqual(health.dead_prerequisites(state), [])  # its lane record gone, its ticket still live


# REFLECT 2026-09-30 21:15 F4: this Mac hibernated 19:28-19:43:44 on a flat battery (the real power log, trimmed) while
# E0426's SPRT ran (the utilisation row at 19:27, trimmed); the 14:24 clamshell sleep had no loop row before it here.
class MacSleeps(unittest.TestCase):
    def test_a_sleep_with_work_in_flight_alerts_naming_the_work(self) -> None:
        text = (DATA.parent / "pmset-sample.txt").read_text()
        woke = datetime.fromisoformat("2026-09-30T19:43:44+10:00").timestamp()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / throughput.LOG).write_text(json.dumps({
                "at": 1790760425.0, "evaluation": {"experiment": "E0426", "stage": "SPRT"},
                "waiting": ["E0398", "E0399", "E0427", "E0430", "E0431"], "coding": ["E0429", "E0432"]}) + "\n")
            (alert,) = health.mac_sleeps(state, woke + 600, text)
            # out of the window by the time its alert may repeat, which the full check's hourly runs would re-send
            self.assertEqual(health.mac_sleeps(state, woke + 600 + health.REPEAT, text), [])
        self.assertIn("this Mac slept from 19:28:24 to 19:43:44 (15 min; Entering Sleep state due to 'Low Power "
                      "Sleep'", alert.text)
        self.assertIn("stalling the evaluation of E0426 (SPRT); coding E0429, E0432; 5 queued", alert.text)


# REFLECT 2026-09-30 21:15 F5: hosts that kept resting went unalerted. Rests most hosts share are this Mac's.
class HostRests(unittest.TestCase):
    def test_a_host_resting_twice_on_its_own_in_one_evaluation_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, now = Path(directory), time.time()
            since = now - 3600
            known = {"az-my": {"rests": [since - 60, since + 600, since + 1500], "connects": [[0, 27.4]] * 5},
                     "mm": {"rests": [since + 2400]}, "ll": {"rests": [since + 2410]}, "mm1": {"rests": [since + 2420]},
                     "az-id": {"rests": [since + 2430]}}
            (state / "host-connects.json").write_text(json.dumps(known))
            marker = evaluating_marker()
            marker.write_text(json.dumps({"pid": os.getpid(), "since": since}))
            self.addCleanup(marker.unlink, missing_ok=True)
            (alert,) = health.host_rests(state, now)
            self.assertEqual(alert.key, f"host rests:az-my:{int(since)}")
            self.assertIn("HOST RESTING: az-my rested 2 times", alert.text)
            self.assertIn("47 s (p95 of 5 connects 27.4 s)", alert.text)
            known["az-my"]["rests"] += [since + 2440, since + 610, since + 640]  # beside the others': this Mac's;
            # and within REST_TOGETHER of its own: the first outage, seen by other game processes
            (state / "host-connects.json").write_text(json.dumps(known))
            self.assertIn("rested 2 times", health.host_rests(state, now)[0].text)
            (state / "host-connects.json").write_text("[]")
            self.assertEqual([a.key for a in health.host_rests(state, now)], ["host connects"])


# E0429's SPRT played while health called it lock-less at 21:20 on 30 Sep (as E0414 at 15:10, E0426 at 20:14): a
# candidate whose SPRT is playing (evaluation_live) is under evaluation whatever the marker says (MISSES 108).
class TicketlessWhilePlaying(unittest.TestCase):
    def test_a_candidate_whose_sprt_is_playing_is_not_ticketless(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            run = state / "runs/E0429"
            run.mkdir(parents=True)
            (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": os.getpid()}))
            (run / "experiment.json").write_text(json.dumps({"id": "E0429", "lane": 5}))
            now = time.time() + health.TICKETLESS_S + 60
            self.assertEqual(len(health.ticketless(state, now)), 1)
            (run / "sprt-progress.json").write_text("{}")
            os.utime(run / "sprt-progress.json", (now - 60, now - 60))
            self.assertEqual(health.ticketless(state, now), [])

if __name__ == "__main__":
    unittest.main()
