"""Version identity and ranked-budget checks, with no real server mutations."""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
import zipfile
from pathlib import Path
from unittest.mock import patch

import online


# GET /battles and /battles/:id as the site answered after it dropped submission ids (online.API_GONE), trimmed.
NEW_API = json.loads((Path(__file__).parent / "testdata/online-api-no-submissions-sample.json").read_text())
REPLAYS = Path(__file__).resolve().parent.parent / "tools/testdata"  # real replays naming our submission on one side


def game(submission=3182, side="A"):
    other = "B" if side == "A" else "A"
    return {"match": {"id": 91, f"team{side}Id": 157, f"team{other}Id": 213,
                      f"submission{side}Id": submission, f"submission{other}Id": 123,
                      "ranked": True, "status": "completed", "replayKey": "replay",
                      "winner": side.lower(), "requestedAt": "2026-09-23T12:00:00Z"}}


class TestVersionEvidence(unittest.TestCase):
    def test_bad_replay_is_quarantined_without_losing_other_games(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            def detail(path):
                result = game()
                result["match"]["id"] = int(path.rsplit('/', 1)[1])
                return result
            summaries = [json.dumps({"checks": {"ok": False}}), json.dumps({"checks": {"ok": True}})]
            from subprocess import CompletedProcess
            with patch.object(online.ladder, "get", side_effect=detail), \
                    patch.object(online.ladder, "download", return_value=b"replay"), \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, s) for s in summaries]):
                games = online.collect_games(state, state, 157, 3182,
                    [{"games": [{"id": 1, "status": "completed"}, {"id": 2, "status": "completed"}]}])
            self.assertEqual([g["id"] for g in games], [2])
            saved = json.loads((state / "online-replays/3182/index.json").read_text())
            self.assertEqual(saved["invalid"]["1"]["attempts"], 1)

    def test_a_previous_versions_games_are_kept_under_that_version(self):
        # 5143's and 5300's battles were dropped once a newer submission went live, starving compare.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            owners = {1: 3181, 2: 3182}
            def detail(path):
                gid = int(path.rsplit('/', 1)[1])
                return game(owners[gid]) | {"match": {**game(owners[gid])["match"], "id": gid}}
            from subprocess import CompletedProcess
            ok = json.dumps({"checks": {"ok": True}})
            with patch.object(online.ladder, "get", side_effect=detail), \
                    patch.object(online.ladder, "download", return_value=b"replay"), \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, ok)] * 2):
                games = online.collect_games(state, state, 157, 3182,
                    [{"games": [{"id": 1, "status": "completed"}, {"id": 2, "status": "completed"}]}])
            self.assertEqual([g["id"] for g in games], [2])
            older = json.loads((state / "online-replays/3181/index.json").read_text())
            self.assertEqual([g["id"] for g in older["games"]], [1])

    def test_prompt_includes_verified_source_and_concrete_loss_causes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            row = {"id": 1, "submission_id": 3182, "opponent": 8, "opponent_submission_id": 99,
                   "map_id": 4, "side": "A", "ranked": False, "replay": "saved.replay",
                   "summary": {"result": {"winner": "B"}, "teams": {"A": {"deaths": {"wall": 7}}}}}
            online.save(state / "online.json", {"submission_id": 3182, "games": [row], "per_map": []})
            online.save(state / "publishing.json", {"verified": {"submission_id": 3182, "commit": "abc"}})
            text = online.evidence_text(state)
            self.assertIn("abc", text)
            self.assertIn('"wall": 7', text)
            self.assertIn("saved.replay", text)

    def test_only_our_team_can_supply_our_evidence_on_either_side(self):
        for side in ("A", "B"):
            match = online.match_for_team(game(side=side), 157)
            self.assertEqual((match["side"], match["our_submission"]), (side, 3182))
        self.assertIsNone(online.match_for_team(game(), 999))
        # Since 2026-09-28 a record names no submission and has no replayKey (API_GONE): our side is still found.
        record = NEW_API["record_475477"]
        self.assertEqual(online.match_for_team(record, 157)["side"], "A")
        self.assertIsNone(online.match_for_team(record, 157)["our_submission"])

    def test_pending_games_are_not_evidence_but_unranked_games_keep_their_label(self):
        detail = game()
        detail["match"]["status"] = "running"
        self.assertIsNone(online.match_for_team(detail, 157))
        detail = game()
        detail["match"]["ranked"] = False
        row = online.match_for_team(detail, 157)
        self.assertIsNotNone(row)
        self.assertFalse(row["ranked"])

    def test_archive_identity_depends_on_content_not_order_or_version_name(self):
        def archive(files):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as bundle:
                for name, text in files:
                    bundle.writestr(name, text)
            return raw.getvalue()
        files = [("main.cpp", "int main() {}\n"), ("bot.hpp", "#pragma once\n")]
        self.assertEqual(online.archive_digest(archive(files)), online.archive_digest(archive(files[::-1])))
        self.assertNotEqual(online.archive_digest(archive(files)),
                            online.archive_digest(archive([("main.cpp", "int main() {return 1;}\n"), files[1]])))

    def test_active_version_change_invalidates_cached_analysis(self):
        report = {"submission_id": 3181, "games": [{"match": 91}]}
        self.assertEqual(online.current_games(report, 3182), [])
        self.assertEqual(online.current_games(report, 3181), [{"match": 91}])


