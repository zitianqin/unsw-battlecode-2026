import testhome  # before anything that reads live paths
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import cutoffs
import driver
import meta

HERE = Path(__file__).resolve().parent
# The champion's params.hpp at 02cebbd, the commit the 2026-09-27 cutoff inventory
# (~/.bc-loop/reports/2026-09-27-cutoff-inventory.md) read by hand.
PARAMS = (HERE / "testdata/cutoffs-params-02cebbd.hpp").read_text()
# Every params.hpp number that report's cutoff rows C1-C37 name: 74 fields.
REPORT_NAMES = set("""
attackMinGain beaconRound childLength crowdFreeDist crowdMemoryRounds crowdRadius doomedScore enemyHalfLife enemyPrior
escapeMinLength evenTradeMinEdge exploreFriendCap exploreRetarget exploreStride farmMinEndLength farmMinPearls
fastMaxGap fastMaxResets fastMinResets foldMergeRadius foldMinLength foldReach foldRound gossipCountdownMax gossipGainCap
gossipMaxAge headReachCapMargin huntMaxDist huntMinEdge hunterMaxLength kingMinLength kingPearlReach kingReportAge
localFoldArea maxRamSteps maxTargetCandidates maxTargetDist mergeMinUnits mergeRadius mergeRound minUnitsToTrade
otherTailMargin outnumberedAttackMinGain ownRamSteps pearlMemoryRounds probablePenalty protectMinLength raceMaxArea
raceRounds raceWallSeen ramGuardMaxEdge rememberedPenalty rescueFrom rescueSlots reserveMinArea riskPenalty
riskTradeMinGain roomMinLength sharedPearlPenalty shedMinLength shedReserve shedThreatSlack shortfallPerTile spaceCap
spaceCapPerLength spaceMargin spawnSlack splitAtLength splitEnemyHeadRadius splitParentSpace splitParentTerritory
splitChildSpace staleCap trappedTailSpace unseenShortfallPerTile visibleNeedCap""".split())


def names(found: list[cutoffs.Cutoff], kind: str) -> set[str]:
    return {c.name for c in found if c.kind == kind}


class Inventory(unittest.TestCase):
    def test_the_champions_params_match_the_hand_inventory_but_for_named_weights(self) -> None:
        found = names(cutoffs.inventory({"params.hpp": PARAMS}), "param")
        self.assertEqual(len(found), 74)
        # The name rule drops five the report counted as parts of a cutoff (the split-out's doomedScore, the flat
        # pearl-trust penalties, riskPenalty) and keeps three it called weights.
        self.assertEqual(REPORT_NAMES - found, {"doomedScore", "probablePenalty", "rememberedPenalty", "riskPenalty",
                                                "sharedPearlPenalty"})
        self.assertEqual(found - REPORT_NAMES, {"attackPerLength", "farmClusterRadius", "friendRepelRadius"})
        self.assertNotIn("debugIndicator", found)  # a bool switch is no number

    def test_a_game_quantity_against_a_bare_number_counts_and_index_checks_do_not(self) -> None:
        code = """
            // fold from round 350 is only a comment: w.round >= 350
            if (w.round >= 350 && 3 < length && d.dist <= 4.5 && team.size() > 12)
                fold();
            for (int dir = 0; dir < 4; dir++) {}
            if (dir < 4 || x < 0 || y == -1 || k != 2 || n >= 1) {}
            std::array<int, 4> moves{}; auto const bits = 1 << 29 | v >> 31;
            int const foldStart = 350;
            inline constexpr int KELP = -1;
            log("round > 400");
        """
        found = cutoffs.inventory({"fold.hpp": code})
        self.assertEqual({(c.name, c.value) for c in found if c.kind == "compare"},
                         {("w.round >=", "350"), ("length >", "3"), ("d.dist <=", "4.5"), ("team.size() >", "12"),
                          ("dir <", "4")})  # dir outside a for header is no loop variable
        self.assertEqual(names(found, "constant"), {"foldStart"})
        self.assertEqual(next(c.line for c in found if c.name == "w.round >="), 3)

    def test_a_literal_beside_a_product_is_a_coefficient_not_a_threshold_but_an_offset_is_a_threshold(self) -> None:
        code = """
            if (roundsLeft < 3 * path) {}
            if (length * 3 > room) {}
            if (spare / 4 >= need) {}
            if (x >= 5 + P.margin) {}
            if (y < 6 - lag) {}
        """
        found = cutoffs.inventory({"race.hpp": code})
        self.assertEqual({(c.name, c.value) for c in found if c.kind == "compare"},
                         {("x >=", "5"), ("y <", "6")})

    def test_a_long_identifier_unclosed_brackets_or_comments_are_read_in_linear_time(self) -> None:
        # A coder's commit can hold anything; a quadratic match would hang the trusted evaluate() (each of these took
        # a minute or more at 200k characters before).
        texts = {"identifier": "if (" + "a" * 200_000 + " >= 7) {}\n" + "b" * 200_000 + "\n",
                 "brackets": "if (w.round >= 7) {}\n" + "a[" * 100_000,
                 "comments": "if (w.round >= 7) {}\n" + "/*a" * 70_000,
                 "escaped quotes": "if (w.round >= 7) {}\n" + '"' + '\\"' * 100_000 + "\n",
                 "single quotes": "if (w.round >= 7) {}\n" + "'" + "\\'" * 100_000 + "\n"}
        for name, text in texts.items():
            start = time.monotonic()
            found = cutoffs.inventory({"big.hpp": text})
            self.assertLess(time.monotonic() - start, 1.0, name)
            self.assertEqual([c.value for c in found], ["7"], name)
        start = time.monotonic()
        found = cutoffs.inventory({"params.hpp": "\n" * 200_000 + "int foldRound = 397;\n"})
        self.assertLess(time.monotonic() - start, 1.0, "blank lines")
        self.assertEqual([c.value for c in found], ["397"])

    def test_the_toolkits_helper_is_not_read(self) -> None:
        self.assertEqual(cutoffs.inventory({"helper.hpp": "if (round >= 400) {}"}), [])


