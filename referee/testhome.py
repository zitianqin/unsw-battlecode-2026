"""Import first in every test module here (`import testhome  # before anything that reads live paths`). Points
$BC_LOOP_HOME at a fresh temporary folder, so every live path (the state folder, the evaluation marker, the
referee's host status and alerts) lands there and no test can touch the running loop's files."""

import atexit
import os
import shutil
import tempfile

os.environ["BC_LOOP_HOME"] = tempfile.mkdtemp(prefix="testhome-")
atexit.register(shutil.rmtree, os.environ["BC_LOOP_HOME"], ignore_errors=True)
# As every game launch the loop makes (driver.py, host_agent.py): the toolkit's "a newer version is out" notice lands
# in a game's stderr, which the tests compare, and its check reaches PyPI.
os.environ.setdefault("UNSWBC_NO_UPDATE", "1")
os.environ.setdefault("UNSWBC_NO_VSCODE", "1")