class TestRankedBudget(unittest.TestCase):
    def test_stop_and_active_version_change_prevent_post(self):
        for stopped in (None, "STOP", "ASTRA_STOP"):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                stop = state / "STOP"
                if stopped:
                    (state / stopped).touch()
                def get(path):
                    return {"/team": {"team": {"id": 157, "elo": 1500, "settings": {"ranked": True}}},
                            f"/battles?limit={online.BATTLE_WINDOW}": [], "/leaderboard": [{"id": 1, "hasBot": True}],
                            "/maps": [{"id": 11, "active": True}]}[path]
                with patch.object(online, "active_submission", side_effect=[{"id": 3182}, {"id": 3182 if stopped else 3183}]), \
                        patch.object(online, "collect_games", return_value=[]), \
                        patch.object(online.ladder, "get", side_effect=get), \
                        patch.object(online.ladder, "post", side_effect=AssertionError("must not submit")):
                    report = online.cycle(state, state, stop)
                self.assertFalse((state / "match-requests.json").exists())
                if not stopped:
                    self.assertIsNone(report["submission_id"])
                    self.assertEqual(report["games"], [])

    def test_a_failed_collection_keeps_the_last_success_time(self):
        # Without it, health.py read every failed or in-progress collection as "last success never".
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online.json").write_text(json.dumps({"attempt_at": 1, "fetched_at": 2, "submission_id": 3182}))
            with patch.object(online, "active_submission", return_value={"id": 3182}), \
                    patch.object(online.ladder, "get", side_effect=RuntimeError("server down")):
                report = online.cycle(state, state)
            self.assertEqual(report["fetched_at"], 2)
            self.assertIn("server down", json.loads((state / "online.json").read_text())["error"])

    def test_unknown_post_outcome_keeps_budget_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            team = {"id": 157, "elo": 1500, "settings": {"ranked": True}}
            def get(path):
                return {"/team": {"team": team}, f"/battles?limit={online.BATTLE_WINDOW}": [],
                        "/leaderboard": [{"id": 1, "elo": 1600, "hasBot": True, "ranked": True}],
                        "/maps": [{"id": 11, "active": True}]}[path]
            with patch.object(online, "active_submission", return_value={"id": 3182}), \
                    patch.object(online, "collect_games", return_value=[]), \
                    patch.object(online.ladder, "get", side_effect=get), \
                    patch.object(online.ladder, "post", side_effect=TimeoutError("unknown outcome")):
                result = online.cycle(state, state)
            rows = json.loads((state / "match-requests.json").read_text())
            self.assertEqual(rows[0]["games"], 1)
            self.assertEqual(rows[0]["status"], "unknown")
            self.assertIn("unknown outcome", result["error"])
            self.assertEqual(online.remaining_games(rows, rows[0]["at"] + 1), 49)

    def test_map_stats_separate_opponent_versions_sides_and_modes(self):
        row = {"id": 1, "submission_id": 3182, "opponent": 5, "opponent_submission_id": 100,
               "map_id": 11, "side": "A", "ranked": False, "summary": {"result": {"winner": "A"}}}
        games = [row, row, {**row, "id": 2, "opponent_submission_id": 101},
                 {**row, "id": 3, "side": "B"}, {**row, "id": 4, "ranked": True}]
        stats = online.per_map_stats(games)
        self.assertEqual(len(stats), 4)
        self.assertEqual(sum(g["games"] for g in stats), 4)
        self.assertEqual(sum(g["wins"] for g in stats), 3)

    def test_sweeps_cover_each_active_map_and_rotate_opponents(self):
        teams = [{"id": 1, "hasBot": True, "ranked": True, "elo": 1600},
                 {"id": 2, "hasBot": True, "ranked": True, "elo": 1700}]
        maps = [{"id": 11, "active": True}, {"id": 12, "active": True}, {"id": 99, "active": False}]
        first = online.choose_match(teams, maps, 157, 1500, set(), [], 3182, 10000)
        self.assertEqual(first, {"teamId": 1, "ranked": False, "mapIds": [11, 12]})
        rows = [{"at": 9000, "games": 2, "opponent": 1, "ranked": False, "submission_id": 3182}]
        self.assertEqual(online.choose_match(teams, maps, 157, 1500, set(), rows, 3182, 10000)["teamId"], 2)

    def test_listed_targets_sweep_first_until_expired(self):
        teams = [{"id": 1, "hasBot": True, "elo": 1500}, {"id": 206, "hasBot": True, "elo": 1500}]
        maps = [{"id": 11, "active": True}, {"id": 12, "active": True}]
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / "online-targets.json"
            path.write_text(json.dumps({"teams": [206], "until": "2026-10-01T12:00:00+10:00"}))
            now = datetime.fromisoformat("2026-10-01T09:00:00+10:00").timestamp()
            pick = lambda rows, when: online.choose_match(teams, maps, 157, 1500, set(), rows, 3182, when,
                                                          targets=online.sweep_targets(Path(home), when))
            self.assertEqual(pick([], now), {"teamId": 206, "ranked": False, "mapIds": [11, 12]})
            half = [{"at": now, "games": 1, "opponent": 206, "map_ids": [11], "submission_id": 3182}]
            self.assertEqual(pick(half, now)["mapIds"], [12])
            full = [{**half[0], "games": 2, "map_ids": [11, 12]}]
            self.assertEqual(pick(full, now)["teamId"], 1)  # swept, so the rotation takes over
            self.assertEqual(pick([], now + 4 * 3600), online.choose_match(teams, maps, 157, 1500, set(), [], 3182,
                                                                           now + 4 * 3600))

    def test_never_requests_ranked_and_skips_teams_that_refuse_unranked(self):
        # Teams 370, 577 and 217 answered ranked requests with HTTP 400: their settings had ranked switched off.
        teams = [{"id": 1, "hasBot": True, "ranked": True, "elo": 1600, "settings": {"unranked": False}},
                 {"id": 2, "hasBot": True, "ranked": True, "elo": 1600, "settings": {"ranked": False}},
                 {"id": 3, "hasBot": True, "ranked": True, "elo": 1600, "settings": {"ranked": True}}]
        maps = [{"id": 11, "active": True}]
        rows, picks = [], []
        for now in range(10000, 16000, 600):
            pick = online.choose_match(teams, maps, 157, 1500, set(), rows, 3182, now)
            picks.append((pick["teamId"], pick["ranked"]))
            rows.append({"at": now, "games": 1, "opponent": pick["teamId"], "ranked": False})
        self.assertEqual({p[1] for p in picks}, {False})
        self.assertEqual({p[0] for p in picks}, {2, 3})

    def test_shared_budget_and_known_map_matchups_prevent_wasted_requests(self):
        teams = [{"id": 1, "hasBot": True, "ranked": True, "elo": 1600}]
        maps = [{"id": 11, "active": True}, {"id": 12, "active": True}]
        rows = [{"at": 9900, "games": 49, "opponent": 2, "ranked": False}]
        self.assertEqual(online.choose_match(teams, maps, 157, 1500, set(), rows, 3182, 10000)["mapIds"], [11])
        rows[0]["games"] = 50
        self.assertIsNone(online.choose_match(teams, maps, 157, 1500, set(), rows, 3182, 10000))
        self.assertIsNone(online.choose_match(teams, maps, 157, 1500, {1}, [], 3182, 10000))

    def test_a_new_version_first_replays_the_previous_versions_opponents_on_the_same_maps(self):
        teams = [{"id": 1, "hasBot": True, "elo": 1600}, {"id": 2, "hasBot": True, "elo": 1700},
                 {"id": 3, "hasBot": True, "elo": 1500}]
        maps = [{"id": m, "active": True} for m in (11, 12, 13)]
        previous = [{"opponent": 2, "map_id": 11}, {"opponent": 2, "map_id": 13}, {"opponent": 3, "map_id": 12}]
        previous = [{**g, "requested_at": "1970-01-01T02:00:00Z", "opponent_submission_id": 99}  # the period of now
                    for g in previous]
        pick = online.choose_match(teams, maps, 157, 1500, set(), [], 4350, 10000, [], previous)
        self.assertEqual(pick, {"teamId": 2, "ranked": False, "mapIds": [11, 13]})
        # Games whose opponent version the site no longer names (online.API_GONE) are replayed too: they pair when no
        # rating read splits them (M73).
        unnamed = [{**g, "opponent_submission_id": None} for g in previous]
        self.assertEqual(online.choose_match(teams, maps, 157, 1500, set(), [], 4350, 10000, [], unnamed), pick)
        # Once requested (or played) by this version, those maps no longer need a pair; then normal rotation.
        rows = [{"at": 9900, "games": 2, "opponent": 2, "submission_id": 4350, "map_ids": [11, 13]}]
        self.assertEqual(online.choose_match(teams, maps, 157, 1500, set(), rows, 4350, 10000, [], previous)["teamId"], 3)
        played = [{"submission_id": 4350, "opponent": 3, "map_id": 12}]
        self.assertEqual(online.choose_match(teams, maps, 157, 1500, set(), rows, 4350, 10000, played, previous),
                         online.choose_match(teams, maps, 157, 1500, set(), rows, 4350, 10000, played))

    # The loop's sweep rows of 2026-09-27 03:23 to 09-29 03:04 UTC: team 106 (CheeseTub, 1497 to our 1993) took 56
    # games and lost all 56, while team 82 (1886) took 59 at our 0.52. The rotation went by least recently requested,
    # and the previous version's opponents were replayed first whoever they were, so CheeseTub kept being played.
    def test_the_sweep_goes_to_teams_that_still_beat_us_sometimes(self):
        teams = [{"id": 106, "hasBot": True, "elo": 1497}, {"id": 82, "hasBot": True, "elo": 1886}]
        maps = [{"id": m, "active": True} for m in (4, 7)]
        scores = {106: [1.0] * 56, 82: [1.0] * 31 + [0.0] * 28}
        rows = [{"at": 9000, "games": 2, "opponent": 82, "submission_id": 10774, "map_ids": [4, 7]}]  # 82 last
        pick = lambda previous=(): online.choose_match(teams, maps, 157, 1993, set(), rows, 10964, 10000, [],
                                                       previous, scores)
        self.assertEqual(pick()["teamId"], 82)
        previous = [{"opponent": o, "map_id": m, "requested_at": "1970-01-01T02:00:00Z", "side": "A",
                     "opponent_submission_id": None} for o, m in ((106, 4), (106, 7), (82, 4))]
        self.assertEqual(pick(previous), {"teamId": 82, "ranked": False, "mapIds": [4]})  # not 106's two maps
        self.assertLess(online.sweep_weight(scores[106], 1993, 1497), online.SWEEP_PAIR_WEIGHT)
        self.assertEqual(online.sweep_weight([], 1993, 1993), 1.0)  # unplayed, level: the most

    # After 2026-09-27 11:00 UTC the previous version's games (all before it) pair with none of this version's, so
    # the sweep chose opponents and maps for pairs compare_versions would reject.
    def test_the_previous_versions_games_lead_the_sweep_only_in_the_current_pool_period(self):
        teams = [{"id": 2, "hasBot": True, "elo": 1700}, {"id": 3, "hasBot": True, "elo": 1500}]
        maps = [{"id": m, "active": True} for m in (11, 12, 13)]
        previous = [{"opponent": 2, "map_id": 11, "requested_at": "2026-09-27T09:00:00Z"},
                    {"opponent": 3, "map_id": 12, "requested_at": "2026-09-27T12:00:00Z"},
                    {"opponent": 3, "map_id": 13, "requested_at": "2026-09-27T10:55:00Z"}]
        previous = [{**g, "opponent_submission_id": 99} for g in previous]
        at = lambda iso: datetime.fromisoformat(iso).timestamp()
        pick = lambda now: online.choose_match(teams, maps, 157, 1500, set(), [], 4350, now, [], previous)
        self.assertEqual(pick(at("2026-09-27T10:00:00+00:00")), {"teamId": 2, "ranked": False, "mapIds": [11]})
        self.assertEqual(pick(at("2026-09-27T13:00:00+00:00")), {"teamId": 3, "ranked": False, "mapIds": [12]})
        # Within the straddle window nothing pairs, so the sweep rotates as though there were no previous games.
        self.assertEqual(pick(at("2026-09-27T10:58:00+00:00")),
                         online.choose_match(teams, maps, 157, 1500, set(), [], 4350, at("2026-09-27T10:58:00+00:00")))

    def test_versions_compare_only_on_the_same_opponent_version_map_and_side(self):
        game = lambda gid, opp_sub, map_id, winner: {"id": gid, "opponent": 11, "opponent_submission_id": opp_sub,
                                                     "map_id": map_id, "side": "A",
                                                     "summary": {"result": {"winner": winner}}}
        before = [game(1, 3944, 3, "A"), game(2, 3944, 4, "A"), game(3, 3944, 5, "B")]
        after = [game(4, 3944, 3, "B"), game(5, 3944, 4, "A"), game(6, 3999, 5, "A"), game(7, 3944, 9, "A")]
        # Game 6 met another opponent version, so its matchup can't pair and isn't a candidate (the health check reads
        # candidates against unread, both matchups).
        self.assertEqual(online.compare_versions(4219, before, after),
                         {"previous": 4219, "pairs": 2, "inferred": 0, "candidates": 2, "unread": 0,
                          "unpairable": {"before": 0, "after": 0},
                          "unknown_opponent": {"before": 0, "after": 0},
                          "opponents": [{"opponent": 11, "submission": 3944, "pairs": 2}],
                          "before": 1.0, "after": 0.5, "better": 0, "worse": 1,
                          "same": 1, "worse_games": [4], "longest_margin": {"pairs": 0},
                          "ours": {fact: {"pairs": 0} for fact in online.OUR_FACTS},
                          "concentration": {who: {fact: {"pairs": 0} for fact in online.CONCENTRATION}
                                            for who in ("ours", "theirs")}})

    # M73: since the site stopped naming battles' submissions (MISSES 77) every game's opponent version is None, and the
    # compare paired none (10774's: 0 pairs, 249 of its games unknown). Three real sweep games of team 82 on Default,
    # side A (8473's, 8636's, 8942's, as the site named them then: 8077, 8077, 8806), with the names dropped, and the
    # real GET /ratings read (testdata/ratings-sample.json): 82's rating stepped 1913 -> 1939 at 16:35, a new bot's K,
    # between the second and third game, and by no more than 24 between the first two.
    def test_unknown_opponent_versions_pair_when_no_rating_read_splits_them(self):
        board = json.loads((Path(__file__).parent / "testdata/ratings-sample.json").read_text())
        splits = online.rating_splits(board, None, datetime.fromisoformat("2026-09-28T10:30:00+00:00").timestamp())
        self.assertEqual(splits["teams"]["801"]["splits"], [])  # its steps never passed 16
        game = lambda gid, sid, at, winner: {"id": gid, "submission_id": sid, "opponent": 82,
                                             "opponent_submission_id": None, "side": "A", "map_id": 4,
                                             "ranked": False, "requested_at": at,
                                             "summary": {"result": {"winner": winner}}}
        g8473 = game(382354, 8473, "2026-09-27T11:24:08.097Z", "B")
        g8636 = game(392822, 8636, "2026-09-27T13:08:25.227Z", "B")
        g8942 = game(408056, 8942, "2026-09-27T16:36:08.711Z", "A")
        same = online.compare_versions(8473, [g8473], [g8636], splits)
        self.assertEqual((same["pairs"], same["inferred"], same["candidates"], same["same"]), (1, 1, 1, 1))
        split = online.compare_versions(8636, [g8636], [g8942], splits)
        self.assertEqual((split["pairs"], split["candidates"]), (0, 1))  # the site named 8077 then 8806
        self.assertEqual(online.compare_versions(8473, [g8473], [g8636])["pairs"], 0)  # no read: no pair
        early = {**splits, "teams": {**splits["teams"], "82": {**splits["teams"]["82"], "to": 1790510000.0}}}  # 12:13 UTC
        waiting = online.compare_versions(8473, [g8473], [g8636], early)  # read before 8636's game: not covered yet
        self.assertEqual((waiting["pairs"], waiting["unread"]), (0, 1))  # the health check waits for the next read
        # A week on, 82's 16:35 split is pruned, and what the reads covered moves up with it: before, `from` stayed at
        # the first read, so 8636 and 8942 (two bots) read as unsplit.
        pruned = online.rating_splits({}, splits, datetime.fromisoformat("2026-10-04T17:00:00+00:00").timestamp())
        self.assertEqual(online.unsplit(pruned, 82, g8636["requested_at"], g8942["requested_at"]), False)

    # Where M73's inferred pairs reach (review of M73): 20 of them all worse, the longest margin down in each, hold the
    # replays and alert, but the alert only asks for a confirming sample, and no re-check is raised or queued (sig,
    # 2026-09-29: re-checks on measured evidence only; the rating rule can't see a team swapping bots in unranked games).
    def test_inferred_pairs_hold_replays_and_alert_only_to_wait_and_never_raise_rechecks(self):
        splits = {"teams": {"11": {"from": 0.0, "to": 1e10, "splits": []}}}
        game = lambda gid, sid, at, winner, ours: {
            "id": gid, "submission_id": sid, "opponent": 11, "opponent_submission_id": None, "map_id": gid % 100,
            "side": "A", "start": "s", "requested_at": at,
            "summary": {"result": {"winner": winner}, "teams": {"A": {"end": {"longest": ours}},
                                                                "B": {"end": {"longest": 20}}}}}
        before = [game(m, 10510, "2026-09-29T01:00:00Z", "A", 30) for m in range(20)]
        after = [game(100 + m, 10568, "2026-09-29T02:00:00Z", "B", 10) for m in range(20)]
        compare = online.compare_versions(10510, before, after, splits)
        self.assertEqual((compare["pairs"], compare["inferred"], compare["worse"], compare["named"]["pairs"]),
                         (20, 20, 20, 0))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report = {"submission_id": 10568, "compare": compare}
            online.save(state / "online.json", report)
            alert = online.version_alert(state)
            self.assertIn("20 of the 20 pairs are inferred", alert)
            self.assertIn("wait for a confirming sample", alert)
            self.assertNotIn("find out why local tests missed it (CLAUDE.md", alert)
            self.assertIsNone(online.raise_rechecks(state, state, report))
        self.assertTrue(online.margin_fell(compare) and not online.margin_fell(online.named_view(compare)))
        self.assertEqual(online.hold_replays(None, compare, 10568, 3000.0), {"versions": [10510, 10568], "at": 3000.0})

    def test_compare_shows_a_longest_margin_change_even_when_no_winner_flips(self):
        # E0059's fold changed none of 17 online winners; its effect was on longest at r499 (review M10).
        game = lambda gid, ours, theirs: {"id": gid, "opponent": 11, "opponent_submission_id": 3944, "map_id": gid % 10,
                                          "side": "B", "summary": {"result": {"winner": "A"}, "teams": {
                                              "B": {"end": {"longest": ours}}, "A": {"end": {"longest": theirs}}}}}
        before = [game(1, 20, 40), game(2, 30, 30)]
        after = [game(11, 35, 40), game(12, 28, 30)]
        compare = online.compare_versions(4219, before, after)
        self.assertEqual((compare["same"], compare["longest_margin"]),
                         (2, {"pairs": 2, "before": -10.0, "after": -3.5, "up": 1, "down": 1}))

    def test_compare_shows_where_the_rest_of_our_length_went_and_the_opening(self):
        # M15: merging our second-longest into the king flips most round-500 losses; the longest alone hides it.
        def game(gid, second, total, alive, pearls, end_reason="roundLimit"):
            end = {"longest": 30, "total": total, "alive": alive} | ({"second": second} if second is not None else {})
            ours = {"end": end, "aliveAt100": alive + 1, "behaviour": {"pearlsByPhase": pearls}}
            return {"id": gid, "opponent": 11, "opponent_submission_id": 3944, "map_id": gid % 10, "side": "A",
                    "summary": {"result": {"winner": "B", "endReason": end_reason},
                                "teams": {"A": ours, "B": {"end": {"longest": 35}}}}}
        before = [game(1, None, 60, 12, {"early": 40, "mid": 90}), game(2, None, 50, 8, {"mid": 70}),
                  game(3, None, 70, 10, {"early": 30})]
        # Game 13 was eliminated: its 0 dragons are losses, not a fold, so it gives no end facts, only the opening.
        after = [game(11, 12, 58, 6, {"early": 50}), game(12, 20, 52, 4, {"early": 10}),
                 game(13, 0, 0, 0, {"early": 30}, "teamEliminated")]
        ours = online.compare_versions(4219, before, after)["ours"]
        self.assertEqual(ours["second"], {"pairs": 0})  # summaries from before the field existed
        self.assertEqual(ours["total"], {"pairs": 2, "before": 55.0, "after": 55.0, "up": 1, "down": 1})
        self.assertEqual(ours["few_pct"], {"pairs": 2, "before": 50.0, "after": 100.0, "up": 1, "down": 0})
        self.assertEqual(ours["alive100"], {"pairs": 3, "before": 11.0, "after": 4.3, "up": 0, "down": 3})
        self.assertEqual(ours["pearls100"], {"pairs": 3, "before": 23.3, "after": 30.0, "up": 2, "down": 0})
        again = online.compare_versions(4219, after, after)["ours"]["second"]
        self.assertEqual(again, {"pairs": 2, "before": 16.0, "after": 16.0, "up": 0, "down": 0})

    def test_alert_only_when_clearly_worse_on_enough_identical_matchups(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            compare = {"previous": 4219, "pairs": 18, "before": 0.67, "after": 0.39, "better": 1, "worse": 6}
            (state / "online.json").write_text(json.dumps({"submission_id": 4350, "compare": compare}))
            self.assertIn("0.39 against 0.67", online.version_alert(state))
            # REFLECT 2026-09-28 17:52 F7: one check of many is a look among many. 6 worse, 1 better is p 0.062:
            # past the bar for one version checked, not for 50 (v0051's first read, p 0.035, re-paired to 0.19).
            self.assertIn("one of 1 versions checked, where luck alone gives up to 0.1 such alerts (p 0.062 alone, "
                          "0.062 over the 1); find out why",
                          online.version_alert(state))
            (state / "uploads.jsonl").write_text("".join(json.dumps({"previous": 3000 + n}) + "\n" for n in range(50)))
            text = online.version_alert(state)
            self.assertIn("sign test p 0.062); one of 50 versions checked, where luck alone gives up to 5.0", text)
            self.assertIn("(p 0.062 alone, 1.000 over the 50)", text)
            self.assertIn("not under 0.002 (0.1 over those checks), so wait for a confirming sample", text)
            (state / "online.json").write_text(json.dumps({"submission_id": 4350, "compare": {
                **compare, "worse": 16, "better": 1}}))  # p 0.0001: far below the bar, straight to the why
            self.assertIn("find out why local tests missed it", online.version_alert(state))
            # 7003 vs 6925 (2026-09-26): 3 worse, 1 better of 18 is luck 31% of the time.
            for change in ({"pairs": 17}, {"after": 0.6}, {"after": 0.67, "before": 0.78, "worse": 3, "better": 1}):
                (state / "online.json").write_text(json.dumps({"submission_id": 4350, "compare": {**compare, **change}}))
                self.assertIsNone(online.version_alert(state))

    def test_redirect_to_storage_does_not_receive_credentials(self):
        import ladder
        original = urllib.request.Request("https://game.battlecode.au/api/v1/battles/1/replay",
                                          headers={"Authorization": "Bearer test-only"})
        redirected = ladder.SafeRedirect().redirect_request(original, None, 302, "Found", {}, "https://storage.example/game")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_ranked_challenges_spend_five_games_and_count_failed_attempts(self):
        rows = [{"at": 1000, "games": 5, "status": "unknown"}] * 9
        self.assertEqual(online.remaining_games(rows, 1100), 5)
        self.assertEqual(online.remaining_games(rows + [{"at": 1050, "games": 5}], 1100), 0)
        self.assertEqual(online.remaining_games(rows, 4601), 50)


# Real online games (captured 2026-09-26, replay paths dropped): 6609 then 6736 as side A against team 545's
# submission 4525 on Stronghold (both to the round limit) and Default (6736 eliminated in round 402), with summaries
# from the current replay tools; and 3182's saved entry for game 95034 on Trauma (side B), summarised before at400.
TESTDATA = Path(__file__).parent / "testdata"
PAIRS = json.loads((TESTDATA / "online-pairs-sample.json").read_text())
OLD_SUMMARY = json.loads((TESTDATA / "online-old-summary-95034.json").read_text())
TRAUMA_REPLAY = Path(__file__).resolve().parent.parent / "tools/testdata/trauma-95034.replay"


class TestConcentration(unittest.TestCase):
    def test_compare_shows_each_sides_longest_at_400_and_how_much_of_its_length_ended_in_it(self):
        # M21: our r400 total matched theirs while our longest there was a third of theirs.
        before, after = [g for g in PAIRS if g["submission_id"] == 6609], [g for g in PAIRS if g["submission_id"] == 6736]
        compare = online.compare_versions(6609, before, after)["concentration"]
        # Stronghold: ours 3 -> 5 at r400, 28 of 124 -> 37 of 133 kept; Default: 12 -> 3 at r400, and 6736 was
        # eliminated in round 402, so it has a round-400 longest but no share kept at the round limit.
        self.assertEqual(compare["ours"], {"longest400": {"pairs": 2, "before": 7.5, "after": 4.0, "up": 1, "down": 1},
                                           "keep_pct": {"pairs": 1, "before": 22.6, "after": 27.8, "up": 1, "down": 0}})
        self.assertEqual(compare["theirs"], {"longest400": {"pairs": 2, "before": 8.0, "after": 7.5, "up": 0, "down": 1},
                                             "keep_pct": {"pairs": 1, "before": 34.7, "after": 29.8, "up": 0,
                                                          "down": 1}})

    # From 2026-09-27 21:00 AEST a map may keep its id while its pearl timings change (rtrgrd, #announcements):
    # before, a game on it after the change paired with one before it, and version_alert, margin_fell and
    # raise_rechecks acted on the difference.
    def test_games_either_side_of_the_pool_change_never_pair(self):
        before, after = [g for g in PAIRS if g["submission_id"] == 6609], [g for g in PAIRS if g["submission_id"] == 6736]
        at = lambda games, when: [{**g, "requested_at": when} for g in games]
        self.assertEqual(online.compare_versions(6609, before, after)["pairs"], 2)
        self.assertEqual(online.compare_versions(6609, before, at(after, "2026-09-27T11:00:00.000Z"))["pairs"], 0)
        self.assertEqual(online.compare_versions(6609, before, at(after, "2026-09-27T10:49:59.999Z"))["pairs"], 2)
        # Requested within POOL_STRADDLE before the cutoff, a battle may play after it: it pairs with nothing, not
        # even another such game (dev battles took up to 4 min from request to done).
        near = at(after, "2026-09-27T10:59:59.999Z")
        self.assertEqual(online.compare_versions(6609, before, near)["pairs"], 0)
        self.assertEqual(online.compare_versions(6609, at(before, "2026-09-27T10:55:00Z"), near)["pairs"], 0)
        self.assertEqual(online.map_compare(near, near)["maps"], [])
        later = at(before, "2026-09-27T12:00:00.000Z")
        self.assertEqual(online.compare_versions(6609, later, at(after, "2026-09-28T01:00:00.000Z"))["pairs"], 2)
        self.assertEqual(online.compare_versions(6609, at(before, None), after)["pairs"], 0)  # no time: its own period
        # The per-map rows and totals keep the two periods apart too.
        moved = [{**g, "id": g["id"] + 1} for g in at(after, "2026-09-27T11:30:00.000Z")]
        rows = online.per_map_stats(after + moved)
        self.assertEqual(len(rows), 4)
        totals = online.map_totals(rows)
        self.assertEqual([(t["map_id"], t["pool"], t["games"]) for t in totals],
                         [(4, "before 2026-09-27T11:00:00Z", 1), (4, "from 2026-09-27T11:00:00Z", 1),
                          (14, "before 2026-09-27T11:00:00Z", 1), (14, "from 2026-09-27T11:00:00Z", 1)])
        self.assertEqual(online.map_totals([{k: v for k, v in rows[0].items() if k != "pool"}])[0]["pool"], "unknown")

    def test_a_summary_from_before_at400_gives_no_concentration(self):
        self.assertEqual(online.concentration(OLD_SUMMARY, "ours"), {"longest400": None, "keep_pct": None})

    def test_old_summaries_are_refreshed_from_their_replay_and_a_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/3182"
            folder.mkdir(parents=True)
            broken = folder / "1.replay"
            broken.write_bytes(b"not a replay")
            games = [{**OLD_SUMMARY, "replay": str(TRAUMA_REPLAY)}, {**OLD_SUMMARY, "id": 1, "replay": str(broken)}]
            (folder / "index.json").write_text(json.dumps({"games": games, "invalid": {}}))
            repo = Path(__file__).resolve().parent.parent
            online.refresh_summaries(repo, state, [3182])
            saved = json.loads((folder / "index.json").read_text())["games"]
            # Side B (ours) was 4 long of 39 at r400 and ended 6 long; side A was 4 of 21.
            ours = online.concentration(saved[0], "ours")
            self.assertEqual((ours["longest400"], round(ours["keep_pct"], 1)), (4, 15.4))
            self.assertEqual(saved[0]["summary"]["teams"]["A"]["at400"], {"longest": 4, "total": 21})
            self.assertIn("refresh_failed", saved[1])
            with patch.object(online, "summarize") as summarize:
                online.refresh_summaries(repo, state, [3182])
            summarize.assert_not_called()

    # batch-health (2026-09-30): 25 summaries a cycle held an upkeep tick up to 17 min. Once the budget is spent no
    # more start: the stale rest keep their old summary for the next cycle.
    def test_summaries_stop_starting_once_the_cycles_budget_is_spent(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/3182"
            folder.mkdir(parents=True)
            games = [{**OLD_SUMMARY, "id": n, "replay": str(TRAUMA_REPLAY)} for n in range(3)]
            (folder / "index.json").write_text(json.dumps({"games": games, "invalid": {}}))
            clock = iter([0.0, 0.0, 70.0, 130.0])  # the deadline, then before each game: two start, the third doesn't
            with patch.object(online.time, "monotonic", side_effect=lambda: next(clock, 130.0)), \
                    patch.object(online, "summarize", return_value={"new": True}) as summarize:
                online.refresh_summaries(Path("/repo"), state, [3182], budget=120)
            self.assertEqual(summarize.call_count, 2)
            saved = json.loads((folder / "index.json").read_text())["games"]
            self.assertEqual([g["summary"] == {"new": True} for g in saved], [True, True, False])


# A real /leaderboard sample (2026-09-26): every team marked "dev", ours, the rank-1 team, teams near our rating and
# the non-dev team named "DEV"; `history` and `members` dropped for size.
LEADERBOARD = json.loads((Path(__file__).parent / "testdata/leaderboard-sample.json").read_text())
LADDER_MAPS = [{"id": m, "active": True} for m in (3, 4, 5, 7, 9, 11, 13, 14, 15)] + [{"id": 1, "active": False}]
# Captured 2026-09-27 from <loop home> (read only): our games against dev test 1 (545, submission 4525) by 7625 (from
# online-replays/7625/index.json) and 7676 (online.json), trimmed to what the records read; match-requests.json's
# two dev test 1 rows for them and its last four rows (7676's sweeps of teams 950, 857, 123 and 939); and the active
# map ids those rows asked for.
DEV_GAMES = json.loads((Path(__file__).parent / "testdata/online-dev-sample.json").read_text())
REQUESTS = json.loads((Path(__file__).parent / "testdata/match-requests-sample.json").read_text())
# Read-only GETs of the live site, 2026-09-27 (its `note` says which): our newest battles, a dev battle's detail, the
# map pool and six of that battle's games' own records.
BATTLES = json.loads((Path(__file__).parent / "testdata/online-battles-sample.json").read_text())
LIVE_MAPS = [4, 7, 9, 11, 13, 15, 17, 19, 20, 21]


def played(gid, submission, opponent, dev_submission, map_id, winner):
    return {"id": gid, "submission_id": submission, "opponent": opponent, "opponent_submission_id": dev_submission,
            "map_id": map_id, "side": "A", "ranked": False, "summary": {"result": {"winner": winner}}}


# One online.cycle at `now` with the site stubbed: `battles` listed, `details` by battle id (any other id is a 404),
# collect_games replaced by `collect` (else one game); returns the report and the post mock.
def run_cycle(state, leaderboard, post=None, now=None, battles=(), details=None, collect=None):
    def get(path):
        if path.startswith("/battles/"):
            if int(path.rsplit("/", 1)[1]) not in (details or {}):  # e.g. a request row's made-up battle id
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            return details[int(path.rsplit("/", 1)[1])]
        return {"/team": {"team": {"id": 157, "elo": 1865}}, f"/battles?limit={online.BATTLE_WINDOW}": list(battles),
                "/leaderboard": leaderboard, "/maps": LADDER_MAPS}[path]
    with patch.object(online, "active_submission", return_value={"id": 6111}), \
            patch.object(online, "collect_games", **({"side_effect": collect} if collect else
                                                      {"return_value": [played(7, 6111, 545, 4525, 3, "B")]})), \
            patch.object(online.ladder, "get", side_effect=get), \
            patch.object(online.ladder, "post", **(post or {"return_value": {"ids": [1]}})) as sent, \
            patch.object(online.time, "sleep"), \
            patch.object(online.time, "time", return_value=now or 10000.0):
        return online.cycle(state, state, state / "STOP"), sent


class TestDevBenchmark(unittest.TestCase):
    def test_dev_teams_come_from_the_dev_flag_and_frontier_wins_over_rating(self):
        self.assertEqual(online.dev_benchmark(LEADERBOARD),
                         {"id": 545, "name": "dev test 1 :P", "elo": 1993,
                          "why": "highest-rated dev team, none named frontier"})
        renamed = [{**t, "name": "frontier"} if t["id"] == 609 else t for t in LEADERBOARD]
        self.assertEqual(online.dev_benchmark(renamed)["id"], 609)  # rated 1683, below dev test 1
        # Team 391 is named "DEV" but isn't a dev team; a non-dev team named frontier isn't one either.
        impostors = [{**t, "name": "frontier"} if t["id"] == 470 else t for t in LEADERBOARD if not t["dev"]]
        self.assertIsNone(online.dev_benchmark(impostors))

    def test_the_rotation_never_picks_a_dev_team_and_still_pairs_the_previous_version(self):
        sid = 6111
        # The previous version played dev test 1 and team 262: dev teams are left to dev_requests, so 262 goes first.
        previous = [{**g, "requested_at": "1970-01-01T02:00:00Z"}  # the pool period of now = 10000
                    for g in (played(1, 5409, 545, 4525, 3, "B"), played(2, 5409, 262, 5000, 4, "A"))]
        pick = lambda rows, games=(), pending=set(): online.choose_match(
            LEADERBOARD, LADDER_MAPS, 157, 1865, pending, rows, sid, 10000, games, previous)
        self.assertEqual(pick([]), {"teamId": 262, "ranked": False, "mapIds": [4]})
        rows = [{"at": 9950, "games": 1, "opponent": 262, "submission_id": sid, "map_ids": [4]}]
        dev = {t["id"] for t in LEADERBOARD if t.get("dev")}
        for _ in range(12):
            chosen = pick(rows)
            self.assertNotIn(chosen["teamId"], dev)
            rows.append({"at": 9960 + len(rows), "games": 0, "opponent": chosen["teamId"], "submission_id": sid,
                         "map_ids": chosen["mapIds"]})

    def test_dev_games_stay_out_of_the_hourly_cap_and_every_other_game_counts(self):
        # The live match-requests.json's last four rows (7676's sweeps of teams 950, 857, 123 and 939, 10 games each)
        # and its two dev test 1 rows; a minute after the last one, 40 games of the 50 are spent.
        rows = [r for r in REQUESTS if r["opponent"] != 545]
        now = rows[-1]["at"] + 60
        self.assertEqual(online.remaining_games(rows, now), 10)
        dev = {"at": now - 30, "games": 10, "ranked": False, "opponent": 545, "submission_id": 7676,
               "status": "requested", "map_ids": LIVE_MAPS, "dev": True}
        self.assertEqual(online.remaining_games(rows + [dev], now), 10)  # a dev battle is ignored
        other = {**dev, "opponent": 262, "games": 6}
        del other["dev"]
        self.assertEqual(online.remaining_games(rows + [dev, other], now), 4)  # a non-dev battle counts
        # The rotation's budget is the same: 4 games left, so a non-dev sweep asks for 4 maps.
        pick = online.choose_match(LEADERBOARD, LADDER_MAPS, 157, 1865, set(), rows + [dev, other], 7676, now)
        self.assertEqual(len(pick["mapIds"]), 4)

    def test_one_pending_battle_per_dev_team(self):
        dev = online.dev_opponents(LEADERBOARD, 157)
        weights = {t["id"]: 1.0 for t in dev}
        pending = {446, 545}
        picks = [r["teamId"] for r in online.dev_requests(dev, weights, {}, [], pending, LIVE_MAPS)]
        self.assertEqual(picks[:3], [609, 704, 722])  # by id, all shares level
        self.assertEqual(len(picks), len(dev) - len(pending))
        # Every dev team with a battle pending: nothing more is asked of them.
        self.assertEqual(online.dev_requests(dev, weights, {}, [], {t["id"] for t in dev}, LIVE_MAPS), [])
        rows = [{"at": 9000, "games": 10, "opponent": 545, "status": "unknown"}]
        self.assertIn(545, online.pending_teams([], {}, rows, 9500))
        # A battle sent since the list was read (a poll's, while a cycle collected) is pending too.
        rows = [{"at": 9600, "games": 10, "opponent": 752, "status": "requested", "dev": True}]
        self.assertEqual(online.pending_teams([], {}, rows, 9500), {752})
        self.assertEqual(online.pending_teams([], {}, rows, 9700), set())
        battle = {"id": 5, "opponentId": 609}
        self.assertEqual(online.pending_teams([battle], {5: {"games": [{"id": 51, "status": "queued"}]}}, [], 9500),
                         {609})

    def test_a_409_means_still_pending_and_spends_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            rows = []
            pending = urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles", 409,
                                             "You already have a pending match against this team", {}, None)
            request = {"teamId": 545, "ranked": False, "mapIds": LIVE_MAPS}
            with patch.object(online.ladder, "post", side_effect=pending):
                self.assertEqual(online.send(state, rows, request, 7676, True, None), "pending")
            self.assertEqual(rows, [])
            self.assertEqual(json.loads((state / "match-requests.json").read_text()), [])
            with patch.object(online.ladder, "post", return_value={"ids": list(range(10))}):
                self.assertEqual(online.send(state, rows, request, 7676, True, None), "requested")
            self.assertEqual((rows[0]["dev"], rows[0]["games"], rows[0]["status"]), (True, 10, "requested"))

    def test_dev_battles_back_off_behind_a_queue_and_fill_the_free_slots_by_share(self):
        dev = online.dev_opponents(LEADERBOARD, 157)
        self.assertEqual(len(dev), 14)  # 16 flagged dev in the sample; mustramallow and brggr have no bot
        weights = {t["id"]: 0.1 for t in dev} | {545: 1.0, 752: 0.9, 722: 0.8}
        picks = online.dev_requests(dev, weights, {}, [], set(), LIVE_MAPS)
        self.assertEqual([p["teamId"] for p in picks][:3], [545, 752, 722])  # the most useful first
        self.assertEqual({tuple(p["mapIds"]) for p in picks}, {tuple(LIVE_MAPS)})  # every active map, however many
        # Games per unit of share, one battle's worth (10 games) added, decide who plays next: 752 at 11.1, 722 at
        # 25, 545 at 50, the low-share teams at 100 though they have none this version (a low share slows a team's
        # count, it never starves it).
        picks = online.dev_requests(dev, weights, {545: 40, 722: 10}, [], set(), LIVE_MAPS)
        self.assertEqual([p["teamId"] for p in picks][:3], [752, 722, 545])
        # Before: games per share alone, so every team with none tied at 0 and each new version played all 14 dev
        # teams once (140 games, ~2.8 h at DEV_GAMES_AN_HOUR) before any had a second battle. Now a share of 0.174
        # with none waits behind one of 0.938 with 40 (the live shares, 2026-09-27 23:10: 57.5 against 53.3).
        live = {t["id"]: 0.174 for t in dev} | {545: 0.938}
        self.assertEqual(online.dev_requests(dev, live, {545: 40}, [], set(), LIVE_MAPS)[0]["teamId"], 545)
        self.assertEqual(online.dev_requests(dev, live, {545: 50}, [], set(), LIVE_MAPS)[0]["teamId"], 446)
        # 120 games per unit of share for the low ones, 25 for 545, 27.8 for 752 and 31.25 for 722.
        counts = {t["id"]: 2 for t in dev} | {545: 15, 752: 15, 722: 15}
        self.assertEqual(online.dev_requests(dev, weights, counts, [], set(), LIVE_MAPS)[0]["teamId"], 545)
        # Five dev battles playing fill DEV_IN_FLIGHT, queue or no queue.
        playing = {545, 752, 722, 609, 753}
        self.assertEqual(online.dev_requests(dev, weights, {}, [], playing, LIVE_MAPS), [])
        self.assertTrue(online.dev_requests(dev, weights, {}, [], playing - {609}, LIVE_MAPS))

    def test_dev_battles_are_paced_to_fit_their_own_hourly_allowance(self):
        # sig 2026-09-27 ~22:57: dev battles have their own 60 an hour on the site; the loop had asked for 370 that hour.
        now, games = 10_000, len(LIVE_MAPS)
        pace = online.dev_pace(LIVE_MAPS)
        # One battle every `pace` seconds spends exactly the hour's dev allowance.
        self.assertAlmostEqual(3600 / pace * games, online.DEV_GAMES_AN_HOUR)
        self.assertEqual(online.dev_due([], LIVE_MAPS), 0)
        # Counted from our last dev request against any team, a refused one too; the other teams' pool doesn't count.
        rows = [{"at": now - 600, "games": games, "opponent": 545, "dev": True, "status": "rejected"},
                {"at": now - 60, "games": 40, "opponent": 71}]
        self.assertEqual(online.dev_due(rows, LIVE_MAPS), now - 600 + pace)
        self.assertEqual(online.remaining_dev_games(rows, now), online.DEV_GAMES_AN_HOUR - games)
        self.assertEqual(online.remaining_games(rows, now), online.GAMES_AN_HOUR - 40)
        # The hour's allowance spent long after the pace: due once enough of the oldest rows leave the rolling hour.
        rows = [{"at": now - 3000 + 100 * n, "games": games, "opponent": 545, "dev": True}
                for n in range(online.DEV_GAMES_AN_HOUR // games)]
        self.assertLess(online.remaining_dev_games(rows, now), games)  # no room for another battle
        self.assertEqual(online.dev_due(rows, LIVE_MAPS), now - 3000 + 3600)
        self.assertGreaterEqual(online.remaining_dev_games(rows, now - 3000 + 3600), games)  # room for one battle
        self.assertLess(online.remaining_dev_games(rows, now - 3000 + 3599), games)
        old = [dict(rows[0], at=now - 3601)]  # past the rolling hour
        self.assertEqual(online.remaining_dev_games(old, now), online.DEV_GAMES_AN_HOUR)
        # A row without `games` (hand-written, or from an older writer) counts none rather than crashing.
        self.assertEqual(online.remaining_dev_games([{"at": now, "dev": True}], now), online.DEV_GAMES_AN_HOUR)
        self.assertEqual(online.remaining_games([{"at": now}], now), online.GAMES_AN_HOUR)

    def test_a_lopsided_record_gives_a_small_share(self):
        self.assertEqual(online.dev_weight([]), 1.0)
        self.assertAlmostEqual(online.dev_weight([1.0] * 30), 0.121, places=3)
        self.assertAlmostEqual(online.dev_weight([0.0] * 30), 0.121, places=3)
        self.assertAlmostEqual(online.dev_weight([1.0] * online.DEV_WEIGHT_GAMES), 0.02, places=3)
        self.assertGreater(online.dev_weight([1.0, 0.0] * 10), 0.99)

    def test_benchmark_records_stay_within_one_dev_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            bench = online.dev_benchmark(LEADERBOARD)
            (state / "online-replays/7625").mkdir(parents=True)
            online.save(state / "online-replays/7625/index.json",
                        {"games": [g for g in DEV_GAMES if g["submission_id"] == 7625]})
            current = [g for g in DEV_GAMES if g["submission_id"] == 7676]
            report = online.benchmark_report(state, bench, 7676, current)
            self.assertEqual([(r["submission_id"], r["dev_submission_id"], r["games"], r["wins"], r["losses"],
                               r["score"]) for r in report["records"]],
                             [(7625, 4525, 10, 6, 4, 0.6), (7676, 4525, 19, 12, 7, 0.632)])
            self.assertEqual(report["records"][1]["ci"], list(online.rate_ci(12, 19)))
            # Map by map on dev submission 4525: 7676 better on maps 7 and 13, worse on 9, level on the other seven.
            compare = report["compare"]
            self.assertEqual((compare["previous"], compare["shared"]), (7625, [4525]))
            maps = compare["by_dev_submission"][0]
            self.assertEqual((maps["better"], maps["worse"], maps["same"], maps["n"], maps["p_better"],
                              maps["p_worse"]), (2, 1, 7, 3, 0.5, 0.875))
            self.assertEqual(maps["maps"][1], {"map_id": 7, "pool": "before 2026-09-27T11:00:00Z",
                                               "before": 0.0, "before_games": 1, "after": 0.5,
                                               "after_games": 2, "change": 0.5})
            # Dev test 1 changes bot mid-sweep: the new submission's games make their own record and are never
            # compared with 7625's, which played only 4525.
            changed = current + [{**g, "id": g["id"] + 10**7, "opponent_submission_id": 4600} for g in current[:3]]
            report = online.benchmark_report(state, bench, 7676, changed)
            self.assertEqual([(r["submission_id"], r["dev_submission_id"], r["games"]) for r in report["records"]],
                             [(7625, 4525, 10), (7676, 4525, 19), (7676, 4600, 3)])
            self.assertEqual(report["compare"]["shared"], [4525])
            only_new = [{**g, "opponent_submission_id": 4600} for g in current]
            report = online.benchmark_report(state, bench, 7676, only_new)
            self.assertEqual(report["compare"]["by_dev_submission"], [])
            # After the pool change of 2026-09-27 11:00 UTC a map id may carry other pearl timings: 7676's games from
            # then are compared with none of 7625's, which were all before it.
            moved = [{**g, "requested_at": "2026-09-27T11:20:00.000Z"} for g in current]
            maps = online.benchmark_report(state, bench, 7676, moved)["compare"]["by_dev_submission"][0]
            self.assertEqual((maps["maps"], maps["n"]), ([], 0))
            self.assertIn("no dev submission in common", report["compare"]["note"])
            news = online.benchmark_news(online.benchmark_report(state, bench, 7676, current), report)
            self.assertIn("new submission 4600 (before: [4525])", news[0])

    # REFLECT 2026-09-28 14:51 F4: dev test 1 moved from 4525 to 9371 with no alert. A watched team's version is its
    # latest game's, over every version of ours; a team first seen is recorded, not news.
    def test_a_watched_teams_new_submission_is_news_once(self):
        def game(n, team, sub, at):
            return {"id": n, "opponent": team, "opponent_submission_id": sub, "requested_at": at}
        played = {545: {7676: [game(1, 545, 4525, "2026-09-26T01:00:00Z")],
                        9553: [game(2, 545, 9371, "2026-09-28T01:00:00Z"), game(3, 545, 4525, "2026-09-27T01:00:00Z")]},
                  306: {9553: [game(4, 306, 8568, "2026-09-28T02:00:00Z")]}}
        versions = online.opponent_versions(played)
        self.assertEqual(versions, {"545": 9371, "306": 8568})
        teams = [{"id": 545, "name": "dev test 1 :P"}, {"id": 306, "name": "Vibing++"}]
        (line,) = online.version_news({"545": 4525}, versions, teams)
        self.assertIn("team 545 (dev test 1 :P) plays a new submission 9371 (before: 4525)", line)
        self.assertEqual(online.version_news(versions, versions, teams), [])

    # From 2026-09-27 11:00 UTC a map id may carry other pearl timings. Before, a version's record against a dev
    # submission pooled games from both sides of the cutoff. A dev team's share, which only orders who plays next,
    # reads both (dev_report).
    def test_dev_records_keep_the_pool_periods_apart_and_the_share_reads_both(self):
        current = [g for g in DEV_GAMES if g["submission_id"] == 7676]  # 19 games, 12-0-7, all on 2026-09-26
        moved = [{**g, "id": g["id"] + 10**7, "requested_at": "2026-09-27T11:30:00Z",
                  "summary": {**g["summary"], "result": {**g["summary"]["result"], "winner": None}}}
                 for g in current[:4]]
        team = {"id": 545, "name": "dev test 1 :P", "elo": 1939}
        (row,) = online.dev_report(Path("/nonexistent"), [team], 7676, [], {545: {7676: current + moved}})
        self.assertEqual([(r["dev_submission_id"], r["pool"], r["games"], r["score"]) for r in row["records"]],
                         [(4525, f"before {online.POOL_CUTOFF}", 19, 0.632),
                          (4525, f"from {online.POOL_CUTOFF}", 4, 0.5)])
        self.assertEqual((row["weight_games"], row["score"]), (23, 0.609))  # 12-4-7: both periods
        self.assertIn(f"7676 vs dev 4525 (from {online.POOL_CUTOFF}): 4 games, 0-4-0",
                      online.record_line(row["records"][1]))

    def test_online_md_reads_a_benchmark_written_before_the_dev_sweep(self):
        # online.json's live benchmark block of 2026-09-27 (records without `ci`, the old per-pair compare): reviews
        # read online.md before the first cycle of the new code rewrites it.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            old = json.loads((TESTDATA / "online-benchmark-before-dev-sweep.json").read_text())
            online.save(state / "online.json", {"submission_id": 7676, "games": [], "per_map": [], "benchmark": old})
            text = online.evidence_text(state, params="")
            low, high = online.rate_ci(12, 19)
            self.assertIn(f"7676 vs dev 4525: 19 games, 12-0-7, score 0.632 (95% {low:.3f}-{high:.3f})", text)

    def test_games_past_the_replay_sample_keep_their_api_result(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            def detail(path):
                gid = int(path.rsplit("/", 1)[1])
                match = game(7676)["match"] | {"id": gid, "teamBId": 545, "mapId": gid, "ranked": False,
                                                "winner": "b" if gid == 3 else "a"}
                return {"match": match} if gid != 4 else {"match": {**match, "winner": 157}}
            from subprocess import CompletedProcess
            summary = json.dumps({"checks": {"ok": True}, "result": {"winner": "A"}})
            battle = {"match": game(7676)["match"] | {"teamBId": 545},
                      "games": [{"id": n, "status": "completed"} for n in (1, 2, 3, 4)]}
            # Losses past the sample keep their replay too (test_every_dev_loss_keeps_its_replay_...); not here.
            with patch.object(online, "DEV_REPLAYS", 2), patch.object(online, "DEV_LOSS_REPLAYS", 0), \
                    patch.object(online.ladder, "get", side_effect=detail), \
                    patch.object(online.ladder, "download", return_value=b"replay") as download, \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, summary)] * 2):
                games = online.collect_games(state, state, 157, 7676, [battle], {545})
            self.assertEqual(download.call_count, 2)
            self.assertEqual([g["id"] for g in games], [1, 2])
            saved = json.loads((state / "online-replays/7676/index.json").read_text())
            self.assertEqual([(r["id"], r["winner"]) for r in saved["results"]], [(3, "B")])
            self.assertIn("unreadable winner 157", saved["invalid"]["4"]["reason"])
            self.assertEqual(online.winner_check(games, {545}), {"checked": 2, "agree": 2})
            self.assertEqual(online.game_score(saved["results"][0]), 0.0)
            # Results count in the records like saved games do.
            report = online.benchmark_report(state, {"id": 545, "name": "dev test 1 :P", "elo": 1939}, 7676, games)
            self.assertEqual((report["records"][0]["games"], report["records"][0]["wins"]), (3, 2))
            self.assertEqual(online.recorded_ids(state), {1, 2, 3})
            finished = {9: {"games": [{"id": n, "status": "completed"} for n in (1, 2, 3)]},
                        8: {"games": [{"id": 5, "status": "running"}]}}
            self.assertEqual(online.settled_battles([{"id": 9}, {"id": 8}, {"id": 7}], finished, {7},
                                                    online.recorded_ids(state)), [9, 7])

    def test_every_dev_game_is_recorded_from_its_battles_detail_once_replays_are_spent(self):
        # Before: a dev game still under its replay sample was skipped unread once the cycle's 10 replays were spent,
        # and dev games came back faster than 60 record reads a cycle could follow. The real detail of battle 335845
        # (10 maps against dev test 1) gives each game's result with no call; its first six games' own records agree.
        detail, maps = BATTLES["detail_335845"], {m["name"]: m["id"] for m in BATTLES["maps"]}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            from subprocess import CompletedProcess
            summary = json.dumps({"checks": {"ok": True}, "result": {"winner": "A"}})
            def get(path):
                gid = path.rsplit("/", 1)[1]
                return {"match": {**detail["match"], **BATTLES["records_335845"][gid], "id": int(gid),
                                  "status": "completed", "replayKey": "r"}}
            # Losses past the sample keep their replay too (test_every_dev_loss_keeps_its_replay_...); not here.
            with patch.object(online, "REPLAYS_PER_CYCLE", 3), patch.object(online, "DEV_LOSS_REPLAYS", 0), \
                    patch.object(online.ladder, "get", side_effect=get) as reads, \
                    patch.object(online.ladder, "download", return_value=b"replay"), \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, summary)] * 3):
                games = online.collect_games(state, state, 157, 7676, [detail], {545}, maps)
            self.assertEqual(len(games), 3)  # the replay budget, from its own records
            self.assertEqual(reads.call_count, 3)  # the other seven cost no call
            saved = json.loads((state / "online-replays/7676/index.json").read_text())
            results = {r["id"]: r for r in saved["results"]}
            self.assertEqual(sorted(results), [g["id"] for g in detail["games"][3:]])
            self.assertTrue(all(r["dev"] and r["from"] == "battle" for r in results.values()))
            self.assertTrue(all(g["dev"] for g in games))
            for gid, record in BATTLES["records_335845"].items():
                if int(gid) in results:
                    row = results[int(gid)]
                    self.assertEqual((row["map_id"], row["winner"], row["side"], row["submission_id"],
                                      row["opponent"], row["opponent_submission_id"]),
                                     (record["mapId"], record["winner"].upper(), "A", 7676, 545, 4525))
            self.assertEqual(online.recorded_ids(state), {g["id"] for g in detail["games"]})
            self.assertEqual(online.settled_battles([{"id": 335845}], {335845: detail}, set(),
                                                    online.recorded_ids(state)), [335845])
            # A map the pool dropped has no id by name: that game's own record is read instead.
            with tempfile.TemporaryDirectory() as other:
                with patch.object(online, "DEV_REPLAYS", 0), patch.object(online, "DEV_LOSS_REPLAYS", 0), \
                        patch.object(online.ladder, "get", side_effect=get) as reads, \
                        patch.object(online.time, "sleep"):
                    online.collect_games(Path(other), Path(other), 157, 7676, [detail], {545},
                                         {n: i for n, i in maps.items() if n != "Default"})
                self.assertEqual([c.args[0] for c in reads.call_args_list], ["/battles/335845"])
                kept = json.loads((Path(other) / "online-replays/7676/index.json").read_text())["results"]
                self.assertEqual(len(kept), 10)
            # From 2026-09-27 the pool may hold variants of a map (same layout, other pearl timings) under one name:
            # a name two maps share has no id by name, so the Default game's own record gives its map id (4), not
            # the variant's; before, {name: id} kept whichever came last.
            variant = BATTLES["maps"] + [{"id": 99, "name": "Default", "active": True}]
            by_name = online.map_ids_by_name(variant)
            self.assertNotIn("Default", by_name)
            self.assertEqual(by_name, {n: i for n, i in maps.items() if n != "Default"})
            with tempfile.TemporaryDirectory() as other:
                with patch.object(online, "DEV_REPLAYS", 0), patch.object(online, "DEV_LOSS_REPLAYS", 0), \
                        patch.object(online.ladder, "get", side_effect=get) as reads, \
                        patch.object(online.time, "sleep"):
                    online.collect_games(Path(other), Path(other), 157, 7676, [detail], {545}, by_name)
                self.assertEqual([c.args[0] for c in reads.call_args_list], ["/battles/335845"])
                kept = json.loads((Path(other) / "online-replays/7676/index.json").read_text())["results"]
                self.assertEqual(next(r["map_id"] for r in kept if r["id"] == 335845), 4)

    def test_a_429_stops_the_reads_and_is_no_failed_attempt(self):
        # Before: each game read in the burst after a 429 was marked invalid for an hour, by which time its battle
        # had left the 100-battle window, so it was never read again.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            limited = urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles/2", 429, "Too Many Requests",
                                             {"Retry-After": "12"}, None)
            def get(path):
                gid = int(path.rsplit("/", 1)[1])
                if gid == 2:
                    raise limited
                return {"match": {**game()["match"], "id": gid}}
            from subprocess import CompletedProcess
            ok = json.dumps({"checks": {"ok": True}})
            battle = {"games": [{"id": n, "status": "completed"} for n in (1, 2, 3)]}
            with patch.object(online.ladder, "get", side_effect=get) as reads, \
                    patch.object(online.ladder, "download", return_value=b"replay"), \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, ok)]):
                games = online.collect_games(state, state, 157, 3182, [battle])
            self.assertEqual([g["id"] for g in games], [1])
            self.assertEqual(reads.call_count, 2)  # game 3 waits for the next call
            self.assertEqual(json.loads((state / "online-replays/3182/index.json").read_text())["invalid"], {})
            # While its Retry-After runs, nothing is read at all.
            with patch.object(online.ladder, "quiet_until", return_value=online.time.time() + 30), \
                    patch.object(online.ladder, "get", side_effect=AssertionError("quiet: no calls")):
                self.assertEqual([g["id"] for g in online.collect_games(state, state, 157, 3182, [battle])], [1])

    def test_ladder_waits_out_a_429s_retry_after_in_every_process(self):
        # Before: the wait lived in one process, so the cycle, the dev poll and publishing each met their own 429.
        limited = urllib.error.HTTPError("https://game.battlecode.au/api/v1/team", 429, "Too Many Requests",
                                         {"Retry-After": "12"}, None)
        class Opener:
            def open(self, request, timeout):
                raise limited
        with tempfile.TemporaryDirectory() as directory:
            quiet_file = Path(directory) / "api-quiet-until"
            with patch.object(online.ladder, "QUIET_FILE", quiet_file), \
                    patch.object(online.ladder, "api_key", return_value="k"), \
                    patch.object(online.ladder.urllib.request, "build_opener", return_value=Opener()), \
                    patch.object(online.ladder.time, "sleep") as slept:
                self.assertFalse(online.ladder.quiet())
                with self.assertRaises(urllib.error.HTTPError):
                    online.ladder.get("/team")
                self.assertTrue(online.ladder.quiet())
                self.assertAlmostEqual(online.ladder.quiet_until() - online.time.time(), 12, delta=1)
                self.assertEqual(len(quiet_file.read_text().splitlines()), 1)
                with self.assertRaises(urllib.error.HTTPError):
                    online.ladder.get("/team")  # the next call waits it out first
                self.assertAlmostEqual(slept.call_args.args[0], 12, delta=1)
                online.ladder.hold_quiet(online.time.time() + 1)  # a shorter wait never cuts a longer one
                self.assertAlmostEqual(online.ladder.quiet_until() - online.time.time(), 12, delta=1)
                # An end further off than one capped wait (the clock stepped back) is ignored, not waited out.
                quiet_file.write_text(str(online.time.time() + 10 * online.ladder.QUIET_CAP))
                self.assertEqual(online.ladder.quiet_until(), 0.0)
                online.ladder.hold_quiet(online.time.time() + 12)
            # Another process on the same loop home sees it.
            other = subprocess.run([sys.executable, "-c", "import ladder; print(ladder.quiet())"], text=True,
                                   capture_output=True, cwd=Path(__file__).parent,
                                   env={**os.environ, "BC_LOOP_HOME": directory}, check=True)
            self.assertEqual(other.stdout.strip(), "True")
        self.assertEqual(online.ladder.retry_after(urllib.error.HTTPError("u", 429, "x", {}, None)), 60.0)

    def test_the_queue_brake_reads_wait_on_the_real_battle_list(self):
        # The live GET /battles rows carry `wait` ({"ahead", "ms"} while a game waits; finished rows have none).
        queued = [b for b in BATTLES["battles"] if b.get("wait")]
        self.assertEqual([b["outcome"] for b in queued], ["live"] * 3)
        self.assertEqual(online.queue_ahead(queued), max(b["wait"]["ahead"] for b in queued))
        self.assertTrue(all("wait" not in b for b in BATTLES["battles"] if b["outcome"] != "live"))
        dev = online.dev_opponents(LEADERBOARD, 157)
        weights = {t["id"]: 1.0 for t in dev}
        self.assertEqual(online.dev_requests(dev, weights, {}, queued, set(), LIVE_MAPS), [])  # 724 ahead
        calm = [b for b in queued if b["wait"]["ahead"] <= online.MAX_AHEAD]  # 339696, ahead 0: not waiting
        self.assertTrue(online.dev_requests(dev, weights, {}, calm, set(), LIVE_MAPS))
        behind = [{**calm[0], "id": n, "wait": {"ahead": 1, "ms": 30000}} for n in range(online.MAX_AHEAD + 1)]
        self.assertTrue(online.dev_requests(dev, weights, {}, behind[:-1], set(), LIVE_MAPS))
        self.assertEqual(online.dev_requests(dev, weights, {}, behind, set(), LIVE_MAPS), [])

    def test_live_dev_battles_whose_games_have_started_leave_room_for_dev_in_flight(self):
        # Before: every live dev battle keeps `wait` until its last game starts (339696: 9 of 10 done, ahead 0), so
        # three in flight filled MAX_AHEAD + 1 and the brake held dev battles to 3, not DEV_IN_FLIGHT.
        live = next(b for b in BATTLES["battles"] if b["id"] == 339696)
        self.assertEqual((live["wait"]["ahead"], live["results"].count("pending")), (0, 1))
        dev = online.dev_opponents(LEADERBOARD, 157)
        weights = {t["id"]: 1.0 for t in dev}
        playing = [t["id"] for t in dev[:3]]
        queued = [{**live, "opponentId": team} for team in playing]
        self.assertFalse(any(map(online.waiting, queued)))
        picks = online.dev_requests(dev, weights, {}, queued, set(playing), LIVE_MAPS)
        self.assertEqual(len(picks), len(dev) - 3)
        self.assertTrue(set(playing).isdisjoint(p["teamId"] for p in picks))

    def test_dev_pairs_cannot_hide_a_ladder_regression(self):
        # 18 ladder pairs, 6 worse and 1 better (p 0.06, alerts), and 60 dev pairs, 30 better and 30 worse: pooled,
        # 36 worse and 31 better is luck 31% of the time and the alert was gone.
        before, after = [], []
        for n in range(18):
            before.append(played(n, 7003, 262, 5000, n, "A"))
            winner = "B" if n < 6 else "A"
            after.append({**played(1000 + n, 7359, 262, 5000, n, winner)})
        before[6] = played(6, 7003, 262, 5000, 6, "B")
        for n in range(60):
            before.append(played(100 + n, 7003, 545, 4525, 100 + n, "B" if n < 30 else "A"))
            after.append(played(2000 + n, 7359, 545, 4525, 100 + n, "A" if n < 30 else "B"))
        dev = {545}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for compare, alerts in ((online.compare_versions(7003, before, after), False),
                                    (online.ladder_compare(7003, before, after, dev), True)):
                online.save(state / "online.json", {"submission_id": 7359, "compare": compare})
                self.assertEqual(online.version_alert(state) is not None, alerts, compare)
            self.assertEqual(online.ladder_compare(7003, before, after, dev)["pairs"], 18)
            self.assertIsNone(online.ladder_compare(None, before, after, dev))

    def test_online_md_keeps_dev_games_out_of_recent_games_and_round500(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            ladder_game = {**played(1, 7676, 262, 5000, 4, "A"), "summary": {"result": {"winner": "A"}}}
            # One marked dev when collected (its team since unflagged), two against the benchmark collected unmarked.
            dev_games = [{**played(2, 7676, 999, 1, 4, "B"), "dev": True}] + [
                played(3 + n, 7676, 545, 4525, 4, "B") for n in range(2)]
            online.save(state / "online.json", {"submission_id": 7676, "games": [ladder_game] + dev_games,
                                                "per_map": [], "benchmark": {"team": {"id": 545}}})
            text = online.evidence_text(state, params="")
            recent = text.split("recent_games:\n", 1)[1].split("\nround500", 1)[0]
            self.assertEqual([json.loads(line)["id"] for line in recent.splitlines() if line.startswith("{")], [1])
            self.assertEqual(online.dev_ids({"dev": {"teams": [{"id": 752}]}, "benchmark": {"team": {"id": 545}}}),
                             {545, 752})

    def test_each_dev_battle_is_timed_from_request_to_results(self):
        rows = [{**r, "dev": True} for r in REQUESTS if r["opponent"] == 545]
        done = {"games": [{"id": i, "status": "completed"} for i in rows[1]["ids"]]}
        half = {"games": [{"id": rows[0]["ids"][0], "status": "running"}]}
        finished_at = rows[1]["at"] + 113
        self.assertTrue(online.mark_done(rows, [done, half], finished_at))
        self.assertNotIn("done_at", rows[0])
        self.assertFalse(online.mark_done(rows, [done], finished_at + 30))  # already marked
        timing = online.dev_timing(rows, finished_at + 60)
        self.assertEqual(timing["minutes"], {"n": 1, "median": 1.9, "slowest": 1.9})
        self.assertEqual((timing["done_last_hour"], timing["games_last_hour"]), (1, 10))

    def test_a_cycle_sends_a_paced_dev_battle_and_one_rotation_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            report, sent = run_cycle(state, LEADERBOARD)
            self.assertIsNone(report["error"])
            posts = [c.args[1] for c in sent.call_args_list]
            dev = {t["id"] for t in LEADERBOARD if t.get("dev")}
            self.assertEqual(len([p for p in posts if p["teamId"] in dev]), 1)
            self.assertEqual(len([p for p in posts if p["teamId"] not in dev]), 1)
            self.assertTrue(all(p["mapIds"] == [3, 4, 5, 7, 9, 11, 13, 14, 15] for p in posts))
            rows = json.loads((state / "match-requests.json").read_text())
            self.assertEqual(online.remaining_games(rows, 10001), 50 - 9)  # only the rotation's 9 games count
            self.assertEqual(len(report["dev"]["teams"]), 14)
            self.assertEqual(report["benchmark"]["records"][0]["dev_submission_id"], 4525)
            self.assertIn("dev benchmark is team 545 (dev test 1 :P", (state / "host-alerts.log").read_text())
            text = online.evidence_text(state)
            self.assertIn("6111 vs dev 4525 (unknown): 1 games, 0-0-1, score 0.000 (95% 0.000-0.793)", text)
            self.assertIn("team 752 (dev test 2, Elo 1871), share 1.0", text)
            pace = online.dev_pace(report["dev"]["maps"])
            self.assertEqual((report["dev"]["allowance"], report["dev"]["due"]),
                             (online.DEV_GAMES_AN_HOUR - 9, 10000.0 + pace))
            # Before: the hour's dev battles went out in one burst (370 games in the hour before sig's message). The
            # next cycle, two minutes on, sends none and no rotation sweep comes within ten minutes of the last.
            battles = [{"id": 100 + n, "opponentId": p["teamId"]} for n, p in enumerate(posts)]
            details = {b["id"]: {"games": [{"id": b["id"] * 10, "status": "running"}]} for b in battles}
            _, sent = run_cycle(state, LEADERBOARD, now=10000.0 + online.DEV_EVERY, battles=battles,
                                details=details)
            self.assertEqual(sent.call_args_list, [])
            # A pace after the first, it still plays: the next dev battle goes to another team.
            _, sent = run_cycle(state, LEADERBOARD, now=10000.0 + pace, battles=battles, details=details)
            again = [c.args[1]["teamId"] for c in sent.call_args_list if c.args[1]["teamId"] in dev]
            self.assertEqual(len(again), 1)
            self.assertNotIn(again[0], {p["teamId"] for p in posts})

    def test_a_cycle_sends_no_dev_battle_while_the_hours_allowance_is_spent(self):
        # Before: nothing tested that dev_round kept to the allowance; dropping it left every test passing.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            online.save(state / "match-requests.json", [
                {"at": 10000.0 - 1000, "games": online.DEV_GAMES_AN_HOUR, "ranked": False, "opponent": 545,
                 "submission_id": 6111, "status": "requested", "dev": True, "ids": []}])
            report, sent = run_cycle(state, LEADERBOARD)
            dev = {t["id"] for t in LEADERBOARD if t.get("dev")}
            posts = [c.args[1]["teamId"] for c in sent.call_args_list]
            self.assertEqual([t for t in posts if t in dev], [])
            self.assertEqual(len(posts), 1)  # the other teams' pool is its own: the rotation sweep still goes
            self.assertEqual((report["dev"]["allowance"], report["dev"]["due"]), (0, 10000.0 - 1000 + 3600))
            text = online.evidence_text(state)
            self.assertIn(f"dev allowance: 0 of {online.DEV_GAMES_AN_HOUR} games left this rolling hour", text)

    def test_a_409_tries_the_next_dev_team_and_a_refused_battle_is_the_rounds_one(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            def post(path, request):
                code = {446: 409, 609: 400}.get(request["teamId"])
                if code:
                    raise urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles", code, "no", {}, None)
                return {"ids": [1]}
            report, sent = run_cycle(state, LEADERBOARD, {"side_effect": post})
            dev = {t["id"] for t in LEADERBOARD if t.get("dev")}
            self.assertEqual([c.args[1]["teamId"] for c in sent.call_args_list if c.args[1]["teamId"] in dev],
                             [446, 609])  # 609's refusal spent its games (remaining_dev_games): the rest wait a pace
            self.assertIsNone(report["error"])
            self.assertIn("HTTPError", report["dev"]["error"])
            rows = json.loads((state / "match-requests.json").read_text())
            status = {r["opponent"]: r["status"] for r in rows}
            self.assertNotIn(446, status)
            self.assertEqual(status[609], "rejected")
            self.assertEqual((report["dev"]["pending"], report["dev"]["tried"]), ([446], [446, 609]))

    def test_a_429_on_a_request_holds_neither_the_team_nor_the_budget_and_stops_the_round(self):
        # Before: a 429 was "rejected", holding its team for an hour (and a non-dev one's games against the cap).
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            def post(path, request):
                if request["teamId"] == 446:
                    raise urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles", 429, "Too Many Requests",
                                                 {"Retry-After": "20"}, None)
                return {"ids": [1]}
            report, sent = run_cycle(state, LEADERBOARD, {"side_effect": post})
            dev = {t["id"] for t in LEADERBOARD if t.get("dev")}
            dev_posts = [c.args[1]["teamId"] for c in sent.call_args_list if c.args[1]["teamId"] in dev]
            self.assertEqual(dev_posts, [446])  # the rest wait for the next round
            rows = json.loads((state / "match-requests.json").read_text())
            self.assertNotIn(446, {r["opponent"] for r in rows})
            self.assertNotIn(446, online.pending_teams([], {}, rows, 0))
            self.assertIn("429", report["dev"]["error"])
            self.assertEqual(report["dev"]["tried"], [])
            self.assertEqual(report["dev"]["due"], 0)  # nothing reserved, so no pace to wait
            # The rotation's own 429: nothing reserved, and it may ask again next cycle.
            rows = []
            limited = urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles", 429, "Too Many Requests", {},
                                             None)
            with patch.object(online.ladder, "post", side_effect=limited):
                self.assertEqual(online.send(state, rows, {"teamId": 262, "mapIds": LIVE_MAPS}, 7676, False, None),
                                 "limited")
            self.assertEqual((rows, online.remaining_games(rows, 10000)), ([], online.GAMES_AN_HOUR))

    def test_a_cycle_without_the_benchmark_keeps_its_record_and_logs_no_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            run_cycle(state, LEADERBOARD)
            without = [t for t in LEADERBOARD if not t["dev"]]
            report, _ = run_cycle(state, without, now=10700.0)
            self.assertEqual(report["benchmark"]["records"][0]["dev_submission_id"], 4525)
            self.assertEqual(report["dev"]["teams"], [])
            run_cycle(state, LEADERBOARD, now=11400.0)
            log = (state / "host-alerts.log").read_text()
            self.assertEqual(log.count("the dev benchmark is team 545"), 1)

    def test_the_poll_follows_a_finished_dev_battle_between_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            shares = {752: 1.0, 545: 0.9}  # 752 first, then 545, then the rest
            dev = [{"id": t["id"], "name": t["name"], "elo": t["elo"], "weight": shares.get(t["id"], 0.5)}
                   for t in online.dev_opponents(LEADERBOARD, 157)]
            online.save(state / "online.json", {"submission_id": 7676, "dev": {
                "submission_id": 7676, "at": online.time.time(), "since": 9000, "teams": dev, "maps": LIVE_MAPS}})
            battles = [{"id": 1, "opponentId": 545}, {"id": 2, "opponentId": 752, "wait": {"ahead": 0}},
                       {"id": 3, "opponentId": 262}]
            details = {1: {"games": [{"id": 11, "status": "completed"}]},
                       2: {"games": [{"id": 21, "status": "queued"}]}}
            def get(path):
                if path == f"/battles?limit={online.POLL_WINDOW}":
                    return battles
                return details[int(path.rsplit("/", 1)[1])]  # never team 262's: not a dev battle
            with patch.object(online.ladder, "get", side_effect=get), \
                    patch.object(online.ladder, "post", return_value={"ids": [5]}) as sent, \
                    patch.object(online.time, "sleep"):
                record = online.dev_poll(state)
                again = online.dev_poll(state)  # within DEV_POLL_EVERY: nothing
            teams = [c.args[1]["teamId"] for c in sent.call_args_list]
            # 752's battle plays (a `wait` at ahead 0 isn't queued behind others'), so one battle goes to 545, whose
            # battle finished.
            self.assertEqual(teams, [545])
            self.assertIsNone(again)
            self.assertEqual((record["queued"], record["ahead"], record["finished"]), (0, 0, [1]))
            self.assertEqual(record["allowance"], online.DEV_GAMES_AN_HOUR - len(LIVE_MAPS))
            # Before: a poll that could send nothing still read the battle list and details every DEV_POLL_EVERY, and
            # said nothing. Now it skips its reads until the next battle is due, and says when that is.
            due = online.time.strftime("%H:%M:%S", online.time.localtime(record["due"]))
            online.save(state / "dev-poll.json", {})
            with patch.object(online.ladder, "get", side_effect=AssertionError("paced: no calls")):
                self.assertIsNone(online.dev_poll(state))
            self.assertEqual(online.read(state / "dev-poll.json", {})["skipped"],
                             f"paced: one dev battle every {online.dev_pace(LIVE_MAPS):.0f} s (dev_pace); the next is due at {due}")
            rows = online.read(state / "match-requests.json", [])
            rows[0].update(at=rows[0]["at"] - 1000, games=online.DEV_GAMES_AN_HOUR - 5)  # the pace passed, not the hour
            online.save(state / "match-requests.json", rows)
            online.save(state / "dev-poll.json", {})
            with patch.object(online.ladder, "get", side_effect=AssertionError("spent: no calls")):
                self.assertIsNone(online.dev_poll(state))
            skipped = online.read(state / "dev-poll.json", {})["skipped"]
            self.assertTrue(skipped.startswith(f"dev allowance spent: 5 of DEV_GAMES_AN_HOUR "
                                               f"({online.DEV_GAMES_AN_HOUR}) dev games left this rolling hour, a "
                                               "battle needs 10; the next is due at "), skipped)
            # Publishing blocked, waiting to activate or in error (the tick's gate for the cycle too): no calls.
            for published in ({"blocked": True}, {"pending": {"name": "v0040"}}, {"error": "build failed"}):
                online.save(state / "publishing.json", published)
                online.save(state / "dev-poll.json", {})
                with patch.object(online.ladder, "get", side_effect=AssertionError("blocked: no calls")):
                    self.assertIsNone(online.dev_poll(state))

    def test_the_poll_sends_nothing_on_a_stale_dev_list_or_under_a_429(self):
        # Before: it played whatever dev teams and maps online.json last held, however old (a cleared dev flag's
        # games then count against the site's hourly 60 while we don't count them; a retired map refuses them all).
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            dev = [{"id": 545, "name": "dev test 1 :P", "elo": 1939, "weight": 1.0}]
            now = online.time.time()
            for at in (now - online.DEV_FRESH - 5, None):
                online.save(state / "online.json", {"submission_id": 7676, "dev": {
                    "submission_id": 7676, "since": 9000, "teams": dev, "maps": LIVE_MAPS,
                    **({"at": at} if at else {})}})
                online.save(state / "dev-poll.json", {})
                with patch.object(online.ladder, "get", side_effect=AssertionError("stale: no calls")):
                    self.assertIsNone(online.dev_poll(state))
                # Before: it went quiet with no record of why (health's stall alert now quotes this).
                skipped = online.read(state / "dev-poll.json", {})["skipped"]
                self.assertTrue(skipped.startswith("stale dev list"), skipped)
            online.save(state / "online.json", {"submission_id": 7676, "dev": {
                "submission_id": 7676, "at": now, "since": 9000, "teams": dev, "maps": LIVE_MAPS}})
            online.save(state / "dev-poll.json", {})
            with patch.object(online.ladder, "quiet_until", return_value=now + 30), \
                    patch.object(online.ladder, "get", side_effect=AssertionError("quiet: no calls")):
                self.assertIsNone(online.dev_poll(state))
            self.assertTrue(online.read(state / "dev-poll.json", {})["skipped"].startswith("429 wait"))
            online.save(state / "dev-poll.json", {})
            online.save(state / "publishing.json", {"blocked": "source mismatch"})
            with patch.object(online.ladder, "get", side_effect=AssertionError("blocked: no calls")):
                self.assertIsNone(online.dev_poll(state))
            self.assertEqual(online.read(state / "dev-poll.json", {})["skipped"],
                             "matches not allowed: publishing.json is blocked")
            (state / "publishing.json").unlink()
            # Within DEV_POLL_EVERY of a look, online.json isn't even parsed.
            online.save(state / "dev-poll.json", {"at": now})
            with patch.object(online, "read", wraps=online.read) as reads:
                self.assertIsNone(online.dev_poll(state))
            self.assertEqual([c.args[0].name for c in reads.call_args_list], ["dev-poll.json"])

    def test_a_team_answering_409_for_half_an_hour_is_stuck(self):
        rounds = [(0, {"tried": [545, 752], "pending": [545]}), (600, {"tried": [545], "pending": [545]}),
                  (1900, {"tried": [545, 722], "pending": [545, 722]})]
        stuck: dict = {}
        for at, round_ in rounds:
            stuck = online.stuck_teams(stuck, round_, at)
        self.assertEqual(stuck, {"545": {"since": 0, "last": 1900}, "722": {"since": 1900, "last": 1900}})
        # Its next try goes through: it leaves; a team not tried this round keeps its times.
        self.assertEqual(online.stuck_teams(stuck, {"tried": [545], "pending": []}, 2000),
                         {"722": {"since": 1900, "last": 1900}})