class Diff(unittest.TestCase):
    base = cutoffs.inventory({"params.hpp": "int foldRound = 397;\nint shedMinLength = 6;\n",
                              "fold.hpp": "if (w.round >= 350) {}\n"})

    def test_a_new_threshold_is_added_a_new_value_is_changed_and_moved_code_is_neither(self) -> None:
        head = cutoffs.inventory({"params.hpp": "int foldRound = 380;\nint shedMinLength = 6;\nint huntMaxDist = 6;\n",
                                  "feed.hpp": "\n\nif (w.round >= 350) {}\nif (length < 7) {}\n"})
        d = cutoffs.diff(self.base, head)
        self.assertEqual({c.name for c in d["added"]}, {"huntMaxDist", "length <"})
        self.assertEqual(d["removed"], [])
        self.assertEqual([(a.value, b.value) for a, b in d["changed"]], [("397", "380")])

    def test_a_threshold_replaced_by_a_formula_is_removed(self) -> None:
        head = cutoffs.inventory({"params.hpp": "int shedMinLength = 6;\ndouble foldSlack = 1.5;\n",
                                  "fold.hpp": "if (roundsLeft < P.foldSlack * pathToKing) {}\n"})
        d = cutoffs.diff(self.base, head)
        self.assertEqual({c.name for c in d["removed"]}, {"foldRound", "w.round >="})
        self.assertEqual({c.name for c in d["added"]}, {"foldSlack"})
        summary = cutoffs.summary(self.base, head)
        self.assertEqual((summary["count"], summary["base_count"], summary["n_added"], summary["n_removed"]),
                         (2, 3, 1, 2))
        self.assertEqual((summary["n_added_params"], summary["n_removed_params"]), (1, 1))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class Commits(unittest.TestCase):
    def test_two_commits_are_read_from_the_object_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q")
            (repo / "bot").mkdir()
            (repo / "bot/params.hpp").write_text("int foldRound = 397;\n")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            (repo / "bot/fold.hpp").write_text("if (w.round >= 350) {}\n")
            (repo / "bot/notes.txt").write_text("if (w.round >= 400) {}\n")  # not a source file
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "head")
            summary = cutoffs.summary_at(repo, base, "HEAD")
            self.assertEqual((summary["n_added"], summary["added"]), (1, ["fold.hpp:1 w.round >= 350"]))
            (repo / "bot/fold.hpp").write_text("if (w.round >= 350 && length < 7) {}\n")  # uncommitted
            working = cutoffs.inventory(cutoffs.read_dir(repo / "bot"))
            self.assertEqual(len(cutoffs.diff(cutoffs.inventory(cutoffs.read_rev(repo, base)), working)["added"]), 2)
            # A result's field never raises, even for a commit that doesn't exist.
            with unittest.mock.patch.object(driver, "REPO", repo):
                self.assertIn("error", driver.candidate_cutoffs(base, "0" * 40))


