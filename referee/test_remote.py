"""Host tests with a fake SSH (no network). Run: .venv/bin/python -m unittest test_remote (from referee/)"""

import testhome  # before anything that reads live paths
import base64
import contextlib
import hashlib
import importlib.metadata
import io
import json
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import host_agent
import remote

UNSWBC = importlib.metadata.version("unswbc")


# Answers Host.ssh as a host would: `uname -sm` with `uname` (or raises it), the helper's install with nothing, and
# each helper call from `replies` (op -> result, (result, blob), or an exception to raise), recording (op, args,
# stdin) in `calls` and every command line in `commands`.
class FakeSsh:
    def __init__(self, replies: dict, uname: bytes | Exception):
        self.replies, self.uname = replies, uname
        self.calls: list[tuple[str, dict, bytes | None]] = []
        self.commands: list[str] = []

    def __call__(self, command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
        self.commands.append(command)
        if command == "uname -sm":
            if isinstance(self.uname, Exception):
                raise self.uname
            return self.uname
        if f'-c "{remote.INSTALL}" {remote.AGENT}' in command:
            return b""
        agent, op, encoded = command.split(" ")[-3:]
        if agent != remote.AGENT:
            raise AssertionError(command)
        self.calls.append((op, host_agent.decode(encoded), data))
        reply = self.replies[op]
        if isinstance(reply, Exception):
            raise reply
        result, blob = reply if isinstance(reply, tuple) else (reply, b"")
        return b"login banner\n" + host_agent.MARK + json.dumps({**result, "blob": len(blob)}).encode() + b"\n" + blob

    def ops(self) -> list[str]:
        return [op for op, _, _ in self.calls]


def fake_host(name: str = "ll", uname: bytes | Exception = b"Linux x86_64\n", **replies) -> tuple[remote.Host, FakeSsh]:
    host = remote.Host(name, 10)
    ssh = FakeSsh(replies, uname)
    host.ssh = ssh
    return host, ssh


LOAD = {"cores": 12, "busy": 1.5, "memory_gb": 8.1}
NO_UNAME = remote.RemoteError("wh: exit 1: 'uname' is not recognized", 1)
WINDOWS = {"platform": {"platform": "Windows AMD64"}}


class TestHostPlatform(unittest.TestCase):
    def test_a_linux_host_reports_cores_load_and_memory(self) -> None:
        host, ssh = fake_host(load=LOAD)
        self.assertTrue(host.foreign())
        self.assertEqual(host.measure(), 10)  # 12 cores less 1.5 busy with other work, capped at max
        host.active = 1
        ssh.replies["load"] = {**LOAD, "memory_gb": 1.9}
        self.assertEqual(host.measure(), 0)  # under 2 GiB free
        self.assertEqual(ssh.ops(), ["load", "load"])
        self.assertTrue(all(c.startswith("U=$HOME/.local/bin/uv;") for c in ssh.commands[1:]))
        ssh.replies["load"] = {"cores": "many"}
        with self.assertRaises(remote.RemoteError):
            host.measure()

    def test_a_host_fills_its_idle_cores_by_what_its_cpu_shows(self) -> None:
        # 2026-09-27: each 2-core az host ran one game with its other core idle, and ll (12 cores) 2-3 games at 19-32%
        # CPU, since cores less the 1-minute load average (1.3 and 7.7 there) left them no room.
        host, ssh = fake_host(load={"cores": 2, "busy": 1.04, "memory_gb": 6.0})
        host.max, host.active = 4, 1
        self.assertEqual(host.measure(), 2)
        host.active = 2
        ssh.replies["load"] = {"cores": 2, "busy": 1.98, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 2)  # full: nothing new until a game ends
        ssh.replies["load"] = {"cores": 2, "busy": 1.4, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 2)  # past one a core only for a whole idle core
        ssh.replies["load"] = {"cores": 2, "busy": 0.9, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 3)
        host.max, host.active = 24, 3
        ssh.replies["load"] = {"cores": 12, "busy": 3.1, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 12)
        # A game waits on SSH and uploads too: past one a core, one more a measurement while CPU sits idle.
        host.max, host.active = 20, 10
        ssh.replies["load"] = {"cores": 10, "busy": 8.4, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 11)
        host.active = 11
        self.assertEqual(host.measure(), 12)
        ssh.replies["load"] = {"cores": 10, "busy": 9.9, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 10)  # full past one a core: one back
        host.active = 10
        self.assertEqual(host.measure(), 10)  # full of our own games: hold, not alternate
        host.active = 9
        self.assertEqual(host.measure(), 9)
        host.active = 0
        ssh.replies["load"] = {"cores": 10, "busy": 8.2, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 2)  # someone else's work keeps its cores
        self.assertEqual(host.cores, 10)

    # 1 Oct: mm and mm1 (kern.maxprocperuid 2666) refused forks at 20-21 games, filled on CPU and memory alone.
    def test_the_users_process_limit_caps_the_hosts_games(self) -> None:
        load = {"cores": 32, "busy": 2.0, "memory_gb": 40.0, "games": 12}
        host, ssh = fake_host(load=load)
        host.max, host.active = 20, 4
        self.assertEqual(host.measure(), 20)  # no limit reported: CPU and memory alone
        room = (2666 - remote.PROCESS_RESERVE) // remote.GAME_PROCESSES
        ssh.replies["load"] = {**load, "proc_limit": 2666}
        self.assertEqual(host.measure(), 4 + room - 12)  # others' 8 games count against the limit too
        ssh.replies["load"] = {**load, "proc_limit": 2666, "games": room + 3}
        self.assertEqual(host.measure(), 4)  # past it: none new, none taken away

    def test_capacity_never_waits_for_the_host_and_a_failed_measurement_rests_it(self) -> None:
        # zz, off all night, cost a 10 s connect timeout on the scheduler's thread every rest, and asking 12 hosts in
        # turn every 10 s took 2.96 s a round: no game started anywhere meanwhile.
        gate, woken = threading.Event(), threading.Event()
        host, ssh = fake_host(load=LOAD)
        answer = host.ssh

        def slow(command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
            gate.wait(10)
            return answer(command, data, timeout)

        host.ssh = slow
        host.wakers.append(woken.set)
        started = time.monotonic()
        self.assertEqual((host.capacity(), host.capacity()), (0, 0))  # nothing known yet, and nothing waited for
        self.assertLess(time.monotonic() - started, 1)
        gate.set()
        self.assertTrue(woken.wait(5))
        self.assertEqual(host.capacity(), 10)
        self.assertEqual(ssh.ops(), ["load"])  # one measurement however often asked

        def down(command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
            raise remote.RemoteError("ll: exit 255: ssh: connect to host ll port 22: Operation timed out", 255)

        host.ssh, host._free = down, (time.monotonic() - remote.LOAD_S - 1, 10)
        woken.clear()
        self.assertEqual(host.capacity(), 10)  # what it last knew, while it asks again
        self.assertTrue(woken.wait(5))
        with self.assertRaisesRegex(remote.RemoteError, "timed out"):
            host.capacity()  # once, so the scheduler rests the host
        self.assertEqual(host.capacity(), 0)  # and nothing until a measurement succeeds

    # az-my's 27-28 s connect probe ran before the measurement's end was announced, holding up its schedulers' wakers.
    def test_the_connect_probe_runs_after_the_measurement_wakes_the_schedulers(self) -> None:
        host, _ = fake_host(load=LOAD)
        probing, woken = threading.Event(), threading.Event()
        host.probe_connect = lambda: probing.wait(10)
        host.wakers.append(woken.set)
        host.capacity()
        self.assertTrue(woken.wait(5))
        self.assertEqual(host.capacity(), 10)  # measured, while the probe still waits
        probing.set()

    def test_a_full_cpu_gives_games_back_so_other_work_keeps_its_share(self) -> None:
        # A busy share can't pass 100%: without a step down, a host with 8 of our games whose other user wants 4 more
        # cores stayed at 8 (the load rule gave 6), and 12 stayed 12.
        host, ssh = fake_host(load={"cores": 10, "busy": 10.0, "memory_gb": 6.0})
        host.max, host.active = 20, 8
        self.assertEqual(host.measure(), 7)
        self.assertEqual(ssh.calls[-1][1], {"root": remote.ROOT})  # the helper counts the games there
        host.active = 12
        self.assertEqual(host.measure(), 11)
        ssh.replies["load"] = {"cores": 10, "busy": 9.7, "memory_gb": 6.0}  # nearly full: still one back
        self.assertEqual(host.measure(), 11)
        ssh.replies["load"] = {"cores": 10, "busy": 9.4, "memory_gb": 6.0}  # half a core idle: hold
        self.assertEqual(host.measure(), 12)
        host.active = 0
        ssh.replies["load"] = {"cores": 10, "busy": 10.0, "memory_gb": 6.0}
        self.assertEqual(host.measure(), 0)  # none of ours to give back

    def test_a_full_shared_host_gives_a_game_back_even_at_one_a_core_less_one(self) -> None:
        # zz is sig's brother's PC: his work keeps its share there, and our games may alternate.
        host, _ = fake_host("zz", load={"cores": 16, "busy": 16.0, "memory_gb": 6.0})
        host.max, host.active = 32, 15
        self.assertEqual(host.measure(), 15)  # our own host: full of our games, it holds
        host.shared = True
        self.assertEqual(host.measure(), 14)
        host.active = 16
        self.assertEqual(host.measure(), 15)
        config = '{"hosts": [{"name": "zz", "max": 32, "shared": true}, {"name": "mm"}]}'
        with patch.object(remote, "_known", {}), patch.dict(remote.os.environ, {"BC_REMOTE_HOSTS": config}):
            self.assertEqual([h.shared for h in remote.load_hosts()], [True, False])

    # Referee processes that each found the same cores idle, on one host whose CPU stays full: each measures in turn,
    # sees every process's games there (the helper counts them), and plays what it is then allowed.
    def unwind(self, cores: int, actives: list[int], rounds: int = 10) -> list[int]:
        hosts = []
        for active in actives:
            host, ssh = fake_host("h", load={})
            host.max, host.active = 2 * cores, active
            hosts.append((host, ssh))
        for _ in range(rounds):
            for host, ssh in hosts:
                ssh.replies["load"] = {"cores": cores, "busy": float(cores), "memory_gb": 6.0,
                                       "games": sum(h.active for h, _ in hosts)}
                host.active = host.measure()
        return [h.active for h, _ in hosts]

    def test_processes_that_overfill_a_host_together_unwind_to_one_game_a_core(self) -> None:
        # Each process's own count alone never gave these back: an evaluation's 2 beside a tune's 1 on a 2-core az host
        # (the yielding floor of one game is cores less one there), three processes at 1 each, and two at 10 on 10 cores.
        for cores, actives in ((2, [2, 1]), (2, [1, 1, 1]), (10, [10, 10]), (10, [10, 5])):
            with self.subTest(cores=cores, actives=actives):
                self.assertEqual(sum(self.unwind(cores, actives)), cores)
        self.assertEqual(self.unwind(10, [10, 5]), [7, 3])  # each gives games back while the host is overfilled

    def test_a_rest_forgets_the_last_measurement(self) -> None:
        # zz died with room for 20: after its rest it got 20 games at once, each waiting out the connect timeout and
        # replaying here past this Mac's load gate.
        host, _ = fake_host("zz")
        host._free = (time.monotonic(), 20)
        with contextlib.redirect_stderr(io.StringIO()), \
                patch.object(remote, "OUTAGES", Path(tempfile.mkdtemp()) / "outages.json"):
            host.rest("zz: exit 255: Operation timed out")
        host.rested_until = 0.0  # its rest over
        host.ssh = lambda *a, **k: (_ for _ in ()).throw(remote.RemoteError("zz: exit 255: timed out", 255))
        self.assertEqual(host.capacity(), 0)

    def test_an_old_measurement_starts_no_more_games_than_run_there(self) -> None:
        gate = threading.Event()
        host, _ = fake_host(load=LOAD)
        answer = host.ssh
        host.ssh = lambda command, data=None, timeout=120: gate.wait(10) and answer(command, data, timeout)
        host.active = 3
        host._free = (time.monotonic() - remote.STALE_S - 1, 10)
        self.assertEqual(host.capacity(), 3)  # until the fresh one lands
        host._free = (time.monotonic() - remote.LOAD_S - 1, 10)
        self.assertEqual(host.capacity(), 10)  # a few seconds old is still what it knows
        gate.set()

    def test_a_host_down_through_many_test_batches_is_alerted_once_and_again_when_back(self) -> None:
        # zz, off overnight, was reported every few minutes: each game process (test, screen, tune batch) makes its
        # own Host, so the per-Host count alone repeats the alert.
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, "ALERTS", Path(directory) / "alerts"), \
                patch.object(remote, "OUTAGES", Path(directory) / "outages.json"), \
                contextlib.redirect_stderr(io.StringIO()):
            for _batch in range(3):
                host = remote.Host("zz", 8)
                host.rest("zz: exit 255: ssh: connect to host port 22: Operation timed out")
                host.rest("zz: exit 255: ssh: connect to host port 22: Operation timed out")
            remote.Host("zz", 8).succeeded()
            lines = (Path(directory) / "alerts").read_text().splitlines()
        self.assertEqual([line.split(" ", 2)[2].split(":")[0] for line in lines], ["zz failing", "zz playing again"])

    def test_a_part_time_hosts_outage_is_recorded_but_not_alerted(self) -> None:
        # sig 2026-09-27: zz is shut down by day. Its outage still goes into OUTAGES, which health.py times.
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, "ALERTS", Path(directory) / "alerts"), \
                patch.object(remote, "OUTAGES", Path(directory) / "outages.json"), \
                contextlib.redirect_stderr(io.StringIO()):
            config = Path(directory) / "remote.json"
            config.write_text(json.dumps({"hosts": [{"name": "zz-part", "max": 32, "part_time": True}]}))
            (host,) = remote.load_hosts(config)
            self.assertTrue(host.part_time)
            host.rest("zz: exit 255: Connection timed out during banner exchange")
            host.rest("zz: exit 255: Connection timed out during banner exchange")
            self.assertIn("zz-part", json.loads((Path(directory) / "outages.json").read_text()))
            host.succeeded()
            self.assertEqual(json.loads((Path(directory) / "outages.json").read_text()), {})
            self.assertFalse((Path(directory) / "alerts").exists())
            # One that answers and fails (a full disk) is no plan: it alerts as any other, and so does its recovery.
            host.rest("zz-part: exit 1: tar: write error: No space left on device")
            host.rest("zz-part: exit 1: tar: write error: No space left on device")
            host.succeeded()
            lines = (Path(directory) / "alerts").read_text().splitlines()
            self.assertEqual([line.split(" ", 2)[2].split(":")[0] for line in lines],
                             ["zz-part failing", "zz-part playing again"])

    def test_health_keeps_the_same_unreachable_test(self) -> None:
        health = (Path(__file__).resolve().parent.parent / "loop/health.py").read_text()
        self.assertIn(f'UNREACHABLE = re.compile(r"{remote.UNREACHABLE.pattern}")', health)
        # The reasons the referee writes for a host that is off: ssh's own exit, a banner or measurement timeout.
        for reason in ("zz: exit 255: ssh: connect to host port 22: Operation timed out", "zz: timed out after 120s"):
            self.assertTrue(remote.UNREACHABLE.search(reason))
        self.assertFalse(remote.UNREACHABLE.search("zz: exit 1: tar: write error: No space left on device"))
        # A failure that merely mentions a timeout (a game's own) isn't the host being off.
        self.assertFalse(remote.UNREACHABLE.search("zz: exit 1: game 12 timed out: dragon 3 used its budget"))
        # Nor does a game's own output quoted in the reason, whatever it says.
        self.assertFalse(remote.UNREACHABLE.search("zz: game exited 1: Connection refused; exit 255: timed out after 3s"))
        self.assertTrue(remote.UNREACHABLE.search("21:40:01 rested 240s: zz-wsl: exit 255: Connection timed out"))

    def test_a_host_failing_twice_running_is_alerted_once(self) -> None:
        host, _ = fake_host()
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, "ALERTS", Path(directory) / "alerts"), \
                patch.object(remote, "OUTAGES", Path(directory) / "outages.json"):
            for _ in range(3):
                host.rest("zz: timed out after 120s")
            self.assertEqual(len((Path(directory) / "alerts").read_text().splitlines()), 1)

    def test_an_unknown_platform_is_a_host_failure(self) -> None:
        host, _ = fake_host(uname=b"FreeBSD amd64\n")
        with self.assertRaises(remote.RemoteError):
            host.platform()

    def test_a_host_without_uname_is_asked_as_windows(self) -> None:
        host, ssh = fake_host("wh", uname=NO_UNAME, load=LOAD, **WINDOWS)
        self.assertEqual(host.platform(), "Windows AMD64")
        self.assertTrue(host.foreign() and host.windows())
        self.assertEqual(host.measure(), 10)
        # cmd.exe and PowerShell both run a program path starting .\ with plain arguments; nothing else is sent.
        for command in ssh.commands[1:]:
            self.assertTrue(command.startswith(".\\.local\\bin\\uv.exe run -q --no-project --python "), command)
            self.assertNotRegex(command.replace(f'"{remote.INSTALL}"', ""), r"[$%;&|<>\"'`]")
        self.assertEqual(ssh.ops(), ["platform", "load"])

    def test_git_for_windows_uname_still_means_windows(self) -> None:
        host, _ = fake_host("wh", uname=b"MINGW64_NT-10.0-26100 x86_64\n", platform={"platform": "Windows ARM64"})
        self.assertEqual(host.platform(), "Windows ARM64")

    def test_an_unreachable_host_is_not_mistaken_for_windows(self) -> None:
        host, ssh = fake_host(uname=remote.RemoteError("ll: exit 255: Connection refused", 255))
        with self.assertRaises(remote.RemoteError):
            host.platform()
        self.assertEqual(ssh.commands, ["uname -sm"])
        host, _ = fake_host(uname=NO_UNAME, platform=remote.RemoteError("exit 1: no uv", 1))
        with self.assertRaisesRegex(remote.RemoteError, "no Windows helper"):
            host.platform()

    def test_the_helper_is_installed_once_named_by_its_content(self) -> None:
        host, ssh = fake_host(load=LOAD)
        host.measure()
        host.measure()
        self.assertEqual(len([c for c in ssh.commands if remote.INSTALL in c]), 1)
        agent = host.ssh
        host.ssh = lambda command, *a, **k: b"" if " load " in command else agent(command, *a, **k)
        with self.assertRaisesRegex(remote.RemoteError, "no result line"):  # an agent torn by a crash says nothing
            host.measure()
        host.ssh = agent
        host.measure()
        self.assertEqual(len([c for c in ssh.commands if remote.INSTALL in c]), 2)  # installed again
        self.assertIn(hashlib.sha256(remote.AGENT_SOURCE).hexdigest()[:16], remote.AGENT)
        self.assertNotRegex(remote.INSTALL, r"[ $%`\\\"]")  # safe inside double quotes in every shell

    # An agent there is kept only while it holds the bytes sent: one a crash left empty is written again (MISSES 94).
    def test_the_install_snippet_writes_whole_files_keeps_a_whole_one_and_rewrites_a_torn_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "a/b/host_agent.py")
            install = [sys.executable, "-c", remote.INSTALL, str(target)]
            subprocess.run(install, input=b"agent", check=True)
            first = target.stat().st_ino
            subprocess.run(install, input=b"agent", check=True)
            self.assertEqual(target.stat().st_ino, first)  # kept, not written again
            target.write_bytes(b"")
            subprocess.run(install, input=b"agent", check=True)
            self.assertEqual(target.read_bytes(), b"agent")
            self.assertEqual([p.name for p in target.parent.iterdir()], ["host_agent.py"])

    def test_prepare_installs_this_pythons_packages_once_and_checks_unswbc(self) -> None:
        host, ssh = fake_host(prepare={"unswbc": UNSWBC})
        unswbc = host.prepare()
        self.assertEqual(host.prepare(), unswbc)
        self.assertEqual(ssh.ops(), ["prepare"])
        _, args, data = ssh.calls[0]
        self.assertEqual((unswbc, args["root"]), (f"{args['folder']}/bin/unswbc", remote.ROOT))
        self.assertIn(f"unswbc=={UNSWBC}", data.decode().splitlines())
        windows, _ = fake_host("wh", uname=NO_UNAME, prepare={"unswbc": UNSWBC}, **WINDOWS)
        self.assertTrue(windows.prepare().endswith("/Scripts/unswbc.exe"))
        wrong, _ = fake_host(prepare={"unswbc": "0.0.1"})
        with self.assertRaises(remote.RemoteError):
            wrong.prepare()

    def test_a_file_is_uploaded_once_by_content_with_its_checksum(self) -> None:
        host, ssh = fake_host(upload={"path": "x"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "m.map")
            path.write_bytes(b"MAP")
            name = host.upload(path)
            self.assertEqual(host.upload(path), name)
        digest = hashlib.sha256(b"MAP").hexdigest()
        self.assertEqual(name, f"{remote.ROOT}/files/{digest[:24]}/m.map")
        self.assertEqual(ssh.calls, [("upload", {"path": name, "sha256": digest}, b"MAP")])

    def test_a_bot_is_built_once_per_source_and_its_executable_path_returned(self) -> None:
        host, ssh = fake_host(build={"executable": ".unswbc-build/bot"})
        with tempfile.TemporaryDirectory() as directory:
            bot = Path(directory, "bot")
            bot.mkdir()
            (bot / "main.cpp").write_text("int main() {}\n")
            (bot / ".unswbc-build").mkdir()
            (bot / ".unswbc-build/bot").write_text("stale")
            unswbc = ".bc-remote/toolchains/abc/bin/unswbc"
            first = host.build(bot, unswbc)
            self.assertEqual(host.build(bot, unswbc), first)
            self.assertEqual(len(ssh.calls), 1)
            self.assertTrue(first.startswith(".bc-remote/builds/abc-") and first.endswith("/.unswbc-build/bot"))
            _, args, data = ssh.calls[0]
            self.assertEqual(args, {"folder": first.removesuffix("/.unswbc-build/bot"),
                                    "toolchain": ".bc-remote/toolchains/abc", "send": False})
            self.assertEqual(tarfile.open(fileobj=io.BytesIO(data)).getnames(), ["main.cpp"])  # no build folder
            (bot / "main.cpp").write_text("int main() { return 0; }\n")
            self.assertNotEqual(host.build(bot, unswbc), first)

    def test_hosts_with_the_same_compiler_build_a_bot_once_and_copy_it(self) -> None:
        # ll built 1135 bots on 26 Sep (5.7 s each) and each az host 255-434, mostly the same bots.
        same = "Linux x86_64\n/usr/bin/c++\nc++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
        first, a = fake_host("ll", build=({"executable": ".unswbc-build/bot"}, b"ELF"))
        second, b = fake_host("az-1", install={"executable": ".unswbc-build/bot"})
        other, c = fake_host("wh", build={"executable": ".unswbc-build/bot.exe"})
        first.compiler, second.compiler, other.compiler = same, same, "Windows AMD64\ng++ 14"
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, "_builds", {}):
            bot = Path(directory, "bot")
            bot.mkdir()
            (bot / "main.cpp").write_text("int main() {}\n")
            unswbc = ".bc-remote/toolchains/abc/bin/unswbc"
            built = [host.build(bot, unswbc) for host in (first, second, other)]
        self.assertEqual(built[0], built[1])
        self.assertEqual((a.ops(), b.ops(), c.ops()), (["build"], ["install"], ["build"]))
        self.assertEqual((a.calls[0][1]["send"], c.calls[0][1]["send"]), (True, True))
        _, install, blob = b.calls[0]
        self.assertEqual((install["executable"], install["sha256"], blob),
                         (".unswbc-build/bot", hashlib.sha256(b"ELF").hexdigest(), b"ELF"))

    def test_a_host_waits_for_another_hosts_build_and_builds_itself_if_that_fails(self) -> None:
        gate = threading.Event()
        first, a = fake_host("ll", build=remote.RemoteError("ll: exit 1: build: no space left", 1))
        second, b = fake_host("az-1", build=({"executable": ".unswbc-build/bot"}, b"ELF"))
        third, c = fake_host("az-2", install={"executable": ".unswbc-build/bot"})
        answer = first.ssh

        def slow(command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
            gate.wait(10)
            return answer(command, data, timeout)

        first.ssh = slow
        for host in (first, second, third):
            host.compiler = "same"
        with tempfile.TemporaryDirectory() as directory, patch.object(remote, "_builds", {}):
            bot = Path(directory, "bot")
            bot.mkdir()
            (bot / "main.cpp").write_text("int main() {}\n")
            unswbc = ".bc-remote/toolchains/abc/bin/unswbc"
            failed = []

            def build_first() -> None:
                try:
                    first.build(bot, unswbc)
                except remote.RemoteError as error:
                    failed.append(error)

            building = threading.Thread(target=build_first)
            building.start()
            # ll must claim the build before az-1 asks: started back to back, az-1's thread sometimes got there first,
            # built itself and shared it, and ll then asked its fake for an install it has no reply for (KeyError
            # 'install', about 6 runs in 20 on 2026-09-27).
            deadline = time.monotonic() + 5
            while not any(isinstance(entry, threading.Event) for entry in remote._builds.values()):
                self.assertLess(time.monotonic(), deadline, "ll never claimed the build")
                time.sleep(0.01)
            waiting = threading.Thread(target=second.build, args=(bot, unswbc))
            waiting.start()
            waiting.join(0.3)
            self.assertTrue(waiting.is_alive())  # waits for ll's build rather than making its own
            self.assertEqual(b.ops(), [])
            gate.set()
            building.join(5)
            waiting.join(5)
            third.build(bot, unswbc)
        self.assertEqual(len(failed), 1)
        self.assertEqual((a.ops(), b.ops(), c.ops()), (["build"], ["build"], ["install"]))

    def test_a_windows_build_names_its_exe(self) -> None:
        host, _ = fake_host("wh", uname=NO_UNAME, build={"executable": ".unswbc-build/bot.exe"}, **WINDOWS)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "main.cpp").write_text("int main() {}\n")
            built = host.build(Path(directory), ".bc-remote/toolchains/abc/Scripts/unswbc.exe")
        self.assertTrue(built.startswith(".bc-remote/builds/abc-") and built.endswith("/.unswbc-build/bot.exe"))

    def test_a_game_is_played_by_the_helper_with_team_a_first(self) -> None:
        host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={},
                              play=({"stdout": "team A wins", "returncode": 0, "replay": True}, b"\x00REPLAY\n"))
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            game = host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1")
            self.assertEqual((game.stdout, game.returncode, game.replay), ("team A wins", 0, b"\x00REPLAY\n"))
            self.assertEqual(ssh.ops(), ["prepare", "upload", "upload", "upload", "play"])
            uploaded = [args["path"] for op, args, _ in ssh.calls if op == "upload"]
            _, play, sent = ssh.calls[-1]
            digest = hashlib.sha256(b"m.map").hexdigest()
            # the map comes with the game, saved where an upload would put it
            self.assertEqual((play["map"], play["map_sha256"], sent), (f"{remote.ROOT}/files/{digest[:24]}/m.map",
                                                                         digest, b"m.map"))
            self.assertEqual((play["bots"], play["folder"]), (uploaded[0:2], ".bc-remote/games/g1"))
            self.assertTrue(play["unswbc"].endswith("/bin/unswbc"))
            # the host's own engine cache, through the uploaded launcher
            self.assertEqual(play["engine"], {"script": uploaded[2], "cache": ".bc-remote/engine-cache"})
            self.assertTrue(uploaded[2].endswith("/engine_cache.py"))
            ssh.replies["play"] = {"stdout": "error: bot crashed", "returncode": 1, "replay": False}
            game = host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g2")
            _, play, sent = ssh.calls[-1]
            self.assertEqual(("map_sha256" in play, sent), (False, None))  # sent once
        self.assertEqual((game.returncode, game.replay), (1, None))  # a game result, for the caller to judge

    # A native game on a map the host hasn't had sends the map with it, whether or not this Mac has a judge to send a
    # Mac host (the review of 2026-09-29: with none, the map's digest went without its bytes, failing the game and
    # resting the host at every new map).
    def test_a_native_game_on_a_new_map_sends_the_map_with_or_without_a_judge(self) -> None:
        import native_judge

        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin", "judge")]
            for path in paths:
                path.write_bytes(path.name.encode())
            for judge in (None, paths[3]):
                with self.subTest(judge=judge), patch.object(native_judge, "local_judge", return_value=judge):
                    host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={},
                                          play={"stdout": "ok", "returncode": 0, "replay": False})
                    host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1", native=True)
                    _, play, sent = ssh.calls[-1]
                    self.assertEqual((play["map_sha256"], sent), (hashlib.sha256(b"m.map").hexdigest(), b"m.map"))
                    self.assertEqual("binary" in play.get("native", {}), judge is not None)
            # a judge's file that can't be read here plays the game without the judge, said in the host's status
            host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={},
                                  play={"stdout": "ok", "returncode": 0, "replay": False})
            with patch.object(remote.Host, "native_files", side_effect=PermissionError("unreadable")), \
                    patch("sys.stderr"):
                game = host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g2", native=True)
            self.assertEqual((game.returncode, "native" in ssh.calls[-1][1]), (0, False))
            self.assertIn("unreadable", host.native_judge)

    # A game past its time on a host that answers the kill after it is the game's (GameTimeout, which run.py replays
    # through unswbc run when it was native); one whose kill goes unanswered too is the connection's.
    def test_a_timed_out_game_is_the_games_only_when_the_host_still_answers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            late = remote.RemoteError("mm: timed out after 300s")
            for kill, raised in (({}, remote.GameTimeout), (late, remote.RemoteError)):
                with self.subTest(kill=kill):
                    host, _ = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={},
                                        play=late, kill=kill)
                    with self.assertRaises(remote.RemoteError) as caught:
                        host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1")
                    self.assertIs(type(caught.exception), raised)

    def test_a_compressed_replay_comes_back_as_the_host_wrote_it(self) -> None:
        from compression import zstd
        replay = bytes(range(256)) * 100
        host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={}, kill={},
                              play=({"stdout": "ok", "returncode": 0, "replay": True, "recording": 3,
                                     "compressed": "zstd"}, zstd.compress(replay + b"rec")))
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            game = host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1", record="A")
            self.assertEqual((game.replay, game.recording), (replay, b"rec"))
            self.assertEqual(ssh.calls[-1][1]["compress"], "zstd")
            ssh.replies["play"] = ({"stdout": "ok", "returncode": 0, "replay": True, "compressed": "zstd"}, b"garbled")
            with self.assertRaisesRegex(remote.RemoteError, "unreadable game report"):
                host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g2")

    def test_a_recorded_game_uploads_the_recorder_and_splits_replay_from_recording(self) -> None:
        host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={},
                              play=({"stdout": "ok", "returncode": 0, "replay": True, "recording": 3}, b"REPLAYrec"))
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            game = host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1", record="A")
        self.assertEqual((game.replay, game.recording), (b"REPLAY", b"rec"))
        play = ssh.calls[-1][1]
        self.assertEqual(play["record"]["team"], "A")
        self.assertTrue(play["record"]["script"].endswith("/record.py"))

    def test_a_game_whose_upload_vanished_uploads_again_and_plays_once_more(self) -> None:
        gone = {"stdout": "python: can't open file '/home/bc/.bc-remote/files/ab/engine_cache.py': [Errno 2]",
                "returncode": 2, "replay": False}
        host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={}, play=gone)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g0")  # uploads everything once
            ssh.calls.clear()
            host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1")
        self.assertEqual(ssh.ops(), ["play", "upload", "upload", "upload", "play"])
        self.assertIn("map_sha256", ssh.calls[-1][1])  # the map again too

    def test_a_game_whose_bot_or_map_vanished_is_rebuilt_and_played_once_more(self) -> None:
        # A long process keeps its uploads and builds; the host's one-day cleanup may drop them meanwhile.
        gone = {"stdout": "error: not found: /home/bc/.bc-remote/builds/abc-123/.unswbc-build/bot",
                "returncode": 1, "replay": False}
        host, ssh = fake_host(prepare={"unswbc": UNSWBC}, upload={}, build={"executable": ".unswbc-build/bot"},
                              play=gone)
        with tempfile.TemporaryDirectory() as directory:
            game_map = Path(directory, "m.map")
            game_map.write_bytes(b"MAP")
            bot = Path(directory, "bot")
            bot.mkdir()
            (bot / "main.cpp").write_text("int main() {}\n")
            with patch.object(remote, "_builds", {}):
                host.play(game_map, (bot, bot), 60, ".bc-remote/games/g0")
                ssh.calls.clear()
                host.play(game_map, (bot, bot), 60, ".bc-remote/games/g1")
        self.assertEqual(ssh.ops(), ["play", "build", "upload", "play"])  # the bot built again, the launcher resent
        self.assertIn("map_sha256", ssh.calls[-1][1])

    # MISSES 94: a repair on the host is said and kept in its status, so its first firing can be checked, and a game
    # still torn after everything is sent again fails the host, never plays as a game (run.judge_failure reads those).
    def test_a_torn_input_is_said_and_one_still_torn_after_resending_is_a_host_failure(self) -> None:
        line = f"not found: /home/bc/.bc-remote/files/ab/m.map whole ({host_agent.TORN}, MISSES 94)"
        torn = {"stdout": line + "\n", "returncode": 1, "replay": False}
        host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC},
                              upload={"repaired": "/home/bc/.bc-remote/files/cd/a.bin: torn upload written again"},
                              play=torn)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            said = io.StringIO()
            with contextlib.redirect_stderr(said), self.assertRaisesRegex(remote.RemoteError, "after sending"):
                host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g0")
        self.assertEqual(ssh.ops().count("play"), 2)
        self.assertIn(f"mm: repaired {line}: sending everything again", said.getvalue())
        self.assertIn("a.bin: torn upload written again", said.getvalue())
        self.assertIn("torn upload written again", host.status()["repaired"])

    def test_a_windows_host_compiles_the_engine_each_game(self) -> None:
        host, ssh = fake_host("wh", uname=NO_UNAME, prepare={"unswbc": UNSWBC}, upload={},
                              play=({"stdout": "ok", "returncode": 0, "replay": True}, b"REPLAY"), **WINDOWS)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1", fork=(paths[1], False))
        self.assertNotIn("engine", ssh.calls[-1][1])
        self.assertNotIn("fork", ssh.calls[-1][1])  # no fork server there either

    # A Mac host forks with this Mac's shim, as it runs this Mac's executables; a Linux one builds its own.
    def test_a_forked_game_sends_the_launcher_and_this_macs_shim_or_has_the_host_build_one(self) -> None:
        reply = ({"stdout": "ok", "returncode": 0, "replay": True}, b"REPLAY")
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin", "fork-shim-x.dylib")]
            for path in paths:
                path.write_bytes(path.name.encode())
            for uname, check in ((b"Darwin arm64\n", True), (b"Linux x86_64\n", False)):
                host, ssh = fake_host("h", uname=uname, prepare={"unswbc": UNSWBC}, upload={}, play=reply)
                host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1", fork=(paths[3], check))
                uploaded = {Path(args["path"]).name: args["path"] for op, args, _ in ssh.calls if op == "upload"}
                fork = ssh.calls[-1][1]["fork"]
                self.assertEqual((fork["script"], fork["check"]), (uploaded["fork_server.py"], check))
                if uname.startswith(b"Darwin"):
                    self.assertEqual(fork["shim"], uploaded["fork-shim-x.dylib"])
                else:
                    self.assertEqual((fork["build"], "fork-shim-x.dylib" in uploaded), (".bc-remote/fork-server", False))
                host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g2")
                self.assertNotIn("fork", ssh.calls[-1][1])  # a game the referee doesn't fork

    def test_a_host_that_cannot_run_the_game_kills_it_and_fails(self) -> None:
        for reply in (remote.RemoteError("mm: exit 1: play: cannot run unswbc", 1),
                      remote.RemoteError("mm: timed out after 60s"), {"stdout": "no status"}):
            host, ssh = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={}, kill={},
                                  play=reply)
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(remote.RemoteError):
                (Path(directory) / "m").write_bytes(b"m")
                host.play(Path(directory, "m"), (Path(directory, "m"), Path(directory, "m")), 60, ".bc-remote/games/g")
            self.assertEqual(ssh.calls[-1][:2], ("kill", {"folder": ".bc-remote/games/g"}))

    def test_a_truncated_reply_is_a_host_failure(self) -> None:
        host, ssh = fake_host(load=(LOAD, b"12345"))
        host.ssh = lambda command, *a, **k: ssh(command, *a, **k)[:-3]
        with self.assertRaisesRegex(remote.RemoteError, "unreadable load reply"):
            host.measure()

    def test_kill_failures_are_ignored(self) -> None:
        host, ssh = fake_host(kill=remote.RemoteError("ll: exit 255", 255))
        host.kill(".bc-remote/games/g")
        self.assertEqual(ssh.calls, [("kill", {"folder": ".bc-remote/games/g"}, None)])


