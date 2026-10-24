"""The compiled-code identity check (samecode.check), built by the toolkit as the judge builds a submission: on the
repo's bot a comment and a rename build the same code and a changed constant doesn't; on the champion's shape
(v0056, P.sonarBuildKey on, so its real build hashes its own source) a comment still passes, the key fixed; and only
what the judge receives is built, a differing bot.toml refused.

    cd referee && ../.venv/bin/python -m unittest test_samecode
"""

import testhome  # before anything that reads live paths
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import samecode
from identical import SONAR_KEY

REPO = Path(__file__).resolve().parent.parent
BOT = REPO / "bot"
KEYED = "v0056"  # a champion with P.sonarBuildKey on and its sonarkey.hpp


class SameCode(unittest.TestCase):
    def test_a_comment_or_rename_builds_the_same_code_and_a_constant_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            renamed, changed, keyed, commented = (root / name / "bot" for name in ("renamed", "changed", "keyed",
                                                                                   "commented"))
            for copy in (renamed, changed):
                shutil.copytree(BOT, copy)
            main = (renamed / "main.cpp").read_text()
            self.assertIn("MoveEval const move ", main)  # the local renamed below
            (renamed / "main.cpp").write_text("// a comment that changes no move\n"
                                              + re.sub(r"\bmove\b", "chosen", main))
            params = (changed / "params.hpp").read_text()
            first = re.search(r"(int\s+\w+\s*=\s*)(\d+)", params)
            (changed / "params.hpp").write_text(params[:first.start(2)] + str(int(first[2]) + 1) + params[first.end(2):])
            same, line, sizes = samecode.check(renamed, BOT)
            self.assertTrue(same, line)
            self.assertEqual(sizes["cand"]["sha256"], sizes["base"]["sha256"])
            self.assertFalse(samecode.check(changed, BOT)[0])

            # The boundary, the champion's shape: with the key on a comment changes the real build's code (measured in
            # samecode's docstring), but not the code with the key fixed.
            archive = subprocess.run(["git", "-C", str(REPO), "archive", KEYED, "bot"], capture_output=True,
                                     check=True).stdout
            for copy in (keyed, commented):
                tarfile.open(fileobj=io.BytesIO(archive)).extractall(copy.parent, filter="data")
            self.assertRegex((keyed / "params.hpp").read_text(), SONAR_KEY)
            (commented / "main.cpp").write_text("// a comment\n" + (keyed / "main.cpp").read_text())
            same, line, _ = samecode.check(commented, keyed)
            self.assertTrue(same, line)

            # Only what the judge receives: bot.toml's include list, not a stray file; another bot.toml is refused.
            (keyed / "notes.txt").write_text("not sent")
            self.assertNotIn("notes.txt", {p.name for p in samecode.submitted(keyed, root / "sent").iterdir()})
            (commented / "bot.toml").write_text((commented / "bot.toml").read_text().replace('"*.c", ', ""))
            self.assertEqual(samecode.check(commented, keyed), (False, samecode.TOML_DIFFERS, {}))


if __name__ == "__main__":
    unittest.main()
