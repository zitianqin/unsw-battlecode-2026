"""bceval's reading of a real site replay whose map text withholds pearl timings (testdata/trauma-392179.replay):
the timings are filled from the map file of that name, side A has a turning point from r100 on carried by one of the
groups, and a second read comes from the cache without running bceval. Needs bceval's clone and venv
(bceval_sync.CLONE, VENV_PY); skipped without them.

    PYTHONPATH=loop .venv/bin/python -m unittest tools.test_bceval_read
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parents[1] / "loop"))  # testhome, without PYTHONPATH=loop
import testhome  # noqa: E402,F401  (before anything that reads live paths)

import bceval_read  # noqa: E402
import bceval_sync  # noqa: E402

REPLAY = Path(__file__).resolve().parent / "testdata/trauma-392179.replay"


@unittest.skipUnless(bceval_sync.CLONE.is_dir() and bceval_sync.VENV_PY.exists(), "bceval's clone or venv is missing")
class Read(unittest.TestCase):
    def test_withheld_timings_filled_turning_point_and_cache(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(home)])
        head = bceval_sync.git(bceval_sync.CLONE, "rev-parse", "HEAD")
        bceval_sync.save_state(home, {"approved": head, "fetched_at": time.time()})  # no fetch in a test
        reading = bceval_read.read(REPLAY, home)
        self.assertTrue(reading["rates"].startswith("filled from "), reading["rates"])
        self.assertIn("trauma.map", reading["rates"])
        self.assertEqual([r for r, _, _ in reading["rows"][:3]], [1, 5, 10])
        turn = bceval_read.turning(reading, "A")  # not the model's exact output: a weights update may move it
        self.assertIn(turn["group"], bceval_read.GROUPS)
        self.assertGreaterEqual(turn["round"], bceval_read.START)
        self.assertGreaterEqual(turn["fall"], bceval_read.MIN_FALL)
        child = Mock(run=Mock(side_effect=AssertionError("not cached")))  # bceval_read's own, not git's
        with patch.object(bceval_read, "subprocess", child):
            self.assertEqual(bceval_read.read(REPLAY, home), reading)


if __name__ == "__main__":
    unittest.main()