# REFLECT 2026-09-28 17:52 F2: work queued or claimed before a rule landed is swept by it. testdata/sweep-ideas-sample
# .jsonl holds six real ideas.jsonl rows of 28 Sep ~18:10 (histories dropped): I428, I574 and I238 have a bare
# threshold, I093 and I100 don't, and I657 was in flight as E0252 with one in its idea.
class Sweep(unittest.TestCase):
    def test_waiting_ideas_and_in_flight_candidates_are_swept_with_their_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, repo, clones = root / "state", root / "repo", root / "clones"
            (state / "runs").mkdir(parents=True)
            (state / "ideas.jsonl").write_text((HERE / "testdata/sweep-ideas-sample.jsonl").read_text())
            ideas = [json.loads(line) for line in (state / "ideas.jsonl").read_text().splitlines()]
            for tree in (repo, clones / "E0251"):
                (tree / "bot").mkdir(parents=True)
                (tree / "bot/params.hpp").write_text("int foldRound = 397;\n")
                git(tree, "init", "-q")
                git(tree, "add", ".")
                git(tree, "commit", "-qm", "base")
            base = git(repo, "rev-parse", "HEAD")
            (clones / "E0251/bot/params.hpp").write_text("int foldRound = 397;\nint portalResendRounds = 20;\n")
            git(clones / "E0251", "commit", "-qam", "coded, not yet recorded as a candidate")
            idea = {"id": "I631", "title": "t", "hypothesis": "h", "status": "trying:lane1"}
            (state / "ideas.jsonl").write_text((state / "ideas.jsonl").read_text() + json.dumps(idea) + "\n")
            rows = {"E0251": idea, "E0252": next(i for i in ideas if i["id"] == "I657"),
                    "E0189": {"id": "I097", "title": "an old run left unfinished", "hypothesis": "h"}}
            for eid, what in rows.items():
                (state / "runs" / eid).mkdir()
                (state / "runs" / eid / "experiment.json").write_text(json.dumps(
                    {"id": eid, "idea": what, "base": base, "candidate": "", "finished": 0.0}))
            lines = cutoffs.sweep(state, repo, clones)
        self.assertEqual([line.split(":")[0] for line in lines],
                         ["I428 (todo)", "I574 (todo)", "I238 (todo)", "E0251 (I631) in flight, its candidate adds 1 "
                          "cutoff(s)", "E0252 (I657) in flight, its idea", "E0252 (I657) in flight"])
        self.assertIn("E0251 (I631) in flight, its candidate adds 1 cutoff(s): params.hpp:2 portalResendRounds 20",
                      lines)
        self.assertIn("bare threshold '1152+ tiles'", lines[-2])
        self.assertIn("its cutoffs couldn't be read (RuntimeError", lines[-1])  # E0252's clone is missing

    # 29 Sep: E0345-E0350, rebased onto champion cfe3b39 while experiment.json kept the claim's base, were each said to
    # add cfe3b39's driftFactor. A rebased candidate is read against its merge-base with the champion.
    def test_a_candidate_rebased_since_its_claim_is_not_charged_with_the_champions_new_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, repo, clones = root / "state", root / "repo", root / "clones"
            (state / "runs/E0350").mkdir(parents=True)
            (repo / "bot").mkdir(parents=True)
            (repo / "bot/params.hpp").write_text("int foldRound = 397;\n")
            git(repo, "init", "-q")
            git(repo, "add", ".")
            git(repo, "commit", "-qm", "claim's base")
            claimed = git(repo, "rev-parse", "HEAD")
            (repo / "bot/params.hpp").write_text("int foldRound = 397;\ndouble driftFactor = 1.0;\n")
            git(repo, "commit", "-qam", "the next champion")
            git(repo, "branch", "loop/champion")
            git(root, "clone", "-q", str(repo), str(clones / "E0350"))
            (clones / "E0350/bot/params.hpp").write_text((repo / "bot/params.hpp").read_text() + "int searchDepth = 3;\n")
            git(clones / "E0350", "commit", "-qam", "coded on the new champion after a rebase")
            idea = {"id": "I966", "title": "t", "hypothesis": "h", "status": "trying:lane1"}
            (state / "ideas.jsonl").write_text(json.dumps(idea) + "\n")
            (state / "runs/E0350/experiment.json").write_text(json.dumps(
                {"id": "E0350", "idea": idea, "base": claimed, "candidate": "", "finished": 0.0}))
            lines = cutoffs.sweep(state, repo, clones)
            # The base is read from the main checkout when it has the commit (MISSES 17), the clone only otherwise.
            champion, coded = git(repo, "rev-parse", "loop/champion"), git(clones / "E0350", "rev-parse", "HEAD")
            self.assertEqual(cutoffs.own_base(repo, clones / "E0350", "HEAD", champion), (repo, champion))
            self.assertEqual(cutoffs.own_base(repo, clones / "E0350", "HEAD", coded), (clones / "E0350", coded))
        self.assertEqual(lines, ["E0350 (I966) in flight, its candidate adds 1 cutoff(s): params.hpp:3 searchDepth 3"])

    # The sweep crashed with "No module named 'home'" when run as `python -m loop.cutoffs` from the checkout (owed.md
    # 29 Sep 02:04).
    def test_the_sweep_runs_as_a_module_from_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "runs").mkdir()
            (Path(tmp) / "ideas.jsonl").write_text("")
            done = subprocess.run([sys.executable, "-m", "loop.cutoffs", "--sweep", "--state", tmp], cwd=HERE.parent,
                                  capture_output=True, text=True, env={**os.environ, "BC_LOOP_HOME": tmp})
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "nothing found"), done.stderr)


