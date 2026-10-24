"""Tests for record.py with real games of a tiny C++ bot (needs a C++20 compiler, no network, no SSH).
Run: uv run python -m unittest discover -s referee"""

import testhome  # before anything that reads live paths
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import record
import remote

HERE = Path(__file__).resolve().parent
MAP = HERE.parent / "maps" / "extra" / "arena.map"
SEED = 12345

# A small deterministic bot: moves to the first open neighbour in an order that turns with the round, splits when
# long enough, pings sonar; mark.hpp can make dragon 0 add an indicator (MARK_ROUND) or exit (CRASH_ROUND) in a round.
BOT = r"""
#include <cstdlib>
#include <iostream>
#include <set>
#include <string>
#include <utility>
#include <vector>
#include "mark.hpp"

int main() {
    std::string word, team;
    int id = 0, width = 0, height = 0, limit = 0;
    std::cin >> word >> id >> word >> team >> word >> width >> height >> word >> limit;
    while (std::cin >> word) {  // ROUND
        int round = 0, length = 0, count = 0, messages = 0;
        std::string dir;
        std::cin >> round >> word >> dir >> word >> length >> word >> count >> word >> messages;
        for (int i = 0; i < messages; i++) std::cin >> word;
        std::vector<std::pair<int, int>> tiles(49);
        for (auto& [x, y] : tiles) { int pearl, when; std::cin >> x >> y >> pearl >> when; }
        int bodies = 0;
        std::cin >> word >> bodies;
        std::set<std::pair<int, int>> taken;
        for (int i = 0; i < bodies; i++) {
            std::string t, facing; int who, x, y, head;
            std::cin >> t >> who >> x >> y >> facing >> head;
            taken.insert({x, y});
        }
        std::vector<std::string> across(56), down(56);
        for (auto& e : across) std::cin >> e;
        for (auto& e : down) std::cin >> e;
        // N, E, S, W: the neighbour tile in the 7x7 window, and the edge crossed to reach it
        int const tile[4] = {17, 25, 31, 23};
        std::string const edge[4] = {across[3 * 7 + 3], down[3 * 8 + 4], across[4 * 7 + 3], down[3 * 8 + 3]};
        char move = 'N';
        for (int k = 0; k < 4; k++) {
            int const d = (k + round + id) % 4;
            if (edge[d] != "w" && !taken.count(tiles[tile[d]])) { move = "NESW"[d]; break; }
        }
        if (id == 0 && round == CRASH_ROUND) std::exit(1);
        if (id == 0 && round == MARK_ROUND) std::cout << "INDICATOR marked\n";
        if (length >= 6 && round % 7 == 0) std::cout << "SPLIT 3\n";
        else std::cout << "MOVE " << move << "\n";
        if (round % 5 == 0) std::cout << "SONAR E " << id << "\n";
        std::cout << "ENDTURN" << std::endl;
    }
}
"""
TOML = '[project]\nlanguage = "c++"\ninclude = [ "*.cpp", "*.hpp" ]\n'


def make_bot(folder: Path, mark: int = -1, crash: int = -1) -> Path:
    folder.mkdir(parents=True)
    (folder / "bot.toml").write_text(TOML)
    (folder / "main.cpp").write_text(BOT)
    (folder / "mark.hpp").write_text(f"constexpr int MARK_ROUND = {mark};\nconstexpr int CRASH_ROUND = {crash};\n")
    return folder


def record_game(a: Path, b: Path, replay: Path, recording: Path, team: str = "A") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "record.py"), str(MAP), str(a), str(b), "-o", str(replay),
                           "--seed", str(SEED), "--team", team, "--recording", str(recording)],
                          capture_output=True, text=True, timeout=300)


