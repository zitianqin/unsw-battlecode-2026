import testhome  # before anything that reads live paths
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import health
import pack

CLAUDE = {"session_id": "3fc9e284-813c-478c-94d1-bb8254c72882"}


def row(eid: str, verdict: str, clone: str | None = None) -> dict:
    return {"id": eid, "verdict": verdict, "session": {**CLAUDE, **({"clone": clone} if clone else {})},
            "started": pack.prune.POSTMORTEMS_FROM, "sprt": {"decision": "H0"}, "idea": {"id": "I1"}}


class TestPack(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.state, self.clones = Path(scratch.name) / "state", Path(scratch.name) / "bc-wt"
        self.state.mkdir()

    def clone(self, name: str, age: float = 4 * 3600) -> Path:
        clone = self.clones / name
        (clone / "data" / "bots").mkdir(parents=True)
        (clone / "data" / "empty").mkdir()
        (clone / "data" / "bots" / "0001-gen-aA.replay").write_bytes(os.urandom(5000))
        (clone / "data" / "link").symlink_to("bots/0001-gen-aA.replay")
        (clone / ".postmortem").mkdir()
        (clone / ".postmortem" / "notes.txt").write_text("loss 3 of 40\n")
        (clone / "bot").mkdir()
        (clone / "bot" / "main.cpp").write_text("int main() {}\n")
        then = time.time() - age
        for path in clone.rglob("*"):
            os.utime(path, (then, then), follow_symlinks=False)
        return clone

    def test_the_latest_result_on_a_clone_decides_and_an_owed_turn_or_recent_write_holds_it(self) -> None:
        for name in ("E0001", "E0002", "E0003", "E0004", "E0005"):
            self.clone(name)
        self.clone("E0006", age=600)
        (self.state / "runs" / "E0003").mkdir(parents=True)
        (self.state / "runs" / "E0003" / "postmortem.json").write_text('{"result": "done"}')
        rows = [row("E0001", "error"), row("E0002", "error"), row("E0009", "discarded", clone="E0002"),
                row("E0003", "discarded"), row("E0004", "discarded"), row("E0006", "kept")]
        with patch.object(pack, "live_clones", lambda clones: set()):
            found = {c.name: why for c, why in pack.finished(self.state, self.clones, rows, time.time()).items()}
        self.assertEqual(found["E0001"], "its latest result E0001 is an error")  # may be re-evaluated
        self.assertEqual(found["E0002"], "owed a coder turn: E0009")  # its re-evaluation's turn, not its own error's
        self.assertEqual(found["E0003"], "")
        self.assertEqual(found["E0004"], "owed a coder turn: E0004")
        self.assertEqual(found["E0005"], "no result recorded (in flight)")
        self.assertEqual(found["E0006"], "a file changed in the last 3 h")

    def test_a_pack_keeps_every_file_and_link_and_removes_only_the_folders(self) -> None:
        clone = self.clone("E0001")
        before = subprocess.run(["find", ".", "-not", "-path", "./bot*"], cwd=clone, capture_output=True,
                                text=True).stdout
        replay = (clone / "data" / "bots" / "0001-gen-aA.replay").read_bytes()
        archive, _, _ = pack.pack(clone, pack.targets(clone))
        self.assertEqual(sorted(p.name for p in clone.iterdir()), ["bot", "replays.tar.zst"])
        subprocess.run(["tar", "--zstd", "-xf", archive.name], cwd=clone, check=True)
        after = subprocess.run(["find", ".", "-not", "-path", "./bot*", "-not", "-name", "*.zst"], cwd=clone,
                               capture_output=True, text=True).stdout
        self.assertEqual(sorted(after.split()), sorted(before.split()))
        self.assertEqual((clone / "data" / "bots" / "0001-gen-aA.replay").read_bytes(), replay)
        self.assertEqual(os.readlink(clone / "data" / "link"), "bots/0001-gen-aA.replay")
        self.assertEqual(pack.pack(clone, pack.targets(clone))[0].name, "replays-2.tar.zst")  # a second pack

    def test_a_tick_records_what_it_packed_and_skips_until_its_interval(self) -> None:
        self.clone("E0003")
        (self.state / "runs" / "E0003").mkdir(parents=True)
        (self.state / "runs" / "E0003" / "postmortem.json").write_text('{"result": "done"}')
        (self.state / "results.jsonl").write_text(json.dumps(row("E0003", "discarded")) + "\n")
        with patch.object(pack, "live_clones", lambda clones: set()), \
                patch("home.clones_folder", lambda repo: self.clones):
            self.assertIn("E0003", pack.tick(Path("repo"), self.state))
            self.assertEqual(pack.tick(Path("repo"), self.state), "")
        status = json.loads((self.state / pack.STATUS).read_text())
        self.assertEqual((status["packed"], status["failed"], status["unpacked"]), (["E0003"], {}, {}))
        self.assertEqual(health.clone_replays(self.state), [])
        (self.state / pack.STATUS).write_text(json.dumps({"at": 0, "packed": [], "failed": {"E0004": "RuntimeError"},
                                                          "unpacked": {"E0004": pack.LIMIT + 1}}))
        self.assertEqual([a.key for a in health.clone_replays(self.state)],
                         ["clone-replays:failed:E0004", "clone-replays:behind"])


if __name__ == "__main__":
    unittest.main()
