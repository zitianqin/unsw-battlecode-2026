"""The daily backlog audit's trusted side (loop/backlog.py) on a real-data sample: testdata/backlog/ideas.jsonl holds
the 29 Sep backlog rows of the ideas that day's one-off P1 and P2 passes edited, as they stood when the passes read
them (15:37: I943 to do, I915 and I604 being tried; history cut to each row's first two notes, hypotheses to 600
characters but I871's, whose Options name I466), results.jsonl the results those passes cited, and edits.jsonl their
edits in the worker's form, the reports' own words. "Live" citations are read in champion 327435fe's tree (tag v0067),
the one those passes read."""

import testhome  # before anything that reads live paths
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backlog
import meta

DATA = Path(__file__).parent / "testdata/backlog"
CHAMPION = backlog.champion_source("327435fe")


def edit(item: str, idea: str, action: str, **fields) -> dict:
    return {"item": item, "id": idea, "action": action, **fields}


def lines(edits: list[dict]) -> str:
    return "".join(json.dumps(e) + "\n" for e in edits)


# A report.md naming `items` the way the brief asks (### B<n>. <title>).
def report(items) -> str:
    return "".join(f"### {item}. finding\n" for item in items)


class BacklogAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.state = Path(self.directory.name)
        (self.state / "ideas.jsonl").write_text((DATA / "ideas.jsonl").read_text())
        self.found = {row["id"]: {"verdict": row["verdict"], "idea": row["idea"]["id"]} for row in
                      map(json.loads, (DATA / "results.jsonl").read_text().splitlines())}
        self.edits = [json.loads(line) for line in (DATA / "edits.jsonl").read_text().splitlines()]
        self.waiting = [i["id"] for i in meta.live_ideas(self.state) if meta.is_waiting(i)]

    def tearDown(self) -> None:
        self.directory.cleanup()

    def ideas(self) -> dict[str, dict]:
        return {i["id"]: i for i in meta.live_ideas(self.state)}

    def apply(self, edits: list[dict], slice_ids=None, items=None) -> dict:
        return backlog.apply(self.state, lines(edits), report(items or {e["item"] for e in edits}), "2026-09-29-1537",
                             self.waiting if slice_ids is None else slice_ids, CHAMPION, self.found)

    # Findings 2, 13: citations checked, P1 closures only proposed, every changed row saved first and revertible.
    def test_the_passes_edits_apply_where_checked_propose_p1_closures_and_revert(self) -> None:
        outcome = self.apply(self.edits)
        ideas = self.ideas()
        # A duplicate merged, its note on the kept idea for that idea's coder, its prerequisite carried over (absorb).
        self.assertEqual((ideas["I884"]["status"], ideas["I884"]["merged_into"]), ("merged", "I943"))
        self.assertTrue(any("P.censusReplacesStaleViews" in note for note in ideas["I943"]["history"]))
        self.assertEqual(ideas["I943"]["after"], ["I883"])
        # A dead after (I914 dropped) re-pointed at what carries its change.
        self.assertEqual(ideas["I905"]["after"], ["I915"])
        why = {e["id"]: reason for e, reason in outcome["proposed"]}
        # The P1 closures' citations check (live code at safety.hpp:342-347, I466's kept E0311, 441 games), but a P1
        # idea is closed only by the orchestrator: proposed, still to do, listed for its decision.
        for idea in ("I592", "I871", "I440"):
            self.assertEqual((ideas[idea]["status"], why[idea]), ("todo", "a priority-1 idea: the orchestrator "
                                                                          "closes those"))
        # Evidence naming only results that weren't kept rejects implementations, not the ideas.
        self.assertIn("E0218, E0283, not kept", why["I374"])
        self.assertIn("E0138, not kept", why["I902"])
        self.assertEqual(sorted(why), ["I374", "I440", "I592", "I871", "I902"])
        refused = {e["id"]: reason for e, reason in outcome["refused"]}
        self.assertEqual(list(refused), ["I615"])  # no priority-1 idea is lowered (sig's rule)
        self.assertEqual(ideas["I615"]["priority"], 1)
        self.assertEqual([e["id"] for e, _ in outcome["skipped"]], ["I915"])  # being tried: not even a note
        self.assertNotIn("backlog", " ".join(ideas["I915"]["history"]))
        listed = outcome["listing"].read_text()
        for line in ("## I884.", "merged into I943", "## I905.", "edited: after", "## I592.",
                     "proposed, not applied: drop as live"):
            self.assertIn(line, listed)
        # Undo: the rows it changed were saved, and revert puts them back, notes kept.
        self.assertEqual(sorted(json.loads(r)["id"] for r in outcome["before"].read_text().splitlines()),
                         ["I884", "I905", "I943"])
        reverted, left = backlog.revert(self.state, outcome["before"])
        ideas = self.ideas()
        self.assertEqual((sorted(reverted), left), (["I884", "I905", "I943"], []))
        self.assertEqual((ideas["I884"]["status"], "merged_into" in ideas["I884"], ideas["I905"]["after"]),
                         ("todo", False, ["I914"]))
        self.assertIn("backlog revert", ideas["I905"]["history"][-1])

    # Findings 1, 3, 15: what each kind of closure's citation must hold, on the reviewer's probes.
    def test_a_closure_citation_must_hold_what_it_claims(self) -> None:
        by_id = self.ideas()

        def fault(idea: str, reason: str, evidence: str) -> str | None:
            return backlog.close_fault({"reason": reason, "evidence": evidence}, by_id[idea], by_id, self.found,
                                       CHAMPION)
        self.assertIsNone(fault("I592", "live", self.edits[1]["evidence"]))  # badRammer, dropHarvestEdge near 342
        self.assertIn("identifier", fault("I592", "live", "the change is live: bot/params.hpp:1"))
        self.assertIn("identifier", fault("I592", "live", "badRammer uses dropHarvestEdge: bot/safety.hpp:40"))
        # I211's dead lever: split.hpp:173's gate runs only with splitParentTerritory > 0, and it is 0.
        self.assertIn("P.splitParentTerritory, 0 or false",
                      fault("I592", "live", "bot/split.hpp:173 gates the split on P.splitParentTerritory"))
        self.assertIsNone(fault("I871", "kept", self.edits[2]["evidence"]))  # E0311 kept I466, which I871 names
        self.assertIn("kept for this idea", fault("I592", "kept", "E0311 covers it"))  # kept, but another idea's
        for evidence in ("The E0058 retest became E0218. It read -3.4 over 1698 pairs", "It lost 2 games", "p = 0.9",
                         "seen on 3 maps"):
            self.assertIsNotNone(fault("I440", "reversed", evidence), evidence)
        for evidence in (self.edits[3]["evidence"], "3 of 40 losses", "n = 12, p < 0.01"):
            self.assertIsNone(fault("I440", "reversed", evidence), evidence)

    # Findings 2, 5, 6, 7, 11, 12: the per-edit rules on the reviewer's probes, and the cap on one run's closures.
    def test_each_edit_breaking_a_rule_is_refused_alone(self) -> None:
        rows = [{"id": i, "category": "strategy", "priority": 2, "status": "todo", "title": f"t{i}", "hypothesis": "h",
                 "history": ["made"], **({"after": after} if after else {})}
                for i, after in (("I1", None), ("I2", None), ("I3", None), ("I5", None), ("I6", ["I7"]),
                                 ("I7", ["I5"]), ("I8", ["I1"]), ("I9", None))]
        (self.state / "ideas.jsonl").write_text(meta.ideas_text(rows + list(self.ideas().values())))
        today = ["I1", "I2", "I3", "I5", "I8", "I592", "I871"]  # 7 ideas: a cap of 1 closure or merge (a quarter)
        ideas = self.ideas()
        for idea in ("I592", "I871"):  # at P2 a closure whose citation checks applies, within the cap
            ideas[idea]["priority"] = 2
        (self.state / "ideas.jsonl").write_text(meta.ideas_text(list(ideas.values())))
        close_592 = {**self.edits[1], "item": "B10", "note": "from the P1 pass"}
        close_871 = {**self.edits[2], "item": "B11"}
        outcome = self.apply([
            edit("B1", "I2", "after", after=["I3"], note="x"), edit("B2", "I3", "after", after=["I2"], note="x"),
            edit("B3", "I5", "merge", into="I6", note="dup"),  # I6 waits on I5 through I7
            edit("B4", "I8", "after", after=[], note="x"),  # I1 still waits: only the orchestrator drops it
            edit("B5", "I9", "note", note="x"),  # outside today's slice
            edit("B6", "I1", "note", note="x"),  # B6 is not in report.md
            close_592, close_871], today, items=["B1", "B2", "B3", "B4", "B5", "B10", "B11"])
        refused = {e["item"]: why for e, why in outcome["refused"]}
        self.assertIn("waiting on itself", refused["B2"])  # B1 alone applies; B2 would close the loop
        self.assertIn("combine policy", refused["B3"])
        self.assertIn("drops I1, still waiting", refused["B4"])
        self.assertIn("not in today's slice", refused["B5"])
        self.assertIn("not an item of report.md", refused["B6"])
        self.assertEqual([e["item"] for e in outcome["applied"]], ["B1", "B10"])
        ideas = self.ideas()
        self.assertEqual(ideas["I592"]["status"], "dropped")
        self.assertIn("Note: from the P1 pass", ideas["I592"]["history"][-1])  # a close's note is kept
        self.assertEqual([(e["item"], why) for e, why in outcome["proposed"]],
                         [("B11", "past today's cap of 1 closures and merges (loop/backlog.py cap)")])

    # Finding 8: a validator failure refuses only the edits on the ideas it names; the rest apply.
    def test_a_validator_failure_refuses_only_the_ideas_it_names(self) -> None:
        real = meta.valid_backlog

        def refuses_i905(before: list[dict], text: str, params: str | None = None) -> list[dict] | None:
            rows = {json.loads(line)["id"]: json.loads(line) for line in text.splitlines()}
            return None if rows["I905"].get("after") == ["I915"] else real(before, text, params)
        with patch.object(meta, "valid_backlog", refuses_i905):
            outcome = self.apply([self.edits[0], self.edits[6]])
        self.assertEqual([e["id"] for e, _ in outcome["refused"]], ["I905"])
        self.assertEqual([e["id"] for e in outcome["applied"]], ["I884"])
        self.assertEqual(self.ideas()["I905"]["after"], ["I914"])

    def collect(self, name: str, text: str) -> str:
        work = self.state / "backlog" / name
        work.mkdir(parents=True)
        (meta.protected_io(self.state, "backlog", work) / "session.json").write_text('{"is_error": false}')
        (work / "report.md").write_text("### B1. a report\n")
        (work / "edits.jsonl").write_text(text)
        record = backlog.reports.read(self.state, backlog.KIND)
        backlog.reports.save(self.state, backlog.KIND, {**record, "pending": str(work), "next_cursor": [2, 1],
                                                        "pid": 1})
        return backlog.reports.collect(self.state, backlog.KIND, work)

    # Finding 8: a malformed list is refused whole, alerted, nothing applied; a second failure moves past the slice.
    def test_a_malformed_list_is_refused_whole_and_a_second_failure_moves_on(self) -> None:
        before = (self.state / "ideas.jsonl").read_text()
        bad = json.dumps(self.edits[0]) + '\n{"item": "B9", "id": "I910", "action": "drop"}\n'
        line = self.collect("1", bad)
        self.assertTrue(line.startswith("BACKLOG refused: ValueError: line 2: action 'drop'"), line)
        self.assertIn(line, (self.state / "host-alerts.log").read_text())
        self.assertEqual((self.state / "ideas.jsonl").read_text(), before)
        record = backlog.reports.read(self.state, backlog.KIND)
        self.assertNotIn("applied_at", record)  # health.backlog_audit's two-day alert counts from the last applied
        self.assertNotIn("cursor", record)  # the same slice comes round once more
        self.assertIn("Refused whole, nothing applied", Path(record["report"]).read_text())
        self.collect("2", bad)
        record = backlog.reports.read(self.state, backlog.KIND)
        self.assertEqual((record["cursor"], record["budget"]), ([2, 1], backlog.SLICE_CHARS // 2))

    # Findings 9, 14: the slice starts at P1 and later days cover the rest, a P1 raised behind the cursor leads the
    # next day's, and prune keeps each run's notes and session.
    def test_the_slice_rotates_leads_with_new_p1s_and_prune_keeps_worker_notes(self) -> None:
        ideas = list(self.ideas().values())
        waiting = {i["id"] for i in ideas if meta.is_waiting(i)}
        budget = sum(len(json.dumps(backlog.slice_row(i))) + 1 for i in ideas if meta.is_waiting(i)) // 2
        first, cursor, covered = backlog.day_slice(ideas, None, budget)
        self.assertEqual([r["priority"] for r in first][:1], [1])
        second, after, covered = backlog.day_slice(ideas, cursor, budget, covered)
        third, _, _ = backlog.day_slice(ideas, after, budget, covered)
        self.assertLess(len(first), len(waiting))
        self.assertEqual({r["id"] for r in first + second + third}, waiting)
        self.assertEqual(backlog.day_slice(ideas, None)[1], None)  # all of it fits a day: no cursor
        raised = [{**i, "priority": 1} if i["id"] == "I374" else i for i in ideas]  # P2 raised after day one
        second, _, _ = backlog.day_slice(raised, cursor, budget, [r["id"] for r in first])
        self.assertEqual(second[0]["id"], "I374")  # behind the cursor, yet not covered: it leads
        for n in range(backlog.KEEP_FOLDERS + 1):
            work = self.state / "backlog" / str(n)
            work.mkdir(parents=True)
            (work / "notes.md").write_text(f"checked {n}")
            (meta.protected_io(self.state, "backlog", work) / "session.json").write_text("{}")
        backlog.prune(self.state)
        self.assertFalse((self.state / "backlog/0").exists())
        self.assertEqual((self.state / "backlog-reports/workers/0.notes.md").read_text(), "checked 0")
        self.assertTrue((self.state / "backlog-reports/workers/0.session.json").exists())


if __name__ == "__main__":
    unittest.main()
