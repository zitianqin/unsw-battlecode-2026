"""bceval's GitHub sync (tools/bceval_sync.py) on a local stand-in for the GitHub repo: a change to the weights alone
is fast-forwarded and used, a change to code is held for review (the clone stays at the approved commit) until
--approve, as is a weights file turned into a symlink, and a changed .git/config stops every run until trusted.

    PYTHONPATH=loop .venv/bin/python -m unittest tools.test_bceval_sync
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1] / "loop"))  # testhome, without PYTHONPATH=loop
import testhome  # noqa: E402,F401  (before anything that reads live paths)

import bceval_sync  # noqa: E402


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args], cwd=folder,
                          check=True, capture_output=True, text=True).stdout.strip()


class Sync(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base)])
        self.remote, self.clone, self.home = base / "github", base / "clone", base / "home"
        (self.remote / "bceval").mkdir(parents=True)
        git(self.remote, "init", "-q", "-b", bceval_sync.BRANCH)
        self.commit({"bceval/model.json": self.model(1), "bceval/score.py": "W = 1\n"})
        git(base, "clone", "-q", str(self.remote), str(self.clone))
        self.first = git(self.clone, "rev-parse", "HEAD")
        self.home.mkdir()
        bceval_sync.save_state(self.home, {"approved": self.first})
        self.now = 10**9

    @staticmethod
    def model(w: float) -> str:
        return ('{"knots": [0, 500], "features": {"alive": "log"}, "weights": {"alive": [%s, 1]}, '
                '"lin_scale": {}}\n' % w)

    def commit(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            (self.remote / name).write_text(text)
        git(self.remote, "add", "-A")
        git(self.remote, "commit", "-qm", "change")
        return git(self.remote, "rev-parse", "HEAD")

    def sync(self) -> str:
        self.now += bceval_sync.FETCH_EVERY
        return bceval_sync.sync(self.home, self.clone, self.now)

    def test_weights_fast_forward_and_code_is_held_until_approved(self):
        weights = self.commit({"bceval/model.json": self.model(2)})
        self.assertEqual(self.sync(), weights)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), weights)

        code = self.commit({"bceval/score.py": "import os\n", "bceval/model.json": self.model(3)})
        self.assertEqual(self.sync(), weights)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), weights)  # nothing unreviewed is checked out
        self.assertEqual(bceval_sync.read_state(self.home)["held"]["sha"], code)
        alerts = (self.home / "host-alerts.log").read_text()
        self.assertIn("BCEVAL UPDATE HELD: bceval/model.json, bceval/score.py, review with git", alerts)
        self.assertIn(f"review with {bceval_sync.review_command(self.clone, weights, code)}", alerts)
        self.assertIn("diff --no-ext-diff --no-textconv", alerts)
        self.assertEqual(bceval_sync.ready(self.home, self.clone), (self.clone, weights))

        bceval_sync.approve(code[:12], self.home, self.clone, self.now)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), code)
        self.assertIsNone(bceval_sync.read_state(self.home)["held"])

        # A filter or program the clone's config gained outside the loop: nothing runs until --trust.
        with (self.clone / ".git/config").open("a") as handle:
            handle.write('[filter "x"]\n\tsmudge = touch /tmp/pwned\n')
        clone, why = bceval_sync.ready(self.home, self.clone)
        self.assertIsNone(clone)
        self.assertIn("--trust", why)
        bceval_sync.trust(self.home, self.clone)
        self.assertEqual(bceval_sync.ready(self.home, self.clone), (self.clone, code))

    def test_a_weights_file_turned_symlink_is_held(self):
        # A link at model.json would have passed as a weights update and been read through (review of a0bcacc).
        (self.remote / "bceval/model.json").unlink()
        os.symlink("/etc/hosts", self.remote / "bceval/model.json")
        link = self.commit({})
        self.assertEqual(self.sync(), self.first)
        self.assertEqual(bceval_sync.read_state(self.home)["held"]["sha"], link)
        self.assertFalse((self.clone / "bceval/model.json").is_symlink())


if __name__ == "__main__":
    unittest.main()