@unittest.skipIf(record.supported(), f"recording unsupported here: {record.supported()}")
class TestRecordedGames(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scratch = tempfile.TemporaryDirectory()
        root = Path(cls.scratch.name)
        cls.plain, cls.marked = make_bot(root / "plain"), make_bot(root / "marked", mark=5)
        cls.crashing = make_bot(root / "crashing", crash=3)
        cls.recording = root / "game.zst"
        cls.game = record_game(cls.plain, cls.plain, root / "game.replay", cls.recording)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.scratch.cleanup()

    def test_a_recorded_game_is_the_game_unswbc_run_plays_and_replays_identically(self) -> None:
        self.assertEqual(self.game.returncode, 0, self.game.stdout + self.game.stderr)
        unswbc = Path(sys.executable).parent / "unswbc"
        plain = Path(self.scratch.name) / "plain.replay"
        done = subprocess.run([str(unswbc), "run", str(MAP), str(self.plain), str(self.plain), "-o", str(plain),
                               "--seed", str(SEED)], capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertEqual((Path(self.scratch.name) / "game.replay").read_bytes(), plain.read_bytes())
        game = record.Recording.load(self.recording)
        self.assertEqual(game.team, "A")
        self.assertGreater(len(game.inits), 1, "the bot splits, so children are recorded too")
        check = record.check(self.recording, self.plain)
        self.assertTrue(check.identical, check.mismatch)
        self.assertEqual((check.checked, check.turns), (len(game.turns), len(game.turns)))
        self.assertGreater(check.cpu, 0)

    def test_a_changed_reply_is_caught_at_its_turn(self) -> None:
        game = record.Recording.load(self.recording)
        first = next(i for i, (dragon, block, _) in enumerate(game.turns)
                     if dragon == 0 and block.startswith("ROUND 5\n"))
        check = record.check(self.recording, self.marked)
        self.assertFalse(check.identical)
        self.assertEqual(check.checked, first + 1)  # stopped at that turn, not before, not after
        self.assertEqual(check.mismatch, "round 5, dragon 0: a different reply")

    def test_a_recording_replaced_since_its_row_was_written_is_not_checked(self) -> None:
        right = record.file_digest(self.recording.read_bytes())
        self.assertTrue(record.check(self.recording, self.plain, digest=right).identical)
        stale = record.check(self.recording, self.plain, digest="0" * 16)
        self.assertFalse(stale.identical)
        self.assertEqual(stale.checked, 0)
        self.assertIn("changed since its game was cached", stale.mismatch)

    def test_a_bot_failure_leaves_no_recording(self) -> None:
        out = Path(self.scratch.name) / "crash.zst"
        done = record_game(self.crashing, self.plain, Path(self.scratch.name) / "crash.replay", out, team="B")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(out.exists())
        self.assertIn("no recording: round 3: bot 0 (team A)", done.stdout)

    def test_two_recordings_of_one_game_are_the_same_game(self) -> None:
        again = Path(self.scratch.name) / "again.zst"
        record_game(self.plain, self.plain, Path(self.scratch.name) / "again.replay", again)
        self.assertTrue(record.same(record.Recording.load(self.recording), record.Recording.load(again)))


    def test_a_recording_that_cannot_be_saved_still_leaves_the_game_counted(self) -> None:
        import contextlib
        import io
        out = Path(self.scratch.name) / "full.zst"
        printed = io.StringIO()
        with patch.object(record.Recording, "save", side_effect=OSError(28, "No space left on device")), \
                contextlib.redirect_stdout(printed):
            code = record.record_game(str(MAP), [str(self.plain), str(self.plain)],
                                      str(Path(self.scratch.name) / "full.replay"), SEED, "A", out)
        self.assertEqual(code, 0)
        self.assertIn("no recording: cannot save", printed.getvalue())
        self.assertIn("team", printed.getvalue())  # the game's own result line was printed as usual
        self.assertFalse(out.exists())


class TestInternals(unittest.TestCase):
    def test_a_changed_toolkit_fails_loudly_everywhere(self) -> None:
        import sprt
        with patch.object(record, "internals", return_value="0000000000000000"):
            self.assertIn("changed (digest 0000000000000000)", record.supported())
            with self.assertRaises(record.RecordError):
                record.check(Path("any.zst"), Path("bot"))
            with self.assertRaises(record.RecordError):
                record.record_game("m.map", ["a", "b"], "r.replay", None, "A", Path("r.zst"))
            with tempfile.TemporaryDirectory() as folder, patch("sys.stderr"):
                remote.ALERTS.unlink(missing_ok=True)
                self.assertFalse(sprt.predicting(sprt.BaselineCache(Path(folder) / "baselines.jsonl")))
                self.assertIn("WARNING predicted games off: unswbc's run.py, bot.py, turn.py, engine.py",
                              remote.ALERTS.read_text())

    def test_a_champion_hashing_its_own_sonar_key_turns_recording_off(self) -> None:
        # The champion's real line (v0033 bot/params.hpp): with it no candidate's game can match a recording.
        import sprt
        line = "    bool sonarBuildKey = true;   // the tag's key is a hash of this build's source (sonarkey.hpp)\n"
        with tempfile.TemporaryDirectory() as folder, patch("sys.stderr"):
            bot = Path(folder) / "bot"
            bot.mkdir()
            cache = sprt.BaselineCache(Path(folder) / "baselines.jsonl")
            (bot / "params.hpp").write_text(line)
            remote.ALERTS.unlink(missing_ok=True)
            self.assertFalse(sprt.predicting(cache, bot))
            self.assertFalse(remote.ALERTS.exists())  # a deliberate state: stderr only (test_sprt checks its line)
            (bot / "params.hpp").write_text(line.replace("true", "false"))
            self.assertEqual(sprt.predicting(cache, bot), record.supported() is None)

    def test_a_recording_made_on_another_toolkit_does_not_load(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "r.zst"
            record.Recording("A", {0: "ID 0"}, [(0, "ROUND 0\n", "d")]).save(path)
            self.assertEqual(record.Recording.load(path).turns, [(0, "ROUND 0\n", "d")])
            with patch.object(record, "internals", return_value="0000000000000000"), \
                    self.assertRaisesRegex(record.RecordError, "recorded on toolkit"):
                record.Recording.load(path)

    def test_the_digest_covers_the_engine(self) -> None:
        self.assertIn("engine.py", record.INTERNAL_FILES)
        self.assertIn("unswbc_engine.wasm", record.INTERNAL_FILES)

    # A toolkit that drops or renames one of its files is a refusal, never an exception: the upgrade probe and
    # sprt.predicting only ask whether recording works.
    @unittest.skipIf(record.internals() is None, "no unswbc toolkit")
    def test_an_unreadable_toolkit_file_is_a_refusal(self) -> None:
        import sprt
        with patch.object(record, "INTERNAL_FILES", (*record.INTERNAL_FILES, "gone.py")):
            self.assertIn("gone.py", record.supported())
            with self.assertRaises(record.RecordError):
                record.require()
            with tempfile.TemporaryDirectory() as folder, patch("sys.stderr"):
                self.assertFalse(sprt.predicting(sprt.BaselineCache(Path(folder) / "baselines.jsonl")))

    # Fails when the dev venv's toolkit was upgraded without its digest being read and added (1.2.0 went a day
    # unrecorded, 28 Sep). KNOWN_INTERNALS names the first toolkit with each digest, so a later one sharing it passes.
    def test_the_known_digest_is_this_toolkits(self) -> None:
        import importlib.metadata
        if (found := record.internals()) is None:
            self.skipTest("no unswbc toolkit")
        self.assertIn(found, record.KNOWN_INTERNALS, f"unswbc {importlib.metadata.version('unswbc')} digests to "
                      f"{found}: read its {', '.join(record.INTERNAL_FILES)} and add it to KNOWN_INTERNALS")


if __name__ == "__main__":
    unittest.main()