class Stats(unittest.TestCase):
    def row(self, eid: str, verdict: str, elo: float | None, added: int, removed: int, count: int,
            params: tuple[int, int] = (0, 0)) -> dict:
        return {"id": eid, "verdict": verdict, "candidate": "c" + eid, "sprt": {"elo": elo},
                "cutoffs": {"count": count, "base_count": count - added + removed, "n_added": added,
                            "n_removed": removed, "n_changed": 0, "n_added_params": params[0],
                            "n_removed_params": params[1], "added": [], "removed": [], "changed": []}}

    def test_results_group_by_removed_added_or_neither_and_the_champion_count_trends(self) -> None:
        rows = [self.row("E0001", "kept", 12.0, 0, 2, 40), self.row("E0002", "discarded", -8.0, 1, 0, 41),
                self.row("E0003", "kept", 6.0, 1, 1, 40), self.row("E0004", "promising", None, 0, 0, 40),
                {"id": "E0005", "verdict": "no code", "candidate": "", "sprt": {}},
                {"id": "E0006", "verdict": "discarded", "candidate": "f" * 40, "base": "e" * 40, "sprt": {}}]
        with tempfile.TemporaryDirectory() as tmp:
            git(Path(tmp), "init", "-q")
            lines = meta.cutoff_lines(rows, Path(tmp))
        text = "\n".join(lines)
        code = text[text.index("By code compares"):text.index("By params.hpp")]
        self.assertIn("- removed: 1 results, {'kept': 1}, mean Elo +12.0 (n 1)", code)
        self.assertIn("- added: 1 results, {'discarded': 1}, mean Elo -8.0 (n 1)", code)
        self.assertIn("- neither: 2 results, {'kept': 1, 'promising': 1}, mean Elo +6.0 (n 1)", code)
        self.assertIn("- n/a: 1 results whose commits couldn't be read", text)  # E0006, recorded before the field
        self.assertIn("by version (kept results): E0001 40, E0003 40", text)

    def test_new_params_are_counted_apart_from_code_and_the_leading_group_is_named_first(self) -> None:
        # E0002 adds two params and one compare; E0003 swaps a compare for a param (a formula's coefficient).
        rows = [self.row("E0001", "discarded", -3.0, 0, 1, 40), self.row("E0002", "kept", 9.0, 3, 0, 43, (2, 0)),
                self.row("E0003", "kept", 7.0, 1, 1, 43, (1, 0)), self.row("E0004", "kept", 5.0, 1, 0, 44, (1, 0))]
        text = "\n".join(meta.cutoff_lines(rows))
        code = text[text.index("By code compares"):text.index("By params.hpp")]
        params = text[text.index("By params.hpp"):]
        self.assertIn("- removed: 2 results, {'discarded': 1, 'kept': 1}", code)  # E0001, E0003
        self.assertIn("- added: 1 results, {'kept': 1}", code)  # E0002
        self.assertIn("- neither: 1 results", code)  # E0004's one new cutoff is a param
        self.assertIn("- added: 3 results, {'kept': 3}", params)
        self.assertIn("- removed: 0 results", params)
        # Added (1/1 kept) leads removed (1/2), so the line says added is ahead, never that removed is.
        self.assertIn("kept, added against removed: 1/1 against 1/2; luck alone puts added this far ahead", code)

    # A formula idea removes a cutoff and is kept for no loss (sig, 2026-09-28): counting its keep in the kept rate
    # would make removing cutoffs look like it wins more gain tests than it does.
    def test_kept_rates_leave_out_results_kept_for_no_loss(self) -> None:
        formula = {**self.row("E0003", "kept", -2.0, 0, 1, 39), "idea": {"kind": "formula"}}
        rows = [self.row("E0001", "discarded", -3.0, 0, 1, 40), self.row("E0002", "kept", 9.0, 1, 0, 41), formula]
        code = "\n".join(meta.cutoff_lines(rows))
        self.assertIn("- removed: 2 results, {'discarded': 1, 'kept': 1}", code)  # listed, with its Elo
        self.assertIn("kept, added against removed: 1/1 against 0/1;", code)

    def test_a_failing_cutoff_report_costs_only_its_own_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.jsonl"
            results.write_text("")
            with unittest.mock.patch.object(meta, "cutoff_lines", side_effect=ValueError("bad row")):
                text = meta.stats_markdown(results)
        self.assertIn("## Hard cutoffs: the report failed (ValueError: bad row)", text)
        self.assertIn("## Acceptance by category", text)


if __name__ == "__main__":
    unittest.main()
