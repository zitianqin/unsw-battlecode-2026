"""Runs tools/hooks/pre-commit through real `git commit`s and a merge in a temp repo that has the hooks installed the
way sig installs them (core.hooksPath tools/hooks). The repo's tools/check_refs.py is a stub that fails when both
`a` and `b` exist (each branch's commit passes, their merge doesn't), so these test the hook, not the checker
(tools/test_check_refs.py does that): a failure blocks the commit, and the hook does nothing where it must not run.
Two tests add the real tools/doc_size.py, whose size cap on a staged CLAUDE.md, and a "LOOP.md: X" pointer left
naming no loop/LOOP.md heading, block too. StopHook runs tools/orchestrator_stop_hook.py, the orchestrator's Claude
Code Stop hook, on real messages.

    .venv/bin/python -m unittest tools.test_hooks
"""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent
STUB = '''import sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index("--root") + 1])
if (root / "a").exists() and (root / "b").exists():
    print("a:1: path b (a and b together)")
    sys.exit(1)
print("x:1: value y 1 (warning: z has 2)")
'''

# What the hooks see in these tests: sig's own shell, outside Claude Code. A test sets CLAUDECODE where it is at stake
# (the pre-commit hook refuses a Claude Code session's commit on main).
OUTSIDE_CLAUDE = {key: value for key, value in os.environ.items() if key != "CLAUDECODE"}


class PreCommit(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name).resolve()
        self.repo = self.scratch / "repo"
        (self.repo / "tools/hooks").mkdir(parents=True)
        for name in ("pre-commit", "pre-merge-commit", "reference-transaction"):
            shutil.copy2(TOOLS / "hooks" / name, self.repo / "tools/hooks" / name)
        (self.repo / "tools/check_refs.py").write_text(STUB)
        self.env = {**OUTSIDE_CLAUDE, "BC_LOOP_HOME": str(self.scratch / "home"), "GIT_TRACE2_EVENT": "0"}
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", "tools/hooks")
        self.assertEqual(self.commit("first").returncode, 0)

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd or self.repo, env=self.env,
                              capture_output=True, text=True)

    def commit(self, message: str, *names: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        for name in names:
            ((cwd or self.repo) / name).write_text(name)
        self.git("add", "-A", cwd=cwd)
        return self.git("commit", "-qm", message, cwd=cwd)

    def test_a_failing_check_blocks_the_commit_and_a_warning_does_not(self) -> None:
        passed = self.commit("a", "a")
        self.assertEqual(passed.returncode, 0)
        self.assertIn("warning", passed.stderr)  # git shows a hook's output on stderr
        blocked = self.commit("b too", "b")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("a:1: path b", blocked.stderr)
        self.assertIn("there is no bypass", blocked.stderr)
        self.assertEqual(self.git("log", "--format=%s").stdout.split("\n")[0], "a")
        (self.repo / "a").unlink()
        self.assertEqual(self.commit("fixed").returncode, 0)

    def test_a_commit_staging_claude_md_over_its_cap_is_blocked(self) -> None:
        shutil.copy2(TOOLS / "doc_size.py", self.repo / "tools/doc_size.py")
        limit = runpy.run_path(str(TOOLS / "doc_size.py"))["LIMITS"]["CLAUDE.md"]
        (self.repo / "CLAUDE.md").write_text("x" * limit)
        self.assertEqual(self.commit("at the cap").returncode, 0)
        (self.repo / "PLAN.md").write_text("x" * (limit + 1))  # not capped
        self.assertEqual(self.commit("a big uncapped doc").returncode, 0)
        (self.repo / "CLAUDE.md").write_text("x" * (limit + 1))
        blocked = self.commit("over the cap")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(f"CLAUDE.md: {limit + 1:,} bytes, over its {limit:,}-byte cap", blocked.stderr)
        self.assertIn("loop/LOOP.md", blocked.stderr)  # says where the detail goes
        self.assertIn("there is no bypass", blocked.stderr)

    def test_renaming_a_loop_md_heading_its_pointers_name_is_blocked(self) -> None:
        shutil.copy2(TOOLS / "doc_size.py", self.repo / "tools/doc_size.py")
        (self.repo / "loop").mkdir()
        (self.repo / "loop/LOOP.md").write_text("# How the loop works\n\n## Dev battles\n\n## Pearl seeds\n")
        (self.repo / "CLAUDE.md").write_text("Rules (LOOP.md: Dev battles,\n  Pearl seeds).\n")
        self.assertEqual(self.commit("pointers").returncode, 0)
        (self.repo / "loop/LOOP.md").write_text("# How the loop works\n\n## Dev games\n\n## Pearl seeds\n")
        blocked = self.commit("rename a heading")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("CLAUDE.md: LOOP.md: Dev battles: loop/LOOP.md has no heading 'Dev battles'", blocked.stderr)
        self.assertIn("there is no bypass", blocked.stderr)
        (self.repo / "CLAUDE.md").write_text("Rules (LOOP.md: Dev games, Pearl seeds).\n")
        self.assertEqual(self.commit("rename a heading and its pointer").returncode, 0)

    def test_a_merge_that_breaks_what_each_side_kept_is_blocked(self) -> None:
        self.git("checkout", "-q", "-b", "side")
        self.assertEqual(self.commit("b", "b").returncode, 0)
        self.git("checkout", "-q", "main")
        self.assertEqual(self.commit("a", "a").returncode, 0)
        merged = self.git("merge", "--no-edit", "side")
        self.assertNotEqual(merged.returncode, 0)
        self.assertIn("there is no bypass", merged.stderr)

    # REFLECT 2026-09-30 09:12 F12: a brief-rules line committed straight to main from the orchestrator's session. Under
    # Claude Code (CLAUDECODE set) a commit or merge commit on main is refused; a branch's commit and a detached
    # worktree's merge (how tools/merge.sh merges, PrePush) pass, as does sig's own commit on main (the tests above).
    def test_a_claude_code_session_never_commits_on_main(self) -> None:
        self.env["CLAUDECODE"] = "1"
        refused = self.commit("straight to main", "a")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("a Claude Code session doesn't commit on main", refused.stderr)
        self.assertIn("tools/merge.sh", refused.stderr)
        self.git("checkout", "-q", "-b", "orch/x")
        self.assertEqual(self.commit("on a branch").returncode, 0)
        self.git("checkout", "-q", "main")
        before = self.git("rev-parse", "main").stdout
        merged = self.git("merge", "--no-ff", "--no-edit", "orch/x")
        self.assertNotEqual(merged.returncode, 0)
        self.assertIn("a Claude Code session doesn't commit on main", merged.stderr)
        self.assertEqual(self.git("rev-parse", "main").stdout, before)
        self.git("merge", "--abort")
        self.git("worktree", "add", "-q", "--detach", str(self.scratch / "merge"), "main")
        self.assertEqual(self.git("merge", "--no-ff", "--no-edit", "orch/x", cwd=self.scratch / "merge").returncode, 0)

    # The commit hook sees only commits: a fast-forward, cherry-pick or reset moved main past it. The
    # reference-transaction hook refuses any move of main under Claude Code but to a commit with a pass marker (how
    # tools/merge.sh fast-forwards, PrePush) or one origin/main holds (a pull); sig's own moves and other refs pass.
    def test_a_claude_code_session_never_moves_main_but_to_a_tested_commit(self) -> None:
        self.git("checkout", "-q", "-b", "orch/x")
        self.assertEqual(self.commit("on a branch", "a").returncode, 0)
        self.git("checkout", "-q", "main")
        self.env["CLAUDECODE"] = "1"
        before, branch = self.git("rev-parse", "main").stdout.strip(), self.git("rev-parse", "orch/x").stdout.strip()
        for move in (("merge", "--ff-only", "orch/x"), ("cherry-pick", "orch/x"), ("reset", "-q", "--hard", "orch/x"),
                     ("update-ref", "refs/heads/main", "orch/x")):
            refused = self.git(*move)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("a Claude Code session doesn't move main", refused.stderr)
            self.assertIn("tools/merge.sh origin/main", refused.stderr)  # the fix when origin moved under a push
            self.assertEqual(self.git("rev-parse", "main").stdout.strip(), before)
            # A refused fast-forward has already written the files; main stays, and a reset in place passes.
            self.git("cherry-pick", "--abort")
            self.assertEqual(self.git("reset", "-q", "--hard").returncode, 0)
        self.assertEqual(self.git("branch", "-f", "orch/y", "orch/x").returncode, 0)  # not main
        markers = Path(self.git("rev-parse", "--git-common-dir").stdout.strip())
        (self.repo / markers / "test-passed").mkdir()
        (self.repo / markers / "test-passed" / branch).write_text("merge.sh orch/x\n")
        self.assertEqual(self.git("merge", "-q", "--ff-only", "orch/x").returncode, 0)
        self.assertEqual(self.git("rev-parse", "main").stdout.strip(), branch)
        # A fast-forward to what origin/main holds passes; a rollback of main does not.
        self.git("checkout", "-q", "orch/y")
        self.assertEqual(self.commit("pushed from elsewhere", "d").returncode, 0)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "main")
        self.assertEqual(self.git("merge", "-q", "--ff-only", "origin/main").returncode, 0)
        self.assertNotEqual(self.git("reset", "-q", "--hard", before).returncode, 0)
        # Not even onto an older tested merge (review: markers last a week); packing refs deletes main's loose file,
        # which passes, as gc must.
        (self.repo / markers / "test-passed" / before).write_text("merge.sh an older merge\n")
        self.assertNotEqual(self.git("reset", "-q", "--hard", before).returncode, 0)
        self.assertEqual(self.git("pack-refs", "--all").returncode, 0)
        self.assertEqual(self.git("gc", "-q").returncode, 0)
        self.env.pop("CLAUDECODE")
        self.assertEqual(self.git("reset", "-q", "--hard", before).returncode, 0)  # sig's own

    def test_a_checkout_without_the_checker_the_champions_and_gauntlet_branches_and_the_loop_home_are_skipped(self) -> None:
        self.commit("a", "a")
        self.git("checkout", "-q", "-b", "cand/E0001")
        self.assertEqual(self.commit("candidate", "b").returncode, 0)
        self.git("checkout", "-q", "-b", "gauntlet/greedyram", "main")
        self.assertEqual(self.commit("a screen opponent's bot", "b").returncode, 0)
        self.git("checkout", "-q", "-b", "old", "main")
        (self.repo / "tools/check_refs.py").unlink()
        self.assertEqual(self.commit("an old branch without the checker", "b").returncode, 0)
        home = self.scratch / "home"
        self.git("worktree", "add", "-q", "-b", "in-home", str(home / "champion"), "main")
        self.assertEqual(self.commit("in the loop home", "b", cwd=home / "champion").returncode, 0)
        # The same kind of worktree outside the loop home is checked.
        self.git("worktree", "add", "-q", "-b", "elsewhere", str(self.scratch / "elsewhere"), "main")
        self.assertNotEqual(self.commit("in a worktree", "b", cwd=self.scratch / "elsewhere").returncode, 0)


