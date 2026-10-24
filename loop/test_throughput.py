"""The utilisation record and stage timing (loop/throughput.py) on samples captured from the real files:
loop/testdata/throughput/e0127-screen.json holds the minute snapshots of the host status files taken during
E0127's opponent screen on 2026-09-27 (00:13-00:39), when the screen's serial batches left sz and the eight az-*
hosts idle most minutes (MISSES 35), with E0127's run-folder file times."""

import testhome  # before anything that reads live paths
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

import health
import throughput
from home import evaluating_marker, loop_home

DATA = Path(__file__).resolve().parent / "testdata/throughput/e0127-screen.json"
FIXTURE = json.loads(DATA.read_text())
# utilisation.jsonl's rows around E0163's SPRT on 2026-09-27 (14:08-14:40), which stalled every other minute, and the
# games in flight each minute when its games are replayed without the scheduler's stall (the file's note says how).
E0163 = json.loads((DATA.parent / "e0163-sprt.json").read_text())
QUIET = {"load": 0.0, "memory_gb": 16.0}  # this Mac under its load gate, with memory to spare


# A loop home laid out as the capture saw it: remote.json, the load allowance, the hosts reported down, and E0127's
# run folder with its files' times (a time set before a file's birth moves its birth there too, on macOS).
def home_as_captured(state: Path) -> None:
    (state / "remote.json").write_text(json.dumps(FIXTURE["remote.json"]))
    (state / "load-fraction").write_text(FIXTURE["load-fraction"])
    (state / "host-outages.json").write_text(json.dumps(FIXTURE["host-outages.json"]))
    run = state / "runs" / FIXTURE["run"]["id"]
    (run / "cand").mkdir(parents=True)
    for name, at in FIXTURE["run"]["times"].items():
        path = run / name
        if not path.exists():
            path.write_text("{}")
        os.utime(path, (at, at))
    for name in ("experiment.json", "worker.json"):
        (run / name).write_text(json.dumps(FIXTURE["run"][name]))
    os.utime(run / "worker.json", (FIXTURE["minutes"][0]["at"],) * 2)


# A minute's evaluation marker, its pid swapped for this process's: the captured evaluation's process has exited, and
# a marker whose process is gone counts as none (home.evaluation_running).
def live_marker(minute: dict) -> dict:
    return {**minute["files"]["evaluating.json"], "pid": os.getpid()}


# Samples each captured minute in turn, the status files and the evaluation marker as they were then; this Mac under
# its load gate with memory to spare.
def replay(state: Path, **marker) -> list[dict]:
    rows = []
    for minute in FIXTURE["minutes"]:
        for name in ("remote-status.json", "remote-status-sweep.json"):
            if name in minute["files"]:
                (state / name).write_text(json.dumps(minute["files"][name]))
        if "evaluating.json" in minute["files"]:
            evaluating_marker().write_text(json.dumps({**live_marker(minute), **marker}))
        else:
            evaluating_marker().unlink(missing_ok=True)
        rows.append(throughput.sample(state, now=minute["at"], cores=FIXTURE["cores"], **QUIET))
    return rows


