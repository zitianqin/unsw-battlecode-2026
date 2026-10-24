"""Import first in every test module here (`import testhome  # before anything that reads live paths`). Points
$BC_LOOP_HOME at a fresh temporary folder, so every live path (the state folder, the evaluation marker, the
referee's host status and alerts) lands there and no test can touch the running loop's files. Sets $BCEVAL_OFF too,
so no report a test builds reads games with bceval (bceval_read.enabled: no GitHub fetch, no added section), and
drops $BC_REMOTE_HOSTS, which replaces remote.json (remote.load_hosts), so no test reaches a real game host.

Raises ImportError when home or meta was imported first (LIVE_FIRST): meta fixes its paths (meta.STATE) from the
loop home at import, so a test module or harness that loaded it before this ran writes the live ~/.bc-loop (29 Sep
16:06: a review harness wrote 39 fake "valid_proposals: refused" alerts there). A tools/ test module imports it
too, with loop/ appended to its path (tools/test_fit.py)."""

import atexit
import os
import shutil
import sys
import tempfile

LIVE_FIRST = ("home", "meta")

if early := [name for name in LIVE_FIRST if name in sys.modules]:
    raise ImportError(f"testhome imported after {', '.join(early)}, which already read the live loop home: import "
                      "testhome first in the test module (and in any harness that runs it)")

os.environ["BC_LOOP_HOME"] = tempfile.mkdtemp(prefix="testhome-")
os.environ["BCEVAL_OFF"] = "1"
os.environ.pop("BC_REMOTE_HOSTS", None)
atexit.register(shutil.rmtree, os.environ["BC_LOOP_HOME"], ignore_errors=True)
