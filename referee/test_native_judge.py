"""Tests for native_judge.py and native_judge/play.c: real games through the native judge are today's games byte for
byte (a bot that crashes included), another engine, toolkit or judge plays today's path, and in the loop's own games
a judge that fails or differs is replayed through `unswbc run` and switched off (needs a C and a C++20 compiler, no
network). Run: .venv/bin/python -m unittest test_native_judge (from referee/)"""

import testhome  # before anything that reads live paths
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import fork_server
import native_fidelity
import native_judge
import remote
import run
import test_record

HERE = Path(__file__).resolve().parent
MAPS = [test_record.MAP, HERE.parent / "maps" / "ladder" / "portals.map"]


class TestNativeJudge(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="native-judge-"))
        cls.bot = test_record.make_bot(cls.root / "bot")
        cls.crasher = test_record.make_bot(cls.root / "crasher", crash=3)  # dragon 0 exits in round 3, every try
        cls.shim, _ = fork_server.warm(cls.root / "fork-server")
        run.build_bots([cls.bot, cls.crasher], False)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self) -> None:
        native_judge.unsupported.cache_clear()
        native_judge._local_judge.cache_clear()
        native_judge._process_key.clear()
        (remote.loop_home() / run.NATIVE_CHECKS).unlink(missing_ok=True)

    # Both ways, forked as the referee forks: (reference, native), and whether the native judge made the command.
    def both(self, job: run.Job, native: bool | None = True) -> tuple[run.RawGame, run.RawGame, list]:
        made, original = [], native_judge.command

        def command(*args, **named):
            made.append(found := original(*args, **named))
            return found

        games = []
        with patch.object(run, "fork_plan", return_value=(self.shim, False)), \
                patch.object(run.native_judge, "command", side_effect=command):
            for way in (False, native):
                games.append(run.run_game(job, False, self.root / f"{way}.replay", native=way))
        return games[0], games[1], made

    # Same replay bytes, parsed result and log on two maps, both seats, with a bot whose dragon 0 (seat A) exits without
    # a reply in round 3 on its first try and its restart (the retry, the skip and its "exited with code 1" line).
    def test_games_match_todays_path(self) -> None:
        for game_map in MAPS:
            for side in ("A", "B"):
                with self.subTest(map=game_map.name, side=side):
                    job = run.Job(game_map, self.crasher, self.bot, side)
                    reference, native, made = self.both(job)
                    self.assertEqual(reference.returncode, 0, reference.stdout)
                    self.assertTrue(made and made[0] and "native-judge-" in made[0][0], made)
                    if side == "A":  # dragon 0 is team A's
                        self.assertIn("bot 0 (team A) exited with code 1", reference.stdout)
                    compared = native_fidelity.compare(job, reference, native)
                    self.assertTrue(compared["match"], compared["problems"])
                    self.assertEqual((run.parse_game(job, reference).native, run.parse_game(job, native).native),
                                     (False, True))

    # Switched on, a game plays natively only once its engine and toolkit's gate has passed on this platform: not with
    # no gate, a gate for another engine (a new digest), or a failed gate (a failed rebuild check).
    def test_a_new_key_plays_todays_path_until_its_gate_passes(self) -> None:
        job = run.Job(test_record.MAP, self.bot, self.bot, "A")
        home = remote.loop_home()
        native_judge.enabled_path(home).unlink(missing_ok=True)

        def natively() -> bool:
            with patch.object(run, "NATIVE_JUDGE", True):
                return bool(self.both(job, native=None)[2])

        self.assertFalse(natively())
        native_judge.record_gate(home, "0000000000000000-another", remote.PLATFORM, True, 40, 40, "report")
        self.assertFalse(natively())
        native_judge.record_gate(home, native_judge.key(), remote.PLATFORM, False, 40, 39, "report", "1 mismatched")
        self.assertFalse(natively())
        native_judge.record_gate(home, native_judge.key(), remote.PLATFORM, True, 40, 40, "report")
        self.assertTrue(natively())
        # A process whose checkout changed under it (the key on disk isn't the one it took, with the launcher it
        # loaded, at its first native game) plays today's path, gate or not.
        self.assertEqual(native_judge.process_key(), native_judge.key())
        native_judge.record_gate(home, "0000000000000000-changed", remote.PLATFORM, True, 40, 40, "report")
        with patch.object(native_judge, "key", return_value="0000000000000000-changed"):
            self.assertFalse(natively())

    # A gate is for the judge's own code too: a changed source (or shim, or what builds its command line: this file and
    # host_agent.native_command, but not the rest of host_agent.py) is a new key, which plays today's path until its
    # own gate passes; and a toolkit whose glue nobody read play.c against plays today's path, gate or not.
    def test_a_changed_judge_or_an_unread_toolkit_plays_todays_path(self) -> None:
        home = remote.loop_home()
        sources = self.root / "sources"
        shutil.copytree(native_judge.SOURCE_DIR, sources, dirs_exist_ok=True)
        with patch.object(native_judge, "SOURCE_DIR", sources):
            native_judge.record_gate(home, native_judge.key(), remote.PLATFORM, True, 40, 40, "report")
            self.assertTrue(native_judge.enabled(home, remote.PLATFORM))
            with (sources / "play.c").open("a") as handle:
                handle.write("\n")
            self.assertFalse(native_judge.enabled(home, remote.PLATFORM))
        referee = self.root / "referee"
        referee.mkdir(exist_ok=True)
        agent = (HERE / "host_agent.py").read_text()
        with patch.object(native_judge, "HERE", referee):
            (referee / "host_agent.py").write_text(agent)
            before = native_judge.launcher_digest()
            (referee / "host_agent.py").write_text(agent.replace("def game_cpu(", "def game_cpu(  "))
            self.assertEqual(native_judge.launcher_digest(), before)
            (referee / "host_agent.py").write_text(agent.replace('"play", *where', '"play", "--x", *where'))
            self.assertNotEqual(native_judge.launcher_digest(), before)
        with patch.object(native_judge, "KNOWN_TOOLKITS", {"0123456789abcdef"}):
            native_judge.unsupported.cache_clear()
            self.assertIn("KNOWN_TOOLKITS", native_judge.unsupported())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(native_judge.local_judge(self.root / "unread-toolkit"))

    # A host plays natively only with the engine and toolkit glue the referee's key was gated for (sent as --engine
    # and --toolkit), else its own command.
    def test_a_host_with_another_engine_or_toolkit_plays_its_own_command(self) -> None:
        judge = native_judge.local_judge(remote.loop_home() / "native-judge")
        engine, toolkit = native_judge.engine_wasm()[1], native_judge.toolkit_digest()
        game = ["--cache", str(self.root / "cache"), "-o", str(self.root / "h.replay"), str(test_record.MAP),
                str(self.bot), str(self.bot)]
        for sent, plays in (((engine, toolkit), True), (("0" * 64, toolkit), False), ((engine, "0" * 16), False)):
            with self.subTest(sent=sent), contextlib.redirect_stderr(io.StringIO()) as said:
                found = native_judge.host_command(["--binary", str(judge), "--engine", sent[0], "--toolkit", sent[1],
                                                   *game])
                self.assertEqual(found is not None, plays, said.getvalue())
                if not plays:
                    self.assertIn("aren't the referee's", said.getvalue())

    # A build that failed is kept: the next games fall back at once, not compiling the same sources again until
    # BUILD_RETRY_S has passed.
    def test_a_failed_build_is_not_tried_again_for_a_while(self) -> None:
        files = {name: native_judge.SOURCE_DIR / name for name in native_judge.SOURCES}
        broken = self.root / "broken.c"
        broken.write_text("this is not C\n")
        files["play.c"] = broken
        folder = self.root / "failed-build"
        with self.assertRaises(subprocess.CalledProcessError):
            native_judge.build(folder, files)
        with patch.object(native_judge.subprocess, "run", side_effect=AssertionError("compiled again")), \
                self.assertRaisesRegex(OSError, "failed 0 min ago"):
            native_judge.build(folder, files)
        marker = next(folder.glob("*.failed"))
        old = marker.stat().st_mtime - native_judge.BUILD_RETRY_S - 1
        os.utime(marker, (old, old))
        with self.assertRaises(subprocess.CalledProcessError):  # compiled again, and failed again
            native_judge.build(folder, files)
        # a compiler that hangs (past BUILD_S) is kept as a failure too
        os.utime(marker, (old, old))
        hung = subprocess.TimeoutExpired("cc", native_judge.BUILD_S)
        with patch.object(native_judge.subprocess, "run", side_effect=hung), \
                self.assertRaises(subprocess.TimeoutExpired):
            native_judge.build(folder, files)
        with patch.object(native_judge.subprocess, "run", side_effect=AssertionError("compiled again")), \
                self.assertRaisesRegex(OSError, "took over"):
            native_judge.build(folder, files)

    # In the loop's own games (run_game's native None): a judge that dies, runs past its time or fails where unswbc run
    # plays is replayed through unswbc run and switches the judge off for every game (native-judge-checks.json's
    # "off"); one that fails as unswbc run does (a map the engine refuses) only said what the toolkit says. A checked
    # game plays both ways and the game that counts, replay file included, is unswbc run's: the same game counts as
    # identical, a different one switches the judge off.
    def test_a_failed_or_differing_native_game_switches_the_judge_off(self) -> None:
        good = run.Job(test_record.MAP, self.bot, self.bot, "A")
        refused = run.Job(HERE / "testdata" / "gen_worldgen_144449864.map", self.bot, self.bot, "A")
        refusal = run.error_line(run.run_game(refused, False, self.root / "refused.replay", native=False))
        self.assertIsNotNone(refusal)
        said = f"echo '{run.NATIVE_PLAYED} (engine x)' >&2"
        fake_win = f"{said}; echo 'team A wins after 1 rounds (by elimination) (T)'"  # and no replay
        cases = [(good, f"{said}; kill -SEGV $$", False, True),  # (job, fake judge, checked, switched off)
                 (good, f"{said}; sleep 60", False, True),  # past GAME_TIMEOUT_S below
                 (refused, f"{said}; echo \"{refusal}\" >&2; exit 1", False, False),
                 (good, None, True, False),  # the real judge
                 (good, fake_win, True, True)]
        path = remote.loop_home() / run.NATIVE_CHECKS
        for job, fake, checked, off in cases:
            with self.subTest(map=job.map.name, fake=fake, checked=checked):
                path.unlink(missing_ok=True)
                command = native_judge.command if fake is None else lambda *args: ["sh", "-c", fake]
                with patch.object(run, "NATIVE_JUDGE", True), \
                        patch.object(native_judge, "enabled", return_value=True), \
                        patch.object(run.native_judge, "command", side_effect=command), \
                        patch.object(run, "fork_plan", return_value=(self.shim, False)), \
                        patch.object(run, "native_check", return_value=checked), patch.object(remote.Host, "alert"), \
                        patch.object(run, "GAME_TIMEOUT_S", 20), contextlib.redirect_stderr(io.StringIO()):
                    game = run.run_game(job, False, self.root / "loop.replay")
                    self.assertEqual(run.native_plan(job, False, None), not off)
                data = json.loads(path.read_text()) if path.exists() else {}
                self.assertEqual(bool(data.get("off")), off, data)
                self.assertEqual((game.native, game.returncode), (False, 1 if job is refused else 0),
                                 game.stdout[-300:])  # a failed or checked judge's game is unswbc run's
                if job is good:
                    self.assertEqual((self.root / "loop.replay").read_bytes(), game.replay)
                if checked:
                    self.assertEqual((data["checks"], data["identical"]), (1, int(not off)))
        # Lowered (nice 19), a native game past its time may have starved: it comes back as it is, for the scheduler
        # to replay at normal priority, and switches nothing off.
        path.unlink(missing_ok=True)
        with patch.object(run, "NATIVE_JUDGE", True), patch.object(native_judge, "enabled", return_value=True), \
                patch.object(run.native_judge, "command", return_value=["sh", "-c", f"{said}; sleep 60"]), \
                patch.object(run, "fork_plan", return_value=(self.shim, False)), patch.object(remote.Host, "alert"), \
                patch.object(run, "GAME_TIMEOUT_S", 3), contextlib.redirect_stderr(io.StringIO()):
            game = run.run_game(good, False, self.root / "loop.replay", nice=19)
        self.assertEqual((game.native, game.returncode, path.exists()), (True, None, False))

    # A gate game: the reference's turn out of time twice is starved, and one that didn't finish an error; the native
    # game's alone out of time the second time is played again unlowered (this process at normal priority) and is a
    # mismatch only when it is out of time there too, and starved where it can't be unlowered (a niced process, as the
    # upkeep's); a native game that fell back (the judge couldn't build), a host that failed or a native game this
    # machine failed twice (an OS error) is left out, and one it failed once is played again. A gate
    # records under the key this process took, and nothing when the key on disk isn't that by its end, too few games
    # compared, or a game found no room (which also ends a --gate-missing run there).
    def test_the_gate_records_only_what_it_compared_under_the_key_it_played(self) -> None:
        job = run.Job(test_record.MAP, self.bot, self.bot, "A")
        win = "team A wins after 3 rounds (by elimination) (T)"
        late = "round 2: bot 0 (team A) ran out of time"

        class Fake:
            host = None

            def __init__(self, reference, *native):
                self.reference, self.native = reference, list(native)

            def play(self, job, native, lowered=True):
                said = (self.native.pop(0) if len(self.native) > 1 else self.native[0]) if native else self.reference
                if isinstance(said, BaseException):
                    raise said
                return said if isinstance(said, run.RawGame) else run.RawGame(said, 0, None, 1.0)

        played = f"{run.NATIVE_PLAYED} (engine x)\n"
        os_error = run.RawGame(f"{played}error: [Errno 35] Resource temporarily unavailable", 1, None, 1.0)
        for reference, native, nice, verdict in (
                (f"{late}\n{win}", [f"{played}{win}"], 0, "starved"),
                (run.RawGame("game exceeded 900s", None, None, 900.0), [f"{played}{win}"], 0, "error"),
                (win, [f"{played}{late}\n{win}"], 0, "MISMATCH"),
                (win, [f"{played}{late}\n{win}", f"{played}{late}\n{win}", f"{played}{win}"], 0, "match"),
                (win, [f"{played}{late}\n{win}"], 19, "starved"),
                (win, [f"native judge: off (cannot compile the judge)\n{win}"], 0, "fell back"),
                (win, [remote.RemoteError("zz: exit 255")], 0, "error"),
                (win, [os_error], 0, "error"), (win, [os_error, f"{played}{win}"], 0, "match")):
            with self.subTest(verdict=verdict, nice=nice), patch.object(run, "own_nice", return_value=nice):
                self.assertEqual(native_fidelity.check(Fake(reference, *native), job)["verdict"], verdict)

        home = remote.loop_home()
        sample = native_fidelity.jobs_for([self.bot, self.crasher], native_fidelity.gate_maps(), "rotate")[
            :native_fidelity.FIDELITY_SAMPLE]

        def rows(jobs: list[run.Job], matched: int) -> list[dict]:  # check's rows: `matched` matches, then starved
            return [{"map": str(job.map), "folder": job.map.parent.name, "a": job.a.name, "b": job.b.name,
                     "a_side": job.a_side, "verdict": "match" if i < matched else "starved", "problems": []}
                    for i, job in enumerate(jobs)]

        # A gate that ended without recording keeps its matched games, and the next run plays only the rest of the
        # sample (review 4, 2026-09-29: each hourly run started over, so a gate that never had an hour's room beside
        # back-to-back evaluations never finished).
        for keys, matched, room, recorded, played in ((["k1", "k1"], [40], [None], "k1", [40]),
                                                      (["k2", "k3"], [40], [None], None, [40]),
                                                      (["k4", "k4"], [30, 6], [None, None], "k4", [40, 10]),
                                                      (["k5", "k5"], [40], ["no room"], None, [40]),
                                                      (["k6", "k6"], [25, 15], ["no room", None], "k6", [40, 15])):
            with self.subTest(keys=keys, matched=matched, room=room):
                native_judge.enabled_path(home).unlink(missing_ok=True)
                given = []
                for run_matched, run_room in zip(matched, room):
                    def play_all(player, jobs, workers, played=None, run_matched=run_matched):
                        given.append(len(jobs))
                        return {"platform": "fake place", "games": rows(jobs, run_matched)}

                    with patch.object(native_fidelity, "play_all", side_effect=play_all), \
                            patch.object(native_fidelity, "Player") as player, \
                            patch.object(native_judge, "process_key", return_value=keys[0]), \
                            patch.object(native_judge, "key", return_value=keys[1]), \
                            patch.object(remote, "PLATFORM", "fake place"), \
                            contextlib.redirect_stdout(io.StringIO()):
                        player.return_value.no_room, player.return_value.host = run_room, None
                        passed = native_fidelity.gate([self.bot, self.crasher], None, self.root / "gate.json", 1)
                    self.assertEqual(passed is None, run_room is not None)
                self.assertEqual(given, played)
                gated = native_judge.gates(home).get("keys", {})
                self.assertEqual(list(gated), [recorded] if recorded else [])
                if recorded:
                    self.assertEqual(gated[recorded]["fake place"]["matched"], 40 if recorded != "k4" else 36)
                    self.assertEqual(native_judge.gate_progress(home, recorded, "fake place"), [])
        native_judge.enabled_path(home).unlink(missing_ok=True)
        # No room at one place ends only that place's gate: the hosts' go first, and this Mac's no-room (over its load
        # share for hours on 2026-09-29) never stops them. The next hour's run plays the rest.
        with patch.object(native_judge, "key", return_value="k6"), patch.object(
                native_fidelity, "gate", side_effect=lambda bots, host, *_: None if host != "yy" else True) as gate, \
                patch.object(native_fidelity, "places", return_value=({"p1": None, "p2": "zz", "p3": "yy"}, {})), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(native_fidelity.gate_missing([self.bot], self.root / "gates", 1), 1)
        self.assertEqual([call.args[1] for call in gate.call_args_list], ["zz", "yy", None])
        native_judge.enabled_path(home).unlink(missing_ok=True)
        # A key that changes on disk while a place is gated (a new checkout mid-run: a gate can take hours) gates no
        # more places under the old one: the next hourly run gates them under the new key.
        native_judge._process_key.clear()
        with patch.object(native_judge, "key", side_effect=["k7", "k7", "k7", "k8", "k8"]), patch.object(
                native_fidelity, "gate", return_value=True) as gate, \
                patch.object(native_fidelity, "places", return_value=({"p1": None, "p2": "zz", "p3": "yy"}, {})), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(native_fidelity.gate_missing([self.bot], self.root / "gates", 1), 1)
        self.assertEqual([call.args[1] for call in gate.call_args_list], ["zz"])
        native_judge._process_key.clear()
        native_judge.enabled_path(home).unlink(missing_ok=True)
        # A Player that found no room once starts nothing more, and waits no longer than its deadline.
        player = native_fidelity.Player([], None, self.root, 1, deadline=time.monotonic() - 1)
        with patch.object(native_fidelity, "wait_for_gate"), patch.object(run, "game_slots", return_value=0):
            with self.assertRaises(native_fidelity.NoRoom):
                player.play(job, True)
        with patch.object(run, "game_slots", return_value=1), self.assertRaises(native_fidelity.NoRoom):
            player.play(job, True)

    # --gate-missing with every place it last listed gated for this key, the listing fresh and every configured host
    # in it, ends without asking a host (it only notes the run); a new key lists the hosts again.
    def test_gate_missing_asks_no_host_when_nothing_is_missing(self) -> None:
        home = remote.loop_home()
        native_judge.enabled_path(home).unlink(missing_ok=True)
        hosts = [remote.Host("mm", 4)]
        native_judge.record_wanted(home, "k1", [remote.PLATFORM, "Darwin arm64 host"], {"mm": "Darwin arm64 host"})
        for place in (remote.PLATFORM, "Darwin arm64 host"):
            native_judge.record_gate(home, "k1", place, True, 40, 40, "report")
        for key, listed in (("k1", False), ("k2", True)):
            with self.subTest(key=key):
                native_judge._process_key.clear()
                with patch.object(native_judge, "key", return_value=key), \
                        patch.object(remote, "load_hosts", return_value=hosts), \
                        patch.object(native_fidelity, "places", return_value=({}, {})) as places, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(native_fidelity.gate_missing([self.bot], self.root / "gates", 1), 0)
                self.assertEqual(places.called, listed)
        native_judge.enabled_path(home).unlink(missing_ok=True)

    # The judge refuses a wasm or a compiled copy of another engine than the one it was built for, and a seed that
    # isn't decimal (the referee's only form).
    def test_the_judge_refuses_another_engine(self) -> None:
        judge = native_judge.local_judge(remote.loop_home() / "native-judge")
        self.assertIsNotNone(judge)
        other = self.root / "other.wasm"
        other.write_bytes(b"\0asm\1\0\0\0")
        wasm, _ = native_judge.engine_wasm()
        exe = str(native_judge.executable(self.bot)[0])
        for flags in (["--wasm", str(other)],
                      ["--wasm", str(wasm), "--cwasm", str(self.root / f"{'0' * 64}-{native_judge.WASMTIME}.cwasm")],
                      ["--wasm", str(wasm), "--seed", "0x10"]):
            with self.subTest(flags=flags):
                done = subprocess.run([str(judge), "--lib", str(native_judge.library()), *flags, "-o",
                                       str(self.root / "x.replay"), str(test_record.MAP), "a", exe, "b", exe],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 1)
                self.assertRegex(done.stderr, r"not \w+ this judge was built for|is not a copy of engine|bad seed")


