"""Replay audit scheduling and application, without launching a model."""

import testhome  # before anything that reads live paths
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import audit
import online

REAL_FRESH_LINE = audit.meta.fresh_line


# Most tests here patch audit's functions, which a fresh process wouldn't see: collect's `audit.py apply`
# (meta.fresh_line) runs its in-process fallback, audit.applied_line, instead. test_the_fresh_process_applies_it
# runs the real one.
def in_process(script: str, args: list[str], outcomes: tuple[str, ...], fallback,
               repo: Path | None = None) -> str:
    return fallback()


def setUpModule() -> None:
    patcher = patch.object(audit.meta, "fresh_line", in_process)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def results(root: Path, n: int, kept: str | None = None) -> None:
    rows = [{"id": f"E{i:04d}", "verdict": "kept" if f"E{i:04d}" == kept else "discarded"} for i in range(1, n + 1)]
    (root / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


class TestAudit(unittest.TestCase):
    def test_lists_our_losses_first_then_the_champions_lost_tests_then_the_leaders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results(root, 5, kept="E0004")
            for name in ("won", "lost", "loss", "leader"):
                (root / f"{name}.replay").write_text("x")
            (root / "online.json").write_text(json.dumps({"submission_id": 7, "games": [
                {"id": 2, "submission_id": 7, "side": "A", "opponent": "x", "replay": str(root / "won.replay"),
                 "summary": {"result": {"winner": "A"}}},
                {"id": 1, "submission_id": 7, "side": "B", "opponent": "y", "replay": str(root / "lost.replay"),
                 "summary": {"result": {"winner": "A"}}}]}))  # replay summaries name the winner "A"/"B"
            (root / "runs/E0004/losses").mkdir(parents=True)
            (root / "runs/E0004/losses/0003-gen_1-aB.replay").write_text("x")
            (root / "study-replays").mkdir()
            (root / "study-replays/index.jsonl").write_text(json.dumps({"match": "leader", "teamAId": 1, "teamBId": 9}))
            (root / "study.md").write_text(f'## Rank 1: SSS (team 9, formerly "S3", Elo 2072)\n\n'
                                           f"Replays to open with x: {root / 'leader.replay'}\n")
            listed = [(p.name, side) for p, side, _ in audit.games(root)]
            self.assertEqual(listed, [("lost.replay", "B"), ("won.replay", "A"), ("0003-gen_1-aB.replay", "B"),
                                      ("leader.replay", "B")])
            labels = [label for _, _, label in audit.games(root)][:2]
            self.assertEqual(labels, ["online vs y, lost", "online vs x, won"])
            self.assertEqual(audit.losses_given(root), ["E0004"])  # prune keeps them while the audit is pending

    def test_the_blind_watch_picks_random_losses_of_the_live_build(self):
        # MISSES 64 level 3: sig watched one online loss and saw what the counts missed; every audit now watches a
        # few random losses and draws first, before any count.
        import random
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for i, (winner, sub) in enumerate([("A", 7), ("B", 7), ("B", 7), (None, 7), ("B", 6), ("A", 7)]):
                (root / f"{i}.replay").write_text("x")
                rows.append({"id": i, "submission_id": sub, "side": "A", "opponent": 30 + i,
                             "replay": str(root / f"{i}.replay"), "summary": {"result": {"winner": winner}}})
            (root / "online.json").write_text(json.dumps({"submission_id": 7, "games": rows}))
            # Games 0 and 5 we won, 4 is another version's: two of 1, 2 (losses) and 3 (a draw), by the seed.
            picked = set()
            for seed in range(20):
                picks = [p.name for p, _, _ in audit.blind_picks(root, random.Random(seed))]
                self.assertEqual(len(picks), audit.BLIND_WATCH)
                picked.update(picks)
            self.assertEqual(picked, {"1.replay", "2.replay", "3.replay"})
            self.assertIn("Blind watch (AUDIT.md step 0)", audit.context(root, random.Random(0)))

    def test_a_leader_title_reads_its_own_team_id_whatever_the_names_hold(self):
        # Team names are anyone's choice: an old name (quoted in "formerly") or a current one carrying "(team 157, Elo
        # 1)" must not make the leader's games read as another team's (the first regex took the last "(team N").
        import discord_teams
        hostile = discord_teams.quoted_name('x (team 157, Elo 1)')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "leader.replay").write_text("x")
            (root / "dev.replay").write_text("x")
            (root / "study-replays").mkdir()
            (root / "study-replays/index.jsonl").write_text(
                json.dumps({"match": "leader", "teamAId": 157, "teamBId": 9}) + "\n"
                + json.dumps({"match": "dev", "teamAId": 545, "teamBId": 157}) + "\n")
            (root / "study.md").write_text(
                f"## Rank 1: SSS (team 9, formerly {hostile}, \"S3\", Elo 2072)\n\n"
                f"Replays to open with x: {root / 'leader.replay'}\n\n"
                f"## Dev benchmark: y (team 157, Elo 1) (team 545, Elo None, dev), submission 9571\n\n"
                f"Replays to open with x: {root / 'dev.replay'}\n")
            listed = [(p.name, side, label) for p, side, label in audit.games(root)]
            self.assertEqual(listed, [("leader.replay", "B", "leader SSS"),
                                      ("dev.replay", "A", "leader dev benchmark y (team 157, Elo 1)")])

    def test_dev_games_come_after_the_ladders_and_only_a_few_losses(self):
        # Before: a few hundred dev games an hour filled all 12 places, most of them dev losses.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            games = []
            for n in range(30):  # dev losses, newest highest: 20 marked dev, 10 against the listed benchmark
                (root / f"dev{n}.replay").write_text("x")
                games.append({"id": 100 + n, "submission_id": 7, "side": "A", "opponent": 545 if n < 10 else 752,
                              "replay": str(root / f"dev{n}.replay"), "summary": {"result": {"winner": "B"}},
                              **({} if n < 10 else {"dev": True})})
            for n, winner in enumerate("AB"):
                (root / f"ladder{n}.replay").write_text("x")
                games.append({"id": n + 1, "submission_id": 7, "side": "A", "opponent": 262,
                              "replay": str(root / f"ladder{n}.replay"), "summary": {"result": {"winner": winner}}})
            games.append({"id": 200, "submission_id": 7, "side": "A", "opponent": 752, "dev": True,
                          "replay": str(root / "pruned.replay"), "summary": {"result": {"winner": "B"}}})
            (root / "online.json").write_text(json.dumps({"submission_id": 7, "games": games,
                                                         "benchmark": {"team": {"id": 545}}}))
            listed = audit.games(root)  # the newest dev loss's file is gone, so it isn't listed
            self.assertEqual([(p.name, label) for p, _, label in listed],
                             [("ladder1.replay", "online vs 262, lost"), ("ladder0.replay", "online vs 262, won")]
                             + [(f"dev{n}.replay", "online vs dev team 545 (the dev benchmark), lost")
                                for n in range(9, 9 - audit.DEV_LOSSES, -1)])

    def test_the_dev_benchmarks_losses_come_before_newer_losses_to_weaker_dev_teams(self):
        # The live online.json at 14:40 on 2026-09-27 (submission 8023): the three newest dev losses were all to dev
        # test 2 (752), while the benchmark, dev test 1 (545), the one dev team near level with us, went unwatched.
        sample = (Path(__file__).parent / "testdata/online-dev-losses-sample.json").read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = json.loads(sample.replace("{home}", str(root)))
            for game in report["games"]:
                Path(game["replay"]).parent.mkdir(parents=True, exist_ok=True)
                Path(game["replay"]).write_text("x")
            (root / "online.json").write_text(json.dumps(report))
            # No team there is measured as beaten heavily (its dev rows predate their `lower`), so the benchmark's
            # losses fill every place.
            dev = [(p.stem, label) for p, _, label in audit.games(root) if "dev team" in label]
            self.assertEqual(dev, [(str(g), "online vs dev team 545 (the dev benchmark), lost")
                                   for g in (347667, 347666, 347487, 346723)])
            # With fewer benchmark losses than places, the other dev teams' newest fill the rest.
            report["games"] = [g for g in report["games"] if g["id"] != 347487 and g["id"] != 346723]
            (root / "online.json").write_text(json.dumps(report))
            dev = [p.stem for p, _, label in audit.games(root) if "dev team" in label]
            self.assertEqual(dev, ["347667", "347666", "347675", "347674"])

    def test_losses_to_teams_we_beat_heavily_come_first_then_the_benchmarks(self):
        # Before: the benchmark's losses took every place, so losses to teams we beat 96% (10 to dev test 8 in 230
        # games, most on Trauma) were never watched. Version 8023's real games (online-dev-caps-sample.json); the
        # results-only losses given a replay file, as online.collect_games now keeps them.
        sample = json.loads((Path(__file__).parent / "testdata/online-dev-caps-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            games = []
            for g in json.loads(json.dumps(sample["games"]).replace("{home}", str(root))):
                if "replay" not in g:
                    g = {**g, "replay": str(root / f"online-replays/8023/{g['id']}.replay"), "kept": "loss",
                         "summary": {"result": {"winner": g.pop("winner")}}}
                games.append(g)
            (root / "online-replays/8023").mkdir(parents=True)
            online.save(root / "online-replays/8023/index.json", {"games": games})
            teams = online.dev_report(root, sample["teams"], 8023, [])
            lost = [g for g in games if g["summary"]["result"]["winner"] != g["side"]]
            for g in lost:
                Path(g["replay"]).write_text("x")
            report = {"submission_id": 8023, "games": lost, "benchmark": {"team": {"id": 545}},
                      "dev": {"submission_id": 8023, "teams": teams}}
            (root / "online.json").write_text(json.dumps(report))
            newest = lambda team: sorted((g["id"] for g in lost if g["opponent"] == team), reverse=True)
            dev = [(p.stem, label) for p, _, label in audit.games(root) if "dev team" in label]
            kept = {g["id"] for g in lost if g.get("kept")}  # labelled so since MISSES 55
            mark = lambda g: "; replay kept because we lost" if g in kept else ""
            self.assertEqual(dev, [(str(g), f"online vs dev team 758 (a team we beat 96% over every version and "
                                             f"pool period{mark(g)}), lost")
                                   for g in newest(758)[:2]]
                             + [(str(g), f"online vs dev team 545 (the dev benchmark{mark(g)}), lost")
                                for g in newest(545)[:2]])
            # One heavy-team loss left: the benchmark fills the other place.
            report["games"] = [g for g in lost if g["opponent"] not in (758, 609)] + [
                g for g in lost if g["id"] == newest(758)[0]]
            (root / "online.json").write_text(json.dumps(report))
            dev = [p.stem for p, _, label in audit.games(root) if "dev team" in label]
            self.assertEqual(dev, [str(newest(758)[0])] + [str(g) for g in newest(545)[:3]])

    def test_a_team_we_beat_heavily_is_judged_by_its_lower_bound(self):
        # Before: dev_picks read a `heavy` flag that the old game cap cleared when this version's own games against a
        # team we beat heavily fell below DEV_HEAVY: those losses, the most surprising, went last. It reads the lower
        # bound of our score over the games that set the team's share (online.dev_report).
        reopened = {"id": 758, "weight_games": 200, "score": 0.957, "lower": 0.922}
        informative = {"id": 752, "weight_games": 100, "score": 0.82, "lower": 0.733}
        losses = [{"id": 9, "opponent": 545}, {"id": 8, "opponent": 752}, {"id": 7, "opponent": 545},
                  {"id": 6, "opponent": 758}, {"id": 5, "opponent": 545}, {"id": 4, "opponent": 758}]
        picks = [(g["id"], which) for g, which in audit.dev_picks(losses, [reopened, informative], 545)]
        heavy = "a team we beat 96% over every version and pool period"
        self.assertEqual(picks, [(6, heavy), (4, heavy), (9, "the dev benchmark"), (7, "the dev benchmark")])

    def test_saved_losses_are_labelled_by_the_rotation_opponent_they_were_lost_to(self):
        # A sample of E0102's real sprt.json and loss names: trial 44 (the map ending 184) was against the champion,
        # trial 3 (ending 143) against breeder.
        sample = json.loads((Path(__file__).parent / "testdata/sprt-E0102-sample.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results(root, 2, kept="E0002")
            (root / "runs/E0002/losses").mkdir(parents=True)
            for name in sample["losses"]:
                (root / "runs/E0002/losses" / name).write_text("x")
            no_sprt = {p.name: label for p, _, label in audit.games(root)}
            self.assertIn("previous champion or a rotation opponent", no_sprt["0006-gen_eval_111406143-aA.replay"])
            (root / "runs/E0002/sprt.json").write_text(json.dumps({k: sample[k] for k in ("rotation", "per_map")}))
            labels = {p.name: label for p, _, label in audit.games(root)}
            # An opponent mix's schedule gave trial 3 to its entry, which isn't in the rotation.
            entry = next(iter(audit.meta.MIX_OPPONENTS))
            schedule = [entry if i == 3 else audit.meta.trial_opponent(sample, i) for i in range(len(sample["per_map"]))]
            (root / "runs/E0002/sprt.json").write_text(json.dumps({"rotation": sample["rotation"],
                                                                   "per_map": sample["per_map"], "schedule": schedule}))
            mixed = {p.name: label for p, _, label in audit.games(root)}
        self.assertTrue(labels["0088-gen_eval_111406184-aA.replay"].endswith("to the previous champion"))
        self.assertIn("rotation's breeder", labels["0006-gen_eval_111406143-aA.replay"])
        self.assertEqual(len(labels), 10)
        self.assertTrue(mixed["0006-gen_eval_111406143-aA.replay"].endswith(
            f"to the opponent mix's {entry} (a gauntlet entry)"))
        self.assertEqual({k: v for k, v in mixed.items() if "143-" not in k},
                         {k: v for k, v in labels.items() if "143-" not in k})

    def test_the_dev_benchmark_block_is_labelled_as_its_own_and_our_block_is_skipped(self):
        # study.md's order (2026-09-26): ranks, then the dev benchmark's block (our games against it), then ours. The
        # benchmark's replays were labelled as rank 5's games for five audits running.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("r5", "bench", "us"):
                (root / f"{name}.replay").write_text("x")
            (root / "study.md").write_text(
                f"## Rank 5: Five (team 55, Elo 2038)\n\nReplays to open with x: {root / 'r5.replay'}\n"
                f"## Dev benchmark: dev test 1 :P (team 545, Elo 1970, dev), submission 4525\n\n"
                f"Replays to open with x: {root / 'bench.replay'}\n"
                f"## Us: submission 6824 (online games)\n\nReplays to open with x: {root / 'us.replay'}\n")
            labels = [(p.name, label) for p, _, label in audit.games(root)]
            self.assertEqual(labels, [("r5.replay", "leader Five"),
                                      ("bench.replay", "leader dev benchmark dev test 1 :P")])

    def test_context_gives_each_versions_full_record_and_marks_replays_kept_because_we_lost(self):
        # MISSES 55: audit 1790492423260536000 counted 8213's index `games` alone and read 107 dev losses of 178
        # (60%); its full record (online-8213-dev-index.json, a trimmed copy of the real index) is 177 of 750.
        index = json.loads((Path(__file__).parent / "testdata/online-8213-dev-index.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "online-replays/8213"
            folder.mkdir(parents=True)
            games = [{**g, "replay": str(folder / g["replay"])} for g in index["games"]]
            for g in games:
                Path(g["replay"]).write_text("x")
            (folder / "index.json").write_text(json.dumps({**index, "games": games}))
            (root / "online.json").write_text(json.dumps({"submission_id": 8213, "games": games}))
            text = audit.context(root)
            self.assertIn("- 8213 vs dev teams: every game 750, won 573, drew 0, lost 177 (23.6%); replays: 94 "
                          "result-blind (lost 23), 84 kept because we lost (for watching only); lost/games by team: "
                          "446 47/250, 545 70/170, 722 33/170, 752 27/160", text)
            self.assertNotIn("lost 107", text.split("Games (replay")[0])
            dev = [label for _, _, label in audit.games(root) if "dev team" in label]
            self.assertEqual(len(dev), audit.DEV_LOSSES)
            kept = {str(g["replay"]) for g in games if g.get("kept")}
            for path, _, label in audit.games(root):
                if "dev team" in label:
                    self.assertEqual("replay kept because we lost" in label, str(path) in kept)
            self.assertTrue(any("replay kept because we lost" in label for label in dev))

    def test_leader_games_an_earlier_audit_watched_are_marked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "leader.replay").write_text("x")
            (root / "study.md").write_text(f"## Rank 1: SSS (team 9, Elo 2072)\n\nReplays to open with x: {root / 'leader.replay'}\n")
            self.assertNotIn("earlier audit", audit.context(root))
            given = audit.leaders_given(root)
            record = {"pending": str(root / "w"), "leaders_given": given, "failures": ["x"] * 9}
            audit.save(root, record)
            (root / "w").mkdir()
            audit.failed(root, root / "w", "crashed")  # abandoned: it watched nothing
            self.assertNotIn("earlier audit", audit.context(root))
            (root / "w/findings.md").write_text("findings\n")
            (root / "audits").mkdir()
            audit.save(root, {**audit.read(root), "pending": str(root / "w"), "started_completed": 0,
                              "leaders_given": given})
            audit.apply(root, root / "w")
            text = audit.context(root)
            self.assertIn("(leader SSS; watched in an earlier audit)", text)
            self.assertIn("No leader game is new", text)

    def test_due_every_period_not_on_results_and_ideas_are_validated_before_queueing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tried = {"id": "I010", "category": "combat", "priority": 3, "title": "t", "hypothesis": "h", "status": "tried"}
            (root / "ideas.jsonl").write_text(json.dumps(tried) + "\n")
            (root / "study.md").write_text("# Leaders\nGaps unchanged: head-ons on longer enemies\n")
            results(root, 50)  # results alone never fire it (sig, 2026-09-29: every 12 hours ish)
            audit.save(root, {"completed": 0, "finished_at": time.time() - audit.AUDIT_EVERY_S + 60})
            self.assertIsNone(audit.prepare(root))
            audit.save(root, {"completed": 0, "finished_at": time.time() - audit.AUDIT_EVERY_S})
            work = audit.prepare(root)
            self.assertTrue((work / "context.md").exists())
            # The method starts from the leaders' gaps, and the worker can't read the state folder itself.
            self.assertEqual((work / "study.md").read_text(), (root / "study.md").read_text())
            self.assertIn("study.md in this folder", (work / "context.md").read_text())
            prompt = root / "worker-io/audits" / work.name / "PROMPT.md"  # out of its reach
            self.assertIn("One finding, one general rule", prompt.read_text())  # loop/GENERALISE.md, filled in
            self.assertIsNone(audit.prepare(root))  # one pending audit at a time
            self.assertIn("incomplete", audit.apply(root, work))
            good = {"category": "move", "priority": 2, "title": "explore when nothing is in view",
                    "hypothesis": "126659: dragon 13 circled a 2x2 for 47 rounds; 3 of 12 games show it. Options: (1) "
                    "explore; (2) farm the 2x2; (3) wall it for them. This is (1)."}
            (work / "findings.md").write_text("findings\n")
            (work / "ideas.jsonl").write_text(json.dumps(good) + "\n" + json.dumps({**good, "priority": 0}) + "\n")
            self.assertIn("I011", audit.apply(root, work))
            rows = [json.loads(line) for line in (root / "ideas.jsonl").read_text().splitlines()]
            self.assertEqual([(r["id"], r["source"]) for r in rows[1:]], [("I011", f"audit:{work.name}")])
            self.assertFalse(audit.due(root))  # finished just now
            audit.save(root, {**audit.read(root), "finished_at": time.time() - audit.AUDIT_EVERY_S})
            self.assertTrue(audit.due(root))
            # The next audit gets the earlier findings as a copy in its folder: it can't read other audit folders.
            later = audit.prepare(root)
            self.assertEqual((later / "earlier-findings.md").read_text(), "findings\n")
            self.assertIn("earlier-findings.md in this folder", (later / "context.md").read_text())

    def test_the_driver_launches_in_the_background_and_collects_when_the_worker_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            results(root, 5)
            with patch.object(audit.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
                self.assertIn("launched", audit.tick(root))
                with patch.object(audit, "alive", return_value=True):
                    self.assertIsNone(audit.tick(root))  # still running: never waits
                work = Path(audit.read(root)["pending"])
                with patch.object(audit, "alive", return_value=False):
                    self.assertIn("failed", audit.tick(root))  # exited without findings
                    self.assertIsNone(audit.tick(root))  # waits an hour before retrying
                    (work / "findings.md").write_text("ok\n")
                    with patch.object(audit.time, "time", return_value=audit.time.time() + audit.RETRY_S + 1):
                        self.assertIn("launched", audit.tick(root))
                    self.assertIn("applied", audit.tick(root))
            self.assertEqual(popen.call_count, 2)

    def test_a_relaunch_never_opens_what_the_worker_left_in_its_folder(self):
        # A failed audit's worker could leave work/session.json as a link to e.g. ~/.zshrc: the relaunch's
        # open("w") followed it and truncated the target. Now its stdin and output live in worker-io/.
        import os
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            results(root, 5)
            outside = root / "zshrc"
            outside.write_text("keep me")
            with patch.object(audit.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
                self.assertIn("launched", audit.tick(root))
                work = Path(audit.read(root)["pending"])
                for name in ("session.json", "session.stderr"):
                    (work / name).unlink(missing_ok=True)
                    (work / name).symlink_to(outside)
                os.mkfifo(work / "PROMPT.md")
                with patch.object(audit, "alive", return_value=False):
                    self.assertIn("retrying", audit.tick(root))
                    os.chmod(work, 0o000)
                    if hasattr(os, "chflags"):
                        os.chflags(work, stat.UF_IMMUTABLE)
                    try:
                        with patch.object(audit.time, "time", return_value=audit.time.time() + audit.RETRY_S + 1):
                            self.assertIn("launched", audit.tick(root))
                    finally:
                        if hasattr(os, "chflags"):
                            os.chflags(work, 0)
                        os.chmod(work, 0o755)
            self.assertEqual(outside.read_text(), "keep me")
            folder = root / "worker-io/audits" / work.name
            self.assertEqual(popen.call_args.kwargs["cwd"], folder)
            command = popen.call_args.args[0]
            self.assertEqual(command[:5], ["/bin/sh", "-c", 'cd "$1" || exit 1; shift; exec "$@"', "in_folder",
                                           str(work)])

    def test_an_audit_failing_twice_is_abandoned_and_a_fresh_one_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            results(root, 5)
            later = audit.time.time() + audit.RETRY_S + 1
            with patch.object(audit.subprocess, "Popen", return_value=Mock(pid=4242)), \
                    patch.object(audit, "alive", return_value=False):
                audit.tick(root)
                first = Path(audit.read(root)["pending"])
                (first / "ideas.jsonl").mkdir()  # unreadable, and findings.md never written
                self.assertIn("retrying", audit.tick(root))
                with patch.object(audit.time, "time", return_value=later):
                    self.assertIn("launched", audit.tick(root))
                    self.assertIn("abandoned", audit.tick(root))
                    self.assertIn("launched", audit.tick(root))
            record = audit.read(root)
            self.assertNotEqual(record["pending"], str(first))
            [abandoned] = record["abandoned"]
            self.assertEqual((abandoned["work"], len(abandoned["failures"])), (str(first), 2))


# The pid of a process that has exited and been reaped, so audit.alive sees it gone.
def dead_pid() -> int:
    done = subprocess.Popen(["true"])
    done.wait()
    return done.pid


GOOD = {"category": "move", "priority": 2, "title": "explore when nothing is in view",
        "hypothesis": "126659: dragon 13 circled a 2x2 for 47 rounds; 3 of 12 games show it. Options: (1) "
        "explore; (2) farm the 2x2; (3) wall it for them. This is (1)."}


# A finished background audit is applied by the upkeep tick (audit.apply_finished), not only at the driver's next
# claim, which can be an hour away with every lane busy; the tick never prepares or launches one.
class ApplyFinished(unittest.TestCase):
    def audit(self, root: Path, pid: int | None) -> Path:
        (root / "ideas.jsonl").write_text("")
        results(root, 5)
        work = audit.prepare(root)
        if pid:
            audit.save(root, {**audit.read(root), "pid": pid})
        (work / "findings.md").write_text("findings\n")
        (work / "ideas.jsonl").write_text(json.dumps(GOOD) + "\n")
        return work

    def ideas(self, root: Path) -> list[str]:
        return [json.loads(line)["id"] for line in (root / "ideas.jsonl").read_text().splitlines()]

    def test_a_finished_worker_is_applied_once_and_the_orchestrator_told(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.audit(root, dead_pid())
            with patch.object(audit.subprocess, "Popen", side_effect=AssertionError("launched")):
                message = audit.apply_finished(root)
                self.assertEqual(message, f"audit applied: 1 ideas ['I001'] ({work})")
                self.assertIsNone(audit.apply_finished(root))
            self.assertEqual(self.ideas(root), ["I001"])
            [line] = (root / "host-alerts.log").read_text().splitlines()
            self.assertTrue(line.endswith(f" {message}"))

    # A driver's upkeep runs for hours on the code it imported at start; the apply, which can spend one of the audit's
    # two launches, runs main's loop/audit.py in a fresh process instead (MISSES 39).
    def test_the_fresh_process_applies_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.audit(root, dead_pid())
            with patch.object(audit.meta, "fresh_line", REAL_FRESH_LINE), \
                    patch.object(audit.meta, "checkout_note", return_value=""):
                self.assertEqual(audit.apply_finished(root), f"audit applied: 1 ideas ['I001'] ({work})")
            self.assertEqual(self.ideas(root), ["I001"])

    def test_a_running_worker_and_a_native_audit_are_left_alone(self):
        for pid in (os.getpid(), None):  # alive; no pid: a native session does its own audit
            with self.subTest(pid=pid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.audit(root, pid)
                before = (root / "audit.json").read_text()
                with patch.object(audit.subprocess, "Popen", side_effect=AssertionError("launched")):
                    self.assertIsNone(audit.apply_finished(root))
                self.assertEqual((root / "audit.json").read_text(), before)
                self.assertEqual(self.ideas(root), [])
                self.assertFalse((root / "host-alerts.log").exists())

    def test_failed_and_abandoned_launches_reach_the_orchestrator_too(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            results(root, 5)
            with patch.object(audit.subprocess, "Popen", side_effect=OSError("no claude")):
                first = audit.tick(root)
                with patch.object(audit.time, "time", return_value=audit.time.time() + audit.RETRY_S + 1):
                    second = audit.tick(root)
            self.assertIn("retrying", first)
            self.assertIn("abandoned", second)
            lines = (root / "host-alerts.log").read_text().splitlines()
            self.assertEqual([line.split(" ", 2)[2] for line in lines], [first, second])

    # 2026-09-27: audit 1790479722912203000's worker stopped on the session limit and wrote no findings, so the reason
    # was only "findings.md is missing" and it counted; two such would have abandoned an audit whose task was fine.
    # testdata/session-limit-sample.json is a real session.json from that limit.
    def test_a_worker_a_quota_error_stopped_is_not_a_failed_launch(self):
        limit = (Path(__file__).parent / "testdata/session-limit-sample.json").read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.audit(root, None)
            (work / "findings.md").unlink()
            output = audit.meta.protected_io(root, "audits", work) / "session.json"
            for _ in range(audit.meta.MAX_LAUNCHES + 1):
                output.write_text(limit)
                audit.save(root, {**audit.read(root), "pid": dead_pid()})
                message = audit.apply_finished(root)
                self.assertIn("provider quota limit", message)
                self.assertIn("You've hit your session limit", message)
                record = audit.read(root)
                self.assertEqual((record["pending"], record.get("failures")), (str(work), None))
            # Relaunched at the reset the error names (5:20pm Sydney) plus the margin, within the hour (limit_retry).
            retry = record["retry_at"]
            self.assertIn(f"retried at {audit.time.strftime('%H:%M', audit.time.localtime(retry))} after the limit "
                          "resets", message)
            with patch.object(audit.subprocess, "Popen", return_value=Mock(pid=4242)) as popen:
                with patch.object(audit.time, "time", return_value=retry - 1):
                    self.assertIsNone(audit.tick(root))
                with patch.object(audit.time, "time", return_value=retry):
                    self.assertIn("launched", audit.tick(root))
            self.assertEqual(popen.call_count, 1)
            (work / "findings.md").write_text("findings\n")
            output.write_text(json.dumps({"type": "result", "result": "done"}))
            audit.save(root, {**audit.read(root), "pid": dead_pid()})
            self.assertEqual(audit.apply_finished(root), f"audit applied: 1 ideas ['I001'] ({work})")

    # The tick reads the record without the audit lock; a native `audited` landing before its apply used to leave
    # "the worker failed" on the next audit's record, one launch closer to abandoning it.
    def test_an_audit_applied_meanwhile_leaves_no_failure_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = self.audit(root, dead_pid())
            real = audit.meta.unlock
            def native_lands(folder):
                real(folder)
                audit.apply(root, work)
            with patch.object(audit.meta, "unlock", side_effect=native_lands):
                self.assertIn("no longer pending", audit.apply_finished(root))
            record = audit.read(root)
            self.assertFalse({"pending", "failures", "failed_at"} & record.keys())
            self.assertEqual(self.ideas(root), ["I001"])
            self.assertFalse((root / "host-alerts.log").exists())

    def test_a_due_audit_is_not_prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ideas.jsonl").write_text("")
            results(root, 5)
            self.assertIsNone(audit.apply_finished(root))
            self.assertFalse((root / "audits").exists())

    # Two callers seeing the same dead pid both used to apply it (the second then recorded a spurious failure).
    def test_while_one_caller_applies_the_others_do_nothing(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.audit(root, dead_pid())
            entered, release = threading.Event(), threading.Event()
            real = audit.apply
            def slow(state, work):
                entered.set()
                release.wait(10)
                return real(state, work)
            results_ = []
            with patch.object(audit, "apply", side_effect=slow) as apply, \
                    patch.object(audit.subprocess, "Popen", side_effect=AssertionError("launched")):
                first = threading.Thread(target=lambda: results_.append(audit.apply_finished(root)))
                first.start()
                self.assertTrue(entered.wait(10))
                self.assertIsNone(audit.apply_finished(root))
                self.assertIsNone(audit.tick(root))
                release.set()
                first.join(10)
            self.assertIn("audit applied", results_[0])
            self.assertEqual(apply.call_count, 1)
            self.assertEqual(self.ideas(root), ["I001"])
            self.assertNotIn("failures", audit.read(root))



class LiveBuild(unittest.TestCase):
    def test_names_the_live_submissions_commit_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.assertEqual(audit.live_build(state), [])  # nothing verified yet
            # v0001, the pre-loop baseline, is a permanent tag of this repo.
            (state / "publishing.json").write_text(json.dumps({"verified": {"commit": "c666f243f36a335b33b7e32e74878098753c830b", "submission_id": 4219}}))
            lines = audit.live_build(state)
            self.assertEqual(lines[0], "Our live submission 4219 is commit c666f24 (v0001); its online games in the list "
                                       "below are that build's.")
            # v0001 is far behind the champion: the audit is told the code it reads is newer, and what changed.
            self.assertIn("The champion code you read is newer", lines[1])
            self.assertTrue(len(lines) > 2)

if __name__ == "__main__":
    unittest.main()
