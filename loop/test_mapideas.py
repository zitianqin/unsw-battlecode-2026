"""The map-ideas job on real Discord rows (loop/testdata/mapideas: rows copied from the live messages.jsonl on
2026-09-26, #map-ideas plus forum rows from #ask-devs and two from #general, and the live roles.json trimmed to their
authors): the trigger's conditions, ideas.md, the worker's tools, and a run's outcomes with the model mocked."""

import testhome  # before anything that reads live paths
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import mapideas
import meta

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "loop/testdata/mapideas"
ROWS = mapideas.parse((DATA / "messages.jsonl").read_text().splitlines())
ROLES = json.loads((DATA / "roles.json").read_text())
NOW = 1_790_400_000.0


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# A loop home holding the sample as its Discord store, beside a main checkout with mapgen/ and loop/.
class Home(unittest.TestCase):
    def setUp(self) -> None:
        # No git trace2 events: a daemon listening for them writes into a fixture repo's .git, racing its removal.
        trace = patch.dict(os.environ, {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        self.addCleanup(trace.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.state, self.repo = root / "state", root / "repo"
        (self.state / "discord").mkdir(parents=True)
        shutil.copy(DATA / "messages.jsonl", self.state / "discord/messages.jsonl")
        shutil.copy(DATA / "roles.json", self.state / "discord/roles.json")
        for name in ("mapgen/gen.py", "loop/driver.py", "maps/ladder/default.map"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text("# fixture\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")


class Trigger(Home):
    def setUp(self) -> None:
        super().setUp()
        launch = patch.object(mapideas, "launch", return_value=4242)
        self.launch = launch.start()
        self.addCleanup(launch.stop)
        self.map_ids = [m["id"] for m in ROWS if m["channel"] in mapideas.CHANNELS]

    def tick(self, now: float, running: bool = False) -> str | None:
        with patch.object(mapideas, "alive", return_value=running):
            return mapideas.tick(self.repo, self.state, now)

    def alerts(self) -> list[str]:
        path = self.state / "host-alerts.log"
        return path.read_text().splitlines() if path.exists() else []

    def test_new_map_channel_messages_start_a_run_in_the_background(self) -> None:
        self.assertIn("worker launched in the background", self.tick(NOW))
        record = mapideas.read(self.state)
        work = Path(record["work"])
        self.assertEqual((record["pid"], record["attempt_at"], record["pending"]), (4242, NOW, self.map_ids))
        self.launch.assert_called_once_with(self.repo, self.state, work)
        self.assertTrue((work / "ideas.md").exists())
        prompt = (self.state / "worker-io/mapideas" / work.name / "PROMPT.md").read_text()  # out of its reach
        self.assertIn(f"on branch `mapideas/{work.name}`: `{work / 'repo'}`", prompt)
        for key in ("{ideas}", "{repo}", "{report}", "{branch}", "{python}", "{work}", "{idea_rule}"):
            self.assertNotIn(key, prompt)
        self.assertIn("One finding, one general rule", prompt)  # loop/GENERALISE.md

    def test_a_failed_run_retries_after_an_hour(self) -> None:
        self.tick(NOW)
        mapideas.failed(self.state, Path(mapideas.read(self.state)["work"]), "worker exited 1: crashed", NOW + 600)
        self.assertIsNone(self.tick(NOW + mapideas.RETRY_S - 60))  # the messages are still new, but too soon
        self.assertIsNotNone(self.tick(NOW + mapideas.RETRY_S + 60))
        self.assertEqual(self.launch.call_count, 2)

    def test_after_a_successful_run_new_messages_wait_12_hours(self) -> None:
        self.tick(NOW)
        self.assertTrue(mapideas.finished(self.state, Path(mapideas.read(self.state)["work"]), 0, NOW + 600))
        row = {"id": "1", "time": "2026-09-27T01:00:00.000Z", "author": "", "text": "a maze", "channel": "map-ideas"}
        with (self.state / "discord/messages.jsonl").open("a") as rows:
            rows.write(json.dumps(row) + "\n")
        self.assertIsNone(self.tick(NOW + mapideas.RETRY_S + 60))  # a retry's wait doesn't apply after a success
        self.assertIsNone(self.tick(NOW + mapideas.EVERY - 60))
        self.assertIsNotNone(self.tick(NOW + mapideas.EVERY + 60))
        self.assertEqual(mapideas.read(self.state)["pending"], ["1"])
        self.assertEqual(self.launch.call_count, 2)

    def test_under_native_orchestration_no_run_starts_and_the_orchestrator_is_told_once(self) -> None:
        with patch.object(mapideas, "alive", return_value=False):
            line = mapideas.tick(self.repo, self.state, NOW, models=False)
            self.assertIn(f"map ideas: {len(self.map_ids)} new map-channel messages, but map ideas are off under native "
                          "orchestration", line)
            self.assertIsNone(mapideas.tick(self.repo, self.state, NOW + 60, models=False))  # at most every EVERY
            self.launch.assert_not_called()
            self.assertEqual(self.alerts()[-1].split(" ", 2)[2], line)
            # The messages stay unhandled, so a Claude driver's upkeep runs them at once.
            self.assertIn("worker launched in the background", mapideas.tick(self.repo, self.state, NOW + 120))

    def test_a_dead_claude_run_is_still_noted_under_native(self) -> None:
        self.tick(NOW)
        with patch.object(mapideas, "alive", return_value=False):
            mapideas.tick(self.repo, self.state, NOW + 60, models=False)
        self.assertIn("exited without finishing", mapideas.read(self.state)["error"])

    def test_a_running_worker_blocks_another_run(self) -> None:
        self.tick(NOW)
        self.assertIsNone(self.tick(NOW + mapideas.STALE - 60, running=True))
        self.assertEqual(self.launch.call_count, 1)

    def test_a_run_process_that_died_without_finishing_is_a_failure(self) -> None:
        self.tick(NOW)
        self.assertIsNone(self.tick(NOW + 60))
        record = mapideas.read(self.state)
        self.assertIsNone(record["pid"])
        self.assertIn("exited without finishing", record["error"])
        self.assertEqual(record["failed_at"], NOW + 60)
        self.assertIn("map ideas failed: the run process exited without finishing", self.alerts()[-1])

    def test_a_run_recorded_running_past_its_limit_is_a_failure_and_no_longer_blocks(self) -> None:
        # Its pid is alive, but it may be someone else's now: recorded as failed, never killed.
        self.tick(NOW)
        # Recorded failed, and the retry starts in the same tick: the run began over RETRY_S ago.
        self.assertIn("worker launched", self.tick(NOW + mapideas.STALE + 60, running=True))
        self.assertIn("still recorded as running", self.alerts()[-1])
        self.assertEqual(self.launch.call_count, 2)

    def test_messages_are_skipped_after_two_failed_runs_on_them(self) -> None:
        for attempt in range(mapideas.MAX_TRIES):
            self.assertIsNotNone(self.tick(NOW + attempt * mapideas.EVERY))
            mapideas.failed(self.state, Path(mapideas.read(self.state)["work"]), "worker exited 1: quota",
                            NOW + attempt * mapideas.EVERY + 60)
        self.assertIn(f"failed 2 runs in a row, skipping their {len(self.map_ids)} messages", self.alerts()[-1])
        self.assertIsNone(self.tick(NOW + 5 * mapideas.EVERY))
        self.assertEqual(self.launch.call_count, mapideas.MAX_TRIES)

    def test_a_run_a_usage_limit_stopped_retries_at_its_reset_and_never_counts(self) -> None:
        # 11:10 on 2026-09-27: the run on the new ladder maps died on the session limit and then waited EVERY (12 h).
        at = NOW
        for _ in range(mapideas.MAX_TRIES + 1):
            self.assertIsNotNone(self.tick(at))
            reset = time.strftime("%I:%M%p", time.localtime(at + 1800))  # half an hour on, in this machine's zone
            limit = f"worker exited 1: You've hit your session limit · resets {reset}"
            mapideas.failed(self.state, Path(mapideas.read(self.state)["work"]), limit, at + 60)
            self.assertIsNone(self.tick(at + 1800 + meta.RESET_MARGIN_S - 1))  # not before the reset plus the margin
            at += 1800 + meta.RESET_MARGIN_S
        self.assertEqual(mapideas.read(self.state)["failures"], 0)  # none skipped the messages
        self.assertEqual(self.launch.call_count, mapideas.MAX_TRIES + 1)

    def test_only_unhandled_map_channel_messages_count_whenever_they_were_posted(self) -> None:
        # #ask-devs and #general rows aren't map channels. A message handled by no run counts even when it is older
        # than every handled one (a forum thread read late), which a time cutoff would skip.
        mapideas.save(self.state, {"handled": self.map_ids, "attempt_at": NOW - 2 * mapideas.EVERY})
        self.assertIsNone(self.tick(NOW))
        mapideas.save(self.state, {"handled": self.map_ids[1:], "attempt_at": NOW - 2 * mapideas.EVERY})
        self.assertIn("1 new map-channel messages", self.tick(NOW))
        self.assertEqual(mapideas.read(self.state)["pending"], self.map_ids[:1])

    def test_a_run_that_cannot_start_is_recorded_and_waits(self) -> None:
        with patch.object(mapideas, "prepare", side_effect=OSError("disk full")):
            self.assertEqual(self.tick(NOW), "map ideas: could not start the run: disk full")
        record = mapideas.read(self.state)
        self.assertEqual((record["attempt_at"], record["pid"]), (NOW, None))
        self.assertIn("disk full", record["error"])
        self.assertIsNone(self.tick(NOW + 60))  # retried after EVERY, not at every tick
        self.launch.assert_not_called()

    def test_a_branch_waiting_for_review_holds_the_next_run(self) -> None:
        git(self.repo, "branch", "mapideas/20260925-000000")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "main moves on")
        git(self.repo, "checkout", "-q", "mapideas/20260925-000000")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "a new style")
        git(self.repo, "checkout", "-q", "main")
        mapideas.save(self.state, {"branches": [{"branch": "mapideas/20260925-000000", "finished_at": NOW - 3600}]})
        self.assertIsNone(self.tick(NOW))
        git(self.repo, "merge", "-q", "--no-edit", "mapideas/20260925-000000")
        self.assertIsNotNone(self.tick(NOW))


# A map the ladder's pool gains starts a run too, with its file: on GET /maps as the server answered it on 2026-09-26
# (testdata/ladder-maps-sample.json) plus the two maps it gained the next day, their real files from maps/ladder.
class Lessons(Home):
    def setUp(self) -> None:
        super().setUp()
        launch = patch.object(mapideas, "launch", return_value=4242)
        launch.start()
        self.addCleanup(launch.stop)
        self.map_ids = [m["id"] for m in ROWS if m["channel"] in mapideas.CHANNELS]
        import ladder_maps
        self.ladder_maps = ladder_maps
        self.sample = json.loads((REPO / "loop/testdata/ladder-maps-sample.json").read_text())
        self.gained = [{**self.sample[0], "id": 60 + i, "name": name, "sizeLabel": size,
                        "mapText": (REPO / "maps/ladder" / f"{stem}.map").read_text()}
                       for i, (name, stem, size) in enumerate([("Portals", "portals", "32×16"),
                                                               ("Slithery Fight", "slithery_fight", "63×27")])]
        mapideas.save(self.state, {"handled": self.map_ids, "attempt_at": NOW - 2 * mapideas.EVERY})

    def pool(self, maps: list[dict], at: float) -> None:
        self.ladder_maps.refresh(self.state, at, lambda path: maps)

    tick, alerts = Trigger.tick, Trigger.alerts

    def test_a_map_the_pool_gains_starts_a_run_with_its_file(self) -> None:
        self.pool(self.sample, NOW - 7200)
        self.assertIsNone(self.tick(NOW - 3600))  # the first pool seen is recorded as studied: nothing to do
        self.assertEqual(len(mapideas.read(self.state)["ladder_studied"]), 11)
        self.pool(self.sample + self.gained[:1], NOW)
        self.assertIn("new ladder maps Portals, worker launched in the background", self.tick(NOW))
        record = mapideas.read(self.state)
        portals = self.ladder_maps.content_hash(self.gained[0]["mapText"])
        self.assertEqual((record["pending"], record["pending_maps"]), ([], [portals]))
        work = Path(record["work"])
        self.assertEqual((work / "ladder" / f"portals-{portals[:8]}.map").read_text(), self.gained[0]["mapText"])
        self.assertIn(f"- Portals (id 60, 32×16): ladder/portals-{portals[:8]}.map", (work / "lessons.md").read_text())
        self.assertIn("No new messages this run", (work / "ideas.md").read_text())
        prompt = (self.state / "worker-io/mapideas" / work.name / "PROMPT.md").read_text()
        self.assertIn(f"`{work / 'lessons.md'}` lists them", prompt)
        self.assertNotIn("{lessons}", prompt)
        mapideas.finished(self.state, work, 0, NOW + 60)
        self.assertIn(portals, mapideas.read(self.state)["ladder_studied"])
        self.assertIsNone(self.tick(NOW + mapideas.EVERY + 60))  # studied now

    # From 2026-09-27 21:00 AEST the pool may change every map's pearl timings, or withhold them (rtrgrd,
    # #announcements). Before, each such change was a "new" map and started a run on the same layout again, and
    # withheld text was studied as a board without pearls; then a pearl change was never studied at all, though
    # pearls shape a map's plans. Now a layout is studied again once with changed pearls, and no more.
    def test_a_studied_layout_is_studied_once_more_with_other_pearls_and_withheld_text_never(self) -> None:
        self.pool(self.sample + self.gained[:1], NOW)
        self.assertIn("new ladder maps Portals, worker launched", self.tick(NOW))
        mapideas.finished(self.state, Path(mapideas.read(self.state)["work"]), 0, NOW + 60)
        portals, slithery = (m["mapText"] for m in self.gained)
        repearled = lambda text, gap: "\n".join(" ".join([*line.split()[:3], "1", gap]) if line.startswith("TILE ")
                                                else line for line in text.splitlines()) + "\n"
        withheld = "\n".join(" ".join(line.split()[:3]) if line.startswith("TILE ") else line
                             for line in slithery.splitlines()) + "\n"
        later = NOW + mapideas.EVERY + 120
        changed = [{**self.gained[0], "mapText": repearled(portals, "77")},
                   {**self.gained[1], "mapText": withheld}, {**self.gained[0], "id": 99,
                                                             "mapText": repearled(portals, "55")}]
        self.pool(self.sample + changed, later)
        new = mapideas.new_ladder_maps(self.state, set(mapideas.studied(mapideas.read(self.state))))
        self.assertEqual([m["id"] for m in new], [self.gained[0]["id"]])  # one of the two variants, once
        self.assertIn("new ladder maps Portals, worker launched", self.tick(later))
        mapideas.finished(self.state, Path(mapideas.read(self.state)["work"]), 0, later + 60)
        # A third set of pearl timings on a layout studied twice starts no run.
        latest = later + mapideas.EVERY + 120
        self.pool(self.sample + [{**self.gained[0], "mapText": repearled(portals, "33")}], latest)
        self.assertEqual(mapideas.new_ladder_maps(self.state, set(mapideas.studied(mapideas.read(self.state)))), [])
        self.assertIsNone(self.tick(latest))
        # Slithery Fight's real text is still a new layout: it starts a run once the pool gives its pearls.
        self.pool(self.sample + self.gained, latest + self.ladder_maps.EVERY)
        self.assertIn("new ladder maps Slithery Fight, worker launched", self.tick(latest + mapideas.EVERY + 60))

    def test_the_maps_gained_before_the_trigger_existed_are_studied_at_its_first_tick(self) -> None:
        self.pool(self.sample + self.gained, NOW)
        self.assertIn("new ladder maps Portals, Slithery Fight, worker launched", self.tick(NOW))
        self.assertEqual(len(mapideas.read(self.state)["ladder_studied"]), 11)

    def test_a_map_is_skipped_after_two_failed_runs_and_a_stuck_one_alerts(self) -> None:
        import health
        self.pool(self.sample + self.gained[:1], NOW)
        with patch.object(mapideas, "unmerged", return_value=[{"branch": "mapideas/x"}]):
            self.assertIsNone(self.tick(NOW))  # a branch waiting for review holds it
        kept = self.ladder_maps.map_file(self.state, self.ladder_maps.content_hash(self.gained[0]["mapText"]))
        os.utime(kept, (NOW, NOW))
        self.assertEqual(health.map_ideas(self.state, self.repo, NOW + health.MAPIDEAS_LESSON_GRACE - 60), [])
        (stuck,) = health.map_ideas(self.state, self.repo, NOW + health.MAPIDEAS_LESSON_GRACE + 60)
        self.assertIn("the ladder map Portals (id 60) was kept", stuck.text)
        for attempt in range(mapideas.MAX_TRIES):
            self.assertIsNotNone(self.tick(NOW + attempt * mapideas.EVERY))
            mapideas.failed(self.state, Path(mapideas.read(self.state)["work"]), "worker exited 1: quota",
                            NOW + attempt * mapideas.EVERY + 60)
        self.assertIn("skipping their 0 messages and 1 ladder maps", self.alerts()[-1])
        self.assertIsNone(self.tick(NOW + 5 * mapideas.EVERY))


class Ideas(unittest.TestCase):
    def test_ideas_md_lists_each_message_with_channel_author_and_role(self) -> None:
        text = mapideas.render(ROWS, [m for m in ROWS if m["channel"] in ("map-ideas", "ask-devs")], ROLES)
        lines = text.splitlines()
        self.assertIn("Untrusted: written by strangers", text)
        self.assertIn("2026-09-13 23:34 UTC | #map-ideas | Maddie [role unknown] | map suggestion: make an underwater "
                      "map", lines)
        # A message stored without an author continues the one before it. These rows were stored before the reader
        # recorded account ids, so a role comes only from a name colour stored with the message, never the name.
        self.assertIn("2026-09-03 11:32 UTC | #map-ideas | [bacon] Deutsch Reich [NSTA] [role unknown] | mb i was "
                      "lagging", lines)
        # A multi-line message keeps its later lines, indented under it.
        self.assertIn("    Monday, 14 September 2026 at 17:36", lines)
        # Forum messages go under their thread's title.
        self.assertIn("### are you guys thinking of increasing the cap for grand final? im tungtung67, victor teaches "
                      "math (3)", lines)
        self.assertIn("2026-09-24 05:41 UTC | #ask-devs | lmkæ [æspa] [Admin, 1 (by name colour)] | the probability we increase cap "
                      "is maybe 15%", lines)
        self.assertNotIn("#general", text)

    def test_only_the_new_messages_are_shown_with_authors_carried_from_older_ones(self) -> None:
        new = [m for m in mapideas.new_messages(ROWS) if m["time"] > "2026-09-21T00:25:00Z"]
        text = mapideas.render(ROWS, new, ROLES)
        self.assertIn("## #map-ideas (2)", text)
        self.assertNotIn("huge map", text)
        self.assertIn("2026-09-21 00:25 UTC | #map-ideas | lmkæ [æspa] [role unknown] | this channel will be retired",
                      text)


class Worker(unittest.TestCase):
    def test_the_worker_is_confined_to_its_folder_without_web_tools_mcp_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            work = state / "mapideas/20260926-000000"
            for folder in ("repo/mapgen", "repo/loop", "repo/.git"):
                (work / folder).mkdir(parents=True)
            (state / "results.jsonl").write_text("")
            settings = mapideas.worker_settings(state, work)
            command = mapideas.worker_command(settings, work)
        parsed = json.loads(settings)
        self.assertEqual(command[:2], ["claude", "-p"])
        # acceptEdits, not bypass: a file-tool write outside the working folder is refused (checked 2026-09-26).
        self.assertEqual(command[command.index("--permission-mode") + 1], "acceptEdits")
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--add-dir", command)  # its only working folder is the run folder
        self.assertTrue(parsed["permissions"]["blockReadsOutsideWorkingDirectories"])
        self.assertEqual(set(command[command.index("--tools") + 1].split(",")),
                         {"Bash", "Read", "Edit", "Write", "Glob", "Grep"})
        self.assertIn("--strict-mcp-config", command)
        self.assertEqual(command[command.index("--disallowedTools") + 1:], list(meta.NOT_FOR_WORKERS))
        deny = parsed["permissions"]["deny"]
        self.assertTrue({"WebFetch", "WebSearch", f"Read(/{state / 'remote.json'})"} <= set(deny))
        self.assertIn(str(state / "remote.json"), parsed["sandbox"]["filesystem"]["denyRead"])
        self.assertEqual(parsed["sandbox"]["network"], {"allowedDomains": [], "deniedDomains": ["*"]})
        self.assertTrue(parsed["sandbox"]["enabled"])
        self.assertFalse(parsed["sandbox"]["allowUnsandboxedCommands"])
        self.assertIn(f"Edit(/{work / 'repo/loop'}/**)", deny)
        self.assertNotIn(f"Edit(/{work / 'repo/.git'}/**)", deny)  # commits write .git
        self.assertIn(f"Edit(/{work / 'repo/.git/config'})", deny)
        self.assertIn(f"Edit(/{work / 'repo/.git/hooks'}/**)", deny)
        self.assertIn(f"Edit(/{state / 'results.jsonl'})", deny)
        self.assertFalse([rule for rule in deny if "mapgen" in rule or rule.endswith("/mapideas/**)")])

    def test_a_worker_past_its_timeout_is_stopped_and_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "repo").mkdir()
            meta.protected_io(work, "mapideas", work).joinpath("PROMPT.md").write_text("")
            with patch.object(mapideas, "TIMEOUT", 0.5), \
                    patch.object(mapideas, "worker_command", return_value=["sleep", "30"]):
                started = time.monotonic()
                self.assertTrue(mapideas.session(work, work).startswith("the worker timed out after"))
            self.assertLess(time.monotonic() - started, 10)

    def test_the_worker_output_is_read_from_the_protected_folder_never_its_run_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            work = state / "mapideas/20260926-000000"
            (work / "repo").mkdir(parents=True)
            outside = state / "secret"
            outside.write_text('{"type": "result", "is_error": false}')
            (work / "session.json").symlink_to(outside)
            meta.protected_io(state, "mapideas", work).joinpath("PROMPT.md").write_text("the prompt: read briefing.md")
            reply = ["/bin/sh", "-c", 'cat >&2; echo \'{"type": "result", "is_error": true, "result": "boom"}\'']
            with patch.object(mapideas, "worker_command", return_value=reply):
                self.assertEqual(mapideas.session(state, work), "worker exited 0: boom")
            folder = state / "worker-io/mapideas" / work.name
            self.assertEqual((folder / "session.stderr").read_text(), "the prompt: read briefing.md")
            self.assertEqual(outside.read_text(), '{"type": "result", "is_error": false}')


# The branch's generator run inside the sandbox, on a copy of this checkout's mapgen/ and maps/.
class Pins(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.check = Path(directory.name).resolve() / "check"
        for name in ("mapgen", "maps"):
            shutil.copytree(REPO / name, self.check / name, ignore=shutil.ignore_patterns("__pycache__"))

    def edit_gen(self, code: str) -> None:
        gen = self.check / "mapgen/gen.py"
        gen.write_text(gen.read_text() + "\n" + code + "\n")

    def test_unchanged_styles_pass_in_the_sandbox(self) -> None:
        self.assertIsNone(mapideas.pins_error(self.check))

    def test_a_changed_existing_style_fails(self) -> None:
        self.edit_gen("_old = generate\ndef generate(seed, style='normal'):\n    return _old(seed, style) + ' '")
        self.assertIn("test_mapgen_pins.py failed", mapideas.pins_error(self.check))

    def test_the_branch_code_can_write_only_to_its_own_temporary_folder(self) -> None:
        # Not the shared temporary folders, which hold the cmux claude shim and the loop's evaluation marker.
        outside = Path(tempfile.gettempdir()).resolve() / f"mapideas-pins-escape-{os.getpid()}"
        self.edit_gen("import tempfile\nopen(tempfile.mkdtemp() + '/inside', 'w').write('ok')\n"
                      f"open({str(outside)!r}, 'w').write('escaped')")
        self.assertIn("PermissionError", mapideas.pins_error(self.check))
        self.assertFalse(outside.exists())
        shutil.copy(REPO / "mapgen/gen.py", self.check / "mapgen/gen.py")
        self.edit_gen("open('/private/tmp/mapideas-pins-escape', 'w').write('escaped')")
        self.assertIn("PermissionError", mapideas.pins_error(self.check))
        self.assertFalse(Path("/private/tmp/mapideas-pins-escape").exists())

    def test_the_branch_code_cannot_start_programs_or_signal_other_processes(self) -> None:
        self.edit_gen("import subprocess\nsubprocess.run(['/bin/echo', 'escaped'], check=True)")
        self.assertIn("PermissionError", mapideas.pins_error(self.check))
        shutil.copy(REPO / "mapgen/gen.py", self.check / "mapgen/gen.py")
        victim = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (victim.kill(), victim.wait()))
        self.edit_gen(f"import os, signal\nos.kill({victim.pid}, signal.SIGTERM)")
        self.assertIn("PermissionError", mapideas.pins_error(self.check))
        self.assertIsNone(victim.poll())  # still running

    def test_the_branch_code_cannot_read_secrets_or_reach_the_network(self) -> None:
        self.edit_gen(f"open({str(Path.home() / '.ssh/config')!r}).read()")
        self.assertIn("PermissionError", mapideas.pins_error(self.check))
        shutil.copy(REPO / "mapgen/gen.py", self.check / "mapgen/gen.py")
        self.edit_gen("import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=5)")
        self.assertIn("Operation not permitted", mapideas.pins_error(self.check))


class Run(Home):
    def setUp(self) -> None:
        super().setUp()
        self.new = mapideas.new_messages(ROWS)
        self.work = mapideas.prepare(self.state, ROWS, self.new, NOW)
        mapideas.save(self.state, {"attempt_at": NOW, "pid": 4242, "work": str(self.work),
                                   "pending": [m["id"] for m in self.new]})
        self.branch = f"mapideas/{self.work.name}"
        sync = patch.object(mapideas, "sync")
        sync.start()
        self.addCleanup(sync.stop)
        pins = patch.object(mapideas, "pins_error", return_value=None)  # Pins below runs the real check
        self.pins = pins.start()
        self.addCleanup(pins.stop)

    # Runs with a mocked model session that edits `changes` (path: text, or None to delete) in the clone, then runs
    # `commands` there (git mv, ln), commits when anything changed, and writes report.md; returns the alert lines.
    def run_with(self, changes: dict[str, str | None], verdict: str, error: str | None = None,
                 commands: tuple[tuple[str, ...], ...] = ()) -> list[str]:
        def session(state: Path, work: Path) -> str | None:
            clone = work / "repo"
            for name, text in changes.items():
                (clone / name).write_text(text)
            for command in commands:
                subprocess.run(command, cwd=clone, check=True, capture_output=True)
            if changes or commands:
                git(clone, "add", "-A")
                git(clone, "commit", "-qm", "worker change")
            (work / "report.md").write_text(f"Verdict: {verdict}\n\nPer idea...\n")
            return error
        with patch.object(mapideas, "session", side_effect=session):
            mapideas.run(self.repo, self.state, self.work)
        return (self.state / "host-alerts.log").read_text().splitlines()

    def refs(self) -> list[str]:
        return git(self.repo, "for-each-ref", "--format=%(refname)").splitlines()

    def test_the_worker_s_clone_shares_no_object_file_with_main(self) -> None:
        # The worker may write its clone's .git; a hard-linked object there would be main's own file (MISSES 17).
        mapideas.setup(self.repo, self.work)
        objects = [p for p in (self.work / "repo/.git/objects").rglob("*") if p.is_file()]
        self.assertTrue(objects)
        self.assertEqual({p.stat().st_nlink for p in objects}, {1})

    def test_a_mapgen_commit_becomes_a_branch_for_the_orchestrator(self) -> None:
        (alert,) = self.run_with({"mapgen/gen.py": "# a new style\n"}, "added style 'islands'")
        self.assertTrue(alert.endswith(f" map ideas ready: branch {self.branch}, report {self.work / 'report.md'}"))
        self.assertEqual(self.refs(), ["refs/heads/main", f"refs/heads/{self.branch}"])  # the quarantine ref is gone
        self.pins.assert_called_once_with(self.work / "check")
        self.assertEqual((self.work / "check/mapgen/gen.py").exists(), False)  # cleaned up with the clone
        self.assertFalse((self.work / "repo").exists())
        record = mapideas.read(self.state)
        self.assertEqual((record["pid"], record["error"], record["commits"]), (None, None, 1))
        self.assertEqual(record["handled"], sorted(m["id"] for m in self.new))
        self.assertEqual([e["branch"] for e in mapideas.unmerged(self.repo, record)], [self.branch])
        git(self.repo, "merge", "-q", "--ff-only", self.branch)
        self.assertEqual(mapideas.unmerged(self.repo, record), [])

    def test_no_commit_says_why_and_still_handles_the_messages(self) -> None:
        (alert,) = self.run_with({}, "every message is a joke or chatter")
        self.assertTrue(alert.endswith(" map ideas: nothing useful: every message is a joke or chatter, report "
                                       f"{self.work / 'report.md'}"))
        self.assertEqual(self.refs(), ["refs/heads/main"])
        record = mapideas.read(self.state)
        self.assertEqual((len(record["handled"]), record.get("branches", [])), (len(self.new), []))
        self.pins.assert_not_called()

    def test_a_change_outside_mapgen_fails_and_is_not_kept(self) -> None:
        (alert,) = self.run_with({"loop/driver.py": "# edited\n"}, "added a style")
        self.assertIn("map ideas failed: the branch changes files outside mapgen/ or adds links: loop/driver.py", alert)
        self.assertEqual(self.refs(), ["refs/heads/main"])
        record = mapideas.read(self.state)
        self.assertNotIn("handled", record)  # the same messages are tried again
        self.assertIsNone(record["pid"])
        self.assertTrue((self.work / "repo").exists())  # kept to look at

    def test_a_blob_main_already_holds_renamed_to_gitmodules_is_refused_leaving_main_clean(self) -> None:
        # Round-3 review: main has the text under a harmless name, so the worker's pack leaves the blob out and git
        # checks it only after saving the pack; a direct fetch was refused but left its commit and trees in main,
        # failing publishing.repo_intact's `git fsck --full` until gc.
        (self.repo / "mapgen/notes.txt").write_text('[submodule "x"]\n\tpath = x\n\turl = --upload-pack=touch /tmp/p\n')
        git(self.repo, "add", "-A"), git(self.repo, "commit", "-qm", "notes")
        objects = sorted(p for p in (self.repo / ".git/objects").rglob("*") if p.is_file())
        (alert,) = self.run_with({}, "added a style",
                                 commands=(("git", "mv", "mapgen/notes.txt", "mapgen/.gitmodules"),))
        self.assertEqual(self.refs(), ["refs/heads/main"])
        self.assertEqual(sorted(p for p in (self.repo / ".git/objects").rglob("*") if p.is_file()), objects)
        git(self.repo, "fsck", "--full", "--no-dangling", "--no-progress")  # raises on a bad blob
        self.assertIn("map ideas failed: FetchRefused: git refused the fetch of", alert)

    def test_a_rename_out_of_loop_into_mapgen_fails(self) -> None:
        (alert,) = self.run_with({}, "added a style", commands=(("git", "mv", "loop/driver.py", "mapgen/driver.py"),))
        self.assertIn("loop/driver.py", alert)
        self.assertEqual(self.refs(), ["refs/heads/main"])

    def test_a_symlink_in_mapgen_fails(self) -> None:
        (alert,) = self.run_with({}, "added a style", commands=(("ln", "-s", "/etc/hosts", "mapgen/hosts"),))
        self.assertIn("adds links: mapgen/hosts", alert)

    def test_a_branch_that_changes_existing_maps_is_not_kept(self) -> None:
        self.pins.return_value = "loop/test_mapgen_pins.py failed on the branch: near differs"
        (alert,) = self.run_with({"mapgen/gen.py": "# changed near\n"}, "added style 'islands'")
        self.assertIn("map ideas failed: loop/test_mapgen_pins.py failed on the branch", alert)
        self.assertEqual(self.refs(), ["refs/heads/main"])

    def test_a_run_that_finishes_after_a_newer_one_started_changes_nothing(self) -> None:
        # The older run was recorded as failed past STALE and a newer run started; the old one then finishes.
        newer = {"attempt_at": NOW + mapideas.EVERY, "pid": 4343, "work": str(self.work.parent / "20260926-172000"),
                 "pending": ["1548839411465064470"], "handled": []}
        mapideas.save(self.state, newer)
        (alert,) = self.run_with({"mapgen/gen.py": "# a new style\n"}, "added style 'islands'")
        self.assertIn(f"run {self.work.name} finished after a newer run started; its result is ignored", alert)
        self.assertEqual(self.refs(), ["refs/heads/main"])
        self.assertEqual(mapideas.read(self.state), newer)

    def test_a_worker_error_is_recorded_and_alerted(self) -> None:
        (alert,) = self.run_with({"mapgen/gen.py": "# half done\n"}, "added", error="worker exited 1: out of quota")
        self.assertIn("map ideas failed: worker exited 1: out of quota; retrying after 1 h", alert)
        self.assertEqual(self.refs(), ["refs/heads/main"])
        self.assertIn("out of quota", mapideas.read(self.state)["error"])


if __name__ == "__main__":
    unittest.main()