# Where a gate counts, read from enabled.json alone (no game plays).
class TestWhereGatesCount(unittest.TestCase):
    def setUp(self) -> None:
        self.home = remote.loop_home()
        native_judge.enabled_path(self.home).unlink(missing_ok=True)
        self.addCleanup(native_judge.enabled_path(self.home).unlink, missing_ok=True)

    # This Mac's own games go on the Mac hosts' passed gate while it has no row of its own (a Mac host plays this
    # Mac's judge binary on its engine and toolkit), and on its own row when it has one: a failed one keeps them off.
    def test_this_mac_goes_on_the_mac_hosts_gate_unless_it_has_its_own(self) -> None:
        native_judge.record_gate(self.home, "k1", remote.MAC_HOSTS, True, 40, 40, "report")
        self.assertTrue(run.native_gated(remote.PLATFORM, "k1"))
        self.assertFalse(run.native_gated("Linux x86_64 1a2b3c4d", "k1"))  # a Linux place has only its own row
        native_judge.record_gate(self.home, "k1", remote.PLATFORM, False, 40, 39, "report", "1 mismatched")
        self.assertFalse(run.native_gated(remote.PLATFORM, "k1"))
        self.assertTrue(run.native_gated(remote.MAC_HOSTS, "k1"))

    # --gate-missing lists this Mac's place only while no Mac host answers: beside one, the hosts' passed gates leave
    # nothing missing (29 Sep: this Mac's own gate, waiting on its load share, never found room).
    def test_gate_missing_lists_this_mac_only_without_a_mac_host(self) -> None:
        mac, linux = (types.SimpleNamespace(name=name, windows=lambda: False, native_place=lambda place=place: place)
                      for name, place in (("mm", remote.MAC_HOSTS), ("ll", "Linux x86_64 1a2b3c4d")))
        with patch.object(remote, "load_hosts", return_value=[linux]):
            self.assertEqual(native_fidelity.places()[0], {remote.PLATFORM: None, "Linux x86_64 1a2b3c4d": "ll"})
        with patch.object(remote, "load_hosts", return_value=[mac, linux]):
            wanted, hosts = native_fidelity.places()
            self.assertEqual(wanted, {remote.MAC_HOSTS: "mm", "Linux x86_64 1a2b3c4d": "ll"})
            native_judge.record_wanted(self.home, "k1", list(wanted), hosts)
            for place in wanted:
                native_judge.record_gate(self.home, "k1", place, True, 40, 40, "report")
            self.assertTrue(native_fidelity.nothing_missing("k1"))


if __name__ == "__main__":
    unittest.main()
