"""Test copies on fixed sonar keys (sharedkey.test_copy, key_code), on real bots: the champion v0071 and E0348's
candidate (46fc10e, which added doorway.hpp to sonarkey.hpp's list); plays no games (MISSES 99 measured them).

    cd referee && ../.venv/bin/python -m unittest test_sharedkey
"""

import testhome  # before anything that reads live paths
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import sharedkey

REPO = Path(__file__).resolve().parent.parent


def export(commit: str, into: Path) -> Path:
    archive = subprocess.run(["git", "-C", str(REPO), "archive", commit, "bot"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(archive)).extractall(into, filter="data")
    return into / "bot"


class TestKeys(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.champion = export("v0071", self.root / "champion")

    def test_a_copy_plays_the_fixed_key_and_head_to_head_another_and_the_bot_is_untouched(self) -> None:
        fixed = sharedkey.test_copy(self.champion, self.root / "copies")
        alt = sharedkey.test_copy(self.champion, self.root / "copies", "alt")
        self.assertRegex((self.champion / "params.hpp").read_text(), sharedkey.SONAR_KEY)  # the real build keeps it
        for copy in (fixed, alt):
            self.assertNotRegex((copy / "params.hpp").read_text(), sharedkey.SONAR_KEY)
            self.assertEqual((copy / "main.cpp").read_bytes(), (self.champion / "main.cpp").read_bytes())
        own = sharedkey.test_copy(self.champion, self.root / "copies", "own")  # as an opponent plays it
        keys = [sharedkey.FIXED.search((bot / "sonarkey.hpp").read_text())[2]
                for bot in (self.champion, fixed, alt, own)]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(int(keys[2], 16), int(keys[0], 16) ^ sharedkey.ALT)
        self.assertEqual(len(set(keys[1:])), 3)
        self.assertEqual(sharedkey.test_copy(self.champion, self.root / "copies"), fixed)  # named by contents: reused
        (self.champion / "main.cpp").write_text("// a comment\n" + (self.champion / "main.cpp").read_text())
        self.assertNotEqual(sharedkey.test_copy(self.champion, self.root / "copies"), fixed)

    def test_a_new_module_in_the_key_list_is_the_same_key_code_and_a_key_made_another_way_is_not(self) -> None:
        cand = export("46fc10e9", self.root / "cand")
        self.assertIn('#embed "doorway.hpp"', (cand / "sonarkey.hpp").read_text())
        self.assertEqual(sharedkey.key_code(cand), sharedkey.key_code(self.champion))
        key = cand / "sonarkey.hpp"
        key.write_text(key.read_text().replace("P.sonarBuildKey && HAS_BUILD_KEY ? mix(SOURCE_HASH ^ KEY)",
                                               "P.sonarBuildKey && HAS_BUILD_KEY ? mix(SOURCE_HASH + KEY)"))
        self.assertNotEqual(sharedkey.key_code(cand), sharedkey.key_code(self.champion))

    def test_a_candidate_that_flips_the_switch_is_another_key_code(self) -> None:
        # Its test copy would switch it off like the champion's, erasing the change under test.
        cand = export("v0071", self.root / "cand")
        self.assertEqual(sharedkey.key_code(cand), sharedkey.key_code(self.champion))
        params = cand / "params.hpp"
        params.write_text(sharedkey.SONAR_KEY.sub(r"\1false", params.read_text()))
        self.assertNotEqual(sharedkey.key_code(cand), sharedkey.key_code(self.champion))


if __name__ == "__main__":
    unittest.main()
