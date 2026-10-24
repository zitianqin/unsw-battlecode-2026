"""Checks tools/sonar: a spec entry written by formats.entry_toml loads back and one whose field overlaps its
recogniser is refused; the field search finds a planted SSS-style layout (tag, x, y, round, length) and its recogniser;
a report row becomes the entry it states.

    .venv/bin/python -m unittest tools.sonar.test_sonar
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import discover  # noqa: E402
import formats  # noqa: E402
import reports  # noqa: E402

ENTRY = dict(team=91, name="SSS", version="test", kind="report", source="test", note="n",
             layout=[dict(mask="0xfff0000000000000", value="0xf5e0000000000000", x=(25, 6), y=(19, 6),
                          round=(10, 9), length=(31, 7))])


class Spec(unittest.TestCase):
    def test_an_entry_round_trips_and_an_overlap_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            spec = Path(d) / "spec.toml"
            spec.write_text(formats.entry_toml(ENTRY))
            [back] = formats.load(spec, Path(d) / "none.json")
            self.assertEqual(back["layout"][0]["x"], [25, 6])
            self.assertIn("SSS", formats.render([back]))
            bad = dict(ENTRY, version="bad", layout=[dict(ENTRY["layout"][0], x=(50, 6))])
            with self.assertRaises(ValueError):
                formats.append([bad], spec)


class Discovery(unittest.TestCase):
    # Values of one team: 12-bit tag 0xf5e, x at 25, y at 19, length at 31, round at 10, noise below; each names a
    # head of the team at the round it states.
    def test_the_field_search_finds_a_planted_layout(self) -> None:
        rng = random.Random(3)
        rows = []
        for _ in range(400):
            x, y, length, rnd = rng.randrange(40), rng.randrange(30), rng.randrange(2, 128), rng.randrange(20, 480)
            v = 0xF5E << 52 | length << 31 | x << 25 | y << 19 | rnd << 10 | rng.getrandbits(10)
            others = [(rng.randrange(40), rng.randrange(30), rng.randrange(2, 9), 7, rnd) for _ in range(6)]
            rows.append(discover.Row(v, 40, 30, [(x, y, length, 5, rnd), *others]))
        [layout] = discover.layouts(rows)
        self.assertEqual((layout["x"], layout["y"]), ((25, 6), (19, 6)))
        self.assertEqual((layout["length"], layout["round"]), ((31, 7), (10, 9)))
        mask, value = int(layout["mask"], 16), int(layout["value"], 16)
        self.assertEqual((mask >> 52, value >> 52), (0xFFF, 0xF5E), "the tag is the recogniser")


class Reports(unittest.TestCase):
    def test_a_report_row_becomes_the_entry_it_states(self) -> None:
        text = ("## Master Protocol Reference Table\n\n| Rank | Team | ID | Elo | Pings | Family | Layout | Status |\n"
                "| 3 | Stockfish | 206 | 2080 | 1 | Custom Plaintext Bitfield | Bits 22..27 X \\| 16..21 Y | ok |\n"
                "| 5 | SSS | 91 | 2054 | 1 | SSS Telemetry Family | SSS Magic `0xf5e` | ok |\n"
                "| 9 | quiet | 27 | 1900 | 0 | Silent (No Sonar) | N/A | N/A |\n")
        stock, sss, quiet = (reports.entry(c, "r", "t") for c in reports.master_rows(text))
        self.assertEqual((stock["layout"][0]["x"], stock["layout"][0]["y"]), ((22, 6), (16, 6)))
        self.assertEqual(sss["layout"][0]["value"], "0xf5e0000000000000")
        self.assertEqual(quiet["kind"], "silent")


if __name__ == "__main__":
    unittest.main()