# The sampler before 9e928681 (and before rows had `stale`), as long-lived drivers still run it: under the record's
# lock, it appends a row when the last one is SAMPLE_S - 5 old or more.
def old_sample(state: Path, now: float) -> None:
    path = state / throughput.LOG
    with open(state / f"{throughput.LOG}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = throughput.last_row(path)
        if previous and abs(now - previous.get("at", 0)) < throughput.SAMPLE_S - 5:
            return
        with path.open("a") as handle:
            handle.write(json.dumps({"at": round(now, 1), "time": time.strftime("%H:%M", time.localtime(now))}) + "\n")


class Utilisation(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loop_home()
        for path in self.state.iterdir():
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        home_as_captured(self.state)

    def test_tonights_screen_left_most_of_the_machines_idle_and_health_says_so(self) -> None:
        rows = replay(self.state)
        self.assertTrue(all(row is not None for row in rows))
        self.assertIsNone(rows[0]["idle"])  # nothing to count finished games against yet
        self.assertEqual(rows[5]["evaluation"], {"experiment": "E0127", "stage": "screen"})
        self.assertEqual(rows[5]["hosts"]["zz"], {"capacity": 0, "down": True, "busy": 0.0})  # off overnight
        spells = throughput.idle_spells(rows)
        self.assertEqual([s["cause"] for s in spells], ["E0127:screen"])
        self.assertGreaterEqual(spells[0]["minutes"], throughput.IDLE_MINUTES)
        self.assertTrue({"sz", "az-syd", "az-mel", "az-nz", "az-jpw"} <= set(spells[0]["idle_hosts"]),
                        spells[0]["idle_hosts"])
        self.assertNotIn("mm", spells[0]["idle_hosts"])
        alerts = health.utilisation(self.state, rows[-1]["at"] + 30, health.REPO)
        self.assertEqual([a.key for a in alerts], ["idle:E0127:screen"])
        text = alerts[0].text
        for part in ("E0127", "screen", "sz", "az-syd", "estimated"):
            self.assertIn(part, text)
        self.assertNotIn("fillers now", text)  # a host stage: the fillers step back for it

    def test_a_stage_marked_local_says_so_in_its_rows_and_its_idle_alert(self) -> None:
        # The same minutes as though the holder had marked them local (driver.host_stage): the hosts were the
        # fillers' then, so the idle alert asks why they didn't play (MISSES 50).
        rows = replay(self.state, hosts=False)
        self.assertEqual(rows[5]["evaluation"], {"experiment": "E0127", "stage": "screen", "local": True})
        (alert,) = health.utilisation(self.state, rows[-1]["at"] + 30)
        self.assertIn("in a stage that plays only on this Mac, with the hosts free for the coders and fillers",
                      alert.text)
        # Given the repo it says what each filler is doing now, so the alert tells a filler that should have played
        # from one with nothing to play (idle_any's lines); a host stage's alert doesn't.
        with unittest.mock.patch.object(health, "git", return_value="c" * 40):
            (alert,) = health.utilisation(self.state, rows[-1]["at"] + 30, health.REPO)
            self.assertIn("; fillers now: sweep no record yet; prewarm no record yet; adversarial maps no record yet",
                          alert.text)

    def test_a_stage_idle_every_other_minute_alerts_on_its_mean(self) -> None:
        rows = E0163["rows"]
        sprt = [r for r in rows if throughput.cause(r) == "E0163:SPRT"]
        streak = longest = 0
        for row in sprt:  # no IDLE_MINUTES in a row at 0.4 idle, so a rule on every minute never saw it
            streak = streak + 1 if row["idle"] >= 0.4 else 0
            longest = max(longest, streak)
        self.assertLess(longest, 3)
        (spell,) = throughput.idle_spells(rows)
        self.assertEqual(spell["cause"], "E0163:SPRT")
        self.assertGreaterEqual(spell["minutes"], throughput.IDLE_MINUTES)
        self.assertGreaterEqual(spell["idle"], throughput.IDLE_SHARE)
        with (self.state / throughput.LOG).open("w") as handle:
            handle.writelines(json.dumps(row) + "\n" for row in rows)
        (alert,) = health.utilisation(self.state, rows[-1]["at"] + 30)
        self.assertEqual(alert.key, "idle:E0163:SPRT")
        self.assertIn("idle on average over", alert.text)
        # Fewer minutes than IDLE_MINUTES, however idle, are no spell.
        self.assertEqual(throughput.idle_spells(sprt[:throughput.IDLE_MINUTES - 1]), [])

    def test_the_same_stage_without_its_stall_raises_no_alert(self) -> None:
        sprt = [r for r in E0163["rows"] if throughput.cause(r) == "E0163:SPRT"]
        filled = []
        for row, busy in zip(sprt, E0163["after_fix_busy"]):  # the replayed run's share of each real row's capacity
            games = round(row["capacity"] * busy / 60, 1)
            filled.append({**row, "busy": games, "idle": round(max(0.0, 1 - games / row["capacity"]), 3)})
        self.assertEqual(throughput.idle_spells(filled), [])

    def test_a_busy_minute_by_the_referees_own_counts_is_not_idle(self) -> None:
        # The referee's status (referee/run.py Scheduler.save) now says how many games are in flight: E0127's
        # 00:14 snapshot with every host full is 0% idle, whatever the games finished.
        minute = FIXTURE["minutes"][1]
        status = dict(minute["files"]["remote-status.json"])
        remote = {h["name"]: h["max"] for h in FIXTURE["remote.json"]["hosts"]}
        status.update(running=9, active={n: m for n, m in remote.items()}, cores={n: m // 2 for n, m in remote.items()},
                      updated=minute["at"] - 5)
        (self.state / "remote-status.json").write_text(json.dumps(status))
        evaluating_marker().write_text(json.dumps(live_marker(minute)))
        row = throughput.sample(self.state, now=minute["at"], cores=FIXTURE["cores"], **QUIET)
        self.assertEqual(row["idle"], 0.0)
        self.assertFalse(row["estimated"])
        self.assertEqual(row["hosts"]["mm"]["by"], {"evaluation": 20.0})
        self.assertEqual(throughput.current_use(self.state, minute["at"] + 10), f"hosts {row['busy']:.0f}/{row['capacity']} slots busy")

    def test_a_status_file_that_cant_be_read_is_named_in_the_idle_alert(self) -> None:
        clone = self.state / "clones/E0131/.git"
        clone.mkdir(parents=True)
        (clone / "bc-remote-status.json").write_text("[" * 100_000 + "]" * 100_000)  # too deep to parse
        rows = replay(self.state)
        self.assertEqual(rows[5]["unreadable"], ["clone/E0131: RecursionError"])
        (alert,) = health.utilisation(self.state, rows[-1]["at"] + 30)
        self.assertIn("unreadable status files: clone/E0131: RecursionError", alert.text)

    def test_counts_are_capped_at_what_each_machine_can_hold(self) -> None:
        now = FIXTURE["minutes"][5]["at"]
        ceilings = {h["name"]: h["max"] for h in FIXTURE["remote.json"]["hosts"]}
        # An int too big for a float is dropped alone; the rest of the file is kept.
        clean = throughput.cleaned({"updated": now, "games": 10 ** 400, "active": {"mm": 3}, "running": 500,
                                    "hosts": {"mm": {"played": 10 ** 400}}}, True, ceilings, now, 10)
        self.assertEqual((clean["games"], clean["active"], clean["running"], clean["hosts"]["mm"]["played"]),
                         (0, {"mm": 3.0}, 10.0, 0))
        # A file without in-flight counts claiming thousands of games finished in a minute: at most each host's max.
        clone = self.state / "clones/E0131/.git"
        clone.mkdir(parents=True)
        status = {"updated": now - 5, "games": 9000, "here": 9000, "hosts": {"az-syd": {"played": 9000}}}
        (clone / "bc-remote-status.json").write_text(json.dumps(status))
        previous = {"at": now - 60, "files": {"clone/E0131": {**status, "updated": now - 65, "games": 0, "here": 0,
                                                             "played": {"az-syd": 0}}}}
        row = throughput.build_row(self.state, now, previous, 10, 0.0, 16.0)
        self.assertEqual(row["hosts"]["az-syd"]["by"]["coder E0131"], ceilings["az-syd"])
        self.assertEqual(row["hosts"]["here"]["by"]["coder E0131"], 10)

    @unittest.mock.patch.object(throughput, "CAP", 20)
    def test_the_record_keeps_exactly_its_newest_rows(self) -> None:
        start = time.time() - 3 * 86400
        for n in range(throughput.CAP + 5):
            throughput.sample(self.state, now=start + n * throughput.SAMPLE_S, cores=FIXTURE["cores"], **QUIET)
        rows = throughput.read_rows(self.state)
        self.assertEqual(len(rows), throughput.CAP)
        self.assertEqual(rows[-1]["at"], round(start + (throughput.CAP + 4) * throughput.SAMPLE_S, 1))

    def test_idle_spells_of_the_rows_a_cause_picks(self) -> None:
        # The live record on 2026-09-27: the machines idle 12:22-12:35 with no evaluation until E0158's at 12:36 (a
        # Claude usage limit, then E0158 being coded; the sampler wrote no row 12:07-12:20) and 13:55-14:06 while the
        # fillers paused for coders the loop took for queued candidates (MISSES 44). Neither stretch had one
        # evaluation or wait throughout, so the evaluation check saw nothing; health.idle_any's cause (rows with no
        # evaluation and nothing waiting) finds both.
        sample = Path(__file__).resolve().parent / "testdata/throughput/utilisation-idle-sample.jsonl"
        rows = [json.loads(line) for line in sample.read_text().splitlines()]
        self.assertEqual(throughput.idle_spells(rows), [])
        spells = throughput.idle_spells(rows, cause_of=lambda r: None if throughput.cause(r) else "no evaluation")
        clock = {row["at"]: row["time"] for row in rows}  # the sampler's own local times
        self.assertEqual([(clock[s["start"]], clock[s["end"]], s["minutes"]) for s in spells],
                         [("12:22", "12:35", 14), ("13:55", "14:06", 12)])
        self.assertTrue(all(s["idle"] > 0.8 and s["cause"] == "no evaluation" for s in spells))

    def test_idle_machines_with_nothing_to_test_raise_no_alert(self) -> None:
        # The captured minutes' status files, but no evaluation and no candidate waiting: idle is nothing for this
        # check, which looks at evaluations only (health.idle_any reports machines idle for any reason).
        evaluating_marker().unlink(missing_ok=True)
        (self.state / "runs/E0127/worker.json").unlink()
        rows = []
        for minute in FIXTURE["minutes"]:
            (self.state / "remote-status.json").write_text(json.dumps(minute["files"]["remote-status.json"]))
            rows.append(throughput.sample(self.state, now=minute["at"], cores=FIXTURE["cores"], **QUIET))
        self.assertTrue(all(row["evaluation"] is None and not row["waiting"] for row in rows))
        self.assertEqual(health.utilisation(self.state, rows[-1]["at"] + 30), [])

    def test_this_macs_capacity_keeps_sigs_sessions_gate_without_a_split(self) -> None:
        # native.game_slots' niced games stop at 0.7 x cores of 1-minute load with or without a CPU split; the record's
        # capacity for this Mac says the same, not the load gate's 0.8 share.
        (self.state / "load-fraction").write_text("0.8")
        with unittest.mock.patch.object(throughput, "published_split", return_value=None):
            self.assertEqual(throughput.local_capacity(self.state, 10, 7.2, 8.0, 0.0), 0)
            self.assertEqual(throughput.local_capacity(self.state, 10, 6.0, 8.0, 0.0), 2)

    def test_a_marker_a_crashed_evaluation_left_behind_is_no_evaluation(self) -> None:
        for marker in ({**live_marker(FIXTURE["minutes"][5]), "pid": 2 ** 22 + 12345},  # no such process
                       {**live_marker(FIXTURE["minutes"][5]), "since": FIXTURE["minutes"][5]["at"] - 13 * 3600}):
            with self.subTest(marker=marker):
                evaluating_marker().write_text(json.dumps(marker))
                self.assertIsNone(throughput.evaluation_now(self.state, FIXTURE["minutes"][5]["at"]))
        # A whole screen's minutes under a dead evaluation's marker, its driver gone too: no evaluation, no candidate
        # waiting, so the idle machines raise no alert.
        dead = 2 ** 22 + 12345
        worker = self.state / "runs/E0127/worker.json"
        worker.write_text(json.dumps({**FIXTURE["run"]["worker.json"], "driver_pid": dead}))
        os.utime(worker, (FIXTURE["minutes"][0]["at"],) * 2)
        evaluating_marker().write_text(json.dumps({**live_marker(FIXTURE["minutes"][0]), "pid": dead}))
        rows = []
        for minute in FIXTURE["minutes"]:
            (self.state / "remote-status.json").write_text(json.dumps(minute["files"]["remote-status.json"]))
            rows.append(throughput.sample(self.state, now=minute["at"], cores=FIXTURE["cores"], **QUIET))
        self.assertTrue(all(row["evaluation"] is None and not row["waiting"] for row in rows))
        self.assertEqual(health.utilisation(self.state, rows[-1]["at"] + 30), [])

    def test_a_candidate_whose_driver_died_is_not_waiting(self) -> None:
        run = self.state / "runs/E0131"
        run.mkdir()
        now = time.time()
        # A pid reused by another program after the driver died reads as dead; one running the loop's native.py or
        # driver.py as alive; a hand-edited value that isn't a pid (meta.alive raised TypeError on it) as unknown,
        # which counts as waiting, as a record from before driver_pid does.
        stranger = subprocess.Popen(["sleep", "60"])
        loop = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "/somewhere/loop/native.py"])
        for child in (stranger, loop):
            self.addCleanup(child.wait)
            self.addCleanup(child.kill)
        for pid, waiting in ((os.getpid(), ["E0131"]), (2 ** 22 + 12345, []), (None, ["E0131"]),
                             (stranger.pid, []), (loop.pid, ["E0131"]), (str(loop.pid), ["E0131"]),
                             ("12x", ["E0131"]), (2 ** 70, [])):
            with self.subTest(pid=pid):
                (run / "worker.json").write_text(json.dumps({"status": "finished", "driver_pid": pid}))
                self.assertEqual(throughput.waiting_now(self.state, now, "E0127"), waiting)

    # The queue page's chart reads each row's coders, their ideas' priorities and the backlog by priority: a recorded
    # run is past coding whatever its worker file says, and a backlog caught mid-append leaves only that field out.
    def test_a_row_records_the_coders_their_priorities_and_the_backlog(self) -> None:
        for exp, status, priority in (("E0130", "running", 2), ("E0131", "starting", 1), ("E0132", "running", 1)):
            (self.state / "runs" / exp).mkdir()
            (self.state / "runs" / exp / "worker.json").write_text(json.dumps({"status": status}))
            (self.state / "runs" / exp / "experiment.json").write_text(json.dumps({"idea": {"priority": priority}}))
        (self.state / "runs/E0132/diff.patch").write_text("")
        ideas = [{"id": f"I{n}", "status": status, "priority": p}
                 for n, (status, p) in enumerate([("todo", 1), ("followup", 1), ("todo", 3), ("trying:lane1", 2)])]
        (self.state / "ideas.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ideas))
        row = throughput.build_row(self.state, time.time(), None, FIXTURE["cores"], 0.0, 16.0)
        self.assertEqual((row["coding"], row["priorities"]), (["E0130", "E0131"], {"E0130": 2, "E0131": 1}))
        self.assertEqual(row["backlog"], {"1": {"todo": 1, "followup": 1}, "3": {"todo": 1, "followup": 0}})
        with (self.state / "ideas.jsonl").open("a") as handle:
            handle.write('{"id": "I9", "prio')
        self.assertNotIn("backlog", throughput.build_row(self.state, time.time(), None, FIXTURE["cores"], 0.0, 16.0))

    def test_a_coders_forged_status_file_can_neither_break_nor_fake_a_row(self) -> None:
        # A coder writes its status in its clone (native.coder_status), from a sandbox the loop doesn't trust.
        minute = FIXTURE["minutes"][5]
        (self.state / "remote-status.json").write_text(json.dumps(minute["files"]["remote-status.json"]))
        evaluating_marker().write_text(json.dumps(live_marker(minute)))
        clone = self.state / "clones/E0131/.git"
        clone.mkdir(parents=True)
        forged = clone / "bc-remote-status.json"
        now = minute["at"]
        honest = {"updated": now - 5, "games": 3, "here": 0, "running": 1, "active": {"az-syd": 1},
                  "hosts": {"az-syd": {"played": 3, "resting": False}}}
        probes = {
            "a future date to stay live forever": {**honest, "updated": now + 3600, "active": {"mm": 20}},
            "every host resting": {**honest, "hosts": {n: {"resting": True} for n in ("mm", "mm1", "ll", "az-syd")}},
            "cores for hosts that don't exist": {**honest, "cores": {f"fake{i}": 8 for i in range(5000)}},
            "counts that aren't numbers": {**honest, "active": {"mm": "lots", "ll": float("inf"), "mm1": True},
                                           "running": [1], "hosts": {"mm": "x"}},
            "a host far over its ceiling": {**honest, "active": {"az-syd": 10 ** 6}},
        }
        base = throughput.build_row(self.state, now, None, FIXTURE["cores"], 0.0, 16.0)
        for name, status in probes.items():
            with self.subTest(name):
                forged.write_text(json.dumps(status))
                row = throughput.build_row(self.state, now, None, FIXTURE["cores"], 0.0, 16.0)
                self.assertEqual({h: e["capacity"] for h, e in row["hosts"].items()},
                                 {h: e["capacity"] for h, e in base["hosts"].items()})
                self.assertLessEqual(row["hosts"]["mm"].get("by", {}).get("coder E0131", 0), 0)
                self.assertLessEqual(row["hosts"]["az-syd"].get("by", {}).get("coder E0131", 0), 4)
                self.assertNotIn("fake0", row.get("cores", {}))
        for name, text in (("not JSON", "{"), ("nested too deep", "[" * 100_000 + "]" * 100_000), ("a list", "[1, 2]")):
            with self.subTest(name):
                forged.write_text(text)
                row = throughput.build_row(self.state, now, None, FIXTURE["cores"], 0.0, 16.0)
                self.assertEqual(row["hosts"], base["hosts"])
        forged.unlink()
        forged.symlink_to(self.state / "remote-status.json")  # never read through a link
        row = throughput.build_row(self.state, now, None, FIXTURE["cores"], 0.0, 16.0)
        self.assertEqual(row["hosts"], base["hosts"])

    def test_a_second_sampler_in_the_same_minute_writes_nothing(self) -> None:
        now = time.time()
        self.assertIsNotNone(throughput.sample(self.state, now=now, cores=8, **QUIET))
        self.assertIsNone(throughput.sample(self.state, now=now + 20, cores=8, **QUIET))
        self.assertEqual(len(throughput.read_rows(self.state)), 1)

    # REFLECT 2026-09-29 20:42 F22: drivers started before 9e928681 won every minute's row, so no row had its new
    # fields. Their code can't change: it polls every SAMPLE_S / 3 and appends when the last row is SAMPLE_S - 5 old
    # (old_sample). A current sampler polling beside any number of them, in any phase, replaces each of their rows:
    # every row is the current one's, one a minute.
    def test_a_current_sampler_takes_every_minute_from_older_ones(self) -> None:
        start = float(round(time.time()))
        for old_phases, phase in (((0.0,), 3.3), ((0.0,), 17.9), ((0.0, 6.97, 13.4), 0.0), ((0.0, 19.95), 10.05)):
            with self.subTest(old=old_phases, current=phase):
                (self.state / throughput.LOG).unlink(missing_ok=True)
                polls = sorted([(start + t + p, 0) for p in old_phases for t in range(0, 900, 20)] +
                               [(start + t + phase, 1) for t in range(0, 900, 20)])  # at a tie the old one goes first
                for at, current in polls:
                    throughput.sample(self.state, now=at, cores=8, **QUIET) if current else old_sample(self.state, at)
                rows = throughput.read_rows(self.state)
                self.assertEqual(len(rows), len((self.state / throughput.LOG).read_text().splitlines()))
                self.assertTrue(all(row.get("stale") is False and "coding" in row for row in rows))
                self.assertEqual(len(rows), 15)
                for a, b in zip(rows, rows[1:]):  # its rows fall on its own polls: 3 of them, a minute apart
                    self.assertAlmostEqual(b["at"] - a["at"], throughput.SAMPLE_S, delta=0.1)

    # The `stale` flag marks the writer, not a missing field: a current row without backlog (a torn ideas.jsonl read)
    # is left alone, and a row from a sampler whose process runs older code than the disk's is replaced.
    def test_only_an_older_samplers_row_is_replaced(self) -> None:
        now = float(round(time.time()))
        with (self.state / "ideas.jsonl").open("a") as handle:
            handle.write('{"id": "I9", "prio')
        self.assertNotIn("backlog", throughput.sample(self.state, now=now, cores=8, **QUIET))
        self.assertIsNone(throughput.sample(self.state, now=now + 20, cores=8, **QUIET))
        with unittest.mock.patch.object(throughput, "current_code", return_value=False):
            self.assertTrue(throughput.sample(self.state, now=now + 60, cores=8, **QUIET)["stale"])
            self.assertIsNone(throughput.sample(self.state, now=now + 70, cores=8, **QUIET))
        self.assertFalse(throughput.sample(self.state, now=now + 80, cores=8, **QUIET)["stale"])
        self.assertEqual([row["at"] for row in throughput.read_rows(self.state)], [now, now + 80])

    def test_a_sampler_just_behind_the_writer_writes_no_twin(self) -> None:
        # "at" is rounded to 0.1 s: a row sampled at .06 says .1, and a sampler in step with it at .07 found it 0.03 s
        # ahead and wrote the same row again (36 twins in utilisation.jsonl on 2026-09-27).
        now = round(time.time()) + 0.06
        self.assertIsNotNone(throughput.sample(self.state, now=now, cores=8, **QUIET))
        self.assertIsNone(throughput.sample(self.state, now=now + 0.01, cores=8, **QUIET))
        self.assertEqual(len((self.state / throughput.LOG).read_text().splitlines()), 1)

    def test_rows_are_read_once_per_sample(self) -> None:
        sample = (DATA.parent / "utilisation-twin-rows-sample.jsonl").read_text()  # the live 19:54-19:58 rows
        (self.state / throughput.LOG).write_text(sample)
        self.assertEqual([r["time"] for r in throughput.read_rows(self.state)],
                         ["19:54", "19:55", "19:56", "19:57", "19:58"])


# 29 Sep: the CPU split was published only while a loop game was gated on this Mac, so coders' sandboxed test gates
# fell back to the 1-minute load for 57 min. The utilisation sampler reads the split every home.LAPSE_S, which keeps a
# CPU sampler running wherever the loop runs, and still writes its minute row every SAMPLE_S / 3.
class KeepsTheSplitPublished(unittest.TestCase):
    def test_the_sampler_reads_the_split_between_its_rows(self) -> None:
        with unittest.mock.patch.object(throughput, "CPU") as cpu, \
                unittest.mock.patch.object(throughput, "sample") as sample, \
                unittest.mock.patch.object(throughput, "LAPSE_S", 0.05):
            with throughput.sampling(Path("/nonexistent")):
                time.sleep(0.5)
        self.assertGreater(cpu.read.call_count, 3)
        self.assertEqual(sample.call_count, 1)


class StageTimes(unittest.TestCase):
    # E0125's run folder (loop/testdata/throughput/e0125-stages.json): its files at their times, and its result row.
    def setUp(self) -> None:
        sample = json.loads((DATA.parent / "e0125-stages.json").read_text())
        self.runs = loop_home() / "stage-runs"
        run = self.runs / "E0125"
        (run / "cand").mkdir(parents=True, exist_ok=True)
        os.utime(run / "cand", (sample["cand"],) * 2)
        for name, entry in sample["files"].items():
            if "rows" in entry:
                (run / name).write_text("".join(json.dumps(r) + "\n" for r in entry["rows"]))
            else:
                (run / name).write_text(json.dumps(entry.get("content", {})))
            os.utime(run / name, (entry["mtime"],) * 2)
        self.row, self.sample = sample["row"], sample

    def test_each_stage_gets_its_wall_time_games_and_games_in_flight(self) -> None:
        found = {name: (end - start, games, seconds)
                 for name, start, end, games, seconds in throughput.stages(self.runs / "E0125", self.row)}
        self.assertEqual(list(found), ["bot tests", "CPU gate and maps", "SPRT", "screen", "recording", "official report"])
        wall, games, seconds = found["SPRT"]
        # 504 trials rotating the champion and three opponents: 126 x 2 + 378 x 4 games, 756 of them reused baselines
        self.assertEqual(games, 126 * 2 + 378 * 4 - 756)
        self.assertAlmostEqual(seconds / wall, 26, delta=1)  # about 26 games in flight on average
        self.assertEqual(found["CPU gate and maps"][1], 11)
        self.assertEqual(found["official report"][1], 110)
        self.assertEqual(found["screen"][1], sum(len(e["rows"]) for n, e in self.sample["files"].items()
                                                  if n.startswith("pool-") and n.endswith("-candidate.jsonl")))

    # E0245's target batch (2026-09-28) was named "screen" in every row and in its idle alert, so the alert pointed at
    # the wrong stage: the batch runs from target-maps/ to target-units.jsonl and is a stage of its own.
    def test_a_targeted_ideas_target_batch_is_its_own_stage(self) -> None:
        run = self.runs / "E0245"
        run.mkdir()
        (run / "cand").mkdir()
        base = time.time() + 10  # after cand/'s birth, where stages() starts
        for name, at in (("bot-tests.log", 100), ("evaluation.json", 200), ("sprt.json", 300)):
            (run / name).write_text("{}")
            os.utime(run / name, (base + at, base + at))
        self.assertEqual(throughput.stage_now(run, "targeted"), "screen")  # an SPRT that failed plays no batch
        (run / "target-maps").mkdir()
        self.assertEqual(throughput.stage_now(run, "targeted"), "target batch")
        (run / "target-swarm-candidate.jsonl").write_text(json.dumps({"map": "m", "seconds": 40.0}) + "\n")
        (run / "target-units.jsonl").write_text(json.dumps({"map": "m"}) + "\n")
        os.utime(run / "target-units.jsonl", (base + 400, base + 400))
        self.assertEqual(throughput.stage_now(run, "targeted"), "screen")
        found = {name: games for name, _, _, games, _ in throughput.stages(run, {})}
        self.assertEqual(found.get("target batch"), 1)  # its games, never target-units.jsonl's rows

    def test_stats_lists_recent_results_and_the_days_slowest_stage(self) -> None:
        lines = throughput.stage_lines([self.row], self.runs, self.row["finished"] + 3600)
        text = "\n".join(lines)
        self.assertIn("- E0125 (kept, ", text)
        self.assertIn("SPRT 6 min, 1008 games", text)
        self.assertIn("Slowest stage over the last day: E0125's screen", text)
        self.assertNotIn("Slowest", "\n".join(throughput.stage_lines([self.row], self.runs, self.row["finished"] + 3 * 86400)))


if __name__ == "__main__":
    unittest.main()
