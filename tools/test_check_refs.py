"""Checks the reference checker on testdata/check-refs/: a small tree of lines copied from CLAUDE.md, PLAN.md, the
champion's params.hpp and belief.hpp, champion commit messages, feed.hpp lines later experiments removed,
MISSES.md (a history file) and a few loop/ and referee/ definitions. Each check has a reference that resolves and
one that doesn't (a file this tree lacks, a name the module lacks, a param that was removed); --drift runs on small
git repos made in a temp folder.

    .venv/bin/python -m unittest tools.test_check_refs
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_refs  # noqa: E402

TREE = Path(__file__).resolve().parent / "testdata" / "check-refs"
# Params the champion's params.hpp declared once and has since removed (feed.hpp's, before the merge cascade).
FORMER = {"consolidateRound", "feedRound", "feedMinUnits", "feedMaxLength"}


def context(main_has_bot: bool = True) -> check_refs.Context:
    files = {str(path.relative_to(TREE)) for path in TREE.rglob("*") if path.is_file()}
    params = check_refs.parse_params((TREE / "bot/params.hpp").read_text())
    tracked = files if main_has_bot else {name for name in files if not name.startswith("bot/")}
    return check_refs.make_context(TREE, tracked, files, {"loop/champion"}, FORMER | params.names, params)


def problems(path: str) -> list[tuple[int, str, str, str]]:
    return [(p.line, p.level, p.kind, p.ref) for p in check_refs.check_file(context(), path)]


class Docs(unittest.TestCase):
    def test_only_the_broken_references_in_claude_md(self) -> None:
        # Paths, driver/sprt names and P.splitAtLength resolve; live state, the champion branch and the plain
        # "referee/quick" slash list aren't paths to check.
        self.assertEqual(problems("CLAUDE.md"), [
            (7, "error", "name", "sprt.limit_kept"),
            (15, "warning", "value", "mergeRound 499"),
        ])

    def test_bot_paths_resolve_in_the_bots_tree_when_main_has_no_bot(self) -> None:
        # `bot/params.hpp` (CLAUDE.md) and belief.hpp's sonarkey.hpp exist only in the bot's tree here.
        ctx = context(main_has_bot=False)
        self.assertEqual(check_refs.paths_in(ctx, "CLAUDE.md", "`bot/params.hpp` numbers (`loop/tune.py`"), [])
        self.assertEqual(check_refs.paths_in(ctx, "CLAUDE.md", "`bot/params.json`"),
                         [("bot/params.json", "no such file or folder")])
        self.assertEqual(check_refs.bot_files_in(ctx, "bot/CLAUDE.md", "key (sonarkey.hpp) and fold.hpp"),
                         [("fold.hpp", "no such file in bot/ or tests/bot/")])

    def test_a_path_into_a_deleted_top_folder_but_not_one_the_repo_never_had(self) -> None:
        # CLAUDE.md lines: with referee/ deleted (but in the history) `referee/WINDOWS.md` is broken; the live
        # state's discord/ folder was never in the repo, so `discord/latest.md` is not a repo path.
        files = {str(path.relative_to(TREE)) for path in TREE.rglob("*") if path.is_file()}
        kept = {name for name in files if not name.startswith("referee/")}
        params = check_refs.parse_params((TREE / "bot/params.hpp").read_text())
        ctx = check_refs.make_context(TREE, kept, files, set(), set(), params, {"referee", "loop", "bot", "tools"})
        self.assertEqual(check_refs.paths_in(ctx, "CLAUDE.md", "`referee/WINDOWS.md`). Workers play games"),
                         [("referee/WINDOWS.md", "no such file or folder: referee/ was deleted")])
        self.assertEqual(check_refs.paths_in(ctx, "CLAUDE.md", "into `discord/latest.md` (replaced at each read"), [])

    def test_a_history_file_warns_about_removed_names_instead_of_failing(self) -> None:
        self.assertEqual(problems("loop/MISSES.md"), [
            (3, "warning", "name", "sprt.limit_kept"),
            (4, "warning", "path", "loop/status.md"),
            (4, "warning", "param", "P.feedRound"),
        ])

    def test_missing_files_and_old_values_in_plan_md(self) -> None:
        # The E0059 message unwrapped quotes P.beaconRound 420 on a dated line: history, skipped. Wrapped, the
        # quote lands on an undated line. "P.foldMinLength 10 -> 1" is a change, not a claim about today.
        self.assertEqual(problems("PLAN.md"), [
            (3, "error", "path", "bot/params.json"),
            (4, "error", "path", "loop/status.md"),
            (9, "warning", "value", "beaconRound 420"),
        ])


class Code(unittest.TestCase):
    def test_bot_comments_but_not_code_or_strings(self) -> None:
        # sonarkey.hpp and P.sonarSipTag resolve; the code's removed param and the string's "fold.hpp" aren't
        # comments.
        self.assertEqual(problems("bot/belief.hpp"), [
            (4, "error", "path", "fold.hpp"),
            (8, "error", "param", "consolidateRound"),
            (9, "error", "param", "P.feedRound"),
            (9, "error", "param", "P.feedMinUnits"),
            (9, "error", "param", "P.feedMaxLength"),
        ])

    def test_python_comments_and_docstrings_but_not_strings(self) -> None:
        # loop/discord_teams.refresh is a function, not a path; the string "referee/gauntlet.toml" is code.
        self.assertEqual(problems("loop/driver.py"), [(18, "error", "path", "referee/gauntlet.toml")])

    def test_a_python_files_pieces_come_in_line_order(self) -> None:
        # driver.py's docstring (line 1) is found after its comments (lines 18 and 20).
        numbers = [n for n, _ in check_refs.text_of("loop/driver.py", (TREE / "loop/driver.py").read_text())]
        self.assertEqual(numbers, [1, 18, 20])

    def test_a_path_relative_to_its_own_folder(self) -> None:
        self.assertEqual(problems("tools/messages_sample.py"), [])

    def test_a_module_attribute_and_a_class_member(self) -> None:
        ctx = context()
        self.assertEqual(check_refs.names_in(ctx, "(`sprt.BaselineCache.key`, `driver.round500`)"),
                         [("driver.round500", "driver.py defines no round500")])
        self.assertEqual(check_refs.names_in(ctx, "sprt.BaselineCache.digest"),
                         [("sprt.BaselineCache.digest", "sprt.BaselineCache has no digest")])
        self.assertEqual(check_refs.names_in(ctx, "sprt.py and driver.py's"), [])

    def test_the_hook_blocks_errors_in_staged_files_and_errors_the_commit_caused(self) -> None:
        def errors(staged: set[str]) -> list[str]:
            found = (check_refs.check_file(context(), "bot/belief.hpp") + check_refs.check_file(context(), "PLAN.md")
                     + check_refs.check_file(context(), "CLAUDE.md"))
            return [p.ref for p in check_refs.for_commit(found, staged) if p.level == "error"]

        self.assertEqual(errors(set()), [])
        self.assertEqual(errors({"PLAN.md"}), ["bot/params.json", "loop/status.md"])
        self.assertEqual(len(errors({"bot/belief.hpp", "PLAN.md"})), 7)
        # Untouched files, but the commit changes what they name: status.md deleted, sprt.py or params.hpp edited.
        self.assertEqual(errors({"loop/status.md"}), ["loop/status.md"])
        self.assertEqual(errors({"referee/sprt.py"}), ["sprt.limit_kept"])
        self.assertEqual(errors({"bot/params.hpp"}), ["consolidateRound", "P.feedRound", "P.feedMinUnits",
                                                      "P.feedMaxLength"])
        self.assertEqual(errors({"bot/fold.hpp"}), ["fold.hpp"])

REMOTE = '''# The loop's live state folder: $BC_LOOP_HOME, or ~/.bc-loop when it is unset. A copy of loop/home.py's loop_home,
# since the referee runs from a copy that can't import loop/; tests set $BC_LOOP_HOME before importing (testhome.py).
def loop_home() -> Path:
    return Path(os.environ.get("BC_LOOP_HOME") or Path.home() / ".bc-loop").expanduser().resolve()


def python(windows: bool) -> str:
    """The pinned Python on the host."""
    return "python.exe" if windows else "python3"
'''

HUNT = '''// How many free steps away findRam can ram a head: each step after the first costs a segment and needs
// length >= 3 before it, up to P.maxRamSteps (pearls on the way can only add to this).
inline int ramReach(int length) { return std::min(P.maxRamSteps, std::max(1, length - 1)); }

// Variants of the flood fill (see Safety::floodFill).
struct FillOptions {
    bool ownBodyOnly = false;          // only walls and our own body block
    int wall = -1;                     // one more tile that blocks (a sprint's middle tile, now our neck)

    bool blocks(int t) const {
        return t == wall;
    }
};

struct Params {
    // --- Survival -----------------------------------------------------------------------------
    int spaceMargin = 2;         // a move needs length + margin reachable tiles
    int spaceCap = 40;           // the flood fill stops counting here ...
};
'''


class Drifts(unittest.TestCase):
    """A comment is a drift candidate when commits after its own last change touched its definition's lines."""

    def setUp(self) -> None:
        # No git trace2 events: a daemon listening for them (git-ai here) writes into a temp repo's .git after its
        # commits, racing the folder's removal.
        trace = patch.dict("os.environ", {"GIT_TRACE2_EVENT": "0"})
        trace.start()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.addCleanup(trace.stop)
        self.repo = Path(scratch.name)
        self.git("init", "-q")
        self.commit({"remote.py": REMOTE, "hunt.hpp": HUNT}, "first")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def commit(self, files: dict[str, str], message: str) -> None:
        for name, text in files.items():
            (self.repo / name).write_text(text)
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", message)

    def head(self) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def found(self, since: str | None = None) -> list[tuple[str, str, int]]:
        return [(d.path, d.name, len(d.later)) for d in check_refs.drift(self.repo, None, [], since)]

    def test_a_body_changed_after_its_comment(self) -> None:
        remote = REMOTE.replace('"~/.bc-loop"', '"~/.bc-loop-v2"').replace('Path.home() / ".bc-loop"',
                                                                             'Path.home() / ".bc-loop-v2"')
        self.commit({"remote.py": remote, "hunt.hpp": HUNT.replace("length - 1", "length - 2")}, "bodies")
        self.assertEqual(self.found(), [("hunt.hpp", "ramReach", 1), ("remote.py", "loop_home", 1)])

    def test_a_comment_changed_with_its_body_and_a_method_or_field_change_outside_a_comment(self) -> None:
        # The docstring changed in the same commit as the body; FillOptions' method body and Params' field
        # changed, but a struct's comment covers its members, not their bodies, and a section header covers nothing.
        remote = REMOTE.replace('"""The pinned Python on the host."""', '"""The pinned Python on the host (uv\'s)."""')
        remote = remote.replace('"python3"', '"python3.12"')
        hunt = HUNT.replace("return t == wall;", "return t == wall || t < 0;").replace("= 40;", "= 50;")
        self.commit({"remote.py": remote, "hunt.hpp": hunt}, "comment with body")
        self.assertEqual(self.found(), [])

    def test_a_commit_that_only_edits_comments_is_no_drift(self) -> None:
        # A comment line added inside a body, a docstring reworded, a trailing comment on a code line reworded:
        # none of it is code, so nothing drifted (a module docstring would otherwise count every such line).
        remote = REMOTE.replace("    return Path(", "    # $BC_LOOP_HOME wins over the default.\n    return Path(")
        remote = remote.replace('"""The pinned Python on the host."""', '"""The pinned Python on each host."""')
        hunt = HUNT.replace("// only walls and our own body block", "// only walls and our body block")
        self.commit({"remote.py": '"""Extra machines."""\n\n' + REMOTE, "hunt.hpp": HUNT}, "module docstring")
        self.commit({"remote.py": '"""Extra machines."""\n\n' + remote, "hunt.hpp": hunt}, "comments only")
        self.assertEqual(self.found(), [])

    def test_a_file_deleted_but_not_staged_is_skipped(self) -> None:
        (self.repo / "hunt.hpp").write_text(HUNT.replace("length - 1", "length"))
        (self.repo / "remote.py").unlink()
        self.assertEqual(self.found(), [("hunt.hpp", "ramReach", 1)])

    def test_since_keeps_only_definitions_changed_after_the_rev(self) -> None:
        # ramReach drifted before `before`, loop_home after it; a later ramReach change brings it back.
        self.commit({"hunt.hpp": HUNT.replace("length - 1", "length - 2")}, "hunt body")
        before = self.head()
        remote = REMOTE.replace('"~/.bc-loop"', '"~/.bc-loop-v2"')
        self.commit({"remote.py": remote.replace('Path.home() / ".bc-loop"', 'Path.home() / ".bc-loop-v2"')},
                    "remote body")
        self.assertEqual(self.found(before), [("remote.py", "loop_home", 1)])
        self.assertEqual(self.found(self.head()), [])
        self.commit({"hunt.hpp": HUNT.replace("length - 1", "length - 3")}, "hunt body again")
        self.assertEqual(self.found(before), [("hunt.hpp", "ramReach", 1), ("remote.py", "loop_home", 1)])
        with self.assertRaises(ValueError):
            self.found("no-such-rev")

    def test_show_prints_the_comment_and_definition_as_they_are_now(self) -> None:
        self.commit({"hunt.hpp": HUNT.replace("length - 1", "length - 2")}, "hunt body")
        [candidate] = check_refs.drift(self.repo, "HEAD", [])
        self.assertEqual((candidate.start, candidate.end), (1, 3))
        self.assertEqual(check_refs.definition_text(self.repo, "HEAD", candidate, 2).splitlines(), [
            "    1  // How many free steps away findRam can ram a head: each step after the first costs a segment and "
            "needs",
            "    2  // length >= 3 before it, up to P.maxRamSteps (pearls on the way can only add to this).",
            "       ... 1 more lines",
        ])
        self.assertIn("length - 2", check_refs.definition_text(self.repo, "HEAD", candidate, 40))

    def test_uncommitted_edits_are_the_newest(self) -> None:
        (self.repo / "hunt.hpp").write_text(HUNT.replace("length - 1", "length"))
        self.assertEqual(self.found(), [("hunt.hpp", "ramReach", 1)])


if __name__ == "__main__":
    unittest.main()