# Captured 2026-09-27 from <loop home> (its `note` says what): version 8023's games against five dev teams, from the
# benchmark (0.54 here) to dev test 5 (120 wins of 120).
CAPS = json.loads((Path(__file__).parent / "testdata/online-dev-caps-sample.json").read_text())


def caps_state(state: Path, games: list[dict] = CAPS["games"]) -> None:
    """Files the sample's games in state's online-replays/8023 index as collect_games would: those with a replay under
    `games`, the rest under `results`."""
    rows = [json.loads(json.dumps(g).replace("{home}", str(state))) for g in games]
    (state / "online-replays/8023").mkdir(parents=True, exist_ok=True)
    online.save(state / "online-replays/8023/index.json", {"games": [g for g in rows if "replay" in g],
                                                           "results": [g for g in rows if "replay" not in g]})


# Captured 2026-09-27 23:30 from <loop home> (its `note` says what): each dev team's latest 300 games against its
# current dev submission, from dev test 1 (0.54) to seven teams we beat in every game, and its last dev request.
SHARES = json.loads((Path(__file__).parent / "testdata/online-dev-shares-sample.json").read_text())
POINTS = {"W": 1.0, "D": 0.5, "L": 0.0}


def simulate(hours: float, outcomes: dict[int, str] | None = None, ago: dict[int, float] | None = None) -> tuple:
    """Plays `hours` of dev rounds through dev_round, one each dev_pace, as the live loop would on SHARES: each team's
    share from its latest DEV_WEIGHT_GAMES games (dev_weight), each battle's ten games scored by cycling through the
    team's `outcomes` (its own record unless given), starting an hour after the capture (so with the hour's allowance
    free), each team last requested `ago` seconds before that capture (the sample's unless given). Returns (battles per
    team, final share per team, [(round, team)] in order)."""
    record = {t["id"]: [POINTS[c] for c in t["scores"]] for t in SHARES["teams"]}
    cycle = {t["id"]: (outcomes or {}).get(t["id"], t["scores"]) for t in SHARES["teams"]}
    ago = {t["id"]: t["last_request_ago"] for t in SHARES["teams"]} | (ago or {})
    start, pace, sid = 1_800_000_000.0, online.dev_pace(LIVE_MAPS), 8636
    rows = [{"at": start - 3600 - ago[t], "games": 10, "opponent": t, "submission_id": 8473, "status": "requested",
             "dev": True, "ids": []} for t in record]
    sent, played = Counter(), []
    with tempfile.TemporaryDirectory() as directory, \
            patch.object(online.ladder, "post", return_value={"ids": list(range(10))}):
        state = Path(directory)
        for n in range(int(hours * 3600 / pace)):
            now = start + n * pace
            block = {"maps": LIVE_MAPS, "teams": [{"id": t, "name": str(t), "weight": online.dev_weight(
                record[t][-online.DEV_WEIGHT_GAMES:])} for t in record]}
            with patch.object(online.time, "time", return_value=now):
                round_ = online.dev_round(state, state / "STOP", sid, block, [], {}, rows, set(), None, now)
            (team,) = round_["sent"]
            sent[team] += 1
            played.append((n, team))
            done = sum(t == team for _, t in played) - 1
            record[team] += [POINTS[cycle[team][(done * 10 + k) % len(cycle[team])]] for k in range(10)]
    return sent, {t: online.dev_weight(record[t][-online.DEV_WEIGHT_GAMES:]) for t in record}, played


