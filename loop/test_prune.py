import testhome  # before anything that reads live paths
import fcntl
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import online
import prune

CLAUDE = {"session_id": "3fc9e284-813c-478c-94d1-bb8254c72882"}
NATIVE = {"provider": "native-agent", "model": "gpt-6-astra", "reasoning_effort": "xhigh"}


# A results.jsonl row with the fields prune reads, as the driver writes them.
def row(eid: str, verdict: str, session: dict = CLAUDE, started: float = prune.POSTMORTEMS_FROM,
        kind: str | None = None) -> dict:
    return {"id": eid, "verdict": verdict, "session": session, "started": started, "sprt": {"decision": "H0"},
            "idea": {"id": "I1", **({"kind": kind} if kind else {})}}


def build(state: Path, rows: list[dict], turns: dict[str, str]) -> None:
    (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for eid in [r["id"] for r in rows] + ["E0133"]:  # E0133: unrecorded, still under test
        losses = state / "runs" / eid / "losses"
        losses.mkdir(parents=True)
        (losses / "0001-gen_eval_1-aA.replay").write_bytes(b"x" * 10)
    for eid, turn in turns.items():
        (state / "runs" / eid / f"{turn}.json").write_text('{"result": "done"}')


def left(state: Path) -> set[str]:
    return {p.parent.name for p in (state / "runs").glob("*/losses")}


class TestPrune(unittest.TestCase):
    # MISSES 58: results that ended before any game got no post-mortem (16 "no code" results and E0186's CPU gate by
    # 2026-09-27 22:20). testdata/results-early-sample.jsonl holds the real rows of E0182 (no code, the coder's
    # stop), E0186 (cpu/crash check failed), E0139 (a session that did no work), E0144 (blocked), E0093 (the old
    # "no commit") and E0076 (a tune's bot tests: no coder to ask).
    def test_a_result_that_ended_before_any_game_owes_a_post_mortem_when_its_coder_can_explain_it(self) -> None:
        rows = {r["id"]: r for r in map(json.loads, (Path(__file__).parent / "testdata/results-early-sample.jsonl")
                                        .read_text().splitlines())}
        later = {eid: dict(r, started=prune.EARLY_TURNS_FROM + 60) for eid, r in rows.items()}
        self.assertEqual({eid: prune.owed_turn(r) for eid, r in later.items()},
                         {"E0182": "postmortem", "E0186": "postmortem", "E0139": None, "E0144": None,
                          "E0093": None, "E0076": None})
        # Recorded before the change: owed only when asked for (driver.py --turn), so they don't all alert at once.
        self.assertEqual({eid: prune.owed_turn(r) for eid, r in rows.items() if prune.owed_turn(r)}, {})
        self.assertEqual({eid for eid, r in rows.items() if prune.owed_turn(r, early_from=0)}, {"E0182", "E0186"})

    # E0040 (an SPRT's, claimed 24 Sep before post-mortems began) and E0087 (an identical check's, claimed 26 Sep 04:18
    # before those got one) never had a turn to save, yet stayed owed for good; E0068 (claimed 25 Sep) is owed one.
    def test_a_result_claimed_before_its_turn_existed_owes_none(self) -> None:
        coder = {"session": {"session_id": "a Claude coder's"}, "idea": {"id": "I1"}}
        rows = {"E0040": {"started": 1790221418.9, "verdict": "discarded", "sprt": {"decision": "H0"}},
                "E0068": {"started": 1790300827.4, "verdict": "promising", "sprt": {"decision": "H0"}},
                "E0087": {"started": 1790359125.5, "verdict": "discarded", "identity": {"identical": False}}}
        self.assertEqual({eid: prune.owed_turn(coder | r) for eid, r in rows.items()},
                         {"E0040": None, "E0068": "postmortem", "E0087": None})
        self.assertEqual(prune.owed_turn(coder | rows["E0087"] | {"started": prune.IDENTITY_TURNS_FROM}), "postmortem")

    # sig, 2026-09-29: cleanup never deletes replays, only packs them (loop/pack.py), readable again with tar.
    def test_only_losses_nothing_will_read_again_are_packed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            # Results claimed before the follow-on turn: E0128's post-mortem finished, E0130's is still due.
            build(state, [row("E0118", "kept"), row("E0125", "kept"), row("E0126", "discarded"), row("E0106", "kept"),
                          row("E0128", "discarded"), row("E0130", "promising")],
                  {"E0126": "postmortem", "E0128": "postmortem"})
            line = prune.tick(state)
            # The newest two kept results (audit), the pending post-mortem and the unrecorded run keep theirs.
            self.assertEqual(left(state), {"E0125", "E0106", "E0130", "E0133"})
            self.assertIn("3 recorded results", line)
            listed = subprocess.run(f"zstd -dc -q {state / 'runs/E0126/replays.tar.zst'} | tar -tf -", shell=True,
                                    capture_output=True, text=True).stdout.split()
            self.assertIn("losses/0001-gen_eval_1-aA.replay", listed)
            self.assertEqual(prune.tick(state), "")

    def test_a_kept_result_keeps_its_losses_until_its_follow_on_has_run(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            new = prune.TURNS_FROM + 60
            # E0140 is owed a follow-on and two newer results were kept meanwhile; E0141 (identical) is owed none.
            build(state, [row("E0140", "kept", started=new), row("E0141", "kept", started=new, kind="identical"),
                          row("E0142", "kept", started=new, kind="tune"), row("E0143", "kept", started=new)],
                  {"E0142": "followon", "E0143": "followon"})
            prune.tick(state)
            self.assertEqual(left(state), {"E0140", "E0142", "E0143", "E0133"})
            (state / "runs" / "E0140" / "followon.json").write_text('{"result": "proposed 2"}')
            prune.tick(state)
            self.assertEqual(left(state), {"E0142", "E0143", "E0133"})

    def test_a_pending_audits_losses_stay_until_it_is_applied(self) -> None:
        # The 18:18 audit of 2026-09-29 listed E0340's losses; two newer kept results pruned them while it ran.
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            build(state, [row("E0340", "kept"), row("E0348", "kept"), row("E0350", "kept")], {})
            (state / "audit.json").write_text(json.dumps({"pending": str(state / "audits/1"), "losses_given": ["E0340"]}))
            prune.tick(state)
            self.assertEqual(left(state), {"E0340", "E0348", "E0350", "E0133"})
            (state / "audit.json").write_text(json.dumps({"finished_at": 1.0, "losses_given": ["E0340"]}))
            prune.tick(state)
            self.assertEqual(left(state), {"E0348", "E0350", "E0133"})

    def test_a_native_post_mortem_frees_its_losses_once_native_followups_saved_its_marker(self) -> None:
        # Native results not kept never wrote postmortem.json, so their losses were never deleted.
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            build(state, [row("E0090", "discarded", session=NATIVE),
                          row("E0150", "discarded", session=NATIVE, started=prune.TURNS_FROM + 60)], {})
            prune.tick(state)
            self.assertEqual(left(state), {"E0150", "E0133"})  # E0090 was claimed before the marker existed
            (state / "runs" / "E0150" / "postmortem.json").write_text('{"native": true}')
            prune.tick(state)
            self.assertEqual(left(state), {"E0133"})

    def test_a_turn_that_ended_in_an_error_result_keeps_its_losses(self) -> None:
        # E0141-E0145's turns at 04:14 on 2026-09-27 hit the usage limit, each saved the error as its turn, and prune
        # deleted E0142's and E0143's losses before any post-mortem had read them.
        error = (Path(__file__).parent / "testdata" / "postmortem-usage-limit.json").read_text()
        self.assertFalse(prune.turn_ok(error))
        self.assertFalse(prune.turn_ok(""))
        self.assertTrue(prune.turn_ok('{"result": "proposed 2", "is_error": false}'))
        self.assertTrue(prune.turn_ok('{"native": true}'))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            build(state, [row("E0143", "discarded", started=prune.TURNS_FROM + 60)], {})
            (state / "runs" / "E0143" / "postmortem.json").write_text(error)
            prune.tick(state)
            self.assertEqual(left(state), {"E0143", "E0133"})

    def test_a_turn_that_ended_waiting_for_its_own_work_is_no_turn(self) -> None:
        # E0068's post-mortem (testdata/postmortem-E0068-waiting.json) saved "Waiting for the variant games to
        # finish." and counted as done; E0197's coder ended "... I'll be notified when they do." (MISSES 63).
        waiting = (Path(__file__).parent / "testdata" / "postmortem-E0068-waiting.json").read_text()
        self.assertFalse(prune.turn_ok(waiting))
        coder = json.loads((Path(__file__).parent / "testdata" / "session-E0197-waiting.json").read_text())
        self.assertTrue(prune.still_waiting(coder["result"]))
        for said in ("Still waiting on the quick check.",  # E0045
                     "Done.\n\nWaiting on the post-fix quick check.",  # E0184
                     "Once they finish I’ll commit.", "I will be notified when it completes."):
            self.assertTrue(prune.still_waiting(said), said)
        # Finished turns that mention waiting, from saved results (E0043, E0086, E0058, E0108).
        for said in ("Turns spent waiting for a spawn tile reset the count.",
                     "which only counted as worth waiting for while `spawnSlack` was 2.",
                     "Nothing I started is still running.\n\nBlocked by: none",
                     "The king waits for folders before round 450.",
                     "Waiting for the games showed nothing new. " + "Measured it all. " * 60 + "Blocked by: none"):
            self.assertFalse(prune.still_waiting(said), said)
        # A coder's labelled lines describe the bot or the queue, not its own turn (the review's cases).
        for said in ("Done.\n\nBlocked by: waiting on I440's scan of the fold rounds",
                     "Committed.\n\nChoices: waiting until round 450 before the king splits.\n\nBlocked by: none",
                     "Follow-up I500: Waiting for pearls near the portal before entering.\n\nBlocked by: none",
                     "- **Follow-up I501**: waiting on a fresh map mix\nPredictable: no: waiting for games showed it",
                     "Changed: the king now waits for its split child.\n\nBlocked by: none",
                     "No code: waiting on I440's scan of the fold rounds\n\nBlocked by: none"):
            self.assertFalse(prune.still_waiting(said), said)
        self.assertTrue(prune.still_waiting("Blocked by: none\n\nWaiting for the quick check to finish."))
        self.assertTrue(prune.turn_ok('{"result": "Nothing I started is still running.", "is_error": false}'))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            build(state, [row("E0068", "discarded", started=prune.TURNS_FROM + 60)], {})
            (state / "runs" / "E0068" / "postmortem.json").write_text(waiting)
            prune.tick(state)
            self.assertIn("E0068", left(state))  # still owed, so its losses stay


    def test_a_coder_saying_it_hands_in_no_code_on_purpose_is_told_from_one_cut_off(self) -> None:
        # E0111 was stopped by the orchestrator and left its measurement edits uncommitted on purpose; E0197 was cut off
        # waiting for its games (their real rows in testdata).
        data = Path(__file__).parent / "testdata"
        summary = lambda name: json.loads((data / name).read_text())["session"]["summary"]
        self.assertTrue(prune.no_code_on_purpose(summary("result-E0111-no-code.json")))
        self.assertFalse(prune.no_code_on_purpose(summary("result-E0197-waiting.json")))
        self.assertTrue(prune.no_code_on_purpose("Measured it: 0.05 changed moves a game.\nNo code: below the SPRT's reach"))
        self.assertFalse(prune.no_code_on_purpose("Still waiting on the quick check."))  # E0045
        # Leaving work uncommitted is what a cut-off coder does too (the review's case).
        self.assertFalse(prune.no_code_on_purpose(
            "I have left the fix uncommitted until the quick check finishes; it is still running."))

    def test_dev_replays_go_two_versions_on_and_their_summaries_stay(self) -> None:
        # online-replays kept up to online.DEV_REPLAYS dev replays a team a version (some 700 MB) with nothing pruning.
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "online.json").write_text(json.dumps({"submission_id": 7700, "dev": {"teams": [{"id": 752}]},
                                                          "benchmark": {"team": {"id": 545}}}))
            for version in (7625, 7676, 7700):
                (state / "online-replays" / str(version)).mkdir(parents=True)
                games = []
                for gid, opponent, mark in ((1, 262, False), (2, 545, False), (3, 999, True), (4, 752, False)):
                    replay = state / "online-replays" / str(version) / f"{version}{gid}.replay"
                    replay.write_bytes(b"x" * 100)
                    games.append({"id": version * 10 + gid, "opponent": opponent, "replay": str(replay),
                                  "side": "A", "summary": {"result": {"winner": "A"}},
                                  **({"dev": True} if mark else {})})
                (state / "online-replays" / str(version) / "index.json").write_text(json.dumps({"games": games}))
            line = prune.tick(state)
            left = sorted(p.name for p in (state / "online-replays").glob("*/*.replay"))
            self.assertEqual(left, ["76251.replay"] + [f"{v}{g}.replay" for v in (7676, 7700) for g in (1, 2, 3, 4)])
            self.assertIn("packed 3 dev-team replays", line)
            listed = subprocess.run(f"zstd -dc -q {state / 'online-replays/7625/replays.tar.zst'} | tar -tf -",
                                    shell=True, capture_output=True, text=True).stdout.split()
            self.assertEqual(sorted(listed), ["76252.replay", "76253.replay", "76254.replay"])  # kept, not deleted
            index = json.loads((state / "online-replays/7625/index.json").read_text())
            self.assertEqual(len(index["games"]), 4)  # the summaries stay
            self.assertEqual(prune.tick(state), "")

    # Three versions' folders, each with a ladder game (262) and dev games (545 the benchmark, 999 marked, 752 listed).
    def dev_versions(self, state: Path, live: int | None, ranked: tuple[int, ...] = ()) -> None:
        (state / "online.json").write_text(json.dumps({"submission_id": live, "dev": {"teams": [{"id": 752}]},
                                                      "benchmark": {"team": {"id": 545}}}))
        for version in (7625, 7676, 7700):
            (state / "online-replays" / str(version)).mkdir(parents=True)
            games = []
            for gid, opponent, mark in ((1, 262, False), (2, 545, False), (3, 999, True), (4, 752, False)):
                replay = state / "online-replays" / str(version) / f"{version}{gid}.replay"
                replay.write_bytes(b"x" * 100)
                games.append({"id": version * 10 + gid, "opponent": opponent, "replay": str(replay),
                              "ranked": version in ranked, "side": "B", "summary": {"result": {"winner": "B"}},
                              **({"dev": True} if mark else {})})
            (state / "online-replays" / str(version) / "index.json").write_text(json.dumps({"games": games}))

    def left(self, state: Path) -> list[str]:
        return sorted(p.name for p in (state / "online-replays").glob("*/*.replay"))

    def test_an_alerted_comparisons_versions_keep_their_dev_replays_for_the_hold(self) -> None:
        # 9553's alert against 9347 was looked into once 9695 was live, and 9347's dev replays had gone. Here 7625 is
        # two versions back from the live 7700, held with 7676 by an alert: its dev wins stay until the hold ends.
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.dev_versions(state, 7700)
            report = json.loads((state / "online.json").read_text())
            held = {**report, "held": {"versions": [7625, 7676], "at": time.time() - prune.HOLD_S + 60}}
            (state / "online.json").write_text(json.dumps(held))
            self.assertEqual(prune.tick(state), "")
            self.assertEqual(len(self.left(state)), 12)
            ended = {**held, "held": {**held["held"], "at": time.time() - prune.HOLD_S}}
            (state / "online.json").write_text(json.dumps(ended))
            self.assertIn("packed 3 dev-team replays", prune.tick(state))
            self.assertEqual(self.left(state)[0], "76251.replay")

    def test_a_dev_loss_or_a_game_with_no_readable_result_keeps_its_replay(self) -> None:
        # Real rows of 9347 against dev team 545 (summaries cut to the result): 434802 won as A, and 436945 was the
        # Prisoners Dilemma loss (round 99) that idea I666 cites, whose replay prune deleted with the wins; the third
        # is 434802's row without its summary, so its result can't be read.
        rows = json.loads((Path(__file__).parent / "testdata/prune-dev-rows-sample.json").read_text())
        win, loss = rows
        self.assertEqual((prune.won(win), prune.won(loss)), (True, False))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "online.json").write_text(json.dumps({"submission_id": 9762, "dev": {"teams": [{"id": 545}]}}))
            for version in (9347, 9695, 9762):  # 9695 the version before the live one: kept_versions
                (state / "online-replays" / str(version)).mkdir(parents=True)
                ladder = [{"id": version, "opponent": 262, "ranked": False}]
                (state / "online-replays" / str(version) / "index.json").write_text(json.dumps({"games": ladder}))
            games = [win, loss, {**{k: v for k, v in win.items() if k != "summary"}, "id": 1}]
            for game in games:
                replay = state / "online-replays/9347" / f"{game['id']}.replay"
                replay.write_bytes(b"x" * 100)
                game["replay"] = str(replay)
            (state / "online-replays/9347/index.json").write_text(json.dumps({"games": games}))
            self.assertIn("packed 1 dev-team replays", prune.tick(state))
            self.assertEqual(self.left(state), ["1.replay", "436945.replay"])
            # With the win gone the files left are chosen by result, so a reader of files finds none; the summaries are
            # still the sample (the win and the unreadable row; 436945 was kept past it by online.collect_games), and
            # the other versions' rows stay.
            saved = json.loads((state / "online-replays/9347/index.json").read_text())["games"]
            self.assertEqual([g.get("kept") for g in saved], [None, "loss", None])
            self.assertEqual([g for g in saved if not online.file_selected(g) and Path(g["replay"]).exists()], [])
            self.assertEqual([g["id"] for g in online.sample_games(saved)], [434802, 1])
            self.assertEqual(json.loads((state / "online-replays/9695/index.json").read_text())["games"],
                             [{"id": 9695, "opponent": 262, "ranked": False}])
            self.assertEqual(prune.tick(state), "")  # marked once

    def test_the_kept_marks_the_old_marking_wrote_are_undone_and_collected_losses_stay_kept(self) -> None:
        # Real rows of 9695 against dev team 545 (summaries cut to the result, doom dropped), as its index read on
        # 2026-09-28: 454868 a win, 454873 a loss of the sample that the old mark_dev_losses marked "kept" (after
        # "replay"), 454872 a loss online.collect_games kept past the sample (its "kept" before "replay"). Marked, a
        # summary reader read 9695's dev games against 545 as wins only.
        win, marked, collected = json.loads((Path(__file__).parent / "testdata/prune-restore-rows-sample.json")
                                            .read_text())
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "online.json").write_text(json.dumps({"submission_id": 9918, "dev": {"teams": [{"id": 545}]}}))
            for version in (9695, 9762, 9918):
                (state / "online-replays" / str(version)).mkdir(parents=True)
                ladder = [{"id": version, "opponent": 262, "ranked": False}]
                (state / "online-replays" / str(version) / "index.json").write_text(json.dumps({"games": ladder}))
            # Marks after "replay" the old code never wrote stay: on a won game, a ladder game, another value, and in
            # the live version (not prunable).
            ladder = {k: v for k, v in marked.items() if k != "dev"} | {"id": 2, "opponent": 262}
            others = [{**win, "id": 1, "kept": "loss"}, ladder, {**marked, "id": 3, "kept": "later"}]
            (state / "online-replays/9695/index.json").write_text(json.dumps({"games": [win, marked, collected,
                                                                                        *others], "invalid": {}}))
            live = [{**marked, "id": 4}]
            (state / "online-replays/9918/index.json").write_text(json.dumps({"games": live}))
            self.assertEqual([g["id"] for g in online.sample_games([win, marked, collected])], [454868])
            line = prune.tick(state)
            self.assertIn("restored 1 old dev losses", line)
            index = json.loads((state / "online-replays/9695/index.json").read_text())
            saved = index["games"]
            self.assertEqual([g.get("kept") for g in saved], [None, None, "loss", "loss", "loss", "later"])
            self.assertEqual([g["id"] for g in online.sample_games(saved)], [454868, 454873])
            self.assertEqual(json.loads((state / "online-replays/9918/index.json").read_text())["games"], live)
            self.assertTrue(all(map(online.file_selected, saved)))  # every file of the version is no sample now
            self.assertEqual(index["invalid"], {})  # the rest of the index as it was
            self.assertEqual(prune.tick(state), "")  # once

    def test_nothing_is_pruned_or_marked_while_an_online_cycle_writes_the_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.dev_versions(state, 7700)
            before = self.left(state)
            with open(state / "online.lock", "w") as handle:  # meta.locked(state / "online"), as online.cycle takes
                fcntl.flock(handle, fcntl.LOCK_EX)
                self.assertEqual(prune.tick(state), "")
            self.assertEqual(self.left(state), before)
            self.assertIn("packed", prune.tick(state))

    def test_an_older_submission_activated_by_hand_keeps_its_dev_replays(self) -> None:
        # Before: the two highest ids were kept, so the live 7625's dev replays went as they downloaded (and their
        # index rows stayed, so they were never downloaded again).
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.dev_versions(state, 7625)
            self.assertIn("packed 6 dev-team replays", prune.tick(state))
            self.assertEqual(self.left(state), [f"7625{g}.replay" for g in (1, 2, 3, 4)] + ["76761.replay",
                                                                                           "77001.replay"])

    def test_the_previous_version_is_the_newest_earlier_one_with_an_unranked_game(self) -> None:
        # online.previous_version's pick: 7676 played only ranked games, so 7625 is the one before the live 7700.
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.dev_versions(state, 7700, (7676,))
            prune.tick(state)
            self.assertEqual(self.left(state), [f"{v}{g}.replay" for v in (7625,) for g in (1, 2, 3, 4)]
                             + ["76761.replay"] + [f"7700{g}.replay" for g in (1, 2, 3, 4)])

    def test_nothing_goes_without_a_live_submission_and_a_vanished_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.dev_versions(state, None)  # a cycle that saw the submission change clears it
            self.assertEqual(prune.tick(state), "")
            self.assertEqual(len(self.left(state)), 12)
            report = json.loads((state / "online.json").read_text())
            (state / "online.json").write_text(json.dumps({**report, "submission_id": 7700}))
            found = prune.old_dev_replays(state)
            self.assertEqual(sorted(p.name for p in found), ["76252.replay", "76253.replay", "76254.replay"])
            found[0].unlink()  # another process pruning at the same time got there first
            with patch.object(prune, "old_dev_replays", return_value=found):
                self.assertIn("packed 2 dev-team replays", prune.tick(state))
            self.assertEqual(self.left(state), ["76251.replay"] + [f"{v}{g}.replay" for v in (7676, 7700)
                                                                   for g in (1, 2, 3, 4)])


if __name__ == "__main__":
    unittest.main()
