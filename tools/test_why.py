"""Checks tools/why.py on a real online game (testdata/queen-403920.replay: our submission 8636, champion b7b553f, as
team A on Queen Of Spades, whose TILE lines withhold pearl timings): the replay's seed reads, the stand-in sends a
sonar that left through the tail back into the neck, the debug copy of the bot keeps the submission's sonar key, the
event comparison ignores only logs, countdowns and CPU counts and reads a site timeout as the stand-in's empty reply,
a submission's commit is found by archive digest, the per-move terms keep the bot's names, and the whole game plays
again event for event, while a changed opponent turn (past the stand-in check, which the test skips) is reported at its round with no debug line from it on. Two
games of submission 9553 check the map choice: one whose teams' labels the site swapped takes the file's timings,
and one the site played on pearl timings no file has is refused by the stand-in check before the bot is built.

    PYTHONPATH=loop .venv/bin/python -m unittest tools.test_why

The last two tests build the bot with the judge's clang and play the game in the sandbox (about 20 s and a minute of
CPU the first time), so run this module under tools/gated_test.sh.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1] / "loop"))  # testhome, without PYTHONPATH=loop
import testhome  # noqa: E402,F401  (before anything that reads live paths)

import replays  # noqa: E402
import watch  # noqa: E402
import why  # noqa: E402

HERE = Path(__file__).resolve().parent
REPLAY = HERE / "testdata/queen-403920.replay"
LOGGED_REPLAY = HERE / "testdata/fold-e0102-0308.replay"  # a local SPRT game, with our debug lines
SWAPPED = HERE / "testdata/default-451633.replay"  # the teams' labels swapped against maps/ladder/default.map
OTHER_TIMINGS = HERE / "testdata/queen-451634.replay"  # pearl timings no map file has
COMMIT = "b7b553f0c20b2b853f39237ba0ae5d8d5869c188"
LADDER = (why.REPO / "maps" / "ladder",)


# Every replay here takes why.wait_turn's lock beside a temporary evaluation marker, never the loop's own
# (/private/tmp/bc-loop-why.lock): a live online_fit replay holding that one kept these tests waiting (29 Sep).
def setUpModule() -> None:
    folder = tempfile.TemporaryDirectory()
    marker = patch("home.evaluating_marker", lambda: Path(folder.name) / "evaluating.json")
    marker.start()
    unittest.addModuleCleanup(marker.stop)
    unittest.addModuleCleanup(folder.cleanup)


class RecordedTurns(unittest.TestCase):
    def test_the_seed_reads(self) -> None:
        self.assertEqual(replays.load_replay(REPLAY).seed.value, 0x169fa08da47fb1f0)

    def test_a_ray_out_of_the_tail_was_sent_into_the_neck(self) -> None:
        # Round 0: dragon 0 moved east to 6,11; its third ray left from its tail, 5,10, heading north, so the
        # reply that made it was a sonar sent west, into its own neck. Dragon 1 (the opponent) sent none.
        turns = why.recorded_replies(replays.load_replay(REPLAY))
        self.assertEqual(turns[0, 0].text().decode().splitlines(),
                         ["MOVE E", "SONAR N 8304637726614894592", "SONAR S 7597572588553649152",
                          "SONAR W 9124292846197948416", "PROTOCOL 3"])
        self.assertEqual(turns[0, 1].text(), b"MOVE W\nPROTOCOL 3\n")
        self.assertTrue(any(t.action.startswith("SPLIT ") for t in turns.values()))


class DebugCopy(unittest.TestCase):
    def test_every_embedded_file_is_the_submissions_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = why.prepare_source(COMMIT, Path(tmp), debug=True)
            self.assertIn("bool debugIndicator = true;", (bot / "params.hpp").read_text())
            self.assertIn("tools/why.py", (bot / "debugline.hpp").read_text())
            embeds = re.findall(r'#embed "([^"]+)"', (bot / "sonarkey.hpp").read_text())
            self.assertIn("params.hpp.pristine", embeds)
            for name in embeds:
                original = subprocess.run(["git", "-C", str(why.REPO), "show", f"{COMMIT}:bot/{name.removesuffix('.pristine')}"],
                                          capture_output=True, check=True).stdout
                self.assertEqual((bot / name).read_bytes(), original, name)

    def test_the_plain_copy_is_the_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = why.prepare_source(COMMIT, Path(tmp), debug=False)
            self.assertFalse(list(bot.glob("*.pristine")))
            original = subprocess.run(["git", "-C", str(why.REPO), "show", f"{COMMIT}:bot/params.hpp"],
                                      capture_output=True, check=True).stdout
            self.assertEqual((bot / "params.hpp").read_bytes(), original)


class Comparison(unittest.TestCase):
    def test_logs_and_cpu_counts_are_left_out(self) -> None:
        events = why.comparable(replays.load_replay(LOGGED_REPLAY))
        self.assertFalse({kind for _, kind, _ in events} & set(why.UNCOMPARED))
        self.assertFalse([body for _, kind, body in events if kind == "dragonAction" and "instructions" in body])

    def test_the_first_difference_names_its_round(self) -> None:
        events = why.comparable(replays.load_replay(REPLAY))
        self.assertIsNone(why.first_difference(events, list(events)))
        k = next(i for i, (r, kind, _) in enumerate(events) if r == 50 and kind == "dragonAction")
        changed = events[:k] + [(50, "dragonAction", "{}")] + events[k + 1:]
        self.assertEqual(why.first_difference(events, changed)[0], 50)
        self.assertEqual(why.first_difference(events, events[:k])[2], "the replayed game ended")

    def test_a_site_timeout_matches_the_stand_ins_empty_reply(self) -> None:
        # Game 394361 (3.3 MB, not kept here), round 171: the site recorded dragon 800's timeout as {"id": 800, "tle":
        # true}; the stand-in sends an empty reply for it, which the local engine records as a suicide.
        def one_action(fill) -> object:
            message = replays.SCHEMA.Replay.new_message()
            events = message.init("events", 2)
            events[0].init("roundStart").round = 171
            fill(events[1].init("dragonAction"))
            return message.as_reader()

        def site(action) -> None:
            action.id, action.tle = 800, True

        def local(action) -> None:
            action.id = 800
            action.init("action").suicide = None

        def moved(action) -> None:
            action.id = 800
            action.init("action").init("move", 1)

        recorded = why.comparable(one_action(site))
        self.assertIsNone(why.first_difference(recorded, why.comparable(one_action(local))))
        self.assertIsNotNone(why.first_difference(recorded, why.comparable(one_action(moved))))

    def test_the_timed_map_is_found_by_layout(self) -> None:
        from ladder_maps import content_hash
        text = replays.load_replay(REPLAY).map
        [(name, grafted)] = why.candidate_maps(text, LADDER)
        self.assertEqual(name, "queen_of_spades.map")
        self.assertEqual(content_hash(grafted), content_hash((LADDER[0] / name).read_text()))

    def test_a_game_with_the_teams_labels_swapped_takes_the_files_timings(self) -> None:
        # Game 451633 (submission 9553, Default): the site's map text names the same bodies with the other team's
        # label (half its games since 2026-09-28), so no file has its layout; with the file's timings grafted on, the
        # recording plays again event for event with every dragon a stand-in.
        replay = replays.load_replay(SWAPPED)
        [(name, grafted)] = why.candidate_maps(replay.map, LADDER)
        self.assertEqual(name, "default.map")

        def dragons(text: str) -> list[str]:
            return [line for line in text.splitlines() if line.startswith("DRAGON ")]

        self.assertEqual(dragons(grafted), dragons(replay.map))
        self.assertNotEqual(dragons(grafted), dragons((LADDER[0] / name).read_text()))
        self.assertEqual(why.reproducing_maps(SWAPPED, replay, [(name, grafted)], LADDER), [(name, grafted)])

    def test_a_game_on_timings_no_file_has_is_refused_before_the_bot_plays(self) -> None:
        # Game 451634 (submission 9553, Queen Of Spades, same layout as the file): the site played its own pearl
        # table, so our bot's first sonar differed in round 0 ("DIVERGED in round 0"), though nothing of ours did.
        # With every dragon a stand-in the engine's own pearls first differ in round 3, so the game is refused as not
        # reproducible.
        replay = replays.load_replay(OTHER_TIMINGS)
        maps = why.candidate_maps(replay.map, LADDER)
        self.assertEqual([name for name, _ in maps], ["queen_of_spades.map"])
        with self.assertRaises(SystemExit) as refused:
            why.reproducing_maps(OTHER_TIMINGS, replay, maps, LADDER)
        self.assertIn("not reproducible", str(refused.exception))
        self.assertRegex(str(refused.exception), r"first difference is in round 3: .*\n.*tileChange|first difference is in round 3:[\s\S]*tileChange")
        # The same file does reproduce game 403920, whose site played the file's timings.
        known = replays.load_replay(REPLAY)
        self.assertEqual([name for name, _ in why.reproducing_maps(REPLAY, known, why.candidate_maps(known.map, LADDER),
                                                                   LADDER)], ["queen_of_spades.map"])


class Submissions(unittest.TestCase):
    def test_a_submission_id_finds_its_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "publishing.json").write_text('{"verified": {"submission_id": 30, "commit": "ccc"}}')
            (state / "uploads.jsonl").write_text(
                '{"commit": "aaa", "digest": "da", "previous": 5, "previous_digest": "d5"}\n'
                '{"commit": "bbb", "digest": "db", "previous": 10, "previous_digest": "da"}\n'
                '{"commit": "ccc", "digest": "dc", "previous": 20, "previous_digest": "db"}\n')
            self.assertEqual(why.submission_commit(30, state), "ccc")
            self.assertEqual(why.submission_commit(10, state), "aaa")  # bbb's row saw 10 live with aaa's digest
            self.assertEqual(why.submission_commit(20, state), "bbb")
            self.assertIsNone(why.submission_commit(99, state))

    def test_a_failed_attempt_is_never_the_build(self) -> None:
        # 10 was aaa's build; bbb's upload, made while 10 was live, was refused; 15 went up outside the loop, so
        # ccc's row, made while 15 was live, names a digest no row of ours has: 15's commit is unknown, not bbb.
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "uploads.jsonl").write_text(
                '{"commit": "aaa", "digest": "da", "previous": 5, "previous_digest": "d5"}\n'
                '{"commit": "bbb", "digest": "db", "previous": 10, "previous_digest": "da"}\n'
                '{"commit": "ccc", "digest": "dc", "previous": 15, "previous_digest": "d15"}\n')
            self.assertEqual(why.submission_commit(10, state), "aaa")
            self.assertIsNone(why.submission_commit(15, state))


class MoveRows(unittest.TestCase):
    def test_terms_keep_the_bots_names(self) -> None:
        # A ninth term added to scoring.hpp, and a new one before penalty: every value keeps its own name.
        line = "mN 7.5,target:2,explore:0,space:1.5,repel:0,pearl:4,spawn:0,wall:-1,penalty:0,nine:1,12,30,10100"
        [row] = why.move_rows(watch.log_fields(line))
        self.assertIn("= target 2, space 1.5, pearl 4, wall -1, nine 1 | fill 12 room 30 [closed open]", row)


@patch.object(why, "MAP_DIRS", LADDER)
class PlayedAgain(unittest.TestCase):
    def test_the_game_plays_again_event_for_event(self) -> None:
        side, commit, game = why.replay_game(REPLAY, "A", COMMIT)
        self.assertIsNone(game.difference)
        self.assertEqual(game.actions[29, 7], "MOVE W")  # dragon 7's last move, into kelp
        self.assertEqual(watch.log_fields(game.lines[28, 7])["term"], "target")
        self.assertRegex(watch.log_fields(game.lines[28, 7])["mW"], r"^[^,]+,target:[^,]+,explore:")
        self.assertEqual(len([k for k in game.lines if k[1] == 7]), len([k for k in game.actions if k[1] == 7]))

    def test_a_changed_opponent_turn_stops_the_lines_at_its_round(self) -> None:
        real = why.recorded_replies

        def changed(replay):
            turns = real(replay)
            turns[5, 1].action = "MOVE N" if turns[5, 1].action != "MOVE N" else "MOVE S"
            return turns

        # The stand-in check would refuse the changed game outright, so it is skipped: this is about the bot's pass.
        with patch.object(why, "recorded_replies", changed), \
                patch.object(why, "reproducing_maps", lambda path, replay, maps, folders: maps):
            out = why.report(REPLAY, 0, 10, None, "A", COMMIT, plain=False)
        self.assertTrue(out[1].startswith("DIVERGED in round 5:"), out[1])
        shown = [int(m[1]) for line in out if (m := re.match(r"r(\d+) dragon", line))]
        self.assertTrue(shown)
        self.assertLess(max(shown), 5)



# A replay whose turn lock another holder keeps (here this process's own first handle, as flock sees a second open)
# goes ahead unlocked once lock_wait_s passes, rather than waiting forever (rulecounts' online step waited on it).
class TurnLock(unittest.TestCase):
    def test_a_held_lock_is_waited_for_only_so_long(self) -> None:
        import fcntl
        with tempfile.TemporaryDirectory() as tmp, patch("home.evaluating_marker", lambda: Path(tmp) / "evaluating.json"), \
                patch.object(why.subprocess, "run"):
            with open(Path(tmp) / "why.lock", "a") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX)
                self.assertIsNone(why.wait_turn(lock_wait_s=0))
            turn = why.wait_turn(lock_wait_s=0)  # freed: taken
            self.assertIsNotNone(turn)
            turn.close()


if __name__ == "__main__":
    unittest.main()