# Host.ssh's real command lines, with subprocess.run faked: each call's ControlPath, and a gate that holds calls open
# so a test can see how many sessions each master carries at once.
class FakeRun:
    def __init__(self, reply=lambda control: subprocess.CompletedProcess([], 0, b"ok", b"")):
        self.reply, self.open, self.paths = reply, [], []
        self.gate, self.lock = threading.Event(), threading.Lock()
        self.gate.set()

    def __call__(self, args: list[str], **_) -> subprocess.CompletedProcess:
        control = next(a.split("=", 1)[1] for a in args if a.startswith("ControlPath="))
        with self.lock:
            self.open.append(control)
            self.paths.append(control)
        self.gate.wait(10)
        with self.lock:
            self.open.remove(control)
        return self.reply(control)


class TestSharedConnections(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.object(remote, "SEATS", Path(directory.name)))
        self.enterContext(patch.dict(remote.os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}))

    def test_sessions_past_nine_open_more_masters_and_reuse_them_when_done(self) -> None:
        # zz (max 32) and sz (max 16) had their sessions refused: sshd allows 10 on one connection.
        run = FakeRun()
        run.gate.clear()
        hosts = [remote.Host("zz", 32), remote.Host("zz", 32)]  # two test batches' Hosts share the masters
        with patch.object(remote.subprocess, "run", run):
            threads = [threading.Thread(target=hosts[i % 2].ssh, args=("x",)) for i in range(20)]
            for thread in threads:
                thread.start()
            for _ in range(500):
                if len(run.open) == 20:
                    break
                threading.Event().wait(0.01)
            counts = {path: run.open.count(path) for path in set(run.open)}
            run.gate.set()
            for thread in threads:
                thread.join()
            self.assertEqual(counts, {"/tmp/bc-ssh-%C-0": 9, "/tmp/bc-ssh-%C-1": 9, "/tmp/bc-ssh-%C-2": 2})
            run.paths.clear()
            hosts[0].ssh("x")
            remote.Host("sz", 16).ssh("x")  # another host has masters of its own
        self.assertEqual(run.paths, ["/tmp/bc-ssh-%C-0", "/tmp/bc-ssh-%C-0"])

    def test_a_game_holds_one_session_at_a_time(self) -> None:
        # prepare, uploads, play and kill run one after another, so a game takes one place on a master.
        host, fake = fake_host("mm", uname=b"Darwin arm64\n", prepare={"unswbc": UNSWBC}, upload={}, kill={},
                               play=remote.RemoteError("mm: timed out after 60s"))
        now, widest = [0], [0]

        def ssh(command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
            now[0] += 1
            widest[0] = max(widest[0], now[0])
            try:
                return fake(command, data, timeout)
            finally:
                now[0] -= 1

        host.ssh = ssh
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(remote.RemoteError):
            paths = [Path(directory, name) for name in ("m.map", "a.bin", "b.bin")]
            for path in paths:
                path.write_bytes(path.name.encode())
            host.play(paths[0], (paths[1], paths[2]), 60, ".bc-remote/games/g1")
        self.assertEqual(fake.ops(), ["prepare", "upload", "upload", "upload", "play", "kill"])
        self.assertEqual(widest, [1])

    def test_a_refused_session_is_run_on_its_own_connection_and_fails_only_if_that_does(self) -> None:
        refused = subprocess.CompletedProcess([], 255, b"", b"mux_client_request_session: session request failed: "
                                                            b"Session open refused by peer\n")

        def reply(control: str) -> subprocess.CompletedProcess:
            return refused if control != "none" else subprocess.CompletedProcess([], 0, b"direct", b"")

        run = FakeRun(reply)
        with patch.object(remote.subprocess, "run", run):
            self.assertEqual(remote.Host("sz", 16).ssh("x"), b"direct")
            self.assertEqual(run.paths, ["/tmp/bc-ssh-%C-0", "none"])
            run.reply = lambda control: refused if control != "none" else subprocess.CompletedProcess(
                [], 255, b"", b"ssh: connect to host sz port 22: Connection refused\n")
            with self.assertRaisesRegex(remote.RemoteError, "Connection refused") as caught:
                remote.Host("sz", 16).ssh("x")
            self.assertEqual(caught.exception.returncode, 255)
            # A host that is down is not retried: nothing was refused on a shared connection.
            run.paths.clear()
            run.reply = lambda control: subprocess.CompletedProcess([], 255, b"", b"Operation timed out")
            with self.assertRaises(remote.RemoteError):
                remote.Host("sz", 16).ssh("x")
            self.assertEqual(run.paths, ["/tmp/bc-ssh-%C-0"])

    def test_unusable_lock_files_mean_a_connection_of_its_own(self) -> None:
        run = FakeRun()
        with patch.object(remote, "SEATS", Path("/dev/null/seats")), patch.object(remote.subprocess, "run", run):
            remote.Host("zz", 32).ssh("x")
        self.assertEqual(run.paths, ["none"])

    def test_every_master_socket_path_fits_the_unix_socket_limit(self) -> None:
        # ssh expands %C to 40 hex characters and binds the new master at the path plus 17 characters first.
        for master in (0, 99):
            path = remote.shared(master)[3].split("=", 1)[1].replace("%C", "0" * 40) + ".XXXXXXXXXXXXXXXX"
            self.assertLess(len(path.encode()), 104)


class TestSandboxedSsh(unittest.TestCase):
    def test_the_host_list_can_come_from_the_environment(self) -> None:
        with patch.dict(remote.os.environ, {"BC_REMOTE_HOSTS": '{"hosts": [{"name": "mm", "max": 5}]}'}):
            hosts = remote.load_hosts(Path(tempfile.mkdtemp()) / "missing.json")
        self.assertEqual([(h.name, h.max) for h in hosts], [("mm", 5)])

    def test_a_host_list_of_the_wrong_shape_leaves_out_what_is_wrong_and_warns_once(self) -> None:
        # A list that parses but isn't {"hosts": [{...}]} raised AttributeError, ValueError or TypeError from every
        # yielding player's capacity check (native.host_runs_now), which run.Scheduler catches only as RemoteError.
        good = '{"name": "mm", "max": 20}'
        for config, names in (("[]", []), ('{"hosts": null}', []), ('{"hosts": {"mm": 20}}', []),
                              (f'{{"hosts": [3, {good}]}}', ["mm"]),
                              (f'{{"hosts": [{{"name": "ll", "max": "x"}}, {{"name": "zz", "max": null}}, {good}]}}',
                               ["mm"]),
                              ('{"hosts": [{"name": "sz", "connect_timeout": [1]}]}', [])):
            with self.subTest(config=config), patch.object(remote, "_known", {}), \
                    patch.object(remote, "_warned", set()), \
                    patch.dict(remote.os.environ, {"BC_REMOTE_HOSTS": config}), \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual([h.name for h in remote.load_hosts()], names)
                warned = stderr.getvalue()
                self.assertEqual([h.name for h in remote.load_hosts()], names)
                self.assertEqual(stderr.getvalue(), warned)  # once a process, not before every game
                self.assertIn("remote hosts $BC_REMOTE_HOSTS: ", warned)

    def test_a_process_keeps_one_host_per_name_and_each_hosts_connect_timeout(self) -> None:
        # A tune's every iteration made fresh Hosts: platform, toolchain, uploads and builds asked again, and zz's
        # 10 s connect timeout paid again. A WSL host that must wake first may wait longer to connect.
        config = '{"hosts": [{"name": "sz-wsl", "max": 16, "connect_timeout": 30}, {"name": "mm"}]}'
        with patch.object(remote, "_known", {}), patch.dict(remote.os.environ, {"BC_REMOTE_HOSTS": config}):
            first = remote.load_hosts()
            first[0].rest("sz-wsl: exit 255: timed out")
            again = remote.load_hosts()
        self.assertEqual([h is g for h, g in zip(first, again)], [True, True])
        self.assertEqual([(h.name, h.max, h.connect_timeout) for h in again], [("sz-wsl", 16, 30), ("mm", 8, 10)])
        self.assertFalse(again[0].usable())  # its rest carries over
        seen = []

        def run(args, **options):
            seen.append((args, options["timeout"]))
            return subprocess.CompletedProcess(args, 0, b"ok", b"")

        with patch.object(remote.subprocess, "run", run), patch.dict(remote.os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}), \
                tempfile.TemporaryDirectory() as seats, patch.object(remote, "SEATS", Path(seats)):
            again[0].ssh("x", timeout=30)
        args, timeout = seen[0]
        self.assertIn("ConnectTimeout=30", args)
        self.assertEqual(timeout, 50)  # the 20 s more it may wait to connect

    # REFLECT 2026-09-30 21:15 F5: az-my and az-id took 27-28 s to connect against remote.json's 30 s, and the timeout
    # was raised to 45 by hand at 20:29. Their own measured connect times set it now; remote.json stays the floor.
    def test_a_hosts_connect_timeout_grows_to_fit_its_measured_connect_times(self) -> None:
        config = '{"hosts": [{"name": "az-my", "max": 4, "connect_timeout": 30}, {"name": "mm"}]}'
        with tempfile.TemporaryDirectory() as folder, patch.object(remote, "CONNECTS", Path(folder, "c.json")), \
                patch.object(remote, "_known", {}), patch.dict(remote.os.environ, {"BC_REMOTE_HOSTS": config,
                                                                                   "HTTPS_PROXY": "", "https_proxy": ""}):
            host = remote.load_hosts()[0]
            calls = []

            def run(args, **options):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, b"", b"")

            with patch.object(remote.subprocess, "run", run):
                host.probe_connect()
                host.probe_connect()  # within PROBE_S: not again
                remote.Host("ll", 8).probe_connect()  # a Host load_hosts didn't make (a test's) never probes
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-2:], ["az-my", "exit 0"])
            self.assertIn("ControlPath=none", calls[0])  # a fresh connection: a shared one hides the connect
            self.assertEqual(len(remote.read_connects()["az-my"]["connects"]), 1)
            for seconds in (27.1, 27.4, 27.9, 28.2):
                remote.record_connect("az-my", "connects", [0, seconds])
            self.assertEqual(remote.measured_timeout("az-my"), 48)  # 1.5 x 28.2 + 5, rounded up
            self.assertEqual([(h.name, h.connect_timeout) for h in remote.load_hosts()], [("az-my", 48), ("mm", 10)])
            for _ in range(remote.CONNECT_SAMPLES + 5):
                remote.record_connect("mm", "connects", [0, 1.0])
            self.assertEqual(len(remote.read_connects()["mm"]["connects"]), remote.CONNECT_SAMPLES)
            self.assertEqual(remote.load_hosts()[1].connect_timeout, 10)  # 1.5 x 1 + 5 is under the floor
            for _ in range(remote.CONNECT_SAMPLES):
                remote.record_connect("mm", "connects", [0, 300.0])  # ever slower: capped at 3 x the floor of 10
                remote.record_connect("az-my", "connects", [0, 300.0])  # and at CONNECT_CAP
            self.assertEqual([h.connect_timeout for h in remote.load_hosts()], [90, 30])
            self.assertEqual(remote.connect_timeout("az-my", 60), remote.CONNECT_CAP)

    def test_ssh_goes_through_the_proxy_only_when_one_is_set(self) -> None:
        with self.ssh_config("Host mm\n  HostName mm.example\n"), \
                patch.dict(remote.os.environ, {"HTTPS_PROXY": "http://u:p@localhost:1"}):
            self.assertIn("ControlPath=none", remote.ssh_command("mm"))
            self.assertIn(f"ProxyCommand={remote.TUNNEL} %h %p", remote.ssh_command("mm"))
        with patch.dict(remote.os.environ, {"BC_LOOP_HOME": remote.os.environ["BC_LOOP_HOME"]}, clear=True):
            self.assertIs(remote.ssh_command("mm"), remote.SSH)

    # Real `ssh -G` on a temporary ssh config in place of ~/.ssh/config.
    @contextlib.contextmanager
    def ssh_config(self, text: str):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config"
            config.write_text(text.replace("CONFIG", str(config)))
            remote.SSH_SETTINGS.clear()
            try:
                with patch.object(remote, "SSH_G", ["ssh", "-F", str(config), "-G"]):
                    yield config
            finally:
                remote.SSH_SETTINGS.clear()

    def test_a_proxycommand_is_split_into_its_hop_by_words(self) -> None:
        wsl = '"wsl -d Ubuntu -u root -- /usr/local/sbin/bc-sshd"'
        self.assertEqual(remote.ssh_hop(f"ssh sz {wsl}"), ("ssh", f" sz {wsl}", ("sz",)))
        # Options and their arguments, apart or attached, come with the hop; flags without one don't take a word.
        command = "/usr/bin/ssh -p 2222 -o User=x -i '/k y' -4v -qF f -l u -p22 -T zitia@sz cmd"
        self.assertEqual(remote.ssh_hop(command)[2],
                         ("-p", "2222", "-o", "User=x", "-i", "/k y", "-4v", "-qF", "f", "-l", "u", "-p22", "-T",
                          "zitia@sz"))
        # -W names what to forward, not how to reach the hop, and `ssh -G` refuses its `%h:%p`.
        self.assertEqual(remote.ssh_hop("ssh -W %h:%p sz")[2], ("sz",))
        self.assertEqual(remote.ssh_hop("ssh -qW %h:%p -p 2 sz")[2], ("-q", "-p", "2", "sz"))
        self.assertEqual(remote.ssh_hop("ssh -- -odd cmd")[2], ("--", "-odd"))
        for other in ("none", "nc %h %p", "ssh", "ssh -p", "sshx sz cmd", "ssh sz 'unclosed", "'ssh' sz cmd",
                      # ssh refuses a jump beside our ProxyCommand ("Cannot specify -J with ProxyCommand")
                      "ssh -J j sz cmd", "ssh -qJj sz cmd", "ssh -o ProxyJump=j sz cmd", "ssh -oproxyjump=j sz",
                      "ssh -o 'ProxyJump j' sz",
                      # ssh honours options after the host, which `ssh -G sz` wouldn't see: :2222, not :22
                      "ssh sz -p 2222 cmd"):
            self.assertIsNone(remote.ssh_hop(other), other)
        self.assertEqual(remote.ssh_hop("ssh -o User=j sz cmd -p 2")[2], ("-o", "User=j", "sz"))

    def test_a_host_behind_an_ssh_hop_tunnels_to_the_hop_and_runs_its_command(self) -> None:
        # sz-wsl's config: the tunnel used to ask the proxy for sz-wsl:22, which resolves nowhere (502).
        config = ('Host sz\n  HostName 100.1.2.3\nHost sz-wsl\n  User zitian\n'
                  '  ProxyCommand ssh sz "wsl -d Ubuntu -u root -- /usr/local/sbin/bc-sshd"\n'
                  'Host far\n  ProxyCommand ssh -p 2222 -W %h:%p jump\nHost jump\n  HostName jump.example\n'
                  'Host deep\n  ProxyCommand ssh sz-wsl nc %h 22\n')
        with self.ssh_config(config):
            command, endpoint = remote.proxy_route(("sz-wsl",))
            self.assertEqual(endpoint, "100.1.2.3:22")
            words = shlex.split(command.replace("%%", "%"))  # as the outer ssh expands and runs it
            self.assertEqual(words[0], "ssh")
            self.assertEqual(words[-2:], ["sz", "wsl -d Ubuntu -u root -- /usr/local/sbin/bc-sshd"])
            self.assertIn(f"ProxyCommand={remote.TUNNEL} %h %p", words)
            self.assertIn("BatchMode=yes", words)
            self.assertEqual(remote.ssh_names("sz-wsl"), ["100.1.2.3:22"])
            # The hop's own options decide where it connects; the host's own %-tokens are left for the outer ssh.
            command, endpoint = remote.proxy_route(("far",))
            self.assertEqual(endpoint, "jump.example:2222")
            self.assertTrue(command.endswith(" -p 2222 -W %h:%p jump"), command)
            self.assertIn("ProxyCommand=" + remote.TUNNEL.replace("%", "%%") + " %%h %%p", shlex.split(command))
            # Two hops deep: each level's tokens are doubled once more.
            command, endpoint = remote.proxy_route(("deep",))
            self.assertEqual(endpoint, "100.1.2.3:22")
            self.assertIn(" %%%%h %%%%p", command)
            self.assertEqual(remote.ssh_names("mm"), ["mm:22"])

    def test_the_sandbox_allows_each_hosts_proxied_port_only(self) -> None:
        with self.ssh_config("Host mm\n  HostName mm.example\n  Port 2200\n"):
            self.assertEqual(remote.ssh_names("mm"), ["mm.example:2200"])
            self.assertEqual(remote.ssh_names("zitia@other"), ["other:22"])
            # `ssh -G` failing allows nothing (not "sz-wsl:22", which the sandbox rejects), and isn't remembered.
            with patch.object(remote, "SSH_G", ["false"]):
                self.assertEqual(remote.ssh_names("sz-wsl"), [])
            self.assertNotIn(("sz-wsl",), remote.SSH_SETTINGS)

    def test_real_ssh_runs_each_hops_command_and_tunnels_to_the_last_hop(self) -> None:
        # Real ssh through the generated ProxyCommand, 1-3 hops deep, with a stub tunnel that records its arguments
        # and the command line of every ssh above it, then exits (so no network is used).
        config = "Host hop1\n  HostName hop1.example\n  Port 2201\n" + "".join(
            f"Host {name}\n  ProxyCommand ssh -F CONFIG {hop} run-{name} %h\n"
            for hop, name in (("hop1", "one"), ("one", "two"), ("two", "three")))
        with self.ssh_config(config) as path, tempfile.TemporaryDirectory() as folder:
            log, stub = Path(folder) / "log", Path(folder) / "stub.py"
            stub.write_text(
                "import json, os, subprocess, sys\nchain, pid = [], os.getppid()\nfor _ in range(4):\n"
                "    line = subprocess.run(['ps', '-ww', '-o', 'ppid=', '-o', 'args=', '-p', str(pid)],\n"
                "                          capture_output=True, text=True).stdout.strip()\n"
                "    parent, _, args = line.partition(' ')\n    chain.append(args.strip())\n"
                "    pid = int(parent or 1)\n"
                f"open({str(log)!r}, 'a').write(json.dumps({{'tunnel': sys.argv[1:], 'chain': chain}}) + '\\n')\n")
            names = ["one", "two", "three"]
            with patch.object(remote, "TUNNEL", f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}"), \
                    patch.dict(remote.os.environ, {"HTTPS_PROXY": "http://localhost:1"}):
                for depth in (1, 2, 3):
                    name = names[depth - 1]
                    subprocess.run([*remote.ssh_command(name), "-F", str(path), "-o", "ConnectTimeout=5", name,
                                    "true"], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                    seen = json.loads(log.read_text().splitlines()[-1])
                    self.assertEqual(seen["tunnel"], ["hop1.example", "2201"], (depth, seen))
                    # The ssh to each hop runs with that level's own %h expanded once, as its host's name.
                    for level in range(depth):
                        hop = names[level - 1] if level else "hop1"
                        self.assertTrue(seen["chain"][level].endswith(f" {hop} run-{names[level]} {names[level]}"),
                                        (depth, level, seen["chain"]))
                    self.assertTrue(seen["chain"][depth].endswith(f" {name} true"), (depth, seen["chain"]))

    def test_the_tunnel_logs_in_to_the_proxy_and_relays_both_ways(self) -> None:
        # A fake proxy: checks the CONNECT line and login, then echoes what it receives in upper case.
        server = socket.create_server(("127.0.0.1", 0))
        seen: list[bytes] = []

        def proxy() -> None:
            conn, _ = server.accept()
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            seen.append(request)
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\nSSH-2.0-fake\r\n")
            while data := conn.recv(4096):
                conn.sendall(data.upper())
            conn.close()

        threading.Thread(target=proxy, daemon=True).start()
        env = {**remote.os.environ, "HTTPS_PROXY": f"http://srt.user:pa%3Ass@127.0.0.1:{server.getsockname()[1]}"}
        done = subprocess.run([sys.executable, remote.__file__, "tunnel", "mm.example", "22"], input=b"hello",
                              capture_output=True, env=env, timeout=30)
        self.assertEqual(done.stdout, b"SSH-2.0-fake\r\nHELLO")
        self.assertTrue(seen[0].startswith(b"CONNECT mm.example:22 HTTP/1.1\r\n"))
        self.assertIn(b"Proxy-Authorization: Basic " + base64.b64encode(b"srt.user:pa:ss"), seen[0])

    def test_a_refused_tunnel_fails_so_the_game_replays_here(self) -> None:
        server = socket.create_server(("127.0.0.1", 0))

        def proxy() -> None:
            conn, _ = server.accept()
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            conn.close()

        threading.Thread(target=proxy, daemon=True).start()
        env = {**remote.os.environ, "HTTPS_PROXY": f"http://127.0.0.1:{server.getsockname()[1]}"}
        done = subprocess.run([sys.executable, remote.__file__, "tunnel", "other.example", "22"], input=b"",
                              capture_output=True, env=env, timeout=30)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(b"403", done.stderr)


# The real POSIX command lines, run by `sh` here instead of over SSH, with a stand-in uv in a temporary home.
class TestPosixCommands(unittest.TestCase):
    def test_the_helper_answers_through_the_shell_commands_a_host_gets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            uv = home / ".local/bin/uv"
            uv.parent.mkdir(parents=True)
            uv.write_text(f'#!/bin/sh\nwhile [ "$1" != python ]; do shift; done; shift; exec {sys.executable} "$@"\n')
            uv.chmod(0o755)

            def ssh(command: str, data: bytes | None = None, timeout: float = 120) -> bytes:
                done = subprocess.run(["sh", "-c", command], input=data, capture_output=True, cwd=home,
                                      env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}, timeout=60)
                if done.returncode:
                    raise remote.RemoteError(done.stderr.decode(), done.returncode)
                return done.stdout

            host = remote.Host("here", 4)
            host.ssh = ssh
            self.assertEqual(host.platform(), remote.PLATFORM)
            self.assertGreaterEqual(host.measure(), 0)
            (home / "m.map").write_bytes(b"MAP")
            name = host.upload(home / "m.map")
            self.assertEqual((home / name).read_bytes(), b"MAP")
            self.assertEqual((home / remote.AGENT).read_bytes(), remote.AGENT_SOURCE)


if __name__ == "__main__":
    unittest.main()