class TestDevShares(unittest.TestCase):
    def test_the_informative_teams_get_most_battles_and_a_team_we_always_beat_only_its_probe(self):
        # Before: shares from our latest 30 games in the pool period gave the ten teams we beat 0.9-1.00 0.12-0.44 each,
        # 39% of the shares (online.json, 2026-09-27 23:27; asked for: "spend the budget almost entirely on the four
        # dev teams that still beat us sometimes", sig's "ok go"), and only a team at its game cap had a probe.
        sent, _, _ = simulate(6)
        informative = sum(sent[t] for t in (545, 446, 722, 752))
        self.assertGreaterEqual(informative / sum(sent.values()), 0.8, sent)
        perfect = [t["id"] for t in SHARES["teams"] if set(t["scores"][-online.DEV_WEIGHT_GAMES:]) == {"W"}]
        self.assertEqual(len(perfect), 7)  # dev tests 4-7, 9 and 10 and sl: 200 wins of their latest 200
        self.assertEqual({t: sent[t] for t in perfect}, {t: 0 for t in perfect})
        # One of them falls due its probe (DEV_PROBE_S unplayed) at the start: it goes ahead of the shares at the
        # first round, and it plays no more in the six hours.
        sent, _, played = simulate(6, ago={755: online.DEV_PROBE_S - 3600})
        self.assertEqual((sent[755], played[0]), (1, (0, 755)))

    def test_a_team_whose_score_drops_gains_share(self):
        # dev test 8, beaten 0.94 over its latest 200, falls to 0.7 (7 wins in 10): more of its games, a bigger share.
        before, shares, _ = simulate(12)
        after, dropped, _ = simulate(12, outcomes={758: "WWWWWWWLLL"})
        self.assertGreater(dropped[758], 1.3 * shares[758])
        self.assertGreater(after[758], before[758])

    def test_every_dev_team_unplayed_for_a_day_goes_first_longest_unplayed_first(self):
        # Before: only a team at its game cap had a probe (every 6 h); an uncapped team with a small share went on
        # waiting behind the shares, however long it had gone unplayed.
        now = online.time.time()
        pace = online.dev_pace(LIVE_MAPS)
        teams = [{"id": 545, "name": "dev test 1 :P", "weight": 0.99}, {"id": 755, "name": "dev test 5",
                                                                        "weight": 0.02},
                 {"id": 754, "name": "dev test 4", "weight": 0.02}, {"id": 752, "name": "dev test 2", "weight": 0.6}]
        last = {545: now - 3600, 755: now - online.DEV_PROBE_S - 60, 754: now - online.DEV_PROBE_S - 3600,
                752: now - 3600 - pace}  # the hour's allowance free
        rows = [{"at": at, "games": 10, "opponent": team, "submission_id": 8473, "status": "requested", "dev": True,
                 "ids": []} for team, at in last.items()]
        rows.append({"at": now - pace + 1, "games": 10, "opponent": 755, "submission_id": 8636, "status": "rejected",
                     "dev": True})  # refused: plays nothing, so 755 is still due, but it holds the pace
        block = {"teams": teams, "maps": LIVE_MAPS}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(online.ladder, "post", return_value={"ids": list(range(10))}):
            state = Path(directory)
            def at(when):
                with patch.object(online.time, "time", return_value=when):
                    return online.dev_round(state, state / "STOP", 8636, block, [], {}, rows, set(), None, when)
            self.assertEqual((at(now)["probes"], at(now)["sent"]), ([754, 755], []))  # a probe waits for dev_due
            self.assertEqual(at(now + 2)["sent"], [754])
            self.assertEqual(rows[-1]["probe"], "day")
            round_ = at(now + pace + 2)  # never two probes in a row: a share pick goes between them
            self.assertEqual((round_["probes"], round_["held"], round_["sent"]), ([755], True, [545]))
            self.assertNotIn("probe", rows[-1])
            round_ = at(now + 2 * pace + 2)
            self.assertEqual((round_["probes"], round_["held"], round_["sent"]), ([755], False, [755]))
            round_ = at(now + 3 * pace + 2)
            self.assertEqual((round_["probes"], round_["sent"]), ([], [752]))  # then the shares

    def test_probes_due_together_alternate_with_share_picks(self):
        # Before: probes went first every round, and the six teams we always beat were last requested between 21:01
        # and 21:18 on 2026-09-27, so from about 21:01 each day they took six rounds running (about 72 minutes) and
        # the teams that inform got no dev battle meanwhile.
        due = [704, 755, 756, 757, 759, 760]
        _, _, played = simulate(3, ago={t: online.DEV_PROBE_S - 3600 for t in due})
        order = [team for _, team in played]
        self.assertEqual(order[0:2 * len(due):2], due)  # each within two rounds a probe, longest unplayed first
        self.assertFalse(set(order[1:2 * len(due):2]) & set(due))  # a share pick between each two
        self.assertFalse(set(order[2 * len(due):]) & set(due))  # and none again until the next day

    def test_a_loss_to_a_team_we_beat_heavily_gets_it_one_extra_probe_a_version(self):
        # Before: one lost game moves the pooled share of a team we always beat very little (one in DEV_WEIGHT_GAMES),
        # so a regression against it waited for its next daily probe.
        now, pace, sid = online.time.time(), online.dev_pace(LIVE_MAPS), 8636
        lost = lambda version, losses: [{"submission_id": version, "games": 10, "losses": losses}]
        heavy = online.DEV_HEAVY + 0.05
        teams = [{"id": 755, "weight": 0.02, "lower": heavy, "records": lost(sid, 1)},  # lost this version
                 {"id": 754, "weight": 0.02, "lower": heavy, "records": lost(sid, 0)},  # won them all
                 {"id": 704, "weight": 0.02, "lower": heavy, "records": lost(8473, 2) + lost(sid, 0)},  # lost before
                 {"id": 758, "weight": 0.3, "lower": online.DEV_HEAVY - 0.05, "records": lost(sid, 2)},  # not heavy
                 {"id": 545, "weight": 0.99, "lower": 0.4, "records": lost(sid, 5)}]
        rows = [{"at": now - 3600 - pace, "games": 10, "opponent": t["id"], "submission_id": sid,
                 "status": "requested", "dev": True, "ids": []} for t in teams]  # none due its daily probe
        block = {"teams": teams, "maps": LIVE_MAPS}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(online.ladder, "post", return_value={"ids": list(range(10))}):
            state = Path(directory)
            def at(when):
                with patch.object(online.time, "time", return_value=when):
                    return online.dev_round(state, state / "STOP", sid, block, [], {}, rows, set(), None, when)
            round_ = at(now)
            self.assertEqual((round_["probes"], round_["sent"], rows[-1]["probe"]), ([755], [755], "loss"))
            self.assertEqual(at(now + pace + 1)["sent"], [545])  # never two probes in a row
            # Its loss is still this version's, but its one extra probe is spent.
            self.assertEqual(at(now + 2 * pace + 2)["probes"], [])
        # A new version that loses to it gets its own.
        self.assertTrue(online.loss_probe_due({**teams[0], "records": lost(8700, 1)}, 8700, rows))
        # Re-review of aae839d: a probe the site refused queued no game, so it neither spends the extra probe nor
        # holds the next round's probes back.
        refused = [{**r, "status": "rejected"} for r in rows if r.get("probe") == "loss"]
        self.assertEqual(len(refused), 1)
        self.assertFalse(online.loss_probe_due(teams[0], sid, rows))
        self.assertTrue(online.loss_probe_due(teams[0], sid, refused))
        probe, share = {**refused[0], "status": "requested"}, {**rows[0], "at": refused[0]["at"] + 1}
        self.assertFalse(online.after_probe([{**share, "at": 0}, refused[0]]))  # the refused probe came last
        self.assertTrue(online.after_probe([probe, {**share, "status": "rejected"}]))  # a refused share pick did

    def test_a_stuck_team_first_in_the_order_does_not_block_the_one_free_slot(self):
        # Re-review of 74ad386: dev_requests kept one request per free slot. A team whose battle is stuck on the site
        # answers 409 and never gains games, so it stayed first: with one slot left, no dev battle went at all.
        dev = [{"id": t, "name": str(t), "weight": w} for t, w in
               ((545, 1.0), (752, 0.9), (722, 0.8), (446, 0.7), (609, 0.6), (753, 0.5))]
        playing = {722, 446, 609, 753}  # DEV_IN_FLIGHT less one
        self.assertEqual(len(playing), online.DEV_IN_FLIGHT - 1)
        def post(path, request):
            if request["teamId"] == 545:
                raise urllib.error.HTTPError("https://game.battlecode.au/api/v1/battles", 409, "pending", {}, None)
            return {"ids": list(range(10))}
        with tempfile.TemporaryDirectory() as directory, patch.object(online.ladder, "post", side_effect=post):
            state = Path(directory)
            round_ = online.dev_round(state, state / "STOP", 8636, {"teams": dev, "maps": LIVE_MAPS}, [], {}, [],
                                      playing, None, 0)
        self.assertEqual((round_["tried"], round_["pending"], round_["sent"]), ([545, 752], [545], [752]))

    def test_the_share_restarts_on_a_new_dev_submission_and_online_md_shows_it(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            caps_state(state)
            rows = {t["id"]: t for t in online.dev_report(state, CAPS["teams"], 8023, [])}
            self.assertEqual({k: rows[609][k] for k in ("weight_games", "score", "lower")},
                             {"weight_games": 120, "score": 0.967, "lower": 0.917})
            self.assertEqual((rows[755]["weight_games"], rows[755]["weight"]), (120, 0.033))  # 120 wins of 120
            # Dev test 5 plays a new bot: its share starts again from that submission's one game, a win.
            (last,) = [g for g in CAPS["games"] if g["opponent"] == 755][-1:]
            new = {**{k: v for k, v in last.items() if k not in ("summary", "replay")}, "id": last["id"] + 10**7,
                   "requested_at": "2026-09-28T00:00:00Z", "opponent_submission_id": 3999, "winner": "A"}
            caps_state(state, CAPS["games"] + [new])
            (row,) = [t for t in online.dev_report(state, CAPS["teams"], 8023, []) if t["id"] == 755]
            self.assertEqual((row["dev_submission_id"], row["weight_games"], row["weight"]), (3999, 1, 0.889))
            text = online.dev_text({"submission_id": 8023, "dev": {"teams": online.dev_report(
                state, CAPS["teams"], 8023, [])}})
            self.assertIn("share 0.157 from our latest 120 games against its submission 2787 (score 0.967, 95% lower "
                          "bound 0.917, over every version and pool period), 120 games this version:", text)

class TestDevReplays(unittest.TestCase):
    def test_a_loss_passed_over_for_the_sample_never_enters_it_later(self):
        # Before: with this cycle's replays and record reads spent, battle 335845's six wins were filed from its detail
        # while its four losses waited, and the next cycle took those losses into the sample (4 of its 4 games).
        detail, maps = BATTLES["detail_335845"], {m["name"]: m["id"] for m in BATTLES["maps"]}
        lost = [335847, 335848, 335849, 335853]
        rows = {str(g["id"]): g for g in detail["games"]}
        reads = []
        def get(path):
            gid = path.rsplit("/", 1)[1]
            reads.append(int(gid))
            record = BATTLES["records_335845"].get(gid) or {"winner": rows[gid]["winner"],
                                                             "mapId": maps[rows[gid]["mapName"]]}
            return {"match": {**detail["match"], **record, "id": int(gid), "status": "completed", "replayKey": "r"}}
        from subprocess import CompletedProcess
        summary = json.dumps({"checks": {"ok": True}, "result": {"winner": "B"}})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            index = lambda: json.loads((state / "online-replays/7676/index.json").read_text())
            def collect(**limits):
                reads.clear()
                with patch.multiple(online, **{"LOSS_REPLAYS_PER_CYCLE": online.LOSS_REPLAYS_PER_CYCLE, **limits}), \
                        patch.object(online.ladder, "get", side_effect=get), \
                        patch.object(online.ladder, "download", return_value=b"replay"), \
                        patch.object(online.time, "sleep"), \
                        patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, summary)] * 10):
                    return online.collect_games(state, state, 157, 7676, [detail], {545}, maps)
            self.assertEqual(collect(REPLAYS_PER_CYCLE=0, RESULTS_PER_CYCLE=0), [])
            self.assertEqual((len(index()["results"]), index()["owed"]), (6, lost))
            # Next cycle, replays to spare and the sample empty: still loss replays, LOSS_REPLAYS_PER_CYCLE of them,
            # the others left owed without a record read.
            games = collect(LOSS_REPLAYS_PER_CYCLE=2)
            self.assertEqual({g["id"]: g.get("kept") for g in games}, {335847: "loss", 335848: "loss"})
            self.assertEqual((reads, index()["owed"]), ([335847, 335848], lost[2:]))
            games = collect()
            self.assertEqual({g["id"]: g.get("kept") for g in games}, {g: "loss" for g in lost})
            self.assertNotIn("owed", index())

    def test_every_dev_loss_keeps_its_replay_past_the_sample_and_the_sample_stays_blind_to_results(self):
        # Before: past each team's first DEV_REPLAYS games only API results were kept, so none of the 15 games lost to
        # the weak dev teams on 2026-09-27 (11 on Trauma) could be watched. Battle 335845's real detail: we (A) lost
        # 335847, 335848, 335849 and 335853 to dev test 1.
        detail, maps = BATTLES["detail_335845"], {m["name"]: m["id"] for m in BATTLES["maps"]}
        lost = [335847, 335848, 335849, 335853]
        rows = {str(g["id"]): g for g in detail["games"]}
        def get(path):  # the sample has the first six games' records; the others' from the detail's own rows
            gid = path.rsplit("/", 1)[1]
            record = BATTLES["records_335845"].get(gid) or {"winner": rows[gid]["winner"],
                                                             "mapId": maps[rows[gid]["mapName"]]}
            return {"match": {**detail["match"], **record, "id": int(gid), "status": "completed", "replayKey": "r"}}
        from subprocess import CompletedProcess
        summary = json.dumps({"checks": {"ok": True}, "result": {"winner": "B"}})
        for sample, budget, losses, saved, kinds in (
                (0, 0, 40, lost, {g: "loss" for g in lost}),  # the sample full and this call's replays spent
                (0, 10, 2, lost[:2], {g: "loss" for g in lost[:2]}),  # DEV_LOSS_REPLAYS bounds them
                # Room in the sample but 3 replays this call: its first 3 games whatever their result, then losses.
                (50, 3, 40, [335845, 335846, *lost], {335847: None, 335848: "loss", 335849: "loss", 335853: "loss"})):
            with tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                with patch.object(online, "DEV_REPLAYS", sample), \
                        patch.object(online, "REPLAYS_PER_CYCLE", budget), \
                        patch.object(online, "DEV_LOSS_REPLAYS", losses), \
                        patch.object(online.ladder, "get", side_effect=get), \
                        patch.object(online.ladder, "download", return_value=b"replay"), \
                        patch.object(online.time, "sleep"), \
                        patch.object(online.subprocess, "run", side_effect=[CompletedProcess([], 0, summary)] * 10):
                    games = online.collect_games(state, state, 157, 7676, [detail], {545}, maps)
                self.assertEqual(sorted(g["id"] for g in games), sorted(saved))
                self.assertEqual({g["id"]: g.get("kept") for g in games if g["id"] in kinds}, kinds)
                index = json.loads((state / "online-replays/7676/index.json").read_text())
                self.assertEqual(len(index["games"]) + len(index["results"]), 10)  # every game recorded

    def test_a_game_whose_replay_is_given_up_keeps_its_result_in_the_full_record(self):
        # Before: a dev game whose replay failed three downloads stayed only in "invalid", so all_games (online.md's
        # records, context.md's) never counted it, and a failing loss replay shrank the losses for good (MISSES 55).
        detail, maps = BATTLES["detail_335845"], {m["name"]: m["id"] for m in BATTLES["maps"]}
        rows = {str(g["id"]): g for g in detail["games"]}
        def get(path):
            gid = path.rsplit("/", 1)[1]
            record = BATTLES["records_335845"].get(gid) or {"winner": rows[gid]["winner"],
                                                             "mapId": maps[rows[gid]["mapName"]]}
            return {"match": {**detail["match"], **record, "id": int(gid), "status": "completed", "replayKey": "r"}}
        clock = [online.time.time()]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            path = state / "online-replays/7676/index.json"
            for attempt in range(3):
                clock[0] += 7200  # past each failure's retry_after
                with patch.object(online.ladder, "get", side_effect=get), \
                        patch.object(online.ladder, "download", side_effect=OSError("replay store down")), \
                        patch.object(online.time, "sleep"), patch.object(online.time, "time", lambda: clock[0]):
                    self.assertEqual(online.collect_games(state, state, 157, 7676, [detail], {545}, maps), [])
                if attempt < 2:
                    self.assertEqual(online.all_games(json.loads(path.read_text())), [])  # retried, not given up
            index = json.loads(path.read_text())
            self.assertEqual(len(online.all_games(index)), 10)
            self.assertEqual(lost(online.all_games(index)), (4, 10))  # we lost 335847-49 and 335853
            self.assertNotIn("owed", index)
            self.assertTrue(all(bad["attempts"] == 3 for bad in index["invalid"].values()))


