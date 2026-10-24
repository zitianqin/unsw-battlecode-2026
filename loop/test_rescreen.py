"""The late screen: v0028 (E0102, its recorded result in testdata/result-promoted-sample.json) was promoted without the
screen's `earlyking`, added to referee/gauntlet.toml after it was claimed. Its missing entries come from main's real
gauntlet.toml, its baseline is the champion before the earliest promotion that skipped the entry, its games are scored
like the screen on the result's own toolkit, a clear regression alerts and queues an idea, it runs beside evaluations,
and health.py alerts only when nothing started, a run hangs or runs keep failing."""

import testhome  # before anything that reads live paths
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import rescreen
import sweep

DATA = Path(__file__).resolve().parent / "testdata"
GAME = json.loads((DATA / "sweep-game.jsonl").read_text())  # a real screen game row (E0089's first swarm game)
ADDED = 1790391311  # 8738665, 2026-09-26 12:55: the merge that brought earlyking to main (its branch commit: 12:37)
PREVIOUS = "36773e611e674e59a615b6bc54190b5b0ba9a604"  # v0027 (E0101), which E0102 was tested against


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class LateScreen(unittest.TestCase):
    def setUp(self) -> None:
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})  # see test_health
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.repo, self.state = root / "repo", root / "state"
        (self.repo / "referee").mkdir(parents=True)
        self.state.mkdir()
        # The real gauntlet.toml as of the merge that added earlyking (8738665), so later entries don't change the case.
        (self.repo / "referee/gauntlet.toml").write_text(git(driver.REPO, "show", "8738665:referee/gauntlet.toml"))
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        git(self.repo, "branch", "loop/champion")
        self.champion = git(self.repo, "rev-parse", "loop/champion")
        self.row = json.loads((DATA / "result-promoted-sample.json").read_text()) | {"candidate": self.champion}
        self.write([self.row])
        (self.state / "ideas.jsonl").write_text("")

    def write(self, rows: list[dict]) -> None:
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_the_entries_its_screen_never_played_are_missing(self) -> None:
        self.assertEqual(rescreen.missing(self.state, self.repo), (self.champion, self.row, ["earlyking"]))
        rescreen.save(self.state, {self.champion: {"done": {"earlyking": {}}}})
        self.assertIsNone(rescreen.missing(self.state, self.repo))
        self.write([{**self.row, "idea": {"kind": "speed"}}])
        rescreen.save(self.state, {})
        self.assertIsNone(rescreen.missing(self.state, self.repo))  # same moves as its predecessor
        # v0070 (E0350, 29 Sep): kept on its predecessor's machine code, its real row's identity; it played all 25.
        same = {"ok": True, "same_code": True, "line": "same machine code as the base but for the build's sonar key "
                "(479248 bytes of wasm with the key fixed)"}
        self.write([{**self.row, "idea": {"kind": "identical"}, "identity": same}])
        self.assertIsNone(rescreen.missing(self.state, self.repo))
        self.write([{**self.row, "idea": {"kind": "identical"}, "identity": {**same, "same_code": False}}])
        self.assertEqual(rescreen.missing(self.state, self.repo)[2], ["earlyking"])
        self.write([{**self.row, "idea": {"kind": "identical"}, "identity": None}])  # a row whose check never ran
        self.assertEqual(rescreen.missing(self.state, self.repo)[2], ["earlyking"])  # its identical check alone
        # A same-code champion over E0102 owes what E0102 still owed, screened from E0102's row (so its baseline is
        # never the identical predecessor), and what was played for E0102 counts.
        v0028 = {**self.row, "candidate": "b" * 40}
        over = {"id": "E0999", "verdict": "kept", "candidate": self.champion, "base": v0028["candidate"],
                "evaluation": {}, "idea": {"kind": "identical"}, "identity": same}
        self.write([v0028, over])
        self.assertEqual(rescreen.missing(self.state, self.repo), (self.champion, v0028, ["earlyking"]))
        rescreen.save(self.state, {v0028["candidate"]: {"done": {"earlyking": {}}}})
        self.assertIsNone(rescreen.missing(self.state, self.repo))

    def test_the_baseline_is_the_champion_before_every_promotion_that_skipped_the_entry(self) -> None:
        # E0101 promoted 36773e6 before earlyking existed; E0102 after it, without it.
        e0101 = {"id": "E0101", "verdict": "kept", "candidate": PREVIOUS, "base": "c7f3fb4", "evaluation": {},
                 "finished": ADDED - 1500, "pool": {"opponents": [{"name": "breeder"}]}}
        self.write([e0101, self.row])
        self.assertEqual(rescreen.baseline(self.state, self.row, "earlyking", ADDED), PREVIOUS)
        self.write([{**e0101, "finished": ADDED + 60}, self.row])  # had it joined first, E0101 skipped it too
        self.assertEqual(rescreen.baseline(self.state, self.row, "earlyking", ADDED), "c7f3fb4")
        self.write([{**e0101, "finished": ADDED + 60, "pool": {"opponents": [{"name": "earlyking"}]}}, self.row])
        self.assertEqual(rescreen.baseline(self.state, self.row, "earlyking", ADDED), PREVIOUS)
        self.assertEqual(rescreen.entry_added(driver.REPO, "earlyking"), ADDED)  # from main's real history

    def test_an_entry_joins_main_when_its_branch_is_merged_not_when_it_was_committed(self) -> None:
        dated = lambda at: {**os.environ, "GIT_COMMITTER_DATE": f"@{at} +1000"}  # noqa: E731  fixture dates only
        run = lambda *args, at: subprocess.run(["git", *args], cwd=self.repo, check=True,  # noqa: E731
                                               capture_output=True, env=dated(at))
        run("checkout", "-qb", "earlyking", at=0)
        with (self.repo / "referee/gauntlet.toml").open("a") as toml:
            toml.write('\n[[opponent]]\nname = "latecomer"\ncommit = "0000000"\n')
        run("commit", "-qam", "add latecomer", at=1000)
        run("checkout", "-q", "main", at=0)
        (self.repo / "other.txt").write_text("meanwhile on main\n")
        run("add", "other.txt", at=0)
        run("commit", "-qm", "unrelated", at=2000)
        run("merge", "-q", "--no-ff", "-m", "merge latecomer", "earlyking", at=5000)
        self.assertEqual(rescreen.entry_added(self.repo, "latecomer"), 5000.0)
        self.assertEqual(rescreen.entry_added(self.repo, "never"), 0.0)

    def play(self, winners: dict[str, str], toolchain: str | None = sys.executable) -> dict:
        maps = [f"/m/{i}.map" for i in range(3)]
        rows = lambda winner: [dict(GAME, map=m, a_side=s, winner=winner) for m in maps for s in "AB"]  # noqa: E731
        self.row["evaluation"]["toolchain"] = {"python": toolchain}
        self.write([self.row])
        seen: dict = {}

        # Both bots' screens of an entry come as one referee run (driver.play_screens), the champion's first.
        def games(screens, yielding=False, status=None):
            self.assertTrue(yielding)  # its games step back while an evaluation runs
            self.assertEqual(status, self.state / "rescreens" / self.champion / "remote-status.json")
            self.assertEqual([screen.bot.name.split("-")[0] for screen in screens], ["candidate", "base"])
            found = []
            for screen in screens:
                self.assertEqual(screen.maps, maps)  # the late screen keeps full screens
                seen[screen.bot.name.split("-")[0]] = (screen.shared, driver.RUNTIME_PYTHON.get())
                found.append(rows(winners[screen.bot.name.split("-")[0]]))
                screen.out.write_text("".join(json.dumps(r) + "\n" for r in found[-1]))
            return found

        bot = lambda spec, into=None: Path(f"{spec['name']}-{spec['commit'][:7]}")  # noqa: E731
        with patch.object(driver, "gauntlet_bot", side_effect=bot), \
                patch.object(driver, "play_screens", side_effect=games), \
                patch.object(rescreen, "screen_maps", return_value=(maps, True)), \
                patch.object(driver, "loadable_maps", side_effect=lambda maps, spares, note: maps), \
                patch("maintenance.toolchain", return_value={"python": "/current/python"}):
            (self.state / "rescreens" / self.champion).mkdir(parents=True, exist_ok=True)
            rescreen.run(self.repo, self.state)
        return seen

    def test_it_replays_the_screens_own_maps_where_they_differ_from_the_sprts(self) -> None:
        # A map mix's SPRT played its own maps and its screen general ones (driver.screen_maps).
        run = self.state / "runs" / self.row["id"]
        run.mkdir(parents=True)
        maps = [run / name for name in ("sprt.map", "screen1.map", "screen2.map")]
        for path in maps:
            path.write_text("MAP_NAME x\n")
        names = list(map(str, maps))
        (run / "evaluation.json").write_text(json.dumps({"maps": names[:1], "screen_maps": names[1:]}))
        self.assertEqual(rescreen.screen_maps(self.state, self.row, self.state / "late", 50), (names[1:], True))
        (run / "evaluation.json").write_text(json.dumps({"maps": names}))  # saved before screen_maps
        self.assertEqual(rescreen.screen_maps(self.state, self.row, self.state / "late", 2), (names[:2], True))

    def test_a_champion_clearly_worse_against_it_alerts_and_queues_a_fix_but_stays(self) -> None:
        seen = self.play({"candidate": "b", "base": "a"})
        done = rescreen.read(self.state)[self.champion]["done"]["earlyking"]
        self.assertEqual((done["candidate"], done["base"], done["worse"], done["baseline"]), (0.0, 1.0, True, PREVIOUS))
        # The baseline's cached games on its shared maps came from the row's toolkit, which is still installed.
        self.assertEqual(seen["base"], (Path(self.row["evaluation"]["shared_maps"]), sys.executable))
        alert = (self.state / "host-alerts.log").read_text()
        self.assertIn("rescreen champion", alert)
        self.assertIn("against the screen's earlyking, which joined after E0102 was claimed: 0.00 vs 1.00 over 6", alert)
        idea = json.loads((self.state / "ideas.jsonl").read_text())
        self.assertEqual((idea["priority"], idea["source"]), (1, f"rescreen:{self.champion[:7]}"))
        self.assertIn("earlyking", idea["title"])
        self.assertIsNone(rescreen.missing(self.state, self.repo))
        self.assertEqual(git(self.repo, "rev-parse", "loop/champion"), self.champion)  # never demoted

    def test_level_results_are_recorded_quietly_and_a_gone_toolkit_plays_everything_fresh(self) -> None:
        seen = self.play({"candidate": "a", "base": "a"}, toolchain="/gone/python")
        self.assertFalse(rescreen.read(self.state)[self.champion]["done"]["earlyking"]["worse"])
        self.assertFalse((self.state / "host-alerts.log").exists())
        self.assertEqual((self.state / "ideas.jsonl").read_text(), "")
        self.assertEqual(seen["base"], (None, "/current/python"))

    # A draw its baseline also drew on the same platform (driver.weak_losses) is logged naming the champion, not
    # alerted: it isn't the champion's own (TestWeakLosses in test_driver has the alerted case).
    def test_a_loss_or_draw_to_the_starter_is_logged_naming_the_champion(self) -> None:
        with (self.repo / "referee/gauntlet.toml").open("a") as toml:
            toml.write('\n[[opponent]]\nname = "starter"\ncommit = "f99ce6b"\n')
        git(self.repo, "commit", "-qam", "add starter")
        with patch.object(driver, "log") as log:
            self.play({"candidate": "draw", "base": "draw"})  # level with its baseline against both: nothing is worse
        done = rescreen.read(self.state)[self.champion]["done"]
        self.assertEqual(sorted(done), ["earlyking", "starter"])
        self.assertFalse(done["starter"]["worse"])
        (said,) = [c.args[0] for c in log.call_args_list if "starter bot" in c.args[0]]  # the starter's only
        self.assertIn(f"WARNING the late screen of champion {self.champion[:7]} ({self.row['id']}) lost or drew 6 "
                      "of 6 screen games against starter", said)
        self.assertIn("the base lost too there: 0 as A (drawn,", said)
        self.assertFalse((self.state / "host-alerts.log").exists())
        self.assertEqual((self.state / "ideas.jsonl").read_text(), "")  # a report, not a fix idea

    def test_a_stop_marker_or_a_new_champion_stops_it_between_entries(self) -> None:
        (self.state / "STOP").write_text("")
        self.play({"candidate": "a", "base": "a"})
        self.assertEqual(rescreen.read(self.state)[self.champion].get("done", {}), {})
        (self.state / "STOP").unlink()
        with patch.object(rescreen.meta, "champion_commit", return_value="0" * 40):
            self.assertIsNone(rescreen.missing(self.state, self.repo))  # a new champion: another record's work

    def test_tick_launches_beside_evaluations_but_not_twice_or_under_a_stop_marker(self) -> None:
        now = time.time()
        driver.EVALUATING.write_text(json.dumps({"pid": os.getpid(), "since": now}))  # an evaluation is running
        self.addCleanup(driver.EVALUATING.unlink, missing_ok=True)
        with patch.object(rescreen, "launch", return_value=4242) as launch:
            self.assertIn("earlyking launched", rescreen.tick(self.repo, self.state, now))
            with patch.object(rescreen.meta, "alive", return_value=True):
                self.assertIsNone(rescreen.tick(self.repo, self.state, now + 60))
            with patch.object(rescreen.meta, "alive", return_value=False):  # died without finishing
                self.assertIsNone(rescreen.tick(self.repo, self.state, now + 120))
                self.assertIn("launched", rescreen.tick(self.repo, self.state, now + 120 + rescreen.RETRY_S))
                (self.state / "STOP").write_text("")
                self.assertIsNone(rescreen.tick(self.repo, self.state, now + 240 + 2 * rescreen.RETRY_S))
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(rescreen.read(self.state)[self.champion]["failures"], 1)

    def test_a_run_pid_owned_by_someone_else_counts_as_alive(self) -> None:
        with patch.object(rescreen.meta.os, "kill", side_effect=PermissionError):  # tick then launches no second run
            self.assertTrue(rescreen.meta.alive(4242))

    def test_it_starts_beside_a_tune(self) -> None:
        # native.host_share gives a tune playing its games the filler share while an evaluation runs and the late screen
        # its usual share otherwise, so a tune no longer holds the late screen back.
        (self.state / "ideas.jsonl").write_text(json.dumps({"id": "I9", "kind": "tune", "status": "trying:lane3"}) + "\n")
        with patch.object(rescreen, "launch", return_value=4242) as launch:
            self.assertIn("launched", rescreen.tick(self.repo, self.state, time.time()))
        launch.assert_called_once()

    def test_the_tick_reads_the_results_again_only_when_they_change(self) -> None:
        # results.jsonl is 66 MB live and the upkeep ticks every minute.
        with patch.object(rescreen, "missing", wraps=rescreen.missing) as found, \
                patch.object(rescreen, "launch", return_value=4242), \
                patch.object(rescreen.meta, "alive", return_value=True):
            rescreen.tick(self.repo, self.state, time.time())  # launches, which rewrites rescreen.json: an input
            found.reset_mock()
            for _ in range(3):
                rescreen.tick(self.repo, self.state, time.time())
            self.assertEqual(found.call_count, 1)
            self.write([self.row, {**self.row, "id": "E0103", "verdict": "discarded", "candidate": "x"}])
            rescreen.tick(self.repo, self.state, time.time())
            self.assertEqual(found.call_count, 2)

    def test_health_says_running_while_alive_and_alerts_only_when_stuck(self) -> None:
        seen: dict = {}
        now = time.time()
        check = lambda at: [a.key for a in health.late_screen(self.state, self.repo, at, seen)]  # noqa: E731
        self.assertEqual(check(now), [])
        self.assertEqual(check(now + health.LATE_SCREEN_GRACE + 60), ["late-screen"])  # nothing ever started
        rescreen.save(self.state, {self.champion: {"pid": 4242, "started_at": now, "progress_at": now}})
        with patch.object(rescreen.meta, "alive", return_value=True):
            self.assertEqual(check(now + rescreen.STALE - 60), [])
            alerts = health.late_screen(self.state, self.repo, now + rescreen.STALE + 60, seen)
            self.assertEqual([a.key for a in alerts], ["late-screen:stale"])
            self.assertIn("is running since", alerts[0].text)
        rescreen.save(self.state, {self.champion: {"started_at": now, "failures": 2, "failed_at": now, "error": "boom"}})
        self.assertEqual(check(now + 60), ["late-screen:failed"])
        rescreen.save(self.state, {self.champion: {"done": {"earlyking": {}}}})
        self.assertEqual(check(now + 2 * health.LATE_SCREEN_GRACE), [])
        self.assertEqual(seen, {})

    def test_the_sweep_starts_beside_it(self) -> None:
        # 29 Sep 18:42-19:40: v0070's late screen held the sweep and the adversarial-map search back through E0349's
        # stages on this Mac, and its half of each host was all that played. It keeps that half while it asks for
        # slots (native.host_share), and the sweep takes the rest.
        rescreen.save(self.state, {self.champion: {"pid": 4242}})
        sweep.save(self.state, {"champion": self.champion})
        with patch.object(rescreen.meta, "alive", return_value=True), patch.object(sweep, "busy", return_value=None), \
                patch.object(sweep.meta, "champion_commit", return_value=self.champion), \
                patch.object(sweep, "launch", return_value=4343) as launch:
            self.assertIn("launched", sweep.tick(self.repo, self.state))
        launch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