class PrePush(unittest.TestCase):
    """tools/hooks/pre-push and tools/merge.sh on a temp repo pushing to a bare remote (REFLECT 2026-09-29 14:29 F10:
    b130f1ad reached main untested). gated_test.sh, doc_size.py and check_refs.py are stubs, so this tests the marker,
    the modules chosen, the push and pre-commit's main check under merge.sh, not the load gate, the caps or the
    references."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        self.repo, remote = root / "repo", root / "remote.git"
        (self.repo / "tools/hooks").mkdir(parents=True)
        (self.repo / "loop").mkdir()
        (self.repo / "mapgen").mkdir()
        (self.repo / ".venv/bin").mkdir(parents=True)
        (self.repo / ".venv/bin/python").symlink_to(sys.executable)
        (self.repo / ".gitignore").write_text(".venv\n")
        for name in ("pre-push", "pre-commit", "pre-merge-commit", "reference-transaction"):
            shutil.copy2(TOOLS / "hooks" / name, self.repo / "tools/hooks" / name)
        (self.repo / "tools/check_refs.py").write_text("")  # a passing checker: the pre-commit hook runs, as on main
        shutil.copy2(TOOLS / "merge.sh", self.repo / "tools/merge.sh")
        (self.repo / "tools/gated_test.sh").write_text('#!/bin/sh\n[ -z "$GATE_SHUT" ] || exit 75\nexec "$@"\n')
        (self.repo / "tools/gated_test.sh").chmod(0o755)
        (self.repo / "tools/doc_size.py").write_text("")
        passing = "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n"
        for name in ("test_home", "test_launches", "test_ok"):
            (self.repo / f"loop/{name}.py").write_text(passing)
        (self.repo / "loop/helper.py").write_text("x = 1\n")
        (self.repo / "loop/test_helper.py").write_text("import helper\n" + passing)
        (self.repo / "mapgen/gen.py").write_text("x = 1\n")
        (self.repo / "tools/test_tool.py").write_text(passing)
        # The loop home apart from TMPDIR, where merge.sh makes its worktree: the pre-commit hook skips the loop home.
        self.home = root / "home"
        self.home.mkdir()
        self.env = {**OUTSIDE_CLAUDE, "GIT_TRACE2_EVENT": "0", "TMPDIR": str(root), "BC_LOOP_HOME": str(self.home)}
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", "tools/hooks")
        self.git("config", "commit.gpgsign", "false")
        self.git("remote", "add", "origin", str(remote))
        self.git("add", "-A")
        self.git("commit", "-qm", "first")
        self.markers = self.repo / ".git/test-passed"

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True)

    def commit(self, text: str, *names: str) -> None:
        for name in names:
            (self.repo / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", names[0])

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    # merge.sh as the orchestrator runs it, from a Claude Code session: its merge commit is made on a detached worktree
    # and main only fast-forwards, so the pre-commit hook's refusal on main (REFLECT 2026-09-30 09:12 F12) never fires.
    def merge(self, branch: str, *modules: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tools/merge.sh", branch, f"Merge {branch}", *modules], cwd=self.repo,
                              env={**self.env, "CLAUDECODE": "1", **(env or {})}, capture_output=True, text=True)

    def branch(self, name: str, text: str, *files: str) -> None:
        self.git("checkout", "-qb", name)
        self.commit(text, *files)
        self.git("checkout", "-q", "main")

    def test_code_reaches_main_only_through_merge_sh(self) -> None:
        refused = self.git("push", "-q", "origin", "main")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("but has no test pass marker", refused.stderr)
        self.assertIn("tools/merge.sh <branch>", refused.stderr)
        self.branch("feature", "y = 2\n", "loop/tool.py")
        merged = self.merge("feature", "test_ok")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        head = self.rev("main")
        self.assertEqual(self.rev("origin/main"), head)
        self.assertEqual(self.rev(f"{head}^2"), self.rev("feature"))
        self.assertIn("merge.sh feature: test_ok test_helper test_home test_launches", (self.markers / head).read_text())
        self.assertEqual(self.git("worktree", "list").stdout.count("\n"), 1)  # its test worktree is gone
        self.commit("docs only\n", "notes.md")
        self.assertEqual(self.git("push", "-q", "origin", "main").returncode, 0)  # no code: passes freely
        # A direct commit on main, unpushed: the hook refuses it (mapgen/ and tools/ scripts are code too), and the
        # next merge tests what it changed, not only the branch's own diff.
        self.commit("x = 2\n", "loop/helper.py", "mapgen/gen.py", "tools/run.sh")
        refused = self.git("push", "-q", "origin", "main")
        self.assertIn("changes loop/helper.py mapgen/gen.py tools/run.sh but", refused.stderr)
        self.assertEqual(self.git("push", "-q", "origin", "main:side").returncode, 0)  # not main
        self.branch("docs", "more docs\n", "notes.md")
        # A named module no file holds stops it before any test; a bare name only in tools/ resolves there.
        before = self.rev("main")
        wrong = self.merge("docs", "test_nope", "tools/test_tool.py")
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("mapgen/: test_nope (name loop ones bare", wrong.stderr)
        self.assertNotIn("testing", wrong.stdout)
        self.assertEqual(self.rev("main"), before)
        merged = self.merge("docs", "test_tool", "tools/test_tool.py")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn("testing 4 modules: tools.test_tool test_helper test_home test_launches", merged.stdout)
        self.assertIn("ok   tools.test_tool: Ran 1 test", merged.stdout)
        self.assertEqual(self.rev("origin/main"), self.rev("main"))
        # A failing module leaves main where it was, writes no marker and pushes nothing.
        self.branch("broken", "import unittest\nclass T(unittest.TestCase):\n    def test(self): self.fail()\n",
                    "loop/test_ok.py")
        before = self.rev("main")
        failed = self.merge("broken")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("failed modules: test_ok", failed.stderr)
        self.assertEqual((self.rev("main"), self.rev("origin/main")), (before, before))
        self.assertEqual(len(list(self.markers.iterdir())), 2)
        # A module gated_test.sh gave up on (exit 75, the load) is not run, never a pass, and stops it too.
        shut = self.merge("docs", env={"GATE_SHUT": "1"})
        self.assertNotEqual(shut.returncode, 0)
        self.assertIn("NOT RUN (load) test_home", shut.stdout)
        self.assertIn("merge.sh: not run (load): ", shut.stderr)
        self.assertNotIn("failed modules", shut.stderr)
        self.assertEqual((self.rev("main"), self.rev("origin/main")), (before, before))

    # 29 Sep 18:19: portal-bets' merge failed on mapgen.test_gen, plain functions its __main__ runs, since unittest
    # found no tests in it. A module with no TestCase runs as a script: its exit status decides.
    def test_a_module_with_no_testcase_runs_as_a_script(self) -> None:
        plain = "def test_x():\n    assert {}\n\nif __name__ == '__main__':\n    test_x()\n    print('ok')\n"
        (self.repo / "mapgen/__init__.py").write_text("")
        self.branch("plain", plain.format("True"), "mapgen/test_plain.py")
        merged = self.merge("plain", "mapgen.test_plain")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn("ok   mapgen.test_plain: run as a script, exit 0", merged.stdout)
        self.branch("plain-broken", plain.format("False"), "mapgen/test_plain.py")
        failed = self.merge("plain-broken", "mapgen.test_plain")
        self.assertIn("failed modules: mapgen.test_plain", failed.stderr)


    # REFLECT 2026-09-29 17:32 F20: a module that passes only on its lone rerun is recorded for health.merge_reruns.
    def test_a_module_passing_only_on_its_rerun_is_recorded(self) -> None:
        flaky = ("import os, pathlib, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
                 "        seen = pathlib.Path(os.environ['BC_LOOP_HOME']) / 'seen'\n"
                 "        first = not seen.exists()\n        seen.touch()\n        self.assertFalse(first)\n")
        self.branch("flaky", flaky, "loop/test_flaky.py")
        merged = self.merge("flaky")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn("ok   test_flaky on its own rerun", merged.stdout)
        (row,) = [json.loads(line) for line in (self.home / "merge-reruns.jsonl").read_text().splitlines()]
        self.assertEqual((row["module"], row["branch"]), ("test_flaky", "flaky"))

    # REFLECT 2026-10-01 03:17: test_mapideas timed out under load and failed an unrelated merge. A TimeoutExpired in a
    # module the merge doesn't touch gets one more run, said so; in one it touches, it fails.
    def test_a_timeout_in_an_untouched_module_retries_once(self) -> None:
        # It times out while <loop home>/slow holds runs left to time out.
        slow = ("import os, pathlib, subprocess, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
                "        left = pathlib.Path(os.environ['BC_LOOP_HOME']) / 'slow'\n"
                "        n = int(left.read_text()) if left.exists() else 0\n"
                "        if n:\n            left.write_text(str(n - 1))\n"
                "            raise subprocess.TimeoutExpired('uv', 900)\n")
        self.commit(slow, "loop/test_slow.py")
        self.assertEqual(self.merge("main", "test_ok").returncode, 0)  # pushes it
        self.branch("other", "y = 2\n", "loop/tool.py")
        (self.home / "slow").write_text("2")  # beside the others, then alone
        merged = self.merge("other", "test_slow")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn("test_slow timed out (TimeoutExpired) and this merge doesn't touch it: retrying it once",
                      merged.stdout)
        self.assertIn("ok   test_slow on its timeout retry", merged.stdout)
        self.branch("touches", slow + "# touched\n", "loop/test_slow.py")
        (self.home / "slow").write_text("2")
        failed = self.merge("touches")
        self.assertIn("failed modules: test_slow", failed.stderr)
        self.assertNotIn("retrying it once", failed.stdout)

    # 473fd77d changed tools/watch.py and broke tools/test_blunders (blunders imports watch), which no merge ran. A
    # changed module's importers' test modules run too, except a hub's (over HUB_IMPORTERS importers), named instead.
    def test_the_tests_of_a_changed_modules_importers_run_but_not_a_hubs(self) -> None:
        passing = "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n"
        self.commit("x = 1\n", "tools/watch.py", "loop/hub.py")
        self.commit("import watch\n", "tools/blunders.py")
        self.commit(passing, "tools/test_blunders.py")
        for n in range(9):
            self.commit("import os, hub\n", f"loop/user{n}.py")
            self.commit(passing, f"loop/test_user{n}.py")
        self.commit("import hub\n" + passing, "loop/test_hubdirect.py")
        self.merge("main", "test_ok")  # pushes the setup, so the next merge's diff is only its branch
        self.branch("watch", "x = 2\n", "tools/watch.py", "loop/hub.py")
        merged = self.merge("watch", "test_ok")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertIn("testing 5 modules: test_ok test_hubdirect tools.test_blunders test_home test_launches",
                      merged.stdout)
        self.assertIn("only their direct test importers run: hub", merged.stdout)

class RealChecker(unittest.TestCase):
    """The real tools/check_refs.py behind the hook: a broken reference already committed in a file this commit
    doesn't touch only warns; staged, it blocks. The setup commits with hooks off, since the checker's own docstring
    names this repo's paths."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        (self.repo / "tools/hooks").mkdir(parents=True)
        (self.repo / "loop").mkdir()
        for name in ("check_refs.py", "hooks/pre-commit", "hooks/pre-merge-commit"):
            shutil.copy2(TOOLS / name, self.repo / "tools" / name)
        (self.repo / "loop/driver.py").write_text("# The driver.\n")
        (self.repo / "README.md").write_text("Run `loop/gone.py`.\n")  # broken on main
        (self.repo / "notes.md").write_text("Run `loop/driver.py`.\n")
        self.env = {**OUTSIDE_CLAUDE, "BC_LOOP_HOME": str(self.repo.parent / "home"), "GIT_TRACE2_EVENT": "0"}
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", "tools/hooks")
        self.git("add", "-A")
        self.assertEqual(self.git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "first").returncode, 0)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def test_an_error_in_an_untouched_file_warns_and_one_in_a_staged_file_blocks(self) -> None:
        (self.repo / "notes.md").write_text("Run `loop/driver.py` first.\n")
        self.git("add", "notes.md")
        passed = self.git("commit", "-qm", "notes")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("README.md:1: path loop/gone.py (warning: no such file or folder; already on main: fix on "
                      "main)", passed.stderr)
        (self.repo / "README.md").write_text("Run `loop/gone.py` now.\n")
        self.git("add", "README.md")
        blocked = self.git("commit", "-qm", "readme")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("README.md:1: path loop/gone.py (no such file or folder)", blocked.stderr)


    def test_a_rename_that_breaks_an_untouched_doc_blocks(self) -> None:
        # A module only guide.md names (the checker's own docstring names the driver, so renaming that would block).
        (self.repo / "loop/tool.py").write_text("")
        (self.repo / "guide.md").write_text("Run `loop/tool.py`.\n")
        self.git("add", "-A")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-qm", "tool")
        self.git("mv", "loop/tool.py", "loop/runner.py")
        blocked = self.git("commit", "-qm", "rename")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("guide.md:1: path loop/tool.py (no such file or folder; broken by this commit: guide.md:1)",
                      blocked.stderr)
        (self.repo / "guide.md").write_text("Run `loop/runner.py`.\n")
        self.git("add", "guide.md")
        self.assertEqual(self.git("commit", "-qm", "rename and its doc").returncode, 0)

    def test_a_merge_is_blocked_only_by_errors_in_the_files_it_changes(self) -> None:
        unhooked = ("-c", "core.hooksPath=/dev/null")
        self.git("checkout", "-q", "-b", "notes")
        (self.repo / "notes.md").write_text("Run `loop/driver.py` first.\n")
        self.git(*unhooked, "commit", "-qam", "notes")
        self.git("checkout", "-q", "-b", "readme", "main")
        (self.repo / "README.md").write_text("Run `loop/gone.py` now.\n")
        self.git(*unhooked, "commit", "-qam", "readme")
        self.git("checkout", "-q", "main")
        (self.repo / "loop/other.py").write_text("")
        self.git("add", "-A")
        self.git(*unhooked, "commit", "-qm", "main moves on")  # so each merge makes a merge commit
        self.assertEqual(self.git("merge", "-q", "--no-edit", "notes").returncode, 0)
        blocked = self.git("merge", "-q", "--no-edit", "readme")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("there is no bypass", blocked.stderr)


