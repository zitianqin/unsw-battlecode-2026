"""Focused mirror checks; no network or browser is started."""

import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

import mirror_docs


class BrowserFetch(unittest.TestCase):
    def test_browser_fetch_uses_json_to_preserve_html_and_escapes(self):
        with patch.dict("os.environ", {"GSTACK_BROWSE": "/tool/browse"}), \
                patch("subprocess.run", return_value=CompletedProcess([], 0, '"<article>64-bit</article>"\n', "")) as run:
            self.assertEqual(mirror_docs.fetch("https://game.battlecode.au/docs/sonar"),
                             "<article>64-bit</article>")
            self.assertEqual(run.call_args.args[0][:2], ["/tool/browse", "js"])


if __name__ == "__main__":
    unittest.main()