# Captured 2026-09-27 from <loop home>: online.json's compare for 7359 (v0032, site v31) against 7003 (v0030), with
# the opponents counted from online-replays (compare had no `opponents` yet); publishing.json's verified record; the
# last two uploads.jsonl rows; the results rows kept around then; I306 as it was queued (priority 2, before the review
# of 1790429284184623000 raised it by hand). All trimmed to the fields raise_rechecks reads (the ideas keep every
# field meta.IDEA_FIELDS requires, hypotheses shortened).
LIVE_COMPARE = {"previous": 7003, "pairs": 22, "before": 0.773, "after": 0.727,
                "opponents": [{"opponent": 158, "submission": 3687, "pairs": 10},
                              {"opponent": 545, "submission": 4525, "pairs": 7},
                              {"opponent": 945, "submission": 7335, "pairs": 5}],
                "longest_margin": {"pairs": 22, "before": 10.8, "after": 7.1, "up": 5, "down": 13}}
VERIFIED = {"commit": "7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50", "submission_id": 7359, "site_version": 31}
UPLOADS = [
    {"commit": "2c79f81d0d90038543c82dff930ce243cc4b16e8", "previous": 6925,
     "digest": "d73e0f082d1faab03f2bc0f71741847b56e2513901849e7d9669bb73cc956b9c",
     "previous_digest": "dd41cc9621653a8cf368fb0edbf98824d78d4db79db296da090e8d62c92fa8a4"},
    {"commit": "7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50", "previous": 7003,
     "digest": "d77036e2f141ec6f886e5ccd795b1f8086271586af957e9467f247e28a8ae3d6",
     "previous_digest": "d73e0f082d1faab03f2bc0f71741847b56e2513901849e7d9669bb73cc956b9c"}]
KEPT = [
    {"id": "E0106", "verdict": "kept", "candidate": "2c79f81d0d90038543c82dff930ce243cc4b16e8",
     "idea": {"id": "I083"}, "sprt": {"decision": "inconclusive", "limit_kept": {"probability": 0.988, "threshold": 0.9}}},
    {"id": "E0116", "verdict": "kept", "candidate": "5f37cbff9329ff653e93471ff28d6a948bafa1d6",
     "idea": {"id": "I216", "kind": "identical"}, "identity": {"ok": True}},
    {"id": "E0118", "verdict": "kept", "candidate": "7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50",
     "idea": {"id": "I243"}, "sprt": {"decision": "inconclusive", "elo": 2.82,
                                      "limit_kept": {"probability": 0.949, "threshold": 0.9}}},
    {"id": "E0125", "verdict": "kept", "candidate": "d52bfb4cac1a2706f791636d1bc8970b0718d59e",
     "idea": {"id": "I196"}, "sprt": {"decision": "H1"}}]
I306 = {"id": "I306", "category": "recheck", "priority": 2, "status": "todo", "source": "recheck:E0118",
        "title": "Re-check E0118 (I243): the champion with its behaviour switched off",
        "hypothesis": "E0118 (At the unit cap, long dragons go where there is room: target choice counts how crowded "
                      "the ground around a target is) was kept on probability, not H1",
        "history": ["E0118 kept I243, which this idea cites: raised to priority 2; check whether what it was waiting on "
                    "is now there"]}
# git merge-base --is-ancestor in the main checkout, 2026-09-27: 5f37cbf (v0031) and 7e4b60b are in the live build
# and not in 7003's 2c79f81; d52bfb4 (E0125, promoted after) is in neither.
ANCESTORS = {"7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50": {"2c79f81d0d90038543c82dff930ce243cc4b16e8",
                                                         "5f37cbff9329ff653e93471ff28d6a948bafa1d6",
                                                         "7e4b60b1f9a49decf45b4e4ba0ae1831a14d6b50"},
             "2c79f81d0d90038543c82dff930ce243cc4b16e8": {"2c79f81d0d90038543c82dff930ce243cc4b16e8"}}


class TestMarginRechecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name)
        (self.state / "publishing.json").write_text(json.dumps({"verified": VERIFIED}))
        (self.state / "uploads.jsonl").write_text("".join(json.dumps(r) + "\n" for r in UPLOADS))
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in KEPT))
        recheck_e0106 = {"id": "I267", "category": "recheck", "status": "trying:lane4", "priority": 1,
                         "source": "recheck:E0106",
                         "title": "Re-check E0106 (I083): the champion with its behaviour switched off",
                         "hypothesis": "E0106 (Stop our saturated swarm boxing its own dragons in: at-cap trapped "
                                       "deaths are walled by teammate bodies) was kept on probability, not H1"}
        (self.state / "ideas.jsonl").write_text(json.dumps(I306) + "\n" + json.dumps(recheck_e0106) + "\n")
        ancestry = patch.object(online, "is_ancestor", side_effect=lambda repo, c, d: c in ANCESTORS.get(d, ()))
        ancestry.start()
        self.addCleanup(ancestry.stop)
        self.known = patch.object(online, "known", return_value=True)
        self.known.start()
        self.addCleanup(self.known.stop)
        self.addCleanup(self.folder.cleanup)

    def ideas(self):
        return {i["id"]: i for i in map(json.loads, (self.state / "ideas.jsonl").read_text().splitlines())}

    def test_the_live_builds_are_named_from_publishing_and_the_upload_digests(self):
        self.assertEqual(online.live_commits(self.state, 7359, 7003),
                         (UPLOADS[0]["commit"], VERIFIED["commit"], ""))
        self.assertIsNone(online.live_commits(self.state, 7400, 7359)[0])  # not the verified submission
        self.assertIsNone(online.live_commits(self.state, 7359, 6824)[0])  # no upload made while 6824 was live

    def test_a_fallen_margin_raises_the_rechecks_of_the_changes_between_the_live_builds_once(self):
        # M33: E0118 was v0032's only behaviour change over v0030 and nothing reacted to the online compare.
        report = {"submission_id": 7359, "compare": LIVE_COMPARE}
        line = online.raise_rechecks(self.state, self.state, report)
        self.assertIn("longest margin 10.8 -> 7.1 (down in 13, up in 5 of 22 pairs, sign test p 0.05", line)
        self.assertIn("E0118 (kept on probability): re-check I306 raised to priority 1", line)
        self.assertIn("E0116 (kept on identical decisions (its games may differ through the sonar key)): no re-check queued", line)
        self.assertNotIn("E0106", line)  # already in 7003's build
        self.assertNotIn("E0125", line)  # promoted after the live build
        idea = self.ideas()["I306"]
        self.assertEqual(idea["priority"], 1)
        self.assertIn("3 opponents: team 158 sub 3687: 10, team 545 sub 4525: 7, team 945 sub 7335: 5",
                      idea["history"][-1])
        self.assertEqual(report["margin_rechecks"]["7003-7359"]["kept"], ["E0116", "E0118"])
        # Later cycles of the same pair write nothing.
        self.assertIsNone(online.raise_rechecks(self.state, self.state, report))
        self.assertEqual(len(self.ideas()["I306"]["history"]), 2)
        self.assertEqual((self.state / "host-alerts.log").read_text().count("longest margin"), 1)

    # Method M62: an H1 keep has no re-check to raise (E0239 was kept on H1 and fell online), so one is queued, with a
    # rammer's share of its trials, once. E0125 (kept on H1) is taken as inside the live build here.
    def test_an_h1_keep_between_the_builds_gets_a_recheck_against_a_rammer_once(self):
        inside = {**ANCESTORS, VERIFIED["commit"]: ANCESTORS[VERIFIED["commit"]] | {KEPT[3]["candidate"]}}
        report = {"submission_id": 7359, "compare": LIVE_COMPARE}
        with patch.object(online, "is_ancestor", side_effect=lambda repo, c, d: c in inside.get(d, ())):
            line = online.raise_rechecks(self.state, self.state, report)
            self.assertIsNone(online.raise_rechecks(self.state, self.state, report))
        [queued] = [i for i in self.ideas().values() if i.get("source") == "recheck:E0125"]
        self.assertIn(f"E0125 (kept on H1): re-check {queued['id']} queued at priority 1", line)
        self.assertEqual((queued["priority"], queued["evaluation"]), (1, {"opponents": online.recheck_mix()}))
        self.assertTrue(online.meta.opponent_mix_ok(queued["evaluation"]["opponents"]))
        # The mix adds to tradedown's rotation seat: its total over the trials is RECHECK_SHARE, not the mix share.
        import driver
        order = driver.trial_order({**queued["evaluation"], "rotation": list(driver.ROTATION)}, 2400)
        self.assertAlmostEqual(order.count("tradedown") / len(order), online.RECHECK_SHARE, delta=0.01)
        self.assertEqual(self.ideas()["I306"]["priority"], 1)  # the probability keep's is raised as before

    # sig 2026-09-29: no re-check is queued at promotion any more, so a probability keep between the builds has none to
    # raise and the fall queues one, the compare its evidence (before, only an H1 keep got one here); a formula keep's
    # is left to a review's off-generator evidence.
    def test_a_keep_with_no_recheck_gets_one_queued_on_the_fall_and_a_formula_none(self):
        rows = [dict(r, idea={**r["idea"], "kind": "formula"}, identity={}) if r["id"] == "E0116" else r for r in KEPT]
        (self.state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (self.state / "ideas.jsonl").write_text("")
        line = online.raise_rechecks(self.state, self.state, {"submission_id": 7359, "compare": LIVE_COMPARE})
        [queued] = [i for i in self.ideas().values() if i.get("source") == "recheck:E0118"]
        self.assertIn(f"E0118 (kept on probability): re-check {queued['id']} queued at priority 1", line)
        self.assertEqual((queued["priority"], queued["evaluation"]), (1, {"opponents": online.recheck_mix()}))
        self.assertIn("Evidence it now hurts: the online compare fell after it went live (submission 7359 against "
                      "7003", queued["hypothesis"])
        self.assertIn("E0116 (kept on no loss (a formula)): no re-check queued", line)
        self.assertEqual(len(self.ideas()), 1)

    # Review of 2026-09-29: E0331's re-check I937 (as it was, trimmed) was dropped, and any re-check by that source
    # counted, so a fall with E0331 between the builds would queue none. Only a waiting one counts.
    def test_a_dropped_recheck_doesnt_stop_a_fall_queueing_one(self):
        e0331 = {"id": "E0331", "verdict": "kept", "candidate": KEPT[3]["candidate"], "stacked_on": [],
                 "idea": {"id": "I921", "title": "Retest E0327 to confirm on near_trauma"},
                 "evaluation": {"maps": {"portalrooms": 0.5}},
                 "sprt": {"decision": "inconclusive", "elo": 3.58, "limit_kept": {"probability": 0.938, "threshold": 0.9}}}
        i937 = {"id": "I937", "category": "recheck", "priority": 3, "status": "dropped", "source": "recheck:E0331",
                "title": "Re-check E0331 (I921): the champion with its behaviour switched off", "hypothesis": "h",
                "history": ["orchestrator 2026-09-29 13:45: dropped: sig 2026-09-29, switch-off re-checks are wasteful"]}
        (self.state / "results.jsonl").write_text(json.dumps(e0331) + "\n")
        (self.state / "ideas.jsonl").write_text(json.dumps(i937) + "\n")
        inside = {**ANCESTORS, VERIFIED["commit"]: ANCESTORS[VERIFIED["commit"]] | {e0331["candidate"]}}
        with patch.object(online, "is_ancestor", side_effect=lambda repo, c, d: c in inside.get(d, ())):
            line = online.raise_rechecks(self.state, self.state, {"submission_id": 7359, "compare": LIVE_COMPARE})
        [queued] = [i for i in self.ideas().values() if i["status"] == "todo"]
        self.assertEqual((queued["source"], queued["priority"]), ("recheck:E0331", 1))
        self.assertIn(f"E0331 (kept on probability): re-check {queued['id']} queued", line)

    # Review of 2026-09-29: a margin fall at p 0.048 is one of every version checked (62 by then), so it queues a new
    # re-check only under TRIGGER_P over that count, as version_alert's bar; below it the alert names the suspect.
    def test_a_fall_under_the_bar_for_one_check_but_not_for_all_queues_no_new_recheck(self):
        (self.state / "ideas.jsonl").write_text("")
        (self.state / "uploads.jsonl").write_text("".join(json.dumps({"commit": f"{n:07x}", "previous": 6000 + n})
                                                          + "\n" for n in range(60)) + "".join(
            json.dumps(r) + "\n" for r in UPLOADS))
        line = online.raise_rechecks(self.state, self.state, {"submission_id": 7359, "compare": LIVE_COMPARE})
        self.assertEqual(self.ideas(), {})
        self.assertIn("E0118 (kept on probability): no re-check queued, p 0.048 not under 0.002 (0.1 over 62 versions "
                      "checked); the orchestrator decides", line)
        self.assertIn("wait for a confirming sample", line)

    def test_a_recheck_already_running_is_named_not_changed(self):
        report = {"submission_id": 7003, "compare": {**LIVE_COMPARE, "previous": 6925}}
        (self.state / "publishing.json").write_text(json.dumps({"verified": {"commit": UPLOADS[0]["commit"],
                                                                             "submission_id": 7003}}))
        (self.state / "uploads.jsonl").write_text(json.dumps({"commit": "c4f28ca", "digest": "x"}) + "\n"
                                                 + json.dumps({**UPLOADS[0], "previous_digest": "x"}) + "\n")
        with patch.object(online, "is_ancestor", side_effect=lambda repo, c, d: c == d == UPLOADS[0]["commit"]):
            line = online.raise_rechecks(self.state, self.state, report)
        self.assertIn("E0106 (kept on probability): no re-check to do (I267 trying:lane4)", line)
        self.assertEqual(self.ideas()["I267"]["status"], "trying:lane4")

    def test_a_build_missing_from_the_repo_is_left_for_a_hand_check(self):
        # git's "not an ancestor" for an unknown commit would name every kept change as a suspect.
        report = {"submission_id": 7359, "compare": LIVE_COMPARE}
        with patch.object(online, "known", side_effect=lambda repo, c: c != UPLOADS[0]["commit"]):
            line = online.raise_rechecks(self.state, self.state, report)
        self.assertIn("can't name the changes between the two builds", line)
        self.assertEqual(self.ideas()["I306"]["priority"], I306["priority"])

    def test_seven_of_ten_down_is_luck_and_does_nothing(self):
        margin = {"pairs": 10, "before": 9.0, "after": 5.0, "up": 3, "down": 7}
        report = {"submission_id": 7359, "compare": {**LIVE_COMPARE, "pairs": 10, "longest_margin": margin}}
        self.assertIsNone(online.raise_rechecks(self.state, self.state, report))
        self.assertFalse((self.state / "host-alerts.log").exists())
        self.assertEqual(self.ideas()["I306"]["priority"], 2)

    def test_unknown_builds_are_alerted_once_for_a_hand_check(self):
        (self.state / "uploads.jsonl").write_text("")
        report = {"submission_id": 7359, "compare": LIVE_COMPARE}
        self.assertIn("can't name the changes", online.raise_rechecks(self.state, self.state, report))
        self.assertIsNone(online.raise_rechecks(self.state, self.state, report))
        self.assertEqual(self.ideas()["I306"]["priority"], 2)


# Real online games captured 2026-09-27, replay paths dropped: every saved game of submission 7594 (16, 6 to the round
# limit: losses 308881 on 512 tiles with 7 of ours left and 308878 on Trauma, 1152 tiles, with 8) and three of the live
# 7625's (310517 won and 310518 lost to the round limit, 310519 an elimination). v0029's params.hpp: mergeMinUnits 8.
ROUND500 = json.loads((TESTDATA / "online-round500-sample.json").read_text())
PARAMS = (TESTDATA / "params-v0029.hpp").read_text()


class TestRound500(unittest.TestCase):
    def state(self, directory: str) -> Path:
        state = Path(directory)
        (state / "online-replays/7594").mkdir(parents=True)
        online.save(state / "online-replays/7594/index.json", {"games": ROUND500["7594"]})
        online.save(state / "online.json", ROUND500["online.json"])
        return state

    # Review M41: 41 of 78 round-500 losses of 7003-7594 ended at the merge floor, which nothing reported.
    def test_each_version_counts_its_round_500_losses_at_the_floor_and_under_the_fold_all_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            section = online.round500(online.saved_games(self.state(directory)), online.merge_floor(PARAMS))
        self.assertEqual(section["floor"], 8)
        old, live = section["versions"]
        # 7594: both losses ended with 8 or fewer, 1 of its 4 wins did; 2 of 2 against 1 of 4 is luck 20% of the time.
        self.assertEqual({k: old[k] for k in ("submission_id", "losses", "wins", "draws", "bound_above_theirs",
                                              "losses_at_floor", "wins_at_floor", "floor_p")},
                         {"submission_id": 7594, "losses": 2, "wins": 4, "draws": 0, "bound_above_theirs": 2,
                          "losses_at_floor": 2, "wins_at_floor": 1, "floor_p": 0.2})
        self.assertEqual({band: (c["losses"], c["wins"]) for band, c in old["by_size"].items()},
                         {"<=1024": (1, 2), "1025-2399": (1, 1), ">=2400": (0, 1)})
        self.assertEqual((live["submission_id"], live["losses"], live["wins"]), (7625, 1, 1))  # 310519 not counted
        # Trauma's loss: 8 alive, longest 12 of 53, so the bound is 12 + 41 / 2 = 32.5 against their 15.
        trauma = next(r for r in section["recent_losses"] if r["id"] == 308878)
        self.assertEqual({k: trauma[k] for k in ("tiles", "size", "alive", "longest", "total", "their_longest",
                                                 "bound", "at_floor")},
                         {"tiles": 1152, "size": "1025-2399", "alive": 8, "longest": 12, "total": 53,
                          "their_longest": 15, "bound": 32.5, "at_floor": True})
        self.assertEqual([r["id"] for r in section["recent_losses"]], [310518, 308881, 308878])  # newest first
        # M74: at the king's measured share of a folder's drop (0.425 of ceil(L/2)), 12 + 0.425 x 41 / 2.
        share = online.KING_FOLD_SHARE
        with tempfile.TemporaryDirectory() as directory:
            shared = online.round500(online.saved_games(self.state(directory)), 8, share)
        trauma = next(r for r in shared["recent_losses"] if r["id"] == 308878)
        self.assertEqual((share, trauma["bound_share"]), (0.425, 20.71))
        self.assertEqual(shared["versions"][0]["bound_share_above_theirs"],
                         sum(r["bound_share"] > r["their_longest"] for r in shared["recent_losses"]
                             if r["submission_id"] == 7594))
        self.assertNotIn("bound_share_above_theirs", section["versions"][0])  # round500 without a share
        with tempfile.TemporaryDirectory() as directory:  # measured, not the bot's tunable P.capFeedFoldShare
            text = online.round500_text(self.state(directory), "    double capFeedFoldShare = 0.9;")
        self.assertIn("(0.425 of ceil(L/2)", text)

    def test_the_floor_is_read_from_params_not_assumed(self):
        with tempfile.TemporaryDirectory() as directory:
            games = online.saved_games(self.state(directory))
        lower = online.round500(games, online.merge_floor(PARAMS.replace("mergeMinUnits = 8", "mergeMinUnits = 7")))
        trauma = next(r for r in lower["recent_losses"] if r["id"] == 308878)
        self.assertFalse(trauma["at_floor"])  # 8 left is above a floor of 7
        self.assertEqual(lower["versions"][0]["losses_at_floor"], 1)
        none = online.round500(games, online.merge_floor("struct Params {};"))
        self.assertNotIn("losses_at_floor", none["versions"][0])
        self.assertIsNone(none["recent_losses"][0]["at_floor"])

    # tools/testdata/trauma-95034.replay is 3182's saved game 95034 (side B, won at the round limit): its row is the replay's.
    def test_a_games_row_matches_its_replay(self):
        sys.path.insert(0, str(TRAUMA_REPLAY.parent.parent))
        import replays
        game = replays.analyse(replays.load_replay(TRAUMA_REPLAY))
        row = online.round500_game(OLD_SUMMARY, 8)
        self.assertEqual((row["result"], row["alive"], row["longest"], row["total"], row["their_longest"]),
                         ("won", game.standing["B"]["alive"], game.standing["B"]["longest"],
                          game.standing["B"]["total"], game.standing["A"]["longest"]))
        self.assertEqual((row["bound"], row["at_floor"], row["size"]), (6 + (42 - 6) / 2, False, "1025-2399"))

    # Review M52: 37 round-500 losses had their king 19.5 long at r396 against our 4.8, which nothing reported. The
    # sample's real games, each given kings (their summaries predate them).
    def test_each_version_gives_both_kings_at_the_fold_for_losses_and_wins_apart(self):
        with tempfile.TemporaryDirectory() as directory:
            games = online.saved_games(self.state(directory))
        def king(length: int, meals: int) -> dict:
            return {"round": 396, "length": length, "meals": meals, "splitOff": 2, "sprints": 1, "handedOn": 0}

        for g in games:
            if g["submission_id"] == 7594:
                won = g["summary"]["result"]["winner"] == g["side"]
                ours, theirs = ("A", "B") if g["side"] == "A" else ("B", "A")
                g["summary"]["teams"][ours]["king"] = king(12 if won else 4, 10)
                g["summary"]["teams"][theirs]["king"] = king(6 if won else 20, 4)
        old = online.round500(games, online.merge_floor(PARAMS))["versions"][0]
        self.assertEqual((old["losses"], old["wins"]), (2, 4))
        lost, won = old["king"]["lost"], old["king"]["won"]
        self.assertEqual((lost["n"], lost["ours"]["length"], lost["theirs"]["length"], lost["their_longer"]),
                         (2, 4, 20, 2))
        self.assertEqual((won["n"], won["ours"]["length"], won["theirs"]["meals"], won["their_longer"]), (4, 12, 4, 0))
        self.assertEqual(lost["longer_p"], 0.25)  # 2 of 2 by luck alone
        self.assertEqual(old["by_size"]["<=1024"]["king"]["lost"]["n"], 1)
        # A version whose summaries predate the kings has none.
        self.assertNotIn("king", online.round500(games, None)["versions"][1])
        self.assertNotIn("build", lost)  # M99: kings summarised before the r250 build have no build row
        lost_ids = {r["id"] for r in online.round500(games, None)["recent_losses"] if r["submission_id"] == 7594}
        loss = next(g for g in games if g["id"] in lost_ids)
        build = {"round": 250, "length": 3, "spawned": 6, "teammateDrops": 9, "enemyDrops": 0, "splitOff": 16,
                 "sprints": 0, "handedOn": 0}
        for team in loss["summary"]["teams"].values():
            team["king"]["build"] = build
        lost = online.round500(games, online.merge_floor(PARAMS))["versions"][0]["king"]["lost"]
        self.assertEqual((lost["build"]["n"], lost["build"]["ours"]["teammateDrops"]), (1, 9))
        self.assertTrue(online.stale_summary({"summary": {**games[0]["summary"], "result": {"endReason": "roundLimit"},
                                                          "teams": {"A": {"at400": 1, "pearlRace": 1, "portals": 1,
                                                                          "king": king(4, 1)}}}}))

    # The sample's pre-at400 summary of 3182's game 95034, re-read from its replay: its row gives the replay's kings.
    def test_a_round_limit_summary_without_kings_is_re_read_and_its_row_gives_them(self):
        sys.path.insert(0, str(TRAUMA_REPLAY.parent.parent))
        import replays
        game = replays.analyse(replays.load_replay(TRAUMA_REPLAY))
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/3182"
            folder.mkdir(parents=True)
            summary = json.loads(json.dumps(OLD_SUMMARY["summary"]))
            for team in summary["teams"].values():
                team["at400"] = None  # as if it had at400 but no kings: still re-read
            ended = json.loads(json.dumps(summary))
            ended["result"]["endReason"] = "teamEliminated"
            ended["teams"]["A"]["pearlRace"] = {}  # M78: one without the pearl race is re-read too
            self.assertTrue(online.stale_summary({"summary": summary | {"result": ended["result"]}}))
            self.assertTrue(online.stale_summary({"summary": ended}))  # M80: and one without the portal crossings
            ended["teams"]["A"]["portals"] = {}
            self.assertTrue(online.stale_summary({"summary": ended}))  # M95: and one whose race was in Manhattan steps
            ended["teams"]["A"]["raceBy"] = "path"
            games =[{**OLD_SUMMARY, "summary": summary, "replay": str(TRAUMA_REPLAY)},
                     {**OLD_SUMMARY, "id": 2, "summary": ended, "replay": str(TRAUMA_REPLAY)}]
            (folder / "index.json").write_text(json.dumps({"games": games, "invalid": {}}))
            self.assertEqual([online.stale_summary(g) for g in games], [True, False])
            online.refresh_summaries(Path(__file__).resolve().parent.parent, state, [3182])
            saved = json.loads((folder / "index.json").read_text())["games"]
        self.assertEqual(saved[1]["summary"], ended)  # an elimination's summary has no kings to add
        row = online.round500_game(saved[0], 8)
        self.assertEqual((row["king"], row["their_king"]),
                         (replays.king_at_fold(game, "B"), replays.king_at_fold(game, "A")))
        ours = row["king"]
        self.assertEqual(ours["length"] + ours["meals"] - ours["splitOff"] - ours["sprints"] - ours["handedOn"],
                         row["longest"])

    def test_online_md_carries_the_section(self):
        with tempfile.TemporaryDirectory() as directory:
            text = online.evidence_text(self.state(directory), PARAMS)
        self.assertIn("round500 (review M41)", text)
        self.assertIn("`king` (review M52)", text)
        self.assertIn("8 dragons or fewer", text)
        self.assertIn('"id": 308878', text)


# Real rows (2026-09-27): 8023's and 8151's dev results against team 545's submission 4525 on Trophy (11) and Devil
# (13), all with us as side A, and E0167's official-map games there (its candidate 412171b is 8151's commit).
SIDES = json.loads((TESTDATA / "online-dev-sides-sample.json").read_text())
MAPS = Path(__file__).resolve().parent.parent / "maps/ladder"


class TestDevSides(unittest.TestCase):
    # A loop home with 8023's results saved, E0167's official-map report (`official`, the real games by default) and
    # 8151 verified as `commit`.
    def home(self, directory: str, commit: str, official: list[dict] | None = None) -> Path:
        state = Path(directory)
        (state / "online-replays/8023").mkdir(parents=True)
        online.save(state / "online-replays/8023/index.json",
                    {"games": [], "results": [r for r in SIDES["results"] if r["submission_id"] == 8023]})
        run = state / "runs/E0167"
        (run / "official-maps").mkdir(parents=True)
        for stem in ("trophy", "devil"):
            (run / f"official-maps/{stem}~0.map").write_text((MAPS / f"{stem}.map").read_text())
        (run / "official.jsonl").write_text("".join(json.dumps(r) + "\n" for r in official or SIDES["official"]))
        online.save(run / "official.json", {"score": 0.5, "games": 20})
        online.save(run / "evaluation.json", SIDES["evaluation"])
        online.save(state / "publishing.json", {"verified": {"submission_id": 8151, "commit": commit}})
        online.save(state / "ladder-maps.json", {"maps": SIDES["ladder_maps"]})
        return state

    # online.md's dev section for 8151 against the benchmark, from `current` (8151's games) and the home's saved ones.
    def section(self, state: Path, current: list[dict]) -> str:
        bench = {"id": 545, "name": "dev test 1 :P", "elo": 1936, "why": "highest-rated dev team"}
        return online.dev_text({"submission_id": 8151, "local_sides": online.local_sides(state, 8151),
                                "benchmark": online.benchmark_report(state, bench, 8151, current)})

    # The per-map rows of the section's compare, parsed.
    def rows(self, text: str) -> dict[int, dict]:
        line = next(line for line in text.splitlines() if line.startswith("  [{"))
        return {row["map_id"]: row for row in json.loads(line)}

    def test_one_sided_dev_rows_get_the_side_count_and_our_local_score_as_that_side(self):
        # Read as before the pool change, when these rows were played (the figure is the current period's).
        self.enterContext(patch.object(online.time, "time", return_value=datetime.fromisoformat(
            online.POOL_CUTOFF.replace("Z", "+00:00")).timestamp() - 3600))
        commit = SIDES["evaluation"]["candidate"]
        current = [r for r in SIDES["results"] if r["submission_id"] == 8151]
        with tempfile.TemporaryDirectory() as directory:
            state = self.home(directory, commit)
            text = self.section(state, current)
            self.assertIn("8023 vs dev 4525 (before 2026-09-27T11:00:00Z): 8 games, 1-0-7, score 0.125", text)
            self.assertIn("16 of 16 dev games here had us as side A,", text)
            self.assertIn("side A 8/8", text)
            self.assertIn("A 16/16", text)  # the compare's games, both versions
            rows = self.rows(text)
            # E0167's candidate as side A: 3 of 5 on Trophy and on Devil (as B, 2 of 5 on each).
            self.assertEqual((rows[11]["map"], rows[11]["local_A"], rows[11]["local_A_games"]), ("trophy", 0.6, 5))
            self.assertEqual((rows[13]["map"], rows[13]["local_A"]), ("devil", 0.6))
            self.assertIn(f"{commit[:7]}", text)
            # As E0167's base instead, our side is the other one of each game and our score its complement. The real
            # candidate's A and B sum to 1 on Trophy (0.6, 0.4), which a version ignoring the role would also give, so
            # one Trophy game the candidate won as B is lost here: candidate A 0.6, B 0.2, so the base A 0.8, B 0.4.
            flipped = [{**r, "winner": "b"} if "trophy~0" in r["map"] and r["a_side"] == "B" and r["winner"] == "a"
                       else r for r in SIDES["official"]]
            base = online.local_sides(self.home(tempfile.mkdtemp(dir=directory), SIDES["evaluation"]["base"], flipped),
                                      8151)
            candidate = online.local_sides(self.home(tempfile.mkdtemp(dir=directory), commit, flipped), 8151)
            self.assertEqual((base["role"], candidate["role"]), ("base", "candidate"))
            trophy = next(m for m in base["maps"] if m["map_id"] == 11)
            theirs = next(m for m in candidate["maps"] if m["map_id"] == 11)
            self.assertEqual((theirs["A"]["score"], theirs["B"]["score"]), (0.6, 0.2))
            self.assertEqual((trophy["A"], trophy["B"]), ({"score": 0.8, "games": 5}, {"score": 0.4, "games": 5}))
            # After the pool change the local figure describes the new maps, so these rows from before it get none.
            after = datetime.fromisoformat(online.POOL_CUTOFF.replace("Z", "+00:00")).timestamp() + 60
            with patch.object(online.time, "time", return_value=after):
                rows = self.rows(self.section(state, current))
            self.assertEqual(rows[11]["local_A"], "no local side-A figure for this pool period")
            # Without an official-map report our commit played, each row says it has no local figure.
            (state / "runs/E0167/official.json").unlink()
            rows = self.rows(self.section(state, current))
            self.assertEqual(rows[11]["local_A"], "no local side-A figure")

    def test_records_written_before_sides_count_as_unknown(self):
        sides = online.records_sides([{"games": 5}, {"games": 3, "sides": {"A": 3}}, {"games": 1, "sides": {"?": 1}}])
        self.assertEqual(sides, {"A": 3, "B": 0, "unknown": 6})
        text = online.local_sides_text({"note": "none"}, sides)
        self.assertIn("3 of 3 dev games here had us as side A (6 more, from records written before review M48", text)
        self.assertIn("no dev game here says", online.local_sides_text({"note": "none"}, {"unknown": 2}))

    def test_a_mixed_side_team_shows_both_counts_and_no_one_sided_figure(self):
        # No dev game has yet had us as B (the battle API takes no side), so half of 8151's are flipped here.
        current = [{**r, "side": "B", "winner": "A" if r["winner"] == "B" else "B"} if i % 2 else r
                   for i, r in enumerate(r for r in SIDES["results"] if r["submission_id"] == 8151)]
        with tempfile.TemporaryDirectory() as directory:
            state = self.home(directory, SIDES["evaluation"]["candidate"])
            text = self.section(state, current)
            self.assertIn("8151 vs dev 4525 (before 2026-09-27T11:00:00Z): 8 games", text)
            self.assertIn("our side in the 16 dev games here that say: A 12/16, B 4/16,", text)
            self.assertIn("side A 4/8, B 4/8", text)
            self.assertIn("A 12/16, B 4/16", text)
            self.assertNotIn("local_A", json.dumps(self.rows(text)))


# A trimmed copy of version 8213's real index (its rows against the four dev teams audit 1790492423260536000 counted):
# 178 saved replays, 84 of them kept because we lost, and 572 API results.
INDEX_8213 = json.loads((TESTDATA / "online-8213-dev-index.json").read_text())
DEV4 = {545, 446, 752, 722}


def lost(games: list[dict]) -> tuple[int, int]:
    return sum(online.game_score(g) == 0 for g in games), len(games)


# Every place in loop/ and tools/ (not tests) that may read our online indexes or online.json's games, as
# folder/module:function, with how it counts them: a function holding a string that names online-replays or
# index.json, or both "online.json" and "games" (so readers of other index.json files, such as the leaders' sample,
# are listed too). A new one fails test_every_reader_of_our_online_games_is_checked until it is checked against
# MISSES 55 (a rate, record or per-map/per-opponent comparison counts every game, online.all_games; a rate or mean
# over replay summaries reads online.sample_games; a result-chosen set is only watched, labelled) and added here. A
# tripwire, not a parser: an index read through a path built entirely elsewhere isn't seen.
ONLINE_READERS = {
    "loop/online:played_against": "every game (all_games): the dev records, compares, weights and caps",
    "tools/sonar/games:games": "other teams' sonar values for the listen table; no rate of ours",
    "loop/discord_teams:tracked": "which teams we played this version, to track their renames; no rate",
    "loop/online:saved_games": "sample_games: round500 means",
    "loop/online:previous_version": "saved games for ladder pairs; ladder_compare and choose_match leave dev out",
    "loop/online:group_index": "a play group's indexes as one, for its callers to count; no rate",
    "loop/online:sweep_scores": "the sweep's shares: every game's result (all_games), dev games apart",
    "loop/online:refresh_rams": "the live version's ram rows: sample_games only, dev games apart",
    "loop/online_fit:pending": "the fit rows' games: sample_games only, dev games apart",
    "loop/online_fit:run": "replays pending's games and writes their rows, no rate",
    "loop/online_fit:rebuild_variants": "pearl timings from games pending chose, no rate",
    "loop/online_fit:rebuild_layout": "one layout's games from rebuild_variants, their pearls only, no rate",
    "loop/health:online_rams": "whether sample_games got ram rows (dev games apart), no rate",
    "loop/online_fit:index_of": "the fit folder's own index",
    "loop/online_fit:versions": "version folders' names only",
    "loop/online:cycle": "per_map and compare over ladder games, all saved; winner_check is agreement, not a rate",
    "loop/online:evidence_text": "lists 10 recent ladder games, no rate",
    "loop/judge:inputs": "online.json's per_map totals (map_totals: every ladder game, dev apart), for the judge",
    "loop/online:collect_games": "writes the indexes",
    "loop/online:index": "collect_games' index cache",
    "loop/online:store": "collect_games' index write",
    "loop/online:recorded_ids": "ids only",
    "loop/online:refresh_summaries": "rewrites summaries",
    "loop/online:refresh_dooms": "adds each saved game's death kinds",
    "loop/online:refresh_starts": "adds each saved game's layout and start",
    "loop/online:doom_text": "sample_games: the death table's per-game means, dev apart",
    "loop/online:refresh_bceval": "adds each saved loss's bceval turning point",
    "loop/online:bceval_text": "saved losses' turning points by group, kept ones included and said to be no loss rate",
    "loop/health:death_tables": "alert text",
    "loop/health:layouts": "whether saved games have starts, no rate",
    "loop/health:bceval": "whether the latest bceval readings all failed, no rate",
    "loop/health:map_flags": "every non-dev game (all_games) of the live version, alone and pooled with the "
                               "previous, per map",
    "loop/health:version_map_records": "map_records' per-map tallies over every non-dev game (all_games)",
    "tools/watch:_index_games": "each replay's side and dev flag",
    "loop/online:dev_poll": "requests, no games",
    "loop/audit:games": "watching list, losses first, labelled (and kept replays marked)",
    "loop/audit:record_lines": "every game (all_games) beside the sample (sample_games)",
    "loop/audit:blind_picks": "2 random live losses for the auditor to watch before any count, a result-chosen set only watched; no rate",
    "loop/maintenance:benchmark_games": "rows for dev_section, which leaves out file_selected ones",
    "loop/maintenance:dev_section": "the benchmark's GAP_STATS means over files, file_selected ones left out",
    "loop/maintenance:our_section": "sample_games: our ladder games' GAP_STATS means",
    "loop/driver:copy_online_sample": "coders' watching sample, losses first and marked kept, counted in its index "
                                      "and PROMPT.md",
    "loop/driver:copy_leaders_sample": "writes .leaders/index.json",
    "loop/driver:copy_online_unselected": "coders' result-blind sample: sample_games less file_selected files",
    "loop/rulecounts:online_counterparts": "sample_games of the live build, dev apart: rule-counts.md's online means",
    "loop/rulecounts:online_sample": "the live build's saved games less file_selected and dev ones, drawn blind by "
                                     "seed: the replayed online column (M90)",
    "loop/prune:version_games": "ids and ranked flags for kept_versions, no rate",
    "tools/replays:result_chosen": "names the kept replays",
    "tools/value:sources": "training rows from saved replay files, file_selected ones left out (kept losses and a "
                           "pruned version's dev files, prune.mark_dev_rows), no rate",
    "loop/health:audit_labels": "label check, no rate",
    "loop/health:dev_battles": "alert text",
    "loop/health:online_recording": "counts unrecorded games and missing API fields, no rate",
    "loop/health:leaders": "whether any ladder game exists",
    "loop/health:unstudied_versions": "saved replays to study per opponent version, a count, no rate",
    "loop/health:old_kept_marks": "counts the kept marks prune's old marking left, no rate",
    "loop/prune:prunable": "versions whose dev wins go, no rate",
    "loop/prune:write_games": "rewrites an index's games",
    "loop/prune:tick": "deletes files",
    "loop/replay_library:version_indexes": "lists our versions' index files",
    "loop/replay_library:build": "the library's games: file_selected ones (kept losses, pruned dev files) and dev games "
                                 "left out; all_games only for which submissions played ranked, no rate",
    "tools/replays:library_path": "the library's path; library_games reads only what build selected",
}


class TestSelectedSample(unittest.TestCase):
    def test_the_full_record_counts_every_game_and_the_sample_leaves_out_kept_losses(self):
        dev = lambda games: [g for g in games if g["opponent"] in DEV4]
        # The artifact: `games` alone, where 84 of 178 were kept because we lost.
        self.assertEqual(lost(dev(INDEX_8213["games"])), (107, 178))
        self.assertEqual(lost(dev(online.all_games(INDEX_8213))), (177, 750))
        self.assertEqual(lost(dev(online.sample_games(INDEX_8213["games"]))), (23, 94))
        self.assertEqual(sum(map(online.selected, INDEX_8213["games"])), 84)

    def test_the_dev_records_online_md_prints_are_the_full_record(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online-replays/8213").mkdir(parents=True)
            (state / "online-replays/8213/index.json").write_text(json.dumps(INDEX_8213))
            played = online.played_against(state, DEV4, 8213, INDEX_8213["games"])
            records = [r for team in DEV4 for r in online.team_records(played[team])]
            self.assertEqual((sum(r["losses"] for r in records), sum(r["games"] for r in records)), (177, 750))

    def test_round500_means_read_no_replay_kept_because_we_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "online-replays/8213").mkdir(parents=True)
            (state / "online-replays/8213/index.json").write_text(json.dumps(INDEX_8213))
            saved = online.saved_games(state)
            self.assertEqual(len(saved), 94)
            self.assertFalse(any(map(online.selected, saved)))

    def test_every_reader_of_our_online_games_is_checked(self):
        import ast
        repo = Path(__file__).resolve().parent.parent
        found = set()
        for folder in ("loop", "tools"):
            for path in (repo / folder).rglob("*.py"):
                if path.name.startswith("test_"):
                    continue
                module = path.relative_to(repo / folder).with_suffix("").as_posix()
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        strings = {n.value for n in ast.walk(node)
                                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
                        if (any("online-replays" in v for v in strings) or {"online.json", "games"} <= strings
                                or "index.json" in strings):
                            found.add(f"{folder}/{module}:{node.name}")
        self.assertEqual(found, set(ONLINE_READERS),
                         "a new reader of our online games: check it against MISSES 55, then add it to ONLINE_READERS")



# online.md's death table (MISSES 64) on our real Queen Of Spades loss 403920 (tools/testdata/queen-403920.replay).
class TestDeathTable(unittest.TestCase):
    def test_each_saved_game_gets_its_deaths_by_kind_and_the_table_leaves_out_kept_losses(self):
        repo = Path(__file__).resolve().parent.parent
        replay = repo / "tools/testdata/queen-403920.replay"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/8636"
            folder.mkdir(parents=True)
            summary = {"map": {"name": "Queen Of Spades"}}
            games = [{"id": 403920, "side": "A", "opponent": 722, "replay": str(replay), "summary": summary},
                     {"id": 2, "side": "A", "opponent": 722, "replay": str(folder / "gone.replay"), "summary": summary}]
            (folder / "index.json").write_text(json.dumps({"games": games}))
            online.refresh_dooms(repo, state, 8636)
            saved = json.loads((folder / "index.json").read_text())["games"]
            self.assertEqual(sum(saved[0]["doom"]["ours"].values()), 35)
            self.assertEqual((saved[0]["doom"]["ours"]["pocket deeper"], saved[0]["doom"]["ours"]["split-out"]), (4, 9))
            self.assertNotIn("doom", saved[1])  # no replay: left for the health check to see, not failed
            kept = {**saved[0], "id": 3, "kept": "loss"}
            ladder = {**saved[0], "id": 4, "opponent": 500}
            (state / "online.json").write_text(json.dumps({"submission_id": 8636, "games": [saved[0], kept, ladder],
                                                           "dev": {"teams": [{"id": 722}]}}))
            text = online.doom_text(state)
        self.assertIn("2 games have it", text)  # the kept loss is left out
        ladder_part, dev_part = text.split("ladder and autoscrim games:")[1].split("dev games:")
        self.assertIn("Queen Of Spades: 1 games, deaths a game ours 35.0, theirs 18.0", ladder_part)
        self.assertIn("Queen Of Spades: 1 games, deaths a game ours 35.0, theirs 18.0", dev_part)
        self.assertIn("a feed (feed?), a cap feed before foldRound (capfeed?, M63) and a split-out's parent are told "
                      "by their moves", text)

    # A feed is labelled from the live build's foldRound, read from its commit's params.hpp, not a hand copy of it
    # (watch.FEED_FROM): tuning foldRound would otherwise label online rows from the old round.
    def test_feeds_are_labelled_from_the_live_builds_fold_round(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            state, bots = Path(directory), Path(directory) / "bots"
            (bots / "bot").mkdir(parents=True)
            (bots / "bot/params.hpp").write_text("    int foldRound = 380;         // fold from here\n")
            git = ["git", "-c", "commit.gpgsign=false"]
            for command in (["init", "-q"], ["add", "."], ["commit", "-qm", "params"]):
                subprocess.run([*git, *command], cwd=bots, check=True, capture_output=True)
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=bots, capture_output=True, text=True).stdout.strip()
            (state / "publishing.json").write_text(json.dumps({"verified": {"submission_id": 8636, "commit": commit}}))
            self.assertEqual(online.live_fold_round(bots, state, 8636), 380)
            self.assertIsNone(online.live_fold_round(bots, state, 8637))  # another submission's build: unknown
            folder = state / "online-replays/8636"
            folder.mkdir(parents=True)
            replay = repo / "tools/testdata/queen-403920.replay"
            (folder / "index.json").write_text(json.dumps({"games": [{"id": 1, "side": "A", "replay": str(replay)}]}))
            runs, real = [], subprocess.run

            def counted(command, *args, **kwargs):
                runs.append(command)
                return real(command, *args, **kwargs)

            with patch.object(online, "live_fold_round", return_value=380), \
                    patch.object(online.subprocess, "run", counted):
                online.refresh_dooms(repo, state, 8636)
            watched = [c for c in runs if "tools/watch.py" in " ".join(map(str, c))]
            self.assertEqual(watched[0][watched[0].index("--feed-from") + 1], "380")
            self.assertIn("doom", json.loads((folder / "index.json").read_text())["games"][0])

    # Regression (review of 2026-09-28): a replay the doom tool failed on once was never read again, and health
    # alerted on it forever. It is tried on DOOM_TRIES cycles, then given up; a new tool version tries it afresh.
    def test_a_failing_replay_is_retried_on_later_cycles_then_given_up(self):
        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/8636"
            folder.mkdir(parents=True)
            (folder / "bad.replay").write_bytes(b"not a replay")
            index = folder / "index.json"
            index.write_text(json.dumps({"games": [{"id": 1, "side": "A", "replay": str(folder / "bad.replay")}]}))
            runs = []
            real = subprocess.run

            def counted(*args, **kwargs):
                runs.append(args)
                return real(*args, **kwargs)

            with patch.object(online.subprocess, "run", counted):
                for _ in range(online.DOOM_TRIES + 1):
                    online.refresh_dooms(repo, state, 8636)
            failed = json.loads(index.read_text())["games"][0]["doom_failed"]
            self.assertEqual((len(runs), failed["tries"]), (online.DOOM_TRIES, online.DOOM_TRIES))
            self.assertIn("unreadable", failed["error"])  # the tool's own words
            saved = json.loads(index.read_text())
            saved["games"][0]["doom_failed"]["version"] = -1  # an older tool's give-up
            index.write_text(json.dumps(saved))
            with patch.object(online.subprocess, "run", counted):
                online.refresh_dooms(repo, state, 8636)
            self.assertEqual(len(runs), online.DOOM_TRIES + 1)


# Three real games of submission 9553 on Default (map 4), after the site began swapping the teams' start tiles
# (online.LAYOUT_CUTOFF), with their index rows (summaries cut to map and result): 447294 as side A on the file's
# layout, 451387 as side B and 451633 (a dev game) as side A, both on the swapped one.
LAYOUT_ROWS = json.loads((TESTDATA / "online-layouts-sample.json").read_text())
LAYOUT_REPLAYS = {g: Path(__file__).resolve().parent.parent / f"tools/testdata/default-{g}.replay"
                  for g in (447294, 451387, 451633)}


class TestRams(unittest.TestCase):
    # M85: the online ram cells need each result-blind game's rams both ways. The real row and replay of game 447294
    # (9553 against team 34 on Default, our side A), a dev copy of it, and one whose replay is gone.
    def test_rams_are_added_both_ways_to_sample_games_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/9553"
            folder.mkdir(parents=True)
            row = {**LAYOUT_ROWS[0], "replay": str(LAYOUT_REPLAYS[447294])}
            games = [row, {**row, "id": 1, "opponent": 545}, {**row, "id": 2, "replay": str(folder / "gone.replay")}]
            (folder / "index.json").write_text(json.dumps({"games": games}))
            online.refresh_rams(state, 9553, {545})
            saved = {g["id"]: g for g in json.loads((folder / "index.json").read_text())["games"]}
            rams = saved[447294]["rams"]
            self.assertEqual((len(rams["ours"]), len(rams["theirs"]), rams["version"]), (7, 5, online.RAM_VERSION))
            self.assertEqual(rams["ours"][0], [0, 0, 3, 2])  # tie, edge 0, swing +3, 2 steps
            self.assertNotIn("rams", saved[1])  # a dev game
            self.assertNotIn("rams", saved[2])
            with patch.object(online, "save") as save:  # nothing left to read: the index isn't rewritten
                online.refresh_rams(state, 9553, {545})
            save.assert_not_called()


class TestStarts(unittest.TestCase):
    def rows(self, **change) -> dict[int, dict]:
        return {g["id"]: {**g, **online.start_fields(LAYOUT_REPLAYS[g["id"]], g["side"]), **change}
                for g in LAYOUT_ROWS}

    def test_side_b_on_the_swapped_layout_starts_where_side_a_does_on_the_files(self):
        rows = self.rows()
        self.assertNotEqual(rows[447294]["layout"], rows[451387]["layout"])
        self.assertEqual(rows[451387]["layout"], rows[451633]["layout"])
        self.assertEqual(online.start_key(rows[447294]), online.start_key(rows[451387]))
        self.assertNotEqual(online.start_key(rows[447294]), online.start_key(rows[451633]))  # side A both times

    def test_versions_pair_on_where_we_started_not_on_the_side(self):
        # 9553's alert against 9347 paired side A on one layout with side A on the other (28 pairs by side, 19 by
        # start). With one opponent version for all three, 447294 pairs with 451387 (side B), not 451633 (side A).
        rows = self.rows(opponent=34, opponent_submission_id=8926)
        compare = online.compare_versions(9347, [rows[447294]], [rows[451633], rows[451387]])
        self.assertEqual((compare["pairs"], compare["unpairable"]), (1, {"before": 0, "after": 0}))
        self.assertEqual(compare["worse_games"], [451387])  # 447294 won as A, 451387 lost as B; 451633 won as A
        by_side = online.compare_versions(9347, [{k: v for k, v in rows[447294].items() if k != "start"}],
                                          [{k: v for k, v in rows[451633].items() if k != "start"}])
        self.assertEqual((by_side["pairs"], by_side["unpairable"]), (0, {"before": 1, "after": 1}))
        # Before the cutoff a game without a replay read still pairs by side, with another such game only.
        early = lambda g: {**{k: v for k, v in g.items() if k != "start"}, "requested_at": "2026-09-28T01:00:00Z"}
        self.assertEqual(online.start_key(early(rows[451633])), "side A")
        self.assertEqual(online.compare_versions(9347, [early(rows[447294])], [early(rows[451633])])["pairs"], 1)
        self.assertEqual(online.compare_versions(9347, [early(rows[447294])], [rows[451633]])["pairs"], 0)
        stats = online.per_map_stats([rows[447294], rows[451387], rows[451633]])
        self.assertEqual(sorted(r["sides"] for r in stats if r["games"] == 2), [{"A": 1, "B": 1}])

    def test_the_sweep_replays_a_map_until_its_start_is_paired_or_asked_for_once_a_start(self):
        rows = self.rows(opponent=2, opponent_submission_id=8926)
        teams = [{"id": 2, "hasBot": True, "elo": 1700}, {"id": 3, "hasBot": True, "elo": 1500}]
        maps = [{"id": 4, "active": True}]
        now = datetime.fromisoformat("2026-09-28T05:00:00+00:00").timestamp()
        pick = lambda asked, games: online.choose_match(teams, maps, 157, 1500, set(), asked, 9695, now, games,
                                                        [rows[447294]])
        self.assertEqual(pick([], [])["teamId"], 2)
        asked = [{"at": now - 900, "games": 1, "opponent": 2, "submission_id": 9695, "map_ids": [4]}]
        self.assertEqual(pick(asked, [])["teamId"], 3)  # one start, asked for once
        other = {**rows[451633], "submission_id": 9695}
        self.assertEqual(pick([], [other])["teamId"], 2)  # played, but from the other start
        self.assertEqual(pick([], [{**rows[451387], "submission_id": 9695}])["teamId"], 3)  # paired

    def test_starts_are_added_to_saved_games_and_an_unreadable_replay_is_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            folder = state / "online-replays/9553"
            folder.mkdir(parents=True)
            (folder / "1.replay").write_bytes(b"not a replay")
            games = [{**LAYOUT_ROWS[0], "replay": str(LAYOUT_REPLAYS[447294])},
                     {**LAYOUT_ROWS[1], "id": 1, "replay": str(folder / "1.replay")},
                     {**LAYOUT_ROWS[2], "replay": str(folder / "gone.replay")}]
            (folder / "index.json").write_text(json.dumps({"games": games}))
            online.refresh_starts(state, [9553])
            saved = json.loads((folder / "index.json").read_text())["games"]
            self.assertEqual(saved[0]["start"], online.start_fields(LAYOUT_REPLAYS[447294], "A")["start"])
            self.assertEqual([g.get("start_failed") for g in saved], [None, True, None])
            self.assertNotIn("start", saved[2])  # no replay: unpairable after the cutoff
            self.assertIsNone(online.start_key(saved[2]))

    def test_a_layout_not_seen_before_alerts_once(self):
        rows = self.rows()
        known, lines = online.layout_news(None, [rows[447294]])
        self.assertEqual(lines, ["online: map layouts now recorded for 1 maps (1 layouts; more than one on maps "
                                 "none); a new one alerts (MISSES 72)"])
        known, lines = online.layout_news(known, [rows[447294], rows[451633], rows[451387]])
        self.assertEqual(len(lines), 1)
        self.assertIn("map 4 is played in a layout not seen before", lines[0])
        self.assertEqual(online.layout_news(known, list(rows.values())), (known, []))

    # A first cycle whose games carry no layout keeps the record unset, so the next cycle's still reads as the first and
    # gives the one summary line, not one alert per map.
    def test_a_cycle_without_layouts_leaves_the_record_unset(self):
        rows = self.rows()
        known, lines = online.layout_news(None, [{k: v for k, v in rows[447294].items() if k != "layout"}])
        self.assertEqual((known, lines), (None, []))
        _, lines = online.layout_news(known, list(rows.values()))
        self.assertEqual(len(lines), 1)
        self.assertIn("map layouts now recorded", lines[0])

    def test_an_alerted_comparison_holds_its_versions_replays(self):
        worse = {"previous": 9347, "pairs": 18, "before": 0.67, "after": 0.39, "better": 1, "worse": 6}
        held = online.hold_replays(None, worse, 9553, 1000.0)
        self.assertEqual(held, {"versions": [9347, 9553], "at": 1000.0})
        level = {**worse, "after": 0.67}
        self.assertEqual(online.hold_replays(held, level, 9695, 1000.0 + online.prune.HOLD_S - 1), held)
        self.assertIsNone(online.hold_replays(held, level, 9695, 1000.0 + online.prune.HOLD_S))


# MISSES 77: at about 19:30 AEST on 2026-09-28 the site dropped submissionAId, submissionBId, replayKey and requestedBy
# from GET /battles/:id, and match_for_team (which wanted replayKey) turned every finished game away unrecorded.
class TestSubmissionsUnnamed(unittest.TestCase):
    def collect(self, state: Path, details: list[dict], replay: Path, dev: set[int] = frozenset(), **patches):
        from subprocess import CompletedProcess
        summary = json.dumps({"checks": {"ok": True}, "result": {"winner": "A"}})
        def get(path):  # a game's own record: its battle's match, as record_475477 is 475476's
            gid = int(path.rsplit("/", 1)[1])
            detail = next(d for d in details if gid in {g["id"] for g in d["games"]})
            return {**detail, "match": {**detail["match"], "id": gid}}
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(patch.object(online, name, value))
            reads = stack.enter_context(patch.object(online.ladder, "get", side_effect=get))
            stack.enter_context(patch.object(online.ladder, "download", return_value=replay.read_bytes()))
            stack.enter_context(patch.object(online.time, "sleep"))
            stack.enter_context(patch.object(online.subprocess, "run",
                                             return_value=CompletedProcess([], 0, summary)))
            maps = {"Queen Of Spades": 7, "Schooltime": 9, "Trophy": 11, "Default": 4}
            games = online.collect_games(state, state, 157, 9918, details, dev, maps)
        return games, reads

    @staticmethod
    def ours(state: Path, sid: int) -> None:  # an older version of ours the loop has a folder for
        (state / f"online-replays/{sid}").mkdir(parents=True)
        online.save(state / f"online-replays/{sid}/index.json", {"games": []})

    def test_a_ladder_game_is_filed_under_the_submission_its_replay_names(self):
        # The real new-shape sweep of WCTW (team 37); the replay names 8473 on side A, as the site names ours.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.ours(state, 8473)
            games, _ = self.collect(state, [NEW_API["detail_475476"]], REPLAYS / "portals-381305.replay")
            saved = json.loads((state / "online-replays/8473/index.json").read_text())["games"]
            self.assertEqual([g["id"] for g in saved], [475476, 475477, 475478])
            self.assertTrue(all(g["submission_from"] == "replay" and g["opponent"] == 37
                                and g["opponent_submission_id"] is None for g in saved))
            self.assertTrue(all(Path(g["replay"]).parent.name == "8473" and Path(g["replay"]).is_file()
                                for g in saved))
            self.assertEqual(list((state / "online-replays/incoming").iterdir()), [])
            self.assertEqual(games, [])  # none is the live 9918's: its replay said 8473
            self.assertEqual(online.unrecorded_games(NEW_API["battles"], {475476: NEW_API["detail_475476"]},
                                                     online.recorded_ids(state)), None)
            # Side B: the replay names ours on B (queen-95037: '' and '3182').
            self.assertEqual(online.replay_submission(REPLAYS / "queen-95037.replay", "B", {3182}), 3182)
            self.assertIsNone(online.replay_submission(REPLAYS / "queen-95037.replay", "A", {3182}))
            # A bot name that isn't one of our submissions, or isn't ASCII digits, is never ours.
            self.assertIsNone(online.replay_submission(REPLAYS / "queen-95037.replay", "B", {9918}))
            import replays
            with patch.object(replays, "load_replay", return_value=type("R", (), {"botA": "\u00b2", "botB": ""})()):
                self.assertIsNone(online.replay_submission(REPLAYS / "queen-95037.replay", "A", {2}))

    def test_a_dev_result_is_filed_under_our_request_rows_submission_without_a_call(self):
        # The real new-shape battle against dev test 2 (752), which the loop requested as 9918.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            detail = NEW_API["detail_475263"]
            online.save(state / "match-requests.json", [{"at": 1.0, "games": 10, "opponent": 752, "dev": True,
                                                          "submission_id": 9918, "status": "requested",
                                                          "ids": list(range(475263, 475273))}])
            _, reads = self.collect(state, [detail], REPLAYS / "portals-381305.replay", {752},
                                    DEV_REPLAYS=0, DEV_LOSS_REPLAYS=0)
            self.assertEqual(reads.call_count, 0)
            results = json.loads((state / "online-replays/9918/index.json").read_text())["results"]
            self.assertEqual([(r["id"], r["submission_from"], r["winner"], r["opponent_submission_id"])
                              for r in results],
                             [(g["id"], "request", "A", None) for g in detail["games"]])
            # Without a request row the game's record is read and its replay names the owner.
            with tempfile.TemporaryDirectory() as other:
                self.ours(Path(other), 8473)
                _, reads = self.collect(Path(other), [detail], REPLAYS / "portals-381305.replay", {752},
                                        DEV_REPLAYS=0, DEV_LOSS_REPLAYS=0)
                self.assertEqual(reads.call_count, 3)
                results = json.loads((Path(other) / "online-replays/8473/index.json").read_text())["results"]
                self.assertEqual({r["submission_from"] for r in results}, {"replay"})
                self.assertEqual(list((Path(other) / "online-replays/incoming").iterdir()), [])

    def test_a_game_nothing_places_is_retried_as_a_bad_one(self):
        # A replay naming neither side (trophy-155732: '' and ''), no request row, no field: retried, never guessed.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.collect(state, [NEW_API["detail_475476"]], REPLAYS / "trophy-155732.replay")
            invalid = json.loads((state / "online-replays/9918/index.json").read_text())["invalid"]
            self.assertEqual(sorted(invalid), ["475476", "475477", "475478"])
            self.assertIn("no source names our submission", invalid["475476"]["reason"])
            missed = online.unrecorded_games(NEW_API["battles"], {475476: NEW_API["detail_475476"]},
                                             online.recorded_ids(state))
            self.assertEqual(missed, {"games": 3, "oldest": "2026-09-28T09:40:52.844Z", "example": 475476})
        # A replay naming a submission that isn't ours (8473, unknown here) is no source either.
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.collect(state, [NEW_API["detail_475476"]], REPLAYS / "portals-381305.replay")
            invalid = json.loads((state / "online-replays/9918/index.json").read_text())["invalid"]
            self.assertEqual(sorted(invalid), ["475476", "475477", "475478"])
            self.assertFalse((state / "online-replays/8473").exists())

    def test_a_game_without_its_replay_yet_waits_without_an_attempt(self):
        detail = NEW_API["detail_475476"]
        early = {**detail, "games": [{**g, "hasReplay": False} for g in detail["games"]]}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.ours(state, 8473)
            _, reads = self.collect(state, [early], REPLAYS / "portals-381305.replay")
            self.assertEqual(reads.call_count, 0)
            self.assertFalse((state / "online-replays/9918/index.json").exists())  # no attempt recorded
            self.collect(state, [detail], REPLAYS / "portals-381305.replay")
            saved = json.loads((state / "online-replays/8473/index.json").read_text())["games"]
            self.assertEqual(len(saved), 3)

    def test_a_missing_field_alerts_once(self):
        details = [NEW_API["detail_475476"], NEW_API["detail_475263"], NEW_API["record_475477"]]
        self.assertEqual(online.missing_fields(details), [])  # the new shape has every field the cycle reads
        gone = {**details[0], "match": {k: v for k, v in details[0]["match"].items() if k != "mapId"},
                "games": [{k: v for k, v in g.items() if k != "winner"} for g in details[0]["games"]]}
        missing = online.missing_fields([gone, details[1]])
        self.assertEqual(missing, ["games.winner", "match.mapId"])
        record, news = online.api_news(None, missing, 100.0)
        self.assertEqual((record, len(news)), ({"fields": missing, "since": 100.0}, 1))
        self.assertIn("games.winner, match.mapId", news[0])
        self.assertEqual(online.api_news(record, missing, 200.0), (record, []))
        self.assertEqual(online.api_news(record, [], 300.0), (None, []))

    def test_unknown_opponent_versions_never_pair_and_dev_shares_keep_their_history(self):
        game = lambda gid, sub, winner: {"id": gid, "opponent": 11, "opponent_submission_id": sub, "map_id": 3,
                                         "side": "A", "requested_at": f"2026-09-28T0{gid}:00:00Z",
                                         "summary": {"result": {"winner": winner}}}
        compare = online.compare_versions(9762, [game(1, 3944, "A"), game(2, None, "A")], [game(3, None, "B")])
        self.assertEqual((compare["pairs"], compare["unknown_opponent"]), (0, {"before": 1, "after": 1}))
        # 200 wins against a dev team's named submission, then 3 unnamed games: its share stays small instead of
        # restarting at 1 as though a new dev bot had arrived.
        wins = [{"id": n, "opponent": 752, "opponent_submission_id": 4525, "side": "A", "winner": "A",
                 "requested_at": f"2026-09-27T{n // 60:02d}:{n % 60:02d}:00Z"} for n in range(200)]
        unnamed = [{**w, "id": 1000 + n, "opponent_submission_id": None, "requested_at": f"2026-09-28T10:0{n}:00Z"}
                   for n, w in enumerate(wins[:3])]
        played = {752: {9762: wins, 9918: unnamed}}
        team = online.dev_report(Path("/nonexistent"), [{"id": 752, "name": "dev test 2", "elo": 1709}], 9918, [],
                                 played)[0]
        self.assertEqual((team["dev_submission_id"], team["weight_games"], team["games"]), (None, 200, 3))
        self.assertLess(team["weight"], 0.05)
        self.assertEqual([r["dev_submission_id"] for r in team["records"]], [4525, None])
        self.assertEqual(team["compare"]["shared"], [])
        self.assertIn("vs dev submission unknown", online.record_line(team["records"][-1]))
        old, new = {"team": {"id": 752}, "records": team["records"][:1]}, {"team": {"id": 752, "name": "x", "elo": 1},
                                                                           "records": team["records"]}
        self.assertEqual(online.benchmark_news(old, new), [])  # an unnamed record is no new dev bot


UNRECORDED = json.loads((Path(__file__).parent / "testdata/online-unrecorded-sample.json").read_text())


class TestUnrecordedGames(unittest.TestCase):
    def test_a_battle_that_leaves_the_list_unrecorded_is_still_read_by_id(self):
        # Before (MISSES 83): only our newest BATTLE_WINDOW battles were read, newest first within the replay budget,
        # so about 270 requested ladder games of 2026-09-26/27 left the list unrecorded, and 474882 (41st of 100 at
        # 22:05 on 2026-09-28) was on its way. The real battle, still unrecorded, and its request row.
        detail, row = UNRECORDED["detail_474882"], UNRECORDED["request_474882"]
        seen = []
        def collect(repo, state, team, sid, details, dev, maps):
            seen.append([d["match"]["id"] for d in details])
            return []
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            # Listed and unrecorded: read, alerted and carried.
            report, _ = run_cycle(state, LEADERBOARD, battles=[{"id": 474882, "at": "t"}],
                                  details={474882: detail}, collect=collect)
            self.assertEqual(list(report["behind"]), ["474882"])
            self.assertEqual(report["unrecorded"]["example"], 474882)
            # Off the list: still read by id, still alerted.
            report, _ = run_cycle(state, LEADERBOARD, now=10000.0 + online.DEV_EVERY,
                                  details={474882: detail}, collect=collect)
            self.assertEqual(seen[-1], [474882])
            self.assertEqual(report["unrecorded"]["games"], 1)
            # Carried nowhere, the request row alone brings it back.
            online.save(state / "online.json", {**report, "behind": {}})
            online.save(state / "match-requests.json", [row])
            report, _ = run_cycle(state, LEADERBOARD, now=10000.0 + 2 * online.DEV_EVERY,
                                  details={474882: detail}, collect=collect)
            self.assertEqual(seen[-1], [474882])
            # Listed battles are read first, then at most CARRIED_PER_CYCLE carried ones, oldest first seen first.
            carried = {str(1000 + k): 5000.0 - k for k in range(online.CARRIED_PER_CYCLE + 1)}
            online.save(state / "online.json", {**report, "behind": carried})
            report, _ = run_cycle(state, LEADERBOARD, now=10000.0 + 3 * online.DEV_EVERY,
                                  battles=[{"id": 474882, "at": "t"}], collect=collect,
                                  details={b: {**detail, "match": {**detail["match"], "id": b}}
                                           for b in [474882, *map(int, carried)]})
            self.assertEqual(seen[-1], [474882, *range(1000 + online.CARRIED_PER_CYCLE, 1000, -1)])
            self.assertIn("1000", report["behind"])  # past the cap: next cycle
            # Still unrecorded BEHIND_S after it was first seen: given up with an alert; a battle whose game was
            # cancelled is closed once read. Neither is read again, though their request rows still name them.
            cancelled = {"match": {"id": 5}, "games": [{"id": 5, "status": "cancelled"}]}
            online.save(state / "online.json", {**report, "behind": {"474882": 10000.0}})
            online.save(state / "match-requests.json",
                        [row, {**row, "ids": [5], "at": 10000.0 + online.BEHIND_S - 100}])
            report, _ = run_cycle(state, LEADERBOARD, now=10000.0 + online.BEHIND_S, collect=collect,
                                  details={474882: detail, 5: cancelled})
            self.assertEqual(seen[-1], [474882, 5])
            self.assertEqual({b: c["why"] for b, c in report["closed"].items()},
                             {"474882": "given up", "5": "unfinished"})
            self.assertEqual(report["behind"], {})
            self.assertIn("battle 474882 still has unrecorded games",
                          (state / "host-alerts.log").read_text())
            run_cycle(state, LEADERBOARD, now=10000.0 + online.BEHIND_S + online.DEV_EVERY, collect=collect,
                      details={474882: detail, 5: cancelled})
            self.assertEqual(seen[-1], [])

    def test_a_drawn_game_is_recorded(self):
        # Before (MISSES 83): a draw's status is "draw", not "completed", so game 411875 (and every draw: none of
        # 17,054 saved rows) was skipped unrecorded, and unrecorded_games didn't count it either.
        detail, record = UNRECORDED["detail_411867"], UNRECORDED["record_411875"]
        def get(path):
            gid = int(path.rsplit("/", 1)[1])
            return record if gid == 411875 else {**detail, "match": {**detail["match"], "id": gid}}
        from subprocess import CompletedProcess
        summary = json.dumps({"checks": {"ok": True}, "result": {"winner": None}})
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            online.save(state / "match-requests.json", [UNRECORDED["request_411867"]])
            self.assertEqual(online.unrecorded_games([], {411867: detail}, set())["games"], 2)
            with patch.object(online.ladder, "get", side_effect=get), \
                    patch.object(online.ladder, "download",
                                 return_value=(REPLAYS / "trophy-155732.replay").read_bytes()), \
                    patch.object(online.time, "sleep"), \
                    patch.object(online.subprocess, "run", return_value=CompletedProcess([], 0, summary)):
                online.collect_games(state, state, 157, 9918, [detail], set(), {})
            saved = json.loads((state / "online-replays/9038/index.json").read_text())["games"]
            self.assertEqual([(g["id"], g.get("api_winner")) for g in saved], [(411875, None), (411876, "a")])
            self.assertEqual(online.game_score(saved[0]), 0.5)
            self.assertIsNone(online.unrecorded_games([], {411867: detail}, online.recorded_ids(state)))


# E0350's champion (v0070, submission 11376) builds the same code but the sonar key as v0069 (E0348, 11349), which
# changed play from v0068 (11259): loop/testdata/play-group-sample.json, the live records of 2026-09-29 ~18:55.
class TestPlayGroup(unittest.TestCase):
    def test_a_same_code_build_reads_its_parents_games_and_a_changed_build_does_not(self):
        sample = json.loads((TESTDATA / "play-group-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample["results"]))
            (state / "uploads.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample["uploads"]))
            (state / "publishing.json").write_text(json.dumps({"verified": sample["verified"]}))
            for sid, index in sample["indexes"].items():
                (state / "online-replays" / sid).mkdir(parents=True)
                (state / "online-replays" / sid / "index.json").write_text(json.dumps(index))
            self.assertEqual(online.play_group(state, 11376), [11376, 11349])
            self.assertEqual(online.play_group(state, 11349), [11349])
            self.assertEqual(online.previous_version(state, 11376)[0], 11259)
            self.assertEqual(online.previous_version(state, 11349)[0], 11259)
            teams = {g["opponent"] for index in sample["indexes"].values() for g in online.all_games(index)}
            games = lambda sid: {v: sum(len(p.get(v, [])) for p in online.played_against(
                state, teams, sid, sample["indexes"][str(sid)]["games"]).values()) for v in (11376, 11349, 11259)}
            self.assertEqual(games(11376), {11376: 11 + 46, 11349: 0, 11259: 185})
            self.assertEqual(games(11349), {11376: 0, 11349: 46, 11259: 185})

    def test_a_corrupt_uploads_line_is_skipped_and_alerted_once_not_raised(self):
        sample = json.loads((TESTDATA / "play-group-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            lines = [json.dumps(r) for r in sample["uploads"]]
            lines.insert(1, '{"submission_id": 1')  # a torn write, not the last line
            (state / "uploads.jsonl").write_text("\n".join(lines) + "\n")
            (state / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample["results"]))
            (state / "publishing.json").write_text(json.dumps({"verified": sample["verified"]}))
            self.assertEqual(online.play_group(state, 11376), [11376, 11349])
            online.versions_checked(state)
            alerts = (state / "host-alerts.log").read_text().splitlines()
            self.assertEqual(len(alerts), 1)
            self.assertIn("line(s) 2 ", alerts[0])


# sig, 1 Oct: "stop queueing any more games ill do them all myself": while ONLINE_PAUSED exists, dev_poll requests
# nothing (it returns before reading anything).
class OnlinePaused(unittest.TestCase):
    def test_dev_poll_does_nothing_while_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / online.PAUSED).write_text("")
            self.assertIsNone(online.dev_poll(state))
            self.assertFalse((state / "dev-poll.json").exists())


if __name__ == "__main__":
    unittest.main()
