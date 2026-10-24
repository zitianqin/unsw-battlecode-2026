"""The identical-games check's shared sonar key (identical.shared_key_copies) and digest pool size; plays no games.

    cd referee && ../.venv/bin/python -m unittest test_identical
"""

import testhome  # before anything that reads live paths
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import identical

PARAMS = "struct Params {\n    bool sonarBuildKey = true;   // the tag's key is a hash of this build's source\n};\n"


def bot(root: Path, name: str, params: str = PARAMS, key: str = "// key\n") -> Path:
    folder = root / name / "bot"
    folder.mkdir(parents=True)
    (folder / "params.hpp").write_text(params)
    (folder / "sonarkey.hpp").write_text(key)
    (folder / "main.cpp").write_text("int main() {}\n")
    return folder


class SharedKey(unittest.TestCase):
    def test_both_copies_use_the_fixed_key_and_the_originals_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cand, base = bot(root, "cand"), bot(root, "base")
            (cand / "main.cpp").write_text("int main() {}  // a comment changes the build key\n")
            copies = identical.shared_key_copies(cand, base, root / "copies")
            for copy in copies:
                self.assertIn("bool sonarBuildKey = false;", (copy / "params.hpp").read_text())
            self.assertIn("a comment", (copies[0] / "main.cpp").read_text())
            self.assertEqual((cand / "params.hpp").read_text(), PARAMS)

    def test_a_change_to_the_key_itself_is_left_to_show(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cand, base = bot(root, "cand", key="// a new key\n"), bot(root, "base")
            self.assertEqual(identical.shared_key_copies(cand, base, root / "copies"), (cand, base))
            old = bot(root, "old", params="struct Params {};\n")  # a champion from before the switch
            self.assertEqual(identical.shared_key_copies(cand, old, root / "copies2"), (cand, old))


class DigestWorkers(unittest.TestCase):
    # The replay digests after the games ran a pool of half the cores whatever this Mac's load (MISSES 53's review):
    # they take no more than the game slots the load gate gives now, and one when it gives none.
    def test_the_digest_pool_keeps_to_this_macs_game_slots(self) -> None:
        with patch.object(identical.os, "cpu_count", return_value=16):
            for slots, expected in ((0, 1), (2, 2), (20, 8)):
                with patch.object(identical.run, "game_slots", return_value=slots) as gate:
                    self.assertEqual(identical.digest_workers("auto"), expected)
                gate.assert_called_once_with("auto")
        with patch.object(identical.os, "cpu_count", return_value=4), \
                patch.object(identical.run, "game_slots", return_value=8):
            self.assertEqual(identical.digest_workers(3), 2)


if __name__ == "__main__":
    unittest.main()
