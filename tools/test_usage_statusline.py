"""tools/usage_statusline.py: each usage level alerts once a window, a new window alerts and starts over, and the status
line never errors.

    .venv/bin/python -m unittest tools.test_usage_statusline
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import usage_statusline as usage  # noqa: E402

RESETS = 1_790_700_000.0  # a window's end; it began 5 h before


def at(hours: float) -> float:
    return RESETS - usage.WINDOW_S + hours * 3600


class Crossings(unittest.TestCase):
    def test_a_window_saved_at_the_old_98_level_stays_at_the_top_level(self) -> None:
        lines, saved = usage.crossings({"resets_at": RESETS, "rank": 4}, 99, RESETS, at(4.9))
        self.assertEqual((lines, saved["rank"]), ([], 3))
        self.assertEqual(usage.marker(saved, {}, at(4.9))["level"], "95%")

    def run_through(self, readings: list[tuple[float, float, float]]) -> list[list[str]]:
        saved, fired = {}, []
        for used, resets, now in readings:
            lines, saved = usage.crossings(saved, used, resets, now)
            fired.append([line.split(":")[0] for line in lines])
        return fired

    def test_each_level_alerts_once_a_window_and_a_new_window_starts_over(self) -> None:
        fired = self.run_through([
            (15, RESETS, at(1)),     # 20% of the window gone: 15% isn't 5 points ahead
            (31, RESETS, at(1)),     # 11 points ahead but under AHEAD_FLOOR
            (61, RESETS, at(2.5)),   # 11 points ahead of the half gone, past the floor
            (30, RESETS, at(3)),     # behind again, then ahead again: still once this window
            (75, RESETS, at(3.2)),
            (86, RESETS, at(4)),
            (90, RESETS, at(4.2)),
            (96, RESETS, at(4.5)),
            (99, RESETS, at(4.9)),   # no level above 95% (98% was one until 30 Sep)
            (2, RESETS + usage.WINDOW_S, at(5.1)),  # the next window
            (97, RESETS + usage.WINDOW_S, at(5.2)),  # straight past every level: its highest only
        ])
        self.assertEqual(fired, [[], [], ["USAGE ahead"], [], [], ["USAGE 85%"], [], ["USAGE 95%"], [],
                                 ["USAGE new 5 h window"], ["USAGE 95%"]])

    def test_a_line_says_the_share_the_reset_and_what_to_do(self) -> None:
        (line,), saved = usage.crossings({"resets_at": RESETS, "rank": 0}, 86.4, RESETS, at(4))
        self.assertEqual(line, f"USAGE 85%: 5 h usage 86% with 80% of the window gone, resets {usage.hhmm(RESETS)}: "
                               "REFLECT and meta reviews wait for the new window; write handoff notes")
        self.assertEqual((saved["rank"], saved["used_percentage"]), (2, 86.4))
        self.assertEqual(usage.crossings(saved, 88, RESETS + 30, at(4.1))[0], [])  # a jittered resets_at: same window

    def test_the_script_alerts_writes_its_record_and_never_errors(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            def run(stdin: str) -> str:
                done = subprocess.run([sys.executable, str(Path(usage.__file__))], input=stdin, text=True,
                                      capture_output=True, env={**os.environ, "BC_LOOP_HOME": home}, timeout=10)
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                return done.stdout
            reading = {"rate_limits": {"five_hour": {"used_percentage": 96, "resets_at": RESETS}}}
            self.assertEqual(run(json.dumps(reading)), f"5h 96% (resets {usage.hhmm(RESETS)})\n")
            self.assertEqual(json.loads((Path(home) / usage.USAGE).read_text())["rank"], 3)
            self.assertEqual((Path(home) / "host-alerts.log").read_text().count(" USAGE 95%: "), 1)
            run(json.dumps(reading))
            self.assertEqual((Path(home) / "host-alerts.log").read_text().count("USAGE"), 1)
            level = json.loads((Path(home) / usage.LEVEL).read_text())
            self.assertEqual((level["level"], level["rank"], level["resets_at"]), ("95%", 3, RESETS))
            (wake,) = [json.loads(line) for line in (Path(home) / "reminders.jsonl").read_text().splitlines()]
            self.assertTrue(wake["text"].startswith(f"USAGE window reset at {usage.hhmm(RESETS)}"))
            self.assertEqual(wake["due"], datetime.fromtimestamp(RESETS + usage.RESET_MARGIN_S).astimezone()
                             .isoformat(timespec="minutes"))
            run(json.dumps(reading))  # the same level: the marker keeps its start, and no second wake
            self.assertEqual(len((Path(home) / "reminders.jsonl").read_text().splitlines()), 1)
            self.assertEqual(json.loads((Path(home) / usage.LEVEL).read_text())["since"], level["since"])
            later = {"rate_limits": {"five_hour": {"used_percentage": 3, "resets_at": RESETS + usage.WINDOW_S}}}
            run(json.dumps(later))  # a new window clears it
            self.assertFalse((Path(home) / usage.LEVEL).exists())
            for bad in ("", "not json", "[]", json.dumps({"rate_limits": {"five_hour": {"used_percentage": "x"}}})):
                self.assertEqual(run(bad), "")


if __name__ == "__main__":
    unittest.main()
