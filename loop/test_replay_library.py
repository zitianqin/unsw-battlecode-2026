"""The replay library's selection, refresh and freshness check (loop/replay_library.py; its loader,
tools/replays.library_games, is tested in tools/test_replays.py).

Run from loop/: python -m unittest test_replay_library"""

import testhome  # before anything that reads live paths
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import health
import replay_library

# Real rows of the live state on 2026-09-29 (summaries cut to map and result): one of our saved games for each way the
# selection treats one ("case": ranked; unranked against a submission a ranked game of ours shows, and the ranked game
# that shows it; unranked with no opponent submission, since the site stopped naming them; unranked against one never
# seen ranked; a loss kept because we lost; a dev file left beside pruned wins; a dev game in the sample), the leader
# study's ranked and unranked games of a leader and a game of no leader, and valuenet folders' rows (Cutlery's
# ranked games; calc's unranked ones).
SAMPLE = json.loads((Path(__file__).parent / "testdata/replay-library-sample.json").read_text())


# Writes SAMPLE as a loop state under `state`, every replay an empty file there.
def write_state(state: Path) -> None:
    (state / "online.json").write_text(json.dumps(SAMPLE["online_json"]))
    for sid, index in SAMPLE["online"].items():
        folder = state / "online-replays" / sid
        folder.mkdir(parents=True)
        games = []
        for game in index["games"]:
            replay = folder / Path(game["replay"]).name
            replay.write_bytes(b"")
            games.append({**game, "replay": str(replay)})
        (folder / "index.json").write_text(json.dumps({"games": games, "results": index["results"]}))
    study = state / "study-replays"
    folders = [(study, SAMPLE["study"])] + [(study / "valuenet" / n, r) for n, r in SAMPLE["valuenet"].items()]
    for folder, rows in folders:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        for row in rows:
            (folder / f"{row['match']}.replay").write_bytes(b"")
    (state / "study-history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in SAMPLE["history"]))


def case_ids(*cases: str) -> set[int]:
    return {g["id"] for index in SAMPLE["online"].values() for g in index["games"] if g["case"] in cases}


class TestReplayLibrary(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(dir=os.environ["BC_LOOP_HOME"]))
        write_state(self.state)

    def test_ours_are_result_blind_non_dev_whatever_the_opponent(self):
        # A decoy opponent doesn't make our own bot's play fake: dropping ours against one cut our library rate to
        # 55.6% from 68.9% over every non-dev game on 2026-09-29. They count, flagged.
        library = replay_library.build(self.state)["online"]
        self.assertEqual({g["id"] for g in library["games"]},
                         case_ids("ranked", "unranked_seen", "ranked_evidence", "unranked_noid", "unranked_unseen"))
        self.assertEqual({g["id"] for g in library["games"] if g["decoy_risk"]},
                         case_ids("unranked_noid", "unranked_unseen"))
        # Kept losses, pruned dev files and dev games are out, though every file is on disk.
        self.assertEqual(library["left_out"], {"file_selected": 2, "dev": 1, "no_file": 0, "no_summary": 0})

    def test_leaders_are_ranked_or_a_submission_seen_ranked(self):
        library = replay_library.build(self.state)["leaders"]
        ranked, unranked, nobody = SAMPLE["study"]
        counted = {(g["source"], g["id"]) for g in library["games"]}
        self.assertIn(("study", ranked["match"]), counted)
        self.assertIn(("study", unranked["match"]), {(g["source"], g["id"]) for g in library["decoy_risk"]})
        self.assertNotIn(nobody["match"], {g["id"] for g in library["games"] + library["decoy_risk"]})
        self.assertEqual(library["left_out"]["no_leader"], 1)
        # A valuenet folder's rows are its team's side only: Cutlery's (306) in every cutlery-ranked row.
        cutlery = [g for g in library["games"] if g["source"] == "valuenet/cutlery-ranked"]
        self.assertEqual(len(cutlery), 3)
        self.assertEqual({g["team_id"] for g in cutlery}, {306})
        # calc's unranked games name no submission (the site stopped naming them), so none counts.
        self.assertFalse([g for g in library["games"] if g["source"] == "valuenet/calc-all"])
        self.assertEqual(len([g for g in library["decoy_risk"] if g["source"] == "valuenet/calc-all"]), 3)

    def test_refresh_is_idempotent_and_follows_its_sources(self):
        path = self.state / replay_library.LIBRARY
        self.assertTrue(replay_library.refresh(self.state))
        first = path.read_bytes()
        self.assertEqual(replay_library.refresh(self.state), "")
        self.assertEqual(path.read_bytes(), first)
        # A source rewritten with the same games: the library takes its time (fresh for health), not a rewrite.
        index = self.state / "online-replays/10072/index.json"
        future = time.time() + 5
        os.utime(index, (future, future))
        self.assertEqual(replay_library.refresh(self.state), "")
        self.assertEqual(path.read_bytes(), first)
        self.assertGreaterEqual(path.stat().st_mtime, future)
        # A counted game removed from its index goes from the library at the next refresh.
        rows = json.loads(index.read_text())
        (gone,) = case_ids("ranked")
        rows["games"] = [g for g in rows["games"] if g["id"] != gone]
        index.write_text(json.dumps(rows))
        os.utime(index, (future + 5, future + 5))
        self.assertTrue(replay_library.refresh(self.state))
        library = json.loads(path.read_text())
        self.assertNotIn(gone, {g["id"] for g in library["online"]["games"]})

    def test_health_alerts_only_when_the_library_falls_behind(self):
        now = time.time()
        # Missing while its sources exist.
        self.assertEqual([a.key for a in health.replay_library(self.state, now)], ["replay library"])
        replay_library.refresh(self.state)
        self.assertEqual(health.replay_library(self.state, now), [])
        # Games keep arriving (a source changed a minute ago) but the library was last matched 3 h ago: its refresh
        # keeps raising. The old check measured the newest source's age, so it stayed silent while games arrived.
        path = self.state / replay_library.LIBRARY
        os.utime(path, (now - 3 * 3600, now - 3 * 3600))
        os.utime(self.state / "online-replays/10072/index.json", (now - 60, now - 60))
        self.assertEqual([a.key for a in health.replay_library(self.state, now)], ["replay library"])
        # Behind by less than LIBRARY_LAG: the next tick's refresh catches it up.
        os.utime(path, (now - health.LIBRARY_LAG + 600,) * 2)
        self.assertEqual(health.replay_library(self.state, now), [])

if __name__ == "__main__":
    unittest.main()
