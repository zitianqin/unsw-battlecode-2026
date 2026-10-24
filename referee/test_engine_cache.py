"""Tests for engine_cache.py: its fallbacks on real wasmtime files, and one real game through the launcher (needs a
C++20 compiler, no network). Run: .venv/bin/python -m unittest test_engine_cache (from referee/)"""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import unswbc.engine
from wasmtime import Engine, Module, wat2wasm

import engine_cache
import record as record_module
import run
import test_record

HERE = Path(__file__).resolve().parent
UNSWBC = str(Path(sys.executable).parent / "unswbc")
WAT = '(module (func (export "answer") (result i32) i32.const 42))'


class TestLoadModule(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="engine-cache-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        self.wasm = self.root / "m.wasm"
        self.wasm.write_bytes(wat2wasm(WAT))
        self.folder = self.root / "cache"
        self.cached = engine_cache.cache_path(self.folder, self.wasm.read_bytes())

    # load_module's module, whether it compiled (a Module built from bytes), and what it said on stderr.
    def load(self) -> tuple[Module, bool, str]:
        said = io.StringIO()
        with patch("wasmtime.Module", wraps=Module) as built, contextlib.redirect_stderr(said):
            built.deserialize = Module.deserialize
            module = engine_cache.load_module(Engine(), self.wasm, self.folder)
        return module, built.called, said.getvalue()

    def test_a_missing_copy_is_compiled_and_saved_then_loaded(self) -> None:
        _, compiled, said = self.load()
        self.assertEqual((compiled, said), (True, ""))
        self.assertTrue(self.cached.is_file())
        self.assertEqual(oct(self.folder.stat().st_mode & 0o777), "0o700")
        _, compiled, said = self.load()
        self.assertEqual((compiled, said), (False, ""))
        self.assertEqual([p.name for p in self.folder.iterdir()], [self.cached.name])  # no temporary left

    def test_a_copy_saved_under_a_group_writable_umask_still_loads(self) -> None:
        old = os.umask(0o002)  # common on Linux hosts; a first version saved group-writable copies it then refused
        try:
            self.load()
        finally:
            os.umask(old)
        self.assertEqual(oct(self.cached.stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.load()[1:], (False, ""))

    def test_the_name_changes_with_the_wasm(self) -> None:
        other = engine_cache.cache_path(self.folder, wat2wasm(WAT.replace("42", "43")))
        self.assertNotEqual(other, self.cached)
        self.assertIn(sys.platform, self.cached.name)

    def test_a_corrupt_or_foreign_copy_is_reported_recompiled_and_replaced(self) -> None:
        self.load()
        good = self.cached.read_bytes()
        header, _, compiled = good.partition(b"\n")
        foreign = self.root / "foreign"  # saved as ours, but by an engine configured unlike the toolkit's
        engine_cache.save_copy(foreign, Module(Engine(unswbc_config()), self.wasm.read_bytes()).serialize())
        bad = {"truncated": good[:len(good) // 2],
               "zeros under the final name, as after a power cut": b"\0" * len(good),
               "zeroed module under its header": header + b"\n" + b"\0" * len(compiled),
               "a bare module from before checksums": compiled,
               "another engine's": foreign.read_bytes()}
        for name, blob in bad.items():
            with self.subTest(name):
                self.cached.write_bytes(blob)
                self.cached.chmod(0o600)
                _, compiled, said = self.load()
                self.assertTrue(compiled)
                self.assertIn(f"cannot load {self.cached.name}", said)
                self.assertEqual(self.load()[1:], (False, ""))  # replaced: loads quietly now

    def test_a_copy_reaches_the_disk_before_its_final_name(self) -> None:
        order, replace = [], os.replace
        with patch.object(engine_cache.os, "fsync", side_effect=lambda fd: order.append("fsync")), \
                patch.object(engine_cache.os, "replace", side_effect=lambda *a: order.append("replace") or
                             replace(*a)):
            self.load()
        self.assertEqual(order, ["fsync", "replace"])

    def test_a_copy_others_could_have_written_is_never_loaded(self) -> None:
        self.load()
        self.cached.chmod(0o666)
        _, compiled, said = self.load()
        self.assertTrue(compiled)
        self.assertIn("could have been written by someone else", said)
        self.assertEqual(oct(self.cached.stat().st_mode & 0o022), "0o0")  # the replacement is ours

    def test_an_unwritable_folder_still_gives_a_module(self) -> None:
        self.folder = self.root / "file"
        self.folder.write_text("a file, not a folder")
        module, compiled, said = self.load()
        self.assertTrue(compiled)
        self.assertIn("cannot save", said)
        self.assertIsInstance(module, Module)


# A config unlike the toolkit's default Engine(), so its compiled copy won't load there.
def unswbc_config():
    from wasmtime import Config
    config = Config()
    config.consume_fuel = True
    return config


class TestInstallAndWarm(unittest.TestCase):
    def setUp(self) -> None:
        self.original = unswbc.engine.EngineModule.__init__
        self.addCleanup(setattr, unswbc.engine.EngineModule, "__init__", self.original)
        self.folder = Path(tempfile.mkdtemp(prefix="engine-cache-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.folder)])

    def test_another_engine_py_is_left_unpatched_with_a_reason(self) -> None:
        said = io.StringIO()
        with patch.object(engine_cache, "KNOWN_ENGINES", {"0123456789abcdef"}), contextlib.redirect_stderr(said):
            self.assertFalse(engine_cache.install(self.folder))
            reason = engine_cache.warm(self.folder)
        self.assertIs(unswbc.engine.EngineModule.__init__, self.original)
        self.assertIn(f"digest {engine_cache.engine_digest()}", said.getvalue())
        self.assertIn("KNOWN_ENGINES", reason)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_this_toolkits_engine_is_known(self) -> None:
        self.assertIsNone(engine_cache.unsupported())

    def test_warm_saves_a_loading_copy_or_says_why_not(self) -> None:
        self.assertIsNone(engine_cache.warm(self.folder))
        self.assertEqual(len(list(self.folder.glob("*.cwasm"))), 1)
        locked = self.folder / "locked"
        locked.mkdir(mode=0o500)  # like the loop home seen from a coder's sandbox
        self.addCleanup(locked.chmod, 0o700)
        self.assertIsNotNone(engine_cache.warm(locked / "engine-cache"))

    def test_the_patched_engine_uses_the_cache(self) -> None:
        self.assertTrue(engine_cache.install(self.folder))
        unswbc.engine.EngineModule()
        self.assertEqual(len(list(self.folder.glob("*.cwasm"))), 1)
        with patch("wasmtime.Module", side_effect=AssertionError("compiled again")) as built:
            built.deserialize = Module.deserialize
            again = unswbc.engine.EngineModule()
        self.assertIsNone(again._live)
        self.assertIn("memory", [export.name for export in again._module.exports])


class TestRefereeFallback(unittest.TestCase):
    # A referee that can't use the cache says so once, plays the plain command and leaves the reason for health.py;
    # the next run that can use it removes that.
    def test_a_run_without_the_cache_says_so_once_and_leaves_the_reason(self) -> None:
        run._engine_launcher.cache_clear()
        self.addCleanup(run._engine_launcher.cache_clear)
        note = run.remote.loop_home() / "engine-cache-off.json"
        said = io.StringIO()
        reason = "unswbc's engine.py changed (digest 0123456789abcdef); re-read it and add it to KNOWN_ENGINES"
        with patch.object(engine_cache, "warm", return_value=reason) as warm, contextlib.redirect_stderr(said):
            self.assertEqual([run.engine_launcher() for _ in range(3)], [None] * 3)
        warm.assert_called_once()
        self.assertEqual(said.getvalue().count("engine cache: off for this run"), 1)
        self.assertEqual(json.loads(note.read_text())["reason"], reason)
        run._engine_launcher.cache_clear()
        self.assertIsNotNone(run.engine_launcher())
        self.assertFalse(note.exists())

    # A remote game's fallback line lands in its host's status, which health.py reads.
    def test_a_hosts_fallback_line_reaches_its_status(self) -> None:
        host = run.remote.Host("ll", 1)
        line = ("engine cache: cannot load x.cwasm (damaged: its content doesn't match its checksum); "
                "compiling the engine and replacing it")
        game = run.remote.RemoteGame(f"{line}\nteam A wins after 152 rounds (by elimination)\n", 0, b"R")
        scheduler = run.Scheduler(1, False, {})
        scheduler.hosts = [host]
        with tempfile.TemporaryDirectory() as folder, patch.object(host, "play", return_value=game), \
                patch.object(scheduler, "remote_bot", side_effect=lambda h, bot: bot):
            Path(folder, "m").write_text("a map")
            scheduler.play_remote(host, run.Job(Path(folder, "m"), Path("a"), Path("b"), "A"), Path(folder, "r"))
        scheduler.save()  # as it does every STATUS_S while games run
        self.assertTrue(run.remote.read_status()["hosts"]["ll"]["engine_cache"].endswith(line))


class TestLauncher(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="engine-cache-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        self.bot = test_record.make_bot(self.root / "bot")

    def play(self, prefix: list[str], name: str) -> tuple[subprocess.CompletedProcess, bytes]:
        replay = self.root / f"{name}.replay"
        done = subprocess.run([*prefix, "run", str(test_record.MAP), str(self.bot), str(self.bot), "-o", str(replay),
                               "--seed", str(test_record.SEED)], capture_output=True, text=True, timeout=300)
        return done, replay.read_bytes()

    # The same game, byte for byte, as `unswbc run`, with a cold cache and a warm one; the script form runs a script.
    def test_a_game_through_the_launcher_is_the_toolkits_game(self) -> None:
        cache = self.root / "cache"
        launcher = [sys.executable, str(HERE / "engine_cache.py"), str(cache)]
        plain, expected = self.play([UNSWBC], "plain")
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        for name in ("cold", "warm"):
            with self.subTest(name):
                done, replay = self.play(launcher, name)
                self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
                self.assertEqual(replay, expected)
                self.assertEqual(len(list(cache.glob("*.cwasm"))), 1)
        # The referee's own games, recorded (record.py through the launcher) or not, match the unpatched ones.
        for record in (None, "rec"):
            with self.subTest(record=record):
                games = {}
                for name, launcher_on in (("off", False), ("on", True)):
                    job = run.Job(test_record.MAP, self.bot, self.bot, "B",
                                  record=self.root / f"{record}-{name}" if record else None)
                    with patch.object(run, "engine_launcher", return_value=None) if not launcher_on \
                            else contextlib.nullcontext():
                        game = run.run_game(job, False, self.root / f"{record}-{name}.replay")
                    self.assertEqual(game.returncode, 0, game.stdout)
                    self.assertNotIn("engine cache", game.stdout)
                    games[name] = (game.replay, record and record_module.Recording.load(job.record))
                self.assertIsNotNone(run.engine_launcher())
                self.assertEqual(games["on"][0], games["off"][0])
                if record:
                    self.assertTrue(record_module.same(games["on"][1], games["off"][1]))
        script = self.root / "script.py"
        script.write_text("import sys, unswbc.engine as e, engine_cache\n"
                          "print(sys.argv[1:], e.EngineModule.__init__.__qualname__)\nsys.exit(3)\n")
        done = subprocess.run([*launcher, str(script), "x", "-o", "y"], capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(HERE)})
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(done.stdout.strip(), "['x', '-o', 'y'] install.<locals>.patched_init")


if __name__ == "__main__":
    unittest.main()