class StopHook(unittest.TestCase):
    """tools/orchestrator_stop_hook.py run as Claude Code runs it, on transcripts of the orchestrator's real messages
    (29 Sep, trimmed): it blocks the stop when a question of sig's since the last reply has no answer by the turn's
    end (REFLECT 2026-09-29 17:32 F5) or a reply hands him work his grants cover (F12, F13), passes an answer that
    follows a short note, and never blocks another session, a stop it already blocked, or on its own error."""

    SESSION = "8406d943-dd71-49fc-b16b-5c8a82eb997f"
    EVALLINE = ("Evaluating: E0342 (I604 Retest E0215 after I603 on the current champion: Carry the k) since 17:12, "
                "12/2400 trials, 12 trials/min, 44 games/min, latest finish from 17:17; queued, in order: E0338 coded "
                "15:00; coding: E0345, E0349, E0351; fillers: sweep; hosts 71/72 slots busy")

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name).resolve()
        (self.home / "orchestrator-session").write_text(self.SESSION + "\n")

    def stop(self, rows: list[tuple[str, str, str]], session: str = SESSION, active: bool = False,
             last: str | None = None) -> str:
        path = self.home / "t.jsonl"
        path.write_text("".join(json.dumps(
            {"type": "user", "timestamp": at, "origin": {"kind": "human"}, "message": {"content": text}}
            if who == "sig" else {"type": "assistant", "timestamp": at,
                                  "message": {"content": [{"type": "text", "text": text}]}}) + "\n"
            for at, who, text in rows))
        event = {"session_id": session, "transcript_path": str(path), "stop_hook_active": active,
                 **({"last_assistant_message": last} if last is not None else {})}
        done = subprocess.run([sys.executable, str(TOOLS / "orchestrator_stop_hook.py")], input=json.dumps(event),
                              env={**os.environ, "BC_LOOP_HOME": str(self.home)}, capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        return done.stdout

    def reason(self, rows: list[tuple[str, str, str]], last: str | None = None) -> str:
        out = json.loads(self.stop(rows, last=last))
        self.assertEqual(out["decision"], "block")
        return out["reason"]

    def test_a_question_whose_answer_sank_under_status_blocks_and_a_later_answer_passes(self) -> None:
        asked = ("2026-09-29T07:11:33Z", "sig", "is the faster engine wrapper being used eveyrwhere now?")
        sunk = ("2026-09-29T07:12:27Z", "claude", self.EVALLINE + "\n\nAll 8 lanes are busy. E0347's post-mortem also "
                "found that counting enemy births from the dragon id numbers works better than counting the deaths "
                "we see, so I974 now measures the births count first.")
        reason = self.reason([asked, sunk])
        self.assertIn("sig's question has no answer yet", reason)
        self.assertIn("is the faster engine wrapper being used eveyrwhere now?", reason)
        self.assertNotIn("standing grants", reason)
        answered = ("2026-09-29T07:13:02Z", "claude", "The engine wrapper (the engine cache) is used everywhere: "
                    "every host loads the precompiled engine.")
        self.assertEqual(self.stop([asked, ("2026-09-29T07:11:50Z", "claude", "Reading the hosts' status."),
                                    answered]), "")
        # Answered in an earlier turn: a new turn's reply doesn't reopen it.
        self.assertEqual(self.stop([asked, answered, ("2026-09-29T07:14:00Z", "sig", "ok thanks"),
                                    ("2026-09-29T07:14:10Z", "claude", "Noted.")]), "")

    def test_a_reply_ending_with_an_ask_the_grants_cover_blocks(self) -> None:
        asked = ("2026-09-29T05:20:25Z", "sig", "what ar eall the portal ideas we have? and which ones will actually "
                                                 "help for the 9 bad 1 good map i had")
        reply = ("Here are the open portal ideas, grouped by what they'd do on your map with 9 death portals and 1 vault "
                 "per side. | So the protocol this map rewards doesn't exist as one idea. It's I923, I758/I228, I812 "
                 "and I524, and they need to be built in that order.")
        reason = self.reason([asked, ("2026-09-29T05:21:22Z", "claude", reply + " | Want me to queue it as one chain, "
                                      "each waiting on the one before, with I812's retest reworked around it? I haven't "
                                      "queued it yet.")])
        self.assertIn("hands sig work his standing grants cover", reason)
        self.assertIn('"Want me to" waits on sig, but the standing grants cover it', reason)
        self.assertNotIn("no answer yet", reason)
        self.assertIn('"Say if you want" waits on sig', self.reason([
            ("2026-09-29T05:14:01Z", "sig", "do we have an updated eta until all the p1 items in our idea queue are "
                                            "done?"),
            ("2026-09-29T05:15:02Z", "claude", "No finite ETA: at today's rates the P1 queue isn't shrinking. | Say if "
                                               "you want either; they change what gets queued, so I haven't started "
                                               "them.")]))
        self.assertEqual(self.stop([asked, ("2026-09-29T05:21:22Z", "claude", reply + " | Queuing it as one chain "
                                                                                     "now, starting it now.")]), "")

    def test_other_sessions_a_stop_blocked_once_and_its_own_errors_pass(self) -> None:
        rows = [("2026-09-29T07:11:33Z", "sig", "is the faster engine wrapper being used eveyrwhere now?"),
                ("2026-09-29T07:12:27Z", "claude", "All 8 lanes are busy.")]
        self.assertTrue(self.stop(rows))
        self.assertEqual(self.stop(rows, session="another"), "")
        self.assertEqual(self.stop(rows, active=True), "")
        (self.home / "t.jsonl").write_text("")
        done = subprocess.run([sys.executable, str(TOOLS / "orchestrator_stop_hook.py")], input="not json",
                              env={**os.environ, "BC_LOOP_HOME": str(self.home)}, capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_the_checks_read_every_line_since_the_question_from_the_transcripts_tail(self) -> None:
        sys.path.insert(0, str(TOOLS))
        import my_messages
        import orchestrator_stop_hook as hook
        from datetime import datetime, timedelta, timezone
        start = datetime(2026, 9, 29, 5, tzinfo=timezone.utc)
        path = self.home / "long.jsonl"
        path.write_text("".join(json.dumps({"type": "assistant", "timestamp": (start + timedelta(minutes=n)).isoformat(),
                                            "message": {"content": [{"type": "text", "text": f"line {n}"}]}}) + "\n"
                                for n in range(300)))
        since = start + timedelta(minutes=250)
        with patch.object(my_messages, "CHUNK", 1000):
            with hook.Tail(path, my_messages.tail_start(path, since)).open() as handle:
                times = [json.loads(line)["timestamp"] for line in handle]
        self.assertLess(times[0], since.isoformat())  # it reaches back past the question
        self.assertLess(len(times), 300)  # but not to the start
        self.assertEqual(times[-50:], [(start + timedelta(minutes=n)).isoformat() for n in range(250, 300)])

    # The review of orch/builder-c: 17:12's block came back at 17:14, in a turn a child's message started, as the turn
    # was taken to start at sig's last line. A turn starts at the previous stop too, so no reply is checked twice.
    def test_a_turn_a_childs_message_starts_never_rechecks_an_earlier_stops_replies(self) -> None:
        asked = ("2026-09-29T07:11:33Z", "sig", "is the faster engine wrapper being used eveyrwhere now?")
        sunk = ("2026-09-29T07:12:27Z", "claude", "All 8 lanes are busy.")
        self.assertIn("no answer yet", self.reason([asked, sunk]))
        path = self.home / "t.jsonl"
        with path.open("a") as handle:
            for row in ({"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-09-29T07:12:31Z"},
                        {"type": "user", "isMeta": True, "timestamp": "2026-09-29T07:14:30Z",
                         "message": {"content": "<cross-session-message>E0351 coding done</cross-session-message>"}},
                        {"type": "assistant", "timestamp": "2026-09-29T07:14:35Z",
                         "message": {"content": [{"type": "text", "text": "Noted."}]}}):
                handle.write(json.dumps(row) + "\n")
        event = {"session_id": self.SESSION, "transcript_path": str(path), "stop_hook_active": False}
        done = subprocess.run([sys.executable, str(TOOLS / "orchestrator_stop_hook.py")], input=json.dumps(event),
                              env={**os.environ, "BC_LOOP_HOME": str(self.home)}, capture_output=True, text=True)
        # "Noted." answers the child's notice without evalline's line, which blocks since REFLECT 2026-10-01 15:21 F17;
        # the question an earlier stop already flagged never comes back.
        self.assertNotIn("no answer yet", done.stdout)
        self.assertIn("not opening with evalline", done.stdout)

    # The review's finding 3 and a 03:00 false positive: an answer in a later line of the reply counts, as does one
    # sharing only a three-letter word ("gpu") with the question.
    def test_an_answer_in_any_line_counts(self) -> None:
        self.assertEqual(self.stop([
            ("2026-09-29T05:03:58Z", "sig", "is the mac load still at 0.4?"),
            ("2026-09-29T05:04:11Z", "sig", "will those portal map ideas i had also be int he eval maps too?"),
            ("2026-09-29T05:07:05Z", "claude", "Yes, the Mac load share is 0.4.\n\nPortal maps in the eval maps: yes.")]),
            "")
        self.assertEqual(self.stop([
            ("2026-09-29T17:00:04Z", "sig", "We have a gpu on zz which will be turned off around 7 am. Any ideas on "
                                            "utilising it?"),
            ("2026-09-29T17:00:39Z", "claude", "zz has an RTX 4070 Ti SUPER (16 GB), sitting at 0%. My recommendation "
                                               "is to leave it idle tonight:\n1. **The GPU can't speed up our games.**")]),
            "")

    # REFLECT 2026-09-30 09:12 F21: the hook runs before the reply it stops on is in the transcript, so all 29 status
    # replies not opening with evalline's line passed (03:12:55's shown here), and 03:12:25's answer ("Mostly no")
    # was missed, blocking as unanswered. The input's last_assistant_message is that reply.
    def test_the_reply_stopped_on_is_checked_before_the_transcript_holds_it(self) -> None:
        turn = [("2026-09-29T17:12:47Z", "sig", "status")]
        reply = "No lane was free after all. The driver counts E0382 in the queue as in flight, so it deferred."
        self.assertEqual(self.stop(turn), "")  # what the hook saw on 30 Sep
        self.assertIn("E0382 in the queue", self.reason(turn, last=reply))
        written = turn + [("2026-09-29T17:12:55Z", "claude", reply)]
        self.assertEqual(self.reason(written, last=reply).count("E0382 in the queue"), 1)  # once, when both have it
        asked = [("2026-09-29T17:12:08Z", "sig", "is that what the sonar messages from the top teams imply? can we "
                                                 "copy them?")]
        self.assertIn("no answer yet", self.reason(asked))
        self.assertEqual(self.stop(asked, last="Mostly no. We can't copy their messages, only their play."), "")

    # REFLECT 2026-09-30 15:13 F8: each question of a message is its own. 13:03:32 answered only "our 5h usage reset",
    # and 12:40:52 filed the history on I1122 without naming what the old experiments found.
    def test_each_question_needs_its_own_answer_and_a_note_on_an_idea_is_none(self) -> None:
        two = ("2026-09-30T03:01:33Z", "sig", "are all the evals operaitng at full capacity? also our 5h usage reset")
        reason = self.reason([two, ("2026-09-30T03:03:32Z", "claude", "The 13:03 wake fired, and a new usage window "
                                    "has started. Nothing was paused and nothing was missed; the next wake is 18:04.")])
        self.assertIn("sig asked: are all the evals operaitng at full capacity?", reason)
        self.assertEqual(self.stop([two, ("2026-09-30T03:03:32Z", "claude", "The 13:03 wake fired, and a new usage "
                                          "window has started.\nNo: 36 of 71 host slots are busy.")]), "")
        old = ("2026-09-30T02:40:29Z", "sig", "Did we already try similar in our old experiments. Did we learn anything")
        noted = ("2026-09-30T02:40:52Z", "claude", "The history is noted on I1122: extend the existing guard "
                 "(`seal.hpp`'s `guardTeammatePocket`) to the follower case, rather than adding another blanket rule.")
        reason = self.reason([old, noted])
        self.assertIn("sig asked: Did we already try similar in our old experiments.", reason)
        self.assertIn("sig asked: Did we learn anything", reason)
        self.assertEqual(self.stop([old, ("2026-09-30T02:40:52Z", "claude", "Yes: E0357 added `guardTeammatePocket` "
                                          "for the pocket case, noted on I1122; we learned that blanket corridor bans "
                                          "cost more than they save.")]), "")

    # REFLECT 2026-09-30 15:13 F5: 11:21:52's beacon study (trimmed) read Cutlery's older ranked replays with no decoy
    # filter; a builder's brief naming the replay folders is code work, not a study.
    def test_a_study_of_opponents_games_needs_its_sources_line(self) -> None:
        brief = ("Reverse engineer how Cutlery (team 306) and Stockfish (team 206) use sonar beacons.\n\nInputs:\n"
                 "- Cutlery's older ranked replays: ~/.bc-loop/study-replays (index.jsonl has teamAId/teamBId, ranked, "
                 "requestedAt), ladder-replays and scout-replays.\n\nRecent ranked games only, split by submission "
                 "period, and say which.")
        sources = ("\nSources: ranked games only, by submission id, loop/decoys.py flags applied; report kept and "
                   "dropped counts.")

        def turn(prompt: str) -> str:
            path = self.home / "t.jsonl"
            rows = [{"type": "user", "timestamp": "2026-09-30T01:21:30Z", "origin": {"kind": "human"},
                     "message": {"content": "let's try to reverse engineer both of those."}},
                    {"type": "assistant", "timestamp": "2026-09-30T01:21:52Z", "message": {"content": [
                        {"type": "tool_use", "id": "toolu_1", "name": "Agent",
                         "input": {"description": "Reverse engineer Cutlery/Stockfish beacons", "prompt": prompt}}]}},
                    {"type": "assistant", "timestamp": "2026-09-30T01:21:57Z",
                     "message": {"content": [{"type": "text", "text": "The beacon study is running."}]}}]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            event = {"session_id": self.SESSION, "transcript_path": str(path), "stop_hook_active": False}
            done = subprocess.run([sys.executable, str(TOOLS / "orchestrator_stop_hook.py")], input=json.dumps(event),
                                  env={**os.environ, "BC_LOOP_HOME": str(self.home)}, capture_output=True, text=True)
            return done.stdout

        reason = json.loads(turn(brief))["reason"]
        self.assertIn("reads opponents' games without the Sources rules", reason)
        self.assertIn("no Sources: line: ranked games only, by submission id, loop/decoys.py's flags applied, kept and "
                      "dropped counts", reason)
        self.assertEqual(turn(brief + sources), "")
        self.assertEqual(turn("Work on branch orch/batch-f. The leader study reads study-replays; fix its fetch."), "")

    # REFLECT 2026-09-30 15:13 F14: 13:49:41's option came with no recommendation.
    def test_an_option_without_a_recommendation_asks_for_one(self) -> None:
        reason = self.reason([("2026-09-30T03:49:34Z", "sig", "why not just sotp it early?"),
                              ("2026-09-30T03:49:41Z", "claude", "You told me not to: you rejected early futility stops. "
                               "Only the SPRT's own bounds end a test.\n\nIf you want futility stops now, say so and "
                               "I'll add them as a rule for every test.")])
        self.assertIn("leaves sig an option with no recommendation", reason)
        self.assertNotIn("hands sig work", reason)

    # REFLECT 2026-09-30 15:13 F4: 11:50:47 left E0411's noValidAction deaths "worth watching" and queued the SPRT.
    def test_a_caution_needs_its_check_started(self) -> None:
        handed = ("2026-09-30T01:50:40Z", "sig", "status")
        caution = ("In each of its two quick checks, one dragon died with no valid move on the same map, where the "
                   "champion's quick check had none. It's worth watching, but it isn't a crash, so it doesn't block the "
                   "SPRT.")
        reason = self.reason([handed, ("2026-09-30T01:50:47Z", "claude", caution)])
        self.assertIn("leaves a problem to watch instead of checking it", reason)
        self.assertNotIn("hands sig work", reason)
        self.assertEqual(self.stop([handed, ("2026-09-30T01:50:47Z", "claude", caution + " A subagent is checking the "
                                             "death's debug lines before it's queued.")]), "")

    # REFLECT 2026-09-30 15:13 F1: 13:00:02's pasted "Stop hook error" (trimmed) got a status line at 13:01:02. A trace
    # is a question even inside a paste, answered only by quoting its failing line and naming its command.
    def test_a_pasted_error_trace_needs_its_failing_line_and_command(self) -> None:
        paste = ("\n\n<pasted_content id=\"2980\">\n\u23fa Ran 6 stop hooks\n  \u23bf  Stop hook error: Failed with "
                 "non-blocking status code: node:internal/modules/cjs/loader:1568\n    throw err;\n    ^\n\n  Error: "
                 "Cannot find module '/var/folders/3d/tqt777y96hg63_9p0kk8j6d40000gn/T/cmux-claude-node-options/"
                 "restore-node-options.cjs'\n  Require stack:\n  - internal/preload\n      at Module._resolveFilename "
                 "(node:internal/modules/cjs/loader:1564:15)\n    code: 'MODULE_NOT_FOUND',\n\n  Node.js v24.21.0\n"
                 "</pasted_content id=\"2980\">")
        pasted = ("2026-09-30T03:00:02Z", "sig", paste)
        status = ("2026-09-30T03:01:02Z", "claude", "Evaluating: E0396 (I1035), 468/2400 trials, due by 13:51.")
        reason = self.reason([pasted, status])
        self.assertIn("sig pasted an error trace", reason)
        self.assertIn("Error: Cannot find module '/var/folders/3d/", reason)
        self.assertNotIn("sig's question has no answer yet", reason)
        # Naming the hook without the failing line isn't enough; quoting it with its command is.
        self.assertIn("pasted an error trace", self.reason([pasted, ("2026-09-30T03:01:02Z", "claude",
                                                                    "It's cmux's `claude-hook` Stop hook, not ours.")]))
        self.assertEqual(self.stop([pasted, ("2026-09-30T03:01:02Z", "claude",
                                             "It failed at \"Cannot find module '/var/folders/3d/tqt777y96hg63_9p0kk8j6d40000"
                                             "gn/T/cmux-claude-node-options/restore-node-options.cjs'\": cmux's Stop hook "
                                             "(`cmux claude-hook stop`) preloads a file its temp folder lost.")]), "")

    # REFLECT 2026-09-30 15:13 F20: while the terse rule stands a long status reply blocks (11:10:22 ran 9 lines, 12:46:06 7);
    # evalline's own lines pasted into it don't count (12:44:01's three).
    def test_a_long_status_reply_blocks_while_the_terse_rule_stands(self) -> None:
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, str(TOOLS))
        import my_messages
        import orchestrator_stop_hook as hook
        memory = self.home / "quota-habits.md"
        memory.write_text("- **Terse main thread** (sig, 2026-09-29 20:42:00): ...\n")
        path = self.home / "t.jsonl"

        def run(reply: str) -> str:
            path.write_text(json.dumps({"type": "system", "subtype": "stop_hook_summary",
                                        "timestamp": "2026-09-30T02:44:00Z"}) + "\n" +
                            json.dumps({"type": "assistant", "timestamp": "2026-09-30T02:44:01Z",
                                        "message": {"content": [{"type": "text", "text": reply}]}}) + "\n")
            event = json.dumps({"session_id": self.SESSION, "transcript_path": str(path), "stop_hook_active": False})
            out = io.StringIO()
            with patch.dict(os.environ, {"BC_LOOP_HOME": str(self.home)}), patch.object(my_messages, "MEMORY", memory), \
                    patch("sys.stdin", io.StringIO(event)), redirect_stdout(out):
                self.assertEqual(hook.main(), 0)
            return out.getvalue()

        evalline = ("Evaluating: E0411 (I1108), SPRT since 12:42. Queued: E0396, E0398, E0399.\n- **Goal line:** 2 of 8 "
                    "in flight aim at a portal style.\n- **Agents:** none. 5 h usage 51%, resets 13:00.")
        long = evalline + "\n" + "\n".join(f"- E04{n:02} is coding." for n in range(5))
        reason = json.loads(run(long))["reason"]
        self.assertIn("ran over the line cap", reason)
        self.assertIn("5 lines long, over 3", reason)
        # (These have no evalline print behind them, which blocks on its own.)
        self.assertNotIn("line cap", run(evalline + "\n- E0412 handed in.\n- E0413 is coding.\n- E0414 is coding."))
        self.assertIn("4 lines long, over 3", run(evalline + "\n" + "\n".join(f"- E04{n:02} is coding." for n in
                                                                               range(4))))  # sig's "at most 2-3"
        memory.write_text("- **Wake the orchestrator less.**\n")
        self.assertNotIn("line cap", run(long))


class PromptHook(unittest.TestCase):
    """tools/orchestrator_prompt_hook.py (REFLECT 2026-09-30 09:12 F20): while sig's terse rule stands, a last reply
    over the cap gets a note naming its line count when he types; within the cap, under no rule or in another
    session, nothing."""

    def test_a_long_last_reply_gets_a_note_naming_its_length(self) -> None:
        import io
        from contextlib import redirect_stdout
        sys.path.insert(0, str(TOOLS))
        import my_messages
        import orchestrator_prompt_hook as hook
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "orchestrator-session").write_text(StopHook.SESSION)
            memory = home / "quota-habits.md"
            path = home / "t.jsonl"

            def run(reply: str, session: str = StopHook.SESSION) -> str:
                path.write_text(json.dumps({"type": "assistant", "timestamp": "2026-09-29T17:30:20Z",
                                            "message": {"content": [{"type": "text", "text": reply}]}}) + "\n")
                event = json.dumps({"session_id": session, "transcript_path": str(path), "prompt": "ok"})
                out = io.StringIO()
                with patch.dict(os.environ, {"BC_LOOP_HOME": folder}), patch.object(my_messages, "MEMORY", memory), \
                        patch("sys.stdin", io.StringIO(event)), redirect_stdout(out):
                    self.assertEqual(hook.main(), 0)
                return out.getvalue()

            memory.write_text("- **Terse main thread** (sig, 2026-09-29 20:42:00): ...\n")
            long = "Evaluating: E0384 ...\n" + "- a finding\n" * 14  # 03:30:20's 15 lines, evalline's not counted
            context = json.loads(run(long))["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Your last reply (03:30:20) ran 14 lines besides evalline's own, over the 3-line cap", context)
            self.assertEqual(run("Evaluating: E0384 ...\n\nOne line.\nTwo."), "")  # blank lines don't count
            self.assertEqual(run(long, session="another"), "")
            memory.write_text("- **Wake the orchestrator less.**\n")
            self.assertEqual(run(long), "")

    # REFLECT 2026-10-01 15:21 F10: sig back after over AWAY_HOURS gets every result since his previous message in
    # the note; the message being sent, already in the transcript, isn't that previous one, and within the hour nothing.
    def test_sig_back_after_an_hour_gets_the_results_digest(self) -> None:
        import io
        from contextlib import redirect_stdout
        from datetime import datetime, timedelta, timezone
        sys.path.insert(0, str(TOOLS))
        import my_messages
        import orchestrator_prompt_hook as hook
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "orchestrator-session").write_text(StopHook.SESSION)
            run_dir = home / "runs" / "E0499"
            run_dir.mkdir(parents=True)
            (run_dir / "experiment.json").write_text(json.dumps({
                "verdict": "discarded", "finished": (now - timedelta(minutes=30)).timestamp(),
                "idea": {"id": "I1209", "title": "Retest E0461"}, "sprt": {"decision": "H0", "elo0": 0}}))
            path = home / "t.jsonl"

            def run(away: timedelta) -> str:
                rows = [(now - away, "what were the findings since 9am"), (now, "back, anything new?")]
                path.write_text("".join(json.dumps({"type": "user", "timestamp": at.isoformat(), "origin": {
                    "kind": "human"}, "message": {"content": text}}) + "\n" for at, text in rows))
                event = json.dumps({"session_id": StopHook.SESSION, "transcript_path": str(path),
                                    "prompt": "back, anything new?"})
                out = io.StringIO()
                with patch.dict(os.environ, {"BC_LOOP_HOME": folder}), \
                        patch.object(my_messages, "MEMORY", home / "none.md"), \
                        patch("sys.stdin", io.StringIO(event)), redirect_stdout(out):
                    self.assertEqual(hook.main(), 0)
                return out.getvalue()

            context = json.loads(run(timedelta(hours=2)))["hookSpecificOutput"]["additionalContext"]
            self.assertIn("sig is back after 2.0 h away", context)
            self.assertIn("E0499 (I1209 Retest E0461) ended", context)
            self.assertEqual(run(timedelta(minutes=50)), "")
            self.assertEqual(len(my_messages.counted_lines(f"Evaluating: E0500\n{context.splitlines()[-1]}")), 0)


if __name__ == "__main__":
    unittest.main()
